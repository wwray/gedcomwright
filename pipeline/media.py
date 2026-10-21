"""Stage 2: media (BRIEF.md "Stage 2: media"). Input is only the media
referenced by published people - i.e. exactly build/data/media_links.json,
already filtered by Stage 1's privacy rules (extract.py). This stage never
re-derives privacy; it only resolves paths, classifies photo vs document,
generates derivatives, and writes build/data/media.json + a media report.

Dependencies: pyvips/libvips for all image work, pypdfium2 to rasterize PDF
pages (chosen over PyMuPDF specifically to avoid AGPL - see docs/ for the
licence discussion with Will, 9 Oct 2026). Both confirmed working against
the real export's files before this module was written.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import pyvips
import pypdfium2 as pdfium

from .gedcom import Gedcom
from .mediapath import resolve_media_path
from .overrides import load_media_type_overrides

if TYPE_CHECKING:  # avoid a runtime circular import: config.py imports
    # MediaThresholds from this module, so this module must not import
    # Config back at runtime.
    from .config import Config

CACHE_VERSION = 1
NOT_PUBLISHED_EXTS = (".htm", ".html", ".doc", ".docx")
_PAREN_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


@dataclass
class MediaThresholds:
    """BRIEF.md Stage 2: "All thresholds are configurable."""

    thumbnail_width: int = 400
    display_width: int = 2048
    srcset_widths: tuple[int, ...] = (640, 1024, 1536, 2048)
    photo_zoom_max_width: int = 3500
    document_dzi_min_long_edge: int = 4000
    dzi_tile_size: int = 510
    pdf_render_dpi: int = 200
    publish_original_pdf: bool = False
    webp_quality: int = 82

    def fingerprint(self) -> str:
        """Changes whenever a setting that affects generated pixels
        changes, so the derivative cache invalidates itself correctly."""
        relevant = (self.thumbnail_width, self.display_width, self.srcset_widths,
                    self.photo_zoom_max_width, self.document_dzi_min_long_edge,
                    self.dzi_tile_size, self.pdf_render_dpi, self.webp_quality)
        return hashlib.sha1(repr(relevant).encode()).hexdigest()[:8]


# --- classification ---------------------------------------------------------

def classify_type(item: dict, *, source_titles: set[str],
                   type_overrides: dict[str, str]) -> str:
    """BRIEF.md Stage 2 "Classification (photo/document)": overrides > PDF >
    Ancestry-collection filename > citation-only > default photo.

    The brief's first-choice signal - "the FTM category, if the fresh
    export carries it" - is dropped entirely, not just left unverified:
    GEDCOM 5.5.1 has no standard media-category tag, and two full
    inspections of real FTM exports (docs/phase0-report.md,
    docs/phase0-remaining.md) found no custom tag for it either, under any
    name. FTM's in-app media categories apparently never reach the GEDCOM
    at all, so there is nothing here to read (confirmed with Will, 9 Oct
    2026) - this isn't a gap to fill later, just a path that doesn't exist.
    """
    mid = item["id"]
    if mid in type_overrides:
        return type_overrides[mid]
    path = PureWindowsPath(item["file_path"])
    if path.suffix.lower() == ".pdf":
        return "document"
    stem = _PAREN_SUFFIX_RE.sub("", path.stem).strip().lower()
    if stem and stem in source_titles:
        return "document"
    if item.get("citation_only"):
        return "document"
    return "photo"


# --- image loading -----------------------------------------------------

def _load_oriented_srgb(path: Path) -> pyvips.Image:
    """Apply EXIF orientation, then convert to sRGB (BRIEF.md: "Apply EXIF
    orientation, then strip all metadata. Convert to sRGB. Handle greyscale,
    ICC profiles and MPO; also handle CMYK, 16-bit and multi-page TIFF").

    MPO needs no special handling: it's a baseline JPEG followed by extra
    JPEG images in APP2 markers, and libvips' ordinary jpegload already
    reads just the primary/first image. Multi-page TIFF needs `page=0`
    explicitly (jpegload doesn't accept that option at all, hence the
    extension check). CMYK, 16-bit and greyscale are all handled by the
    colourspace conversion below without special-casing. All four cases
    were verified against synthetic fixtures before this was written.

    Deliberately NOT `access="sequential"`: this image gets resized several
    times over (thumbnail, display, zoom), and sequential access only
    supports a single forward pass - using it here raised "out of order
    read" the first time this ran against a real file.
    """
    kwargs = {}
    if path.suffix.lower() in (".tif", ".tiff"):
        kwargs["page"] = 0
    im = pyvips.Image.new_from_file(str(path), **kwargs)
    im = im.autorot()
    if im.get_typeof("icc-profile-data") != 0:
        try:
            im = im.icc_transform("srgb", embedded=True)
        except pyvips.Error:
            im = im.colourspace("srgb")
    else:
        im = im.colourspace("srgb")
    return im


def _pdf_page_to_vips(page: "pdfium.PdfPage", dpi: int) -> pyvips.Image:
    """Render one PDF page to a pyvips image, without PIL or numpy - just
    the raw bitmap buffer pypdfium2 already exposes, reordered from its BGR
    byte order to RGB. Verified against a real PDF in the media folder
    before this was written."""
    bitmap = page.render(scale=dpi / 72)
    raw = bytes(bitmap.buffer)
    im = pyvips.Image.new_from_memory(raw, bitmap.width, bitmap.height, 3, "uchar")
    im = im[2].bandjoin([im[1], im[0]])  # BGR -> RGB
    return im.copy(interpretation="srgb")


# --- derivative generation -----------------------------------------------

def _resize_to_width(im: pyvips.Image, width: int) -> pyvips.Image:
    if im.width <= width:
        return im  # never upscale
    return im.resize(width / im.width)


def _save_webp(im: pyvips.Image, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.webpsave(str(path), Q=quality, strip=True)


def generate_derivatives(im: pyvips.Image, out_dir: Path, rel_prefix: str,
                          content_hash: str, mtype: str,
                          cfg: MediaThresholds) -> dict:
    """Writes the thumbnail, display (+srcset) and zoom-tier derivatives for
    one already-oriented, already-sRGB image, sharded under
    `out_dir/<hash[:2]>/`. Returns the manifest fields plus an internal
    "_files" list (every derivative path, absolute, for the cache to verify
    they still exist before trusting a cache hit)."""
    shard = out_dir / content_hash[:2]
    rel_shard = f"{rel_prefix}/{content_hash[:2]}"
    files: list[Path] = []

    thumb_im = _resize_to_width(im, cfg.thumbnail_width)
    thumb_path = shard / f"{content_hash}-thumb.webp"
    _save_webp(thumb_im, thumb_path, cfg.webp_quality)
    files.append(thumb_path)

    display: dict[str, str] = {}
    widths = sorted(set(cfg.srcset_widths) | {cfg.display_width})
    for w in widths:
        if w > im.width and display:
            continue  # already have at least one entry at <= native size
        sized = _resize_to_width(im, w)
        p = shard / f"{content_hash}-{sized.width}w.webp"
        if str(sized.width) not in display:
            _save_webp(sized, p, cfg.webp_quality)
            files.append(p)
            display[str(sized.width)] = f"{rel_shard}/{p.name}"

    if mtype == "photo":
        zoom_im = _resize_to_width(im, cfg.photo_zoom_max_width)
        zoom_path = shard / f"{content_hash}-zoom.webp"
        _save_webp(zoom_im, zoom_path, cfg.webp_quality)
        files.append(zoom_path)
        zoom = {"kind": "image", "path": f"{rel_shard}/{zoom_path.name}"}
    else:
        long_edge = max(im.width, im.height)
        if long_edge >= cfg.document_dzi_min_long_edge:
            dzi_base = shard / f"{content_hash}-dzi"
            im.dzsave(str(dzi_base), tile_size=cfg.dzi_tile_size, overlap=1,
                      suffix=".webp")
            files.append(Path(f"{dzi_base}.dzi"))
            files.append(Path(f"{dzi_base}_files"))
            zoom = {"kind": "dzi", "path": f"{rel_shard}/{dzi_base.name}.dzi"}
        else:
            zoom_path = shard / f"{content_hash}-zoom.webp"
            _save_webp(im, zoom_path, cfg.webp_quality)
            files.append(zoom_path)
            zoom = {"kind": "image", "path": f"{rel_shard}/{zoom_path.name}"}

    return {
        "width": im.width,
        "height": im.height,
        "thumbnail": f"{rel_shard}/{thumb_path.name}",
        "display": display,
        "zoom": zoom,
        "_files": [str(p) for p in files],
    }


# --- incremental cache -----------------------------------------------------

class DerivativeCache:
    """Keyed by content hash + the settings that affect generated pixels, so
    an unchanged source file is never reprocessed, but a config/threshold
    change invalidates exactly what it should (BRIEF.md: "content-hash
    filenames, plus a hash cache for incremental rebuilds")."""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            try:
                self.data: dict = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.data = {}
        else:
            self.data = {}

    def get(self, key: str) -> dict | None:
        entry = self.data.get(key)
        if entry is None:
            return None
        for p in entry.get("_files", []):
            if not Path(p).exists():
                return None  # a derivative went missing - regenerate
        return entry

    def put(self, key: str, entry: dict) -> None:
        self.data[key] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def _sha256_prefix(path: Path, length: int = 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


# --- per-item processing ---------------------------------------------------

def _common_fields(item: dict) -> dict:
    return {
        "id": item["id"],
        "caption": item.get("title"),
        "description": item.get("description"),
        "date_text": item.get("date_text"),
        "people": item.get("people", []),
        "portrait_of": item.get("portrait_of", []),
    }


def process_image(item: dict, path: Path, content_hash: str, mtype: str,
                   out_dir: Path, cfg: MediaThresholds,
                   cache: DerivativeCache) -> tuple[dict, bool]:
    key = f"{content_hash}:{CACHE_VERSION}:{mtype}:{cfg.fingerprint()}"
    cached = cache.get(key)
    from_cache = cached is not None
    if not from_cache:
        im = _load_oriented_srgb(path)
        cached = generate_derivatives(im, out_dir, "media", content_hash, mtype, cfg)
        cache.put(key, cached)
    entry = {**_common_fields(item), "type": mtype,
             **{k: v for k, v in cached.items() if k != "_files"}}
    return entry, from_cache


def process_pdf(item: dict, path: Path, content_hash: str, out_dir: Path,
                 cfg: MediaThresholds, cache: DerivativeCache) -> tuple[dict, bool]:
    """BRIEF.md: "PDFs: render pages to images and treat them as documents.
    Publish the original PDF only if config allows it." - always type
    'document', one sub-entry per page."""
    pages: list[dict] = []
    any_from_cache = False
    pdf = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(pdf)):
            page_hash = f"{content_hash}-p{i + 1}"
            key = f"{page_hash}:{CACHE_VERSION}:document:{cfg.fingerprint()}"
            cached = cache.get(key)
            if cached is None:
                im = _pdf_page_to_vips(pdf[i], cfg.pdf_render_dpi)
                cached = generate_derivatives(im, out_dir, "media", page_hash,
                                               "document", cfg)
                cache.put(key, cached)
            else:
                any_from_cache = True
            pages.append({"page": i + 1,
                          **{k: v for k, v in cached.items() if k != "_files"}})
    finally:
        pdf.close()

    entry = {**_common_fields(item), "type": "document", "is_pdf": True, "pages": pages}
    if cfg.publish_original_pdf:
        # BRIEF.md's one deliberate, config-gated exception to Hard Rule 2
        # ("original media files are never published") - off by default.
        orig_dir = out_dir / content_hash[:2]
        orig_dir.mkdir(parents=True, exist_ok=True)
        orig_path = orig_dir / f"{content_hash}.pdf"
        if not orig_path.exists():
            orig_path.write_bytes(path.read_bytes())
        entry["original"] = f"media/{content_hash[:2]}/{orig_path.name}"
    return entry, any_from_cache


def process_item(item: dict, *, media_dir: Path, out_dir: Path, mtype: str,
                  cfg: MediaThresholds, cache: DerivativeCache,
                  unresolved: list[tuple[str, str]],
                  not_published: list[tuple[str, str, str]]) -> tuple[dict | None, bool]:
    resolved = resolve_media_path(media_dir, item["file_path"])
    if resolved is None or not resolved.exists():
        unresolved.append((item["id"], item["file_path"]))
        return None, False

    ext = resolved.suffix.lower()
    if ext in NOT_PUBLISHED_EXTS:
        not_published.append((item["id"], str(resolved), ext))
        return None, False

    content_hash = _sha256_prefix(resolved)
    if ext == ".pdf":
        return process_pdf(item, resolved, content_hash, out_dir, cfg, cache)
    return process_image(item, resolved, content_hash, mtype, out_dir, cfg, cache)


# --- orchestration -----------------------------------------------------

def write_report(summary: dict, unresolved: list[tuple[str, str]],
                  not_published: list[tuple[str, str, str]], path: Path) -> None:
    lines = [
        "# Media report", "",
        f"**{summary['processed']} processed** ({summary['photos']} photos, "
        f"{summary['documents']} documents, {summary['pdf_pages']} PDF pages across "
        f"them), **{summary['cached']} reused from cache** (unchanged since last run).",
        "", f"## Unresolved files ({len(unresolved)})",
        "", "BRIEF.md: \"Report unresolvable files without failing the build.\"", "",
    ]
    if not unresolved:
        lines.append("(none)")
    for mid, raw in unresolved:
        lines.append(f"- `{mid}` {raw!r}")
    lines += ["", f"## Not published - .htm/.doc/.docx ({len(not_published)})", "",
              "BRIEF.md: \".htm and Word files are not published in the MVP.\"", ""]
    if not not_published:
        lines.append("(none)")
    for mid, raw, ext in not_published:
        lines.append(f"- `{mid}` {ext} {raw!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(config: "Config", *, limit: int | None = None) -> dict:
    """`limit`: process only the first N items (testing/spot-checking only -
    the real run should omit it)."""
    media_links_path = config.data_dir / "media_links.json"
    if not media_links_path.exists():
        raise FileNotFoundError(
            f"{media_links_path} not found - run 'gedcomwright extract' first")
    items = json.loads(media_links_path.read_text(encoding="utf-8"))
    if limit is not None:
        items = items[:limit]

    gedcom = Gedcom(config.resolve_gedcom())
    source_titles = {(s.val("TITL") or "").strip().lower() for s in gedcom.of_type("SOUR")}
    source_titles.discard("")
    type_overrides = load_media_type_overrides(config.overrides_dir / "media-type.txt")

    out_dir = config.site_dir / "media"
    cache = DerivativeCache(config.data_dir / "media_cache.json")

    manifest: list[dict] = []
    unresolved: list[tuple[str, str]] = []
    not_published: list[tuple[str, str, str]] = []
    cached_count = 0
    photos = documents = pdf_pages = 0
    ancestry_only_skipped = 0

    for item in items:
        if item.get("is_ancestry_only"):
            # Will, 7 Oct 2026: "if we're not going to show the ancestry
            # sources perhaps they shouldn't be in the upload" - render.py
            # already never links to these (group_citations/build_gallery),
            # so there's no point spending I/O generating derivatives or
            # shipping the files at all.
            ancestry_only_skipped += 1
            continue
        mtype = classify_type(item, source_titles=source_titles,
                               type_overrides=type_overrides)
        entry, from_cache = process_item(
            item, media_dir=config.media_dir, out_dir=out_dir, mtype=mtype,
            cfg=config.media, cache=cache, unresolved=unresolved,
            not_published=not_published,
        )
        if entry is None:
            continue
        if from_cache:
            cached_count += 1
        manifest.append(entry)
        if entry.get("is_pdf"):
            documents += 1
            pdf_pages += len(entry.get("pages", []))
        elif entry["type"] == "photo":
            photos += 1
        else:
            documents += 1

    cache.save()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    (config.data_dir / "media.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "processed": len(manifest),
        "photos": photos,
        "documents": documents,
        "pdf_pages": pdf_pages,
        "cached": cached_count,
        "unresolved": len(unresolved),
        "not_published": len(not_published),
        "ancestry_only_skipped": ancestry_only_skipped,
    }
    write_report(summary, unresolved, not_published, config.reports_dir / "media.md")
    return summary

"""Stage 4: audit (BRIEF.md). Independently re-verifies the already-built
output against build/data/*.json and a fresh privacy classification of the
GEDCOM itself, rather than re-running (and so implicitly trusting) the same
code path that built the site. BRIEF.md, Hard Rule 1: "Enforce it in the
data stage, then verify it independently with an audit step that fails the
build." Read-only: never writes to build/site/ or build/data/, only to
build/reports/audit.md.

Six checks, matching BRIEF.md's Stage 4 list exactly:
  1. any published person fails the privacy rules
  2. any hidden person's ID or page appears in the output
  3. any output file references a media file not in the manifest
  4. any published image still contains EXIF/GPS/XMP
  5. any internal link is broken
  6. the output contains originals (by hash) from the media folder

Checks 3 and 5 share one HTML-attribute scan, since both need the same
"what does every page actually link to" pass over the rendered site.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

import pyvips

from .gedcom import Gedcom
from .mediapath import resolve_media_path
from .overrides import load_xref_list
from .privacy import classify, withheld_media

if TYPE_CHECKING:
    from .config import Config

# The fixed set of attributes templates/ actually uses for page-to-page or
# page-to-media references (confirmed by grepping templates/, 6 Oct 2026).
# Not a generic "scan all attributes" net: data-kind and data-icons-prefix
# aren't paths, and c.link / contact hrefs are deliberately external.
# data-pages (a JSON array, for the multi-page document viewer) is handled
# separately in _LinkExtractor, since it isn't a single bare path.
LINKED_ATTRS = ("href", "src")

# Hard Rule 4 names EXIF, GPS and XMP. GPS lives inside EXIF's GPS sub-IFD,
# so checking "exif-data" covers it; icc-profile-data isn't itself a privacy
# leak, but its survival would mean webpsave's strip=True didn't run, which
# is the same underlying failure - so it's checked under the same rule.
METADATA_FIELDS = ("exif-data", "xmp-data", "icc-profile-data")


@dataclass
class AuditResult:
    failures: dict[str, list[str]] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(self.failures.values())

    @property
    def total_failures(self) -> int:
        return sum(len(v) for v in self.failures.values())


# --- check 1 + fresh privacy classification --------------------------------

def _fresh_privacy(config: "Config", gedcom: Gedcom):
    living_overrides = load_xref_list(config.overrides_dir / "living.txt")
    deceased_overrides = load_xref_list(config.overrides_dir / "deceased.txt")
    media_allow = load_xref_list(config.overrides_dir / "media-allow.txt")
    priv = classify(
        gedcom, home_person=config.home_person, min_generation=config.min_generation,
        thresholds=config.thresholds, living_overrides=frozenset(living_overrides),
        deceased_overrides=frozenset(deceased_overrides),
    )
    withheld = withheld_media(gedcom, priv.hidden_xrefs, frozenset(media_allow))
    return priv, withheld


def check_privacy_rules(people: list[dict], priv) -> list[str]:
    """Every person actually published in people.json must still classify
    as published under a fresh run of the same rules against the current
    GEDCOM. (The reverse - someone the fresh rules would publish but who
    isn't in people.json - isn't a violation; it just means extract hasn't
    been re-run since the GEDCOM changed, which `gedcomwright extract`
    already reports via its own summary line.)"""
    fresh_published_bare = {x.strip("@") for x in priv.published_xrefs}
    failures = []
    for p in people:
        if p["id"] in fresh_published_bare:
            continue
        xref = f"@{p['id']}@"
        reason = (priv.people[xref].publish_reason if xref in priv.people
                   else "no longer present in the GEDCOM at all")
        failures.append(
            f"published person {p['id']} ({p['name']['display']}) fails a fresh "
            f"privacy re-check: {reason}")
    return failures


# --- check 2: hidden people never appear in the output ---------------------

def _iter_rendered_html(site_dir: Path):
    for p in sorted(site_dir.rglob("index.html")):
        if "pagefind" in p.parts:
            continue
        yield p
    p404 = site_dir / "404.html"
    if p404.exists():
        yield p404


def check_no_hidden_in_output(site_dir: Path, hidden_bare: set[str]) -> list[str]:
    """BRIEF.md: "any hidden person's ID, page or data appears in the
    output (names inside published free text are exempt; see Hard Rule
    1)". The exemption is specifically for names in prose, so this checks
    for the hidden person's own bare xref - as a page directory, and as a
    token anywhere in any rendered file (a link, a schema.org stub, a
    data- attribute) - not for name text, which is deliberately out of
    scope per that exemption."""
    failures = []
    people_dir = site_dir / "people"
    if people_dir.exists():
        for d in sorted(people_dir.iterdir()):
            if not d.is_dir():
                continue
            if d.name in hidden_bare or any(d.name.endswith(f"-{h}") for h in hidden_bare):
                failures.append(f"people/{d.name}/ is a page for a hidden person")

    if not hidden_bare:
        return failures
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(re.escape(h) for h in sorted(hidden_bare)) + r")(?![A-Za-z0-9])"
    )
    for html_path in _iter_rendered_html(site_dir):
        text = html_path.read_text(encoding="utf-8")
        m = pattern.search(text)
        if m:
            failures.append(
                f"{html_path.relative_to(site_dir).as_posix()}: hidden id "
                f"{m.group(1)!r} appears in the output")
    return failures


# --- checks 3 + 5: one scan of every rendered page's links ------------------

class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for attr in LINKED_ATTRS:
            v = d.get(attr)
            if v is None:
                continue
            v = v.split("#", 1)[0].split("?", 1)[0]
            if not v or v.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
                continue
            self.links.append(v)

        pages_raw = d.get("data-pages")
        if pages_raw:
            try:
                pages = json.loads(pages_raw)
            except json.JSONDecodeError:
                return
            for p in pages:
                if p.get("thumbnail"):
                    self.links.append(p["thumbnail"])
                zoom_path = (p.get("zoom") or {}).get("path")
                if zoom_path:
                    self.links.append(zoom_path)


def check_links_and_media_manifest(site_dir: Path, manifest_paths: set[str]) -> tuple[list[str], list[str]]:
    """One pass over every rendered page: resolves every href/src value and
    every page path inside a data-pages JSON blob against the file's own
    location (the site is entirely root-relative, never absolute
    `/base_path/...` - see render.py's person_context docstring), and
    returns (broken_links, media_not_in_manifest)."""
    broken: list[str] = []
    not_in_manifest: list[str] = []
    site_dir_r = site_dir.resolve()

    for html_path in _iter_rendered_html(site_dir):
        text = html_path.read_text(encoding="utf-8")
        parser = _LinkExtractor()
        parser.feed(text)
        rel_html = html_path.relative_to(site_dir).as_posix()
        for link in parser.links:
            target_dir = html_path.parent
            target = (target_dir / link / "index.html") if link.endswith("/") else (target_dir / link)
            target = target.resolve()
            if not target.exists():
                broken.append(f"{rel_html}: broken link {link!r}")
                continue
            try:
                rel_to_site = target.relative_to(site_dir_r).as_posix()
            except ValueError:
                continue  # resolved outside site_dir entirely; not a media check concern
            if rel_to_site.startswith("media/") and rel_to_site not in manifest_paths:
                not_in_manifest.append(f"{rel_html}: references {rel_to_site!r}, not in media.json")
    return broken, not_in_manifest


def _manifest_media_paths(manifest: list[dict]) -> set[str]:
    """A PDF's top-level entry (`is_pdf`) carries no thumbnail/display/zoom
    of its own - those live only on each page in `pages` - so only `pages`
    is collected for it; every other entry (photo or non-PDF document)
    carries them directly."""
    paths: set[str] = set()

    def collect(entry: dict):
        paths.add(entry["thumbnail"])
        paths.update(entry.get("display", {}).values())
        zoom = entry.get("zoom")
        if zoom:
            paths.add(zoom["path"])

    for entry in manifest:
        if entry.get("original"):
            paths.add(entry["original"])
        if entry.get("is_pdf"):
            for page in entry.get("pages", []):
                collect(page)
        else:
            collect(entry)
    return paths


# --- check 4: metadata stripped on every published image -------------------

def check_image_metadata(manifest: list[dict], site_dir: Path) -> list[str]:
    """Checks every individually-tracked derivative (thumbnail, each
    display width, zoom) plus one spot-check tile per DZI document (DZI
    tiles aren't individually listed in media.json - only the .dzi
    descriptor is - and a full document can have hundreds of tiles across
    pyramid levels, so checking all of them isn't worth the runtime; one
    tile per DZI, generated by the same dzsave call as every other tile in
    it, is a reasonable spot-check, confirmed separately that dzsave
    strips metadata by default, 6 Oct 2026)."""
    failures = []
    image_paths: set[str] = set()
    dzi_bases: set[str] = set()

    def collect(entry: dict):
        image_paths.add(entry["thumbnail"])
        image_paths.update(entry.get("display", {}).values())
        zoom = entry.get("zoom")
        if zoom:
            if zoom["kind"] == "image":
                image_paths.add(zoom["path"])
            else:
                dzi_bases.add(zoom["path"][:-len(".dzi")])

    for entry in manifest:
        if entry.get("is_pdf"):
            for page in entry.get("pages", []):
                collect(page)
        else:
            collect(entry)

    for rel_path in sorted(image_paths):
        full = site_dir / rel_path
        if not full.exists():
            continue  # reported separately as a broken/missing-file issue
        im = pyvips.Image.new_from_file(str(full))
        bad = [t for t in METADATA_FIELDS if im.get_typeof(t) != 0]
        if bad:
            failures.append(f"{rel_path}: still has {', '.join(bad)}")

    for dzi_base in sorted(dzi_bases):
        tiles_dir = site_dir / f"{dzi_base}_files"
        if not tiles_dir.exists():
            continue
        first_tile = next(tiles_dir.rglob("*.webp"), None)
        if first_tile is None:
            continue
        im = pyvips.Image.new_from_file(str(first_tile))
        bad = [t for t in METADATA_FIELDS if im.get_typeof(t) != 0]
        if bad:
            rel = first_tile.relative_to(site_dir).as_posix()
            failures.append(f"{rel}: still has {', '.join(bad)} (spot-check of {dzi_base} tiles)")
    return failures


# --- check 6: no original file bytes leaked into the output ----------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_no_original_copies(config: "Config", media_links: list[dict],
                              manifest: list[dict]) -> list[str]:
    """BRIEF.md Hard Rule 2: "Original media files are never published.
    Only processed derivatives" - except the one deliberate, config-gated
    exception (publish_original_pdf), which copies the original PDF bytes
    on purpose; that path is excluded here, not flagged.

    Hashing is size-prefiltered: an output derivative is only hashed if
    some referenced original happens to share its exact file size, since
    every real derivative is a recompressed webp at a different size from
    its (much larger, original-format) source - this keeps the check fast
    against the full ~37,000-file media output without hashing all of it."""
    size_to_hashes: dict[int, set[str]] = defaultdict(set)
    for item in media_links:
        resolved = resolve_media_path(config.media_dir, item["file_path"])
        if resolved is not None and resolved.exists():
            size_to_hashes[resolved.stat().st_size].add(_sha256(resolved))

    allowed = {entry["original"] for entry in manifest if entry.get("original")}

    failures = []
    media_root = config.site_dir / "media"
    if not media_root.exists() or not size_to_hashes:
        return failures
    for f in media_root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(config.site_dir).as_posix()
        if rel in allowed:
            continue
        candidates = size_to_hashes.get(f.stat().st_size)
        if not candidates:
            continue
        if _sha256(f) in candidates:
            failures.append(f"{rel}: byte-identical to a source media file (same size+hash)")
    return failures


# --- summary + orchestration -------------------------------------------------

def _missing_media_files(config: "Config", media_links: list[dict]) -> list[str]:
    missing = []
    for item in media_links:
        resolved = resolve_media_path(config.media_dir, item["file_path"])
        if resolved is None or not resolved.exists():
            missing.append(item["id"])
    return missing


def run(config: "Config") -> AuditResult:
    data_dir = config.data_dir
    for name in ("people.json", "sources.json", "media_links.json", "media.json"):
        if not (data_dir / name).exists():
            raise FileNotFoundError(
                f"{data_dir / name} not found - run extract/media/render first")
    if not config.site_dir.exists():
        raise FileNotFoundError(f"{config.site_dir} not found - run 'gedcomwright render' first")

    people = json.loads((data_dir / "people.json").read_text(encoding="utf-8"))
    media_links = json.loads((data_dir / "media_links.json").read_text(encoding="utf-8"))
    manifest = json.loads((data_dir / "media.json").read_text(encoding="utf-8"))

    gedcom = Gedcom(config.resolve_gedcom())
    priv, withheld = _fresh_privacy(config, gedcom)
    hidden_bare = {x.strip("@") for x in priv.hidden_xrefs}

    manifest_paths = _manifest_media_paths(manifest)
    broken_links, media_not_in_manifest = check_links_and_media_manifest(
        config.site_dir, manifest_paths)

    failures = {
        "privacy_rules": check_privacy_rules(people, priv),
        "hidden_in_output": check_no_hidden_in_output(config.site_dir, hidden_bare),
        "media_not_in_manifest": media_not_in_manifest,
        "image_metadata": check_image_metadata(manifest, config.site_dir),
        "broken_links": broken_links,
        "original_copies": check_no_original_copies(config, media_links, manifest),
    }

    group_counts = Counter(priv.people[x].group for x in priv.hidden_xrefs)
    summary = {
        "people_published": len(priv.published_xrefs),
        "people_withheld_by_group": dict(sorted(group_counts.items())),
        "media_published": len(manifest),
        "media_withheld": len(withheld),
        "missing_media_files": _missing_media_files(config, media_links),
    }
    return AuditResult(failures=failures, summary=summary)


_CHECK_TITLES = {
    "privacy_rules": "Published person fails a fresh privacy re-check",
    "hidden_in_output": "Hidden person's ID or page appears in the output",
    "media_not_in_manifest": "Output references a media file not in the manifest",
    "image_metadata": "Published image still has EXIF/GPS/XMP metadata",
    "broken_links": "Broken internal link",
    "original_copies": "Original media file bytes found in the output",
}


def write_report(result: AuditResult, path: Path) -> None:
    lines = ["# Audit report", ""]
    s = result.summary
    lines.append(f"**{s['people_published']} people published, "
                 f"{s['media_published']} media items published, "
                 f"{s['media_withheld']} media items withheld.**")
    lines.append("")
    lines.append("## People withheld, by privacy group")
    lines.append("")
    if not s["people_withheld_by_group"]:
        lines.append("(none)")
    for group, count in s["people_withheld_by_group"].items():
        lines.append(f"- group {group}: {count}")
    lines.append("")
    lines.append(f"## Missing media files ({len(s['missing_media_files'])})")
    lines.append("")
    if not s["missing_media_files"]:
        lines.append("(none)")
    for mid in s["missing_media_files"]:
        lines.append(f"- `{mid}`")
    lines.append("")
    lines.append(f"## Result: {'PASS' if result.ok else 'FAIL'} "
                 f"({result.total_failures} issue(s))")
    lines.append("")
    for key, title in _CHECK_TITLES.items():
        items = result.failures.get(key, [])
        lines.append(f"### {title} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("(none)")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

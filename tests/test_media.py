"""Tests for pipeline.media (BRIEF.md "Stage 2: media").

The synthetic CMYK/16-bit/multi-page-TIFF/MPO-equivalent fixtures required
by BRIEF.md's Testing section are built here directly via pyvips (no
external fixture files needed - pyvips can construct and save these formats
itself), validated against the real export before this file was written.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pyvips

from pipeline import media
from pipeline.config import Config
from pipeline.mediapath import resolve_media_path
from pipeline.overrides import load_media_type_overrides

# A minimal, hand-written, valid single-page PDF (200x100pt) - confirmed to
# open and render correctly with pypdfium2 before this was written.
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 58 >>
stream
BT /F1 18 Tf 20 40 Td (Test PDF page) Tj ET
endstream
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF
"""


# --- mediapath ---------------------------------------------------------

def test_resolve_media_path_rewrites_prefix_case_insensitively(tmp_path):
    media_dir = tmp_path / "MyMedia"
    (media_dir / "sub").mkdir(parents=True)
    (media_dir / "sub" / "photo.jpg").write_bytes(b"x")
    raw = r"C:\Users\someoneelse\Documents\mymedia\sub\photo.jpg"
    assert resolve_media_path(media_dir, raw) == media_dir / "sub" / "photo.jpg"


def test_resolve_media_path_handles_spaces_and_ampersand(tmp_path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    awkward = media_dir / "George Wray & Ann Allingham(3).jpg"
    awkward.write_bytes(b"x")
    assert resolve_media_path(media_dir, str(awkward)) == awkward


def test_resolve_media_path_handles_leading_space_and_double_space(tmp_path):
    media_dir = tmp_path / "Family Tree  Media"  # double space, like the real one
    media_dir.mkdir()
    awkward = media_dir / " bert reid.jpg"  # leading space
    awkward.write_bytes(b"x")
    assert resolve_media_path(media_dir, str(awkward)) == awkward


def test_resolve_media_path_returns_none_if_folder_name_not_found(tmp_path):
    assert resolve_media_path(tmp_path / "Media", r"C:\Somewhere\Else\photo.jpg") is None


# --- classify_type -----------------------------------------------------

def _item(file_path="x.jpg", citation_only=False, mid="M1"):
    return {"id": mid, "file_path": file_path, "citation_only": citation_only}


def test_classify_pdf_is_always_document():
    item = _item("x.pdf")
    assert media.classify_type(item, source_titles=set(), type_overrides={}) == "document"


def test_classify_ancestry_collection_filename_is_document():
    item = _item(r"C:\Media\1921 census of canada(72).jpg")
    titles = {"1921 census of canada"}
    assert media.classify_type(item, source_titles=titles, type_overrides={}) == "document"


def test_classify_citation_only_is_document():
    item = _item("random-name.jpg", citation_only=True)
    assert media.classify_type(item, source_titles=set(), type_overrides={}) == "document"


def test_classify_default_is_photo():
    item = _item("family-photo.jpg")
    assert media.classify_type(item, source_titles=set(), type_overrides={}) == "photo"


def test_classify_override_wins_over_everything():
    item = _item("x.pdf", mid="M9")
    assert media.classify_type(item, source_titles=set(),
                                type_overrides={"M9": "photo"}) == "photo"


# --- image loading: orientation, sRGB, and the synthetic format cases ---

def _save_jpeg_with_orientation(path: Path, orientation: int, w: int = 10, h: int = 6):
    im = pyvips.Image.black(w, h, bands=3) + 0
    im = im.insert(pyvips.Image.black(2, 2, bands=3) + 255, 0, 0)  # marker, top-left
    im = im.copy()
    im.set_type(pyvips.GValue.gint_type, "orientation", orientation)
    im.jpegsave(str(path), Q=95)


def test_load_oriented_srgb_applies_exif_orientation(tmp_path):
    p = tmp_path / "oriented.jpg"
    _save_jpeg_with_orientation(p, orientation=6, w=10, h=6)
    im = media._load_oriented_srgb(p)
    assert (im.width, im.height) == (6, 10)  # swapped by the 90-degree rotation
    assert im.bands == 3
    assert im.interpretation == "srgb"


def test_load_oriented_srgb_handles_cmyk(tmp_path):
    p = tmp_path / "cmyk.tif"
    (pyvips.Image.black(20, 10, bands=4) + 50).copy(interpretation="cmyk").tiffsave(str(p))
    loaded = media._load_oriented_srgb(p)
    assert loaded.bands == 3
    assert loaded.interpretation == "srgb"


def test_load_oriented_srgb_handles_16_bit(tmp_path):
    p = tmp_path / "sixteen.tif"
    (pyvips.Image.black(20, 10, bands=1) + 1000).cast("ushort").tiffsave(str(p))
    loaded = media._load_oriented_srgb(p)
    assert loaded.bands == 3


def test_multipage_tiff_page_selection_picks_the_first_page_only(tmp_path):
    """The core "handle multi-page TIFF" requirement: page=0 must select
    page 1's actual content, not page 2's, and not some blend of both."""
    p = tmp_path / "multi.tif"
    page1 = pyvips.Image.black(10, 10, bands=3) + 10
    page2 = pyvips.Image.black(10, 10, bands=3) + 200
    page1.join(page2, "vertical").tiffsave(str(p), page_height=10)
    first = pyvips.Image.new_from_file(str(p), page=0)
    second = pyvips.Image.new_from_file(str(p), page=1)
    assert (first.width, first.height) == (10, 10)
    assert first.avg() == 10.0
    assert second.avg() == 200.0


def test_load_oriented_srgb_does_not_crash_on_multipage_tiff(tmp_path):
    """BRIEF.md: "handle ... multi-page TIFF (synthetic tests only)" - the
    full pipeline must not crash and must pick a single page (not stack or
    animate them). Doesn't assert exact pixel values: libvips' page_height
    multi-page-WRITING trick doesn't preserve colour-interpretation tags
    faithfully on reload (confirmed directly while writing this test - it
    comes back as 'scrgb' instead of 'srgb' even when explicitly tagged
    before saving), which corrupts colourspace-converted pixel values. A
    real scanner-written multi-page TIFF wouldn't hit this, since it isn't
    produced via this synthetic single-strip trick."""
    p = tmp_path / "multi.tif"
    page1 = pyvips.Image.black(10, 10, bands=3) + 10
    page2 = pyvips.Image.black(10, 10, bands=3) + 200
    page1.join(page2, "vertical").tiffsave(str(p), page_height=10)
    loaded = media._load_oriented_srgb(p)
    assert (loaded.width, loaded.height) == (10, 10)
    assert loaded.bands == 3


def test_load_oriented_srgb_handles_greyscale(tmp_path):
    p = tmp_path / "grey.jpg"
    (pyvips.Image.black(15, 8, bands=1) + 120).jpegsave(str(p))
    assert media._load_oriented_srgb(p).bands == 3


def test_saved_webp_has_no_exif_or_icc_metadata(tmp_path):
    src = tmp_path / "src.jpg"
    _save_jpeg_with_orientation(src, orientation=6)
    im = media._load_oriented_srgb(src)
    out = tmp_path / "out.webp"
    media._save_webp(im, out, 80)
    reloaded = pyvips.Image.new_from_file(str(out))
    assert reloaded.get_typeof("exif-data") == 0
    assert reloaded.get_typeof("icc-profile-data") == 0


# --- generate_derivatives ------------------------------------------------

def test_generate_derivatives_never_upscales(tmp_path):
    im = pyvips.Image.black(300, 200, bands=3) + 100
    out = media.generate_derivatives(im, tmp_path, "media", "abc123", "photo",
                                      media.MediaThresholds())
    assert out["width"] == 300
    for w_str in out["display"]:
        assert int(w_str) <= 300


def test_generate_derivatives_photo_zoom_is_capped_and_a_single_image(tmp_path):
    im = pyvips.Image.black(5000, 3000, bands=3) + 100
    cfg = media.MediaThresholds(photo_zoom_max_width=3500)
    out = media.generate_derivatives(im, tmp_path, "media", "abc123", "photo", cfg)
    assert out["zoom"]["kind"] == "image"
    shard = tmp_path / "abc123"[:2]
    zoom_files = list(shard.glob("*-zoom.webp"))
    assert len(zoom_files) == 1
    assert pyvips.Image.new_from_file(str(zoom_files[0])).width == 3500


def test_generate_derivatives_large_document_uses_dzi(tmp_path):
    im = pyvips.Image.black(5000, 3000, bands=3) + 100
    cfg = media.MediaThresholds(document_dzi_min_long_edge=4000, dzi_tile_size=510)
    out = media.generate_derivatives(im, tmp_path, "media", "doc123", "document", cfg)
    assert out["zoom"]["kind"] == "dzi"
    shard = tmp_path / "doc123"[:2]
    assert list(shard.glob("*.dzi"))
    assert list(shard.glob("*_files"))


def test_generate_derivatives_small_document_uses_a_single_image(tmp_path):
    im = pyvips.Image.black(1000, 800, bands=3) + 100
    cfg = media.MediaThresholds(document_dzi_min_long_edge=4000)
    out = media.generate_derivatives(im, tmp_path, "media", "doc456", "document", cfg)
    assert out["zoom"]["kind"] == "image"


# --- DerivativeCache ---------------------------------------------------

def test_cache_round_trips(tmp_path):
    cache_path = tmp_path / "cache.json"
    marker = tmp_path / "f.webp"
    marker.write_bytes(b"x")
    cache = media.DerivativeCache(cache_path)
    cache.put("key1", {"_files": [str(marker)], "width": 10})
    cache.save()

    reloaded = media.DerivativeCache(cache_path)
    assert reloaded.get("key1") == {"_files": [str(marker)], "width": 10}


def test_cache_misses_if_a_derivative_file_went_missing(tmp_path):
    cache = media.DerivativeCache(tmp_path / "cache.json")
    cache.put("key1", {"_files": [str(tmp_path / "gone.webp")], "width": 10})
    assert cache.get("key1") is None


def test_cache_miss_on_unknown_key(tmp_path):
    assert media.DerivativeCache(tmp_path / "cache.json").get("nope") is None


# --- process_item: resolution, exclusion, full image flow ---------------

def test_process_item_reports_unresolved_without_raising(tmp_path):
    item = {"id": "M1", "file_path": r"C:\Nowhere\real.jpg", "citation_only": False}
    unresolved: list = []
    entry, _ = media.process_item(
        item, media_dir=tmp_path, out_dir=tmp_path / "out", mtype="photo",
        cfg=media.MediaThresholds(), cache=media.DerivativeCache(tmp_path / "c.json"),
        unresolved=unresolved, not_published=[],
    )
    assert entry is None
    assert unresolved == [("M1", r"C:\Nowhere\real.jpg")]


def test_process_item_excludes_htm_and_word_files(tmp_path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    htm = media_dir / "obituary.htm"
    htm.write_text("<html></html>")
    not_published: list = []
    entry, _ = media.process_item(
        {"id": "M2", "file_path": str(htm), "citation_only": False},
        media_dir=media_dir, out_dir=tmp_path / "out", mtype="document",
        cfg=media.MediaThresholds(), cache=media.DerivativeCache(tmp_path / "c.json"),
        unresolved=[], not_published=not_published,
    )
    assert entry is None
    assert not_published == [("M2", str(htm), ".htm")]


def test_process_item_full_image_flow(tmp_path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    img_path = media_dir / "photo.jpg"
    (pyvips.Image.black(1000, 800, bands=3) + 128).jpegsave(str(img_path))
    item = {"id": "M3", "file_path": str(img_path), "citation_only": False,
            "title": "A photo", "people": ["I1"], "portrait_of": []}
    entry, from_cache = media.process_item(
        item, media_dir=media_dir, out_dir=tmp_path / "out", mtype="photo",
        cfg=media.MediaThresholds(), cache=media.DerivativeCache(tmp_path / "c.json"),
        unresolved=[], not_published=[],
    )
    assert entry["type"] == "photo"
    assert entry["width"] == 1000
    assert entry["people"] == ["I1"]
    assert from_cache is False
    # the recorded path is relative to out_dir (production: config.site_dir)
    assert (tmp_path / "out" / entry["thumbnail"][len("media/"):]).exists()


# --- PDF processing ------------------------------------------------------

def test_process_pdf_renders_every_page(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(MINIMAL_PDF)
    entry, from_cache = media.process_pdf(
        {"id": "M4", "title": "A document"}, pdf_path, "pdfhash1", tmp_path / "out",
        media.MediaThresholds(), media.DerivativeCache(tmp_path / "c.json"),
    )
    assert entry["type"] == "document"
    assert entry["is_pdf"] is True
    assert len(entry["pages"]) == 1
    assert entry["pages"][0]["page"] == 1
    assert from_cache is False


def test_process_pdf_reuses_cache_on_second_call(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(MINIMAL_PDF)
    cache = media.DerivativeCache(tmp_path / "c.json")
    media.process_pdf({"id": "M4"}, pdf_path, "pdfhash1", tmp_path / "out",
                       media.MediaThresholds(), cache)
    _, from_cache = media.process_pdf({"id": "M4"}, pdf_path, "pdfhash1", tmp_path / "out",
                                       media.MediaThresholds(), cache)
    assert from_cache is True


def test_process_pdf_does_not_publish_original_by_default(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(MINIMAL_PDF)
    entry, _ = media.process_pdf(
        {"id": "M5"}, pdf_path, "h2", tmp_path / "out",
        media.MediaThresholds(publish_original_pdf=False),
        media.DerivativeCache(tmp_path / "c.json"),
    )
    assert "original" not in entry


def test_process_pdf_publishes_original_when_configured(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(MINIMAL_PDF)
    entry, _ = media.process_pdf(
        {"id": "M6"}, pdf_path, "h3", tmp_path / "out",
        media.MediaThresholds(publish_original_pdf=True),
        media.DerivativeCache(tmp_path / "c.json"),
    )
    assert "original" in entry
    assert (tmp_path / "out" / entry["original"][len("media/"):]).exists()


# --- overrides/media-type.txt ------------------------------------------

def test_load_media_type_overrides(tmp_path):
    p = tmp_path / "media-type.txt"
    p.write_text("@M1@ document\n# a comment\n@M2@ photo\n", encoding="utf-8")
    assert load_media_type_overrides(p) == {"@M1@": "document", "@M2@": "photo"}


def test_load_media_type_overrides_missing_file_returns_empty(tmp_path):
    assert load_media_type_overrides(tmp_path / "nope.txt") == {}


def test_load_media_type_overrides_rejects_malformed_lines(tmp_path):
    p = tmp_path / "media-type.txt"
    p.write_text("@M1@ nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_media_type_overrides(p)


# --- end to end run() -----------------------------------------------------

def test_run_end_to_end(tmp_path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    img_path = media_dir / "family-photo.jpg"
    (pyvips.Image.black(800, 600, bands=3) + 128).jpegsave(str(img_path))
    htm_path = media_dir / "obit.htm"
    htm_path.write_text("<html></html>")

    build_dir = tmp_path / "build"
    (build_dir / "data").mkdir(parents=True)
    media_links = [
        {"id": "M1", "file_path": str(img_path), "title": "Family", "description": None,
         "date_text": None, "citation_only": False, "people": ["I1"], "portrait_of": []},
        {"id": "M2", "file_path": str(htm_path), "title": "Obit", "description": None,
         "date_text": None, "citation_only": False, "people": ["I1"], "portrait_of": []},
        {"id": "M3", "file_path": r"C:\Nowhere\ghost.jpg", "title": "Ghost", "description": None,
         "date_text": None, "citation_only": False, "people": [], "portrait_of": []},
    ]
    (build_dir / "data" / "media_links.json").write_text(
        json.dumps(media_links), encoding="utf-8")

    ged_path = tmp_path / "test.ged"
    ged_path.write_text("0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", encoding="utf-8")

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(ged_path)!r}\n  media_dir: {str(media_dir)!r}\n"
        f"  build_dir: build\nprivacy:\n  home_person: '@I1@'\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    summary = media.run(config)

    assert summary["processed"] == 1
    assert summary["photos"] == 1
    assert summary["not_published"] == 1
    assert summary["unresolved"] == 1

    manifest = json.loads((build_dir / "data" / "media.json").read_text(encoding="utf-8"))
    assert len(manifest) == 1
    assert manifest[0]["id"] == "M1"

    report = (build_dir / "reports" / "media.md").read_text(encoding="utf-8")
    assert "M2" in report
    assert "M3" in report


def test_run_skips_ancestry_only_media_entirely(tmp_path):
    """Will, 7 Oct 2026: "if we're not going to show the ancestry sources
    perhaps they shouldn't be in the upload" - render.py already never
    links to is_ancestry_only media, so media.py shouldn't spend I/O
    generating derivatives for it, or publish the files at all."""
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    kept_path = media_dir / "family-photo.jpg"
    (pyvips.Image.black(800, 600, bands=3) + 128).jpegsave(str(kept_path))
    skipped_path = media_dir / "1901-census.jpg"
    (pyvips.Image.black(800, 600, bands=3) + 64).jpegsave(str(skipped_path))

    build_dir = tmp_path / "build"
    (build_dir / "data").mkdir(parents=True)
    media_links = [
        {"id": "M1", "file_path": str(kept_path), "title": "Family", "description": None,
         "date_text": None, "citation_only": False, "people": ["I1"], "portrait_of": [],
         "is_ancestry_only": False},
        {"id": "M2", "file_path": str(skipped_path), "title": "1901 Census", "description": None,
         "date_text": None, "citation_only": True, "people": ["I1"], "portrait_of": [],
         "is_ancestry_only": True},
    ]
    (build_dir / "data" / "media_links.json").write_text(
        json.dumps(media_links), encoding="utf-8")

    ged_path = tmp_path / "test.ged"
    ged_path.write_text("0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(ged_path)!r}\n  media_dir: {str(media_dir)!r}\n"
        f"  build_dir: build\nprivacy:\n  home_person: '@I1@'\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    summary = media.run(config)

    assert summary["processed"] == 1
    assert summary["ancestry_only_skipped"] == 1

    manifest = json.loads((build_dir / "data" / "media.json").read_text(encoding="utf-8"))
    assert [m["id"] for m in manifest] == ["M1"]

    media_root = build_dir / "site" / "media"
    produced_files = list(media_root.rglob("*")) if media_root.exists() else []
    assert all("census" not in str(p).lower() for p in produced_files)


def test_run_is_incremental_on_a_second_call(tmp_path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    img_path = media_dir / "family-photo.jpg"
    (pyvips.Image.black(800, 600, bands=3) + 128).jpegsave(str(img_path))

    build_dir = tmp_path / "build"
    (build_dir / "data").mkdir(parents=True)
    media_links = [{"id": "M1", "file_path": str(img_path), "title": "Family",
                    "description": None, "date_text": None, "citation_only": False,
                    "people": ["I1"], "portrait_of": []}]
    (build_dir / "data" / "media_links.json").write_text(
        json.dumps(media_links), encoding="utf-8")
    ged_path = tmp_path / "test.ged"
    ged_path.write_text("0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(ged_path)!r}\n  media_dir: {str(media_dir)!r}\n"
        f"  build_dir: build\nprivacy:\n  home_person: '@I1@'\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    first = media.run(config)
    second = media.run(config)
    assert first["cached"] == 0
    assert second["cached"] == 1


def test_run_raises_a_clear_error_if_extract_has_not_run(tmp_path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    ged_path = tmp_path / "test.ged"
    ged_path.write_text("0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(ged_path)!r}\n  media_dir: {str(media_dir)!r}\n"
        f"  build_dir: build\nprivacy:\n  home_person: '@I1@'\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        media.run(config)

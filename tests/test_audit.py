"""Tests for pipeline.audit (BRIEF.md Stage 4: "verify independently with
an audit step that fails the build"). Each check is tested directly against
small, hand-built inputs so a failure pinpoints which check broke, plus one
end-to-end pass over the real extract+render pipeline on the synthetic
fixture as a regression test that the pipeline's own output satisfies its
own audit."""
from __future__ import annotations

import json
from pathlib import Path

import pyvips
import pytest

from pipeline import audit
from pipeline.config import Config
from pipeline.extract import run as extract_run
from pipeline.gedcom import Gedcom
from pipeline.render import render_site

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.ged"


# --- check 1: privacy rules --------------------------------------------

def test_check_privacy_rules_flags_a_wrongly_published_person():
    gedcom = Gedcom(FIXTURE)
    priv, _ = audit._fresh_privacy(
        _FakeConfig(home_person="@I1@"), gedcom)
    # @I1@ is the home person - always hidden - but pretend it made it into
    # people.json anyway.
    people = [{"id": "I1", "name": {"display": "William Testwright"}}]
    failures = audit.check_privacy_rules(people, priv)
    assert len(failures) == 1
    assert "I1" in failures[0]


def test_check_privacy_rules_passes_a_rightly_published_person():
    gedcom = Gedcom(FIXTURE)
    priv, _ = audit._fresh_privacy(_FakeConfig(home_person="@I1@"), gedcom)
    # @I2@ has a death record - group 2, always published.
    people = [{"id": "I2", "name": {"display": "Robert Testwright"}}]
    assert audit.check_privacy_rules(people, priv) == []


class _FakeConfig:
    """Just enough of Config for _fresh_privacy()."""
    def __init__(self, home_person, min_generation=2):
        from pipeline.privacy import Thresholds
        self.home_person = home_person
        self.min_generation = min_generation
        self.thresholds = Thresholds()
        self.overrides_dir = Path(__file__).parent / "fixtures" / "_no_overrides_here"


# --- check 2: hidden people never appear in the output ---------------------

def test_check_no_hidden_in_output_flags_a_leaked_directory(tmp_path):
    people_dir = tmp_path / "people" / "william-testwright-I1"
    people_dir.mkdir(parents=True)
    (people_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    failures = audit.check_no_hidden_in_output(tmp_path, {"I1"})
    assert any("I1" in f for f in failures)


def test_check_no_hidden_in_output_flags_a_leaked_id_in_html(tmp_path):
    out_dir = tmp_path / "people" / "robert-testwright-I2"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        '<a href="../../people/unknown-I1/">link</a>', encoding="utf-8")
    failures = audit.check_no_hidden_in_output(tmp_path, {"I1"})
    assert len(failures) == 1
    assert "robert-testwright-I2" in failures[0]


def test_check_no_hidden_in_output_does_not_false_positive_on_prefix_overlap(tmp_path):
    """A hidden id 'I45' must not falsely match inside a different, longer
    id like 'I450' - that would make the check useless noise on any real
    tree with ids that happen to be numeric prefixes of each other."""
    out_dir = tmp_path / "people" / "someone-I450"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text("ordinary content, no leak", encoding="utf-8")
    failures = audit.check_no_hidden_in_output(tmp_path, {"I45"})
    assert failures == []


def test_check_no_hidden_in_output_clean_site_passes(tmp_path):
    out_dir = tmp_path / "people" / "robert-testwright-I2"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text("<html>nothing hidden here</html>", encoding="utf-8")
    assert audit.check_no_hidden_in_output(tmp_path, {"I1"}) == []


# --- checks 3 + 5: links and media manifest ---------------------------------

def test_check_links_flags_a_broken_link(tmp_path):
    out_dir = tmp_path / "people" / "a"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        '<a href="../../people/does-not-exist/">gone</a>', encoding="utf-8")
    broken, not_in_manifest = audit.check_links_and_media_manifest(tmp_path, set())
    assert len(broken) == 1
    assert not_in_manifest == []


def test_check_links_flags_media_not_in_manifest(tmp_path):
    (tmp_path / "media" / "ab").mkdir(parents=True)
    (tmp_path / "media" / "ab" / "stray.webp").write_bytes(b"not a real webp, just bytes")
    out_dir = tmp_path / "people" / "a"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        '<img src="../../media/ab/stray.webp">', encoding="utf-8")
    broken, not_in_manifest = audit.check_links_and_media_manifest(tmp_path, set())
    assert broken == []
    assert len(not_in_manifest) == 1
    assert "media/ab/stray.webp" in not_in_manifest[0]


def test_check_links_passes_a_path_in_the_manifest(tmp_path):
    (tmp_path / "media" / "ab").mkdir(parents=True)
    (tmp_path / "media" / "ab" / "real.webp").write_bytes(b"fake webp bytes")
    out_dir = tmp_path / "people" / "a"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        '<img src="../../media/ab/real.webp">', encoding="utf-8")
    broken, not_in_manifest = audit.check_links_and_media_manifest(
        tmp_path, {"media/ab/real.webp"})
    assert broken == []
    assert not_in_manifest == []


def test_check_links_parses_data_pages_json_for_multi_page_documents(tmp_path):
    """Regression: data-pages replaced the old single-path data-tilesource
    attribute for the multi-page document viewer (6 Oct 2026) - audit must
    look inside that JSON blob, not just at href/src, or every document
    page path silently stops being checked at all."""
    (tmp_path / "media" / "ab").mkdir(parents=True)
    (tmp_path / "media" / "ab" / "p1.webp").write_bytes(b"x")
    out_dir = tmp_path / "people" / "a"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        "<a class=\"gallery-document\" data-pages='"
        '[{"thumbnail": "../../media/ab/p1-thumb.webp", '
        '"zoom": {"kind": "image", "path": "../../media/ab/p1.webp"}}, '
        '{"thumbnail": "../../media/ab/p2-thumb.webp", '
        '"zoom": {"kind": "image", "path": "../../media/ab/p2.webp"}}]'
        "'></a>",
        encoding="utf-8",
    )
    broken, not_in_manifest = audit.check_links_and_media_manifest(
        tmp_path, {"media/ab/p1.webp"})
    # p1-thumb.webp and p2* don't exist on disk at all -> broken;
    # p1.webp exists and is in the manifest -> fine, not flagged either way.
    assert any("p1-thumb.webp" in b for b in broken)
    assert any("p2-thumb.webp" in b for b in broken)
    assert any("p2.webp" in b for b in broken)
    assert not any("p1.webp" in b and "thumb" not in b for b in broken)


def test_check_links_ignores_external_and_hash_and_mailto(tmp_path):
    out_dir = tmp_path / "people" / "a"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        '<a href="https://example.com">ext</a>'
        '<a href="#main">anchor</a>'
        '<a href="mailto:x@example.com">mail</a>',
        encoding="utf-8")
    broken, not_in_manifest = audit.check_links_and_media_manifest(tmp_path, set())
    assert broken == []
    assert not_in_manifest == []


def test_check_links_resolves_directory_style_links_to_index_html(tmp_path):
    (tmp_path / "surnames").mkdir()
    (tmp_path / "surnames" / "index.html").write_text("ok", encoding="utf-8")
    out_dir = tmp_path / "people" / "a"
    out_dir.mkdir(parents=True)
    (out_dir / "index.html").write_text(
        '<a href="../../surnames/">surnames</a>', encoding="utf-8")
    broken, _ = audit.check_links_and_media_manifest(tmp_path, set())
    assert broken == []


# --- check 4: image metadata -------------------------------------------

def test_check_image_metadata_flags_unstripped_exif(tmp_path):
    im = pyvips.Image.black(100, 100).copy(interpretation="srgb")
    im.set_type(pyvips.GValue.blob_type, "exif-data", b"fake-exif-bytes")
    path = tmp_path / "media" / "ab" / "leaky-thumb.webp"
    path.parent.mkdir(parents=True)
    im.webpsave(str(path), strip=False)

    manifest = [{"thumbnail": "media/ab/leaky-thumb.webp", "display": {}, "zoom": None}]
    failures = audit.check_image_metadata(manifest, tmp_path)
    assert len(failures) == 1
    assert "exif-data" in failures[0]


def test_manifest_media_paths_handles_pdf_entries_with_no_top_level_thumbnail():
    """A PDF's top-level manifest entry (is_pdf) carries no thumbnail/
    display/zoom of its own - only each page in `pages` does - this must
    not KeyError on real data."""
    manifest = [{
        "id": "M9", "type": "document", "is_pdf": True,
        "pages": [{"page": 1, "thumbnail": "media/ab/p1-thumb.webp",
                   "display": {}, "zoom": {"kind": "image", "path": "media/ab/p1-zoom.webp"}}],
    }]
    paths = audit._manifest_media_paths(manifest)
    assert paths == {"media/ab/p1-thumb.webp", "media/ab/p1-zoom.webp"}


def test_check_image_metadata_handles_pdf_entries_with_no_top_level_thumbnail(tmp_path):
    im = pyvips.Image.black(100, 100).copy(interpretation="srgb")
    path = tmp_path / "media" / "ab" / "p1-thumb.webp"
    path.parent.mkdir(parents=True)
    im.webpsave(str(path), strip=True)

    manifest = [{
        "id": "M9", "type": "document", "is_pdf": True,
        "pages": [{"page": 1, "thumbnail": "media/ab/p1-thumb.webp",
                   "display": {}, "zoom": None}],
    }]
    assert audit.check_image_metadata(manifest, tmp_path) == []


def test_check_image_metadata_passes_a_stripped_image(tmp_path):
    im = pyvips.Image.black(100, 100).copy(interpretation="srgb")
    im.set_type(pyvips.GValue.blob_type, "exif-data", b"fake-exif-bytes")
    path = tmp_path / "media" / "ab" / "clean-thumb.webp"
    path.parent.mkdir(parents=True)
    im.webpsave(str(path), strip=True)

    manifest = [{"thumbnail": "media/ab/clean-thumb.webp", "display": {}, "zoom": None}]
    assert audit.check_image_metadata(manifest, tmp_path) == []


# --- check 6: no original file bytes in the output --------------------

def test_check_no_original_copies_flags_a_byte_identical_copy(tmp_path):
    media_dir = tmp_path / "source_media"
    media_dir.mkdir()
    original = media_dir / "photo.jpg"
    original.write_bytes(b"original bytes, pretend this is a jpeg" * 100)

    site_dir = tmp_path / "site"
    (site_dir / "media" / "ab").mkdir(parents=True)
    leaked = site_dir / "media" / "ab" / "oops.jpg"
    leaked.write_bytes(original.read_bytes())  # byte-identical copy

    config = _FakeMediaConfig(media_dir=media_dir, site_dir=site_dir)
    media_links = [{"id": "M1", "file_path": str(original)}]
    failures = audit.check_no_original_copies(config, media_links, manifest=[])
    assert len(failures) == 1
    assert "oops.jpg" in failures[0]


def test_check_no_original_copies_passes_a_real_derivative(tmp_path):
    media_dir = tmp_path / "source_media"
    media_dir.mkdir()
    original = media_dir / "photo.jpg"
    original.write_bytes(b"original bytes, pretend this is a jpeg" * 100)

    site_dir = tmp_path / "site"
    (site_dir / "media" / "ab").mkdir(parents=True)
    derivative = site_dir / "media" / "ab" / "thumb.webp"
    derivative.write_bytes(b"a totally different, much smaller re-encoded file")

    config = _FakeMediaConfig(media_dir=media_dir, site_dir=site_dir)
    media_links = [{"id": "M1", "file_path": str(original)}]
    assert audit.check_no_original_copies(config, media_links, manifest=[]) == []


def test_check_no_original_copies_allows_the_deliberate_pdf_exception(tmp_path):
    media_dir = tmp_path / "source_media"
    media_dir.mkdir()
    original = media_dir / "doc.pdf"
    original.write_bytes(b"pdf bytes" * 50)

    site_dir = tmp_path / "site"
    (site_dir / "media" / "ab").mkdir(parents=True)
    copied = site_dir / "media" / "ab" / "doc.pdf"
    copied.write_bytes(original.read_bytes())

    config = _FakeMediaConfig(media_dir=media_dir, site_dir=site_dir)
    media_links = [{"id": "M1", "file_path": str(original)}]
    manifest = [{"thumbnail": "media/ab/other.webp", "display": {}, "zoom": None,
                 "original": "media/ab/doc.pdf"}]
    assert audit.check_no_original_copies(config, media_links, manifest) == []


class _FakeMediaConfig:
    def __init__(self, media_dir, site_dir):
        self.media_dir = media_dir
        self.site_dir = site_dir


# --- end-to-end: the real pipeline's own output passes its own audit -------

@pytest.fixture
def built_config(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(FIXTURE)!r}\n"
        f"  media_dir: {str(tmp_path / 'media')!r}\n  build_dir: build\n"
        f"privacy:\n  home_person: '@I1@'\n  min_generation: 2\n"
        f"site:\n  title: Test Site\n  domain: example.com\n  base_path: ''\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    extract_run(config)
    (config.data_dir / "media.json").write_text("[]", encoding="utf-8")
    render_site(config)
    return config


def test_audit_passes_on_the_real_pipelines_own_output(built_config):
    result = audit.run(built_config)
    assert result.failures["privacy_rules"] == []
    assert result.failures["hidden_in_output"] == []
    assert result.failures["media_not_in_manifest"] == []
    assert result.failures["image_metadata"] == []
    assert result.failures["broken_links"] == []
    assert result.failures["original_copies"] == []
    assert result.ok
    assert result.summary["people_published"] > 0

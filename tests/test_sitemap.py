"""Tests for build/site/sitemap.xml (BRIEF.md Stage 3: "Canonical URLs,
sitemap.xml and robots.txt"), written by render_site on every full render."""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pipeline import render
from pipeline.config import Config
from pipeline.extract import run as extract_run

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.ged"
NS = {"sm": render.SITEMAP_NS}


def _config(tmp_path: Path, *, domain: str = "example.com", base_path: str = "/gene") -> Config:
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(FIXTURE)!r}\n"
        f"  media_dir: {str(tmp_path / 'media')!r}\n  build_dir: build\n"
        f"privacy:\n  home_person: '@I1@'\n  min_generation: 2\n"
        f"site:\n  title: Test Site\n  domain: {domain!r}\n  base_path: {base_path!r}\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    extract_run(config)
    (config.data_dir / "media.json").write_text("[]", encoding="utf-8")
    return config


@pytest.fixture
def built_config(tmp_path):
    return _config(tmp_path)


def _people(config: Config) -> list[dict]:
    return json.loads((config.data_dir / "people.json").read_text(encoding="utf-8"))


def _write_people(config: Config, people: list[dict]) -> None:
    (config.data_dir / "people.json").write_text(json.dumps(people), encoding="utf-8")


def _locs(config: Config) -> list[str]:
    root = ET.parse(config.site_dir / "sitemap.xml").getroot()
    assert root.tag == f"{{{render.SITEMAP_NS}}}urlset"
    return [loc.text for loc in root.findall("sm:url/sm:loc", NS)]


# --- URL correctness -----------------------------------------------------

def test_sitemap_urls_are_exactly_the_rendered_pages_canonicals(built_config):
    """Every page with a canonical tag is listed under that exact URL, and
    nothing else is - a non-empty base_path, so a missing or doubled prefix
    would show."""
    summary = render.render_site(built_config)
    locs = _locs(built_config)

    canonicals = set()
    for page in built_config.site_dir.rglob("index.html"):
        if "pagefind" in page.parts:
            continue
        m = re.search(r'<link rel="canonical" href="([^"]+)">', page.read_text(encoding="utf-8"))
        assert m, f"{page} has no canonical tag"
        canonicals.add(m.group(1))

    assert len(locs) == len(set(locs)) == summary["sitemap_urls"]
    assert set(locs) == canonicals
    assert locs[:3] == ["https://example.com/gene/", "https://example.com/gene/about/",
                        "https://example.com/gene/surnames/"]
    assert all(loc.startswith("https://example.com/gene/") and loc.endswith("/") for loc in locs)


def test_sitemap_has_no_lastmod_or_other_optional_fields(built_config):
    render.render_site(built_config)
    root = ET.parse(built_config.site_dir / "sitemap.xml").getroot()
    for url in root.findall("sm:url", NS):
        assert [child.tag for child in url] == [f"{{{render.SITEMAP_NS}}}loc"]


def test_sitemap_with_empty_base_path_sits_at_domain_root(tmp_path):
    config = _config(tmp_path, base_path="")
    render.render_site(config)
    locs = _locs(config)
    assert locs[0] == "https://example.com/"
    assert not any("//" in loc.removeprefix("https://") for loc in locs)


def test_sitemap_is_byte_identical_across_rebuilds(built_config):
    render.render_site(built_config)
    first = (built_config.site_dir / "sitemap.xml").read_bytes()
    render.render_site(built_config)
    assert (built_config.site_dir / "sitemap.xml").read_bytes() == first
    assert first.startswith(b"<?xml version='1.0' encoding='utf-8'?>")


# --- privacy and exclusions ------------------------------------------------

def test_sitemap_excludes_hidden_people_404_and_assets(built_config):
    """@I1@ is always hidden in the synthetic fixture (Hard Rule 1)."""
    render.render_site(built_config)
    locs = _locs(built_config)
    assert not any(loc.endswith("-I1/") or "/I1/" in loc for loc in locs)
    # "404.html", not bare "404": real xrefs like @I404@ end up in person slugs
    for excluded in ("404.html", "/static/", "/media/", "/pagefind/", ".html", ".xml"):
        assert not any(excluded in loc for loc in locs), excluded


def test_sitemap_ignores_stale_pages_left_in_the_output_folder(built_config):
    stale = built_config.site_dir / "people" / "old-person-I999"
    stale.mkdir(parents=True)
    (stale / "index.html").write_text("<p>left over</p>", encoding="utf-8")
    render.render_site(built_config)
    assert not any("old-person-I999" in loc for loc in _locs(built_config))


def test_sitemap_omits_a_person_whose_page_failed_to_render(built_config):
    people = _people(built_config)
    rendered = {p["id"] for p in people} - {people[0]["id"]}
    render.write_sitemap(built_config, rendered)
    locs = _locs(built_config)
    assert not any(f"/people/{people[0]['slug']}/" in loc for loc in locs)
    assert any(f"/people/{people[1]['slug']}/" in loc for loc in locs)


def test_no_domain_skips_the_sitemap_and_removes_a_stale_one(tmp_path):
    config = _config(tmp_path, domain="")
    config.site_dir.mkdir(parents=True)
    (config.site_dir / "sitemap.xml").write_text("stale", encoding="utf-8")
    summary = render.render_site(config)
    assert summary["sitemap_urls"] is None
    assert not (config.site_dir / "sitemap.xml").exists()


# --- updates when published people change ----------------------------------

def test_sitemap_drops_a_person_removed_from_the_published_set(built_config):
    render.render_site(built_config)
    people = _people(built_config)
    removed = people[0]
    assert any(loc.endswith(f"/people/{removed['slug']}/") for loc in _locs(built_config))

    _write_people(built_config, people[1:])
    render.render_site(built_config)  # removed's old page directory is still on disk
    assert not any(removed["slug"] in loc for loc in _locs(built_config))


def test_sitemap_adds_a_newly_published_person(built_config):
    render.render_site(built_config)
    people = _people(built_config)
    added = copy.deepcopy(people[0])
    added["id"], added["slug"] = "I98", "added-person-I98"
    _write_people(built_config, people + [added])

    summary = render.render_site(built_config)
    assert summary["errors"] == []
    assert "https://example.com/gene/people/added-person-I98/" in _locs(built_config)

"""Tests for pipeline.render (BRIEF.md Stage 3), deferred until the design
itself was signed off (9 Oct 2026) so test-writing effort wasn't spent on a
page that might still change."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import render
from pipeline.config import Config
from pipeline.extract import run as extract_run
from pipeline.privacy import PersonPrivacy

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.ged"


# --- surname_groups -------------------------------------------------------

def _person(xref_id, given, surname, birth_year=None, death_year=None):
    return {
        "id": xref_id,
        "name": {"given": given, "surname": surname,
                 "display": f"{given} {surname}".strip()},
        "birth_year": birth_year, "death_year": death_year,
    }


def test_source_count_counts_distinct_sources_not_citations():
    """Five citations to the same one source should show '1 source', not 5."""
    person = {"facts": [
        {"citations": [{"source_id": "S1"}, {"source_id": "S1"}]},
        {"citations": [{"source_id": "S1"}, {"source_id": "S2"}]},
        {"citations": []},
    ]}
    assert render._source_count(person) == 2


def test_surname_groups_merges_case_variants():
    """11 real pairs in the actual export differ only by case or stray
    punctuation ('Marshall'/'MARSHALL') - grouping by exact string would
    silently let one overwrite the other's page on disk."""
    people = [
        _person("I1", "Jane", "Marshall"),
        _person("I2", "John", "MARSHALL"),
        _person("I3", "Jim", "Marshall"),
    ]
    groups = render.surname_groups(people)
    assert len(groups) == 1
    assert groups[0]["count"] == 3
    assert groups[0]["label"] == "Marshall"  # the more common exact casing


def test_surname_groups_empty_surname_is_unknown_and_sorted_last():
    people = [_person("I1", "Ellen", ""), _person("I2", "Jane", "Abbott")]
    groups = render.surname_groups(people)
    assert groups[-1]["key"] == "unknown"
    assert groups[-1]["label"] == "Surname unknown"
    assert groups[0]["label"] == "Abbott"


def test_surname_groups_sorted_alphabetically_by_label():
    people = [_person("I1", "A", "Wray"), _person("I2", "B", "Abbott"),
              _person("I3", "C", "Methven")]
    groups = render.surname_groups(people)
    assert [g["label"] for g in groups] == ["Abbott", "Methven", "Wray"]


def test_surname_groups_members_sorted_by_birth_year():
    people = [_person("I1", "Late", "Wray", birth_year=1900),
              _person("I2", "Early", "Wray", birth_year=1850),
              _person("I3", "Unknown", "Wray", birth_year=None)]
    groups = render.surname_groups(people)
    names = [p["name"]["given"] for p in groups[0]["people"]]
    assert names == ["Early", "Late", "Unknown"]  # unknown birth sorts last


# --- vital_facts -----------------------------------------------------------

def _fact(tag, date_display=None, place=None):
    return {"tag": tag,
            "date": {"display": date_display, "parsed": True} if date_display else None,
            "place": {"text": place} if place else None}


def test_vital_facts_picks_only_birth_death_burial():
    facts = [_fact("RESI", "1900", "Ontario"), _fact("BIRT", "1838", "Hawkesville"),
              _fact("DEAT", "1888", "Toronto"), _fact("_MILT")]
    vitals = render.vital_facts(facts)
    assert [v["label"] for v in vitals] == ["Born", "Died"]
    assert vitals[0]["date"] == "1838"
    assert vitals[0]["place"] == "Hawkesville"


def test_vital_facts_missing_ones_are_omitted_not_blanked():
    vitals = render.vital_facts([_fact("BIRT", "1838", "Hawkesville")])
    assert len(vitals) == 1
    assert vitals[0]["label"] == "Born"


def test_vital_facts_handles_no_date_gracefully():
    vitals = render.vital_facts([_fact("BURI", place="Souchez, France")])
    assert vitals[0]["date"] is None
    assert vitals[0]["place"] == "Souchez, France"


# --- relation_summary -------------------------------------------------------

def _ref(label, hidden=False, slug=None):
    return {"hidden": hidden, "label": label, **({"slug": slug} if slug else {})}


def test_relation_summary_son_of_two_parents():
    person = {"sex": "M"}
    lines = render.relation_summary(person, [_ref("Robert Wray"), _ref("Jane Methven")], [], [])
    assert lines == ["Son of Robert Wray and Jane Methven"]


def test_relation_summary_daughter_of_one_parent():
    person = {"sex": "F"}
    lines = render.relation_summary(person, [_ref("Robert Wray")], [], [])
    assert lines == ["Daughter of Robert Wray"]


def test_relation_summary_unknown_sex_says_child():
    person = {"sex": None}
    lines = render.relation_summary(person, [_ref("Robert Wray")], [], [])
    assert lines == ["Child of Robert Wray"]


def test_relation_summary_married():
    person = {"sex": "M"}
    lines = render.relation_summary(person, [], [_ref("Jane Methven")], [])
    assert lines == ["Married Jane Methven"]


def test_relation_summary_lists_up_to_four_children_by_name():
    person = {"sex": "M"}
    kids = [_ref(n) for n in ("Alice", "Bob", "Cora", "Dan")]
    lines = render.relation_summary(person, [], [], kids)
    assert lines == ["Parent of Alice, Bob, Cora and Dan"]


def test_relation_summary_counts_more_than_four_children():
    person = {"sex": "M"}
    kids = [_ref(n) for n in ("Alice", "Bob", "Cora", "Dan", "Eve")]
    lines = render.relation_summary(person, [], [], kids)
    assert lines == ["Parent of 5 children"]


def test_relation_summary_empty_when_no_family():
    assert render.relation_summary({"sex": "M"}, [], [], []) == []


# --- resolve_relative --------------------------------------------------

def test_resolve_relative_published_person():
    people_by_id = {"I1": {"name": {"display": "Jane Methven"}, "slug": "jane-methven-I1"}}
    ref = render.resolve_relative({"id": "I1", "hidden": False}, people_by_id)
    assert ref == {"hidden": False, "label": "Jane Methven", "slug": "jane-methven-I1"}


def test_resolve_relative_hidden_shows_surname_only():
    ref = render.resolve_relative({"hidden": True, "surname": "Wray"}, {})
    assert ref == {"hidden": True, "label": "Living Wray"}


def test_resolve_relative_hidden_no_surname():
    ref = render.resolve_relative({"hidden": True, "surname": ""}, {})
    assert ref == {"hidden": True, "label": "Living"}


# --- fact_label --------------------------------------------------------

def test_fact_label_marriage_names_a_published_spouse():
    people_by_id = {"I2": {"name": {"display": "Jane Methven"}}}
    fact = {"tag": "MARR", "label": None, "date": {"display": "1860", "parsed": True},
            "spouse_id": "I2", "spouse_published": True}
    assert render.fact_label(fact, people_by_id) == "Marriage to Jane Methven — 1860"


def test_fact_label_marriage_with_hidden_spouse_omits_name():
    fact = {"tag": "MARR", "label": None, "date": None,
            "spouse_id": "I2", "spouse_published": False}
    assert render.fact_label(fact, {}) == "Marriage"


def test_fact_label_uses_known_tag_labels():
    fact = {"tag": "BAPM", "label": None, "date": None}
    assert render.fact_label(fact, {}) == "Baptism"


def test_fact_label_shows_unparsed_date_with_a_usable_display_string():
    """Regression: a slash-formatted date ('16/07/2020') isn't a GEDCOM
    date shape, so it comes back parsed=False - but dates.parse_date()
    still gives it a usable raw `display` string, same as vital_facts()
    already shows above the fold. The Timeline used to silently drop the
    date in this case (gated on `parsed` instead of `display`), found by
    Will on a real DEAT fact shaped exactly like this."""
    fact = {"tag": "DEAT", "label": None,
            "date": {"display": "16/07/2020", "parsed": False}}
    assert render.fact_label(fact, {}) == "Death — 16/07/2020"


def test_fact_label_falls_back_to_titlecased_tag():
    fact = {"tag": "FOOBAR", "label": None, "date": None}
    assert render.fact_label(fact, {}) == "Foobar"


# --- group_citations: Ancestry-sourced citation images suppressed ------
# (Will's call, 6 Oct 2026 - reversing BRIEF.md's original "no Ancestry
# exclusion flag" - citation text stays, record-image thumbnails don't.)

def _person_with_one_citation(source_id):
    return {"facts": [{
        "tag": "BIRT", "label": None, "date": None,
        "citations": [{"source_id": source_id, "page": None, "notes": [],
                        "link": None, "media": ["M1"]}],
    }]}


def test_group_citations_suppresses_media_for_ancestry_source(tmp_path):
    sources_by_id = {"S1": {"id": "S1", "title": "1881 England Census",
                             "is_other_researchers_tree": False, "is_ancestry": True}}
    media_by_id = {"M1": {"id": "M1", "type": "photo", "thumbnail": "media/ab/m1-thumb.webp",
                           "zoom": {"kind": "image", "path": "media/ab/m1-zoom.webp"}}}
    person = _person_with_one_citation("S1")
    record_groups, other_groups = render.group_citations(
        person, sources_by_id, {}, media_by_id, tmp_path, "../../")
    assert record_groups[0]["citations"][0]["media_items"] == []
    assert record_groups[0]["citations"][0]["page"] is None  # citation text untouched


def test_group_citations_keeps_media_for_non_ancestry_source(tmp_path):
    import pyvips
    (tmp_path / "media" / "ab").mkdir(parents=True)
    pyvips.Image.black(10, 10).copy(interpretation="srgb").webpsave(
        str(tmp_path / "media" / "ab" / "m1-zoom.webp"))
    sources_by_id = {"S1": {"id": "S1", "title": "Testshire Parish Registers",
                             "is_other_researchers_tree": False, "is_ancestry": False}}
    media_by_id = {"M1": {"id": "M1", "type": "photo", "thumbnail": "media/ab/m1-thumb.webp",
                           "zoom": {"kind": "image", "path": "media/ab/m1-zoom.webp"}}}
    person = _person_with_one_citation("S1")
    record_groups, _ = render.group_citations(
        person, sources_by_id, {}, media_by_id, tmp_path, "../../")
    assert len(record_groups[0]["citations"][0]["media_items"]) == 1


# --- group_citations: identical duplicate citations collapse into one -----
# (Will found this on the real site, 7 Oct 2026, James Wray @I313@: FTM's
# own data has two SOUR citations under one RESI fact, same source and page
# text, but two different scanned images - a genuine duplicate GEDCOM entry,
# confirmed by reading the raw tree, not a pipeline bug. Visible as a
# duplicate line precisely because nothing in the rendered text tells the
# two citations apart.)

def test_group_citations_merges_identical_duplicate_citations():
    sources_by_id = {"S1": {"id": "S1", "title": "Directory",
                             "is_other_researchers_tree": False, "is_ancestry": False}}
    person = {"facts": [{
        "tag": "RESI", "label": None, "date": {"display": "1890", "parsed": True},
        "citations": [
            {"source_id": "S1", "page": "Farmers Directory, 1890", "notes": [], "link": None, "media": []},
            {"source_id": "S1", "page": "Farmers Directory, 1890", "notes": [], "link": None, "media": []},
            {"source_id": "S1", "page": None, "notes": [], "link": None, "media": []},
        ],
    }]}
    record_groups, _ = render.group_citations(person, sources_by_id, {}, {}, Path("."), "../../")
    citations = record_groups[0]["citations"]
    assert len(citations) == 2  # the identical pair merged; the distinct one kept


def test_group_citations_merges_media_items_from_duplicate_citations(tmp_path):
    import pyvips
    (tmp_path / "media" / "ab").mkdir(parents=True)
    for name in ("m1", "m2"):
        pyvips.Image.black(10, 10).copy(interpretation="srgb").webpsave(
            str(tmp_path / "media" / "ab" / f"{name}-zoom.webp"))
    sources_by_id = {"S1": {"id": "S1", "title": "Directory",
                             "is_other_researchers_tree": False, "is_ancestry": False}}
    media_by_id = {
        "M1": {"id": "M1", "type": "photo", "thumbnail": "media/ab/m1-thumb.webp",
               "zoom": {"kind": "image", "path": "media/ab/m1-zoom.webp"}},
        "M2": {"id": "M2", "type": "photo", "thumbnail": "media/ab/m2-thumb.webp",
               "zoom": {"kind": "image", "path": "media/ab/m2-zoom.webp"}},
    }
    person = {"facts": [{
        "tag": "RESI", "label": None, "date": {"display": "1890", "parsed": True},
        "citations": [
            {"source_id": "S1", "page": "Same page text", "notes": [], "link": None, "media": ["M1"]},
            {"source_id": "S1", "page": "Same page text", "notes": [], "link": None, "media": ["M2"]},
        ],
    }]}
    record_groups, _ = render.group_citations(person, sources_by_id, {}, media_by_id, tmp_path, "../../")
    citations = record_groups[0]["citations"]
    assert len(citations) == 1
    assert {m["id"] for m in citations[0]["media_items"]} == {"M1", "M2"}


# --- _dedupe_facts: the same merge, applied to the Timeline itself --------
# (found tree-wide, 7 Oct 2026: Susan Tupholme @I329@ has two complete,
# byte-identical BIRT and DEAT blocks directly under one INDI record -
# genuine duplicate GEDCOM data, confirmed by reading the raw tree.)

def test_dedupe_facts_merges_identical_timeline_lines():
    facts = [
        {"tag": "BIRT", "label": None, "date": {"display": "8 Mar 1895", "parsed": True},
         "place": {"text": "Middlesex Co, London, Ontario"}, "citations": [], "media": []},
        {"tag": "BIRT", "label": None, "date": {"display": "8 Mar 1895", "parsed": True},
         "place": {"text": "Middlesex Co, London, Ontario"}, "citations": [], "media": []},
        {"tag": "DEAT", "label": None, "date": {"display": "20 Mar 1895", "parsed": True},
         "place": {"text": "Middlesex Co, London, Ontario"}, "citations": [], "media": []},
    ]
    deduped = render._dedupe_facts(facts, {})
    assert [f["tag"] for f in deduped] == ["BIRT", "DEAT"]


def test_dedupe_facts_merges_citations_and_media_from_duplicates():
    facts = [
        {"tag": "MARR", "label": None, "date": {"display": "25 Jul 1873", "parsed": True},
         "place": {"text": "Wellington, Ontario, Canada"},
         "citations": [{"source_id": "S1", "page": None, "notes": [], "link": None, "media": []}],
         "media": ["M1"]},
        {"tag": "MARR", "label": None, "date": {"display": "25 Jul 1873", "parsed": True},
         "place": {"text": "Wellington, Ontario, Canada"},
         "citations": [{"source_id": "S2", "page": None, "notes": [], "link": None, "media": []}],
         "media": ["M2"]},
    ]
    deduped = render._dedupe_facts(facts, {})
    assert len(deduped) == 1
    assert {c["source_id"] for c in deduped[0]["citations"]} == {"S1", "S2"}
    assert deduped[0]["media"] == ["M1", "M2"]


def test_dedupe_facts_keeps_distinguishable_facts_separate():
    """Two MARR facts with different spouses render differently (fact_label
    includes the spouse's name), so they must NOT merge even though tag,
    date and place coincide."""
    facts = [
        {"tag": "MARR", "label": None, "date": {"display": "1900", "parsed": True},
         "place": {"text": "Ontario"}, "spouse_id": "I2", "spouse_published": True,
         "citations": [], "media": []},
        {"tag": "MARR", "label": None, "date": {"display": "1900", "parsed": True},
         "place": {"text": "Ontario"}, "spouse_id": "I3", "spouse_published": True,
         "citations": [], "media": []},
    ]
    people_by_id = {"I2": {"name": {"display": "Jane Doe"}}, "I3": {"name": {"display": "Mary Roe"}}}
    deduped = render._dedupe_facts(facts, people_by_id)
    assert len(deduped) == 2


# --- build_gallery: the same Ancestry exclusion, applied to person["media"]
# (Will found this gap on the real site, 7 Oct 2026: the Sources section
# correctly hid the thumbnail, but the same image still showed in the main
# gallery above, since person["media"] is a union that includes citation
# media too - group_citations' suppression never touched that list.)

def test_build_gallery_excludes_media_only_reached_via_ancestry_citation(tmp_path):
    sources_by_id = {"S1": {"id": "S1", "is_ancestry": True}}
    media_by_id = {"M1": {"id": "M1", "type": "document", "thumbnail": "media/ab/m1-thumb.webp",
                           "zoom": {"kind": "image", "path": "media/ab/m1-zoom.webp"}}}
    person = {
        "portrait": None,
        "media": ["M1"],
        "facts": [{"tag": "BIRT", "media": [], "citations": [
            {"source_id": "S1", "media": ["M1"]}]}],
    }
    gallery = render.build_gallery(person, sources_by_id, media_by_id, tmp_path, "../../")
    assert gallery == []


def test_build_gallery_keeps_media_also_reached_a_non_ancestry_way(tmp_path):
    import pyvips
    (tmp_path / "media" / "ab").mkdir(parents=True)
    pyvips.Image.black(10, 10).copy(interpretation="srgb").webpsave(
        str(tmp_path / "media" / "ab" / "m1-zoom.webp"))
    sources_by_id = {"S1": {"id": "S1", "is_ancestry": True},
                      "S2": {"id": "S2", "is_ancestry": False}}
    media_by_id = {"M1": {"id": "M1", "type": "photo", "thumbnail": "media/ab/m1-thumb.webp",
                           "zoom": {"kind": "image", "path": "media/ab/m1-zoom.webp"}}}
    person = {
        "portrait": None,
        "media": ["M1"],
        "facts": [{"tag": "BIRT", "media": [], "citations": [
            {"source_id": "S1", "media": ["M1"]}, {"source_id": "S2", "media": ["M1"]}]}],
    }
    gallery = render.build_gallery(person, sources_by_id, media_by_id, tmp_path, "../../")
    assert len(gallery) == 1


def test_build_gallery_keeps_ancestry_cited_portrait(tmp_path):
    """Belt and suspenders: group_citations' docstring assumes a portrait is
    never attached via a citation in the first place, but build_gallery
    never hides the portrait slot regardless."""
    import pyvips
    (tmp_path / "media" / "ab").mkdir(parents=True)
    pyvips.Image.black(10, 10).copy(interpretation="srgb").webpsave(
        str(tmp_path / "media" / "ab" / "m1-zoom.webp"))
    sources_by_id = {"S1": {"id": "S1", "is_ancestry": True}}
    media_by_id = {"M1": {"id": "M1", "type": "photo", "thumbnail": "media/ab/m1-thumb.webp",
                           "zoom": {"kind": "image", "path": "media/ab/m1-zoom.webp"}}}
    person = {
        "portrait": "M1",
        "media": ["M1"],
        "facts": [{"tag": "BIRT", "media": [], "citations": [
            {"source_id": "S1", "media": ["M1"]}]}],
    }
    gallery = render.build_gallery(person, sources_by_id, media_by_id, tmp_path, "../../")
    assert len(gallery) == 1


def test_build_gallery_collapses_distinct_ids_with_identical_content(tmp_path):
    """Two OBJE records with byte-identical content (Phase 0: 38 such pairs
    in the real export) share one derivative file on disk already (same
    content hash -> same thumbnail/zoom path), but before this fix each
    surviving media id still got its own gallery entry - found by Will on
    the real site, 7 Oct 2026 (James Wray Sr. @I313@): the same photo shown
    two, three, even four times side by side with nothing to distinguish
    them."""
    import pyvips
    (tmp_path / "media" / "ab").mkdir(parents=True)
    pyvips.Image.black(10, 10).copy(interpretation="srgb").webpsave(
        str(tmp_path / "media" / "ab" / "same-zoom.webp"))
    sources_by_id = {}
    media_by_id = {
        "M1": {"id": "M1", "type": "photo", "caption": "Headstone (scan A)",
               "thumbnail": "media/ab/same-thumb.webp",
               "zoom": {"kind": "image", "path": "media/ab/same-zoom.webp"}},
        "M2": {"id": "M2", "type": "photo", "caption": "Headstone (scan B)",
               "thumbnail": "media/ab/same-thumb.webp",  # identical content, different OBJE id
               "zoom": {"kind": "image", "path": "media/ab/same-zoom.webp"}},
        "M3": {"id": "M3", "type": "photo", "caption": "A different photo",
               "thumbnail": "media/ab/other-thumb.webp",
               "zoom": {"kind": "image", "path": "media/ab/same-zoom.webp"}},
    }
    person = {"portrait": None, "media": ["M1", "M2", "M3"], "facts": []}
    gallery = render.build_gallery(person, sources_by_id, media_by_id, tmp_path, "../../")
    assert [g["id"] for g in gallery] == ["M1", "M3"]


# --- end-to-end: synthetic fixture through extract + render ------------

@pytest.fixture
def built_config(tmp_path):
    """Runs the real extract stage against the synthetic fixture, then
    stubs an empty media.json (no real files behind the fixture's media
    paths, so there's nothing for the media stage to resolve - exactly
    like a real person with no media, already proven to render fine)."""
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
    return config


def test_render_site_end_to_end(built_config):
    summary = render.render_site(built_config)
    assert summary["errors"] == []
    assert summary["written"] == summary["total"] > 0
    assert summary["surnames"] > 0

    assert (built_config.site_dir / "index.html").exists()
    assert (built_config.site_dir / "about" / "index.html").exists()
    assert (built_config.site_dir / "404.html").exists()
    assert (built_config.site_dir / "surnames" / "index.html").exists()
    assert (built_config.site_dir / "pagefind").is_dir()
    assert (built_config.site_dir / "static" / "css" / "site.css").exists()


def test_rendered_person_page_has_no_duplicate_or_unclosed_tags(built_config):
    from html.parser import HTMLParser

    render.render_site(built_config)
    people = json.loads((built_config.data_dir / "people.json").read_text(encoding="utf-8"))
    some_person = people[0]
    html_path = built_config.site_dir / "people" / some_person["slug"] / "index.html"
    content = html_path.read_text(encoding="utf-8")

    void = {"meta", "link", "img", "br", "input", "area", "base", "col",
            "embed", "hr", "source", "track", "wbr"}

    class Checker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.ids, self.dupes = [], set(), []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                if attrs["id"] in self.ids:
                    self.dupes.append(attrs["id"])
                self.ids.add(attrs["id"])
            if tag not in void:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()

    checker = Checker()
    checker.feed(content)
    assert checker.stack == []
    assert checker.dupes == []


# --- _build_media_item: documents normalize to a `pages` list (multi-page
# PDF viewer, Will's ask 6 Oct 2026 - previously only page 1 of a PDF was
# ever reachable in the UI, even though every page was processed) --------

def test_build_media_item_single_page_document_has_one_page_entry():
    media_by_id = {"M1": {"id": "M1", "type": "document", "thumbnail": "media/ab/thumb.webp",
                           "zoom": {"kind": "image", "path": "media/ab/zoom.webp"}}}
    item = render._build_media_item("M1", media_by_id, Path("."), "../../")
    assert item["page_count"] == 1
    assert item["pages"] == [{"thumbnail": "../../media/ab/thumb.webp",
                               "zoom": {"kind": "image", "path": "../../media/ab/zoom.webp"}}]


def test_build_media_item_multi_page_pdf_exposes_every_page():
    media_by_id = {"M1": {
        "id": "M1", "type": "document", "is_pdf": True,
        "pages": [
            {"page": 1, "thumbnail": "media/ab/p1-thumb.webp",
             "zoom": {"kind": "image", "path": "media/ab/p1-zoom.webp"}},
            {"page": 2, "thumbnail": "media/ab/p2-thumb.webp",
             "zoom": {"kind": "dzi", "path": "media/ab/p2-zoom.dzi"}},
        ],
    }}
    item = render._build_media_item("M1", media_by_id, Path("."), "../../")
    assert item["page_count"] == 2
    assert item["thumbnail"] == "media/ab/p1-thumb.webp"  # bare, for the <img> tag's {{ root }} prefix
    assert item["pages"][0]["thumbnail"] == "../../media/ab/p1-thumb.webp"  # root baked in, for JS
    assert item["pages"][1]["zoom"] == {"kind": "dzi", "path": "../../media/ab/p2-zoom.dzi"}


def test_rendered_multi_page_document_has_a_parseable_data_pages_attribute(tmp_path):
    """Regression: Jinja2's `tojson` isn't HTML-attribute-safe inside
    double quotes (its output isn't re-escaped, so the JSON's own quotes
    terminate the attribute early) - confirmed with a throwaway Jinja2
    script before trusting the fix, 6 Oct 2026, rather than assuming.
    person.html must single-quote `data-pages` for this to parse as valid
    JSON at all; this test renders a real page through the real template
    and parses the real output, rather than only checking the Python data
    shape (which the double-quoted bug would NOT have caught)."""
    import pyvips
    from html.parser import HTMLParser

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

    shard = config.site_dir / "media" / "ab"
    shard.mkdir(parents=True)
    for name in ("p1-thumb", "p1-zoom", "p2-thumb", "p2-zoom"):
        pyvips.Image.black(10, 10).copy(interpretation="srgb").webpsave(str(shard / f"{name}.webp"))
    media_json = [{
        "id": "M2", "type": "document", "is_pdf": True, "caption": "Test doc",
        "pages": [
            {"page": 1, "thumbnail": "media/ab/p1-thumb.webp",
             "zoom": {"kind": "image", "path": "media/ab/p1-zoom.webp"}},
            {"page": 2, "thumbnail": "media/ab/p2-thumb.webp",
             "zoom": {"kind": "image", "path": "media/ab/p2-zoom.webp"}},
        ],
    }]
    (config.data_dir / "media.json").write_text(json.dumps(media_json), encoding="utf-8")

    people = json.loads((config.data_dir / "people.json").read_text(encoding="utf-8"))
    i2 = next(p for p in people if p["id"] == "I2")  # @I2@ has a direct OBJE @M1@ attachment
    out_path = render.render_person_page(i2["id"], config)
    html = out_path.read_text(encoding="utf-8")

    found = []

    class Checker(HTMLParser):
        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if "data-pages" in d:
                found.append(json.loads(d["data-pages"]))  # raises if not valid JSON

    Checker().feed(html)
    assert len(found) >= 1
    assert len(found[0]) == 2
    assert found[0][0]["thumbnail"] == "../../media/ab/p1-thumb.webp"


def test_build_media_item_pdf_with_no_pages_is_skipped():
    media_by_id = {"M1": {"id": "M1", "type": "document", "is_pdf": True, "pages": []}}
    assert render._build_media_item("M1", media_by_id, Path("."), "../../") is None


# --- pagefind bundle-path (regression: auto-detection can't cope with
# our root-relative script src, silently falling back to the wrong
# "/pagefind/" default on any subpath deployment - confirmed against
# Pagefind's own docs, 6 Oct 2026) ------------------------------------

def test_site_context_carries_base_path_for_pagefind_config():
    class _FakeConfig:
        site_title = "Test"
        contact_email = ""
        site_base_path = "/gene"
    assert render._site_context(_FakeConfig())["base_path"] == "/gene"


def test_rendered_page_has_correct_absolute_pagefind_bundle_path(tmp_path):
    """Must be an absolute, base_path-prefixed path, not root-relative -
    a non-empty base_path is essential here, since an empty one would
    make a broken ("/pagefind/" always) and a working implementation look
    identical."""
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n  gedcom_file: {str(FIXTURE)!r}\n"
        f"  media_dir: {str(tmp_path / 'media')!r}\n  build_dir: build\n"
        f"privacy:\n  home_person: '@I1@'\n  min_generation: 2\n"
        f"site:\n  title: Test Site\n  domain: example.com\n  base_path: '/gene'\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    extract_run(config)
    (config.data_dir / "media.json").write_text("[]", encoding="utf-8")
    people = json.loads((config.data_dir / "people.json").read_text(encoding="utf-8"))
    out_path = render.render_person_page(people[0]["id"], config)
    html = out_path.read_text(encoding="utf-8")
    assert '<pagefind-config bundle-path="/gene/pagefind/">' in html


def test_hidden_people_never_get_a_page(built_config):
    """Hard Rule 1: no page at all for a hidden person. @I1@ (the home
    person) is always hidden in the synthetic fixture - confirm neither a
    bare-id nor a slugged directory for it exists anywhere under people/."""
    render.render_site(built_config)
    people = json.loads((built_config.data_dir / "people.json").read_text(encoding="utf-8"))
    assert "I1" not in {p["id"] for p in people}

    people_dir = built_config.site_dir / "people"
    slugs = [p.name for p in people_dir.iterdir() if p.is_dir()]
    assert not any(slug == "I1" or slug.endswith("-I1") for slug in slugs)

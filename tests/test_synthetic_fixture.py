"""Integration test over the synthetic GEDCOM fixture (BRIEF.md Testing:
"A synthetic GEDCOM fixture containing: living, deceased and undetermined
people; private facts; media linked to living people; citation-only media;
an adopted child; an empty surname; non-conforming dates; over-long lines;
a CMYK, a 16-bit and a multi-page TIFF").

tests/fixtures/synthetic.ged's cast: home person @I1@, with people
deliberately covering every group (1-4), the generation cutoff on both
sides, an adopted child, an unconnected branch, and the other bullet points
above. (Full vs half vs adopted sibling rules are covered by test_privacy.py's
isolated fixtures instead, where each case can be pinned down precisely
without one family's dates leaking into another's classification - which is
exactly what happened here in an earlier draft: see git history.) The
CMYK/16-bit/multi-page TIFF items are GEDCOM-level placeholders only - actual
binary image fixtures are a Milestone 2 (media stage) task.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pipeline import medialink
from pipeline.config import Config
from pipeline.extract import Extractor, _is_ancestry_publ, _media_metadata, build_person, run
from pipeline.gedcom import Gedcom
from pipeline.privacy import classify, withheld_media
from pipeline.relationships import build as build_relationships

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.ged"
AS_OF = date(2026, 10, 5)

EXPECTED_PUBLISHED = {"@I2@", "@I6@", "@I7@", "@I8@", "@I9@", "@I14@"}
EXPECTED_HIDDEN = {"@I1@", "@I3@", "@I4@", "@I5@", "@I10@", "@I13@", "@I15@"}


def _classify():
    g = Gedcom(FIXTURE)
    priv = classify(g, home_person="@I1@", as_of=AS_OF)
    return g, priv


def test_fixture_parses_and_covers_every_person():
    g, priv = _classify()
    assert len(g.of_type("INDI")) == len(EXPECTED_PUBLISHED) + len(EXPECTED_HIDDEN)
    assert set(priv.people) == EXPECTED_PUBLISHED | EXPECTED_HIDDEN


def test_published_set_matches_expectations_for_every_rule_path():
    _, priv = _classify()
    assert priv.published_xrefs == EXPECTED_PUBLISHED
    assert priv.hidden_xrefs == EXPECTED_HIDDEN


def test_home_person_always_hidden():
    _, priv = _classify()
    assert priv.people["@I1@"].publish_reason == "home person (always hidden)"


def test_undetermined_parent_generation_is_hidden_like_the_briefs_own_example():
    """BRIEF.md: "A parent with no dates at all is therefore hidden" - @I3@ is the
    same shape: undetermined, generation 1, below min_generation."""
    _, priv = _classify()
    p = priv.people["@I3@"]
    assert p.group == 4
    assert p.generation == 1
    assert p.published is False


def test_undetermined_grandparent_generation_is_published():
    _, priv = _classify()
    p = priv.people["@I6@"]
    assert p.group == 4
    assert p.generation == 2
    assert p.published is True


def test_private_fact_marker_hides_regardless_of_death_record():
    _, priv = _classify()
    p = priv.people["@I15@"]
    assert p.group == 1
    assert p.published is False
    assert p.publish_reason == "marked private in FTM"


def test_adopted_child_is_classified_like_anyone_else():
    _, priv = _classify()
    p = priv.people["@I13@"]
    assert p.generation == -1
    assert p.group == 4
    assert p.published is False


def test_unconnected_people_split_on_group_as_the_brief_requires():
    _, priv = _classify()
    assert priv.people["@I8@"].generation is None
    assert priv.people["@I8@"].published is True   # group 2
    assert priv.people["@I10@"].generation is None
    assert priv.people["@I10@"].published is False  # group 4


def test_empty_surname_person_is_still_published_via_a_death_record_tag():
    """@I14@'s DEAT date ('pre 1870') doesn't parse, but BRIEF.md's own
    death-record rule counts the tag's presence "with or without a date" -
    and it's reported as unparseable regardless."""
    g, priv = _classify()
    p = priv.people["@I14@"]
    assert p.published is True
    assert ("@I14@", "DEAT", "pre 1870") in priv.unparseable_dates
    name_node = g.by_xref["@I14@"].first("NAME")
    assert name_node.value == "Ellen //"


def test_media_linked_to_a_living_person_is_withheld_even_though_also_published():
    g, priv = _classify()
    withheld = withheld_media(g, priv.hidden_xrefs)
    assert "@M1@" in withheld  # linked to @I2@ (published) AND @I5@ (hidden)
    assert withheld["@M1@"] == {"@I5@"}


def test_citation_only_media_is_not_withheld_and_not_misclassified():
    g, priv = _classify()
    withheld = withheld_media(g, priv.hidden_xrefs)
    assert "@M2@" not in withheld


def test_over_long_note_line_is_preserved_in_full():
    g, _ = _classify()
    note = g.by_xref["@I2@"].first("NOTE")
    assert len(note.value) > 255
    assert note.value.startswith("This note is deliberately long")


def test_html_source_note_is_sanitized_through_the_full_extractor():
    g, priv = _classify()
    r = build_relationships(g)
    ex = Extractor(g, r, priv.published_xrefs, set(), set())
    person = build_person(ex, "@I2@", priv.people["@I2@"], show_living_placeholder=True)

    birt = next(f for f in person["facts"] if f["tag"] == "BIRT")
    cite = birt["citations"][0]
    assert cite["source_id"] == "S2"
    assert "M2" in cite["media"]

    # the source note itself is sanitized in _source_metadata, not on the
    # citation - fetch it via the extractor's own helper for a direct check
    from pipeline.extract import _source_metadata
    meta = _source_metadata(g, "@S2@")
    assert "<script>" not in meta["note"]
    assert "alert(1)" not in meta["note"]              # script content dropped entirely
    assert "<div>" not in meta["note"]
    assert "disallowed wrapper" in meta["note"]          # but its text survives
    assert '<a href="https://example.com">' in meta["note"]
    assert "<i>allowed</i>" in meta["note"]


def test_is_ancestry_flag_keys_on_publ_not_title_or_author():
    """Will's call, 6 Oct 2026: citation *images* are suppressed for
    Ancestry-published sources (BRIEF.md originally said the opposite - "no
    Ancestry exclusion flag" - reversed over concern about republishing
    Ancestry's own scanned records). PUBL is the signal, not TITL/AUTH -
    @S1@ in the fixture is titled "Ancestry Family Trees" with AUTH
    "Ancestry.com" but has no PUBL at all, matching the real export's
    "Other researchers' trees" sources, which is a separate, pre-existing
    concept (is_other_researchers_tree) from this one."""
    from pipeline.extract import _source_metadata
    g = Gedcom(FIXTURE)
    s1 = _source_metadata(g, "@S1@")
    assert s1["is_other_researchers_tree"] is True
    assert s1["is_ancestry"] is False  # no PUBL tag on this fixture source

    g2 = Gedcom.from_text(
        "0 HEAD\n1 CHAR UTF-8\n"
        "0 @S9@ SOUR\n1 TITL 1881 England Census\n"
        "1 PUBL Name: Ancestry.com Operations Inc; Location: Provo, UT\n"
        "0 TRLR\n"
    )
    s9 = _source_metadata(g2, "@S9@")
    assert s9["is_ancestry"] is True
    assert s9["is_other_researchers_tree"] is False


def test_media_metadata_is_ancestry_only_flag():
    """Will, 7 Oct 2026: "if we're not going to show the ancestry sources
    perhaps they shouldn't be in the upload." A media item is ancestry-only
    (media.py should skip generating derivatives for it at all) only when
    EVERY path that reaches it is an Ancestry-published citation - never
    directly attached, never a portrait, and never cited by a non-Ancestry
    source too."""
    g = Gedcom.from_text(
        "0 HEAD\n1 CHAR UTF-8\n"
        "0 @I1@ INDI\n1 NAME Test /Person/\n1 SEX M\n"
        "1 _PHOTO @O4@\n"
        "1 OBJE @O3@\n"
        "1 BIRT\n2 DATE 1 JAN 1900\n"
        "2 SOUR @S1@\n3 OBJE @O1@\n"
        "2 SOUR @S1@\n3 OBJE @O2@\n"
        "2 SOUR @S2@\n3 OBJE @O2@\n"
        "2 SOUR @S1@\n3 OBJE @O3@\n"
        "2 SOUR @S1@\n3 OBJE @O4@\n"
        "0 @S1@ SOUR\n1 TITL Ancestry Source\n"
        "1 PUBL Name: Ancestry.com Operations Inc\n"
        "0 @S2@ SOUR\n1 TITL Some Archive\n"
        "0 @O1@ OBJE\n1 FILE o1.jpg\n"
        "0 @O2@ OBJE\n1 FILE o2.jpg\n"
        "0 @O3@ OBJE\n1 FILE o3.jpg\n"
        "0 @O4@ OBJE\n1 FILE o4.jpg\n"
        "0 TRLR\n"
    )
    links = medialink.build(g)
    ancestry_sources = {s.xref: _is_ancestry_publ(s) for s in g.of_type("SOUR")}
    published = {"@I1@"}

    def flag(obje_xref):
        return _media_metadata(g, obje_xref, links.get(obje_xref), published,
                                ancestry_sources)["is_ancestry_only"]

    assert flag("@O1@") is True   # only ever cited under the Ancestry source
    assert flag("@O2@") is False  # also cited under a non-Ancestry source
    assert flag("@O3@") is False  # also attached directly to the person
    assert flag("@O4@") is False  # the portrait


def test_person_with_a_death_record_gets_their_portrait_and_direct_media():
    g, priv = _classify()
    r = build_relationships(g)
    ex = Extractor(g, r, priv.published_xrefs, set(), set())
    person = build_person(ex, "@I2@", priv.people["@I2@"], show_living_placeholder=True)
    assert person["portrait"] == "M1"
    assert "M1" in person["media"]
    assert "M2" in person["media"]  # pulled in via the BIRT citation


def test_hidden_person_appears_only_as_a_living_placeholder_with_surname():
    """BRIEF.md Hard Rule 1: a hidden relative shows only as 'Living
    [Surname]', with no link and no other data of their own."""
    g, priv = _classify()
    r = build_relationships(g)
    ex = Extractor(g, r, priv.published_xrefs, set(), set())
    person = build_person(ex, "@I1@", priv.people["@I1@"], show_living_placeholder=True)
    spouse_refs = person["spouses"]
    assert len(spouse_refs) == 1
    assert spouse_refs[0] == {"hidden": True, "surname": "Spouseton"}


def test_full_pipeline_run_end_to_end(tmp_path):
    """Runs extract.run() for real, against the fixture, writing to a temp
    build dir - the closest thing to BRIEF.md's "one command that runs the
    full pipeline end to end", scoped to the synthetic fixture rather than
    the real (gitignored) export."""
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"paths:\n"
        f"  gedcom_file: {str(FIXTURE)!r}\n"
        f"  media_dir: {str(tmp_path / 'media')!r}\n"
        f"  build_dir: build\n"
        f"  overrides_dir: overrides\n"
        f"privacy:\n"
        f"  home_person: '@I1@'\n"
        f"  min_generation: 2\n",
        encoding="utf-8",
    )
    config = Config.load(config_yaml, base_dir=tmp_path)
    summary = run(config, as_of=AS_OF)

    assert summary["people_total"] == len(EXPECTED_PUBLISHED) + len(EXPECTED_HIDDEN)
    assert summary["people_published"] == len(EXPECTED_PUBLISHED)
    assert summary["people_hidden"] == len(EXPECTED_HIDDEN)

    import json
    people = json.loads((config.data_dir / "people.json").read_text(encoding="utf-8"))
    assert {p["id"] for p in people} == {x.strip("@") for x in EXPECTED_PUBLISHED}
    # Hard Rule 1: a hidden person's own data never appears, with the one
    # explicit exception (a hidden relative's surname in a "Living
    # [Surname]" placeholder - already covered by its own test above).
    blob = json.dumps(people)
    assert '"id": "I15"' not in blob   # the private-override person
    assert '"id": "I5"' not in blob    # the hidden baby
    assert "Susan" not in blob         # I4's given name - never, even though
                                       # I4's surname legitimately appears as
                                       # a "Living Spouseton" placeholder

    assert (config.reports_dir / "privacy.md").exists()
    report = (config.reports_dir / "privacy.md").read_text(encoding="utf-8")
    assert "Everyone hidden" in report
    assert "centenarian" in report.lower()

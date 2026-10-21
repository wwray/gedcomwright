"""Tests for pipeline.lint (BRIEF.md "Tree lint" checklist)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pipeline.gedcom import Gedcom
from pipeline.privacy import classify
from pipeline.relationships import build as build_relationships
from pipeline import lint

AS_OF = date(2026, 10, 5)
HEAD = "0 HEAD\n1 CHAR UTF-8\n1 GEDC\n2 VERS 5.5.1\n"
TRLR = "0 TRLR\n"


def indi(xref, name="Test /Person/", sex="M", events=(), famc=None, fams=(), extra=()):
    lines = [f"0 {xref} INDI", f"1 NAME {name}", f"1 SEX {sex}"]
    for tag, date_str, cites in events:
        lines.append(f"1 {tag} Y")
        if date_str is not None:
            lines.append(f"2 DATE {date_str}")
        for sx in cites:
            lines.append(f"2 SOUR {sx}")
    if famc:
        lines.append(f"1 FAMC {famc}")
    for f in fams:
        lines.append(f"1 FAMS {f}")
    for tag, val in extra:
        lines.append(f"1 {tag} {val}")
    return "\n".join(lines) + "\n"


def fam(xref, husb=None, wife=None, children=(), marr_date=None):
    lines = [f"0 {xref} FAM"]
    if husb:
        lines.append(f"1 HUSB {husb}")
    if wife:
        lines.append(f"1 WIFE {wife}")
    for c, frel, mrel in children:
        lines.append(f"1 CHIL {c}")
        if frel:
            lines.append(f"2 _FREL {frel}")
        if mrel:
            lines.append(f"2 _MREL {mrel}")
    if marr_date:
        lines.append("1 MARR Y")
        lines.append(f"2 DATE {marr_date}")
    return "\n".join(lines) + "\n"


def sour(xref, title):
    return f"0 {xref} SOUR\n1 TITL {title}\n"


def objef(xref, filepath, titl=None, text=None):
    lines = [f"0 {xref} OBJE", f"1 FILE {filepath}", "2 FORM jpg"]
    if titl:
        lines.append(f"2 TITL {titl}")
    if text:
        lines.append(f"2 _TEXT {text}")
    return "\n".join(lines) + "\n"


def build(*parts: str) -> Gedcom:
    return Gedcom.from_text(HEAD + "".join(parts) + TRLR)


def setup(g: Gedcom, home="@I1@", min_generation=2):
    r = build_relationships(g)
    priv = classify(g, home_person=home, min_generation=min_generation, as_of=AS_OF)
    return r, priv


# --- death fact checks ---------------------------------------------------

def test_uncited_death_fact_in_recent_generation_flagged():
    g = build(indi("@I1@", events=[("DEAT", "1990", [])]))
    r, priv = setup(g)
    section = lint.check_uncited_recent_death_facts(r, priv, 2)
    assert section.count == 1
    assert "no citation" in section.items[0][1]


def test_death_fact_cited_only_to_ancestry_family_trees_flagged_tree_wide():
    g = build(
        sour("@S1@", "Ancestry Family Trees"),
        indi("@I1@", events=[("DEAT", "1990", ["@S1@"])]),
    )
    r, priv = setup(g)
    # Not flagged by the recent-generations/no-citation check (it HAS a citation)...
    assert lint.check_uncited_recent_death_facts(r, priv, 2).count == 0
    # ...but is flagged by the dedicated, tree-wide Ancestry-only check.
    section = lint.check_ancestry_only_death_citations(g, r)
    assert section.count == 1
    assert "Ancestry Family Trees" in section.items[0][1]


def test_ancestry_only_check_is_tree_wide_not_generation_restricted():
    # @I2@ is @I1@'s grandparent (generation 2) - outside min_generation,
    # but the Ancestry-only check still catches it (it's tree-wide).
    g = build(
        sour("@S1@", "Ancestry Family Trees"),
        indi("@I1@", famc="@F1@"),
        indi("@I3@", fams=["@F1@"], famc="@F2@"),
        indi("@I2@", fams=["@F2@"], events=[("DEAT", "1900", ["@S1@"])]),
        fam("@F1@", husb="@I3@", children=[("@I1@", "Natural", "Natural")]),
        fam("@F2@", husb="@I2@", children=[("@I3@", "Natural", "Natural")]),
    )
    section = lint.check_ancestry_only_death_citations(g, build_relationships(g))
    assert section.count == 1


def test_death_fact_with_a_real_citation_is_not_flagged():
    g = build(
        sour("@S2@", "A Real Parish Register"),
        indi("@I1@", events=[("DEAT", "1990", ["@S2@"])]),
    )
    r, priv = setup(g)
    assert lint.check_uncited_recent_death_facts(r, priv, 2).count == 0
    assert lint.check_ancestry_only_death_citations(g, r).count == 0


def test_death_fact_outside_recent_generations_is_not_flagged():
    # @I2@ is @I1@'s grandparent (generation 2) - above min_generation.
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I3@", fams=["@F1@"], famc="@F2@"),
        indi("@I2@", fams=["@F2@"], events=[("DEAT", "1900", [])]),
        fam("@F1@", husb="@I3@", children=[("@I1@", "Natural", "Natural")]),
        fam("@F2@", husb="@I2@", children=[("@I3@", "Natural", "Natural")]),
    )
    r, priv = setup(g)
    section = lint.check_uncited_recent_death_facts(r, priv, 2)
    assert section.count == 0


def test_undated_death_fact_flagged():
    g = build(indi("@I1@", events=[("DEAT", None, [])]))
    r, _ = setup(g)
    section = lint.check_undated_death_facts(r)
    assert section.count == 1


def test_dated_death_fact_not_flagged_as_undated():
    g = build(indi("@I1@", events=[("DEAT", "1990", [])]))
    r, _ = setup(g)
    section = lint.check_undated_death_facts(r)
    assert section.count == 0


# --- names -----------------------------------------------------------------

def test_empty_surname_flagged():
    g = build(indi("@I1@", name="Ellen //"))
    r, _ = setup(g)
    assert lint.check_placeholder_names(r).count == 1


def test_placeholder_surname_flagged():
    g = build(indi("@I1@", name="/None/"))
    r, _ = setup(g)
    section = lint.check_placeholder_names(r)
    assert section.count == 1
    assert "placeholder surname" in section.items[0][1]


def test_surname_only_record_flagged():
    g = build(indi("@I1@", name="/Patterson/"))
    r, _ = setup(g)
    section = lint.check_placeholder_names(r)
    assert section.count == 1
    assert "surname-only" in section.items[0][1]


def test_ordinary_name_not_flagged_as_placeholder():
    g = build(indi("@I1@", name="James /Wray/"))
    r, _ = setup(g)
    assert lint.check_placeholder_names(r).count == 0


def test_no_slash_name_is_a_nickname_issue_not_a_placeholder_surname_issue():
    g = build(indi("@I1@", name="Emerson, Emer"))
    r, _ = setup(g)
    assert lint.check_placeholder_names(r).count == 0
    section = lint.check_inconsistent_names(r)
    assert section.count == 1
    assert "no slashes" in section.items[0][1] or "Given, Nick" in section.items[0][1]


def test_quoted_nickname_flagged():
    g = build(indi("@I1@", name='Jennett "Jean" Althea /Knapp/'))
    r, _ = setup(g)
    section = lint.check_inconsistent_names(r)
    assert section.count == 1
    assert "quotes" in section.items[0][1]


def test_rev_suffix_flagged_distinctly_from_generic_parens():
    g = build(indi("@I1@", name="John Latta /Picken/ (Rev)"))
    r, _ = setup(g)
    section = lint.check_inconsistent_names(r)
    assert section.count == 1
    assert "Rev" in section.items[0][1]


def test_stray_plus_flagged():
    g = build(indi("@I1@", name='Thomas Christian "Gapper" /Lawson/ +'))
    r, _ = setup(g)
    section = lint.check_inconsistent_names(r)
    assert section.count == 1
    assert "stray '+'" in section.items[0][1]


def test_clean_name_is_not_flagged_at_all():
    g = build(indi("@I1@", name="James /Wray/"))
    r, _ = setup(g)
    assert lint.check_inconsistent_names(r).count == 0


# --- likely duplicates -----------------------------------------------------

def test_nickname_equivalent_siblings_flagged_as_likely_duplicate():
    g = build(
        indi("@I1@", name="Robert /Keillar/", famc="@F1@"),
        indi("@I2@", name="Bob /Keillar/", famc="@F1@"),
        fam("@F1@", husb="@I9@", wife="@I9W@",
            children=[("@I1@", "Natural", "Natural"), ("@I2@", "Natural", "Natural")]),
    )
    r, _ = setup(g)
    section = lint.check_likely_duplicates(r)
    assert section.count == 1


def test_different_surnames_are_not_flagged_as_duplicates():
    g = build(
        indi("@I1@", name="Robert /Keillar/", famc="@F1@"),
        indi("@I2@", name="Robert /Wray/", famc="@F1@"),
        fam("@F1@", husb="@I9@", wife="@I9W@",
            children=[("@I1@", "Natural", "Natural"), ("@I2@", "Natural", "Natural")]),
    )
    r, _ = setup(g)
    assert lint.check_likely_duplicates(r).count == 0


def test_unrelated_given_names_are_not_flagged_as_duplicates():
    g = build(
        indi("@I1@", name="Robert /Keillar/", famc="@F1@"),
        indi("@I2@", name="William /Keillar/", famc="@F1@"),
        fam("@F1@", husb="@I9@", wife="@I9W@",
            children=[("@I1@", "Natural", "Natural"), ("@I2@", "Natural", "Natural")]),
    )
    r, _ = setup(g)
    assert lint.check_likely_duplicates(r).count == 0


# --- generation / connectivity ---------------------------------------------

def test_generation_conflict_is_surfaced():
    # Same conflict shape as test_privacy.py's conflicting-path test: I_S (a
    # full sibling of home, "true" generation 0) marries I_Y (home's actual
    # grandchild, generation -2). The spousal edge is bidirectional, so BOTH
    # of them end up with conflicting seen values - and "take the younger"
    # pulls both down to -2, not just I_Y.
    g = build(
        indi("@I1@", famc="@F_P@", fams=["@F_C@"]),
        indi("@I_S@", famc="@F_P@", fams=["@F_SY@"]),
        indi("@I_P@", fams=["@F_P@"]),
        indi("@I_C@", famc="@F_C@", fams=["@F_CY@"]),
        indi("@I_Y@", famc="@F_CY@", fams=["@F_SY@"]),
        fam("@F_P@", husb="@I_P@", children=[
            ("@I1@", "Natural", "Natural"), ("@I_S@", "Natural", "Natural")]),
        fam("@F_C@", husb="@I1@", children=[("@I_C@", "Natural", "Natural")]),
        fam("@F_CY@", husb="@I_C@", children=[("@I_Y@", "Natural", "Natural")]),
        fam("@F_SY@", husb="@I_S@", wife="@I_Y@"),
    )
    _, priv = setup(g)
    section = lint.check_generation_conflicts(priv)
    assert {ident for ident, _ in section.items} == {"@I_S@", "@I_Y@"}
    assert priv.people["@I_Y@"].generation == -2
    assert priv.people["@I_S@"].generation == -2


def test_unconnected_person_is_surfaced():
    g = build(indi("@I1@"), indi("@I2@"))
    _, priv = setup(g)
    section = lint.check_unconnected_people(priv)
    assert section.count == 1
    assert section.items[0][0] == "@I2@"


# --- impossible ordering ----------------------------------------------------

def test_death_before_birth_flagged():
    g = build(indi("@I1@", events=[("BIRT", "1900", []), ("DEAT", "1890", [])]))
    r, _ = setup(g)
    section = lint.check_impossible_ordering(
        r, min_parent_age=12, max_mother_age=55, min_marriage_age=14)
    assert section.count == 1
    assert "death" in section.items[0][1]


def test_child_born_before_parent_was_12_flagged():
    g = build(
        indi("@I1@", events=[("BIRT", "1900", [])], fams=["@F1@"]),
        indi("@I2@", famc="@F1@", events=[("BIRT", "1905", [])]),  # parent only 5
        fam("@F1@", husb="@I1@", children=[("@I2@", "Natural", "Natural")]),
    )
    r, _ = setup(g)
    section = lint.check_impossible_ordering(
        r, min_parent_age=12, max_mother_age=55, min_marriage_age=14)
    assert section.count == 1
    assert "father" in section.items[0][1]


def test_child_born_after_mother_was_55_flagged():
    g = build(
        indi("@I1@", events=[("BIRT", "1840", [])], fams=["@F1@"]),
        indi("@I2@", famc="@F1@", events=[("BIRT", "1900", [])]),  # mother would be 60
        fam("@F1@", wife="@I1@", children=[("@I2@", "Natural", "Natural")]),
    )
    r, _ = setup(g)
    section = lint.check_impossible_ordering(
        r, min_parent_age=12, max_mother_age=55, min_marriage_age=14)
    assert section.count == 1
    assert "mother" in section.items[0][1]


def test_plausible_family_ages_are_not_flagged():
    g = build(
        indi("@I1@", events=[("BIRT", "1870", [])], fams=["@F1@"]),
        indi("@I3@", events=[("BIRT", "1872", [])], fams=["@F1@"]),
        indi("@I2@", famc="@F1@", events=[("BIRT", "1895", [])]),
        fam("@F1@", husb="@I1@", wife="@I3@", children=[("@I2@", "Natural", "Natural")],
            marr_date="1890"),
    )
    r, _ = setup(g)
    section = lint.check_impossible_ordering(
        r, min_parent_age=12, max_mother_age=55, min_marriage_age=14)
    assert section.count == 0


def test_marriage_before_14_flagged():
    g = build(
        indi("@I1@", events=[("BIRT", "1900", [])], fams=["@F1@"]),
        indi("@I2@", events=[("BIRT", "1895", [])], fams=["@F1@"]),
        fam("@F1@", husb="@I1@", wife="@I2@", marr_date="1912"),  # I1 is 12
    )
    r, _ = setup(g)
    section = lint.check_impossible_ordering(
        r, min_parent_age=12, max_mother_age=55, min_marriage_age=14)
    assert any("married by age" in desc for _, desc in section.items)


# --- obituary attachment ----------------------------------------------------

def test_obituary_not_mentioning_the_attached_person_is_flagged():
    g = Gedcom.from_text(
        HEAD +
        "0 @I1@ INDI\n1 NAME Ann /Small/\n1 OBJE @M1@\n" +
        objef("@M1@", "x.jpg", titl="Obituary", text="Mrs Anne Gowdy, widow of Joseph Gowdy") +
        TRLR
    )
    section = lint.check_obituary_attachment(g)
    assert section.count == 1
    assert section.items[0][0] == "@I1@"


def test_obituary_mentioning_the_attached_person_is_not_flagged():
    g = Gedcom.from_text(
        HEAD +
        "0 @I1@ INDI\n1 NAME James /Wray/\n1 OBJE @M1@\n" +
        objef("@M1@", "x.jpg", titl="James Wray Obituary") +
        TRLR
    )
    section = lint.check_obituary_attachment(g)
    assert section.count == 0


def test_non_obituary_media_is_never_flagged():
    g = Gedcom.from_text(
        HEAD +
        "0 @I1@ INDI\n1 NAME Ann /Small/\n1 OBJE @M1@\n" +
        objef("@M1@", "x.jpg", titl="Family photo") +
        TRLR
    )
    assert lint.check_obituary_attachment(g).count == 0


# --- unreferenced media on disk ---------------------------------------------

def test_unreferenced_file_is_flagged(tmp_path: Path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    (media_dir / "ref.jpg").write_bytes(b"referenced content")
    (media_dir / "orphan.jpg").write_bytes(b"something else entirely")

    g = Gedcom.from_text(
        HEAD + objef("@M1@", str(media_dir / "ref.jpg")) + TRLR
    )
    section = lint.check_unreferenced_media(g, media_dir)
    assert section.count == 1
    assert "orphan.jpg" in section.items[0][0]


def test_byte_identical_copy_of_a_referenced_file_is_not_flagged(tmp_path: Path):
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    (media_dir / "ref.jpg").write_bytes(b"identical content")
    (media_dir / "ref(1).jpg").write_bytes(b"identical content")  # FTM's duplicate-name pattern

    g = Gedcom.from_text(
        HEAD + objef("@M1@", str(media_dir / "ref.jpg")) + TRLR
    )
    section = lint.check_unreferenced_media(g, media_dir)
    assert section.count == 0


def test_missing_media_dir_does_not_crash(tmp_path: Path):
    g = Gedcom.from_text(HEAD + TRLR)
    section = lint.check_unreferenced_media(g, tmp_path / "does-not-exist")
    assert section.count == 0
    assert "skipped" in section.title


# --- report writing ----------------------------------------------------

def test_write_report_includes_every_section_and_a_total(tmp_path: Path):
    result = lint.LintResult([
        lint.LintSection("Section A", [("@I1@", "reason one")]),
        lint.LintSection("Section B", [], note="all clear"),
    ])
    out = tmp_path / "lint.md"
    lint.write_report(result, out)
    text = out.read_text(encoding="utf-8")
    assert "Section A (1)" in text
    assert "Section B (0)" in text
    assert "all clear" in text
    assert "**1 items across 2 checks.**" in text

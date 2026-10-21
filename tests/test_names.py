"""Tests for pipeline.extract.choose_primary_name (GEDCOM standard
NAME.TYPE handling - married name as primary, maiden name bracketed).
Will's choice, 6 Oct 2026: forward-looking, since the real export's
NAME.TYPE is only ever "aka" today (confirmed by inspecting the whole
file) - this changes nothing until FTM records a "married"-typed name."""
from __future__ import annotations

from pipeline.extract import choose_primary_name
from pipeline.gedcom import Gedcom


def _name_nodes(ged_snippet: str) -> list:
    g = Gedcom.from_text(
        "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n" + ged_snippet + "0 TRLR\n"
    )
    return g.by_xref["@I1@"].all("NAME")


def test_no_married_type_falls_back_to_first_name_as_before():
    nodes = _name_nodes("1 NAME Jane /Testwright/\n1 NAME Janie /Testwright/\n2 TYPE aka\n")
    primary, aka = choose_primary_name(nodes, "F")
    assert primary["display"] == "Jane Testwright"
    assert [a["display"] for a in aka] == ["Janie Testwright"]


def test_married_type_becomes_primary_with_maiden_bracketed():
    nodes = _name_nodes(
        "1 NAME Peg /Testwright/\n"
        "1 NAME Peg /Spouseton/\n2 TYPE married\n"
    )
    primary, aka = choose_primary_name(nodes, "F")
    assert primary["display"] == "Peg Spouseton (née Testwright)"
    assert primary["surname"] == "Spouseton"
    assert [a["display"] for a in aka] == ["Peg Testwright"]


def test_married_type_prefers_explicit_birth_type_over_first_listed():
    nodes = _name_nodes(
        "1 NAME Peg /Spouseton/\n2 TYPE married\n"
        "1 NAME Peg /Something Else/\n2 TYPE aka\n"
        "1 NAME Peg /Testwright/\n2 TYPE birth\n"
    )
    primary, aka = choose_primary_name(nodes, "F")
    assert primary["display"] == "Peg Spouseton (née Testwright)"
    assert len(aka) == 2


def test_married_type_with_no_other_differing_surname_shows_no_bracket():
    nodes = _name_nodes("1 NAME Peg /Spouseton/\n2 TYPE married\n")
    primary, aka = choose_primary_name(nodes, "F")
    assert primary["display"] == "Peg Spouseton"
    assert aka == []


def test_married_type_uses_born_not_nee_for_non_female():
    nodes = _name_nodes(
        "1 NAME John /Smith/\n1 NAME John /Jones/\n2 TYPE married\n"
    )
    primary, _ = choose_primary_name(nodes, "M")
    assert primary["display"] == "John Jones (born Smith)"


def test_no_name_records_at_all():
    g = Gedcom.from_text("0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n0 TRLR\n")
    primary, aka = choose_primary_name(g.by_xref["@I1@"].all("NAME"), "F")
    assert primary["display"] == "Unknown"
    assert aka == []

"""Tests for pipeline.gedcom: CONC/CONT, cross-references, over-long lines,
header encoding (BRIEF.md Stage 1: "Handle CONC/CONT, the encoding from the
header, cross-references, and over-long lines")."""
from __future__ import annotations

import pytest

from pipeline.gedcom import Gedcom, UnsupportedEncoding, newest_ged

MINIMAL_HEAD = "0 HEAD\n1 CHAR UTF-8\n1 GEDC\n2 VERS 5.5.1\n"
TRLR = "0 TRLR\n"


def test_cross_references_and_pointers():
    g = Gedcom.from_text(
        MINIMAL_HEAD +
        "0 @I1@ INDI\n1 NAME Jane /Doe/\n1 FAMS @F1@\n"
        "0 @F1@ FAM\n1 HUSB @I1@\n1 WIFE @I2@\n" + TRLR
    )
    assert "@I1@" in g.by_xref
    indi = g.by_xref["@I1@"]
    assert indi.val("NAME") == "Jane /Doe/"
    fam = g.by_xref["@F1@"]
    assert fam.val("HUSB") == "@I1@"


def test_conc_joins_with_no_separator():
    g = Gedcom.from_text(
        MINIMAL_HEAD +
        "0 @I1@ INDI\n1 NOTE This is a long\n2 CONC  note that continues.\n" + TRLR
    )
    note = g.by_xref["@I1@"].first("NOTE")
    assert note.value == "This is a long note that continues."


def test_cont_joins_with_a_newline():
    g = Gedcom.from_text(
        MINIMAL_HEAD +
        "0 @I1@ INDI\n1 NOTE First line\n2 CONT Second line\n" + TRLR
    )
    note = g.by_xref["@I1@"].first("NOTE")
    assert note.value == "First line\nSecond line"


def test_conc_and_cont_together():
    g = Gedcom.from_text(
        MINIMAL_HEAD +
        "0 @I1@ INDI\n1 NOTE Para one line one,\n"
        "2 CONC  continued.\n2 CONT Para two.\n" + TRLR
    )
    note = g.by_xref["@I1@"].first("NOTE")
    assert note.value == "Para one line one, continued.\nPara two."


def test_over_long_lines_are_tracked_but_not_enforced():
    long_value = "x" * 300
    g = Gedcom.from_text(
        MINIMAL_HEAD + f"0 @I1@ INDI\n1 NOTE {long_value}\n" + TRLR
    )
    assert len(g.long_lines) == 1
    note = g.by_xref["@I1@"].first("NOTE")
    assert note.value == long_value  # not truncated


def test_unparseable_line_is_recorded_not_fatal():
    g = Gedcom.from_text(
        MINIMAL_HEAD + "0 @I1@ INDI\nthis is not a gedcom line\n1 SEX M\n" + TRLR
    )
    assert len(g.bad_lines) == 1
    # parsing continues after the bad line
    assert g.by_xref["@I1@"].val("SEX") == "M"


def test_utf8_bom_is_detected_and_stripped():
    text = MINIMAL_HEAD + "0 @I1@ INDI\n1 NAME Café /Été/\n" + TRLR
    raw = b"\xef\xbb\xbf" + text.encode("utf-8")
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample.ged"
        p.write_bytes(raw)
        g = Gedcom(p)
    assert g.has_bom is True
    assert g.by_xref["@I1@"].val("NAME") == "Café /Été/"


def test_non_utf8_charset_is_rejected_not_mis_decoded():
    text = "0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n" + TRLR
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample.ged"
        p.write_bytes(text.encode("ascii"))
        with pytest.raises(UnsupportedEncoding):
            Gedcom(p)


def test_newest_ged_picks_the_most_recently_modified_file(tmp_path):
    import os
    import time

    old = tmp_path / "old_2026-01-01.ged"
    new = tmp_path / "new_2026-06-01.ged"
    old.write_text("0 HEAD\n")
    time.sleep(0.01)
    new.write_text("0 HEAD\n")
    assert newest_ged(tmp_path) == new


def test_newest_ged_raises_when_none_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        newest_ged(tmp_path)

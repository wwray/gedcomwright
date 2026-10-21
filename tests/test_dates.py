"""Tests for pipeline.dates (BRIEF.md Testing: "date parsing")."""
from __future__ import annotations

from datetime import date

import pytest

from pipeline.dates import parse_date


def test_empty_is_unparsed_not_an_error():
    gd = parse_date("")
    assert gd.parsed is False
    assert gd.earliest is None and gd.latest is None
    gd = parse_date(None)
    assert gd.parsed is False


@pytest.mark.parametrize("raw,earliest,latest,display", [
    ("1838", date(1838, 1, 1), date(1838, 12, 31), "1838"),
    ("JUL 1838", date(1838, 7, 1), date(1838, 7, 31), "Jul 1838"),
    ("4 JUL 1838", date(1838, 7, 4), date(1838, 7, 4), "4 Jul 1838"),
    ("FEB 1900", date(1900, 2, 1), date(1900, 2, 28), "Feb 1900"),  # not a leap year
    ("FEB 1904", date(1904, 2, 1), date(1904, 2, 29), "Feb 1904"),  # leap year
])
def test_exact_dates(raw, earliest, latest, display):
    gd = parse_date(raw)
    assert gd.parsed is True
    assert gd.qualifier is None
    assert gd.earliest == earliest
    assert gd.latest == latest
    assert gd.display == display


def test_about_est_cal_use_both_bounds():
    for qualifier, word in (("ABT", "about"), ("EST", "estimated"), ("CAL", "calculated")):
        gd = parse_date(f"{qualifier} 1870")
        assert gd.parsed is True
        assert gd.qualifier == qualifier
        assert gd.earliest == date(1870, 1, 1)
        assert gd.latest == date(1870, 12, 31)
        assert gd.display == f"{word} 1870"


def test_before_is_open_at_the_earliest_end():
    gd = parse_date("BEF 1950")
    assert gd.parsed is True
    assert gd.earliest is None          # "likely living" test must not count this
    assert gd.latest == date(1950, 12, 31)   # "deceased" test must use this


def test_after_is_open_at_the_latest_end():
    gd = parse_date("AFT 1950")
    assert gd.parsed is True
    assert gd.earliest == date(1950, 1, 1)   # "likely living" test must count this
    assert gd.latest is None                 # "deceased" test must not use this


def test_between_and_uses_the_later_date_as_latest():
    gd = parse_date("BET 1840 AND 1845")
    assert gd.parsed is True
    assert gd.qualifier == "BET"
    assert gd.earliest == date(1840, 1, 1)
    assert gd.latest == date(1845, 12, 31)
    assert gd.display == "between 1840 and 1845"


def test_from_to():
    gd = parse_date("FROM 1900 TO 1910")
    assert gd.parsed is True
    assert gd.earliest == date(1900, 1, 1)
    assert gd.latest == date(1910, 12, 31)


def test_from_alone_is_open_ended():
    gd = parse_date("FROM 1950")
    assert gd.parsed is True
    assert gd.earliest == date(1950, 1, 1)
    assert gd.latest is None


def test_interpreted_date_with_phrase():
    gd = parse_date("INT 1920 (Easter)")
    assert gd.parsed is True
    assert gd.qualifier == "INT"
    assert gd.earliest == date(1920, 1, 1)
    assert "1920" in gd.display and "Easter" in gd.display


def test_bare_phrase_is_unparsed():
    gd = parse_date("(Unknown)")
    assert gd.parsed is False
    assert gd.qualifier == "PHRASE"
    assert gd.display == "(Unknown)"


# Every non-conforming shape actually found in the 5 Oct export
# (docs/phase0-remaining.md) must come back unparsed, not guessed at.
@pytest.mark.parametrize("raw", [
    "pre 1870",
    "26/10/1968",
    "25.07.1897",
    "28th July, 1805",
    "1909 ???",
    "7 May 1797 (or 5 May 1791?)",
    "JAN 1660/61",
    "1767/1768",
    "2nd qtr. 1873",
    "Bef. 2001(as per Husband Ian's Obituary Notice)",
    "9 APR",                       # no year
    "BET JAN AND MAR 1847",        # first bound has no year
    "1995 1996 1997 1998 1999 2000 2001 2002",
])
def test_non_conforming_dates_are_never_guessed_at(raw):
    gd = parse_date(raw)
    assert gd.parsed is False
    assert gd.earliest is None
    assert gd.latest is None
    assert gd.raw == raw


def test_never_invents_precision_year_only_stays_year_only():
    gd = parse_date("1920")
    assert gd.display == "1920"
    assert "Jan" not in gd.display and "1" not in gd.display.replace("1920", "")


def test_to_dict_round_trips_bounds_as_iso_strings():
    gd = parse_date("BET 1840 AND 1845")
    d = gd.to_dict()
    assert d["earliest"] == "1840-01-01"
    assert d["latest"] == "1845-12-31"
    assert d["parsed"] is True

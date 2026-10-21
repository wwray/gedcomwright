"""GEDCOM date parsing (BRIEF.md, Stage 1: "parse into a structure that
keeps qualifiers... and displays them nicely. Never invent precision.").

`parse_date` turns a raw DATE value into a `GedcomDate` with an inclusive
[earliest, latest] bound. Either bound may be None for an open-ended value
(AFT, FROM with no TO) or both None when the value didn't parse at all -
unparseable values are never guessed at (BRIEF.md's "Date handling").

The earliest/latest bounds are deliberately the whole thing the privacy
rules need:
- the "deceased" test uses the *latest* bound (BRIEF.md: "BEF and exact
  dates are used as given. ABT/EST/CAL are used as the stated date.
  BET x AND y uses y. AFT and open-ended dates don't count") - which falls
  out for free here, since AFT's latest bound is None and "None < cutoff"
  is simply false;
- the "likely living" test uses the *earliest* bound ("AFT 1950 counts as
  recent; BEF 1950 does not") - same mechanism, BEF's earliest is None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)  # Feb is the
# non-leap value; the leap case is handled separately where this is used.

# Matches ONLY the three exact GEDCOM date shapes this parser supports:
# "yyyy", "MON yyyy", "d MON yyyy". Anything else (slashes, ordinals, full
# month names, missing year, stray text) deliberately fails to match, so it
# falls through to "unparsed" rather than being guessed at.
_SIMPLE_RE = re.compile(
    r"^(?:(?P<day>\d{1,2})[ ])?(?:(?P<mon>[A-Z]{3})[ ])?(?P<year>\d{3,4})$"
)

_QUALIFIER_WORDS = {
    "ABT": "about", "EST": "estimated", "CAL": "calculated",
    "BEF": "before", "AFT": "after", "FROM": "from", "TO": "to",
}


@dataclass(frozen=True)
class GedcomDate:
    """A parsed GEDCOM DATE value. `display` never claims more precision
    than `raw` gives - a year-only value displays as just a year."""

    raw: str
    qualifier: str | None  # ABT/EST/CAL/BEF/AFT/BET/FROM_TO/INT/PHRASE/None
    parsed: bool
    earliest: date | None
    latest: date | None
    display: str

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "qualifier": self.qualifier,
            "parsed": self.parsed,
            "earliest": self.earliest.isoformat() if self.earliest else None,
            "latest": self.latest.isoformat() if self.latest else None,
            "display": self.display,
        }


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _parse_simple(token: str) -> tuple[date, date, str] | None:
    """Parse 'yyyy' / 'MON yyyy' / 'd MON yyyy' -> (earliest, latest, display).
    None if the token doesn't match one of those three forms exactly."""
    token = token.strip()
    m = _SIMPLE_RE.match(token)
    if not m:
        return None
    day, mon, year = m.group("day"), m.group("mon"), m.group("year")
    if mon and mon not in _MONTHS:
        return None
    if day and not mon:
        return None  # a bare "5 1920" isn't a GEDCOM date shape
    y = int(year)
    try:
        if day and mon:
            d = date(y, _MONTHS[mon], int(day))
            return d, d, f"{int(day)} {mon.title()} {y}"
        if mon:
            mi = _MONTHS[mon]
            last = 29 if (mi == 2 and _is_leap(y)) else _DAYS_IN_MONTH[mi - 1]
            return date(y, mi, 1), date(y, mi, last), f"{mon.title()} {y}"
        return date(y, 1, 1), date(y, 12, 31), str(y)
    except ValueError:
        return None


def _unparsed(raw: str, qualifier: str | None = None) -> GedcomDate:
    return GedcomDate(raw, qualifier, False, None, None, raw)


def parse_date(raw: str | None) -> GedcomDate:
    """Parse one GEDCOM DATE value. Always succeeds - an unparseable value
    comes back with parsed=False and both bounds None, never an exception,
    so a bad date in a 1,400-person file never aborts the whole extract."""
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return GedcomDate(raw_stripped, None, False, None, None, "")

    v = raw_stripped.upper()

    # A bare phrase, with no formal date at all: "(Unknown)".
    if v.startswith("(") and v.endswith(")"):
        return GedcomDate(raw_stripped, "PHRASE", False, None, None, raw_stripped)

    m = re.match(r"^INT (.+?)\s*\((.+)\)$", v)
    if m:
        inner = parse_date(m.group(1))
        phrase = m.group(2).strip().capitalize()
        if inner.parsed:
            return GedcomDate(raw_stripped, "INT", True, inner.earliest,
                               inner.latest, f"{inner.display} ({phrase})")
        return _unparsed(raw_stripped, "INT")

    m = re.match(r"^BET (.+?) AND (.+)$", v)
    if m:
        a, b = _parse_simple(m.group(1)), _parse_simple(m.group(2))
        if a and b:
            return GedcomDate(raw_stripped, "BET", True, a[0], b[1],
                               f"between {a[2]} and {b[2]}")
        return _unparsed(raw_stripped, "BET")

    m = re.match(r"^FROM (.+?) TO (.+)$", v)
    if m:
        a, b = _parse_simple(m.group(1)), _parse_simple(m.group(2))
        if a and b:
            return GedcomDate(raw_stripped, "FROM_TO", True, a[0], b[1],
                               f"from {a[2]} to {b[2]}")
        return _unparsed(raw_stripped, "FROM_TO")

    m = re.match(r"^(ABT|EST|CAL|BEF|AFT|FROM|TO) (.+)$", v)
    if m:
        kind, rest = m.group(1), _parse_simple(m.group(2))
        if not rest:
            return _unparsed(raw_stripped, kind)
        e, l, disp = rest
        word = _QUALIFIER_WORDS[kind]
        if kind in ("ABT", "EST", "CAL"):
            return GedcomDate(raw_stripped, kind, True, e, l, f"{word} {disp}")
        if kind in ("BEF", "TO"):
            return GedcomDate(raw_stripped, kind, True, None, l, f"{word} {disp}")
        return GedcomDate(raw_stripped, kind, True, e, None, f"{word} {disp}")  # AFT, FROM

    simple = _parse_simple(v)
    if simple:
        e, l, disp = simple
        return GedcomDate(raw_stripped, None, True, e, l, disp)

    return _unparsed(raw_stripped)

"""Stable URL slug generation (BRIEF.md, Stage 1: "stable, readable URL slugs
(e.g. /people/james-wray-1838-1888-I123/). Slugs change only if the name or
dates change.")

The bare xref is always appended, so collisions are impossible and a slug is
a pure function of (name, birth year, death year, xref) - nothing else, and
nothing incremental or order-dependent.
"""
from __future__ import annotations

import re
import unicodedata


def _ascii_fold(text: str) -> str:
    """Best-effort transliteration to ASCII (e.g. 'é' -> 'e', 'ü' -> 'u') via
    Unicode NFKD decomposition and dropping combining marks. Pure stdlib."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def slugify(text: str) -> str:
    """ASCII-folded, lowercase, hyphenated. Shared by person_slug and by
    anything else that needs a stable slug from free text - e.g. the
    surname index's per-surname page paths."""
    text = _ascii_fold(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def person_slug(
    given: str | None,
    surname: str | None,
    birth_year: int | None,
    death_year: int | None,
    xref: str,
) -> str:
    """e.g. person_slug("James", "Wray", 1838, 1888, "@I123@")
    -> "james-wray-1838-1888-I123".

    Falls back gracefully: an empty surname (6 people in the Oct export) or
    no name at all still produces a usable slug, since the xref suffix alone
    guarantees uniqueness.
    """
    name_part = slugify(f"{given or ''} {surname or ''}".strip()) or "unknown"

    if birth_year and death_year:
        years = f"{birth_year}-{death_year}"
    elif birth_year:
        years = str(birth_year)
    elif death_year:
        years = f"d{death_year}"
    else:
        years = None

    bare_xref = xref.strip("@")
    parts = [name_part] + ([years] if years else []) + [bare_xref]
    return "-".join(parts)

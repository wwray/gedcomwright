"""Loaders for the hand-maintained lists in overrides/ (BRIEF.md: "hand-
maintained lists" - living.txt, deceased.txt, media-allow.txt, media-type.txt).

Format for the plain xref lists (living.txt, deceased.txt, media-allow.txt):
one GEDCOM xref per line (e.g. '@I123@' or '@M45@'), '#' starts a comment,
blank lines ignored.
"""
from __future__ import annotations

from pathlib import Path


def load_xref_list(path: Path) -> set[str]:
    """Read a list of GEDCOM xrefs, one per line. Missing file -> empty set,
    since every list in overrides/ is optional ("not expected to be needed")."""
    path = Path(path)
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def load_media_type_overrides(path: Path) -> dict[str, str]:
    """overrides/media-type.txt (BRIEF.md Stage 2: "overrides/media-type.txt
    corrects any of these"). Format: '<xref> <photo|document>' one per
    line, e.g. '@M123@ document'. '#' starts a comment; blank lines ignored.
    Missing file -> empty dict."""
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[1] not in ("photo", "document"):
            raise ValueError(f"{path}: malformed line {line!r} "
                              "(expected '<xref> <photo|document>')")
        out[parts[0]] = parts[1]
    return out

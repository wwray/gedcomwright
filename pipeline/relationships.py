"""Family-relationship maps derived from a parsed Gedcom, shared by the
privacy classifier and the extractor so the two can never disagree about who
someone's parents/children/spouses are."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .gedcom import Gedcom, Node


@dataclass
class Relationships:
    indis: dict[str, Node]
    fams: dict[str, Node]
    parents: dict[str, set[str]]
    children: dict[str, set[str]]
    spouses: dict[str, set[str]]
    # (child xref, family xref) -> (_FREL, _MREL), e.g. ("Natural", "Adopted")
    child_rel: dict[tuple[str, str], tuple[str | None, str | None]]
    fam_of_child: dict[str, set[str]]


def build(gedcom: Gedcom) -> Relationships:
    indis = {r.xref: r for r in gedcom.of_type("INDI")}
    fams = {r.xref: r for r in gedcom.of_type("FAM")}

    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    spouses: dict[str, set[str]] = defaultdict(set)
    child_rel: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    fam_of_child: dict[str, set[str]] = defaultdict(set)

    for fx, fam in fams.items():
        sp = [x for x in (fam.val("HUSB"), fam.val("WIFE")) if x in indis]
        for a in sp:
            for b in sp:
                if a != b:
                    spouses[a].add(b)
        for c in fam.all("CHIL"):
            cx = c.value
            if cx not in indis:
                continue
            fam_of_child[cx].add(fx)
            child_rel[(cx, fx)] = (c.val("_FREL"), c.val("_MREL"))
            for p in sp:
                parents[cx].add(p)
                children[p].add(cx)

    # Left as defaultdicts (not converted to plain dict): callers routinely
    # ask "does person X have any parents/children/spouses?" for people who
    # have none, and should get back an empty set, not a KeyError.
    return Relationships(indis, fams, parents, children, spouses,
                          child_rel, fam_of_child)

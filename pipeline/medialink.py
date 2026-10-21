"""The media-attachment graph: which people (INDI) each OBJE reaches, and by
what route. One walk of the tree, shared by:
- pipeline.privacy.withheld_media (BRIEF.md: "media linked to any living
  person is excluded by default");
- pipeline.extract (needs the linked-PUBLISHED-people list for media.json);
- pipeline.media (needs the direct-vs-citation-only distinction for the
  photo/document classification rule).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .gedcom import Gedcom, Node


@dataclass
class MediaLink:
    people: set[str] = field(default_factory=set)
    # True if attached directly to a person or one of their own facts (not
    # only reachable via a citation) - BRIEF.md Stage 2's "citation-only
    # attachment" classification signal is the negation of this.
    direct: bool = False
    portrait_of: set[str] = field(default_factory=set)
    # Every SOUR xref (e.g. "@S9@") this OBJE is cited under - lets
    # pipeline.extract decide whether a media item is *only* ever reached
    # through an Ancestry-published source (7 Oct 2026, Will: "if we're not
    # going to show the ancestry sources perhaps they shouldn't be in the
    # upload" - see extract.py's is_ancestry_only).
    citation_sources: set[str] = field(default_factory=set)


def build(gedcom: Gedcom) -> dict[str, MediaLink]:
    indis = {r.xref for r in gedcom.of_type("INDI")}
    out: dict[str, MediaLink] = defaultdict(MediaLink)

    for n in gedcom.walk_all():
        if n.tag not in ("OBJE", "_PHOTO") or not (n.value or "").startswith("@"):
            continue
        chain: list[Node] = []
        p = n.parent
        while p is not None:
            chain.append(p)
            p = p.parent
        if not chain:
            continue
        root = chain[-1]
        link = out[n.value]

        if n.tag == "_PHOTO":
            if root.tag == "INDI" and root.xref in indis:
                link.people.add(root.xref)
                link.direct = True
                link.portrait_of.add(root.xref)
            continue

        # Citations always sit as OBJE's immediate parent (an OBJE is a
        # direct child of its citation SOUR node - confirmed in Phase 0:
        # "citation sub-tags: PAGE, OBJE, _LINK, NOTE").
        is_citation = chain[0].tag == "SOUR"
        if is_citation:
            link.citation_sources.add(chain[0].value)

        if root.tag == "INDI" and root.xref in indis:
            link.people.add(root.xref)
            if not is_citation:
                link.direct = True
        elif root.tag == "FAM":
            for t in ("HUSB", "WIFE"):
                sx = root.val(t)
                if sx in indis:
                    link.people.add(sx)
            if not is_citation:
                link.direct = True

    return dict(out)

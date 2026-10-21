"""Stage 1: extract (BRIEF.md). GEDCOM -> normalized JSON, with privacy
applied before anything is written (BRIEF.md: "Privacy (in extract, before
anything is written)").

Writes:
  build/data/people.json        - one record per PUBLISHED person only
  build/data/sources.json       - metadata for every source actually cited
                                   by a published person
  build/data/media_links.json   - metadata + linking people for every media
                                   object that survived the privacy filter
  build/reports/privacy.md      - the privacy report BRIEF.md requires
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from . import medialink
from . import relationships as rel
from .config import Config
from .dates import parse_date
from .gedcom import Gedcom, Node
from .overrides import load_xref_list
from .privacy import PrivacyResult, classify, withheld_media, years_ago
from .sanitize import sanitize_html
from .slugs import person_slug

_NAME_RE = re.compile(r"^(?P<given>.*?)/(?P<surname>.*?)/(?P<suffix>.*)$")

# Tags shown on a person's page as facts (BRIEF.md: "events/facts in
# chronological order"). Overlaps privacy.EVENT_TAGS but is kept separate:
# a tag added there for privacy math shouldn't silently start appearing as a
# displayed fact without a deliberate choice here too.
FACT_TAGS = ("BIRT", "DEAT", "BURI", "CREM", "PROB", "BAPM", "CHR", "RESI",
             "OCCU", "NATU", "NATI", "IMMI", "ADOP", "_MILT", "RELI", "EVEN")

CENTENARIAN_YEARS = 110


def parse_name(raw: str | None) -> dict:
    """'James /Wray/' -> {given: James, surname: Wray, ...}. A NAME with no
    slashes at all (6 people in the Oct export, e.g. 'Emerson, Emer') falls
    back to treating the whole value as the given name - a lint item, not a
    pipeline failure."""
    raw = (raw or "").strip()
    m = _NAME_RE.match(raw)
    if m:
        given, surname, suffix = (m.group("given").strip(),
                                   m.group("surname").strip(),
                                   m.group("suffix").strip())
    else:
        given, surname, suffix = raw, "", ""
    display = " ".join(p for p in (given, surname, suffix) if p) or "Unknown"
    return {"given": given, "surname": surname, "suffix": suffix,
            "display": display, "raw": raw}


def choose_primary_name(name_nodes: list[Node], sex: str | None) -> tuple[dict, list[dict]]:
    """GEDCOM's standard NAME.TYPE values include "married"/"maiden"/
    "birth" (among others), but the real export's NAME.TYPE is only ever
    "aka" - confirmed by inspecting the whole file, 6 Oct 2026 - so this
    currently changes nothing for anyone. It's forward-looking (Will's
    choice, 6 Oct 2026): the moment a person gets a NAME record typed
    "married" in FTM, their page shows that name as primary, with a
    differently-surnamed "birth"/"maiden"-typed (or, failing that, the
    first-listed untyped) name bracketed after it as "(née X)"/"(born X)" -
    with no further pipeline change needed.

    Falls back to exactly the previous behaviour (first-listed name
    primary, the rest as aka, no bracket) whenever no "married"-typed name
    exists."""
    if not name_nodes:
        return parse_name(""), []

    parsed = [(parse_name(n.value), (n.val("TYPE") or "").strip().lower())
              for n in name_nodes]
    married = next((pn for pn in parsed if pn[1] == "married"), None)
    if married is None:
        return parsed[0][0], [pn[0] for pn in parsed[1:]]

    primary = married[0]
    rest = [pn for pn in parsed if pn is not married]
    birth = next((pn[0] for pn in rest if pn[1] in ("birth", "maiden")
                  and pn[0]["surname"] and pn[0]["surname"] != primary["surname"]), None)
    if birth is None and rest and rest[0][1] == "" \
            and rest[0][0]["surname"] and rest[0][0]["surname"] != primary["surname"]:
        birth = rest[0][0]
    if birth:
        word = "née" if sex == "F" else "born"
        primary = {**primary, "display": f"{primary['display']} ({word} {birth['surname']})"}
    return primary, [pn[0] for pn in rest]


def _resolve_note(node: Node, gedcom: Gedcom) -> str:
    """A NOTE value is either inline text or a pointer to a top-level NOTE
    record; resolve either way, then sanitize to the HTML allowlist."""
    text = node.value
    if text.startswith("@") and text in gedcom.by_xref:
        text = gedcom.by_xref[text].value
    return sanitize_html(text)


def _notes_of(node: Node, gedcom: Gedcom) -> list[str]:
    return [t for t in (_resolve_note(n, gedcom) for n in node.all("NOTE")) if t]


def _place(node: Node) -> dict | None:
    plac = node.first("PLAC")
    if plac is None:
        return None
    out: dict = {"text": plac.value}
    map_node = plac.first("MAP")
    if map_node is not None:
        lati, longi = map_node.val("LATI"), map_node.val("LONG")
        if lati and longi:
            out["lati"], out["long"] = lati, longi
    return out


def _is_ancestry_publ(rec) -> bool:
    publ = rec.val("PUBL")
    return bool(publ) and "ancestry" in publ.lower()


def _media_metadata(gedcom: Gedcom, obje_xref: str, link, published: set[str],
                     ancestry_sources: dict[str, bool]) -> dict | None:
    """`link` is the medialink.MediaLink for this xref (may be None if, for
    some reason, the medialink walk never saw it - shouldn't happen for
    anything in ex.used_media, but handled defensively)."""
    rec = gedcom.by_xref.get(obje_xref)
    if rec is None or rec.tag != "OBJE":
        return None
    file_node = rec.first("FILE")
    if file_node is None:
        return None
    linked_people = sorted(x.strip("@") for x in (link.people if link else set()) if x in published)
    return {
        "id": obje_xref.strip("@"),
        "file_path": file_node.value,
        "title": file_node.val("TITL"),
        "description": file_node.val("_TEXT"),
        "date_text": file_node.val("_DATE"),
        # For Stage 2 (media.py): BRIEF.md's photo/document rule needs to
        # know whether this item was EVER attached directly (to a person or
        # one of their own facts), or only ever reached via a citation.
        "citation_only": not (link.direct if link else False),
        "people": linked_people,
        "portrait_of": sorted(x.strip("@") for x in (link.portrait_of if link else set())),
        # Will, 7 Oct 2026: "if we're not going to show the ancestry
        # sources perhaps they shouldn't be in the upload" - a media item
        # is ancestry-only when it's never a portrait, never attached
        # directly (to a person or one of their own facts), and every
        # citation that reaches it cites an Ancestry-published source.
        # pipeline.media skips derivative generation for these entirely -
        # render.py's own is_ancestry checks (group_citations/build_gallery)
        # stay in place too, as a second, independent safeguard.
        "is_ancestry_only": bool(
            link and not link.direct and not link.portrait_of
            and link.citation_sources
            and all(ancestry_sources.get(sx, False) for sx in link.citation_sources)
        ),
    }


def _source_metadata(gedcom: Gedcom, sour_xref: str) -> dict | None:
    rec = gedcom.by_xref.get(sour_xref)
    if rec is None:
        return None
    note = rec.first("NOTE")
    title = rec.val("TITL") or ""
    return {
        "id": sour_xref.strip("@"),
        "title": title,
        "author": rec.val("AUTH"),
        "publ": rec.val("PUBL"),
        "note": _resolve_note(note, gedcom) if note is not None else "",
        # BRIEF.md: match the "Other researchers' trees" bucket on title,
        # not xref, "in case the xref changes" (which Phase 0's ID test
        # suggests is unlikely, but the brief is explicit either way).
        "is_other_researchers_tree": title.strip() == "Ancestry Family Trees",
        # Will's call, 6 Oct 2026 (reversing BRIEF.md's original "no
        # Ancestry exclusion flag"): worried about republishing Ancestry's
        # own scanned record images. PUBL is a reliable, structured signal
        # for "this record collection is Ancestry's" - confirmed against
        # the real export: 239 of 269 sources carry an "Ancestry.com
        # Operations"/"Ancestry Operations" PUBL string (the rest are
        # FamilySearch, Find A Grave, state archives, or unpublished direct
        # citations) - far more reliable than matching on title or
        # filename, which only catches specific wording. Citation *text*
        # (this field's sibling keys) is unaffected either way - only
        # render.py's citation *images* are gated on this flag.
        "is_ancestry": _is_ancestry_publ(rec),
    }


class Extractor:
    """Builds one event/citation/media subtree at a time, drops anything the
    privacy filter forbids, and tracks which sources/media ended up actually
    referenced so only those get written to sources.json/media_links.json."""

    def __init__(self, gedcom: Gedcom, r: rel.Relationships,
                 published: set[str], withheld_obje: set[str],
                 suppress_facts: set[str]):
        self.gedcom = gedcom
        self.r = r
        self.published = published
        self.withheld_obje = withheld_obje
        self.suppress_facts = {f.lower() for f in suppress_facts}
        self.used_sources: set[str] = set()
        self.used_media: set[str] = set()

    def _media_ids(self, node: Node) -> list[str]:
        out = []
        for o in node.all("OBJE"):
            if not o.value.startswith("@") or o.value in self.withheld_obje:
                continue
            out.append(o.value.strip("@"))
            self.used_media.add(o.value)
        return out

    def _citations(self, node: Node) -> list[dict]:
        out = []
        for s in node.all("SOUR"):
            if not s.value.startswith("@"):
                continue
            self.used_sources.add(s.value)
            out.append({
                "source_id": s.value.strip("@"),
                "page": s.val("PAGE"),
                "notes": _notes_of(s, self.gedcom),
                "link": s.val("_LINK"),
                "media": self._media_ids(s),
            })
        return out

    def _event(self, node: Node, tag: str, *, label: str | None = None,
               extra: dict | None = None) -> dict | None:
        """None if this fact type is configured-suppressed (BRIEF.md
        'facts.suppress', default [Race]) - dropped in extract, per the
        brief, not just hidden at render time."""
        if label and label.lower() in self.suppress_facts:
            return None
        date_node = node.first("DATE")
        gd = parse_date(date_node.value) if date_node is not None else None
        ev = {
            "tag": tag,
            "label": label,
            "date": gd.to_dict() if gd else None,
            "place": _place(node),
            "notes": _notes_of(node, self.gedcom),
            "citations": self._citations(node),
            "media": self._media_ids(node),
        }
        if extra:
            ev.update(extra)
        return ev

    def person_facts(self, px: str) -> list[dict]:
        rec = self.r.indis[px]
        out = []
        for n in rec.children:
            if n.tag not in FACT_TAGS:
                continue
            label = n.val("TYPE") if n.tag == "EVEN" else None
            ev = self._event(n, n.tag, label=label)
            if ev is not None:
                out.append(ev)

        # A marriage is as much this person's event as the family's, and
        # there's no separate family-page stage in the MVP to show it on
        # instead, so it's attached to both spouses' own fact lists.
        for fs in rec.all("FAMS"):
            fam = self.r.fams.get(fs.value)
            if fam is None:
                continue
            spouse = next((x for x in (fam.val("HUSB"), fam.val("WIFE"))
                           if x and x != px), None)
            for n in fam.all("MARR"):
                ev = self._event(n, "MARR", extra={
                    "family_id": fs.value.strip("@"),
                    "spouse_id": spouse.strip("@") if spouse else None,
                    "spouse_published": bool(spouse and spouse in self.published),
                })
                if ev is not None:
                    out.append(ev)

        def sort_key(ev):
            d = ev["date"]
            if d and d["parsed"]:
                return (0, d["earliest"] or d["latest"] or "9999-99-99")
            return (1, "")

        return sorted(out, key=sort_key)

    def relative_ref(self, xref: str, *, show_living_placeholder: bool) -> dict | None:
        """BRIEF.md Hard Rule 1: a hidden relative appears only as 'Living
        [Surname]', with no link and none of their own data - or is omitted
        if config says so. A published relative is referenced by bare id;
        render resolves id -> slug from the full people list."""
        if xref in self.published:
            return {"id": xref.strip("@"), "hidden": False}
        if not show_living_placeholder:
            return None
        rec = self.r.indis.get(xref)
        surname = ""
        if rec is not None:
            name_node = rec.first("NAME")
            if name_node is not None:
                surname = parse_name(name_node.value)["surname"]
        return {"hidden": True, "surname": surname}


def build_person(ex: Extractor, px: str, priv, *, show_living_placeholder: bool) -> dict:
    rec = ex.r.indis[px]
    names = rec.all("NAME")
    primary, aka = choose_primary_name(names, rec.val("SEX"))

    facts = ex.person_facts(px)
    birth_year = death_year = None
    for f in facts:
        d = f["date"]
        if f["tag"] == "BIRT" and d and d["parsed"] and d["earliest"]:
            birth_year = int(d["earliest"][:4])
        if f["tag"] == "DEAT" and d and d["parsed"] and d["latest"]:
            death_year = int(d["latest"][:4])

    slug = person_slug(primary["given"], primary["surname"], birth_year, death_year, px)

    def refs(xrefs):
        out = [ex.relative_ref(x, show_living_placeholder=show_living_placeholder)
               for x in sorted(xrefs)]
        return [r for r in out if r is not None]

    portrait_node = rec.first("_PHOTO")
    portrait = None
    if (portrait_node is not None and portrait_node.value.startswith("@")
            and portrait_node.value not in ex.withheld_obje):
        portrait = portrait_node.value.strip("@")
        ex.used_media.add(portrait_node.value)

    media_ids = set(ex._media_ids(rec))
    if portrait:
        media_ids.add(portrait)
    for f in facts:
        media_ids.update(f["media"])
        for c in f["citations"]:
            media_ids.update(c["media"])

    return {
        "id": px.strip("@"),
        "slug": slug,
        "name": primary,
        "aka": aka,
        "sex": rec.val("SEX"),
        "fsid": rec.val("FSID"),
        "birth_year": birth_year,
        "death_year": death_year,
        "facts": facts,
        "parents": refs(ex.r.parents.get(px, ())),
        "spouses": refs(ex.r.spouses.get(px, ())),
        "children": refs(ex.r.children.get(px, ())),
        "portrait": portrait,
        "media": sorted(media_ids),
        "notes": _notes_of(rec, ex.gedcom),
        "privacy_group": priv.group,
        "generation": priv.generation,
    }


def write_privacy_report(r: rel.Relationships, priv: PrivacyResult,
                          withheld: dict[str, set[str]], path: Path, *,
                          as_of: date) -> None:
    """BRIEF.md's four required sections, in order."""

    def name_of(xref: str) -> str:
        rec = r.indis.get(xref)
        if rec is None:
            return "?"
        n = rec.first("NAME")
        return parse_name(n.value)["display"] if n is not None else "Unknown"

    lines = [
        "# Privacy report", "",
        f"Generated {as_of.isoformat()}.", "",
        f"**{len(priv.published_xrefs)} published, {len(priv.hidden_xrefs)} hidden, "
        f"{len(withheld)} media objects withheld.**", "",
        "## Everyone hidden, with group and reason", "",
    ]
    for x in sorted(priv.hidden_xrefs, key=lambda x: (priv.people[x].group, name_of(x))):
        p = priv.people[x]
        gen = "not connected" if p.generation is None else f"generation {p.generation}"
        lines.append(f"- `{x}` {name_of(x)!r} -- group {p.group}, {gen}: {p.publish_reason}")

    lines += ["", "## Everyone published as undetermined (group 4)", ""]
    undetermined = sorted(
        (x for x in priv.published_xrefs if priv.people[x].group == 4),
        key=name_of,
    )
    if not undetermined:
        lines.append("(none)")
    for x in undetermined:
        lines.append(f"- `{x}` {name_of(x)!r} -- generation {priv.people[x].generation}")

    lines += [
        "", "## Centenarian check", "",
        "Anyone published only by the event or child rule whose qualifying "
        f"date is within the last {CENTENARIAN_YEARS} years.", "",
    ]
    cutoff = years_ago(as_of, CENTENARIAN_YEARS)
    hits = sorted(
        (x for x in priv.published_xrefs
         if priv.people[x].evidence_date and priv.people[x].evidence_date > cutoff),
        key=lambda x: priv.people[x].evidence_date,
    )
    if not hits:
        lines.append("(none)")
    for x in hits:
        p = priv.people[x]
        lines.append(f"- `{x}` {name_of(x)!r} -- {p.group_reason}, "
                      f"qualifying date {p.evidence_date.isoformat()}")

    lines += ["", "## People with unparseable dates", ""]
    if not priv.unparseable_dates:
        lines.append("(none)")
    for x, fact_path, raw in sorted(priv.unparseable_dates):
        lines.append(f"- `{x}` {name_of(x)!r} -- {fact_path} `{raw}`")

    if priv.generation_conflicts:
        lines += ["", "## People with conflicting generations by path", "",
                  "Informational only (BRIEF.md: take the younger value; this "
                  "changes a publish decision only if the paths straddle "
                  "min_generation).", ""]
        for x in sorted(priv.generation_conflicts, key=name_of):
            lines.append(f"- `{x}` {name_of(x)!r} -- used generation "
                          f"{priv.people[x].generation}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def run(config: Config, *, as_of: date | None = None) -> dict:
    """Runs the whole extract stage and writes every output file. Returns a
    summary dict (also printed by the CLI)."""
    as_of = as_of or date.today()
    ged_path = config.resolve_gedcom()
    gedcom = Gedcom(ged_path)
    r = rel.build(gedcom)

    living_overrides = load_xref_list(config.overrides_dir / "living.txt")
    deceased_overrides = load_xref_list(config.overrides_dir / "deceased.txt")
    media_allow = load_xref_list(config.overrides_dir / "media-allow.txt")

    priv = classify(
        gedcom,
        home_person=config.home_person,
        min_generation=config.min_generation,
        thresholds=config.thresholds,
        living_overrides=frozenset(living_overrides),
        deceased_overrides=frozenset(deceased_overrides),
        as_of=as_of,
    )
    withheld = withheld_media(gedcom, priv.hidden_xrefs, frozenset(media_allow))
    withheld_obje = set(withheld.keys())

    ex = Extractor(gedcom, r, priv.published_xrefs, withheld_obje, set(config.suppress_facts))

    people = [
        build_person(ex, px, p, show_living_placeholder=config.show_living_placeholder)
        for px, p in priv.people.items() if p.published
    ]
    people.sort(key=lambda p: p["slug"])

    sources = [m for sx in sorted(ex.used_sources)
               if (m := _source_metadata(gedcom, sx)) is not None]
    links = medialink.build(gedcom)
    ancestry_sources = {s.xref: _is_ancestry_publ(s) for s in gedcom.of_type("SOUR")}
    media_links = [m for mx in sorted(ex.used_media)
                   if (m := _media_metadata(gedcom, mx, links.get(mx), priv.published_xrefs,
                                             ancestry_sources)) is not None]

    _write_json(config.data_dir / "people.json", people)
    _write_json(config.data_dir / "sources.json", sources)
    _write_json(config.data_dir / "media_links.json", media_links)
    write_privacy_report(r, priv, withheld, config.reports_dir / "privacy.md", as_of=as_of)

    return {
        "gedcom_path": str(ged_path),
        "people_total": len(r.indis),
        "people_published": len(people),
        "people_hidden": len(priv.hidden_xrefs),
        "sources_used": len(sources),
        "media_used": len(media_links),
        "media_withheld": len(withheld),
        "unparseable_dates": len(priv.unparseable_dates),
        "generation_conflicts": len(priv.generation_conflicts),
    }

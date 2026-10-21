"""Stage 3: render (BRIEF.md). Person pages, the surname index, and (soon)
home/about/404/search - the design approved on one person page (Gordon
Wray Crisp, 9 Oct 2026), now applied across the site.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2
import pyvips

from .slugs import slugify

if TYPE_CHECKING:
    from .config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
STATIC_DIR = REPO_ROOT / "static"

FACT_LABELS = {
    "BIRT": "Birth", "DEAT": "Death", "BURI": "Burial", "CREM": "Cremation",
    "PROB": "Probate", "BAPM": "Baptism", "CHR": "Christening", "RESI": "Residence",
    "OCCU": "Occupation", "NATU": "Naturalization", "NATI": "Nationality",
    "IMMI": "Immigration", "ADOP": "Adoption", "_MILT": "Military service",
    "RELI": "Religion",
}


_env_cache: jinja2.Environment | None = None


def _env() -> jinja2.Environment:
    """A module-level singleton: building a fresh jinja2.Environment per call
    (the old behaviour) re-reads and re-compiles every template from disk
    every time, since a new Environment's template cache starts empty - on
    the real site that meant person.html alone was recompiled 1,379 times.
    Templates are static for the life of one process, so one Environment,
    built once and reused, is safe."""
    global _env_cache
    if _env_cache is None:
        _env_cache = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=jinja2.select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env_cache


def fact_label(fact: dict, people_by_id: dict) -> str:
    """'Birth — 13 Nov 1896', 'Marriage to Jane Methven — 1860'."""
    base = fact.get("label") or FACT_LABELS.get(
        fact["tag"], fact["tag"].replace("_", " ").title())
    if fact["tag"] == "MARR":
        spouse_id = fact.get("spouse_id")
        if spouse_id and fact.get("spouse_published") and spouse_id in people_by_id:
            base = f"Marriage to {people_by_id[spouse_id]['name']['display']}"
        else:
            base = "Marriage"
    d = fact.get("date")
    if d and d.get("display"):
        # Not gated on d["parsed"]: that flag only means the date couldn't
        # be reduced to an earliest/latest bound for privacy-threshold math
        # (e.g. "16/07/2020" - GEDCOM dates don't use slashes) - it still
        # carries a usable raw `display` string, which vital_facts() above
        # the fold already shows unconditionally. Gating this on `parsed`
        # made the Timeline silently drop a date the vitals block showed
        # (found by Will, 6 Oct 2026, on a DEAT fact with exactly this shape).
        return f"{base} — {d['display']}"
    return base


def _dedupe_facts(facts: list[dict], people_by_id: dict) -> list[dict]:
    """Collapse facts that would render as the literal same Timeline line
    (same label - see fact_label - and same place) into one, unioning their
    citations (deduped by source/page/link) and media (deduped by id)
    rather than dropping either side.

    Found tree-wide (12 people, 7 Oct 2026), the same root cause as
    _dedupe_citations: genuine duplicate data in FTM itself, not a pipeline
    bug - confirmed by reading the raw GEDCOM. Susan Tupholme (@I329@) has
    two complete, byte-identical BIRT and DEAT blocks directly under one
    INDI record; Hugh Tupholme and Martha Louise Watts's shared marriage is
    duplicated via an extra FAM record with no spouse link at all (same
    MARR date/place, @F315@'s WIFE tag is simply absent). Done here rather
    than in extract.py so people.json stays a faithful mirror of the GEDCOM
    - this is a display-layer merge, same as _dedupe_citations."""
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for f in facts:
        label = fact_label(f, people_by_id)
        place = (f.get("place") or {}).get("text")
        key = (label, place)
        if key not in merged:
            merged[key] = {**f, "citations": list(f.get("citations") or []),
                           "media": list(f.get("media") or [])}
            order.append(key)
        else:
            existing = merged[key]
            seen_cites = {(c["source_id"], c.get("page"), c.get("link"))
                          for c in existing["citations"]}
            for c in f.get("citations") or ():
                ck = (c["source_id"], c.get("page"), c.get("link"))
                if ck not in seen_cites:
                    existing["citations"].append(c)
                    seen_cites.add(ck)
            seen_media = set(existing["media"])
            for mid in f.get("media") or ():
                if mid not in seen_media:
                    existing["media"].append(mid)
                    seen_media.add(mid)
    return [merged[k] for k in order]


def group_citations(person: dict, sources_by_id: dict, people_by_id: dict,
                     media_by_id: dict, site_dir: Path, root: str) -> tuple[list[dict], list[dict]]:
    """BRIEF.md: sources are grouped by source, with citations under each
    (otherwise NAME/RESI citations swamp the page), and citations to
    Ancestry Family Trees are shown in their own group, labelled "Other
    researchers' trees (not original records)" and listed after the record
    sources, matched on title rather than id - precomputed onto each source
    as `is_other_researchers_tree` in extract.py.

    Each citation also carries `media_items` - the actual record image(s) it
    cites, resolved the same way as the main gallery - so a citation can
    link straight to the source image in the same lightbox/viewer, rather
    than making a reader hunt for it in the gallery below (Will's feedback,
    9 Oct 2026) - EXCEPT when the citation's source is Ancestry-published
    (`is_ancestry`, set in extract.py's _source_metadata): citation *text*
    (title/author/page/link/notes) still shows, but no record-image
    thumbnail is built at all (Will's call, 6 Oct 2026, over concern about
    republishing Ancestry's own scanned record images - reversing
    BRIEF.md's original "no Ancestry exclusion flag"). build_gallery below
    applies the same exclusion to `person["media"]` via
    _ancestry_only_media_ids(), since that list is a union that includes
    every citation's media too - without it, the same Ancestry record scan
    would reappear in the main gallery even though the Sources section hid
    it (found by Will on the real site, 7 Oct 2026, same day as the
    original fix)."""
    grouped: dict[str, list[dict]] = {}
    for fact in person["facts"]:
        label = fact_label(fact, people_by_id)
        for cite in fact["citations"]:
            source = sources_by_id.get(cite["source_id"])
            if source is not None and source.get("is_ancestry"):
                media_items = []
            else:
                media_items = [item for mid in cite.get("media", [])
                               if (item := _build_media_item(mid, media_by_id, site_dir, root))]
            grouped.setdefault(cite["source_id"], []).append(
                {**cite, "fact_label": label, "media_items": media_items})

    record_groups, other_groups = [], []
    for sid, cites in grouped.items():
        source = sources_by_id.get(sid)
        if source is None:
            continue
        entry = {"source": source, "citations": _dedupe_citations(cites)}
        (other_groups if source["is_other_researchers_tree"] else record_groups).append(entry)
    record_groups.sort(key=lambda g: g["source"]["title"].lower())
    other_groups.sort(key=lambda g: g["source"]["title"].lower())
    return record_groups, other_groups


def _dedupe_citations(cites: list[dict]) -> list[dict]:
    """Collapse citations to the same source that would render as literally
    identical lines (same fact label, page text, link and notes) into one,
    merging their media_items (deduped by id) rather than dropping either
    side.

    Found by Will on the real site, 7 Oct 2026 (James Wray, @I313@): FTM's
    own data has two separate SOUR citations under one RESI fact, both
    citing source S139 with the exact same PAGE text but two *different*
    scanned images (@M1289@ vs @M1292@) - a genuine duplicate data entry,
    not a pipeline bug (confirmed by reading the raw GEDCOM subtree). It
    reads as a visible duplicate specifically because nothing in the
    rendered text distinguishes the two citations - true for any source,
    but guaranteed for an Ancestry one, since its images are suppressed
    entirely (see group_citations' docstring) and only the identical text
    is left. 155 such (source, fact, page) groups exist across 124 people
    tree-wide - common enough to fix in render rather than ask Will to hand
    -edit each one in FTM."""
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for c in cites:
        key = (c["fact_label"], c.get("page"), c.get("link"), tuple(c.get("notes") or ()))
        if key not in merged:
            merged[key] = {**c, "media_items": list(c.get("media_items") or [])}
            order.append(key)
        else:
            existing = merged[key]
            seen_ids = {m["id"] for m in existing["media_items"]}
            for m in c.get("media_items") or ():
                if m["id"] not in seen_ids:
                    existing["media_items"].append(m)
                    seen_ids.add(m["id"])
    return [merged[k] for k in order]


def resolve_relative(ref: dict, people_by_id: dict) -> dict:
    """BRIEF.md Hard Rule 1: a hidden relative appears only as 'Living
    [Surname]', with no link - ref already carries only a surname (never a
    given name) for that case, from extract.py."""
    if ref.get("hidden"):
        surname = (ref.get("surname") or "").strip()
        return {"hidden": True, "label": f"Living {surname}" if surname else "Living"}
    p = people_by_id.get(ref["id"])
    if p is None:
        return {"hidden": True, "label": "Unknown"}
    return {"hidden": False, "label": p["name"]["display"], "slug": p["slug"]}


def _image_dims(path: Path) -> tuple[int | None, int | None]:
    if not path.exists():
        return None, None
    im = pyvips.Image.new_from_file(str(path))
    return im.width, im.height


def _build_media_item(mid: str, media_by_id: dict, site_dir: Path, root: str,
                       *, is_portrait: bool = False) -> dict | None:
    """One gallery-ready media item, shared by the main Media gallery and by
    any citation that cites this same image (so both use the identical
    PhotoSwipe/OpenSeadragon markup and the one click-handler in gallery.js
    picks up both, with no new JS needed).

    Every "document" item (PDF or not) carries a normalized `pages` list -
    length 1 for a plain document, length N for an N-page PDF - so the
    template/JS don't need to special-case PDFs at all; OpenSeadragon's own
    `sequenceMode` handles paging when there's more than one. Previously
    only a PDF's first page was ever shown at all (found by Will, 6 Oct
    2026: 3 of the real export's 7 PDFs have more than one page, with pages
    2+ processed but unreachable in the UI). `root` is baked into each
    page's path here (unlike every other root-relative reference, which the
    *template* prefixes) because these paths are read by JS from a JSON
    attribute, not interpolated by Jinja."""
    m = media_by_id.get(mid)
    if m is None:
        return None
    item = {
        "id": mid,
        "type": m["type"],
        "caption": m.get("caption"),
        "description": m.get("description"),
        "date_text": m.get("date_text"),
        "is_portrait": is_portrait,
    }
    if m["type"] == "photo":
        item["thumbnail"] = m.get("thumbnail")
        zoom_path = site_dir / m["zoom"]["path"]
        w, h = _image_dims(zoom_path)
        item.update(full=m["zoom"]["path"], full_width=w, full_height=h)
        return item

    raw_pages = m.get("pages") if m.get("is_pdf") else [m]
    if not raw_pages:
        return None
    item["thumbnail"] = raw_pages[0]["thumbnail"]
    item["page_count"] = len(raw_pages)
    item["pages"] = [
        {"thumbnail": root + p["thumbnail"],
         "zoom": {"kind": p["zoom"]["kind"], "path": root + p["zoom"]["path"]}}
        for p in raw_pages
    ]
    return item


def _ancestry_only_media_ids(person: dict, sources_by_id: dict) -> set[str]:
    """Media ids that only ever reach this person through an
    Ancestry-published citation - see group_citations' docstring. A media id
    stays visible if it's ever reached another way too: a fact's own direct
    media, the portrait, or a citation to a non-Ancestry source."""
    ancestry_ids: set[str] = set()
    safe_ids: set[str] = set()
    for fact in person["facts"]:
        safe_ids.update(fact.get("media", ()))
        for cite in fact["citations"]:
            source = sources_by_id.get(cite["source_id"])
            target = ancestry_ids if (source is not None and source.get("is_ancestry")) else safe_ids
            target.update(cite.get("media", ()))
    return ancestry_ids - safe_ids


def build_gallery(person: dict, sources_by_id: dict, media_by_id: dict,
                   site_dir: Path, root: str) -> list[dict]:
    """Portrait first (BRIEF.md: "media gallery, with the _PHOTO portrait
    first"), then the rest in id order.

    Distinct OBJE records with byte-identical content (Phase 0: 38 such
    pairs in the real export) already share one derivative file on disk -
    content-hash-keyed caching in media.py sees them as the same work and
    writes it once - but that only dedupes storage, not *this* list: before
    this fix, each surviving id still got its own gallery entry, so the
    same photo could appear two, three, even four times side by side with
    nothing to tell them apart (found by Will on the real site, 7 Oct 2026,
    James Wray Sr. @I313@ - 7 distinct repeated images, 10 redundant
    thumbnails total; 18 people tree-wide, 40 redundant thumbnails). Since
    `thumbnail` is always a content-hash-derived path (set in
    _build_media_item for both photo and document types), the same
    underlying image always produces the identical `thumbnail` string -
    keying on it here is therefore an exact, not approximate, content
    check, not a guess based on caption or filename."""
    ancestry_only = _ancestry_only_media_ids(person, sources_by_id)
    if person["portrait"]:
        ancestry_only.discard(person["portrait"])
    ids = [mid for mid in person["media"] if mid not in ancestry_only]
    if person["portrait"] and person["portrait"] in ids:
        ids.remove(person["portrait"])
        ids.insert(0, person["portrait"])

    gallery = []
    seen_thumbnails: set[str] = set()
    for mid in ids:
        item = _build_media_item(mid, media_by_id, site_dir, root,
                                  is_portrait=(mid == person["portrait"]))
        if item is None or item["thumbnail"] in seen_thumbnails:
            continue
        seen_thumbnails.add(item["thumbnail"])
        gallery.append(item)
    return gallery


VITAL_TAGS = ("BIRT", "DEAT", "BURI")
VITAL_LABELS = {"BIRT": "Born", "DEAT": "Died", "BURI": "Buried"}


def vital_facts(facts: list[dict]) -> list[dict]:
    """The compact Born/Died/Buried summary shown above the fold, alongside
    the portrait and Family - the rest of the facts (residences, military
    service, and so on) stay in the full timeline further down. GedcomDate's
    `display` already falls back to the raw text for an unparsed date (e.g.
    "pre 1870"), so no special-casing is needed here for that case."""
    out = []
    for tag in VITAL_TAGS:
        f = next((x for x in facts if x["tag"] == tag), None)
        if f is None:
            continue
        d = f.get("date")
        out.append({
            "label": VITAL_LABELS[tag],
            "date": d["display"] if d else None,
            "place": (f.get("place") or {}).get("text"),
        })
    return out


def _join_and(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def relation_summary(person: dict, parents: list[dict], spouses: list[dict],
                      children: list[dict]) -> list[str]:
    """A short plain-language summary near the top of the page - 'Son of
    Robert Wray and Margaret Crisp', 'Married Jane Methven' - ahead of the
    full Family section lower down."""
    out = []
    child_word = {"M": "Son", "F": "Daughter"}.get(person.get("sex"), "Child")
    if parents:
        out.append(f"{child_word} of {_join_and([p['label'] for p in parents])}")
    for s in spouses:
        out.append(f"Married {s['label']}")
    if children:
        if len(children) <= 4:
            out.append(f"Parent of {_join_and([c['label'] for c in children])}")
        else:
            out.append(f"Parent of {len(children)} children")
    return out


def _representative_place(person: dict) -> str | None:
    for tag in ("DEAT", "BIRT"):
        for f in person["facts"]:
            if f["tag"] == tag:
                place = (f.get("place") or {}).get("text")
                if place:
                    return place
    return None


def _meta_description(person: dict, place: str | None, site_title: str) -> str:
    name = person["name"]["display"]
    bits = []
    if person.get("birth_year"):
        bits.append(f"born {person['birth_year']}")
    if person.get("death_year"):
        bits.append(f"died {person['death_year']}")
    where = f" in {place}" if place else ""
    facts_bit = (", " + " and ".join(bits) + where) if bits else where
    return f"{name}{facts_bit}. A family history record from {site_title}."


def _schema_org_person(person: dict, context: dict, canonical: str, url_prefix: str) -> dict:
    """BRIEF.md: "schema.org Person JSON-LD, using URLs of published
    people only." """
    def person_stub(ref: dict) -> dict | None:
        if ref["hidden"]:
            return None
        return {
            "@type": "Person", "name": ref["label"],
            "url": f"{url_prefix}/people/{ref['slug']}/",
        }

    out: dict = {"@context": "https://schema.org", "@type": "Person",
                 "name": person["name"]["display"], "url": canonical}
    if person.get("birth_year"):
        out["birthDate"] = str(person["birth_year"])
    if person.get("death_year"):
        out["deathDate"] = str(person["death_year"])
    place = _representative_place(person)
    if place:
        out["deathPlace" if person.get("death_year") else "birthPlace"] = \
            {"@type": "Place", "name": place}
    parents = [s for r in context["parents"] if (s := person_stub(r))]
    if parents:
        out["parent"] = parents
    children = [s for r in context["children"] if (s := person_stub(r))]
    if children:
        out["children"] = children
    spouses = [s for r in context["spouses"] if (s := person_stub(r))]
    if spouses:
        out["spouse"] = spouses
    return out


def person_context(person_id: str, config: "Config", *, root: str = "../../",
                    people: list[dict] | None = None, sources: list[dict] | None = None,
                    media: list[dict] | None = None) -> dict:
    """`root` defaults to the real site's path depth (person pages sit two
    levels deep, people/<slug>/) but is overridable - used to generate a
    flat-rooted preview copy for hosts (like a published Artifact preview)
    that serve the page at their own root rather than at this nested path.

    `people`/`sources`/`media` are optional preloaded data (already-parsed
    people.json/sources.json/media.json): render_all_person_pages loads each
    once and passes them through, rather than every one of 1,379 person
    pages re-reading and re-parsing the same ~4.6MB people.json from disk.
    Left optional, loaded from disk when omitted, so single-person callers
    (render_preview_html, tests) don't need to load anything themselves."""
    data_dir = config.data_dir
    if people is None:
        people = json.loads((data_dir / "people.json").read_text(encoding="utf-8"))
    if sources is None:
        sources = json.loads((data_dir / "sources.json").read_text(encoding="utf-8"))
    if media is None:
        media = json.loads((data_dir / "media.json").read_text(encoding="utf-8"))

    people_by_id = {p["id"]: p for p in people}
    sources_by_id = {s["id"]: s for s in sources}
    media_by_id = {m["id"]: m for m in media}

    person = people_by_id[person_id]
    deduped_facts = _dedupe_facts(person["facts"], people_by_id)
    person = {**person, "facts": deduped_facts}
    record_sources, other_sources = group_citations(
        person, sources_by_id, people_by_id, media_by_id, config.site_dir, root)
    facts = [{**f, "label_display": fact_label(f, people_by_id)} for f in deduped_facts]
    place = _representative_place(person)
    years = "–".join(str(y) for y in (person["birth_year"], person["death_year"]) if y)

    url_prefix = f"https://{config.site_domain}{config.site_base_path}".rstrip("/")
    canonical = f"{url_prefix}/people/{person['slug']}/"

    context = {
        # Every internal link/asset reference is built relative to `root`,
        # never as an absolute "/<base_path>/..." path. The real site lives
        # at a subpath of a shared docroot (e.g. example.com/family), and relative paths work correctly
        # regardless of that, with no base_path-threading needed through
        # every template.
        "root": root,
        "site": _site_context(config),
        "person": person,
        "facts": facts,
        "parents": (parents := [resolve_relative(r, people_by_id) for r in person["parents"]]),
        "spouses": (spouses := [resolve_relative(r, people_by_id) for r in person["spouses"]]),
        "children": (children := [resolve_relative(r, people_by_id) for r in person["children"]]),
        "relation_summary": relation_summary(person, parents, spouses, children),
        "vitals": vital_facts(facts),
        "gallery": build_gallery(person, sources_by_id, media_by_id, config.site_dir, root),
        "record_sources": record_sources,
        "other_sources": other_sources,
        "years": years,
        "place": place,
        "seo": {
            "title": f"{person['name']['display']} ({years})"
                     f"{' – ' + place if place else ''} | {config.site_title}",
            "description": _meta_description(person, place, config.site_title),
            "canonical": canonical,
        },
    }
    context["seo"]["schema_json"] = json.dumps(
        _schema_org_person(person, context, canonical, url_prefix), ensure_ascii=False)
    return context


def copy_static(config: "Config") -> None:
    """Static assets (CSS/JS/fonts/vendor) are the same for every page;
    copied wholesale into the site output rather than re-derived."""
    dest = config.site_dir / "static"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(STATIC_DIR, dest)


def render_person_page(person_id: str, config: "Config", *,
                        people: list[dict] | None = None, sources: list[dict] | None = None,
                        media: list[dict] | None = None) -> Path:
    context = person_context(person_id, config, people=people, sources=sources, media=media)
    html = _env().get_template("person.html").render(**context)
    out_dir = config.site_dir / "people" / context["person"]["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_all_person_pages(config: "Config") -> dict:
    """BRIEF.md Milestone 3: "...then the full site" - applying the
    approved one-page design across every published person. Errors are
    collected per-person rather than halting the whole batch: one
    person's unusual data (an edge case the single test page didn't
    happen to have) shouldn't block everyone else's page from building,
    but every failure is still reported, never silently dropped.

    people.json/sources.json/media.json are loaded once here and passed to
    every render_person_page call - each used to reload and re-parse all
    three from disk per person (~0.1s x 1,379 people on the real site, pure
    waste, since none of them change mid-batch)."""
    data_dir = config.data_dir
    people = json.loads((data_dir / "people.json").read_text(encoding="utf-8"))
    sources = json.loads((data_dir / "sources.json").read_text(encoding="utf-8"))
    media = json.loads((data_dir / "media.json").read_text(encoding="utf-8"))
    written: list[Path] = []
    errors: list[tuple[str, str]] = []
    for p in people:
        try:
            written.append(render_person_page(
                p["id"], config, people=people, sources=sources, media=media))
        except Exception as e:  # noqa: BLE001 - deliberately broad; see docstring
            errors.append((p["id"], f"{type(e).__name__}: {e}"))
    return {"total": len(people), "written": len(written), "errors": errors}


# --- surname index -----------------------------------------------------

def surname_groups(people: list[dict]) -> list[dict]:
    """BRIEF.md: "all surnames with counts... people with an empty surname
    go under 'Surname unknown'." Grouped by *slug*, not by the exact
    surname string: the real data has 11 same-surname pairs that differ
    only in case or stray punctuation ("Marshall"/"MARSHALL", "Donaldson"/
    "Donaldson."), which would otherwise slugify to the same URL and
    silently overwrite each other's page. The most common exact casing
    among a group's members is used as the display label."""
    by_key: dict[str, list[dict]] = defaultdict(list)
    label_votes: dict[str, Counter] = defaultdict(Counter)
    for p in people:
        surname = p["name"]["surname"].strip()
        key = slugify(surname) if surname else "unknown"
        by_key[key].append(p)
        label_votes[key][surname or "Surname unknown"] += 1

    groups = []
    for key, members in by_key.items():
        label = label_votes[key].most_common(1)[0][0]
        members.sort(key=lambda p: (p["birth_year"] or 9999, p["name"]["display"]))
        groups.append({"key": key, "label": label, "count": len(members), "people": members})
    groups.sort(key=lambda g: (g["key"] == "unknown", g["label"].lower()))
    return groups


def _source_count(person: dict) -> int:
    """Distinct sources cited across every fact, for the surname listing
    (Will's ask, 6 Oct 2026) - a count of *sources*, not citations, so a
    person with five citations to the same one record still shows "1
    source", not "5"."""
    return len({c["source_id"] for f in person["facts"] for c in f["citations"]})


def render_surname_pages(config: "Config") -> dict:
    people = json.loads((config.data_dir / "people.json").read_text(encoding="utf-8"))
    for p in people:
        p["source_count"] = _source_count(p)
    groups = surname_groups(people)
    url_prefix = f"https://{config.site_domain}{config.site_base_path}".rstrip("/")

    index_html = _env().get_template("surname-index.html").render(
        root="../", site=_site_context(config), groups=groups,
        seo={"title": f"Surnames | {config.site_title}",
             "description": f"Every surname in {config.site_title}, with how many "
                             "people are recorded under each.",
             "canonical": f"{url_prefix}/surnames/"})
    index_dir = config.site_dir / "surnames"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.html").write_text(index_html, encoding="utf-8")

    for group in groups:
        html = _env().get_template("surname.html").render(
            root="../../", site=_site_context(config), group=group,
            seo={"title": f"{group['label']} family | {config.site_title}",
                 "description": f"{group['count']} people with the surname "
                                 f"{group['label']} in {config.site_title}.",
                 "canonical": f"{url_prefix}/surnames/{group['key']}/"})
        out_dir = index_dir / group["key"]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(html, encoding="utf-8")

    return {"surnames": len(groups)}


def _site_context(config: "Config") -> dict:
    return {
        "title": config.site_title,
        "contact_email": config.contact_email or "contact@example.com",
        "contact_is_placeholder": not config.contact_email,
        # Absolute, not root-relative, and deliberately so: Pagefind's
        # Component UI auto-detects its bundle path from the pagefind
        # script's own src attribute, but that detection doesn't cope with
        # our root-relative "../../pagefind/..." paths - it silently falls
        # back to the hardcoded default "/pagefind/", which is wrong for a
        # subpath deployment. base.html's <pagefind-config bundle-path>
        # needs an absolute, domain-root path specifically so it resolves
        # identically regardless of how deep the current page is nested -
        # confirmed against Pagefind's own docs (llms-component-ui.txt),
        # not assumed, 6 Oct 2026.
        "base_path": config.site_base_path,
    }


FEATURED_SURNAME_COUNT = 8


def render_home_page(config: "Config") -> None:
    """BRIEF.md Stage 3: "Home: site intro, featured surnames, search box,
    contact invitation." "Featured" = the most-populous surnames - a
    defensible, non-arbitrary default rather than a hand-picked list,
    since the brief doesn't say how to choose; easy to make configurable
    later if Will wants specific lines featured instead."""
    people = json.loads((config.data_dir / "people.json").read_text(encoding="utf-8"))
    groups = surname_groups(people)
    featured = sorted((g for g in groups if g["key"] != "unknown"),
                       key=lambda g: -g["count"])[:FEATURED_SURNAME_COUNT]

    birth_years = [p["birth_year"] for p in people if p["birth_year"]]
    death_years = [p["death_year"] for p in people if p["death_year"]]
    span = None
    if birth_years and death_years:
        span = (min(birth_years), max(death_years))

    url_prefix = f"https://{config.site_domain}{config.site_base_path}".rstrip("/")
    intro = (
        f"A public record of the {config.site_title} lines - {len(people)} people"
        f"{f', {span[0]}-{span[1]}' if span else ''}, gathered from documents, "
        "photographs and research over the years. If a name here connects to "
        "your own family, I'd love to hear from you."
    )
    html = _env().get_template("home.html").render(
        root="", site=_site_context(config), featured=featured,
        total_people=len(people), total_surnames=len(groups), intro=intro,
        seo={"title": config.site_title,
             "description": f"A public family history record: {len(people)} people "
                             f"from the {config.site_title} lines.",
             "canonical": f"{url_prefix}/"})
    config.site_dir.mkdir(parents=True, exist_ok=True)
    (config.site_dir / "index.html").write_text(html, encoding="utf-8")


def render_about_page(config: "Config") -> None:
    url_prefix = f"https://{config.site_domain}{config.site_base_path}".rstrip("/")
    html = _env().get_template("about.html").render(
        root="../", site=_site_context(config),
        seo={"title": f"About | {config.site_title}",
             "description": f"About {config.site_title}: what's published here, "
                             "and how privacy is handled.",
             "canonical": f"{url_prefix}/about/"})
    out_dir = config.site_dir / "about"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def render_404_page(config: "Config") -> None:
    """Written to build/site/404.html (not nested) since that's the
    conventional path a webserver's error-document directive expects -
    wiring Apache to actually serve it on a 404 is a Stage 5 (publish)
    server-config detail, not done here."""
    html = _env().get_template("404.html").render(
        root="", site=_site_context(config),
        seo={"title": f"Page not found | {config.site_title}", "description": ""})
    config.site_dir.mkdir(parents=True, exist_ok=True)
    (config.site_dir / "404.html").write_text(html, encoding="utf-8")


def build_search_index(config: "Config") -> dict:
    """BRIEF.md: "Search: Pagefind over person pages." Runs the `pagefind`
    CLI (via `python -m pagefind`, so it always resolves to this venv's
    copy, regardless of PATH) as a post-build step over the already-
    rendered site - it crawls `data-pagefind-body` content (set on
    `<main>` in base.html, so every page type is indexed uniformly) and
    writes its self-hosted search index + Component UI assets to
    build/site/pagefind/. Current version (1.5.2) replaced the old
    PagefindUI/pagefind-ui.js default with this Component UI - confirmed
    against the current docs (9 Oct 2026), not assumed from training data.

    Never indexes a hidden person: the privacy filter already ran, before
    any HTML existed, so there is nothing for Pagefind to find that wasn't
    already safe to publish.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pagefind", "--site", str(config.site_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pagefind indexing failed (exit {result.returncode}):\n"
                            f"{result.stdout}\n{result.stderr}")
    return {"pagefind_output": result.stdout}


def render_site(config: "Config") -> dict:
    """Runs every render sub-stage and copies static assets once at the
    end (not per-page - copy_static wipes and recopies the whole static/
    tree, which 1,379 repeats of would make the slowest part of the
    build by far). Search indexing runs last, since it crawls the HTML
    this function just finished writing."""
    person_summary = render_all_person_pages(config)
    surname_summary = render_surname_pages(config)
    render_home_page(config)
    render_about_page(config)
    render_404_page(config)
    copy_static(config)
    search_summary = build_search_index(config)
    return {**person_summary, **surname_summary, **search_summary}


def render_preview_html(person_id: str, config: "Config") -> tuple[str, dict]:
    """Design-proposal-review helper, not part of the real build: renders
    with `root=""` (flat, no "../../") for hosts that serve the page at
    their own root - e.g. a published Artifact preview - and returns the
    html plus the full context (so the caller can enumerate exactly which
    media files the page references, for building a file manifest)."""
    context = person_context(person_id, config, root="")
    html = _env().get_template("person.html").render(**context)
    return html, context

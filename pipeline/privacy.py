"""Privacy classification (BRIEF.md, Stage 1: "Privacy (in extract, before
anything is written)"). Classifies every person into exactly one of four
groups (first match wins), then applies the generation cutoff on top to
decide the final publish/hide call. See BRIEF.md's Privacy section for the
rules this implements line by line.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

from . import medialink
from . import relationships as rel
from .dates import GedcomDate, parse_date
from .gedcom import Gedcom, Node

DEATH_TAGS = ("DEAT", "BURI", "CREM", "PROB")
EVENT_TAGS = ("BIRT", "DEAT", "BURI", "CREM", "PROB", "BAPM", "CHR", "RESI",
              "MARR", "EVEN", "OCCU", "NATU", "NATI", "IMMI", "ADOP", "_MILT",
              "RELI")
_FTM_PRIVATE_RESN = ("confidential", "privacy", "locked")


@dataclass
class Thresholds:
    """Years for each rule (BRIEF.md defaults, all configurable)."""

    event_years: int = 100
    child_years: int = 100
    parent_years: int = 175
    sibling_years: int = 130
    recent_years: int = 100
    recent_child_years: int = 85


@dataclass
class PersonPrivacy:
    xref: str
    group: int                 # 1 (known living) .. 4 (undetermined)
    group_reason: str
    generation: int | None     # None if not connected to the home person
    published: bool
    publish_reason: str        # why published, or why hidden
    # Set only when group_reason is the "own dated event" or "child with a
    # dated event" rule: the specific qualifying date (latest bound), for
    # the privacy report's centenarian check (BRIEF.md: "anyone published
    # only by the event or child rule whose qualifying date is within the
    # last 110 years").
    evidence_date: date | None = None


@dataclass
class PrivacyResult:
    people: dict[str, PersonPrivacy]
    unparseable_dates: list[tuple[str, str, str]] = field(default_factory=list)
    generation_conflicts: list[str] = field(default_factory=list)

    @property
    def published_xrefs(self) -> set[str]:
        return {x for x, p in self.people.items() if p.published}

    @property
    def hidden_xrefs(self) -> set[str]:
        return {x for x, p in self.people.items() if not p.published}


def years_ago(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:
        # d was Feb 29 and (d.year - n) isn't a leap year.
        return d.replace(month=2, day=28, year=d.year - n)


def _is_ftm_private(rec: Node) -> bool:
    """BRIEF.md group 1: 'anyone marked private in FTM, if that's visible in
    the export.' This export carries no such marker (no RESN/_PRIV anywhere -
    see docs/phase0-remaining.md), so this path is untested against real
    data; it's covered by the synthetic fixture instead."""
    resn = rec.first("RESN")
    if resn and resn.value.strip().lower() in _FTM_PRIVATE_RESN:
        return True
    return rec.first("_PRIV") is not None


def classify(
    gedcom: Gedcom,
    *,
    home_person: str,
    min_generation: int = 2,
    thresholds: Thresholds | None = None,
    living_overrides: frozenset[str] = frozenset(),
    deceased_overrides: frozenset[str] = frozenset(),
    as_of: date | None = None,
) -> PrivacyResult:
    thresholds = thresholds or Thresholds()
    as_of = as_of or date.today()

    r = rel.build(gedcom)
    indis, fams = r.indis, r.fams
    parents, children, spouses = r.parents, r.children, r.spouses
    child_rel, fam_of_child = r.child_rel, r.fam_of_child

    unparseable: list[tuple[str, str, str]] = []

    def person_events(px: str) -> list[tuple[str, GedcomDate]]:
        """Every dated event on this person, including their own family
        facts (e.g. MARR), by tag."""
        rec = indis[px]
        out: list[tuple[str, GedcomDate]] = []

        def collect(owner_rec: Node, path_prefix: str):
            for n in owner_rec.children:
                if n.tag not in EVENT_TAGS:
                    continue
                d = n.first("DATE")
                if d is None or not d.value.strip():
                    continue
                gd = parse_date(d.value)
                if gd.parsed:
                    out.append((n.tag, gd))
                else:
                    unparseable.append((px, f"{path_prefix}{n.tag}", d.value))

        collect(rec, "")
        for fs in rec.all("FAMS"):
            fam = fams.get(fs.value)
            if fam is not None:
                collect(fam, f"{fs.value}.")
        return out

    events_by_person = {px: person_events(px) for px in indis}

    def birth_bounds(px: str) -> tuple[date | None, date | None]:
        for tag, gd in events_by_person[px]:
            if tag == "BIRT":
                return gd.earliest, gd.latest
        return None, None

    def has_death_record(px: str) -> bool:
        return indis[px].first(*DEATH_TAGS) is not None

    cut_event = years_ago(as_of, thresholds.event_years)
    cut_child = years_ago(as_of, thresholds.child_years)
    cut_parent = years_ago(as_of, thresholds.parent_years)
    cut_sibling = years_ago(as_of, thresholds.sibling_years)
    cut_recent = years_ago(as_of, thresholds.recent_years)
    cut_recent_child = years_ago(as_of, thresholds.recent_child_years)

    group: dict[str, int] = {}
    group_reason: dict[str, str] = {}
    evidence_date: dict[str, date] = {}

    for px, rec in indis.items():
        # --- Group 1: known living, always hidden. First match wins. ---
        if px in living_overrides:
            group[px], group_reason[px] = 1, "overrides/living.txt"
            continue
        if _is_ftm_private(rec):
            group[px], group_reason[px] = 1, "marked private in FTM"
            continue

        # --- overrides/deceased.txt: force group 2 ---
        if px in deceased_overrides:
            group[px], group_reason[px] = 2, "overrides/deceased.txt"
            continue

        # --- Group 2: deceased, published. Any one rule is enough. ---
        why: str | None = None
        own_dates = [gd.latest for _, gd in events_by_person[px]
                     if gd.latest and gd.latest < cut_event]
        child_dates = [gd.latest for c in children[px] for _, gd in events_by_person[c]
                        if gd.latest and gd.latest < cut_child]
        if has_death_record(px):
            why = "death/burial/cremation/probate record"
        elif own_dates:
            why = f"own dated event more than {thresholds.event_years}y ago"
            evidence_date[px] = max(own_dates)
        elif child_dates:
            why = f"child with a dated event more than {thresholds.child_years}y ago"
            evidence_date[px] = max(child_dates)
        else:
            if any((pl := birth_bounds(p)[1]) and pl < cut_parent
                   for p in parents[px]):
                why = f"parent born more than {thresholds.parent_years}y ago"
            else:
                for fx in fam_of_child[px]:
                    fam = fams[fx]
                    if not (fam.val("HUSB") and fam.val("WIFE")):
                        continue
                    if child_rel.get((px, fx)) != ("Natural", "Natural"):
                        continue
                    sib_hit = False
                    for c in fam.all("CHIL"):
                        sx = c.value
                        if sx == px or sx not in indis:
                            continue
                        if child_rel.get((sx, fx)) != ("Natural", "Natural"):
                            continue
                        _, sl = birth_bounds(sx)
                        if sl and sl < cut_sibling:
                            sib_hit = True
                            break
                    if sib_hit:
                        why = f"full sibling born more than {thresholds.sibling_years}y ago"
                        break

        if why:
            group[px], group_reason[px] = 2, why
            continue

        # --- Group 3: likely living, hidden. ---
        if any(gd.earliest and gd.earliest >= cut_recent
               for _, gd in events_by_person[px]):
            group[px] = 3
            group_reason[px] = f"own dated event within {thresholds.recent_years}y"
            continue
        if any(tag in ("BIRT", "CHR") and gd.earliest and gd.earliest >= cut_recent_child
               for c in children[px] for tag, gd in events_by_person[c]):
            group[px] = 3
            group_reason[px] = f"child born/christened within {thresholds.recent_child_years}y"
            continue

        # --- Group 4: undetermined. ---
        group[px], group_reason[px] = 4, "no evidence either way"

    # --- Generation: breadth-first walk from the home person (first visit
    # wins); the brief's "where two paths disagree, take the younger" means
    # recording any later-seen value and keeping the minimum, NOT globally
    # re-propagating - a spouse edge (weight 0) makes that diverge, since it
    # lets a walk re-enter the parent/child ladder at the wrong rung. See
    # docs/phase0-remaining.md for how this was found.
    #
    # neighbours() must return a DETERMINISTIC order. parents/children/
    # spouses are built as sets, and Python randomizes str hash seeds per
    # process by default, so iterating a set directly gives a different
    # order every run - which changes which path "wins" first-visit on a
    # conflict, which changed the reported conflict count from run to run
    # on identical input (seen as 3/5/12 across runs while prototyping this).
    # On this data it never flipped a publish decision (every conflicting
    # person's candidate generations are all >= min_generation either way),
    # but that's luck, not a guarantee - a privacy classifier must give the
    # same answer every time for the same input. Sorting by xref fixes it. ---
    def neighbours(p: str):
        return ([(x, 1) for x in sorted(parents[p])] +
                [(x, -1) for x in sorted(children[p])] +
                [(x, 0) for x in sorted(spouses[p])])

    gen: dict[str, int] = {}
    seen_vals: dict[str, set[int]] = defaultdict(set)
    if home_person in indis:
        gen[home_person] = 0
        seen_vals[home_person].add(0)
        q = deque([home_person])
        while q:
            p = q.popleft()
            for nxt, delta in neighbours(p):
                v = gen[p] + delta
                seen_vals[nxt].add(v)
                if nxt not in gen:
                    gen[nxt] = v
                    q.append(nxt)
    generation_conflicts = sorted(p for p in gen if len(seen_vals[p]) > 1)
    gen_final = {p: min(seen_vals[p]) for p in gen}

    # --- Final publish/hide call. BRIEF.md: groups 1 and 3 are hidden
    # outright ("groups 1-3 apply as written" at every generation band, and
    # "either way" for the ones not connected to the home person) - the
    # group's own reason explains it fully, with no generation involved.
    # Group 2 always publishes, everywhere, connected or not. Only group 4
    # (undetermined) is actually decided by generation/connectivity. ---
    people: dict[str, PersonPrivacy] = {}
    for px in indis:
        g = group[px]
        gp = gen_final.get(px)

        if px == home_person:
            published, why = False, "home person (always hidden)"
        elif g in (1, 3):
            published, why = False, group_reason[px]
        elif g == 2:
            published, why = True, group_reason[px]
        elif gp is None:
            published, why = False, f"{group_reason[px]}; not connected to home person"
        elif gp < min_generation:
            published, why = False, (
                f"{group_reason[px]}; generation {gp} < min_generation {min_generation}")
        else:
            published, why = True, group_reason[px]

        people[px] = PersonPrivacy(px, g, group_reason[px], gp, published, why,
                                    evidence_date=evidence_date.get(px))

    return PrivacyResult(
        people=people,
        unparseable_dates=unparseable,
        generation_conflicts=generation_conflicts,
    )


def withheld_media(
    gedcom: Gedcom,
    hidden_xrefs: set[str],
    allow_overrides: frozenset[str] = frozenset(),
) -> dict[str, set[str]]:
    """BRIEF.md: "media linked to any living person is excluded by default,
    even if it's also linked to a published person. overrides/media-allow.txt
    publishes specific items anyway." Returns {obje_xref: {hidden xrefs that
    link to it}} for every withheld item not in allow_overrides."""
    links = medialink.build(gedcom)
    withheld: dict[str, set[str]] = {}
    for obje_xref, link in links.items():
        if obje_xref in allow_overrides:
            continue
        hit = link.people & hidden_xrefs
        if hit:
            withheld[obje_xref] = hit
    return withheld

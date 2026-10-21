"""Milestone 1a: tree lint (BRIEF.md "Tree lint"). A read-only report of
tree problems that affect the site. Fixes are made in FTM, never by this
pipeline - this module only ever reads the GEDCOM and (for the media check)
looks at file names/hashes on disk. Reuses the extract stage's parser and
the privacy classifier (for generation/connectivity), rather than
duplicating either.

Writes build/reports/lint.md (gitignored - it names living people). Each
check is independent and reports a count, "so progress is visible" as the
brief puts it; FTM's own tools (Find Duplicate People, the Data Errors
report, place resolution) are not duplicated here.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import relationships as rel
from .config import Config
from .dates import GedcomDate, parse_date
from .extract import parse_name
from .gedcom import Gedcom, Node
from .mediapath import resolve_media_path
from .privacy import PrivacyResult, classify

DEATH_LINT_TAGS = ("DEAT", "BURI")  # BRIEF.md says "Death/Burial" here,
# deliberately narrower than privacy.DEATH_TAGS (which also counts CREM/PROB)

PLACEHOLDER_SURNAMES = {"none", "unknown", "?"}

# A modest nickname-equivalence table (BRIEF.md names only two examples -
# Bob/Robert, Doug/Douglas - as a hint list, not a merge; this is a small,
# documented best-effort, not an attempt at a complete nickname dictionary).
_NICKNAME_GROUPS = [
    {"robert", "rob", "bob", "bobby", "bert"},
    {"douglas", "doug"},
    {"william", "will", "bill", "billy", "liam"},
    {"richard", "rich", "rick", "dick"},
    {"james", "jim", "jimmy", "jamie"},
    {"john", "jack", "johnny"},
    {"elizabeth", "liz", "beth", "betty", "eliza", "lisa"},
    {"margaret", "maggie", "meg", "peggy", "marge"},
    {"charles", "charlie", "chuck"},
    {"edward", "ed", "eddie", "ted", "teddy"},
    {"katherine", "catherine", "kate", "katie", "cathy", "kathy"},
    {"thomas", "tom", "tommy"},
    {"michael", "mike", "mickey"},
    {"alexander", "alex", "sandy"},
    {"benjamin", "ben", "benny"},
]
_NICKNAME_CANON = {
    name: sorted(group)[0] for group in _NICKNAME_GROUPS for name in group
}


@dataclass
class LintSection:
    title: str
    items: list[tuple[str, str]] = field(default_factory=list)  # (xref_or_id, description)
    note: str | None = None  # optional one-line context shown under the heading

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class LintResult:
    sections: list[LintSection] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(s.count for s in self.sections)


def _source_title(gedcom: Gedcom, sour_xref: str) -> str:
    rec = gedcom.by_xref.get(sour_xref)
    return (rec.val("TITL") or "").strip() if rec is not None else ""


def _year_bounds(gd: GedcomDate) -> tuple[int | None, int | None]:
    return (gd.earliest.year if gd.earliest else None,
            gd.latest.year if gd.latest else None)


def _event_year_bounds(rec: Node, *tags: str) -> tuple[int | None, int | None]:
    """The first dated occurrence of any of `tags` directly on `rec`."""
    n = rec.first(*tags)
    if n is None:
        return None, None
    d = n.first("DATE")
    if d is None or not d.value.strip():
        return None, None
    gd = parse_date(d.value)
    return _year_bounds(gd) if gd.parsed else (None, None)


# --- individual checks -----------------------------------------------------

def check_uncited_recent_death_facts(r: rel.Relationships, priv: PrivacyResult,
                                      min_generation: int) -> LintSection:
    """BRIEF.md: "any Death/Burial fact in the parents' generation or
    younger with no citation" - scoped to recent generations because that's
    exactly the evidence the generation cutoff relies on to publish someone.
    Kept separate from the "Ancestry Family Trees only" case (see
    check_ancestry_only_death_citations): confirmed with Will (8 Oct 2026)
    that BRIEF.md's "5 such facts tree-wide" meant the Ancestry-only case
    specifically, not this one - this is a much larger, genuinely different
    backlog (mostly recent deaths nobody has sourced yet)."""
    out = []
    for px, p in priv.people.items():
        if p.generation is None or p.generation >= min_generation:
            continue
        rec = r.indis[px]
        for n in rec.children:
            if n.tag not in DEATH_LINT_TAGS:
                continue
            citations = [c for c in n.all("SOUR") if c.value.startswith("@")]
            if not citations:
                out.append((px, f"{n.tag} (generation {p.generation}) has no citation"))
    return LintSection("Death/burial facts in recent generations with no citation at all", out)


def check_ancestry_only_death_citations(gedcom: Gedcom, r: rel.Relationships) -> LintSection:
    """BRIEF.md: "...or whose only citation is 'Ancestry Family Trees'
    (on 5 Oct: 5 such facts tree-wide)." Deliberately tree-wide, not
    generation-restricted, per Will's confirmation (8 Oct 2026) - this is a
    general sourcing-quality concern ("someone else's tree" is not a
    record), not specifically a recent-generations publishing concern."""
    out = []
    for px, rec in r.indis.items():
        for n in rec.children:
            if n.tag not in DEATH_LINT_TAGS:
                continue
            citations = [c for c in n.all("SOUR") if c.value.startswith("@")]
            if citations and all(_source_title(gedcom, c.value) == "Ancestry Family Trees"
                                  for c in citations):
                out.append((px, f"{n.tag} is cited only to 'Ancestry Family Trees'"))
    return LintSection("Death/burial facts cited only to 'Ancestry Family Trees' (tree-wide)", out)


def check_undated_death_facts(r: rel.Relationships) -> LintSection:
    out = []
    for px, rec in r.indis.items():
        for n in rec.children:
            if n.tag in DEATH_LINT_TAGS and n.first("DATE") is None:
                out.append((px, f"{n.tag} has no date"))
    return LintSection("Death/burial facts with no date", out)


def check_non_conforming_dates(r: rel.Relationships, priv: PrivacyResult) -> LintSection:
    """Reuses privacy.classify()'s own unparseable-date tracking rather than
    re-scanning the tree, since it already walks every dated fact a person
    has (their own and their family's)."""
    out = [(px, f"{fact} `{raw}`") for px, fact, raw in priv.unparseable_dates]
    return LintSection("Dates that aren't valid GEDCOM", out)


def check_placeholder_names(r: rel.Relationships) -> LintSection:
    out = []
    for px, rec in r.indis.items():
        for n in rec.all("NAME"):
            raw = n.value
            if raw.count("/") < 2:
                continue  # a different problem - see check_inconsistent_names
            info = parse_name(raw)
            if not info["surname"]:
                out.append((px, f"empty surname: {raw!r}"))
            elif info["surname"].strip().lower() in PLACEHOLDER_SURNAMES:
                out.append((px, f"placeholder surname: {raw!r}"))
            elif not info["given"]:
                out.append((px, f"surname-only record: {raw!r}"))
    return LintSection("Empty or placeholder surnames", out)


def check_inconsistent_names(r: rel.Relationships) -> LintSection:
    out = []
    for px, rec in r.indis.items():
        for n in rec.all("NAME"):
            raw = n.value
            reasons = []
            if raw.count("/") < 2:
                reasons.append("'Given, Nick' with no slashes" if "," in raw
                                else "no slashes around the surname")
            if re.search(r'"[^"]+"', raw):
                reasons.append("nickname in quotes")
            if re.search(r"\(rev\.?\)", raw, re.IGNORECASE):
                reasons.append("'(Rev)'-style suffix")
            elif re.search(r"\([^)]+\)", raw):
                reasons.append("nickname in parentheses")
            if "+" in raw:
                reasons.append("stray '+'")
            if reasons:
                out.append((px, f"{raw!r}: " + "; ".join(reasons)))
    return LintSection("Nicknames written inconsistently / stray characters", out)


def check_likely_duplicates(r: rel.Relationships) -> LintSection:
    """BRIEF.md: "same surname, compatible given names ..., same family.
    These are a hint list for FTM's own duplicate finder, not merges." """
    out = []
    for fx, fam in r.fams.items():
        kids = [c.value for c in fam.all("CHIL") if c.value in r.indis]
        infos: dict[str, dict] = {}
        for k in kids:
            name_node = r.indis[k].first("NAME")
            if name_node is not None:
                infos[k] = parse_name(name_node.value)
        for i, a in enumerate(kids):
            if a not in infos:
                continue
            for b in kids[i + 1:]:
                if b not in infos:
                    continue
                sa = infos[a]["surname"].strip().lower()
                sb = infos[b]["surname"].strip().lower()
                if not sa or sa != sb:
                    continue
                ca = _canon_given(infos[a]["given"])
                cb = _canon_given(infos[b]["given"])
                if ca and ca == cb:
                    out.append((a, f"possibly the same as `{b}` - family {fx}, surname "
                                    f"{infos[a]['surname']!r}, given names "
                                    f"{infos[a]['given']!r} / {infos[b]['given']!r}"))
    return LintSection("Likely duplicates (same family, compatible names) - a hint list only", out)


def _canon_given(given: str) -> str:
    token = given.strip().split()[0].lower() if given.strip() else ""
    token = re.sub(r"[\"'(),.]", "", token)
    return _NICKNAME_CANON.get(token, token)


def check_generation_conflicts(priv: PrivacyResult) -> LintSection:
    out = [(px, f"used generation {priv.people[px].generation} (take the younger)")
           for px in priv.generation_conflicts]
    return LintSection("People whose generation differs by path", out)


def check_unconnected_people(priv: PrivacyResult) -> LintSection:
    out = [(px, f"group {p.group}, {'published' if p.published else 'hidden'}")
           for px, p in priv.people.items() if p.generation is None]
    return LintSection("People not connected to the home person", out)


def check_impossible_ordering(r: rel.Relationships, *, min_parent_age: int,
                               max_mother_age: int, min_marriage_age: int) -> LintSection:
    """Flags only what's impossible under EVERY reading of an uncertain date
    (e.g. ABT/BET), to avoid noise from approximate dates - BRIEF.md says
    "report only", so false positives are worse than a missed one here."""
    out = []

    def birth_years(px: str) -> tuple[int | None, int | None]:
        rec = r.indis.get(px)
        return _event_year_bounds(rec, "BIRT") if rec is not None else (None, None)

    for px, rec in r.indis.items():
        be, _ = birth_years(px)
        _, dl = _event_year_bounds(rec, *DEATH_LINT_TAGS, "CREM", "PROB")
        if be is not None and dl is not None and dl < be:
            out.append((px, f"death (by {dl}) before birth (from {be})"))

    for fx, fam in r.fams.items():
        husb, wife = fam.val("HUSB"), fam.val("WIFE")
        for c in fam.all("CHIL"):
            cx = c.value
            if cx not in r.indis:
                continue
            ce, cl = birth_years(cx)
            if ce is None and cl is None:
                continue
            for parent, label in ((husb, "father"), (wife, "mother")):
                if not parent or parent not in r.indis:
                    continue
                pe, pl = birth_years(parent)
                if pe is not None and cl is not None and (cl - pe) < min_parent_age:
                    out.append((cx, f"born when {label} `{parent}` was at most "
                                     f"{cl - pe} (< {min_parent_age})"))
                if label == "mother" and pl is not None and ce is not None \
                        and (ce - pl) > max_mother_age:
                    out.append((cx, f"born when mother `{parent}` was at least "
                                     f"{ce - pl} (> {max_mother_age})"))

        for m in fam.all("MARR"):
            d = m.first("DATE")
            if d is None or not d.value.strip():
                continue
            gd = parse_date(d.value)
            if not gd.parsed:
                continue
            _, ml = _year_bounds(gd)
            if ml is None:
                continue
            for spouse in (husb, wife):
                if not spouse or spouse not in r.indis:
                    continue
                be, _ = birth_years(spouse)
                if be is not None and (ml - be) < min_marriage_age:
                    out.append((spouse, f"married by age {ml - be} "
                                         f"(< {min_marriage_age}) in family {fx}"))

    return LintSection("Impossible fact ordering (configurable thresholds)", out)


def check_obituary_attachment(gedcom: Gedcom) -> LintSection:
    """BRIEF.md: "objects whose title or description says 'obit...' attached
    to someone other than the subject." Heuristic: the attached person's own
    name should appear somewhere in the media's title/description."""
    out = []
    for n in gedcom.walk_all():
        if n.tag not in ("OBJE", "_PHOTO") or not n.value.startswith("@"):
            continue
        obje = gedcom.by_xref.get(n.value)
        if obje is None:
            continue
        file_node = obje.first("FILE")
        if file_node is None:
            continue
        text = f"{file_node.val('TITL') or ''} {file_node.val('_TEXT') or ''}"
        if "obit" not in text.lower():
            continue
        chain: list[Node] = []
        p = n.parent
        while p is not None:
            chain.append(p)
            p = p.parent
        if not chain or chain[-1].tag != "INDI":
            continue
        person = chain[-1]
        name_node = person.first("NAME")
        if name_node is None:
            continue
        info = parse_name(name_node.value)
        person_tokens = {t.lower() for t in f"{info['given']} {info['surname']}".split() if t}
        title_tokens = {t.lower() for t in re.findall(r"[A-Za-z']+", text)}
        if person_tokens and not (person_tokens & title_tokens):
            out.append((person.xref, f"{n.value} ({text.strip()!r}) doesn't mention this person's name"))
    return LintSection("Obituary media possibly attached to someone other than its subject", out)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_unreferenced_media(gedcom: Gedcom, media_dir: Path) -> LintSection:
    """BRIEF.md: "media files on disk that no OBJE references (excluding
    byte-identical copies)." Read-only: files are only hashed, never moved
    or written to."""
    if not media_dir.exists():
        return LintSection("Media files on disk that no OBJE references "
                            f"(skipped: {media_dir} not found)", [])

    referenced: set[Path] = set()
    for o in gedcom.of_type("OBJE"):
        f = o.first("FILE")
        if f is None:
            continue
        resolved = resolve_media_path(media_dir, f.value)
        if resolved is not None and resolved.exists():
            referenced.add(resolved.resolve())

    all_files = [p.resolve() for p in media_dir.rglob("*") if p.is_file()]
    unreferenced = [p for p in all_files if p not in referenced]
    if not unreferenced:
        return LintSection("Media files on disk that no OBJE references", [])

    referenced_hashes = {_sha256(p) for p in referenced}
    out = []
    for p in unreferenced:
        if _sha256(p) not in referenced_hashes:
            out.append((str(p), "not referenced by any OBJE, and not a byte-identical "
                                 "copy of a referenced file"))
    return LintSection("Media files on disk that no OBJE references "
                        "(excluding byte-identical copies)", out)


# --- orchestration -----------------------------------------------------

def run(config: Config, *, as_of=None) -> LintResult:
    gedcom = Gedcom(config.resolve_gedcom())
    r = rel.build(gedcom)
    priv = classify(
        gedcom,
        home_person=config.home_person,
        min_generation=config.min_generation,
        thresholds=config.thresholds,
        as_of=as_of,
    )

    sections = [
        check_uncited_recent_death_facts(r, priv, config.min_generation),
        check_ancestry_only_death_citations(gedcom, r),
        check_undated_death_facts(r),
        check_non_conforming_dates(r, priv),
        check_placeholder_names(r),
        check_inconsistent_names(r),
        check_likely_duplicates(r),
        check_generation_conflicts(priv),
        check_unconnected_people(priv),
        check_impossible_ordering(
            r,
            min_parent_age=config.lint_min_parent_age,
            max_mother_age=config.lint_max_mother_age,
            min_marriage_age=config.lint_min_marriage_age,
        ),
        check_obituary_attachment(gedcom),
        check_unreferenced_media(gedcom, config.media_dir),
    ]
    result = LintResult(sections)
    write_report(result, config.reports_dir / "lint.md")
    return result


def write_report(result: LintResult, path: Path) -> None:
    lines = ["# Tree lint report", "",
             "Read-only. Fixes are made in FTM, never here. Re-run after each export "
             "so this list shrinks.", "",
             f"**{result.total} items across {len(result.sections)} checks.**", ""]
    for section in result.sections:
        lines.append(f"## {section.title} ({section.count})")
        lines.append("")
        if section.note:
            lines.append(f"*{section.note}*")
            lines.append("")
        if not section.items:
            lines.append("(none)")
        else:
            for ident, desc in section.items:
                lines.append(f"- `{ident}` {desc}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")

"""Tests for pipeline.privacy (BRIEF.md Testing: "privacy determination: no
dates, each group and their precedence, each deceased rule, half vs full vs
adopted siblings, each qualifier in both directions, boundary years, the
generation rule (direct ancestor vs collateral, an undated direct ancestor,
an unconnected person, conflicting generation paths, and the home person),
and all overrides").
"""
from __future__ import annotations

from datetime import date

from pipeline.gedcom import Gedcom
from pipeline.privacy import Thresholds, classify, withheld_media

AS_OF = date(2026, 10, 5)
DEFAULT_THRESHOLDS = Thresholds()  # event/child 100, parent 175, sibling 130,
                                    # recent 100, recent_child 85 - BRIEF.md defaults

HEAD = "0 HEAD\n1 CHAR UTF-8\n1 GEDC\n2 VERS 5.5.1\n"
TRLR = "0 TRLR\n"


def indi(xref, name="Test /Person/", sex="M", events=(), famc=None, fams=(),
         resn=None, extra=()):
    """events: iterable of (tag, date_str_or_None)."""
    lines = [f"0 {xref} INDI", f"1 NAME {name}", f"1 SEX {sex}"]
    for tag, date_str in events:
        lines.append(f"1 {tag} Y")
        if date_str is not None:
            lines.append(f"2 DATE {date_str}")
    if famc:
        lines.append(f"1 FAMC {famc}")
    for f in fams:
        lines.append(f"1 FAMS {f}")
    if resn:
        lines.append(f"1 RESN {resn}")
    for tag, val in extra:
        lines.append(f"1 {tag} {val}")
    return "\n".join(lines) + "\n"


def fam(xref, husb=None, wife=None, children=()):
    """children: iterable of (xref, frel, mrel)."""
    lines = [f"0 {xref} FAM"]
    if husb:
        lines.append(f"1 HUSB {husb}")
    if wife:
        lines.append(f"1 WIFE {wife}")
    for c, frel, mrel in children:
        lines.append(f"1 CHIL {c}")
        if frel:
            lines.append(f"2 _FREL {frel}")
        if mrel:
            lines.append(f"2 _MREL {mrel}")
    return "\n".join(lines) + "\n"


def build(*parts: str) -> Gedcom:
    return Gedcom.from_text(HEAD + "".join(parts) + TRLR)


def classify_with(g: Gedcom, home="@I1@", **kw):
    kw.setdefault("thresholds", DEFAULT_THRESHOLDS)
    kw.setdefault("as_of", AS_OF)
    return classify(g, home_person=home, **kw)


# --- no dates / no evidence --------------------------------------------

def test_no_dates_at_all_is_undetermined():
    g = build(indi("@I1@"))
    r = classify_with(g)
    p = r.people["@I1@"]
    assert p.group == 4
    assert p.group_reason == "no evidence either way"


# --- group precedence: group 1 beats everything else --------------------

def test_living_override_beats_a_death_record():
    g = build(indi("@I1@", events=[("DEAT", "1990")]))
    r = classify_with(g, living_overrides=frozenset({"@I1@"}))
    p = r.people["@I1@"]
    assert p.group == 1
    assert p.group_reason == "overrides/living.txt"


def test_ftm_private_marker_beats_a_death_record():
    g = build(indi("@I1@", events=[("DEAT", "1990")], resn="confidential"))
    r = classify_with(g)
    p = r.people["@I1@"]
    assert p.group == 1
    assert p.group_reason == "marked private in FTM"


def test_deceased_override_applies_when_no_other_evidence():
    g = build(indi("@I1@"))
    r = classify_with(g, deceased_overrides=frozenset({"@I1@"}))
    p = r.people["@I1@"]
    assert p.group == 2
    assert p.group_reason == "overrides/deceased.txt"


# --- each deceased (group 2) rule ---------------------------------------

def test_death_record_with_no_date_is_still_group_2():
    g = build(indi("@I1@", events=[("DEAT", None)]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "death" in p.group_reason


def test_burial_cremation_probate_all_count_as_a_death_record():
    for tag in ("BURI", "CREM", "PROB"):
        g = build(indi("@I1@", events=[(tag, "1990")]))
        p = classify_with(g).people["@I1@"]
        assert p.group == 2, f"{tag} should count as a death record"


def test_own_dated_event_over_100_years_triggers_group_2_without_a_death_record():
    g = build(indi("@I1@", events=[("RESI", "1900")]))  # no DEAT/BURI/etc
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "own dated event" in p.group_reason


def test_own_birth_date_alone_counts_as_an_own_event():
    g = build(indi("@I1@", events=[("BIRT", "BET 1700 AND 1710")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "own dated event" in p.group_reason


def test_child_with_dated_event_over_100_years_triggers_group_2():
    g = build(
        indi("@I1@", fams=["@F1@"]),
        indi("@I2@", famc="@F1@", events=[("BIRT", "1900")]),
        fam("@F1@", husb="@I1@", children=[("@I2@", "Natural", "Natural")]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "child with a dated event" in p.group_reason


def test_parent_born_over_175_years_ago_triggers_group_2():
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I2@", events=[("BIRT", "1840")], fams=["@F1@"]),
        fam("@F1@", husb="@I2@", children=[("@I1@", "Natural", "Natural")]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "parent born" in p.group_reason


def test_full_sibling_born_over_130_years_ago_triggers_group_2():
    # BRIEF.md: "same two parents" - the family needs both HUSB and WIFE
    # known, or the sibling rule can't apply at all.
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I2@", famc="@F1@", events=[("BIRT", "1880")]),
        fam("@F1@", husb="@I9@", wife="@I9W@", children=[
            ("@I1@", "Natural", "Natural"),
            ("@I2@", "Natural", "Natural"),
        ]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "full sibling" in p.group_reason


def test_half_sibling_adopted_on_the_siblings_side_does_not_count():
    """The 'sibling' isn't a full/Natural-Natural child of the shared family,
    so the rule must not fire even though px's own side is Natural/Natural."""
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I2@", famc="@F1@", events=[("BIRT", "1880")]),
        fam("@F1@", husb="@I9@", wife="@I9W@", children=[
            ("@I1@", "Natural", "Natural"),
            ("@I2@", "Adopted", "Adopted"),
        ]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group != 2 or "full sibling" not in p.group_reason


def test_adopted_child_is_not_a_full_sibling_of_their_own_siblings():
    """px is the Adopted one; even though their sibling is old and Natural,
    px was not a Natural/Natural child of that family, so the rule can't
    apply to px via this family."""
    g = build(
        indi("@I1@", famc="@F1@"),  # adopted
        indi("@I2@", famc="@F1@", events=[("BIRT", "1880")]),  # natural, old
        fam("@F1@", husb="@I9@", wife="@I9W@", children=[
            ("@I1@", "Adopted", "Adopted"),
            ("@I2@", "Natural", "Natural"),
        ]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group != 2 or "full sibling" not in p.group_reason


# --- group 3 (likely living) --------------------------------------------

def test_own_recent_event_triggers_group_3():
    g = build(indi("@I1@", events=[("RESI", "AFT 2000")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 3
    assert "own dated event within" in p.group_reason


def test_recent_child_birth_triggers_group_3():
    g = build(
        indi("@I1@", fams=["@F1@"]),
        indi("@I2@", famc="@F1@", events=[("BIRT", "AFT 2010")]),
        fam("@F1@", husb="@I1@", children=[("@I2@", "Natural", "Natural")]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group == 3
    assert "child born/christened" in p.group_reason


def test_recent_child_christening_also_counts():
    g = build(
        indi("@I1@", fams=["@F1@"]),
        indi("@I2@", famc="@F1@", events=[("CHR", "AFT 2010")]),
        fam("@F1@", husb="@I1@", children=[("@I2@", "Natural", "Natural")]),
    )
    p = classify_with(g).people["@I1@"]
    assert p.group == 3


# --- qualifiers in both directions ---------------------------------------

def test_bef_never_counts_as_recent_no_matter_how_recent_the_year():
    """BEF's earliest bound is always None, so it can never satisfy the
    'likely living' test (which uses earliest) - even with a year that
    would otherwise look recent."""
    g = build(indi("@I1@", events=[("RESI", "BEF 2020")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 4  # not group 3: BEF never counts as "recent"


def test_aft_counts_as_recent():
    g = build(indi("@I1@", events=[("RESI", "AFT 2000")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 3


def test_aft_never_counts_for_the_deceased_test():
    """AFT's latest bound is always None, so an open-ended-into-the-future
    date can never satisfy the 'deceased' test (which uses latest)."""
    g = build(indi("@I1@", events=[("RESI", "AFT 1700")]))
    p = classify_with(g).people["@I1@"]
    assert p.group != 2


def test_bef_counts_for_the_deceased_test_when_old_enough():
    g = build(indi("@I1@", events=[("RESI", "BEF 1900")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 2
    assert "own dated event" in p.group_reason


def test_bet_and_uses_the_later_date_for_the_deceased_test():
    g = build(indi("@I1@", events=[("RESI", "BET 1700 AND 1710")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 2


# --- boundary years --------------------------------------------------------

def test_exactly_at_the_cutoff_day_counts_as_recent_not_old():
    """as_of=2026-10-05, event_years=100 -> cutoff=1926-10-05. A latest bound
    exactly ON the cutoff is not STRICTLY older than 100 years, so it must
    not satisfy the deceased rule - and does satisfy the >= recent test."""
    g = build(indi("@I1@", events=[("BIRT", "5 OCT 1926")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 3
    assert "own dated event within" in p.group_reason


def test_one_day_past_the_cutoff_counts_as_old():
    g = build(indi("@I1@", events=[("BIRT", "4 OCT 1926")]))
    p = classify_with(g).people["@I1@"]
    assert p.group == 2


# --- unparseable dates ------------------------------------------------------

def test_unparseable_date_counts_for_nothing_and_is_reported():
    g = build(indi("@I1@", events=[("RESI", "26/10/1968")]))
    r = classify_with(g)
    assert r.people["@I1@"].group == 4
    assert ("@I1@", "RESI", "26/10/1968") in r.unparseable_dates


# --- generation rule ---------------------------------------------------

def test_home_person_is_always_hidden_regardless_of_group():
    g = build(indi("@I1@", events=[("DEAT", "1990")]))  # would be group 2
    p = classify_with(g).people["@I1@"]
    assert p.published is False
    assert p.publish_reason == "home person (always hidden)"


def test_direct_ancestor_generation_and_min_generation_cutoff():
    # @I1@ home -> parent @I2@ (gen 1, undetermined) -> grandparent @I3@
    # (gen 2, undetermined). min_generation=2: gen1 undetermined hidden,
    # gen2 undetermined published.
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I2@", famc="@F2@", fams=["@F1@"]),
        indi("@I3@", fams=["@F2@"]),
        fam("@F1@", husb="@I2@", children=[("@I1@", "Natural", "Natural")]),
        fam("@F2@", husb="@I3@", children=[("@I2@", "Natural", "Natural")]),
    )
    r = classify_with(g, min_generation=2)
    assert r.people["@I2@"].generation == 1
    assert r.people["@I2@"].published is False  # group 4, gen 1 < min_generation
    assert r.people["@I3@"].generation == 2
    assert r.people["@I3@"].published is True  # group 4, gen 2 >= min_generation


def test_undated_direct_ancestor_still_gets_a_generation():
    """Generation comes purely from FAM links, never from dates."""
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I2@", fams=["@F1@"]),  # no dates at all
        fam("@F1@", husb="@I2@", children=[("@I1@", "Natural", "Natural")]),
    )
    p = classify_with(g).people["@I2@"]
    assert p.generation == 1


def test_collateral_relative_generation_via_grandparent():
    # I1 home; I2 = parent (gen1); I3 = grandparent (gen2); I4 = grandparent's
    # other child, i.e. I1's great-aunt/uncle - a collateral, not a direct
    # ancestor, who should land at the SAME generation as I2 (gen 1).
    g = build(
        indi("@I1@", famc="@F1@"),
        indi("@I2@", famc="@F2@", fams=["@F1@"]),
        indi("@I3@", fams=["@F2@"]),
        indi("@I4@", famc="@F2@"),  # collateral: sibling of I2
        fam("@F1@", husb="@I2@", children=[("@I1@", "Natural", "Natural")]),
        fam("@F2@", husb="@I3@", children=[
            ("@I2@", "Natural", "Natural"),
            ("@I4@", "Natural", "Natural"),
        ]),
    )
    r = classify_with(g)
    assert r.people["@I4@"].generation == 1  # same generation as I2, not I3


def test_unconnected_person_has_no_generation_and_group_4_is_hidden():
    g = build(indi("@I1@"), indi("@I2@"))  # I2 shares no FAM with I1 at all
    r = classify_with(g)
    p = r.people["@I2@"]
    assert p.generation is None
    assert p.published is False
    assert "not connected" in p.publish_reason


def test_unconnected_person_group_2_is_still_published():
    g = build(indi("@I1@"), indi("@I2@", events=[("DEAT", "1990")]))
    r = classify_with(g)
    p = r.people["@I2@"]
    assert p.generation is None
    assert p.published is True


def test_conflicting_generation_path_takes_the_younger_value():
    # I1 home, gen 0. I1's sibling S (gen 0, via shared parent P).
    # I1's grandchild Y (child of I1's child C, so C=-1, Y=-2).
    # Y marries S: a spousal (weight-0) edge from S (gen 0) to Y would
    # imply gen 0 for Y too, conflicting with -2 from the direct line.
    # "Take the younger" -> the more negative value, -2, must win.
    g = build(
        indi("@I1@", famc="@F_P@", fams=["@F_C@"]),
        indi("@I_S@", famc="@F_P@", fams=["@F_SY@"]),       # sibling of I1
        indi("@I_P@", fams=["@F_P@"]),                       # shared parent
        indi("@I_C@", famc="@F_C@", fams=["@F_CY@"]),        # I1's child
        indi("@I_Y@", famc="@F_CY@", fams=["@F_SY@"]),       # grandchild + S's spouse
        fam("@F_P@", husb="@I_P@", children=[
            ("@I1@", "Natural", "Natural"),
            ("@I_S@", "Natural", "Natural"),
        ]),
        fam("@F_C@", husb="@I1@", children=[("@I_C@", "Natural", "Natural")]),
        fam("@F_CY@", husb="@I_C@", children=[("@I_Y@", "Natural", "Natural")]),
        fam("@F_SY@", husb="@I_S@", wife="@I_Y@"),
    )
    r = classify_with(g)
    assert "@I_Y@" in r.generation_conflicts
    assert r.people["@I_Y@"].generation == -2  # the younger of {-2, 0}


# --- withheld_media -------------------------------------------------------

def test_media_linked_only_to_a_hidden_person_is_withheld():
    # I1 has OBJE @M1@ attached, and I1 is hidden (home=@I0@, a person not
    # present in the file, so I1 has no generation and isn't group 2).
    g = Gedcom.from_text(
        HEAD +
        "0 @I1@ INDI\n1 NAME Test /Person/\n1 OBJE @M1@\n" +
        "0 @M1@ OBJE\n1 FILE x.jpg\n" +
        TRLR
    )
    priv = classify_with(g, home="@I0@")
    assert priv.people["@I1@"].published is False
    withheld = withheld_media(g, priv.hidden_xrefs)
    assert "@M1@" in withheld
    assert withheld["@M1@"] == {"@I1@"}


def test_media_linked_to_both_hidden_and_published_is_still_withheld():
    g = Gedcom.from_text(
        HEAD +
        "0 @I1@ INDI\n1 NAME Hidden /Person/\n1 OBJE @M1@\n" +
        "0 @I2@ INDI\n1 NAME Published /Person/\n1 DEAT Y\n1 DATE 1990\n1 OBJE @M1@\n" +
        "0 @M1@ OBJE\n1 FILE x.jpg\n" +
        TRLR
    )
    priv = classify_with(g, home="@I0@")
    assert priv.people["@I1@"].published is False
    assert priv.people["@I2@"].published is True
    withheld = withheld_media(g, priv.hidden_xrefs)
    assert "@M1@" in withheld  # BRIEF.md: withheld "even if also linked to a published person"


def test_media_allow_override_publishes_an_otherwise_withheld_item():
    g = Gedcom.from_text(
        HEAD +
        "0 @I1@ INDI\n1 NAME Hidden /Person/\n1 OBJE @M1@\n" +
        "0 @M1@ OBJE\n1 FILE x.jpg\n" +
        TRLR
    )
    priv = classify_with(g, home="@I0@")
    withheld = withheld_media(g, priv.hidden_xrefs, allow_overrides=frozenset({"@M1@"}))
    assert "@M1@" not in withheld

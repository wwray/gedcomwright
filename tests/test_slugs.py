"""Tests for pipeline.slugs (BRIEF.md Testing: "slug stability")."""
from __future__ import annotations

from pipeline.slugs import person_slug


def test_basic_slug_shape():
    assert person_slug("James", "Wray", 1838, 1888, "@I123@") == "james-wray-1838-1888-I123"


def test_birth_year_only():
    assert person_slug("James", "Wray", 1838, None, "@I123@") == "james-wray-1838-I123"


def test_death_year_only_is_prefixed_so_it_cant_be_mistaken_for_a_birth_year():
    assert person_slug("James", "Wray", None, 1888, "@I123@") == "james-wray-d1888-I123"


def test_no_dates_at_all_still_produces_a_slug():
    assert person_slug("James", "Wray", None, None, "@I123@") == "james-wray-I123"


def test_empty_surname_falls_back_to_given_name_only():
    assert person_slug("Ellen", "", 1900, None, "@I130@") == "ellen-1900-I130"


def test_no_name_at_all_falls_back_to_unknown():
    assert person_slug("", "", None, None, "@I1@") == "unknown-I1"


def test_accented_characters_are_ascii_folded():
    assert person_slug("André", "Lévesque", 1900, None, "@I1@") == "andre-levesque-1900-I1"


def test_punctuation_in_name_is_collapsed_to_hyphens():
    slug = person_slug("Jennett \"Jean\"", "Knapp", 1850, None, "@I1@")
    assert slug == "jennett-jean-knapp-1850-I1"
    assert "--" not in slug


def test_the_bare_xref_is_always_appended_so_collisions_are_impossible():
    a = person_slug("James", "Wray", 1838, 1888, "@I123@")
    b = person_slug("James", "Wray", 1838, 1888, "@I456@")
    assert a != b
    assert a.endswith("-I123")
    assert b.endswith("-I456")


def test_slug_is_a_pure_function_of_its_inputs_stability():
    """BRIEF.md: "Slugs change only if the name or dates change." - i.e. the
    same inputs always produce the same slug, with nothing order- or
    time-dependent sneaking in."""
    args = ("James", "Wray", 1838, 1888, "@I123@")
    assert person_slug(*args) == person_slug(*args)


def test_slug_changes_when_name_changes():
    a = person_slug("James", "Wray", 1838, 1888, "@I123@")
    b = person_slug("Jim", "Wray", 1838, 1888, "@I123@")
    assert a != b


def test_slug_changes_when_dates_change():
    a = person_slug("James", "Wray", 1838, 1888, "@I123@")
    b = person_slug("James", "Wray", 1838, 1889, "@I123@")
    assert a != b

"""Unit tests for company-name normalization and fuzzy entity keys."""

import pytest

from app.utils.companies import fuzzy_entity_key, normalize_company_name


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Acme Inc.", "Acme"),
        ("Acme, Inc.", "Acme"),
        ("Acme LLC", "Acme"),
        ("Acme Corp.", "Acme"),
        ("Acme Ltd.", "Acme"),
        ("Acme Incorporated", "Acme"),
        ("Acme Corporation", "Acme"),
        ("Acme Limited", "Acme"),
        ("Acme Co.", "Acme"),
        ("Acme PLC", "Acme"),
        ("Acme GmbH", "Acme"),
        ("Acme L.L.C.", "Acme"),
        ("Acme Pvt Ltd", "Acme"),
        ("Acme Pvt. Ltd.", "Acme"),
    ],
)
def test_legal_suffixes_are_stripped(value, expected) -> None:
    assert normalize_company_name(value) == (expected, True)


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_blank_values_map_to_none_without_a_change(value) -> None:
    assert normalize_company_name(value) == (None, False)
    assert fuzzy_entity_key(value) is None


def test_repeated_whitespace_and_trailing_punctuation_are_cleaned() -> None:
    assert normalize_company_name("  Acme   Widgets, Inc.  ") == (
        "Acme Widgets",
        True,
    )


def test_already_clean_name_reports_no_change() -> None:
    assert normalize_company_name("Acme") == ("Acme", False)


def test_lowercase_multiword_name_becomes_title_case() -> None:
    assert normalize_company_name("acme widgets inc") == ("Acme Widgets", True)


def test_all_caps_name_becomes_title_case() -> None:
    assert normalize_company_name("ACME WIDGETS CORP") == ("ACME Widgets", True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("IBM Corp.", "IBM"),
        ("AWS LLC", "AWS"),
        ("SAP Corp.", "SAP"),
        ("ACME INC", "ACME"),
    ],
)
def test_short_uppercase_acronyms_are_preserved(value, expected) -> None:
    cleaned, changed = normalize_company_name(value)
    assert cleaned == expected
    assert changed is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ACME, Inc.", "acme"),
        ("Acme Corp", "acme"),
        ("Acme, Inc.", "acme"),
        ("  ACME   WIDGETS LLC  ", "acme widgets"),
        ("Acme-Corp", "acme"),
        ("Acme L.L.C.", "acme"),
        ("At&T Systems", "at t systems"),
    ],
)
def test_fuzzy_entity_key_normalises_variants(value, expected) -> None:
    assert fuzzy_entity_key(value) == expected


def test_fuzzy_entity_key_makes_entity_variants_equal() -> None:
    assert fuzzy_entity_key("ACME, Inc.") == fuzzy_entity_key("Acme Corp")


def test_fuzzy_entity_key_does_not_merge_distinct_entities() -> None:
    assert fuzzy_entity_key("Acme Corp") != fuzzy_entity_key("Acme Widgets")

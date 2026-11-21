"""Unit tests for E.164 phone normalization."""

import pytest

from app.utils.phones import normalize_phone_e164


@pytest.mark.parametrize(
    ("value", "region", "expected"),
    [
        ("(415) 555-2671", "US", "+14155552671"),
        ("020 7946 0958", "GB", "+442079460958"),
        ("+44 20 7946 0958", "US", "+442079460958"),
        ("+14155552671", "US", "+14155552671"),
    ],
)
def test_valid_numbers_are_formatted_as_e164(value, region, expected) -> None:
    normalized, changed, error = normalize_phone_e164(value, region)
    assert normalized == expected
    assert changed == (value.strip() != expected)
    assert error is None


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_blank_values_are_preserved_without_error(value) -> None:
    assert normalize_phone_e164(value) == (None, False, None)


def test_invalid_number_returns_a_region_specific_error() -> None:
    assert normalize_phone_e164("not a phone", "US") == (
        None,
        False,
        "Invalid phone number for region US: not a phone",
    )


def test_wrong_region_can_make_a_number_invalid() -> None:
    assert normalize_phone_e164("020 7946 0958", "US") == (
        None,
        False,
        "Invalid phone number for region US: 020 7946 0958",
    )

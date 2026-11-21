"""Unit tests for the pure JSON record validator."""

from app.schemas.validate import FieldRule
from app.services.json_validator import validate_records


def test_validate_all_valid() -> None:
    rules = {
        "email": FieldRule(required=True, type="email"),
        "dob": FieldRule(type="date"),
    }
    records = [
        {"email": "alice@example.com", "dob": "2024-01-15"},
        {"email": "bob@example.org", "dob": "15/03/2024"},
    ]
    result = validate_records(records, rules)
    assert result.valid == records
    assert result.quarantined == []
    assert result.summary.rows_in == 2
    assert result.summary.rows_out == 2
    assert result.summary.rows_dropped == 0
    assert result.summary.fixes_applied == {}


def test_validate_multiple_errors_per_record() -> None:
    rules = {
        "email": FieldRule(required=True, type="email"),
        "age": FieldRule(type="integer"),
    }
    result = validate_records([{"email": "bad", "age": "abc"}], rules)
    assert len(result.quarantined) == 1
    assert result.quarantined[0].row_index == 0
    assert result.quarantined[0].errors == [
        "'email' must be a valid email address",
        "'age' must be a number",
    ]
    assert result.summary.errors_by_field == {"email": 1, "age": 1}


def test_validate_required_missing_and_empty() -> None:
    rules = {"name": FieldRule(required=True)}
    records = [{}, {"name": "   "}, {"name": "Alice"}]
    result = validate_records(records, rules)
    assert [row.row_index for row in result.quarantined] == [0, 1]
    assert result.valid == [{"name": "Alice"}]
    assert result.quarantined[0].errors == ["'name' is required"]


def test_validate_nullable_allows_null_and_empty() -> None:
    rules = {"note": FieldRule(required=True, nullable=True)}
    records = [{"note": None}, {"note": ""}, {}]
    result = validate_records(records, rules)
    # Null (including an absent field) satisfies nullable; a blank string does not.
    assert result.valid == [{"note": None}, {}]
    assert [row.row_index for row in result.quarantined] == [1]
    assert result.quarantined[0].errors == ["'note' is required"]


def test_validate_email_valid_and_invalid() -> None:
    rules = {"email": FieldRule(type="email")}
    result = validate_records(
        [{"email": "a@b.com"}, {"email": "nope"}, {"email": 123}], rules
    )
    assert result.valid == [{"email": "a@b.com"}]
    assert [row.row_index for row in result.quarantined] == [1, 2]


def test_validate_date_valid_formats_and_invalid() -> None:
    rules = {"d": FieldRule(type="date")}
    result = validate_records(
        [
            {"d": "2024-01-15"},
            {"d": "15/03/2024"},
            {"d": "Jan 5, 2024"},
            {"d": "2024-13-45"},
        ],
        rules,
    )
    assert len(result.valid) == 3
    assert result.quarantined[0].row_index == 3
    assert result.quarantined[0].errors == ["'d' must be a valid date"]


def test_validate_number_valid_invalid_and_bool_rejected() -> None:
    rules = {"n": FieldRule(type="number")}
    result = validate_records(
        [{"n": 1}, {"n": 2.5}, {"n": "3.5"}, {"n": True}, {"n": "abc"}],
        rules,
    )
    assert [row["n"] for row in result.valid] == [1, 2.5, "3.5"]
    assert [row.row_index for row in result.quarantined] == [3, 4]


def test_validate_integer_valid_and_invalid() -> None:
    rules = {"n": FieldRule(type="integer")}
    result = validate_records(
        [{"n": 3}, {"n": "4"}, {"n": 3.5}, {"n": "4.5"}], rules
    )
    assert len(result.valid) == 2
    assert [row.row_index for row in result.quarantined] == [2, 3]


def test_validate_boolean_valid_and_invalid() -> None:
    rules = {"flag": FieldRule(type="boolean")}
    result = validate_records(
        [{"flag": True}, {"flag": "no"}, {"flag": "maybe"}, {"flag": 1}],
        rules,
    )
    assert len(result.valid) == 2
    assert [row.row_index for row in result.quarantined] == [2, 3]


def test_validate_empty_records_returns_zero_summary() -> None:
    result = validate_records([], {"id": FieldRule(required=True)})
    assert result.valid == []
    assert result.quarantined == []
    assert result.summary.rows_in == 0
    assert result.summary.rows_out == 0
    assert result.summary.valid_count == 0
    assert result.summary.quarantined_count == 0
    assert result.summary.errors_by_field == {}


def test_validate_record_with_missing_values_is_accepted_when_optional() -> None:
    rules = {"name": FieldRule(), "note": FieldRule(type="string")}
    result = validate_records([{}], rules)
    assert result.valid == [{}]
    assert result.quarantined == []


def test_validate_duplicate_keys_across_records_are_not_dropped() -> None:
    rules = {"id": FieldRule(required=True, type="integer")}
    records = [{"id": 1}, {"id": 1}, {"id": 2}]
    result = validate_records(records, rules)
    assert result.valid == records
    assert result.summary.rows_out == 3
    assert result.summary.rows_dropped == 0


def test_validate_non_dict_record_is_quarantined() -> None:
    result = validate_records(["not an object"], {})
    assert result.quarantined[0].errors == ["record must be a JSON object"]
    assert result.summary.rows_dropped == 0  # quarantines are counted separately
    assert result.summary.rows_in == 1
    assert result.summary.quarantined_count == 1


def test_validate_huge_integers_do_not_overflow() -> None:
    huge = 10**400
    for kind in ("integer", "number"):
        rules = {"n": FieldRule(type=kind)}
        records = [{"n": huge}, {"n": -huge}]
        result = validate_records(records, rules)
        assert result.valid == records
        assert result.quarantined == []
        assert result.summary.rows_out == 2
        assert result.summary.rows_dropped == 0


def test_validate_overflowing_numeric_string_is_rejected_cleanly() -> None:
    rules = {"n": FieldRule(type="integer")}
    result = validate_records([{"n": "1" + "0" * 400}], rules)
    assert result.valid == []
    assert len(result.quarantined) == 1
    assert result.quarantined[0].errors == ["'n' must be a finite number"]


def test_validate_integer_rejects_bool_like_int() -> None:
    rules = {"n": FieldRule(type="integer")}
    result = validate_records([{"n": True}], rules)
    assert result.quarantined[0].errors == ["'n' must be a number"]


def test_validate_long_email_is_rejected_before_regex() -> None:
    rules = {"email": FieldRule(type="email")}
    long_email = "a" * 250 + "@b.com"  # 256 chars
    pathological = "a." * 10_000 + "a@b.com"
    result = validate_records(
        [{"email": "ok@b.com"}, {"email": long_email}, {"email": pathological}],
        rules,
    )
    assert result.valid == [{"email": "ok@b.com"}]
    assert [row.row_index for row in result.quarantined] == [1, 2]


def test_validate_numeric_strings_reject_underscores_and_exponents() -> None:
    for kind in ("number", "integer"):
        rules = {"n": FieldRule(type=kind)}
        result = validate_records(
            [
                {"n": "1_000"},
                {"n": "1e3"},
                {"n": " 2 "},
                {"n": "-3"},
            ],
            rules,
        )
        assert [row.row_index for row in result.quarantined] == [0, 1]
        # Values are returned unchanged: validation never coerces.
        assert [row["n"] for row in result.valid] == [" 2 ", "-3"]


def test_validate_dates_match_the_cleaner_format_list() -> None:
    rules = {"d": FieldRule(type="date")}
    result = validate_records(
        [
            {"d": "2024-01-15T10:30:00"},
            {"d": "2024-01-15T10:30:00+05:30"},
            {"d": "2024-01-15T10:30:00.123456"},
        ],
        rules,
    )
    # The cleaner's strptime formats accept only the first of these.
    assert result.valid == [{"d": "2024-01-15T10:30:00"}]
    assert [row.row_index for row in result.quarantined] == [1, 2]


def test_validate_summary_warnings_have_stable_keys() -> None:
    result = validate_records([], {})
    assert result.summary.warnings.model_dump() == {
        "malformed_rows": 0,
        "unknown_columns": [],
    }
    assert result.summary.rows_in == (
        result.summary.rows_out
        + result.summary.rows_dropped
        + result.summary.quarantined_count
    )

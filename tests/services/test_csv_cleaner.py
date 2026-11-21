"""Unit tests for the pure Polars CSV cleaning transforms."""

import csv
import io

import polars as pl
import pytest

from app.schemas.clean import CleanOptions
from app.services import csv_cleaner
from app.services.csv_cleaner import (
    clean_csv_bytes,
    decode_csv_bytes,
    dedupe,
    normalize_dates,
    normalize_emails,
    read_csv_bytes,
    read_csv_bytes_with_quarantine,
    render_csv,
    render_quarantined_csv,
)
from app.schemas.common import QuarantinedRecord
from app.utils.errors import CsvReadError


# --------------------------------------------------------------------------- #
# Reading / decoding
# --------------------------------------------------------------------------- #
def test_read_csv_bytes_utf8_no_bom() -> None:
    frame = read_csv_bytes(b"name,email\nAlice,a@x.com\n")
    assert frame.columns == ["name", "email"]
    assert frame.to_dicts() == [{"name": "Alice", "email": "a@x.com"}]
    assert frame.dtypes == [pl.String, pl.String]


def test_read_csv_bytes_utf8_with_bom_header_not_polluted() -> None:
    frame = read_csv_bytes(b"\xef\xbb\xbfname,email\nAlice,a@x.com\n")
    assert frame.columns == ["name", "email"]


def test_read_csv_bytes_latin1_fallback() -> None:
    payload = "name,city\nJosé,Zürich\n".encode("latin-1")
    frame = read_csv_bytes(payload)
    assert frame.to_dicts() == [{"name": "José", "city": "Zürich"}]


def test_read_csv_bytes_empty_returns_empty_frame() -> None:
    frame = read_csv_bytes(b"")
    assert frame.height == 0
    assert frame.columns == []


def test_read_csv_bytes_bom_only_returns_empty_frame() -> None:
    assert read_csv_bytes(b"\xef\xbb\xbf").height == 0


def test_read_csv_bytes_binary_with_nul_raises() -> None:
    with pytest.raises(CsvReadError, match="binary content"):
        read_csv_bytes(b"a,b\n\x00,2\n")


def test_read_csv_bytes_ragged_rows_are_quarantined() -> None:
    frame, quarantined = read_csv_bytes_with_quarantine(b"a,b\n1,2,3\n4,5\n")
    assert frame.to_dicts() == [{"a": "4", "b": "5"}]
    assert len(quarantined) == 1
    assert quarantined[0].row_index == 0
    assert quarantined[0].record == ["1", "2", "3"]
    assert quarantined[0].errors == [
        "malformed row: expected 2 columns, found 3"
    ]
    # The plain reader exposes only the well-formed rows.
    assert read_csv_bytes(b"a,b\n1,2,3\n4,5\n").to_dicts() == [
        {"a": "4", "b": "5"}
    ]


def test_read_csv_bytes_header_only_has_zero_rows() -> None:
    frame = read_csv_bytes(b"a,b\n")
    assert frame.height == 0
    assert frame.columns == ["a", "b"]


def test_decode_csv_bytes_prefers_cp1252_over_latin1() -> None:
    payload = "name\n\u201chello\u201d\n".encode("cp1252")
    # cp1252 maps 0x93/0x94 to smart quotes; Latin-1 would yield C1 controls.
    assert "\u201chello\u201d" in decode_csv_bytes(payload)
    assert read_csv_bytes(payload).to_dicts() == [{"name": "\u201chello\u201d"}]


def test_read_csv_bytes_sniffs_semicolon_delimiter() -> None:
    frame = read_csv_bytes(b"id;email;joined\n1;a@x.com;2024-01-15\n")
    assert frame.columns == ["id", "email", "joined"]
    assert frame.to_dicts() == [
        {"id": "1", "email": "a@x.com", "joined": "2024-01-15"}
    ]


def test_read_csv_bytes_sniffs_tab_delimiter() -> None:
    frame = read_csv_bytes(b"id\tname\n1\tAlice\n")
    assert frame.columns == ["id", "name"]
    assert frame.to_dicts() == [{"id": "1", "name": "Alice"}]


def test_read_csv_bytes_sniffs_pipe_delimiter() -> None:
    payload = "id|city\n1|Z\u00fcrich\n".encode("utf-8")
    frame = read_csv_bytes(payload)
    assert frame.columns == ["id", "city"]
    assert frame.to_dicts() == [{"id": "1", "city": "Z\u00fcrich"}]


def test_explicit_delimiter_option_overrides_sniffing() -> None:
    outcome = clean_csv_bytes(
        b"id;name\n1;Alice\n", CleanOptions(delimiter=";")
    )
    assert outcome.frame.columns == ["id", "name"]
    assert outcome.frame.to_dicts() == [{"id": "1", "name": "Alice"}]


def test_literal_backslash_t_delimiter_is_accepted() -> None:
    assert CleanOptions(delimiter="\\t").delimiter == "\t"
    frame = read_csv_bytes(b"id\tname\n1\tAlice\n")
    assert frame.columns == ["id", "name"]


def test_invalid_delimiter_is_rejected() -> None:
    with pytest.raises(ValueError):
        CleanOptions(delimiter=":")


def test_single_column_header_with_semicolon_raises() -> None:
    with pytest.raises(CsvReadError, match="single-column header"):
        read_csv_bytes_with_quarantine(b"a;b\n1;2\n", delimiter=",")


def test_single_column_header_with_tab_raises() -> None:
    with pytest.raises(CsvReadError, match="single-column header"):
        clean_csv_bytes(b"a\tb\n1\t2\n", CleanOptions(delimiter=","))


def test_header_whitespace_is_stripped_so_options_apply() -> None:
    outcome = clean_csv_bytes(
        b" email , dob \n A@X.COM , 15/03/2024 \n",
        CleanOptions(email_columns=["email"], date_columns=["dob"]),
    )
    assert outcome.frame.columns == ["email", "dob"]
    assert outcome.frame.to_dicts() == [
        {"email": "a@x.com", "dob": "2024-03-15"}
    ]
    assert outcome.summary.fixes_applied["emails_normalized"] == 1
    assert outcome.summary.fixes_applied["dates_normalized"] == 1
    assert outcome.ignored_columns == []


def test_duplicate_headers_raise_csv_read_error() -> None:
    with pytest.raises(CsvReadError, match="duplicate column headers"):
        clean_csv_bytes(b"a,a,b\n1,2,3\n", CleanOptions())


def test_headers_duplicate_after_whitespace_stripping_raise() -> None:
    with pytest.raises(CsvReadError, match="duplicate column headers"):
        clean_csv_bytes(b"a, a,b\n1,2,3\n", CleanOptions())


# --------------------------------------------------------------------------- #
# Email normalisation
# --------------------------------------------------------------------------- #
def test_normalize_emails_trim_lowercase_and_count() -> None:
    frame = pl.DataFrame({"email": ["  Alice@Example.COM ", None, "", "bob@x.com"]})
    result, changed, ignored = normalize_emails(frame, ["email"])
    assert result["email"].to_list() == [
        "alice@example.com",
        None,
        "",
        "bob@x.com",
    ]
    assert changed == 1
    assert ignored == []


def test_normalize_emails_missing_column_is_noop() -> None:
    frame = pl.DataFrame({"email": ["A@X.COM"]})
    result, changed, ignored = normalize_emails(frame, ["nope"])
    assert result.to_dicts() == frame.to_dicts()
    assert changed == 0
    assert ignored == ["nope"]


def test_normalize_emails_nulls_and_empty_unchanged() -> None:
    frame = pl.DataFrame({"email": [None, "", "  "]})
    result, changed, _ = normalize_emails(frame, ["email"])
    # A whitespace-only string becomes "" and is therefore counted as a fix.
    assert result["email"].to_list() == [None, "", ""]
    assert changed == 1


# --------------------------------------------------------------------------- #
# Date normalisation
# --------------------------------------------------------------------------- #
def test_normalize_dates_all_supported_formats() -> None:
    frame = pl.DataFrame(
        {
            "d": [
                "2024-01-15",
                "15/03/2024",
                "03/04/2024",
                "Jan 5, 2024",
                "20240120",
                "2024-02-01T10:30:00",
                "not a date",
                "",
                None,
            ]
        }
    )
    result, normalized, unparseable, ambiguous, ignored = normalize_dates(frame, ["d"])
    assert result["d"].to_list() == [
        "2024-01-15",
        "2024-03-15",
        "2024-04-03",
        "2024-01-05",
        "2024-01-20",
        "2024-02-01",
        "not a date",
        None,  # empty strings are treated as missing values
        None,
    ]
    assert normalized == 6
    assert unparseable == 1
    assert ambiguous == 1  # 03/04/2024 parses both ways
    assert ignored == []


def test_normalize_dates_unparseable_is_preserved_and_counted() -> None:
    frame = pl.DataFrame({"d": ["definitely not a date"]})
    result, normalized, unparseable, ambiguous, _ = normalize_dates(frame, ["d"])
    assert result["d"].to_list() == ["definitely not a date"]
    assert (normalized, unparseable, ambiguous) == (0, 1, 0)


def test_normalize_dates_nulls_and_empty_do_not_crash() -> None:
    frame = pl.DataFrame({"d": [None, "", "  "]})
    result, normalized, unparseable, ambiguous, _ = normalize_dates(frame, ["d"])
    # Empty / whitespace-only values become null; nothing is counted as a fix.
    assert result["d"].to_list() == [None, None, None]
    assert (normalized, unparseable, ambiguous) == (0, 0, 0)


def test_normalize_dates_missing_column_is_noop() -> None:
    frame = pl.DataFrame({"d": ["2024-01-15"]})
    result, normalized, unparseable, ambiguous, ignored = normalize_dates(
        frame, ["nope"]
    )
    assert result.to_dicts() == frame.to_dicts()
    assert (normalized, unparseable, ambiguous) == (0, 0, 0)
    assert ignored == ["nope"]


def test_normalize_dates_uses_one_numeric_format_per_column() -> None:
    frame = pl.DataFrame({"d": ["13/05/2024", "05/13/2024"]})
    result, normalized, unparseable, ambiguous, _ = normalize_dates(frame, ["d"])
    # One parse per format -> tie -> dayfirst default; the month-first-only
    # value is left verbatim instead of being parsed with the other format.
    assert result["d"].to_list() == ["2024-05-13", "05/13/2024"]
    assert (normalized, unparseable, ambiguous) == (1, 1, 0)


def test_normalize_dates_dayfirst_option_selects_month_first() -> None:
    frame = pl.DataFrame({"d": ["05/06/2024", "06/07/2024"]})
    day_first, *_ = normalize_dates(frame, ["d"], dayfirst=True)
    month_first, *_ = normalize_dates(frame, ["d"], dayfirst=False)
    assert day_first["d"].to_list() == ["2024-06-05", "2024-07-06"]
    assert month_first["d"].to_list() == ["2024-05-06", "2024-06-07"]


def test_normalize_dates_counts_only_genuinely_ambiguous_values() -> None:
    frame = pl.DataFrame({"d": ["05/06/2024", "13/05/2024", "2024-01-15", None]})
    result, normalized, unparseable, ambiguous, _ = normalize_dates(frame, ["d"])
    # 05/06 parses both ways; 13/05 is day-first only; ISO is unambiguous.
    assert result["d"].to_list() == ["2024-06-05", "2024-05-13", "2024-01-15", None]
    assert (normalized, unparseable, ambiguous) == (3, 0, 1)


def test_normalize_dates_dash_pair_also_uses_one_format_per_column() -> None:
    frame = pl.DataFrame({"d": ["05-06-2024", "06-07-2024"]})
    result, normalized, unparseable, ambiguous, _ = normalize_dates(
        frame, ["d"], dayfirst=False
    )
    assert result["d"].to_list() == ["2024-05-06", "2024-06-07"]
    assert (normalized, unparseable, ambiguous) == (2, 0, 2)


# --------------------------------------------------------------------------- #
# Deduplication
# --------------------------------------------------------------------------- #
def test_dedupe_keeps_first_trimmed_key_case_sensitively() -> None:
    frame = pl.DataFrame({"k": [" A ", "a", "B"], "v": [1, 2, 3]})
    result, removed, missing, ignored = dedupe(frame, "k")
    # Case is preserved by default, so " A " and "a" are distinct keys.
    assert result.to_dicts() == [
        {"k": " A ", "v": 1},
        {"k": "a", "v": 2},
        {"k": "B", "v": 3},
    ]
    assert (removed, missing) == (0, 0)
    assert ignored == []


def test_dedupe_ignore_case_merges_case_variants() -> None:
    frame = pl.DataFrame({"k": [" A ", "a", "B"], "v": [1, 2, 3]})
    result, removed, missing, ignored = dedupe(frame, "k", ignore_case=True)
    assert result.to_dicts() == [{"k": " A ", "v": 1}, {"k": "B", "v": 3}]
    assert (removed, missing) == (1, 0)
    assert ignored == []


def test_dedupe_null_keys_are_handled_and_counted() -> None:
    frame = pl.DataFrame({"k": [None, None, " a ", "A"], "v": [1, 2, 3, 4]})
    result, removed, missing, _ = dedupe(frame, "k")
    # Both null-key rows survive; only genuinely duplicated non-null keys go.
    assert result.to_dicts() == [
        {"k": None, "v": 1},
        {"k": None, "v": 2},
        {"k": " a ", "v": 3},
        {"k": "A", "v": 4},
    ]
    assert (removed, missing) == (0, 2)


def test_dedupe_blank_keys_are_preserved_as_missing() -> None:
    frame = pl.DataFrame({"k": ["", "  ", None, "x", "x"], "v": [1, 2, 3, 4, 5]})
    result, removed, missing, _ = dedupe(frame, "k")
    assert result["v"].to_list() == [1, 2, 3, 4]
    assert (removed, missing) == (1, 3)


def test_dedupe_missing_column_is_noop() -> None:
    frame = pl.DataFrame({"k": ["a", "a"]})
    result, removed, missing, ignored = dedupe(frame, "nope")
    assert result.to_dicts() == frame.to_dicts()
    assert (removed, missing) == (0, 0)
    assert ignored == ["nope"]


def test_dedupe_no_key_requested_is_noop() -> None:
    frame = pl.DataFrame({"k": ["a", "a"]})
    result, removed, _, _ = dedupe(frame, None)
    assert result.height == 2
    assert removed == 0


# --------------------------------------------------------------------------- #
# Orchestration / rendering
# --------------------------------------------------------------------------- #
def test_clean_csv_bytes_summary_counts_are_consistent() -> None:
    payload = b"email,dob\n A@X.COM ,2024-01-15\na@x.com,15/03/2024\n"
    options = CleanOptions(
        dedupe_by="email", email_columns=["email"], date_columns=["dob"]
    )
    outcome = clean_csv_bytes(payload, options)

    assert outcome.summary.rows_in == 2
    assert outcome.summary.rows_out == 1
    assert outcome.summary.rows_dropped == 1
    assert outcome.summary.fixes_applied == {
        "emails_normalized": 1,
        "dates_normalized": 2,
        "unparseable_dates": 0,
        "duplicates_removed": 1,
    }
    assert outcome.summary.warnings.model_dump() == {
        "malformed_rows": 0,
        "unknown_columns": [],
    }
    # Dedupe keeps the *first* occurrence after normalisation.
    assert outcome.frame.to_dicts() == [
        {"email": "a@x.com", "dob": "2024-01-15"}
    ]


def test_clean_csv_bytes_reports_unknown_columns() -> None:
    options = CleanOptions(
        email_columns=["nada"], date_columns=["nope"], dedupe_by="ghost"
    )
    outcome = clean_csv_bytes(b"a\n1\n", options)
    assert outcome.ignored_columns == ["nada", "nope", "ghost"]
    assert outcome.summary.warnings.unknown_columns == ["nada", "nope", "ghost"]
    assert outcome.summary.warnings.malformed_rows == 0
    assert outcome.summary.rows_dropped == 0


def test_render_csv_produces_utf8_bytes() -> None:
    frame = pl.DataFrame({"a": ["1"], "b": ["2"]})
    assert render_csv(frame) == b"a,b\n1,2\n"


def test_clean_csv_bytes_malformed_rows_reconcile_with_dedupe() -> None:
    payload = b"id,name\n1,Alice\n2,Bob,extra\n1,Alicia\n3,Carol\n"
    outcome = clean_csv_bytes(payload, CleanOptions(dedupe_by="id"))
    assert outcome.frame.to_dicts() == [
        {"id": "1", "name": "Alice"},
        {"id": "3", "name": "Carol"},
    ]
    assert outcome.summary.rows_in == 4
    assert outcome.summary.rows_out == 2
    assert outcome.summary.rows_dropped == 1
    assert outcome.summary.warnings.malformed_rows == 1
    assert len(outcome.quarantined) == 1
    assert outcome.quarantined[0].record == ["2", "Bob", "extra"]
    assert (
        outcome.summary.rows_in
        == outcome.summary.rows_out
        + outcome.summary.rows_dropped
        + len(outcome.quarantined)
    )


def test_clean_csv_bytes_all_malformed_rows_are_quarantined() -> None:
    outcome = clean_csv_bytes(b"a,b\n1,2,3\n4\n", CleanOptions())
    assert outcome.frame.height == 0
    assert [row.row_index for row in outcome.quarantined] == [0, 1]
    assert outcome.summary.rows_in == 2
    assert outcome.summary.rows_out == 0
    assert outcome.summary.rows_dropped == 0
    assert outcome.summary.warnings.malformed_rows == 2


def test_clean_csv_bytes_unparseable_dates_are_counted_not_quarantined() -> None:
    outcome = clean_csv_bytes(
        b"d\nnot a date\n2024-01-15\n", CleanOptions(date_columns=["d"])
    )
    assert outcome.frame.to_dicts() == [{"d": "not a date"}, {"d": "2024-01-15"}]
    assert outcome.summary.fixes_applied["unparseable_dates"] == 1
    assert outcome.quarantined == []
    assert outcome.summary.rows_in == outcome.summary.rows_out


def test_clean_csv_bytes_blank_rows_are_skipped_and_counted() -> None:
    outcome = clean_csv_bytes(b"a,b\n1,2\n\n3,4\n", CleanOptions())
    assert outcome.frame.to_dicts() == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ]
    assert outcome.quarantined == []
    assert outcome.summary.rows_in == 3
    assert outcome.summary.rows_out == 2
    assert outcome.summary.rows_dropped == 1
    assert outcome.summary.blank_rows_skipped == 1
    assert (
        outcome.summary.rows_in
        == outcome.summary.rows_out
        + outcome.summary.rows_dropped
        + len(outcome.quarantined)
    )


def test_whitespace_only_lines_are_counted_as_blank_rows() -> None:
    outcome = clean_csv_bytes(b"a,b\n1,2\n   \n\n3,4\n", CleanOptions())
    assert outcome.frame.to_dicts() == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ]
    assert outcome.summary.rows_in == 4
    assert outcome.summary.blank_rows_skipped == 2
    assert outcome.summary.rows_dropped == 2


def test_blank_lines_are_not_mistaken_for_malformed_rows() -> None:
    outcome = clean_csv_bytes(b"a,b\n\n1,2\n\n", CleanOptions())
    assert outcome.quarantined == []
    assert outcome.summary.rows_in == 3
    assert outcome.summary.blank_rows_skipped == 2
    assert outcome.summary.rows_out == 1


def test_row_count_mismatch_raises_csv_read_error(monkeypatch) -> None:
    real_read_csv = csv_cleaner.pl.read_csv

    def duplicated_read_csv(*args, **kwargs):
        frame = real_read_csv(*args, **kwargs)
        return csv_cleaner.pl.concat([frame, frame])

    monkeypatch.setattr(csv_cleaner.pl, "read_csv", duplicated_read_csv)
    with pytest.raises(CsvReadError, match="row count mismatch"):
        clean_csv_bytes(b"a,b\n1,2\n", CleanOptions())


def test_clean_csv_bytes_dedupe_is_case_sensitive_by_default() -> None:
    outcome = clean_csv_bytes(
        b"k,v\nABC,1\nabc,2\n", CleanOptions(dedupe_by="k")
    )
    assert outcome.frame.height == 2
    assert outcome.summary.rows_dropped == 0
    assert "duplicates_removed" in outcome.summary.fixes_applied


def test_clean_csv_bytes_dedupe_ignore_case_option() -> None:
    outcome = clean_csv_bytes(
        b"k,v\nABC,1\nabc,2\n",
        CleanOptions(dedupe_by="k", dedupe_ignore_case=True),
    )
    assert outcome.frame.to_dicts() == [{"k": "ABC", "v": "1"}]
    assert outcome.summary.rows_dropped == 1
    assert outcome.summary.fixes_applied["duplicates_removed"] == 1


def test_clean_csv_bytes_missing_dedupe_keys_are_kept_and_counted() -> None:
    outcome = clean_csv_bytes(
        b"k,v\n,1\n   ,2\nX,3\nx,4\n", CleanOptions(dedupe_by="k")
    )
    assert [row["v"] for row in outcome.frame.to_dicts()] == ["1", "2", "3", "4"]
    assert outcome.summary.rows_dropped == 0
    assert outcome.summary.fixes_applied["rows_missing_dedupe_key"] == 2


def test_clean_csv_bytes_reports_ambiguous_dates_and_reconciles() -> None:
    outcome = clean_csv_bytes(
        b"d\n03/04/2024\n2024-01-15\n", CleanOptions(date_columns=["d"])
    )
    assert outcome.frame.to_dicts() == [{"d": "2024-04-03"}, {"d": "2024-01-15"}]
    assert outcome.summary.fixes_applied["ambiguous_dates"] == 1
    assert outcome.summary.rows_in == outcome.summary.rows_out == 2
    assert (
        outcome.summary.rows_in
        == outcome.summary.rows_out
        + outcome.summary.rows_dropped
        + len(outcome.quarantined)
    )


def test_clean_csv_bytes_date_dayfirst_option() -> None:
    payload = b"d\n05/06/2024\n"
    day = clean_csv_bytes(payload, CleanOptions(date_columns=["d"]))
    month = clean_csv_bytes(
        payload, CleanOptions(date_columns=["d"], date_dayfirst=False)
    )
    assert day.frame.to_dicts() == [{"d": "2024-06-05"}]
    assert month.frame.to_dicts() == [{"d": "2024-05-06"}]


def test_render_csv_escapes_formula_prefixes() -> None:
    frame = pl.DataFrame({"v": ["=1+1", "+x", "-y", "@SUM(A1)", "plain", None]})
    assert render_csv(frame) == b"v\n'=1+1\n'+x\n'-y\n'@SUM(A1)\nplain\n\n"
    # JSON mode uses the frame directly, so the data must remain untouched.
    assert frame.to_dicts()[0] == {"v": "=1+1"}


def test_render_csv_can_disable_formula_escaping() -> None:
    frame = pl.DataFrame({"v": ["=1+1"]})
    assert render_csv(frame, escape_formulas=False) == b"v\n=1+1\n"


def test_render_quarantined_csv_preserves_reasons() -> None:
    rows = [
        QuarantinedRecord(
            row_index=1,
            record=["2", "Bob", "extra"],
            errors=["malformed row: expected 2 columns, found 3"],
        )
    ]
    text = render_quarantined_csv(rows).decode("utf-8")
    rows_out = list(csv.reader(io.StringIO(text)))
    assert rows_out[0] == ["row_index", "record", "errors"]
    assert rows_out[1][0] == "1"
    assert rows_out[1][1] == '["2", "Bob", "extra"]'
    assert rows_out[1][2] == '["malformed row: expected 2 columns, found 3"]'

"""Endpoint tests for POST /clean/csv."""

import asyncio
import csv
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from app.routers.clean import read_upload_limited
from app.routers.demo import FALLBACK_MESSY_LEADS_CSV
from tests.conftest import csv_bytes, upload_csv


def test_clean_json_basic_shape(client) -> None:
    response = upload_csv(client, b"name,email\nAlice,a@x.com\n")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"name": "Alice", "email": "a@x.com"}]
    assert set(body["summary"]) == {
        "rows_in",
        "rows_out",
        "rows_dropped",
        "blank_rows_skipped",
        "fixes_applied",
        "warnings",
        "processing_time_ms",
    }
    assert body["summary"]["processing_time_ms"] >= 0
    assert body["summary"]["rows_in"] == 1
    assert body["summary"]["rows_out"] == 1
    assert body["summary"]["rows_dropped"] == 0


def test_clean_json_all_options_applied(client) -> None:
    payload = (
        b"email,dob,signup\n"
        b"  Alice@Example.COM ,15/03/2024,2024-01-15\n"
        b"alice@example.com,2024-03-15,not a date\n"
    )
    response = upload_csv(
        client,
        payload,
        dedupe_by="email",
        date_columns=["dob", "signup"],
        email_columns=["email"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [
        {"email": "alice@example.com", "dob": "2024-03-15", "signup": "2024-01-15"}
    ]
    assert body["summary"]["rows_dropped"] == 1
    assert body["summary"]["fixes_applied"] == {
        "emails_normalized": 1,
        "dates_normalized": 3,
        "unparseable_dates": 1,
        "duplicates_removed": 1,
    }
    assert body["summary"]["warnings"] == {
        "malformed_rows": 0,
        "unknown_columns": [],
    }


def test_clean_csv_output_media_type_headers_and_body(client) -> None:
    response = upload_csv(client, b"a,b\n1,2\n", output_format="csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.strip() == "a,b\n1,2"
    assert response.headers["x-rows-in"] == "1"
    assert response.headers["x-rows-out"] == "1"
    assert response.headers["x-rows-dropped"] == "0"
    assert json.loads(response.headers["x-fixes-applied"])["duplicates_removed"] == 0
    assert 'filename="cleaned.csv"' in response.headers["content-disposition"]


def test_clean_empty_file_returns_zero_summary(client) -> None:
    response = upload_csv(client, b"")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["quarantined"] == []
    assert body["summary"]["rows_in"] == 0
    assert body["summary"]["rows_out"] == 0
    assert body["summary"]["fixes_applied"]["emails_normalized"] == 0


def test_clean_missing_values_are_preserved(client) -> None:
    response = upload_csv(client, b"a,b\n1,\n,2\n")
    assert response.status_code == 200
    assert response.json()["data"] == [
        {"a": "1", "b": None},
        {"a": None, "b": "2"},
    ]


def test_clean_duplicate_keys_are_dropped_and_counted(client) -> None:
    response = upload_csv(
        client,
        b"id,name\n1,Alice\n 1 ,Alicia\n2,Bob\n",
        dedupe_by="id",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
    assert body["summary"]["rows_in"] == 3
    assert body["summary"]["rows_out"] == 2
    assert body["summary"]["fixes_applied"]["duplicates_removed"] == 1


def test_clean_latin1_upload(client) -> None:
    payload = csv_bytes("name,city\nJosé,Zürich\n", encoding="latin-1")
    response = upload_csv(client, payload)
    assert response.status_code == 200
    assert response.json()["data"] == [{"name": "José", "city": "Zürich"}]


def test_clean_bom_upload(client) -> None:
    response = upload_csv(client, csv_bytes("a,b\n1,2\n", bom=True))
    assert response.status_code == 200
    assert response.json()["data"] == [{"a": "1", "b": "2"}]


def test_clean_unreadable_binary_returns_400(client) -> None:
    response = upload_csv(client, b"a,b\n\x00,2\n")
    assert response.status_code == 400
    assert "binary content" in response.json()["detail"]


def test_clean_missing_file_part_returns_422(client) -> None:
    response = client.post("/clean/csv", data={"output_format": "json"})
    assert response.status_code == 422


def test_clean_invalid_output_format_returns_422(client) -> None:
    response = upload_csv(client, b"a\n1\n", output_format="yaml")
    assert response.status_code == 422


def test_clean_list_options_via_repeated_form_fields(client) -> None:
    # A dict-of-lists form value is sent as repeated fields by the test client.
    response = client.post(
        "/clean/csv",
        files={
            "file": (
                "data.csv",
                b"dob,signup\n15/03/2024,2024-01-15\n",
                "text/csv",
            )
        },
        data={"date_columns": ["dob", "signup"]},
    )
    assert response.status_code == 200
    assert response.json()["data"] == [
        {"dob": "2024-03-15", "signup": "2024-01-15"}
    ]


def test_clean_list_options_via_json_encoded_string(client) -> None:
    response = upload_csv(
        client, b"dob\n15/03/2024\n", date_columns='["dob", "missing"]'
    )
    assert response.status_code == 200
    assert response.json()["data"] == [{"dob": "2024-03-15"}]
    assert response.json()["summary"]["warnings"]["unknown_columns"] == ["missing"]


def test_clean_unknown_columns_are_ignored(client) -> None:
    response = upload_csv(
        client,
        b"a\n1\n",
        date_columns="nope",
        email_columns="nada",
        dedupe_by="ghost",
    )
    assert response.status_code == 200
    # Order follows transform application: emails, dates, then dedupe.
    assert response.json()["summary"]["warnings"]["unknown_columns"] == [
        "nada",
        "nope",
        "ghost",
    ]


def test_clean_options_from_query_string(client) -> None:
    response = client.post(
        "/clean/csv?dedupe_by=id&output_format=json",
        files={"file": ("data.csv", b"id,name\n1,Alice\n1,Alicia\n", "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "1", "name": "Alice"}]


def test_clean_json_reports_malformed_rows_with_reasons(client) -> None:
    response = upload_csv(
        client,
        b"id,name\n1,Alice\n2,Bob,extra\n1,Alicia\n",
        dedupe_by="id",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"id": "1", "name": "Alice"}]
    assert body["quarantined"] == [
        {
            "row_index": 1,
            "record": ["2", "Bob", "extra"],
            "errors": ["malformed row: expected 2 columns, found 3"],
        }
    ]
    summary = body["summary"]
    assert summary["rows_in"] == 3
    assert summary["rows_out"] == 1
    assert summary["rows_dropped"] == 1
    assert summary["warnings"]["malformed_rows"] == 1
    assert summary["rows_in"] == (
        summary["rows_out"] + summary["rows_dropped"] + len(body["quarantined"])
    )


def test_clean_csv_output_has_quarantine_header(client) -> None:
    response = upload_csv(client, b"a,b\n1,2,3\n4,5\n", output_format="csv")
    assert response.status_code == 200
    assert response.headers["x-quarantined-rows"] == "1"
    assert response.text.strip() == "a,b\n4,5"


def test_clean_oversized_upload_rejected_early(client, monkeypatch) -> None:
    from app.services import csv_cleaner

    monkeypatch.setattr(csv_cleaner, "MAX_FILE_BYTES", 32)
    response = upload_csv(client, b"a,b\n" + b"1,2\n" * 20)
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"]


def test_clean_csv_offloads_cpu_work_to_threadpool(client, monkeypatch) -> None:
    import app.routers.clean as clean_router
    from fastapi.concurrency import run_in_threadpool

    offloaded: list[str] = []
    real_run = clean_router.run_in_threadpool

    def spy(func, *args, **kwargs):
        offloaded.append(getattr(func, "__name__", type(func).__name__))
        return real_run(func, *args, **kwargs)

    monkeypatch.setattr(clean_router, "run_in_threadpool", spy)

    response = upload_csv(client, b"a,b\n1,2\n")
    assert response.status_code == 200
    assert response.json()["data"] == [{"a": "1", "b": "2"}]
    assert "clean_csv_bytes" in offloaded
    assert "write_json" in offloaded


def test_clean_cp1252_upload_decodes_smart_quotes(client) -> None:
    payload = "name\n\u201chello\u201d\n".encode("cp1252")
    response = upload_csv(client, payload)
    assert response.status_code == 200
    assert response.json()["data"] == [{"name": "\u201chello\u201d"}]


def test_clean_semicolon_delimited_upload(client) -> None:
    response = upload_csv(
        client,
        b"id;email\n1;A@X.COM\n",
        email_columns=["email"],
    )
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "1", "email": "a@x.com"}]


def test_clean_tab_delimited_upload(client) -> None:
    response = upload_csv(client, b"id\tname\n1\tAlice\n")
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "1", "name": "Alice"}]


def test_clean_delimiter_option(client) -> None:
    response = upload_csv(client, b"id;name\n1;Alice\n", delimiter=";")
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "1", "name": "Alice"}]


def test_clean_invalid_delimiter_returns_422(client) -> None:
    response = upload_csv(client, b"a\n1\n", delimiter=":")
    assert response.status_code == 422


def test_clean_single_column_delimited_header_returns_400(client) -> None:
    response = upload_csv(client, b"id;name\n1;Alice\n", delimiter=",")
    assert response.status_code == 400
    assert "single-column header" in response.json()["detail"]


def test_clean_header_whitespace_does_not_defeat_options(client) -> None:
    response = upload_csv(
        client,
        b" email , dob \n A@X.COM ,15/03/2024\n",
        email_columns=["email"],
        date_columns=["dob"],
    )
    assert response.status_code == 200
    assert response.json()["data"] == [{"email": "a@x.com", "dob": "2024-03-15"}]


def test_clean_duplicate_headers_return_400(client) -> None:
    response = upload_csv(client, b"a,a,b\n1,2,3\n")
    assert response.status_code == 400
    assert "duplicate column headers" in response.json()["detail"]


def test_clean_blank_rows_skipped_and_reported(client) -> None:
    response = upload_csv(client, b"a,b\n1,2\n\n3,4\n")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    summary = body["summary"]
    assert summary["rows_in"] == 3
    assert summary["rows_out"] == 2
    assert summary["rows_dropped"] == 1
    assert summary["blank_rows_skipped"] == 1


def test_clean_csv_output_has_blank_rows_header(client) -> None:
    response = upload_csv(client, b"a,b\n1,2\n\n3,4\n", output_format="csv")
    assert response.status_code == 200
    assert response.headers["x-blank-rows-skipped"] == "1"
    assert response.text.strip() == "a,b\n1,2\n3,4"


def test_clean_json_does_not_materialise_records_with_to_dicts(client, monkeypatch) -> None:
    import polars as pl

    def forbidden(self):
        raise AssertionError("to_dicts should not be called in JSON mode")

    monkeypatch.setattr(pl.DataFrame, "to_dicts", forbidden)
    response = upload_csv(client, b"a,b\n1,2\n")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"].pop("processing_time_ms") >= 0
    assert body == {
        "data": [{"a": "1", "b": "2"}],
        "summary": {
            "rows_in": 1,
            "rows_out": 1,
            "rows_dropped": 0,
            "blank_rows_skipped": 0,
            "fixes_applied": {
                "emails_normalized": 0,
                "dates_normalized": 0,
                "unparseable_dates": 0,
                "duplicates_removed": 0,
            },
            "warnings": {"malformed_rows": 0, "unknown_columns": []},
        },
        "quarantined": [],
    }


def test_clean_dedupe_ignore_case_option(client) -> None:
    payload = b"k,v\nABC,1\nabc,2\n"

    sensitive = upload_csv(client, payload, dedupe_by="k")
    assert sensitive.status_code == 200
    assert len(sensitive.json()["data"]) == 2

    insensitive = upload_csv(
        client, payload, dedupe_by="k", dedupe_ignore_case="true"
    )
    assert insensitive.status_code == 200
    assert insensitive.json()["data"] == [{"k": "ABC", "v": "1"}]
    assert insensitive.json()["summary"]["fixes_applied"]["duplicates_removed"] == 1


def test_clean_date_dayfirst_option(client) -> None:
    payload = b"d\n05/06/2024\n"

    day_first = upload_csv(client, payload, date_columns="d")
    assert day_first.status_code == 200
    assert day_first.json()["data"] == [{"d": "2024-06-05"}]

    month_first = upload_csv(
        client, payload, date_columns="d", date_dayfirst="false"
    )
    assert month_first.status_code == 200
    assert month_first.json()["data"] == [{"d": "2024-05-06"}]


def test_clean_ambiguous_dates_are_reported(client) -> None:
    response = upload_csv(
        client, b"d\n03/04/2024\n", date_columns="d"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"d": "2024-04-03"}]
    assert body["summary"]["fixes_applied"]["ambiguous_dates"] == 1
    assert body["summary"]["rows_in"] == body["summary"]["rows_out"] == 1


def test_clean_repeated_query_params_are_all_kept(client) -> None:
    response = client.post(
        "/clean/csv?date_columns=dob&date_columns=signup&output_format=json",
        files={
            "file": (
                "data.csv",
                b"dob,signup\n15/03/2024,2024-01-15\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"dob": "2024-03-15", "signup": "2024-01-15"}]
    assert body["summary"]["fixes_applied"]["dates_normalized"] == 2


def test_clean_csv_zip_includes_quarantined_rows(client) -> None:
    response = upload_csv(
        client,
        b"a,b\n1,2\n3,4,5\n",
        output_format="csv",
        include_quarantined="true",
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert 'filename="cleaned.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"cleaned.csv", "quarantined.csv"}
        assert archive.read("cleaned.csv") == b"a,b\n1,2\n"
        details = archive.read("quarantined.csv").decode("utf-8")
    rows = list(csv.reader(io.StringIO(details)))
    assert rows[0] == ["row_index", "record", "errors"]
    assert rows[1][0] == "1"
    assert json.loads(rows[1][1]) == ["3", "4", "5"]
    assert "malformed row" in rows[1][2]


def test_clean_csv_output_escapes_formulas_but_json_does_not(client) -> None:
    csv_response = upload_csv(client, b"v\n=1+1\n", output_format="csv")
    assert csv_response.status_code == 200
    assert csv_response.text.strip() == "v\n'=1+1"

    json_response = upload_csv(client, b"v\n=1+1\n")
    assert json_response.json()["data"] == [{"v": "=1+1"}]


def test_clean_csv_output_has_warnings_header(client) -> None:
    response = upload_csv(client, b"a,b\n1,2,3\n", output_format="csv")
    assert response.status_code == 200
    assert json.loads(response.headers["x-warnings"]) == {
        "malformed_rows": 1,
        "unknown_columns": [],
    }


def test_read_upload_limited_rejects_oversized_file_size(monkeypatch) -> None:
    from app.services import csv_cleaner

    class FakeUpload:
        size = 100

        async def read(self, size: int) -> bytes:
            return b"x" * size

    monkeypatch.setattr(csv_cleaner, "MAX_FILE_BYTES", 10)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_upload_limited(FakeUpload()))
    assert exc.value.status_code == 413


def test_clean_json_reports_processing_time(client) -> None:
    response = upload_csv(client, b"a,b\n1,2\n")
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["processing_time_ms"] >= 0
    assert response.headers["x-processing-time-ms"] == str(
        summary["processing_time_ms"]
    )


def test_clean_csv_output_reports_processing_time(client) -> None:
    response = upload_csv(client, b"a,b\n1,2\n", output_format="csv")
    assert response.status_code == 200
    timing = float(response.headers["x-processing-time-ms"])
    assert timing >= 0

    zipped = upload_csv(
        client,
        b"a,b\n1,2\n3,4,5\n",
        output_format="csv",
        include_quarantined="true",
    )
    assert zipped.status_code == 200
    assert float(zipped.headers["x-processing-time-ms"]) >= 0


def test_demo_messy_leads_json_mode(client) -> None:
    response = client.post("/demo/messy-leads")
    assert response.status_code == 200
    body = response.json()
    summary = body["summary"]
    assert summary["processing_time_ms"] >= 0
    assert response.headers["x-processing-time-ms"] == str(
        summary["processing_time_ms"]
    )
    assert summary["rows_in"] == 7
    assert summary["rows_out"] == 3
    assert summary["rows_dropped"] == 3
    assert len(body["quarantined"]) == 1
    assert summary["rows_in"] == (
        summary["rows_out"] + summary["rows_dropped"] + len(body["quarantined"])
    )
    assert [row["email"] for row in body["data"]] == [
        "ada@example.com",
        "grace@navy.mil",
        None,
    ]


def test_demo_messy_leads_accepts_form_output_format(client) -> None:
    response = client.post("/demo/messy-leads", data={"output_format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert float(response.headers["x-processing-time-ms"]) >= 0
    assert response.headers["x-rows-in"] == "7"
    assert response.headers["x-quarantined-rows"] == "1"
    assert "full_name,email,created_at,company" in response.text


def test_demo_messy_leads_query_output_format_matches_clean_csv(client) -> None:
    demo_response = client.post("/demo/messy-leads?output_format=csv")
    clean_response = upload_csv(
        client,
        FALLBACK_MESSY_LEADS_CSV.encode("utf-8"),
        dedupe_by="email",
        dedupe_ignore_case="true",
        date_columns=["created_at"],
        email_columns=["email"],
        output_format="csv",
    )
    assert demo_response.status_code == clean_response.status_code == 200
    assert demo_response.text == clean_response.text
    for header in (
        "x-rows-in",
        "x-rows-out",
        "x-rows-dropped",
        "x-quarantined-rows",
        "x-fixes-applied",
        "x-warnings",
    ):
        assert demo_response.headers[header] == clean_response.headers[header]


def test_demo_messy_leads_invalid_output_format_returns_422(client) -> None:
    response = client.post("/demo/messy-leads?output_format=yaml")
    assert response.status_code == 422


def test_demo_messy_leads_falls_back_when_sample_file_missing(client, monkeypatch, tmp_path) -> None:
    import app.routers.demo as demo_router

    monkeypatch.setattr(demo_router, "DEMO_CSV_PATH", tmp_path / "missing.csv")
    response = client.post("/demo/messy-leads")
    assert response.status_code == 200
    assert response.json()["summary"]["rows_in"] == 7


def test_demo_messy_leads_reads_repo_sample_when_present(client, monkeypatch, tmp_path) -> None:
    import app.routers.demo as demo_router

    sample = tmp_path / "test_messy_leads.csv"
    sample.write_bytes(b"x,y\n1,2\n")
    monkeypatch.setattr(demo_router, "DEMO_CSV_PATH", sample)
    response = client.post("/demo/messy-leads")
    assert response.status_code == 200
    assert response.json()["data"] == [{"x": "1", "y": "2"}]


def test_clean_phone_columns_accept_comma_string_and_repeated_fields(client) -> None:
    payload = b"phone,mobile\n(415) 555-2671,202-555-2671\nbad,(415) 555-2671\n"
    comma = upload_csv(client, payload, phone_columns="phone,mobile")
    repeated = client.post(
        "/clean/csv",
        files={"file": ("data.csv", payload, "text/csv")},
        data={"phone_columns": ["phone", "mobile"]},
    )
    assert comma.status_code == repeated.status_code == 200
    comma_body, repeated_body = comma.json(), repeated.json()
    for payload_body in (comma_body, repeated_body):
        payload_body["summary"].pop("processing_time_ms")
    assert comma_body == repeated_body
    body = comma.json()
    assert body["data"][0] == {
        "phone": "+14155552671",
        "mobile": "+12025552671",
    }
    assert body["quarantined"][0]["record"] == ["bad", "(415) 555-2671"]
    assert body["summary"]["fixes_applied"]["phones_normalized"] == 2


def test_clean_default_country_code_is_case_insensitive(client) -> None:
    response = upload_csv(
        client,
        b"phone\n020 7946 0958\n",
        phone_columns="phone",
        default_country_code="gb",
    )
    assert response.status_code == 200
    assert response.json()["data"] == [{"phone": "+442079460958"}]

    invalid = upload_csv(
        client,
        b"phone\nnope\n",
        phone_columns="phone",
        default_country_code="gb",
    )
    assert invalid.json()["quarantined"][0]["errors"] == [
        "Invalid phone number for region GB: nope"
    ]


def test_clean_preset_via_form_and_query(client) -> None:
    payload = (
        b"Email,Phone Number,Create Date\n"
        b" A@X.COM ,(415) 555-2671,15/03/2024\n"
        b"a@x.com,202-555-2671,2024-02-01\n"
    )
    via_form = upload_csv(client, payload, preset="hubspot")
    via_query = client.post(
        "/clean/csv?preset=hubspot",
        files={"file": ("data.csv", payload, "text/csv")},
    )
    assert via_form.status_code == via_query.status_code == 200
    form_body, query_body = via_form.json(), via_query.json()
    for payload_body in (form_body, query_body):
        payload_body["summary"].pop("processing_time_ms")
    assert form_body == query_body
    body = via_form.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["Email"] == "a@x.com"
    assert body["data"][0]["Phone Number"] == "+14155552671"
    assert body["data"][0]["Create Date"] == "2024-03-15"
    assert body["summary"]["fixes_applied"]["duplicates_removed"] == 1


def test_clean_preset_does_not_override_explicit_options(client) -> None:
    payload = b"Email,Contact Email\nA@X.COM,b@y.com\n"
    response = upload_csv(
        client, payload, preset="hubspot", email_columns="Missing Column"
    )
    assert response.status_code == 200
    body = response.json()
    # The explicit (unknown) column wins; the preset must not add "Email".
    assert body["data"] == [{"Email": "A@X.COM", "Contact Email": "b@y.com"}]
    assert body["summary"]["warnings"]["unknown_columns"] == ["Missing Column"]


def test_clean_zip_bundle_contains_three_files_and_reconciles(client) -> None:
    payload = b"email,phone,v\nA@X.COM,(415) 555-2671,=1+1\nbad,badphone,x\n"
    response = upload_csv(
        client, payload, output_format="zip", phone_columns="phone"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert 'filename="cleaned_bundle.zip"' in response.headers["content-disposition"]
    assert float(response.headers["x-processing-time-ms"]) >= 0

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "cleaned.csv",
            "quarantined.csv",
            "summary.json",
        }
        summary = json.loads(archive.read("summary.json"))
        assert summary["rows_in"] == 2
        assert summary["rows_out"] == 1
        assert summary["rows_dropped"] == 0
        assert summary["rows_in"] == (
            summary["rows_out"] + summary["rows_dropped"] + 1
        )
        assert "'=1+1" in archive.read("cleaned.csv").decode("utf-8")
        quarantine_rows = list(
            csv.reader(io.StringIO(archive.read("quarantined.csv").decode("utf-8")))
        )
    assert quarantine_rows[0] == ["row_index", "errors", "email", "phone", "v"]
    assert quarantine_rows[1] == [
        "1",
        "Invalid phone number for region US: badphone",
        "bad",
        "badphone",
        "x",
    ]


def test_clean_zip_bundle_quarantined_file_is_header_only_when_empty(client) -> None:
    response = upload_csv(client, b"a,b\n1,2\n", output_format="zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("quarantined.csv") == b"row_index,errors,a,b\n"
        summary = json.loads(archive.read("summary.json"))
        assert summary["rows_in"] == summary["rows_out"] == 1


def test_clean_empty_file_with_phone_options_in_zip_mode(client) -> None:
    response = upload_csv(client, b"", phone_columns="phone", output_format="zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "cleaned.csv",
            "quarantined.csv",
            "summary.json",
        }
        assert json.loads(archive.read("summary.json"))["rows_in"] == 0


def test_demo_messy_leads_zip_mode(client) -> None:
    response = client.post("/demo/messy-leads?output_format=zip")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "cleaned.csv",
            "quarantined.csv",
            "summary.json",
        }
        summary = json.loads(archive.read("summary.json"))
        assert summary["rows_in"] == 7
        assert summary["rows_out"] == 3


# --------------------------------------------------------------------------- #
# Company normalization / fuzzy deduplication
# --------------------------------------------------------------------------- #
def test_clean_company_columns_accept_comma_string_and_repeated_fields(client) -> None:
    payload = b"company,name\nAcme Inc.,A\nBeta LLC,B\n"
    comma = upload_csv(client, payload, company_columns="company,ghost")
    repeated = client.post(
        "/clean/csv",
        files={"file": ("data.csv", payload, "text/csv")},
        data={"company_columns": ["company", "ghost"]},
    )
    assert comma.status_code == repeated.status_code == 200
    comma_body, repeated_body = comma.json(), repeated.json()
    for payload_body in (comma_body, repeated_body):
        payload_body["summary"].pop("processing_time_ms")
    assert comma_body == repeated_body

    body = comma.json()
    assert body["data"] == [
        {"company": "Acme", "name": "A"},
        {"company": "Beta", "name": "B"},
    ]
    assert body["summary"]["fixes_applied"]["companies_normalized"] == 2
    assert body["summary"]["warnings"]["unknown_columns"] == ["ghost"]


def test_clean_company_columns_via_repeated_query_params(client) -> None:
    response = client.post(
        "/clean/csv?company_columns=company&company_columns=ghost",
        files={
            "file": (
                "data.csv",
                b"company\nAcme Inc.\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"company": "Acme"}]
    assert body["summary"]["warnings"]["unknown_columns"] == ["ghost"]


def test_clean_fuzzy_dedupe_collapses_entity_variants_and_reconciles(client) -> None:
    payload = (
        b'company,email\n"ACME, Inc.",a@x.com\nAcme Corp,b@x.com\n,c@x.com\n'
    )
    via_form = upload_csv(
        client, payload, dedupe_by="company", fuzzy_dedupe="true"
    )
    via_query = client.post(
        "/clean/csv?dedupe_by=company&fuzzy_dedupe=true",
        files={"file": ("data.csv", payload, "text/csv")},
    )
    assert via_form.status_code == via_query.status_code == 200
    form_body, query_body = via_form.json(), via_query.json()
    for payload_body in (form_body, query_body):
        payload_body["summary"].pop("processing_time_ms")
    assert form_body == query_body

    body = via_form.json()
    assert body["data"] == [
        {"company": "ACME, Inc.", "email": "a@x.com"},
        {"company": None, "email": "c@x.com"},
    ]
    summary = body["summary"]
    assert summary["rows_in"] == 3
    assert summary["rows_out"] == 2
    assert summary["rows_dropped"] == 1
    assert summary["fixes_applied"]["duplicates_removed"] == 1
    assert summary["rows_in"] == (
        summary["rows_out"] + summary["rows_dropped"] + len(body["quarantined"])
    )


def test_clean_fuzzy_dedupe_disabled_keeps_entity_variants(client) -> None:
    payload = b'company\n"ACME, Inc."\nAcme Corp\n\n'
    sensitive = upload_csv(client, payload, dedupe_by="company")
    fuzzy = upload_csv(
        client, payload, dedupe_by="company", fuzzy_dedupe="true"
    )
    assert sensitive.status_code == fuzzy.status_code == 200
    assert len(sensitive.json()["data"]) == 2  # trailing blank row is skipped
    assert len(fuzzy.json()["data"]) == 1


def test_clean_preset_maps_company_column(client) -> None:
    payload = (
        b"Email,Company Name\n"
        b"A@X.COM,Acme Inc.\n"
        b"a@x.com,Acme LLC\n"
    )
    response = upload_csv(client, payload, preset="hubspot")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [{"Email": "a@x.com", "Company Name": "Acme"}]
    assert body["summary"]["fixes_applied"]["companies_normalized"] == 2


def test_clean_invalid_fuzzy_dedupe_returns_422(client) -> None:
    response = upload_csv(client, b"a\n1\n", fuzzy_dedupe="maybe")
    assert response.status_code == 422

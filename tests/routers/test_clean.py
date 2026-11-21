"""Endpoint tests for POST /clean/csv."""

import json

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
    }
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
        "malformed_rows": 0,
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
    # Build a raw multipart body so the same form key is sent twice.
    boundary = "----repeated-field-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="data.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        "dob,signup\n15/03/2024,2024-01-15\n"
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="date_columns"\r\n\r\n'
        "dob\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="date_columns"\r\n\r\n'
        "signup\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    response = client.post(
        "/clean/csv",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
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
    assert response.json()["summary"]["fixes_applied"]["unknown_columns_ignored"] == 1


def test_clean_unknown_columns_are_ignored(client) -> None:
    response = upload_csv(
        client,
        b"a\n1\n",
        date_columns="nope",
        email_columns="nada",
        dedupe_by="ghost",
    )
    assert response.status_code == 200
    assert response.json()["summary"]["fixes_applied"]["unknown_columns_ignored"] == 3


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
    assert summary["fixes_applied"]["malformed_rows"] == 1
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
    assert response.json() == {
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
                "malformed_rows": 0,
            },
        },
        "quarantined": [],
    }

"""Endpoint tests for POST /validate/json."""

import inspect

from app.routers.validate import validate_json

FIELD_RULES = {
    "email": {"required": True, "type": "email"},
    "age": {"type": "integer"},
    "joined": {"type": "date"},
}


def test_validate_endpoint_valid_payload_shape(client) -> None:
    payload = {
        "records": [
            {"email": "alice@example.com", "age": 30, "joined": "2024-01-15"}
        ],
        "field_rules": FIELD_RULES,
    }
    response = client.post("/validate/json", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] == payload["records"]
    assert body["quarantined"] == []
    assert body["summary"]["rows_in"] == 1
    assert body["summary"]["rows_out"] == 1
    assert body["summary"]["rows_dropped"] == 0
    assert body["summary"]["fixes_applied"] == {}
    assert body["summary"]["errors_by_field"] == {}


def test_validate_endpoint_quarantine_structure(client) -> None:
    payload = {
        "records": [{"email": "bad", "age": "old", "joined": "not a date"}],
        "field_rules": FIELD_RULES,
    }
    response = client.post("/validate/json", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] == []
    assert len(body["quarantined"]) == 1
    row = body["quarantined"][0]
    assert row["row_index"] == 0
    assert row["record"] == payload["records"][0]
    assert len(row["errors"]) == 3
    assert body["summary"]["rows_dropped"] == 1
    assert body["summary"]["quarantined_count"] == 1


def test_validate_endpoint_empty_array(client) -> None:
    response = client.post(
        "/validate/json", json={"records": [], "field_rules": FIELD_RULES}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] == []
    assert body["quarantined"] == []
    assert body["summary"]["rows_in"] == 0
    assert body["summary"]["valid_count"] == 0


def test_validate_endpoint_missing_values_and_duplicates(client) -> None:
    payload = {
        "records": [
            {"email": "a@b.com"},
            {"email": "a@b.com"},
            {},
        ],
        "field_rules": {"email": {"required": True, "type": "email"}},
    }
    response = client.post("/validate/json", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert len(body["valid"]) == 2
    assert [row["row_index"] for row in body["quarantined"]] == [2]
    assert body["summary"]["errors_by_field"] == {"email": 1}


def test_validate_endpoint_malformed_body_returns_422(client) -> None:
    response = client.post(
        "/validate/json",
        json={"records": "not-a-list", "field_rules": {}},
    )
    assert response.status_code == 422


def test_validate_endpoint_unknown_rule_key_returns_422(client) -> None:
    response = client.post(
        "/validate/json",
        json={
            "records": [],
            "field_rules": {"email": {"required": True, "typo": "email"}},
        },
    )
    assert response.status_code == 422


def test_validate_endpoint_unknown_top_level_key_returns_422(client) -> None:
    response = client.post(
        "/validate/json",
        json={"records": [], "field_rules": {}, "extra": 1},
    )
    assert response.status_code == 422


def test_validate_json_handler_is_synchronous() -> None:
    # A plain def makes FastAPI run the handler in a threadpool instead of
    # blocking the event loop with synchronous validation work.
    assert not inspect.iscoroutinefunction(validate_json)

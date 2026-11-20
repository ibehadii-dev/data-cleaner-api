"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def csv_bytes(text: str, encoding: str = "utf-8", bom: bool = False) -> bytes:
    """Build CSV bytes with the requested encoding and optional UTF-8 BOM."""
    payload = text.encode(encoding)
    return (b"\xef\xbb\xbf" if bom else b"") + payload


def upload_csv(client: TestClient, payload: bytes, **options: object):
    """POST CSV bytes as a multipart file with the given cleaning options."""
    return client.post(
        "/clean/csv",
        files={"file": ("data.csv", payload, "text/csv")},
        data=options,
    )

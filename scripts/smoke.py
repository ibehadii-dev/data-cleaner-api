"""Live smoke test: boot uvicorn and exercise both endpoints over real HTTP.

Run from anywhere with ``python scripts/smoke.py``.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_healthy(
    base_url: str, process: subprocess.Popen[bytes], timeout: float = 20.0
) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=base_url, timeout=1.0) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"uvicorn exited early with code {process.returncode}")
            try:
                if client.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise TimeoutError(f"server did not become healthy within {timeout}s")


def main() -> int:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_healthy(base_url, process)
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text
            assert health.json() == {"status": "ok"}

            clean = client.post(
                "/clean/csv",
                files={
                    "file": (
                        "data.csv",
                        b"id,email,joined\n"
                        b"1, A@X.COM ,15/03/2024\n"
                        b"2,b@y.com,not a date\n"
                        b"3,c@z.com,extra,more\n",
                        "text/csv",
                    )
                },
                data={
                    "dedupe_by": "id",
                    "email_columns": "email",
                    "date_columns": "joined",
                },
            )
            assert clean.status_code == 200, clean.text
            body = clean.json()
            assert body["data"] == [
                {"id": "1", "email": "a@x.com", "joined": "2024-03-15"},
                {"id": "2", "email": "b@y.com", "joined": "not a date"},
            ]
            assert body["quarantined"] == [
                {
                    "row_index": 2,
                    "record": ["3", "c@z.com", "extra", "more"],
                    "errors": ["malformed row: expected 3 columns, found 4"],
                }
            ]
            summary = body["summary"]
            assert summary["rows_in"] == 3
            assert summary["rows_out"] == 2
            assert summary["rows_dropped"] == 0
            assert summary["warnings"]["malformed_rows"] == 1
            assert summary["warnings"]["unknown_columns"] == []
            assert summary["fixes_applied"]["unparseable_dates"] == 1
            assert summary["rows_in"] == (
                summary["rows_out"] + summary["rows_dropped"] + len(body["quarantined"])
            )

            validate = client.post(
                "/validate/json",
                json={
                    "records": [
                        {"email": "alice@example.com", "age": "30"},
                        {"email": "bad", "age": "abc"},
                    ],
                    "field_rules": {
                        "email": {"required": True, "type": "email"},
                        "age": {"type": "integer"},
                    },
                },
            )
            assert validate.status_code == 200, validate.text
            result = validate.json()
            assert result["summary"]["valid_count"] == 1
            assert result["summary"]["quarantined_count"] == 1
            assert result["summary"]["rows_dropped"] == 0
            assert result["summary"]["warnings"] == {
                "malformed_rows": 0,
                "unknown_columns": [],
            }
            assert result["summary"]["fixes_applied"] == {}

        print(f"smoke OK: /health, /clean/csv, /validate/json on {base_url}")
        return 0
    except Exception as exc:
        print(f"smoke FAILED: {exc}", file=sys.stderr)
        if process.poll() is not None and process.stdout:
            print(
                process.stdout.read().decode("utf-8", errors="replace"),
                file=sys.stderr,
            )
        return 1
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()


if __name__ == "__main__":
    raise SystemExit(main())

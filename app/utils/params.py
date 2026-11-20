"""Helpers for normalising option values supplied via forms or query strings."""

from __future__ import annotations

import json
from typing import Any, Iterator


def _flatten(value: Any) -> Iterator[str]:
    """Yield individual, trimmed string items from any supported option shape.

    Supports repeated form fields (a list), comma-separated strings, and
    JSON-encoded arrays such as ``'["dob", "signup"]'``.
    """
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
        return

    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                yield from _flatten(parsed)
                return
            text = text[1:-1]
        for part in text.split(","):
            part = part.strip()
            if part:
                yield part
        return

    if value is not None:
        item = str(value).strip()
        if item:
            yield item


def normalize_list_field(value: Any) -> list[str] | None:
    """Return a de-duplicated list of option names, or ``None`` if absent."""
    if value is None:
        return None
    result: list[str] = []
    for item in _flatten(value):
        if item not in result:
            result.append(item)
    return result

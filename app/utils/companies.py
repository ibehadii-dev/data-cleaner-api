"""Company-name normalization and fuzzy entity keys (standard library only)."""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")

# Legal suffixes are matched at the *end* of a name, case-insensitively, and
# only after a separator (comma or whitespace) so a partial word never matches.
# Longer alternatives come first for readability; the end anchor makes the
# alternation unambiguous.
_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:"
    r"incorporated|inc\.?|"
    r"l\.l\.c\.?|llc|limited|ltd\.?|"
    r"corporation|corp\.?|co\.?|"
    r"plc|gmbh|"
    r"pvt\.?\s+ltd\.?"
    r")[,\s.]*$",
    re.IGNORECASE,
)

_PUNCTUATION_RE = re.compile(r"[.,\-&'\"()]")


def _as_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else str(value).strip()


def _strip_legal_suffixes(text: str) -> str:
    """Drop trailing commas/periods and any trailing legal entity suffixes."""
    for _ in range(3):  # e.g. "Acme Inc LLC" sheds both suffixes
        previous = text
        text = text.rstrip(" ,.")
        text = _LEGAL_SUFFIX_RE.sub("", text).rstrip(" ,.")
        if text == previous:
            break
    return text


def _is_acronym(word: str) -> bool:
    """Short all-caps tokens (IBM, AWS, SAP, ...) survive Title Casing."""
    return len(word) <= 4 and word.isupper() and any(ch.isalpha() for ch in word)


def normalize_company_name(value: str | None) -> tuple[str | None, bool]:
    """Return ``(cleaned, was_changed)`` for one company-name value.

    Blank/null values map to ``(None, False)``. Other values are whitespace-
    collapsed, have trailing commas/periods and legal suffixes removed, and
    all-lowercase or ALL-CAPS multi-word names are converted to Title Case
    while short uppercase acronyms are preserved.
    """
    raw = _as_text(value)
    if not raw:
        return None, False

    text = _WS_RE.sub(" ", raw)
    words = text.split(" ")
    letters = [ch for ch in text if ch.isalpha()]
    if len(words) > 1 and letters and (
        all(ch.islower() for ch in letters) or all(ch.isupper() for ch in letters)
    ):
        text = " ".join(word if _is_acronym(word) else word.title() for word in words)

    cleaned = _strip_legal_suffixes(text)
    return cleaned, cleaned != raw


def fuzzy_entity_key(value: str | None) -> str | None:
    """Return a comparable entity key, or ``None`` for blank/null values.

    Legal suffixes and punctuation are removed before whitespace is collapsed
    and the result is lowercased, so ``"ACME, Inc."`` and ``"Acme Corp"`` both
    produce ``"acme"``.
    """
    raw = _as_text(value)
    if not raw:
        return None

    text = _WS_RE.sub(" ", raw)
    text = _strip_legal_suffixes(text)  # handles dotted suffixes such as L.L.C.
    text = _PUNCTUATION_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    text = _strip_legal_suffixes(text).strip().lower()  # e.g. "Acme-Corp"
    return text or None

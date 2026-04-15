from __future__ import annotations

import re
import unicodedata


def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_identity_text(text: str | None, *, keep_hyphens: bool = False) -> str:
    if not text:
        return ""

    ascii_text = _strip_diacritics(text)
    pattern = r"[^a-z0-9\s-]+" if keep_hyphens else r"[^a-z0-9\s]+"
    cleaned = re.sub(pattern, " ", ascii_text.lower().replace("_", " "))
    return " ".join(cleaned.split())


def tokenize_identity_text(text: str | None, *, keep_hyphens: bool = False) -> list[str]:
    normalized = normalize_identity_text(text, keep_hyphens=keep_hyphens)
    if not normalized:
        return []

    tokens = normalized.split()
    if not keep_hyphens:
        return tokens
    return [token.strip("-") for token in tokens if token.strip("-")]


def compact_identity_text(text: str | None) -> str:
    return "".join(
        token.replace("-", "")
        for token in tokenize_identity_text(text, keep_hyphens=True)
    )

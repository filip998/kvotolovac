from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ..config import settings
from .text_normalizer import normalize_identity_text


@dataclass(frozen=True)
class LeagueResolution:
    league_id: str
    display_name: str
    country: str | None
    source: str
    is_known: bool


@dataclass(frozen=True)
class LeagueRegistry:
    canonical_leagues: dict[str, dict[str, str | None]]
    aliases: dict[str, str]
    bookmaker_aliases: dict[str, dict[str, str]]


def _registry_path() -> Path:
    return Path(settings.league_registry_path)


def _humanize_league_id(league_id: str) -> str:
    if not league_id:
        return ""
    parts = league_id.replace("-", "_").split("_")
    formatted: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) <= 4 and part.isalpha():
            formatted.append(part.upper())
        else:
            formatted.append(part.capitalize())
    return " ".join(formatted)


def _normalize_canonical_league_id(value: str | None) -> str:
    return normalize_identity_text(value).replace(" ", "_")


def _default_registry_payload() -> dict[str, Any]:
    return {
        "canonical_leagues": {},
        "aliases": {},
        "bookmaker_aliases": {},
    }


def _read_registry_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_registry_payload()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return _default_registry_payload()
    return {
        "canonical_leagues": data.get("canonical_leagues", {}),
        "aliases": data.get("aliases", {}),
        "bookmaker_aliases": data.get("bookmaker_aliases", {}),
    }


@contextmanager
def _registry_lock(path: Path):
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@lru_cache(maxsize=1)
def load_league_registry() -> LeagueRegistry:
    payload = _read_registry_payload(_registry_path())
    canonical_leagues: dict[str, dict[str, str | None]] = {}
    for raw_id, raw_meta in payload["canonical_leagues"].items():
        league_id = _normalize_canonical_league_id(raw_id)
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        canonical_leagues[league_id] = {
            "name": str(meta.get("name") or _humanize_league_id(league_id)),
            "country": meta.get("country"),
        }

    aliases = {
        normalize_identity_text(raw_alias): _normalize_canonical_league_id(raw_target)
        for raw_alias, raw_target in payload["aliases"].items()
        if raw_alias and raw_target
    }
    bookmaker_aliases = {
        normalize_identity_text(bookmaker_id): {
            normalize_identity_text(raw_alias): _normalize_canonical_league_id(raw_target)
            for raw_alias, raw_target in raw_aliases.items()
            if raw_alias and raw_target
        }
        for bookmaker_id, raw_aliases in payload["bookmaker_aliases"].items()
        if isinstance(raw_aliases, dict)
    }
    return LeagueRegistry(
        canonical_leagues=canonical_leagues,
        aliases=aliases,
        bookmaker_aliases=bookmaker_aliases,
    )


def clear_league_registry_cache() -> None:
    load_league_registry.cache_clear()


def resolve_league(raw_league_id: str | None, bookmaker_id: str | None = None) -> LeagueResolution:
    registry = load_league_registry()
    raw_key = normalize_identity_text(raw_league_id)
    canonical_candidate = _normalize_canonical_league_id(raw_league_id)
    bookmaker_key = normalize_identity_text(bookmaker_id) if bookmaker_id else ""

    if bookmaker_key and raw_key in registry.bookmaker_aliases.get(bookmaker_key, {}):
        league_id = registry.bookmaker_aliases[bookmaker_key][raw_key]
        source = "bookmaker_alias"
    elif raw_key in registry.aliases:
        league_id = registry.aliases[raw_key]
        source = "alias"
    elif canonical_candidate in registry.canonical_leagues:
        league_id = canonical_candidate
        source = "canonical"
    else:
        league_id = canonical_candidate
        source = "unknown"

    meta = registry.canonical_leagues.get(league_id, {})
    display_name = str(meta.get("name") or _humanize_league_id(league_id))
    country = meta.get("country")
    is_known = source != "unknown" or league_id in registry.canonical_leagues
    return LeagueResolution(
        league_id=league_id,
        display_name=display_name,
        country=country if isinstance(country, str) else None,
        source=source,
        is_known=is_known,
    )


def league_display_name(league_id: str | None) -> str:
    return resolve_league(league_id).display_name


def league_country(league_id: str | None) -> str | None:
    return resolve_league(league_id).country


def remember_bookmaker_league_alias(
    bookmaker_id: str,
    raw_league_id: str,
    league_id: str,
) -> LeagueResolution:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    bookmaker_key = normalize_identity_text(bookmaker_id)
    raw_key = normalize_identity_text(raw_league_id)
    normalized_league_id = _normalize_canonical_league_id(league_id)

    with _registry_lock(path):
        clear_league_registry_cache()
        payload = _read_registry_payload(path)
        existing_resolution = resolve_league(normalized_league_id)

        canonical_leagues = payload.setdefault("canonical_leagues", {})
        if normalized_league_id not in canonical_leagues:
            canonical_leagues[normalized_league_id] = {
                "name": existing_resolution.display_name or _humanize_league_id(normalized_league_id),
                "country": existing_resolution.country,
            }

        bookmaker_aliases = payload.setdefault("bookmaker_aliases", {})
        bookmaker_aliases.setdefault(bookmaker_key, {})[raw_key] = normalized_league_id

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".json",
        ) as tmp_file:
            json.dump(payload, tmp_file, indent=2, sort_keys=True)
            tmp_file.write("\n")
            temp_path = Path(tmp_file.name)

        temp_path.replace(path)
    clear_league_registry_cache()
    return resolve_league(normalized_league_id, bookmaker_id=bookmaker_id)

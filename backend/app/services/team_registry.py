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


class CircularAliasError(Exception):
    """Raised when saving an alias would create a cycle."""


@dataclass(frozen=True)
class TeamAliasResolution:
    team_name: str
    source: str


@dataclass(frozen=True)
class TeamRegistry:
    aliases: dict[str, str]
    bookmaker_aliases: dict[str, dict[str, str]]
    competition_aliases: dict[str, dict[str, str]]
    bookmaker_competition_aliases: dict[str, dict[str, dict[str, str]]]


def _registry_path() -> Path:
    return Path(settings.team_registry_path)


def _normalize_competition_key(value: str | None) -> str:
    return normalize_identity_text(value).replace(" ", "_")


def _default_registry_payload() -> dict[str, Any]:
    return {
        "aliases": {},
        "bookmaker_aliases": {},
        "competition_aliases": {},
        "bookmaker_competition_aliases": {},
    }


def _read_registry_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_registry_payload()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return _default_registry_payload()
    return {
        "aliases": data.get("aliases", {}),
        "bookmaker_aliases": data.get("bookmaker_aliases", {}),
        "competition_aliases": data.get("competition_aliases", {}),
        "bookmaker_competition_aliases": data.get("bookmaker_competition_aliases", {}),
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
def load_team_registry() -> TeamRegistry:
    payload = _read_registry_payload(_registry_path())
    aliases = {
        normalize_identity_text(raw_alias): str(raw_target)
        for raw_alias, raw_target in payload["aliases"].items()
        if raw_alias and raw_target
    }
    bookmaker_aliases = {
        normalize_identity_text(bookmaker_id): {
            normalize_identity_text(raw_alias): str(raw_target)
            for raw_alias, raw_target in raw_aliases.items()
            if raw_alias and raw_target
        }
        for bookmaker_id, raw_aliases in payload["bookmaker_aliases"].items()
        if isinstance(raw_aliases, dict)
    }
    competition_aliases = {
        _normalize_competition_key(competition_id): {
            normalize_identity_text(raw_alias): str(raw_target)
            for raw_alias, raw_target in raw_aliases.items()
            if raw_alias and raw_target
        }
        for competition_id, raw_aliases in payload["competition_aliases"].items()
        if isinstance(raw_aliases, dict)
    }
    bookmaker_competition_aliases = {
        normalize_identity_text(bookmaker_id): {
            _normalize_competition_key(competition_id): {
                normalize_identity_text(raw_alias): str(raw_target)
                for raw_alias, raw_target in raw_aliases.items()
                if raw_alias and raw_target
            }
            for competition_id, raw_aliases in competition_aliases.items()
            if isinstance(raw_aliases, dict)
        }
        for bookmaker_id, competition_aliases in payload["bookmaker_competition_aliases"].items()
        if isinstance(competition_aliases, dict)
    }
    return TeamRegistry(
        aliases=aliases,
        bookmaker_aliases=bookmaker_aliases,
        competition_aliases=competition_aliases,
        bookmaker_competition_aliases=bookmaker_competition_aliases,
    )


def clear_team_registry_cache() -> None:
    load_team_registry.cache_clear()


def resolve_team_alias(
    raw_team_name: str | None,
    *,
    bookmaker_id: str | None = None,
    competition_id: str | None = None,
) -> TeamAliasResolution | None:
    raw_key = normalize_identity_text(raw_team_name)
    if not raw_key:
        return None

    registry = load_team_registry()
    bookmaker_key = normalize_identity_text(bookmaker_id) if bookmaker_id else ""
    competition_key = _normalize_competition_key(competition_id)
    current_key = raw_key
    current_team_name = str(raw_team_name).strip()
    source: str | None = None
    seen_keys: set[str] = set()

    while current_key and current_key not in seen_keys:
        seen_keys.add(current_key)
        next_team_name: str | None = None
        next_source: str | None = None

        if bookmaker_key and competition_key:
            bookmaker_competition = registry.bookmaker_competition_aliases.get(bookmaker_key, {})
            if current_key in bookmaker_competition.get(competition_key, {}):
                next_team_name = bookmaker_competition[competition_key][current_key]
                next_source = "bookmaker_competition_alias"

        if next_team_name is None and competition_key:
            competition_aliases = registry.competition_aliases.get(competition_key, {})
            if current_key in competition_aliases:
                next_team_name = competition_aliases[current_key]
                next_source = "competition_alias"

        if next_team_name is None and bookmaker_key:
            bookmaker_aliases = registry.bookmaker_aliases.get(bookmaker_key, {})
            if current_key in bookmaker_aliases:
                next_team_name = bookmaker_aliases[current_key]
                next_source = "bookmaker_alias"

        if next_team_name is None and current_key in registry.aliases:
            next_team_name = registry.aliases[current_key]
            next_source = "alias"

        if next_team_name is None:
            break

        source = source or next_source
        current_team_name = next_team_name
        current_key = normalize_identity_text(next_team_name)

    if source is None:
        return None

    return TeamAliasResolution(team_name=current_team_name, source=source)


def remember_team_alias(
    *,
    bookmaker_id: str,
    raw_team_name: str,
    team_name: str,
    competition_id: str | None = None,
) -> TeamAliasResolution:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    bookmaker_key = normalize_identity_text(bookmaker_id)
    raw_key = normalize_identity_text(raw_team_name)
    competition_key = _normalize_competition_key(competition_id)
    target_team_name = team_name.strip()

    if not raw_key or raw_key == normalize_identity_text(target_team_name):
        return TeamAliasResolution(
            team_name=target_team_name,
            source="bookmaker_competition_alias" if competition_key else "bookmaker_alias",
        )

    with _registry_lock(path):
        clear_team_registry_cache()

        # Check for circular aliases inside the lock with fresh registry state
        existing_resolution = resolve_team_alias(
            team_name,
            bookmaker_id=bookmaker_id,
            competition_id=competition_id,
        )
        if existing_resolution is not None:
            resolved_key = normalize_identity_text(existing_resolution.team_name)
            if resolved_key == raw_key:
                raise CircularAliasError(
                    f"Circular alias: '{team_name}' already resolves to "
                    f"'{existing_resolution.team_name}' which matches '{raw_team_name}'"
                )

        payload = _read_registry_payload(path)

        if competition_key:
            bookmaker_competition_aliases = payload.setdefault(
                "bookmaker_competition_aliases", {}
            )
            bookmaker_competition_aliases.setdefault(bookmaker_key, {}).setdefault(
                competition_key, {}
            )[raw_key] = target_team_name
        else:
            bookmaker_aliases = payload.setdefault("bookmaker_aliases", {})
            bookmaker_aliases.setdefault(bookmaker_key, {})[raw_key] = target_team_name

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
    clear_team_registry_cache()
    resolution = resolve_team_alias(
        raw_team_name,
        bookmaker_id=bookmaker_id,
        competition_id=competition_id,
    )
    if resolution is None:
        raise RuntimeError("Saved team alias could not be reloaded")
    return resolution

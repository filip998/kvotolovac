from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import settings
from app.database import init_db, close_db
from app.services.league_registry import clear_league_registry_cache
from app.services.team_registry import clear_team_registry_cache


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """Fresh SQLite database file for every test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    clear_league_registry_cache()
    clear_team_registry_cache()
    conn = asyncio.run(init_db(str(db_path)))
    yield conn
    clear_league_registry_cache()
    clear_team_registry_cache()
    asyncio.run(close_db())


@pytest.fixture
def league_registry_file(tmp_path, monkeypatch):
    source_path = Path(settings.league_registry_path)
    target_path = tmp_path / "league_registry.json"
    if source_path.exists():
        target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        target_path.write_text(
            '{"canonical_leagues": {}, "aliases": {}, "bookmaker_aliases": {}}\n',
            encoding="utf-8",
        )
    monkeypatch.setattr(settings, "league_registry_path", str(target_path))
    clear_league_registry_cache()
    yield target_path
    clear_league_registry_cache()


@pytest.fixture
def team_registry_file(tmp_path, monkeypatch):
    target_path = tmp_path / "team_registry.json"
    target_path.write_text(
        '{"aliases": {}, "bookmaker_aliases": {}, "competition_aliases": {}, "bookmaker_competition_aliases": {}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "team_registry_path", str(target_path))
    clear_team_registry_cache()
    yield str(target_path)
    clear_team_registry_cache()

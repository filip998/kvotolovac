from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


class DatabaseMigrationRequired(RuntimeError):
    """Raised when the app is pointed at a database that is not at migration head."""


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"
_SCRIPT_LOCATION = _BACKEND_ROOT / "app" / "migrations"


def sqlite_url_for_path(db_path: str) -> str:
    if db_path == ":memory:":
        raise DatabaseMigrationRequired(
            "In-memory SQLite databases cannot be verified with Alembic migrations. "
            "Use a file-backed SQLite database and run migrations first."
        )
    return f"sqlite:///{db_path}"


def alembic_config(db_path: str) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    database_url = sqlite_url_for_path(db_path)
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    return config


def upgrade_database(db_path: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(db_path), revision)


def _migration_required_message(actual: str | None, expected: str) -> str:
    state = actual or "unversioned/missing"
    return (
        "Database schema is not migrated. "
        f"Current revision: {state}; expected: {expected}. "
        "Run `cd backend && ./venv/bin/alembic upgrade head` before starting the backend."
    )


def migrate_database_to_head(db_path: str) -> tuple[str | None, str]:
    expected = head_revision(db_path)
    actual = current_revision(db_path)
    if actual == expected:
        return actual, expected

    try:
        upgrade_database(db_path)
    except Exception as exc:
        raise DatabaseMigrationRequired(
            "Automatic database migration failed. "
            f"{_migration_required_message(actual, expected)}"
        ) from exc

    migrated = current_revision(db_path)
    if migrated != expected:
        raise DatabaseMigrationRequired(_migration_required_message(migrated, expected))

    return actual, expected


def head_revision(db_path: str) -> str:
    script = ScriptDirectory.from_config(alembic_config(db_path))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected exactly one Alembic head, found {heads!r}")
    return heads[0]


def current_revision(db_path: str) -> str | None:
    if db_path == ":memory:":
        return None
    if not Path(db_path).exists():
        return None
    with sqlite3.connect(db_path) as conn:
        has_version_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        if has_version_table is None:
            return None
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError(f"Expected one Alembic revision row, found {len(rows)}")
    return str(rows[0][0])


def ensure_database_at_head(db_path: str) -> None:
    expected = head_revision(db_path)
    actual = current_revision(db_path)
    if actual == expected:
        return

    raise DatabaseMigrationRequired(_migration_required_message(actual, expected))

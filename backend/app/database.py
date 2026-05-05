from __future__ import annotations

import aiosqlite

from .migrations.runner import ensure_database_at_head

_db_connection: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _db_connection


async def init_db(db_path: str = ":memory:") -> aiosqlite.Connection:
    global _db_connection
    ensure_database_at_head(db_path)
    _db_connection = await aiosqlite.connect(db_path)
    _db_connection.row_factory = aiosqlite.Row
    await _db_connection.execute("PRAGMA busy_timeout = 5000")
    await _db_connection.execute("PRAGMA foreign_keys = ON")
    return _db_connection


async def close_db() -> None:
    global _db_connection
    if _db_connection is not None:
        await _db_connection.close()
        _db_connection = None

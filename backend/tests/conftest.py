from __future__ import annotations

import pytest
import pytest_asyncio

from app.database import init_db, close_db


@pytest_asyncio.fixture(autouse=True)
async def db():
    """Fresh in-memory database for every test."""
    conn = await init_db(":memory:")
    yield conn
    await close_db()

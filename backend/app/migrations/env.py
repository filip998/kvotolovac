from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings

config = context.config

target_metadata = None


def _database_url() -> str:
    return (
        config.attributes.get("database_url")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
        or config.get_main_option("sqlalchemy.url")
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _set_sqlite_foreign_keys(connection, enabled: bool) -> None:
    state = "ON" if enabled else "OFF"
    connection.exec_driver_sql(f"PRAGMA foreign_keys = {state}")
    connection.commit()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _set_sqlite_foreign_keys(connection, False)
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            _set_sqlite_foreign_keys(connection, True)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

from __future__ import annotations

from app.config import Settings


def test_settings_reads_auto_migrate_on_startup(monkeypatch):
    monkeypatch.setenv("AUTO_MIGRATE_ON_STARTUP", "true")

    settings = Settings(_env_file=None)

    assert settings.auto_migrate_on_startup is True

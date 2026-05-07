"""Add Telegram notification profiles and delivery dedupe.

Revision ID: 0003_telegram_notifications
Revises: 0002_legacy_compatibility
"""

from __future__ import annotations

from alembic import op


revision = "0003_telegram_notifications"
down_revision = "0002_legacy_compatibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS telegram_notification_profiles (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               label TEXT NOT NULL,
               chat_id TEXT NOT NULL,
               enabled BOOLEAN NOT NULL DEFAULT TRUE,
               min_gap REAL NOT NULL DEFAULT 0,
               min_roi_percent REAL NOT NULL DEFAULT 0,
               bookmaker_ids TEXT NOT NULL DEFAULT '[]',
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_telegram_profiles_enabled
           ON telegram_notification_profiles (enabled, id)"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS telegram_notification_deliveries (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               profile_id INTEGER NOT NULL REFERENCES telegram_notification_profiles(id)
                   ON DELETE CASCADE,
               opportunity_fingerprint TEXT NOT NULL,
               publish_id TEXT,
               status TEXT NOT NULL DEFAULT 'pending',
               attempt_count INTEGER NOT NULL DEFAULT 0,
               telegram_message_id INTEGER,
               error TEXT,
               last_attempt_at TIMESTAMP,
               sent_at TIMESTAMP,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               UNIQUE (profile_id, opportunity_fingerprint)
           )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_profile_status
           ON telegram_notification_deliveries (profile_id, status, updated_at)"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_publish
           ON telegram_notification_deliveries (publish_id)"""
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")

"""Add Telegram command permissions and poller state.

Revision ID: 0007_telegram_command_permissions
Revises: 0006_telegram_middle_ev_threshold
"""

from __future__ import annotations

from alembic import op


revision = "0007_telegram_command_permissions"
down_revision = "0006_telegram_middle_ev_threshold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE telegram_notification_profiles
           ADD COLUMN command_permission_preset TEXT NOT NULL DEFAULT 'none'"""
    )
    op.execute(
        """ALTER TABLE telegram_notification_profiles
           ADD COLUMN allowed_commands TEXT NOT NULL DEFAULT '[]'"""
    )
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_telegram_profiles_command_chat
           ON telegram_notification_profiles (chat_id)
           WHERE enabled = 1 AND command_permission_preset != 'none'"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS telegram_command_state (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               last_update_id INTEGER,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS telegram_command_executions (
               update_id INTEGER PRIMARY KEY,
               chat_id TEXT NOT NULL,
               command TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS telegram_command_message_deliveries (
               update_id INTEGER NOT NULL,
               command TEXT NOT NULL,
               message_key TEXT NOT NULL,
               message_index INTEGER NOT NULL,
               telegram_message_id INTEGER,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               PRIMARY KEY (update_id, command, message_key)
           )"""
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")

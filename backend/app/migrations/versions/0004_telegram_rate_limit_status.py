"""Add Telegram profile delivery status fields.

Revision ID: 0004_telegram_rate_limit_status
Revises: 0003_telegram_notifications
"""

from __future__ import annotations

from alembic import op


revision = "0004_telegram_rate_limit_status"
down_revision = "0003_telegram_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE telegram_notification_profiles
           ADD COLUMN rate_limited_until TIMESTAMP"""
    )
    op.execute(
        """ALTER TABLE telegram_notification_profiles
           ADD COLUMN last_delivery_error TEXT"""
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")

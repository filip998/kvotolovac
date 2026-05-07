"""Add Telegram middle EV threshold.

Revision ID: 0006_telegram_middle_ev_threshold
Revises: 0005_middle_ev_opportunity_fields
"""

from __future__ import annotations

from alembic import op


revision = "0006_telegram_middle_ev_threshold"
down_revision = "0005_middle_ev_opportunity_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE telegram_notification_profiles
           ADD COLUMN min_middle_ev_percent REAL NOT NULL DEFAULT 0"""
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")

"""Add canonical team merge audit metadata.

Revision ID: 0008_team_merge_history_audit
Revises: 0007_telegram_command_permissions
"""

from __future__ import annotations

from alembic import op


revision = "0008_team_merge_history_audit"
down_revision = "0007_telegram_command_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE team_merge_history
           ADD COLUMN merge_source TEXT NOT NULL DEFAULT 'manual'"""
    )
    op.execute("ALTER TABLE team_merge_history ADD COLUMN merge_reason TEXT")
    op.execute("ALTER TABLE team_merge_history ADD COLUMN identity_policy TEXT")
    op.execute("ALTER TABLE team_merge_history ADD COLUMN identity_decision TEXT")


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")

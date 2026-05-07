"""Add middle EV fields to opportunities.

Revision ID: 0005_middle_ev_opportunity_fields
Revises: 0004_telegram_rate_limit_status
"""

from __future__ import annotations

from alembic import op


revision = "0005_middle_ev_opportunity_fields"
down_revision = "0004_telegram_rate_limit_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE opportunities ADD COLUMN middle_hit_probability REAL")
    op.execute("ALTER TABLE opportunities ADD COLUMN middle_ev REAL")
    op.execute("ALTER TABLE opportunities ADD COLUMN middle_model_confidence TEXT")
    op.execute("ALTER TABLE opportunities ADD COLUMN middle_model_diagnostics TEXT")
    op.execute("ALTER TABLE opportunities ADD COLUMN middle_ev_rank REAL")


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")

"""Title pattern cooldown — per-project CJK n-gram ledger.

Adds ``diversity_budgets.title_patterns`` (JSONB) to persist the
``DiversityBudget.title_patterns`` ledger across worker restarts.

The column maps each 2–3 char CJK n-gram seen in a chapter title to the
most-recent chapter number where it appeared. ``DiversityBudget.
title_pattern_cooldown_violations`` queries this ledger to block patterns
like 《血脉决堤》→《灵脉决堤》→《道心决堤》 from appearing within a
75-chapter sliding window. Without the column the ledger resets to empty
on every restart (in-flight projects accumulate no pattern history).

Additive only: existing rows get ``'{}'::jsonb`` so the engine no-ops on
historical data and builds the ledger incrementally as new chapters land.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0024_title_patterns"
down_revision = "0023_cross_project_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diversity_budgets",
        sa.Column(
            "title_patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("diversity_budgets", "title_patterns")

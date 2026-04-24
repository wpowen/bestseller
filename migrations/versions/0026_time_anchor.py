"""Phase D2 — time_anchor + chapter_time_span on ChapterStateSnapshotModel.

Adds two additive, nullable columns to ``chapter_state_snapshots`` so
the Phase D3 continuity validators (``CountdownArithmeticCheck`` and
``TimeRegressionCheck``) can reason about when each chapter takes
place and how much in-story time elapsed.

Both columns are free-prose so non-numeric anchors ("末世第4天 清晨")
survive unchanged; the parser runs in ``story_bible.py`` when the
timeline file is rendered. Existing rows stay valid (``NULL`` means
"anchor unknown — skip validation").
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0026_time_anchor"
down_revision = "0025_override_debt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chapter_state_snapshots",
        sa.Column("time_anchor", sa.Text(), nullable=True),
    )
    op.add_column(
        "chapter_state_snapshots",
        sa.Column("chapter_time_span", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chapter_state_snapshots", "chapter_time_span")
    op.drop_column("chapter_state_snapshots", "time_anchor")

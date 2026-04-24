"""Phase B1 — Per-chapter narrative line dominance.

Adds three additive columns on ``chapters`` so the
``narrative_line_tracker`` service can record which of the four
narrative layers (``overt`` / ``undercurrent`` / ``hidden`` /
``core_axis``) carried each chapter and by what intensity:

* ``chapters.dominant_line`` — the single strongest layer for the
  chapter.  Nullable because pre-tracker chapters were never classified
  and the ``LineGapCheck`` validator only enforces once the project has
  at least a rolling window of classifications.
* ``chapters.support_lines`` — JSONB string array of the supporting
  layers (background color).  Typically one or two entries; never
  contains the ``dominant_line`` value itself.
* ``chapters.line_intensity`` — 0..1 float representing how
  concentrated the dominant line's signal was (≥0.6 means the chapter
  was genuinely carried by that layer as opposed to shared attention).

Additive only: existing rows stay valid with ``NULL`` in every new
column; the Phase B1 write-gate check runs in ``audit_only`` mode until
chapter 10 so historical chapters are grandfathered via the same
``only_enforce_from_chapter`` pattern already in
``config/quality_gates.yaml``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0024_line_dominance"
down_revision = "0023_cross_project_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chapters",
        sa.Column("dominant_line", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column(
            "support_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "chapters",
        sa.Column("line_intensity", sa.Float(), nullable=True),
    )
    # Aggregation-path index: the pacing dashboard groups by
    # (project_id, dominant_line); leading with project_id keeps the
    # index usable for per-project rollups and for ``report_gaps`` which
    # scans the most recent N chapters of a single project.
    op.create_index(
        "ix_chapters_dominant_line",
        "chapters",
        ["project_id", "dominant_line"],
    )


def downgrade() -> None:
    op.drop_index("ix_chapters_dominant_line", table_name="chapters")
    op.drop_column("chapters", "line_intensity")
    op.drop_column("chapters", "support_lines")
    op.drop_column("chapters", "dominant_line")

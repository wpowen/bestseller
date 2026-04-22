"""Reader Hype Engine — Phase 1 persistence surface.

Adds three additive column groups so the engine can round-trip its state
through PostgreSQL:

* ``projects.hype_scheme_json`` — the frozen ``HypeScheme`` portion of
  ``ProjectInvariants`` (recipe deck, reader_promise, selling_points,
  hook_keywords, chapter_hook_strategy, comedic/min/payoff targets).
  Seeded once from the preset's ``hype`` namespace and then read-only.
* ``diversity_budgets.hype_moments`` — ordered JSONB list of
  ``HypeMoment`` entries (chapter_no / hype_type / recipe_key /
  intensity) recorded by the chapter validator, consumed by the L3
  prompt constructor for LRU / diversity rotation.
* ``chapters.hype_type`` / ``hype_intensity`` / ``hype_recipe_key`` —
  per-chapter denormalization of the assigned hype moment, used for
  fast aggregation in the L8 scorecard and analytics queries without
  having to traverse the JSONB history list.

Additive only: every existing row stays valid; empty defaults let the
engine no-op on historical projects that never declared a hype scheme
(``HypeScheme`` with an empty ``recipe_deck`` skips prompt injection
and validator checks gracefully).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0019_hype_engine"
down_revision = "0018_backfill_production_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- Project-level frozen hype scheme ----------------
    op.add_column(
        "projects",
        sa.Column(
            "hype_scheme_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # ---------------- Per-project mutable hype history ----------------
    op.add_column(
        "diversity_budgets",
        sa.Column(
            "hype_moments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # ---------------- Per-chapter assigned hype metadata ----------------
    # Kept denormalized (alongside the JSONB history) so dashboards and
    # scorecards can aggregate without unpacking the budget row. Nullable
    # because pre-engine chapters never had a hype assignment.
    op.add_column(
        "chapters",
        sa.Column("hype_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("hype_intensity", sa.Float(), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("hype_recipe_key", sa.String(length=120), nullable=True),
    )
    # Aggregation-path index: scorecard histograms group by
    # (project_id, hype_type); leading the composite with project_id keeps
    # the index usable for the per-project projection.
    op.create_index(
        "ix_chapters_hype_type",
        "chapters",
        ["project_id", "hype_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_chapters_hype_type", table_name="chapters")
    op.drop_column("chapters", "hype_recipe_key")
    op.drop_column("chapters", "hype_intensity")
    op.drop_column("chapters", "hype_type")
    op.drop_column("diversity_budgets", "hype_moments")
    op.drop_column("projects", "hype_scheme_json")

"""Relax style_guides enum-like VARCHAR(32) columns to TEXT.

These columns (``pov_type``, ``tense``, ``sentence_style``, ``info_density``)
were originally sized for short English enum codes, but both the Pydantic
domain model (``StylePreferenceConfig`` uses ``max_length=4000``) and the LLM
conception pipeline (``conception.py`` finalize step) in practice treat them
as free-form Chinese descriptions. Migration 0011 skipped them because it only
relaxed VARCHAR(>=100) columns.

This makes the four columns consistent with ``prose_style``, which is already
TEXT. ``ALTER COLUMN ... TYPE TEXT`` on PostgreSQL preserves the existing
``NOT NULL`` constraint and any ``DEFAULT`` expression, so no extra DDL is
needed.
"""
from __future__ import annotations

from alembic import op


revision = "0013_style_guides_to_text"
down_revision = "0012_chapter_state_snapshots"
branch_labels = None
depends_on = None


_COLUMNS: tuple[str, ...] = (
    "pov_type",
    "tense",
    "sentence_style",
    "info_density",
)


def upgrade() -> None:
    for column in _COLUMNS:
        # Wrap in a DO block so a missing column (older snapshot, partial
        # environment) is skipped instead of aborting the migration.
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'style_guides'
                      AND column_name = '{column}'
                ) THEN
                    EXECUTE 'ALTER TABLE style_guides '
                         || 'ALTER COLUMN "{column}" TYPE TEXT';
                END IF;
            END$$;
            """
        )


def downgrade() -> None:
    # No-op: shrinking TEXT back to VARCHAR(32) would risk truncating data
    # written under the new schema (the whole point of this migration).
    pass

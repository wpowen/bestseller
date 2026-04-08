"""Add chapter_state_snapshots table for hard-fact continuity tracking.

Every chapter generates a structured snapshot of its end-of-chapter ``hard
facts`` (countdowns, character levels, resources, inventory counts, positions,
distances, time-of-day) that gets injected into the next chapter's writing
prompt as a strict constraint.  This prevents cross-chapter drift such as the
countdown timer going 24h → 74h → 10d between consecutive chapters.
"""
from __future__ import annotations

from alembic import op


revision = "0012_chapter_state_snapshots"
down_revision = "0011_relax_varchar_to_text"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS chapter_state_snapshots (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
        chapter_number INTEGER NOT NULL,
        facts JSONB NOT NULL DEFAULT '{}'::jsonb,
        raw_extraction TEXT,
        extraction_model TEXT,
        extraction_status VARCHAR(32) NOT NULL DEFAULT 'ok',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_chapter_state_snapshot_chapter UNIQUE (project_id, chapter_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chapter_state_snapshots_lookup
    ON chapter_state_snapshots(project_id, chapter_number)
    """,
)


DOWNGRADE_SQL = (
    "DROP TABLE IF EXISTS chapter_state_snapshots CASCADE",
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement)

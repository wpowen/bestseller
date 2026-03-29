from __future__ import annotations

from alembic import op


revision = "0008_interactive_fiction"
down_revision = "0007_world_expansion_boundaries"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    # Add project_type column to projects (default 'linear' for existing rows)
    """
    ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS project_type VARCHAR(32) NOT NULL DEFAULT 'linear'
    """,

    # Create if_generation_runs table
    """
    CREATE TABLE IF NOT EXISTS if_generation_runs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        phase VARCHAR(32) NOT NULL DEFAULT 'story_bible',
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        book_id VARCHAR(128),
        bible_artifact_id UUID REFERENCES planning_artifact_versions(id) ON DELETE SET NULL,
        arc_artifact_id UUID REFERENCES planning_artifact_versions(id) ON DELETE SET NULL,
        walkthrough_artifact_id UUID REFERENCES planning_artifact_versions(id) ON DELETE SET NULL,
        total_chapters INTEGER NOT NULL DEFAULT 0,
        completed_chapters INTEGER NOT NULL DEFAULT 0,
        output_dir TEXT,
        error_message TEXT,
        config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # Index for looking up runs by project
    """
    CREATE INDEX IF NOT EXISTS idx_if_generation_runs_project
    ON if_generation_runs(project_id, created_at DESC)
    """,
)

DOWNGRADE_SQL = (
    "DROP INDEX IF EXISTS idx_if_generation_runs_project",
    "DROP TABLE IF EXISTS if_generation_runs",
    "ALTER TABLE projects DROP COLUMN IF EXISTS project_type",
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement.strip())

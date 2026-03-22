from __future__ import annotations

from alembic import op


revision = "0007_world_expansion_boundaries"
down_revision = "0006_chapter_word_defaults"
branch_labels = None
depends_on = None


WORLD_EXPANSION_SQL = (
    """
    CREATE TABLE IF NOT EXISTS world_backbones (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title VARCHAR(200) NOT NULL DEFAULT '全书世界主干',
        core_promise TEXT NOT NULL,
        mainline_drive TEXT NOT NULL,
        protagonist_destiny TEXT,
        antagonist_axis TEXT,
        thematic_melody TEXT,
        world_frame TEXT,
        invariant_elements JSONB NOT NULL DEFAULT '[]'::jsonb,
        stable_unknowns JSONB NOT NULL DEFAULT '[]'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_world_backbone_project UNIQUE (project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS volume_frontiers (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        volume_id UUID REFERENCES volumes(id) ON DELETE SET NULL,
        volume_number INTEGER NOT NULL,
        title VARCHAR(200) NOT NULL,
        frontier_summary TEXT NOT NULL,
        expansion_focus TEXT,
        start_chapter_number INTEGER NOT NULL,
        end_chapter_number INTEGER,
        visible_rule_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        active_locations JSONB NOT NULL DEFAULT '[]'::jsonb,
        active_factions JSONB NOT NULL DEFAULT '[]'::jsonb,
        active_arc_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        future_reveal_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_volume_frontier_number UNIQUE (project_id, volume_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deferred_reveals (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        volume_id UUID REFERENCES volumes(id) ON DELETE SET NULL,
        reveal_code VARCHAR(64) NOT NULL,
        label VARCHAR(200) NOT NULL,
        category VARCHAR(64) NOT NULL DEFAULT 'key_reveal',
        summary TEXT NOT NULL,
        source_volume_number INTEGER,
        reveal_volume_number INTEGER NOT NULL,
        reveal_chapter_number INTEGER NOT NULL,
        guard_condition TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'scheduled',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_deferred_reveal_code UNIQUE (project_id, reveal_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS expansion_gates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        volume_id UUID REFERENCES volumes(id) ON DELETE SET NULL,
        gate_code VARCHAR(64) NOT NULL,
        label VARCHAR(200) NOT NULL,
        gate_type VARCHAR(64) NOT NULL DEFAULT 'world_expansion',
        condition_summary TEXT NOT NULL,
        unlocks_summary TEXT NOT NULL,
        source_volume_number INTEGER,
        unlock_volume_number INTEGER NOT NULL,
        unlock_chapter_number INTEGER NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'planned',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_expansion_gate_code UNIQUE (project_id, gate_code)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_volume_frontiers_project_chapter_range
    ON volume_frontiers(project_id, start_chapter_number, end_chapter_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_deferred_reveals_project_visibility
    ON deferred_reveals(project_id, reveal_volume_number, reveal_chapter_number, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_expansion_gates_project_unlock
    ON expansion_gates(project_id, unlock_volume_number, unlock_chapter_number, status)
    """,
)


def upgrade() -> None:
    for statement in WORLD_EXPANSION_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_expansion_gates_project_unlock")
    op.execute("DROP INDEX IF EXISTS idx_deferred_reveals_project_visibility")
    op.execute("DROP INDEX IF EXISTS idx_volume_frontiers_project_chapter_range")
    op.execute("DROP TABLE IF EXISTS expansion_gates")
    op.execute("DROP TABLE IF EXISTS deferred_reveals")
    op.execute("DROP TABLE IF EXISTS volume_frontiers")
    op.execute("DROP TABLE IF EXISTS world_backbones")

from __future__ import annotations

from alembic import op


revision = "0010_narr_depth_char_voice"
down_revision = "0009_if_branch_and_memory"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    # ── Character voice profile & moral framework columns ──
    """
    ALTER TABLE characters
    ADD COLUMN IF NOT EXISTS voice_profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS moral_framework_json JSONB NOT NULL DEFAULT '{}'::jsonb
    """,

    # ── SceneContract new columns ──
    """
    ALTER TABLE scene_contracts
    ADD COLUMN IF NOT EXISTS thematic_task TEXT,
    ADD COLUMN IF NOT EXISTS dramatic_irony_intent TEXT,
    ADD COLUMN IF NOT EXISTS transition_type VARCHAR(32),
    ADD COLUMN IF NOT EXISTS subplot_codes JSONB NOT NULL DEFAULT '[]'::jsonb
    """,

    # ── Theme Arcs ──
    """
    CREATE TABLE IF NOT EXISTS theme_arcs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        theme_code VARCHAR(64) NOT NULL,
        theme_statement TEXT NOT NULL,
        symbol_set JSONB NOT NULL DEFAULT '[]'::jsonb,
        evolution_stages JSONB NOT NULL DEFAULT '[]'::jsonb,
        current_stage VARCHAR(32) NOT NULL DEFAULT 'introduced',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_theme_arc_code UNIQUE (project_id, theme_code)
    )
    """,

    # ── Motif Placements ──
    """
    CREATE TABLE IF NOT EXISTS motif_placements (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        theme_arc_id UUID NOT NULL REFERENCES theme_arcs(id) ON DELETE CASCADE,
        motif_label VARCHAR(200) NOT NULL,
        placement_type VARCHAR(32) NOT NULL,
        volume_number INTEGER,
        chapter_number INTEGER,
        scene_number INTEGER,
        description TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'planned',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_motif_placements_project_theme
    ON motif_placements(project_id, theme_arc_id)
    """,

    # ── Subplot Schedule ──
    """
    CREATE TABLE IF NOT EXISTS subplot_schedule (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        plot_arc_id UUID NOT NULL REFERENCES plot_arcs(id) ON DELETE CASCADE,
        arc_code VARCHAR(64) NOT NULL,
        chapter_number INTEGER NOT NULL,
        prominence VARCHAR(16) NOT NULL,
        notes TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_subplot_schedule_arc_chapter UNIQUE (project_id, plot_arc_id, chapter_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_subplot_schedule_project_chapter
    ON subplot_schedule(project_id, chapter_number)
    """,

    # ── Relationship Events ──
    """
    CREATE TABLE IF NOT EXISTS relationship_events (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        character_a_label VARCHAR(200) NOT NULL,
        character_b_label VARCHAR(200) NOT NULL,
        chapter_number INTEGER NOT NULL,
        scene_number INTEGER,
        event_description TEXT NOT NULL,
        relationship_change TEXT NOT NULL,
        is_milestone BOOLEAN NOT NULL DEFAULT FALSE,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_relationship_events_project_chapter
    ON relationship_events(project_id, chapter_number)
    """,

    # ── Reader Knowledge Entries ──
    """
    CREATE TABLE IF NOT EXISTS reader_knowledge_entries (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        chapter_number INTEGER NOT NULL,
        knowledge_item TEXT NOT NULL,
        audience VARCHAR(16) NOT NULL,
        source_clue_code VARCHAR(64),
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reader_knowledge_project_chapter
    ON reader_knowledge_entries(project_id, chapter_number)
    """,

    # ── Ending Contract ──
    """
    CREATE TABLE IF NOT EXISTS ending_contracts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        arcs_to_resolve JSONB NOT NULL DEFAULT '[]'::jsonb,
        clues_to_payoff JSONB NOT NULL DEFAULT '[]'::jsonb,
        relationships_to_close JSONB NOT NULL DEFAULT '[]'::jsonb,
        thematic_final_expression TEXT,
        denouement_plan TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'planned',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_ending_contract_project UNIQUE (project_id)
    )
    """,

    # ── Pacing Curve Points ──
    """
    CREATE TABLE IF NOT EXISTS pacing_curve_points (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        chapter_number INTEGER NOT NULL,
        tension_level NUMERIC(4, 2) NOT NULL,
        scene_type_plan VARCHAR(100),
        notes TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_pacing_curve_chapter UNIQUE (project_id, chapter_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pacing_curve_project
    ON pacing_curve_points(project_id)
    """,
)


DOWNGRADE_SQL = (
    "DROP TABLE IF EXISTS pacing_curve_points CASCADE",
    "DROP TABLE IF EXISTS ending_contracts CASCADE",
    "DROP TABLE IF EXISTS reader_knowledge_entries CASCADE",
    "DROP TABLE IF EXISTS relationship_events CASCADE",
    "DROP TABLE IF EXISTS subplot_schedule CASCADE",
    "DROP TABLE IF EXISTS motif_placements CASCADE",
    "DROP TABLE IF EXISTS theme_arcs CASCADE",
    "ALTER TABLE scene_contracts DROP COLUMN IF EXISTS thematic_task",
    "ALTER TABLE scene_contracts DROP COLUMN IF EXISTS dramatic_irony_intent",
    "ALTER TABLE scene_contracts DROP COLUMN IF EXISTS transition_type",
    "ALTER TABLE scene_contracts DROP COLUMN IF EXISTS subplot_codes",
    "ALTER TABLE characters DROP COLUMN IF EXISTS voice_profile_json",
    "ALTER TABLE characters DROP COLUMN IF EXISTS moral_framework_json",
)


def upgrade() -> None:
    for sql in UPGRADE_SQL:
        op.execute(sql)


def downgrade() -> None:
    for sql in DOWNGRADE_SQL:
        op.execute(sql)

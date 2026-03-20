from __future__ import annotations

from alembic import op


revision = "0003_narrative_graph"
down_revision = "0002_story_bible_entities"
branch_labels = None
depends_on = None


NARRATIVE_GRAPH_SQL = (
    """
    CREATE TABLE IF NOT EXISTS plot_arcs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        arc_code VARCHAR(64) NOT NULL,
        name VARCHAR(200) NOT NULL,
        arc_type VARCHAR(64) NOT NULL,
        promise TEXT NOT NULL,
        core_question TEXT NOT NULL,
        target_payoff TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'planned',
        scope_level VARCHAR(32) NOT NULL DEFAULT 'project',
        scope_volume_number INTEGER,
        scope_chapter_number INTEGER,
        description TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_plot_arc_code UNIQUE (project_id, arc_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS arc_beats (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        plot_arc_id UUID NOT NULL REFERENCES plot_arcs(id) ON DELETE CASCADE,
        beat_order INTEGER NOT NULL,
        scope_level VARCHAR(32) NOT NULL,
        scope_volume_number INTEGER,
        scope_chapter_number INTEGER,
        scope_scene_number INTEGER,
        beat_kind VARCHAR(64) NOT NULL,
        title VARCHAR(200),
        summary TEXT NOT NULL,
        emotional_shift TEXT,
        information_release TEXT,
        expected_payoff TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'planned',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_arc_beat_scope UNIQUE (
            plot_arc_id,
            beat_order,
            scope_level,
            scope_volume_number,
            scope_chapter_number,
            scope_scene_number
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS clues (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        plot_arc_id UUID REFERENCES plot_arcs(id) ON DELETE SET NULL,
        clue_code VARCHAR(64) NOT NULL,
        label VARCHAR(200) NOT NULL,
        clue_type VARCHAR(64) NOT NULL DEFAULT 'foreshadow',
        description TEXT NOT NULL,
        planted_in_volume_number INTEGER,
        planted_in_chapter_number INTEGER,
        planted_in_scene_number INTEGER,
        expected_payoff_by_volume_number INTEGER,
        expected_payoff_by_chapter_number INTEGER,
        expected_payoff_by_scene_number INTEGER,
        actual_paid_off_chapter_number INTEGER,
        actual_paid_off_scene_number INTEGER,
        reveal_guard TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'planted',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_clue_code UNIQUE (project_id, clue_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payoffs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        plot_arc_id UUID REFERENCES plot_arcs(id) ON DELETE SET NULL,
        source_clue_id UUID REFERENCES clues(id) ON DELETE SET NULL,
        payoff_code VARCHAR(64) NOT NULL,
        label VARCHAR(200) NOT NULL,
        description TEXT NOT NULL,
        target_volume_number INTEGER,
        target_chapter_number INTEGER,
        target_scene_number INTEGER,
        actual_chapter_number INTEGER,
        actual_scene_number INTEGER,
        status VARCHAR(32) NOT NULL DEFAULT 'planned',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_payoff_code UNIQUE (project_id, payoff_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chapter_contracts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
        chapter_number INTEGER NOT NULL,
        contract_summary TEXT NOT NULL,
        opening_state JSONB NOT NULL DEFAULT '{}'::jsonb,
        core_conflict TEXT,
        emotional_shift TEXT,
        information_release TEXT,
        closing_hook TEXT,
        primary_arc_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        supporting_arc_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        active_arc_beat_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        planted_clue_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        due_payoff_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_chapter_contract_chapter UNIQUE (project_id, chapter_id),
        CONSTRAINT uq_chapter_contract_number UNIQUE (project_id, chapter_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scene_contracts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
        scene_card_id UUID NOT NULL REFERENCES scene_cards(id) ON DELETE CASCADE,
        chapter_number INTEGER NOT NULL,
        scene_number INTEGER NOT NULL,
        contract_summary TEXT NOT NULL,
        entry_state JSONB NOT NULL DEFAULT '{}'::jsonb,
        exit_state JSONB NOT NULL DEFAULT '{}'::jsonb,
        core_conflict TEXT,
        emotional_shift TEXT,
        information_release TEXT,
        tail_hook TEXT,
        arc_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        arc_beat_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        planted_clue_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        payoff_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_scene_contract_scene UNIQUE (project_id, scene_card_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_plot_arcs_project_type_status
    ON plot_arcs(project_id, arc_type, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_arc_beats_project_scope
    ON arc_beats(project_id, scope_level, scope_volume_number, scope_chapter_number, scope_scene_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_clues_project_status
    ON clues(project_id, status, planted_in_chapter_number, expected_payoff_by_chapter_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_payoffs_project_status
    ON payoffs(project_id, status, target_chapter_number, actual_chapter_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scene_contracts_project_position
    ON scene_contracts(project_id, chapter_number, scene_number)
    """,
)


def upgrade() -> None:
    for statement in NARRATIVE_GRAPH_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    for statement in (
        "DROP TABLE IF EXISTS scene_contracts CASCADE",
        "DROP TABLE IF EXISTS chapter_contracts CASCADE",
        "DROP TABLE IF EXISTS payoffs CASCADE",
        "DROP TABLE IF EXISTS clues CASCADE",
        "DROP TABLE IF EXISTS arc_beats CASCADE",
        "DROP TABLE IF EXISTS plot_arcs CASCADE",
    ):
        op.execute(statement)

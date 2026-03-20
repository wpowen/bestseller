from __future__ import annotations

from alembic import op


revision = "0005_emotion_tracks_and_antagonist_plans"
down_revision = "0004_narrative_tree"
branch_labels = None
depends_on = None


EMOTION_AND_ANTAGONIST_SQL = (
    """
    CREATE TABLE IF NOT EXISTS emotion_tracks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        track_code VARCHAR(64) NOT NULL,
        track_type VARCHAR(64) NOT NULL,
        title VARCHAR(200) NOT NULL,
        character_a_id UUID REFERENCES characters(id) ON DELETE SET NULL,
        character_b_id UUID REFERENCES characters(id) ON DELETE SET NULL,
        character_a_label VARCHAR(200) NOT NULL,
        character_b_label VARCHAR(200) NOT NULL,
        relationship_type VARCHAR(100),
        summary TEXT NOT NULL,
        desired_payoff TEXT,
        trust_level NUMERIC(5,4) NOT NULL DEFAULT 0.5,
        attraction_level NUMERIC(5,4) NOT NULL DEFAULT 0,
        distance_level NUMERIC(5,4) NOT NULL DEFAULT 0.5,
        conflict_level NUMERIC(5,4) NOT NULL DEFAULT 0.5,
        intimacy_stage VARCHAR(64) NOT NULL DEFAULT 'setup',
        last_shift_chapter_number INTEGER,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_emotion_track_code UNIQUE (project_id, track_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS antagonist_plans (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        antagonist_character_id UUID REFERENCES characters(id) ON DELETE SET NULL,
        antagonist_label VARCHAR(200) NOT NULL,
        plan_code VARCHAR(64) NOT NULL,
        title VARCHAR(200) NOT NULL,
        threat_type VARCHAR(64) NOT NULL DEFAULT 'pressure',
        goal TEXT NOT NULL,
        current_move TEXT NOT NULL,
        next_countermove TEXT NOT NULL,
        escalation_condition TEXT,
        reveal_timing VARCHAR(100),
        scope_volume_number INTEGER,
        target_chapter_number INTEGER,
        pressure_level NUMERIC(5,4) NOT NULL DEFAULT 0.6,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_antagonist_plan_code UNIQUE (project_id, plan_code)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_emotion_tracks_project_type_status
    ON emotion_tracks(project_id, track_type, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_antagonist_plans_project_scope_status
    ON antagonist_plans(project_id, scope_volume_number, target_chapter_number, status)
    """,
)


def upgrade() -> None:
    for statement in EMOTION_AND_ANTAGONIST_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS antagonist_plans CASCADE")
    op.execute("DROP TABLE IF EXISTS emotion_tracks CASCADE")

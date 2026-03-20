from __future__ import annotations

from alembic import op


revision = "0002_story_bible_entities"
down_revision = "0001_postgresql_first_schema"
branch_labels = None
depends_on = None


STORY_BIBLE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS world_rules (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        rule_code VARCHAR(32) NOT NULL,
        name VARCHAR(200) NOT NULL,
        description TEXT NOT NULL,
        story_consequence TEXT,
        exploitation_potential TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_world_rule_code UNIQUE (project_id, rule_code),
        CONSTRAINT uq_world_rule_name UNIQUE (project_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS locations (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL,
        location_type VARCHAR(100) NOT NULL DEFAULT 'location',
        atmosphere TEXT,
        key_rule_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        story_role TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_location_name UNIQUE (project_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS factions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL,
        goal TEXT,
        method TEXT,
        relationship_to_protagonist TEXT,
        internal_conflict TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_faction_name UNIQUE (project_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS characters (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL,
        role VARCHAR(64) NOT NULL DEFAULT 'supporting',
        age INTEGER,
        background TEXT,
        goal TEXT,
        fear TEXT,
        flaw TEXT,
        strength TEXT,
        secret TEXT,
        arc_trajectory TEXT,
        arc_state TEXT,
        power_tier VARCHAR(100),
        knowledge_state JSONB NOT NULL DEFAULT '{}'::jsonb,
        is_pov_character BOOLEAN NOT NULL DEFAULT FALSE,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_character_name UNIQUE (project_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS relationships (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        character_a_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        character_b_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        relationship_type VARCHAR(100) NOT NULL,
        strength NUMERIC(5,4) NOT NULL DEFAULT 0,
        public_face TEXT,
        private_reality TEXT,
        tension_summary TEXT,
        established_chapter_no INTEGER,
        last_changed_chapter_no INTEGER,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_relationship_pair UNIQUE (project_id, character_a_id, character_b_id),
        CONSTRAINT ck_relationships_relationship_self_reference CHECK (character_a_id <> character_b_id),
        CONSTRAINT ck_relationships_relationship_strength_range CHECK (strength >= -1 AND strength <= 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS character_state_snapshots (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        chapter_id UUID REFERENCES chapters(id),
        scene_card_id UUID REFERENCES scene_cards(id),
        chapter_number INTEGER NOT NULL,
        scene_number INTEGER,
        arc_state TEXT,
        emotional_state TEXT,
        physical_state TEXT,
        power_tier VARCHAR(100),
        trust_map JSONB NOT NULL DEFAULT '{}'::jsonb,
        beliefs JSONB NOT NULL DEFAULT '[]'::jsonb,
        notes TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_characters_project_role ON characters(project_id, role)",
    """
    CREATE INDEX IF NOT EXISTS idx_character_state_snapshots_lookup
    ON character_state_snapshots(project_id, character_id, chapter_number, scene_number)
    """,
)


def upgrade() -> None:
    for statement in STORY_BIBLE_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    for statement in (
        "DROP TABLE IF EXISTS character_state_snapshots CASCADE",
        "DROP TABLE IF EXISTS relationships CASCADE",
        "DROP TABLE IF EXISTS characters CASCADE",
        "DROP TABLE IF EXISTS factions CASCADE",
        "DROP TABLE IF EXISTS locations CASCADE",
        "DROP TABLE IF EXISTS world_rules CASCADE",
    ):
        op.execute(statement)

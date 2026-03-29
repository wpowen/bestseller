from __future__ import annotations

from alembic import op


revision = "0009_if_branch_and_memory"
down_revision = "0008_interactive_fiction"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    # Extend if_generation_runs with multi-branch fields
    """
    ALTER TABLE if_generation_runs
    ADD COLUMN IF NOT EXISTS total_routes INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS act_plan_json JSONB,
    ADD COLUMN IF NOT EXISTS generation_mode VARCHAR(32) NOT NULL DEFAULT 'simple'
    """,

    # Acts-level story structure plan
    """
    CREATE TABLE IF NOT EXISTS if_act_plans (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id UUID NOT NULL REFERENCES if_generation_runs(id) ON DELETE CASCADE,
        act_id VARCHAR(32) NOT NULL,
        act_index INTEGER NOT NULL,
        title VARCHAR(200) NOT NULL,
        chapter_start INTEGER NOT NULL,
        chapter_end INTEGER NOT NULL,
        act_goal TEXT NOT NULL,
        core_theme VARCHAR(100),
        dominant_emotion VARCHAR(64),
        climax_chapter INTEGER,
        entry_state TEXT,
        exit_state TEXT,
        payoff_promises JSONB NOT NULL DEFAULT '[]'::jsonb,
        branch_opportunities JSONB NOT NULL DEFAULT '[]'::jsonb,
        arc_breakdown JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_if_act_plan UNIQUE (project_id, run_id, act_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_if_act_plans_run
    ON if_act_plans(project_id, run_id)
    """,

    # Hard-branch route definitions
    """
    CREATE TABLE IF NOT EXISTS if_route_definitions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id UUID NOT NULL REFERENCES if_generation_runs(id) ON DELETE CASCADE,
        route_id VARCHAR(64) NOT NULL,
        route_type VARCHAR(32) NOT NULL,
        title VARCHAR(200) NOT NULL,
        description TEXT,
        branch_start_chapter INTEGER,
        merge_chapter INTEGER,
        entry_condition JSONB NOT NULL DEFAULT '{}'::jsonb,
        merge_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
        generation_status VARCHAR(32) NOT NULL DEFAULT 'planned',
        chapter_count INTEGER NOT NULL DEFAULT 0,
        output_arc_file TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_if_route UNIQUE (project_id, run_id, route_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_if_route_definitions_run
    ON if_route_definitions(project_id, run_id)
    """,

    # World state snapshots (taken after each arc)
    """
    CREATE TABLE IF NOT EXISTS if_world_state_snapshots (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id UUID NOT NULL REFERENCES if_generation_runs(id) ON DELETE CASCADE,
        route_id VARCHAR(64) NOT NULL DEFAULT 'mainline',
        snapshot_chapter INTEGER NOT NULL,
        arc_index INTEGER NOT NULL,
        character_states JSONB NOT NULL DEFAULT '{}'::jsonb,
        faction_states JSONB NOT NULL DEFAULT '{}'::jsonb,
        revealed_truths JSONB NOT NULL DEFAULT '[]'::jsonb,
        active_threats JSONB NOT NULL DEFAULT '[]'::jsonb,
        planted_unrevealed JSONB NOT NULL DEFAULT '[]'::jsonb,
        power_rankings JSONB NOT NULL DEFAULT '[]'::jsonb,
        world_summary TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_if_world_snapshot UNIQUE (project_id, run_id, route_id, snapshot_chapter)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_if_world_snapshots_lookup
    ON if_world_state_snapshots(project_id, run_id, route_id, snapshot_chapter DESC)
    """,

    # Arc-level summaries (generated after each arc)
    """
    CREATE TABLE IF NOT EXISTS if_arc_summaries (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id UUID NOT NULL REFERENCES if_generation_runs(id) ON DELETE CASCADE,
        route_id VARCHAR(64) NOT NULL DEFAULT 'mainline',
        arc_index INTEGER NOT NULL,
        chapter_start INTEGER NOT NULL,
        chapter_end INTEGER NOT NULL,
        act_id VARCHAR(32),
        protagonist_growth TEXT,
        relationship_changes JSONB NOT NULL DEFAULT '[]'::jsonb,
        unresolved_threads JSONB NOT NULL DEFAULT '[]'::jsonb,
        power_level_summary TEXT,
        next_arc_setup TEXT,
        open_clues JSONB NOT NULL DEFAULT '[]'::jsonb,
        resolved_clues JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_if_arc_summary UNIQUE (project_id, run_id, route_id, arc_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_if_arc_summaries_lookup
    ON if_arc_summaries(project_id, run_id, route_id, arc_index)
    """,

    # IF-specific canon facts with route awareness
    """
    CREATE TABLE IF NOT EXISTS if_canon_facts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id UUID NOT NULL REFERENCES if_generation_runs(id) ON DELETE CASCADE,
        route_id VARCHAR(64) NOT NULL DEFAULT 'all',
        chapter_number INTEGER NOT NULL,
        fact_type VARCHAR(64) NOT NULL,
        subject_label VARCHAR(255) NOT NULL,
        fact_body TEXT NOT NULL,
        importance VARCHAR(16) NOT NULL DEFAULT 'major',
        is_payoff_of_clue VARCHAR(64),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_if_canon_facts_lookup
    ON if_canon_facts(project_id, run_id, route_id, chapter_number, importance)
    """,
)

DOWNGRADE_SQL = (
    "DROP INDEX IF EXISTS idx_if_canon_facts_lookup",
    "DROP TABLE IF EXISTS if_canon_facts",
    "DROP INDEX IF EXISTS idx_if_arc_summaries_lookup",
    "DROP TABLE IF EXISTS if_arc_summaries",
    "DROP INDEX IF EXISTS idx_if_world_snapshots_lookup",
    "DROP TABLE IF EXISTS if_world_state_snapshots",
    "DROP INDEX IF EXISTS idx_if_route_definitions_run",
    "DROP TABLE IF EXISTS if_route_definitions",
    "DROP INDEX IF EXISTS idx_if_act_plans_run",
    "DROP TABLE IF EXISTS if_act_plans",
    """
    ALTER TABLE if_generation_runs
    DROP COLUMN IF EXISTS total_routes,
    DROP COLUMN IF EXISTS act_plan_json,
    DROP COLUMN IF EXISTS generation_mode
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement.strip())

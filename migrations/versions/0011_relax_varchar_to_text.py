"""Relax all VARCHAR(>=100) columns to TEXT.

LLM-generated content (titles, labels, descriptions, character names, etc.)
occasionally exceeds the original VARCHAR length caps and breaks
materialization. PostgreSQL TEXT has no length limit and identical perf to
VARCHAR, so we widen every previously-bounded descriptive column.

Enum/code/status columns (originally <100 chars) are left unchanged.
"""
from __future__ import annotations

from alembic import op


revision = "0011_relax_varchar_to_text"
down_revision = "0010_publishing_and_api_keys"
branch_labels = None
depends_on = None


# (table, column) pairs whose original definition was VARCHAR(>=100).
# Mirrors the bulk-edit applied to src/bestseller/infra/db/models.py.
COLUMNS_TO_TEXT: list[tuple[str, str]] = [
    ("projects", "title"),
    ("projects", "genre"),
    ("projects", "sub_genre"),
    ("projects", "audience"),
    ("planning_artifact_versions", "artifact_type"),
    ("world_rules", "name"),
    ("locations", "name"),
    ("locations", "location_type"),
    ("factions", "name"),
    ("characters", "name"),
    ("characters", "power_tier"),
    ("character_relationships", "relationship_type"),
    ("antagonist_camps", "power_tier"),
    ("volumes", "title"),
    ("world_arcs", "title"),
    ("chapters", "title"),
    ("scene_cards", "title"),
    ("scene_cards", "time_label"),
    ("information_reveals", "label"),
    ("world_expansion_gates", "label"),
    ("story_arcs", "name"),
    ("narrative_beats", "title"),
    ("clue_threads", "label"),
    ("payoff_threads", "label"),
    ("relationship_tracks", "title"),
    ("relationship_tracks", "character_a_label"),
    ("relationship_tracks", "character_b_label"),
    ("relationship_tracks", "relationship_type"),
    ("antagonist_plans", "antagonist_label"),
    ("antagonist_plans", "title"),
    ("antagonist_plans", "reveal_timing"),
    ("thematic_motifs", "motif_label"),
    ("cast_arc_assignments", "character_a_label"),
    ("cast_arc_assignments", "character_b_label"),
    ("scene_contracts", "scene_type_plan"),
    ("narrative_tree_nodes", "node_path"),
    ("narrative_tree_nodes", "parent_path"),
    ("narrative_tree_nodes", "title"),
    ("automatic_facts", "subject_label"),
    ("automatic_facts", "predicate"),
    ("automatic_events", "event_name"),
    ("automatic_events", "story_time_label"),
    ("automatic_events", "duration_hint"),
    ("acts", "title"),
    ("acts", "core_theme"),
    ("routes", "title"),
    ("publishing_platform_targets", "platform_chapter_id"),
    ("project_review_artifacts", "subject_label"),
    ("model_assignments", "model_name"),
    ("model_assignments", "prompt_template"),
    ("model_assignments", "prompt_hash"),
    ("api_keys", "api_base_url"),
]


def upgrade() -> None:
    # Use a DO block per column so that any table/column mismatch in older
    # deployments is silently skipped instead of aborting the migration.
    for table, column in COLUMNS_TO_TEXT:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = '{table}' AND column_name = '{column}'
                ) THEN
                    EXECUTE 'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE TEXT';
                END IF;
            END$$;
            """
        )

    # Catch-all sweep: convert ANY remaining character varying(N) where
    # N >= 100 in the public schema to TEXT. This guarantees no
    # length-overflow surprises even for tables not enumerated above.
    op.execute(
        """
        DO $$
        DECLARE
            r record;
        BEGIN
            FOR r IN
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type = 'character varying'
                  AND character_maximum_length >= 100
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER COLUMN %I TYPE TEXT',
                    r.table_name, r.column_name
                );
            END LOOP;
        END$$;
        """
    )


def downgrade() -> None:
    # Intentionally a no-op: shrinking TEXT back to VARCHAR(N) would risk
    # truncating data written under the new schema.
    pass

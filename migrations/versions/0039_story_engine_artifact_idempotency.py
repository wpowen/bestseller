"""Extend planning artifact idempotency to StoryEngine V2 artifacts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0039_story_engine_artifact_idempotency"
down_revision = "0038_book_production_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_planning_snapshot_idempotency", table_name="planning_artifact_versions")
    op.create_index(
        "uq_planning_snapshot_idempotency",
        "planning_artifact_versions",
        ["project_id", "artifact_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "artifact_type IN ('creation_intent','conception_snapshot','story_engine_v2',"
            "'story_engine_window_v2') AND scope_ref_id IS NULL "
            "AND idempotency_key IS NOT NULL OR "
            "artifact_type = 'story_transition_receipt_v1' "
            "AND scope_ref_id IS NOT NULL AND idempotency_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "artifact_type IN ('creation_intent','conception_snapshot','story_engine_v2',"
            "'story_engine_window_v2') AND scope_ref_id IS NULL "
            "AND idempotency_key IS NOT NULL OR "
            "artifact_type = 'story_transition_receipt_v1' "
            "AND scope_ref_id IS NOT NULL AND idempotency_key IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_planning_snapshot_idempotency", table_name="planning_artifact_versions")
    op.create_index(
        "uq_planning_snapshot_idempotency",
        "planning_artifact_versions",
        ["project_id", "artifact_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "artifact_type IN ('creation_intent','conception_snapshot') "
            "AND scope_ref_id IS NULL AND idempotency_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "artifact_type IN ('creation_intent','conception_snapshot') "
            "AND scope_ref_id IS NULL AND idempotency_key IS NOT NULL"
        ),
    )

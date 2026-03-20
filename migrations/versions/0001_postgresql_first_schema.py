from __future__ import annotations

from alembic import op

from bestseller.infra.db.schema import render_schema_statements


revision = "0001_postgresql_first_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in render_schema_statements():
        op.execute(statement.rstrip(";"))


def downgrade() -> None:
    table_names = [
        "timeline_events",
        "scene_draft_versions",
        "canon_facts",
        "scene_cards",
        "llm_runs",
        "chapter_draft_versions",
        "workflow_step_runs",
        "quality_scores",
        "export_artifacts",
        "chapters",
        "rewrite_impacts",
        "workflow_runs",
        "volumes",
        "style_guides",
        "rewrite_tasks",
        "review_reports",
        "retrieval_chunks",
        "planning_artifact_versions",
        "projects",
    ]
    for table_name in table_names:
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")

"""Add idempotency keys for conception attempts and snapshots."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0036_conception_snapshot_idempotency"
down_revision = "0035_quality_promotion_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "planning_artifact_versions",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_conception_workflow_idempotency",
        "workflow_runs",
        ["workflow_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "workflow_type IN ('conception_initial','conception_revision') "
            "AND idempotency_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "workflow_type IN ('conception_initial','conception_revision') "
            "AND idempotency_key IS NOT NULL"
        ),
    )
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


def downgrade() -> None:
    op.drop_index("uq_planning_snapshot_idempotency", table_name="planning_artifact_versions")
    op.drop_index("uq_conception_workflow_idempotency", table_name="workflow_runs")
    op.drop_column("planning_artifact_versions", "idempotency_key")
    op.drop_column("workflow_runs", "idempotency_key")

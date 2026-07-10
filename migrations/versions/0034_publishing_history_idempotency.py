"""Add durable deterministic publishing idempotency keys."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0034_publishing_history_idempotency"
down_revision = "0033_export_artifact_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publishing_history",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                schedule_id,
                chapter_number,
                row_number() OVER (
                    PARTITION BY schedule_id, chapter_number
                    ORDER BY created_at DESC, id DESC
                ) AS occurrence
            FROM publishing_history
        )
        UPDATE publishing_history AS history
        SET idempotency_key =
            ranked.schedule_id::text || ':' || ranked.chapter_number::text ||
            CASE
                WHEN ranked.occurrence = 1 THEN ''
                ELSE ':legacy:' || ranked.id::text
            END
        FROM ranked
        WHERE history.id = ranked.id
        """
    )
    op.alter_column("publishing_history", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_publishing_history_idempotency_key",
        "publishing_history",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_publishing_history_idempotency_key",
        "publishing_history",
        type_="unique",
    )
    op.drop_column("publishing_history", "idempotency_key")

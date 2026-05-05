"""book_generation_schedules — defer/schedule autowrite task starts.

Lets users pick a future timestamp for when an autowrite (or quickstart)
pipeline should kick off. The scheduler service registers a one-shot
APScheduler ``trigger="date"`` job per row; on fire it posts the stored
payload to the web task manager exactly like a manual "start" click.

Status lifecycle: pending → fired (one-shot job started) → completed |
failed | cancelled.  ``fired_at`` records when the schedule actually
triggered; ``task_id`` references the ``WebTaskState`` task it spawned.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID


revision = "0027_book_generation_schedules"
down_revision = "0026_time_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_generation_schedules",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'Asia/Shanghai'"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "task_type IN ('autowrite', 'quickstart')",
            name="ck_book_generation_schedules_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'fired', 'completed', 'failed', 'cancelled')",
            name="ck_book_generation_schedules_status",
        ),
    )
    op.create_index(
        "idx_book_generation_schedules_status_time",
        "book_generation_schedules",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "idx_book_generation_schedules_slug",
        "book_generation_schedules",
        ["project_slug"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_book_generation_schedules_slug",
        table_name="book_generation_schedules",
    )
    op.drop_index(
        "idx_book_generation_schedules_status_time",
        table_name="book_generation_schedules",
    )
    op.drop_table("book_generation_schedules")

"""Operator run/pause/stop intent in its own row, outside projects.metadata.

Books could not be stopped. The stop was written by the web process into
``projects.metadata``; the pipeline, running in a worker, rewrites that JSONB
column as a whole block from its own in-memory copy at the next checkpoint,
silently erasing the stop. Self-heal then found a book that looked healthy and
requeued it, so a stopped book came back as ``running`` about a minute later.

Moving operator intent to a dedicated table removes the write conflict entirely:
the pipeline only ever reads this table. Quality-driven pauses stay in
``projects.metadata`` — the pipeline writes those itself, so they were never
subject to the clobber.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0038_book_production_control"
down_revision = "0037_guard_bulk_project_delete"
branch_labels = None
depends_on = None

PORTABLE_JSON = sa.JSON().with_variant(JSONB(), "postgresql")

DESIRED_STATES_SQL = "'run','pause','stop'"


def upgrade() -> None:
    op.create_table(
        "book_production_control",
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "desired_state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'run'"),
        ),
        sa.Column("state_reason", sa.Text()),
        sa.Column("requested_by", sa.String(length=128)),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column(
            "command_serial",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "resume_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("resume_pending_at", sa.DateTime(timezone=True)),
        sa.Column(
            "detail",
            PORTABLE_JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"desired_state IN ({DESIRED_STATES_SQL})",
            name="ck_book_production_control_desired_state",
        ),
        sa.CheckConstraint(
            "command_serial >= 0",
            name="ck_book_production_control_serial_non_negative",
        ),
    )
    # Self-heal scans "which books are not allowed to auto-resume" on every
    # sweep; keep that a partial-index lookup rather than a full scan.
    op.create_index(
        "idx_book_production_control_halted",
        "book_production_control",
        ["desired_state"],
        postgresql_where=sa.text("desired_state <> 'run'"),
        sqlite_where=sa.text("desired_state <> 'run'"),
    )
    op.create_index(
        "idx_book_production_control_resume_pending",
        "book_production_control",
        ["resume_pending_at"],
        postgresql_where=sa.text("resume_pending"),
        sqlite_where=sa.text("resume_pending"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_book_production_control_resume_pending",
        table_name="book_production_control",
    )
    op.drop_index(
        "idx_book_production_control_halted",
        table_name="book_production_control",
    )
    op.drop_table("book_production_control")

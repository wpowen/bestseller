"""Persist export warnings, skipped chapters, and reading statistics."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033_export_artifact_metadata"
down_revision = "0032_chapter_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "export_artifacts",
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("export_artifacts", "metadata")

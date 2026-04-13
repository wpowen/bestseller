"""Add physical_description column to characters table.

This column stores structured physical appearance descriptions for each
character, used by the Identity Guardian (identity_guard.py) to enforce
consistent physical trait references across chapters.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_character_physical_description"
down_revision = "0013_style_guides_to_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("physical_description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("characters", "physical_description")

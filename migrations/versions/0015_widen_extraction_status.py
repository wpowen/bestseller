"""Widen chapter_state_snapshots.extraction_status to TEXT.

The previous ``VARCHAR(32)`` column was too short for the composite error
strings produced by the hard-fact extractor. Values such as
``failed:json_decode_error:Invalid control character at`` (52 chars) would
trip ``StringDataRightTruncationError`` and, because the write lives inside
a nested SAVEPOINT, cause every chapter with a mildly malformed LLM
payload to lose its snapshot row entirely.

Switching to ``TEXT`` removes the truncation risk altogether and matches
the pattern already used by the other extraction metadata columns
(``raw_extraction``, ``extraction_model``) on the same table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0015_widen_extraction_status"
down_revision = "0014_character_physical_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "chapter_state_snapshots",
        "extraction_status",
        existing_type=sa.String(length=32),
        type_=sa.Text(),
        existing_nullable=False,
        existing_server_default=sa.text("'ok'"),
    )


def downgrade() -> None:
    op.alter_column(
        "chapter_state_snapshots",
        "extraction_status",
        existing_type=sa.Text(),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default=sa.text("'ok'"),
    )

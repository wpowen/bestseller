"""Persist blind-reader studies and pseudonymous response evidence."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0041_story_engine_reader_study"
down_revision = "0040_story_engine_canary_campaign"
branch_labels = None
depends_on = None

PORTABLE_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "story_engine_reader_studies",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("study_key", sa.String(length=128), nullable=False),
        sa.Column("canary_campaign_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'planned'"),
        ),
        sa.Column(
            "evidence_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", PORTABLE_JSON, nullable=False),
        sa.Column(
            "report_json",
            PORTABLE_JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("source_run_id", sa.Uuid()),
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
            "status IN ('planned','collecting','insufficient_data','reader_validated','blocked')",
            name="ck_story_engine_reader_studies_story_engine_reader_study_status",
        ),
        sa.CheckConstraint(
            "evidence_source IN ('pending','fixture','live','mixed','unavailable')",
            name="ck_story_engine_reader_studies_story_engine_reader_study_evidence_source",
        ),
        sa.ForeignKeyConstraint(
            ["canary_campaign_id"],
            ["story_engine_canary_campaigns.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "study_key",
            name="uq_story_engine_reader_studies_study_key",
        ),
    )
    op.create_index(
        "idx_story_engine_reader_study_status",
        "story_engine_reader_studies",
        ["status", "created_at"],
    )

    op.create_table(
        "story_engine_reader_responses",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("study_id", sa.Uuid(), nullable=False),
        sa.Column("response_key", sa.String(length=128), nullable=False),
        sa.Column("participant_hash", sa.String(length=64), nullable=False),
        sa.Column("cell_key", sa.String(length=128), nullable=False),
        sa.Column("assigned_order", sa.String(length=32), nullable=False),
        sa.Column("preferred_variant", sa.String(length=16), nullable=False),
        sa.Column("engine_recall_accurate", sa.Boolean(), nullable=False),
        sa.Column("baseline_recall_accurate", sa.Boolean(), nullable=False),
        sa.Column("engine_severe_abandonment", sa.Boolean(), nullable=False),
        sa.Column("baseline_severe_abandonment", sa.Boolean(), nullable=False),
        sa.Column("evidence_source", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "assigned_order IN ('baseline_first','engine_first')",
            name="ck_story_engine_reader_responses_story_engine_reader_response_assigned_order",
        ),
        sa.CheckConstraint(
            "preferred_variant IN ('baseline','engine','tie')",
            name="ck_story_engine_reader_responses_story_engine_reader_response_preferred_variant",
        ),
        sa.CheckConstraint(
            "evidence_source IN ('fixture','live')",
            name="ck_story_engine_reader_responses_story_engine_reader_response_evidence_source",
        ),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["story_engine_reader_studies.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "study_id",
            "response_key",
            name="uq_story_engine_reader_response_key",
        ),
        sa.UniqueConstraint(
            "study_id",
            "participant_hash",
            name="uq_story_engine_reader_participant",
        ),
    )
    op.create_index(
        "idx_story_engine_reader_response_cell",
        "story_engine_reader_responses",
        ["study_id", "cell_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_story_engine_reader_response_cell",
        table_name="story_engine_reader_responses",
    )
    op.drop_table("story_engine_reader_responses")
    op.drop_index(
        "idx_story_engine_reader_study_status",
        table_name="story_engine_reader_studies",
    )
    op.drop_table("story_engine_reader_studies")

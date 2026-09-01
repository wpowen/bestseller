"""Persist frozen StoryEngine canary campaign manifests and reports."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0040_story_engine_canary_campaign"
down_revision = "0039_story_engine_artifact_idempotency"
branch_labels = None
depends_on = None

PORTABLE_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "story_engine_canary_campaigns",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("campaign_key", sa.String(length=128), nullable=False),
        sa.Column("experiment", sa.String(length=8), nullable=False),
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
            "experiment IN ('E1','E2')",
            name="ck_story_engine_canary_campaigns_story_engine_canary_campaign_experiment",
        ),
        sa.CheckConstraint(
            "status IN ('planned','running','blocked','fixture_validated','canary_validated')",
            name="ck_story_engine_canary_campaigns_story_engine_canary_campaign_status",
        ),
        sa.UniqueConstraint(
            "campaign_key",
            name="uq_story_engine_canary_campaigns_campaign_key",
        ),
    )
    op.create_index(
        "idx_story_engine_canary_campaign_status",
        "story_engine_canary_campaigns",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_story_engine_canary_campaign_status",
        table_name="story_engine_canary_campaigns",
    )
    op.drop_table("story_engine_canary_campaigns")

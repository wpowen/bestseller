"""Add Fanqie market snapshot and profile tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0031_fanqie_market_profiles"
down_revision = "0030_chapter_word_target_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fanqie_ranking_snapshots",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source", sa.String(64), nullable=False, server_default=sa.text("'fanqiehub'")),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("board_type", sa.String(64), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False, server_default=sa.text("'fanqie'")),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "normalized_books_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "source",
            "board_type",
            "category",
            "channel",
            "data_date",
            name="uq_fanqie_ranking_snapshot_identity",
        ),
    )
    op.create_index(
        "idx_fanqie_ranking_snapshots_category_date",
        "fanqie_ranking_snapshots",
        ["category", "data_date"],
    )

    op.create_table(
        "fanqie_competitor_profiles",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "snapshot_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("fanqie_ranking_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_book_id", sa.String(128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("board_type", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("reader_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "profile_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "source_book_id",
            name="uq_fanqie_competitor_snapshot_book",
        ),
    )
    op.create_index(
        "idx_fanqie_competitor_profiles_category_rank",
        "fanqie_competitor_profiles",
        ["category", "rank"],
    )

    op.create_table(
        "fanqie_category_profiles",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "snapshot_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("fanqie_ranking_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("board_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False, server_default=sa.text("'fanqie'")),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "profile_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "category",
            "board_type",
            name="uq_fanqie_category_profile_snapshot",
        ),
    )
    op.create_index(
        "idx_fanqie_category_profiles_category_date",
        "fanqie_category_profiles",
        ["category", "data_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_fanqie_category_profiles_category_date",
        table_name="fanqie_category_profiles",
    )
    op.drop_table("fanqie_category_profiles")
    op.drop_index(
        "idx_fanqie_competitor_profiles_category_rank",
        table_name="fanqie_competitor_profiles",
    )
    op.drop_table("fanqie_competitor_profiles")
    op.drop_index(
        "idx_fanqie_ranking_snapshots_category_date",
        table_name="fanqie_ranking_snapshots",
    )
    op.drop_table("fanqie_ranking_snapshots")

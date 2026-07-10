"""Add exact-version quality promotion state and audit contracts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0035_quality_promotion_contract"
down_revision = "0034_publishing_history_idempotency"
branch_labels = None
depends_on = None

PROMOTION_STATES_SQL = (
    "'legacy_unverified','candidate','under_review','eligible','promoted',"
    "'superseded','rejected','quarantined'"
)
PORTABLE_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _add_draft_promotion_columns(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(
            sa.Column(
                "promotion_state",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'legacy_unverified'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "promotion_reason_codes",
                PORTABLE_JSON,
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(sa.Column("promotion_score", sa.Numeric(5, 4)))
        batch_op.add_column(sa.Column("promoted_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("quarantined_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column(
                "promotion_metadata",
                PORTABLE_JSON,
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
    draft_table = sa.table(
        table_name,
        sa.column("promotion_state", sa.String(length=32)),
        sa.column("promotion_reason_codes", PORTABLE_JSON),
        sa.column("promotion_metadata", PORTABLE_JSON),
    )
    op.execute(
        draft_table.update().values(
            promotion_state="legacy_unverified",
            promotion_reason_codes=[],
            promotion_metadata={},
        )
    )


def _add_draft_checks(table_name: str, prefix: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.create_check_constraint(
            f"ck_{prefix}_promotion_state",
            f"promotion_state IN ({PROMOTION_STATES_SQL})",
        )
        batch_op.create_check_constraint(
            f"ck_{prefix}_promotion_score_range",
            "promotion_score IS NULL OR (promotion_score >= 0 AND promotion_score <= 1)",
        )


def upgrade() -> None:
    _add_draft_promotion_columns("scene_draft_versions")
    _add_draft_promotion_columns("chapter_draft_versions")

    _add_draft_checks("scene_draft_versions", "scene_draft")
    _add_draft_checks("chapter_draft_versions", "chapter_draft")
    for table_name in ("scene_draft_versions", "chapter_draft_versions"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "promotion_state",
                server_default=sa.text("'candidate'"),
            )
    op.create_index(
        "uq_scene_draft_promoted",
        "scene_draft_versions",
        ["scene_card_id"],
        unique=True,
        postgresql_where=sa.text("promotion_state = 'promoted'"),
        sqlite_where=sa.text("promotion_state = 'promoted'"),
    )
    op.create_index(
        "uq_chapter_draft_promoted",
        "chapter_draft_versions",
        ["chapter_id"],
        unique=True,
        postgresql_where=sa.text("promotion_state = 'promoted'"),
        sqlite_where=sa.text("promotion_state = 'promoted'"),
    )

    with op.batch_alter_table("quality_scores") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scene_draft_version_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "scene_draft_versions.id",
                    name="fk_quality_scores_scene_draft",
                    ondelete="CASCADE",
                ),
            )
        )
        batch_op.add_column(
            sa.Column(
                "chapter_draft_version_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "chapter_draft_versions.id",
                    name="fk_quality_scores_chapter_draft",
                    ondelete="CASCADE",
                ),
            )
        )
        batch_op.add_column(
            sa.Column(
                "evaluation_round",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(sa.Column("judge_key", sa.String(length=128)))
        batch_op.add_column(sa.Column("pairwise_group_id", sa.Uuid()))
        batch_op.create_check_constraint(
            "ck_quality_score_at_most_one_draft",
            "scene_draft_version_id IS NULL OR chapter_draft_version_id IS NULL",
        )
    op.create_index(
        "idx_quality_scores_scene_draft_judge_round",
        "quality_scores",
        ["scene_draft_version_id", "judge_key", "evaluation_round"],
    )
    op.create_index(
        "idx_quality_scores_chapter_draft_judge_round",
        "quality_scores",
        ["chapter_draft_version_id", "judge_key", "evaluation_round"],
    )
    op.create_index(
        "idx_quality_scores_pairwise_group",
        "quality_scores",
        ["pairwise_group_id"],
    )

    op.create_table(
        "draft_promotion_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scene_draft_version_id",
            sa.Uuid(),
            sa.ForeignKey("scene_draft_versions.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "chapter_draft_version_id",
            sa.Uuid(),
            sa.ForeignKey("chapter_draft_versions.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "quality_score_id",
            sa.Uuid(),
            sa.ForeignKey("quality_scores.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "workflow_run_id",
            sa.Uuid(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("from_state", sa.String(length=32), nullable=False),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("decision_source", sa.String(length=32), nullable=False),
        sa.Column(
            "reason_codes",
            PORTABLE_JSON,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("promotion_score", sa.Numeric(5, 4)),
        sa.Column("actor", sa.String(length=128)),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "evidence_json",
            PORTABLE_JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "metadata",
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
        sa.CheckConstraint(
            "((scene_draft_version_id IS NOT NULL AND chapter_draft_version_id IS NULL) "
            "OR (scene_draft_version_id IS NULL AND chapter_draft_version_id IS NOT NULL))",
            name="ck_draft_promotion_decision_exactly_one_draft",
        ),
        sa.CheckConstraint(
            f"from_state IN ({PROMOTION_STATES_SQL})",
            name="ck_draft_promotion_decision_from_state",
        ),
        sa.CheckConstraint(
            f"to_state IN ({PROMOTION_STATES_SQL})",
            name="ck_draft_promotion_decision_to_state",
        ),
        sa.CheckConstraint(
            "promotion_score IS NULL OR (promotion_score >= 0 AND promotion_score <= 1)",
            name="ck_draft_promotion_decision_score_range",
        ),
    )
    op.create_index(
        "idx_draft_promotion_decisions_scene",
        "draft_promotion_decisions",
        ["scene_draft_version_id", "created_at"],
    )
    op.create_index(
        "idx_draft_promotion_decisions_chapter",
        "draft_promotion_decisions",
        ["chapter_draft_version_id", "created_at"],
    )
    op.create_index(
        "idx_draft_promotion_decisions_project",
        "draft_promotion_decisions",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_draft_promotion_decisions_project",
        table_name="draft_promotion_decisions",
    )
    op.drop_index(
        "idx_draft_promotion_decisions_chapter",
        table_name="draft_promotion_decisions",
    )
    op.drop_index(
        "idx_draft_promotion_decisions_scene",
        table_name="draft_promotion_decisions",
    )
    op.drop_table("draft_promotion_decisions")

    op.drop_index("idx_quality_scores_pairwise_group", table_name="quality_scores")
    op.drop_index(
        "idx_quality_scores_chapter_draft_judge_round",
        table_name="quality_scores",
    )
    op.drop_index(
        "idx_quality_scores_scene_draft_judge_round",
        table_name="quality_scores",
    )
    with op.batch_alter_table("quality_scores") as batch_op:
        batch_op.drop_constraint(
            "ck_quality_score_at_most_one_draft",
            type_="check",
        )
        batch_op.drop_column("pairwise_group_id")
        batch_op.drop_column("judge_key")
        batch_op.drop_column("evaluation_round")
        batch_op.drop_column("chapter_draft_version_id")
        batch_op.drop_column("scene_draft_version_id")

    op.drop_index("uq_chapter_draft_promoted", table_name="chapter_draft_versions")
    op.drop_index("uq_scene_draft_promoted", table_name="scene_draft_versions")
    for table_name, prefix in (
        ("chapter_draft_versions", "chapter_draft"),
        ("scene_draft_versions", "scene_draft"),
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                f"ck_{prefix}_promotion_score_range",
                type_="check",
            )
            batch_op.drop_constraint(
                f"ck_{prefix}_promotion_state",
                type_="check",
            )
            batch_op.drop_column("promotion_metadata")
            batch_op.drop_column("quarantined_at")
            batch_op.drop_column("promoted_at")
            batch_op.drop_column("promotion_score")
            batch_op.drop_column("promotion_reason_codes")
            batch_op.drop_column("promotion_state")

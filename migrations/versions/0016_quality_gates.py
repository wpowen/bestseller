"""Quality-gate architecture — Phase 1.

Adds the persistence surface for the 8-layer quality pipeline:

* ``projects.invariants_json`` (JSONB) — L1 per-project immutable contract.
* ``chapters.production_state`` (VARCHAR(20)) — L6 write-gate outcome, so a
  resume path can tell the difference between "never generated" and
  "generated then blocked".
* ``chapter_quality_reports`` — raw L4/L5 ``QualityReport`` persisted per
  chapter attempt; used by dashboards to chart ``block_rate`` /
  ``false_positive_rate`` per violation code.
* ``chapter_audit_findings`` — L7 post-generation audit output.
* ``novel_scorecards`` — L8 aggregated per-project scorecard.
* ``diversity_budgets`` — tracks opening/cliffhanger/vocab rotation so L3
  prompt construction can enforce non-repetition across chapters.

The migration is additive only — no existing row is touched, and the
defaults are chosen so the pipeline keeps running untouched until the
service-layer code starts populating the new columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0016_quality_gates"
down_revision = "0015_widen_extraction_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("invariants_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_projects_invariants_json",
        "projects",
        ["invariants_json"],
        postgresql_using="gin",
    )

    op.add_column(
        "chapters",
        sa.Column(
            "production_state",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.create_index(
        "ix_chapters_production_state",
        "chapters",
        ["production_state"],
    )

    op.create_table(
        "diversity_budgets",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "openings_used",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "cliffhangers_used",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "titles_used",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "vocab_freq",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_table(
        "chapter_quality_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("regen_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("blocks_write", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_chapter_quality_reports_chapter_id",
        "chapter_quality_reports",
        ["chapter_id"],
    )

    op.create_table(
        "chapter_audit_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_no", sa.Integer(), nullable=True),
        sa.Column("auditor", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "auto_repairable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("repair_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repair_success", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_chapter_audit_findings_project_id",
        "chapter_audit_findings",
        ["project_id"],
    )
    op.create_index(
        "ix_chapter_audit_findings_code",
        "chapter_audit_findings",
        ["code"],
    )

    op.create_table(
        "novel_scorecards",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("quality_score", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("novel_scorecards")
    op.drop_index("ix_chapter_audit_findings_code", table_name="chapter_audit_findings")
    op.drop_index("ix_chapter_audit_findings_project_id", table_name="chapter_audit_findings")
    op.drop_table("chapter_audit_findings")
    op.drop_index("ix_chapter_quality_reports_chapter_id", table_name="chapter_quality_reports")
    op.drop_table("chapter_quality_reports")
    op.drop_table("diversity_budgets")
    op.drop_index("ix_chapters_production_state", table_name="chapters")
    op.drop_column("chapters", "production_state")
    op.drop_index("ix_projects_invariants_json", table_name="projects")
    op.drop_column("projects", "invariants_json")

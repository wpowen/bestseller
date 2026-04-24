"""Phase C2/C3 — Override Contracts + Chase Debt Ledger.

Also resolves the 0024 branch point: ``0024_line_dominance`` (Phase B1)
and ``0024_title_patterns`` (an unrelated mid-flight repair) both
descended from ``0023_cross_project_fingerprint``. This revision
merges them and adds the two Phase C tables in the same step.

New tables:

* ``override_contracts`` — one row per signed soft-constraint waiver.
  The write gate consults the active subset via
  ``OverrideStore.as_lookup`` to downgrade ``block`` → ``audit_only``
  for the covered chapter window.

* ``chase_debts`` — interest-accruing debt spawned either by an
  ``override_contracts`` row (``source='override_contract'``) or by the
  legacy setup→payoff tracker (``source='setup_payoff'``). Balance
  accrues at ``interest_rate`` per chapter until the author closes the
  debt; overdue rows are surfaced in the scorecard.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0025_override_debt"
# Merge migration — both 0024 heads reduce to this single parent.
down_revision = ("0024_line_dominance", "0024_title_patterns")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "override_contracts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_no", sa.Integer(), nullable=False),
        sa.Column("violation_code", sa.String(length=64), nullable=False),
        sa.Column("rationale_type", sa.String(length=48), nullable=False),
        sa.Column("rationale_text", sa.Text(), nullable=False),
        sa.Column("payback_plan", sa.Text(), nullable=False),
        sa.Column("due_chapter", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "due_chapter > chapter_no",
            name="ck_override_due_after_chapter",
        ),
    )
    op.create_index(
        "ix_override_project_status",
        "override_contracts",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_override_project_chapter",
        "override_contracts",
        ["project_id", "chapter_no"],
    )

    op.create_table(
        "chase_debts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "override_contract_id",
            sa.BigInteger(),
            sa.ForeignKey("override_contracts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chapter_no", sa.Integer(), nullable=False),
        sa.Column("violation_code", sa.String(length=64), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'override_contract'"),
        ),
        sa.Column("principal", sa.Numeric(10, 4), nullable=False),
        sa.Column("balance", sa.Numeric(10, 4), nullable=False),
        sa.Column(
            "interest_rate",
            sa.Numeric(6, 4),
            nullable=False,
            server_default=sa.text("0.10"),
        ),
        sa.Column("accrued_through_chapter", sa.Integer(), nullable=False),
        sa.Column("due_chapter", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_debt_project_status",
        "chase_debts",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_debt_due_chapter",
        "chase_debts",
        ["project_id", "due_chapter"],
    )


def downgrade() -> None:
    op.drop_index("ix_debt_due_chapter", table_name="chase_debts")
    op.drop_index("ix_debt_project_status", table_name="chase_debts")
    op.drop_table("chase_debts")
    op.drop_index("ix_override_project_chapter", table_name="override_contracts")
    op.drop_index("ix_override_project_status", table_name="override_contracts")
    op.drop_table("override_contracts")

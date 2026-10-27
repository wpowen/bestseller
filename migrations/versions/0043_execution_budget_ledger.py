"""Durable paid-execution reservations, operation results and transport attempts.

No project foreign key: conception precedes project creation and evidence must
survive workflow rollback/deletion. Historical llm_runs are adopted lazily once
per stable scope by the gateway, not reset by this additive migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0043_execution_budget_ledger"
down_revision = "0042_ranking_evidence_authority"
branch_labels = None
depends_on = None
JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "execution_budgets",
        sa.Column("scope_id", sa.String(256), primary_key=True),
        sa.Column("max_calls", sa.Integer, nullable=False),
        sa.Column("max_tokens", sa.BigInteger, nullable=False),
        sa.Column("calls_used", sa.Integer, nullable=False),
        sa.Column("tokens_charged", sa.BigInteger, nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata_json", JSON, nullable=False),
    )
    op.create_table(
        "execution_operations",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column(
            "scope_id", sa.String(256), sa.ForeignKey("execution_budgets.scope_id"), nullable=False
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_json", JSON),
    )
    op.create_index("ix_execution_operations_scope_id", "execution_operations", ["scope_id"])
    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "operation_key",
            sa.String(64),
            sa.ForeignKey("execution_operations.key"),
            nullable=False,
        ),
        sa.Column(
            "scope_id", sa.String(256), sa.ForeignKey("execution_budgets.scope_id"), nullable=False
        ),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger, nullable=False),
        sa.Column("actual_tokens", sa.BigInteger),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_execution_attempts_operation_key", "execution_attempts", ["operation_key"])
    op.create_index("ix_execution_attempts_scope_id", "execution_attempts", ["scope_id"])


def downgrade() -> None:
    # Explicit destructive downgrade only; never used for workflow recovery.
    op.drop_table("execution_attempts")
    op.drop_table("execution_operations")
    op.drop_table("execution_budgets")

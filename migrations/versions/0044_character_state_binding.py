"""Add nullable character snapshot evidence; preserve all historical rows unbound."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044_character_state_binding"
down_revision = "0043_execution_budget_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character_state_snapshots",
        sa.Column(
            "source_binding",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("character_state_snapshots", "source_binding")

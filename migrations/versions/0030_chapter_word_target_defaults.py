from __future__ import annotations

from alembic import op


revision = "0030_chapter_word_target_defaults"
down_revision = "0029_project_delete_fk_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chapters ALTER COLUMN target_word_count SET DEFAULT 2200")


def downgrade() -> None:
    op.execute("ALTER TABLE chapters ALTER COLUMN target_word_count SET DEFAULT 5500")

from __future__ import annotations

from alembic import op


revision = "0006_chapter_word_defaults"
down_revision = "0005_emotion_antagonists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chapters ALTER COLUMN target_word_count SET DEFAULT 5500")


def downgrade() -> None:
    op.execute("ALTER TABLE chapters ALTER COLUMN target_word_count SET DEFAULT 3000")

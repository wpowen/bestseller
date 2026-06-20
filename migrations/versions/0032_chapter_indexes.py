"""Add ChapterModel access-path indexes.

The chapters table carried only the ``(project_id, chapter_number)`` unique
constraint, so frequent lookups by ``volume_id``, ``pov_character_id``, and
``(project_id, status / production_state)`` were sequential scans on a hot
table. Add covering indexes. (Greenfield databases get these from the model
metadata via the bootstrap baseline; this migration covers existing DBs.)
"""

from __future__ import annotations

from alembic import op


revision = "0032_chapter_indexes"
down_revision = "0031_fanqie_market_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_chapters_volume ON chapters (volume_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapters_pov_character ON chapters (pov_character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapters_project_production_state "
        "ON chapters (project_id, production_state)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapters_project_status "
        "ON chapters (project_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chapters_project_status")
    op.execute("DROP INDEX IF EXISTS idx_chapters_project_production_state")
    op.execute("DROP INDEX IF EXISTS idx_chapters_pov_character")
    op.execute("DROP INDEX IF EXISTS idx_chapters_volume")

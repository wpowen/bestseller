"""Character lifecycle — alive_status / stance tracking.

Adds lifecycle columns so the writing pipeline can enforce "dead stays
dead" and "stance only flips on a milestone event":

* ``characters.alive_status`` — current aliveness (``alive`` |
  ``injured`` | ``dying`` | ``deceased``). Defaults to ``alive``;
  feedback extraction is allowed to move it forward but never
  resurrect.
* ``characters.death_chapter_number`` — stamped the first time the
  feedback loop marks a character ``deceased``. Read by the
  contradiction ``_check_resurrection`` gate and by the drafts
  "本书已故角色" prompt section.
* ``characters.stance`` — current ally / enemy / neutral / conflicted
  position toward the protagonist. Nullable so historical rows stay
  valid without backfill; the bible materializer seeds it from the
  role field for new cast spec upserts.
* ``characters.stance_locked_until_chapter`` — inclusive chapter
  beyond which stance must not flip without an explicit ArcBeat turn
  point, enforced by ``StanceFlipJustificationCheck``.
* ``character_state_snapshots.alive_status`` /
  ``character_state_snapshots.stance`` — per-snapshot capture used by
  ``get_effective_character_state`` to resolve "most recent non-null".

Migration is additive-only. Existing rows default to ``alive_status='alive'``
and ``stance=NULL``; nothing rewrites historical data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0020_character_lifecycle"
down_revision = "0019_hype_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column(
            "alive_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'alive'"),
        ),
    )
    op.add_column(
        "characters",
        sa.Column("death_chapter_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "characters",
        sa.Column("stance", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "characters",
        sa.Column("stance_locked_until_chapter", sa.Integer(), nullable=True),
    )

    op.add_column(
        "character_state_snapshots",
        sa.Column("alive_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "character_state_snapshots",
        sa.Column("stance", sa.String(length=32), nullable=True),
    )

    op.create_index(
        "ix_characters_project_alive_status",
        "characters",
        ["project_id", "alive_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_characters_project_alive_status", table_name="characters")
    op.drop_column("character_state_snapshots", "stance")
    op.drop_column("character_state_snapshots", "alive_status")
    op.drop_column("characters", "stance_locked_until_chapter")
    op.drop_column("characters", "stance")
    op.drop_column("characters", "death_chapter_number")
    op.drop_column("characters", "alive_status")

"""Commercial-novel quality anchors — Phase 2.

Pushes defect prevention upstream by persisting the "static cores" and
"dynamic rotation axes" commercial bestsellers rely on to stay fresh:

* ``characters.quirks_json`` / ``sensory_signatures_json`` /
  ``signature_objects_json`` / ``core_wound`` — per-character IP anchors.
  Commercial readers remember characters by 3+ concrete quirks, a signature
  object, and a core psychological wound. Without these, protagonists blur.
* ``chapters`` dynamic axes — ``opening_archetype``, ``ending_cliff_type``,
  ``primary_emotion``, ``conflict_type``, ``scene_pacing``, ``location_tag``,
  ``pov_character_id``. Each axis is one rotation dimension L3 uses to
  prevent "every chapter feels the same" (problems 5/7/10/11/12).
* ``projects.theme_statement`` / ``dramatic_question`` /
  ``reader_contract_json`` — the meta-frame every decision echoes back to.
* ``foreshadowing_ledger`` — setup/payoff tracking so hooks don't vanish
  unresolved (root cause of "emotional promises not delivered").

Additive only: every existing row stays valid; the new anchors default to
empty so pre-gate novels keep loading. L2 Bible Gate (shipped in the same
phase) enforces non-emptiness on *new* generations.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0017_commercial_anchors"
down_revision = "0016_quality_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- Character IP anchors ----------------
    op.add_column(
        "characters",
        sa.Column(
            "quirks_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "characters",
        sa.Column(
            "sensory_signatures_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "characters",
        sa.Column(
            "signature_objects_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "characters",
        sa.Column("core_wound", sa.Text(), nullable=True),
    )

    # ---------------- Chapter dynamic rotation axes ----------------
    # VARCHAR + no FK on vocab-tag columns: we want cheap rotation lookups
    # without a full enum table per axis (new values land as the writer room
    # invents them).
    op.add_column(
        "chapters",
        sa.Column("opening_archetype", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("ending_cliff_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("primary_emotion", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("conflict_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("scene_pacing", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("location_tag", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column(
            "pov_character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Rotation-lookup indexes (the three axes L3 queries every chapter).
    op.create_index(
        "ix_chapters_opening_archetype",
        "chapters",
        ["project_id", "opening_archetype"],
    )
    op.create_index(
        "ix_chapters_ending_cliff_type",
        "chapters",
        ["project_id", "ending_cliff_type"],
    )
    op.create_index(
        "ix_chapters_primary_emotion",
        "chapters",
        ["project_id", "primary_emotion"],
    )

    # ---------------- Project-level anchors ----------------
    op.add_column(
        "projects",
        sa.Column("theme_statement", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("dramatic_question", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "reader_contract_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # ---------------- Foreshadowing ledger ----------------
    op.create_table(
        "foreshadowing_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("setup_chapter_no", sa.Integer(), nullable=False),
        sa.Column("planned_payoff_chapter_no", sa.Integer(), nullable=True),
        sa.Column("actual_payoff_chapter_no", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'planned'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_foreshadowing_project_status",
        "foreshadowing_ledger",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_foreshadowing_project_setup",
        "foreshadowing_ledger",
        ["project_id", "setup_chapter_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_foreshadowing_project_setup", table_name="foreshadowing_ledger")
    op.drop_index("ix_foreshadowing_project_status", table_name="foreshadowing_ledger")
    op.drop_table("foreshadowing_ledger")

    op.drop_column("projects", "reader_contract_json")
    op.drop_column("projects", "dramatic_question")
    op.drop_column("projects", "theme_statement")

    op.drop_index("ix_chapters_primary_emotion", table_name="chapters")
    op.drop_index("ix_chapters_ending_cliff_type", table_name="chapters")
    op.drop_index("ix_chapters_opening_archetype", table_name="chapters")
    op.drop_column("chapters", "pov_character_id")
    op.drop_column("chapters", "location_tag")
    op.drop_column("chapters", "scene_pacing")
    op.drop_column("chapters", "conflict_type")
    op.drop_column("chapters", "primary_emotion")
    op.drop_column("chapters", "ending_cliff_type")
    op.drop_column("chapters", "opening_archetype")

    op.drop_column("characters", "core_wound")
    op.drop_column("characters", "signature_objects_json")
    op.drop_column("characters", "sensory_signatures_json")
    op.drop_column("characters", "quirks_json")

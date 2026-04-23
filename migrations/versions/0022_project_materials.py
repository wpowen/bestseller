"""Project Materials — per-project derivations from the global material library.

Batch 2 of the multi-dimensional material library refactor.  Each project
gets its own ``project_materials`` rows forged by the 5 Forge agents:

  WorldForge       → world_settings, factions, locale_templates
  PowerSystemForge → power_systems
  CharacterForge   → character_archetypes, character_templates
  PlotForge        → plot_patterns (main_line, sub_line), scene_templates
  DeviceForge      → device_templates, thematic_motifs

Unlike the global ``material_library`` table, rows here are project-scoped
(FK to ``projects.id``).  Two projects can have a row with the same base
``slug`` because the ``UNIQUE(project_id, material_type, slug)`` constraint
scopes uniqueness per project × dimension.

Relationships to the global library are stored in
``source_library_ids_json`` (list of integer IDs from ``material_library``).
``promoted_to_library_id`` is NULL until a Batch-3 novelty_critic approves
the entry for promotion back into ``material_library``.

Feature flag ``enable_forge_pipeline`` gates all Forge invocations, so
deploying this migration alone has zero behavioural impact on existing
projects.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_project_materials"
down_revision = "0021_material_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_materials",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        # ── Project FK ─────────────────────────────────────────────
        # projects.id is a UUID (PGUUID) column — the FK must match.
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ── Classification ─────────────────────────────────────────
        # material_type mirrors material_library.dimension
        sa.Column("material_type", sa.String(length=48), nullable=False),
        # ── Content ────────────────────────────────────────────────
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("narrative_summary", sa.Text(), nullable=False),
        # ── Library provenance ─────────────────────────────────────
        # JSON array of integer material_library.id values that the
        # Forge agent used as seed entries for this derivation.
        sa.Column(
            "source_library_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Notes describing how the Forge differentiated this entry
        # from the library seed entries.
        sa.Column("variation_notes", sa.Text(), nullable=True),
        # ── Promotion (Batch 3) ────────────────────────────────────
        # Populated by novelty_critic when this entry is backfilled
        # into the global material_library.
        sa.Column("promoted_to_library_id", sa.BigInteger(), nullable=True),
        # ── Lifecycle ──────────────────────────────────────────────
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
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id",
            "material_type",
            "slug",
            name="uq_pm_project_type_slug",
        ),
    )

    # ── Lookup indexes ─────────────────────────────────────────────────────
    op.create_index(
        "ix_pm_project_type",
        "project_materials",
        ["project_id", "material_type"],
    )
    op.create_index(
        "ix_pm_status",
        "project_materials",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_pm_status", table_name="project_materials")
    op.drop_index("ix_pm_project_type", table_name="project_materials")
    op.drop_table("project_materials")

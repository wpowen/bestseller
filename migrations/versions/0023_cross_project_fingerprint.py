"""Cross-Project Fingerprint — entity-level novelty guard.

Batch 3 of the multi-dimensional material library refactor.

Each time a Forge emits a ``project_materials`` row its name +
``narrative_summary`` are encoded as a hashed embedding and stored here
so future Forges in the same genre can detect near-duplicate entities
*across projects*.

The novelty check in ``bestseller.services.novelty_critic`` uses two layers:

  1. Exact ``entity_name`` match (same genre + dimension)  → immediate block.
  2. Cosine similarity above a configurable threshold      → block.

Additionally, ``material_library.usage_count`` is checked against a warn
limit so the Forge's variation instructions can call out over-used seeds.

The table stores embeddings as ``JSONB`` float arrays rather than
``pgvector`` so the check can run without adding an HNSW index (the
genre × dimension buckets are small enough for in-Python cosine scans).

Feature flag ``enable_novelty_guard`` (default False) gates all calls so
deploying this migration alone has zero behavioural impact.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0023_cross_project_fingerprint"
down_revision = "0022_project_materials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cross_project_fingerprints",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        # ── Origin ─────────────────────────────────────────────────────────
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ── Scope ──────────────────────────────────────────────────────────
        # Checks are always scoped to genre + dimension so an xianxia name
        # never collides with a scifi name.
        sa.Column("genre", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=48), nullable=False),
        # ── Identity ───────────────────────────────────────────────────────
        # Lower-cased entity name for O(1) exact-match collision detection.
        sa.Column("entity_name", sa.String(length=200), nullable=True),
        sa.Column("slug", sa.String(length=160), nullable=False),
        # Hashed embedding of (name + " " + narrative_summary) stored as a
        # JSONB float array.  No pgvector required; cosine is computed in
        # Python across the (small) genre×dimension bucket.
        sa.Column(
            "embedding_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Informational back-link to the project_materials row that
        # produced this fingerprint (no FK constraint; rows can be deleted
        # independently).
        sa.Column("source_material_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    # Primary lookup: all fingerprints in the same genre × dimension bucket.
    op.create_index(
        "ix_cpf_genre_dim",
        "cross_project_fingerprints",
        ["genre", "dimension"],
    )
    # Secondary: all fingerprints for a given project (cleanup / reporting).
    op.create_index(
        "ix_cpf_project",
        "cross_project_fingerprints",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cpf_project", table_name="cross_project_fingerprints")
    op.drop_index("ix_cpf_genre_dim", table_name="cross_project_fingerprints")
    op.drop_table("cross_project_fingerprints")

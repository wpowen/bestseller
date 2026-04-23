"""Material Library — global multi-dimensional research corpus.

Adds the ``material_library`` table, a system-wide knowledge base that
holds structured research entries (world settings, power systems,
factions, character archetypes, plot patterns, …) indexed by both
``dimension`` + ``genre`` and by a pgvector HNSW index for semantic
retrieval.

This is the foundation for the "multi-dimensional material library +
Research → Forge → Reference-style generation" refactor documented in
plan ``twinkly-rolling-pnueli.md``.  The table is deliberately designed
to be:

* **Multi-dimensional** — the ``dimension`` column is a *string* (not an
  enum) so the taxonomy can grow without schema migrations.
* **Genre-aware but cross-genre reusable** — ``genre``/``sub_genre`` may
  be NULL for entries that apply to every genre.
* **Source-annotated** — ``source_type`` / ``source_citations_json`` /
  ``confidence`` let the Research Agent, LLM-synth, and user-curated
  entries coexist.
* **Usage-counted** — ``usage_count`` / ``last_used_at`` drive the
  cross-project novelty guard that blocks "everyone writes 方域".

All columns are additive; the migration does not touch any existing
table.  Feature flag ``enable_material_library`` gates all reads and
writes, so deploying this migration alone has zero behavioural impact.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0021_material_library"
down_revision = "0020_character_lifecycle"
branch_labels = None
depends_on = None


# Embedding dimensions — matches the default BAAI/bge-m3 model configured
# in ``config/default.yaml`` (``retrieval.embedding_dimensions: 1024``).
# Kept in sync with ``retrieval_chunks.embedding`` (also Vector(1024)).
_EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        "material_library",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        # ── Classification ───────────────────────────────────────────
        sa.Column("dimension", sa.String(length=48), nullable=False),
        sa.Column("genre", sa.String(length=64), nullable=True),
        sa.Column("sub_genre", sa.String(length=64), nullable=True),
        sa.Column(
            "tags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # ── Content ──────────────────────────────────────────────────
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("narrative_summary", sa.Text(), nullable=False),
        # Semantic retrieval embedding — nullable so entries can be
        # inserted first and re-embedded in a batch job later.
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float()),  # Overridden below via raw SQL.
            nullable=True,
        ),
        # ── Provenance ───────────────────────────────────────────────
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column(
            "source_citations_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column("coverage_score", sa.Float(), nullable=True),
        # ── Lifecycle ────────────────────────────────────────────────
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "usage_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("dimension", "slug", name="uq_material_dimension_slug"),
    )

    # ── Replace the placeholder embedding column with a real pgvector
    # column.  Alembic's ``autogen`` cannot emit Vector() directly so we
    # drop + re-add via raw SQL.  This runs inside the same upgrade tx so
    # the final column definition is pgvector.Vector(1024).
    op.execute(sa.text("ALTER TABLE material_library DROP COLUMN embedding"))
    op.execute(
        sa.text(
            f"ALTER TABLE material_library ADD COLUMN embedding "
            f"vector({_EMBEDDING_DIM})"
        )
    )

    # ── Lookup indexes ───────────────────────────────────────────────
    op.create_index(
        "ix_material_dim_genre",
        "material_library",
        ["dimension", "genre", "sub_genre"],
    )
    op.create_index(
        "ix_material_status",
        "material_library",
        ["status"],
    )
    op.create_index(
        "ix_material_usage",
        "material_library",
        ["usage_count"],
    )

    # ── Vector index (HNSW, cosine) ──────────────────────────────────
    # Wrapped in IF NOT EXISTS so re-running the migration on a
    # partially-created table stays idempotent.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_material_embedding_hnsw "
            "ON material_library USING hnsw (embedding vector_cosine_ops)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_material_embedding_hnsw"))
    op.drop_index("ix_material_usage", table_name="material_library")
    op.drop_index("ix_material_status", table_name="material_library")
    op.drop_index("ix_material_dim_genre", table_name="material_library")
    op.drop_table("material_library")

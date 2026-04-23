"""novelty_critic — Cross-project entity-level novelty guard.

Batch 3 of the multi-dimensional material library refactor.

Two-layer novelty check applied before a Forge persists a new
``project_materials`` entry:

  Layer 1 — Exact entity_name collision
      A case-insensitive match against ``cross_project_fingerprints`` in the
      same genre + dimension → immediate block.  Prevents "方域" appearing in
      every xianxia project.

  Layer 2 — Cosine similarity threshold
      The fingerprint text (``name + " " + narrative_summary``) is embedded
      with the same hashed embedding used by the retrieval system
      (``build_hashed_embedding``).  If any existing fingerprint in the same
      genre × dimension bucket has cosine ≥ threshold (default 0.85), the
      entry is blocked.

Additionally, :func:`check_novelty` can warn when seed entries from
``material_library`` are over-used (``usage_count ≥ usage_count_limit``)
so the Forge prompt can explicitly call out those over-used entries.

Usage (inside ``BaseForge._build_emit_tool``)::

    if settings.pipeline.enable_novelty_guard:
        verdict = await check_novelty(
            session,
            genre=genre,
            dimension=dimension,
            entity_name=mat.name,
            narrative_summary=mat.narrative_summary,
            source_library_ids=mat.source_library_ids,
        )
        if not verdict.ok:
            return {"error": f"novelty_block:{verdict.reason}"}
        await register_fingerprint(
            session,
            project_id=mat.project_id,
            genre=genre,
            dimension=dimension,
            entity_name=mat.name,
            slug=mat.slug,
            narrative_summary=mat.narrative_summary,
        )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.retrieval import build_hashed_embedding, cosine_similarity

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

_DEFAULT_COSINE_THRESHOLD: float = 0.85
_DEFAULT_USAGE_WARN_LIMIT: int = 8
_EMBED_DIM: int = 1024


# ── Data Transfer Objects ─────────────────────────────────────────────────


@dataclass(frozen=True)
class NoveltyVerdict:
    """Result of a novelty check.

    Attributes
    ----------
    ok:
        ``True`` when the entry is novel enough to accept; ``False`` if
        it should be rejected and the Forge should regenerate.
    reason:
        Machine-readable code:
        * ``"ok"``                  — passed all checks.
        * ``"exact_name_collision"``— same lower-cased name already exists.
        * ``"cosine_too_high"``     — embedding is too similar to an existing
                                      entry in the same genre × dimension.
        * ``"usage_count_warning"`` — ok=True but seed entries are over-used;
                                      Forge should diverge from those seeds.
    conflicting_project_id:
        Project that owns the conflicting fingerprint (for logging).
    similarity_score:
        Cosine similarity that triggered a ``cosine_too_high`` block (0–1).
    overused_library_ids:
        ``material_library.id`` values whose ``usage_count`` ≥ the warn limit.
        Only populated when ``reason == "usage_count_warning"``.
    """

    ok: bool
    reason: str
    conflicting_project_id: str | None = None
    similarity_score: float = 0.0
    overused_library_ids: list[int] = field(default_factory=list)


# ── Embedding helpers ─────────────────────────────────────────────────────


def _fingerprint_text(name: str, narrative_summary: str) -> str:
    """Canonical text used to produce the embedding for a material entry."""
    return f"{name.strip()} {narrative_summary.strip()}".strip()


def compute_fingerprint_embedding(name: str, narrative_summary: str) -> list[float]:
    """Return a hashed embedding for *name* + *narrative_summary* (1024-dim)."""
    return build_hashed_embedding(_fingerprint_text(name, narrative_summary), _EMBED_DIM)


# ── Core novelty check ────────────────────────────────────────────────────


async def check_novelty(
    session: AsyncSession,
    *,
    genre: str,
    dimension: str,
    entity_name: str,
    narrative_summary: str,
    threshold: float = _DEFAULT_COSINE_THRESHOLD,
    usage_count_limit: int = _DEFAULT_USAGE_WARN_LIMIT,
    source_library_ids: list[int] | None = None,
) -> NoveltyVerdict:
    """Check whether a proposed entity is novel enough to persist.

    Parameters
    ----------
    session:
        Active SQLAlchemy async session.
    genre:
        Genre of the project being forged (scope for cross-project search).
        Checks are **never** cross-genre — an xianxia name "方域" does not
        block a scifi name "方域".
    dimension:
        Material dimension (e.g. ``"character_templates"``).
    entity_name:
        Display name of the proposed entry.
    narrative_summary:
        1–3 sentence summary used for semantic embedding comparison.
    threshold:
        Cosine similarity cutoff above which the entry is treated as a clone.
    usage_count_limit:
        Warn (but do not block) if any seed in ``source_library_ids`` has
        ``usage_count ≥ usage_count_limit``.
    source_library_ids:
        ``material_library.id`` values used as seeds; checked for overuse.
    """
    from bestseller.infra.db.models import CrossProjectFingerprintModel  # noqa: PLC0415

    # ── Layer 1: Exact lower-cased name collision ──────────────────────────
    name_lower = entity_name.strip().lower()
    exact_rows = list(
        await session.scalars(
            select(CrossProjectFingerprintModel).where(
                CrossProjectFingerprintModel.genre == genre,
                CrossProjectFingerprintModel.dimension == dimension,
                CrossProjectFingerprintModel.entity_name == name_lower,
            )
        )
    )
    if exact_rows:
        conflicting = str(exact_rows[0].project_id)
        logger.warning(
            "novelty_critic: exact name collision genre=%s dim=%s name=%r "
            "conflicting_project=%s",
            genre,
            dimension,
            entity_name,
            conflicting,
        )
        return NoveltyVerdict(
            ok=False,
            reason="exact_name_collision",
            conflicting_project_id=conflicting,
        )

    # ── Layer 2: Cosine similarity scan over genre × dimension bucket ──────
    query_vec = compute_fingerprint_embedding(entity_name, narrative_summary)
    candidate_rows = list(
        await session.scalars(
            select(CrossProjectFingerprintModel).where(
                CrossProjectFingerprintModel.genre == genre,
                CrossProjectFingerprintModel.dimension == dimension,
                CrossProjectFingerprintModel.embedding_json.is_not(None),
            )
        )
    )
    for row in candidate_rows:
        stored = list(row.embedding_json or [])
        if not stored:
            continue
        sim = cosine_similarity(query_vec, stored)
        if sim >= threshold:
            conflicting = str(row.project_id)
            logger.warning(
                "novelty_critic: cosine block genre=%s dim=%s sim=%.3f "
                "threshold=%.2f slug=%s conflicting_project=%s",
                genre,
                dimension,
                sim,
                threshold,
                row.slug,
                conflicting,
            )
            return NoveltyVerdict(
                ok=False,
                reason="cosine_too_high",
                conflicting_project_id=conflicting,
                similarity_score=round(sim, 4),
            )

    # ── Layer 3: Library seed over-use warning (non-blocking) ─────────────
    overused_ids: list[int] = []
    if source_library_ids:
        from bestseller.infra.db.models import MaterialLibraryModel  # noqa: PLC0415

        lib_rows = list(
            await session.scalars(
                select(MaterialLibraryModel).where(
                    MaterialLibraryModel.id.in_(source_library_ids),
                )
            )
        )
        for lib_row in lib_rows:
            if lib_row.usage_count >= usage_count_limit:
                overused_ids.append(lib_row.id)

    if overused_ids:
        logger.info(
            "novelty_critic: seed overuse warning genre=%s dim=%s "
            "overused_ids=%s — ok=True, forge should diverge further",
            genre,
            dimension,
            overused_ids,
        )
        return NoveltyVerdict(
            ok=True,
            reason="usage_count_warning",
            overused_library_ids=overused_ids,
        )

    return NoveltyVerdict(ok=True, reason="ok")


# ── Register fingerprint ──────────────────────────────────────────────────


async def register_fingerprint(
    session: AsyncSession,
    *,
    project_id: Any,  # str or UUID — both accepted
    genre: str,
    dimension: str,
    entity_name: str,
    slug: str,
    narrative_summary: str,
    source_material_id: int | None = None,
) -> None:
    """Persist a fingerprint for a newly emitted project material.

    Called by ``BaseForge._build_emit_tool`` after a successful emit so
    future Forges in the same genre can detect near-duplicate entities.

    Parameters
    ----------
    session:
        Active SQLAlchemy async session.
    project_id:
        UUID or string project identifier.
    genre, dimension:
        Scope of the fingerprint.
    entity_name:
        Display name of the entity (stored lower-cased).
    slug:
        Kebab-case slug of the project_materials row.
    narrative_summary:
        Used together with *entity_name* to build the embedding.
    source_material_id:
        ``project_materials.id`` of the row that produced this fingerprint
        (informational, no FK constraint).
    """
    from bestseller.infra.db.models import CrossProjectFingerprintModel  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

    embedding = compute_fingerprint_embedding(entity_name, narrative_summary)

    stmt = pg_insert(CrossProjectFingerprintModel).values(
        project_id=project_id,
        genre=genre,
        dimension=dimension,
        entity_name=entity_name.strip().lower(),
        slug=slug,
        embedding_json=embedding,
        source_material_id=source_material_id,
    )
    await session.execute(stmt)
    logger.debug(
        "novelty_critic: registered fingerprint project=%s genre=%s dim=%s slug=%s",
        project_id,
        genre,
        dimension,
        slug,
    )

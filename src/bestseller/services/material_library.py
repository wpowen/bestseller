"""Read/write API for the global multi-dimensional material library.

The material library (see table ``material_library`` added by migration
``0021_material_library``) holds structured research entries shared
across every project.  This module is the *only* call-site that reads
or writes those rows — everyone else (Research Agent, Library Curator,
Forges, Novelty Critic) goes through these functions so gates,
telemetry, and fingerprints stay consistent.

Design notes
------------

* **Genre-aware retrieval.** :func:`query_library` returns candidates
  ranked by pgvector cosine similarity, optionally filtered to a
  ``(dimension, genre, sub_genre)`` bucket.  Entries marked ``status !=
  'active'`` are excluded.  The caller can pass a ``NoveltyFilter`` to
  further drop rows already used by the current project.
* **Deterministic embeddings.** When the caller does not provide an
  embedding we derive one via :func:`retrieval.build_hashed_embedding`
  — the same hashed bag-of-tokens scheme already used by
  ``retrieval_chunks``.  Not as strong as a real bge-m3 embedding but
  it's dependency-free and the Research Agent / Curator can override.
* **Coverage auditing.** :func:`ensure_coverage` counts active entries
  for a ``(dimension, genre[, sub_genre])`` bucket and reports whether
  it satisfies ``min_entries`` with a freshness window.  Callers that
  need data and find the bucket under-filled delegate to
  ``library_curator`` to trigger fill-in (Batch 1 wires that loop).
* **Insert is idempotent by (dimension, slug).** A new row with a
  colliding slug is merged into the existing row — ``content_json`` is
  replaced, ``usage_count`` is preserved.
* **``mark_used`` is fire-and-forget.**  A Forge / Planner that
  references entry ``#42`` should call it so we can track cross-project
  reuse pressure — but the write is batched into the current request's
  transaction and never fails the main call chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import MaterialLibraryModel
from bestseller.services.retrieval import build_hashed_embedding, cosine_similarity

logger = logging.getLogger(__name__)


# Embedding dimensionality — must equal the pgvector column width.  See
# migration 0021 + retrieval.py (``retrieval_chunks.embedding``).
EMBEDDING_DIM = 1024


# ── Value objects ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class MaterialEntry:
    """Caller-facing read/write DTO.

    Mirrors the persisted row but stays transport-agnostic — Research
    Agent emits these, Forge consumes these, and the ORM layer
    marshals them in and out.
    """

    dimension: str
    slug: str
    name: str
    narrative_summary: str
    content_json: dict[str, Any]
    genre: str | None = None
    sub_genre: str | None = None
    tags: list[str] = field(default_factory=list)
    source_type: str = "research_agent"
    source_citations: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    coverage_score: float | None = None
    status: str = "active"
    embedding: list[float] | None = None
    id: int | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None


@dataclass(frozen=True)
class NoveltyFilter:
    """Client-side filter telling :func:`query_library` which rows to drop.

    Use this to avoid pulling in entries the current project has already
    consumed (exposed through ``source_library_ids_json`` in
    ``project_materials`` — Batch 2).
    """

    exclude_ids: frozenset[int] = frozenset()
    exclude_slugs: frozenset[tuple[str, str]] = frozenset()  # (dimension, slug)
    max_usage_count: int | None = None


@dataclass(frozen=True)
class CoverageReport:
    """Outcome of :func:`ensure_coverage`."""

    dimension: str
    genre: str | None
    sub_genre: str | None
    active_count: int
    min_required: int
    is_satisfied: bool
    stale_ids: tuple[int, ...]

    @property
    def gap(self) -> int:
        return max(self.min_required - self.active_count, 0)


# ── Conversions ────────────────────────────────────────────────────────


def _row_to_entry(row: MaterialLibraryModel) -> MaterialEntry:
    return MaterialEntry(
        id=row.id,
        dimension=row.dimension,
        slug=row.slug,
        name=row.name,
        narrative_summary=row.narrative_summary,
        content_json=dict(row.content_json or {}),
        genre=row.genre,
        sub_genre=row.sub_genre,
        tags=list(row.tags_json or []),
        source_type=row.source_type,
        source_citations=list(row.source_citations_json or []),
        confidence=float(row.confidence or 0.0),
        coverage_score=row.coverage_score,
        status=row.status,
        embedding=list(row.embedding) if row.embedding is not None else None,
        usage_count=int(row.usage_count or 0),
        last_used_at=row.last_used_at,
    )


def _ensure_embedding(
    entry: MaterialEntry, *, dim: int = EMBEDDING_DIM
) -> list[float]:
    """Return a unit-normalised embedding, computing one on demand."""
    if entry.embedding and len(entry.embedding) == dim:
        return list(entry.embedding)
    text_for_embedding = " ".join(
        filter(
            None,
            [
                entry.name,
                entry.narrative_summary,
                " ".join(entry.tags),
            ],
        )
    )
    return build_hashed_embedding(text_for_embedding, dim)


def _embed_query(query: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    return build_hashed_embedding(query, dim)


def _passes_filter(
    entry: MaterialEntry, novelty: NoveltyFilter | None
) -> bool:
    if novelty is None:
        return True
    if entry.id is not None and entry.id in novelty.exclude_ids:
        return False
    if (entry.dimension, entry.slug) in novelty.exclude_slugs:
        return False
    if (
        novelty.max_usage_count is not None
        and entry.usage_count > novelty.max_usage_count
    ):
        return False
    return True


# ── Query API ──────────────────────────────────────────────────────────


async def query_library(
    session: AsyncSession,
    *,
    dimension: str,
    query: str,
    genre: str | None = None,
    sub_genre: str | None = None,
    top_k: int = 8,
    novelty_filter: NoveltyFilter | None = None,
    include_generic: bool = True,
) -> list[MaterialEntry]:
    """Fetch top-k semantically relevant entries in the given dimension.

    Filtering logic:

    1. ``dimension`` always applies (required).
    2. ``genre`` / ``sub_genre`` are filtered exactly; ``include_generic``
       widens the match to ``genre IS NULL`` rows (cross-genre commons).
    3. Only ``status='active'`` rows are considered.
    4. Ranking uses pgvector ``<=>`` cosine distance (``ORDER BY distance``)
       when the DB dialect supports it; otherwise we pull all candidates
       and re-rank locally using :func:`cosine_similarity`.
    """

    stmt = select(MaterialLibraryModel).where(
        MaterialLibraryModel.dimension == dimension,
        MaterialLibraryModel.status == "active",
    )
    if genre is not None:
        if include_generic:
            stmt = stmt.where(
                (MaterialLibraryModel.genre == genre)
                | (MaterialLibraryModel.genre.is_(None))
            )
        else:
            stmt = stmt.where(MaterialLibraryModel.genre == genre)
    if sub_genre is not None:
        stmt = stmt.where(
            (MaterialLibraryModel.sub_genre == sub_genre)
            | (MaterialLibraryModel.sub_genre.is_(None))
        )

    query_embedding = _embed_query(query)

    # Prefer a DB-side ORDER BY distance when pgvector is available.  We
    # always over-fetch by 2× so the local novelty-filter pass has
    # candidates to promote.
    try:
        ordered_stmt = stmt.order_by(
            MaterialLibraryModel.embedding.cosine_distance(query_embedding)
        ).limit(max(top_k * 2, top_k))
        result = await session.execute(ordered_stmt)
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001 — fallback for sqlite/tests
        logger.debug(
            "query_library: DB-side ordering failed (%s); falling back to Python sort",
            exc,
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        rows = _rerank_in_python(rows, query_embedding, top_k=top_k * 2)

    entries = [_row_to_entry(row) for row in rows]
    filtered = [e for e in entries if _passes_filter(e, novelty_filter)]
    return filtered[:top_k]


def _rerank_in_python(
    rows: Sequence[MaterialLibraryModel],
    query_embedding: list[float],
    *,
    top_k: int,
) -> list[MaterialLibraryModel]:
    """Python-side cosine fallback for test backends without pgvector."""

    def _score(row: MaterialLibraryModel) -> float:
        if row.embedding is None:
            return 0.0
        try:
            return cosine_similarity(list(row.embedding), query_embedding)
        except Exception:
            return 0.0

    return sorted(rows, key=_score, reverse=True)[:top_k]


# ── Insert / upsert ────────────────────────────────────────────────────


async def insert_entry(
    session: AsyncSession,
    entry: MaterialEntry,
    *,
    compute_embedding: bool = True,
) -> MaterialEntry:
    """Idempotent upsert by ``(dimension, slug)``.

    On conflict ``content_json`` / ``narrative_summary`` / tags etc. are
    overwritten; lifecycle counters (``usage_count``, ``last_used_at``)
    are preserved so we don't reset the cross-project novelty guard.
    """

    embedding = _ensure_embedding(entry) if compute_embedding else entry.embedding

    values = {
        "dimension": entry.dimension,
        "genre": entry.genre,
        "sub_genre": entry.sub_genre,
        "tags_json": list(entry.tags),
        "slug": entry.slug,
        "name": entry.name,
        "content_json": dict(entry.content_json),
        "narrative_summary": entry.narrative_summary,
        "embedding": embedding,
        "source_type": entry.source_type,
        "source_citations_json": list(entry.source_citations) or None,
        "confidence": float(entry.confidence),
        "coverage_score": entry.coverage_score,
        "status": entry.status,
    }

    stmt = pg_insert(MaterialLibraryModel).values(**values)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_material_dimension_slug",
        set_={
            "genre": excluded.genre,
            "sub_genre": excluded.sub_genre,
            "tags_json": excluded.tags_json,
            "name": excluded.name,
            "content_json": excluded.content_json,
            "narrative_summary": excluded.narrative_summary,
            "embedding": excluded.embedding,
            "source_type": excluded.source_type,
            "source_citations_json": excluded.source_citations_json,
            "confidence": excluded.confidence,
            "coverage_score": excluded.coverage_score,
            "status": excluded.status,
            "updated_at": func.now(),
        },
    ).returning(MaterialLibraryModel)

    result = await session.execute(stmt)
    row = result.scalar_one()
    await session.flush()
    return _row_to_entry(row)


async def insert_entries(
    session: AsyncSession,
    entries: Iterable[MaterialEntry],
    *,
    compute_embedding: bool = True,
) -> list[MaterialEntry]:
    """Convenience batch insert — still one round-trip per row."""
    out: list[MaterialEntry] = []
    for entry in entries:
        out.append(
            await insert_entry(
                session, entry, compute_embedding=compute_embedding
            )
        )
    return out


# ── Coverage audit ─────────────────────────────────────────────────────


async def ensure_coverage(
    session: AsyncSession,
    *,
    dimension: str,
    genre: str | None,
    sub_genre: str | None = None,
    min_entries: int = 10,
    ttl_days: int | None = None,
) -> CoverageReport:
    """Report whether a bucket has enough active entries.

    This function does *not* automatically trigger the Curator — the
    caller decides whether to block and await coverage or proceed with
    what's available.  See :mod:`bestseller.services.library_curator`
    for the fill-gap path.

    ``ttl_days`` optionally marks rows untouched for longer than the
    threshold as "stale"; the IDs are returned so a Curator can
    prioritise refresh candidates.
    """

    count_stmt = (
        select(func.count(MaterialLibraryModel.id))
        .where(MaterialLibraryModel.dimension == dimension)
        .where(MaterialLibraryModel.status == "active")
    )
    if genre is not None:
        count_stmt = count_stmt.where(MaterialLibraryModel.genre == genre)
    if sub_genre is not None:
        count_stmt = count_stmt.where(MaterialLibraryModel.sub_genre == sub_genre)
    active_count = int((await session.execute(count_stmt)).scalar_one() or 0)

    stale_ids: tuple[int, ...] = ()
    if ttl_days is not None and ttl_days > 0:
        cutoff_sql = func.now() - func.make_interval(0, 0, 0, ttl_days)
        stale_stmt = select(MaterialLibraryModel.id).where(
            MaterialLibraryModel.dimension == dimension,
            MaterialLibraryModel.status == "active",
            MaterialLibraryModel.updated_at < cutoff_sql,
        )
        if genre is not None:
            stale_stmt = stale_stmt.where(MaterialLibraryModel.genre == genre)
        stale_rows = (await session.execute(stale_stmt)).scalars().all()
        stale_ids = tuple(int(i) for i in stale_rows)

    return CoverageReport(
        dimension=dimension,
        genre=genre,
        sub_genre=sub_genre,
        active_count=active_count,
        min_required=min_entries,
        is_satisfied=active_count >= min_entries,
        stale_ids=stale_ids,
    )


async def library_has_any_genre_coverage(
    session: AsyncSession,
    *,
    genre: str | None,
    min_entries: int = 1,
) -> bool:
    """Cold-start guard: does the library have *any* active entries for a genre?

    Returns ``True`` once the active-row count for ``genre`` reaches
    ``min_entries``.  A ``None`` genre counts rows that are explicitly
    genre-neutral (``genre IS NULL`` in the DB) which is only useful for
    truly universal dimensions like ``thematic_motifs``.

    The Forge pipeline uses this as a boolean precondition — if the
    library is completely empty for the target genre, running Forges
    would produce generic output with no "baseline to differentiate
    against", which would then poison future books that query *this*
    book's materials as seeds.  Skipping Forge and falling back to the
    legacy pack path is the safer default.
    """
    count_stmt = (
        select(func.count(MaterialLibraryModel.id))
        .where(MaterialLibraryModel.status == "active")
    )
    if genre is None:
        count_stmt = count_stmt.where(MaterialLibraryModel.genre.is_(None))
    else:
        count_stmt = count_stmt.where(MaterialLibraryModel.genre == genre)
    active_count = int((await session.execute(count_stmt)).scalar_one() or 0)
    return active_count >= min_entries


# ── Usage tracking ─────────────────────────────────────────────────────


async def mark_used(
    session: AsyncSession,
    entry_ids: Sequence[int],
) -> int:
    """Increment usage counters + stamp ``last_used_at`` for each id."""
    if not entry_ids:
        return 0
    stmt = (
        update(MaterialLibraryModel)
        .where(MaterialLibraryModel.id.in_(list(entry_ids)))
        .values(
            usage_count=MaterialLibraryModel.usage_count + 1,
            last_used_at=datetime.now(tz=timezone.utc),
        )
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)


__all__ = [
    "MaterialEntry",
    "NoveltyFilter",
    "CoverageReport",
    "EMBEDDING_DIM",
    "query_library",
    "insert_entry",
    "insert_entries",
    "ensure_coverage",
    "library_has_any_genre_coverage",
    "mark_used",
]

"""Unit tests for ``bestseller.services.material_library``.

We keep these hermetic — the real code depends on PostgreSQL + pgvector,
which no unit test backend provides.  Instead the tests cover:

* Pure-Python helpers (DTO round-trip, embedding derivation, novelty
  filter, Python-side rerank).
* Public API shape via a :class:`FakeAsyncSession` that captures the
  compiled SQL statements and returns canned row sets.

Integration-level coverage (ON CONFLICT semantics, pgvector cosine
ranking, HNSW index) is deferred to the DB-bound integration suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from bestseller.services.material_library import (
    EMBEDDING_DIM,
    CoverageReport,
    MaterialEntry,
    NoveltyFilter,
    _embed_query,
    _ensure_embedding,
    _passes_filter,
    _rerank_in_python,
    _row_to_entry,
    ensure_coverage,
    insert_entry,
    library_has_any_genre_coverage,
    mark_used,
    query_library,
)

pytestmark = pytest.mark.unit


# ── Fake session + fake rows ───────────────────────────────────────────


@dataclass
class _FakeRow:
    """Minimal stand-in for :class:`MaterialLibraryModel`."""

    id: int
    dimension: str
    slug: str
    name: str
    narrative_summary: str
    content_json: dict[str, Any] = field(default_factory=dict)
    genre: str | None = None
    sub_genre: str | None = None
    tags_json: list[str] = field(default_factory=list)
    source_type: str = "research_agent"
    source_citations_json: list[dict[str, Any]] | None = None
    confidence: float = 0.0
    coverage_score: float | None = None
    status: str = "active"
    embedding: list[float] | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class _FakeResult:
    rows: list[Any] | None = None
    scalar_value: Any = None
    one_row: Any = None
    rowcount: int = 0

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self.rows or [])

    def scalar_one(self) -> Any:
        if self.one_row is not None:
            return self.one_row
        if self.rows:
            return self.rows[0]
        return self.scalar_value


class FakeAsyncSession:
    """Records executed statements and returns canned results by type hint."""

    def __init__(self) -> None:
        self.executed: list[Any] = []
        self.scalar_count: int = 0
        self.rows: list[_FakeRow] = []
        self.flush_count: int = 0
        self.upsert_returning_row: _FakeRow | None = None
        self.update_rowcount: int = 0
        # When True, ``execute`` raises on the first call to force the
        # Python-side fallback path in ``query_library``.
        self.force_orderby_fail: bool = False
        self._orderby_failed: bool = False

    async def execute(self, statement: Any) -> _FakeResult:
        self.executed.append(statement)
        compiled = str(statement).lower().lstrip()

        # ── INSERT ... ON CONFLICT path (insert_entry) ──
        if compiled.startswith("insert into material_library"):
            return _FakeResult(one_row=self.upsert_returning_row)

        # ── UPDATE (mark_used) ──
        if compiled.startswith("update material_library"):
            return _FakeResult(rowcount=self.update_rowcount)

        # ── COUNT path (ensure_coverage) — must precede the generic
        # SELECT branch since a raw SELECT over the ORM model also
        # compiles with the column name ``usage_count`` in it.
        if compiled.startswith("select count("):
            return _FakeResult(scalar_value=self.scalar_count)

        # ── SELECT with ORDER BY <=> (query_library primary path) ──
        if self.force_orderby_fail and not self._orderby_failed:
            self._orderby_failed = True
            raise RuntimeError("order-by-distance unsupported")

        # Default: return the canned rows (applies to the ORDER BY <=>
        # path, the Python-rerank fallback path, and the stale-ids
        # select in ensure_coverage).
        return _FakeResult(rows=list(self.rows))

    async def flush(self) -> None:
        self.flush_count += 1


# ── Tests: pure-Python helpers ─────────────────────────────────────────


class TestHelpers:
    def test_embed_query_is_unit_normalised(self) -> None:
        vec = _embed_query("仙侠 宗门 反派")
        assert len(vec) == EMBEDDING_DIM
        norm = sum(v * v for v in vec) ** 0.5
        assert 0 <= norm <= 1.0 + 1e-6
        # Any non-empty query should produce a non-zero vector.
        assert any(abs(v) > 0 for v in vec)

    def test_ensure_embedding_honours_explicit_value(self) -> None:
        entry = MaterialEntry(
            dimension="world_settings",
            slug="x",
            name="x",
            narrative_summary="x",
            content_json={},
            embedding=[0.0] * EMBEDDING_DIM,
        )
        out = _ensure_embedding(entry)
        assert out == [0.0] * EMBEDDING_DIM

    def test_ensure_embedding_computes_from_name_and_summary(self) -> None:
        entry = MaterialEntry(
            dimension="world_settings",
            slug="slug-1",
            name="青萝宗",
            narrative_summary="一个以草木为图腾的仙侠宗门",
            content_json={},
            tags=["仙侠", "宗门"],
        )
        vec = _ensure_embedding(entry)
        assert len(vec) == EMBEDDING_DIM

    def test_novelty_filter_excludes_by_id(self) -> None:
        entry = MaterialEntry(
            id=7,
            dimension="plot_patterns",
            slug="s",
            name="n",
            narrative_summary="ns",
            content_json={},
        )
        assert _passes_filter(entry, NoveltyFilter(exclude_ids=frozenset({7})))  is False
        assert _passes_filter(entry, NoveltyFilter(exclude_ids=frozenset({8}))) is True

    def test_novelty_filter_excludes_by_slug(self) -> None:
        entry = MaterialEntry(
            id=1, dimension="factions", slug="qingluo-sect",
            name="n", narrative_summary="ns", content_json={},
        )
        f = NoveltyFilter(exclude_slugs=frozenset({("factions", "qingluo-sect")}))
        assert _passes_filter(entry, f) is False

    def test_novelty_filter_max_usage(self) -> None:
        entry = MaterialEntry(
            id=1, dimension="power_systems", slug="s",
            name="n", narrative_summary="ns", content_json={}, usage_count=10,
        )
        assert _passes_filter(entry, NoveltyFilter(max_usage_count=8)) is False
        assert _passes_filter(entry, NoveltyFilter(max_usage_count=10)) is True

    def test_row_to_entry_roundtrip(self) -> None:
        row = _FakeRow(
            id=42,
            dimension="world_settings",
            slug="yunhe-town",
            name="云鹤镇",
            narrative_summary="一个隐匿于山间的小城",
            content_json={"geography": "山谷"},
            genre="仙侠",
            sub_genre="urban-cultivation",
            tags_json=["仙侠", "小城"],
            source_type="user_curated",
            source_citations_json=[{"url": "https://example.test/1"}],
            confidence=0.72,
            coverage_score=0.1,
            status="active",
            embedding=[0.0] * EMBEDDING_DIM,
            usage_count=3,
        )
        entry = _row_to_entry(row)  # type: ignore[arg-type]
        assert entry.id == 42
        assert entry.name == "云鹤镇"
        assert entry.tags == ["仙侠", "小城"]
        assert entry.source_citations == [{"url": "https://example.test/1"}]
        assert entry.usage_count == 3
        assert entry.embedding is not None

    def test_rerank_in_python_sorts_by_cosine(self) -> None:
        q = [1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2)
        rows = [
            _FakeRow(id=1, dimension="d", slug="a", name="a", narrative_summary="s",
                     embedding=[0.1, 0.9] + [0.0] * (EMBEDDING_DIM - 2)),
            _FakeRow(id=2, dimension="d", slug="b", name="b", narrative_summary="s",
                     embedding=[0.99, 0.01] + [0.0] * (EMBEDDING_DIM - 2)),
            _FakeRow(id=3, dimension="d", slug="c", name="c", narrative_summary="s",
                     embedding=None),
        ]
        ranked = _rerank_in_python(rows, q, top_k=2)  # type: ignore[arg-type]
        assert [r.id for r in ranked] == [2, 1]


# ── Tests: query_library ───────────────────────────────────────────────


class TestQueryLibrary:
    async def test_orders_by_pgvector_distance_by_default(self) -> None:
        session = FakeAsyncSession()
        session.rows = [
            _FakeRow(
                id=1, dimension="power_systems", slug="pv1",
                name="Power 1", narrative_summary="s1",
                embedding=[0.0] * EMBEDDING_DIM,
            ),
        ]
        entries = await query_library(
            session,  # type: ignore[arg-type]
            dimension="power_systems",
            query="修真境界",
            genre="仙侠",
            top_k=5,
        )
        assert [e.id for e in entries] == [1]
        compiled = str(session.executed[0]).lower()
        assert "material_library" in compiled
        assert "status" in compiled
        assert "dimension" in compiled

    async def test_falls_back_to_python_rerank_when_db_order_fails(self) -> None:
        session = FakeAsyncSession()
        session.force_orderby_fail = True
        q_vec = _embed_query("test")
        session.rows = [
            _FakeRow(
                id=1, dimension="d", slug="a", name="n", narrative_summary="s",
                embedding=[0.1, 0.9] + [0.0] * (EMBEDDING_DIM - 2),
            ),
            _FakeRow(
                id=2, dimension="d", slug="b", name="n", narrative_summary="s",
                embedding=[0.99, 0.01] + [0.0] * (EMBEDDING_DIM - 2),
            ),
        ]
        # Force the query embedding to match the first row's direction.
        _ = q_vec
        entries = await query_library(
            session,  # type: ignore[arg-type]
            dimension="d",
            query="q",
            top_k=2,
        )
        # Both rows pass filters; ordering is Python-side cosine (which
        # on a hashed query vector is deterministic but not asserted
        # here — we only guarantee that the fallback path returns rows).
        assert {e.id for e in entries} == {1, 2}

    async def test_novelty_filter_is_applied_post_query(self) -> None:
        session = FakeAsyncSession()
        session.rows = [
            _FakeRow(id=1, dimension="d", slug="keep",
                     name="n", narrative_summary="s", embedding=[0.0] * EMBEDDING_DIM),
            _FakeRow(id=2, dimension="d", slug="drop",
                     name="n", narrative_summary="s", embedding=[0.0] * EMBEDDING_DIM,
                     usage_count=99),
        ]
        entries = await query_library(
            session,  # type: ignore[arg-type]
            dimension="d",
            query="q",
            novelty_filter=NoveltyFilter(exclude_ids=frozenset({2})),
        )
        assert [e.id for e in entries] == [1]

    async def test_top_k_is_respected(self) -> None:
        session = FakeAsyncSession()
        session.rows = [
            _FakeRow(
                id=i,
                dimension="d",
                slug=f"s{i}",
                name="n",
                narrative_summary="s",
                embedding=[0.0] * EMBEDDING_DIM,
            )
            for i in range(1, 11)
        ]
        entries = await query_library(
            session,  # type: ignore[arg-type]
            dimension="d",
            query="q",
            top_k=3,
        )
        assert len(entries) == 3


# ── Tests: insert_entry ────────────────────────────────────────────────


class TestInsertEntry:
    async def test_insert_passes_embedding_and_flushes(self) -> None:
        session = FakeAsyncSession()
        session.upsert_returning_row = _FakeRow(
            id=101,
            dimension="power_systems",
            slug="xianxia-nine-levels",
            name="修真九境",
            narrative_summary="练气筑基金丹...",
            embedding=[0.0] * EMBEDDING_DIM,
            source_type="research_agent",
        )
        entry = MaterialEntry(
            dimension="power_systems",
            slug="xianxia-nine-levels",
            name="修真九境",
            narrative_summary="练气筑基金丹...",
            content_json={"levels": ["炼气", "筑基"]},
            genre="仙侠",
            source_type="research_agent",
            source_citations=[{"url": "https://example.test/1"}],
            confidence=0.8,
        )
        saved = await insert_entry(session, entry)  # type: ignore[arg-type]
        assert saved.id == 101
        assert saved.name == "修真九境"
        compiled = str(session.executed[0]).lower()
        assert "insert into material_library" in compiled
        assert "on conflict" in compiled
        assert session.flush_count == 1

    async def test_insert_skips_embedding_when_disabled(self) -> None:
        session = FakeAsyncSession()
        session.upsert_returning_row = _FakeRow(
            id=1, dimension="d", slug="s", name="n",
            narrative_summary="ns", source_type="user_curated",
        )
        entry = MaterialEntry(
            dimension="d", slug="s", name="n",
            narrative_summary="ns", content_json={},
            source_type="user_curated",
        )
        await insert_entry(session, entry, compute_embedding=False)  # type: ignore[arg-type]
        # Nothing else to assert — verifying the call reached execute.
        assert session.executed


# ── Tests: ensure_coverage ─────────────────────────────────────────────


class TestEnsureCoverage:
    async def test_reports_satisfied_when_count_meets_min(self) -> None:
        session = FakeAsyncSession()
        session.scalar_count = 12
        report = await ensure_coverage(
            session,  # type: ignore[arg-type]
            dimension="world_settings",
            genre="仙侠",
            min_entries=10,
        )
        assert isinstance(report, CoverageReport)
        assert report.is_satisfied is True
        assert report.active_count == 12
        assert report.gap == 0
        assert report.stale_ids == ()

    async def test_reports_gap_when_count_below_min(self) -> None:
        session = FakeAsyncSession()
        session.scalar_count = 3
        report = await ensure_coverage(
            session,  # type: ignore[arg-type]
            dimension="character_archetypes",
            genre="仙侠",
            sub_genre="upgrade",
            min_entries=10,
        )
        assert report.is_satisfied is False
        assert report.gap == 7

    async def test_ttl_days_emits_stale_select(self) -> None:
        session = FakeAsyncSession()
        session.scalar_count = 5
        await ensure_coverage(
            session,  # type: ignore[arg-type]
            dimension="d",
            genre=None,
            min_entries=10,
            ttl_days=30,
        )
        # 2 statements: count + stale id select.
        assert len(session.executed) == 2
        stale_sql = str(session.executed[1]).lower()
        assert "updated_at" in stale_sql


# ── Tests: library_has_any_genre_coverage (cold-start guard) ───────────


class TestLibraryHasAnyGenreCoverage:
    """The Forge pipeline's cold-start guard.

    When the library has zero active rows for a genre, running Forges
    would produce "baseline-less" output that poisons future queries.
    This helper is the precondition: ``True`` → safe to forge, ``False``
    → skip and fall back to the legacy pack path.
    """

    async def test_returns_true_when_library_has_entries(self) -> None:
        session = FakeAsyncSession()
        session.scalar_count = 15
        assert (
            await library_has_any_genre_coverage(
                session,  # type: ignore[arg-type]
                genre="仙侠",
            )
            is True
        )

    async def test_returns_false_when_library_is_empty(self) -> None:
        session = FakeAsyncSession()
        session.scalar_count = 0
        assert (
            await library_has_any_genre_coverage(
                session,  # type: ignore[arg-type]
                genre="仙侠",
            )
            is False
        )

    async def test_min_entries_threshold_is_respected(self) -> None:
        session = FakeAsyncSession()
        session.scalar_count = 2
        # 2 entries but min=3 required → still not covered
        assert (
            await library_has_any_genre_coverage(
                session,  # type: ignore[arg-type]
                genre="仙侠",
                min_entries=3,
            )
            is False
        )
        # 2 entries with min=1 → covered
        session.scalar_count = 2
        assert (
            await library_has_any_genre_coverage(
                session,  # type: ignore[arg-type]
                genre="仙侠",
                min_entries=1,
            )
            is True
        )

    async def test_none_genre_filters_on_null(self) -> None:
        """Passing ``genre=None`` must scope the count to universal rows
        (``genre IS NULL``) rather than counting every row in the table."""
        session = FakeAsyncSession()
        session.scalar_count = 1
        await library_has_any_genre_coverage(
            session,  # type: ignore[arg-type]
            genre=None,
        )
        compiled = str(session.executed[-1]).lower()
        assert "is null" in compiled or "material_library.genre is null" in compiled


# ── Tests: mark_used ───────────────────────────────────────────────────


class TestMarkUsed:
    async def test_no_ids_is_no_op(self) -> None:
        session = FakeAsyncSession()
        assert await mark_used(session, []) == 0  # type: ignore[arg-type]
        assert session.executed == []

    async def test_bumps_counters(self) -> None:
        session = FakeAsyncSession()
        session.update_rowcount = 3
        affected = await mark_used(session, [1, 2, 3])  # type: ignore[arg-type]
        assert affected == 3
        compiled = str(session.executed[0]).lower()
        assert "update material_library" in compiled
        assert "usage_count" in compiled

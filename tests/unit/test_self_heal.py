"""Unit tests for worker self-heal detector.

Focus: pure functions and the *logic* of ``reap_orphan_workflow_runs`` /
``find_stuck_projects``. Real DB integration is exercised manually via the
worker container; here we stub session objects so the tests stay fast and
offline-friendly.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from bestseller.worker.self_heal import (
    STARTUP_GRACE_SECONDS,
    StuckProject,
    find_stuck_projects,
    reap_orphan_workflow_runs,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal in-memory stand-in for the async SQLAlchemy session
# ---------------------------------------------------------------------------


@dataclass
class _FakeProject:
    id: Any
    slug: str
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeWorkflowRun:
    id: Any
    project_id: Any
    workflow_type: str
    status: str
    updated_at: _dt.datetime
    error_message: str | None = None


@dataclass
class _FakeChapter:
    id: Any
    project_id: Any


@dataclass
class _FakeDraft:
    id: Any
    chapter_id: Any
    is_current: bool
    content_md: str = "body"


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self):  # noqa: ANN204 — async session uses list() on scalars result
        return iter(self._rows)


class _FakeSession:
    """Stand-in that understands just enough of the SQLAlchemy async API.

    We inspect the SQL object's kind (select / update) and the targeted
    model class to decide which in-memory list to read / write. This keeps
    the tests decoupled from the real ORM while still exercising the
    production code paths unchanged.
    """

    def __init__(
        self,
        projects: list[_FakeProject],
        runs: list[_FakeWorkflowRun],
        chapters: list[_FakeChapter],
        drafts: list[_FakeDraft],
    ) -> None:
        self.projects = projects
        self.runs = runs
        self.chapters = chapters
        self.drafts = drafts
        self.committed = False

    # --- scalars ---------------------------------------------------------
    async def scalars(self, stmt: Any) -> _FakeResult:
        from bestseller.infra.db.models import ProjectModel  # noqa: PLC0415

        target = self._target_model(stmt)
        if target is ProjectModel:
            return _FakeResult(list(self.projects))
        raise NotImplementedError(f"scalars for {target}")

    async def scalar(self, stmt: Any) -> Any:
        from bestseller.infra.db.models import (  # noqa: PLC0415
            ChapterDraftVersionModel,
            ChapterModel,
            WorkflowRunModel,
        )

        target = self._target_model(stmt)
        project_id = self._filter_project_id(stmt)

        if target is WorkflowRunModel:
            active = {"pending", "queued", "running"}
            pipeline_types = {"autowrite_pipeline", "project_pipeline"}
            for r in self.runs:
                if r.project_id != project_id:
                    continue
                if r.workflow_type not in pipeline_types:
                    continue
                if r.status not in active:
                    continue
                return r.id
            return None

        if target is ChapterModel:
            return sum(1 for c in self.chapters if c.project_id == project_id)

        if target is ChapterDraftVersionModel:
            chapter_ids = {c.id for c in self.chapters if c.project_id == project_id}
            return sum(
                1
                for d in self.drafts
                if d.chapter_id in chapter_ids and d.is_current
            )

        raise NotImplementedError(f"scalar for {target}")

    # --- execute (used by update()) --------------------------------------
    async def execute(self, stmt: Any) -> Any:
        from bestseller.domain.enums import WorkflowStatus  # noqa: PLC0415

        # Only update(WorkflowRunModel) is exercised here.
        cutoff = self._filter_updated_before(stmt)
        statuses = {"pending", "queued", "running"}
        count = 0
        for r in self.runs:
            if r.status in statuses and r.updated_at < cutoff:
                r.status = WorkflowStatus.FAILED.value
                r.error_message = "reaped by self-heal (abandoned by prior worker)"
                count += 1

        class _ExecResult:
            def __init__(self, n: int) -> None:
                self.rowcount = n

        return _ExecResult(count)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _target_model(stmt: Any) -> type:
        from bestseller.infra.db.models import (  # noqa: PLC0415
            ChapterDraftVersionModel,
            ChapterModel,
            ProjectModel,
            WorkflowRunModel,
        )

        # select(X) → column_descriptions[0]["entity"]; update(X) → entity_description
        descs = getattr(stmt, "column_descriptions", None)
        if descs:
            entity = descs[0].get("entity")
            if entity is not None:
                return entity
        ent = getattr(stmt, "entity_description", None)
        if ent is not None:
            found = ent.get("entity")
            if found is not None:
                return found
        # Fall back: search compiled SQL for one of the known table names.
        sql_text = str(stmt)
        for model in (
            ChapterDraftVersionModel,
            WorkflowRunModel,
            ChapterModel,
            ProjectModel,
        ):
            table_name = getattr(model, "__tablename__", None)
            if table_name and table_name in sql_text:
                return model
        raise RuntimeError(f"cannot determine target model for stmt: {stmt!r}")

    @staticmethod
    def _filter_project_id(stmt: Any) -> Any:
        # Walk the WHERE clause children and find a literal bound to project_id
        from sqlalchemy.sql import operators  # noqa: PLC0415

        def _walk(node: Any) -> Any:
            try:
                clauses = list(getattr(node, "clauses", []) or [])
            except Exception:  # noqa: BLE001
                clauses = []
            for c in clauses:
                found = _walk(c)
                if found is not None:
                    return found
            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            if left is not None and right is not None:
                key = getattr(left, "key", None) or getattr(left, "name", None)
                if key == "project_id":
                    return getattr(right, "value", None) or getattr(right, "effective_value", None)
            return None

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            whereclause = getattr(stmt, "_whereclause", None)
        return _walk(whereclause)

    @staticmethod
    def _filter_updated_before(stmt: Any) -> _dt.datetime:
        def _walk(node: Any) -> Any:
            try:
                clauses = list(getattr(node, "clauses", []) or [])
            except Exception:  # noqa: BLE001
                clauses = []
            for c in clauses:
                found = _walk(c)
                if found is not None:
                    return found
            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            if left is not None and right is not None:
                key = getattr(left, "key", None) or getattr(left, "name", None)
                if key == "updated_at":
                    return getattr(right, "value", None)
            return None

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            whereclause = getattr(stmt, "_whereclause", None)
        return _walk(whereclause) or _dt.datetime.now(_dt.UTC)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


@pytest.mark.asyncio
async def test_find_stuck_projects_detects_missing_drafts(now: _dt.datetime) -> None:
    """A project with 10 chapter rows but only 7 current drafts is stuck."""
    p = _FakeProject(id=uuid4(), slug="book-1")
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(10)]
    drafts = [
        _FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True)
        for c in chapters[:7]
    ]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-1"
    assert stuck[0].reason == "missing_drafts"
    assert stuck[0].stuck_at_chapter == 8
    assert stuck[0].chapters_total == 10
    assert stuck[0].chapters_with_draft == 7


@pytest.mark.asyncio
async def test_find_stuck_projects_detects_explicit_stuck_marker(
    now: _dt.datetime,
) -> None:
    """A project with ``stuck_at_chapter`` in metadata is stuck regardless of draft count."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-2",
        metadata_json={"stuck_at_chapter": 42, "last_error": "boom"},
    )
    session = _FakeSession(projects=[p], runs=[], chapters=[], drafts=[])

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].reason == "explicit_stuck_marker"
    assert stuck[0].stuck_at_chapter == 42


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_projects_with_active_pipeline(
    now: _dt.datetime,
) -> None:
    """Projects with an active pipeline must not be touched."""
    p = _FakeProject(id=uuid4(), slug="book-3")
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(5)]
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="autowrite_pipeline",
            status="running",
            updated_at=now,
        )
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=chapters, drafts=[])

    stuck = await find_stuck_projects(session)

    assert stuck == []


@pytest.mark.asyncio
async def test_find_stuck_projects_ignores_complete_projects(now: _dt.datetime) -> None:
    """Every chapter has a current draft — nothing to heal."""
    p = _FakeProject(id=uuid4(), slug="book-4")
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(3)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_by_startup_cutoff(
    now: _dt.datetime,
) -> None:
    """Worker startup must reap every active row written before boot.

    Without this, workflow rows left over from the previous (dead) container
    block the new worker from restarting stuck projects.
    """
    p = _FakeProject(id=uuid4(), slug="book-5")
    old = now - _dt.timedelta(minutes=45)
    fresh = now - _dt.timedelta(seconds=5)
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="autowrite_pipeline",
            status="running",
            updated_at=old,
        ),
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="autowrite_pipeline",
            status="running",
            updated_at=fresh,
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    startup_cutoff = now - _dt.timedelta(seconds=STARTUP_GRACE_SECONDS)
    reaped = await reap_orphan_workflow_runs(session, startup_cutoff=startup_cutoff)

    # Only the pre-boot row is reaped; the freshly-written one is assumed to
    # belong to the current worker.
    assert reaped == 1
    assert runs[0].status == "failed"
    assert runs[1].status == "running"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_by_heartbeat_timeout(
    now: _dt.datetime,
) -> None:
    """When no startup_cutoff is provided, falls back to heartbeat timeout."""
    p = _FakeProject(id=uuid4(), slug="book-6")
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="autowrite_pipeline",
            status="running",
            updated_at=now - _dt.timedelta(hours=5),
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session)

    assert reaped == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_stuck_project_is_frozen_dataclass() -> None:
    sp = StuckProject(
        project_id="p1",
        slug="x",
        reason="missing_drafts",
        stuck_at_chapter=5,
        chapters_total=10,
        chapters_with_draft=4,
    )
    with pytest.raises(Exception):
        sp.slug = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Boot-lock + enqueue dedup
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Mimics the SET NX EX subset of redis.asyncio we actually use."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[dict[str, Any]] = []
        self.fail_on_set: bool = False

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if self.fail_on_set:
            raise RuntimeError("redis unavailable")
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_try_acquire_heal_lock_first_caller_wins() -> None:
    from bestseller.worker.self_heal import (
        SELF_HEAL_LOCK_KEY,
        _try_acquire_heal_lock,
    )

    redis = _FakeRedis()

    assert await _try_acquire_heal_lock(redis, "worker-a") is True
    assert await _try_acquire_heal_lock(redis, "worker-b") is False
    # First writer's identity was persisted
    assert redis.store[SELF_HEAL_LOCK_KEY] == "worker-a"
    # Both attempts used NX + EX semantics
    for call in redis.set_calls:
        assert call["nx"] is True
        assert call["ex"] is not None and call["ex"] > 0


@pytest.mark.asyncio
async def test_try_acquire_heal_lock_none_redis_returns_true() -> None:
    """CLI/test paths pass redis=None and must always proceed."""
    from bestseller.worker.self_heal import _try_acquire_heal_lock

    assert await _try_acquire_heal_lock(None, "worker-x") is True


@pytest.mark.asyncio
async def test_try_acquire_heal_lock_falls_back_on_redis_error() -> None:
    """Transient Redis failure must not silently skip self-heal."""
    from bestseller.worker.self_heal import _try_acquire_heal_lock

    redis = _FakeRedis()
    redis.fail_on_set = True

    assert await _try_acquire_heal_lock(redis, "worker-y") is True


def test_autowrite_heal_job_id_is_deterministic() -> None:
    from bestseller.worker.self_heal import _autowrite_heal_job_id

    assert _autowrite_heal_job_id("slug-a") == "autowrite:heal:slug-a"
    # Identical across calls → ARQ dedup will reject a second enqueue.
    assert _autowrite_heal_job_id("slug-a") == _autowrite_heal_job_id("slug-a")
    # Different slugs → different ids
    assert _autowrite_heal_job_id("slug-a") != _autowrite_heal_job_id("slug-b")


class _FakeArqPool:
    def __init__(self, reject_job_ids: set[str] | None = None) -> None:
        self.reject_job_ids = reject_job_ids or set()
        self.enqueued: list[dict[str, Any]] = []

    async def enqueue_job(
        self,
        function: str,
        *,
        workflow_run_id: str,
        payload: dict[str, Any],
        _job_id: str,
    ) -> Any:
        self.enqueued.append(
            {
                "function": function,
                "workflow_run_id": workflow_run_id,
                "payload": payload,
                "_job_id": _job_id,
            }
        )
        if _job_id in self.reject_job_ids:
            return None
        return object()  # non-None sentinel — ARQ returns a Job instance


@pytest.mark.asyncio
async def test_requeue_autowrite_returns_job_id_on_success() -> None:
    from bestseller.worker.self_heal import _requeue_autowrite

    pool = _FakeArqPool()
    stuck = StuckProject(
        project_id="p1",
        slug="book-z",
        reason="missing_drafts",
        stuck_at_chapter=3,
        chapters_total=10,
        chapters_with_draft=2,
    )

    job_id = await _requeue_autowrite(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "autowrite:heal:book-z"
    assert len(pool.enqueued) == 1
    assert pool.enqueued[0]["_job_id"] == "autowrite:heal:book-z"
    assert pool.enqueued[0]["payload"] == {"project_slug": "book-z", "premise": None}


@pytest.mark.asyncio
async def test_requeue_autowrite_returns_none_when_arq_dedups() -> None:
    """ARQ returning None means a same-id job is already pending/running."""
    from bestseller.worker.self_heal import _requeue_autowrite

    pool = _FakeArqPool(reject_job_ids={"autowrite:heal:book-dup"})
    stuck = StuckProject(
        project_id="p2",
        slug="book-dup",
        reason="missing_drafts",
        stuck_at_chapter=1,
        chapters_total=5,
        chapters_with_draft=0,
    )

    job_id = await _requeue_autowrite(pool, stuck)  # type: ignore[arg-type]

    assert job_id is None

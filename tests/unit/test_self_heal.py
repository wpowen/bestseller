"""Unit tests for worker self-heal detector.

Focus: pure functions and the *logic* of ``reap_orphan_workflow_runs`` /
``find_stuck_projects``. Real DB integration is exercised manually via the
worker container; here we stub session objects so the tests stay fast and
offline-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
import pickle
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from bestseller.worker.self_heal import (
    GENERATION_GATE_RESUME_COOLDOWN_SECONDS,
    SELF_HEAL_PENDING_REWRITE_TASK_LIMIT,
    STARTUP_GRACE_SECONDS,
    UNDER_TARGET_SELF_HEAL_GRACE_SECONDS,
    WAITING_REPAIR_SUPPRESSION_SECONDS,
    StuckProject,
    _active_arq_project_slugs,
    _clear_auto_resumable_generation_gate_pause,
    _project_resume_is_blocked,
    find_stuck_projects,
    heal_stuck_projects,
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
    target_chapters: int = 0
    status: str = "writing"
    created_at: _dt.datetime | None = None
    updated_at: _dt.datetime | None = None


@dataclass
class _FakeWorkflowRun:
    id: Any
    project_id: Any
    workflow_type: str
    status: str
    updated_at: _dt.datetime
    created_at: _dt.datetime | None = None
    current_step: str | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeChapter:
    id: Any
    project_id: Any
    production_state: str = "ok"
    updated_at: _dt.datetime = field(default_factory=lambda: _dt.datetime.now(_dt.UTC))
    chapter_number: int = 0
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeDraft:
    id: Any
    chapter_id: Any
    is_current: bool
    content_md: str = "body"


@dataclass
class _FakeRewriteTask:
    id: Any
    project_id: Any
    status: str = "pending"
    # Default trigger maps to "structural" (unknown gate) so existing
    # pending-rewrite tests keep their repair-first behavior unless a test
    # explicitly sets a local-gate trigger.
    trigger_type: str = "review_score"


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
        rewrite_tasks: list[_FakeRewriteTask] | None = None,
    ) -> None:
        self.projects = projects
        self.runs = runs
        self.chapters = chapters
        self.drafts = drafts
        self.rewrite_tasks = rewrite_tasks or []
        self.committed = False

    # --- scalars ---------------------------------------------------------
    async def scalars(self, stmt: Any) -> _FakeResult:
        from bestseller.infra.db.models import (  # noqa: PLC0415
            ChapterModel,
            ProjectModel,
            RewriteTaskModel,
        )

        target = self._target_model(stmt)
        if target is ProjectModel:
            return _FakeResult(list(self.projects))
        if target is ChapterModel:
            project_id = self._filter_project_id(stmt)
            production_state = self._filter_production_state(stmt)
            return _FakeResult(
                [
                    c
                    for c in self.chapters
                    if c.project_id == project_id
                    and (
                        production_state is None
                        or c.production_state == production_state
                    )
                ]
            )
        if target is RewriteTaskModel:
            project_id = self._filter_project_id(stmt)
            return _FakeResult(
                [
                    task
                    for task in self.rewrite_tasks
                    if task.project_id == project_id
                    and task.status in {"pending", "queued"}
                ]
            )
        raise NotImplementedError(f"scalars for {target}")

    async def scalar(self, stmt: Any) -> Any:
        from bestseller.infra.db.models import (  # noqa: PLC0415
            ChapterDraftVersionModel,
            ChapterModel,
            WorkflowRunModel,
            RewriteTaskModel,
        )

        target = self._target_model(stmt)
        project_id = self._filter_project_id(stmt)

        if target is WorkflowRunModel:
            sql_text = str(stmt).lower()
            workflow_types = self._filter_workflow_types(stmt)
            if "max(" in sql_text:
                if "current_step" in sql_text:
                    machine_steps = {
                        "scene_machine_repair_required",
                        "scene_rewrite_stalled_blocked",
                    }
                    matching = [
                        r.updated_at
                        for r in self.runs
                        if r.project_id == project_id
                        and r.status == "machine_blocked"
                        and r.current_step in machine_steps
                    ]
                    return max(matching, default=None)
                matching = [
                    r.updated_at
                    for r in self.runs
                    if r.project_id == project_id
                    and r.workflow_type == "project_repair"
                    and r.status == "machine_blocked"
                ]
                return max(matching, default=None)

            active = {"pending", "queued", "running"}
            pipeline_types = workflow_types or {
                "autowrite_pipeline",
                "generate_foundation_plan",
                "generate_novel_plan",
                "generate_volume_plan",
                "project_pipeline",
                "chapter_pipeline",
                "scene_pipeline",
                "project_repair",
                "materialize_story_bible",
                "materialize_chapter_outline_batch",
                "materialize_narrative_graph",
                "materialize_narrative_tree",
            }
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
            sql_text = str(stmt).lower()
            production_state = self._filter_production_state(stmt)
            matching = [
                c
                for c in self.chapters
                if c.project_id == project_id
                and (
                    production_state is None
                    or c.production_state == production_state
                )
            ]
            if "max(" in sql_text:
                return max((c.updated_at for c in matching), default=None)

            if "count(" not in sql_text:
                return max(matching, key=lambda c: c.updated_at, default=None)
            return sum(
                1 for c in matching
            )

        if target is ChapterDraftVersionModel:
            chapter_ids = {c.id for c in self.chapters if c.project_id == project_id}
            return sum(
                1
                for d in self.drafts
                if d.chapter_id in chapter_ids and d.is_current
            )

        if target is RewriteTaskModel:
            return sum(
                1
                for task in self.rewrite_tasks
                if task.project_id == project_id and task.status in {"pending", "queued"}
            )

        raise NotImplementedError(f"scalar for {target}")

    # --- execute (used by update()) --------------------------------------
    async def execute(self, stmt: Any, params: Any | None = None) -> Any:
        from bestseller.domain.enums import WorkflowStatus  # noqa: PLC0415

        # Only update(WorkflowRunModel) is exercised here.
        sql_text = str(stmt)
        if "parent_workflow_run_id" in sql_text:
            active = {"pending", "queued", "running"}
            parents = {str(r.id): r for r in self.runs}
            count = 0
            for r in self.runs:
                if r.status not in active:
                    continue
                parent_id = (r.metadata_json or {}).get("parent_workflow_run_id")
                parent = parents.get(str(parent_id))
                if parent is None or parent.status in active:
                    continue
                r.status = WorkflowStatus.FAILED.value
                r.error_message = "reaped by self-heal (abandoned by prior worker)"
                count += 1

            class _ExecResult:
                def __init__(self, n: int) -> None:
                    self.rowcount = n

            return _ExecResult(count)

        # The real reaper uses two ``updated_at <`` cutoffs OR'd together: the
        # long heartbeat timeout (3h) for all reapable types, plus a short
        # window (30min) that also applies to heartbeating types (writing
        # pipelines + project_repair). Model both so the dual-window behavior is
        # faithfully exercised. min() = oldest timestamp = long heartbeat cutoff;
        # max() = most recent = short window cutoff.
        cutoffs = self._filter_all_updated_before(stmt)
        heartbeat_cutoff = min(cutoffs)
        short_window_cutoff = max(cutoffs)
        created_cutoff = self._filter_created_before(stmt)
        protected_project_ids = self._filter_project_id_not_in(stmt)
        statuses = {"pending", "queued", "running"}
        reapable_types = {
            "autowrite_pipeline",
            "generate_foundation_plan",
            "generate_novel_plan",
            "generate_volume_plan",
            "project_pipeline",
            "chapter_pipeline",
            "scene_pipeline",
            "project_repair",
        }
        short_window_types = {
            "project_pipeline",
            "chapter_pipeline",
            "scene_pipeline",
            "project_repair",
        }
        count = 0
        for r in self.runs:
            created_at = r.created_at or r.updated_at
            stale_by_heartbeat = r.updated_at < heartbeat_cutoff
            stale_by_short_window = (
                r.workflow_type in short_window_types
                and r.updated_at < short_window_cutoff
            )
            stale_by_startup = created_cutoff is not None and created_at < created_cutoff
            if (
                r.workflow_type in reapable_types
                and r.status in statuses
                and r.project_id not in protected_project_ids
                and (stale_by_heartbeat or stale_by_short_window or stale_by_startup)
            ):
                r.status = WorkflowStatus.FAILED.value
                r.error_message = "reaped by self-heal (abandoned by prior worker)"
                count += 1

        class _ExecResult:
            def __init__(self, n: int) -> None:
                self.rowcount = n

        return _ExecResult(count)

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def get(self, model: type, pk: Any) -> Any:
        from bestseller.infra.db.models import ProjectModel  # noqa: PLC0415

        if model is ProjectModel:
            return next((p for p in self.projects if p.id == pk), None)
        raise NotImplementedError(f"get for {model}")

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _target_model(stmt: Any) -> type:
        from bestseller.infra.db.models import (  # noqa: PLC0415
            ChapterDraftVersionModel,
            ChapterModel,
            ProjectModel,
            RewriteTaskModel,
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
            RewriteTaskModel,
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
    def _filter_workflow_types(stmt: Any) -> set[str]:
        try:
            params = stmt.compile().params
        except Exception:  # noqa: BLE001
            return set()
        values: set[str] = set()
        for key, value in params.items():
            if not str(key).startswith("workflow_type"):
                continue
            if isinstance(value, str):
                values.add(value)
            else:
                try:
                    values.update(str(item) for item in value)
                except TypeError:
                    pass
        return values

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

    @staticmethod
    def _filter_all_updated_before(stmt: Any) -> list[_dt.datetime]:
        """Collect every ``updated_at < <ts>`` cutoff in the statement.

        The reaper OR's multiple ``updated_at`` cutoffs (long heartbeat + short
        window); a single-value extractor would only ever see the first.
        """
        found: list[_dt.datetime] = []

        def _walk(node: Any) -> None:
            try:
                clauses = list(getattr(node, "clauses", []) or [])
            except Exception:  # noqa: BLE001
                clauses = []
            for c in clauses:
                _walk(c)
            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            if left is not None and right is not None:
                key = getattr(left, "key", None) or getattr(left, "name", None)
                if key == "updated_at":
                    value = getattr(right, "value", None)
                    if value is not None:
                        found.append(value)

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            whereclause = getattr(stmt, "_whereclause", None)
        _walk(whereclause)
        return found or [_dt.datetime.now(_dt.UTC)]

    @staticmethod
    def _filter_created_before(stmt: Any) -> _dt.datetime | None:
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
                if key == "created_at":
                    return getattr(right, "value", None)
            return None

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            whereclause = getattr(stmt, "_whereclause", None)
        return _walk(whereclause)

    @staticmethod
    def _filter_project_id_not_in(stmt: Any) -> set[Any]:
        def _walk(node: Any) -> set[Any]:
            found: set[Any] = set()
            try:
                clauses = list(getattr(node, "clauses", []) or [])
            except Exception:  # noqa: BLE001
                clauses = []
            for c in clauses:
                found.update(_walk(c))
            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            operator = getattr(node, "operator", None)
            if left is not None and right is not None:
                key = getattr(left, "key", None) or getattr(left, "name", None)
                operator_name = getattr(operator, "__name__", "")
                if key == "project_id" and "not_in" in operator_name:
                    values = getattr(right, "value", None) or ()
                    found.update(values)
            return found

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            whereclause = getattr(stmt, "_whereclause", None)
        return _walk(whereclause)

    @staticmethod
    def _filter_production_state(stmt: Any) -> str | None:
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
                if key == "production_state":
                    return getattr(right, "value", None) or getattr(right, "effective_value", None)
            return None

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            whereclause = getattr(stmt, "_whereclause", None)
        return _walk(whereclause)


class _FakeInProgressRedis:
    def __init__(
        self,
        jobs: dict[str, dict[str, Any]],
        *,
        queue_scores: dict[str, float] | None = None,
    ) -> None:
        self.jobs = jobs
        self.queue_scores = queue_scores or {}

    async def scan_iter(self, match: str) -> Any:  # noqa: ARG002
        for job_id in self.jobs:
            if match == "arq:job:*":
                yield f"arq:job:{job_id}".encode()
            elif match == "arq:in-progress:*":
                yield f"arq:in-progress:{job_id}".encode()
            elif match == "arq:retry:*":
                yield f"arq:retry:{job_id}".encode()

    async def get(self, key: str) -> bytes | None:
        job_id = key.removeprefix("arq:job:")
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return pickle.dumps(job)

    async def zscore(self, key: str, member: str) -> float | None:
        return self.queue_scores.get(f"{key}:{member}")


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
    assert stuck[0].heal_kind == "project_pipeline"


@pytest.mark.asyncio
async def test_find_stuck_projects_detects_explicit_stuck_marker(
    now: _dt.datetime,
) -> None:
    """A project with ``stuck_at_chapter`` and no persisted draft is stuck."""
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
async def test_find_stuck_projects_detects_paused_explicit_stuck_marker(
    now: _dt.datetime,
) -> None:
    """Paused projects with stuck_at_chapter are system-resumable, not user-paused."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-paused-stuck",
        status="paused",
        metadata_json={"stuck_at_chapter": 42, "last_error": "writer crashed"},
    )
    session = _FakeSession(projects=[p], runs=[], chapters=[], drafts=[])

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].reason == "explicit_stuck_marker"
    assert stuck[0].stuck_at_chapter == 42


@pytest.mark.asyncio
async def test_find_stuck_projects_ignores_stale_explicit_marker_when_draft_exists(
    now: _dt.datetime,
) -> None:
    """A stale marker must not requeue work that already has current drafts."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-stale",
        metadata_json={"stuck_at_chapter": 3, "last_error": "old"},
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(3)]
    drafts = [
        _FakeDraft(id=uuid4(), chapter_id=chapter.id, is_current=True)
        for chapter in chapters
    ]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert stuck == []


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
async def test_find_stuck_projects_ignores_active_volume_planning(
    now: _dt.datetime,
) -> None:
    """Volume planning is already an active autowrite child step."""
    p = _FakeProject(id=uuid4(), slug="book-volume-active", target_chapters=100)
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(50)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="generate_volume_plan",
            status="running",
            updated_at=now,
        )
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_ignores_complete_projects(now: _dt.datetime) -> None:
    """Every chapter has a current draft — nothing to heal."""
    p = _FakeProject(id=uuid4(), slug="book-4")
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(3)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_detects_blocked_chapters(
    now: _dt.datetime,
) -> None:
    """Blocked chapters with current drafts must enter repair, not autowrite."""
    p = _FakeProject(id=uuid4(), slug="book-blocked")
    chapters = [
        _FakeChapter(id=uuid4(), project_id=p.id, production_state="ok"),
        _FakeChapter(id=uuid4(), project_id=p.id, production_state="blocked"),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-blocked"
    assert stuck[0].reason == "blocked_chapters"
    assert stuck[0].heal_kind == "repair"
    assert stuck[0].chapters_total == 2
    assert stuck[0].chapters_with_draft == 2


@pytest.mark.asyncio
async def test_find_stuck_projects_ignores_terminal_quality_debt(
    now: _dt.datetime,
) -> None:
    """Accepted best attempts are closure state, not endlessly repairable work."""
    p = _FakeProject(id=uuid4(), slug="book-terminal-debt")
    chapter = _FakeChapter(
        id=uuid4(),
        project_id=p.id,
        production_state="blocked",
        chapter_number=1,
        metadata_json={
            "chapter_quality_debt": True,
            "blocked_by_material_referential_integrity_gate": True,
        },
    )
    session = _FakeSession(
        projects=[p],
        runs=[],
        chapters=[chapter],
        drafts=[_FakeDraft(id=uuid4(), chapter_id=chapter.id, is_current=True)],
    )

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_local_block_does_not_starve_continuation(
    now: _dt.datetime,
) -> None:
    """A local-quality block must let new-chapter writing proceed.

    Regression: 青囊不语问阴阳 looped ch1's opening-tension gate forever while
    later chapters were never written. ch1 is blocked locally; ch2 is planned
    but undrafted — self-heal must dispatch continuation, not repair-first.
    """
    p = _FakeProject(id=uuid4(), slug="book-local-block")
    blocked = _FakeChapter(
        id=uuid4(),
        project_id=p.id,
        production_state="blocked",
        chapter_number=1,
        metadata_json={"qimao_opening_gate_blocked": True},
    )
    planned = _FakeChapter(
        id=uuid4(), project_id=p.id, production_state="pending", chapter_number=2
    )
    chapters = [blocked, planned]
    # Only the blocked chapter has a current draft → ch2 is missing its draft.
    drafts = [_FakeDraft(id=uuid4(), chapter_id=blocked.id, is_current=True)]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-local-block"
    assert stuck[0].heal_kind == "project_pipeline"
    assert stuck[0].reason == "missing_drafts"


@pytest.mark.asyncio
async def test_active_repair_does_not_starve_local_block_continuation(
    now: _dt.datetime,
) -> None:
    """An active repair workflow must not suppress safe forward writing."""
    p = _FakeProject(id=uuid4(), slug="book-local-block-active-repair")
    blocked = _FakeChapter(
        id=uuid4(),
        project_id=p.id,
        production_state="blocked",
        chapter_number=86,
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "CHAPTER_LENGTH_BLOCK_HIGH",
        },
    )
    planned = _FakeChapter(
        id=uuid4(), project_id=p.id, production_state="pending", chapter_number=87
    )
    drafts = [_FakeDraft(id=uuid4(), chapter_id=blocked.id, is_current=True)]
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="running",
            updated_at=now,
        )
    ]
    session = _FakeSession(
        projects=[p],
        runs=runs,
        chapters=[blocked, planned],
        drafts=drafts,
    )

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-local-block-active-repair"
    assert stuck[0].heal_kind == "project_pipeline"
    assert stuck[0].reason == "missing_drafts"


@pytest.mark.asyncio
async def test_local_block_drains_repair_once_caught_up(
    now: _dt.datetime,
) -> None:
    """When all planned chapters are drafted, local blocks drain via repair."""
    p = _FakeProject(id=uuid4(), slug="book-local-drain", target_chapters=2)
    chapters = [
        _FakeChapter(
            id=uuid4(),
            project_id=p.id,
            production_state="blocked",
            chapter_number=1,
            metadata_json={"qimao_opening_gate_blocked": True},
        ),
        _FakeChapter(
            id=uuid4(), project_id=p.id, production_state="ok", chapter_number=2
        ),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-local-drain"
    assert stuck[0].reason == "local_quality_repair_drain"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_structural_block_still_repairs_first_over_continuation(
    now: _dt.datetime,
) -> None:
    """A structural block keeps repair-first even when chapters are undrafted."""
    p = _FakeProject(id=uuid4(), slug="book-structural-block")
    blocked = _FakeChapter(
        id=uuid4(),
        project_id=p.id,
        production_state="blocked",
        chapter_number=1,
        metadata_json={"blocked_by_material_referential_integrity_gate": True},
    )
    planned = _FakeChapter(
        id=uuid4(), project_id=p.id, production_state="pending", chapter_number=2
    )
    chapters = [blocked, planned]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=blocked.id, is_current=True)]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].reason == "blocked_chapters"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_local_pending_rewrite_tasks_do_not_block_continuation(
    now: _dt.datetime,
) -> None:
    """Pending rewrite tasks from a local gate must not stall writing."""
    p = _FakeProject(id=uuid4(), slug="book-local-rewrite")
    chapters = [
        _FakeChapter(
            id=uuid4(), project_id=p.id, production_state="ok", chapter_number=1
        ),
        _FakeChapter(
            id=uuid4(), project_id=p.id, production_state="pending", chapter_number=2
        ),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    rewrite_tasks = [
        _FakeRewriteTask(
            id=uuid4(),
            project_id=p.id,
            status="pending",
            trigger_type="qimao_opening_gate",
        )
    ]
    session = _FakeSession(
        projects=[p],
        runs=[],
        chapters=chapters,
        drafts=drafts,
        rewrite_tasks=rewrite_tasks,
    )

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].heal_kind == "project_pipeline"
    assert stuck[0].reason == "missing_drafts"


@pytest.mark.asyncio
async def test_scene_review_pending_rewrite_task_does_not_block_continuation(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(id=uuid4(), slug="book-scene-review-rewrite")
    chapters = [
        _FakeChapter(
            id=uuid4(),
            project_id=p.id,
            production_state="blocked",
            chapter_number=87,
            metadata_json={
                "blocked_by_write_safety_gate": True,
                "write_safety_block_code": "CHAPTER_LENGTH_BLOCK_HIGH",
            },
        ),
        _FakeChapter(
            id=uuid4(), project_id=p.id, production_state="pending", chapter_number=88
        ),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    rewrite_tasks = [
        _FakeRewriteTask(
            id=uuid4(),
            project_id=p.id,
            status="pending",
            trigger_type="scene_review",
        )
    ]
    session = _FakeSession(
        projects=[p],
        runs=[],
        chapters=chapters,
        drafts=drafts,
        rewrite_tasks=rewrite_tasks,
    )

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].heal_kind == "project_pipeline"
    assert stuck[0].reason == "missing_drafts"


@pytest.mark.asyncio
async def test_find_stuck_projects_temporarily_suppresses_recent_waiting_repair(
    now: _dt.datetime,
) -> None:
    """Fresh machine_blocked repair rows should not be duplicated immediately."""
    p = _FakeProject(id=uuid4(), slug="book-recent-waiting-repair")
    chapters = [
        _FakeChapter(
            id=uuid4(),
            project_id=p.id,
            production_state="blocked",
            updated_at=now - _dt.timedelta(seconds=50),
        ),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="machine_blocked",
            updated_at=now - _dt.timedelta(seconds=10),
        )
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_requeues_after_stale_waiting_repair(
    now: _dt.datetime,
) -> None:
    """Old machine_blocked rows are history, not a permanent self-heal stop."""
    p = _FakeProject(id=uuid4(), slug="book-stale-waiting-repair")
    stale_waiting_update = now - _dt.timedelta(
        seconds=WAITING_REPAIR_SUPPRESSION_SECONDS + 60
    )
    chapters = [
        _FakeChapter(
            id=uuid4(),
            project_id=p.id,
            production_state="blocked",
            updated_at=stale_waiting_update - _dt.timedelta(minutes=30),
        ),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="machine_blocked",
            updated_at=stale_waiting_update,
        )
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-stale-waiting-repair"
    assert stuck[0].reason == "blocked_chapters"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_find_stuck_projects_repairs_paused_structural_repair_project(
    now: _dt.datetime,
) -> None:
    """Structural-repair pauses stop continuation, not blocked-chapter repair."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-paused",
        status="paused",
        metadata_json={
            "generation_resume_blocked_until_repair_audit": True,
            "production_pause_reason": "structural_repair_before_continuation",
        },
    )
    chapters = [
        _FakeChapter(id=uuid4(), project_id=p.id, production_state="blocked"),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-paused"
    assert stuck[0].reason == "blocked_chapters"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_find_stuck_projects_repairs_pending_rewrite_tasks_behind_gate(
    now: _dt.datetime,
) -> None:
    """Repair-gated projects with queued rewrite work must keep self-healing."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-pending-repairs",
        status="revising",
        target_chapters=100,
        metadata_json={
            "generation_resume_blocked_until_repair_audit": True,
            "last_generation_gate_error": "Scene 12.3 blocked by plan-richness gate",
        },
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(50)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    rewrite_tasks = [
        _FakeRewriteTask(id=uuid4(), project_id=p.id, status="pending"),
        _FakeRewriteTask(id=uuid4(), project_id=p.id, status="queued"),
    ]
    session = _FakeSession(
        projects=[p],
        runs=[],
        chapters=chapters,
        drafts=drafts,
        rewrite_tasks=rewrite_tasks,
    )

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-pending-repairs"
    assert stuck[0].reason == "pending_rewrite_tasks"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_find_stuck_projects_retries_old_scene_machine_blocked_repair_loop(
    now: _dt.datetime,
) -> None:
    """Old scene machine-blocks are history, not permanent self-heal stops."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-scene-machine-blocked",
        status="revising",
        target_chapters=6,
        metadata_json={},
    )
    chapter = _FakeChapter(
        id=uuid4(),
        project_id=p.id,
        production_state="blocked",
        updated_at=now - _dt.timedelta(minutes=8),
    )
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="scene_pipeline",
            status="machine_blocked",
            current_step="scene_rewrite_stalled_blocked",
            updated_at=now - _dt.timedelta(minutes=7),
        ),
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="machine_blocked",
            current_step="machine_repair_required",
            updated_at=now - _dt.timedelta(minutes=7),
        ),
    ]
    rewrite_tasks = [_FakeRewriteTask(id=uuid4(), project_id=p.id, status="pending")]
    session = _FakeSession(
        projects=[p],
        runs=runs,
        chapters=[chapter],
        drafts=[_FakeDraft(id=uuid4(), chapter_id=chapter.id, is_current=True)],
        rewrite_tasks=rewrite_tasks,
    )

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-scene-machine-blocked"
    assert stuck[0].reason == "blocked_chapters"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_fresh_scene_machine_blocked_repair_loop(
    now: _dt.datetime,
) -> None:
    """Fresh scene machine-blocks suppress duplicate repair dispatch briefly."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-fresh-scene-machine-blocked",
        status="revising",
        target_chapters=6,
        metadata_json={},
    )
    chapter = _FakeChapter(
        id=uuid4(),
        project_id=p.id,
        production_state="blocked",
        updated_at=now - _dt.timedelta(seconds=30),
    )
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="scene_pipeline",
            status="machine_blocked",
            current_step="scene_rewrite_stalled_blocked",
            updated_at=now - _dt.timedelta(seconds=20),
        ),
    ]
    rewrite_tasks = [_FakeRewriteTask(id=uuid4(), project_id=p.id, status="pending")]
    session = _FakeSession(
        projects=[p],
        runs=runs,
        chapters=[chapter],
        drafts=[_FakeDraft(id=uuid4(), chapter_id=chapter.id, is_current=True)],
        rewrite_tasks=rewrite_tasks,
    )

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_library_archived_projects(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-archived",
        status="revising",
        target_chapters=100,
        metadata_json={"library_archived": True},
    )
    chapters = [
        _FakeChapter(id=uuid4(), project_id=p.id, production_state="blocked"),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    rewrite_tasks = [_FakeRewriteTask(id=uuid4(), project_id=p.id, status="pending")]
    session = _FakeSession(
        projects=[p],
        runs=[],
        chapters=chapters,
        drafts=drafts,
        rewrite_tasks=rewrite_tasks,
    )

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_focus_paused_projects(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-focus-paused",
        status="paused",
        target_chapters=100,
        metadata_json={
            "production_paused": True,
            "production_pause_reason": "focus_qingnang_only_20260525",
            "focus_pause": {"reason": "focus_qingnang_only_20260525"},
        },
    )
    chapters = [
        _FakeChapter(id=uuid4(), project_id=p.id, production_state="blocked"),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    rewrite_tasks = [_FakeRewriteTask(id=uuid4(), project_id=p.id, status="pending")]
    session = _FakeSession(
        projects=[p],
        runs=[],
        chapters=chapters,
        drafts=drafts,
        rewrite_tasks=rewrite_tasks,
    )

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_framework_owned_chapter_first_project(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-chapter-first-owned",
        status="revising",
        target_chapters=10,
        metadata_json={
            "chapter_first_generation": True,
            "self_heal_suppressed": True,
            "self_heal_suppressed_reason": "chapter_first_framework_owned",
        },
    )
    chapters = [
        _FakeChapter(id=uuid4(), project_id=p.id, production_state="blocked"),
    ]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=chapters[0].id, is_current=True)]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_retries_stale_generation_gate_pause(
    now: _dt.datetime,
) -> None:
    """Planning gate pauses should re-enter machine repair after the cooldown."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-stale-planning-gate",
        status="paused",
        target_chapters=500,
        metadata_json={
            "generation_resume_blocked_by_planning_gate": True,
            "generation_auto_repair_exhausted": True,
            "production_paused": True,
            "production_pause_reason": "volume_outline_gate_failed:plan_chapter_opening_generic",
            "last_generation_gate_blocked_at": (
                now
                - _dt.timedelta(seconds=GENERATION_GATE_RESUME_COOLDOWN_SECONDS + 60)
            ).isoformat(),
        },
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(50)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-stale-planning-gate"
    assert stuck[0].reason == "generation_gate_auto_retry_needed"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_find_stuck_projects_retries_stale_scene_plan_gate_pause(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-stale-scene-plan-gate",
        status="paused",
        target_chapters=120,
        metadata_json={
            "generation_resume_blocked_by_planning_gate": True,
            "generation_auto_repair_exhausted": True,
            "production_paused": True,
            "production_pause_reason": (
                "scene_plan_richness_gate_failed:interactive_needs_two"
            ),
            "last_generation_gate_blocked_at": (
                now
                - _dt.timedelta(seconds=GENERATION_GATE_RESUME_COOLDOWN_SECONDS + 60)
            ).isoformat(),
        },
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(50)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-stale-scene-plan-gate"
    assert stuck[0].reason == "generation_gate_auto_retry_needed"
    assert stuck[0].heal_kind == "repair"


@pytest.mark.asyncio
async def test_find_stuck_projects_keeps_fresh_generation_gate_pause_blocked(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-fresh-planning-gate",
        status="paused",
        target_chapters=500,
        metadata_json={
            "generation_resume_blocked_by_planning_gate": True,
            "generation_auto_repair_exhausted": True,
            "production_paused": True,
            "production_pause_reason": "story_bible_gate_failed",
            "last_generation_gate_blocked_at": (
                now - _dt.timedelta(seconds=max(GENERATION_GATE_RESUME_COOLDOWN_SECONDS // 2, 1))
            ).isoformat(),
        },
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(50)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_clear_auto_resumable_generation_gate_pause(
    now: _dt.datetime,
) -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-clear-planning-gate",
        status="paused",
        metadata_json={
            "generation_resume_blocked_by_planning_gate": True,
            "generation_auto_repair_exhausted": True,
            "production_paused": True,
            "production_pause_reason": "volume_outline_gate_failed:plan_chapter_opening_generic",
            "last_generation_gate_blocked_at": (
                now
                - _dt.timedelta(seconds=GENERATION_GATE_RESUME_COOLDOWN_SECONDS + 60)
            ).isoformat(),
            "last_generation_gate_error": "old diagnostic",
        },
    )
    session = _FakeSession(projects=[p], runs=[], chapters=[], drafts=[])

    cleared = await _clear_auto_resumable_generation_gate_pause(session, p.id)

    assert cleared is True
    assert p.status == "revising"
    assert "generation_resume_blocked_by_planning_gate" not in p.metadata_json
    assert "generation_auto_repair_exhausted" not in p.metadata_json
    assert "production_paused" not in p.metadata_json
    assert "production_pause_reason" not in p.metadata_json
    assert p.metadata_json["last_generation_gate_error"] == "old diagnostic"
    assert p.metadata_json["last_generation_gate_auto_resumed_reason"] == (
        "volume_outline_gate_failed:plan_chapter_opening_generic"
    )


@pytest.mark.asyncio
async def test_clear_auto_resumable_generation_gate_pause_handles_temporary_throttle() -> None:
    p = _FakeProject(
        id=uuid4(),
        slug="book-clear-temporary-throttle",
        status="paused",
        metadata_json={
            "production_paused": True,
            "production_pause_reason": "temporary_planning_throttle_for_new_books",
            "generation_resume_blocked_until_repair_audit": True,
            "paused_at": "2026-06-02T08:00:00+00:00",
        },
    )
    session = _FakeSession(projects=[p], runs=[], chapters=[], drafts=[])

    cleared = await _clear_auto_resumable_generation_gate_pause(session, p.id)

    assert cleared is True
    assert p.status == "revising"
    assert "production_paused" not in p.metadata_json
    assert "production_pause_reason" not in p.metadata_json
    assert "generation_resume_blocked_until_repair_audit" not in p.metadata_json
    assert "paused_at" not in p.metadata_json
    assert (
        p.metadata_json["last_generation_gate_auto_resumed_reason"]
        == "temporary_planning_throttle_for_new_books"
    )


def test_local_gate_exhaustion_does_not_block_resume() -> None:
    """A *local* gate exhaustion must NOT terminally block project resume.

    Previously ``qimao_opening_gate_exhausted`` froze the whole project; under
    the continuation-impact framework a local opening-gate failure is confined
    to one chapter, so the project stays resumable and keeps writing forward.
    """
    project = _FakeProject(
        id=uuid4(),
        slug="blocked-book",
        metadata_json={"qimao_opening_gate_exhausted": True},
    )

    assert _project_resume_is_blocked(project) is False


@pytest.mark.asyncio
async def test_find_stuck_projects_detects_under_target_chapters(
    now: _dt.datetime,
) -> None:
    """A project still in a writing state whose total chapter rows are
    below the planned ``target_chapters`` is stuck — the outer pipeline
    exited early before later volumes could be materialized, so every
    existing chapter row correctly has a draft but the book is nowhere
    near its planned length.
    """
    p = _FakeProject(
        id=uuid4(),
        slug="book-under-target",
        target_chapters=800,
        status="writing",
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(150)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    stuck = await find_stuck_projects(session)

    assert len(stuck) == 1
    assert stuck[0].slug == "book-under-target"
    assert stuck[0].reason == "under_target_chapters"
    assert stuck[0].stuck_at_chapter == 151
    assert stuck[0].chapters_total == 150
    assert stuck[0].chapters_with_draft == 150


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_recent_under_target_project(
    now: _dt.datetime,
) -> None:
    """Fresh quickstart projects can be under target while their first
    workflow row is still being created; self-heal must not duplicate them.
    """
    p = _FakeProject(
        id=uuid4(),
        slug="fresh-short",
        target_chapters=4,
        status="planning",
        created_at=now - _dt.timedelta(seconds=UNDER_TARGET_SELF_HEAL_GRACE_SECONDS - 5),
        updated_at=now,
    )
    chapter = _FakeChapter(id=uuid4(), project_id=p.id)
    draft = _FakeDraft(id=uuid4(), chapter_id=chapter.id, is_current=True)
    session = _FakeSession(projects=[p], runs=[], chapters=[chapter], drafts=[draft])

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_active_foundation_plan(
    now: _dt.datetime,
) -> None:
    """Foundation planning is an active pipeline for short projects; do not
    under-target heal it into duplicate foundation runs.
    """
    p = _FakeProject(
        id=uuid4(),
        slug="short-validation",
        target_chapters=4,
        status="planning",
        created_at=now - _dt.timedelta(hours=1),
        updated_at=now - _dt.timedelta(hours=1),
    )
    session = _FakeSession(
        projects=[p],
        runs=[
            _FakeWorkflowRun(
                id=uuid4(),
                project_id=p.id,
                workflow_type="generate_foundation_plan",
                status="running",
                updated_at=now,
            )
        ],
        chapters=[],
        drafts=[],
    )

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_skips_under_target_when_completed(
    now: _dt.datetime,
) -> None:
    """A project the user marked ``completed`` must not be auto-resumed,
    even if its chapter count is below ``target_chapters``. Otherwise the
    self-healer would override an explicit user decision to stop writing.
    """
    p = _FakeProject(
        id=uuid4(),
        slug="book-completed-short",
        target_chapters=800,
        status="completed",
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(50)]
    drafts = [_FakeDraft(id=uuid4(), chapter_id=c.id, is_current=True) for c in chapters]
    session = _FakeSession(projects=[p], runs=[], chapters=chapters, drafts=drafts)

    assert await find_stuck_projects(session) == []


@pytest.mark.asyncio
async def test_find_stuck_projects_ignores_at_target_project(
    now: _dt.datetime,
) -> None:
    """A project whose chapter rows exactly match ``target_chapters`` is
    complete and must not be flagged under-target."""
    p = _FakeProject(
        id=uuid4(),
        slug="book-full",
        target_chapters=10,
        status="writing",
    )
    chapters = [_FakeChapter(id=uuid4(), project_id=p.id) for _ in range(10)]
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
async def test_reap_orphan_workflow_runs_by_startup_created_at(
    now: _dt.datetime,
) -> None:
    """A pre-boot child row is stale even if it heartbeated right before restart."""
    p = _FakeProject(id=uuid4(), slug="book-created-before-boot")
    startup_cutoff = now - _dt.timedelta(seconds=STARTUP_GRACE_SECONDS)
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="chapter_pipeline",
            status="running",
            created_at=startup_cutoff - _dt.timedelta(minutes=5),
            updated_at=now - _dt.timedelta(seconds=5),
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(
        session,
        startup_cutoff=startup_cutoff,
    )

    assert reaped == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_preserves_in_progress_project(
    now: _dt.datetime,
) -> None:
    """Startup self-heal must not reap a project owned by a live ARQ job."""
    protected = _FakeProject(id=uuid4(), slug="book-live")
    stale = _FakeProject(id=uuid4(), slug="book-stale")
    startup_cutoff = now - _dt.timedelta(seconds=STARTUP_GRACE_SECONDS)
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=protected.id,
            workflow_type="chapter_pipeline",
            status="running",
            created_at=startup_cutoff - _dt.timedelta(minutes=5),
            updated_at=now - _dt.timedelta(seconds=5),
        ),
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=stale.id,
            workflow_type="chapter_pipeline",
            status="running",
            created_at=startup_cutoff - _dt.timedelta(minutes=5),
            updated_at=now - _dt.timedelta(seconds=5),
        ),
    ]
    session = _FakeSession(projects=[protected, stale], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(
        session,
        startup_cutoff=startup_cutoff,
        protected_project_ids={protected.id},
    )

    assert reaped == 1
    assert runs[0].status == "running"
    assert runs[1].status == "failed"


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
async def test_reap_orphan_workflow_runs_reaps_volume_planning_rows(
    now: _dt.datetime,
) -> None:
    """Per-volume planner rows are worker-owned and must not stay running forever."""
    p = _FakeProject(id=uuid4(), slug="book-volume-plan")
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="generate_volume_plan",
            status="running",
            updated_at=now - _dt.timedelta(hours=5),
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session)

    assert reaped == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_reaps_project_repair_by_heartbeat(
    now: _dt.datetime,
) -> None:
    """Project repair rows have a worker DB heartbeat and should not stall forever."""
    p = _FakeProject(id=uuid4(), slug="book-repair")
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="running",
            updated_at=now - _dt.timedelta(hours=5),
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session)

    assert reaped == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_reaps_project_repair_on_short_window(
    now: _dt.datetime,
) -> None:
    """Project repair heartbeats per chapter (60s), so a 45-min-stale row is a
    dead worker and must reap on the short (30-min) window — not wait the 3h
    planning timeout. Leaving it ``running`` keeps the synthetic ``db-repair:``
    card "running", which deadlocks the dashboard Stop/Delete buttons.
    """
    p = _FakeProject(id=uuid4(), slug="book-repair-short-window")
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="running",
            # Stale by 45 min: past the 30-min heartbeat window but well within
            # the 3h planning timeout. Under the old 3h-only rule this would NOT
            # reap; it must now.
            updated_at=now - _dt.timedelta(minutes=45),
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session)

    assert reaped == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_preserves_live_project_repair_heartbeat(
    now: _dt.datetime,
) -> None:
    """A project_repair row that heartbeated within the short window is a live
    worker and must NOT be reaped (no startup_cutoff in play)."""
    p = _FakeProject(id=uuid4(), slug="book-repair-live")
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="running",
            updated_at=now - _dt.timedelta(minutes=2),
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session)

    assert reaped == 0
    assert runs[0].status == "running"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_reaps_project_repair_by_startup_cutoff(
    now: _dt.datetime,
) -> None:
    """A repair row from a previous worker must not block startup self-heal."""
    p = _FakeProject(id=uuid4(), slug="book-repair-startup")
    startup_cutoff = now - _dt.timedelta(seconds=STARTUP_GRACE_SECONDS)
    runs = [
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="running",
            created_at=startup_cutoff - _dt.timedelta(minutes=5),
            updated_at=startup_cutoff - _dt.timedelta(minutes=1),
        ),
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="project_repair",
            status="running",
            created_at=now,
            updated_at=now,
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session, startup_cutoff=startup_cutoff)

    assert reaped == 1
    assert runs[0].status == "failed"
    assert runs[1].status == "running"


@pytest.mark.asyncio
async def test_reap_orphan_workflow_runs_reaps_child_when_parent_terminal(
    now: _dt.datetime,
) -> None:
    """A child scene workflow cannot remain running after its parent failed."""
    p = _FakeProject(id=uuid4(), slug="book-child")
    parent_failed_id = uuid4()
    parent_active_id = uuid4()
    runs = [
        _FakeWorkflowRun(
            id=parent_failed_id,
            project_id=p.id,
            workflow_type="chapter_pipeline",
            status="failed",
            updated_at=now,
        ),
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="scene_pipeline",
            status="running",
            updated_at=now,
            metadata_json={"parent_workflow_run_id": str(parent_failed_id)},
        ),
        _FakeWorkflowRun(
            id=parent_active_id,
            project_id=p.id,
            workflow_type="chapter_pipeline",
            status="running",
            updated_at=now,
        ),
        _FakeWorkflowRun(
            id=uuid4(),
            project_id=p.id,
            workflow_type="scene_pipeline",
            status="running",
            updated_at=now,
            metadata_json={"parent_workflow_run_id": str(parent_active_id)},
        ),
    ]
    session = _FakeSession(projects=[p], runs=runs, chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(
        session,
        startup_cutoff=now - _dt.timedelta(seconds=STARTUP_GRACE_SECONDS),
    )

    assert reaped == 1
    assert runs[1].status == "failed"
    assert runs[3].status == "running"


@pytest.mark.asyncio
async def test_active_arq_project_slugs_reads_in_progress_payload() -> None:
    redis = _FakeInProgressRedis(
        {
            "job-1": {
                "k": {
                    "payload": {
                        "project_slug": "book-live",
                    },
                },
            },
            "job-2": {
                "k": {
                    "payload": {},
                },
            },
        },
    )

    assert await _active_arq_project_slugs(redis) == {"book-live"}


@pytest.mark.asyncio
async def test_active_arq_project_slugs_reads_retry_payload() -> None:
    class _RetryOnlyRedis(_FakeInProgressRedis):
        async def scan_iter(self, match: str) -> Any:  # noqa: ARG002
            if match == "arq:retry:*":
                for job_id in self.jobs:
                    yield f"arq:retry:{job_id}".encode()

    redis = _RetryOnlyRedis(
        {
            "job-retry": {
                "k": {
                    "payload": {
                        "project_slug": "book-retrying",
                    },
                },
            },
        },
    )

    assert await _active_arq_project_slugs(redis) == {"book-retrying"}


@pytest.mark.asyncio
async def test_active_arq_project_slugs_ignores_stale_in_progress_owner() -> None:
    redis = _FakeInProgressRedis(
        {
            "job-stale": {
                "k": {
                    "payload": {
                        "project_slug": "book-stale",
                    },
                },
            },
        },
        queue_scores={"arq:queue:job-stale": 0.0},
    )

    assert await _active_arq_project_slugs(redis) == set()


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
    from bestseller.worker.self_heal import (
        _autowrite_heal_job_id,
        _project_pipeline_heal_job_id,
        _repair_heal_job_id,
    )

    assert _autowrite_heal_job_id("slug-a") == "autowrite:heal:slug-a"
    assert _project_pipeline_heal_job_id("slug-a") == "project-pipeline:heal:slug-a"
    assert _repair_heal_job_id("slug-a") == "repair:heal:slug-a"
    # Identical across calls → ARQ dedup will reject a second enqueue.
    assert _autowrite_heal_job_id("slug-a") == _autowrite_heal_job_id("slug-a")
    # Different slugs → different ids
    assert _autowrite_heal_job_id("slug-a") != _autowrite_heal_job_id("slug-b")


def test_coalesce_stuck_projects_prefers_fresh_autowrite_for_same_slug() -> None:
    from bestseller.worker.self_heal import _coalesce_stuck_projects_for_enqueue

    project_id = uuid4()
    stuck = [
        StuckProject(
            project_id=project_id,
            slug="book-a",
            reason="missing_drafts",
            stuck_at_chapter=10,
            chapters_total=20,
            chapters_with_draft=9,
            heal_kind="project_pipeline",
        ),
        StuckProject(
            project_id=project_id,
            slug="book-a",
            reason="no_chapters",
            stuck_at_chapter=None,
            chapters_total=0,
            chapters_with_draft=0,
            heal_kind="autowrite",
        ),
        StuckProject(
            project_id=project_id,
            slug="book-a",
            reason="blocked_chapters",
            stuck_at_chapter=None,
            chapters_total=20,
            chapters_with_draft=20,
            heal_kind="repair",
        ),
    ]

    coalesced = _coalesce_stuck_projects_for_enqueue(stuck)

    assert len(coalesced) == 1
    assert coalesced[0].slug == "book-a"
    assert coalesced[0].heal_kind == "autowrite"


class _FakeArqPool:
    def __init__(
        self,
        reject_job_ids: set[str] | None = None,
        reject_once_job_ids: set[str] | None = None,
        existing_keys: set[str] | None = None,
        queue_scores: dict[str, float] | None = None,
    ) -> None:
        self.reject_job_ids = reject_job_ids or set()
        self.reject_once_job_ids = reject_once_job_ids or set()
        self.existing_keys = existing_keys or set()
        self.queue_scores = queue_scores or {}
        self.enqueued: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.zremoved: list[tuple[str, str]] = []

    async def enqueue_job(
        self,
        function: str,
        *,
        workflow_run_id: str,
        payload: dict[str, Any],
        _job_id: str,
        _expires: Any = None,
    ) -> Any:
        self.enqueued.append(
            {
                "function": function,
                "workflow_run_id": workflow_run_id,
                "payload": payload,
                "_job_id": _job_id,
                "_expires": _expires,
            }
        )
        if _job_id in self.reject_once_job_ids:
            self.reject_once_job_ids.remove(_job_id)
            return None
        if _job_id in self.reject_job_ids:
            return None
        return object()  # non-None sentinel — ARQ returns a Job instance

    async def exists(self, *keys: str) -> int:
        return sum(1 for key in keys if key in self.existing_keys)

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        count = 0
        for key in keys:
            if key in self.existing_keys:
                self.existing_keys.remove(key)
                count += 1
        return count

    async def zscore(self, key: str, member: str) -> float | None:
        return self.queue_scores.get(f"{key}:{member}")

    async def zrem(self, key: str, member: str) -> int:
        self.zremoved.append((key, member))
        return 1 if self.queue_scores.pop(f"{key}:{member}", None) is not None else 0


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
    assert pool.enqueued[0]["_expires"].days >= 7


@pytest.mark.asyncio
async def test_requeue_repair_returns_job_id_on_success() -> None:
    from bestseller.worker.self_heal import _requeue_repair

    pool = _FakeArqPool()
    stuck = StuckProject(
        project_id="p1",
        slug="book-repair",
        reason="blocked_chapters",
        stuck_at_chapter=None,
        chapters_total=10,
        chapters_with_draft=10,
        heal_kind="repair",
    )

    job_id = await _requeue_repair(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "repair:heal:book-repair"
    assert len(pool.enqueued) == 1
    assert pool.enqueued[0]["function"] == "run_project_repair_task"
    assert pool.enqueued[0]["_job_id"] == "repair:heal:book-repair"
    assert pool.enqueued[0]["payload"] == {
        "project_slug": "book-repair",
        "requested_by": "worker_self_heal",
        "include_pending_rewrite_tasks": True,
        "pending_rewrite_task_limit": SELF_HEAL_PENDING_REWRITE_TASK_LIMIT,
    }


@pytest.mark.asyncio
async def test_requeue_project_pipeline_returns_job_id_on_success() -> None:
    from bestseller.worker.self_heal import _requeue_project_pipeline

    pool = _FakeArqPool()
    stuck = StuckProject(
        project_id="p1",
        slug="book-continue",
        reason="missing_drafts",
        stuck_at_chapter=8,
        chapters_total=10,
        chapters_with_draft=7,
        heal_kind="project_pipeline",
    )

    job_id = await _requeue_project_pipeline(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "project-pipeline:heal:book-continue"
    assert len(pool.enqueued) == 1
    assert pool.enqueued[0]["function"] == "run_project_pipeline_task"
    assert pool.enqueued[0]["_job_id"] == "project-pipeline:heal:book-continue"
    assert pool.enqueued[0]["payload"] == {"project_slug": "book-continue"}
    assert pool.enqueued[0]["_expires"].days >= 7


@pytest.mark.asyncio
async def test_requeue_project_pipeline_allows_parallel_repair_owner() -> None:
    from bestseller.worker.self_heal import _requeue_project_pipeline

    pool = _FakeArqPool(
        existing_keys={"arq:in-progress:repair:heal:book-continue"}
    )
    stuck = StuckProject(
        project_id="p1",
        slug="book-continue",
        reason="missing_drafts",
        stuck_at_chapter=89,
        chapters_total=200,
        chapters_with_draft=88,
        heal_kind="project_pipeline",
    )

    job_id = await _requeue_project_pipeline(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "project-pipeline:heal:book-continue"
    assert len(pool.enqueued) == 1
    assert pool.enqueued[0]["function"] == "run_project_pipeline_task"
    assert pool.enqueued[0]["_job_id"] == "project-pipeline:heal:book-continue"


@pytest.mark.asyncio
async def test_requeue_autowrite_skips_when_repair_job_owns_project() -> None:
    from bestseller.worker.self_heal import _requeue_autowrite

    pool = _FakeArqPool(existing_keys={"arq:in-progress:repair:heal:book-owned"})
    stuck = StuckProject(
        project_id="p1",
        slug="book-owned",
        reason="missing_drafts",
        stuck_at_chapter=3,
        chapters_total=10,
        chapters_with_draft=2,
    )

    job_id = await _requeue_autowrite(pool, stuck)  # type: ignore[arg-type]

    assert job_id is None
    assert pool.enqueued == []


@pytest.mark.asyncio
async def test_requeue_repair_skips_when_autowrite_job_owns_project() -> None:
    from bestseller.worker.self_heal import _requeue_repair

    pool = _FakeArqPool(existing_keys={"arq:in-progress:autowrite:heal:book-owned"})
    stuck = StuckProject(
        project_id="p1",
        slug="book-owned",
        reason="blocked_chapters",
        stuck_at_chapter=None,
        chapters_total=10,
        chapters_with_draft=10,
        heal_kind="repair",
    )

    job_id = await _requeue_repair(pool, stuck)  # type: ignore[arg-type]

    assert job_id is None
    assert pool.enqueued == []


@pytest.mark.asyncio
async def test_requeue_autowrite_returns_none_when_arq_dedups() -> None:
    """ARQ returning None means a same-id job is already pending/running."""
    from bestseller.worker.self_heal import _requeue_autowrite

    pool = _FakeArqPool(
        reject_job_ids={"autowrite:heal:book-dup"},
        existing_keys={"arq:job:autowrite:heal:book-dup"},
    )
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


@pytest.mark.asyncio
async def test_requeue_autowrite_clears_stale_result_before_retry() -> None:
    """A stale ARQ result key must not permanently block self-heal requeue."""
    from bestseller.worker.self_heal import _requeue_autowrite

    pool = _FakeArqPool(
        reject_once_job_ids={"autowrite:heal:book-result"},
        existing_keys={"arq:result:autowrite:heal:book-result"},
    )
    stuck = StuckProject(
        project_id="p3",
        slug="book-result",
        reason="missing_drafts",
        stuck_at_chapter=1,
        chapters_total=5,
        chapters_with_draft=0,
    )

    job_id = await _requeue_autowrite(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "autowrite:heal:book-result"
    assert len(pool.enqueued) == 2
    assert "arq:result:autowrite:heal:book-result" in pool.deleted


@pytest.mark.asyncio
async def test_requeue_autowrite_clears_stale_in_progress_before_retry() -> None:
    """A ghost in-progress ARQ key must not permanently block self-heal."""
    from bestseller.worker.self_heal import _requeue_autowrite

    job_id = "autowrite:heal:book-ghost"
    pool = _FakeArqPool(
        reject_once_job_ids={job_id},
        existing_keys={
            f"arq:job:{job_id}",
            f"arq:in-progress:{job_id}",
            f"arq:retry:{job_id}",
        },
        queue_scores={f"arq:queue:{job_id}": 0.0},
    )
    stuck = StuckProject(
        project_id="p4",
        slug="book-ghost",
        reason="missing_drafts",
        stuck_at_chapter=1,
        chapters_total=5,
        chapters_with_draft=0,
    )

    actual_job_id = await _requeue_autowrite(pool, stuck)  # type: ignore[arg-type]

    assert actual_job_id == job_id
    assert len(pool.enqueued) == 2
    assert f"arq:job:{job_id}" in pool.deleted
    assert f"arq:in-progress:{job_id}" in pool.deleted
    assert f"arq:retry:{job_id}" in pool.deleted
    assert ("arq:queue", job_id) in pool.zremoved


@pytest.mark.asyncio
async def test_requeue_autowrite_clears_stale_repair_owner() -> None:
    """A stale repair owner must not suppress autowrite recovery forever."""
    from bestseller.worker.self_heal import _requeue_autowrite

    repair_job_id = "repair:heal:book-cross-stale"
    pool = _FakeArqPool(
        existing_keys={
            f"arq:job:{repair_job_id}",
            f"arq:in-progress:{repair_job_id}",
            f"arq:retry:{repair_job_id}",
        },
        queue_scores={f"arq:queue:{repair_job_id}": 0.0},
    )
    stuck = StuckProject(
        project_id="p5",
        slug="book-cross-stale",
        reason="missing_drafts",
        stuck_at_chapter=1,
        chapters_total=5,
        chapters_with_draft=0,
    )

    job_id = await _requeue_autowrite(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "autowrite:heal:book-cross-stale"
    assert f"arq:in-progress:{repair_job_id}" in pool.deleted
    assert ("arq:queue", repair_job_id) in pool.zremoved


@pytest.mark.asyncio
async def test_requeue_repair_clears_stale_autowrite_owner() -> None:
    """A stale autowrite owner must not suppress repair recovery forever."""
    from bestseller.worker.self_heal import _requeue_repair

    autowrite_job_id = "autowrite:heal:book-cross-stale"
    pool = _FakeArqPool(
        existing_keys={
            f"arq:job:{autowrite_job_id}",
            f"arq:in-progress:{autowrite_job_id}",
            f"arq:retry:{autowrite_job_id}",
        },
        queue_scores={f"arq:queue:{autowrite_job_id}": 0.0},
    )
    stuck = StuckProject(
        project_id="p6",
        slug="book-cross-stale",
        reason="blocked_chapters",
        stuck_at_chapter=None,
        chapters_total=5,
        chapters_with_draft=5,
        heal_kind="repair",
    )

    job_id = await _requeue_repair(pool, stuck)  # type: ignore[arg-type]

    assert job_id == "repair:heal:book-cross-stale"
    assert f"arq:in-progress:{autowrite_job_id}" in pool.deleted
    assert ("arq:queue", autowrite_job_id) in pool.zremoved


# ---------------------------------------------------------------------------
# heal_stuck_projects: delete-tombstone guard
# ---------------------------------------------------------------------------


class _NullSessionCM:
    """Async context manager yielding a session that satisfies the dispatch loop."""

    def __init__(self, project: Any) -> None:
        self._project = project

    async def __aenter__(self) -> Any:
        project = self._project

        class _S:
            async def get(self, _model: Any, _pid: Any) -> Any:
                return project

            async def commit(self) -> None:
                return None

        return _S()

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_heal_stuck_projects_skips_delete_tombstoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.worker import self_heal as sh

    tombstoned = StuckProject(
        project_id="p-dead",
        slug="book-deleted",
        reason="under_target",
        stuck_at_chapter=None,
        chapters_total=3,
        chapters_with_draft=3,
    )
    alive = StuckProject(
        project_id="p-alive",
        slug="book-alive",
        reason="under_target",
        stuck_at_chapter=None,
        chapters_total=3,
        chapters_with_draft=3,
    )

    async def _fake_find(_session: Any) -> list[StuckProject]:
        return [tombstoned, alive]

    async def _async_true(*_a: Any, **_k: Any) -> bool:
        return True

    async def _async_false(*_a: Any, **_k: Any) -> bool:
        return False

    async def _async_zero(*_a: Any, **_k: Any) -> int:
        return 0

    async def _async_empty_set(*_a: Any, **_k: Any) -> set[Any]:
        return set()

    class _Pool:
        async def aclose(self) -> None:
            return None

    async def _fake_create_pool(*_a: Any, **_k: Any) -> Any:
        return _Pool()

    requeued: list[str] = []

    async def _fake_requeue(_pool: Any, stuck: StuckProject) -> str:
        requeued.append(stuck.slug)
        return f"task:{stuck.slug}"

    monkeypatch.setattr(sh, "find_stuck_projects", _fake_find)
    monkeypatch.setattr(sh, "reap_orphan_workflow_runs", _async_zero)
    monkeypatch.setattr(sh, "_active_arq_project_slugs", _async_empty_set)
    monkeypatch.setattr(sh, "_resolve_project_ids_for_slugs", _async_empty_set)
    monkeypatch.setattr(sh, "_try_acquire_heal_lock", _async_true)
    monkeypatch.setattr(sh, "_has_active_continuation_pipeline_run", _async_false)
    monkeypatch.setattr(sh, "_has_active_pipeline_run", _async_false)
    monkeypatch.setattr(sh, "_clear_auto_resumable_generation_gate_pause", _async_false)
    monkeypatch.setattr(sh, "_requeue_stuck_project", _fake_requeue)
    monkeypatch.setattr(
        sh, "_compute_heal_progress_state", lambda meta, total: (meta, False)
    )
    monkeypatch.setattr(
        sh, "get_server_session", lambda: _NullSessionCM(_FakeProject(id="p", slug="x"))
    )
    monkeypatch.setattr(
        sh,
        "is_project_delete_tombstoned",
        lambda _settings, slug: slug == "book-deleted",
    )

    import arq.connections as arq_connections

    monkeypatch.setattr(arq_connections, "create_pool", _fake_create_pool)

    settings = SimpleNamespace(
        output=SimpleNamespace(base_dir="/tmp/does-not-matter"),
        redis=SimpleNamespace(url="redis://localhost:6379/0"),
    )

    dispatched = await heal_stuck_projects(settings, redis=None)

    # The tombstoned project is never re-queued; the live one still is.
    assert requeued == ["book-alive"]
    assert [d["slug"] for d in dispatched] == ["book-alive"]


# ---------------------------------------------------------------------------
# R24: orphan ``arq:job`` key guard — a half-enqueued job key (not in queue,
# not executing) must stop blocking re-enqueue once it is over the age cap.
# ---------------------------------------------------------------------------


class _FakeOrphanArqPool(_FakeArqPool):
    """_FakeArqPool + the GET/PTTL surface the orphan-age probe needs."""

    def __init__(
        self,
        *args: Any,
        job_payloads: dict[str, bytes] | None = None,
        pttls: dict[str, int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.job_payloads = job_payloads or {}
        self.pttls = pttls or {}

    async def get(self, key: str) -> bytes | None:
        return self.job_payloads.get(key)

    async def pttl(self, key: str) -> int:
        return self.pttls.get(key, -2)


def _job_payload_bytes(age_seconds: float) -> bytes:
    import time

    return pickle.dumps({"t": (time.time() - age_seconds) * 1000})


@pytest.mark.asyncio
async def test_is_orphan_heal_job_clears_overage_bare_key() -> None:
    from bestseller.worker.self_heal import _is_orphan_heal_job

    job_id = "repair:heal:book-orphan"
    pool = _FakeOrphanArqPool(
        existing_keys={f"arq:job:{job_id}", f"arq:retry:{job_id}"},
        job_payloads={f"arq:job:{job_id}": _job_payload_bytes(2 * 60 * 60)},
    )

    assert await _is_orphan_heal_job(pool, job_id) is True


@pytest.mark.asyncio
async def test_is_orphan_heal_job_respects_queue_membership() -> None:
    from bestseller.worker.self_heal import _is_orphan_heal_job

    job_id = "repair:heal:book-queued"
    pool = _FakeOrphanArqPool(
        existing_keys={f"arq:job:{job_id}"},
        queue_scores={f"arq:queue:{job_id}": 12345.0},
        job_payloads={f"arq:job:{job_id}": _job_payload_bytes(2 * 60 * 60)},
    )

    assert await _is_orphan_heal_job(pool, job_id) is False


@pytest.mark.asyncio
async def test_is_orphan_heal_job_respects_in_progress_lock() -> None:
    from bestseller.worker.self_heal import _is_orphan_heal_job

    job_id = "repair:heal:book-running"
    pool = _FakeOrphanArqPool(
        existing_keys={f"arq:job:{job_id}", f"arq:in-progress:{job_id}"},
        job_payloads={f"arq:job:{job_id}": _job_payload_bytes(2 * 60 * 60)},
    )

    assert await _is_orphan_heal_job(pool, job_id) is False


@pytest.mark.asyncio
async def test_is_orphan_heal_job_keeps_young_key() -> None:
    from bestseller.worker.self_heal import _is_orphan_heal_job

    job_id = "repair:heal:book-fresh"
    pool = _FakeOrphanArqPool(
        existing_keys={f"arq:job:{job_id}"},
        job_payloads={f"arq:job:{job_id}": _job_payload_bytes(10 * 60)},
    )

    assert await _is_orphan_heal_job(pool, job_id) is False


@pytest.mark.asyncio
async def test_is_orphan_heal_job_infers_age_from_ttl_when_payload_unreadable() -> None:
    """No readable enqueue time + no result key → age from remaining TTL."""

    from bestseller.worker.self_heal import (
        SELF_HEAL_JOB_EXPIRES_DAYS,
        _is_orphan_heal_job,
    )

    job_id = "repair:heal:book-ttl"
    # 1 day of the 7-day expiry window remains → key is ~6 days old.
    remaining_ms = 1 * 24 * 60 * 60 * 1000
    assert SELF_HEAL_JOB_EXPIRES_DAYS >= 2
    pool = _FakeOrphanArqPool(
        existing_keys={f"arq:job:{job_id}"},
        pttls={f"arq:job:{job_id}": remaining_ms},
    )

    assert await _is_orphan_heal_job(pool, job_id) is True


@pytest.mark.asyncio
async def test_is_orphan_heal_job_unknown_age_is_not_cleared() -> None:
    """Age unresolvable (no payload, no TTL) → conservative keep."""

    from bestseller.worker.self_heal import _is_orphan_heal_job

    job_id = "repair:heal:book-unknown"
    pool = _FakeOrphanArqPool(existing_keys={f"arq:job:{job_id}"})

    assert await _is_orphan_heal_job(pool, job_id) is False


@pytest.mark.asyncio
async def test_requeue_repair_clears_overage_orphan_with_retry_counter() -> None:
    """The exact R24 deploy-window shape: job key + retry counter, no queue
    score, no in-progress lock. Ghost detection bails on the retry key, so
    before the orphan guard the project never retried (manual DEL only)."""

    from bestseller.worker.self_heal import _requeue_repair

    job_id = "repair:heal:book-r24"
    pool = _FakeOrphanArqPool(
        reject_once_job_ids={job_id},
        existing_keys={f"arq:job:{job_id}", f"arq:retry:{job_id}"},
        job_payloads={f"arq:job:{job_id}": _job_payload_bytes(3 * 60 * 60)},
    )
    stuck = StuckProject(
        project_id="p-r24",
        slug="book-r24",
        reason="blocked_chapters",
        stuck_at_chapter=None,
        chapters_total=10,
        chapters_with_draft=10,
        heal_kind="repair",
    )

    actual_job_id = await _requeue_repair(pool, stuck)  # type: ignore[arg-type]

    assert actual_job_id == job_id
    assert len(pool.enqueued) == 2
    assert f"arq:job:{job_id}" in pool.deleted
    assert f"arq:retry:{job_id}" in pool.deleted


@pytest.mark.asyncio
async def test_requeue_repair_keeps_young_half_enqueued_job() -> None:
    """A job key younger than the cap is treated as live — no clearing."""

    from bestseller.worker.self_heal import _requeue_repair

    job_id = "repair:heal:book-young"
    pool = _FakeOrphanArqPool(
        reject_job_ids={job_id},
        existing_keys={f"arq:job:{job_id}", f"arq:retry:{job_id}"},
        job_payloads={f"arq:job:{job_id}": _job_payload_bytes(5 * 60)},
    )
    stuck = StuckProject(
        project_id="p-young",
        slug="book-young",
        reason="blocked_chapters",
        stuck_at_chapter=None,
        chapters_total=10,
        chapters_with_draft=10,
        heal_kind="repair",
    )

    actual_job_id = await _requeue_repair(pool, stuck)  # type: ignore[arg-type]

    assert actual_job_id is None
    assert f"arq:job:{job_id}" not in pool.deleted

"""Unit tests for worker self-heal detector.

Focus: pure functions and the *logic* of ``reap_orphan_workflow_runs`` /
``find_stuck_projects``. Real DB integration is exercised manually via the
worker container; here we stub session objects so the tests stay fast and
offline-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
from typing import Any
from uuid import uuid4

import pytest

from bestseller.worker.self_heal import (
    GENERATION_GATE_RESUME_COOLDOWN_SECONDS,
    SELF_HEAL_PENDING_REWRITE_TASK_LIMIT,
    STARTUP_GRACE_SECONDS,
    WAITING_REPAIR_SUPPRESSION_SECONDS,
    StuckProject,
    _clear_auto_resumable_generation_gate_pause,
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
    target_chapters: int = 0
    status: str = "writing"


@dataclass
class _FakeWorkflowRun:
    id: Any
    project_id: Any
    workflow_type: str
    status: str
    updated_at: _dt.datetime
    created_at: _dt.datetime | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeChapter:
    id: Any
    project_id: Any
    production_state: str = "ok"
    updated_at: _dt.datetime = field(default_factory=lambda: _dt.datetime.now(_dt.UTC))


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
            RewriteTaskModel,
        )

        target = self._target_model(stmt)
        project_id = self._filter_project_id(stmt)

        if target is WorkflowRunModel:
            sql_text = str(stmt).lower()
            if "max(" in sql_text:
                matching = [
                    r.updated_at
                    for r in self.runs
                    if r.project_id == project_id
                    and r.workflow_type == "project_repair"
                    and r.status == "machine_blocked"
                ]
                return max(matching, default=None)

            active = {"pending", "queued", "running"}
            pipeline_types = {
                "autowrite_pipeline",
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

        cutoff = self._filter_updated_before(stmt)
        created_cutoff = self._filter_created_before(stmt)
        statuses = {"pending", "queued", "running"}
        reapable_types = {
            "autowrite_pipeline",
            "generate_novel_plan",
            "generate_volume_plan",
            "project_pipeline",
            "chapter_pipeline",
            "scene_pipeline",
            "project_repair",
        }
        count = 0
        for r in self.runs:
            created_at = r.created_at or r.updated_at
            stale_by_heartbeat = r.updated_at < cutoff
            stale_by_startup = created_cutoff is not None and created_at < created_cutoff
            if (
                r.workflow_type in reapable_types
                and r.status in statuses
                and (stale_by_heartbeat or stale_by_startup)
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
async def test_find_stuck_projects_retries_stale_generation_gate_pause(
    now: _dt.datetime,
) -> None:
    """Planning gate pauses should re-enter autowrite after the cooldown."""
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
    assert stuck[0].reason == "under_target_chapters"
    assert stuck[0].stuck_at_chapter == 51


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
    assert stuck[0].reason == "under_target_chapters"
    assert stuck[0].stuck_at_chapter == 51


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
                now - _dt.timedelta(minutes=5)
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


def test_coalesce_stuck_projects_prefers_repair_for_same_slug() -> None:
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
    assert coalesced[0].heal_kind == "repair"


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

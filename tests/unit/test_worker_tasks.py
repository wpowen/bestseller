from __future__ import annotations

import sys
import types
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")
redis_asyncio_module.Redis = object
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

from bestseller.services.planning_concurrency import PlanningConflictError
from bestseller.worker import tasks as worker_tasks

pytestmark = pytest.mark.unit


def test_generation_gate_block_classifies_l2_bible_gate() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError("L2 bible gate failed for project 'demo'. Regenerate the story bible.")
    )

    assert result is not None
    assert result[0] == "story_bible_gate_failed"


def test_generation_gate_block_classifies_volume_outline_gate() -> None:
    result = worker_tasks._generation_gate_block(
        RuntimeError(
            "Planner artifact 'volume_2_chapter_outline' failed chapter-outline "
            "repair loop after 3 attempt(s)."
        )
    )

    assert result is not None
    assert result[0] == "volume_outline_gate_failed"


def test_generation_gate_block_classifies_progressive_semantic_failure() -> None:
    result = worker_tasks._generation_gate_block(
        RuntimeError(
            "Progressive volume outline did not earn semantic promotion; "
            "OPENING_PULL_PARAGRAPH_FAIL, PROTAGONIST_PLOT_SERVING_STUPIDITY_CH1_CH3"
        )
    )

    assert result is not None
    assert result[0] == "outline_semantic_gate_failed:opening_pull_paragraph_fail"


def test_generation_gate_block_classifies_commercial_planning_readiness() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError(
            "Commercial planning readiness gate failed: "
            "llm:PROTAGONIST_PLOT_SERVING_STUPIDITY"
        )
    )

    assert result is not None
    assert result[0] == "commercial_planning_readiness_gate_failed"


def test_cancelled_outline_replan_closes_owner_and_keeps_hard_gate() -> None:
    project = types.SimpleNamespace(
        status="planning",
        metadata_json={
            "outline_replan_in_progress": True,
            "outline_replan_prior_outline_version": 12,
        },
    )
    owner = types.SimpleNamespace(
        status="running",
        current_step="generate_volume_1_outline",
        error_message=None,
        metadata_json={},
    )

    worker_tasks._apply_cancelled_outline_replan_state(project, [owner])

    assert project.status == "needs_replan"
    assert project.metadata_json["outline_replan_required"] is True
    assert project.metadata_json["production_pause_reason"] == (
        "outline_replan_cancelled_recoverable"
    )
    assert "outline_replan_in_progress" not in project.metadata_json
    assert owner.status == "failed"
    assert owner.current_step == "cancelled_recoverable"
    assert owner.metadata_json["cancelled_recoverable"] is True


@pytest.mark.asyncio
async def test_owned_outline_replan_conflict_is_closed_and_left_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovered: list[str] = []
    events: list[tuple[str, dict, str | None]] = []

    async def fake_mark(project_slug: str) -> None:
        recovered.append(project_slug)

    class _Reporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    monkeypatch.setattr(
        worker_tasks,
        "_mark_cancelled_outline_replan_recoverable",
        fake_mark,
    )
    conflict = PlanningConflictError(uuid4(), "generate_volume_plan")

    result = await worker_tasks._recover_owned_outline_replan_conflict(
        "novel",
        conflict,
        _Reporter(),
    )

    assert recovered == ["novel"]
    assert result["status"] == "outline_replan_retry_pending"
    assert result["reason"] == "owned_planning_conflict_recovered"
    assert events == [
        (
            "outline_replan_retry_pending",
            result,
            "repairable_auto_continue_pending",
        )
    ]


def test_generation_gate_block_classifies_chapter_plan_contract() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError(
            "chapter_plan_contract failed for project 'demo' while validating "
            "chapter_outline_batch: PLAN_SCENE_UNKNOWN_PARTICIPANT"
        )
    )

    assert result is not None
    # Sub-code mining surfaces the specific PLAN_* violation in the slug
    # so the UI badge can show the actionable cause without unfolding
    # the long error blob.
    assert result[0] == "volume_outline_gate_failed:plan_scene_unknown_participant"


def test_generation_gate_block_classifies_chapter_plan_contract_without_subcode() -> None:
    """Fallback path when the contract error has no recognisable PLAN_ code."""

    result = worker_tasks._generation_gate_block(
        ValueError("chapter_plan_contract failed for project 'demo'")
    )

    assert result is not None
    assert result[0] == "volume_outline_gate_failed"


def test_generation_gate_block_classifies_plan_fingerprint_gate() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError(
            "Chapter outline batch blocked by plan fingerprint gate: "
            "1225 duplicate chapter pair(s) found."
        )
    )

    assert result is not None
    assert result[0] == "volume_outline_gate_failed:plan_fingerprint"


def test_generation_gate_block_classifies_l2_bible_motive_overlap() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError(
            "L2 bible gate failed for project 'demo'. "
            "1) [ANTAGONIST_MOTIVE_OVERLAP] characters:A,B"
        )
    )

    assert result is not None
    assert result[0] == "story_bible_gate_failed:antagonist_motive_overlap"


def test_generation_gate_block_classifies_write_safety_identity_violation() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError(
            "Scene novel 396.2 blocked by write-safety gate: "
            "[identity:dead_alive:critical] Elena Vasquez: expected dead, found alive"
        )
    )

    assert result is not None
    assert result[0] == "write_safety_gate_failed:identity_dead_alive"


def test_generation_gate_block_classifies_plan_richness_gate() -> None:
    result = worker_tasks._generation_gate_block(
        ValueError(
            "Scene 508.2 blocked by plan-richness gate: "
            "['interactive_needs_two']. Re-plan required (card too thin)."
        )
    )

    assert result is not None
    assert result[0] == "scene_plan_richness_gate_failed:interactive_needs_two"


def test_generation_gate_block_ignores_transient_errors() -> None:
    assert worker_tasks._generation_gate_block(ConnectionError("redis timeout")) is None


@pytest.mark.asyncio
async def test_run_project_repair_task_auto_continues_quality_closure_when_not_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []
    enqueued: list[dict[str, object]] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    class _FakeResult:
        requires_human_review = True
        workflow_run_id = uuid4()

        def model_dump(self, *, mode: str) -> dict:
            return {
                "project_slug": "novel",
                "requires_human_review": self.requires_human_review,
                "final_verdict": "attention",
            }

    class _FakeRedis:
        async def enqueue_job(self, function: str, **kwargs: object) -> object:
            enqueued.append({"function": function, **kwargs})
            return types.SimpleNamespace(job_id=kwargs.get("_job_id"))

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    captured_kwargs: dict[str, object] = {}

    async def fake_run_project_repair(*_args, **_kwargs):
        captured_kwargs.update(_kwargs)
        return _FakeResult()

    import bestseller.services.repair as repair_services

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)
    monkeypatch.setattr(repair_services, "run_project_repair", fake_run_project_repair)

    result = await worker_tasks.run_project_repair_task(
        {"redis": _FakeRedis()},
        "repair:heal:novel",
        {"project_slug": "novel"},
    )

    assert result == {
        "project_slug": "novel",
        "requires_human_review": True,
        "final_verdict": "attention",
    }
    assert captured_kwargs["include_pending_rewrite_tasks"] is True
    assert captured_kwargs["pending_rewrite_task_limit"] == 10
    assert enqueued[0]["function"] == "run_book_quality_closure_task"
    assert enqueued[0]["workflow_run_id"] == "quality-closure:heal:novel"
    assert events[-1][0] == "repairable_auto_continue"
    assert events[-1][2] == "repairable_auto_continue"
    assert events[-1][1]["source"] == "project_repair"


@pytest.mark.asyncio
async def test_run_project_repair_task_skips_archived_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []
    called = False

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    async def fake_archived(_project_slug: str) -> bool:
        return True

    async def fake_run_project_repair(*_args, **_kwargs):
        nonlocal called
        called = True

    import bestseller.services.repair as repair_services

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "_project_slug_is_archived", fake_archived)
    monkeypatch.setattr(repair_services, "run_project_repair", fake_run_project_repair)

    result = await worker_tasks.run_project_repair_task(
        {"redis": object()},
        "repair:heal:archived",
        {"project_slug": "archived"},
    )

    assert result == {
        "status": "skipped_archived",
        "project_slug": "archived",
        "reason": "library_archived",
    }
    assert called is False
    assert events == [
        (
            "skipped_archived",
            {
                "status": "skipped_archived",
                "project_slug": "archived",
                "reason": "library_archived",
            },
            "skipped_archived",
        )
    ]


@pytest.mark.asyncio
async def test_run_project_pipeline_task_marks_attention_as_auto_continue_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    class _FakeResult:
        requires_human_review = True
        workflow_run_id = uuid4()
        final_verdict = "attention"

        def model_dump(self, *, mode: str) -> dict:
            return {
                "requires_human_review": self.requires_human_review,
                "final_verdict": self.final_verdict,
            }

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_run_project_pipeline(*_args, **_kwargs):
        return _FakeResult()

    import bestseller.services.pipelines as pipeline_services

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)
    monkeypatch.setattr(
        pipeline_services,
        "run_project_pipeline",
        fake_run_project_pipeline,
    )

    result = await worker_tasks.run_project_pipeline_task(
        {"redis": object()},
        "project:heal:novel",
        {"project_slug": "novel"},
    )

    assert result == {"requires_human_review": True, "final_verdict": "attention"}
    assert events[-1][0] == "repairable_auto_continue_pending"
    assert events[-1][2] == "repairable_auto_continue_pending"
    assert events[-1][1]["reason"] == "project_pipeline_requires_attention"


@pytest.mark.asyncio
async def test_run_project_pipeline_task_refreshes_stale_truth_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []
    calls: list[str] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    class _FakeResult:
        requires_human_review = False
        workflow_run_id = uuid4()
        final_verdict = "pass"

        def model_dump(self, *, mode: str) -> dict:
            return {
                "project_slug": "novel",
                "requires_human_review": self.requires_human_review,
                "final_verdict": self.final_verdict,
            }

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    from bestseller.services import pipelines as pipeline_services
    from bestseller.services import projects as project_services
    from bestseller.services.truth_version import (
        TruthMaterializationStatus,
        TruthVersionStaleError,
    )

    async def fake_run_project_pipeline(*_args, **_kwargs):
        calls.append("run")
        if len(calls) == 1:
            raise TruthVersionStaleError(
                project_slug="novel",
                truth_version=8,
                stale_components=(
                    TruthMaterializationStatus(
                        component="story_bible",
                        workflow_type="materialize_story_bible",
                        status="stale",
                        required_truth_version=8,
                    ),
                ),
            )
        return _FakeResult()

    async def fake_get_project_by_slug(*_args, **_kwargs):
        return types.SimpleNamespace(slug="novel")

    async def fake_refresh(*_args, **_kwargs):
        calls.append("refresh")
        return True

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)
    monkeypatch.setattr(
        pipeline_services,
        "run_project_pipeline",
        fake_run_project_pipeline,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_refresh_stale_truth_materializations_for_resume",
        fake_refresh,
    )
    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)

    result = await worker_tasks.run_project_pipeline_task(
        {"redis": object()},
        "project-pipeline:heal:novel",
        {"project_slug": "novel"},
    )

    assert result["final_verdict"] == "pass"
    assert calls == ["run", "refresh", "run"]
    assert events[-1][0] == "completed"


@pytest.mark.asyncio
async def test_run_autowrite_task_marks_attention_as_auto_continue_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    class _FakeResult:
        requires_human_review = True
        workflow_run_id = uuid4()
        final_verdict = "attention"

        def model_dump(self, *, mode: str) -> dict:
            return {
                "requires_human_review": self.requires_human_review,
                "final_verdict": self.final_verdict,
                "chapter_count": 2,
            }

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_get_project_by_slug(*_args, **_kwargs):
        return types.SimpleNamespace(
            slug="novel",
            title="Novel",
            genre="sci-fi",
            sub_genre=None,
            audience=None,
            target_word_count=12000,
            target_chapters=4,
            project_type="linear",
            metadata_json={"premise": "demo"},
        )

    async def fake_run_autowrite_pipeline(*_args, **_kwargs):
        return _FakeResult()

    import bestseller.services.pipelines as pipeline_services
    import bestseller.services.projects as project_services

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)
    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services,
        "run_autowrite_pipeline",
        fake_run_autowrite_pipeline,
    )

    result = await worker_tasks.run_autowrite_task(
        {"redis": object()},
        "autowrite:heal:novel",
        {"project_slug": "novel"},
    )

    assert result == {
        "requires_human_review": True,
        "final_verdict": "attention",
        "chapter_count": 2,
    }
    assert events[-1][0] == "repairable_auto_continue_pending"
    assert events[-1][2] == "repairable_auto_continue_pending"
    assert events[-1][1]["reason"] == "autowrite_requires_attention"


@pytest.mark.parametrize(
    "message",
    [
        "Whole-book outline semantic gate failed; prose promotion is blocked "
        "until replan. issues=OUTLINE_IDENTITY_MISMATCH",
        "Whole-book outline semantic gate rejected promotion; replan is "
        "required. [OUTLINE_INFORMATION_CONTRACT_GAP]",
    ],
)
def test_generation_gate_block_routes_whole_book_semantic_failures_to_replan(
    message: str,
) -> None:
    reason, returned_message = worker_tasks._generation_gate_block(
        RuntimeError(message)
    ) or ("", "")

    assert reason.startswith("outline_semantic_gate_failed")
    assert returned_message == message


def test_generation_gate_block_does_not_replan_for_transient_judge_outage() -> None:
    error = RuntimeError(
        "Commercial planning readiness gate failed: "
        "COMMERCIAL_LLM_JUDGE_UNAVAILABLE [LLM judge]"
    )

    assert worker_tasks._generation_gate_block(error) is None


def test_not_exported_result_requires_autonomous_attention_even_when_review_passed() -> None:
    assert worker_tasks._result_payload_requires_attention(
        {
            "final_verdict": "pass",
            "export_status": "not_exported",
            "requires_human_review": False,
        }
    ) is True


@pytest.mark.asyncio
async def test_run_autowrite_self_heal_skips_framework_owned_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_get_project_by_slug(*_args, **_kwargs):
        return types.SimpleNamespace(
            metadata_json={
                "self_heal_suppressed": True,
                "self_heal_suppressed_reason": "chapter_first_framework_owned",
            }
        )

    import bestseller.services.projects as project_services

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)
    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)

    result = await worker_tasks.run_autowrite_task(
        {"redis": object()},
        "autowrite:heal:owned-book",
        {"project_slug": "owned-book"},
    )

    assert result["status"] == "skipped_framework_owned"
    assert events[-1][0] == "framework_owned_self_heal_skipped"
    assert events[-1][2] == "completed"


@pytest.mark.asyncio
async def test_run_autowrite_skips_project_that_requires_outline_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    project = types.SimpleNamespace(
        slug="bad-outline",
        status="needs_replan",
        metadata_json={"production_pause_reason": "outline_semantic_gate_failed"},
    )

    class _FakeSession:
        async def scalar(self, _stmt: object) -> object:
            return project

    @asynccontextmanager
    async def fake_session_scope():
        yield _FakeSession()

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)

    result = await worker_tasks.run_autowrite_task(
        {"redis": object()},
        "autowrite:heal:bad-outline",
        {"project_slug": "bad-outline"},
    )

    assert result["status"] == "skipped_outline_replan"
    assert result["reason"] == "outline_semantic_gate_failed"
    assert result["requires_machine_repair"] is True
    assert events[-1][0] == "repairable_auto_continue_pending"
    assert events[-1][2] == "repairable_auto_continue_pending"


@pytest.mark.asyncio
async def test_run_project_repair_task_auto_continues_generation_gate_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []
    marked: list[tuple[str, str]] = []
    enqueued: list[dict[str, object]] = []

    class _FakeReporter:
        async def emit(
            self,
            message: str,
            data: dict,
            event_type: str | None = None,
        ) -> None:
            events.append((message, data, event_type))

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_run_project_repair(*_args, **_kwargs):
        raise ValueError(
            "chapter_plan_contract failed for project 'novel' while validating "
            "chapter_outline_batch: PLAN_SCENE_UNKNOWN_PARTICIPANT"
        )

    async def fake_mark(project_slug: str, *, reason: str, error_message: str) -> None:
        marked.append((project_slug, reason))

    class _FakeRedis:
        async def enqueue_job(self, function: str, **kwargs: object) -> object:
            enqueued.append({"function": function, **kwargs})
            return types.SimpleNamespace(job_id=kwargs.get("_job_id"))

    import bestseller.services.repair as repair_services

    monkeypatch.setattr(
        worker_tasks,
        "RedisProgressReporter",
        lambda *_args, **_kwargs: _FakeReporter(),
    )
    monkeypatch.setattr(worker_tasks, "make_sync_callback", lambda _reporter: None)
    monkeypatch.setattr(worker_tasks, "get_server_session", fake_session_scope)
    monkeypatch.setattr(
        worker_tasks,
        "_mark_project_generation_repair_exhausted",
        fake_mark,
    )
    monkeypatch.setattr(repair_services, "run_project_repair", fake_run_project_repair)

    result = await worker_tasks.run_project_repair_task(
        {"redis": _FakeRedis()},
        "repair:heal:novel",
        {"project_slug": "novel"},
    )

    expected_reason = "volume_outline_gate_failed:plan_scene_unknown_participant"
    assert result == {
        "status": "generation_gate_auto_retry_pending",
        "project_slug": "novel",
        "reason": expected_reason,
        "repair_queued": False,
    }
    assert marked == [("novel", expected_reason)]
    assert enqueued == []
    assert events[-2][0] == "repairable_auto_continue_deferred"
    assert events[-2][2] == "repairable_auto_continue_pending"
    assert events[-1][0] == "repairable_auto_continue_pending"
    assert events[-1][2] == "repairable_auto_continue_pending"


class _FakeDeadlockOrig(Exception):
    """Mimics asyncpg's DeadlockDetectedError (carries a .sqlstate)."""

    sqlstate = "40P01"


def _make_deadlock_error() -> Exception:
    from sqlalchemy.exc import DBAPIError

    return DBAPIError("stmt", {}, _FakeDeadlockOrig("deadlock detected"))


def _make_other_dbapi_error() -> Exception:
    from sqlalchemy.exc import DBAPIError

    class _OtherOrig(Exception):
        sqlstate = "23505"  # unique_violation, NOT a deadlock

    return DBAPIError("stmt", {}, _OtherOrig("dup key"))


def test_is_postgres_deadlock_detects_40p01() -> None:
    assert worker_tasks._is_postgres_deadlock(_make_deadlock_error()) is True
    assert worker_tasks._is_postgres_deadlock(_make_other_dbapi_error()) is False
    assert worker_tasks._is_postgres_deadlock(ValueError("nope")) is False


@pytest.mark.asyncio
async def test_deadlock_retry_recovers_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(worker_tasks.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    async def _op() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _make_deadlock_error()
        return "ok"

    result = await worker_tasks._run_with_deadlock_retry(_op, description="test op")
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_deadlock_retry_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(worker_tasks.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    async def _op() -> str:
        calls["n"] += 1
        raise _make_deadlock_error()

    with pytest.raises(Exception):  # noqa: B017 — DBAPIError re-raised after exhaustion
        await worker_tasks._run_with_deadlock_retry(_op, description="test op", max_attempts=3)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_deadlock_retry_does_not_retry_non_deadlock_error() -> None:
    calls = {"n": 0}

    async def _op() -> str:
        calls["n"] += 1
        raise _make_other_dbapi_error()

    with pytest.raises(Exception):  # noqa: B017
        await worker_tasks._run_with_deadlock_retry(_op, description="test op")
    assert calls["n"] == 1  # non-deadlock errors surface immediately, no retry

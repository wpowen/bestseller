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


def test_generation_gate_block_ignores_transient_errors() -> None:
    assert worker_tasks._generation_gate_block(ConnectionError("redis timeout")) is None


@pytest.mark.asyncio
async def test_run_project_repair_task_emits_waiting_human_when_repair_not_closed(
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

        def model_dump(self, *, mode: str) -> dict:
            return {"requires_human_review": self.requires_human_review}

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_run_project_repair(*_args, **_kwargs):
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
        {"redis": object()},
        "repair:heal:novel",
        {"project_slug": "novel"},
    )

    assert result == {"requires_human_review": True}
    assert events[-1][0] == "waiting_human"
    assert events[-1][2] == "waiting_human"
    assert events[-1][1]["reason"] == "project_repair_requires_attention"


@pytest.mark.asyncio
async def test_run_project_repair_task_blocks_generation_gate_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict, str | None]] = []
    marked: list[tuple[str, str]] = []

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
        {"redis": object()},
        "repair:heal:novel",
        {"project_slug": "novel"},
    )

    expected_reason = "volume_outline_gate_failed:plan_scene_unknown_participant"
    assert result == {
        "status": "blocked_generation_gate",
        "project_slug": "novel",
        "reason": expected_reason,
    }
    assert marked == [("novel", expected_reason)]
    assert events[-1][0] == "blocked_generation_gate"
    assert events[-1][2] == "blocked_generation_gate"

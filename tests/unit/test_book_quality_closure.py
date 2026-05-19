from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.book_quality_closure import (
    LLMPreflightReport,
    audit_chapter_generation_modes,
    audit_rewrite_task_generation_modes,
    build_legacy_scene_story_purpose,
    determine_next_action,
    filter_fleet_slugs,
    fleet_row_from_acceptance,
    is_real_llm_provider,
    load_pending_autonomous_repair_task_ids,
    run_llm_execution_preflight,
)
from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult


def test_real_llm_provider_rejects_mock_and_fallback_modes() -> None:
    assert is_real_llm_provider("openai", finish_reason="stop")
    assert is_real_llm_provider("anthropic", finish_reason="stop")
    assert not is_real_llm_provider("mock", finish_reason="stop")
    assert not is_real_llm_provider("fallback", finish_reason="stop")
    assert not is_real_llm_provider("openai", finish_reason="fallback")
    assert not is_real_llm_provider("", finish_reason="stop")


@pytest.mark.asyncio
async def test_llm_preflight_allows_real_provider() -> None:
    async def fake_complete(
        session: object,
        settings: object,
        request: LLMCompletionRequest,
    ) -> LLMCompletionResult:
        assert request.fallback_response == "FALLBACK_PRECHECK"
        assert request.max_tokens_override == 256
        return LLMCompletionResult(
            content="OK",
            provider="openai",
            model_name="gpt-test",
            finish_reason="stop",
        )

    report = await run_llm_execution_preflight(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        complete_text_fn=fake_complete,  # type: ignore[arg-type]
    )

    assert report.ready is True
    assert report.provider == "openai"
    assert report.reason is None


@pytest.mark.asyncio
async def test_llm_preflight_blocks_fallback_provider() -> None:
    async def fake_complete(
        session: object,
        settings: object,
        request: LLMCompletionRequest,
    ) -> LLMCompletionResult:
        return LLMCompletionResult(
            content=str(request.fallback_response),
            provider="fallback",
            model_name="fallback",
            finish_reason="fallback",
        )

    report = await run_llm_execution_preflight(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        complete_text_fn=fake_complete,  # type: ignore[arg-type]
    )

    assert report.ready is False
    assert report.reason == "provider_returned_mock_or_fallback"


@pytest.mark.asyncio
async def test_llm_preflight_times_out_stuck_provider_call() -> None:
    async def fake_complete(
        session: object,
        settings: object,
        request: LLMCompletionRequest,
    ) -> LLMCompletionResult:
        await asyncio.sleep(1)
        return LLMCompletionResult(
            content="OK",
            provider="openai",
            model_name="gpt-test",
            finish_reason="stop",
        )

    report = await run_llm_execution_preflight(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        timeout_seconds=0.01,
        complete_text_fn=fake_complete,  # type: ignore[arg-type]
    )

    assert report.ready is False
    assert report.reason == "provider_call_timeout"


def test_filter_fleet_slugs_excludes_verify_and_test_outputs_by_default() -> None:
    slugs = [
        "exorcist-detective-1778051012",
        "verify-premium-gate",
        "book-test-output",
        "romantasy-1776330993",
    ]

    assert filter_fleet_slugs(slugs) == [
        "exorcist-detective-1778051012",
        "romantasy-1776330993",
    ]
    assert filter_fleet_slugs(slugs, include_verify=True) == slugs


def test_legacy_scene_story_purpose_enriches_thin_scene_anchor() -> None:
    purpose = build_legacy_scene_story_purpose(
        scene_anchor="发现第一具尸体",
        chapter_goal="镜中局开始后的第一个夜晚，一名参与者死亡",
        main_conflict="林渊必须判断这是模仿杀人还是真正的镜灵作祟",
        participants=["林渊", "苏婉宁"],
    )

    assert "发现第一具尸体" in purpose
    assert "镜中局开始后的第一个夜晚" in purpose
    assert "林渊、苏婉宁" in purpose
    assert len(purpose) > 40


def test_determine_next_action_routes_repair_and_continuation_states() -> None:
    ready_model = LLMPreflightReport(ready=True, provider="openai")
    acceptance = {
        "acceptance": {
            "passed": False,
            "metrics": {
                "quality_score": 82,
                "chapters_blocked": 0,
                "missing_chapters": 4,
            },
        }
    }

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 3},
        model_preflight=ready_model,
        execute_requested=True,
    ) == ("repairing", "execute_next_repair_round")

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 0},
        model_preflight=ready_model,
        execute_requested=True,
    ) == ("continuing", "generate_missing_chapters_under_state_gates")


def test_determine_next_action_treats_medium_backlog_as_nonblocking() -> None:
    ready_model = LLMPreflightReport(ready=True, provider="deepseek")
    acceptance = {
        "acceptance": {
            "passed": False,
            "metrics": {
                "quality_score": 61,
                "chapters_blocked": 0,
                "missing_chapters": 400,
            },
        }
    }

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 15, "priority_counts": {"medium": 15}},
        model_preflight=ready_model,
        execute_requested=True,
    ) == ("continuing", "generate_missing_chapters_under_state_gates")


def test_determine_next_action_continues_for_draftless_planned_chapters() -> None:
    ready_model = LLMPreflightReport(ready=True, provider="deepseek")
    acceptance = {
        "acceptance": {
            "passed": False,
            "metrics": {
                "quality_score": 85,
                "chapters_blocked": 0,
                "missing_chapters": 0,
                "draftless_chapters": 43,
            },
        }
    }

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 0},
        model_preflight=ready_model,
        execute_requested=True,
    ) == ("continuing", "generate_missing_chapters_under_state_gates")


def test_determine_next_action_blocks_when_execution_would_use_fallback() -> None:
    blocked_model = LLMPreflightReport(ready=False, reason="provider_returned_mock_or_fallback")
    acceptance = {
        "acceptance": {
            "passed": False,
            "metrics": {
                "quality_score": 70,
                "chapters_blocked": 2,
                "missing_chapters": 10,
            },
        }
    }

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 5},
        model_preflight=blocked_model,
        execute_requested=True,
    ) == ("blocked", "fix_llm_provider_preflight")

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 0},
        model_preflight=blocked_model,
        execute_requested=True,
        invalid_generation_count=1,
    ) == ("blocked", "inspect_fallback_or_invalid_rewrites")


def test_determine_next_action_rejects_invalid_generation_before_ready() -> None:
    ready_model = LLMPreflightReport(ready=True, provider="deepseek")
    acceptance = {
        "acceptance": {
            "passed": True,
            "metrics": {
                "quality_score": 90,
                "chapters_blocked": 0,
                "missing_chapters": 0,
                "draftless_chapters": 0,
            },
        }
    }

    assert determine_next_action(
        acceptance=acceptance,
        repair_plan={"task_count": 0},
        model_preflight=ready_model,
        execute_requested=True,
        invalid_generation_count=1,
    ) == ("blocked", "inspect_fallback_or_invalid_rewrites")


def test_fleet_row_from_acceptance_keeps_closure_metrics() -> None:
    row = fleet_row_from_acceptance(
        slug="book-a",
        status="repairing",
        next_action="execute_next_repair_round",
        acceptance_payload={
            "category": "suspense-mystery",
            "target_chapters": 500,
            "current_chapters": 100,
            "acceptance": {"readiness_level": "repairable"},
            "scorecard": {
                "quality_score": 54.9,
                "chapters_blocked": 7,
                "missing_chapters": 400,
            },
            "repair_plan": {"task_count": 40},
        },
    )

    assert row.to_dict() == {
        "slug": "book-a",
        "category": "suspense-mystery",
        "target_chapters": 500,
        "current_chapters": 100,
        "quality_score": 54.9,
        "blocked_chapters": 7,
        "repair_tasks": 40,
        "missing_chapters": 400,
        "acceptance_status": "repairable",
        "status": "repairing",
        "next_action": "execute_next_repair_round",
        "error": None,
    }


@pytest.mark.asyncio
async def test_rewrite_generation_audit_rejects_missing_or_fallback_modes() -> None:
    valid_id = uuid4()
    invalid_id = uuid4()
    tasks = [
        SimpleNamespace(
            id=valid_id,
            status="completed",
            metadata_json={"generation_mode": "openai"},
        ),
        SimpleNamespace(
            id=invalid_id,
            status="completed",
            metadata_json={"generation_mode": "fallback"},
        ),
    ]

    class _Result:
        def scalars(self) -> list[object]:
            return tasks

    class _Session:
        async def execute(self, statement: object) -> _Result:
            return _Result()

    audit = await audit_rewrite_task_generation_modes(
        _Session(),  # type: ignore[arg-type]
        [str(valid_id), str(invalid_id)],
    )

    assert audit.checked == 2
    assert audit.valid == 1
    assert audit.invalid == 1
    assert audit.gate_rejected == 0
    assert audit.invalid_task_ids == (str(invalid_id),)
    assert audit.invalid_generation_modes == ("fallback",)


@pytest.mark.asyncio
async def test_rewrite_generation_audit_separates_gate_rejected_real_provider() -> None:
    rejected_id = uuid4()
    tasks = [
        SimpleNamespace(
            id=rejected_id,
            status="failed",
            metadata_json={"generation_mode": "deepseek"},
        ),
    ]

    class _Result:
        def scalars(self) -> list[object]:
            return tasks

    class _Session:
        async def execute(self, statement: object) -> _Result:
            return _Result()

    audit = await audit_rewrite_task_generation_modes(
        _Session(),  # type: ignore[arg-type]
        [str(rejected_id)],
    )

    assert audit.checked == 1
    assert audit.valid == 0
    assert audit.invalid == 0
    assert audit.gate_rejected == 1
    assert audit.gate_rejected_task_ids == (str(rejected_id),)


@pytest.mark.asyncio
async def test_rewrite_generation_audit_treats_timeout_as_retryable_rejection() -> None:
    timeout_id = uuid4()
    tasks = [
        SimpleNamespace(
            id=timeout_id,
            status="failed",
            error_log="TimeoutError: rewrite task exceeded 240.0s",
            metadata_json={
                "closure_execution_error": "TimeoutError: rewrite task exceeded 240.0s",
            },
        ),
    ]

    class _Result:
        def scalars(self) -> list[object]:
            return tasks

    class _Session:
        async def execute(self, statement: object) -> _Result:
            return _Result()

    audit = await audit_rewrite_task_generation_modes(
        _Session(),  # type: ignore[arg-type]
        [str(timeout_id)],
    )

    assert audit.checked == 1
    assert audit.valid == 0
    assert audit.invalid == 0
    assert audit.gate_rejected == 1
    assert audit.gate_rejected_task_ids == (str(timeout_id),)


@pytest.mark.asyncio
async def test_chapter_generation_audit_rejects_missing_llm_provider() -> None:
    rows = [
        (58, "complete", "ok", "deepseek", "stop"),
        (59, "complete", "ok", "fallback", "fallback"),
        (60, "complete", "ok", None, None),
        (62, "revision", "blocked", None, None),
    ]

    class _Result:
        def all(self) -> list[object]:
            return rows

    class _Session:
        async def execute(self, statement: object) -> _Result:
            return _Result()

    audit = await audit_chapter_generation_modes(
        _Session(),  # type: ignore[arg-type]
        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        [58, 59, 60, 61, 62],
    )

    assert audit.checked == 4
    assert audit.valid == 1
    assert audit.invalid == 3
    assert audit.gate_rejected == 1
    assert audit.invalid_chapter_numbers == (59, 60, 61)
    assert audit.invalid_generation_modes == (
        "fallback",
        "missing_llm_run",
        "chapter_missing",
    )
    assert audit.gate_rejected_chapter_numbers == (62,)


@pytest.mark.asyncio
async def test_load_pending_tasks_builds_order_when_no_fresh_quality_blocks() -> None:
    class _Result:
        def scalars(self) -> list[object]:
            return []

    class _Session:
        def __init__(self) -> None:
            self.statements: list[object] = []

        async def execute(self, statement: object) -> _Result:
            self.statements.append(statement)
            return _Result()

    session = _Session()
    project = SimpleNamespace(id=uuid4())

    task_ids = await load_pending_autonomous_repair_task_ids(
        session,  # type: ignore[arg-type]
        project,  # type: ignore[arg-type]
        limit=10,
    )

    assert task_ids == []
    assert len(session.statements) == 2

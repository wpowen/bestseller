from __future__ import annotations

import json

import pytest

from bestseller.services import volume_quality_judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_volume_quality_judge_blocks_low_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.84,
                    "dimension_scores": {"volume_alignment": 0.76},
                    "blocking_issues": [],
                    "rewrite_plan": {"scope": "volume", "instructions": "拉回卷目标。"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(volume_quality_judge, "complete_text", fake_complete_text)

    result = await volume_quality_judge.judge_volume_quality_checkpoint(
        FakeSession(),
        load_settings(env={}),
        volume_plan={"goal": "查清镜债第一环"},
        chapter_summaries=[{"chapter_number": 10, "summary": "支线跑偏"}],
    )

    assert result.passed is False
    assert result.dimension_scores["volume_alignment"] == 0.76


@pytest.mark.asyncio
async def test_volume_quality_judge_passes_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.86,
                    "dimension_scores": {"volume_alignment": 0.84},
                    "blocking_issues": [],
                    "rewrite_plan": {"scope": "volume"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(volume_quality_judge, "complete_text", fake_complete_text)

    result = await volume_quality_judge.judge_volume_quality_checkpoint(
        FakeSession(),
        load_settings(env={}),
        volume_plan={"goal": "查清镜债第一环"},
        chapter_summaries=[{"chapter_number": 10, "summary": "林渊锁定镜债源头"}],
    )

    assert result.passed is True


@pytest.mark.asyncio
async def test_volume_quality_judge_maps_model_subdimensions_to_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.87,
                    "dimension_scores": {
                        "volume_goal_service": 0.90,
                        "obstacle_progression": 0.88,
                        "climax_setup": 0.85,
                        "resolution_foreshadowing": 0.83,
                        "revelation_budget": 0.85,
                    },
                    "blocking_issues": [],
                    "rewrite_plan": {"scope": "volume"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(volume_quality_judge, "complete_text", fake_complete_text)

    result = await volume_quality_judge.judge_volume_quality_checkpoint(
        FakeSession(),
        load_settings(env={}),
        volume_plan={"goal": "查清镜债第一环"},
        chapter_summaries=[{"chapter_number": 1, "summary": "林渊建立镜债规则"}],
    )

    assert result.passed is True
    assert result.dimension_scores["volume_alignment"] == 0.83


@pytest.mark.asyncio
async def test_volume_quality_judge_blocks_when_any_subdimension_is_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.81,
                    "dimension_scores": {
                        "goal_service": 0.85,
                        "obstacle_efficacy": 0.85,
                        "climax_delivery": 0.80,
                        "resolution_utility": 0.75,
                        "budget_revelation": 0.75,
                    },
                    "blocking_issues": [],
                    # Actionable rewrite plan: lets the framework override
                    # the LLM's pass verdict when numeric thresholds fail.
                    # When the LLM gives no actionable feedback we no
                    # longer fabricate blockers — see silent-judge
                    # regression on 青囊不语问阴阳 ch1, 2026-05-25.
                    "rewrite_plan": {
                        "scope": "volume",
                        "change": ["补足卷尾揭示预算"],
                        "instructions": "在最后两章把 reveal_budget 补齐。",
                    },
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(volume_quality_judge, "complete_text", fake_complete_text)

    result = await volume_quality_judge.judge_volume_quality_checkpoint(
        FakeSession(),
        load_settings(env={}),
        volume_plan={"goal": "查清镜债第一环"},
        chapter_summaries=[{"chapter_number": 1, "summary": "林渊建立镜债规则"}],
    )

    assert result.passed is False
    assert result.dimension_scores["volume_alignment"] == 0.75


@pytest.mark.asyncio
async def test_volume_quality_judge_prompt_scopes_future_reveals_to_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_prompt: dict[str, str] = {}

    async def fake_complete_text(session, settings, request):
        captured_prompt["user"] = request.user_prompt
        captured_prompt["system"] = request.system_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.86,
                    "dimension_scores": {"volume_alignment": 0.84},
                    "blocking_issues": [],
                    "rewrite_plan": {"scope": "volume"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(volume_quality_judge, "complete_text", fake_complete_text)

    await volume_quality_judge.judge_volume_quality_checkpoint(
        FakeSession(),
        load_settings(env={}),
        volume_plan={"goal": "完成第一卷镜债入局", "future_reveal": "第30章揭示父亲旧债"},
        chapter_summaries=[{"chapter_number": 10, "summary": "林渊建立镜债规则"}],
        current_chapter_number=10,
        volume_checkpoint_interval=10,
        volume_checkpoint_min_chapters=10,
    )

    # The "scope to current stage" constraints live in the system prompt; the chapter
    # context (current chapter number, future-plan-as-direction-only) is in the user
    # prompt. Check across both so prompt-section refactors don't break the contract.
    _judge_prompt = captured_prompt["system"] + "\n" + captured_prompt["user"]
    assert "不得要求提前兑现未来章节" in _judge_prompt
    assert "当前章节号：10" in _judge_prompt
    assert "未来章节计划只能作为方向校验" in _judge_prompt

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.services import outline_llm_judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.prompt_packs import get_prompt_pack
from bestseller.settings import load_settings


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_outline_llm_judge_enforces_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_prompts = {}

    async def fake_complete_text(session, settings, request):
        captured_prompts["user"] = request.user_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.79,
                    "dimension_scores": {
                        "commercial_pull": 0.78,
                        "methodology_compliance": 0.84,
                    },
                    "blocking_issues": [],
                    "rewrite_plan": {
                        "scope": "outline",
                        "change": ["开篇压力"],
                        "instructions": "重做第一章开场压力。",
                    },
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", fake_complete_text)

    result = await outline_llm_judge.judge_outline_commercial_readiness(
        FakeSession(),
        load_settings(env={}),
        outline_payload={"chapters": []},
        threshold=0.82,
    )

    assert result.passed is False
    assert result.overall_score == 0.79
    assert result.rewrite_plan.instructions == "重做第一章开场压力。"
    assert "knowledge_boundary" in captured_prompts["user"]
    assert "非专业角色不得天然理解专业规则" in captured_prompts["user"]
    assert "铜钱/罗盘/青囊等物件信号必须有稳定含义" in captured_prompts["user"]


@pytest.mark.asyncio
async def test_outline_llm_judge_passes_clean_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.88,
                    "dimension_scores": {
                        "commercial_pull": 0.87,
                        "methodology_compliance": 0.86,
                    },
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {"scope": "outline"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
            llm_run_id=None,
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", fake_complete_text)

    result = await outline_llm_judge.judge_outline_commercial_readiness(
        FakeSession(),
        load_settings(env={}),
        outline_payload={"chapters": [{"chapter_number": 1}]},
        threshold=0.82,
    )

    assert result.passed is True
    assert result.has_critical is False


@pytest.mark.asyncio
async def test_outline_llm_judge_parses_fenced_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=(
                "```json\n"
                + json.dumps(
                    {
                        "pass": True,
                        "overall_score": 0.84,
                        "dimension_scores": {
                            "commercial_pull": 0.83,
                            "methodology_compliance": 0.82,
                        },
                        "blocking_issues": [],
                        "audit_issues": [],
                        "rewrite_plan": {"scope": "outline"},
                    },
                    ensure_ascii=False,
                )
                + "\n```"
            ),
            provider="mock",
            model_name="mock-critic",
            llm_run_id=None,
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", fake_complete_text)

    result = await outline_llm_judge.judge_outline_commercial_readiness(
        FakeSession(),
        load_settings(env={}),
        outline_payload={"chapters": [{"chapter_number": 1}]},
        threshold=0.82,
    )

    assert result.passed is True
    assert result.overall_score == 0.84


def test_quality_judge_accepts_ten_point_scores_and_type_aliases() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": True,
            "overall_score": 7.4,
            "dimension_scores": {"commercial_pull": 8.0, "methodology_compliance": 7.8},
            "blocking_issues": [
                {
                    "type": "character_arc_static",
                    "severity": "high",
                    "evidence": "主角选择不足。",
                    "suggestion": "补充主角付代价的决定时刻。",
                }
            ],
            "audit_issues": [{"type": "hook_type_repetition"}],
            "rewrite_plan": [
                {"priority": "high", "action": "补强前三章主角可见选择"},
                "降低重复 hook 类型",
            ],
        },
        scope="outline",
        min_overall=0.82,
        min_dimensions={"commercial_pull": 0.80},
    )

    assert result.overall_score == 0.74
    assert result.dimension_scores["commercial_pull"] == 0.8
    assert result.blocking_issues[0].code == "character_arc_static"
    assert result.blocking_issues[0].required_fix == "补充主角付代价的决定时刻。"
    assert result.rewrite_plan.change == ("补强前三章主角可见选择", "降低重复 hook 类型")
    assert result.passed is False


def test_quality_judge_maps_blocking_severity_to_critical() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": False,
            "overall_score": 0.9,
            "dimension_scores": {"commercial_pull": 0.9},
            "blocking_issues": [
                {
                    "code": "KNOWLEDGE_BOUNDARY_LEAK",
                    "severity": "blocking",
                    "evidence": "张建军主动解释入账。",
                    "required_fix": "改成林渊判断，张建军只描述症状。",
                }
            ],
            "rewrite_plan": {"scope": "outline", "change": ["修复角色认知边界"]},
        },
        scope="outline",
        min_overall=0.82,
        min_dimensions={"commercial_pull": 0.80},
    )

    assert result.blocking_issues[0].severity == "critical"
    assert result.has_critical is True


def test_quality_judge_synthesizes_threshold_issue_when_model_omits_blockers() -> None:
    # When the judge omits blocking_issues but its rewrite_plan carries
    # actionable instructions, we still synthesise threshold-based blockers
    # so the gate can route the chapter into the repair loop.
    result = quality_judge_result_from_mapping(
        {
            "pass": False,
            "overall_score": 0.81,
            "dimension_scores": {"commercial_pull": 0.79},
            "blocking_issues": [],
            "audit_issues": [],
            "rewrite_plan": {
                "scope": "outline",
                "change": ["补强前三章商业拉力"],
                "instructions": "重做开场冲突。",
            },
        },
        scope="outline",
        min_overall=0.82,
        min_dimensions={"commercial_pull": 0.80},
    )

    assert result.passed is False
    assert [issue.code for issue in result.blocking_issues] == [
        "LLM_SCORE_BELOW_THRESHOLD",
        "LLM_DIMENSION_BELOW_THRESHOLD_COMMERCIAL_PULL",
    ]
    # Synthesised severity is capped at ``high`` so downstream merge logic
    # routes to ``severity_max=major`` and not ``critical`` — keeps
    # numeric-only misses from escalating into critical repair loops.
    assert all(issue.severity == "high" for issue in result.blocking_issues)


def test_quality_judge_does_not_fabricate_blockers_when_judge_is_silent() -> None:
    """Regression: 青囊不语问阴阳 ch1 looped 124 times because the volume
    judge returned ``pass=true`` with a borderline score and an empty
    rewrite plan, but the framework still fabricated a critical
    ``LLM_SCORE_BELOW_THRESHOLD`` blocker. With no actionable feedback,
    we should now leave the verdict alone instead of spinning."""

    result = quality_judge_result_from_mapping(
        {
            "pass": True,
            "overall_score": 0.76,
            "dimension_scores": {"volume_alignment": 0.76},
            "blocking_issues": [],
            "audit_issues": [],
            "rewrite_plan": {"scope": "volume"},
        },
        scope="volume",
        min_overall=0.80,
        min_dimensions={"volume_alignment": 0.80},
    )

    # No actionable feedback → no fabricated blockers. The LLM's pass
    # verdict stands. The chapter pipeline can still surface a low score
    # via dimension_scores for human inspection.
    assert result.blocking_issues == ()
    assert result.passed is True
    assert result.overall_score == 0.76


def test_compact_outline_payload_preserves_all_chapter_scene_cards() -> None:
    payload = {
        "batch_name": "front10",
        "chapters": [
            {
                "chapter_number": index,
                "title": f"第{index}章",
                "chapter_goal": "目标" * 100,
                "causal_contract": {"visible_action_or_reaction": "主角做出可见动作"},
                "redundant": "冗余" * 500,
                "scenes": [
                    {
                        "scene_number": scene_index,
                        "title": f"场景{scene_index}",
                        "purpose": {"story": "故事任务"},
                        "entry_state": {"state": "进入"},
                        "exit_state": {"state": "退出"},
                        "methodology_contract": {
                            "conflict_stakes": "赌注",
                            "signature_image": "标志画面",
                            "cut_point": "断点",
                        },
                        "redundant": "冗余" * 500,
                    }
                    for scene_index in range(1, 5)
                ],
            }
            for index in range(1, 11)
        ],
    }

    compact = outline_llm_judge._compact_outline_payload(payload)

    assert len(compact["chapters"]) == 10
    assert len(compact["chapters"][-1]["scenes"]) == 4
    assert "redundant" not in compact["chapters"][0]
    assert "redundant" not in compact["chapters"][0]["scenes"][0]


@pytest.mark.asyncio
async def test_outline_judge_prompt_includes_methodology_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session, settings, request):
        captured["system"] = request.system_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.88,
                    "dimension_scores": {
                        "commercial_pull": 0.88,
                        "opening_pull": 0.88,
                        "methodology_compliance": 0.88,
                    },
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {"scope": "outline"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", fake_complete_text)

    await outline_llm_judge.judge_outline_commercial_readiness(
        FakeSession(),
        load_settings(env={}),
        outline_payload={"chapters": [{"chapter_number": 1}]},
        pack=get_prompt_pack("suspense-mystery"),
    )

    assert "评估时必须参照的方法论标准" in captured["system"]
    assert "【opening_rules】" in captured["system"]
    assert "【spring_model】" in captured["system"]


@pytest.mark.asyncio
async def test_commercial_planning_judge_prompt_includes_methodology_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session, settings, request):
        captured["system"] = request.system_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.80,
                    "dimension_scores": {"commercial_retention": 0.80},
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {"scope": "commercial_planning"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", fake_complete_text)

    await outline_llm_judge.judge_commercial_planning_readiness(
        FakeSession(),
        load_settings(env={}),
        chapters_payload=[{"chapter_number": 1, "chapter_goal": "林渊接到委托"}],
        pack=get_prompt_pack("suspense-mystery"),
    )

    assert "评估时必须参照的方法论标准" in captured["system"]
    assert "【character_design】" in captured["system"]

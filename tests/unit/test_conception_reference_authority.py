"""Reference advice must stay below the selected story in composed prompts."""
import ast
from pathlib import Path

import pytest

from bestseller.services import conception
from bestseller.services.degradation_tracker import DegradationTracker
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_reference_block_preserves_advice_and_declares_its_authority(language):
    ctx = {"language": language, "concept_methodology_block": "REFERENCE_SENTINEL"}
    block = conception._concept_methodology_prompt_block(ctx)
    assert '<creative_reference authority="advisory">' in block
    assert "REFERENCE_SENTINEL" in block
    assert block.count("REFERENCE_SENTINEL") == 1
    assert "</creative_reference>" in block
    if language.startswith("en"):
        assert "not story facts" in block
        assert "explicit user" in block
    else:
        assert "不是本书事实" in block
        assert "用户明确" in block


def test_disabled_reference_has_no_added_obligations():
    assert conception._concept_methodology_prompt_block({}) == ""


def test_production_does_not_pay_for_unconsumed_post_selection_exploration():
    tree = ast.parse(Path(conception.__file__).read_text())
    pipeline = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "run_conception_pipeline")
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "_creative_exploration" for n in ast.walk(pipeline))


@pytest.mark.parametrize("system", ["_REVIEW_SYSTEM", "_FINALIZE_SYSTEM"])
def test_editor_system_cannot_require_reference_world_facts(system):
    prompt = getattr(conception, system)
    assert "参考" in prompt and "不是" in prompt
    assert "用户" in prompt


def test_required_editorial_stage_rejects_static_fallback_but_allows_model_fallback():
    tracker = DegradationTracker()
    tracker.record(
        stage="conception.review",
        component="conception_review",
        reason="static_response",
        severity="error",
        fallback=True,
    )
    tracker.record(
        stage="conception.final",
        component="conception_finalize",
        reason="model_fallback",
        severity="error",
        fallback=True,
    )
    assert conception._required_conception_stage_fallbacks(
        tracker, start_index=0, stage="conception.review"
    )
    assert not conception._required_conception_stage_fallbacks(
        tracker, start_index=0, stage="conception.final"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "fallback_source", "expected_static"),
    [
        ("fallback", "static_response", True),
        ("fallback", "model_fallback", False),
    ],
)
async def test_llm_call_json_records_real_fallback_provenance(
    monkeypatch, provider, fallback_source, expected_static
):
    async def fake_complete_text(*_args, **_kwargs):
        return LLMCompletionResult(
            content='{"reviewed": true}',
            provider=provider,
            model_name="fallback-critic",
            fallback_used=True,
            fallback_source=fallback_source,
            finish_reason="fallback",
        )

    monkeypatch.setattr(conception, "complete_text", fake_complete_text)
    tracker = DegradationTracker()
    _, _ = await conception._llm_call_json(
        object(),
        load_settings(env={"BESTSELLER__LLM__MOCK": "true"}),
        role="critic",
        system_prompt="审查故事。",
        user_prompt="检查冲突和读者期待。",
        fallback='{"reviewed": false}',
        template="conception_review",
        stage="conception.review",
        language="zh-CN",
        degradation_tracker=tracker,
        degradation_component="conception_review",
    )
    matches = conception._required_conception_stage_fallbacks(
        tracker, start_index=0, stage="conception.review"
    )
    assert bool(matches) is expected_static


@pytest.mark.parametrize(
    ("review", "final", "passed"),
    [
        (
            {"contradictions": ["主角年龄与角色档案的年龄不一致"], "gaps": []},
            {"review_adjudications": []},
            False,
        ),
        (
            {"contradictions": ["主角年龄与角色档案的年龄不一致"], "gaps": []},
            {"review_adjudications": ["已消除：统一世界地图中的地名"]},
            False,
        ),
        (
            {
                "contradictions": [],
                "gaps": ["建议增加更强的开篇反差"],
                "market_suggestions": ["增加更强的开篇反差"],
            },
            {"review_adjudications": []},
            True,
        ),
        (
            {"contradictions": ["主角年龄与角色档案的年龄不一致"], "gaps": []},
            {"review_adjudications": ["保留主角年龄与角色档案的年龄不一致：角色刻意隐瞒年龄。"]},
            True,
        ),
        (
            {
                "contradictions": [],
                "gaps": [{"kind": "actionable_gap", "issue": "触发事件缺少明确地点"}],
            },
            {
                "review_adjudications": [
                    {
                        "issue_id": "gaps:0",
                        "decision": "resolved",
                        "reason": "已在开场补入北门。",
                    }
                ]
            },
            True,
        ),
        (
            {"contradictions": ["年龄与档案不一致", "地点与年表不一致"], "gaps": []},
            {
                "review_adjudications": [
                    {"issue_id": "contradictions:0", "decision": "retained", "reason": "角色隐瞒年龄。"},
                    {"issue_id": "contradictions:0", "decision": "resolved", "reason": "重复错误编号。"},
                ]
            },
            False,
        ),
        (
            {"contradictions": ["年龄与档案不一致"], "gaps": []},
            {"review_adjudications": [{"issue_id": "contradictions:0", "decision": "retained", "reason": ""}]},
            False,
        ),
        (
            {"contradictions": ["the protagonist's age conflicts with the archive"], "gaps": []},
            {"review_adjudications": ["retained: the antagonist has the same age"]},
            False,
        ),
    ],
)
def test_review_adjudication_audit_distinguishes_facts_from_advice(review, final, passed):
    report = conception._review_adjudication_audit(review, final)
    assert report["passed"] is passed


@pytest.mark.parametrize("is_en", [False, True])
def test_finalizer_prompt_handoff_lists_stable_issue_ids_only_when_needed(is_en):
    ctx = {
        "genre": "fantasy" if is_en else "玄幻",
        "sub_genre": "epic" if is_en else "东方玄幻",
        "chapter_count": 30,
        "language": "en-US" if is_en else "zh-CN",
        "description": "A courier must protect a witness." if is_en else "一个信使必须保护证人。",
    }
    builder = conception._finalize_user_prompt_en if is_en else conception._finalize_user_prompt
    base = builder(ctx, {}, {}, {}, {})
    prompt = conception._append_review_adjudication_contract(
        base,
        {"contradictions": ["age conflicts with the archive" if is_en else "年龄与档案不一致"]},
        is_en=is_en,
    )
    assert "issue_id=contradictions:0" in prompt
    assert "decision" in prompt and "reason" in prompt
    assert "market_suggestions" not in prompt[-500:]
    clean = conception._append_review_adjudication_contract(
        base, {"contradictions": [], "gaps": ["建议换一种开篇"]}, is_en=is_en
    )
    assert clean == base

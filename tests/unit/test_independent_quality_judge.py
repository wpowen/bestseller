from __future__ import annotations

# ruff: noqa: RUF001
import asyncio
import json
from types import SimpleNamespace

import pytest

from bestseller.services.independent_quality_judge import (
    JUDGE_DIMENSIONS,
    BlindJudgeInput,
    ModelFamilyConflictError,
    build_blind_judge_user_prompt,
    run_independent_quality_judge,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _payload(winner: str, *, margin: float = 0.4, dimension_winner: str | None = None) -> str:
    selected = dimension_winner or winner
    return json.dumps(
        {
            "winner": winner,
            "margin": margin,
            "dimensions": {
                key: {"winner": selected, "evidence": f"{key} evidence"} for key in JUDGE_DIMENSIONS
            },
            "reason": "有明确正文证据",
        },
        ensure_ascii=False,
    )


def _result(content: str, *, fallback: bool = False) -> LLMCompletionResult:
    return LLMCompletionResult(
        content=content,
        provider="test",
        model_name="judge/test",
        fallback_used=fallback,
        finish_reason="stop",
    )


def _settings():
    return load_settings(
        env={
            "BESTSELLER__LLM__MOCK": "true",
            "BESTSELLER__LLM__INDEPENDENT_JUDGE_PRIMARY_MODEL_KEY": "judge-primary",
            "BESTSELLER__LLM__INDEPENDENT_JUDGE_SECONDARY_MODEL_KEY": "judge-secondary",
        }
    )


def _input() -> BlindJudgeInput:
    return BlindJudgeInput(
        genre="修仙升级",
        chapter_number=3,
        compact_contract="主角必须夺回灵印，并付出经脉受损代价。",
        draft_a="甲稿正文：少年按住裂开的经脉。",
        draft_b="乙稿正文：少年平静地总结了全部风险。",
    )


def _entry(model: str, *, available: bool = True):
    return SimpleNamespace(model=model, available=available, id="test")


def test_dimensions_and_blind_prompt_are_exact_and_provenance_free() -> None:
    assert JUDGE_DIMENSIONS == (
        "reader_pull",
        "character_embodiment",
        "conflict_payoff",
        "emotional_movement",
        "prose_texture",
        "ai_flavor",
        "continuity_contract",
    )

    prompt = build_blind_judge_user_prompt(_input(), swapped=False)

    assert "修仙升级" in prompt and "第3章" in prompt
    assert "主角必须夺回灵印" in prompt
    assert "【A】" in prompt and "【B】" in prompt
    for leak in (
        "writer_prompt",
        "rewrite_instruction",
        "candidate_id",
        "model_name",
        "strategy_id",
        "created_at",
        "draft_id",
    ):
        assert leak not in prompt


def test_strict_model_family_conflict_fails_before_llm_call(monkeypatch) -> None:
    calls = 0

    async def fake_complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _result(_payload("A"))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        lambda key: _entry("anthropic/claude-opus-4-5"),
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    with pytest.raises(ModelFamilyConflictError):
        asyncio.run(
            run_independent_quality_judge(
                object(),
                _settings(),
                _input(),
                writer_model="anthropic/claude-sonnet-4-5",
                editor_model="anthropic/claude-sonnet-4-5",
                strict=True,
            )
        )

    assert calls == 0


def test_calls_primary_through_critic_catalog_key_in_both_positions(monkeypatch) -> None:
    requests = []
    responses = iter([_payload("A"), _payload("B")])

    async def fake_complete(session, settings, request):
        requests.append(request)
        return _result(next(responses))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        lambda key: _entry(
            "deepseek/deepseek-v4-flash" if key == "judge-primary" else "openai/mistralai/mistral"
        ),
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude-sonnet-4-5",
            editor_model="anthropic/claude-sonnet-4-5",
            strict=True,
        )
    )

    assert result.status == "decisive"
    assert result.winner == "draft_a"
    assert result.advisory_only is True
    assert len(requests) == 2
    assert all(request.logical_role == "critic" for request in requests)
    assert all(request.model_catalog_key == "judge-primary" for request in requests)
    assert requests[0].user_prompt.index("甲稿正文") < requests[0].user_prompt.index("乙稿正文")
    assert requests[1].user_prompt.index("乙稿正文") < requests[1].user_prompt.index("甲稿正文")


def test_position_inconsistency_is_ambiguous_tie(monkeypatch) -> None:
    responses = iter([_payload("A"), _payload("A"), _payload("tie"), _payload("tie")])

    async def fake_complete(session, settings, request):
        return _result(next(responses))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        lambda key: _entry(
            "deepseek/deepseek-v4-flash" if key == "judge-primary" else "openai/mistralai/mistral"
        ),
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude",
            editor_model="anthropic/claude",
        )
    )

    assert result.status == "ambiguous"
    assert result.winner == "tie"
    assert result.secondary_used is True


def test_invalid_json_repairs_once_then_is_inconclusive(monkeypatch) -> None:
    responses = iter(["not json", "still not json", _payload("B")])
    requests = []

    async def fake_complete(session, settings, request):
        requests.append(request)
        return _result(next(responses))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        lambda key: _entry(
            "deepseek/deepseek-v4-flash" if key == "judge-primary" else "openai/mistralai/mistral"
        ),
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude",
            editor_model="anthropic/claude",
        )
    )

    assert result.status == "inconclusive"
    assert len(requests) == 3
    assert (
        sum(
            request.prompt_template == "independent_quality_judge_json_repair"
            for request in requests
        )
        == 1
    )


def test_fallback_never_becomes_a_passing_verdict(monkeypatch) -> None:
    responses = iter([_result(_payload("A"), fallback=True), _result(_payload("B"))])

    async def fake_complete(session, settings, request):
        return next(responses)

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        lambda key: _entry(
            "deepseek/deepseek-v4-flash" if key == "judge-primary" else "openai/mistralai/mistral"
        ),
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude",
            editor_model="anthropic/claude",
        )
    )

    assert result.status == "inconclusive"
    assert result.winner is None


def test_secondary_runs_only_for_tie_low_margin_or_core_disagreement(monkeypatch) -> None:
    calls = []
    responses = iter(
        [
            _payload("A", margin=0.05),
            _payload("B", margin=0.05),
            _payload("A", margin=0.4),
            _payload("B", margin=0.4),
        ]
    )

    async def fake_complete(session, settings, request):
        calls.append(request.model_catalog_key)
        return _result(next(responses))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        lambda key: _entry(
            "deepseek/deepseek-v4-flash" if key == "judge-primary" else "openai/mistralai/mistral"
        ),
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude",
            editor_model="anthropic/claude",
        )
    )

    assert result.status == "decisive"
    assert calls == ["judge-primary", "judge-primary", "judge-secondary", "judge-secondary"]


def test_high_confidence_primary_does_not_require_secondary_catalog(monkeypatch) -> None:
    calls = []
    responses = iter([_payload("A", margin=0.4), _payload("B", margin=0.4)])

    def fake_catalog(key):
        if key == "judge-secondary":
            raise AssertionError("secondary catalog must not be resolved")
        return _entry("deepseek/deepseek-v4-flash")

    async def fake_complete(session, settings, request):
        calls.append(request.model_catalog_key)
        return _result(next(responses))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        fake_catalog,
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude",
            editor_model="anthropic/claude",
        )
    )

    assert result.status == "decisive"
    assert result.secondary_used is False
    assert calls == ["judge-primary", "judge-primary"]


def test_low_margin_primary_with_unavailable_secondary_is_inconclusive(monkeypatch) -> None:
    calls = []
    responses = iter([_payload("A", margin=0.05), _payload("B", margin=0.05)])

    def fake_catalog(key):
        if key == "judge-secondary":
            return _entry("openai/mistralai/mistral", available=False)
        return _entry("deepseek/deepseek-v4-flash")

    async def fake_complete(session, settings, request):
        calls.append(request.model_catalog_key)
        return _result(next(responses))

    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.get_model_catalog_entry",
        fake_catalog,
    )
    monkeypatch.setattr(
        "bestseller.services.independent_quality_judge.complete_text", fake_complete
    )

    result = asyncio.run(
        run_independent_quality_judge(
            object(),
            _settings(),
            _input(),
            writer_model="anthropic/claude",
            editor_model="anthropic/claude",
        )
    )

    assert result.status == "inconclusive"
    assert result.winner is None
    assert "secondary_judge_required_but_unavailable" in result.reasons
    assert calls == ["judge-primary", "judge-primary"]


def test_default_settings_keep_independent_judge_in_shadow_mode() -> None:
    settings = load_settings(env={"BESTSELLER__LLM__MOCK": "true"})

    assert settings.llm.independent_judge_mode == "shadow"
    assert settings.llm.independent_judge_strict_model_family is True
    assert settings.llm.independent_judge_primary_model_key


def test_chapter_and_reader_adapters_only_forward_blind_contract(monkeypatch) -> None:
    from bestseller.services import chapter_llm_quality_judge, reader_panel_judge

    captured = []

    async def fake_run(session, settings, value, **kwargs):
        captured.append((value, kwargs))
        return "advisory-result"

    monkeypatch.setattr(chapter_llm_quality_judge, "run_independent_quality_judge", fake_run)
    monkeypatch.setattr(reader_panel_judge, "run_independent_quality_judge", fake_run)
    settings = _settings()

    chapter_result = asyncio.run(
        chapter_llm_quality_judge.judge_chapter_pair_advisory(
            object(),
            settings,
            genre="悬疑推理",
            chapter_number=10,
            compact_contract="必须揭示一条证据并保留一个疑问。",
            draft_a="正文甲",
            draft_b="正文乙",
        )
    )
    reader_result = asyncio.run(
        reader_panel_judge.judge_reader_pair_advisory(
            object(),
            settings,
            genre="情感言情",
            chapter_number=30,
            compact_contract="关系必须发生不可逆变化。",
            draft_a="正文丙",
            draft_b="正文丁",
        )
    )

    assert chapter_result == reader_result == "advisory-result"
    assert captured[0][0] == BlindJudgeInput(
        genre="悬疑推理",
        chapter_number=10,
        compact_contract="必须揭示一条证据并保留一个疑问。",
        draft_a="正文甲",
        draft_b="正文乙",
    )
    assert captured[0][1]["writer_model"] == settings.llm.writer.model
    assert captured[0][1]["editor_model"] == settings.llm.editor.model

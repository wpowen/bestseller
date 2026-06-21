"""L1 unit tests for the premise appeal judge (deterministic parts + assembly).

The LLM call is not exercised here (covered in the L3 e2e script). These tests
pin the deterministic pre-pass, the trigger detector, score coercion across
0-1/0-5 scales, the fail-open fallback, and the one-vote-veto gating.
"""

from __future__ import annotations

import json

import pytest

from bestseller.services.premise_appeal_judge import (
    _assemble_verdict,
    _coerce_score,
    _detect_triggers,
    _deterministic_scores,
    evaluate_premise_appeal,
)
from bestseller.services.story_appeal import load_story_appeal_config, resolve_genre_lexicon

_DIM_KEYS = (
    "concept_strength", "novelty", "conflict_stakes", "emotional_value",
    "hook_suspense", "immersion", "sustainability", "audience_fit", "structure_pace",
)


class _FakeCompletion:
    def __init__(self, content: str, run_id: str | None = "run-1"):
        self.content = content
        self.llm_run_id = run_id


def _stub_complete_text(monkeypatch, payload: dict | None, *, raise_exc: bool = False):
    import bestseller.services.llm as llm_mod

    async def fake(session, settings, request):
        if raise_exc:
            raise RuntimeError("llm down")
        return _FakeCompletion(json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr(llm_mod, "complete_text", fake)

# ruff: noqa: RUF001, RUF003, E501 — Chinese test fixtures, long inline tuples.

_STRONG_PREMISE = "被退婚的废物赘婿，其实是隐藏的商业帝国之主，三天对赌局里步步打脸翻盘。"
_STRONG_SYN = "退婚宴上岳父羞辱他三天内拿不出一个亿就滚，没人知道他是隐藏的首富，这一次他要让所有人跪着求他。"
_WEAK_PREMISE = "一个少年踏上修炼之路，历经磨难最终成为强者的故事。"
_WEAK_SYN = "这是一个关于成长的故事，本以为平凡却没想到不平凡，敬请期待。"


@pytest.mark.unit
def test_deterministic_scores_discriminate_strong_from_weak():
    lex = resolve_genre_lexicon("都市", "赘婿")
    strong = _deterministic_scores(_STRONG_PREMISE, _STRONG_SYN, lex)
    weak = _deterministic_scores(_WEAK_PREMISE, _WEAK_SYN, lex)
    strong_avg = sum(strong.values()) / len(strong)
    weak_avg = sum(weak.values()) / len(weak)
    assert strong_avg > weak_avg
    # all nine dims present and in range
    assert len(strong) == 9
    assert all(0.0 <= v <= 5.0 for v in strong.values())


@pytest.mark.unit
def test_deterministic_baseline_not_punitively_low():
    # A genuinely strong premise must not land in 'pass' deterministically,
    # else the LLM-down fallback would spuriously trigger regeneration
    # (gate self-harm anti-pattern).
    lex = resolve_genre_lexicon("都市", "赘婿")
    strong = _deterministic_scores(_STRONG_PREMISE, _STRONG_SYN, lex)
    pct = sum(v / 5.0 for v in strong.values()) / len(strong) * 100
    assert pct >= 65  # at least 'consider'


@pytest.mark.unit
def test_trigger_detection_fires_on_strong_signals():
    lex = resolve_genre_lexicon("都市", "赘婿")
    fired = _detect_triggers(
        f"{_STRONG_PREMISE} {_STRONG_SYN}", _STRONG_SYN[:40] + _STRONG_PREMISE[:40], lex
    )
    assert "T3_high_arousal" in fired       # 退婚/打脸
    assert "T5_wish_preview" in fired       # 隐藏身份
    assert "T6_loss_tension" in fired or "T4_immersion_anchor" in fired


@pytest.mark.unit
@pytest.mark.parametrize(
    "llm_value,det_value,expected_floor,expected_ceil",
    [
        (0.9, 3.0, 4.0, 5.0),   # 0-1 scale → ×5
        (4.5, 3.0, 4.5, 4.5),   # 0-5 scale → as-is
        (None, 3.2, 3.2, 3.2),  # missing → deterministic
        ("bad", 2.0, 2.0, 2.0), # unparseable → deterministic
    ],
)
def test_coerce_score_scale_handling(llm_value, det_value, expected_floor, expected_ceil):
    got = _coerce_score(llm_value, det_value)
    assert expected_floor <= got <= expected_ceil


@pytest.mark.unit
def test_assemble_verdict_fail_open_uses_deterministic_when_llm_empty():
    cfg = load_story_appeal_config()
    det = dict.fromkeys(("concept_strength", "novelty", "conflict_stakes", "emotional_value", "hook_suspense", "immersion", "sustainability", "audience_fit", "structure_pace"), 3.5)
    verdict = _assemble_verdict(
        parsed={}, det_scores=det, triggers=["T3_high_arousal"],
        rubric=cfg["premise_rubric"], cfg=cfg, is_long=True,
        llm_used=False, llm_run_id=None,
    )
    assert verdict.total == pytest.approx(70.0, abs=1.0)  # 3.5/5 * 100
    assert len(verdict.dimensions) == 9
    assert verdict.llm_used is False


@pytest.mark.unit
def test_gating_low_concept_caps_to_pass():
    cfg = load_story_appeal_config()
    # concept_strength below floor (gate_below=2 → cap pass), everything else high
    det = dict.fromkeys(("concept_strength", "novelty", "conflict_stakes", "emotional_value", "hook_suspense", "immersion", "sustainability", "audience_fit", "structure_pace"), 4.5)
    det["concept_strength"] = 1.0
    verdict = _assemble_verdict(
        parsed={}, det_scores=det, triggers=[], rubric=cfg["premise_rubric"],
        cfg=cfg, is_long=True, llm_used=False, llm_run_id=None,
    )
    assert verdict.gated_grade == "pass"
    assert any("概念" in c or "卖点" in c for c in verdict.gating_caps)


@pytest.mark.unit
def test_gating_long_only_sustainability_skipped_for_short_form():
    cfg = load_story_appeal_config()
    det = dict.fromkeys(("concept_strength", "novelty", "conflict_stakes", "emotional_value", "hook_suspense", "immersion", "sustainability", "audience_fit", "structure_pace"), 4.5)
    det["sustainability"] = 0.5  # below floor
    long_v = _assemble_verdict(
        parsed={}, det_scores=det, triggers=[], rubric=cfg["premise_rubric"],
        cfg=cfg, is_long=True, llm_used=False, llm_run_id=None,
    )
    short_v = _assemble_verdict(
        parsed={}, det_scores=det, triggers=[], rubric=cfg["premise_rubric"],
        cfg=cfg, is_long=False, llm_used=False, llm_run_id=None,
    )
    # sustainability gate is gate_long_only → caps long form, not short form
    assert any("可持续" in c for c in long_v.gating_caps)
    assert not any("可持续" in c for c in short_v.gating_caps)


@pytest.mark.unit
async def test_evaluate_premise_appeal_consumes_llm_scores(monkeypatch):
    _stub_complete_text(
        monkeypatch,
        {
            "dimension_scores": dict.fromkeys(_DIM_KEYS, 4.6),
            "rationale": {"concept_strength": "一句话强卖点"},
            "suggestions": ["保持当前钩子强度"],
            "overall_comment": "great idea",
        },
    )
    verdict = await evaluate_premise_appeal(
        None, None,
        premise=_STRONG_PREMISE, synopsis=_STRONG_SYN, title="我老婆是首富",
        tags=["赘婿", "打脸"], genre="都市", sub_genre="赘婿",
        chapter_count=500, project_slug=None,
    )
    assert verdict.llm_used is True
    assert verdict.total > 85
    assert verdict.gated_grade == "recommend"
    assert len(verdict.dimensions) == 9


@pytest.mark.unit
async def test_evaluate_premise_appeal_fail_open_on_llm_error(monkeypatch):
    _stub_complete_text(monkeypatch, None, raise_exc=True)
    verdict = await evaluate_premise_appeal(
        None, None,
        premise=_STRONG_PREMISE, synopsis=_STRONG_SYN, title="我老婆是首富",
        tags=["赘婿"], genre="都市", sub_genre="赘婿",
        chapter_count=500, project_slug=None,
    )
    # never raises; falls back to deterministic scores
    assert verdict.llm_used is False
    assert 0.0 <= verdict.total <= 100.0
    assert len(verdict.dimensions) == 9


@pytest.mark.unit
async def test_evaluate_premise_appeal_llm_0to1_scale_normalized(monkeypatch):
    # LLM returns 0-1 scale; judge must rescale to 0-5 internally.
    _stub_complete_text(
        monkeypatch,
        {
            "dimension_scores": dict.fromkeys(_DIM_KEYS, 0.9),
            "rationale": {},
            "suggestions": [],
            "overall_comment": "scaled",
        },
    )
    verdict = await evaluate_premise_appeal(
        None, None,
        premise=_STRONG_PREMISE, synopsis=_STRONG_SYN, title="t",
        tags=["赘婿"], genre="都市", sub_genre="赘婿",
        chapter_count=500, project_slug=None,
    )
    assert verdict.total > 85  # 0.9*5 = 4.5/5 → ~90

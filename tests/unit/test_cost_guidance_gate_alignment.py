"""Guard: conception cost guidance must not recommend what the logline gate vetoes.

Root cause this file exists to prevent (2026-07-16): the anti-debt campaign
rewrote conception's cost guidance as a *menu* of replacement cost forms
(寿元损耗 / 记忆消解 / 扣命 …), while the logline gate's CORE axis
``cost_integrity`` one-vote-vetoes exactly those "random system tax" families.
Generation obeyed the menu, the gate executed the result, and two consecutive
book creations died at the bar — the user saw it as "cannot create projects".

These tests render the real guidance blocks and require that any mention of a
gate-banned cost family sits inside a prohibition sentence, never a
recommendation.
"""

from __future__ import annotations

import re

import pytest

from bestseller.services.anti_default_motif import anti_debt_block
from bestseller.services.conception import (
    _GOLDEN_FINGER_DESIGN_PRINCIPLE,
    _anti_debt_metaphor_guardrail,
    _render_debt_rewrite_feedback,
)

# The cost families the logline gate's cost_integrity axis names as hard
# violations (logline_gate.py _FIX_DIRECTIVES["cost_integrity"]), plus their
# common surface variants in prompt text.
_GATE_BANNED_FAMILIES = ("失忆", "扣命", "掉寿命", "寿元", "折寿", "记忆消解", "资源债")

_PROHIBITION_MARKERS = ("禁止", "不要", "不得", "不写", "删掉", "一律不要", "never", "ban", "no cost")

_SENTENCE_SPLIT = re.compile(r"[。；;.!?！？\n]")


def _guidance_blocks() -> list[tuple[str, str]]:
    """Guidance surfaces that still render text.

    (2026-08-02) ``anti_debt_block`` and ``_render_debt_rewrite_feedback`` were
    retired and now return "", so they are no longer guidance — an empty block
    cannot recommend a gate-banned cost family, and cannot be asked to state a
    replacement rule either. Only the golden-finger design principle remains.
    """
    return [
        ("golden_finger_principle", _GOLDEN_FINGER_DESIGN_PRINCIPLE),
    ]


def test_retired_debt_guidance_renders_nothing() -> None:
    # prompt 护栏块维持退役（8·2：枚举禁词=种词）
    assert anti_debt_block(is_en=False) == ""
    assert anti_debt_block(is_en=True) == ""
    # 2026-08-13 修订（用户令）：冠军级债务支配的重试反馈复活，但必须
    # withhold 词汇——只下达换族指令，不携带任何族内 token（种词铁律）。
    for is_en in (False, True):
        feedback = _render_debt_rewrite_feedback(is_en=is_en)
        assert feedback.strip()
        for token in ("债", "账", "灵堂", "棺", "丧", "寿", "欠"):
            assert token not in feedback, token


def test_conception_prompt_guard_is_intentionally_empty() -> None:
    assert _anti_debt_metaphor_guardrail({}, is_en=False) == ""
    assert _anti_debt_metaphor_guardrail({}, is_en=True) == ""


@pytest.mark.parametrize(("name", "text"), _guidance_blocks())
def test_banned_cost_families_appear_only_inside_prohibitions(name: str, text: str) -> None:
    """失忆/扣命/寿元-family words may appear only to BAN them, never as a menu."""

    for sentence in _SENTENCE_SPLIT.split(text):
        lowered = sentence.lower()
        hits = [f for f in _GATE_BANNED_FAMILIES if f in sentence or f in lowered]
        if not hits:
            continue
        assert any(marker in sentence or marker in lowered for marker in _PROHIBITION_MARKERS), (
            f"{name}: sentence recommends gate-banned cost family {hits} without a "
            f"prohibition marker — the logline gate's cost_integrity axis will veto "
            f"every concept that follows this guidance. Sentence: {sentence!r}"
        )


def test_guidance_states_costs_are_optional_and_causal() -> None:
    """The replacement rule (causal derivation or no cost) must actually be present —
    deleting the menu without stating the rule would leave the model guessing."""

    for name, text in _guidance_blocks():
        if name in {"debt_rewrite_feedback_en", "anti_debt_block_en"}:
            assert "caus" in text.lower(), f"{name} lost the causal-derivation rule"
        elif "zh" in name or name == "golden_finger_principle":
            assert "因果" in text and "不是必选" in text or "推导" in text, (
                f"{name} lost the causal-derivation rule"
            )

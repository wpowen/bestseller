from __future__ import annotations

import pytest

from bestseller.services.fanqie_long_ranking_gate import evaluate_fanqie_long_ranking_gate
from bestseller.services.whole_book_quality_gate import evaluate_whole_book_quality

pytestmark = pytest.mark.unit

_NEUTRAL_COSTS = [None, "", "unknown", "invalid", "STANDARD", " standard ", "minimal", "external"]
_WHOLE_BOOK_COST_CODES = {"emotion_cost_free_win", "emotion_irreversible_cost_gap"}
_CHAPTERS = dict.fromkeys(
    range(1, 4), "主角使用能力,夺回证据,当众翻盘。新的线索出现,门外是谁?",
)
_KERNEL = {
    "emotion_chain": [
        {
            "chapter_range": "1-3",
            "target_reader_emotion": "satisfaction",
            "payoff_or_aftereffect": "主角翻盘成功。",
        },
    ],
    "ending_texture_contract": {
        "ending_type": "HE",
        "core_wish_fulfilled": "主角找到答案。",
        "irreversible_cost_retained": "",
        "theme_answer": "",
    },
}


@pytest.mark.parametrize("cost_style", _NEUTRAL_COSTS)
def test_whole_book_only_explicit_standard_can_require_cost(cost_style):
    standard = evaluate_whole_book_quality(
        _CHAPTERS, emotion_driven_kernel=_KERNEL, cost_style="standard",
    )
    neutral = evaluate_whole_book_quality(
        _CHAPTERS, emotion_driven_kernel=_KERNEL, cost_style=cost_style,
    )
    standard_codes = {finding.code for finding in standard.findings}
    neutral_codes = {finding.code for finding in neutral.findings}
    assert _WHOLE_BOOK_COST_CODES <= standard_codes
    assert not neutral_codes & _WHOLE_BOOK_COST_CODES
    assert neutral.findings == tuple(
        finding for finding in standard.findings if finding.code not in _WHOLE_BOOK_COST_CODES
    )
    assert "emotion_visible_desire_gap" in neutral_codes
    assert "emotion_ending_texture_not_ready" in neutral_codes
    assert neutral.passed is False
    assert neutral.metrics == standard.metrics


def test_whole_book_omitted_cost_is_neutral():
    omitted = evaluate_whole_book_quality(_CHAPTERS, emotion_driven_kernel=_KERNEL)
    unknown = evaluate_whole_book_quality(_CHAPTERS, emotion_driven_kernel=_KERNEL, cost_style=None)
    assert omitted == unknown
    assert not {finding.code for finding in omitted.findings} & _WHOLE_BOOK_COST_CODES


@pytest.mark.parametrize("cost_style", _NEUTRAL_COSTS)
def test_fanqie_only_explicit_standard_can_require_cost(cost_style):
    # Ability without a payoff/hook keeps independently actionable failures.
    chapters = {1: "主角展示能力。"}
    standard = evaluate_fanqie_long_ranking_gate(chapters, cost_style="standard")
    neutral = evaluate_fanqie_long_ranking_gate(chapters, cost_style=cost_style)
    standard_codes = {finding.code for finding in standard.findings}
    neutral_codes = {finding.code for finding in neutral.findings}
    assert "advantage_cost_missing" in standard_codes
    assert "advantage_cost_missing" not in neutral_codes
    assert neutral.findings == [
        finding for finding in standard.findings if finding.code != "advantage_cost_missing"
    ]
    assert "chapter_payoff_missing" in neutral_codes
    assert "chapter_future_hook_missing" in neutral_codes
    assert neutral.passed is False
    assert neutral.metrics == standard.metrics


def test_fanqie_omitted_cost_is_neutral():
    omitted = evaluate_fanqie_long_ranking_gate({1: "主角展示能力。"})
    unknown = evaluate_fanqie_long_ranking_gate({1: "主角展示能力。"}, cost_style=None)
    assert omitted == unknown
    assert "advantage_cost_missing" not in {finding.code for finding in omitted.findings}

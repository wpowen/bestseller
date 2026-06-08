"""Tests for the shared hook-signal vocabulary (services.hook_signals)."""

from __future__ import annotations

import pytest

from bestseller.services.hook_signals import (
    SHARED_HOOK_TERMS,
    tail_contains_hook,
)


# The concrete chapter-1 ending that the Qimao gate falsely flagged as
# weak_hook: it sets up an appointment, a threat, and an open identity question.
_REAL_HOOK_ENDING = (
    "她从口袋里摸出一张折叠的纸条。"
    "“今晚十一点半，地铁三号线末班，城南维修通道。"
    "带着你刚才撕开规则断层用的那套东西，来换一份能暂时压住痕迹的药剂配方。”"
    "“他们会找到你的。”"
    "“你是什么。”"
    "走廊尽头的应急灯跳了两下，熄了。"
)

# A genuinely flat ending — no question, threat, appointment, or open loop.
_FLAT_ENDING = (
    "他回到房间，坐下来喝了一口水。窗外天色渐暗，街道安静。"
    "他想着今天发生的事，觉得有些累，便闭上眼睛休息。"
)


@pytest.mark.unit
def test_real_hook_ending_is_recognized():
    """Appointment + threat + open question must count as a hook."""
    assert tail_contains_hook(_REAL_HOOK_ENDING) is True


@pytest.mark.unit
def test_real_hook_ending_without_punctuation_still_recognized():
    """The hook must be detected on semantics, not on a trailing ？."""
    stripped = _REAL_HOOK_ENDING.replace("？", "").replace("?", "")
    assert tail_contains_hook(stripped) is True


@pytest.mark.unit
def test_flat_ending_is_not_a_hook():
    assert tail_contains_hook(_FLAT_ENDING) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "ending",
    [
        "倒计时还剩三十分钟。",  # countdown
        "他被盯上了。",  # pursuit
        "明天午夜，老地方见。",  # appointment
        "这个人究竟是谁。",  # open question, no ？
        "约在11:30，城南。",  # explicit clock
    ],
)
def test_individual_hook_classes(ending: str):
    assert tail_contains_hook(ending) is True


@pytest.mark.unit
def test_shared_terms_cover_three_classes():
    # sanity: the merged vocabulary is non-trivial and includes a sample from
    # each class so the three downstream gates inherit real coverage.
    assert "末班" in SHARED_HOOK_TERMS  # appointment
    assert "会找到你" in SHARED_HOOK_TERMS  # threat
    assert "究竟" in SHARED_HOOK_TERMS  # open question
    assert len(SHARED_HOOK_TERMS) >= 40

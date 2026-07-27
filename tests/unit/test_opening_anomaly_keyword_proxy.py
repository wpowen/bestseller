"""A keyword count may not veto an opening it cannot read.

Field failure (2026-07-26, urban-power-reversal-1785026717 ch1): the first
chapter the framework ever produced was blocked with

    OPENING_NO_ANOMALY (critical) — 前 200 字仅含 0 个异常信号关键词，开篇不抓人

The opening it rejected:

    板房顶上的日光灯管又闪了一下…赵崇业的左手拍下来，A4纸压在岑野面前…
    「十秒。」赵崇业…把那张六万三的真欠条捏在拇指和食指之间，慢慢揉成团，
    「签完字，九万八打到账上。」

That is a coercion scene with a countdown, a physical threat, and a concrete
stake in the first 200 characters — commercially strong by any reading. It
simply expresses its anomaly through ACTION rather than through the twelve
words the detector counts (没有/不对/突然/冷/镜/影…).

This is the recurring defect this repo already documented twice: a keyword
proxy standing in for a quality keywords cannot measure, then punishing the
show-don't-tell prose it was meant to encourage (see the scene emotion/hook
scorer, and the falsified opening-jargon lever). ``severity="critical"`` routed
it straight into ``WriteSafetyFinding`` and blocked the chapter.

The signal is kept — a genuinely flat opening is still worth flagging — but a
lexical proxy does not get a hard veto.
"""

from __future__ import annotations

import pytest

from bestseller.services.opening_hook_density_gate import check_opening_hook_density


pytestmark = pytest.mark.unit


# Verbatim shape of the opening that was blocked in production.
_STRONG_ACTION_OPENING = (
    "板房顶上的日光灯管又闪了一下。柴油味混着烟灰，从白沙牌玻璃烟缸口蹿上来，"
    "呛得老周把手里的搪瓷缸往桌面外挪了半寸。赵崇业的左手拍下来，A4纸压在岑野"
    "面前。抬头的红字印着城东工程监理咨询有限公司，签字栏旁边摆着一枚枣红色的"
    "监理工程师章。「十秒。」赵崇业抬起右手，把那张六万三的真欠条捏在拇指和食指"
    "之间，慢慢揉成团，「签完字，九万八打到账上。清账。」岑野没有去接那支笔。"
)


def _codes(text: str, chapter: int = 1) -> dict[str, str]:
    return {f.code: f.severity for f in check_opening_hook_density(text, chapter)}


def test_action_driven_opening_is_not_hard_blocked() -> None:
    """THE regression, verbatim."""

    severities = _codes(_STRONG_ACTION_OPENING)

    assert severities.get("OPENING_NO_ANOMALY") not in {"critical", "high"}, (
        "a coercion scene with a countdown and a physical threat cannot be "
        "hard-blocked for missing twelve specific words"
    )


def test_the_signal_is_still_reported() -> None:
    """Downgraded, not deleted — a flat opening should still be visible."""

    flat = "他坐在办公室里整理文件。桌上摆着一杯茶。窗外阳光很好。" * 6

    assert "OPENING_NO_ANOMALY" in _codes(flat), (
        "the detector must keep reporting; only its veto power is removed"
    )


def test_structural_opening_defects_still_block() -> None:
    """No blanket loosening: defects a lexical check CAN judge stay blocking.

    A 200-character run-on first sentence is measurable without semantics, so
    it keeps its severity.
    """

    long_first_sentence = "他" + "又想起了那些事情" * 30 + "。后面还有内容。"

    severities = _codes(long_first_sentence)

    assert severities.get("OPENING_FIRST_SENTENCE_TOO_LONG") in {"critical", "high"}


def test_write_safety_only_promotes_high_and_critical() -> None:
    """Pin the coupling that made severity the veto switch, so a future bump
    back to critical is a visible decision rather than an accident."""

    import inspect

    from bestseller.services import drafts

    source = inspect.getsource(drafts)
    idx = source.index("check_opening_hook_density(")
    region = source[idx : idx + 700]

    assert 'severity not in {"critical", "high"}' in region, (
        "write-safety promotion still keys off severity — OPENING_NO_ANOMALY "
        "must therefore stay below that bar"
    )

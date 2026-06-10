# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from bestseller.services.opening_hook_density_gate import check_opening_hook_density

pytestmark = pytest.mark.unit


def test_ch1_real_chapter_triggers_flashback_overuse() -> None:
    # Use inline text with enough flashback keywords to reliably trigger the gate
    # (flashback_max=2, so we need >2 hits in the first 500 CJK chars).
    text = (
        "林渊记得那年他曾经跟着父亲走过这条路，当年的回忆像水一样漫上来，"
        "那时候以前的事情已经过去很久，他想起父亲说的那句话。"
        "楼道灯亮着，但电梯口那块区域黑得像一个洞。"
        "他把铜钱压在门槛上，决定先拦住王建业再进楼。"
        "电梯空井里传来一声轻响，像有人从下面敲了三下。"
    )
    findings = check_opening_hook_density(text, chapter_number=1)
    codes = {finding.code for finding in findings}
    assert "OPENING_FLASHBACK_OVERUSE" in codes


def test_clean_opening_passes_critical_checks() -> None:
    text = (
        "镜子里多了一张脸。\n\n"
        "林渊后退一步，手按到铜钱上。电梯空着，镜影却突然动了。"
        "他把王建业拦在门外，决定先压住镜脚再问话。"
    )
    findings = check_opening_hook_density(text, chapter_number=1)
    assert all(finding.severity != "critical" for finding in findings)


def test_long_first_sentence_fails() -> None:
    text = (
        "雨棚下的灯管闪了两下王建业攥着那张旧名片的手在发抖林渊把电动车停稳。\n\n"
        "镜子里突然没有影子，楼道灯一亮一灭，门缝里的水逆着墙往上爬。"
        "他把铜钱压在门槛上，决定先拦住王建业再进楼。"
        "电梯空井里传来一声轻响，像有人从下面敲了三下。"
    )
    findings = check_opening_hook_density(text, chapter_number=1)
    codes = {finding.code for finding in findings}
    assert "OPENING_FIRST_SENTENCE_TOO_LONG" in codes


def test_non_golden_three_is_ignored() -> None:
    text = "这是一段很长的普通章节。" * 80
    assert check_opening_hook_density(text, chapter_number=86) == []

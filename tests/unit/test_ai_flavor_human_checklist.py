# ruff: noqa: RUF001
"""Human-editor anti-AI checklist coverage.

These checks turn the recurring editor complaint ("AI is explaining
information instead of staging experience") into concrete, soft detection
families. They complement show-don't-tell gates: one or two ordinary uses
stay legal, but density or authorial shortcuts surface in the AI-flavor
report.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.prompt_constructor import build_anti_slop_footer
from bestseller.services.quality_levers.detectors import scan_banned_patterns


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


def test_authorial_spoiler_and_fate_cliche_are_blocked() -> None:
    text = (
        "他不知道的是，封在井底的东西已经醒了。\n\n"
        "命运的齿轮开始转动。\n\n"
        "事情并不简单。"
    )
    cats = _cats(text)
    assert "authorial_spoiler" in cats
    assert "fate_cliche" in cats
    assert "plot_summary" in cats


def test_formulaic_narration_patterns_surface_as_warns() -> None:
    text = (
        "随着夜色压低，巷子里的人越来越少。\n\n"
        "无论他怎么解释，巡捕都不会放人。\n\n"
        "他既紧张又愤怒。"
    )
    cats = _cats(text)
    assert "essay_progression" in cats
    assert "blank_vow" in cats
    assert "balanced_formula" in cats


def test_density_clusters_flag_telling_not_single_uses() -> None:
    dense = (
        "他感到不对，似乎有人在门外，紧张得说不出话。\n\n"
        "她意识到原来钥匙被换过，震惊地退了一步。\n\n"
        "一种莫名的恐惧渐渐升起来。\n\n"
        "他发现那张纸实际上早就湿透了，心里只剩愤怒。\n\n"
        "事实上，墙后还有声音。"
    )
    cats = _cats(dense)
    assert "filter_word_density" in cats
    assert "emotion_label_density" in cats

    clean = (
        "雨水顺着配电柜往下滴，滴到第三下，楼梯口传来一声鞋底刮地。"
        "林渊把手电压低，指腹在保险丝断口上停住。"
    )
    assert "filter_word_density" not in _cats(clean)


def test_anti_slop_footer_names_experience_first_rules() -> None:
    out = build_anti_slop_footer("zh-CN")
    assert "他不知道的是" in out
    assert "无论……都" in out
    assert "动作、异常、确认、结果" in out


def test_quality_lever_scanner_knows_new_banned_patterns() -> None:
    result = scan_banned_patterns(
        "他不知道的是，命运的齿轮开始转动。随着时间推移，他越来越紧张。"
    )
    ids = {hit.pattern_id for hit in result.hits}
    assert {"authorial_spoiler", "fate_cliche", "essay_progression"} <= ids

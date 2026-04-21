"""Unit tests for volume-level convergence detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bestseller.services.volume_fingerprint import (
    VolumeConvergenceFinding,
    VolumeConvergenceReport,
    VolumeFingerprint,
    build_volume_fingerprint,
    render_prior_volumes_summary_block,
    scan_volume_plan_for_convergence,
)


# ---------------------------------------------------------------------------
# Fake VolumeModel-style row
# ---------------------------------------------------------------------------

@dataclass
class FakeVolumeModel:
    volume_number: int
    title: str
    theme: str | None = None
    goal: str | None = None
    obstacle: str | None = None


# ---------------------------------------------------------------------------
# build_volume_fingerprint
# ---------------------------------------------------------------------------

def _vol_entry(
    n: int,
    *,
    title: str = "",
    theme: str = "",
    goal: str = "",
    obstacle: str = "",
    climax: str = "",
    resolution: str = "",
    phase: str = "",
    force: str = "",
) -> dict[str, Any]:
    return {
        "volume_number": n,
        "volume_title": title,
        "volume_theme": theme,
        "volume_goal": goal,
        "volume_obstacle": obstacle,
        "volume_climax": climax,
        "volume_resolution": resolution,
        "conflict_phase": phase,
        "primary_force_name": force,
    }


def test_fingerprint_extracts_all_expected_fields() -> None:
    entry = _vol_entry(
        1,
        title="逆命入局",
        theme="身份觉醒",
        goal="进入仙门并站稳脚跟",
        obstacle="家族旧敌与门派派系斗争",
        climax="揭示真实身世并接管旧部",
        resolution="结为师门并立下立派盟约",
        phase="survival",
        force="白河派外门执事",
    )
    fp = build_volume_fingerprint(entry)
    assert fp.volume_number == 1
    assert fp.volume_title == "逆命入局"
    assert fp.conflict_phase == "survival"
    assert fp.primary_force_name == "白河派外门执事"
    assert "进入仙门并站稳脚跟" in fp.combined_text
    assert "揭示真实身世并接管旧部" in fp.combined_text


def test_fingerprint_from_volume_model_row() -> None:
    row = FakeVolumeModel(
        volume_number=3,
        title="灰楼开门",
        theme="信息战",
        goal="打开灰楼并还原旧日真相",
        obstacle="灰楼禁制与幕后黑手",
    )
    fp = build_volume_fingerprint(row)
    assert fp.volume_number == 3
    assert fp.volume_title == "灰楼开门"
    assert "打开灰楼并还原旧日真相" in fp.combined_text


def test_fingerprint_lowercases_conflict_phase() -> None:
    fp = build_volume_fingerprint(_vol_entry(1, phase="SURVIVAL"))
    assert fp.conflict_phase == "survival"


def test_fingerprint_tolerates_missing_fields() -> None:
    fp = build_volume_fingerprint({"volume_number": 2})
    assert fp.volume_number == 2
    assert fp.volume_title == ""
    assert fp.combined_text == ""


# ---------------------------------------------------------------------------
# scan_volume_plan_for_convergence
# ---------------------------------------------------------------------------

def test_identical_volumes_are_critical() -> None:
    entries = [
        _vol_entry(
            1,
            goal="掌控宗门外门并建立自己的势力",
            obstacle="派系之间的争斗与旧恩怨",
            climax="在宗门大会上击败旧派系",
            resolution="被任命为新任外门执事",
            theme="身份觉醒与势力扩张",
            phase="political_intrigue",
            force="白河派旧执事势力",
        ),
        _vol_entry(
            2,
            goal="掌控宗门外门并建立自己的势力",
            obstacle="派系之间的争斗与旧恩怨",
            climax="在宗门大会上击败旧派系",
            resolution="被任命为新任外门执事",
            theme="身份觉醒与势力扩张",
            phase="political_intrigue",
            force="白河派旧执事势力",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    assert report.has_critical
    assert report.findings[0].similarity >= 0.65


def test_distinct_volumes_pass_without_findings() -> None:
    entries = [
        _vol_entry(
            1,
            goal="从灰楼逃出并建立信息网",
            obstacle="灰楼禁制与押解队",
            climax="借浮标反噬突围出灰楼",
            resolution="潜伏青州等待时机",
            phase="survival",
            force="灰楼押解队",
        ),
        _vol_entry(
            2,
            goal="在青州建立商会外衣并摸清守将",
            obstacle="守将对外来修士的排查网",
            climax="借一场商战挤垮对手商会",
            resolution="获得青州商会头衔",
            phase="political_intrigue",
            force="青州守将的亲信",
        ),
        _vol_entry(
            3,
            goal="揭开浮标背后的家族旧债",
            obstacle="家族长辈与故人情感压力",
            climax="面对旧盟友做出斩情之举",
            resolution="与旧家族彻底断绝",
            phase="betrayal",
            force="主角家族长辈",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    # No pair above warning threshold
    assert not report.findings
    assert not report.has_critical


def test_same_conflict_phase_is_flagged_even_with_low_jaccard() -> None:
    entries = [
        _vol_entry(1, goal="different goal A" * 5, obstacle="different obstacle A",
                   phase="survival", force="force A"),
        _vol_entry(2, goal="unrelated story B" * 5, obstacle="unrelated obstacle B",
                   phase="survival", force="force B"),
    ]
    report = scan_volume_plan_for_convergence(entries)
    # Jaccard low but conflict_phase exact match → warning
    assert report.findings
    assert any("conflict_phase='survival'" in f.reason for f in report.findings)


def test_same_primary_force_name_is_flagged() -> None:
    entries = [
        _vol_entry(1, goal="story goal one which is quite unique here indeed",
                   obstacle="obstacle one", phase="survival", force="灰楼执事"),
        _vol_entry(2, goal="story goal two which is totally separate elsewhere",
                   obstacle="obstacle two", phase="political_intrigue", force="灰楼执事"),
    ]
    report = scan_volume_plan_for_convergence(entries)
    assert any("primary_force_name='灰楼执事'" in f.reason for f in report.findings)


def test_report_tracks_overused_conflict_phase_count() -> None:
    entries = [
        _vol_entry(i, phase="survival", goal=f"goal {i}" * 10, obstacle=f"obstacle {i}")
        for i in range(1, 4)
    ]
    report = scan_volume_plan_for_convergence(entries)
    assert report.conflict_phase_counts.get("survival") == 3


def test_report_tracks_overused_force_name_count() -> None:
    entries = [
        _vol_entry(i, force="白河派", goal=f"goal {i}" * 10, obstacle=f"obstacle {i}")
        for i in range(1, 4)
    ]
    report = scan_volume_plan_for_convergence(entries)
    assert report.force_name_counts.get("白河派") == 3


def test_short_text_below_threshold_is_skipped() -> None:
    entries = [
        _vol_entry(1, goal="短"),
        _vol_entry(2, goal="短"),
    ]
    report = scan_volume_plan_for_convergence(entries)
    # No exact-match tags, no phase/force either, so no findings
    assert not report.findings


def test_warning_severity_between_thresholds() -> None:
    entries = [
        _vol_entry(
            1,
            goal="进入灰楼拿回母亲遗物",
            obstacle="灰楼禁制",
            climax="用浮标反噬突围",
            theme="家族遗恨",
        ),
        _vol_entry(
            2,
            goal="进入灰楼取回家族旧信",
            obstacle="灰楼禁制",
            climax="引动浮标反噬突围",
            theme="家族秘密",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries, warning_threshold=0.2, critical_threshold=0.9)
    assert report.findings
    assert report.findings[0].severity == "warning"


# ---------------------------------------------------------------------------
# Prompt block rendering
# ---------------------------------------------------------------------------

def test_to_prompt_block_zh_contains_all_finding_tags() -> None:
    entries = [
        _vol_entry(1, goal="a" * 40, obstacle="b" * 40, phase="survival", force="X"),
        _vol_entry(2, goal="a" * 40, obstacle="b" * 40, phase="survival", force="X"),
    ]
    report = scan_volume_plan_for_convergence(entries)
    block = report.to_prompt_block(language="zh-CN")
    assert "【卷间内容趋同" in block
    assert "❗关键" in block or "⚠️提示" in block
    assert "重复使用的卷标签" in block


def test_to_prompt_block_en_format() -> None:
    entries = [
        _vol_entry(1, goal="a" * 40, obstacle="b" * 40, phase="survival", force="X"),
        _vol_entry(2, goal="a" * 40, obstacle="b" * 40, phase="survival", force="X"),
    ]
    report = scan_volume_plan_for_convergence(entries)
    block = report.to_prompt_block(language="en")
    assert "[Volume-level convergence" in block
    assert "Over-used volume tags" in block


def test_to_prompt_block_empty_report_returns_empty() -> None:
    report = VolumeConvergenceReport(findings=(), conflict_phase_counts={}, force_name_counts={})
    assert report.to_prompt_block() == ""


# ---------------------------------------------------------------------------
# Prior-volumes summary block
# ---------------------------------------------------------------------------

def test_prior_summary_zh_renders_all_prior_volumes() -> None:
    prior = [
        _vol_entry(1, title="逆命入局", goal="进入仙门并站稳脚跟",
                   phase="survival", force="白河派外门执事"),
        _vol_entry(2, title="灰楼开门", goal="打开灰楼还原真相",
                   phase="political_intrigue", force="灰楼禁卒"),
    ]
    block = render_prior_volumes_summary_block(prior, current_volume_number=3)
    assert "已写定的前序卷概要" in block
    assert "逆命入局" in block
    assert "灰楼开门" in block
    assert "差异化硬约束" in block
    assert "survival" in block
    assert "political_intrigue" in block


def test_prior_summary_en_renders_english() -> None:
    prior = [
        _vol_entry(1, title="Opening Gambit", goal="Enter the sect and establish footing",
                   phase="survival", force="Outer gate elder"),
    ]
    block = render_prior_volumes_summary_block(prior, current_volume_number=2, language="en")
    assert "Prior volume summary" in block
    assert "Opening Gambit" in block
    assert "Differentiation HARD CONSTRAINT" in block


def test_prior_summary_excludes_current_and_future_volumes() -> None:
    entries = [
        _vol_entry(1, title="A", goal="goal A"),
        _vol_entry(2, title="B", goal="goal B"),
        _vol_entry(3, title="C", goal="goal C"),
        _vol_entry(4, title="D", goal="goal D"),
    ]
    block = render_prior_volumes_summary_block(entries, current_volume_number=3)
    assert "A" in block and "B" in block
    assert "D" not in block  # future volume
    # Current volume (3) not itself listed as "prior"
    assert "第3卷" not in block


def test_prior_summary_returns_empty_when_no_priors() -> None:
    assert render_prior_volumes_summary_block([], current_volume_number=1) == ""
    # Current is first volume — nothing is prior.
    entries = [_vol_entry(1, title="A", goal="g")]
    assert render_prior_volumes_summary_block(entries, current_volume_number=1) == ""


def test_prior_summary_caps_at_configured_limit() -> None:
    entries = [_vol_entry(i, title=f"V{i}", goal=f"goal {i} content" * 5) for i in range(1, 30)]
    block = render_prior_volumes_summary_block(entries, current_volume_number=30, cap=5)
    # Only the last 5 prior volumes should appear
    for i in range(1, 25):
        assert f"第{i}卷" not in block
    for i in range(25, 30):
        assert f"第{i}卷" in block


# ---------------------------------------------------------------------------
# Real scenario: every volume converges on "survival via ally rescue"
# ---------------------------------------------------------------------------

def test_detects_series_wide_convergence_across_five_volumes() -> None:
    """Simulates the user-reported pattern: every volume ends up being
    'survive a crisis, get rescued by ally, gain new tier'."""
    template_goal = "在绝境中求生并获得突破"
    template_obstacle = "强敌压境、内部背叛、资源枯竭"
    template_climax = "盟友赶来相救并配合反杀"
    template_resolution = "突破新境界并吸纳残部"
    entries = [
        _vol_entry(
            i,
            title=f"V{i}",
            goal=template_goal,
            obstacle=template_obstacle,
            climax=template_climax,
            resolution=template_resolution,
            phase="survival",
            force=f"敌对势力{i}",
        )
        for i in range(1, 6)
    ]
    report = scan_volume_plan_for_convergence(entries)
    # Every pair (C(5,2)=10) registers as convergent via per-field
    # matching, PLUS one synthetic tag-overuse critical for the
    # repeated conflict_phase. Total = 11.
    pair_findings = [f for f in report.findings if f.volume_a != 0]
    assert len(pair_findings) == 10
    assert all(f.severity in {"critical", "warning"} for f in report.findings)
    # Over-used conflict_phase should be flagged.
    assert report.conflict_phase_counts["survival"] == 5
    # ...and escalated to a critical pattern finding.
    overuse_findings = [
        f for f in report.findings
        if "conflict_phase_overuse" in f.matched_fields
    ]
    assert len(overuse_findings) == 1
    assert overuse_findings[0].severity == "critical"


# ---------------------------------------------------------------------------
# B7 — per-field convergence & tag-overuse criticals
# ---------------------------------------------------------------------------

def test_per_field_goal_convergence_is_critical() -> None:
    """Two volumes with identical goals but different obstacles/climaxes
    are still critical — the goal beat is the strongest signal."""

    entries = [
        _vol_entry(
            1,
            goal="在绝境中求生并获得境界突破的关键机缘",
            obstacle="甲势力围剿主角的秘境探索队",
            climax="借助秘境灵脉反杀甲势力统领",
            phase="survival",
            force="甲势力",
        ),
        _vol_entry(
            2,
            goal="在绝境中求生并获得境界突破的关键机缘",
            obstacle="乙势力的海上舰队全面封锁海港",
            climax="主角点燃火船破开海上包围圈",
            phase="political_intrigue",
            force="乙势力",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    critical = [f for f in report.findings if f.severity == "critical"]
    assert critical
    assert any("volume_goal" in f.matched_fields for f in critical)


def test_per_field_obstacle_convergence_is_critical() -> None:
    entries = [
        _vol_entry(
            1,
            goal="夺回失落的家族信物以证清白",
            obstacle="门中派系、秘境禁制、长老误会三重压力并起",
            climax="在雷劫之中破局",
            phase="survival",
            force="门中反派",
        ),
        _vol_entry(
            2,
            goal="揭开前世旧债并向师门复命",
            obstacle="门中派系、秘境禁制、长老误会三重压力并起",
            climax="在最后一刻揭穿幕后黑手",
            phase="betrayal",
            force="幕后黑手",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    critical = [f for f in report.findings if f.severity == "critical"]
    assert critical
    assert any("volume_obstacle" in f.matched_fields for f in critical)


def test_per_field_short_text_below_floor_not_scored() -> None:
    """Very short per-field text must not trigger field-level findings."""

    entries = [
        _vol_entry(
            1, goal="短", obstacle="短", climax="短",
            phase="survival", force="A" * 30,
        ),
        _vol_entry(
            2, goal="短", obstacle="短", climax="短",
            phase="betrayal", force="B" * 30,
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    # The per-field text is below _MIN_FIELD_LEN (12 chars) so no
    # field-level finding can fire. No exact-match tags, either.
    # Should produce no findings.
    field_findings = [
        f for f in report.findings
        if f.matched_fields and "volume_goal" in f.matched_fields
    ]
    assert not field_findings


def test_phase_overuse_count_three_is_critical() -> None:
    """When conflict_phase repeats across ≥3 volumes, escalate to a
    plan-wide critical finding."""

    entries = [
        _vol_entry(
            i,
            goal=f"独立且差异明显的卷{i}主线目标描写内容",
            obstacle=f"卷{i}的障碍完全独立",
            climax=f"卷{i}的高潮与其他卷不重合",
            resolution=f"卷{i}的结局独立",
            phase="survival",
            force=f"独立势力{i}",
        )
        for i in range(1, 4)
    ]
    report = scan_volume_plan_for_convergence(entries)
    overuse = [
        f for f in report.findings
        if "conflict_phase_overuse" in f.matched_fields
    ]
    assert len(overuse) == 1
    assert overuse[0].severity == "critical"
    assert report.has_critical


def test_force_overuse_count_three_is_critical() -> None:
    entries = [
        _vol_entry(
            i,
            goal=f"独立且差异明显的卷{i}主线目标描写内容",
            obstacle=f"卷{i}的障碍完全独立",
            climax=f"卷{i}的高潮与其他卷不重合",
            phase=f"phase_{i}",
            force="白河派老贼",
        )
        for i in range(1, 4)
    ]
    report = scan_volume_plan_for_convergence(entries)
    overuse = [
        f for f in report.findings
        if "primary_force_name_overuse" in f.matched_fields
    ]
    assert len(overuse) == 1
    assert overuse[0].severity == "critical"


def test_phase_overuse_count_two_is_not_critical() -> None:
    """Two volumes sharing a phase is a pair-level warning, not a
    plan-wide pattern critical."""

    entries = [
        _vol_entry(
            1,
            goal="独立且差异明显的卷1主线目标描写内容",
            obstacle="卷1的障碍完全独立",
            climax="卷1的高潮与其他卷不重合",
            phase="survival",
            force="独立势力A",
        ),
        _vol_entry(
            2,
            goal="独立且差异明显的卷2主线目标描写内容",
            obstacle="卷2的障碍完全独立",
            climax="卷2的高潮与其他卷不重合",
            phase="survival",
            force="独立势力B",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    overuse = [
        f for f in report.findings
        if "conflict_phase_overuse" in f.matched_fields
    ]
    assert not overuse


def test_recalibrated_default_thresholds_catch_template_shape() -> None:
    """Template volumes that score 0.35-0.55 Jaccard (below the old
    warning bar of 0.45 but above the new 0.35) should now fire."""

    entries = [
        _vol_entry(
            1,
            goal="进入甲城并在城中站稳脚跟建立地下势力网",
            obstacle="甲城守军对外来人的严密盘查",
            climax="借助一场商战击垮甲城反对势力",
            theme="势力扩张",
        ),
        _vol_entry(
            2,
            goal="进入乙城并在城中站稳脚跟建立地下势力网",
            obstacle="乙城守军对外来人的严密盘查",
            climax="借助一场斗法击垮乙城反对势力",
            theme="势力扩张",
        ),
    ]
    report = scan_volume_plan_for_convergence(entries)
    # Either the per-field goal/obstacle/climax check or the combined
    # similarity must catch this — these volumes are obviously the
    # same template with swapped location names.
    assert report.findings
    assert any(f.severity == "critical" for f in report.findings)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_scan_is_deterministic() -> None:
    entries = [
        _vol_entry(1, goal="goal one is quite unique" * 3, phase="survival"),
        _vol_entry(2, goal="goal two is totally separate" * 3, phase="betrayal"),
        _vol_entry(3, goal="goal one is quite unique" * 3, phase="survival"),
    ]
    a = scan_volume_plan_for_convergence(entries)
    b = scan_volume_plan_for_convergence(entries)
    assert a.findings == b.findings
    assert a.to_prompt_block() == b.to_prompt_block()

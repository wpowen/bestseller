"""Tests for the cross-chapter plan fingerprint / dedup gate."""

from __future__ import annotations

import pytest

from bestseller.services.plan_fingerprint import (
    ChapterFingerprint,
    DEFAULT_CRITICAL_THRESHOLD,
    DEFAULT_WARNING_THRESHOLD,
    DuplicationFinding,
    FingerprintScanReport,
    build_chapter_fingerprint,
    find_near_duplicate_chapters,
    scan_batch_for_duplicates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_chapter(
    chapter_number: int,
    *,
    main_conflict: str,
    hook_type: str = "shock",
    hook_description: str = "",
    chapter_goal: str = "",
    scene_purposes: list[str] | None = None,
) -> dict:
    scenes = [
        {"scene_number": i + 1, "purpose": {"story": p}}
        for i, p in enumerate(scene_purposes or [])
    ]
    return {
        "chapter_number": chapter_number,
        "main_conflict": main_conflict,
        "hook_type": hook_type,
        "hook_description": hook_description,
        "chapter_goal": chapter_goal,
        "scenes": scenes,
    }


# ---------------------------------------------------------------------------
# build_chapter_fingerprint
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_fingerprint_extracts_all_fields():
    ch = _make_chapter(
        5,
        main_conflict="林风闯入禁地抢夺核心灵石",
        hook_type="shock",
        hook_description="阵眼激活",
        chapter_goal="突破浮标封锁",
        scene_purposes=["林风破阵取石", "长老追击林风"],
    )
    fp = build_chapter_fingerprint(ch)
    assert isinstance(fp, ChapterFingerprint)
    assert fp.chapter_number == 5
    assert fp.hook_type == "shock"
    assert "林风" in fp.combined_text
    assert "禁地" in fp.combined_text
    assert len(fp.scene_story_purposes) == 2


@pytest.mark.unit
def test_build_fingerprint_handles_missing_fields():
    fp = build_chapter_fingerprint({"chapter_number": 1})
    assert fp.chapter_number == 1
    assert fp.hook_type == ""
    assert fp.combined_text == ""
    assert fp.scene_story_purposes == ()


@pytest.mark.unit
def test_build_fingerprint_from_object_like_orm_row():
    class _Row:
        chapter_number = 7
        main_conflict = "The protagonist confronts the traitor in the council hall"
        hook_type = "revelation"
        hook_description = "The map was a forgery"
        chapter_goal = "Expose the traitor"
        scenes: list = []
    fp = build_chapter_fingerprint(_Row())
    assert fp.chapter_number == 7
    assert fp.hook_type == "revelation"
    assert "traitor" in fp.combined_text.lower()


@pytest.mark.unit
def test_build_fingerprint_lowercases_hook_type():
    fp = build_chapter_fingerprint({
        "chapter_number": 1,
        "hook_type": "SHOCK",
        "main_conflict": "x" * 50,
    })
    assert fp.hook_type == "shock"


# ---------------------------------------------------------------------------
# find_near_duplicate_chapters — intra-batch
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_identical_chapters_flagged_critical():
    ch_a = _make_chapter(
        1,
        main_conflict="林风闯入禁地抢夺核心灵石并触发巡守阵眼",
        scene_purposes=["林风破解浮标封锁", "长老组织巡守追击"],
    )
    ch_b = _make_chapter(
        5,
        main_conflict="林风闯入禁地抢夺核心灵石并触发巡守阵眼",
        scene_purposes=["林风破解浮标封锁", "长老组织巡守追击"],
    )
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(fps)
    assert report.has_critical
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.chapter_a == 1 and finding.chapter_b == 5
    assert finding.severity == "critical"
    assert finding.similarity >= DEFAULT_CRITICAL_THRESHOLD


@pytest.mark.unit
def test_totally_different_chapters_pass():
    ch_a = _make_chapter(
        1,
        main_conflict="林风在浮标阵中夺得核心灵石并逃离禁地",
        hook_type="shock",
        scene_purposes=["林风触发阵眼", "林风闪避长老追击"],
    )
    ch_b = _make_chapter(
        2,
        main_conflict="苏璃在拍卖会上识破伪造的阵盘并揭穿买家身份",
        hook_type="revelation",
        scene_purposes=["苏璃验看阵盘", "苏璃当众揭穿买家"],
    )
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(fps)
    assert not report.has_critical
    # Could still have a tiny incidental overlap — but no findings above threshold
    assert len(report.findings) == 0


@pytest.mark.unit
def test_warning_tier_between_thresholds():
    # Two chapters that partially overlap but are not identical
    shared = "林风潜入仓库夺取关键符纸"
    ch_a = _make_chapter(
        1,
        main_conflict=f"{shared}，然后被追到断崖",
        scene_purposes=[shared, "林风跳崖脱身"],
    )
    ch_b = _make_chapter(
        8,
        main_conflict=f"{shared}，接着在客栈被围",
        scene_purposes=[shared, "林风伪装脱身"],
    )
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(
        fps,
        warning_threshold=0.3,
        critical_threshold=0.95,
    )
    assert report.findings
    assert all(f.severity == "warning" for f in report.findings)
    assert not report.has_critical


@pytest.mark.unit
def test_max_chapter_distance_respected():
    """Chapters too far apart should be skipped when max_chapter_distance is set."""
    ch_a = _make_chapter(1, main_conflict="林风破解浮标封锁抢夺灵石", scene_purposes=["阵眼爆发"])
    ch_b = _make_chapter(100, main_conflict="林风破解浮标封锁抢夺灵石", scene_purposes=["阵眼爆发"])
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(fps, max_chapter_distance=10)
    assert report.findings == ()


@pytest.mark.unit
def test_short_texts_skipped():
    """Tiny main_conflicts should not produce Jaccard noise."""
    ch_a = _make_chapter(1, main_conflict="打斗")
    ch_b = _make_chapter(2, main_conflict="打斗")
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(fps)
    assert report.findings == ()


# ---------------------------------------------------------------------------
# matched_fields explanation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_matched_fields_includes_hook_type_when_equal():
    ch_a = _make_chapter(
        1,
        main_conflict="林风破解浮标封锁并夺走核心灵石",
        hook_type="shock",
        scene_purposes=["林风触发阵眼"],
    )
    ch_b = _make_chapter(
        2,
        main_conflict="林风破解浮标封锁并夺走核心灵石",
        hook_type="shock",
        scene_purposes=["林风触发阵眼"],
    )
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(fps)
    assert report.findings
    finding = report.findings[0]
    assert any("hook_type" in m for m in finding.matched_fields)


@pytest.mark.unit
def test_reason_field_populated():
    ch_a = _make_chapter(
        1, main_conflict="林风闯入禁地抢夺核心灵石", scene_purposes=["阵眼激活"]
    )
    ch_b = _make_chapter(
        2, main_conflict="林风闯入禁地抢夺核心灵石", scene_purposes=["阵眼激活"]
    )
    fps = [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    report = find_near_duplicate_chapters(fps)
    assert report.findings
    assert report.findings[0].reason  # non-empty


# ---------------------------------------------------------------------------
# FingerprintScanReport.to_prompt_block
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_prompt_block_chinese_format():
    ch_a = _make_chapter(
        1, main_conflict="林风闯入禁地抢夺核心灵石", scene_purposes=["阵眼激活"]
    )
    ch_b = _make_chapter(
        2, main_conflict="林风闯入禁地抢夺核心灵石", scene_purposes=["阵眼激活"]
    )
    report = find_near_duplicate_chapters(
        [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    )
    block = report.to_prompt_block(language="zh-CN")
    assert "章节指纹" in block
    assert "第1章" in block
    assert "第2章" in block


@pytest.mark.unit
def test_prompt_block_english_format():
    ch_a = _make_chapter(
        1, main_conflict="Lin breaches the array and seizes the spirit core stone", scene_purposes=["the array eye activates"]
    )
    ch_b = _make_chapter(
        2, main_conflict="Lin breaches the array and seizes the spirit core stone", scene_purposes=["the array eye activates"]
    )
    report = find_near_duplicate_chapters(
        [build_chapter_fingerprint(ch_a), build_chapter_fingerprint(ch_b)]
    )
    block = report.to_prompt_block(language="en")
    assert "near-duplicate" in block
    assert "ch1" in block and "ch2" in block


@pytest.mark.unit
def test_empty_report_produces_empty_block():
    report = FingerprintScanReport(findings=())
    assert report.to_prompt_block() == ""


# ---------------------------------------------------------------------------
# scan_batch_for_duplicates
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_scan_batch_merges_intra_and_cross_batch_findings():
    # Within batch: ch 1 and ch 2 are similar
    # Cross batch: ch 1 also matches existing ch 99
    similar_conflict = "林风在浮标阵中夺得核心灵石并逃离禁地"
    batch = [
        _make_chapter(1, main_conflict=similar_conflict, scene_purposes=["阵眼激活"]),
        _make_chapter(2, main_conflict=similar_conflict, scene_purposes=["阵眼激活"]),
        _make_chapter(3, main_conflict="苏璃在拍卖会识破伪造阵盘并揭穿买家"),
    ]

    class _Row:
        def __init__(self, n, conflict, hook_type="shock", scenes=None):
            self.chapter_number = n
            self.main_conflict = conflict
            self.hook_type = hook_type
            self.hook_description = ""
            self.chapter_goal = ""
            self.scenes = scenes or []

    existing = [_Row(99, similar_conflict, scenes=[{"purpose": {"story": "阵眼激活"}}])]
    report = scan_batch_for_duplicates(batch, existing)
    # At minimum: one intra-batch finding (1 ↔ 2) and two cross-batch (1 ↔ 99, 2 ↔ 99)
    pairs = {(f.chapter_a, f.chapter_b) for f in report.findings}
    assert (1, 2) in pairs
    assert (1, 99) in pairs or (99, 1) in pairs
    assert (2, 99) in pairs or (99, 2) in pairs


@pytest.mark.unit
def test_scan_batch_with_no_existing_chapters_runs_intra_only():
    similar_conflict = "林风在浮标阵中夺得核心灵石并逃离禁地"
    batch = [
        _make_chapter(1, main_conflict=similar_conflict, scene_purposes=["阵眼激活"]),
        _make_chapter(2, main_conflict=similar_conflict, scene_purposes=["阵眼激活"]),
    ]
    report = scan_batch_for_duplicates(batch, [])
    pairs = {(f.chapter_a, f.chapter_b) for f in report.findings}
    assert (1, 2) in pairs


@pytest.mark.unit
def test_scan_batch_empty_batch_returns_empty_report():
    report = scan_batch_for_duplicates([], [])
    assert report.findings == ()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_scan_is_deterministic():
    batch = [
        _make_chapter(1, main_conflict="林风闯入禁地抢夺核心灵石", scene_purposes=["阵眼激活"]),
        _make_chapter(2, main_conflict="林风闯入禁地抢夺核心灵石", scene_purposes=["阵眼激活"]),
    ]
    r1 = scan_batch_for_duplicates(batch, [])
    r2 = scan_batch_for_duplicates(batch, [])
    assert r1 == r2


@pytest.mark.unit
def test_default_thresholds_sane():
    assert 0 < DEFAULT_WARNING_THRESHOLD < DEFAULT_CRITICAL_THRESHOLD <= 1.0

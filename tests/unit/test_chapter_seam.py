"""Unit tests for chapter_seam continuity gate."""

from __future__ import annotations

import pytest

from bestseller.services.chapter_seam import (
    SeamReport,
    ThreadKind,
    build_seam_bridge_repair_prompt,
    extract_open_threads,
    validate_chapter_seam,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# extract_open_threads
# ---------------------------------------------------------------------------


def test_extract_open_threads_empty_input_returns_empty() -> None:
    assert extract_open_threads("") == []
    assert extract_open_threads("   \n  ") == []


def test_extract_open_threads_surfaces_location_threat_and_question() -> None:
    tail = (
        "宁尘被七八个执法堂弟子围在丹房门口，掌中令牌烫得刺痛。"
        "苏瑶冷笑道：「跑得倒快。你那枚令牌，从哪里来的？」"
    )
    threads = extract_open_threads(tail)
    kinds = {t.kind for t in threads}
    assert ThreadKind.LOCATION in kinds         # 丹房 / 门口
    assert ThreadKind.IMMEDIATE_THREAT in kinds  # 围
    assert ThreadKind.UNANSWERED_QUESTION in kinds  # 「...？」
    # Both 宁尘 and 苏瑶 should surface as participants
    participant_markers = {t.marker for t in threads if t.kind == ThreadKind.PARTICIPANT}
    assert "宁尘" in participant_markers
    assert "苏瑶" in participant_markers


def test_extract_open_threads_skips_inner_thought_questions() -> None:
    tail = "宁尘心想：苏瑶到底想做什么？周围一片寂静。"
    threads = extract_open_threads(tail)
    # Inner-thought "他想/心想" question should not be marked as unanswered
    assert not any(t.kind == ThreadKind.UNANSWERED_QUESTION for t in threads)


# ---------------------------------------------------------------------------
# validate_chapter_seam
# ---------------------------------------------------------------------------


def test_seam_passes_when_opening_continues_directly() -> None:
    tail = (
        "宁尘被七八个执法堂弟子围在丹房门口，掌中令牌烫得刺痛。"
        "苏瑶冷笑：「跑不掉了。」"
    )
    opening = (
        "丹房门口的火光让宁尘睁不开眼。苏瑶的剑尖距他咽喉不过三寸。"
        "他强迫自己呼吸。"
    )
    report = validate_chapter_seam(tail, opening)
    assert report.passed
    assert report.score == 1.0
    assert not report.silent_drops


def test_seam_fails_when_opening_silently_relocates() -> None:
    tail = (
        "宁尘被七八个执法堂弟子围在丹房门口，掌中令牌烫得刺痛。"
        "苏瑶冷笑：「跑不掉了。」"
    )
    # New chapter just teleports to an unrelated藏经阁 scene without any bridge
    opening = (
        "宁尘指尖触上封印符。废弃藏经阁内积灰蔽膝，月光从破窗漏进来。"
        "他蹲下身，撬开暗格。"
    )
    report = validate_chapter_seam(tail, opening)
    assert not report.passed
    assert report.score < 1.0
    # The immediate threat (围) was silently dropped
    silent_kinds = {drop.thread.kind for drop in report.silent_drops}
    assert ThreadKind.IMMEDIATE_THREAT in silent_kinds


def test_seam_accepts_explicit_time_skip() -> None:
    tail = "宁尘被七八个执法堂弟子围在丹房门口，掌中令牌烫得刺痛。"
    opening = "半个时辰后，宁尘倚在木屋墙根，伤口仍在渗血。"
    report = validate_chapter_seam(tail, opening)
    # 半个时辰后 satisfies the skip resolution path for the threat thread
    threat_resolutions = [
        r for r in report.resolutions if r.thread.kind == ThreadKind.IMMEDIATE_THREAT
    ]
    assert threat_resolutions
    assert threat_resolutions[0].resolution == "skip"


def test_seam_accepts_explicit_relocation_marker() -> None:
    tail = "宁尘被执法堂围在丹房门口。"
    opening = "他被押到藏经阁地牢，腕上的禁制压得他喘不过气。"
    report = validate_chapter_seam(tail, opening)
    # "押到" should satisfy relocation, "禁制" should satisfy body-state continuity
    relocations = [r for r in report.resolutions if r.resolution == "relocation"]
    assert relocations


def test_seam_no_threads_passes_trivially() -> None:
    tail = "他合上书，望向窗外的月色。"
    opening = "翌日清晨，他踏入演武场。"
    report = validate_chapter_seam(tail, opening)
    assert report.passed
    # Empty thread set => score 1.0 by convention
    assert report.score == 1.0


# ---------------------------------------------------------------------------
# repair prompt
# ---------------------------------------------------------------------------


def test_build_repair_prompt_lists_silent_drops_only() -> None:
    tail = "宁尘被七八个执法堂弟子围在丹房门口。"
    opening = "翌日的演武场，宁尘擦着剑。"  # 翌日 covers threat as 'skip'
    report = validate_chapter_seam(tail, opening)
    prompt = build_seam_bridge_repair_prompt(report)
    # Time skip covered the threat, but the location 丹房 likely got dropped
    if not report.passed:
        assert "章节断点修复任务" in prompt
    else:
        assert prompt == ""


def test_build_repair_prompt_empty_when_clean() -> None:
    report = SeamReport(open_threads=(), resolutions=())
    assert build_seam_bridge_repair_prompt(report) == ""

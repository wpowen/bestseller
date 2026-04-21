"""Tests for the narrative-lines gate.

Locks in the four-layer contract (明线 / 暗线 / 隐藏线 / 核心轴) that
prevents the observed production failure mode of single-layer plots
where every volume collapses to a rotating-antagonist template.
"""

from __future__ import annotations

import pytest

from bestseller.services.narrative_lines import (
    CANONICAL_LINES,
    CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO,
    HIDDEN_THREAD_MIN_VOLUME_SPAN_RATIO,
    LINE_CORE_AXIS,
    LINE_HIDDEN,
    LINE_OVERT,
    LINE_UNDERCURRENT,
    OVERT_LINE_MAX_VOLUMES_PER_ARC,
    OVERT_LINE_MIN_ARCS_FLOOR,
    UNDERCURRENT_MIN_VOLUME_SPAN,
    render_narrative_lines_constraints_block,
    scan_narrative_lines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _healthy_lines(volume_count: int = 10) -> dict:
    """A four-layer spec that passes all gates for a 10-volume book."""

    # Overt: 4 rotating arcs (V1-2, V3-5, V6-8, V9-10) — no arc > 3 vols.
    overt = [
        {"name": "幼年困境", "volumes": [1, 2], "antagonist_ref": "village_elder"},
        {"name": "外门试炼", "volumes": [3, 4, 5], "antagonist_ref": "sect_rival"},
        {"name": "盟会动荡", "volumes": [6, 7, 8], "antagonist_ref": "alliance_traitor"},
        {"name": "终局对决", "volumes": [9, 10], "antagonist_ref": "final_boss"},
    ]
    # Undercurrent: one arc spanning V2-V9 (8 volumes)
    undercurrent = [
        {
            "name": "影手幕后",
            "start_volume": 2,
            "end_volume": 9,
            "antagonist_ref": "shadow_hand",
        },
    ]
    # Hidden thread spans V1 → V10
    hidden = {
        "statement": "主角血脉源自上古封印的禁忌之物",
        "seed_volumes": [1, 2],
        "payoff_volumes": [9, 10],
    }
    core_axis = {
        "statement": "力量增长的代价是什么",
        "phrasing_tokens": ["代价", "力量", "增长"],
    }
    return {
        "overt_line": overt,
        "undercurrent_line": undercurrent,
        "hidden_thread": hidden,
        "core_axis": core_axis,
    }


def _healthy_volume_plan(volume_count: int = 10) -> list[dict]:
    """A volume plan where each volume's theme echoes the core axis."""

    return [
        {
            "volume_number": i + 1,
            "volume_title": f"第{i + 1}卷",
            "volume_theme": "主角思索力量增长的代价",
            "core_axis_reference": "代价",
            "chapter_count_target": 15,
        }
        for i in range(volume_count)
    ]


# ---------------------------------------------------------------------------
# Canonical list
# ---------------------------------------------------------------------------

def test_canonical_lines_present():
    assert set(CANONICAL_LINES) == {
        LINE_OVERT, LINE_UNDERCURRENT, LINE_HIDDEN, LINE_CORE_AXIS,
    }


# ---------------------------------------------------------------------------
# Healthy plan passes
# ---------------------------------------------------------------------------

def test_healthy_narrative_lines_pass():
    spec = _healthy_lines(10)
    vp = _healthy_volume_plan(10)
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10, volume_plan=vp
    )
    assert report.critical_count == 0
    assert report.is_critical is False
    assert report.has_overt
    assert report.has_undercurrent
    assert report.has_hidden_thread
    assert report.has_core_axis
    assert report.core_axis_reference_ratio == 1.0


# ---------------------------------------------------------------------------
# Missing layers — all critical
# ---------------------------------------------------------------------------

def test_missing_overt_line_critical():
    spec = _healthy_lines(10)
    spec["overt_line"] = []
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "missing_overt_line" in codes
    assert report.is_critical is True


def test_missing_undercurrent_line_critical():
    spec = _healthy_lines(10)
    spec["undercurrent_line"] = []
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "missing_undercurrent_line" in codes


def test_missing_hidden_thread_critical():
    spec = _healthy_lines(10)
    spec["hidden_thread"] = {}
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "missing_hidden_thread" in codes


def test_missing_core_axis_critical():
    spec = _healthy_lines(10)
    spec["core_axis"] = {}
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "missing_core_axis" in codes


# ---------------------------------------------------------------------------
# Starved overt arcs — the canonical 道种破虚 failure mode
# ---------------------------------------------------------------------------

def test_starved_overt_arcs_daozhongpoxu_case():
    """24 volumes but only 1 overt arc → every volume looks identical."""

    spec = {
        "overt_line": [
            {"name": "元婴老者威压", "volumes": list(range(1, 25)),
             "antagonist_ref": "yuanying_elder"},
        ],
        "undercurrent_line": [
            {"name": "影门", "start_volume": 5, "end_volume": 20},
        ],
        "hidden_thread": {
            "statement": "原初道种",
            "seed_volumes": [1],
            "payoff_volumes": [24],
        },
        "core_axis": {"statement": "破虚之道"},
    }
    report = scan_narrative_lines(
        spec, total_chapters=316, volume_count=24
    )
    codes = {f.code for f in report.findings}
    assert "starved_overt_line_arcs" in codes
    # Single arc spans 24 volumes → far above OVERT_LINE_MAX_VOLUMES_PER_ARC
    assert "overt_arc_too_wide" in codes


# ---------------------------------------------------------------------------
# Shallow undercurrent
# ---------------------------------------------------------------------------

def test_shallow_undercurrent_critical():
    """Undercurrent only spanning 2 volumes is indistinguishable from an
    overt arc."""

    spec = _healthy_lines(10)
    spec["undercurrent_line"] = [
        {"name": "短暗线", "start_volume": 5, "end_volume": 6},
    ]
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "shallow_undercurrent_line" in codes


# ---------------------------------------------------------------------------
# Shallow hidden thread
# ---------------------------------------------------------------------------

def test_shallow_hidden_thread_critical():
    """Hidden thread that only touches V5-V7 doesn't span the book."""

    spec = _healthy_lines(10)
    spec["hidden_thread"] = {
        "statement": "某个秘密",
        "seed_volumes": [5],
        "payoff_volumes": [7],
    }
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "shallow_hidden_thread" in codes


# ---------------------------------------------------------------------------
# Weak core axis threading
# ---------------------------------------------------------------------------

def test_weak_core_axis_threading_critical():
    """If the volume plan never references the core_axis, warn."""

    spec = _healthy_lines(10)
    vp = [
        {"volume_number": i + 1, "volume_theme": "普通冒险故事"}
        for i in range(10)
    ]
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10, volume_plan=vp
    )
    codes = {f.code for f in report.findings}
    assert "weak_core_axis_threading" in codes


def test_core_axis_referenced_via_phrasing_token():
    """Volume themes that include a token from core_axis.phrasing_tokens
    count as referenced."""

    spec = _healthy_lines(10)
    spec["core_axis"]["phrasing_tokens"] = ["代价"]
    vp = [
        {"volume_number": i + 1, "volume_theme": "主角探究力量的代价"}
        for i in range(10)
    ]
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10, volume_plan=vp
    )
    codes = {f.code for f in report.findings}
    assert "weak_core_axis_threading" not in codes
    assert report.core_axis_reference_ratio == 1.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_payload_yields_all_critical():
    report = scan_narrative_lines(
        {}, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "missing_overt_line" in codes
    assert "missing_undercurrent_line" in codes
    assert "missing_hidden_thread" in codes
    assert "missing_core_axis" in codes
    assert report.is_critical


def test_accepts_pydantic_like_payload():
    class _Fake:
        def model_dump(self):
            return _healthy_lines(10)

    report = scan_narrative_lines(
        _Fake(),
        total_chapters=150,
        volume_count=10,
        volume_plan=_healthy_volume_plan(10),
    )
    assert report.critical_count == 0


def test_small_book_does_not_penalise_undercurrent_span():
    """A 3-volume novella should not fail the undercurrent-span rule
    just because the book is shorter than the span floor."""

    spec = _healthy_lines(3)
    spec["overt_line"] = [
        {"name": "A", "volumes": [1]},
        {"name": "B", "volumes": [2]},
        {"name": "C", "volumes": [3]},
    ]
    spec["undercurrent_line"] = [
        {"name": "短书暗线", "start_volume": 1, "end_volume": 3},
    ]
    spec["hidden_thread"] = {
        "statement": "短书秘密",
        "seed_volumes": [1],
        "payoff_volumes": [3],
    }
    report = scan_narrative_lines(
        spec, total_chapters=45, volume_count=3
    )
    codes = {f.code for f in report.findings}
    # 3-vol book can't span 4 vols, so undercurrent shallow check must
    # be suppressed (volume_count < floor).
    assert "shallow_undercurrent_line" not in codes


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_report_to_prompt_block_empty_when_healthy():
    spec = _healthy_lines(10)
    vp = _healthy_volume_plan(10)
    report = scan_narrative_lines(
        spec, total_chapters=150, volume_count=10, volume_plan=vp
    )
    assert report.to_prompt_block() == ""


def test_report_to_prompt_block_lists_findings():
    report = scan_narrative_lines(
        {}, total_chapters=150, volume_count=10
    )
    block = report.to_prompt_block(language="zh-CN")
    assert block
    assert "明线" in block or "overt" in block.lower()
    assert "暗线" in block or "undercurrent" in block.lower()


def test_render_constraints_block_zh():
    block = render_narrative_lines_constraints_block(
        total_chapters=316, volume_count=24, language="zh-CN"
    )
    assert "316" in block
    assert "24" in block
    assert "明线" in block
    assert "暗线" in block
    assert "隐藏线" in block
    assert "核心轴" in block


def test_render_constraints_block_en():
    block = render_narrative_lines_constraints_block(
        total_chapters=544, volume_count=16, language="en-US"
    )
    assert "544" in block
    assert "16" in block
    assert "overt" in block.lower()
    assert "undercurrent" in block.lower()
    assert "hidden_thread" in block.lower()
    assert "core_axis" in block.lower()


def test_render_constraints_block_handles_single_volume():
    block = render_narrative_lines_constraints_block(
        total_chapters=30, volume_count=1, language="zh-CN"
    )
    # Must not crash on div-by-zero or negative ranges.
    assert block

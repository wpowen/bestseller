"""Tests for the linear hierarchy architecture: Act → Volume → Arc → Chapter.

Covers:
- compute_linear_hierarchy() parametric tests across all scales
- _fallback_act_plan() structure validation
- Arc boundary computation
- Arc phase within arc
- Backward compatibility for small novels (≤50 chapters)
- Ending contract constraints
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services import planner as planner_services
from bestseller.services.planner import (
    _compute_chapter_arc_info,
    _fallback_act_plan,
    _phase_name_within_arc,
    compute_linear_hierarchy,
)
from bestseller.infra.db.models import ProjectModel


pytestmark = pytest.mark.unit


def _build_project(target_chapters: int = 12, language: str = "zh") -> ProjectModel:
    project = ProjectModel(
        slug="test-hierarchy",
        title="测试项目",
        genre="science-fantasy",
        target_word_count=target_chapters * 3000,
        target_chapters=target_chapters,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    project.language = language
    return project


# ── compute_linear_hierarchy ──────────────────────────────────────────


@pytest.mark.parametrize(
    "total_chapters,expected_act,expected_vol",
    [
        (4, 1, 1),
        (6, 1, 1),
        (11, 1, 1),
        (16, 1, 1),
        (30, 1, 1),
        (50, 1, 1),
        (54, 3, 2),
        (100, 3, 3),
        (108, 3, 4),
        (120, 3, 4),
        (180, 4, 4),
        (272, 4, 6),
        (1000, 5, 20),
        (2000, 6, 40),
    ],
)
def test_compute_linear_hierarchy_parametric(
    total_chapters: int, expected_act: int, expected_vol: int,
) -> None:
    result = compute_linear_hierarchy(total_chapters)
    assert result["act_count"] == expected_act
    assert result["volume_count"] == expected_vol
    assert result["arc_batch_size"] == 12


def test_compute_linear_hierarchy_always_returns_positive() -> None:
    for n in range(1, 2001):
        h = compute_linear_hierarchy(n)
        assert h["act_count"] >= 1
        assert h["volume_count"] >= 1
        assert h["arc_batch_size"] >= 1


# ── Backward compatibility: ≤50 chapters ─────────────────────────────


def test_backward_compat_12_chapters() -> None:
    """12-chapter project should behave identically to the old system."""
    project = _build_project(12)
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    assert len(volume_plan) == 1
    assert volume_plan[0]["volume_number"] == 1
    assert volume_plan[0]["chapter_count_target"] == 12


def test_backward_compat_30_chapters() -> None:
    """30-chapter project: single volume, single act."""
    project = _build_project(30)
    h = compute_linear_hierarchy(30)
    assert h["act_count"] == 1
    assert h["volume_count"] == 1


# ── _fallback_act_plan ────────────────────────────────────────────────


def test_fallback_act_plan_1000_chapters() -> None:
    """1000-chapter novel should produce 5 acts with complete structure."""
    project = _build_project(1000)
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    act_plan = _fallback_act_plan(project, book_spec, cast_spec, world_spec)
    assert len(act_plan) == 5

    # All chapters covered without gaps or overlaps
    covered = set()
    for act in act_plan:
        for ch in range(act["chapter_start"], act["chapter_end"] + 1):
            assert ch not in covered, f"Chapter {ch} covered by multiple acts"
            covered.add(ch)
    assert covered == set(range(1, 1001))

    # Each act has required fields
    for act in act_plan:
        assert "act_id" in act
        assert "act_index" in act
        assert "title" in act
        assert "chapter_start" in act
        assert "chapter_end" in act
        assert "act_goal" in act
        assert "core_theme" in act
        assert "dominant_emotion" in act
        assert "climax_chapter" in act
        assert "entry_state" in act
        assert "exit_state" in act
        assert "payoff_promises" in act
        assert "arc_breakdown" in act

    # Last act should be marked final
    last_act = act_plan[-1]
    assert last_act["is_final_act"] is True
    assert "resolution_contract" in last_act

    # First act should not be final
    assert act_plan[0]["is_final_act"] is False


def test_fallback_act_plan_english() -> None:
    """Act plan should work in English mode too."""
    project = _build_project(100, language="en")
    premise = "A banished navigator discovers the Empire is tampering with border records."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    act_plan = _fallback_act_plan(project, book_spec, cast_spec, world_spec)
    assert len(act_plan) == 3  # 100 chapters → 3 acts
    assert "Act 1:" in act_plan[0]["title"]


def test_fallback_act_plan_contiguous() -> None:
    """Acts must be contiguous: no gaps between act_end and next act_start."""
    for total in [54, 100, 180, 272, 1000, 2000]:
        project = _build_project(total)
        premise = "Test premise."
        book_spec = planner_services._fallback_book_spec(project, premise)
        world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
        cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

        act_plan = _fallback_act_plan(project, book_spec, cast_spec, world_spec)
        for i in range(len(act_plan) - 1):
            assert act_plan[i]["chapter_end"] + 1 == act_plan[i + 1]["chapter_start"], (
                f"Gap between acts {i} and {i+1} for {total} chapters"
            )
        # First act starts at 1, last act ends at total
        assert act_plan[0]["chapter_start"] == 1
        assert act_plan[-1]["chapter_end"] == total


# ── Volume plan arc_ranges ───────────────────────────────────────────


def test_volume_plan_has_arc_ranges() -> None:
    """Volume plan should include arc_ranges for multi-volume novels."""
    project = _build_project(100)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    for vol in volume_plan:
        assert "arc_ranges" in vol
        assert isinstance(vol["arc_ranges"], list)
        assert len(vol["arc_ranges"]) >= 1
        for arc_range in vol["arc_ranges"]:
            assert len(arc_range) == 2
            assert arc_range[0] <= arc_range[1]


def test_arc_ranges_cover_all_chapters() -> None:
    """Arc ranges across all volumes should cover every chapter exactly once."""
    project = _build_project(100)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    covered = set()
    for vol in volume_plan:
        for arc_start, arc_end in vol["arc_ranges"]:
            for ch in range(arc_start, arc_end + 1):
                assert ch not in covered, f"Chapter {ch} covered by multiple arcs"
                covered.add(ch)
    assert covered == set(range(1, 101))


def test_volume_plan_is_final_volume() -> None:
    """Only the last volume should have is_final_volume=True."""
    project = _build_project(100)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    assert len(volume_plan) >= 2
    for vol in volume_plan[:-1]:
        assert vol.get("is_final_volume") is False
    assert volume_plan[-1]["is_final_volume"] is True


# ── _phase_name_within_arc ───────────────────────────────────────────


def test_phase_name_within_arc_distribution() -> None:
    """Phase names should cover the full arc progression."""
    phases = [_phase_name_within_arc(i, 12) for i in range(12)]
    assert phases[0] == "hook"
    assert "setup" in phases
    assert "escalation" in phases
    assert "twist" in phases
    assert "climax" in phases
    assert phases[-1] == "resolution_hook"


def test_phase_name_within_arc_small_arc() -> None:
    """Small arcs (1-3 chapters) should still produce valid phases."""
    assert _phase_name_within_arc(0, 1) == "hook"
    assert _phase_name_within_arc(0, 2) == "hook"
    assert _phase_name_within_arc(1, 2) == "escalation"


# ── Chapter outline arc_index and arc_phase ──────────────────────────


def test_chapter_outline_has_arc_info() -> None:
    """Each chapter in the outline should have arc_index and arc_phase."""
    project = _build_project(100)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

    for ch in outline["chapters"]:
        assert "arc_index" in ch, f"Chapter {ch['chapter_number']} missing arc_index"
        assert "arc_phase" in ch, f"Chapter {ch['chapter_number']} missing arc_phase"
        assert ch["arc_phase"] in {"hook", "setup", "escalation", "twist", "climax", "resolution_hook"}


def test_compute_chapter_arc_info_consistency() -> None:
    """_compute_chapter_arc_info should match arc_ranges in the volume plan."""
    project = _build_project(100)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    # Build expected arc assignments
    expected_arc_by_chapter: dict[int, int] = {}
    global_arc_idx = 0
    for vol in volume_plan:
        for arc_start, arc_end in vol["arc_ranges"]:
            for ch in range(arc_start, arc_end + 1):
                expected_arc_by_chapter[ch] = global_arc_idx
            global_arc_idx += 1

    # Verify _compute_chapter_arc_info agrees
    for ch_num in range(1, 101):
        arc_index, _ = _compute_chapter_arc_info(ch_num, volume_plan)
        assert arc_index == expected_arc_by_chapter[ch_num], (
            f"Chapter {ch_num}: expected arc {expected_arc_by_chapter[ch_num]}, got {arc_index}"
        )


# ── Ending contract ──────────────────────────────────────────────────


def test_ending_contract_last_3_chapters() -> None:
    """Last 3 chapters should have forced scene goals for ending."""
    project = _build_project(100)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

    chapters = outline["chapters"]
    ch98 = next(ch for ch in chapters if ch["chapter_number"] == 98)
    ch99 = next(ch for ch in chapters if ch["chapter_number"] == 99)
    ch100 = next(ch for ch in chapters if ch["chapter_number"] == 100)

    # Chapter 98 (3rd from end): convergence
    assert "伏笔汇聚" in ch98["goal"] or "converge" in ch98["goal"].lower()
    # Chapter 99 (2nd from end): ultimate confrontation
    assert "终极对决" in ch99["goal"] or "ultimate" in ch99["goal"].lower() or "揭晓" in ch99["goal"]
    # Chapter 100 (last): resolution
    assert "结局" in ch100["goal"] or "resolution" in ch100["goal"].lower() or "着陆" in ch100["goal"]


def test_ending_contract_small_novel() -> None:
    """Small novels (12 chapters) should also get ending goals on last 3."""
    project = _build_project(12)
    premise = "Test premise."
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

    chapters = outline["chapters"]
    ch12 = next(ch for ch in chapters if ch["chapter_number"] == 12)
    assert "结局" in ch12["goal"] or "resolution" in ch12["goal"].lower() or "着陆" in ch12["goal"]


# ── Act plan prompts ─────────────────────────────────────────────────


def test_act_plan_prompts_zh() -> None:
    """Chinese act plan prompts should be generated correctly."""
    project = _build_project(100)
    book_spec = {"title": "测试"}
    world_spec = {"rules": []}
    cast_spec = {"protagonist": {"name": "主角"}}

    system_prompt, user_prompt = planner_services._act_plan_prompts(
        project, book_spec, world_spec, cast_spec,
    )
    assert "幕" in user_prompt
    assert "act_id" in user_prompt
    assert "3" in user_prompt  # 100 chapters → 3 acts


def test_act_plan_prompts_en() -> None:
    """English act plan prompts should be generated correctly."""
    project = _build_project(100, language="en")
    book_spec = {"title": "Test"}
    world_spec = {"rules": []}
    cast_spec = {"protagonist": {"name": "Hero"}}

    system_prompt, user_prompt = planner_services._act_plan_prompts(
        project, book_spec, world_spec, cast_spec,
    )
    assert "Acts" in user_prompt or "Act" in user_prompt
    assert "act_id" in user_prompt


# ── Volume plan prompts with act context ─────────────────────────────


def test_volume_plan_prompts_with_act_plan() -> None:
    """Volume plan prompts should include act plan context when provided."""
    project = _build_project(100)
    book_spec = {"title": "测试"}
    world_spec = {"rules": []}
    cast_spec = {"protagonist": {"name": "主角"}}
    act_plan = [{"act_id": "act_01", "act_index": 0, "core_theme": "觉醒"}]

    _, user_prompt = planner_services._volume_plan_prompts(
        project, book_spec, world_spec, cast_spec, act_plan=act_plan,
    )
    assert "幕计划" in user_prompt or "ActPlan" in user_prompt
    assert "觉醒" in user_prompt


def test_volume_plan_prompts_without_act_plan() -> None:
    """Volume plan prompts should work without act plan (backward compat)."""
    project = _build_project(12)
    book_spec = {"title": "测试"}
    world_spec = {"rules": []}
    cast_spec = {"protagonist": {"name": "主角"}}

    _, user_prompt = planner_services._volume_plan_prompts(
        project, book_spec, world_spec, cast_spec,
    )
    assert "幕计划" not in user_prompt
    assert "ActPlan" not in user_prompt

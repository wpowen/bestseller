"""Outline title soft-fill + golden-three field backfill."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.services.planner import (
    PlannerFallbackError,
    _derive_chapter_title_seed,
    _derive_information_held_back_seed,
    _normalize_generated_outline_titles_or_fail,
    _soft_fill_golden_three_outline_fields,
)

pytestmark = pytest.mark.unit

# Regression guard: these two sentences were a hardcoded, all-books-identical
# "held back" placeholder that satisfied the outline existence gate without
# giving the writer a real per-book obligation (2026-07-09 remediation
# review). Soft-fill must derive from real per-book fields (dramatic_question
# / a later chapter's stated conflict) or leave the field empty — never fall
# back to boilerplate prose again.
_BANNED_HELD_BACK_TEMPLATES = (
    "金手指完整规则与最终代价尚未向读者摊开",
    "幕后真正目标仍被压在后续章",
)
_BANNED_OBJECT_USES_TEMPLATE = "产生状态变化"


def test_derive_title_from_goal_clause() -> None:
    title = _derive_chapter_title_seed(
        {
            "goal": "姜炙以一勺最素的豆腐喂瘫灶根叔，保住临时掌勺位置",
        }
    )
    assert "豆腐" in title or "姜炙" in title
    assert len(title) <= 12


def test_normalize_titles_soft_fills_from_goal() -> None:
    chapters = [
        {"chapter_number": 1, "goal": "黑铁锅认主，杂役开灶"},
        {"chapter_number": 2, "title": "噬嗔藕片"},
    ]
    _normalize_generated_outline_titles_or_fail(chapters, logical_name="test_outline")
    assert chapters[0]["title"]
    assert chapters[1]["title"] == "噬嗔藕片"
    assert chapters[0].get("_meta", {}).get("title_soft_filled") is True


def test_normalize_titles_still_fails_without_seed() -> None:
    chapters = [{"chapter_number": 3}]
    with pytest.raises(PlannerFallbackError, match="omitted concrete chapter titles"):
        _normalize_generated_outline_titles_or_fail(chapters, logical_name="empty")


def test_soft_fill_golden_three_fields() -> None:
    scene = SimpleNamespace(
        purpose={"story": "开灶试菜"},
        concrete_goal=None,
        object_signal=None,
        sensory_anchors={"signature_image": "豆腐自翻"},
        methodology_contract={},
        hook_requirement="菜名浮出",
        exit_state={"summary": "菜名浮出"},
    )
    chapter = SimpleNamespace(
        chapter_number=1,
        tail_hook=None,
        hook_description="白案司宣读成瘾榜",
        required_payoff=None,
        chapter_object_uses=None,
        world_asset_refs=["黑铁锅"],
        world_rule_refs=[],
        world_rule_landing="豆腐自翻→忘姓",
        chapter_information_introduced=None,
        key_reveals=["成瘾榜机制"],
        chapter_information_held_back=None,
        scenes=[scene],
    )
    batch = SimpleNamespace(chapters=[chapter])
    n = _soft_fill_golden_three_outline_fields(batch)
    assert n >= 4
    assert chapter.tail_hook
    assert chapter.chapter_object_uses
    assert chapter.chapter_information_introduced
    assert scene.concrete_goal == "开灶试菜"
    assert scene.object_signal == "豆腐自翻"


def test_soft_fill_object_uses_skips_when_landing_empty() -> None:
    """No world_rule_landing → leave chapter_object_uses empty, not a hollow filler."""
    chapter = SimpleNamespace(
        chapter_number=2,
        tail_hook="已存在",
        hook_description=None,
        required_payoff=None,
        chapter_object_uses=None,
        world_asset_refs=["黑铁锅"],
        world_rule_refs=[],
        world_rule_landing=None,
        chapter_information_introduced=["已存在"],
        key_reveals=[],
        chapter_information_held_back=["已存在"],
        scenes=[],
    )
    batch = SimpleNamespace(chapters=[chapter])
    _soft_fill_golden_three_outline_fields(batch)
    assert not chapter.chapter_object_uses
    assert _BANNED_OBJECT_USES_TEMPLATE not in (chapter.chapter_object_uses or [])


def test_soft_fill_held_back_derives_from_dramatic_question() -> None:
    chapter = SimpleNamespace(
        chapter_number=1,
        tail_hook="已存在",
        hook_description=None,
        required_payoff=None,
        chapter_object_uses=["已存在"],
        world_asset_refs=[],
        world_rule_refs=[],
        world_rule_landing=None,
        chapter_information_introduced=[],
        key_reveals=[],
        chapter_information_held_back=None,
        main_conflict=None,
        scenes=[],
    )
    batch = SimpleNamespace(chapters=[chapter])
    project = SimpleNamespace(dramatic_question="灶根叔的断指到底是谁下的手？")
    _soft_fill_golden_three_outline_fields(batch, project=project)
    assert chapter.chapter_information_held_back == ["灶根叔的断指到底是谁下的手？"]
    for banned in _BANNED_HELD_BACK_TEMPLATES:
        assert banned not in chapter.chapter_information_held_back


def test_soft_fill_held_back_derives_from_later_chapter_conflict() -> None:
    ch1 = SimpleNamespace(
        chapter_number=1,
        tail_hook="已存在",
        hook_description=None,
        required_payoff=None,
        chapter_object_uses=["已存在"],
        world_asset_refs=[],
        world_rule_refs=[],
        world_rule_landing=None,
        chapter_information_introduced=[],
        key_reveals=[],
        chapter_information_held_back=None,
        main_conflict=None,
        scenes=[],
    )
    ch2 = SimpleNamespace(
        chapter_number=2,
        main_conflict="灶根叔的旧账主找上门",
        hook_description=None,
    )
    batch = SimpleNamespace(chapters=[ch1, ch2])
    _soft_fill_golden_three_outline_fields(batch)
    chapter_held_back = ch1.chapter_information_held_back
    assert chapter_held_back
    assert "灶根叔的旧账主找上门" in chapter_held_back
    for banned in _BANNED_HELD_BACK_TEMPLATES:
        assert banned not in chapter_held_back


def test_soft_fill_held_back_stays_empty_without_real_source() -> None:
    """No dramatic_question, no later chapter, nothing to derive → stay empty.

    This is the deliberate trade-off: leaving the field empty means the
    outline existence gate (``_require_outline_systemic_fields_or_raise``)
    still fires and forces a real repair, instead of the old hardcoded
    template silently satisfying it.
    """
    chapter = SimpleNamespace(
        chapter_number=1,
        tail_hook="已存在",
        hook_description=None,
        required_payoff=None,
        chapter_object_uses=["已存在"],
        world_asset_refs=[],
        world_rule_refs=[],
        world_rule_landing=None,
        chapter_information_introduced=[],
        key_reveals=[],
        chapter_information_held_back=None,
        main_conflict=None,
        scenes=[],
    )
    batch = SimpleNamespace(chapters=[chapter])
    _soft_fill_golden_three_outline_fields(batch)
    assert not chapter.chapter_information_held_back


def test_derive_information_held_back_seed_direct() -> None:
    chapter = SimpleNamespace(chapter_number=1, chapter_information_introduced=[])
    assert _derive_information_held_back_seed(chapter, [chapter], None) == []
    assert _derive_information_held_back_seed(chapter, [chapter], "  ") == []
    assert _derive_information_held_back_seed(chapter, [chapter], "核心悬念") == ["核心悬念"]


def test_banned_held_back_templates_absent_from_planner_source() -> None:
    """Static regression: the removed hardcoded sentences must never come back."""
    source = (Path(__file__).resolve().parents[2] / "src/bestseller/services/planner.py").read_text(
        encoding="utf-8"
    )
    for banned in _BANNED_HELD_BACK_TEMPLATES:
        assert banned not in source, f"hardcoded held-back template leaked back in: {banned}"

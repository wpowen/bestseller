"""Unit tests for intra-chapter stitched-draft detection."""

from __future__ import annotations

import pytest

from bestseller.services.deduplication import (
    build_stitched_draft_repair_prompt,
    detect_intra_chapter_stitched_drafts,
)

pytestmark = pytest.mark.unit


def test_clean_chapter_yields_no_findings() -> None:
    chapter = """
宁尘推开木门，月光洒在桌上的灵草丸上。他坐下来，开始打坐。
丹田深处那枚金色种子缓缓搏动，像一颗心脏。他闭上眼，感受灵气流入经脉的轨迹。

---

第二日清晨，陆沉敲门而入。"配给真的取消了？"
宁尘点头，没有抬眼。陆沉把一瓶聚气散放在桌上："周霸最近在找你。"
"""
    assert detect_intra_chapter_stitched_drafts(chapter) == []


def test_two_drafts_of_same_暗格_scene_are_detected() -> None:
    """The exact failure mode observed in 道种破虚 chapter 2."""
    chapter = """
宁尘和陆沉来到废弃藏经阁。月光从破窗漏进来，照出墙角的暗格。陆沉用铁钎撬开符文，
暗格应声而开。里面躺着一本无字册子。宁尘伸手拿过，丹田深处的道种猛然震颤。
就在此时，苏瑶带着两个杂役从廊道转出。「小子，暗格里的东西，拿了没有？」她冷笑。
宁尘从袖口掏出一枚碎裂灵石递过去：「就这个。」苏瑶接过，嗤笑一声："滚。"

---

宁尘指尖触到那卷泛黄纸页，丹田深处骤然传来一声闷响。书架后传来脚步声——苏瑶
带两个杂役出现在廊道尽头。"管事，这里有块松动的地砖。"陆沉脱口而出。"撬开。"
地砖撬开，暗格里躺着一枚刻满符文的玉简。道种蠢蠢欲动，力量暴涨。
苏瑶没料到这个废物会出现："杂役峰的废物，竟敢闯——"话未说完，廊道骤然被金光吞没。
"""
    findings = detect_intra_chapter_stitched_drafts(chapter)
    assert findings, "应当检测到双稿拼接"
    f = findings[0]
    # Both blocks share 宁尘, 苏瑶, 陆沉
    shared = f.block_a.participants & f.block_b.participants
    assert {"宁尘", "苏瑶", "陆沉"}.issubset(shared)
    # Should surface prop conflicts (册子 vs 玉简)
    assert any("道具差异" in c for c in f.conflicts)


def test_distinct_scenes_with_one_shared_character_do_not_collide() -> None:
    """Two genuinely different scenes that happen to share the protagonist."""
    chapter = """
宁尘在藏经阁翻找古籍，桌上摊着一卷玉简。他指尖摩过符文，眉头紧锁。
窗外月光透进来，照亮了册子封面的阴阳图腾。他必须在天亮前找到答案。

---

练剑场上，宁尘对周霸出拳。剑风扫过他的肩膀，划出一道血痕。
周霸冷笑："废物果然是废物。"宁尘咬牙不语，第二拳已经打出。
两人僵持不下，韩九在远处冷眼旁观。
"""
    # Different participants (周霸/韩九 vs solo), different props (玉简/册子 vs 剑)
    # Should not be flagged as stitched
    findings = detect_intra_chapter_stitched_drafts(chapter)
    assert not findings


def test_repair_prompt_lists_block_pairs() -> None:
    chapter = """
宁尘和陆沉来到藏经阁深处，撬开地面的暗格，拿出一本无字册子。
苏瑶突然带着两个杂役现身，挡在他们面前。宁尘从袖口掏出一枚
碎灵石递了过去，蒙混过关。两人转身就走。道种在丹田深处剧烈
搏动，宁尘咬牙强压住吞噬冲动。陆沉在背后悄声叮嘱：「小心。」

---

宁尘再次摸到藏经阁，掀开地砖下另一处暗格，里面是一枚泛着金光的玉简。
苏瑶带着两个杂役突然赶到，将他堵在墙角。宁尘体内的道种猛然暴起，
一道金光掀飞两个杂役，震得苏瑶踉跄后退几步。陆沉在身后倒抽冷气，
伸手扶住摇晃的书架，呼吸都顿住了。
"""
    findings = detect_intra_chapter_stitched_drafts(chapter)
    assert findings
    prompt = build_stitched_draft_repair_prompt(findings)
    assert "拼接稿修复任务" in prompt
    assert "二选一" in prompt
    assert "禁止合并" in prompt

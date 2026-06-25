"""L1 unit tests for the pre-write reader-review assembler.

Covers: golden-finger classification, genericness smell-test (system flag +
no false positive on original engines), scene→storyboard translation (normal /
field-missing / hollow), cross-book sameness (no-dup / all-dup / partial
highlight), rhythm curve, and assembler no-op byte-stability.
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002 — Chinese punctuation in test fixtures is intentional.
import pytest

from bestseller.services.reader_review import (
    build_reader_review,
    build_rhythm_curve,
    build_sameness_table,
    classify_golden_finger_type,
    detect_genericness,
    render_chapter_storyboard,
    render_scene_beat,
)

# --- screen ② engine health -------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("绑定了一个签到系统，每日领取奖励", "系统流"),
        ("脑海中浮现出金色的属性面板", "系统流"),
        ("重生回到高考前一天，带着前世记忆", "重生流"),
        ("穿越到异世界成为废柴少爷", "穿越流"),
        ("戒指里住着一位上古大能的传承", "老爷爷流"),
        ("觉醒了上古神血血脉", "血脉觉醒流"),
        ("能听见所有人的心声", "读心/预知流"),
        ("他靠一手能让物体回到三秒前的能力翻盘", "原创"),
        ("", "未知"),
    ],
)
def test_classify_golden_finger_type(text: str, expected: str) -> None:
    assert classify_golden_finger_type(text) == expected


@pytest.mark.unit
def test_detect_genericness_flags_system() -> None:
    out = detect_genericness("一个会发布任务奖励的系统")
    assert out["golden_finger_type"] == "系统流"
    assert out["is_generic"] is True
    assert any(f["code"] == "GOLDEN_FINGER_IS_SYSTEM" for f in out["flags"])


@pytest.mark.unit
def test_detect_genericness_no_false_positive_on_original() -> None:
    out = detect_genericness(
        "能让任何谎言在他眼中显形的天赋",
        power_system={"tiers": ["见微", "察隐", "通幽", "证心"]},
    )
    assert out["golden_finger_type"] == "原创"
    assert out["is_generic"] is False
    assert out["flags"] == []


@pytest.mark.unit
def test_detect_genericness_falls_back_to_concept_text_for_system() -> None:
    # Real books often leave golden_finger empty; the system lives in the logline.
    out = detect_genericness(
        "", concept_text="社畜接手了一套来路不明的福报结算系统，好运会自动外溢给身边的人"
    )
    assert out["golden_finger_type"] == "系统流"
    assert out["inferred_from_concept"] is True
    assert any(f["code"] == "GOLDEN_FINGER_IS_SYSTEM" for f in out["flags"])


@pytest.mark.unit
def test_detect_genericness_concept_text_not_used_when_field_present() -> None:
    out = detect_genericness("能让谎言显形的原创天赋", concept_text="一套签到系统")
    assert out["golden_finger_type"] == "原创"
    assert out["inferred_from_concept"] is False


@pytest.mark.unit
def test_detect_genericness_flags_stock_cultivation_ladder() -> None:
    out = detect_genericness(
        "原创读心天赋", power_system={"tiers": ["练气", "筑基", "金丹", "元婴"]}
    )
    assert any(f["code"] == "POWER_SYSTEM_STOCK_LADDER" for f in out["flags"])


@pytest.mark.unit
def test_detect_genericness_accepts_power_system_as_string() -> None:
    out = detect_genericness("原创金手指", power_system="练气、筑基、金丹、元婴")
    assert any(f["code"] == "POWER_SYSTEM_STOCK_LADDER" for f in out["flags"])


# --- screen ③ storyboard ----------------------------------------------------


@pytest.mark.unit
def test_render_scene_beat_normal() -> None:
    scene = {
        "scene_number": 1,
        "title": "城门对峙",
        "scene_type": "action",
        "time_label": "黄昏",
        "participants": ["陈默", "守将"],
        "purpose": {"冲突": "主角硬闯被拦", "情绪": "压迫感"},
        "key_dialogue_beats": ["你敢动我试试"],
        "exit_state": {"位置": "城内"},
        "hook_requirement": "守将认出了主角的旧伤",
    }
    beat = render_scene_beat(scene, 0)
    assert beat["is_hollow"] is False
    assert "陈默" in beat["readable"]
    assert "城内" in beat["readable"]
    assert beat["hook"] == "守将认出了主角的旧伤"


@pytest.mark.unit
def test_render_scene_beat_hollow_is_flagged() -> None:
    scene = {"scene_number": 2, "scene_type": "prose", "participants": []}
    beat = render_scene_beat(scene, 1)
    assert beat["is_hollow"] is True
    assert "未指定出场人物" in beat["readable"]


@pytest.mark.unit
def test_render_chapter_storyboard_counts_hollow_and_asks_when_no_hook() -> None:
    chapter = {
        "chapter_number": 1,
        "title": "开端",
        "chapter_goal": "主角登场",
        "hook_description": "",
        "ending_cliff_type": "",
    }
    scenes = [
        {"scene_number": 1, "participants": ["A"], "purpose": {"x": "y"}},
        {"scene_number": 2},  # hollow
    ]
    out = render_chapter_storyboard(chapter, scenes)
    assert out["scene_count"] == 2
    assert out["hollow_scene_count"] == 1
    assert "没有任何结尾钩子" in out["reader_question"]


# --- screen ⑤ sameness ------------------------------------------------------


@pytest.mark.unit
def test_build_sameness_table_highlights_repeated_columns() -> None:
    books = [
        {
            "slug": "a",
            "title": "书A",
            "protagonist_archetype": "废柴逆袭",
            "golden_finger_type": "系统流",
            "opening_archetype": "羞辱",
            "power_system_signature": "练气筑基金丹",
            "ch1_hook_type": "退婚",
        },
        {
            "slug": "b",
            "title": "书B",
            "protagonist_archetype": "废柴逆袭",  # repeated
            "golden_finger_type": "系统流",  # repeated
            "opening_archetype": "穿越",
            "power_system_signature": "星力等级",
            "ch1_hook_type": "捡到神器",
        },
    ]
    table = build_sameness_table(books, current_slug="a")
    assert table["book_count"] == 2
    assert "废柴逆袭" in table["repeated_values"]["protagonist_archetype"]
    assert "系统流" in table["repeated_values"]["golden_finger_type"]
    current = next(r for r in table["rows"] if r["is_current"])
    assert "protagonist_archetype" in current["repeated_cells"]
    assert "opening_archetype" not in current["repeated_cells"]
    assert table["current_sameness_score"] == pytest.approx(2 / 5)


@pytest.mark.unit
def test_build_sameness_table_no_duplicates() -> None:
    books = [
        {"slug": "a", "protagonist_archetype": "侦探", "golden_finger_type": "原创"},
        {"slug": "b", "protagonist_archetype": "刺客", "golden_finger_type": "重生流"},
    ]
    table = build_sameness_table(books, current_slug="a")
    assert all(not vals for vals in table["repeated_values"].values())
    assert table["current_sameness_score"] == 0.0


@pytest.mark.unit
def test_build_sameness_ignores_empty_placeholder() -> None:
    # Two books both missing a field must NOT count as "repeated".
    books = [{"slug": "a"}, {"slug": "b"}]
    table = build_sameness_table(books, current_slug="a")
    assert table["repeated_values"]["protagonist_archetype"] == []


# --- screen ④ rhythm --------------------------------------------------------


@pytest.mark.unit
def test_build_rhythm_curve_coverage() -> None:
    chapters = [
        {"chapter_number": 1, "hype_type": "打脸", "hype_intensity": 0.8},
        {"chapter_number": 2, "hype_type": "", "hype_intensity": None},
    ]
    out = build_rhythm_curve(chapters)
    assert out["chapter_count"] == 2
    assert out["hype_coverage"] == pytest.approx(0.5)


# --- top-level assembler + no-op byte stability ------------------------------


@pytest.mark.unit
def test_build_reader_review_assembles_all_screens() -> None:
    out = build_reader_review(
        project={"slug": "demo", "title": "演示", "premise": "一句话", "genre": "玄幻"},
        golden_finger="签到系统",
        power_system={"tiers": ["练气", "筑基", "金丹"]},
        golden_three_chapters=[({"chapter_number": 1, "chapter_goal": "登场"}, [])],
        rhythm_chapters=[{"chapter_number": 1, "hype_intensity": 0.5}],
        cross_book_skeletons=[{"slug": "demo", "golden_finger_type": "系统流"}],
    )
    assert out["schema_version"] == "reader-review.v1"
    assert set(out) >= {
        "screen1_logline",
        "screen2_engine",
        "screen3_storyboard",
        "screen4_rhythm",
        "screen5_sameness",
    }
    assert out["screen2_engine"]["is_generic"] is True


@pytest.mark.unit
def test_build_reader_review_noop_on_empty_is_byte_stable() -> None:
    """未启用 / 无数据时必须产出稳定可复现的空结构（字节级不变）。"""
    a = build_reader_review()
    b = build_reader_review()
    assert a == b
    assert a["screen3_storyboard"] == []
    assert a["screen5_sameness"]["book_count"] == 0
    assert a["screen1_logline"]["arena_win_rate"] is None
    assert a["screen2_engine"]["golden_finger_type"] == "未知"

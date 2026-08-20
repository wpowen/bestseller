"""章纲的情绪与信息字段必须落库（2026-08-20 真机《罚我守坟》定罪）。

判官 132 份报告：style 0.815 但 **contract_alignment 0.250**，
全书质量门 43 项未过，码全是结构层的
（chapter_function_missing / chapter_hook_missing /
early_retention_hook_density_low / **emotion_visible_desire_gap**）。

顺着挖到章节契约：5 个实质字段里 `emotional_shift` **38/38 全空**、
`information_release` **38/38 与 closing_hook 逐字相同**。

⚠️ 但根因不是「规划层没产出」——**模型答得很好，是落库时丢了**：

    planning_artifact_versions 里存着
      target_emotion = "爽"
      chapter_information_introduced = [
        "碑纹之源挑人不是随机，是顺着账走",
        "温十娘的名字第一次出现",
        "何九秤被换进来不是因为他卖过菜，而是因为他今天赊过账"]

而 `chapters.primary_emotion` 0/38、`chapters.information_revealed` 0/38
（全 `[]`）、`chapter_emotion_arc` 全库 0/111。

真因是章纲→章节行的映射只搬 6 个字段
（chapter_goal / opening_situation / main_conflict / hook_type /
hook_description / target_word_count），`target_emotion` 与
`chapter_information_introduced` 不在其列，**算出来了却丢掉**——
与 [[computed-then-discarded-ceiling-ships-worst-draft]] 同形。
"""

from __future__ import annotations

import pytest

from bestseller.services.workflows import _sync_chapter_outline_semantics

pytestmark = pytest.mark.unit


class _Outline:
    target_emotion = "爽"
    chapter_information_introduced = [
        "碑纹之源挑人不是随机，是顺着账走",
        "温十娘的名字第一次出现",
    ]
    key_reveals: list[str] = []


class _Chapter:
    primary_emotion = None
    information_revealed: list[str] = []
    chapter_emotion_arc = None


def test_target_emotion_lands_on_the_chapter_row():
    ch = _Chapter()
    _sync_chapter_outline_semantics(ch, _Outline())
    assert ch.primary_emotion == "爽"


def test_information_introduced_lands_on_the_chapter_row():
    ch = _Chapter()
    _sync_chapter_outline_semantics(ch, _Outline())
    assert "温十娘的名字第一次出现" in ch.information_revealed


def test_key_reveals_is_the_fallback_source():
    class _O(_Outline):
        chapter_information_introduced: list[str] = []
        key_reveals = ["井底那层薄底是活的"]

    ch = _Chapter()
    _sync_chapter_outline_semantics(ch, _O())
    assert ch.information_revealed == ["井底那层薄底是活的"]


def test_empty_outline_does_not_clobber_existing_values():
    class _O:
        target_emotion = None
        chapter_information_introduced: list[str] = []
        key_reveals: list[str] = []

    ch = _Chapter()
    ch.primary_emotion = "悬"
    ch.information_revealed = ["旧值"]
    _sync_chapter_outline_semantics(ch, _O())
    assert ch.primary_emotion == "悬"
    assert ch.information_revealed == ["旧值"]


def test_primary_emotion_is_truncated_to_column_width():
    class _O(_Outline):
        target_emotion = "爽" * 40  # varchar(32)

    ch = _Chapter()
    _sync_chapter_outline_semantics(ch, _O())
    assert len(ch.primary_emotion) <= 32


def test_both_materialization_paths_are_wired():
    """新建章节与同步既有章节两条路都要接线——只接一条是本项目反复的元病。"""
    import inspect

    from bestseller.services import workflows

    src = inspect.getsource(workflows)
    assert src.count("_sync_chapter_outline_semantics(") >= 2


# ── 落库之后还要真的进章节契约 ──────────────────────────────────────────
# 只落 chapters 列不够：契约构造处 emotional_shift 读的是 chapter_emotion_arc
# （全库恒空），information_release 读的是 hook_description（与 closing_hook
# 同源）。两处都要改成优先用刚落库的真实值。


def test_contract_emotional_shift_falls_back_to_primary_emotion():
    from bestseller.services.narrative import _contract_emotional_shift

    class _C:
        chapter_emotion_arc = None
        primary_emotion = "爽"

    assert _contract_emotional_shift(_C()) == "爽"


def test_contract_emotional_shift_prefers_a_real_arc():
    from bestseller.services.narrative import _contract_emotional_shift

    class _C:
        chapter_emotion_arc = "从憋屈到扬眉"
        primary_emotion = "爽"

    assert _contract_emotional_shift(_C()) == "从憋屈到扬眉"


def test_contract_information_release_uses_reveals_not_the_hook():
    from bestseller.services.narrative import _contract_information_release

    class _C:
        information_revealed = ["温十娘的名字第一次出现", "碑纹顺着账走"]
        hook_description = "纹尖指向戒律堂"

    out = _contract_information_release(_C())
    assert "温十娘的名字第一次出现" in out
    assert out != "纹尖指向戒律堂", "信息释放不得再与收尾钩子同源"


def test_contract_information_release_falls_back_to_hook_when_empty():
    from bestseller.services.narrative import _contract_information_release

    class _C:
        information_revealed: list[str] = []
        hook_description = "纹尖指向戒律堂"

    assert _contract_information_release(_C()) == "纹尖指向戒律堂"

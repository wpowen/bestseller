"""有 brief 时，让故事从 brief 长出来，而不是长在框架的分类里。

九条「破题路线」（人际困局／世界规则／成长道路／世界扩张／势力选择／身份变化／
职业处境／资源分配／纯题材直觉）是框架自定的 taxonomy，而候选是**固定轮转**分配
的：候选1 人际困局、候选2 世界规则、候选3 成长道路……不管用户选了什么。

用户选轻松＋喜剧＋爽感时，「世界规则」「势力选择」「资源分配」这些天然偏体系、
偏权谋、偏零和的路线照样占掉一半候选，生成的概念必然沉重，判官再正确地判它们
不喜剧、不想点。用加权去救仍然是在这九个桶里做选择——框架还是在规定故事写什么。

用户 2026-07-30：「所谓的世界规则、资源分配、势力等这些，我们不需要去做严格的
限定吧？要通过大模型自然去生长起来。」

**但差异性不能一起丢掉。** 现在候选之间的不同完全靠那九个标签撑着；六个候选拿
同一份 brief 会退化成同一个故事的六种措辞——2026-07-28《东方玄幻》正是这样，
六个候选全是「杂役掏沟挖出戴木镯的腕骨」，6/6 挂在新颖度上。

所以差异性换个来源：**不规定故事写什么，只要求候选之间在故事自身的维度上真的
不同**（主角位置／异常来源／压力来源／关系结构／舞台）。这些是区分约束，不是
题材指令。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


class TestABriefReplacesTheTaxonomy:
    def test_a_brief_driven_axis_exists(self) -> None:
        assert hasattr(ct, "_GROWTH_DIFFERENTIATION_AXES")

    def test_the_axes_are_story_dimensions_not_subject_categories(self) -> None:
        """区分主角位置是约束；规定写资源分配是指令。两者不能混。"""

        axes = set(ct._GROWTH_DIFFERENTIATION_AXES)
        for imposed in ("世界规则", "资源分配", "势力选择", "人际困局"):
            assert imposed not in axes, f"{imposed} 是题材指令，不该当区分轴用"

    def test_dimensions_come_from_the_axes_when_a_brief_is_present(self) -> None:
        source = inspect.getsource(ct.run_concept_tournament)
        assert "_GROWTH_DIFFERENTIATION_AXES" in source


class TestTheGeneratorIsToldToGrowNotToFillABucket:
    def test_a_growth_axis_renders_as_a_difference_constraint(self) -> None:
        _system, user = ct._build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="自然生长#主角的社会位置",
            chapter_count=50,
            tone_preference="light",
        )
        assert "主角的社会位置" in user
        assert "忘掉框架术语" in user, "边界应回到纯题材直觉，而不是某个分类的边界"

    def test_a_framework_lane_still_works_for_seeded_refinement(self) -> None:
        """种子打磨路径不变——它本来就该顺着既有故事走。

        2026-08-02：轴的边界改成正向描述（"从什么起步"），不再附"不要写契约、
        债务或仲裁"这类禁令——禁令进 prompt 本身就是注入。
        """

        _system, user = ct._build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="人际困局", chapter_count=50
        )
        assert "人际困局" in user
        assert "从一段无法轻易割舍的人际关系和两难选择起步" in user
        assert "不要写契约、债务或仲裁" not in user


class TestVarietyIsPreserved:
    def test_enough_axes_to_differentiate_a_full_candidate_batch(self) -> None:
        """默认 6 个、无种子时 16 个——轴不够就会退化成同一个故事的多种措辞。"""

        assert len(ct._GROWTH_DIFFERENTIATION_AXES) >= 6

    def test_axes_are_distinct(self) -> None:
        axes = list(ct._GROWTH_DIFFERENTIATION_AXES)
        assert len(axes) == len(set(axes))


class TestGrowthAppliesToBareGenreToo:
    """「只选一个题材、其他都不选」也必须自然生长。

    2026-07-30 实测：只选题材时 ``_creation_intent_prompt_block`` 返回空串（那是
    它的「无选择契约」——不替用户注入任何东西，本身是对的），于是 ``_has_brief``
    为假，候选又回落到九条框架路线。而这恰恰是用户最初的场景：

    「就比如说我只选一个题材，只选一个选项，只是单单选一个题材也应该能生成。」

    自然生长的触发条件应当是「从零生成」而不是「有没有 brief」：没有 brief 时
    更没有理由让框架替用户决定故事写什么。
    """

    def test_growth_is_not_conditional_on_a_brief(self) -> None:
        source = inspect.getsource(ct.run_concept_tournament)
        assert "_has_brief" not in source, (
            "自然生长不该以 brief 为条件——只选题材时更需要它"
        )

    def test_fresh_generation_no_longer_rotates_framework_lanes(self) -> None:
        """九条框架路线不再参与从零生成。"""

        source = inspect.getsource(ct.run_concept_tournament)
        assert "_NATIVE_STORY_LANES[index" not in source
        assert "_GROWTH_DIFFERENTIATION_AXES" in source

    def test_the_lane_briefs_survive_for_seeded_paths(self) -> None:
        """词表本身保留：种子打磨仍可能落到某条框架路线上。"""

        assert ct._NATIVE_STORY_LANE_BRIEFS["人际困局"]

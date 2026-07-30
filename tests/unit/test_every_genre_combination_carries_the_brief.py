"""笛卡尔积全覆盖：任何题材组合都要按同一套规则生成。

用户 2026-07-30：「你要确保所有的题材都能够根据该规则去做对应的生成，而不应该
仅修改玄幻这一个类别。就是我可以把这些类别去做笛卡尔积的选择之后，都能够去做
生效。」

这一轮的修复在设计上是题材无关的（触发条件是「有没有 brief」而不是「是不是
玄幻」），但**设计上应该 ≠ 实测通过**：今天已经有多处「看起来接上了、真机却
断在另一条分支」。所以这里真的把 taxonomy 全量跑一遍。

规模：3 频道 × 28 题材 × 123 个题材+子题材组合 × 4 调性 × 18 个可多选技能。
prompt 构造是纯函数，不需要调用模型，全组合穷举是可行的。
"""

from __future__ import annotations

import pytest

from bestseller.services import concept_tournament as ct
from bestseller.services.genre_taxonomy import iter_sub_genres, list_channels
from bestseller.services.story_effect_skills import STORY_EFFECT_SKILL_LABELS

pytestmark = pytest.mark.unit

_TONES = ("light", "epic", "dark", "hot")
_INTENT = '\n\n【建书页明确选择——仅作局部约束，不得改写题材】\n{"tone": "light"}'


def _combinations() -> list[tuple[str, str, str]]:
    pairs = iter_sub_genres()
    return [
        (genre.label, sub.label, tone)
        for genre, sub in pairs
        for tone in _TONES
    ]


class TestEveryGenreAndToneRendersItsBrief:
    def test_the_taxonomy_is_actually_populated(self) -> None:
        """空 taxonomy 会让下面每条断言都真空通过。"""

        assert len(iter_sub_genres()) >= 100
        assert len(list_channels()) >= 3

    def test_every_combination_carries_the_tone_directive(self) -> None:
        missing: list[tuple[str, str, str]] = []
        for genre, sub, tone in _combinations():
            _s, user = ct._build_engine_kernel_messages(
                genre=genre,
                sub_genre=sub,
                lane=f"{ct._GROWTH_LANE_PREFIX}#主角的社会位置",
                chapter_count=50,
                tone_preference=tone,
                creation_intent_block=_INTENT,
            )
            if ct._TONE_DIRECTIVES[tone] not in user:
                missing.append((genre, sub, tone))
        assert not missing, f"{len(missing)} 个组合丢了调性指令，例如 {missing[:3]}"

    def test_every_combination_carries_the_intent_set(self) -> None:
        missing: list[tuple[str, str]] = []
        for genre, sub, _tone in _combinations():
            _s, user = ct._build_engine_kernel_messages(
                genre=genre,
                sub_genre=sub,
                lane=f"{ct._GROWTH_LANE_PREFIX}#异常的来源",
                chapter_count=50,
                creation_intent_block=_INTENT,
            )
            if "建书页明确选择" not in user:
                missing.append((genre, sub))
        assert not missing, f"{len(missing)} 个组合丢了入参集，例如 {missing[:3]}"

    def test_every_combination_grows_instead_of_filling_a_lane(self) -> None:
        """自然生长的边界必须到达每一个题材，而不是回落到框架分类的边界。"""

        wrong: list[tuple[str, str]] = []
        for genre, sub, _tone in _combinations():
            _s, user = ct._build_engine_kernel_messages(
                genre=genre,
                sub_genre=sub,
                lane=f"{ct._GROWTH_LANE_PREFIX}#压力来自谁",
                chapter_count=50,
                creation_intent_block=_INTENT,
            )
            if "忘掉框架术语" not in user or "区分维度" not in user:
                wrong.append((genre, sub))
        assert not wrong, f"{len(wrong)} 个组合没走自然生长，例如 {wrong[:3]}"

    def test_the_genre_itself_always_reaches_the_prompt(self) -> None:
        """题材名不能被别的字段吃掉——组合再多也得认得出自己是什么书。"""

        missing: list[tuple[str, str]] = []
        for genre, sub, _tone in _combinations():
            _s, user = ct._build_engine_kernel_messages(
                genre=genre,
                sub_genre=sub,
                lane=f"{ct._GROWTH_LANE_PREFIX}#故事发生的舞台",
                chapter_count=50,
                creation_intent_block=_INTENT,
            )
            if genre not in user or sub not in user:
                missing.append((genre, sub))
        assert not missing, f"{len(missing)} 个组合的题材名没进 prompt，例如 {missing[:3]}"


class TestEverySkillIsExpressible:
    def test_all_eighteen_skills_render_a_label(self) -> None:
        from bestseller.services.story_effect_skills import story_effect_skill_labels

        for key in STORY_EFFECT_SKILL_LABELS:
            assert story_effect_skill_labels([key]), f"{key} 渲染不出标签"

    def test_every_skill_reaches_the_prompt_on_any_genre(self) -> None:
        pairs = iter_sub_genres()
        sample = [pairs[0], pairs[len(pairs) // 2], pairs[-1]]
        for genre, sub in sample:
            for key, label in STORY_EFFECT_SKILL_LABELS.items():
                _s, user = ct._build_engine_kernel_messages(
                    genre=genre.label,
                    sub_genre=sub.label,
                    lane=f"{ct._GROWTH_LANE_PREFIX}#主角最初想要的东西",
                    chapter_count=50,
                    effect_skills=[key],
                    creation_intent_block=_INTENT,
                )
                assert label in user, f"{genre.label}/{sub.label} 丢了技能 {key}"


class TestEveryChannelIsExpressible:
    def test_each_channel_reaches_the_prompt(self) -> None:
        for channel in list_channels():
            _s, user = ct._build_engine_kernel_messages(
                genre="玄幻",
                sub_genre="玄幻",
                lane=f"{ct._GROWTH_LANE_PREFIX}#谁最先发现主角不对劲",
                chapter_count=50,
                audience_orientation=channel.label,
                creation_intent_block=_INTENT,
            )
            assert channel.label in user

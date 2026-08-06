"""建书页选的调性和故事技能必须到达概念生成。

用户的原话："男频玄幻，题材不限，风格比较轻松，拥有极致的爽感，偏喜剧。"
把这段给任何大模型都能给出可用设定——框架却干涸了。

原因：``_creation_intent_prompt_block``（承载 tone / effect_skills / brainhole
等全部建书页勾选）只被 ``_commercial_brief_prompt_block`` 使用，而那是**商业定位
brief**，供市场／角色／世界观那批 agent。淘汰赛跑在 Round -1，**早于它们**，
拿到的只有：

    genre, sub_genre, chapter_count, audience_orientation, cost_style, seed_concept

于是 轻松 / 喜剧 / 爽点满足 三项从未到达概念生成。真机取证
（2026-07-29 玄幻）四个候选全是沉重压抑的路子——村塾先生识海钉活字、炭灰描侄儿
魂魄、自请禁业呈文——判官再正确地判它们「不想点、不好懂」，干涸，书死。用户的
要求从头到尾没有被任何模型看见。

这也解释了为什么填「故事创意」就能过：那句话把调性直接带进了淘汰赛。

判据：建书页上任何影响**故事内容**的选择，都必须在概念生成时就在场，而不是等到
概念已经定稿后才被下游 agent 读到。``audience_orientation`` 和 ``cost_style``
已经是这么做的，调性与技能同理。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


class TestTheTournamentAcceptsThem:
    def test_run_concept_tournament_takes_a_tone(self) -> None:
        sig = inspect.signature(ct.run_concept_tournament)
        assert "tone_preference" in sig.parameters

    def test_run_concept_tournament_takes_effect_skills(self) -> None:
        sig = inspect.signature(ct.run_concept_tournament)
        assert "effect_skills" in sig.parameters


class TestTheyReachTheGeneratorPrompt:
    def test_the_premise_prompt_renders_the_tone(self) -> None:
        source = inspect.getsource(ct._build_engine_kernel_messages)
        assert "tone_preference" in source

    def test_the_premise_prompt_renders_the_skills(self) -> None:
        source = inspect.getsource(ct._build_engine_kernel_messages)
        assert "effect_skills" in source

    def test_a_light_comedic_brief_appears_in_the_prompt(self) -> None:
        """真机那次的入参：轻松 + 喜剧 + 爽点满足。"""

        _system, user = ct._build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="纯题材直觉",
            chapter_count=50,
            audience_orientation="男频",
            tone_preference="light",
            effect_skills=["comedy_engine", "hype_satisfaction_engine"],
        )
        assert "轻松" in user, "调性必须出现在生成 prompt 里"
        assert "喜剧" in user, "喜剧技能必须出现在生成 prompt 里"

    def test_the_raw_idea_pool_receives_choices_before_it_mints_the_seed(self) -> None:
        """原始种子比 premise card 更早；这里断线会让后层被污染种子锁死。"""

        _system, user = ct._build_raw_idea_pool_messages(
            genre="仙侠",
            sub_genre="仙侠",
            count=4,
            tone_preference="light",
            effect_skills=["comedy_engine", "hype_satisfaction_engine"],
            creation_intent_block="【建书页明确选择】tone=light",
        )
        combined = _system + user
        assert "轻松" in combined
        assert "喜剧" in combined
        assert "建书页明确选择" in combined

    def test_contradiction_gate_rejects_a_dark_concept_for_light_tone(self) -> None:
        violations = ct._creation_intent_content_violations(
            "阴冷仙城里，主角背着尸体追查惨烈旧案。",
            tone_preference="light",
            effect_skills=["comedy_engine", "hype_satisfaction_engine"],
        )
        assert any("轻松调性" in item for item in violations)

    def test_effect_skills_do_not_require_literal_keywords(self) -> None:
        violations = ct._creation_intent_content_violations(
            "小贩借一次公开比试夺回摊位，还让执事不得不改掉旧规。",
            effect_skills=["comedy_engine", "hype_satisfaction_engine"],
        )
        assert violations == ()

    def test_strict_option_gate_accepts_visible_light_comedy_and_hype(self) -> None:
        violations = ct._creation_intent_content_violations(
            "主角用荒诞反差和自嘲拆穿仙门骗局，当众翻盘打脸，整体轻松明快。",
            tone_preference="light",
            effect_skills=["comedy_engine", "hype_satisfaction_engine"],
        )
        assert violations == ()

    def test_style_labels_cannot_cancel_heavy_story_surface(self) -> None:
        """writing_profile labels must never neutralize corpse/death prose."""

        violations = ct._creation_intent_content_violations(
            "主角背着尸体穿过矿道，第二具尸首堵住了出口。\n"
            "writing_profile.style.tone_keywords=轻松、幽默、明快",
            tone_preference="light",
        )
        assert any("轻松调性" in item for item in violations)

    def test_minimal_cost_no_longer_rejects_a_lifespan_mechanic(self) -> None:
        """2026-08-02: 纯爽 is pacing, not a ban on the genre's cost words."""
        assert (
            ct._creation_intent_content_violations(
                "幼崽越强，宿主寿命越短，把喂养变强和倒计时绑定。",
                tone_preference="",
                cost_style="minimal",
            )
            == ()
        )

    def test_minimal_cost_no_longer_rejects_meridian_injury_phrases(self) -> None:
        assert (
            ct._creation_intent_content_violations(
                "雷意灼脉后，经脉灼伤仍被迫继续施术。",
                cost_style="minimal",
            )
            == ()
        )

    def test_standard_cost_allows_production_meridian_injury_phrases(self) -> None:
        violations = ct._creation_intent_content_violations(
            "雷意灼脉后，经脉灼伤仍被迫继续施术。",
            cost_style="standard",
        )
        assert violations == ()

    def test_non_minimal_cost_does_not_activate_the_minimal_gate(self) -> None:
        violations = ct._creation_intent_content_violations(
            "幼崽越强，宿主寿命越短。",
            cost_style="standard",
        )
        assert violations == ()

    def test_corpse_motif_detector_is_retired(self) -> None:
        """A mine full of unclaimed bodies is a setting, not a defect."""
        from bestseller.services.anti_default_motif import is_anonymous_death_dominated

        assert not is_anonymous_death_dominated("矿工尸体没人收，另一具遗体堵在矿道")


class TestDefaultsChangeNothing:
    def test_no_tone_and_no_skills_leaves_the_prompt_as_before(self) -> None:
        """不选就不注入——保持原 prompt 逐字节不变，与 cost_style 同一约定。"""

        _s1, without = ct._build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="纯题材直觉", chapter_count=50
        )
        assert "调性" not in without
        assert "故事技能" not in without

    def test_an_unknown_tone_is_ignored_rather_than_leaked_raw(self) -> None:
        _s, user = ct._build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="纯题材直觉",
            chapter_count=50, tone_preference="nonsense-value",
        )
        assert "nonsense-value" not in user


class TestConceptionPassesTheUserChoice:
    """构思端必须把用户的选择交给淘汰赛，而不是留给下游 agent。"""

    def test_conception_forwards_tone_and_skills(self) -> None:
        from bestseller.services import conception

        source = inspect.getsource(conception.run_conception_pipeline)
        call = source[source.index("run_concept_tournament(") :]
        head = call[: call.index("retry_feedback=")]
        assert "tone_preference=" in head, "用户调性必须传进淘汰赛"
        assert "effect_skills=" in head, "用户勾的故事技能必须传进淘汰赛"

    def test_it_reads_them_from_the_creation_contract(self) -> None:
        """来源必须是建书契约（用户的显式选择），不是题材推导值。"""

        from bestseller.services import conception

        source = inspect.getsource(conception.run_conception_pipeline)
        call = source[source.index("tone_preference=") :][:400]
        assert "genre_intent_contract" in call

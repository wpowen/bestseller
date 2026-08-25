"""提示词契约测试——全库 prompt 工程规则的集中回归面（2026-08-24）。

背景：docs/一句话创意提示词工程分析-20260824.md 三个附录。每条契约都对应一次
真机定罪，删除前先读对应墓碑：
  ① 指令词即骨架（四轮实证）：prompt 里点名的要求词/示例被整批复印；
  ② 负例种词分层：概念层禁逐字负例（正文层反而被 A/B 保护，不在此测）；
  ③ 数值契约自洽：目标字数必须落在硬区间内（2026-08-24 六成重写指令自相矛盾）；
  ④ 判据双载→答辩腔：logline 救援改写必须带硬上限与反答辩令（《废丹成神》401字）。
"""

from __future__ import annotations


def _pool_prompt(**overrides) -> str:
    from bestseller.services.concept_tournament import _build_raw_idea_pool_messages

    kwargs = dict(
        genre="玄幻", sub_genre="东方玄幻", count=12, seed_concept="",
        prompt_arm="author_pitch", focus_hint="", audience_orientation="男频",
        tone_preference="hot", effect_skills=("hype_satisfaction_engine",),
        creation_intent_block="",
    )
    kwargs.update(overrides)
    _, user = _build_raw_idea_pool_messages(**kwargs)
    return user


class TestConceptLayerSeedTokenPolicy:
    """概念层生成 prompt：不许出现已定罪的种词/逐字负例。"""

    def test_pool_prompt_carries_no_convicted_seed_tokens(self) -> None:
        user = _pool_prompt()
        # 各轮真机被整批复印过的 token（亲手 11/12 两轮、挖矿示例被抄成创意）
        for token in ("亲手", "挖矿", "他要的不是"):
            assert token not in user, f"种词回流: {token}"

    def test_hook_distill_carries_no_verbatim_banned_tokens(self) -> None:
        from bestseller.services.concept_tournament import (
            _build_hook_from_engine_messages,
        )

        _, user = _build_hook_from_engine_messages(
            genre="玄幻", sub_genre="东方玄幻", kernel={"k": "v"},
            audience_orientation="男频",
        )
        for token in ("命运齿轮", "一步步", "随着真相浮现", "他只能/他必须"):
            assert token not in user, f"负例种词回流: {token}"

    def test_cliche_block_never_quotes_the_ban_bank(self) -> None:
        from bestseller.services.concept_tournament import (
            render_cliche_avoidance_block,
        )

        banned = ("废柴逆袭", "系统签到")
        block = render_cliche_avoidance_block(banned)
        for token in banned:
            assert token not in block


class TestNumericContractsAreSelfConsistent:
    """「必须落在 A-B」与「目标约 C」必须满足 A≤C≤B（六成重写指令自打案）。"""

    def test_chapter_length_band_contains_target(self) -> None:
        from types import SimpleNamespace

        from bestseller.services.drafts import _chapter_length_contract_band

        for declared in (0, 500, 2600, 2900, 5000, 12000):
            project = SimpleNamespace(metadata_json={}, language="zh-CN")
            hard_min, target, hard_max = _chapter_length_contract_band(
                project, declared
            )
            assert hard_min <= target <= hard_max, (declared, hard_min, target, hard_max)


class TestRescueRewriterAntiDefense:
    """logline 救援改写：硬上限+反答辩令必须在 prompt 里（判据双载第二案）。"""

    def test_zh_rescue_prompt_has_cap_and_anti_defense(self) -> None:
        import inspect

        from bestseller.services.conception import _rewrite_logline_for_gate

        source = inspect.getsource(_rewrite_logline_for_gate)
        assert "硬上限 90 字" in source
        assert "不是答辩状" in source
        assert "Hard cap: 120 characters" in source

    def test_rescue_loop_discards_overlong_rewrites(self) -> None:
        import inspect

        from bestseller.services.conception import _logline_regen_rescue

        source = inspect.getsource(_logline_regen_rescue)
        assert "len(rewritten) > 150" in source


class TestHardGateContractsStayInGenerator:
    """硬杀轴的契约必须前置到生成端（反例=压缩删章末规则再毙章）。"""

    def test_engine_kernel_carries_story_logic_gate_rules(self) -> None:
        from bestseller.services.concept_tournament import (
            _build_engine_kernel_messages,
        )

        _, user = _build_engine_kernel_messages(
            genre="玄幻", sub_genre="东方玄幻", lane="职业处境",
            chapter_count=240, audience_orientation="男频",
        )
        assert "故事逻辑硬门" in user

    def test_compactor_protects_ending_hook_rules(self) -> None:
        from bestseller.services import prompt_compactor

        import inspect

        source = inspect.getsource(prompt_compactor)
        assert "章末规则" in source


class TestSourceBoundLoglineNotPremise:
    """source-bound book_spec 不得把 premise 硬写进 logline（401 字案）。"""

    def test_hook_shaped_guard_rejects_defense_walls(self) -> None:
        from bestseller.services.planner import _hook_shaped_or_empty

        assert _hook_shaped_or_empty("短钩子") == "短钩子"
        assert _hook_shaped_or_empty("长" * 121) == ""
        assert _hook_shaped_or_empty(None) == ""

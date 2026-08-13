"""概念淘汰赛（services/concept_tournament.py）L1 测试。

真机根因（《谁敢动我山头》custom-xianxia-1783586500, 2026-07-09）：概念层
finalize 一锤子买卖 → 输出题材语料众数（废脉藏宝/破宗门重建/债主逼门），
读者可自动补全全书。本工序 = 反俗套禁用 + 杂交 N 候选 + 引擎审计 +
判官对撞榜单参照集，冠军注入 ctx["description"] 源头。

全部 LLM 走注入的 fake generator/judge；真实效果由真机对照书验证。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures are intentional.
import json
import random
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    ConceptTournamentResult,
    load_concept_tournament_config,
    render_high_concept_block,
    resolve_banned_cliches,
    run_concept_tournament,
)

pytestmark = pytest.mark.unit

# 一个合格的高概念候选（杂交=电网调度×仙侠，引擎字段齐全，不撞俗套）
GOOD_PAYLOAD = {
    "concept": "灵气是按'功德账户'配给的公共电网，主角是唯一敢给仙门拉闸限电的调度员。",
    "mechanism": "每次拉闸都重新定价一段修真界的权力关系，被限电者必须向调度台出让秘密或资源。",
    "hook_question": "凡人调度员凭什么拉闸？谁给他的权限，代价记在谁头上？",
    "protagonist_identity": "在州府灵网值夜的凡人调度员，掌握最低级但真实有效的断电权限",
    "protagonist_private_desire": "保住被宗门除名的妹妹最后一份灵气配额",
    "protagonist_flaw": "过度相信可计算的规则，不擅长判断人情背叛",
    "core_abnormality": "他能切断任何经过自己值班台的灵气线路，但每次操作都留下实名审计记录",
    "opening_crisis": "妹妹的救命配额被宗门临时挪走，他只有一刻钟决定是否违规拉闸",
    "opponent_system": "掌握配额审批与追责权、会通过旁路供能反制的宗门和天庭官署",
    "decision_proof": (
        "申诉要三日且妹妹撑不过今夜，偷配额会连累同批病人；"
        "实名拉闸可保全她并留下可追责证据，是唯一可逆转的公开行动"
    ),
    "emotional_promise": "看底层专业者用真实规则逼高位者当众付出代价",
    "core_promise_invariant": "看凡人调度员用真实配电权迫使高位者重新谈判资源规则",
    "unit_families": ["事故诊断", "配额交易", "团队分歧", "公开听证", "对手绕路反制"],
    "progress_bar": "调度权限等级：村级电闸→坊市→州府→天庭主干网",
    "unit_frequency": "每天至少一桩新的配额冲突或线路事故",
    "unit_count_estimate": 180,
    "question_ladder": ["谁建的灵气电网", "功德账户由谁记账", "第一任调度员为什么消失"],
    "ch50": "州府断电七日之战：三大宗门联手要夺调度台改自由市场",
    "renewal_sources": ["各地灵网故障持续产生调度任务", "宗门争夺配额不断制造冲突"],
    "accumulation_tracks": ["调度权限等级", "主角控制的灵网节点"],
    "phase_transitions": [
        "第1-80章村镇配电",
        "第81-180章坊市定价",
        "第181-350章州府电战",
        "第351-500章天庭主干网制度战",
    ],
    "opposing_ecology": ["争夺配额的宗门", "垄断主干网的天庭官署"],
    "endgame_direction": "决定灵气应由天庭垄断还是成为公共资源",
}

# 撞俗套的候选（废脉+宝脉 双词元命中 xianxia 禁用清单）
CLICHE_PAYLOAD = {
    **GOOD_PAYLOAD,
    "concept": "主角发现脚下废脉其实是上古宝脉，重建破败宗门。",
    "mechanism": "废脉里挖出的灵石能换资源，宗门逐步崛起。",
}

# 引擎残缺的候选（问题梯只有一级）
NO_ENGINE_PAYLOAD = {
    **GOOD_PAYLOAD,
    "concept": "修真界的丹药全部由一家神秘钱庄统一定价，主角是钱庄唯一的凡人柜员。",
    "question_ladder": ["钱庄老板是谁"],
}

WEAK_PAYLOAD = {
    **GOOD_PAYLOAD,
    "concept": "少年得到强大传承，一路修炼变强，最终问鼎巅峰。",
    "mechanism": "修炼吸收灵气突破境界。",
}


def _gen_from(payloads: list[dict]) -> callable:
    calls = iter(payloads)

    async def _gen(system: str, user: str):
        payload = next(calls, payloads[-1])
        return json.dumps(payload, ensure_ascii=False), f"run-{id(payload) % 997}"

    return _gen


def _judge_scoring(scores: dict[str, tuple[float, float, float]]) -> callable:
    """按概念文本片段路由 (freshness, click, predictable)。未命中给低分。"""

    async def _judge(system: str, user: str):
        for needle, (fresh, click, pred) in scores.items():
            if needle in user:
                return (
                    json.dumps(
                        {"freshness": fresh, "click": click, "predictable": pred,
                         "character_logic": 8,
                         "mechanism_causality": 8,
                         "genre_fidelity": 8,
                         "plain_language": 8,
                         "story_motion": 8,
                         "reason": "test"},
                        ensure_ascii=False,
                    ),
                    None,
                )
        return json.dumps({
            "freshness": 2, "click": 2, "predictable": 9,
            "character_logic": 6, "reason": "平庸",
        }), None

    return _judge


_CFG = {
    "enabled": True,
    "n_candidates": 3,
    "winner_min": 5.5,
    "judge_hard_floors": {
        "freshness": 6.0,
        "click": 7.0,
        "predictable_max": 6.0,
        "character_logic": 6.0,
        "mechanism_causality": 6.0,
        "genre_fidelity": 7.0,
        "plain_language": 7.0,
        "story_motion": 7.0,
    },
    "judge_weights": {"freshness": 0.4, "click": 0.4, "unpredictability": 0.2},
    "dimension_pool": ["电网调度与停电分配", "保险与精算定价", "殡葬入殓与遗产执行"],
    "cliche_seeds": {
        "generic": ["穿越自带系统面板"],
        "xianxia": ["废脉其实是宝脉", "破宗门重建崛起"],
    },
}


class TestResolveBannedCliches:
    def test_merges_generic_and_canonical_genre(self):
        bans = resolve_banned_cliches("古典仙侠", "古典仙侠", _CFG)
        assert "穿越自带系统面板" in bans
        assert "废脉其实是宝脉" in bans

    def test_unknown_genre_falls_back_to_generic_only(self):
        bans = resolve_banned_cliches("完全未知题材zzz", None, _CFG)
        assert bans == ("穿越自带系统面板",)

    def test_real_config_has_xianxia_seeds(self):
        # 真实配置：canonical 键必须命中（此前踩过 xianxia-classic≠xianxia 的坑）。
        load_concept_tournament_config.cache_clear()
        bans = resolve_banned_cliches("古典仙侠", "古典仙侠")
        assert any("废脉" in b for b in bans)

    def test_real_config_blocks_verified_dead_debt_contamination(self):
        load_concept_tournament_config.cache_clear()
        bans = resolve_banned_cliches("都市", "都市异能")
        assert "继承死人的债" in bans

    def test_real_config_uses_native_engine_first_route(self):
        load_concept_tournament_config.cache_clear()
        config = load_concept_tournament_config()
        assert config["candidate_prompt_mode"] == "engine_first"
        assert config["control_candidates"] == 2


class TestDeterministicScreens:
    def test_hook_floor_can_allow_three_non_catastrophic_soft_misses(self):
        from bestseller.services.concept_tournament import _hard_floor_failed_axes

        scores = {
            "freshness": 4,
            "click": 5,
            "predictable": 6.5,
            "character_logic": 7,
            "mechanism_causality": 7,
            "genre_fidelity": 8,
            "plain_language": 7,
            "story_motion": 7,
        }
        calibrated = {
            **_CFG["judge_hard_floors"],
            "catastrophe_floor": 4.0,
            "soft_miss_allowance": 3,
        }

        strict = {
            **_CFG["judge_hard_floors"],
            "soft_miss_allowance": 2,
        }
        assert _hard_floor_failed_axes(scores, strict)
        assert _hard_floor_failed_axes(scores, calibrated) == []
        assert _hard_floor_failed_axes(
            {**scores, "plain_language": 4}, calibrated
        )
        assert _hard_floor_failed_axes(
            {**scores, "freshness": 3}, calibrated
        )

    def test_incomplete_engine_verdict_is_not_coerced_to_zero(self):
        from bestseller.services.concept_tournament import (
            _parse_complete_axis_scores,
        )

        axes = ("freshness", "action_conflict", "reader_promise")
        scores, missing = _parse_complete_axis_scores(
            {"freshness": 8, "reader_promise": 9}, axes
        )

        assert scores is None
        assert missing == ["action_conflict"]

    def test_complete_engine_verdict_clamps_numeric_scores(self):
        from bestseller.services.concept_tournament import (
            _parse_complete_axis_scores,
        )

        axes = ("freshness", "action_conflict", "reader_promise")
        scores, missing = _parse_complete_axis_scores(
            {
                "freshness": "8.5",
                "action_conflict": 12,
                "reader_promise": -1,
            },
            axes,
        )

        assert scores == {
            "freshness": 8.5,
            "action_conflict": 10.0,
            "reader_promise": 0.0,
        }
        assert missing == []

    def test_premise_card_audit_rejects_nested_objects_inside_string_arrays(self):
        from bestseller.services.concept_tournament import _premise_card_audit

        card = {
            "protagonist_identity": "主角",
            "protagonist_private_desire": "欲望",
            "protagonist_flaw": "缺陷",
            "core_abnormality": "异常",
            "current_goal": "目标",
            "effective_resistance": "阻力",
            "failure_cost": "失败后果",
            "success_cost": "成功后果",
            "irreversible_change": "不可逆变化",
            "reader_promise": "承诺",
            "difference_point": "差异",
            "deformable_loop": "循环",
            "opening_crisis": "危机",
            "emotional_promise": "情绪",
            "scene_seeds": ["1", "2", "3", "4", "5", {"bad": "nested"}],
            "post_reveal_scene_seeds": ["1", "2", "3"],
            "expansion_axes": ["1", "2", "3"],
            "opposing_ecology": ["1", "2"],
        }

        assert _premise_card_audit(card) == ["scene_seeds"]

    def test_premise_card_repair_freezes_existing_story(self):
        from bestseller.services.concept_tournament import (
            _build_engine_kernel_repair_messages,
        )

        _, prompt = _build_engine_kernel_repair_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            lane="世界扩张#7",
            chapter_count=500,
            seed_concept="仙门下凡后，流民少年持有半卷旧法旨",
            card={"protagonist_identity": "流民少年"},
            missing_fields=["difference_point", "scene_seeds"],
        )

        assert "PREMISE_CARD_REPAIR" in prompt
        assert "人物、异常、关系、目标、阻力和后果全部冻结" in prompt
        assert "difference_point/scene_seeds" in prompt
        assert "数组只能直接包含非空字符串" in prompt

    def test_tournament_result_with_repair_run_id_is_json_serializable(self):
        result = ConceptTournamentResult(
            premise_cards=[{"repair": {"run_id": str(uuid4())}}]
        )

        json.dumps(result.to_dict(), ensure_ascii=False)

    def test_engine_first_separates_capacity_design_from_hook_copy(self):
        from bestseller.services.concept_tournament import (
            _build_engine_kernel_messages,
            _build_hook_from_engine_messages,
        )

        _, engine_prompt = _build_engine_kernel_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            lane="世界扩张",
            chapter_count=500,
            seed_concept="",
        )
        assert "PREMISE_CARD" in engine_prompt
        assert "本轮不写一句话钩子，也不规划卷章" in engine_prompt
        assert "current_goal" in engine_prompt
        assert "success_cost" in engine_prompt
        assert "deformable_loop" in engine_prompt
        assert "scene_seeds" in engine_prompt
        assert "从探索、迁徙、建设、经营或夺取生存空间起步" in engine_prompt

        kernel = {
            "core_promise_invariant": "主角不断改变灵气分配规则",
            "role_ladder": ["矿工", "调度员", "城主", "规则制定者"],
            "world_ladder": ["矿区", "州府", "天庭"],
        }
        _, hook_prompt = _build_hook_from_engine_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            kernel=kernel,
            seed_concept="",
        )
        assert "HOOK_DISTILL" in hook_prompt
        assert "主角不断改变灵气分配规则" in hook_prompt
        assert "独立写3条最想让人点开" in hook_prompt
        assert "不要解释500章" in hook_prompt
        assert "每条30-75字" in hook_prompt
        assert "不要写‘他只能/他必须/否则’" in hook_prompt
        assert '"hooks"' in hook_prompt
        assert '"decision_proof"' not in hook_prompt.split("只输出JSON：", 1)[1]

    def test_engine_kernel_freezes_story_facts_onto_hook_only_candidate(self):
        from bestseller.services.concept_tournament import (
            ConceptCandidate,
            _attach_engine_kernel,
        )

        attached = _attach_engine_kernel(
            ConceptCandidate(dimension="世界规则:scene", concept="一句话钩子"),
            {
                "protagonist_identity": "守山门的落魄剑修",
                "protagonist_private_desire": "保住师妹的入门名额",
                "protagonist_flaw": "凡事先退让",
                "core_abnormality": "能听见断剑还没完成的招式",
                "current_goal": "在天亮前修好镇山剑",
                "effective_resistance": "掌门已命人封死剑冢",
                "failure_cost": "山门会被仇家攻破",
                "success_cost": "会暴露他偷学禁剑",
                "reader_promise": "他能否补全失传剑法并守住山门",
                "deformable_loop": "每补一式都会改变门派关系和下一次对手",
                "opposing_ecology": ["夺剑的长老", "觊觎山门的敌宗"],
                "opening_crisis": "镇山剑在敌宗来袭前夜折断",
                "emotional_promise": "绝境翻盘与师门羁绊",
            },
        )

        assert attached.protagonist_identity == "守山门的落魄剑修"
        assert attached.mechanism.startswith("每补一式")
        assert "眼下必须完成" in attached.decision_proof
        assert "安全退让仍会失败" in attached.decision_proof
        assert "即使成功也要承受" in attached.decision_proof
        assert "夺剑的长老" in attached.opponent_system

    @pytest.mark.asyncio
    async def test_engine_first_runs_architect_then_hook_distillation(self):
        calls: list[str] = []
        kernel = {
            "protagonist_identity": GOOD_PAYLOAD["protagonist_identity"],
            "protagonist_private_desire": GOOD_PAYLOAD["protagonist_private_desire"],
            "protagonist_flaw": GOOD_PAYLOAD["protagonist_flaw"],
            "core_abnormality": GOOD_PAYLOAD["core_abnormality"],
            "current_goal": "今夜保住妹妹的灵气配额",
            "effective_resistance": GOOD_PAYLOAD["opponent_system"],
            "failure_cost": "妹妹失去救命配额",
            "success_cost": "实名拉闸会暴露他的权限",
            "irreversible_change": "宗门与调度台公开决裂",
            "reader_promise": GOOD_PAYLOAD["core_promise_invariant"],
            "difference_point": "凡人调度员掌握真实断电权",
            "deformable_loop": GOOD_PAYLOAD["mechanism"],
            "expansion_axes": [*GOOD_PAYLOAD["renewal_sources"], "团队分裂产生新目标"],
            "opposing_ecology": GOOD_PAYLOAD["opposing_ecology"],
            "scene_seeds": ["场面1", "场面2", "场面3", "场面4", "场面5"],
            "post_reveal_scene_seeds": ["揭晓后场面1", "揭晓后场面2", "揭晓后场面3"],
            "opening_crisis": GOOD_PAYLOAD["opening_crisis"],
            "emotional_promise": GOOD_PAYLOAD["emotional_promise"],
        }

        async def generator(system: str, user: str):
            calls.append(user)
            if '"ideas"' in user:
                payload = {
                    "ideas": [
                        {"lane": "世界规则", "seed": "矿工听见灵脉求救"},
                        {"lane": "职业处境", "seed": "调度员发现天庭偷电"},
                    ]
                }
            else:
                payload = kernel if "PREMISE_CARD" in user else GOOD_PAYLOAD
            return json.dumps(payload, ensure_ascii=False), None

        result = await run_concept_tournament(
            None,
            None,
            genre="仙侠",
            sub_genre="古典仙侠",
            chapter_count=20,
            config={
                **_CFG,
                "n_candidates": 2,
                "candidate_prompt_mode": "engine_first",
                "raw_idea_prompt_arm": "methodology",
                # This test is about architect → hook distillation, not pool
                # replenishment. The stub returns a deliberately short pool, so
                # leave the (separately tested) top-up out of the call count.
                "raw_idea_pool_topup_calls": 0,
            },
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )

        assert len(calls) == 5
        assert sum('"ideas"' in call for call in calls) == 1
        assert any("异常被公开或第一次使用之后" in call for call in calls)
        assert result.raw_idea_prompt_arm == "methodology"
        assert sum("PREMISE_CARD" in call for call in calls) == 2
        assert sum("HOOK_DISTILL" in call for call in calls) == 2
        assert result.candidates[0].core_promise_invariant
        assert result.candidates[0].renewal_sources
        assert result.candidate_generation_calls == 5
        assert any(candidate.dimension.endswith(":raw") for candidate in result.candidates)

    @pytest.mark.asyncio
    async def test_incomplete_batch_premise_verdict_retries_single_card(self):
        kernel = {
            "protagonist_identity": GOOD_PAYLOAD["protagonist_identity"],
            "protagonist_private_desire": GOOD_PAYLOAD["protagonist_private_desire"],
            "protagonist_flaw": GOOD_PAYLOAD["protagonist_flaw"],
            "core_abnormality": GOOD_PAYLOAD["core_abnormality"],
            "current_goal": "今夜保住妹妹的灵气配额",
            "effective_resistance": GOOD_PAYLOAD["opponent_system"],
            "failure_cost": "妹妹失去救命配额",
            "success_cost": "实名拉闸会暴露他的权限",
            "irreversible_change": "宗门与调度台公开决裂",
            "reader_promise": GOOD_PAYLOAD["core_promise_invariant"],
            "difference_point": "凡人调度员掌握真实断电权",
            "deformable_loop": GOOD_PAYLOAD["mechanism"],
            "expansion_axes": [*GOOD_PAYLOAD["renewal_sources"], "团队分裂产生新目标"],
            "opposing_ecology": GOOD_PAYLOAD["opposing_ecology"],
            "scene_seeds": ["场面1", "场面2", "场面3", "场面4", "场面5"],
            "post_reveal_scene_seeds": ["揭晓后场面1", "揭晓后场面2", "揭晓后场面3"],
            "opening_crisis": GOOD_PAYLOAD["opening_crisis"],
            "emotional_promise": GOOD_PAYLOAD["emotional_promise"],
        }

        async def generator(system: str, user: str):
            if '"ideas"' in user:
                return json.dumps(
                    {
                        "ideas": [
                            {"lane": "世界规则", "seed": "矿工听见灵脉求救"},
                            {"lane": "职业处境", "seed": "调度员发现天庭偷电"},
                        ]
                    },
                    ensure_ascii=False,
                ), None
            return json.dumps(
                kernel if "PREMISE_CARD" in user else GOOD_PAYLOAD,
                ensure_ascii=False,
            ), None

        premise_calls: list[str] = []
        complete_scores = {
            "seed_fidelity": 8,
            "freshness": 8,
            "click_seed": 8,
            "action_conflict": 8,
            "reader_promise": 8,
            "character_choice": 8,
            "scene_generation": 8,
            "promise_survival": 8,
            "deformable_loop": 8,
            "post_reveal_engine": 8,
            "genre_fidelity": 8,
            "reason": "合格",
        }

        async def premise_judge(system: str, user: str):
            premise_calls.append(user)
            if "候选=" in user:
                incomplete = {**complete_scores, "index": 0}
                incomplete.pop("action_conflict")
                return json.dumps(
                    {
                        "verdicts": [
                            incomplete,
                            {**complete_scores, "index": 1},
                        ]
                    },
                    ensure_ascii=False,
                ), "batch-run"
            return json.dumps(complete_scores, ensure_ascii=False), "retry-run"

        result = await run_concept_tournament(
            None,
            None,
            genre="仙侠",
            sub_genre="古典仙侠",
            chapter_count=20,
            config={
                **_CFG,
                "n_candidates": 2,
                "candidate_prompt_mode": "engine_first",
                "raw_idea_prompt_arm": "methodology",
                "raw_idea_pool_multiplier": 1,
            },
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            premise_judge=premise_judge,
            rng=random.Random(7),
        )

        assert len(premise_calls) == 2
        assert "batch-run" in result.llm_run_ids
        assert "retry-run" in result.llm_run_ids
        assert not any(
            rejection.get("failed_axes") == ["missing_verdict_fields"]
            for rejection in result.engine_rejections
        )
        assert result.candidates

    @pytest.mark.asyncio
    async def test_engine_batch_keeps_clean_near_passes_for_the_final_hook_gate(self):
        """A strict card prefilter must not shrink four paid cards to zero hooks."""

        kernel = {
            "protagonist_identity": "外门种药的废脉少年",
            "protagonist_private_desire": "保住自己培育的第一块药田",
            "protagonist_flaw": "嘴硬又不肯求人",
            "core_abnormality": "他能听见灵草嫌弃功法里的错处",
            "current_goal": "在考核前救活整片枯黄灵草",
            "effective_resistance": "管事要收回药田交给内门弟子",
            "failure_cost": "失去外门资格",
            "success_cost": "公开暴露自己能听懂灵草",
            "irreversible_change": "灵草当众改认他为主",
            "reader_promise": "看他用灵草吐槽拆穿天才功法并连续翻盘",
            "difference_point": "灵草会直接吐槽修炼错误",
            "deformable_loop": "每次纠错都会让对手换功法并暴露新的漏洞",
            "expansion_axes": ["药田", "宗门考核", "功法争夺"],
            "opposing_ecology": ["外门管事", "抢药田的内门弟子"],
            "scene_seeds": ["场面1", "场面2", "场面3", "场面4", "场面5"],
            "post_reveal_scene_seeds": ["揭晓后场面1", "揭晓后场面2", "揭晓后场面3"],
            "opening_crisis": "考核前夜药田突然全枯",
            "emotional_promise": "轻松吐槽、连续打脸和成长兑现",
        }
        hook_calls: list[str] = []

        async def generator(system: str, user: str):
            if '"ideas"' in user:
                return json.dumps(
                    {
                        "ideas": [
                            {"lane": "成长道路", "seed": "少年听见灵草吐槽功法漏洞"},
                            {"lane": "职业处境", "seed": "药童靠灵草纠错保住药田"},
                        ]
                    },
                    ensure_ascii=False,
                ), None
            if "PREMISE_CARD" in user:
                return json.dumps(kernel, ensure_ascii=False), None
            hook_calls.append(user)
            return json.dumps(GOOD_PAYLOAD, ensure_ascii=False), None

        weak_card_scores = {
            "seed_fidelity": 6,
            "freshness": 6,
            "click_seed": 6,
            "action_conflict": 6,
            "reader_promise": 6,
            "character_choice": 6,
            "scene_generation": 6,
            "promise_survival": 6,
            "deformable_loop": 6,
            "post_reveal_engine": 6,
            "genre_fidelity": 6,
            "reason": "可进入最终钩子门继续竞争",
        }

        async def premise_judge(system: str, user: str):
            return json.dumps(
                {
                    "verdicts": [
                        {**weak_card_scores, "index": 0},
                        {**weak_card_scores, "index": 1},
                    ]
                },
                ensure_ascii=False,
            ), None

        result = await run_concept_tournament(
            None,
            None,
            genre="东方玄幻",
            sub_genre="东方玄幻",
            chapter_count=50,
            config={
                **_CFG,
                "n_candidates": 2,
                "candidate_prompt_mode": "engine_first",
                "raw_idea_prompt_arm": "methodology",
                "raw_idea_pool_multiplier": 1,
                "premise_card_min_survivors": 2,
            },
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            premise_judge=premise_judge,
            rng=random.Random(7),
        )

        assert len(hook_calls) == 2
        assert result.candidates

    def test_engine_judge_does_not_trust_claimed_unit_count(self):
        from bestseller.services.concept_tournament import _build_engine_judge_messages

        _, prompt = _build_engine_judge_messages(
            kernel={
                "repeatable_story_unit": "每接一单就用同一种能力解决",
                "unit_count_estimate": 500,
            },
            genre="仙侠",
            sub_genre="古典仙侠",
            chapter_count=500,
            seed_concept="书生靠近剑阁时万剑自断",
        )

        assert "声称能写500章不加分" in prompt
        assert "五个场面只是换人换地" in prompt
        assert "seed_fidelity" in prompt
        assert "书生靠近剑阁时万剑自断" in prompt

    def test_hook_variants_are_competed_separately(self):
        from bestseller.services.concept_tournament import _parse_hook_variants

        payload = {
            **GOOD_PAYLOAD,
            "hooks": [
                {"angle": "promise", "concept": "收殓师替枉死者整理遗容时，能看见他们没来得及活完的明天。"},
                {"angle": "paradox", "concept": "他在老人没来得及活完的明天里，看见了第二天就会死去的自己。"},
                {"angle": "scene", "concept": "雨夜缝合老人伤口时，他看见停尸柜里的自己睁开了眼。"},
            ],
        }

        variants = _parse_hook_variants(
            json.dumps(payload, ensure_ascii=False), "职业处境"
        )

        assert len(variants) == 3
        assert {candidate.dimension for candidate in variants} == {
            "职业处境:promise",
            "职业处境:paradox",
            "职业处境:scene",
        }

    def test_raw_idea_pool_is_minimal_and_parses_known_lanes(self):
        from bestseller.services.concept_tournament import (
            _build_raw_idea_pool_messages,
            _parse_raw_idea_pool,
        )

        _, prompt = _build_raw_idea_pool_messages(
            genre="仙侠", sub_genre="古典仙侠", count=4
        )
        assert "一个具体的人，遇到一个让人立刻想追问的异常处境" in prompt
        assert "不写大纲、世界观说明" in prompt
        parsed = _parse_raw_idea_pool(
            json.dumps(
                {
                    "ideas": [
                        {"lane": "职业处境", "seed": "收尸人看见死者明天"},
                        {"lane": "未知路线", "seed": "矿工听见灵脉求救"},
                    ]
                },
                ensure_ascii=False,
            ),
            limit=2,
        )
        assert parsed == [
            ("职业处境", "收尸人看见死者明天"),
            ("纯题材直觉", "矿工听见灵脉求救"),
        ]

    def test_raw_idea_pool_focus_is_a_single_lightweight_hint(self):
        from bestseller.services.concept_tournament import (
            _build_raw_idea_pool_messages,
        )

        _, prompt = _build_raw_idea_pool_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            count=4,
            prompt_arm="methodology",
            focus_hint="题材原生职业与资源争夺",
        )

        assert "本批优先从题材原生职业与资源争夺寻找" in prompt
        assert prompt.count("本批优先从") == 1

    def test_consequence_arm_adds_only_positive_causality_constraints(self):
        from bestseller.services.concept_tournament import (
            _build_raw_idea_pool_messages,
        )

        _, methodology = _build_raw_idea_pool_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            count=1,
            prompt_arm="methodology",
        )
        _, consequence = _build_raw_idea_pool_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            count=1,
            prompt_arm="consequence",
        )

        assert "第一眼意外" not in methodology
        assert "第一眼意外" in consequence
        assert "又觉得必然" in consequence
        assert "角色、关系、资源、暴露风险或未来选择" in consequence
        assert "折寿" not in consequence

    def test_author_pitch_thinks_through_story_before_distilling_hook(self):
        from bestseller.services.concept_tournament import (
            _build_raw_idea_pool_messages,
        )

        _, prompt = _build_raw_idea_pool_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            count=1,
            prompt_arm="author_pitch",
        )

        assert "先把人物、开篇和故事为什么会继续想通" in prompt
        assert '"seed":"一句话故事"' in prompt
        assert '"opening":"具体开篇"' in prompt
        assert '"future_situations"' in prompt
        assert "卷纲、体系表或章数计划" in prompt

    def test_author_pitch_support_is_parsed_and_passed_to_premise_card(self):
        from bestseller.services.concept_tournament import (
            _build_engine_kernel_messages,
            _parse_raw_idea_records,
        )

        records = _parse_raw_idea_records(
            json.dumps(
                {
                    "ideas": [
                        {
                            "lane": "职业处境",
                            "seed": "守井女修发现井底菌后正在苏醒",
                            "opening": "宗门准备封井",
                            "why_it_keeps_moving": "菌后生长会改变整条灵脉",
                            "future_situations": ["救井", "迁菌", "争夺灵脉"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            limit=1,
        )

        assert records[0]["opening"] == "宗门准备封井"
        assert records[0]["future_situations"] == ["救井", "迁菌", "争夺灵脉"]
        _, prompt = _build_engine_kernel_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            lane="职业处境#0",
            chapter_count=500,
            seed_concept=records[0]["seed"],
            seed_support=records[0],
        )

        assert "作者在提炼一句话前已做过的故事探索" in prompt
        assert "菌后生长会改变整条灵脉" in prompt

    @pytest.mark.asyncio
    async def test_engine_first_generates_raw_ideas_in_independent_small_batches(self):
        pool_prompts: list[str] = []
        kernel = {
            "protagonist_identity": "边城守灯人",
            "protagonist_private_desire": "保住家中旧灯",
            "protagonist_flaw": "过度谨慎",
            "core_abnormality": "能听见灯火说出死者未完之事",
            "current_goal": "今夜点亮城门灯",
            "effective_resistance": "巡城司禁止旧灯复燃",
            "failure_cost": "归城商队会迷失",
            "success_cost": "他会暴露旧灯仍在",
            "irreversible_change": "巡城司开始追查他",
            "reader_promise": "每盏灯改变一段城中关系",
            "difference_point": "灯火不是线索而是公开行动入口",
            "deformable_loop": "每次点灯的后果改变下一处可点之灯",
            "scene_seeds": ["场面1", "场面2", "场面3", "场面4", "场面5"],
            "post_reveal_scene_seeds": ["揭晓后1", "揭晓后2", "揭晓后3"],
            "expansion_axes": ["城门", "商路", "诸国"],
            "opposing_ecology": ["巡城司", "商会"],
            "opening_crisis": "商队将在今夜迷失",
            "emotional_promise": "小人物公开违令救人",
        }

        async def generator(system: str, user: str):
            if '"ideas"' in user:
                pool_prompts.append(user)
                batch_no = len(pool_prompts)
                return json.dumps(
                    {
                        "ideas": [
                            {
                                "lane": "职业处境",
                                "seed": f"第{batch_no}批守灯人异常一",
                            },
                            {
                                "lane": "世界扩张",
                                "seed": f"第{batch_no}批守灯人异常二",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ), None
            return json.dumps(
                kernel if "PREMISE_CARD" in user else GOOD_PAYLOAD,
                ensure_ascii=False,
            ), None

        await run_concept_tournament(
            None,
            None,
            genre="仙侠",
            sub_genre="古典仙侠",
            chapter_count=20,
            config={
                **_CFG,
                "n_candidates": 3,
                "candidate_prompt_mode": "engine_first",
                "raw_idea_prompt_arm": "methodology",
                "raw_idea_pool_multiplier": 2,
                "raw_idea_generation_batch_size": 2,
                "raw_idea_batch_focuses": ["人物", "职业", "世界"],
            },
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )

        # 2026-08-10: a focus hint is the ONE-AT-A-TIME case's only diversity
        # lever. These batches carry 2 ideas each, so the model must make the
        # set internally distinct instead — pinning a batched call to a single
        # dimension measurably worsened the attractor (20.0% batched-and-free
        # vs 36.4% batched-and-focused, live ablation).
        assert len(pool_prompts) == 3
        for prompt in pool_prompts:
            assert "本批优先从" not in prompt

    def test_raw_idea_ablation_arms_add_only_the_intended_context(self):
        from bestseller.services.concept_tournament import (
            _build_raw_idea_pool_messages,
        )

        _, minimal = _build_raw_idea_pool_messages(
            genre="仙侠", sub_genre="古典仙侠", count=12, prompt_arm="minimal"
        )
        _, methodology = _build_raw_idea_pool_messages(
            genre="仙侠", sub_genre="古典仙侠", count=12, prompt_arm="methodology"
        )
        _, enhanced = _build_raw_idea_pool_messages(
            genre="仙侠", sub_genre="古典仙侠", count=12, prompt_arm="enhanced"
        )
        _, guarded = _build_raw_idea_pool_messages(
            genre="仙侠", sub_genre="古典仙侠", count=12, prompt_arm="guarded"
        )

        assert "持续选择" not in minimal
        assert "持续做出不同选择" in methodology
        assert "稳定的故事场" not in methodology
        assert "固定惩罚" not in methodology
        assert "与核心动作无因果关系的固定惩罚" in guarded
        assert "稳定的故事场" in enhanced
        assert len(minimal) < len(methodology) < len(guarded) < len(enhanced)

    def test_raw_idea_rank_rejects_finite_gimmicks(self):
        from bestseller.services.concept_tournament import (
            _build_raw_idea_rank_messages,
            _parse_raw_idea_ranking,
        )

        _, prompt = _build_raw_idea_rank_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            ideas=[("世界规则", "雨声能测谎，但每问一人就永久失灵一成")],
        )
        assert "会归零的次数" in prompt
        assert "promise_survival" in prompt
        assert "genre_fidelity" in prompt
        assert "character_logic" in prompt
        assert "ai_assembly" in prompt
        assert "dumb_cost" in prompt
        assert "换成别的题材仍完全成立" in prompt
        assert "after_opening_promise" in prompt
        assert "action_families" in prompt
        assert "growth_surface" in prompt
        parsed = _parse_raw_idea_ranking(
            '{"ranked":[{"index":0,"freshness":8,"click_seed":8,'
            '"action_seed":7,"promise_survival":3,"genre_fidelity":9,'
            '"after_opening_promise":"","action_families":[],'
            '"growth_surface":"",'
            '"reason":"十轮耗尽"}]}'
        )
        assert parsed[0]["promise_survival"] == 3
        assert parsed[0]["genre_fidelity"] == 9
        assert parsed[0]["action_families"] == []

    def test_raw_idea_progression_is_wide_in_but_not_unconditional(self):
        from bestseller.services.concept_tournament import (
            _select_raw_ideas_for_expansion,
        )

        common = {
            "character_logic": 7,
            "action_seed": 7,
            "ai_assembly": 2,
            "dumb_cost": False,
            "after_opening_promise": "主角经营的驿站持续改变人妖两界的通行格局",
            "action_families": ["经营驿站", "处理来客", "周旋势力"],
            "growth_surface": "驿站规模、人脉和区域权力持续积累",
        }
        strict = {
            **common,
            "index": 0,
            "freshness": 8,
            "click_seed": 8,
            "promise_survival": 8,
            "genre_fidelity": 8,
        }
        near = {
            **common,
            "index": 1,
            "freshness": 6,
            "click_seed": 8,
            "promise_survival": 6,
            "genre_fidelity": 8,
        }
        finite = {
            **common,
            "index": 2,
            "freshness": 9,
            "click_seed": 9,
            "promise_survival": 4,
            "genre_fidelity": 9,
        }

        selected = _select_raw_ideas_for_expansion(
            [near, finite, strict, {**near, "click_seed": 7}],
            raw_floor=7,
            progression_floor=5,
            limit=4,
        )

        assert [item["index"] for item in selected] == [0, 1]

    def test_raw_idea_progression_rejects_dumb_cost_and_ai_assembly(self):
        from bestseller.services.concept_tournament import (
            _select_raw_ideas_for_expansion,
        )

        common = {
            "freshness": 9,
            "click_seed": 9,
            "character_logic": 8,
            "action_seed": 8,
            "promise_survival": 8,
            "genre_fidelity": 8,
            "after_opening_promise": "主角的选择持续改变宗门权力格局",
            "action_families": ["谈判", "追查", "公开对抗"],
            "growth_surface": "关系、证据和权力边界持续积累",
        }

        selected = _select_raw_ideas_for_expansion(
            [
                {**common, "index": 0, "ai_assembly": 2, "dumb_cost": True},
                {**common, "index": 1, "ai_assembly": 8, "dumb_cost": False},
                {**common, "index": 2, "ai_assembly": 2, "dumb_cost": False},
            ],
            raw_floor=7,
            progression_floor=5,
            limit=4,
        )

        assert [item["index"] for item in selected] == [2]

    def test_engine_hook_distillation_avoids_forced_angle_templates(self):
        from bestseller.services.concept_tournament import (
            _build_hook_from_engine_messages,
        )

        _, prompt = _build_hook_from_engine_messages(
            genre="仙侠",
            sub_genre="古典仙侠",
            kernel={"reader_promise": "经营人妖共用的荒山驿站"},
            seed_concept="镖师成为荒山驿官",
        )

        assert "不要套promise/paradox/scene模板" in prompt
        assert "一个具体主角" in prompt
        assert "允许选择并压缩" in prompt
        assert "一步步" in prompt

    def test_native_baseline_prompt_has_no_dimension_or_long_plan_pollution(self):
        from bestseller.services.concept_tournament import (
            _build_lean_candidate_messages,
            _build_native_candidate_messages,
        )

        kwargs = {
            "genre": "仙侠",
            "sub_genre": "古典仙侠",
            "dimension": "保险与精算定价",
            "chapter_count": 500,
            "banned": ("系统签到",),
            "avoid_mechanisms_block": "",
            "seed_concept": "",
            "retry_feedback": "",
        }
        _, native = _build_native_candidate_messages(**kwargs)
        _, lean = _build_lean_candidate_messages(**kwargs)

        assert "原生故事基线" in native
        assert "保险与精算定价" not in native
        assert "正常人的欲望和选择" in native
        assert "不要为了显得新奇硬加代价" in native
        assert "phase_transitions" not in native
        assert "三级发动机" not in native
        assert len(native) < len(lean)

    def test_native_six_lane_search_includes_growth_and_world_expansion(self):
        from bestseller.services.concept_tournament import _NATIVE_STORY_LANES

        first_six = _NATIVE_STORY_LANES[:6]
        assert "成长道路" in first_six
        assert "世界扩张" in first_six

    @pytest.mark.asyncio
    async def test_native_mode_uses_story_lanes_instead_of_cross_domain_pool(self):
        prompts: list[str] = []

        async def generator(system: str, user: str):
            prompts.append(user)
            return json.dumps(GOOD_PAYLOAD, ensure_ascii=False), None

        await run_concept_tournament(
            None,
            None,
            genre="仙侠",
            sub_genre="古典仙侠",
            chapter_count=20,
            config={
                **_CFG,
                "n_candidates": 3,
                "candidate_prompt_mode": "native_baseline",
            },
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )

        assert len(prompts) == 3
        assert all("原生故事基线" in prompt for prompt in prompts)
        assert all("保险与精算定价" not in prompt for prompt in prompts)

    @pytest.mark.parametrize(
        ("concept", "expected_code"),
        [
            ("主角每接一个新案件就破解一次秘密，然后遇到更强的新案件。", "parallel_repetition"),
            ("主角每使用一次能力就随机折寿十年，为了活命只能继续使用能力。", "external_cost"),
            ("他每复活一条死路，就逼未来的资本真身现身封杀。", "abstract_formula"),
            ("主角明知报警即可安全解决，却为了故事开始主动独自赴死。", "irrational_protagonist"),
            ("每继承一次亡者未来，她的存在就被抹去一层，直到没人记得她。", "external_cost"),
            ("每揭穿一条天条，天地就从他身上讨走一块肉，直到他只剩一双眼。", "external_cost"),
            ("每改写一次因果，他都必须支付十年寿命或一段气运。", "external_cost"),
            ("每渡一位亡魂，他的当世记忆便愈稀薄，直到忘记自己。", "external_cost"),
            ("每递一封阴书，他就离死亡越近。", "external_cost"),
        ],
    )
    def test_frozen_anti_hooks_are_rejected_without_model_help(
        self, concept: str, expected_code: str
    ):
        from bestseller.services.concept_tournament import _deterministic_anti_pattern

        candidate = ConceptCandidate(
            dimension="anti",
            concept=concept,
            mechanism=concept,
            decision_proof=concept,
        )

        assert _deterministic_anti_pattern(candidate) == expected_code

    def test_lean_story_package_prompt_is_compact_and_does_not_plan_the_book(self):
        from bestseller.services.concept_tournament import (
            _build_candidate_messages,
            _build_lean_candidate_messages,
        )

        kwargs = {
            "genre": "都市异能",
            "sub_genre": "黑科技创业",
            "dimension": "主角决策因果",
            "chapter_count": 500,
            "banned": ("系统签到",),
            "avoid_mechanisms_block": "",
            "seed_concept": "芯片工程师从报废机器里复原被放弃的技术路线。",
            "retry_feedback": "上一轮只是换一条路线，再被封杀一次。",
        }
        _, current = _build_candidate_messages(**kwargs)
        _, lean = _build_lean_candidate_messages(**kwargs)

        assert kwargs["seed_concept"] in lean
        assert "第一眼意外，解释后必然" in lean
        assert "正常聪明人" in lean
        assert "更安全" in lean
        assert "会学习" in lean
        assert "最多两个分句" in lean
        assert "规则+一个具体悖论" in lean
        assert "protagonist_identity" in lean
        assert "decision_proof" in lean
        assert "phase_transitions" not in lean
        assert "第50章" not in lean
        assert "167" not in lean
        assert "三级发动机" not in lean
        assert len(lean) <= len(current) * 0.6

    def test_seed_refinement_includes_signature_paradox_lane(self):
        from bestseller.services.concept_tournament import _SEED_REFINEMENT_DIMENSIONS

        assert "标志性反常事件" in _SEED_REFINEMENT_DIMENSIONS[:6]

    def test_seriality_prompt_requires_phase_specific_unit_families(self):
        from bestseller.services.concept_tournament import _build_seriality_messages

        candidate = ConceptCandidate(
            dimension="长篇",
            concept=GOOD_PAYLOAD["concept"],
            mechanism=GOOD_PAYLOAD["mechanism"],
            protagonist_identity=GOOD_PAYLOAD["protagonist_identity"],
        )
        _, user = _build_seriality_messages(
            candidate=candidate,
            genre="仙侠",
            chapter_count=500,
        )

        assert "core_promise_invariant" in user
        assert "unit_families" in user
        assert "裁判不会因数字高而加分" in user
        assert '"unit_count_estimate":167' in user
        assert "每个阶段至少一种新的主角动作" in user

    @pytest.mark.asyncio
    async def test_tournament_routes_candidate_generation_to_lean_prompt_mode(self):
        prompts: list[str] = []

        async def generator(system: str, user: str):
            prompts.append(user)
            return json.dumps(GOOD_PAYLOAD, ensure_ascii=False), None

        await run_concept_tournament(
            None,
            None,
            genre="古典仙侠",
            sub_genre="古典仙侠",
            chapter_count=20,
            config={
                **_CFG,
                "n_candidates": 2,
                "candidate_prompt_mode": "lean_story_package",
            },
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )

        assert len(prompts) == 2
        assert all("【精简故事包】" in prompt for prompt in prompts)
        assert all("第50章" not in prompt for prompt in prompts)

    def test_selected_seed_is_refined_without_unrelated_cross_domain_mutation(self):
        from bestseller.services.concept_tournament import _build_candidate_messages

        seed = "芯片工程师从报废机器里复原被放弃的技术路线。"
        _, user = _build_candidate_messages(
            genre="都市异能",
            sub_genre="黑科技创业",
            dimension="主角决策因果",
            chapter_count=500,
            banned=(),
            avoid_mechanisms_block="",
            seed_concept=seed,
        )

        assert seed in user
        assert "同源补强" in user
        assert "不得嫁接考古、殡葬、法医、戏班" in user
        assert "允许彻底重建持续行动" in user
        assert "本路强制杂交" not in user
        assert "抽象隐喻" in user
        assert "对手如何反制" in user
        assert "因果飞轮" in user
        assert "平行重复" in user
        assert "碎片汇成同一个增长中的答案" in user
        assert "最多两个分句" in user
        assert "不要把第一危机、决策证明" in user

    def test_retry_feedback_is_injected_into_candidate_rewrite(self):
        from bestseller.services.concept_tournament import _build_candidate_messages

        _, user = _build_candidate_messages(
            genre="都市异能",
            sub_genre="黑科技创业",
            dimension="阶段跃迁",
            chapter_count=500,
            banned=(),
            avoid_mechanisms_block="",
            seed_concept="芯片工程师从报废机器里复原失败路线。",
            retry_feedback="上一轮只是换一条路线，再被封杀一次，属于外部投喂。",
        )

        assert "上一轮真实失败" in user
        assert "换一条路线，再被封杀一次" in user
        assert "不得只换措辞" in user

    @pytest.mark.asyncio
    async def test_retry_uses_two_targeted_near_miss_lanes(self):
        prompts: list[str] = []

        async def generator(system: str, user: str):
            prompts.append(user)
            return json.dumps(GOOD_PAYLOAD, ensure_ascii=False), None

        await run_concept_tournament(
            None,
            None,
            genre="都市异能",
            sub_genre="黑科技创业",
            chapter_count=20,
            config={**_CFG, "n_candidates": 2},
            generator=generator,
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            seed_concept="芯片工程师从报废机器里复原失败路线。",
            retry_feedback="最佳近失只差持续故事运动。",
        )

        assert len(prompts) == 2
        assert "定向修复最佳近失" in prompts[0]
        assert "定向反转最佳近失" in prompts[1]

    def test_judge_uses_full_genre_and_does_not_require_forced_cost(self):
        from bestseller.services.concept_tournament import _build_judge_messages

        candidate = ConceptCandidate(
            dimension="题材兑现",
            concept="芯片工程师把报废机器里的失败路线做成新产品。",
            mechanism="拆机、复原、产品化，再应对垄断者反制。",
        )
        _, user = _build_judge_messages(
            candidate=candidate,
            genre="都市异能",
            sub_genre="都市创业, 硬核科技",
            references=[],
        )

        assert "都市异能（都市创业, 硬核科技）" in user
        assert "没有显式代价不得扣分" in user
        assert "硬核科技、创业、职业操作" in user
        assert "报废晶圆" in user
        assert "应为8-10分" in user
        assert "清晰的连载承诺，不是可预测缺陷" in user
        assert "能力类型见过不等于后续可预测" in user
        # Production judge calibration must stay genre-neutral; concrete corpse
        # examples previously anchored unrelated books onto funeral/forensic ideas.
        assert "能听见尚未完成的剑招" in user
        assert "收殓师继承死者未来" not in user
        assert "predictable应为2-4分" in user
        assert "只评上面‘概念：’这一行" in user
        assert "做一个产品，资本再封杀一次" in user
        assert "后附机制写得再完整也不能救分" in user
        assert "一个具体到令人不安的反常实例" in user
        assert "不强制把对手塞进一句话" in user
        assert "现代职业流程搬进超凡世界" in user
        assert "原生常识为什么不能替代" in user

    def test_selected_brainhole_principle_only_enters_hybrid_candidate(self):
        from bestseller.services.concept_tournament import _build_candidate_messages

        _, hybrid = _build_candidate_messages(
            genre="仙侠", sub_genre="古典仙侠", dimension="电网调度",
            chapter_count=500, banned=(), avoid_mechanisms_block="",
        )
        _, control = _build_candidate_messages(
            genre="仙侠", sub_genre="古典仙侠", dimension="纯题材对照",
            chapter_count=500, banned=(), avoid_mechanisms_block="",
        )

        assert "第一眼意外，解释后必然" in hybrid
        assert "第一眼意外，解释后必然" not in control

        _, character_control = _build_candidate_messages(
            genre="仙侠", sub_genre="古典仙侠", dimension="纯题材人物困局对照",
            chapter_count=500, banned=(), avoid_mechanisms_block="",
        )
        assert "第一人称" in character_control
        assert "第一眼意外，解释后必然" not in character_control
        assert "三层因果" in hybrid
        assert "不断换地图" in hybrid

    @pytest.mark.asyncio
    async def test_cliche_candidate_rejected(self):
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([CLICHE_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )
        rejected = [c for c in result.candidates if c.rejected_reason]
        assert any("俗套命中" in (c.rejected_reason or "") for c in rejected)
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]

    @pytest.mark.asyncio
    async def test_candidate_without_capacity_proof_rejected_for_long_target(self):
        finite = {
            key: value
            for key, value in GOOD_PAYLOAD.items()
            if key not in {
                "renewal_sources", "accumulation_tracks", "phase_transitions",
                "opposing_ecology", "endgame_direction",
            }
        }
        finite["concept"] = "灵气电网只有一个总闸，主角拉下它就能结束天庭垄断。"
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=500,
            config={**_CFG, "n_candidates": 2}, generator=_gen_from([finite, GOOD_PAYLOAD]),
            judge=_judge_scoring({
                finite["concept"][:12]: (8, 8, 2),
                GOOD_PAYLOAD["concept"][:12]: (9, 9, 2),
            }),
            rng=random.Random(7),
        )

        rejected = [c for c in result.candidates if c.rejected_reason]
        assert any("缺SerialityProof" in (c.rejected_reason or "") for c in rejected)
        assert result.winner is not None
        assert result.winner.seriality_report["estimated_chapter_ceiling"] >= 500

    @pytest.mark.asyncio
    async def test_concept_without_minimum_story_seed_is_rejected(self):
        incomplete = {
            key: value
            for key, value in GOOD_PAYLOAD.items()
            if key not in {"opening_crisis", "opponent_system", "decision_proof"}
        }
        incomplete["concept"] = "凡人调度员发现灵气配额可以被远程切断，却没有任何人物动机。"
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([incomplete, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )

        assert any("CoreStorySeed 不完整" in (c.rejected_reason or "") for c in result.candidates)
        assert result.winner is not None

    @pytest.mark.asyncio
    async def test_overlong_explanatory_hook_is_rejected(self):
        verbose = {
            **GOOD_PAYLOAD,
            "concept": "这是一个关于凡人调度员的故事，" + "他需要解释灵气电网如何运行" * 12,
        }
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([verbose, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )

        assert any("18-120字" in (c.rejected_reason or "") for c in result.candidates)
        assert result.winner is not None

    def test_clear_81_character_hook_is_not_mechanically_rejected(self):
        from bestseller.services.concept_tournament import _seed_audit

        candidate = ConceptCandidate(
            dimension="边界",
            concept="芯" * 81,
            mechanism="本轮产品改变市场状态，对手反制制造下一轮不同问题。",
            protagonist_identity="被开除的芯片架构师",
            protagonist_private_desire="查清自己被开除的真相",
            core_abnormality="能从报废设备复原被放弃的设计",
            opening_crisis="唯一客户即将因断供倒闭",
            opponent_system="会学习并调整封锁方式的产业联盟",
            decision_proof="直接卖技术会丢失证据，先量产才能逼对手留下交易痕迹",
            emotional_promise="把行业判死刑的东西重新做活，并迫使垄断者改规则",
        )

        assert _seed_audit(candidate) is None

    @pytest.mark.asyncio
    async def test_short_candidate_is_not_forced_to_write_long_plan_during_hook_round(self):
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([NO_ENGINE_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )
        assert not any("故事种子审计" in (c.rejected_reason or "") for c in result.candidates)
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]


class TestJudgeTournament:
    def test_seriality_payload_cannot_overwrite_frozen_story_mechanism(self):
        from bestseller.services.concept_tournament import _apply_seriality_payload

        candidate = ConceptCandidate(
            dimension="冻结",
            concept=GOOD_PAYLOAD["concept"],
            mechanism="原始人物因果机制",
        )
        expanded = _apply_seriality_payload(
            candidate,
            json.dumps(
                {
                    "repeatable_story_unit": "为了凑数量而生成的三级发动机",
                    "progress_bar": GOOD_PAYLOAD["progress_bar"],
                },
                ensure_ascii=False,
            ),
        )

        assert expanded is not None
        assert expanded.mechanism == "原始人物因果机制"
        assert expanded.repeatable_story_unit == "为了凑数量而生成的三级发动机"

    @pytest.mark.asyncio
    async def test_default_callers_separate_generation_and_judge_models(self, monkeypatch):
        from bestseller.services import concept_tournament as module
        from bestseller.services import llm as llm_module

        captured = []

        async def _complete_text(session, settings, request):
            captured.append(request)
            return SimpleNamespace(content="{}", llm_run_id=None)

        monkeypatch.setattr(llm_module, "complete_text", _complete_text)
        generator = await module._default_generator(
            None,
            None,
            template="concept_tournament_candidate",
            logical_role="planner",
            model_catalog_key="nim-kimi-k2.6",
        )
        judge = await module._default_generator(
            None,
            None,
            template="concept_tournament_judge",
            logical_role="critic",
            model_catalog_key="nim-deepseek-v4-pro",
        )

        await generator("system", "user")
        await judge("system", "user")

        assert captured[0].logical_role == "planner"
        assert captured[0].model_catalog_key == "nim-kimi-k2.6"
        assert captured[1].logical_role == "critic"
        assert captured[1].model_catalog_key == "nim-deepseek-v4-pro"

    def test_plain_language_rubric_is_calibrated_for_genre_vocabulary(self):
        from bestseller.services.concept_tournament import _build_judge_messages

        _, user = _build_judge_messages(
            candidate=ConceptCandidate(
                dimension="供应链",
                concept="修仙界快递员发现自己送的不是货，是活人。",
            ),
            genre="仙侠",
            references=[],
        )

        assert "题材目标读者本来就懂的常识词不算术语" in user
        assert "COTRS精算残差" in user

    def test_genre_fidelity_rejects_unexplained_modern_forensics_in_xianxia(self):
        from bestseller.services.concept_tournament import _build_judge_messages

        _, user = _build_judge_messages(
            candidate=ConceptCandidate(
                dimension="职业处境",
                concept="仙门废物靠给修仙者做尸检、卖报告挣钱。",
                mechanism="复盘尸体，写成报告卖给下一批修士。",
            ),
            genre="仙侠",
            references=[],
        )

        assert "原生常识为什么不能替代" in user
        assert "尸体为何必然保留" not in user
        assert "不得超过3分" in user

    @pytest.mark.asyncio
    async def test_long_target_expands_only_top_two_hook_finalists(self):
        seed_keys = {
            "concept", "mechanism", "hook_question", "protagonist_identity",
            "protagonist_private_desire", "protagonist_flaw", "core_abnormality",
            "opening_crisis", "opponent_system", "decision_proof", "emotional_promise",
        }
        seeds = []
        for label in ("甲", "乙", "丙"):
            seed = {key: GOOD_PAYLOAD[key] for key in seed_keys}
            seed["concept"] = f"概念{label}：灵气配额每天午夜重算，凡人调度员能让仙门整夜断电。"
            seeds.append(seed)

        expansion_calls: list[str] = []

        async def _expand(system: str, user: str):
            expansion_calls.append(user)
            payload = {
                "core_promise_invariant": GOOD_PAYLOAD["core_promise_invariant"],
                "repeatable_story_unit": GOOD_PAYLOAD["mechanism"],
                "unit_families": GOOD_PAYLOAD["unit_families"],
                "progress_bar": GOOD_PAYLOAD["progress_bar"],
                "unit_frequency": GOOD_PAYLOAD["unit_frequency"],
                "unit_count_estimate": GOOD_PAYLOAD["unit_count_estimate"],
                "renewal_sources": GOOD_PAYLOAD["renewal_sources"],
                "accumulation_tracks": GOOD_PAYLOAD["accumulation_tracks"],
                "phase_transitions": GOOD_PAYLOAD["phase_transitions"],
                "opposing_ecology": GOOD_PAYLOAD["opposing_ecology"],
                "mystery_ladder": GOOD_PAYLOAD["question_ladder"],
                "ch50": GOOD_PAYLOAD["ch50"],
                "endgame_direction": GOOD_PAYLOAD["endgame_direction"],
            }
            return json.dumps(payload, ensure_ascii=False), None

        async def _seriality_judge(system: str, user: str):
            return json.dumps({
                "renewability": 8, "escalation": 8, "anti_reset": 8,
                "coherence": 8, "promise_survival": 8, "unit_density": 8,
                "reason": "可持续",
            }, ensure_ascii=False), None

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=500,
            config=_CFG, generator=_gen_from(seeds),
            judge=_judge_scoring({"概念甲": (9, 9, 1), "概念乙": (8, 8, 2), "概念丙": (7, 7, 3)}),
            expander=_expand, seriality_judge=_seriality_judge,
            rng=random.Random(7),
        )

        assert len(expansion_calls) == 2
        assert all("概念丙" not in call for call in expansion_calls)
        assert result.winner is not None
        assert result.winner.concept.startswith("概念甲")
        assert result.winner.seriality_report["estimated_chapter_ceiling"] >= 500

    @pytest.mark.asyncio
    async def test_seriality_proof_gets_one_bounded_repair_without_rewriting_hook(self):
        compact = {
            key: GOOD_PAYLOAD[key]
            for key in (
                "concept", "mechanism", "hook_question", "protagonist_identity",
                "protagonist_private_desire", "protagonist_flaw", "core_abnormality",
                "opening_crisis", "opponent_system", "decision_proof", "emotional_promise",
            )
        }
        calls = 0
        prompts: list[str] = []

        async def _expand(system: str, user: str):
            nonlocal calls
            calls += 1
            prompts.append(user)
            payload = {
                "core_promise_invariant": GOOD_PAYLOAD["core_promise_invariant"],
                "repeatable_story_unit": GOOD_PAYLOAD["mechanism"],
                "unit_families": GOOD_PAYLOAD["unit_families"],
                "progress_bar": "" if calls == 1 else GOOD_PAYLOAD["progress_bar"],
                "unit_frequency": GOOD_PAYLOAD["unit_frequency"],
                "unit_count_estimate": GOOD_PAYLOAD["unit_count_estimate"],
                "renewal_sources": GOOD_PAYLOAD["renewal_sources"],
                "accumulation_tracks": GOOD_PAYLOAD["accumulation_tracks"],
                "phase_transitions": GOOD_PAYLOAD["phase_transitions"],
                "opposing_ecology": GOOD_PAYLOAD["opposing_ecology"],
                "mystery_ladder": GOOD_PAYLOAD["question_ladder"],
                "ch50": GOOD_PAYLOAD["ch50"],
                "endgame_direction": GOOD_PAYLOAD["endgame_direction"],
            }
            return json.dumps(payload, ensure_ascii=False), None

        async def _seriality_judge(system: str, user: str):
            return json.dumps({
                "renewability": 8, "escalation": 8, "anti_reset": 8,
                "coherence": 8, "promise_survival": 8, "unit_density": 8,
                "reason": "修复后通过",
            }, ensure_ascii=False), None

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=500,
            config={**_CFG, "n_candidates": 2},
            generator=_gen_from([compact, WEAK_PAYLOAD]),
            judge=_judge_scoring({compact["concept"][:12]: (9, 9, 2)}),
            expander=_expand, seriality_judge=_seriality_judge,
            rng=random.Random(7),
        )

        assert calls == 2
        assert result.winner is not None
        assert result.winner.concept == compact["concept"]
        assert result.winner.progress_bar == GOOD_PAYLOAD["progress_bar"]
        assert "内生微单元" in prompts[0]
        assert "回溯到主角已经做出的选择" in prompts[0]
        assert "失败反馈" in prompts[1]
        assert "三级发动机" in prompts[1]

    @pytest.mark.asyncio
    async def test_implausible_character_decision_is_hard_vetoed(self):
        async def _judge_without_human_logic(system: str, user: str):
            return json.dumps({
                "freshness": 10,
                "click": 10,
                "predictable": 0,
                "character_logic": 3,
                "mechanism_causality": 8,
                "genre_fidelity": 8,
                "plain_language": 8,
                "story_motion": 8,
                "reason": "主角明知没有收益且必死，仍只为推动情节主动入局",
            }, ensure_ascii=False), None

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]),
            judge=_judge_without_human_logic,
            rng=random.Random(7),
        )

        assert result.winner is None
        assert result.candidates[0].composite == 0
        assert "人物决策" in (result.candidates[0].rejected_reason or "")

    @pytest.mark.asyncio
    async def test_arbitrary_supernatural_cost_is_hard_vetoed(self):
        async def _judge_arbitrary_cost(system: str, user: str):
            return json.dumps({
                "freshness": 9,
                "click": 9,
                "predictable": 2,
                "character_logic": 8,
                "mechanism_causality": 3,
                "genre_fidelity": 8,
                "plain_language": 8,
                "story_motion": 8,
                "reason": "器官衰竭是外置惩罚，不是排序行为的自然后果",
            }, ensure_ascii=False), None

        result = await run_concept_tournament(
            None, None, genre="悬疑", sub_genre="民俗悬疑", chapter_count=20,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]),
            judge=_judge_arbitrary_cost, rng=random.Random(7),
        )

        assert result.winner is None
        assert "机制因果" in (result.candidates[0].rejected_reason or "")

    @pytest.mark.asyncio
    async def test_jargon_or_genre_replacement_is_hard_vetoed(self):
        async def _judge_jargon(system: str, user: str):
            return json.dumps({
                "freshness": 8,
                "click": 8,
                "predictable": 3,
                "character_logic": 8,
                "mechanism_causality": 8,
                "genre_fidelity": 4,
                "plain_language": 3,
                "story_motion": 8,
                "reason": "更像保险职业文，且术语需要解释",
            }, ensure_ascii=False), None

        result = await run_concept_tournament(
            None, None, genre="民俗悬疑", sub_genre="民俗悬疑", chapter_count=20,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]),
            judge=_judge_jargon, rng=random.Random(7),
        )

        assert result.winner is None
        reason = result.candidates[0].rejected_reason or ""
        assert "题材保真" in reason
        assert "大白话" in reason

    @pytest.mark.asyncio
    async def test_witty_setup_without_story_motion_is_hard_vetoed(self):
        async def _judge_static_setup(system: str, user: str):
            return json.dumps({
                "freshness": 8, "click": 8, "predictable": 3,
                "character_logic": 8, "mechanism_causality": 8,
                "genre_fidelity": 8, "plain_language": 9,
                "story_motion": 4,
                "reason": "只有反差设定，没有持续行动与阻力",
            }, ensure_ascii=False), None

        result = await run_concept_tournament(
            None, None, genre="都市", sub_genre="都市异能", chapter_count=500,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]),
            judge=_judge_static_setup, rng=random.Random(7),
        )

        assert result.winner is None
        assert "故事运动" in (result.candidates[0].rejected_reason or "")

    @pytest.mark.asyncio
    async def test_high_novelty_cannot_average_away_catastrophic_click_desire(self):
        # 2026-07-17 双层地板语义:click 5.0(平庸)作为唯一软失可容忍,
        # 但灾难线(<5.0)以下仍然一票死——高新颖救不了没人想点的概念。
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (10, 4, 0)}),
            rng=random.Random(7),
        )

        assert result.winner is None
        assert "想点欲" in (result.candidates[0].rejected_reason or "")

    @pytest.mark.asyncio
    async def test_higher_composite_wins_order_independent(self):
        # 弱候选排第一个生成——若选择退化成 list 顺序会选错。
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([WEAK_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({
                WEAK_PAYLOAD["concept"][:10]: (3, 4, 9),
                GOOD_PAYLOAD["concept"][:10]: (9, 8, 2),
            }),
            rng=random.Random(7),
        )
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]
        # 评审分回写进 candidates 快照（可落库复盘,同 blurb 淘汰赛 F4 教训）
        scored = {c.concept: c for c in result.candidates if c.composite is not None}
        assert GOOD_PAYLOAD["concept"] in scored
        assert WEAK_PAYLOAD["concept"] in scored

    @pytest.mark.asyncio
    async def test_all_below_winner_min_yields_no_winner(self):
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([WEAK_PAYLOAD]),
            judge=_judge_scoring({WEAK_PAYLOAD["concept"][:10]: (3, 3, 9)}),
            rng=random.Random(7),
        )
        # composite = 3*0.4+3*0.4+(10-9)*0.2 = 2.6 < 5.5 → 不注入,回落现状
        assert result.winner is None
        assert result.candidates  # 但候选记录保留供复盘

    @pytest.mark.asyncio
    async def test_predictability_drags_composite_down(self):
        # 同 freshness/click,可预测性 9 vs 1 → 差 1.6 分,足以翻盘。
        a = {**GOOD_PAYLOAD, "concept": "概念Alpha：灵气电网调度员的拉闸权战争。"}
        b = {**GOOD_PAYLOAD, "concept": "概念Beta：灵气保险精算师给渡劫定保费。"}
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([a, b]),
            judge=_judge_scoring({
                "概念Alpha": (7, 7, 9),   # composite 5.8
                "概念Beta": (7, 7, 1),    # composite 7.4
            }),
            rng=random.Random(7),
        )
        assert result.winner is not None
        assert "概念Beta" in result.winner.concept


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty_without_calls(self):
        called = {"n": 0}

        async def _gen(system, user):
            called["n"] += 1
            return "{}", None

        result = await run_concept_tournament(
            None, None, genre="仙侠", sub_genre="", chapter_count=20,
            config={"enabled": False}, generator=_gen,
        )
        assert called["n"] == 0
        assert result.winner is None
        assert result.candidates == []

    @pytest.mark.asyncio
    async def test_generator_always_raising_yields_no_winner_without_raise(self):
        async def _boom(system, user):
            raise RuntimeError("llm down")

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="", chapter_count=20,
            config=_CFG, generator=_boom, judge=_boom, rng=random.Random(7),
        )
        assert result.winner is None

    @pytest.mark.asyncio
    async def test_judge_garbage_yields_no_winner(self):
        async def _garbage(system, user):
            return "不是JSON", None

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="", chapter_count=20,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]), judge=_garbage,
            rng=random.Random(7),
        )
        # 判官全废 → 无 composite → 无冠军(宁缺毋滥),但不 raise
        assert result.winner is None


class TestRenderHighConceptBlock:
    def test_winner_block_carries_concept_engine_without_ban_samples(self):
        winner = ConceptCandidate(
            dimension="电网调度与停电分配",
            concept=GOOD_PAYLOAD["concept"],
            mechanism=GOOD_PAYLOAD["mechanism"],
            hook_question=GOOD_PAYLOAD["hook_question"],
            progress_bar=GOOD_PAYLOAD["progress_bar"],
            question_ladder=tuple(GOOD_PAYLOAD["question_ladder"]),
            ch50=GOOD_PAYLOAD["ch50"],
            composite=8.0,
        )
        result = ConceptTournamentResult(
            winner=winner, banned_cliches=("废脉其实是宝脉",),
        )
        block = render_high_concept_block(result)
        assert GOOD_PAYLOAD["concept"] in block
        assert "进度条=" in block and "问题梯=" in block and "第50章" in block
        assert "废脉其实是宝脉" not in block
        assert "禁用样本文本不向下游传播" in block
        assert "禁止回归题材默认套路" in block

    def test_no_winner_renders_empty(self):
        assert render_high_concept_block(ConceptTournamentResult()) == ""


# ── conception.py 接线结构钉（同 T4/T6 既有测试惯例）─────────────────────────


class TestConceptionWiring:
    def _source(self) -> str:
        import inspect

        from bestseller.services import conception as conception_services

        return inspect.getsource(conception_services.run_conception_pipeline)

    def test_tournament_runs_before_round0_and_after_mechanism_dedup(self):
        source = self._source()
        dedup_pos = source.index("_attach_mechanism_dedup(session, settings, ctx)")
        tournament_pos = source.index("run_concept_tournament(")
        round0_pos = source.index("Round 0: Autonomous Commercial Positioning")
        assert dedup_pos < tournament_pos < round0_pos, (
            "tournament must consume avoid_mechanisms (after dedup) and inject "
            "before any downstream agent prompt is built (before Round 0)"
        )

    def test_user_concept_is_seeded_but_cannot_bypass_capacity_gate(self):
        source = self._source()
        idx = source.index("run_concept_tournament(")
        # 锚点定位而不是固定窗口:调用参数只会越来越多(2026-07-24 加了
        # audience/cost_style,07-30 又加了 tone/effect_skills/入参集),每次都靠
        # 调大字符数来救测试,等于让测试跟着实现漂。取到调用结束括号为止。
        call_region = source[idx : source.index("\n                _emit(", idx)]
        assert "seed_concept=" in call_region
        assert 'user_hints.get("concept_seed")' in source
        assert "_user_owned_story_seed" in call_region
        assert "concept_bundle.one_liner or concept_bundle.reader_promise" in source
        assert "require_conception_contract_for_target" in source

    def test_fail_open_and_injection_shape(self):
        source = self._source()
        idx = source.index("run_concept_tournament(")
        # 同理:注入段落在调用之后,用它自己的锚点收尾,不用字符数。
        region = source[max(0, idx - 1200) : source.index('ctx["high_concept"]', idx) + 200]
        assert "except Exception as exc:" in source
        assert "if isinstance(exc, ConceptContractError):" in source
        assert 'logger.warning("Concept tournament failed (non-fatal)' in source
        # 注入=augment description(全 prompt 源头) + high_concept 观测键 + 消毒。
        assert '_sanitize_forbidden_default_motifs(' in region
        assert 'ctx["description"] = f"{ctx.get(\'description\') or \'\'}\\n{_hc_block}"' in region
        assert 'ctx["high_concept"] = _ct_result.winner.to_dict()' in region

    def test_tournament_record_lands_in_conception_log(self):
        source = self._source()
        idx = source.index('"agent": "concept_tournament"')
        region = source[max(0, idx - 300) : idx + 200]
        assert "conception_log.append(" in region

    def test_long_book_retries_then_stops_before_downstream_agents(self):
        source = self._source()
        # 2026-07-17: short/mid books get 2 attempts so the near-miss retry
        # branch is reachable (1 attempt made it dead code for 50-chapter books).
        retry_pos = source.index("max_concept_attempts = 3 if chapter_count >= 200 else 2")
        stop_pos = source.index("已在市场/角色/世界观生成前终止")
        round0_pos = source.index("Round 0: Autonomous Commercial Positioning")

        assert retry_pos < stop_pos < round0_pos


class TestClicheCalibration:
    """2026-07-09 真机校准回归：长短语散词误伤。"""

    def test_long_phrase_two_scattered_tokens_do_not_kill(self):
        # 首轮真机对照组"修真账房做假功德账"被"老祖飞升前留下传承"(8词元)以
        # 【老祖+飞升】两个散词误毙——长短语(>4词元)现在要求≥3命中。
        from bestseller.services.concept_tournament import _cliche_hits

        candidate = ConceptCandidate(
            dimension="纯题材对照",
            concept="主角修真做账房，专为濒死大能伪造功德假账，结果飞升的不是客户是他自己。",
            mechanism="修仙界所有死而复生的气运老祖都是他客户；金手指是真·账道。",
            hook_question="谁能在飞升审核眼皮底下开账房做到上市？",
        )
        hits = _cliche_hits(candidate, ("老祖飞升前留下传承",))
        assert hits == []

    def test_long_phrase_three_tokens_still_kill(self):
        from bestseller.services.concept_tournament import _cliche_hits

        candidate = ConceptCandidate(
            dimension="纯题材对照",
            concept="老祖飞升前给主角留下一份传承。",
            mechanism="靠传承变强。",
            hook_question="传承里有什么？",
        )
        hits = _cliche_hits(candidate, ("老祖飞升前留下传承",))
        assert hits == ["老祖飞升前留下传承"]

    def test_short_phrase_keeps_two_token_threshold(self):
        from bestseller.services.concept_tournament import _cliche_hits

        candidate = ConceptCandidate(
            dimension="纯题材对照",
            concept="他被退婚后当众打脸前未婚妻全家。",
            mechanism="打脸变强。",
            hook_question="下一个打谁的脸？",
        )
        hits = _cliche_hits(candidate, ("退婚打脸",))
        assert hits == ["退婚打脸"]


class TestHighConceptDownstreamConsumers:
    """三小修(2026-07-09 第二轮)：冠军概念要被书名工序和文案工序真正吃到。"""

    def test_title_profile_logline_prefers_high_concept(self):
        # 真机《我靠签契改地脉》教训：书名从 premise 取材,丢掉概念最独特的
        # "同传/翻译官"维度——title_profile.logline 必须优先吃冠军 concept。
        import inspect

        from bestseller.services import conception as conception_services

        source = inspect.getsource(conception_services.run_conception_pipeline)
        assert '"logline": _hc_concept or premise,' in source
        idx = source.index('"logline": _hc_concept or premise,')
        region = source[max(0, idx - 800) : idx]
        assert '(ctx.get("high_concept") or {}).get("concept")' in region

    def test_copywriter_prompt_demands_plain_speech_translation(self):
        # persona 划走理由"拓扑/界枢署名词脑瓜子疼"——候选生成 prompt 必须
        # 硬性要求把学术词/机构名翻译成大白话。
        from bestseller.services.blurb_copywriter import _build_candidate_messages

        _, user = _build_candidate_messages(
            "scene_hook",
            spine={"who": "x"}, premise="p", golden_finger_line="g",
            title="t", tags=[], genre="古典仙侠", sub_genre="古典仙侠",
            platform=None, persona=None, emotion_exemplars=(),
            book_jargon_terms=(), band=(80, 220),
        )
        assert "翻译成" in user and "大白话" in user

    def test_jargon_source_includes_high_concept_in_conception(self):
        import inspect

        from bestseller.services import conception as conception_services

        source = inspect.getsource(conception_services.run_conception_pipeline)
        assert '"high_concept": ctx.get("high_concept")' in source


# ── P1b 脑洞全开(wild_concept):三道收敛闸门 eliminate→penalize + 降 winner_min ──


_WILD = {
    **_CFG,
    "cliche_mode": "penalize",
    "audit_mode": "penalize",
    "winner_min": 5.5,
    "cliche_penalty": 1.5,
    "audit_penalty": 1.0,
}


class TestResolveTournamentConfig:
    def test_non_wild_returns_base_unchanged(self):
        from bestseller.services.concept_tournament import resolve_tournament_config

        base = {**_CFG, "wild_mode": {"winner_min": 4.0, "cliche_mode": "penalize"}}
        assert resolve_tournament_config(wild=False, base=base) is base

    def test_wild_merges_overrides_and_deep_merges_weights(self):
        from bestseller.services.concept_tournament import resolve_tournament_config

        base = {
            **_CFG,
            "wild_mode": {
                "winner_min": 4.0,
                "cliche_mode": "penalize",
                "audit_mode": "penalize",
                "judge_weights": {"freshness": 0.5},
            },
        }
        merged = resolve_tournament_config(wild=True, base=base)
        assert merged["winner_min"] == 4.0
        assert merged["cliche_mode"] == "penalize"
        assert merged["audit_mode"] == "penalize"
        # judge_weights 深合并：freshness 覆盖，click/unpredictability 保留。
        assert merged["judge_weights"]["freshness"] == 0.5
        assert merged["judge_weights"]["click"] == 0.4
        # 基线对象绝不被污染（lru_cache 安全）。
        assert base["winner_min"] == 5.5
        assert base["judge_weights"]["freshness"] == 0.4

    def test_real_config_wild_mode_keeps_quality_gate(self):
        from bestseller.services.concept_tournament import (
            load_concept_tournament_config,
            resolve_tournament_config,
        )

        load_concept_tournament_config.cache_clear()
        merged = resolve_tournament_config(wild=True)
        assert float(merged["winner_min"]) == 5.5
        assert merged["cliche_mode"] == "penalize"
        # 真实基线未被污染。
        assert float(load_concept_tournament_config()["winner_min"]) == 5.5


class TestWildConceptMode:
    """penalize 模式：俗套/审计命中不淘汰，改 composite 罚分；降 winner_min 收留大胆概念。"""

    @pytest.mark.asyncio
    async def test_penalize_keeps_cliche_candidate_alive(self):
        # eliminate 下 CLICHE 被毙(见 TestDeterministicScreens);penalize 下存活并打分。
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_WILD, generator=_gen_from([CLICHE_PAYLOAD]),
            judge=_judge_scoring({CLICHE_PAYLOAD["concept"][:8]: (9, 9, 1)}),
            rng=random.Random(7),
        )
        assert not any("俗套命中" in (c.rejected_reason or "") for c in result.candidates)
        assert result.winner is not None
        assert result.winner.concept == CLICHE_PAYLOAD["concept"]
        # raw = 9*0.4+9*0.4+(10-1)*0.2 = 9.0; 减俗套罚分 1.5 = 7.5。
        assert abs((result.winner.composite or 0.0) - 7.5) < 1e-6

    @pytest.mark.asyncio
    async def test_penalty_flips_ranking_vs_clean_candidate(self):
        # 俗套候选原始分更高(8.2),罚分后(6.7)输给干净候选(7.2)——证明罚分真扣。
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_WILD, generator=_gen_from([CLICHE_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({
                CLICHE_PAYLOAD["concept"][:8]: (8, 8, 1),
                GOOD_PAYLOAD["concept"][:12]: (7, 7, 2),
            }),
            rng=random.Random(7),
        )
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]

    @pytest.mark.asyncio
    async def test_wild_mode_cannot_bypass_quality_floor(self):
        # 生成策略可以更野，但低新颖度/低点击欲不能借 wild 模式绕过验收。
        bold = {**GOOD_PAYLOAD, "concept": "概念Gamma：殡葬入殓师给渡劫失败者办身后事的暗黑仙侠。"}
        wild_res = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_WILD, generator=_gen_from([bold]),
            judge=_judge_scoring({"概念Gamma": (4, 5, 6)}),
            rng=random.Random(7),
        )
        base_res = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([bold]),
            judge=_judge_scoring({"概念Gamma": (4, 5, 6)}),
            rng=random.Random(7),
        )
        assert wild_res.winner is None
        assert base_res.winner is None

    @pytest.mark.asyncio
    async def test_default_mode_still_eliminates_cliche(self):
        # no-op 守卫：不给 penalize 键 → 与现状一致，CLICHE 仍被毙。
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([CLICHE_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )
        assert any("俗套命中" in (c.rejected_reason or "") for c in result.candidates)


class TestWildConceptWiring:
    def _source(self) -> str:
        import inspect

        from bestseller.services import conception as conception_services

        return inspect.getsource(conception_services.run_conception_pipeline)

    def test_wild_flag_read_and_config_passed(self):
        source = self._source()
        assert 'ctx["wild_concept"] = bool(user_hints.get("wild_concept"))' in source
        config_idx = source.index("resolve_tournament_config(")
        call_end = source.index("retry_feedback=concept_retry_feedback", config_idx)
        region = source[config_idx:call_end]
        assert 'resolve_tournament_config(\n                wild=bool(ctx.get("wild_concept"))' in region
        assert 'ctx.get("wild_concept")' in region
        assert "config=attempt_config," in region

    def test_pipeline_threads_wild_into_user_hints(self):
        import inspect

        from bestseller.services import pipelines

        source = inspect.getsource(pipelines.run_autowrite_pipeline)
        assert "wants_wild_concept(project_payload.metadata or {})" in source
        assert '_conception_hints["wild_concept"] = True' in source


class TestCandidatePromptJudgeAlignment:
    """Generation must aim at what the judge floors measure (2026-07-16).

    Live dry-run forensics: for a 男频玄幻 request all 4 candidates were the
    same literary 目录师 concept family — 题材保真 3.0-6.5 and 大白话 3.0-5.0
    against 7.0 floors. Three gaps: the builder never受众 (channel unknown), the
    hybrid directive ordered 职业/冲突形态 rebuilt from the foreign field (i.e.
    instructed drift), and plain-language was never demanded.
    """

    _KWARGS = {
        "genre": "玄幻",
        "sub_genre": "玄幻",
        "dimension": "目录学",
        "chapter_count": 50,
        "banned": (),
        "avoid_mechanisms_block": "",
    }

    def test_audience_orientation_reaches_the_candidate_prompt(self):
        from bestseller.services.concept_tournament import _build_candidate_messages

        _, user = _build_candidate_messages(**self._KWARGS, audience_orientation="男频")
        assert "男频" in user

    def test_hybrid_directive_preserves_genre_core_instead_of_rebuilding_it(self):
        from bestseller.services.concept_tournament import _build_candidate_messages

        _, user = _build_candidate_messages(**self._KWARGS)
        assert "题材保真" in user or "本题材的核心读者契约" in user
        assert "由该领域重塑" not in user

    def test_plain_language_floor_is_announced_to_the_generator(self):
        from bestseller.services.concept_tournament import _build_candidate_messages

        _, user = _build_candidate_messages(**self._KWARGS)
        assert "大白话" in user

    def test_lean_builder_carries_the_same_alignment(self):
        from bestseller.services.concept_tournament import _build_lean_candidate_messages

        _, lean = _build_lean_candidate_messages(**self._KWARGS, audience_orientation="女频")
        assert "女频" in lean
        assert "大白话" in lean


class TestEngineFirstAudienceAnchoring:
    """Production runs candidate_prompt_mode=engine_first — the kernel and hook
    distiller are the prompts that actually shaped the drifted candidates, so
    the channel must reach THEM, not just the generic builder."""

    def test_engine_kernel_carries_audience(self):
        from bestseller.services.concept_tournament import _build_engine_kernel_messages

        _, user = _build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="纯题材直觉",
            chapter_count=50, audience_orientation="男频",
        )
        assert "男频" in user

    def test_hook_distiller_carries_audience(self):
        from bestseller.services.concept_tournament import _build_hook_from_engine_messages

        _, user = _build_hook_from_engine_messages(
            genre="玄幻", sub_genre="玄幻", kernel={"protagonist_identity": "外门弟子"},
            audience_orientation="男频",
        )
        assert "男频" in user

    def test_empty_audience_adds_no_noise(self):
        from bestseller.services.concept_tournament import _build_engine_kernel_messages

        _, user = _build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="纯题材直觉", chapter_count=50,
        )
        assert "频道/受众" not in user


class TestJudgePersonaPriming:
    """The tournament judge's click axis said "目标读者3秒内想不想点" but never
    said WHO the target reader is — a neutral-editor voice passed jargon-heavy
    concepts that the downstream persona judge then vetoed 0/3 (round 6,
    2026-07-17), too late for any retry to fix the concept. The channel reader
    must be defined where the click score is born."""

    def test_judge_prompt_defines_channel_reader(self):
        from bestseller.services.concept_tournament import _build_judge_messages
        from types import SimpleNamespace

        cand = SimpleNamespace(
            concept="c", mechanism="m", hook_question="q",
            protagonist_identity="i", protagonist_private_desire="d",
            opening_crisis="o", opponent_system="s", decision_proof="p",
            emotional_promise="e",
        )
        _, user = _build_judge_messages(
            candidate=cand, genre="玄幻", sub_genre="玄幻", references=[],
            audience_orientation="男频",
        )
        assert "男频" in user and "划走" in user

    def test_judge_prompt_without_channel_is_unchanged_shape(self):
        from bestseller.services.concept_tournament import _build_judge_messages
        from types import SimpleNamespace

        cand = SimpleNamespace(
            concept="c", mechanism="m", hook_question="q",
            protagonist_identity="i", protagonist_private_desire="d",
            opening_crisis="o", opponent_system="s", decision_proof="p",
            emotional_promise="e",
        )
        _, user = _build_judge_messages(
            candidate=cand, genre="玄幻", sub_genre="玄幻", references=[],
        )
        assert "划走" not in user


class TestTwoTierHardFloors:
    """Eight simultaneous 7.0+ floors vs a judge whose scores cluster at 6-7 =
    joint pass probability near zero (8 rounds, 30+ candidates, zero contenders;
    round 8: mech 9.0 / click 8.0 candidates killed solely by plain 6.0).
    Two tiers instead: any axis below the catastrophe line is fatal; otherwise
    exactly one soft miss is tolerated — the downstream logline/persona gates
    remain the real authority."""

    _FLOORS = {
        "freshness": 6.0, "click": 7.0, "predictable_max": 5.5,
        "character_logic": 7.0, "mechanism_causality": 7.0,
        "genre_fidelity": 7.0, "plain_language": 7.0, "story_motion": 7.0,
    }

    def _verdict(self, **scores):
        from bestseller.services.concept_tournament import _hard_floor_failed_axes

        base = {
            "freshness": 7.0, "click": 8.0, "predictable": 4.0,
            "character_logic": 8.0, "mechanism_causality": 8.0,
            "genre_fidelity": 8.0, "plain_language": 8.0, "story_motion": 8.0,
        }
        base.update(scores)
        return _hard_floor_failed_axes(base, self._FLOORS)

    def test_one_soft_miss_is_tolerated(self):
        assert self._verdict(plain_language=6.0) == []

    def test_two_soft_misses_reject_with_both_axes_named(self):
        failed = self._verdict(plain_language=6.0, freshness=5.5)
        assert "大白话" in failed and "新颖度" in failed

    def test_catastrophe_axis_is_fatal_even_when_everything_else_is_perfect(self):
        failed = self._verdict(genre_fidelity=2.5)
        assert "题材保真" in failed

    def test_predictable_catastrophe_is_fatal(self):
        assert "可预测性" in self._verdict(predictable=8.0)

    def test_predictable_soft_miss_is_tolerated(self):
        assert self._verdict(predictable=6.0) == []

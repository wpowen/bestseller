from __future__ import annotations

import pytest

from bestseller.domain.enums import IntentDiffSeverity
from bestseller.services import conception


def _valid_reconciliation_architecture() -> dict:
    units = []
    for index, family in enumerate(("证据", "关系", "程序", "公开"), start=1):
        units.append(
            {
                "id": f"u{index}",
                "input": f"第{index}阶段的新事实",
                "goal": f"完成第{index}阶段目标",
                "choice": f"主角选择第{index}种行动并放弃退路",
                "choice_mode": f"选择模式{index}",
                "mechanism_family": family,
                "counter": f"对手以第{index}种策略反制",
                "counter_mode": f"反制模式{index}",
                "cost": f"主角失去第{index}项筹码",
                "cost_mode": f"代价类型{index}",
                "state_change": f"状态从{index - 1}变为{index}",
                "state_axis": f"状态轴{index}",
                "payoff": f"兑现第{index}类承诺",
                "caused_by": "" if index == 1 else f"u{index - 1}",
                "next_pressure": f"下一阶段压力{index}",
            }
        )
    return {
        "schema_version": "story-architecture.v1",
        "target_chapters": 60,
        "grammar": {"key": "都市悬疑", "required_contracts": []},
        "genre_contracts": {
            key: {
                "rule": f"可审计的{key}规则",
                "observable_state": f"可观察的{key}状态",
                "failure_consequence": f"违反{key}后的后果",
            }
            for key in (
                "source_world_profession",
                "target_world_dominant_rule_system",
                "cross_system_mapping_limits",
                "host_identity_obligation",
                "exposure_cost_model",
                "measurable_progression_system",
                "resource_accounting",
                "opponent_pressure",
                "cost_boundary",
                "payoff_ladder",
                "fair_play_clue_path",
                "suspect_state_map",
                "rule_or_case_boundary",
                "revelation_ladder",
                "dread_or_pressure_curve",
            )
        },
        "causal_units": units,
        "progression": [
            {"name": "轨道一", "from": "初始状态", "to": "阶段状态"},
            {"name": "轨道二", "from": "阶段状态", "to": "终局状态"},
        ],
        "relationships": [
            {
                "a": "主角",
                "b": "对手",
                "tension": "目标冲突",
                "initial_state": "互不信任",
                "turning_choice": "主角公开证据",
                "changed_state": "对手改变策略",
                "change": "关系升级",
            }
        ],
        "conflicts": [
            {"actor": "对手甲", "goal": "阻止主角", "tactics": ["封锁", "误导"]},
            {"actor": "对手乙", "goal": "夺取证据", "tactics": ["交易", "施压"]},
        ],
        "opponent_adaptations": [
            {
                "after_unit": "u1",
                "actor": "对手甲",
                "learned": "主角的行动路径",
                "strategy_change": "改用程序封锁",
                "forces_new_choice": "主角转向公开证据",
            },
            {
                "after_unit": "u2",
                "actor": "对手乙",
                "learned": "主角的关系弱点",
                "strategy_change": "改用关系施压",
                "forces_new_choice": "主角切断旧关系",
            },
        ],
        "story_engine": {
            "core_engine": "每轮以新证据推动下一次调查",
            "payoff_rhythm": "每轮兑现一项证据发现",
            "reader_promise": "持续追查并验证真相",
            "endgame_direction": "完成终局追责",
        },
    }
from bestseller.services.creation_intent_contract import (
    build_creation_intent_contract,
    diff_creation_intents,
)
from bestseller.services.genre_intent_contract import contract_from_selection

pytestmark = pytest.mark.unit


def _effect_contract_stub():
    calls = [0]

    async def fake_effect_json(*_args, **kwargs):
        template = str(kwargs.get("template") or "")
        if template.endswith("effect_contract"):
            calls[0] += 1
            prompt = str(kwargs.get("user_prompt") or "")
            mechanism = "读取并重组流通规则"
            if mechanism not in prompt:
                return (
                    {"applicability": "unknown", "operations": []},
                    [f"00000000-0000-4000-8000-{calls[0]:012d}"],
                )
            return (
                {
                    "applicability": "applicable",
                    "causal_mode": "changes_target_state",
                    "mechanism_quote": mechanism,
                },
                [f"00000000-0000-4000-8000-{calls[0]:012d}"],
            )
        raise AssertionError(f"unexpected unmocked LLM template: {template}")

    return fake_effect_json


def test_same_creation_identity_has_no_diff() -> None:
    genre = contract_from_selection({"channel": "male", "genre": "xianxia"})
    contract = build_creation_intent_contract(genre, chapter_count=50)

    assert diff_creation_intents(contract, contract).items == ()


def test_revision_cannot_silently_replace_hook_or_genre_identity() -> None:
    old = build_creation_intent_contract(
        contract_from_selection({"channel": "male", "genre": "xianxia"}),
        hook_spec={"mechanism_key": "forge-debt"},
    )
    new = old.model_copy(
        update={
            "hook_spec": {"mechanism_key": "bloodline-awakening"},
            "genre_intent": contract_from_selection({"channel": "male", "genre": "xuanhuan"}),
        }
    )

    diff = diff_creation_intents(old, new)
    assert diff.has_hard_conflicts
    assert any(
        item.path == "hook_spec.mechanism_key" and item.severity is IntentDiffSeverity.HARD
        for item in diff.items
    )
    assert any(
        item.path == "genre_intent.genre_key" and item.severity is IntentDiffSeverity.HARD
        for item in diff.items
    )


def test_materialized_protagonist_identity_is_a_hard_diff() -> None:
    diff = diff_creation_intents(
        {"book_spec": {"protagonist": {"name": "庄溯", "age": 19}}},
        {"book_spec": {"protagonist": {"name": "陆沉舟", "age": 14}}},
    )
    assert {item.path for item in diff.hard_conflicts} == {
        "book_spec.protagonist.age",
        "book_spec.protagonist.name",
    }


def test_explicit_seed_always_reconciles_contract_when_names_already_match() -> None:
    assert conception._should_reconcile_concept_contract(
        explicit_concept_seed="林策经营七日一迁的移动驿城。",
        authoritative_name="林策",
        winner_identity="现代应急调度员林策",
        spine_identity="移动驿城代理城主林策",
        infant_precision_candidate=False,
    )


def test_seedless_matching_contract_reconciles_final_materialization_once() -> None:
    assert conception._should_reconcile_concept_contract(
        explicit_concept_seed="",
        authoritative_name="林策",
        winner_identity="现代应急调度员林策",
        spine_identity="移动驿城代理城主林策",
        infant_precision_candidate=False,
    )


def test_materialized_body_fingerprint_ignores_packaging_fields_and_tracks_core_body() -> None:
    base = {
        "premise": "维修员林野今晚保住旧城线路。",
        "writing_profile": {
            "character": {"name": "林野", "golden_finger": "读懂故障记录"},
            "market": {"reader_promise": "在倒计时中查明事故原因"},
        },
        "story_spine": {"who": "维修员林野", "why_now": "今晚断电"},
        "world_model": {"rules": ["线路故障必须留下可验痕迹"]},
    }
    same_body = conception._materialized_story_body_fingerprint(**base)
    changed_promise = conception._materialized_story_body_fingerprint(
        **{**base, "writing_profile": {**base["writing_profile"], "market": {"reader_promise": "守住线路并追责"}}}
    )
    assert same_body == conception._materialized_story_body_fingerprint(**base)
    assert changed_promise != same_body


def test_final_canonical_body_projection_excludes_packaging_and_review_artifacts() -> None:
    projected = conception._project_final_canonical_body(
        premise="维修员守住旧城线路。",
        writing_profile={
            "character": {"name": "林野"},
            "market": {
                "reader_promise": "在倒计时中查明事故原因",
                "title_workflow_primary": {"title": "评审候选"},
            },
            "concept_methodology": {"score": 9},
        },
        story_spine={"who": "维修员林野"},
        story_architecture={"causal_units": [], "reconciliation_lineage": {"id": "audit"}},
        world_model={"rules": ["线路故障留下痕迹"]},
    )
    assert projected["writing_profile"] == {
        "character": {"name": "林野"},
        "market": {"reader_promise": "在倒计时中查明事故原因"},
    }
    assert "reconciliation_lineage" not in projected["story_architecture"]
    assert "title_workflow_primary" not in projected["writing_profile"].get("market", {})
    assert "concept_methodology" not in projected["writing_profile"]


@pytest.mark.asyncio
async def test_final_premise_reconciliation_replaces_stale_champion_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_llm_call_json(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        captured["fallback"] = kwargs["fallback"]
        return (
            {
                "concept": "纪赊把自己做成账上死人",
                "mechanism": "辨墨查账，每次改账都会把风险转移给具体活人",
                "hook_question": "纪赊能否翻出司账族灭口真相？",
                "protagonist_identity": "矿场贱民纪赊，司账族遗脉",
                "protagonist_private_desire": "活过今夜并查清灭族真相",
                "protagonist_flaw": "习惯把人命先算成账",
                "core_abnormality": "辨墨之眼",
                "opening_crisis": "今夜账上第一行就是纪赊",
                "opponent_system": "中层账房与灭口司账族的势力",
                "decision_proof": "不改账今夜就会被盖掉",
                "emotional_promise": "以账翻命的步步反杀",
                "repeatable_story_unit": "辨一笔假账并承担转移风险",
                "unit_families": ["查假账", "藏活人", "追灭口线"],
                "unit_frequency": "每2-4章一次",
                "unit_count_estimate": 13,
                "renewal_sources": ["新账层", "新债主"],
                "accumulation_tracks": ["暴露度", "盟友债"],
                "phase_transitions": [
                    "第1-16章矿场",
                    "第17-34章账阁",
                    "第35-50章总账公开战",
                ],
                "opposing_ecology": ["监工", "账阁"],
                "question_ladder": ["谁改账", "谁灭族", "谁掌总账"],
                "endgame_direction": "翻掉司账总账并公开灭族真相",
                "story_architecture": _valid_reconciliation_architecture(),
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)

    repaired, _ = await conception._reconcile_concept_seed_with_final_premise(
        object(),
        object(),
        winner={
            "concept": "陆沉与吸命铜钱",
            "protagonist_identity": "矿工陆沉",
            "core_abnormality": "吸命铜钱",
        },
        premise="矿场贱民纪赊天生一双辨墨之眼。",
        synopsis="包装新增的独有虚构案件SYNOPSIS_ONLY_SENTINEL。",
        writing_profile={"character": {"golden_finger": "辨墨之眼"}},
        authoritative_name="纪赊",
        ctx={"genre": "玄幻", "sub_genre": "矿场求生", "chapter_count": 50},
    )

    assert repaired["protagonist_identity"] == "矿场贱民纪赊，司账族遗脉"
    assert repaired["core_abnormality"] == "辨墨之眼"
    assert "最终 premise：矿场贱民纪赊天生一双辨墨之眼" in captured["prompt"]
    assert "旧冠军种子" in captured["prompt"]
    assert "吸命铜钱" not in captured["prompt"]
    assert captured["fallback"] == "{}"
    assert repaired["reconciliation_lineage"]["source_kind"] == "final_materialization"

    assert "SYNOPSIS_ONLY_SENTINEL" not in captured["prompt"]


@pytest.mark.asyncio
async def test_seedless_reconciliation_failure_never_returns_old_champion(monkeypatch):
    from bestseller.services.concept_contract import ConceptContractError

    calls = []

    async def unavailable(*args, **kwargs):
        calls.append(kwargs)
        return {}, []

    monkeypatch.setattr(conception, "_llm_call_json", unavailable)
    with pytest.raises(ConceptContractError, match="CONCEPT_RECONCILIATION_UNRESOLVED"):
        await conception._reconcile_concept_seed_with_final_premise(
            object(), object(), winner={"concept": "旧角色与旧案件"},
            premise="新角色调查新事件", synopsis="新角色开始行动", writing_profile={},
            authoritative_name="新角色", ctx={"chapter_count": 20},
        )
    assert len(calls) == 2
    assert all(call["fallback"] == "{}" for call in calls)


@pytest.mark.asyncio
async def test_explicit_reconciliation_repairs_seriality_without_replaying_rejected_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    base = {
        "concept": "林策经营七日一迁的移动驿城",
        "mechanism": "每次迁徙前用交换意愿和单次风险组织居民完成一项经营选择",
        "hook_question": "林策能否把避难点经营成各族离不开的中立商城？",
        "protagonist_identity": "现代应急调度员、移动驿城代理城主林策",
        "protagonist_private_desire": "让七户居民在下一次兽潮前愿意留下",
        "protagonist_flaw": "习惯把人当调度节点",
        "core_abnormality": "只能看见交换意愿和一次选择的风险",
        "opening_crisis": "沉睡巨兽七天后必须迁移",
        "opponent_system": "争夺移动商路控制权的族群与旧城势力",
        "decision_proof": "不行动城市会被兽潮吞没，错误调度会失去居民信任",
        "emotional_promise": "经营升级、族群博弈与规则错位喜剧",
        "repeatable_story_unit": "林策组织一次交易或迁徙选择，对手据此改变下一轮条件",
        "unit_families": ["迁徙选址", "族群交易", "居民经营", "对手封锁"],
        "unit_frequency": "每周迁徙一次",
        "unit_count_estimate": 15,
        "renewal_sources": ["居民选择后果", "对手学习反制"],
        "accumulation_tracks": ["商城信用", "巨兽路线控制权"],
        "phase_transitions": ["第1-20章", "第21-40章", "第41-60章"],
        "opposing_ecology": ["商路旧盟", "逐水部族"],
        "question_ladder": ["巨兽为何苏醒", "谁控制迁徙线", "中立城归谁"],
        "endgame_direction": "决定移动驿城是公器还是新垄断",
        "story_architecture": _valid_reconciliation_architecture(),
    }

    async def fake_llm_call_json(*_args, **kwargs):
        prompts.append(kwargs["user_prompt"])
        payload = dict(base)
        if len(prompts) == 2:
            payload["unit_frequency"] = "每2-4章一次经营选择与对手反制"
        return payload, []

    async def fidelity_pass(*_args, **_kwargs):
        return {"passed": True, "hard_conflicts": []}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    monkeypatch.setattr(
        conception,
        "_judge_tournament_winner_seed_fidelity",
        fidelity_pass,
    )

    repaired, _ = await conception._reconcile_concept_seed_with_final_premise(
        object(),
        object(),
        winner={
            "concept": "卫七签在金渡口经营合单脚夫铺",
            "protagonist_identity": "卫七签",
            "core_abnormality": "调度眼",
        },
        premise="林策经营七日一迁的移动驿城。",
        synopsis="他要把七户居民的避难点经营成中立商城。",
        writing_profile={"character": {"golden_finger": "交换意愿与单次风险"}},
        authoritative_name="林策",
        ctx={"genre": "玄幻", "sub_genre": "异世大陆", "chapter_count": 60},
        concept_seed="应急调度员林策穿越成移动驿城代理城主，城市每七天必须迁移。",
    )

    assert len(prompts) == 2
    assert "unit_frequency_unproven_or_sparse" in prompts[1]
    assert "卫七签" not in "\n".join(prompts)
    assert "source_world_profession" in prompts[0]
    assert '"required_contract":' not in prompts[0]
    assert "causal_units" in prompts[0]
    import json
    from bestseller.services.story_viability import _ARCHITECTURE_UNIT_FIELDS

    schema_text = prompts[0].split("严格按此 schema 返回全部字段：", 1)[1]
    requested_schema, _ = json.JSONDecoder().raw_decode(schema_text)
    units = requested_schema["story_architecture"]["causal_units"]
    assert all(set(_ARCHITECTURE_UNIT_FIELDS) <= set(unit) for unit in units)
    assert len({unit["id"] for unit in units}) == len(units)
    assert units[1]["caused_by"] == units[0]["id"]
    assert repaired["unit_frequency"].startswith("每2-4章一次")


@pytest.mark.asyncio
async def test_explicit_reconciliation_reuses_proven_seriality_before_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    winner = {
        "concept": "旧的短概念",
        "mechanism": "班组长本能把成交价写成下一笔底价",
        "hook_question": "商洛能否守住收费权？",
        "protagonist_identity": "收费站班组长商洛",
        "protagonist_private_desire": "经营废城",
        "protagonist_flaw": "习惯先开单再讲人情",
        "core_abnormality": "旧界碑碎片会向商旅代表显示报价",
        "opening_crisis": "第一支商队拒绝进城",
        "opponent_system": "绕道据点与商队联盟",
        "decision_proof": "首单失败就再无商队停靠",
        "emotional_promise": "经营升级与规则错位喜剧",
        "repeatable_story_unit": "商队到来、报价、反制、成交并留下新压力",
        "unit_families": ["商队议价", "据点反制", "城建投入"],
        "unit_frequency": "每2-4章一次",
        "unit_count_estimate": 15,
        "renewal_sources": ["新商队", "据点学习反制"],
        "accumulation_tracks": ["城建资产", "通行信用"],
        "phase_transitions": ["第1-20章", "第21-40章", "第41-60章"],
        "opposing_ecology": ["绕道据点", "商队联盟"],
        "question_ladder": ["首单能否成交", "收费权能否成规", "北线能否承认"],
        "endgame_direction": "把废城经营成北线交通节点",
    }
    captured: dict[str, object] = {}

    async def fidelity_pass(*_args, **kwargs):
        captured.update(kwargs)
        return {"passed": True, "hard_conflicts": []}, []

    async def rebuild(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                **winner,
                "concept": premise,
                "mechanism": "拍界碑显示实物报价",
                "core_abnormality": "拍界碑显示实物报价",
                "protagonist_identity": "废城挂名城主商洛",
                "protagonist_private_desire": "把废城经营成北线最贵关卡",
                "opening_crisis": "赤鬃狮人商队正在城门外拒绝进城",
                "opponent_system": "抢客的绕道据点与会学习规则的商队",
                "decision_proof": synopsis,
                "hook_question": "商洛能否让整条北线承认他的收费规矩？",
                "story_architecture": _valid_reconciliation_architecture(),
            },
            [],
        )

    monkeypatch.setattr(
        conception,
        "_judge_tournament_winner_seed_fidelity",
        fidelity_pass,
    )
    monkeypatch.setattr(conception, "_llm_call_json", rebuild)

    premise = "商洛醒来成了废城挂名城主，拍旧界碑给狮人商队开出过路价。"
    synopsis = "七名老矿工后裔守着烂木门，商洛收下首单后说：下一位。"
    repaired, ids = await conception._reconcile_concept_seed_with_final_premise(
        object(),
        object(),
        winner=winner,
        premise=premise,
        synopsis=synopsis,
        writing_profile={"character": {"golden_finger": "拍界碑显示实物报价"}},
        authoritative_name="商洛",
        ctx={"genre": "玄幻", "sub_genre": "异世大陆", "chapter_count": 60},
        concept_seed="收费站班组长商洛穿越成废城城主，拍界碑向狮人显示过路价。",
        story_spine={
            "who": "前收费站班组长、废城挂名城主商洛",
            "wants": "把废城经营成北线最贵的关卡",
            "why_now": "赤鬃狮人商队正在城门外拒绝进城",
            "against": "抢客的绕道据点与会学习规则的商队",
            "question": "商洛能否让整条北线承认他的收费规矩？",
        },
    )

    assert ids == []
    assert repaired["concept"] == premise
    assert repaired["decision_proof"] == synopsis
    assert repaired["mechanism"] == "拍界碑显示实物报价"
    assert repaired["core_abnormality"] == "拍界碑显示实物报价"
    assert repaired["protagonist_identity"].endswith("商洛")
    assert repaired["unit_frequency"] == "每2-4章一次"
    assert captured["premise"] == premise
    assert captured["synopsis"] == synopsis
    assert captured["writing_profile"] == {
        "character": {"golden_finger": "拍界碑显示实物报价"}
    }
    assert "旧的短概念" not in str(captured["prompt"])
    assert repaired["story_architecture"]["story_engine"]["core_engine"] == (
        "每轮以新证据推动下一次调查"
    )
    assert repaired["story_architecture"]["reconciliation_lineage"]["schema_version"] == (
        "concept-reconciliation.v1"
    )


@pytest.mark.asyncio
async def test_contract_seed_fidelity_judges_final_story_and_seriality_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def judge(*_args, **kwargs):
        captured.update(kwargs)
        return {"passed": True, "hard_conflicts": []}, []

    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", judge)

    await conception._judge_tournament_winner_seed_fidelity(
        object(),
        object(),
        concept_seed="商洛拍界碑向狮人商队显示过路价。",
        winner={
            "concept": "商洛经营废城",
            "mechanism": "每2-4章处理一轮新商队与绕道反制",
            "protagonist_identity": "废城城主商洛",
            "unit_families": ["议价", "反制", "城建"],
        },
        ctx={"genre": "玄幻", "language": "zh-CN"},
        premise="商洛拍旧界碑，狮人耳边浮出盐与火油的报价。",
        synopsis="七名矿工后裔守住烂木门，邻族探子看见首单成交。",
        writing_profile={"market": {"reader_promise": "经营废城"}},
    )

    final_result = captured["final_result"]
    assert captured["fidelity_stage"] == "tournament_winner"
    assert "title" not in final_result
    assert "tags" not in final_result
    assert final_result["candidate_contract"]["protagonist_identity"] == "废城城主商洛"
    assert final_result["premise"].startswith("商洛拍旧界碑")
    assert final_result["synopsis"].startswith("七名矿工后裔")
    assert "每2-4章" in final_result["synopsis"]
    assert final_result["writing_profile"]["market"]["reader_promise"] == "经营废城"
    assert final_result["writing_profile"]["seriality"]["unit_families"] == [
        "议价",
        "反制",
        "城建",
    ]


@pytest.mark.asyncio
async def test_tournament_fidelity_ignores_unavailable_packaging_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def mistaken_judge(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "passed": False,
                "hard_conflicts": [
                    {
                        "asset": "title",
                        "fact": "主角商洛",
                        "generated": "标题为空",
                        "reason": "标题未体现主角",
                    },
                    {
                        "asset": "tags",
                        "fact": "经营喜剧",
                        "generated": "标签尚未生成",
                        "reason": "缺少题材标签",
                    },
                ],
                "repair_directives": ["补充书名", "补齐缺失标签"],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", mistaken_judge)
    report, _ = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="商洛拍界碑向狮人显示报价，并把废城经营成最贵关卡。",
        final_result={
            "candidate_contract": {
                "concept": "旧界碑当场给狮人开价。",
                "protagonist_identity": "收费站班组长商洛",
                "mechanism": "商洛拍界碑后，狮人耳中浮出具体报价",
                "endgame_direction": "把废城经营成过路费最贵的关卡",
            },
            "premise": "商洛接管废城。",
        },
        ctx={"language": "zh-CN", "genre": "异世大陆"},
        fidelity_stage="tournament_winner",
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert len(report["stage_ignored_findings"]) == 2
    assert report["repair_directives"] == []
    assert "书名和标签在此阶段尚未生成" in captured["system"]
    assert "不得要求一句话 concept 单独复述全部用户事实" in captured["system"]
    assert "【终稿资产：title】" not in captured["prompt"]
    assert "【终稿资产：tags】" not in captured["prompt"]


@pytest.mark.asyncio
async def test_final_premise_reconciliation_adds_infant_body_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_llm_call_json(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return ({}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)

    from bestseller.services.concept_contract import ConceptContractError

    with pytest.raises(ConceptContractError):
        await conception._reconcile_concept_seed_with_final_premise(
            object(),
            object(),
            winner={"protagonist_identity": "姬衡", "mechanism": "精确假哭操控萧崇"},
            premise="废太子姬衡重生为襁褓中的三个月婴儿。",
            synopsis="姬衡在仇人膝上求生。",
            writing_profile={},
            authoritative_name="姬衡",
            ctx={"genre": "玄幻", "sub_genre": "重生", "chapter_count": 50},
        )

    assert "前世记忆、判断和内心策略属于合法知识来源" in captured["prompt"]
    assert "不得把精确假哭节拍" in captured["prompt"]
    assert "重大转折必须由有独立动机的成人角色" in captured["prompt"]
    assert "不得继续把婴啼、注视、抓握" in captured["prompt"]
    assert "成人阵营因各自利益行动" in captured["prompt"]


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_gate_rejects_replaced_named_protagonist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def permissive_judge(*_args, **_kwargs):
        return (
            {
                "passed": True,
                "hard_conflicts": [],
                "preserved_facts": ["都市异能"],
                "repair_directives": [],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", permissive_judge)

    report, _ = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed=("审计员沈砚被栽赃。他发现伪造合同会显出真正签署人的指纹，证据只保留三分钟。"),
        final_result={
            "premise": "工程监理岑野获得感知切片权限，看见八秒彩色残像。",
            "synopsis": "岑野追查空钢印。",
        },
        ctx={"language": "zh-CN", "genre": "都市异能"},
    )

    assert report["passed"] is False
    assert any("沈砚" in str(item) for item in report["hard_conflicts"])
    assert any("沈砚" in item for item in report["repair_directives"])


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_gate_does_not_block_judge_labeled_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def contradictory_judge(*_args, **_kwargs):
        return (
            {
                "passed": False,
                "hard_conflicts": [
                    {
                        "fact": "失去职业资格",
                        "generated": "审计执业资格吊销",
                        "reason": "终稿保留并细化，符合扩写，不构成替换。",
                    }
                ],
                "preserved_facts": ["沈砚", "合同指纹", "三分钟"],
                "repair_directives": [],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", contradictory_judge)
    report, _ = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="审计员沈砚能看见合同指纹，代价是失去职业资格。",
        final_result={"premise": "审计员沈砚能看见合同指纹，最终审计执业资格被吊销。"},
        ctx={"language": "zh-CN", "genre": "都市异能"},
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert len(report["compatible_extensions"]) == 1


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_gate_rejects_core_trigger_object_demotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def permissive_judge(*_args, **_kwargs):
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    monkeypatch.setattr(conception, "_llm_call_json", permissive_judge)

    report, _ = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="商洛抬手一拍旧界碑碎片，狮人耳中直接浮出一行报价。",
        final_result={
            "premise": "商洛接手废城并重开商路。",
            "synopsis": "旧界碑碎片只负责提示，真正的金手指是商洛的收费站职业本能。",
        },
        ctx={"language": "zh-CN", "genre": "异世大陆"},
    )

    assert report["passed"] is False
    assert any("能力触发物降级" in item["reason"] for item in report["hard_conflicts"])
    assert conception._seed_fidelity_has_non_appealable_conflict(report)


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_evaluator_unavailable_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable_judge(*_args, **kwargs):
        import json

        return (json.loads(kwargs["fallback"]), [])

    monkeypatch.setattr(conception, "_llm_call_json", unavailable_judge)

    report, _ = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="商洛经营废城。",
        final_result={"premise": "商洛经营废城。"},
        ctx={"language": "zh-CN", "genre": "异世大陆"},
    )

    assert report["passed"] is False
    assert report["evaluator_error"] == "judge_unavailable_fail_closed"
    assert report["hard_conflicts"][0]["source"] == "evaluator_unavailable"


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_repair_uses_seed_without_replaying_bad_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_repair(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "title": "三分钟指纹",
                "premise": "审计员沈砚追查伪造合同留下的三分钟指纹。",
                "synopsis": "沈砚必须在证据消失前拿到原始合同。",
                "tags": ["审计", "异能"],
                "writing_profile": {},
                "story_spine": {},
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)
    repaired, _ = await conception._repair_final_result_to_explicit_seed(
        object(),
        object(),
        concept_seed="审计员沈砚能看见伪造合同的三分钟指纹。",
        final_result={"premise": "监理岑野追查空钢印。"},
        report={
            "passed": False,
            "repair_directives": ["恢复沈砚与合同指纹机制"],
        },
        ctx={"language": "zh-CN", "genre": "都市异能"},
    )

    assert repaired["premise"].startswith("审计员沈砚")
    assert "用户明确故事创意" in captured["prompt"]
    assert "监理岑野追查空钢印" not in captured["prompt"]
    assert "恢复沈砚与合同指纹机制" not in captured["prompt"]
    assert "被拒终稿和判官自由文本已隔离" in captured["prompt"]


@pytest.mark.asyncio
async def test_tournament_winner_seed_repair_preserves_seriality_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services.concept_tournament import ConceptCandidate

    original = ConceptCandidate(
        dimension="世界规则#1",
        concept="商洛主动压价逼狮人签字。",
        mechanism="主角举牌叫价。",
        endgame_direction="终局在放弃收费权与并入王庭之间二选一。",
        unit_families=("商队竞价", "行会压价", "邻族抽成", "王庭收编"),
        renewal_sources=("新商族", "新据点", "新行会"),
        phase_transitions=("1-15章", "16-30章", "31-45章", "46-60章"),
        seriality_report={"passed": True, "estimated_chapter_ceiling": 60},
        seriality_judge={"renewability": 7.0, "unit_density": 7.0},
        judge_click=7.0,
        composite=6.2,
    )
    captured: dict[str, str] = {}

    async def fake_repair(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "candidate": {
                    **{
                        key: value
                        for key, value in original.to_dict().items()
                        if not key.startswith("judge_")
                        and key
                        not in {
                            "dimension",
                            "composite",
                            "rejected_reason",
                            "seriality_report",
                            "seriality_judge",
                        }
                    },
                    "concept": "商洛拍响旧界碑，狮人耳根浮出盐与火油的固定价。",
                    "mechanism": "每次拍碑，过路者耳根自动浮价。",
                    "endgame_direction": "把废城一路经营成过路费最贵的关卡。",
                }
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)
    repaired, _ = await conception._repair_tournament_winner_to_explicit_seed(
        object(),
        object(),
        concept_seed="商洛拍界碑让狮人耳根浮价，最终把废城建成最贵关卡。",
        winner=original,
        report={"repair_directives": ["恢复拍碑浮价，不得由主角主动压价。"]},
        ctx={"language": "zh-CN"},
    )

    assert repaired.concept.startswith("商洛拍响旧界碑")
    assert repaired.mechanism == "每次拍碑，过路者耳根自动浮价。"
    assert repaired.endgame_direction == "把废城一路经营成过路费最贵的关卡。"
    assert repaired.seriality_report == original.seriality_report
    assert repaired.seriality_judge == original.seriality_judge
    assert repaired.judge_click == 7.0
    assert repaired.composite == 6.2
    assert "judge_click" not in captured["prompt"]
    assert "恢复拍碑浮价" in captured["prompt"]


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_final_repair_uses_conservative_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_repair(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return ({"premise": "审计员沈砚看见三分钟指纹。"}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)

    await conception._repair_final_result_to_explicit_seed(
        object(),
        object(),
        concept_seed="审计员沈砚能看见伪造合同的三分钟指纹。",
        final_result={"premise": "沈砚获得可升级的永久指纹能力。"},
        report={"passed": False, "repair_directives": ["删除能力升级"]},
        ctx={"language": "zh-CN", "genre": "都市异能"},
        attempt=3,
        max_attempts=3,
    )

    assert "修复轮次：3/3" in captured["prompt"]
    assert "最后一次保守修复" in captured["prompt"]
    assert "逐项核对用户已明确的机制、边界、时限、目标、数额和代价" in captured["prompt"]
    assert "新增内容须与明确事实、禁止项和冻结题材兼容" in captured["system"]
    assert "可升级的永久指纹能力" not in captured["prompt"]


@pytest.mark.asyncio
async def test_materialized_story_fidelity_checks_profile_spine_and_world(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def rejecting_judge(*_args, **kwargs):
        captured["final_result"] = kwargs["final_result"]
        return (
            {
                "passed": False,
                "hard_conflicts": [{"reason": "金手指被残页替换"}],
                "repair_directives": [],
            },
            [],
        )

    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", rejecting_judge)
    monkeypatch.setattr(conception, "_llm_call_json", _effect_contract_stub())

    async def no_repair(*_args, **_kwargs):
        return {}, []

    monkeypatch.setattr(conception, "_repair_final_result_to_explicit_seed", no_repair)

    async def rejecting_arbitrator(*_args, **kwargs):
        return (dict(kwargs["challenged_report"]), [])

    monkeypatch.setattr(
        conception,
        "_arbitrate_explicit_seed_fidelity_report",
        rejecting_arbitrator,
    )

    with pytest.raises(Exception, match="FINAL_STORY_ARCHITECTURE_SEED_FIDELITY_UNRESOLVED"):
        await conception._enforce_materialized_story_seed_fidelity(
            object(),
            object(),
            concept_seed="主角读取并重组流通规则经营废城。",
            title="流通之城",
            premise="主角经营废城。",
            synopsis="他重建城市循环。",
            tags=["经营"],
            writing_profile={"character": {"golden_finger": "燃烧记忆的残页"}},
            story_spine={"core_engine": "残页升级"},
            world_model={"laws": ["观星者追猎残页"]},
            ctx={"language": "zh-CN", "genre": "玄幻"},
        )

    projection = captured["final_result"]
    assert projection["writing_profile"]["character"]["golden_finger"] == "燃烧记忆的残页"
    assert projection["story_spine"]["core_engine"] == "残页升级"
    assert projection["world_model"]["laws"] == ["观星者追猎残页"]


@pytest.mark.asyncio
async def test_materialized_story_fidelity_adopts_seed_rebuild_only_after_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = iter(
        [
            (
                {
                    "passed": False,
                    "hard_conflicts": [{"reason": "界碑近亲变体"}],
                    "repair_directives": ["移除界碑近亲变体"],
                },
                [],
            ),
            ({"passed": True, "hard_conflicts": [], "repair_directives": []}, []),
        ]
    )

    async def judge(*_args, **_kwargs):
        return next(reports)

    async def rebuild(*_args, **_kwargs):
        return (
            {
                "title": "废城流转法则",
                "premise": "城市运营负责人读取并重组流通规则，重建边境废城。",
                "synopsis": "他必须在效率、民生与盟约之间做选择。",
                "tags": ["经营", "基建", "喜剧"],
                "writing_profile": {
                    "market": {"reader_promise": "经营废城，争夺大陆交通主权"},
                    "character": {"golden_finger": "读取并重组流通规则"},
                    "world": {"power_system_style": "流通规则可重组，商路封锁限制资源"},
                    "style": {"pov_type": "third-limited"},
                    "serialization": {"opening_mandate": "先让商路封锁造成损失"},
                },
                "story_spine": {
                    "who": "城市运营负责人",
                    "wants": "在封锁扩大前重建边境废城",
                    "why_now": "商路封锁切断了城市补给",
                    "against": "封锁商路的盟约势力",
                    "stakes": "城市居民将失去生存所需的物资",
                    "question": "他能否重建流通规则并守住废城？",
                    "core_engine": "每次改写流通规则都转移真实代价",
                },
                "world_model": {"laws": ["规则改写会把代价转移到城市真实环节"]},
            },
            [],
        )

    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", judge)
    monkeypatch.setattr(conception, "_repair_final_result_to_explicit_seed", rebuild)
    monkeypatch.setattr(conception, "_llm_call_json", _effect_contract_stub())

    async def rejecting_arbitrator(*_args, **kwargs):
        return (dict(kwargs["challenged_report"]), [])

    monkeypatch.setattr(
        conception,
        "_arbitrate_explicit_seed_fidelity_report",
        rejecting_arbitrator,
    )

    story, report, _ = await conception._enforce_materialized_story_seed_fidelity(
        object(),
        object(),
        concept_seed="城市运营负责人读取并重组流通规则经营废城。",
        title="旧标题",
        premise="旧前提",
        synopsis="旧简介",
        tags=["玄幻"],
        writing_profile={"character": {"golden_finger": "界碑"}},
        story_spine={"core_engine": "界碑报价"},
        world_model={"laws": ["界碑定价"]},
        ctx={"language": "zh-CN", "genre": "玄幻", "sub_genre": "异世大陆"},
    )

    assert report["materialized_repaired"] is True
    assert report["materialized_repair_attempt"] == 1
    assert story["writing_profile"]["character"]["golden_finger"] == "读取并重组流通规则"
    assert story["story_spine"]["core_engine"] == "每次改写流通规则都转移真实代价"
    assert story["world_model"]["laws"] == ["规则改写会把代价转移到城市真实环节"]


@pytest.mark.asyncio
async def test_materialized_story_fidelity_arbitrates_false_prosecution_before_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def false_prosecution(*_args, **_kwargs):
        return (
            {
                "passed": False,
                "hard_conflicts": [
                    {
                        "fact": "用户明确要求锈铜路牌",
                        "reason": "把模型候选误标成用户事实",
                    }
                ],
                "repair_directives": [],
            },
            [],
        )

    async def acquitting_arbitrator(*_args, **_kwargs):
        return (
            {
                "passed": True,
                "hard_conflicts": [],
                "compatible_extensions": [],
                "repair_directives": [],
            },
            [],
        )

    async def repair_must_not_run(*_args, **_kwargs):
        raise AssertionError("acquitted materialized story must not be regenerated")

    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", false_prosecution)
    monkeypatch.setattr(conception, "_llm_call_json", _effect_contract_stub())
    monkeypatch.setattr(
        conception,
        "_arbitrate_explicit_seed_fidelity_report",
        acquitting_arbitrator,
    )
    monkeypatch.setattr(conception, "_repair_final_result_to_explicit_seed", repair_must_not_run)

    story, report, _ = await conception._enforce_materialized_story_seed_fidelity(
        object(),
        object(),
        concept_seed="城市运营负责人读取并重组流通规则经营废城。",
        title="废城流转法则",
        premise="城市运营负责人经营边境废城。",
        synopsis="他重建城市生存循环。",
        tags=["经营"],
        writing_profile={"character": {"golden_finger": "读取并重组流通规则"}},
        story_spine={"core_engine": "规则改写会转移真实代价"},
        world_model={"laws": ["道路与契约具有可重组的流通规则"]},
        ctx={"language": "zh-CN", "genre": "玄幻"},
    )

    assert story["title"] == "废城流转法则"
    assert report["passed"] is True
    assert report["arbitration_applied"] is True
    assert report["source_effect_contract"]["status"] == "pass"
    assert report["source_effect_contract"]["candidate_input"]["world_model"] == {
        "laws": ["道路与契约具有可重组的流通规则"]
    }


@pytest.mark.asyncio
async def test_seed_fidelity_judge_excludes_model_authored_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_judge(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_judge)

    await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="城市运营负责人读取并重组流通规则经营废城。",
        final_result={"premise": "城市运营负责人经营边境废城。"},
        ctx={
            "genre": "玄幻",
            "sub_genre": "异世大陆",
            "chapter_count": 30,
            "language": "zh-CN",
            "genre_intent_contract": {"channel_key": "male"},
            "high_concept": {"mechanism": "锈铜路牌，不通车就赔命"},
            "description": "前世给外卖平台排夜班送餐路线的女调度",
            "user_hints": {"generated_retry": "七日内成为新路标"},
        },
    )

    assert "锈铜路牌" not in captured["prompt"]
    assert "女调度" not in captured["prompt"]
    assert "成为新路标" not in captured["prompt"]
    assert '"channel_key": "male"' in captured["prompt"]
    assert "不是用户故事事实" in captured["prompt"]


@pytest.mark.asyncio
async def test_seed_fidelity_prompt_reserves_space_for_late_story_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_judge(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_judge)

    await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="城市运营负责人读取并重组流通规则经营废城。",
        final_result={
            "title": "从鬼城到大陆第一枢纽",
            "premise": "城市运营负责人林枢经营边境废城。",
            "synopsis": "林枢重建城市生存循环。",
            "tags": ["经营"],
            "writing_profile": {"oversized": "甲" * 40000},
            "story_spine": {"who": "庄未明，旧稿主角", "question": "沈砚舟能否翻案？"},
            "world_model": {"laws": ["旧稿当铺契约链"]},
        },
        ctx={"language": "zh-CN", "genre": "玄幻"},
    )

    assert "【终稿资产：writing_profile】" in captured["prompt"]
    assert "【终稿资产：story_spine】" in captured["prompt"]
    assert "庄未明" in captured["prompt"]
    assert "沈砚舟" in captured["prompt"]
    assert "【终稿资产：world_model】" in captured["prompt"]
    assert "旧稿当铺契约链" in captured["prompt"]


@pytest.mark.asyncio
async def test_seed_fidelity_gate_rejects_cross_asset_protagonist_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def permissive_judge(*_args, **_kwargs):
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    monkeypatch.setattr(conception, "_llm_call_json", permissive_judge)
    report, _ = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="现代城市运营负责人穿越异世，重组流通规则经营废城。",
        final_result={
            "premise": "现代城市运营负责人穿越异世，重组流通规则经营废城。",
            "synopsis": "林枢，现代城市运营负责人，在废城重建生存循环。",
            "story_spine": {
                "who": "庄未明，前世是物流主管",
                "question": "沈砚舟能否夺回交通主权？",
            },
        },
        ctx={"language": "zh-CN", "genre": "玄幻"},
    )

    assert report["passed"] is False
    assert any("庄未明" in str(item) for item in report["hard_conflicts"])
    assert any("沈砚舟" in str(item) for item in report["hard_conflicts"])
    assert any("统一 premise" in item for item in report["repair_directives"])


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_arbitration_uses_logical_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_arbitrator(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "passed": True,
                "hard_conflicts": [],
                "compatible_extensions": [
                    {"fact": "午夜十二点", "reason": "与午夜显影可以同时为真"}
                ],
                "preserved_facts": ["三分钟"],
                "repair_directives": [],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_arbitrator)
    report, _ = await conception._arbitrate_explicit_seed_fidelity_report(
        object(),
        object(),
        concept_seed="合同在午夜显出指纹，证据保留三分钟。",
        final_result={"premise": "合同在午夜十二点显出指纹，三分钟后消失。"},
        challenged_report={
            "passed": False,
            "hard_conflicts": [{"reason": "用户未限定必须午夜十二点整"}],
        },
        ctx={"language": "zh-CN", "genre": "都市异能"},
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert "能否同时为真" in captured["system"]
    assert "用户未限定/未禁止/未规定" in captured["prompt"]


@pytest.mark.asyncio
async def test_conception_pipeline_blocks_unrepaired_explicit_seed_drift(monkeypatch) -> None:
    """Exercise the real final boundary, judge, arbitration and bounded repair."""
    from bestseller.services import llm
    from bestseller.services.concept_contract import ConceptContractError

    class ExternalCallTripwire(BaseException):
        pass

    def denied(*args, **kwargs):
        raise ExternalCallTripwire("offline seed boundary must never dispatch a provider")

    calls = []
    effect = _effect_contract_stub()

    async def gateway(*args, **kwargs):
        template = kwargs["template"]
        calls.append(template)
        if template.endswith("effect_contract"):
            return await effect(*args, **kwargs)
        if template in {
            "conception_preproject_materialized_seed_fidelity_judge",
            "conception_preproject_materialized_seed_fidelity_arbitration",
        }:
            return {"passed": False, "hard_conflicts": [{
                "classification": "hard_conflict", "fact": "每段两千字，共八千字",
                "generated": "成品没有保留篇幅数额",
                "reason": "明确数量被删除，而不是合理扩写。",
            }], "repair_directives": ["恢复数量约束。"]}, []
        if template == "conception_explicit_seed_fidelity_repair":
            return {}, []  # Incomplete repair cannot certify the original.
        raise ExternalCallTripwire("unexpected model boundary: " + template)

    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(conception, "complete_text", denied)
    monkeypatch.setattr(conception, "_llm_call_json", gateway)
    with pytest.raises(ConceptContractError, match="FINAL_STORY_ARCHITECTURE_SEED_FIDELITY_UNRESOLVED") as caught:
        await conception._enforce_materialized_story_seed_fidelity(
            object(), object(), concept_seed="主角读取并重组流通规则，故事四段，每段两千字，共八千字。",
            title="流通之城", premise="主角读取并重组流通规则经营废城。",
            synopsis="主角在四个阶段重建城市循环。", tags=["经营"],
            writing_profile={"character": {"golden_finger": "读取并重组流通规则"}},
            story_spine={"core_engine": "读取并重组流通规则"}, world_model={},
            ctx={"language": "zh-CN", "genre": "玄幻"},
        )
    assert calls == [
        "conception_source_effect_contract", "conception_candidate_effect_contract",
        "conception_preproject_materialized_seed_fidelity_judge",
        "conception_preproject_materialized_seed_fidelity_arbitration",
        "conception_explicit_seed_fidelity_repair", "conception_explicit_seed_fidelity_repair",
    ]
    diagnostics = caught.value.materialized_seed_fidelity_diagnostics
    assert len(diagnostics["repair_attempts"]) == 2
    assert all(row["status"] == "incomplete_structure" for row in diagnostics["repair_attempts"])


@pytest.mark.asyncio
async def test_late_materialized_hook_runs_current_review_and_bounded_repair(monkeypatch):
    from bestseller.services import logline_gate
    reviewed = []
    rewritten = []

    async def judge(*args, **kwargs):
        reviewed.append(kwargs['logline'])
        return logline_gate.LoglineGateVerdict(
            action=logline_gate.LoglineAction.REGENERATE if len(reviewed) == 1 else logline_gate.LoglineAction.EXPAND,
            scores={axis: 4.0 for axis in logline_gate.AXIS_KEYS},
            axis_why={axis: {"reason": "表达不清", "repair_scope": "expression"}
                      for axis in logline_gate.AXIS_KEYS},
            overall=3 if len(reviewed) == 1 else 5, llm_used=True,
        )

    async def rewrite(*args, **kwargs):
        rewritten.append(kwargs['premise'])
        return '重新审查后的钩子'

    monkeypatch.setattr(logline_gate, 'evaluate_logline_gate', judge)
    monkeypatch.setattr(conception, '_rewrite_logline_for_gate', rewrite)
    text, report = await conception._recheck_materialized_logline(
        object(), object(), logline='重建后的钩子', premise='最终事实', synopsis='简介',
        genre='现实', sub_genre='', is_en=False, config={},
    )
    assert reviewed == ['重建后的钩子', '重新审查后的钩子']
    assert rewritten == ['最终事实']
    assert text == report['logline_text'] == '重新审查后的钩子'
    assert report['status'] == 'reviewed_after_materialization'


def test_materialized_body_ignores_logline_copy_but_tracks_world_rules():
    base = dict(premise='故事', story_spine={}, world_model={})
    a = conception._materialized_story_body_fingerprint(**base,
        writing_profile={'world': {'rule': '甲'}, 'market': {'logline': '原文案'}})
    b = conception._materialized_story_body_fingerprint(**base,
        writing_profile={'world': {'rule': '甲'}, 'market': {'logline': '新文案'}})
    c = conception._materialized_story_body_fingerprint(**base,
        writing_profile={'world': {'rule': '乙'}, 'market': {'logline': '新文案'}})
    assert a == b
    assert a != c

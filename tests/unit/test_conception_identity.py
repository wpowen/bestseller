from __future__ import annotations

import pytest

from bestseller.domain.enums import IntentDiffSeverity
from bestseller.services import conception
from bestseller.services.creation_intent_contract import (
    build_creation_intent_contract,
    diff_creation_intents,
)
from bestseller.services.genre_intent_contract import contract_from_selection

pytestmark = pytest.mark.unit


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


@pytest.mark.asyncio
async def test_final_premise_reconciliation_replaces_stale_champion_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_llm_call_json(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
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
                "unit_count_estimate": 10,
                "renewal_sources": ["新账层", "新债主"],
                "accumulation_tracks": ["暴露度", "盟友债"],
                "phase_transitions": ["第1-20章矿场", "第21-50章账阁"],
                "opposing_ecology": ["监工", "账阁"],
                "question_ladder": ["谁改账", "谁灭族", "谁掌总账"],
                "endgame_direction": "翻掉司账总账并公开灭族真相",
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
        synopsis="纪赊查清司账族灭口真相。",
        writing_profile={"character": {"golden_finger": "辨墨之眼"}},
        authoritative_name="纪赊",
        ctx={"genre": "玄幻", "sub_genre": "矿场求生", "chapter_count": 50},
    )

    assert repaired["protagonist_identity"] == "矿场贱民纪赊，司账族遗脉"
    assert repaired["core_abnormality"] == "辨墨之眼"
    assert "最终 premise：矿场贱民纪赊天生一双辨墨之眼" in captured["prompt"]
    assert "旧冠军种子" in captured["prompt"]


@pytest.mark.asyncio
async def test_final_premise_reconciliation_adds_infant_body_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_llm_call_json(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return ({}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)

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
        concept_seed=(
            "审计员沈砚被栽赃。他发现伪造合同会显出真正签署人的指纹，"
            "证据只保留三分钟。"
        ),
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
        final_result={
            "premise": "审计员沈砚能看见合同指纹，最终审计执业资格被吊销。"
        },
        ctx={"language": "zh-CN", "genre": "都市异能"},
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert len(report["compatible_extensions"]) == 1


@pytest.mark.asyncio
async def test_explicit_seed_fidelity_repair_receives_authoritative_seed_and_report(
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
    assert "恢复沈砚与合同指纹机制" in captured["prompt"]


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
    assert "最后一次保守锁定修复" in captured["prompt"]
    assert "积分/等级/分段扣除" in captured["prompt"]
    assert "额外能力形态" in captured["system"]


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


def test_conception_pipeline_blocks_unrepaired_explicit_seed_drift() -> None:
    source = conception.run_conception_pipeline.__code__
    assert "_judge_explicit_concept_seed_fidelity" in source.co_names
    assert "_arbitrate_explicit_seed_fidelity_report" in source.co_names
    assert "_repair_final_result_to_explicit_seed" in source.co_names

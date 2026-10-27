"""The contract builder must preserve the winner's declared architecture ending."""

from copy import deepcopy

import pytest

from bestseller.services import conception
from bestseller.services.concept_contract import build_concept_contract, validate_concept_contract
from bestseller.services.story_viability import evaluate_story_viability


def test_contract_keeps_existing_nested_ending_without_rewriting_it():
    architecture = {"story_engine": {"endgame_direction": "The witness publishes the evidence"}}
    original = deepcopy(architecture)
    winner = {
        "concept": "A witness must choose whom to protect",
        "endgame_direction": "The witness must choose between protection and disclosure",
        "story_architecture": architecture,
    }
    contract = build_concept_contract(
        winner=winner, story_spine={}, target_chapters=30, genre="现实", sub_genre="现实百态"
    )
    result = contract["story_architecture"]
    assert result["story_engine"] == original["story_engine"]
    assert architecture == original
    assert not any(
        "input_hash" in issue for issue in validate_concept_contract(contract, target_chapters=30)
    )
    assert (
        "MISSING_ENDGAME_DIRECTION"
        not in evaluate_story_viability(result, target_chapters=30)["blocking_codes"]
    )


def test_absent_canonical_endgame_is_still_blocked():
    contract = build_concept_contract(
        winner={"story_architecture": {"story_engine": {}}},
        story_spine={},
        target_chapters=30,
        genre="现实",
        sub_genre="现实百态",
    )
    assert (
        "MISSING_ENDGAME_DIRECTION"
        in evaluate_story_viability(contract["story_architecture"], target_chapters=30)[
            "blocking_codes"
        ]
    )


@pytest.mark.asyncio
async def test_final_materialization_reconciliation_rejects_invalid_model_architecture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_architecture = {
        "schema_version": "story-architecture.v1",
        "target_chapters": 60,
        "causal_units": [{"id": "u1", "input": "原始事实"}],
        "endgame_direction": "原始审核终局",
        "story_engine": {
            "core_engine": "原始发动机",
            "reader_promise": "原始承诺",
            "endgame_direction": "原始审核终局",
        },
    }
    winner = {
        "concept": "林策经营移动驿城",
        "mechanism": "每次迁徙前组织一次交易选择",
        "hook_question": "林策能否让居民留下？",  # noqa: RUF001
        "protagonist_identity": "移动驿城城主林策",
        "protagonist_private_desire": "让居民在兽潮前留下",
        "protagonist_flaw": "习惯把人当调度节点",
        "core_abnormality": "能看见交换意愿",
        "opening_crisis": "兽潮七天后抵达",
        "opponent_system": "争夺商路的族群与旧城势力",
        "decision_proof": "不行动城市会被兽潮吞没",
        "emotional_promise": "经营选择留下关系代价",
        "repeatable_story_unit": "一次交易选择与对手反制",
        "unit_families": ["迁徙", "交易", "经营"],
        "unit_frequency": "每2-4章一次",
        "unit_count_estimate": 15,
        "renewal_sources": ["居民选择", "对手反制"],
        "accumulation_tracks": ["商城信用", "路线控制权"],
        "phase_transitions": ["开局迁徙", "中段经营", "终局公器"],
        "opposing_ecology": ["商路旧盟", "逐水部族"],
        "question_ladder": ["巨兽为何苏醒", "谁控制迁徙线", "中立城归谁"],
        "endgame_direction": "把移动驿城交还为公器",
        "story_architecture": original_architecture,
    }
    captured: dict[str, str] = {}

    async def fake_llm(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                **winner,
                "story_architecture": {
                    "story_engine": {"endgame_direction": "模型额外返回的错误终局"},
                    "causal_units": [{"id": "bad-u1"}],
                },
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm)
    from bestseller.services.concept_contract import ConceptContractError

    with pytest.raises(ConceptContractError):
        await conception._reconcile_concept_seed_with_final_premise(
            object(),
            object(),
            winner=winner,
            premise="林策经营移动驿城。",
            synopsis="他要让居民在兽潮前留下。",
            writing_profile={},
            authoritative_name="林策",
            ctx={"genre": "玄幻", "sub_genre": "异世大陆", "chapter_count": 60},
        )

    assert "story_architecture 必须使用" in captured["prompt"]

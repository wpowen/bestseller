import pytest

from bestseller.services import concept_tournament as ct


@pytest.mark.parametrize(
    "genre,seed",
    [
        (
            "东方玄幻",
            "边城矿奴能看见废矿灵脉；宗门封矿时他带工友凿出一条生路。",
        ),
        (
            "悬疑推理",
            "夜班修复师听见失踪者的录音；警方封锁档案前她必须交出第一份证据。",
        ),
    ],
)
def test_premise_prompt_binds_explicit_opening_event_to_card_fields(genre, seed):
    _system, prompt = ct._build_engine_kernel_messages(
        genre=genre,
        sub_genre=genre,
        lane="用户原始#0",
        chapter_count=20,
        authoritative_seed_concept=seed,
        seed_concept=seed,
    )
    assert "原始开局事件保真" in prompt
    assert "开局触发事件、异常现象及其先后因果" in prompt
    for field in ("opening_crisis", "reader_promise", "core_abnormality", "current_goal"):
        assert field in prompt
    assert "不只是source引用或候选提炼角度" in prompt
    assert "不得用泛化技能、身份说明或新事件" in prompt


def test_source_fidelity_contract_requires_event_evidence_and_rejects_omission():
    contract = ct._render_engine_source_fidelity_contract(
        "修复师听见失踪者录音；封锁档案前必须交出第一份证据。"
    )
    assert "遗漏用户明确的" in contract
    assert "标为unknown" in contract
    assert "标为replacement" in contract
    assert "异常现象" in contract
    assert "先后因果" in contract
    assert "用户原文连续引文" in contract
    assert "候选卡字段中的逐字证据" in contract
    assert "unknown/omission" in contract
    assert "source_change_type" in contract
    assert "supplement" in contract


def test_source_fidelity_contract_does_not_require_event_fields_without_authority():
    contract = ct._render_engine_source_fidelity_contract("")
    assert "用户明确的开局触发事件" not in contract
    assert "supplement" in contract

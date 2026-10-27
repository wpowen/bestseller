"""Canonical winner and review-gate state must survive conception resume."""
from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from bestseller.services import conception
from bestseller.services.conception_checkpoints import build_conception_checkpoint

pytestmark = pytest.mark.unit


def _state() -> dict:
    return {
        "_ap_genre": "都市",
        "_ap_sub": "行业",
        "_ap_language": "zh-CN",
        "_ap_platform": "番茄小说",
        "_appeal_block_below": False,
        "_appeal_blocked_by": [],
        "_appeal_blocked_feedback": "",
        "_appeal_cfg": {"logline_gate": {"enabled": False}},
        "_canon_repair_report": None,
        "_contract_body_fingerprint": "old-fingerprint",
        "_hook_one_liner_adapted_result": None,
        "_primary_source_effect_contract": None,
        "_title_tournament_receipt": {"winner": "已选标题"},
        "chapter_count": 5,
        "genre_key": "urban",
        "is_en": False,
        "commercial_brief": {},
        "concept_contract": {"story_architecture": {}},
        "conception_log": [],
        "ctx": {
            "genre": "都市",
            "sub_genre": "行业",
            "language": "zh-CN",
            "high_concept": {"concept": "旧冠军"},
        },
        "explicit_concept_seed": "",
        "premise": "修理工拒绝在虚假的检修单上签字。",
        "story_appeal_report": {},
        "story_spine": {"who": "修理工", "against": "厂长"},
        "synopsis": "修理工发现机器故障并与厂长交涉。",
        "tags": [],
        "title": "检修日",
        "title_profile": {},
        "world_model_payload": {},
        "writing_profile": {"market": {}, "character": {}, "world": {}},
        "tournament_winner": None,
        "copywriting_receipt": None,
        "genre_intent_contract": None,
        "concept_bundle": None,
        "selected_hook_spec": None,
        "degradation_events": [],
        "llm_run_ids": [str(uuid4())],
    }


def _winner(label: str) -> dict:
    return {
        "concept": f"{label}让修理工查出假单",
        "mechanism": "每次检修都会暴露新的责任链",
        "hook_question": "谁在改写检修单",
        "protagonist_identity": "修理工",
        "protagonist_private_desire": "保住工作并查清真相",
        "protagonist_flaw": "不肯及时求助",
        "core_abnormality": "故障记录会留下无法伪造的痕迹",
        "opening_crisis": "厂长逼他签下假检修单",
        "opponent_system": "厂长控制的责任链",
        "decision_proof": "拒签就会丢掉工作",
        "emotional_promise": "在现实压力下守住判断",
        "repeatable_story_unit": "查一份检修单并逼出责任人",
        "unit_families": ["检修", "对账", "交涉"],
        "unit_frequency": "每2-4章一次",
        "unit_count_estimate": 2,
        "renewal_sources": ["厂长施压", "同事反水"],
        "accumulation_tracks": ["责任链", "职业信誉"],
        "phase_transitions": ["从拒签到公开追责"],
        "opposing_ecology": ["厂长", "供应商"],
        "question_ladder": ["谁改了单", "谁获利", "如何追责"],
        "endgame_direction": "公开责任链并保住职业选择",
    }


def _valid_contract(winner: dict) -> dict:
    from bestseller.services.concept_contract import build_concept_contract

    return build_concept_contract(
        winner=winner,
        story_spine={"who": "修理工", "against": "厂长"},
        target_chapters=5,
        genre="都市",
        sub_genre="行业",
    )


@pytest.mark.asyncio
async def test_resume_rechecks_late_contract_without_early_tournament(monkeypatch):
    state = _state()
    old_winner = _winner("旧冠军")
    new_winner = _winner("新冠军")
    state["tournament_winner"] = old_winner
    state["concept_contract"] = _valid_contract(old_winner)
    calls: list[dict] = []
    reconciliation_inputs: list[dict] = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("resume restarted early conception")

    async def reconcile(*args, **kwargs):
        reconciliation_inputs.append(kwargs)
        return copy.deepcopy(new_winner), [uuid4()]

    async def recheck(*args, **kwargs):
        return kwargs["logline"], {"action": "expand", "status": "reviewed"}

    async def audit(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["ctx"]["high_concept"] == new_winner
        return (
            {
                "passed": True,
                "quality_passed": True,
                "audit_identity": conception._final_packaging_audit_identity(
                    title=kwargs["title"], synopsis=kwargs["synopsis"]
                ),
            },
            {},
            {},
            "",
            [],
        )

    monkeypatch.setattr(conception, "_attach_concept_methodology", forbidden)
    monkeypatch.setattr(conception, "_attach_mechanism_dedup", forbidden)
    monkeypatch.setattr(conception, "_reconcile_concept_seed_with_final_premise", reconcile)
    monkeypatch.setattr(conception, "_recheck_materialized_logline", recheck)
    monkeypatch.setattr(conception, "_audit_final_packaging_candidate", audit)
    monkeypatch.setattr(
        conception, "_materialized_story_body_fingerprint", lambda **kwargs: "new-fingerprint"
    )

    checkpoint = build_conception_checkpoint(state, input_hash="same-input")
    result = await conception.run_conception_pipeline(
        None,
        None,
        genre_key="urban",
        chapter_count=5,
        resume_checkpoint=checkpoint,
        checkpoint_input_hash="same-input",
    )

    assert len(reconciliation_inputs) == 1
    assert len(calls) == 1
    assert result.concept_contract["hook_card"]["one_liner"] == new_winner["concept"]
    assert calls[0]["ctx"]["high_concept"] == new_winner
    # The successful path has no need to persist a second copy: the result and
    # final audit both observe the reconciled canonical winner.  The checkpoint
    # callback is intentionally reserved for resumable failures.


@pytest.mark.asyncio
async def test_failure_checkpoint_records_mutated_review_gate_state(monkeypatch):
    from bestseller.services import logline_gate
    from bestseller.services.story_appeal import AppealBarNotMetError

    state = _state()
    state["tournament_winner"] = _winner("旧冠军")
    state["concept_contract"] = _valid_contract(state["tournament_winner"])
    state["_contract_body_fingerprint"] = None
    saved: list[dict] = []

    def fail_config(*args, **kwargs):
        raise RuntimeError("synthetic logline gate failure")

    async def save(item):
        saved.append(copy.deepcopy(item))

    monkeypatch.setattr(logline_gate, "load_logline_gate_config", fail_config)
    with pytest.raises(AppealBarNotMetError):
        await conception._complete_conception_candidate(
            None,
            None,
            state=state,
            checkpoint_input_hash="same-input",
            checkpoint_callback=save,
        )

    assert len(saved) == 2
    failure = saved[-1]["state"]["last_failure"]
    # Entry values intentionally remain the resume input; the latest local gate
    # state is separately observable and must not lock the next review closed.
    assert saved[-1]["state"]["_appeal_block_below"] is False
    assert failure["review_gate_state"] == {
        "blocked": True,
        "blocked_by": ["logline_gate_execution"],
        "feedback": "一句话故事大纲硬门执行失败，未创建书籍、未进入规划。",  # noqa: RUF001
    }

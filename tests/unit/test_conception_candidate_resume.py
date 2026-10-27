"""Resume must bypass ideation but retain common final review and provenance."""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from bestseller.services import conception
from bestseller.services.conception_checkpoints import build_conception_checkpoint

pytestmark = pytest.mark.unit


def review_state():
    return {
        "_ap_genre": "都市",
        "_ap_sub": "行业",
        "_ap_language": "zh-CN",
        "_ap_platform": "",
        "_appeal_block_below": False,
        "_appeal_blocked_by": [],
        "_appeal_blocked_feedback": "",
        "_appeal_cfg": {"logline_gate": {"enabled": False}},
        "_canon_repair_report": None,
        "_contract_body_fingerprint": None,
        "_hook_one_liner_adapted_result": None,
        "_primary_source_effect_contract": None,
        "_title_tournament_receipt": {"winner": "保留标题"},
        "chapter_count": 5,
        "genre_key": "urban",
        "is_en": False,
        "commercial_brief": {},
        "concept_contract": {},
        "conception_log": [],
        "ctx": {"genre": "都市", "sub_genre": "行业", "language": "zh-CN"},
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


@pytest.mark.asyncio
async def test_resume_skips_preparation_and_keeps_final_package_review(monkeypatch):
    state = review_state()
    state["degradation_events"] = [
        {
            "stage": "earlier",
            "component": "test",
            "reason": "retained warning",
            "severity": "warning",
            "fallback": False,
            "model": None,
            "metadata": {},
        }
    ]
    checkpoint = build_conception_checkpoint(state, input_hash="same-input")
    calls = []
    saved = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("resume restarted early conception")

    async def audit(*args, **kwargs):
        calls.append(kwargs)
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

    async def save(item):
        saved.append(copy.deepcopy(item))

    monkeypatch.setattr(conception, "_attach_concept_methodology", forbidden)
    monkeypatch.setattr(conception, "_attach_mechanism_dedup", forbidden)
    monkeypatch.setattr(conception, "_audit_final_packaging_candidate", audit)
    result = await conception.run_conception_pipeline(
        None,
        None,
        genre_key="urban",
        chapter_count=5,
        resume_checkpoint=checkpoint,
        checkpoint_input_hash="same-input",
        checkpoint_callback=save,
    )
    assert len(calls) == 1
    assert result.title == state["title"]
    assert [str(item) for item in result.llm_run_ids] == state["llm_run_ids"]
    assert result.title_tournament == state["_title_tournament_receipt"]
    assert result.degraded and result.degradation_events[0].reason == "retained warning"
    assert saved and checkpoint["state"]["conception_log"] == []


@pytest.mark.asyncio
async def test_missing_state_or_changed_target_cannot_resume(monkeypatch):
    state = review_state()
    state.pop("degradation_events")
    checkpoint = build_conception_checkpoint(state, input_hash="same-input")
    with pytest.raises(ValueError, match="missing fields"):
        await conception.run_conception_pipeline(
            None,
            None,
            genre_key="urban",
            chapter_count=5,
            resume_checkpoint=checkpoint,
            checkpoint_input_hash="same-input",
        )
    checkpoint = build_conception_checkpoint(review_state(), input_hash="same-input")
    with pytest.raises(ValueError, match="target mismatch"):
        await conception.run_conception_pipeline(
            None,
            None,
            genre_key="urban",
            chapter_count=50,
            resume_checkpoint=checkpoint,
            checkpoint_input_hash="same-input",
        )


@pytest.mark.asyncio
async def test_failure_saves_candidate_and_preserves_exception(monkeypatch):
    from bestseller.services.concept_contract import ConceptContractError

    state = review_state()
    saved = []

    async def fail(*args, **kwargs):
        raise ConceptContractError(["test-final-review-failure"])

    async def save(item):
        saved.append(copy.deepcopy(item))

    monkeypatch.setattr(conception, "_audit_final_packaging_candidate", fail)
    with pytest.raises(ConceptContractError, match="test-final-review-failure"):
        await conception._complete_conception_candidate(
            None,
            None,
            state=state,
            checkpoint_input_hash="input",
            checkpoint_callback=save,
        )
    assert len(saved) == 2
    assert "test-final-review-failure" in saved[-1]["state"]["last_failure"]["message"]
    assert saved[-1]["state"]["premise"] == state["premise"]


def structural_failure(state, *, scope="story", quote=None):
    state["last_failure"] = {
        "error_type": "ConceptContractError",
        "violations": ["FINAL_MATERIALIZATION_LOGLINE_REVIEW_UNRESOLVED"],
        "conception_log": [
            {
                "agent": "materialized_logline_blocked",
                "report": {
                    "repair_scope": scope,
                    "axis_why": {
                        "conflict": {
                            "repair_scope": scope,
                            "reason": "行动缺少后果",
                            "evidence": [{"field": "premise", "quote": quote or state["premise"]}],
                        }
                    },
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "scope,quote",
    [("insufficient_context", None), ("story", "不在正文中的证据"), ("expression", None)],
)
def test_unverified_diagnosis_does_not_authorize_structural_edit(scope, quote):
    state = review_state()
    structural_failure(state, scope=scope, quote=quote)
    assert conception._conception_structure_diagnosis(state) is None


@pytest.mark.asyncio
async def test_structural_budget_is_persisted_before_edit_and_not_refilled_on_resume(monkeypatch):
    from bestseller.services.concept_contract import ConceptContractError

    state, saved, edits, reviews = review_state(), [], [], []

    async def review(*args, state, **kwargs):
        reviews.append(state["premise"])
        structural_failure(state)
        raise ConceptContractError(["FINAL_MATERIALIZATION_LOGLINE_REVIEW_UNRESOLVED"])

    async def repair(session, settings, state, diagnosis, **kwargs):
        assert saved[-1]["state"]["structure_repair_attempts"] == 1
        edits.append(diagnosis)
        state["premise"] += "工厂因此停工。"

    async def save(item):
        saved.append(copy.deepcopy(item))

    monkeypatch.setattr(conception, "_review_conception_candidate", review)
    monkeypatch.setattr(conception, "_repair_conception_candidate_structure", repair)
    with pytest.raises(ConceptContractError):
        await conception._complete_conception_candidate(
            None, None, state=state, checkpoint_callback=save, checkpoint_input_hash="input"
        )
    assert len(edits) == 1 and len(reviews) == 2
    with pytest.raises(ConceptContractError):
        await conception.run_conception_pipeline(
            None,
            None,
            genre_key="urban",
            chapter_count=5,
            resume_checkpoint=saved[-1],
            checkpoint_callback=save,
            checkpoint_input_hash="input",
        )
    assert len(edits) == 1 and len(reviews) == 3


@pytest.mark.asyncio
async def test_unknown_review_keeps_candidate_without_edit(monkeypatch):
    from bestseller.services.concept_contract import ConceptContractError

    state = review_state()

    async def review(*args, state, **kwargs):
        structural_failure(state, scope="insufficient_context")
        raise ConceptContractError(["FINAL_MATERIALIZATION_LOGLINE_REVIEW_UNRESOLVED"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("unknown diagnosis cannot authorize edits")

    monkeypatch.setattr(conception, "_review_conception_candidate", review)
    monkeypatch.setattr(conception, "_repair_conception_candidate_structure", forbidden)
    with pytest.raises(ConceptContractError):
        await conception._complete_conception_candidate(None, None, state=state)
    assert "structure_repair_attempts" not in state


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [-1, 2, True, "0"])
async def test_corrupt_repair_budget_cannot_resume(attempts):
    state = review_state()
    state["structure_repair_attempts"] = attempts
    with pytest.raises(ValueError, match="repair budget"):
        await conception._complete_conception_candidate(None, None, state=state)


@pytest.mark.asyncio
async def test_long_resume_cannot_omit_source_contract():
    from bestseller.services.concept_contract import ConceptContractError

    state = review_state()
    state["chapter_count"] = 80
    with pytest.raises(ConceptContractError, match="禁止进入书籍规划"):
        await conception.run_conception_pipeline(
            None,
            None,
            genre_key="urban",
            chapter_count=80,
            resume_checkpoint=build_conception_checkpoint(state, input_hash="input"),
            checkpoint_input_hash="input",
        )


@pytest.mark.asyncio
async def test_repair_failure_without_checkpoint_retains_candidate_on_exception(monkeypatch):
    from bestseller.services.concept_contract import ConceptContractError

    state = review_state()

    async def review(*args, state, **kwargs):
        structural_failure(state)
        raise ConceptContractError(["FINAL_MATERIALIZATION_LOGLINE_REVIEW_UNRESOLVED"])

    async def repair(session, settings, state, diagnosis, **kwargs):
        state["conception_log"].append(
            {"agent": "conception_structure_repair", "candidate": {"premise": "待核的新候选"}}
        )
        raise ConceptContractError(["STRUCTURE_REPAIR_REGRESSION_REVIEW_UNRESOLVED"])

    monkeypatch.setattr(conception, "_review_conception_candidate", review)
    monkeypatch.setattr(conception, "_repair_conception_candidate_structure", repair)
    with pytest.raises(ConceptContractError) as failure:
        await conception._complete_conception_candidate(None, None, state=state)
    assert failure.value.conception_log[-1]["candidate"]["premise"] == "待核的新候选"
    assert state["premise"] == review_state()["premise"]


@pytest.mark.asyncio
async def test_unchanged_paid_proposal_does_not_spend_review_or_stop_next_real_repair(monkeypatch):
    from bestseller.services import conception_packaging_recovery as proposals
    state=review_state();state['repair_quotas']={'final_packaging':2}
    audits=[];repairs=[]
    async def audit(*args,**kwargs):
        audits.append((kwargs['title'],kwargs['synopsis']))
        passed=kwargs['synopsis']=='corrected full synopsis'
        return ({'passed':passed,'hard_conflicts':[] if passed else [{'classification':'hard_conflict','scope':'synopsis','reason':'wrong identity'}],
            'audit_identity':conception._final_packaging_audit_identity(title=kwargs['title'],synopsis=kwargs['synopsis'])},{},None,{},[])
    async def completed(session):
        return [{'title_candidates':[state['title']],'synopsis_candidates':[state['synopsis']],'source_operation':'paid-result'}]
    async def repair(*args,**kwargs):
        repairs.append(kwargs['attempt']);return {'title_candidates':[state['title']],'synopsis_candidates':['corrected full synopsis']},[]
    monkeypatch.setattr(proposals,'collect_retained_packaging_proposals',lambda _: ([],{'source_identity':{'same_canon':True}}))
    monkeypatch.setattr(proposals,'collect_completed_scope_packaging_proposals',completed)
    monkeypatch.setattr(conception,'_audit_final_packaging_candidate',audit)
    monkeypatch.setattr(conception,'_repair_final_packaging_to_explicit_seed',repair)
    monkeypatch.setattr(conception,'_select_final_packaging_title_candidate',lambda candidates,**kw:kw['incumbent'])
    monkeypatch.setattr(conception,'_select_final_packaging_synopsis_candidate',lambda candidates,**kw:candidates[-1])
    checkpoint=build_conception_checkpoint(state,input_hash='same-input')
    result=await conception.run_conception_pipeline(None,None,genre_key='urban',chapter_count=5,
        resume_checkpoint=checkpoint,checkpoint_input_hash='same-input')
    assert result.synopsis=='corrected full synopsis' and repairs==[3]
    assert len(audits)==2 and audits[0]!=(audits[1])
    assert any(x.get('status')=='unchanged_rejected_package' and x['paid_review_skipped'] for x in result.conception_log)

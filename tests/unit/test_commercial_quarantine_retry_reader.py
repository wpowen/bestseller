"""Report retry readers retain verified quarantines as neutral, non-repairable slots."""

from copy import deepcopy
import json

import pytest
from test_commercial_component_cohort import SCOPE, _judge, _save_report
from test_commercial_component_cohort import cohort as cohort
from test_commercial_quarantine_action_proofs import _all_rows, _load, _sources
from test_commercial_quarantine_round2 import _round2_case
from test_commercial_slot_quarantine import _case

from bestseller.infra.db import models as m
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _read(case, prior, *, sources=True):
    rig = case.rig
    with bind_execution_scope(SCOPE):
        return await recovery.load_commercial_retry_context(
            rig.session,
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            draft_id=rig.draft.id,
            content_md=rig.draft.content_md,
            retry_context={"attempt": 2, "prior_report_id": str(prior.id)},
            sources=_sources(rig) if sources else None,
        )


@pytest.mark.parametrize(
    "round2,projected", [(False, False), (False, True), (True, False), (True, True)]
)
async def test_direct_report_retry_consumer_authenticates_abstention_and_never_reopens(
    cohort,
    monkeypatch,
    round2,
    projected,
):
    case = await _round2_case(cohort, monkeypatch) if round2 else await _case(cohort, monkeypatch)
    result = await _judge(cohort, bound=True)
    prior = await _save_report(cohort, result)
    if projected:
        proofs = await _load(case, result)
        view, projection = project_commercial_actions(
            result,
            sources=_sources(cohort),
            gate_config=GateConfig({}),
            chapter_number=2,
            verified_quarantine_slots=proofs,
        )
        assert view is not None and projection["status"] == "projected"
        payload = deepcopy(prior.structured_output)
        payload["evidence_summary"].update(
            {
                "llm_commercial_judge": view,
                "llm_commercial_judge_original": result.model_dump(mode="json"),
                "commercial_action_projection": projection,
            }
        )
        prior.structured_output = payload
        await cohort.session.commit()
    before = await _all_rows(cohort)
    context = await _read(case, prior)
    neutral = context["slots"][case.slot]
    assert neutral["state"] == "quarantined"
    assert neutral["report"]["llm_run_id"] is None and not neutral["report"]["passed"]
    assert neutral["report"]["overall_score"] == 0
    assert neutral["raw_report"] == "" and neutral["spent_attempt"] == (2 if round2 else 1)
    assert "proof" not in neutral and "sources" not in neutral
    assert (
        neutral["quarantine_proof_sha256"]
        == json.loads(neutral["report"]["raw_excerpt"])["proof_sha256"]
    )
    with pytest.raises(ValueError, match="commercial_recovery_raw_report_missing"):
        recovery.prepare_commercial_slot_retry(
            context,
            sample_slot=case.slot,
            sources=_sources(cohort),
            evaluation_contract=neutral["evaluation_contract"],
        )
    with bind_execution_scope(SCOPE):
        value, call = await recovery.prepare_commercial_component_slot(
            cohort.session,
            project_id=cohort.project.id,
            chapter_id=cohort.chapter.id,
            draft_id=cohort.draft.id,
            workflow_run_id=case.owner_id,
            content_md=cohort.draft.content_md,
            sources=_sources(cohort),
            evaluation_contract=case.request.metadata["commercial_evaluation_contract"],
            sample_slot=case.slot,
            samples=3,
            retry_context=context,
            effective_critic=(await _load(case, result))[case.slot]["proof"]["effective_critic"],
            current_input_hash=case.input_hash,
            settings=cohort.settings,
        )
    assert value.model_dump(mode="json") == neutral["report"] and call == {}
    assert await _all_rows(cohort) == before
    assert await _read(case, prior) == context
    assert case.forbidden == []


@pytest.mark.parametrize(
    "fault",
    [
        "sources_missing",
        "pointer_missing",
        "root_drift",
        "unknown_budget",
        "original_missing",
        "report_changed",
    ],
)
async def test_direct_report_quarantine_reader_has_no_historical_or_unverified_fallback(
    cohort,
    monkeypatch,
    fault,
):
    case = await _case(cohort, monkeypatch)
    result = await _judge(cohort, bound=True)
    prior = await _save_report(cohort, result)
    if fault == "pointer_missing":
        metadata = deepcopy(cohort.chapter.metadata_json)
        metadata["commercial_component_cohorts"].pop(case.key)
        cohort.chapter.metadata_json = metadata
    elif fault == "root_drift":
        root = await cohort.session.get(
            m.ExecutionOperationModel, case.root, populate_existing=True
        )
        root.status = "completed"
    elif fault == "unknown_budget":
        budget = await cohort.session.get(m.ExecutionBudgetModel, SCOPE, populate_existing=True)
        budget.status = "unknown"
    elif fault in {"original_missing", "report_changed"}:
        payload = deepcopy(prior.structured_output)
        if fault == "original_missing":
            payload["evidence_summary"]["commercial_action_projection"] = {"status": "projected"}
        else:
            payload["evidence_summary"]["llm_commercial_judge"]["overall_score"] = 0.99
        prior.structured_output = payload
    await cohort.session.commit()
    before = await _all_rows(cohort)
    with pytest.raises((ValueError, ExecutionBudgetError)):
        await _read(case, prior, sources=fault != "sources_missing")
    assert await _all_rows(cohort) == before and case.forbidden == []

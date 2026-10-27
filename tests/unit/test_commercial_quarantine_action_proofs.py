"""Real SQLite reader custody; synthetic paid returns, no provider or PG."""

from copy import deepcopy
from datetime import UTC, datetime
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_commercial_component_cohort import SCOPE, _judge
from test_commercial_component_cohort import cohort as cohort
from test_commercial_slot_quarantine import _case, _financial, _journal

from bestseller.infra.db import models as m
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_slot_quarantine import (
    SOURCE_KEYS,
    _digest,
    load_verified_quarantined_slot_proofs,
    requires_verified_quarantine_slots,
)
from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    assemble_commercial_sources,
)
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _sources(rig):
    return assemble_commercial_sources(
        chapter_number=2,
        content_md=rig.draft.content_md,
        source=judge._commercial_source_projection(
            {},
            chapter_number=2,
            content_md=rig.draft.content_md,
        ),
        previous_chapters=[],
    )


async def _load(case, result, **changes):
    rig = case.rig
    args = {
        "project_id": rig.project.id,
        "chapter_id": rig.chapter.id,
        "content_md": rig.draft.content_md,
        "result": result,
        "sources": _sources(rig),
    }
    args.update(changes)
    with bind_execution_scope(SCOPE):
        return await load_verified_quarantined_slot_proofs(rig.session, **args)


async def _all_rows(rig):
    result = {}
    for model in (
        m.ChapterModel,
        m.ChapterDraftVersionModel,
        m.WorkflowRunModel,
        m.ExecutionBudgetModel,
        m.ExecutionAttemptModel,
        m.ExecutionOperationModel,
        m.LlmRunModel,
        m.ReviewReportModel,
    ):
        rows = (await rig.session.execute(select(model.__table__))).mappings().all()
        result[model.__tablename__] = sorted(
            json.dumps(dict(r), sort_keys=True, default=str) for r in rows
        )
    return result


@pytest.mark.parametrize("malformed", [False, True])
async def test_live_action_receipt_preserves_exact_aggregate_and_all_rows(
    cohort, monkeypatch, malformed
):
    case = await _case(cohort, monkeypatch, malformed=malformed)
    result = await _judge(cohort, bound=True)
    original = result.model_dump(mode="json")
    before = await _all_rows(cohort)
    receipts = await _load(case, result)
    assert set(receipts) == {case.slot}
    entry = (await _journal(case))["slots"][case.slot]
    receipt = receipts[case.slot]
    proof = entry["quarantine_history"][-1]["proof"]
    assert receipt == {
        "schema": "verified-quarantined-slot-action.v1",
        "proof": proof,
        "source_binding": {k: _sources(cohort).receipt[k] for k in SOURCE_KEYS},
        "evaluation_contract": (await _journal(case))["binding"]["evaluation_contract"],
        "original_report_sha256": _digest(entry["report"]),
    }
    assert _digest(proof) == json.loads(entry["report"]["raw_excerpt"])["proof_sha256"]
    assert receipt["proof"]["root"]["status"] == "stopped"
    assert (
        receipt["proof"]["reconciliations"][0]["result_json"]["receipt"]["credit_released"] is False
    )
    assert await _load(case, result) == receipts
    view, action = project_commercial_actions(
        result,
        sources=_sources(cohort),
        gate_config=GateConfig({}),
        chapter_number=2,
        verified_quarantine_slots=receipts,
    )
    if malformed:
        assert view is None and action["status"] == "unsupported"
    else:
        assert action["status"] == "projected", action
        assert view["raw_excerpt"] == result.raw_excerpt
        assert view["overall_score"] == result.overall_score
        assert action["unknown_sample_slots"] == [case.slot]
    assert await _all_rows(cohort) == before
    assert result.model_dump(mode="json") == original
    assert result.infrastructure_failure is malformed  # Receipt never supplies a second grade.
    assert case.forbidden == [] and len(cohort.calls) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "body",
        "draft",
        "project",
        "scope",
        "owner",
        "pointer",
        "missing_pointer",
        "aggregate",
        "result_score",
        "result_raw",
        "slot_report",
        "proof_digest",
        "attempt",
        "new_unsettled",
        "reconciliation",
        "root_return",
        "budget_unknown",
    ],
)
async def test_action_reader_rejects_stale_or_unbound_proof_without_writes(
    cohort, monkeypatch, fault
):
    case = await _case(cohort, monkeypatch)
    result = await _judge(cohort, bound=True)
    changes = {}
    owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
    metadata = deepcopy(owner.metadata_json)
    journal = metadata["commercial_component_cohorts"][case.key]
    if fault == "source":
        sources = _sources(cohort)
        changes["sources"] = CommercialSources(
            sources.text, sources.catalog, {**sources.receipt, "semantic_source_sha256": "0" * 64}
        )
    elif fault == "body":
        changes["content_md"] = cohort.draft.content_md + "changed"
    elif fault == "draft":
        cohort.draft.content_md += "changed"
    elif fault == "project":
        changes["project_id"] = uuid4()
    elif fault == "scope":
        journal["binding"]["scope_id"] = "other-scope"
        owner.metadata_json = metadata
    elif fault == "owner":
        owner.scope_id = uuid4()
    elif fault in {"pointer", "missing_pointer"}:
        chapter_meta = deepcopy(cohort.chapter.metadata_json)
        if fault == "pointer":
            chapter_meta["commercial_component_cohorts"][case.key]["binding_sha256"] = "0" * 64
        else:
            chapter_meta["commercial_component_cohorts"].pop(case.key)
        cohort.chapter.metadata_json = chapter_meta
    elif fault == "result_score":
        changes["result"] = result.model_copy(update={"overall_score": 0.99})
    elif fault == "result_raw":
        changes["result"] = result.model_copy(update={"raw_excerpt": result.raw_excerpt + " "})
    elif fault in {"aggregate", "slot_report", "proof_digest"}:
        if fault == "aggregate":
            journal["aggregate"]["slots_sha256"] = "0" * 64
        elif fault == "slot_report":
            journal["slots"][case.slot]["report"]["passed"] = True
        else:
            entry = journal["slots"][case.slot]
            raw = json.loads(entry["report"]["raw_excerpt"])
            raw["proof_sha256"] = "0" * 64
            entry["report"]["raw_excerpt"] = json.dumps(raw)
        owner.metadata_json = metadata
    elif fault == "attempt":
        attempt = await cohort.session.get(m.ExecutionAttemptModel, case.attempt)
        attempt.input_hash = "0" * 64
    elif fault == "new_unsettled":
        cohort.session.add(
            m.ExecutionAttemptModel(
                id=uuid4().hex,
                operation_key=case.root,
                scope_id=SCOPE,
                input_hash=case.input_hash,
                model_name=cohort.settings.llm.critic.model,
                status="reserved",
                reserved_tokens=1000,
                created_at=datetime.now(UTC),
            )
        )
    elif fault == "reconciliation":
        row = await cohort.session.get(m.ExecutionOperationModel, case.reconciliation_key)
        row.result_json = {**row.result_json, "local_dispatcher_stopped": False}
    elif fault == "root_return":
        row = await cohort.session.get(m.ExecutionOperationModel, case.root)
        row.result_json = {"content": "conflicting return"}
    elif fault == "budget_unknown":
        budget = await cohort.session.get(m.ExecutionBudgetModel, SCOPE)
        budget.status = "unknown"
    await cohort.session.commit()
    before = await _all_rows(cohort)
    if "result" in changes:
        result = changes.pop("result")
    with pytest.raises((ValueError, ExecutionBudgetError)):
        await _load(case, result, **changes)
    assert await _all_rows(cohort) == before
    assert case.forbidden == []


async def test_normal_completed_cohort_needs_no_quarantine_receipt(cohort):
    cohort.outcomes[1] = cohort.outcomes[2] = [True]
    result = await _judge(cohort, bound=True)
    before = await _financial(cohort)
    with bind_execution_scope(SCOPE):
        assert (
            await load_verified_quarantined_slot_proofs(
                cohort.session,
                project_id=cohort.project.id,
                chapter_id=cohort.chapter.id,
                content_md=cohort.draft.content_md,
                result=result,
                sources=_sources(cohort),
            )
            == {}
        )
    assert await _financial(cohort) == before


async def test_detector_matches_only_decoded_quarantine_envelope():
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

    def result(envelope):
        return LLMQualityJudgeResult.model_validate(
            {
                "scope": "chapter",
                "passed": False,
                "overall_score": 0,
                "raw_excerpt": json.dumps(envelope),
            }
        )

    quoted = {
        "schema_version": "commercial-slot-evidence.v1",
        "raw_report": json.dumps(
            {
                "quote": '{"schema":"commercial-slot-quarantine.v1"}',
            }
        ),
    }
    aggregate = {
        "schema_version": "chapter-quality-aggregation.v1",
        "samples": [
            {
                "report": {"raw_excerpt": json.dumps(quoted)},
            }
        ],
    }
    assert not requires_verified_quarantine_slots(result(aggregate))
    aggregate["samples"][0]["report"]["raw_excerpt"] = json.dumps(
        {
            "schema": "commercial-slot-quarantine.v1",
        }
    )
    assert requires_verified_quarantine_slots(result(aggregate))
    assert requires_verified_quarantine_slots(result({"schema": "commercial-slot-quarantine.v1"}))
    for invalid in ([], None, {"schema_version": "other", "samples": aggregate["samples"]}):
        assert not requires_verified_quarantine_slots(result(invalid))

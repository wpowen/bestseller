"""Future-only waiting retains the same logical slot; no PG/provider/credit changes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from test_adopted_knowledge_lineage import db  # noqa: F401
from test_chapter_critic_semantic_dependencies import semantic_db  # noqa: F401
from test_chapter_review_first_reuse_proof import proof_case, snapshot  # noqa: F401
from test_chapter_review_reconciliation import migration  # noqa: F401
from test_review_first_pipeline_resume import resume_case  # noqa: F401

from bestseller.domain.review import ChapterReviewResult
from bestseller.infra.db.models import QualityScoreModel, ReviewReportModel, WorkflowRunModel
from bestseller.services import pipelines
from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
from bestseller.services.chapter_review_prerequisite import (
    WAITING,
    prerequisite_wait_receipt,
    wait_binding,
)


@pytest.fixture
async def waiting_case(proof_case, monkeypatch):  # noqa: F811
    rig = proof_case
    rig.chapter.metadata_json = {**rig.chapter.metadata_json, "chapter_review_attempts_active": 7}
    rig.original_top_metadata = {
        k: deepcopy(v)
        for k, v in rig.chapter.metadata_json.items()
        if k
        not in {
            "chapter_critic_cycles",
            "chapter_review_active_cycle",
            "chapter_review_recovery_cycles",
        }
    }
    rig.old_cycles = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"])
    fingerprint = pipelines.chapter_review_body_fingerprint(rig.source.content_md)
    cid = pipelines._chapter_critic_cycle_id(
        rig.project.id, rig.chapter.id, fingerprint["digest"], "future-only"
    )
    prepared = {
        "cycle_id": cid,
        "state": "prepared",
        "source_draft_id": str(rig.source.id),
        "source_body_hash": fingerprint["digest"],
        "policy_source_hash": "current-policy",
    }
    pipelines._store_chapter_review_recovery_cycle(rig.chapter, prepared)
    owner = WorkflowRunModel(
        project_id=rig.project.id,
        scope_type="chapter",
        scope_id=rig.chapter.id,
        workflow_type="chapter_pipeline",
        status="running",
        metadata_json={},
    )
    payload = deepcopy(rig.report.structured_output)
    payload["scores"] = deepcopy(rig.original_cycle["attempts"][-1]["review_result"]["scores"])
    payload["findings"] = []
    payload.update(
        draft_id=str(rig.source.id),
        reviewed_body_fingerprint=fingerprint,
        primary_submission_original=None,
        primary_submission_receipt=None,
        rewrite_instructions=None,
        untrusted_critic_response="",
        critic_evidence={"status": "unavailable", "passed": False},
    )
    payload["evidence_summary"] = {
        "primary_review_not_dispatched": {
            "reason": "required_judge_evidence_unavailable",
            "provider_calls": 0,
            "body_fingerprint": fingerprint,
        },
        "llm_commercial_judge": {"kept": "actual prerequisite diagnostic"},
    }
    report = ReviewReportModel(
        project_id=rig.project.id,
        target_type="chapter",
        target_id=rig.chapter.id,
        reviewer_type="rule-based-critic",
        verdict="unknown",
        severity_max="high",
        llm_run_id=None,
        structured_output=payload,
    )
    rig.session.add_all([owner, report])
    await rig.session.flush()
    columns = {
        a.key: getattr(rig.quality, a.key)
        for a in inspect(QualityScoreModel).column_attrs
        if a.key not in {"id", "created_at", "review_report_id"}
    }
    quality = QualityScoreModel(**columns, review_report_id=report.id)
    rig.session.add(quality)
    await rig.session.commit()
    result = ChapterReviewResult(
        verdict="unknown",
        scores=payload["scores"],
        findings=[],
        severity_max="high",
        evidence_summary=payload["evidence_summary"],
        rewrite_instructions=None,
    )
    reviewer = AsyncMock(return_value=(result, report, quality, None))
    monkeypatch.setattr(pipelines, "review_chapter_draft", reviewer)
    rig.cid, rig.owner, rig.wait_report, rig.wait_quality = cid, owner, report, quality
    rig.result, rig.reviewer, rig.settings = result, reviewer, SimpleNamespace()
    return rig


async def produce(rig):
    return await pipelines._review_chapter_with_evidence_retry(
        rig.session,
        rig.settings,
        rig.project.slug,
        rig.chapter.chapter_number,
        workflow=rig.owner,
        draft=rig.source,
        cycle_id=rig.cid,
    )


@pytest.mark.parametrize(
    "reason", ["required_judge_evidence_unavailable", "primary_source_capacity_unavailable"]
)
async def test_new_prerequisite_waits_once_and_replays_all_rows_readonly(waiting_case, reason):
    rig = waiting_case
    payload = deepcopy(rig.wait_report.structured_output)
    payload["evidence_summary"]["primary_review_not_dispatched"]["reason"] = reason
    rig.wait_report.structured_output = payload
    await rig.session.commit()
    await produce(rig)
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cid])
    assert cycle["state"] == WAITING and len(cycle["attempts"]) == 1
    entry = cycle["attempts"][0]
    assert entry["state"] == WAITING and entry["attempt"] == 1
    assert entry["operation_namespace"] == f"chapter-critic:{rig.cid}:1"
    assert "outcome" not in entry and len(entry["infrastructure_history"]) == 1
    receipt = entry["infrastructure_history"][0]["waiting_receipt"]
    assert (
        receipt["financial_proof"]
        is receipt["credit_released"]
        is receipt["dispatch_authorized"]
        is False
    )
    assert {
        k: v
        for k, v in rig.chapter.metadata_json.items()
        if k
        not in {
            "chapter_critic_cycles",
            "chapter_review_active_cycle",
            "chapter_review_recovery_cycles",
        }
    } == rig.original_top_metadata
    for cid, old in rig.old_cycles.items():
        assert rig.chapter.metadata_json["chapter_critic_cycles"][cid] == old
    before = await snapshot(rig.session)
    proof = await validate_retained_review_before_processing(rig.session, rig.settings, rig.chapter)
    assert proof["reuse_kind"] == WAITING and proof["dispatch_authorized"] is False
    replay = await produce(rig)
    assert replay[1].id == rig.wait_report.id and replay[2].id == rig.wait_quality.id
    rig.reviewer.assert_awaited_once()
    assert await snapshot(rig.session) == before
    assert rig.source.is_current and rig.source.promotion_state != "promoted"


@pytest.mark.parametrize(
    "fault",
    [
        "paid_run",
        "provider_bool",
        "provider_positive",
        "reason",
        "body",
        "draft",
        "project",
        "submission",
        "raw_primary",
        "critic",
        "rewrite",
    ],
)
async def test_marker_cannot_classify_paid_or_unbound_primary_as_waiting(waiting_case, fault):
    rig = waiting_case
    report = SimpleNamespace(
        id=rig.wait_report.id,
        project_id=rig.project.id,
        target_id=rig.chapter.id,
        target_type="chapter",
        llm_run_id=None,
        verdict="unknown",
        structured_output=deepcopy(rig.wait_report.structured_output),
    )
    p = report.structured_output
    marker = p["evidence_summary"]["primary_review_not_dispatched"]
    if fault == "paid_run":
        report.llm_run_id = uuid4()
    elif fault == "provider_bool":
        marker["provider_calls"] = False
    elif fault == "provider_positive":
        marker["provider_calls"] = 1
    elif fault == "reason":
        marker["reason"] = "arbitrary_transport_exception"
    elif fault == "body":
        marker["body_fingerprint"] = {"digest": "forged"}
    elif fault == "draft":
        p["draft_id"] = str(uuid4())
    elif fault == "project":
        report.project_id = uuid4()
    elif fault == "submission":
        p["primary_submission_original"] = "paid raw"
    elif fault == "raw_primary":
        p["untrusted_critic_response"] = "paid invalid anchor"
    elif fault == "critic":
        p["critic_evidence"]["status"] = "evidence_invalid"
    else:
        p["rewrite_instructions"] = "change prose"
    binding = wait_binding(
        {
            "cycle_id": rig.cid,
            "review_workflow_run_id": str(rig.owner.id),
            "source_draft_id": str(rig.source.id),
            "source_body_hash": pipelines.chapter_review_body_fingerprint(rig.source.content_md)[
                "digest"
            ],
            "policy_source_hash": "current-policy",
            "semantic_dependencies_sha256": "same-data",
        },
        {"attempt": 1, "operation_namespace": f"chapter-critic:{rig.cid}:1"},
        project_id=rig.project.id,
        chapter_id=rig.chapter.id,
        body_fingerprint=pipelines.chapter_review_body_fingerprint(rig.source.content_md),
    )
    assert prerequisite_wait_receipt(report, binding=binding) is None


@pytest.mark.parametrize(
    "fault", ["namespace", "history_empty", "receipt", "report_changed", "policy", "semantic"]
)
async def test_saved_waiting_proof_fails_closed_before_reentry(waiting_case, monkeypatch, fault):
    rig = waiting_case
    await produce(rig)
    metadata = deepcopy(rig.chapter.metadata_json)
    cycle = metadata["chapter_critic_cycles"][rig.cid]
    entry = cycle["attempts"][0]
    if fault == "namespace":
        entry["operation_namespace"] = "new-namespace"
    elif fault == "history_empty":
        entry["infrastructure_history"] = []
    elif fault == "receipt":
        entry["infrastructure_history"][0]["waiting_receipt"]["credit_released"] = True
    elif fault == "policy":
        cycle["policy_source_hash"] = "new-policy"
    elif fault == "semantic":
        cycle["semantic_dependencies_sha256"] = "new-source"
    else:
        payload = deepcopy(rig.wait_report.structured_output)
        payload["evidence_summary"]["llm_commercial_judge"] = {"forged": True}
        rig.wait_report.structured_output = payload
    rig.chapter.metadata_json = metadata
    await rig.session.commit()
    with pytest.raises(ValueError):
        await produce(rig)
    rig.reviewer.assert_awaited_once()


async def test_waiting_terminal_is_machine_blocked_unknown_without_human_or_adoption(waiting_case):
    rig = waiting_case
    await produce(rig)
    result = await pipelines._finish_unknown_chapter_review(
        rig.session,
        settings=rig.settings,
        workflow=rig.owner,
        chapter=rig.chapter,
        draft=rig.source,
        report=rig.wait_report,
        quality=rig.wait_quality,
    )
    assert result.final_verdict == "unknown" and result.requires_human_review is False
    assert rig.owner.status == "machine_blocked" and rig.owner.current_step == WAITING
    assert rig.chapter.production_state == "review_unknown"
    assert rig.owner.metadata_json["actual_adoption_receipt"]["adopted"] is False
    assert rig.owner.metadata_json["review_prerequisite_wait"]["dispatch_authorized"] is False


async def test_recovery_wrapper_cannot_complete_a_waiting_cycle(waiting_case, monkeypatch):
    rig = waiting_case
    await produce(rig)
    recovery = deepcopy(rig.chapter.metadata_json["chapter_review_active_cycle"])
    result = SimpleNamespace(workflow_run_id=rig.owner.id)
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", AsyncMock(return_value=result))
    await pipelines._run_prepared_chapter_review_cycle(
        rig.session,
        rig.settings,
        rig.project.slug,
        rig.chapter.chapter_number,
        chapter=rig.chapter,
        cycle=recovery,
        requested_by="authorized-test-operator",
        progress=None,
    )
    assert rig.chapter.metadata_json["chapter_review_active_cycle"]["state"] == WAITING
    assert (
        rig.chapter.metadata_json["chapter_critic_cycles"][rig.cid]["attempts"][0]["attempt"] == 1
    )


@pytest.mark.parametrize("chapter_first,resume", [(True, False), (True, True), (False, False)])
async def test_real_pipeline_entry_waits_before_backfill_style_or_review(
    resume_case, monkeypatch, chapter_first, resume  # noqa: F811
):
    rig = resume_case
    cid = rig.cycle["cycle_id"]
    rig.chapter.metadata_json["chapter_critic_cycles"][cid]["state"] = WAITING
    proof = {
        "reuse_kind": WAITING,
        "review_workflow_run_id": str(rig.review_owner.id),
        "draft_id": str(rig.draft.id),
        "report_id": str(rig.report.id),
        "quality_score_id": str(rig.quality.id),
        "requires_human_review": False,
    }
    from bestseller.services import outline_source_entry
    import bestseller.services.chapter_review_preflight as preflight

    async def no_plan_source(session, *, project, chapter):
        # This FakeSession exercises waiting-review orchestration, not SQL
        # freshness. Declare its no-plan source boundary explicitly.
        assert session is rig.session and project is rig.project and chapter is rig.chapter
        assert "outline_source_admission" not in (chapter.metadata_json or {})
        return None

    # This pre-existing FakeSession test isolates orchestration, not the new
    # native SQL admission proof (covered by test_native_chapter_review_resume).
    from bestseller.services import chapter_review_reconciliation as native
    async def native_boundary(session, settings, *, project_id, chapter_id, requested_by):
        assert session is rig.session and settings is rig.settings
        assert project_id == rig.project.id and chapter_id == rig.chapter.id
        return {"status": "unchanged", "provider_calls": 0}
    native_admission = AsyncMock(side_effect=native_boundary)
    monkeypatch.setattr(native, "prepare_native_review_resume", native_admission)
    no_plan = AsyncMock(side_effect=no_plan_source)
    monkeypatch.setattr(outline_source_entry, "current_outline_artifact", no_plan)

    monkeypatch.setattr(
        preflight, "validate_retained_review_before_processing", AsyncMock(return_value=proof)
    )

    async def forbidden(*a, **kw):
        raise AssertionError("no backfill or primary review while waiting")

    monkeypatch.setattr(pipelines, "_ensure_project_invariants", forbidden)
    monkeypatch.setattr(pipelines, "_review_chapter_with_evidence_retry", forbidden)
    before = deepcopy(rig.draft.content_md)
    result = await pipelines.run_chapter_pipeline.__wrapped__(
        rig.session,
        rig.settings,
        rig.project.slug,
        1,
        chapter_first=chapter_first,
        resume_existing_draft_only=resume,
        supersede_pending_rewrites=False,
    )
    assert result.workflow_run_id == rig.review_owner.id and result.final_verdict == "unknown"
    assert result.requires_human_review is False and rig.draft.content_md == before
    assert all(spy.call_count == 0 for spy in rig.forbidden.values())
    assert rig.observer.call_count == 0
    native_admission.assert_awaited_once()
    no_plan.assert_awaited_once_with(rig.session, project=rig.project, chapter=rig.chapter)


async def test_nested_waiting_receipt_does_not_change_real_semantic_source_hash(semantic_db):  # noqa: F811
    from bestseller.settings import load_settings

    session, _project, chapter, *_ = semantic_db
    settings = load_settings(env={})
    before = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    chapter.metadata_json = {
        **(chapter.metadata_json or {}),
        "chapter_critic_cycles": {
            "cycle": {
                "state": WAITING,
                "attempts": [
                    {
                        "attempt": 1,
                        "state": WAITING,
                        "infrastructure_history": [{"retained": "full report"}],
                    }
                ],
            }
        },
        "chapter_review_active_cycle": {"cycle_id": "cycle", "state": WAITING},
    }
    await session.flush()
    after = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    assert after == before

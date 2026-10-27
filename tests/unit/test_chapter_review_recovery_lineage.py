"""Real SQLite native successor chains; no providers or PostgreSQL."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.schema import CreateTable
from test_chapter_failed_review_recovery import db as db
from test_chapter_failed_review_recovery import failed as failed
from test_chapter_failed_review_recovery import retained as base_retained

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import pipelines
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_review_prerequisite import (
    WAITING,
    prerequisite_wait_receipt,
    wait_binding,
)
from bestseller.services.chapter_review_recovery_lineage import (
    _digest,
    validate_failed_review_recovery_lineage,
)

__all__ = ["base_retained"]

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def retained(base_retained, request):
    if hasattr(request, "param"):
        session, project, chapter, draft, *_ = base_retained
        project.title, project.genre, draft.content_md = request.param
        old = next(iter(chapter.metadata_json["chapter_critic_cycles"].values()))
        old = deepcopy(old)
        body = pipelines.chapter_review_body_fingerprint(draft.content_md)["digest"]
        key = pipelines._chapter_critic_cycle_id(project.id, chapter.id, body)
        old.update(cycle_id=key, source_body_hash=body)
        old["attempts"][0]["operation_namespace"] = f"chapter-critic:{key}:1"
        chapter.metadata_json = {**chapter.metadata_json, "chapter_critic_cycles": {key: old}}
        await session.commit()
    return base_retained


async def invoke(r, owner=None):
    with bind_execution_scope(r["scope"]):
        return await pipelines.resume_chapter_review_after_policy_change(
            r["session"],
            r["settings"],
            r["project"].slug,
            r["chapter"].chapter_number,
            previous_workflow_run_id=(owner or r["latest"]).id,
            expected_draft_id=r["draft"].id,
            expected_body_hash=pipelines.chapter_review_body_fingerprint(r["draft"].content_md)[
                "digest"
            ],
            policy_revision="explicit-generic-source-change",
            policy_source_hash=r["policy"][0],
            reason="Retain exact body, prior unknowns and charged history.",
        )


@pytest.fixture
async def lineage(failed, monkeypatch, request):
    r = failed
    ddl = str(CreateTable(RewriteTaskModel.__table__).compile(dialect=r["session"].bind.dialect))
    await r["session"].execute(text(ddl.replace("::jsonb", "")))
    await r["session"].commit()
    option = getattr(request, "param", "absent")
    metadata = deepcopy(r["previous"].metadata_json)
    if option in {"empty", "null", "list"}:
        metadata["terminal_state_history"] = {
            "empty": [],
            "null": None,
            "list": [{"status": "preserved", "step": "older"}],
        }[option]
    if option.startswith("quarantine"):
        count = 2 if "multi" in option else 1
        key = "c" * 64
        slots = {
            f"chapter-quality.independent-v2:{i}": {
                "state": "prepared",
                "used_attempts": 1,
                "request": {"retained_bytes": f"slot-{i}"},
            }
            for i in range(count)
        }
        journal = {
            "schema": "commercial-component-cohort.v1",
            "binding": {"samples": count},
            "slots": slots,
        }
        if option.endswith("null"):
            journal["aggregate"] = None
        metadata["commercial_component_cohorts"] = {key: journal}
    r["previous"].metadata_json = metadata
    await r["session"].commit()
    r["root"] = r["previous"]
    r["policy"] = ["3" * 64]
    r["dispatches"] = []
    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: r["policy"][0])

    async def returned_waiting(session, settings, *args, critic_cycle_id, **kwargs):
        chapter, draft = r["chapter"], r["draft"]
        owner = WorkflowRunModel(
            project_id=r["project"].id,
            scope_type="chapter",
            scope_id=chapter.id,
            workflow_type="chapter_pipeline",
            status="machine_blocked",
            current_step=WAITING,
            metadata_json={
                "execution_scope_id": r["scope"],
                "critic_cycle_id": critic_cycle_id,
                "review_recovery_source": kwargs["review_source_binding"],
            },
        )
        session.add(owner)
        await session.flush()
        fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
        result = ChapterReviewResult(
            verdict="unknown",
            severity_max="critical",
            scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.0)),
        )
        report = ReviewReportModel(
            project_id=r["project"].id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="rule-based-critic",
            verdict="unknown",
            structured_output={
                **result.model_dump(mode="json"),
                "draft_id": str(draft.id),
                "reviewed_body_fingerprint": fingerprint,
                "critic_evidence": {"status": "unavailable", "passed": False},
                "primary_submission_original": None,
                "primary_submission_receipt": None,
                "evidence_summary": {
                    "primary_review_not_dispatched": {
                        "provider_calls": 0,
                        "reason": "required_judge_evidence_unavailable",
                        "body_fingerprint": fingerprint,
                    }
                },
            },
        )
        session.add(report)
        await session.flush()
        quality = QualityScoreModel(
            project_id=r["project"].id,
            target_type="chapter",
            target_id=chapter.id,
            chapter_draft_version_id=draft.id,
            review_report_id=report.id,
            score_overall=0,
            is_current=True,
        )
        session.add(quality)
        await session.flush()
        entry = {
            "attempt": 1,
            "state": WAITING,
            "operation_namespace": f"chapter-critic:{critic_cycle_id}:1",
        }
        critic = {
            "schema_version": "chapter-critic-cycle.v1",
            "cycle_id": critic_cycle_id,
            "state": WAITING,
            "source_draft_id": str(draft.id),
            "source_body_hash": fingerprint["digest"],
            "policy_source_hash": r["policy"][0],
            "semantic_dependencies_sha256": "2" * 64,
            "review_workflow_run_id": str(owner.id),
            "attempts": [entry],
        }
        binding = wait_binding(
            critic,
            entry,
            project_id=r["project"].id,
            chapter_id=chapter.id,
            body_fingerprint=fingerprint,
        )
        record = {
            "attempt": 1,
            "outcome": "unknown",
            "draft_id": str(draft.id),
            "body_fingerprint": fingerprint,
            "review_report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "rewrite_task_id": None,
            "review_result": result.model_dump(mode="json"),
            "waiting_receipt": prerequisite_wait_receipt(report, binding=binding),
        }
        assert record["waiting_receipt"] is not None
        entry["infrastructure_history"] = [record]
        chapter.metadata_json = {
            **chapter.metadata_json,
            "chapter_critic_cycles": {
                **chapter.metadata_json["chapter_critic_cycles"],
                critic_cycle_id: critic,
            },
            "chapter_review_evidence_status": "unknown",
            "chapter_review_evidence_report_id": str(report.id),
        }
        waiting = {
            "schema": "chapter-review-first-reuse.v1",
            "project_id": str(r["project"].id),
            "chapter_id": str(chapter.id),
            "draft_id": str(draft.id),
            "body_sha256": fingerprint["digest"],
            "cycle_id": critic_cycle_id,
            "policy_source_hash": r["policy"][0],
            "semantic_dependencies_sha256": "2" * 64,
            "report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "task_id": None,
            "verdict": "unknown",
            "reuse_kind": WAITING,
            "review_workflow_run_id": str(owner.id),
            "requires_human_review": False,
            "dispatch_authorized": False,
            "credit_released": False,
        }
        owner.metadata_json = {
            **owner.metadata_json,
            "review_prerequisite_wait": waiting,
            "final_verdict": "unknown",
            "requires_human_review": False,
            "chapter_critic_evidence_unknown": True,
            "review_report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "actual_adoption_receipt": {
                "adopted": False,
                "draft_id": str(draft.id),
                "body_fingerprint": fingerprint,
                "promotion_state": draft.promotion_state,
                "numeric_adoption_receipt": None,
            },
        }
        await session.commit()
        r["latest"], r["latest_report"], r["latest_quality"] = owner, report, quality
        r["dispatches"].append(owner.id)
        return SimpleNamespace(workflow_run_id=owner.id)

    monkeypatch.setattr(pipelines, "run_chapter_pipeline", returned_waiting)
    await invoke(r, r["root"])
    if option.startswith("quarantine"):
        from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
        from bestseller.services.commercial_source_evidence import commercial_judge_unavailable

        metadata = deepcopy(r["root"].metadata_json)
        journal = metadata["commercial_component_cohorts"]["c" * 64]
        for slot, prepared in list(journal["slots"].items()):
            proof = {
                "schema": "commercial-slot-quarantine.v1",
                "cohort_key": "c" * 64,
                "slot": slot,
            }
            report = commercial_judge_unavailable(
                diagnostic="Retained charged unknown."
            ).model_dump(mode="json")
            report["raw_excerpt"] = json.dumps(
                {
                    "schema": "commercial-slot-quarantine.v1",
                    "sample_slot": slot,
                    "proof_sha256": _digest(proof),
                }
            )
            journal["slots"][slot] = {
                **deepcopy(prepared),
                "state": "quarantined",
                "report": report,
                "quarantine_history": [{"prepared_entry": deepcopy(prepared), "proof": proof}],
            }
        reports = {k: e["report"] for k, e in journal["slots"].items()}
        n = len(reports)
        aggregate = deepcopy(next(iter(reports.values())))
        if n > 1:
            aggregate["raw_excerpt"] = json.dumps(
                {
                    "schema_version": "chapter-quality-aggregation.v1",
                    "requested_samples": n,
                    "required_valid_samples": n // 2 + 1,
                    "valid_samples": 0,
                    "samples": [
                        {"sample_slot": k, "report": v, "valid": False} for k, v in reports.items()
                    ],
                }
            )
        aggregate = LLMQualityJudgeResult.model_validate(aggregate).model_dump(mode="json")
        journal["aggregate"] = {"slots_sha256": _digest(reports), "report": aggregate}
        r["root"].metadata_json = metadata
        await r["session"].commit()
    r["policy"][0] = "4" * 64
    return r


async def checked(r, owner=None):
    session = r["session"]
    for row in list(session.identity_map.values()):
        if inspect(row).persistent and row not in session.dirty:
            await session.refresh(row)
    before = [
        deepcopy({c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs})
        for row in session.identity_map.values()
    ]
    writes = []

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    event.listen(session.bind.sync_connection, "before_cursor_execute", sql)
    try:
        result = await validate_failed_review_recovery_lineage(
            session,
            settings=r["settings"],
            project=r["project"],
            chapter=r["chapter"],
            draft=r["draft"],
            previous=owner or r["latest"],
            execution_scope_id=r["scope"],
            current_policy_hash=r["policy"][0],
        )
        json.dumps(result, allow_nan=False)
        return result
    finally:
        event.remove(session.bind.sync_connection, "before_cursor_execute", sql)
        assert not writes
        assert not session.new and not session.dirty and not session.deleted
        after = [
            deepcopy({c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs})
            for row in session.identity_map.values()
        ]
        assert all(item in after for item in before)


async def test_native_one_hop_proof_is_read_only_and_authority_free(lineage):
    r = lineage
    proof = await checked(r)
    assert proof == await checked(r)
    assert len(proof["hops"]) == 1
    assert proof["previous_workflow_run_id"] == str(r["latest"].id)
    assert r["root"].status == "failed"
    assert r["report"].verdict == r["latest_report"].verdict == "unknown"
    assert proof["budget_usage"] == {"calls_used": 13, "tokens_charged": 42000}
    assert all(
        proof[k] is False
        for k in (
            "grade_reuse_allowed",
            "adoption_allowed",
            "attempt_reset_allowed",
            "new_credit_allowed",
            "dispatch_allowed",
        )
    )


async def test_read_only_api_uses_unlocked_actual_adoption_validator(lineage, monkeypatch):
    from bestseller.services import feedback

    r = lineage
    actual = feedback.validate_adopted_knowledge_source
    observed = []

    async def unlocked(*args, **kwargs):
        observed.append(kwargs.get("lock"))
        assert kwargs.get("lock") is False
        return await actual(*args, **kwargs)

    def no_lock(execute_state):
        assert getattr(execute_state.statement, "_for_update_arg", None) is None

    monkeypatch.setattr(feedback, "validate_adopted_knowledge_source", unlocked)
    event.listen(r["session"].sync_session, "do_orm_execute", no_lock)
    try:
        await checked(r)
    finally:
        event.remove(r["session"].sync_session, "do_orm_execute", no_lock)
    assert observed == [False]


async def test_actual_native_entry_linear_two_hops_keeps_history_and_allowance(lineage):
    r = lineage
    first = r["latest"]
    before = deepcopy(r["chapter"].metadata_json)
    old_source = deepcopy(r["root"].metadata_json)
    body = r["draft"].content_md
    await invoke(r)
    assert first.current_step == "chapter_adoption_required"
    assert r["root"].metadata_json == old_source and r["root"].status == "failed"
    assert (
        r["chapter"].metadata_json["chapter_review_recovery_cycles"][:-1]
        == before["chapter_review_recovery_cycles"]
    )
    assert (
        r["chapter"].metadata_json["chapter_review_recovery_cycles"][-1]["attempts_base"]
        == before["chapter_review_attempts_active"]
    )
    assert r["draft"].content_md == body
    r["policy"][0] = "5" * 64
    proof = await checked(r)
    assert len(proof["hops"]) == 2
    assert proof["hops"][0]["result_workflow_run_id"] == str(first.id)
    assert len(r["dispatches"]) == 2
    r["policy"][0] = "4" * 64
    with pytest.raises(
        ValueError, match=r"waiting_requires_original_cycle|waiting_policy_change_required"
    ):
        await invoke(r)
    assert len(r["dispatches"]) == 2


@pytest.mark.parametrize(
    "retained",
    [
        (
            "Aster Observatory",
            "science-fiction",
            "The astronomer seals the lens and chooses the ground antenna.",
        ),
        (
            "The Orchard Debt",
            "romance",
            "Mira returns the lease. She chooses the shared orchard despite the debt.",
        ),
    ],
    indirect=True,
)
async def test_unrelated_books_have_identical_lineage_rules(lineage):
    proof = await checked(lineage)
    assert len(proof["hops"]) == 1 and proof["dispatch_allowed"] is False


@pytest.mark.parametrize(
    "change",
    [
        "stale_report",
        "quality",
        "latest_receipt",
        "receipt_payload",
        "other_body",
        "source_cycle",
        "proof_digest",
        "failed_metadata",
        "terminal_history",
        "prepared_snapshot",
        "cycle_ambiguity",
        "unconsumed_failed",
        "fork",
        "receipt_state",
        "missing_edge",
        "unknown_grade",
        "primary",
        "semantic",
        "pause",
        "authorization",
        "unsettled",
        "released_credit",
        "receipt_changed",
    ],
)
async def test_tamper_or_unconsumed_origin_is_fail_closed(lineage, change):
    r, session = lineage, lineage["session"]
    cm, wm = deepcopy(r["chapter"].metadata_json), deepcopy(r["root"].metadata_json)
    latest = cm["chapter_critic_cycles"][r["latest"].metadata_json["critic_cycle_id"]]
    if change == "stale_report":
        cm["chapter_review_evidence_report_id"] = str(r["report"].id)
    elif change == "quality":
        r["latest_quality"].chapter_draft_version_id = uuid4()
    elif change == "latest_receipt":
        m = deepcopy(r["latest"].metadata_json)
        m["review_prerequisite_wait"]["quality_score_id"] = str(uuid4())
        r["latest"].metadata_json = m
    elif change == "receipt_payload":
        r["latest_report"].structured_output = {
            **r["latest_report"].structured_output,
            "new_raw_evidence": "tampered",
        }
    elif change == "other_body":
        r["draft"].content_md += " Changed body."
    elif change == "source_cycle":
        cm["chapter_critic_cycles"][r["root"].metadata_json["critic_cycle_id"]]["attempts"][-1][
            "state"
        ] = "prepared"
    elif change == "proof_digest":
        wm["review_recovery_policy"]["failed_origin_proof"]["proof_sha256"] = "0" * 64
    elif change == "failed_metadata":
        wm["unproven_change"] = True
    elif change == "terminal_history":
        wm["terminal_state_history"].insert(0, {"status": "other"})
    elif change == "prepared_snapshot":
        wm["review_recovery_policy"]["attempts_base"] += 1
    elif change == "cycle_ambiguity":
        cm["chapter_critic_cycles"]["0" * 64] = {**deepcopy(latest), "cycle_id": "0" * 64}
    elif change in {"unconsumed_failed", "fork"}:
        other = WorkflowRunModel(
            project_id=r["project"].id,
            scope_type="chapter",
            scope_id=r["chapter"].id,
            workflow_type="chapter_pipeline",
            status="failed",
            current_step="review_chapter",
            metadata_json={"execution_scope_id": r["scope"], "critic_cycle_id": "9" * 64},
        )
        session.add(other)
        await session.flush()
        cm["chapter_critic_cycles"]["9" * 64] = {
            **deepcopy(latest),
            "cycle_id": "9" * 64,
            "review_workflow_run_id": str(other.id),
        }
        if change == "fork":
            edge = deepcopy(cm["chapter_review_recovery_cycles"][-1])
            edge["result_workflow_run_id"] = str(other.id)
            edge["cycle_id"] = "9" * 64
            cm["chapter_review_recovery_cycles"].append(edge)
    elif change == "receipt_state":
        cm["chapter_review_recovery_cycles"][-1]["state"] = "running"
    elif change == "missing_edge":
        cm["chapter_review_recovery_cycles"] = []
    elif change == "unknown_grade":
        r["latest_report"].verdict = "rewrite"
    elif change == "primary":
        session.add(
            LlmRunModel(
                project_id=r["project"].id,
                workflow_run_id=r["latest"].id,
                logical_role="critic",
                provider="fixture",
                model_name="fixture",
                prompt_template="chapter_review",
                metadata_json={
                    "primary_review_body_sha256": hashlib.sha256(
                        r["draft"].content_md.encode()
                    ).hexdigest()
                },
            )
        )
    elif change == "semantic":
        r["semantic"].return_value = "6" * 64
    elif change == "pause":
        r["control"].desired_state = "pause"
    elif change == "authorization":
        r["budget"].metadata_json = {}
    elif change == "unsettled":
        r["operation"].status = "unknown"
    elif change in {"released_credit", "receipt_changed"}:
        stop = deepcopy(r["stop"].result_json)
        if change == "released_credit":
            stop["receipt"]["credit_released"] = True
        else:
            stop["actor"] = "different-actor"
        r["stop"].result_json = stop
    r["chapter"].metadata_json, r["root"].metadata_json = cm, wm
    await session.commit()
    with pytest.raises(ValueError, match="chapter_review_recovery_lineage_"):
        await checked(r)


async def test_another_historical_owner_cannot_replace_latest(lineage):
    r = lineage
    with pytest.raises(ValueError, match="chapter_review_recovery_lineage_"):
        await checked(r, r["root"])
    r["latest"].current_step = "critic_evidence_unknown"
    await r["session"].commit()
    with pytest.raises(ValueError, match="latest_owner_binding"):
        await checked(r)


async def test_old_failed_helper_still_rejects_successor_latest_report(lineage):
    from bestseller.services.chapter_failed_review_recovery import (
        validate_failed_review_policy_source,
    )

    r = lineage
    with pytest.raises(ValueError, match="latest_unknown_report_binding"):
        await validate_failed_review_policy_source(
            r["session"],
            settings=r["settings"],
            project=r["project"],
            chapter=r["chapter"],
            draft=r["draft"],
            previous=r["root"],
            execution_scope_id=r["scope"],
            current_policy_hash=r["policy"][0],
        )


@pytest.mark.parametrize(
    "lineage",
    ["empty", "null", "list", "quarantine-absent", "quarantine-null", "quarantine-multi"],
    indirect=True,
)
async def test_exact_native_metadata_preimages_preserve_prior_history(lineage):
    proof = await checked(lineage)
    assert len(proof["hops"]) == 1


@pytest.mark.parametrize("change", ["prepared", "proof", "aggregate", "cohort", "unrelated"])
@pytest.mark.parametrize("lineage", ["quarantine-multi"], indirect=True)
async def test_quarantine_reversal_cannot_hide_arbitrary_metadata(lineage, change):
    r = lineage
    metadata = deepcopy(r["root"].metadata_json)
    journal = metadata["commercial_component_cohorts"]["c" * 64]
    entry = next(iter(journal["slots"].values()))
    if change == "prepared":
        entry["quarantine_history"][-1]["prepared_entry"]["request"]["retained_bytes"] = "tamper"
    elif change == "proof":
        entry["quarantine_history"][-1]["proof"]["unexpected"] = True
    elif change == "aggregate":
        journal["aggregate"]["slots_sha256"] = "0" * 64
    elif change == "cohort":
        journal["binding"]["unexpected"] = True
    else:
        metadata["unrelated"] = "unproven"
    r["root"].metadata_json = metadata
    await r["session"].commit()
    with pytest.raises(ValueError, match="chapter_review_recovery_lineage_"):
        await checked(r)

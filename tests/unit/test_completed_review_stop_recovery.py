"""Real guarded SQLite, current preflight and financial closure; no provider IO."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db  # noqa: F401
from test_chapter_critic_semantic_dependencies import semantic_db  # noqa: F401

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.base import Base
from bestseller.infra.db.models import (
    BookProductionControlModel, ChapterDraftVersionModel, ChapterModel,
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel,
    ProjectModel, QualityScoreModel, ReviewReportModel, RewriteTaskModel, WorkflowRunModel,
)
from bestseller.services import completed_review_stop_recovery as recovery, pipelines
from bestseller.services.creation_configuration import project_settings
from bestseller.services.waiting_chapter_review_resume import _ledger, _row, _sha
from bestseller.settings import apply_runtime_llm_profile, load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

MODELS = (ProjectModel, ChapterModel, ChapterDraftVersionModel, WorkflowRunModel,
          ReviewReportModel, QualityScoreModel, RewriteTaskModel,
          BookProductionControlModel, ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel)


async def snapshot(session):
    with session.no_autoflush:
        return {model.__tablename__: [dict(row) for row in (await session.execute(
            select(*model.__table__.columns).order_by(*model.__table__.primary_key.columns)
        )).mappings()] for model in MODELS}


async def snapshot_all_tables(session):
    with session.no_autoflush:
        connection = await session.connection()
        names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        assert len(names) >= 20
        rows = {}
        for name in names:
            table = Base.metadata.tables[name]
            rows[name] = [dict(row) for row in (await session.execute(
                select(*table.columns).order_by(*table.primary_key.columns)
            )).mappings()]
        return rows


@pytest.fixture
async def case(semantic_db, monkeypatch):  # noqa: F811
    session, project, chapter, scene, design, _ = semantic_db
    conn = await session.connection()
    for model in MODELS:
        ddl = str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    async def forbidden(*args, **kwargs):
        pytest.fail("Recovery must never dispatch a model request")
    from bestseller.services import llm
    monkeypatch.setattr(llm, "complete_text", forbidden)
    scope = f"project:{project.id}"
    project.status = "paused"
    project.metadata_json = {**(project.metadata_json or {}), "execution_scope_id": scope,
                             "production_paused": False, "production_pause_reason": None,
                             "execution_programmatic_recovery_history": [{"old": "preserve"}]}
    source = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
        version_no=3, content_md="阿青关上门，把唯一的钥匙交还给船长。", is_current=True,
        promotion_state="candidate")
    owner = WorkflowRunModel(project_id=project.id, scope_type="chapter", scope_id=chapter.id,
        workflow_type="chapter_pipeline", status="completed",
        current_step="formal_review_requires_source_check_before_adoption", metadata_json={})
    control = BookProductionControlModel(project_id=project.id, desired_state="run", command_serial=25)
    budget = ExecutionBudgetModel(scope_id=scope, max_calls=100, max_tokens=100000,
        calls_used=1, tokens_charged=100,
        deadline_at=datetime.now(timezone.utc) + timedelta(days=1), status="active", metadata_json={})
    session.add_all([source, owner, control, budget])
    await session.flush()
    fingerprint = pipelines.chapter_review_body_fingerprint(source.content_md)
    result = ChapterReviewResult(verdict="pass", severity_max="low",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, .8)))
    report = ReviewReportModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
        reviewer_type="synthetic-no-provider", verdict="pass", structured_output={
            "draft_id": str(source.id), "reviewed_body_fingerprint": fingerprint,
            "critic_evidence": {"status": "verified"}})
    session.add(report)
    await session.flush()
    quality = QualityScoreModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
        chapter_draft_version_id=source.id, review_report_id=report.id, score_overall=.8, is_current=False)
    session.add(quality)
    await session.flush()
    terminal = {"code": recovery.CODE, "terminal": True, "workflow_id": str(owner.id)}
    project.metadata_json = {**project.metadata_json, "execution_terminal": terminal}
    settings = project_settings(apply_runtime_llm_profile(load_settings()), project)
    cid = pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"])
    cycle = {"schema_version": "chapter-critic-cycle.v1", "cycle_id": cid, "state": "completed",
        "source_draft_id": str(source.id), "source_body_hash": fingerprint["digest"],
        "review_workflow_run_id": str(owner.id),
        "policy_source_hash": pipelines.chapter_review_recovery_source_hash(),
        "semantic_dependencies_sha256": await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter),
        "attempts": [{"state": "completed", "outcome": "available", "attempt": 1,
            "operation_namespace": f"chapter-critic:{cid}:1", "draft_id": str(source.id),
            "body_fingerprint": fingerprint, "review_report_id": str(report.id),
            "quality_score_id": str(quality.id), "review_result": result.model_dump(mode="json")}]}
    chapter.metadata_json = {"chapter_critic_cycles": {cid: cycle}}
    owner.metadata_json = {"execution_scope_id": scope, "critic_cycle_id": cid,
        "source_draft_id": str(source.id), "source_body_hash": fingerprint["digest"],
        "review_report_id": str(report.id), "quality_score_id": str(quality.id), "final_verdict": "pass",
        "execution_terminal": deepcopy(terminal), "previous_failures": [{"step": "old", "code": recovery.CODE}]}
    op = ExecutionOperationModel(key="a" * 64, scope_id=scope, input_hash="b" * 64,
        status="completed", owner=str(owner.id), lease_until=budget.deadline_at,
        result_json={"historical": "retained"})
    attempt = ExecutionAttemptModel(id="c" * 64, operation_key=op.key, scope_id=scope,
        model_name="fixture-only", input_hash=op.input_hash, reserved_tokens=100,
        actual_tokens=100, status="completed", created_at=datetime.now(timezone.utc))
    session.add_all([op, attempt])
    await session.commit()
    return SimpleNamespace(session=session, project=project, chapter=chapter, source=source,
        owner=owner, control=control, budget=budget, op=op, attempt=attempt, report=report,
        quality=quality, scene=scene, design=design, cid=cid, settings=settings, scope=scope)


def projection(r):
    return SimpleNamespace(id=r.project.id, status=r.project.status,
                           metadata_json=deepcopy(r.project.metadata_json))


async def recover(r, target=None):
    return await recovery.recover_completed_review_stop(r.session, r.settings,
        target if target is not None else r.project, actor="test operator", reason="explicit stale-stop repair")


async def test_projection_only_binds_real_preflight_and_ledger_without_changing_any_row(case):
    r = case
    before = await snapshot(r.session)
    target = projection(r)
    receipt = await recover(r, target)
    assert await snapshot(r.session) == before
    assert not r.session.new and not r.session.dirty and not r.session.deleted
    expected = deepcopy(r.project.metadata_json)
    expected.pop("execution_terminal")
    expected["execution_programmatic_recovery_history"].append(receipt)
    assert target.metadata_json == expected and target.status == "paused"
    assert receipt["binding"]["report_id"] == str(r.report.id)
    assert receipt["binding"]["quality_score_id"] == str(r.quality.id)
    assert receipt["binding"]["reuse_kind"] == "available_review"
    assert receipt["owner_sha256"] == _sha(_row(r.owner))
    assert receipt["owner_binding"]["status"] == "completed"
    assert "owner_metadata" not in receipt
    assert receipt["previous_terminal"] == r.project.metadata_json["execution_terminal"]
    assert receipt["cycle_sha256"] == _sha(r.chapter.metadata_json["chapter_critic_cycles"][r.cid])
    assert receipt["report_sha256"] == _sha(_row(r.report))
    assert receipt["quality_sha256"] == _sha(_row(r.quality))
    assert receipt["ledger_sha256"] == _sha(await _ledger(r.session, r.scope, lock=True))
    assert receipt["receipt_sha256"] == _sha({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    assert all(receipt[k] is False for k in (
        "grade_changed", "adoption_created", "credit_released", "dispatch_authorized", "physical_prompt_proof"))
    assert receipt["provider_calls"] == 0
    assert await recover(r, target) is None
    assert len(target.metadata_json["execution_programmatic_recovery_history"]) == 2


async def test_real_project_changes_only_metadata_and_rolls_back_atomically(case):
    r = case
    before = await snapshot(r.session)
    savepoint = await r.session.begin_nested()
    await recover(r)
    assert r.project in r.session.dirty
    await r.session.flush()
    assert r.project.status == "paused" and r.control.command_serial == 25
    await savepoint.rollback()
    assert await snapshot(r.session) == before
    await r.session.refresh(r.project)
    await recover(r)
    await r.session.commit()
    assert await recover(r) is None
    assert r.project.status == "paused" and r.project.metadata_json["production_paused"] is False
    after = await snapshot(r.session)
    assert {k: v for k, v in after.items() if k != "projects"} == {k: v for k, v in before.items() if k != "projects"}


@pytest.mark.parametrize("fault", [
    "changed_body", "unfinished", "unknown", "other_code", "malformed_stop", "missing_workflow",
    "foreign_owner", "owner_scope", "cycle_owner", "owner_report", "owner_quality", "owner_source",
    "pause", "stop", "missing_control", "active", "active_other_chapter", "active_global",
    "reserved", "unknown_attempt", "unknown_operation", "orphan_attempt", "budget_unknown",
    "policy_changed", "semantic_changed", "missing_report", "conflicting_code", "malformed_history",
])
async def test_partial_foreign_changed_and_unsettled_evidence_fails_closed(case, fault):
    r = case
    cm = deepcopy(r.chapter.metadata_json)
    cycle = cm["chapter_critic_cycles"][r.cid]
    pm = deepcopy(r.project.metadata_json)
    wm = deepcopy(r.owner.metadata_json)
    if fault == "changed_body": r.source.content_md += "Changed."
    elif fault == "unfinished": r.owner.status = "running"
    elif fault == "unknown": cycle["attempts"][-1]["outcome"] = "unknown"
    elif fault == "other_code": pm["execution_terminal"]["code"] = "execution_result_unknown"
    elif fault == "malformed_stop": pm["execution_terminal"] = None
    elif fault == "missing_workflow": pm["execution_terminal"].pop("workflow_id")
    elif fault == "foreign_owner": r.owner.project_id = uuid4()
    elif fault == "owner_scope": wm["execution_scope_id"] = "project:other"
    elif fault == "cycle_owner": cycle["review_workflow_run_id"] = str(uuid4())
    elif fault == "owner_report": wm["review_report_id"] = str(uuid4())
    elif fault == "owner_quality": wm["quality_score_id"] = str(uuid4())
    elif fault == "owner_source": wm["source_draft_id"] = str(uuid4())
    elif fault in {"pause", "stop"}: r.control.desired_state = fault
    elif fault == "missing_control": await r.session.delete(r.control)
    elif fault.startswith("active"):
        r.session.add(WorkflowRunModel(project_id=None if fault == "active_global" else r.project.id,
            scope_type="project" if fault == "active_global" else "chapter",
            scope_id=r.project.id if fault == "active_global" else uuid4(),
            workflow_type="chapter_pipeline", status="queued", metadata_json={}))
    elif fault == "reserved": r.attempt.status = "reserved"
    elif fault == "unknown_attempt": r.attempt.status = "unknown"
    elif fault == "unknown_operation": r.op.status = "unknown"
    elif fault == "orphan_attempt": r.attempt.operation_key = "d" * 64
    elif fault == "budget_unknown": r.budget.status = "unknown"
    elif fault == "policy_changed": cycle["policy_source_hash"] = "0" * 64
    elif fault == "semantic_changed": r.scene.exit_state = {"changed": True}
    elif fault == "missing_report": await r.session.delete(r.report)
    elif fault == "conflicting_code": pm["execution_budget"] = {"code": "execution_cancelled", "terminal": True}
    elif fault == "malformed_history": pm["execution_programmatic_recovery_history"] = {}
    r.chapter.metadata_json, r.project.metadata_json, r.owner.metadata_json = cm, pm, wm
    await r.session.commit()
    target = projection(r)
    old_target = deepcopy(target.metadata_json)
    before = await snapshot(r.session)
    with pytest.raises(ValueError):
        await recover(r, target)
    assert target.metadata_json == old_target
    assert await snapshot(r.session) == before


async def test_dirty_source_is_preserved_without_autoflush(case):
    r = case
    before = await snapshot(r.session)
    r.source.content_md += "Uncommitted."
    target = projection(r)
    with pytest.raises(ValueError, match="uncommitted_source"):
        await recover(r, target)
    assert r.source in r.session.dirty and r.source.content_md.endswith("Uncommitted.")
    assert await snapshot(r.session) == before


async def test_projection_changed_before_lock_is_refused(case):
    r = case
    target = projection(r)
    target.metadata_json["other"] = "unpersisted projection"
    with pytest.raises(ValueError, match="project_changed"):
        await recover(r, target)


async def test_source_changes_during_preflight_are_refused_without_clearing(case, monkeypatch):
    r = case
    target = projection(r)
    original = recovery.validate_retained_review_before_processing
    async def changed(*args):
        proof = await original(*args)
        r.source.content_md += "Changed during inspection."
        return proof
    monkeypatch.setattr(recovery, "validate_retained_review_before_processing", changed)
    with pytest.raises(ValueError):
        async with r.session.begin_nested():
            await recover(r, target)
    assert "execution_terminal" in target.metadata_json


async def test_settings_are_explicit_and_current_not_defaulted(case):
    r = case
    changed = r.settings.model_copy(deep=True)
    changed.llm.critic.model = "different-current-model"
    with pytest.raises(ValueError, match="semantic_dependencies_changed"):
        await recovery.recover_completed_review_stop(r.session, changed, projection(r), actor="test", reason="test")


async def test_no_stop_is_noop_even_without_settings_or_authority(case):
    target = projection(case)
    target.metadata_json.pop("execution_terminal")
    assert await recovery.recover_completed_review_stop(case.session, None, target, actor="", reason="") is None


async def test_normal_web_entry_recovers_then_commits_run_with_real_helper(case):
    from bestseller.services.web_project_resume import request_explicit_web_resume

    r = case
    before = await snapshot(r.session)
    assert await request_explicit_web_resume(r.session, slug=r.project.slug,
        actor="web operator", reason="explicit same-owner repair") is True
    await r.session.commit()
    assert r.project.status == "writing"
    assert "execution_terminal" not in r.project.metadata_json
    receipt = r.project.metadata_json["execution_programmatic_recovery_history"][-1]
    assert receipt["binding"]["report_id"] == str(r.report.id)
    assert receipt["actor"] == "web operator"
    assert r.control.command_serial == 26 and r.control.desired_state == "run"
    assert await request_explicit_web_resume(r.session, slug=r.project.slug,
        actor="web operator", reason="repeat") is False
    after = await snapshot(r.session)
    excluded = {"projects", "book_production_control"}
    assert {k: v for k, v in after.items() if k not in excluded} == {
        k: v for k, v in before.items() if k not in excluded}


async def test_normal_entry_late_run_failure_rolls_back_receipt_even_if_caller_commits(case, monkeypatch):
    from bestseller.services import web_project_resume as web

    r = case
    before = await snapshot(r.session)
    real_run = web.request_run
    async def fail_after_run(*args, **kwargs):
        await real_run(*args, **kwargs)
        raise ValueError("injected late RUN failure")
    monkeypatch.setattr(web, "request_run", fail_after_run)
    with pytest.raises(ValueError, match="injected late RUN failure"):
        await web.request_explicit_web_resume(r.session, slug=r.project.slug, actor="test", reason="test")
    await r.session.commit()
    assert await snapshot(r.session) == before


async def test_normal_entry_structural_veto_does_not_persist_prepared_receipt(case):
    from bestseller.services import web_project_resume as web

    r = case
    r.project.metadata_json = {**r.project.metadata_json, "structural_repair_required": True}
    await r.session.commit()
    before = await snapshot(r.session)
    with pytest.raises(web.WebProjectResumeBlockedError, match="structurally_blocked"):
        await web.request_explicit_web_resume(r.session, slug=r.project.slug, actor="test", reason="test")
    await r.session.commit()
    assert await snapshot(r.session) == before


@pytest.mark.parametrize("fault", ["invalid_source", "unfinished_owner"])
async def test_normal_entry_refusal_raises_blocked_and_preserves_every_table(case, fault):
    from bestseller.services import web_project_resume as web

    r = case
    if fault == "invalid_source":
        r.source.content_md += "正文已经变化。"
    else:
        r.owner.status = "running"
    await r.session.commit()
    before = await snapshot_all_tables(r.session)
    with pytest.raises(web.WebProjectResumeBlockedError,
                       match="^web_resume_completed_review_proof_invalid$") as caught:
        await web.request_explicit_web_resume(r.session, slug=r.project.slug,
                                             actor="test", reason="explicit repair")
    assert isinstance(caught.value.__cause__, ValueError)
    # Catching the refusal and committing cannot accidentally clear the stop
    # or queue normal RUN by returning the legal already-running False value.
    await r.session.commit()
    assert await snapshot_all_tables(r.session) == before


async def test_normal_entry_available_rewrite_preserves_real_pending_task_and_source(case):
    from bestseller.services.rewrite_patch import text_hash
    from bestseller.services.web_project_resume import request_explicit_web_resume

    r = case
    task = RewriteTaskModel(project_id=r.project.id, trigger_type="chapter_review",
        trigger_source_id=r.chapter.id, rewrite_strategy="chapter_coherence_bridge_rewrite",
        status="pending", instructions="修复已审校的衔接问题。", metadata_json={
            "chapter_id": str(r.chapter.id), "review_report_id": str(r.report.id),
            "draft_id": str(r.source.id), "parent_draft_id": str(r.source.id),
            "parent_draft_hash": text_hash(r.source.content_md)})
    r.session.add(task)
    await r.session.flush()
    metadata = deepcopy(r.chapter.metadata_json)
    entry = metadata["chapter_critic_cycles"][r.cid]["attempts"][-1]
    entry["rewrite_task_id"] = str(task.id)
    entry["review_result"]["verdict"] = "rewrite"
    r.chapter.metadata_json = metadata
    r.report.verdict = "rewrite"
    r.owner.metadata_json = {**r.owner.metadata_json, "final_verdict": "rewrite",
                             "rewrite_task_id": str(task.id)}
    await r.session.commit()
    before = await snapshot_all_tables(r.session)
    assert await request_explicit_web_resume(r.session, slug=r.project.slug,
        actor="web operator", reason="resume existing available rewrite") is True
    await r.session.commit()
    receipt = r.project.metadata_json["execution_programmatic_recovery_history"][-1]
    assert receipt["binding"]["reuse_kind"] == "available_review"
    assert receipt["binding"]["verdict"] == "rewrite"
    assert receipt["binding"]["task_id"] == str(task.id)
    assert receipt["binding"]["report_id"] == str(r.report.id)
    assert receipt["binding"]["quality_score_id"] == str(r.quality.id)
    assert receipt["adoption_created"] is False and receipt["grade_changed"] is False
    assert task.status == "pending" and task.attempts == 0
    assert r.source.is_current and r.source.promotion_state == "candidate"
    assert r.project.status == "writing" and r.control.command_serial == 26
    after = await snapshot_all_tables(r.session)
    excluded = {"projects", "book_production_control"}
    assert {k: v for k, v in after.items() if k not in excluded} == {
        k: v for k, v in before.items() if k not in excluded}

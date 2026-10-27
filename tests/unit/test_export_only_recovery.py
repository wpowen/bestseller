"""Offline R08 control-flow tests; synthetic seams are never native grades.

No engine or database connection is used. SQLAlchemy's unbound identity map
exercises the real source-write listener; publisher doubles check transaction
wiring only, not PostgreSQL locking or real SAVEPOINT durability (integration).
"""
from copy import deepcopy
import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import socket
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, make_transient_to_detached

from bestseller.infra.db import models as m
from bestseller.services import export_only_recovery as recovery
from bestseller.services import exports, llm

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_paid_or_external_io(monkeypatch):
    """Fail even when run() catches an accidental provider/connection error."""
    attempted = []

    def forbidden(*_args, **_kwargs):
        attempted.append(True)
        raise AssertionError("R08 unit tests forbid provider/SDK/network/DB calls")

    for name in ("complete_text", "_call_litellm", "_paid_transport_attempt", "_get_litellm"):
        monkeypatch.setattr(llm, name, forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    yield
    assert not attempted, "A swallowed exception must not hide an external call"


class MemorySession:
    """Read seams plus observable transaction boundaries, with no DB driver."""

    def __init__(self, rig, label):
        self.rig, self.label = rig, label
        self.statements = []
        self.commits = self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        if entity is m.ProjectModel:
            return self.rig.project
        if entity is m.ExecutionBudgetModel:
            return self.rig.budget
        if entity is m.RewriteTaskModel:
            return self.rig.pending_rewrite
        raise AssertionError(f"Unexpected scalar read: {entity}")

    async def scalars(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        return self.rig.rows.get(entity, [])

    async def get(self, model, key):
        assert model is m.ProjectModel and key == self.rig.project_id
        return self.rig.project

    async def connection(self):
        return self.rig.connection

    async def refresh(self, row):
        assert row is self.rig.project

    async def commit(self):
        self.commits += 1
        self.rig.events.append(self.label + ":commit")

    async def rollback(self):
        self.rollbacks += 1
        self.rig.events.append(self.label + ":rollback")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    pid, cid, did = uuid4(), uuid4(), uuid4()
    r = NS(project_id=pid, chapter_id=cid, draft_id=did, events=[], audits=[],
           publishers=[], rows={}, pending_rewrite=None, halted=False,
           already_complete=False, body_complete=True, gate_error=None,
           published=False, financial_change=False, source_change=False,
           control_change=False, postcommit_complete=True, connection=object())
    r.project = m.ProjectModel(id=pid, slug="synthetic-export-unit", title="Synthetic",
        genre="fiction", language="zh-CN", project_type="linear", target_chapters=1,
        target_word_count=2000, status="writing", metadata_json={
            "execution_scope_id": "synthetic-export-scope", "calls_used": 17,
            "creation_canonical_snapshot_hash": "canonical-preserved",
            "book_design_snapshot": {"promise": "preserved"},
            "title_revision_history": [{"status": "failed", "reason": "keep me"}],
        })
    r.settings = NS(output=NS(base_dir=tmp_path / "never-written-output"))
    r.budget = m.ExecutionBudgetModel(scope_id="synthetic-export-scope", max_calls=17,
        calls_used=17, max_tokens=100, tokens_charged=100, status="exhausted",
        deadline_at=datetime(2020, 1, 1, tzinfo=UTC), metadata_json={"spent": "preserve"})
    # This is explicitly a synthetic native-reader seam, not a debt/grade proof.
    r.sources = [{"chapter_id": str(cid), "draft_id": str(did), "body_sha256": "a" * 64,
                  "adoption_state": "accepted_with_debt",
                  "adoption_receipt": {"fixture": "control-flow-only"}}]
    r.ready = NS(verdict=NS(body_complete=True), sources=r.sources,
                 payloads=[(NS(metadata_json={"historic": True}), NS(content_md="UNCHANGED BODY"))])
    r.outer = MemorySession(r, "outer")
    r.fresh = MemorySession(r, "fresh")
    r.factory_calls = 0

    def factory():
        r.factory_calls += 1
        assert r.factory_calls <= 2
        return r.outer if r.factory_calls == 1 else r.fresh

    r.factory = factory

    async def control(session, project_id, *, for_update):
        assert project_id == pid
        r.events.append("control:locked" if for_update else "control:read")
        serial = int(r.control_change and r.published)
        return NS(halted=r.halted, to_payload=lambda: {"serial": serial, "desired_state": "run"})

    async def closure(session, project, *, lock):
        assert project is r.project and lock is False
        complete = r.already_complete or r.published
        if session is r.fresh:
            complete = r.postcommit_complete
        return NS(verdict=NS(is_complete=complete, body_complete=r.body_complete,
                             reason="synthetic-readback"), sources=deepcopy(r.sources))

    async def readiness(session, project, *, lock):
        assert project is r.project
        r.events.append("source:locked" if lock else "source:read")
        if r.source_change and r.published:
            changed = deepcopy(r.ready)
            changed.sources[0]["body_sha256"] = "b" * 64
            return changed
        return r.ready

    def gate(_project, payloads, **_kwargs):
        r.events.append("gate")
        if r.gate_error:
            raise ValueError(r.gate_error)
        # Native annotations affect the defensive copies, not adopted rows.
        payloads[0][0].metadata_json["synthetic-annotation"] = True

    def audit(path, value):
        r.events.append("audit:" + value["status"])
        r.audits.append((path, deepcopy(value)))

    class Publisher(MemorySession):
        def __init__(self, **kwargs):
            assert kwargs == {"bind": r.connection, "join_transaction_mode": "create_savepoint",
                              "autoflush": False, "expire_on_commit": False}
            super().__init__(r, "publisher-savepoint")
            r.publishers.append(self)

    async def settle(session, project, **_kwargs):
        assert session in r.publishers and project is r.project
        r.events.append("publish")
        r.published = True
        if r.financial_change:
            r.budget.tokens_charged += 1
        return NS(is_complete=True)

    monkeypatch.setattr(recovery, "load_control_state", control)
    monkeypatch.setattr(recovery, "read_book_delivery_closure", closure)
    monkeypatch.setattr(recovery, "read_book_delivery_readiness", readiness)
    monkeypatch.setattr(recovery, "project_settings", lambda settings, project: settings)
    monkeypatch.setattr(exports, "_raise_if_export_blocked", gate)
    monkeypatch.setattr(exports, "_run_terminal_export_gate", gate)
    monkeypatch.setattr(recovery, "_audit_write", audit)
    monkeypatch.setattr(recovery, "AsyncSession", Publisher)
    # Actual source guard is tested independently with real unbound ORM state.
    monkeypatch.setattr(recovery, "_protect_chapters", lambda session, sources: r.events.append("protect"))
    from bestseller.services import book_closure
    monkeypatch.setattr(book_closure, "settle_project_status_on_closure", settle)
    return r


async def preflight(r, **kwargs):
    return await recovery.preflight_export_only_recovery(r.outer, r.settings,
        project_id=r.project_id, requested_by=kwargs.pop("requested_by", "unit-operator"), **kwargs)


async def run(r, **kwargs):
    return await recovery.run_export_only_recovery(r.factory, r.settings,
        project_id=r.project_id, requested_by=kwargs.pop("requested_by", "unit-operator"), **kwargs)


def assert_no_writes(r):
    assert not r.audits and not r.publishers and not r.published
    assert r.outer.commits == r.fresh.commits == 0
    assert not r.settings.output.base_dir.exists()


@pytest.mark.asyncio
async def test_preflight_is_typed_readonly_and_retains_source_and_finance(rig):
    before = deepcopy(recovery._row(rig.budget)), deepcopy(rig.project.metadata_json)
    result = await preflight(rig)
    assert isinstance(result, recovery.ExportOnlyRecoveryResult)
    assert result.status is recovery.ExportOnlyStatus.ELIGIBLE
    assert result.to_dict()["project_id"] == str(rig.project_id)
    assert result.to_dict()["status"] == "eligible"
    assert result.source_receipt["source_sha256"] == recovery._sha({
        k: v for k, v in result.source_receipt.items() if k != "source_sha256"})
    assert len(result.source_receipt["financial_sha256"]) == 64
    assert (recovery._row(rig.budget), rig.project.metadata_json) == before
    assert all(stmt._for_update_arg is None for stmt in rig.outer.statements)
    assert "control:read" in rig.events and "source:read" in rig.events
    assert rig.ready.payloads[0][0].metadata_json == {"historic": True}
    assert_no_writes(rig)
    with pytest.raises(FrozenInstanceError):
        result.reason = "mutated"
    detached = result.to_dict()
    detached["source_receipt"]["control"]["serial"] = 99
    assert result.source_receipt["control"]["serial"] == 0


@pytest.mark.asyncio
async def test_generic_preflight_matches_native_debt_argument_and_keeps_terminal_gate(rig, monkeypatch):
    calls = []
    def debt_gate(project, payloads, *, debt_warnings):
        assert project is rig.project and debt_warnings == []
        debt_warnings.append("SYNTHETIC conceded-debt diagnostic; not a new grade")
        calls.append("native-debt-check")
    def terminal_gate(project, payloads, *, settings):
        assert project is rig.project and settings is rig.settings
        assert payloads is not rig.ready.payloads
        assert payloads[0][1].content_md == "UNCHANGED BODY"
        calls.append("unchanged-terminal-gate")
        raise ValueError("SYNTHETIC terminal blocker still enforced")
    monkeypatch.setattr(exports, "_raise_if_export_blocked", debt_gate)
    monkeypatch.setattr(exports, "_run_terminal_export_gate", terminal_gate)
    result = await preflight(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert "terminal blocker still enforced" in result.reason
    assert calls == ["native-debt-check", "unchanged-terminal-gate"]
    assert_no_writes(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [preflight, run])
@pytest.mark.parametrize("missing,reason", [
    ("score", "current_grade_missing_or_ambiguous"),
    ("report", "retained_pass_report_required"),
])
async def test_public_entry_rejects_promoted_metadata_without_retained_grade(rig, monkeypatch, entry, missing, reason):
    # The native readiness seam says source/KG ready. A promoted flag alone
    # must still fail the service's actual retained-grade joins before export.
    rig.sources[0]["adoption_state"] = "promoted"
    rig.sources[0].pop("adoption_receipt")
    draft = m.ChapterDraftVersionModel(id=rig.draft_id, project_id=rig.project_id,
        chapter_id=rig.chapter_id, content_md="SYNTHETIC unchanged draft")
    original_scalar = rig.outer.scalar
    async def scalar(statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is m.ChapterDraftVersionModel:
            criteria = {c.left.key: c.right.value for c in statement._where_criteria}
            assert criteria["project_id"] == rig.project_id
            assert criteria["chapter_id"] == rig.chapter_id and criteria["id"] == rig.draft_id
            return draft
        if entity is m.ReviewReportModel:
            return None
        return await original_scalar(statement)
    monkeypatch.setattr(rig.outer, "scalar", scalar)
    if missing == "report":
        rig.rows[m.QualityScoreModel] = [m.QualityScoreModel(id=uuid4(), review_report_id=uuid4())]
    result = await entry(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert reason in result.reason and result.source_receipt is None
    assert "gate" not in rig.events
    assert_no_writes(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [preflight, run])
@pytest.mark.parametrize("case,reason", [
    ("stop", "operator_halted"), ("actor", "requested_by_required"),
    ("missing", "project_missing"), ("state", "project_state_blocked"),
    ("knowledge", "adoption_or_knowledge_incomplete"),
    ("rewrite", "pending_rewrite"), ("scope", "execution_scope_missing"),
    ("unknown_attempt", "unsettled_paid_attempt"), ("reserved_attempt", "unsettled_paid_attempt"),
    ("orphan", "orphan_paid_attempt"), ("owner", "active_foreign_owner"),
])
async def test_early_blocks_never_start_a_publisher_or_write_audit(rig, entry, case, reason):
    kwargs = {}
    if case == "stop": rig.halted = True
    elif case == "actor": kwargs["requested_by"] = "  "
    elif case == "missing": rig.project = None
    elif case == "state": rig.project.status = "archived"
    elif case == "knowledge": rig.body_complete = False
    elif case == "rewrite": rig.pending_rewrite = uuid4()
    elif case == "scope": rig.project.metadata_json = {}
    elif case in {"unknown_attempt", "reserved_attempt", "orphan"}:
        status = "unknown" if case == "unknown_attempt" else "reserved"
        rig.rows[m.ExecutionAttemptModel] = [m.ExecutionAttemptModel(
            id="attempt", operation_key="operation", status=status)]
        if case != "orphan":
            rig.rows[m.ExecutionOperationModel] = [m.ExecutionOperationModel(key="operation", status="completed")]
    elif case == "owner":
        rig.rows[m.WorkflowRunModel] = [m.WorkflowRunModel(id=uuid4(), workflow_type="chapter_pipeline",
            requested_by="foreign", status="running")]
    result = await entry(rig, **kwargs)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert reason in result.reason
    assert result.audit_path is None
    assert_no_writes(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [preflight, run])
async def test_pure_gate_rejects_before_publisher_audit_or_file_operations(rig, monkeypatch, entry):
    rig.gate_error = "synthetic_current_gate_rejected"
    def forbidden(*_args, **_kwargs):
        pytest.fail("A rejected pure gate must precede every file operation")
    for name in ("mkdir", "write_text", "replace"):
        monkeypatch.setattr(Path, name, forbidden)
    result = await entry(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert "synthetic_current_gate_rejected" in result.reason
    assert_no_writes(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic", [False, True])
async def test_short_story_gate_rejects_before_native_publisher_or_any_file_write(rig, monkeypatch, semantic):
    from bestseller.services import fanqie_short_quality as quality
    from bestseller.services import fanqie_short_semantic_readiness as semantics
    from bestseller.services import fanqie_short_source_export as native
    rig.project.project_type = "fanqie_short"
    assembly = NS(manifest={"sources": deepcopy(rig.sources)}, full_text="SYNTHETIC adopted story")
    async def read_assembly(session, project_id, *, lock):
        assert session is rig.outer and project_id == rig.project_id and lock is True
        return assembly
    async def context(*_args, **_kwargs):
        return NS(fixture="control-flow-only") if semantic else None
    def view(*_args, **_kwargs):
        return {"semantic_derived_review": {"remaining_blockers": ["SYNTHETIC blocker"]}} if semantic else None
    def forbidden(*_args, **_kwargs):
        pytest.fail("Rejected short-story gate reached publisher or filesystem")
    monkeypatch.setattr(native, "read_adopted_assembly", read_assembly)
    monkeypatch.setattr(native, "publish_adopted_assembly", forbidden)
    monkeypatch.setattr(quality, "review_whole_fanqie_short_story", lambda *_args, **_kwargs: NS(passed=False))
    monkeypatch.setattr(semantics, "load_upload_semantic_context", context)
    monkeypatch.setattr(semantics, "semantic_readiness_view", view)
    for name in ("mkdir", "write_text", "replace"):
        monkeypatch.setattr(Path, name, forbidden)
    result = await run(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert "current_whole_gate_blocked" in result.reason
    assert_no_writes(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [preflight, run])
async def test_already_complete_returns_without_export_or_audit(rig, entry):
    rig.already_complete = True
    result = await entry(rig)
    assert result.status is recovery.ExportOnlyStatus.ALREADY_COMPLETE
    assert result.source_receipt["sources"] == rig.sources
    assert_no_writes(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "id", "type", "actor", "second_owner"])
async def test_owner_parameter_allows_only_exact_native_owner(rig, defect):
    owner = m.WorkflowRunModel(id=uuid4(), project_id=rig.project_id,
        workflow_type="export_only_recovery", requested_by="unit-operator", status="running")
    rig.rows[m.WorkflowRunModel] = [owner]
    supplied = owner.id
    if defect == "id": supplied = uuid4()
    if defect == "type": owner.workflow_type = "chapter_pipeline"
    if defect == "actor": owner.requested_by = "other-actor"
    if defect == "second_owner":
        rig.rows[m.WorkflowRunModel].append(m.WorkflowRunModel(id=uuid4(), workflow_type="chapter_pipeline"))
    result = await preflight(rig, owner_workflow_run_id=supplied)
    assert result.status is (recovery.ExportOnlyStatus.ELIGIBLE if defect is None else recovery.ExportOnlyStatus.BLOCKED)
    if defect is not None: assert "active_foreign_owner" in result.reason
    assert_no_writes(rig)


@pytest.mark.asyncio
async def test_unknown_operation_requires_verified_non_dispatch_control(rig, monkeypatch):
    from bestseller.services import execution_control_purpose
    operation = m.ExecutionOperationModel(key="synthetic-control", status="unrecognized")
    rig.rows[m.ExecutionOperationModel] = [operation]
    calls = []
    async def verify(session, **kwargs):
        calls.append(kwargs["operation"])
        return None
    monkeypatch.setattr(execution_control_purpose, "verify_non_dispatch_control", verify)
    result = await preflight(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert "unsettled_paid_operation" in result.reason and calls == [operation]
    assert_no_writes(rig)


@pytest.mark.asyncio
async def test_run_rechecks_then_returns_completed_after_fresh_readback(rig):
    before = deepcopy(rig.project.metadata_json), deepcopy(recovery._row(rig.budget))
    result = await run(rig)
    assert result.status is recovery.ExportOnlyStatus.COMPLETED
    assert result.to_dict()["status"] == "completed"
    assert result.audit["provider_calls"] == 0
    assert result.audit["status"] == "completed"
    assert rig.factory_calls == 2 and rig.fresh.rollbacks == 1
    assert [value["status"] for _, value in rig.audits] == [
        "eligible", "commit_attempt_in_progress", "completed"]
    assert rig.events.index("gate") < rig.events.index("audit:eligible") < rig.events.index("publish")
    assert rig.events.index("publisher-savepoint:commit") < rig.events.index("outer:commit")
    assert rig.outer.commits == 1 and rig.publishers[0].commits == 1
    assert (rig.project.metadata_json, recovery._row(rig.budget)) == before
    assert not rig.settings.output.base_dir.exists(), "Audit was an in-memory control-flow seam"


@pytest.mark.asyncio
async def test_run_forwards_exact_native_owner_without_creating_workflows(rig):
    owner = m.WorkflowRunModel(id=uuid4(), project_id=rig.project_id,
        workflow_type="export_only_recovery", requested_by="unit-operator", status="running")
    rig.rows[m.WorkflowRunModel] = [owner]
    before = deepcopy(recovery._row(owner))
    result = await run(rig, owner_workflow_run_id=owner.id)
    assert result.status is recovery.ExportOnlyStatus.COMPLETED
    assert rig.rows[m.WorkflowRunModel] == [owner]
    assert recovery._row(owner) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change,reason", [
    ("source_change", "source_changed_during_export"),
    ("financial_change", "financial_changed_during_export"),
    ("control_change", "control_changed_during_export"),
])
async def test_precommit_fences_roll_back_without_committing_outer_transaction(rig, change, reason):
    setattr(rig, change, True)
    result = await run(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert reason in result.reason
    assert rig.outer.commits == 0 and rig.outer.rollbacks == 1
    assert rig.audits[-1][1]["status"] == "rolled_back"
    assert "completed" not in [a["status"] for _, a in rig.audits]


@pytest.mark.asyncio
async def test_failed_postcommit_readback_reports_uncertain_outcome_without_republish(rig):
    rig.postcommit_complete = False
    result = await run(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert "native_postcommit_readback_failed" in result.reason
    assert result.audit["status"] == "committed_readback_failed"
    assert rig.outer.commits == 1 and len(rig.publishers) == 1


@pytest.mark.asyncio
async def test_commit_exception_is_distinct_from_known_commit_readback_failure(rig, monkeypatch):
    async def ambiguous_commit():
        raise OSError("SYNTHETIC connection lost during commit")
    monkeypatch.setattr(rig.outer, "commit", ambiguous_commit)
    result = await run(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert result.audit["status"] == "commit_outcome_requires_readback"
    assert rig.factory_calls == 1 and len(rig.publishers) == 1
    assert rig.outer.rollbacks == 1


@pytest.mark.asyncio
async def test_cancellation_retains_audit_rolls_back_and_propagates(rig, monkeypatch):
    from bestseller.services import book_closure
    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError("SYNTHETIC publisher cancellation")
    monkeypatch.setattr(book_closure, "settle_project_status_on_closure", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await run(rig)
    assert [audit["status"] for _, audit in rig.audits] == ["eligible", "rolled_back"]
    assert rig.audits[-1][1]["error_type"] == "CancelledError"
    assert rig.outer.commits == 0 and rig.outer.rollbacks == 1
    assert len(rig.publishers) == 1 and rig.factory_calls == 1


@pytest.mark.asyncio
async def test_unsafe_output_slug_is_rejected_before_audit_or_publisher(rig):
    rig.project.slug = "../outside-output"
    result = await run(rig)
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert "unsafe_output_slug" in result.reason
    assert_no_writes(rig)


def test_financial_snapshot_uses_mapped_metadata_and_decimal_without_alias_loss():
    run_row = m.LlmRunModel(id=uuid4(), metadata_json={"retained": {"charge": "unchanged"}})
    score = m.QualityScoreModel(id=uuid4(), score_overall=Decimal("0.8300"))
    snapshot = recovery._row(run_row)
    assert snapshot["metadata_json"] == run_row.metadata_json
    assert "metadata" not in snapshot
    assert '"0.8300"' in recovery._json(recovery._row(score))
    snapshot["metadata_json"]["retained"]["charge"] = "different"
    assert run_row.metadata_json["retained"]["charge"] == "unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["budget", "operation", "attempt", "native_run"])
async def test_financial_fingerprint_fences_all_existing_paid_evidence(rig, changed):
    operation = m.ExecutionOperationModel(key="paid", status="completed", result_json={"content": "retained"})
    attempt = m.ExecutionAttemptModel(id="attempt", operation_key="paid", status="completed", actual_tokens=31)
    native_run = m.LlmRunModel(id=uuid4(), project_id=rig.project_id, metadata_json={"origin": "original"})
    rig.rows.update({m.ExecutionOperationModel: [operation], m.ExecutionAttemptModel: [attempt],
                     m.LlmRunModel: [native_run]})
    baseline = await recovery._finance(rig.outer, rig.project, lock=False)
    assert await recovery._finance(rig.outer, rig.project, lock=False) == baseline
    if changed == "budget": rig.budget.calls_used += 1
    elif changed == "operation": operation.result_json = {"content": "changed"}
    elif changed == "attempt": attempt.actual_tokens += 1
    else: native_run.metadata_json = {"origin": "changed"}
    assert await recovery._finance(rig.outer, rig.project, lock=False) != baseline
    assert_no_writes(rig)


def test_project_source_preserves_canon_design_history_and_counters(rig):
    before = recovery._project_source(rig.project)
    rig.project.metadata_json = {**rig.project.metadata_json,
        "completion_export_path": "synthetic-export.md", "book_delivery": {"derived": True}}
    assert recovery._project_source(rig.project) == before
    for key in ("creation_canonical_snapshot_hash", "book_design_snapshot", "title_revision_history", "calls_used"):
        original = deepcopy(rig.project.metadata_json)
        rig.project.metadata_json = {**original, key: "altered"}
        assert recovery._project_source(rig.project) != before
        rig.project.metadata_json = original


@pytest.mark.asyncio
@pytest.mark.parametrize("defect,reason", [
    ("missing_score", "current_grade_missing_or_ambiguous"),
    ("ambiguous_score", "current_grade_missing_or_ambiguous"),
    ("foreign_report", "retained_pass_report_required"),
    ("body", "retained_grade_source_mismatch"),
    ("unverified", "retained_grade_unverified"),
    ("missing_run", "retained_grade_native_run_missing"),
    ("open_owner", "retained_grade_owner_not_closed"),
])
async def test_promoted_grade_guards_reject_missing_or_foreign_retained_evidence(rig, defect, reason):
    from bestseller.services.reviews import chapter_review_body_fingerprint
    draft = m.ChapterDraftVersionModel(id=rig.draft_id, project_id=rig.project_id,
        chapter_id=rig.chapter_id, content_md="SYNTHETIC unchanged source")
    score = m.QualityScoreModel(id=uuid4(), review_report_id=uuid4())
    report = m.ReviewReportModel(id=score.review_report_id, project_id=rig.project_id,
        verdict="pass", llm_run_id=uuid4(), structured_output={
            "draft_id": str(draft.id), "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
            "critic_evidence": {"status": "verified", "passed": True}})
    native_run = m.LlmRunModel(id=report.llm_run_id, workflow_run_id=uuid4())
    owner = m.WorkflowRunModel(id=native_run.workflow_run_id, status="completed")
    if defect == "foreign_report": report.project_id = uuid4()
    if defect == "body": report.structured_output = {**report.structured_output, "draft_id": str(uuid4())}
    if defect == "unverified": report.structured_output["critic_evidence"]["status"] = "unknown"
    if defect == "open_owner": owner.status = "running"
    class GradeReads:
        async def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity is m.ChapterDraftVersionModel: return draft
            if entity is m.ReviewReportModel:
                # Assert the actual SQL carries isolation, then simulate its
                # no-row result for a foreign report. No native grade claimed.
                criteria = {c.left.key: c.right.value for c in statement._where_criteria}
                assert criteria["project_id"] == rig.project_id
                assert criteria["target_id"] == rig.chapter_id and criteria["target_type"] == "chapter"
                return report if report.project_id == criteria["project_id"] else None
            if entity is m.LlmRunModel: return None if defect == "missing_run" else native_run
            if entity is m.WorkflowRunModel: return owner
            raise AssertionError(entity)
        async def scalars(self, statement):
            assert statement.column_descriptions[0]["entity"] is m.QualityScoreModel
            return [] if defect == "missing_score" else [score, score] if defect == "ambiguous_score" else [score]
    source = {"chapter_id": str(rig.chapter_id), "draft_id": str(rig.draft_id), "adoption_state": "promoted"}
    with pytest.raises(ValueError, match=reason):
        await recovery._grades(GradeReads(), rig.project, [source])
    assert_no_writes(rig)


@pytest.mark.asyncio
async def test_debt_grade_path_requires_native_reader_receipt_and_does_not_rejudge(rig):
    class NoReads:
        def __getattr__(self, name):
            raise AssertionError("Accepted debt must use the reader's existing verified receipt")
    source = {"lineage": {"adoption_state": "accepted_with_debt"}}
    with pytest.raises(ValueError, match="adopted_debt_receipt_missing"):
        await recovery._grades(NoReads(), rig.project, [source])
    source["lineage"]["adoption_receipt"] = {"fixture": "existing-native-reader-seam"}
    result = await recovery._grades(NoReads(), rig.project, [source])
    assert result == [source["lineage"]["adoption_receipt"]]
    result[0]["fixture"] = "changed"
    assert source["lineage"]["adoption_receipt"]["fixture"] == "existing-native-reader-seam"


def attached(session, row):
    """Install known persisted ORM state without connecting or flushing."""
    make_transient_to_detached(row)
    return session.merge(row, load=False)


def dispatch_guard(session):
    session.dispatch.before_flush(session, None, None)


def test_native_annotation_is_suppressed_but_old_chapter_metadata_is_preserved():
    cid = uuid4(); old = {"history": [{"outcome": "failed"}], "terminal_quality_gate_content_hash": "old"}
    with Session() as session:
        chapter = attached(session, m.ChapterModel(id=cid, metadata_json=deepcopy(old), title="UNCHANGED"))
        recovery._protect_chapters(NS(sync_session=session), [{"chapter_id": str(cid), "body_sha256": "a" * 64}])
        chapter.metadata_json = {**chapter.metadata_json, "terminal_quality_gate_content_hash": "a" * 64}
        dispatch_guard(session)
        assert chapter.metadata_json == old
        assert chapter.title == "UNCHANGED"
        assert not session.is_modified(chapter, include_collections=True)


@pytest.mark.parametrize("defect", ["wrong_hash", "extra_annotation", "chapter_title", "draft_body", "snapshot_facts",
                                    "new_chapter", "new_draft", "new_snapshot", "deleted_draft", "foreign_null_hash"])
def test_source_guard_rejects_every_write_except_exact_known_native_annotation(defect):
    cid = uuid4()
    with Session() as session:
        recovery._protect_chapters(NS(sync_session=session), [{"chapter_id": str(cid), "body_sha256": "a" * 64}])
        if defect in {"new_chapter", "new_draft", "new_snapshot"}:
            model = {"new_chapter": m.ChapterModel, "new_draft": m.ChapterDraftVersionModel,
                     "new_snapshot": m.ChapterStateSnapshotModel}[defect]
            session.add(model(id=uuid4()))
        elif defect == "draft_body":
            row = attached(session, m.ChapterDraftVersionModel(id=uuid4(), content_md="original"))
            row.content_md = "rewritten"
        elif defect == "deleted_draft":
            row = attached(session, m.ChapterDraftVersionModel(id=uuid4(), content_md="original"))
            session.delete(row)
        elif defect == "snapshot_facts":
            row = attached(session, m.ChapterStateSnapshotModel(id=uuid4(), facts={"facts": []}))
            row.facts = {"facts": ["new source fact"]}
        else:
            row = attached(session, m.ChapterModel(id=uuid4() if defect == "foreign_null_hash" else cid,
                title="original", metadata_json={"history": "preserved"}))
            if defect == "chapter_title": row.title = "rewritten"
            elif defect == "foreign_null_hash":
                row.metadata_json = {**row.metadata_json, "terminal_quality_gate_content_hash": None}
            else:
                row.metadata_json = {**row.metadata_json, "terminal_quality_gate_content_hash":
                    "wrong" if defect == "wrong_hash" else "a" * 64}
                if defect == "extra_annotation": row.metadata_json = {**row.metadata_json, "other": "changed"}
        with pytest.raises(ValueError, match="export_attempted_(source|chapter)_write"):
            dispatch_guard(session)

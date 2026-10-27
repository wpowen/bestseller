"""Same pending slot policy rebind with real SQLite and actual semantic fences."""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_waiting_review_policy_source import db as db
from test_waiting_review_policy_source import semantic_db as semantic_db
from test_waiting_review_policy_source import waiting as waiting

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services import llm, pipelines
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_pending_review_policy_recovery import (
    HISTORY,
    _operation_key,
    _primary_versions,
    apply_pending_review_policy_recovery,
    validate_pending_review_policy_recovery,
)
from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
from bestseller.services.commercial_protocol_transition_runtime import _row

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def pending(waiting):
    r = waiting
    r.prior_owner = r.owner
    r.prior_owner.current_step = "chapter_adoption_required"
    r.old_policy = hashlib.sha256(b"actual prior reviewer source snapshot").hexdigest()
    r.cycle_id = pipelines._chapter_critic_cycle_id(
        r.project.id, r.chapter.id, r.fingerprint["digest"], r.old_policy
    )
    r.namespace = f"chapter-critic:{r.cycle_id}:1"
    owner = WorkflowRunModel(
        project_id=r.project.id,
        scope_type="chapter",
        scope_id=r.chapter.id,
        workflow_type="chapter_pipeline",
        status="failed",
        current_step="review_chapter_v1",
        error_message="workflow_failed",
    )
    r.session.add(owner)
    await r.session.flush()
    owner.metadata_json = {
        "execution_scope_id": r.scope,
        "review_recovery_source": {
            "draft_id": str(r.draft.id),
            "body_hash": r.fingerprint["digest"],
        },
        "retained_paid_history": ["do not replace"],
        "execution_failure_receipts": [
            {
                "workflow_id": str(owner.id),
                "project_id": str(r.project.id),
                "workflow_type": "chapter_pipeline",
                "step": owner.current_step,
                "code": "workflow_failed",
                "recoverable": True,
                "error_type": "ValueError",
            }
        ],
    }
    cycle = {
        "schema_version": "chapter-critic-cycle.v1",
        "cycle_id": r.cycle_id,
        "source_draft_id": str(r.draft.id),
        "source_body_hash": r.fingerprint["digest"],
        "policy_source_hash": r.old_policy,
        "semantic_dependencies_sha256": r.semantic,
        "review_workflow_run_id": str(owner.id),
        "state": "running",
        "attempts": [{"attempt": 1, "state": "running", "operation_namespace": r.namespace}],
    }
    recovery = {
        "cycle_id": r.cycle_id,
        "state": "running",
        "execution_scope_id": r.scope,
        "source_draft_id": str(r.draft.id),
        "source_body_hash": r.fingerprint["digest"],
        "policy_source_hash": r.old_policy,
        "attempts_base": 5,
        "previous_workflow_run_id": str(r.prior_owner.id),
        "policy_revision": "original policy migration",
        "reason": "Retain prior source",
        "quarantined_rewrite_task_ids": [],
    }
    r.prior_owner.metadata_json = {
        **r.prior_owner.metadata_json,
        "review_recovery_policy": {**deepcopy(recovery), "state": "prepared"},
    }
    r.chapter.metadata_json = {
        **r.chapter.metadata_json,
        "chapter_critic_cycles": {
            **r.chapter.metadata_json["chapter_critic_cycles"],
            r.cycle_id: cycle,
        },
        "chapter_review_active_cycle": deepcopy(recovery),
        "chapter_review_recovery_cycles": [deepcopy(recovery)],
    }
    r.owner = owner
    # A settled semantic judge is allowed; its original route is immutable.
    metadata = {
        "paid_call_origin": {
            "schema": "paid-call-origin.v1",
            "status": "recorded",
            "project_id": str(r.project.id),
            "workflow_run_id": str(owner.id),
            "step_run_id": None,
            "logical_role": "critic",
            "prompt_template": "fanqie_long_semantic_review",
            "prompt_version": "v1",
            "prompt_hash": r.operation.input_hash,
        },
        "execution_receipt": {
            "scope_id": r.scope,
            "operation_key": r.operation.key,
            "attempts": [{"attempt_id": r.physical.id, "status": "completed"}],
        },
    }
    r.operation.result_json = llm.LLMCompletionResult(
        content="retained semantic evidence",
        provider="openai",
        model_name="fixture",
        metadata=metadata,
    ).model_dump(mode="json")
    await r.session.commit()
    for row in list(r.session.identity_map.values()):
        if inspect(row).persistent:
            await r.session.refresh(row)
    return r


def args(r):
    return {
        "settings": r.settings,
        "project": r.project,
        "chapter": r.chapter,
        "draft": r.draft,
        "previous": r.owner,
        "execution_scope_id": r.scope,
        "current_policy_hash": pipelines.chapter_review_recovery_source_hash(),
    }


async def validate(r):
    for row in list(r.session.identity_map.values()):
        if inspect(row).persistent and row not in r.session.dirty:
            await r.session.refresh(row)
    rows = list(r.session.identity_map.values())
    before = {id(row): _row(row) for row in rows}
    writes, locks = [], []

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    def orm(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append(state.statement)

    event.listen(r.session.bind.sync_connection, "before_cursor_execute", sql)
    event.listen(r.session.sync_session, "do_orm_execute", orm)
    try:
        return await validate_pending_review_policy_recovery(r.session, **args(r))
    finally:
        event.remove(r.session.bind.sync_connection, "before_cursor_execute", sql)
        event.remove(r.session.sync_session, "do_orm_execute", orm)
        assert not writes and not locks
        assert before == {id(row): _row(row) for row in rows}
        assert not r.session.new and not r.session.dirty and not r.session.deleted


async def apply(r, witness):
    return await apply_pending_review_policy_recovery(
        r.session,
        **args(r),
        witness=witness,
        actor="explicit-human-recovery",
        reason="Preserve the pending logical slot after source guard repair.",
    )


async def test_readonly_origin_proof_preserves_pending_and_prior_waiting_records(pending):
    r = pending
    assert "critic_cycle_id" not in r.owner.metadata_json
    with pytest.raises(ValueError, match="policy_changed_requires_explicit_recovery"):
        await validate_retained_review_before_processing(r.session, r.settings, r.chapter)
    proof = await validate(r)
    assert proof == await validate(r)
    assert proof["cycle_id"] == r.cycle_id and proof["operation_namespace"] == r.namespace
    assert proof["attempt"] == 1 and proof["previous_policy_source_hash"] == r.old_policy
    assert proof["policy_rebind_only"] is True
    assert all(
        proof[k] is False
        for k in (
            "grade_reuse_allowed",
            "adoption_allowed",
            "new_credit_allowed",
            "dispatch_allowed",
            "attempt_reset_allowed",
            "new_cycle_allowed",
        )
    )
    assert r.owner.status == "failed" and r.report.verdict == "unknown"


async def test_pending_owner_with_explicit_matching_cycle_remains_compatible(pending):
    r = pending
    r.owner.metadata_json = {**r.owner.metadata_json, "critic_cycle_id": r.cycle_id}
    await r.session.commit()
    assert (await validate(r))["cycle_id"] == r.cycle_id


@pytest.mark.parametrize("recorded", [None, "f" * 64])
async def test_pending_owner_with_present_conflicting_cycle_is_rejected(pending, recorded):
    r = pending
    r.owner.metadata_json = {**r.owner.metadata_json, "critic_cycle_id": recorded}
    await r.session.commit()
    with pytest.raises(ValueError, match="chapter_pending_review_policy_pending_cycle_binding"):
        await validate(r)


@pytest.mark.parametrize("missing", [True, False])
async def test_prior_waiting_owner_still_requires_exact_cycle_binding(pending, missing):
    r = pending
    metadata = deepcopy(r.prior_owner.metadata_json)
    if missing:
        metadata.pop("critic_cycle_id")
    else:
        metadata["critic_cycle_id"] = "f" * 64
    r.prior_owner.metadata_json = metadata
    await r.session.commit()
    with pytest.raises(ValueError, match="chapter_pending_review_policy_prior_owner"):
        await validate(r)


async def test_existing_failed_origin_helper_remains_strict(pending):
    from bestseller.services.chapter_failed_review_recovery import (
        validate_failed_review_policy_source,
    )

    with pytest.raises(ValueError, match="chapter_failed_review_recovery_"):
        await validate_failed_review_policy_source(pending.session, **args(pending))


async def test_native_review_retains_original_owner_attempt_and_operation_namespace(
    pending, monkeypatch
):
    r = pending
    witness = await validate(r)
    await apply(r, witness)
    observed = []

    class ReachedOriginalSlot(BaseException):
        pass

    async def original_slot(*args, **kwargs):
        observed.append((llm._semantic_operation_namespace.get(), kwargs["workflow_run_id"]))
        raise ReachedOriginalSlot

    monkeypatch.setattr(pipelines, "review_chapter_draft", original_slot)
    with bind_execution_scope(r.scope), pytest.raises(ReachedOriginalSlot):
        await pipelines._review_chapter_with_evidence_retry(
            r.session,
            r.settings,
            r.project.slug,
            r.chapter.chapter_number,
            workflow=r.owner,
            draft=r.draft,
            cycle_id=r.cycle_id,
            source_binding={"draft_id": str(r.draft.id), "body_hash": r.fingerprint["digest"]},
        )
    assert observed == [((r.namespace, 0), r.owner.id)]
    assert (
        r.chapter.metadata_json["chapter_critic_cycles"][r.cycle_id]["attempts"]
        == witness["before_records"]["critic"]["attempts"]
    )


@pytest.mark.parametrize("initial_state", ["prepared", "running"])
async def test_native_pre_report_exception_retains_durable_owner_and_running_cursor(
    pending, monkeypatch, initial_state
):
    r = pending
    metadata = deepcopy(r.chapter.metadata_json)
    cycle = metadata["chapter_critic_cycles"][r.cycle_id]
    cycle["state"] = cycle["attempts"][0]["state"] = initial_state
    r.chapter.metadata_json = metadata
    await r.session.commit()
    witness = await validate(r)
    await apply(r, witness)
    await r.session.commit()
    factory = async_sessionmaker(r.session.bind.engine, expire_on_commit=False)

    async def persisted():
        # A separate connection/session must observe the native checkpoint,
        # independently of the writer's identity map and any uncommitted state.
        async with factory() as reader:
            owner = await reader.get(WorkflowRunModel, r.owner.id)
            chapter = await reader.get(ChapterModel, r.chapter.id)
            reports = list(await reader.scalars(select(ReviewReportModel).where(
                ReviewReportModel.project_id == r.project.id,
                ReviewReportModel.target_id == r.chapter.id,
            )))
            qualities = list(await reader.scalars(select(QualityScoreModel).where(
                QualityScoreModel.project_id == r.project.id,
                QualityScoreModel.target_id == r.chapter.id,
            )))
            return (
                deepcopy(owner.metadata_json),
                deepcopy(chapter.metadata_json),
                sorted((_row(row) for row in reports), key=lambda row: str(row["id"])),
                sorted((_row(row) for row in qualities), key=lambda row: str(row["id"])),
            )

    before = await persisted()
    assert "critic_cycle_id" not in before[0]
    running = deepcopy(before[1])
    cycle = running["chapter_critic_cycles"][r.cycle_id]
    cycle["state"] = cycle["attempts"][0]["state"] = "running"
    observed = []

    class PreReportError(ValueError):
        pass

    async def reviewer(*args, **kwargs):
        snapshot = await persisted()
        observed.append((llm._semantic_operation_namespace.get(), kwargs["workflow_run_id"]))
        assert snapshot[0] == {**before[0], "critic_cycle_id": r.cycle_id}
        cycle = snapshot[1]["chapter_critic_cycles"][r.cycle_id]
        assert cycle["state"] == "running"
        assert cycle["review_workflow_run_id"] == str(r.owner.id)
        assert cycle["attempts"] == [
            {"attempt": 1, "state": "running", "operation_namespace": r.namespace}
        ]
        assert snapshot[1] == running
        assert snapshot[2:] == before[2:]
        raise PreReportError("reviewer failed before any report")

    monkeypatch.setattr(pipelines, "review_chapter_draft", reviewer)
    async with factory() as writer:
        owner = await writer.get(WorkflowRunModel, r.owner.id)
        draft = await writer.get(ChapterDraftVersionModel, r.draft.id)
        with (
            bind_execution_scope(r.scope),
            pytest.raises(PreReportError, match="before any report"),
        ):
            await pipelines._review_chapter_with_evidence_retry(
                writer,
                r.settings,
                r.project.slug,
                r.chapter.chapter_number,
                workflow=owner,
                draft=draft,
                cycle_id=r.cycle_id,
                source_binding={"draft_id": str(r.draft.id), "body_hash": r.fingerprint["digest"]},
            )
        await writer.rollback()
    assert observed == [((r.namespace, 0), r.owner.id)]
    after = await persisted()
    assert after[0] == {**before[0], "critic_cycle_id": r.cycle_id}
    assert after[1] == running
    assert after[2:] == before[2:]


@pytest.mark.parametrize(
    "waiting",
    [
        ("The Orchard Choice", "romance", "Mira signs the lease and chooses the shared orchard."),
        (
            "The Lower Airlock",
            "science-fiction",
            "The engineer seals the valve and chooses the lower airlock.",
        ),
    ],
    indirect=True,
)
async def test_apply_changes_only_bound_policy_and_audit_retains_native_namespace(pending):
    r = pending
    witness = await validate(r)
    before, owner = deepcopy(r.chapter.metadata_json), deepcopy(r.owner.metadata_json)
    paid = _row(r.budget), _row(r.operation), _row(r.physical), _row(r.report), _row(r.quality)
    semantic = await pipelines._chapter_critic_semantic_dependencies(
        r.session, r.settings, r.chapter
    )
    receipt = await apply(r, witness)
    assert r.session.in_transaction() and not r.session.dirty
    assert r.owner.metadata_json == owner and r.owner.status == "failed"
    changed = deepcopy(r.chapter.metadata_json)
    critic = changed["chapter_critic_cycles"][r.cycle_id]
    assert critic[HISTORY] == [receipt]
    assert (
        receipt["proof"]["before_records"]["critic"] == before["chapter_critic_cycles"][r.cycle_id]
    )
    critic.pop(HISTORY)
    critic["policy_source_hash"] = r.old_policy
    changed["chapter_review_active_cycle"]["policy_source_hash"] = r.old_policy
    changed["chapter_review_recovery_cycles"][0]["policy_source_hash"] = r.old_policy
    assert changed == before
    assert (
        _row(r.budget),
        _row(r.operation),
        _row(r.physical),
        _row(r.report),
        _row(r.quality),
    ) == paid
    assert (
        await pipelines._chapter_critic_semantic_dependencies(r.session, r.settings, r.chapter)
        == semantic
    )
    preflight = await validate_retained_review_before_processing(r.session, r.settings, r.chapter)
    assert (
        preflight["reuse_kind"] == "resume_pending_review" and preflight["cycle_id"] == r.cycle_id
    )
    assert (
        r.chapter.metadata_json["chapter_critic_cycles"][r.cycle_id]["attempts"]
        == before["chapter_critic_cycles"][r.cycle_id]["attempts"]
    )
    assert await apply(r, witness) == receipt
    assert len(r.chapter.metadata_json["chapter_critic_cycles"][r.cycle_id][HISTORY]) == 1
    with pytest.raises(ValueError, match="policy_already_current"):
        await validate(r)


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "policy",
        "semantic",
        "scope",
        "owner",
        "failure",
        "missing_failure",
        "report",
        "available",
        "pending_return",
        "namespace",
        "cycle",
        "other_cycle",
        "pause",
        "auth",
        "active",
        "unsettled",
        "physical",
        "orphan",
        "wrong_recovery",
        "allowance",
        "adoption",
    ],
)
async def test_invalid_origin_source_policy_and_financial_state_fail_closed(pending, change):
    r = pending
    cm, wm = deepcopy(r.chapter.metadata_json), deepcopy(r.owner.metadata_json)
    cycle = cm["chapter_critic_cycles"][r.cycle_id]
    if change == "body":
        r.draft.content_md += " Changed source."
    elif change == "policy":
        cycle["policy_source_hash"] = pipelines.chapter_review_recovery_source_hash()
    elif change == "semantic":
        r.settings.quality.thresholds.scene_min_score += 0.01
    elif change == "scope":
        wm["execution_scope_id"] = "other:scope"
    elif change == "owner":
        r.owner.current_step = "draft_chapter"
    elif change == "failure":
        wm["execution_failure_receipts"][-1]["recoverable"] = False
    elif change == "missing_failure":
        wm["execution_failure_receipts"] = []
    elif change == "report":
        r.report.structured_output = {**r.report.structured_output, "unexpected": True}
    elif change == "available":
        r.report.verdict = "rewrite"
    elif change == "pending_return":
        cycle["attempts"][-1]["received_report_id"] = str(r.report.id)
    elif change == "namespace":
        cycle["attempts"][-1]["operation_namespace"] += ":new"
    elif change == "cycle":
        cm["chapter_review_active_cycle"]["cycle_id"] = "f" * 64
    elif change == "other_cycle":
        cm["chapter_critic_cycles"]["f" * 64] = {**deepcopy(cycle), "cycle_id": "f" * 64}
    elif change == "pause":
        r.control.desired_state = "pause"
    elif change == "auth":
        r.budget.metadata_json = {}
    elif change == "active":
        r.session.add(
            WorkflowRunModel(
                project_id=r.project.id,
                scope_type="chapter",
                scope_id=r.chapter.id,
                workflow_type="chapter_pipeline",
                status="running",
            )
        )
    elif change == "unsettled":
        r.operation.status = "unknown"
    elif change == "physical":
        r.physical.status = "reserved"
    elif change == "orphan":
        r.physical.operation_key = "e" * 64
    elif change == "wrong_recovery":
        cm["chapter_review_recovery_cycles"].append(deepcopy(cm["chapter_review_active_cycle"]))
    elif change == "allowance":
        cm["chapter_review_recovery_cycles"][0]["attempts_base"] += 1
    else:
        r.draft.promotion_state = "promoted"
    r.chapter.metadata_json, r.owner.metadata_json = cm, wm
    await r.session.commit()
    with pytest.raises(ValueError):
        await validate(r)


@pytest.mark.parametrize(
    "kind", ["root_only", "origin_disguised", "run_only", "orphan_run", "opaque_root"]
)
async def test_paid_primary_or_orphan_is_rejected_without_any_report(pending, kind):
    r = pending
    if kind in {"run_only", "orphan_run"}:
        run = LlmRunModel(
            project_id=r.project.id,
            workflow_run_id=r.owner.id if kind == "run_only" else None,
            logical_role="critic",
            provider="fixture",
            model_name="fixture",
            prompt_template="chapter_review",
            metadata_json={
                "primary_review_body_sha256": hashlib.sha256(
                    r.draft.content_md.encode()
                ).hexdigest()
            },
        )
        r.session.add(run)
    else:
        h = hashlib.sha256(b"unreported primary input").hexdigest()
        version = next(iter(_primary_versions()))
        key = (
            _operation_key(r.scope, r.namespace, h, version) if kind != "opaque_root" else "d" * 64
        )
        root = ExecutionOperationModel(
            key=key,
            scope_id=r.scope,
            input_hash=h,
            status="completed",
            owner="existing",
            lease_until=datetime.now(UTC),
            result_json={},
        )
        actual = ExecutionAttemptModel(
            id="unreported-primary",
            scope_id=r.scope,
            operation_key=key,
            input_hash=h,
            model_name="fixture",
            status="completed",
            actual_tokens=10,
            reserved_tokens=100,
            created_at=datetime.now(UTC),
        )
        if kind == "origin_disguised":
            root.result_json = {
                "metadata": {
                    "paid_call_origin": {
                        "schema": "paid-call-origin.v1",
                        "status": "recorded",
                        "logical_role": "critic",
                        "prompt_template": "chapter_review",
                        "prompt_version": version,
                        "prompt_hash": h,
                        "workflow_run_id": str(r.prior_owner.id),
                    },
                    "execution_receipt": {"scope_id": r.scope, "operation_key": key},
                }
            }
        r.session.add_all([root, actual])
    await r.session.commit()
    with pytest.raises(ValueError):
        await validate(r)


@pytest.mark.parametrize("change", ["body", "control", "ledger", "metadata", "actor"])
async def test_apply_revalidates_exact_witness_and_never_writes_on_stale_input(pending, change):
    r = pending
    witness = await validate(r)
    if change == "body":
        r.draft.content_md += " Changed after proof."
    elif change == "control":
        r.control.command_serial += 1
    elif change == "ledger":
        r.budget.calls_used += 1
    elif change == "metadata":
        r.owner.metadata_json = {**r.owner.metadata_json, "changed": True}
    await r.session.commit()
    before = deepcopy(r.chapter.metadata_json)
    if change == "actor":
        with pytest.raises(ValueError, match="actor_reason"):
            await apply_pending_review_policy_recovery(
                r.session, **args(r), witness=witness, actor="", reason="Bound repair"
            )
    else:
        with pytest.raises(ValueError):
            await apply(r, witness)
    assert r.chapter.metadata_json == before
    assert not r.session.new and not r.session.dirty and not r.session.deleted


@pytest.mark.parametrize("kind", ["physical", "transport"])
async def test_cross_scope_financial_alias_is_not_omitted_from_namespace_proof(pending, kind):
    r = pending
    scope = "other:retained-financial-scope"
    r.session.add(
        ExecutionBudgetModel(
            scope_id=scope,
            status="active",
            max_calls=10,
            max_tokens=1000,
            calls_used=0,
            tokens_charged=0,
            deadline_at=datetime.now(UTC),
        )
    )
    await r.session.flush()
    if kind == "physical":
        r.physical.scope_id = scope
    else:
        r.session.add(
            ExecutionOperationModel(
                key=hashlib.sha256(
                    f"transport-result.v1\n{scope}\n{r.physical.id}".encode()
                ).hexdigest(),
                scope_id=scope,
                input_hash=r.operation.input_hash,
                status="transport_result",
                owner="unverified-alias",
                lease_until=datetime.now(UTC),
                result_json={
                    "schema": "transport-result.v1",
                    "operation_key": r.operation.key,
                    "attempt_id": r.physical.id,
                    "input_hash": r.operation.input_hash,
                    "result": deepcopy(r.operation.result_json),
                },
            )
        )
    await r.session.commit()
    with pytest.raises(ValueError):
        await validate(r)


async def test_apply_flushes_only_chapter_and_does_not_commit(pending, monkeypatch):
    r = pending
    witness = await validate(r)
    writes = []

    async def no_commit():
        raise AssertionError("caller owns commit")

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    monkeypatch.setattr(r.session, "commit", no_commit)
    event.listen(r.session.bind.sync_connection, "before_cursor_execute", sql)
    try:
        await apply(r, witness)
    finally:
        event.remove(r.session.bind.sync_connection, "before_cursor_execute", sql)
    assert len(writes) == 1 and writes[0].lstrip().lower().startswith("update chapters ")


async def test_idempotent_apply_rechecks_live_context_and_receipt_integrity(pending):
    r = pending
    witness = await validate(r)
    await apply(r, witness)
    changed = deepcopy(r.chapter.metadata_json)
    changed["chapter_critic_cycles"][r.cycle_id][HISTORY][-1]["reason"] = "unproven replacement"
    r.chapter.metadata_json = changed
    await r.session.flush()
    with pytest.raises(ValueError, match="receipt_digest"):
        await apply(r, witness)

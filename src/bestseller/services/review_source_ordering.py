"""Recorded source epochs and append-only disposition; never an old grade upgrade."""

from __future__ import annotations

# Native DB/JSON boundaries are checked explicitly below.
# ruff: noqa: ANN001,ANN002,ANN202,ANN401
from copy import deepcopy
import json
from uuid import UUID

from sqlalchemy import inspect, select

from bestseller.infra.db import models as db
from bestseller.services import recorded_review_sources as recorded

SOURCE_ORDERING_POLICY = "source-ordering.total-order.v1"
LEGACY_SCHEMA = "pre-f0-review-disposition.v1"
SCHEMA = "pre-f0-review-disposition.v2"
CODE = "pre_f0_source_unprovable"
HISTORY_KEY = "deterministic_audit_f0_review_supersessions"
POINTER_HISTORY_KEY = "deterministic_audit_f0_prior_active_pointers"
DENIALS = {
    "grade_granted": False,
    "new_credit_allowed": False,
    "dispatch_authorized": False,
    "adoption_authorized": False,
    "unknown_reconciled": False,
}


class ReviewSourceOrderingError(ValueError):
    def __init__(self, code, *, differences):
        self.differences = differences
        super().__init__("review_source_ordering:" + code)


def _equal(field, expected, actual, code="binding_changed"):
    differences = recorded._identity_differences(field, expected, actual)
    if differences:
        raise ReviewSourceOrderingError(code, differences=differences)


def _source_epoch(view):
    """Inspect only decoded original wire, never a caller/cycle epoch flag.

    A pre-F0 typed selection also hashes its raw context, whose omitted arrays
    cannot retrospectively acquire ordering/identity authority from a new DB read.
    Classification does not reconstruct, sort, or change any historical value.
    """
    epochs, paths = [], []
    for index, call in enumerate(view.calls):
        source = call.source
        if source is None:  # window: its paid identity is bound separately
            continue
        if isinstance(source, recorded.CommercialSources):
            selected = json.loads(source.text)["source_context"]
        else:
            _equal(f"calls[{index}].source.type", "dict", type(source).__name__)
            selected = source.get("current_source_channels", {})
        receipt = selected.get("source_selection_receipt") or {}
        _equal(
            f"calls[{index}].source_selection.policy",
            "typed-world-and-relevant-current-state.v1",
            receipt.get("policy"),
        )
        marker = receipt.get("source_ordering_policy")
        if marker is not None:
            _equal(f"calls[{index}].source_ordering_policy", SOURCE_ORDERING_POLICY, marker)
        epochs.append(marker)
        for key, value in (selected.get("story_bible") or {}).items():
            if (
                key
                in {
                    "relationships",
                    "deceased_characters",
                    "protected_characters",
                    "restricted_characters",
                }
                and isinstance(value, list)
                and value
            ):
                paths.append(f"calls[{index}].story_bible.{key}")
    _equal("recorded_source_count.positive", True, bool(epochs))
    _equal("recorded_source_epochs.consistent", 1, len(set(epochs)), code="mixed_source_epochs")
    return {
        "status": "recorded_post_f0" if epochs[0] else "not_reusable",
        "code": None if epochs[0] else CODE,
        "source_ordering_policy": epochs[0],
        "observed_array_paths": paths,
        "opaque_raw_context_binding": not bool(epochs[0]),
        **DENIALS,
    }


def _successor_id(*, project_id, chapter_id, draft_id, body_sha256, source_cycle_id, receipt_schema=SCHEMA):
    # A version/actor/timestamp change cannot allocate another successor.
    return recorded._hash(
        {
            "schema": receipt_schema,
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "draft_id": str(draft_id),
            "body_sha256": body_sha256,
            "source_cycle_id": source_cycle_id,
            "source_ordering_policy": SOURCE_ORDERING_POLICY,
        }
    )


def _call_receipts(view):
    return [
        {
            "llm_run_id": str(call.llm_run_id),
            "operation_key": call.operation_key,
            "attempt_id": call.original_attempt_id,
            "transport_operation_key": call.transport_operation_key,
            "effective_input_sha256": call.effective_input_sha256,
            "request_sha256": call.request_sha256,
            "response_sha256": call.response_sha256,
        }
        for call in view.calls
    ]


async def read_review_reuse_decision(session, settings, *, project_id, chapter_id, draft_id):
    """Zero-write classification; original paid files and native rows must verify."""
    view = await recorded.read_recorded_review_sources(
        session,
        settings,
        project_id=project_id,
        chapter_id=chapter_id,
        draft_id=draft_id,
    )
    return {
        "schema": SCHEMA,
        **_source_epoch(view),
        "project_id": str(project_id),
        "chapter_id": str(chapter_id),
        "draft_id": str(draft_id),
        "source_cycle_id": view.cycle_id,
        "review_report_id": str(view.report_id),
        "recorded_calls": _call_receipts(view),
        "provider_calls": 0,
        "historical_input_custody_verified": True,
        "issued_grade_reconstruction_asserted": False,
    }


def _append_supersession(metadata, receipt):
    """Append a separately bound closure; old cycle/task/once objects stay intact."""
    result = deepcopy(metadata)
    history = result.setdefault(HISTORY_KEY, [])
    _equal("supersessions.type", "list", type(history).__name__)
    existing = [
        item for item in history if item.get("source_cycle_id") == receipt["source_cycle_id"]
    ]
    if existing:
        _equal("supersession.once", [receipt], existing)
        return result
    history.append(deepcopy(receipt))
    previous = result.get("chapter_review_active_cycle") or {}
    result.setdefault(POINTER_HISTORY_KEY, {})[receipt["receipt_sha256"]] = deepcopy(previous)
    baseline = previous.get("attempts_base", 0)
    _equal("active.attempts_base.type", "int", type(baseline).__name__)
    _equal("active.attempts_base.nonnegative", True, baseline >= 0)
    result["chapter_review_active_cycle"] = {
        "cycle_id": receipt["successor_cycle_id"],
        "state": "prepared",
        "source_draft_id": receipt["draft_id"],
        "source_body_hash": receipt["body_sha256"],
        "f0_supersession_receipt_sha256": receipt["receipt_sha256"],
        # Preserve the previous baseline; supersession never resets a counter.
        "attempts_base": baseline,
    }
    return result


async def _receipt_for_original(session, settings, *, project_id, chapter_id, draft_id, cycle_id, receipt_schema=SCHEMA):
    from bestseller.services import pipelines

    _equal("supersession.known_schema", True, type(receipt_schema) is str and receipt_schema in {LEGACY_SCHEMA, SCHEMA})
    view = await recorded.read_archived_review_sources(
        session,
        settings,
        project_id=project_id,
        chapter_id=chapter_id,
        draft_id=draft_id,
        cycle_id=cycle_id,
    )
    classification = _source_epoch(view)
    if classification["status"] != "not_reusable":
        return None
    reader = recorded._Reader(session)
    chapter = await reader.get(db.ChapterModel, chapter_id)
    draft = await reader.get(db.ChapterDraftVersionModel, draft_id)
    cycle = chapter.metadata_json["chapter_critic_cycles"][cycle_id]
    _, report, quality, task = await pipelines._retained_chapter_critic_result(
        reader, draft, cycle["attempts"][-1]
    )
    owner = await reader.get(db.WorkflowRunModel, UUID(cycle["review_workflow_run_id"]))
    _equal(
        "original_owner.terminal", True, owner.status in {"completed", "machine_blocked", "failed"}
    )
    receipt = {
        "schema": receipt_schema,
        "status": "superseded",
        "code": CODE,
        "project_id": str(project_id),
        "chapter_id": str(chapter_id),
        "draft_id": str(draft_id),
        "body_sha256": cycle["source_body_hash"],
        "source_cycle_id": cycle_id,
        "source_cycle_sha256": recorded._hash(cycle),
        "review_report_id": str(report.id),
        "review_report_sha256": _row_hash(db.ReviewReportModel, report),
        "quality_score_id": str(quality.id),
        "quality_score_sha256": _row_hash(db.QualityScoreModel, quality, excluded={"is_current"}),
        "rewrite_task_id": str(task.id) if task else None,
        "rewrite_task_sha256": _row_hash(db.RewriteTaskModel, task) if task else None,
        "recorded_calls": _call_receipts(view),
        "successor_cycle_id": _successor_id(
            project_id=project_id,
            chapter_id=chapter_id,
            draft_id=draft_id,
            body_sha256=cycle["source_body_hash"],
            source_cycle_id=cycle_id,
            receipt_schema=receipt_schema,
        ),
        "provider_calls": 0,
        "new_attempts": 0,
        **DENIALS,
    }
    if receipt_schema == SCHEMA:
        receipt["commercial_cohort"] = deepcopy(view.commercial_cohort)
    receipt["receipt_sha256"] = recorded._hash(receipt)
    return receipt


def _row_hash(model, row, *, excluded=frozenset()):
    values = {
        c.key: deepcopy(getattr(row, c.key))
        for c in inspect(model).column_attrs
        if c.key not in excluded
    }
    return recorded._hash(
        json.loads(json.dumps(values, default=str, ensure_ascii=False, allow_nan=False))
    )


async def _open_f0_ledger(session, scope, *, lock):
    from bestseller.services.waiting_chapter_review_resume import _ledger

    try:
        return await _ledger(session, scope, lock=lock)
    except ValueError as exc:
        if str(exc) not in {
            "waiting_chapter_review_resume_scope_not_open",
            "waiting_chapter_review_resume_unsettled_operations",
            "waiting_chapter_review_resume_unsettled_attempts",
        }:
            raise
        from bestseller.services.budget import ExecutionBudgetError, execution_scope_block_code

        budget = await session.get(db.ExecutionBudgetModel, scope, populate_existing=True)
        code = (
            (execution_scope_block_code(budget) or "execution_result_unknown")
            if budget
            else "execution_ledger_unavailable"
        )
        error = ExecutionBudgetError(code, scope)
        error.differences = recorded._identity_differences(
            "supersession.financial_admission", "closed operations and open budget", str(exc)
        )
        raise error from exc


async def _prepare_supersession_locked(session, settings, *, project, chapter, draft, cycle_id):
    """Native prepare holds Control/Project/Chapter/Draft locks, then budget.

    No report, task, cycle, financial row or owner is rewritten. Caller commits.
    The receipt opens no paid capacity; ordinary budget admission still owns it.
    """
    from bestseller.services.retry_ledger import project_execution_scope
    from bestseller.services.waiting_chapter_review_resume import _ledger

    # Unknown/unsettled operations remain blocked by the native financial guard.
    scope = project_execution_scope(project)
    before = await _open_f0_ledger(session, scope, lock=True)
    receipt = await _receipt_for_original(
        session,
        settings,
        project_id=project.id,
        chapter_id=chapter.id,
        draft_id=draft.id,
        cycle_id=cycle_id,
        receipt_schema=next((r.get("schema") for r in (chapter.metadata_json or {}).get(HISTORY_KEY, [])
                             if r.get("source_cycle_id") == cycle_id), SCHEMA),
    )
    if receipt is None:
        return None
    metadata = deepcopy(chapter.metadata_json or {})
    revised = _append_supersession(metadata, receipt)
    if revised != metadata:
        chapter.metadata_json = revised
        await session.flush()
    _equal(
        "supersession.financial_rows",
        before,
        await _ledger(session, project_execution_scope(project), lock=True),
    )
    return receipt


async def read_fresh_review_preflight(session, settings, *, chapter, draft):
    """Prove a native successor pointer, without borrowing any old grade."""
    from bestseller.services import pipelines

    metadata = chapter.metadata_json or {}
    active = metadata.get("chapter_review_active_cycle") or {}
    digest = active.get("f0_supersession_receipt_sha256")
    if digest is None:
        return None
    if active.get("cycle_id") not in (metadata.get("chapter_critic_cycles") or {}):
        from bestseller.services.feedback import validate_adopted_knowledge_source

        _, rejection = await validate_adopted_knowledge_source(
            session,
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            source_chapter_draft_version_id=draft.id,
            chapter_md=draft.content_md,
            lock=False,
        )
        _equal(
            "fresh_review.adoption_status",
            "source_not_adopted",
            rejection,
            code="source_adoption_changed",
        )
    matches = [r for r in metadata.get(HISTORY_KEY, []) if r.get("receipt_sha256") == digest]
    _equal("supersession.match_count", 1, len(matches))
    old = matches[0]
    expected = await _receipt_for_original(
        session,
        settings,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        draft_id=draft.id,
        cycle_id=old["source_cycle_id"],
        receipt_schema=old.get("schema"),
    )
    _equal("supersession.receipt", expected, old)
    _equal("active.successor_cycle_id", old["successor_cycle_id"], active.get("cycle_id"))
    _equal("active.source_draft_id", str(draft.id), active.get("source_draft_id"))
    _equal("active.source_body_hash", old["body_sha256"], active.get("source_body_hash"))
    if active["cycle_id"] in (metadata.get("chapter_critic_cycles") or {}):
        return None  # Existing new cycle still undergoes the normal full preflight.
    return {
        "schema": SCHEMA,
        "reuse_kind": "fresh_review_required",
        "code": CODE,
        "project_id": str(chapter.project_id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "body_sha256": old["body_sha256"],
        "cycle_id": active["cycle_id"],
        "policy_source_hash": pipelines.chapter_review_recovery_source_hash(),
        "report_id": None,
        "quality_score_id": None,
        "rewrite_task_id": None,
        "provider_calls": 0,
        **DENIALS,
    }


async def prepare_fresh_commercial_cohort(
    session, settings, *, chapter, old_owner, old_journal, key, pointer,
    binding, workflow_run_id,
):
    """Advance only a re-proved native F0 successor; caller owns its checkpoint.

    The old cohort and all paid/Unknown rows remain immutable. The new cohort
    receives a once-bound namespace and still needs ordinary ledger admission.
    """
    reader = recorded._Reader(session)
    native = await reader.get(db.ChapterModel, chapter.id)
    metadata = native.metadata_json or {}
    active = metadata.get("chapter_review_active_cycle") or {}
    digest = active.get("f0_supersession_receipt_sha256")
    if digest is None:
        return None
    started = (old_journal or {}).get("f0_supersession_receipt_sha256") == digest
    _equal("commercial_successor.settings_present", True, settings is not None)
    history = metadata.get(HISTORY_KEY) or []
    receipts = [r for r in history if r.get("receipt_sha256") == digest]
    _equal("commercial_successor.receipt_count", 1, len(receipts))
    receipt = receipts[0]
    current = (metadata.get("chapter_critic_cycles") or {}).get(active.get("cycle_id")) or {}
    _equal("commercial_successor.cycle_id", receipt["successor_cycle_id"], current.get("cycle_id"))
    _equal("commercial_successor.owner", str(workflow_run_id), current.get("review_workflow_run_id"))
    _equal("commercial_successor.cycle_running", True, current.get("state") in ({"running", "completed"} if started else {"running"}))
    draft = await reader.get(db.ChapterDraftVersionModel, UUID(receipt["draft_id"]))
    _equal("commercial_successor.current_draft", True, draft is not None and draft.is_current)
    _equal("commercial_successor.project", str(native.project_id), binding.get("project_id"))
    _equal("commercial_successor.chapter", str(native.id), binding.get("chapter_id"))
    _equal("commercial_successor.draft", str(draft.id), binding.get("draft_id"))
    _equal("commercial_successor.body", recorded._sha(draft.content_md.encode()),
           binding.get("source_binding", {}).get("body_sha256"))
    control = await reader.get(db.BookProductionControlModel, native.project_id)
    _equal("commercial_successor.control_run", True, control is not None and control.desired_state == "run")
    _equal("commercial_successor.control_serial", True,
           type(control.command_serial) is int and control.command_serial > 0)
    from bestseller.services.retry_ledger import project_execution_scope
    from bestseller.services.waiting_chapter_review_resume import _ledger
    project = await reader.get(db.ProjectModel, native.project_id)
    scope = project_execution_scope(project)
    _equal("commercial_successor.scope", scope, binding.get("scope_id"))
    before = None if started else await _open_f0_ledger(session, scope, lock=False)
    expected = await _receipt_for_original(session, settings, project_id=native.project_id,
        chapter_id=native.id, draft_id=draft.id, cycle_id=receipt["source_cycle_id"],
        receipt_schema=receipt.get("schema"))
    _equal("commercial_successor.original_receipt", expected, receipt)
    descriptor = receipt.get("commercial_cohort")
    if receipt.get("schema") == LEGACY_SCHEMA:
        # Re-derive the new forward descriptor from actual paid custody. The
        # exact v1 receipt/hash/successor stays untouched and is still compared.
        original = await recorded.read_archived_review_sources(session, settings,
            project_id=native.project_id, chapter_id=native.id, draft_id=draft.id,
            cycle_id=receipt["source_cycle_id"])
        descriptor = original.commercial_cohort
    _equal("commercial_successor.descriptor_present", True, isinstance(descriptor, dict))
    if started:
        # Re-prove original custody even during parallel new sends. The common
        # paid reader checks the old completed roots, not an invented cache flag.
        consumed = [row for row in metadata.get("deterministic_audit_f0_commercial_successions", [])
                    if row.get("supersession_receipt_sha256") == digest]
        _equal("commercial_successor.consumed_count", 1, len(consumed))
        audit = consumed[0]
        _equal("commercial_successor.consumed_old", descriptor, audit.get("old_cohort"))
        _equal("commercial_successor.consumed_owner", str(workflow_run_id), audit.get("new_owner_id"))
        _equal("commercial_successor.active_owner", str(workflow_run_id), str(old_owner.id))
        _equal("commercial_successor.separate_owner", True, str(old_owner.id) != descriptor["workflow_run_id"])
        _equal("commercial_successor.consumed_binding", recorded._hash(binding), audit.get("new_binding_sha256"))
        _equal("commercial_successor.active_binding", binding, old_journal.get("binding"))
        _equal("commercial_successor.active_pointer", {"workflow_run_id":str(workflow_run_id),
            "binding_sha256":recorded._hash(binding)}, pointer)
        return None
    _equal("commercial_successor.old_owner", descriptor["workflow_run_id"], str(old_owner.id))
    _equal("commercial_successor.old_key", descriptor["key"], key)
    _equal("commercial_successor.old_journal", descriptor["journal_sha256"], recorded._hash(old_journal))
    _equal("commercial_successor.old_pointer", {"workflow_run_id": descriptor["workflow_run_id"],
        "binding_sha256": descriptor["binding_sha256"]}, pointer)
    _equal("commercial_successor.pointer_CAS", pointer,
           (metadata.get("commercial_component_cohorts") or {}).get(key))
    owners = list(await session.scalars(select(db.WorkflowRunModel).where(
        db.WorkflowRunModel.id.in_([old_owner.id, workflow_run_id]),
    ).order_by(db.WorkflowRunModel.id).with_for_update().execution_options(populate_existing=True)))
    by_id = {owner.id: owner for owner in owners}
    old = by_id.get(old_owner.id)
    new = by_id.get(workflow_run_id)
    _equal("commercial_successor.owner_rows", True, old is not None and new is not None and old.id != new.id)
    _equal("commercial_successor.old_journal_CAS", descriptor["journal_sha256"], recorded._hash(
        ((old.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)))
    _equal("commercial_successor.new_owner_scope", True,
        new.project_id == native.project_id and new.scope_type == "chapter" and new.scope_id == native.id)
    _equal("commercial_successor.new_owner_running", "running", new.status)
    _equal("commercial_successor.native_cycle", current["cycle_id"], (new.metadata_json or {}).get("critic_cycle_id"))
    journals = deepcopy((new.metadata_json or {}).get("commercial_component_cohorts") or {})
    _equal("commercial_successor.new_journal_absent", False, key in journals)
    audit_key = "deterministic_audit_f0_commercial_successions"
    audit = deepcopy(metadata.get(audit_key) or [])
    _equal("commercial_successor.not_previously_consumed", False,
        any(row.get("supersession_receipt_sha256") == digest for row in audit))
    journal = {"schema": "commercial-component-cohort.v1", "binding": deepcopy(binding),
        "historical": False, "slots": {}, "f0_supersession_receipt_sha256": digest,
        "original_cohort": deepcopy(descriptor)}
    journals[key] = journal
    # Caller stages its first slot after this flush. Keep the mutable staging
    # objects detached from the ORM attribute so its next assignment is dirty.
    new.metadata_json = deepcopy({
        **(new.metadata_json or {}), "commercial_component_cohorts": journals,
    })
    pointers = deepcopy(metadata.get("commercial_component_cohorts") or {})
    pointers[key] = {"workflow_run_id": str(new.id), "binding_sha256": recorded._hash(binding)}
    audit.append({"schema": "pre-f0-commercial-succession.v1", "supersession_receipt_sha256": digest,
        "source_cycle_id": receipt["source_cycle_id"], "successor_cycle_id": current["cycle_id"],
        "old_cohort": deepcopy(descriptor), "new_owner_id": str(new.id),
        "new_binding_sha256": recorded._hash(binding), **DENIALS})
    chapter.metadata_json = deepcopy({
        **metadata, "commercial_component_cohorts": pointers, audit_key: audit,
    })
    await session.flush()
    _equal("commercial_successor.financial_rows", before, await _ledger(session, scope, lock=False))
    return new, journals, journal


async def verify_fresh_commercial_paid_origin(session, *, journal, key, slot, entry, report):
    """A new F0 cohort cannot import a completed root from the retired namespace."""
    digest = journal.get("f0_supersession_receipt_sha256")
    if digest is None:
        return
    from bestseller.services.budget import execution_operation_id

    reader = recorded._Reader(session)
    run = await reader.get(db.LlmRunModel, UUID(report["llm_run_id"]))
    _equal("commercial_successor.paid_run_present", True, run is not None)
    scope = journal["binding"]["scope_id"]
    namespace = f"commercial-component:{key}:f0:{digest}:round:{entry['used_attempts']}"
    identity = execution_operation_id(namespace,
        subject=f"{run.logical_role}:{run.prompt_template}:{run.prompt_version}", revision=run.prompt_hash)
    expected = recorded._sha(f"{scope}\n{identity}:sample:{slot}".encode())
    receipt = (run.metadata_json or {}).get("execution_receipt") or {}
    _equal("commercial_successor.paid_namespace", expected, receipt.get("operation_key"))
    root = await reader.get(db.ExecutionOperationModel, expected)
    _equal("commercial_successor.paid_root", True, root is not None and root.status == "completed"
        and root.scope_id == scope and root.input_hash == run.prompt_hash)


async def archived_rewrite_task_ids(session, *, project_id, chapter_id=None, settings=None):
    """Exclude only native supersessions re-proved from original paid custody.

    Hiding a pending task changes readiness, so self-consistent metadata hashes
    are insufficient. Every consumer shares the same original receipt builder.
    No report, score or task is modified by this read.
    """
    reader = recorded._Reader(session)
    if chapter_id is None:
        chapters = await reader.scalars(
            select(db.ChapterModel)
            .where(db.ChapterModel.project_id == project_id)
            .order_by(db.ChapterModel.id)
        )
    else:
        chapter = await reader.get(db.ChapterModel, chapter_id)
        chapters = [chapter] if chapter is not None else []
    ids = set()
    for chapter in chapters:
        _equal("archived_tasks.project_id", str(project_id), str(chapter.project_id))
        metadata = chapter.metadata_json or {}
        history = metadata.get(HISTORY_KEY, [])
        _equal("archived_tasks.history_type", "list", type(history).__name__)
        if not history:
            continue
        if settings is None:
            # Match the normal application configuration loader. No app/server
            # startup or runtime profile mutation occurs during this read.
            from bestseller.settings import load_settings

            settings = load_settings()
        seen = set()
        for item in history:
            _equal("archived_tasks.receipt_type", "dict", type(item).__name__)
            cid = item.get("source_cycle_id")
            _equal("archived_tasks.duplicate_cycle", False, cid in seen)
            seen.add(cid)
            cycle = (metadata.get("chapter_critic_cycles") or {}).get(cid) or {}
            _equal("archived_tasks.cycle.cycle_id", cid, cycle.get("cycle_id"))
            _equal("archived_tasks.cycle.state", "completed", cycle.get("state"))
            expected = await _receipt_for_original(
                session,
                settings,
                project_id=project_id,
                chapter_id=chapter.id,
                draft_id=UUID(item["draft_id"]),
                cycle_id=cid,
                receipt_schema=item.get("schema"),
            )
            _equal("archived_tasks.native_receipt", expected, item)
            if item["rewrite_task_id"]:
                ids.add(UUID(item["rewrite_task_id"]))
    return ids

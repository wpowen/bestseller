"""Delegated editorial adoption of an existing paid title, no new LLM call.

The original native proof is validated under its ACTUAL adopted title first.
This is a separate selection workflow; native heuristic grades stay unchanged.
Task delegation is not a claim that a human selected the exact title. A soft
attraction veto is retained as open editorial_quality_debt, never a new PASS.
The caller owns transactions. No engine, CLI, dispatch, budget grant or commit
is created here. The selection-chain reader/mapper verifies both native nodes;
the caller must validate that wrapper before commit and read it afresh after.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from bestseller.services import native_publication_title as native
from bestseller.services.native_title_operator import title_operator as op
from bestseller.services.platform_title_workflow import build_platform_title_workflow

SCHEMA = "native-paid-title-selection.v1"
WORKFLOW_TYPE = "paid_title_candidate_selection"
LINEAGE_KEY = "title_paid_selection_lineage"
_LOADED_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
HISTORY_KEYS = ("title_revision_status", "title_workflow_primary", "title_workflow",
                "title_candidates", "writing_profile", "title_repair_lineage", LINEAGE_KEY)
ORIGINAL_TASK_DELEGATION = {
    "actor": "assistant", "original_task_id": op.TASK_ID,
    "authorization_ref": "current-chat/user:按计划修好原四章，不反复问;original-task:" + op.TASK_ID,
    "reason": "原四章修复委托内的编辑性选择：采用既有付费同源候选，保留吸引力软债，后续独立核验新题与正文承诺。",
}


class PaidTitleSelectionError(ValueError):
    """Safe failure codes only; no provider payload or DB exception text."""


def need(value, code):
    if not value:
        raise PaidTitleSelectionError(code)


def _source_sha():
    current = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    need(current == _LOADED_SHA, "selection_source_changed_restart_required")
    return current


def _same(a, b):
    return op.wire(a) == op.wire(b)


def prepare_paid_title_selection(origin_evidence, *, context, selected_title,
                                 delegation=None, project_lock_version,
                                 review_fence_sha256, current_metadata_sha256):
    """Pure source-bound plan, not permission to execute a caller's snapshot.

    Only the async entry below may use a plan for adoption: it rereads strict
    native assembly/census/control/paid files and compares this plan exactly.
    """
    try:
        return _prepare(origin_evidence, context, selected_title, delegation,
                        project_lock_version, review_fence_sha256, current_metadata_sha256)
    except PaidTitleSelectionError:
        raise
    except (native.PublicationTitleError, op.Stop) as error:
        raise PaidTitleSelectionError("origin_or_source:" + str(error)) from None
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        raise PaidTitleSelectionError("malformed_selection_inputs") from None


def _prepare(origin, context, selected, delegation, lock_version, fence, metadata_hash):
    # DO NOT relabel the old proof/current manifest to the requested title.
    receipt = native.validate(origin, **context)
    old_title = origin["project"]["title"]
    manifest = context["manifest"]
    need(context["title"] == old_title == manifest["project"]["title"], "origin_actual_adopted_title")
    need(context["project_id"] == op.PROJECT_ID, "project_identity")
    audit = origin["paid_return"]
    delegated = deepcopy(ORIGINAL_TASK_DELEGATION if delegation is None else delegation)
    raw, paid_plan = audit["completion"]["content"], audit["plan"]
    candidates = op.literal_candidates(raw)
    need(type(selected) is str and selected in candidates and selected != old_title,
         "new_title_must_be_whole_paid_candidate")
    need(raw.count(selected) == 1, "candidate_literal_ambiguous")
    need(not op.unsupported_title(selected), "unsupported_title_promise")
    # The original-task repair mandate permits routine editorial choice. This
    # is an attribution reference, not a newly invented exact-title approval.
    need(type(delegated) is dict and set(delegated) == {
        "actor", "original_task_id", "authorization_ref", "reason"}, "selection_delegation_shape")
    need(delegated["actor"] == "assistant" and delegated["original_task_id"] == op.TASK_ID,
         "selection_delegation_binding")
    need(all(type(delegated[k]) is str and bool(delegated[k].strip())
             for k in ("authorization_ref", "reason")), "original_task_delegation_reference")
    workflow = build_platform_title_workflow(
        {**paid_plan["profile"], "primary_title": selected},
        target_platform=paid_plan["profile"]["target_platform"],
    )
    evaluation = workflow["candidate_evaluations"][selected]
    checks = evaluation["checks"]
    # This entry handles the observed soft attraction veto only. No reject,
    # unreadability, story mismatch, platform mismatch or hard reject is waived.
    need(evaluation["decision"] in {"pass", "revise"}
         and set(checks) == {"reader_attraction", "story_transmission", "platform_fit", "title_quality"}
         and all(checks[k]["passed"] is True for k in ("story_transmission", "platform_fit", "title_quality"))
         and all(not c.get("hard_reject") for c in checks.values()), "non_attraction_native_check_failed")
    need(type(lock_version) is int and lock_version >= 0, "project_version")
    for digest in (fence, metadata_hash):
        need(type(digest) is str and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "source_digest")
    binding = {
        "project_id": op.PROJECT_ID, "original_task_id": op.TASK_ID, "execution_scope_id": op.SCOPE,
        "origin_evidence_sha256": op.fingerprint(origin), "origin_mapping_receipt": receipt,
        "origin_workflow_run_id": origin["workflow"]["id"], "origin_llm_run_id": origin["llm_run"]["id"],
        "origin_paid_audit_sha256": audit["audit_sha256"], "origin_operation_key": paid_plan["operation_key"],
        "paid_raw_sha256": op.sha(raw), "profile_sha256": op.fingerprint(paid_plan["profile"]),
        "manifest_before": deepcopy(manifest), "old_title": old_title, "selected_title": selected,
        "candidate_index": candidates.index(selected), "literal_start": raw.index(selected),
        "literal_end": raw.index(selected) + len(selected), "literal_index_unit": "python_unicode_codepoints",
        "project_lock_version": lock_version, "review_fence_sha256": fence,
        "project_metadata_sha256": metadata_hash, "service_source_sha256": _source_sha(),
    }
    debt = []
    if checks["reader_attraction"]["passed"] is not True:
        # Existing project convention: workflow.metadata.editorial_quality_debt
        # retains unresolved editorial grades (planner's finite-book path).
        # Chapter OverrideContract requires a future chapter; do not invent a
        # fifth chapter/payback window for a four-chapter publication title.
        debt = [{"schema": "paid-title-editorial-quality-debt.v1", "status": "open",
            "unresolved_codes": ["reader_attraction"], "overall_score": checks["reader_attraction"]["score"],
            "commercial_passed": False, "native_decision": evaluation["decision"],
            "source_native_evaluation_sha256": op.fingerprint(evaluation),
            "source_candidate_sha256": op.sha(selected), "source_paid_audit_sha256": audit["audit_sha256"],
            "rationale_type": "EDITORIAL_INTENT", "rationale_text": delegated["reason"],
            "actor": "assistant", "authorization_ref": delegated["authorization_ref"],
            "scope": "this_paid_candidate_selection_only",
            "due_event": "independent_title_and_body_promise_review_before_formal_export",
            "payback_plan": "独立核验新题可读承诺与成稿事实；保留原始revise与评分，不视为真人吸引力验收。",
            "human_attraction_acceptance": "not_performed", "new_title_call_authorized": False,
            "semantic_verdict_granted": False, "export_authority_granted": False}]
    identity = op.fingerprint({"schema": SCHEMA, "binding": binding, "delegation": delegated,
                               "editorial_quality_debt": debt,
                               "native_evaluation": evaluation})
    plan = {"schema": SCHEMA, "binding": binding, "delegation": delegated,
            "editorial_quality_debt": debt,
            "native_evaluation": deepcopy(evaluation),
            "workflow_run_id": str(uuid5(NAMESPACE_URL, "bestseller:" + SCHEMA + ":" + identity)),
            "adoption_basis": "delegated_editorial_selection_from_existing_paid_candidates",
            "provider_calls": 0, "budget_mutation": False, "semantic_verdict_granted": False,
            "whole_book_pass_granted": False, "export_authority_granted": False}
    plan["plan_sha256"] = op.fingerprint(plan)
    return plan


def selection_metadata(before, *, plan, origin_project):
    """Pure prospective native metadata. Preserve old lineage, debt/raw grades."""
    from types import SimpleNamespace
    from bestseller.services.planner import _apply_project_title_workflow_metadata

    need(op.fingerprint(before) == plan["binding"]["project_metadata_sha256"], "metadata_changed")
    binding = plan["binding"]
    need(origin_project["title"] == binding["old_title"]
         and origin_project["metadata_json"]["title_repair_lineage"]["workflow_run_id"] == binding["origin_workflow_run_id"],
         "origin_project_binding")
    history = deepcopy(before.get("title_revision_history", []))
    need(type(history) is list and LINEAGE_KEY not in before, "selection_already_exists_or_history_invalid")
    history.append({"schema": SCHEMA, "workflow_run_id": plan["workflow_run_id"],
        "previous_title": binding["old_title"], "plan_sha256": plan["plan_sha256"],
        "previous_native_metadata": {k: deepcopy(before[k]) for k in HISTORY_KEYS if k in before}})
    lineage = {
        "schema": SCHEMA, "workflow_run_id": plan["workflow_run_id"], "plan_sha256": plan["plan_sha256"],
        **{k: deepcopy(binding[k]) for k in (
            "project_id", "original_task_id", "execution_scope_id", "origin_workflow_run_id",
            "origin_llm_run_id", "origin_paid_audit_sha256", "origin_operation_key", "origin_evidence_sha256",
            "paid_raw_sha256", "manifest_before", "old_title", "selected_title", "candidate_index",
            "literal_start", "literal_end", "literal_index_unit")},
        "native_evaluation": deepcopy(plan["native_evaluation"]), "adoption_basis": plan["adoption_basis"],
        "delegation_sha256": op.fingerprint(plan["delegation"]),
        "editorial_quality_debt": deepcopy(plan["editorial_quality_debt"]),
        "new_provider_calls": 0, "fees_changed": False,
        "historical_promise_fulfillment_granted": False, "semantic_verdict_granted": False,
        "whole_book_pass_granted": False, "export_authority_granted": False,
    }
    record = {"title": binding["selected_title"], "source": SCHEMA,
              "selection_workflow_run_id": plan["workflow_run_id"],
              "origin_workflow_run_id": binding["origin_workflow_run_id"],
              "llm_run_id": binding["origin_llm_run_id"],
              "adoption_basis": plan["adoption_basis"],
              "title_evaluation": deepcopy(plan["native_evaluation"])}
    # Preserve the original paid workflow in history. This new workflow is an
    # delegated editorial record, not a new model-generated PASS pool.
    workflow = {"schema_version": SCHEMA, "candidate_policy": "existing_paid_literal_delegated_selection",
                "candidates": [deepcopy(record)], "candidate_evaluations": {
                    binding["selected_title"]: deepcopy(plan["native_evaluation"])},
                "platform_count": before.get("title_workflow", {}).get("platform_count"),
                "recommended_primary_title": deepcopy(record)}
    project = SimpleNamespace(metadata_json=deepcopy(before))
    _apply_project_title_workflow_metadata(project, metadata=before, title_record=record, title_workflow=workflow)
    metadata = {**project.metadata_json, "title_revision_history": history, LINEAGE_KEY: lineage,
                "title_revision_status": {
                    "schema": SCHEMA, "previous_title": binding["old_title"],
                    "adopted_title": binding["selected_title"], "quality_status": "delegated_selection_with_soft_debt"
                    if plan["editorial_quality_debt"] else "delegated_selection_native_checks_passed",
                    "title_evaluation": deepcopy(plan["native_evaluation"]),
                    "selection_workflow_run_id": plan["workflow_run_id"],
                    "origin_llm_run_id": binding["origin_llm_run_id"],
                    "semantic_verdict_granted": False}}
    need(metadata["title_repair_lineage"] == before["title_repair_lineage"], "original_lineage_changed")
    for key, value in before.items():
        if key not in HISTORY_KEYS and key != "title_revision_history":
            need(_same(metadata[key], value), "unrelated_metadata_changed")
    return metadata, lineage, record


async def _inspect(session, settings, *, selected_title, delegation, expected_manifest, lock):
    from sqlalchemy import select
    from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowStepRunModel
    from bestseller.services.fanqie_short_semantic_inputs import build_current_inputs
    from bestseller.services.fanqie_short_source_export import AdoptedAssembly
    from bestseller.services.fanqie_short_title_evidence import load_native_title_evidence
    from bestseller.services.native_title_operator.title_operator_native import NativeRuntime

    need(not session.new and not session.dirty and not session.deleted, "clean_session_required")
    runtime = NativeRuntime()
    runtime.settings = settings
    # Existing reader locks control/project/source first, budget last; it has no
    # new dispatch. Preserve original financial gateway without hashing scope
    # ledger/budget into a plan. Own selection WF does not exist before this.
    snapshot = await runtime._snapshot(session, lock=lock)
    op.check_snapshot(snapshot, expected_manifest, paid=True)
    project = await session.get(ProjectModel, UUID(op.PROJECT_ID))
    need(project is not None and project.metadata_json.get("execution_scope_id") == op.SCOPE, "current_project_scope")
    evidence = await load_native_title_evidence(session, settings, project=project)
    need(evidence is not None, "original_native_title_proof_required")
    if lock:
        # Historical paid attribution/step/root are immutable inputs too. The
        # snapshot already owns control -> project/source -> budget; acquire
        # the original paid row locks afterwards, then reread the actual proof.
        await session.scalar(select(LlmRunModel).where(
            LlmRunModel.id == UUID(evidence["llm_run"]["id"])
        ).execution_options(populate_existing=True).with_for_update())
        list(await session.scalars(select(WorkflowStepRunModel).where(
            WorkflowStepRunModel.workflow_run_id == UUID(evidence["workflow"]["id"])
        ).order_by(WorkflowStepRunModel.step_order).execution_options(populate_existing=True).with_for_update()))
        await runtime._ledger(session, evidence["paid_return"]["plan"], lock=True)
        evidence = await load_native_title_evidence(session, settings, project=project)
        need(evidence is not None, "original_native_title_proof_required")
    assembly = AdoptedAssembly(snapshot["full_text"], op.wire(snapshot["manifest"]))
    inputs = await build_current_inputs(session, project=project, assembly=assembly,
        workflow_run_id=evidence["workflow"]["id"], native_title_evidence=evidence)
    context = {"project_id": op.PROJECT_ID, "title": project.title,
               "full_text": assembly.full_text, "manifest": assembly.manifest,
               "approved_contract": inputs["approved_contract"]}
    plan = prepare_paid_title_selection(evidence, context=context, selected_title=selected_title,
        delegation=delegation, project_lock_version=project.lock_version,
        review_fence_sha256=snapshot["review_fence_sha256"],
        current_metadata_sha256=op.fingerprint(project.metadata_json))
    return plan, project, evidence


async def preflight_paid_title_selection(session, settings, *, selected_title, expected_manifest, delegation=None):
    """Explicit READ ONLY transaction required; no engine/session/commit created."""
    from sqlalchemy import text
    need(await session.scalar(text("SHOW transaction_read_only")) == "on", "readonly_transaction_required")
    plan, _, _ = await _inspect(session, settings, selected_title=selected_title,
        delegation=delegation, expected_manifest=expected_manifest, lock=False)
    return plan


async def adopt_paid_title_selection(session, settings, *, plan):
    """Stage one atomic title/WF/step change; caller must commit and read back.

    Do not invoke in production before the reviewed reader/mapper branch exists.
    No model, LlmRun, execution root/attempt, grant or counters are written.
    """
    from bestseller.infra.db.models import WorkflowRunModel, WorkflowStepRunModel

    need(plan.get("schema") == SCHEMA and plan["plan_sha256"] == op.fingerprint({
        k: v for k, v in plan.items() if k != "plan_sha256"}), "selection_plan_hash")
    fresh, project, origin = await _inspect(session, settings,
        selected_title=plan["binding"]["selected_title"], delegation=plan["delegation"],
        expected_manifest=plan["binding"]["manifest_before"], lock=True)
    need(_same(fresh, plan), "selection_source_or_delegation_changed")
    wf_id = UUID(plan["workflow_run_id"])
    need(await session.get(WorkflowRunModel, wf_id) is None, "selection_workflow_exists_do_not_repeat")
    metadata, lineage, record = selection_metadata(project.metadata_json, plan=plan, origin_project=origin["project"])
    # All validation completes before the first mutation. One caller-owned DB
    # transaction publishes all three rows, or rolls them back together.
    workflow = WorkflowRunModel(id=wf_id, project_id=project.id, workflow_type=WORKFLOW_TYPE,
        scope_type="project", scope_id=project.id, status="completed",
        current_step="paid_title_candidate_adopted", requested_by=plan["delegation"]["actor"],
        metadata_json={"schema": SCHEMA, "selection_plan": deepcopy(plan),
            "execution_scope_id": op.SCOPE, "original_task_id": op.TASK_ID,
            "origin_project_projection": deepcopy(origin["project"]), LINEAGE_KEY: deepcopy(lineage),
            "selection_metadata_before": deepcopy(project.metadata_json),
            "editorial_quality_debt": deepcopy(plan["editorial_quality_debt"])})
    step = WorkflowStepRunModel(id=uuid5(wf_id, "paid_title_candidate_selection"),
        workflow_run_id=wf_id, step_name="adopt_existing_paid_title_candidate", step_order=1,
        status="completed", input_ref={"plan_sha256": plan["plan_sha256"],
            "source_manifest": deepcopy(plan["binding"]["manifest_before"])},
        output_ref={LINEAGE_KEY: deepcopy(lineage), "title_record": deepcopy(record)})
    session.add(workflow)
    session.add(step)
    project.title = plan["binding"]["selected_title"]
    project.metadata_json = metadata
    await session.flush()
    return {"schema": SCHEMA, "status": "selection_staged_in_transaction",
            "workflow_run_id": str(wf_id), "lineage": lineage,
            "requires_commit_and_fresh_readback": True, "provider_calls": 0,
            "whole_book_pass_granted": False, "export_authority_granted": False}

"""Explicit one-target isolation of stopped zero-actual Unknown history.

Never attributes or reconciles old requests. Never changes budget, operation,
cohort identity, sample labels or retry allowance. No provider calls/commits.
Only the privileged operator path can persist a human-approved fixed baseline.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from bestseller.infra.db.models import (
    ProjectModel, ChapterModel, ChapterDraftVersionModel, ExecutionBudgetModel, ExecutionOperationModel,
    ExecutionAttemptModel, LlmRunModel, WorkflowRunModel,
)
from bestseller.services.budget import completion_authorization, execution_scope_block_code, execution_operation_id
from bestseller.services.commercial_protocol_transition import _digest, _sha
from bestseller.services.commercial_protocol_transition_runtime import _row, protocol_manifest
from bestseller.services.commercial_source_evidence import PROMPT_VERSION
from bestseller.services.operator_action_approval import require, require_operator_action_approval
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retry_ledger import project_execution_scope

SCHEMA = "exact-unknown-new-business.v1"
_SOURCE_KEYS = ("body_sha256", "semantic_source_sha256", "previous_sha256", "source_protocol_sha256")
_INFO = "exact_unknown_business_verified"


def isolation_policy_manifest():
    """Additional authority fence; does not rewrite the historical protocol."""
    base = Path(__file__).resolve().parent
    files = ("exact_unknown_business.py", "operator_action_approval.py",
             "budget.py", "execution_authorization.py", "prompt_source_policy.py",
             "commercial_review_transport.py", "lossless_source_encoding.py", "critic_evidence_gate.py",
             "commercial_review_context.py", "prompt_compiler.py", "word_targets.py",
             "retry_ledger.py", "../domain/llm_quality_judge.py")
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in files}


def _identity(key):
    return uuid5(NAMESPACE_URL, "bestseller:exact-unknown-business:" + key)


def approval_binding(plan):
    return {"project_id": plan["binding"]["project_id"], "target_id": plan["binding"]["chapter_id"],
        "plan_sha256": plan["plan_sha256"], "target_sha256": content_hash(plan["binding"]),
        "baseline_sha256": content_hash(plan["baseline"])}


async def _target(session, *, project_id, chapter_id, draft_id, scope, sources,
        evaluation_contract, current_input_hash, effective_critic, samples=3, lock=False):
    require(type(samples) is int and samples == 3 and _sha(current_input_hash), "exact_target_input")
    project = await session.get(ProjectModel, project_id)
    saved_scope = (project.metadata_json or {}).get("execution_scope_id") if project is not None else None
    require(project is not None and scope == (saved_scope or project_execution_scope(project)),
            "real_project_scope_required")
    chapter = await session.get(ChapterModel, chapter_id, populate_existing=True, with_for_update=lock)
    draft = await session.get(ChapterDraftVersionModel, draft_id, populate_existing=True, with_for_update=lock)
    require(chapter is not None and chapter.project_id == project_id and draft is not None
        and draft.project_id == project_id and draft.chapter_id == chapter_id and draft.is_current is True,
        "real_current_target_required")
    source = dict(sources.receipt)
    require(all(_sha(source.get(k)) for k in _SOURCE_KEYS)
        and source["body_sha256"] == hashlib.sha256(draft.content_md.encode()).hexdigest(), "target_body_source")
    budget = await session.get(ExecutionBudgetModel, scope)
    auth = completion_authorization(budget) if budget is not None else None
    require(auth is not None and not execution_scope_block_code(budget), "original_completion_authorization_required")
    require(effective_critic.get("model") in auth.get("models", [])
        and urlparse(effective_critic.get("api_base") or "").hostname in auth.get("endpoint_hosts", []),
        "provider_not_authorized")
    key = _digest({"project_id": str(project_id), "chapter_id": str(chapter_id), "body_sha256": source["body_sha256"]})
    binding = {"project_id": str(project_id), "chapter_id": str(chapter_id), "draft_id": str(draft_id),
        "scope": scope, "stable_cohort_key": key, "source_binding": {k: source[k] for k in _SOURCE_KEYS},
        "source_receipt_sha256": content_hash(source), "current_effective_input_hash": current_input_hash,
        "effective_critic_sha256": content_hash(effective_critic),
        "original_authorization_ref": auth.get("authorization_ref"),
        "target_protocol": {"prompt_version": PROMPT_VERSION, "source_manifest": protocol_manifest(),
            "isolation_policy_manifest": isolation_policy_manifest(),
            "evaluation_contract": deepcopy(dict(evaluation_contract))},
        "allowed_slot_labels": [f"chapter-quality.independent-v2:{i}" for i in range(3)]}
    return binding, budget, chapter


async def _inventory(session, scope):
    operations = [_row(o) for o in await session.scalars(select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id == scope))]
    attempts = [_row(a) for a in await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == scope))]
    runs = [_row(r) for r in await session.scalars(select(LlmRunModel).where(
        LlmRunModel.metadata_json["execution_receipt"]["scope_id"].as_string() == scope))]
    return operations, attempts, runs


async def _baseline(session, scope, root_keys, budget):
    from bestseller.services.commercial_ambiguity_retirement import _no_protocol_evidence
    from bestseller.services.unsent_reclaim_control import validate_unsent_reclaim_control
    require(isinstance(root_keys, list) and root_keys and len(set(root_keys)) == len(root_keys)
            and all(_sha(k) for k in root_keys), "exact_root_keys")
    operations, attempts, runs = await _inventory(session, scope)
    by_key = {o["key"]: o for o in operations}
    roots, controls = [], []
    for key in sorted(root_keys):
        row = by_key.get(key)
        require(row is not None and row["status"] == "stopped" and row["result_json"] is None,
                "stopped_unknown_root_required")
        _no_protocol_evidence(row)
        require(not any(a["operation_key"] == key for a in attempts), "zero_actual_required")
        # An unlinked original run with the same retained hash is contrary
        # evidence too; absence is never converted into never-sent evidence.
        linked = list(await session.scalars(select(LlmRunModel).where(LlmRunModel.prompt_hash == row["input_hash"])))
        require(not linked and not any((r.get("metadata_json") or {}).get("execution_receipt", {}).get("operation_key") == key for r in runs),
                "new_original_run_evidence")
        related = [o for o in operations if isinstance(o.get("result_json"), dict)
            and o["result_json"].get("operation_key") == key]
        for control in related:
            require(control["status"] == "unsent_reclaim_receipt", "physical_or_unverified_control_evidence")
            validate_unsent_reclaim_control(control, operations=operations, actual_attempts=attempts)
        roots.append(row)
        controls.extend(related)
    require(not any(o["status"] in {"pending", "unknown"} for o in operations), "inflight_baseline")
    b = _row(budget)
    return {"roots": roots, "controls": controls, "root_keys": sorted(root_keys),
        "operation_hashes": {o["key"]: content_hash(o) for o in operations},
        "attempt_hashes": {a["id"]: content_hash(a) for a in attempts},
        "run_hashes": {r["id"]: content_hash(r) for r in runs},
        "budget_fence": {k: b[k] for k in ("scope_id", "max_calls", "max_tokens", "deadline_at", "metadata_json")},
        "calls_used": b["calls_used"], "tokens_charged": b["tokens_charged"],
        "purpose": "Unknown", "ever_sent": "Unknown", "remote_outcome": "Unknown",
        "credit_released": False, "old_operation_replay_allowed": False}


async def prepare_exact_unknown_business(session, *, root_keys, **target):
    binding, budget, chapter = await _target(session, **target)
    require(binding["stable_cohort_key"] not in (chapter.metadata_json or {}).get("commercial_component_cohorts", {}),
            "existing_cohort_cannot_receive_new_credit")
    baseline = await _baseline(session, binding["scope"], root_keys, budget)
    require(all(r["input_hash"] != binding["current_effective_input_hash"] for r in baseline["roots"]),
            "current_input_may_already_be_spent")
    plan = {"schema": SCHEMA, "binding": binding, "baseline": baseline,
            "source_receipt": deepcopy(dict(target["sources"].receipt)),
            "effective_critic": deepcopy(dict(target["effective_critic"])),
            "different_old_request_asserted": False, "new_automatic_credit": False}
    plan["plan_sha256"] = content_hash(plan)
    return plan


async def persist_exact_unknown_business(session, *, expected_plan_sha256, approval_id=None, root_keys, **target):
    require(approval_id is not None, "approval_missing")
    binding, _, _ = await _target(session, **target, lock=True)
    rid = _identity(binding["stable_cohort_key"])
    prior = await session.get(WorkflowRunModel, rid)
    if prior is not None:
        plan = prior.metadata_json["plan"]
        require(plan["binding"] == binding and plan["plan_sha256"] == expected_plan_sha256,
                "existing_isolation_cannot_rebind")
        await require_operator_action_approval(session, approval_id=prior.metadata_json["approval_id"],
            purpose="exact_unknown_new_business", binding=approval_binding(plan), capability="isolate_unknown")
        return {"status": "already_persisted", "receipt_id": str(rid)}
    plan = await prepare_exact_unknown_business(session, root_keys=root_keys, **target)
    require(plan["plan_sha256"] == expected_plan_sha256, "isolation_plan_changed")
    await require_operator_action_approval(session, approval_id=approval_id,
        purpose="exact_unknown_new_business", binding=approval_binding(plan), capability="isolate_unknown")
    session.add(WorkflowRunModel(id=rid, project_id=UUID(binding["project_id"]),
        workflow_type="exact_unknown_new_business", scope_type="chapter", scope_id=UUID(binding["chapter_id"]),
        status="completed", current_step="authorized_not_consumed",
        metadata_json={"schema": SCHEMA, "plan": plan, "approval_id": str(approval_id)}))
    await session.flush()
    return {"status": "persisted_not_consumed", "receipt_id": str(rid)}


async def inspect_exact_unknown_business(session, *, ambiguous_roots, **target):
    """Runtime validator only; absence is no authority, and no row is created."""
    session.info.pop(_INFO, None)
    key = _digest({"project_id": str(target["project_id"]), "chapter_id": str(target["chapter_id"]),
                  "body_sha256": target["sources"].receipt["body_sha256"]})
    row = await session.get(WorkflowRunModel, _identity(key), populate_existing=True)
    if row is None:
        return set()
    chapter = await session.get(ChapterModel, target['chapter_id'], populate_existing=True)
    heads = (chapter.metadata_json or {}).get('deterministic_audit_exact_unknown_authority_heads', {})
    require(isinstance(heads, dict), 'authority_heads')
    if key in heads:
        row = await session.get(WorkflowRunModel, UUID(heads[key]), populate_existing=True)
        require(row is not None, 'authority_head_missing')
        link = (row.metadata_json or {}).get('supersedes')
        require(isinstance(link, dict), 'authority_successor_link')
        parent = await session.get(WorkflowRunModel, UUID(link['receipt_id']), populate_existing=True)
        require(parent is not None and content_hash(parent.metadata_json) == link['metadata_sha256'],
                'authority_parent_changed')
    require(row.workflow_type == "exact_unknown_new_business" and row.status == "completed", "isolation_record")
    binding, budget, _ = await _target(session, **target)
    plan = row.metadata_json["plan"]
    require(content_hash({k: v for k, v in plan.items() if k != "plan_sha256"}) == plan.get("plan_sha256"),
            "isolation_plan_corrupt")
    require(row.project_id == UUID(binding["project_id"]) and row.scope_id == UUID(binding["chapter_id"])
            and plan["schema"] == SCHEMA and plan["binding"] == binding, "isolation_target_drift")
    await require_operator_action_approval(session, approval_id=row.metadata_json["approval_id"],
        purpose="exact_unknown_new_business", binding=approval_binding(plan), capability="isolate_unknown")
    base = plan["baseline"]
    operations, attempts, runs = await _inventory(session, binding["scope"])
    opmap = {o["key"]: o for o in operations}
    require(all(k in opmap and content_hash(opmap[k]) == h for k, h in base["operation_hashes"].items()), "historical_operation_drift")
    require(all(not any(a["operation_key"] == k for a in attempts) for k in base["root_keys"]), "new_actual_evidence")
    for root in base["roots"]:
        require(not list(await session.scalars(select(LlmRunModel.id).where(LlmRunModel.prompt_hash == root["input_hash"]))),
                "new_original_run_evidence")
    for name, values, identity in (("attempt_hashes", attempts, "id"), ("run_hashes", runs, "id")):
        current = {r[identity]: content_hash(r) for r in values}
        require(all(current.get(k) == h for k, h in base[name].items()), "historical_evidence_drift")
    current_budget = _row(budget)
    require(all(current_budget[k] == v for k, v in base["budget_fence"].items())
            and current_budget["calls_used"] >= base["calls_used"]
            and current_budget["tokens_charged"] >= base["tokens_charged"], "budget_or_authorization_drift")
    allowed = set()
    for label in binding["allowed_slot_labels"]:
        op = execution_operation_id(f"commercial-component:{key}:round:1",
            subject=f"critic:chapter_commercial_quality_judge:{PROMPT_VERSION}", revision=binding["current_effective_input_hash"])
        allowed.add(hashlib.sha256(f"{binding['scope']}\n{op}:sample:{label}".encode()).hexdigest())
    from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request
    proven_other_roots = set()
    for item in operations:
        if item["key"] in base["operation_hashes"]:
            continue
        payload = item.get("result_json") or {}
        parent = payload.get("operation_key") if isinstance(payload, dict) else None
        if (item["key"] in allowed or (parent in allowed and item["status"] in {
                "transport_result", "transport_failure_receipt", "unsent_reclaim_receipt"})) and item["input_hash"] == binding["current_effective_input_hash"]:
            continue  # Existing normal slot operation only; no new namespace.
        # Other new history needs positive, complete request-purpose proof.
        if item["status"] in {"transport_result", "transport_failure_receipt"}:
            root = opmap.get(parent)
        else:
            root = item
        require(root is not None and prove_known_noncommercial_request(operation=root,
            actual_attempts=[a for a in attempts if a["operation_key"] == root["key"]],
            original_runs=[r for r in runs if (r.get("metadata_json") or {}).get("execution_receipt", {}).get("operation_key") == root["key"]],
            transport_results=[o for o in operations if o["status"] == "transport_result" and (o.get("result_json") or {}).get("operation_key") == root["key"]],
            failure_receipts=[o for o in operations if o["status"] == "transport_failure_receipt" and (o.get("result_json") or {}).get("operation_key") == root["key"]]), "unlisted_history_requires_recovery")
        proven_other_roots.add(root["key"])
    for attempt in attempts:
        if attempt["id"] not in base["attempt_hashes"]:
            require(attempt["operation_key"] in allowed | proven_other_roots, "unlisted_actual_evidence")
            if attempt["operation_key"] in allowed:
                require(attempt["input_hash"] == binding["current_effective_input_hash"]
                    and attempt["model_name"] == target["effective_critic"]["model"], "current_actual_binding")
    require(all(sum(a["operation_key"] == k for a in attempts) <= 1 for k in allowed), "no_additional_actual_credit")
    for run in runs:
        if run["id"] not in base["run_hashes"]:
            receipt = (run.get("metadata_json") or {}).get("execution_receipt") or {}
            require(receipt.get("operation_key") in allowed | proven_other_roots, "unlisted_original_run")
    require(set(ambiguous_roots) >= set(base["root_keys"]), "isolation_does_not_cover_requested_roots")
    session.info[_INFO] = {"receipt_id": str(row.id), "target_sha256": content_hash(binding),
        "key": key, "input_hash": binding["current_effective_input_hash"]}
    return set(base["root_keys"])


async def persist_unspent_exact_unknown_successor(session, *, parent_receipt_id, expected_parent_metadata_sha256,
        expected_plan_sha256, approval_id, root_keys, **target):
    """Explicit versioned authority migration BEFORE any stable-slot operation.

    Historical receipts stay byte-identical. Body, effective request, sources,
    contract and three slot identities cannot change; no paid slot is reset.
    """
    chapter = await session.get(ChapterModel, target['chapter_id'], populate_existing=True, with_for_update=True)
    parent = await session.get(WorkflowRunModel, UUID(str(parent_receipt_id)), populate_existing=True, with_for_update=True)
    require(parent is not None and parent.workflow_type == 'exact_unknown_new_business'
        and parent.status == 'completed' and content_hash(parent.metadata_json) == expected_parent_metadata_sha256,
        'successor_parent')
    plan = await prepare_exact_unknown_business(session, root_keys=root_keys, **target)
    require(plan['plan_sha256'] == expected_plan_sha256, 'successor_plan_changed')
    old = parent.metadata_json['plan']; before, after = old['binding'], plan['binding']
    require({k:v for k,v in before.items() if k != 'target_protocol'}
            == {k:v for k,v in after.items() if k != 'target_protocol'}
            and before['target_protocol']['evaluation_contract'] == after['target_protocol']['evaluation_contract']
            and old['baseline']['root_keys'] == plan['baseline']['root_keys'], 'successor_business_changed')
    for key in ('operation_hashes', 'attempt_hashes', 'run_hashes'):
        require(all(plan['baseline'][key].get(k) == v for k,v in old['baseline'][key].items()),
                'successor_historical_evidence_changed')
    op = execution_operation_id(f"commercial-component:{after['stable_cohort_key']}:round:1",
        subject=f"critic:chapter_commercial_quality_judge:{PROMPT_VERSION}", revision=after['current_effective_input_hash'])
    keys = {hashlib.sha256(f"{after['scope']}\n{op}:sample:{label}".encode()).hexdigest()
            for label in after['allowed_slot_labels']}
    require(not await session.scalar(select(ExecutionOperationModel.key).where(ExecutionOperationModel.key.in_(keys)).limit(1))
        and not await session.scalar(select(ExecutionAttemptModel.id).where(ExecutionAttemptModel.operation_key.in_(keys)).limit(1)),
        'successor_slots_already_claimed')
    require(after['stable_cohort_key'] not in (chapter.metadata_json or {}).get('commercial_component_cohorts', {}),
            'successor_cohort_already_created')
    await require_operator_action_approval(session, approval_id=approval_id, purpose='exact_unknown_new_business',
        binding=approval_binding(plan), capability='isolate_unknown')
    identity = uuid5(NAMESPACE_URL, 'bestseller:unspent-exact-authority:' + str(parent.id) + ':' + plan['plan_sha256'])
    existing = await session.get(WorkflowRunModel, identity)
    data = {'schema':SCHEMA, 'plan':plan, 'approval_id':str(approval_id),
        'supersedes':{'receipt_id':str(parent.id), 'metadata_sha256':expected_parent_metadata_sha256},
        'same_stable_slots':True, 'attempts_reset':False, 'grade_aliased':False}
    if existing is None:
        session.add(WorkflowRunModel(id=identity, project_id=UUID(after['project_id']), workflow_type='exact_unknown_new_business',
            scope_type='chapter', scope_id=chapter.id, status='completed', current_step='unspent_authority_migrated',
            requested_by='trusted-operator', metadata_json=data))
    else:
        require(existing.metadata_json == data, 'successor_conflict')
    chapter.metadata_json = {**chapter.metadata_json, 'deterministic_audit_exact_unknown_authority_heads':{
        **(chapter.metadata_json or {}).get('deterministic_audit_exact_unknown_authority_heads', {}),
        after['stable_cohort_key']:str(identity)}}
    await session.flush()
    return {'receipt_id':str(identity), 'parent_receipt_id':str(parent.id), 'new_slot_credit':False}


def consume_inspected_exact_unknown(session, *, key, current_input_hash):
    """Link the already persisted authority to the existing cohort transaction."""
    proof = session.info.pop(_INFO, None)
    if proof is not None:
        require(proof.get("key") == key and proof.get("input_hash") == current_input_hash, "stale_inspection")
    return deepcopy(proof)


async def validate_exact_unknown_reservation(session, *, guard, scope, operation_key, input_hash, model):
    """Linearize admission with the existing budget reservation, before SEND.

    The reservation transaction holds authority/history rows while rechecking;
    later revocation cannot retroactively cancel an already admitted send.
    """
    require(isinstance(guard, dict) and set(guard) == {"receipt_id", "target_sha256", "key", "input_hash"}, "dispatch_guard")
    row = await session.get(WorkflowRunModel, UUID(guard["receipt_id"]), populate_existing=True, with_for_update=True)
    require(row is not None and row.workflow_type == "exact_unknown_new_business", "dispatch_receipt")
    plan = row.metadata_json["plan"]
    binding = plan["binding"]
    require(guard["key"] == binding["stable_cohort_key"] and guard["target_sha256"] == content_hash(binding)
        and guard["input_hash"] == input_hash == binding["current_effective_input_hash"]
        and scope == binding["scope"] and model == plan["effective_critic"]["model"], "dispatch_binding")
    # Locks protect persisted authority and old root evidence through reserve.
    await session.get(WorkflowRunModel, UUID(row.metadata_json["approval_id"]), populate_existing=True, with_for_update=True)
    await session.get(ProjectModel, UUID(binding["project_id"]), populate_existing=True, with_for_update=True)
    await session.get(ChapterModel, UUID(binding["chapter_id"]), populate_existing=True, with_for_update=True)
    await session.get(ChapterDraftVersionModel, UUID(binding["draft_id"]), populate_existing=True, with_for_update=True)
    list(await session.scalars(select(ExecutionOperationModel).where(
        ExecutionOperationModel.key.in_(plan["baseline"]["operation_hashes"])).order_by(
        ExecutionOperationModel.key).with_for_update().execution_options(populate_existing=True)))
    await inspect_exact_unknown_business(session, ambiguous_roots=plan["baseline"]["root_keys"],
        project_id=UUID(binding["project_id"]), chapter_id=UUID(binding["chapter_id"]), draft_id=UUID(binding["draft_id"]),
        scope=scope, sources=SimpleNamespace(receipt=plan["source_receipt"]),
        evaluation_contract=binding["target_protocol"]["evaluation_contract"], current_input_hash=input_hash,
        effective_critic=plan["effective_critic"], samples=3)
    op = execution_operation_id(f"commercial-component:{binding['stable_cohort_key']}:round:1",
        subject=f"critic:chapter_commercial_quality_judge:{PROMPT_VERSION}", revision=input_hash)
    allowed = {hashlib.sha256(f"{scope}\n{op}:sample:{label}".encode()).hexdigest() for label in binding["allowed_slot_labels"]}
    require(operation_key in allowed, "dispatch_operation_not_allowed")
    require(not await session.scalar(select(ExecutionAttemptModel.id).where(
        ExecutionAttemptModel.operation_key == operation_key).limit(1)), "dispatch_actual_already_spent")

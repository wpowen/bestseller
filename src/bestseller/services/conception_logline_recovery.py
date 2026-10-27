"""One charged replacement for a stopped, projectless logline stream.

The old partial return remains unusable and fully charged.  An explicit local
operator record binds one replacement to the retained final-candidate checkpoint,
the native quarantine and the original effective input.  The existing gateway
checks that input again and consumes a durable one-send grant at reservation.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from uuid import UUID

from sqlalchemy import or_, select

from bestseller.infra.db.models import (
    ExecutionAttemptModel as Attempt,
)
from bestseller.infra.db.models import (
    ExecutionBudgetModel as Budget,
)
from bestseller.infra.db.models import (
    ExecutionOperationModel as Operation,
)
from bestseller.infra.db.models import (
    ProjectModel as Project,
)
from bestseller.infra.db.models import (
    WorkflowRunModel as Workflow,
)
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionLedger,
    completion_authorization,
    current_execution_scope,
    execution_operation_id,
    execution_scope_block_code,
)
from bestseller.services.conception_checkpoints import (
    stable_conception_input_hash,
    validate_conception_checkpoint,
)
from bestseller.services.creation_intent_contract import (
    ConceptionAttemptInput,
    contract_from_payload,
)
from bestseller.services.model_catalog import get_model_catalog_entry
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.single_dispatch_authorization import (
    authorization_id,
    bind_grant,
    no_retry_settings,
)

SCHEMA = "conception-logline-charged-replacement.v1"
MODEL = "openai/MiniMax-M3.1-Flash"
ENDPOINT = "https://api.minimaxi.com/v1"
_ACTIVE = ("running", "pending", "queued", "stopping")


def _need(value, reason):
    if not value:
        raise ValueError("conception_logline_recovery:" + reason)


def _key(kind, scope, identifier):
    return hashlib.sha256((kind + "\n" + scope + "\n" + identifier).encode()).hexdigest()


def _message_binding(system, user):
    return {
        "system_sha256": hashlib.sha256(system.encode()).hexdigest(),
        "user_sha256": hashlib.sha256(user.encode()).hexdigest(),
    }


def _checkpoint_messages(state):
    from bestseller.services.conception import (
        _creation_intent_prompt_block,
        _seed_fidelity_context_projection,
    )
    from bestseller.services.logline_gate import _reader_judge_messages, load_logline_gate_config

    profile, ctx = state["writing_profile"], state["ctx"]
    intent = {
        "controls": _seed_fidelity_context_projection(ctx),
        "explicit_choices": _creation_intent_prompt_block(ctx),
        "concept_seed": state["explicit_concept_seed"],
    }
    story = {
        "synopsis": state["synopsis"],
        "story_spine": deepcopy(state["story_spine"]),
        "story_architecture": deepcopy(
            (state["concept_contract"] or {}).get("story_architecture", {})
        ),
        "character": deepcopy(profile.get("character", {})),
        "world": deepcopy(profile.get("world", {})),
    }
    system, user, _ = _reader_judge_messages(
        text=str((profile.get("market", {}) or {}).get("logline") or state["premise"]),
        premise=state["premise"],
        genre=state["_ap_genre"],
        sub_genre=state["_ap_sub"],
        gate=load_logline_gate_config(state["_appeal_cfg"]),
        creation_intent=intent,
        story_context=story,
    )
    return _message_binding(system, user)


async def _native_proof(session, *, scope_id, receipt, source_id, slug, allowed_workflow=None):
    """Only native rows; no caller flags can replace a missing receipt or source."""
    _need(
        receipt.get("scope_id") == scope_id
        and receipt.get("decision") == "quarantine_charged"
        and receipt.get("remote_outcome") == "unknown_quarantined"
        and receipt.get("operation_replay_allowed") is False
        and receipt.get("credit_released") is False
        and receipt.get("remaining_unknown") == 0,
        "quarantine_receipt",
    )
    saved = await session.get(
        Operation,
        _key("reconciliation.v1", scope_id, receipt["reconciliation_id"]),
        populate_existing=True,
    )
    record = saved.result_json if saved is not None else {}
    _need(
        saved is not None
        and saved.status == "reconciliation_receipt"
        and saved.scope_id == scope_id
        and record.get("receipt") == receipt
        and record.get("evidence_kind") == "dispatcher_exit"
        and record.get("local_dispatcher_stopped") is True
        and record.get("remote_operation_closed") is False,
        "native_quarantine_missing",
    )
    budget = await session.get(Budget, scope_id, populate_existing=True)
    policy = completion_authorization(budget) if budget else None
    _need(
        budget is not None
        and execution_scope_block_code(budget) is None
        and policy
        and policy.get("status") == "active"
        and policy.get("models") == [MODEL]
        and policy.get("endpoint_hosts") == ["api.minimaxi.com"],
        "current_completion_authority",
    )
    auths = (budget.metadata_json or {}).get("provider_authorizations", {})
    _need(
        sum(value.get("authorization") == policy for value in auths.values()) == 1,
        "native_completion_receipt",
    )
    _need(
        await session.scalar(select(Project.id).where(Project.slug == slug).limit(1)) is None,
        "project_exists",
    )
    source = await session.get(Workflow, source_id, populate_existing=True)
    metadata = source.metadata_json if source else {}
    _need(
        source is not None
        and source.project_id is None
        and source.workflow_type == "conception_initial"
        and source.status == "failed"
        and source.error_message == "execution_result_unknown"
        and metadata.get("execution_scope_id") == scope_id,
        "stopped_source",
    )
    active = select(Workflow.id).where(
        Workflow.status.in_(_ACTIVE),
        or_(
            Workflow.metadata_json["execution_scope_id"].as_string() == scope_id,
            (Workflow.scope_type == "creation")
            & (Workflow.scope_id == UUID(scope_id.removeprefix("conception:"))),
        ),
    )
    if allowed_workflow is not None:
        active = active.where(Workflow.id != allowed_workflow)
    _need(await session.scalar(active.limit(1)) is None, "another_active_owner")
    checkpoint = metadata.get("conception_checkpoint")
    state = validate_conception_checkpoint(
        checkpoint, input_hash=metadata.get("conception_checkpoint_input_hash")
    )
    attempt = await session.get(Attempt, receipt["attempt_id"], populate_existing=True)
    root = (
        await session.get(Operation, attempt.operation_key, populate_existing=True)
        if attempt
        else None
    )
    _need(
        attempt is not None
        and root is not None
        and attempt.scope_id == root.scope_id == scope_id
        and attempt.status == "reconciled_charged"
        and attempt.model_name == MODEL
        and attempt.actual_tokens is None
        and attempt.reserved_tokens > 0
        and root.status == "stopped"
        and root.result_json is None
        and attempt.input_hash == root.input_hash,
        "old_root_not_charged_quarantine",
    )
    failure = await session.get(
        Operation, _key("transport-failure.v1", scope_id, attempt.id), populate_existing=True
    )
    f = failure.result_json if failure else {}
    _need(
        failure is not None
        and failure.status == "transport_failure_receipt"
        and failure.input_hash == root.input_hash
        and f.get("schema") == "transport-failure.v1"
        and f.get("operation_key") == root.key
        and f.get("attempt_id") == attempt.id
        and f.get("workflow_run_id") == str(source.id)
        and f.get("project_id") is None
        and f.get("prompt_template") == "logline_gate"
        and f.get("prompt_version") == "v3"
        and f.get("logical_role") == "critic"
        and f.get("physical_sends") == 1
        and f.get("partial_stream_observed") is True
        and f.get("operator_interruption") is False
        and f.get("http_status") == 500,
        "partial_stream_failure",
    )
    partial = await session.get(
        Operation, _key("provider-return.v1", scope_id, attempt.id), populate_existing=True
    )
    p = partial.result_json if partial else {}
    response = p.get("response") or {}
    response_sha = hashlib.sha256(
        json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    choices, usage = response.get("choices") or [], response.get("usage") or {}
    _need(
        partial is not None
        and partial.status == "unusable_provider_return"
        and p.get("schema") == "provider-return.v1"
        and p.get("operation_key") == root.key
        and p.get("attempt_id") == attempt.id
        and p.get("input_hash") == root.input_hash
        and p.get("model_name") == MODEL
        and p.get("response_sha256") == response_sha
        and p.get("usable") is False
        and p.get("adoption_authorized") is False
        and p.get("credit_released") is False
        and response.get("stream_complete") is False
        and type(response.get("chunks_observed")) is int
        and response["chunks_observed"] > 0
        and len(choices) == 1
        and choices[0].get("finish_reason") is None
        and usage.get("prompt_tokens") is None
        and usage.get("completion_tokens") is None,
        "unusable_partial_return",
    )
    _need(
        await session.get(Operation, _key("transport-result.v1", scope_id, attempt.id)) is None,
        "contrary_complete_result",
    )
    _need(
        await session.scalar(
            select(Attempt.id)
            .where(Attempt.operation_key == root.key, Attempt.id != attempt.id)
            .limit(1)
        )
        is None,
        "multiple_old_attempts",
    )
    _need(
        await session.scalar(
            select(Attempt.id)
            .where(Attempt.scope_id == scope_id, Attempt.status.in_(["unknown", "reserved"]))
            .limit(1)
        )
        is None,
        "unsettled_attempt",
    )
    _need(
        await session.scalar(
            select(Operation.key)
            .where(Operation.scope_id == scope_id, Operation.status.in_(["unknown", "pending"]))
            .limit(1)
        )
        is None,
        "unsettled_operation",
    )
    return {
        "schema": SCHEMA,
        "scope_id": scope_id,
        "source_workflow_id": str(source.id),
        "slug": slug,
        "checkpoint_state_sha256": checkpoint["state_sha256"],
        "stable_input_hash": checkpoint["input_hash"],
        "creation_intent_hash": metadata.get("creation_intent_hash"),
        "original_operation_key": root.key,
        "original_attempt_id": attempt.id,
        "input_hash": root.input_hash,
        "failure_sha256": content_hash(f),
        "partial_sha256": content_hash(p),
        "quarantine": deepcopy(receipt),
        "quarantine_record_sha256": content_hash(record),
        "authorization_ref": policy["authorization_ref"],
        "message_binding": _checkpoint_messages(state),
        "remote_outcome": "unknown_quarantined",
        "credit_released": False,
        "old_operation_replay_allowed": False,
        "adoption_authorized": False,
        "provider_calls": 0,
    }


async def verified_projectless_quarantine(
    session, settings, *, task_id, task_snapshot, scope_id, receipt
):
    """Read-only normal Web reconciliation proof; None denies clearing its fence."""
    del settings
    try:
        t = task_snapshot
        _need(
            t.get("task_id") == task_id
            and str(UUID(task_id)) == task_id
            and scope_id == "conception:" + task_id
            and t.get("task_type") == "autowrite"
            and t.get("status") == "failed"
            and t.get("cancel_requested") is False
            and t.get("current_stage") == t.get("error") == "execution_result_unknown",
            "stopped_task",
        )
        terminal = (t.get("result") or {}).get("execution_terminal") or {}
        _need(
            terminal.get("code") == "execution_result_unknown" and terminal.get("terminal") is True,
            "unknown_terminal",
        )
        payload = t["payload"]
        _need(
            payload.get("_run_conception") is True
            and payload.get("_active_continuation_scope") in (None, "", scope_id)
            and t.get("project_slug") == payload.get("slug")
            and bool(t.get("project_slug")),
            "creation_task",
        )
        contract = contract_from_payload(payload)
        route = get_model_catalog_entry(payload.get("llm_model_id"))
        _need(
            route and route.model == MODEL and contract.llm_model_id == payload["llm_model_id"],
            "task_model",
        )
        attempt = ConceptionAttemptInput(
            contract=contract,
            attempt_id="quarantine-proof",
            idempotency_key="quarantine-proof",
            input_payload={
                "premise": payload.get("premise"),
                "user_hints": payload.get("user_hints") or {},
                "genre": payload.get("genre"),
                "sub_genre": payload.get("sub_genre"),
                "target_chapters": payload.get("target_chapters"),
            },
        )
        with session.no_autoflush:
            proof = await _native_proof(
                session,
                scope_id=scope_id,
                receipt=receipt,
                source_id=UUID(payload["_conception_workflow_run_id"]),
                slug=t["project_slug"],
            )
        _need(
            proof["stable_input_hash"] == stable_conception_input_hash(attempt)
            and proof["creation_intent_hash"] == contract.contract_hash(),
            "creation_source_binding",
        )
        return {**proof, "task_id": task_id, "task_snapshot_sha256": content_hash(t)}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


async def authorize_logline_replacement(
    ledger, *, task_id, task_snapshot, receipt, actor, authorization_ref
):
    """Explicit operator write; no send, settlement, counter reset or task change."""
    _need(isinstance(actor, str) and bool(actor.strip()), "operator_actor")
    async with ledger.transaction() as session:
        budget = await ledger._lock(session)
        _need(ledger._check(budget) is None, "scope_not_open")
        proof = await verified_projectless_quarantine(
            session,
            None,
            task_id=task_id,
            task_snapshot=task_snapshot,
            scope_id=ledger.scope_id,
            receipt=receipt,
        )
        _need(
            proof is not None and proof["authorization_ref"] == authorization_ref,
            "operator_source_proof",
        )
        operation_id = execution_operation_id(
            SCHEMA, subject=proof["original_operation_key"], revision=proof["input_hash"]
        )
        plan = {
            **proof,
            "operator_actor": actor,
            "operation_id": operation_id,
            "replacement_operation_key": hashlib.sha256(
                (ledger.scope_id + "\n" + operation_id).encode()
            ).hexdigest(),
            "prompt_version": "v3:replacement:" + proof["original_operation_key"][:16],
            "physical_attempt_cap": 1,
        }
        plan["plan_sha256"] = content_hash(plan)
        key = _key(SCHEMA, ledger.scope_id, proof["original_operation_key"])
        existing = await session.get(Operation, key)
        if existing is not None:
            _need(
                existing.status == "conception_logline_replacement"
                and existing.result_json == plan,
                "replacement_cannot_rebind",
            )
        else:
            session.add(
                Operation(
                    key=key,
                    scope_id=ledger.scope_id,
                    input_hash=content_hash(plan),
                    status="conception_logline_replacement",
                    owner=actor,
                    lease_until=budget.deadline_at,
                    result_json=plan,
                )
            )
        return deepcopy(plan)


async def prepare_logline_request(session, settings, request):
    """Admit only the saved exact request; the paid gateway remains final authority."""
    scope = current_execution_scope()
    if (
        not scope
        or not scope.startswith("conception:")
        or request.project_id is not None
        or settings.llm.mock
    ):
        return settings, request
    with session.no_autoflush:
        rows = list(
            await session.scalars(
                select(Operation).where(
                    Operation.scope_id == scope,
                    Operation.status == "conception_logline_replacement",
                )
            )
        )
    if not rows:
        return settings, request
    matching = [
        r
        for r in rows
        if (r.result_json or {}).get("message_binding")
        == _message_binding(request.system_prompt, request.user_prompt)
    ]
    try:
        if not matching:
            # A changed candidate must not evade an available replacement cap.
            # Once the replacement has a native completed return, later story
            # repairs are genuinely new inputs and retain normal gate admission.
            for row in rows:
                plan = deepcopy(row.result_json)
                digest = plan.pop("plan_sha256")
                _need(content_hash(plan) == digest, "plan_integrity")
                gid = authorization_id(
                    plan["authorization_ref"] + ":" + plan["original_operation_key"],
                    plan["source_workflow_id"],
                )
                with session.no_autoflush:
                    budget = await session.get(Budget, scope, populate_existing=True)
                    grant = (
                        (budget.metadata_json or {}).get("single_dispatch_grants", {})
                        if budget
                        else {}
                    ).get(gid)
                    result = await session.get(
                        Operation, plan["replacement_operation_key"], populate_existing=True
                    )
                    attempt = (
                        await session.get(Attempt, grant["used_attempt_id"], populate_existing=True)
                        if grant and grant.get("used_attempt_id")
                        else None
                    )
                _need(
                    grant
                    and grant.get("status") == "spent"
                    and grant.get("physical_attempt_cap") == 1
                    and result
                    and result.scope_id == scope
                    and result.status == "completed"
                    and isinstance(result.result_json, dict)
                    and result.input_hash == plan["input_hash"]
                    and result.result_json.get("model_name") == MODEL
                    and result.result_json.get("provider") not in (None, "mock", "fallback")
                    and attempt
                    and attempt.scope_id == scope
                    and attempt.status == "completed"
                    and attempt.operation_key == result.key
                    and attempt.input_hash == result.input_hash,
                    "changed_input_before_completed_replacement",
                )
            return settings, request
        _need(
            len(matching) == 1
            and request.prompt_template == "logline_gate"
            and request.prompt_version == "v3"
            and request.logical_role == "critic"
            and request.operation_id is None
            and request.sample_slot is None,
            "replacement_request_shape",
        )
        plan = deepcopy(matching[0].result_json)
        digest = plan.pop("plan_sha256")
        _need(
            content_hash(plan) == digest
            and matching[0].key == _key(SCHEMA, scope, plan["original_operation_key"])
            and matching[0].input_hash == content_hash({**plan, "plan_sha256": digest}),
            "plan_integrity",
        )
        plan["plan_sha256"] = digest
        from bestseller.services.llm import _conception_task_id_var, _conception_workflow_id_var

        workflow_id = request.workflow_run_id or _conception_workflow_id_var.get()
        _need(
            workflow_id is not None and _conception_task_id_var.get() == plan["task_id"],
            "runtime_creation_owner",
        )
        # Complete_text releases the caller's read transaction before reservation.
        # Register this exact one-send cap in an independent native budget transaction.
        ledger = ExecutionLedger(session.bind, scope, settings.budget)
        async with ledger.transaction() as fresh:
            budget = await ledger._lock(fresh)
            _need(ledger._check(budget) is None, "scope_not_open")
            proof = await _native_proof(
                fresh,
                scope_id=scope,
                receipt=plan["quarantine"],
                source_id=UUID(plan["source_workflow_id"]),
                slug=plan["slug"],
                allowed_workflow=workflow_id,
            )
            for field in proof:
                _need(proof[field] == plan[field], "native_source_changed")
            owner = await fresh.get(Workflow, workflow_id, populate_existing=True)
            _need(
                owner
                and owner.project_id is None
                and owner.workflow_type == "conception_initial"
                and owner.status == "running"
                and (owner.metadata_json or {}).get("execution_scope_id") == scope
                and (owner.metadata_json or {}).get("conception_checkpoint_input_hash")
                == plan["stable_input_hash"],
                "current_workflow",
            )
            grant_id = authorization_id(
                plan["authorization_ref"] + ":" + plan["original_operation_key"],
                plan["source_workflow_id"],
            )
            grants = (budget.metadata_json or {}).get("single_dispatch_grants", {})
            existing = grants.get(grant_id)
            bound_workflow = existing["binding"]["workflow_id"] if existing else str(workflow_id)
            binding = {
                "scope_id": scope,
                "operation_key": plan["replacement_operation_key"],
                "input_hash": plan["input_hash"],
                "model": MODEL,
                "endpoint": ENDPOINT,
                "workflow_id": bound_workflow,
                "user_message_id": plan["authorization_ref"] + ":" + plan["original_operation_key"],
                "prior_workflow_id": plan["source_workflow_id"],
                "prior_journal_sha256": digest,
                "template": "logline_gate",
                "version_prefix": plan["prompt_version"],
                "authority_evidence": deepcopy(plan),
            }
            if existing and bound_workflow != str(workflow_id):
                result = await fresh.get(
                    Operation, plan["replacement_operation_key"], populate_existing=True
                )
                _need(
                    result and result.status == "completed" and result.result_json is not None,
                    "replacement_once_only",
                )
            budget.metadata_json = bind_grant(
                budget.metadata_json, grant_id=grant_id, binding=binding
            )
        prepared = request.model_copy(
            update={
                "operation_id": plan["operation_id"],
                "prompt_version": plan["prompt_version"],
                "metadata": {
                    **request.metadata,
                    "single_dispatch_authorization_id": grant_id,
                    "prepared_effective_input": {
                        "schema": "effective-input.v1",
                        "hash": plan["input_hash"],
                    },
                    "charged_replacement_plan_sha256": digest,
                },
            }
        )
        return no_retry_settings(settings), prepared
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ExecutionBudgetError("execution_logline_replacement_denied", scope) from error

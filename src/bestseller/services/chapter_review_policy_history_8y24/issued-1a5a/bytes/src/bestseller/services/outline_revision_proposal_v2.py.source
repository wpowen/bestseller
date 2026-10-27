"""Stable same-scope full-source proposal owner; no grade or budget renewal.

The V1 input document remains unchanged. Semantic identity is independent of
source-code inspection/audit receipts; a changed wire never gets a new owner
for the same family and protocol. Actual paid physical evidence proves results.
"""

# ruff: noqa: ANN001, ANN201, ANN202 -- Strictly hashed heterogeneous JSON contracts.
from copy import deepcopy
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, update

from bestseller.infra.db import models as db
from bestseller.services import llm, planner
from bestseller.services import outline_revision_proposal as legacy
from bestseller.services import planning_surgical_budget as budget
from bestseller.services import planning_surgical_repair as surgical
from bestseller.services.outline_revision_lifecycle import _runnable
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

SCHEMA = "full-outline-revision-proposal.v2"
WORKFLOW = legacy.WORKFLOW
LOGICAL_NAME = legacy.LOGICAL_NAME
_inputs, _directives, _need, _same = legacy._inputs, legacy._directives, legacy._need, legacy._same


def semantic_family(project_id, scope, context):
    """Actual selected source values only; no code hashes or compiler audit tree."""
    base = context["legacy_materialized_base"]
    binding = base["binding"]
    return {
        "project_id": str(project_id),
        "original_scope": scope,
        "base": {
            key: deepcopy(binding[key])
            for key in (
                "source_artifact_id",
                "source_content_sha256",
                "candidate_hash",
                "materialization_workflow_run_id",
                "chapter_projection_hashes",
            )
        },
        "base_candidate": deepcopy(base["candidate_payload"]),
        "current_canonical": deepcopy(context["current_canonical"]),
        "approved_foundation": deepcopy(context["approved_foundation"]),
        "beat": {
            key: deepcopy(context["untrusted_retained_beat"][key])
            for key in ("artifact_id", "content_sha256", "content")
        },
        "compiler_candidate": deepcopy(context["compiler_candidate"]),
    }


def proposal_workflow_id(family):
    return uuid5(
        NAMESPACE_URL, f"bestseller:{SCHEMA}:{content_hash(family)}:{budget.PROMPT_VERSION}"
    )


def _request_contract(
    settings, project, identity, family, context, validation, directives, *, feedback=None
):
    request = surgical.build_request(
        settings,
        project=project,
        workflow_run_id=identity,
        logical_name=LOGICAL_NAME,
        outline_payload=deepcopy(context["compiler_candidate"]),
        directives=deepcopy(directives),
        source_context=deepcopy(context),
        protocol_feedback=feedback,
    )
    role = budget.resolve_request_role(settings, project, request)
    payload = llm._effective_input_payload(request, role)
    from bestseller.services.source_complete_retention import retention_limit

    with llm.bind_project_prompt_audit(project.id, reason=SCHEMA):
        retention_limit(settings, request, audit=llm._project_prompt_audit.get(), role=role)
    contract = {
        "schema": SCHEMA,
        "workflow_run_id": str(identity),
        "family_sha256": content_hash(family),
        "prompt_version": request.prompt_version,
        "model": role.model,
        "total_output_tokens": payload["max_tokens"],
        "output_contract": deepcopy(request.metadata["planning_surgical_output_budget"]),
        "effective_input_sha256": llm._effective_input_hash(request, role),
        "effective_input": payload,
        "request_sha256": content_hash(request.model_dump(mode="json")),
        "validation_sha256": content_hash(validation),
    }
    return request, contract


async def _capacity(
    session, settings, project, family, context, target, predecessor_id, reconstructed
):
    """Inventory every legacy owner of this base; never infer a fresh allowance."""
    from bestseller.services.planning_capacity_continuation import verify_capacity_predecessor

    reader = _Reader(session)
    owners = await reader.rows(
        db.WorkflowRunModel,
        db.WorkflowRunModel.project_id == project.id,
        db.WorkflowRunModel.workflow_type == WORKFLOW,
        db.WorkflowRunModel.scope_id == UUID(family["base"]["source_artifact_id"]),
    )
    spent = []
    for owner in owners:
        state = owner.metadata_json or {}
        _need(state.get("execution_scope_id") == family["original_scope"], "original_scope_changed")
        if state.get("schema") != legacy.SCHEMA:
            continue
        prior = state.get("source_context")
        _need(isinstance(prior, dict), "legacy_family_unknown")
        if not _same(semantic_family(project.id, family["original_scope"], prior), family):
            continue
        _need(owner.status != "running", "legacy_inflight_recovery_required")
        runs = await reader.rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == owner.id)
        operations = await reader.rows(
            db.ExecutionOperationModel,
            db.ExecutionOperationModel.scope_id == family["original_scope"],
        )
        physical = any(
            str(
                (((op.result_json or {}).get("metadata") or {}).get("paid_call_origin") or {}).get(
                    "workflow_run_id"
                )
            )
            == str(owner.id)
            for op in operations
        )
        if runs or physical:
            spent.append(owner)
        else:
            _need(owner.status != "completed", "legacy_completed_without_proof")
    if not spent:
        _need(predecessor_id is None and reconstructed is None, "unexpected_capacity_predecessor")
        return None
    _need(
        len(spent) == 1 and predecessor_id is not None and str(spent[0].id) == str(predecessor_id),
        "exact_capacity_predecessor_required",
    )
    _need(isinstance(reconstructed, list), "reconstructed_capacity_requests_required")
    # The core proves the original V1 document, not a rewritten modern context.
    # Its selected semantic family has already been compared to the current one.
    return await verify_capacity_predecessor(
        session,
        settings,
        project=project,
        predecessor_workflow_id=spent[0].id,
        source_context=spent[0].metadata_json["source_context"],
        reconstructed_requests=reconstructed,
        target_contract=target,
    )


def _strict_patch(content):
    from bestseller.services.source_complete_outline_json import parse_strict_outline_json

    value = parse_strict_outline_json(content)
    _need(
        set(value) == {"patches"}
        and isinstance(value["patches"], list)
        and all(type(row) is dict for row in value["patches"]),
        "native_patch_array",
    )
    return value["patches"]


def _capacity_compatible(current, baseline):
    """Keep the original accounting observation; new settled work may add costs."""
    if current is None or baseline is None:
        return current is None and baseline is None
    if not isinstance(current, dict) or not isinstance(baseline, dict):
        return False

    def immutable(value):
        return {key: item for key, item in value.items() if key != "existing_accounting"}

    if not _same(immutable(current), immutable(baseline)):
        return False
    now, old = current.get("existing_accounting"), baseline.get("existing_accounting")
    if not isinstance(now, dict) or not isinstance(old, dict):
        return False
    if not isinstance(old.get("limits"), dict) or not _same(now.get("limits"), old["limits"]):
        return False
    return all(
        type(old.get(key)) is int and type(now.get(key)) is int and 0 <= old[key] <= now[key]
        for key in ("calls_used", "tokens_charged")
    )


async def _failed_error(session, project, before, validation, completion):
    """Reproduce the producer's actual protocol error, including domain checks."""
    _need(completion.finish_reason != "length", "capacity_return_cannot_protocol_retry")
    try:
        from bestseller.services.source_complete_outline_json import parse_strict_outline_json

        value = parse_strict_outline_json(completion.content)
        if set(value) != {"patches"}:
            raise planner.PlannerFallbackError(
                "Source-complete patch requires exactly the patches field."
            )
        rows = value.get("patches")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise planner.PlannerFallbackError("Patches must be an array of objects.")
        await surgical.validate_native_patch(
            session,
            project=project,
            logical_name=LOGICAL_NAME,
            outline_payload=before,
            patches=rows,
            validation_context=validation,
        )
    except (planner.PlannerFallbackError, ValueError, TypeError) as error:
        return str(error)[:1200]
    _need(False, "prior_protocol_return_was_valid")


async def _fresh_paid_return(session, settings, run, completion, proof, *, scope, contract):
    """Recheck physical settlement through fresh columns without refreshing ORM state."""
    from bestseller.services import retained_planning_generation as physical
    from bestseller.services.planning_capacity_continuation import fresh_physical_return

    fresh, key = await fresh_physical_return(
        session,
        run,
        scope=scope,
        expected_hash=contract["effective_input_sha256"],
        expected_model=contract["model"],
    )
    response, _ = physical._file(settings, run, "response")
    physical._response(response, fresh)
    attempts = await _Reader(session).rows(
        db.ExecutionAttemptModel, db.ExecutionAttemptModel.operation_key == key
    )
    _need(
        len(attempts) == 1
        and type(run.input_tokens) is int
        and type(run.output_tokens) is int
        and fresh.input_tokens == run.input_tokens
        and fresh.output_tokens == run.output_tokens
        and attempts[0].scope_id == scope
        and attempts[0].status == "completed"
        and attempts[0].model_name == contract["model"]
        and attempts[0].input_hash == contract["effective_input_sha256"]
        and type(attempts[0].actual_tokens) is int
        and attempts[0].actual_tokens == run.input_tokens + run.output_tokens,
        "fresh_paid_settlement_changed",
    )
    _need(
        key == proof["operation_key"]
        and _same(fresh.model_dump(mode="json"), completion.model_dump(mode="json")),
        "paid_return_changed_or_stale",
    )
    return fresh


def _decode_at_expected_bound(request, expected):
    """Only today's trusted builder may select a prospective decode ceiling."""
    from bestseller.services.planning_surgical_transport import decode_source_complete_surgical_prompts
    from bestseller.services.source_complete_retention import MAX_WIRE_CHARACTERS

    bound = expected.metadata["planning_surgical_source_transport"]["prompt_capacity"]["max_estimated_tokens"]
    _need(type(bound) is int and bound > 0, "trusted_decode_bound_required")
    kwargs = {"system_prompt": request.system_prompt, "user_prompt": request.user_prompt,
        "max_chars": MAX_WIRE_CHARACTERS, "max_estimated_tokens": bound}
    if request is expected:
        kwargs["transport_receipt"] = expected.metadata["planning_surgical_source_transport"]
    return decode_source_complete_surgical_prompts(**kwargs)


async def _paid_rows(session, run, operation_key):
    """The immutable first root, transport, and settled attempt, read by columns."""
    import hashlib
    reader = _Reader(session)
    attempts = await reader.rows(db.ExecutionAttemptModel, db.ExecutionAttemptModel.operation_key == operation_key)
    _need(len(attempts) == 1, "retained_first_attempt_required")
    attempt = attempts[0]
    key = hashlib.sha256(f"transport-result.v1\n{attempt.scope_id}\n{attempt.id}".encode()).hexdigest()
    operations = [await reader.get(db.ExecutionOperationModel, k) for k in (operation_key, key)]
    _need(all(row is not None for row in operations), "retained_first_operations_required")
    for operation in operations:
        value = operation.result_json or {}
        paid = value.get("result", {}) if value.get("schema") == "transport-result.v1" else value
        origin = (paid.get("metadata") or {}).get("paid_call_origin") or {}
        _need(all(origin.get(field) == expected for field, expected in (
            ("project_id", str(run.project_id)), ("workflow_run_id", str(run.workflow_run_id)),
            ("logical_role", run.logical_role), ("prompt_template", run.prompt_template),
            ("prompt_version", run.prompt_version), ("prompt_hash", run.prompt_hash))),
            "paid_root_or_transport_origin_changed")
        _need(paid.get("llm_run_id") is None or paid.get("llm_run_id") == str(run.id),
            "paid_root_or_transport_telemetry_conflict")
    from bestseller.services.planning_capacity_continuation import _row_hash
    return {"attempt_id": attempt.id, "attempt_sha256": _row_hash(attempt, db.ExecutionAttemptModel),
        "operations": [{"key": row.key, "sha256": _row_hash(row, db.ExecutionOperationModel)} for row in operations]}


async def _retained_protocol_failure(session, settings, project, identity, state):
    """Prove the one original invalid native return; never synthesize a candidate."""
    from bestseller.services.foundation_field_recovery import _paid
    from bestseller.services.planning_capacity_continuation import _accounting

    reader = _Reader(session)
    runs = await reader.rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity)
    _need(len(runs) == 1, "exactly_one_retained_protocol_run_required")
    fresh = runs[0]
    context, validation = state["source_context"], state["validation_context"]
    family = semantic_family(project.id, state["execution_scope_id"], context)
    _need(_same(family, state["semantic_family"]) and proposal_workflow_id(family) == identity,
        "proposal_family_changed")
    expected, contract = _request_contract(settings, project, identity, family, context,
        validation, state["directives"])
    _need(_same(contract, state["request_contract"]), "prepared_request_changed")
    run, request, completion, proof = await _paid(session, settings, project, str(fresh.id))
    _need(_same({a.key: getattr(run, a.key) for a in inspect(db.LlmRunModel).column_attrs}, vars(fresh)),
        "paid_run_changed_or_stale")
    _need(run.project_id == project.id and run.workflow_run_id == identity
        and request.workflow_run_id == identity and request.project_id == project.id
        and request.logical_role == "planner"
        and request.prompt_template == f"planner_{LOGICAL_NAME}_commercial_surgical_repair"
        and request.prompt_version == budget.PROMPT_VERSION
        and (completion.llm_run_id is None or completion.llm_run_id == run.id)
        and not completion.tool_calls and not completion.fallback_used
        and completion.finish_reason in {"stop", "end_turn", "completed"},
        "paid_request_owner_or_protocol")
    completion = await _fresh_paid_return(session, settings, fresh, completion, proof,
        scope=state["execution_scope_id"], contract=contract)
    role = budget.resolve_request_role(settings, project, expected)
    _need(_same(llm._effective_input_payload(request, role), contract["effective_input"])
        and fresh.prompt_hash == contract["effective_input_sha256"], "paid_entire_input_changed")
    _need(_same(_decode_at_expected_bound(request, expected), _decode_at_expected_bound(expected, expected)),
        "paid_five_field_source_changed")
    error = await _failed_error(session, project, context["compiler_candidate"], validation, completion)
    rows = await _paid_rows(session, fresh, proof["operation_key"])
    aid = rows["attempt_id"]
    # Detect another actual returned operation even if telemetry failed to insert.
    operations = await reader.rows(db.ExecutionOperationModel,
        db.ExecutionOperationModel.scope_id == state["execution_scope_id"])
    allowed = {row["key"] for row in rows["operations"]}
    for operation in operations:
        value = operation.result_json or {}
        paid = value.get("result", {}) if value.get("schema") == "transport-result.v1" else value
        origin = (paid.get("metadata") or {}).get("paid_call_origin") or {}
        if str(origin.get("workflow_run_id")) == str(identity):
            _need(operation.key in allowed, "unlisted_retained_protocol_operation")
    accounting = await _accounting(reader, state["execution_scope_id"], {aid: {
        "operation_key": proof["operation_key"], "input_hash": fresh.prompt_hash,
        "model": fresh.model_name, "cost": fresh.input_tokens + 4 * fresh.output_tokens}})
    baseline = state.get("execution_before_dispatch")
    if baseline is None:
        baseline = (state.get("capacity_predecessor") or {}).get("existing_accounting")
    _need(isinstance(baseline, dict), "original_execution_baseline_required")
    from bestseller.services.planning_capacity_continuation import _row_hash
    all_attempts = await reader.rows(db.ExecutionAttemptModel,
        db.ExecutionAttemptModel.scope_id == state["execution_scope_id"])
    remaining = [row for row in all_attempts if row.id != aid]
    remaining_tokens = 0
    for attempt in remaining:
        amount = attempt.actual_tokens if attempt.actual_tokens is not None else attempt.reserved_tokens
        _need(type(amount) is int and amount >= 0, "remaining_scope_charge_invalid")
        remaining_tokens += amount
    _need(type(baseline.get("calls_used")) is int
        and type(baseline.get("tokens_charged")) is int
        and len(remaining) == baseline["calls_used"]
        and remaining_tokens == baseline["tokens_charged"]
        and content_hash(sorted(_row_hash(row, db.ExecutionAttemptModel) for row in remaining))
            == baseline.get("scope_attempt_rows_sha256")
        and _same(accounting["limits"], baseline.get("limits")),
        "unlisted_scope_attempt_or_baseline_changed")
    token = surgical.RetainedProtocolFailure(project_id=project.id, workflow_run_id=identity,
        scope_id=state["execution_scope_id"], source_context_sha256=content_hash(context),
        candidate_sha256=content_hash(planner._without_planning_meta(context["compiler_candidate"])),
        effective_input_sha256=contract["effective_input_sha256"], llm_run_id=fresh.id,
        content=completion.content, error=error)
    receipt = {"schema": "retained-outline-protocol-failure.v1", "paid_proof": deepcopy(proof),
        "immutable_rows": rows, "failure": token.attempt(),
        "raw_sha256": content_hash(completion.content),
        "source_context_sha256": token.source_context_sha256,
        "candidate_sha256": token.candidate_sha256,
        "first_request_contract_sha256": content_hash(contract),
        "existing_accounting": accounting, "original_execution_baseline": deepcopy(baseline),
        "exact_remaining_scope_attempts_verified": True, "provider_dispatches": 0,
        "grade_asserted": False, "fee_credit_granted": False}
    return token, receipt


async def _prove(session, settings, project, identity, state):
    from bestseller.services.foundation_field_recovery import _paid
    context, validation = state["source_context"], state["validation_context"]
    family = semantic_family(project.id, state["execution_scope_id"], context)
    _need(
        _same(family, state["semantic_family"]) and proposal_workflow_id(family) == identity,
        "proposal_family_changed",
    )
    _request, contract = _request_contract(
        settings, project, identity, family, context, validation, state["directives"]
    )
    _need(_same(contract, state["request_contract"]), "prepared_request_changed")
    result = deepcopy(state["candidate_payload"])
    surgical_receipt = result.get("_meta", {}).get("surgical_repair_receipt", {})
    before = context["compiler_candidate"]
    _need(
        _same(surgical_receipt.get("before_candidate"), before)
        and surgical_receipt.get("protocol_version") == budget.PROMPT_VERSION,
        "compiler_preimage",
    )
    failed = surgical_receipt.get("protocol_attempts", [])
    _need(isinstance(failed, list) and len(failed) <= 1, "patch_attempt_limit")
    run_ids = [item.get("llm_run_id") for item in failed] + [surgical_receipt.get("llm_run_id")]
    _need(
        all(isinstance(value, str) and bool(value) for value in run_ids)
        and len(set(run_ids)) == len(run_ids),
        "duplicate_or_missing_paid_attempt",
    )
    runs = await _Reader(session).rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity)
    _need({str(row.id) for row in runs} == set(run_ids), "exact_paid_run_set")
    fresh_runs = {str(row.id): row for row in runs}
    proofs, feedback = [], []
    for index, run_id in enumerate(run_ids):
        run, request, completion, proof = await _paid(session, settings, project, run_id)
        _need(
            _same(
                {a.key: getattr(run, a.key) for a in inspect(db.LlmRunModel).column_attrs},
                vars(fresh_runs[run_id]),
            ),
            "paid_run_changed_or_stale",
        )
        _need(
            run.workflow_run_id == identity
            and request.workflow_run_id == identity
            and request.project_id == project.id
            and request.logical_role == "planner"
            and request.prompt_template == f"planner_{LOGICAL_NAME}_commercial_surgical_repair"
            and request.prompt_version == budget.PROMPT_VERSION
            and (completion.llm_run_id is None or completion.llm_run_id == run.id)
            and not completion.tool_calls
            and not completion.fallback_used
            and completion.finish_reason in {"stop", "end_turn", "completed"},
            "paid_request_owner_or_protocol",
        )
        expected, expected_contract = _request_contract(
            settings,
            project,
            identity,
            family,
            context,
            validation,
            state["directives"],
            feedback=feedback,
        )
        completion = await _fresh_paid_return(
            session,
            settings,
            fresh_runs[run_id],
            completion,
            proof,
            scope=state["execution_scope_id"],
            contract=expected_contract,
        )
        role = budget.resolve_request_role(settings, project, expected)
        _need(
            _same(llm._effective_input_payload(request, role), expected_contract["effective_input"])
            and run.prompt_hash == expected_contract["effective_input_sha256"],
            "paid_entire_input_changed",
        )
        decoded = _decode_at_expected_bound(request, expected)
        expected_decoded = _decode_at_expected_bound(expected, expected)
        _need(_same(decoded, expected_decoded), "paid_five_field_source_changed")
        if index < len(failed):
            error = await _failed_error(session, project, before, validation, completion)
            item = {"attempt": index + 1, "llm_run_id": run_id, "error": error}
            _need(_same(item, failed[index]), "prior_protocol_error_changed")
            feedback.append(surgical.protocol_failure_feedback(item, completion.content, before))
        if index == 0 and state.get("retained_protocol_proof") is not None:
            retained = state["retained_protocol_proof"]
            _need(_same(proof, retained["paid_proof"])
                and _same(await _paid_rows(session, fresh_runs[run_id], proof["operation_key"]),
                    retained["immutable_rows"]), "retained_first_paid_evidence_changed")
        proofs.append({**proof,
            "provider_return_llm_run_id": (
                str(completion.llm_run_id) if completion.llm_run_id is not None else None
            ),
            "telemetry_row_verified": True,
        })
    patches = _strict_patch(completion.content)
    exact = await surgical.validate_native_patch(
        session,
        project=project,
        logical_name=LOGICAL_NAME,
        outline_payload=before,
        patches=patches,
        validation_context=validation,
    )
    clean = planner._without_planning_meta(result)
    _need(_same(clean, planner._without_planning_meta(exact)), "patch_result_changed")
    _need(not _same(before, clean), "empty_repair")
    _need(
        surgical_receipt.get("after_candidate_hash") == planner._approved_payload_hash(clean),
        "after_candidate_receipt_changed",
    )
    return {
        "schema": SCHEMA,
        "family_sha256": content_hash(family),
        "request_contract_sha256": content_hash(contract),
        "source_context_sha256": content_hash(context),
        "compiler_candidate_sha256": content_hash(before),
        "native_patch": deepcopy(patches),
        "candidate_sha256": content_hash(clean),
        "paid_patch_attempts": proofs,
        "capacity_predecessor": deepcopy(state.get("capacity_predecessor")),
        "legacy_beat_paid_full_source_asserted": False,
        "grade_asserted": False,
        "adoption_granted": False,
        "budget_or_retry_allowance_granted": False,
        "actor": state["actor"],
        "authorization_ref": state["authorization_ref"],
    }


async def propose_full_outline_revision(
    session,
    settings,
    *,
    project_slug,
    parent_artifact_id,
    parent_materialization_workflow_id,
    beat_artifact_id,
    actor,
    authorization_ref,
    explicit_user_approval=False,
    capacity_predecessor_workflow_id=None,
    reconstructed_capacity_requests=None,
    recover_retained_candidate=False,
    resume_retained_protocol=False,
):
    """Append one ungraded V2 proposal; terminal/inflight owners never dispatch again."""
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.model_context_capacity import bind_selected_model_capacity
    from bestseller.services.pipelines import _checkpoint_commit
    from bestseller.services.retry_ledger import project_execution_scope

    _need(
        explicit_user_approval is True
        and isinstance(actor, str)
        and bool(actor.strip())
        and isinstance(authorization_ref, str)
        and bool(authorization_ref.strip()),
        "explicit_authorization",
    )
    _need(not session.in_nested_transaction(), "outer_checkpoint_required")
    protected = (
        db.ProjectModel,
        db.PlanningArtifactVersionModel,
        db.ChapterModel,
        db.SceneCardModel,
        db.BookProductionControlModel,
    )
    _need(
        not any(
            isinstance(row, protected)
            and (row in session.new or row in session.deleted or session.is_modified(row))
            for row in tuple(session.new) + tuple(session.dirty) + tuple(session.deleted)
        ),
        "pending_source_state",
    )
    args = {
        "project_slug": project_slug,
        "parent_artifact_id": parent_artifact_id,
        "parent_materialization_workflow_id": parent_materialization_workflow_id,
        "beat_artifact_id": beat_artifact_id,
    }
    project, context, validation = await _inputs(session, settings, **args)
    project = await _runnable(session, project)
    scope = project_execution_scope(project)
    _need(scope == current_execution_scope(), "original_scope_changed")
    family = semantic_family(project.id, scope, context)
    identity = proposal_workflow_id(family)
    directives = _directives(context)
    _, contract = _request_contract(
        settings, project, identity, family, context, validation, directives
    )
    binding = {
        "context": context,
        "validation": validation,
        "directives": directives,
        "request_contract": contract,
    }
    digest = content_hash(binding)
    _need(type(recover_retained_candidate) is bool, "explicit_recovery_flag")
    _need(type(resume_retained_protocol) is bool
        and not (recover_retained_candidate and resume_retained_protocol), "explicit_resume_flag")
    recovering = False
    resuming_protocol = False
    retained_failure = None
    retained_proof = None
    reader = _Reader(session)
    owner = await reader.get(db.WorkflowRunModel, identity)
    if owner is None:
        surgical.require_surgical_repair_coverage(context['compiler_candidate'])
    if owner is not None:
        _need(
            owner.project_id == project.id
            and owner.workflow_type == WORKFLOW
            and owner.scope_type == "planning_artifact"
            and owner.scope_id == UUID(str(parent_artifact_id)),
            "existing_owner_changed",
        )
        state = deepcopy(owner.metadata_json or {})
        _need(state.get("execution_scope_id") == scope, "original_scope_changed")
        _need(
            state.get("schema") == SCHEMA
            and state.get("binding_sha256") == digest
            and _same(state.get("semantic_family"), family),
            "existing_wire_or_validation_changed",
        )
        saved_id = state.get("capacity_predecessor_workflow_id")
        if capacity_predecessor_workflow_id is None:
            capacity_predecessor_workflow_id = saved_id
        _need(
            str(capacity_predecessor_workflow_id) == str(saved_id), "capacity_predecessor_changed"
        )
        if reconstructed_capacity_requests is None:
            reconstructed_capacity_requests = state.get("reconstructed_capacity_requests")
        _need(
            _same(reconstructed_capacity_requests, state.get("reconstructed_capacity_requests")),
            "capacity_reconstruction_changed",
        )
    else:
        state = None
    capacity = await _capacity(
        session,
        settings,
        project,
        family,
        context,
        contract,
        capacity_predecessor_workflow_id,
        reconstructed_capacity_requests,
    )
    if state is not None:
        _need(
            _capacity_compatible(capacity, state.get("capacity_predecessor")),
            "capacity_proof_changed",
        )
        capacity = deepcopy(state.get("capacity_predecessor"))
        if owner.status == "completed":
            receipt = await _prove(session, settings, project, identity, state)
            # These two additive audit fields are optional on receipts written
            # before this fix. All physical/source/settlement checks still ran.
            saved = state.get("proposal_receipt")
            _need(isinstance(saved, dict), "completed_proof_changed")
            old_attempts = saved.get("paid_patch_attempts", [])
            _need(isinstance(old_attempts, list), "completed_proof_changed")
            for now, old in zip(receipt["paid_patch_attempts"], old_attempts):
                _need(isinstance(old, dict), "completed_proof_changed")
                for field in ("provider_return_llm_run_id", "telemetry_row_verified"):
                    if field not in old:
                        now.pop(field, None)
            _need(_same(receipt, saved), "completed_proof_changed")
            return {
                "candidate_payload": deepcopy(state["candidate_payload"]),
                "proposal_receipt": receipt,
                "workflow_run_id": str(identity),
            }
        if resume_retained_protocol:
            _need(owner.status == "failed" and state.get("phase") == "terminal_error"
                and state.get("candidate_payload") is None
                and state.get("error_type") == "SourceBoundPlanningCapacityError",
                "retained_protocol_capacity_failure_required")
            _need(actor == state.get("actor") and authorization_ref == state.get("authorization_ref"),
                "original_recovery_authorization_required")
            _need(isinstance(state.get("protocol_resume_history", []), list), "protocol_resume_history_shape")
            retained_failure, retained_proof = await _retained_protocol_failure(
                session, settings, project, identity, state)
            resuming_protocol = True
        elif recover_retained_candidate:
            _need(
                owner.status == "failed" and state.get("phase") == "terminal_error"
                and isinstance(state.get("candidate_payload"), dict)
                and bool(state["candidate_payload"]),
                "retained_failed_candidate_required",
            )
            _need(actor == state.get("actor")
                and authorization_ref == state.get("authorization_ref"),
                "original_recovery_authorization_required")
            _need(isinstance(state.get("recovery_history", []), list), "recovery_history_shape")
            recovering = True
        else:
            _need(
                owner.status == "running" and state.get("phase") == "prepared",
                "terminal_or_inflight_recovery_required",
            )
            _need(
                not await reader.rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity),
                "prepared_owner_already_spent",
            )
    else:
        _need(not recover_retained_candidate and not resume_retained_protocol, "recovery_owner_missing")
        state = {
            "schema": SCHEMA,
            "binding_sha256": digest,
            "semantic_family": family,
            "source_context": context,
            "validation_context": validation,
            "directives": directives,
            "request_contract": contract,
            "actor": actor,
            "authorization_ref": authorization_ref,
            "phase": "prepared",
            "execution_scope_id": scope,
            "capacity_predecessor": capacity,
            "capacity_predecessor_workflow_id": str(capacity_predecessor_workflow_id)
            if capacity_predecessor_workflow_id is not None
            else None,
            "reconstructed_capacity_requests": deepcopy(reconstructed_capacity_requests),
        }
        session.add(
            db.WorkflowRunModel(
                id=identity,
                project_id=project.id,
                workflow_type=WORKFLOW,
                scope_type="planning_artifact",
                scope_id=UUID(str(parent_artifact_id)),
                status="running",
                current_step="prepare_exact_proposal_v2",
                requested_by=actor,
                idempotency_key=f"{WORKFLOW}:{identity}",
                metadata_json=deepcopy(state),
            )
        )
        await _checkpoint_commit(session)
        owner = await reader.get(db.WorkflowRunModel, identity)
    observed = {
        key: deepcopy(getattr(owner, key)) for key in ("status", "current_step", "metadata_json")
    }

    async def persist(status, step):
        nonlocal observed
        with session.no_autoflush:
            changed = await session.execute(
                update(db.WorkflowRunModel)
                .where(
                    db.WorkflowRunModel.id == identity,
                    db.WorkflowRunModel.project_id == project.id,
                    db.WorkflowRunModel.workflow_type == WORKFLOW,
                    db.WorkflowRunModel.scope_id == UUID(str(parent_artifact_id)),
                    db.WorkflowRunModel.status == observed["status"],
                    db.WorkflowRunModel.current_step == observed["current_step"],
                    db.WorkflowRunModel.metadata_json == observed["metadata_json"],
                )
                .values(status=status, current_step=step, metadata_json=deepcopy(state))
                .execution_options(synchronize_session=False)
            )
        _need(changed.rowcount == 1, "concurrent_owner_change")
        await _checkpoint_commit(session)
        observed = {"status": status, "current_step": step, "metadata_json": deepcopy(state)}

    async def current(stage):
        live, now, now_validation = await _inputs(session, settings, **args)
        live = await _runnable(session, live)
        _need(
            project_execution_scope(live) == scope == current_execution_scope(),
            "original_scope_changed",
        )
        _need(_same(now, context) and _same(now_validation, validation), "sources_changed_" + stage)
        _, now_contract = _request_contract(
            settings, live, identity, family, now, now_validation, directives
        )
        _need(_same(now_contract, contract), "prepared_request_changed")
        now_capacity = await _capacity(
            session,
            settings,
            live,
            family,
            now,
            now_contract,
            capacity_predecessor_workflow_id,
            reconstructed_capacity_requests,
        )
        _need(_capacity_compatible(now_capacity, capacity), "capacity_proof_changed")
        return live

    if recovering:
        # Separate from the dispatch exception handler: rejected/cancelled recovery
        # must leave the entire terminal checkpoint untouched, never re-arm it.
        previous_state = deepcopy(state)
        project = await current("before_recovery_proof")
        receipt = await _prove(session, settings, project, identity, state)
        await current("before_recovery_commit")
        history = deepcopy(state.get("recovery_history", []))
        history.append({
            "schema": "retained-outline-proposal-recovery.v1",
            "original_phase": previous_state.get("phase"),
            "original_error_type": previous_state.get("error_type"),
            "fullprevious_state": previous_state,
            "actor": actor, "authorization_ref": authorization_ref,
            "provider_dispatches": 0,
            "proposal_receipt": deepcopy(receipt),
        })
        state.update(proposal_receipt=receipt, phase="proposal_ready", recovery_history=history)
        await persist("completed", "retained_proposal_proof_recovered_v2")
        return {
            "candidate_payload": deepcopy(state["candidate_payload"]),
            "proposal_receipt": deepcopy(receipt),
            "workflow_run_id": str(identity),
        }

    try:
        project = await current("before_dispatch")
        if resuming_protocol:
            previous_state = deepcopy(state)
            retained_failure, fresh_proof = await _retained_protocol_failure(
                session, settings, project, identity, state)
            _need(_capacity_compatible(fresh_proof, retained_proof), "retained_protocol_proof_changed")
            _, remaining_contract = _request_contract(settings, project, identity, family, context,
                validation, directives, feedback=[retained_failure.feedback(context["compiler_candidate"])])
            history = deepcopy(state.get("protocol_resume_history", []))
            history.append({"schema": "retained-outline-protocol-resume.v1",
                "fullprevious_state": previous_state,
                "original_phase": previous_state.get("phase"),
                "original_error_type": previous_state.get("error_type"),
                "actor": actor, "authorization_ref": authorization_ref,
                "retained_protocol_proof": deepcopy(retained_proof),
                "remaining_request_contract": deepcopy(remaining_contract),
                "original_spent_protocol_slots": 1, "remaining_protocol_slots": 1,
                "budget_or_retry_allowance_granted": False})
            state.update(phase="dispatching_retained_protocol", protocol_resume_history=history,
                retained_protocol_proof=deepcopy(retained_proof),
                retained_protocol_request_contract=deepcopy(remaining_contract))
            await persist("running", "exact_retained_protocol_slot_v2")
            project = await current("before_retained_protocol_dispatch")
            retained_failure, fresh_proof = await _retained_protocol_failure(
                session, settings, project, identity, state)
            _need(_capacity_compatible(fresh_proof, retained_proof), "retained_protocol_proof_changed")
            _, now_contract = _request_contract(settings, project, identity, family, context,
                validation, directives, feedback=[retained_failure.feedback(context["compiler_candidate"])])
            _need(_same(now_contract, remaining_contract), "retained_protocol_request_changed")
        else:
            from bestseller.services.planning_capacity_continuation import _accounting
            if "execution_before_dispatch" not in state:
                state["execution_before_dispatch"] = await _accounting(_Reader(session), scope, {})
            state["phase"] = "dispatching"
            await persist("running", "exact_surgical_patch_v2")
        dispatch_owner = await _Reader(session).get(db.WorkflowRunModel, identity)
        _need(dispatch_owner is not None
            and dispatch_owner.project_id == project.id
            and dispatch_owner.workflow_type == WORKFLOW
            and dispatch_owner.scope_type == "planning_artifact"
            and dispatch_owner.scope_id == UUID(str(parent_artifact_id))
            and _same({key: deepcopy(getattr(dispatch_owner, key))
                for key in ("status", "current_step", "metadata_json")}, observed),
            "dispatch_owner_changed")
        with (
            bind_selected_model_capacity(project.metadata_json),
            llm.bind_execution_operation(f"outline-revision-proposal:{identity}"),
            llm.bind_project_prompt_audit(project.id, reason=SCHEMA),
        ):
            repair_kwargs = dict(project=project, workflow_run_id=identity,
                logical_name=LOGICAL_NAME, outline_payload=deepcopy(context["compiler_candidate"]),
                directives=deepcopy(directives), validation_context=deepcopy(validation),
                source_report={}, source_context=deepcopy(context))
            if resuming_protocol:
                candidate, returned_run_id = await surgical.repair(
                    session, settings, retained_failure=retained_failure, **repair_kwargs)
            else:
                candidate, returned_run_id = await planner._repair_outline_commercial_findings_surgically(
                    session, settings, world_spec=context["approved_foundation"]["world_spec"]["content"],
                    **repair_kwargs)
        state["candidate_payload"] = deepcopy(candidate)
        _need(
            str(returned_run_id)
            == str(candidate.get("_meta", {}).get("surgical_repair_receipt", {}).get("llm_run_id")),
            "returned_run_identity_changed",
        )
        project = await current("after_return")
        receipt = await _prove(session, settings, project, identity, state)
        state.update(proposal_receipt=receipt, phase="proposal_ready")
        await persist("completed", "ungraded_proposal_retained_v2")
        return {
            "candidate_payload": deepcopy(candidate),
            "proposal_receipt": deepcopy(receipt),
            "workflow_run_id": str(identity),
        }
    except BaseException as error:
        state.update(phase="terminal_error", error_type=type(error).__name__)
        try:
            await persist("failed", "proposal_requires_recovery_v2")
        except BaseException as checkpoint_error:
            error.add_note(
                "Proposal checkpoint retained no replacement error: "
                + type(checkpoint_error).__name__
            )
        raise

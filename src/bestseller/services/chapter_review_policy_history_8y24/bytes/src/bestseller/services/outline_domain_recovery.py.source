"""A separate, source-bound typed correction purpose; old native slots stay closed.

No approval or projection authority is derived from representation repair. Every
return remains an ungraded composite candidate until ordinary hard admission.
"""

# ruff: noqa: ANN001, ANN201, ANN202 -- Heterogeneous JSON with strict canonical guards.
from copy import deepcopy
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, update

from bestseller.infra.db import models as db
from bestseller.services import llm, planner
from bestseller.services import outline_revision_proposal as legacy
from bestseller.services import outline_revision_proposal_v2 as native
from bestseller.services import planning_surgical_budget as budget
from bestseller.services import planning_surgical_repair as surgical
from bestseller.services.outline_revision_lifecycle import _runnable, _unadopted
from bestseller.services.planning_capacity_continuation import _accounting, _row_hash
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

SCHEMA = "outline-domain-recovery.v1"
WORKFLOW = "recover_outline_domain_fields"
DOMAIN_VERSION = "3.6-domain-fields"
DOMAIN_TEMPLATE = "planner_outline_domain_field_corrections"


def _need(ok, reason):
    if not ok:
        raise ValueError("outline_domain_recovery:" + reason)


def _same(left, right):
    return content_hash(left) == content_hash(right)


@dataclass(frozen=True)
class _Paid:
    run: object
    request: object
    completion: object
    proof: dict
    rows: dict


async def _verified_paid(session, settings, project, identity, fresh, expected, contract):
    """The fixture seam is complete_text; physical/fee proof is never substituted."""
    from bestseller.services.foundation_field_recovery import _paid

    run, request, completion, proof = await _paid(session, settings, project, str(fresh.id))
    _need(_same({a.key: getattr(run, a.key) for a in inspect(db.LlmRunModel).column_attrs},
        vars(fresh)), "paid_run_changed_or_stale")
    _need(run.project_id == project.id and run.workflow_run_id == identity
        and request.project_id == project.id and request.workflow_run_id == identity
        and request.logical_role == expected.logical_role == "planner"
        and request.prompt_template == expected.prompt_template
        and request.prompt_version == expected.prompt_version
        and (completion.llm_run_id is None or completion.llm_run_id == fresh.id)
        and not completion.fallback_used, "paid_owner_or_protocol")
    completion = await native._fresh_paid_return(session, settings, fresh, completion, proof,
        scope=(project.metadata_json or {})["execution_scope_id"], contract=contract)
    role = budget.resolve_request_role(settings, project, expected)
    _need(_same(llm._effective_input_payload(request, role), contract["effective_input"])
        and fresh.prompt_hash == contract["effective_input_sha256"], "paid_entire_input_changed")
    rows = await native._paid_rows(session, fresh, proof["operation_key"])
    return _Paid(fresh, request, completion, deepcopy(proof), rows)


def _known_fees(returns):
    return {paid.rows["attempt_id"]: {"operation_key": paid.proof["operation_key"],
        "input_hash": paid.run.prompt_hash, "model": paid.run.model_name,
        "cost": paid.run.input_tokens + 4 * paid.run.output_tokens} for paid in returns}


async def _owner_inventory(session, scope, identity, returns):
    allowed = {row["key"] for paid in returns for row in paid.rows["operations"]}
    for operation in await _Reader(session).rows(db.ExecutionOperationModel,
            db.ExecutionOperationModel.scope_id == scope):
        value = operation.result_json or {}
        returned = value.get("result", {}) if value.get("schema") == "transport-result.v1" else value
        origin = (returned.get("metadata") or {}).get("paid_call_origin") or {}
        if str(origin.get("workflow_run_id")) == str(identity):
            _need(operation.key in allowed, "unlisted_owner_physical_return")


async def _source(session, settings, *, args, source_proposal_workflow_id,
        own_returns=(), anchor=None, completed=False):
    """Prove both spent native slots and the unchanged pre-native execution rows."""
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.retry_ledger import project_execution_scope

    project, now, validation = await legacy._inputs(session, settings, **args)
    project = await _runnable(session, project)
    scope = project_execution_scope(project)
    _need(scope == current_execution_scope(), "original_scope_changed")
    numbers = [row["chapter_number"] for row in now["compiler_candidate"]["chapters"]]
    await _unadopted(session, project.id, numbers)
    reader = _Reader(session)
    identity = UUID(str(source_proposal_workflow_id))
    owner = await reader.get(db.WorkflowRunModel, identity)
    _need(owner is not None and owner.project_id == project.id
        and owner.workflow_type == native.WORKFLOW and owner.scope_type == "planning_artifact"
        and owner.scope_id == UUID(str(args["parent_artifact_id"])) and owner.status == "failed",
        "failed_native_owner_required")
    state = deepcopy(owner.metadata_json or {})
    _need(state.get("schema") == native.SCHEMA and state.get("phase") == "terminal_error"
        and state.get("candidate_payload") is None and state.get("execution_scope_id") == scope,
        "failed_native_state_required")
    context = state["source_context"]
    family = native.semantic_family(project.id, scope, context)
    _need(_same(family, state["semantic_family"])
        and _same(family, native.semantic_family(project.id, scope, now))
        and native.proposal_workflow_id(family) == identity
        and _same(validation, state["validation_context"]), "current_native_sources_changed")
    first, contract = native._request_contract(settings, project, identity, family, context,
        state["validation_context"], state["directives"])
    _need(_same(contract, state["request_contract"]), "first_prepared_contract_changed")
    _need(state.get("binding_sha256") == content_hash({"context": context,
        "validation": state["validation_context"], "directives": state["directives"],
        "request_contract": contract}), "native_prepared_binding_changed")
    runs = await reader.rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity)
    _need(len(runs) == 2, "exactly_two_native_runs_required")
    candidates = [run for run in runs if run.prompt_hash == contract["effective_input_sha256"]]
    _need(len(candidates) == 1, "unique_first_native_run_required")
    ordered = [candidates[0], next(run for run in runs if run.id != candidates[0].id)]
    returns, errors, feedback = [], [], []
    for index, fresh in enumerate(ordered):
        expected, actual_contract = native._request_contract(settings, project, identity, family,
            context, state["validation_context"], state["directives"], feedback=feedback)
        if index == 1:
            _need(_same(actual_contract, state.get("retained_protocol_request_contract")),
                "second_retained_contract_changed")
        paid = await _verified_paid(session, settings, project, identity, fresh, expected, actual_contract)
        _need(not paid.completion.tool_calls and paid.completion.finish_reason in {"stop", "end_turn", "completed"},
            "native_complete_stop_required")
        _need(_same(native._decode_at_expected_bound(paid.request, expected),
            native._decode_at_expected_bound(expected, expected)), "native_five_fields_changed")
        error = await native._failed_error(session, project, context["compiler_candidate"],
            state["validation_context"], paid.completion)
        errors.append(error)
        if index == 0:
            item = {"attempt": 1, "llm_run_id": str(fresh.id), "error": error}
            retained = state.get("retained_protocol_proof") or {}
            _need(_same(item, retained.get("failure"))
                and _same(paid.proof, retained.get("paid_proof"))
                and _same(paid.rows, retained.get("immutable_rows"))
                and content_hash(paid.completion.content) == retained.get("raw_sha256"),
                "first_retained_failure_changed")
            feedback = [surgical.protocol_failure_feedback(item, paid.completion.content,
                context["compiler_candidate"])]
        returns.append(paid)
    _need(len({paid.rows["attempt_id"] for paid in returns}) == 2, "distinct_native_attempts_required")
    await _owner_inventory(session, scope, identity, returns)
    all_proven = [*returns, *own_returns]
    accounting = await _accounting(reader, scope, _known_fees(all_proven))
    baseline = state.get("execution_before_dispatch")
    if baseline is None:
        baseline = (state.get("capacity_predecessor") or {}).get("existing_accounting")
    _need(isinstance(baseline, dict), "original_native_execution_baseline_required")
    excluded = {paid.rows["attempt_id"] for paid in all_proven}
    attempts = await reader.rows(db.ExecutionAttemptModel, db.ExecutionAttemptModel.scope_id == scope)
    remaining = [row for row in attempts if row.id not in excluded]
    if completed:
        _need(isinstance(anchor, dict), "completed_execution_anchor_required")
        by_id = {row.id: row for row in attempts}
        _need(all(aid in by_id and _row_hash(by_id[aid], db.ExecutionAttemptModel) == digest
            for aid, digest in anchor.items()), "accepted_execution_anchor_changed")
    else:
        _need(type(baseline.get("calls_used")) is int and type(baseline.get("tokens_charged")) is int
            and len(remaining) == baseline["calls_used"]
            and sum(row.actual_tokens if row.actual_tokens is not None else row.reserved_tokens
                for row in remaining) == baseline["tokens_charged"]
            and content_hash(sorted(_row_hash(row, db.ExecutionAttemptModel) for row in remaining))
                == baseline.get("scope_attempt_rows_sha256"), "unlisted_native_or_scope_attempt")
    _need(_same(accounting["limits"], baseline.get("limits")), "original_limits_changed")
    proof = {"schema": SCHEMA, "native_workflow_run_id": str(identity),
        "native_workflow_row_sha256": _row_hash(owner, db.WorkflowRunModel),
        "family_sha256": content_hash(family), "source_context_sha256": content_hash(context),
        "validation_sha256": content_hash(validation), "native_errors": errors,
        "paid_returns": [{"proof": paid.proof, "rows": paid.rows,
            "provider_return_llm_run_id": (str(paid.completion.llm_run_id)
                if paid.completion.llm_run_id is not None else None),
            "telemetry_row_verified": True} for paid in returns],
        "original_execution_baseline": deepcopy(baseline),
        "existing_accounting": accounting, "grade_asserted": False}
    return project, context, validation, returns, proof, {
        row.id: _row_hash(row, db.ExecutionAttemptModel) for row in attempts}


async def _diagnostics(session, project, candidate, validation):
    """Collect validation views without placeholder edits or accepted normalization."""
    from bestseller.domain.workflow import ChapterOutlineBatchInput
    from bestseller.services.narrative_contracts import build_identity_manifest, validate_chapter_plan_contract
    from bestseller.services.planning_readiness_gate import evaluate_chapter_outline_batch_planning_readiness
    from bestseller.services.source_bound_outline_integrity import (
        SourceBoundOutlineIntegrityError, prepare_source_bound_outline,
    )

    original = content_hash(candidate)
    manifest = build_identity_manifest(validation["cast_spec"])
    _need(bool(manifest), "declared_cast_required")
    with session.no_autoflush:
        titles = await planner._fetch_existing_chapter_titles(session, project.id,
            exclude_volume_number=int(validation["volume_number"]))
    try:
        prepare_source_bound_outline(deepcopy(candidate),
            volume_number=int(validation["volume_number"]), expected_count=int(validation["expected_count"]),
            chapter_number_offset=int(validation["chapter_number_offset"]),
            identity_manifest=manifest, existing_titles=titles)
        integrity = {"passed": True, "violations": []}
    except SourceBoundOutlineIntegrityError as error:
        integrity = deepcopy(error.report)
    # Pydantic defaults live only in this throwaway view, never in candidate.
    view = ChapterOutlineBatchInput.model_validate(deepcopy(candidate))
    narrative = validate_chapter_plan_contract(view, identity_manifest=manifest,
        require_identity_registry=True).to_dict()
    readiness = evaluate_chapter_outline_batch_planning_readiness(deepcopy(candidate)).model_dump(mode="json")
    _need(content_hash(candidate) == original, "diagnostics_changed_candidate")
    return {"source_integrity": integrity, "chapter_contract": narrative,
        "readiness": readiness, "validation_view_only": True}, manifest


async def _full_domain(session, project, candidate, validation):
    """The exact normal validator; type-view output may not rewrite source values."""
    with session.no_autoflush:
        titles = await planner._fetch_existing_chapter_titles(session, project.id,
            exclude_volume_number=int(validation["volume_number"]))
    before = content_hash(project.metadata_json)
    # The normal source validator records deterministic audit metadata. Its
    # validation view must not mutate the caller's project/source custody.
    validation_project = deepcopy(project)
    clean = planner._validate_generated_volume_outline_or_raise(deepcopy(candidate), project=validation_project,
        logical_name=native.LOGICAL_NAME, volume_number=int(validation["volume_number"]),
        expected_count=int(validation["expected_count"]),
        chapter_number_offset=int(validation["chapter_number_offset"]),
        cast_spec=deepcopy(validation["cast_spec"]), volume_entry=deepcopy(validation["volume_entry"]),
        existing_titles=titles, strict_story_effects=True, strict_field_degeneracy=True)
    _need(content_hash(project.metadata_json) == before, "validator_changed_project")
    _need(_same(planner._without_planning_meta(clean), planner._without_planning_meta(candidate)),
        "validator_normalization_requires_explicit_edit")
    from bestseller.services.planning_readiness_gate import evaluate_chapter_outline_batch_planning_readiness
    readiness = evaluate_chapter_outline_batch_planning_readiness(candidate).model_dump(mode="json")
    _need(readiness.get("passed") is True, "corrected_readiness_rejected")
    return deepcopy(candidate)


async def _prepare_recovery(session, project, context, validation, returns, source_proof):
    from bestseller.services import planning_domain_field_protocol as protocol

    candidate, adapter = protocol.prepare_domain_candidate(context["compiler_candidate"],
        returns[1].completion.content)
    diagnostics, manifest = await _diagnostics(session, project, candidate, validation)
    readiness = diagnostics["readiness"]
    ready_layer = {"gate_name": readiness["gate_name"], "passed": readiness["passed"],
        "violations": [{**deepcopy(finding), "location": finding["path"]}
            for finding in readiness["blocking_findings"]],
        "warnings": deepcopy(readiness["audit_findings"]), "original_report": deepcopy(readiness)}
    report = protocol.combine_domain_validation_reports(diagnostics["source_integrity"],
        diagnostics["chapter_contract"], ready_layer)
    tools, contract = protocol.tools_for_domain_corrections(candidate, report, manifest)
    immutable_proof = {key: deepcopy(value) for key, value in source_proof.items()
        if key != "existing_accounting"}
    return {"candidate": candidate, "adapter_receipt": adapter, "validation_report": report,
        "identity_manifest": manifest, "tools": tools, "contract": contract,
        "validation_context": deepcopy(validation),
        "project_brief": deepcopy(context["project_source_brief"]),
        "source_context": {"original_source_context": deepcopy(context),
            "native_returns": [{"llm_run_id": str(paid.run.id), "content": paid.completion.content,
                "authority": "untrusted_failed_native_return_not_approval"} for paid in returns],
            "native_source_proof": immutable_proof,
            "adapter_receipt": deepcopy(adapter), "grade_asserted": False}}


def recovery_workflow_id(source_id, prepared):
    # Wire/code-inspection changes cannot create an alternative owner.
    return uuid5(NAMESPACE_URL, "bestseller:" + SCHEMA + ":" + str(source_id) + ":" + content_hash({
        "native_raw_sha256": content_hash(prepared["source_context"]["native_returns"][1]["content"]),
        "adapter_policy": prepared["adapter_receipt"]["schema"],
        "contract": prepared["contract"]}))


def _build_request(settings, project, identity, prepared, feedback):
    import math
    from bestseller.services import planning_domain_field_protocol as protocol
    from bestseller.services.finite_book_budget import current_capacity
    from bestseller.services.llm_capacity import INPUT_SAFETY_FACTOR
    from bestseller.services.model_context_capacity import payload_capacity
    from bestseller.services.source_complete_retention import (
        MAX_WIRE_CHARACTERS, retention_limit, source_complete_retention_metadata,
    )

    request = llm.LLMCompletionRequest(logical_role="planner", project_id=project.id,
        workflow_run_id=identity, system_prompt="Typed field capacity preflight; never sent.",
        user_prompt="{}", fallback_response='{"corrections":{}}', prompt_template=DOMAIN_TEMPLATE,
        prompt_version=DOMAIN_VERSION, tools=deepcopy(prepared["tools"]), tool_choice="required")
    role = budget.resolve_request_role(settings, project, request)
    allowance = budget.output_budget(role, answer_tokens=2000)
    request.max_tokens_override = allowance["total_output_tokens"]
    request.metadata = {"project_slug": project.slug, "artifact": WORKFLOW,
        "planning_surgical_output_budget": allowance}
    payload = llm._effective_input_payload(request, role)
    estimated = min(1_000_000, math.floor((current_capacity(model_limit=payload_capacity(payload))
        - payload["max_tokens"] * payload["n_candidates"]) / INPUT_SAFETY_FACTOR))
    _need(estimated > 0, "selected_model_has_no_input_capacity")
    source = {**deepcopy(prepared["source_context"]), "typed_protocol_feedback": deepcopy(feedback)}
    system, user, transport = protocol.build_domain_field_prompts(
        project_brief=prepared["project_brief"], candidate=prepared["candidate"],
        validation_report=prepared["validation_report"], contract=prepared["contract"],
        source_context=source, max_chars=MAX_WIRE_CHARACTERS, max_estimated_tokens=estimated)
    request.system_prompt, request.user_prompt = system, user
    request.metadata["planning_domain_field_transport"] = transport
    logical = protocol.decode_domain_field_prompts(system_prompt=system, user_prompt=user,
        transport_receipt=transport, max_chars=MAX_WIRE_CHARACTERS, max_estimated_tokens=estimated)
    request.metadata["source_complete_retention"] = source_complete_retention_metadata(request,
        role=role, logical_source=logical, packet_start=transport["packet_start"],
        packet_end=transport["packet_end"])
    with llm.bind_project_prompt_audit(project.id, reason=SCHEMA):
        retention_limit(settings, request, audit=llm._project_prompt_audit.get(), role=role)
    contract = {"schema": SCHEMA, "workflow_run_id": str(identity), "model": role.model,
        "effective_input": llm._effective_input_payload(request, role),
        "effective_input_sha256": llm._effective_input_hash(request, role),
        "request_sha256": content_hash(request.model_dump(mode="json")),
        "output_contract": deepcopy(allowance), "transport": deepcopy(transport)}
    return request, contract


def _decode(request, expected):
    from bestseller.services import planning_domain_field_protocol as protocol
    from bestseller.services.source_complete_retention import MAX_WIRE_CHARACTERS
    transport = expected.metadata["planning_domain_field_transport"]
    kwargs = {"system_prompt": request.system_prompt, "user_prompt": request.user_prompt,
        "max_chars": MAX_WIRE_CHARACTERS, "max_estimated_tokens": transport["max_estimated_tokens"]}
    if request is expected:
        kwargs["transport_receipt"] = transport
    return protocol.decode_domain_field_prompts(**kwargs)


def _parse_apply(completion, prepared):
    from bestseller.services import planning_domain_field_protocol as protocol
    kwargs = {"candidate": prepared["candidate"], "validation_report": prepared["validation_report"],
        "identity_manifest": prepared["identity_manifest"], "contract": prepared["contract"]}
    corrections, submission = protocol.parse_domain_corrections(completion, **kwargs)
    candidate, receipt = protocol.apply_domain_corrections(corrections=corrections, **kwargs)
    return candidate, {"submission": submission, "derivation": receipt}


def _feedback(index, paid, error):
    return {"attempt": index + 1, "llm_run_id": str(paid.run.id), "error": str(error)[:1200],
        "rejected_response": paid.completion.model_dump(mode="json"),
        "authority": "untrusted_protocol_failure_not_candidate_or_approval"}


def _attempt_receipt(paid):
    return {"llm_run_id": str(paid.run.id), "paid_proof": deepcopy(paid.proof),
        "immutable_rows": deepcopy(paid.rows),
        "provider_return_sha256": content_hash(paid.completion.model_dump(mode="json"))}


async def _own_returns(session, settings, project, identity, state):
    entries = state.get("protocol_attempts", [])
    _need(isinstance(entries, list) and 1 <= len(entries) <= 2, "typed_attempt_receipts_required")
    runs = await _Reader(session).rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity)
    ids = [entry.get("llm_run_id") for entry in entries]
    _need(len(set(ids)) == len(ids) and {str(run.id) for run in runs} == set(ids), "exact_typed_run_set")
    indexed = {str(run.id): run for run in runs}
    paid_returns, feedback, candidate, correction = [], [], None, None
    for index, entry in enumerate(entries):
        request, contract = _build_request(settings, project, identity, state["prepared"], feedback)
        _need(_same(contract, state["request_contracts"][str(index)]), "typed_prepared_wire_changed")
        paid = await _verified_paid(session, settings, project, identity, indexed[entry["llm_run_id"]],
            request, contract)
        evidence = _attempt_receipt(paid)
        _need(_same({key: entry.get(key) for key in evidence}, evidence), "typed_paid_receipt_changed")
        _need(paid.completion.finish_reason in {"tool_calls", "stop", "end_turn", "completed"},
            "typed_complete_native_return_required")
        _need(_same(_decode(paid.request, request), _decode(request, request)), "typed_five_fields_changed")
        if "error" in entry:
            try:
                _parse_apply(paid.completion, state["prepared"])
            except (ValueError, TypeError) as error:
                item = _feedback(index, paid, error)
                _need(_same(entry, {**evidence, "error": item["error"]}),
                    "typed_protocol_error_changed")
                feedback.append(item)
            else:
                _need(False, "typed_rejected_return_now_valid")
        else:
            _need(index == len(entries) - 1, "typed_success_not_final")
            _need(_same(entry, evidence), "typed_success_receipt_changed")
            candidate, correction = _parse_apply(paid.completion, state["prepared"])
            candidate = await _full_domain(session, project, candidate, state["prepared"]["validation_context"])
        paid_returns.append(paid)
    await _owner_inventory(session, (project.metadata_json or {})["execution_scope_id"], identity, paid_returns)
    _need(candidate is not None, "typed_final_candidate_required")
    return paid_returns, candidate, correction


def _composite(candidate, identity, source_id, prepared, returns):
    result = deepcopy(candidate)
    result["_meta"] = {**deepcopy(result.get("_meta") or {}), "outline_domain_recovery": {
        "schema": SCHEMA, "workflow_run_id": str(identity),
        "native_failed_workflow_run_id": str(source_id),
        "adapter_receipt_sha256": content_hash(prepared["adapter_receipt"]),
        "correction_contract_sha256": content_hash(prepared["contract"]),
        "correction_llm_run_ids": [str(paid.run.id) for paid in returns],
        "composite_candidate": True, "single_provider_payload_claimed": False,
        "grade_asserted": False, "adoption_granted": False}}
    return result


def _receipt(state, candidate, returns, correction):
    return {"schema": SCHEMA, "source_proposal_workflow_id": state["source_proposal_workflow_id"],
        "source_evidence": deepcopy(state["source_evidence"]),
        "adapter_receipt": deepcopy(state["prepared"]["adapter_receipt"]),
        "correction_contract": deepcopy(state["prepared"]["contract"]),
        "actual_domain_diagnostics": deepcopy(state["prepared"]["validation_report"]),
        "model_correction": deepcopy(correction),
        "paid_correction_attempts": [{"proof": paid.proof, "rows": paid.rows,
            "provider_return_llm_run_id": (str(paid.completion.llm_run_id)
                if paid.completion.llm_run_id is not None else None),
            "telemetry_row_verified": True} for paid in returns],
        "candidate_payload_sha256": content_hash(candidate),
        "full_domain_and_readiness_passed": True, "grade_asserted": False,
        "adoption_granted": False, "budget_or_retry_allowance_granted": False,
        "actor": state["actor"], "authorization_ref": state["authorization_ref"]}


async def recover_outline_domain_fields(session, settings, *, project_slug,
        parent_artifact_id, parent_materialization_workflow_id, beat_artifact_id,
        source_proposal_workflow_id, actor, authorization_ref, explicit_user_approval=False):
    """Create one distinct typed purpose, never reopen either old native slot."""
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.model_context_capacity import bind_selected_model_capacity
    from bestseller.services.pipelines import _checkpoint_commit

    _need(explicit_user_approval is True and isinstance(actor, str) and bool(actor.strip())
        and isinstance(authorization_ref, str) and bool(authorization_ref.strip()),
        "explicit_authorization_required")
    _need(not session.in_nested_transaction(), "outer_checkpoint_required")
    protected = (db.ProjectModel, db.PlanningArtifactVersionModel, db.ChapterModel,
        db.SceneCardModel, db.BookProductionControlModel)
    _need(not any(isinstance(row, protected) and (row in session.new or row in session.deleted
        or session.is_modified(row)) for row in tuple(session.new) + tuple(session.dirty)
        + tuple(session.deleted)), "pending_source_changes")
    args = dict(project_slug=project_slug, parent_artifact_id=parent_artifact_id,
        parent_materialization_workflow_id=parent_materialization_workflow_id,
        beat_artifact_id=beat_artifact_id)
    source_id = UUID(str(source_proposal_workflow_id))
    reader = _Reader(session)
    project, _, _ = await legacy._inputs(session, settings, **args)
    project = await _runnable(session, project)
    scope = current_execution_scope()
    owners = await reader.rows(db.WorkflowRunModel, db.WorkflowRunModel.project_id == project.id,
        db.WorkflowRunModel.workflow_type == WORKFLOW, db.WorkflowRunModel.scope_id == source_id)
    if not owners:
        # Prospective work uses the transparent, flat contract. Historical
        # owners remain on their exact original proof/read path below.
        from bestseller.services.outline_domain_transport_recovery import propose_flat_outline_domain_fields
        return await propose_flat_outline_domain_fields(session, settings,
            **args, source_proposal_workflow_id=source_id, actor=actor,
            authorization_ref=authorization_ref, explicit_user_approval=explicit_user_approval)
    _need(len(owners) <= 1, "ambiguous_domain_owner")
    if owners:
        owner = owners[0]
        state = deepcopy(owner.metadata_json or {})
        _need(owner.scope_type == "workflow_run" and state.get("schema") == SCHEMA
            and state.get("execution_scope_id") == scope
            and state.get("source_proposal_workflow_id") == str(source_id), "existing_domain_owner_changed")
        _need(owner.status == "completed", "terminal_or_inflight_domain_owner")
        own, candidate, correction = await _own_returns(session, settings, project, owner.id, state)
        project, context, validation, originals, proof, _ = await _source(session, settings, args=args,
            source_proposal_workflow_id=source_id, own_returns=own,
            anchor=state["execution_anchor"], completed=True)
        _need(native._capacity_compatible(proof, state["source_evidence"]), "source_proof_changed")
        prepared = await _prepare_recovery(session, project, context, validation, originals, proof)
        _need(_same(prepared, state["prepared"]) and recovery_workflow_id(source_id, prepared) == owner.id,
            "completed_domain_inputs_changed")
        payload = _composite(candidate, owner.id, source_id, prepared, own)
        receipt = _receipt(state, payload, own, correction)
        _need(_same(payload, state.get("candidate_payload"))
            and _same(receipt, state.get("proposal_receipt")), "completed_domain_proof_changed")
        return {"candidate_payload": payload, "proposal_receipt": receipt,
            "workflow_run_id": str(owner.id)}
    project, context, validation, originals, source_proof, anchor = await _source(
        session, settings, args=args, source_proposal_workflow_id=source_id)
    prepared = await _prepare_recovery(session, project, context, validation, originals, source_proof)
    identity = recovery_workflow_id(source_id, prepared)
    _, first_contract = _build_request(settings, project, identity, prepared, [])
    state = {"schema": SCHEMA, "phase": "prepared", "execution_scope_id": scope,
        "source_proposal_workflow_id": str(source_id), "source_evidence": deepcopy(source_proof),
        "execution_anchor": deepcopy(anchor), "prepared": deepcopy(prepared),
        "actor": actor, "authorization_ref": authorization_ref,
        "protocol_attempts": [], "request_contracts": {"0": deepcopy(first_contract)}}
    initial_state = deepcopy(state)
    observed = None

    async def persist(status, step):
        nonlocal observed
        with session.no_autoflush:
            changed = await session.execute(update(db.WorkflowRunModel).where(
                db.WorkflowRunModel.id == identity, db.WorkflowRunModel.project_id == project.id,
                db.WorkflowRunModel.workflow_type == WORKFLOW, db.WorkflowRunModel.scope_type == "workflow_run",
                db.WorkflowRunModel.scope_id == source_id,
                db.WorkflowRunModel.status == observed["status"],
                db.WorkflowRunModel.current_step == observed["current_step"],
                db.WorkflowRunModel.metadata_json == observed["metadata_json"])
                .values(status=status, current_step=step, metadata_json=deepcopy(state))
                .execution_options(synchronize_session=False))
        _need(changed.rowcount == 1, "concurrent_domain_owner_change")
        await _checkpoint_commit(session)
        observed = {"status": status, "current_step": step, "metadata_json": deepcopy(state)}

    async def owner_current():
        live = await reader.get(db.WorkflowRunModel, identity)
        _need(live is not None and live.project_id == project.id
            and live.workflow_type == WORKFLOW and live.scope_type == "workflow_run" and live.scope_id == source_id
            and _same({key: getattr(live, key) for key in observed}, observed), "domain_owner_changed")

    async def current(own):
        live, context_now, validation_now, native_now, proof, _ = await _source(session, settings,
            args=args, source_proposal_workflow_id=source_id, own_returns=own)
        _need(native._capacity_compatible(proof, source_proof), "source_proof_changed")
        now = await _prepare_recovery(session, live, context_now, validation_now, native_now, proof)
        _need(_same(now, prepared), "domain_inputs_changed")
        return live

    own, feedback, correction, candidate = [], [], None, None
    try:
        session.add(db.WorkflowRunModel(id=identity, project_id=project.id, workflow_type=WORKFLOW,
            scope_type="workflow_run", scope_id=source_id, status="running",
            current_step="prepare_domain_fields", requested_by=actor,
            idempotency_key=f"{WORKFLOW}:{identity}", metadata_json=deepcopy(state)))
        await _checkpoint_commit(session)
        created = await reader.get(db.WorkflowRunModel, identity)
        _need(created is not None and created.project_id == project.id and created.scope_id == source_id
            and created.workflow_type == WORKFLOW, "registered_domain_owner_missing")
        observed = {key: deepcopy(getattr(created, key))
            for key in ("status", "current_step", "metadata_json")}
        for index in range(2):
            project = await current(own)
            request, contract = _build_request(settings, project, identity, prepared, feedback)
            if index == 0:
                _need(_same(contract, first_contract), "prepared_domain_wire_changed")
            state["request_contracts"][str(index)] = deepcopy(contract)
            state.update(phase="dispatching", pending_protocol_index=index)
            await persist("running", f"dispatch_domain_fields_{index}")
            project = await current(own)
            same_request, same_contract = _build_request(settings, project, identity, prepared, feedback)
            _need(_same(contract, same_contract), "prepared_domain_wire_changed")
            await owner_current()
            with (bind_selected_model_capacity(project.metadata_json),
                    llm.bind_execution_operation(f"outline-domain-recovery:{identity}"),
                    llm.bind_project_prompt_audit(project.id, reason=SCHEMA)):
                completion = await llm.complete_text(session, settings, same_request)
            _need(completion.llm_run_id is not None, "typed_telemetry_receipt_required")
            state["protocol_attempts"].append({"llm_run_id": str(completion.llm_run_id),
                "verification_pending": True})
            state["phase"] = "paid_return_retained"
            await persist("running", "retain_domain_paid_return")
            fresh = await reader.get(db.LlmRunModel, completion.llm_run_id)
            _need(fresh is not None, "typed_paid_run_missing")
            paid = await _verified_paid(session, settings, project, identity, fresh, same_request, same_contract)
            state["protocol_attempts"][-1] = _attempt_receipt(paid)
            await persist("running", "retain_domain_paid_proof")
            _need(paid.completion.finish_reason in {"tool_calls", "stop", "end_turn", "completed"},
                "typed_complete_native_return_required")
            _need(_same(_decode(paid.request, same_request), _decode(same_request, same_request)),
                "typed_five_fields_changed")
            own.append(paid)
            project = await current(own)
            await _owner_inventory(session, scope, identity, own)
            actual_runs = await reader.rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity)
            _need({row.id for row in actual_runs} == {item.run.id for item in own}, "unlisted_typed_paid_run")
            try:
                candidate, correction = _parse_apply(paid.completion, prepared)
            except (ValueError, TypeError) as error:
                item = _feedback(index, paid, error)
                state["protocol_attempts"][-1]["error"] = item["error"]
                if index == 1:
                    raise
                feedback.append(item)
                state["phase"] = "protocol_rejected"
                await persist("running", "retain_domain_protocol_failure")
                continue
            try:
                candidate = await _full_domain(session, project, candidate, validation)
            except (ValueError, planner.PlannerFallbackError) as error:
                # A valid tool return can still fail the full domain. Retain
                # its paid custody and diagnostics without expanding targets
                # or reopening a terminal owner on a later invocation.
                state["protocol_attempts"][-1]["domain_error"] = {
                    "error_type": type(error).__name__, "error": str(error)[:1200],
                    "report": deepcopy(getattr(error, "report", None))}
                diagnostics, _ = await _diagnostics(session, project, candidate, validation)
                state["protocol_attempts"][-1]["domain_error"]["diagnostics"] = diagnostics
                state["phase"] = "domain_rejected"
                await persist("running", "retain_domain_validation_failure")
                raise
            break
        _need(candidate is not None and correction is not None, "typed_candidate_missing")
        project = await current(own)
        await owner_current()
        payload = _composite(candidate, identity, source_id, prepared, own)
        receipt = _receipt(state, payload, own, correction)
        state.update(phase="proposal_ready", candidate_payload=payload, proposal_receipt=receipt)
        await persist("completed", "ungraded_domain_candidate_retained")
        return {"candidate_payload": payload, "proposal_receipt": receipt, "workflow_run_id": str(identity)}
    except BaseException as error:
        state.update(phase="terminal_error", error_type=type(error).__name__)
        try:
            if observed is None:
                await session.rollback()
                registered = await reader.get(db.WorkflowRunModel, identity)
                # An uncommitted/missing insert is not a replacement workflow.
                if registered is not None and registered.project_id == project.id and registered.scope_id == source_id \
                        and registered.workflow_type == WORKFLOW and registered.scope_type == "workflow_run" \
                        and registered.status == "running" and registered.current_step == "prepare_domain_fields" \
                        and _same(registered.metadata_json, initial_state):
                    observed = {key: deepcopy(getattr(registered, key))
                        for key in ("status", "current_step", "metadata_json")}
            if observed is not None:
                await persist("failed", "domain_recovery_requires_attention")
        except BaseException as checkpoint_error:
            error.add_note("Domain recovery retained original error; checkpoint: " + type(checkpoint_error).__name__)
        raise

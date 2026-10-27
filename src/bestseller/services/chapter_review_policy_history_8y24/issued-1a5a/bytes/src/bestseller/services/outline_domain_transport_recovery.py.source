"""One durable flat/literal dispatch; a wire change never renews a purpose.

Old failed owners, raw returns, grades and financial rows are read-only inputs.
Successful returns are composite candidates, never admission or adoption.
"""

# ruff: noqa: ANN001, ANN201, ANN202 -- Canonically guarded heterogeneous JSON.
from copy import deepcopy
import hashlib
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import update

from bestseller.infra.db import models as db
from bestseller.services import llm, outline_domain_recovery as old
from bestseller.services import outline_revision_proposal as legacy
from bestseller.services import outline_revision_proposal_v2 as native
from bestseller.services import planning_surgical_budget as budget
from bestseller.services.outline_revision_lifecycle import _runnable
from bestseller.services.planning_capacity_continuation import _row_hash
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.services.single_dispatch_authorization import (
    authorization_id, no_retry_settings, register_grant,
)

SCHEMA = "outline-domain-flat-purpose.v1"
WORKFLOW = "propose_flat_outline_domain_fields"
TRANSITION = "single-flat-literal-transition.v1"
FRESH = "single-flat-literal-purpose.v1"


def _need(ok, code):
    if not ok:
        error = ValueError("outline_domain_transport:" + code)
        error.domain_transport_guard = code
        raise error


def _same(left, right):
    return content_hash(left) == content_hash(right)


def transport_workflow_id(parent_domain_workflow_id):
    return uuid5(NAMESPACE_URL, "bestseller:" + TRANSITION + ":" + str(parent_domain_workflow_id))


def flat_workflow_id(source_proposal_workflow_id):
    return uuid5(NAMESPACE_URL, "bestseller:" + FRESH + ":" + str(source_proposal_workflow_id))


async def _failed_parent(session, settings, project, parent, state):
    """Reproduce exactly two empty native returns, including every paid row."""
    from bestseller.services.planning_domain_field_protocol import TOOL_NAME
    from bestseller.services.source_complete_outline_json import parse_strict_outline_json

    entries = state.get("protocol_attempts")
    _need(type(entries) is list and len(entries) == 2, "two_parent_attempts_required")
    runs = await _Reader(session).rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == parent.id)
    ids = [entry.get("llm_run_id") for entry in entries]
    _need(len(set(ids)) == 2 and set(ids) == {str(run.id) for run in runs}, "exact_parent_run_set")
    indexed = {str(run.id): run for run in runs}
    returns, feedback = [], []
    for index, entry in enumerate(entries):
        request, contract = old._build_request(settings, project, parent.id, state["prepared"], feedback)
        _need(_same(contract, state["request_contracts"][str(index)]), "parent_wire_changed")
        paid = await old._verified_paid(session, settings, project, parent.id,
            indexed[entry["llm_run_id"]], request, contract)
        _need(paid.completion.finish_reason == "tool_calls" and len(paid.completion.tool_calls or []) == 1,
            "parent_complete_tool_return_required")
        function = paid.completion.tool_calls[0].get("function", {})
        _need(function.get("name") == TOOL_NAME and _same(
            parse_strict_outline_json(function.get("arguments", "")), {"corrections": {}}),
            "parent_return_not_empty_corrections")
        _need(_same(old._decode(paid.request, request), old._decode(request, request)),
            "parent_five_fields_changed")
        try:
            old._parse_apply(paid.completion, state["prepared"])
        except (ValueError, TypeError) as error:
            expected_error = "schema_required:$/corrections:missing=" + ",".join(state["prepared"]["contract"]["paths"])
            _need(str(error) == expected_error, "parent_rejection_not_missing_all_fields")
            item = old._feedback(index, paid, error)
        else:
            _need(False, "parent_return_was_valid")
        _need(_same(entry, {**old._attempt_receipt(paid), "error": item["error"]}),
            "parent_paid_receipt_changed")
        feedback.append(item)
        returns.append(paid)
    await old._owner_inventory(session, state["execution_scope_id"], parent.id, returns)
    return returns


async def _source(session, settings, *, args, parent_domain_workflow_id=None,
        source_proposal_workflow_id=None, own_returns=(), anchor=None, completed=False):
    """Read-only production preflight: parent/native custody and exact scope inventory."""
    reader = _Reader(session)
    project, _, _ = await legacy._inputs(session, settings, **args)
    project = await _runnable(session, project)
    parent, prior_returns = None, []
    if parent_domain_workflow_id is not None:
        parent = await reader.get(db.WorkflowRunModel, UUID(str(parent_domain_workflow_id)))
        _need(parent is not None and parent.project_id == project.id and parent.workflow_type == old.WORKFLOW
            and parent.scope_type == "workflow_run" and parent.status == "failed", "failed_domain_parent_required")
        state = deepcopy(parent.metadata_json or {})
        _need(state.get("schema") == old.SCHEMA and state.get("phase") == "terminal_error"
            and state.get("candidate_payload") is None and state.get("execution_scope_id")
            == (project.metadata_json or {}).get("execution_scope_id"), "failed_domain_parent_state")
        source_id = UUID(state["source_proposal_workflow_id"])
        _need(parent.scope_id == source_id, "parent_source_scope_changed")
        prior_returns = await _failed_parent(session, settings, project, parent, state)
    else:
        source_id = UUID(str(source_proposal_workflow_id))
    project, context, validation, originals, proof, current_anchor = await old._source(session, settings,
        args=args, source_proposal_workflow_id=source_id, own_returns=[*prior_returns, *own_returns],
        anchor=anchor, completed=completed)
    prepared = await old._prepare_recovery(session, project, context, validation, originals, proof)
    if parent is not None:
        _need(old.recovery_workflow_id(source_id, prepared) == parent.id
            and _same(prepared, state["prepared"])
            and native._capacity_compatible(proof, state["source_evidence"]), "parent_current_sources_changed")
        baseline = state["source_evidence"]["existing_accounting"]
        if not completed:
            attempts = await reader.rows(db.ExecutionAttemptModel,
                db.ExecutionAttemptModel.scope_id == state["execution_scope_id"])
            excluded = {paid.rows["attempt_id"] for paid in [*prior_returns, *own_returns]}
            remaining = [row for row in attempts if row.id not in excluded]
            _need(len(remaining) == baseline["calls_used"]
                and sum(row.actual_tokens if row.actual_tokens is not None else row.reserved_tokens
                    for row in remaining) == baseline["tokens_charged"]
                and content_hash(sorted(_row_hash(row, db.ExecutionAttemptModel) for row in remaining))
                    == baseline["scope_attempt_rows_sha256"], "parent_scope_baseline_changed")
        parent_evidence = {"workflow_id": str(parent.id), "workflow_row_sha256": _row_hash(parent, db.WorkflowRunModel),
            "full_state_sha256": content_hash(state), "paid_returns": [old._attempt_receipt(paid) for paid in prior_returns],
            "original_baseline": deepcopy(baseline)}
        # Reasoning and provider draft text stay exclusively in physical audit
        # evidence. The new fictional input receives only exact empty arguments
        # and the reproducible technical rejection, never reasoning content.
        technical = {"same_semantic_purpose": str(parent.id), "prior_returns": [
            {"llm_run_id": str(paid.run.id), "arguments": {"corrections": {}},
                "error": state["protocol_attempts"][index]["error"],
                "physical_proof_sha256": content_hash(paid.proof)} for index, paid in enumerate(prior_returns)],
            "authority": "untrusted_empty_protocol_returns_not_story_source_or_approval"}
        prepared = {**prepared, "source_context": {**prepared["source_context"], "wire_transition": technical}}
    else:
        parent_evidence = None
    from bestseller.services import planning_domain_field_flat_transport as flat
    tools, wire_contract = flat.tools_for_flat_domain_fields(prepared["candidate"],
        prepared["validation_report"], prepared["identity_manifest"], prepared["contract"])
    prepared = {**prepared, "flat_tools": tools, "wire_contract": wire_contract}
    evidence = {"schema": SCHEMA, "native_source": proof, "parent": parent_evidence,
        "source_proposal_workflow_id": str(source_id), "same_semantic_purpose": str(parent.id) if parent else None,
        "prior_purpose_physical_attempts": len(prior_returns), "maximum_purpose_physical_attempts": 3 if parent else 1,
        "maximum_child_physical_attempts": 1}
    return project, prepared, evidence, current_anchor


def _compatible(now, before):
    return native._capacity_compatible(now["native_source"], before["native_source"]) and _same(
        {key: value for key, value in now.items() if key != "native_source"},
        {key: value for key, value in before.items() if key != "native_source"})


def _build_request(settings, project, identity, prepared, *, authorization_ref, prior_workflow_id,
        source_evidence, actor):
    import math
    from bestseller.services import planning_domain_field_flat_transport as flat
    from bestseller.services.finite_book_budget import current_capacity
    from bestseller.services.llm_capacity import INPUT_SAFETY_FACTOR
    from bestseller.services.model_context_capacity import payload_capacity
    from bestseller.services.source_complete_retention import (
        MAX_WIRE_CHARACTERS, retention_limit, source_complete_retention_metadata,
    )
    operation_id = "outline-domain-single:" + str(identity)
    gid = authorization_id(authorization_ref, str(prior_workflow_id))
    request = llm.LLMCompletionRequest(logical_role="planner", project_id=project.id, workflow_run_id=identity,
        system_prompt="Unsent flat field capacity preflight.", user_prompt="{}", fallback_response="{}",
        prompt_template=flat.TEMPLATE, prompt_version=flat.VERSION, tools=deepcopy(prepared["flat_tools"]),
        tool_choice={"type": "function", "function": {"name": flat.TOOL}}, operation_id=operation_id)
    role = budget.resolve_request_role(settings, project, request)
    allowance = budget.output_budget(role, answer_tokens=2000)
    request.max_tokens_override = allowance["total_output_tokens"]
    request.metadata = {"project_slug": project.slug, "planning_surgical_output_budget": allowance,
        "single_dispatch_authorization_id": gid}
    seed = _binding_seed(project, request, source_evidence, authorization_ref=authorization_ref,
        prior_workflow_id=prior_workflow_id, endpoint=role.api_base, model=role.model, actor=actor)
    payload = llm._effective_input_payload(request, role)
    estimated = min(1_000_000, math.floor((current_capacity(model_limit=payload_capacity(payload))
        - payload["max_tokens"] * payload["n_candidates"]) / INPUT_SAFETY_FACTOR))
    _need(estimated > 0, "selected_model_has_no_input_capacity")
    system, user, transport = flat.build_flat_domain_prompts(project_brief=prepared["project_brief"],
        candidate=prepared["candidate"], validation_report=prepared["validation_report"],
        domain_contract=prepared["contract"], wire_contract=prepared["wire_contract"],
        source_context={**prepared["source_context"], "flat_admission_witness": {
            "schema": SCHEMA, "seed_sha256": content_hash(seed),
            "authority": "technical_admission_receipt_not_story_source_or_grade"}},
        max_chars=MAX_WIRE_CHARACTERS, max_estimated_tokens=estimated)
    request.system_prompt, request.user_prompt = system, user
    request.metadata["planning_domain_flat_transport"] = transport
    logical = flat.decode_flat_domain_prompts(system_prompt=system, user_prompt=user,
        transport_receipt=transport, max_chars=MAX_WIRE_CHARACTERS, max_estimated_tokens=estimated)
    request.metadata["source_complete_retention"] = source_complete_retention_metadata(request, role=role,
        logical_source=logical, packet_start=transport["packet_start"], packet_end=transport["packet_end"])
    with llm.bind_project_prompt_audit(project.id, reason=SCHEMA):
        retention_limit(settings, request, audit=llm._project_prompt_audit.get(), role=role)
    contract = {"schema": SCHEMA, "effective_input": llm._effective_input_payload(request, role),
        "effective_input_sha256": llm._effective_input_hash(request, role), "model": role.model,
        "transport": deepcopy(transport), "grant_binding_seed": seed}
    binding = {**seed, "input_hash": contract["effective_input_sha256"]}
    request.metadata["domain_transport_grant_sha256"] = content_hash(binding)
    contract["grant_binding_sha256"] = content_hash(binding)
    contract["request_sha256"] = content_hash(request.model_dump(mode="json"))
    return request, contract


def _decode(request, expected):
    from bestseller.services import planning_domain_field_flat_transport as flat
    from bestseller.services.source_complete_retention import MAX_WIRE_CHARACTERS
    transport = expected.metadata["planning_domain_flat_transport"]
    return flat.decode_flat_domain_prompts(system_prompt=request.system_prompt, user_prompt=request.user_prompt,
        transport_receipt=transport if request is expected else None,
        max_chars=MAX_WIRE_CHARACTERS, max_estimated_tokens=transport["max_estimated_tokens"])


def _parse_apply(completion, prepared):
    from bestseller.services import planning_domain_field_flat_transport as flat
    from bestseller.services import planning_domain_field_protocol as protocol
    corrections, submitted = flat.parse_flat_domain_fields(completion, candidate=prepared["candidate"],
        validation_report=prepared["validation_report"], identity_manifest=prepared["identity_manifest"],
        domain_contract=prepared["contract"], wire_contract=prepared["wire_contract"])
    candidate, receipt = protocol.apply_domain_corrections(prepared["candidate"], corrections,
        validation_report=prepared["validation_report"], identity_manifest=prepared["identity_manifest"],
        contract=prepared["contract"])
    return candidate, {"submission": submitted, "derivation": receipt}


def _binding_seed(project, request, evidence, *, authorization_ref, prior_workflow_id, endpoint, model, actor):
    scope = project.metadata_json["execution_scope_id"]
    return {"scope_id": scope, "operation_key": hashlib.sha256((scope + "\n" + request.operation_id).encode()).hexdigest(),
        "model": model,
        "endpoint": endpoint,
        "workflow_id": str(request.workflow_run_id), "user_message_id": authorization_ref,
        "prior_workflow_id": str(prior_workflow_id), "prior_journal_sha256": content_hash(evidence),
        "template": request.prompt_template, "version_prefix": request.prompt_version, "dispatch_scope": "workflow",
        "authority_evidence": {"type": SCHEMA, "existing_completion_authorization_ref": authorization_ref,
            "actor": actor,
            "same_semantic_purpose": evidence["same_semantic_purpose"],
            "maximum_purpose_physical_attempts": evidence["maximum_purpose_physical_attempts"],
            "maximum_child_physical_attempts": 1}}


def _grant_binding(project, request, contract, evidence, *, authorization_ref, prior_workflow_id, endpoint, actor):
    seed = _binding_seed(project, request, evidence, authorization_ref=authorization_ref,
        prior_workflow_id=prior_workflow_id, endpoint=endpoint, model=contract["model"], actor=actor)
    _need(_same(seed, contract["grant_binding_seed"]), "admission_evidence_changed")
    binding = {**seed, "input_hash": contract["effective_input_sha256"]}
    _need(content_hash(binding) == contract["grant_binding_sha256"]
        == request.metadata["domain_transport_grant_sha256"], "grant_binding_witness_changed")
    return binding


async def _grant(session, scope, gid, binding, paid=None):
    row = await _Reader(session).get(db.ExecutionBudgetModel, scope)
    grant = ((row.metadata_json or {}).get("single_dispatch_grants") or {}).get(gid) if row else None
    _need(type(grant) is dict and _same(grant.get("binding"), binding) and grant.get("physical_attempt_cap") == 1,
        "single_dispatch_binding_changed")
    _need((grant.get("status"), grant.get("used_attempt_id")) == (
        ("spent", paid.rows["attempt_id"]) if paid else ("available", None)), "single_dispatch_consumption_changed")
    return deepcopy(grant)


async def _paid(session, settings, project, identity, state, request, contract):
    runs = await _Reader(session).rows(db.LlmRunModel, db.LlmRunModel.workflow_run_id == identity)
    _need(len(runs) == 1, "exactly_one_child_run_required")
    paid = await old._verified_paid(session, settings, project, identity, runs[0], request, contract)
    _need((paid.run.metadata_json or {}).get("domain_transport_grant_sha256")
        == contract["grant_binding_sha256"], "telemetry_admission_witness_changed")
    _need(paid.proof["operation_key"] == state["grant_binding"]["operation_key"], "child_operation_changed")
    _need(_same(state["paid_attempt"], old._attempt_receipt(paid)), "child_paid_receipt_changed")
    _need(_same(_decode(paid.request, request), _decode(request, request)), "child_five_fields_changed")
    await old._owner_inventory(session, project.metadata_json["execution_scope_id"], identity, [paid])
    await _grant(session, project.metadata_json["execution_scope_id"], request.metadata["single_dispatch_authorization_id"],
        state["grant_binding"], paid)
    return paid


async def _family_guard(reader, project_id, source_id, identity, parent_id):
    for row in await reader.rows(db.WorkflowRunModel, db.WorkflowRunModel.project_id == project_id):
        data = row.metadata_json or {}
        if row.workflow_type == WORKFLOW and data.get("source_proposal_workflow_id") == str(source_id):
            _need(row.id == identity, "existing_semantic_purpose_owner")
        if row.workflow_type == old.WORKFLOW and str(row.scope_id) == str(source_id):
            _need(parent_id is not None and row.id == parent_id, "legacy_semantic_purpose_owner")


def _result(candidate, correction, identity, state, paid):
    payload = old._composite(candidate, identity, state["source_proposal_workflow_id"], state["prepared"], [paid])
    payload["_meta"]["outline_domain_transport"] = {"schema": SCHEMA,
        "workflow_run_id": str(identity), "parent_domain_workflow_id": state["parent_domain_workflow_id"],
        "same_semantic_purpose": state["source_evidence"]["same_semantic_purpose"],
        "grade_asserted": False, "adoption_granted": False}
    receipt = {"schema": SCHEMA, "source_evidence": deepcopy(state["source_evidence"]),
        "parent_domain_workflow_id": state["parent_domain_workflow_id"],
        "source_proposal_workflow_id": state["source_proposal_workflow_id"],
        "model_correction": correction, "paid_attempt": old._attempt_receipt(paid),
        "domain_contract": deepcopy(state["prepared"]["contract"]),
        "wire_contract": deepcopy(state["prepared"]["wire_contract"]),
        "candidate_payload_sha256": content_hash(payload), "full_domain_and_readiness_passed": True,
        "grade_asserted": False, "adoption_granted": False, "budget_or_retry_allowance_granted": False,
        "actor": state["actor"], "authorization_ref": state["authorization_ref"]}
    return {"candidate_payload": payload, "proposal_receipt": receipt, "workflow_run_id": str(identity)}


async def _run(session, settings, *, args, parent_domain_workflow_id, source_proposal_workflow_id,
        actor, authorization_ref, explicit_user_approval):
    from bestseller.services.budget import ExecutionLedger
    from bestseller.services.model_context_capacity import bind_selected_model_capacity
    from bestseller.services.pipelines import _checkpoint_commit

    _need(explicit_user_approval is True and type(actor) is str and bool(actor.strip())
        and type(authorization_ref) is str and bool(authorization_ref.strip()), "explicit_authorization_required")
    _need(not session.in_nested_transaction(), "outer_checkpoint_required")
    protected = (db.ProjectModel, db.PlanningArtifactVersionModel, db.ChapterModel,
        db.SceneCardModel, db.BookProductionControlModel)
    _need(not any(isinstance(row, protected) and (row in session.new or row in session.deleted
        or session.is_modified(row)) for row in tuple(session.new) + tuple(session.dirty)
        + tuple(session.deleted)), "pending_source_changes")
    migration = parent_domain_workflow_id is not None
    target = UUID(str(parent_domain_workflow_id if migration else source_proposal_workflow_id))
    identity = transport_workflow_id(target) if migration else flat_workflow_id(target)
    scoped = no_retry_settings(settings)
    reader = _Reader(session)
    project, _, _ = await legacy._inputs(session, scoped, **args)
    project = await _runnable(session, project)
    owner = await reader.get(db.WorkflowRunModel, identity)
    if owner is not None:
        _need(owner.project_id == project.id and owner.workflow_type == WORKFLOW
            and owner.scope_type == "workflow_run" and owner.scope_id == target,
            "existing_child_owner_changed")
        state = deepcopy(owner.metadata_json or {})
        _need(state.get("schema") == SCHEMA and state.get("mode") == (TRANSITION if migration else FRESH),
            "existing_child_state_changed")
        _need(owner.status == "completed", "terminal_or_inflight_child_owner")
        _need(state.get("actor") == actor and state.get("authorization_ref") == authorization_ref,
            "completed_child_authorization_changed")
        request, contract = _build_request(scoped, project, identity, state["prepared"],
            authorization_ref=state["authorization_ref"], prior_workflow_id=target,
            source_evidence=state["source_evidence"], actor=actor)
        _need(_same(contract, state["request_contract"]), "completed_child_wire_changed")
        rebuilt_binding = _grant_binding(project, request, contract, state["source_evidence"],
            authorization_ref=authorization_ref, prior_workflow_id=target,
            endpoint=budget.resolve_request_role(scoped, project, request).api_base, actor=actor)
        _need(_same(rebuilt_binding, state["grant_binding"]), "completed_grant_binding_changed")
        paid = await _paid(session, scoped, project, identity, state, request, contract)
        project, prepared, evidence, _ = await _source(session, scoped, args=args,
            parent_domain_workflow_id=target if migration else None,
            source_proposal_workflow_id=None if migration else target, own_returns=[paid],
            anchor=state["execution_anchor"], completed=True)
        _need(_same(prepared, state["prepared"]) and _compatible(evidence, state["source_evidence"]),
            "completed_child_sources_changed")
        await _family_guard(reader, project.id, evidence["source_proposal_workflow_id"], identity,
            target if migration else None)
        candidate, correction = _parse_apply(paid.completion, prepared)
        candidate = await old._full_domain(session, project, candidate, prepared["validation_context"])
        result = _result(candidate, correction, identity, state, paid)
        _need(_same(result, state["result"]), "completed_child_result_changed")
        return result
    if not migration:
        # A spent legacy purpose is an explicit migration case, never a new
        # flat allowance even before its extra attempts enter source proof.
        await _family_guard(reader, project.id, target, identity, None)
    project, prepared, evidence, anchor = await _source(session, scoped, args=args,
        parent_domain_workflow_id=target if migration else None,
        source_proposal_workflow_id=None if migration else target)
    source_id = evidence["source_proposal_workflow_id"]
    await _family_guard(reader, project.id, source_id, identity, target if migration else None)
    request, contract = _build_request(scoped, project, identity, prepared,
        authorization_ref=authorization_ref, prior_workflow_id=target, source_evidence=evidence, actor=actor)
    binding = _grant_binding(project, request, contract, evidence,
        authorization_ref=authorization_ref, prior_workflow_id=target,
        endpoint=budget.resolve_request_role(scoped, project, request).api_base, actor=actor)
    state = {"schema": SCHEMA, "mode": TRANSITION if migration else FRESH, "phase": "prepared",
        "execution_scope_id": project.metadata_json["execution_scope_id"],
        "parent_domain_workflow_id": str(target) if migration else None,
        "source_proposal_workflow_id": source_id, "prepared": prepared,
        "source_evidence": evidence, "execution_anchor": anchor, "request_contract": contract,
        "grant_binding": binding, "actor": actor, "authorization_ref": authorization_ref}
    initial_state, observed = deepcopy(state), None

    async def persist(status, step):
        nonlocal observed
        with session.no_autoflush:
            changed = await session.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == identity,
                db.WorkflowRunModel.project_id == project.id, db.WorkflowRunModel.workflow_type == WORKFLOW,
                db.WorkflowRunModel.scope_type == "workflow_run", db.WorkflowRunModel.scope_id == target,
                db.WorkflowRunModel.status == observed["status"],
                db.WorkflowRunModel.current_step == observed["current_step"],
                db.WorkflowRunModel.metadata_json == observed["metadata_json"])
                .values(status=status, current_step=step, metadata_json=deepcopy(state))
                .execution_options(synchronize_session=False))
        _need(changed.rowcount == 1, "concurrent_child_owner_change")
        await _checkpoint_commit(session)
        observed = {"status": status, "current_step": step, "metadata_json": deepcopy(state)}

    async def owner_current():
        row = await reader.get(db.WorkflowRunModel, identity)
        _need(row is not None and row.project_id == project.id and row.workflow_type == WORKFLOW
            and row.scope_type == "workflow_run" and row.scope_id == target
            and _same({key: getattr(row, key) for key in observed}, observed), "child_owner_changed")

    async def current(own=()):
        live, now, proof, _ = await _source(session, scoped, args=args,
            parent_domain_workflow_id=target if migration else None,
            source_proposal_workflow_id=None if migration else target, own_returns=own)
        _need(_same(now, prepared) and _compatible(proof, evidence), "child_sources_changed")
        await _family_guard(reader, live.id, source_id, identity, target if migration else None)
        _, rebuilt = _build_request(scoped, live, identity, now,
            authorization_ref=authorization_ref, prior_workflow_id=target, source_evidence=evidence, actor=actor)
        _need(_same(rebuilt, contract), "prepared_child_wire_changed")
        return live

    try:
        session.add(db.WorkflowRunModel(id=identity, project_id=project.id, workflow_type=WORKFLOW,
            scope_type="workflow_run", scope_id=target, status="running", current_step="prepare_flat_fields",
            requested_by=actor, idempotency_key=f"{WORKFLOW}:{identity}", metadata_json=deepcopy(state)))
        await _checkpoint_commit(session)
        observed = {"status": "running", "current_step": "prepare_flat_fields", "metadata_json": deepcopy(state)}
        project = await current()
        state["phase"] = "dispatching"
        await persist("running", "dispatch_flat_fields_once")
        project = await current()
        await old._owner_inventory(session, state["execution_scope_id"], identity, [])
        await owner_current()
        await session.commit()
        ledger = ExecutionLedger(session.bind, state["execution_scope_id"], scoped.budget)
        gid = request.metadata["single_dispatch_authorization_id"]
        await register_grant(ledger, grant_id=gid, binding=binding)
        project = await current()
        await _grant(session, state["execution_scope_id"], gid, binding)
        await owner_current()
        with bind_selected_model_capacity(project.metadata_json), llm.bind_project_prompt_audit(project.id, reason=SCHEMA):
            completion = await llm.complete_text(session, scoped, request)
        _need(completion.llm_run_id is not None, "child_telemetry_required")
        state["paid_attempt"] = {"llm_run_id": str(completion.llm_run_id), "verification_pending": True}
        state["phase"] = "paid_return_retained"
        await persist("running", "retain_flat_paid_return")
        run = await reader.get(db.LlmRunModel, completion.llm_run_id)
        _need(run is not None, "child_telemetry_missing")
        paid = await old._verified_paid(session, scoped, project, identity, run, request, contract)
        state["paid_attempt"] = old._attempt_receipt(paid)
        await persist("running", "retain_flat_paid_proof")
        paid = await _paid(session, scoped, project, identity, state, request, contract)
        project = await current([paid])
        candidate, correction = _parse_apply(paid.completion, prepared)
        candidate = await old._full_domain(session, project, candidate, prepared["validation_context"])
        await owner_current()
        result = _result(candidate, correction, identity, state, paid)
        state.update(phase="proposal_ready", result=result)
        await persist("completed", "ungraded_flat_candidate_retained")
        return result
    except BaseException as error:
        from bestseller.services.execution_error_diagnostics import safe_error_chain
        state.update(phase="terminal_error", error_type=type(error).__name__,
            failure_diagnostics=safe_error_chain(error))
        if getattr(error, "domain_transport_guard", None) is not None:
            state["guard_code"] = error.domain_transport_guard
        if isinstance(error, old.planner.OutlineContractValidationError):
            state["domain_validation_report"] = deepcopy(error.report)
        try:
            if observed is None:
                await session.rollback()
                row = await reader.get(db.WorkflowRunModel, identity)
                if row is not None and row.project_id == project.id and row.workflow_type == WORKFLOW \
                        and row.scope_type == "workflow_run" and row.scope_id == target and row.status == "running" \
                        and row.current_step == "prepare_flat_fields" and _same(row.metadata_json, initial_state):
                    observed = {key: deepcopy(getattr(row, key)) for key in ("status", "current_step", "metadata_json")}
            if observed is not None:
                await persist("failed", "flat_recovery_requires_attention")
        except BaseException as checkpoint_error:
            error.add_note("Retained original error; child checkpoint: " + type(checkpoint_error).__name__)
        raise


async def recover_outline_domain_transport(session, settings, *, project_slug, parent_artifact_id,
        parent_materialization_workflow_id, beat_artifact_id, parent_domain_workflow_id,
        actor, authorization_ref, explicit_user_approval=False):
    return await _run(session, settings, args=dict(project_slug=project_slug, parent_artifact_id=parent_artifact_id,
        parent_materialization_workflow_id=parent_materialization_workflow_id, beat_artifact_id=beat_artifact_id),
        parent_domain_workflow_id=parent_domain_workflow_id, source_proposal_workflow_id=None,
        actor=actor, authorization_ref=authorization_ref, explicit_user_approval=explicit_user_approval)


async def propose_flat_outline_domain_fields(session, settings, *, project_slug, parent_artifact_id,
        parent_materialization_workflow_id, beat_artifact_id, source_proposal_workflow_id,
        actor, authorization_ref, explicit_user_approval=False):
    return await _run(session, settings, args=dict(project_slug=project_slug, parent_artifact_id=parent_artifact_id,
        parent_materialization_workflow_id=parent_materialization_workflow_id, beat_artifact_id=beat_artifact_id),
        parent_domain_workflow_id=None, source_proposal_workflow_id=source_proposal_workflow_id,
        actor=actor, authorization_ref=authorization_ref, explicit_user_approval=explicit_user_approval)

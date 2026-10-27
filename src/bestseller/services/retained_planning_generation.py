"""Read a fully proven paid planning proposal; never dispatch, grade or persist.

An exact retained generation can survive its foundation workflow failing before
asset import. Original ownership and charges remain unchanged. A matching
opaque result is an error, never permission to buy its replacement.
"""

from __future__ import annotations

# Heterogeneous JSON and serialized ORM snapshots are validated at the boundary.
# ruff: noqa: ANN001, ANN002, ANN202
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import inspect as python_inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.source_bound_planning_content import (
    CONTENT_POLICY,
    PLANNING_PROMPT_VERSION,
    VOLUME_PLANNING_PROMPT_VERSION,
)
from bestseller.services.source_bound_planning_submission import (
    KINDS,
    parse_planning_submission,
    planning_submission_tool,
    VOLUME_NATIVE_POLICY,
)

POLICY = "retained-planning-generation.v1"


class RetainedPlanningGenerationError(ValueError):
    """A fixed, prompt-free error code: callers must not turn this into a miss."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("retained_planning_generation:" + code)


@dataclass(frozen=True)
class RetainedPlanningGeneration:
    payload: dict[str, Any] | list[dict[str, Any]] = field(repr=False)
    completion: llm.LLMCompletionResult = field(repr=False)
    original_llm_run_id: UUID
    original_workflow_run_id: UUID
    submission_receipt: dict[str, Any] = field(repr=False)
    proof: dict[str, Any]


def _require(ok, code: str) -> None:
    if not ok:
        raise RetainedPlanningGenerationError(code)


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "physical_duplicate_key")
        result[key] = value
    return result


def _constant(_):
    raise RetainedPlanningGenerationError("physical_nonfinite")


class _Reader:
    """Read columns, not refreshed ORM objects; preserve pending caller edits."""

    def __init__(self, session) -> None:
        self.session = session

    async def rows(self, model, *conditions):
        columns = [getattr(model, c.key) for c in inspect(model).column_attrs]
        with self.session.no_autoflush:
            result = await self.session.execute(select(*columns).where(*conditions))
        return [SimpleNamespace(**dict(row)) for row in result.mappings()]

    async def get(self, model, key):
        primary = inspect(model).primary_key
        _require(len(primary) == 1, "unsupported_primary_key")
        rows = await self.rows(model, primary[0] == key)
        return rows[0] if rows else None


def _file(settings, run, kind):
    _require(settings.artifact_store.mode == "local", "physical_store_unavailable")
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    ref = getattr(run, kind + "_payload_ref")
    _require(isinstance(ref, str) and bool(ref), "physical_ref_missing")
    path = (root / ref).resolve()
    _require(path.is_relative_to(root), "physical_path_outside_store")
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    _require(
        isinstance(value, dict)
        and value.get("schema_version") == "llm-payload.v1"
        and value.get("kind") == kind
        and value.get("replayable") is True
        and value.get("replayability_reasons") == [],
        "physical_not_replayable",
    )
    _json(value)  # Reject overflow-exponent nonfinite values anywhere in the receipt.
    for key, expected in (
        ("llm_run_id", str(run.id)),
        ("workflow_run_id", str(run.workflow_run_id)),
        ("project_id", str(run.project_id)),
        ("logical_role", run.logical_role),
    ):
        _require(value.get(key) == expected, "physical_identity")
    return value, hashlib.sha256(raw).hexdigest()


async def _canonical(reader, project_id, binding):
    _require(
        isinstance(binding, Mapping) and set(binding) == {"id", "content_hash", "content"},
        "canonical_binding_required",
    )
    project = await reader.get(ProjectModel, project_id)
    _require(project is not None, "project_missing")
    meta = project.metadata_json or {}
    _require(
        meta.get("conception_approved") is True
        and meta.get("creation_canonical_snapshot_id") == binding["id"]
        and meta.get("creation_canonical_snapshot_hash") == binding["content_hash"],
        "canonical_selection_changed",
    )
    row = await reader.get(PlanningArtifactVersionModel, UUID(binding["id"]))
    _require(
        row is not None
        and row.project_id == project_id
        and row.artifact_type == "conception_snapshot"
        and row.status == "canonical"
        and snapshot_hash(row.content) == binding["content_hash"]
        and row.content == binding["content"],
        "canonical_content_changed",
    )
    scope = meta.get("execution_scope_id")
    _require(isinstance(scope, str) and bool(scope), "original_scope_missing")
    return project, scope, _hash(vars(row))


def _current_contract(request, project_id, kind, planner_hash, binding):
    _require(
        kind in KINDS
        and request.project_id == project_id
        and request.logical_role == "planner"
        and request.prompt_template == "planner_" + kind
        and (request.prompt_version == PLANNING_PROMPT_VERSION
             or (kind == "volume_plan" and request.prompt_version == VOLUME_PLANNING_PROMPT_VERSION))
        and request.messages_override is None,
        "current_request_identity",
    )
    _require(
        isinstance(planner_hash, str)
        and len(planner_hash) == 64
        and all(c in "0123456789abcdef" for c in planner_hash),
        "planner_input_hash_required",
    )
    if "input_hash" in request.metadata:
        _require(request.metadata["input_hash"] == planner_hash, "current_planner_hash_mismatch")
    _require(
        request.tools == [planning_submission_tool(kind, version=(
            VOLUME_NATIVE_POLICY if kind == "volume_plan"
            and request.prompt_version == VOLUME_PLANNING_PROMPT_VERSION else None))]
        and request.tool_choice == {"type": "function", "function": {"name": "submit_" + kind}},
        "current_submission_tools",
    )
    document, _ = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_constant).raw_decode(
        request.user_prompt.lstrip()
    )
    from bestseller.services.source_bound_planning_content import (
        decode_source_bound_planning_document,
    )

    try:
        document = decode_source_bound_planning_document(document)
    except (ValueError, TypeError, KeyError) as exc:
        raise RetainedPlanningGenerationError("current_prompt_overflow_invalid") from exc
    _require(
        isinstance(document, dict)
        and document.get("protocol") == CONTENT_POLICY
        and document.get("kind") == kind
        and (document.get("approved_sources") or {}).get("canonical_snapshot") == binding,
        "current_prompt_canonical_binding",
    )


def _response(value, paid):
    # Physical JSON types are evidence: Python considers False == 0 and 0 == 0.0.
    # Compare structured JSON before any model normalization; retain array order.
    content = value.get("content") or {}
    _require(
        content.get("text") == paid.content
        and content.get("truncated") is False
        and type(content.get("original_chars")) is int
        and content.get("original_chars") == len(paid.content),
        "physical_content",
    )
    for key in ("tool_calls", "raw_message"):
        actual = getattr(paid, key)
        record = (value.get("structured_payload_receipts") or {}).get(key) or {}
        text = _json(actual)
        _require(
            _json(value.get(key)) == text
            and record.get("status") == "retained"
            and record.get("encoding") == "json-utf8"
            and record.get("truncated") is False
            and type(record.get("original_chars")) is int
            and record.get("original_chars") == len(text)
            and type(record.get("limit_chars")) is int
            and record["limit_chars"] >= len(text)
            and record.get("sha256") == hashlib.sha256(text.encode()).hexdigest(),
            "physical_structured_return",
        )
    for key in (
        "provider",
        "finish_reason",
        "fallback_used",
        "fallback_source",
        "input_tokens",
        "output_tokens",
    ):
        expected = getattr(paid, key)
        _require(
            type(value.get(key)) is type(expected) and value.get(key) == expected,
            "physical_response_field",
        )
    _require(
        value.get("model") == paid.model_name
        and type(value.get("tool_call_count")) is int
        and value.get("tool_call_count") == len(paid.tool_calls or []),
        "physical_response_model",
    )


async def find_retained_planning_generation(
    session,
    settings,
    *,
    project,
    kind: str,
    current_request: llm.LLMCompletionRequest,
    planner_input_hash: str,
    canonical_binding: Mapping[str, Any],
    validator: Callable[[Any], Any],
    source_llm_run_id: UUID | None = None,
    allow_cast_dependency_review_compatibility: bool = False,
) -> RetainedPlanningGeneration | None:
    """Return the original proposal, or a *proven absence*; never repair or buy.

    Only one fully completed actual attempt is supported by this initial reader.
    Additional attempts, opaque matching operations and ambiguous candidates are
    explicit errors. The supplied validator receives a disposable deep copy.
    Consumer WF/step IDs may differ; paid origin always keeps the original IDs.
    Cast dependency-review compatibility is opt-in and requires an explicit
    source_llm_run_id; it returns a candidate, never an exact paid-input claim.
    """
    try:
        _require(
            type(allow_cast_dependency_review_compatibility) is bool, "compatibility_flag_invalid"
        )
        if allow_cast_dependency_review_compatibility:
            _require(kind == "cast_spec", "compatibility_cast_only")
            return await _find_cast_compatible(
                session,
                settings,
                project=project,
                kind=kind,
                current_request=current_request,
                planner_input_hash=planner_input_hash,
                canonical_binding=canonical_binding,
                validator=validator,
                source_llm_run_id=source_llm_run_id,
            )
        return await _find(
            session,
            settings,
            project=project,
            kind=kind,
            current_request=current_request,
            planner_input_hash=planner_input_hash,
            canonical_binding=canonical_binding,
            validator=validator,
            source_llm_run_id=source_llm_run_id,
        )
    except RetainedPlanningGenerationError:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OSError,
        OverflowError,
        RecursionError,
    ) as exc:
        # Existing proof/parser errors may embed evidence. Expose a stable code
        # and retain the exception cause locally, without copying its contents.
        raise RetainedPlanningGenerationError("proof_unavailable") from exc


async def _find(
    session,
    settings,
    *,
    project,
    kind,
    current_request,
    planner_input_hash,
    canonical_binding,
    validator,
    source_llm_run_id,
):
    binding = deepcopy(dict(canonical_binding))
    request = current_request.model_copy(deep=True)
    _current_contract(request, project.id, kind, planner_input_hash, binding)
    _require(
        callable(validator) and not python_inspect.iscoroutinefunction(validator),
        "synchronous_validator_required",
    )
    reader = _Reader(session)
    fresh_project, scope, canonical_sha = await _canonical(reader, project.id, binding)
    catalog, catalog_sha = retained._fresh_catalog()
    role = retained._fresh_role(settings, fresh_project, request, catalog)
    effective_hash = llm._effective_input_hash(request, role)
    operations = await reader.rows(
        ExecutionOperationModel,
        ExecutionOperationModel.scope_id == scope,
        ExecutionOperationModel.input_hash == effective_hash,
    )
    runs = await reader.rows(
        LlmRunModel,
        LlmRunModel.project_id == project.id,
        LlmRunModel.logical_role == "planner",
        LlmRunModel.prompt_template == request.prompt_template,
    )
    candidates = []
    for run in runs:
        meta = run.metadata_json or {}
        explicit = source_llm_run_id is not None and str(run.id) == str(source_llm_run_id)
        matches = run.prompt_hash == effective_hash or (
            meta.get("input_hash") == planner_input_hash
            and run.prompt_version == request.prompt_version
        )
        run_scope = (meta.get("execution_receipt") or {}).get("scope_id")
        if not explicit and (not matches or run_scope not in (None, scope)):
            continue
        owner = (
            await reader.get(WorkflowRunModel, run.workflow_run_id) if run.workflow_run_id else None
        )
        receipt = (
            ((owner.metadata_json or {}).get("planning_submissions") or {}).get(str(run.id))
            if owner
            else None
        )
        if explicit or (isinstance(receipt, dict) and receipt.get("status") == "verified"):
            candidates.append((run, owner, receipt))
        elif run.prompt_hash == effective_hash:
            raise RetainedPlanningGenerationError("matching_generation_opaque")
    if not candidates:
        _require(source_llm_run_id is None, "requested_generation_missing")
        _require(not operations, "matching_operation_opaque")
        return None
    _require(len(candidates) == 1, "ambiguous_generations")
    run, owner, saved = candidates[0]
    _require(
        source_llm_run_id is None or str(run.id) == str(source_llm_run_id),
        "requested_generation_mismatch",
    )
    _require(
        owner is not None
        and owner.project_id == project.id
        and owner.workflow_type == "generate_foundation_plan"
        and owner.status in {"failed", "completed"}
        and (owner.metadata_json or {}).get("execution_scope_id") == scope,
        "original_workflow_binding",
    )
    _require(
        run.workflow_run_id == owner.id
        and run.prompt_version == request.prompt_version
        and (run.metadata_json or {}).get("input_hash") == planner_input_hash,
        "original_generation_binding",
    )
    _require(
        isinstance(saved, dict) and saved.get("status") == "verified" and saved.get("kind") == kind,
        "original_submission_missing",
    )
    physical_request, request_sha = _file(settings, run, "request")
    physical_response, response_sha = _file(settings, run, "response")
    original = retained._retained_request(settings, run)
    for key in (
        "project_id",
        "logical_role",
        "system_prompt",
        "user_prompt",
        "prompt_template",
        "prompt_version",
        "model_tier",
        "model_catalog_key",
        "temperature_override",
        "max_tokens_override",
        "cache_system",
        "tools",
        "tool_choice",
    ):
        _require(getattr(original, key) == getattr(request, key), "current_request_changed")
    _require(llm._effective_input_hash(original, role) == effective_hash, "physical_effective_hash")
    original_role = retained._fresh_role(settings, fresh_project, original, catalog)
    _require(original_role == role, "physical_role_changed")
    expected = llm._effective_input_payload(request, role)
    effective = physical_request.get("effective") or {}
    for key, value in (
        ("model", role.model),
        ("provider", llm._provider_from_model(role.model)),
        ("temperature", expected["temperature"]),
        ("max_tokens", expected["max_tokens"]),
        ("timeout_seconds", role.timeout_seconds),
        ("stream", role.stream),
        ("thinking_type", expected["thinking_type"]),
        ("reasoning_effort", expected["reasoning_effort"]),
    ):
        _require(effective.get(key) == value, "physical_effective_parameters")
    _require(
        (run.metadata_json or {}).get("effective_input_receipt", {}).get("schema")
        == "effective-input.v1",
        "effective_hash_domain",
    )
    paid, operation_key = await retained._physical_return(
        reader, run, scope=scope, expected_hash=effective_hash, expected_model=role.model
    )
    _require(
        paid.finish_reason in {"stop", "tool_calls", "function_call", "end_turn"},
        "incomplete_return",
    )
    root = await reader.get(ExecutionOperationModel, operation_key)
    root_metadata = (root.result_json or {}).get("metadata") or {}
    expected_origin = llm._paid_call_origin(original, effective_hash)
    _require(
        root_metadata.get("paid_call_origin") == expected_origin
        and paid.metadata.get("paid_call_origin") == expected_origin,
        "paid_origin_changed",
    )
    for metadata in (root_metadata, paid.metadata):
        effective_receipt = metadata.get("effective_input_receipt") or {}
        _require(
            effective_receipt.get("schema") == "effective-input.v1"
            and effective_receipt.get("hash") == effective_hash,
            "paid_effective_hash_domain",
        )
    _require(
        run.input_tokens == paid.input_tokens and run.output_tokens == paid.output_tokens,
        "paid_telemetry_usage_mismatch",
    )
    attempts = await reader.rows(
        ExecutionAttemptModel, ExecutionAttemptModel.operation_key == operation_key
    )
    listed = (run.metadata_json or {}).get("execution_receipt", {}).get("attempts", [])
    _require(
        len(attempts) == len(listed) == 1
        and attempts[0].id == listed[0].get("attempt_id")
        and attempts[0].status == listed[0].get("status") == "completed",
        "actual_attempt_set",
    )
    transport_key = hashlib.sha256(
        f"transport-result.v1\n{scope}\n{attempts[0].id}".encode()
    ).hexdigest()
    _require(
        {op.key for op in operations} == {operation_key, transport_key}, "matching_operation_set"
    )
    _response(physical_response, paid)
    payload, _, replayed = parse_planning_submission(paid, kind=kind, version=(
        VOLUME_NATIVE_POLICY if kind == "volume_plan"
        and request.prompt_version == VOLUME_PLANNING_PROMPT_VERSION else None))
    _require(replayed == saved, "original_submission_changed")
    try:
        result = validator(deepcopy(payload))
        if python_inspect.isawaitable(result):
            if python_inspect.iscoroutine(result):
                result.close()
            raise RetainedPlanningGenerationError("synchronous_validator_required")
        _require(result is not False, "current_validator_rejected")
    except RetainedPlanningGenerationError:
        raise
    except Exception as exc:
        raise RetainedPlanningGenerationError("current_validator_rejected") from exc
    final_project, final_scope, final_sha = await _canonical(reader, project.id, binding)
    _require(final_scope == scope and final_sha == canonical_sha, "canonical_changed_during_proof")
    final_catalog, final_catalog_sha = retained._fresh_catalog()
    _require(
        final_catalog_sha == catalog_sha
        and retained._fresh_role(settings, final_project, request, final_catalog) == role,
        "route_changed_during_proof",
    )

    def matching_rows(rows):
        return sorted(
            (
                vars(row)
                for row in rows
                if ((row.metadata_json or {}).get("execution_receipt") or {}).get("scope_id")
                in (None, scope)
                and (
                    row.prompt_hash == effective_hash
                    or (
                        (row.metadata_json or {}).get("input_hash") == planner_input_hash
                        and row.prompt_version == request.prompt_version
                    )
                )
            ),
            key=lambda row: str(row["id"]),
        )

    final_runs = await reader.rows(
        LlmRunModel,
        LlmRunModel.project_id == project.id,
        LlmRunModel.logical_role == "planner",
        LlmRunModel.prompt_template == request.prompt_template,
    )
    final_owner = await reader.get(WorkflowRunModel, owner.id)
    final_operations = await reader.rows(
        ExecutionOperationModel,
        ExecutionOperationModel.scope_id == scope,
        ExecutionOperationModel.input_hash == effective_hash,
    )
    final_attempts = await reader.rows(
        ExecutionAttemptModel, ExecutionAttemptModel.operation_key == operation_key
    )
    _require(
        matching_rows(final_runs) == matching_rows(runs)
        and final_owner is not None
        and vars(final_owner) == vars(owner)
        and _hash([vars(op) for op in sorted(final_operations, key=lambda op: op.key)])
        == _hash([vars(op) for op in sorted(operations, key=lambda op: op.key)])
        and _hash([vars(a) for a in final_attempts]) == _hash([vars(a) for a in attempts]),
        "evidence_changed_during_proof",
    )
    await retained._physical_return(
        reader, run, scope=scope, expected_hash=effective_hash, expected_model=role.model
    )
    # Fresh file reads fence replacement during awaited DB/validator work.
    _require(
        _file(settings, run, "request")[1] == request_sha
        and _file(settings, run, "response")[1] == response_sha,
        "physical_files_changed",
    )
    proof = {
        "schema": POLICY,
        "project_id": str(project.id),
        "kind": kind,
        "scope_id": scope,
        "source_content_policy": CONTENT_POLICY,
        "prompt_version": request.prompt_version,
        "effective_role_sha256": _hash(role.model_dump()),
        "original_workflow_run_id": str(owner.id),
        "original_llm_run_id": str(run.id),
        "operation_key": operation_key,
        "attempt_ids": [a.id for a in attempts],
        "effective_input_sha256": effective_hash,
        "planner_input_sha256": planner_input_hash,
        "canonical_id": binding["id"],
        "canonical_content_sha256": binding["content_hash"],
        "canonical_row_sha256": canonical_sha,
        "catalog_sha256": catalog_sha,
        "request_file_sha256": request_sha,
        "response_file_sha256": response_sha,
        "submission_receipt_sha256": _hash(saved),
        "payload_sha256": _hash(payload),
        "llm_row_sha256": _hash(vars(run)),
        "workflow_row_sha256": _hash(vars(owner)),
        "operation_rows_sha256": _hash(
            [vars(op) for op in sorted(operations, key=lambda op: op.key)]
        ),
        "attempt_rows_sha256": _hash([vars(a) for a in attempts]),
        "provider_dispatches": 0,
        "grade_reused": False,
        "approval_asserted": False,
        "semantic_review_required": True,
        "charges_retained": True,
    }
    completion = paid.model_copy(deep=True, update={"llm_run_id": run.id})
    return RetainedPlanningGeneration(
        deepcopy(payload), completion, run.id, owner.id, deepcopy(saved), proof
    )


_CAST_COMPAT_PATHS = (
    "/existing_planning_assets/world_spec/_meta/source_bound_expansion_review_receipt",
    "/projection/_meta/world_spec_hash",
    "/prompt_capacity",
)


def _prompt_document(request):
    text = request.user_prompt
    start = len(text) - len(text.lstrip())
    document, end = json.JSONDecoder(
        object_pairs_hook=_unique,
        parse_constant=_constant,
    ).raw_decode(text, start)
    _require(isinstance(document, dict), "compatibility_prompt_object")
    return document, text[:start], text[start:end], text[end:]


def _cast_dependency_compatibility(original, current):
    """Check derived values, then compare the complete remaining source packet.

    This is candidate compatibility, never an assertion that paid request bytes
    or effective input hashes match. Unknown metadata is not a removable leaf.
    """
    from bestseller.services import source_bound_planning_content as content

    docs = []
    capacities = []
    world_hashes = []
    for request, historical in ((original, True), (current, False)):
        document, prefix, body, suffix = _prompt_document(request)
        _require(
            document.get("kind") == "cast_spec" and document.get("protocol") == CONTENT_POLICY,
            "compatibility_protocol",
        )
        _require(
            document.get("prompt_capacity")
            == content._prompt_capacity_receipt(request.system_prompt, body),
            "compatibility_capacity_receipt",
        )
        capacities.append(
            content.assert_source_bound_planning_prompt_capacity(
                request.system_prompt,
                request.user_prompt,
            )
        )
        projection = document["projection"]
        world = document["existing_planning_assets"]["world_spec"]
        _require(
            isinstance(world, dict) and isinstance(world.get("_meta"), dict),
            "compatibility_world_object",
        )
        dependency = content._dependency_projection(world)
        if historical and "source_bound_expansion_review_receipt" in world["_meta"]:
            # Restore the fourth key ONLY to reconstruct the historical
            # three-key projection, using the current shared projection core.
            dependency["_meta"]["source_bound_expansion_review_receipt"] = deepcopy(
                world["_meta"]["source_bound_expansion_review_receipt"]
            )
        _require(
            projection["_meta"]["world_spec_hash"] == content._hash(dependency),
            "compatibility_world_dependency_hash",
        )
        world_hashes.append(
            content._hash({key: value for key, value in world.items() if key != "_meta"})
        )
        document = deepcopy(document)
        document["existing_planning_assets"]["world_spec"]["_meta"].pop(
            "source_bound_expansion_review_receipt",
            None,
        )
        # A ref-wrapped projection/hash would hide extra differences; support
        # only the literal producer hash path this compatibility policy names.
        _require(
            isinstance(document["projection"].get("_meta"), dict)
            and "world_spec_hash" in document["projection"]["_meta"],
            "compatibility_literal_projection_hash_required",
        )
        document["projection"]["_meta"].pop("world_spec_hash")
        document.pop("prompt_capacity")
        docs.append((document, prefix, suffix))
    _require(
        docs[0] == docs[1] and world_hashes[0] == world_hashes[1],
        "compatibility_unlisted_input_difference",
    )
    return {
        "schema": "retained-cast-dependency-review-compatibility.v1",
        "full_effective_input_exact": False,
        "paid_request_bytes_equivalence_asserted": False,
        "approved_difference_paths": list(_CAST_COMPAT_PATHS),
        "world_story_sha256": world_hashes[0],
        "original_final_capacity": capacities[0],
        "current_final_capacity": capacities[1],
    }


async def _find_cast_compatible(
    session,
    settings,
    *,
    project,
    kind,
    current_request,
    planner_input_hash,
    canonical_binding,
    validator,
    source_llm_run_id,
):
    # This is an explicit recovery option, never latest-run discovery. Other
    # successful or malformed historical Cast requests are not selected here.
    _require(source_llm_run_id is not None, "compatibility_source_run_required")
    request = current_request.model_copy(deep=True)
    _current_contract(request, project.id, kind, planner_input_hash, canonical_binding)
    reader = _Reader(session)
    fresh_project, scope, canonical_sha = await _canonical(reader, project.id, canonical_binding)
    run = await reader.get(LlmRunModel, UUID(str(source_llm_run_id)))
    _require(
        run is not None
        and run.project_id == project.id
        and run.logical_role == "planner"
        and run.prompt_template == "planner_cast_spec"
        and run.prompt_version == request.prompt_version,
        "requested_generation_mismatch",
    )
    original = retained._retained_request(settings, run)
    old_hash = (run.metadata_json or {}).get("input_hash")
    # The unchanged exact reader proves ORIGINAL paid identity, physical files,
    # original ownership, all actual attempts, root/transport and current schema.
    proven = await _find(
        session,
        settings,
        project=project,
        kind=kind,
        current_request=original,
        planner_input_hash=old_hash,
        canonical_binding=canonical_binding,
        validator=validator,
        source_llm_run_id=run.id,
    )
    for key in (
        "project_id",
        "logical_role",
        "system_prompt",
        "prompt_template",
        "prompt_version",
        "model_tier",
        "model_catalog_key",
        "temperature_override",
        "max_tokens_override",
        "cache_system",
        "tools",
        "tool_choice",
    ):
        _require(getattr(original, key) == getattr(request, key), "current_request_changed")
    if original.user_prompt == request.user_prompt:
        return await _find(
            session,
            settings,
            project=project,
            kind=kind,
            current_request=request,
            planner_input_hash=planner_input_hash,
            canonical_binding=canonical_binding,
            validator=validator,
            source_llm_run_id=run.id,
        )
    catalog, catalog_sha = retained._fresh_catalog()
    role = retained._fresh_role(settings, fresh_project, request, catalog)
    _require(
        retained._fresh_role(settings, fresh_project, original, catalog) == role,
        "physical_role_changed",
    )
    effective_old = llm._effective_input_payload(original, role)
    effective_current = llm._effective_input_payload(request, role)
    _require(
        {key: value for key, value in effective_old.items() if key != "messages"}
        == {key: value for key, value in effective_current.items() if key != "messages"},
        "compatibility_effective_parameters_changed",
    )
    current_hash = llm._effective_input_hash(request, role)
    compatibility = _cast_dependency_compatibility(
        original,
        request,
    )

    async def assert_current_unspent():
        _require(
            not await reader.rows(
                ExecutionOperationModel,
                ExecutionOperationModel.scope_id == scope,
                ExecutionOperationModel.input_hash == current_hash,
            ),
            "current_matching_operation_conflict",
        )
        rows = await reader.rows(
            LlmRunModel,
            LlmRunModel.project_id == project.id,
            LlmRunModel.logical_role == "planner",
            LlmRunModel.prompt_template == request.prompt_template,
        )
        _require(
            not any(
                (
                    ((row.metadata_json or {}).get("execution_receipt") or {}).get("scope_id")
                    in (None, scope)
                )
                and (
                    row.prompt_hash == current_hash
                    or (
                        row.prompt_version == request.prompt_version
                        and (row.metadata_json or {}).get("input_hash") == planner_input_hash
                    )
                )
                for row in rows
            ),
            "current_matching_generation_conflict",
        )

    await assert_current_unspent()
    # Fence all proofs again after awaited work. Original producer hashes stay
    # original: these re-reads must not be redirected to the current hash.
    final = await _find(
        session,
        settings,
        project=project,
        kind=kind,
        current_request=original,
        planner_input_hash=old_hash,
        canonical_binding=canonical_binding,
        validator=validator,
        source_llm_run_id=run.id,
    )
    _require(final.proof == proven.proof, "compatibility_original_proof_changed")
    final_project, final_scope, final_sha = await _canonical(reader, project.id, canonical_binding)
    final_catalog, final_catalog_sha = retained._fresh_catalog()
    _require(
        final_scope == scope
        and final_sha == canonical_sha
        and final_catalog_sha == catalog_sha
        and retained._fresh_role(settings, final_project, request, final_catalog) == role,
        "compatibility_source_changed",
    )
    await assert_current_unspent()
    proof = {
        **proven.proof,
        **compatibility,
        "original_effective_input_sha256": proven.proof["effective_input_sha256"],
        "current_effective_input_sha256": current_hash,
        "original_planner_input_sha256": proven.proof["planner_input_sha256"],
        "current_planner_input_sha256": planner_input_hash,
        "planner_hash_provenance": "original_llm_row_and_current_caller",
        "candidate_reuse_only": True,
        "credit_released": False,
        "new_allowance_granted": False,
    }
    return RetainedPlanningGeneration(
        proven.payload,
        proven.completion,
        proven.original_llm_run_id,
        proven.original_workflow_run_id,
        proven.submission_receipt,
        proof,
    )

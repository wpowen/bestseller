"""Exact reconstructed request purpose; NULL attribution and spend stay intact.

The legacy request has no retained message file. A known forensic renderer must
reproduce its ENTIRE effective input hash and all original financial/transport
witnesses must agree. This proves purpose only, not a new source, grade or draw.
"""

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from bestseller.infra.db.models import ProjectModel
from bestseller.services import llm
from bestseller.services.commercial_protocol_transition_runtime import _row
from bestseller.services.commercial_request_purpose import (
    _execution,
    _effective,
    _no_commercial_markers,
)
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "legacy-beat-request-purpose.v1"
INFO = "verified_legacy_noncommercial_requests"


def _need(ok, reason):
    if not ok:
        raise ValueError("legacy_request_purpose:" + reason)


def _data(value):
    return deepcopy(dict(value)) if isinstance(value, Mapping) else _row(value)


def verify_witnesses(
    *, operation, actual_attempts, original_runs, transport_results, request, role, source_artifacts
):
    """Pure original witnesses plus a fully reconstructed effective request."""
    root = _data(operation)
    runs = [_data(r) for r in original_runs]
    attempts = [_data(a) for a in actual_attempts]
    transports = [_data(o) for o in transport_results]
    _need(len(runs) == len(attempts) == len(transports) == 1, "complete_single_send")
    run, attempt, transport = runs[0], attempts[0], transports[0]
    scope, key, digest = root["scope_id"], root["key"], root["input_hash"]
    _need(root["status"] == "completed" and attempt["status"] == "completed", "settled_status")
    _need(
        attempt["scope_id"] == scope
        and attempt["operation_key"] == key
        and attempt["input_hash"] == digest,
        "attempt_binding",
    )
    _need(
        run.get("project_id") == str(request.project_id)
        and run.get("logical_role") == "planner"
        and all(
            run.get(k) is None
            for k in ("workflow_run_id", "step_run_id", "prompt_template", "prompt_version")
        ),
        "original_null_route",
    )
    _need((run.get("metadata_json") or {}).get("task") == "fanqie_beat_sheet", "producer_context")
    _need(
        request.logical_role == "planner"
        and request.workflow_run_id is None
        and request.step_run_id is None
        and request.prompt_template is None
        and request.prompt_version is None
        and request.tools is None
        and request.metadata.get("task") == "fanqie_beat_sheet",
        "reconstruction_route",
    )
    _need(
        llm._effective_input_hash(request, role) == digest == run["prompt_hash"],
        "entire_effective_request",
    )
    _need(run["model_name"] == role.model == attempt["model_name"], "model")
    result = root["result_json"]
    t = transport["result_json"]
    _need(
        transport["scope_id"] == scope
        and transport["input_hash"] == digest
        and transport["status"] == "transport_result"
        and transport["key"]
        == hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt['id']}".encode()).hexdigest()
        and t.get("schema") == "transport-result.v1"
        and t.get("operation_key") == key
        and t.get("attempt_id") == attempt["id"]
        and t.get("input_hash") == digest,
        "transport_binding",
    )
    returned = t["result"]
    _no_commercial_markers({k: v for k, v in root.items() if k != "content"})
    _no_commercial_markers({k: v for k, v in run.items() if k != "content"})
    _no_commercial_markers(transport)
    for field in (
        "content",
        "provider",
        "model_name",
        "tool_calls",
        "raw_message",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
    ):
        _need(
            content_hash(result.get(field)) == content_hash(returned.get(field)),
            "physical_return:" + field,
        )
    _need(
        result.get("fallback_used") is False
        and result.get("provider") == run["provider"] == llm._provider_from_model(role.model)
        and result.get("finish_reason") == "stop" == run["finish_reason"]
        and result.get("model_name") == role.model
        and not result.get("tool_calls"),
        "paid_result",
    )
    _need(
        type(run["input_tokens"]) is int
        and type(run["output_tokens"]) is int
        and type(result.get("input_tokens")) is int
        and type(result.get("output_tokens")) is int
        and run["input_tokens"] >= 0
        and run["output_tokens"] >= 0
        and result.get("input_tokens") == run["input_tokens"]
        and result.get("output_tokens") == run["output_tokens"]
        and type(attempt["actual_tokens"]) is int
        and attempt["actual_tokens"] == run["input_tokens"] + run["output_tokens"],
        "actual_usage",
    )
    expected_origin = {
        "schema": "paid-call-origin.v1",
        "status": "recorded",
        "project_id": str(request.project_id),
        "workflow_run_id": None,
        "step_run_id": None,
        "logical_role": "planner",
        "prompt_template": None,
        "prompt_version": None,
        "prompt_hash": digest,
    }
    for metadata in (result.get("metadata"), returned.get("metadata"), run.get("metadata_json")):
        _need(isinstance(metadata, Mapping), "metadata")
        _effective(metadata, digest, role.model)
        _execution(metadata, scope, key, {attempt["id"]: attempt})
        origin = metadata.get("paid_call_origin") or {}
        _need(
            all(content_hash(origin.get(k)) == content_hash(v) for k, v in expected_origin.items()),
            "origin",
        )
        _need(set(expected_origin) <= set(origin), "origin_missing_fields")
    return {
        "schema": SCHEMA,
        "purpose": "known_legacy_beat_sheet_planning",
        "operation_key": key,
        "scope_id": scope,
        "llm_run_id": run["id"],
        "input_sha256": digest,
        "request_reconstructed": True,
        "physical_request_file_retained": False,
        "effective_payload_sha256": content_hash(llm._effective_input_payload(request, role)),
        "original_rows_sha256": {
            "operation": content_hash(root),
            "attempt": content_hash(attempt),
            "transport": content_hash(transport),
            "run": content_hash(run),
        },
        "reconstruction_sources": deepcopy(source_artifacts),
        "original_null_attribution_preserved": True,
        "source_authority_granted": False,
        "grade_granted": False,
        "unused_credit_asserted": False,
        "credit_granted": False,
    }


async def prove_unlabelled_beatsheet_request(
    session, settings, *, operation, actual_attempts, original_runs, transport_results
):
    """Read-only purpose proof; no provider, flush, commit or inferred owners."""
    if settings is None or len(original_runs) != 1:
        return None
    run = original_runs[0]
    if (
        run.logical_role != "planner"
        or run.project_id is None
        or run.prompt_template is not None
        or run.prompt_version is not None
        or run.workflow_run_id is not None
        or (run.metadata_json or {}).get("task") != "fanqie_beat_sheet"
    ):
        return None
    from bestseller.services.foundation_selection import load_selected_foundation_artifacts
    from bestseller.services.legacy_fanqie_beat_request import build_legacy_request
    from bestseller.services.retained_review_input_proof import _fresh_catalog, _fresh_role

    with session.no_autoflush:
        project = await session.get(ProjectModel, run.project_id)
        _need(project is not None, "project")
        sources = await load_selected_foundation_artifacts(
            session, project=project, settings=settings
        )
        _need(sources is not None, "selected_sources")
        request = build_legacy_request(
            project=project,
            premise=project.metadata_json["premise"],
            book_spec=sources["book_spec"].content,
            cast_spec=sources["cast_spec"].content,
        )
        catalog, _ = _fresh_catalog()
        role = _fresh_role(settings, project, request, catalog)
        proof = verify_witnesses(
            operation=operation,
            actual_attempts=actual_attempts,
            original_runs=original_runs,
            transport_results=transport_results,
            request=request,
            role=role,
            source_artifacts={
                k: {"id": str(row.id), "content_sha256": content_hash(row.content)}
                for k, row in sources.items()
            },
        )
        saved = session.info.setdefault(INFO, {})
        saved[operation.key] = proof
        return proof

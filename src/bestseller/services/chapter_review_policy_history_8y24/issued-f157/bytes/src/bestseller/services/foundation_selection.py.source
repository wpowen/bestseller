"""Select one completed foundation cohort; never infer a cohort from latest rows.

Reads do not flush. Pinning only changes project runtime metadata; the caller
owns locking, persistence and subsequent materialization/physical paid proofs.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import AppSettings

SCHEMA = "foundation-selection.v1"
KINDS = ("book_spec", "world_spec", "cast_spec", "volume_plan")
WORKFLOW_TYPES = frozenset({"generate_foundation_plan", "generate_novel_plan", "complete_foundation_revision"})
POINTER = "active_foundation_selection"
HISTORY = "deterministic_audit_foundation_selection"
CAST_APPROVAL_FIELDS = (
    "source_bound_expansion",
    "source_bound_expansion_draft_eligible",
    "source_bound_expansion_review",
)


def _require(ok: object, code: str) -> None:
    if not ok:
        raise ValueError("foundation_selection_" + code)


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("foundation_selection_invalid_id") from exc


def _asset_meta(content: object, kind: str) -> list[dict]:
    if kind != "volume_plan":
        _require(isinstance(content, dict) and isinstance(content.get("_meta"), dict), "asset_meta")
        return [content["_meta"]]
    rows = (
        content
        if isinstance(content, list)
        else content.get("volumes")
        if isinstance(content, dict)
        else None
    )
    _require(isinstance(rows, list) and bool(rows), "volume_shape")
    _require(
        all(isinstance(row, dict) and isinstance(row.get("_meta"), dict) for row in rows),
        "volume_meta",
    )
    metas = [row["_meta"] for row in rows]
    if isinstance(content, dict) and "_meta" in content:
        _require(isinstance(content["_meta"], dict), "volume_meta")
        metas.append(content["_meta"])
    return metas


def _strict_decoder() -> json.JSONDecoder:
    def unique(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise ValueError("foundation_selection_nonfinite_json")

    def finite(value: str) -> float:
        parsed = float(value)
        _require(math.isfinite(parsed), "nonfinite_json")
        return parsed

    return json.JSONDecoder(object_pairs_hook=unique, parse_constant=nonfinite, parse_float=finite)


def _physical_file(settings: AppSettings, run: LlmRunModel, kind: str) -> tuple[dict, str]:
    _require(settings.artifact_store.mode == "local", "physical_store")
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    ref = getattr(run, kind + "_payload_ref")
    _require(isinstance(ref, str) and bool(ref), "physical_ref")
    path = (root / ref).resolve()
    _require(path.is_relative_to(root), "physical_path")
    raw = path.read_bytes()
    value = _strict_decoder().decode(raw.decode("utf-8"))
    _require(
        isinstance(value, dict)
        and value.get("schema_version") == "llm-payload.v1"
        and value.get("kind") == kind
        and value.get("replayable") is True,
        "physical_replayability",
    )
    for key, expected in (
        ("llm_run_id", str(run.id)),
        ("workflow_run_id", str(run.workflow_run_id)),
        ("project_id", str(run.project_id)),
        ("logical_role", run.logical_role),
    ):
        _require(value.get(key) == expected, "physical_identity")
    return value, hashlib.sha256(raw).hexdigest()


def _row_hash(row: object) -> str:
    return content_hash({c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs})


def _expand_projection(value: object, document: dict, trail: tuple = ()) -> object:
    if isinstance(value, dict):
        if (
            set(value) == {"$ref"}
            and isinstance(value["$ref"], str)
            and value["$ref"].startswith("#/")
        ):
            ref = value["$ref"]
            _require(ref not in trail and len(trail) < 100, "projection_reference_cycle")
            target = document
            for token in ref[2:].split("/"):
                _require(re.search(r"~(?![01])", token) is None, "projection_pointer")
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(target, list):
                    _require(
                        token.isascii()
                        and token.isdecimal()
                        and str(int(token)) == token
                        and int(token) < len(target),
                        "projection_pointer",
                    )
                    target = target[int(token)]
                else:
                    _require(isinstance(target, dict) and token in target, "projection_pointer")
                    target = target[token]
            return _expand_projection(target, document, (*trail, ref))
        return {key: _expand_projection(item, document, trail) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_projection(item, document, trail) for item in value]
    return value


def _cast_approval(cast: dict, world: dict) -> dict:
    from bestseller.services import planner

    meta = cast["_meta"]
    report = meta.get("source_bound_expansion_review_receipt")
    candidate_hash = planner._approved_payload_hash(
        {
            "world_spec": planner._without_planning_meta(world),
            "cast_spec": planner._without_planning_meta(cast),
        }
    )
    _require(
        meta.get("source_bound_expansion") == "reviewed"
        and meta.get("source_bound_expansion_review") == "passed"
        and meta.get("source_bound_expansion_draft_eligible") is True
        and isinstance(report, dict)
        and report.get("decision") == "pass"
        and report.get("reason") == "reviewed"
        and report.get("core_preserved") is True
        and report.get("core_conflicts") == []
        and report.get("draft_eligible") is True
        and report.get("evidence_verified") is True
        and planner._source_bound_report_is_draft_eligible(report)
        and report.get("candidate_hash") == report.get("candidate_hash_echo") == candidate_hash,
        "cast_approval",
    )
    _uuid(report.get("llm_run_id"))
    attempts = report.get("review_attempts")
    _require(
        isinstance(attempts, list) and bool(attempts) and isinstance(attempts[-1], dict),
        "cast_approval_attempt",
    )
    last = attempts[-1]
    _require(
        last.get("llm_run_id") == report["llm_run_id"]
        and last.get("fallback_used") is False
        and last.get("raw_response_hash") == report.get("raw_response_hash")
        and isinstance(report.get("raw_response_hash"), str)
        and len(report["raw_response_hash"]) == 64
        and report.get("provider") == last.get("provider")
        and report.get("provider") not in {None, "", "mock", "fallback", "fixture", "test"}
        and report.get("finish_reason") == last.get("finish_reason")
        and report.get("finish_reason") in {"stop", "end_turn", "completed"},
        "cast_approval_attempt",
    )
    return {
        "receipt_sha256": content_hash(report),
        "candidate_hash": candidate_hash,
        "llm_run_id": report["llm_run_id"],
        "existing_approval_only": True,
    }


async def _volume_paid_proof(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    volume_meta: list[dict],
    volume: PlanningArtifactVersionModel,
) -> tuple[object, dict]:
    from bestseller.services import llm
    from bestseller.services import retained_review_input_proof as proof
    from bestseller.services.source_bound_planning_submission import (
        VOLUME_NATIVE_POLICY,
        parse_planning_submission,
    )

    run_ids = {record.get("generation_llm_run_id") for record in volume_meta}
    _require(len(run_ids) == 1, "volume_generation_ids")
    run = await session.get(LlmRunModel, _uuid(next(iter(run_ids))))
    _require(
        run is not None
        and run.project_id == project.id
        and run.logical_role == "planner"
        and run.prompt_template == "planner_volume_plan"
        and run.prompt_version == "v4-volume-native"
        and run.workflow_run_id == volume.source_run_id,
        "volume_generation_binding",
    )
    owner = await session.get(WorkflowRunModel, run.workflow_run_id)
    _require(
        owner is not None
        and owner.project_id == project.id
        and owner.workflow_type in WORKFLOW_TYPES,
        "volume_generation_owner",
    )
    request_json, request_sha = _physical_file(settings, run, "request")
    response, response_sha = _physical_file(settings, run, "response")
    request = proof._retained_request(settings, run)
    catalog, catalog_sha = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    digest = llm._effective_input_hash(request, role)
    scope = (project.metadata_json or {}).get("execution_scope_id")
    _require(isinstance(scope, str) and bool(scope), "scope_missing")
    paid, key = await proof._physical_return(
        session, run, scope=scope, expected_hash=digest, expected_model=role.model
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key == key)
        )
    )
    claimed = (run.metadata_json or {}).get("execution_receipt", {}).get("attempts", [])
    _require(
        len(attempts) == len(claimed) == 1
        and {a.id for a in attempts} == {a.get("attempt_id") for a in claimed}
        and all(a.status == "completed" for a in attempts),
        "volume_attempt_set",
    )
    text = response.get("content", {})
    _require(
        text.get("truncated") is False
        and text.get("text") == paid.content
        and text.get("original_chars") == len(paid.content),
        "volume_physical_content",
    )
    for field in ("tool_calls", "raw_message"):
        value = getattr(paid, field)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        record = (response.get("structured_payload_receipts") or {}).get(field, {})
        _require(
            response.get(field) == value
            and record.get("status") == "retained"
            and record.get("encoding") == "json-utf8"
            and record.get("truncated") is False
            and record.get("original_chars") == len(encoded)
            and type(record.get("limit_chars")) is int
            and record["limit_chars"] >= len(encoded)
            and record.get("sha256") == hashlib.sha256(encoded.encode()).hexdigest(),
            "volume_physical_structured",
        )
    for field in (
        "provider",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
    ):
        _require(response.get(field) == getattr(paid, field), "volume_physical_response")
    _require(
        response.get("model") == paid.model_name and response.get("tool_call_count") == 1,
        "volume_physical_response",
    )
    payload, _, _ = parse_planning_submission(
        paid, kind="volume_plan", version=VOLUME_NATIVE_POLICY
    )
    stored = volume.content if isinstance(volume.content, list) else volume.content["volumes"]
    # Only each volume's root producer receipt is absent from the paid story.
    _require(
        content_hash(payload)
        == content_hash([{k: v for k, v in row.items() if k != "_meta"} for row in stored]),
        "volume_paid_story",
    )
    operation = await session.get(ExecutionOperationModel, key)
    root_metadata = (operation.result_json or {}).get("metadata") or {}
    _require(
        root_metadata.get("paid_call_origin") == paid.metadata.get("paid_call_origin")
        and (root_metadata.get("effective_input_receipt") or {}).get("hash") == digest,
        "volume_root_origin",
    )
    transport_key = hashlib.sha256(
        f"transport-result.v1\n{scope}\n{attempts[0].id}".encode()
    ).hexdigest()
    transport = await session.get(ExecutionOperationModel, transport_key)
    return request, {
        "llm_run_id": str(run.id),
        "request_file_sha256": request_sha,
        "response_file_sha256": response_sha,
        "effective_input_sha256": digest,
        "catalog_sha256": catalog_sha,
        "scope_id": scope,
        "root_row_sha256": _row_hash(operation),
        "transport_row_sha256": _row_hash(transport),
        "attempt_rows_sha256": content_hash([_row_hash(a) for a in attempts]),
        "llm_row_sha256": _row_hash(run),
        "physical_request_sha256": content_hash(request_json),
    }


async def _historical_volume_compatibility(
    session: AsyncSession,
    settings: AppSettings | None,
    project: ProjectModel,
    artifacts: dict[str, PlanningArtifactVersionModel],
    projection: list[dict],
    metas: list[dict],
) -> dict:
    from bestseller.services import planner
    from bestseller.services import source_bound_planning_content as content

    _require(settings is not None, "volume_compatibility_settings_required")
    request, paid_proof = await _volume_paid_proof(
        session, settings, project, metas, artifacts["volume_plan"]
    )
    outer, _ = _strict_decoder().raw_decode(request.user_prompt.lstrip())
    logical = content.decode_source_bound_planning_document(outer)
    _require(
        logical.get("protocol") == content.SOURCE_BOUND_PLANNING_PROTOCOL
        and logical.get("kind") == "volume_plan",
        "volume_logical_contract",
    )
    old_assets = logical.get("existing_planning_assets")
    _require(
        isinstance(old_assets, dict) and set(old_assets) == set(KINDS[:3]), "volume_dependencies"
    )
    old_projection = _expand_projection(logical.get("projection"), logical)
    old_hash = planner._approved_payload_hash(old_projection)
    _require(
        all(record.get("source_projection_sha256") == old_hash for record in metas),
        "volume_original_projection",
    )
    sources = logical.get("approved_sources") or {}
    _require(
        sources.get("canonical_snapshot") == project._source_bound_planning_canonical,
        "volume_original_canonical",
    )
    _require(
        _expand_projection(logical.get("approved_premise"), logical)
        == project.metadata_json["premise"],
        "volume_original_premise",
    )
    normalized_hashes = {}
    for kind in KINDS[:3]:
        old = deepcopy(old_assets[kind])
        new = content._dependency_projection(artifacts[kind].content)
        _require(
            isinstance(old, dict) and isinstance(old.get("_meta"), dict), "volume_dependency_shape"
        )
        if kind == "cast_spec":
            _require(
                old["_meta"].get("source_bound_expansion") == "derived_pending_review"
                and old["_meta"].get("source_bound_expansion_review")
                == "independent_semantic_review_required"
                and "source_bound_expansion_draft_eligible" not in old["_meta"],
                "volume_old_cast_state",
            )
            for field in CAST_APPROVAL_FIELDS:
                old["_meta"].pop(field, None)
                new["_meta"].pop(field, None)
        _require(content_hash(old) == content_hash(new), "volume_dependency_changed")
        normalized_hashes[kind] = content_hash(old)
    approval = _cast_approval(artifacts["cast_spec"].content, artifacts["world_spec"].content)
    reconstructed = planner._compile_source_bound_volume_plan(
        project,
        project.metadata_json["premise"],
        *(deepcopy(old_assets[kind]) for kind in KINDS[:3]),
    )
    _require(content_hash(reconstructed) == old_hash, "volume_original_sources_changed")
    adjusted = deepcopy(old_projection)
    _require(
        isinstance(adjusted, list)
        and isinstance(projection, list)
        and len(adjusted) == len(projection),
        "volume_projection_shape",
    )
    changed = []
    for index, (old, new) in enumerate(zip(adjusted, projection, strict=True)):
        _require(
            isinstance(old, dict)
            and isinstance(old.get("_meta"), dict)
            and old["_meta"].get("cast_spec_hash") == content._hash(old_assets["cast_spec"])
            and new["_meta"].get("cast_spec_hash")
            == content._hash(content._dependency_projection(artifacts["cast_spec"].content)),
            "volume_cast_dependency_hash",
        )
        old["_meta"]["cast_spec_hash"] = new["_meta"]["cast_spec_hash"]
        changed.append(f"/{index}/_meta/cast_spec_hash")
    _require(content_hash(adjusted) == content_hash(projection), "volume_projection_other_change")
    return {
        "schema": "foundation-volume-approval-metadata-compatibility.v1",
        "paid_proof": paid_proof,
        "original_projection_sha256": old_hash,
        "current_projection_sha256": content_hash(projection),
        "normalized_dependency_sha256": normalized_hashes,
        "changed_projection_paths": changed,
        "allowed_cast_root_meta_fields": list(CAST_APPROVAL_FIELDS),
        "current_cast_approval": approval,
        "original_logical_document_sha256": content_hash(logical),
        "author_authority": False,
        "grade_asserted": False,
        "credit_granted": False,
    }


async def _select(
    session: AsyncSession,
    project: ProjectModel,
    workflow_id: UUID,
    settings: AppSettings | None = None,
) -> tuple[dict, dict[str, PlanningArtifactVersionModel]]:
    # Imports are lazy: selecting/reading has no provider or global-pool setup.
    from bestseller.services import planner

    metadata = project.metadata_json
    _require(isinstance(metadata, dict), "project_metadata")
    workflow = await session.get(WorkflowRunModel, _uuid(workflow_id))
    _require(
        workflow is not None
        and workflow.project_id == project.id
        and workflow.workflow_type in WORKFLOW_TYPES
        and workflow.status == "completed"
        and workflow.scope_type == "project"
        and workflow.scope_id == project.id,
        "completed_workflow",
    )
    mapping = (workflow.metadata_json or {}).get("artifact_ids")
    _require(
        isinstance(mapping, dict) and all(kind in mapping for kind in KINDS),
        "workflow_artifact_ids",
    )
    ids = {kind: _uuid(mapping[kind]) for kind in KINDS}
    _require(len(set(ids.values())) == len(KINDS), "duplicate_artifact_ids")
    artifacts = {}
    for kind, identity in ids.items():
        artifact = await session.get(PlanningArtifactVersionModel, identity)
        _require(
            artifact is not None
            and artifact.id == identity
            and artifact.project_id == project.id
            and artifact.artifact_type == kind
            and artifact.status == "approved"
            and artifact.scope_ref_id is None,
            "artifact_binding",
        )
        artifacts[kind] = artifact

    # Only a detached copy may run the existing snapshot builder (it can repair
    # legacy metadata). Any changed snapshot is a failure, not an implicit pin.
    detached = SimpleNamespace(
        **{
            key: deepcopy(getattr(project, key, None))
            for key in (
                "id",
                "slug",
                "title",
                "genre",
                "sub_genre",
                "language",
                "target_chapters",
                "target_word_count",
                "metadata_json",
                "audience",
            )
        }
    )
    stored_snapshot = metadata.get("book_design_snapshot")
    _require(isinstance(stored_snapshot, dict), "book_design_missing")
    asset_meta = planner.planning_artifact_meta(detached)
    _require(
        detached.metadata_json.get("book_design_snapshot") == stored_snapshot
        and asset_meta.get("source_snapshot_hash") == metadata.get("book_design_snapshot_hash")
        and asset_meta.get("source_snapshot_id") == stored_snapshot.get("snapshot_id")
        and asset_meta.get("source_snapshot_hash") == stored_snapshot.get("source_hash"),
        "book_design_binding",
    )
    lineage = {
        key: asset_meta[key]
        for key in (
            "source_snapshot_id",
            "source_snapshot_hash",
            "source_snapshot_version",
        )
    }
    if "book_design_snapshot_id" in metadata:
        _require(
            metadata["book_design_snapshot_id"] == lineage["source_snapshot_id"],
            "book_design_binding",
        )
    _require(metadata.get("conception_approved") is True, "canonical_approval")
    canonical = await session.get(
        PlanningArtifactVersionModel, _uuid(metadata.get("creation_canonical_snapshot_id"))
    )
    _require(
        canonical is not None
        and canonical.project_id == project.id
        and canonical.artifact_type == "conception_snapshot"
        and canonical.status == "canonical"
        and canonical.scope_ref_id is None
        and isinstance(canonical.content, dict)
        and snapshot_hash(canonical.content) == metadata.get("creation_canonical_snapshot_hash"),
        "canonical_binding",
    )
    design_contract = (stored_snapshot.get("approved_story_design") or {}).get(
        "source_contract_hash"
    )
    if design_contract:
        _require(
            (canonical.content.get("concept_contract") or {}).get("input_hash") == design_contract,
            "canonical_contract",
        )
    detached._source_bound_planning_canonical = {
        "id": str(canonical.id),
        "content_hash": snapshot_hash(canonical.content),
        "content": deepcopy(canonical.content),
    }
    from bestseller.services.retained_planning_clauses import (
        validate_retained_planning_clauses, validate_planning_chapter_references,
    )
    # An approved legacy row is not evidence that its story still fits the
    # current creator revision. Apply the same source gate before consumption.
    for kind, artifact in artifacts.items():
        validate_retained_planning_clauses(artifact.content, project=detached, kind=kind)
        validate_planning_chapter_references(artifact.content, target_chapters=project.target_chapters)
    revision_compatibility = None
    reviewed_edit = None
    consumer_kind = (workflow.metadata_json or {}).get('foundation_consumer_kind')
    _require(consumer_kind is not None or 'verified_edit_binding' not in (workflow.metadata_json or {}),
             'unsupported_consumer_kind')
    if consumer_kind is not None:
        from bestseller.services.foundation_volume_edit import CONSUMER_KIND, validate_reviewed_volume_foundation
        _require(workflow.workflow_type == 'complete_foundation_revision' and consumer_kind == CONSUMER_KIND,
                 'unsupported_consumer_kind')
        _require(settings is not None, 'revision_settings_required')
        reviewed_edit = await validate_reviewed_volume_foundation(
            session, settings, project=project, workflow=workflow, artifacts=artifacts)
    if workflow.workflow_type == "complete_foundation_revision" and consumer_kind is None:
        _require(settings is not None, "revision_settings_required")
        from bestseller.services.foundation_revision_selection import validate_reviewed_foundation_revision
        revision_compatibility = await validate_reviewed_foundation_revision(
            session, settings, project=project, workflow=workflow, artifacts=artifacts)
    metas = {kind: _asset_meta(artifact.content, kind) for kind, artifact in artifacts.items()}
    for kind, records in metas.items():
        for record in records:
            if kind != "volume_plan" or any(key in record for key in lineage):
                _require(
                    all(record.get(key) == value for key, value in lineage.items()),
                    "asset_snapshot_binding",
                )

    premise = metadata.get("premise")
    _require(isinstance(premise, str) and bool(premise.strip()), "premise_missing")
    projection = planner._compile_source_bound_volume_plan(
        detached,
        premise,
        *(deepcopy(artifacts[kind].content) for kind in KINDS[:3]),
    )
    projection_hash = planner._approved_payload_hash(projection)
    compatibility = None
    if reviewed_edit is None and any('field_recovery_receipt' in record for record in metas['volume_plan']):
        _require(settings is not None, 'field_recovery_settings_required')
        from bestseller.services.foundation_field_recovery import verify_recovered_volume
        compatibility = await verify_recovered_volume(
            session,settings,project=detached,artifacts=artifacts,projection=projection)
    elif reviewed_edit is None and not all(
        record.get("source_projection_sha256") == projection_hash for record in metas["volume_plan"]
    ):
        compatibility = await _historical_volume_compatibility(
            session,
            settings,
            detached,
            artifacts,
            projection,
            metas["volume_plan"],
        )
    receipt = {
        "schema": SCHEMA,
        "project_id": str(project.id),
        "workflow_id": str(workflow.id),
        "workflow_type": workflow.workflow_type,
        "status": "completed",
        "workflow_artifact_ids_sha256": content_hash(mapping),
        "artifact_ids": {kind: str(identity) for kind, identity in ids.items()},
        "artifact_content_hashes": {
            kind: content_hash(artifact.content) for kind, artifact in artifacts.items()
        },
        "canonical_source": {
            "id": str(canonical.id),
            "content_hash": snapshot_hash(canonical.content),
            "status": canonical.status,
        },
        "book_design_source": lineage,
        "volume_projection_sha256": projection_hash,
        "derived_compatibility": compatibility,
        **({"reviewed_volume_edit": reviewed_edit} if reviewed_edit else {}),
        **({"reviewed_source_revision": revision_compatibility} if revision_compatibility else {}),
        "author_authority": False,
        "grade_asserted": False,
        "credit_granted": False,
    }
    return receipt, artifacts


async def select_completed_foundation(
    session: AsyncSession,
    *,
    project: ProjectModel,
    workflow_id: UUID,
    settings: AppSettings | None = None,
) -> dict:
    """Read and validate exact four IDs from one completed consumer workflow."""
    with session.no_autoflush:
        receipt, _ = await _select(session, project, workflow_id, settings)
    return receipt


async def load_selected_foundation_artifacts(
    session: AsyncSession,
    *,
    project: ProjectModel,
    settings: AppSettings | None = None,
) -> dict[str, PlanningArtifactVersionModel] | None:
    """No pointer means legacy lookup; an invalid pointer never means latest."""
    metadata = project.metadata_json
    _require(isinstance(metadata, dict), "project_metadata")
    if POINTER not in metadata:
        return None
    saved = metadata[POINTER]
    _require(isinstance(saved, dict) and saved.get("schema") == SCHEMA, "pointer_invalid")
    # New derived consumers are DB-pinned cohorts; an externally removed or
    # switched pin must not be silently served from a cached project object.
    with session.no_autoflush:
        current_metadata = await session.scalar(select(ProjectModel.metadata_json).where(ProjectModel.id == project.id))
    current_saved = (current_metadata or {}).get(POINTER)
    if 'reviewed_volume_edit' in saved or (isinstance(current_saved,dict) and 'reviewed_volume_edit' in current_saved):
        _require(current_saved == saved, 'pointer_changed')
    with session.no_autoflush:
        current, artifacts = await _select(
            session, project, _uuid(saved.get("workflow_id")), settings
        )
    _require(saved == current, "pointer_changed")
    return artifacts


async def pin_completed_foundation(
    session: AsyncSession,
    *,
    project: ProjectModel,
    workflow_id: UUID,
    settings: AppSettings | None = None,
) -> dict:
    """Stage runtime selection/history only; never flush or commit the caller."""
    selected = await select_completed_foundation(
        session, project=project, workflow_id=workflow_id, settings=settings
    )
    metadata = deepcopy(project.metadata_json)
    if metadata.get(POINTER) == selected:
        return selected
    history = metadata.get(HISTORY, [])
    _require(isinstance(history, list), "history_invalid")
    history = deepcopy(history)
    if POINTER in metadata and (not history or history[-1] != metadata[POINTER]):
        history.append(deepcopy(metadata[POINTER]))
    if not history or history[-1] != selected:
        history.append(deepcopy(selected))
    metadata[POINTER] = deepcopy(selected)
    metadata[HISTORY] = history
    project.metadata_json = metadata
    return selected

# ruff: noqa: ANN001, ANN201, ANN202
"""Read-only native provenance seam; no dispatch, publication or upload authority.

The caller supplies a trusted fresh assembly and a mandatory async input factory
which re-reads current canonical/design/contracts. No uploaded bundle is accepted.
Consumer/validator are reviewed code dependencies, never artifact-selected code.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID

from sqlalchemy import inspect, or_, select

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)

ARTIFACT_TYPE = "fanqie_whole_semantic_review"


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _safe(value):
    if isinstance(value, (UUID, datetime, date, Decimal)):
        return str(value)
    raise TypeError("non_json_native_column")


def json_safe(value):
    return json.loads(json.dumps(value, default=_safe, ensure_ascii=False, allow_nan=False))


def equal(left, right):
    def encoded(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)

    return encoded(left) == encoded(right)


async def _rows(session, model, *criteria):
    # Column SELECTs bypass stale identity-map objects without refreshing over
    # caller-owned dirty objects. No ORM row loading, autoflush, lock or write.
    columns = [getattr(model, prop.key).label(prop.key) for prop in inspect(model).column_attrs]
    result = await session.execute(
        select(*columns).where(*criteria).execution_options(autoflush=False)
    )
    return [dict(row) for row in result.mappings()]


async def _one(session, model, *criteria):
    rows = await _rows(session, model, *criteria)
    need(len(rows) == 1, "native_row_missing_or_ambiguous:" + model.__tablename__)
    return rows[0]


def _read_file(local_dir, ref, run_id, kind):
    """Walk every component with NOFOLLOW; read only the canonical native ref."""
    need(ref == f"runtime/llm_payloads/{run_id}.{kind}.json", "physical_ref_not_canonical")
    root = Path(local_dir).expanduser()
    need(".." not in root.parts, "physical_root_parent_traversal")
    if not root.is_absolute():
        root = Path.cwd() / root
    directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.parts[1:], "runtime", "llm_payloads"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            f"{run_id}.{kind}.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "rb") as stream:
            need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "physical_file_not_regular")
            return stream.read()
    finally:
        os.close(directory)


def _source_identities(manifest):
    # Native adopted-assembly naming -> v4 naming, without inventing adoption.
    result = []
    for row in manifest["sources"]:
        states = [
            value
            for value in (
                row.get("promotion_state"),
                (row.get("lineage") or {}).get("adoption_state"),
            )
            if value is not None
        ]
        need(
            bool(states) and all(value == "promoted" for value in states),
            "assembly_source_not_promoted",
        )
        result.append(
            (
                row["chapter"] if "chapter" in row else row["chapter_number"],
                str(row["draft_id"]),
                row["body_sha256"] if "body_sha256" in row else row["raw_body_sha256"],
            )
        )
    need(bool(result) and len(result) == len(set(result)), "assembly_source_inventory_invalid")
    return result


@dataclass(frozen=True)
class SemanticContext:
    current_inputs: dict
    evidence: dict


def _same_source(artifact, assembly, title):
    payload = (artifact["content"] or {}).get("request_payload")
    need(isinstance(payload, dict) and isinstance(payload.get("full_text"), str)
         and isinstance(payload.get("title"), str), "artifact_cannot_be_classified")
    return payload["full_text"] == assembly.full_text and payload["title"] == title


async def _protocol_parent(session, settings, project, assembly, parent_artifact_id,
                           *, semantic, consumer, child=None):
    from bestseller.services.fanqie_short_semantic_protocol_recovery import verify_parent_batch

    parent_id = UUID(str(parent_artifact_id))
    artifacts = await _rows(session, PlanningArtifactVersionModel,
                            PlanningArtifactVersionModel.project_id == project.id,
                            PlanningArtifactVersionModel.artifact_type == ARTIFACT_TYPE)
    parents = [a for a in artifacts if a["id"] == parent_id]
    need(len(parents) == 1, "protocol_parent_missing_or_foreign")
    parent = parents[0]
    need(_same_source(parent, assembly, project.title), "protocol_parent_source_mismatch")
    allowed = {parent_id} if child is None else {parent_id, child["id"]}
    for artifact in artifacts:
        recovery = (artifact["content"] or {}).get("protocol_recovery")
        if recovery is not None and recovery.get("artifact_id") == str(parent_id):
            need(child is not None and artifact["id"] == child["id"], "protocol_second_recovery")
        if _same_source(artifact, assembly, project.title):
            need(artifact["id"] in allowed, "protocol_extra_current_artifact")
    workflow = await _one(session, WorkflowRunModel, WorkflowRunModel.id == parent["source_run_id"])
    workflows = await _rows(session, WorkflowRunModel, WorkflowRunModel.project_id == project.id)
    allowed_workflows = {parent["source_run_id"]} if child is None else {parent["source_run_id"], child["source_run_id"]}
    for row in workflows:
        meta = row["metadata_json"] or {}
        recovery = meta.get("protocol_recovery")
        if recovery is not None and recovery.get("artifact_id") == str(parent_id):
            need(child is not None and row["id"] == child["source_run_id"], "protocol_second_recovery_workflow")
        manifest = meta.get("source_manifest") or {}
        if (row["workflow_type"] in {"original_four_whole_semantic_review", "fanqie_whole_semantic_review"}
                and manifest.get("assembly_sha256") == assembly.manifest["assembly_sha256"]
                and (manifest.get("project") or {}).get("title") == project.title):
            need(row["id"] in allowed_workflows, "protocol_extra_current_workflow")
    evidence, _ = await _load_artifact_native(session, settings, parent, workflow, consumer)
    proof = verify_parent_batch(json_safe(parent), json_safe(workflow), evidence,
                                consumer=consumer, semantic=semantic)
    need(equal(evidence["request_payload"]["manifest"], assembly.manifest), "protocol_parent_manifest_changed")
    proof["artifact_inventory"] = json_safe(artifacts)
    proof["workflow_inventory"] = json_safe(workflows)
    return parent, workflow, evidence, proof


async def build_protocol_recovery_parent(session, settings, project, assembly, parent_artifact_id):
    """Public preflight: SELECT/files only; requires an unconsumed failed v4 parent.

    Returns five JSON-safe descriptor fields. A caller must repeat this check in
    its serialized native execute preflight; this reader cannot reserve a slot.
    """
    from bestseller.services import fanqie_short_semantic_consumer as consumer
    from bestseller.services import fanqie_short_semantic_review as semantic

    with session.no_autoflush:
        stored = await _one(session, ProjectModel, ProjectModel.id == project.id)
        project = ProjectModel(**stored)
        _, _, _, proof = await _protocol_parent(
            session, settings, project, assembly, parent_artifact_id,
            semantic=semantic, consumer=consumer)
    return deepcopy(proof["descriptor"])


async def _load_artifact_native(session, settings, artifact, workflow, consumer):
    saved = artifact["content"]
    receipt = saved["provider_receipt"]
    run_id = UUID(receipt["llm_run_id"])
    need(str(run_id) == receipt["llm_run_id"], "artifact_run_id_not_canonical")
    run = await _one(session, LlmRunModel, LlmRunModel.id == run_id)
    need(
        run["project_id"] == artifact["project_id"] and run["workflow_run_id"] == workflow["id"],
        "llm_run_not_original_owner",
    )
    root_key = run["metadata_json"]["execution_receipt"]["operation_key"]
    root = await _one(session, ExecutionOperationModel, ExecutionOperationModel.key == root_key)
    attempts = await _rows(
        session, ExecutionAttemptModel, ExecutionAttemptModel.operation_key == root_key
    )
    budget = await _one(
        session, ExecutionBudgetModel, ExecutionBudgetModel.scope_id == root["scope_id"]
    )
    # Inventory every status, not a successful suffix or a latest attempt.
    ids = {row["id"] for row in attempts}
    related_keys = {
        hashlib.sha256(f"{kind}\n{root['scope_id']}\n{aid}".encode()).hexdigest()
        for aid in ids
        for kind in ("transport-result.v1", "transport-failure.v1", "provider-return.v1")
    }
    operations = await _rows(
        session,
        ExecutionOperationModel,
        or_(
            ExecutionOperationModel.scope_id == root["scope_id"],
            ExecutionOperationModel.key.in_(related_keys),
            ExecutionOperationModel.result_json["operation_key"].as_string() == root_key,
            ExecutionOperationModel.result_json["attempt_id"].as_string().in_(ids),
            ExecutionOperationModel.result_json["receipt"]["attempt_id"].as_string().in_(ids),
        ),
    )
    related = []
    for row in operations:
        if row["key"] == root_key:
            continue
        doc = row["result_json"] or {}
        belongs = (
            row["key"] in related_keys
            or doc.get("operation_key") == root_key
            or doc.get("attempt_id") in ids
            or (doc.get("receipt") or {}).get("attempt_id") in ids
        )
        if belongs:
            related.append(row)
        elif row["status"] in {
            "transport_result",
            "transport_failure_receipt",
            "unusable_provider_return",
            "reconciliation_receipt",
        }:
            need(
                bool(
                    doc.get("operation_key")
                    or doc.get("attempt_id")
                    or (doc.get("receipt") or {}).get("attempt_id")
                ),
                "unclassifiable_native_receipt",
            )
    need(
        root["status"] == "completed"
        and len(attempts) == 1
        and attempts[0]["status"] == "completed",
        "single_original_completed_send_required",
    )
    need(budget["status"] in {"active", "exhausted"}, "native_scope_not_settled")
    reconciliations = (budget["metadata_json"] or {}).get("reconciliations", [])
    need(
        not any(
            (row.get("receipt") or {}).get("attempt_id") in ids
            or row.get("operation_key") == root_key
            for row in reconciliations
        ),
        "native_reconciliation_present",
    )
    need(
        len(related) == 1 and related[0]["status"] == "transport_result",
        "native_transport_inventory_not_single_original",
    )
    completion = deepcopy(root["result_json"])
    need(
        isinstance(completion, dict) and saved["raw_response"] == completion.get("content"),
        "artifact_raw_response_not_native_content",
    )
    # Early native root writes legitimately precede final telemetry retention.
    # Fill only these two fields from actual persisted LlmRun, never flags.
    completion["llm_run_id"] = str(run["id"])
    completion["metadata"] = deepcopy(run["metadata_json"])
    need(
        equal(receipt["metadata"], completion["metadata"]),
        "artifact_provider_metadata_mismatch",
    )
    for key in (
        "llm_run_id",
        "provider",
        "model_name",
        "finish_reason",
        "fallback_used",
        "fallback_source",
    ):
        need(receipt.get(key) == completion.get(key), "artifact_provider_receipt_mismatch")
    files = {}
    for kind in ("request", "response"):
        ref = run[kind + "_payload_ref"]
        files[ref] = _read_file(settings.artifact_store.local_dir, ref, str(run_id), kind)
        consumer.strict_json(files[ref].decode("utf-8"))
    evidence = {
        "request_payload": deepcopy(saved["request_payload"]),
        "completion": json_safe(completion),
        "retained_payloads": deepcopy(saved["retained_payloads"]),
        "physical_files": files,
        "native": json_safe(
            {
                "llm_run": run,
                "operation": root,
                "attempts": attempts,
                "transport_results": related,
            }
        ),
    }
    return evidence, {"run": run, "run_id": run_id, "root_key": root_key, "budget": budget}


async def load_current_semantic_context(
    session,
    settings,
    *,
    project,
    assembly,
    title,
    original_report,
    current_inputs_factory,
    semantic,
    consumer,
):
    """Return fresh in-memory inputs/evidence plus JSON-safe audit references.

    `current_inputs_factory(session, fresh_project, assembly, source_run_id)` is
    mandatory, async, read-only, trusted integration code. It receives no saved
    payload/evidence. It must independently rebuild the complete prepare_review
    kwargs, including approval/canonical/design/contracts and original scopes.
    No artifact is None-success: absent/ambiguous/corrupt current evidence raises.
    """
    need(callable(current_inputs_factory), "fresh_current_inputs_factory_required")
    with session.no_autoflush:
        stored = await _one(session, ProjectModel, ProjectModel.id == project.id)
        fresh_project = ProjectModel(**stored)  # transient; never added or flushed
        body_hash = hashlib.sha256(assembly.full_text.encode()).hexdigest()
        manifest = assembly.manifest
        need(manifest["assembly_sha256"] == body_hash, "current_assembly_hash_mismatch")
        report = (
            original_report.to_dict() if hasattr(original_report, "to_dict") else original_report
        )
        report = json_safe(report)
        artifacts = await _rows(
            session,
            PlanningArtifactVersionModel,
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ARTIFACT_TYPE,
        )
        current = []
        for artifact in artifacts:
            payload = (artifact["content"] or {}).get("request_payload")
            need(isinstance(payload, dict), "artifact_cannot_be_classified")
            need(
                isinstance(payload.get("full_text"), str) and isinstance(payload.get("title"), str),
                "artifact_cannot_be_classified",
            )
            # Only proven historical body/title mismatch is ignorable. A current
            # body/title with stale binding/status/manifest must never disappear.
            if payload["full_text"] != assembly.full_text or payload["title"] != title:
                continue
            current.append(artifact)
        validated = [a for a in current if a["status"] == "validated"]
        need(len(validated) == 1, "current_semantic_artifact_missing_or_ambiguous")
        artifact = validated[0]
        saved = artifact["content"]
        recovery = saved.get("protocol_recovery")
        recovery_evidence = None
        if recovery is None:
            need(len(current) == 1, "current_artifact_not_validated_or_ambiguous")
        else:
            from bestseller.services.fanqie_short_semantic_protocol_recovery import DESCRIPTOR_KEYS
            need(isinstance(recovery, dict) and set(recovery) == DESCRIPTOR_KEYS, "protocol_descriptor_schema")
            need(len(current) == 2, "protocol_parent_inventory")
            parent, parent_workflow, parent_evidence, parent_proof = await _protocol_parent(
                session, settings, fresh_project, assembly, recovery["artifact_id"],
                semantic=semantic, consumer=consumer, child=artifact)
            need(equal(parent_proof["descriptor"], recovery), "protocol_parent_descriptor_mismatch")
            recovery_evidence = {
                "descriptor": deepcopy(recovery), "parent_artifact": json_safe(parent),
                "parent_workflow": json_safe(parent_workflow), "parent_evidence": parent_evidence,
                "child_artifact": json_safe(artifact),
                "artifact_inventory": parent_proof["artifact_inventory"],
                "workflow_inventory": parent_proof["workflow_inventory"],
            }
        need(
            saved.get("schema") == semantic.SCHEMA and "error" in saved and saved["error"] is None,
            "current_artifact_invalid",
        )
        need(artifact["source_run_id"] is not None, "artifact_source_workflow_missing")
        workflow = await _one(
            session, WorkflowRunModel, WorkflowRunModel.id == artifact["source_run_id"]
        )
        need(
            workflow["project_id"] == project.id and workflow["status"] == "completed",
            "source_workflow_not_completed_owner",
        )
        if recovery_evidence is not None:
            recovery_evidence["child_workflow"] = json_safe(workflow)
        else:
            need("protocol_recovery" not in (workflow["metadata_json"] or {}), "protocol_artifact_pointer_missing")
        inputs = deepcopy(
            await current_inputs_factory(
                session, fresh_project, assembly, artifact["source_run_id"]
            )
        )
        need(isinstance(inputs, dict), "fresh_inputs_required")
        if recovery_evidence is not None:
            actual_parent = deepcopy(recovery_evidence["descriptor"])
            if "recovery_parent" in inputs:
                need(equal(inputs["recovery_parent"], actual_parent), "fresh_recovery_parent_mismatch")
            inputs["recovery_parent"] = actual_parent
        need(
            str(inputs["project_id"]) == str(project.id)
            and str(inputs["workflow_run_id"]) == str(workflow["id"]),
            "fresh_workflow_owner_mismatch",
        )
        need(
            inputs["full_text"] == assembly.full_text
            and inputs["title"] == title
            and equal(inputs["original_report"], report),
            "fresh_body_title_report_mismatch",
        )
        need(
            inputs["manifest"]["assembly_sha256"] == body_hash
            and _source_identities(inputs["manifest"]) == _source_identities(manifest),
            "fresh_assembly_manifest_mismatch",
        )
        # A factory may add v4 source aliases, but cannot drop native manifest
        # fields (control/canonical/reader contract/lineage) from the binding.
        need(
            all(
                equal(inputs["manifest"].get(key), value)
                for key, value in manifest.items()
                if key != "sources"
            ),
            "fresh_native_manifest_fields_missing",
        )
        for native_source, fresh_source in zip(
            manifest["sources"], inputs["manifest"]["sources"], strict=True
        ):
            need(
                all(equal(fresh_source.get(key), value) for key, value in native_source.items()),
                "fresh_native_source_fields_missing",
            )
        if "project_id" in manifest:
            need(str(manifest["project_id"]) == str(project.id), "assembly_project_mismatch")
        if "execution_scope_id" in manifest:
            need(
                inputs["execution_scope_id"] == manifest["execution_scope_id"],
                "fresh_assembly_scope_mismatch",
            )
        payload = semantic.prepare_review(**deepcopy(inputs))
        need(equal(saved["request_payload"], payload), "current_payload_mismatch")
        need(
            equal(saved["original_report"], report)
            and saved["binding_sha256"] == payload["binding_sha256"],
            "artifact_report_binding_mismatch",
        )

        evidence, native_ref = await _load_artifact_native(session, settings, artifact, workflow, consumer)
        if recovery_evidence is not None:
            evidence["protocol_recovery"] = recovery_evidence
        run_id, root_key, budget = (native_ref[k] for k in ("run_id", "root_key", "budget"))
        derived = consumer.consume_semantic_evidence(
            current_inputs=inputs, evidence=evidence, semantic=semantic
        )
        need(
            derived["adjudication_trace"]["status"] == "verified",
            "semantic_evidence_unverified:" + str(derived["adjudication_trace"]["error"]),
        )
        reference = json_safe(
            {
                "id": artifact["id"],
                "project_id": artifact["project_id"],
                "artifact_type": artifact["artifact_type"],
                "version_no": artifact["version_no"],
                "status": artifact["status"],
                "schema_version": artifact["schema_version"],
                "source_run_id": workflow["id"],
                "llm_run_id": run_id,
                "operation_key": root_key,
                "binding_sha256": payload["binding_sha256"],
                "workflow_sha256": consumer.digest(json_safe(workflow)),
                "artifact_content_sha256": consumer.digest(saved),
                "budget_sha256": consumer.digest(json_safe(budget)),
            }
        )
        context = SemanticContext(current_inputs=deepcopy(inputs), evidence=evidence)
        return {
            "semantic_context": context,
            "current_inputs": context.current_inputs,
            "evidence": evidence,
            "artifact_reference": reference,
            "derived_review": json_safe(derived),
            "derived_trace": json_safe(derived["adjudication_trace"]),
        }

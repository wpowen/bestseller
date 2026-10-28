"""Read-only full-candidate audit. Never exports, imports, or changes guard policy."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import logging
from pathlib import Path
import re
import shutil
from uuid import NAMESPACE_URL, UUID, uuid5

from e2e_sim.capture_source import (
    REPO,
    SOURCE_ID,
    SOURCE_SLUG,
    freeze_source_runtime,
    install_proof_environment,
    load_authorized_source,
    network_fence,
)
from e2e_sim.diagnostics import safe_exception

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_INTERNAL = re.compile(
    r"(?P<namespace>workflow_runs|planning_artifact_versions):(?P<id>"
    + _UUID
    + r"):(?P<sha>[0-9a-f]{64})\Z"
)


def parse_internal_reference(value):
    """Strict table:UUID:SHA grammar; arbitrary operator labels do not qualify."""
    match = _INTERNAL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    return {
        "namespace": match["namespace"],
        "id": UUID(match["id"]),
        "content_sha256": match["sha"],
    }


def verify_reference_target(parsed, row, project_id):
    from bestseller.services.operator_action_approval import PURPOSES
    from bestseller.services.prompt_source_policy import content_hash

    if row is None or row.id != parsed["id"] or row.project_id != project_id:
        return False
    if parsed["namespace"] == "workflow_runs":
        data = row.metadata_json or {}
        binding = data.get("binding") or {}
        capabilities = data.get("capabilities")
        return bool(
            row.workflow_type == "operator_action_approval"
            and row.status == "completed"
            and data.get("schema") == "operator-action-approval.v1"
            and data.get("source") == "trusted_operator_api_explicit_human_instruction"
            and data.get("purpose") in PURPOSES
            and row.scope_type == data["purpose"]
            and binding.get("project_id") == str(project_id)
            and binding.get("target_id") == str(row.scope_id)
            and isinstance(capabilities, list)
            and capabilities
            and set(capabilities) <= PURPOSES[data["purpose"]]
            and row.id == uuid5(NAMESPACE_URL, "bestseller:operator-approval:" + content_hash(data))
            and content_hash(data) == parsed["content_sha256"]
        )
    # No native artifact receipt verifier is implemented here. A row/type/hash
    # coincidence must never be upgraded into scoped authorization evidence.
    return False


def reference_shape(value):
    if not isinstance(value, str):
        return {
            "full_internal_format": False,
            "uuid_tokens": 0,
            "sha256_tokens": 0,
            "other_text_present": False,
        }
    uuid_tokens = list(re.finditer(_UUID, value))
    replaced = re.sub(_UUID, "<UUID>", value)
    sha_tokens = list(re.finditer(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", replaced))
    residue = re.sub(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", "<SHA>", replaced)
    residue = residue.replace("<UUID>", "").replace("<SHA>", "")
    return {
        "full_internal_format": parse_internal_reference(value) is not None,
        "uuid_tokens": len(uuid_tokens),
        "sha256_tokens": len(sha_tokens),
        "other_text_present": bool(re.search("[A-Za-z]", residue)),
    }


def _authorization_values(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "authorization_ref":
                yield (*path, key), item
            yield from _authorization_values(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _authorization_values(item, (*path, index))


def _location(path):
    def label(part):
        if type(part) is int:
            return f"[{part}]"
        if (
            isinstance(part, str)
            and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$-]{0,99}", part)
            and not re.fullmatch(r"[0-9a-fA-F]{32,}", part)
            and not re.search(r"sk-|gh[pousr]_|AKIA|xox[baprs]-|AIza|ya29\.", part)
        ):
            return "." + part
        return ".<dynamic-key>"

    return "$" + "".join(label(part) for part in path)


def payload_metadata(ref, index):
    kind = (
        "request"
        if isinstance(ref, str) and ref.endswith(".request.json")
        else ("response" if isinstance(ref, str) and ref.endswith(".response.json") else "unknown")
    )
    return {"kind": kind, "file": f"payload[{index}].{kind}"}


async def audit_references(session, candidate, *, extra_references=()):
    from sqlalchemy import select

    from bestseller.infra.db import models as db
    from bestseller.services.prompt_source_policy import content_hash

    occurrences = [
        (_location(("tables", *path)), value)
        for path, value in _authorization_values(candidate["tables"])
    ]
    occurrences.extend(extra_references)
    values = []
    for _path, value in occurrences:
        if isinstance(value, str) and value not in values:
            values.append(value)
    workflows = list(
        await session.scalars(
            select(db.WorkflowRunModel).where(db.WorkflowRunModel.project_id == SOURCE_ID)
        )
    )
    scopes = [record["scope_id"] for record in candidate["tables"]["execution_budgets"]]
    budgets = (
        list(
            await session.scalars(
                select(db.ExecutionBudgetModel).where(db.ExecutionBudgetModel.scope_id.in_(scopes))
            )
        )
        if scopes
        else []
    )
    result = []
    for index, value in enumerate(values, 1):
        parsed = parse_internal_reference(value)
        row = None
        if parsed:
            model = (
                db.WorkflowRunModel
                if parsed["namespace"] == "workflow_runs"
                else db.PlanningArtifactVersionModel
            )
            row = await session.scalar(
                select(model).where(model.id == parsed["id"], model.project_id == SOURCE_ID)
            )
        pointed = bool(parsed and verify_reference_target(parsed, row, SOURCE_ID))
        # Unsupported syntax is not the same as a missing DB row. Probe any
        # embedded UUIDs and ledger IDs separately, without publishing tokens.
        embedded_uuid_ids = {UUID(match.group()) for match in re.finditer(_UUID, value)}
        embedded_hex_ids = set(re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", value))
        embedded_workflow_rows = embedded_artifact_rows = embedded_ledger_rows = 0
        embedded_native_approval_rows = 0
        for identity in embedded_uuid_ids:
            workflow = await session.scalar(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.id == identity, db.WorkflowRunModel.project_id == SOURCE_ID
                )
            )
            artifact = await session.scalar(
                select(db.PlanningArtifactVersionModel).where(
                    db.PlanningArtifactVersionModel.id == identity,
                    db.PlanningArtifactVersionModel.project_id == SOURCE_ID,
                )
            )
            embedded_workflow_rows += workflow is not None
            embedded_artifact_rows += artifact is not None
            if workflow is not None:
                own = {
                    "namespace": "workflow_runs",
                    "id": workflow.id,
                    "content_sha256": content_hash(workflow.metadata_json or {}),
                }
                embedded_native_approval_rows += verify_reference_target(own, workflow, SOURCE_ID)
        if scopes:
            for identity in embedded_hex_ids:
                operation = await session.scalar(
                    select(db.ExecutionOperationModel).where(
                        db.ExecutionOperationModel.key == identity,
                        db.ExecutionOperationModel.scope_id.in_(scopes),
                    )
                )
                embedded_ledger_rows += operation is not None
        # Equality with an echoed label is recorded separately, never a pointer proof.
        approval_matches = 0
        for workflow in workflows:
            data = workflow.metadata_json or {}
            if (
                workflow.workflow_type != "operator_action_approval"
                or data.get("authorization_ref") != value
            ):
                continue
            check = {
                "namespace": "workflow_runs",
                "id": workflow.id,
                "content_sha256": content_hash(data),
            }
            approval_matches += verify_reference_target(check, workflow, SOURCE_ID)
        provider_receipts = 0
        for budget in budgets:
            records = (budget.metadata_json or {}).get("provider_authorizations") or {}
            if not isinstance(records, dict):
                continue
            for receipt in records.values():
                if not isinstance(receipt, dict):
                    continue
                request = receipt.get("request") or {}
                authorization = receipt.get("authorization") or {}
                if (
                    receipt.get("policy") == "operator-provider-completion.v1"
                    and request.get("authorization_ref") == value
                    and authorization.get("authorization_ref") == value
                    and request.get("actor")
                    and request.get("objective") == authorization.get("objective")
                    and receipt.get("authorized_at")
                    and isinstance(receipt.get("before"), dict)
                ):
                    provider_receipts += 1
        # Also count existing receipt-like workflow records separately from strong approval.
        related = 0
        for workflow in workflows:
            if workflow.workflow_type not in {
                "operator_action_approval",
                "creation_source_revision",
            }:
                continue
            if (workflow.metadata_json or {}).get(
                "authorization_ref"
            ) == value and workflow.status == "completed":
                related += 1
        result.append(
            {
                "reference_group": "R" + str(index),
                "locations": [path for path, item in occurrences if item == value],
                "length": len(value),
                **reference_shape(value),
                "target_lookup_performed": parsed is not None,
                "explicit_target_record_exists": (row is not None) if parsed else None,
                "explicit_target_binding_valid": pointed,
                "same_label_valid_native_approvals": approval_matches,
                "same_label_provider_receipts": provider_receipts,
                "same_label_completed_approval_or_revision_rows": related,
                "embedded_uuid_lookups": len(embedded_uuid_ids),
                "embedded_workflow_rows_existing": embedded_workflow_rows,
                "embedded_artifact_rows_existing": embedded_artifact_rows,
                "embedded_native_approval_rows": embedded_native_approval_rows,
                "embedded_ledger_rows_existing": embedded_ledger_rows,
                "both_conditions_satisfied": bool(parsed and pointed),
            }
        )
    return result


async def scan():
    from sqlalchemy import select, text
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from bestseller.infra.db import models as db
    from bestseller.settings import load_settings
    from support.planning_prefix_capture import _prefix_candidate, scan_credential_fields

    profile = load_authorized_source(
        REPO.parents[2], Path("/Volumes/MACSSD/Docker/bestseller/artifacts")
    )
    params = profile["connection"]
    network_fence(params["port"])
    settings = load_settings(
        config_path=profile["config_path"],
        local_config_path=profile["local_config_path"],
        env=profile["settings_environment"],
    )
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = profile["artifact_root"]
    settings = freeze_source_runtime(settings)
    install_proof_environment(settings, profile["runtime_environment"])
    engine = create_async_engine(
        URL.create("postgresql+asyncpg", **params),
        pool_size=1,
        max_overflow=0,
        connect_args={
            "timeout": 10,
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "30000",
                "application_name": "par_P3_whole_guard_scan",
            },
        },
    )
    findings = []
    retained_references = []
    payload_count = 0
    payload_kinds = {}

    def inspect_payload(ref, document):
        nonlocal payload_count
        payload_count += 1
        labels = payload_metadata(ref, payload_count)
        kind = labels["kind"]
        payload_kinds[kind] = payload_kinds.get(kind, 0) + 1
        retained_references.extend(
            (labels["file"] + _location(path)[1:], value)
            for path, value in _authorization_values(document)
        )
        for finding in scan_credential_fields(document):
            findings.append(
                {
                    **finding,
                    "origin": "retained_payload",
                    "file": labels["file"],
                    "table": "llm_runs",
                    "structure": kind,
                }
            )

    try:
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='30s'"))
            state = (
                await session.execute(
                    text(
                        "SELECT current_setting('transaction_read_only'), "
                        "current_setting('statement_timeout')"
                    )
                )
            ).one()
            if state != ("on", "30s"):
                raise RuntimeError("guard_scan_readonly_required")
            project = await session.scalar(
                select(db.ProjectModel).where(db.ProjectModel.id == SOURCE_ID)
            )
            if project is None or project.slug != SOURCE_SLUG:
                raise RuntimeError("guard_scan_project_identity")
            candidate = await _prefix_candidate(
                session, settings, project_id=SOURCE_ID, inspect_payload=inspect_payload
            )
            findings.extend(
                {**finding, "origin": "candidate_tables", "file": None}
                for finding in scan_credential_fields(candidate)
            )
            refs = await audit_references(session, candidate, extra_references=retained_references)
            counts = {key: len(rows) for key, rows in candidate["tables"].items()}
            files = len(candidate["files"])
            await session.rollback()
        return {
            "status": "scan_complete",
            "read_only": True,
            "statement_timeout": "30s",
            "source_project_id": str(SOURCE_ID),
            "native_source_reader_passed": True,
            "table_counts": counts,
            "payload_inspections": payload_count,
            "unique_payload_files": files,
            "payload_kinds": payload_kinds,
            "findings": findings,
            "authorization_references": refs,
            "candidate_exported": False,
            "candidate_signature_created": False,
            "pg_imports": 0,
            "guard_policy_changed": False,
            "run_counter_changed": False,
        }
    finally:
        await engine.dispose()


def main():
    output = REPO / "reports/parallel-2026-10-28/P3/GUARD-SCAN.json"
    if output.exists():
        raise SystemExit("guard_scan_refuses_existing_evidence")
    if shutil.disk_usage("/System/Volumes/Data").free < 5 * 1024**3:
        raise SystemExit("disk_below_5GiB")
    logging.disable(logging.CRITICAL)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            report = asyncio.run(scan())
    except Exception as error:
        report = {"status": "scan_error", **safe_exception(error)}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "finding_count": len(report.get("findings", [])),
                "report": str(output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "scan_complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline operator guards and synthetic real-SQLite migration/readback."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_conception_structure_protocol_recovery import case, dbcase  # noqa: F401

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import conception_checkpoints as checkpoints
from bestseller.services import conception_structure_protocol_recovery as service

PATH = Path(__file__).resolve().parents[2] / "reports/continuous-recovery-2026-10-16/migrate_structure_protocol.py"
spec = importlib.util.spec_from_file_location("bounded_protocol_migration_subject", PATH)
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)
pytestmark = pytest.mark.unit


def test_safe_diagnostic_preserves_service_wrong_root_code_and_only_frame_locations():
    with pytest.raises(ValueError) as caught:
        service.require(False, "paid_root_changed")
    diagnostic = op.safe_diagnostic(caught.value)
    assert diagnostic["error_code"] == "conception_structure_protocol_paid_root_changed"
    assert diagnostic["error_type"] == "ValueError"
    assert diagnostic["traceback_frames"]
    assert all(set(frame) == {"filename", "lineno", "function"} for frame in diagnostic["traceback_frames"])
    with pytest.raises(op.Refused) as cas:
        op.require(False, "inputs_or_sources_changed_during_migration")
    assert op.safe_diagnostic(cas.value)["error_code"] == "fresh_acceptance:inputs_or_sources_changed_during_migration"


def test_safe_diagnostic_redacts_secrets_wrappers_oversize_and_nonmatching_messages():
    from sqlalchemy.exc import DBAPIError
    secret = "sk-secret-password=https://private.example/?token=hidden"
    errors = [ValueError(secret), ValueError("fresh_acceptance:" + secret),
        ValueError("conception_structure_protocol_" + "a" * 300),
        ValueError("fresh_acceptance:valid\n"),
        DBAPIError("SELECT private_password FROM credentials", {"password": secret},
            ValueError("conception_structure_protocol_paid_root_changed"))]
    for error in errors:
        diagnostic = op.safe_diagnostic(error)
        assert diagnostic["error_code"] == "inspect_preserved_service_evidence"
        serialized = json.dumps(diagnostic)
        assert secret not in serialized and "SELECT" not in serialized and "private_password" not in serialized
        assert "orig" not in diagnostic and "statement" not in diagnostic and "params" not in diagnostic


def minimal():
    snapshot = {"budget": {"status": "active", "calls_used": 147, "tokens_charged": 1291669,
        "metadata_json": {"provider_authorization": {"mode": "completion_bound", "status": "active",
            "authorization_ref": "reviewed-original", "provider": "minimax", "models": ["openai/MiniMax-M3"],
            "endpoint_hosts": ["api.minimaxi.com"]}}},
        "workflows": [{"id": "target", "status": "failed", "updated_at": "2026-01-01T00:00:00+00:00",
            "metadata_json": {"conception_checkpoint": {"state": {"structure_repair_attempts": 1}},
                "old_history": [{"rejected": True}]}}],
        "runs": [{"id": "run", "raw": "retained"}], "attempts": [{"id": "attempt", "spent": 14}],
        "operations": [{"key": "paid-root", "result": "retained"}]}
    prepared = {"derived_checkpoint": {"state": {"structure_repair_attempts": 1,
        "structure_repair_protocol_attempts": {"v1": 1, "current-v2": 0}}}}
    after = deepcopy(snapshot)
    after["workflows"][0]["metadata_json"][service.KEY] = deepcopy(prepared)
    after["workflows"][0]["updated_at"] = "2026-01-01T00:00:01+00:00"
    return snapshot, after, prepared


def test_default_offline_never_enters_execute_or_database(monkeypatch, capsys):
    monkeypatch.setattr(op, "prepare", lambda _: SimpleNamespace(plan={"conditions": op.CONDITIONS, "plan_sha256": "review-me"}))
    execute = AsyncMock(side_effect=AssertionError("No live path in offline mode"))
    monkeypatch.setattr(op, "execute", execute)
    assert op.main(["--workflow", "selected-later", "--run", "selected-later", "--proof-receipt", "actual-later",
        "--proof-receipt-sha256", "reviewed-file-hash", "--proof-source-sha256", "reviewed-source-hash",
        "--parent-review-ref", "actual-parent-reference"]) == 0
    execute.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "prepared_only" and output["database_access"] is False
    assert output["provider_calls"] == 0
    assert output["conditions"]["fresh_normal_reviews_required"] is True
    assert output["conditions"]["old_diagnosis_endorsed"] is False


def test_all_parent_proof_fields_are_mandatory():
    with pytest.raises(SystemExit):
        op.parser().parse_args(["--workflow", "selected", "--run", "selected"])


@pytest.mark.asyncio
async def test_execute_requires_reviewed_plan_before_engine_creation(monkeypatch):
    from bestseller.infra.db import session as db
    monkeypatch.setattr(db, "create_engine", lambda *a, **k: pytest.fail("Engine creation before review"))
    bundle = SimpleNamespace(args=SimpleNamespace(reviewed_plan_sha256="stale"), intake=SimpleNamespace(),
        plan={"plan_sha256": "current"})
    with pytest.raises(op.Refused, match="review_current_offline_plan"):
        await op.execute(bundle)


def test_only_additive_key_and_target_audit_clock_are_permitted():
    before, after, prepared = minimal()
    result = op.compare_transition(before, after, "target", prepared)
    assert result["all_financial_rows_unchanged"] is result["original_checkpoint_unchanged"] is True
    assert service.KEY not in before["workflows"][0]["metadata_json"]


@pytest.mark.parametrize("fault", ["calls", "tokens", "raw_run", "attempt", "paid_root", "old_cp", "history",
    "status", "another_key", "missing_key", "derived_map", "clock", "new_workflow"])
def test_full_readback_refuses_every_change_outside_one_metadata_key(fault):
    before, after, prepared = minimal()
    metadata = after["workflows"][0]["metadata_json"]
    if fault == "calls": after["budget"]["calls_used"] = 0
    elif fault == "tokens": after["budget"]["tokens_charged"] += 1
    elif fault == "raw_run": after["runs"][0]["raw"] = "different"
    elif fault == "attempt": after["attempts"][0]["spent"] = 0
    elif fault == "paid_root": after["operations"][0]["result"] = "different"
    elif fault == "old_cp": metadata["conception_checkpoint"]["state"]["structure_repair_attempts"] = 0
    elif fault == "history": metadata["old_history"] = []
    elif fault == "status": after["workflows"][0]["status"] = "running"
    elif fault == "another_key": metadata["unreviewed"] = True
    elif fault == "missing_key": metadata.pop(service.KEY)
    elif fault == "derived_map": metadata[service.KEY]["derived_checkpoint"]["state"]["structure_repair_attempts"] = 0
    elif fault == "clock": after["workflows"][0]["updated_at"] = "2025-01-01T00:00:00+00:00"
    else: after["workflows"].append({"id": "another-target"})
    with pytest.raises(op.Refused):
        op.compare_transition(before, after, "target", prepared)


@pytest.mark.parametrize("field,value", [("authorization_ref", "new-authority"), ("provider", "other"),
    ("models", ["other-model"]), ("endpoint_hosts", ["other.example"]), ("mode", "bounded"), ("status", "paused")])
def test_original_authorization_scope_and_minimax_route_cannot_change(field, value):
    snapshot, _, _ = minimal()
    op.original_authorization(snapshot, "reviewed-original")
    snapshot["budget"]["metadata_json"]["provider_authorization"][field] = value
    with pytest.raises(op.Refused, match="original_authorization_changed"):
        op.original_authorization(snapshot, "reviewed-original")


def test_physical_error_exception_is_one_explicit_derived_target_and_keeps_false_flags(tmp_path):
    proof = {"llm_run_id": "legacy", "operation_key": "paid-root", "prompt_hash": "exact",
        "source_proof_sha256": "reviewed-source", "files": {"request": {"replayable": False}}}
    error = {"code": "fresh_acceptance:resume_physical_file_binding", "llm_run_id": "legacy"}
    runtime = {"physical_proof_errors": [error]}
    accepted = op.physical_exception(runtime, proof, tmp_path / "proof.json", "reviewed-file")
    assert accepted["actual_physical_request_proof"] is accepted["old_request_replayable"] is False
    assert runtime["physical_proof_errors"] == accepted["preserved_physical_proof_errors"] == [error]
    runtime["physical_proof_errors"].append({**error, "llm_run_id": "another-paid-call"})
    with pytest.raises(op.Refused, match="unproved_physical_error"):
        op.physical_exception(runtime, proof, tmp_path / "proof.json", "reviewed-file")


def test_immutable_receipts_are_new_readonly_and_preserve_old_files(tmp_path):
    first = op.immutable(tmp_path, "migration", {"before": 147})
    raw = first.read_bytes()
    second = op.immutable(tmp_path, "runtime", {"after": 147})
    assert first != second and first.read_bytes() == raw
    assert first.stat().st_mode & 0o777 == second.stat().st_mode & 0o777 == 0o400


def test_old_function_basis_receipt_is_not_a_fresh_caller_freeze_receipt(tmp_path):
    caller = tmp_path / "caller.py"
    caller.write_text("frozen caller")
    os.utime(caller, (1767225600, 1767225600))
    with pytest.raises(op.Refused, match="fresh_derived_receipt_after_caller_freeze"):
        op.caller_freeze({"created_at": "2025-12-31T23:59:59+00:00"}, caller)
    binding = op.caller_freeze({"created_at": "2026-01-01T00:00:01+00:00"}, caller)
    assert binding["caller_sha256"] == op.file_sha(caller)


async def bundle_for(case, session):
    return SimpleNamespace(state={"scope": case.scope}, workflow=str(case.wid), recovery=case.prepared,
        expected=await op.snapshot_session(session, case.scope), authorization_ref="test-only-human-authorization")


@pytest.mark.asyncio
async def test_real_sqlite_locked_migration_and_independent_normal_loader(dbcase):
    case = dbcase
    async with case.factory() as session:
        bundle = await bundle_for(case, session)
        verification = await op.apply_locked(session, bundle)
        assert verification["only_added_metadata_key"] == service.KEY
        await session.commit()
    async with case.factory() as readback:
        selected = await checkpoints.load_conception_checkpoint(readback, workflow_run_id=case.wid,
            input_hash=case.checkpoint["input_hash"])
        assert selected == case.prepared["derived_checkpoint"]
        assert selected["state"]["structure_repair_attempts"] == 1
        row = await readback.get(WorkflowRunModel, case.wid)
        assert row.metadata_json[checkpoints.CHECKPOINT_KEY] == case.checkpoint
        after = await op.snapshot_session(readback, case.scope)
        op.compare_transition(bundle.expected, after, str(case.wid), case.prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["row_cas", "source_policy"])
async def test_locked_transaction_rejects_row_and_source_drift_before_addition(dbcase, fault):
    case = dbcase
    async with case.factory() as session:
        bundle = await bundle_for(case, session)
        if fault == "row_cas":
            row = await session.get(WorkflowRunModel, case.wid)
            row.error_message = "changed after offline inspection"
            await session.commit()
        else:
            bundle.recovery = deepcopy(bundle.recovery)
            bundle.recovery["source_policy"]["protocol"] = "different"
        with pytest.raises(op.Refused):
            await op.apply_locked(session, bundle)
        await session.rollback()
    async with case.factory() as readback:
        row = await readback.get(WorkflowRunModel, case.wid)
        assert service.KEY not in row.metadata_json

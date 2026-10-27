"""Offline operator checks only: no PG, provider, network or production writes."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

PATH = (
    Path(__file__).resolve().parents[2]
    / "reports/continuous-recovery-2026-10-16/restore_commercial_successor.py"
)
SPEC = importlib.util.spec_from_file_location("restore_commercial_successor_operator", PATH)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)
pytestmark = pytest.mark.unit


def rows():
    proof = {
        "schema": "commercial-successor-pointer-restoration.v1",
        "identity": "one-restoration",
        "key": "same-body-key",
        "old_pointer": {"workflow_run_id": "old", "binding_sha256": "old-hash"},
        "new_pointer": {"workflow_run_id": "consumed-owner", "binding_sha256": "new-hash"},
        "actor": "explicit-actor",
        "reason": "Restore independently committed pointer",
    }
    chapter = {
        "id": "chapter",
        "updated_at": "before",
        "metadata_json": {
            "author_requirement": "Keep the orchard",
            "commercial_component_cohorts": {proof["key"]: deepcopy(proof["old_pointer"])},
        },
    }
    auth = {
        "id": "authorization",
        "updated_at": "before",
        "metadata_json": {
            "receipt": {"fixed-original-proof": True},
            "consumed_owner_id": "consumed-owner",
        },
    }
    state = {
        "tables": {
            "chapters": {"chapter": operator.digest(chapter)},
            "workflow_runs": {
                "authorization": operator.digest(auth),
                "consumed-owner": "journal-unchanged",
            },
            "chapter_draft_versions": {"draft": "full-body-hash"},
            "review_reports": {"review": "retained-grade"},
            "quality_scores": {"quality": "retained-score"},
        },
        "scope_ledger": {
            "execution_budgets": ["same"],
            "execution_operations": ["same"],
            "execution_attempts": ["same"],
        },
        "global_counts": {"workflow_runs": 3, "llm_runs": 7},
    }
    return state, chapter, auth, proof


def changed_rows():
    before, chapter, auth, proof = rows()
    changed_chapter, changed_auth = deepcopy(chapter), deepcopy(auth)
    changed_chapter["metadata_json"]["commercial_component_cohorts"][proof["key"]] = proof[
        "new_pointer"
    ]
    changed_auth["metadata_json"]["pointer_restoration_history"] = [proof]
    changed_chapter["updated_at"] = changed_auth["updated_at"] = "after"
    after = deepcopy(before)
    after["tables"]["chapters"]["chapter"] = operator.digest(changed_chapter)
    after["tables"]["workflow_runs"]["authorization"] = operator.digest(changed_auth)
    return before, after, chapter, changed_chapter, auth, changed_auth, proof


def prepared_document():
    before, chapter, auth, proof = rows()
    return {
        "schema": operator.SCHEMA,
        "status": "preflight_passed",
        "mode": "preflight",
        "committed": False,
        "preflight_rolled_back": True,
        "independent_readback_verified": True,
        "provider_calls_performed": 0,
        "blocked_gateway_attempts": 0,
        "restoration_receipt": proof,
        "chapter_before": chapter,
        "authorization_before": auth,
        "before_inventory_sha256": operator.digest(before),
    }


def test_default_is_offline_and_apply_requires_explicit_reviewed_preflight():
    assert not operator.parse_args([]).execute
    for argv in (
        ["--apply"],
        ["--execute"],
        ["--execute", "--apply"],
        ["--execute", "--authorization", "not-a-uuid", "--reason", "repair"],
    ):
        with pytest.raises(SystemExit):
            operator.parse_args(argv)
    args = operator.parse_args(["--execute", "--authorization", str(uuid4()), "--reason", "repair"])
    assert args.execute and not args.apply
    assert operator.parse_args(["--execute", "--apply", "--receipt", "reviewed.json"]).apply


def test_offline_main_never_loads_services_or_database(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("offline loaded live dependencies")

    monkeypatch.setattr(operator, "services", forbidden)
    assert operator.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] and result["database_initialized"] is False
    assert result["provider_calls_performed"] == 0 and result["live_restoration_verified"] is False


def test_only_two_exact_derived_changes_allowed_including_idempotence():
    args = changed_rows()
    before, after, chapter, new_chapter, auth, new_auth, proof = args
    operator.assert_only_restoration(
        before, after, chapter, new_chapter, auth, new_auth, proof, apply=True
    )
    operator.assert_only_restoration(
        after, after, new_chapter, new_chapter, new_auth, new_auth, proof, apply=True
    )
    operator.assert_only_restoration(
        before, before, chapter, chapter, auth, auth, proof, apply=False
    )
    prepared = prepared_document()
    operator.assert_prepared_baseline(prepared, before, chapter, auth, proof)
    operator.assert_prepared_baseline(prepared, after, new_chapter, new_auth, proof)


@pytest.mark.parametrize(
    "what",
    [
        "author_requirement",
        "body",
        "grade",
        "quality",
        "old_journal",
        "budget",
        "attempt",
        "operation",
        "new_workflow",
        "auth_receipt",
        "cohort_owner",
    ],
)
def test_any_other_history_or_financial_change_fails(what):
    before, after, chapter, new_chapter, auth, new_auth, proof = changed_rows()
    if what == "author_requirement":
        new_chapter["metadata_json"]["author_requirement"] = "changed"
    elif what == "auth_receipt":
        new_auth["metadata_json"]["receipt"] = {"changed": True}
    elif what == "cohort_owner":
        new_auth["metadata_json"]["consumed_owner_id"] = "another-owner"
    elif what in {"budget", "attempt", "operation"}:
        key = {
            "budget": "execution_budgets",
            "attempt": "execution_attempts",
            "operation": "execution_operations",
        }[what]
        after["scope_ledger"][key] = ["changed"]
    elif what == "new_workflow":
        after["global_counts"]["workflow_runs"] += 1
    else:
        table, key = {
            "body": ("chapter_draft_versions", "draft"),
            "grade": ("review_reports", "review"),
            "quality": ("quality_scores", "quality"),
            "old_journal": ("workflow_runs", "consumed-owner"),
        }[what]
        after["tables"][table][key] = "changed"
    with pytest.raises(operator.OperatorBlocked):
        operator.assert_only_restoration(
            before, after, chapter, new_chapter, auth, new_auth, proof, apply=True
        )


def test_apply_cannot_silently_accept_new_activity_after_preflight():
    before, chapter, auth, proof = rows()
    before["scope_ledger"]["execution_attempts"].append("new-paid-attempt")
    with pytest.raises(operator.OperatorBlocked, match="prepared_protected_inventory_changed"):
        operator.assert_prepared_baseline(prepared_document(), before, chapter, auth, proof)


@pytest.mark.parametrize(
    "problem", ["not_preflight", "not_rolled_back", "paid", "duplicate_key", "overflow", "nan"]
)
def test_prepared_receipt_file_is_strict_and_cannot_be_fabricated_from_failed_run(
    tmp_path, problem
):
    document = prepared_document()
    if problem == "not_preflight":
        document["status"] = "blocked"
    elif problem == "not_rolled_back":
        document["preflight_rolled_back"] = False
    elif problem == "paid":
        document["provider_calls_performed"] = 1
    text = json.dumps(document)
    if problem == "duplicate_key":
        text = '{"schema":"bad","schema":"bad"}'
    elif problem == "overflow":
        text = '{"score":1e999}'
    elif problem == "nan":
        text = '{"score":NaN}'
    path = tmp_path / "receipt.json"
    path.write_text(text)
    with pytest.raises((operator.OperatorBlocked, ValueError)):
        operator.prepared_file(path)
    path.write_text(json.dumps(prepared_document()))
    assert operator.prepared_file(path) == prepared_document()


@pytest.mark.asyncio
async def test_real_future_judge_source_capture_stops_before_recovery_or_provider(monkeypatch):
    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services import commercial_judge_recovery as recovery
    from bestseller.settings import load_settings

    settings = load_settings()
    monkeypatch.setattr(
        judge,
        "_commercial_effective_critic_configuration",
        AsyncMock(return_value=settings.llm.critic.model_dump(mode="json")),
    )
    gateway = AsyncMock(side_effect=AssertionError("provider forbidden"))
    monkeypatch.setattr(judge, "complete_text", gateway)
    prepared = await operator.support().capture_prepared(
        judge,
        recovery,
        object(),
        settings,
        {
            **{
                name: uuid4()
                for name in ("project_id", "chapter_id", "draft_id", "workflow_run_id")
            },
            "chapter_number": 7,
            "content_md": "A botanist opens the greenhouse after the storm.",
            "generation_input": {"story_bible": {"world": "orbital orchard"}},
            "sample_slot": "chapter-quality.independent-v2:0",
            "commercial_cohort_samples": 3,
            "commercial_retained_only": True,
        },
    )
    binding = operator.support().prepared_binding(prepared, "test:original-scope")
    assert binding["scope_id"] == "test:original-scope" and binding["samples"] == 3
    assert prepared["sources"].receipt["truncated"] is False
    gateway.assert_not_awaited()


def test_payload_proofs_require_configured_root_and_expected_actual_hash(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    payload = root / "retained.json"
    payload.write_text("{}")
    settings = SimpleNamespace(artifact_store=SimpleNamespace(local_dir=str(root)))
    valid = {"request": {"ref": "retained.json", "sha256": operator.support().file_hash(payload)}}
    auth = SimpleNamespace(metadata_json={"physical_file_proofs": valid})
    proof = {"physical_file_proofs": valid}
    assert operator.file_proofs(settings, auth, proof) == {
        str(payload): operator.support().file_hash(payload)
    }
    proof["physical_file_proofs"] = {"request": {"ref": "retained.json", "sha256": "0" * 64}}
    with pytest.raises(operator.OperatorBlocked):
        operator.file_proofs(settings, auth, proof)

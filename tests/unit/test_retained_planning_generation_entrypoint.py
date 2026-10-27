"""Actual wrapper/generator/physical reader entry; no provider or production DB."""

from copy import deepcopy
import hashlib
from unittest.mock import AsyncMock

import pytest
from test_retained_planning_generation import case as case
from test_retained_planning_generation import snapshot

from bestseller.services import planner

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_source_wrapper_recovers_paid_world_with_original_receipt_and_new_reference_only(
    case, monkeypatch
):
    # These replace only runtime heartbeat plumbing and the token cap needed
    # to reproduce the fixture's *real* current request. Validators stay real.
    heartbeat = AsyncMock()
    monkeypatch.setattr(planner, "_heartbeat_planner_workflow", heartbeat)
    monkeypatch.setattr(planner, "_planner_stage_max_tokens", lambda *args, **kwargs: 4096)
    provider_attempts = []

    async def deny_complete_text(*args, **kwargs):
        provider_attempts.append(True)
        raise AssertionError("entrypoint must restore the original paid result")

    monkeypatch.setattr(planner, "complete_text", deny_complete_text)
    before = await snapshot(case)
    original_metadata = deepcopy(case.original.metadata_json)
    before_consumer = next(row for row in before["workflow_runs"] if row["id"] == case.consumer.id)
    files = {key: path.read_bytes() for key, path in case.paths.items()}
    writes = len(case.writes)
    binding = deepcopy(case.args["canonical_binding"])
    case.project._source_bound_planning_canonical = binding

    payload, run_id = await planner._complete_source_bound_planning_content(
        case.session,
        case.settings,
        project=case.project,
        premise="Scientists inspect measurement errors",
        kind="world_spec",
        projection={},
        workflow_run_id=case.consumer.id,
        book_spec={"theme_statement": "Publish the error", "protagonist": {"name": "Mara"}},
        world_spec={"world_name": "Observatory", "world_premise": "Scientists inspect errors"},
        cast_spec={"protagonist": {"name": "Mara", "goal": "Find the error"}},
    )
    assert run_id == case.run.id
    assert {key: value for key, value in payload.items() if key != "_meta"} == case.payload
    meta = payload["_meta"]
    assert meta["generation_llm_run_id"] == str(case.run.id)
    assert meta["workflow_run_id"] == str(case.consumer.id)
    assert meta["input_hash"] == case.args["planner_input_hash"]
    compact = meta["planning_submission_receipt"]
    assert compact == {
        **{
            key: value
            for key, value in case.submission.items()
            if key not in {"raw_arguments", "original_content", "asset_json"}
        },
        "workflow_run_id": str(case.original.id),
        "llm_run_id": str(case.run.id),
        "original_receipt_sha256": planner._approved_payload_hash(case.submission),
    }
    reuse = meta["retained_generation_reuse"]
    assert reuse["original_workflow_run_id"] == str(case.original.id)
    assert reuse["original_llm_run_id"] == str(case.run.id)
    assert reuse["proof"]["effective_input_sha256"] == case.run.prompt_hash
    assert reuse["proof"]["request_file_sha256"] == hashlib.sha256(files["request"]).hexdigest()
    assert reuse["proof"]["response_file_sha256"] == hashlib.sha256(files["response"]).hexdigest()
    assert reuse["proof"]["grade_reused"] is False
    assert reuse["proof"]["semantic_review_required"] is True
    assert meta["approved_complete"] is False
    expected_consumer_metadata = {
        **before_consumer["metadata_json"],
        "planning_generation_reuses": {str(case.run.id): reuse},
    }
    assert case.consumer.metadata_json == expected_consumer_metadata
    assert "planning_submissions" not in case.consumer.metadata_json
    assert case.original.metadata_json == original_metadata
    assert len(case.writes) == writes  # Wrapper stages only its own WF metadata.
    assert provider_attempts == case.denied == []
    heartbeat.assert_awaited()

    # Caller-owned persistence, followed by a real SQLite readback. Only the
    # consumer WF reference/timestamp may change; all original rows are exact.
    await case.session.commit()
    after = await snapshot(case)
    for table, rows in before.items():
        if table != "workflow_runs":
            assert after[table] == rows
        else:
            after_by_id = {row["id"]: row for row in after[table]}
            for row in rows:
                if row["id"] != case.consumer.id:
                    assert after_by_id[row["id"]] == row
                else:
                    actual = after_by_id[row["id"]]
                    assert actual["metadata_json"] == expected_consumer_metadata
                    assert {
                        key: value
                        for key, value in actual.items()
                        if key not in {"metadata_json", "updated_at"}
                    } == {
                        key: value
                        for key, value in row.items()
                        if key not in {"metadata_json", "updated_at"}
                    }
    assert len(case.writes) == writes + 1
    assert case.writes[-1].lstrip().startswith("UPDATE workflow_runs")
    assert case.original.status == "failed" and case.original.metadata_json == original_metadata
    assert files == {key: path.read_bytes() for key, path in case.paths.items()}
    assert provider_attempts == case.denied == []

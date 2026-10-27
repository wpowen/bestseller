"""Real wrapper, parser and retained paid reader; SQLite only, no provider."""

from copy import deepcopy
import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_retained_cast_dependency_compatibility import compat_case as compat_case
from test_retained_planning_generation import snapshot

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import planner
from bestseller.services.retained_planning_generation import RetainedPlanningGenerationError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def install_runtime(monkeypatch):
    heartbeat = AsyncMock()
    monkeypatch.setattr(planner, "_heartbeat_planner_workflow", heartbeat)
    monkeypatch.setattr(planner, "_planner_stage_max_tokens", lambda *_args, **_kwargs: 4096)
    denied = []

    async def deny(*_args, **_kwargs):
        denied.append(True)
        raise AssertionError("paid Cast candidate must be restored without provider")

    monkeypatch.setattr(planner, "complete_text", deny)
    return heartbeat, denied


async def complete(case, **changes):
    return await planner._complete_source_bound_planning_content(
        case.session,
        case.settings,
        **{**case.current_args, "workflow_run_id": case.consumer.id, **changes},
    )


async def test_actual_cast_wrapper_keeps_original_generation_and_current_consumption_separate(
    compat_case,
    monkeypatch,
):
    case = compat_case
    heartbeat, denied = install_runtime(monkeypatch)
    case.project._retained_planning_generation_source_runs = {"cast_spec": case.run.id}
    before = await snapshot(case)
    original_metadata = deepcopy(case.original.metadata_json)
    before_consumer = next(row for row in before["workflow_runs"] if row["id"] == case.consumer.id)
    files = {key: path.read_bytes() for key, path in case.paths.items()}
    writes = len(case.writes)

    payload, run_id = await complete(case)
    assert run_id == case.run.id
    assert {key: value for key, value in payload.items() if key != "_meta"} == case.payload
    meta = payload["_meta"]
    original_hash = case.run.metadata_json["input_hash"]
    current_hash = case.compat_args["planner_input_hash"]
    assert original_hash != current_hash
    assert meta["input_hash"] == meta["generation_input_hash"] == original_hash
    assert meta["consumption_input_hash"] == current_hash
    assert meta["paid_current_input_asserted"] is False
    assert meta["generation_llm_run_id"] == str(case.run.id)
    assert meta["workflow_run_id"] == str(case.consumer.id)
    assert meta["approved_complete"] is False
    assert meta["planning_status"] == "generated_pending_semantic_review"
    assert meta["creator_authorship_asserted"] is False
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
    proof = reuse["proof"]
    assert proof["full_effective_input_exact"] is False
    assert proof["original_planner_input_sha256"] == original_hash
    assert proof["current_planner_input_sha256"] == current_hash
    assert proof["original_effective_input_sha256"] == case.run.prompt_hash
    assert proof["current_effective_input_sha256"] != case.run.prompt_hash
    assert proof["request_file_sha256"] == hashlib.sha256(files["request"]).hexdigest()
    assert proof["response_file_sha256"] == hashlib.sha256(files["response"]).hexdigest()
    assert proof["provider_dispatches"] == 0
    assert proof["candidate_reuse_only"] and proof["semantic_review_required"]
    assert proof["grade_reused"] is proof["approval_asserted"] is False
    expected_consumer = {
        **before_consumer["metadata_json"],
        "planning_generation_reuses": {str(case.run.id): reuse},
    }
    assert case.consumer.metadata_json == expected_consumer
    assert "planning_submissions" not in case.consumer.metadata_json
    assert case.original.metadata_json == original_metadata
    assert case.project.metadata_json.get("_retained_planning_generation_source_runs") is None
    assert len(case.writes) == writes and denied == case.denied == []
    heartbeat.assert_awaited()

    await case.session.commit()
    async with async_sessionmaker(case.engine, expire_on_commit=False)() as readback:
        original = await readback.get(WorkflowRunModel, case.original.id)
        consumer = await readback.get(WorkflowRunModel, case.consumer.id)
        assert original.status == "failed" and original.metadata_json == original_metadata
        assert consumer.metadata_json == expected_consumer
        assert "planning_submissions" not in consumer.metadata_json
    after = await snapshot(case)
    for table, rows in before.items():
        if table != "workflow_runs":
            assert after[table] == rows
        else:
            after_by_id = {row["id"]: row for row in after[table]}
            for row in rows:
                actual = after_by_id[row["id"]]
                if row["id"] != case.consumer.id:
                    assert actual == row
                else:
                    assert {
                        k: v for k, v in actual.items() if k not in {"metadata_json", "updated_at"}
                    } == {k: v for k, v in row.items() if k not in {"metadata_json", "updated_at"}}
    assert len(case.writes) == writes + 1
    assert case.writes[-1].lstrip().startswith("UPDATE workflow_runs")
    assert files == {key: path.read_bytes() for key, path in case.paths.items()}
    assert denied == case.denied == []


@pytest.mark.parametrize("invalid", ["missing_id", "other_kind"])
async def test_actual_wrapper_rejects_bad_explicit_selector_without_provider_or_new_receipt(
    compat_case,
    monkeypatch,
    invalid,
):
    case = compat_case
    _heartbeat, denied = install_runtime(monkeypatch)
    case.project._retained_planning_generation_source_runs = (
        {"cast_spec": uuid4()} if invalid == "missing_id" else {"world_spec": case.run.id}
    )
    before = await snapshot(case)
    writes = len(case.writes)
    with pytest.raises(RetainedPlanningGenerationError):
        await complete(case, **({"kind": "world_spec"} if invalid == "other_kind" else {}))
    assert await snapshot(case) == before
    assert len(case.writes) == writes and denied == case.denied == []

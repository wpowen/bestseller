"""Actual isolated SQLite native-volume wrapper; completions are local fixtures."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from test_source_bound_planning_model_completion_entry import entry as entry
from test_volume_native_submission import physical, volumes

from bestseller.infra.db.models import LlmRunModel
from bestseller.services import planner
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.source_bound_planning_submission import (
    VOLUME_NATIVE_POLICY,
    planning_submission_tool,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.fixture
def capture_failures(entry, monkeypatch):
    def capture(**values):
        entry.failures.append(
            {
                "attempt": values["attempt"],
                "content": values["content"],
                "error": str(values["error"]),
            }
        )

    monkeypatch.setattr(planner, "_persist_failing_planner_output", capture)


@pytest.mark.asyncio
@pytest.mark.parametrize("length_first", [False, True])
async def test_actual_sqlite_volume_wrapper_keeps_full_payload_and_8192_output_budget(
    entry,
    capture_failures,
    monkeypatch,
    length_first,
):
    produced = volumes(1)
    transport_outputs = []

    async def complete(session, settings, request):
        assert session is entry.session and session.bind.dialect.name == "sqlite"
        entry.calls.append(request)
        value = physical({"volumes": produced})
        truncated = length_first and len(entry.calls) == 1
        if truncated:
            value.tool_calls[0]["function"]["arguments"] = '{"volumes":[{"volume_number":1'
            value.raw_message["tool_calls"] = deepcopy(value.tool_calls)
        run = LlmRunModel(
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            logical_role="planner",
            provider="minimax",
            model_name="isolated-volume-fixture",
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            finish_reason="length" if truncated else "tool_calls",
            output_tokens=8192 if truncated else 800,
            metadata_json={"physical_arguments": value.tool_calls[0]["function"]["arguments"]},
        )
        session.add(run)
        await session.flush()
        result = LLMCompletionResult(
            content=value.content,
            tool_calls=value.tool_calls,
            raw_message=value.raw_message,
            provider=run.provider,
            model_name=run.model_name,
            finish_reason=run.finish_reason,
            output_tokens=run.output_tokens,
            llm_run_id=run.id,
        )
        transport_outputs.append(result)
        return result

    monkeypatch.setattr(planner, "complete_text", complete)
    book, world, cast = (
        entry.completed,
        {"world_name": "Observatory"},
        {"protagonist": {"name": "Mara"}},
    )
    projection = planner._compile_source_bound_volume_plan(
        entry.project, "观测站故事", book, world, cast
    )
    result, run_id = await planner._complete_source_bound_planning_content(
        entry.session,
        load_settings(),
        project=entry.project,
        premise="观测站故事",
        kind="volume_plan",
        projection=projection,
        book_spec=book,
        world_spec=world,
        cast_spec=cast,
        workflow_run_id=entry.workflow.id,
    )
    assert len(entry.calls) == 1 + int(length_first)
    assert [
        {key: value for key, value in row.items() if key != "_meta"} for row in result
    ] == produced
    for request in entry.calls:
        assert request.prompt_version == "v4-volume-native"
        assert request.max_tokens_override == 8192
        assert request.tools == [
            planning_submission_tool("volume_plan", version=VOLUME_NATIVE_POLICY)
        ]
        assert request.tool_choice == {
            "type": "function",
            "function": {"name": "submit_volume_plan"},
        }
    assert len(entry.failures) == int(length_first)
    if length_first:
        assert "finish_reason=length" in entry.failures[0]["error"]
        assert transport_outputs[0].output_tokens == 8192
    await entry.session.commit()
    await entry.session.refresh(entry.workflow)
    full = entry.workflow.metadata_json["planning_submissions"][str(run_id)]
    assert full["schema"] == VOLUME_NATIVE_POLICY
    assert full["raw_arguments"] == transport_outputs[-1].tool_calls[0]["function"]["arguments"]
    assert full["payload_sha256"] == content_hash(produced)
    assert "asset_json" not in full
    assert full["grade_reused"] is False
    compact = result[0]["_meta"]["planning_submission_receipt"]
    assert compact["workflow_run_id"] == str(entry.workflow.id)
    assert compact["original_receipt_sha256"] == content_hash(full)
    assert result[0]["_meta"]["approved_complete"] is False
    runs = list(await entry.session.scalars(select(LlmRunModel)))
    assert len(runs) == 1 + int(length_first)
    if length_first:
        first = next(run for run in runs if run.id == transport_outputs[0].llm_run_id)
        assert first.finish_reason == "length" and first.output_tokens == 8192

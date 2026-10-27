"""World model schema through the real SQLite wrapper, with no provider calls."""

from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from test_source_bound_planning_kind_entry import drift
from test_source_bound_planning_kind_entry import retain_failure_receipt as retain_failure_receipt
from test_source_bound_planning_model_completion_entry import entry as entry

from bestseller.infra.db.models import LlmRunModel, PlanningArtifactVersionModel
from bestseller.services import planner
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.source_bound_planning_submission import (
    planning_submission_contract,
    planning_submission_tool,
)
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def install(monkeypatch, rig, *, invalid=False, change_after_first=False):
    outputs = []

    async def complete(session, settings, request):
        assert session is rig.session and session.bind.dialect.name == "sqlite"
        rig.calls.append(request)
        value = {
            "world_name": "山地观测站", "world_premise": "两地协作观测并保存原始数据",
            "rules": [{"name": "留存原始观测", "description": "每次测量保留原始记录"}],
            "history_key_events": [{"event": "共同建站", "relevance": "协作的起点"}],
        }
        if invalid:
            value["rules"] = ["Do not coerce this string into a rule object"]
            value["history_key_events"] = ["Do not coerce this string into an event object"]
        arguments = json.dumps(value, ensure_ascii=False, indent=2)
        calls = [{"id": f"world-fixture-{len(rig.calls)}", "type": "function", "function": {
            "name": "submit_world_spec", "arguments": arguments,
        }}]
        run = LlmRunModel(
            project_id=request.project_id, workflow_run_id=request.workflow_run_id,
            logical_role="planner", provider="minimax", model_name="isolated-fixture",
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            finish_reason="tool_calls", metadata_json={"physical_arguments": arguments},
        )
        session.add(run)
        await session.flush()
        if change_after_first and len(rig.calls) == 1:
            await drift(rig)
        result = LLMCompletionResult(
            content="", provider=run.provider, model_name=run.model_name, llm_run_id=run.id,
            finish_reason="tool_calls", tool_calls=calls,
            raw_message={"role": "assistant", "content": "", "tool_calls": deepcopy(calls)},
        )
        outputs.append((value, arguments, result))
        return result

    monkeypatch.setattr(planner, "complete_text", complete)
    return outputs


async def invoke(rig):
    projection = planner._compile_source_bound_world_spec(
        rig.project, "观测站故事", rig.completed,
    )
    return await planner._complete_source_bound_planning_content(
        rig.session, load_settings(), project=rig.project, premise="观测站故事", kind="world_spec",
        projection=projection, book_spec=rig.completed, workflow_run_id=rig.workflow.id,
    )


def assert_request(request, rig):
    assert request.prompt_template == "planner_world_spec" and len(request.prompt_version) <= 32
    assert request.tools == [planning_submission_tool("world_spec")]
    assert request.tools[0]["function"]["strict"] is False
    assert request.tool_choice == {"type": "function", "function": {"name": "submit_world_spec"}}
    data, _ = json.JSONDecoder().raw_decode(request.user_prompt)
    assert data["approved_sources"]["canonical_snapshot"]["content"] == rig.canonical.content
    assert data["existing_planning_assets"]["book_spec"] == rig.completed
    assert data["current_output_contract"]["kind"] == "world_spec"
    # The actual nested schema reaches both the native tool and rendered task,
    # including the fields that the original production strings could not fill.
    schema = planning_submission_contract("world_spec")["json_schema"]
    assert request.tools[0]["function"]["parameters"] == schema
    assert '"HistoryEventInput"' in request.user_prompt
    assert '"WorldRuleInput"' in request.user_prompt
    return data


async def test_world_actual_native_entry_returns_typed_payload_and_durable_full_receipt(
    entry, monkeypatch,
):
    outputs = install(monkeypatch, entry)
    value, run_id = await invoke(entry)
    assert len(entry.calls) == 1 and not entry.failures
    assert_request(entry.calls[0], entry)
    raw_value, arguments, returned = outputs[0]
    assert {k: v for k, v in value.items() if k != "_meta"} == raw_value
    assert run_id == returned.llm_run_id
    await entry.session.commit()
    await entry.session.refresh(entry.workflow)
    receipt = entry.workflow.metadata_json["planning_submissions"][str(run_id)]
    contract = planning_submission_contract("world_spec")
    assert receipt["raw_arguments"] == receipt["asset_json"] == arguments
    assert receipt["wire_format"] == "native_asset_object"
    assert receipt["schema_sha256"] == contract["schema_sha256"]
    assert receipt["original_model_schema_sha256"] == contract["original_model_schema_sha256"]
    compact = value["_meta"]["planning_submission_receipt"]
    assert not {"raw_arguments", "original_content", "asset_json"} & compact.keys()
    assert compact["original_receipt_sha256"] == planner._approved_payload_hash(receipt)
    assert (await entry.session.get(LlmRunModel, run_id)).metadata_json == {
        "physical_arguments": arguments,
    }
    assert value["_meta"]["approved_complete"] is False
    assert value["_meta"]["planning_status"] == "generated_pending_semantic_review"


async def test_old_world_string_lists_exhaust_bounded_retry_without_compiled_fallback(
    entry, monkeypatch,
):
    install(monkeypatch, entry, invalid=True)
    with pytest.raises(planner.PlannerFallbackError):
        await invoke(entry)
    assert len(entry.calls) == len(entry.failures) == 4
    original = assert_request(entry.calls[0], entry)
    for request in entry.calls[1:]:
        assert assert_request(request, entry) == original
        assert ("Submit only submit_world_spec with the complete typed asset object"
                in request.user_prompt)
    assert all("schema_type:$/rules/0:object" in f["error_code"] for f in entry.failures)
    assert len(list(await entry.session.scalars(select(LlmRunModel)))) == 4
    assert not (entry.workflow.metadata_json or {}).get("planning_submissions")
    assert list(await entry.session.scalars(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.artifact_type == "world_spec"
    ))) == []


async def test_world_canonical_change_stops_next_dispatch_keeps_first_raw_attempt(
    entry, monkeypatch,
):
    outputs = install(monkeypatch, entry, invalid=True, change_after_first=True)
    with pytest.raises(ValueError, match="canonical_changed_during_generation"):
        await invoke(entry)
    assert len(entry.calls) == len(entry.failures) == len(outputs) == 1
    runs = list(await entry.session.scalars(select(LlmRunModel)))
    assert len(runs) == 1 and runs[0].metadata_json["physical_arguments"] == outputs[0][1]
    assert not (entry.workflow.metadata_json or {}).get("planning_submissions")

"""Actual SQLite planner entry and scalar submission, with inert completions.

No schema/parser/canonical/reuse bypasses. Only the physical completion boundary
is replaced, so these tests establish transport contracts, never paid grades.
"""

from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from test_source_bound_planning_model_completion_entry import entry as entry
from test_source_bound_planning_model_completion_entry import invoke

from bestseller.infra.db.models import LlmRunModel, PlanningArtifactVersionModel, ProjectModel
from bestseller.services import planner
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.source_bound_planning_content import (
    CONTENT_POLICY,
    PLANNING_PROMPT_VERSION,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def retain_failure_receipt(entry, monkeypatch):
    # The shared old fixture deep-copies Exception objects. Production writes
    # strings/receipt values; capture those here without reconstructing the
    # exception through its constructor or touching the frozen fixture file.
    def retain(**values):
        error = values["error"]
        entry.failures.append({
            "project_id": str(values["project"].id),
            "content": values["content"], "attempt": values["attempt"],
            "error_code": str(error), "receipt": deepcopy(getattr(error, "receipt", None)),
        })

    monkeypatch.setattr(planner, "_persist_failing_planner_output", retain)


def source(request):
    return json.JSONDecoder().raw_decode(request.user_prompt)[0]


def install(monkeypatch, rig, modes, *, raw_message=False, change_after_first=False):
    outputs = []

    async def complete(session, settings, request):
        assert session is rig.session and session.bind.dialect.name == "sqlite"
        rig.calls.append(request)
        mode = modes[min(len(rig.calls) - 1, len(modes) - 1)]
        payload = deepcopy(rig.completed)
        payload["literal_note"] = "CURRENT_ASSET_LITERAL_NOT_A_RECEIPT_DUPLICATE"
        text = json.dumps(payload, ensure_ascii=False)
        if mode.startswith("duplicate"):
            text = text[:-1] + ',"theme_statement":""}'
        elif mode == "wrong_kind":
            text = '[{"volume_number":1,"volume_title":"wrong asset must not replace book"}]'
        native = mode != "text" and mode != "duplicate_text"
        content = "" if native else text
        calls = [{"id": f"isolated-native-{len(rig.calls)}", "type": "function", "function": {
            "name": "submit_book_spec",
            "arguments": json.dumps({"asset_json": text}, ensure_ascii=False),
        }}] if native else None
        row = LlmRunModel(
            project_id=request.project_id, workflow_run_id=request.workflow_run_id,
            logical_role=request.logical_role, provider="minimax", model_name="isolated-fixture",
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            finish_reason="tool_calls" if native else "stop", metadata_json={},
        )
        session.add(row)
        await session.flush()
        if change_after_first and len(rig.calls) == 1:
            await drift(rig)
        result = LLMCompletionResult(
            content=content, provider="minimax", model_name="isolated-fixture", llm_run_id=row.id,
            finish_reason=row.finish_reason, tool_calls=calls,
            raw_message={"role": "assistant", "content": content, "tool_calls": deepcopy(calls)}
            if raw_message else None,
        )
        outputs.append(deepcopy(result))
        return result

    monkeypatch.setattr(planner, "complete_text", complete)
    return outputs


async def drift(rig):
    await rig.session.execute(update(ProjectModel).where(ProjectModel.id == rig.project.id).values(
        metadata_json={**rig.project.metadata_json, "creation_canonical_snapshot_id": str(uuid4())},
    ).execution_options(synchronize_session=False))


def assert_request(request, rig):
    assert request.prompt_version == PLANNING_PROMPT_VERSION
    assert len(request.prompt_version) <= 32
    assert request.prompt_template == "planner_book_spec"
    assert request.project_id == rig.project.id and request.workflow_run_id == rig.workflow.id
    assert request.tool_choice == {"type": "function", "function": {"name": "submit_book_spec"}}
    assert len(request.tools) == 1
    parameters = request.tools[0]["function"]["parameters"]
    assert parameters["required"] == ["asset_json"]
    assert parameters["properties"]["asset_json"]["type"] == "string"
    assert parameters["additionalProperties"] is False
    data = source(request)
    assert data["protocol"] == CONTENT_POLICY
    assert data["approved_sources"]["canonical_snapshot"]["content"] == rig.canonical.content
    assert list(data)[-1] == "current_output_contract"
    contract = data["current_output_contract"]
    assert contract["kind"] == "book_spec" and contract["expected_root"] == "object"
    assert contract["native_submission"]["function_name"] == "submit_book_spec"
    assert contract["proposed_output_schema"]["duplicate_object_keys"] == "forbidden"
    assert contract["proposed_output_schema"]["wrapper_or_other_asset_kind"] == "forbidden"
    return data


@pytest.mark.parametrize("mode,raw_message", [("native", False), ("native", True), ("text", True)])
async def test_actual_kind_entry_persists_full_receipt_only_in_workflow(entry, monkeypatch, mode,
                                                                    raw_message):
    entry.workflow.metadata_json = {"older_paid_audit": {"keep": "unchanged"}}
    await entry.session.commit()
    outputs = install(monkeypatch, entry, [mode], raw_message=raw_message)
    result, run_id = await invoke(entry)
    assert len(entry.calls) == 1
    assert_request(entry.calls[0], entry)
    await entry.session.commit()
    await entry.session.refresh(entry.workflow)
    full = entry.workflow.metadata_json["planning_submissions"][str(run_id)]
    compact = result["_meta"]["planning_submission_receipt"]
    assert full["status"] == "verified" and full["kind"] == "book_spec"
    assert full["channel"] == ("text_json" if mode == "text" else "tool_call")
    assert full["original_content"] == outputs[0].content
    assert full["raw_message_present"] is raw_message
    if mode == "native":
        assert full["raw_arguments"] == outputs[0].tool_calls[0]["function"]["arguments"]
    assert compact["original_receipt_sha256"] == planner._approved_payload_hash(full)
    assert compact["workflow_run_id"] == str(entry.workflow.id)
    assert compact["llm_run_id"] == str(run_id)
    assert not {"raw_arguments", "original_content", "asset_json"} & compact.keys()
    assert "CURRENT_ASSET_LITERAL_NOT_A_RECEIPT_DUPLICATE" not in json.dumps(result["_meta"])
    assert result["literal_note"] == "CURRENT_ASSET_LITERAL_NOT_A_RECEIPT_DUPLICATE"
    assert result["_meta"]["planning_status"] == "generated_pending_semantic_review"
    assert result["_meta"]["approved_complete"] is False
    assert entry.workflow.metadata_json["older_paid_audit"] == {"keep": "unchanged"}
    assert (await entry.session.get(LlmRunModel, run_id)).workflow_run_id == entry.workflow.id


@pytest.mark.parametrize("mode", ["duplicate_native", "duplicate_text", "wrong_kind"])
async def test_invalid_kind_or_duplicate_never_falls_back_to_compiled_asset(
    entry, monkeypatch, mode,
):
    install(monkeypatch, entry, [mode])
    with pytest.raises(planner.PlannerFallbackError):
        await invoke(entry)
    assert len(entry.calls) == len(entry.failures) == 4
    expected = "duplicate_json_key" if mode.startswith("duplicate") else "asset_object_required"
    assert all(f["error_code"] == f"planning_submission:{expected}" for f in entry.failures)
    assert all(f["receipt"]["kind"] == "book_spec" for f in entry.failures)
    assert len(list(await entry.session.scalars(select(LlmRunModel)))) == 4
    first = assert_request(entry.calls[0], entry)
    for request in entry.calls[1:]:
        assert assert_request(request, entry) == first
        assert "CURRENT_ASSET=book_spec" in request.user_prompt
        assert "Submit only submit_book_spec(asset_json)" in request.user_prompt
        assert "Every JSON key must be unique" in request.user_prompt
    assert not (entry.workflow.metadata_json or {}).get("planning_submissions")
    assert list(await entry.session.scalars(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.artifact_type == "book_spec"
    ))) == []


async def test_wrong_kind_retry_preserves_full_source_and_contract_then_records_success(
    entry, monkeypatch,
):
    outputs = install(monkeypatch, entry, ["wrong_kind", "native"], raw_message=True)
    result, run_id = await invoke(entry)
    assert len(entry.calls) == 2 and len(entry.failures) == 1
    original = assert_request(entry.calls[0], entry)
    assert assert_request(entry.calls[1], entry) == original
    assert "Submit only submit_book_spec(asset_json)" in entry.calls[1].user_prompt
    assert len(list(await entry.session.scalars(select(LlmRunModel)))) == 2
    assert run_id == outputs[1].llm_run_id
    assert result["_meta"]["generation_llm_run_id"] == str(outputs[1].llm_run_id)
    receipts = entry.workflow.metadata_json["planning_submissions"]
    assert set(receipts) == {str(outputs[1].llm_run_id)}
    assert receipts[str(run_id)]["raw_arguments"] == (
        outputs[1].tool_calls[0]["function"]["arguments"]
    )


async def test_changed_canonical_selection_before_dispatch_spends_zero_calls(entry, monkeypatch):
    install(monkeypatch, entry, ["native"])
    await drift(entry)
    with pytest.raises(ValueError, match="canonical_changed_during_generation"):
        await invoke(entry)
    assert entry.calls == []
    assert list(await entry.session.scalars(select(LlmRunModel))) == []


async def test_canonical_drift_after_wrong_kind_blocks_next_dispatch_without_reset(
    entry, monkeypatch,
):
    outputs = install(monkeypatch, entry, ["wrong_kind", "native"], change_after_first=True)
    with pytest.raises(ValueError, match="canonical_changed_during_generation"):
        await invoke(entry)
    assert len(entry.calls) == len(entry.failures) == len(outputs) == 1
    assert len(list(await entry.session.scalars(select(LlmRunModel)))) == 1
    assert not (entry.workflow.metadata_json or {}).get("planning_submissions")

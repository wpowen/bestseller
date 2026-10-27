"""Native planning admission through complete_text and actual SQLite receipts.

Only the provider transport and approved source reads are fixture boundaries.
No provider is contacted; these tests prove protocol/binding, not story truth.
"""

from copy import deepcopy
import hashlib
import json
from uuid import UUID

import pytest
from sqlalchemy import select
from test_planning_draft_admission import (
    FAIL, _assess_protocol, full_candidate, grade_payload,
)
from test_planning_draft_admission import harness as harness  # noqa: F401

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel,
)
from bestseller.domain.enums import ArtifactType
from bestseller.services import llm, planning_draft_admission as admission
from bestseller.services.lossless_source_encoding import decode_lossless_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "audit:admission-fixture"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _native_grade(candidate, verdict=None):
    """Construct future consistent fixtures with no conflict-only field.

    This is fixture creation only; retained paid responses are never repaired.
    """
    return grade_payload(candidate) if verdict is None else grade_payload(candidate, verdict)


def _protected_inputs(h):
    return deepcopy((
        {key: value for key, value in h.kwargs().items() if key != "project"},
        h.project.metadata_json, h.commercial,
    ))


def _logical_prompt(request):
    if request.metadata["source_transport"]["encoding"] == "identity":
        return json.loads(request.user_prompt)
    text = request.user_prompt.split("source_review_data=", 1)[1]
    encoded, end = json.JSONDecoder().raw_decode(text)
    assert text[end:] == ""
    if request.metadata["source_transport"]["schema"]=="planning-review-transport.v4":
        from bestseller.services.planning_catalog_transport import decode
        return decode(encoded)
    return decode_lossless_source(json.loads(_json(encoded)))


def _schema_at(schema, *keys):
    node = schema
    for key in keys:
        while "$ref" in node:
            target = node["$ref"]
            assert target.startswith("#/")
            node = schema
            for part in target[2:].split("/"):
                node = node[part]
        node = node[key]
    return node


@pytest.fixture
async def native(harness, monkeypatch):  # noqa: F811
    h = harness
    h.native_mutation = None
    h.raw_message_present = True
    h.raw = json.dumps(_native_grade(h.kwargs()["candidate_payload"]), ensure_ascii=False)

    async def native_transport(request, role):
        h.sent.append(request)
        assert role.model == "openai/MiniMax-M3"
        assert request.project_id == h.project.id and request.workflow_run_id == h.workflow.id
        assert request.prompt_template == admission.TEMPLATE
        assert request.prompt_version == "planning-draft-admission.v3"
        assert request.tool_choice == {
            "type": "function", "function": {"name": "submit_planning_admission"},
        }
        calls = [{"id": "fixture-native-submission", "type": "function", "function": {
            "name": "submit_planning_admission", "arguments": h.raw,
        }}]
        message = {"role": "assistant", "content": None, "tool_calls": deepcopy(calls)}
        result = {"content": "", "tool_calls": calls,
                  "raw_message": message if h.raw_message_present else None,
                  "finish_reason": "tool_calls"}
        if h.native_mutation:
            h.native_mutation(result)
        return result["content"], 10, 5, result["finish_reason"], result["tool_calls"], result["raw_message"]

    monkeypatch.setattr(llm, "_call_litellm", native_transport)
    return h


async def _physical_rows(h, receipt):
    async with h.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(receipt["llm_run_id"]))
        operation = await session.get(ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"])
        attempts = list(await session.scalars(select(ExecutionAttemptModel).where(
            ExecutionAttemptModel.operation_key == operation.key)))
        assert len(attempts) == 1
        attempt = attempts[0]
        key = hashlib.sha256(f"transport-result.v1\n{SCOPE}\n{attempt.id}".encode()).hexdigest()
        transport = await session.get(ExecutionOperationModel, key)
        budget = await session.get(ExecutionBudgetModel, SCOPE)
        return run, operation, attempt, transport, budget


@pytest.mark.parametrize("raw_message_present", [True, False])
async def test_native_actual_gateway_retains_exact_args_and_replays_without_new_credit(native, raw_message_present):
    h = native
    h.raw_message_present = raw_message_present
    h.raw = "\n" + json.dumps(json.loads(h.raw), ensure_ascii=False, indent=3) + " \n"
    before = _protected_inputs(h)
    state = {}
    receipt = await _assess_protocol(h, state)
    assert receipt["draft_eligible"] is True and receipt["raw_grade"] == h.raw
    assert receipt["submission_receipt"]["channel"] == "tool"
    assert receipt["submission_receipt"]["raw_arguments"] == h.raw
    # The real gateway constructs its normalized assistant replay message from
    # top-level tool_calls even when the SDK stub supplied no raw message.
    assert receipt["submission_receipt"]["raw_message_present"] is True
    completion = receipt["protocol_attempts"][0]["completion"]
    assert completion["content"] == "" and completion["finish_reason"] == "tool_calls"
    run, root, attempt, transport, budget = await _physical_rows(h, receipt)
    assert run.project_id == h.project.id and run.workflow_run_id == h.workflow.id
    assert root.status == "completed" and transport.status == "transport_result"
    assert attempt.status == "completed" and attempt.scope_id == root.scope_id == SCOPE
    assert transport.result_json["operation_key"] == root.key
    assert transport.result_json["input_hash"] == run.prompt_hash == receipt["effective_input_hash"]
    for saved in (root.result_json, transport.result_json["result"]):
        assert saved["content"] == "" and saved["finish_reason"] == "tool_calls"
        assert saved["tool_calls"] == completion["tool_calls"]
        assert saved["raw_message"] == completion["raw_message"]
        assert saved["tool_calls"][0]["function"]["arguments"] == h.raw
    assert budget.calls_used == 1
    assert await _assess_protocol(h, json.loads(json.dumps(state))) == receipt
    assert len(h.sent) == 1 and _protected_inputs(h) == before
    assert (await _physical_rows(h, receipt))[-1].calls_used == 1


async def test_two_distinct_issues_on_same_scene_are_losslessly_retained_and_not_admitted(native):
    h = native
    first = deepcopy(FAIL["hard_violations"][0])
    second = {**deepcopy(first), "evidence": "A second independent violation remains in this same scene.",
              "source_requirement": "The same actual source clause is independently contradicted."}
    payload = _native_grade(h.kwargs()["candidate_payload"], {
        **FAIL, "hard_violations": [first, second],
    })
    for check in payload["projection_checks"]:
        check.update(status="conflict", related_violation_paths=[first["path"]])
    h.raw = json.dumps(payload, ensure_ascii=False, indent=2)
    receipt = await _assess_protocol(h, {})
    assert receipt["assessment"]["hard_violations"] == [first, second]
    assert receipt["submission_receipt"]["issues_dropped"] == 0
    assert receipt["raw_grade"] == h.raw and receipt["draft_eligible"] is False
    async with h.session_factory() as session:
        with pytest.raises(ValueError, match="admission_hard_rejected"):
            await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(), receipt=receipt)
    assert len(h.sent) == 1


@pytest.mark.parametrize("location,field", [
    ("root", "tool_calls"), ("root", "raw_message"),
    ("transport", "tool_calls"), ("transport", "raw_message"),
])
async def test_actual_sqlite_paid_or_transport_tool_evidence_mismatch_fails_closed(native, location, field):
    h = native
    receipt = await _assess_protocol(h, {})
    _, root, _, transport, _ = await _physical_rows(h, receipt)
    async with h.session_factory() as session:
        row = await session.get(ExecutionOperationModel, root.key if location == "root" else transport.key)
        saved = deepcopy(row.result_json)
        target = saved if location == "root" else saved["result"]
        calls = target["tool_calls"] if field == "tool_calls" else target["raw_message"]["tool_calls"]
        # Keep a parseable Grade and unchanged scores/verdict, but alter exact bytes.
        calls[0]["function"]["arguments"] += " "
        row.result_json = saved
        await session.commit()
    before = deepcopy(receipt)
    async with h.session_factory() as session:
        error = "admission_paid_grade_mismatch" if location == "root" else "admission_transport_return_missing"
        with pytest.raises(ValueError, match=error):
            await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(), receipt=receipt)
    assert receipt == before and len(h.sent) == 1
    assert (await _physical_rows(h, receipt))[-1].calls_used == 1


@pytest.mark.parametrize("fault", [
    "shape", "incomplete_json", "unknown_scope", "leaf_scope", "candidate_prefix",
    "compound", "dangling_dependency", "consistent_with_related",
    "conflict_missing_related", "conflict_empty_related",
])
async def test_native_protocol_invalid_stays_unknown_and_recovery_cannot_buy_new_rounds(native, fault):
    h = native
    payload = json.loads(h.raw)
    if fault == "shape":
        payload["draft_eligible"] = "true"
    elif fault in {"unknown_scope", "leaf_scope", "candidate_prefix", "compound"}:
        payload = _native_grade(h.kwargs()["candidate_payload"], FAIL)
        payload["hard_violations"][0]["path"] = {
            "unknown_scope": "chapters[99]", "leaf_scope": "chapters[0].scenes[0].action",
            "candidate_prefix": "candidate:chapters[0].scenes[0]",
            "compound": "chapters[0].scenes[0] + chapters[0]",
        }[fault]
    elif fault == "dangling_dependency":
        payload["projection_checks"][0].update(status="conflict", related_violation_paths=["chapters[0].scenes[0]"])
    elif fault == "consistent_with_related":
        payload["projection_checks"][0]["related_violation_paths"] = []
    elif fault in {"conflict_missing_related", "conflict_empty_related"}:
        payload = _native_grade(h.kwargs()["candidate_payload"], FAIL)
        payload["projection_checks"][0]["status"] = "conflict"
        if fault == "conflict_empty_related":
            payload["projection_checks"][0]["related_violation_paths"] = []
    h.raw = '{"draft_eligible":true,' if fault == "incomplete_json" else json.dumps(payload)
    before = _protected_inputs(h)
    state = {}
    for _ in range(2):
        with pytest.raises(admission.PlanningDraftAdmissionProtocolError) as error:
            await _assess_protocol(h, state)
        assert error.value.receipt["status"] == state["status"] == "protocol_exhausted"
        assert len(state["attempts"]) == len(h.sent) == admission.MAX_PROTOCOL_REPAIRS + 1
        assert all(item["status"] == "protocol_invalid" for item in state["attempts"])
        assert all(item["completion"]["tool_calls"][0]["function"]["arguments"] == h.raw
                   for item in state["attempts"])
        assert "assessment" not in state and "draft_eligible" not in state
    assert _protected_inputs(h) == before
    async with h.session_factory() as session:
        budget = await session.get(ExecutionBudgetModel, SCOPE)
        assert budget.calls_used == 2
        assert len(list(await session.scalars(select(LlmRunModel)))) == 2


async def test_post_gateway_raw_message_conflict_cannot_diverge_from_original_paid_return(native, monkeypatch):
    h = native
    gateway = admission.complete_text

    async def changed_completion(*args, **kwargs):
        completion = await gateway(*args, **kwargs)
        raw = deepcopy(completion.raw_message)
        raw["tool_calls"][0]["function"]["arguments"] += " "
        return completion.model_copy(update={"raw_message": raw})

    monkeypatch.setattr(admission, "complete_text", changed_completion)
    state = {}
    with pytest.raises(ValueError, match="admission_paid_grade_mismatch"):
        await _assess_protocol(h, state)
    assert len(h.sent) == 1 and len(state["attempts"]) == 1
    assert "assessment" not in state and "draft_eligible" not in state
    async with h.session_factory() as session:
        root = await session.scalar(select(ExecutionOperationModel).where(ExecutionOperationModel.status == "completed"))
        assert root.result_json["tool_calls"] == root.result_json["raw_message"]["tool_calls"]
        assert (await session.get(ExecutionBudgetModel, SCOPE)).calls_used == 1


async def test_native_request_declares_only_exact_target_scopes_not_leaf_or_source_paths(native):
    h = native
    await _assess_protocol(h, {})
    request = h.sent[0]
    logical = _logical_prompt(request)
    targets = logical["input"]["projection_audit_targets"]
    expected = [item["path"] for item in targets]
    assert expected == ["chapters[0]", "chapters[0].scenes[0]"]
    schema = request.tools[0]["function"]["parameters"]
    assert _schema_at(schema, "properties", "hard_violations", "items", "properties", "path")["enum"] == expected
    item = _schema_at(schema, "properties", "projection_checks", "items")
    assert len(item["oneOf"]) == 2
    assert '"$defs"' not in _json(schema) and '"$ref"' not in _json(schema)
    variants = {branch["properties"]["status"]["enum"][0]: branch for branch in item["oneOf"]}
    common = {"path", "projection_hash", "status", "explanation"}
    assert set(variants) == {"consistent", "conflict"}
    assert set(variants["consistent"]["properties"]) == common
    assert set(variants["conflict"]["properties"]) == common | {"related_violation_paths"}
    for branch in variants.values():
        assert set(branch["required"]) == set(branch["properties"])
        assert branch["additionalProperties"] is False
        assert branch["properties"]["path"]["enum"] == expected
    related = variants["conflict"]["properties"]["related_violation_paths"]
    assert related["minItems"] >= 1 and related["items"]["enum"] == expected
    assert _schema_at(schema, "properties", "hard_violations", "items", "properties", "source_refs")["items"].get("enum") is None
    transport_capacity=request.metadata['source_transport']['request_capacity']
    assert transport_capacity['effective_max_output_tokens']==6000
    assert transport_capacity['tool_schema_chars']>0 and transport_capacity['total_budget']==65536
    assert transport_capacity['safety_factor']==2.2 and transport_capacity['truncated'] is False
    capacity = request.metadata["planning_admission_input_receipt"]
    assert capacity["tools_sha256"] == admission.content_hash(request.tools)
    assert capacity == admission._input_capacity_receipt(request.user_prompt, request.tools)
    assert capacity["estimated_input_tokens"] > admission._input_capacity_receipt(request.user_prompt)["estimated_input_tokens"]


async def test_overflow_native_request_decodes_full_source_and_preserves_hashes_controls_and_time_order(native):
    h = native
    full = '完整证据含尾部否定：此处只是计划，尚未发生。标点“引文”保持。' * 300
    candidate = {"chapters": [{"chapter_number": 1, "goal": full, "scenes": [{
        "scene_number": 1, "entry_state": {"summary": "Before these planned actions."},
        "action_sequence": [full] * 20, "exit_state": {"summary": "After the ordered actions."},
        "authored_literal": {"$ref": "/not-authority"},
    }]}]}
    original_kwargs = h.kwargs
    h.kwargs = lambda: {**original_kwargs(), "candidate_payload": deepcopy(candidate)}
    h.raw = json.dumps(_native_grade(candidate))
    before = deepcopy((candidate, h.project.metadata_json, h.commercial))
    receipt = await _assess_protocol(h, {})
    request = h.sent[0]
    assert request.metadata["source_transport"]["encoding"] == "json-pointer"
    logical = _logical_prompt(request)
    assert len(_json(logical)) > 120_000
    assert logical["input"]["candidate_payload"] == candidate
    assert logical["input"]["untrusted_commercial_report"] == h.commercial
    assert logical["input"]["approved_creation_contracts"]["genre_intent_contract"]["tags"] == ["EXPLICIT_COMEDY", "EXPLICIT_HYPE"]
    assert logical["input"]["temporal_positions"][0]["ordered_actions"] == [
        f"chapters[0].scenes[0].action_sequence[{i}]" for i in range(20)]
    assert logical["graded_input_hash"] == receipt["graded_input_hash"]
    assert admission.content_hash(logical["input"]) == receipt["graded_input_hash"]
    assert receipt["candidate_hash"] == admission.content_hash(candidate)
    assert request.metadata["planning_admission_input_receipt"]["budget_passed"] is True
    assert (candidate, h.project.metadata_json, h.commercial) == before


async def test_native_tool_schema_capacity_is_checked_before_any_paid_claim(native, monkeypatch):
    from bestseller.services import planning_admission_submission
    h = native
    original = planning_admission_submission.planning_admission_tool

    def large_tool(*args, **kwargs):
        tool = original(*args, **kwargs)
        # A real, non-droppable schema enum, not a story-field placeholder.
        tool["function"]["parameters"]["properties"]["editorial_notes"]["items"]["enum"] = ["x" * 130_000]
        return tool

    monkeypatch.setattr(planning_admission_submission, "planning_admission_tool", large_tool)
    state = {}
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError) as error:
        await _assess_protocol(h, state)
    assert error.value.code == "admission_input_budget_exceeded"
    assert state["attempts"] == [] and state["status"] == "input_budget_blocked"
    assert h.sent == []
    async with h.session_factory() as session:
        assert await session.get(ExecutionBudgetModel, SCOPE) is None
        assert not list(await session.scalars(select(ExecutionOperationModel)))


@pytest.mark.parametrize("kind,field", [
    (ArtifactType.BOOK_SPEC, "approved_book_spec"),
    (ArtifactType.WORLD_SPEC, "approved_world_spec"),
    (ArtifactType.CAST_SPEC, "approved_cast_spec"),
])
async def test_producer_root_meta_is_not_story_but_still_fences_full_foundation_identity(
    harness, kind, field,
):
    h = harness
    artifact = h.sources[kind]
    artifact.content.update({
        "unknown_story_field": {
            "_meta": {"narrator_belief": "The letter might be forged; this is a story qualifier."},
            "unrecognized_extension": [None, False, 3, {"$ref": "/literal-story-symbol"}],
        },
        "metadata": {"story_record": "This differently named root field must remain complete."},
        "_meta": {
            **artifact.content.get("_meta", {}),
            "producer_receipt": {"generation_run_id": "inert-original-run", "state": "completed"},
            "old_runtime_history": "producer-only replay evidence; not a character fact\n" * 2500,
        },
    })
    before_full = deepcopy(artifact.content)
    before_kwargs = h.kwargs()
    original_project = deepcopy(h.project.metadata_json)

    async def current_input(kwargs):
        async with h.session_factory() as session:
            return await admission._input(
                h.wrap(session), **kwargs, commercial_report=h.commercial, check_initial_context=True,
            )

    first = await current_input(before_kwargs)
    expected_story = {key: deepcopy(value) for key, value in before_full.items() if key != "_meta"}
    assert first[field] == expected_story
    assert "_meta" not in first[field]
    assert first[field]["unknown_story_field"]["_meta"] == before_full["unknown_story_field"]["_meta"]
    assert first[field]["metadata"] == before_full["metadata"]
    assert first["foundation"][kind.value]["content_hash"] == admission.content_hash(before_full)
    selection = first["foundation_source_selection"]
    assert "/" + field + "/_meta" in selection["excluded_paths"]
    assert selection["full_sources"] == first["foundation"]
    assert selection["story_fields_complete"] is True
    assert selection["producer_metadata_is_authority"] is False
    assert "producer-only replay evidence" not in _json(first)
    assert artifact.content == before_full

    # Only runtime metadata changes; no version or story field is changed.
    artifact.content["_meta"]["producer_receipt"]["generation_run_id"] = "inert-later-replay-owner"
    after_full = deepcopy(artifact.content)
    second = await current_input(h.kwargs())
    assert second[field] == first[field] == expected_story
    assert second["candidate_payload"] == first["candidate_payload"]
    assert second["approved_creation_contracts"] == first["approved_creation_contracts"]
    assert second["foundation"][kind.value]["artifact_id"] == first["foundation"][kind.value]["artifact_id"]
    assert second["foundation"][kind.value]["version"] == first["foundation"][kind.value]["version"]
    assert second["foundation"][kind.value]["content_hash"] == admission.content_hash(after_full)
    assert second["foundation"][kind.value]["content_hash"] != first["foundation"][kind.value]["content_hash"]
    assert second["foundation_source_selection"]["full_sources"] == second["foundation"]
    assert admission.content_hash(second) != admission.content_hash(first)
    with pytest.raises(ValueError, match="admission_foundation_not_current_approved"):
        await current_input(before_kwargs)

    # A nested _meta edit really changes the story view; exclusion must not
    # accidentally grow into a recursive denylist for unknown story content.
    artifact.content["unknown_story_field"]["_meta"]["narrator_belief"] = "The letter is now suspect for a different stated reason."
    third = await current_input(h.kwargs())
    assert third[field] != second[field]
    assert third[field]["unknown_story_field"] == artifact.content["unknown_story_field"]
    assert third[field]["metadata"] == before_full["metadata"]
    assert admission.content_hash(third) != admission.content_hash(second)
    assert h.project.metadata_json == original_project and h.sent == []
    async with h.session_factory() as session:
        assert await session.get(ExecutionBudgetModel, SCOPE) is None
        assert not list(await session.scalars(select(ExecutionOperationModel)))


@pytest.mark.parametrize("content", ["", "Auxiliary commentary; this is not the submitted grade."])
async def test_actual_protocol_error_reports_all_five_native_argument_errors_not_empty_content_json(content):
    candidate = full_candidate()
    payload = _native_grade(candidate)
    assert len(payload["projection_checks"]) == 5
    for check in payload["projection_checks"]:
        check["related_violation_paths"] = ""
    raw = "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + " \n"
    calls = [{"id": "fixture-five-invalid-related-paths", "type": "function", "function": {
        "name": "submit_planning_admission", "arguments": raw,
    }}]
    completion = {"content": content, "finish_reason": "tool_calls", "tool_calls": calls,
                  "raw_message": {"role": "assistant", "content": content, "tool_calls": deepcopy(calls)}}
    original = deepcopy((completion, candidate))
    error = admission._protocol_error(completion, candidate, {})
    assert error["type"] == "PlanningAdmissionSubmissionError"
    assert error["submission_receipt"]["status"] == "rejected"
    assert error["submission_receipt"]["raw_arguments"] == raw
    assert error["submission_receipt"]["original_content"] == content
    feedback = error["feedback"]
    expected = {("projection_checks", i, "related_violation_paths") for i in range(5)}
    actual = {tuple(item["loc"]) for item in feedback if item["code"] == "admission_schema_invalid"}
    assert expected <= actual
    assert all(item["code"] != "admission_json_invalid" for item in feedback)
    assert not any("JSONDecodeError" in _json(item) for item in feedback)
    assert "assessment" not in error and "draft_eligible" not in error
    assert (completion, candidate) == original
    assert all(check["related_violation_paths"] == "" for check in payload["projection_checks"])


@pytest.mark.parametrize("hard_rejected", [False, True])
async def test_plain_commentary_is_audit_only_and_exact_paid_physical_text_is_preserved(native, hard_rejected):
    h = native
    payload = _native_grade(h.kwargs()["candidate_payload"], FAIL if hard_rejected else None)
    if hard_rejected:
        for check in payload["projection_checks"]:
            check.update(status="conflict", related_violation_paths=[FAIL["hard_violations"][0]["path"]])
    h.raw = "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + " \n"
    commentary = "This is auxiliary prose about the review process, not the authoritative submission. " * 180
    assert len(commentary) > 12_564

    def prose(result):
        result["content"] = commentary
        result["raw_message"]["content"] = commentary

    h.native_mutation = prose
    receipt = await _assess_protocol(h, {})
    assert receipt["draft_eligible"] is (not hard_rejected)
    assert receipt["assessment"]["hard_violations"] == payload["hard_violations"]
    assert receipt["raw_grade"] == h.raw
    assert receipt["submission_receipt"]["schema"] == "planning-admission-submission.v3"
    assert receipt["submission_receipt"]["commentary_status"] == "non_authoritative"
    assert receipt["submission_receipt"]["original_content"] == commentary
    assert receipt["protocol_attempts"][0]["completion"]["content"] == commentary
    _, root, _, transport, budget = await _physical_rows(h, receipt)
    for saved in (root.result_json, transport.result_json["result"]):
        assert saved["content"] == saved["raw_message"]["content"] == commentary
        assert saved["tool_calls"][0]["function"]["arguments"] == h.raw
    assert budget.calls_used == len(h.sent) == 1
    if hard_rejected:
        async with h.session_factory() as session:
            with pytest.raises(ValueError, match="admission_hard_rejected"):
                await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(), receipt=receipt)


@pytest.mark.parametrize("form", ["grade", "envelope", "fence", "array", "ambiguous_braces"])
async def test_competing_json_or_ambiguous_commentary_never_overrides_native_grade(native, form):
    h = native
    grade = json.loads(h.raw)
    commentary = {
        "grade": json.dumps(grade),
        "envelope": json.dumps({"assessment": grade}),
        "fence": "```json\n" + json.dumps(grade) + "\n```",
        "array": json.dumps([grade]),
        "ambiguous_braces": 'Some prose then {"draft_eligible":false} and more prose.',
    }[form]

    def competing(result):
        result["content"] = commentary
        result["raw_message"]["content"] = commentary

    h.native_mutation = competing
    state = {}
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await _assess_protocol(h, state)
    assert state["status"] == "protocol_exhausted" and len(h.sent) == 2
    assert "assessment" not in state and "draft_eligible" not in state
    for item in state["attempts"]:
        assert item["completion"]["content"] == commentary
        assert item["completion"]["tool_calls"][0]["function"]["arguments"] == h.raw
        assert item["status"] == "protocol_invalid"


async def test_native_repair_feedback_uses_exact_args_and_hashes_without_repeating_long_commentary(native):
    h = native
    candidate = full_candidate()
    original_kwargs = h.kwargs
    h.kwargs = lambda: {**original_kwargs(), "candidate_payload": deepcopy(candidate)}
    good = _native_grade(candidate)
    bad = deepcopy(good)
    for check in bad["projection_checks"]:
        check["related_violation_paths"] = ""
    bad_raw = "\n" + json.dumps(bad, ensure_ascii=False, indent=2) + " \n"
    h.raw = json.dumps(good, ensure_ascii=False, indent=2)
    commentary = "Auxiliary prose only, with no new story facts or another structured submission. " * 200
    assert len(commentary) > 12_564

    def first_bad(result):
        if len(h.sent) == 1:
            result["tool_calls"][0]["function"]["arguments"] = bad_raw
            result["content"] = commentary
            result["raw_message"] = {"role": "assistant", "content": commentary,
                                     "tool_calls": deepcopy(result["tool_calls"])}

    h.native_mutation = first_bad
    state = {}
    receipt = await _assess_protocol(h, state)
    assert receipt["draft_eligible"] is True and len(h.sent) == 2
    first = state["attempts"][0]["completion"]
    assert first["content"] == first["raw_message"]["content"] == commentary
    assert first["tool_calls"][0]["function"]["arguments"] == bad_raw
    prompt = _logical_prompt(h.sent[1])
    feedback = prompt["protocol_repair"]["previous_attempts"][0]
    assert feedback["raw_submission_sha256"] == admission.content_hash(bad_raw)
    assert feedback["prior_draft_eligible_literal"]["value"] is json.loads(bad_raw)["draft_eligible"]
    assert "raw_grade" not in feedback
    assert feedback["physical_return_sha256"] == admission.content_hash(admission._physical_return(first))
    assert feedback["original_raw_and_replay_metadata_retained_in_audit"] and not feedback["old_grade_rewritten"]
    assert commentary not in h.sent[1].user_prompt
    assert "tool_calls" not in feedback
    assert len(_json(feedback)) < len(commentary)
    expected = {("projection_checks", i, "related_violation_paths") for i in range(5)}
    assert expected <= {tuple(item["loc"]) for item in feedback["error"]["diagnostics"]}
    assert receipt["protocol_attempts"][0]["completion"] == first

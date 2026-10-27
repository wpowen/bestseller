"""Typed, window-bounded patch recovery; no database or provider sends."""
from __future__ import annotations

import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def sqlite_settings(isolate_settings_environment):
    """Refuse accidental production configuration even though these tests do no IO."""
    settings = load_settings()
    url = make_url(settings.database.url)
    assert url.get_backend_name() == "sqlite"
    assert url.database == ":memory:" or "test" in (url.database or "").rsplit("/", 1)[-1]
    return settings


def _outline():
    return {
        "batch_name": "first-rolling-window",
        "chapters": [
            {
                "chapter_number": number, "volume_number": 1,
                "title": f"Parcel {number}",
                "goal": f"Retrieve parcel {number} before the depot closes.",
                "main_conflict": "The guard requires a signed receipt.",
                "hook_description": "The signature belongs to the missing courier.",
                "causal_contract": {"pressure": "The depot closes at dusk.",
                                    "gain_or_reveal": "The old receipt is unreadable."},
                "scenes": [{
                    "scene_number": 1, "participants": ["Courier", "Guard"],
                    "purpose": {"story": "Compare the receipt with the depot register.",
                                "emotion": "The courier fears losing the parcel."},
                    "entry_state": {"summary": "The receipt has not been checked."},
                    "exit_state": {"summary": "The guard identifies a missing signature."},
                    "action_sequence": ["Read the register", "Compare the dates"],
                    "forbidden_actions": ["Invent a signature"],
                }],
            }
            for number in range(1, 10)
        ],
        "_meta": {"artifact_id": str(uuid4()), "version_no": 4,
                  "input_hash": "unchanged-approved-input", "source_snapshot_hash": "unit-source"},
    }


def _patch(scope="chapter", field="goal", value="Trade a delivery pass for the register.", **target):
    return {"chapter_number": 1, "scope": scope, "field": field, "value": value,
            **({"scene_number": 1} if scope == "scene" else {}), **target}


def _without_meta(payload):
    return {key: copy.deepcopy(value) for key, value in payload.items() if key != "_meta"}


@pytest.mark.parametrize("field", ["gain_or_reveal", "chapter_function", "next_reader_desire"])
def test_legitimate_causal_fields_replace_only_the_named_value(field):
    original = _outline()
    before = copy.deepcopy(original)
    repaired = planner._apply_outline_commercial_patches(
        original, [_patch("causal_contract", field, "The receipt reveals the courier's destination.")], strict=True)
    expected = copy.deepcopy(original)
    expected["chapters"][0]["causal_contract"][field] = "The receipt reveals the courier's destination."
    assert repaired == expected
    assert original == before


@pytest.mark.parametrize("field", ["action_sequence", "forbidden_actions"])
@pytest.mark.parametrize("value", [["Ask for the original receipt", "Compare both signatures"], []])
def test_scene_list_patch_is_a_complete_typed_replacement(field, value):
    original = _outline()
    before = copy.deepcopy(original)
    repaired = planner._apply_outline_commercial_patches(
        original, [_patch("scene", field, value)], strict=True)
    expected = copy.deepcopy(original)
    expected["chapters"][0]["scenes"][0][field] = value
    assert repaired == expected
    assert original == before
    ChapterOutlineBatchInput.model_validate(repaired)


@pytest.mark.parametrize("invalid", ["Read register then compare dates", ["Read register", 7]])
def test_scene_action_sequence_wrong_type_fails_atomically_instead_of_noop(invalid):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="(?i)(array|list|strings|type)"):
        planner._apply_outline_commercial_patches(
            original, [_patch(), _patch("scene", "action_sequence", invalid)], strict=True)
    assert original == before


@pytest.mark.parametrize("bad_target", [12, "1", True, 1.0])
def test_nonexistent_or_noninteger_chapter_is_not_coerced_into_a_valid_target(bad_target):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="(?i)(chapter|integer)"):
        planner._apply_outline_commercial_patches(original, [_patch(chapter_number=bad_target)], strict=True)
    assert original == before


@pytest.mark.parametrize("bad_target", [12, "1", True, 1.0])
def test_nonexistent_or_noninteger_scene_is_not_coerced_into_a_valid_target(bad_target):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="(?i)(scene|integer)"):
        planner._apply_outline_commercial_patches(
            original, [_patch("scene", "action_sequence", ["Request a receipt"], scene_number=bad_target)], strict=True)
    assert original == before


def test_shared_prompt_protocol_advertises_only_existing_window_targets():
    original = _outline()
    before = copy.deepcopy(original)
    protocol = planner._outline_patch_protocol(original)
    assert protocol["targets"] == {str(n): [1] for n in range(1, 10)}
    assert "12" not in protocol["targets"]
    assert {"gain_or_reveal", "chapter_function", "next_reader_desire"} <= set(protocol["fields"]["causal_contract"])
    assert {"action_sequence", "forbidden_actions"} <= set(protocol["fields"]["scene"])
    assert protocol["value_types"]["action_sequence"] == "array of strings"
    assert original == before


def test_unknown_field_diagnostic_names_target_and_allowed_fields_for_correction():
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError) as captured:
        planner._apply_outline_commercial_patches(
            original, [_patch(field="invented_information_field")], strict=True)
    message = str(captured.value)
    assert "scope='chapter'" in message
    assert "field='invented_information_field'" in message
    assert "allowed fields=" in message and "main_conflict" in message
    assert original == before


def test_window_repair_can_fix_cast_labels_and_contracts_in_one_atomic_candidate():
    original = _outline()
    original["chapters"][0]["methodology_contract"] = {
        "visible_action_or_reaction": "The guard closes the register.",
        "relationship_debts": ["The courier owes the guard a receipt."],
    }
    before = copy.deepcopy(original)
    patches = [_patch("scene", "participants", ["Courier", "Depot owner"],
                      chapter_number=n, scene_number=1) for n in range(1, 10)]
    patches += [_patch("causal_contract", "gain_or_reveal",
        f"The register identifies parcel destination {n}.", chapter_number=n) for n in range(1, 5)]
    patches += [_patch("chapter", "methodology_contract", {
        "visible_action_or_reaction": "The courier points to the dated entry."})]
    repaired = planner._apply_outline_commercial_patches(original, patches, strict=True)
    assert len(patches) > 12
    assert all(c["scenes"][0]["participants"] == ["Courier", "Depot owner"]
               for c in repaired["chapters"])
    assert repaired["chapters"][0]["methodology_contract"]["relationship_debts"] == \
        before["chapters"][0]["methodology_contract"]["relationship_debts"]
    assert [c["chapter_number"] for c in repaired["chapters"]] == list(range(1, 10))
    assert original == before
    ChapterOutlineBatchInput.model_validate(repaired)


def test_idempotent_patch_does_not_discard_other_valid_edits():
    original = _outline()
    before = copy.deepcopy(original)
    patches = [_patch(value=original["chapters"][0]["goal"]),
        _patch("scene", "participants", ["Courier", "Depot owner"])]
    repaired = planner._apply_outline_commercial_patches(original, patches, strict=True)
    assert repaired["chapters"][0]["goal"] == before["chapters"][0]["goal"]
    assert repaired["chapters"][0]["scenes"][0]["participants"] == ["Courier", "Depot owner"]
    assert original == before
    with pytest.raises(planner.PlannerFallbackError, match="no change"):
        planner._apply_outline_commercial_patches(original, patches[:1], strict=True)


def test_wrong_scalar_type_is_not_accepted_as_an_idempotent_patch():
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="must be a string"):
        planner._apply_outline_commercial_patches(original,
            [_patch(), _patch("chapter", "main_conflict", {"invalid": "object"})], strict=True)
    assert original == before


@pytest.mark.parametrize("field", ["concrete_goal", "hook_requirement", "information_control_mode", "cut_point"])
def test_wrong_scene_scalar_type_cannot_hide_beside_a_valid_patch(field):
    original = _outline()
    original["chapters"][0]["scenes"][0][field] = "Read the parcel's original seal."
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="must be a string"):
        planner._apply_outline_commercial_patches(original,
            [_patch(), _patch("scene", field, ["incorrect array"])], strict=True)
    assert original == before


@pytest.mark.parametrize("field,value", [
    ("methodology_contract", "An untyped instruction"),
    ("chapter_information_introduced", "A scalar instead of a list"),
    ("chapter_information_held_back", ["A clue", 7]),
])
def test_new_repairable_contract_and_information_fields_reject_wrong_types(field, value):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="(object|array of strings)"):
        planner._apply_outline_commercial_patches(
            original, [_patch("chapter", field, value)], strict=True)
    assert original == before


def test_large_window_patch_capacity_is_finite_and_targets_remain_window_bound():
    original = _outline()
    protocol = planner._outline_patch_protocol(original)
    assert protocol["capacity_policy"] == "registered-outline-targets.v2"
    assert protocol["max_patches"] > 12
    with pytest.raises(planner.PlannerFallbackError, match="capacity"):
        planner._apply_outline_commercial_patches(original, [_patch()] * (protocol["max_patches"] + 1), strict=True)
    assert 12 not in [int(n) for n in protocol["targets"]]


@pytest.fixture
def repair_harness(monkeypatch, sqlite_settings):
    """Stub only external IO and unrelated commercial gates, keeping real patch logic."""
    source = _outline()
    project = ProjectModel(id=uuid4(), slug="patch-protocol-unit", title="The Missing Receipt",
        genre="fantasy", language="en", target_chapters=50, target_word_count=130000, metadata_json={})
    workflow_id = uuid4()
    requests, responses, validated = [], [], []
    report = {"passed": False, "overall_score": 0.71,
              "repair_directives": ["Make the discovery and action sequence concrete."]}

    async def gateway(_session, _settings, request):
        requests.append(request)
        assert len(requests) <= len(responses), "Unexpected third send or exhausted scripted responses"
        response = responses[len(requests) - 1]
        if isinstance(response, BaseException):
            raise response
        return response

    async def titles(*_args, **_kwargs):
        return []

    def validate(payload, **kwargs):
        assert kwargs["expected_count"] == 9
        assert kwargs["chapter_number_offset"] == 1
        assert kwargs["strict_story_effects"] and kwargs["strict_field_degeneracy"]
        parsed = ChapterOutlineBatchInput.model_validate(copy.deepcopy(payload))
        assert [c.chapter_number for c in parsed.chapters] == list(range(1, 10))
        validated.append(copy.deepcopy(payload))
        return copy.deepcopy(payload)

    async def no_regeneration(*_args, **_kwargs):
        pytest.fail("Patch protocol recovery must never regenerate the outline")

    monkeypatch.setattr(planner, "complete_text", gateway)
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", titles)
    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", validate)
    monkeypatch.setattr(planner, "_generate_volume_outline_batched", no_regeneration)
    monkeypatch.setattr(planner, "_generate_volume_outline_with_repair_loop", no_regeneration)

    async def run():
        return await planner._repair_outline_commercial_findings_surgically(
            object(), sqlite_settings, project=project, workflow_run_id=workflow_id,
            logical_name="volume_1_chapter_outline", outline_payload=source,
            directives=report["repair_directives"], source_report=report,
            validation_context={"volume_number": 1, "expected_count": 9, "chapter_number_offset": 1,
                "cast_spec": {}, "volume_entry": {"volume_number": 1, "chapter_count_target": 9,
                    "start_chapter_number": 1, "end_chapter_number": 9, "rolling_window_index": 1}})

    return {"source": source, "project": project, "workflow_id": workflow_id, "report": report,
            "requests": requests, "responses": responses, "validated": validated, "run": run}


def _completion(patches, *, run_id=None, finish_reason="stop", **overrides):
    values = dict(content=json.dumps({"patches": patches}), provider="minimax", model_name="openai/MiniMax-M3",
                  llm_run_id=run_id or uuid4(), finish_reason=finish_reason)
    values.update(overrides)
    return LLMCompletionResult(**values)


@pytest.mark.asyncio
async def test_invalid_patch_response_gets_diagnostic_correction_with_both_paid_run_ids(repair_harness):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    first_id, second_id = uuid4(), uuid4()
    invalid = [_patch("causal_contract", "gain_or_reveal", "The stamp identifies the sender."),
               _patch("scene", "action_sequence", "Ask then compare"), _patch(chapter_number=12)]
    valid = [_patch("causal_contract", "gain_or_reveal", "The stamp identifies the sender."),
             _patch("scene", "action_sequence", ["Ask for the receipt", "Compare the stamp"])]
    h["responses"].extend([_completion(invalid, run_id=first_id), _completion(valid, run_id=second_id)])
    repaired, final_run_id = await h["run"]()
    assert final_run_id == second_id
    assert len(h["requests"]) == 2 and len(h["validated"]) == 1
    first, correction = h["requests"]
    assert correction.user_prompt != first.user_prompt
    assert "array of strings" in first.user_prompt
    protocol = planner._outline_patch_protocol(before)
    embedded_objects = []
    decoder = json.JSONDecoder()
    for position, character in enumerate(first.user_prompt):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(first.user_prompt[position:])
        except json.JSONDecodeError:
            continue
        embedded_objects.append(value)
    assert protocol in embedded_objects
    assert "action_sequence" in correction.user_prompt
    assert all(request.project_id == h["project"].id and request.workflow_run_id == h["workflow_id"]
               for request in h["requests"])
    expected = planner._apply_outline_commercial_patches(before, valid, strict=True)
    assert _without_meta(repaired) == _without_meta(expected)
    assert h["source"] == before and h["project"].target_chapters == 50
    receipt = repaired["_meta"]["surgical_repair_receipt"]
    assert receipt["before_candidate"] == _without_meta(before)
    assert receipt["source_reference"] == {key: before["_meta"][key] for key in ("artifact_id", "version_no", "input_hash")}
    assert receipt["source_report"] == h["report"]
    assert receipt["after_candidate_hash"] == planner._approved_payload_hash(_without_meta(repaired))
    assert [attempt["llm_run_id"] for attempt in receipt["protocol_attempts"]] + [receipt["llm_run_id"]] == [str(first_id), str(second_id)]
    assert receipt["protocol_attempts"][0]["error"] in correction.user_prompt
    assert str(first_id) in correction.user_prompt


@pytest.mark.asyncio
async def test_persistently_invalid_patch_stops_after_one_corrective_response(repair_harness):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    h["responses"].extend([_completion([_patch(chapter_number=12)]), _completion([_patch(chapter_number=12)])])
    with pytest.raises(planner.PlannerFallbackError, match="chapter does not exist"):
        await h["run"]()
    assert len(h["requests"]) == 2 and not h["validated"]
    assert h["requests"][0].user_prompt != h["requests"][1].user_prompt
    assert h["source"] == before


@pytest.mark.asyncio
async def test_truncated_patch_response_is_never_applied_before_correction(repair_harness):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    h["responses"].extend([_completion([_patch(value="TRUNCATED_REPLY_MUST_NOT_BE_APPLIED")], finish_reason="length"),
                           _completion([_patch(value="The guard trades a stamped receipt for the pass.")])])
    repaired, _ = await h["run"]()
    assert h["source"] == before and len(h["requests"]) == 2
    assert "TRUNCATED_REPLY_MUST_NOT_BE_APPLIED" not in json.dumps(_without_meta(repaired))
    assert "truncated" in repaired["_meta"]["surgical_repair_receipt"]["protocol_attempts"][0]["error"]


@pytest.mark.parametrize("code", ["execution_budget_exhausted", "execution_operation_unknown"])
@pytest.mark.asyncio
async def test_terminal_execution_budget_error_is_rethrown_without_retry(repair_harness, code):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    terminal = ExecutionBudgetError(code, "project:unit-test")
    h["responses"].append(terminal)
    with pytest.raises(ExecutionBudgetError) as caught:
        await h["run"]()
    assert caught.value is terminal
    assert len(h["requests"]) == 1 and not h["validated"] and h["source"] == before


@pytest.mark.asyncio
async def test_budget_error_during_corrective_send_is_not_retried_again(repair_harness):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    terminal = ExecutionBudgetError("execution_authorization_paused", "project:unit-test")
    h["responses"].extend([_completion([_patch(chapter_number=12)]), terminal])
    with pytest.raises(ExecutionBudgetError) as caught:
        await h["run"]()
    assert caught.value is terminal
    assert len(h["requests"]) == 2 and not h["validated"] and h["source"] == before


@pytest.mark.asyncio
async def test_unclassified_provider_failure_is_not_treated_as_invalid_patch(repair_harness):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    unknown = RuntimeError("Unknown paid-send outcome")
    h["responses"].append(unknown)
    with pytest.raises(RuntimeError) as caught:
        await h["run"]()
    assert caught.value is unknown
    assert len(h["requests"]) == 1 and not h["validated"] and h["source"] == before


@pytest.mark.parametrize("overrides", [{"llm_run_id": None}, {"provider": "mock"}, {"fallback_used": True}])
@pytest.mark.asyncio
async def test_unproven_provider_receipt_is_not_retried_or_adopted(repair_harness, overrides):
    h = repair_harness
    before = copy.deepcopy(h["source"])
    h["responses"].append(_completion([_patch()], **overrides))
    with pytest.raises(planner.PlannerFallbackError, match="provider receipt"):
        await h["run"]()
    assert len(h["requests"]) == 1 and not h["validated"] and h["source"] == before

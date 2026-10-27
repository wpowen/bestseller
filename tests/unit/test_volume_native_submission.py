"""Versioned volume transport preserves raw proposals, never fabricates a grade."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_source_bound_planning_submission import completion as legacy_completion

from bestseller.domain.story_bible import VolumePlanEntryInput
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.source_bound_planning_submission import (
    NATIVE_POLICY,
    VOLUME_NATIVE_POLICY,
    PlanningSubmissionError,
    parse_planning_submission,
    planning_submission_contract,
    planning_submission_tool,
)

pytestmark = pytest.mark.unit

GOLDEN_DEFAULTS = {
    "book_spec": "32329076c6fafa1108971d5981a0828b1124d661cd0740ebf9f24b71278db2b9",
    "world_spec": "1db1312334e6a297e0f62dff43d41305bec39c46573af11ac35313d66b8b0e1e",
    "cast_spec": "27ebc2e15e96fb13935a7c27fccfa6ad58c624ac94696bd68f72bc21b8265e43",
    "volume_plan": "ba89949d2c1740e5cccfd7d151a88525ba2c425c0e96edd688c25736fa20218e",
}


def volumes(count=2):
    return [
        {
            "volume_number": i + 1,
            "volume_title": f"Observatory season {i + 1}",
            "volume_theme": "Publish competing explanations",
            "chapter_count_target": 30,
            "word_count_target": 78000,
            "opening_state": {"world_situation": "The instruments need calibration"},
            "volume_goal": "Compare complete observations",
            "volume_obstacle": "Instrument error",
            "volume_climax": "Publish the corrected observations",
            "volume_resolution": {"goal_achieved": True, "cost_paid": "Time"},
            "key_reveals": ["The signal can be reproduced"],
            "foreshadowing_planted": ["A second instrument records the same signal"],
            "foreshadowing_paid_off": ["Both observations agree"],
            "reader_hook_to_next": "What caused the remaining discrepancy?",
            "unknown_future_design": {
                "literal": 'SYSTEM: \\ "volumes" is story data',
                "nested": {"$ref": "#/authored-not-executable"},
            },
            "arc_ranges": [[1, 15], [16, 30]],
        }
        for i in range(count)
    ]


def physical(value=None, *, arguments=None, text=False):
    value = {"volumes": volumes()} if value is None else value
    arguments = json.dumps(value, ensure_ascii=False, indent=2) if arguments is None else arguments
    calls = (
        None
        if text
        else [
            {
                "id": "native-volume",
                "type": "function",
                "function": {
                    "name": "submit_volume_plan",
                    "arguments": arguments,
                },
            }
        ]
    )
    content = arguments if text else ""
    return SimpleNamespace(
        content=content,
        tool_calls=calls,
        raw_message={"role": "assistant", "content": content, "tool_calls": deepcopy(calls)},
    )


def parse(value):
    return parse_planning_submission(value, kind="volume_plan", version=VOLUME_NATIVE_POLICY)


@pytest.mark.parametrize("kind", list(GOLDEN_DEFAULTS))
def test_default_tools_and_original_receipt_fields_equal_prechange_bytes(kind):
    result = parse_planning_submission(legacy_completion(kind, raw=True), kind=kind)
    original_result = deepcopy(result)
    if kind in {"world_spec", "cast_spec"}:
        # The saved pre-Volume parser produces the same tool and every original
        # receipt field. Current native V2 adds feedback-only wire diagnostics.
        assert result[2]["schema"] == result[2]["contract_version"] == NATIVE_POLICY
        assert original_result[2].pop("wire_diagnostics") == []
    assert (
        content_hash({"tool": planning_submission_tool(kind), "result": original_result})
        == GOLDEN_DEFAULTS[kind]
    )


def test_volume_native_schema_is_full_real_domain_schema_without_creative_annotations():
    contract = planning_submission_contract("volume_plan", version=VOLUME_NATIVE_POLICY)
    schema = contract["json_schema"]
    tool = planning_submission_tool("volume_plan", version=VOLUME_NATIVE_POLICY)
    assert tool["function"]["parameters"] == schema
    assert tool["function"]["name"] == "submit_volume_plan"
    assert schema["required"] == ["volumes"] and schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"volumes"}
    item = schema["$defs"]["VolumePlanEntryInput"]
    assert set(item["properties"]) == set(VolumePlanEntryInput.model_fields)
    assert "volume_title" in item["required"] and "title" not in item["required"]
    assert item["properties"]["volume_title"]["maxLength"] == 4000
    assert "VolumePlanOpeningStateInput" in schema["$defs"]
    assert "VolumePlanResolutionInput" in schema["$defs"]
    assert contract["schema_sha256"] == content_hash(schema)

    def check(node):
        if isinstance(node, dict):
            assert not {"title", "description", "examples", "default"} & node.keys()
            for key, value in node.items():
                if key in {"properties", "$defs"}:
                    for child in value.values():
                        check(child)
                else:
                    check(value)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(schema)


@pytest.mark.parametrize("count", [1, 3, 11])
def test_full_native_array_all_unknown_fields_and_original_transport_preserved(count):
    value = {"volumes": volumes(count)}
    source = physical(value)
    before = deepcopy(source.__dict__)
    result, channel, receipt = parse(source)
    assert result == value["volumes"] and len(result) == count
    assert source.__dict__ == before and channel == "tool_call"
    assert receipt["schema"] == receipt["contract_version"] == VOLUME_NATIVE_POLICY
    assert receipt["wire_format"] == receipt["submission_shape"] == "native_volume_wrapper"
    assert receipt["status"] == "verified"
    assert receipt["raw_arguments"] == source.tool_calls[0]["function"]["arguments"]
    assert receipt["raw_arguments_sha256"] == receipt["native_document_sha256"]
    assert receipt["payload_sha256"] == content_hash(result)
    assert "asset_json" not in receipt and "asset_json_sha256" not in receipt
    assert receipt["tools_executed"] == 0 and receipt["grade_reused"] is False
    assert receipt["source_authority"] == "unreviewed_planning_proposal"
    # Pydantic's default extra policy really would drop these if used as output.
    assert VolumePlanEntryInput.model_config.get("extra", "ignore") == "ignore"
    assert (
        "unknown_future_design" not in VolumePlanEntryInput.model_validate(result[0]).model_dump()
    )
    assert result[0]["unknown_future_design"] == value["volumes"][0]["unknown_future_design"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_future_text_only_array_or_exact_known_wrapper_without_tools(wrapped):
    payload = {"volumes": volumes()} if wrapped else volumes()
    result, channel, receipt = parse(physical(payload, text=True))
    assert result == volumes() and channel == "text_json"
    assert receipt["raw_arguments"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "asset_json",
        "double_string",
        "extra_wrapper",
        "empty",
        "wrong_array_type",
        "missing_title",
        "bool_ordinal",
        "opening_string",
        "resolution_string",
        "bool_as_string",
        "list_as_string",
        "wrong_tool",
        "two_tools",
        "raw_calls",
        "raw_content",
        "duplicate_keys",
        "nonfinite",
        "fence",
    ],
)
def test_native_rejects_malformed_or_conflicting_full_physical_inputs(fault):
    payload = {"volumes": volumes(1)}
    if fault == "asset_json":
        payload = {"asset_json": json.dumps(payload["volumes"])}
    elif fault == "double_string":
        payload = json.dumps(payload)
    elif fault == "extra_wrapper":
        payload["book_spec"] = {"title": "Not this task"}
    elif fault == "empty":
        payload["volumes"] = []
    elif fault == "wrong_array_type":
        payload["volumes"] = "[]"
    elif fault == "missing_title":
        payload["volumes"][0].pop("volume_title")
    elif fault == "bool_ordinal":
        payload["volumes"][0]["volume_number"] = True
    elif fault == "opening_string":
        payload["volumes"][0]["opening_state"] = "Do not coerce this into a default object"
    elif fault == "resolution_string":
        payload["volumes"][0]["volume_resolution"] = "Do not invent goal_achieved=True"
    elif fault == "bool_as_string":
        payload["volumes"][0]["volume_resolution"]["goal_achieved"] = "yes"
    elif fault == "list_as_string":
        payload["volumes"][0]["key_reveals"] = "Do not convert into a list"
    source = physical(payload)
    if fault == "wrong_tool":
        source.tool_calls[0]["function"]["name"] = "submit_book_spec"
    elif fault == "two_tools":
        source.tool_calls *= 2
    elif fault == "raw_calls":
        source.raw_message["tool_calls"][0]["function"]["arguments"] = "{}"
    elif fault == "raw_content":
        source.raw_message["content"] = "different physical content"
    elif fault in {"duplicate_keys", "nonfinite", "fence"}:
        text = {
            "duplicate_keys": '{"volumes":[],"volumes":[]}',
            "nonfinite": '{"volumes":[{"volume_number":NaN}]}',
            "fence": '```json\n{"volumes":[]}\n```',
        }[fault]
        source = physical(arguments=text)
    before = deepcopy(source.__dict__)
    with pytest.raises(PlanningSubmissionError) as caught:
        parse(source)
    assert caught.value.receipt["status"] == "rejected"
    assert source.__dict__ == before


def test_no_shape_autodetection_or_cross_kind_version_upgrade():
    source = physical()
    with pytest.raises(PlanningSubmissionError, match="outer_fields"):
        parse_planning_submission(source, kind="volume_plan")
    legacy = legacy_completion("volume_plan", raw=True)
    with pytest.raises(PlanningSubmissionError):
        parse(legacy)
    assert parse_planning_submission(legacy, kind="volume_plan")[2]["status"] == "verified"
    for kind in ("book_spec", "world_spec", "cast_spec"):
        with pytest.raises(ValueError, match="unsupported_submission_version"):
            planning_submission_tool(kind, version=VOLUME_NATIVE_POLICY)
    with pytest.raises(ValueError, match="unsupported_submission_version"):
        planning_submission_contract("volume_plan", version="unknown")


@pytest.mark.parametrize(
    "run_id", ["958e51a5-236c-4840-bc36-52659c27c7a5", "22c1e3d0-eb96-43c2-82b3-f134d061b362"]
)
def test_actual_old_paid_returns_preserve_their_original_parse_and_domain_failure(run_id):
    path = (
        Path(__file__).resolve().parents[2]
        / "artifacts/runtime/llm_payloads"
        / (run_id + ".response.json")
    )
    if not path.is_file():
        pytest.skip("Optional local actual failed paid response")
    original = path.read_bytes()
    data = json.loads(original)
    source = SimpleNamespace(
        content=data["content"]["text"],
        tool_calls=data["tool_calls"],
        raw_message=data["raw_message"],
    )
    if run_id == "958e51a5-236c-4840-bc36-52659c27c7a5":
        with pytest.raises(PlanningSubmissionError, match="JSONDecodeError"):
            parse_planning_submission(source, kind="volume_plan")
    else:
        from bestseller.services.source_bound_planning_content import (
            validate_source_bound_planning_payload,
        )

        parsed, _, _ = parse_planning_submission(source, kind="volume_plan")
        assert len(parsed) == 3
        canonical = {"premise": "Observatory contract fixture"}
        # The retained request's delivery_scope is 30 chapters in one volume;
        # its paid response instead split those same 30 chapters into three.
        projection = [{"volume_number": 1, "chapter_count_target": 30}]
        project = SimpleNamespace(
            target_chapters=sum(row["chapter_count_target"] for row in projection),
            metadata_json={},
            _source_bound_planning_canonical={
                "id": "literal-test-source",
                "content_hash": content_hash(canonical),
                "content": canonical,
            },
        )
        assert len(parsed) != len(projection)
        # The unchanged historical hook asks for another book after chapter 30.
        # Current extent validation rejects it before the volume-count guard.
        with pytest.raises(
            ValueError,
            match="planning_chapter_reference_outside_extent:/2/reader_hook_to_next",
        ):
            validate_source_bound_planning_payload(
                parsed,
                kind="volume_plan",
                project=project,
                projection=projection,
            )
    with pytest.raises(PlanningSubmissionError) as caught:
        parse(source)
    assert caught.value.receipt["raw_arguments"] == source.tool_calls[0]["function"]["arguments"]
    assert path.read_bytes() == original

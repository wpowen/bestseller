"""Lossless Beat consumer shape repair through the public outline compiler."""

from copy import deepcopy
import hashlib
import json
import socket
from types import SimpleNamespace

from pydantic import ValidationError
import pytest
from sqlalchemy.engine import Engine

from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services import fanqie_beat_recovery, llm
from bestseller.services.fanqie_short_planner import build_fanqie_segment_outline_batch
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit

CONTRACTS = (
    "opening_contract",
    "unlock_contract",
    "ability_cost_contract",
    "payoff_contract",
    "closure_contract",
    "continuity_contract",
)


@pytest.fixture(autouse=True)
def forbid_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Beat state tests forbid database/provider/network IO")

    monkeypatch.setattr(Engine, "raw_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    from bestseller.services import fanqie_short_planner

    monkeypatch.setattr(fanqie_short_planner, "complete_text", forbidden)


def payload():
    return {
        "title": "State adapter fixture",
        "pov": "first_person",
        "unlock_milestone_segment": 1,
        "beats": [{"segment_number": 1, "beat_role": "hook", "purpose": "Read the record."}],
    }


def project():
    return SimpleNamespace(target_chapters=1, target_word_count=8000, metadata_json={})


def project_scene(sheet):
    batch = build_fanqie_segment_outline_batch(project(), sheet)
    scene = batch["chapters"][0]["scenes"][0]
    canonical = ChapterOutlineBatchInput.model_validate(batch).chapters[0].scenes[0]
    return batch, scene, canonical


@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize(
    "text",
    [
        "  原始状态：保留空格。\n\t第二行；§0 $ref  ",  # noqa: RUF001 - preserve source punctuation
        "完整状态，不能截断。" * 600,  # noqa: RUF001 - preserve source punctuation
        "",
        " \n\t ",
        "None / 无 / null",
    ],
)
def test_strings_reach_structured_scene_without_text_loss_or_raw_mutation(contract, text):
    raw = payload()
    raw["beats"][0][contract] = {
        "entry_state": text,
        "exit_state": text + "\n尾部  ",
        "opaque": {"entry_state": "not a scene state", "unknown": [None, {"n": 1}]},
    }
    before = deepcopy(raw)
    sheet = FanqieShortBeatSheet.model_validate(raw)
    typed_before = deepcopy(sheet.model_dump(mode="json"))
    _, scene, canonical = project_scene(sheet)

    for field in ("entry_state", "exit_state"):
        expected = {"summary": before["beats"][0][contract][field]}
        assert scene[field] == expected
        assert getattr(canonical, field) == expected
    expected_contract = deepcopy(before["beats"][0][contract])
    expected_contract.update(entry_state=scene["entry_state"], exit_state=scene["exit_state"])
    assert getattr(sheet.beats[0], contract) == expected_contract
    assert raw == before
    assert sheet.model_dump(mode="json") == typed_before

    # Both the consumer and projected scene own their mutable subtrees.
    scene["entry_state"]["summary"] = "changed projection"
    getattr(sheet.beats[0], contract)["opaque"]["unknown"][1]["n"] = 2
    assert raw == before


@pytest.mark.parametrize("contract", CONTRACTS)
def test_explicit_state_mappings_and_all_other_keys_remain_unchanged(contract):
    raw = payload()
    raw["beats"][0][contract] = {
        "entry_state": {"summary": "  exact summary\n", "known": [None, {"count": 0}]},
        "exit_state": {},
        "participants": ["Mara"],
        "action_sequence": ["Read the record."],
        "opaque": {"exit_state": "nested text is untouched", "n": 1.5, "flag": False},
    }
    before = deepcopy(raw)
    sheet = FanqieShortBeatSheet.model_validate(raw)
    _, scene, canonical = project_scene(sheet)
    assert getattr(sheet.beats[0], contract) == before["beats"][0][contract]
    for field in ("entry_state", "exit_state"):
        assert scene[field] == before["beats"][0][contract][field]
        assert getattr(canonical, field) == scene[field]
    getattr(sheet.beats[0], contract)["entry_state"]["known"][1]["count"] = 9
    assert raw == before


@pytest.mark.parametrize("field", ["entry_state", "exit_state"])
@pytest.mark.parametrize("other", ["different state", {"summary": "different state"}])
def test_contradictory_source_contracts_still_reject(field, other):
    raw = payload()
    raw["beats"][0]["opening_contract"] = {field: "source state"}
    raw["beats"][0]["continuity_contract"] = {field: other}
    before = deepcopy(raw)
    sheet = FanqieShortBeatSheet.model_validate(raw)
    with pytest.raises(ValueError, match=f"fanqie_projection_conflicting_scene_field:{field}"):
        build_fanqie_segment_outline_batch(project(), sheet)
    assert raw == before


@pytest.mark.parametrize("field", ["entry_state", "exit_state"])
def test_equivalent_string_and_mapping_contracts_agree(field):
    raw = payload()
    raw["beats"][0]["opening_contract"] = {field: " exact source\n"}
    raw["beats"][0]["continuity_contract"] = {field: {"summary": " exact source\n"}}
    _, scene, canonical = project_scene(FanqieShortBeatSheet.model_validate(raw))
    assert scene[field] == {"summary": " exact source\n"}
    assert getattr(canonical, field) == scene[field]


@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("field", ["entry_state", "exit_state"])
@pytest.mark.parametrize("value", [[], ["text"], [["summary", "text"]], 7, 1.5, False])
def test_unrecognized_state_types_are_retained_then_strictly_rejected(contract, field, value):
    raw = payload()
    raw["beats"][0][contract] = {field: value}
    before = deepcopy(raw)
    sheet = FanqieShortBeatSheet.model_validate(raw)
    actual = getattr(sheet.beats[0], contract)[field]
    assert actual == value
    assert type(actual) is type(value)
    with pytest.raises(ValueError, match=f"fanqie_projection_scene_field_type:{field}"):
        build_fanqie_segment_outline_batch(project(), sheet)
    assert raw == before


@pytest.mark.parametrize("contract", CONTRACTS)
def test_none_and_missing_states_are_preserved_without_new_defaults(contract):
    raw = payload()
    raw["beats"][0][contract] = {"entry_state": None, "other": {"exit_state": "leave alone"}}
    before = deepcopy(raw)
    sheet = FanqieShortBeatSheet.model_validate(raw)
    assert getattr(sheet.beats[0], contract) == before["beats"][0][contract]
    assert "exit_state" not in getattr(sheet.beats[0], contract)
    assert raw == before


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("participants", "Mara, Neri", "participants_array_required"),
        ("participants", {}, "participants_array_required"),
        ("participants", ["Mara", 1], "participants_array_required"),
        ("action_sequence", "Read → wait", "action_array_required"),
        ("action_sequence", {}, "action_array_required"),
        ("sensory_anchors", ["a sound"], "scene_field_type:sensory_anchors"),
        ("key_dialogue_beats", {}, "scene_field_type:key_dialogue_beats"),
        ("forbidden_actions", "none", "scene_field_type:forbidden_actions"),
        ("relationship_debts", [1], "scene_field_type:relationship_debts"),
        ("scene_type", {}, "text_required:scene_type"),
    ],
)
def test_other_list_dict_and_participant_fields_are_not_coerced(field, value, error):
    raw = payload()
    raw["beats"][0]["opening_contract"] = {"entry_state": "exact", field: value}
    before = deepcopy(raw)
    sheet = FanqieShortBeatSheet.model_validate(raw)
    assert sheet.beats[0].opening_contract[field] == value
    with pytest.raises(ValueError, match="fanqie_projection_" + error):
        build_fanqie_segment_outline_batch(project(), sheet)
    assert raw == before


@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("value", [[], [["entry_state", "text"]], "text", None])
def test_invalid_contract_types_still_fail_model_validation(contract, value):
    raw = payload()
    raw["beats"][0][contract] = value
    before = deepcopy(raw)
    with pytest.raises(ValidationError) as raised:
        FanqieShortBeatSheet.model_validate(raw)
    assert any(error["type"] == "dict_type" for error in raised.value.errors())
    assert raw == before


def test_real_parser_keeps_provider_tree_and_consumer_hash_separate():
    raw = payload()
    raw["_meta"] = {"provider": {"unknown": [None, " §0 $ref "]}}
    raw["unknown_root"] = None
    raw["beats"][0]["unknown_beat"] = {"literal": None}
    raw["beats"][0]["opening_contract"] = {
        "entry_state": "  完整入场状态\n\t",
        "exit_state": "\n完整离场状态  ",
        "opaque": {"unknown": [None, {"entry_state": "not a scene state"}]},
    }
    before = deepcopy(raw)
    content = json.dumps(raw, ensure_ascii=False)
    provider, sheet = fanqie_beat_recovery._parse_new(content, 1)
    consumer = sheet.model_dump(mode="json")
    consumer_sha = fanqie_beat_recovery._sha(consumer)
    batch, scene, canonical = project_scene(sheet)
    assert provider == before
    assert fanqie_beat_recovery._sha(provider) == fanqie_beat_recovery._sha(before)
    assert fanqie_beat_recovery._sha(provider) != consumer_sha
    assert batch["_meta"]["segment_projection"]["typed_beat_sheet_sha256"] == consumer_sha
    assert content_hash(consumer) == consumer_sha
    for field in ("entry_state", "exit_state"):
        expected = {"summary": before["beats"][0]["opening_contract"][field]}
        assert consumer["beats"][0]["opening_contract"][field] == expected
        assert scene[field] == expected
        assert getattr(canonical, field) == expected
    provider_again, sheet_again = fanqie_beat_recovery._parse_new(content, 1)
    assert provider_again == before
    assert fanqie_beat_recovery._sha(sheet_again.model_dump(mode="json")) == consumer_sha
    sheet.beats[0].opening_contract["opaque"]["unknown"][1]["entry_state"] = "changed consumer"
    assert provider == before
    assert raw == before


# SHA256 of full schemas frozen before editing domain source 37ba0640cd4d1da2...
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (FanqieShortBeat, "048c3e832cc84b96aad439f511d4a1ae989ce9a3a8f693f348a5ad9316b21490"),
        (FanqieShortBeatSheet, "51444c68e35e83fb01eef333a2e304fb4239f6b094e63b64082e15766b63de83"),
    ],
)
@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_full_json_schema_is_identical_to_preimage(model, expected, mode):
    schema = model.model_json_schema(mode=mode)
    encoded = (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    assert hashlib.sha256(encoded).hexdigest() == expected

"""Pure public compiler API: literal candidate data, no synthetic approval proof."""

from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.chapter_causality_gate import evaluate_chapter_causality_contract
from bestseller.services.creation_intent_contract import build_creation_intent_contract
from bestseller.services.fanqie_short_planner import build_fanqie_segment_outline_batch
from bestseller.services.genre_intent_contract import contract_from_selection

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Projection tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    from bestseller.services import llm

    monkeypatch.setattr(llm, "complete_text", forbidden)


@pytest.fixture
def compiler():
    return build_fanqie_segment_outline_batch


def project(genre="现实生活", metadata=None):
    return SimpleNamespace(
        id=uuid4(),
        title="Unrelated source",
        genre=genre,
        language="en",
        target_chapters=4,
        target_word_count=8000,
        metadata_json=metadata or {},
    )


def beats():
    return FanqieShortBeatSheet(
        beats=[
            FanqieShortBeat(
                segment_number=n,
                beat_role="observational",
                purpose=f"Inspect dated specimen {n} without deciding its owner.",
                payoff=f"Measurement {n} remains provisional.",
                emotional_turn="A small uncertainty remains.",
            )
            for n in range(1, 5)
        ]
    )


def cast():
    return {
        "protagonist": {"name": "Mara"},
        "supporting_cast": [
            {"name": name} for name in ("Neri", "Otto", "Priya", "Quin", "Rui", "Sora")
        ],
    }


def author_metadata(**sources):
    contract = build_creation_intent_contract(
        contract_from_selection({"channel": "male", "genre": "xianxia"}),
        chapter_count=4,
        concept_seed="Maintain the shared archive.",
        creative_direction="Keep the unresolved question open.",
        pov="first_person",
        hook_spec={
            "ideology_contract": {
                "version": "ideology-author-contract.v1",
                "fields": {"core_question": "Who may keep a shared memory?"},
            }
        },
        field_sources=sources,
    )
    raw = contract.model_dump(mode="json")
    return {
        "creation_intent_contract": raw,
        "creation_intent_contract_hash": contract.contract_hash(),
        "creation_input_receipt": {
            "schema_version": contract.schema_version,
            "contract_hash": contract.contract_hash(),
            "field_sources": deepcopy(raw["field_sources"]),
        },
    }


@pytest.mark.parametrize("genre", ["现实生活", "科幻", "玄幻", "悬疑", "言情", "历史"])
def test_absent_story_fields_do_not_become_genre_template(compiler, genre):
    result = compiler(project(genre), beats(), cast_spec=cast())
    for chapter in result["chapters"]:
        scene = chapter["scenes"][0]
        assert scene["participants"] == []
        assert chapter["protagonist_flaw"] is None
        assert chapter["causal_contract"]["cost_or_tradeoff"] == ""
        assert chapter["causal_contract"]["state_change"] == ""
        assert chapter["causal_contract"]["fanqie_short_v2"]["ability_cost_contract"] == {}
        assert "emotion_contract" not in chapter["causal_contract"]["fanqie_short_v2"]
        assert scene["signature_image"] is None
        assert scene["time_label"] == ""
        assert scene["scene_type"] == ""
        assert scene["purpose"]["emotion"] == "A small uncertainty remains."


def test_never_calls_inference_or_legacy_story_renderers(compiler, monkeypatch):
    from bestseller.services import fanqie_short_planner as service

    def forbidden(*args, **kwargs):
        raise AssertionError("Automatic narrative invention was called")

    for name in (
        "select_emotion_stack",
        "_default_contracts",
        "_visible_goal_from_beat",
        "_hook_from_beat",
        "derive_short_thesis_vectors",
        "render_short_ideology_scene_block",
    ):
        monkeypatch.setattr(service, name, forbidden)
    compiler(project(), beats(), cast_spec=cast())


def test_structured_presence_is_not_roster_or_prose_mention(compiler):
    sheet = beats()
    sheet.beats[0].purpose = "Mara reads Neri's letter and phones Otto, who remains abroad."
    sheet.beats[0].continuity_contract = {"participants": ["Mara", "Priya"]}
    sheet.beats[1].opening_contract = {
        "participants": ["Sora", "Quin", "Rui", "Neri", "Otto", "Priya"]
    }
    result = compiler(project(), sheet, cast_spec=cast())
    assert [c["scenes"][0]["participants"] for c in result["chapters"]] == [
        ["Mara", "Priya"],
        ["Sora", "Quin", "Rui", "Neri", "Otto", "Priya"],
        [],
        [],
    ]


def test_explicit_noncanonical_participant_is_kept_for_existing_identity_gate(compiler):
    sheet = beats()
    sheet.beats[0].opening_contract = {"participants": ["Unregistered witness"]}
    result = compiler(project(), sheet, cast_spec=cast())
    assert result["chapters"][0]["scenes"][0]["participants"] == ["Unregistered witness"]


@pytest.mark.parametrize("kind", ["contradiction", "scalar"])
def test_conflicting_or_unstructured_presence_is_not_guessed(compiler, kind):
    sheet = beats()
    sheet.beats[0].opening_contract = {
        "participants": ["Mara"] if kind == "contradiction" else "Mara, Neri"
    }
    if kind == "contradiction":
        sheet.beats[0].continuity_contract = {"participants": ["Neri"]}
    with pytest.raises(ValueError, match="fanqie_projection_"):
        compiler(project(), sheet, cast_spec=cast())


def test_literal_scene_and_book_fields_are_not_shortened_or_spread_as_plot(compiler):
    sheet = beats()
    b = sheet.beats[0]
    stamp = "  Before sunrise; after the observatory closes. " * 4
    b.opening_contract = {
        "summary": "  A quiet repair is interrupted by rain.  ",
        "public_pressure": "The room must close before dawn.",
        "immediate_stakes": "The borrowed instrument must remain dry.",
        "signature_image": "The unmarked brass ring.",
        "action_sequence": ["Cover the lens.", "Measure the dry rim."],
    }
    b.continuity_contract = {"time_position": stamp, "fact_boundary": "Only the date is known."}
    b.closure_contract = {
        "irreversible_choice": "Mara stays.",
        "state_change": "The room is sealed.",
        "cut_point": "The final latch clicks.",
    }
    b.ability_cost_contract = {
        "source": "Ordinary inspection skills.",
        "cost": "A missed appointment.",
        "limits": "No diagnosis without measurements.",
        "unknown_leaf": None,
    }
    b.payoff_contract = {"visible_result": "The instrument remains dry."}
    book = {
        "series_engine": {"chapter_arc": "Propose → suspend → reconsider → leave open"},
        "premise": "Maintain the station.",
        "opaque": {"raw": None},
    }
    selected = cast()
    selected["protagonist"]["flaw"] = "Mara delays asking for help."
    before = deepcopy((sheet.model_dump(mode="json"), book, selected))
    result = compiler(project(), sheet, book_spec=book, cast_spec=selected)
    c = result["chapters"][0]
    s = c["scenes"][0]
    assert c["chapter_goal"] == b.purpose
    assert c["opening_situation"] == b.opening_contract["summary"]
    assert c["protagonist_flaw"] == selected["protagonist"]["flaw"]
    assert c["causal_contract"]["state_change"] == "The room is sealed."
    assert c["causal_contract"]["cost_or_tradeoff"] == "A missed appointment."
    assert s["time_label"] == stamp
    assert s["signature_image"] == "The unmarked brass ring."
    assert s["action_sequence"] == b.opening_contract["action_sequence"]
    assert s["cut_point"] == "The final latch clicks."
    assert all(
        c["chapter_goal"] == b.purpose for c, b in zip(result["chapters"], sheet.beats, strict=True)
    )
    for place in (c["causal_contract"], c["methodology_contract"], s["methodology_contract"]):
        assert place["fanqie_short_v2"]["ability_cost_contract"] == b.ability_cost_contract
    result["chapters"][0]["causal_contract"]["fanqie_short_v2"]["ability_cost_contract"]["cost"] = (
        "tampered output"
    )
    assert (sheet.model_dump(mode="json"), book, selected) == before
    assert (
        c["methodology_contract"]["fanqie_short_v2"]["ability_cost_contract"]["cost"]
        == "A missed appointment."
    )


@pytest.mark.parametrize("genre", ["现实生活", "科幻", "玄幻", "悬疑", "言情", "历史"])
@pytest.mark.parametrize("explicit", [False, True])
def test_only_selected_hash_bound_author_clauses_survive(compiler, explicit, genre):
    kind = "explicit" if explicit else "default"
    metadata = author_metadata(creative_direction=kind, hook_spec=kind, pov=kind)
    metadata["ideology_kernel"] = {"belief_arc": {"initial_belief": "The AI cache chose this."}}
    result = compiler(project(genre, metadata=metadata), beats())
    selected = result["chapters"][0]["methodology_contract"]["selected_creation_contracts"].get(
        "creation_intent_contract", {}
    )
    if explicit:
        assert selected["creative_direction"] == "Keep the unresolved question open."
        assert selected["pov"] == "first_person"
        assert selected["hook_spec"]["ideology_contract"]["fields"] == {
            "core_question": "Who may keep a shared memory?"
        }
    else:
        assert (
            "creative_direction" not in selected
            and "hook_spec" not in selected
            and "pov" not in selected
        )
    assert all(c["causal_contract"]["thesis_vector"] == "" for c in result["chapters"])
    assert "The AI cache chose this." not in json.dumps(result)


@pytest.mark.parametrize("fault", ["hash", "receipt", "boolean_hash"])
def test_bad_author_binding_fails_before_projection(compiler, fault):
    metadata = author_metadata(creative_direction="explicit")
    if fault == "hash":
        metadata["creation_intent_contract"]["creative_direction"] = "An unselected rewrite."
    elif fault == "receipt":
        metadata["creation_input_receipt"]["field_sources"]["pov"] = "explicit"
    else:
        metadata["creation_intent_contract_hash"] = True
    with pytest.raises(ValueError, match="fanqie_projection_creation_"):
        compiler(project(metadata=metadata), beats())


def test_missing_old_author_hash_is_unknown_not_new_authority(compiler):
    metadata = author_metadata(creative_direction="explicit")
    del metadata["creation_intent_contract_hash"]
    result = compiler(project(metadata=metadata), beats())
    assert result["chapters"][0]["methodology_contract"]["selected_creation_contracts"] == {}


@pytest.mark.parametrize("fault", ["missing", "duplicate", "reorder", "extra"])
def test_invalid_beat_coverage_does_not_invent_segment(compiler, fault):
    sheet = beats()
    if fault == "missing":
        sheet.beats.pop()
    elif fault == "duplicate":
        sheet.beats[2].segment_number = 2
    elif fault == "reorder":
        sheet.beats.reverse()
    else:
        sheet.beats.append(sheet.beats[-1].model_copy(update={"segment_number": 5}))
    with pytest.raises(ValueError, match="complete_ordered_beats_required"):
        compiler(project(), sheet)


def test_unknown_prototype_remains_rejected_by_real_causality_gate(compiler):
    sheet = beats()
    for b in sheet.beats:
        b.purpose = "观察。"
        b.payoff = ""
        b.emotional_turn = ""
    result = compiler(project(), sheet)
    report = evaluate_chapter_causality_contract(ChapterOutlineBatchInput.model_validate(result))
    assert not report.passed
    assert report.blocking_findings
    assert result["_meta"]["segment_projection"]["grade_asserted"] is False
    assert result["_meta"]["segment_projection"]["paid_source_proof_asserted"] is False




def test_no_cross_project_state_and_no_mutation(compiler):
    a = author_metadata(creative_direction="explicit")
    p = project(metadata=a)
    before = deepcopy(a)
    first = compiler(p, beats(), cast_spec={"protagonist": {"name": "Mara", "flaw": "Distracted."}})
    second = compiler(project("科幻"), beats(), cast_spec={"protagonist": {"name": "Ivo"}})
    assert first["chapters"][0]["protagonist_flaw"] == "Distracted."
    assert second["chapters"][0]["protagonist_flaw"] is None
    assert second["chapters"][0]["methodology_contract"]["selected_creation_contracts"] == {}
    assert p.metadata_json == before


def test_unknown_contract_keys_none_and_historical_raw_are_kept(compiler):
    sheet = beats()
    historical = {
        "raw": {"status": "rejected", "time": "old timestamp", "unknown": None},
        "unrecognized_fact": {"nested": [None, False, 0]},
        "summary": None,
    }
    sheet.beats[0].continuity_contract = deepcopy(historical)
    before = sheet.model_dump(mode="json")
    result = compiler(project(), sheet)
    for contract in (
        result["chapters"][0]["causal_contract"],
        result["chapters"][0]["methodology_contract"],
        result["chapters"][0]["scenes"][0]["methodology_contract"],
    ):
        assert contract["fanqie_short_v2"]["continuity_contract"] == historical
    assert sheet.model_dump(mode="json") == before


def test_structured_execution_controls_are_not_replaced_with_inferred_states(compiler):
    sheet = beats()
    controls = {
        "entry_state": {"door": "open", "unknown": None},
        "exit_state": {"door": "unchanged"},
        "sensory_anchors": {"sound": "An irregular ticking."},
        "key_dialogue_beats": ["Leave it open."],
        "forbidden_actions": ["Invent a culprit."],
        "relationship_debts": ["An unanswered invitation."],
    }
    sheet.beats[0].continuity_contract = deepcopy(controls)
    result = compiler(project(), sheet)
    scene = result["chapters"][0]["scenes"][0]
    for key, value in controls.items():
        assert scene[key] == value
    assert sheet.beats[0].continuity_contract == controls

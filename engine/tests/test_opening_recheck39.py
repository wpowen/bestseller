"""F39.0: accept proven opening transfers without whole-chapter extraction."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookengine.q1_continuity import boundary_facts, opening_patch, repair_opening


FIXTURES = Path(__file__).parent / "fixtures" / "opening_recheck39"


def run_repair(text, raw, state, patch, response=None, *, bible=None, number=2, failed=False):
    calls, extractions = [], []
    client = SimpleNamespace(prefix="cached-whole-chapter")

    def ask(client, name, prompt, sections, schema, **kwargs):
        calls.append(dict(name=name, prompt=prompt, sections=sections, schema=schema, options=kwargs))
        if name.endswith("-opening-repair"):
            return patch
        assert client.prefix == ""
        return deepcopy(response if response is not None else {})

    def extract(*args, **kwargs):
        extractions.append(args[1])
        return deepcopy(response if response is not None else {}), False, 0

    before = deepcopy((raw, state, patch, response))
    result = repair_opening(client, "ch2", text, raw, failed, state, bible or {}, dict(number=number), extract, ask)
    assert (raw, state, patch, response) == before
    assert client.prefix == "cached-whole-chapter"
    return result, calls, extractions


def example(replacement="林舟离开石桥，走进驿站。"):
    text = "林舟站在驿站门外。\n\n车夫把三只破木箱搬到了驿站的墙边。\n\n林舟留在驿站。"
    raw = dict(
        opening_location="驿站",
        opening_quote=text.split("\n\n")[0],
        end_location="驿站",
        end_quote=text.split("\n\n")[-1],
        events=[],
        summary="原有摘要",
        solutions=[dict(type="智取")],
    )
    return text, raw, dict(end_location="石桥"), dict(replace_count=1, paragraphs=[replacement])


@pytest.mark.parametrize("number", [3, 4])
def test_saved_trial38_repairs_are_adopted_despite_bad_boundary_returns(number):
    data = json.loads((FIXTURES / f"trial38-ch{number}.json").read_text())
    (actual, checked, failed, observed, note), calls, extractions = run_repair(
        data["original"], data["raw"], data["state"], data["patch"], data["bad_recheck"], number=number
    )
    assert actual == opening_patch(data["original"], data["patch"])
    assert note["status"] == "repaired" and not failed
    assert note["recheck"]["evidence"] == "Inference"
    assert observed["opening_location"] is None  # no invented quote or planning fact
    assert observed["end_location"] == data["raw"]["end_location"]
    assert checked["end_quote"] in actual
    assert not extractions
    assert calls[-1]["name"] == "ch2-opening-boundary-recheck"


def test_dedicated_call_only_sees_boundaries_and_never_inherits_cached_prefix():
    text, raw, state, patch = example()
    replacement = patch["paragraphs"][0]
    response = dict(opening_location="驿站", opening_quote=replacement)
    (actual, checked, failed, observed, note), calls, extractions = run_repair(text, raw, state, patch, response)
    assert note["status"] == "repaired" and not failed and actual != text
    assert not extractions
    boundary = calls[-1]
    assert boundary["name"] == "ch2-opening-boundary-recheck"
    assert [name for name, _ in boundary["sections"]] == ["开头", "结尾"]
    assert boundary["sections"] == [("开头", actual[:300]), ("结尾", actual[-1200:])]
    assert boundary["options"]["cap"] <= 1200 and boundary["options"]["input_limit"] <= 4000
    assert "state" not in json.loads(boundary["schema"])
    assert observed["opening_location"] == "驿站"
    assert checked["summary"] == raw["summary"] and checked["solutions"] == raw["solutions"]
    assert checked["end_quote"] == raw["end_quote"]


@pytest.mark.parametrize("response", [{}, None, [], "bad", {"opening_location": "驿站", "opening_quote": "伪造引文"}])
def test_missing_or_malformed_fields_keep_deterministic_proof_as_inference(response):
    text, raw, state, patch = example()
    (actual, checked, failed, facts, note), _, extractions = run_repair(text, raw, state, patch, response)
    assert actual != text and note["status"] == "repaired" and not failed
    assert note["recheck"]["evidence"] == "Inference" and not extractions
    assert facts["opening_location"] is None
    assert facts["end_location"] == "驿站" and checked["end_quote"] == raw["end_quote"]
    assert boundary_facts(checked, actual)["opening_location"] is None


@pytest.mark.parametrize(
    "replacement",
    [
        "林舟在驿站端起茶碗。",
        "林舟没有离开石桥去驿站。",
        "林舟想起从石桥走进驿站的旧事。",
        "“林舟离开石桥，走进驿站。”",
        "林舟站在驿站。门外的伙计追着鸡跑。",
        "风" * 301 + "林舟离开石桥，走进驿站。",
    ],
)
def test_no_transition_rolls_back_before_any_boundary_call(replacement):
    text, raw, state, patch = example(replacement)
    (actual, checked, failed, _, note), calls, extractions = run_repair(text, raw, state, patch)
    assert actual == text and checked == raw and not failed
    assert note["status"] == "rolled_back" and note["rollback_reason"] == "opening_still_disconnected"
    assert [c["name"] for c in calls] == ["ch2-opening-repair"] and not extractions


@pytest.mark.parametrize("replacement", ["次日，林舟抵达驿站。", "另一边，林舟在驿站。", "林舟离开旧渡，抵达驿站。"])
def test_shared_time_scene_and_registered_alias_rules(replacement):
    text, raw, state, patch = example(replacement)
    state["end_location"] = "白沙渡"
    result, _, extractions = run_repair(
        text, raw, state, patch, bible=dict(locations=[dict(name="白沙渡", aliases=["旧渡"])])
    )
    assert result[0] != text and result[-1]["status"] == "repaired" and not extractions


def test_repetition_is_not_allowed_even_when_deterministic_transition_passes():
    text, raw, state, patch = example("林舟离开石桥，走进驿站。车夫把三只破木箱搬到了驿站的墙边。")
    result, _, _ = run_repair(text, raw, state, patch)
    assert result[0] == text and result[-1]["rollback_reason"] == "introduced_repetition"


def test_short_chapter_cannot_lose_previously_grounded_end():
    text = "林舟站在驿站门外。"
    raw = dict(opening_location="驿站", opening_quote=text, end_location="驿站", end_quote=text)
    patch = dict(replace_count=1, paragraphs=["林舟离开石桥，走进驿站。"])
    result, _, _ = run_repair(text, raw, dict(end_location="石桥"), patch)
    assert result[0] == text and result[-1]["rollback_reason"] == "lost_grounded_boundary"


def test_new_grounded_boundary_cannot_override_a_known_disconnection():
    text, raw, state, patch = example("林舟离开石桥，走进驿站。林舟站在南城。")
    response = dict(opening_location="南城", opening_quote="林舟站在南城。")
    result, _, _ = run_repair(text, raw, state, patch, response)
    assert result[0] == text and result[-1]["rollback_reason"] == "opening_still_disconnected"


def test_existing_state_failure_is_not_cleared_by_boundary_only_call():
    text, raw, state, patch = example()
    result, _, extractions = run_repair(text, raw, state, patch, failed=True)
    assert result[0] != text and result[2] is True and not extractions


@pytest.mark.parametrize("kind", ["dead_character", "simultaneous_locations", "time_reversal"])
def test_hard_conflicts_in_replacement_still_roll_back_without_full_extraction(kind):
    additions = dict(
        dead_character="陶青站在驿站。",
        simultaneous_locations="林舟同时在石桥等人，同时林舟在驿站喝茶。",
        time_reversal="第2天八点整，林舟在驿站喝茶。",
    )
    text, raw, state, patch = example("林舟离开石桥，走进驿站。" + additions[kind])
    state.update(characters={"林舟": dict(location="石桥"), "陶青": dict(dead=True)}, clock=dict(day=2, minute=600))
    result, _, extractions = run_repair(text, raw, state, patch)
    assert result[0] == text and result[-1]["rollback_reason"] == "hard_fact_conflict" and not extractions


def test_invalid_patch_cannot_modify_suffix_or_trigger_boundary_call():
    text, raw, state, patch = example()
    patch.update(replace_count=3, paragraphs=["改一", "改二", "改三"])
    result, calls, extractions = run_repair(text, raw, state, patch)
    assert result[0] == text and result[-1]["rollback_reason"] == "invalid_or_unchanged_patch"
    assert len(calls) == 1 and not extractions


def test_two_paragraph_patch_preserves_suffix_and_unchanged_event_records():
    text, raw, state, _ = example()
    raw["events"] = [
        dict(kind="character", name="林舟", location="驿站", action="stay", paragraph=3, quote="林舟留在驿站。")
    ]
    patch = dict(replace_count=2, paragraphs=["林舟离开石桥，走进驿站。", "车夫递给他一碗水。"])
    (actual, checked, _, facts, note), _, _ = run_repair(text, raw, state, patch)
    assert note["status"] == "repaired" and actual.endswith("\n\n林舟留在驿站。")
    assert checked["events"] == raw["events"] and facts["end_quote"] == raw["end_quote"]


def test_grounded_end_in_unchanged_suffix_wins_over_new_extractor_disagreement():
    text, raw, state, patch = example()
    raw.update(end_location="驿站", end_quote="林舟留在驿站。")
    text += "\n\n门外还能看见南城。"
    response = dict(
        opening_location="驿站",
        opening_quote=patch["paragraphs"][0],
        end_location="南城",
        end_quote="门外还能看见南城。",
    )
    (actual, checked, _, facts, note), _, _ = run_repair(text, raw, state, patch, response)
    assert note["status"] == "repaired" and facts["end_location"] == "驿站"
    assert checked["end_quote"] == raw["end_quote"]
    assert note["recheck"]["evidence"] == "Inference" and note["recheck"]["discarded_end_location"] == "南城"


def test_already_grounded_hard_event_still_blocks_repair():
    text, raw, state, patch = example()
    raw["events"] = [
        dict(kind="character", name="林舟", location="驿站", action="stay", paragraph=3, quote="林舟留在驿站。")
    ]
    state["characters"] = {"林舟": dict(dead=True)}
    result, _, _ = run_repair(text, raw, state, patch)
    assert result[0] == text and result[-1]["rollback_reason"] == "hard_fact_conflict"


@pytest.mark.parametrize("addition", ["陶青打算站在驿站。", "陶青没有站在驿站。", "第3天八点整，林舟在驿站喝茶。"])
def test_plans_negation_and_forward_time_do_not_become_hard_conflicts(addition):
    text, raw, state, patch = example("林舟离开石桥，走进驿站。" + addition)
    state.update(characters={"陶青": dict(dead=True)}, clock=dict(day=2, minute=600))
    result, _, _ = run_repair(text, raw, state, patch)
    assert result[0] != text and result[-1]["status"] == "repaired"


def test_synthetic_hard_checks_never_invent_saved_events():
    text, raw, state, patch = example("林舟离开石桥，走进驿站。林舟站在驿站。")
    state["characters"] = {"林舟": dict(location="石桥")}
    result, _, _ = run_repair(text, raw, state, patch)
    assert result[-1]["status"] == "repaired" and result[1]["events"] == []


def test_viewpoint_history_reference_survives_missing_boundary_viewpoint_fields():
    text, raw, state, patch = example()
    raw.update(opening_pov="林舟", opening_pov_quote=text.split("\n\n")[0])
    state.update(end_location="灶间", end_pov="陶青", pov_locations={"林舟": dict(location="石桥")})
    result, _, _ = run_repair(text, raw, state, patch)
    assert result[-1]["status"] == "repaired"
    assert result[-1]["reference"]["end_location"] == "石桥"
    assert result[-1]["recheck"]["evidence"] == "Inference"

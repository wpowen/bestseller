"""Source-authority/merge regressions only; no model or production database."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services import planner

pytestmark = pytest.mark.unit


def baseline():
    return {
        "protagonist": {
            "name": "Mira", "gender": "unknown",
            "fear": "compiler emotional promise", "secret": "compiler dramatic question?",
            "beliefs": {"philosophical_stance": "compiler theme"},
            "goal": "compiler promise", "decision_policy": {"preferred_tactics": "already occurred only"},
            "metadata": {"source_bound_design": True, "identity_source_text": "Mira works at the quay."},
        },
        "antagonist": None, "supporting_cast": [], "conflict_map": [],
        "_meta": {"source_compiler": "approved-design-cast.v1", "source_hash": "historical"},
    }


def proposal():
    return {"protagonist": {
        "name": "Mira", "gender": "female", "fear": "Losing her hearing",
        "secret": "She helped build the previous prototype",
        "beliefs": {"philosophical_stance": "Every debt must be acknowledged"},
        "goal": "Finish the trial", "life_history": {"career_history": ["Former instrument maker"]},
        "mechanisms": ["Acoustic measurement", "Mechanical amplification"],
    }, "supporting_cast": [{"name": "Ivo", "goal": "Secure a test berth"}]}


def test_unknown_fear_secret_belief_do_not_inherit_compiler_aliases():
    source, generated = baseline(), proposal()
    original_source, original_generated = deepcopy(source), deepcopy(generated)
    result = planner._rebind_source_bound_cast_expansion(
        source, generated, canonical_protagonist={"name": "Mira"})
    hero = result["protagonist"]
    for field in ("fear", "secret", "beliefs", "goal", "life_history", "mechanisms"):
        assert hero[field] == generated["protagonist"][field]
    assert "decision_policy" not in hero
    assert hero["gender"] == "female" and hero["pronoun_set_en"] == "she/her"
    assert source == original_source and generated == original_generated
    assert result["_meta"]["source_bound_expansion"] == "derived_pending_review"
    assert result["_meta"]["source_bound_canonical_rebind"]["proposals_are_canonical"] is False


def test_actual_canonical_same_fields_and_nested_leaves_are_retained():
    canon = {"name": "Mira", "gender": "female", "fear": "Deep water",
        "secret": "A witnessed past event", "beliefs": {"religious_stance": "Agnostic"},
        "abilities": ["A verified skill"], "can_swim": False, "remaining_tokens": 0}
    generated = proposal()
    generated["protagonist"].update({"gender": "male", "abilities": ["Replacement"],
        "can_swim": True, "remaining_tokens": 10})
    generated["protagonist"]["beliefs"]["religious_stance"] = "Contradiction"
    result = planner._rebind_source_bound_cast_expansion(baseline(), generated, canonical_protagonist=canon)
    hero = result["protagonist"]
    for field in ("name", "gender", "fear", "secret", "abilities", "can_swim", "remaining_tokens"):
        assert hero[field] == canon[field]
    assert hero["beliefs"] == {"religious_stance": "Agnostic",
        "philosophical_stance": "Every debt must be acknowledged"}
    assert hero["pronoun_set_zh"] == "她"


def test_no_cross_field_promotion_of_question_promise_theme_or_hook():
    canon = {"name": "Mira", "dramatic_question": "Will she escape?",
        "emotional_promise": "Relief", "unique_hook": "Two instruments", "theme_statement": "Trust"}
    result = planner._rebind_source_bound_cast_expansion(baseline(), proposal(), canonical_protagonist=canon)
    hero = result["protagonist"]
    assert hero["secret"] == proposal()["protagonist"]["secret"]
    assert hero["fear"] == proposal()["protagonist"]["fear"]
    assert hero["beliefs"] == proposal()["protagonist"]["beliefs"]
    assert all(hero[key] == value for key, value in canon.items())


@pytest.mark.parametrize("canon", [None, {}, [], {"name": "Other"}, {"name": "Mira", "fear": ("x",)},
    {"name": "Mira", "fear": float("nan")}, {"name": "Mira", "fear": float("inf")},
    {"name": "Mira", "metadata": {"source": "user"}}, {"name": "Mira", "gender": "unsupported"}])
def test_missing_malformed_or_misbound_canonical_projection_fails_closed(canon):
    with pytest.raises(ValueError, match="source_bound_"):
        planner._rebind_source_bound_cast_expansion(baseline(), proposal(), canonical_protagonist=canon)


@pytest.mark.parametrize("source", [{}, {"protagonist": []}, {"protagonist": {"fear": "unknown"}}])
def test_missing_real_canonical_name_cannot_be_filled_by_model(source):
    with pytest.raises(ValueError, match="canonical_protagonist_required"):
        planner._rebind_source_bound_cast_expansion(source, proposal(), canonical_protagonist={"name": "Mira"})


def test_wrong_person_proposals_cannot_be_attached_to_canonical_protagonist():
    generated = proposal()
    generated["protagonist"]["name"] = "Another person"
    with pytest.raises(ValueError, match="generated_protagonist_binding_invalid"):
        planner._rebind_source_bound_cast_expansion(baseline(), generated, canonical_protagonist={"name": "Mira"})


def test_unknown_canonical_gender_is_open_but_canonical_pronouns_are_not_overwritten():
    result = planner._rebind_source_bound_cast_expansion(baseline(), proposal(),
        canonical_protagonist={"name": "Mira", "gender": "unknown"})
    assert result["protagonist"]["gender"] == "female"
    with pytest.raises(ValueError, match="canonical_pronouns_conflict"):
        planner._rebind_source_bound_cast_expansion(baseline(), proposal(),
            canonical_protagonist={"name": "Mira", "pronoun_set_zh": "他", "pronoun_set_en": "he/him"})


def test_model_metadata_does_not_manufacture_source_authority():
    generated = proposal()
    generated["protagonist"]["metadata"] = {"source": "user", "canonical": True}
    result = planner._rebind_source_bound_cast_expansion(baseline(), generated, canonical_protagonist={"name": "Mira"})
    assert result["protagonist"]["metadata"] == baseline()["protagonist"]["metadata"]
    assert not result["_meta"]["source_bound_canonical_rebind"]["proposals_are_canonical"]


def test_empty_generation_does_not_claim_generated_assets_from_removing_defaults():
    result = planner._rebind_source_bound_cast_expansion(baseline(), {}, canonical_protagonist={"name": "Mira"})
    assert result["_meta"]["source_bound_expansion"] == "unavailable"
    assert result["_meta"]["source_bound_expansion_fields"] == []
    assert "fear" not in result["protagonist"] and "secret" not in result["protagonist"]


def test_real_cast_schema_retains_new_history_and_personal_fields_as_planning():
    result = planner._rebind_source_bound_cast_expansion(baseline(), proposal(), canonical_protagonist={"name": "Mira"})
    parsed = planner.parse_cast_spec_input(result).model_dump(mode="json")
    assert parsed["protagonist"]["fear"] == "Losing her hearing"
    assert parsed["protagonist"]["secret"] == "She helped build the previous prototype"
    assert parsed["protagonist"]["life_history"]["career_history"] == ["Former instrument maker"]


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("asset", ["cast", "world"])
@pytest.mark.parametrize("genre", ["fantasy", "romance", "historical"])
def test_expansion_contract_has_source_authority_without_closed_world(language, asset, genre):
    project = SimpleNamespace(language=language, genre=genre)
    text = planner._source_bound_expansion_contract(project, asset=asset,
        book_spec={"protagonist": {"name": "Mira"}, "unique_hook": "Two complementary mechanisms"}, world_spec={})
    assert "Mira" in text and "Two complementary mechanisms" in text
    if language == "en":
        assert "need not have already occurred in prose" in text
        assert "actual" in text and "adopted past" in text and "proposal" in text
        assert "Do not add a new protagonist, second ability" not in text
        assert "Do not introduce a second power source" not in text
    else:
        assert "不要求已经在正文发生" in text
        assert "真实" in text and "正式采用" in text and "提案" in text
        assert "不得新增主角、第二能力来源" not in text
        assert "不得引入第二能力来源" not in text

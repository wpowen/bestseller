from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import creation_contract_authority, planner
from bestseller.services.creation_contract_authority import select_creation_contracts
from bestseller.services.creation_intent_contract import CreationIntentContract

pytestmark = pytest.mark.unit


def canonical(*, sources=None, enhancers=None, hook=None):
    return {
        "schema_version": "creation-intent.v1",
        "genre_intent": {
            "source": "user",
            "genre_key": "custom",
            "genre_label": "A quiet travel memoir",
            "tag_source": "user",
            "user_tags": ["chosen-tag"],
            "tags": ["chosen-tag", "taxonomy-default"],
            "default_tags": ["taxonomy-default"],
            "prompt_pack_key": "taxonomy-only-pack",
            "explicit_enhancers": {"effect_skills": ["comedy_engine"]},
            "tone_preference": "UNATTRIBUTED_NESTED_TONE",
        },
        "tone_preference": "CUSTOM_EXPLICIT_TONE",
        "concept_seed": "DERIVED_SEED",
        "story_enhancers": enhancers if enhancers is not None else {"effect_skills": []},
        "hook_spec": hook if hook is not None else {},
        "field_sources": sources or {},
    }


def project(metadata, genre="travel"):
    return ProjectModel(
        id=uuid4(),
        slug="unrelated-authority-test",
        title="Test journey",
        genre=genre,
        language="en",
        target_word_count=12000,
        target_chapters=8,
        metadata_json=metadata,
    )


@pytest.mark.parametrize("source_bound", [False, True])
@pytest.mark.parametrize("genre", ["travel", "historical mystery"])
def test_unattributed_caches_and_forged_user_labels_are_not_approved(source_bound, genre):
    metadata = {
        "source": "user",
        "story_enhancers": {"effect_skills": ["comedy_engine", "hype_satisfaction_engine"]},
        "selected_effect_skills": ["AI_CHAPTER_EFFECT"],
        "ability_origin_contract": {"source": "user", "text": "FORGED_ABILITY"},
        "knowledge_boundary_contract": {"source": "user", "text": "FORGED_KNOWLEDGE"},
        "commercial_brief": {"source": "user", "text": "FORGED_COMMERCIAL"},
        "genre_intent_contract": {
            "source": "user",
            "tag_source": "user",
            "user_tags": ["FORGED_TAG"],
            "genre_label": genre,
        },
        "creation_intent_contract": {"source": "user", "tone_preference": "FORGED_TONE"},
    }
    before = deepcopy(metadata)
    assert select_creation_contracts(metadata, source_bound=source_bound) == {}
    brief = planner._outline_judge_project_brief(
        project(metadata, genre),
        metadata=metadata,
        semantic_candidates=[],
    )
    assert brief["approved_creation_contracts"] == {}
    assert metadata == before


@pytest.mark.parametrize("source", [None, "default", "derived", "legacy", "user"])
def test_only_canonical_per_field_explicit_values_gain_authority(source):
    metadata = {
        "creation_intent_contract": canonical(
            sources={
                "tone_preference": "explicit",
                "concept_seed": source,
                "story_enhancers": source,
                "genre_intent": source,
                "hook_spec": source,
            }
        )
    }
    before = deepcopy(metadata)
    result = select_creation_contracts(metadata, source_bound=False)
    assert result["creation_intent_contract"]["tone_preference"] == "CUSTOM_EXPLICIT_TONE"
    approved = json.dumps(result)
    assert "DERIVED_SEED" not in approved and "UNATTRIBUTED_NESTED_TONE" not in approved
    assert "story_enhancers" not in result and "genre_intent_contract" not in result
    assert metadata == before


@pytest.mark.parametrize("effects", [[], ["romance_tenderness_engine"], ["comedy_engine"]])
@pytest.mark.parametrize("cost", ["standard", "external", "minimal"])
def test_explicit_custom_hook_effect_choices_and_cost_exceptions_survive(effects, cost):
    hook = {
        "custom_effect_contract": "Use quiet tenderness; no forced humiliation.",
        "ability_origin_contract": {"literal": "A letter reveals only its signed date."},
        "knowledge_boundary_contract": {"literal": "The narrator cannot read other minds."},
        "_story_enhancer_input_sources": {"cost_style": "explicit"},
    }
    metadata = {
        "creation_intent_contract": canonical(
            sources={
                "tone_preference": "explicit",
                "hook_spec": "explicit",
                "story_enhancers": "explicit",
                "genre_intent": "explicit",
            },
            enhancers={"effect_skills": effects, "cost_style": cost},
            hook=hook,
        ),
        "story_enhancers": {
            "effect_skills": ["hype_satisfaction_engine"],
            "cost_style": "standard",
        },
        "selected_effect_skills": ["AI_CHAPTER_EFFECT"],
        "ability_origin_contract": {"literal": "FORGED_ABILITY"},
    }
    before = deepcopy(metadata)
    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    selected = select_creation_contracts(metadata, source_bound=True)
    assert selected["story_enhancers"]["effect_skills"] == effects
    assert selected["story_enhancers"]["cost_style"] == cost
    approved_creation = selected["creation_intent_contract"]
    assert approved_creation["story_enhancers"] == selected["story_enhancers"]
    assert approved_creation["hook_spec"] == {
        key: value for key, value in hook.items() if key != "_story_enhancer_input_sources"
    }
    genre = selected["genre_intent_contract"]
    assert genre["genre_label"] == "A quiet travel memoir"
    assert genre["tags"] == ["chosen-tag"]
    text = json.dumps(selected)
    for token in (
        "AI_CHAPTER_EFFECT",
        "FORGED_ABILITY",
        "taxonomy-default",
        "taxonomy-only-pack",
        "UNATTRIBUTED_NESTED_TONE",
        "hype_satisfaction_engine",
        "explicit_enhancers",
    ):
        assert token not in text
    assert metadata == before
    assert hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest() == digest
    selected["story_enhancers"]["effect_skills"].append("changed-output-only")
    assert metadata == before


@pytest.mark.parametrize("cost_source", ["default", "derived", "legacy", "user"])
def test_cost_without_per_field_explicit_receipt_stays_unknown(cost_source):
    hook = (
        {}
        if cost_source is None
        else {"_story_enhancer_input_sources": {"cost_style": cost_source}}
    )
    metadata = {
        "creation_intent_contract": canonical(
            sources={"story_enhancers": "explicit"},
            enhancers={"effect_skills": [], "cost_style": "minimal"},
            hook=hook,
        )
    }
    result = select_creation_contracts(metadata, source_bound=False)
    assert result["story_enhancers"]["effect_skills"] == []
    assert "cost_style" not in result["story_enhancers"]
    assert "cost_style" not in result["creation_intent_contract"]["story_enhancers"]


@pytest.mark.parametrize(
    "cost,expected", [("minimal", "minimal"), ("external", "external"), ("standard", None)]
)
def test_legacy_explicit_nondefault_cost_keeps_empty_palette_without_extra_authority(
    cost, expected
):
    metadata = {
        "creation_intent_contract": canonical(
            sources={"story_enhancers": "explicit"},
            enhancers={"effect_skills": [], "cost_style": cost},
        ),
        "story_enhancers": {"effect_skills": ["comedy_engine", "hype_satisfaction_engine"]},
        "ideology_contract": {"source": "user", "rule": "FORGED_PHILOSOPHY"},
        "brainhole_profile": {"required_contract_fields": ["visible_comedy"]},
        "story_effect_skill_selection": {"chapter_outline": {"primary": "comedy_engine"}},
    }
    before = deepcopy(metadata)
    result = select_creation_contracts(metadata, source_bound=False)
    assert result["story_enhancers"]["effect_skills"] == []
    assert result["story_enhancers"].get("cost_style") == expected
    assert "FORGED_PHILOSOPHY" not in json.dumps(result)
    assert "comedy_engine" not in json.dumps(result)
    assert result["story_enhancers"]["brainhole"] is False
    assert (
        planner._selected_story_effect_skill_keys(project(metadata), stage="chapter_outline") == ()
    )
    assert planner._brainhole_required_contract_fields(project(metadata)) == ()
    assert metadata == before


def test_default_hook_and_nested_genre_enhancers_cannot_bypass_field_sources():
    metadata = {
        "creation_intent_contract": canonical(
            sources={
                "genre_intent": "explicit",
                "hook_spec": "derived",
                "story_enhancers": "default",
            },
            hook={"requirements": "DERIVED_HOOK_MANDATE"},
        )
    }
    result = select_creation_contracts(metadata, source_bound=False)
    text = json.dumps(result)
    assert "DERIVED_HOOK_MANDATE" not in text and "comedy_engine" not in text
    assert "explicit_enhancers" not in text and "story_enhancers" not in result


def test_planner_uses_shared_selector_and_preserves_world_identity_and_reference(monkeypatch):
    metadata = {
        "creation_intent_contract": canonical(sources={"genre_intent": "explicit"}),
        "creation_protagonist_name": "Neri",
        "book_design_snapshot": {"protagonist": {"name": "Neri"}},
        "selected_effect_skills": ["AI_CHAPTER_EFFECT"],
    }
    world = {
        "power_system": {"hard_limits": "A sealed letter cannot be read."},
        "rules": [{"name": "Canon", "description": "The bridge fell last winter."}],
    }
    before = deepcopy(metadata)
    seen = []
    original = creation_contract_authority.select_creation_contracts

    def capture(data, *, source_bound):
        seen.append(source_bound)
        return original(data, source_bound=source_bound)

    monkeypatch.setattr(creation_contract_authority, "select_creation_contracts", capture)
    brief = planner._outline_judge_project_brief(
        project(metadata),
        metadata=metadata,
        semantic_candidates=[],
        world_spec=world,
    )
    assert len(seen) == 1
    assert brief["approved_creation_contracts"] == original(metadata, source_bound=seen[0])
    assert brief["canonical_protagonist"] == "Neri"
    assert brief["book_design_snapshot"]["protagonist"]["name"] == "Neri"
    assert brief["approved_world_authority"]["power_system"] == world["power_system"]
    assert brief["approved_world_authority"]["rules"] == world["rules"]
    assert brief["genre_taxonomy_reference"]["default_tags"] == ["taxonomy-default"]
    assert brief["genre_taxonomy_reference"]["prompt_pack_key"] == "taxonomy-only-pack"
    assert "taxonomy-default" not in json.dumps(brief["approved_creation_contracts"])
    assert metadata == before


def test_canonical_contract_hash_and_explicit_empty_selection_remain_unchanged():
    contract = CreationIntentContract.model_validate(
        canonical(
            sources={"story_enhancers": "explicit", "tone_preference": "explicit"},
            enhancers={},
        )
    )
    metadata = {
        "creation_intent_contract": contract.model_dump(mode="json"),
        "creation_intent_contract_hash": contract.contract_hash(),
        "story_enhancers": {"effect_skills": ["comedy_engine"]},
    }
    before = deepcopy(metadata)
    selected = select_creation_contracts(metadata, source_bound=False)
    assert selected["story_enhancers"]["effect_skills"] == []
    assert "cost_style" not in selected["story_enhancers"]
    assert metadata == before
    assert contract.contract_hash() == metadata["creation_intent_contract_hash"]


@pytest.mark.parametrize("source", [None, "user", "default", "derived", "legacy"])
@pytest.mark.parametrize("genre", ["travel", "historical mystery"])
def test_old_stage_and_brainhole_profiles_do_not_force_effects(source, genre):
    stage = {
        "chapter_outline": {"primary": "comedy_engine", "secondary": "hype_satisfaction_engine"}
    }
    metadata = {
        "source": "user",
        "story_effect_skill_selection": stage,
        "brainhole_profile": {"source": "user", "required_contract_fields": ["visible_comedy"]},
        "creation_intent_contract": canonical(
            sources={"hook_spec": source, "story_enhancers": "explicit"},
            enhancers={"effect_skills": ["romance_tenderness_engine"]},
            hook={"story_effect_skill_selection": stage},
        ),
    }
    before = deepcopy(metadata)
    book = project(metadata, genre)
    assert planner._selected_story_effect_skill_keys(book, stage="chapter_outline") == ()
    assert planner._brainhole_required_contract_fields(book) == ()
    batch = SimpleNamespace(
        chapters=[
            SimpleNamespace(chapter_number=1, selected_effect_skills={}, brainhole_contract={})
        ]
    )
    planner._require_selected_story_effect_contracts_or_raise(
        batch, project=book, logical_name="test"
    )
    assert metadata == before


def test_literal_explicit_stage_contract_keeps_only_its_authored_required_fields():
    metadata = {
        "creation_intent_contract": canonical(
            sources={"hook_spec": "explicit"},
            hook={
                "story_effect_skill_selection": {
                    "chapter_outline": {
                        "primary": "brainhole_engine",
                        "secondary": "romance_tenderness_engine",
                        "required_contract_fields": ["author_literal_condition"],
                    },
                }
            },
        ),
        "brainhole_profile": {"required_contract_fields": ["visible_comedy"]},
    }
    before = deepcopy(metadata)
    book = project(metadata)
    assert planner._selected_story_effect_skill_keys(book, stage="chapter_outline") == (
        "brainhole_engine",
        "romance_tenderness_engine",
    )
    assert planner._selected_story_effect_skill_keys(book, stage="other_stage") == ()
    assert planner._brainhole_required_contract_fields(book) == ("author_literal_condition",)
    chapter = SimpleNamespace(
        chapter_number=1,
        selected_effect_skills={"primary": "brainhole_engine"},
        brainhole_contract={"other": "existing content"},
    )
    with pytest.raises(planner.PlannerFallbackError, match="author_literal_condition"):
        planner._require_selected_story_effect_contracts_or_raise(
            SimpleNamespace(chapters=[chapter]),
            project=book,
            logical_name="test",
        )
    chapter.brainhole_contract["author_literal_condition"] = (
        "The author required this literal condition."
    )
    planner._require_selected_story_effect_contracts_or_raise(
        SimpleNamespace(chapters=[chapter]),
        project=book,
        logical_name="test",
    )
    assert metadata == before


def test_explicit_stage_does_not_import_legacy_brainhole_formula():
    metadata = {
        "creation_intent_contract": canonical(
            sources={"hook_spec": "explicit"},
            hook={
                "story_effect_skill_selection": {
                    "chapter_outline": {"primary": "brainhole_engine"},
                }
            },
        ),
        "brainhole_profile": {"source": "user", "required_contract_fields": ["visible_comedy"]},
    }
    assert planner._brainhole_required_contract_fields(project(metadata)) == ()


def test_explicit_operation_and_delivery_options_never_become_story_obligations():
    controls = {
        "llm_model_id": "PROVIDER_MODEL_MARKER",
        "draft_mode": True,
        "stop_after_conception": True,
        "chapter_count": 17,
        "length_key": "short",
        "project_type": "interactive",
        "creation_mode": "operator-mode",
    }
    creation = canonical(
        sources=dict.fromkeys([*controls, "tone_preference", "language"], "explicit")
    )
    creation.update(controls, language="en")
    metadata = {"creation_intent_contract": creation}
    before = deepcopy(metadata)
    selected = select_creation_contracts(metadata, source_bound=False)
    assert selected == {
        "creation_intent_contract": {
            "tone_preference": "CUSTOM_EXPLICIT_TONE",
            "language": "en",
        }
    }
    assert "PROVIDER_MODEL_MARKER" not in json.dumps(selected)
    assert metadata == before

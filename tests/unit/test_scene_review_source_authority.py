"""Scene review keeps source constraints without importing a universal recipe."""

import copy
import json
from types import SimpleNamespace

import pytest

from bestseller.domain.review import SceneReviewFinding, SceneReviewResult, SceneReviewScores
from bestseller.services import reviews

pytestmark = pytest.mark.unit


def _inputs(language="zh-CN", genre="custom"):
    project = SimpleNamespace(title="Independent source fixture", language=language,
        genre=genre, sub_genre=None, metadata_json={})
    chapter = SimpleNamespace(chapter_number=1)
    scene = SimpleNamespace(scene_number=2, scene_type="transition", title="A quiet departure",
        participants=["Mira"], purpose={"story": "Leave after the meeting", "emotion": "Relief"},
        entry_state={"door": "open"}, exit_state={"door": "closed"},
        forbidden_actions=["Do not reveal the sender"], hook_requirement=None)
    draft = SimpleNamespace(content_md="Mira closed the door. She left the letter unopened.")
    scores = SceneReviewScores(**{name: 0.2 for name, field in SceneReviewScores.model_fields.items()
        if field.is_required()})
    result = SceneReviewResult(verdict="rewrite", severity_max="high", scores=scores,
        findings=[SceneReviewFinding(category="methodology", severity="high",
            message="DIAGNOSTIC_ONLY: add a new telepathy ability and a countdown.")])
    return project, chapter, scene, draft, result


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("genre", ["仙侠", "literary", "romance"])
def test_source_priority_and_advisory_discipline_across_languages_and_genres(language, genre):
    project, chapter, scene, draft, result = _inputs(language, genre)
    system, user = reviews.build_scene_review_prompts(project, chapter, scene, draft, result)
    assert "SOURCE AUTHORITY" in system and "Canon/Timeline" in system
    assert "CURRENT_DRAFT:\n" + draft.content_md + "\nEND_CURRENT_DRAFT" in user
    assert "SCENE_CARD (planning intent; not proof that an event occurred)" in user
    assert "UNVERIFIED_DIAGNOSTICS" in user
    assert user.index("UNVERIFIED_DIAGNOSTICS") < user.index("DIAGNOSTIC_ONLY") < user.index("CURRENT_DRAFT:")
    if language.startswith("en"):
        assert "never because several advisory axes fail" in system
        assert "only explicitly approved, applicable requirements are binding" in system
        assert "do not execute new content proposed by a finding" in system
        assert "ongoing rule or personality reference alone is not a repeated event" in system
        assert "A mentioned name does not establish presence" in system
    else:
        assert "不按多轴失败数量返工" in system
        assert "只有明确批准且适用于本场的条款才是约束" in system
        assert "不得直接执行诊断建议的新内容" in system
        assert "持续条款或性格引用本身不等于事件重播" in system
        assert "提到名字不等于角色到场" in system
    for obsolete in ("前200字", "first 200", "≥2", "≥ 2", "七项逐条", "第 7 项 FAIL",
                     "拇指碾过随身物件的缺口", "空了的胃", "第1章特查", "characters don't state intentions"):
        assert obsolete not in system + user


def test_no_pack_yaml_genre_or_quality_lever_loader_is_called(monkeypatch):
    from bestseller.services import genre_review_profiles, methodology_compiler, prompt_assembly, quality_levers

    def forbidden(*args, **kwargs):
        pytest.fail("Scene review must not load inferred craft requirements")

    for name in ("_resolve_project_writing_profile", "_resolve_project_prompt_pack",
                 "render_methodology_block", "render_prompt_pack_prompt_block", "render_prompt_pack_fragment"):
        monkeypatch.setattr(reviews, name, forbidden)
    monkeypatch.setattr(methodology_compiler, "compile_methodology", forbidden)
    monkeypatch.setattr(quality_levers, "extract_quality_levers_meta", forbidden)
    monkeypatch.setattr(quality_levers, "build_critic_quality_levers_block", forbidden)
    for name in ("compile_methodology", "extract_quality_levers_meta", "build_critic_quality_levers_block"):
        assert not hasattr(reviews, name)
    monkeypatch.setattr(genre_review_profiles, "resolve_genre_review_profile", forbidden)
    monkeypatch.setattr(prompt_assembly, "genre_wants_reaction_amplification", forbidden)
    inputs = _inputs()
    inputs[0].metadata_json = {
        "prompt_pack_name": "DO_NOT_LOAD_PACK", "prompt_pack_key": "DO_NOT_LOAD_KEY",
        "writing_profile": {"market": {"prompt_pack_key": "DO_NOT_LOAD_PROFILE"}},
        "quality_levers": {"opening": "DO_NOT_LOAD_LEVERS"},
        "configured_methodology_profile_block": "DO_NOT_BROADCAST_YAML",
    }
    system, user = reviews.build_scene_review_prompts(*inputs)
    assert "DO_NOT_" not in system + user


def test_actual_scene_and_source_context_retained_without_mutating_inputs():
    inputs = _inputs()
    context = SimpleNamespace(
        previous_scene_summaries=[{"extended_tail": "The seal was broken once.", "source_id": "previous-body"}],
        recent_scene_summaries=[SimpleNamespace(summary="The meeting ended.")],
        participant_canon_facts=[SimpleNamespace(subject="Mira", fact="Cannot read sealed letters")],
        recent_timeline_events=[{"event": "The key was returned", "source_id": "accepted-draft"}],
        identity_registry=[{"canonical_name": "Mira", "aliases": ["The keeper"]}],
        identity_constraint_block="IDENTITY_LOCK_SOURCE", canon_guardrails_block="CANON_LOCK_SOURCE",
        participant_knowledge_states=[{"name": "Mira", "unknown": "sender"}],
        scene_contract={"information_release": "Keep sender hidden", "source_id": "approved-outline"},
        chapter_contract={"core_conflict": "An unopened letter", "source_id": "approved-outline"},
        hard_fact_snapshot={"facts": [{"name": "keys", "value": 0}]},
        configured_methodology_profile_block="EXCLUDED_TEMPLATE")
    before = copy.deepcopy((inputs[0].metadata_json, vars(inputs[2]), vars(context), inputs[4].model_dump()))
    system, user = reviews.build_scene_review_prompts(*inputs, scene_context=context)
    for expected in ("Mira", "The keeper", "IDENTITY_LOCK_SOURCE", "CANON_LOCK_SOURCE",
                     "Cannot read sealed letters", "The seal was broken once.", "previous-body",
                     "The meeting ended.", "The key was returned", "accepted-draft", "sender",
                     "Keep sender hidden", "approved-outline", "Do not reveal the sender", '"door": "closed"'):
        assert expected in user
    assert "EXCLUDED_TEMPLATE" not in system + user
    after = (inputs[0].metadata_json, vars(inputs[2]), vars(context), inputs[4].model_dump())
    assert before == after


def test_source_bound_creation_contracts_filter_taxonomy_and_keep_explicit_effects():
    inputs = _inputs()
    inputs[0].metadata_json = {
        "book_design_snapshot_status": "locked",
        "book_design_snapshot": {"snapshot_id": "locked-source", "source_hash": "source-hash",
                                 "protagonist": {"name": "Mira"}},
        "creation_intent_contract": {
            "creative_direction": "No new abilities",
            "story_enhancers": {"effect_skills": ["comedy_engine"]},
            "genre_intent": {"genre_key": "literary", "tag_source": "taxonomy", "tags": ["AUTO_TAG"]},
            "field_sources": {"creative_direction": "explicit", "story_enhancers": "explicit",
                              "genre_intent": "explicit"},
        },
        "genre_intent_contract": {"tag_source": "taxonomy", "tags": ["AUTO_TAG"],
                                  "default_tags": ["AUTO_DEFAULT"], "prompt_pack_key": "AUTO_PACK"},
        "commercial_brief": {"default": "AUTO_COMMERCIAL"},
        "selected_effect_skills": ["comedy", "hype"],
    }
    system, user = reviews.build_scene_review_prompts(*inputs)
    for unwanted in ("AUTO_TAG", "AUTO_DEFAULT", "AUTO_PACK", "AUTO_COMMERCIAL"):
        assert unwanted not in system + user
    block = user.split("APPROVED_CREATION_CONTRACTS (source-bound; no taxonomy defaults):\n", 1)[1].split("\nSCENE_CARD", 1)[0]
    contracts = json.loads(block)
    assert contracts["creation_intent_contract"]["creative_direction"] == "No new abilities"
    assert contracts["story_enhancers"]["effect_skills"] == ["comedy_engine"]
    assert "selected_effect_skills" not in contracts
    assert contracts["genre_intent_contract"]["tags"] == []


def test_missing_goals_remain_missing_without_default_tension_or_hook():
    inputs = _inputs()
    inputs[2].purpose = {}
    system, user = reviews.build_scene_review_prompts(*inputs)
    assert '"purpose": {}' in user
    for invented in ("raise tension", "拉高当前张力", "推进本章主线", "advance the chapter spine"):
        assert invented not in system + user


def test_missing_adopted_reference_still_fails_closed(monkeypatch):
    from bestseller.services.character_intelligence.strategy import AdoptedReferenceUnavailable

    def unavailable(*args, **kwargs):
        raise AdoptedReferenceUnavailable("The explicitly adopted source is missing")

    monkeypatch.setattr(reviews, "required_reference_projection", unavailable)
    with pytest.raises(AdoptedReferenceUnavailable):
        reviews.build_scene_review_prompts(*_inputs())

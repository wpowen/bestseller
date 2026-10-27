"""Genre goals guide prompts; only selected contracts authorize story events."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.genre_review_profiles import resolve_genre_review_profile
from bestseller.services.reviews import build_chapter_rewrite_prompts

pytestmark = pytest.mark.unit


def _rewrite_prompts(language: str, contract_summary: str) -> tuple[str, str]:
    # Minimal in-memory counterparts of test_review_services' rewrite fixtures.
    project = SimpleNamespace(
        title="山门议事", genre="玄幻", sub_genre=None,
        language=language,
        # The genre-focus marker belongs to the explicitly selected legacy
        # template path; ordinary source-only requests are covered below.
        metadata_json={"creation_intent_contract": {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {
                "require_methodology_overlay": True,
                "methodology_contract_mode": "strict",
                "selected_chapter_contract": {
                    "chapter_number": 12, "contract_summary": contract_summary,
                },
            },
        }},
    )
    chapter = SimpleNamespace(
        chapter_number=12, title="核对名册", chapter_goal="核实名册并作出选择。",
        target_word_count=2200, metadata_json={},
    )
    context = SimpleNamespace(
        chapter_contract=SimpleNamespace(contract_summary=contract_summary),
        previous_scene_summaries=[], chapter_scenes=[], recent_timeline_events=[],
        retrieval_chunks=[],
    )
    return build_chapter_rewrite_prompts(
        project, chapter,
        SimpleNamespace(content_md="众人核对名册，确认误记后同意重审。", word_count=2200),
        SimpleNamespace(
            instructions="修复名册核对段落的指代。", rewrite_strategy="targeted",
            metadata_json={"editor_prompt_protocol": "3.1-lossless-source-time"},
        ),
        chapter_context=context,
    )


_BAD_CHAPTER_REWRITE = {
    "zh-CN": (
        "确保本章的核心战斗/升级事件完整落地",
        "必须让读者对下一章的威胁或升级充满期待",
        "如果战斗密度不足，考虑在准备场景中加入小规模冲突或试探战",
    ),
    "en": (
        "Ensure the chapter's core battle/upgrade event lands completely",
        "readers must anticipate the next chapter's threat or upgrade",
        "If combat density is low, add small-scale skirmishes or probing fights",
    ),
}


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_actual_chapter_rewrite_does_not_promote_genre_to_event_contract(language: str) -> None:
    contract = "本章只核实名册，结尾双方同意重新审议。"
    system, user = _rewrite_prompts(language, contract)
    assert contract in user
    assert ("【品类重写方向】" if language == "zh-CN" else "[Genre rewrite focus]") in user
    for instruction in _BAD_CHAPTER_REWRITE[language]:
        assert instruction not in system + "\n" + user


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_actual_chapter_rewrite_preserves_selected_battle_and_ending_contract(language: str) -> None:
    battle = "本章已选事件：演武场战斗在缴械后结束，不追加追兵。"
    ending = "本章已选结尾：胜者归还木剑，双方约定下月再会。"
    _, user = _rewrite_prompts(language, battle + ending)
    assert battle in user
    assert ending in user
    # Battle vocabulary remains legitimate when it comes from this contract.
    assert "战斗" in user


def _source_complete_rewrite_prompt(project, chapter, draft, task, context):
    """Use the executor's real pure source projection, voice and editor adapter.

    This fixture supplies current author inputs; it does not simulate database
    adoption or label a generated chapter plan as an approved author contract.
    """
    from copy import deepcopy
    import hashlib
    import json

    from bestseller.services.chapter_generation_input_builder import (
        build_chapter_generation_input_bundle,
    )
    from bestseller.services.commercial_review_context import project_commercial_source
    from bestseller.services.editable_plan_source import REVIEW_SOURCE_SCHEMA
    from bestseller.services.editor_writing_voice_source import attach_current_editor_voice
    from bestseller.services.lossless_rewrite_prompt import prepare_source_complete_prompt

    system, user = build_chapter_rewrite_prompts(project, chapter, draft, task, context)
    bundle = build_chapter_generation_input_bundle(
        project=project, chapter=chapter, scenes=[], context_packet=context,
        target_word_count=chapter.target_word_count,
    ).model_dump(mode="json")
    selected = project_commercial_source(
        bundle, chapter_number=chapter.chapter_number, content_md=draft.content_md,
    )
    source = attach_current_editor_voice({
        "schema": REVIEW_SOURCE_SCHEMA,
        "current_source_channels": selected,
    }, project)
    before = deepcopy(source)
    compiled_system, compiled_user, receipt = prepare_source_complete_prompt(
        system_prompt=system, user_prompt=user, source_context=source, output_tokens=16000,
    )
    source_json = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert source_json in compiled_user
    assert receipt["source_sha256"] == hashlib.sha256(source_json.encode()).hexdigest()
    assert receipt["compiler"]["source_integrity_complete"] is True
    assert receipt["compiler"]["dropped"] == []
    assert receipt["compiler"]["truncated"] == []
    assert source == before
    return compiled_system, compiled_user, receipt, selected


def _selected_source_fixture(*, language="zh-CN", mode="off", provenance="explicit"):
    selected = {
        "canon_guardrails": {"rule": "裴镜渊不得登场。"},
        "selected_chapter_contract": {
            "chapter_number": 2,
            "event": "演武场战斗在缴械后结束。不追加追兵。",
        },
        "ending_frame_contract": {
            "chapter_number": 2,
            "rule": "胜者归还木剑。双方约定下月再会。",
        },
        "opening_quality_contract": {
            "applicable_chapters": [1, 2, 3],
            "rule": "开篇先核对名册。确认误记后同意重审。",
        },
        "effect_realization_contracts": {
            "comedy_engine": {
                "chapter_number": 2,
                "concrete_beat": "把包销草案折成扇面。借守卒笑声当厅钉价。",
            },
        },
    }
    hook = {
        **selected,
        "require_methodology_overlay": True,
        "methodology_contract_mode": mode,
    }
    creation = {
        "hook_spec": hook,
        "story_enhancers": {"effect_skills": ["comedy_engine"]},
        "field_sources": (
            {"hook_spec": provenance, "story_enhancers": provenance}
            if provenance is not None else {}
        ),
    }
    project = SimpleNamespace(
        id=uuid4(), slug="selected-source-editor-test", title="名册复核",
        genre="玄幻", sub_genre=None, language=language,
        # Copies in caches deliberately carry the same data: only canonical
        # field provenance may authorize the author channel.
        metadata_json={"creation_intent_contract": creation, **selected},
    )
    chapter = SimpleNamespace(
        chapter_number=2, title="复核", chapter_goal="修复有据的指代。",
        target_word_count=2200, metadata_json={**selected},
    )
    context = SimpleNamespace(
        chapter_contract={"contract_summary": selected},
        canon_guardrails_block="旧正典块不能自行授予来源权。",
        hook_echo_block="旧尾钩块不能自行授予来源权。",
        previous_scene_summaries=[], chapter_scenes=[], recent_timeline_events=[],
        retrieval_chunks=[],
    )
    draft = SimpleNamespace(content_md="众人核对名册。确认误记后同意重审。", word_count=2200)
    task = SimpleNamespace(
        instructions="修复名册核对段落的指代。", rewrite_strategy="targeted", metadata_json={},
    )
    return project, chapter, draft, task, context, selected


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("mode", ["off", "warn"])
@pytest.mark.parametrize("protocol", [None, "3.1-lossless-source-time"])
def test_source_complete_editor_retains_explicit_sources_in_both_protocols(
    language, mode, protocol,
):
    from copy import deepcopy
    import json

    project, chapter, draft, task, context, selected = _selected_source_fixture(
        language=language, mode=mode,
    )
    if protocol is not None:
        task.metadata_json["editor_prompt_protocol"] = protocol
    before = deepcopy(project.metadata_json)
    original_system, original_user = build_chapter_rewrite_prompts(
        project, chapter, draft, task, context,
    )
    system, user, receipt, sources = _source_complete_rewrite_prompt(
        project, chapter, draft, task, context,
    )

    approved = sources["explicit_creator_requirements"]["creation_intent_contract"]
    for key, value in selected.items():
        assert approved["hook_spec"][key] == value
        assert json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) in user
    assert approved["field_sources"] == {"hook_spec": "explicit", "story_enhancers": "explicit"}
    assert draft.content_md not in original_system
    assert user.count(draft.content_md) == 1
    assert original_user in user
    assert "旧正典块不能自行授予来源权" not in user
    assert "旧尾钩块不能自行授予来源权" not in user
    assert "current_writing_voice" in user
    assert "Source precedence:" in system
    assert receipt["source_complete"] is True and receipt["source_sha256"]
    assert project.metadata_json == before
    if protocol is None:
        assert ("Do not invent story facts" if language == "en"
                else "不得为满足建议虚构故事事实") in original_system
    else:
        assert ("New fictional actions" if language == "en"
                else "允许在已选故事范围内创作后续行动") in original_system
    for instruction in _BAD_CHAPTER_REWRITE[language]:
        assert instruction not in system + "\n" + user


@pytest.mark.parametrize("provenance", [None, "default", "user"])
def test_source_complete_editor_does_not_promote_same_data_from_caches(provenance):
    import json

    project, chapter, draft, task, context, selected = _selected_source_fixture(
        provenance=provenance,
    )
    _, user, receipt, sources = _source_complete_rewrite_prompt(
        project, chapter, draft, task, context,
    )

    assert sources["explicit_creator_requirements"] == {}
    assert sources["approved_creation_contracts"] == {}
    for value in selected.values():
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert encoded not in user
    assert receipt["source_complete"] is True
    assert user.count(draft.content_md) == 1


# Check consumed profile fields, not the source file or every genre's vocabulary.
_BAD_PROFILE_INSTRUCTIONS = {
    "volume_plan": {
        "zh": ("至少 2-3 场有阶段意义的战斗/对碰", "指向下一卷更大的威胁或更高的升级诱惑"),
        "en": ("at least 2-3 phase-significant fights", "pointing to a larger threat or higher upgrade lure"),
    },
    "outline": {
        "zh": (
            "确保每一章都有明确的战斗推进或升级进展", "避免出现连续两章以上",
            "每章至少包含一个冲突升级节点或升级进展事件", "战斗章节和准备/恢复章节交替出现",
            "每章结尾必须有硬钩子（新威胁、异变、升级契机）", "每 3-5 章必须出现",
        ),
        "en": (
            "ensuring every chapter has clear combat advancement or upgrade progress",
            "Avoid two or more consecutive chapters of pure setup",
            "Every chapter has at least one conflict escalation node or upgrade progress event",
            "Battle and preparation/recovery chapters alternate",
            "Every chapter ending must have a hard hook (new threat, mutation, upgrade opportunity)",
            "Every 3-5 chapters must deliver",
        ),
    },
    "scene_review": {
        "zh": ("任意类型：尾钩是否指向更大的威胁、更深的谜题或更高的诱惑",),
        "en": ("Any type: Does the ending hook point to a bigger threat, deeper mystery, or higher lure",),
    },
    "chapter_review": {
        "zh": ("章节末尾是否留下了让读者无法停下来的悬念、威胁或期待",),
        "en": ("Does the chapter ending leave a cliffhanger, threat, or anticipation that compels continued reading",),
    },
    "scene_rewrite": {
        "zh": (
            "重写时必须强化战斗碰撞", "突破后的新能力立刻在场景内被演示",
            "用更大的威胁、更高的悬念或更诱人的升级线索收尾", "改为通过对战动作间接展示",
        ),
        "en": (
            "Rewrites must strengthen power-collision impact", "new abilities demonstrated or contrasted immediately in-scene",
            "close with a larger threat, higher suspense, or more enticing upgrade clue", "show through combat actions instead",
        ),
    },
    "chapter_rewrite": {
        "zh": _BAD_CHAPTER_REWRITE["zh-CN"] + ("保持战斗场景的密度和爽感",),
        "en": _BAD_CHAPTER_REWRITE["en"] + ("Maintain combat scene density and satisfaction",),
    },
}


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("stage", list(_BAD_PROFILE_INSTRUCTIONS))
def test_action_progression_profile_has_no_default_event_or_cadence_mandate(stage: str, language: str) -> None:
    profile = resolve_genre_review_profile("玄幻", None)
    assert profile.category_key == "action-progression"
    prompts = profile.planner_prompts if stage in {"volume_plan", "outline"} else profile.judge_prompts
    system = getattr(prompts, f"{stage}_system_{language}")
    instruction = getattr(prompts, f"{stage}_instruction_{language}")
    assert system.strip() and instruction.strip()
    combined = system + "\n" + instruction
    growth_terms = ("成长", "升级") if language == "zh" else ("growth", "progression")
    assert any(term in combined.lower() for term in growth_terms)
    for old_mandate in _BAD_PROFILE_INSTRUCTIONS[stage][language]:
        assert old_mandate not in combined

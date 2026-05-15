"""End-to-end integration tests for the quality-levers Mode-A wiring.

These tests do NOT exercise the LLM or the database. They verify the
plumbing: given a ``ProjectModel.metadata`` payload that mirrors what
a real project's meta.yaml would carry, the writer/critic prompt
fragments rendered by :mod:`integrator` contain the expected lever
content.

Together with the loader-level tests in
``tests/unit/test_quality_levers_*.py`` this proves the end-to-end
chain:

    meta.yaml fields
       → ProjectModel.metadata dict
       → extract_quality_levers_meta
       → WriterLeverContext / CriticLeverContext
       → build_writer_quality_levers_block / build_critic_quality_levers_block
       → rendered prompt fragment
"""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers import (
    CriticLeverContext,
    WriterLeverContext,
    build_critic_quality_levers_block,
    build_writer_quality_levers_block,
    extract_quality_levers_meta,
)

pytestmark = pytest.mark.unit


def _qingya_project_metadata() -> dict[str, object]:
    """Synthetic metadata matching the《青崖诡事》meta.yaml shape."""

    return {
        "target_platform": "qimao",
        "style_anchors": ["lu_xun_cold", "yan_leisheng", "jin_yong_dialogue"],
        "chapter_positions": {
            "1": ["first_chapter"],
            "31": ["volume_opener"],
        },
        "character_profiles": ["shen_qingya", "zhou_shensuan", "the_fourth_man"],
        "rejection_history": [
            {
                "date": "2026-05-14",
                "platform": "qimao",
                "reason_text": "故事开篇的切入点缺乏足够的吸引力",
                "parsed_causes": ["ordinary_entry", "weak_attraction"],
                "affected_chapters": [1],
            },
        ],
    }


def _build_writer_context_from_metadata(
    metadata: dict[str, object],
    *,
    chapter_number: int,
    scene_stimulus: str | None = None,
    scene_type: str | None = None,
) -> WriterLeverContext:
    meta = extract_quality_levers_meta(metadata)
    return WriterLeverContext(
        chapter_number=chapter_number,
        language="zh-CN",
        platform=meta.target_platform,
        style_anchors=meta.style_anchors,
        chapter_positions=meta.positions_for_chapter(chapter_number),
        participating_character_ids=meta.character_profile_ids,
        participating_character_profiles=meta.character_profiles,
        scene_stimulus=scene_stimulus,
        scene_type=scene_type,
        chapter_role="hook_chapter" if chapter_number <= 3 else "ordinary_chapter",
        rejection_cause_ids=tuple(
            cause
            for entry in meta.rejection_history
            for cause in entry.parsed_causes
        ),
    )


def test_end_to_end_writer_prompt_includes_all_levers_for_ch1() -> None:
    metadata = _qingya_project_metadata()
    context = _build_writer_context_from_metadata(
        metadata,
        chapter_number=1,
        scene_stimulus="confrontation_with_villain",
        scene_type="investigation_scene",
    )
    prompt = build_writer_quality_levers_block(context)

    # platform (qimao signing gate)
    assert "七猫" in prompt
    assert "前100字" in prompt

    # position profile + window
    assert "first_chapter" in prompt
    assert "opening_window" in prompt

    # style anchors
    assert "lu_xun_cold" in prompt
    assert "yan_leisheng" in prompt
    # anti-AI baseline always present
    assert "anti_ai_voice" in prompt

    # character profile (signature + voice_dna injected)
    assert "shen_qingya" in prompt
    assert "voice_dna" in prompt
    assert "三步反应链" in prompt  # confrontation_with_villain matched a chain entry

    # sensory inventory for investigation_scene
    assert "investigation_scene" in prompt
    assert "olfactory" in prompt

    # phase-4 blocks always render
    assert "chapter_signature_audit" in prompt
    assert "rhythm_engineering" in prompt
    assert "emotion_choreography" in prompt
    assert "information_choreography" in prompt

    # rejection repair playbook applied
    assert "ordinary_entry" in prompt
    assert "weak_attraction" in prompt


def test_end_to_end_writer_prompt_skips_first_chapter_block_for_later_chapter() -> None:
    metadata = _qingya_project_metadata()
    context = _build_writer_context_from_metadata(metadata, chapter_number=50)
    prompt = build_writer_quality_levers_block(context)

    # No first_chapter position tagged for ch50 → fragment absent
    assert "first_chapter" not in prompt
    # Platform pacing block still rendered
    assert "节奏" in prompt
    # Phase 4 blocks still rendered
    assert "chapter_signature_audit" in prompt


def test_end_to_end_writer_prompt_accepts_project_character_profile_dict() -> None:
    metadata = {
        "character_profiles": {
            "lin_che": {
                "display_name": "林澈",
                "role": "protagonist",
                "want_vs_need": {
                    "want": "夺回灵田",
                    "need": "学会授权",
                    "tension": "目标和内在缺口互相拉扯。",
                },
                "voice_dna": {"signature_words": ["先把账算清"]},
                "unique_response_chain": {
                    "moral_dilemma": {
                        "step_1": "停住算盘。",
                        "step_2": "计算授权代价。",
                        "step_3": "选择承担损失。",
                    }
                },
            }
        }
    }
    context = _build_writer_context_from_metadata(
        metadata,
        chapter_number=2,
        scene_stimulus="moral_dilemma",
    )
    prompt = build_writer_quality_levers_block(context)

    assert "character_engine 融合档案" in prompt
    assert "林澈" in prompt
    assert "先把账算清" in prompt


def test_end_to_end_writer_prompt_empty_for_missing_metadata() -> None:
    """A project with empty metadata still produces some content (phase-4 blocks)."""

    context = _build_writer_context_from_metadata({}, chapter_number=5)
    prompt = build_writer_quality_levers_block(context)
    # No platform / position / anchors / characters → these blocks absent
    assert "七猫" not in prompt
    assert "first_chapter" not in prompt
    # Phase 4 still emits, so prompt is non-empty
    assert prompt != ""
    assert "chapter_signature_audit" in prompt


def test_end_to_end_critic_prompt_carries_platform_and_position() -> None:
    metadata = _qingya_project_metadata()
    meta = extract_quality_levers_meta(metadata)
    context = CriticLeverContext(
        chapter_number=1,
        language="zh-CN",
        platform=meta.target_platform,
        chapter_positions=meta.positions_for_chapter(1),
    )
    prompt = build_critic_quality_levers_block(context)

    assert "七猫" in prompt
    assert "first_chapter" in prompt
    # Critic must not be polluted with writer-only fragments
    assert "rhythm_engineering" not in prompt
    assert "character_engine" not in prompt or "voice_dna" not in prompt


def test_end_to_end_critic_prompt_empty_when_no_signals() -> None:
    context = CriticLeverContext(chapter_number=5)
    assert build_critic_quality_levers_block(context) == ""


def test_end_to_end_writer_prompt_english_skips_platform_block() -> None:
    metadata = _qingya_project_metadata()
    meta = extract_quality_levers_meta(metadata)
    context = WriterLeverContext(
        chapter_number=1,
        language="en",
        platform=meta.target_platform,
        style_anchors=meta.style_anchors,
        chapter_positions=meta.positions_for_chapter(1),
    )
    prompt = build_writer_quality_levers_block(context)

    # English projects bypass the Chinese platform block
    assert "七猫" not in prompt
    # But style anchors + position + phase-4 still render
    assert "first_chapter" in prompt
    assert "anti_ai_voice" in prompt

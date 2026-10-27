from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services import narrative_contracts, planner, workflows
from bestseller.services.story_enhancers import (
    StoryEnhancerSelection,
    render_story_enhancer_writer_block,
)

pytestmark = pytest.mark.unit


def _batch(*, emotion: str | None = None) -> ChapterOutlineBatchInput:
    purpose = {"story": "林澈撬开封死的药柜，取出被藏起的账页。"}
    if emotion is not None:
        purpose["emotion"] = emotion
    return ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "content-boundary",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "药柜后的账页",
                    "goal": "林澈要在掌柜回来前找到被藏起的账页。",
                    "main_conflict": "伙计守在门外，掌柜随时会回来。",
                    "hook_description": "账页末尾写着林澈父亲的名字。",
                    "protagonist_flaw": "遇事总想独自扛下。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "time_label": "掌柜离店后的一刻钟",
                            "participants": ["林澈"],
                            "purpose": purpose,
                            "entry_state": {"summary": "林澈被锁在药柜外。"},
                            "exit_state": {"summary": "林澈拿到账页。"},
                        }
                    ],
                }
            ],
        }
    )


def _identity_manifest() -> list[dict[str, str]]:
    return [{"name": "林澈", "role": "protagonist"}]


def test_generated_outline_repair_does_not_invent_missing_emotion() -> None:
    batch = _batch()

    planner._repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=_identity_manifest(),
        protagonist_flaw_default="遇事总想独自扛下。",
    )

    assert batch.chapters[0].scenes[0].purpose == {
        "story": "林澈撬开封死的药柜，取出被藏起的账页。"
    }


def test_generated_outline_repair_preserves_real_emotion() -> None:
    batch = _batch(emotion="林澈怕账页证明父亲曾经出卖同伴，却仍决定翻到最后一页。")

    planner._repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=_identity_manifest(),
        protagonist_flaw_default="遇事总想独自扛下。",
    )


@pytest.mark.parametrize(
    "repair",
    [
        lambda batch: planner._repair_generated_volume_outline_contract_inputs(
            batch,
            identity_manifest=_identity_manifest(),
            protagonist_flaw_default="遇事总想独自扛下。",
        ),
        lambda batch: workflows._repair_chapter_outline_contract_inputs(
            batch,
            identity_manifest=_identity_manifest(),
        ),
    ],
)
def test_saved_framework_emotion_template_is_removed_on_recovery(repair) -> None:
    batch = _batch(
        emotion=(
            "围绕「林澈撬开封死的药柜，取出被藏起的账页。」写出当事人一个具体的怕、要或误判，"
            "让读者看见情绪来源，并把选择或代价推到下一拍。"
        )
    )

    repair(batch)

    assert "emotion" not in batch.chapters[0].scenes[0].purpose


def test_similar_author_written_emotion_is_not_removed() -> None:
    emotion = "林澈同时有怕、要和误判：他盯着父亲的名字，手指压住账页不肯松开。"
    batch = _batch(emotion=emotion)

    planner._repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=_identity_manifest(),
        protagonist_flaw_default="遇事总想独自扛下。",
    )

    assert batch.chapters[0].scenes[0].purpose["emotion"] == emotion


@pytest.mark.parametrize(
    "repair",
    [
        lambda batch: planner._repair_generated_volume_outline_contract_inputs(
            batch,
            identity_manifest=_identity_manifest(),
            protagonist_flaw_default="遇事总想独自扛下。",
        ),
        lambda batch: workflows._repair_chapter_outline_contract_inputs(
            batch,
            identity_manifest=_identity_manifest(),
        ),
    ],
)
def test_saved_effect_label_is_removed_only_for_structured_known_beat(repair) -> None:
    beat = "伙计把掌柜的催问听成催账，当众翻出三张过期欠条。"
    batch = _batch(emotion="林澈盯着父亲的名字，手指不肯松开账页。")
    chapter = batch.chapters[0]
    chapter.selected_effect_skills = {
        "primary": "comedy_engine",
        "expected_contracts": {"comedy_engine": {"concrete_beat": beat}},
    }
    chapter.scenes[0].purpose["story"] += f"；效果落点：{beat}"

    repair(batch)

    assert chapter.scenes[0].purpose["story"].endswith(f"；{beat}")
    assert "效果落点" not in chapter.scenes[0].purpose["story"]


def test_unproven_effect_label_text_is_preserved() -> None:
    batch = _batch()
    batch.chapters[0].scenes[0].purpose["story"] += "；效果落点：这是作者自己的栏目标题。"

    planner._repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=_identity_manifest(),
        protagonist_flaw_default="遇事总想独自扛下。",
    )

    assert "效果落点：这是作者自己的栏目标题" in batch.chapters[0].scenes[0].purpose["story"]


def test_materialization_repair_keeps_missing_emotion_visible() -> None:
    batch = _batch()

    workflows._repair_chapter_outline_contract_inputs(
        batch,
        identity_manifest=_identity_manifest(),
    )

    assert "emotion" not in batch.chapters[0].scenes[0].purpose


def test_story_purpose_repair_does_not_backfill_emotion_placeholder() -> None:
    batch = _batch()
    batch.chapters[0].scenes[0].purpose = {}
    report = SimpleNamespace(
        blocking_violations=(
            SimpleNamespace(
                code="PLAN_SCENE_STORY_PURPOSE_MISSING",
                metadata={"chapter_number": 1, "scene_number": 1},
            ),
        )
    )

    planner._repair_generated_volume_outline_contract_blocks(
        batch,
        report,
        identity_manifest=_identity_manifest(),
    )

    purpose = batch.chapters[0].scenes[0].purpose
    assert purpose.get("story")
    assert "emotion" not in purpose


def test_legacy_scene_repair_does_not_backfill_emotion_placeholder() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        time_label="章节开场",
        purpose={"story": "推动剧情"},
        concrete_goal="林澈撬开药柜取出账页。",
        title="药柜",
    )

    narrative_contracts.repair_legacy_scene_contract_pre_draft(
        scene,
        chapter_number=1,
    )

    assert scene.purpose.get("story")
    assert "emotion" not in scene.purpose


def test_effect_route_keeps_story_and_beat_without_internal_label() -> None:
    payload = {
        "chapters": [
            {
                "chapter_number": 1,
                "title": "药柜后的账页",
                "selected_effect_skills": {},
                "scenes": [
                    {
                        "scene_number": 1,
                        "purpose": {"story": "林澈撬开药柜，伙计在门外拖住掌柜。"},
                    }
                ],
            }
        ]
    }
    beat = "伙计故意把掌柜的质问听成催账，当众翻出三张过期欠条拖延时间。"

    repaired = planner._apply_story_enhancer_route_patches(
        payload,
        [
            {
                "chapter_number": 1,
                "scene_number": 1,
                "effect": "comedy_engine",
                "slot": "primary",
                "beat": beat,
            }
        ],
        selection=StoryEnhancerSelection(effect_skills=("comedy_engine",)),
    )

    chapter = repaired["chapters"][0]
    story = chapter["scenes"][0]["purpose"]["story"]
    assert chapter["selected_effect_skills"]["primary"] == "comedy_engine"
    assert chapter["selected_effect_skills"]["expected_contracts"]["comedy_engine"] == {
        "concrete_beat": beat
    }
    assert story == "林澈撬开药柜，伙计在门外拖住掌柜。"
    assert "效果落点" not in story
    writer_block = render_story_enhancer_writer_block(
        {},
        {"selected_effect_skills": chapter["selected_effect_skills"]},
    )
    assert beat in writer_block

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.story_engine import (
    ChapterCreativeProjection,
    ChoiceOption,
    StoryEngineWindow,
    canonical_json_hash,
    story_engine_window_to_mapping,
)


def _projection(chapter_number: int, before: int, after: int) -> ChapterCreativeProjection:
    post_hash = canonical_json_hash(
        {"pressure": {"category": "exposure", "value": after}}
    )
    return ChapterCreativeProjection(
        chapter_number=chapter_number,
        choice_id=f"ch{chapter_number:03d}-publish",
        pre_state={"pressure": {"category": "exposure", "value": before}},
        pressure="对手正在销毁唯一证据",
        known_facts=("档案室今晚封存",),
        options=(
            ChoiceOption(
                choice_id=f"ch{chapter_number:03d}-publish",
                label="立即公开证据",
                reachable_state_hash=post_hash,
            ),
            ChoiceOption(
                choice_id=f"ch{chapter_number:03d}-hide",
                label="暂时隐藏证据",
                reachable_state_hash=f"alternative-{chapter_number}",
            ),
        ),
        chosen_option_id=f"ch{chapter_number:03d}-publish",
        chosen_path="立即公开证据并保护证人",
        alternative_costs=("隐藏证据会让证人失去最后窗口",),
        opponent_strategy="冻结主角权限并追查证人",
        due_obligations=("保护证人",),
        expected_transitions=(
            {
                "key": "pressure",
                "category": "exposure",
                "before": before,
                "operator": "set",
                "after": after,
                "evidence": "主角公开了证据",
            },
        ),
        expected_post_state_hash=post_hash,
        fingerprint=f"publish|evidence|pressure-{chapter_number}",
    )


def test_window_serialization_keeps_only_current_chapter_creative_facts() -> None:
    window = StoryEngineWindow(
        window_id=str(uuid4()),
        engine_id="engine-1",
        engine_version=2,
        engine_artifact_id=str(uuid4()),
        source_engine_hash="engine-hash",
        projections=(_projection(1, 0, 1), _projection(2, 1, 2)),
    )

    payload = story_engine_window_to_mapping(window)

    assert payload["start_chapter"] == 1
    assert payload["end_chapter"] == 2
    assert len(payload["projections"]) == 2
    assert payload["projections"][0]["pre_state_hash"]
    assert payload["projections"][0]["projection_hash"]
    assert "future_facts" not in payload["projections"][0]


def test_window_rejects_non_contiguous_chapters() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        StoryEngineWindow(
            window_id="window-1",
            engine_id="engine-1",
            engine_version=1,
            engine_artifact_id="artifact-1",
            source_engine_hash="engine-hash",
            projections=(_projection(1, 0, 1), _projection(3, 1, 2)),
        )


def test_projection_rejects_chosen_path_hash_mismatch() -> None:
    with pytest.raises(ValueError, match="chosen option"):
        ChapterCreativeProjection(
            chapter_number=1,
            choice_id="publish",
            pre_state={"pressure": {"category": "exposure", "value": 0}},
            pressure="对手施压",
            options=(
                ChoiceOption("publish", "公开", "wrong-hash"),
                ChoiceOption("hide", "隐藏", "other-hash"),
            ),
            chosen_option_id="publish",
            chosen_path="公开证据",
            opponent_strategy="追查证人",
            expected_transitions=(
                {
                    "key": "pressure",
                    "category": "exposure",
                    "before": 0,
                    "operator": "set",
                    "after": 1,
                    "evidence": "公开证据",
                },
            ),
        )

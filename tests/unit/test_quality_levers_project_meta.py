"""Unit tests for ``quality_levers.project_meta``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.project_meta import (
    extract_quality_levers_meta,
)

pytestmark = pytest.mark.unit


def test_extract_empty_metadata_returns_empty_view() -> None:
    meta = extract_quality_levers_meta(None)
    assert meta.target_platform is None
    assert meta.style_anchors == ()
    assert meta.chapter_positions == {}
    assert meta.character_profile_ids == ()
    assert meta.rejection_history == ()
    assert meta.emotion_driven_kernel is None


def test_extract_extracts_target_platform() -> None:
    meta = extract_quality_levers_meta({"target_platform": "qimao"})
    assert meta.target_platform == "qimao"


def test_extract_blank_target_platform_normalises_to_none() -> None:
    meta = extract_quality_levers_meta({"target_platform": "   "})
    assert meta.target_platform is None


def test_extract_style_anchors_from_list_and_string() -> None:
    meta_list = extract_quality_levers_meta(
        {"style_anchors": ["lu_xun_cold", "yan_leisheng"]}
    )
    assert meta_list.style_anchors == ("lu_xun_cold", "yan_leisheng")

    meta_str = extract_quality_levers_meta({"style_anchors": "lu_xun_cold"})
    assert meta_str.style_anchors == ("lu_xun_cold",)


def test_extract_chapter_positions_coerces_keys_and_values() -> None:
    meta = extract_quality_levers_meta(
        {
            "chapter_positions": {
                "1": ["first_chapter"],
                "31": ["volume_opener", "first_villain_reveal"],
                "abc": ["should_be_dropped"],
            }
        }
    )
    assert meta.chapter_positions == {
        1: ("first_chapter",),
        31: ("volume_opener", "first_villain_reveal"),
    }
    assert meta.positions_for_chapter(1) == ("first_chapter",)
    assert meta.positions_for_chapter(99) == ()


def test_extract_character_profile_ids_from_list_and_dict() -> None:
    meta_list = extract_quality_levers_meta(
        {"character_profiles": ["shen_qingya", "zhou_shensuan"]}
    )
    assert meta_list.character_profile_ids == ("shen_qingya", "zhou_shensuan")
    assert meta_list.character_profiles == ()

    meta_dict = extract_quality_levers_meta(
        {
            "character_profiles": {
                "shen_qingya": {"voice_dna": {}, "display_name": "沈青崖"},
                "the_fourth_man": {},
            }
        }
    )
    assert set(meta_dict.character_profile_ids) == {
        "shen_qingya",
        "the_fourth_man",
    }
    assert meta_dict.character_profiles[0]["character_id"] == "shen_qingya"
    assert meta_dict.character_profiles[0]["display_name"] == "沈青崖"

    meta_profile_list = extract_quality_levers_meta(
        {
            "character_profiles": [
                {"character_id": "lin_che", "display_name": "林澈"},
                {"id": "su_wan", "display_name": "苏绾"},
            ]
        }
    )
    assert meta_profile_list.character_profile_ids == ("lin_che", "su_wan")
    assert meta_profile_list.character_profiles[0]["display_name"] == "林澈"


def test_extract_rejection_history() -> None:
    meta = extract_quality_levers_meta(
        {
            "rejection_history": [
                {
                    "date": "2026-05-14",
                    "platform": "qimao",
                    "reason_text": "故事开篇的切入点缺乏足够的吸引力",
                    "parsed_causes": ["ordinary_entry", "weak_attraction"],
                    "affected_chapters": [1],
                },
                {"date": "ignored — missing reason"},
            ]
        }
    )
    assert len(meta.rejection_history) == 2
    first = meta.rejection_history[0]
    assert first.platform == "qimao"
    assert "ordinary_entry" in first.parsed_causes
    assert first.affected_chapters == (1,)


def test_extract_emotion_driven_kernel_when_mapping() -> None:
    payload = {
        "reader_emotion_promise": "让读者等待身份揭开。",
        "empathy_contracts": [],
    }
    meta = extract_quality_levers_meta({"emotion_driven_kernel": payload})
    assert meta.emotion_driven_kernel == payload


def test_extract_handles_corrupt_inputs() -> None:
    meta = extract_quality_levers_meta(
        {
            "style_anchors": 42,
            "chapter_positions": "not a dict",
            "character_profiles": 3.14,
            "rejection_history": "garbage",
            "emotion_driven_kernel": "not a dict",
        }
    )
    assert meta.style_anchors == ()
    assert meta.chapter_positions == {}
    assert meta.character_profile_ids == ()
    assert meta.character_profiles == ()
    assert meta.rejection_history == ()
    assert meta.emotion_driven_kernel is None

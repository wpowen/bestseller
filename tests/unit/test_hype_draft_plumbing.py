"""End-to-end plumbing: hype blocks → scene prompt.

Verifies the integration seam from the plan §Phase 1-2 "wire hype engine
into the live pipeline" task:

* ``SceneWriterContextPacket`` carries the new hype fields with safe
  ``None`` defaults so legacy code paths don't have to be updated.
* ``build_scene_draft_prompts`` renders the pre-rendered hype blocks into
  the user prompt in both Chinese and English language branches.
* When the blocks are ``None`` (legacy project with empty HypeScheme),
  the prompt is unchanged — no stray markers leak through.

These tests stand between the ``test_hype_engine_prompt`` unit tests
(which cover the rendering helpers in isolation) and the scene-pipeline
integration path in ``pipelines.py`` (covered by the scene pipeline
tests).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.services.drafts import build_scene_draft_prompts


def _minimal_packet_kwargs() -> dict:
    return {
        "project_id": uuid4(),
        "project_slug": "test-project",
        "chapter_id": uuid4(),
        "scene_id": uuid4(),
        "chapter_number": 1,
        "scene_number": 1,
        "query_text": "q",
    }


pytestmark = pytest.mark.unit


def _sample_project(*, language: str = "zh-CN") -> SimpleNamespace:
    return SimpleNamespace(
        title="诡豪试炼",
        slug="gui-hao-trial",
        language=language,
    )


def _sample_chapter() -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=1,
        chapter_goal="亮出冥符翻盘",
        title="第一章·冥符出世",
    )


def _sample_scene() -> SimpleNamespace:
    return SimpleNamespace(
        scene_number=1,
        title="当众羞辱",
        participants=["主角", "仇家"],
        purpose={"story": "抛出冥符底牌", "emotion": "压迫感"},
        time_label="夜",
        entry_state={"status": "被羞辱"},
        exit_state={"status": "冥符显形"},
        scene_type="hook",
        target_word_count=1200,
    )


def _sample_style_guide() -> SimpleNamespace:
    return SimpleNamespace(
        pov_type="third-limited",
        tone_keywords=["紧张", "压迫"],
    )


# ---------------------------------------------------------------------------
# SceneWriterContextPacket defaults.
# ---------------------------------------------------------------------------


class TestSceneContextPacketHypeDefaults:
    def test_new_packet_has_none_hype_fields(self) -> None:
        packet = SceneWriterContextPacket(**_minimal_packet_kwargs())
        assert packet.reader_contract_block is None
        assert packet.hype_constraints_block is None
        assert packet.assigned_hype_type is None
        assert packet.assigned_hype_recipe_key is None
        assert packet.assigned_hype_intensity is None

    def test_packet_accepts_populated_hype_fields(self) -> None:
        packet = SceneWriterContextPacket(
            **_minimal_packet_kwargs(),
            reader_contract_block="【读者契约】...",
            hype_constraints_block="【本章爽点约束】...",
            assigned_hype_type="face_slap",
            assigned_hype_recipe_key="冥符拍脸",
            assigned_hype_intensity=8.5,
        )
        assert packet.reader_contract_block == "【读者契约】..."
        assert packet.hype_constraints_block == "【本章爽点约束】..."
        assert packet.assigned_hype_type == "face_slap"
        assert packet.assigned_hype_recipe_key == "冥符拍脸"
        assert packet.assigned_hype_intensity == 8.5


# ---------------------------------------------------------------------------
# build_scene_draft_prompts — hype blocks land in user_prompt.
# ---------------------------------------------------------------------------


class TestSceneDraftPromptsHypeBlocks:
    def test_none_blocks_leave_prompt_unchanged(self) -> None:
        """Legacy projects: no hype fields → no stray markers in prompt."""

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=None,
            hype_constraints_block=None,
        )
        assert "【读者契约】" not in user_prompt
        assert "【本章爽点约束】" not in user_prompt

    def test_empty_string_blocks_leave_prompt_unchanged(self) -> None:
        """Empty strings (also produced by no-op path) must not emit markers."""

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block="",
            hype_constraints_block="",
        )
        assert "【读者契约】" not in user_prompt
        assert "【本章爽点约束】" not in user_prompt

    def test_populated_blocks_land_in_user_prompt_zh(self) -> None:
        reader_block = (
            "【读者契约】(卖点：诡异复苏 / 阴阳万亿资产)\n"
            "本书承诺：第一章就要亮出冥符阴兵才是万亿资产的世界规则。"
        )
        hype_block = (
            "【本章爽点约束】\n"
            "- 爽点类型: face_slap (强度目标 8.5/10)\n"
            "- 推荐配方: 冥符拍脸-当众羞辱反转\n"
            "- 爽点 ≠ 章末悬念"
        )
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=reader_block,
            hype_constraints_block=hype_block,
        )
        assert "【读者契约】" in user_prompt
        assert "诡异复苏" in user_prompt
        assert "【本章爽点约束】" in user_prompt
        assert "face_slap" in user_prompt
        assert "冥符拍脸-当众羞辱反转" in user_prompt
        assert "爽点 ≠ 章末悬念" in user_prompt

    def test_populated_blocks_land_in_user_prompt_en(self) -> None:
        reader_block = (
            "[READER CONTRACT] selling points: ghost wealth, supernatural capitalism\n"
            "The first chapter must show that money is no longer currency."
        )
        hype_block = (
            "[CHAPTER HYPE CONSTRAINTS]\n"
            "- Assigned hype type: face_slap (intensity 8.5/10)\n"
            "- Recipe: ghost-talisman face-slap\n"
            "- Hype is NOT the cliffhanger."
        )
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(language="en"),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=reader_block,
            hype_constraints_block=hype_block,
        )
        assert "READER CONTRACT" in user_prompt
        assert "CHAPTER HYPE CONSTRAINTS" in user_prompt
        assert "face_slap" in user_prompt
        assert "Hype is NOT the cliffhanger." in user_prompt

    def test_block_order_reader_contract_precedes_hype(self) -> None:
        """Plan-documented order: reader contract, then hype constraints."""

        reader_block = "【读者契约】CONTRACT_MARKER"
        hype_block = "【本章爽点约束】HYPE_MARKER"
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=reader_block,
            hype_constraints_block=hype_block,
        )
        contract_at = user_prompt.index("CONTRACT_MARKER")
        hype_at = user_prompt.index("HYPE_MARKER")
        assert contract_at < hype_at

    def test_only_hype_block_no_reader_contract_still_renders(self) -> None:
        """Chapter 12 (head=10, tail=5) omits reader contract but keeps hype."""

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=None,
            hype_constraints_block="【本章爽点约束】hype-only",
        )
        assert "【读者契约】" not in user_prompt
        assert "【本章爽点约束】" in user_prompt
        assert "hype-only" in user_prompt

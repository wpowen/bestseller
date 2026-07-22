"""Guards for the lean prose-prompt profile.

Evidence behind the profile lives in ``services/prose_prompt_profile``. These
tests guard the two things that can silently break when blocks are dropped:

1. ``full`` must stay byte-identical to the historical prompt (no-op default).
2. ``lean`` must drop only acceptance/planning blocks and must NOT drop canon
   context — losing canon trades AI-flavour for continuity errors.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services.drafts import build_chapter_first_draft_prompts
from bestseller.services.prose_prompt_profile import (
    LEAN_DROPPED_CONSTRAINT_FIELDS,
    constraint_field_enabled,
    resolve_prose_prompt_profile,
    section_enabled,
)

pytestmark = pytest.mark.unit


def _prompts(profile: str | None = None, metadata: dict | None = None) -> tuple[str, str]:
    project = ProjectModel(
        slug="prose-profile-test",
        title="遗物师",
        genre="都市",
        language="zh-CN",
        target_word_count=26_000,
        target_chapters=10,
        metadata_json=metadata or {},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=7,
        title="封条",
        chapter_goal="沈渡当众摊开欠条。",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=2_600,
    )
    chapter.id = uuid4()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="confrontation",
        title="对质",
        time_label="now",
        participants=["沈渡", "卫戎"],
        purpose={"story": "摊牌"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=2_600,
    )
    scene.id = uuid4()
    packet = SimpleNamespace(
        chapter_contract={"closing_hook": "徽章"},
        hard_fact_snapshot={},
        chapter_length_block="正文必须在1800到3500字。",
        timeline_canon_block="只允许使用今晚十一点这个时间锚点。",
        character_role_block="沈渡不能预知门后的人。",
        dialogue_voice_block="沈渡短句。",
        scene_coherence_block="地点变化必须有可见转场。",
        canon_guardrails_block="不得新增角色名。",
        reader_contract_block="读者承诺。",
        hype_constraints_block="爽点约束。",
        hook_echo_block="钩子回环要求。",
        exposition_density_block="铺垫节制要求。",
        voice_dna_block="作者声纹要求。",
        chapter_market_constraints_block="市场硬约束。",
        signature_scene_block="签名场景。",
        prior_persona_feedback_block="上章画像反馈。",
        participant_knowledge_states=[
            {"name": "沈渡", "knows": ["欠条存在"], "does_not_know": ["徽章来历"]}
        ],
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )
    return build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2_600,
        prose_prompt_profile=profile,
    )


class TestResolution:
    def test_default_is_full(self) -> None:
        assert resolve_prose_prompt_profile() == "full"

    def test_explicit_outranks_metadata(self) -> None:
        assert (
            resolve_prose_prompt_profile(
                explicit="full", project_metadata={"prose_prompt_profile": "lean"}
            )
            == "full"
        )

    def test_metadata_outranks_settings(self) -> None:
        assert (
            resolve_prose_prompt_profile(
                project_metadata={"prose_prompt_profile": "lean"}, settings_default="full"
            )
            == "lean"
        )

    def test_unknown_value_falls_through_instead_of_guessing(self) -> None:
        assert (
            resolve_prose_prompt_profile(
                project_metadata={"prose_prompt_profile": "banana"}, settings_default="lean"
            )
            == "lean"
        )

    def test_generation_passes_effective_settings_into_prompt_builder(self) -> None:
        from bestseller.services.drafts import generate_chapter_draft_once

        source = inspect.getsource(generate_chapter_draft_once)
        assert "effective_settings.pipeline" in source
        assert "prose_prompt_profile=" in source


class TestFullIsUnchanged:
    """The historical prompt is the default; a regression here changes every
    in-flight book."""

    def test_unspecified_equals_full(self) -> None:
        assert _prompts(None) == _prompts("full")

    @pytest.mark.parametrize(
        "marker",
        [
            "【写前验收契约】",
            "【字数与结构】",
            "AI套话黑名单",
            "【角色认知边界】",
            "【硬约束与门禁】",
        ],
    )
    def test_full_still_carries_every_block(self, marker: str) -> None:
        assert marker in _prompts("full")[1]


class TestLeanDropsOnlyWhatItShould:
    @pytest.mark.parametrize(
        "marker",
        [
            "【写前验收契约】",
            "【字数与结构】",
            "AI套话黑名单",
            "【方法论证据】",
            "【章末收尾钩子】",
            "【前十章留存硬规则】",
        ],
    )
    def test_acceptance_and_planning_blocks_are_gone(self, marker: str) -> None:
        assert marker not in _prompts("lean")[1]

    @pytest.mark.parametrize(
        "marker",
        [
            "【章节目标】",
            "【章节契约】",
            "【故事圣经上下文】",
            "【弱场景逻辑地图】",
            "【角色认知边界】",
            "【硬约束与门禁】",
        ],
    )
    def test_story_material_survives(self, marker: str) -> None:
        assert marker in _prompts("lean")[1]

    @pytest.mark.parametrize(
        "canon",
        ["今晚十一点", "不得新增角色名", "沈渡不能预知门后的人"],
    )
    def test_canon_context_survives(self, canon: str) -> None:
        """Canon is story fact the writer cannot invent. Dropping it would
        trade AI-flavour for contradictions."""

        assert canon in _prompts("lean")[1]

    @pytest.mark.parametrize(
        "gate_text",
        [
            "钩子回环要求",
            "铺垫节制要求",
            "作者声纹要求",
            "市场硬约束",
            "上章画像反馈",
            "地点变化必须有可见转场",
            "正文必须在1800到3500字",
        ],
    )
    def test_gate_feedback_fields_are_dropped(self, gate_text: str) -> None:
        assert gate_text not in _prompts("lean")[1]

    def test_lean_is_substantially_smaller(self) -> None:
        full_user = _prompts("full")[1]
        lean_user = _prompts("lean")[1]
        assert len(lean_user) < len(full_user) * 0.5

    def test_per_book_metadata_selects_lean(self) -> None:
        assert _prompts(None, {"prose_prompt_profile": "lean"}) == _prompts("lean")


class TestProfileTable:
    def test_full_enables_everything(self) -> None:
        for section in ("acceptance_contract", "word_count_rules", "slop_blacklist"):
            assert section_enabled(section, "full")
        for field in LEAN_DROPPED_CONSTRAINT_FIELDS:
            assert constraint_field_enabled(field, "full")

    def test_canon_fields_are_never_in_the_dropped_set(self) -> None:
        for field in (
            "timeline_canon_block",
            "character_role_block",
            "dialogue_voice_block",
            "canon_guardrails_block",
        ):
            assert field not in LEAN_DROPPED_CONSTRAINT_FIELDS
            assert constraint_field_enabled(field, "lean")


class TestTrimAnchorsSurviveLean:
    """Six of the eight must-keep markers belong to blocks lean drops. Without
    lean-safe anchors the soft trim degrades to head-only truncation and
    silently discards the tail."""

    def test_lean_prompt_matches_at_least_one_trim_anchor(self) -> None:
        from bestseller.services.drafts import _MUST_KEEP_TAIL_MARKERS_ZH

        lean_user = _prompts("lean")[1]
        assert any(marker in lean_user for marker in _MUST_KEEP_TAIL_MARKERS_ZH)

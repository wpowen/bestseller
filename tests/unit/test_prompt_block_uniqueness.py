"""Regression tests for prompt block uniqueness.

Ensures that key prompt sections (AI slop blacklist, golden-three rules,
word-count directives) appear at most once in the final assembled prompt
for both scene-level and chapter-first paths. This guards against future
changes re-introducing the duplication that was removed in Phase 1.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services.ai_slop_blacklist import (
    ALL_SLOP_PHRASES,
    EN_SLOP_ENDINGS,
    EN_SLOP_OPENERS,
    EN_SLOP_PHRASES,
    ZH_SLOP_ENDINGS,
    ZH_SLOP_OPENERS,
    ZH_SLOP_PHRASES,
    render_slop_blacklist_block,
)
from bestseller.services.drafts import (
    build_chapter_first_draft_prompts,
    build_scene_draft_prompts,
)
from bestseller.services.golden_rules import render_golden_three_rules

pytestmark = pytest.mark.unit


def _project(language: str) -> ProjectModel:
    project = ProjectModel(
        slug=f"prompt-uniqueness-{language.lower()}",
        title="Prompt Uniqueness",
        genre="fantasy",
        language=language,
        target_word_count=60_000,
        target_chapters=30,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def _chapter(project_id: object, chapter_number: int = 1) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=chapter_number,
        title="The First Signal",
        chapter_goal="Force the protagonist to make a visible choice.",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=2_600,
    )
    chapter.id = uuid4()
    return chapter


def _scene(
    project_id: object,
    chapter_id: object,
    *,
    scene_number: int = 1,
) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=scene_number,
        scene_type="setup",
        title="The sealed gate",
        time_label="now",
        participants=["Ari"],
        purpose={"story": "reveal the signal", "emotion": "pressure"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=900,
    )
    scene.id = uuid4()
    return scene


def _context_packet() -> SimpleNamespace:
    return SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )


def _assembled_prompt(path_mode: str, language: str, chapter_number: int = 1) -> str:
    project = _project(language)
    chapter = _chapter(project.id, chapter_number)
    scene = _scene(project.id, chapter.id)
    if path_mode == "scene":
        system, user = build_scene_draft_prompts(project, chapter, scene, None)
    else:
        system, user = build_chapter_first_draft_prompts(
            project,
            chapter,
            [scene],
            None,
            _context_packet(),
            target_word_count=2_600,
        )
    return f"{system}\n{user}"


class TestSlopBlacklistSingularity:
    """The slop blacklist block should render once and be self-contained."""

    def test_zh_render_contains_all_phrases(self):
        block = render_slop_blacklist_block("zh-CN")
        for phrase in ZH_SLOP_PHRASES:
            assert phrase in block, f"Missing phrase: {phrase}"

    def test_en_render_contains_all_phrases(self):
        block = render_slop_blacklist_block("en")
        for phrase in EN_SLOP_PHRASES:
            assert phrase in block, f"Missing phrase: {phrase}"

    def test_zh_block_has_header(self):
        block = render_slop_blacklist_block("zh-CN")
        assert "AI套话黑名单" in block

    def test_en_block_has_header(self):
        block = render_slop_blacklist_block("en")
        assert "BANNED AI CLICH" in block

    def test_post_generation_phrase_set_covers_all_prompt_blacklist_categories(self):
        expected = {
            *ZH_SLOP_PHRASES,
            *ZH_SLOP_OPENERS,
            *ZH_SLOP_ENDINGS,
            *EN_SLOP_PHRASES,
            *EN_SLOP_OPENERS,
            *EN_SLOP_ENDINGS,
        }
        assert expected <= set(ALL_SLOP_PHRASES)

    @pytest.mark.parametrize(
        ("language", "phrases"),
        [
            ("zh-CN", (*ZH_SLOP_PHRASES, *ZH_SLOP_OPENERS, *ZH_SLOP_ENDINGS)),
            ("en", (*EN_SLOP_PHRASES, *EN_SLOP_OPENERS, *EN_SLOP_ENDINGS)),
        ],
    )
    def test_every_canonical_blacklist_phrase_is_injected_at_most_once(
        self,
        language: str,
        phrases: tuple[str, ...],
    ) -> None:
        for path_mode in ("scene", "chapter_first"):
            prompt = _assembled_prompt(path_mode, language)
            for phrase in phrases:
                assert prompt.count(phrase) <= 1, (
                    f"duplicate {phrase!r} in {path_mode}/{language}"
                )

    @pytest.mark.parametrize("path_mode", ["scene", "chapter_first"])
    @pytest.mark.parametrize(
        ("language", "expected_header", "other_header", "representative_phrase"),
        [
            ("zh-CN", "AI套话黑名单", "BANNED AI CLICH", "血液仿佛凝固了"),
            ("en", "BANNED AI CLICH", "AI套话黑名单", "blood crystallized"),
        ],
    )
    def test_assembled_writer_prompt_injects_exactly_one_language_correct_blacklist(
        self,
        path_mode: str,
        language: str,
        expected_header: str,
        other_header: str,
        representative_phrase: str,
    ) -> None:
        prompt = _assembled_prompt(path_mode, language)

        assert prompt.count(expected_header) == 1
        assert other_header not in prompt
        assert prompt.count(representative_phrase) == 1

    @pytest.mark.parametrize(
        ("path_mode", "language", "golden_marker", "front_ten_marker"),
        [
            ("scene", "zh-CN", "# OUTPUT FORMAT · 开篇硬指标", "【前十章留存硬规则】"),
            (
                "chapter_first",
                "zh-CN",
                "【黄金三章·开篇硬契约】",
                "【前十章留存硬规则】",
            ),
            ("scene", "en", "# OPENING METRICS", "[FRONT-TEN RETENTION RULES]"),
            (
                "chapter_first",
                "en",
                "[GOLDEN THREE CHAPTERS — OPENING HARD CONTRACT]",
                "[FRONT-TEN RETENTION RULES]",
            ),
        ],
    )
    def test_assembled_writer_prompt_respects_opening_rule_ranges(
        self,
        path_mode: str,
        language: str,
        golden_marker: str,
        front_ten_marker: str,
    ) -> None:
        chapter_one = _assembled_prompt(path_mode, language, 1)
        chapter_four = _assembled_prompt(path_mode, language, 4)
        chapter_eleven = _assembled_prompt(path_mode, language, 11)

        assert golden_marker in chapter_one
        assert front_ten_marker not in chapter_one
        assert front_ten_marker in chapter_four
        assert golden_marker not in chapter_four
        assert golden_marker not in chapter_eleven
        assert front_ten_marker not in chapter_eleven

        if path_mode == "chapter_first":
            assert "章节开篇硬指标" not in chapter_eleven
            assert "Chapter-opening hard indicators" not in chapter_eleven


class TestGoldenRulesConsistency:
    """Golden rules should be consistent across path modes."""

    def test_scene_mode_is_compact(self):
        rules = render_golden_three_rules(1, "zh-CN", path_mode="scene")
        assert "开篇硬指标" in rules
        assert "主角想要什么、怕失去什么、为何不能离开" in rules
        assert "爽点铺设可见条件" in rules
        assert "第一步结果" in rules

    def test_chapter_first_mode_is_full(self):
        rules = render_golden_three_rules(1, "zh-CN", path_mode="chapter_first")
        assert "黄金三章" in rules
        assert "主角目标与利害" in rules
        assert "旁人反应或结果变化确认" in rules
        assert "赢了什么、付出了什么" in rules

    def test_front_ten_for_chapter_4(self):
        rules = render_golden_three_rules(5, "zh-CN")
        assert "前十章" in rules

    def test_empty_for_chapter_11_plus(self):
        """Chapters 11+ should get empty string (no special opening rules)."""
        assert render_golden_three_rules(11, "zh-CN") == ""

    def test_en_mode_works(self):
        rules = render_golden_three_rules(1, "en", path_mode="chapter_first")
        assert "GOLDEN THREE" in rules

    @pytest.mark.parametrize("chapter_number", [1, 4, 11])
    @pytest.mark.parametrize("language", ["zh-CN", "en"])
    def test_scene_opening_rules_only_reach_first_scene(
        self,
        chapter_number: int,
        language: str,
    ) -> None:
        project = _project(language)
        chapter = _chapter(project.id, chapter_number)
        first_scene = _scene(project.id, chapter.id, scene_number=1)
        later_scene = _scene(project.id, chapter.id, scene_number=2)

        first_system, first_user = build_scene_draft_prompts(
            project, chapter, first_scene, None
        )
        later_system, later_user = build_scene_draft_prompts(
            project, chapter, later_scene, None
        )
        expected = render_golden_three_rules(
            chapter_number, language, path_mode="scene"
        )

        if expected:
            assert expected in f"{first_system}\n{first_user}"
            assert expected not in f"{later_system}\n{later_user}"
        assert "【开篇规则（黄金三章）】" not in f"{first_system}\n{first_user}"
        assert "【开篇规则（黄金三章）】" not in f"{later_system}\n{later_user}"

    @pytest.mark.parametrize(
        ("language", "golden_marker", "blacklist_marker"),
        [
            ("zh-CN", "【黄金三章·开篇硬契约】", "AI套话黑名单"),
            ("en", "[GOLDEN THREE CHAPTERS — OPENING HARD CONTRACT]", "BANNED AI CLICH"),
        ],
    )
    def test_chapter_first_hard_rules_survive_tight_budget(
        self,
        language: str,
        golden_marker: str,
        blacklist_marker: str,
    ) -> None:
        project = _project(language)
        chapter = _chapter(project.id, 1)
        scene = _scene(project.id, chapter.id)
        packet = _context_packet()
        packet.retrieval_chunks = ["long context " * 1200]

        system, user = build_chapter_first_draft_prompts(
            project,
            chapter,
            [scene],
            None,
            packet,
            target_word_count=2_600,
            context_budget_tokens=1_000,
        )
        prompt = f"{system}\n{user}"

        assert prompt.count(golden_marker) == 1
        assert prompt.count(blacklist_marker) == 1


class TestNoNovelOutputProhibitionDuplicates:
    """Verify that _NOVEL_OUTPUT_PROHIBITION no longer contains slop phrases.

    After Phase 1 dedup, the specific slop phrase list was removed from
    _NOVEL_OUTPUT_PROHIBITION (both ZH and EN). The phrases now live only
    in ai_slop_blacklist.py and are injected once via
    render_slop_blacklist_block().
    """

    def test_zh_prohibition_has_no_slop_list(self):
        from bestseller.services.drafts import _NOVEL_OUTPUT_PROHIBITION

        # The prohibition block should NOT contain the dedicated slop section
        assert "AI套话黑名单" not in _NOVEL_OUTPUT_PROHIBITION
        # But it should still contain other prohibition rules
        assert "策划信息" in _NOVEL_OUTPUT_PROHIBITION

    def test_en_prohibition_has_no_slop_list(self):
        from bestseller.services.drafts import _NOVEL_OUTPUT_PROHIBITION_EN

        assert "BANNED AI CLICH" not in _NOVEL_OUTPUT_PROHIBITION_EN
        assert "FORBIDDEN OUTPUT" in _NOVEL_OUTPUT_PROHIBITION_EN

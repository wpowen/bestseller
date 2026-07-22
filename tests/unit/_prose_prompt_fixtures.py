"""Minimal fixtures that render the real prose system prompts.

Shared by the 反AI腔 discipline guards so each prose path is exercised through
its *actual* prompt builder rather than a stand-in.
"""

from __future__ import annotations

from types import SimpleNamespace


def _project(language: str = "zh-CN") -> SimpleNamespace:
    return SimpleNamespace(
        title="吞神证我",
        genre="仙侠",
        sub_genre=None,
        language=language,
        metadata_json={},
    )


def _chapter() -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=8,
        title="血枭",
        chapter_goal="裴铸夺刀反制陈七。",
        target_word_count=2400,
    )


def _rewrite_task() -> SimpleNamespace:
    return SimpleNamespace(
        instructions="补足尾钩与冲突代价。",
        rewrite_strategy="scene_quality_rewrite",
        metadata_json={},
    )


def build_scene_rewrite_system_prompt(language: str = "zh-CN") -> str:
    from bestseller.services.reviews import build_scene_rewrite_prompts

    scene = SimpleNamespace(
        scene_number=1,
        title="夺刀",
        purpose={"story": "夺刀", "emotion": "压迫感"},
        target_word_count=1200,
    )
    system_prompt, _ = build_scene_rewrite_prompts(
        _project(language),
        _chapter(),
        scene,
        SimpleNamespace(content_md="旧稿。", word_count=900),
        _rewrite_task(),
        SimpleNamespace(pov_type="third-limited", tone_keywords=["紧张"]),
    )
    return system_prompt


def build_chapter_rewrite_system_prompt(language: str = "zh-CN") -> str:
    from bestseller.services.reviews import build_chapter_rewrite_prompts

    system_prompt, _ = build_chapter_rewrite_prompts(
        _project(language),
        _chapter(),
        SimpleNamespace(content_md="旧稿。", word_count=2000),
        _rewrite_task(),
        None,
    )
    return system_prompt


def build_chapter_first_system_prompt(
    language: str = "zh-CN",
    *,
    chapter_number: int = 8,
) -> str:
    """Render the real chapter-first writer system prompt.

    This is the path that ships whole chapters under ``chapter_hybrid``; it is
    also the path the 反AI腔 guards originally missed, because it carried a
    hand-rolled copy of the rules instead of importing the single source.
    """

    from uuid import uuid4

    from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
    from bestseller.services.drafts import build_chapter_first_draft_prompts

    project = ProjectModel(
        slug=f"chapter-first-discipline-{language.lower()}-{chapter_number}",
        title="吞神证我",
        genre="仙侠",
        language=language,
        target_word_count=60_000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=chapter_number,
        title="血枭",
        chapter_goal="裴铸夺刀反制陈七。",
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
        title="夺刀",
        time_label="now",
        participants=["裴铸", "陈七"],
        purpose={"story": "夺刀", "emotion": "压迫感"},
        entry_state={"blade": "held"},
        exit_state={"blade": "taken"},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=2_600,
    )
    scene.id = uuid4()
    packet = SimpleNamespace(
        chapter_contract={"closing_hook": "刀锋转向", "core_conflict": "夺刀"},
        hard_fact_snapshot={"facts": []},
        chapter_length_block="正文必须在1800到3500字。",
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
        participant_knowledge_states=[],
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )
    system_prompt, _ = build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2_600,
    )
    return system_prompt

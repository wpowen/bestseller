from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services.drafts import (
    _compile_rendered_writer_prompt,
    build_scene_draft_prompts,
)
from bestseller.services.prompt_compiler import (
    CompiledPrompt,
    PromptBlock,
    PromptBudgetError,
)

pytestmark = pytest.mark.unit


_CASES = [
    ("zh-CN", 1, 1),
    ("zh-CN", 2, 1),
    ("zh-CN", 3, 2),
    ("zh-CN", 4, 1),
    ("zh-CN", 7, 2),
    ("zh-CN", 10, 1),
    ("zh-CN", 11, 2),
    ("zh-CN", 50, 1),
    ("en", 1, 1),
    ("en", 2, 2),
    ("en", 3, 1),
    ("en", 4, 2),
    ("en", 10, 1),
    ("en", 11, 2),
    ("en", 50, 1),
]


def _models(language: str, chapter_number: int, scene_number: int):
    project = ProjectModel(
        slug=f"compiled-scene-{language.lower()}-{chapter_number}-{scene_number}",
        title="编译器长上下文测试" if language.startswith("zh") else "Compiler Long Context Test",
        genre="fantasy",
        language=language,
        target_word_count=60_000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=chapter_number,
        title="封锁线" if language.startswith("zh") else "The Cordon",
        chapter_goal="让主角在倒计时结束前作出不可逆选择。",
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
        scene_number=scene_number,
        scene_type="confrontation",
        title="门外的倒计时",
        time_label="now",
        participants=["林砚", "周禾"],
        purpose={"story": "force a choice", "emotion": "compress tension"},
        entry_state={"door": "sealed"},
        exit_state={"choice": "made"},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=900,
    )
    scene.id = uuid4()
    return project, chapter, scene


def _creative_core(chapter_number: int) -> dict[str, object]:
    return {
        "engine_version": 2,
        "chapter_number": chapter_number,
        "choice_id": "publish",
        "pre_state": {"pressure": 3},
        "pre_state_hash": "pre-hash",
        "known_facts": ["档案室今晚封存"],
        "pressure": "对手正在销毁证据",
        "options": [
            {"choice_id": "publish", "label": "立即公开"},
            {"choice_id": "hide", "label": "暂时隐藏"},
        ],
        "chosen_path": "立即公开并保护证人",
        "alternative_costs": ["隐藏会失去最后窗口"],
        "opponent_strategy": "冻结权限并追查证人",
        "due_obligations": ["保护证人"],
        "required_state_changes": [{"key": "pressure", "after": 4}],
        "expected_post_state_hash": "post-hash",
        "can_drive_generation": True,
    }


@pytest.mark.parametrize(("language", "chapter_number", "scene_number"), _CASES)
def test_compiled_scene_prompt_matrix_stays_complete_unique_and_within_budget(
    language: str,
    chapter_number: int,
    scene_number: int,
) -> None:
    project, chapter, scene = _models(language, chapter_number, scene_number)
    long_context = "持续变化的现场证据 " * 2_000

    compiled = build_scene_draft_prompts(
        project,
        chapter,
        scene,
        None,
        story_bible_context={"world": long_context},
        retrieval_context=[{"text": long_context, "score": 0.99}],
        recent_scene_summaries=[{"summary": long_context}],
        context_budget_tokens=100_000,
        prompt_mode="compiled",
        total_input_budget_tokens=8_000,
        prompt_safety_margin=0.10,
    )

    assert isinstance(compiled, CompiledPrompt)
    assert compiled.report.total_tokens <= compiled.report.usable_budget_tokens <= 8_000
    assert compiled.report.required_complete is True
    assert compiled.report.duplicates == ()
    assert compiled.report.conflicts == ()
    optional_outcomes = (*compiled.report.dropped, *compiled.report.truncated)
    assert any(
        marker in key
        for key in optional_outcomes
        for marker in ("story_bible", "retrieval", "recent")
    )
    assert "scene.system_contract" in compiled.report.required_blocks_kept
    assert "scene.primary_task" in compiled.report.required_blocks_kept
    blacklist = "AI套话黑名单" if language.startswith("zh") else "BANNED AI CLICH"
    assert blacklist in compiled.system


def test_scene_compiler_dedupes_an_injected_semantic_blacklist_family() -> None:
    project, chapter, scene = _models("zh-CN", 1, 1)
    duplicate = PromptBlock(
        key="test.duplicate_blacklist",
        channel="system",
        layer="craft",
        authority=1,
        instruction_family="writer.output.blacklist",
        source="test",
        text="另一份较弱黑名单。",
    )

    compiled = build_scene_draft_prompts(
        project,
        chapter,
        scene,
        None,
        prompt_mode="compiled",
        compiler_additional_blocks=(duplicate,),
    )

    assert isinstance(compiled, CompiledPrompt)
    assert "另一份较弱黑名单" not in compiled.system
    assert compiled.report.duplicates == ("test.duplicate_blacklist",)
    assert compiled.report.conflicts == ()


def test_compiled_scene_prompt_keeps_story_engine_creative_core_as_required() -> None:
    project, chapter, scene = _models("zh-CN", 4, 1)

    compiled = build_scene_draft_prompts(
        project,
        chapter,
        scene,
        None,
        creative_core=_creative_core(chapter.chapter_number),
        prompt_mode="compiled",
    )

    assert isinstance(compiled, CompiledPrompt)
    assert "scene.section.creative_core_line" in compiled.report.required_blocks_kept
    assert compiled.user.count("【StoryEngine 本章创意核心") == 1
    assert "立即公开并保护证人" in compiled.user


def test_compiler_keeps_every_hard_canon_section_and_fails_closed_if_they_do_not_fit() -> None:
    hard_fact = "硬事实：林砚从未见过门后的人。"
    timeline = "时间线：今晚十一点前必须离开封锁区。"
    compiled = _compile_rendered_writer_prompt(
        path="scene",
        system_prompt="# ROLE\nWrite the scene.",
        user_prompt=f"任务：推进冲突。\n{hard_fact}\n{timeline}",
        section_texts={
            "hard_fact_line": hard_fact,
            "timeline_canon_line": timeline,
        },
        total_input_budget_tokens=1_000,
        prompt_safety_margin=0.10,
    )

    assert {
        "scene.section.hard_fact_line",
        "scene.section.timeline_canon_line",
    } <= set(compiled.report.required_blocks_kept)

    with pytest.raises(PromptBudgetError):
        _compile_rendered_writer_prompt(
            path="scene",
            system_prompt="# ROLE\nWrite the scene.",
            user_prompt=f"任务：推进冲突。\n{hard_fact}\n{timeline}",
            section_texts={
                "hard_fact_line": hard_fact,
                "timeline_canon_line": timeline,
            },
            total_input_budget_tokens=16,
            prompt_safety_margin=0.10,
        )


def test_compiler_removes_all_duplicate_known_sections_before_typed_reinsertion() -> None:
    blacklist = "不得使用万能比喻。"
    compiled = _compile_rendered_writer_prompt(
        path="scene",
        system_prompt="# ROLE\nWrite the scene.",
        user_prompt=f"任务：推进冲突。\n{blacklist}\n{blacklist}",
        section_texts={"slop_blacklist": blacklist},
        total_input_budget_tokens=1_000,
        prompt_safety_margin=0.10,
    )

    assert compiled.user.count(blacklist) == 1
    assert "scene.section.slop_blacklist" in compiled.report.required_blocks_kept

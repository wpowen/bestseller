from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services.drafts import build_chapter_first_draft_prompts
from bestseller.services.prompt_compiler import CompiledPrompt

pytestmark = pytest.mark.unit


_CASES = [
    ("zh-CN", 1),
    ("zh-CN", 2),
    ("zh-CN", 3),
    ("zh-CN", 4),
    ("zh-CN", 7),
    ("zh-CN", 10),
    ("zh-CN", 11),
    ("zh-CN", 50),
    ("en", 1),
    ("en", 2),
    ("en", 3),
    ("en", 4),
    ("en", 10),
    ("en", 11),
    ("en", 50),
]


def _inputs(language: str, chapter_number: int):
    project = ProjectModel(
        slug=f"compiled-chapter-{language.lower()}-{chapter_number}",
        title="整章编译器测试" if language.startswith("zh") else "Compiled Chapter Test",
        genre="mystery",
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
        scene_number=1,
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
        target_word_count=2_600,
    )
    scene.id = uuid4()
    long_context = "持续变化的现场证据 " * 2_000
    packet = SimpleNamespace(
        chapter_contract={"closing_hook": "门锁从里面转动", "core_conflict": "倒计时"},
        hard_fact_snapshot={"facts": [long_context]},
        chapter_length_block="正文必须在1800到3500字。",
        timeline_canon_block="只允许使用今晚十一点这个时间锚点。",
        character_role_block="林砚不能预知门后的人。",
        dialogue_voice_block="林砚短句，周禾反问。",
        scene_coherence_block="地点变化必须有可见转场。",
        canon_guardrails_block="不得新增角色名。",
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        participant_knowledge_states=[],
        story_bible={"world": long_context},
        previous_scene_summaries=[{"summary": long_context}],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[{"text": long_context}],
    )
    return project, chapter, scene, packet


@pytest.mark.parametrize(("language", "chapter_number"), _CASES)
def test_compiled_chapter_first_matrix_stays_complete_unique_and_within_budget(
    language: str,
    chapter_number: int,
) -> None:
    project, chapter, scene, packet = _inputs(language, chapter_number)

    compiled = build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2_600,
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
    assert "chapter_first.system_contract" in compiled.report.required_blocks_kept
    assert "chapter_first.primary_task" in compiled.report.required_blocks_kept
    blacklist = "AI套话黑名单" if language.startswith("zh") else "BANNED AI CLICH"
    assert blacklist in compiled.user

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


@pytest.mark.parametrize("prose_prompt_profile", ["full", "lean"])
@pytest.mark.parametrize(("language", "chapter_number"), _CASES)
def test_compiled_chapter_first_matrix_stays_complete_unique_and_within_budget(
    language: str,
    chapter_number: int,
    prose_prompt_profile: str,
) -> None:
    """Compiler integrity must hold for BOTH shipping profiles.

    ``lean`` became the default on 2026-07-24 and ``full`` remains reachable
    per-book via ``metadata["prose_prompt_profile"]``, so budget/uniqueness/
    required-block guarantees have to be proven on each. Leaving the profile
    implicit meant this matrix silently only ever covered whichever one was
    currently the default.
    """

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
        prose_prompt_profile=prose_prompt_profile,
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

    # The blacklist is a full-profile block: lean routes the concern to the
    # post-generation deslop pass instead (plan §4.3), because listing banned
    # diction primes it (50-round arena, 2026-07-18).
    blacklist = "AI套话黑名单" if language.startswith("zh") else "BANNED AI CLICH"
    if prose_prompt_profile == "full":
        assert blacklist in compiled.user
    else:
        assert blacklist not in compiled.user


def test_chapter_writer_receives_explicit_protagonist_decision_block() -> None:
    project, chapter, scene, packet = _inputs("zh-CN", 1)
    chapter.metadata_json = {
        "methodology_contract": {
            "decision_protocol": {
                "viewpoint_character": "林砚",
                "known_facts": ["门锁倒计时已经启动", "周禾还在封锁线内"],
                "unknowns": ["门后是谁"],
                "immediate_goal": "先让周禾撤离，再验证门锁。",
                "options_considered": ["直接撞门", "撤离", "先断电试锁"],
                "obvious_safe_option": "立刻撤离并等待支援。",
                "chosen_action": "先让周禾撤到楼梯口，林砚断电试锁。",
                "why_not_safer_option": "门锁会在支援抵达前完成反锁，楼内还有被困者。",
                "personality_basis": "林砚谨慎且重视同伴安全。",
                "risk_control": "周禾在楼梯口拉保险绳，试锁失败立刻撤。",
                "first_person_reasoning": "我先把人送出去，再用能撤回的一步验证门锁。",
            }
        }
    }

    _, user = build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2_600,
    )

    assert "【主角决策落地·不得把本清单写进正文】" in user
    assert "显然更安全的选项：立刻撤离并等待支援" in user
    assert "止损/退路/后手：周禾在楼梯口拉保险绳" in user


def test_chapter_writer_gets_one_weak_scene_map_without_scene_prose_duplication() -> None:
    project, chapter, scene, packet = _inputs("zh-CN", 5)
    scene.key_dialogue_beats = [
        "周禾盯着门锁说，这扇门今天谁先碰，谁就会被留在里面。"
    ]
    scene.sensory_anchors = {"sound": "锁芯摩擦声像细针一样扎进耳朵"}
    scene.metadata_json = {
        "methodology_contract": {
            "action_sequence": "林砚断电，周禾后退，门锁从里面转动。"
        }
    }

    _, user = build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2_600,
    )

    assert user.count("【弱场景逻辑地图】") == 1
    assert "【统一生成输入包】" not in user
    assert "谁就会被留在里面" not in user
    assert "锁芯摩擦声像细针" not in user
    assert "林砚断电，周禾后退" not in user
    assert "弱场景地图只约束顺序与状态变化" in user

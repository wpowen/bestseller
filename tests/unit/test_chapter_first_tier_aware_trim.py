# ruff: noqa: RUF001
"""T3 验收: chapter-first 分层预算器（替代盲切尾）."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services import drafts as draft_services


def _project() -> ProjectModel:
    project = ProjectModel(
        slug="chapter-first-trim",
        title="Chapter First Trim",
        genre="悬疑",
        target_word_count=60_000,
        target_chapters=30,
        language="zh-CN",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def _chapter(project_id) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=3,
        title="病历背面",
        chapter_goal="承接上一章规则医院异常，并兑现本章证据钩子。",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=2400,
    )
    chapter.id = uuid4()
    return chapter


def _scene(project_id, chapter_id) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=1,
        scene_type="hook",
        title="病历背面",
        time_label="凌晨三点",
        participants=["沈砚"],
        purpose={"story": "发现病历背面第二行字", "emotion": "被迫确认代价"},
        entry_state={"state": "沈砚站在护士站前"},
        exit_state={"state": "病历背面出现他自己的签名"},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        hook_requirement="病历背面浮出第二行字。",
        target_word_count=800,
    )
    scene.id = uuid4()
    return scene


def _context_packet(chapter_contract: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_contract=chapter_contract,
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
        participant_knowledge_states=[],
        story_bible={"low_priority_bloat": "LOW_PRIORITY_BLOAT" * 800},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=["LOW_PRIORITY_RETRIEVAL" * 500],
    )


def test_trim_preserves_chapter_closing_hook_zh():
    """_soft_trim_user_prompt 必须保留【章末收尾钩子】块。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    # Build a user_prompt > budget, with protected tail marker
    body = "A" * 100  # filler
    middle = "B" * 5000  # bloated middle
    protected = "【章末收尾钩子】\n主角推开木门，外面是灰白的天空。"
    user_prompt = body + middle + protected
    char_budget = 2000
    trimmed = _soft_trim_user_prompt(user_prompt, char_budget=char_budget, language="zh-CN")
    assert "【章末收尾钩子】" in trimmed, "must preserve closing hook marker"
    assert "灰白的天空" in trimmed, "must preserve closing hook content"


def test_trim_preserves_methodology_evidence_zh():
    """_soft_trim_user_prompt 必须保留【方法论证据】块。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    middle = "X" * 5000
    protected = "【方法论证据】\n本章兑现 payoff p1 在 scene 3。"
    user_prompt = "head" + middle + protected
    char_budget = 2000
    trimmed = _soft_trim_user_prompt(user_prompt, char_budget=char_budget, language="zh-CN")
    assert "【方法论证据】" in trimmed
    assert "payoff p1" in trimmed


def test_trim_preserves_closing_hook_en():
    """英文 [chapter closing hook] 块也必须保留。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    middle = "X" * 5000
    protected = "[chapter closing hook]\nProtagonist pushes open the door."
    user_prompt = "head" + middle + protected
    trimmed = _soft_trim_user_prompt(user_prompt, char_budget=2000, language="en-US")
    assert "[chapter closing hook]" in trimmed
    assert "Protagonist pushes open" in trimmed


def test_trim_falls_back_to_head_only_when_no_marker():
    """无 protected marker 时退化为头部截断（保留原行为）。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    user_prompt = "A" * 5000  # no marker
    trimmed = _soft_trim_user_prompt(user_prompt, char_budget=1000, language="zh-CN")
    # Head only - first 1000 chars preserved
    assert trimmed.startswith("A" * 100)
    assert "已截断" in trimmed or "trimmed" in trimmed


def test_trim_no_op_when_under_budget():
    """prompt 短于 budget 时原样返回。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    user_prompt = "Short prompt. 【章末收尾钩子】ok"
    trimmed = _soft_trim_user_prompt(user_prompt, char_budget=10000, language="zh-CN")
    assert trimmed == user_prompt


def test_trim_preserves_tail_when_protected_too_long():
    """protected tail 本身比 budget 长时：保留 tail，截断 head。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    head = "Filler text " * 5000  # very long head
    protected = "【章末收尾钩子】" + "long " * 2000  # protected tail is also long
    user_prompt = head + protected
    char_budget = 1500
    trimmed = _soft_trim_user_prompt(user_prompt, char_budget=char_budget, language="zh-CN")
    assert "【章末收尾钩子】" in trimmed
    # Marker should mention "开头已截断" (head trimmed) since protected was long
    assert "开头已截断" in trimmed or "head trimmed" in trimmed


def test_trim_preserves_marker_when_marker_starts_inside_budget():
    """marker 在预算内但正文超预算时，也不能丢掉 marker 后面的必保区。"""
    from bestseller.services.drafts import _soft_trim_user_prompt

    head = "H" * 200
    protected = "【章末收尾钩子】\n病历背面浮出第二行字。"
    suffix = "S" * 3000
    trimmed = _soft_trim_user_prompt(
        head + protected + suffix,
        char_budget=1000,
        language="zh-CN",
    )

    assert "【章末收尾钩子】" in trimmed
    assert "病历背面浮出第二行字" in trimmed


def test_chapter_first_prompt_produces_and_preserves_real_must_keep_markers():
    """从真实 chapter-first 组装入口进入，验证生产会产出并保留必保 marker。"""
    project = _project()
    chapter = _chapter(project.id)
    scene = _scene(project.id, chapter.id)
    context_packet = _context_packet(
        {
            "contract_summary": "病历规则进入下一层。",
            "core_conflict": "沈砚必须在签名前找出被替换的病历页。",
            "information_release": "病历背面有第二行字。",
            "closing_hook": "病历背面浮出第二行字：下一位签名人是沈砚。",
            "methodology_declared_payoffs": ["payoff_rule_signature"],
            "payoff_evidence_paths": [
                {"scene_number": "1", "evidence": "病历背面浮出第二行字"}
            ],
            "hooks_to_resolve": ["hospital_rule_backside"],
            "hooks_to_plant": ["shenyan_signature_next"],
        }
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
        context_budget_tokens=360,
        # Explicitly full: this test is about the TRIMMER keeping must-keep
        # markers under a tight budget, which presupposes the blocks are
        # emitted. lean does not emit them (see the lean test below).
        prose_prompt_profile="full",
    )

    assert "【章末收尾钩子】" in user_prompt
    assert "下一位签名人是沈砚" in user_prompt
    assert "【方法论证据】" in user_prompt
    assert "payoff_rule_signature" in user_prompt
    assert "LOW_PRIORITY_BLOAT" not in user_prompt


def test_lean_profile_currently_omits_the_closing_hook_entirely():
    """Documents a gap between plan §4.3 and the lean implementation.

    §4.3 prescribes "章末钩子 verbatim 长文 | 缩成 Beats 一行" — compress the
    hook to a single line, NOT drop it. Today lean removes the block and its
    content outright, so the writer never learns which hook the chapter is
    contracted to land on and invents its own.

    That is not automatically wrong: ENDING_HOOK_MISSING / HOOK_ECHO_MISSING /
    HOOK_ECHO_LOW are auto-repairable, so a mismatch is caught after assembly.
    But it converts a free instruction into a paid repair round, and no blind
    test has yet measured which is better. This test pins CURRENT behavior so
    the choice is explicit and shows up the moment someone changes it —
    changing it should be driven by the A6 blind comparison, not by assumption.
    """

    project = _project()
    chapter = _chapter(project.id)
    scene = _scene(project.id, chapter.id)
    context_packet = _context_packet(
        {"closing_hook": "病历背面浮出第二行字：下一位签名人是沈砚。"}
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
        context_budget_tokens=360,
        prose_prompt_profile="lean",
    )

    assert "【章末收尾钩子】" not in user_prompt
    assert "下一位签名人是沈砚" not in user_prompt

    from bestseller.settings import PipelineSettings

    repairable = set(PipelineSettings().chapter_auto_repair_repairable_codes)
    assert {"ENDING_HOOK_MISSING", "HOOK_ECHO_MISSING", "HOOK_ECHO_LOW"} <= repairable

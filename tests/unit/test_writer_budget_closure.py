"""W2 预算闭合回归：比较**具体事实与数值**，而不是只断言标题存在。

对应 FRAMEWORK_QUALITY_FIX_PLAN.md 的 W2 回归清单：
  * 任务头被挤出
  * 保护尾段超过总预算
  * 某事实仅在被删段出现
  * 硬事实相互冲突
  * 无前章的第一章 / 无适用伏笔 / 正常第二章
  * repair 与 compiled 不二次裁剪
  * 最终输入预算可解释且失败信息可见

真实证据（不是构造样本）：
  reports/framework-repair-2026-09-08/chapter2-current-input.json 重放后，
  【活动主线/伏笔/回收】与【时间线与硬事实快照】被上限淘汰，而压缩标记原文却说
  "规划层冗余内容已移除"。这两段没有第二来源，丢掉它们等于写手接不住上一章。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services.drafts import (
    _budget_context_sections,
    _soft_trim_user_prompt,
    _soft_trim_user_prompt_with_report,
    build_chapter_first_draft_prompts,
)
from bestseller.services.prompt_assembly import build_prompt_assembly_report
from bestseller.services.prompt_compactor import (
    CompactionReport,
    _cap_prompt,
    _section_has_body,
    compact_user_prompt,
)
from bestseller.services.prompt_compiler import (
    CompiledPrompt,
    PromptBlock,
    PromptConflictError,
    compile_prompt,
)

pytestmark = pytest.mark.unit


# ── 上限淘汰：连贯性/硬事实损失必须可诊断，且标记不许说错话 ────────────────


def _sections(*pairs: tuple[str, str]) -> str:
    """拼装真实形状的平铺 prompt。

    段边界由 ``\\n【`` 界定（见 prompt_compactor._SECTION_BOUNDARY），所以除首段外
    每段都必须以换行开头——否则整份 prompt 会被当成一个 15k 字符的大段。
    """

    return "".join(
        f"{'' if index == 0 else chr(10)}【{label}】{chr(10)}{body}"
        for index, (label, body) in enumerate(pairs)
    )


def test_cap_reports_continuity_loss_and_marker_stops_calling_it_redundant() -> None:
    """某事实只在被删段出现 → 必须被点名，且标记不能自称"清理了冗余"。"""

    raw = _sections(
        ("任务", "一次性写完整章。"),
        ("卷级首章埋钩硬约束", "P" * 2_200),
        ("章节契约", "CONTRACT_ONLY_1a2b" + "C" * 3_200),
        ("近期章节/场景摘要", "RECENT_ONLY_5c6d" + "R" * 1_700),
        ("活动主线/伏笔/回收", "FORESHADOW_ONLY_9f3a" + "F" * 3_600),
        ("时间线与硬事实快照", "TIMELINE_ONLY_7c21" + "T" * 3_400),
        ("检索补充", "Q" * 800),
        ("删减策略", "只写正文。"),
    )

    compacted, report = compact_user_prompt(raw, chapter_no=12, forbidden_terms_full=[])

    # 1) 具体事实：被删段的独有事实消失，保留段的独有事实还在。
    assert "FORESHADOW_ONLY_9f3a" not in compacted
    assert "TIMELINE_ONLY_7c21" not in compacted
    assert "CONTRACT_ONLY_1a2b" in compacted
    assert "RECENT_ONLY_5c6d" in compacted

    # 2) 结构化可诊断：点名两个没有第二来源的连贯性/硬事实段。
    assert "【活动主线/伏笔/回收】" in report.continuity_evicted_sections
    assert "【时间线与硬事实快照】" in report.continuity_evicted_sections
    assert report.budget_conflict is True

    # 3) 标记必须如实：不许把丢硬事实说成"清理冗余"。
    assert "规划层冗余内容已移除" not in compacted
    assert "预算冲突" in compacted
    assert "活动主线/伏笔/回收" in compacted  # 点名的正是被删的那两段


def test_cap_does_not_cry_conflict_for_sections_that_have_another_copy() -> None:
    """正常第二章：只淘汰有第二来源的段时，不许误报预算冲突。"""

    raw = _sections(
        ("任务", "写第二章。"),
        ("章节契约", "CONTRACT_ONLY_1a2b" + "C" * 5_600),
        ("近期章节/场景摘要", "RECENT_ONLY_5c6d" + "R" * 3_200),
        ("故事圣经上下文", "BIBLE_ONLY_3e8f" + "S" * 3_600),
        ("检索补充", "RETRIEVAL_ONLY_2b7c" + "Q" * 1_500),
        ("删减策略", "只写正文。"),
    )

    compacted, report = compact_user_prompt(raw, chapter_no=2, forbidden_terms_full=[])

    # 故事圣经在 pack/system 里另有一份，淘汰它不构成事实损失。
    assert "BIBLE_ONLY_3e8f" not in compacted
    assert "RETRIEVAL_ONLY_2b7c" not in compacted
    assert report.continuity_evicted_sections == ()
    assert report.budget_conflict is False
    # 真的只是冗余规划层 → 保留原来的措辞才是诚实的。
    assert "规划层冗余内容已移除" in compacted
    # 本章该写什么仍在：契约与近期摘要是具体事实，不是标题。
    assert "CONTRACT_ONLY_1a2b" in compacted
    assert "RECENT_ONLY_5c6d" in compacted
    assert "【任务】" in compacted


def test_empty_protected_header_is_not_counted_as_a_retained_fact() -> None:
    """保护名单命中的空标题不能算"已保留的事实"（计划 §2 更正）。"""

    # 直接钉住判定函数：标题 + 空体 ≠ 有内容。
    assert not _section_has_body("【硬约束与门禁】")
    assert not _section_has_body("【硬约束与门禁】\n")
    assert not _section_has_body("【硬约束与门禁】\n   \n")
    assert _section_has_body("【章末规则】\n最后 200 字必须落到一个未解问题或具体威胁上。")

    raw = _sections(
        ("任务", "写正文。"),
        ("硬约束与门禁", ""),  # 保护名单命中，但没有事实
        ("章末规则", "最后 200 字必须落到具体威胁上。"),
        ("普通填充", "Z" * 11_000),
        ("删减策略", "只写正文。"),
    )
    compacted, report = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=[])

    assert report.evicted_sections, "本例必须触发淘汰"
    assert "【硬约束与门禁】" in report.protected_headers_without_body
    assert "【章末规则】" not in report.protected_headers_without_body
    # 空标题只占名额、不带事实：报告要把它单列，不能混进"已保留的高价值内容"。
    assert report.closure()["protected_headers_without_body"] == ["【硬约束与门禁】"]
    assert "普通填充" not in compacted


def test_cap_reports_over_budget_when_protected_tail_exceeds_the_limit() -> None:
    """保护尾段自己就超预算时，超出的字符数必须被报出来，而不是静默超限。"""

    raw = _sections(
        ("任务", "写正文。"),
        ("普通参考", "R" * 900),
        ("卷级首章埋钩硬约束", "P" * 30_000),
        ("删减策略", "只写。"),
    )

    capped, evicted, continuity_evicted, header_only, over = _cap_prompt(
        raw, max_chars=2_000
    )

    assert over > 0
    assert len(capped) > 2_000
    assert "PPPPP" in capped, "保护尾段必须逐字保留"
    assert continuity_evicted == ()
    # 超限是"预算没闭合"，必须能和普通淘汰区分开。
    report = CompactionReport(
        original_chars=len(raw),
        compacted_chars=len(capped),
        saved_tokens_estimate=0,
        evicted_sections=evicted,
        cap_max_chars=2_000,
        cap_over_budget_chars=over,
    )
    assert report.budget_conflict is True
    assert report.closure()["cap_over_budget_chars"] == over


# ── 软裁剪：任务头被挤出、保护尾段超预算，都要有回执 ─────────────────────


def test_soft_trim_reports_that_it_ate_the_task_head() -> None:
    """保护尾段超过预算 → 整段头部被切掉，【任务】必须出现在回执里。"""

    task = "【任务】\n本章必须让林砚在封锁线内找到第二份病历，且不能提前透露门后是谁。"
    middle = "M" * 2_000
    protected = "【章末收尾钩子】\n门锁从里面转动。" + "K" * 2_500

    trimmed, report = _soft_trim_user_prompt_with_report(
        task + middle + protected,
        char_budget=2_000,
        language="zh-CN",
    )

    # 具体事实：本章目标被切走，必保尾段的事实还在。
    assert "第二份病历" not in trimmed
    assert "【任务】" not in trimmed
    assert "门锁从里面转动" in trimmed

    assert "【任务】" in report.task_head_markers_dropped
    assert report.dropped_head_chars == len(task + middle)
    # 必保尾段自己超预算 → 最终输入没有闭合，必须报出来。
    assert report.protected_tail_exceeds_budget is True
    assert report.over_budget_chars > 0
    assert report.budget_conflict is True
    assert report.closure()["over_budget_chars"] == report.over_budget_chars


def test_soft_trim_reports_head_block_dropped_by_tail_trim() -> None:
    """无必保 marker 时退化为尾部截断，被切掉的头部块同样要能查到。"""

    prompt = "A" * 1_500 + "【任务】\n写正文。"
    trimmed, report = _soft_trim_user_prompt_with_report(
        prompt, char_budget=1_000, language="zh-CN"
    )

    assert report.head_only_fallback is True
    assert "【任务】" in report.task_head_markers_dropped
    assert "【任务】" not in trimmed
    # 尾部正常截断后，标记本身会让最终长度略超 char_budget；如实记录，
    # 但这不算"保护尾段超过总预算"那种预算冲突。
    assert report.over_budget_chars > 0
    assert report.protected_tail_exceeds_budget is False
    assert report.protected_tail_chars == 0


def test_soft_trim_is_a_clean_noop_under_budget() -> None:
    prompt = "Short prompt. 【章末收尾钩子】ok"
    trimmed, report = _soft_trim_user_prompt_with_report(
        prompt, char_budget=10_000, language="zh-CN"
    )

    assert trimmed == prompt
    assert report.final_chars == report.original_chars == len(prompt)
    assert report.task_head_markers_dropped == ()
    assert report.budget_conflict is False


def test_soft_trim_text_entry_point_is_unchanged_for_legacy_callers() -> None:
    """保留原函数签名与返回类型——旧调用者/测试不能被诊断改动破坏。"""

    task = "【任务】\n写正文。"
    protected = "【章末收尾钩子】\n门锁从里面转动。"
    text_only = _soft_trim_user_prompt(
        task + "M" * 3_000 + protected, char_budget=200, language="zh-CN"
    )
    with_report, _ = _soft_trim_user_prompt_with_report(
        task + "M" * 3_000 + protected, char_budget=200, language="zh-CN"
    )
    assert isinstance(text_only, str)
    assert text_only == with_report


# ── 事实只在被删段出现：编译前丢的事实必须留痕 ───────────────────────────


def test_scene_pre_budget_loss_of_a_hard_fact_is_recorded() -> None:
    """compiled 场景路径先做结构化预算：编译前丢的事实要能在装配报告里查到。

    这是 W2 步骤 4 点名的坑——返回 CompiledPrompt 不等于只有一处预算处理。
    事实在这里就被清了，编译器报告看到的只是"已经没问题"的成品。
    """

    unique_fact = "RECENT_ONLY_b7e4"
    before = {
        "creative_core_line": "核心",
        "contract_section": "章" * 300,
        "recent_scene_section": f"上一场决定性事实 {unique_fact} 已经发生。",
    }

    after = _budget_context_sections(before, 10)

    assert after["recent_scene_section"] == ""
    assert unique_fact not in after["recent_scene_section"]
    # Tier 1 的契约块不受影响——丢的是 Tier 2。
    assert after["contract_section"] == before["contract_section"]

    assembly = build_prompt_assembly_report(
        before, after, budget_tokens=10, mode="budgeted"
    )
    assert "recent_scene_section" in assembly.dropped_keys
    assert "contract_section" not in assembly.dropped_keys


# ── 硬事实相互冲突：同族 required 硬块内容不一致必须失败关闭 ─────────────


def _hard_block(key: str, text: str, family: str) -> PromptBlock:
    return PromptBlock(
        key=key,
        channel="user",
        layer="hard_canon",
        authority=95,
        instruction_family=family,
        required=True,
        source=f"test.{key}",
        text=text,
    )


def test_conflicting_required_hard_facts_fail_closed() -> None:
    """两个权威硬块说不同的事实 → 必须报冲突，不能悄悄留一个。"""

    with pytest.raises(PromptConflictError) as excinfo:
        compile_prompt(
            [
                _hard_block("hard.a", "林砚今晚十一点在档案室。", "writer.hard.timeline"),
                _hard_block("hard.b", "林砚今晚十点在天台。", "writer.hard.timeline"),
            ],
            total_budget_tokens=8_000,
        )

    assert set(excinfo.value.conflicts) == {"hard.a", "hard.b"}


# ── 第一章 / 第二章：compiled 路径不二次裁剪 ─────────────────────────────


def _chapter_inputs(language: str, chapter_number: int):
    project = ProjectModel(
        slug=f"w2-closure-{language.lower()}-{chapter_number}",
        title="W2 预算闭合测试",
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
        title="封锁线",
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
    packet = SimpleNamespace(
        chapter_contract={"closing_hook": "门锁从里面转动", "core_conflict": "倒计时"},
        hard_fact_snapshot={"facts": ["档案室今晚封存"]},
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
        story_bible={"world": "旧城封锁区"},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],  # 无适用伏笔
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
        creative_core=None,
    )
    return project, chapter, scene, packet


@pytest.mark.parametrize("chapter_number", [1, 2])
def test_compiled_chapter_path_is_not_double_trimmed(chapter_number: int) -> None:
    """compiled 整章路径自己就是最终输入：不许再叠一层软裁剪/压缩标记。"""

    import bestseller.services.drafts as drafts

    project, chapter, scene, packet = _chapter_inputs("zh-CN", chapter_number)
    # 读模块属性而不是 import 时的快照，否则这个断言永远为真。
    drafts._LAST_SOFT_TRIM_REPORT = None

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
        prose_prompt_profile="lean",
    )

    assert isinstance(compiled, CompiledPrompt)
    # 无前章（第 1 章）与正常第二章都要满足：必需块完整、冲突为空。
    assert compiled.report.required_complete is True
    assert compiled.report.conflicts == ()
    assert "chapter_first.primary_task" in compiled.report.required_blocks_kept
    # 二次裁剪的证据：两层标记都不该出现。
    for text in (compiled.system, compiled.user):
        assert "开头已截断" not in text
        assert "已截断" not in text
        assert "【写作提示已压缩】" not in text
    assert drafts._LAST_SOFT_TRIM_REPORT is None, (
        "compiled 路径不应触发软裁剪；触发了说明发生了二次裁剪"
    )

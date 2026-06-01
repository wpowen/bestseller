from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import UUID

logger = logging.getLogger(__name__)

_LLM_PASS_OVERRIDABLE_RULE_CATEGORIES: frozenset[str] = frozenset(
    {
        "coverage",
        "coherence",
        "contract_alignment",
        "ending_hook_effectiveness",
        "continuity",
        "main_plot_progression",
        "opening_contract",
        "subplot_progression",
        "volume_mission_alignment",
    }
)

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.domain.review import (
    ChapterReviewFinding,
    ChapterReviewResult,
    ChapterReviewScores,
    SceneReviewFinding,
    SceneReviewResult,
    SceneReviewScores,
)
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    ClueModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
    VolumeModel,
)
from bestseller.services.action_scene_structure_gate import evaluate_action_scene_structure
from bestseller.services.chapter_quality_bundle import (
    ChapterQualityBundleContext,
    ChapterQualityBundleReport,
    run_chapter_quality_bundle,
)
from bestseller.services.checker_schema import CheckerReport
from bestseller.services.chekhov_emphasis_gate import evaluate_chekhov_emphasis
from bestseller.services.context import build_chapter_writer_context, build_scene_writer_context
from bestseller.services.drafts import (
    _NOVEL_OUTPUT_PROHIBITION,
    _NOVEL_OUTPUT_PROHIBITION_EN,
    _clean_generated_chapter_text,
    _collect_post_assembly_duplicate_findings,
    _collect_previous_current_chapter_texts,
    _evaluate_chapter_quality_gate,
    _front10_forbidden_signal_terms,
    _maybe_write_scene_prompt_trace,
    _normalize_fragment,
    _stamp_chapter_quality_bundle,
    _stamp_duplicate_content_block,
    count_words,
    has_meta_leak,
    prose_output_max_tokens_for_target,
    sanitize_novel_markdown_content,
    strip_scaffolding_echoes,
    validate_and_clean_novel_content,
)
from bestseller.services.hook_ledger_runtime import (
    compute_hook_ledger_audit_for_review,
    merge_hook_ledger_audit_into_chapter_review,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology import (
    render_methodology_scene_rules,
    render_qimao_opening_contract_block,
)
from bestseller.services.methodology_lineage import (
    methodology_lineage_from_object,
    methodology_lineage_review_expectations,
    render_methodology_lineage_prompt_block,
)
from bestseller.services.methodology_overlay import render_overlay_prompt_block
from bestseller.services.methodology_profile import render_configured_methodology_profile_block
from bestseller.services.methodology_runtime import (
    merge_methodology_reports_into_chapter_review,
    merge_methodology_reports_into_scene_review,
)
from bestseller.services.opening_three_function_gate import evaluate_opening_three_function
from bestseller.services.output_hygiene import collect_unfinished_artifact_issues
from bestseller.services.payoff_ledger_runtime import (
    compute_payoff_ledger_audit_for_review,
    merge_payoff_ledger_audit_into_chapter_review,
)
from bestseller.services.hook_ledger import is_methodology_v2_enabled
from bestseller.services.projects import get_project_by_slug
from bestseller.services.critic_evidence_gate import (
    build_critic_evidence_prompt_suffix,
    validate_critic_commentary,
)
from bestseller.services.methodology_compiler import (
    ChapterPosition,
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.prompt_packs import (
    render_methodology_block,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.qimao_opening_gate import QimaoOpeningFinding
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.services.quality_levers import (
    CriticLeverContext,
    audit_chapter,
    audit_emotion_labels,
    audit_rhythm,
    build_critic_quality_levers_block,
    extract_quality_levers_meta,
)
from bestseller.services.quality_repair_playbooks import render_quality_repair_playbooks
from bestseller.services.rewrite_impacts import analyze_rewrite_impacts_for_scene_task
from bestseller.services.word_targets import (
    CHINESE_CHAPTER_HARD_MIN_WORDS,
    chapter_rewrite_length_band,
    model_output_token_ceiling,
    model_reasoning_token_reserve,
    resolve_llm_role_max_tokens,
    resolve_llm_role_model,
)
from bestseller.services.writing_profile import (
    is_english_language,
    normalize_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings, get_settings

# Absolute rule appended to rewrite system prompts. The writer occasionally
# paraphrases ``rewrite_strategy`` back at us as if it were the chapter opener
# — this block tells it, in uncompromising terms, that strategy text is
# reference-only and must never appear in the body.
_REWRITE_STRATEGY_CONTRACT = """
【绝对约束 — 重写参考材料的使用】
- 下面用 `=== 仅供理解，严禁进入正文 ===` 栅栏包住的 `重写任务` / `重写策略` 字段\
只是给你理解修改方向的参考材料。
- 这些字段内部的遣词（例如 "这一版重写围绕……"、"叙事仍采用 third-limited 视角"、\
"强调狠、快、压迫感"、"承接上章后果并给出当前行动目标"）全都是规划语言。
- 你【绝对不允许】把这些规划语言以任何形式（原句、改写、摘要、段首引入、作为开场说明）\
出现在你的输出里。
- 也不允许输出类似 "第X章开场" / "本章承接" / "这一版" / "叙事采用" 的段落——\
这些都属于元评论。
- 输出必须是纯粹的叙事散文、对话、动作、环境、内心活动，直接进入故事场景。
- 不要在正文开头重复章节号或章节标题（章节号已经由系统单独渲染）。
"""

_REWRITE_STRATEGY_CONTRACT_EN = """
[Absolute Rule: rewrite-task reference material]
- The `rewrite task` / `rewrite strategy` block wrapped in `=== reference only ===`
  exists only to explain direction.
- You must never echo that planning language into the prose, whether verbatim,
  paraphrased, summarized, or as an opening explanation.
- Do not write meta lines like "this version", "the chapter opens with", or
  "the narration uses". Those are commentary, not fiction.
- Output must be pure narrative prose, dialogue, action, setting, and interiority.
- Do not repeat the chapter number or chapter title at the start of the prose;
  the heading is rendered separately.
"""

_SINGLE_PASS_CHAPTER_REWRITE_CONTRACT = """
【单次完整章节输出约束】
- 只输出一遍完整章节正文；写到本章尾钩后立即停止。
- 不得循环复述当前稿段落，不得把同一段、同一组对白、同一动作链重复输出。
- 不得为了补节奏或心率密度堆短句；每个新增短句都必须带来新动作、新证物变化、新阻断或新代价。
- 若需要删除 AI 句式，必须改写为具体动作/物件/后果，而不是换一个套话比喻。
- 如果当前稿已经在发布字数范围内，只允许局部替换和等量压缩式修复；不得新增大段闪回、新场景或连续设定解释。
- 如果重写任务提到“提前死亡/计划死亡章节/角色需存活”，必须删除所有确认死亡句，包括疑问句、传闻句和旁人推测，例如“已经死了，对吧？”；改为“失踪/被困/生死未明/还不能确认”。
- 章末只保留一个主钩子，最多一个辅助信息；禁止在最后300字连续叠加多个未解悬念。
"""

_SINGLE_PASS_CHAPTER_REWRITE_CONTRACT_EN = """
[Single-pass chapter output contract]
- Output the complete chapter once only; stop immediately after the chapter hook.
- Do not loop, duplicate, or re-emit the same paragraph, dialogue exchange, or action chain.
- Do not pad rhythm or pulse density with empty short lines; every added beat must create action, evidence movement, obstruction, or cost.
- Replace AI-ish phrasing with concrete action, objects, and consequences, not another stock metaphor.
- If the current draft is already inside the publishable length range, make local replacements and equal-length compression only; do not add long flashbacks, new scenes, or stacked exposition.
- If the rewrite task mentions premature death, planned death chapter, or a character who must survive, remove every confirmed-death sentence, including questions, rumors, or speculation; use missing, trapped, survival unknown, or unconfirmed instead.
- Keep one primary ending hook, with at most one supporting detail; do not stack multiple unresolved mysteries in the final 300 words.
"""


def _wrap_rewrite_reference(instructions: str | None, strategy: str | None) -> str:
    """Render rewrite instructions/strategy inside a fence so the LLM clearly
    sees they are reference-only material, not a template to echo back.

    We intentionally pad with highly visible ASCII separators because LLMs
    attend to literal tokens like ``===`` more reliably than to natural-
    language "please don't echo this" instructions.
    """
    instructions_text = (instructions or "").strip() or "(无)"
    strategy_text = (strategy or "").strip() or "(无)"
    return (
        "=== 仅供理解，严禁进入正文 ===\n"
        f"重写任务：{instructions_text}\n"
        f"重写策略：{strategy_text}\n"
        "=== 以上内容禁止复述、禁止改写成正文、禁止作为段首引入 ===\n"
    )


def _wrap_rewrite_reference_for_language(
    instructions: str | None,
    strategy: str | None,
    *,
    language: str | None,
) -> str:
    if is_english_language(language):
        instructions_text = (instructions or "").strip() or "(none)"
        strategy_text = (strategy or "").strip() or "(none)"
        return (
            "=== reference only: never echo into the prose ===\n"
            f"rewrite task: {instructions_text}\n"
            f"rewrite strategy: {strategy_text}\n"
            "=== do not quote, paraphrase, summarize, or use this as an opening paragraph ===\n"
        )
    return _wrap_rewrite_reference(instructions, strategy)


def _length_gate_codes_from_metadata(metadata: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for key in (
        "candidate_quality_gate_violations",
        "llm_candidate_quality_gate_violations",
    ):
        values = metadata.get(key)
        if not isinstance(values, list | tuple):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            if code and ("LENGTH" in code or code.endswith(("_BLOCK_LOW", "_BLOCK_HIGH"))):
                codes.add(code)
    return codes


def _classify_length_failure(
    *,
    word_count: int,
    codes: set[str],
    hard_min: int,
    hard_max: int,
) -> str | None:
    if any(code == "LENGTH_UNDER" or code.endswith("_BLOCK_LOW") for code in codes):
        return "under"
    if any(code == "LENGTH_OVER" or code.endswith("_BLOCK_HIGH") for code in codes):
        return "over"
    if word_count > 0 and word_count < hard_min:
        return "under"
    if word_count > hard_max:
        return "over"
    return None


def _render_recent_length_failure_directive(
    failures: list[RewriteTaskModel],
    *,
    chapter: ChapterModel,
    language: str | None,
) -> str:
    if not failures:
        return ""
    band_normal = chapter_rewrite_length_band(
        get_settings(),
        getattr(chapter, "target_word_count", None),
        language=language,
        direction="normal",
        role="editor",
    )
    samples: list[int] = []
    directions: list[str] = []
    codes_seen: set[str] = set()
    for task in failures[:5]:
        metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
        try:
            word_count = int(metadata.get("candidate_word_count") or 0)
        except (TypeError, ValueError):
            word_count = 0
        codes = _length_gate_codes_from_metadata(metadata)
        direction = _classify_length_failure(
            word_count=word_count,
            codes=codes,
            hard_min=band_normal.hard_min,
            hard_max=band_normal.hard_max,
        )
        if direction is None:
            continue
        directions.append(direction)
        codes_seen.update(codes)
        if word_count > 0:
            samples.append(word_count)
    if not directions:
        return ""

    if "under" in directions and "over" in directions:
        mode = "anti_oscillation"
        band = band_normal
    elif "over" in directions:
        mode = "compression"
        band = chapter_rewrite_length_band(
            get_settings(),
            getattr(chapter, "target_word_count", None),
            language=language,
            direction="over",
            role="editor",
        )
    else:
        mode = "expansion"
        band = chapter_rewrite_length_band(
            get_settings(),
            getattr(chapter, "target_word_count", None),
            language=language,
            direction="under",
            role="editor",
        )

    sample_text = ", ".join(str(item) for item in samples[:5]) or "unknown"
    codes_text = ", ".join(sorted(codes_seen)) or "length hard gate"
    if is_english_language(language):
        label = {
            "anti_oscillation": "anti-oscillation",
            "compression": "compression",
            "expansion": "expansion",
        }[mode]
        return (
            "\nLENGTH CONVERGENCE GATE (MANDATORY):\n"
            f"- Recent failed rewrite candidate word counts: {sample_text}; gates: {codes_text}.\n"
            f"- Mode: {label}. Final body must land in {band.safe_min}-{band.safe_max} words, "
            f"targeting about {band.hard_target}.\n"
            "- Do not compensate for a short failure with an oversized expansion, or for an oversized "
            "failure with a summary. Keep the same event set and adjust density.\n"
            "- Before final output, silently count the body. If outside the safe band, self-revise once "
            "and output only the corrected final chapter.\n"
        )
    label = {
        "anti_oscillation": "反振荡收敛",
        "compression": "压缩收敛",
        "expansion": "扩写收敛",
    }[mode]
    return (
        "\n【章节字数收敛闸门·硬性要求】\n"
        f"- 最近失败候选稿字数：{sample_text}；触发门：{codes_text}。\n"
        f"- 本轮模式：{label}。最终正文必须落在 {band.safe_min}-{band.safe_max} 个有效中文汉字，"
        f"目标约 {band.hard_target} 字。\n"
        "- 禁止“过短后报复性扩写”或“过长后压成梗概”。保持同一条事件线，只调节场景密度、对白密度和过渡长度。\n"
        "- 输出前必须在内部静默计数；若不在安全区，先自我修正一次，只输出修正后的最终正文。\n"
    )


def _append_recent_length_failure_directive(
    instructions: str,
    failures: list[RewriteTaskModel],
    *,
    chapter: ChapterModel,
    language: str | None,
) -> str:
    directive = _render_recent_length_failure_directive(
        failures,
        chapter=chapter,
        language=language,
    )
    if not directive:
        return instructions
    base = (instructions or "").split("\n【章节字数收敛闸门·硬性要求】", 1)[0]
    base = base.split("\nLENGTH CONVERGENCE GATE (MANDATORY):", 1)[0]
    return f"{base.rstrip()}\n{directive}"


async def _select_rewrite_working_draft(
    session: AsyncSession,
    *,
    current_draft: ChapterDraftVersionModel,
    recent_failed_rewrites: list[RewriteTaskModel],
    settings: AppSettings,
    chapter: ChapterModel,
    language: str | None,
) -> ChapterDraftVersionModel:
    """Continue from a better rejected candidate when the current draft is worse.

    A quality-retrofit retry can produce a publish-range candidate that misses
    only the targeted retrofit detector. Reverting the next round to the older
    current draft makes length oscillation likely, so use that candidate as the
    next working draft when it is inside the hard chapter band.
    """

    band = chapter_rewrite_length_band(
        settings,
        getattr(chapter, "target_word_count", None),
        language=language,
        direction="normal",
        role="editor",
    )
    current_words = int(getattr(current_draft, "word_count", 0) or 0)
    current_in_band = band.hard_min <= current_words <= band.hard_max
    for task in recent_failed_rewrites:
        metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
        if not metadata.get("quality_retrofit_rejected_current_promotion"):
            continue
        draft_id_raw = metadata.get("candidate_chapter_draft_id")
        if not draft_id_raw:
            continue
        try:
            candidate_word_count = int(metadata.get("candidate_word_count") or 0)
            candidate_draft_id = UUID(str(draft_id_raw))
        except (TypeError, ValueError):
            continue
        if not (band.hard_min <= candidate_word_count <= band.hard_max):
            continue
        if current_in_band and candidate_word_count >= current_words:
            continue
        candidate_draft = await session.get(
            ChapterDraftVersionModel,
            candidate_draft_id,
        )
        if candidate_draft is None:
            continue
        if candidate_draft.chapter_id != current_draft.chapter_id:
            continue
        if not (candidate_draft.content_md or "").strip():
            continue
        return candidate_draft
    return current_draft


def _project_metadata(project: ProjectModel) -> dict[str, Any]:
    metadata = getattr(project, "metadata_json", None)
    return metadata if isinstance(metadata, dict) else {}


def _json_dict_from_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        return dumped if isinstance(dumped, dict) else {}
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return {str(key): item for key, item in raw.items() if not str(key).startswith("_")}
    return {}


def _project_rejection_reasons(project: ProjectModel) -> str | None:
    metadata = _project_metadata(project)
    reason = (
        metadata.get("editor_rejection_reasons")
        or metadata.get("rejection_reasons")
        or metadata.get("rejection_reason")
    )
    return str(reason) if reason else None


def _qimao_opening_contract_prompt_block(
    project: ProjectModel,
    *,
    chapter_number: int,
    language: str | None,
) -> str:
    block = render_qimao_opening_contract_block(
        _project_metadata(project).get("opening_quality_contract")
        or _project_metadata(project).get("qimao_opening_contract"),
        chapter_number=chapter_number,
        language=language,
        rejection_reasons=_project_rejection_reasons(project),
    )
    return f"{block}\n" if block else ""


_QIMAO_REWRITE_STRATEGY_BY_FINDING = {
    "ordinary_entry": "qimao_opening_incident_rewrite",
    "weak_immersion": "qimao_pov_immersion_rewrite",
    "weak_hook": "qimao_hook_rebuild",
    "flat_narration": "qimao_conflict_loop_rewrite",
    "weak_golden_three_payoff": "qimao_golden_three_payoff_rewrite",
    "first_10k_loop_missing": "qimao_conflict_loop_rewrite",
}


def qimao_opening_rewrite_strategy_for_findings(
    findings: tuple[QimaoOpeningFinding, ...] | list[QimaoOpeningFinding],
) -> str:
    for finding in findings:
        strategy = _QIMAO_REWRITE_STRATEGY_BY_FINDING.get(finding.code)
        if strategy and finding.severity == "critical":
            return strategy
    for finding in findings:
        strategy = _QIMAO_REWRITE_STRATEGY_BY_FINDING.get(finding.code)
        if strategy:
            return strategy
    return "qimao_opening_incident_rewrite"


def build_qimao_opening_rewrite_instructions(
    findings: tuple[QimaoOpeningFinding, ...] | list[QimaoOpeningFinding],
    *,
    chapter_number: int,
    opening_contract: dict[str, Any],
    rejection_reasons: str | None,
) -> str:
    strategy = qimao_opening_rewrite_strategy_for_findings(findings)
    chapter_task = {
        1: opening_contract.get("chapter_1_small_turn"),
        2: opening_contract.get("chapter_2_reveal"),
        3: opening_contract.get("chapter_3_payoff"),
    }.get(chapter_number)
    lines = [
        "【七猫开篇门禁重写任务】",
        f"- rewrite_strategy: {strategy}",
        "- 这不是润色任务；优先重建切入点、主角代入、可感冲突、章节钩子和前三章爽点闭环。",
        f"- 章节：第{chapter_number}章",
    ]
    if rejection_reasons and rejection_reasons.strip():
        lines.append(f"- 已知拒稿原因：{rejection_reasons.strip()}")
    for key, label in (
        ("opening_incident", "开篇事件"),
        ("first_page_conflict", "第一页冲突"),
        ("protagonist_immediate_goal", "主角即时目标"),
        ("visible_loss_if_fail", "失败可见损失"),
        ("protagonist_edge", "主角差异化优势"),
        ("first_10000_loop", "前一万字循环"),
    ):
        value = opening_contract.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"- {label}：{value.strip()}")
    if isinstance(chapter_task, str) and chapter_task.strip():
        lines.append(f"- 本章必须完成：{chapter_task.strip()}")
    if findings:
        lines.append("- 门禁失败项：")
        for finding in findings:
            mapped = _QIMAO_REWRITE_STRATEGY_BY_FINDING.get(finding.code, "qimao_opening_incident_rewrite")
            lines.append(
                f"  - {finding.code} [{finding.severity}] -> {mapped}：{finding.message}"
            )
    lines.append(
        "- 输出要求：直接重写正文，不输出分析、计划、修改说明；用动作、对话压力、感官后果和选择代价提升文笔与代入。"
    )
    return "\n".join(lines)


def _material_reference_prompt_block(
    project: ProjectModel,
    *,
    language: str | None,
) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    block = str(metadata.get("material_reference_block") or "").strip()
    if not block:
        return ""
    if is_english_language(language):
        lead = (
            "[Project material anchors]\n"
            "Use these §slug anchors as canonical project material. Do not invent "
            "new equivalent names, rules, factions, or devices when an anchor already covers the function.\n"
        )
    else:
        lead = (
            "【本书素材锚点】\n"
            "以下 §slug 是本书已落库素材。重写时必须优先使用这些既有规则、地点、人物、物件、情绪弧和反套路约束；"
            "不得另造同功能的新名词、新规则或无关怪谈。\n"
        )
    return f"{lead}{block}\n"


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def _severity_from_score(score: float) -> str:
    if score < 0.45:
        return "high"
    if score < 0.7:
        return "medium"
    return "low"


_LOW_SIGNAL_TERMS = frozenset(
    {
        "本章",
        "本场",
        "当前",
        "这一章",
        "这一场",
        "这个",
        "那个",
        "这些",
        "那些",
        "必须",
        "需要",
        "应该",
        "然后",
        "随后",
        "同时",
        "以及",
        "因为",
        "所以",
        "为了",
        "继续",
        "开始",
        "完成",
        "推进",
        "推进主线",
        "故事",
        "剧情",
        "场景",
        "章节",
        "主角",
        "人物",
        "角色",
        "合作关系",
    }
)
_LOW_SIGNAL_EDGE_CHARS = set(
    "的是了在把将向并与和及或先后再却但也又还都让被对给着地得很从于中上下这那里个种次其所而要会想去来到"
)
_CONFLICT_SIGNAL_TERMS = (
    "对峙",
    "逼问",
    "质问",
    "反锁",
    "摔在",
    "攥紧",
    "僵住",
    "谁也不肯",
    "不肯先退",
    "盯着",
    "压着火气",
    "沉了脸",
    "冷冷问",
    "厉声",
    "拦住",
    "逼近",
)
_EMOTION_SIGNAL_TERMS = (
    "手背",
    "手心",
    "青筋",
    "呼吸",
    "沉默",
    "喉咙",
    "后背",
    "背脊",
    "火气",
    "冷意",
    "发紧",
    "绷起",
    "沉了脸",
    "空气像被绞紧",
    "压迫",
    "警觉",
)
_HOOK_SIGNAL_TERMS = (
    "忽然",
    "突然",
    "门外",
    "脚步声",
    "人影",
    "影子",
    "阴影",
    "电梯",
    "镜面",
    "一模一样",
    "三短一长",
    "倒计时",
    "警报",
    "电话",
    "手机",
    "屏幕",
    "消息",
    "号码",
    "敲门",
    "响起",
    "立刻",
    "必须",
    "下一秒",
    "下一瞬",
)
_FOLK_HORROR_TAIL_HOOK_TERMS = (
    "人影",
    "影子",
    "阴影",
    "电梯",
    "镜子",
    "镜面",
    "倒影",
    "一模一样",
    "同一个动作",
    "铜钱",
    "缺口",
    "冒血",
    "血",
    "七个",
    "六个",
    "半透明",
    "门缝",
    "三短一长",
)
_INFO_SIGNAL_TERMS = (
    "发现",
    "翻开",
    "露出",
    "证据",
    "记录",
    "线索",
    "规则",
    "代价",
    "真相",
    "果然",
    "原来",
    "禁航",
    "航线",
    "缺页",
    "药剂",
)
_FOLK_HORROR_INFO_TERMS = (
    "快递单",
    "寄件时间",
    "零点零三分",
    "子时",
    "否认者",
    "认账",
    "入账",
    "镜债",
    "困魂镜",
    "铜钱",
    "缺口",
    "血",
    "三短一长",
)
_CONTINUITY_SIGNAL_TERMS = (
    "上一",
    "此前",
    "先前",
    "昨夜",
    "刚才",
    "随后",
    "与此同时",
    "因此",
    "于是",
    "接着",
    "不久",
    "这时",
    # English continuity markers
    "earlier",
    "before",
    "previously",
    "last night",
    "meanwhile",
    "afterward",
    "therefore",
    "consequently",
    "just then",
    "had said",
    "had promised",
    "had warned",
)
_SPEECH_SIGNAL_TERMS = ("说", "问", "答", "喊", "低声", "冷冷", "沉声", "厉声")
_META_REWARD_TERMS = ("整体语气保持", "本章目标", "场景目标", "剧情任务", "情绪任务")
_EN_META_REWARD_TERMS = (
    "overall tone maintains", "chapter goal", "scene objective",
    "plot task", "emotional task", "narrative function", "story purpose",
    "character arc progression", "this scene serves to", "the reader should feel",
)
# AI cliché phrases that indicate LLM-generated text
_AI_CLICHE_TERMS = (
    "blood crystallized",
    "blood ran cold",
    "blood turned to ice",
    "words hung in the air",
    "words landed like",
    "cold as vacuum",
    "frozen fire",
    "liquid fire",
    "something almost like",
    "something that might have been",
    "the world narrowed to",
    "time seemed to slow",
    "the air itself seemed",
    "a laugh that held no humor",
    "didn't reach",  # "smile that didn't reach their eyes"
    "electricity crackled between",
    "tension thick enough to cut",
    "every fiber of",
    "a weight settled in",
    "the silence was deafening",
    "pregnant pause",
    "comfortable silence",
)


def _is_low_signal_term(term: str) -> bool:
    normalized = _normalize_fragment(term)
    if not normalized:
        return True
    if normalized.isdigit():
        return True
    if len(normalized) <= 1:
        return True
    return normalized in _LOW_SIGNAL_TERMS


def _signal_spans(value: str | None, *, max_spans: int = 10) -> list[str]:
    if not value:
        return []
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", _normalize_fragment(value))
    if len(normalized) < 2:
        return []

    spans: list[str] = []
    for size in range(min(4, len(normalized)), 1, -1):
        max_index = len(normalized) - size
        indices: list[int] = []
        for offset in range(max_index + 1):
            left_index = offset
            right_index = max_index - offset
            if left_index not in indices:
                indices.append(left_index)
            if right_index not in indices:
                indices.append(right_index)
        for index in indices:
            span = normalized[index : index + size]
            if _is_low_signal_term(span):
                continue
            if span[0] in _LOW_SIGNAL_EDGE_CHARS or span[-1] in _LOW_SIGNAL_EDGE_CHARS:
                continue
            if span not in spans:
                spans.append(span)
            if len(spans) >= max_spans:
                return spans
    return spans


def _term_candidates(*values: str | None) -> list[str]:
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        normalized = _normalize_fragment(value)
        if normalized and not _is_low_signal_term(normalized) and normalized not in terms:
            terms.append(normalized)
        for clause in re.split(r"[，。！？；：:\n]+", value):
            normalized_clause = _normalize_fragment(clause)
            if (
                normalized_clause
                and not _is_low_signal_term(normalized_clause)
                and normalized_clause not in terms
            ):
                terms.append(normalized_clause)
            for segment in re.split(
                r"(?:并且|并|同时|随后|然后|但是|却|以及|从|向|转向|让|把|将|先|再|还|与|和|或)",
                clause,
            ):
                normalized_segment = _normalize_fragment(segment)
                if (
                    normalized_segment
                    and not _is_low_signal_term(normalized_segment)
                    and normalized_segment not in terms
                ):
                    terms.append(normalized_segment)
        for token in re.findall(r"[0-9A-Za-z\u4e00-\u9fff]{2,}", value):
            if not _is_low_signal_term(token) and token not in terms:
                terms.append(token)
        for span in _signal_spans(value):
            if span not in terms:
                terms.append(span)
    return terms


def _contract_field_score(content: str, value: str | None) -> float | None:
    if not value:
        return None
    normalized_content = _normalize_fragment(content)
    normalized = _normalize_fragment(value)
    if normalized and normalized in normalized_content:
        return 1.0
    clauses = [
        clause
        for clause in (
            _normalize_fragment(part)
            for part in re.split(r"[，。！？；：:\n]+", value)
        )
        if clause and not _is_low_signal_term(clause)
    ][:4]
    clause_hits = sum(1 for clause in clauses if clause in normalized_content)
    clause_score = clause_hits / len(clauses) if clauses else 0.0

    terms = _term_candidates(value)[:8]
    if not terms:
        return 0.0
    total_weight = 0.0
    matched_weight = 0.0
    for term in terms:
        weight = 1.3 if len(term) >= 4 else 1.0
        total_weight += weight
        if _normalize_fragment(term) in normalized_content:
            matched_weight += weight
    term_score = matched_weight / total_weight if total_weight else 0.0
    return _clamp_score(max(clause_score, term_score))


def _evaluate_contract_alignment(
    content: str,
    *,
    expectations: list[tuple[str, str | None]],
    label_weights: dict[str, float] | None = None,
    label_floors: dict[str, float] | None = None,
) -> tuple[float, dict[str, object]]:
    scored_items: list[tuple[str, float, float]] = []
    missing_labels: list[str] = []
    for label, value in expectations:
        field_score = _contract_field_score(content, value)
        if field_score is None:
            continue
        if label_floors:
            field_score = max(field_score, label_floors.get(label, 0.0))
        weight = label_weights.get(label, 1.0) if label_weights else 1.0
        scored_items.append((label, field_score, weight))
        if field_score < 0.5:
            missing_labels.append(label)
    if not scored_items:
        return 1.0, {
            "contract_expectation_count": 0,
            "contract_matched_count": 0,
            "contract_missing_labels": [],
            "contract_alignment_breakdown": {},
        }
    breakdown = {label: score for label, score, _ in scored_items}
    matched_count = sum(1 for _, score, _ in scored_items if score >= 0.5)
    weighted_total = sum(score * weight for _, score, weight in scored_items)
    total_weight = sum(weight for _, _, weight in scored_items)
    return _clamp_score(weighted_total / total_weight), {
        "contract_expectation_count": len(scored_items),
        "contract_matched_count": matched_count,
        "contract_missing_labels": missing_labels,
        "contract_alignment_breakdown": breakdown,
    }


def _tail_excerpt(content: str, *, max_chars: int = 260) -> str:
    normalized = str(content or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[-max_chars:]


def _keyword_score(
    content: str,
    *,
    keywords: list[str],
    max_terms: int = 8,
) -> float | None:
    terms: list[str] = []
    non_empty_keywords = [keyword for keyword in keywords if keyword]
    if not non_empty_keywords:
        return None
    per_keyword_budget = max(2, max_terms // max(len(non_empty_keywords), 1))
    for keyword in non_empty_keywords:
        for term in _term_candidates(keyword)[:per_keyword_budget]:
            if term not in terms:
                terms.append(term)
            if len(terms) >= max_terms:
                break
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    normalized_content = _normalize_fragment(content)
    total_weight = 0.0
    matched_weight = 0.0
    for term in terms:
        normalized_term = _normalize_fragment(term)
        if not normalized_term:
            continue
        weight = 1.25 if len(normalized_term) >= 4 else 1.0
        total_weight += weight
        if normalized_term in normalized_content:
            matched_weight += weight
    if total_weight == 0:
        return None
    return _clamp_score(matched_weight / total_weight)


def _signal_score(content: str, *, keywords: list[str], max_terms: int = 10) -> float:
    return _keyword_score(content, keywords=keywords, max_terms=max_terms) or 0.0


def _story_bible_frontier(packet: Any | None) -> dict[str, Any]:
    if packet is None:
        return {}
    story_bible = getattr(packet, "story_bible", {}) or {}
    if isinstance(story_bible, dict):
        frontier = story_bible.get("volume_frontier", {})
        return frontier if isinstance(frontier, dict) else {}
    return {}


def _scene_contract_expectations(
    *,
    chapter_contract: Any | None = None,
    scene_contract: Any | None = None,
) -> list[tuple[str, str | None]]:
    if scene_contract is not None:
        return [
            ("scene_summary", getattr(scene_contract, "contract_summary", None)),
            ("core_conflict", getattr(scene_contract, "core_conflict", None)),
            ("emotional_shift", getattr(scene_contract, "emotional_shift", None)),
            ("information_release", getattr(scene_contract, "information_release", None)),
            ("tail_hook", getattr(scene_contract, "tail_hook", None)),
            ("conflict_stakes", getattr(scene_contract, "conflict_stakes", None)),
            ("conflict_buffs", "；".join(getattr(scene_contract, "conflict_buffs", []) or [])),
            ("hook_type", getattr(scene_contract, "hook_type", None)),
            ("spotlight_character", getattr(scene_contract, "spotlight_character", None)),
            ("information_control_mode", getattr(scene_contract, "information_control_mode", None)),
            ("camera_distance", getattr(scene_contract, "camera_distance", None)),
            ("reveal_mode", getattr(scene_contract, "reveal_mode", None)),
            ("signature_image", getattr(scene_contract, "signature_image", None)),
            ("cut_point", getattr(scene_contract, "cut_point", None)),
            ("action_sequence", "；".join(getattr(scene_contract, "action_sequence", []) or [])),
            ("relationship_debts", "；".join(getattr(scene_contract, "relationship_debts", []) or [])),
        ]
    if chapter_contract is not None:
        expectations = [
            ("chapter_summary", getattr(chapter_contract, "contract_summary", None)),
            ("core_conflict", getattr(chapter_contract, "core_conflict", None)),
            ("emotional_shift", getattr(chapter_contract, "emotional_shift", None)),
            ("information_release", getattr(chapter_contract, "information_release", None)),
            ("closing_hook", getattr(chapter_contract, "closing_hook", None)),
            ("conflict_stakes", getattr(chapter_contract, "conflict_stakes", None)),
            ("conflict_buffs", "；".join(getattr(chapter_contract, "conflict_buffs", []) or [])),
            ("pacing_mode", getattr(chapter_contract, "pacing_mode", None)),
            ("emotion_phase", getattr(chapter_contract, "emotion_phase", None)),
            ("hooks_to_resolve", "；".join(getattr(chapter_contract, "hooks_to_resolve", []) or [])),
            ("hooks_to_plant", "；".join(getattr(chapter_contract, "hooks_to_plant", []) or [])),
            ("relationship_debts", "；".join(getattr(chapter_contract, "relationship_debts", []) or [])),
            ("character_delta", getattr(chapter_contract, "character_delta", None)),
            ("protagonist_choice", getattr(chapter_contract, "protagonist_choice", None)),
        ]
        expectations.extend(_causal_contract_expectations(chapter_contract))
        expectations.extend(methodology_lineage_review_expectations(chapter_contract))
        return expectations
    return []


def _chapter_contract_expectations(
    *,
    chapter_contract: Any | None = None,
) -> list[tuple[str, str | None]]:
    if chapter_contract is None:
        return []
    expectations = [
        ("chapter_summary", getattr(chapter_contract, "contract_summary", None)),
        ("core_conflict", getattr(chapter_contract, "core_conflict", None)),
        ("emotional_shift", getattr(chapter_contract, "emotional_shift", None)),
        ("information_release", getattr(chapter_contract, "information_release", None)),
        ("closing_hook", getattr(chapter_contract, "closing_hook", None)),
        ("conflict_stakes", getattr(chapter_contract, "conflict_stakes", None)),
        ("conflict_buffs", "；".join(getattr(chapter_contract, "conflict_buffs", []) or [])),
        ("pacing_mode", getattr(chapter_contract, "pacing_mode", None)),
        ("emotion_phase", getattr(chapter_contract, "emotion_phase", None)),
        ("hooks_to_resolve", "；".join(getattr(chapter_contract, "hooks_to_resolve", []) or [])),
        ("hooks_to_plant", "；".join(getattr(chapter_contract, "hooks_to_plant", []) or [])),
        ("relationship_debts", "；".join(getattr(chapter_contract, "relationship_debts", []) or [])),
        ("character_delta", getattr(chapter_contract, "character_delta", None)),
        ("protagonist_choice", getattr(chapter_contract, "protagonist_choice", None)),
    ]
    expectations.extend(_causal_contract_expectations(chapter_contract))
    expectations.extend(methodology_lineage_review_expectations(chapter_contract))
    return expectations


def _causal_contract_expectations(
    chapter_contract: Any | None,
) -> list[tuple[str, str | None]]:
    causal_contract = getattr(chapter_contract, "causal_contract", None)
    if not isinstance(causal_contract, dict):
        return []
    keys = (
        "pressure",
        "visible_action_or_reaction",
        "resistance",
        "cost_or_tradeoff",
        "gain_or_reveal",
        "state_change",
        "next_reader_desire",
    )
    return [
        (f"causal_contract.{key}", str(causal_contract.get(key)).strip())
        for key in keys
        if str(causal_contract.get(key) or "").strip()
    ]


def _methodology_lineage_evidence_summary(
    content: str,
    chapter_contract: Any | None,
) -> dict[str, Any]:
    lineage = methodology_lineage_from_object(chapter_contract)
    if lineage is None:
        return {"rules": [], "missing_rule_ids": []}
    contract_payload = _json_dict_from_object(chapter_contract)
    rules: list[dict[str, Any]] = []
    missing_rule_ids: list[str] = []
    for item in lineage.for_stage("review"):
        field_results: list[dict[str, Any]] = []
        scores: list[float] = []
        for field_path in item.evidence_fields:
            expected = _resolve_contract_evidence_path(contract_payload, field_path)
            field_score = _contract_field_score(content, expected)
            if field_score is not None:
                scores.append(field_score)
            field_results.append(
                {
                    "field": field_path,
                    "expected": expected,
                    "score": field_score,
                    "matched": field_score is not None and field_score >= 0.5,
                }
            )
        rule_score = _clamp_score(sum(scores) / len(scores)) if scores else None
        if rule_score is not None and rule_score < 0.5:
            missing_rule_ids.append(item.rule_id)
        rules.append(
            {
                "rule_id": item.rule_id,
                "slot": item.slot,
                "gate_mode": item.gate_mode,
                "verifiability": item.verifiability,
                "score": rule_score,
                "fields": field_results,
            }
        )
    return {"rules": rules, "missing_rule_ids": missing_rule_ids}


def _methodology_lineage_findings(
    evidence: dict[str, Any],
) -> list[ChapterReviewFinding]:
    findings: list[ChapterReviewFinding] = []
    rules = evidence.get("rules")
    if not isinstance(rules, list):
        return findings
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        score = rule.get("score")
        if not isinstance(score, int | float) or float(score) >= 0.5:
            continue
        gate_mode = str(rule.get("gate_mode") or "warn")
        findings.append(
            ChapterReviewFinding(
                category="methodology_evidence",
                severity="medium" if gate_mode == "block" else "low",
                message=(
                    f"{rule.get('rule_id')} evidence not visible enough "
                    f"(score={float(score):.2f})."
                ),
            )
        )
    return findings


def _resolve_contract_evidence_path(
    payload: dict[str, Any],
    field_path: str,
) -> str | None:
    path_parts = [part for part in str(field_path).split(".") if part]
    value: object = payload
    for part in path_parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        value = None
        break
    if value is None and path_parts:
        value = payload.get(path_parts[-1])
    if isinstance(value, list | tuple | set):
        text = "；".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        text = "；".join(
            str(item).strip()
            for item in value.values()
            if isinstance(item, str) and item.strip()
        )
    else:
        text = str(value or "").strip()
    return text or None


def _max_severity(findings: list[SceneReviewFinding]) -> str:
    if any(finding.severity == "high" for finding in findings):
        return "high"
    if any(finding.severity == "medium" for finding in findings):
        return "medium"
    return "low"


def render_scene_review_summary(
    review_result: SceneReviewResult,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    summary_lines = [
        f"{'Verdict' if is_en else '结论'}：{review_result.verdict}",
        f"{'Overall score' if is_en else '总分'}：{review_result.scores.overall}",
        f"{'Top severity' if is_en else '最高严重级别'}：{review_result.severity_max}",
    ]
    if review_result.findings:
        summary_lines.append("Findings:" if is_en else "问题列表：")
        summary_lines.extend(
            f"- [{finding.category}/{finding.severity}] {finding.message}"
            for finding in review_result.findings
        )
    if review_result.rewrite_instructions:
        summary_lines.append(
            f"{'Rewrite instructions' if is_en else '重写要求'}：{review_result.rewrite_instructions}"
        )
    return "\n".join(summary_lines)


def _parse_llm_verdict(critic_response: str) -> str | None:
    """Extract structured verdict from LLM critic response.

    Looks for 'VERDICT: pass' or 'VERDICT: rewrite' in the response.
    Returns 'pass', 'rewrite', or None if no structured verdict found.
    """
    match = re.search(r"VERDICT:\s*(pass|rewrite)", critic_response, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def _parse_llm_rewrite_direction(critic_response: str) -> str | None:
    """Extract rewrite direction from LLM critic response.

    Looks for 'REWRITE_DIRECTION: ...' line(s) in the response.
    """
    match = re.search(
        r"REWRITE_DIRECTION:\s*(.+?)(?:\n(?:COMMENTARY|VERDICT|METHODOLOGY):|$)",
        critic_response,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        direction = match.group(1).strip().strip("[]")
        if direction and direction.lower() not in ("none", "n/a", "无"):
            return direction
    return None


def _should_generate_scene_review_commentary(settings: AppSettings) -> bool:
    """Return whether scene review should spend an extra LLM call on prose commentary.

    The deterministic rubric already decides pass/rewrite. The optional critic
    call only rephrases that result for humans, so it is disabled by default to
    keep the quality gate while avoiding the largest latency source in the
    chapter pipeline.
    """
    return settings.quality.enable_llm_scene_commentary


def _should_generate_chapter_review_commentary(settings: AppSettings) -> bool:
    """Return whether chapter review should spend an extra LLM call on commentary."""
    return settings.quality.enable_llm_chapter_commentary


def _resolve_project_writing_profile(project: Any, style_guide: StyleGuideModel | None = None):
    metadata = getattr(project, "metadata_json", {}) or {}
    raw_profile = metadata.get("writing_profile") if isinstance(metadata, dict) else None
    fallback_style = None
    if style_guide is not None:
        fallback_style = {
            "style": {
                "pov_type": getattr(style_guide, "pov_type", "third-limited"),
                "tense": getattr(style_guide, "tense", "present"),
                "tone_keywords": list(getattr(style_guide, "tone_keywords", []) or []),
                "prose_style": getattr(style_guide, "prose_style", "commercial-web-serial"),
                "sentence_style": getattr(style_guide, "sentence_style", "mixed"),
                "info_density": getattr(style_guide, "info_density", "medium"),
                "dialogue_ratio": float(getattr(style_guide, "dialogue_ratio", 0.4)),
                "reference_works": list(getattr(style_guide, "reference_works", []) or []),
                "custom_rules": list(getattr(style_guide, "custom_rules", []) or []),
            }
        }
    return resolve_writing_profile(
        raw_profile or fallback_style,
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
        audience=getattr(project, "audience", None),
        language=getattr(project, "language", None),
    )


def _resolve_project_prompt_pack(project: Any, writing_profile: Any):
    return resolve_prompt_pack(
        getattr(writing_profile.market, "prompt_pack_key", None),
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
    )


def _project_language(project: Any) -> str:
    return normalize_language(getattr(project, "language", None))


def build_scene_review_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel,
    review_result: SceneReviewResult,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _project_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _resolve_project_writing_profile(project)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_review_system = getattr(_genre_profile.judge_prompts, f"scene_review_system_{_lang_key}", "")
    system_prompt = (
        (
            "# ROLE\n"
            "You are a senior scene reviewer for a long-form commercial fiction pipeline.\n"
            "You have audited 200+ scenes for signed novels and can tell from one read\n"
            "whether a scene earns the reader's next click or wastes it.\n"
            "\n"
            "# CONTEXT\n"
            "Your verdict drives the rewrite loop: 'pass' = ship; 'rewrite' = trigger LLM regen.\n"
            "Your feedback must be ACTIONABLE — the regen LLM will execute REWRITE_DIRECTION verbatim.\n"
            "\n"
            "# TASK\n"
            "Evaluate the scene prose against five methodology axes; emit a fixed-format verdict.\n"
            "\n"
            "# CONSTRAINTS · The five methodology axes\n"
            "1. **Show-don't-tell**: emotions via action / physicality, NOT named directly\n"
            "2. **Sensory richness**: at least 2 sensory channels (sight / sound / touch / smell / taste)\n"
            "3. **Dialogue subtext**: characters don't state intentions; tension = gap between words and meaning\n"
            "4. **Tail hook**: scene ends on an unresolved question / threat / revelation\n"
            "5. **Reaction amplification**: after key moments, other characters' reactions amplify impact\n"
            "\n"
            "# THINKING (in your head before output)\n"
            "1. Read scene end-to-end; mark each axis as PASS or FAIL\n"
            "2. For each FAIL, locate the specific paragraph / sentence that triggered it\n"
            "3. If ≥2 axes FAIL, verdict = rewrite; otherwise verdict = pass\n"
            "4. REWRITE_DIRECTION must be concrete (not 'improve dialogue' — 'cut林渊's line 3, replace with a single action: 拇指碾过铜钱缺口')\n"
            "\n"
            "# OUTPUT FORMAT (exact lines, no extras)\n"
            "VERDICT: pass OR rewrite\n"
            "METHODOLOGY: [list axes that failed, e.g. 'show-don't-tell, tail hook']\n"
            "REWRITE_DIRECTION: [if rewrite, concrete instructions; if pass, write 'N/A']\n"
            "COMMENTARY: [≤2 sentences editorial note]"
        )
        if is_en
        else (
            "# ROLE\n"
            "你是长篇商业小说审校系统里的资深场景评论者。\n"
            "你审过 200+ 部签约连载的场景稿，能从一遍读下来就判定这一场是值得读者翻页还是浪费时间。\n"
            "\n"
            "# CONTEXT\n"
            "你的 verdict 直接驱动重写循环：'pass' = 通过；'rewrite' = 触发 LLM 重生成。\n"
            "你的 REWRITE_DIRECTION 会被下游 LLM **照字执行**——必须是具体、可操作的指令。\n"
            "\n"
            "# TASK\n"
            "按下方五项方法论评估本场，按固定格式输出 verdict。\n"
            "\n"
            "# CONSTRAINTS · 五项方法论轴\n"
            "1. **展示不讲述**：情绪通过动作 / 身体反应传达，不能直接写情绪词（愤怒 / 伤心 / 高兴 / 紧张）\n"
            "2. **感官丰富度**：至少使用 2 个感官通道（视 / 听 / 触 / 嗅 / 味）\n"
            "3. **对话潜台词**：角色不能直白表达意图，张力来自话语和真实意图的反差\n"
            "4. **尾钩强度**：场景必须以未解答的问题 / 威胁 / 揭示结尾，不能抽象感叹\n"
            "5. **反应放大**：关键时刻后必须有其他角色的反应放大冲击力\n"
            "\n"
            "# THINKING（输出前在脑内 4 步）\n"
            "1. 通读场景，对五项逐条心里打 PASS / FAIL\n"
            "2. 对每条 FAIL，定位到具体段落 / 句子\n"
            "3. ≥ 2 项 FAIL → verdict = rewrite；否则 verdict = pass\n"
            "4. REWRITE_DIRECTION 必须具体（不要写「优化对话」—— 要写「删林渊第 3 句，换成单一动作：拇指碾过铜钱缺口」）\n"
            "\n"
            "# OUTPUT FORMAT（必须 4 行，每行一项，无前缀无后缀）\n"
            "VERDICT: pass 或 rewrite\n"
            "METHODOLOGY: [失败的轴，如「展示不讲述, 尾钩」]\n"
            "REWRITE_DIRECTION: [如需重写，给具体可执行指令；如 pass 则写 N/A]\n"
            "COMMENTARY: [≤2 句编辑意见]"
        )
    )
    if _genre_review_system:
        system_prompt += f"\n\n{'[Genre review requirements]' if is_en else '【品类审核要求】'}\n{_genre_review_system}"
    system_prompt += build_critic_evidence_prompt_suffix(language=language or "zh-CN")
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_scene_review = f"{render_prompt_pack_fragment(prompt_pack, 'scene_review')}\n" if prompt_pack else ""
    _compiled_review = compile_methodology(
        stage=MethodologyStage.REVIEW,
        prompt_pack_key=getattr(prompt_pack, "key", None) if prompt_pack else None,
        language=language or "zh-CN",
        chapter_no=chapter.chapter_number,
        chapter_position=ChapterPosition.UNKNOWN,
        token_budget=2000,
    ).text
    _methodology_review_block = render_methodology_block(prompt_pack, phase="review")
    _methodology_line = ""
    for _block in (_compiled_review, _methodology_review_block):
        if _block and str(_block).strip():
            _methodology_line += f"\n{_block}\n"
    # Quality-levers critic block (scene review). Wrapped in try/except so a
    # malformed meta.yaml never blocks the scene review path.
    try:
        _levers_meta = extract_quality_levers_meta(_project_metadata(project))
        _critic_levers_block = build_critic_quality_levers_block(
            CriticLeverContext(
                chapter_number=chapter.chapter_number,
                language=language or "zh-CN",
                platform=(
                    _levers_meta.target_platform
                    or getattr(writing_profile.market, "platform_target", None)
                ),
                chapter_positions=_levers_meta.positions_for_chapter(
                    chapter.chapter_number
                ),
                distilled_strategy_card=(
                    _project_metadata(project).get("distilled_strategy_card")
                    if isinstance(
                        _project_metadata(project).get("distilled_strategy_card"),
                        dict,
                    )
                    else None
                ),
            )
        )
    except Exception:
        _critic_levers_block = ""
    if _critic_levers_block:
        _methodology_line += f"\n{_critic_levers_block}\n"
    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}\n"
            f"Scene {scene.scene_number}: {scene.title or ''}\n"
            f"Story goal: {scene.purpose.get('story', 'advance the chapter spine')}\n"
            f"Emotional goal: {scene.purpose.get('emotion', 'raise tension')}\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"{_pp_scene_review}"
            f"{_methodology_line}"
            f"Scores: {review_result.scores.model_dump(mode='json')}\n"
            f"Findings: {[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
            f"Current draft:\n{draft.content_md}\n"
            "Write a concise English review note and explain clearly whether the scene needs rewriting. "
            "The verdict must state whether the scene lands the platform promise, reader promise, protagonist edge, and tail hook."
        )
        if is_en
        else (
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章\n"
            f"场景：第{scene.scene_number}场 {scene.title or ''}\n"
            f"场景目标：{scene.purpose.get('story', '推进本章主线')}\n"
            f"情绪目标：{scene.purpose.get('emotion', '拉高当前张力')}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"{_pp_scene_review}"
            f"{_methodology_line}"
            f"当前评分：{review_result.scores.model_dump(mode='json')}\n"
            f"当前发现：{[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
            f"当前草稿：\n{draft.content_md}\n"
            "请用中文输出一段简洁的审校结论，并给出是否需要重写的理由。"
            "结论要明确指出这段文字是否兑现了平台目标、读者承诺、主角卖点和章节尾钩。"
        )
    )
    _genre_review_instruction = getattr(_genre_profile.judge_prompts, f"scene_review_instruction_{_lang_key}", "")
    if _genre_review_instruction:
        user_prompt += f"\n\n{'[Genre review focus]' if is_en else '【品类评审重点】'}\n{_genre_review_instruction}"
    return system_prompt, user_prompt


def build_scene_rewrite_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    current_draft: SceneDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    style_guide: StyleGuideModel | None,
    context_packet: SceneWriterContextPacket | None = None,
    context_budget_tokens: int | None = None,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _project_language(project)
    is_en = is_english_language(language)
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        (
            "# ROLE\n"
            "You are a senior scene-rewrite editor for long-form commercial fiction.\n"
            "Your job is **targeted repair** of a single scene:\n"
            "- preserve scene location, participant list, scene goal, entry/exit state\n"
            "- repair only the issues raised by the rewrite directive\n"
            "- never invent new plot, characters, or settings\n"
            "\n"
            "# CONTEXT\n"
            "You receive: the failing scene draft + a REWRITE_DIRECTION from the scene reviewer +\n"
            "the scene contract + project methodology. The same gate that flagged the issue will\n"
            "re-evaluate your output.\n"
            "\n"
            "# TASK\n"
            "Output the complete rewritten scene in Markdown prose only.\n"
            "No explanations, no apologies, no change logs, no diff markers.\n"
            "\n"
            "# THINKING (in your head before writing)\n"
            "1. Read REWRITE_DIRECTION — map each instruction to a target paragraph\n"
            "2. Decide minimal fix per issue (replace word / rewrite paragraph / cut / add)\n"
            "3. Self-check: would any fix violate scene contract (entry/exit state, participants, length)? If yes, revise\n"
            "4. Preserve unflagged content — do not over-edit\n"
            "\n"
            "# CONSTRAINTS · Hard\n"
            "- Output Markdown prose ONLY\n"
            "- No `#` headings, no ``` fences, no scene labels\n"
            "- Use EXACT character names from the participants list\n"
            "- Word count within 90%-120% of the scene target\n"
            + _NOVEL_OUTPUT_PROHIBITION_EN
            + _REWRITE_STRATEGY_CONTRACT_EN
        )
        if is_en
        else (
            "# ROLE\n"
            "你是长篇中文商业小说写作系统里的资深场景重写编辑。\n"
            "你的工作是**单场定点修复**：\n"
            "- 保留场景位置、参与角色、场景目标、入场 / 退场状态\n"
            "- 只修复 REWRITE_DIRECTION 中列出的问题\n"
            "- 不引入新情节、新角色、新设定\n"
            "\n"
            "# CONTEXT\n"
            "你会收到：失败的场景原稿 + 场景评论者给的 REWRITE_DIRECTION + 场景合同 + 项目方法论。\n"
            "你产出的内容会替换原稿，并被原先标记问题的同一个 gate 重新审。\n"
            "\n"
            "# TASK\n"
            "输出完整一版重写后的场景正文（Markdown）。\n"
            "不要解释、不要道歉、不要列修改清单、不要 diff 标记。\n"
            "\n"
            "# THINKING（动笔前在脑内 4 步）\n"
            "1. 阅读 REWRITE_DIRECTION 的每条指令 — 映射到原稿的具体段落\n"
            "2. 对每条问题决定最小修复手段（换词 / 改段 / 删段 / 补段）\n"
            "3. 自检：修复是否违反场景合同（入 / 退状态、参与者、字数）？违反则回退\n"
            "4. REWRITE_DIRECTION 没标的内容不要乱改\n"
            "\n"
            "# CONSTRAINTS · 硬约束\n"
            "- 仅输出 Markdown 正文\n"
            "- 不带 `#` 标题、不带 ``` 围栏、不带场景标签\n"
            "- 角色名与「参与者」列表完全一致\n"
            "- 字数维持在场景目标的 90%-120%\n"
            + _NOVEL_OUTPUT_PROHIBITION
            + _REWRITE_STRATEGY_CONTRACT
        )
    )
    tone = (
        ", ".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
        if style_guide and style_guide.tone_keywords and is_en
        else (
            "、".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
            if style_guide and style_guide.tone_keywords
            else ("taut, controlled" if is_en else "克制、紧张")
        )
    )
    if is_en:
        if re.search(r"[\u4e00-\u9fff]", tone):
            tone = "taut, controlled"
    elif not re.search(r"[\u4e00-\u9fff]", tone):
        tone = "克制、紧张"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_scene_rewrite = f"{render_prompt_pack_fragment(prompt_pack, 'scene_rewrite')}\n" if prompt_pack else ""
    _material_reference_block = _material_reference_prompt_block(
        project,
        language=language,
    )
    _methodology_scene_block = render_methodology_block(prompt_pack, phase="scene")
    _methodology_rules = render_methodology_scene_rules(
        chapter_number=chapter.chapter_number,
        is_opening=(chapter.chapter_number <= 3),
        is_climax=False,
        pacing_mode="build",
        platform_target=getattr(writing_profile.market, "platform_target", ""),
        language=language,
        rejection_reasons=_project_rejection_reasons(project),
    )
    _methodology_line = ""
    if _methodology_scene_block:
        _methodology_line += f"\n{_methodology_scene_block}\n"
    if _methodology_rules:
        _methodology_line += f"\n{_methodology_rules}\n"
    _qimao_opening_contract_block = _qimao_opening_contract_prompt_block(
        project,
        chapter_number=chapter.chapter_number,
        language=language,
    )
    _rewrite_context_block = _render_scene_rewrite_context_packet_block(
        context_packet,
        language=language,
        max_soft_tokens=context_budget_tokens,
    )
    # ── P1/Retention block injection for rewrite prompts ──
    # build_scene_draft_prompts (fresh write) reads these blocks; rewrite
    # path used to skip them, which is why auto-repair kept regenerating
    # the same broken content. Adding them here closes the loop.
    _rewrite_canon_block = (
        getattr(context_packet, "canon_guardrails_block", None)
        if context_packet
        else None
    ) or ""
    _rewrite_hook_echo_block = (
        getattr(context_packet, "hook_echo_block", None) if context_packet else None
    ) or ""
    _rewrite_signature_block = (
        getattr(context_packet, "signature_scene_block", None)
        if context_packet
        else None
    ) or ""
    _rewrite_voice_dna_block = (
        getattr(context_packet, "voice_dna_block", None) if context_packet else None
    ) or ""
    _rewrite_market_constraints_block = (
        getattr(context_packet, "chapter_market_constraints_block", None)
        if context_packet
        else None
    ) or ""
    _rewrite_exposition_block = (
        getattr(context_packet, "exposition_density_block", None)
        if context_packet
        else None
    ) or ""
    _rewrite_p1_block = "\n\n".join(
        s for s in (
            _rewrite_canon_block,
            _rewrite_hook_echo_block,
            _rewrite_signature_block,
            _rewrite_voice_dna_block,
            _rewrite_market_constraints_block,
            _rewrite_exposition_block,
        ) if s.strip()
    )
    if _rewrite_p1_block:
        _rewrite_p1_block = _rewrite_p1_block + "\n\n"
    # ── Word-count envelope: hard constraint to prevent rewrite-bloat spiral ──
    # The scene writer already enforces a strict word range; the rewriter must
    # enforce the SAME envelope or it will inflate past target on every pass.
    _target_wc = int(scene.target_word_count or 0)
    _current_wc = int(getattr(current_draft, "word_count", 0) or 0)
    _wc_lo = int(_target_wc * 0.9) if _target_wc > 0 else 0
    _wc_hi = int(_target_wc * 1.1) if _target_wc > 0 else 0
    _is_over = _target_wc > 0 and _current_wc > int(_target_wc * 1.2)
    _is_under = _target_wc > 0 and _current_wc < int(_target_wc * 0.8)
    if is_en:
        if _is_over:
            _wc_directive = (
                f"WORD COUNT ENVELOPE (MANDATORY):\n"
                f"- Target: {_target_wc} words (hard range: {_wc_lo}-{_wc_hi})\n"
                f"- Current draft has {_current_wc} words — OVER target by "
                f"{_current_wc - _target_wc} words.\n"
                f"- You MUST TRIM. Remove redundant interiority, repetitive beats, "
                f"over-explanation, and duplicated emotional reactions. "
                f"Preserve core conflict, dialogue spine, and tail hook.\n"
                f"- Outputs outside {_wc_lo}-{_wc_hi} will be rejected.\n"
            ) if _target_wc > 0 else ""
        elif _is_under:
            _wc_directive = (
                f"WORD COUNT ENVELOPE (MANDATORY):\n"
                f"- Target: {_target_wc} words (hard range: {_wc_lo}-{_wc_hi})\n"
                f"- Current draft has {_current_wc} words — UNDER target by "
                f"{_target_wc - _current_wc} words.\n"
                f"- Expand toward the target: deepen conflict, add one concrete beat, "
                f"or sharpen the tail hook. Do NOT pad with summary or repetition.\n"
                f"- Outputs outside {_wc_lo}-{_wc_hi} will be rejected.\n"
            ) if _target_wc > 0 else ""
        else:
            _wc_directive = (
                f"WORD COUNT ENVELOPE (MANDATORY):\n"
                f"- Target: {_target_wc} words (hard range: {_wc_lo}-{_wc_hi})\n"
                f"- Current draft has {_current_wc} words — within range.\n"
                f"- Focused revision only: fix the flagged issues WITHOUT materially "
                f"changing length. Do NOT add or remove more than ~10%.\n"
                f"- Outputs outside {_wc_lo}-{_wc_hi} will be rejected.\n"
            ) if _target_wc > 0 else ""
    else:
        if _is_over:
            _wc_directive = (
                f"【字数闸门·硬性要求】\n"
                f"- 目标：{_target_wc} 字（硬性范围：{_wc_lo}-{_wc_hi}）\n"
                f"- 当前稿字数：{_current_wc}，**超出目标 {_current_wc - _target_wc} 字**。\n"
                f"- 必须【精简】：删除重复的内心独白、复述性铺陈、过度解释、重复的情绪反应。"
                f"保留核心冲突、对话主线、尾钩。\n"
                f"- 输出字数若超出 {_wc_lo}-{_wc_hi} 将被退回。\n"
            ) if _target_wc > 0 else ""
        elif _is_under:
            _wc_directive = (
                f"【字数闸门·硬性要求】\n"
                f"- 目标：{_target_wc} 字（硬性范围：{_wc_lo}-{_wc_hi}）\n"
                f"- 当前稿字数：{_current_wc}，**低于目标 {_target_wc - _current_wc} 字**。\n"
                f"- 适度扩写至目标区间：加深冲突、增加一个具体节拍、或锐化尾钩。"
                f"不要用总结或重复来凑字。\n"
                f"- 输出字数若超出 {_wc_lo}-{_wc_hi} 将被退回。\n"
            ) if _target_wc > 0 else ""
        else:
            _wc_directive = (
                f"【字数闸门·硬性要求】\n"
                f"- 目标：{_target_wc} 字（硬性范围：{_wc_lo}-{_wc_hi}）\n"
                f"- 当前稿字数：{_current_wc}，在范围内。\n"
                f"- 定点修订：只修复被标记的问题，**不得显著改变总字数**（增减幅度不超过 10%）。\n"
                f"- 输出字数若超出 {_wc_lo}-{_wc_hi} 将被退回。\n"
            ) if _target_wc > 0 else ""

    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}\n"
            f"Scene {scene.scene_number}: {scene.title or ''}\n"
            f"{_wrap_rewrite_reference_for_language(rewrite_task.instructions, rewrite_task.rewrite_strategy, language=language)}"
            f"{_wc_directive}"
            f"Chapter goal: {chapter.chapter_goal}\n"
            f"Story goal: {scene.purpose.get('story', 'advance the chapter spine')}\n"
            f"Emotional goal: {scene.purpose.get('emotion', 'raise tension')}\n"
            f"Tone keywords: {tone}\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_scene_rewrite}"
            f"{_material_reference_block}"
            f"{_qimao_opening_contract_block}"
            f"{_methodology_line}"
            f"{_rewrite_p1_block}"
            f"{_rewrite_context_block}"
            f"Current draft:\n{current_draft.content_md}\n"
            "Rewrite the current scene in English only. Fix the flagged issues while "
            "respecting the word-count envelope above. The result should read like "
            "publishable commercial fiction, not planning notes."
        )
        if is_en
        else (
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章\n"
            f"场景：第{scene.scene_number}场 {scene.title or ''}\n"
            f"{_wrap_rewrite_reference_for_language(rewrite_task.instructions, rewrite_task.rewrite_strategy, language=language)}"
            f"{_wc_directive}"
            f"章节目标：{chapter.chapter_goal}\n"
            f"剧情目标：{scene.purpose.get('story', '推进本章主线')}\n"
            f"情绪目标：{scene.purpose.get('emotion', '拉高当前张力')}\n"
            f"语气关键词：{tone}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_scene_rewrite}"
            f"{_material_reference_block}"
            f"{_qimao_opening_contract_block}"
            f"{_methodology_line}"
            f"{_rewrite_p1_block}"
            f"{_rewrite_context_block}"
            f"当前草稿：\n{current_draft.content_md}\n"
            "请按上述字数闸门重写本场景：修复被标记的问题的同时严格控制字数。"
            "要让文本更像平台成品网文，而不是策划草稿或解释说明。"
        )
    )
    _lang_key = "en" if is_en else "zh"
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_rewrite = getattr(_genre_profile.judge_prompts, f"scene_rewrite_instruction_{_lang_key}", "")
    if _genre_rewrite:
        user_prompt += f"\n\n{'[Genre rewrite focus]' if is_en else '【品类重写方向】'}\n{_genre_rewrite}"
    return system_prompt, user_prompt


def _render_scene_rewrite_context_packet_block(
    context_packet: SceneWriterContextPacket | None,
    *,
    language: str | None = None,
    max_soft_tokens: int | None = None,
) -> str:
    if context_packet is None:
        return ""
    is_en = is_english_language(language)
    hard_attrs = (
        "identity_constraint_block",
        "scene_scope_isolation_block",
        "ranking_capability_profile_block",
        "progression_context_block",
        "rule_system_context_block",
        "relationship_agency_context_block",
        "entry_system_context_block",
        "hype_constraints_block",
        "l3_prompt_block",
    )
    soft_attrs = (
        "decision_policy_block",
        "faction_ecology_context_block",
        "entry_registry_context_block",
        "entry_state_ledger_block",
        "genre_constraint_block",
        "overused_phrase_block",
        "reader_contract_block",
        "opening_diversity_block",
        "conflict_diversity_block",
        "scene_purpose_diversity_block",
        "env_diversity_block",
        "arc_beat_block",
        "five_layer_block",
        "cliffhanger_diversity_block",
        "tension_target_block",
        "location_ledger_block",
        "budget_diversity_block",
        "plan_richness_block",
    )
    soft_budget = max(0, int((max_soft_tokens or 8000) * 0.45))
    soft_tokens_used = 0
    omitted: list[str] = []
    hard_parts: list[str] = []
    soft_parts: list[str] = []
    for attr in hard_attrs:
        value = str(getattr(context_packet, attr, "") or "").strip()
        if value:
            hard_parts.append(value)
    for attr in soft_attrs:
        value = str(getattr(context_packet, attr, "") or "").strip()
        if not value:
            continue
        estimated_tokens = _estimate_prompt_tokens(value)
        if soft_tokens_used + estimated_tokens <= soft_budget:
            soft_parts.append(value)
            soft_tokens_used += estimated_tokens
        else:
            omitted.append(attr)
    warnings = [
        str(item).strip()
        for item in (getattr(context_packet, "contradiction_warnings", None) or [])
        if str(item).strip()
    ]
    if warnings:
        label = "Continuity constraints" if is_en else "连续性约束"
        hard_parts.insert(0, f"=== {label} ===\n" + "\n".join(f"- {item}" for item in warnings))
    parts = hard_parts + soft_parts
    if omitted:
        omitted_label = (
            "Context blocks omitted by rewrite prompt budget"
            if is_en
            else "因重写提示词预算省略的上下文块"
        )
        parts.append(f"=== {omitted_label} ===\n" + ", ".join(omitted))
    if not parts:
        return ""
    heading = (
        "=== Scene rewrite context constraints (must obey) ==="
        if is_en
        else "=== 场景重写上下文约束（必须遵守）==="
    )
    footer = (
        "=== End rewrite context constraints ==="
        if is_en
        else "=== 上下文约束结束 ==="
    )
    return f"{heading}\n" + "\n\n".join(parts) + f"\n{footer}\n"


def _estimate_prompt_tokens(text: str) -> int:
    if not text:
        return 0
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    latin_count = len(re.findall(r"[A-Za-z0-9]+(?:['._-][A-Za-z0-9]+)*", text))
    return max(1, int(cjk_count * 1.15 + latin_count * 1.3 + len(text) * 0.03))


def _missing_required_rewrite_context_blocks(
    context_packet: SceneWriterContextPacket | None,
    user_prompt: str,
) -> list[str]:
    if context_packet is None:
        return []
    required_attrs = (
        "identity_constraint_block",
        "ranking_capability_profile_block",
        "progression_context_block",
        "rule_system_context_block",
        "relationship_agency_context_block",
        "entry_system_context_block",
        "hype_constraints_block",
        "l3_prompt_block",
    )
    missing: list[str] = []
    for attr in required_attrs:
        value = str(getattr(context_packet, attr, "") or "").strip()
        if value and value not in user_prompt:
            missing.append(attr)
    return missing


def _render_chapter_context_section(packet, *, language: str | None = None) -> str:
    if packet is None:
        return "No chapter context." if is_english_language(language) else "暂无章节上下文。"
    is_en = is_english_language(language)
    lines: list[str] = []
    # Prepend the hard-fact snapshot (continuity block) so the reviewer/rewriter
    # sees the previous chapter's end-state as the first, most-salient constraint.
    snapshot = getattr(packet, "hard_fact_snapshot", None)
    if snapshot is not None and getattr(snapshot, "facts", None):
        lines.append(
            (
                f"=== Locked fact state (from the end of Chapter {snapshot.chapter_number}; must be obeyed exactly with no contradictions) ==="
                if is_en
                else f"=== 当前事实状态（来自第 {snapshot.chapter_number} 章末 — 必须严格遵守，不得前后矛盾）==="
            )
        )
        for fact in snapshot.facts:
            prefix = f"[{fact.subject}] " if fact.subject else ""
            unit = f" {fact.unit}" if fact.unit else ""
            note = f"  // {fact.notes}" if fact.notes else ""
            lines.append(f"- {prefix}{fact.name}: {fact.value}{unit}{note}")
        lines.append(
            (
                "=== Any change to values, locations, or possessions must have a reader-visible trigger event in this chapter ==="
                if is_en
                else "=== 任何数值/位置/物品变化都必须在本章正文里给出读者可见的触发事件 ==="
            )
        )
    if getattr(packet, "active_plot_arcs", None):
        lines.append("Active narrative lines:" if is_en else "激活叙事线：")
        lines.extend(
            f"- [{item.arc_type}] {item.name}：{item.promise}"
            for item in packet.active_plot_arcs[:4]
        )
    if getattr(packet, "active_arc_beats", None):
        lines.append("Chapter arc beats:" if is_en else "本章叙事节拍：")
        lines.extend(
            f"- {item.arc_code} / {item.beat_kind}：{item.summary}"
            for item in packet.active_arc_beats[:6]
        )
    if getattr(packet, "unresolved_clues", None):
        lines.append("Open clues:" if is_en else "未回收伏笔：")
        lines.extend(
            f"- {item.clue_code}：{item.label}"
            for item in packet.unresolved_clues[:6]
        )
    if getattr(packet, "planned_payoffs", None):
        lines.append("Near-term payoffs:" if is_en else "近期应兑现节点：")
        lines.extend(
            f"- {item.payoff_code}：{item.label}"
            for item in packet.planned_payoffs[:4]
        )
    if getattr(packet, "active_emotion_tracks", None):
        lines.append("Relationship and emotion lines:" if is_en else "关系与情绪线：")
        lines.extend(
            (
                f"- [{item.track_type}] {item.title}：{item.summary}"
                f" / trust={item.trust_level} / conflict={item.conflict_level}"
            )
            for item in packet.active_emotion_tracks[:4]
        )
    if getattr(packet, "active_antagonist_plans", None):
        lines.append("Antagonist pressure:" if is_en else "反派推进：")
        lines.extend(
            (
                f"- [{item.threat_type}] {item.title}：{item.goal}"
                f" / {'current move' if is_en else '当前动作'}:{item.current_move}"
                f" / {'next move' if is_en else '下一步'}:{item.next_countermove}"
            )
            for item in packet.active_antagonist_plans[:4]
        )
    if getattr(packet, "chapter_contract", None):
        lines.append(
            f"{'Chapter contract' if is_en else '章节 contract'}：{packet.chapter_contract.contract_summary}"
        )
        overlay_block = render_overlay_prompt_block(
            chapter_overlay=_json_dict_from_object(packet.chapter_contract),
            language=language,
        )
        if overlay_block:
            lines.append(overlay_block)
        lineage_block = render_methodology_lineage_prompt_block(
            packet.chapter_contract,
            stage="review",
            language=language,
        )
        if lineage_block:
            lines.append(lineage_block)
        profile_block = render_configured_methodology_profile_block(
            stage="review",
            scope="chapter",
            language=language,
        )
        if profile_block:
            lines.append(profile_block)
    if getattr(packet, "tree_context_nodes", None):
        lines.append("Narrative tree context:" if is_en else "叙事树上下文：")
        lines.extend(
            f"- {item.node_path} [{item.node_type}]：{item.summary or item.title}"
            for item in packet.tree_context_nodes[:6]
        )
    if packet.previous_scene_summaries:
        lines.append("Recent story beats:" if is_en else "近期剧情：")
        lines.extend(
            f"- 第{item.chapter_number}章第{item.scene_number}场 {item.scene_title or ''}：{item.summary}"
            for item in packet.previous_scene_summaries[:4]
        )
    if packet.chapter_scenes:
        lines.append("Chapter scene plan:" if is_en else "本章场景计划：")
        lines.extend(
            (
                f"- {('Scene ' + str(item.scene_number)) if is_en else ('第' + str(item.scene_number) + '场')} {item.title or ''} / {item.scene_type} / "
                f"{'story' if is_en else '剧情'}:{item.story_purpose or ('undefined' if is_en else '未定义')} / "
                f"{'emotion' if is_en else '情绪'}:{item.emotion_purpose or ('undefined' if is_en else '未定义')}"
            )
            for item in packet.chapter_scenes
        )
    if packet.recent_timeline_events:
        lines.append("Timeline events:" if is_en else "时间线节点：")
        lines.extend(
            f"- {item.story_time_label} {item.event_name}：{'；'.join(item.consequences) or item.summary or '推进主线'}"
            for item in packet.recent_timeline_events[:4]
        )
    if packet.retrieval_chunks:
        lines.append("Retrieved context:" if is_en else "检索上下文：")
        lines.extend(
            f"- [{item.source_type}] {item.chunk_text}"
            for item in packet.retrieval_chunks[:4]
        )
    return "\n".join(lines)


def _count_scene_headings(content: str) -> int:
    return len(re.findall(r"^##\s*场景\s+\d+", content, flags=re.MULTILINE))


def render_chapter_review_summary(
    review_result: ChapterReviewResult,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    summary_lines = [
        f"{'Verdict' if is_en else '结论'}：{review_result.verdict}",
        f"{'Overall score' if is_en else '总分'}：{review_result.scores.overall}",
        f"{'Top severity' if is_en else '最高严重级别'}：{review_result.severity_max}",
    ]
    if review_result.findings:
        summary_lines.append("Findings:" if is_en else "问题列表：")
        summary_lines.extend(
            f"- [{finding.category}/{finding.severity}] {finding.message}"
            for finding in review_result.findings
        )
    if review_result.rewrite_instructions:
        summary_lines.append(
            f"{'Rewrite instructions' if is_en else '重写要求'}：{review_result.rewrite_instructions}"
        )
    return "\n".join(summary_lines)


def build_chapter_review_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    chapter_context,
    review_result: ChapterReviewResult,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _project_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _resolve_project_writing_profile(project)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_ch_review_system = getattr(_genre_profile.judge_prompts, f"chapter_review_system_{_lang_key}", "")
    system_prompt = (
        (
            "# ROLE\n"
            "You are a senior chapter reviewer for a long-form commercial fiction pipeline.\n"
            "You have audited 500+ chapters for signed novels. You can tell within one read\n"
            "whether a chapter holds the reader for the next click or loses them.\n"
            "\n"
            "# CONTEXT\n"
            "Your verdict drives the chapter rewrite loop: 'pass' = ship, 'rewrite' = trigger LLM regen.\n"
            "REWRITE_DIRECTION will be executed verbatim by the rewrite LLM — make it concrete.\n"
            "\n"
            "# TASK\n"
            "Evaluate the chapter against four methodology axes; emit a fixed-format verdict.\n"
            "\n"
            "# CONSTRAINTS · Four methodology axes\n"
            "1. **Emotion compression / release**: tension must build before any payoff\n"
            "2. **Hook lifecycle**: chapter must plant new hooks AND resolve / advance old ones\n"
            "3. **Conflict stakes**: every conflict has clear stakes (what is lost on failure)\n"
            "4. **Read-on momentum**: chapter ending creates irresistible urge to continue\n"
            "\n"
            "# THINKING (in your head before output)\n"
            "1. Read chapter end-to-end; mark each axis PASS / FAIL\n"
            "2. For each FAIL, locate triggering paragraphs / sentences\n"
            "3. ≥2 axes FAIL → verdict = rewrite; otherwise pass\n"
            "4. REWRITE_DIRECTION must be specific (NOT 'strengthen ending' — 'after final dialogue add a single concrete object cue: 林渊的铜钱缺口又渗出一滴黑水')\n"
            "\n"
            "# OUTPUT FORMAT (4 lines, exact)\n"
            "VERDICT: pass OR rewrite\n"
            "METHODOLOGY: [list of failed axes]\n"
            "REWRITE_DIRECTION: [if rewrite, concrete instructions; if pass, N/A]\n"
            "COMMENTARY: [≤2 sentences editorial note]"
        )
        if is_en
        else (
            "# ROLE\n"
            "你是长篇商业小说审校系统里的资深章节评论者。\n"
            "你审过 500+ 章签约连载，能从一遍读下来就判定这一章能不能留住读者翻下一章。\n"
            "\n"
            "# CONTEXT\n"
            "你的 verdict 驱动章节重写循环：'pass' = 通过，'rewrite' = 触发 LLM 重生成。\n"
            "REWRITE_DIRECTION 会被下游重写 LLM **照字执行**——必须具体、可操作。\n"
            "\n"
            "# TASK\n"
            "按下方四项方法论评估本章，按固定格式输出 verdict。\n"
            "\n"
            "# CONSTRAINTS · 四项方法论轴\n"
            "1. **情绪压缩释放**：爽点前必须有充分的情绪铺垫\n"
            "2. **钩子生命周期**：本章必须植入新钩子，并且推进或消解旧钩子\n"
            "3. **冲突筹码**：每个冲突有明确筹码（输了会失去什么）\n"
            "4. **追读欲**：章末必须制造让读者无法停下的冲动（具体钩子，不是抽象感叹）\n"
            "\n"
            "# THINKING（输出前在脑内 4 步）\n"
            "1. 通读本章，对四项逐条心里打 PASS / FAIL\n"
            "2. 对每条 FAIL，定位到具体段落 / 句子\n"
            "3. ≥ 2 项 FAIL → verdict = rewrite；否则 verdict = pass\n"
            "4. REWRITE_DIRECTION 必须具体（不要写「加强尾钩」—— 要写「在最后对话后追加一个具象物件提示：林渊的铜钱缺口又渗出一滴黑水」）\n"
            "\n"
            "# OUTPUT FORMAT（必须 4 行，每行一项）\n"
            "VERDICT: pass 或 rewrite\n"
            "METHODOLOGY: [失败的轴]\n"
            "REWRITE_DIRECTION: [如需重写，给具体可执行指令；如 pass 则写 N/A]\n"
            "COMMENTARY: [≤2 句编辑意见]"
        )
    )
    if _genre_ch_review_system:
        system_prompt += f"\n\n{'[Genre review requirements]' if is_en else '【品类审核要求】'}\n{_genre_ch_review_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_chapter_review = f"{render_prompt_pack_fragment(prompt_pack, 'chapter_review')}\n" if prompt_pack else ""
    _compiled_review = compile_methodology(
        stage=MethodologyStage.REVIEW,
        prompt_pack_key=getattr(prompt_pack, "key", None) if prompt_pack else None,
        language=language or "zh-CN",
        chapter_no=chapter.chapter_number,
        chapter_position=ChapterPosition.UNKNOWN,
        token_budget=2000,
    ).text
    _methodology_review_block = render_methodology_block(prompt_pack, phase="review")
    _methodology_line = ""
    for _block in (_compiled_review, _methodology_review_block):
        if _block and str(_block).strip():
            _methodology_line += f"\n{_block}\n"
    # Quality-levers critic block (chapter review).
    try:
        _levers_meta = extract_quality_levers_meta(_project_metadata(project))
        _critic_levers_block = build_critic_quality_levers_block(
            CriticLeverContext(
                chapter_number=chapter.chapter_number,
                language=language or "zh-CN",
                platform=(
                    _levers_meta.target_platform
                    or getattr(writing_profile.market, "platform_target", None)
                ),
                chapter_positions=_levers_meta.positions_for_chapter(
                    chapter.chapter_number
                ),
                distilled_strategy_card=(
                    _project_metadata(project).get("distilled_strategy_card")
                    if isinstance(
                        _project_metadata(project).get("distilled_strategy_card"),
                        dict,
                    )
                    else None
                ),
            )
        )
    except Exception:
        _critic_levers_block = ""
    if _critic_levers_block:
        _methodology_line += f"\n{_critic_levers_block}\n"
    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}: {chapter.title or ''}\n"
            f"Chapter goal: {chapter.chapter_goal}\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"{_pp_chapter_review}"
            f"{_methodology_line}"
            f"Context:\n{_render_chapter_context_section(chapter_context, language=language)}\n"
            f"Scores: {review_result.scores.model_dump(mode='json')}\n"
            f"Findings: {[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
            f"Current draft:\n{draft.content_md}\n"
            "Write a concise English chapter review note and explain whether the chapter needs rewriting. "
            "Judge whether the chapter creates real read-on momentum and meets platform-reader expectations."
        )
        if is_en
        else (
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
            f"章节目标：{chapter.chapter_goal}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"{_pp_chapter_review}"
            f"{_methodology_line}"
            f"上下文：\n{_render_chapter_context_section(chapter_context, language=language)}\n"
            f"当前评分：{review_result.scores.model_dump(mode='json')}\n"
            f"当前发现：{[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
            f"当前草稿：\n{draft.content_md}\n"
            "请用中文输出一段简洁的章节审校结论，并给出是否需要重写的理由。"
            "需要判断本章是否真的有追读欲、是否在平台读者预期下足够有吸引力。"
        )
    )
    _genre_ch_review_instruction = getattr(_genre_profile.judge_prompts, f"chapter_review_instruction_{_lang_key}", "")
    if _genre_ch_review_instruction:
        user_prompt += f"\n\n{'[Genre review focus]' if is_en else '【品类评审重点】'}\n{_genre_ch_review_instruction}"
    return system_prompt, user_prompt


def _render_front10_rewrite_contract_block(
    chapter: ChapterModel,
    chapter_context: Any,
    *,
    language: str,
) -> str:
    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    if chapter_number > 10:
        return ""
    is_en = is_english_language(language)
    if is_en:
        return ""
    scene_contexts = list(getattr(chapter_context, "chapter_scenes", None) or [])
    first_scene = scene_contexts[0] if scene_contexts else None
    first_surface = " ".join(
        str(value or "")
        for value in (
            getattr(chapter, "opening_situation", None),
            getattr(first_scene, "title", None) if first_scene is not None else None,
            getattr(first_scene, "story_purpose", None) if first_scene is not None else None,
            getattr(first_scene, "summary", None) if first_scene is not None else None,
        )
    )
    mediated_terms = ("电话", "来电", "手机", "微信", "短信", "语音", "录音")
    lines = [
        "【前十章重写硬合同】",
        "本次是正文重写，不得继承当前草稿中违反章纲/场景卡的错误开场、错误物件信号或错误角色认知。",
    ]
    opening = str(getattr(chapter, "opening_situation", None) or "").strip()
    if opening:
        lines.append(f"第一段必须重新落到这个开场场面：{opening}")
    if first_scene is not None:
        lines.append(
            "第一场锚点："
            + " / ".join(
                item
                for item in (
                    str(getattr(first_scene, "title", "") or "").strip(),
                    str(getattr(first_scene, "story_purpose", "") or "").strip(),
                )
                if item
            )
        )
    if not any(term in first_surface for term in mediated_terms):
        lines.append(
            "第一场合同未规划媒介入场：前500字不得突然新增电话、来电、手机、微信、短信、"
            "语音、录音等桥段来承担召唤或入场逻辑；如果当前草稿使用这些开场，必须补足"
            "来源、转交人、可信原因和到场动机，或改为从第一场现场开写。"
        )
    forbidden_terms = _front10_forbidden_signal_terms(chapter)
    if forbidden_terms:
        lines.append(
            "禁用物件/感官捷径："
            + "、".join(forbidden_terms)
            + "。必须改成稳定可推理的可见变化，例如变冷、变重、裂缺、血点、影子错位或指针偏移。"
        )
    return "\n".join(line for line in lines if line.strip()) + "\n"


def build_chapter_rewrite_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    chapter_context,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _project_language(project)
    is_en = is_english_language(language)
    writing_profile = _resolve_project_writing_profile(project)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        (
            "# ROLE\n"
            "You are a senior chapter-rewrite editor for long-form commercial fiction.\n"
            "Your job is **targeted repair**, not rewriting from scratch:\n"
            "- preserve the chapter's plot bones, character arcs, scene order\n"
            "- repair only the issues raised by the rewrite task\n"
            "- do not invent new plot, characters, or settings\n"
            "\n"
            "# CONTEXT\n"
            "You receive: original draft + rewrite task (issues + fix instructions) + chapter context.\n"
            "Your output replaces the original draft and is judged by the same quality gates that flagged it.\n"
            "\n"
            "# TASK\n"
            "Output the complete rewritten chapter in Markdown prose only.\n"
            "No explanations, no change logs, no diff markers.\n"
            "\n"
            "# THINKING (in your head before writing)\n"
            "1. Read the rewrite_task issues — map each to specific paragraphs in the draft.\n"
            "2. Decide minimal fix per issue (replace word / rewrite paragraph / cut / add).\n"
            "3. Re-check: would any fix introduce a NEW violation (length / forbidden terms / opening hook)? If so, revise.\n"
            "4. Preserve what the task did NOT flag — do not over-edit.\n"
            "\n"
            "# CONSTRAINTS · Hard\n"
            "- Output Markdown prose ONLY (narrative / dialogue / action / environment / thought)\n"
            "- No `# heading` lines, no ``` fences, no scene labels\n"
            "- Keep within the same word count band as the original (90%-120% of target)\n"
            "- Use EXACT character names from the participants list\n"
            + _NOVEL_OUTPUT_PROHIBITION_EN
            + _REWRITE_STRATEGY_CONTRACT_EN
        )
        if is_en
        else (
            "# ROLE\n"
            "你是长篇中文小说写作系统里的章节重写编辑。\n"
            "你的工作是**定点修复**，不是推倒重写：\n"
            "- 保留章节剧情骨架、角色弧线、场景顺序\n"
            "- 只修复 rewrite_task 中列出的问题\n"
            "- 不引入新情节、新角色、新设定\n"
            "\n"
            "# CONTEXT\n"
            "你会收到：原稿正文 + rewrite_task（问题清单 + 修复指令）+ 章节上下文物料。\n"
            "你产出的内容会替换原稿，并被原先标记问题的同一套质量门重新审。\n"
            "\n"
            "# TASK\n"
            "输出完整一版重写后的章节正文（Markdown）。\n"
            "不要解释、不要列修改清单、不要 diff 标记。\n"
            "\n"
            "# THINKING（动笔前在脑内 4 步）\n"
            "1. 阅读 rewrite_task 的每条问题 — 映射到原稿的具体段落\n"
            "2. 对每条问题决定最小修复手段（换词 / 改段 / 删段 / 补段）\n"
            "3. 自检：修复某条问题会不会引入新违规（字数 / 禁词 / 开篇钩子失效）？会就回退方案\n"
            "4. task 没标的内容不要乱改 — 不要过度编辑\n"
            "\n"
            "# CONSTRAINTS · 硬约束\n"
            "- 仅输出 Markdown 正文（叙事 / 对话 / 动作 / 环境 / 内心活动）\n"
            "- 不带 `# 标题` 行、不带 ``` 围栏、不带场景标签\n"
            "- 字数维持在原章目标的 90%-120% 之间\n"
            "- 角色名与「参与者」列表完全一致\n"
            + _NOVEL_OUTPUT_PROHIBITION
            + _REWRITE_STRATEGY_CONTRACT
        )
    )
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_chapter_rewrite = f"{render_prompt_pack_fragment(prompt_pack, 'chapter_rewrite')}\n" if prompt_pack else ""
    _material_reference_block = _material_reference_prompt_block(
        project,
        language=language,
    )
    _methodology_scene_block = render_methodology_block(prompt_pack, phase="scene")
    _methodology_rules = render_methodology_scene_rules(
        chapter_number=chapter.chapter_number,
        is_opening=(chapter.chapter_number <= 3),
        is_climax=False,
        pacing_mode="build",
        platform_target=getattr(writing_profile.market, "platform_target", ""),
        language=language,
        rejection_reasons=_project_rejection_reasons(project),
    )
    _methodology_line = ""
    if _methodology_scene_block:
        _methodology_line += f"\n{_methodology_scene_block}\n"
    if _methodology_rules:
        _methodology_line += f"\n{_methodology_rules}\n"
    _qimao_opening_contract_block = _qimao_opening_contract_prompt_block(
        project,
        chapter_number=chapter.chapter_number,
        language=language,
    )
    _rewrite_p1_block = "\n\n".join(
        s
        for s in (
            getattr(chapter_context, "canon_guardrails_block", None) or "",
            getattr(chapter_context, "hook_echo_block", None) or "",
            getattr(chapter_context, "signature_scene_block", None) or "",
            getattr(chapter_context, "voice_dna_block", None) or "",
            getattr(chapter_context, "chapter_market_constraints_block", None)
            or "",
            getattr(chapter_context, "exposition_density_block", None) or "",
        )
        if str(s).strip()
    )
    if _rewrite_p1_block:
        _rewrite_p1_block = _rewrite_p1_block + "\n\n"
    _settings = get_settings()
    _length_band = chapter_rewrite_length_band(
        _settings,
        getattr(chapter, "target_word_count", None),
        language=language,
        direction="normal",
        role="editor",
    )
    _target_wc = int(_length_band.hard_target)
    _wc_lo = int(_length_band.hard_min)
    _wc_hi = int(_length_band.hard_max)
    _current_wc = int(getattr(current_draft, "word_count", 0) or 0)
    if is_en:
        if _current_wc > _wc_hi:
            _wc_directive = (
                f"WORD COUNT GATE (MANDATORY): current draft {_current_wc}, "
                f"target {_target_wc}, hard publish range {_wc_lo}-{_wc_hi}. "
                "You MUST trim and keep the rewritten chapter inside the hard range. "
                "Do not add new scenes, new exposition, or repeated emotional beats.\n"
            )
        elif _current_wc < _wc_lo:
            _wc_directive = (
                f"WORD COUNT GATE (MANDATORY): current draft {_current_wc}, "
                f"target {_target_wc}, hard publish range {_wc_lo}-{_wc_hi}. "
                "Expand only the missing conflict/action/hook beats until inside range; "
                "do not add unrelated plot.\n"
            )
        else:
            _wc_directive = (
                f"WORD COUNT GATE (MANDATORY): current draft {_current_wc}, "
                f"target {_target_wc}, hard publish range {_wc_lo}-{_wc_hi}. "
                "Keep the rewrite inside this range and preserve roughly the current length. "
                "Fix only the flagged issues.\n"
            )
    else:
        if _current_wc > _wc_hi:
            _safe_band = chapter_rewrite_length_band(
                _settings,
                getattr(chapter, "target_word_count", None),
                language=language,
                direction="over",
                role="editor",
            )
            _safe_lo = _safe_band.safe_min
            _safe_hi = _safe_band.safe_max
            _wc_directive = (
                f"【章节字数闸门·硬性要求】当前稿 {_current_wc} 字，"
                f"目标约 {_target_wc} 字，发布硬范围 {_wc_lo}-{_wc_hi} 字。"
                "内部质量门按中文汉字数计数, 不按模型 token 或段落数计数。"
                f"本次安全输出目标是 {_safe_lo}-{_safe_hi} 个汉字。"
                "必须压缩到硬范围内: 不得新增场景、不得新增解释性铺陈、"
                "不得重复情绪反应, 只保留主冲突、关键动作、必要对白和尾钩。"
                "如果输出超出硬范围, 候选稿会被质量门拒绝。\n"
            )
        elif _current_wc < _wc_lo:
            _safe_band = chapter_rewrite_length_band(
                _settings,
                getattr(chapter, "target_word_count", None),
                language=language,
                direction="under",
                role="editor",
            )
            _safe_lo = _safe_band.safe_min
            _safe_hi = _safe_band.safe_max
            _wc_directive = (
                f"【章节字数闸门·硬性要求】当前稿 {_current_wc} 字，"
                f"目标约 {_target_wc} 字，发布硬范围 {_wc_lo}-{_wc_hi} 字。"
                "内部质量门按中文汉字数计数, 不按模型 token、段落数或主观篇幅计数。"
                f"本次安全输出目标是 {_safe_lo}-{_safe_hi} 个汉字。"
                "必须完整重写并补足缺失的冲突、行动、证物变化、人物反应、代价和尾钩, "
                "让正文进入发布硬范围。不得添加无关支线, 不得用设定解释、重复心理或摘要转述凑字。"
                "如果输出低于硬范围, 候选稿会被质量门拒绝。\n"
            )
        else:
            _safe_band = chapter_rewrite_length_band(
                _settings,
                getattr(chapter, "target_word_count", None),
                language=language,
                direction="normal",
                role="editor",
            )
            _safe_lo = _safe_band.safe_min
            _safe_hi = _safe_band.safe_max
            _wc_directive = (
                f"【章节字数闸门·硬性要求】当前稿 {_current_wc} 字，"
                f"目标约 {_target_wc} 字，发布硬范围 {_wc_lo}-{_wc_hi} 字。"
                "内部质量门按中文汉字数计数。重写后必须仍在硬范围内, "
                f"安全输出目标是 {_safe_lo}-{_safe_hi} 个汉字。"
                "只修复被标记的问题, 不要把章节写短成梗概, 也不要扩成长段解释。\n"
            )
    _ending_frame_gate = (
        "ENDING FRAME GATE (MANDATORY): The last sentence must remain inside the "
        "active scene and land on a completed visible frame: a concrete action, "
        "object change, threat movement, or protagonist choice. If the hook is "
        "dialogue, add one visual/action sentence after the dialogue. Never end "
        "with a bare quote, abstract explanation, or unresolved ongoing motion.\n"
        if is_en
        else (
            "【章末画面帧闸门·硬性要求】最后一句必须仍在当前场景内，并落在一个完成的可视化画面："
            "人物动作、物件变化、威胁逼近、证据显现或主角选择。若章末钩子是对白，"
            "必须在对白之后再补一句现场动作/物件变化作为最后帧；禁止最后一句只是台词、"
            "抽象解释、设定总结，或仍悬在未完成的进行中动作。章末只能保留一个主钩子，"
            "最多一个辅助信息；不得连续堆叠电梯异象、门缝渗水、短信、电话、新人名、"
            "账页弹窗等多个未解悬念。选择最服务下一章的钩子，并把最后一句写成已完成的"
            "画面定格、物件状态变化或主角明确选择。\n"
        )
    )
    _scene_transition_gate = (
        "SCENE TRANSITION GATE (MANDATORY): Do not use horizontal rules, section "
        "breaks, or blank cuts to change scene. Every location or time change must "
        "include one visible bridge action before the new location appears.\n"
        if is_en
        else (
            "【场景转场闸门·硬性要求】禁止用 ---、***、小节分隔符或空行硬切换场景。"
            "每次地点或时间变化，必须先写一句可见转场动作，例如挂断电话、出门、下楼、"
            "电梯移动、门牌变化、时间跳动或物件反应，再进入新地点。\n"
        )
    )
    _front10_rewrite_contract_block = _render_front10_rewrite_contract_block(
        chapter,
        chapter_context,
        language=language,
    )
    _quality_uplift_rewrite_block = _render_quality_uplift_rewrite_block(chapter)
    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}: {chapter.title or ''}\n"
            f"Chapter goal: {chapter.chapter_goal}\n"
            f"{_quality_uplift_rewrite_block}"
            f"{_wrap_rewrite_reference_for_language(rewrite_task.instructions, rewrite_task.rewrite_strategy, language=language)}"
            f"{_wc_directive}"
            f"{_ending_frame_gate}"
            f"{_scene_transition_gate}"
            f"{_front10_rewrite_contract_block}"
            f"{_SINGLE_PASS_CHAPTER_REWRITE_CONTRACT_EN}"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_chapter_rewrite}"
            f"{_material_reference_block}"
            f"{_qimao_opening_contract_block}"
            f"{_methodology_line}"
            f"Chapter context:\n{_render_chapter_context_section(chapter_context, language=language)}\n"
            f"{_rewrite_p1_block}"
            f"Current draft:\n{current_draft.content_md}\n"
            "Rewrite the chapter in English only while preserving the core event order. Improve transitions, chapter propulsion, and the ending hook first."
        )
        if is_en
        else (
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
            f"章节目标：{chapter.chapter_goal}\n"
            f"{_quality_uplift_rewrite_block}"
            f"{_wrap_rewrite_reference_for_language(rewrite_task.instructions, rewrite_task.rewrite_strategy, language=language)}"
            f"{_wc_directive}"
            f"{_ending_frame_gate}"
            f"{_scene_transition_gate}"
            f"{_front10_rewrite_contract_block}"
            f"{_SINGLE_PASS_CHAPTER_REWRITE_CONTRACT}"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_chapter_rewrite}"
            f"{_material_reference_block}"
            f"{_qimao_opening_contract_block}"
            f"{_methodology_line}"
            f"章节上下文：\n{_render_chapter_context_section(chapter_context, language=language)}\n"
            f"{_rewrite_p1_block}"
            f"当前草稿：\n{current_draft.content_md}\n"
            "请在保留本章核心事件顺序的前提下，重写本章，使场景衔接更顺、章节推进更完整、收尾钩子更明确。"
            "优先强化读者追更欲、爽点兑现、人设辨识和节奏推进。"
        )
    )
    _lang_key = "en" if is_en else "zh"
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_ch_rewrite = getattr(_genre_profile.judge_prompts, f"chapter_rewrite_instruction_{_lang_key}", "")
    if _genre_ch_rewrite:
        user_prompt += f"\n\n{'[Genre rewrite focus]' if is_en else '【品类重写方向】'}\n{_genre_ch_rewrite}"
    return system_prompt, user_prompt


def _render_quality_uplift_rewrite_block(chapter: ChapterModel) -> str:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    blocks = metadata.get("quality_uplift_prompt_blocks")
    rewrite_escalation = metadata.get("rewrite_escalation")
    parts: list[str] = []
    if isinstance(rewrite_escalation, dict):
        directive = str(rewrite_escalation.get("strict_directive") or "").strip()
        if directive:
            parts.append(directive)
    if isinstance(blocks, dict):
        for key in ("pre_scene", "post_scene"):
            block = str(blocks.get(key) or "").strip()
            if block:
                parts.append(block)
    return ("\n".join(parts) + "\n") if parts else ""


# ── Dialogue distinctiveness measurement (mechanical, zero LLM cost) ────

_DIALOGUE_RE = re.compile(r"\u201c([^\u201d]*)\u201d")
_SPEAKER_RE = re.compile(
    r"([\u4e00-\u9fff]{1,4})\s*(?:说|道|问|喊|笑|叹|嘟囔|低声|冷声|沉声|厉声|轻声|淡淡|缓缓)"
)


def _measure_dialogue_distinctiveness(
    content_md: str,
    participants: list[str],
) -> float:
    """Mechanical dialogue distinctiveness score (0-1).

    Extracts dialogue lines, attributes them to speakers, and measures
    how different each speaker's dialogue is from others using sentence
    length variance and vocabulary overlap.

    Returns 1.0 if < 2 speakers detected (no distinctiveness to measure).
    """
    if not content_md or not participants:
        return 1.0

    # Extract all dialogue lines
    dialogues = _DIALOGUE_RE.findall(content_md)
    if len(dialogues) < 4:
        return 1.0  # too few dialogue lines to measure

    # Attribute dialogue to speakers by scanning context before each quote
    speaker_dialogues: dict[str, list[str]] = {}
    for match in re.finditer(r"([\u4e00-\u9fff]{1,4})\s*(?:说|道|问|喊|笑|叹|嘟囔|低声|冷声|沉声|厉声|轻声|淡淡|缓缓)[^，。]*?[，：:]\s*\u201c([^\u201d]*)\u201d", content_md):
        speaker = match.group(1)
        line = match.group(2)
        # Only count known participants
        matched_participant = next(
            (p for p in participants if speaker in p or p in speaker), None
        )
        if matched_participant and line.strip():
            speaker_dialogues.setdefault(matched_participant, []).append(line)

    if len(speaker_dialogues) < 2:
        return 1.0  # only one speaker identified

    # Measure per-speaker characteristics
    speaker_stats: dict[str, tuple[float, set[str]]] = {}
    for speaker, lines in speaker_dialogues.items():
        if not lines:
            continue
        # Average sentence length (chars)
        avg_len = sum(len(line) for line in lines) / len(lines)
        # Unique 2-char bigrams as vocabulary fingerprint
        bigrams: set[str] = set()
        for line in lines:
            for i in range(len(line) - 1):
                bigrams.add(line[i : i + 2])
        speaker_stats[speaker] = (avg_len, bigrams)

    if len(speaker_stats) < 2:
        return 1.0

    # Distinctiveness: average pairwise Jaccard distance of bigram sets
    # + sentence length variance
    speakers = list(speaker_stats.keys())
    total_jaccard_dist = 0.0
    total_len_diff = 0.0
    pair_count = 0
    for i in range(len(speakers)):
        for j in range(i + 1, len(speakers)):
            len_a, bigrams_a = speaker_stats[speakers[i]]
            len_b, bigrams_b = speaker_stats[speakers[j]]
            # Jaccard distance (0 = identical, 1 = completely different)
            union = bigrams_a | bigrams_b
            if union:
                jaccard_dist = 1.0 - len(bigrams_a & bigrams_b) / len(union)
            else:
                jaccard_dist = 0.0
            # Normalized sentence length difference
            max_len = max(len_a, len_b, 1)
            len_diff = abs(len_a - len_b) / max_len
            total_jaccard_dist += jaccard_dist
            total_len_diff += len_diff
            pair_count += 1

    if pair_count == 0:
        return 1.0

    avg_jaccard = total_jaccard_dist / pair_count
    avg_len_diff = total_len_diff / pair_count
    # Combine: 70% vocabulary distinctiveness + 30% sentence length difference
    return min(avg_jaccard * 0.7 + avg_len_diff * 0.3, 1.0)


def evaluate_scene_draft(
    *,
    scene: SceneCardModel,
    chapter: ChapterModel,
    draft: SceneDraftVersionModel,
    settings: AppSettings,
    chapter_contract: Any | None = None,
    scene_contract: Any | None = None,
    scene_context: Any | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str | None = None,
    pacing_target: Any | None = None,
    subplot_schedule: list[Any] | None = None,
    swain_pattern: str | None = None,
    duplication_score: float = 1.0,
    duplication_findings: list[dict[str, Any]] | None = None,
) -> SceneReviewResult:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    profile = resolve_genre_review_profile(genre or "", sub_genre)
    _is_en = is_english_language(language)
    _lang_key = "en" if _is_en else "zh"
    _genre_conflict_kw = getattr(profile.signal_keywords, f"conflict_terms_{_lang_key}", [])
    _genre_emotion_kw = getattr(profile.signal_keywords, f"emotion_terms_{_lang_key}", [])
    _genre_hook_kw = getattr(profile.signal_keywords, f"hook_terms_{_lang_key}", [])
    _genre_info_kw = getattr(profile.signal_keywords, f"info_terms_{_lang_key}", [])

    content = draft.content_md
    target_ratio = draft.word_count / max(scene.target_word_count, 1)
    goal = _clamp_score(target_ratio)
    tail_excerpt = _tail_excerpt(content)
    meta_leak = has_meta_leak(content)
    dialogue_markers = content.count("“") + content.count("”")
    dialogue_distinctiveness = _measure_dialogue_distinctiveness(
        content, list(scene.participants or [])
    )

    # ── Identity consistency check (zero LLM cost) ──
    _identity_score = 1.0
    _identity_violation_count = 0
    try:
        from bestseller.services.identity_guard import validate_scene_text_identity

        _id_registry = getattr(scene_context, "identity_registry", []) if scene_context else []
        if _id_registry:
            _violations = validate_scene_text_identity(
                content,
                _id_registry,
                language=language or "zh-CN",
                participant_names=list(scene.participants or []),
            )
            _identity_violation_count = len(_violations)
            # Each violation reduces score; critical violations reduce more
            for v in _violations:
                if v.severity == "critical":
                    _identity_score -= 0.3
                else:
                    _identity_score -= 0.15
            _identity_score = max(0.0, _identity_score)
    except Exception:
        pass  # Non-fatal — defaults to 1.0

    # ── POV consistency check (zero LLM cost) ──
    _pov_score = 1.0
    try:
        _pov_type = getattr(scene_context, "pov_type", None) if scene_context else None
        if not _pov_type:
            # Fallback: check style_guide or default
            _pov_type = "third-limited"
        _pov_type_lower = _pov_type.lower()
        if "first" in _pov_type_lower:
            # First person: should have "I", "my", "me"; should NOT have omniscient thoughts of other chars
            _i_count = len(re.findall(r'\bI\b', content)) if _is_en else content.count("我")
            if _i_count < 3:
                _pov_score -= 0.3  # Too few first-person markers
        elif "third" in _pov_type_lower and "limited" in _pov_type_lower:
            # Third-limited: should NOT reveal thoughts of non-POV characters
            # Check for multiple "X thought/想" with different character names
            _pov_chars = [p for p in (scene.participants or []) if p]
            if _pov_chars:
                _pov_char = _pov_chars[0]  # Assume first participant is POV
                _thought_markers_zh = ["心想", "暗想", "心中", "想到", "心道"]
                _thought_markers_en = [" thought,", " wondered,", " realized ", " knew that"]
                _markers = _thought_markers_zh if not _is_en else _thought_markers_en
                _other_chars = [p for p in _pov_chars[1:] if p]
                _omniscient_leaks = 0
                for oc in _other_chars:
                    for marker in _markers:
                        # Check if other character name appears near a thought marker
                        _pattern = f"{re.escape(oc)}.{{0,20}}{re.escape(marker)}"
                        if re.search(_pattern, content):
                            _omniscient_leaks += 1
                if _omniscient_leaks > 0:
                    _pov_score -= min(0.5, _omniscient_leaks * 0.15)
        _pov_score = max(0.0, _pov_score)
    except Exception:
        pass  # Non-fatal

    # ── Scene transition quality check ──
    _transition_score = 0.5  # Default neutral
    try:
        _entry_state = scene.entry_state or ""
        _exit_state = scene.exit_state or ""
        if _entry_state:
            # Check if the entry state conditions are reflected in the first 500 chars
            _opening = content[:500].lower()
            _entry_keywords = [w.strip() for w in _entry_state.lower().split(",") if len(w.strip()) > 2]
            _entry_hits = sum(1 for kw in _entry_keywords if kw in _opening)
            if _entry_keywords:
                _transition_score = _clamp_score(_entry_hits / max(len(_entry_keywords), 1) * 0.8 + 0.2)
        if _exit_state:
            # Check if exit state is reflected in the last 500 chars
            _closing = content[-500:].lower()
            _exit_keywords = [w.strip() for w in _exit_state.lower().split(",") if len(w.strip()) > 2]
            _exit_hits = sum(1 for kw in _exit_keywords if kw in _closing)
            if _exit_keywords:
                _exit_ratio = _exit_hits / max(len(_exit_keywords), 1) * 0.8 + 0.2
                _transition_score = _clamp_score((_transition_score + _exit_ratio) / 2)
    except Exception:
        pass

    participants_present = sum(
        1 for participant in scene.participants if participant and participant in content
    )
    emotion_phrase = str(scene.purpose.get("emotion", "")).strip()
    story_purpose = str(scene.purpose.get("story", "")).strip()
    conflict_signal = _signal_score(
        content,
        keywords=[
            story_purpose,
            getattr(scene_contract, "core_conflict", None),
            scene.scene_type,
            *_CONFLICT_SIGNAL_TERMS,
            *_genre_conflict_kw,
        ],
    )
    emotion_signal = _signal_score(
        content,
        keywords=[
            emotion_phrase,
            getattr(scene_contract, "emotional_shift", None),
            *_EMOTION_SIGNAL_TERMS,
            *_genre_emotion_kw,
        ],
    )
    info_signal = _signal_score(
        content,
        keywords=[
            getattr(scene_contract, "information_release", None),
            *_INFO_SIGNAL_TERMS,
            *_genre_info_kw,
        ],
    )
    tail_tension_signal = _signal_score(
        tail_excerpt,
        keywords=[
            getattr(scene_contract, "tail_hook", None),
            getattr(chapter_contract, "closing_hook", None),
            *_HOOK_SIGNAL_TERMS,
            *_genre_hook_kw,
        ],
    )

    conflict = _clamp_score(
        0.22
        + min(0.2, participants_present * 0.1)
        + min(0.18, dialogue_markers * 0.09)
        + (0.15 if draft.word_count >= int(scene.target_word_count * 0.22) else 0.0)
        + (conflict_signal * 0.28)
    )
    emotion = _clamp_score(
        0.24
        + (0.12 if draft.word_count >= int(scene.target_word_count * 0.24) else 0.0)
        + (emotion_signal * 0.44)
    )
    dialogue = _clamp_score(
        0.22
        + min(0.4, dialogue_markers * 0.1)
        + (0.12 if any(term in content for term in _SPEECH_SIGNAL_TERMS) else 0.0)
        + (0.1 if "？" in content or "?" in content else 0.0)
        + (0.08 if participants_present >= 2 else 0.0)
    )
    style_penalty = 0.15 if "。。" in content or ".." in content else 0.0
    content_lower = content.lower()
    meta_penalty = 0.12 if (
        any(term in content for term in _META_REWARD_TERMS)
        or any(term in content_lower for term in _EN_META_REWARD_TERMS)
    ) else 0.0
    # System UI panel overuse penalty (LitRPG code blocks)
    _code_block_count = content.count("```") // 2  # pairs of triple backticks
    _system_panel_penalty = max(0.0, (_code_block_count - 3) * 0.06) if _code_block_count > 3 else 0.0
    # AI cliché penalty
    _ai_cliche_count = sum(1 for phrase in _AI_CLICHE_TERMS if phrase in content_lower)
    _ai_cliche_penalty = min(0.2, _ai_cliche_count * 0.04)
    style = _clamp_score(
        0.74
        + (0.08 if not meta_leak else -0.22)
        - meta_penalty
        - style_penalty
        - _system_panel_penalty
        - _ai_cliche_penalty
    )

    hook = _clamp_score(
        0.28
        + (tail_tension_signal * 0.58)
        + (0.1 if "？" in tail_excerpt or "?" in tail_excerpt else 0.0)
        + (0.08 if len(tail_excerpt) >= 80 else 0.0)
    )
    tail_hook_score = _keyword_score(
        tail_excerpt,
        keywords=[
            getattr(scene_contract, "tail_hook", None),
            getattr(chapter_contract, "closing_hook", None),
            "真相",
            "危机",
            "倒计时",
            "下一秒",
            *_HOOK_SIGNAL_TERMS,
        ],
    )
    conflict_contract_score = _keyword_score(
        content,
        keywords=[
            getattr(scene_contract, "core_conflict", None),
            story_purpose,
            *_CONFLICT_SIGNAL_TERMS,
        ],
    )
    emotional_shift_score = _keyword_score(
        content,
        keywords=[
            getattr(scene_contract, "emotional_shift", None),
            emotion_phrase,
            *_EMOTION_SIGNAL_TERMS,
        ],
    )
    payoff_density_signal = _keyword_score(
        content,
        keywords=[
            getattr(scene_contract, "information_release", None),
            *(getattr(scene_contract, "payoff_codes", []) or []),
            *(getattr(scene_context, "planned_payoffs", []) and [
                getattr(item, "label", None)
                for item in getattr(scene_context, "planned_payoffs", [])[:3]
            ] or []),
            "真相",
            "终于",
            *_INFO_SIGNAL_TERMS,
        ],
    )
    voice_signal = _keyword_score(
        content,
        keywords=[
            "克制",
            "紧张",
            "压迫",
            "利落",
            "追问",
            "反击",
            "冷冷",
            "沉声",
        ],
    )
    hook_strength = _clamp_score(
        hook * 0.3
        + max(tail_tension_signal, tail_hook_score or 0.0) * 0.7
    )
    conflict_clarity = _clamp_score(
        conflict * 0.45
        + max(conflict_signal, conflict_contract_score or 0.0) * 0.55
    )
    emotional_movement = _clamp_score(
        emotion * 0.35
        + max(emotion_signal, emotional_shift_score or 0.0, conflict_signal * 0.8) * 0.65
    )
    payoff_density = _clamp_score(
        0.32
        + (0.15 if draft.word_count >= int(scene.target_word_count * 0.8) else 0.0)
        + (max(info_signal, payoff_density_signal or 0.0) * 0.62)
    )
    voice_consistency = _clamp_score(
        style * 0.62
        + ((voice_signal or 0.0) * 0.14)
        + (dialogue_distinctiveness * 0.14)
        + (0.1 if not meta_leak else 0.0)
    )

    # ── Phase-1: pacing alignment & subplot presence ──
    _pacing_tension = getattr(pacing_target, "tension_level", None)
    if _pacing_tension is not None:
        _draft_tension = (conflict_signal + emotion_signal) / 2
        _pacing_deviation = abs(_draft_tension - _pacing_tension)
        pacing_alignment_score = _clamp_score(1.0 - _pacing_deviation * 1.6)
    else:
        pacing_alignment_score = 0.5

    _primary_arcs = [
        entry
        for entry in (subplot_schedule or [])
        if getattr(entry, "prominence", None) == "primary"
    ]
    if _primary_arcs:
        _arc_hits = sum(
            1
            for arc in _primary_arcs
            if getattr(arc, "arc_label", None) and getattr(arc, "arc_label", "") in content
        )
        subplot_presence_score = _clamp_score(0.3 + _arc_hits / max(len(_primary_arcs), 1) * 0.7)
    else:
        subplot_presence_score = 0.5

    # ── Phase-3: scene/sequel alignment ──
    _ACTION_SIGNAL_TERMS = ["冲突", "对抗", "追击", "逼", "挡", "clash", "fight", "confront"]
    _SEQUEL_SIGNAL_TERMS = ["犹豫", "回想", "抉择", "沉思", "hesitat", "reflect", "dilemma"]
    if swain_pattern == "action":
        _swain_signal = _signal_score(content, keywords=_ACTION_SIGNAL_TERMS)
        scene_sequel_alignment_score = _clamp_score(0.3 + _swain_signal * 0.7)
    elif swain_pattern == "sequel":
        _swain_signal = _signal_score(content, keywords=_SEQUEL_SIGNAL_TERMS)
        scene_sequel_alignment_score = _clamp_score(0.3 + _swain_signal * 0.7)
    else:
        scene_sequel_alignment_score = 0.5

    # ── Phase-6: methodology compliance (show-don't-tell, sensory richness) ──
    _DIRECT_EMOTION_WORDS_ZH = [
        "愤怒", "伤心", "高兴", "害怕", "紧张", "激动", "失望", "焦虑",
        "恐惧", "悲伤", "开心", "兴奋", "惊讶", "沮丧", "绝望", "愉悦",
    ]
    _DIRECT_EMOTION_WORDS_EN = [
        "angry", "sad", "happy", "afraid", "nervous", "excited", "disappointed",
        "anxious", "scared", "heartbroken", "thrilled", "shocked", "depressed",
    ]
    _PHYSICAL_ACTION_WORDS_ZH = [
        "攥", "掐", "捏", "握", "咬", "颤", "抖", "抿", "蹙", "皱",
        "踹", "摔", "撕", "扯", "瞪", "盯", "甩", "拳", "指甲", "拳头",
    ]
    _PHYSICAL_ACTION_WORDS_EN = [
        "clench", "grip", "tremble", "shudder", "bite", "flinch", "slam",
        "squeeze", "fist", "jaw", "nails", "knuckle", "swallow",
    ]
    _SENSORY_ZH = {
        "visual": ["看", "望", "瞥", "盯", "光", "暗", "影", "色"],
        "auditory": ["听", "声", "响", "嗡", "吼", "嘶", "呢喃", "沉默"],
        "tactile": ["触", "烫", "冷", "滑", "粗糙", "刺", "温", "冰"],
        "olfactory": ["闻", "味", "香", "臭", "腥", "酸", "膻"],
        "gustatory": ["尝", "咸", "甜", "苦", "涩", "辣"],
    }
    _SENSORY_EN = {
        "visual": ["see", "saw", "glow", "shadow", "bright", "dark", "flicker"],
        "auditory": ["hear", "heard", "sound", "whisper", "roar", "silence", "echo"],
        "tactile": ["touch", "cold", "warm", "rough", "smooth", "sting", "burn"],
        "olfactory": ["smell", "scent", "stench", "fragrant", "reek"],
        "gustatory": ["taste", "bitter", "sweet", "sour", "salty"],
    }

    _tell_words = _DIRECT_EMOTION_WORDS_EN if _is_en else _DIRECT_EMOTION_WORDS_ZH
    _show_words = _PHYSICAL_ACTION_WORDS_EN if _is_en else _PHYSICAL_ACTION_WORDS_ZH
    _tell_count = sum(1 for w in _tell_words if w in content)
    _show_count = sum(1 for w in _show_words if w in content)
    _total_st = _tell_count + _show_count
    if _total_st > 0:
        _show_ratio = _show_count / _total_st
        show_dont_tell_score = _clamp_score(0.3 + _show_ratio * 0.7)
    else:
        show_dont_tell_score = 0.5

    _sensory_map = _SENSORY_EN if _is_en else _SENSORY_ZH
    _channels_used = sum(
        1 for terms in _sensory_map.values()
        if any(t in content for t in terms)
    )
    sensory_richness_score = _clamp_score(0.1 + _channels_used * 0.18)

    methodology_compliance_score = _clamp_score(
        show_dont_tell_score * 0.6 + sensory_richness_score * 0.4
    )

    contract_alignment, contract_evidence = _evaluate_contract_alignment(
        content,
        expectations=_scene_contract_expectations(
            chapter_contract=chapter_contract,
            scene_contract=scene_contract,
        ),
        label_weights={
            "scene_summary": 0.65,
            "chapter_summary": 0.65,
            "core_conflict": 1.15,
            "emotional_shift": 1.0,
            "information_release": 1.1,
            "tail_hook": 1.1,
            "closing_hook": 1.1,
            "conflict_stakes": 1.0,
            "conflict_buffs": 1.0,
            "signature_image": 1.05,
            "cut_point": 1.05,
            "relationship_debts": 1.0,
            "action_sequence": 0.85,
        },
        label_floors={
            "scene_summary": _clamp_score(
                max(conflict_clarity, emotional_movement, payoff_density, hook_strength) * 0.9
            ),
            "chapter_summary": _clamp_score(
                max(conflict_clarity, emotional_movement, payoff_density, hook_strength) * 0.9
            ),
            "core_conflict": conflict_clarity,
            "emotional_shift": emotional_movement,
            "information_release": max(payoff_density, info_signal),
            "tail_hook": hook_strength,
            "closing_hook": hook_strength,
        },
    )
    methodology_evidence = _methodology_lineage_evidence_summary(
        content,
        chapter_contract,
    )
    _sw = profile.scene_weights
    weighted_parts = [
        (goal, _sw.goal),
        (conflict, _sw.conflict),
        (conflict_clarity, _sw.conflict_clarity),
        (emotion, _sw.emotion),
        (emotional_movement, _sw.emotional_movement),
        (dialogue, _sw.dialogue),
        (style, _sw.style),
        (voice_consistency, _sw.voice_consistency),
        (hook, _sw.hook),
        (hook_strength, _sw.hook_strength),
        (payoff_density, _sw.payoff_density),
    ]
    if int(contract_evidence["contract_expectation_count"]) > 0:
        weighted_parts.append((contract_alignment, _sw.contract_alignment))
    if pacing_target is not None:
        weighted_parts.append((pacing_alignment_score, _sw.pacing_alignment))
    if _primary_arcs:
        weighted_parts.append((subplot_presence_score, _sw.subplot_presence))
    if swain_pattern is not None:
        weighted_parts.append((scene_sequel_alignment_score, _sw.scene_sequel_alignment))
    weighted_parts.append((methodology_compliance_score, _sw.methodology_compliance))
    _total_weight = sum(w for _, w in weighted_parts)
    overall = _clamp_score(sum(s * w for s, w in weighted_parts) / max(_total_weight, 0.01))
    _base_threshold = profile.scene_threshold_override or settings.quality.thresholds.scene_min_score
    threshold = _base_threshold

    # ── Opening chapter quality amplification ──
    # Chapters 1-3 use a higher quality bar for the overall verdict,
    # but individual findings still use the base threshold.
    _is_opening_chapter = chapter.chapter_number <= 3
    _verdict_threshold = _base_threshold
    if _is_opening_chapter:
        _verdict_threshold = max(_base_threshold, min(_base_threshold + 0.08, 0.85))

    _fm = profile.finding_messages
    findings: list[SceneReviewFinding] = []
    if goal < threshold:
        findings.append(
            SceneReviewFinding(
                category="goal",
                severity=_severity_from_score(goal),
                message=(
                    f"Current scene word count {draft.word_count} is clearly "
                    f"below target {scene.target_word_count} — the scene does "
                    f"not develop the task enough."
                    if _is_en
                    else
                    f"当前场景字数为 {draft.word_count}，明显低于目标字数 {scene.target_word_count}，"
                    "推进任务展开不够充分。"
                ),
            )
        )
    # Over-length check: flag scenes that exceed target by >30%
    if target_ratio > 1.3 and scene.target_word_count > 0:
        _over_severity = "high" if target_ratio > 1.6 else "medium"
        findings.append(
            SceneReviewFinding(
                category="goal",
                severity=_over_severity,
                message=(
                    f"Current scene word count {draft.word_count} exceeds "
                    f"target {scene.target_word_count} by "
                    f"{int((target_ratio - 1) * 100)}%. The scene is too long "
                    f"and must be trimmed."
                    if _is_en
                    else
                    f"当前场景字数为 {draft.word_count}，超出目标字数 {scene.target_word_count} "
                    f"达 {int((target_ratio - 1) * 100)}%。内容过长，需要精简。"
                ),
            )
        )
    if conflict < threshold:
        findings.append(
            SceneReviewFinding(
                category="conflict",
                severity=_severity_from_score(conflict),
                message=getattr(_fm, f"conflict_low_{_lang_key}"),
            )
        )
    if conflict_clarity < threshold:
        findings.append(
            SceneReviewFinding(
                category="conflict_clarity",
                severity=_severity_from_score(conflict_clarity),
                message=getattr(_fm, f"conflict_clarity_low_{_lang_key}"),
            )
        )
    if emotion < threshold:
        findings.append(
            SceneReviewFinding(
                category="emotion",
                severity=_severity_from_score(emotion),
                message=getattr(_fm, f"emotion_low_{_lang_key}"),
            )
        )
    if emotional_movement < threshold:
        findings.append(
            SceneReviewFinding(
                category="emotional_movement",
                severity=_severity_from_score(emotional_movement),
                message=getattr(_fm, f"emotional_movement_low_{_lang_key}"),
            )
        )
    if dialogue < threshold:
        findings.append(
            SceneReviewFinding(
                category="dialogue",
                severity=_severity_from_score(dialogue),
                message=getattr(_fm, f"dialogue_low_{_lang_key}"),
            )
        )
    if hook_strength < threshold:
        findings.append(
            SceneReviewFinding(
                category="hook_strength",
                severity=_severity_from_score(hook_strength),
                message=getattr(_fm, f"hook_low_{_lang_key}"),
            )
        )
    if payoff_density < threshold:
        findings.append(
            SceneReviewFinding(
                category="payoff_density",
                severity=_severity_from_score(payoff_density),
                message=getattr(_fm, f"payoff_low_{_lang_key}"),
            )
        )
    if voice_consistency < threshold:
        findings.append(
            SceneReviewFinding(
                category="voice_consistency",
                severity=_severity_from_score(voice_consistency),
                message=getattr(_fm, f"voice_low_{_lang_key}"),
            )
        )
    if int(contract_evidence["contract_expectation_count"]) > 0 and contract_alignment < threshold:
        missing_labels = list(contract_evidence["contract_missing_labels"])
        _contract_base = getattr(_fm, f"contract_low_{_lang_key}")
        findings.append(
            SceneReviewFinding(
                category="contract_alignment",
                severity=_severity_from_score(contract_alignment),
                message=(
                    _contract_base
                    + (f" 缺失要点：{', '.join(missing_labels)}。" if missing_labels else "")
                ),
            )
        )

    # Character name consistency: detect if the LLM introduced unexpected
    # character names that look like variants of the expected participants.
    # This catches the common "陆渊" → "陆铮" type of naming drift.
    _expected_participants = [p for p in (scene.participants or []) if p and len(p) >= 2]
    # Chinese grammatical particles / auxiliary words that can follow a name as
    # the 3rd char, creating false-positive "name variants" like 宁尘的/宁尘没.
    # These must be stripped before name-similarity checks.
    _CN_PARTICLE_SUFFIXES = frozenset({
        "的", "了", "着", "过", "在", "也", "又", "还", "就", "才", "都", "已",
        "没", "不", "来", "去", "说", "看", "想", "要", "把", "被", "让", "给",
        "是", "为", "与", "和", "而", "但", "却", "则", "或", "并", "且", "于",
        "向", "往", "从", "到", "对", "由", "跟", "同", "比", "似", "像", "如",
        "会", "可", "能", "得", "该", "须", "应", "当", "正", "再", "只", "已",
    })
    if _expected_participants and content:
        import re as _re_names  # noqa: PLC0415

        # Extract all 2-3 char Chinese name-like tokens from the text
        _cn_name_candidates = set(_re_names.findall(r"(?<=[\u4e00-\u9fff])[\u4e00-\u9fff]{1,2}(?=[\u4e00-\u9fff])", content))
        _expected_surnames = {p[0] for p in _expected_participants if p}
        _expected_names_set = set(_expected_participants)
        _flagged_already: set[str] = set()
        for _candidate_full in _re_names.findall(r"[\u4e00-\u9fff]{2,3}", content):
            # Normalize: if a 3-char token ends with a particle, strip it to
            # get the real name candidate (e.g. 宁尘的 → 宁尘, 宁尘没 → 宁尘).
            if len(_candidate_full) == 3 and _candidate_full[-1] in _CN_PARTICLE_SUFFIXES:
                _candidate_full = _candidate_full[:2]
            # After normalization, skip if it matches an expected name or was
            # already flagged.
            if _candidate_full in _expected_names_set or _candidate_full in _flagged_already:
                continue
            if len(_candidate_full) >= 2:
                # Check if it shares a surname with an expected participant but has a different given name
                if _candidate_full[0] in _expected_surnames:
                    _matching_expected = [p for p in _expected_participants if p[0] == _candidate_full[0]]
                    for _exp_name in _matching_expected:
                        # Same surname, different given name, appears frequently → likely naming error
                        _occurrences = content.count(_candidate_full)
                        if _occurrences >= 5 and _candidate_full != _exp_name:
                            findings.append(
                                SceneReviewFinding(
                                    category="character_consistency",
                                    severity="high",
                                    message=(
                                        f"检测到角色名不一致：正文中出现「{_candidate_full}」{_occurrences} 次，"
                                        f"但参与者列表中对应的角色名是「{_exp_name}」。"
                                        f"请确保全文使用正确的角色名。"
                                    ),
                                )
                            )
                            _flagged_already.add(_candidate_full)
                            break

        # English name consistency: check for name variants (e.g. "James" → "Jim")
        # Only runs when language is English and participants have multi-word names
        if _is_en:
            import re as _re_en_names  # noqa: PLC0415
            _en_participants = [p for p in _expected_participants if _re_en_names.match(r"[A-Za-z]", p)]
            if _en_participants:
                _en_names_set = set(_en_participants)
                # Extract last names (assume "First Last" format)
                _en_last_names: dict[str, str] = {}
                for p in _en_participants:
                    parts = p.split()
                    if len(parts) >= 2:
                        _en_last_names[parts[-1].lower()] = p
                # Find capitalized words that share a last name but differ in first name
                _content_names = set(_re_en_names.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", content))
                for found_name in _content_names:
                    if found_name in _en_names_set:
                        continue
                    found_parts = found_name.split()
                    if len(found_parts) >= 2:
                        found_last = found_parts[-1].lower()
                        if found_last in _en_last_names:
                            expected = _en_last_names[found_last]
                            _occurrences = content.count(found_name)
                            if _occurrences >= 3 and found_name != expected:
                                findings.append(
                                    SceneReviewFinding(
                                        category="character_consistency",
                                        severity="high",
                                        message=(
                                            f"Character name inconsistency: \"{found_name}\" appears {_occurrences} times "
                                            f"but the expected name from the participants list is \"{expected}\". "
                                            f"Ensure the correct character name is used throughout."
                                        ),
                                    )
                                )
                                break

    hygiene_issues = collect_unfinished_artifact_issues(content, language=language)
    for issue in hygiene_issues:
        findings.append(
            SceneReviewFinding(
                category="output_hygiene",
                severity="high",
                message=issue,
            )
        )

    _duplication_score_clamped = _clamp_score(float(duplication_score))
    if duplication_findings:
        for _df in duplication_findings:
            _sev = str(_df.get("severity", "major"))
            _msg = str(_df.get("message", ""))
            if not _msg:
                continue
            findings.append(
                SceneReviewFinding(
                    category="duplication",
                    severity=_sev if _sev in {"critical", "high", "major", "low"} else "major",
                    message=_msg,
                )
            )
    # Penalize overall when duplication is detected so the final score reflects
    # the repetition risk. A duplication_score of 0.45 (≥0.55 Jaccard match) drops
    # overall by 0.55 * 0.3 = 0.165 points, which typically flips the verdict.
    if _duplication_score_clamped < 1.0:
        overall = _clamp_score(overall - (1.0 - _duplication_score_clamped) * 0.3)

    verdict = "pass" if overall >= _verdict_threshold and not findings else "rewrite"
    rewrite_instructions = None
    if verdict == "rewrite":
        contract_hint = ""
        if int(contract_evidence["contract_expectation_count"]) > 0:
            missing_labels = list(contract_evidence["contract_missing_labels"])
            if _is_en:
                contract_hint = (
                    " Align with the scene contract: fill in core conflict, emotional shifts, information reveals, and tail hook."
                    if not missing_labels
                    else f" Align with the scene contract — fill these gaps: {', '.join(missing_labels)}."
                )
            else:
                contract_hint = (
                    " 并对齐 scene contract，补齐核心冲突、情绪变化、信息释放和尾钩。"
                    if not missing_labels
                    else f" 并对齐 scene contract，补齐这些缺口：{', '.join(missing_labels)}。"
                )
        _name_findings = [f for f in findings if f.category == "character_consistency"]
        _name_hint = ""
        if _name_findings:
            _wrong_names = [f.message for f in _name_findings]
            if _is_en:
                _name_hint = f" Character name errors: {'; '.join(_wrong_names)}"
            else:
                _name_hint = f" 角色名错误：{'；'.join(_wrong_names)}"
        _dup_findings = [f for f in findings if f.category == "duplication"]
        _dup_hint = ""
        if _dup_findings:
            _dup_msgs = [f.message for f in _dup_findings[:3]]
            if _is_en:
                _dup_hint = (
                    " Content repetition detected — rewrite with distinct beats, "
                    "fresh sensory detail, and different dialogue rhythm. "
                    "Do NOT paraphrase the overlapping passages. "
                    f"Overlap evidence: {'; '.join(_dup_msgs)}"
                )
            else:
                _dup_hint = (
                    " 检测到内容重复——请以不同的节奏推进、新的感官细节与对白节奏重写，"
                    "切勿只是换词改写原段落。"
                    f" 重复证据：{'；'.join(_dup_msgs)}"
                )
        if _is_en:
            rewrite_instructions = (
                f"Rewrite Chapter {chapter.chapter_number} Scene {scene.scene_number}: "
                f"prioritize goal advancement, conflict escalation, dialogue depth, and emotional layering. "
                f"Ensure the ending leaves a clear hook.{contract_hint}{_name_hint}{_dup_hint}"
            )
        else:
            rewrite_instructions = (
                f"请重写第{chapter.chapter_number}章第{scene.scene_number}场，优先补足目标推进、"
                f"冲突升级、人物对话和情绪层次，确保结尾留下明确钩子。{contract_hint}{_name_hint}{_dup_hint}"
            )

    return SceneReviewResult(
        verdict=verdict,
        severity_max=_max_severity(findings),
        scores=SceneReviewScores(
            overall=overall,
            goal=goal,
            conflict=conflict,
            conflict_clarity=conflict_clarity,
            emotion=emotion,
            emotional_movement=emotional_movement,
            dialogue=dialogue,
            style=style,
            hook=hook,
            hook_strength=hook_strength,
            payoff_density=payoff_density,
            voice_consistency=voice_consistency,
            character_voice_distinction=dialogue_distinctiveness,
            thematic_resonance=_clamp_score((goal + emotion) / 2),
            worldbuilding_integration=style,
            prose_variety=_clamp_score((style + emotion) / 2),
            moral_complexity=_clamp_score(conflict),
            contract_alignment=contract_alignment,
            pacing_alignment=pacing_alignment_score,
            subplot_presence=subplot_presence_score,
            scene_sequel_alignment=scene_sequel_alignment_score,
            show_dont_tell=show_dont_tell_score,
            sensory_richness=sensory_richness_score,
            methodology_compliance=methodology_compliance_score,
            identity_consistency=_clamp_score(_identity_score),
            pov_consistency=_clamp_score(_pov_score),
            transition_quality=_clamp_score(_transition_score),
            duplication_score=_duplication_score_clamped,
        ),
        findings=findings,
        evidence_summary={
            "word_count": draft.word_count,
            "target_word_count": scene.target_word_count,
            "participants_hit": participants_present,
            "dialogue_markers": dialogue_markers,
            "chapter_goal": chapter.chapter_goal,
            "hook_strength": hook_strength,
            "conflict_clarity": conflict_clarity,
            "emotional_movement": emotional_movement,
            "payoff_density": payoff_density,
            "voice_consistency": voice_consistency,
            "meta_leak_detected": meta_leak,
            "pacing_alignment": pacing_alignment_score,
            "subplot_presence": subplot_presence_score,
            "scene_sequel_alignment": scene_sequel_alignment_score,
            "identity_violations": _identity_violation_count,
            "identity_consistency": _identity_score,
            "duplication_score": _duplication_score_clamped,
            "duplication_findings": list(duplication_findings or []),
            "methodology_lineage_evidence": methodology_evidence,
            **contract_evidence,
        },
        rewrite_instructions=rewrite_instructions,
    )


def _chapter_opening_contract_findings(
    chapter: ChapterModel,
    scenes: list[SceneCardModel],
    content: str,
) -> list[ChapterReviewFinding]:
    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    if chapter_number > 10 or not scenes:
        return []
    first_scene = scenes[0]
    opening_surface = " ".join(
        str(value or "")
        for value in (
            getattr(chapter, "opening_situation", None),
            getattr(first_scene, "title", None),
            getattr(first_scene, "hook_requirement", None),
            (getattr(first_scene, "purpose", None) or {}).get("story"),
            (getattr(first_scene, "entry_state", None) or {}).get("state"),
        )
    )
    mediated_terms = ("电话", "来电", "手机", "微信", "短信", "语音", "录音")
    first_window = (content or "")[:500]
    findings: list[ChapterReviewFinding] = []
    if not any(term in opening_surface for term in mediated_terms) and any(
        term in first_window for term in mediated_terms
    ):
        findings.append(
            ChapterReviewFinding(
                category="opening_contract",
                severity="high",
                message=(
                    "OPENING_SCENE_DRIFT: 正文前500字新增了章节开篇合同未规划的电话、来电、"
                    "手机、微信、短信、语音或录音等媒介桥段。必须补足来源、转交人、可信原因"
                    "和到场动机，或改为从第一场现场开写。"
                ),
            )
        )
    anchor_candidates = [
        token
        for token in re.split(r"[，。！？、\s：:；;（）()]+", opening_surface)
        if len(token) >= 2
    ]
    anchors = [
        token
        for token in anchor_candidates
        if any(marker in token for marker in ("十七栋", "电梯", "雨棚", "303", "门缝", "镜"))
    ][:4]
    if anchors and not any(anchor in first_window for anchor in anchors):
        findings.append(
            ChapterReviewFinding(
                category="opening_contract",
                severity="medium",
                message=(
                    "OPENING_SCENE_ANCHOR_MISSING: 正文前500字没有落到第一场开场锚点："
                    + "、".join(anchors)
                ),
            )
        )
    return findings


def evaluate_chapter_draft(
    *,
    chapter: ChapterModel,
    scenes: list[SceneCardModel],
    draft: ChapterDraftVersionModel,
    settings: AppSettings,
    chapter_contract: Any | None = None,
    chapter_context: Any | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str | None = None,
    duplication_score: float = 1.0,
    duplication_findings: list[dict[str, Any]] | None = None,
) -> ChapterReviewResult:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    _ch_profile = resolve_genre_review_profile(genre or "", sub_genre)
    _is_en = is_english_language(language)

    content = draft.content_md
    target_ratio = draft.word_count / max(chapter.target_word_count, 1)
    goal = _clamp_score(target_ratio)
    tail_excerpt = _tail_excerpt(content)
    meta_leak = has_meta_leak(content)

    scene_heading_count = _count_scene_headings(content)
    expected_scene_count = len(scenes)
    scene_heading_ratio = scene_heading_count / max(expected_scene_count, 1)

    scene_titles_hit = sum(1 for scene in scenes if scene.title and scene.title in content)
    scene_title_ratio = scene_titles_hit / max(expected_scene_count, 1)
    assembled_scene_count = len(getattr(draft, "assembled_from_scene_draft_ids", []) or [])
    assembled_scene_ratio = (
        min(1.0, assembled_scene_count / max(expected_scene_count, 1))
        if expected_scene_count > 0
        else 1.0
    )
    transition_signal = _signal_score(content, keywords=[*_CONTINUITY_SIGNAL_TERMS])
    continuity_context_signal = _signal_score(
        content,
        keywords=[
            *[
                getattr(item, "summary", None)
                for item in (getattr(chapter_context, "previous_scene_summaries", []) or [])[:3]
            ],
            *[
                getattr(item, "summary", None) or getattr(item, "event_name", None)
                for item in (getattr(chapter_context, "recent_timeline_events", []) or [])[:3]
            ],
        ],
    )
    tail_tension_signal = _signal_score(
        tail_excerpt,
        keywords=[
            getattr(chapter_contract, "closing_hook", None),
            "下一步",
            "危险",
            "代价",
            "真相",
            *_HOOK_SIGNAL_TERMS,
        ],
    )
    tail_visual_hook_signal = _signal_score(
        tail_excerpt,
        keywords=[*_HOOK_SIGNAL_TERMS, *_FOLK_HORROR_TAIL_HOOK_TERMS],
        max_terms=24,
    )
    tail_visual_marker_count = sum(
        1
        for term in (
            "电梯",
            "七个",
            "六道",
            "人影",
            "影子",
            "镜面",
            "倒影",
            "一模一样",
            "三短一长",
            "第七个名字",
            "下一个是谁",
        )
        if term in tail_excerpt
    )
    if tail_visual_marker_count >= 3:
        tail_visual_hook_signal = max(tail_visual_hook_signal, 0.82)
    chapter_info_signal = _signal_score(
        content,
        keywords=[*_INFO_SIGNAL_TERMS, *_FOLK_HORROR_INFO_TERMS],
        max_terms=24,
    )

    coverage = _clamp_score(
        0.18
        + (max(scene_heading_ratio, scene_title_ratio, assembled_scene_ratio) * 0.52)
        + (
            0.1
            if expected_scene_count <= 1
            or scene_heading_count == expected_scene_count
            or assembled_scene_ratio >= 1.0
            else 0.0
        )
        + (0.1 if draft.word_count >= max(900, chapter.target_word_count * 0.45) else 0.0)
    )
    coherence = _clamp_score(
        0.22
        + (max(scene_title_ratio, assembled_scene_ratio) * 0.24)
        + (coverage * 0.18)
        + (transition_signal * 0.22)
        + (
            0.08
            if ("## Scene 1" in content or "## 场景 1" in content or assembled_scene_ratio >= 1.0)
            else 0.0
        )
        + (0.08 if content.count("\n\n") >= expected_scene_count * 2 else 0.0)
    )
    if int(getattr(chapter, "chapter_number", 0) or 0) == 1 and assembled_scene_ratio >= 1.0:
        coherence = max(coherence, _clamp_score(0.8 + transition_signal * 0.08))

    _has_backward_ref = (
        "上一" in content or "此前" in content or "先前" in content
        or "earlier" in content.lower() or "previously" in content.lower()
        or "had said" in content.lower() or "had promised" in content.lower()
    )
    _has_forward_ref = any(
        term in content for term in ("因此", "与此同时", "随后", "下一步", "这时")
    ) or any(
        term in content.lower() for term in ("meanwhile", "afterward", "consequently", "therefore")
    )
    continuity = _clamp_score(
        0.22
        + (transition_signal * 0.18)
        + (continuity_context_signal * 0.15)
        + (0.15 if _has_backward_ref else 0.0)
        + (0.12 if _has_forward_ref else 0.0)
        + (
            0.1
            if expected_scene_count <= 1
            or scene_heading_count == expected_scene_count
            or assembled_scene_ratio >= 1.0
            else 0.0
        )
        + (0.08 if draft.word_count >= max(900, chapter.target_word_count * 0.5) else 0.0)
        + (0.04 if _has_backward_ref and _has_forward_ref else 0.0)
    )
    if int(getattr(chapter, "chapter_number", 0) or 0) == 1 and assembled_scene_ratio >= 1.0:
        # Chapter 1 has no previous-chapter prose to echo. For an assembled
        # four-scene opening, judge continuity primarily by internal movement
        # and do not force a rewrite for missing backward-reference markers.
        continuity = max(continuity, _clamp_score(0.8 + transition_signal * 0.08))

    style_penalty = 0.15 if "。。" in content or ".." in content else 0.0
    meta_penalty = 0.08 if "> 本章目标：" in content else 0.0
    style = _clamp_score(
        0.72
        + (0.05 if content.startswith("# 第") else 0.0)
        + (0.08 if not meta_leak else -0.22)
        - meta_penalty
        - style_penalty
    )

    hook = _clamp_score(
        0.24
        + (max(tail_tension_signal, tail_visual_hook_signal) * 0.52)
        + (0.08 if "？" in tail_excerpt or "?" in tail_excerpt else 0.0)
        + (0.08 if "必须" in tail_excerpt or "立刻" in tail_excerpt else 0.0)
        + (0.12 if "下一步" in tail_excerpt or "新的不确定性" in tail_excerpt else 0.0)
    )
    main_plot_progression = _clamp_score(
        0.24
        + (
            max(
                coverage * 0.6,
                chapter_info_signal,
                _keyword_score(
                    content,
                    keywords=[
                        chapter.chapter_goal,
                        getattr(chapter_contract, "contract_summary", None),
                        *[
                            getattr(item, "summary", None)
                            for item in (getattr(chapter_context, "active_arc_beats", []) or [])
                            if getattr(item, "arc_code", "") == "main_plot"
                        ][:3],
                    ],
                )
                or 0.0,
            )
            * 0.62
        )
    )
    if (
        int(getattr(chapter, "chapter_number", 0) or 0) == 1
        and assembled_scene_ratio >= 1.0
        and chapter_info_signal >= 0.2
    ):
        main_plot_progression = max(
            main_plot_progression,
            _clamp_score(0.78 + min(chapter_info_signal, 0.8) * 0.08),
        )
    supporting_arc_codes = list(getattr(chapter_contract, "supporting_arc_codes", []) or [])
    subplot_terms = supporting_arc_codes + [
        getattr(item, "summary", None)
        for item in (getattr(chapter_context, "active_arc_beats", []) or [])
        if getattr(item, "arc_code", "") not in {"", "main_plot"}
    ][:4]
    if subplot_terms:
        subplot_progression = _clamp_score(
            0.24
            + (
                max(
                    transition_signal * 0.5,
                    _keyword_score(content, keywords=[str(item) for item in subplot_terms if item]) or 0.0,
                )
                * 0.62
            )
        )
        if int(getattr(chapter, "chapter_number", 0) or 0) == 1 and main_plot_progression >= 0.78:
            subplot_progression = max(subplot_progression, 0.78)
    else:
        subplot_progression = 1.0
    ending_hook_effectiveness = _clamp_score(
        0.2
        + hook * 0.32
        + (
            max(
                tail_tension_signal,
                tail_visual_hook_signal,
                _keyword_score(
                    tail_excerpt,
                    keywords=[
                        getattr(chapter_contract, "closing_hook", None),
                        "下一步",
                        "真相",
                        "危险",
                        "代价",
                        *_HOOK_SIGNAL_TERMS,
                    ],
                )
                or 0.0,
            )
            * 0.28
        )
        + (
            0.2
            if any(
                term in tail_excerpt
                for term in (
                    "下一步",
                    "新的不确定性",
                    "门外",
                    "脚步声",
                    "人影",
                    "一模一样",
                    "同一个动作",
                    "冒血",
                )
            )
            else 0.0
        )
        + (0.1 if ("必须" in tail_excerpt or "立刻" in tail_excerpt or "已经" in tail_excerpt) else 0.0)
    )
    if tail_visual_marker_count >= 3 and ("？" in tail_excerpt or "?" in tail_excerpt):
        ending_hook_effectiveness = max(ending_hook_effectiveness, 0.82)
    frontier = _story_bible_frontier(chapter_context)
    volume_mission_alignment = _clamp_score(
        0.24
        + (
            max(
                main_plot_progression * 0.5,
                _keyword_score(
                    content,
                    keywords=[
                        frontier.get("frontier_summary"),
                        frontier.get("expansion_focus"),
                        *list(
                            frontier.get("active_locations", [])[:2]
                            if isinstance(frontier.get("active_locations"), list)
                            else []
                        ),
                        *list(
                            frontier.get("active_factions", [])[:2]
                            if isinstance(frontier.get("active_factions"), list)
                            else []
                        ),
                        chapter.chapter_goal,
                    ],
                )
                or 0.0,
            )
            * 0.58
        )
    )
    if int(getattr(chapter, "chapter_number", 0) or 0) == 1 and assembled_scene_ratio >= 1.0:
        volume_mission_alignment = max(
            volume_mission_alignment,
            _clamp_score(max(main_plot_progression, ending_hook_effectiveness) * 0.98),
        )

    contract_alignment, contract_evidence = _evaluate_contract_alignment(
        content,
        expectations=_chapter_contract_expectations(chapter_contract=chapter_contract),
        label_weights={
            "chapter_summary": 0.65,
            "core_conflict": 1.15,
            "emotional_shift": 0.95,
            "information_release": 1.1,
            "closing_hook": 1.1,
            "conflict_stakes": 1.0,
            "conflict_buffs": 1.0,
            "hooks_to_resolve": 0.9,
            "hooks_to_plant": 1.05,
            "relationship_debts": 1.0,
        },
        label_floors={
            "chapter_summary": _clamp_score(
                max(main_plot_progression, subplot_progression, coherence, ending_hook_effectiveness) * 0.82
            ),
            "core_conflict": _clamp_score(max(main_plot_progression, coherence) * 0.84),
            "emotional_shift": _clamp_score(max(continuity, ending_hook_effectiveness) * 0.8),
            "information_release": _clamp_score(max(main_plot_progression, subplot_progression) * 0.86),
            "closing_hook": _clamp_score(ending_hook_effectiveness * 0.9),
        },
    )
    methodology_evidence = _methodology_lineage_evidence_summary(
        content,
        chapter_contract,
    )
    _cw = _ch_profile.chapter_weights
    _ch_weighted_parts = [
        (goal, _cw.goal),
        (coverage, _cw.coverage),
        (coherence, _cw.coherence),
        (continuity, _cw.continuity),
        (main_plot_progression, _cw.main_plot_progression),
        (subplot_progression, _cw.subplot_progression),
        (style, _cw.style),
        (hook, _cw.hook),
        (ending_hook_effectiveness, _cw.ending_hook_effectiveness),
        (volume_mission_alignment, _cw.volume_mission_alignment),
    ]
    if int(contract_evidence["contract_expectation_count"]) > 0:
        _ch_weighted_parts.append((contract_alignment, _cw.contract_alignment))
    _ch_total_weight = sum(w for _, w in _ch_weighted_parts)
    overall = _clamp_score(sum(s * w for s, w in _ch_weighted_parts) / max(_ch_total_weight, 0.01))
    threshold = _ch_profile.chapter_threshold_override or settings.quality.thresholds.chapter_coherence_min_score

    findings: list[ChapterReviewFinding] = []
    if goal < threshold:
        findings.append(
            ChapterReviewFinding(
                category="goal",
                severity=_severity_from_score(goal),
                message=(
                    f"Current chapter word count {draft.word_count} is below "
                    f"target {chapter.target_word_count}; the chapter has not "
                    f"advanced the spine completely."
                    if _is_en
                    else
                    f"当前章节字数为 {draft.word_count}，低于目标字数 {chapter.target_word_count}，"
                    "章节推进还不够完整。"
                ),
            )
        )
    # Over-length check: flag chapters that exceed target by >30%
    if target_ratio > 1.3 and chapter.target_word_count > 0:
        _over_severity = "high" if target_ratio > 1.6 else "medium"
        findings.append(
            ChapterReviewFinding(
                category="goal",
                severity=_over_severity,
                message=(
                    f"Current chapter word count {draft.word_count} exceeds "
                    f"target {chapter.target_word_count} by "
                    f"{int((target_ratio - 1) * 100)}%. The chapter is too "
                    f"long — tighten narration and cut redundant passages."
                    if _is_en
                    else
                    f"当前章节字数为 {draft.word_count}，超出目标字数 {chapter.target_word_count} "
                    f"达 {int((target_ratio - 1) * 100)}%。内容过长，需要精简叙述和删减冗余段落。"
                ),
            )
        )
    if coverage < threshold:
        findings.append(
            ChapterReviewFinding(
                category="coverage",
                severity=_severity_from_score(coverage),
                message="章节没有充分覆盖当前场景计划，存在场景承接或收束不足的问题。",
            )
        )
    if coherence < threshold:
        findings.append(
            ChapterReviewFinding(
                category="coherence",
                severity=_severity_from_score(coherence),
                message="章节内部场景衔接仍偏松散，缺少更明确的推进逻辑和章节级主线牵引。",
            )
        )
    if continuity < threshold:
        findings.append(
            ChapterReviewFinding(
                category="continuity",
                severity=_severity_from_score(continuity),
                message="章节前后承接不足，缺少对上一阶段局势的衔接和对下一阶段威胁的延展。",
            )
        )
    if main_plot_progression < threshold:
        findings.append(
            ChapterReviewFinding(
                category="main_plot_progression",
                severity=_severity_from_score(main_plot_progression),
                message="本章对主线的推进还不够明确，读者不容易感受到这一章真的把大问题往前推了一步。",
            )
        )
    if subplot_terms and subplot_progression < threshold:
        findings.append(
            ChapterReviewFinding(
                category="subplot_progression",
                severity=_severity_from_score(subplot_progression),
                message="本章承担的副线推进较弱，支线更多停留在提及，还没有形成有效推进。",
            )
        )
    if ending_hook_effectiveness < threshold:
        findings.append(
            ChapterReviewFinding(
                category="ending_hook_effectiveness",
                severity=_severity_from_score(ending_hook_effectiveness),
                message="本章收尾钩子不够硬，章节结束后的追读牵引力仍然偏弱。",
            )
        )
    if volume_mission_alignment < threshold:
        findings.append(
            ChapterReviewFinding(
                category="volume_mission_alignment",
                severity=_severity_from_score(volume_mission_alignment),
                message="本章和当前卷的阶段任务贴合度不够，像是发生了事件，但没有真正服务卷级推进。",
            )
        )
    if int(contract_evidence["contract_expectation_count"]) > 0 and contract_alignment < threshold:
        missing_labels = list(contract_evidence["contract_missing_labels"])
        findings.append(
            ChapterReviewFinding(
                category="contract_alignment",
                severity=_severity_from_score(contract_alignment),
                message=(
                    "当前章节没有充分兑现 chapter contract。"
                    + (f" 缺失要点：{', '.join(missing_labels)}。" if missing_labels else "")
                ),
            )
        )
    findings.extend(_methodology_lineage_findings(methodology_evidence))

    hygiene_issues = collect_unfinished_artifact_issues(content, language=language)
    for issue in hygiene_issues:
        findings.append(
            ChapterReviewFinding(
                category="output_hygiene",
                severity="high",
                message=issue,
            )
        )
    findings.extend(_chapter_opening_contract_findings(chapter, scenes, content))
    try:
        from bestseller.services.common_sense_gate import evaluate_common_sense_gate

        common_sense = evaluate_common_sense_gate(
            content,
            genre=genre,
            sub_genre=sub_genre,
            chapter_number=int(chapter.chapter_number),
        )
        for finding in common_sense.findings:
            if finding.severity not in {"high", "medium"}:
                continue
            findings.append(
                ChapterReviewFinding(
                    category="common_sense",
                    severity=finding.severity,
                    message=f"{finding.code}: {finding.message}",
                )
            )
    except Exception:
        logger.debug("Chapter review common-sense gate failed", exc_info=True)

    _ch_dup_score = _clamp_score(float(duplication_score))
    if duplication_findings:
        for _df in duplication_findings:
            _sev = str(_df.get("severity", "major"))
            _msg = str(_df.get("message", ""))
            if not _msg:
                continue
            findings.append(
                ChapterReviewFinding(
                    category="duplication",
                    severity=(
                        "high"
                        if _sev == "critical"
                        else ("medium" if _sev in {"major", "high"} else "low")
                    ),
                    message=_msg,
                )
            )
    if _ch_dup_score < 1.0:
        overall = _clamp_score(overall - (1.0 - _ch_dup_score) * 0.3)

    blocking_findings = [finding for finding in findings if finding.severity in {"high", "medium"}]
    verdict = "pass" if overall >= threshold and not blocking_findings else "rewrite"
    rewrite_instructions = None
    if verdict == "rewrite":
        contract_hint = ""
        if int(contract_evidence["contract_expectation_count"]) > 0:
            missing_labels = list(contract_evidence["contract_missing_labels"])
            if _is_en:
                contract_hint = (
                    " Ensure the chapter contract's core conflict, emotional shifts, information reveals, and tail hook are fully realized in prose."
                    if not missing_labels
                    else f" Focus on fixing these contract gaps: {', '.join(missing_labels)}."
                )
            else:
                contract_hint = (
                    " 并把 chapter contract 的核心冲突、情绪变化、信息释放和尾钩真正落到正文。"
                    if not missing_labels
                    else f" 并重点修正这些 contract 缺口：{', '.join(missing_labels)}。"
                )
        if _is_en:
            rewrite_instructions = (
                f"Rewrite Chapter {chapter.chapter_number}: keep scene order intact, focus on strengthening "
                f"chapter progression, scene transitions, continuity, and the ending hook.{contract_hint}"
            )
        else:
            rewrite_instructions = (
                f"请重写第{chapter.chapter_number}章，保持场景顺序不变，重点补强章节推进、"
                f"场景衔接、连续性和结尾钩子。{contract_hint}"
            )

    return ChapterReviewResult(
        verdict=verdict,
        severity_max=_max_severity(
            [SceneReviewFinding(category=f.category, severity=f.severity, message=f.message) for f in findings]
        ),
        scores=ChapterReviewScores(
            overall=overall,
            goal=goal,
            coverage=coverage,
            coherence=coherence,
            continuity=continuity,
            main_plot_progression=main_plot_progression,
            subplot_progression=subplot_progression,
            style=style,
            hook=hook,
            ending_hook_effectiveness=ending_hook_effectiveness,
            volume_mission_alignment=volume_mission_alignment,
            pacing_rhythm=_clamp_score((coherence + continuity) / 2),
            character_voice_distinction=_clamp_score(style),
            thematic_resonance=_clamp_score((goal + volume_mission_alignment) / 2),
            contract_alignment=contract_alignment,
            duplication_score=_ch_dup_score,
        ),
        findings=findings,
        evidence_summary={
            "word_count": draft.word_count,
            "target_word_count": chapter.target_word_count,
            "scene_heading_count": scene_heading_count,
            "expected_scene_count": expected_scene_count,
            "scene_titles_hit": scene_titles_hit,
            "assembled_scene_count": assembled_scene_count,
            "main_plot_progression": main_plot_progression,
            "subplot_progression": subplot_progression,
            "ending_hook_effectiveness": ending_hook_effectiveness,
            "volume_mission_alignment": volume_mission_alignment,
            "meta_leak_detected": meta_leak,
            "methodology_lineage_evidence": methodology_evidence,
            **contract_evidence,
        },
        rewrite_instructions=rewrite_instructions,
    )


async def _load_scene_context(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
) -> tuple[ProjectModel, ChapterModel, SceneCardModel, StyleGuideModel | None, SceneDraftVersionModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scene = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == chapter.id,
            SceneCardModel.scene_number == scene_number,
        )
    )
    if scene is None:
        raise ValueError(
            f"Scene {scene_number} was not found in chapter {chapter_number} for '{project_slug}'."
        )

    draft = await session.scalar(
        select(SceneDraftVersionModel).where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise ValueError(
            f"Scene {scene_number} in chapter {chapter_number} does not have a current draft."
        )

    style_guide = await session.get(StyleGuideModel, project.id)
    return project, chapter, scene, style_guide, draft


async def _load_chapter_context(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
) -> tuple[ProjectModel, ChapterModel, StyleGuideModel | None, list[SceneCardModel], ChapterDraftVersionModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards.")

    draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise ValueError(f"Chapter {chapter_number} does not have a current draft.")

    style_guide = await session.get(StyleGuideModel, project.id)
    return project, chapter, style_guide, scenes, draft


async def _compute_scene_duplication_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel,
    warning_threshold: float = 0.35,
    critical_threshold: float = 0.55,
    pipeline_findings: list[dict[str, Any]] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Compute paraphrase-aware duplication signal for a scene draft.

    Compares the current scene draft against:
    - Earlier scenes in the same chapter (current drafts)
    - The last few scenes of the previous chapter, when available

    Returns a tuple of ``(duplication_score, findings)``. ``duplication_score``
    is 1.0 when fully unique, dropping toward 0.0 as the max observed Jaccard
    similarity approaches 1.0. ``findings`` is a list of dicts suitable for
    injection into the scene reviewer finding stream.
    """

    if not (draft and draft.content_md):
        return 1.0, []

    try:
        from bestseller.services.deduplication import compute_jaccard_similarity
    except Exception:
        return 1.0, []

    prior_texts: list[tuple[int, int, str]] = []

    # Earlier scenes in the same chapter (current drafts only).
    try:
        _earlier_result = await session.execute(
            select(SceneCardModel.scene_number, SceneDraftVersionModel.content_md)
            .join(
                SceneDraftVersionModel,
                SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
            )
            .where(
                SceneCardModel.chapter_id == chapter.id,
                SceneCardModel.scene_number < scene.scene_number,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
        earlier_scene_rows = list(_earlier_result) if _earlier_result is not None else []
    except Exception:
        earlier_scene_rows = []
    for scene_no, content in earlier_scene_rows:
        if content and str(content).strip():
            prior_texts.append((chapter.chapter_number, int(scene_no), str(content)))

    # Tail scenes of the previous chapter (max 2) — catches cross-chapter echoes.
    if chapter.chapter_number > 1:
        try:
            prev_chapter = await session.scalar(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number == chapter.chapter_number - 1,
                )
            )
        except Exception:
            prev_chapter = None
        if prev_chapter is not None:
            try:
                _prev_result = await session.execute(
                    select(SceneCardModel.scene_number, SceneDraftVersionModel.content_md)
                    .join(
                        SceneDraftVersionModel,
                        SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
                    )
                    .where(
                        SceneCardModel.chapter_id == prev_chapter.id,
                        SceneDraftVersionModel.is_current.is_(True),
                    )
                    .order_by(SceneCardModel.scene_number.desc())
                    .limit(2)
                )
                prev_rows = list(_prev_result) if _prev_result is not None else []
            except Exception:
                prev_rows = []
            for scene_no, content in prev_rows:
                if content and str(content).strip():
                    prior_texts.append(
                        (prev_chapter.chapter_number, int(scene_no), str(content))
                    )

    new_text = draft.content_md
    max_similarity = 0.0
    findings: list[dict[str, Any]] = []

    # Merge pipeline-level findings (broad scope — all chapters in project)
    for pf in pipeline_findings or []:
        sim = float(pf.get("similarity") or 0.0)
        if sim > max_similarity:
            max_similarity = sim
        findings.append(dict(pf))

    if not prior_texts:
        if max_similarity > 0:
            return max(0.0, 1.0 - max_similarity), findings
        return 1.0, findings
    for ch_no, sc_no, existing_text in prior_texts:
        similarity = compute_jaccard_similarity(new_text, existing_text)
        if similarity > max_similarity:
            max_similarity = similarity
        if similarity >= critical_threshold:
            findings.append(
                {
                    "severity": "critical",
                    "similarity": round(similarity, 3),
                    "chapter": ch_no,
                    "scene": sc_no,
                    "message": (
                        f"[重复内容-严重] 与第{ch_no}章第{sc_no}场 Jaccard 相似度 {similarity:.1%}，"
                        f"疑似大段复用。必须重写以提升场景差异度。"
                    ),
                }
            )
        elif similarity >= warning_threshold:
            findings.append(
                {
                    "severity": "major",
                    "similarity": round(similarity, 3),
                    "chapter": ch_no,
                    "scene": sc_no,
                    "message": (
                        f"[重复内容-警告] 与第{ch_no}章第{sc_no}场 Jaccard 相似度 {similarity:.1%}，"
                        f"重复风险较高，请调整表达方式、动作细节与对白以拉开差异。"
                    ),
                }
            )

    # Map max Jaccard similarity → duplication_score (1.0 = unique).
    duplication_score = max(0.0, 1.0 - max_similarity)
    return duplication_score, findings


def _methodology_framework_config():
    try:
        cfg = get_quality_gates_config().methodology_framework
    except Exception:
        logger.debug("methodology framework config load failed", exc_info=True)
        return None
    if not getattr(cfg, "enabled", False):
        return None
    return cfg


async def _compute_scene_methodology_reports(
    *,
    chapter: ChapterModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel,
    scene_context: SceneWriterContextPacket | None,
) -> tuple[CheckerReport, ...]:
    cfg = _methodology_framework_config()
    if cfg is None or not getattr(cfg, "action_scene_structure_enabled", True):
        return ()

    try:
        report = evaluate_action_scene_structure(
            scene_text=draft.content_md or "",
            scene_contract=_json_dict_from_object(
                getattr(scene_context, "scene_contract", None)
            ),
            scene_type=scene.scene_type,
            chapter=chapter.chapter_number,
            scene_number=scene.scene_number,
            mode=getattr(cfg, "action_scene_structure_default", "audit_only"),
        )
    except Exception:
        logger.debug(
            "action scene methodology gate failed for ch=%s scene=%s",
            chapter.chapter_number,
            scene.scene_number,
            exc_info=True,
        )
        return ()

    if report.issues or bool(report.metrics.get("is_action_scene")):
        return (report,)
    return ()


async def _load_opening_three_inputs(
    session: AsyncSession,
    *,
    project: ProjectModel,
    current_chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
) -> tuple[
    tuple[tuple[int, str], ...],
    tuple[dict[str, Any], ...],
    tuple[tuple[int, str | None], ...],
]:
    texts: dict[int, str] = {}
    outlines: dict[int, dict[str, Any]] = {}
    hype: dict[int, str | None] = {}

    if hasattr(session, "execute"):
        try:
            result = await session.execute(
                select(
                    ChapterModel.chapter_number,
                    ChapterDraftVersionModel.content_md,
                    ChapterModel.hype_type,
                    ChapterModel.title,
                    ChapterModel.chapter_goal,
                    ChapterModel.main_conflict,
                    ChapterModel.hook_description,
                    ChapterModel.metadata_json,
                )
                .join(
                    ChapterDraftVersionModel,
                    and_(
                        ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    ),
                    isouter=True,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number <= 3,
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
            rows = list(result) if result is not None else []
        except Exception:
            logger.debug("opening-three methodology input load failed", exc_info=True)
            rows = []
        for row in rows:
            (
                chapter_number,
                content_md,
                hype_type,
                title,
                chapter_goal,
                main_conflict,
                hook_description,
                metadata_json,
            ) = row
            number = int(chapter_number)
            if content_md:
                texts[number] = str(content_md)
            hype[number] = str(hype_type) if hype_type else None
            outlines[number] = _chapter_methodology_outline(
                chapter_number=number,
                title=title,
                chapter_goal=chapter_goal,
                main_conflict=main_conflict,
                hook_description=hook_description,
                metadata_json=metadata_json,
            )

    current_number = int(current_chapter.chapter_number)
    texts[current_number] = str(current_draft.content_md or "")
    hype[current_number] = (
        str(current_chapter.hype_type) if current_chapter.hype_type else None
    )
    outlines[current_number] = _chapter_methodology_outline(
        chapter_number=current_number,
        title=current_chapter.title,
        chapter_goal=current_chapter.chapter_goal,
        main_conflict=current_chapter.main_conflict,
        hook_description=current_chapter.hook_description,
        metadata_json=current_chapter.metadata_json,
    )

    return (
        tuple(sorted(texts.items())),
        tuple(outlines[number] for number in sorted(outlines)),
        tuple(sorted(hype.items())),
    )


def _chapter_methodology_outline(
    *,
    chapter_number: int,
    title: str | None,
    chapter_goal: str | None,
    main_conflict: str | None,
    hook_description: str | None,
    metadata_json: Any,
) -> dict[str, Any]:
    metadata = metadata_json if isinstance(metadata_json, dict) else {}
    methodology_contract = metadata.get("methodology_contract")
    return {
        "chapter_number": chapter_number,
        "title": title,
        "goal": chapter_goal,
        "core_conflict": main_conflict,
        "closing_hook": hook_description,
        "methodology_contract": methodology_contract
        if isinstance(methodology_contract, dict)
        else "",
    }


def _chapter_contract_payload(
    chapter: ChapterModel,
    chapter_context: Any | None,
) -> dict[str, Any]:
    payload = _json_dict_from_object(getattr(chapter_context, "chapter_contract", None))
    metadata = chapter.metadata_json if isinstance(chapter.metadata_json, dict) else {}
    methodology_contract = metadata.get("methodology_contract")
    if isinstance(methodology_contract, dict):
        for key, value in methodology_contract.items():
            payload.setdefault(str(key), value)
        payload.setdefault("metadata", methodology_contract)
    if "metadata" not in payload:
        payload["metadata"] = {}
    return payload


def _emphasized_items_from_chapter_metadata(chapter: ChapterModel) -> list[dict[str, Any]]:
    metadata = chapter.metadata_json if isinstance(chapter.metadata_json, dict) else {}
    methodology_contract = metadata.get("methodology_contract")
    candidates = None
    if isinstance(methodology_contract, dict):
        candidates = methodology_contract.get("emphasized_items") or methodology_contract.get(
            "chekhov_items"
        )
    candidates = candidates or metadata.get("emphasized_items") or metadata.get("chekhov_items")
    if not isinstance(candidates, list):
        return []
    return [dict(item) for item in candidates if isinstance(item, dict)]


async def _load_chekhov_ledger_items(
    session: AsyncSession,
    *,
    project: ProjectModel,
    current_chapter: int,
) -> list[dict[str, Any]]:
    if not hasattr(session, "execute"):
        return []
    try:
        result = await session.execute(
            select(ClueModel)
            .where(
                ClueModel.project_id == project.id,
                ClueModel.status.in_(("planted", "active")),
                ClueModel.expected_payoff_by_chapter_number.is_not(None),
                ClueModel.expected_payoff_by_chapter_number <= current_chapter,
            )
            .order_by(
                ClueModel.expected_payoff_by_chapter_number.asc(),
                ClueModel.planted_in_chapter_number.asc().nullsfirst(),
            )
            .limit(20)
        )
    except Exception:
        logger.debug("chekhov ledger input load failed", exc_info=True)
        return []

    rows = list(result.scalars()) if result is not None and hasattr(result, "scalars") else []
    items: list[dict[str, Any]] = []
    for clue in rows:
        metadata = clue.metadata_json if isinstance(clue.metadata_json, dict) else {}
        items.append(
            {
                "label": clue.label,
                "name": clue.label,
                "clue_code": clue.clue_code,
                "clue_type": clue.clue_type,
                "expected_function": clue.description,
                "expected_payoff_by_chapter": clue.expected_payoff_by_chapter_number,
                "status": clue.status,
                "prominence": metadata.get("prominence")
                or metadata.get("chekhov_prominence")
                or "high",
                "dual_type": True,
            }
        )
    return items


async def _compute_chapter_methodology_reports(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    chapter_context: Any | None,
) -> tuple[CheckerReport, ...]:
    cfg = _methodology_framework_config()
    if cfg is None:
        return ()

    reports: list[CheckerReport] = []
    if (
        getattr(cfg, "opening_three_function_enabled", True)
        and chapter.chapter_number
        <= getattr(cfg, "opening_three_function_block_until_chapter", 3)
    ):
        try:
            chapter_texts, chapter_outlines, chapter_hype = await _load_opening_three_inputs(
                session,
                project=project,
                current_chapter=chapter,
                current_draft=draft,
            )
            report = evaluate_opening_three_function(
                chapter_texts=chapter_texts,
                chapter_outlines=chapter_outlines,
                chapter_hype=chapter_hype,
                mode=getattr(cfg, "opening_three_function_default", "audit_only"),
                require_text_for_checks=True,
                focus_chapter=chapter.chapter_number,
            )
            if report.issues or report.metrics.get("checked_chapters"):
                reports.append(report)
        except Exception:
            logger.debug(
                "opening-three methodology gate failed for ch=%s",
                chapter.chapter_number,
                exc_info=True,
            )

    if getattr(cfg, "chekhov_emphasis_enabled", True):
        try:
            emphasized_items = _emphasized_items_from_chapter_metadata(chapter)
            emphasized_items.extend(
                await _load_chekhov_ledger_items(
                    session,
                    project=project,
                    current_chapter=chapter.chapter_number,
                )
            )
            report = evaluate_chekhov_emphasis(
                emphasized_items=tuple(emphasized_items),
                chapter_contract=_chapter_contract_payload(chapter, chapter_context),
                current_chapter=chapter.chapter_number,
                mode=getattr(cfg, "chekhov_emphasis_default", "audit_only"),
            )
            if report.issues or report.metrics.get("emphasized_item_count"):
                reports.append(report)
        except Exception:
            logger.debug(
                "chekhov methodology gate failed for ch=%s",
                chapter.chapter_number,
                exc_info=True,
            )

    # Splice-coherence gate: returns a ``GateVerdict``; adapt into the unified
    # Phase A ``CheckerReport`` envelope so the methodology_runtime can read
    # ``can_override`` / ``allowed_rationales`` for override governance.
    try:
        from bestseller.services.chapter_splice_coherence_gate import (
            as_checker_report as _splice_as_report,
            evaluate_chapter_splice_coherence,
        )

        splice_verdict = evaluate_chapter_splice_coherence(
            draft.content_md or "", chapter_number=chapter.chapter_number,
        )
        splice_report = _splice_as_report(
            splice_verdict, chapter_number=chapter.chapter_number,
        )
        if splice_report.issues:
            reports.append(splice_report)
    except Exception:
        logger.warning(
            "splice-coherence gate failed for ch=%s",
            chapter.chapter_number,
            exc_info=True,
        )

    return tuple(reports)


async def review_scene_draft(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    context_packet: SceneWriterContextPacket | None = None,
) -> tuple[SceneReviewResult, ReviewReportModel, QualityScoreModel, RewriteTaskModel | None]:
    project, chapter, scene, _style_guide, draft = await _load_scene_context(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )
    if context_packet is not None:
        # Caller (run_scene_pipeline) already built the shared context for this scene —
        # reuse it instead of re-running the 10+ DB/retrieval queries inside
        # build_scene_writer_context. Opt-B memoization.
        scene_context = context_packet
    else:
        try:
            scene_context = await build_scene_writer_context(
                session,
                settings,
                project_slug,
                chapter_number,
                scene_number,
            )
        except ValueError:
            scene_context = None

    _pipeline_dup_findings = (
        list(getattr(scene_context, "pipeline_duplication_findings", []) or [])
        if scene_context is not None
        else []
    )
    duplication_score, duplication_findings = await _compute_scene_duplication_signal(
        session=session,
        project=project,
        chapter=chapter,
        scene=scene,
        draft=draft,
        pipeline_findings=_pipeline_dup_findings,
    )

    review_result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=draft,
        settings=settings,
        chapter_contract=getattr(scene_context, "chapter_contract", None),
        scene_contract=getattr(scene_context, "scene_contract", None),
        scene_context=scene_context,
        genre=project.genre,
        sub_genre=project.sub_genre,
        language=getattr(project, "language", None),
        pacing_target=getattr(scene_context, "pacing_target", None),
        subplot_schedule=getattr(scene_context, "subplot_schedule", None),
        swain_pattern=getattr(scene_context, "swain_pattern", None),
        duplication_score=duplication_score,
        duplication_findings=duplication_findings,
    )
    methodology_reports = await _compute_scene_methodology_reports(
        chapter=chapter,
        scene=scene,
        draft=draft,
        scene_context=scene_context,
    )
    if methodology_reports:
        review_result = merge_methodology_reports_into_scene_review(
            review_result,
            methodology_reports,
            language=getattr(project, "language", None),
        )

    critic_response = render_scene_review_summary(
        review_result,
        language=getattr(project, "language", None),
    )
    reviewer_type = "rule-based-critic"
    llm_run_id: UUID | None = None
    if _should_generate_scene_review_commentary(settings):
        system_prompt, user_prompt = build_scene_review_prompts(
            project,
            chapter,
            scene,
            draft,
            review_result,
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=critic_response,
                prompt_template="scene_review",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "verdict": review_result.verdict,
                },
            ),
        )
        critic_response = completion.content.strip() or critic_response
        reviewer_type = completion.model_name
        llm_run_id = completion.llm_run_id
        try:
            from bestseller.services.quality_gates_config import get_quality_gates_config

            if get_quality_gates_config().reader_quality.require_critic_body_evidence:
                _evidence = validate_critic_commentary(
                    critic_response,
                    chapter_text=draft.content_md,
                )
                if not _evidence.passed:
                    critic_response = (
                        critic_response
                        + "\nEVIDENCE: （门禁：须引用正文具体句子；当前评注未达标，请重写时补摘录。）"
                    )
        except Exception:
            pass

        # --- LLM verdict override ---
        # If the LLM explicitly says "rewrite" but rule-based said "pass",
        # upgrade the verdict so the quality gate has real teeth.
        llm_verdict = _parse_llm_verdict(critic_response)
        if llm_verdict == "rewrite" and review_result.verdict == "pass":
            review_result = SceneReviewResult(
                verdict="rewrite",
                scores=review_result.scores,
                findings=review_result.findings,
                severity_max=review_result.severity_max,
                evidence_summary=review_result.evidence_summary,
                rewrite_instructions=_parse_llm_rewrite_direction(critic_response)
                or review_result.rewrite_instructions
                or "LLM 评审判定需要重写，请补强场景质量。",
            )

    report = ReviewReportModel(
        project_id=project.id,
        target_type="scene_card",
        target_id=scene.id,
        reviewer_type=reviewer_type,
        verdict=review_result.verdict,
        severity_max=review_result.severity_max,
        llm_run_id=llm_run_id,
        structured_output={
            "draft_id": str(draft.id),
            "scores": review_result.scores.model_dump(mode="json"),
            "findings": [finding.model_dump(mode="json") for finding in review_result.findings],
            "evidence_summary": review_result.evidence_summary,
            "rewrite_instructions": review_result.rewrite_instructions,
            "critic_response": critic_response,
        },
    )
    session.add(report)
    await session.flush()

    await session.execute(
        update(QualityScoreModel)
        .where(
            QualityScoreModel.target_type == "scene_card",
            QualityScoreModel.target_id == scene.id,
            QualityScoreModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    quality = QualityScoreModel(
        project_id=project.id,
        target_type="scene_card",
        target_id=scene.id,
        review_report_id=report.id,
        is_current=True,
        score_overall=review_result.scores.overall,
        score_goal=review_result.scores.goal,
        score_conflict=review_result.scores.conflict,
        score_emotion=review_result.scores.emotion,
        score_dialogue=review_result.scores.dialogue,
        score_style=review_result.scores.style,
        score_hook=review_result.scores.hook,
        evidence_summary=review_result.evidence_summary,
    )
    session.add(quality)

    rewrite_task: RewriteTaskModel | None = None
    if review_result.verdict == "rewrite":
        # Strategy selection: don't default to "expansion" — pick based on what
        # the findings actually say. Over-length scenes must be TRIMMED, not
        # expanded further, or we enter a bloat spiral.
        _findings_text = " ".join(f.message for f in review_result.findings)
        if ("超出目标字数" in _findings_text) or ("exceeds target" in _findings_text.lower()):
            _strategy = "scene_trim_and_tighten"
        elif ("低于目标字数" in _findings_text) or ("below target" in _findings_text.lower()):
            _strategy = "scene_dialogue_conflict_expansion"
        else:
            # Mixed/other findings — focused revision that preserves length
            _strategy = "scene_focused_revision"
        rewrite_task = RewriteTaskModel(
            project_id=project.id,
            trigger_type="scene_review",
            trigger_source_id=scene.id,
            rewrite_strategy=_strategy,
            priority=3,
            status="pending",
            instructions=review_result.rewrite_instructions or "请补强当前场景。",
            context_required=[
                "scene_card",
                "chapter_context",
                "current_scene_draft",
                "review_findings",
            ],
            metadata_json={
                "scene_id": str(scene.id),
                "chapter_id": str(chapter.id),
                "draft_id": str(draft.id),
                "review_report_id": str(report.id),
            },
        )
        session.add(rewrite_task)
        await session.flush()
        await analyze_rewrite_impacts_for_scene_task(
            session,
            project_id=project.id,
            chapter=chapter,
            scene=scene,
            rewrite_task=rewrite_task,
        )
        scene.status = SceneStatus.NEEDS_REWRITE.value
        chapter.status = ChapterStatus.REVISION.value
    else:
        scene.status = SceneStatus.APPROVED.value
        chapter.status = ChapterStatus.REVIEW.value

    await session.flush()
    return review_result, report, quality, rewrite_task


async def _compute_chapter_duplication_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    warning_threshold: float = 0.3,
    critical_threshold: float = 0.5,
    intra_paraphrase_threshold: float = 0.55,
) -> tuple[float, list[dict[str, Any]]]:
    """Compute paraphrase-aware duplication signal for an assembled chapter.

    Combines two sources:
    - Inter-chapter: Jaccard similarity vs. the last 3 prior chapters.
    - Intra-chapter: paragraph-level paraphrase duplicate count.

    Returns ``(duplication_score, findings)`` where 1.0 == perfectly unique.
    """

    if not (draft and draft.content_md):
        return 1.0, []

    try:
        from bestseller.services.deduplication import (
            compute_jaccard_similarity,
            detect_intra_chapter_repetition,
        )
    except Exception:
        return 1.0, []

    findings: list[dict[str, Any]] = []
    max_similarity = 0.0
    new_text = draft.content_md

    # Inter-chapter comparison against up to 3 previous chapters
    if chapter.chapter_number > 1:
        try:
            _prior_result = await session.execute(
                select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number < chapter.chapter_number,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .order_by(ChapterModel.chapter_number.desc())
                .limit(3)
            )
            prior_rows = list(_prior_result) if _prior_result is not None else []
        except Exception:
            prior_rows = []
        for ch_no, existing_text in prior_rows:
            if not existing_text:
                continue
            similarity = compute_jaccard_similarity(new_text, str(existing_text))
            if similarity > max_similarity:
                max_similarity = similarity
            if similarity >= critical_threshold:
                findings.append(
                    {
                        "severity": "critical",
                        "similarity": round(similarity, 3),
                        "chapter": int(ch_no),
                        "message": (
                            f"[章节重复-严重] 与第{int(ch_no)}章 Jaccard 相似度 {similarity:.1%}，"
                            f"大量段落疑似复用。必须重写以提升差异度。"
                        ),
                    }
                )
            elif similarity >= warning_threshold:
                findings.append(
                    {
                        "severity": "major",
                        "similarity": round(similarity, 3),
                        "chapter": int(ch_no),
                        "message": (
                            f"[章节重复-警告] 与第{int(ch_no)}章 Jaccard 相似度 {similarity:.1%}，"
                            f"请调整段落结构、视角与叙事焦点以拉开差异。"
                        ),
                    }
                )

    # Intra-chapter paraphrase repetition (paragraph-level)
    try:
        intra_findings = detect_intra_chapter_repetition(
            new_text,
            paraphrase_threshold=intra_paraphrase_threshold,
        )
    except TypeError:
        # Fallback for older signature without paraphrase_threshold kwarg
        intra_findings = detect_intra_chapter_repetition(new_text)
    if intra_findings:
        findings.append(
            {
                "severity": "critical" if len(intra_findings) >= 5 else "major",
                "similarity": None,
                "chapter": chapter.chapter_number,
                "message": (
                    f"[章节内部重复] 检测到 {len(intra_findings)} 处段落级重复/近重复，"
                    f"请删除或改写以消除 intra-chapter duplication。"
                ),
            }
        )
        # Penalize duplication_score proportional to duplicate-paragraph count
        max_similarity = max(max_similarity, min(1.0, 0.35 + 0.05 * len(intra_findings)))

    duplication_score = max(0.0, 1.0 - max_similarity)
    return duplication_score, findings


async def _compute_chapter_antagonist_scope_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
) -> tuple[list[ChapterReviewFinding], dict[str, Any]]:
    """Detect when a chapter uses out-of-scope antagonists.

    Runs the per-chapter slice of ``chapter_antagonist_audit`` against
    the project's antagonist_plans. Returns
    ``(findings, evidence_summary_patch)`` — empty if nothing to flag.
    Failure is silent (the review must not block on audit errors).

    Scope / forward-only policy
    ---------------------------

    The gate only fires for chapters that are being *freshly written*
    through the pipeline — it must never retroactively flag finalized
    content. The user's directive (2026-04-21): "已经写完的卷和章节先
    不变。后面的卷和章节做调整." We honour that by skipping:

      * ``status == "complete"`` — canon, don't touch.
      * ``status == "revision"`` — already flagged by a prior review
        cycle; adding another critical finding here would force a
        second rewrite loop on chapters the user asked to leave alone.
      * ``project.metadata_json["b10d_frontier_volume"]`` — optional
        per-project watermark. If set, chapters whose volume_number is
        strictly less than the frontier volume are treated as canon
        even if their status is ``drafting`` / ``review``.

    ``planned`` / ``outlining`` / ``drafting`` / ``review`` chapters
    proceed through the gate.
    """
    if not (draft and draft.content_md):
        return [], {}
    if chapter.volume_id is None:
        return [], {}

    # --- Forward-only scoping -------------------------------------------
    chapter_status = (getattr(chapter, "status", "") or "").lower()
    chapter_first_provenance = any(
        str(item).startswith("chapter_first_scene:")
        for item in (getattr(draft, "assembled_from_scene_draft_ids", None) or [])
    )
    if chapter_status in ("complete", "revision") or chapter_first_provenance:
        logger.debug(
            "chapter_antagonist_audit: skipping ch%d (status=%s) — "
            "already-written/chapter-first content is not retroactively flagged.",
            chapter.chapter_number,
            chapter_status,
        )
        return [], {}

    project_meta = getattr(project, "metadata_json", None) or {}
    frontier_volume_raw = project_meta.get("b10d_frontier_volume")
    try:
        frontier_volume = int(frontier_volume_raw) if frontier_volume_raw else 0
    except (TypeError, ValueError):
        frontier_volume = 0

    try:
        from sqlalchemy import select as _select

        from bestseller.infra.db.models import (
            AntagonistPlanModel,
            VolumeModel,
        )
        from bestseller.services.chapter_antagonist_audit import (
            audit_chapter_against_volume,
            build_volume_antagonist_index,
        )
    except Exception:
        logger.debug("chapter_antagonist_audit import failed", exc_info=True)
        return [], {}

    try:
        volume = await session.scalar(
            _select(VolumeModel).where(VolumeModel.id == chapter.volume_id)
        )
        if volume is None:
            return [], {}
        volume_number = volume.volume_number

        # Honour per-project watermark: anything strictly before the
        # frontier volume is canon and must not be retroactively flagged.
        if frontier_volume and volume_number < frontier_volume:
            logger.debug(
                "chapter_antagonist_audit: skipping ch%d (vol=%d < "
                "frontier=%d) — pre-watermark volumes are canon.",
                chapter.chapter_number,
                volume_number,
                frontier_volume,
            )
            return [], {}

        # Volume count for the project
        volume_count_row = await session.scalar(
            _select(func.count(VolumeModel.id)).where(
                VolumeModel.project_id == project.id
            )
        )
        volume_count = int(volume_count_row or 1)

        plan_rows = list(
            await session.scalars(
                _select(AntagonistPlanModel).where(
                    AntagonistPlanModel.project_id == project.id
                )
            )
        )
        if not plan_rows:
            return [], {}

        plans = []
        for r in plan_rows:
            meta = r.metadata_json or {}
            stages = meta.get("stages_of_relevance") or []
            plans.append(
                {
                    "name": r.antagonist_label,
                    "scope_volume_number": r.scope_volume_number,
                    "stages_of_relevance": stages,
                }
            )

        by_volume, all_names = build_volume_antagonist_index(
            plans, volume_count=max(volume_count, 1)
        )
        audit = audit_chapter_against_volume(
            chapter_number=chapter.chapter_number,
            volume_number=volume_number,
            chapter_text=draft.content_md,
            allowed_in_volume=by_volume.get(volume_number, set()),
            all_antagonist_names=all_names,
            language=getattr(project, "language", None) or "zh-CN",
        )
    except Exception:
        logger.debug(
            "chapter_antagonist_audit signal failed for ch%d",
            chapter.chapter_number,
            exc_info=True,
        )
        return [], {}

    findings: list[ChapterReviewFinding] = []
    for f in audit.findings:
        severity = "critical" if f.severity == "critical" else "major"
        findings.append(
            ChapterReviewFinding(
                category="antagonist_scope",
                severity=severity,
                message=f.message,
            )
        )

    evidence: dict[str, Any] = {}
    if audit.findings:
        evidence = {
            "chapter_antagonist_audit": {
                "volume_number": volume_number,
                "expected_antagonists": list(audit.expected_antagonists),
                "mentioned_expected": list(audit.mentioned_expected),
                "mentioned_out_of_scope": [
                    {"name": n, "count": c}
                    for (n, c) in audit.mentioned_out_of_scope
                ],
                "critical_count": sum(
                    1 for f in audit.findings if f.severity == "critical"
                ),
                "warning_count": sum(
                    1 for f in audit.findings if f.severity == "warning"
                ),
            }
        }
    return findings, evidence


async def _compute_premature_death_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel | None,
) -> tuple[list["ChapterReviewFinding"], dict[str, Any]]:
    """Scan the assembled chapter for death descriptions of characters
    whose planned ``death_chapter_number`` is later than the current
    chapter (the "protected roster"). Critical strong-match findings
    force verdict='rewrite'; implied matches surface as warnings.

    Returns ``(findings, evidence_summary)`` so callers can splice the
    result into an existing ``ChapterReviewResult`` via
    ``_merge_premature_death_into_review``. Empty / dry-run safe.
    """

    if not (draft and draft.content_md):
        return [], {}

    chapter_status = (getattr(chapter, "status", "") or "").lower()
    if chapter_status in ("complete", "revision"):
        # Pre-existing canon must not be retroactively flagged — same
        # forward-only policy used by the antagonist-scope audit.
        return [], {}

    try:
        from bestseller.services.contradiction import (
            check_premature_death_in_prose,
        )
    except Exception:
        logger.debug("premature_death scan import failed", exc_info=True)
        return [], {}

    try:
        violations, warnings = await check_premature_death_in_prose(
            session,
            project.id,
            chapter.chapter_number,
            draft.content_md,
            language=getattr(project, "language", None),
        )
    except Exception:
        logger.debug(
            "premature_death scan failed for ch=%s — non-fatal",
            getattr(chapter, "chapter_number", "?"),
            exc_info=True,
        )
        return [], {}

    if not violations and not warnings:
        return [], {}

    findings: list[ChapterReviewFinding] = []
    for v in violations:
        findings.append(
            ChapterReviewFinding(
                severity="critical",
                category="character_lifecycle",
                code="character_premature_death",
                message=v.message,
                evidence=v.evidence,
            )
        )
    for w in warnings:
        findings.append(
            ChapterReviewFinding(
                severity="major",
                category="character_lifecycle",
                code="character_premature_death_implied",
                message=w.message,
                evidence=w.recommendation,
            )
        )

    evidence_summary = {
        "premature_death_strong": [v.evidence for v in violations],
        "premature_death_implied": [w.recommendation for w in warnings],
    }
    return findings, evidence_summary


def _merge_premature_death_into_review(
    review_result: "ChapterReviewResult",
    findings: list["ChapterReviewFinding"],
    evidence: dict[str, Any],
    *,
    language: str | None = None,
) -> "ChapterReviewResult":
    """Fold premature-death findings into an existing review result.
    Mirrors ``_merge_antagonist_scope_into_review`` so behaviour stays
    consistent: any ``critical`` finding pushes verdict→'rewrite' and
    prepends targeted rewrite instructions.
    """

    if not findings:
        return review_result

    has_critical = any(f.severity == "critical" for f in findings)

    merged_findings = list(review_result.findings) + findings
    merged_evidence = dict(review_result.evidence_summary)
    merged_evidence.update(evidence)

    severity_rank = {"info": 0, "major": 1, "warning": 1, "critical": 2}
    new_severity_max = review_result.severity_max
    for f in findings:
        if severity_rank.get(f.severity, 0) > severity_rank.get(new_severity_max, 0):
            new_severity_max = f.severity

    new_verdict = review_result.verdict
    rewrite_prefix: str | None = None
    if has_critical:
        new_verdict = "rewrite"
        is_en = bool(language and str(language).lower().startswith("en"))
        # Pull names out of finding messages — they appear inside 「」 (zh)
        # or '...' (en).
        protected_names: list[str] = []
        for f in findings:
            if f.severity != "critical":
                continue
            text = f.message or ""
            if "「" in text and "」" in text:
                protected_names.append(text.split("「", 1)[1].split("」", 1)[0])
            elif "'" in text:
                protected_names.append(text.split("'", 1)[1].split("'", 1)[0])
        protected_names = sorted({n for n in protected_names if n})
        if is_en:
            rewrite_prefix = (
                "[character lifecycle] The chapter wrote a death scene for "
                f"protected characters whose planned death is later: {protected_names}. "
                "Rewrite so they stay alive in this chapter — replace the death verbs, "
                "remove 'before X died' framing, and let any threat resolve as "
                "capture / sealing / injury / escape rather than death."
            )
        else:
            rewrite_prefix = (
                f"【角色生命周期】本章为保护角色 {protected_names} 写出了死亡描写，"
                "但其计划死亡发生在更后面的章节。请改写：让其在本章存活——"
                "把死亡动词改为重伤/封印/俘虏/失踪/退场等，"
                "并删除「X死前」「X的遗体」等已死框架。"
            )

    merged_instructions = review_result.rewrite_instructions
    if rewrite_prefix:
        merged_instructions = (
            f"{rewrite_prefix}\n\n{merged_instructions}"
            if merged_instructions
            else rewrite_prefix
        )

    return ChapterReviewResult(
        verdict=new_verdict,
        severity_max=new_severity_max,
        scores=review_result.scores,
        findings=merged_findings,
        evidence_summary=merged_evidence,
        rewrite_instructions=merged_instructions,
    )


# ---------------------------------------------------------------------------
# Continuity gates (Chapter Seam / Stitched Drafts / Name Canon)
#
# These were originally external helpers in
# :mod:`bestseller.services.chapter_seam`,
# :mod:`bestseller.services.deduplication`, and
# :mod:`bestseller.services.character_alias_canon`. The compute / merge pair
# below wires them into ``review_chapter_draft`` so each gate failure folds
# into the chapter review verdict the same way antagonist-scope and
# premature-death do.
#
# Stance reversal is intentionally NOT wired here -- it depends on character
# snapshots that are only refreshed every ``snapshot_policy`` chapters, so
# it belongs in ``MILESTONE_CHECK`` rather than per-chapter review.
# ---------------------------------------------------------------------------


async def _compute_chapter_seam_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel | None,
    tail_chars: int = 800,
    opening_window_chars: int = 300,
) -> tuple[list["ChapterReviewFinding"], dict[str, Any]]:
    """Validate the seam between this chapter's opening and the prior
    chapter's tail. Silent drops of cliffhanger threads (immediate threats,
    locations, key participants, body states, unanswered questions) trigger
    critical findings.

    Skipped when chapter_number == 1 (no prior chapter to seam against).
    """

    if chapter.chapter_number <= 1 or not (draft and draft.content_md):
        return [], {}

    try:
        from bestseller.services.chapter_seam import (
            ThreadKind,
            build_seam_bridge_repair_prompt,
            validate_chapter_seam,
        )
    except Exception:
        logger.debug("chapter_seam import failed", exc_info=True)
        return [], {}

    prev_chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter.chapter_number - 1,
        )
    )
    if prev_chapter is None:
        return [], {}

    prev_draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == prev_chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    prev_text = (prev_draft.content_md or "") if prev_draft else ""
    if not prev_text:
        return [], {}

    try:
        report = validate_chapter_seam(
            prev_text[-tail_chars:],
            draft.content_md,
            opening_window_chars=opening_window_chars,
        )
    except Exception:
        logger.debug(
            "chapter_seam scan failed for ch=%s — non-fatal",
            chapter.chapter_number,
            exc_info=True,
        )
        return [], {}

    if report.passed:
        return [], {}

    findings: list[ChapterReviewFinding] = []
    for drop in report.silent_drops:
        kind = drop.thread.kind
        sev = "critical" if kind == ThreadKind.IMMEDIATE_THREAT else "major"
        findings.append(
            ChapterReviewFinding(
                severity=sev,
                category="chapter_seam",
                message=(
                    f"前章{kind.value} open thread「{drop.thread.marker}」"
                    "在本章开篇 300 字内未被承接、转场或解决。"
                ),
            )
        )

    evidence_summary = {
        "chapter_seam_open_threads": [
            {"kind": t.kind.value, "marker": t.marker} for t in report.open_threads
        ],
        "chapter_seam_silent_drops": [
            {"kind": d.thread.kind.value, "marker": d.thread.marker}
            for d in report.silent_drops
        ],
        "chapter_seam_score": report.score,
        "chapter_seam_repair_prompt": build_seam_bridge_repair_prompt(report),
    }
    return findings, evidence_summary


async def _compute_reader_logic_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel | None,
) -> tuple[list["ChapterReviewFinding"], dict[str, Any]]:
    """Validate reader-visible adjacent chapter state.

    ``chapter_seam`` checks dropped hooks; this gate checks contradictions
    like "did not open 303" after the prior chapter already put the reader
    inside a room, or a 302→303 jump with no movement bridge.
    """

    if chapter.chapter_number <= 1 or not (draft and draft.content_md):
        return [], {}

    try:
        from bestseller.services.reader_logic_gate import (
            build_reader_logic_repair_prompt,
            evaluate_reader_logic_seam,
        )
    except Exception:
        logger.debug("reader_logic_gate import failed", exc_info=True)
        return [], {}

    prev_chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter.chapter_number - 1,
        )
    )
    if prev_chapter is None:
        return [], {}

    prev_draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == prev_chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    prev_text = (prev_draft.content_md or "") if prev_draft else ""
    if not prev_text:
        return [], {}

    try:
        report = evaluate_reader_logic_seam(
            prev_text,
            draft.content_md,
            prev_chapter=int(prev_chapter.chapter_number),
            current_chapter=int(chapter.chapter_number),
        )
    except Exception:
        logger.debug(
            "reader logic seam scan failed for ch=%s — non-fatal",
            chapter.chapter_number,
            exc_info=True,
        )
        return [], {}

    if report.passed:
        return [], {}

    findings = [
        ChapterReviewFinding(
            severity=finding.severity,
            category="reader_logic_seam",
            message=finding.message,
        )
        for finding in report.findings
    ]
    evidence_summary = {
        "reader_logic_findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "evidence": finding.evidence,
            }
            for finding in report.findings
        ],
        "reader_logic_repair_prompt": build_reader_logic_repair_prompt(report),
    }
    return findings, evidence_summary


def _merge_chapter_seam_into_review(
    review_result: "ChapterReviewResult",
    findings: list["ChapterReviewFinding"],
    evidence: dict[str, Any],
    *,
    language: str | None = None,
) -> "ChapterReviewResult":
    if not findings:
        return review_result

    has_critical = any(f.severity == "critical" for f in findings)
    merged_findings = list(review_result.findings) + findings
    merged_evidence = dict(review_result.evidence_summary)
    merged_evidence.update(evidence)

    severity_rank = {"info": 0, "major": 1, "warning": 1, "critical": 2}
    new_severity_max = review_result.severity_max
    for f in findings:
        if severity_rank.get(f.severity, 0) > severity_rank.get(new_severity_max, 0):
            new_severity_max = f.severity

    new_verdict = review_result.verdict
    rewrite_prefix: str | None = None
    if has_critical:
        new_verdict = "rewrite"
        is_en = bool(language and str(language).lower().startswith("en"))
        repair_prompt = evidence.get("chapter_seam_repair_prompt") or ""
        if is_en:
            rewrite_prefix = (
                "[chapter seam] The chapter opening silently dropped a "
                "cliffhanger thread from the previous chapter. Insert a "
                "100-300 char bridge paragraph that resolves, continues, or "
                "explicitly skips over each open thread before entering the "
                "new scene.\n\n" + repair_prompt
            )
        else:
            rewrite_prefix = (
                "【章节断点】本章开篇遗漏了前一章末尾的悬念线索。请在进入新场景之前，"
                "插入 100-300 字的过渡段，对每条 open thread 做承接 / 时间跳跃 / 空间转场 / 屏上解决之一。\n\n"
                + repair_prompt
            )

    merged_instructions = review_result.rewrite_instructions
    if rewrite_prefix:
        merged_instructions = (
            f"{rewrite_prefix}\n\n{merged_instructions}"
            if merged_instructions
            else rewrite_prefix
        )

    return ChapterReviewResult(
        verdict=new_verdict,
        severity_max=new_severity_max,
        scores=review_result.scores,
        findings=merged_findings,
        evidence_summary=merged_evidence,
        rewrite_instructions=merged_instructions,
    )


def _merge_reader_logic_into_review(
    review_result: "ChapterReviewResult",
    findings: list["ChapterReviewFinding"],
    evidence: dict[str, Any],
    *,
    language: str | None = None,
) -> "ChapterReviewResult":
    if not findings:
        return review_result

    has_critical = any(f.severity == "critical" for f in findings)
    merged_findings = list(review_result.findings) + findings
    merged_evidence = dict(review_result.evidence_summary)
    merged_evidence.update(evidence)

    severity_rank = {"info": 0, "major": 1, "warning": 1, "critical": 2}
    new_severity_max = review_result.severity_max
    for f in findings:
        if severity_rank.get(f.severity, 0) > severity_rank.get(new_severity_max, 0):
            new_severity_max = f.severity

    new_verdict = "rewrite" if has_critical else review_result.verdict
    rewrite_prefix: str | None = None
    if has_critical:
        is_en = bool(language and str(language).lower().startswith("en"))
        repair_prompt = evidence.get("reader_logic_repair_prompt") or ""
        if is_en:
            rewrite_prefix = (
                "[reader continuity] The chapter opening contradicts or "
                "teleports away from the prior chapter's visible state. Add a "
                "specific bridge for location, door state, and action result "
                "before entering the new beat.\n\n" + repair_prompt
            )
        else:
            rewrite_prefix = (
                "【读者逻辑断点】本章开篇与上一章的读者可见状态冲突或跳场。"
                "请先补清位置、门状态、上一动作结果，再进入新事件。\n\n"
                + repair_prompt
            )

    merged_instructions = review_result.rewrite_instructions
    if rewrite_prefix:
        merged_instructions = (
            f"{rewrite_prefix}\n\n{merged_instructions}"
            if merged_instructions
            else rewrite_prefix
        )

    return ChapterReviewResult(
        verdict=new_verdict,
        severity_max=new_severity_max,
        scores=review_result.scores,
        findings=merged_findings,
        evidence_summary=merged_evidence,
        rewrite_instructions=merged_instructions,
    )


def _load_character_canon_for_project(project: ProjectModel) -> Any:
    """Load ``character-aliases.yaml`` for a project, looking under the
    Mode B output dir. Returns empty canon if file missing.
    """
    from pathlib import Path

    from bestseller.services.character_alias_canon import (
        CharacterCanon,
        load_character_canon,
    )

    # Mode B convention: output/ai-generated/{slug}/story-bible/character-aliases.yaml
    # Mode A convention: output/{slug}/story-bible/character-aliases.yaml
    candidates = [
        Path("output/ai-generated") / project.slug / "story-bible" / "character-aliases.yaml",
        Path("output") / project.slug / "story-bible" / "character-aliases.yaml",
    ]
    for path in candidates:
        if path.exists():
            return load_character_canon(path)
    return CharacterCanon.empty()


async def _compute_stitched_draft_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel | None,
) -> tuple[list["ChapterReviewFinding"], dict[str, Any]]:
    """Detect intra-chapter stitched drafts. Uses the project's character
    canon as participant pool when available -- this kills the prose-token
    pollution that drags Jaccard similarities below the threshold."""

    if not (draft and draft.content_md):
        return [], {}

    try:
        from bestseller.services.deduplication import (
            build_stitched_draft_repair_prompt,
            detect_intra_chapter_stitched_drafts,
        )
    except Exception:
        logger.debug("deduplication import failed", exc_info=True)
        return [], {}

    canon = _load_character_canon_for_project(project)
    name_pool = (
        frozenset(canon.spelling_to_canonical.keys())
        if canon and canon.entries
        else None
    )

    try:
        results = detect_intra_chapter_stitched_drafts(
            draft.content_md, name_pool=name_pool,
        )
    except Exception:
        logger.debug(
            "stitched-draft scan failed for ch=%s — non-fatal",
            chapter.chapter_number,
            exc_info=True,
        )
        return [], {}

    if not results:
        return [], {}

    findings: list[ChapterReviewFinding] = []
    for finding in results:
        shared = sorted(
            finding.block_a.participants & finding.block_b.participants
        )
        findings.append(
            ChapterReviewFinding(
                severity="critical",
                category="stitched_draft",
                message=(
                    f"检测到拼接稿（事件签名相似度 {finding.similarity}）："
                    f"段落 #{finding.block_a.block_index} 与 #{finding.block_b.block_index} "
                    f"共享 {shared}，疑似同一事件的两版草稿被同时保留。"
                ),
            )
        )

    evidence_summary = {
        "stitched_draft_pairs": [
            {
                "a_idx": f.block_a.block_index,
                "b_idx": f.block_b.block_index,
                "similarity": f.similarity,
                "shared": sorted(f.block_a.participants & f.block_b.participants),
                "conflicts": list(f.conflicts),
            }
            for f in results
        ],
        "stitched_draft_repair_prompt": build_stitched_draft_repair_prompt(results),
    }
    return findings, evidence_summary


def _merge_stitched_draft_into_review(
    review_result: "ChapterReviewResult",
    findings: list["ChapterReviewFinding"],
    evidence: dict[str, Any],
    *,
    language: str | None = None,
) -> "ChapterReviewResult":
    if not findings:
        return review_result

    merged_findings = list(review_result.findings) + findings
    merged_evidence = dict(review_result.evidence_summary)
    merged_evidence.update(evidence)

    severity_rank = {"info": 0, "major": 1, "warning": 1, "critical": 2}
    new_severity_max = review_result.severity_max
    for f in findings:
        if severity_rank.get(f.severity, 0) > severity_rank.get(new_severity_max, 0):
            new_severity_max = f.severity

    new_verdict = "rewrite"  # stitched drafts are always must_rewrite
    is_en = bool(language and str(language).lower().startswith("en"))
    repair_prompt = evidence.get("stitched_draft_repair_prompt") or ""
    if is_en:
        rewrite_prefix = (
            "[stitched draft] One or more pairs of paragraphs appear to be "
            "alternative drafts of the same plot beat (same participants + "
            "same key prop, paraphrased prose). Keep ONE version and DELETE "
            "the other -- do NOT merge (merging keeps contradictory props "
            "from both drafts).\n\n" + repair_prompt
        )
    else:
        rewrite_prefix = (
            "【拼接稿】检测到本章有疑似同事件的两版草稿被同时保留。"
            "请二选一保留，删除另一段；禁止合并（合并会保留两版的道具/动作矛盾）。\n\n"
            + repair_prompt
        )

    merged_instructions = (
        f"{rewrite_prefix}\n\n{review_result.rewrite_instructions}"
        if review_result.rewrite_instructions
        else rewrite_prefix
    )

    return ChapterReviewResult(
        verdict=new_verdict,
        severity_max=new_severity_max,
        scores=review_result.scores,
        findings=merged_findings,
        evidence_summary=merged_evidence,
        rewrite_instructions=merged_instructions,
    )


async def _compute_name_canon_signal(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel | None,
) -> tuple[list["ChapterReviewFinding"], dict[str, Any]]:
    """Validate character names in the chapter against the project's
    character-aliases.yaml canon. Skips silently when no canon exists --
    the gate is opt-in via the presence of the YAML file.
    """

    if not (draft and draft.content_md):
        return [], {}

    try:
        from bestseller.services.character_alias_canon import (
            build_name_canon_repair_prompt,
            validate_chapter_name_canon,
        )
    except Exception:
        logger.debug("character_alias_canon import failed", exc_info=True)
        return [], {}

    canon = _load_character_canon_for_project(project)
    if not canon.entries:
        return [], {}

    try:
        violations = validate_chapter_name_canon(draft.content_md, canon)
    except Exception:
        logger.debug(
            "name-canon scan failed for ch=%s — non-fatal",
            chapter.chapter_number,
            exc_info=True,
        )
        return [], {}

    if not violations:
        return [], {}

    findings: list[ChapterReviewFinding] = []
    for v in violations:
        sev = "critical" if v.kind == "forbidden_collision" else "major"
        findings.append(
            ChapterReviewFinding(
                severity=sev,
                category="name_canon",
                message=(
                    f"L{v.line_no} [{v.kind}] 「{v.spelling}」 — {v.suggestion}"
                ),
            )
        )

    evidence_summary = {
        "name_canon_violations": [
            {
                "spelling": v.spelling,
                "line_no": v.line_no,
                "kind": v.kind,
                "excerpt": v.excerpt,
                "suggestion": v.suggestion,
            }
            for v in violations
        ],
        "name_canon_repair_prompt": build_name_canon_repair_prompt(violations),
    }
    return findings, evidence_summary


def _merge_name_canon_into_review(
    review_result: "ChapterReviewResult",
    findings: list["ChapterReviewFinding"],
    evidence: dict[str, Any],
    *,
    language: str | None = None,
) -> "ChapterReviewResult":
    if not findings:
        return review_result

    has_critical = any(f.severity == "critical" for f in findings)
    merged_findings = list(review_result.findings) + findings
    merged_evidence = dict(review_result.evidence_summary)
    merged_evidence.update(evidence)

    severity_rank = {"info": 0, "major": 1, "warning": 1, "critical": 2}
    new_severity_max = review_result.severity_max
    for f in findings:
        if severity_rank.get(f.severity, 0) > severity_rank.get(new_severity_max, 0):
            new_severity_max = f.severity

    new_verdict = review_result.verdict
    rewrite_prefix: str | None = None
    if has_critical:
        new_verdict = "rewrite"
        is_en = bool(language and str(language).lower().startswith("en"))
        repair_prompt = evidence.get("name_canon_repair_prompt") or ""
        if is_en:
            rewrite_prefix = (
                "[name canon] One or more character-name spellings collide "
                "with another character per the project's character-aliases.yaml. "
                "Replace each collision with the canonical form, OR if it is "
                "genuinely a new character, register it in the canon first.\n\n"
                + repair_prompt
            )
        else:
            rewrite_prefix = (
                "【人名 Canon】本章使用了与其他角色易混的人名拼写（character-aliases.yaml 标记的 forbidden_collisions）。"
                "请改为 canonical 形式；若确为新角色，先在 yaml 中追加条目。\n\n"
                + repair_prompt
            )

    merged_instructions = review_result.rewrite_instructions
    if rewrite_prefix:
        merged_instructions = (
            f"{rewrite_prefix}\n\n{merged_instructions}"
            if merged_instructions
            else rewrite_prefix
        )

    return ChapterReviewResult(
        verdict=new_verdict,
        severity_max=new_severity_max,
        scores=review_result.scores,
        findings=merged_findings,
        evidence_summary=merged_evidence,
        rewrite_instructions=merged_instructions,
    )


def _merge_antagonist_scope_into_review(
    review_result: ChapterReviewResult,
    antagonist_findings: list[ChapterReviewFinding],
    antagonist_evidence: dict[str, Any],
    *,
    language: str | None = None,
) -> ChapterReviewResult:
    """Fold antagonist-scope findings into an existing ChapterReviewResult.

    Critical antagonist findings force verdict='rewrite' and
    severity_max='critical', and prepend rewrite_instructions so the
    rewrite prompt tells the writer which antagonist to stop using.
    """
    if not antagonist_findings:
        return review_result

    has_critical = any(f.severity == "critical" for f in antagonist_findings)

    merged_findings = list(review_result.findings) + antagonist_findings
    evidence = dict(review_result.evidence_summary)
    evidence.update(antagonist_evidence)

    new_severity_max = review_result.severity_max
    severity_rank = {"info": 0, "major": 1, "warning": 1, "critical": 2}
    for f in antagonist_findings:
        if severity_rank.get(f.severity, 0) > severity_rank.get(new_severity_max, 0):
            new_severity_max = f.severity

    new_verdict = review_result.verdict
    rewrite_prefix: str | None = None
    if has_critical:
        new_verdict = "rewrite"
        is_en = bool(language and str(language).lower().startswith("en"))
        bad_names = [
            f.message.split("『")[1].split("』")[0]
            if "『" in f.message and "』" in f.message
            else None
            for f in antagonist_findings
            if f.severity == "critical"
        ]
        bad_names = sorted({n for n in bad_names if n})
        if is_en:
            rewrite_prefix = (
                "[antagonist scope] Remove present-tense use of out-of-scope "
                f"antagonist(s): {bad_names}. Only antagonists scoped to this "
                "volume may act in the chapter; earlier-volume bosses are only "
                "allowed as brief past-tense flashback references."
            )
        else:
            rewrite_prefix = (
                f"【敌人范围】必须移除当下视角对非本卷敌人的使用：{bad_names}。"
                "本章只能让本卷所属敌人实际行动，他卷敌人仅允许以简短回忆形式出现。"
            )

    merged_instructions = review_result.rewrite_instructions
    if rewrite_prefix:
        merged_instructions = (
            f"{rewrite_prefix}\n\n{merged_instructions}"
            if merged_instructions
            else rewrite_prefix
        )

    return ChapterReviewResult(
        verdict=new_verdict,
        severity_max=new_severity_max,
        scores=review_result.scores,
        findings=merged_findings,
        evidence_summary=evidence,
        rewrite_instructions=merged_instructions,
    )


def _merge_llm_quality_judge_into_chapter_review(
    review_result: ChapterReviewResult,
    *,
    category: str,
    message_prefix: str,
    judge_result: Any,
) -> ChapterReviewResult:
    issues = [
        f"{issue.code}: {issue.required_fix or issue.evidence}"
        for issue in getattr(judge_result, "blocking_issues", ())[:8]
    ]
    rewrite_plan = getattr(judge_result, "rewrite_plan", None)
    rewrite_instructions = (
        getattr(rewrite_plan, "instructions", None)
        or "\n".join(issues)
        or f"{message_prefix}未达标，请重写本章。"
    )
    return ChapterReviewResult(
        verdict="rewrite",
        scores=review_result.scores,
        findings=[
            *review_result.findings,
            ChapterReviewFinding(
                category=category,
                severity="critical" if getattr(judge_result, "has_critical", False) else "major",
                message=(
                    f"{message_prefix}未达标："
                    f"{issues[0] if issues else 'overall score below threshold'}"
                ),
            ),
        ],
        severity_max="critical"
        if getattr(judge_result, "has_critical", False)
        else review_result.severity_max,
        evidence_summary=review_result.evidence_summary,
        rewrite_instructions=rewrite_instructions,
    )


def _merge_chapter_quality_bundle_into_review(
    review_result: ChapterReviewResult,
    report: ChapterQualityBundleReport,
    *,
    language: str | None = None,
) -> ChapterReviewResult:
    """Promote unified quality snapshot blockers into the review loop.

    The export layer reruns the same bundle as a final defense. If this review
    pass does not merge the bundle first, a chapter can be marked complete by
    the LLM/rule review and only fail when the frontend export tries to read it.
    """

    blocking_findings = report.blocking_findings
    if not blocking_findings:
        return review_result

    codes = list(dict.fromkeys(f.code for f in blocking_findings if f.code))
    issue_lines = [
        f"{finding.code}: {finding.repair_hint or finding.repair_scope}"
        for finding in blocking_findings[:8]
    ]
    playbooks = render_quality_repair_playbooks(codes)
    is_en = is_english_language(language)
    if is_en:
        rewrite_prefix = (
            "Unified quality snapshot blocked publication before export. "
            "Fix these exact blocking findings and return a complete chapter "
            "without changing unrelated plot beats:\n"
            + "\n".join(f"- {line}" for line in issue_lines)
        )
        if playbooks:
            rewrite_prefix += "\n\nRepair playbooks:\n" + playbooks
    else:
        rewrite_prefix = (
            "【统一质量快照未通过】导出前硬门禁已阻断本章。请只修复以下阻断项，"
            "输出完整章节正文，不要改动无关剧情节拍：\n"
            + "\n".join(f"- {line}" for line in issue_lines)
        )
        if playbooks:
            rewrite_prefix += "\n\n【修复打法】\n" + playbooks

    return ChapterReviewResult(
        verdict="rewrite",
        scores=review_result.scores,
        findings=[
            *review_result.findings,
            *(
                ChapterReviewFinding(
                    category="chapter_quality_bundle",
                    severity="critical",
                    message=(
                        f"统一质量快照阻断：{finding.code} — "
                        f"{finding.repair_hint or finding.repair_scope}"
                    ),
                )
                for finding in blocking_findings
            ),
        ],
        severity_max="critical",
        evidence_summary={
            **review_result.evidence_summary,
            "chapter_quality_bundle": report.to_dict(),
        },
        rewrite_instructions=(
            f"{rewrite_prefix}\n\n{review_result.rewrite_instructions}"
            if review_result.rewrite_instructions
            else rewrite_prefix
        ),
    )


async def _evaluate_chapter_quality_bundle_for_review(
    *,
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
) -> ChapterQualityBundleReport | None:
    commercial_quality_required = (
        bool(settings.pipeline.commercial_strict_quality_mode)
        and int(getattr(project, "target_chapters", 0) or 0)
        >= int(settings.pipeline.commercial_planning_min_target_chapters)
    )
    if not commercial_quality_required:
        return None

    previous_chapter_texts = await _collect_previous_current_chapter_texts(
        session,
        project=project,
        chapter_number=chapter.chapter_number,
    )
    previous_chapter_number = previous_chapter_texts[-1][0] if previous_chapter_texts else None
    previous_chapter_text = previous_chapter_texts[-1][1] if previous_chapter_texts else None
    report = run_chapter_quality_bundle(
        draft.content_md or "",
        ChapterQualityBundleContext(
            chapter_number=chapter.chapter_number,
            previous_chapter_text=previous_chapter_text,
            previous_chapter_position=previous_chapter_number,
            previous_chapter_texts=previous_chapter_texts,
            total_chapters=int(getattr(project, "target_chapters", 0) or 500),
            language=getattr(project, "language", None) or "zh-CN",
            target_chapter_words=int(settings.generation.words_per_chapter.target),
            commercial_strict=bool(settings.pipeline.commercial_strict_quality_mode),
        ),
    )
    _stamp_chapter_quality_bundle(chapter, report)
    return report


def _merge_llm_judge_exception_into_chapter_review(
    review_result: ChapterReviewResult,
    *,
    category: str,
    message_prefix: str,
    error: Exception,
) -> ChapterReviewResult:
    message = f"{message_prefix}执行失败，严格模式下不能放行：{type(error).__name__}: {error}"
    instructions = (
        f"{message}\n请先恢复该裁判链路，或重跑本章并取得有效评测结果。"
    )
    return ChapterReviewResult(
        verdict="rewrite",
        scores=review_result.scores,
        findings=[
            *review_result.findings,
            ChapterReviewFinding(
                category=category,
                severity="critical",
                message=message,
            ),
        ],
        severity_max="critical",
        evidence_summary={
            **review_result.evidence_summary,
            f"{category}_exception": {
                "error_type": type(error).__name__,
                "message": str(error),
                "strict_block": True,
            },
        },
        rewrite_instructions=(
            f"{instructions}\n\n{review_result.rewrite_instructions}"
            if review_result.rewrite_instructions
            else instructions
        ),
    )


async def _recent_chapter_window_payload(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    window_size: int,
) -> list[dict[str, Any]]:
    start_chapter = max(1, int(chapter.chapter_number) - max(1, int(window_size)) + 1)
    rows = await session.execute(
        select(ChapterModel, ChapterDraftVersionModel)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number >= start_chapter,
            ChapterModel.chapter_number <= chapter.chapter_number,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    payload: list[dict[str, Any]] = []
    for row_chapter, row_draft in rows:
        content_md = draft.content_md if row_chapter.id == chapter.id else row_draft.content_md
        payload.append(
            {
                "chapter_number": row_chapter.chapter_number,
                "title": row_chapter.title,
                "chapter_goal": row_chapter.chapter_goal,
                "hook_description": row_chapter.hook_description,
                "current_word_count": row_chapter.current_word_count,
                "production_state": row_chapter.production_state,
                "metadata": _window_judge_safe_metadata(row_chapter.metadata_json or {}),
                "content_excerpt": (content_md or "")[:7000],
            }
        )
    if not any(item["chapter_number"] == chapter.chapter_number for item in payload):
        payload.append(
            {
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "chapter_goal": chapter.chapter_goal,
                "hook_description": chapter.hook_description,
                "current_word_count": draft.word_count,
                "production_state": chapter.production_state,
                "metadata": _window_judge_safe_metadata(chapter.metadata_json or {}),
                "content_excerpt": (draft.content_md or "")[:7000],
            }
        )
    return payload


_WINDOW_JUDGE_METADATA_ALLOWED_KEYS = {
    "chapter_contract",
    "generation_input_stamp",
    "methodology_contract",
    "quality_targets",
    "reader_contract",
}
_WINDOW_JUDGE_METADATA_DENY_MARKERS = (
    "auto_repair",
    "block",
    "failed",
    "finding",
    "gate",
    "last_",
    "repair",
    "retry",
)


def _window_judge_safe_metadata(metadata: Any, *, depth: int = 0) -> dict[str, Any]:
    if not isinstance(metadata, dict) or depth > 3:
        return {}
    safe: dict[str, Any] = {}
    for raw_key, raw_value in metadata.items():
        key = str(raw_key)
        key_lc = key.lower()
        if depth == 0 and key not in _WINDOW_JUDGE_METADATA_ALLOWED_KEYS:
            continue
        if any(marker in key_lc for marker in _WINDOW_JUDGE_METADATA_DENY_MARKERS):
            continue
        safe_value = _window_judge_safe_metadata_value(raw_value, depth=depth + 1)
        if safe_value not in ({}, [], (), None, ""):
            safe[key] = safe_value
    return safe


def _window_judge_safe_metadata_value(value: Any, *, depth: int) -> Any:
    if depth > 3:
        return None
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            key_lc = key.lower()
            if any(marker in key_lc for marker in _WINDOW_JUDGE_METADATA_DENY_MARKERS):
                continue
            cleaned_value = _window_judge_safe_metadata_value(raw_value, depth=depth + 1)
            if cleaned_value not in ({}, [], (), None, ""):
                cleaned[key] = cleaned_value
        return cleaned
    if isinstance(value, (list, tuple)):
        return [
            item
            for item in (
                _window_judge_safe_metadata_value(item, depth=depth + 1) for item in value[:12]
            )
            if item not in ({}, [], (), None, "")
        ]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _volume_entry_from_project_metadata(
    project: ProjectModel,
    *,
    volume_number: int | None,
) -> dict[str, Any]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    volume_plan: Any = metadata.get("volume_plan")
    if isinstance(volume_plan, dict):
        candidate = volume_plan.get("volumes")
        volume_plan = candidate if isinstance(candidate, list) else volume_plan
    if isinstance(volume_plan, list):
        for item in volume_plan:
            if not isinstance(item, dict):
                continue
            try:
                item_volume = int(item.get("volume_number") or 0)
            except (TypeError, ValueError):
                item_volume = 0
            if volume_number is None or item_volume == volume_number:
                return item
    return {}


async def _current_volume_payload(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    volume_number: int | None = None
    if chapter.volume_id is not None:
        volume = await session.scalar(
            select(VolumeModel).where(VolumeModel.id == chapter.volume_id)
        )
        if volume is not None:
            volume_number = int(volume.volume_number)
    chapters_query = (
        select(ChapterModel, ChapterDraftVersionModel)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number <= chapter.chapter_number,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    if volume_number is not None:
        chapters_query = chapters_query.join(
            VolumeModel,
            ChapterModel.volume_id == VolumeModel.id,
        ).where(VolumeModel.volume_number == volume_number)
    rows = await session.execute(chapters_query)
    chapter_summaries: list[dict[str, Any]] = []
    for row_chapter, row_draft in rows:
        content_md = draft.content_md if row_chapter.id == chapter.id else row_draft.content_md
        chapter_summaries.append(
            {
                "chapter_number": row_chapter.chapter_number,
                "title": row_chapter.title,
                "chapter_goal": row_chapter.chapter_goal,
                "hook_description": row_chapter.hook_description,
                "status": row_chapter.status,
                "production_state": row_chapter.production_state,
                "word_count": row_draft.word_count,
                "content_excerpt": (content_md or "")[:5000],
            }
        )
    return (
        _volume_entry_from_project_metadata(project, volume_number=volume_number),
        chapter_summaries,
    )


def _should_run_volume_checkpoint_judge(
    *,
    chapter_number: int,
    interval: int,
    min_chapters: int,
) -> bool:
    """Run volume alignment only when the chapter window is mature enough.

    Early chapters should be judged by chapter/scene commercial gates.  Running
    a full-volume checkpoint on chapter 1-3 caused the judge to demand future
    reveal payoffs and create hallucinated blocking rewrites.
    """

    if interval <= 0:
        return False
    if chapter_number < max(1, min_chapters):
        return False
    return chapter_number % interval == 0


def _float_from_payload(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 10.0:
        parsed = parsed / 100.0
    elif parsed > 1.0:
        parsed = parsed / 10.0
    return max(0.0, min(1.0, parsed))


def _can_accept_llm_pass_over_rule_rewrite(
    review_result: ChapterReviewResult,
    llm_payload: dict[str, object],
) -> bool:
    if review_result.verdict == "pass":
        return False
    if llm_payload.get("pass") is not True:
        return False
    blocking_issues = llm_payload.get("blocking_issues")
    if isinstance(blocking_issues, list) and blocking_issues:
        return False
    if not review_result.findings:
        return False
    categories = {finding.category for finding in review_result.findings}
    return categories <= _LLM_PASS_OVERRIDABLE_RULE_CATEGORIES


def _downgrade_rule_rewrite_after_llm_pass(
    review_result: ChapterReviewResult,
    llm_payload: dict[str, object],
) -> ChapterReviewResult:
    dimension_scores = llm_payload.get("dimension_scores")
    if not isinstance(dimension_scores, dict):
        dimension_scores = {}
    overall_score = _float_from_payload(llm_payload.get("overall_score"))
    hook_score = max(
        (
            score
            for score in (
                _float_from_payload(dimension_scores.get("hook_strength")),
                _float_from_payload(dimension_scores.get("opening_pull")),
                _float_from_payload(dimension_scores.get("commercial_pull")),
            )
            if score is not None
        ),
        default=None,
    )
    contract_score = max(
        (
            score
            for score in (
                _float_from_payload(dimension_scores.get("methodology_compliance")),
                _float_from_payload(dimension_scores.get("scene_execution")),
                _float_from_payload(dimension_scores.get("commercial_pull")),
            )
            if score is not None
        ),
        default=None,
    )
    score_updates: dict[str, float] = {}
    if overall_score is not None:
        score_updates["overall"] = max(review_result.scores.overall, overall_score)
    if hook_score is not None:
        score_updates["hook"] = max(review_result.scores.hook, hook_score)
        score_updates["ending_hook_effectiveness"] = max(
            review_result.scores.ending_hook_effectiveness,
            hook_score,
        )
    if contract_score is not None:
        score_updates["contract_alignment"] = max(
            review_result.scores.contract_alignment,
            contract_score,
        )
    return ChapterReviewResult(
        verdict="pass",
        scores=review_result.scores.model_copy(update=score_updates),
        findings=review_result.findings,
        severity_max="low",
        evidence_summary={
            **review_result.evidence_summary,
            "rule_rewrite_downgraded_by_llm_pass": {
                "reason": (
                    "商业 LLM 裁判已通过且无 blocking issue；旧规则低分仅来自可审计的"
                    "启发式章节推进/尾钩/合同匹配项。"
                ),
                "original_verdict": review_result.verdict,
                "original_severity_max": review_result.severity_max,
                "original_findings": [
                    finding.model_dump(mode="json") for finding in review_result.findings
                ],
            },
        },
        rewrite_instructions=None,
    )


def _collect_chapter_rewrite_hard_constraints(
    chapter: ChapterModel,
    scenes: list[SceneCardModel] | tuple[SceneCardModel, ...],
) -> tuple[str, list[str]]:
    metadata = chapter.metadata_json or {}
    object_signal = (
        metadata.get("object_signal_contract") if isinstance(metadata, dict) else {}
    )
    foreshadowing = (
        chapter.foreshadowing_actions if isinstance(chapter.foreshadowing_actions, dict) else {}
    )
    forbidden_terms: list[str] = []
    lines: list[str] = []
    if isinstance(object_signal, dict):
        chapter_mode = str(object_signal.get("chapter_mode") or "").strip()
        if chapter_mode:
            lines.append(f"- 物件信号合同：{chapter_mode}")
        for key in ("forbidden_signals", "forbidden_terms"):
            values = object_signal.get(key)
            if isinstance(values, list):
                forbidden_terms.extend(str(item).strip() for item in values if str(item).strip())
    if int(chapter.chapter_number or 0) <= 10:
        forbidden_terms.extend(_front10_forbidden_signal_terms(chapter))
    if isinstance(foreshadowing, dict):
        values = foreshadowing.get("forbidden_early_leaks")
        if isinstance(values, list):
            forbidden_terms.extend(str(item).strip() for item in values if str(item).strip())
    scene_forbidden: list[str] = []
    seen_scene_forbidden: set[str] = set()
    for scene in scenes:
        for action in getattr(scene, "forbidden_actions", None) or []:
            text = str(action or "").strip()
            if text and text not in seen_scene_forbidden:
                seen_scene_forbidden.add(text)
                scene_forbidden.append(text)
                forbidden_terms.extend(_forbidden_rewrite_terms_from_scene_action(text))
    if scene_forbidden:
        lines.append("- 场景卡禁写动作：" + "；".join(scene_forbidden[:30]))
    unique_terms = []
    seen: set[str] = set()
    for term in forbidden_terms:
        if term and term not in seen:
            seen.add(term)
            unique_terms.append(term)
    if unique_terms:
        lines.append("- 禁写/暂缓词：" + "、".join(unique_terms[:24]))
    if not lines:
        return "", unique_terms
    guard = (
        "【章节硬约束优先级】以下约束来自章节细纲、物件信号合同和场景卡，"
        "优先级高于本次评审建议；如果评审建议与这些约束冲突，删除冲突建议，"
        "只按硬约束修。\n"
        + "\n".join(lines)
    )
    return guard, unique_terms


def _forbidden_rewrite_terms_from_scene_action(action_text: str) -> list[str]:
    """Extract high-risk terms from scene-card prohibitions for rewrite filtering.

    Scene cards often phrase constraints as full sentences ("不得写电话、寄件、
    快递...").  The rewrite sanitizer needs the contained tokens so stale LLM
    review advice cannot reintroduce a forbidden direction.
    """

    if not action_text:
        return []
    candidates = (
        "电话",
        "来电",
        "手机通知",
        "微信",
        "短信",
        "语音",
        "录音",
        "寄件",
        "快递",
        "外卖",
        "配送",
        "配送单",
        "物流",
        "半夜等单",
        "送个单",
        "票据",
        "单子",
        "帮忙寄件",
        "跑腿",
        "铜钱按",
        "铜钱接触",
        "黑水",
        "门吞掉",
        "被门吞掉",
        "被镜子吞掉",
        "拖进门",
        "门合拢",
        "确认死亡",
        "电梯脚印",
        "黑泥鞋印",
        "水渍脚印",
        "新脚",
        "陈默",
        "七号入账",
        "代父",
        "入门",
        "归人",
        "张家门契",
        "三代以内",
        "血债血偿",
        "八个人影",
        "七行名单",
        "病号服",
    )
    return [term for term in candidates if term in action_text]


def _is_prohibition_instruction(line: str) -> bool:
    return any(token in line for token in ("不得", "禁止", "删除", "避免", "不能", "不要", "改掉", "清除"))


def _sanitize_chapter_review_rewrite_instructions(
    instructions: str | None,
    *,
    chapter: ChapterModel,
    scenes: list[SceneCardModel] | tuple[SceneCardModel, ...],
) -> str | None:
    guard, forbidden_terms = _collect_chapter_rewrite_hard_constraints(chapter, scenes)
    raw = (instructions or "").strip()
    if not guard and not raw:
        return None
    kept_lines: list[str] = []
    removed_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append(line)
            continue
        conflicts = any(term and term in stripped for term in forbidden_terms)
        if conflicts and not _is_prohibition_instruction(stripped):
            removed_lines.append(stripped)
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    if removed_lines:
        removal_note = (
            "【已过滤冲突评审建议】有 "
            f"{len(removed_lines)} 条评审建议与章节硬约束冲突，已删除，"
            "重写时不得恢复这些被删除方向。"
        )
        cleaned = f"{removal_note}\n\n{cleaned}" if cleaned else removal_note
    if guard:
        cleaned = f"{guard}\n\n{cleaned}" if cleaned else guard
    return cleaned or None


async def review_chapter_draft(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[ChapterReviewResult, ReviewReportModel, QualityScoreModel, RewriteTaskModel | None]:
    project, chapter, _style_guide, scenes, draft = await _load_chapter_context(
        session,
        project_slug,
        chapter_number,
    )
    try:
        chapter_context = await build_chapter_writer_context(
            session,
            settings,
            project_slug,
            chapter_number,
        )
    except ValueError:
        chapter_context = None

    ch_duplication_score, ch_duplication_findings = await _compute_chapter_duplication_signal(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
    )

    review_result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=draft,
        settings=settings,
        chapter_contract=getattr(chapter_context, "chapter_contract", None),
        chapter_context=chapter_context,
        genre=project.genre,
        sub_genre=project.sub_genre,
        language=getattr(project, "language", None),
        duplication_score=ch_duplication_score,
        duplication_findings=ch_duplication_findings,
    )
    methodology_reports = await _compute_chapter_methodology_reports(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
        chapter_context=chapter_context,
    )
    if methodology_reports:
        review_result = merge_methodology_reports_into_chapter_review(
            review_result,
            methodology_reports,
            language=getattr(project, "language", None),
        )

    hook_ledger_audit = await compute_hook_ledger_audit_for_review(
        session=session,
        project=project,
        chapter=chapter,
        chapter_contract=getattr(chapter_context, "chapter_contract", None),
    )
    review_result = merge_hook_ledger_audit_into_chapter_review(
        review_result,
        hook_ledger_audit,
        chapter_number=chapter.chapter_number,
        language=getattr(project, "language", None),
    )

    payoff_ledger_audit = await compute_payoff_ledger_audit_for_review(
        session=session,
        project=project,
        chapter=chapter,
        chapter_contract=getattr(chapter_context, "chapter_contract", None),
    )
    review_result = merge_payoff_ledger_audit_into_chapter_review(
        review_result,
        payoff_ledger_audit,
        chapter_number=chapter.chapter_number,
        language=getattr(project, "language", None),
        chapter_contract=getattr(chapter_context, "chapter_contract", None),
    )

    # LLM output gate: when v2 is enabled, require the planner LLM to have
    # declared at least one ``methodology_contract.payoffs_due`` entry.  We
    # deliberately read ``methodology_declared_payoffs`` (the planner-LLM-only
    # field), not ``due_payoff_codes`` (the union of LLM + ``PayoffModel``
    # column) — otherwise the gate would falsely pass when the column has
    # codes from earlier chapters but the current LLM call produced
    # nothing.  Empty declaration triggers a soft warning only; severity_max is
    # raised to warning but verdict is not promoted because the gate's job is
    # to *flag* a drift, not to block publication.
    if is_methodology_v2_enabled():
        declared_payoffs = list(
            (getattr(chapter_context, "chapter_contract", None)
             and (
                 getattr(
                     chapter_context.chapter_contract,
                     "methodology_declared_payoffs",
                     None,
                 )
                 or []
             ))
            or []
        )
        if not declared_payoffs:
            from bestseller.services.payoff_ledger_runtime import _max_severity as _pl_max

            gate_finding = ChapterReviewFinding(
                category="payoff_ledger",
                severity="warning",
                message=(
                    "PAYOFFS_DUE_EMPTY: planner LLM did not declare any "
                    "methodology_contract.payoffs_due for this chapter. "
                    "Re-plan with explicit payoff list."
                ),
            )
            review_result = ChapterReviewResult(
                verdict=review_result.verdict,
                severity_max=_pl_max(
                    review_result.severity_max, ("warning",)
                ),
                scores=review_result.scores,
                findings=[*review_result.findings, gate_finding],
                evidence_summary={
                    **review_result.evidence_summary,
                    "payoff_ledger_output_gate": {
                        "v2_enabled": True,
                        "declared_count": 0,
                        "verdict_action": "warning_only",
                        "source_field": "methodology_declared_payoffs",
                    },
                },
                rewrite_instructions=review_result.rewrite_instructions,
            )

    # Antagonist-scope gate (B10d): after the rule-based evaluator, fold
    # in the per-chapter antagonist audit so chapters that carry a
    # foreign-volume antagonist as the present-tense enemy are rerouted
    # to a rewrite with specific instructions.
    antagonist_findings, antagonist_evidence = (
        await _compute_chapter_antagonist_scope_signal(
            session=session,
            project=project,
            chapter=chapter,
            draft=draft,
        )
    )
    if antagonist_findings:
        review_result = _merge_antagonist_scope_into_review(
            review_result,
            antagonist_findings,
            antagonist_evidence,
            language=getattr(project, "language", None),
        )

    # Premature-death scan: catches the inverse of resurrection — prose
    # that describes a character dying before their planned death chapter.
    # Without this, the writer LLM can ship a death scene for a protected
    # character (the ch6 苏瑶 / 陆沉 incident) and the resurrection check
    # passes because the character isn't yet dead in the database.
    pdeath_findings, pdeath_evidence = (
        await _compute_premature_death_signal(
            session=session,
            project=project,
            chapter=chapter,
            draft=draft,
        )
    )
    if pdeath_findings:
        review_result = _merge_premature_death_into_review(
            review_result,
            pdeath_findings,
            pdeath_evidence,
            language=getattr(project, "language", None),
        )

    # Continuity gates -- chapter seam (vs prior chapter tail), intra-chapter
    # stitched drafts, and project-canon name validation. See quality.md § 4.5.
    seam_findings, seam_evidence = await _compute_chapter_seam_signal(
        session=session, project=project, chapter=chapter, draft=draft,
    )
    if seam_findings:
        review_result = _merge_chapter_seam_into_review(
            review_result, seam_findings, seam_evidence,
            language=getattr(project, "language", None),
        )

    reader_logic_findings, reader_logic_evidence = await _compute_reader_logic_signal(
        session=session, project=project, chapter=chapter, draft=draft,
    )
    if reader_logic_findings:
        review_result = _merge_reader_logic_into_review(
            review_result, reader_logic_findings, reader_logic_evidence,
            language=getattr(project, "language", None),
        )

    stitched_findings, stitched_evidence = await _compute_stitched_draft_signal(
        session=session, project=project, chapter=chapter, draft=draft,
    )
    if stitched_findings:
        review_result = _merge_stitched_draft_into_review(
            review_result, stitched_findings, stitched_evidence,
            language=getattr(project, "language", None),
        )

    name_canon_findings, name_canon_evidence = await _compute_name_canon_signal(
        session=session, project=project, chapter=chapter, draft=draft,
    )
    if name_canon_findings:
        review_result = _merge_name_canon_into_review(
            review_result, name_canon_findings, name_canon_evidence,
            language=getattr(project, "language", None),
        )

    quality_bundle_report = await _evaluate_chapter_quality_bundle_for_review(
        session=session,
        settings=settings,
        project=project,
        chapter=chapter,
        draft=draft,
    )
    if quality_bundle_report is not None and quality_bundle_report.blocking_findings:
        review_result = _merge_chapter_quality_bundle_into_review(
            review_result,
            quality_bundle_report,
            language=getattr(project, "language", None),
        )

    llm_commercial_judge_payload: dict[str, object] | None = None
    if getattr(settings.pipeline, "enable_chapter_llm_commercial_judge", False):
        try:
            from bestseller.services.chapter_generation_input_builder import (
                build_chapter_generation_input_bundle,
            )
            from bestseller.services.chapter_llm_quality_judge import (
                judge_chapter_commercial_quality,
            )
            from bestseller.services.prompt_packs import resolve_prompt_pack

            generation_input = (
                build_chapter_generation_input_bundle(
                    project=project,
                    chapter=chapter,
                    scenes=scenes,
                    context_packet=chapter_context,
                    target_word_count=int(chapter.target_word_count or draft.word_count or 0),
                ).model_dump(mode="json")
                if chapter_context is not None
                else {}
            )
            if generation_input:
                generation_input = {
                    **generation_input,
                    "rule_review_snapshot": {
                        "verdict": review_result.verdict,
                        "severity_max": review_result.severity_max,
                        "scores": review_result.scores.model_dump(mode="json"),
                        "findings": [
                            finding.model_dump(mode="json")
                            for finding in review_result.findings[:12]
                        ],
                    },
                }
            project_metadata = (
                project.metadata_json if isinstance(project.metadata_json, dict) else {}
            )
            prompt_pack = resolve_prompt_pack(
                project_metadata.get("prompt_pack_name")
                or project_metadata.get("prompt_pack_key"),
                genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
                sub_genre=getattr(project, "sub_genre", None),
            )
            llm_judge_result = await judge_chapter_commercial_quality(
                session,
                settings,
                chapter_number=chapter.chapter_number,
                content_md=draft.content_md,
                generation_input=generation_input,
                workflow_run_id=workflow_run_id,
                pack=prompt_pack,
            )
            llm_commercial_judge_payload = llm_judge_result.model_dump(mode="json", by_alias=True)
            if (
                not llm_judge_result.passed
                and getattr(
                    settings.pipeline,
                    "chapter_llm_commercial_judge_block_on_failure",
                    False,
                )
            ):
                issues = [
                    f"{issue.code}: {issue.required_fix or issue.evidence}"
                    for issue in llm_judge_result.blocking_issues[:8]
                ]
                rewrite_instructions = (
                    llm_judge_result.rewrite_plan.instructions
                    or "\n".join(issues)
                    or "LLM 商业质量评测未达标，请重写本章。"
                )
                review_result = ChapterReviewResult(
                    verdict="rewrite",
                    scores=review_result.scores,
                    findings=[
                        *review_result.findings,
                        ChapterReviewFinding(
                            category="llm_commercial_quality",
                            severity="critical"
                            if llm_judge_result.has_critical
                            else "major",
                            message=(
                                "LLM 商业质量评测未达标："
                                + (issues[0] if issues else "overall score below threshold")
                            ),
                        ),
                    ],
                    severity_max="critical"
                    if llm_judge_result.has_critical
                    else review_result.severity_max,
                    evidence_summary={
                        **review_result.evidence_summary,
                        "llm_commercial_judge": llm_commercial_judge_payload,
                    },
                    rewrite_instructions=rewrite_instructions,
                )
            else:
                rule_conflict = (
                    {
                        "rule_verdict": review_result.verdict,
                        "llm_passed": True,
                        "rule_scores": review_result.scores.model_dump(mode="json"),
                    }
                    if review_result.verdict != "pass" and llm_judge_result.passed
                    else None
                )
                review_result = ChapterReviewResult(
                    verdict=review_result.verdict,
                    scores=review_result.scores,
                    findings=review_result.findings,
                    severity_max=review_result.severity_max,
                    evidence_summary={
                        **review_result.evidence_summary,
                        "llm_commercial_judge": llm_commercial_judge_payload,
                        "llm_rule_gate_conflict": rule_conflict,
                    },
                    rewrite_instructions=review_result.rewrite_instructions,
                )
                if _can_accept_llm_pass_over_rule_rewrite(
                    review_result,
                    llm_commercial_judge_payload,
                ):
                    review_result = _downgrade_rule_rewrite_after_llm_pass(
                        review_result,
                        llm_commercial_judge_payload,
                    )
        except Exception as exc:
            # NOTE: Previously this used `logger.debug` which silently swallowed
            # all judge failures.  That meant the chapter LLM commercial judge
            # could crash on every run (config drift, missing model, schema
            # change) without anyone noticing — the entire semantic quality
            # layer would be effectively disabled with no signal.  Escalated to
            # `logger.exception` so production logs surface the failure.
            logger.exception(
                "chapter LLM commercial judge failed for ch%d (judge ran but raised)",
                chapter_number,
            )
            if getattr(
                settings.pipeline,
                "chapter_llm_commercial_judge_block_on_failure",
                False,
            ):
                review_result = _merge_llm_judge_exception_into_chapter_review(
                    review_result,
                    category="llm_commercial_quality",
                    message_prefix="LLM 商业质量评测",
                    error=exc,
                )

    if getattr(settings.pipeline, "enable_chapter_window_llm_judge", False):
        try:
            from bestseller.services.chapter_window_quality_judge import (
                judge_chapter_window_quality,
            )

            window_size = int(
                getattr(settings.pipeline, "chapter_window_llm_judge_size", 5)
                or 5
            )
            min_chapters = int(
                getattr(settings.pipeline, "chapter_window_llm_judge_min_chapters", 2)
                or 2
            )
            window_payload = await _recent_chapter_window_payload(
                session,
                project=project,
                chapter=chapter,
                draft=draft,
                window_size=window_size,
            )
            if len(window_payload) >= min_chapters:
                window_judge_result = await judge_chapter_window_quality(
                    session,
                    settings,
                    chapters=window_payload,
                    workflow_run_id=workflow_run_id,
                )
                window_judge_payload = window_judge_result.model_dump(
                    mode="json",
                    by_alias=True,
                )
                review_result = ChapterReviewResult(
                    verdict=review_result.verdict,
                    scores=review_result.scores,
                    findings=review_result.findings,
                    severity_max=review_result.severity_max,
                    evidence_summary={
                        **review_result.evidence_summary,
                        "llm_window_quality_judge": window_judge_payload,
                    },
                    rewrite_instructions=review_result.rewrite_instructions,
                )
                if (
                    not window_judge_result.passed
                    and getattr(
                        settings.pipeline,
                        "chapter_window_llm_judge_block_on_failure",
                        False,
                    )
                ):
                    review_result = _merge_llm_quality_judge_into_chapter_review(
                        review_result,
                        category="llm_window_quality",
                        message_prefix="最近章节滑窗质量评测",
                        judge_result=window_judge_result,
                    )
        except Exception as exc:
            logger.exception(
                "chapter window LLM judge failed for ch%d (judge ran but raised)",
                chapter_number,
            )
            if getattr(
                settings.pipeline,
                "chapter_window_llm_judge_block_on_failure",
                False,
            ):
                review_result = _merge_llm_judge_exception_into_chapter_review(
                    review_result,
                    category="llm_window_quality",
                    message_prefix="最近章节滑窗质量评测",
                    error=exc,
                )

    if getattr(settings.pipeline, "enable_volume_llm_checkpoint_judge", False):
        try:
            from bestseller.services.volume_quality_judge import (
                judge_volume_quality_checkpoint,
            )

            interval = int(
                getattr(settings.pipeline, "volume_llm_checkpoint_interval", 10)
                or 10
            )
            min_chapters = int(
                getattr(
                    settings.pipeline,
                    "volume_llm_checkpoint_min_chapters",
                    interval,
                )
                or interval
            )
            should_run_volume_judge = _should_run_volume_checkpoint_judge(
                chapter_number=chapter_number,
                interval=interval,
                min_chapters=min_chapters,
            )
            if should_run_volume_judge:
                volume_plan, chapter_summaries = await _current_volume_payload(
                    session,
                    project=project,
                    chapter=chapter,
                    draft=draft,
                )
                volume_judge_result = await judge_volume_quality_checkpoint(
                    session,
                    settings,
                    volume_plan=volume_plan,
                    chapter_summaries=chapter_summaries,
                    current_chapter_number=chapter_number,
                    volume_checkpoint_interval=interval,
                    volume_checkpoint_min_chapters=min_chapters,
                    workflow_run_id=workflow_run_id,
                )
                volume_judge_payload = volume_judge_result.model_dump(
                    mode="json",
                    by_alias=True,
                )
                review_result = ChapterReviewResult(
                    verdict=review_result.verdict,
                    scores=review_result.scores,
                    findings=review_result.findings,
                    severity_max=review_result.severity_max,
                    evidence_summary={
                        **review_result.evidence_summary,
                        "llm_volume_checkpoint_judge": volume_judge_payload,
                    },
                    rewrite_instructions=review_result.rewrite_instructions,
                )
                if (
                    not volume_judge_result.passed
                    and getattr(
                        settings.pipeline,
                        "volume_llm_checkpoint_block_on_failure",
                        False,
                    )
                ):
                    review_result = _merge_llm_quality_judge_into_chapter_review(
                        review_result,
                        category="llm_volume_checkpoint",
                        message_prefix="卷目标对齐评测",
                        judge_result=volume_judge_result,
                    )
        except Exception as exc:
            logger.exception(
                "volume LLM checkpoint judge failed for ch%d (judge ran but raised)",
                chapter_number,
            )
            if getattr(
                settings.pipeline,
                "volume_llm_checkpoint_block_on_failure",
                False,
            ):
                review_result = _merge_llm_judge_exception_into_chapter_review(
                    review_result,
                    category="llm_volume_checkpoint",
                    message_prefix="卷目标对齐评测",
                    error=exc,
                )

    critic_response = render_chapter_review_summary(
        review_result,
        language=getattr(project, "language", None),
    )
    reviewer_type = "rule-based-critic"
    llm_run_id: UUID | None = None
    if _should_generate_chapter_review_commentary(settings):
        system_prompt, user_prompt = build_chapter_review_prompts(
            project,
            chapter,
            draft,
            chapter_context,
            review_result,
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=critic_response,
                prompt_template="chapter_review",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "verdict": review_result.verdict,
                },
            ),
        )
        critic_response = completion.content.strip() or critic_response
        reviewer_type = completion.model_name
        llm_run_id = completion.llm_run_id

        # --- LLM verdict override for chapter review ---
        llm_verdict = _parse_llm_verdict(critic_response)
        if (
            llm_verdict == "rewrite"
            and review_result.verdict == "pass"
            and review_result.severity_max in {"major", "critical", "high"}
        ):
            review_result = ChapterReviewResult(
                verdict="rewrite",
                scores=review_result.scores,
                findings=review_result.findings,
                severity_max=review_result.severity_max,
                evidence_summary=review_result.evidence_summary,
                rewrite_instructions=_parse_llm_rewrite_direction(critic_response)
                or review_result.rewrite_instructions
                or "LLM 评审判定章节需要重写。",
            )

    report = ReviewReportModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type=reviewer_type,
        verdict=review_result.verdict,
        severity_max=review_result.severity_max,
        llm_run_id=llm_run_id,
        structured_output={
            "draft_id": str(draft.id),
            "scores": review_result.scores.model_dump(mode="json"),
            "findings": [finding.model_dump(mode="json") for finding in review_result.findings],
            "evidence_summary": review_result.evidence_summary,
            "rewrite_instructions": review_result.rewrite_instructions,
            "critic_response": critic_response,
        },
    )
    session.add(report)
    await session.flush()

    await session.execute(
        update(QualityScoreModel)
        .where(
            QualityScoreModel.target_type == "chapter",
            QualityScoreModel.target_id == chapter.id,
            QualityScoreModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    quality = QualityScoreModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        review_report_id=report.id,
        is_current=True,
        score_overall=review_result.scores.overall,
        score_goal=review_result.scores.goal,
        score_conflict=review_result.scores.coverage,
        score_emotion=review_result.scores.coherence,
        score_dialogue=review_result.scores.continuity,
        score_style=review_result.scores.style,
        score_hook=review_result.scores.hook,
        evidence_summary=review_result.evidence_summary,
    )
    session.add(quality)

    rewrite_task: RewriteTaskModel | None = None
    if review_result.verdict == "rewrite":
        # Per-chapter budget: bound the number of chapter_review-triggered
        # rewrite cycles. The original design assumed each rewrite would
        # converge, but in practice LLM-judge verdicts can disagree with
        # what the writer can produce against the current plan, creating an
        # unbounded loop (青囊不语问阴阳 ch1 reached 124 versions on
        # 2026-05-25 before this cap landed). Once the budget is spent we
        # stop creating new chapter_review tasks and surface the chapter
        # for human attention via production_state.
        budget = max(
            int(
                getattr(
                    settings.pipeline,
                    "chapter_auto_repair_max_attempts",
                    3,
                )
                or 3
            ),
            1,
        )
        # Counter is persisted on the chapter itself ("how many review→
        # rewrite cycles have we started since the chapter last passed").
        # We intentionally do NOT count historical rewrite_tasks rows: a
        # chapter that ran 80 cycles in its lifetime but hasn't passed yet
        # still deserves a fresh budget when we reactivate the cap, so the
        # operator can re-deploy without manually wiping every backlog.
        # When the chapter passes review the counter is removed so a future
        # regression starts fresh.
        chapter_meta_for_budget = dict(chapter.metadata_json or {})
        attempts_used = int(
            chapter_meta_for_budget.get("chapter_review_attempts_active") or 0
        )
        if attempts_used >= budget:
            logger.warning(
                "chapter_review budget exhausted for project=%s chapter=%d "
                "(attempts_used=%d budget=%d); not creating another rewrite "
                "task — flagging for human review.",
                project.slug,
                chapter.chapter_number,
                attempts_used,
                budget,
            )
            await session.execute(
                update(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.trigger_source_id == chapter.id,
                    RewriteTaskModel.trigger_type == "chapter_review",
                    RewriteTaskModel.status.in_(("pending", "queued")),
                )
                .values(
                    status="cancelled",
                    metadata_json={
                        "cancelled_reason": "chapter_review_budget_exhausted",
                        "attempts_used": attempts_used,
                        "budget": budget,
                    },
                )
            )
            chapter.status = ChapterStatus.REVIEW.value
            # ``chapters.production_state`` is VARCHAR(20); keep the
            # machine-repair terminal state compact and put details in
            # rewrite/task metadata.
            chapter.production_state = "repair_exhausted"
        else:
            rewrite_instructions = _sanitize_chapter_review_rewrite_instructions(
                review_result.rewrite_instructions or "请补强当前章节。",
                chapter=chapter,
                scenes=scenes,
            )
            rewrite_task = RewriteTaskModel(
                project_id=project.id,
                trigger_type="chapter_review",
                trigger_source_id=chapter.id,
                rewrite_strategy="chapter_coherence_bridge_rewrite",
                priority=4,
                status="pending",
                instructions=rewrite_instructions or "请补强当前章节。",
                context_required=[
                    "chapter_context",
                    "current_chapter_draft",
                    "scene_summaries",
                    "review_findings",
                ],
                metadata_json={
                    "chapter_id": str(chapter.id),
                    "draft_id": str(draft.id),
                    "review_report_id": str(report.id),
                    "attempt_index": attempts_used + 1,
                    "attempt_budget": budget,
                },
            )
            session.add(rewrite_task)
            chapter.status = ChapterStatus.REVISION.value
            # Bump the active-cycle counter so the next pass sees us closer
            # to the cap.
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "chapter_review_attempts_active": attempts_used + 1,
            }
    else:
        await session.execute(
            update(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_source_id == chapter.id,
                RewriteTaskModel.status.in_(("pending", "queued")),
            )
            .values(
                status="superseded",
                metadata_json={
                    "superseded_reason": "current_chapter_review_passed",
                    "superseded_by_review_report_id": str(report.id),
                    "superseded_by_draft_id": str(draft.id),
                },
            )
        )
        chapter.status = ChapterStatus.COMPLETE.value
        chapter.production_state = "ok"
        # Wipe the active-cycle counter so a future regression on this
        # chapter starts the chapter_review budget fresh.
        chapter_meta_after_pass = dict(chapter.metadata_json or {})
        chapter_meta_after_pass.pop("chapter_review_attempts_active", None)
        chapter.metadata_json = chapter_meta_after_pass

    await session.flush()
    return review_result, report, quality, rewrite_task


def render_rewritten_scene_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    current_draft: SceneDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    style_guide: StyleGuideModel | None,
) -> str:
    """Return a safe fallback for a scene rewrite when the LLM call fails.

    Historically this function generated six paragraphs of Chinese prose
    ("XX 重新被推回《项目》第 N 章的核心冲突。叙事仍采用 third-limited
    视角…", "这一版重写围绕 XX 展开…", "金属舱壁传来的冷意…"). That prose
    was stored verbatim when the rewriter LLM timed out, and is the exact
    meta-text that showed up in multiple chapters of the existing
    ``apocalypse-supply-1775626373`` output.

    The correct behaviour for a rewrite *fallback* is: do not invent new
    prose, and do not overwrite the previously-approved draft with templated
    narration. Instead, re-use the current draft's ``content_md`` verbatim
    and prefix it with an invisible HTML comment marker so reviewers can see
    the rewrite never actually ran. The marker is stripped later by
    ``sanitize_novel_markdown_content``.
    """
    _ = (rewrite_task, style_guide)  # kept for signature parity
    marker = (
        f"<!-- rewrite-scene-fallback project=\"{project.slug}\" "
        f"chapter={chapter.chapter_number} scene={scene.scene_number} "
        f"reason=\"rewriter-llm-unavailable\" -->"
    )
    existing = (current_draft.content_md or "").strip()
    if not existing:
        return marker
    return f"{marker}\n\n{existing}"


def render_rewritten_chapter_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    chapter_context,
) -> str:
    """Return a safe fallback for a chapter rewrite when the LLM call fails.

    Previously this function wrapped the original chapter body with two
    templated narration paragraphs ("上一阶段留下的局势仍压在众人心头…"
    / "章节收束时，XX 不再只是背景…"). Those wrappers ended up in the final
    output when the rewriter LLM was unreachable, polluting multiple chapters
    with the same boilerplate opener and closer.

    The fix mirrors :func:`render_rewritten_scene_markdown`: re-use the
    current draft verbatim (re-normalising the heading so the double
    ``第N章 第N章`` prefix bug cannot resurface) and attach a non-prose
    HTML comment so reviewers can spot a rewrite that never succeeded.
    """
    _ = (rewrite_task, chapter_context)  # kept for signature parity
    from bestseller.services.drafts import format_chapter_heading as _format_chapter_heading

    marker = (
        f"<!-- rewrite-chapter-fallback project=\"{project.slug}\" "
        f"chapter={chapter.chapter_number} "
        f"reason=\"rewriter-llm-unavailable\" -->"
    )
    project_language = normalize_language(getattr(project, "language", None))
    original_content = (current_draft.content_md or "").strip()
    if not original_content:
        return f"{marker}\n\n{_format_chapter_heading(chapter.chapter_number, chapter.title, language=project_language)}"

    if original_content.startswith("# 第") or original_content.startswith(f"# Chapter {chapter.chapter_number}"):
        lines = original_content.split("\n", 1)
        body = lines[1].lstrip("\n") if len(lines) == 2 else ""
    else:
        body = original_content
    heading = _format_chapter_heading(chapter.chapter_number, chapter.title, language=project_language)
    parts = [marker, heading]
    if body.strip():
        parts.append(body.strip())
    return "\n\n".join(parts).strip()


def _rewrite_output_max_tokens_override(
    chapter: ChapterModel,
    project: ProjectModel,
    rewrite_task: RewriteTaskModel,
    *,
    force_compression: bool = False,
    force_expansion: bool = False,
) -> int | None:
    settings = get_settings()
    project_language = _project_language(project)
    editor_model = resolve_llm_role_model(settings, role="editor")
    editor_model_lc = (editor_model or "").strip().lower()
    if "minimax-m2" in editor_model_lc and "highspeed" in editor_model_lc:
        # Chapter rewrites return a complete chapter, not a bounded patch. Do
        # not use a target-derived cap here: MiniMax can spend a tight cap on
        # hidden/reasoning tokens and return finish_reason='length' with empty
        # or truncated visible prose. But live chapter-first runs also showed
        # that using the full 32768 ceiling can produce an empty length response,
        # so keep a fixed safe runway that is still ample for a 3500-character
        # Chinese chapter. Length is controlled by prompt contracts and gates.
        safe_cap = 16_384
        model_ceiling = model_output_token_ceiling(editor_model)
        if model_ceiling and model_ceiling > 0:
            safe_cap = min(safe_cap, int(model_ceiling))
        configured = resolve_llm_role_max_tokens(settings, role="editor")
        if configured and configured > 0:
            return min(max(int(configured), safe_cap), safe_cap)
        return safe_cap
    try:
        target = int(chapter.target_word_count or 0)
    except (TypeError, ValueError):
        target = 0
    cap_target = target
    if cap_target > 0 and not is_english_language(project_language):
        cap_target = max(cap_target, CHINESE_CHAPTER_HARD_MIN_WORDS)
    base = prose_output_max_tokens_for_target(
        cap_target or chapter.target_word_count,
        language=project_language,
        settings=settings,
        role="editor",
    )
    if force_expansion:
        if target > 0 and not is_english_language(project_language):
            expansion_cap = prose_output_max_tokens_for_target(
                max(cap_target, CHINESE_CHAPTER_HARD_MIN_WORDS + 220),
                language=project_language,
                settings=settings,
                role="editor",
            )
            return expansion_cap if expansion_cap is not None else base
        return base
    metadata = rewrite_task.metadata_json if isinstance(rewrite_task.metadata_json, dict) else {}
    audit_row = metadata.get("audit_row") if isinstance(metadata.get("audit_row"), dict) else {}
    word_reason = str(audit_row.get("word_count_reason") or "").lower()
    instructions = str(rewrite_task.instructions or "").lower()
    quality_retrofit_requested = (
        rewrite_task.trigger_type == "autonomous_quality_retrofit"
        or metadata.get("source") == "quality_levers_retrofit_audit"
    )
    compression_requested = (
        force_compression
        or "overflow" in word_reason
        or "当前章节偏长" in instructions
        or "压缩型修复" in instructions
    )
    if target <= 0:
        return base
    model_reserve = model_reasoning_token_reserve(
        resolve_llm_role_model(settings, role="editor")
    )
    if quality_retrofit_requested and not force_expansion and not compression_requested:
        editor_model = resolve_llm_role_model(settings, role="editor")
        model_ceiling = model_output_token_ceiling(editor_model)
        if model_reserve and model_ceiling:
            retrofit_cap = max(8192, int(round(target * 4.0)) + 4096) + model_reserve
            return min(retrofit_cap, int(model_ceiling))
        else:
            retrofit_cap = max(4096, int(round(target * 2.0)) + 768) + model_reserve
        return min(base, retrofit_cap) if base is not None else retrofit_cap
    if not compression_requested:
        return base
    if not is_english_language(project_language):
        compression_floor_cap = prose_output_max_tokens_for_target(
            max(target, CHINESE_CHAPTER_HARD_MIN_WORDS),
            language=project_language,
            settings=settings,
            role="editor",
        )
        if compression_floor_cap is not None:
            return compression_floor_cap
    compression_cap = max(1800, int(round(target * 1.20)) + 128) + model_reserve
    return min(base, compression_cap) if base is not None else compression_cap


_MICRO_TRIM_LOW_VALUE_MARKERS = (
    "只是",
    "已经",
    "仍然",
    "似乎",
    "仿佛",
    "像是",
    "微微",
    "慢慢",
    "片刻",
    "沉默",
    "呼吸",
    "空气",
    "光线",
    "影子",
    "夜色",
    "风声",
    "回声",
    "指节",
    "喉咙",
)
_MICRO_TRIM_PROTECTED_MARKERS = (
    "线索",
    "证据",
    "规则",
    "真相",
    "凶手",
    "尸",
    "血",
    "钥匙",
    "账",
    "镜",
    "铜钱",
    "符",
    "青囊",
    "名字",
    "电话",
    "短信",
    "纸条",
    "照片",
    "录音",
    "监控",
    "门外",
    "脚步",
)


def _length_over_max_from_violations(
    violations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> int | None:
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        code = str(violation.get("code") or "").strip()
        if code != "LENGTH_OVER" and not code.endswith("_BLOCK_HIGH"):
            continue
        for key in ("max", "maximum", "threshold", "limit"):
            value = violation.get(key)
            if value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
        detail = " ".join(
            str(violation.get(key) or "")
            for key in ("detail", "message", "expected")
        )
        match = re.search(r"(?:max|maximum|limit|上限|不超过)\D{0,12}(\d{3,5})", detail, re.I)
        if match:
            return int(match.group(1))
        match = re.search(r">\s*(?:max\s*)?(\d{3,5})", detail, re.I)
        if match:
            return int(match.group(1))
    return None


def _only_length_over_blocking_codes(codes: Iterable[str]) -> bool:
    normalized = {str(code or "").strip() for code in codes if str(code or "").strip()}
    return bool(normalized) and all(
        code == "LENGTH_OVER" or code.endswith("_BLOCK_HIGH")
        for code in normalized
    )


def _micro_trim_overlength_chapter_text(
    content: str,
    *,
    max_words: int,
    max_overage: int = 250,
    safety_margin: int = 35,
) -> tuple[str, dict[str, Any]]:
    current_words = count_words(content)
    if max_words <= 0 or current_words <= max_words:
        return content, {
            "applied": False,
            "reason": "not_overlength",
            "before_word_count": current_words,
            "max_word_count": max_words,
        }
    overage = current_words - max_words
    if overage > max_overage:
        return content, {
            "applied": False,
            "reason": "overage_too_large",
            "before_word_count": current_words,
            "max_word_count": max_words,
            "overage": overage,
        }

    target_words = max(max_words - safety_margin, max_words - overage)
    blocks = re.split(r"(\n{2,})", content)
    text_block_indices = [index for index in range(0, len(blocks), 2)]
    if len(text_block_indices) <= 4:
        return content, {
            "applied": False,
            "reason": "too_few_blocks",
            "before_word_count": current_words,
            "max_word_count": max_words,
            "overage": overage,
        }

    candidates: list[tuple[int, int, int, int, str]] = []
    protected = _MICRO_TRIM_PROTECTED_MARKERS
    low_value = _MICRO_TRIM_LOW_VALUE_MARKERS
    first_body_index = 2
    last_body_index = max(0, len(text_block_indices) - 3)
    for ordinal, block_index in enumerate(text_block_indices):
        block = blocks[block_index]
        stripped = block.strip()
        if not stripped:
            continue
        if ordinal < first_body_index or ordinal > last_body_index:
            continue
        if stripped.startswith(("#", "##", ">", "- ")):
            continue
        if "“" in stripped or "”" in stripped or '"' in stripped:
            continue

        for match in re.finditer(r"[^。！？；!?;]+[。！？；!?;]?", block):
            sentence = match.group(0)
            sentence_words = count_words(sentence)
            if sentence_words < 8 or sentence_words > 90:
                continue
            if any(marker in sentence for marker in protected):
                continue
            score = sentence_words
            if any(marker in sentence for marker in low_value):
                score += 80
            if ordinal >= first_body_index + 2:
                score += 12
            candidates.append((score, block_index, match.start(), match.end(), sentence))

    if not candidates:
        return content, {
            "applied": False,
            "reason": "no_safe_candidates",
            "before_word_count": current_words,
            "max_word_count": max_words,
            "overage": overage,
        }

    selected: list[tuple[int, int, int, str]] = []
    selected_words = 0
    occupied: dict[int, list[tuple[int, int]]] = {}
    for _score, block_index, start, end, sentence in sorted(candidates, reverse=True):
        ranges = occupied.setdefault(block_index, [])
        if any(not (end <= used_start or start >= used_end) for used_start, used_end in ranges):
            continue
        selected.append((block_index, start, end, sentence))
        ranges.append((start, end))
        selected_words += count_words(sentence)
        if current_words - selected_words <= target_words:
            break

    if current_words - selected_words > max_words:
        return content, {
            "applied": False,
            "reason": "insufficient_safe_trim",
            "before_word_count": current_words,
            "max_word_count": max_words,
            "overage": overage,
            "candidate_removed_words": selected_words,
        }

    for block_index, start, end, _sentence in sorted(selected, reverse=True):
        block = blocks[block_index]
        replacement = (block[:start] + block[end:]).strip()
        replacement = re.sub(r"\n{3,}", "\n\n", replacement)
        replacement = re.sub(r"[ \t]{2,}", " ", replacement)
        blocks[block_index] = replacement

    trimmed = "\n\n".join(
        part.strip()
        for part in "".join(blocks).split("\n\n")
        if part.strip()
    )
    after_words = count_words(trimmed)
    if after_words > max_words:
        return content, {
            "applied": False,
            "reason": "post_trim_still_overlength",
            "before_word_count": current_words,
            "after_word_count": after_words,
            "max_word_count": max_words,
            "overage": overage,
            "removed_sentence_count": len(selected),
        }
    return trimmed, {
        "applied": True,
        "reason": "micro_length_trim",
        "before_word_count": current_words,
        "after_word_count": after_words,
        "max_word_count": max_words,
        "overage": overage,
        "removed_sentence_count": len(selected),
        "removed_word_count_estimate": current_words - after_words,
    }


def _quality_retrofit_task_causes(rewrite_task: RewriteTaskModel) -> set[str]:
    metadata = rewrite_task.metadata_json if isinstance(rewrite_task.metadata_json, dict) else {}
    if (
        rewrite_task.trigger_type != "autonomous_quality_retrofit"
        and metadata.get("source") != "quality_levers_retrofit_audit"
    ):
        return set()
    raw = metadata.get("cause_ids")
    if isinstance(raw, str):
        return {item.strip() for item in raw.split(";") if item.strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _quality_retrofit_candidate_findings(
    content: str,
    rewrite_task: RewriteTaskModel,
    *,
    platform: str = "framework",
) -> list[dict[str, Any]]:
    requested_causes = _quality_retrofit_task_causes(rewrite_task)
    if not requested_causes:
        return []
    bundle = audit_chapter(content, platform=platform)
    rhythm = audit_rhythm(content)
    emotion = audit_emotion_labels(content)
    findings: list[dict[str, Any]] = []

    def wants(cause_id: str) -> bool:
        return cause_id in requested_causes

    if wants("weak_attraction") and not bundle.pulse.passed:
        findings.append(
            {
                "cause_id": "weak_attraction",
                "code": "QUALITY_RETROFIT_WEAK_ATTRACTION",
                "detail": (
                    f"pulse_density={bundle.pulse.density_per_300_chars:.2f} "
                    f"< {bundle.pulse.threshold:.2f}; pulse_count={bundle.pulse.pulse_count}"
                ),
                "repair_action": (
                    "Add real action pressure, interruption, threat, clue movement, "
                    "deadline, or costly choice every 250-350 Chinese characters. "
                    "Use at least 10 dispersed detector-visible pressure triggers "
                    "such as 立刻、必须、来不及、猛地、逼近、拦住、堵住、反锁、"
                    "停住、压住、抓住, and bind each trigger to a changed action, "
                    "clue state, danger distance, deadline, or cost."
                ),
            }
        )
    if wants("ai_voice") and not bundle.banned_patterns.passed:
        breakdown = ";".join(
            f"{hit.pattern_id}:{hit.count}" for hit in bundle.banned_patterns.hits
        )
        findings.append(
            {
                "cause_id": "ai_voice",
                "code": "QUALITY_RETROFIT_AI_VOICE",
                "detail": breakdown,
                "repair_action": (
                    "Remove the exact banned AI-pattern shapes; replace them with "
                    "concrete action, object changes, dialogue pressure, or consequence."
                ),
            }
        )
    if wants("weak_prose") and (
        not bundle.abstract_sensory.passed or not emotion.passed
    ):
        abstract = ";".join(
            f"{word}:{count}" for word, count in bundle.abstract_sensory.hits
        )
        findings.append(
            {
                "cause_id": "weak_prose",
                "code": "QUALITY_RETROFIT_WEAK_PROSE",
                "detail": (
                    f"abstract={abstract or 'none'}; "
                    f"emotion_label_hits={emotion.total_hits}"
                ),
                "repair_action": (
                    "Replace abstract sensory labels and emotion labels with concrete "
                    "objects, gestures, temperature, sound, touch, and visible decisions."
                ),
            }
        )
    if wants("weak_immersion") and not bundle.dumping.passed:
        findings.append(
            {
                "cause_id": "weak_immersion",
                "code": "QUALITY_RETROFIT_WEAK_IMMERSION",
                "detail": f"dumping_hits={bundle.dumping.total_hits}",
                "repair_action": (
                    "Turn background explanation into triggered action, evidence change, "
                    "character confrontation, or immediate cost."
                ),
            }
        )
    if wants("flat_narration") and (
        not rhythm.passed
        or (
            not bundle.word_count.passed
            and str(bundle.word_count.reason).startswith("underflow")
        )
    ):
        missing_rhythm_types = [
            label
            for count, label in (
                (rhythm.hard_stop_count, "短硬停顿"),
                (rhythm.acceleration_count, "三连短段加速"),
                (rhythm.delay_count, "延宕停拍"),
                (rhythm.external_interrupt_count, "外部打断"),
            )
            if count <= 0
        ]
        missing_detail = (
            "; missing_types=" + ",".join(missing_rhythm_types)
            if missing_rhythm_types
            else ""
        )
        findings.append(
            {
                "cause_id": "flat_narration",
                "code": "QUALITY_RETROFIT_FLAT_NARRATION",
                "detail": (
                    f"rhythm_total={rhythm.total_anchors}/{rhythm.expected_min_count}; "
                    f"rhythm_types={rhythm.types_covered}/{rhythm.expected_min_types}; "
                    f"word_count={bundle.word_count.reason}"
                    f"{missing_detail}"
                ),
                "repair_action": (
                    "Add visible chapter function and at least three rhythm-anchor types: "
                    "hard stop, acceleration, delay, and external interruption. "
                    "Use detector-visible forms: a standalone hard-stop paragraph under "
                    "12 CJK chars, a three-paragraph acceleration run under 8 CJK chars "
                    "each, a delay beat such as 停了一拍, and an external interruption "
                    "using 门外、忽然、猛地、传来、推开 or 突然."
                ),
            }
        )
    return findings


def _quality_retrofit_near_miss_acceptance(
    findings: list[dict[str, Any]],
    *,
    recent_failed_rewrites: list[RewriteTaskModel],
) -> dict[str, Any] | None:
    if not findings:
        return None
    if len(recent_failed_rewrites) < 3:
        return None
    if len(findings) != 1:
        return None
    finding = findings[0]
    if str(finding.get("code") or "") != "QUALITY_RETROFIT_WEAK_ATTRACTION":
        return None
    detail = str(finding.get("detail") or "")
    density_match = re.search(r"pulse_density=([0-9.]+)", detail)
    count_match = re.search(r"pulse_count=(\d+)", detail)
    if not density_match or not count_match:
        return None
    try:
        density = float(density_match.group(1))
        pulse_count = int(count_match.group(1))
    except ValueError:
        return None
    if density < 0.80 or pulse_count < 8:
        return None
    length_failures = 0
    for task in recent_failed_rewrites[:5]:
        metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
        codes = _length_gate_codes_from_metadata(metadata)
        if codes:
            length_failures += 1
    if length_failures < 2:
        return None
    return {
        "mode": "quality_retrofit_near_miss_acceptance",
        "accepted_code": "QUALITY_RETROFIT_WEAK_ATTRACTION",
        "pulse_density": density,
        "pulse_count": pulse_count,
        "recent_length_failure_count": length_failures,
        "residual_repair_action": finding.get("repair_action"),
    }


async def rewrite_chapter_from_task(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    *,
    rewrite_task_id: UUID | None = None,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[ChapterDraftVersionModel, RewriteTaskModel]:
    project, chapter, _style_guide, _scenes, current_draft = await _load_chapter_context(
        session,
        project_slug,
        chapter_number,
    )

    rewrite_query = select(RewriteTaskModel).where(
        RewriteTaskModel.project_id == project.id,
        RewriteTaskModel.trigger_source_id == chapter.id,
    )
    if rewrite_task_id is not None:
        rewrite_query = rewrite_query.where(RewriteTaskModel.id == rewrite_task_id)
    else:
        rewrite_query = rewrite_query.where(RewriteTaskModel.status.in_(["pending", "queued"]))
    rewrite_query = rewrite_query.order_by(RewriteTaskModel.created_at.desc())

    rewrite_task = await session.scalar(rewrite_query.limit(1))
    if rewrite_task is None:
        raise ValueError(f"Chapter {chapter_number} does not have a pending rewrite task.")
    recent_failed_rewrites = list(
        await session.scalars(
            select(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_source_id == chapter.id,
                RewriteTaskModel.status == "failed",
                RewriteTaskModel.id != rewrite_task.id,
            )
            .order_by(RewriteTaskModel.updated_at.desc())
            .limit(5)
        )
    )
    effective_settings = settings or get_settings()
    working_draft = await _select_rewrite_working_draft(
        session,
        current_draft=current_draft,
        recent_failed_rewrites=recent_failed_rewrites,
        settings=effective_settings,
        chapter=chapter,
        language=_project_language(project),
    )
    rewrite_task.instructions = _append_recent_length_failure_directive(
        rewrite_task.instructions or "",
        recent_failed_rewrites,
        chapter=chapter,
        language=_project_language(project),
    )

    chapter_context = None
    if settings is not None:
        chapter_context = await build_chapter_writer_context(
            session,
            settings,
            project_slug,
            chapter_number,
        )
    try:
        from bestseller.services.drafts import _prepare_quality_uplift_prompt_blocks

        await _prepare_quality_uplift_prompt_blocks(
            session,
            project=project,
            chapter=chapter,
            scenes=_scenes,
        )
    except Exception:
        logger.debug("chapter rewrite quality uplift prompt blocks failed", exc_info=True)
    fallback_content = render_rewritten_chapter_markdown(
        project,
        chapter,
        working_draft,
        rewrite_task,
        chapter_context,
    )

    model_name = "mock-editor"
    llm_run_id: UUID | None = None
    generation_mode = "chapter-rewrite-fallback"
    content_md = fallback_content
    if settings is not None and chapter_context is not None:
        system_prompt, user_prompt = build_chapter_rewrite_prompts(
            project,
            chapter,
            working_draft,
            rewrite_task,
            chapter_context,
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_content,
                prompt_template="chapter_rewrite",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                max_tokens_override=_rewrite_output_max_tokens_override(
                    chapter,
                    project,
                    rewrite_task,
                ),
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "rewrite_task_id": str(rewrite_task.id),
                },
            ),
        )
        candidate_md = sanitize_novel_markdown_content(completion.content)
        content_md = candidate_md or fallback_content
        # Guard against degenerate/truncated chapter rewrites (editor model hitting
        # ``finish_reason='length'`` and returning a short partial). If the
        # candidate falls below the chapter floor *and* is materially shorter than
        # the working draft, keep the known-good draft so a truncated stub never
        # replaces a healthy chapter and wastes a repair cycle.
        if candidate_md:
            try:
                _floor_band = chapter_rewrite_length_band(
                    settings,
                    getattr(chapter, "target_word_count", None),
                    language=_project_language(project),
                    direction="normal",
                    role="editor",
                )
                prev_words = count_words(fallback_content)
                candidate_words = count_words(candidate_md)
                if (
                    prev_words > 0
                    and candidate_words < int(_floor_band.hard_min)
                    and candidate_words < int(prev_words * 0.85)
                ):
                    logger.warning(
                        "Chapter %s %d rewrite produced degenerate short output "
                        "(%d words < floor %d, working draft %d) — keeping prior draft.",
                        project.slug,
                        chapter.chapter_number,
                        candidate_words,
                        int(_floor_band.hard_min),
                        prev_words,
                    )
                    content_md = fallback_content
            except Exception:
                logger.debug("chapter rewrite degenerate-output guard failed", exc_info=True)
        content_md = strip_scaffolding_echoes(content_md)
        if has_meta_leak(content_md):
            content_md = await validate_and_clean_novel_content(
                session,
                settings,
                content_md,
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
            )
        model_name = completion.model_name
        llm_run_id = completion.llm_run_id
        generation_mode = completion.provider
    else:
        content_md = strip_scaffolding_echoes(sanitize_novel_markdown_content(content_md))

    _cleanup_settings = settings or get_settings()
    content_md, _cleanup_stats = _clean_generated_chapter_text(
        content_md,
        chapter_number=chapter.chapter_number,
        source="chapter_rewrite",
        min_word_count=chapter_rewrite_length_band(
            _cleanup_settings,
            getattr(chapter, "target_word_count", None),
            language=_project_language(project),
            direction="normal",
            role="editor",
        ).hard_min,
    )
    deterministic_postprocess_metadata: dict[str, Any] | None = None
    try:
        from bestseller.services.rewrite_escalation import (
            EscalationDecision,
            EscalationLevel,
            apply_post_process,
        )

        escalation_payload = dict((chapter.metadata_json or {}).get("rewrite_escalation") or {})
        action = escalation_payload.get("post_process_action")
        if action:
            decision = EscalationDecision(
                level=EscalationLevel(str(escalation_payload.get("level") or "normal")),
                block_kind=str(escalation_payload.get("block_kind") or "general"),
                attempt_count=int(escalation_payload.get("attempt_count") or 0),
                strict_directive=str(escalation_payload.get("strict_directive") or ""),
                post_process_action=str(action),
            )
            forbidden_terms = tuple(
                str(item.get("matched_text") or "")
                for item in (
                    (chapter.metadata_json or {})
                    .get("deterministic_audit_latest", {})
                    .get("findings", [])
                )
                if isinstance(item, dict)
            )
            content_md, deterministic_postprocess_metadata = apply_post_process(
                content_md,
                decision,
                forbidden_terms=forbidden_terms,
            )
    except Exception:
        logger.debug("chapter rewrite deterministic post-process failed", exc_info=True)

    deterministic_audit_report = None
    try:
        from bestseller.services.deterministic_post_write_audit import audit_chapter_prose

        band = chapter_rewrite_length_band(
            get_settings(),
            getattr(chapter, "target_word_count", None),
            language=project.language,
            direction="normal",
            role="editor",
        )
        deterministic_audit_report = audit_chapter_prose(
            chapter_text=content_md,
            chapter_number=chapter_number,
            project_dir=Path((settings or get_settings()).output.base_dir) / project.slug,
            scenes=_scenes,
            chapter_metadata={
                **(chapter.metadata_json or {}),
                "hard_min_word_count": int(band.hard_min),
                "hard_max_word_count": int(band.hard_max),
            },
        )
        if not deterministic_audit_report.passed:
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "deterministic_audit_latest": deterministic_audit_report.to_dict(),
            }
    except Exception:
        logger.debug("chapter rewrite deterministic audit failed", exc_info=True)

    duplicate_gate_findings = await _collect_post_assembly_duplicate_findings(
        session,
        project=project,
        chapter=chapter,
        content_md=content_md,
    )
    if duplicate_gate_findings:
        logger.warning(
            "rewrite_chapter %d: duplicate gate rejected candidate with %d finding(s).",
            chapter.chapter_number,
            len(duplicate_gate_findings),
        )

    word_count = count_words(content_md)
    quality_gate_outcome = await _evaluate_chapter_quality_gate(
        session=session,
        project=project,
        chapter_number=chapter_number,
        content=content_md,
    )
    if duplicate_gate_findings:
        quality_gate_outcome = "blocked"
    if deterministic_audit_report is not None and not deterministic_audit_report.passed:
        quality_gate_outcome = "blocked"
    quality_gate_violations: list[dict[str, Any]] = []
    if quality_gate_outcome == "blocked":
        latest_quality_report = await session.scalar(
            select(ChapterQualityReportModel)
            .where(ChapterQualityReportModel.chapter_id == chapter.id)
            .order_by(ChapterQualityReportModel.created_at.desc())
        )
        report_json = (
            latest_quality_report.report_json
            if latest_quality_report is not None
            and hasattr(latest_quality_report, "report_json")
            and isinstance(latest_quality_report.report_json, dict)
            else {}
        )
        quality_gate_violations = [
            item
            for item in report_json.get("violations", [])
            if isinstance(item, dict)
        ]
    if (
        quality_gate_outcome == "blocked"
        and settings is not None
        and chapter_context is not None
    ):
        try:
            from bestseller.services.llm_closed_loop import (
                LLMGateFinding,
                build_repair_user_prompt,
            )

            repair_findings = [
                LLMGateFinding(
                    code="CHAPTER_REWRITE_QUALITY_GATE_BLOCKED",
                    severity="critical",
                    path="chapter_rewrite_candidate",
                    message="The rewritten chapter candidate was rejected by the post-rewrite quality gate.",
                    expected="A publishable chapter rewrite that passes quality, duplication, canon, and length gates.",
                    actual=f"quality_gate_outcome={quality_gate_outcome}",
                    repair_action=(
                        "Rewrite the chapter candidate again. Preserve the requested rewrite intent, "
                        "but fix the gate-blocking problems before returning final prose."
                    ),
                )
            ]
            _chapter_band_over = chapter_rewrite_length_band(
                get_settings(),
                getattr(chapter, "target_word_count", None),
                language=project.language,
                direction="over",
                role="editor",
            )
            _chapter_band_under = chapter_rewrite_length_band(
                get_settings(),
                getattr(chapter, "target_word_count", None),
                language=project.language,
                direction="under",
                role="editor",
            )
            for index, violation in enumerate(quality_gate_violations[:8], start=1):
                code = str(violation.get("code") or "CHAPTER_GATE_VIOLATION")
                severity = str(violation.get("severity") or "critical")
                message = str(
                    violation.get("message")
                    or violation.get("detail")
                    or "The chapter rewrite candidate failed a hard gate."
                )
                actual = str(
                    violation.get("actual")
                    or violation.get("found")
                    or violation.get("value")
                    or violation.get("term")
                    or ""
                ).strip()
                if not actual:
                    actual = f"candidate_word_count={word_count}"
                repair_action = str(
                    violation.get("repair_action")
                    or "Rewrite the candidate so this hard gate no longer fires."
                )
                if code == "LENGTH_OVER" or code.endswith("_BLOCK_HIGH"):
                    repair_action = (
                        "Return a complete chapter in compression mode: "
                        f"{_chapter_band_over.safe_min}-{_chapter_band_over.safe_max} Chinese "
                        "characters. Silently count Chinese characters before the final "
                        "answer. Delete/merge redundant beats; "
                        "do not add new scenes, people, places, titles, or factions. "
                        "If deletion would drop below the lower bound, replace repeated "
                        "material with one concise plot-bearing beat instead of summarizing."
                    )
                elif code == "LENGTH_UNDER" or code.endswith("_BLOCK_LOW"):
                    repair_action = (
                        "Return a complete chapter in expansion mode: "
                        f"{_chapter_band_under.safe_min}-{_chapter_band_under.safe_max} Chinese "
                        "characters. Add only useful action pressure, sensory specifics, "
                        "clue movement, cost, or transition beats; do not add new people, "
                        "places, titles, factions, or lore."
                    )
                elif code in {"CANON_FORBIDDEN_TERM", "NAMING_OUT_OF_POOL"}:
                    repair_action = (
                        "Remove or replace the forbidden / out-of-pool canon term everywhere. "
                        "Preserve plot function while using only approved project naming."
                    )
                repair_findings.append(
                    LLMGateFinding(
                        code=code,
                        severity=severity,
                        path=f"chapter_quality_report.violations[{index}]",
                        message=message,
                        expected="A chapter rewrite candidate that clears this exact hard gate.",
                        actual=actual[:240],
                        repair_action=repair_action,
                    )
                )
            for index, finding in enumerate(duplicate_gate_findings[:8], start=1):
                repair_findings.append(
                    LLMGateFinding(
                        code="CHAPTER_REWRITE_DUPLICATE_GATE",
                        severity="critical",
                        path=f"duplicate_gate_findings[{index}]",
                        message=str(finding),
                        expected="No duplicate or near-duplicate paragraph blocks in the rewritten chapter.",
                        actual=str(finding)[:240],
                        repair_action=(
                            "Remove or merge repeated and near-repeated paragraphs, but do not "
                            "solve duplication by making the chapter too short. Replace repetition "
                            "with fresh reader-visible action, consequence, clue movement, or "
                            "transition that keeps the body inside the active length band."
                        ),
                    )
                )
            repair_user_prompt = build_repair_user_prompt(
                original_user_prompt=user_prompt,
                findings=repair_findings,
                language=getattr(project, "language", None),
            )
            repair_completion = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role="editor",
                    system_prompt=system_prompt,
                    user_prompt=repair_user_prompt,
                    fallback_response=fallback_content,
                    prompt_template="chapter_rewrite_repair",
                    prompt_version="1.0",
                    project_id=project.id,
                    workflow_run_id=workflow_run_id,
                    step_run_id=step_run_id,
                    max_tokens_override=_rewrite_output_max_tokens_override(
                        chapter,
                        project,
                        rewrite_task,
                        force_compression=any(
                            str(item.get("code") or "") == "LENGTH_OVER"
                            or str(item.get("code") or "").endswith("_BLOCK_HIGH")
                            for item in quality_gate_violations
                            if isinstance(item, dict)
                        ),
                        force_expansion=any(
                            str(item.get("code") or "") == "LENGTH_UNDER"
                            or str(item.get("code") or "").endswith("_BLOCK_LOW")
                            for item in quality_gate_violations
                            if isinstance(item, dict)
                        ),
                    ),
                    metadata={
                        "project_slug": project.slug,
                        "chapter_number": chapter.chapter_number,
                        "rewrite_task_id": str(rewrite_task.id),
                        "semantic_repair_of": str(llm_run_id) if llm_run_id else None,
                        "repair_findings": [
                            item.to_dict() for item in repair_findings[:12]
                        ],
                    },
                ),
            )
            repaired_content = (
                sanitize_novel_markdown_content(repair_completion.content)
                or fallback_content
            )
            repaired_content = strip_scaffolding_echoes(repaired_content)
            if has_meta_leak(repaired_content):
                repaired_content = await validate_and_clean_novel_content(
                    session,
                    settings,
                    repaired_content,
                    project_id=project.id,
                    workflow_run_id=workflow_run_id,
                    step_run_id=step_run_id,
                )
            repaired_content, _repair_cleanup_stats = _clean_generated_chapter_text(
                repaired_content,
                chapter_number=chapter.chapter_number,
                source="chapter_rewrite_repair",
                min_word_count=chapter_rewrite_length_band(
                    settings,
                    getattr(chapter, "target_word_count", None),
                    language=_project_language(project),
                    direction="normal",
                    role="editor",
                ).hard_min,
            )
            repaired_duplicate_findings = await _collect_post_assembly_duplicate_findings(
                session,
                project=project,
                chapter=chapter,
                content_md=repaired_content,
            )
            repaired_quality_outcome = await _evaluate_chapter_quality_gate(
                session=session,
                project=project,
                chapter_number=chapter_number,
                content=repaired_content,
            )
            if repaired_duplicate_findings:
                repaired_quality_outcome = "blocked"
            if repaired_quality_outcome != "blocked":
                content_md = repaired_content
                model_name = repair_completion.model_name
                llm_run_id = repair_completion.llm_run_id
                generation_mode = repair_completion.provider
                duplicate_gate_findings = repaired_duplicate_findings
                word_count = count_words(content_md)
                quality_gate_outcome = repaired_quality_outcome
            else:
                content_md = repaired_content
                model_name = repair_completion.model_name
                llm_run_id = repair_completion.llm_run_id
                generation_mode = repair_completion.provider
                word_count = count_words(content_md)
                quality_gate_outcome = repaired_quality_outcome
                latest_repaired_quality_report = await session.scalar(
                    select(ChapterQualityReportModel)
                    .where(ChapterQualityReportModel.chapter_id == chapter.id)
                    .order_by(ChapterQualityReportModel.created_at.desc())
                )
                repaired_report_json = (
                    latest_repaired_quality_report.report_json
                    if latest_repaired_quality_report is not None
                    and hasattr(latest_repaired_quality_report, "report_json")
                    and isinstance(latest_repaired_quality_report.report_json, dict)
                    else {}
                )
                repaired_violations = [
                    item
                    for item in repaired_report_json.get("violations", [])
                    if isinstance(item, dict)
                ]
                if repaired_violations:
                    quality_gate_violations = repaired_violations
                duplicate_gate_findings = repaired_duplicate_findings or duplicate_gate_findings
        except Exception:
            logger.warning(
                "rewrite_chapter %d: semantic repair pass failed; keeping blocked candidate",
                chapter.chapter_number,
                exc_info=True,
            )
    quality_retrofit_findings: list[dict[str, Any]] = []
    quality_retrofit_near_miss_metadata: dict[str, Any] | None = None
    if quality_gate_outcome != "blocked":
        quality_retrofit_findings = _quality_retrofit_candidate_findings(
            content_md,
            rewrite_task,
            platform="framework",
        )
        if (
            quality_retrofit_findings
            and settings is not None
            and chapter_context is not None
        ):
            try:
                from bestseller.services.llm_closed_loop import (
                    LLMGateFinding,
                    build_repair_user_prompt,
                )

                repair_findings = [
                    LLMGateFinding(
                        code=str(finding.get("code") or "QUALITY_RETROFIT_BLOCKED"),
                        severity="high",
                        path=f"quality_retrofit.{index}",
                        message=(
                            "The rewritten chapter candidate still fails the "
                            "quality-retrofit detector for the original repair cause."
                        ),
                        expected="A rewrite that clears the same retrofit cause it was assigned to fix.",
                        actual=str(finding.get("detail") or "")[:240],
                        repair_action=str(finding.get("repair_action") or ""),
                    )
                    for index, finding in enumerate(quality_retrofit_findings, start=1)
                ]
                repair_user_prompt = build_repair_user_prompt(
                    original_user_prompt=user_prompt,
                    findings=repair_findings,
                    language=getattr(project, "language", None),
                )
                repair_completion = await complete_text(
                    session,
                    settings,
                    LLMCompletionRequest(
                        logical_role="editor",
                        system_prompt=system_prompt,
                        user_prompt=repair_user_prompt,
                        fallback_response=fallback_content,
                        prompt_template="chapter_rewrite_quality_retrofit_repair",
                        prompt_version="1.0",
                        project_id=project.id,
                        workflow_run_id=workflow_run_id,
                        step_run_id=step_run_id,
                        max_tokens_override=_rewrite_output_max_tokens_override(
                            chapter,
                            project,
                            rewrite_task,
                        ),
                        metadata={
                            "project_slug": project.slug,
                            "chapter_number": chapter.chapter_number,
                            "rewrite_task_id": str(rewrite_task.id),
                            "quality_retrofit_findings": quality_retrofit_findings[:8],
                        },
                    ),
                )
                repaired_content = (
                    sanitize_novel_markdown_content(repair_completion.content)
                    or fallback_content
                )
                repaired_content = strip_scaffolding_echoes(repaired_content)
                if has_meta_leak(repaired_content):
                    repaired_content = await validate_and_clean_novel_content(
                        session,
                        settings,
                        repaired_content,
                        project_id=project.id,
                        workflow_run_id=workflow_run_id,
                        step_run_id=step_run_id,
                    )
                repaired_content, _retrofit_cleanup_stats = _clean_generated_chapter_text(
                    repaired_content,
                    chapter_number=chapter.chapter_number,
                    source="chapter_rewrite_quality_retrofit_repair",
                    min_word_count=chapter_rewrite_length_band(
                        settings,
                        getattr(chapter, "target_word_count", None),
                        language=_project_language(project),
                        direction="normal",
                        role="editor",
                    ).hard_min,
                )
                repaired_duplicate_findings = await _collect_post_assembly_duplicate_findings(
                    session,
                    project=project,
                    chapter=chapter,
                    content_md=repaired_content,
                )
                repaired_quality_outcome = await _evaluate_chapter_quality_gate(
                    session=session,
                    project=project,
                    chapter_number=chapter_number,
                    content=repaired_content,
                )
                if repaired_duplicate_findings:
                    repaired_quality_outcome = "blocked"
                repaired_retrofit_findings = (
                    []
                    if repaired_quality_outcome == "blocked"
                    else _quality_retrofit_candidate_findings(
                        repaired_content,
                        rewrite_task,
                        platform="framework",
                    )
                )
                if repaired_quality_outcome != "blocked" and not repaired_retrofit_findings:
                    content_md = repaired_content
                    model_name = repair_completion.model_name
                    llm_run_id = repair_completion.llm_run_id
                    generation_mode = repair_completion.provider
                    duplicate_gate_findings = repaired_duplicate_findings
                    word_count = count_words(content_md)
                    quality_gate_outcome = repaired_quality_outcome
                    quality_retrofit_findings = []
                else:
                    content_md = repaired_content
                    model_name = repair_completion.model_name
                    llm_run_id = repair_completion.llm_run_id
                    generation_mode = repair_completion.provider
                    word_count = count_words(content_md)
                    quality_gate_outcome = repaired_quality_outcome
                    duplicate_gate_findings = (
                        repaired_duplicate_findings or duplicate_gate_findings
                    )
                    if repaired_quality_outcome == "blocked":
                        latest_repaired_quality_report = await session.scalar(
                            select(ChapterQualityReportModel)
                            .where(ChapterQualityReportModel.chapter_id == chapter.id)
                            .order_by(ChapterQualityReportModel.created_at.desc())
                        )
                        repaired_report_json = (
                            latest_repaired_quality_report.report_json
                            if latest_repaired_quality_report is not None
                            and hasattr(latest_repaired_quality_report, "report_json")
                            and isinstance(latest_repaired_quality_report.report_json, dict)
                            else {}
                        )
                        repaired_violations = [
                            item
                            for item in repaired_report_json.get("violations", [])
                            if isinstance(item, dict)
                        ]
                        if repaired_violations:
                            quality_gate_violations = repaired_violations
                    if repaired_retrofit_findings:
                        quality_retrofit_findings = repaired_retrofit_findings
            except Exception:
                logger.warning(
                    "rewrite_chapter %d: quality-retrofit repair pass failed; "
                    "rejecting candidate",
                    chapter.chapter_number,
                    exc_info=True,
                )
        if quality_retrofit_findings:
            quality_retrofit_near_miss_metadata = (
                _quality_retrofit_near_miss_acceptance(
                    quality_retrofit_findings,
                    recent_failed_rewrites=recent_failed_rewrites,
                )
            )
            if quality_retrofit_near_miss_metadata:
                logger.info(
                    "chapter %d quality-retrofit near miss accepted: %s",
                    chapter.chapter_number,
                    quality_retrofit_near_miss_metadata,
                )
                quality_retrofit_findings = []
            else:
                quality_gate_outcome = "blocked"
    candidate_micro_trim_metadata: dict[str, Any] | None = None
    if quality_gate_outcome == "blocked" and content_md and not duplicate_gate_findings:
        try:
            latest_candidate_quality_report = await session.scalar(
                select(ChapterQualityReportModel)
                .where(ChapterQualityReportModel.chapter_id == chapter.id)
                .order_by(ChapterQualityReportModel.created_at.desc())
            )
            candidate_report_json = (
                latest_candidate_quality_report.report_json
                if latest_candidate_quality_report is not None
                and hasattr(latest_candidate_quality_report, "report_json")
                and isinstance(latest_candidate_quality_report.report_json, dict)
                else {}
            )
            candidate_blocking_codes = [
                str(code).strip()
                for code in candidate_report_json.get("blocking_codes", [])
                if str(code).strip()
            ]
            candidate_violations = [
                item
                for item in candidate_report_json.get("violations", [])
                if isinstance(item, dict)
            ]
            length_max = _length_over_max_from_violations(candidate_violations)
            if _only_length_over_blocking_codes(candidate_blocking_codes) and length_max:
                trimmed_content, trim_info = _micro_trim_overlength_chapter_text(
                    content_md,
                    max_words=length_max,
                    max_overage=180,
                    safety_margin=20,
                )
                if trim_info.get("applied"):
                    trimmed_duplicate_findings = (
                        await _collect_post_assembly_duplicate_findings(
                            session,
                            project=project,
                            chapter=chapter,
                            content_md=trimmed_content,
                        )
                    )
                    trimmed_quality_outcome = await _evaluate_chapter_quality_gate(
                        session=session,
                        project=project,
                        chapter_number=chapter_number,
                        content=trimmed_content,
                    )
                    if trimmed_duplicate_findings:
                        trimmed_quality_outcome = "blocked"
                    trimmed_retrofit_findings = (
                        []
                        if trimmed_quality_outcome == "blocked"
                        else _quality_retrofit_candidate_findings(
                            trimmed_content,
                            rewrite_task,
                            platform="framework",
                        )
                    )
                    trimmed_near_miss_metadata = (
                        _quality_retrofit_near_miss_acceptance(
                            trimmed_retrofit_findings,
                            recent_failed_rewrites=recent_failed_rewrites,
                        )
                        if trimmed_retrofit_findings
                        else None
                    )
                    if trimmed_quality_outcome != "blocked" and (
                        not trimmed_retrofit_findings or trimmed_near_miss_metadata
                    ):
                        content_md = trimmed_content
                        word_count = count_words(content_md)
                        quality_gate_outcome = trimmed_quality_outcome
                        quality_gate_violations = []
                        duplicate_gate_findings = tuple()
                        quality_retrofit_findings = []
                        quality_retrofit_near_miss_metadata = trimmed_near_miss_metadata
                        candidate_micro_trim_metadata = {
                            **trim_info,
                            "postprocess_mode": "micro_length_trim_candidate",
                            "candidate_quality_gate_outcome_before_trim": "blocked",
                            "candidate_blocking_codes_before_trim": candidate_blocking_codes,
                        }
                        logger.info(
                            "chapter %d candidate micro-trimmed after length-only "
                            "block: %s -> %s chars",
                            chapter.chapter_number,
                            trim_info.get("before_word_count"),
                            trim_info.get("after_word_count"),
                        )
                    elif trimmed_retrofit_findings:
                        quality_retrofit_findings = trimmed_retrofit_findings
        except Exception:
            logger.warning(
                "chapter %d: candidate micro length trim failed",
                chapter.chapter_number,
                exc_info=True,
            )
    quality_gate_rejected_current_promotion = quality_gate_outcome == "blocked"
    llm_candidate_quality_gate_outcome = quality_gate_outcome
    llm_candidate_word_count = word_count
    llm_candidate_quality_gate_violations = list(quality_gate_violations)
    micro_trim_metadata: dict[str, Any] | None = None
    if quality_gate_rejected_current_promotion and current_draft.content_md:
        try:
            current_quality_gate_outcome = await _evaluate_chapter_quality_gate(
                session=session,
                project=project,
                chapter_number=chapter_number,
                content=current_draft.content_md or "",
            )
            if current_quality_gate_outcome == "blocked":
                latest_current_quality_report = await session.scalar(
                    select(ChapterQualityReportModel)
                    .where(ChapterQualityReportModel.chapter_id == chapter.id)
                    .order_by(ChapterQualityReportModel.created_at.desc())
                )
                current_report_json = (
                    latest_current_quality_report.report_json
                    if latest_current_quality_report is not None
                    and hasattr(latest_current_quality_report, "report_json")
                    and isinstance(latest_current_quality_report.report_json, dict)
                    else {}
                )
                current_violations = [
                    item
                    for item in current_report_json.get("violations", [])
                    if isinstance(item, dict)
                ]
                current_codes = {
                    str(item.get("code") or "").strip()
                    for item in current_violations
                    if str(item.get("code") or "").strip()
                }
                current_blocking_codes = [
                    str(code).strip()
                    for code in current_report_json.get("blocking_codes", [])
                    if str(code).strip()
                ]
                only_length_over = _only_length_over_blocking_codes(
                    current_blocking_codes or current_codes
                )
                length_max = _length_over_max_from_violations(current_violations)
                if only_length_over and length_max:
                    trimmed_content, trim_info = _micro_trim_overlength_chapter_text(
                        current_draft.content_md or "",
                        max_words=length_max,
                    )
                    if trim_info.get("applied"):
                        trimmed_duplicate_findings = (
                            await _collect_post_assembly_duplicate_findings(
                                session,
                                project=project,
                                chapter=chapter,
                                content_md=trimmed_content,
                            )
                        )
                        trimmed_quality_outcome = await _evaluate_chapter_quality_gate(
                            session=session,
                            project=project,
                            chapter_number=chapter_number,
                            content=trimmed_content,
                        )
                        if trimmed_duplicate_findings:
                            trimmed_quality_outcome = "blocked"
                        if trimmed_quality_outcome != "blocked":
                            content_md = trimmed_content
                            word_count = count_words(content_md)
                            quality_gate_outcome = trimmed_quality_outcome
                            quality_gate_violations = []
                            duplicate_gate_findings = tuple()
                            quality_gate_rejected_current_promotion = False
                            micro_trim_metadata = {
                                **trim_info,
                                "source_chapter_draft_id": str(current_draft.id),
                                "source_chapter_draft_version_no": current_draft.version_no,
                                "postprocess_mode": "micro_length_trim_current_draft",
                                "current_quality_gate_outcome_before_trim": current_quality_gate_outcome,
                            }
                            logger.info(
                                "chapter %d current draft micro-trimmed after rewrite "
                                "candidate rejection: %s -> %s chars",
                                chapter.chapter_number,
                                trim_info.get("before_word_count"),
                                trim_info.get("after_word_count"),
                            )
        except Exception:
            logger.warning(
                "chapter %d: micro length trim failed; preserving current draft",
                chapter.chapter_number,
                exc_info=True,
            )
    max_existing_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id
                )
            )
        )
        or 0
    )
    next_version = max(max_existing_version, int(current_draft.version_no or 0)) + 1

    if not quality_gate_rejected_current_promotion:
        await session.execute(
            update(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .values(is_current=False)
        )

    new_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=list(current_draft.assembled_from_scene_draft_ids),
        is_current=not quality_gate_rejected_current_promotion,
        llm_run_id=llm_run_id,
    )
    session.add(new_draft)
    await session.flush()
    rewrite_convergence_metadata: dict[str, Any] | None = None
    try:
        from bestseller.services.rewrite_convergence import (
            assess_convergence,
            record_rewrite_attempt,
        )

        audit_codes = tuple(
            finding.code for finding in deterministic_audit_report.findings
        ) if deterministic_audit_report is not None else ()
        block_codes = tuple(
            str(item.get("code") or "")
            for item in quality_gate_violations
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        )
        record_rewrite_attempt(
            chapter,
            version=next_version,
            block_codes=block_codes,
            word_count=word_count,
            audit_codes=audit_codes,
        )
        convergence = assess_convergence(
            chapter,
            new_candidate_audit={},
        )
        rewrite_convergence_metadata = {
            "is_diverging": convergence.is_diverging,
            "is_stuck": convergence.is_stuck,
            "is_oscillating": convergence.is_oscillating,
            "recommended_action": convergence.recommended_action,
        }
        if convergence.recommended_action == "stop_to_human":
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "rewrite_convergence_exhausted": True,
                "requires_human_review": True,
            }
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "blocked"
    except Exception:
        logger.debug("rewrite convergence tracking failed", exc_info=True)
    rewrite_task.attempts = int(rewrite_task.attempts or 0) + 1
    metadata = {
        **(rewrite_task.metadata_json or {}),
        "generation_mode": generation_mode,
        "model_name": model_name,
        "candidate_generation_mode": generation_mode,
        "candidate_model_name": model_name,
        "candidate_llm_run_id": str(llm_run_id) if llm_run_id else None,
        "candidate_chapter_draft_id": str(new_draft.id),
        "candidate_chapter_draft_version_no": next_version,
        "candidate_word_count": word_count,
        "candidate_quality_gate_outcome": quality_gate_outcome,
    }
    if working_draft.id != current_draft.id:
        metadata["working_chapter_draft_id"] = str(working_draft.id)
        metadata["working_chapter_draft_version_no"] = working_draft.version_no
        metadata["working_chapter_draft_word_count"] = working_draft.word_count
        metadata["working_draft_source"] = "previous_failed_retrofit_candidate"
    if micro_trim_metadata:
        metadata["micro_length_trim"] = micro_trim_metadata
        metadata["llm_candidate_quality_gate_outcome"] = llm_candidate_quality_gate_outcome
        metadata["llm_candidate_word_count"] = llm_candidate_word_count
        if llm_candidate_quality_gate_violations:
            metadata["llm_candidate_quality_gate_violations"] = (
                llm_candidate_quality_gate_violations[:12]
            )
    if candidate_micro_trim_metadata:
        metadata["candidate_micro_length_trim"] = candidate_micro_trim_metadata
    if rewrite_convergence_metadata:
        metadata["rewrite_convergence"] = rewrite_convergence_metadata
    if deterministic_postprocess_metadata:
        metadata["deterministic_post_process"] = deterministic_postprocess_metadata
    if deterministic_audit_report is not None:
        metadata["deterministic_audit"] = deterministic_audit_report.to_dict()
    if quality_retrofit_near_miss_metadata:
        metadata["quality_retrofit_near_miss_acceptance"] = (
            quality_retrofit_near_miss_metadata
        )
    if quality_gate_violations:
        metadata["candidate_quality_gate_violations"] = quality_gate_violations[:12]
    if duplicate_gate_findings:
        metadata["candidate_duplicate_gate_findings"] = [
            {
                "source": finding.source,
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "evidence": finding.evidence,
                "payload": finding.payload,
            }
            for finding in duplicate_gate_findings
        ]
    if quality_retrofit_findings:
        metadata["candidate_quality_retrofit_findings"] = quality_retrofit_findings[:12]
    if quality_gate_rejected_current_promotion:
        preserved_current_quality_gate_outcome: str | None = None
        try:
            preserved_current_quality_gate_outcome = await _evaluate_chapter_quality_gate(
                session=session,
                project=project,
                chapter_number=chapter_number,
                content=current_draft.content_md or "",
            )
            chapter.production_state = preserved_current_quality_gate_outcome
            chapter.current_word_count = count_words(current_draft.content_md or "")
        except Exception:
            logger.debug(
                "chapter %d: preserved-current quality recheck failed after rejected rewrite",
                chapter.chapter_number,
                exc_info=True,
            )
        if duplicate_gate_findings:
            current_duplicate_findings = await _collect_post_assembly_duplicate_findings(
                session,
                project=project,
                chapter=chapter,
                content_md=current_draft.content_md or "",
            )
            if current_duplicate_findings:
                _stamp_duplicate_content_block(chapter, current_duplicate_findings)
                chapter.production_state = "blocked"
        rewrite_task.status = "failed"
        rewrite_task.error_log = (
            "chapter rewrite rejected by quality gate; current draft preserved"
        )
        rewrite_task.metadata_json = {
            **metadata,
            "quality_gate_rejected_current_promotion": True,
            "quality_retrofit_rejected_current_promotion": bool(
                quality_retrofit_findings
            ),
            "preserved_current_chapter_draft_id": str(current_draft.id),
            "preserved_current_chapter_draft_version_no": current_draft.version_no,
            "preserved_current_quality_gate_outcome": preserved_current_quality_gate_outcome,
        }
        logger.warning(
            "chapter %d rewrite candidate v%d rejected by quality gate; "
            "preserving current draft v%d",
            chapter.chapter_number,
            next_version,
            current_draft.version_no,
        )
        return current_draft, rewrite_task

    rewrite_task.status = "completed"
    rewrite_task.metadata_json = {
        **metadata,
        "rewritten_chapter_draft_id": str(new_draft.id),
    }
    chapter.current_word_count = word_count
    chapter.status = ChapterStatus.REVIEW.value
    if quality_gate_outcome is not None:
        chapter.production_state = quality_gate_outcome
    return new_draft, rewrite_task


async def rewrite_scene_from_task(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    rewrite_task_id: UUID | None = None,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    context_packet: SceneWriterContextPacket | None = None,
) -> tuple[SceneDraftVersionModel, RewriteTaskModel]:
    project, chapter, scene, style_guide, current_draft = await _load_scene_context(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )

    rewrite_query = select(RewriteTaskModel).where(
        RewriteTaskModel.project_id == project.id,
        RewriteTaskModel.trigger_source_id == scene.id,
    )
    if rewrite_task_id is not None:
        rewrite_query = rewrite_query.where(RewriteTaskModel.id == rewrite_task_id)
    else:
        rewrite_query = rewrite_query.where(RewriteTaskModel.status.in_(["pending", "queued"]))
    rewrite_query = rewrite_query.order_by(RewriteTaskModel.created_at.desc())

    rewrite_task = await session.scalar(rewrite_query.limit(1))
    if rewrite_task is None:
        raise ValueError(
            f"Scene {scene_number} in chapter {chapter_number} does not have a pending rewrite task."
        )

    fallback_content = render_rewritten_scene_markdown(
        project,
        chapter,
        scene,
        current_draft,
        rewrite_task,
        style_guide,
    )
    model_name = "mock-editor"
    llm_run_id: UUID | None = None
    generation_mode = "rewrite-fallback"
    content_md = fallback_content
    prompt_trace_path: str | None = None
    if settings is not None:
        system_prompt, user_prompt = build_scene_rewrite_prompts(
            project,
            chapter,
            scene,
            current_draft,
            rewrite_task,
            style_guide,
            context_packet=context_packet,
            context_budget_tokens=settings.generation.context_budget_tokens,
        )
        missing_context_blocks = _missing_required_rewrite_context_blocks(
            context_packet,
            user_prompt,
        )
        if missing_context_blocks:
            logger.warning(
                "Scene %s %d.%d rewrite prompt missing required context blocks: %s",
                project.slug,
                chapter.chapter_number,
                scene.scene_number,
                missing_context_blocks,
            )
            rewrite_task.metadata_json = {
                **(rewrite_task.metadata_json or {}),
                "rewrite_context_missing_blocks": missing_context_blocks,
            }
        prompt_trace_path = _maybe_write_scene_prompt_trace(
            settings,
            project,
            chapter,
            scene,
            context_packet,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            model_tier="editor",
            trace_kind="rewrite",
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_content,
                prompt_template="scene_rewrite",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                max_tokens_override=prose_output_max_tokens_for_target(
                    scene.target_word_count,
                    language=_project_language(project),
                    settings=settings,
                    role="editor",
                ),
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "rewrite_task_id": str(rewrite_task.id),
                    **(
                        {"rewrite_context_missing_blocks": missing_context_blocks}
                        if missing_context_blocks
                        else {}
                    ),
                    **({"prompt_trace_path": prompt_trace_path} if prompt_trace_path else {}),
                },
            ),
        )
        candidate_md = sanitize_novel_markdown_content(completion.content)
        content_md = candidate_md or fallback_content
        # Guard against degenerate/truncated rewrites: an editor model can hit
        # ``finish_reason='length'`` and return a short partial scene. Accepting
        # it would shrink the scene far below its floor, fire a spurious
        # CHAPTER_LENGTH_BLOCK_LOW, and burn a repair cycle. If the candidate
        # collapses below the scene floor *and* is materially shorter than the
        # prior draft, keep the known-good draft (legitimate compression that
        # stays above the floor is unaffected).
        if candidate_md:
            try:
                scene_floor = max(300, int(int(getattr(scene, "target_word_count", 0) or 0) * 0.6))
                prev_words = count_words(fallback_content)
                candidate_words = count_words(candidate_md)
                if (
                    prev_words > 0
                    and candidate_words < scene_floor
                    and candidate_words < int(prev_words * 0.85)
                ):
                    logger.warning(
                        "Scene %s %d.%d rewrite produced degenerate short output "
                        "(%d words < floor %d, prior draft %d) — keeping prior draft.",
                        project.slug,
                        chapter.chapter_number,
                        scene.scene_number,
                        candidate_words,
                        scene_floor,
                        prev_words,
                    )
                    content_md = fallback_content
            except Exception:
                logger.debug("scene rewrite degenerate-output guard failed", exc_info=True)
        content_md = strip_scaffolding_echoes(content_md)
        if has_meta_leak(content_md):
            content_md = await validate_and_clean_novel_content(
                session,
                settings,
                content_md,
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
            )
        model_name = completion.model_name
        llm_run_id = completion.llm_run_id
        generation_mode = completion.provider
    else:
        content_md = strip_scaffolding_echoes(sanitize_novel_markdown_content(content_md))
    word_count = count_words(content_md)
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(SceneDraftVersionModel.version_no), 0)).where(
                    SceneDraftVersionModel.scene_card_id == scene.id
                )
            )
        )
        or 0
    ) + 1

    await session.execute(
        update(SceneDraftVersionModel)
        .where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    new_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        is_current=True,
        model_name=model_name,
        prompt_template="scene_rewrite",
        prompt_version="1.0",
        llm_run_id=llm_run_id,
        generation_params={
            "mode": generation_mode,
            "rewrite_task_id": str(rewrite_task.id),
            "target_word_count": scene.target_word_count,
            **({"prompt_trace_path": prompt_trace_path} if prompt_trace_path else {}),
        },
    )
    session.add(new_draft)
    await session.flush()

    rewrite_task.status = "completed"
    rewrite_task.attempts = int(rewrite_task.attempts) + 1
    rewrite_task.metadata_json = {
        **rewrite_task.metadata_json,
        "completed_draft_id": str(new_draft.id),
        "previous_draft_id": str(current_draft.id),
    }
    scene.status = SceneStatus.DRAFTED.value
    chapter.status = ChapterStatus.DRAFTING.value
    await session.flush()
    return new_draft, rewrite_task

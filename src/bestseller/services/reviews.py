from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

from sqlalchemy import func, select, update
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
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
)
from bestseller.services.context import build_chapter_writer_context, build_scene_writer_context
from bestseller.services.drafts import (
    _NOVEL_OUTPUT_PROHIBITION,
    _NOVEL_OUTPUT_PROHIBITION_EN,
    _normalize_fragment,
    count_words,
    has_meta_leak,
    sanitize_novel_markdown_content,
    strip_scaffolding_echoes,
    validate_and_clean_novel_content,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology import render_methodology_scene_rules
from bestseller.services.output_hygiene import collect_unfinished_artifact_issues
from bestseller.services.prompt_packs import (
    render_methodology_block,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.rewrite_impacts import analyze_rewrite_impacts_for_scene_task
from bestseller.services.writing_profile import (
    is_english_language,
    normalize_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings


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
        ]
    if chapter_contract is not None:
        return [
            ("chapter_summary", getattr(chapter_contract, "contract_summary", None)),
            ("core_conflict", getattr(chapter_contract, "core_conflict", None)),
            ("emotional_shift", getattr(chapter_contract, "emotional_shift", None)),
            ("information_release", getattr(chapter_contract, "information_release", None)),
            ("closing_hook", getattr(chapter_contract, "closing_hook", None)),
        ]
    return []


def _chapter_contract_expectations(
    *,
    chapter_contract: Any | None = None,
) -> list[tuple[str, str | None]]:
    if chapter_contract is None:
        return []
    return [
        ("chapter_summary", getattr(chapter_contract, "contract_summary", None)),
        ("core_conflict", getattr(chapter_contract, "core_conflict", None)),
        ("emotional_shift", getattr(chapter_contract, "emotional_shift", None)),
        ("information_release", getattr(chapter_contract, "information_release", None)),
        ("closing_hook", getattr(chapter_contract, "closing_hook", None)),
    ]


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
        "You are a scene reviewer for a long-form fiction pipeline. Return concise, actionable editorial feedback.\n"
        "You MUST evaluate the prose against these methodology rules:\n"
        "1. Show-don't-tell: emotions conveyed through action/physicality, NOT named directly\n"
        "2. Sensory richness: at least 2 sensory channels (sight, sound, touch, smell, taste)\n"
        "3. Dialogue subtext: characters should NOT state intentions directly; tension comes from gap between words and meaning\n"
        "4. Tail hook: scene must end on an unresolved question, threat, or revelation\n"
        "5. Reaction amplification: after key moments, other characters' reactions amplify impact\n\n"
        "Return your response in this EXACT format:\n"
        "VERDICT: pass OR rewrite\n"
        "METHODOLOGY: [list violations found]\n"
        "REWRITE_DIRECTION: [if rewrite, specific instructions]\n"
        "COMMENTARY: [brief editorial note]"
        if is_en
        else (
            "你是长篇小说审校系统里的场景评论者。"
            "请输出简洁、专业、可执行的审校意见，不要复述需求。\n"
            "你必须按以下方法论规则评估文本质量：\n"
            "1. 展示不讲述：情绪通过动作/身体反应传达，不能直接写情绪词（愤怒、伤心、高兴等）\n"
            "2. 感官丰富度：至少使用2个感官通道（视觉、听觉、触觉、嗅觉、味觉）\n"
            "3. 对话潜台词：角色不能直白表达意图，张力来自话语和真实意图的反差\n"
            "4. 尾钩强度：场景必须以未解答的问题、威胁或揭示结尾\n"
            "5. 反应放大：关键时刻后必须有其他角色的反应来放大冲击力\n\n"
            "请严格按以下格式输出：\n"
            "VERDICT: pass 或 rewrite\n"
            "METHODOLOGY: [发现的方法论违规]\n"
            "REWRITE_DIRECTION: [如需重写，给出具体方向]\n"
            "COMMENTARY: [简要编辑意见]"
        )
    )
    if _genre_review_system:
        system_prompt += f"\n\n{'[Genre review requirements]' if is_en else '【品类审核要求】'}\n{_genre_review_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_scene_review = f"{render_prompt_pack_fragment(prompt_pack, 'scene_review')}\n" if prompt_pack else ""
    _methodology_review_block = render_methodology_block(prompt_pack, phase="review")
    _methodology_line = f"\n{_methodology_review_block}\n" if _methodology_review_block else ""
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
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _project_language(project)
    is_en = is_english_language(language)
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        "You are an English-language fiction rewriting editor. "
        "Output Markdown prose only, with no explanations, apologies, or change logs.\n"
        + _NOVEL_OUTPUT_PROHIBITION_EN
        + _REWRITE_STRATEGY_CONTRACT_EN
        if is_en
        else (
            "你是长篇中文小说写作系统里的重写编辑。"
            "输出必须是 Markdown 正文，不要解释，不要道歉，不要列修改清单。\n"
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
    _methodology_scene_block = render_methodology_block(prompt_pack, phase="scene")
    _methodology_rules = render_methodology_scene_rules(
        chapter_number=chapter.chapter_number,
        is_opening=(chapter.chapter_number <= 3),
        is_climax=False,
        pacing_mode="build",
    )
    _methodology_line = ""
    if _methodology_scene_block:
        _methodology_line += f"\n{_methodology_scene_block}\n"
    if _methodology_rules:
        _methodology_line += f"\n{_methodology_rules}\n"
    # ── Word-count envelope: hard constraint to prevent rewrite-bloat spiral ──
    # The scene writer already enforces a strict word range; the rewriter must
    # enforce the SAME envelope or it will inflate past target on every pass.
    _target_wc = int(scene.target_word_count or 0)
    _current_wc = int(current_draft.word_count or 0)
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
            f"{_methodology_line}"
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
            f"{_methodology_line}"
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
        "You are a chapter reviewer for a long-form fiction pipeline. Return concise, actionable editorial feedback.\n"
        "Evaluate the chapter against these methodology rules:\n"
        "1. Emotion compression/release: tension must build before payoff\n"
        "2. Hook lifecycle: chapter must plant new hooks and resolve old ones\n"
        "3. Conflict stakes: every conflict needs clear stakes (what is lost on failure)\n"
        "4. Read-on momentum: chapter ending must create irresistible urge to continue\n\n"
        "Return your response in this EXACT format:\n"
        "VERDICT: pass OR rewrite\n"
        "METHODOLOGY: [list violations found]\n"
        "REWRITE_DIRECTION: [if rewrite, specific instructions]\n"
        "COMMENTARY: [brief editorial note]"
        if is_en
        else (
            "你是长篇小说审校系统里的章节评论者。"
            "请输出简洁、专业、可执行的章节审校意见，不要复述需求。\n"
            "请按以下方法论规则评估章节质量：\n"
            "1. 情绪压缩释放：爽点前必须有充分的情绪铺垫\n"
            "2. 钩子生命周期：本章必须植入新钩子并消解旧钩子\n"
            "3. 冲突筹码：每个冲突必须有明确筹码（输了会失去什么）\n"
            "4. 追读欲：章末必须制造让读者无法停下的冲动\n\n"
            "请严格按以下格式输出：\n"
            "VERDICT: pass 或 rewrite\n"
            "METHODOLOGY: [发现的方法论违规]\n"
            "REWRITE_DIRECTION: [如需重写，给出具体方向]\n"
            "COMMENTARY: [简要编辑意见]"
        )
    )
    if _genre_ch_review_system:
        system_prompt += f"\n\n{'[Genre review requirements]' if is_en else '【品类审核要求】'}\n{_genre_ch_review_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_chapter_review = f"{render_prompt_pack_fragment(prompt_pack, 'chapter_review')}\n" if prompt_pack else ""
    _methodology_review_block = render_methodology_block(prompt_pack, phase="review")
    _methodology_line = f"\n{_methodology_review_block}\n" if _methodology_review_block else ""
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
        "You are an English-language fiction chapter rewriting editor. Output Markdown prose only, with no explanations or change logs.\n"
        + _NOVEL_OUTPUT_PROHIBITION_EN
        + _REWRITE_STRATEGY_CONTRACT_EN
        if is_en
        else (
            "你是长篇中文小说写作系统里的章节重写编辑。"
            "输出必须是 Markdown 正文，不要解释，不要列修改清单。\n"
            + _NOVEL_OUTPUT_PROHIBITION
            + _REWRITE_STRATEGY_CONTRACT
        )
    )
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_chapter_rewrite = f"{render_prompt_pack_fragment(prompt_pack, 'chapter_rewrite')}\n" if prompt_pack else ""
    _methodology_scene_block = render_methodology_block(prompt_pack, phase="scene")
    _methodology_rules = render_methodology_scene_rules(
        chapter_number=chapter.chapter_number,
        is_opening=(chapter.chapter_number <= 3),
        is_climax=False,
        pacing_mode="build",
    )
    _methodology_line = ""
    if _methodology_scene_block:
        _methodology_line += f"\n{_methodology_scene_block}\n"
    if _methodology_rules:
        _methodology_line += f"\n{_methodology_rules}\n"
    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}: {chapter.title or ''}\n"
            f"Chapter goal: {chapter.chapter_goal}\n"
            f"{_wrap_rewrite_reference_for_language(rewrite_task.instructions, rewrite_task.rewrite_strategy, language=language)}"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_chapter_rewrite}"
            f"{_methodology_line}"
            f"Chapter context:\n{_render_chapter_context_section(chapter_context, language=language)}\n"
            f"Current draft:\n{current_draft.content_md}\n"
            "Rewrite the chapter in English only while preserving the core event order. Improve transitions, chapter propulsion, and the ending hook first."
        )
        if is_en
        else (
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
            f"章节目标：{chapter.chapter_goal}\n"
            f"{_wrap_rewrite_reference_for_language(rewrite_task.instructions, rewrite_task.rewrite_strategy, language=language)}"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_chapter_rewrite}"
            f"{_methodology_line}"
            f"章节上下文：\n{_render_chapter_context_section(chapter_context, language=language)}\n"
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
            **contract_evidence,
        },
        rewrite_instructions=rewrite_instructions,
    )


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

    coverage = _clamp_score(
        0.18
        + (max(scene_heading_ratio, scene_title_ratio) * 0.52)
        + (0.1 if expected_scene_count <= 1 or scene_heading_count == expected_scene_count else 0.0)
        + (0.1 if draft.word_count >= max(900, chapter.target_word_count * 0.45) else 0.0)
    )
    coherence = _clamp_score(
        0.22
        + (scene_title_ratio * 0.24)
        + (coverage * 0.18)
        + (transition_signal * 0.22)
        + (0.08 if ("## Scene 1" in content or "## 场景 1" in content) else 0.0)
        + (0.08 if content.count("\n\n") >= expected_scene_count * 2 else 0.0)
    )

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
        + (0.1 if expected_scene_count <= 1 or scene_heading_count == expected_scene_count else 0.0)
        + (0.08 if draft.word_count >= max(900, chapter.target_word_count * 0.5) else 0.0)
        + (0.04 if _has_backward_ref and _has_forward_ref else 0.0)
    )

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
        + (tail_tension_signal * 0.52)
        + (0.08 if "？" in tail_excerpt or "?" in tail_excerpt else 0.0)
        + (0.08 if "必须" in tail_excerpt or "立刻" in tail_excerpt else 0.0)
        + (0.12 if "下一步" in tail_excerpt or "新的不确定性" in tail_excerpt else 0.0)
    )
    main_plot_progression = _clamp_score(
        0.24
        + (
            max(
                coverage * 0.6,
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
    else:
        subplot_progression = 1.0
    ending_hook_effectiveness = _clamp_score(
        0.2
        + hook * 0.32
        + (
            max(
                tail_tension_signal,
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
            if any(term in tail_excerpt for term in ("下一步", "新的不确定性", "门外", "脚步声"))
            else 0.0
        )
        + (0.1 if ("必须" in tail_excerpt or "立刻" in tail_excerpt or "已经" in tail_excerpt) else 0.0)
    )
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

    contract_alignment, contract_evidence = _evaluate_contract_alignment(
        content,
        expectations=_chapter_contract_expectations(chapter_contract=chapter_contract),
        label_weights={
            "chapter_summary": 0.65,
            "core_conflict": 1.15,
            "emotional_shift": 0.95,
            "information_release": 1.1,
            "closing_hook": 1.1,
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

    hygiene_issues = collect_unfinished_artifact_issues(content, language=language)
    for issue in hygiene_issues:
        findings.append(
            ChapterReviewFinding(
                category="output_hygiene",
                severity="high",
                message=issue,
            )
        )

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
            "main_plot_progression": main_plot_progression,
            "subplot_progression": subplot_progression,
            "ending_hook_effectiveness": ending_hook_effectiveness,
            "volume_mission_alignment": volume_mission_alignment,
            "meta_leak_detected": meta_leak,
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
    if chapter_status in ("complete", "revision"):
        logger.debug(
            "chapter_antagonist_audit: skipping ch%d (status=%s) — "
            "already-written content is not retroactively flagged.",
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
        if llm_verdict == "rewrite" and review_result.verdict == "pass":
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
        rewrite_task = RewriteTaskModel(
            project_id=project.id,
            trigger_type="chapter_review",
            trigger_source_id=chapter.id,
            rewrite_strategy="chapter_coherence_bridge_rewrite",
            priority=4,
            status="pending",
            instructions=review_result.rewrite_instructions or "请补强当前章节。",
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
            },
        )
        session.add(rewrite_task)
        chapter.status = ChapterStatus.REVISION.value
    else:
        chapter.status = ChapterStatus.COMPLETE.value

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

    chapter_context = None
    if settings is not None:
        chapter_context = await build_chapter_writer_context(
            session,
            settings,
            project_slug,
            chapter_number,
        )
    fallback_content = render_rewritten_chapter_markdown(
        project,
        chapter,
        current_draft,
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
            current_draft,
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
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "rewrite_task_id": str(rewrite_task.id),
                },
            ),
        )
        content_md = sanitize_novel_markdown_content(completion.content) or fallback_content
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

    # ── Post-rewrite intra-chapter deduplication ──
    # Chapter rewrite LLMs occasionally echo large blocks verbatim (or near-verbatim).
    # Without this cleanup, byte-identical and paraphrased paragraphs survive
    # into the saved draft. Parity with assemble_chapter_draft.
    try:
        from bestseller.services.deduplication import (
            clean_meta_text_markers,
            detect_intra_chapter_repetition,
            remove_intra_chapter_duplicates_paraphrase,
        )

        content_md, _meta_removed = clean_meta_text_markers(content_md)
        if _meta_removed:
            logger.info(
                "rewrite_chapter %d: removed %d meta-text marker(s)",
                chapter.chapter_number, _meta_removed,
            )
        _dup_findings = detect_intra_chapter_repetition(content_md)
        if _dup_findings:
            logger.warning(
                "rewrite_chapter %d: %d duplicate paragraph(s) after rewrite \u2014 auto-removing",
                chapter.chapter_number, len(_dup_findings),
            )
            content_md, _removed = remove_intra_chapter_duplicates_paraphrase(content_md)
            logger.info(
                "rewrite_chapter %d: removed %d duplicate paragraph(s)",
                chapter.chapter_number, _removed,
            )
    except Exception:
        logger.debug("Post-rewrite dedup failed (non-fatal)", exc_info=True)

    word_count = count_words(content_md)
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id
                )
            )
        )
        or 0
    ) + 1

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
        is_current=True,
        llm_run_id=llm_run_id,
    )
    session.add(new_draft)
    await session.flush()
    rewrite_task.status = "completed"
    rewrite_task.attempts = int(rewrite_task.attempts or 0) + 1
    rewrite_task.metadata_json = {
        **(rewrite_task.metadata_json or {}),
        "rewritten_chapter_draft_id": str(new_draft.id),
        "generation_mode": generation_mode,
    }
    chapter.status = ChapterStatus.REVIEW.value
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
    if settings is not None:
        system_prompt, user_prompt = build_scene_rewrite_prompts(
            project,
            chapter,
            scene,
            current_draft,
            rewrite_task,
            style_guide,
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
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "rewrite_task_id": str(rewrite_task.id),
                },
            ),
        )
        content_md = sanitize_novel_markdown_content(completion.content) or fallback_content
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

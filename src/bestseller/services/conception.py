"""AI-driven novel conception pipeline.

Replaces manual WritingProfile customization with a multi-agent discussion flow:

Round 1 — Three specialist "agents" (market strategist, character architect,
          world builder) independently generate their sections.
Round 2 — A critic reviews all three proposals and produces suggestions.
Round 3 — An editor merges, revises, and finalizes the complete WritingProfile
          plus premise and title.

The result is a studio-quality WritingProfile generated purely from ``genre_key``
and ``chapter_count``, eliminating the gap between quickstart and studio paths.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, LLMRole, complete_text
from bestseller.services.writing_profile import resolve_writing_profile, sanitize_genre_story_overrides
from bestseller.services.writing_presets import list_genre_presets, list_platform_presets
from bestseller.settings import AppSettings

# Import GenreReviewProfile type for type hints; actual resolution is guarded.
from bestseller.services.genre_review_profiles import (
    GenreReviewProfile,
    resolve_genre_review_profile,
)
from bestseller.services.novel_categories import (
    render_category_anti_patterns,
    render_category_reader_promise,
    resolve_novel_category,
)

logger = logging.getLogger(__name__)

ProgressCallback = Any  # Callable[[str, dict | None], None]


@dataclass(frozen=True)
class ConceptionResult:
    """Output of the multi-agent conception pipeline."""

    writing_profile: dict[str, Any]
    premise: str
    title: str
    conception_log: list[dict[str, Any]]
    llm_run_ids: list[UUID]
    commercial_brief: dict[str, Any] = field(default_factory=dict)
    synopsis: str = ""
    tags: list[str] = field(default_factory=list)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from LLM output."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for opening, closing in (("{", "}"),):
        start = stripped.find(opening)
        end = stripped.rfind(closing)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                pass
    logger.warning(
        "Failed to extract JSON from LLM output (len=%d): %.200s...",
        len(text),
        text,
    )
    return {}


def _safe_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    val = data.get(key)
    return val if val is not None else default


async def _llm_call(
    session: AsyncSession,
    settings: AppSettings,
    *,
    role: LLMRole,
    system_prompt: str,
    user_prompt: str,
    fallback: str,
    template: str,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
) -> tuple[str, UUID | None]:
    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role=role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback,
            prompt_template=template,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
        ),
    )
    return result.content, result.llm_run_id


def _build_genre_context(
    genre_key: str,
    chapter_count: int,
    story_facets: object | None = None,
) -> dict[str, Any]:
    """Build context dict from genre preset for prompts.

    When story_facets is provided, enriches the context with multi-dimensional
    facet information for the conception agents.
    """
    presets = {p.key: p for p in list_genre_presets()}
    preset = presets.get(genre_key)
    if preset is None:
        raise ValueError(f"Unknown genre_key: {genre_key}")

    is_en = preset.language.startswith("en")
    platform_presets = {p.key: p for p in list_platform_presets()}
    recommended_platform = None
    if preset.recommended_platforms:
        priority = (
            ("Kindle Unlimited", "Royal Road", "Wattpad")
            if is_en
            else ("番茄小说", "起点中文网", "七猫小说", "晋江文学城")
        )
        for pkey in priority:
            if pkey in preset.recommended_platforms:
                recommended_platform = pkey
                break
        if recommended_platform is None:
            recommended_platform = preset.recommended_platforms[0]

    ctx: dict[str, Any] = {
        "genre_key": genre_key,
        "genre": preset.genre,
        "sub_genre": preset.sub_genre,
        "description": preset.description,
        "language": preset.language,
        "chapter_count": chapter_count,
        "recommended_platforms": preset.recommended_platforms,
        "recommended_audiences": preset.recommended_audiences,
        "trend_keywords": preset.trend_keywords,
        "trend_score": preset.trend_score,
        "trend_summary": preset.trend_summary,
        "default_platform": recommended_platform,
        "existing_overrides": sanitize_genre_story_overrides(preset.writing_profile_overrides),
    }

    # Enrich with StoryFacets if available
    if story_facets is not None:
        try:
            from bestseller.domain.facets import StoryFacets

            facets: StoryFacets | None = None
            if isinstance(story_facets, StoryFacets):
                facets = story_facets
            elif isinstance(story_facets, dict):
                facets = StoryFacets(**story_facets)

            if facets is not None:
                ctx["story_facets"] = {
                    "sub_genres": list(facets.sub_genres),
                    "setting": facets.setting,
                    "tone": facets.tone,
                    "power_system": facets.power_system,
                    "relationship_mode": facets.relationship_mode,
                    "narrative_drive": facets.narrative_drive,
                    "emotional_register": facets.emotional_register,
                    "trope_tags": list(facets.trope_tags),
                }
                # Override sub_genre with richer facet data
                if facets.sub_genres:
                    ctx["sub_genre"] = ", ".join(facets.sub_genres)
                # Add facet-driven description enhancement
                ctx["facet_description"] = (
                    f"Setting: {facets.setting}\n"
                    f"Tone: {facets.tone}\n"
                    f"Narrative Drive: {facets.narrative_drive}\n"
                    f"Relationship: {facets.relationship_mode}\n"
                    f"Tropes: {', '.join(facets.trope_tags)}"
                )
        except Exception:
            logger.debug("Failed to enrich genre context with story_facets", exc_info=True)

    return ctx


def _commercial_brief_prompt_block(ctx: dict[str, Any]) -> str:
    brief = ctx.get("commercial_brief")
    if not isinstance(brief, dict) or not brief:
        return ""
    label = "[Auto commercial positioning brief]" if str(ctx.get("language", "")).startswith("en") else "【自动商业化立项 brief】"
    return f"\n\n{label}\n{json.dumps(brief, ensure_ascii=False, indent=2)}\n"


def _normalize_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _build_commercial_fallback(ctx: dict[str, Any]) -> dict[str, Any]:
    is_en = str(ctx.get("language", "zh-CN")).startswith("en")
    existing_overrides = ctx.get("existing_overrides", {})
    market = existing_overrides.get("market", {}) if isinstance(existing_overrides, dict) else {}
    style = existing_overrides.get("style", {}) if isinstance(existing_overrides, dict) else {}
    target_audiences = _normalize_string_list(ctx.get("recommended_audiences"))[:3]
    trend_keywords = _normalize_string_list(ctx.get("trend_keywords"))[:4]
    benchmark_works = (
        [
            f"{ctx.get('sub_genre') or ctx.get('genre')}头部连载",
            f"{ctx.get('default_platform') or '目标平台'}同类爆款",
        ]
        if not is_en
        else [
            f"Top {ctx.get('sub_genre') or ctx.get('genre')} serial",
            f"Best-performing title on {ctx.get('default_platform') or 'the target platform'}",
        ]
    )
    return {
        "platform_target": market.get("platform_target") or ctx.get("default_platform"),
        "target_audiences": target_audiences,
        "benchmark_works": benchmark_works,
        "reader_promise": market.get("reader_promise") or (
            f"以{ctx.get('genre')}核心爽点提供稳定追读回报。"
            if not is_en else f"Deliver a dependable {ctx.get('genre')} page-turning payoff."
        ),
        "selling_points": _normalize_string_list(market.get("selling_points")) or trend_keywords[:3],
        "trope_keywords": _normalize_string_list(market.get("trope_keywords")) or trend_keywords[:3],
        "hook_keywords": _normalize_string_list(market.get("hook_keywords")) or trend_keywords[:2],
        "content_mode": market.get("content_mode") or (
            "中文网文长篇连载" if not is_en else "Commercial English web serial"
        ),
        "opening_strategy": market.get("opening_strategy") or (
            "开篇先亮出主角差异化优势、即时利益和明确危险。"
            if not is_en else "Reveal the protagonist edge, immediate upside, and visible danger in the opening."
        ),
        "chapter_hook_strategy": market.get("chapter_hook_strategy") or (
            "每章末尾都要留下更大的问题、威胁或利益诱因。"
            if not is_en else "End each chapter with a sharper question, threat, or temptation."
        ),
        "pacing_profile": market.get("pacing_profile") or "fast",
        "payoff_rhythm": market.get("payoff_rhythm") or (
            "短回报密集，长回报递延" if not is_en else "Dense short payoffs with delayed major reversals"
        ),
        "update_strategy": market.get("update_strategy") or (
            "日更连载" if not is_en else "Frequent serial updates"
        ),
        "taboo_topics": _normalize_string_list(style.get("taboo_topics")),
        "taboo_words": _normalize_string_list(style.get("taboo_words")),
        "commercial_rationale": (
            f"优先匹配 {ctx.get('default_platform')} 平台与 {', '.join(target_audiences) or '核心受众'} 的追读偏好。"
            if not is_en
            else f"Bias toward {ctx.get('default_platform')} and the retention pattern of {', '.join(target_audiences) or 'the core audience'}."
        ),
        "confidence": round(float(ctx.get("trend_score", 70)) / 100.0, 2),
        "assumptions": (
            ["按推荐平台的主流商业连载节奏组织前 30 章。"]
            if not is_en else ["Assume the first 30 chapters should follow the dominant retention pattern of the target platform."]
        ),
    }


def _apply_commercial_brief_to_profile(
    profile: dict[str, Any],
    brief: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(profile)
    market = dict(merged.get("market") or {})
    style = dict(merged.get("style") or {})

    for key in (
        "platform_target",
        "reader_promise",
        "content_mode",
        "opening_strategy",
        "chapter_hook_strategy",
        "pacing_profile",
        "payoff_rhythm",
        "update_strategy",
    ):
        value = brief.get(key)
        if value and not market.get(key):
            market[key] = value

    for key in ("selling_points", "trope_keywords", "hook_keywords"):
        existing = _normalize_string_list(market.get(key))
        incoming = _normalize_string_list(brief.get(key))
        market[key] = existing + [item for item in incoming if item not in existing]

    benchmark_works = _normalize_string_list(brief.get("benchmark_works"))
    taboo_topics = _normalize_string_list(brief.get("taboo_topics"))
    taboo_words = _normalize_string_list(brief.get("taboo_words"))
    style["reference_works"] = _normalize_string_list(style.get("reference_works")) + [
        item for item in benchmark_works if item not in _normalize_string_list(style.get("reference_works"))
    ]
    style["taboo_topics"] = _normalize_string_list(style.get("taboo_topics")) + [
        item for item in taboo_topics if item not in _normalize_string_list(style.get("taboo_topics"))
    ]
    style["taboo_words"] = _normalize_string_list(style.get("taboo_words")) + [
        item for item in taboo_words if item not in _normalize_string_list(style.get("taboo_words"))
    ]
    rationale = str(brief.get("commercial_rationale") or "").strip()
    if rationale:
        custom_rules = _normalize_string_list(style.get("custom_rules"))
        if rationale not in custom_rules:
            style["custom_rules"] = custom_rules + [rationale]

    merged["market"] = market
    merged["style"] = style
    return merged


_COMMERCIAL_POSITIONING_SYSTEM = (
    "你是一位商业化网文立项总监。你要在无人干预的前提下，为新小说自动完成平台定位、受众细分、"
    "对标作品、追读承诺、更新节奏和内容禁区设计。你的判断必须可执行、偏商业结果导向。"
    "输出必须是合法 JSON，不要解释。"
)

_COMMERCIAL_POSITIONING_SYSTEM_EN = (
    "You are a commercial fiction commissioning director. Autonomously decide the platform fit, audience segment, "
    "benchmark works, retention promise, release cadence, and content boundaries for a new novel. "
    "Be concrete, market-minded, and execution-ready. Output valid JSON only."
)


def _commercial_positioning_user_prompt(
    ctx: dict[str, Any],
    genre_profile: GenreReviewProfile | None = None,
) -> str:
    prompt = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"推荐平台：{', '.join(ctx['recommended_platforms'])}\n"
        f"推荐受众：{', '.join(ctx['recommended_audiences'])}\n"
        f"趋势关键词：{', '.join(ctx['trend_keywords'])}\n"
        f"趋势摘要：{ctx.get('trend_summary') or ''}\n"
        f"\n请自动完成商业化立项，输出 JSON：\n"
        "{\n"
        '  "platform_target": "最优平台",\n'
        '  "target_audiences": ["核心受众1", "核心受众2"],\n'
        '  "benchmark_works": ["对标作品1", "对标作品2"],\n'
        '  "reader_promise": "一句话追读承诺",\n'
        '  "selling_points": ["卖点1", "卖点2", "卖点3"],\n'
        '  "trope_keywords": ["题材标签1", "题材标签2"],\n'
        '  "hook_keywords": ["钩子词1", "钩子词2"],\n'
        '  "content_mode": "内容模式",\n'
        '  "opening_strategy": "开篇抓手",\n'
        '  "chapter_hook_strategy": "章末钩子策略",\n'
        '  "pacing_profile": "fast/medium/slow",\n'
        '  "payoff_rhythm": "回报节奏",\n'
        '  "update_strategy": "更新节奏",\n'
        '  "taboo_topics": ["禁区1"],\n'
        '  "taboo_words": ["禁词1"],\n'
        '  "commercial_rationale": "为什么这样定位最适合商业化",\n'
        '  "confidence": 0.0,\n'
        '  "assumptions": ["关键假设1"]\n'
        "}"
    )
    if genre_profile:
        instruction = genre_profile.planner_prompts.book_spec_instruction_zh
        if instruction:
            prompt += f"\n\n【品类商业定位要求】\n{instruction}"
    return prompt


def _commercial_positioning_user_prompt_en(
    ctx: dict[str, Any],
    genre_profile: GenreReviewProfile | None = None,
) -> str:
    prompt = (
        f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
        f"Description: {ctx['description']}\n"
        f"Target chapters: {ctx['chapter_count']}\n"
        f"Recommended platforms: {', '.join(ctx['recommended_platforms'])}\n"
        f"Target audiences: {', '.join(ctx['recommended_audiences'])}\n"
        f"Trend keywords: {', '.join(ctx['trend_keywords'])}\n"
        f"Trend summary: {ctx.get('trend_summary') or ''}\n"
        f"\nGenerate an autonomous commercial positioning JSON:\n"
        "{\n"
        '  "platform_target": "best-fit platform",\n'
        '  "target_audiences": ["audience 1", "audience 2"],\n'
        '  "benchmark_works": ["benchmark 1", "benchmark 2"],\n'
        '  "reader_promise": "one-line retention promise",\n'
        '  "selling_points": ["point1", "point2", "point3"],\n'
        '  "trope_keywords": ["trope1", "trope2"],\n'
        '  "hook_keywords": ["hook1", "hook2"],\n'
        '  "content_mode": "content mode",\n'
        '  "opening_strategy": "opening hook plan",\n'
        '  "chapter_hook_strategy": "chapter-ending hook plan",\n'
        '  "pacing_profile": "fast/medium/slow",\n'
        '  "payoff_rhythm": "payoff rhythm",\n'
        '  "update_strategy": "release cadence",\n'
        '  "taboo_topics": ["boundary 1"],\n'
        '  "taboo_words": ["word 1"],\n'
        '  "commercial_rationale": "why this positioning is commercially strong",\n'
        '  "confidence": 0.0,\n'
        '  "assumptions": ["assumption 1"]\n'
        "}"
    )
    if genre_profile:
        instruction = genre_profile.planner_prompts.book_spec_instruction_en
        if instruction:
            prompt += f"\n\n[Genre commercial requirements]\n{instruction}"
    return prompt


# ─────────────────────────────────────────────────────────────────────
# Round 1: Independent proposals from three specialist perspectives
# ─────────────────────────────────────────────────────────────────────

_MARKET_SYSTEM = (
    "你是一位资深网文市场策略师，精通各大网文平台（番茄小说、起点中文网、七猫小说、晋江文学城等）的读者偏好、"
    "留存机制和爆款规律。你的任务是为一部新小说制定精准的市场定位策略。"
    "输出必须是合法 JSON，不要解释。"
)

_CHARACTER_SYSTEM = (
    "你是一位专业的小说角色架构师，擅长设计能让读者深度代入的主角、令人印象深刻的反派、"
    "以及功能明确的配角体系。你特别擅长中文网文的角色命名——名字要朗朗上口、符合题材背景、"
    "避免生僻字和不雅谐音，主角名要有记忆点。"
    "输出必须是合法 JSON，不要解释。"
)

_WORLD_SYSTEM = (
    "你是一位小说世界观构建师，擅长设计自洽的世界体系、力量系统和地理结构。"
    "你设计的世界必须服务于剧情冲突和爽点制造，而非空洞的百科全书。"
    "输出必须是合法 JSON，不要解释。"
)


def _market_user_prompt(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    prompt = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"推荐平台：{', '.join(ctx['recommended_platforms'])}\n"
        f"推荐受众：{', '.join(ctx['recommended_audiences'])}\n"
        f"趋势关键词：{', '.join(ctx['trend_keywords'])}\n"
        f"趋势评分：{ctx['trend_score']}/100\n"
        f"\n请生成 market 定位 JSON，包含：\n"
        f'{{"platform_target": "最适合的平台",\n'
        f'  "reader_promise": "给读者的核心承诺（一句话）",\n'
        f'  "selling_points": ["卖点1", "卖点2", "卖点3", "卖点4"],\n'
        f'  "trope_keywords": ["标签1", "标签2", "标签3"],\n'
        f'  "hook_keywords": ["钩子词1", "钩子词2"],\n'
        f'  "opening_strategy": "开篇策略描述",\n'
        f'  "chapter_hook_strategy": "章末钩子策略",\n'
        f'  "pacing_profile": "fast/medium/slow",\n'
        f'  "payoff_rhythm": "回报节奏描述",\n'
        f'  "content_mode": "内容模式描述"\n'
        f"}}"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        instruction = genre_profile.planner_prompts.book_spec_instruction_zh
        if instruction:
            prompt += f"\n\n【品类市场策略要求】\n{instruction}"
    return prompt


def _character_user_prompt(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    prompt = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n请设计角色体系 JSON，包含：\n"
        f'{{"protagonist_archetype": "主角原型（如：重生复仇者、天才少年、隐忍谋略家）",\n'
        f'  "protagonist_name": "为主角取一个自然、好记、符合题材背景的中文名（2-3字）",\n'
        f'  "protagonist_name_reasoning": "命名理由",\n'
        f'  "protagonist_core_drive": "主角核心驱动力",\n'
        f'  "golden_finger": "主角金手指/差异化优势",\n'
        f'  "growth_curve": "成长曲线描述",\n'
        f'  "romance_mode": "感情线模式（none/slow-burn/harem/single等）",\n'
        f'  "relationship_tension": "核心关系张力",\n'
        f'  "antagonist_mode": "反派模式（escalating/rotating/hidden等）",\n'
        f'  "conflict_forces": [\n'
        f'    {{"name": "冲突力量名称",\n'
        f'     "force_type": "character/faction/environment/internal/systemic",\n'
        f'     "active_volumes": [1, 2],\n'
        f'     "threat_description": "这个力量对主角构成什么样的威胁",\n'
        f'     "relationship_to_protagonist": "与主角的关系",\n'
        f'     "escalation_path": "威胁如何升级和演变"}}\n'
        f'  ],\n'
        f'  "key_characters": [\n'
        f'    {{"name": "角色名", "role": "protagonist/antagonist/ally/mentor",\n'
        f'     "name_reasoning": "命名理由",\n'
        f'     "personality_keywords": ["关键词1", "关键词2"],\n'
        f'     "relationship_to_protagonist": "与主角的关系"}}\n'
        f'  ]\n'
        f"}}\n"
        f"\n【冲突力量设计要求】\n"
        f"故事的精彩在于主角在不同阶段面临不同类型的挑战：\n"
        f"- 每个阶段（卷）应该有不同的主要冲突力量\n"
        f"- 不要全书只有一个反派持续施压——要有生存威胁、权力博弈、信任危机、多方对抗等不同类型\n"
        f"- 冲突力量可以是角色（character）、势力（faction）、环境（environment）、内心（internal）、体制（systemic）\n"
        f"- 每个冲突力量标注在哪几卷是主要威胁（active_volumes）\n"
        f"- 确保有明线冲突也有暗线伏笔\n"
        f"\n角色命名要求：\n"
        f"1. 根据题材选择合适的姓名风格（古风仙侠用古典名、都市用现代名、末日科幻可用普通名）\n"
        f"2. 主角名 2-3 字，音调优美，避免拗口\n"
        f"3. 配角和反派姓氏不与主角重复\n"
        f"4. 避免谐音不雅或过于常见的网文烂大街名字\n"
        f"5. 每个名字附命名理由"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        instruction = genre_profile.planner_prompts.cast_spec_instruction_zh
        if instruction:
            prompt += f"\n\n【品类角色设计要求】\n{instruction}"
    return prompt


def _world_user_prompt(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    prompt = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n请设计世界观 JSON，包含：\n"
        f'{{"worldbuilding_density": "low/medium/high",\n'
        f'  "info_reveal_strategy": "信息揭示策略",\n'
        f'  "rule_hardness": "soft/medium/hard",\n'
        f'  "power_system_style": "力量体系风格描述",\n'
        f'  "mystery_density": "low/medium/high",\n'
        f'  "world_era": "世界时代背景（古代/现代/未来/架空）",\n'
        f'  "core_conflict_source": "世界核心冲突来源",\n'
        f'  "escalation_mechanism": "势力/力量升级机制"\n'
        f"}}"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        instruction = genre_profile.planner_prompts.world_spec_instruction_zh
        if instruction:
            prompt += f"\n\n【品类世界构建要求】\n{instruction}"
    return prompt


# ─────────────────────────────────────────────────────────────────────
# English prompt variants
# ─────────────────────────────────────────────────────────────────────

_MARKET_SYSTEM_EN = (
    "You are a senior commercial fiction market strategist, expert in Kindle Unlimited page-read economics, "
    "Royal Road serial dynamics, Wattpad engagement, and indie publishing trends. "
    "Your task is to craft a precise market positioning strategy for a new novel. "
    "Output must be valid JSON only, no explanations."
)

_CHARACTER_SYSTEM_EN = (
    "You are a professional fiction character architect. You design compelling protagonists readers "
    "can't put down, memorable antagonists, and a functional supporting cast. You are skilled at "
    "naming characters naturally for English-language commercial fiction — names should be memorable, "
    "genre-appropriate, and easy to pronounce. "
    "Output must be valid JSON only, no explanations."
)

_WORLD_SYSTEM_EN = (
    "You are a world-building specialist for commercial fiction. You design self-consistent world systems, "
    "magic/power frameworks, and settings that serve conflict and reader satisfaction — not empty encyclopedias. "
    "Output must be valid JSON only, no explanations."
)


def _market_user_prompt_en(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    prompt = (
        f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
        f"Description: {ctx['description']}\n"
        f"Target chapters: {ctx['chapter_count']}\n"
        f"Recommended platforms: {', '.join(ctx['recommended_platforms'])}\n"
        f"Target audiences: {', '.join(ctx['recommended_audiences'])}\n"
        f"Trend keywords: {', '.join(ctx['trend_keywords'])}\n"
        f"Trend score: {ctx['trend_score']}/100\n"
        f"\nGenerate a market positioning JSON:\n"
        f'{{"platform_target": "best-fit platform",\n'
        f'  "reader_promise": "core promise to readers (one sentence)",\n'
        f'  "selling_points": ["point1", "point2", "point3", "point4"],\n'
        f'  "trope_keywords": ["trope1", "trope2", "trope3"],\n'
        f'  "hook_keywords": ["hook1", "hook2"],\n'
        f'  "opening_strategy": "opening strategy description",\n'
        f'  "chapter_hook_strategy": "chapter-ending hook strategy",\n'
        f'  "pacing_profile": "fast/medium/slow",\n'
        f'  "payoff_rhythm": "payoff rhythm description",\n'
        f'  "content_mode": "content mode description"\n'
        f"}}"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        instruction = genre_profile.planner_prompts.book_spec_instruction_en
        if instruction:
            prompt += f"\n\n[Genre market strategy requirements]\n{instruction}"
    return prompt


def _character_user_prompt_en(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    prompt = (
        f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
        f"Description: {ctx['description']}\n"
        f"Target chapters: {ctx['chapter_count']}\n"
        f"\nDesign a character system JSON:\n"
        f'{{"protagonist_archetype": "archetype (e.g., reluctant hero, cunning survivor, morally gray anti-hero)",\n'
        f'  "protagonist_name": "a natural, memorable English name that fits the genre",\n'
        f'  "protagonist_name_reasoning": "why this name fits",\n'
        f'  "protagonist_core_drive": "core motivation",\n'
        f'  "golden_finger": "protagonist\'s unique advantage or ability",\n'
        f'  "growth_curve": "character growth arc description",\n'
        f'  "romance_mode": "none/slow-burn/love-triangle/harem/single etc.",\n'
        f'  "relationship_tension": "core relationship tension",\n'
        f'  "antagonist_mode": "escalating/rotating/hidden etc.",\n'
        f'  "conflict_forces": [\n'
        f'    {{"name": "conflict force name",\n'
        f'     "force_type": "character/faction/environment/internal/systemic",\n'
        f'     "active_volumes": [1, 2],\n'
        f'     "threat_description": "what threat this force poses to the protagonist",\n'
        f'     "relationship_to_protagonist": "relationship to protagonist",\n'
        f'     "escalation_path": "how the threat evolves and escalates"}}\n'
        f'  ],\n'
        f'  "key_characters": [\n'
        f'    {{"name": "character name", "role": "protagonist/antagonist/ally/mentor",\n'
        f'     "name_reasoning": "why this name",\n'
        f'     "personality_keywords": ["keyword1", "keyword2"],\n'
        f'     "relationship_to_protagonist": "relationship description"}}\n'
        f'  ]\n'
        f"}}\n"
        f"\nConflict forces design requirements:\n"
        f"A great story evolves as the protagonist grows — each phase should present different challenges:\n"
        f"- Each volume should have a different primary conflict force\n"
        f"- Don't rely on a single antagonist pressuring throughout — vary between survival threats, political intrigue, betrayal, faction warfare, etc.\n"
        f"- Forces can be characters, factions, environments, internal struggles, or systemic pressures\n"
        f"- Tag each force with active_volumes showing when it's the primary threat\n"
        f"- Include both visible plotlines and hidden threads\n"
        f"\nNaming guidelines:\n"
        f"1. Choose names that fit the genre setting (fantasy names for epic fantasy, modern names for contemporary, etc.)\n"
        f"2. Protagonist name should be distinctive and memorable\n"
        f"3. Avoid name confusion — supporting characters should have distinct first letters/sounds\n"
        f"4. Each name should have a brief reasoning"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        instruction = genre_profile.planner_prompts.cast_spec_instruction_en
        if instruction:
            prompt += f"\n\n[Genre character design requirements]\n{instruction}"
    return prompt


def _world_user_prompt_en(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    prompt = (
        f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
        f"Description: {ctx['description']}\n"
        f"Target chapters: {ctx['chapter_count']}\n"
        f"\nDesign a world-building JSON:\n"
        f'{{"worldbuilding_density": "low/medium/high",\n'
        f'  "info_reveal_strategy": "information reveal strategy",\n'
        f'  "rule_hardness": "soft/medium/hard",\n'
        f'  "power_system_style": "power/magic system description",\n'
        f'  "mystery_density": "low/medium/high",\n'
        f'  "world_era": "setting era (medieval/modern/futuristic/secondary world)",\n'
        f'  "core_conflict_source": "world-level core conflict source",\n'
        f'  "escalation_mechanism": "how power/stakes escalate"\n'
        f"}}"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        instruction = genre_profile.planner_prompts.world_spec_instruction_en
        if instruction:
            prompt += f"\n\n[Genre world-building requirements]\n{instruction}"
    return prompt


# ─────────────────────────────────────────────────────────────────────
# Round 2: Cross-review
# ─────────────────────────────────────────────────────────────────────

_REVIEW_SYSTEM = (
    "你是一位资深的小说总编辑，擅长从整体视角审查市场定位、角色体系和世界观之间的配合度。"
    "你需要找出三份提案之间的矛盾、空白和可优化之处。"
    "输出必须是合法 JSON，不要解释。"
)


def _build_rubric_checklist_zh(genre_profile: GenreReviewProfile) -> list[str]:
    """Build a Chinese genre-specific review checklist from the plan rubric."""
    items: list[str] = []
    rubric = genre_profile.plan_rubric
    if rubric.require_power_system_tiers:
        items.append("检查角色设计中是否定义了力量等级体系和升级路径")
    if rubric.require_relationship_milestones:
        items.append("检查是否有明确的关系里程碑路线图和情感引擎设计")
    if rubric.require_clue_chain:
        items.append("检查是否有线索分层分布计划和误导策略")
    if rubric.min_antagonist_forces > 1:
        items.append(f"检查是否有至少{rubric.min_antagonist_forces}种不同类型的冲突力量")
    if rubric.require_theme_per_volume:
        items.append("检查每卷是否有独立主题定义")
    if rubric.require_foreshadowing:
        items.append("检查是否有伏笔和前后呼应的设计")
    for check in rubric.required_checks:
        items.append(check)
    return items


def _build_rubric_checklist_en(genre_profile: GenreReviewProfile) -> list[str]:
    """Build an English genre-specific review checklist from the plan rubric."""
    items: list[str] = []
    rubric = genre_profile.plan_rubric
    if rubric.require_power_system_tiers:
        items.append("Verify the character design defines a power tier system and progression path")
    if rubric.require_relationship_milestones:
        items.append("Verify there is a clear relationship milestone roadmap and emotional engine design")
    if rubric.require_clue_chain:
        items.append("Verify there is a layered clue distribution plan and misdirection strategy")
    if rubric.min_antagonist_forces > 1:
        items.append(f"Verify there are at least {rubric.min_antagonist_forces} distinct conflict force types")
    if rubric.require_theme_per_volume:
        items.append("Verify each volume has a distinct thematic focus")
    if rubric.require_foreshadowing:
        items.append("Verify foreshadowing and callback design is present")
    for check in rubric.required_checks:
        items.append(check)
    return items


def _review_user_prompt(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
    genre_profile: GenreReviewProfile | None = None,
) -> str:
    prompt = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n## 市场定位提案\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## 角色体系提案\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## 世界观提案\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n请审查以上三份提案，输出 JSON：\n"
        f'{{"overall_coherence_score": 0.0-1.0,\n'
        f'  "contradictions": ["矛盾1", "矛盾2"],\n'
        f'  "gaps": ["空白1", "空白2"],\n'
        f'  "market_suggestions": ["建议1"],\n'
        f'  "character_suggestions": ["建议1"],\n'
        f'  "world_suggestions": ["建议1"],\n'
        f'  "name_quality_issues": ["名字问题1（如有）"],\n'
        f'  "conflict_force_review": "conflict_forces是否提供了真正不同类型的挑战？各阶段冲突是否有明显差异化？是否有明线与暗线的交织？",\n'
        f'  "premise_seeds": ["可作为premise种子的核心冲突点1", "种子2"]\n'
        f"}}"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        checklist = _build_rubric_checklist_zh(genre_profile)
        if checklist:
            items_text = "\n".join(f"- {item}" for item in checklist)
            prompt += f"\n\n【品类审查清单】\n请在审查中额外关注以下要点：\n{items_text}"
        review_instruction = genre_profile.judge_prompts.scene_review_instruction_zh
        if review_instruction:
            prompt += f"\n\n【品类审查重点】\n{review_instruction}"
    return prompt


_REVIEW_SYSTEM_EN = (
    "You are a senior developmental editor, skilled at evaluating the coherence between market positioning, "
    "character design, and world-building. Find contradictions, gaps, and optimization opportunities "
    "across the three proposals. Output must be valid JSON only, no explanations."
)


def _review_user_prompt_en(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
    genre_profile: GenreReviewProfile | None = None,
) -> str:
    prompt = (
        f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
        f"Target chapters: {ctx['chapter_count']}\n"
        f"\n## Market Positioning Proposal\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## Character System Proposal\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## World-Building Proposal\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\nReview the above three proposals and output JSON:\n"
        f'{{"overall_coherence_score": 0.0-1.0,\n'
        f'  "contradictions": ["contradiction1", "contradiction2"],\n'
        f'  "gaps": ["gap1", "gap2"],\n'
        f'  "market_suggestions": ["suggestion1"],\n'
        f'  "character_suggestions": ["suggestion1"],\n'
        f'  "world_suggestions": ["suggestion1"],\n'
        f'  "name_quality_issues": ["name issue1 (if any)"],\n'
        f'  "conflict_force_review": "Do conflict_forces provide genuinely different challenge types across volumes? Is there proper visible/hidden plotline interweaving?",\n'
        f'  "premise_seeds": ["core conflict seed1", "seed2"]\n'
        f"}}"
    )
    prompt += _commercial_brief_prompt_block(ctx)
    if genre_profile:
        checklist = _build_rubric_checklist_en(genre_profile)
        if checklist:
            items_text = "\n".join(f"- {item}" for item in checklist)
            prompt += f"\n\n[Genre review checklist]\nPay special attention to the following during review:\n{items_text}"
        review_instruction = genre_profile.judge_prompts.scene_review_instruction_en
        if review_instruction:
            prompt += f"\n\n[Genre review focus]\n{review_instruction}"
    return prompt


# ─────────────────────────────────────────────────────────────────────
# Round 3: Merge & finalize
# ─────────────────────────────────────────────────────────────────────

_FINALIZE_SYSTEM = (
    "你是一位小说项目总策划，负责将市场定位、角色体系、世界观的讨论成果整合为最终方案。"
    "你需要产出完整的 WritingProfile、一段精炼的 premise、一个有设计感的书名、"
    "一段宣传用作品简介（synopsis）和作品标签（tags）。"
    "输出必须是合法 JSON，不要解释。"
)


def _finalize_user_prompt(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
    review: dict[str, Any],
    genre_profile: GenreReviewProfile | None = None,
) -> str:
    base = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n## 市场定位提案\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## 角色体系提案\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## 世界观提案\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n## 审查意见\n{json.dumps(review, ensure_ascii=False, indent=2)}\n"
        f"\n请根据以上讨论成果，生成最终方案 JSON：\n"
        f'{{\n'
        f'  "title": "小说书名（必须2-8个汉字。要求有设计感，让读者看到书名就想点进去。'
        f'好的书名应该：①暗示核心冲突或世界观（如「遮天」暗示逆天改命）；'
        f'②制造悬念或反差（如「我师兄实在太稳健了」）；'
        f'③有画面感或意象（如「雪中悍刀行」）；'
        f'④避免直白描述题材（如「都市修仙记」这种流水线书名）。'
        f'禁止使用描述性长句，禁止直接用题材名当书名）",\n'
        f'  "premise": "小说前提/核心设定（100-200字，包含主角、核心冲突、金手指和悬念）",\n'
        f'  "synopsis": "作品宣传简介（200-500字，面向读者的营销文案。要求：'
        f'①开头一句话勾住读者好奇心；②介绍主角身份和核心困境；'
        f'③展示世界观最吸引人的设定；④留下悬念，不剧透关键反转。'
        f'风格参考起点/番茄热门作品简介，有感染力，让人想追更）",\n'
        f'  "tags": ["标签1", "标签2", "...（5-10个作品标签，包括题材、风格、元素、受众标签）"],\n'
        f'  "writing_profile": {{\n'
        f'    "market": {{\n'
        f'      "platform_target": "...", "reader_promise": "...",\n'
        f'      "selling_points": [...], "trope_keywords": [...],\n'
        f'      "hook_keywords": [...], "opening_strategy": "...",\n'
        f'      "chapter_hook_strategy": "...", "pacing_profile": "...",\n'
        f'      "payoff_rhythm": "..."\n'
        f'    }},\n'
        f'    "character": {{\n'
        f'      "protagonist_archetype": "...", "protagonist_core_drive": "...",\n'
        f'      "golden_finger": "...", "growth_curve": "...",\n'
        f'      "romance_mode": "...", "relationship_tension": "...",\n'
        f'      "antagonist_mode": "...",\n'
        f'      "conflict_forces": [{{"name": "...", "force_type": "...", "active_volumes": [...], "threat_description": "...", "escalation_path": "..."}}]\n'
        f'    }},\n'
        f'    "world": {{\n'
        f'      "worldbuilding_density": "...", "info_reveal_strategy": "...",\n'
        f'      "rule_hardness": "...", "power_system_style": "...",\n'
        f'      "mystery_density": "..."\n'
        f'    }},\n'
        f'    "style": {{\n'
        f'      "pov_type": "first/third-limited/third-omniscient",\n'
        f'      "prose_style": "commercial-web-serial/literary/...",\n'
        f'      "sentence_style": "short/mixed/...",\n'
        f'      "dialogue_ratio": 0.30,\n'
        f'      "tone_keywords": ["关键词1", "关键词2"]\n'
        f'    }},\n'
        f'    "serialization": {{\n'
        f'      "opening_mandate": "开篇要求",\n'
        f'      "first_three_chapter_goal": "前三章目标",\n'
        f'      "scene_drive_rule": "场景驱动规则",\n'
        f'      "chapter_ending_rule": "章末规则",\n'
        f'      "free_chapter_strategy": "免费章策略"\n'
        f'    }}\n'
        f'  }}\n'
        f"}}"
    )
    if genre_profile:
        instruction = genre_profile.planner_prompts.book_spec_instruction_zh
        if instruction:
            base += f"\n\n【品类最终质量要求】\n{instruction}"
    base += _commercial_brief_prompt_block(ctx)
    # Inject category anti-patterns and reader promise
    cat = resolve_novel_category(ctx.get("genre", ""), ctx.get("sub_genre"))
    promise = render_category_reader_promise(cat, is_en=False)
    anti = render_category_anti_patterns(cat, is_en=False)
    if promise:
        base += f"\n\n{promise}"
    if anti:
        base += f"\n\n{anti}"
    return base


_FINALIZE_SYSTEM_EN = (
    "You are a fiction project director responsible for merging market positioning, character design, "
    "and world-building proposals into a final plan. You must produce a complete WritingProfile, "
    "a compelling premise, an attention-grabbing title, a promotional synopsis, and genre tags. "
    "Output must be valid JSON only, no explanations."
)


def _finalize_user_prompt_en(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
    review: dict[str, Any],
    genre_profile: GenreReviewProfile | None = None,
) -> str:
    base = (
        f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
        f"Target chapters: {ctx['chapter_count']}\n"
        f"\n## Market Positioning Proposal\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## Character System Proposal\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## World-Building Proposal\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n## Review Feedback\n{json.dumps(review, ensure_ascii=False, indent=2)}\n"
        f"\nBased on the above discussion, generate the final plan JSON:\n"
        f'{{\n'
        f'  "title": "Novel title (2-6 words ONLY. Must feel designed and evocative — '
        f'a title readers WANT to click. Great titles: ①hint at the core conflict or world '
        f'(e.g. The Name of the Wind, A Court of Thorns and Roses); '
        f'②create intrigue or contrast (e.g. The Girl with the Dragon Tattoo); '
        f'③have vivid imagery (e.g. Blood Meridian, The Shadow of the Wind). '
        f'Avoid generic genre labels like The Fantasy Quest or Urban Cultivation Story. '
        f'Must NOT be a sentence or description)",\n'
        f'  "premise": "Novel premise (50-150 words: protagonist, core conflict, unique hook, and central mystery)",\n'
        f'  "synopsis": "Promotional book blurb (100-300 words, reader-facing marketing copy. '
        f'Requirements: ①Open with a hook sentence that sparks curiosity; '
        f'②Introduce the protagonist and their core dilemma; '
        f'③Showcase the most compelling world-building elements; '
        f'④End with a cliffhanger question — no major spoilers. '
        f'Style: compelling back-cover copy that makes readers want to buy)",\n'
        f'  "tags": ["tag1", "tag2", "...(5-10 tags: genre, style, tropes, audience)"],\n'
        f'  "writing_profile": {{\n'
        f'    "market": {{\n'
        f'      "platform_target": "...", "reader_promise": "...",\n'
        f'      "selling_points": [...], "trope_keywords": [...],\n'
        f'      "hook_keywords": [...], "opening_strategy": "...",\n'
        f'      "chapter_hook_strategy": "...", "pacing_profile": "...",\n'
        f'      "payoff_rhythm": "..."\n'
        f'    }},\n'
        f'    "character": {{\n'
        f'      "protagonist_archetype": "...", "protagonist_core_drive": "...",\n'
        f'      "golden_finger": "...", "growth_curve": "...",\n'
        f'      "romance_mode": "...", "relationship_tension": "...",\n'
        f'      "antagonist_mode": "...",\n'
        f'      "conflict_forces": [{{"name": "...", "force_type": "...", "active_volumes": [...], "threat_description": "...", "escalation_path": "..."}}]\n'
        f'    }},\n'
        f'    "world": {{\n'
        f'      "worldbuilding_density": "...", "info_reveal_strategy": "...",\n'
        f'      "rule_hardness": "...", "power_system_style": "...",\n'
        f'      "mystery_density": "..."\n'
        f'    }},\n'
        f'    "style": {{\n'
        f'      "pov_type": "first/third-limited/third-omniscient",\n'
        f'      "prose_style": "commercial-genre/literary/serial-web-fiction/...",\n'
        f'      "sentence_style": "short/mixed/...",\n'
        f'      "dialogue_ratio": 0.35,\n'
        f'      "tone_keywords": ["keyword1", "keyword2"]\n'
        f'    }},\n'
        f'    "serialization": {{\n'
        f'      "opening_mandate": "opening requirements",\n'
        f'      "first_three_chapter_goal": "first three chapters goal",\n'
        f'      "scene_drive_rule": "scene drive rule",\n'
        f'      "chapter_ending_rule": "chapter ending rule",\n'
        f'      "free_chapter_strategy": "sample/Look Inside strategy"\n'
        f'    }}\n'
        f'  }}\n'
        f"}}"
    )
    if genre_profile:
        instruction = genre_profile.planner_prompts.book_spec_instruction_en
        if instruction:
            base += f"\n\n[Genre final quality requirements]\n{instruction}"
    base += _commercial_brief_prompt_block(ctx)
    # Inject category anti-patterns and reader promise
    cat = resolve_novel_category(ctx.get("genre", ""), ctx.get("sub_genre"))
    promise = render_category_reader_promise(cat, is_en=True)
    anti = render_category_anti_patterns(cat, is_en=True)
    if promise:
        base += f"\n\n{promise}"
    if anti:
        base += f"\n\n{anti}"
    return base


# ─────────────────────────────────────────────────────────────────────
# Creative exploration (anti-cliché step)
# ─────────────────────────────────────────────────────────────────────

_CREATIVE_EXPLORATION_SYSTEM = (
    "你是一位专注于差异化创意的小说策划师。"
    "你的任务是基于当前的市场/角色/世界设定提案，"
    "提出3个有差异化的创意方向，每个方向都必须避开品类常见陷阱。"
    "输出必须是合法 JSON。"
)

_CREATIVE_EXPLORATION_SYSTEM_EN = (
    "You are a differentiation-focused fiction planner. "
    "Based on current market/character/world proposals, "
    "propose 3 differentiated creative directions, each avoiding common category traps. "
    "Output must be valid JSON only."
)


async def _creative_exploration(
    session: AsyncSession,
    settings: AppSettings,
    *,
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
    review: dict[str, Any],
    category: Any,  # NovelCategoryResearch
    is_en: bool,
) -> tuple[str, UUID | None]:
    """Generate 3 creative directions and choose the most differentiated one."""
    anti = render_category_anti_patterns(category, is_en=is_en)
    promise = render_category_reader_promise(category, is_en=is_en)

    if is_en:
        user_prompt = (
            f"Genre: {ctx['genre']} ({ctx['sub_genre']})\n"
            f"Target chapters: {ctx['chapter_count']}\n\n"
            f"## Current Proposals\n"
            f"Market: {json.dumps(market, ensure_ascii=False)[:500]}\n"
            f"Character: {json.dumps(character, ensure_ascii=False)[:500]}\n"
            f"World: {json.dumps(world, ensure_ascii=False)[:500]}\n"
            f"Review feedback: {json.dumps(review, ensure_ascii=False)[:500]}\n\n"
            f"{promise}\n\n{anti}\n\n"
            "Generate 3 creative directions JSON:\n"
            '{"directions": [\n'
            '  {"premise_variation": "...", "unique_hook": "...", "avoids_traps": ["trap_key_1"]},\n'
            '  ...\n'
            '],\n'
            '"chosen_direction": {"premise_variation": "...", "unique_hook": "...", "reason": "..."}}'
        )
    else:
        user_prompt = (
            f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
            f"目标章节数：{ctx['chapter_count']}章\n\n"
            f"## 当前提案\n"
            f"市场定位：{json.dumps(market, ensure_ascii=False)[:500]}\n"
            f"角色体系：{json.dumps(character, ensure_ascii=False)[:500]}\n"
            f"世界观：{json.dumps(world, ensure_ascii=False)[:500]}\n"
            f"审查意见：{json.dumps(review, ensure_ascii=False)[:500]}\n\n"
            f"{promise}\n\n{anti}\n\n"
            "请生成3个差异化创意方向 JSON：\n"
            '{"directions": [\n'
            '  {"premise_variation": "前提变体描述", "unique_hook": "独特卖点", "avoids_traps": ["trap_key"]},\n'
            '  ...\n'
            '],\n'
            '"chosen_direction": {"premise_variation": "最终选择", "unique_hook": "差异化卖点", "reason": "选择理由"}}'
        )

    return await _llm_call(
        session, settings,
        role="planner",
        system_prompt=_CREATIVE_EXPLORATION_SYSTEM_EN if is_en else _CREATIVE_EXPLORATION_SYSTEM,
        user_prompt=user_prompt,
        fallback='{"directions": [], "chosen_direction": {}}',
        template="conception_creative_exploration",
    )


# ─────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────


async def run_conception_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    genre_key: str,
    chapter_count: int,
    user_hints: dict[str, Any] | None = None,
    story_facets: object | None = None,
    progress: ProgressCallback | None = None,
) -> ConceptionResult:
    """Multi-agent discussion to auto-generate a complete WritingProfile.

    Three rounds:
    1. Independent proposals from market/character/world specialists
    2. Cross-review by a critic
    3. Merge & finalize by an editor

    When story_facets is provided, the conception agents receive enriched
    multi-dimensional context instead of flat genre descriptions.

    Returns a ConceptionResult with the complete writing_profile, premise, and title.
    """
    ctx = _build_genre_context(genre_key, chapter_count, story_facets=story_facets)
    if user_hints:
        ctx["user_hints"] = user_hints

    is_en = ctx.get("language", "zh-CN").startswith("en")

    # Resolve genre-specific review profile for prompt injection.
    _genre_profile: GenreReviewProfile | None = None
    try:
        _genre_profile = resolve_genre_review_profile(
            genre=ctx.get("genre", ""),
            sub_genre=ctx.get("sub_genre"),
            genre_preset_key=genre_key,
        )
    except Exception:
        logger.debug(
            "Genre review profile resolution failed for genre_key=%s; "
            "proceeding without genre-specific prompt injection.",
            genre_key,
            exc_info=True,
        )

    llm_run_ids: list[UUID] = []
    conception_log: list[dict[str, Any]] = []

    def _emit(stage: str, data: dict[str, Any] | None = None) -> None:
        if progress is not None:
            progress(stage, data)

    def _track_id(llm_id: UUID | None) -> None:
        if llm_id is not None:
            llm_run_ids.append(llm_id)

    # ── Round 0: Autonomous Commercial Positioning ───────────────────
    _emit("conception_commercial_positioning", {"round": 0, "agent": "commercial_commissioner"})
    commercial_text, commercial_llm_id = await _llm_call(
        session,
        settings,
        role="planner",
        system_prompt=_COMMERCIAL_POSITIONING_SYSTEM_EN if is_en else _COMMERCIAL_POSITIONING_SYSTEM,
        user_prompt=(
            _commercial_positioning_user_prompt_en if is_en else _commercial_positioning_user_prompt
        )(ctx, _genre_profile),
        fallback=json.dumps(_build_commercial_fallback(ctx), ensure_ascii=False),
        template="conception_commercial_positioning",
    )
    _track_id(commercial_llm_id)
    commercial_brief = _extract_json(commercial_text) or _build_commercial_fallback(ctx)
    ctx["commercial_brief"] = commercial_brief
    conception_log.append({"round": 0, "agent": "commercial_commissioner", "brief": commercial_brief})

    # ── Round 1: Independent Proposals ──────────────────────────────
    _emit("conception_market", {"round": 1, "agent": "market_strategist"})
    market_text, market_llm_id = await _llm_call(
        session, settings,
        role="planner",
        system_prompt=_MARKET_SYSTEM_EN if is_en else _MARKET_SYSTEM,
        user_prompt=(_market_user_prompt_en if is_en else _market_user_prompt)(ctx, _genre_profile),
        fallback=json.dumps(ctx.get("existing_overrides", {}).get("market", {}), ensure_ascii=False),
        template="conception_market",
    )
    _track_id(market_llm_id)
    market_proposal = _extract_json(market_text) or ctx.get("existing_overrides", {}).get("market", {})
    conception_log.append({"round": 1, "agent": "market_strategist", "proposal": market_proposal})

    _emit("conception_character", {"round": 1, "agent": "character_architect"})
    character_text, character_llm_id = await _llm_call(
        session, settings,
        role="planner",
        system_prompt=_CHARACTER_SYSTEM_EN if is_en else _CHARACTER_SYSTEM,
        user_prompt=(_character_user_prompt_en if is_en else _character_user_prompt)(ctx, _genre_profile),
        fallback=json.dumps(ctx.get("existing_overrides", {}).get("character", {}), ensure_ascii=False),
        template="conception_character",
    )
    _track_id(character_llm_id)
    character_proposal = _extract_json(character_text) or ctx.get("existing_overrides", {}).get("character", {})
    conception_log.append({"round": 1, "agent": "character_architect", "proposal": character_proposal})

    _emit("conception_world", {"round": 1, "agent": "world_builder"})
    world_text, world_llm_id = await _llm_call(
        session, settings,
        role="planner",
        system_prompt=_WORLD_SYSTEM_EN if is_en else _WORLD_SYSTEM,
        user_prompt=(_world_user_prompt_en if is_en else _world_user_prompt)(ctx, _genre_profile),
        fallback=json.dumps(ctx.get("existing_overrides", {}).get("world", {}), ensure_ascii=False),
        template="conception_world",
    )
    _track_id(world_llm_id)
    world_proposal = _extract_json(world_text) or ctx.get("existing_overrides", {}).get("world", {})
    conception_log.append({"round": 1, "agent": "world_builder", "proposal": world_proposal})

    # ── Round 2: Cross-Review ───────────────────────────────────────
    _emit("conception_review", {"round": 2, "agent": "chief_editor"})
    review_text, review_llm_id = await _llm_call(
        session, settings,
        role="critic",
        system_prompt=_REVIEW_SYSTEM_EN if is_en else _REVIEW_SYSTEM,
        user_prompt=(_review_user_prompt_en if is_en else _review_user_prompt)(ctx, market_proposal, character_proposal, world_proposal, _genre_profile),
        fallback='{"overall_coherence_score": 0.7, "contradictions": [], "gaps": [], '
                 '"market_suggestions": [], "character_suggestions": [], "world_suggestions": [], '
                 '"name_quality_issues": [], "premise_seeds": []}',
        template="conception_review",
    )
    _track_id(review_llm_id)
    review_result = _extract_json(review_text)
    conception_log.append({"round": 2, "agent": "chief_editor", "review": review_result})

    # ── Round 2.5: Creative Exploration (anti-cliché) ────────────────
    _cat = resolve_novel_category(ctx.get("genre", ""), ctx.get("sub_genre"))
    if _cat and _cat.quality_traps:
        _emit("conception_creative_exploration", {"round": 2.5, "agent": "creative_explorer"})
        exploration_text, exploration_llm_id = await _creative_exploration(
            session, settings,
            ctx=ctx,
            market=market_proposal,
            character=character_proposal,
            world=world_proposal,
            review=review_result,
            category=_cat,
            is_en=is_en,
        )
        _track_id(exploration_llm_id)
        exploration_result = _extract_json(exploration_text) or {}
        conception_log.append({"round": 2.5, "agent": "creative_explorer", "exploration": exploration_result})
        # Merge the chosen creative direction into proposals for the finalizer
        chosen = exploration_result.get("chosen_direction", {})
        if chosen:
            if chosen.get("premise_variation"):
                ctx["creative_premise_seed"] = chosen["premise_variation"]
            if chosen.get("unique_hook"):
                ctx["creative_hook"] = chosen["unique_hook"]

    # ── Round 3: Merge & Finalize ───────────────────────────────────
    _emit("conception_finalize", {"round": 3, "agent": "project_director"})
    final_text, final_llm_id = await _llm_call(
        session, settings,
        role="editor",
        system_prompt=_FINALIZE_SYSTEM_EN if is_en else _FINALIZE_SYSTEM,
        user_prompt=(_finalize_user_prompt_en if is_en else _finalize_user_prompt)(ctx, market_proposal, character_proposal, world_proposal, review_result, _genre_profile),
        fallback=_build_fallback_final(ctx, market_proposal, character_proposal, world_proposal),
        template="conception_finalize",
    )
    _track_id(final_llm_id)
    final_result = _extract_json(final_text)
    conception_log.append({"round": 3, "agent": "project_director", "final": final_result})

    # Extract final outputs with fallbacks
    writing_profile = final_result.get("writing_profile", {})
    premise = _safe_get(final_result, "premise", "")
    title = _safe_get(final_result, "title", "")

    # Ensure writing_profile has all required sections
    writing_profile = _ensure_complete_profile(writing_profile, ctx, market_proposal, character_proposal, world_proposal)
    writing_profile = _apply_commercial_brief_to_profile(writing_profile, commercial_brief)

    # Fallback premise if empty
    if not premise or len(premise) < 10:
        premise = (
            f"A {ctx['genre']} ({ctx['sub_genre']}) novel: {ctx['description']}"
            if is_en
            else (
                f"基于{ctx['genre']}（{ctx['sub_genre']}）题材，"
                f"{ctx['description']}"
            )
        )

    # Validate title: must be a concise book name, not a description or premise.
    # Chinese titles should be 2-10 characters; English 2-8 words.
    title = (title or "").strip()
    _is_valid_title = bool(title) and (
        (not is_en and 2 <= len(title) <= 10)
        or (is_en and 2 <= len(title.split()) <= 8 and len(title) <= 60)
    )
    if not _is_valid_title:
        # Try to extract a short title from a longer generated one (LLM sometimes
        # returns a description instead of a concise title).
        if title and not is_en and len(title) > 10:
            # Take up to the first punctuation or 8 chars, whichever is shorter
            import re as _re_title  # noqa: PLC0415
            m = _re_title.match(r"[\u4e00-\u9fff]{2,8}", title)
            if m:
                title = m.group(0)
                _is_valid_title = True
    if not _is_valid_title:
        # Fallback: use genre name as basis (never the description/premise)
        genre_name = ctx.get("genre", "")
        sub_genre = ctx.get("sub_genre", "")
        if is_en:
            title = f"The {sub_genre or genre_name} Chronicles" if genre_name else "Untitled Novel"
        else:
            title = sub_genre[:8] if sub_genre else genre_name[:8] if genre_name else "未命名小说"

    # Extract synopsis and tags from the finalized result
    synopsis = _safe_get(final_result, "synopsis", "").strip()
    if len(synopsis) > 500:
        synopsis = synopsis[:497] + "..."
    raw_tags = final_result.get("tags", [])
    tags = [str(t).strip() for t in raw_tags if isinstance(t, str) and t.strip()][:10]

    logger.info(
        "Conception pipeline completed for genre=%s: title=%s, premise_len=%d, synopsis_len=%d, tags=%s, profile_keys=%s",
        genre_key, title, len(premise), len(synopsis), tags, list(writing_profile.keys()),
    )

    return ConceptionResult(
        writing_profile=writing_profile,
        premise=premise,
        title=title,
        commercial_brief=commercial_brief,
        conception_log=conception_log,
        llm_run_ids=llm_run_ids,
        synopsis=synopsis,
        tags=tags,
    )


def _ensure_complete_profile(
    profile: dict[str, Any],
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
) -> dict[str, Any]:
    """Ensure the writing profile has all required sections, filling from proposals if needed."""
    existing_overrides = ctx.get("existing_overrides", {})
    fallback_profile = resolve_writing_profile(
        None,
        genre=str(ctx.get("genre", "general-fiction") or "general-fiction"),
        sub_genre=ctx.get("sub_genre"),
        language=ctx.get("language"),
    ).model_dump(mode="json")

    if "market" not in profile or not profile["market"]:
        profile["market"] = (
            market
            or existing_overrides.get("market", {})
            or fallback_profile.get("market", {})
        )

    if "character" not in profile or not profile["character"]:
        profile["character"] = {}
    # Merge character proposal fields
    char_section = profile["character"]
    for key in ("protagonist_archetype", "protagonist_core_drive", "golden_finger",
                "growth_curve", "romance_mode", "relationship_tension", "antagonist_mode"):
        if not char_section.get(key) and character.get(key):
            char_section[key] = character[key]
    # Also use existing_overrides as fallback
    for key, val in existing_overrides.get("character", {}).items():
        if not char_section.get(key):
            char_section[key] = val
    for key, val in fallback_profile.get("character", {}).items():
        if not char_section.get(key):
            char_section[key] = val

    if "world" not in profile or not profile["world"]:
        profile["world"] = (
            world
            or existing_overrides.get("world", {})
            or fallback_profile.get("world", {})
        )

    if "style" not in profile or not profile["style"]:
        profile["style"] = (
            existing_overrides.get("style")
            or fallback_profile.get("style", {})
        )

    if "serialization" not in profile or not profile["serialization"]:
        profile["serialization"] = (
            existing_overrides.get("serialization")
            or fallback_profile.get("serialization", {})
        )

    return profile


def _build_fallback_final(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
) -> str:
    """Build fallback JSON string for the finalize step."""
    fallback_profile = resolve_writing_profile(
        None,
        genre=str(ctx.get("genre", "general-fiction") or "general-fiction"),
        sub_genre=ctx.get("sub_genre"),
        language=ctx.get("language"),
    ).model_dump(mode="json")
    is_en = str(ctx.get("language", "zh-CN")).startswith("en")
    fallback_profile["market"] = market or fallback_profile.get("market", {})
    fallback_profile["character"] = {
        **fallback_profile.get("character", {}),
        **{
            k: v
            for k, v in character.items()
            if k
            in (
                "protagonist_archetype",
                "protagonist_core_drive",
                "golden_finger",
                "growth_curve",
                "romance_mode",
                "relationship_tension",
                "antagonist_mode",
            )
        },
    }
    fallback_profile["world"] = {
        **fallback_profile.get("world", {}),
        **{
            k: v
            for k, v in world.items()
            if k
            in (
                "worldbuilding_density",
                "info_reveal_strategy",
                "rule_hardness",
                "power_system_style",
                "mystery_density",
            )
        },
    }
    commercial_brief = ctx.get("commercial_brief")
    if isinstance(commercial_brief, dict) and commercial_brief:
        fallback_profile = _apply_commercial_brief_to_profile(fallback_profile, commercial_brief)
    fallback = {
        "title": (ctx.get("sub_genre") or ctx.get("genre", ""))[:8 if not is_en else 40],
        "premise": (
            f"A {ctx['genre']} ({ctx['sub_genre']}) novel: {ctx['description']}"
            if is_en
            else f"基于{ctx['genre']}（{ctx['sub_genre']}）题材，{ctx['description']}"
        ),
        "writing_profile": fallback_profile,
    }
    return json.dumps(fallback, ensure_ascii=False)

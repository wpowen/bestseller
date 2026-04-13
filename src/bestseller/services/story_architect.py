"""Story Architect Agent — AI-driven facet generation for novel projects.

This agent takes minimal user input (primary_genre + language) and generates
a complete, creative, non-repetitive StoryFacets specification. It ensures:
- No repetition with existing projects
- Creative cross-genre fusion
- Market trend awareness
- Audience fit
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.facets import StoryFacets
from bestseller.services.facet_registry import (
    expand_legacy_preset_with_variation,
    get_dimensions_summary_for_ai,
    get_trend_data_for_genre,
    list_existing_facets,
    validate_story_facets,
)
from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    complete_text,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_MAX_SIMILARITY_THRESHOLD = 0.7
_MAX_RETRIES = 2


# ──────────────────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────────────────


async def architect_story_facets(
    session: AsyncSession,
    settings: AppSettings,
    *,
    primary_genre: str,
    language: str = "zh-CN",
    genre_key: str | None = None,
    user_hints: dict[str, Any] | None = None,
) -> StoryFacets:
    """Generate a complete StoryFacets from minimal user input using AI.

    Args:
        session: Database session for querying existing projects.
        settings: App settings for LLM configuration.
        primary_genre: The main genre (only required user input).
        language: Target language (zh-CN or en).
        genre_key: Optional legacy genre_key for trend data lookup.
        user_hints: Optional dict of user preferences
            (e.g., {"mood": "轻松", "avoid": "宫斗"}).

    Returns:
        A complete StoryFacets with all dimensions filled.
        Falls back to legacy expansion if AI fails.
    """
    # 1. Gather context for the AI agent
    existing_facets = await list_existing_facets(
        session, primary_genre=primary_genre, limit=15
    )

    trend_data = get_trend_data_for_genre(genre_key or primary_genre)
    dimensions_summary = get_dimensions_summary_for_ai(language)

    # 2. Build prompt and call LLM
    for attempt in range(_MAX_RETRIES + 1):
        try:
            facets = await _call_architect_llm(
                session=session,
                settings=settings,
                primary_genre=primary_genre,
                language=language,
                user_hints=user_hints,
                existing_facets=existing_facets,
                trend_data=trend_data,
                dimensions_summary=dimensions_summary,
            )

            # 3. Anti-repetition check
            if existing_facets:
                max_sim = max(
                    facets.similarity_score(existing) for existing in existing_facets
                )
                if max_sim > _MAX_SIMILARITY_THRESHOLD:
                    logger.info(
                        "Story Architect output too similar (%.2f) to existing project, "
                        "retrying (attempt %d/%d)",
                        max_sim, attempt + 1, _MAX_RETRIES + 1,
                    )
                    if attempt < _MAX_RETRIES:
                        continue
                    # On final attempt, accept it anyway
                    logger.warning("Accepting similar facets after max retries")

            # 4. Validate
            warnings = validate_story_facets(facets)
            if warnings:
                logger.info("StoryFacets validation warnings: %s", warnings)

            return facets

        except Exception:
            logger.warning(
                "Story Architect LLM call failed (attempt %d/%d)",
                attempt + 1, _MAX_RETRIES + 1,
                exc_info=True,
            )

    # 5. Fallback to legacy expansion
    logger.warning("All Story Architect attempts failed, using legacy fallback")
    return _fallback_facets(genre_key or primary_genre, language)


# ──────────────────────────────────────────────────────────────────────
# LLM Interaction
# ──────────────────────────────────────────────────────────────────────


async def _call_architect_llm(
    session: AsyncSession,
    settings: AppSettings,
    *,
    primary_genre: str,
    language: str,
    user_hints: dict[str, Any] | None,
    existing_facets: list[StoryFacets],
    trend_data: dict[str, Any],
    dimensions_summary: str,
) -> StoryFacets:
    """Build prompt and call LLM to generate StoryFacets."""

    system_prompt = _build_system_prompt(language)
    user_prompt = _build_user_prompt(
        primary_genre=primary_genre,
        language=language,
        user_hints=user_hints,
        existing_facets=existing_facets,
        trend_data=trend_data,
        dimensions_summary=dimensions_summary,
    )

    # Use the "planner" role (lighter model, suitable for structured generation)
    request = LLMCompletionRequest(
        logical_role="planner",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response="{}",
        prompt_template="story_architect_v1",
        prompt_version="1.0",
        metadata={"agent": "story_architect", "genre": primary_genre},
    )

    result: LLMCompletionResult = await complete_text(session, settings, request)
    return _parse_architect_output(result.content, primary_genre, language)


def _build_system_prompt(language: str) -> str:
    """Build the system prompt for the Story Architect Agent."""
    if language.startswith("zh"):
        return (
            "你是「故事建筑师」— 一位精通全球网络文学市场的创意策划专家。\n"
            "你的任务是为小说项目设计独特的「故事基因组合」，确保每部作品都有差异化卖点。\n\n"
            "## 核心原则\n"
            "1. **反套路** — 至少在一个核心维度上做出非主流选择\n"
            "2. **跨类型融合** — trope_tags 必须包含至少1个来自其他类型的标签\n"
            "3. **市场感知** — 优先选择热度上升期的元素\n"
            "4. **差异化** — 不得与已有项目在关键维度上完全重复\n"
            "5. **具象化** — setting 必须具体可视化，不要抽象泛泛\n\n"
            "## 输出格式\n"
            "严格输出 JSON 格式，不要输出其他内容。\n"
            "所有 string 字段使用中文（除了 key 类字段用英文标识符）。"
        )
    return (
        "You are the 'Story Architect' — a creative strategist expert in global web fiction markets.\n"
        "Your task is to design a unique 'story genome' for novel projects, ensuring each work has differentiated appeal.\n\n"
        "## Core Principles\n"
        "1. **Against Convention** — Make at least one unconventional choice on a core dimension\n"
        "2. **Cross-Genre Fusion** — trope_tags must include at least 1 tag from a different genre\n"
        "3. **Market Awareness** — Prefer elements with rising popularity\n"
        "4. **Differentiation** — Must not duplicate key dimensions from existing projects\n"
        "5. **Specificity** — Setting must be vivid and visual, never vague\n\n"
        "## Output Format\n"
        "Output ONLY valid JSON. No other text.\n"
        "String fields should be in English."
    )


def _build_user_prompt(
    *,
    primary_genre: str,
    language: str,
    user_hints: dict[str, Any] | None,
    existing_facets: list[StoryFacets],
    trend_data: dict[str, Any],
    dimensions_summary: str,
) -> str:
    """Build the user prompt with all context for the AI agent."""
    parts: list[str] = []

    # Section 1: User input
    parts.append(f"## User Input\n- primary_genre: {primary_genre}\n- language: {language}")
    if user_hints:
        hints_str = "\n".join(f"  - {k}: {v}" for k, v in user_hints.items())
        parts.append(f"- User preferences:\n{hints_str}")

    # Section 2: Existing projects (for anti-repetition)
    if existing_facets:
        parts.append("\n## Existing Projects (MUST differentiate from these)")
        for i, ef in enumerate(existing_facets[:8], 1):
            parts.append(
                f"  {i}. tone={ef.tone}, drive={ef.narrative_drive}, "
                f"sub_genres={list(ef.sub_genres)}, "
                f"trope_tags={list(ef.trope_tags)[:5]}"
            )
    else:
        parts.append("\n## Existing Projects\nNone yet — you have full creative freedom.")

    # Section 3: Market trends
    parts.append("\n## Market Trends")
    if trend_data.get("trend_keywords"):
        parts.append(f"  Keywords: {', '.join(trend_data['trend_keywords'])}")
    if trend_data.get("trend_summary"):
        parts.append(f"  Summary: {trend_data['trend_summary']}")
    if trend_data.get("recommended_audiences"):
        parts.append(f"  Target audiences: {', '.join(trend_data['recommended_audiences'])}")

    # Section 4: Available dimensions
    parts.append(f"\n## Available Dimensions & Values{dimensions_summary}")

    # Section 5: Output schema
    parts.append(
        "\n## Required JSON Output Schema\n"
        "```json\n"
        "{\n"
        '  "sub_genres": ["string", "string"],  // 2-3 items\n'
        '  "setting": "string",  // Vivid, specific, visual. 20-60 chars\n'
        '  "tone": "string",  // From: dark/lighthearted/tense/comedic/bittersweet/epic/cozy/gritty/whimsical/melancholic\n'
        '  "power_system": "string|null",  // From dimension values or null\n'
        '  "relationship_mode": "string",  // From dimension values\n'
        '  "narrative_drive": "string",  // From dimension values\n'
        '  "emotional_register": "string",  // From dimension values\n'
        '  "trope_tags": ["string", ...],  // 4-8 creative tags\n'
        '  "platform_style": "string|null",  // Inferred from genre+language\n'
        '  "gender_channel": "string|null"  // male/female/neutral\n'
        "}\n"
        "```\n"
        "\n"
        "Output ONLY the JSON object. No explanation, no markdown fences."
    )

    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Output Parsing
# ──────────────────────────────────────────────────────────────────────


def _parse_architect_output(
    raw_output: str,
    primary_genre: str,
    language: str,
) -> StoryFacets:
    """Parse LLM output into a StoryFacets object.

    Handles common LLM output issues:
    - Markdown code fences around JSON
    - Extra text before/after JSON
    - Missing fields (filled with defaults)
    """
    # Strip markdown code fences if present
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        # Remove opening fence
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1:]
        # Remove closing fence
        if "```" in cleaned:
            cleaned = cleaned[:cleaned.rindex("```")]
        cleaned = cleaned.strip()

    # Try to extract JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in architect output: {raw_output[:200]}")

    json_str = cleaned[start:end + 1]
    data = json.loads(json_str)

    return StoryFacets(
        primary_genre=primary_genre,
        language=language,
        sub_genres=tuple(data.get("sub_genres", [])),
        setting=data.get("setting", ""),
        tone=data.get("tone", "balanced"),
        power_system=data.get("power_system"),
        relationship_mode=data.get("relationship_mode", "no-cp"),
        narrative_drive=data.get("narrative_drive", "progression"),
        emotional_register=data.get("emotional_register", "balanced"),
        trope_tags=tuple(data.get("trope_tags", [])),
        platform_style=data.get("platform_style"),
        gender_channel=data.get("gender_channel"),
        generation_source="ai",
    )


# ──────────────────────────────────────────────────────────────────────
# Fallback
# ──────────────────────────────────────────────────────────────────────


def _fallback_facets(genre_key: str, language: str) -> StoryFacets:
    """Provide StoryFacets when AI is unavailable.

    Uses legacy expansion with random variation for diversity.
    """
    facets = expand_legacy_preset_with_variation(genre_key)
    if facets is not None:
        # Override language if needed
        if facets.language != language:
            return StoryFacets(
                primary_genre=facets.primary_genre,
                language=language,
                sub_genres=facets.sub_genres,
                setting=facets.setting,
                tone=facets.tone,
                power_system=facets.power_system,
                relationship_mode=facets.relationship_mode,
                narrative_drive=facets.narrative_drive,
                emotional_register=facets.emotional_register,
                trope_tags=facets.trope_tags,
                platform_style=facets.platform_style,
                gender_channel=facets.gender_channel,
                generation_source="legacy",
            )
        return facets

    # Absolute last resort — minimal facets
    logger.warning(
        "No legacy expansion found for genre_key=%s, returning minimal facets",
        genre_key,
    )
    return StoryFacets(
        primary_genre=genre_key,
        language=language,
        generation_source="legacy",
    )

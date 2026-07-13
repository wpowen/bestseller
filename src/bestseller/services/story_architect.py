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

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.facets import StoryFacets
from bestseller.services.concept_lab import render_concept_lab_prompt_block
from bestseller.services.facet_registry import (
    expand_legacy_preset_with_variation,
    get_dimensions_summary_for_ai,
    get_trend_data_for_genre,
    list_existing_facets,
    validate_story_facets,
)
from bestseller.services.genre_intent_contract import GenreIntentContract
from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    complete_text,
)
from bestseller.services.llm_closed_loop import (
    LLMGateFinding,
    build_repair_user_prompt,
    findings_from_exception,
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
    genre_intent: GenreIntentContract | None = None,
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
    repair_findings: list[LLMGateFinding] = []

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
                repair_findings=repair_findings,
                genre_intent=genre_intent,
            )

            # The architect may enrich surface facets, but it never owns the
            # selected genre. Re-assert the immutable contract at the agent
            # boundary so a model cannot turn xianxia into urban-cultivation.
            if genre_intent is not None:
                facets = facets.model_copy(
                    update={
                        "primary_genre": genre_intent.genre_key,
                        "language": language,
                    }
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
                        repair_findings = [
                            LLMGateFinding(
                                code="STORY_FACETS_TOO_SIMILAR",
                                severity="major",
                                path="story_facets",
                                message="Generated facets are too similar to an existing project.",
                                expected=(
                                    "Use clearly differentiated sub_genres, setting, narrative_drive, "
                                    "emotional_register, and trope_tags."
                                ),
                                actual=f"max_similarity={max_sim:.2f}",
                                repair_action=(
                                    "Regenerate the full JSON with a more distinctive story genome. "
                                    "Do not keep the same core combination."
                                ),
                            )
                        ]
                        continue
                    # On final attempt, accept it anyway
                    logger.warning("Accepting similar facets after max retries")

            # 4. Validate
            warnings = validate_story_facets(facets)
            if warnings:
                logger.info("StoryFacets validation warnings: %s", warnings)
                if attempt < _MAX_RETRIES:
                    repair_findings = [
                        LLMGateFinding(
                            code="STORY_FACETS_VALIDATION_WARNING",
                            severity="major",
                            path="story_facets",
                            message="Story facets validation emitted warnings.",
                            expected="All story facet dimensions should be concrete, valid, and market-specific.",
                            actual="; ".join(str(warning) for warning in warnings[:8]),
                            repair_action="Regenerate the full JSON and fix the listed facet validation warnings.",
                        )
                    ]
                    continue

            return facets

        except Exception as exc:
            repair_findings = findings_from_exception(exc, default_path="story_facets")
            logger.warning(
                "Story Architect LLM call failed (attempt %d/%d)",
                attempt + 1, _MAX_RETRIES + 1,
                exc_info=True,
            )

    # 5. Fallback to legacy expansion
    logger.warning("All Story Architect attempts failed, using legacy fallback")
    fallback = _fallback_facets(
        genre_intent.genre_key if genre_intent is not None else (genre_key or primary_genre),
        language,
    )
    if genre_intent is not None:
        fallback = fallback.model_copy(update={"primary_genre": genre_intent.genre_key})
    return fallback


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
    repair_findings: list[LLMGateFinding] | None = None,
    genre_intent: GenreIntentContract | None = None,
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
        genre_intent=genre_intent,
    )
    if repair_findings:
        user_prompt = build_repair_user_prompt(
            original_user_prompt=user_prompt,
            findings=repair_findings,
            language=language,
        )

    # Use the "planner" role (lighter model, suitable for structured generation)
    request = LLMCompletionRequest(
        logical_role="planner",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response="{}",
        prompt_template="story_architect_v1_repair" if repair_findings else "story_architect_v1",
        prompt_version="1.0",
        metadata={
            "agent": "story_architect",
            "genre": primary_genre,
            "genre_intent_contract_hash": (
                genre_intent.contract_hash() if genre_intent is not None else None
            ),
            "semantic_repair_findings": [
                finding.to_dict() for finding in (repair_findings or [])
            ],
        },
    )

    result: LLMCompletionResult = await complete_text(session, settings, request)
    return _parse_architect_output(result.content, primary_genre, language)


def _build_system_prompt(language: str) -> str:
    """Build the system prompt for the Story Architect Agent."""
    if language.startswith("zh"):
        return (
            "# ROLE\n"
            "你是「故事建筑师」——一位精通全球网络文学市场的创意策划专家。\n"
            "你做过 50+ 部签约长篇的「故事基因设计」，最擅长在大纲未动笔前就识别一本书是「红海复刻」还是「能上推荐」。\n"
            "\n"
            "# CONTEXT\n"
            "你的产出会成为后续 plotting / character / world 三大子系统的种子。\n"
            "你这里设计得平庸 = 整本书天花板就在 C 区；设计得有锐度 = 至少给签约机会。\n"
            "\n"
            "# TASK\n"
            "为小说项目设计独特的「故事基因组合」（StoryFacets），确保每部作品有差异化卖点。\n"
            "\n"
            "# CONSTRAINTS · 五大核心原则\n"
            "1. **反套路（仅限表皮）**——在**表皮维度**（设定外壳/支线机制/反派）上做非主流选择；"
            "绝不在题材的爽点脊柱上反套路（见下方 SPINE LOCK）\n"
            "2. **跨类型融合（当调味，非换骨）**——trope_tags 可含 ≥ 1 个其他类型标签作点缀，"
            "但不得让外类型盖过本题材的核心爽点\n"
            "3. **市场感知**——优先选择热度上升期的元素\n"
            "4. **差异化（差表皮，锁脊柱）**——与已有项目在表皮上区分开，但保留本题材读者要的承重配方\n"
            "5. **具象化**——setting 必须具体可视化，不要抽象泛泛\n"
            "\n"
            "# THINKING（产 JSON 前在脑内 4 步）\n"
            "1. 浏览 existing_facets——已有项目占了哪些坑？我必须避开\n"
            "2. 浏览 market_trends——什么元素在涨？什么标签开始过热？\n"
            "3. 基于以上两点，确定本书的「不可替代点」（one-line USP）\n"
            "4. 由 USP 反推 5 大维度的具体选择\n"
            "\n"
            "# OUTPUT FORMAT\n"
            "严格输出 JSON，不带 markdown 围栏、不带解释。\n"
            "所有 string 字段使用中文（key 类字段用英文标识符）。"
        )
    return (
        "# ROLE\n"
        "You are the 'Story Architect' — a creative strategist expert in global web fiction markets.\n"
        "You have designed story DNA for 50+ signed novels and know how to spot 'red ocean clone' vs 'recommendation candidate'.\n"
        "\n"
        "# CONTEXT\n"
        "Your output becomes the seed for downstream plotting / character / world subsystems.\n"
        "Mediocre design here = the whole book is capped at C tier; sharp design = at least a signing chance.\n"
        "\n"
        "# TASK\n"
        "Design a unique 'story genome' (StoryFacets) for the novel project, ensuring differentiated appeal.\n"
        "\n"
        "# CONSTRAINTS · Core Principles\n"
        "1. **Against Convention (SURFACE only)** — Make an unconventional choice on a *surface* "
        "dimension (setting shell / side mechanics / villains); never subvert the genre's payoff "
        "spine (see SPINE LOCK below)\n"
        "2. **Cross-Genre Fusion (as seasoning, not a skeleton swap)** — trope_tags may include ≥1 "
        "tag from another genre as a topping, but it must not eclipse this genre's core payoff\n"
        "3. **Market Awareness** — Prefer elements with rising popularity\n"
        "4. **Differentiation (differ on surface, lock the spine)** — Differ from existing projects "
        "on the surface, but keep the load-bearing formula this genre's readers come for\n"
        "5. **Specificity** — Setting must be vivid and visual, never vague\n"
        "\n"
        "# THINKING (before JSON, in your head)\n"
        "1. Scan existing_facets — which slots are taken? must avoid\n"
        "2. Scan market_trends — what's rising? what's over-heated?\n"
        "3. Decide this book's one-line USP\n"
        "4. Reverse-engineer 5 dimensions from USP\n"
        "\n"
        "# OUTPUT FORMAT\n"
        "Output ONLY valid JSON. No markdown fences, no commentary.\n"
        "String fields should be in English."
    )


def _render_persona_spine_lock(
    primary_genre: str,
    user_hints: dict[str, Any] | None,
    language: str,
) -> str:
    """Render a reader-persona SPINE LOCK so differentiation cannot subvert the
    genre's load-bearing 爽点 spine.

    Root cause this guards: the Story Architect's 反套路 / 跨类型融合 / 差异化
    constraints, with no persona guardrail, push a 仙侠升级 (male-channel power
    fantasy) request away from its load-bearing spine (金手指/升级线/打脸兑现)
    toward novelty (无金手指 / morally-grey / 克系规则怪谈) — beautifully written
    but off-persona, so the target reader bounces in 3 seconds. The persona's
    turnoffs literally include 节奏慢/铺垫长/文绉绉 — exactly the drift signature.
    Differentiation must stay on the SURFACE (setting skin / specific hook /
    side mechanics), never the spine.
    """
    try:
        from bestseller.services.genre_persona import resolve_persona
    except Exception:  # noqa: BLE001 — guardrail is best-effort, never fatal
        return ""

    orientation = ""
    if isinstance(user_hints, dict):
        raw = str(user_hints.get("audience_orientation") or "").strip()
        orientation = {
            "男频": "男频", "女频": "女频", "male": "男频", "female": "女频",
        }.get(raw, "")

    persona = resolve_persona(primary_genre, None, (), orientation or None)
    triggers = "、".join(persona.click_triggers[:5])
    turnoffs = "、".join(persona.turnoffs[:5])

    if language.startswith("zh"):
        return (
            "\n## 题材脊柱锁定（SPINE LOCK · 不可妥协，优先级高于下方差异化要求）\n"
            f"本书面向【{persona.channel}】读者。差异化只能改表皮，绝不可动以下承重脊柱：\n"
            f"- 必须保留·爽点内核（按本题材落地，不可删除/反转）：{persona.fantasy}\n"
            f"- 必须命中·点击钩（≥2 项要落进 narrative_drive / trope_tags）：{triggers}\n"
            f"- 绝对避免·读者雷点（命中即三秒划走）：{turnoffs}\n"
            "差异化边界：上方 CONSTRAINTS 的「反套路 / 跨类型融合 / 差异化」"
            "只允许作用于【表皮】——具体设定外壳、独特场景、支线机制、反派与配角设计；"
            "严禁把「核心维度反套路」理解为删除爽点脊柱，严禁把 tone 选成 "
            "dark/gritty/melancholic 等压抑/文艺/道德灰色，严禁 power_system=null（无金手指/无成长线）。\n"
            "记住：目标读者要的是「熟悉配方 + 新鲜外壳」，不是「反配方」。"
            "动了脊柱 = 红海之外的自杀式差异化，本书天花板归零。\n"
        )
    return (
        "\n## GENRE SPINE LOCK (non-negotiable, OUTRANKS the differentiation asks below)\n"
        f"This book targets [{persona.channel}] readers. Differentiate the SURFACE only; "
        "never touch the load-bearing spine:\n"
        f"- MUST KEEP — core payoff (adapt to this genre, never remove/invert): {persona.fantasy}\n"
        f"- MUST HIT — click triggers (≥2 into narrative_drive / trope_tags): {triggers}\n"
        f"- MUST AVOID — reader turnoffs (instant bounce): {turnoffs}\n"
        "Differentiation boundary: the 'against-convention / cross-genre fusion / differentiation' "
        "constraints above may only act on the SURFACE (setting skin, specific hook, side mechanics, "
        "villains). Do NOT read 'unconventional choice on a core dimension' as deleting the payoff spine, "
        "do NOT pick dark/gritty/melancholic tone for a power-fantasy channel, do NOT set power_system=null.\n"
        "Readers want a familiar formula in a fresh shell, not an anti-formula. "
        "Breaking the spine = suicidal differentiation; the book's ceiling drops to zero.\n"
    )


def _build_user_prompt(
    *,
    primary_genre: str,
    language: str,
    user_hints: dict[str, Any] | None,
    existing_facets: list[StoryFacets],
    trend_data: dict[str, Any],
    dimensions_summary: str,
    genre_intent: GenreIntentContract | None = None,
) -> str:
    """Build the user prompt with all context for the AI agent."""
    parts: list[str] = []

    # Section 1: User input
    parts.append(f"## User Input\n- primary_genre: {primary_genre}\n- language: {language}")
    if genre_intent is not None:
        tags = "、".join(genre_intent.tags) or "无"
        parts.append(
            "\n## Genre Intent Contract (AUTHORITATIVE — do not infer or replace)\n"
            f"- genre_key: {genre_intent.genre_key}\n"
            f"- genre: {genre_intent.genre_label}\n"
            f"- selected_sub_genre: {genre_intent.sub_genre_label or '未指定'}\n"
            f"- selected_tags: {tags}\n"
            f"- prompt_pack: {genre_intent.prompt_pack_key}\n"
            f"- allowed_modernity: {genre_intent.allowed_modernity}\n"
            "Hard rule: you may only propose surface setting/trope variations. "
            "Never change genre, selected sub-genre, prompt pack, or ontology. "
            "Any sub_genres you output are advisory suggestions, not a replacement "
            "for the selected taxonomy."
        )
    if user_hints:
        concept_block = render_concept_lab_prompt_block(user_hints, language=language)
        if concept_block:
            parts.append(f"\n## Selected Concept Lab Contract\n{concept_block}")
        hints_str = "\n".join(
            f"  - {k}: {v}"
            for k, v in user_hints.items()
            if k not in {"concept_lab", "concept_lab_bundle"}
        )
        if hints_str:
            parts.append(f"- User preferences:\n{hints_str}")

    # Section 1.5: Reader-persona SPINE LOCK — differentiation must not subvert
    # the genre's load-bearing 爽点 spine. Placed BEFORE the anti-repetition
    # pressure so the spine is read first.
    spine_lock = _render_persona_spine_lock(primary_genre, user_hints, language)
    if spine_lock:
        parts.append(spine_lock)

    # Section 2: Same-genre peers — differentiate the SURFACE, keep the spine.
    # Framing matters: telling the model to differ on tone/drive (the genre
    # spine) is what pulled prior same-genre books OFF-genre. These peers share
    # the spine with the new book BY DESIGN; the new book must differ on the
    # concrete premise SKIN (setting / specific mechanic / specific hook), NOT by
    # swapping tone, dropping the golden finger, or grafting on another genre.
    if existing_facets:
        parts.append(
            "\n## Same-Genre Peers (differentiate the SURFACE from these — "
            "NOT the spine)"
        )
        parts.append(
            "These books share your genre. It is EXPECTED and CORRECT that you "
            "share their spine (tone family / narrative_drive / core payoff "
            "tropes). Do NOT differ by changing tone, dropping the payoff, or "
            "fusing in another genre. Differ ONLY by a distinct concrete "
            "`setting` and a distinct specific mechanic/hook."
        )
        for i, ef in enumerate(existing_facets[:8], 1):
            parts.append(
                f"  {i}. setting=《{(ef.setting or '—')[:40]}》, "
                f"trope_combo={list(ef.trope_tags)[:5]}"
            )
        parts.append(
            "→ Your `setting` and specific mechanic must be clearly distinct "
            "from every one above; your tone/drive/core tropes may match."
        )
    else:
        parts.append("\n## Same-Genre Peers\nNone yet — you have full creative freedom.")

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

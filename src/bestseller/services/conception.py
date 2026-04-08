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
from bestseller.services.writing_presets import list_genre_presets, list_platform_presets
from bestseller.settings import AppSettings

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


def _build_genre_context(genre_key: str, chapter_count: int) -> dict[str, Any]:
    """Build context dict from genre preset for prompts."""
    presets = {p.key: p for p in list_genre_presets()}
    preset = presets.get(genre_key)
    if preset is None:
        raise ValueError(f"Unknown genre_key: {genre_key}")

    platform_presets = {p.key: p for p in list_platform_presets()}
    recommended_platform = None
    if preset.recommended_platforms:
        for pkey in ("番茄小说", "起点中文网", "七猫小说", "晋江文学城"):
            if pkey in preset.recommended_platforms:
                recommended_platform = pkey
                break
        if recommended_platform is None:
            recommended_platform = preset.recommended_platforms[0]

    return {
        "genre_key": genre_key,
        "genre": preset.genre,
        "sub_genre": preset.sub_genre,
        "description": preset.description,
        "chapter_count": chapter_count,
        "recommended_platforms": preset.recommended_platforms,
        "recommended_audiences": preset.recommended_audiences,
        "trend_keywords": preset.trend_keywords,
        "trend_score": preset.trend_score,
        "trend_summary": preset.trend_summary,
        "default_platform": recommended_platform,
        "existing_overrides": preset.writing_profile_overrides,
    }


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


def _market_user_prompt(ctx: dict[str, Any]) -> str:
    return (
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


def _character_user_prompt(ctx: dict[str, Any]) -> str:
    return (
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
        f'  "key_characters": [\n'
        f'    {{"name": "角色名", "role": "protagonist/antagonist/ally/mentor",\n'
        f'     "name_reasoning": "命名理由",\n'
        f'     "personality_keywords": ["关键词1", "关键词2"],\n'
        f'     "relationship_to_protagonist": "与主角的关系"}}\n'
        f'  ]\n'
        f"}}\n"
        f"\n角色命名要求：\n"
        f"1. 根据题材选择合适的姓名风格（古风仙侠用古典名、都市用现代名、末日科幻可用普通名）\n"
        f"2. 主角名 2-3 字，音调优美，避免拗口\n"
        f"3. 配角和反派姓氏不与主角重复\n"
        f"4. 避免谐音不雅或过于常见的网文烂大街名字\n"
        f"5. 每个名字附命名理由"
    )


def _world_user_prompt(ctx: dict[str, Any]) -> str:
    return (
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


# ─────────────────────────────────────────────────────────────────────
# Round 2: Cross-review
# ─────────────────────────────────────────────────────────────────────

_REVIEW_SYSTEM = (
    "你是一位资深的小说总编辑，擅长从整体视角审查市场定位、角色体系和世界观之间的配合度。"
    "你需要找出三份提案之间的矛盾、空白和可优化之处。"
    "输出必须是合法 JSON，不要解释。"
)


def _review_user_prompt(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
) -> str:
    return (
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
        f'  "premise_seeds": ["可作为premise种子的核心冲突点1", "种子2"]\n'
        f"}}"
    )


# ─────────────────────────────────────────────────────────────────────
# Round 3: Merge & finalize
# ─────────────────────────────────────────────────────────────────────

_FINALIZE_SYSTEM = (
    "你是一位小说项目总策划，负责将市场定位、角色体系、世界观的讨论成果整合为最终方案。"
    "你需要产出完整的 WritingProfile、一段精炼的 premise 和一个吸引人的书名。"
    "输出必须是合法 JSON，不要解释。"
)


def _finalize_user_prompt(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
    review: dict[str, Any],
) -> str:
    return (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n## 市场定位提案\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## 角色体系提案\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## 世界观提案\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n## 审查意见\n{json.dumps(review, ensure_ascii=False, indent=2)}\n"
        f"\n请根据以上讨论成果，生成最终方案 JSON：\n"
        f'{{\n'
        f'  "title": "小说书名（4-8字，有吸引力）",\n'
        f'  "premise": "小说前提/核心设定（100-200字，包含主角、核心冲突、金手指和悬念）",\n'
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
        f'      "antagonist_mode": "..."\n'
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
    progress: ProgressCallback | None = None,
) -> ConceptionResult:
    """Multi-agent discussion to auto-generate a complete WritingProfile.

    Three rounds:
    1. Independent proposals from market/character/world specialists
    2. Cross-review by a critic
    3. Merge & finalize by an editor

    Returns a ConceptionResult with the complete writing_profile, premise, and title.
    """
    ctx = _build_genre_context(genre_key, chapter_count)
    if user_hints:
        ctx["user_hints"] = user_hints

    llm_run_ids: list[UUID] = []
    conception_log: list[dict[str, Any]] = []

    def _emit(stage: str, data: dict[str, Any] | None = None) -> None:
        if progress is not None:
            progress(stage, data)

    def _track_id(llm_id: UUID | None) -> None:
        if llm_id is not None:
            llm_run_ids.append(llm_id)

    # ── Round 1: Independent Proposals ──────────────────────────────
    _emit("conception_market", {"round": 1, "agent": "market_strategist"})
    market_text, market_llm_id = await _llm_call(
        session, settings,
        role="planner",
        system_prompt=_MARKET_SYSTEM,
        user_prompt=_market_user_prompt(ctx),
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
        system_prompt=_CHARACTER_SYSTEM,
        user_prompt=_character_user_prompt(ctx),
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
        system_prompt=_WORLD_SYSTEM,
        user_prompt=_world_user_prompt(ctx),
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
        system_prompt=_REVIEW_SYSTEM,
        user_prompt=_review_user_prompt(ctx, market_proposal, character_proposal, world_proposal),
        fallback='{"overall_coherence_score": 0.7, "contradictions": [], "gaps": [], '
                 '"market_suggestions": [], "character_suggestions": [], "world_suggestions": [], '
                 '"name_quality_issues": [], "premise_seeds": []}',
        template="conception_review",
    )
    _track_id(review_llm_id)
    review_result = _extract_json(review_text)
    conception_log.append({"round": 2, "agent": "chief_editor", "review": review_result})

    # ── Round 3: Merge & Finalize ───────────────────────────────────
    _emit("conception_finalize", {"round": 3, "agent": "project_director"})
    final_text, final_llm_id = await _llm_call(
        session, settings,
        role="editor",
        system_prompt=_FINALIZE_SYSTEM,
        user_prompt=_finalize_user_prompt(ctx, market_proposal, character_proposal, world_proposal, review_result),
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

    # Fallback premise if empty (10 Chinese chars ≈ one sentence)
    if not premise or len(premise) < 10:
        premise = (
            f"基于{ctx['genre']}（{ctx['sub_genre']}）题材，"
            f"{ctx['description']}"
        )

    # Fallback title if empty
    if not title:
        title = ctx.get("description", ctx["genre"])[:20]

    logger.info(
        "Conception pipeline completed for genre=%s: title=%s, premise_len=%d, profile_keys=%s",
        genre_key, title, len(premise), list(writing_profile.keys()),
    )

    return ConceptionResult(
        writing_profile=writing_profile,
        premise=premise,
        title=title,
        conception_log=conception_log,
        llm_run_ids=llm_run_ids,
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

    if "market" not in profile or not profile["market"]:
        profile["market"] = market or existing_overrides.get("market", {})

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

    if "world" not in profile or not profile["world"]:
        profile["world"] = world or existing_overrides.get("world", {})

    if "style" not in profile or not profile["style"]:
        profile["style"] = existing_overrides.get("style", {
            "pov_type": "third-limited",
            "prose_style": "commercial-web-serial",
            "sentence_style": "mixed",
            "dialogue_ratio": 0.30,
        })

    if "serialization" not in profile or not profile["serialization"]:
        profile["serialization"] = existing_overrides.get("serialization", {
            "opening_mandate": "前3章内必须亮出主角差异化优势和核心悬念",
            "first_three_chapter_goal": "完成主角卖点展示、核心异常亮相、第一轮得失",
            "chapter_ending_rule": "每章末尾必须留下追读钩子",
        })

    return profile


def _build_fallback_final(
    ctx: dict[str, Any],
    market: dict[str, Any],
    character: dict[str, Any],
    world: dict[str, Any],
) -> str:
    """Build fallback JSON string for the finalize step."""
    fallback = {
        "title": ctx.get("description", ctx["genre"])[:20],
        "premise": f"基于{ctx['genre']}（{ctx['sub_genre']}）题材，{ctx['description']}",
        "writing_profile": {
            "market": market,
            "character": {
                k: v for k, v in character.items()
                if k in ("protagonist_archetype", "protagonist_core_drive", "golden_finger",
                         "growth_curve", "romance_mode", "relationship_tension", "antagonist_mode")
            },
            "world": {
                k: v for k, v in world.items()
                if k in ("worldbuilding_density", "info_reveal_strategy", "rule_hardness",
                         "power_system_style", "mystery_density")
            },
        },
    }
    return json.dumps(fallback, ensure_ascii=False)

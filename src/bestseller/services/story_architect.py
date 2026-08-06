"""Story Architect Agent — AI-driven facet generation for novel projects.

This agent takes minimal user input (primary_genre + language) and generates
a complete, creative, non-repetitive StoryFacets specification. It ensures:
- No repetition with existing projects (deterministic gate — never shown to the model)
- Creative cross-genre fusion
- Audience fit grounded in the user's own choices
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.facets import StoryFacets
from bestseller.services.anti_default_motif import (
    mentions_death_theme,
    mentions_debt_theme,
)
from bestseller.services.concept_lab import render_concept_lab_prompt_block
from bestseller.services.facet_registry import (
    expand_legacy_preset_with_variation,
    list_existing_facets,
    validate_story_facets,
)
from bestseller.services.genre_intent_contract import (
    GenreIntentContract,
    detect_genre_native_ontology_violations,
)
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

_FACET_TONE_BY_CREATION_TONE: dict[str, str] = {
    "light": "lighthearted",
    "epic": "epic",
    "dark": "dark",
    "hot": "tense",
}


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
    # 1. Gather context for the AI agent. Existing facets feed ONLY the
    # deterministic similarity gate below — they are never shown to the model.
    existing_facets = await list_existing_facets(
        session, primary_genre=primary_genre, limit=15
    )
    # Trend-keyword feed removed by product ruling (2026-07-31); genre_key
    # remains in use only for the legacy fallback expansion below.
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
                repair_findings=repair_findings,
                genre_intent=genre_intent,
            )

            # The architect may enrich surface facets, but it never owns the
            # selected genre. Re-assert the immutable contract at the agent
            # boundary so a model cannot turn xianxia into urban-cultivation.
            facets = _apply_genre_intent_to_facets(
                facets,
                genre_intent=genre_intent,
                language=language,
            )

            # Prompt instructions are not a boundary.  The live 2026-07-31
            # xianxia run received allowed_modernity=genre_native yet returned
            # an ``外卖小哥`` setting.  Reject it here, before StoryFacets can
            # become a seed for the whole book.  Do not echo matched vocabulary
            # into repair prompts; that would turn the validator into a prompt-
            # pollution channel.  A final invalid attempt falls through to the
            # genre-native legacy fallback instead of being accepted.
            ontology_findings = _story_facets_ontology_findings(facets, genre_intent)
            if ontology_findings:
                logger.info(
                    "Story Architect ontology mismatch (%d finding(s)), retrying "
                    "(attempt %d/%d)",
                    len(ontology_findings),
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )
                repair_findings = ontology_findings
                continue

            motif_findings = _story_facets_default_motif_findings(
                facets,
                genre_intent=genre_intent,
                user_hints=user_hints,
            )
            if motif_findings:
                logger.info(
                    "Story Architect introduced an unrequested default motif, "
                    "retrying (attempt %d/%d)",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )
                repair_findings = motif_findings
                continue

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

            # 4. Validate — advisory only. Dimension values are freeform by
            # product ruling (2026-07-31): out-of-catalog values are the
            # model's own wording, not defects, so they never trigger a retry.
            warnings = validate_story_facets(facets)
            if warnings:
                logger.info("StoryFacets validation warnings (advisory): %s", warnings)

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
        fallback = _apply_genre_intent_to_facets(
            fallback,
            genre_intent=genre_intent,
            language=language,
        )
    if _story_facets_default_motif_findings(
        fallback,
        genre_intent=genre_intent,
        user_hints=user_hints,
    ):
        # A legacy preset is still generated data.  If it carries a motif the
        # user did not choose, do not promote it into the automatic seed.  A
        # minimal facet set keeps the selected contract while forcing the
        # tournament to create and validate a fresh premise.
        fallback = _apply_genre_intent_to_facets(
            StoryFacets(
                primary_genre=(
                    genre_intent.genre_key
                    if genre_intent is not None
                    else primary_genre
                ),
                language=language,
                generation_source="legacy",
            ),
            genre_intent=genre_intent,
            language=language,
        )
    return fallback


def _apply_genre_intent_to_facets(
    facets: StoryFacets,
    *,
    genre_intent: GenreIntentContract | None,
    language: str,
) -> StoryFacets:
    """Re-assert user-owned identity and tone at the agent boundary.

    Story Architect is allowed to propose a setting and story skin.  It does
    not own the selected genre or tone.  Prompt text alone did not protect this
    boundary: a live ``tone=light`` request returned ``tone=epic`` and that
    value became the automatic seed for every downstream conception agent.
    """

    if genre_intent is None:
        return facets
    updates: dict[str, Any] = {
        "primary_genre": genre_intent.genre_key,
        "language": language,
    }
    selected_tone = str(genre_intent.tone_preference or "").strip().lower()
    canonical_tone = _FACET_TONE_BY_CREATION_TONE.get(selected_tone)
    if canonical_tone:
        updates["tone"] = canonical_tone
    return facets.model_copy(update=updates)


def _story_facets_ontology_findings(
    facets: StoryFacets,
    genre_intent: GenreIntentContract | None,
) -> list[LLMGateFinding]:
    """Return deterministic ontology findings for generated story facets.

    StoryFacets sit upstream of every conception agent, so an incompatible
    modern role here is more damaging than the same drift in a late synopsis.
    Reuse the canonical genre-intent detector and disclose only the hit count
    to the repairing model.
    """

    if genre_intent is None:
        return []
    story_surface = json.dumps(
        {
            "sub_genres": list(facets.sub_genres),
            "setting": facets.setting,
            "power_system": facets.power_system,
            "relationship_mode": facets.relationship_mode,
            "narrative_drive": facets.narrative_drive,
            "trope_tags": list(facets.trope_tags),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    hits = detect_genre_native_ontology_violations(story_surface, genre_intent)
    if not hits:
        return []
    count = len(hits)
    return [
        LLMGateFinding(
            code="STORY_FACETS_ONTOLOGY_MISMATCH",
            severity="major",
            path="story_facets.setting",
            message="Generated StoryFacets crossed the selected genre ontology boundary.",
            expected=(
                "Use only roles, institutions, tools, and settings native to the "
                "selected genre contract."
            ),
            actual=f"{count} incompatible term(s)",
            repair_action=(
                "Rebuild the setting and related facets from the selected genre's "
                "native world rules. Do not preserve or paraphrase the rejected "
                "modern role, institution, or tool."
            ),
        )
    ]


def _story_facets_default_motif_findings(
    facets: StoryFacets,
    *,
    genre_intent: GenreIntentContract | None,
    user_hints: dict[str, Any] | None,
) -> list[LLMGateFinding]:
    """Retired 2026-08-02 — always returns no findings.

    This rejected generated facets whose setting or tropes touched death or debt
    unless the user had named that theme first. It meant a 仙侠 book could not
    open on a funeral, and it re-rolled the architect until the story was
    bloodless. Genre fidelity is enforced by the ontology guard (does the world
    match the selected taxonomy); premise content is the book's own.
    """

    del facets, genre_intent, user_hints
    return []


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
            "# CONSTRAINTS · 核心原则\n"
            "1. **反套路（仅限表皮）**——在**表皮维度**（设定外壳/支线机制/反派）上做非主流选择；"
            "用户选定的题材及其核心满足感不动\n"
            "2. **跨类型融合（当调味，非换骨）**——trope_tags 可含 ≥ 1 个其他类型标签作点缀，"
            "但不得让外类型盖过本题材的核心爽点\n"
            "3. **差异化（差表皮，锁骨架）**——与已有项目在表皮上区分开，但保留用户选定题材的承重配方\n"
            "4. **具象化**——setting 必须具体可视化，不要抽象泛泛\n"
            "\n"
            "# THINKING（产 JSON 前在脑内 2 步）\n"
            "1. 基于用户的题材与选项，确定本书的「不可替代点」（one-line USP）\n"
            "2. 由 USP 反推各维度的具体选择\n"
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
        "dimension (setting shell / side mechanics / villains); the user-selected genre and its "
        "core payoff stay untouched\n"
        "2. **Cross-Genre Fusion (as seasoning, not a skeleton swap)** — trope_tags may include ≥1 "
        "tag from another genre as a topping, but it must not eclipse this genre's core payoff\n"
        "3. **Differentiation (differ on surface, keep the frame)** — Differ from existing projects "
        "on the surface, but keep the load-bearing formula of the user-selected genre\n"
        "4. **Specificity** — Setting must be vivid and visual, never vague\n"
        "\n"
        "# THINKING (before JSON, in your head)\n"
        "1. Decide this book's one-line USP from the user's genre and choices\n"
        "2. Reverse-engineer the dimensions from that USP\n"
        "\n"
        "# OUTPUT FORMAT\n"
        "Output ONLY valid JSON. No markdown fences, no commentary.\n"
        "String fields should be in English."
    )


def _build_user_prompt(
    *,
    primary_genre: str,
    language: str,
    user_hints: dict[str, Any] | None,
    existing_facets: list[StoryFacets],
    genre_intent: GenreIntentContract | None = None,
) -> str:
    """Build the user prompt with all context for the AI agent."""
    parts: list[str] = []

    # Section 1: User input
    parts.append(f"## User Input\n- primary_genre: {primary_genre}\n- language: {language}")
    if genre_intent is not None:
        tags = "、".join(genre_intent.tags) or "无"
        enhancers = genre_intent.explicit_enhancers
        effect_skills = ", ".join(enhancers.effect_skills) or "none"
        tone_preference = str(genre_intent.tone_preference or "unspecified")
        parts.append(
            "\n## Genre Intent Contract (AUTHORITATIVE — do not infer or replace)\n"
            f"- genre_key: {genre_intent.genre_key}\n"
            f"- genre: {genre_intent.genre_label}\n"
            f"- selected_sub_genre: {genre_intent.sub_genre_label or '未指定'}\n"
            f"- selected_tags: {tags}\n"
            f"- prompt_pack: {genre_intent.prompt_pack_key}\n"
            f"- allowed_modernity: {genre_intent.allowed_modernity}\n"
            f"- tone_preference: {tone_preference}\n"
            f"- cost_style: {enhancers.cost_style}\n"
            f"- effect_skills: {effect_skills}\n"
            "Hard rule: you may only propose surface setting/trope variations. "
            "Never change genre, selected sub-genre, prompt pack, or ontology. "
            "An explicit tone is user-owned and must not be replaced by a darker, "
            "heavier, or otherwise contradictory tone. Selected effect skills must "
            "shape repeatable story situations rather than appear as decorative labels. "
            "For cost_style=minimal, build pressure through opponent response, resource "
            "movement, exposure, and choices; do not invent a recurring universal price. "
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

    # (2026-07-31) The reader-persona SPINE LOCK was removed by explicit
    # product ruling: hardcoded persona tables must not exist in prompts.
    # Genre fidelity is enforced by the user's Genre Intent Contract above plus
    # the deterministic ontology guard at the agent boundary.

    # Section 2: Same-genre peers — differentiate the SURFACE, keep the spine.
    # Framing matters: telling the model to differ on tone/drive (the genre
    # spine) is what pulled prior same-genre books OFF-genre. These peers share
    # the spine with the new book BY DESIGN; the new book must differ on the
    # concrete premise SKIN (setting / specific mechanic / specific hook), NOT by
    # swapping tone, dropping the golden finger, or grafting on another genre.
    #
    # QUARANTINE: peer settings/trope combos are deliberately WITHHELD from this
    # prompt. Quoting them verbatim was a cross-book pollution channel — old
    # books' concrete imagery (and any motif drift they carried) re-entered
    # every new book as negative examples, and the architect's `setting` output
    # is promoted into the conception-wide automatic story seed. Enforcement
    # lives in the deterministic similarity gate (facets.similarity_score +
    # STORY_FACETS_TOO_SIMILAR retry), which needs no peer text in the prompt.
    if existing_facets:
        parts.append(
            f"\n## Same-Genre Peers\n{len(existing_facets)} book(s) already "
            "occupy this genre. Their concrete settings are intentionally not "
            "shown. Invent your `setting` and specific mechanic from the genre "
            "and the user's input alone; a deterministic similarity gate "
            "rejects near-duplicates and you will receive retry feedback if "
            "you collide. Share the genre spine (tone family / narrative_drive "
            "/ core payoff tropes) — differ ONLY on the concrete `setting` and "
            "the specific mechanic/hook."
        )
    else:
        parts.append("\n## Same-Genre Peers\nNone yet — you have full creative freedom.")

    # (2026-07-31) The hardcoded "Market Trends" keyword feed and the
    # "Available Dimensions & Values" enum catalog were removed by explicit
    # product ruling: framework-baked trend words homogenise same-genre books,
    # and enum catalogs constrain creativity without validated benefit. The
    # schema below keeps the structural FIELDS (required skeleton) while every
    # value is the model's own wording, grounded only in the user's choices.

    # Section 3: Output schema — structural fields required, values freeform.
    parts.append(
        "\n## Required JSON Output Schema\n"
        "```json\n"
        "{\n"
        '  "sub_genres": ["string", "string"],  // 2-3 items, your own wording\n'
        '  "setting": "string",  // Vivid, specific, visual. 20-60 chars\n'
        '  "tone": "string",  // concise tone descriptor in your own words; if the user picked a tone, honour it\n'
        '  "power_system": "string|null",  // this book\'s growth/ability frame in your own words, or null if the genre has none\n'
        '  "relationship_mode": "string",  // how relationships drive this story, your own words\n'
        '  "narrative_drive": "string",  // concise chapter-to-chapter pull; hard max 64 characters\n'
        '  "emotional_register": "string",  // dominant emotional texture, your own words\n'
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

    def _bounded_text(value: object, default: str, limit: int) -> str:
        """Normalize free-form facet labels before strict domain validation.

        These fields classify the story; they are not the story bible.  A model
        occasionally returns a full explanatory sentence despite the schema.
        Keeping the bounded prefix preserves the classification signal and
        avoids spending another LLM call solely on a length violation.
        """

        text = str(value or default).strip()
        return text[:limit].rstrip()

    return StoryFacets(
        primary_genre=primary_genre,
        language=language,
        sub_genres=tuple(data.get("sub_genres", [])),
        setting=data.get("setting", ""),
        tone=_bounded_text(data.get("tone"), "balanced", 64),
        power_system=data.get("power_system"),
        relationship_mode=_bounded_text(
            data.get("relationship_mode"), "no-cp", 64
        ),
        narrative_drive=_bounded_text(
            data.get("narrative_drive"), "progression", 64
        ),
        emotional_register=_bounded_text(
            data.get("emotional_register"), "balanced", 64
        ),
        trope_tags=tuple(data.get("trope_tags", [])),
        platform_style=(
            _bounded_text(data.get("platform_style"), "", 64)
            if data.get("platform_style") is not None
            else None
        ),
        gender_channel=(
            _bounded_text(data.get("gender_channel"), "", 20)
            if data.get("gender_channel") is not None
            else None
        ),
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

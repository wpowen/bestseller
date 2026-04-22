from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.domain.context import SceneWriterContextPacket
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    CharacterModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
)
from bestseller.services.context import build_scene_writer_context_from_models
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology import render_methodology_scene_rules
from bestseller.services.prompt_packs import (
    render_methodology_block,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.invariants import InvariantSeedError, invariants_from_dict
from bestseller.services.chapter_validator import classify_cliffhanger
from bestseller.services.diversity_budget import (
    DEFAULT_HOT_VOCAB_WINDOW,
    DiversityBudget,
    load_diversity_budget,
    save_diversity_budget,
)
from bestseller.services.output_validator import (
    OutputValidator,
    QualityReport,
    ValidationContext,
)
from bestseller.services.quality_gates_config import (
    build_validator_from_config,
    get_quality_gates_config,
)
from bestseller.services.regen_loop import (
    DEFAULT_BUDGET_PER_CHAPTER,
    GlobalBudget,
    RegenerationExhausted,
    regenerate_until_valid,
)
from bestseller.services.write_gate import filter_blocking
from bestseller.services.story_bible import load_scene_story_bible_context
from bestseller.services.writing_profile import (
    is_english_language,
    normalize_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings


def count_words(text: str) -> int:
    han_chars = re.findall(r"[\u4e00-\u9fff]", text)
    latin_words = re.findall(r"[A-Za-z0-9_]+", text)
    return len(han_chars) + len(latin_words)


async def _load_character_name_roster(
    session: AsyncSession, project_id: UUID
) -> frozenset[str]:
    """Collect character names for the project — used as the allowlist for
    ``NamingConsistencyCheck``.

    Returns an empty set on any error so the gate degrades gracefully (the
    check no-ops on empty allowlists rather than flagging every name).
    """

    try:
        rows = await session.scalars(
            select(CharacterModel.name).where(CharacterModel.project_id == project_id)
        )
        return frozenset(str(n).strip() for n in rows if n and str(n).strip())
    except Exception:  # pragma: no cover — defensive guard for gate robustness
        logger.debug(
            "character roster load failed for project %s", project_id, exc_info=True
        )
        return frozenset()


async def _evaluate_chapter_quality_gate(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter_number: int,
    content: str,
) -> str | None:
    """Run L4 + L5 validators + L6 gate resolution on an assembled chapter draft.

    Returns the ``production_state`` string to stamp onto the chapter row:
        * ``"ok"`` — no blocking violations (audit-only findings may exist).
        * ``"blocked"`` — at least one violation resolved to ``block``.
        * ``None`` — gate is disabled or invariants missing; leave state untouched.

    Also threads ``DiversityBudget.recent_cliffhangers`` into the validation
    context so ``CliffhangerRotationCheck`` can see prior chapter kinds, and
    — when the gate passes — records this chapter's opening / cliffhanger
    / vocab / title into the budget so future chapters get honest rotation.

    Phase 1 intentionally keeps the gate non-raising so the pipeline can
    persist the rejected draft for later inspection. A downstream regen
    loop (Phase 2) will read ``production_state == "blocked"`` and retry.
    """

    if not project.invariants_json:
        return None

    gates_cfg = get_quality_gates_config()
    if not gates_cfg.l6_enabled or (not gates_cfg.l4.enabled and not gates_cfg.l5.enabled):
        return None

    try:
        invariants = invariants_from_dict(project.invariants_json)
    except InvariantSeedError:
        logger.warning(
            "chapter %d: invariants payload invalid, skipping quality gate",
            chapter_number,
        )
        return None

    # Build the validator once per call — cheap, lets config-flag flips take
    # effect between chapters without process restarts.
    validator = build_validator_from_config(gates_cfg)
    allowed_names = await _load_character_name_roster(session, project.id)

    # Load diversity budget so CliffhangerRotationCheck can see the recent
    # kinds. Missing budget row → empty tuple, which makes the check no-op.
    try:
        budget = await load_diversity_budget(session, project.id)
    except Exception:  # pragma: no cover — defensive; budget is non-critical
        logger.debug(
            "diversity budget load failed for project %s", project.id, exc_info=True
        )
        budget = None

    window = max(invariants.cliffhanger_policy.no_repeat_within, 0) if invariants else 0
    recent_cliffhangers = (
        budget.recent_cliffhangers(window) if (budget is not None and window > 0) else ()
    )

    # Hype engine context — required by HypeOccurrenceCheck /
    # HypeDiversityCheck. Read the current chapter's assignment from any scene
    # draft's generation_params (all scenes of one chapter share the same
    # pick); recent hype types come from DiversityBudget.hype_moments.
    assigned_hype_type: Any = None
    assigned_hype_recipe: Any = None
    recent_hype_types: tuple[Any, ...] = ()
    try:
        from bestseller.services.hype_engine import HypeType as _HypeTypeEnum
        _chapter_row = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        if _chapter_row is not None:
            _scene_rows = list(
                await session.scalars(
                    select(SceneDraftVersionModel)
                    .join(SceneCardModel, SceneCardModel.id == SceneDraftVersionModel.scene_card_id)
                    .where(
                        SceneCardModel.chapter_id == _chapter_row.id,
                        SceneDraftVersionModel.is_current.is_(True),
                    )
                )
            )
            for _sd in _scene_rows:
                _gp = dict(_sd.generation_params or {})
                if _gp.get("assigned_hype_type"):
                    try:
                        assigned_hype_type = _HypeTypeEnum(str(_gp["assigned_hype_type"]))
                    except ValueError:
                        assigned_hype_type = None
                    # assigned_hype_recipe is the recipe *key* here; checks
                    # only need identity, not the full recipe object.
                    assigned_hype_recipe = _gp.get("assigned_hype_recipe_key")
                    break
        if budget is not None and budget.hype_moments:
            recent_hype_types = tuple(
                m.hype_type for m in list(budget.hype_moments)[-5:]
            )
    except Exception:
        logger.debug(
            "hype context load failed for chapter %d (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    ctx = ValidationContext(
        invariants=invariants,
        chapter_no=chapter_number,
        scope="chapter",
        allowed_names=allowed_names,
        recent_cliffhangers=recent_cliffhangers,
        assigned_hype_type=assigned_hype_type,
        assigned_hype_recipe=assigned_hype_recipe,
        recent_hype_types=recent_hype_types,
    )
    report = validator.validate(content, ctx)

    blocking = filter_blocking(
        report, gates_cfg.l6_gate, chapter_no=chapter_number
    )
    outcome: str
    if blocking:
        logger.warning(
            "chapter %d: blocked by quality gate — %s",
            chapter_number,
            ", ".join(v.code for v in blocking),
        )
        outcome = "blocked"
    else:
        if report.violations:
            logger.info(
                "chapter %d: audit-only findings — %s",
                chapter_number,
                ", ".join(v.code for v in report.violations),
            )
        outcome = "ok"

    # Persist report row for L8 scorecard + Phase 2 promotion analysis.
    # Always write — even when passes — so the dashboard sees coverage.
    await _persist_chapter_quality_report(
        session,
        project_id=project.id,
        chapter_number=chapter_number,
        report=report,
        blocking_codes=tuple(v.code for v in blocking),
    )

    # Only register diversity telemetry for chapters that actually ship —
    # blocked drafts will be re-rendered and we don't want their cliffhanger
    # / vocab contaminating the rotation window.
    if outcome == "ok" and budget is not None:
        try:
            detected_cliffhanger = classify_cliffhanger(
                content, invariants.language if invariants else None
            )
            title_candidate: str | None = None
            project_slug = getattr(project, "slug", None)
            chapter_title = await _lookup_chapter_title(
                session, project.id, chapter_number
            )
            if chapter_title:
                title_candidate = chapter_title
            budget.register_chapter(
                chapter_number,
                opening=None,  # Opening is registered at prompt-construction time (L3).
                cliffhanger=detected_cliffhanger,
                title=title_candidate,
                text=content,
                language=invariants.language if invariants else None,
            )
            await save_diversity_budget(session, budget)
            logger.debug(
                "chapter %d: diversity budget updated (cliffhanger=%s, slug=%s)",
                chapter_number,
                detected_cliffhanger.value if detected_cliffhanger else None,
                project_slug,
            )
        except Exception:  # pragma: no cover — budget update must never fail the gate
            logger.debug(
                "diversity budget save failed for project %s chapter %d",
                project.id,
                chapter_number,
                exc_info=True,
            )

    return outcome


async def _persist_chapter_quality_report(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    report: QualityReport,
    blocking_codes: tuple[str, ...],
) -> None:
    """Insert a ``ChapterQualityReportModel`` row snapshotting this gate pass.

    Failing to persist must NOT fail the gate — scoring infra loss is
    recoverable, a lost draft isn't. Wrapped in a broad ``except`` to
    degrade gracefully; the scorecard job can still read existing rows.
    """

    try:
        chapter_row = await session.execute(
            select(ChapterModel.id).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        chapter_id = chapter_row.scalar_one_or_none()
        if chapter_id is None:
            return
        payload: dict[str, Any] = {
            "violations": [
                {
                    "code": v.code,
                    "severity": v.severity,
                    "location": v.location,
                    "detail": v.detail,
                }
                for v in report.violations
            ],
            "blocking_codes": list(blocking_codes),
        }
        session.add(
            ChapterQualityReportModel(
                chapter_id=chapter_id,
                report_json=payload,
                regen_attempts=0,
                blocks_write=bool(blocking_codes),
            )
        )
        await session.flush()
    except Exception:  # pragma: no cover — telemetry, never fails the gate
        logger.debug(
            "chapter_quality_report persist failed for project %s chapter %d",
            project_id,
            chapter_number,
            exc_info=True,
        )


async def _lookup_chapter_title(
    session: AsyncSession, project_id: UUID, chapter_number: int
) -> str | None:
    try:
        row = await session.execute(
            select(ChapterModel.title).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        title = row.scalar_one_or_none()
        return (title or "").strip() or None
    except Exception:  # pragma: no cover — defensive
        return None


# ---------------------------------------------------------------------------
# Scene-scope L4 validation + L4.5 regen loop.
# ---------------------------------------------------------------------------


async def _build_scene_validator(
    session: AsyncSession, project: ProjectModel
) -> tuple[OutputValidator | None, ValidationContext | None]:
    """Construct an ``OutputValidator`` + ``ValidationContext`` for scene scope.

    Returns ``(None, None)`` when invariants are missing or gates disabled —
    the caller then skips scene-level validation gracefully. The returned
    validator bundles L4 + L5 checks; several checks self-exempt at scene
    scope (length envelope, entity density, cliffhanger rotation) so the
    per-scene runtime cost is dominated by language + naming + dialog
    integrity + POV lock — all fast.
    """

    if not project.invariants_json:
        return None, None

    gates_cfg = get_quality_gates_config()
    if not gates_cfg.l4.enabled and not gates_cfg.l5.enabled:
        return None, None

    try:
        invariants = invariants_from_dict(project.invariants_json)
    except InvariantSeedError:
        return None, None

    validator = build_validator_from_config(gates_cfg)
    allowed_names = await _load_character_name_roster(session, project.id)
    ctx = ValidationContext(
        invariants=invariants,
        chapter_no=None,
        scope="scene",
        allowed_names=allowed_names,
        recent_cliffhangers=(),  # Scene scope never checks cliffhanger rotation.
    )
    return validator, ctx


async def _regenerate_scene_until_valid(
    *,
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter_number: int,
    scene_number: int,
    initial_content: str,
    validator: OutputValidator,
    ctx: ValidationContext,
    system_prompt: str,
    user_prompt: str,
    fallback_content: str,
    workflow_run_id: UUID | None,
    step_run_id: UUID | None,
    model_tier: str,
    context_query: str,
    global_budget: GlobalBudget | None,
) -> tuple[str, str | None, UUID | None, str, int]:
    """Validate ``initial_content`` and, if blocked, regen until it passes.

    Returns ``(final_content, model_name, llm_run_id, provider, regen_count)``.
    When the regen budget is exhausted, returns the best-effort output with a
    ``regen_count`` that reflects attempts made — the caller still persists
    the scene and lets the chapter-level gate / audit loop clean up later.
    """

    gates_cfg = get_quality_gates_config()
    scene_budget = max(0, min(gates_cfg.l4_5.budget_per_chapter, DEFAULT_BUDGET_PER_CHAPTER))

    initial_report = validator.validate(initial_content, ctx)
    if not initial_report.blocks_write or scene_budget == 0:
        return initial_content, None, None, "initial", 0

    last_model_name: str | None = None
    last_llm_run_id: UUID | None = None
    last_provider = "initial"

    async def _regenerator(feedback: str) -> str:
        nonlocal last_model_name, last_llm_run_id, last_provider
        retry_user_prompt = (
            f"{user_prompt}\n\n---\n【质量整改指令】\n{feedback}"
            "\n\n请基于以上整改要求重写整段场景，保持剧情、人物、场景不变。"
        )
        retry = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                model_tier=model_tier,
                system_prompt=system_prompt,
                user_prompt=retry_user_prompt,
                fallback_response=fallback_content,
                prompt_template="scene_writer_regen",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene_number,
                    "context_query": context_query,
                    "model_tier": model_tier,
                    "regen_feedback_codes": [
                        v.code for v in initial_report.violations
                    ],
                },
            ),
        )
        last_model_name = retry.model_name
        last_llm_run_id = retry.llm_run_id
        last_provider = retry.provider
        cleaned = sanitize_novel_markdown_content(
            retry.content, language=_project_language(project)
        ) or fallback_content
        cleaned = strip_scaffolding_echoes(cleaned)
        return cleaned

    async def _validator_fn(text: str) -> QualityReport:
        return validator.validate(text, ctx)

    try:
        result = await regenerate_until_valid(
            initial_output=initial_content,
            initial_report=initial_report,
            regenerator=_regenerator,
            validator=_validator_fn,
            budget=scene_budget,
            global_budget=global_budget,
            context_label=f"scene-{chapter_number}-{scene_number}",
        )
    except RegenerationExhausted as exc:
        logger.warning(
            "scene %d.%d: regen budget exhausted (%d attempts); shipping best-effort",
            chapter_number,
            scene_number,
            len(exc.attempts),
        )
        best = exc.attempts[-1].output if exc.attempts else initial_content
        return (
            best,
            last_model_name,
            last_llm_run_id,
            last_provider or "regen_exhausted",
            max(0, len(exc.attempts) - 1),
        )

    return (
        result.final_output,
        last_model_name,
        last_llm_run_id,
        last_provider if result.regen_count > 0 else "initial",
        result.regen_count,
    )


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK chars ~1 token each, Latin words ~1.3 tokens each."""
    if not text:
        return 0
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9_]+", text))
    punct = len(re.findall(r"[^\w\s]", text))
    return han + int(latin * 1.3) + int(punct * 0.5)


# Priority tiers for context budget enforcement.
# Tier 1: structural contracts & safety — always included.
# Tier 2: recent narrative state — included when budget allows.
# Tier 3: background & enrichment — only when ample room.
_CONTEXT_TIER_1 = frozenset({
    "contract_section",
    "methodology_line",
    "participant_fact_section",
    "contradiction_line",
    "hard_fact_line",
    "knowledge_line",
    "identity_line",
    "phrase_avoidance_line",
    "genre_constraint_line",
    "plan_richness_line",
})
_CONTEXT_TIER_2 = frozenset({
    "recent_scene_section",
    "emotion_track_section",
    "antagonist_plan_section",
    "clue_section",
    "scene_sequel_line",
    "structure_beat_line",
    "pacing_line",
})
_CONTEXT_TIER_3 = frozenset({
    "story_bible_section",
    "arc_section",
    "arc_summary_line",
    "world_snapshot_line",
    "retrieval_section",
    "recent_timeline_section",
    "reader_knowledge_line",
    "relationship_line",
    "subplot_line",
    "ending_line",
    "obligations_line",
    "foreshadow_line",
    "tree_section",
    "pp_line",
    "pp_writer_line",
})


def _budget_context_sections(
    sections: dict[str, str],
    budget_tokens: int,
) -> dict[str, str]:
    """Enforce a token budget on rendered context sections by tier priority.

    Tier 1 sections are always kept.  Tier 2 sections are added next.
    Tier 3 sections fill remaining budget.  Sections that don't fit are blanked.
    """
    result = dict(sections)
    used = 0

    # Pass 1: Tier 1 — always include (sum their tokens, never blank them)
    for key in _CONTEXT_TIER_1:
        used += _estimate_tokens(result.get(key, ""))

    # Pass 2: Tier 2 — add in definition order while budget allows
    for key in _CONTEXT_TIER_2:
        cost = _estimate_tokens(result.get(key, ""))
        if used + cost <= budget_tokens:
            used += cost
        else:
            result[key] = ""

    # Pass 3: Tier 3 — add remaining while budget allows
    for key in _CONTEXT_TIER_3:
        cost = _estimate_tokens(result.get(key, ""))
        if used + cost <= budget_tokens:
            used += cost
        else:
            result[key] = ""

    return result


_STRUCTURED_METADATA_KEYS = (
    "scene_summary",
    "chapter_summary",
    "core_conflict",
    "emotional_shift",
    "contract_alignment",
    "story_task",
    "emotion_task",
    "information_release",
    "tail_hook",
    "closing_hook",
    "entry_state",
    "exit_state",
)

_STRUCTURED_METADATA_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*|__)?"
    r"(?P<key>" + "|".join(_STRUCTURED_METADATA_KEYS) + r")"
    r"(?:\*\*|__)?\s*:\s*.+$",
    re.IGNORECASE,
)

# Chinese structural / meta-commentary terms that should NEVER appear in novel prose.
_CN_META_HEADER_RE = re.compile(
    r"^\s*#{1,4}\s*(?:修订说明|上一版草稿|重写策略|写作说明|场景说明|改写说明|润色说明"
    r"|策划说明|提纲|大纲|剧情任务|情绪任务|写法指导"
    r"|重写第\d+章第?\d*场?)\s*$"
)

_CN_META_LINE_RE = re.compile(
    r"^\s*(?:>+\s*)?[-*]?\s*(?:重写策略|本次任务|修订说明|剧情任务|情绪任务|入场状态|离场状态|收束状态"
    r"|开场状态|场景类型|场景目标|章节目标|本章目标|钩子设计|尾钩|结尾钩子|开场白设计|开场白|设想"
    r"|戏剧反讽意图|过渡方式|主题任务|信息释放|contract|合同式写作约束"
    r"|叙事树上下文|伏笔与兑现约束|关系与情绪推进约束|反派推进约束"
    r"|商业网文硬约束|Prompt Pack"
    r"|小钩子|中钩子|大钩子|章末钩子|场景钩子|章节钩子)\s*[：:].+$"
)

# Lines wrapped in Chinese fullwidth brackets 【...】 that contain planning
# labels (hook summaries, foreshadowing notes, transition markers, etc.).
# These are structural annotations the LLM leaks at scene / chapter
# boundaries and must never appear in published prose.
_CN_BRACKET_META_RE = re.compile(
    r"^\s*【(?:小钩子|中钩子|大钩子|钩子|尾钩|章末钩子|过渡|伏笔|悬念|铺垫"
    r"|章节钩子|场景钩子|hook|设定|本章目标|剧情任务|情绪任务)[：:].*】\s*$"
)

# Scene scaffold headings that must never appear in prose:
#   "## 场景 1：xxx"  /  "### 第三场"  /  "第1场" / "第一场"
# NOTE: This must NOT match chapter headings like "# 第1章：xxx" — those are
# legitimate headings inserted by _format_chapter_heading and must be preserved.
# Duplicate chapter markers (e.g. "第1章 第1章：xxx") are handled separately by
# _CN_DUPLICATE_CHAPTER_MARKER_RE.
_CN_SCAFFOLD_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:第\s*[一二三四五六七八九十百零\d]+\s*场"
    r"|场景\s*[一二三四五六七八九十百零\d]+|结尾钩子|本章目标)"
    r"(?:\s*[:：].*)?$"
)

_CN_META_PROSE_RE = re.compile(
    r"(?:这一场景要完成的剧情任务是|这一场景的情绪任务是|本场景的写作目标是"
    r"|以下是.*的(?:场景|章节|草稿|初稿|提纲|大纲)"
    r"|以下为.*改写后的版本|以上是.*的(?:重写|修订|润色)版本"
    r"|根据(?:修订|重写|润色)(?:说明|要求|策略))"
)

# ---------------------------------------------------------------------------
# English structural / meta-commentary patterns (mirrors the Chinese set above)
# ---------------------------------------------------------------------------

# English structural headers: "## Scene 1:", "Chapter 3:", "Act 2:"
_EN_META_HEADER_RE = re.compile(
    r"^(?:##?\s*)?(?:Scene\s+\d+|Chapter\s+\d+|Act\s+\d+)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)

# English metadata key-value lines: "POV:", "Setting:", "Story Goal:", etc.
_EN_META_LINE_RE = re.compile(
    r"^(?:POV|Point of View|Setting|Time|Location|Participants|"
    r"Story Goal|Emotional Goal|Scene Type|Word Count|Target|"
    r"Character Arc|Plot Purpose|Hook|Conflict Type)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)

# English scaffold headings: "## Scene 1", "### Climax", "# Inciting Incident"
_EN_SCAFFOLD_HEADING_RE = re.compile(
    r"^#{1,3}\s+(?:Scene\s+\d+|Opening|Climax|Resolution|Denouement|"
    r"Rising Action|Falling Action|Inciting Incident)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# English template leak substrings — precise phrases that only originate from
# planning prompts or template fallback prose, never from legitimate fiction.
_EN_TEMPLATE_LEAK_SUBSTRINGS: tuple[str, ...] = (
    "[Author's Note",
    "[Note:",
    "[End of",
    "--- End",
    "Word count:",
    "POV:",
    "Scene goal:",
    "This scene",
    "In this chapter",
    "The purpose of this scene",
    "This chapter establishes",
    "Moving on to",
    "As outlined in the",
    "Per the story bible",
    "According to the plan",
    "The narrative shifts to",
    "scene transitions to",
)

# English meta-reward terms — planning language that should never appear in
# novel prose. Mirrors the Chinese ``_META_REWARD_TERMS`` in reviews.py.
_EN_META_REWARD_TERMS: tuple[str, ...] = (
    "overall tone maintains",
    "chapter goal",
    "scene objective",
    "plot task",
    "emotional task",
    "narrative function",
    "story purpose",
    "character arc progression",
    "this scene serves to",
    "the reader should feel",
)

# Sentences that only ever originate from the fallback template prose in
# ``render_rewritten_scene_markdown`` / ``render_rewritten_chapter_markdown``.
# These are the exact phrases that leaked into chapters 2/3/5/7–13/15/20/25 of
# the apocalypse-supply output. They are precise enough that matching a line
# means the line is template residue, never legitimate prose.
_CN_TEMPLATE_LEAK_SUBSTRINGS: tuple[str, ...] = (
    "重新被推回《",
    "叙事仍采用",
    "这一版重写围绕",
    "third-limited 视角",
    "third-limited视角",
    "third-person limited",
    "叙事采用 third-limited",
    "真正落实到动作、停顿、呼吸和目光变化",
    "金属舱壁传来的冷意",
    "人物说出口的话和没有说出口的话同时构成冲突",
    "上一阶段留下的局势仍压在众人心头",
    "这一章不再只是承接，而是要把冲突继续推向更高层级",
    "章节收束时，",
    # Time-labelled reflection openers used by the fallback outline builder
    # ("第13章中段，程彻…", "第15章开场，周远…", "第22章结尾，…").
    "第1章开场",
    "第1章中段",
    "第1章结尾",
)

# Regex form that captures "第<digits>章(开场|中段|结尾)[，,]" at line start.
# More robust than listing every chapter number as a substring.
_CN_CHAPTER_PHASE_PREFIX_RE = re.compile(
    r"^\s*第\s*\d+\s*章(?:开场|中段|结尾)\s*[，,、]",
)

# Any standalone HTML comment — used by us to mark fallbacks and must never
# appear in published chapters.
_HTML_COMMENT_BLOCK_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)


def sanitize_novel_markdown_content(content_md: str, *, language: str | None = None) -> str:
    """Strip non-fiction structural markers and meta-commentary from novel prose.

    Detects BOTH Chinese and English meta-leaks simultaneously — a scene draft
    could contain mixed-language leaks.

    Order of operations:

    1. Remove all HTML comments (our fallback markers plus any stray notes).
    2. Drop block-level meta sections (CN: ``### 修订说明``; EN: ``### Revision Notes``).
    3. Filter line-by-line to strip structural markers, meta headers, meta
       key-value rows, scaffolding headings, meta-prose sentences and the
       rewrite template sentences in both languages.
    4. Drop the leading-paragraph "第N章中段，XX 重新被推回…" pattern even when
       the rewrite template wasn't flagged by the substring list (catches LLM
       paraphrases of the same prompt seed).
    """
    if not content_md:
        return ""
    # 1. Strip all HTML comments first so our rewrite / scene-draft fallback
    # markers never reach the final output.
    content_md = _HTML_COMMENT_BLOCK_RE.sub("", content_md)

    # 2a. Remove Chinese meta-commentary blocks entirely. These run from the
    # header to end-of-string or the next H2+ header.
    content_md = re.sub(
        r"#{1,4}\s*(?:修订说明|上一版草稿|改写说明|润色说明).*?(?=\n##\s|\Z)",
        "",
        content_md,
        flags=re.DOTALL,
    )
    # 2b. Remove English meta-commentary blocks: "### Revision Notes",
    # "## Author's Notes", "### Rewrite Strategy", etc.
    content_md = re.sub(
        r"#{1,4}\s*(?:Revision Notes?|Author'?s? Notes?|Rewrite Strategy"
        r"|Writing Notes?|Scene Notes?|Draft Notes?)\b.*?(?=\n##\s|\Z)",
        "",
        content_md,
        flags=re.DOTALL | re.IGNORECASE,
    )

    cleaned_lines: list[str] = []
    for raw_line in content_md.splitlines():
        stripped = raw_line.strip()
        # --- Shared / structural metadata (both languages) ---
        if _STRUCTURED_METADATA_LINE_RE.match(stripped):
            continue

        # --- Chinese meta-leak line filters ---
        if _CN_META_HEADER_RE.match(stripped):
            continue
        if _CN_META_LINE_RE.match(stripped):
            continue
        if _CN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        if _CN_META_PROSE_RE.search(stripped):
            continue
        if any(substr in stripped for substr in _CN_TEMPLATE_LEAK_SUBSTRINGS):
            continue
        if _CN_CHAPTER_PHASE_PREFIX_RE.match(stripped):
            continue
        if _CN_BRACKET_META_RE.match(stripped):
            continue

        # --- English meta-leak line filters ---
        if _EN_META_HEADER_RE.match(stripped):
            continue
        if _EN_META_LINE_RE.match(stripped):
            continue
        if _EN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        # Case-insensitive check for English template leak substrings
        stripped_lower = stripped.lower()
        if any(substr.lower() in stripped_lower for substr in _EN_TEMPLATE_LEAK_SUBSTRINGS):
            continue
        # English meta-reward terms leaked into prose
        if any(term in stripped_lower for term in _EN_META_REWARD_TERMS):
            continue

        cleaned_lines.append(raw_line.rstrip())

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # Strip tier-1 AI-flavor kill-on-sight phrases (zero LLM cost).
    from bestseller.services.anti_slop import strip_tier1_slop  # noqa: PLC0415

    # Always strip Chinese slop (can appear even in English drafts from bilingual models).
    cleaned = strip_tier1_slop(cleaned)
    # Also strip English slop when language is English or unknown (covers both).
    if language is None or (language and language.strip().lower().startswith("en")):
        cleaned = strip_tier1_slop(cleaned, language="en-US")
    return cleaned.strip()


# Duplicate / nested chapter-scene markers that should never appear in prose:
#   "第1章 第2场" / "第3章 第3章：碰撞" / "## 第15章 第15章：xxx"
# Matches an entire line (or paragraph-leading fragment) that starts with one
# chapter/scene marker followed by another. Line-level match is enough because
# these leaks always come in at paragraph boundaries.
_CN_DUPLICATE_CHAPTER_MARKER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?第\s*[一二三四五六七八九十百零\d]+\s*[章场]"
    r"[\s·：:、，,]*第\s*[一二三四五六七八九十百零\d]+\s*[章场]"
    r".*$"
)

# Mid-content chapter heading: "# 第N章 XYZ" / "# 第N章：副标题" appearing AFTER
# the first line. The legitimate chapter heading sits at position 0 (prepended
# by _format_chapter_heading). Any subsequent "# 第N章 ..." line is a leaked
# outline note, scene label, or planning task and must be stripped.
# Only matches markdown headings (#{1,4} prefix) — bare "第N章" in prose is ok.
# The character after 章 may be whitespace, Chinese/ASCII colon, or end-of-string.
_CN_MID_CONTENT_CHAPTER_HEADING_RE = re.compile(
    r"^\s*#{1,4}\s*第\s*[一二三四五六七八九十百零\d]+\s*章(?:[\s：:].*|$)"
)

# Prose-wrapped reasoning / rewrite-plan paragraphs, e.g.:
#   "第15章开场，程彻、周远重新被推回《...》第15章的核心冲突..."
# Matches a paragraph (delimited by blank lines) whose FIRST line starts with
# "第N章" followed by planning vocabulary. We erase the whole paragraph so
# multi-line reflections are cleaned in one shot. Anchored to start-of-string
# or double-newline to avoid eating legitimate in-dialogue mentions.
_CN_LEADING_REASONING_PARA_RE = re.compile(
    r"(?:^|\n\n)\s*第\s*[一二三四五六七八九十百零\d]+\s*章[^\n]*?"
    r"(?:开场|的核心冲突|继续|承接|重写围绕|重写的|这一版)[^\n]*"
    r"(?:\n[^\n]+)*?"
    r"(?=\n\n|\Z)"
)

# English mid-content chapter heading: "# Chapter 4: The Clash" appearing after
# the first line. Mirrors _CN_MID_CONTENT_CHAPTER_HEADING_RE.
_EN_MID_CONTENT_CHAPTER_HEADING_RE = re.compile(
    r"^\s*#{1,4}\s*Chapter\s+\d+(?:[\s:：].*|$)",
    re.IGNORECASE,
)

# English leading reasoning paragraph: "Chapter 5 opens with..." / "This
# rewrite focuses on..." — AI reflection that leaked as the first paragraph.
_EN_LEADING_REASONING_PARA_RE = re.compile(
    r"(?:^|\n\n)\s*(?:Chapter\s+\d+\s+(?:opens|begins|continues|picks up)|"
    r"This\s+(?:rewrite|revision|draft)\s+(?:focuses|centers|aims)|"
    r"In\s+this\s+(?:rewrite|revision|version))[^\n]*"
    r"(?:\n[^\n]+)*?"
    r"(?=\n\n|\Z)",
    re.IGNORECASE,
)

# Additional phrase-pair rules for has_meta_leak. Each tuple is a list of
# phrases that must all be present for the pair to count as a leak — this
# avoids false positives where "视角" or "开场" appears in legitimate prose.
_HAS_META_PHRASE_PAIRS: tuple[tuple[str, ...], ...] = (
    ("这一版", "重写"),
    ("重写围绕",),
    ("叙事仍采用",),
    ("third-limited",),
    ("third limited",),
    ("third-person limited",),
    ("核心冲突", "第", "章"),  # "第X章的核心冲突"
    ("开场", "重新被推回"),
)

# English phrase-pair rules: mirrors _HAS_META_PHRASE_PAIRS for English content.
_EN_HAS_META_PHRASE_PAIRS: tuple[tuple[str, ...], ...] = (
    ("this rewrite", "focuses on"),
    ("scene objective",),
    ("chapter goal",),
    ("narrative function",),
    ("the reader should feel",),
    ("this scene serves to",),
    ("character arc", "progression"),
    ("per the story bible",),
    ("according to the plan",),
)


def strip_scaffolding_echoes(content_md: str) -> str:
    """Strip duplicate chapter markers and leading AI-reasoning paragraphs.

    This runs AFTER ``sanitize_novel_markdown_content`` and is the last
    regex-level net before falling back to LLM-based cleanup. It catches
    leaks in both Chinese and English:

    1. Duplicate / nested chapter-scene headers like "第1章 第2场" or
       "第3章 第3章：碰撞", which the sanitizer's line-start regex can't
       match when both markers land on the same line.
    2. Mid-content chapter headings — CN: "# 第4章 关键碰撞";
       EN: "# Chapter 4: The Clash" — leaked outline notes / planning
       tasks that mimic chapter headings. The first-line heading is preserved.
    3. Prose-wrapped AI reflection paragraphs — CN: "第15章开场，XXX 重新
       被推回 ..."; EN: "Chapter 5 opens with ..." / "This rewrite focuses
       on ..." — where the LLM leaked its rewrite plan as the first paragraph.
    """
    if not content_md:
        return content_md

    # Erase duplicate chapter markers and mid-content chapter headings.
    cleaned_lines: list[str] = []
    first_line_seen = False
    for line in content_md.splitlines():
        stripped = line.strip()
        # Chinese duplicate / nested chapter-scene markers
        if _CN_DUPLICATE_CHAPTER_MARKER_RE.match(stripped):
            continue
        # Strip mid-content "# 第N章 ..." headings (leaked outline/planning
        # notes). The first non-blank line is skipped — it may be the
        # legitimate chapter heading from _format_chapter_heading.
        if first_line_seen and _CN_MID_CONTENT_CHAPTER_HEADING_RE.match(stripped):
            continue
        # Strip mid-content "# Chapter N ..." headings (English equivalent)
        if first_line_seen and _EN_MID_CONTENT_CHAPTER_HEADING_RE.match(stripped):
            continue
        # Strip English scaffold headings that survived the line-level filter
        # (e.g. "## Scene 3" / "### Climax" appearing mid-content)
        if first_line_seen and _EN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        if stripped:
            first_line_seen = True
        cleaned_lines.append(line)
    content_md = "\n".join(cleaned_lines)

    # Erase prose-wrapped reasoning paragraphs (Chinese). Loop until stable in
    # case multiple reflection paragraphs stack at the top.
    while True:
        new_content = _CN_LEADING_REASONING_PARA_RE.sub("", content_md, count=1)
        if new_content == content_md:
            break
        content_md = new_content

    # Erase prose-wrapped reasoning paragraphs (English).
    while True:
        new_content = _EN_LEADING_REASONING_PARA_RE.sub("", content_md, count=1)
        if new_content == content_md:
            break
        content_md = new_content

    content_md = re.sub(r"\n{3,}", "\n\n", content_md)

    # Normalize quotation marks to a consistent format
    try:
        from bestseller.services.output_hygiene import normalize_quote_format
        content_md = normalize_quote_format(content_md, language=language)
    except Exception:
        pass  # Non-fatal

    return content_md.strip()


logger = logging.getLogger(__name__)

# Shared prohibition block injected into all writer / editor system prompts.
# Uses triple-quoted string to safely contain Chinese fullwidth quotes.
_NOVEL_OUTPUT_PROHIBITION = """\
【严禁出现以下内容】：
- 不得出现\u201c钩子\u201d\u201c开场白\u201d\u201c设想\u201d\u201c尾钩\u201d\u201c入场状态\u201d\u201c离场状态\u201d\u201c收束状态\u201d\u201c剧情任务\u201d\u201c情绪任务\u201d等策划术语
- 严禁在章节或场景末尾输出【小钩子：...】【中钩子：...】【大钩子：...】等钩子摘要标记——这些是内部规划标签，绝不能出现在正文中
- 不得出现\u201c修订说明\u201d\u201c重写策略\u201d\u201c上一版草稿\u201d\u201c场景说明\u201d\u201c写法指导\u201d等元评论
- 不得出现\u201c这一场景要完成的剧情任务是\u201d\u201c以下是\u201d\u201c以上是\u201d等解释性前缀
- 不得出现 entry_state / exit_state / contract / scene_type 等英文结构化标签
- 不得输出 Markdown 标题标记（# 或 ##）——正文中不需要章节标题、场景标题或任何层级标题
- 不得把\u201c章节目标\u201d\u201c场景标题\u201d\u201c卷目标\u201d原文搬入正文——这些信息仅供理解意图
- 所有策划信息（场景目的、情绪目标、contract 约束）仅供你理解意图，严禁直接输出到正文
- 严禁使用AI味套话：\u201c显而易见\u201d\u201c毫无疑问\u201d\u201c不言而喻\u201d\u201c心中五味杂陈\u201d\u201c空气仿佛凝固了\u201d等
- 严禁堆砌虚弱修饰副词（缓缓、轻轻、微微、淡淡），同类副词每千字不超过2次
- 严禁模板式微表情描写（眼眶微红、嘴角上扬、瞳孔骤缩），用具体动作替代
- 每个角色说话必须有自己的风格——参考角色语言指纹，不同角色的对话必须可区分
- 输出中只允许出现：叙事散文、对话、动作描写、环境描写、内心活动

【AI套话黑名单——以下表达绝对禁止】：
- "血液仿佛凝固了" / "血液冰封" / "浑身的血液都冷了"
- "空气仿佛凝固了" / "时间仿佛静止了" / "周围的一切仿佛都消失了"
- "心中五味杂陈" / "心中百感交集" / "眼眶不由得湿润了"
- "一股莫名的情绪" / "一种说不清的感觉" / "一阵莫名的恐惧"
- "电流般的感觉" / "触电般的感觉" / "沉甸甸的"
- "仿佛有一只无形的手" / "像是被什么东西攫住了"
用具体、原创、从故事世界中生长出来的意象替代这些套话。

【系统面板/游戏界面规则】（如适用 LitRPG/GameLit 类型）：
- 系统面板/代码块每场最多出现 2-3 次，不是每段一个
- 面板必须短小（不超过 4-5 行），不要大段倾倒状态数据
- 故事必须脱离面板独立成立——用动作、角色反应传递危险和信息
- 先写角色的身体/情绪反应，再出面板。不要用面板代替张力
"""

_NOVEL_OUTPUT_PROHIBITION_EN = """\
FORBIDDEN OUTPUT — the following must NEVER appear in the prose:
- Do not output planning terms: "hook", "opening beat", "premise", "tail hook", "entry state", "exit state", "closing state", "story task", "emotion task"
- Do not output hook summary labels at the end of scenes or chapters: [Small Hook: ...] [Medium Hook: ...] [Large Hook: ...] — these are internal planning tags and must never appear in prose
- Do not output meta-commentary: "revision notes", "rewrite strategy", "previous draft", "scene notes", "writing guidance"
- Do not output explanatory prefixes: "The story task for this scene is", "The following is", "The above is"
- Do not output structural labels: entry_state / exit_state / contract / scene_type
- Do not output Markdown heading markers (# or ##) — the prose does not need chapter titles, scene titles, or any heading levels
- Do not copy "chapter goal", "scene title", "volume goal" text verbatim into the prose — that information is for your understanding only
- All planning information (scene purpose, emotional goals, contract constraints) is for your understanding ONLY — never output it into the prose
- Avoid weak filler adverbs (slowly, gently, slightly, softly) — no more than 2 uses of the same adverb per 1000 words
- Avoid template micro-expressions (eyes reddened, lips curled, pupils constricted) — use specific actions instead
- Every character must speak with their own distinct voice — reference the character voice fingerprint; different characters' dialogue must be distinguishable
- Output ONLY: narrative prose, dialogue, action, environmental description, internal thought

BANNED AI CLICHÉS — these phrases instantly mark text as machine-generated. NEVER use them:
- "blood crystallized" / "blood ran cold" / "blood turned to ice"
- "words landed like a stone in still water" / "words hung in the air"
- "cold as vacuum" / "frozen fire" / "liquid fire"
- "something almost like [emotion]" / "something that might have been [emotion]"
- "the world narrowed to" / "time seemed to slow" / "the air itself seemed to"
- "a laugh that held no humor" / "a smile that didn't reach their eyes"
- "electricity crackled between them" / "tension thick enough to cut"
- "It goes without saying" / "Without a doubt" / "Needless to say"
- "every fiber of their being" / "a weight settled in their chest"
- "the silence was deafening" / "pregnant pause" / "comfortable silence"
Replace these with concrete, specific, original imagery drawn from the story's world.

SYSTEM UI / GAME INTERFACE RULE (for LitRPG/GameLit genres):
- System panels, stat blocks, and notifications may appear at most 2-3 times per scene (NOT per paragraph).
- System text must be SHORT (max 4-5 lines) — never a full-screen dump of stats, quests, and warnings.
- The story must function WITHOUT the panels — use prose, action, and character reaction to convey danger and information.
- Never use system panels as a substitute for tension. Show the character's physical/emotional reaction FIRST, panel SECOND.
- If a scene has more than 3 panels, rewrite the excess as narrative prose or internal thought.
"""

# Quick heuristic: if any of these terms appear in the output, it likely
# contains non-fiction meta-commentary that slipped through the regex filter.
_META_LEAK_KEYWORDS = (
    # --- Chinese ---
    "修订说明", "上一版草稿", "重写策略", "本次任务",
    "剧情任务是", "情绪任务是", "入场状态：", "离场状态：",
    "收束状态：", "开场状态：", "entry_state", "exit_state",
    "scene_summary", "contract_alignment", "tail_hook",
    "closing_hook", "story_task", "emotion_task",
    # Hook summary labels leaked at scene / chapter boundaries.
    "小钩子", "中钩子", "大钩子",
    # Rewrite-plan vocabulary that leaked into the body of a rewritten chapter
    # (see reviews.build_chapter_rewrite_prompts — the LLM occasionally
    # paraphrases rewrite_strategy back at us instead of writing prose).
    "这一版重写", "重写围绕", "叙事仍采用",
    "third-limited", "third limited", "third-person limited",
    # --- English ---
    "[Author's Note",
    "[Note:",
    "[End of",
    "Word count:",
    "POV:",
    "Scene goal:",
    "The purpose of this scene",
    "This chapter establishes",
    "Per the story bible",
    "According to the plan",
    "This scene serves to",
    "The reader should feel",
    "In this rewrite",
    "This revision focuses",
    "Scene objective:",
    "Chapter goal:",
    "Narrative function:",
    "As per the outline",
    "Based on the story bible",
)


def has_meta_leak(content_md: str) -> bool:
    """Return True if *content_md* still contains non-fiction meta-commentary.

    Scans for both Chinese and English meta-leak indicators simultaneously.
    """
    if any(kw in content_md for kw in _META_LEAK_KEYWORDS):
        return True
    # Chinese phrase-pair check: each rule fires only if EVERY phrase in the
    # tuple is present. This lets us flag ambiguous single words ("开场",
    # "视角") only when they co-occur with other planning vocabulary.
    if any(
        all(phrase in content_md for phrase in phrases)
        for phrases in _HAS_META_PHRASE_PAIRS
    ):
        return True
    # English phrase-pair check (case-insensitive for natural prose matching).
    content_lower = content_md.lower()
    return any(
        all(phrase in content_lower for phrase in phrases)
        for phrases in _EN_HAS_META_PHRASE_PAIRS
    )


async def validate_and_clean_novel_content(
    session: AsyncSession,
    settings: AppSettings,
    content_md: str,
    *,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> str:
    """LLM-based content validation gate.

    Called after ``sanitize_novel_markdown_content`` only when the heuristic
    ``has_meta_leak`` still detects non-fiction markers.  The critic role
    rewrites the offending paragraphs, keeping story content intact.
    """
    # Fast path: no leak detected — skip LLM call entirely.
    if not has_meta_leak(content_md):
        return content_md

    logger.warning(
        "Meta-commentary leak detected in output (len=%d), invoking LLM cleanup",
        len(content_md),
    )

    system_prompt = (
        "你是小说正文校验编辑。你的唯一任务是删除或改写混入正文的非小说内容。\n"
        "非小说内容包括但不限于：\n"
        "1. 策划术语：钩子、开场白、设想、尾钩、剧情任务、情绪任务、入场状态、离场状态、收束状态\n"
        "2. 元评论：修订说明、重写策略、上一版草稿、写法指导、场景说明\n"
        "3. 英文结构标签：entry_state、exit_state、scene_summary、contract 等\n"
        "4. 解释性前缀/后缀：\u201c以下是\u201d\u201c以上是\u201d\u201c这一场景要完成的剧情任务是\u201d\n\n"
        "处理规则：\n"
        "- 如果某个段落完全是元评论/策划说明，直接删除整段\n"
        "- 如果某个段落混合了小说正文和策划术语，只删除策划术语部分，保留小说正文\n"
        "- 不要改变小说正文的情节、对话、描写\n"
        "- 不要添加新内容\n"
        "- 输出清理后的完整正文，直接输出 Markdown，不要解释你做了什么\n"
    )
    user_prompt = f"以下是需要校验的小说正文：\n\n{content_md}"

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=content_md,
            prompt_template="content_validation",
            prompt_version="1.0",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={"task": "meta_leak_cleanup"},
        ),
    )
    cleaned = sanitize_novel_markdown_content(completion.content)
    if not cleaned:
        logger.warning("LLM cleanup returned empty content, falling back to original")
        return content_md
    return cleaned


def _render_state(state: dict[str, Any]) -> str:
    if not state:
        return "暂无明确状态"
    return "；".join(f"{key}: {value}" for key, value in state.items())


def _render_purpose(purpose: dict[str, Any], key: str, fallback: str) -> str:
    value = purpose.get(key)
    return str(value) if value else fallback


def _normalize_fragment(text: str) -> str:
    return text.strip().rstrip("。！？!?")


def _render_story_bible_section(
    story_bible_context: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    if not story_bible_context:
        return ""
    is_en = is_english_language(language)
    lines: list[str] = []
    if story_bible_context.get("logline"):
        lines.append(
            f"{'Series spine' if is_en else '全书主线'}：{story_bible_context['logline']}"
        )
    backbone = story_bible_context.get("world_backbone") or {}
    if backbone.get("mainline_drive"):
        lines.append(
            f"{'Mainline drive' if is_en else '全书主旋律'}：{backbone['mainline_drive']}"
        )
    if backbone.get("thematic_melody"):
        lines.append(
            f"{'Thematic melody' if is_en else '主题旋律'}：{backbone['thematic_melody']}"
        )
    if backbone.get("invariant_elements"):
        lines.append(
            f"{'Do-not-break elements' if is_en else '不可轻改元素'}："
            f"{(', ' if is_en else '、').join(str(item) for item in backbone['invariant_elements'][:5])}"
        )
    if story_bible_context.get("themes"):
        lines.append(
            f"{'Themes' if is_en else '主题'}："
            f"{(', ' if is_en else '、').join(str(item) for item in story_bible_context['themes'])}"
        )
    volume = story_bible_context.get("volume") or {}
    if volume.get("goal"):
        lines.append(f"{'Volume goal' if is_en else '本卷目标'}：{volume['goal']}")
    if volume.get("obstacle"):
        lines.append(f"{'Volume obstacle' if is_en else '本卷障碍'}：{volume['obstacle']}")
    frontier = story_bible_context.get("volume_frontier") or {}
    if frontier.get("frontier_summary"):
        lines.append(
            f"{'Current world frontier' if is_en else '当前世界边界'}：{frontier['frontier_summary']}"
        )
    if frontier.get("expansion_focus"):
        lines.append(
            f"{'Current expansion focus' if is_en else '当前扩张焦点'}：{frontier['expansion_focus']}"
        )
    if frontier.get("active_locations"):
        lines.append(
            f"{'Active locations' if is_en else '当前主要舞台'}："
            f"{(', ' if is_en else '、').join(str(item) for item in frontier['active_locations'][:4])}"
        )
    if frontier.get("active_factions"):
        lines.append(
            f"{'Active factions' if is_en else '当前活跃势力'}："
            f"{(', ' if is_en else '、').join(str(item) for item in frontier['active_factions'][:4])}"
        )
    rules = story_bible_context.get("world_rules") or []
    if rules:
        rendered_rules = "；".join(
            f"{item['name']}({item['story_consequence'] or item['description']})"
            for item in rules[:3]
        )
        lines.append(f"{'Key world rules' if is_en else '关键世界规则'}：{rendered_rules}")
    reveal_status = story_bible_context.get("deferred_reveal_status") or {}
    hidden_reveal_count = reveal_status.get("hidden_count")
    if isinstance(hidden_reveal_count, int) and hidden_reveal_count > 0:
        lines.append(
            (
                f"There are still {hidden_reveal_count} deferred reveals that must stay hidden; preserve them through anomalies and suspense only."
                if is_en
                else f"仍有 {hidden_reveal_count} 个延后揭示不得提前说破，只能通过异常与悬念间接保留。"
            )
        )
    next_gate = story_bible_context.get("next_expansion_gate") or {}
    if next_gate.get("condition_summary"):
        lines.append(
            f"{'Next expansion gate' if is_en else '下一层世界解锁条件'}：{next_gate['condition_summary']}"
        )
    # Render canonical character definitions from cast_spec so the LLM always
    # sees immutable character attributes (background, role, relationships).
    cast_spec = story_bible_context.get("cast_spec") or {}
    cast_characters = cast_spec.get("characters") or []
    if not cast_characters:
        # Fallback: try protagonist + allies + antagonists keys
        for _key in ("protagonist", "allies", "antagonists"):
            _val = cast_spec.get(_key)
            if isinstance(_val, dict):
                cast_characters.append(_val)
            elif isinstance(_val, list):
                cast_characters.extend(item for item in _val if isinstance(item, dict))
    if cast_characters:
        cast_lines: list[str] = []
        for char in cast_characters[:6]:
            parts = [f"{char.get('name', 'Unknown' if is_en else '未知')}"]
            if char.get("role"):
                parts.append(f"{'Role' if is_en else '角色'}:{char['role']}")
            if char.get("background"):
                bg = str(char["background"])[:80]
                parts.append(f"{'Background' if is_en else '背景'}:{bg}")
            cast_lines.append((" | " if is_en else "｜").join(parts))
        lines.append(
            ("Core cast anchors (do not alter):\n" if is_en else "【核心角色设定（不可更改）】：\n")
            + "\n".join(cast_lines)
        )
    participants = story_bible_context.get("participants") or []
    if participants:
        rendered_participants = "；".join(
            (
                f"{item['name']}[{item.get('role') or 'character'}]"
                f" {'Background' if is_en else '背景'}:{(item.get('background') or ('undefined' if is_en else '未定义'))[:40]}"
                f" {'Goal' if is_en else '目标'}:{item.get('goal') or ('undefined' if is_en else '未定义')}"
                f" {'Arc' if is_en else '弧线状态'}:{item.get('arc_state') or ('undefined' if is_en else '未定义')}"
                f" {'Power' if is_en else '力量层级'}:{item.get('power_tier') or ('undefined' if is_en else '未定义')}"
                f" {'Emotion' if is_en else '情绪'}:{item.get('emotional_state') or ('undefined' if is_en else '未定义')}"
            )
            for item in participants[:4]
        )
        lines.append(
            f"{'Current participant states' if is_en else '参与角色当前状态'}：{rendered_participants}"
        )
        voice_lines: list[str] = []
        for item in participants[:4]:
            vp = item.get("voice_profile") or {}
            parts: list[str] = []
            if vp.get("speech_register"):
                parts.append(f"{'Register' if is_en else '语言层次'}:{vp['speech_register']}")
            if vp.get("verbal_tics"):
                parts.append(f"{'Verbal tics' if is_en else '口头禅'}:{'/'.join(vp['verbal_tics'][:3])}")
            if vp.get("sentence_style"):
                parts.append(f"{'Sentence style' if is_en else '句式'}:{vp['sentence_style']}")
            if vp.get("emotional_expression"):
                parts.append(f"{'Emotional expression' if is_en else '情绪表达'}:{vp['emotional_expression']}")
            if vp.get("mannerisms"):
                parts.append(f"{'Mannerisms' if is_en else '习惯动作'}:{'/'.join(vp['mannerisms'][:2])}")
            if parts:
                voice_lines.append(f"{item['name']}{' - ' if is_en else '——'}{(' / ' if is_en else '，').join(parts)}")
        if voice_lines:
            lines.append(
                ("Character voice fingerprints (dialogue must stay distinct):\n" if is_en else "角色语言指纹（对话必须体现区分度）：\n")
                + "\n".join(voice_lines)
            )
    relationships = story_bible_context.get("relationships") or []
    if relationships:
        rendered_relationships = "；".join(
            (
                f"{item.get('relationship_type') or '关系'}:"
                f"{item.get('tension_summary') or item.get('private_reality') or '存在潜在张力'}"
            )
            for item in relationships[:3]
        )
        lines.append(
            f"{'Current relationship tension' if is_en else '当前关系张力'}：{rendered_relationships}"
        )
    return "\n".join(lines)


def _render_retrieval_section(chunks: list[dict[str, Any]] | None) -> str:
    if not chunks:
        return ""
    return "\n".join(
        f"- [{chunk.get('source_type')}] {chunk.get('chunk_text')}"
        for chunk in chunks[:4]
    )


def _render_recent_scene_section(recent_scene_summaries: list[dict[str, Any]] | None) -> str:
    if not recent_scene_summaries:
        return ""
    lines: list[str] = []
    for item in recent_scene_summaries[:8]:
        if not item.get("summary"):
            continue
        line = (
            f"- 第{item.get('chapter_number')}章第{item.get('scene_number')}场"
            f" {item.get('scene_title') or ''}：{item.get('summary')}"
        )
        opening = item.get("opening_lines")
        if opening:
            line += f"\n  [开头原文] {opening}"

        # extended_tail is provided for the immediately preceding same-chapter
        # scene — it contains the last ~1000 chars of actual prose, covering
        # dialog and action that AI-generated summaries routinely omit.
        extended_tail = item.get("extended_tail")
        if extended_tail:
            line += (
                f"\n  [前一场结尾原文 — 以下内容已写入，新场景严禁逐字重复]\n"
                f"  ---\n"
                f"  {extended_tail.replace(chr(10), chr(10) + '  ')}\n"
                f"  ---"
            )
        else:
            # Fallback to shorter closing_lines for cross-chapter preceding scenes
            closing = item.get("closing_lines")
            if closing:
                line += f"\n  [结尾原文（禁止在新场景中重复这段内容）] {closing}"

        lines.append(line)
    return "\n".join(lines)


def _render_timeline_section(timeline_events: list[dict[str, Any]] | None) -> str:
    if not timeline_events:
        return ""
    return "\n".join(
        (
            f"- {item.get('story_time_label') or '未指定时间'} / {item.get('event_name')}："
            f"{'；'.join(item.get('consequences') or []) or item.get('summary') or '推进主线'}"
        )
        for item in timeline_events[:4]
    )


def _render_participant_fact_section(participant_facts: list[dict[str, Any]] | None) -> str:
    if not participant_facts:
        return ""
    return "\n".join(
        (
            f"- {item.get('subject_label')} / {item.get('predicate')}："
            f"{item.get('value')}"
        )
        for item in participant_facts[:6]
    )


def _render_arc_section(
    plot_arcs: list[dict[str, Any]] | None,
    arc_beats: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    sections: list[str] = []
    if plot_arcs:
        sections.append("Active narrative lines:" if is_en else "激活叙事线：")
        sections.extend(
            f"- [{item.get('arc_type')}] {item.get('name')}：{item.get('promise')}"
            for item in plot_arcs[:4]
        )
    if arc_beats:
        sections.append("Current arc beats:" if is_en else "当前承担的叙事节拍：")
        sections.extend(
            (
                f"- {item.get('arc_code')} / {item.get('beat_kind')}：{item.get('summary')}"
                + (
                    f" / {'emotion' if is_en else '情绪'}:{item.get('emotional_shift')}"
                    if item.get("emotional_shift")
                    else ""
                )
            )
            for item in arc_beats[:6]
        )
    return "\n".join(sections)


def _render_clue_section(
    unresolved_clues: list[dict[str, Any]] | None,
    planned_payoffs: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    sections: list[str] = []
    if unresolved_clues:
        sections.append("Open clues:" if is_en else "未回收伏笔：")
        sections.extend(
            f"- {item.get('clue_code')} / {item.get('label')}：{item.get('description')}"
            for item in unresolved_clues[:6]
        )
    if planned_payoffs:
        sections.append("Near-term payoffs:" if is_en else "近期应兑现节点：")
        sections.extend(
            f"- {item.get('payoff_code')} / {item.get('label')}：{item.get('description')}"
            for item in planned_payoffs[:4]
        )
    return "\n".join(sections)


def _render_emotion_track_section(
    emotion_tracks: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    if not emotion_tracks:
        return ""
    is_en = is_english_language(language)
    lines = ["Current relationship/emotion lines:" if is_en else "当前关系/情绪线："]
    lines.extend(
        (
            f"- [{item.get('track_type')}] {item.get('title')}：{item.get('summary')}"
            f" / trust={item.get('trust_level')}"
            f" / attraction={item.get('attraction_level')}"
            f" / conflict={item.get('conflict_level')}"
            f" / stage={item.get('intimacy_stage')}"
        )
        for item in emotion_tracks[:4]
    )
    return "\n".join(lines)


def _render_antagonist_plan_section(
    antagonist_plans: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    if not antagonist_plans:
        return ""
    is_en = is_english_language(language)
    lines = ["Current antagonist pressure:" if is_en else "当前反派推进："]
    lines.extend(
        (
            f"- [{item.get('threat_type')}] {item.get('title')}：{item.get('goal')}"
            f" / {'current move' if is_en else '当前动作'}:{item.get('current_move')}"
            f" / {'next move' if is_en else '下一步'}:{item.get('next_countermove')}"
        )
        for item in antagonist_plans[:4]
    )
    return "\n".join(lines)


def _render_contract_section(
    chapter_contract: dict[str, Any] | None,
    scene_contract: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    sections: list[str] = []
    if chapter_contract:
        sections.append(
            f"{'Chapter contract' if is_en else '章节 contract'}："
            f"{chapter_contract.get('contract_summary') or ('This chapter must carry a clear narrative task.' if is_en else '本章需要承担明确叙事任务')}"
        )
        if chapter_contract.get("core_conflict"):
            sections.append(f"- {'Chapter core conflict' if is_en else '章节核心冲突'}：{chapter_contract['core_conflict']}")
        if chapter_contract.get("closing_hook"):
            sections.append(f"- {'Chapter closing hook' if is_en else '章节尾钩'}：{chapter_contract['closing_hook']}")
    if scene_contract:
        sections.append(
            f"{'Scene contract' if is_en else '场景 contract'}："
            f"{scene_contract.get('contract_summary') or ('This scene must produce a clean forward move.' if is_en else '本场必须完成清晰推进')}"
        )
        if scene_contract.get("core_conflict"):
            sections.append(f"- {'Scene core conflict' if is_en else '场景核心冲突'}：{scene_contract['core_conflict']}")
        if scene_contract.get("tail_hook"):
            sections.append(f"- {'Scene tail hook' if is_en else '场景尾钩'}：{scene_contract['tail_hook']}")
        if scene_contract.get("thematic_task"):
            sections.append(
                (
                    f"- Thematic task: {scene_contract['thematic_task']} (express it through action and imagery, never direct sermonizing)"
                    if is_en
                    else f"- 主题任务：{scene_contract['thematic_task']}（通过行动和意象表达，不要直白说教）"
                )
            )
        if scene_contract.get("dramatic_irony_intent"):
            sections.append(
                (
                    f"- Dramatic irony: {scene_contract['dramatic_irony_intent']} (the reader knows this before the character)"
                    if is_en
                    else f"- 戏剧反讽：{scene_contract['dramatic_irony_intent']}（读者知道但角色不知道）"
                )
            )
        if scene_contract.get("transition_type"):
            sections.append(f"- {'Transition type' if is_en else '过渡方式'}：{scene_contract['transition_type']}")
        if scene_contract.get("subplot_codes"):
            sections.append(
                f"- {'Subplots advanced' if is_en else '推进副线'}："
                f"{(', ' if is_en else '、').join(scene_contract['subplot_codes'])}"
            )
    return "\n".join(sections)


def _render_tree_section(
    tree_context_nodes: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    if not tree_context_nodes:
        return ""
    is_en = is_english_language(language)
    return "\n".join(
        (
            f"- {item.get('node_path')} [{item.get('node_type')}]："
            f"{item.get('summary') or item.get('title') or ('No summary' if is_en else '无摘要')}"
        )
        for item in tree_context_nodes[:8]
    )


def _render_hard_fact_snapshot_section(
    snapshot: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    """Render the chapter-end hard-fact snapshot block.

    ``snapshot`` is the JSON-serialized form of
    :class:`bestseller.domain.context.ChapterStateSnapshotContext`.  Returns an
    empty string when there is nothing to inject so the caller can safely
    concatenate.
    """
    if not snapshot:
        return ""
    facts = snapshot.get("facts") or []
    if not facts:
        return ""
    is_en = is_english_language(language)
    chapter_number = snapshot.get("chapter_number")
    header = (
        f"=== Locked fact state (from the end of Chapter {chapter_number}; must be obeyed exactly with no contradictions) ==="
        if chapter_number is not None and is_en
        else (
            f"=== 当前事实状态（来自第 {chapter_number} 章末 — 必须严格遵守，不得前后矛盾）==="
            if chapter_number is not None
            else (
                "=== Locked fact state (from the previous chapter end; must be obeyed exactly with no contradictions) ==="
                if is_en
                else "=== 当前事实状态（来自上一章末 — 必须严格遵守，不得前后矛盾）==="
            )
        )
    )
    lines: list[str] = [header]
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        name = fact.get("name")
        value = fact.get("value")
        if not name or value is None:
            continue
        subject = fact.get("subject")
        unit = fact.get("unit")
        notes = fact.get("notes")
        prefix = f"[{subject}] " if subject else ""
        unit_suffix = f" {unit}" if unit else ""
        notes_suffix = f"  // {notes}" if notes else ""
        lines.append(f"- {prefix}{name}: {value}{unit_suffix}{notes_suffix}")
    lines.append(
        (
            "=== Any change to quantities, locations, or possessions must have a reader-visible trigger event in this chapter (trade, combat, elapsed time, etc.) ==="
            if is_en
            else "=== 任何数值/位置/物品变化都必须在本章正文里给出读者可见的触发事件（交易、战斗、时间流逝等）==="
        )
    )
    return "\n".join(lines)


def _resolve_project_writing_profile(project: Any, style_guide: StyleGuideModel | None) -> Any:
    metadata = getattr(project, "metadata_json", {}) or {}
    raw_profile = metadata.get("writing_profile") if isinstance(metadata, dict) else None
    fallback_style = (
        {
            "style": {
                "pov_type": getattr(style_guide, "pov_type", "third-limited"),
                "tense": getattr(style_guide, "tense", "present"),
                "tone_keywords": list(getattr(style_guide, "tone_keywords", []) or []),
                "prose_style": getattr(style_guide, "prose_style", "commercial-web-serial"),
                "sentence_style": getattr(style_guide, "sentence_style", "mixed"),
                "info_density": getattr(style_guide, "info_density", "medium"),
                "dialogue_ratio": float(getattr(style_guide, "dialogue_ratio", 0.4)),
                "taboo_topics": list(getattr(style_guide, "taboo_topics", []) or []),
                "taboo_words": list(getattr(style_guide, "taboo_words", []) or []),
                "reference_works": list(getattr(style_guide, "reference_works", []) or []),
                "custom_rules": list(getattr(style_guide, "custom_rules", []) or []),
            }
        }
        if style_guide is not None
        else None
    )
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


def _scene_participant_text(participants: list[str] | None, *, language: str) -> str:
    if not participants:
        return "relevant characters" if is_english_language(language) else "相关角色"
    return ", ".join(participants) if is_english_language(language) else "、".join(participants)


def render_scene_draft_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    style_guide: StyleGuideModel | None,
    story_bible_context: dict[str, Any] | None = None,
    retrieval_context: list[dict[str, Any]] | None = None,
    recent_scene_summaries: list[dict[str, Any]] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    participant_canon_facts: list[dict[str, Any]] | None = None,
    active_plot_arcs: list[dict[str, Any]] | None = None,
    active_arc_beats: list[dict[str, Any]] | None = None,
    unresolved_clues: list[dict[str, Any]] | None = None,
    planned_payoffs: list[dict[str, Any]] | None = None,
    chapter_contract: dict[str, Any] | None = None,
    scene_contract: dict[str, Any] | None = None,
    tree_context_nodes: list[dict[str, Any]] | None = None,
    active_emotion_tracks: list[dict[str, Any]] | None = None,
    active_antagonist_plans: list[dict[str, Any]] | None = None,
) -> str:
    """Return a minimal fallback markdown for a scene whose LLM draft failed.

    IMPORTANT: this function must NOT return narrative prose. Its output is
    used as ``fallback_response`` for the scene-writer LLM call, which means it
    can end up being stored verbatim as the scene's final ``content_md`` when
    the LLM is unreachable or returns empty text.

    Historically this function returned a six-paragraph template that looked
    like prose ("XX 被推入《项目名》第 N 章的核心冲突。叙事采用 third-limited
    视角…"). Those sentences repeatedly leaked into the final novel output as
    meta-commentary, because the sanitizer only matched structural markers and
    could not tell them apart from real scene text.

    The fix is to return an obviously non-prose HTML comment placeholder. When
    a scene relies on this fallback, the placeholder is easy to spot during
    review, the sanitizer drops it from the rendered chapter, and it cannot
    masquerade as narrative prose.
    """
    # The unused arguments below are intentional: callers still pass context
    # for parity with ``build_scene_draft_prompts`` and to keep the signature
    # stable. Reference them once so linters do not flag unused parameters.
    _ = (
        style_guide,
        story_bible_context,
        retrieval_context,
        recent_scene_summaries,
        recent_timeline_events,
        participant_canon_facts,
        active_plot_arcs,
        active_arc_beats,
        unresolved_clues,
        planned_payoffs,
        chapter_contract,
        scene_contract,
        tree_context_nodes,
        active_emotion_tracks,
        active_antagonist_plans,
    )
    participants = _scene_participant_text(scene.participants, language=_project_language(project))
    return (
        f"<!-- scene-draft-fallback project=\"{project.slug}\" "
        f"chapter={chapter.chapter_number} scene={scene.scene_number} "
        f"participants=\"{participants}\" -->"
    )


_SCENE_TYPE_GUIDANCE: dict[str, str] = {
    "hook": (
        "这是一个钩子/开场场景。用强烈的感官画面或悬念动作立刻抓住读者注意力："
        "角色必须在第一段就处于行动或困境中，严禁平铺直叙的背景介绍。"
        "抛出一个读者必须知道答案的问题或一个打破日常的意外事件。"
        "结尾要让读者非翻下一页不可。"
    ),
    "setup": (
        "这是一个铺垫/建设场景。为即将到来的冲突种下种子："
        "通过角色日常行动中的细节暗示即将到来的变化。建立角色关系的基线和世界规则。"
        "每一段看似平常的描写都要包含后续会回收的伏线。节奏可以稍慢，但严禁无意义的闲聊。"
    ),
    "transition": (
        "这是一个过渡/桥接场景。承接上一个情节高点并导向下一个冲突："
        "角色在消化刚发生的事件同时向新目标移动。用旅途、环境变化或新角色登场推动过渡。"
        "必须包含至少一个微型紧张点（一个隐患、一条坏消息、一次误判），避免节奏完全平坦。"
    ),
    "conflict": (
        "这是一个核心冲突场景。对抗必须直接、具体、有后果："
        "明确展示双方的筹码和代价。冲突中角色要做出艰难选择，不允许轻松化解。"
        "对话要带刺，动作要有后果，信息差要起作用。冲突结果必须改变力量格局。"
    ),
    "reveal": (
        "这是一个揭示/反转场景。核心信息的曝光必须带来范式转换："
        "精确控制信息释放的时机——先铺足读者和角色的错误预期，再用一个关键细节翻盘。"
        "重点写角色发现真相后的情绪冲击和行为变化，而不仅仅是信息本身。"
        "揭示必须改变角色之后的所有行动逻辑。"
    ),
    "introspection": (
        "这是一个沉思/内省场景。不需要强制外部冲突，重点放在角色内心世界："
        "让角色回顾过去、质疑自我、整理情绪。用内心独白、环境映射和感官细节构建氛围。"
        "结尾留下角色心态转变或新决定的暗示。"
    ),
    "relationship_building": (
        "这是一个关系深化场景。重点放在两个或多个角色之间的互动质量："
        "通过共同经历、坦诚对话或无声默契加深关系。展示角色间的化学反应和信任变化。"
        "不需要高强度冲突，但需要情感层次推进。"
    ),
    "worldbuilding_discovery": (
        "这是一个世界观发现场景。通过角色的亲身体验让读者感受世界："
        "用五感细节、角色反应和具体互动展示世界规则。严禁长段解释，一切设定信息必须藏在行动里。"
    ),
    "aftermath": (
        "这是一个余波/善后场景。上一个高潮刚刚结束，角色需要消化后果："
        "处理伤亡、评估损失、重新规划。情绪从高强度向内收，展示事件对角色的真实影响。"
        "节奏放慢，但要留下下一步行动的种子。"
    ),
    "preparation": (
        "这是一个蓄势场景。角色在为接下来的大事件做准备："
        "收集资源、制定计划、联络盟友。通过准备过程侧面展示挑战的严峻。"
        "营造紧迫感和期待感，但不要提前揭示结果。"
    ),
    "comic_relief": (
        "这是一个调剂场景。在持续紧张的剧情后给读者喘息空间："
        "用轻松幽默的日常互动展示角色的另一面。可以有轻微的搞笑冲突或温馨时刻。"
        "但调剂中也要自然植入一两个对后续情节有用的信息或线索。"
    ),
    "montage": (
        "这是一个时间流逝/蒙太奇场景。通过场景片段展示一段时间内的变化："
        "用精炼的场景碎片串联成长、训练、旅途或时间推进。每个碎片要有鲜明的感官标记。"
    ),
}

_SCENE_TYPE_GUIDANCE_EN: dict[str, str] = {
    "hook": (
        "This is a hook scene. Grab the reader's attention immediately with vivid sensory imagery or a disruption. "
        "The character must be in action or crisis by the first paragraph — no flat background exposition. "
        "Pose a question the reader cannot ignore or an event that breaks the status quo. "
        "End with a line that makes turning the page irresistible."
    ),
    "setup": (
        "This is a setup scene. Plant seeds for the coming conflict through everyday actions that carry hidden significance. "
        "Establish baseline relationships, character wants, and world rules. "
        "Every seemingly ordinary detail should contain a thread that pays off later. "
        "Pace can be moderate, but every exchange must advance characterization or stakes — no empty chatter."
    ),
    "transition": (
        "This is a transition scene. Bridge the aftermath of the last event to the next conflict zone. "
        "Show the character processing what happened while moving toward a new objective. "
        "Use travel, environment shifts, or a new character's arrival to carry the transition. "
        "Include at least one micro-tension beat (a warning, bad news, or misjudgment) so the pace never goes flat."
    ),
    "conflict": (
        "This is a core conflict scene. The confrontation must be direct, specific, and consequential. "
        "Show what each side stands to gain or lose. Force the character into a hard choice with no easy exit. "
        "Dialogue should carry subtext and edge; actions should have visible costs; information asymmetry should drive the stakes. "
        "The outcome must shift the power balance."
    ),
    "reveal": (
        "This is a reveal scene. The core information drop must create a paradigm shift. "
        "First solidify the character's (and reader's) wrong assumptions, then shatter them with one precise detail. "
        "Focus on the emotional shockwave and behavioral change the truth triggers, not just the information itself. "
        "The reveal must alter the character's decision logic for everything that follows."
    ),
    "introspection": (
        "This is an introspection scene. External conflict is optional; prioritize the character's inner reckoning, self-doubt, emotional sorting, and the decision forming underneath the silence."
    ),
    "relationship_building": (
        "This is a relationship-building scene. Prioritize interaction quality, shifting trust, and emotional subtext between the characters. It does not need explosive conflict, but it does need clear emotional progression."
    ),
    "worldbuilding_discovery": (
        "This is a world-discovery scene. Let the reader feel the setting through direct experience, sensory detail, and consequence. Avoid exposition blocks; hide the world rules inside action and reaction."
    ),
    "aftermath": (
        "This is an aftermath scene. The previous spike has just landed, so focus on consequence, damage assessment, emotional settling, and the seed of the next move."
    ),
    "preparation": (
        "This is a preparation scene. Show resource gathering, plan-making, or alliance-building in a way that makes the coming event feel larger and more dangerous without revealing the outcome early."
    ),
    "comic_relief": (
        "This is a relief scene. Let the pressure ease just enough for humor, warmth, or awkward humanity, but still plant at least one useful clue or piece of future leverage."
    ),
    "montage": (
        "This is a montage / time-passage scene. Use compressed scene fragments to show growth, travel, training, or time progression; each fragment should carry a sharp sensory anchor."
    ),
}


def _scene_type_writing_guidance(scene_type: str, *, language: str | None = None) -> str:
    is_en = is_english_language(language)
    guidance_map = _SCENE_TYPE_GUIDANCE_EN if is_en else _SCENE_TYPE_GUIDANCE
    return guidance_map.get(
        scene_type,
        (
            "Write a full scene with conflict movement, character action, effective dialogue, information change, and a closing hook."
            if is_en
            else "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。"
        ),
    )


def _render_knowledge_state_section(
    knowledge_states: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render character cognitive states into a prompt section."""
    if not knowledge_states:
        return ""
    lines: list[str] = []
    header = (
        "=== Character cognitive states (writing MUST obey) ==="
        if is_en
        else "=== 角色认知状态（写作必须遵守）==="
    )
    footer = (
        "=== Characters must NOT act on knowledge they don't have ==="
        if is_en
        else "=== 角色的对话和行为不得超越其认知边界 ==="
    )
    lines.append(header)
    for ks in knowledge_states:
        name = ks.get("character_name", "?")
        lines.append(f"{name}:")
        knows = ks.get("knows", [])
        if knows:
            lines.append(
                f"  {'Knows' if is_en else '已知'}："
                f"{'; '.join(str(k) for k in knows[:6])}"
            )
        fb = ks.get("falsely_believes", [])
        if fb:
            lines.append(
                f"  {'Falsely believes' if is_en else '错误相信'}："
                f"{'; '.join(str(b) for b in fb[:4])}"
            )
        unaware = ks.get("unaware_of", [])
        if unaware:
            lines.append(
                f"  {'Unaware of' if is_en else '尚不知道'}："
                f"{'; '.join(str(u) for u in unaware[:4])}"
            )
        # Phase-4: lie/truth arc guidance
        lt_arc = ks.get("lie_truth_arc")
        if isinstance(lt_arc, dict) and lt_arc.get("core_lie"):
            phase = lt_arc.get("current_phase", "believing_lie")
            if is_en:
                lines.append(f"  Core lie: {lt_arc['core_lie']}")
                lines.append(f"  Core truth: {lt_arc.get('core_truth', '?')}")
                lines.append(f"  Arc type: {lt_arc.get('arc_type', 'positive')} | Phase: {phase}")
                _phase_hints = {
                    "believing_lie": "Character fully believes the lie — actions driven by it.",
                    "questioning_lie": "Cracks appear — character encounters contradictions.",
                    "confronting_lie": "Crisis forces character to choose lie or truth.",
                    "embracing_truth": "Character acts from truth, paying transformation cost.",
                }
            else:
                lines.append(f"  核心谎言：{lt_arc['core_lie']}")
                lines.append(f"  核心真相：{lt_arc.get('core_truth', '?')}")
                lines.append(f"  弧线类型：{lt_arc.get('arc_type', 'positive')} | 阶段：{phase}")
                _phase_hints = {
                    "believing_lie": "角色完全相信谎言——行为受其驱动。",
                    "questioning_lie": "裂痕出现——角色遭遇矛盾。",
                    "confronting_lie": "危机迫使角色在谎言与真相间抉择。",
                    "embracing_truth": "角色基于真相行动，付出蜕变代价。",
                }
            hint = _phase_hints.get(phase, "")
            if hint:
                lines.append(f"  → {hint}")
    lines.append(footer)
    return "\n".join(lines)


# ── Phase-3 wiring: Swain scene/sequel pattern ──


def _render_scene_sequel_section(
    swain_pattern: str | None,
    scene_skeleton: dict[str, str] | None,
    *,
    is_en: bool = False,
) -> str:
    if not swain_pattern:
        return ""
    if is_en:
        header = "=== SCENE PATTERN ==="
        if swain_pattern == "action":
            lines = [
                f"\n{header}",
                f"This is an ACTION scene (Goal → Conflict → Disaster).",
            ]
            if scene_skeleton:
                lines.append(f"- Goal: {scene_skeleton.get('goal', '')}")
                lines.append(f"- Conflict: {scene_skeleton.get('conflict', '')}")
                lines.append(f"- Disaster: {scene_skeleton.get('disaster', '')}")
        else:
            lines = [
                f"\n{header}",
                f"This is a SEQUEL scene (Reaction → Dilemma → Decision).",
            ]
            if scene_skeleton:
                lines.append(f"- Reaction: {scene_skeleton.get('reaction', '')}")
                lines.append(f"- Dilemma: {scene_skeleton.get('dilemma', '')}")
                lines.append(f"- Decision: {scene_skeleton.get('decision', '')}")
        return "\n".join(lines) + "\n"
    # Chinese
    if swain_pattern == "action":
        lines = [
            "\n=== 场景节奏 ===",
            "本场是【行动场景】（目标 → 冲突 → 灾难）。",
        ]
        if scene_skeleton:
            lines.append(f"- 目标：{scene_skeleton.get('goal', '')}")
            lines.append(f"- 冲突：{scene_skeleton.get('conflict', '')}")
            lines.append(f"- 灾难：{scene_skeleton.get('disaster', '')}")
    else:
        lines = [
            "\n=== 场景节奏 ===",
            "本场是【反应场景】（反应 → 两难 → 决定）。",
        ]
        if scene_skeleton:
            lines.append(f"- 反应：{scene_skeleton.get('reaction', '')}")
            lines.append(f"- 两难：{scene_skeleton.get('dilemma', '')}")
            lines.append(f"- 决定：{scene_skeleton.get('decision', '')}")
    return "\n".join(lines) + "\n"


# ── Phase-2 wiring: structure template beat ──


def _render_structure_beat_section(
    structure_beat_name: str | None,
    structure_beat_description: str | None,
    *,
    is_en: bool = False,
) -> str:
    if not structure_beat_name:
        return ""
    desc = structure_beat_description or ""
    if is_en:
        return (
            f"\n=== STRUCTURE BEAT ===\n"
            f"This chapter lands on the \"{structure_beat_name}\" beat.\n"
            f"{desc}\n"
            f"Write to serve this structural role.\n"
        )
    return (
        f"\n=== 结构节拍 ===\n"
        f"本章对应结构节拍：「{structure_beat_name}」。\n"
        f"{desc}\n"
        f"请在写作中服务于这一结构定位。\n"
    )


# ── Phase-5 wiring: genre obligatory scenes ──


def _render_genre_obligations_section(
    obligations: list[dict[str, str]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render genre-required scenes due near the current chapter."""
    if not obligations:
        return ""
    if is_en:
        lines = ["\n=== GENRE OBLIGATIONS (due this chapter) ==="]
        for ob in obligations:
            lines.append(f"- [{ob.get('code', '')}] {ob.get('label', '')} (timing: {ob.get('timing', 'any')})")
        lines.append("Ensure at least one of these obligations is addressed in this scene if appropriate.")
    else:
        lines = ["\n=== 题材必须场景（本章附近应出现）==="]
        for ob in obligations:
            lines.append(f"- [{ob.get('code', '')}] {ob.get('label', '')}（时机：{ob.get('timing', 'any')}）")
        lines.append("请在合适时机安排上述必须场景元素。")
    return "\n".join(lines) + "\n"


# ── Phase-6 wiring: foreshadowing gap warning ──


def _render_foreshadowing_gap_section(
    warning: str | None,
    *,
    is_en: bool = False,
) -> str:
    """Render a foreshadowing gap warning if present."""
    if not warning:
        return ""
    if is_en:
        return (
            f"\n=== FORESHADOWING GAP WARNING ===\n"
            f"{warning}\n"
        )
    return (
        f"\n=== 伏笔空白警告 ===\n"
        f"{warning}\n"
    )


# ── Phase-1 wiring: render five previously orphaned narrative context sections ──


def _render_pacing_target_section(
    pacing_target: dict[str, Any] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render the chapter tension target into a prompt section."""
    if not pacing_target:
        return ""
    tension = pacing_target.get("tension_level", 0)
    scene_type_plan = pacing_target.get("scene_type_plan", "")
    notes = pacing_target.get("notes", "")
    if is_en:
        header = "=== Chapter Tension Target ==="
        body = f"Target tension level: {tension:.2f}/1.00"
        if scene_type_plan:
            body += f". Planned scene type: {scene_type_plan}"
        if notes:
            body += f". Notes: {notes}"
        return f"{header}\n{body}"
    header = "=== 本章张力目标 ==="
    body = f"目标张力水平: {tension:.2f}/1.00"
    if scene_type_plan:
        body += f"。计划场景类型: {scene_type_plan}"
    if notes:
        body += f"。备注: {notes}"
    return f"{header}\n{body}"


def _render_subplot_prominence_section(
    subplot_schedule: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render subplot prominence schedule for this chapter."""
    if not subplot_schedule:
        return ""
    primary = [e for e in subplot_schedule if e.get("prominence") == "primary"]
    secondary = [e for e in subplot_schedule if e.get("prominence") == "secondary"]
    mention = [e for e in subplot_schedule if e.get("prominence") == "mention"]
    lines: list[str] = []
    if is_en:
        lines.append("=== Subplot Focus This Chapter ===")
        if primary:
            lines.append(f"Primary arcs (must advance): {', '.join(e.get('arc_code', '?') for e in primary)}")
        if secondary:
            lines.append(f"Secondary arcs (touch briefly): {', '.join(e.get('arc_code', '?') for e in secondary)}")
        if mention:
            lines.append(f"Mention only: {', '.join(e.get('arc_code', '?') for e in mention)}")
    else:
        lines.append("=== 本章子情节焦点 ===")
        if primary:
            lines.append(f"主推弧线（必须推进）: {', '.join(e.get('arc_code', '?') for e in primary)}")
        if secondary:
            lines.append(f"辅助弧线（简短触及）: {', '.join(e.get('arc_code', '?') for e in secondary)}")
        if mention:
            lines.append(f"仅提及: {', '.join(e.get('arc_code', '?') for e in mention)}")
    return "\n".join(lines)


def _render_ending_contract_section(
    ending_contract: dict[str, Any] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render the ending contract checklist for final chapters."""
    if not ending_contract:
        return ""
    arcs = ending_contract.get("arcs_to_resolve", [])
    clues = ending_contract.get("clues_to_payoff", [])
    rels = ending_contract.get("relationships_to_close", [])
    thematic = ending_contract.get("thematic_final_expression", "")
    denouement = ending_contract.get("denouement_plan", "")
    lines: list[str] = []
    if is_en:
        lines.append("=== ENDING CONTRACT (MUST RESOLVE) ===")
        if arcs:
            lines.append(f"Arcs to resolve: {', '.join(arcs[:8])}")
        if clues:
            lines.append(f"Clues to pay off: {', '.join(clues[:8])}")
        if rels:
            lines.append(f"Relationships to close: {', '.join(rels[:6])}")
        if thematic:
            lines.append(f"Thematic final expression: {thematic}")
        if denouement:
            lines.append(f"Denouement plan: {denouement}")
    else:
        lines.append("=== 结局合约（必须收束）===")
        if arcs:
            lines.append(f"待收束弧线: {', '.join(arcs[:8])}")
        if clues:
            lines.append(f"待回收伏笔: {', '.join(clues[:8])}")
        if rels:
            lines.append(f"待收束关系: {', '.join(rels[:6])}")
        if thematic:
            lines.append(f"主题最终表达: {thematic}")
        if denouement:
            lines.append(f"余韵计划: {denouement}")
    return "\n".join(lines)


def _render_reader_knowledge_section(
    reader_knowledge_entries: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render dramatic irony cues — what the reader knows but characters may not."""
    if not reader_knowledge_entries:
        return ""
    lines: list[str] = []
    if is_en:
        lines.append("=== Reader Knowledge (Dramatic Irony) ===")
        for entry in reader_knowledge_entries[:8]:
            audience = entry.get("audience", "both")
            marker = " [reader only]" if audience == "reader_only" else ""
            lines.append(f"- Ch{entry.get('chapter_number', '?')}: {entry.get('knowledge_item', '?')}{marker}")
    else:
        lines.append("=== 读者已知信息（戏剧反讽）===")
        for entry in reader_knowledge_entries[:8]:
            audience = entry.get("audience", "both")
            marker = " [仅读者知道]" if audience == "reader_only" else ""
            lines.append(f"- 第{entry.get('chapter_number', '?')}章: {entry.get('knowledge_item', '?')}{marker}")
    return "\n".join(lines)


def _render_relationship_milestone_section(
    milestones: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render recent relationship milestone events."""
    if not milestones:
        return ""
    lines: list[str] = []
    if is_en:
        lines.append("=== Recent Relationship Milestones ===")
        for m in milestones[:6]:
            lines.append(
                f"- Ch{m.get('chapter_number', '?')}: "
                f"{m.get('character_a_label', '?')} ↔ {m.get('character_b_label', '?')}: "
                f"{m.get('event_description', '?')} → {m.get('relationship_change', '?')}"
            )
    else:
        lines.append("=== 近期关系里程碑 ===")
        for m in milestones[:6]:
            lines.append(
                f"- 第{m.get('chapter_number', '?')}章: "
                f"{m.get('character_a_label', '?')} ↔ {m.get('character_b_label', '?')}: "
                f"{m.get('event_description', '?')} → {m.get('relationship_change', '?')}"
            )
    return "\n".join(lines)


def build_scene_draft_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    style_guide: StyleGuideModel | None,
    story_bible_context: dict[str, Any] | None = None,
    retrieval_context: list[dict[str, Any]] | None = None,
    recent_scene_summaries: list[dict[str, Any]] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    participant_canon_facts: list[dict[str, Any]] | None = None,
    active_plot_arcs: list[dict[str, Any]] | None = None,
    active_arc_beats: list[dict[str, Any]] | None = None,
    unresolved_clues: list[dict[str, Any]] | None = None,
    planned_payoffs: list[dict[str, Any]] | None = None,
    chapter_contract: dict[str, Any] | None = None,
    scene_contract: dict[str, Any] | None = None,
    tree_context_nodes: list[dict[str, Any]] | None = None,
    active_emotion_tracks: list[dict[str, Any]] | None = None,
    active_antagonist_plans: list[dict[str, Any]] | None = None,
    hard_fact_snapshot: dict[str, Any] | None = None,
    contradiction_warnings: list[str] | None = None,
    participant_knowledge_states: list[dict[str, Any]] | None = None,
    arc_summaries: list[dict[str, Any]] | None = None,
    world_snapshot: dict[str, Any] | None = None,
    # Phase-1 wiring
    pacing_target: dict[str, Any] | None = None,
    subplot_schedule: list[dict[str, Any]] | None = None,
    ending_contract: dict[str, Any] | None = None,
    reader_knowledge_entries: list[dict[str, Any]] | None = None,
    relationship_milestones: list[dict[str, Any]] | None = None,
    # Phase-2 wiring
    structure_beat_name: str | None = None,
    structure_beat_description: str | None = None,
    # Phase-3 wiring
    swain_pattern: str | None = None,
    scene_skeleton: dict[str, str] | None = None,
    # Phase-5 wiring
    genre_obligations_due: list[dict[str, str]] | None = None,
    # Phase-6 wiring
    foreshadowing_gap_warning: str | None = None,
    # Identity / dedup / genre constraint blocks (Tier 0/1)
    identity_constraint_block: str | None = None,
    overused_phrase_block: str | None = None,
    genre_constraint_block: str | None = None,
    # Opening diversity block: list of (chapter_number, opening_snippet) for recent chapters
    opening_diversity_block: str | None = None,
    # Stage A — conflict diversity (per-scene, all scenes)
    conflict_diversity_block: str | None = None,
    # Stage B — scene-purpose diversity (all scenes)
    scene_purpose_diversity_block: str | None = None,
    # Stage B — environment 7-d diversity (all scenes)
    env_diversity_block: str | None = None,
    # Stage C — POV character arc + inner structure
    arc_beat_block: str | None = None,
    # Stage C — 5-layer thinking contract (POV decision points)
    five_layer_block: str | None = None,
    # Stage D — 7-type cliffhanger diversity
    cliffhanger_diversity_block: str | None = None,
    # Stage D — chapter tension target (beat-sheet driven)
    tension_target_block: str | None = None,
    # Stage B+ — location ledger (same-location reframe + visit cap)
    location_ledger_block: str | None = None,
    # L3 — DiversityBudget-sourced block (hot vocab, opening/cliffhanger rotation)
    budget_diversity_block: str | None = None,
    # Scene scope isolation block (earlier scenes written — don't rewrite them)
    scene_scope_isolation_block: str | None = None,
    # Plan-richness gate findings (pre-draft)
    plan_richness_block: str | None = None,
    # Reader Hype Engine — per-book commercial contract (cadenced) + per-chapter
    # hype constraints. Both are pre-rendered by
    # ``prompt_constructor.build_chapter_hype_blocks`` so this function only has
    # to concatenate them into the user_prompt. Empty/None → no-op.
    reader_contract_block: str | None = None,
    hype_constraints_block: str | None = None,
    # Context budget
    context_budget_tokens: int = 6000,
) -> tuple[str, str]:
    language = _project_language(project)
    is_en = is_english_language(language)
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    writing_profile_section = render_writing_profile_prompt_block(writing_profile, language=language)
    serial_guardrails = render_serial_fiction_guardrails(writing_profile, language=language)
    # Build system prompt with project-level static content first (cache-
    # friendly: Anthropic's automatic prompt caching keeps the shared prefix
    # across scenes in the same chapter, reducing TTFT by 60-80%).
    if is_en:
        system_prompt = (
            # --- Part A: Creative voice anchor ---
            "You are an expert fiction writer inside a long-form commercial fiction system. "
            "You write vivid, cinematic prose with sharp rhythm and irresistible hooks.\n"
            "Your core craft:\n"
            "- Show, don't tell — replace adjectives with action: not 'she was nervous' but 'her nails bit into her palm'.\n"
            "- Consequences over description — not 'the wave was huge' but 'the freighter flipped like a bathtub toy'.\n"
            "- Subtext over directness — true feelings live in action, silence, and environment.\n"
            "- Every paragraph ending plants an unanswered question that compels the reader forward.\n"
            "- Each passage should carry multiple functions: environment + character + foreshadowing + emotion in one beat.\n"
            "\n"
            # --- Part B: Pacing & Character ---
            "PACING RULE — not every scene is a chase. A good novel breathes:\n"
            "- After high-tension scenes, include moments of quiet: reflection, humor, small human interactions, sensory rest.\n"
            "- Vary paragraph rhythm: long flowing passages for atmosphere, short punchy lines for action. Mix them.\n"
            "- Silence, stillness, and waiting can build more tension than explosions. Use the space between events.\n"
            "\n"
            "CHARACTER DISTINCTION RULE — every character is a unique person:\n"
            "- Each character has their own sentence length, vocabulary level, speech habits, and emotional style.\n"
            "- Show characters through UNIQUE actions — not just 'jaw tightened' and 'arms crossed'. Give each person specific physical habits that belong only to them.\n"
            "- Characters must have agency: they initiate, refuse, surprise, joke, contradict. They are not reactive props.\n"
            "- When two characters talk, a reader should identify who is speaking WITHOUT dialogue tags.\n"
            "\n"
            # --- Part C: Consolidated iron rules ---
            "IRON RULES: Output direct Markdown prose only (narrative, dialogue, action, environment, thought). "
            "No explanations, bullet lists, planning notes, or commentary.\n"
            "Write in English only. Do not switch to Chinese.\n"
            "Word count must land within 90%-120% of target — too short or too long will be rejected.\n"
            "Use EXACT character names from the Participants list — no renaming, abbreviation, or substitution.\n"
            "Vary openings across time, place, action, and angle — never repeat the same pattern in consecutive chapters.\n"
            + _NOVEL_OUTPUT_PROHIBITION_EN
            + f"\nWriting profile:\n{writing_profile_section}\n"
            f"Serial fiction guardrails:\n{serial_guardrails}\n"
        )
    else:
        system_prompt = (
            # --- Part A: 创作声音锚点 (Positive creative guidance) ---
            "你是功力深厚的中文网文写手，擅长写出有画面感、有节奏、有钩子的商业小说。\n"
            "你的核心能力：\n"
            "・用动作代替形容词——不写「她很紧张」，写「她的手指死死掐进掌心」\n"
            "・用后果代替描述——不写「巨浪很大」，写「几千吨重的巨轮像塑料玩具一样被瞬间掀翻」\n"
            "・用潜台词代替直白——角色真实想法藏在动作、沉默和环境反应里\n"
            "・每一段结尾留一个没解答的问题，让读者必须翻下一页\n"
            "\n【风格锚点——好文字长这样】\n"
            "动作描写：「他一低头，发现一个还没他膝盖高的小女孩正扯着他的裤脚，仰着一张肉嘟嘟的小脸。」\n"
            "情绪隐藏：「滂沱大雨中他孤身一人，指甲深深掐进肉里。」\n"
            "环境交互：「手里签牛排的刀狠狠切下去——他盯着对面那张笑脸，刀刃陷进瓷盘。」\n"
            "一笔多用：一段文字同时承载环境、人设暗示、伏笔和情感，绝不浪费笔墨。\n"
            "\n"
            # --- Part B: 节奏与角色 ---
            "【节奏呼吸】不是每场都是追击战：\n"
            "・高张力场景之后，必须有喘息节拍——安静对话、幽默、感官休息、人物独处。\n"
            "・段落节奏要变化：长段铺氛围，短段打冲击。不要全文一个节奏。\n"
            "・沉默和等待有时比爆炸更有张力。善用留白。\n"
            "\n"
            "【角色区分度】每个角色都是独立的人：\n"
            "・每个角色有独属的句式长度、用词层次、说话习惯和情绪表达方式。\n"
            "・不要只用「收紧下巴」「抱臂」这种通用动作。每个角色给一个只属于他的肢体语言。\n"
            "・角色必须有主动性：主动、拒绝、出人意料、开玩笑、反驳。不是被动道具。\n"
            "・两人对话时，读者不看对话标签就能分辨是谁在说话。\n"
            "\n"
            # --- Part C: 硬约束 (Consolidated iron rules) ---
            "【铁律】输出仅限 Markdown 正文（叙事、对话、动作、环境、内心活动），不要解释/清单/策划说明。\n"
            "字数必须在目标的 90%-120% 范围内，不足或超出均退回重写。\n"
            "角色名必须与「参与者」列表完全一致，一字不差，禁止改名/别名/缩写。\n"
            "开场必须在时间、地点、视角、动作上变化，禁止与前几章重复同一模式。\n"
            + _NOVEL_OUTPUT_PROHIBITION
            + f"\n写作画像：\n{writing_profile_section}\n"
            f"商业网文硬约束：\n{serial_guardrails}\n"
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
    participants = _scene_participant_text(scene.participants, language=language)
    story_bible_section = _render_story_bible_section(story_bible_context, language=language)
    retrieval_section = _render_retrieval_section(retrieval_context)
    recent_scene_section = _render_recent_scene_section(recent_scene_summaries)
    recent_timeline_section = _render_timeline_section(recent_timeline_events)
    participant_fact_section = _render_participant_fact_section(participant_canon_facts)
    arc_section = _render_arc_section(active_plot_arcs, active_arc_beats, language=language)
    clue_section = _render_clue_section(unresolved_clues, planned_payoffs, language=language)
    emotion_track_section = _render_emotion_track_section(active_emotion_tracks, language=language)
    antagonist_plan_section = _render_antagonist_plan_section(active_antagonist_plans, language=language)
    contract_section = _render_contract_section(chapter_contract, scene_contract, language=language)
    tree_section = _render_tree_section(tree_context_nodes, language=language)
    hard_fact_section = _render_hard_fact_snapshot_section(hard_fact_snapshot, language=language)
    prompt_pack_section = render_prompt_pack_prompt_block(prompt_pack)
    prompt_pack_scene_writer = render_prompt_pack_fragment(prompt_pack, "scene_writer")
    _pp_line = (
        f"Prompt Pack:\n{prompt_pack_section}\n"
        if prompt_pack_section and is_en
        else (f"Prompt Pack：\n{prompt_pack_section}\n" if prompt_pack_section else "")
    )
    _pp_writer_line = (
        f"Extra Prompt Pack guidance:\n{prompt_pack_scene_writer}\n"
        if prompt_pack_scene_writer and is_en
        else (f"Prompt Pack 额外写法：\n{prompt_pack_scene_writer}\n" if prompt_pack_scene_writer else "")
    )
    # Methodology rules injection (猫神方法论)
    _methodology_pack_block = render_methodology_block(prompt_pack, phase="scene")
    # Try contract fields first; fall back to positional heuristics when empty.
    _is_climax = bool(chapter_contract and chapter_contract.get("is_climax"))
    _pacing_mode_val = (chapter_contract or {}).get("pacing_mode") or ""
    if not _pacing_mode_val:
        # Heuristic: derive pacing mode from chapter position within the book
        _total = max(getattr(project, "target_chapters", None) or 20, 1)
        _pos = chapter.chapter_number / _total
        if _pos >= 0.75:
            _pacing_mode_val = "accelerate"
            # The last 10% of the book is climax territory
            if _pos >= 0.90:
                _is_climax = True
                _pacing_mode_val = "accelerate"
        elif _pos <= 0.15:
            _pacing_mode_val = "build"  # opening chapters
        elif chapter.chapter_number % 5 == 0:
            _pacing_mode_val = "breathe"  # periodic breathing room
        else:
            _pacing_mode_val = "build"
    _methodology_rules = render_methodology_scene_rules(
        chapter_number=chapter.chapter_number,
        is_opening=(chapter.chapter_number <= 3),
        is_climax=_is_climax,
        pacing_mode=_pacing_mode_val,
    )
    _methodology_line = ""
    if _methodology_pack_block or _methodology_rules:
        _methodology_line = (
            f"{_methodology_pack_block}\n\n{_methodology_rules}\n\n"
            if _methodology_pack_block
            else f"{_methodology_rules}\n\n"
        )
    _hard_fact_line = f"{hard_fact_section}\n\n" if hard_fact_section else ""
    _contradiction_line = ""
    if contradiction_warnings:
        _warning_items = "\n".join(f"- {w}" for w in contradiction_warnings)
        _contradiction_line = (
            f"=== Continuity constraints (must obey) ===\n{_warning_items}\n"
            f"=== Do not violate the constraints above ===\n\n"
            if is_en
            else (
                f"=== 连续性约束（必须遵守）===\n{_warning_items}\n"
                f"=== 不得违反以上约束 ===\n\n"
            )
        )
    _knowledge_line = _render_knowledge_state_section(participant_knowledge_states, is_en=is_en)
    if _knowledge_line:
        _knowledge_line += "\n\n"

    # Character identity constraints (Tier 0 — always included)
    _identity_line = ""
    if identity_constraint_block:
        _identity_line = f"{identity_constraint_block}\n\n"

    # Overused phrase avoidance (Tier 1 — always included)
    _phrase_avoidance_line = ""
    if overused_phrase_block:
        _phrase_avoidance_line = f"{overused_phrase_block}\n\n"

    # Opening diversity avoidance (Tier 1 — injected for scene 1 of each chapter)
    _opening_diversity_line = ""
    if opening_diversity_block:
        _opening_diversity_line = f"{opening_diversity_block}\n\n"

    # Genre-specific constraints (Tier 1 — always included)
    _genre_constraint_line = ""
    if genre_constraint_block:
        _genre_constraint_line = f"{genre_constraint_block}\n\n"

    # Stage A — conflict diversity (ALL scenes, not just scene 1)
    _conflict_diversity_line = ""
    if conflict_diversity_block:
        _conflict_diversity_line = f"{conflict_diversity_block}\n\n"

    # Stage B — scene-purpose diversity (ALL scenes)
    _scene_purpose_line = ""
    if scene_purpose_diversity_block:
        _scene_purpose_line = f"{scene_purpose_diversity_block}\n\n"

    # Stage B — environment 7-d diversity (ALL scenes)
    _env_diversity_line = ""
    if env_diversity_block:
        _env_diversity_line = f"{env_diversity_block}\n\n"

    # Stage C — POV arc beat block (ALL scenes)
    _arc_beat_line = ""
    if arc_beat_block:
        _arc_beat_line = f"{arc_beat_block}\n\n"

    # Stage C — 5-layer thinking contract (ALL scenes)
    _five_layer_line = ""
    if five_layer_block:
        _five_layer_line = f"{five_layer_block}\n\n"

    # Stage D — cliffhanger diversity (last-scene-only ideally, but we show it on all)
    _cliffhanger_line = ""
    if cliffhanger_diversity_block:
        _cliffhanger_line = f"{cliffhanger_diversity_block}\n\n"

    # Stage D — chapter tension target (ALL scenes of the chapter share this)
    _tension_target_line = ""
    if tension_target_block:
        _tension_target_line = f"{tension_target_block}\n\n"

    # Stage B+ — location ledger (ALL scenes)
    _location_ledger_line = ""
    if location_ledger_block:
        _location_ledger_line = f"{location_ledger_block}\n\n"

    # L3 — DiversityBudget (hot vocab + structured rotation). Orthogonal to
    # the heuristic ``overused_phrase_block`` above.
    _budget_diversity_line = ""
    if budget_diversity_block:
        _budget_diversity_line = f"{budget_diversity_block}\n\n"

    # Scene scope isolation — earlier scenes in this chapter that are already
    # written.  Prevents the writer from rewriting / paraphrasing them in
    # this scene (root cause of intra-chapter duplication).
    _scene_scope_isolation_line = ""
    if scene_scope_isolation_block:
        _scene_scope_isolation_line = f"{scene_scope_isolation_block}\n\n"

    # Plan-richness RED FLAGS — the pre-draft gate detected a thin scene card.
    # Surface the issues so the writer LLM knows which fields are generic /
    # missing and must compensate with its own concrete invention.
    _plan_richness_line = ""
    if plan_richness_block:
        _plan_richness_line = f"{plan_richness_block}\n\n"

    # Reader Hype Engine — reader contract (commercial frame, cadenced) and
    # per-chapter hype constraints. Pre-rendered by
    # ``build_chapter_hype_blocks``; empty strings stay no-ops for legacy
    # projects with empty HypeScheme.
    _reader_contract_line = ""
    if reader_contract_block:
        _reader_contract_line = f"{reader_contract_block}\n\n"
    _hype_constraints_line = ""
    if hype_constraints_block:
        _hype_constraints_line = f"{hype_constraints_block}\n\n"

    # Phase-3 wiring: scene/sequel pattern
    _scene_sequel_line = _render_scene_sequel_section(
        swain_pattern, scene_skeleton, is_en=is_en,
    )
    if _scene_sequel_line:
        _scene_sequel_line += "\n\n"

    # Phase-2 wiring: structure beat
    _structure_beat_line = _render_structure_beat_section(
        structure_beat_name, structure_beat_description, is_en=is_en,
    )
    if _structure_beat_line:
        _structure_beat_line += "\n\n"

    # Phase-1 wiring: render five previously orphaned narrative context sections
    _pacing_line = _render_pacing_target_section(pacing_target, is_en=is_en)
    if _pacing_line:
        _pacing_line += "\n\n"
    _subplot_line = _render_subplot_prominence_section(subplot_schedule, is_en=is_en)
    if _subplot_line:
        _subplot_line += "\n\n"
    _ending_line = _render_ending_contract_section(ending_contract, is_en=is_en)
    if _ending_line:
        _ending_line += "\n\n"
    _reader_knowledge_line = _render_reader_knowledge_section(reader_knowledge_entries, is_en=is_en)
    if _reader_knowledge_line:
        _reader_knowledge_line += "\n\n"
    _relationship_line = _render_relationship_milestone_section(relationship_milestones, is_en=is_en)
    if _relationship_line:
        _relationship_line += "\n\n"

    # Phase-5 wiring: genre obligatory scenes
    _obligations_line = _render_genre_obligations_section(genre_obligations_due, is_en=is_en)
    if _obligations_line:
        _obligations_line += "\n\n"

    # Phase-6 wiring: foreshadowing gap warning
    _foreshadow_line = _render_foreshadowing_gap_section(foreshadowing_gap_warning, is_en=is_en)
    if _foreshadow_line:
        _foreshadow_line += "\n\n"

    # Arc summaries (warm context) and world snapshot (cold context)
    _arc_summary_line = ""
    if arc_summaries:
        _arc_items = []
        for arc_s in arc_summaries:
            ch_start = arc_s.get("chapter_start", "?")
            ch_end = arc_s.get("chapter_end", "?")
            growth = arc_s.get("protagonist_growth", "")
            threads = ", ".join(arc_s.get("unresolved_threads", [])[:3])
            _arc_items.append(
                f"  Arc Ch{ch_start}-{ch_end}: {growth}"
                + (f" | Unresolved: {threads}" if threads else "")
            )
        _arc_block = "\n".join(_arc_items)
        _arc_summary_line = (
            f"=== Recent arc recap (warm context) ===\n{_arc_block}\n\n"
            if is_en
            else f"=== 近期弧线回顾（温上下文）===\n{_arc_block}\n\n"
        )
    _world_snapshot_line = ""
    if world_snapshot:
        ws = world_snapshot.get("world_summary", "")
        if ws:
            _world_snapshot_line = (
                f"=== World state (cold context) ===\n{ws}\n\n"
                if is_en
                else f"=== 世界状态（冷上下文）===\n{ws}\n\n"
            )

    # --- Context budget enforcement ---
    # Pack all rendered sections into a dict, run through the budget filter,
    # then unpack back into local variables.  This keeps Tier 1 sections
    # intact while trimming Tier 2/3 when the combined context is too large.
    _ctx = _budget_context_sections(
        {
            "contract_section": contract_section,
            "methodology_line": _methodology_line,
            "participant_fact_section": participant_fact_section,
            "contradiction_line": _contradiction_line,
            "identity_line": _identity_line,
            "phrase_avoidance_line": _phrase_avoidance_line,
            "opening_diversity_line": _opening_diversity_line,
            "genre_constraint_line": _genre_constraint_line,
            "conflict_diversity_line": _conflict_diversity_line,
            "scene_purpose_line": _scene_purpose_line,
            "env_diversity_line": _env_diversity_line,
            "arc_beat_line": _arc_beat_line,
            "five_layer_line": _five_layer_line,
            "cliffhanger_line": _cliffhanger_line,
            "tension_target_line": _tension_target_line,
            "location_ledger_line": _location_ledger_line,
            "budget_diversity_line": _budget_diversity_line,
            "scene_scope_isolation_line": _scene_scope_isolation_line,
            "plan_richness_line": _plan_richness_line,
            "reader_contract_line": _reader_contract_line,
            "hype_constraints_line": _hype_constraints_line,
            "hard_fact_line": _hard_fact_line,
            "knowledge_line": _knowledge_line,
            "recent_scene_section": recent_scene_section,
            "emotion_track_section": emotion_track_section,
            "antagonist_plan_section": antagonist_plan_section,
            "clue_section": clue_section,
            "scene_sequel_line": _scene_sequel_line,
            "structure_beat_line": _structure_beat_line,
            "pacing_line": _pacing_line,
            "story_bible_section": story_bible_section,
            "arc_section": arc_section,
            "arc_summary_line": _arc_summary_line,
            "world_snapshot_line": _world_snapshot_line,
            "retrieval_section": retrieval_section,
            "recent_timeline_section": recent_timeline_section,
            "reader_knowledge_line": _reader_knowledge_line,
            "relationship_line": _relationship_line,
            "subplot_line": _subplot_line,
            "ending_line": _ending_line,
            "obligations_line": _obligations_line,
            "foreshadow_line": _foreshadow_line,
            "tree_section": tree_section,
            "pp_line": _pp_line,
            "pp_writer_line": _pp_writer_line,
        },
        context_budget_tokens,
    )
    # Unpack budgeted sections back into local variables
    contract_section = _ctx["contract_section"]
    _methodology_line = _ctx["methodology_line"]
    participant_fact_section = _ctx["participant_fact_section"]
    _contradiction_line = _ctx["contradiction_line"]
    _identity_line = _ctx["identity_line"]
    _phrase_avoidance_line = _ctx["phrase_avoidance_line"]
    _opening_diversity_line = _ctx["opening_diversity_line"]
    _genre_constraint_line = _ctx["genre_constraint_line"]
    _conflict_diversity_line = _ctx["conflict_diversity_line"]
    _scene_purpose_line = _ctx["scene_purpose_line"]
    _env_diversity_line = _ctx["env_diversity_line"]
    _arc_beat_line = _ctx["arc_beat_line"]
    _five_layer_line = _ctx["five_layer_line"]
    _cliffhanger_line = _ctx["cliffhanger_line"]
    _tension_target_line = _ctx["tension_target_line"]
    _location_ledger_line = _ctx["location_ledger_line"]
    _budget_diversity_line = _ctx["budget_diversity_line"]
    _scene_scope_isolation_line = _ctx["scene_scope_isolation_line"]
    _plan_richness_line = _ctx["plan_richness_line"]
    _reader_contract_line = _ctx["reader_contract_line"]
    _hype_constraints_line = _ctx["hype_constraints_line"]
    _hard_fact_line = _ctx["hard_fact_line"]
    _knowledge_line = _ctx["knowledge_line"]
    recent_scene_section = _ctx["recent_scene_section"]
    emotion_track_section = _ctx["emotion_track_section"]
    antagonist_plan_section = _ctx["antagonist_plan_section"]
    clue_section = _ctx["clue_section"]
    _scene_sequel_line = _ctx["scene_sequel_line"]
    _structure_beat_line = _ctx["structure_beat_line"]
    _pacing_line = _ctx["pacing_line"]
    story_bible_section = _ctx["story_bible_section"]
    arc_section = _ctx["arc_section"]
    _arc_summary_line = _ctx["arc_summary_line"]
    _world_snapshot_line = _ctx["world_snapshot_line"]
    retrieval_section = _ctx["retrieval_section"]
    recent_timeline_section = _ctx["recent_timeline_section"]
    _reader_knowledge_line = _ctx["reader_knowledge_line"]
    _relationship_line = _ctx["relationship_line"]
    _subplot_line = _ctx["subplot_line"]
    _ending_line = _ctx["ending_line"]
    _obligations_line = _ctx["obligations_line"]
    _foreshadow_line = _ctx["foreshadow_line"]
    tree_section = _ctx["tree_section"]
    _pp_line = _ctx["pp_line"]
    _pp_writer_line = _ctx["pp_writer_line"]

    if is_en:
        user_prompt = (
            f"{_hard_fact_line}"
            f"{_contradiction_line}"
            f"{_reader_contract_line}"
            f"{_hype_constraints_line}"
            f"{_plan_richness_line}"
            f"{_identity_line}"
            f"{_scene_scope_isolation_line}"
            f"{_genre_constraint_line}"
            f"{_phrase_avoidance_line}"
            f"{_opening_diversity_line}"
            f"{_budget_diversity_line}"
            f"{_conflict_diversity_line}"
            f"{_scene_purpose_line}"
            f"{_env_diversity_line}"
            f"{_location_ledger_line}"
            f"{_arc_beat_line}"
            f"{_five_layer_line}"
            f"{_tension_target_line}"
            f"{_cliffhanger_line}"
            f"{_knowledge_line}"
            f"{_arc_summary_line}"
            f"{_world_snapshot_line}"
            f"{_structure_beat_line}"
            f"{_scene_sequel_line}"
            f"{_pacing_line}"
            f"{_subplot_line}"
            f"{_ending_line}"
            f"{_reader_knowledge_line}"
            f"{_relationship_line}"
            f"{_obligations_line}"
            f"{_foreshadow_line}"
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}: {chapter.title or ''}\n"
            f"Chapter goal (for intent only, never quote it verbatim): {chapter.chapter_goal}\n"
            f"Scene {scene.scene_number}: {scene.title or ''}\n"
            f"Scene type: {scene.scene_type}\n"
            f"Time label: {scene.time_label or 'unspecified'}\n"
            f"Participants: {participants}\n"
            f"Story purpose: {scene.purpose.get('story', 'advance the chapter spine')}\n"
            f"Emotional purpose: {scene.purpose.get('emotion', 'raise tension')}\n"
            f"Entry state: {scene.entry_state}\n"
            f"Exit state: {scene.exit_state}\n"
            f"Target words: {scene.target_word_count} (STRICT RANGE: {int(scene.target_word_count * 0.9)}-{int(scene.target_word_count * 1.2)} words. Scenes outside this range will be rejected. Do NOT cut short and do NOT over-write.)\n"
            f"POV: {style_guide.pov_type if style_guide else 'third-limited'}\n"
            f"Tone keywords: {tone}\n"
            f"{_pp_line}"
            f"Story bible constraints:\n{story_bible_section or 'No additional story-bible constraints.'}\n"
            f"Recent story recap:\n{recent_scene_section or 'No recent-scene recap.'}\n"
            f"Known timeline beats:\n{recent_timeline_section or 'No known timeline beats.'}\n"
            f"Active narrative lines and beats:\n{arc_section or 'No explicit arc constraints.'}\n"
            f"Clue and payoff constraints:\n{clue_section or 'No explicit clue/payoff constraints.'}\n"
            f"Relationship and emotional progression:\n{emotion_track_section or 'No explicit relationship/emotion constraints.'}\n"
            f"Antagonist pressure:\n{antagonist_plan_section or 'No explicit antagonist constraints.'}\n"
            f"Chapter/scene contract:\n{contract_section or 'No explicit contract constraints.'}\n"
            f"Narrative tree context:\n{tree_section or 'No narrative tree context.'}\n"
            f"Visible facts for current participants:\n{participant_fact_section or 'No extra participant facts.'}\n"
            f"Retrieved supporting context:\n{retrieval_section or 'No extra retrieval context.'}\n"
            f"{_pp_writer_line}"
            f"{_methodology_line}"
            f"Scene-type guidance: {_scene_type_writing_guidance(scene.scene_type, language=language)}\n"
            "Write the scene in English only. Do not switch to Chinese. "
            "Do not reveal information that belongs to future chapters, and do not contradict established facts or timeline beats. "
            "Prioritize the deterministic path retrieval and narrative-tree constraints when they exist. "
            "The scene must land the core conflict, emotional movement, information release, and tail hook required by the scene contract. "
            "Keep exposition compressed; hide setting inside action, exchange, consequence, and detail.\n"
            "OPENING DIVERSITY: Do NOT open this scene with darkness, pain, waking up, or loss of consciousness. "
            "Choose from: mid-action, dialogue, a sensory detail, an environmental observation, a character's thought about something specific, a question, or an ironic contrast. "
            "Check the recent story recap above — if the previous scene opened with a similar pattern, CHOOSE A DIFFERENT ONE.\n"
            "CONTINUITY: If a character explicitly stated they would NOT do something in a previous scene, do not reverse that decision without showing the reason why. "
            "Every character entrance must be motivated — explain (through action or implication) how they arrived."
        )
    else:
        user_prompt = (
            f"{_hard_fact_line}"
            f"{_contradiction_line}"
            f"{_reader_contract_line}"
            f"{_hype_constraints_line}"
            f"{_plan_richness_line}"
            f"{_identity_line}"
            f"{_scene_scope_isolation_line}"
            f"{_genre_constraint_line}"
            f"{_phrase_avoidance_line}"
            f"{_opening_diversity_line}"
            f"{_budget_diversity_line}"
            f"{_conflict_diversity_line}"
            f"{_scene_purpose_line}"
            f"{_env_diversity_line}"
            f"{_location_ledger_line}"
            f"{_arc_beat_line}"
            f"{_five_layer_line}"
            f"{_tension_target_line}"
            f"{_cliffhanger_line}"
            f"{_knowledge_line}"
            f"{_arc_summary_line}"
            f"{_world_snapshot_line}"
            f"{_structure_beat_line}"
            f"{_scene_sequel_line}"
            f"{_pacing_line}"
            f"{_subplot_line}"
            f"{_ending_line}"
            f"{_reader_knowledge_line}"
            f"{_relationship_line}"
            f"{_obligations_line}"
            f"{_foreshadow_line}"
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
            f"章节目标（仅供你理解意图，严禁出现在正文中）：{chapter.chapter_goal}\n"
            f"场景定位（仅供参考，不要作为标题输出）：第{scene.scene_number}场 {scene.title or ''}\n"
            f"场景类型：{scene.scene_type}\n"
            f"时间标签：{scene.time_label or '未指定'}\n"
            f"参与者：{participants}\n"
            f"剧情目的：{scene.purpose.get('story', '推进本章主线')}\n"
            f"情绪目的：{scene.purpose.get('emotion', '拉高当前张力')}\n"
            f"入场状态：{scene.entry_state}\n"
            f"离场状态：{scene.exit_state}\n"
            f"目标字数：{scene.target_word_count}（【硬性要求】正文字数必须在 {int(scene.target_word_count * 0.9)}-{int(scene.target_word_count * 1.2)} 字范围内。不足或超出均会退回重写。不要提前收束，也不要注水拖长。）\n"
            f"视角：{style_guide.pov_type if style_guide else 'third-limited'}\n"
            f"语气关键词：{tone}\n"
            f"{_pp_line}"
            f"故事圣经约束：\n{story_bible_section or '暂无额外故事圣经约束'}\n"
            f"近期剧情回顾：\n{recent_scene_section or '暂无近期剧情回顾'}\n"
            f"已知时间线节点：\n{recent_timeline_section or '暂无已知时间线节点'}\n"
            f"当前叙事线与节拍：\n{arc_section or '暂无显式叙事线约束'}\n"
            f"伏笔与兑现约束：\n{clue_section or '暂无显式伏笔/兑现约束'}\n"
            f"关系与情绪推进约束：\n{emotion_track_section or '暂无显式关系/情绪线约束'}\n"
            f"反派推进约束：\n{antagonist_plan_section or '暂无显式反派推进约束'}\n"
            f"chapter/scene contract：\n{contract_section or '暂无显式 contract 约束'}\n"
            f"叙事树上下文：\n{tree_section or '暂无叙事树上下文'}\n"
            f"参与角色当前可见事实：\n{participant_fact_section or '暂无额外角色事实'}\n"
            f"检索到的相关上下文：\n{retrieval_section or '暂无额外检索上下文'}\n"
            f"{_pp_writer_line}"
            f"{_methodology_line}"
            f"{_scene_type_writing_guidance(scene.scene_type, language=language)}"
            "不得泄露未来章节才会揭示的信息，不得与当前已知事实和时间线冲突。"
            "优先服从 deterministic path retrieval 与 narrative tree 提供的结构化约束。"
            "必须覆盖 scene contract 的核心冲突、情绪变化、信息释放和尾钩。"
            "背景说明必须压缩到最少，优先把设定藏进人物行动、交易、冲突后果和细节里。"
            "不要用空泛抒情、不要先解释世界观、不要写成提纲口吻。\n"
            "【开头多样性】本场景不要以黑暗、痛苦、失去意识或醒来开场。"
            "从以下方式中选择：正在进行的动作、对话、一个感官细节、环境观察、角色对具体事物的想法、一个问题、或一个反差。"
            "对照「近期剧情回顾」，如果前几场用了类似的开头，必须选一个不同的。\n"
            "【连续性】如果角色在前一场明确说了不做某事，不要无理由翻转。"
            "每个角色登场必须有动机——通过动作或暗示说明他/她为何出现在这里。"
        )
    return system_prompt, user_prompt


def _packet_story_bible_context(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    return packet.story_bible if packet is not None else None


def _packet_recent_scene_summaries(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.recent_scene_summaries]


def _packet_recent_timeline_events(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.recent_timeline_events]


def _packet_participant_canon_facts(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.participant_canon_facts]


def _packet_active_plot_arcs(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_plot_arcs]


def _packet_active_arc_beats(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_arc_beats]


def _packet_unresolved_clues(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.unresolved_clues]


def _packet_planned_payoffs(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.planned_payoffs]


def _packet_chapter_contract(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.chapter_contract is None:
        return None
    return packet.chapter_contract.model_dump(mode="json")


def _packet_scene_contract(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.scene_contract is None:
        return None
    return packet.scene_contract.model_dump(mode="json")


def _packet_tree_context(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.tree_context_nodes]


def _packet_retrieval_context(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.retrieval_chunks]


def _packet_emotion_tracks(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_emotion_tracks]


def _packet_antagonist_plans(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_antagonist_plans]


def _packet_hard_fact_snapshot(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.hard_fact_snapshot is None:
        return None
    return packet.hard_fact_snapshot.model_dump(mode="json")


def _has_leading_chapter_heading(content_md: str, chapter_number: int) -> bool:
    """Return True if ``content_md`` already begins with a canonical chapter heading.

    Tolerates leading blank lines and matches both Chinese ("# 第N章…") and
    English ("# Chapter N…") headings. Used to skip a second heading prepend
    on the disk-sync path when ``render_chapter_draft_markdown`` has already
    added one.
    """
    if not content_md:
        return False
    first_line = ""
    for line in content_md.splitlines():
        if line.strip():
            first_line = line.strip()
            break
    if not first_line:
        return False
    return bool(
        re.match(rf"^#{{1,4}}\s*第\s*{chapter_number}\s*章\b", first_line)
        or re.match(rf"^#{{1,4}}\s*Chapter\s+{chapter_number}\b", first_line, re.IGNORECASE)
    )


def format_chapter_heading(
    chapter_number: int,
    raw_title: str | None,
    *,
    language: str | None = None,
) -> str:
    """Build a single ``# 第N章：子标题`` heading without double-prefixing.

    ``chapter.title`` in older data can look like any of:

    - ``"零点前的抢购"``             → ``# 第1章：零点前的抢购``
    - ``"第1章：零点前的抢购"``      → ``# 第1章：零点前的抢购``
    - ``"第1章 零点前的抢购"``        → ``# 第1章：零点前的抢购``
    - ``"第1章"``                    → ``# 第1章``
    - ``None`` / ``""``              → ``# 第1章``

    Previously the renderer unconditionally prepended ``# 第N章 {title}`` and
    produced ``# 第1章 第1章：零点前的抢购``. This helper strips any existing
    ``第N章`` prefix (with optional whitespace / colon) before re-attaching a
    single canonical prefix.
    """
    is_en = is_english_language(language)
    chapter_prefix = f"Chapter {chapter_number}" if is_en else f"第{chapter_number}章"
    title = (raw_title or "").strip()
    if not title:
        return f"# {chapter_prefix}"
    # Strip any existing "第N章" prefix (with optional separator) to avoid
    # double-prefixing. Tolerate both the exact chapter number and generic
    # leading "第\d+章" forms so earlier data with stale numbering still works.
    stripped = re.sub(r"^第\s*\d+\s*章\s*[：:\-\s]*", "", title).strip()
    stripped = re.sub(r"^Chapter\s*\d+\s*[:\-\s]*", "", stripped, flags=re.IGNORECASE).strip()
    if not stripped:
        return f"# {chapter_prefix}"
    separator = ": " if is_en else "："
    return f"# {chapter_prefix}{separator}{stripped}"


def render_chapter_draft_markdown(
    chapter: ChapterModel,
    scene_drafts: list[SceneDraftVersionModel],
    *,
    language: str | None = None,
) -> str:
    header = [format_chapter_heading(chapter.chapter_number, chapter.title, language=language)]
    scene_sections = [
        strip_scaffolding_echoes(
            sanitize_novel_markdown_content(scene_draft.content_md, language=language)
        )
        for scene_draft in scene_drafts
    ]
    # Strip leading chapter/scene headings from each scene section.
    # render_chapter_draft_markdown already prepends the canonical chapter
    # heading, so any "# 第N章 ..." or "# 第N章 第M场 ..." line at the top
    # of a scene section is always a leak from the LLM writer.
    _scene_heading_re = re.compile(
        r"^\s*#{1,4}\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*[章场]|Chapter\s+\d+)",
        re.IGNORECASE,
    )
    # Also strip bare subtitle headings (e.g. "# 雾锁探针") that match the
    # chapter title — the LLM sometimes outputs the subtitle alone as a
    # heading, duplicating the canonical "# 第N章：雾锁探针" heading above.
    _raw_title = (chapter.title or "").strip()
    _stripped_title = re.sub(r"^第\s*\d+\s*章\s*[：:\-\s]*", "", _raw_title).strip()
    cleaned_sections: list[str] = []
    for section in scene_sections:
        lines = section.split("\n")
        # Strip leading blank lines, then check the first content line.
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and _scene_heading_re.match(lines[0].strip()):
            lines.pop(0)
        # Strip bare subtitle heading that duplicates the chapter title
        elif lines and _stripped_title:
            _first = lines[0].strip()
            if _first.startswith("#") and _stripped_title in _first:
                # Check it's just a heading with the subtitle, not prose
                _heading_text = re.sub(r"^#{1,4}\s*", "", _first).strip()
                if _heading_text == _stripped_title:
                    lines.pop(0)
        cleaned_sections.append("\n".join(lines).strip())
    scene_sections = cleaned_sections
    # Drop any scene section that collapsed to an empty string after sanitizing
    # (e.g. when the section was 100% meta-commentary leakage) so the final
    # chapter does not contain stray blank "<!-- fallback -->" placeholders or
    # double blank lines.
    scene_sections = [section for section in scene_sections if section.strip()]
    if not scene_sections:
        raise ValueError(
            f"Chapter {chapter.chapter_number} has no scene content after sanitization. "
            f"All {len(scene_drafts)} scene drafts were empty or contained only "
            f"fallback placeholders. The LLM writer failed for every scene. "
            f"Check: 1) API key is set (MINIMAX_API_KEY / ANTHROPIC_API_KEY), "
            f"2) model name is valid, 3) network connectivity to the LLM provider."
        )
    return "\n\n".join(header + scene_sections).strip()


def _determine_model_tier(
    chapter: ChapterModel,
    scene: SceneCardModel,
    chapter_contract: dict[str, Any] | None = None,
) -> str:
    """Determine whether this scene should use the 'strong' model tier.

    Golden-three chapters, climax scenes, and turning points get the stronger
    model for richer prose quality.
    """
    if chapter.chapter_number <= 3:
        return "strong"
    if chapter_contract and chapter_contract.get("is_climax"):
        return "strong"
    if scene.scene_type in ("climax", "revelation", "turning_point"):
        return "strong"
    return "standard"


async def generate_scene_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    context_packet: SceneWriterContextPacket | None = None,
) -> SceneDraftVersionModel:
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

    style_guide = await session.get(StyleGuideModel, project.id)
    if context_packet is not None:
        # Caller (run_scene_pipeline) already built a shared context for this scene —
        # reuse it instead of re-running the 10+ DB/retrieval queries inside
        # build_scene_writer_context_from_models. Opt-B memoization.
        pass
    elif settings is not None:
        context_packet = await build_scene_writer_context_from_models(
            session,
            settings,
            project,
            chapter,
            scene,
        )
    else:
        story_bible_context = await load_scene_story_bible_context(
            session,
            project=project,
            chapter=chapter,
            scene=scene,
        )
        context_packet = SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            query_text=(
                f"{chapter.chapter_goal} "
                f"{scene.title or ''} "
                f"{scene.purpose.get('story', '')} "
                f"{' '.join(scene.participants)}"
            ).strip(),
            story_bible=story_bible_context,
            recent_scene_summaries=[],
            recent_timeline_events=[],
            participant_canon_facts=[],
            active_plot_arcs=[],
            active_arc_beats=[],
            unresolved_clues=[],
            planned_payoffs=[],
            active_emotion_tracks=[],
            active_antagonist_plans=[],
            chapter_contract=None,
            scene_contract=None,
            tree_context_nodes=[],
            retrieval_chunks=[],
        )
    fallback_content = render_scene_draft_markdown(
        project,
        chapter,
        scene,
        style_guide,
        _packet_story_bible_context(context_packet),
        _packet_retrieval_context(context_packet),
        _packet_recent_scene_summaries(context_packet),
        _packet_recent_timeline_events(context_packet),
        _packet_participant_canon_facts(context_packet),
        _packet_active_plot_arcs(context_packet),
        _packet_active_arc_beats(context_packet),
        _packet_unresolved_clues(context_packet),
        _packet_planned_payoffs(context_packet),
        _packet_chapter_contract(context_packet),
        _packet_scene_contract(context_packet),
        _packet_tree_context(context_packet),
        _packet_emotion_tracks(context_packet),
        _packet_antagonist_plans(context_packet),
    )
    model_name = "mock-writer"
    llm_run_id: UUID | None = None
    generation_mode = "template-fallback"
    content_md = fallback_content
    if settings is not None:
        system_prompt, user_prompt = build_scene_draft_prompts(
            project,
            chapter,
            scene,
            style_guide,
            _packet_story_bible_context(context_packet),
            _packet_retrieval_context(context_packet),
            _packet_recent_scene_summaries(context_packet),
            _packet_recent_timeline_events(context_packet),
            _packet_participant_canon_facts(context_packet),
            _packet_active_plot_arcs(context_packet),
            _packet_active_arc_beats(context_packet),
            _packet_unresolved_clues(context_packet),
            _packet_planned_payoffs(context_packet),
            _packet_chapter_contract(context_packet),
            _packet_scene_contract(context_packet),
            _packet_tree_context(context_packet),
            _packet_emotion_tracks(context_packet),
            _packet_antagonist_plans(context_packet),
            hard_fact_snapshot=_packet_hard_fact_snapshot(context_packet),
            contradiction_warnings=getattr(context_packet, "contradiction_warnings", None) if context_packet else None,
            participant_knowledge_states=getattr(context_packet, "participant_knowledge_states", None) if context_packet else None,
            arc_summaries=getattr(context_packet, "arc_summaries", None) if context_packet else None,
            world_snapshot=getattr(context_packet, "world_snapshot", None) if context_packet else None,
            # Phase-1 wiring
            pacing_target=(
                context_packet.pacing_target.model_dump(mode="json")
                if context_packet and context_packet.pacing_target
                else None
            ),
            subplot_schedule=(
                [e.model_dump(mode="json") for e in context_packet.subplot_schedule]
                if context_packet and context_packet.subplot_schedule
                else None
            ),
            ending_contract=(
                context_packet.ending_contract.model_dump(mode="json")
                if context_packet and context_packet.ending_contract
                else None
            ),
            reader_knowledge_entries=(
                [e.model_dump(mode="json") for e in context_packet.reader_knowledge_entries]
                if context_packet and context_packet.reader_knowledge_entries
                else None
            ),
            relationship_milestones=(
                [e.model_dump(mode="json") for e in context_packet.relationship_milestones]
                if context_packet and context_packet.relationship_milestones
                else None
            ),
            # Phase-2 wiring
            structure_beat_name=(
                context_packet.structure_beat_name if context_packet else None
            ),
            structure_beat_description=(
                context_packet.structure_beat_description if context_packet else None
            ),
            # Phase-3 wiring
            swain_pattern=(
                context_packet.swain_pattern if context_packet else None
            ),
            scene_skeleton=(
                context_packet.scene_skeleton if context_packet else None
            ),
            genre_obligations_due=(
                context_packet.genre_obligations_due if context_packet else None
            ),
            foreshadowing_gap_warning=(
                context_packet.foreshadowing_gap_warning if context_packet else None
            ),
            identity_constraint_block=(
                context_packet.identity_constraint_block if context_packet else None
            ),
            overused_phrase_block=(
                context_packet.overused_phrase_block if context_packet else None
            ),
            genre_constraint_block=(
                context_packet.genre_constraint_block if context_packet else None
            ),
            opening_diversity_block=(
                context_packet.opening_diversity_block if context_packet else None
            ),
            conflict_diversity_block=(
                context_packet.conflict_diversity_block if context_packet else None
            ),
            scene_purpose_diversity_block=(
                context_packet.scene_purpose_diversity_block if context_packet else None
            ),
            env_diversity_block=(
                context_packet.env_diversity_block if context_packet else None
            ),
            arc_beat_block=(
                context_packet.arc_beat_block if context_packet else None
            ),
            five_layer_block=(
                context_packet.five_layer_block if context_packet else None
            ),
            cliffhanger_diversity_block=(
                context_packet.cliffhanger_diversity_block if context_packet else None
            ),
            tension_target_block=(
                context_packet.tension_target_block if context_packet else None
            ),
            location_ledger_block=(
                context_packet.location_ledger_block if context_packet else None
            ),
            budget_diversity_block=(
                context_packet.budget_diversity_block if context_packet else None
            ),
            scene_scope_isolation_block=(
                context_packet.scene_scope_isolation_block if context_packet else None
            ),
            plan_richness_block=(
                context_packet.plan_richness_block if context_packet else None
            ),
            reader_contract_block=(
                context_packet.reader_contract_block if context_packet else None
            ),
            hype_constraints_block=(
                context_packet.hype_constraints_block if context_packet else None
            ),
            context_budget_tokens=(
                settings.generation.context_budget_tokens if settings else 6000
            ),
        )
        # Inject voice drift correction prompts for scene participants
        proj_metadata = getattr(project, "metadata_json", None) or {}
        voice_corrections = proj_metadata.get("voice_corrections", {}) if isinstance(proj_metadata, dict) else {}
        if voice_corrections and scene.participants:
            _vc_is_en = is_english_language(_project_language(project))
            correction_lines: list[str] = []
            for participant in scene.participants:
                correction = voice_corrections.get(participant)
                if correction:
                    _vc_label = f"[{participant} Voice Correction]" if _vc_is_en else f"【{participant}语音修正】"
                    correction_lines.append(f"{_vc_label}{correction}")
            if correction_lines:
                system_prompt += "\n\n" + "\n".join(correction_lines)
        _model_tier = _determine_model_tier(
            chapter,
            scene,
            _packet_chapter_contract(context_packet),
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                model_tier=_model_tier,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_content,
                prompt_template="scene_writer",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "context_query": context_packet.query_text,
                    "model_tier": _model_tier,
                },
            ),
        )
        if completion.provider == "fallback":
            # LLM call failed after all retries. Log clearly but let the
            # pipeline continue — the chapter-level guard in
            # render_chapter_draft_markdown will raise if ALL scenes failed.
            logger.error(
                "Scene %d.%d LLM writer FAILED — using fallback placeholder. "
                "model=%s finish_reason=%s",
                chapter_number,
                scene_number,
                completion.model_name,
                completion.finish_reason,
            )
        content_md = sanitize_novel_markdown_content(completion.content, language=_project_language(project)) or fallback_content
        content_md = strip_scaffolding_echoes(content_md)
        # LLM-based cleanup if regex sanitizer missed meta-commentary
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

        # ── L4 + L4.5: scene-scope quality gate with bounded regen ──
        # Runs AFTER meta-leak cleanup so the validator sees the published
        # text, not LLM scaffolding. Checks that self-exempt at scene scope
        # (length envelope, entity density) are cheap no-ops. Language /
        # naming / dialog integrity / POV lock run here and earn their keep.
        scene_regen_count = 0
        scene_validator, scene_ctx = await _build_scene_validator(session, project)
        if scene_validator is not None and scene_ctx is not None:
            (
                content_md,
                regen_model_name,
                regen_llm_run_id,
                regen_provider,
                scene_regen_count,
            ) = await _regenerate_scene_until_valid(
                session=session,
                settings=settings,
                project=project,
                chapter_number=chapter_number,
                scene_number=scene_number,
                initial_content=content_md,
                validator=scene_validator,
                ctx=scene_ctx,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_content=fallback_content,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                model_tier=_model_tier,
                context_query=context_packet.query_text,
                global_budget=None,
            )
            if scene_regen_count > 0:
                # Overwrite with the retry's provenance so the draft row
                # points to the accepted attempt rather than the rejected one.
                if regen_model_name:
                    model_name = regen_model_name
                if regen_llm_run_id:
                    llm_run_id = regen_llm_run_id
                generation_mode = f"regen_{regen_provider}"
                logger.info(
                    "scene %d.%d: regenerated %d time(s) before passing gate",
                    chapter_number,
                    scene_number,
                    scene_regen_count,
                )
    else:
        content_md = strip_scaffolding_echoes(sanitize_novel_markdown_content(content_md))
        scene_regen_count = 0
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

    draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        is_current=True,
        model_name=model_name,
        prompt_template="scene_writer",
        prompt_version="1.0",
        llm_run_id=llm_run_id,
        generation_params={
            "mode": generation_mode,
            "scene_type": scene.scene_type,
            "target_word_count": scene.target_word_count,
            "story_bible_context_used": bool(_packet_story_bible_context(context_packet)),
            "recent_scene_count": len(_packet_recent_scene_summaries(context_packet)),
            "recent_timeline_count": len(_packet_recent_timeline_events(context_packet)),
            "participant_fact_count": len(_packet_participant_canon_facts(context_packet)),
            "active_arc_count": len(_packet_active_plot_arcs(context_packet)),
            "active_beat_count": len(_packet_active_arc_beats(context_packet)),
            "unresolved_clue_count": len(_packet_unresolved_clues(context_packet)),
            "emotion_track_count": len(_packet_emotion_tracks(context_packet)),
            "antagonist_plan_count": len(_packet_antagonist_plans(context_packet)),
            "tree_context_count": len(_packet_tree_context(context_packet)),
            "retrieval_chunk_count": len(_packet_retrieval_context(context_packet)),
            "regen_count": int(scene_regen_count),
            # Hype assignment — read by assemble_chapter_draft to stamp the
            # chapter row + register the moment on DiversityBudget.
            "assigned_hype_type": (
                context_packet.assigned_hype_type if context_packet else None
            ),
            "assigned_hype_recipe_key": (
                context_packet.assigned_hype_recipe_key if context_packet else None
            ),
            "assigned_hype_intensity": (
                context_packet.assigned_hype_intensity if context_packet else None
            ),
        },
    )
    session.add(draft)
    scene.status = SceneStatus.DRAFTED.value
    chapter.status = ChapterStatus.DRAFTING.value
    await session.flush()
    return draft


async def assemble_chapter_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    *,
    settings: AppSettings | None = None,
) -> ChapterDraftVersionModel:
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
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards to assemble.")

    scene_drafts: list[SceneDraftVersionModel] = []
    missing_scenes: list[int] = []
    for scene in scenes:
        draft = await session.scalar(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.scene_card_id == scene.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
        if draft is None:
            missing_scenes.append(scene.scene_number)
            continue
        scene_drafts.append(draft)

    if missing_scenes:
        missing = ", ".join(str(scene_number) for scene_number in missing_scenes)
        raise ValueError(
            f"Chapter {chapter_number} cannot be assembled because current drafts are missing for scenes: {missing}."
        )

    content_md = render_chapter_draft_markdown(chapter, scene_drafts, language=project.language)

    # ── Post-assembly intra-chapter deduplication ──
    # Detect and remove repeated paragraph blocks that can occur when multiple
    # scenes accidentally reproduce the same dialog or action.
    try:
        from bestseller.services.deduplication import (
            clean_meta_text_markers,
            detect_chapter_text_loop,
            detect_intra_chapter_repetition,
            detect_short_cluster_near_repeat,
            remove_chapter_text_loops,
            remove_intra_chapter_duplicates_paraphrase,
            remove_short_cluster_near_repeats,
        )

        # 1. Strip author/tool meta-text markers (e.g. "**第28章 完**", "（本章完）")
        content_md, _meta_removed = clean_meta_text_markers(content_md)
        if _meta_removed:
            logger.info(
                "Chapter %d: removed %d meta-text marker(s) from draft.",
                chapter_number, _meta_removed,
            )

        # 2a. Block-loop detector — catch LLM "stuck-in-loop" failure mode
        # where a short-line sequence repeats N times. Must run *before*
        # per-paragraph dedup because the per-paragraph threshold (≥12 chars)
        # would skip short lines inside the loop.
        _loop_findings = detect_chapter_text_loop(content_md)
        if _loop_findings:
            logger.warning(
                "Chapter %d: %d LLM-loop block(s) detected after assembly — auto-collapsing.",
                chapter_number, len(_loop_findings),
            )
            for _lf in _loop_findings:
                logger.warning("  %s", _lf["message"])
            content_md, _loop_removed = remove_chapter_text_loops(content_md)
            logger.info(
                "Chapter %d: collapsed %d paragraph(s) from LLM-loop blocks.",
                chapter_number, _loop_removed,
            )

        # 2a-2. Fuzzy short-line cluster near-repeat — catches the failure mode
        # where two clusters of short lines echo each other with insertions/
        # deletions (so the exact-match block detector above misses them).
        _short_findings = detect_short_cluster_near_repeat(content_md)
        if _short_findings:
            logger.warning(
                "Chapter %d: %d short-line cluster near-repeat(s) — auto-collapsing.",
                chapter_number, len(_short_findings),
            )
            content_md, _short_removed = remove_short_cluster_near_repeats(content_md)
            logger.info(
                "Chapter %d: dropped %d short-line paragraph(s) from cluster repeats.",
                chapter_number, _short_removed,
            )

        # 2b. Remove intra-chapter duplicate paragraphs (byte-exact + paraphrased)
        _dup_findings = detect_intra_chapter_repetition(content_md)
        if _dup_findings:
            logger.warning(
                "Chapter %d: %d duplicate paragraph(s) detected after assembly — auto-removing.",
                chapter_number, len(_dup_findings),
            )
            for _f in _dup_findings:
                logger.warning("  %s", _f["message"])
            content_md, _removed = remove_intra_chapter_duplicates_paraphrase(content_md)
            logger.info("Chapter %d: removed %d duplicate paragraph(s).", chapter_number, _removed)
    except Exception:
        logger.debug("Post-assembly dedup failed (non-fatal)", exc_info=True)

    # ── L4/L5/L6 pre-write quality gate ──
    # Runs before the draft row + disk file land. L4 checks language/length/
    # naming/density, L5 checks dialog integrity & POV lock, L6 resolves the
    # per-violation block/audit_only mode from config/quality_gates.yaml.
    # Phase 1 only blocks on the high-confidence codes; audit-only codes
    # still get logged for future precision tuning.
    quality_gate_outcome = await _evaluate_chapter_quality_gate(
        session=session,
        project=project,
        chapter_number=chapter_number,
        content=content_md,
    )

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

    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=[str(scene_draft.id) for scene_draft in scene_drafts],
        is_current=True,
    )
    session.add(chapter_draft)
    chapter.current_word_count = word_count
    chapter.status = ChapterStatus.DRAFTING.value
    if quality_gate_outcome is not None:
        chapter.production_state = quality_gate_outcome

    # ── Reader Hype Engine — persist chapter metadata + register the moment ──
    # Scene drafts carry the assignment in ``generation_params``; all scenes of
    # the same chapter share the same pick (the per-chapter picker is
    # deterministic until ``register_hype_moment`` mutates the budget). Pick
    # the first non-null triple and use it as the chapter-level assignment.
    try:
        _hype_type: str | None = None
        _hype_recipe_key: str | None = None
        _hype_intensity: float | None = None
        for _sd in scene_drafts:
            _gp = dict(_sd.generation_params or {})
            if _gp.get("assigned_hype_type"):
                _hype_type = str(_gp["assigned_hype_type"])
                _hype_recipe_key = (
                    str(_gp["assigned_hype_recipe_key"])
                    if _gp.get("assigned_hype_recipe_key") is not None
                    else None
                )
                _hype_intensity = (
                    float(_gp["assigned_hype_intensity"])
                    if _gp.get("assigned_hype_intensity") is not None
                    else None
                )
                break
        if _hype_type:
            chapter.hype_type = _hype_type
            chapter.hype_recipe_key = _hype_recipe_key
            chapter.hype_intensity = _hype_intensity

            from bestseller.services.diversity_budget import (
                load_diversity_budget,
                save_diversity_budget,
            )
            from bestseller.services.hype_engine import HypeType as _HypeTypeEnum

            try:
                _hype_enum = _HypeTypeEnum(_hype_type)
            except ValueError:
                _hype_enum = None
            if _hype_enum is not None:
                _budget = await load_diversity_budget(session, project.id)
                _budget.register_hype_moment(
                    chapter_no=chapter_number,
                    hype_type=_hype_enum,
                    recipe_key=_hype_recipe_key,
                    intensity=float(_hype_intensity or 0.0),
                )
                await save_diversity_budget(session, _budget)
    except Exception:
        logger.warning(
            "Hype metadata persistence failed for chapter %d (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    await session.flush()

    # ── Eagerly sync disk file so web UI always shows current content ──
    if settings is not None:
        try:
            from pathlib import Path  # noqa: PLC0415

            output_path = (
                Path(settings.output.base_dir) / project.slug / f"chapter-{chapter_number:03d}.md"
            )
            # render_chapter_draft_markdown already prepends the canonical
            # chapter heading. Only prepend here if it's missing (e.g. the
            # content was sourced from an older code path that stored bare
            # prose). Avoids the "# 第N章 …" / "# 第N章 …" twin-heading bug.
            if _has_leading_chapter_heading(content_md, chapter_number):
                full_content = content_md
            else:
                heading = format_chapter_heading(
                    chapter_number, chapter.title, language=project.language
                )
                full_content = f"{heading}\n\n{content_md}"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(full_content, encoding="utf-8")
            logger.debug("Chapter %d: disk file synced → %s", chapter_number, output_path)
        except Exception:
            logger.debug("Chapter %d: disk file sync failed (non-fatal)", chapter_number, exc_info=True)

    return chapter_draft

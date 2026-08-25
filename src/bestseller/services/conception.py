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

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
import copy
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.blurb_pathology import (
    champion_swaps_protagonist,
    derive_book_jargon_terms,
    detect_blurb_pathology,
    truncate_at_sentence,
)
from bestseller.services.concept_lab import (
    coerce_concept_lab_bundle,
    render_concept_lab_prompt_block,
)
from bestseller.services.copy_flavor import pick_reader_facing
from bestseller.services.degradation_tracker import DegradationEvent, DegradationTracker
from bestseller.services.genre_intent_contract import GenreIntentContract

# Import GenreReviewProfile type for type hints; actual resolution is guarded.
from bestseller.services.genre_review_profiles import (
    GenreReviewProfile,
    resolve_genre_review_profile,
)
from bestseller.services.hook_propagation import coerce_hook_spec, render_hook_spec_prompt_block
from bestseller.services.llm import LLMCompletionRequest, LLMRole, complete_text
from bestseller.services.llm_closed_loop import build_repair_user_prompt, findings_from_exception
from bestseller.services.methodology import render_qimao_regeneration_contract
from bestseller.services.methodology_compiler import MethodologyStage, compile_methodology
from bestseller.services.novel_categories import (
    render_category_anti_patterns,
    render_category_reader_promise,
    resolve_novel_category,
)
from bestseller.services.planning_concurrency import run_in_isolated_session
from bestseller.services.platform_title_workflow import (
    build_story_dna_fallback_title,
    build_title_revision_messages,
    finalize_revised_title,
    is_bare_taxonomy_title,
    select_primary_platform_title,
    should_revise_primary_title,
)
from bestseller.services.progress_context import emit_activity, emit_milestone
from bestseller.services.writing_presets import list_genre_presets
from bestseller.services.writing_profile import (
    fold_near_duplicate_points,
    strip_enumeration_label,
    resolve_writing_profile,
    sanitize_genre_story_overrides,
)
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
    commercial_brief: dict[str, Any] = field(default_factory=dict)
    synopsis: str = ""
    tags: list[str] = field(default_factory=list)
    hook_spec: dict[str, Any] | None = None
    # Unified v2 concept lineage.  HookCard is the opening expression;
    # SerialityProof independently proves the requested long-form capacity.
    concept_contract: dict[str, Any] = field(default_factory=dict)
    hook_card: dict[str, Any] = field(default_factory=dict)
    seriality_proof: dict[str, Any] = field(default_factory=dict)
    # Surfaced so the web layer can persist these as inspectable book artifacts.
    concept_methodology: dict[str, Any] = field(default_factory=dict)
    hook_candidates: list[dict[str, Any]] = field(default_factory=list)
    # Story/blurb appeal evaluation report (story_appeal.StoryAppealReport.to_dict()).
    # Empty dict when the appeal system is disabled (config) — keeps historical
    # output byte-identical (no-op contract).
    story_appeal: dict[str, Any] = field(default_factory=dict)
    # 故事脊柱(2026-07-08 框架层):谁+要什么+为什么现在+谁挡着+代价+读者追问。
    # 全管线传导的故事核;确定性验收见 story_spine.validate_story_spine。
    story_spine: dict[str, Any] = field(default_factory=dict)
    # 世界模型(2026-07-08 设定/逻辑框架层):终稿草稿前提差分出的世界规律,
    # 供 planner/prose 复用同一份世界宪法(避免二次派生产生的漂移)。
    # 见 world_model_deriver.derive_world_model / domain.world_model.WorldModel。
    world_model: dict[str, Any] = field(default_factory=dict)
    # 母题放大报告(2026-08-09):一个词是否占满了全部设计轴,以及那一次重写有没有
    # 解决它。advisory——它只换一次重写,永不阻断建书;但结论必须落库可查,否则
    # 等于没检测(见 motif_concentration)。
    motif_amplification: dict[str, Any] = field(default_factory=dict)
    # 默认族(债务/账本)门的结论(2026-08-24):检出/重生成后是否仍在族内/是否采纳。
    # 此前只写进 ctx["default_family_report"] —— 零消费方、不落库。真机书9
    # (零创意种子)构思当时 is_debt_dominated=True 且 user_requested_debt=False,
    # debt_hit 必然成立,可全书上下找不到一条痕迹,事后无法区分「门没跑」与
    # 「门跑了但没救回来」。与 motif_amplification / title_tournament 同一形状,
    # 同一结论:回执不落库等于没检测。
    default_family: dict[str, Any] = field(default_factory=dict)
    # 书名淘汰赛回执(2026-08-22):谁参赛、谁被哪道确定性门淘汰、谁赢、赢了几票。
    # 首跑时我把它写进 writing_profile.market，**被 extra=ignore 吃掉了**——
    # 与既有的 title_workflow_primary 同样下场。回执不落库等于没留痕，
    # 照 motif_amplification 的先例走一等字段。
    title_tournament: dict[str, Any] = field(default_factory=dict)
    # 降级追踪(2026-07-10):记录哪些轮次/门触发了 fallback 或异常。
    # 如 ("market_strategist:fallback", "concept_tournament:error")。
    # 下游可据此感知 conception 质量降级。
    degraded_rounds: tuple[DegradationEvent, ...] = ()
    degraded: bool = False
    degradation_events: tuple[DegradationEvent, ...] = ()


class ConceptionRequiredLaneError(RuntimeError):
    """Strict-quality conception was blocked by a required lane degradation."""

    code = "conception_required_lane_blocked"

    def __init__(
        self,
        event: DegradationEvent,
        *,
        blocking_events: tuple[DegradationEvent, ...] | None = None,
    ) -> None:
        self.event = event
        self.blocking_events = blocking_events or (event,)
        super().__init__(
            f"Conception required lane blocked: component={event.component} "
            f"stage={event.stage} reason={event.reason}"
        )


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
    try:
        from json_repair import repair_json

        repaired = repair_json(stripped, return_objects=True)
        if isinstance(repaired, dict):
            logger.warning(
                "Conception JSON repaired via json-repair (orig_len=%d).",
                len(text),
            )
            return repaired
    except Exception:
        pass
    logger.warning(
        "Failed to extract JSON from LLM output (len=%d): %.200s...",
        len(text),
        text,
    )
    raise ValueError("Conception LLM output does not contain valid JSON.")


def _safe_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    val = data.get(key)
    return val if val is not None else default


def _attach_conception_methodology(
    user_prompt: str,
    *,
    ctx: dict[str, Any],
    is_en: bool,
    token_budget: int = 800,
) -> str:
    """Prepend stage=CONCEPTION methodology to zh conception prompts."""

    if is_en:
        return user_prompt
    market = ctx.get("market")
    compiled = compile_methodology(
        stage=MethodologyStage.CONCEPTION,
        prompt_pack_key=ctx.get("prompt_pack_key")
        or (market.get("prompt_pack_key") if isinstance(market, dict) else None),
        language=str(ctx.get("language") or "zh-CN"),
        token_budget=token_budget,
    )
    if not compiled.text:
        return user_prompt
    return f"{compiled.text}\n\n---\n\n{user_prompt}"


def _compact_conception_proposal(value: Any, *, max_text: int = 900) -> Any:
    """Keep review-stage proposals short while preserving keys and decisions."""

    if isinstance(value, str):
        text = value.strip()
        return text if len(text) <= max_text else text[:max_text] + "..."
    if isinstance(value, list):
        return [_compact_conception_proposal(item, max_text=max_text // 2) for item in value[:8]]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in list(value.items())[:20]:
            compact[str(key)] = _compact_conception_proposal(item, max_text=max_text)
        return compact
    return value


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


# (2026-08-02) The family-trauma motif regexes were retired together with the
# rest of the motif police. They matched any sentence linking a relative to a
# death, disappearance, secret, or bloodline — i.e. the premise of a large share
# of published fiction — and rewrote it. Kept as never-matching patterns so the
# few remaining call sites stay valid.
_NEVER_MATCH = re.compile(r"(?!x)x")
_ZH_DEFAULT_MOTIF_RE = _NEVER_MATCH
_EN_DEFAULT_MOTIF_RE = _NEVER_MATCH

_ZH_DEFAULT_MOTIF_REPLACEMENT = "由本书题材核心机制触发的具体危机与选择代价"
_EN_DEFAULT_MOTIF_REPLACEMENT = "a genre-specific initiating crisis with visible choice costs"

# Methodology guidance (NOT a hardcoded form): the golden finger is a mandatory
# *commercial* element for male-channel progression fiction, but its FORM must
# NOT default to a 系统/属性面板. Pinning it to one form ("挂系统") is exactly
# what makes every book read the same → cliché → nobody clicks. Give the model a
# rich form pool + a fit-driven selection rule + an opt-out for genres that do
# not need an external cheat, and let it choose the freshest form that grows out
# of THIS book's world rules. This is injected into the conception prompts.
_GOLDEN_FINGER_DESIGN_PRINCIPLE = (
    "## 主角差异化优势设计原则\n"
    "优势必须从本书已经选定的主角身份、当前目标、题材原生资源和世界规律中推导，"
    "不得先套一个平台常见外挂，再给它换名字。先说明主角具体能做成什么、怎样改变局面，"
    "再决定是否需要显性能力；不依赖外挂的题材可以直接使用谋略、技能、人脉或意志作为优势。\n"
    "不要向模型提供可照抄的能力菜单。至少在内部比较三种不同原理，只保留最贴合本书冲突、"
    "最能引发对手反应和后续局面变化的一种。\n"
    "限制也不是必填槽位。只有能从该优势的作用过程自然推出时才写，并落实为行动造成的"
    "具体资源、关系、暴露或身体状态变化；推导不出来就不额外安装统一收费装置。"
)
_GOLDEN_FINGER_DESIGN_PRINCIPLE_EN = (
    "## Protagonist differentiating-edge design rule\n"
    "Derive the edge from the selected protagonist, present goal, genre-native resources, "
    "and world rules. Do not start from a platform-standard power template and rename it. "
    "State what the protagonist can concretely accomplish and how it changes the situation "
    "before deciding whether an explicit power is needed.\n"
    "Do not provide or copy a menu of power forms. Internally compare three different "
    "principles and keep only the one that best fits this book and provokes changing responses.\n"
    "A limitation is optional. Include one only when it follows naturally from use, as a "
    "concrete change in resources, relationships, exposure, or physical state; otherwise do "
    "not install a generic charging device."
)


def _default_motif_guardrail(ctx: dict[str, Any] | None = None, *, is_en: bool | None = None) -> str:
    """(2026-08-01 product ruling) Returns "" — guardrails no longer enter prompts.

    A guard rendered into the prompt is itself an injection: it makes the
    framework's fear part of every book's context. Default-motif drift is
    caught only on the OUTPUT side (deterministic candidate/facet detectors in
    ``anti_default_motif``), whose retry feedback names no vocabulary. The
    function is kept so 22 call sites stay no-ops until they are swept away.
    """

    del ctx, is_en
    return ""


# Financial/ledger vocabulary that keeps colonising cultivation golden fingers.
# Canonical bank lives in ``anti_default_motif`` so the conception / planning /
# prose layers can never drift apart (the cross-book leakage test enforces sync).
from bestseller.services.anti_default_motif import (  # noqa: E402
    DEBT_LEDGER_TOKENS as _DEBT_LEDGER_TOKENS,
)
from bestseller.services.anti_default_motif import (
    contains_default_death_motif as _contains_default_death_motif,
)
from bestseller.services.anti_default_motif import (
    default_debt_family_hits as _default_debt_family_hits,
)
from bestseller.services.anti_default_motif import (
    is_anonymous_death_dominated as _is_anonymous_death_dominated,
)
from bestseller.services.anti_default_motif import (
    is_death_revival_dominated as _is_death_revival_dominated,
)
from bestseller.services.anti_default_motif import (
    is_debt_dominated as _canonical_is_debt_dominated,
)
from bestseller.services.anti_default_motif import (
    mentions_death_theme as _mentions_death_theme,
)
from bestseller.services.anti_default_motif import (
    snapshot_user_intent as _snapshot_user_intent,
)
from bestseller.services.anti_default_motif import (
    user_requested_death_revival as _user_requested_death_revival,
)
from bestseller.services.anti_default_motif import (
    user_requested_debt as _user_requested_debt,
)

# Vocabulary-free successor to the retired debt/ledger police (2026-08-09).
from bestseller.services.motif_concentration import (  # noqa: E402
    AmplifiedMotif,
    detect_profile_motif_amplification,
)
from bestseller.services.motif_concentration import (
    flatten_text as _motif_flatten,
)
from bestseller.services.motif_concentration import (
    render_motif_amplification_feedback as _render_motif_amplification_feedback,
)
from bestseller.services.story_enhancers import (  # noqa: E402
    reader_contract_labels,
)


def _mentions_debt_theme(*texts: Any) -> bool:
    """True when any text already frames the book around debt/lending.

    Used to *respect user intent*: a book the user deliberately wants about
    debt collection must not be gagged by the anti-debt guardrail.
    """

    blob = " ".join(str(t) for t in texts if t)
    return any(token in blob for token in _DEBT_LEDGER_TOKENS)


def _is_debt_dominated_mechanism(text: Any) -> bool:
    """Compatibility wrapper around the shared concentration-based detector.

    NOT a shim any more (2026-08-14 靶向复活): it delegates to the canonical
    concentration detector, which requires ≥2 sub-families or a density above
    the human-corpus p95 before calling a concept debt-dominated. The docstring
    that still called it "always False" cost a full diagnosis round on
    2026-08-24 —— 注释过时会把查案的人送去错误的方向。
    ``_motif_amplification_hits`` below remains the vocabulary-free companion.
    """

    return _canonical_is_debt_dominated(text)


def _champion_story_text(high_concept: Any) -> str:
    """The approved champion's STORY facts, without any judging commentary.

    ``ctx["high_concept"]`` is the winning candidate's full dict, which carries
    ``judge_reason`` / ``rejected_reason`` / ``seriality_judge`` alongside the
    story. Those fields quote the story back at itself, so including them in the
    amplification baseline both enlarges the denominator and hands the baseline
    the very words being measured — the gate then under-detects.
    """

    if not isinstance(high_concept, Mapping):
        return ""
    pieces = [
        _motif_flatten(high_concept.get(field))
        for field in _ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS
    ]
    # A champion may also arrive already distilled into a hook card.
    for field in ("hook_card", "one_liner", "premise", "synopsis"):
        pieces.append(_motif_flatten(high_concept.get(field)))
    return "\n".join(piece for piece in pieces if piece.strip())


_MARKET_COMPETITORS_CTX_KEY = "_market_competitor_rows"


def _market_competitor_rows(ctx: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """On-board rival books, prefetched into ``ctx`` before the tournament."""

    rows = ctx.get(_MARKET_COMPETITORS_CTX_KEY)
    return tuple(rows) if isinstance(rows, (list, tuple)) else ()


async def _prefetch_market_competitors(
    session: AsyncSession, settings: AppSettings, ctx: dict[str, Any]
) -> None:
    """Pull the current board for this genre so the tournament can use it.

    Ordering is the entire point. Market validation already existed but ran
    AFTER conception finalized, writing a summary next to a title/premise/blurb
    that were already locked — a receipt, not an input, and
    ``metadata["market_validation_summary"]`` was never non-empty for a single
    project in the database. The documented process starts with "does the market
    already have this book?"; that question can only change an outcome while the
    candidates are still being picked.

    Fail-open and advisory: no rows simply means the tournament screens nothing,
    exactly as before. Never raises, never gates, never touches a prompt.
    """

    if not bool(
        getattr(getattr(settings, "pipeline", None), "enable_market_validation", False)
    ):
        return
    try:
        from bestseller.services.market_validation.request_builder import (
            build_creation_request,
        )
        from bestseller.services.market_validation.service import (
            run_market_validation,
        )

        report = await run_market_validation(
            build_creation_request(
                metadata={},
                genre_label=str(ctx.get("genre") or ""),
                sub_genre_label=str(ctx.get("sub_genre") or ""),
                title="",
                concept="",
                blurb="",
                fallback_genre_key=str(ctx.get("genre_key") or ""),
                project_slug="",
            ),
            settings=settings,
            session=session,
        )
        seen: dict[str, dict[str, Any]] = {}
        for book in [
            *report.genre_heat.top_books,
            *report.competitor_scan.competitors,
        ]:
            title = str(getattr(book, "title", "") or "").strip()
            if title and title not in seen:
                seen[title] = {
                    "title": title,
                    "intro": str(getattr(book, "intro", "") or ""),
                    "tags": [str(t) for t in (getattr(book, "tags", None) or [])],
                }
        ctx[_MARKET_COMPETITORS_CTX_KEY] = list(seen.values())
        logger.info(
            "market prefetch: %d on-board competitors available to the tournament",
            len(seen),
        )
    except Exception:
        logger.warning("market competitor prefetch failed (fail-open)", exc_info=True)


def _motif_amplification_hits(
    result: Any, approved_source: str
) -> tuple[AmplifiedMotif, ...]:
    """One token owning every design axis of the generated writing profile.

    ``approved_source`` is what the user asked for plus the tournament champion
    they approved. Measuring against it is what separates "this book is about a
    ledger" (fine — the source says so) from "an incidental noun became the
    golden finger, the romance mode and the per-chapter law" (the 2026-08-09
    《灵根废我用烂账翻盘》 defect). Fail-open: a detector fault must never stop a
    conception.
    """

    if not isinstance(result, Mapping):
        return ()
    profile = result.get("writing_profile")
    if not isinstance(profile, Mapping):
        return ()
    try:
        return detect_profile_motif_amplification(
            profile, source_text=approved_source
        )
    except Exception:
        logger.warning("motif amplification scan failed", exc_info=True)
        return ()


def _anti_debt_metaphor_guardrail(ctx: dict[str, Any] | None = None, *, is_en: bool) -> str:
    """(2026-08-01 product ruling) Returns "" — guardrails no longer enter prompts.

    The debt twin of ``_default_motif_guardrail``; same reasoning. Drift is
    caught by the output-side detectors (``_is_debt_dominated_mechanism`` and
    the candidate hard-rejection gates), never by prompt text.
    """

    del ctx, is_en
    return ""


def _sanitize_forbidden_default_motifs(value: Any, *, is_en: bool) -> Any:
    """Retired 2026-08-02 — returns the payload untouched.

    This walked every string in a conception payload and regex-replaced any
    family-trauma phrasing with one framework-authored sentence ("由本书题材核心
    机制触发的具体危机与选择代价"). It was censorship and injection at once: the
    book lost its own words, and every book that tripped it received the same
    substitute text. A protagonist whose parents died is a premise, not a defect.
    """

    del is_en
    return value


def _is_qimao_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return "七猫" in text or "qimao" in text


def _qimao_regeneration_prompt_block(ctx: dict[str, Any]) -> str:
    market = {}
    overrides = ctx.get("existing_overrides")
    if isinstance(overrides, dict) and isinstance(overrides.get("market"), dict):
        market = overrides["market"]
    platform_target = (
        market.get("platform_target")
        or ctx.get("platform_target")
        or ctx.get("default_platform")
    )
    block = render_qimao_regeneration_contract(
        platform_target=str(platform_target or ""),
        language=str(ctx.get("language") or "zh-CN"),
        rejection_reasons=ctx.get("editor_rejection_reasons"),
    )
    return f"\n\n{block}\n" if block else ""


def _apply_qimao_hints_to_context(ctx: dict[str, Any]) -> None:
    hints = ctx.get("user_hints")
    if not isinstance(hints, dict):
        return
    requested_platform = (
        hints.get("platform_target")
        or hints.get("platform")
        or hints.get("target_platform")
    )
    if not _is_qimao_text(requested_platform):
        return
    ctx["default_platform"] = "七猫小说"
    ctx["platform_target"] = "七猫小说"
    if "七猫小说" not in ctx.get("recommended_platforms", []):
        ctx["recommended_platforms"] = ["七猫小说", *ctx.get("recommended_platforms", [])]
    overrides = ctx.setdefault("existing_overrides", {})
    if isinstance(overrides, dict):
        market = overrides.setdefault("market", {})
        if isinstance(market, dict):
            market["platform_target"] = "七猫小说"
            market.setdefault(
                "opening_contract",
                "第一章禁止普通日常/背景/风景开场；必须从异常、危机、误会、侮辱、损失、利益冲突或被迫选择切入。",
            )
    reasons = hints.get("editor_rejection_reasons") or hints.get("rejection_reasons")
    if reasons:
        ctx["editor_rejection_reasons"] = reasons


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
    degradation_tracker: DegradationTracker | None = None,
    degradation_stage: str | None = None,
    degradation_component: str | None = None,
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
    if degradation_tracker is not None:
        result_metadata = getattr(result, "metadata", None)
        if not isinstance(result_metadata, dict):
            result_metadata = {}
        role_settings = getattr(getattr(settings, "llm", None), role, None)
        primary_model = getattr(result, "effective_primary_model", None) or getattr(
            role_settings, "model", None
        )
        configured_fallback_model = getattr(
            role_settings, "rate_limit_fallback_model", None
        )
        result_model = getattr(result, "model_name", None)
        provider_fallback = (
            bool(getattr(result, "fallback_used", False))
            or
            getattr(result, "provider", None) == "fallback"
            or getattr(result, "finish_reason", None) == "fallback"
        )
        configured_model_fallback = bool(
            configured_fallback_model
            and result_model == configured_fallback_model
            and result_model != primary_model
        )
        metadata_fallback = bool(
            result_metadata.get("rate_limit_fallback_active")
            or result_metadata.get("rate_limit_fallback_primary_model")
            or result_metadata.get("fallback_model")
        )
        if provider_fallback or configured_model_fallback or metadata_fallback:
            degradation_tracker.record(
                stage=degradation_stage or template,
                component=degradation_component or template,
                reason=(
                    str(getattr(result, "fallback_source", None) or "provider_fallback")
                    if provider_fallback
                    else "model_fallback"
                ),
                severity="error",
                fallback=True,
                model=result_model,
                metadata={
                    "primary_model": primary_model,
                    "configured_fallback_model": configured_fallback_model,
                    **result_metadata,
                },
            )
    return result.content, result.llm_run_id


def _safe_extract_json(text: str) -> dict[str, Any]:
    """书名工序专用的不抛版 JSON 解析——解析失败只当作「没有候选」。

    书名不该有能力阻断建书：`_extract_json` 解析失败会抛，
    这里吞掉并返回空字典，让淘汰赛静默退回现任书名。
    """

    try:
        return _extract_json(text)
    except Exception:
        return {}


async def _run_title_tournament(
    session: AsyncSession,
    settings: AppSettings,
    *,
    incumbent: str,
    title_profile: Mapping[str, Any],
    golden_finger: str,
    logline: str,
    genre_label: str,
    platform_label: str,
    is_en: bool,
    user_named_default_family: bool = False,
) -> tuple[str, dict[str, Any] | None, list[UUID]]:
    """书名淘汰赛：候选 → 确定性门 → 默认族降权 → 榜单并排盲评 → 胜出。

    2026-08-21 真机 custom-xuanhuan-1787320762 定罪：书名此前是**单发调用**
    （`title_platform_revision` 全书仅 1 次），而概念有 16 次判官 + 4 轮候选。
    更糟的是它上游的 65 个模板候选全是把分类标签塞进槽位
    （「开局市井日常，我用市井日常证道」），因为槽位词来自 tags、
    物件抽取器写死了**上一本书**的清单。用户原话：「更像是一些字符串的拼接」。

    这里另起一条按**故事实体**竞争的路。三条自我约束：
      * 全程 fail-open——任何异常都保留现任书名，不让书名工序阻断建书；
      * **判官只挣排序权**，确定性门（接地/长度/查重）才有否决权；
      * 留回执，让「书名为什么是这个」以后可查（真机上这件事查不到任何记录）。
    """

    run_ids: list[UUID] = []
    if is_en:
        # 句式家族按中文网文书名总结，英文书走原路径。
        return incumbent, None, run_ids

    from bestseller.services.title_tournament import (
        TitleCandidate,
        apply_arena_verdict,
        build_title_arena_messages,
        build_title_candidate_messages,
        deterministic_title_defects,
        extract_title_entities,
        demote_default_family_titles,
        parse_title_candidates,
        select_title_winner,
        title_tournament_receipt,
    )

    entities = extract_title_entities(
        protagonist_name=str(
            ((title_profile.get("main_characters") or [{}])[0] or {}).get("name") or ""
        ),
        golden_finger=golden_finger,
        logline=logline,
        premise=logline,
    )
    if entities.is_empty:
        # 抽不出任何实体就没有竞争的基础——不要靠标签硬凑（那正是旧病）。
        return incumbent, None, run_ids

    cand_system, cand_user = build_title_candidate_messages(
        entities=entities,
        logline=logline,
        genre_label=genre_label,
        platform_label=platform_label or "番茄小说",
    )
    raw, cand_run_id = await _llm_call(
        session,
        settings,
        role="editor",
        system_prompt=cand_system,
        user_prompt=cand_user,
        fallback="{}",
        template="title_tournament_candidates",
    )
    if cand_run_id is not None:
        run_ids.append(cand_run_id)
    candidates = parse_title_candidates(_safe_extract_json(raw))
    if not candidates:
        return incumbent, None, run_ids

    # 现任书名也进场比——不是替换，是竞争。
    if incumbent.strip() and all(row.title != incumbent.strip() for row in candidates):
        candidates.append(TitleCandidate(title=incumbent.strip(), family="现任"))

    tags = list(title_profile.get("tags") or [])
    prose = " ".join(
        str(title_profile.get(key) or "")
        for key in ("logline", "short_intro", "reader_promise")
    )
    for row in candidates:
        row.rejected_by = deterministic_title_defects(
            row.title, tags=tags, prose=prose
        )

    survivors = [row for row in candidates if row.survives]
    # 默认族降权（2026-08-24）：书名是读者最先看到的东西，而跨书量下来
    # 2/6 本书名带债务族、两条路径各一本。比较式，全池同族不清空。
    survivors, _title_family_demoted = demote_default_family_titles(
        survivors, user_named_family=user_named_default_family
    )
    _demoted_titles = {row.title for row in _title_family_demoted}
    if _demoted_titles:
        for row in candidates:
            if row.title in _demoted_titles:
                row.rejected_by = [
                    *(row.rejected_by or []),
                    "default_family_in_title",
                ]
    if len(survivors) >= 2:
        arena_system, arena_user = build_title_arena_messages(
            titles=[row.title for row in survivors],
            logline=logline,
            genre_label=genre_label,
            platform_label=platform_label or "番茄小说",
        )
        arena_raw, arena_run_id = await _llm_call(
            session,
            settings,
            role="editor",
            system_prompt=arena_system,
            user_prompt=arena_user,
            fallback="{}",
            template="title_tournament_arena",
        )
        if arena_run_id is not None:
            run_ids.append(arena_run_id)
        apply_arena_verdict(survivors, _safe_extract_json(arena_raw))

    winner = select_title_winner(candidates, incumbent=incumbent)
    receipt = title_tournament_receipt(candidates, winner)
    receipt["entities"] = entities.to_dict()
    receipt["incumbent"] = incumbent
    return (winner.title if winner else incumbent), receipt, run_ids


def _title_profile_protagonist(
    *,
    metadata: Mapping[str, Any],
    premise: str,
    is_en: bool,
) -> str:
    """书名生成器要用的主角名。

    2026-08-21 真机 custom-xuanhuan-1787320762：这里原本写死成「主角」，
    书名生成器因此永远不知道主角叫什么，`_resolve_identity` 只好退回 tags，
    于是 65 个候选全是把分类标签塞模板（「开局市井日常，我用市井日常证道」）。

    取名顺序刻意与身份层一致：
      1. **用户显式选定**的名字（creation_protagonist_source 不是 LLM 推断）；
      2. 已批准构思正文里抽出的名字——真机上 creation_protagonist_name 是
         LLM 从**旧候选**猜的「沈小禾」，而定稿构思写的是「温迟」，
         猜来的名字不能盖过构思正文；
      3. 都拿不到才退回占位符（不制造新的失败模式）。
    """

    from bestseller.services.book_design import _has_explicit_protagonist_choice
    from bestseller.services.concept_entities import (
        extract_role_bound_name,
        is_placeholder_name,
    )

    placeholder = "Protagonist" if is_en else "主角"
    meta = metadata if isinstance(metadata, Mapping) else {}
    if _has_explicit_protagonist_choice(meta):
        for key in ("creation_protagonist_name", "protagonist_name", "canonical_protagonist_name"):
            explicit = str(meta.get(key) or "").strip()
            if explicit and not is_placeholder_name(explicit):
                return explicit
    extracted = extract_role_bound_name(premise or "")
    if extracted and not is_placeholder_name(extracted):
        return extracted
    return placeholder


async def _maybe_revise_platform_title(
    session: AsyncSession,
    settings: AppSettings,
    *,
    title_profile: dict[str, Any],
    primary_candidate: dict[str, Any],
    target_platform: str,
    workflow_title: str,
) -> tuple[str, bool, UUID | None]:
    """Optionally LLM-revise a weak platform title for platform-口播 fit.

    Returns ``(title, was_revised, llm_run_id)``. No-op (returns the original
    title) when the feature is disabled, the title is already strong (clean IP
    name / passing), or the revised candidate fails validation. See
    platform_title_workflow.py § P2 (2026-06-03 book-title regression fix).
    """

    if not getattr(settings.generation, "title_llm_revision_enabled", True):
        return workflow_title, False, None
    if not should_revise_primary_title(primary_candidate):
        return workflow_title, False, None
    messages = build_title_revision_messages(
        title_profile, primary_candidate, target_platform=target_platform
    )
    if messages is None:
        return workflow_title, False, None
    revision_system, revision_user = messages
    revised_raw, revision_llm_id = await _llm_call(
        session,
        settings,
        role="editor",
        system_prompt=revision_system,
        user_prompt=revision_user,
        # On LLM failure, fall back to the original title so finalize keeps it.
        fallback=workflow_title or "未命名",
        template="title_platform_revision",
    )
    adopted_title, was_revised = finalize_revised_title(
        title_profile,
        workflow_title,
        revised_raw,
        target_platform=target_platform,
    )
    return adopted_title, was_revised, revision_llm_id


async def _llm_call_json(
    session: AsyncSession,
    settings: AppSettings,
    *,
    role: LLMRole,
    system_prompt: str,
    user_prompt: str,
    fallback: str,
    template: str,
    stage: str,
    language: str | None = None,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    degradation_tracker: DegradationTracker | None = None,
    degradation_component: str | None = None,
) -> tuple[dict[str, Any], list[UUID]]:
    """Call a conception LLM stage and repair invalid JSON with diagnostics."""

    is_en = str(language or "").startswith("en")
    llm_run_ids: list[UUID] = []
    text, llm_id = await _llm_call(
        session,
        settings,
        role=role,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback=fallback,
        template=template,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        degradation_tracker=degradation_tracker,
        degradation_stage=stage,
        degradation_component=degradation_component or stage,
    )
    if llm_id is not None:
        llm_run_ids.append(llm_id)
    try:
        return _sanitize_forbidden_default_motifs(_extract_json(text), is_en=is_en), llm_run_ids
    except Exception as exc:
        findings = findings_from_exception(exc, default_path=stage)
        logger.warning(
            "Conception stage %s produced invalid JSON; retrying with diagnostics: %s",
            stage,
            [finding.code for finding in findings],
            exc_info=True,
        )

    repair_text, repair_llm_id = await _llm_call(
        session,
        settings,
        role=role,
        system_prompt=system_prompt,
        user_prompt=build_repair_user_prompt(
            original_user_prompt=user_prompt,
            findings=findings,
            language=language,
        ),
        fallback=fallback,
        template=f"{template}_repair",
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        degradation_tracker=degradation_tracker,
        degradation_stage=stage,
        degradation_component=degradation_component or stage,
    )
    if repair_llm_id is not None:
        llm_run_ids.append(repair_llm_id)
    try:
        payload = _sanitize_forbidden_default_motifs(_extract_json(repair_text), is_en=is_en)
        if degradation_tracker is not None:
            degradation_tracker.record(
                stage=stage,
                component=degradation_component or stage,
                reason="json_repair",
                severity="warning",
                fallback=False,
            )
        return payload, llm_run_ids
    except Exception:
        logger.warning(
            "Conception stage %s repair still produced invalid JSON; using fallback payload.",
            stage,
            exc_info=True,
        )
        try:
            payload = _sanitize_forbidden_default_motifs(_extract_json(fallback), is_en=is_en)
            if degradation_tracker is not None:
                degradation_tracker.record(
                    stage=stage,
                    component=degradation_component or stage,
                    reason="static_fallback",
                    severity="error",
                    fallback=True,
                )
            return payload, llm_run_ids
        except Exception:
            logger.error(
                "Conception stage %s: both repair and fallback payloads were "
                "unparseable; returning empty payload (downstream will degrade).",
                stage,
                exc_info=True,
            )
            if degradation_tracker is not None:
                degradation_tracker.record(
                    stage=stage,
                    component=degradation_component or stage,
                    reason="fallback_unparseable",
                    severity="critical",
                    fallback=True,
                )
            return {}, llm_run_ids


def _build_genre_context(
    genre_key: str,
    chapter_count: int,
    story_facets: object | None = None,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    genre_intent_contract: GenreIntentContract | None = None,
) -> dict[str, Any]:
    """Build context dict from genre preset for prompts.

    When story_facets is provided, enriches the context with multi-dimensional
    facet information for the conception agents.
    """
    presets = {p.key: p for p in list_genre_presets()}
    preset = presets.get(genre_key)
    if preset is None:
        # The free taxonomy picker (频道·题材·子题材·标签) yields a synthetic key
        # (e.g. ``custom-xuanhuan``) that is absent from the 62-card registry.
        # Rebuild a usable preset from the canonical taxonomy + the genre/
        # sub_genre carried alongside, instead of hard-failing conception.
        from bestseller.services.writing_presets import synthesize_genre_preset

        preset = synthesize_genre_preset(genre_key, genre=genre, sub_genre=sub_genre)

    is_en = preset.language.startswith("en")
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
        # Prompt-pack routing is framework-owned.  Keep it in the context even
        # for synthetic taxonomy presets so methodology/conception cannot fall
        # back to a model-inferred pack.
        "prompt_pack_key": preset.prompt_pack_key,
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

    # Taxonomy selection is authoritative.  Keep the contract visible to every
    # downstream prompt and re-assert its labels/pack over synthetic presets.
    if genre_intent_contract is not None:
        ctx["genre_intent_contract"] = genre_intent_contract.model_dump(mode="json")
        ctx["genre"] = genre_intent_contract.genre_label
        ctx["sub_genre"] = genre_intent_contract.sub_genre_label
        ctx["prompt_pack_key"] = genre_intent_contract.prompt_pack_key
        ctx["genre_intent_lock"] = (
            "题材契约是用户在建书时明确选择的权威事实。"
            f"本书只能写【{genre_intent_contract.genre_label}】"
            f"/【{genre_intent_contract.sub_genre_label or '未指定子题材'}】；"
            f"提示词包固定为【{genre_intent_contract.prompt_pack_key}】。"
            "StoryFacets、热度趋势和模型建议只能提供表层创意，禁止改写题材、"
            "子题材、现代性边界或提示词包。"
        )
        # The creation-page enhancer selection is carried by the immutable
        # contract, not by an untrusted free-form prompt. This keeps the
        # explicit wild-concept switch available to the tournament while
        # preventing unselected enhancers from appearing in context.
        ctx["wild_concept"] = bool(
            genre_intent_contract.explicit_enhancers.wild_concept
        )

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
                # StoryFacets are advisory surface suggestions.  They must not
                # overwrite the user's selected sub-genre or prompt-pack route.
                # Add facet-driven description enhancement
                ctx["facet_description"] = (
                    (f"{ctx.get('genre_intent_lock')}\n" if ctx.get("genre_intent_lock") else "")
                    + "StoryFacets are advisory surface suggestions only; they cannot override "
                    "the selected genre/sub-genre or ontology.\n"
                    + f"Setting: {facets.setting}\n"
                    f"Tone: {facets.tone}\n"
                    f"Narrative Drive: {facets.narrative_drive}\n"
                    f"Relationship: {facets.relationship_mode}\n"
                    f"Tropes: {', '.join(facets.trope_tags)}"
                )
        except Exception:
            logger.debug("Failed to enrich genre context with story_facets", exc_info=True)

    return ctx


def _concept_methodology_prompt_block(ctx: dict[str, Any]) -> str:
    """Soft 脑洞/爽点 methodology block (Agent ①), empty when disabled/unset."""

    block = ctx.get("concept_methodology_block")
    return f"\n\n{block}" if isinstance(block, str) and block.strip() else ""


#: 调性键 → 人话。与 `STORY_EFFECT_SKILL_LABELS` 同理：模型读不懂 "light"。
_TONE_PROSE_ZH: dict[str, str] = {
    "light": "轻松",
    "dark": "沉重压抑",
    "epic": "史诗厚重",
    "hot": "高热快节奏",
}
_TONE_PROSE_EN: dict[str, str] = {
    "light": "light-hearted",
    "dark": "dark and heavy",
    "epic": "epic and weighty",
    "hot": "hot, fast-paced",
}


def _render_picks_in_prose(selected: dict[str, Any], *, is_en: bool) -> str:
    """把用户勾选翻成一句人话，附在机器 JSON 之后。

    未知键**降级为原键**而不是丢弃：宁可露出机器键，也不能让用户的勾选
    在 prompt 里凭空消失。
    """

    from bestseller.services.story_effect_skills import STORY_EFFECT_SKILL_LABELS

    parts: list[str] = []
    tone = str(selected.get("tone") or "").strip().lower()
    tone_map = _TONE_PROSE_EN if is_en else _TONE_PROSE_ZH
    if tone:
        parts.append(
            (f"tone = {tone_map.get(tone, tone)}")
            if is_en
            else f"调性＝{tone_map.get(tone, tone)}"
        )
    skills = [str(k).strip() for k in (selected.get("effect_skills") or []) if str(k).strip()]
    if skills:
        labels = [STORY_EFFECT_SKILL_LABELS.get(k, k) for k in skills]
        parts.append(
            ("story effects = " + ", ".join(labels))
            if is_en
            else "故事效果＝" + "、".join(labels)
        )
    if not parts:
        return ""
    if is_en:
        return (
            "\nIn plain words, the reader picked: "
            + "; ".join(parts)
            + ". These are the promises the concept must deliver."
        )
    return (
        "\n用人话说，用户要的是："
        + "；".join(parts)
        + "。这些是本书必须当场兑现的承诺，不是可选装饰。"
    )


def _creation_intent_prompt_block(ctx: dict[str, Any]) -> str:
    """Render only explicit creation-page choices as a scoped prompt block.

    The old path mixed taxonomy, model-suggested facets, methodology and the
    global concept-tournament winner into ``description``. That made optional
    UI enhancers behave like a new genre. Keep the user-owned choices separate:
    they may shape the mechanism/tone, but never replace the selected genre,
    prompt pack, or ontology.
    """

    contract = ctx.get("genre_intent_contract")
    if not isinstance(contract, dict):
        return ""
    # Only what the user actually ticked may be presented as an explicit choice.
    # The merged ``tags`` also carries the sub-genre's default_tags (picking
    # 东方玄幻 silently adds 废柴逆袭/升级流/血脉觉醒), and labelling those under
    # 【建书页明确选择】 told the model the user demanded tropes they never picked
    # — a cross-book homogeniser wearing the user's name.
    user_tags = [str(i).strip() for i in (contract.get("user_tags") or []) if str(i).strip()]
    default_tags = [str(i).strip() for i in (contract.get("default_tags") or []) if str(i).strip()]
    if not user_tags and not default_tags:
        # Contracts built before the split carry only the merged list.
        user_tags = [str(i).strip() for i in (contract.get("tags") or []) if str(i).strip()]
    tags = user_tags
    enhancers = contract.get("explicit_enhancers")
    enhancers = enhancers if isinstance(enhancers, dict) else {}
    selected_effects = [
        str(item).strip()
        for item in (enhancers.get("effect_skills") or [])
        if str(item).strip()
    ]
    selected = {
        "channel": contract.get("channel_key"),
        "genre": contract.get("genre_label"),
        "sub_genre": contract.get("sub_genre_label"),
        "tags": tags,
        # Labelled honestly: these came with the sub-genre, the user did not tick them.
        "genre_default_tags": default_tags,
        "audience": contract.get("audience_orientation"),
        "scale": contract.get("narrative_scale"),
        "tone": contract.get("tone_preference"),
        "brainhole": bool(enhancers.get("brainhole")),
        "wild_concept": bool(enhancers.get("wild_concept")),
        "concept_lab": bool(enhancers.get("concept_lab")),
        "creativity_direction": enhancers.get("creativity_direction"),
        "effect_skills": selected_effects,
        "cost_style": enhancers.get("cost_style") or "standard",
    }
    # Do not add a block for an empty/default creation form. This is the
    # no-selection contract: no hidden brainhole, Skill or style injection.
    #
    # ``narrative_scale`` must NOT count as a user choice while it equals the UI's
    # own default: the form initialises it to "serial" and submits it on every
    # create, so counting it made this guard always-true and the block ALWAYS
    # render — leaking the sub-genre's default_tags into every book under the
    # 【建书页明确选择】 header. Only a non-default scale is a real choice.
    _scale_chosen = str(selected["scale"] or "").strip().lower() not in ("", "serial")
    if not any(
        (
            tags,
            selected["audience"],
            _scale_chosen,
            selected["tone"],
            selected["brainhole"],
            selected["wild_concept"],
            selected["concept_lab"],
            selected["creativity_direction"],
            selected_effects,
            selected["cost_style"] != "standard",
        )
    ):
        return ""
    language = str(ctx.get("language") or "zh-CN")
    is_en = language.lower().startswith("en")
    # cost_style must arrive as a TRANSLATED directive, not a bare enum token:
    # the model cannot act on ``"cost_style": "minimal"`` alone, and the layer
    # that used to translate it (ideology_kernel) only runs at planning — too
    # late for a book that dies at the conception gates. 2026-07-24: a 纯爽
    # (minimal) book had a 随机系统收税 invented at finalize and was then
    # hard-killed by the logline gate for exactly that cost.
    from bestseller.services.ideology_kernel import (
        cost_style_directive,
    )

    _cost_directive = cost_style_directive(
        str(selected["cost_style"] or "standard"), is_en=is_en
    )
    # 勾选必须**用人话讲给模型**，不能只丢一串英文机器键。
    # 2026-08-23 真机：用户勾了轻松+喜剧+爽点满足，模型看到的却是
    # {"tone": "light", "effect_skills": ["comedy_engine", ...]}——一份中文
    # 创作 prompt 里嵌英文 snake_case。产出是「市井悬疑/庙堂暗战」的沉重官场
    # 稿，模拟读者 0/3 会点。翻译表 STORY_EFFECT_SKILL_LABELS 早就建好了
    # （它的注释正写着「需要把用户勾了什么讲给模型时无从下手」），只是没人用。
    # 同款教训已定案两次：中文 prompt 嵌英文判据，判据本身会被绕过。
    _picks_line = _render_picks_in_prose(selected, is_en=is_en)
    if is_en:
        return (
            "\n\n[EXPLICIT CREATION INTENT — scoped, not a genre override]\n"
            + json.dumps(selected, ensure_ascii=False)
            + _picks_line
            + "\nUse only these user-selected constraints. Do not invent extra skills,"
            " modern settings, professions, or cross-genre mechanisms."
            + _cost_directive
        )
    return (
        "\n\n【建书页明确选择——仅作局部约束，不得改写题材】\n"
        + json.dumps(selected, ensure_ascii=False)
        + _picks_line
        + "\n只能兑现用户实际勾选的脑洞、调性和 Skill；未勾选的能力不得自行启用，"
        "不得把可选增强器变成新的题材、职业、现代设定或跨题材机制。"
        + _cost_directive
    )


def _build_automatic_story_seed(story_facets: object | None) -> str:
    """Render Story Architect output as system-owned conception material.

    The user may intentionally leave the free-text idea blank and ask the
    product to create from selected options.  Story Architect already spends
    an LLM call producing a concrete setting for exactly that path; previously
    the result lived only in ``ctx['story_facets']`` / a progress event while
    the concept tournament started from an empty seed.  Keep this source
    clearly labelled as system-generated so it can guide ideation without
    masquerading as an explicit user fact.
    """

    if story_facets is None:
        return ""

    def _value(name: str) -> object:
        if isinstance(story_facets, Mapping):
            return story_facets.get(name)
        return getattr(story_facets, name, None)

    setting = str(_value("setting") or "").strip()
    if not setting:
        return ""
    fields: list[tuple[str, str]] = [("具体故事起点", setting)]
    for label, field_name in (
        ("题材原生优势", "power_system"),
        ("叙事驱动", "narrative_drive"),
        ("关系模式", "relationship_mode"),
        ("调性", "tone"),
    ):
        value = str(_value(field_name) or "").strip()
        if value:
            fields.append((label, value))
    raw_tags = _value("trope_tags")
    if isinstance(raw_tags, (list, tuple, set)):
        tags = [str(item).strip() for item in raw_tags if str(item).strip()][:6]
        if tags:
            fields.append(("可用表层标签", "、".join(tags)))
    rendered = "\n".join(f"- {label}：{value}" for label, value in fields)
    return (
        "\n\n【系统自动故事种子——用户未填写自由创意时的故事起点】\n"
        f"{rendered}\n"
        "围绕这个起点生成同一本书的不同可行版本；先补齐主角眼前目标、反常事件和"
        "第一次不可逆选择。它不是用户明确事实，可优化表达与机制，但不得无视后另起"
        "一个泛化题材故事。"
    )


def _automatic_story_seed_for_tournament_attempt(
    automatic_story_seed: str,
    *,
    attempt: int,
) -> str:
    """Let Story Architect anchor the first draw without locking a reroll.

    Story Architect output is generated guidance, not user-owned story intent.
    If every first-round candidate fails a refinement-resistant axis such as
    novelty, replaying the same automatic seed makes the advertised fresh
    reroll structurally identical to the failed round.  Only explicit user
    material or an eligible near-miss may survive into later attempts.
    """

    value = str(automatic_story_seed or "").strip()
    return value if attempt == 1 else ""


_ONTOLOGY_MARKET_STORY_FIELDS: tuple[str, ...] = (
    "logline",
    "reader_promise",
    "selling_points",
    "trope_keywords",
    "hook_keywords",
    "unique_hook",
)

_ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS: tuple[str, ...] = (
    "concept",
    "mechanism",
    "hook_question",
    "protagonist_identity",
    "protagonist_private_desire",
    "protagonist_flaw",
    "core_abnormality",
    "opening_crisis",
    "opponent_system",
    "decision_proof",
    "emotional_promise",
    "core_promise_invariant",
    "role_ladder",
    "world_ladder",
    "repeatable_story_unit",
    "unit_families",
    "progress_bar",
    "question_ladder",
    "ch50",
    "renewal_sources",
    "accumulation_tracks",
    "phase_transitions",
    "opposing_ecology",
    "endgame_direction",
)


def _ontology_text_values(value: Any) -> list[str]:
    """Flatten only textual values from a story-bearing structured field."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        values: list[str] = []
        for item in value.values():
            values.extend(_ontology_text_values(item))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_ontology_text_values(item))
        return values
    return []


def _conception_ontology_story_surface(
    *,
    title: str,
    premise: str,
    synopsis: str,
    tags: list[str] | tuple[str, ...],
    writing_profile: Mapping[str, Any] | None,
    high_concept: Mapping[str, Any] | None,
    story_spine: Mapping[str, Any] | None,
) -> str:
    """Build the final ontology scan from story facts, not control metadata.

    ``writing_profile`` also stores taboos/custom rules, while a tournament
    winner stores judge reasons and rejection diagnostics.  Those fields often
    mention forbidden modern terms precisely to reject them.  Serialising the
    whole objects made a sentence such as ``不得写成职场故事`` indistinguishable
    from an actual workplace premise.  Keep the tripwire strict on title,
    premise, synopsis, character/world facts, reader-facing promises, the
    champion's story contract, and the story spine; omit control-plane prose.
    """

    pieces = [str(title or ""), str(premise or ""), str(synopsis or "")]
    pieces.extend(_ontology_text_values(tags))

    profile = writing_profile if isinstance(writing_profile, Mapping) else {}
    pieces.extend(_ontology_text_values(profile.get("character")))
    pieces.extend(_ontology_text_values(profile.get("world")))
    market = profile.get("market")
    if isinstance(market, Mapping):
        for field in _ONTOLOGY_MARKET_STORY_FIELDS:
            pieces.extend(_ontology_text_values(market.get(field)))

    champion = high_concept if isinstance(high_concept, Mapping) else {}
    for field in _ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS:
        pieces.extend(_ontology_text_values(champion.get(field)))
    pieces.extend(_ontology_text_values(story_spine))
    return "\n".join(piece for piece in pieces if piece.strip())


def _contains_ontology_term(value: Any, violations: tuple[str, ...]) -> bool:
    text = "\n".join(_ontology_text_values(value)).lower()
    return any(term.lower() in text for term in violations)


def _merge_ontology_repair(
    original: Any,
    repaired: Any,
    violations: tuple[str, ...],
) -> Any:
    """Adopt repair output only where the original actually contained drift."""

    if not _contains_ontology_term(original, violations):
        return original
    if isinstance(original, Mapping) and isinstance(repaired, Mapping):
        merged = dict(original)
        for key, old_value in original.items():
            if key in repaired:
                merged[key] = _merge_ontology_repair(
                    old_value,
                    repaired[key],
                    violations,
                )
        return merged
    if isinstance(original, list) and isinstance(repaired, list):
        return repaired
    if isinstance(original, tuple) and isinstance(repaired, (list, tuple)):
        return tuple(repaired)
    if isinstance(original, str) and isinstance(repaired, str) and repaired.strip():
        return repaired.strip()
    return original


async def _repair_final_ontology_drift(
    session: AsyncSession,
    settings: AppSettings,
    *,
    violations: tuple[str, ...],
    title: str,
    premise: str,
    synopsis: str,
    tags: list[str],
    writing_profile: dict[str, Any],
    story_spine: dict[str, Any],
    high_concept: dict[str, Any] | None,
    genre_label: str,
    sub_genre_label: str,
    language: str,
) -> tuple[
    str,
    str,
    str,
    list[str],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[Any],
]:
    """One bounded repair for ontology drift introduced by late copywriting.

    The early concept scan cannot see later title/blurb/logline/spine rewrites.
    Instead of discarding an otherwise approved conception at the last step,
    give those story fields one terminology-only repair.  The merge below only
    adopts fields that contained a detected term; all other story facts and all
    control metadata remain byte-for-byte unchanged.  The caller re-runs the
    deterministic detector and still fails closed if any drift survives.

    ``high_concept`` is repaired too (2026-08-06). The verdict surface reads 24
    champion fields that the repair could not reach, so a modern term landing in
    any of them was structurally unfixable: the repair would run, change
    nothing, and the re-scan would kill an otherwise approved book. A region
    that is judged must also be repairable — otherwise the gate is a coin toss
    on where the model happened to put the word.
    """

    profile_story: dict[str, Any] = {
        "character": dict(writing_profile.get("character") or {}),
        "world": dict(writing_profile.get("world") or {}),
        "market": {
            field: writing_profile.get("market", {}).get(field)
            for field in _ONTOLOGY_MARKET_STORY_FIELDS
            if isinstance(writing_profile.get("market"), Mapping)
            and writing_profile.get("market", {}).get(field) is not None
        },
    }
    champion = high_concept if isinstance(high_concept, Mapping) else {}
    champion_story: dict[str, Any] = {
        field: champion.get(field)
        for field in _ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS
        if champion.get(field) is not None
    }
    repair_input = {
        "title": title,
        "premise": premise,
        "synopsis": synopsis,
        "tags": tags,
        "writing_profile_story": profile_story,
        "story_spine": story_spine,
        "high_concept_story": champion_story,
    }
    is_en = str(language or "").lower().startswith("en")
    if is_en:
        system_prompt = (
            "You are a fiction ontology copy editor. Replace only the listed "
            "setting-incompatible terms with equivalents native to the selected "
            "genre. Preserve every character, causal fact, promise, and structure. "
            "Return valid JSON only."
        )
        user_prompt = (
            f"Genre: {genre_label} / {sub_genre_label or 'unspecified'}\n"
            f"Incompatible terms: {', '.join(violations)}\n"
            "Repair only fields containing those terms. Do not modernize, add a "
            "profession, or invent a new mechanism. Keep the same JSON shape:\n"
            f"{json.dumps(repair_input, ensure_ascii=False)}"
        )
    else:
        system_prompt = (
            "你是小说题材本体校对编辑。只把列出的越界现代设定词改成本题材世界内"
            "成立的等价称谓；人物、因果、卖点、冲突与结构一律不变。只输出合法 JSON。"
        )
        user_prompt = (
            f"题材：{genre_label} / {sub_genre_label or '未指定子题材'}\n"
            f"越界词：{'、'.join(violations)}\n"
            "只改含越界词的字段，不得现代化，不得新增职业或机制，不得改换故事。"
            "保持下列 JSON 结构原样返回：\n"
            f"{json.dumps(repair_input, ensure_ascii=False)}"
        )
    repaired, llm_ids = await _llm_call_json(
        session,
        settings,
        role="editor",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback=json.dumps(repair_input, ensure_ascii=False),
        template="conception_final_ontology_repair",
        stage="conception.final_ontology_repair",
        language=language,
    )
    if not isinstance(repaired, Mapping):
        repaired = repair_input

    repaired_profile = repaired.get("writing_profile_story")
    repaired_profile = repaired_profile if isinstance(repaired_profile, Mapping) else {}
    new_profile = dict(writing_profile)
    for section in ("character", "world"):
        new_profile[section] = _merge_ontology_repair(
            writing_profile.get(section, {}),
            repaired_profile.get(section, {}),
            violations,
        )
    original_market = writing_profile.get("market")
    if isinstance(original_market, Mapping):
        new_profile["market"] = _merge_ontology_repair(
            original_market,
            repaired_profile.get("market", {}),
            violations,
        )

    repaired_tags = repaired.get("tags")
    new_tags = _merge_ontology_repair(
        tags,
        repaired_tags if isinstance(repaired_tags, list) else tags,
        violations,
    )
    repaired_spine = repaired.get("story_spine")
    new_spine = _merge_ontology_repair(
        story_spine,
        repaired_spine if isinstance(repaired_spine, Mapping) else story_spine,
        violations,
    )
    repaired_champion = repaired.get("high_concept_story")
    new_champion = dict(champion)
    if isinstance(repaired_champion, Mapping):
        for field in _ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS:
            if field not in champion:
                continue
            new_champion[field] = _merge_ontology_repair(
                champion.get(field),
                repaired_champion.get(field),
                violations,
            )
    return (
        str(_merge_ontology_repair(title, repaired.get("title"), violations)),
        str(_merge_ontology_repair(premise, repaired.get("premise"), violations)),
        str(_merge_ontology_repair(synopsis, repaired.get("synopsis"), violations)),
        [str(item).strip() for item in new_tags if str(item).strip()][:10],
        new_profile,
        dict(new_spine) if isinstance(new_spine, Mapping) else story_spine,
        new_champion,
        list(llm_ids),
    )


def _commercial_brief_prompt_block(ctx: dict[str, Any]) -> str:
    brief = ctx.get("commercial_brief")
    qimao_block = _qimao_regeneration_prompt_block(ctx)
    concept_block = render_concept_lab_prompt_block(ctx, language=str(ctx.get("language") or "zh-CN"))
    concept_block = f"\n\n{concept_block}" if concept_block else ""
    hook_spec = coerce_hook_spec(ctx.get("hook_spec"))
    hook_block = ""
    if hook_spec is not None:
        hook_block = "\n\n" + render_hook_spec_prompt_block(
            hook_spec,
            language=str(ctx.get("language") or "zh-CN"),
        )
    intent_block = _creation_intent_prompt_block(ctx)
    if not isinstance(brief, dict) or not brief:
        return f"{concept_block}{hook_block}{intent_block}{qimao_block}"
    label = "[Auto commercial positioning brief]" if str(ctx.get("language", "")).startswith("en") else "【自动商业化立项 brief】"
    return (
        f"\n\n{label}\n{json.dumps(brief, ensure_ascii=False, indent=2)}"
        f"{concept_block}{hook_block}{intent_block}\n{qimao_block}"
    )


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
        # Two defects lived on this line. ``market.reader_promise`` arrives from
        # the genre preset's writing_profile_overrides, where it is an order to
        # the writer ("开篇快速亮出…持续维持强追读") — so it is filtered, not taken
        # on trust. And the hand-written fallback itself said 「核心爽点…稳定追读
        # 回报」: when the model failed, the framework wrote the worst copy in the
        # book by hand.
        "reader_promise": pick_reader_facing(
            market.get("reader_promise"),
            (
                f"看主角在{ctx.get('genre')}的世界里，一次次把眼看要输的局面翻过来。"
                if not is_en
                else f"Watch the lead turn a losing position around, again and "
                f"again, in a {ctx.get('genre')} world."
            ),
        ),
        # fold_near_duplicate_points：同一卖点的多个措辞版本在此折叠（真机两书
        # 复现：7 条实为 4 条 / 500 字长句），这里是所有来源入库前的单一咽喉。
        "selling_points": fold_near_duplicate_points(
            _normalize_string_list(market.get("selling_points"))
        ) or trend_keywords[:3],
        "trope_keywords": _normalize_string_list(market.get("trope_keywords")) or trend_keywords[:3],
        "hook_keywords": _normalize_string_list(market.get("hook_keywords")) or trend_keywords[:2],
        "content_mode": market.get("content_mode") or (
            "中文网文长篇连载" if not is_en else "Commercial English web serial"
        ),
        "opening_contract": market.get("opening_contract") or (
            "第一章必须以异常、危机、误会、损失、利益冲突或被迫选择切入。"
            if _is_qimao_text(market.get("platform_target") or ctx.get("default_platform"))
            else ""
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
    # The LLM occasionally emits ``market`` / ``style`` as a bare string instead
    # of the required object; ``dict("some string")`` then raises
    # "ValueError: dictionary update sequence element #0 has length 1".
    # Normalize to empty dict so the whole conception run does not crash on a
    # single malformed field (2026-08-02 observed on xianxia-upgrade creation).
    raw_market = merged.get("market") or {}
    raw_style = merged.get("style") or {}
    market = dict(raw_market) if isinstance(raw_market, dict) else {}
    style = dict(raw_style) if isinstance(raw_style, dict) else {}

    for key in (
        "platform_target",
        "reader_promise",
        "content_mode",
        "opening_contract",
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


def _cost_style_block_for_ctx(ctx: dict[str, Any], *, is_en: bool) -> str:
    """Translated cost-style directive for any stage that designs mechanics.

    This is a *user choice*, not a framework guardrail, so unlike
    ``_default_motif_guardrail`` it belongs in the prompt — and because it names
    no motif vocabulary it cannot seed the thing it constrains.

    Added for the commercial-positioning stage (2026-08-06): that stage owns
    ``book_spec_instruction``, which orders a 力量体系 + 升级引擎 while knowing
    nothing about cost_style. A 爽文无代价 book came back with 「人情债滚动；
    每10章解一层境界，但必须先还清上一阶段欠下的债」 — cost promoted to the
    progression engine itself, the exact thing the switch excludes.
    """

    contract = ctx.get("genre_intent_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    enhancers = contract.get("explicit_enhancers")
    enhancers = enhancers if isinstance(enhancers, Mapping) else {}
    style = str(enhancers.get("cost_style") or "standard")
    if style == "standard":
        return ""
    from bestseller.services.ideology_kernel import (
        cost_style_directive,
    )

    directive = cost_style_directive(style, is_en=is_en)
    if not directive:
        return ""
    # The directive shapes the story; it is not copy. Without this line the
    # model paraphrased it straight into selling_points — a real book shipped
    # 「极简代价、不掉链子」「绝不反向惩罚主角」 as its cover blurb, which is the
    # framework's internal instruction printed in the shop window.
    scope = (
        "\nThis instruction shapes the design only. Never quote or paraphrase it "
        "in reader-facing copy (logline, blurb, selling points, tags).\n"
        if is_en
        else "\n以上只约束设定与机制的设计，不是文案素材。"
        "禁止把它的措辞复述或改写进任何对外文字（一句话、简介、卖点、标签）。\n"
    )
    return f"\n{directive}{scope}"


#: Fields of the commercial brief a reader will actually be shown. The rest of
#: the brief (opening_strategy, chapter_hook_strategy, payoff_rhythm …) is meant
#: to instruct the generator, so directive voice is correct there and must not
#: be scrubbed.
_READER_FACING_BRIEF_FIELDS: Final[tuple[str, ...]] = ("reader_promise", "selling_points")


def prefer_cleaner_reader_copy(candidates: Sequence[Any]) -> str:
    """在若干候选文案里挑**生产口吻最少**的那一份。不发明，只做选择。

    2026-08-24 真机（custom-xuanhuan-1787543232）：去行业词通道把
    `commercial_brief.reader_promise` 洗成了「一个连正经丹炉都没摸过的小学徒，
    靠捡别人扔掉的药渣，一题一题扛住老狐狸的刁难……」，而**出货的**
    `market_profile["reader_promise"]` 是「前几章给出……的爽点」，打分 14.0。

    顺序是致命的：6433 行先洗 commercial_brief，7225 行再用未清洗的
    concept_bundle 覆盖 market_profile —— **清洗在前，覆盖在后**。
    `爽点` 一直在 trade_jargon 词表里，检测器没瞎，是它量的那份不是发出去的那份。
    本仓库反复复发的「同一事实住两地，后写的赢」。

    比较式而非清单式：分数最低者胜，**分数相同保持原顺序**（都干净时不改变
    既有优先级）。
    """

    from bestseller.services.copy_flavor import detect_copy_flavor

    best: str = ""
    best_score: float | None = None
    for item in candidates:
        text = str(item or "").strip()
        if not text:
            continue
        score = float(detect_copy_flavor(text).score)
        if best_score is None or score < best_score:
            best, best_score = text, score
    return best


def _brief_copy_flavour(brief: Mapping[str, Any]) -> tuple[float, list[str]]:
    """Score the reader-facing part of a commercial brief. Higher is worse."""

    from bestseller.services.copy_flavor import detect_copy_flavor

    total = 0.0
    evidence: list[str] = []
    for field in _READER_FACING_BRIEF_FIELDS:
        value = brief.get(field)
        items = value if isinstance(value, list) else [value]
        for item in items:
            report = detect_copy_flavor(item if isinstance(item, str) else None)
            total += report.score
            evidence.extend(f"{span.matched}（{span.why}）" for span in report.spans)
    return round(total, 1), evidence


async def _rewrite_flavoured_copy(
    session: AsyncSession,
    settings: AppSettings,
    *,
    brief: dict[str, Any],
    is_en: bool,
    language: str,
) -> tuple[dict[str, Any], list[UUID]]:
    """Rewrite reader-facing copy that addresses an editor instead of a reader.

    The schema now states the register, which fixed most of it — but a schema is
    advice, and the model still reaches for 追读/爽点/每章 when the surrounding
    context is full of them. So the copy gets read back and, if it still smells,
    re-asked once with its own offending phrases quoted.

    The rewrite is adopted **only when it actually scores lower**. A rewrite that
    is no better is discarded and the original kept: a repair loop that ships its
    last attempt regardless of score is how a book ends up published from its
    worst draft.
    """

    before, evidence = _brief_copy_flavour(brief)
    if before <= 0.0:
        return brief, []

    quoted = "\n".join(f"- {item}" for item in dict.fromkeys(evidence))
    current = json.dumps(
        {field: brief.get(field) for field in _READER_FACING_BRIEF_FIELDS},
        ensure_ascii=False,
    )
    if is_en:
        system = (
            "You rewrite marketing copy. You speak to a reader who has never "
            "heard of this book, never to an editor or a producer."
        )
        user = (
            "Rewrite the fields below. They currently read like a production "
            "brief.\n\nWhat is wrong with them:\n"
            f"{quoted}\n\nCurrent value:\n{current}\n\n"
            "Rules: name a person and what happens to them. No industry terms, "
            "no chapter arithmetic, no talk of what the book 'must' do.\n"
            'Return JSON only: {"reader_promise": "...", "selling_points": ["...", "..."]}'
        )
    else:
        system = (
            "你是写书封文案的人。你的听众是一个从没听说过这本书的读者，"
            "不是编辑，不是平台，也不是写手。"
        )
        user = (
            "下面这几个字段现在读起来像一份生产任务书，请重写。\n\n"
            f"具体问题：\n{quoted}\n\n当前内容：\n{current}\n\n"
            "要求：说人、说这个人身上发生了什么、说读者会看到什么画面。"
            "不用行业词，不写第几章会发生什么，不说这本书「必须」怎样。"
            "像跟朋友安利一本书那样说话。\n"
            '只输出 JSON：{"reader_promise": "...", "selling_points": ["...", "..."]}'
        )

    rewritten, run_ids = await _llm_call_json(
        session,
        settings,
        role="planner",
        system_prompt=system,
        user_prompt=user,
        fallback=current,
        template="conception_copy_deflavour",
        stage="conception.copy_deflavour",
        language=language,
    )
    if not isinstance(rewritten, dict):
        return brief, run_ids

    candidate = dict(brief)
    for field in _READER_FACING_BRIEF_FIELDS:
        value = rewritten.get(field)
        if isinstance(value, list):
            cleaned = _normalize_string_list(value)
            if cleaned:
                candidate[field] = cleaned
        elif isinstance(value, str) and value.strip():
            candidate[field] = value.strip()

    after, _ = _brief_copy_flavour(candidate)
    if after >= before:
        logger.info(
            "Copy de-flavour discarded: %.1f -> %.1f (no improvement)", before, after
        )
        return brief, run_ids

    logger.info("Copy de-flavour applied: %.1f -> %.1f", before, after)
    return candidate, run_ids


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
        '  "reader_promise": "一句话说清读者能持续得到什么体验。写给读者听，'
        '不用行业词（追读/爽点/留存/黄金三章），不写章节节奏（每N章…）",\n'
        '  "selling_points": ["用故事本身的人、事、反常之处说，'
        '像跟朋友安利一本书；三条各说不同的点，不要改写同一条", "同上", "同上"],\n'
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
    prompt += _cost_style_block_for_ctx(ctx, is_en=False)
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _qimao_regeneration_prompt_block(ctx)
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
    prompt += _cost_style_block_for_ctx(ctx, is_en=True)
    prompt += _default_motif_guardrail(ctx, is_en=True)
    prompt += _qimao_regeneration_prompt_block(ctx)
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
    "你设计的世界必须服务于这本书的核心张力——可以是冲突与爽感，也可以是氛围、"
    "主题表达、人物处境或真实质感，按题材取最贴合的那一种，而非空洞的百科全书。"
    "输出必须是合法 JSON，不要解释。"
)


def _market_user_prompt(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    from bestseller.services.genre_persona import render_channel_style_stamp

    _channel_stamp = render_channel_style_stamp(
        (ctx.get("user_hints") or {}).get("audience_orientation")
    )
    prompt = (
        f"{_channel_stamp}"
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"推荐平台：{', '.join(ctx['recommended_platforms'])}\n"
        f"推荐受众：{', '.join(ctx['recommended_audiences'])}\n"
        f"趋势关键词：{', '.join(ctx['trend_keywords'])}\n"
        f"趋势评分：{ctx['trend_score']}/100\n"
        f"\n请生成 market 定位 JSON，包含：\n"
        f'{{"platform_target": "最适合的平台",\n'
        f'  "reader_promise": "一句话说清读者能持续得到什么体验。写给读者听，'
        f'不用行业词（追读/爽点/留存/黄金三章），不写章节节奏（每N章…）",\n'
        f'  "selling_points": ["用故事本身的人、事、反常之处说，'
        f'像跟朋友安利一本书；四条各说不同的点，不要改写同一条", "同上", "同上", "同上"],\n'
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
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _concept_methodology_prompt_block(ctx)
    prompt += _mechanism_dedup_prompt_block(ctx, is_en=False)
    prompt += _anti_debt_metaphor_guardrail(ctx, is_en=False)
    # cost_style is a user CHOICE, not a framework guardrail, so it belongs
    # in the stage that designs mechanics. The retired motif police left
    # nothing on the output side (2026-08-02, all detectors are False-shims),
    # so a 爽文无代价 book kept acquiring a debt/backlash engine here.
    prompt += _cost_style_block_for_ctx(ctx, is_en=False)
    return prompt


# 烂名黑名单单一来源在 naming_normalizer（2026-08-18《九姓井口只认我》定罪：
# 禁令只挂 cast prompt，而主角名在概念淘汰赛展开层就铸死了——陆沉一路进了
# 书名/前提/简介）。此处保留旧名做别名，勿再内联词表。
# Cross-book de-dup against actually-used names is layered on top via
# ``_recent_cast_names`` → ``ctx['avoid_names']``.
from bestseller.services.naming_normalizer import (  # noqa: E402
    CLICHE_NAME_BLOCKLIST as _CLICHE_NAME_BLOCKLIST,
)


def _naming_constraint_block(ctx: dict[str, Any], *, is_en: bool) -> str:
    """Append a do-not-reuse name list to the cast prompt.

    Two layers: a static blocklist of overused / legacy-baked names, and a
    dynamic list of names already used by other projects in this system
    (``ctx['avoid_names']``) — the cross-book de-dup that was missing at the
    conception layer and let the same names recur book after book.
    """

    avoid = [str(n).strip() for n in (ctx.get("avoid_names") or []) if str(n).strip()]
    if is_en:
        if not avoid:
            return ""
        shown = ", ".join(avoid[:40])
        return (
            "\n\n[Naming de-duplication — hard constraint]\n"
            f"These names are already used by other books in this system; do NOT "
            f"reuse them or near-identical variants: {shown}.\n"
            "Pick fresh names that fit this book's specific premise and protagonist."
        )
    lines = [
        "\n\n【命名去重 · 硬约束】",
        "以下名字在网文里被严重滥用、或被旧模板固化，主角与主要配角一律禁止使用，"
        "也不要用仅差一字的高度雷同变体：",
        "、".join(_CLICHE_NAME_BLOCKLIST) + "。",
    ]
    if avoid:
        lines.append(
            "以下名字已被本系统其他作品使用，为保证每本书差异化，禁止再用（含同姓近似变体）："
            + "、".join(avoid[:40])
            + "。"
        )
    lines.append("请主动避开以上所有名字，为本书取一个新鲜、贴合具体设定与主角气质的名字。")
    return "\n".join(lines)


async def _recent_cast_names(session: AsyncSession, *, limit: int = 60) -> list[str]:
    """Names already used by existing projects, newest first, for cross-book
    de-dup at the conception layer.

    Best-effort: any failure returns an empty list so conception never blocks
    on it. Parenthetical qualifiers (e.g. ``"Rowan Ashford (18th daughter)"``)
    are stripped so a single character is not counted as many.
    """

    from sqlalchemy import select

    from bestseller.infra.db.models import CharacterModel

    try:
        rows = list(
            await session.scalars(
                select(CharacterModel.name)
                .order_by(CharacterModel.created_at.desc())
                .limit(limit * 5)
            )
        )
    except Exception:
        logger.debug("recent cast-name fetch failed", exc_info=True)
        return []

    seen: list[str] = []
    for raw in rows:
        name = (raw or "").strip()
        for sep in ("（", "(", "·", "/"):
            name = name.split(sep)[0].strip()
        # Sanity guard: real names are short; longer strings are role/desc junk.
        # Cap allows multi-word English full names ("Rowan Ashford") through.
        if not name or len(name) > 24:
            continue
        if name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            break
    return seen


# Cross-book *mechanism* de-dup caps: how many recent same-genre books are
# surfaced in the avoid-list and how much of each field survives (the prompt
# needs the mechanism's identity, not the whole premise).
_MECHANISM_DEDUP_MAX_BOOKS = 6
_MECHANISM_DEDUP_FIELD_CHARS = 80
_MECHANISM_DEDUP_MAX_TROPES = 6

# ── 平台俗套底牌库(Phase 5, 2026-07-08 设定/逻辑框架层) ──────────────────
# 跨书回声闸门(_recent_core_mechanisms)只拦"和自己旧书重复"——数据库一清空
# (冷启动)avoid_mechanisms 就是空的，模型可以自由撞上全平台已经写烂的老梗
# （规则怪谈的"你就是规则书写者/你就是鬼"、无限流的"世界是场实验"）。这份
# 静态底牌库按 novel_categories 的题材族给几条已知高频俗套反转，冷启动时
# 补进 avoid_mechanisms，与真实旧书条目同格式（title/golden_finger/premise/
# trope_keywords），既进 echo 检测语料，也进 prompt 可见的差异化清单。
# 不是详尽俗套词典，只覆盖已确认真机命中的高频俗套，按后续真机反馈追加。
_GENRE_CLICHE_BASELINE: dict[str, list[dict[str, Any]]] = {
    "suspense-mystery": [
        {
            "title": "（平台俗套·规则怪谈反转）",
            "source": "platform_cliche",
            "intent_aliases": ["规则书写者", "怪谈本体", "你就是鬼"],
            "golden_finger": "主角发现自己就是规则的原始书写者/编写者",
            "premise": "记录或研究诡异规则的人，到头来发现规则其实是他自己写的，"
            "或者他本人就是大家都在躲避的那个'鬼'/怪谈本体",
            "trope_keywords": ["你就是鬼", "你就是书写者"],
        },
        {
            "title": "（平台俗套·世界是实验）",
            "source": "platform_cliche",
            "intent_aliases": ["世界是实验", "实验场", "游戏世界"],
            "golden_finger": "主角发现所在世界是上位者/组织设计的实验或游戏",
            "premise": "所有诡异现象、规则副本，最终揭示是某个组织或文明用来"
            "测试、豢养或收割参与者的实验场/游戏关卡",
            "trope_keywords": ["世界是实验", "游戏管理员", "楚门的世界"],
        },
    ],
    # 玄幻/仙侠/修仙/末日/异能/升级 全部解析到 action-progression。冷启动(清库/
    # 首本)这里若为空，模型会均值回归到平台最烂大街的玄幻套路——尤其"死者归来讨
    # 旧账/借尸还魂"这条(证据书「龙椅上坐着我亡夫」「替亡人落最后一笔」连撞两本)。
    "action-progression": [
        {
            "title": "（平台俗套·死者归来讨旧账）",
            "source": "platform_cliche",
            "intent_aliases": ["死者归来", "借尸还魂", "死而复生", "亡者复生"],
            "golden_finger": "亲人（亡夫/亡妻/亡母）借尸还魂或死而复生归来，主角被迫认账/对质",
            "premise": "主角亲手埋葬/钉棺的亲人十七年后借尸还魂、诈尸或登基归来，"
            "回来讨一笔旧债、索一条命或掀翻主角赖以自欺的十七年谎言",
            "trope_keywords": ["借尸还魂", "亡夫归来", "亡妻讨账", "死者归来", "诈尸复活", "开棺认尸"],
        },
        {
            "title": "（平台俗套·灭门遗孤复仇）",
            "source": "platform_cliche",
            "intent_aliases": ["灭门", "遗孤复仇", "血仇", "复仇"],
            "golden_finger": "灭门夜唯一活口，血脉/剑骨觉醒后复仇",
            "premise": "全家/全宗被屠，主角是唯一幸存的遗孤/遗脉，靠觉醒的血脉或"
            "上古传承一路复仇雪恨、血债血偿",
            "trope_keywords": ["灭门遗孤", "灭门血仇", "血脉觉醒复仇", "屠宗灭门"],
        },
        {
            "title": "（平台俗套·废材觉醒逆袭）",
            "source": "platform_cliche",
            "intent_aliases": ["废柴逆袭", "废材逆袭", "血脉觉醒", "退婚打脸"],
            "golden_finger": "被诊断为废脉/废体，其实是隐藏的绝世天赋",
            "premise": "开局废材被退婚/被瞧不起，觉醒后发现废脉是伪装的宝脉/特殊体质，一路打脸逆袭",
            "trope_keywords": ["废材觉醒", "废脉是宝脉", "退婚打脸", "扮猪吃虎"],
        },
        {
            "title": "（平台俗套·钻天道规则漏洞）",
            "source": "platform_cliche",
            "intent_aliases": ["天道漏洞", "规则漏洞", "系统漏洞", "卡bug"],
            "golden_finger": "发现并钻天道/系统/世界规则的漏洞白嫖",
            "premise": "主角找到天道或修炼体系的 bug/漏洞，靠规则套利、卡 bug 无限刷取变强",
            "trope_keywords": ["天道漏洞", "钻规则漏洞", "卡bug变强", "系统套利"],
        },
    ],
}


def _genre_cliche_baseline(genre: str | None, sub_genre: str | None) -> list[dict[str, Any]]:
    """按题材族查静态俗套底牌(冷启动兜底,fail-open)。"""

    try:
        category = resolve_novel_category(genre or "", sub_genre)
    except Exception:
        return []
    if category is None:
        return []
    return list(_GENRE_CLICHE_BASELINE.get(category.key, []))


async def _recent_core_mechanisms(
    session: AsyncSession,
    *,
    genre: str | None,
    sub_genre: str | None = None,
    limit: int = _MECHANISM_DEDUP_MAX_BOOKS,
) -> list[dict[str, Any]]:
    """Core-mechanism summaries of recent same-genre projects, newest first.

    The concept-level twin of ``_recent_cast_names``: names had cross-book
    de-dup, but nothing stopped book N+1 from re-minting book N's golden
    finger — xianxia-upgrade books kept converging on the same debt/ledger
    mechanism. "Same genre" is resolved through ``genre_taxonomy.canonicalize``
    so free-form genre strings ("仙侠升级流" vs "仙侠升级") group together while
    other genres never leak into the avoid-list.

    Best-effort: any failure returns an empty list so conception never blocks.
    """

    from sqlalchemy import select

    from bestseller.infra.db.models import ProjectModel
    from bestseller.services.genre_taxonomy import canonicalize

    try:
        target_key = canonicalize(genre, sub_genre)
    except Exception:
        target_key = None
    target_raw = str(genre or "").strip()

    try:
        rows = (
            await session.execute(
                select(
                    ProjectModel.title,
                    ProjectModel.genre,
                    ProjectModel.sub_genre,
                    ProjectModel.metadata_json,
                    ProjectModel.id,
                )
                .order_by(ProjectModel.created_at.desc())
                .limit(limit * 8)
            )
        ).all()
    except Exception:
        logger.debug("recent core-mechanism fetch failed", exc_info=True)
        return []

    # A book that never got written is not prior art. An abandoned shell — no
    # chapters, still carrying its conception placeholder title — used to count
    # as a "previous book" for cross-book echo, so the next book in the same
    # genre collided with a story that does not exist and was killed for
    # plagiarising it (2026-08-06: collisions=["东方玄幻·构思中"], a 0-chapter
    # husk that was deleted minutes later).
    written_ids: set[Any] = set()
    try:
        from bestseller.infra.db.models import ChapterModel

        written_ids = {
            row[0]
            for row in (
                await session.execute(
                    select(ChapterModel.project_id).distinct()
                )
            ).all()
        }
    except Exception:
        # Fail open: if we cannot tell which books have prose, keep the old
        # behaviour rather than silently disabling cross-book de-dup.
        logger.debug("chapter presence lookup failed for echo baseline", exc_info=True)
        written_ids = None  # type: ignore[assignment]

    entries: list[dict[str, Any]] = []
    for title, row_genre, row_sub_genre, metadata, row_id in rows:
        if written_ids is not None and row_id not in written_ids:
            continue
        try:
            row_key = canonicalize(row_genre, row_sub_genre)
        except Exception:
            row_key = None
        if target_key is not None:
            if row_key != target_key:
                continue
        elif not target_raw or str(row_genre or "").strip() != target_raw:
            # Unresolvable target genre: only exact raw-genre matches qualify —
            # never let a resolvable-but-different genre bleed in.
            continue

        meta = metadata if isinstance(metadata, dict) else {}
        profile_raw = meta.get("writing_profile")
        profile = profile_raw if isinstance(profile_raw, dict) else {}
        character_raw = profile.get("character")
        character = character_raw if isinstance(character_raw, dict) else {}
        market_raw = profile.get("market")
        market = market_raw if isinstance(market_raw, dict) else {}

        golden_finger = str(character.get("golden_finger") or "").strip()[
            :_MECHANISM_DEDUP_FIELD_CHARS
        ]
        premise = str(meta.get("premise") or "").strip()[:_MECHANISM_DEDUP_FIELD_CHARS]
        tropes = _normalize_string_list(market.get("trope_keywords"))[
            :_MECHANISM_DEDUP_MAX_TROPES
        ]
        if not golden_finger and not premise:
            continue
        entries.append(
            {
                "title": str(title or "").strip(),
                "golden_finger": golden_finger,
                "premise": premise,
                "trope_keywords": tropes,
            }
        )
        if len(entries) >= limit:
            break
    return entries


def _mechanism_dedup_prompt_block(ctx: dict[str, Any], *, is_en: bool) -> str:
    """Render cross-book novelty pressure without exposing prior-book text.

    Exact prior mechanisms remain in ``ctx`` for deterministic comparison after
    generation. They must never become a negative-example mood board inside an
    LLM prompt.
    """

    items = [
        item for item in (ctx.get("avoid_mechanisms") or []) if isinstance(item, dict)
    ]
    if not items:
        return ""

    count = min(len(items), _MECHANISM_DEDUP_MAX_BOOKS)
    if is_en:
        return (
            "\n\n[Mechanism de-duplication — cross-book differentiation, hard constraint]\n"
            f"{count} same-genre mechanism fingerprints will be compared by code after "
            "generation; their titles, premises, powers, and imagery are intentionally "
            "withheld from this prompt. Build independently from the user's current intent. "
            "The operating principle, state changes, and recurring conflict must be original; "
            "renaming a familiar structure does not count."
        )
    return (
        "\n\n【机制去重 · 跨书差异化（硬约束）】\n"
        f"已有{count}条同题材机制指纹将在生成后由程序比对；旧书书名、前提、能力与"
        "意象原文均不进入提示词。请只从本次用户设定独立生长作用原理、局面变化和"
        "持续冲突；只换名词、换皮不换骨不算原创。"
    )


async def _attach_mechanism_dedup(
    session: AsyncSession,
    settings: AppSettings,
    ctx: dict[str, Any],
) -> None:
    """Attach ``ctx['avoid_mechanisms']`` for prompt injection. Fail-open.

    Gated by ``pipeline.enable_conception_mechanism_dedup`` (kill-switch);
    any failure leaves the ctx usable so conception never blocks on de-dup.
    """

    pipeline = getattr(settings, "pipeline", None)
    if not bool(getattr(pipeline, "enable_conception_mechanism_dedup", True)):
        return
    try:
        entries = await _recent_core_mechanisms(
            session,
            genre=str(ctx.get("genre") or "") or None,
            sub_genre=str(ctx.get("sub_genre") or "") or None,
        )
    except Exception:
        logger.debug("mechanism de-dup attach failed", exc_info=True)
        entries = []
    # 冷启动兜底(Phase 5):DB 无同题材旧书时(清库/新题材)avoid_mechanisms 为
    # 空，回声闸门与 prompt 差异化清单形同虚设——补进静态平台俗套底牌，
    # 补到 _MECHANISM_DEDUP_MAX_BOOKS 上限，真实旧书条目优先。
    if len(entries) < _MECHANISM_DEDUP_MAX_BOOKS:
        try:
            baseline = _genre_cliche_baseline(ctx.get("genre"), ctx.get("sub_genre"))
        except Exception:
            baseline = []
        entries = entries + baseline[: _MECHANISM_DEDUP_MAX_BOOKS - len(entries)]
    ctx["avoid_mechanisms"] = entries


# ── Mechanism echo screen ───────────────────────────────────────────────────
# The avoid-list alone is not enough: live verification showed the finalize
# LLM absorbing the forbidden vocabulary as material (a premise opening copied
# verbatim from an old book, a golden finger named after an old ledger
# mechanism). This deterministic screen detects surface-level reuse so the
# pipeline can retry finalize once with the specific collisions named.

# A shared CJK run at least this long is treated as a verbatim echo.
_ECHO_SPAN_MIN_CHARS = 5
# Distinct non-background bigrams shared with a single old book to flag it.
_ECHO_BIGRAM_MIN_HITS = 2
# 没有任何连续共享片段（span）时的门槛。
#
# 2026-08-22 真机定罪：《书院笔仙》完本后用同参数建第二本玄幻书，构思被
# 整体毙掉，证据是 span="" + shared_bigrams=["杂役","逆袭"]——两个玄幻
# 题材通用词，没有一段机制描述被复制。重试那次是 ["废柴","打脸","柴逆",
# "逆袭"]，其中「柴逆」跨了词边界根本不是词。
#
# span 为空意味着**没有任何一段机制描述被抄**，此时零散的两字词证据力
# 弱得多，门槛必须更高（两票定罪）。有 span 时维持原门槛不变。
_ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN = 3
# A bigram present in at least this many avoid entries is genre background
# (宗门/升级/修仙…) rather than one book's identity, and never counts.
#
# ⚠️ 这条过滤在**小书库下会退化**：库里只有 2 本书时,没有任何二元组能出现在
# ≥3 个条目里,于是最常见的虚词也成了「独特指纹」。2026-08-16 实测:一本新书
# 因为与旧书共享「事情」「所有」两个词被判跨书污染、构思整体毙掉。
# 故取 `min(该常数, max(2, 条目数))`——两个条目都出现即算背景。
_ECHO_BACKGROUND_ENTRY_COUNT = 3


def _is_cjk_char(ch: str) -> bool:
    return "一" <= ch <= "鿿"


@lru_cache(maxsize=1)
def _background_bigrams_zh() -> frozenset[str]:
    """人类语料里的通用二元组——跨书回声里一律不算指纹。

    文档频率 ≥5% 标定自 `.distillation_private`(3969 章抽样)。分离度是三个
    数量级:「一个」95.2%、「自己」89.6%、「事情」56.6%、「所有」53.8%,而真正的
    书指纹「澡堂」0.2%、「真话」0.9%、「挂号」0.1%、「画符」0.4% 全在 1% 以下。
    5%~12% 这一带抽检 40 项全是通用词(隐隐/错了/严重/一顿…),无题材或机制词,
    所以 5% 这个切点有 5 倍安全边际。

    缺文件时返回空集:回声门禁退回原行为(会更容易误报),但绝不因为读不到词表
    而放行或崩溃。
    """

    path = Path(__file__).resolve().parents[3] / "data" / "echo" / "background_bigrams_zh.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("background bigram list unavailable at %s", path, exc_info=True)
        return frozenset()
    grams = payload.get("bigrams") if isinstance(payload, dict) else payload
    if not isinstance(grams, list):
        return frozenset()
    return frozenset(str(g) for g in grams if isinstance(g, str) and len(g) == 2)


def _content_bigrams(text: str) -> set[str]:
    """CJK 二元组，**扣掉人类语料里的通用词**。

    函数名一直承诺「content」,但原实现返回的是全部 CJK 二元组、没有任何内容
    过滤——唯一的过滤在调用方那个「≥3 个条目」的背景集里,而它在小书库下失效。
    真正的过滤放这里,名字才成立。
    """

    background = _background_bigrams_zh()
    grams: set[str] = set()
    for i in range(len(text) - 1):
        pair = text[i : i + 2]
        if _is_cjk_char(pair[0]) and _is_cjk_char(pair[1]) and pair not in background:
            grams.add(pair)
    return grams


def _longest_common_cjk_span(a: str, b: str) -> str:
    """Longest common substring of ``a``/``b`` made of CJK characters only."""

    a_cjk = "".join(ch if _is_cjk_char(ch) else " " for ch in a)
    b_cjk = "".join(ch if _is_cjk_char(ch) else " " for ch in b)
    best_len, best_end = 0, 0
    prev = [0] * (len(b_cjk) + 1)
    for i in range(1, len(a_cjk) + 1):
        cur = [0] * (len(b_cjk) + 1)
        ch = a_cjk[i - 1]
        if ch != " ":
            for j in range(1, len(b_cjk) + 1):
                if ch == b_cjk[j - 1]:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best_len:
                        best_len, best_end = cur[j], i
        prev = cur
    return a_cjk[best_end - best_len : best_end]


def _entry_echo_text(entry: dict[str, Any]) -> str:
    tropes = " ".join(_normalize_string_list(entry.get("trope_keywords")))
    return " ".join(
        str(entry.get(key) or "") for key in ("title", "golden_finger", "premise")
    ) + f" {tropes}"


def _candidate_echo_text(final_result: dict[str, Any]) -> str:
    profile = final_result.get("writing_profile")
    profile = profile if isinstance(profile, dict) else {}
    character = profile.get("character")
    character = character if isinstance(character, dict) else {}
    market = profile.get("market")
    market = market if isinstance(market, dict) else {}
    tropes = " ".join(_normalize_string_list(market.get("trope_keywords")))
    return " ".join(
        [
            str(final_result.get("title") or ""),
            str(final_result.get("premise") or ""),
            str(character.get("golden_finger") or ""),
            tropes,
        ]
    )


def _label_bigrams(
    final_result: dict[str, Any], entries: list[dict[str, Any]]
) -> set[str]:
    """双方**标签字段**的词汇——按来源判定，不按词频。

    ``trope_keywords`` 是选题标签栏（悬疑/权谋/扮猪吃虎…），同题材书共享它
    是设计使然。它却被拼进了指纹比对文本，于是「都是玄幻悬疑权谋」被读成
    「抄了那本书」。
    """

    texts: list[str] = []
    profile = final_result.get("writing_profile")
    market = profile.get("market") if isinstance(profile, dict) else None
    if isinstance(market, dict):
        texts.extend(_normalize_string_list(market.get("trope_keywords")))
    texts.extend(str(t) for t in _normalize_string_list(final_result.get("tags")))
    for entry in entries:
        texts.extend(_normalize_string_list(entry.get("trope_keywords")))
        texts.extend(_normalize_string_list(entry.get("tags")))
    return _content_bigrams(" ".join(texts))


def _echo_report_has_hard_evidence(report: list[dict[str, Any]]) -> bool:
    """报告里是否有**逐字重合片段**——只有它够格阻断建书。

    2026-08-23 真机：验证书 8 因 `shared_span=""` + 三个零散 bigram 被处决，
    project_created=false。我随后试了两条统计判据想把噪声 bigram 单独筛掉，
    都被自己的测量证伪：

      * PMI（词性）：噪声「里那」-0.39 vs 真机制词「神位」-0.34，而边界碎片
        「了一」2.62 更高 —— 不可分。
      * 文档频率（12000 章）：「里那」2.9%，真机制词「阵法」5.5%、「丹田」
        2.1% —— 切点定在哪都会切掉真词。

    既然单个 2 字 bigram 在统计上无法与噪声区分，「3 个零散 bigram」就不足以
    证明可见复用，更不足以判死刑。它照旧留痕、照旧换来一次重生（与本文件里
    ``debt_hit`` / ``motif_hits`` 两条先例同待遇），杀权只留给逐字片段。
    """

    return any(str(item.get("shared_span") or "").strip() for item in report)


def _mechanism_echo_report(
    final_result: dict[str, Any],
    entries: list[Any],
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    user_intent_text: str = "",
) -> list[dict[str, Any]]:
    """Per-old-book surface-echo findings for a finalized concept.

    A finding means the candidate visibly reuses one specific old book's
    material: a verbatim CJK span of ≥ ``_ECHO_SPAN_MIN_CHARS`` chars, or
    ≥ ``_ECHO_BIGRAM_MIN_HITS`` distinctive bigrams. Bigrams recurring across
    ``_ECHO_BACKGROUND_ENTRY_COUNT``+ entries or present in the genre labels
    are background vocabulary and never count. Empty list = no collision.
    """

    if not isinstance(final_result, dict) or not final_result:
        return []
    clean_entries = [e for e in entries if isinstance(e, dict)]
    if not clean_entries:
        return []

    candidate_text = _candidate_echo_text(final_result)
    candidate_grams = _content_bigrams(candidate_text)
    if not candidate_text.strip():
        return []

    # 标签词永远不是指纹（2026-08-23 真机：验证书 8 被「悬疑/权谋/里那」判死）。
    # 本函数 docstring 早就写着「present in the genre labels are background
    # vocabulary and never count」，但只减掉了 genre/sub_genre——而
    # ``trope_keywords`` 这个同样是标签的字段被 `_candidate_echo_text` /
    # `_entry_echo_text` 拼进了比对文本，于是同题材书按设计共享的「悬疑」
    # 「权谋」成了跨书抄袭证据。人类语料 12000 章实测这类词在正文里的
    # 文档频率只有 0.02%~0.2%（它们活在标签栏不活在正文），DF 背景表在
    # 结构上就抓不到它们——必须按字段来源减，不能指望词频。
    label_vocabulary = _label_bigrams(final_result, clean_entries)
    entry_grams = [_content_bigrams(_entry_echo_text(e)) for e in clean_entries]
    gram_entry_count: dict[str, int] = {}
    for grams in entry_grams:
        for g in grams:
            gram_entry_count[g] = gram_entry_count.get(g, 0) + 1
    # 小书库退化保护：只有 2 本旧书时，`>= 3` 永远不成立，于是背景过滤整体空转。
    # 两个条目都出现的词，按任何合理定义都是背景而不是某一本的身份。
    background_entry_min = min(_ECHO_BACKGROUND_ENTRY_COUNT, max(2, len(clean_entries)))
    background = {
        g for g, n in gram_entry_count.items() if n >= background_entry_min
    }
    background |= _content_bigrams(f"{genre or ''} {sub_genre or ''}")
    background |= label_vocabulary

    report: list[dict[str, Any]] = []
    for entry, grams in zip(clean_entries, entry_grams):
        shared = sorted((candidate_grams & grams) - background)
        span = _longest_common_cjk_span(candidate_text, _entry_echo_text(entry))
        if len(span) < _ECHO_SPAN_MIN_CHARS:
            span = ""

        # Static platform clichés are broad category guardrails, not prior-book
        # fingerprints.  Two generic bigrams (for example 血脉/觉醒/逆袭) must
        # never turn a user-selected genre trope into "cross-book pollution".
        # Only a specific cliché phrase or a long copied span may flag these
        # entries, and an explicitly requested matching trope is authoritative.
        is_platform_cliche = entry.get("source") == "platform_cliche"
        if is_platform_cliche:
            aliases = _normalize_string_list(entry.get("intent_aliases"))
            if any(alias and alias in user_intent_text for alias in aliases):
                continue
            trope_phrases = _normalize_string_list(entry.get("trope_keywords"))
            specific_phrase_hit = any(
                len("".join(ch for ch in phrase if _is_cjk_char(ch))) >= 4
                and phrase in candidate_text
                for phrase in trope_phrases
            )
            if not specific_phrase_hit and len(span) < 8:
                continue

        # 重叠的 bigram 先合并回原短语——「废柴逆袭」是一份证据不是三份。
        shared = merge_overlapping_bigrams(shared)
        _min_hits = (
            _ECHO_BIGRAM_MIN_HITS if span else _ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN
        )
        if span or len(shared) >= _min_hits:
            report.append(
                {
                    "title": str(entry.get("title") or "").strip(),
                    "shared_span": span,
                    "shared_bigrams": shared,
                }
            )
    return report


def merge_overlapping_bigrams(bigrams: list[str] | tuple[str, ...]) -> list[str]:
    """把首尾重叠的 2-gram 合并回它们本来的短语，再计数。

    2026-08-22 真机定罪：滑动 2-gram 会把「废柴逆袭」拆成「废柴」「柴逆」
    「逆袭」三条——**一个共享短语被数成三份独立证据**，命中数虚高 3 倍，
    直接冲破定罪门槛。而「柴逆」根本不是词，它跨了词边界。

    合并后一个短语只算一份证据，这才是它本来的证据力。
    """

    items = sorted({str(b).strip() for b in bigrams if str(b).strip()})
    if len(items) <= 1:
        return items
    remaining = list(items)
    merged: list[str] = []
    while remaining:
        run = remaining.pop(0)
        extended = True
        while extended:
            extended = False
            for candidate in list(remaining):
                if run.endswith(candidate[0]) and run[-1] == candidate[0]:
                    run = run + candidate[1:]
                    remaining.remove(candidate)
                    extended = True
                elif candidate.endswith(run[0]) and candidate[-1] == run[0]:
                    run = candidate[:-1] + run
                    remaining.remove(candidate)
                    extended = True
        merged.append(run)
    return sorted(merged)


def _echo_severity(report: list[dict[str, Any]]) -> int:
    """Comparable badness score: verbatim spans dominate, bigrams add up."""

    score = 0
    for item in report:
        score += 3 * len(str(item.get("shared_span") or ""))
        score += len(item.get("shared_bigrams") or [])
    return score


def _should_adopt_mechanism_retry(
    *,
    retry_result: Any,
    original_echo: list[dict[str, Any]],
    retry_echo: list[dict[str, Any]],
    original_hard: tuple[bool, ...],
    retry_hard: tuple[bool, ...],
    original_motif_count: int = 0,
    retry_motif_count: int = 0,
) -> bool:
    """Hard pollution repair outranks weak surface-similarity noise.

    The real failure case produced a debt-free retry, then discarded it because
    two generic numeral bigrams made ``retry_echo`` one point worse.  When the
    original has a hard violation, a valid retry that clears every hard class is
    always safer than the original; echo severity is only a tie-breaker when
    neither side has a hard violation.

    Motif amplification (2026-08-09) is advisory and cannot make a retry
    mandatory, so it only decides the case where it is the *sole* complaint:
    there, swap only if the rewrite actually reduced the takeover. Trading one
    concept for an equally saturated one buys nothing and risks losing a better
    premise.
    """

    if not isinstance(retry_result, dict) or not retry_result:
        return False
    if any(original_hard):
        return not any(retry_hard)
    if any(retry_hard):
        return False
    if original_motif_count and _echo_severity(original_echo) == 0:
        return retry_motif_count < original_motif_count
    return _echo_severity(retry_echo) <= _echo_severity(original_echo)


def _render_mechanism_echo_feedback(
    report: list[dict[str, Any]], *, is_en: bool
) -> str:
    """Hard retry feedback that never quotes prior-book material."""

    if not report:
        return ""
    count = len(report)
    if is_en:
        return (
            "\n\n[Rewrite required — cross-book similarity gate]\n"
            f"The result matched {count} withheld prior-book fingerprint(s). Their text is "
            "not shown to avoid priming. Rebuild the opening situation, differentiating "
            "edge, operating principle, and dominant imagery from the user's current intent."
        )
    return (
        "\n\n【重写要求 · 跨书相似度门】\n"
        f"当前结果命中{count}条已隔离的旧书指纹；为避免反向提示，旧书原文不会展示。"
        "请从本次用户设定重新生成主角开局处境、差异化优势、作用原理与核心意象，"
        "不得沿用上一版的名词和句式。"
    )


def _render_debt_rewrite_feedback(
    *, is_en: bool, matched_terms: tuple[str, ...] = ()
) -> str:
    """2026-08-13 复活（词汇 withhold 版）。

    触发条件回到「用户没点名 + 冠军被该默认族支配」。反馈沿用本体漂移
    渲染器的策略：不点任何族内词汇（点名即种词），只下达换族指令。
    """

    quoted_en = ""
    quoted_zh = ""
    if matched_terms:
        joined = ", ".join(f"'{t}'" for t in matched_terms[:8])
        quoted_en = (
            f" The words you just wrote that put it in that family: {joined}. "
            "Swapping them for close synonyms keeps the concept in the SAME "
            "family and does not count as a rewrite."
        )
        joined_zh = "、".join(f"「{t}」" for t in matched_terms[:8])
        quoted_zh = (
            f"本次构思里把它归进该族的正是你自己写下的这些词：{joined_zh}。"
            "把它们换成近义词仍然停在**同一个族**里，不算重写。"
        )
    if is_en:
        return (
            "\n\n[Rewrite required — default-theme convergence]\n"
            "The core mechanism/scenes of this concept fall into a theme "
            "family this framework has measurably over-reused, and the user "
            "did not ask for it. Rebuild around a COMPLETELY different "
            "mechanism family and scene family; do not reuse this concept's "
            "core nouns or their close synonyms." + quoted_en
        )
    return (
        "\n\n【必须重写——默认主题族收敛】\n"
        "本次构思的核心机制与场景落在了本框架被实测过度复用的默认主题族上，"
        "且用户并没有要求它。换一个**完全不同**的机制家族与场景家族重新构思；"
        "不得沿用本次构思的核心名词及其近义词。" + quoted_zh
    )


def _render_death_revival_rewrite_feedback(*, is_en: bool) -> str:
    """Retired 2026-08-02 — unreachable, renders nothing."""

    del is_en
    return ""


def _render_ontology_drift_rewrite_feedback(hits: tuple[str, ...], *, is_en: bool) -> str:
    """Retry feedback that withholds the drift vocabulary from generation."""

    count = len(hits)
    if is_en:
        return (
            "\n\n[Rewrite required — genre ontology mismatch]\n"
            f"The deterministic gate found {count} incompatible setting term(s); their text is "
            "withheld to avoid priming. Rebuild every setting, role, institution, tool, and action "
            "from the selected genre's native world rules and present story source."
        )
    return (
        "\n\n【重写要求 · 题材本体不一致】\n"
        f"确定性检测发现{count}个不属于所选题材世界的设定词；为避免反向提示，具体词不展示。"
        "请只依据已选题材的原生世界规律和当前故事事实，重新建立场景、身份、机构、工具与行动。"
    )


def _hook_candidate_seed(genre_key: str) -> int:
    """Per-run rotation seed for anti-commonsense hook candidates.

    Was ``sha256(genre_key)`` — deterministic per genre, so every book of a
    genre preset drew the *same* candidate rotation and the same top hook;
    that single hook (mind-reading cost / "旁人以为他会算命") then leaked into
    every xianxia book's premise and golden finger. Mixing per-run entropy
    keeps mechanism-bucket rotation coverage while restoring cross-book
    diversity (the planner path already seeds per book via slug+premise).
    """

    return int(
        hashlib.sha256(f"{genre_key}:{uuid4().hex}".encode()).hexdigest()[:8],
        16,
    )


def _hook_duplicate_corpus(
    ctx: dict[str, Any], user_hints: dict[str, Any] | None
) -> list[str]:
    """Texts hook candidates are penalised for duplicating.

    This book's own inputs plus recent same-genre books' mechanism summaries
    (``ctx['avoid_mechanisms']``), so candidate ranking applies cross-book
    novelty pressure instead of only self-consistency.
    """

    corpus = [
        str(ctx.get("description") or ""),
        str(ctx.get("premise_seed") or ""),
        str(user_hints or "") if user_hints else "",
    ]
    for entry in ctx.get("avoid_mechanisms") or []:
        if isinstance(entry, dict):
            corpus.append(_entry_echo_text(entry))
    return [text for text in corpus if text.strip()]


def _character_user_prompt(ctx: dict[str, Any], genre_profile: GenreReviewProfile | None = None) -> str:
    from bestseller.services.genre_persona import render_channel_style_stamp

    # 主角在这里出生:第10轮真机,淘汰赛干涸后保底路径给男频请求生成了虐女主
    # 圣母概念——频道钢印必须盖在主角诞生处,不只盖在包装工序。
    prompt = (
        render_channel_style_stamp((ctx.get("user_hints") or {}).get("audience_orientation"))
        + f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n请设计角色体系 JSON，包含：\n"
        f'{{"protagonist_archetype": "主角原型（如：重生复仇者、天才少年、隐忍谋略家）",\n'
        f'  "protagonist_name": "为主角取一个自然、好记、符合题材背景的中文名（2-3字）",\n'
        f'  "protagonist_name_reasoning": "命名理由",\n'
        f'  "protagonist_age": 主角年龄数字,\n'
        f'  "protagonist_profession": "主角职业+职级/身份（如：急诊科主治医师/刑警队实习警员/外卖骑手）",\n'
        f'  "career_reality_ledger": "职业现实自洽账（硬要求）：列式核算 年龄=入行年龄+培养年限+执业年限，'
        f'且职级与年限匹配。例：临床医生=5年本科毕业23岁+3年规培26岁才能独立值班，'
        f'主治医师≥29岁、十年资历的夜班医生≥36岁；律师=法考+1年实习≥25岁；'
        f'刑警=警校+基层年限。账算不平就改年龄或改资历，绝不硬凑'
        f'（反例：32岁干了十年夜班急诊=账不平，直接毙）。拿不准的职业写模糊年限不写具体数字",\n'
        f'  "profession_boundary": "该职业的职权与场地边界一句话（如：急诊科医生不接管住院科室的抢救，'
        f'跨科出现需要会诊单/支援调令这类制度性理由）——全书写作不得越界，越界必须先给制度性理由",\n'
        f'  "protagonist_core_drive": "主角核心驱动力",\n'
        f'  "golden_finger": "主角的差异化优势——形态按【与本书冲突/世界规律的贴合度】择优'
        f'（系统/血脉觉醒/上古传承/特殊体质/独门手艺/重生先知/气运/信息差/契约异兽等，可自创），'
        f'【绝不默认系统/属性面板】（已极烂大街）；若题材本不依赖外挂（武侠/历史/权谋/文学向/群像），'
        f'可写“无显性金手指，优势在X（谋略/境界/人脉/性格）”",\n'
        f'  "growth_curve": "成长曲线描述",\n'
        # 占位符只描述形状，不给会被逐字抄走的假阶名——2026-08-24 真机教训：
        # `"selling_points": ["卖点1：…", "卖点2：同上"]` 让模型把「卖点1：」
        # 当成内容抄了出去。
        f'  "power_tiers": ["主角依次经过的层级名，按顺序，每项不超过8字；'
        f'没有分层体系就给空数组"],\n'
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
        f'     "age_profession": "年龄+职业（有职业设定的角色必填，年龄资历同样要账算得平）",\n'
        f'     "personality_keywords": ["关键词1", "关键词2"],\n'
        f'     "relationship_to_protagonist": "与主角的关系"}}\n'
        f'  ]\n'
        f"}}\n"
        f"\n【职业现实硬要求——违者整份提案作废】\n"
        f"读者里永远有干这行的人。任何职业角色必须过三道账：\n"
        f"① 年龄账：年龄=入行年龄+培养年限+执业年限，列式核算，算不平就改；\n"
        f"② 职级账：职级/头衔与年限匹配（规培医生≠主治≠主任；实习律师≠合伙人）；\n"
        f"③ 边界账：角色只能在其职权与场地内行事（急诊医生不接管住院病区抢救、"
        f"片区民警不主办刑事大案、实习生不签手术单）——剧情需要跨界时，必须先在设定里"
        f"写明制度性理由（会诊、借调、支援、代班），否则改剧情。\n"
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
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _concept_methodology_prompt_block(ctx)
    prompt += _mechanism_dedup_prompt_block(ctx, is_en=False)
    prompt += _anti_debt_metaphor_guardrail(ctx, is_en=False)
    prompt += _naming_constraint_block(ctx, is_en=False)
    # cost_style is a user CHOICE, not a framework guardrail, so it belongs
    # in the stage that designs mechanics. The retired motif police left
    # nothing on the output side (2026-08-02, all detectors are False-shims),
    # so a 爽文无代价 book kept acquiring a debt/backlash engine here.
    prompt += _cost_style_block_for_ctx(ctx, is_en=False)
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
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _concept_methodology_prompt_block(ctx)
    # cost_style is a user CHOICE, not a framework guardrail, so it belongs
    # in the stage that designs mechanics. The retired motif police left
    # nothing on the output side (2026-08-02, all detectors are False-shims),
    # so a 爽文无代价 book kept acquiring a debt/backlash engine here.
    prompt += _cost_style_block_for_ctx(ctx, is_en=False)
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
    prompt += _default_motif_guardrail(ctx, is_en=True)
    prompt += _concept_methodology_prompt_block(ctx)
    prompt += _mechanism_dedup_prompt_block(ctx, is_en=True)
    prompt += _anti_debt_metaphor_guardrail(ctx, is_en=True)
    # cost_style is a user CHOICE, not a framework guardrail, so it belongs
    # in the stage that designs mechanics. The retired motif police left
    # nothing on the output side (2026-08-02, all detectors are False-shims),
    # so a 爽文无代价 book kept acquiring a debt/backlash engine here.
    prompt += _cost_style_block_for_ctx(ctx, is_en=True)
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
        f'  "golden_finger": "the protagonist\'s differentiating edge — pick the FORM that best '
        f'fits this book\'s conflict/world (system, bloodline, inheritance, special physique, '
        f'signature craft, rebirth/precognition, info-edge, contracted beast, etc.; invent one if '
        f'better). NEVER default to a stat/system panel (oversaturated). If the genre needs no '
        f'external cheat (wuxia/history/literary/ensemble), write \'no explicit golden finger; '
        f'the edge is X\'",\n'
        f'  "growth_curve": "character growth arc description",\n'
        f'  "power_tiers": ["tier names the lead passes through, in order, <=8 chars each; empty array when there is no tier system"],\n'
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
    prompt += _default_motif_guardrail(ctx, is_en=True)
    prompt += _concept_methodology_prompt_block(ctx)
    prompt += _mechanism_dedup_prompt_block(ctx, is_en=True)
    prompt += _anti_debt_metaphor_guardrail(ctx, is_en=True)
    prompt += _naming_constraint_block(ctx, is_en=True)
    # cost_style is a user CHOICE, not a framework guardrail, so it belongs
    # in the stage that designs mechanics. The retired motif police left
    # nothing on the output side (2026-08-02, all detectors are False-shims),
    # so a 爽文无代价 book kept acquiring a debt/backlash engine here.
    prompt += _cost_style_block_for_ctx(ctx, is_en=True)
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
    prompt += _default_motif_guardrail(ctx, is_en=True)
    prompt += _concept_methodology_prompt_block(ctx)
    # cost_style is a user CHOICE, not a framework guardrail, so it belongs
    # in the stage that designs mechanics. The retired motif police left
    # nothing on the output side (2026-08-02, all detectors are False-shims),
    # so a 爽文无代价 book kept acquiring a debt/backlash engine here.
    prompt += _cost_style_block_for_ctx(ctx, is_en=True)
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
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _concept_methodology_prompt_block(ctx)
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
    prompt += _default_motif_guardrail(ctx, is_en=True)
    prompt += _concept_methodology_prompt_block(ctx)
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
    # (2026-08-01 product ruling) The framework-authored 高唤起情绪范例表
    # (genre_emotion_exemplars) was deleted from this prompt: a shared per-genre
    # event list steers every same-genre book toward the same events. The
    # requirement stays ("front-load THIS book's high-arousal event") without
    # the framework supplying the events.
    from bestseller.services.blurb_appeal_gate import platform_blurb_band

    # 简介字数带与验收闸门同源（按目标平台解析；旧版硬编码 80-140 与起点 140-220 打架）。
    _blurb_platform = str(
        market.get("platform_target")
        or ctx.get("platform_target")
        or ctx.get("default_platform")
        or ""
    )
    _band_min, _band_max = platform_blurb_band(_blurb_platform)
    # Pass the user-selected channel so an explicit 通用/女频 pick is honoured
    # here too. The outline layer already passes it (planner._planner_channel_key);
    # conception was silently dropping it and re-inferring 男频 from the genre,
    # so a 通用 selection on a 玄幻 book still got the 打脸/扮猪吃虎 persona.
    # (2026-08-01 product ruling) The hardcoded reader-persona anchor
    # (who/knowledge/fantasy/turnoffs/hook_formula from genre_persona tables)
    # was deleted: framework-authored persona sheets must not enter prompts.
    # The channel stamp below survives because it only translates the USER's
    # explicit 男频/女频 pick.
    from bestseller.services.genre_persona import render_channel_style_stamp

    _channel_stamp = render_channel_style_stamp(
        (ctx.get("user_hints") or {}).get("audience_orientation")
    )
    base = (
        f"{_channel_stamp}"
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"权威故事创意：{ctx.get('description') or '按冻结建书选项生成'}\n"
        f"最终方案必须保留这条故事身份，不得用提案中的新故事替换。\n"
        f"\n## 市场定位提案\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## 角色体系提案\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## 世界观提案\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n## 审查意见\n{json.dumps(review, ensure_ascii=False, indent=2)}\n"
        # 2026-08-18《九姓井口只认我》定罪：chief_editor 抓到 8 条真矛盾
        # （世代数打架/死因两版本/标签与体系冲突）但审查意见只是背景资料，
        # director 从不逐条裁决——矛盾直通成稿。矛盾清单必须变成必裁清单。
        f"\n【必裁清单——审查意见里的 contradictions 与 gaps 逐条裁决】\n"
        f"上面审查意见中列出的每一条 contradiction 和 gap，你必须二选一：\n"
        f"(a) 在最终方案的对应字段里直接消除它（改数字/删冲突设定/统一事实）；\n"
        f"(b) 在输出 JSON 的 review_adjudications 数组里写明为何保留原样。\n"
        f"世代数、年份、死亡地点、人名这类硬事实在所有字段间必须只有一个版本。\n"
        f"\n{_GOLDEN_FINGER_DESIGN_PRINCIPLE}\n"
        f"\n请根据以上讨论成果，生成最终方案 JSON：\n"
        f'{{\n'
        f'  "title": "书名种子。必须匹配 writing_profile.market.platform_target：'
        f'番茄/飞卢可用开局、身份反差、金手指和强钩子；起点要有短 IP 感、'
        f'职业/制度/世界观质感；七猫强调身份逆袭、强职业、低位起势；'
        f'晋江强调关系张力、情绪钩子和标签筛选。禁止只写抽象意象或题材名。",\n'
        f'  "premise": "小说前提/核心设定（100-200字，包含主角、核心冲突、主角差异化优势'
        f'（金手指或谋略/境界/人脉等，不必是系统）和悬念）",\n'
        f'  "synopsis": "面向读者的【点击型】作品简介（番茄/起点详情页文案，目标：读者只看这段就忍不住点进去）。'
        f'硬性要求，逐条照做：'
        f'①长度 {_band_min}-{_band_max} 字（中文字符，目标平台带），不是长设定介绍——要短、要狠；'
        f'②首句 ≤30 字，必须是一句能瞬间抓人的强钩：用反差或开局冲突事件开场'
        f'（陈述句；真榜单只有 6% 问句收尾、问句钩也已过时），'
        f'严禁用"穿越到…的他/本以为…"这类平铺设定句开头；'
        f'③卖点三要素必须齐全：主角身份反差 + 开局冲突事件 + 失败代价（不做到会怎样）；'
        f'③b【主角优势必须讲清】：用一句大白话让读者看懂主角靠什么'
        f'（能力/机缘/谋略/外挂）能做到别人做不到的事、由此拿到什么好处或翻盘——'
        f'这一句先写可见行动与直接收益，再写由该行动自然产生的限制。'
        f'③c【主线上升感必须可见】：用一句让读者看到这书的上升阶梯或终极目标'
        f'（从X做到Y、越做越大、最终要成为/夺下/对上谁），'
        f'让人知道爽点会持续升级、这书值得一路追——只有开局没有"往哪走"= 主线模糊，必劝退；'
        f'④把本书自己最强的高唤起情绪事件放在最前面（从本书前提与冲突里选，不套其他题材的情绪词）；'
        f'⑤结尾用陈述句或名场面截断（不许问句收尾、不许"殊不知/却不知道"式'
        f'全知吊胃口），绝不剧透关键反转或结局；'
        f'⑥分 2-4 段、动词驱动、克制形容词。'
        f'⑦【新读者可懂铁律】当成写给一个完全没读过本书、不懂任何设定的陌生人看：'
        f'禁止堆砌生造黑话/自定义机制名/系统术语/等级编号（如「灵码编辑器」「怪谈词条」'
        f'「S级/#0371」「数据化修炼」之类）——每个独特概念要么不出现、要么紧跟一句大白话点破'
        f'它是什么、有什么用、不做会怎样；一整段最多保留 1 个需要脑补的专有名词。'
        f'读者看完必须能一句话说出：主角是谁、要干什么、爽点/钩子在哪。'
        f'⑧【自洽铁律，违反即废稿】简介全文只许存在一条倒计时/期限（多个时间压力'
        f'选最狠的一个写，其余不写）；人物年龄、经历年数、物品在谁手里，必须与'
        f'premise 和 story_spine 完全一致；每句由上句因果引出，不是各写各的卖点'
        f'再拼起来；写完逐句自查任何两句不得互相矛盾。'
        f'严禁 AI 腔与套话：本以为/却没想到/命运的齿轮/何去何从/拭目以待/敬请期待/一段不平凡的旅程）",\n'
        f'  "tags": ["标签1", "标签2", "...（5-10个作品标签，包括题材、风格、元素、受众标签）"],\n'
        f'  "review_adjudications": ["对审查意见中每条 contradiction/gap 的裁决：'
        f'已消除的写「已消除：怎么改的」，保留的写「保留：理由」"],\n'
        f'  "story_spine": {{\n'
        f'    "who": "主角一句话身份（名字+处境）",\n'
        f'    "wants": "他要的具体目标——可验收（拿到X/救出X/在X之前做到X）。'
        f'严禁\'活下去/变强/复仇\'这类没有宾语的模糊词",\n'
        f'    "why_now": "触发事件：为什么是现在非动不可。只写一个触发事件 + '
        f'至多一条期限——把多个时间压力塞进这一格（灶火只剩一个时辰+月底断租）'
        f'会让下游文案自相矛盾；数字必须与 premise 一致（年龄/从业年数别打架）",\n'
        f'    "against": "挡路的人/势力/规则（有名字或有形态）",\n'
        f'    "stakes": "做不到就失去什么（具体：谁的命/什么身份/哪个家）",\n'
        f'    "question": "读者一路追的问题，一句疑问句（他能不能……？）"\n'
        f'  }},\n'
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
        f'      "power_tiers": ["主角依次经过的层级名，按顺序，每项≤8字；没有分层体系就给空数组"],\n'
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
        f"\n\n【故事脊柱硬测试——脊柱不合格整份方案作废】\n"
        f"story_spine 六字段连读必须是一段60字左右、能讲给朋友听的人话："
        f"「(who)想要(wants)，因为(why_now)；但(against)挡着；做不到，(stakes)。(question)」"
        f"朋友听完能复述出「这书讲什么」=合格；复述不出=重写脊柱。\n"
        f"wants 必须具体可验收（拿到X/救出X/在X之前做到X），「活下去/变强/复仇」"
        f"这类没有宾语的词不构成故事目标。\n"
        f"全书所有设定/规则/金手指/简介卖点都必须服务这根脊柱——服务不上的设定，删。"
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
    base += _default_motif_guardrail(ctx, is_en=False)
    base += _mechanism_dedup_prompt_block(ctx, is_en=False)
    base += _anti_debt_metaphor_guardrail(ctx, is_en=False)
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
        f"Authoritative story idea: {ctx.get('description') or 'derive from frozen creation controls'}\n"
        f"Preserve this story identity; do not replace it with a new story from the proposals.\n"
        f"\n## Market Positioning Proposal\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## Character System Proposal\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## World-Building Proposal\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n## Review Feedback\n{json.dumps(review, ensure_ascii=False, indent=2)}\n"
        f"\n{_GOLDEN_FINGER_DESIGN_PRINCIPLE_EN}\n"
        f"\nBased on the above discussion, generate the final plan JSON:\n"
        f'{{\n'
        f'  "title": "Title seed matched to writing_profile.market.platform_target. '
        f'It must signal genre, hook, audience, and shelf fit. Royal Road/KU/Wattpad-style '
        f'titles may be longer and more direct; literary or premium platforms may be shorter '
        f'and more IP-like. Avoid generic genre labels.",\n'
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
        f'      "power_tiers": ["主角依次经过的层级名，按顺序，每项≤8字；没有分层体系就给空数组"],\n'
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
    base += _default_motif_guardrail(ctx, is_en=True)
    base += _mechanism_dedup_prompt_block(ctx, is_en=True)
    base += _anti_debt_metaphor_guardrail(ctx, is_en=True)
    return base


def _pollution_retry_finalize_prompt(
    ctx: dict[str, Any],
    *,
    genre_profile: GenreReviewProfile | None,
    is_en: bool,
) -> str:
    """Build a clean-room finalization prompt after a pollution rejection.

    Market, character, world, review, exploration, and old-book material are
    mutable model outputs. Feeding them into the repair round reproduces the
    rejected story. The clean-room retry sees only the frozen creation choices
    and the tournament champion that already passed deterministic screening.
    """

    clean_ctx = copy.deepcopy(ctx)
    for key in (
        "commercial_brief",
        "avoid_mechanisms",
        "creative_premise_seed",
        "creative_hook",
    ):
        clean_ctx.pop(key, None)

    user_hints = clean_ctx.get("user_hints")
    explicit_seed = (
        str(user_hints.get("concept_seed") or "").strip()
        if isinstance(user_hints, Mapping)
        else ""
    )
    champion = clean_ctx.get("high_concept")
    champion_source = {
        key: champion.get(key)
        for key in (
            "concept",
            "mechanism",
            "protagonist_identity",
            "protagonist_private_desire",
            "core_abnormality",
            "opening_crisis",
            "opponent_system",
            "decision_proof",
        )
        if isinstance(champion, Mapping) and champion.get(key) not in (None, "", [], {})
    }
    automatic_seed = str(clean_ctx.get("automatic_story_seed") or "").strip()
    # A dry tournament has no champion.  In that branch the validated
    # Story-Architect seed is the only authoritative story identity; putting it
    # only in the trailing prose is insufficient because the base finalizer can
    # still invent a new premise before reading the source note.
    if not explicit_seed and not champion_source and automatic_seed:
        clean_ctx["description"] = automatic_seed

    prompt_fn = _finalize_user_prompt_en if is_en else _finalize_user_prompt
    base = prompt_fn(clean_ctx, {}, {}, {}, {}, genre_profile)
    base = _attach_conception_methodology(
        base,
        ctx=clean_ctx,
        is_en=is_en,
        token_budget=800,
    )
    if is_en:
        source = (
            "\n\n[Clean-room authoritative story source]\n"
            f"Explicit user seed: {explicit_seed or '(not provided; follow frozen controls)'}\n"
            f"Screened champion: {json.dumps(champion_source, ensure_ascii=False)}\n"
            f"Automatic story seed: {automatic_seed or '(none)'}\n"
            "Generate only from this source and the frozen creation controls above."
        )
    else:
        source = (
            "\n\n【隔离后的权威故事事实】\n"
            f"用户原始创意：{explicit_seed or '未填写；按冻结建书选项生成'}\n"
            f"已过筛冠军：{json.dumps(champion_source, ensure_ascii=False)}\n"
            f"系统自动故事种子：{automatic_seed or '(无)'}\n"
            "只从这里和上方冻结建书选项生成，不继承任何被拒中间稿。"
        )
    return base + source


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
) -> tuple[dict[str, Any], list[UUID]]:
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
            f"{_default_motif_guardrail(ctx, is_en=True)}"
            f"{_mechanism_dedup_prompt_block(ctx, is_en=True)}"
            f"{_anti_debt_metaphor_guardrail(ctx, is_en=True)}\n\n"
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
            f"{_default_motif_guardrail(ctx, is_en=False)}"
            f"{_mechanism_dedup_prompt_block(ctx, is_en=False)}"
            f"{_anti_debt_metaphor_guardrail(ctx, is_en=False)}\n\n"
            "请生成3个差异化创意方向 JSON：\n"
            '{"directions": [\n'
            '  {"premise_variation": "前提变体描述", "unique_hook": "独特卖点", "avoids_traps": ["trap_key"]},\n'
            '  ...\n'
            '],\n'
            '"chosen_direction": {"premise_variation": "最终选择", "unique_hook": "差异化卖点", "reason": "选择理由"}}'
        )

    return await _llm_call_json(
        session, settings,
        role="planner",
        system_prompt=_CREATIVE_EXPLORATION_SYSTEM_EN if is_en else _CREATIVE_EXPLORATION_SYSTEM,
        user_prompt=user_prompt,
        fallback='{"directions": [], "chosen_direction": {}}',
        template="conception_creative_exploration",
        stage="conception.creative_exploration",
        language=str(ctx.get("language") or "zh-CN"),
    )


# ─────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────


async def _attach_concept_methodology(
    session: AsyncSession,
    settings: AppSettings,
    ctx: dict[str, Any],
    *,
    user_hints: dict[str, Any] | None,
) -> None:
    """Run Agent ① and attach its methodology to ctx (best-effort, non-fatal)."""

    if not getattr(settings.pipeline, "enable_concept_methodology_agent", True):
        return
    try:
        from bestseller.services.concept_methodology_agent import (
            render_concept_methodology_block,
            select_concept_methodology,
        )

        hints = user_hints if isinstance(user_hints, dict) else {}
        orientation_raw = str(hints.get("audience_orientation") or "").strip()
        orientation = {"男频": "male", "女频": "female", "male": "male", "female": "female"}.get(
            orientation_raw, ""
        )
        language = str(ctx.get("language") or "zh-CN")
        intent_contract = (
            ctx.get("genre_intent_contract")
            if isinstance(ctx.get("genre_intent_contract"), dict)
            else {}
        )
        enhancers = (
            intent_contract.get("explicit_enhancers")
            if isinstance(intent_contract.get("explicit_enhancers"), dict)
            else {}
        )
        explicit_seed = str(hints.get("concept_seed") or "").strip()
        emit_activity(
            "methodology_selection_started",
            {
                "genre": str(ctx.get("genre") or ""),
                "orientation": orientation or "auto",
            },
        )
        methodology = await select_concept_methodology(
            session,
            settings,
            genre=str(ctx.get("genre") or ""),
            sub_genre=str(ctx.get("sub_genre") or ""),
            genre_key=str(ctx.get("genre_key") or ""),
            description=str(ctx.get("description") or ""),
            premise=str(ctx.get("premise_seed") or ctx.get("description") or ""),
            audience_orientation=orientation,
            recommended_audiences=list(ctx.get("recommended_audiences") or []),
            trend_keywords=list(ctx.get("trend_keywords") or []),
            language=language,
            allowed_modernity=(
                str(intent_contract.get("allowed_modernity") or "genre_native")
            ),
            tone_preference=str(intent_contract.get("tone_preference") or ""),
            effect_skills=list(enhancers.get("effect_skills") or []),
            cost_style=str(enhancers.get("cost_style") or "standard"),
            allow_debt=_mentions_debt_theme(explicit_seed),
            allow_death=_mentions_death_theme(explicit_seed),
        )
        methodology_payload = methodology.model_dump(mode="json")
        ctx["concept_methodology"] = methodology_payload
        ctx["concept_methodology_block"] = render_concept_methodology_block(
            methodology, language=language
        )
        emit_milestone(
            "methodology_selected",
            {
                "framework": str(
                    methodology_payload.get("framework_name")
                    or methodology_payload.get("name")
                    or methodology_payload.get("brain_hole_framework")
                    or "已选定"
                ),
                "count": len(methodology_payload.get("trend_keywords") or []),
            },
        )
    except Exception:  # pragma: no cover - never let Agent ① break conception
        logger.debug("concept methodology agent failed; continuing without it", exc_info=True)


async def _audit_cast_reality(
    session: AsyncSession,
    settings: AppSettings,
    *,
    character_proposal: dict[str, Any],
    ctx: dict[str, Any],
    is_en: bool,
    degradation_tracker: DegradationTracker | None = None,
) -> tuple[dict[str, Any], list[UUID]]:
    """职业现实审计——设定层 enforcement（2026-07-08）。

    真机终审:"32岁干了十年夜班急诊"账不平、急诊医生无理由接管住院病区抢救
    ——这类职业设定硬伤在构思层落地后污染全书,prompt 硬要求只是"劝",
    此处用一次 LLM 审计调用做"验":三道账(年龄/职级/边界)不平直接修正字段。
    Fail-open:审计调用失败或返回不可解析时原样放行,绝不阻断构思。
    """

    if not isinstance(character_proposal, dict) or not character_proposal:
        return character_proposal, []
    audit_system = (
        "You are a professional-realism auditor for fiction character sheets."
        if is_en
        else (
            "你是小说人设的职业现实审计员。读者里永远有干这行的人——"
            "职业设定的年龄/资历/职级/职权边界必须像真的一样。"
        )
    )
    audit_user = (
        f"题材：{ctx.get('genre')}（{ctx.get('sub_genre')}）\n"
        f"角色体系提案 JSON：\n{json.dumps(character_proposal, ensure_ascii=False)}\n\n"
        "对提案里每个有职业设定的角色核三道账：\n"
        "① 年龄账：年龄=入行年龄+培养年限+执业年限（临床医生=5年本科23岁毕业+3年规培,"
        "26-27岁才能独立值班,主治≥29,'32岁十年夜班'即账不平）；\n"
        "② 职级账：头衔与年限匹配；\n"
        "③ 边界账：profession_boundary 是否写清该职业的职权/场地边界,"
        "金手指与剧情前提是否要求角色越界行事而没给制度性理由。\n"
        "输出完整 JSON（与输入同结构）：全部自洽→原样返回；发现账不平→直接修正相关字段"
        "（改年龄或改资历年限,保持故事意图不变),并新增字段 reality_audit_notes 数组,"
        "每条一句话写明改了什么、为什么。只输出 JSON,不要解释。"
    )
    audited, ids = await _llm_call_json(
        session, settings,
        role="critic",
        system_prompt=audit_system,
        user_prompt=audit_user,
        fallback=json.dumps(character_proposal, ensure_ascii=False),
        template="conception_cast_reality_audit",
        stage="conception.cast_reality_audit",
        language=str(ctx.get("language") or "zh-CN"),
        degradation_tracker=degradation_tracker,
        degradation_component="cast_reality_auditor",
    )
    if not isinstance(audited, dict) or not audited.get("protagonist_archetype"):
        # 结构损坏 → fail-open 用原提案
        if degradation_tracker is not None:
            degradation_tracker.record(
                stage="conception.cast_reality_audit",
                component="cast_reality_auditor",
                reason="structure_invalid",
                severity="error",
                fallback=True,
                metadata={"returned_keys": sorted(audited) if isinstance(audited, dict) else []},
            )
        return character_proposal, ids
    return audited, ids


_REQUIRED_CONCEPTION_LANES = frozenset(
    {
        "market_strategist",
        "character_architect",
        "cast_reality_auditor",
        "world_builder",
    }
)


async def _run_required_conception_lanes(
    *,
    market_lane: Callable[[], Awaitable[Any]],
    character_lane: Callable[[], Awaitable[Any]],
    world_lane: Callable[[], Awaitable[Any]],
    fallbacks: dict[str, Any],
    tracker: DegradationTracker,
    quality_mode: str,
) -> dict[str, Any]:
    """Run required Round-1 lanes with structured fail-open/fail-closed semantics.

    ``TaskGroup`` owns sibling cancellation and waits for all child cleanup when
    the caller is cancelled. Lane exceptions are converted to fallback outcomes
    only in closure mode; strict mode blocks after all evidence is collected.
    """

    async def _capture(component: str, operation: Callable[[], Awaitable[Any]]) -> Any:
        try:
            outcome = await operation()
            payload = outcome[0] if isinstance(outcome, tuple) and outcome else outcome
            if not isinstance(payload, Mapping) or not payload:
                tracker.record(
                    stage=f"conception.{component}",
                    component=component,
                    reason="empty_result",
                    severity="critical" if quality_mode == "strict" else "error",
                    fallback=True,
                    metadata={"result_type": type(payload).__name__},
                )
                return fallbacks.get(component, outcome)
            return outcome
        except Exception as exc:
            tracker.record(
                stage=f"conception.{component}",
                component=component,
                reason="lane_error",
                severity="critical" if quality_mode == "strict" else "error",
                fallback=True,
                metadata={"error_type": type(exc).__name__, "error": str(exc)},
            )
            return fallbacks.get(component)

    tasks: dict[str, asyncio.Task[Any]] = {}
    async with asyncio.TaskGroup() as task_group:
        tasks["market_strategist"] = task_group.create_task(
            _capture("market_strategist", market_lane)
        )
        tasks["character_architect"] = task_group.create_task(
            _capture("character_architect", character_lane)
        )
        tasks["world_builder"] = task_group.create_task(
            _capture("world_builder", world_lane)
        )

    outcomes = {component: task.result() for component, task in tasks.items()}
    if quality_mode == "strict":
        blocking = tracker.blocking_events(set(_REQUIRED_CONCEPTION_LANES))
        if blocking:
            component_order = {
                "market_strategist": 0,
                "character_architect": 1,
                "cast_reality_auditor": 2,
                "world_builder": 3,
            }
            ordered = tuple(
                sorted(
                    blocking,
                    key=lambda event: (
                        component_order.get(event.component, 99),
                        event.stage,
                        event.reason,
                    ),
                )
            )
            raise ConceptionRequiredLaneError(
                ordered[0],
                blocking_events=ordered,
            )
    return outcomes


# ═══════════════════════════════════════════════════════════════════════
# 设定/逻辑框架层(2026-07-08)——"不知道在讲啥/没逻辑/没爽感"用户终审的框架修复。
#
# 根因诊断：构思是纯 LLM 自由发挥 + 一堆合规闸门(反债务/反模糊/三道账)，机制、
# 代价、数字全是为过闸门"凑"的，不是从世界规律"推"出来的——模型在做约束满足
# 求解，不是在讲故事。四把刀：
#   ① 造世前置：finalize 草稿产出后，从草稿前提差分出世界模型(公理→规律)；
#   ② 机制因果账：金手指/代价/关键数字必须能溯源到某条世界规律，溯不出的删改；
#   ③ 机制极性闸门：爽文类金手指不能只有代价没有获得(戏剧张力型豁免)；
#   ④ 跨产物事实台账：market/character/world 三个 agent 各写各的，年龄等硬事实
#     确定性核对，冲突了才用一次 LLM 精修(而非正则盲替换，防误伤无关数字)。
# 全部 fail-open：任一环节失败，原样放行，绝不阻断构思。
# ═══════════════════════════════════════════════════════════════════════


async def _derive_conception_world_model(
    session: AsyncSession,
    settings: AppSettings,
    *,
    premise: str,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], Any, list[UUID]]:
    """Compatibility shim: conception no longer invents a second world truth.

    The authoritative WorldModel is now deterministically compiled only after
    WorldSpec passes planning validation.  Returning an empty result here keeps
    older callers stable while preventing an unapproved premise-only model from
    being persisted or used to rewrite the chosen concept.
    """

    del session, settings, premise, ctx
    return {}, None, []


async def _audit_mechanism_causality(
    session: AsyncSession,
    settings: AppSettings,
    *,
    premise: str,
    writing_profile: dict[str, Any],
    world_model: Any,
    ctx: dict[str, Any],
    is_en: bool,
) -> tuple[str, dict[str, Any], list[UUID], list[str]]:
    """机制因果账审计——金手指/代价/数字必须溯源到世界规律(fail-open)。

    真机终审："抢救了一圈不知道要干什么"——机制没有因果来源，读者理所当然
    看不懂。审五问：①代价从哪条规律来 ②数字怎么推导 ③关键道具谁提供/为什么
    存在 ④暗示的社会架构是否自洽 ⑤主角凭什么知道他知道的事。
    """

    from bestseller.domain.world_model import render_world_model_prompt_block

    character = writing_profile.get("character") if isinstance(writing_profile, dict) else None
    golden_finger = str(character.get("golden_finger") or "") if isinstance(character, dict) else ""
    if world_model is None or not (premise or golden_finger):
        return premise, writing_profile, [], []

    law_block = render_world_model_prompt_block(world_model, max_laws=6)
    audit_system = (
        "You are a mechanism-causality auditor for commercial fiction."
        if is_en
        else "你是小说机制因果审计员。专治'设定读起来精致但推不出来龙去脉'。"
    )
    audit_user = (
        f"{law_block}\n\n"
        f"题材：{ctx.get('genre')}（{ctx.get('sub_genre')}）\n"
        f"当前前提：{premise}\n"
        f"当前金手指：{golden_finger}\n\n"
        "对上面的前提/金手指核五问：\n"
        "① 代价能否指向上面某条世界规律？指不出来 → 改成能指出的代价，或删除该代价细节；\n"
        "② 前提/金手指里出现的具体数字（天数/次数/年限/段数等）是否有推导依据？"
        "无依据的数字 → 删除具体数字改为定性描述，或换成能从世界规律算出的数字；\n"
        "③ 机制里出现的关键道具/信息（如某种备份、某件信物）是谁提供的、为什么存在？"
        "答不出 → 删除该道具，或补一句其世界规律来源；\n"
        "④ 若机制暗示某种社会/组织架构（发证、备案、公开知晓等），这个社会反应是否自洽？"
        "不自洽 → 收窄机制的公开范围，或补一句社会反应说明；\n"
        "⑤ 主角凭什么知道他所知道的关键信息？没有来源 → 补一条获知渠道。\n"
        "输出 JSON，只含以下字段：{\"premise\": \"...\", \"golden_finger\": \"...\", "
        "\"mechanism_causality_notes\": [\"...\"]}。全部自洽 → premise/golden_finger 原样返回、"
        "notes 为空数组；发现问题 → 直接修正 premise/golden_finger（保持故事意图不变），"
        "notes 每条一句话写明改了什么、为什么、对应哪条世界规律。只输出 JSON，不要解释。"
    )
    audited, ids = await _llm_call_json(
        session, settings,
        role="critic",
        system_prompt=audit_system,
        user_prompt=audit_user,
        fallback=json.dumps({"premise": premise, "golden_finger": golden_finger}, ensure_ascii=False),
        template="conception_mechanism_causality_audit",
        stage="conception.mechanism_causality_audit",
        language=str(ctx.get("language") or "zh-CN"),
    )
    if not isinstance(audited, dict) or not str(audited.get("premise") or "").strip():
        return premise, writing_profile, ids, []
    new_premise = str(audited.get("premise") or premise)
    new_golden_finger = str(audited.get("golden_finger") or golden_finger)
    notes = [str(n) for n in (audited.get("mechanism_causality_notes") or []) if str(n).strip()]
    new_profile = dict(writing_profile)
    if isinstance(character, dict):
        new_character = dict(character)
        new_character["golden_finger"] = new_golden_finger
        new_profile["character"] = new_character
    return new_premise, new_profile, ids, notes


# ── 机制极性闸门(Phase 3) ─────────────────────────────────────────────

_GOLDEN_FINGER_OPTOUT_ALLOWED_KEYWORDS: tuple[str, ...] = ("武侠", "历史", "权谋", "文学", "群像")
_GOLDEN_FINGER_OPTOUT_PHRASES: tuple[str, ...] = ("无显性金手指", "无金手指", "不设金手指", "没有金手指")
_MECHANISM_COST_WORDS: tuple[str, ...] = (
    "代价", "损耗", "丢失", "失去", "反噬", "透支", "消耗", "付出", "燃烧", "侵蚀", "吞噬",
)


def _detect_golden_finger_optout_violation(*, golden_finger: str, ctx: dict[str, Any]) -> str | None:
    """金手指豁免资格校验(确定性)。

    豁免"无显性金手指"仅对纯武侠/历史/权谋/文学向/群像题材开放
    （见 _GOLDEN_FINGER_DESIGN_PRINCIPLE）——提示词有"劝"没有"验"，此处补上。
    """

    if not any(p in golden_finger for p in _GOLDEN_FINGER_OPTOUT_PHRASES):
        return None
    genre_text = f"{ctx.get('genre') or ''}{ctx.get('sub_genre') or ''}"
    if any(k in genre_text for k in _GOLDEN_FINGER_OPTOUT_ALLOWED_KEYWORDS):
        return None
    return (
        "[金手指豁免资格不符] 本题材不在'纯武侠/历史/权谋/文学向/群像'白名单内，"
        "却写了'无显性金手指'——必须给出真实差异化优势"
    )


def _detect_golden_finger_polarity_violation(
    *, golden_finger: str, growth_curve: str, synopsis: str, premise: str,
) -> str | None:
    """机制极性检测(确定性)——爽文类金手指不能只有代价没有获得。

    真机终审"要爽感没爽感"：负和代价机制(只失去不获得)混进爽文/进度流通道，
    全管线没有一处度量。复用 blurb_appeal_gate 已校准的戏剧张力豁免边界
    (悬疑/怪谈/代价流本就不靠上升阶梯)：豁免边界命中就跳过，不与其冲突。
    """

    from bestseller.services.blurb_appeal_gate import (
        _embodied_emotion_categories,
        _has_progression_signal,
    )

    combined_for_exemption = f"{synopsis}\n{premise}"
    if _embodied_emotion_categories(combined_for_exemption):
        return None  # 戏剧两难型：负和代价是合理设计，非缺陷
    gf_text = f"{golden_finger}\n{growth_curve}".strip()
    if not gf_text:
        return None
    if _has_progression_signal(gf_text):
        return None
    if not any(w in gf_text for w in _MECHANISM_COST_WORDS):
        return None  # 没写代价也就没有"只有代价"的问题，不在本检查范围
    return (
        "[机制极性缺失] 金手指/成长曲线只写了代价和损耗，没有任何获得/变强/掌控信号，"
        "读者看不到上升感——爽文类需要机制整体正和（有得有失，但净值向上）"
    )


async def _polish_golden_finger_mechanism(
    session: AsyncSession,
    settings: AppSettings,
    *,
    golden_finger: str,
    growth_curve: str,
    violations: list[str],
    ctx: dict[str, Any],
    is_en: bool,
) -> tuple[str, str, list[UUID]]:
    """金手指机制聚焦重写——极性/豁免违规的一次有界修复(fail-open)。"""

    user_prompt = (
        f"题材：{ctx.get('genre')}（{ctx.get('sub_genre')}）\n"
        f"当前金手指：{golden_finger}\n当前成长曲线：{growth_curve}\n"
        f"问题：\n" + "\n".join(f"- {v}" for v in violations) + "\n\n"
        "请重写金手指与成长曲线：保留原有代价/限制设定，但必须补上明确的获得/变强/掌控"
        "信号，让读者看到机制整体是正和的（有得有失，但净值向上）；若原本套用了豁免条款"
        "却不满足豁免资格，改为给出真实差异化优势。只输出 JSON："
        '{"golden_finger": "...", "growth_curve": "...", "power_tiers": [...]}，不要解释。'
    )
    fixed, ids = await _llm_call_json(
        session, settings,
        role="planner",
        system_prompt="你是小说机制设计师,专治'读起来只有代价没有爽感'。",
        user_prompt=user_prompt,
        fallback=json.dumps(
            {"golden_finger": golden_finger, "growth_curve": growth_curve}, ensure_ascii=False
        ),
        template="conception_golden_finger_polish",
        stage="conception.golden_finger_polish",
        language=str(ctx.get("language") or "zh-CN"),
    )
    if isinstance(fixed, dict) and str(fixed.get("golden_finger") or "").strip():
        return str(fixed["golden_finger"]), str(fixed.get("growth_curve") or growth_curve), ids
    return golden_finger, growth_curve, ids


async def _adapt_hook_one_liner(
    session: AsyncSession,
    settings: AppSettings,
    *,
    one_liner: str,
    title: str,
    protagonist: str,
    premise: str,
    genre: str,
    is_en: bool,
) -> tuple[str, list[UUID]]:
    """把钩子候选池的模板句改写成含本书实体、读者三秒可懂的大白话承诺。

    一次有界改写(fail-open)：候选池选优产物是通用规则骨架，未经本书语境适配直挂
    读者承诺会产出模板插值病句(真机案例：锦鲤钩子挂到规则怪谈书上)。
    """

    lang = "英文" if is_en else "中文"
    user_prompt = (
        f"题材：{genre}\n主角：{protagonist or '（未命名）'}\n书名：{title}\n"
        f"故事核：{premise}\n\n"
        f"原钩子规则（通用模板句，可能含生造机制词、无本书实体）：{one_liner}\n\n"
        f"请把这条钩子规则改写成一句给读者看的{lang}追读承诺：必须用【本书的人物/世界"
        "名词】把规则具体化，禁止生造机制/系统黑话，≤60字，读者三秒能懂主角要面对"
        '什么。只输出 JSON：{"reader_promise": "..."}，不要解释。'
    )
    fixed, ids = await _llm_call_json(
        session, settings,
        role="editor",
        system_prompt="你是网文简介文案师，专治'钩子讲的是抽象机制,不是读者能懂的承诺'。",
        user_prompt=user_prompt,
        fallback=json.dumps({"reader_promise": one_liner}, ensure_ascii=False),
        template="conception_hook_one_liner_adapt",
        stage="conception.hook_one_liner_adapt",
        language="en" if is_en else "zh-CN",
    )
    adapted = str(fixed.get("reader_promise") or "").strip() if isinstance(fixed, dict) else ""
    return (adapted or one_liner), ids


def _hook_one_liner_is_adapted(text: str, *, protagonist: str, title: str) -> bool:
    """一句读者承诺是否"贴合本书语境"：无 fatal 病理 且 含至少一个本书实体。

    实体锚点只用主角名/书名(conception.py 此时无 world_name 变量)；两者都太短
    或缺失时不做实体检查(fail-open，不因数据缺失而误判)。
    """

    if not text.strip():
        return False
    fatal = [f for f in detect_blurb_pathology(text) if f.severity == "fatal"]
    if fatal:
        return False
    entities = [e for e in (protagonist, title) if e and len(e) >= 2]
    return not entities or any(e in text for e in entities)


# 一句话卖点的声口铁律(2026-07-17 用户终审:"钩子AI味很足,没有可读性")。
# 旧指令原文是"压缩成25-40字"——电报腔是被明确要求出来的(真机产物
# "凭闻鞋识脏,七天内将宗门把柄换筹码,从妖树口中夺命":四字生造+抽象记账+
# 三连逗号摘要,冷读者三处卡壳)。卖点是说给人听的一句话,不是压缩包。
_LOGLINE_VOICE_RULES = (
    "铁律：\n"
    "①像跟朋友安利这本书时脱口而出的那句话——自然口语句式，主谓宾完整，"
    "读出来不打磕巴；25-45字。\n"
    "②必须有一个具体的画面或反常事实（人+事+压力），让人听完想追问'然后呢？'。\n"
    # 不点名要禁的词。2026-08-03 的《雾街债主》就是这样长出来的：概念生成
    # prompt 里两条禁令分别点名了「资源债」等词，删掉后同一条 prompt 里这些词
    # 全部归零（见 test_concept_generation_prompt_names_no_motif_vocabulary）。
    # 这一条是同一次清剿的漏网——它点名「把柄/筹码/代价/博弈/记账」，而 2026-08-04
    # 的样本书 logline 里就写着「多换一枚筹码」。改成只说要什么，不说不要什么。
    "③用日常说得出口的词。四字生造压缩词要拆开说（不说'闻鞋识脏'，说'闻一下"
    "鞋底就知道谁干了脏事'）；说人在做的具体事，不用抽象名词概括利害关系；"
    "禁止逗号串三段摘要。\n"
    "④禁止生造机制黑话，不剧透结局。\n"
)


async def _derive_logline_from_champion(
    session: AsyncSession,
    settings: AppSettings,
    *,
    synopsis: str,
    spine_question: str,
    title: str,
    genre: str,
    is_en: bool,
) -> tuple[str, list[UUID]]:
    """从简介文案工序冠军定稿提炼一句 25-40 字的 logline（T6，一次有界调用）。

    保证 logline 与最终见光的简介同源，而不是各写各的。改写产物过一遍病理
    检查，不过或调用失败 → 保留调用方已有的 logline（fail-open）。
    """

    lang = "英文" if is_en else "中文"
    user_prompt = (
        f"题材：{genre}\n书名：{title}\n\n【定稿简介】\n{synopsis}\n\n"
        f"【故事追问】{spine_question}\n\n"
        f"请从这段定稿简介写一条{lang}一句话卖点(logline)。"
        f"{_LOGLINE_VOICE_RULES}"
        '只输出 JSON：{"logline": "..."}，不要解释。'
    )
    fixed, ids = await _llm_call_json(
        session, settings,
        role="editor",
        system_prompt="你是网文平台标语文案师，专精把一段简介压缩成一句抓人的卖点。",
        user_prompt=user_prompt,
        fallback=json.dumps({"logline": spine_question}, ensure_ascii=False),
        template="conception_logline_from_champion",
        stage="conception.logline_from_champion",
        language="en" if is_en else "zh-CN",
    )
    candidate = str(fixed.get("logline") or "").strip() if isinstance(fixed, dict) else ""
    if candidate and not any(
        f.severity == "fatal" for f in detect_blurb_pathology(candidate)
    ):
        return candidate, ids
    return "", ids


# ── 跨产物事实台账(Phase 4) ───────────────────────────────────────────

_CN_DIGIT_VALUES: dict[str, int] = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_AGE_MENTION_RE = re.compile(r"([〇零一二两三四五六七八九十百]{1,4}|\d{1,3})\s*岁")


def _cn_age_to_int(text: str) -> int | None:
    """把「三十二」「二十七」「十」这类中文数字(0-99)转 int；解析失败返回 None。"""

    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if any(ch not in _CN_DIGIT_VALUES and ch != "十" for ch in text):
        return None
    if text == "十":
        return 10
    if text.startswith("十"):
        ones = _CN_DIGIT_VALUES.get(text[1:])
        return 10 + ones if ones is not None else None
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGIT_VALUES.get(left)
        if tens is None:
            return None
        if not right:
            return tens * 10
        ones = _CN_DIGIT_VALUES.get(right)
        return tens * 10 + ones if ones is not None else None
    if len(text) == 1:
        return _CN_DIGIT_VALUES.get(text)
    return None


def _extract_role_tags(profession_text: str) -> list[str]:
    """profession 文本取职业词的前2字(词根)+后2字(词尾)当角色标签——粗粒度启发式。

    中文职业名词常见"词根+词尾"结构（外卖+骑手/外卖+员，急诊科+医师），
    前后各取一段以覆盖"外卖骑手"↔"外卖员"这类同根异尾变体。取首个逗号分句
    （职业名词通常打头，"27岁外卖骑手，无学历要求"的职业词在第一段），并去掉
    打头的年龄前缀。仅供跨产物年龄核对的粗筛，误配风险由下游 LLM 精修兜底
    （读上下文判断，不盲改）。
    """

    text = re.sub(r"[（(].*?[）)]", "", profession_text).strip()
    if not text:
        return []
    head = re.split(r"[，,、/]", text)[0].strip()
    head = _AGE_MENTION_RE.sub("", head, count=1).strip()
    tags: set[str] = set()
    if len(head) >= 2:
        tags.add(head[:2])
        tags.add(head[-2:])
    return [t for t in tags if t]


def _extract_cast_age_roster(character_proposal: dict[str, Any]) -> list[tuple[str, int, str]]:
    """从人设提案(经三道账审计)取 (姓名, 年龄, 职业文本) 名册。"""

    roster: list[tuple[str, int, str]] = []
    if not isinstance(character_proposal, dict):
        return roster
    p_name = str(character_proposal.get("protagonist_name") or "").strip()
    p_age = character_proposal.get("protagonist_age")
    p_profession = str(character_proposal.get("protagonist_profession") or "")
    if p_name and isinstance(p_age, (int, float)) and not isinstance(p_age, bool):
        roster.append((p_name, int(p_age), p_profession))
    for item in character_proposal.get("key_characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        age_profession = str(item.get("age_profession") or "")
        match = _AGE_MENTION_RE.search(age_profession)
        if not name or not match:
            continue
        raw = match.group(1)
        age = int(raw) if raw.isdigit() else _cn_age_to_int(raw)
        if age is not None:
            roster.append((name, age, age_profession))
    return roster


def _detect_cross_artifact_age_mismatches(
    text: str, roster: list[tuple[str, int, str]],
) -> list[str]:
    """跨产物年龄一致性核对(确定性)。

    真机终审：简介"三十二岁的外卖员" vs 人设同一角色 27 岁——market/character/
    world 三个 agent 各写各的，editor 合并没做事实核对。只核年龄(硬事实、
    正则可判)，不做语义比对(会误报)。
    """

    if not text or not roster:
        return []
    mismatches: list[str] = []
    seen: set[tuple[int, str]] = set()
    for match in _AGE_MENTION_RE.finditer(text):
        raw = match.group(1)
        mentioned_age = int(raw) if raw.isdigit() else _cn_age_to_int(raw)
        if mentioned_age is None:
            continue
        window = text[max(0, match.start() - 6) : match.end() + 14]
        for name, canon_age, profession_text in roster:
            if mentioned_age == canon_age:
                continue
            tags = _extract_role_tags(profession_text)
            if not tags or not any(tag in window for tag in tags):
                continue
            key = (match.start(), name)
            if key in seen:
                continue
            seen.add(key)
            mismatches.append(
                f"文中「…{window.strip()}…」写{mentioned_age}岁，"
                f"但人设「{name}」（{profession_text[:20]}）年龄为{canon_age}岁，两处冲突"
            )
    return mismatches


async def _reconcile_cross_artifact_facts(
    session: AsyncSession,
    settings: AppSettings,
    *,
    premise: str,
    synopsis: str,
    mismatches: list[str],
    ctx: dict[str, Any],
) -> tuple[str, str, list[UUID]]:
    """跨产物事实台账修复——检测确定性，修复交给 LLM(读上下文精改,防正则误伤)。fail-open。"""

    if not mismatches:
        return premise, synopsis, []
    user_prompt = (
        f"前提：{premise}\n简介：{synopsis}\n\n"
        "以下是与人设年龄冲突的具体位置：\n" + "\n".join(f"- {m}" for m in mismatches) + "\n\n"
        "只修正前提/简介里与人设冲突的年龄或身份数字，使其与人设一致；"
        "不改动其他任何内容、不改风格。输出 JSON："
        '{"premise": "...", "synopsis": "..."}，未冲突的字段原样返回，只输出 JSON，不要解释。'
    )
    fixed, ids = await _llm_call_json(
        session, settings,
        role="editor",
        system_prompt="你是小说编辑,只做事实一致性勘误,不做创作性改写。",
        user_prompt=user_prompt,
        fallback=json.dumps({"premise": premise, "synopsis": synopsis}, ensure_ascii=False),
        template="conception_fact_reconcile",
        stage="conception.fact_reconcile",
        language=str(ctx.get("language") or "zh-CN"),
    )
    if isinstance(fixed, dict) and str(fixed.get("premise") or "").strip() and str(fixed.get("synopsis") or "").strip():
        return str(fixed["premise"]), str(fixed["synopsis"]), ids
    return premise, synopsis, ids


async def _polish_story_spine(
    session: AsyncSession,
    settings: AppSettings,
    *,
    spine: dict[str, Any],
    violations: list[str],
    premise: str,
    synopsis: str,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], list[UUID]]:
    """故事脊柱聚焦重写——确定性验收不过时的一次有界修复(fail-open)。"""

    from bestseller.services.story_spine import SPINE_FIELDS

    user_prompt = (
        f"题材：{ctx.get('genre')}（{ctx.get('sub_genre')}）\n"
        f"前提：{premise}\n简介：{synopsis}\n"
        f"当前故事脊柱：{json.dumps(spine or {}, ensure_ascii=False)}\n"
        f"验收不通过的原因：\n" + "\n".join(f"- {v}" for v in violations) + "\n\n"
        "请重写 story_spine 六字段(who/wants/why_now/against/stakes/question)，"
        "使六字段连读成一段60字左右、讲给朋友能复述的人话；wants 必须具体可验收；"
        "question 是一句疑问句。只输出 JSON 对象(六个键)，不要解释。"
    )
    fixed, ids = await _llm_call_json(
        session, settings,
        role="planner",
        system_prompt="你是故事策划,专治'不知道这书在讲啥'。",
        user_prompt=user_prompt,
        fallback=json.dumps(spine or {}, ensure_ascii=False),
        template="conception_story_spine_polish",
        stage="conception.story_spine_polish",
        language=str(ctx.get("language") or "zh-CN"),
    )
    if isinstance(fixed, dict) and all(k in fixed for k in SPINE_FIELDS):
        return fixed, ids
    return spine or {}, ids


async def _reconcile_concept_seed_with_final_premise(
    session: AsyncSession,
    settings: AppSettings,
    *,
    winner: object,
    premise: str,
    synopsis: str,
    writing_profile: Mapping[str, Any],
    authoritative_name: str,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], list[UUID]]:
    """Rebuild a tournament seed when later audits materially changed the book.

    Causality and market audits are allowed to improve the final premise.  They
    are not allowed to leave HookCard/SerialityProof pointing at a different
    protagonist or mechanism.  A focused LLM call realigns the complete seed;
    the deterministic concept-contract builder still owns hashes and capacity
    validation afterwards.
    """

    if isinstance(winner, Mapping):
        original = dict(winner)
    else:
        to_dict = getattr(winner, "to_dict", None)
        original_payload = to_dict() if callable(to_dict) else {}
        original = dict(original_payload) if isinstance(original_payload, Mapping) else {}
    schema = {
        "concept": "一句话读者承诺",
        "mechanism": "可持续产生剧情的核心机制",
        "hook_question": "读者追问",
        "protagonist_identity": f"必须明确包含姓名 {authoritative_name}",
        "protagonist_private_desire": "具体、可验收的私人目标",
        "protagonist_flaw": "会制造选择代价的缺陷",
        "core_abnormality": "题材内成立的异常/能力",
        "opening_crisis": "开篇立刻发生的危机",
        "opponent_system": "持续反制主角的对手系统",
        "decision_proof": "为何必须行动且不能安全退出",
        "emotional_promise": "读者持续获得的情绪体验",
        "repeatable_story_unit": "每若干章可换内容重复一次的故事单元",
        "unit_families": ["至少三类不同单元"],
        "unit_frequency": "例如每2-4章一次",
        "unit_count_estimate": 5,
        "renewal_sources": ["至少两个新冲突来源"],
        "accumulation_tracks": ["至少两个不可逆累积轨道"],
        "phase_transitions": ["按目标篇幅给出阶段变化"],
        "opposing_ecology": ["至少两个会主动反应的对手/阵营"],
        "question_ladder": ["至少三个逐层升级的问题"],
        "endgame_direction": "终局方向",
    }
    premise_surface = f"{premise}\n{synopsis}"
    infant_body_contract = ""
    if any(marker in premise_surface for marker in ("婴儿", "襁褓", "新生儿", "三个月")):
        infant_body_contract = (
            "\n本书含成年意识进入婴儿身体：前世记忆、判断和内心策略属于合法知识来源，"
            "但感知距离、发声、抓握、翻身、注视控制和精细动作必须服从真实婴儿身体。"
            "不得把精确假哭节拍、注视时长/方向、抓握力度或呼吸深浅写成可稳定操控成人、"
            "决定军政表态、暴露/处死旧臣的遥控器。重大转折必须由有独立动机的成人角色"
            "或外部事件触发；婴儿只能观察、记忆、被动伪装、做粗粒度且可能失败的反应，"
            "最多改变结果的边际程度。前三章的正向兑现也必须由外部因果链完成，不能靠婴儿"
            "突然具备精细运动或语言能力。删除为微动作强行发明的术语体系。"
            "即使加上‘粗粒度/不能精准遥控’免责声明，也不得继续把婴啼、注视、抓握、"
            "呼吸组成暗号、竞价或可升级的机制；mechanism、repeatable_story_unit、"
            "core_abnormality 必须改由成人阵营的自主行动和外部事件发动。推荐循环："
            "成人阵营因各自利益行动→被抱在场的婴儿被动获得信息碎片→婴儿只做不暴露"
            "成年意识的保命伪装→成人因自身目标作出后续决定。任何旧臣的调离、暴露或"
            "死亡都必须有成人调查/军政利益的独立因果，不能由婴儿对望、哭声或抓握触发。"
        )

    fixed, ids = await _llm_call_json(
        session,
        settings,
        role="planner",
        system_prompt=(
            "你是长篇小说构思契约主编。最终 premise 是唯一事实源。"
            "发现旧候选与最终 premise 冲突时，要重建一份完整、单一故事的冠军种子，"
            "不能混用旧主角、旧能力、旧道具或旧世界规则。只输出 JSON。"
        ),
        user_prompt=(
            f"题材：{ctx.get('genre')}（{ctx.get('sub_genre')}）\n"
            f"目标章节：{ctx.get('chapter_count') or ctx.get('target_chapters')}\n"
            f"最终 premise：{premise}\n"
            f"最终 synopsis：{synopsis}\n"
            "最终 writing_profile："
            f"{json.dumps(dict(writing_profile), ensure_ascii=False)[:12000]}\n"
            "旧冠军种子（仅用于识别并删除漂移，不是事实源）："
            f"{json.dumps(original, ensure_ascii=False)[:12000]}\n\n"
            f"严格按此 schema 返回全部字段：{json.dumps(schema, ensure_ascii=False)}\n"
            f"protagonist_identity 必须包含姓名“{authoritative_name}”；"
            "所有机制、道具、阵营和问题梯度必须能从最终 premise/synopsis 推导。"
            f"{infant_body_contract}"
        ),
        fallback=json.dumps(original, ensure_ascii=False),
        template="conception_concept_seed_final_premise_reconciliation",
        stage="conception.concept_seed_final_premise_reconciliation",
        language=str(ctx.get("language") or "zh-CN"),
    )
    if not isinstance(fixed, Mapping) or any(key not in fixed for key in schema):
        return original, ids
    merged = {**original, **fixed}
    return merged, ids


async def _judge_explicit_concept_seed_fidelity(
    session: AsyncSession,
    settings: AppSettings,
    *,
    concept_seed: str,
    final_result: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> tuple[dict[str, Any], list[UUID]]:
    """Use an LLM adjudicator to prove that a user seed survived conception.

    Prompt presence alone is not evidence that an input took effect.  This gate
    compares the user's explicit facts with the materialized premise, synopsis,
    profile, and story spine.  Genre-fitting elaboration is allowed; replacing
    the protagonist, core mechanism, deadline, objective, or stated cost is not.
    """

    fallback = {
        "passed": True,
        "evaluator_error": "judge_unavailable_fail_open",
        "hard_conflicts": [],
        "preserved_facts": [],
        "repair_directives": [],
    }
    payload, ids = await _llm_call_json(
        session,
        settings,
        role="critic",
        system_prompt=(
            "你是小说创建意图保真判官。只判断用户明确写出的事实是否在终稿构思中保留。"
            "允许补充人物、世界与商业包装，但不得替换主角身份/姓名、核心能力或机制、"
            "明确时限、具体目标、数额、代价和结局承诺。不要用题材相似替代事实一致。"
            "只输出 JSON。"
        ),
        user_prompt=(
            f"用户明确故事创意（唯一上位事实源）：\n{concept_seed}\n\n"
            "构思终稿：\n"
            f"{json.dumps(dict(final_result), ensure_ascii=False)[:30000]}\n\n"
            f"题材上下文：{json.dumps(dict(ctx), ensure_ascii=False)[:5000]}\n\n"
            "返回 schema：{\"passed\": true, \"hard_conflicts\": "
            "[{\"classification\":\"hard_conflict\",\"fact\":\"用户事实\","
            "\"generated\":\"终稿冲突事实\",\"reason\":\"为何属于替换而非扩写\"}], "
            "\"compatible_extensions\":[{\"classification\":\"compatible_extension\","
            "\"fact\":\"新增细节\",\"reason\":\"为何不替换上位事实\"}], "
            "\"preserved_facts\":[], \"repair_directives\":[]}。"
            "hard_conflicts 只能放真正替换/删除/反转用户事实的项目；凡结论含"
            "‘合理扩写’‘不构成替换’‘并未替换’的项目必须放 compatible_extensions。"
            "只要有一项硬冲突，passed 必须为 false；没有硬冲突必须为 true。"
        ),
        fallback=json.dumps(fallback, ensure_ascii=False),
        template="conception_explicit_seed_fidelity_judge",
        stage="conception.explicit_seed_fidelity_judge",
        language=str(ctx.get("language") or "zh-CN"),
    )
    report = dict(payload) if isinstance(payload, Mapping) else dict(fallback)
    report["passed"] = bool(report.get("passed"))
    hard_conflicts: list[dict[str, Any]] = []
    compatible_extensions = [
        dict(item)
        for item in report.get("compatible_extensions", [])
        if isinstance(item, Mapping)
    ]
    for raw_item in report.get("hard_conflicts", []):
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        classification = str(item.get("classification") or "").strip().lower()
        if classification in {"preserved", "compatible_extension", "extension"}:
            compatible_extensions.append(item)
            continue
        # Older/less disciplined judges occasionally put an explanation that
        # explicitly says "not a replacement" into hard_conflicts.  Treat that
        # as an internally labelled compatible extension, not as a semantic
        # verdict from a regex.  The LLM still owns every substantive finding.
        conclusion = str(item.get("reason") or "")
        if any(
            marker in conclusion
            for marker in ("不构成替换", "并未替换", "合理扩写", "符合扩写")
        ):
            item["classification"] = "compatible_extension"
            compatible_extensions.append(item)
            continue
        item["classification"] = "hard_conflict"
        hard_conflicts.append(item)
    report["hard_conflicts"] = hard_conflicts
    report["compatible_extensions"] = compatible_extensions
    report["repair_directives"] = [
        str(item).strip() for item in report.get("repair_directives", []) if str(item).strip()
    ][:8]

    # Hybrid guard: the LLM owns semantic adjudication, while an explicit
    # protagonist name is also a cheap invariant.  This prevents a permissive
    # judge response from approving a visibly different book.
    try:
        from bestseller.services.book_design import extract_creation_protagonist_name

        seed_name = extract_creation_protagonist_name({"premise": concept_seed})
    except Exception:
        seed_name = ""
    generated_surface = json.dumps(dict(final_result), ensure_ascii=False)
    if seed_name and seed_name not in generated_surface:
        report["passed"] = False
        report["hard_conflicts"].append(
            {
                "source": "deterministic_invariant",
                "fact": f"主角姓名：{seed_name}",
                "generated": "构思终稿未保留该姓名",
                "reason": "用户显式主角身份被替换",
            }
        )
        report["repair_directives"].insert(
            0, f"全量恢复主角姓名与身份“{seed_name}”，删除替代主角。"
        )
    elif not report["hard_conflicts"]:
        report["passed"] = True
    return report, ids


async def _arbitrate_explicit_seed_fidelity_report(
    session: AsyncSession,
    settings: AppSettings,
    *,
    concept_seed: str,
    final_result: Mapping[str, Any],
    challenged_report: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> tuple[dict[str, Any], list[UUID]]:
    """Second-model semantic appeal for a disputed fidelity rejection.

    The first judge sometimes calls a generated constraint a "replacement"
    solely because the user did not specify that degree of freedom.  That is a
    logical category error, not something a growing regex vocabulary can solve.
    This independent appeal asks whether both statements can be true at once.
    Deterministic invariants (for example a missing explicit protagonist name)
    are intentionally outside the appeal surface.
    """

    fallback = dict(challenged_report)
    payload, ids = await _llm_call_json(
        session,
        settings,
        role="critic",
        system_prompt=(
            "你是小说创建意图保真复核仲裁官。复核初审提出的硬冲突是否真的构成逻辑矛盾。"
            "判定标准只有一个：终稿事实与用户明确原文能否同时为真。用户没有说明、没有限定、"
            "没有禁止的细节，只要不改变其明确事实、目标、时限与代价，就属于兼容扩写；"
            "不得因为新增了更具体的执行顺序、场景条件或边界而判成替换。只有不同主角、改变"
            "明确时长/数额、删除或反转目标、用另一套能力或代价取代原设定，才是硬冲突。"
            "只输出 JSON。"
        ),
        user_prompt=(
            f"用户明确故事创意（唯一上位事实源）：\n{concept_seed}\n\n"
            "待复核构思终稿：\n"
            f"{json.dumps(dict(final_result), ensure_ascii=False)[:30000]}\n\n"
            "初审报告（允许推翻）：\n"
            f"{json.dumps(dict(challenged_report), ensure_ascii=False)[:12000]}\n\n"
            f"题材上下文：{json.dumps(dict(ctx), ensure_ascii=False)[:4000]}\n\n"
            "返回 schema：{\"passed\":true,\"hard_conflicts\":[],"
            "\"compatible_extensions\":[],\"preserved_facts\":[],"
            "\"repair_directives\":[],\"arbitration_reason\":\"...\"}。"
            "若初审理由依赖‘用户未限定/未禁止/未规定’，且两者能同时为真，必须改判为"
            "compatible_extension。passed 必须与 hard_conflicts 是否为空严格一致。"
        ),
        fallback=json.dumps(fallback, ensure_ascii=False),
        template="conception_explicit_seed_fidelity_arbitration",
        stage="conception.explicit_seed_fidelity_arbitration",
        language=str(ctx.get("language") or "zh-CN"),
    )
    report = dict(payload) if isinstance(payload, Mapping) else fallback
    report["hard_conflicts"] = [
        dict(item)
        for item in report.get("hard_conflicts", [])
        if isinstance(item, Mapping)
    ]
    report["compatible_extensions"] = [
        dict(item)
        for item in report.get("compatible_extensions", [])
        if isinstance(item, Mapping)
    ]
    report["repair_directives"] = [
        str(item).strip()
        for item in report.get("repair_directives", [])
        if str(item).strip()
    ][:8]
    report["passed"] = not bool(report["hard_conflicts"])
    return report, ids


async def _repair_final_result_to_explicit_seed(
    session: AsyncSession,
    settings: AppSettings,
    *,
    concept_seed: str,
    final_result: Mapping[str, Any],
    report: Mapping[str, Any],
    ctx: Mapping[str, Any],
    attempt: int = 1,
    max_attempts: int = 3,
) -> tuple[dict[str, Any], list[UUID]]:
    """Regenerate from the authoritative seed without replaying the bad draft."""

    conservative_lock = ""
    if attempt >= max_attempts:
        conservative_lock = (
            "\n这是最后一次保守锁定修复：premise 必须直接包含用户原文；"
            "所有机制、边界、时限、目标、数额和代价都只能直接保留用户已经写明的事实。"
            "新增内容只限人物当下行动、现存关系、场景阻力和题材原生环境，不再补充新的机制事实。"
        )

    # A repair prompt is another generation prompt. Replaying the rejected
    # draft, free-form judge report, or the entire mutable ctx taught the model
    # the exact foreign story it was supposed to remove. Pass only frozen
    # creation controls and conflict counts; the evaluator keeps the detailed
    # evidence out-of-band for logging and the next deterministic re-check.
    context_projection = {
        key: ctx.get(key)
        for key in (
            "genre",
            "sub_genre",
            "chapter_count",
            "language",
            "genre_intent_contract",
        )
        if ctx.get(key) not in (None, "", {}, [])
    }
    conflict_count = len(
        [item for item in report.get("hard_conflicts", []) if isinstance(item, Mapping)]
    )

    repaired, ids = await _llm_call_json(
        session,
        settings,
        role="editor",
        system_prompt=(
            "你是小说创建意图修复主编。用户明确故事创意是不可替换的上位事实。"
            "请从该原文重新建立同一本书的完整构思终稿。主角身份、核心机制、时限、"
            "目标、数额、代价与因果必须整体一致；任何新增事实只能由原文或题材原生规律"
            "直接推出。只输出 JSON。"
        ),
        user_prompt=(
            f"修复轮次：{attempt}/{max_attempts}\n"
            f"用户明确故事创意：\n{concept_seed}\n\n"
            f"本轮需闭合的事实冲突数：{conflict_count}\n"
            f"冻结建书上下文：{json.dumps(context_projection, ensure_ascii=False)[:8000]}\n"
            "被拒终稿和判官自由文本已隔离，不得猜测或复述它们。\n"
            "输出完整终稿 JSON，至少保留 title、premise、synopsis、tags、"
            "writing_profile、story_spine 字段。"
            f"{conservative_lock}"
        ),
        fallback=json.dumps(dict(final_result), ensure_ascii=False),
        template="conception_explicit_seed_fidelity_repair",
        stage="conception.explicit_seed_fidelity_repair",
        language=str(ctx.get("language") or "zh-CN"),
    )
    return (dict(repaired) if isinstance(repaired, Mapping) else dict(final_result)), ids


# 同源补强指令写着「保留其故事身份，只修被判不达标的轴」，所以它只能修执行层。
# 这两轴度量的是**点子本身**：新颖度问「这个故事是不是已经被写烂了」，可预测性
# 问「读者是不是已经猜到走向」。保留故事身份 = 保留问题，指令自相矛盾。
#
# 真机取证（2026-07-26「东方玄幻」空题材建书）：两轮共 6 个候选**全是同一个故事**
# （杂役掏沟挖出戴木镯的腕骨＝失踪师姐遗骸），6/6 挂新颖度。根因就在下面的排序：
# 「挂的轴越少越优先」使「只挂新颖度」的候选成为**最优先**种子，而那正是改良
# 修不好的那一个——第 2 轮于是结构性必败。
_REFINEMENT_RESISTANT_AXES: frozenset[str] = frozenset(
    {"新颖度", "可预测性", "题材保真"}
)

# 无种子建书时的采样量。淘汰赛有七个硬门且必须同时达标，真机实测（2026-07-29
# 空题材玄幻，10 候选）单候选通过率约 10%：人物决策/机制因果稳定 7~8 全过，
# 新颖度分布 3~6、中位数 4.5 而地板 6.0——它是主要杀手。默认 6 个候选的全灭率
# 是 0.9⁶≈53%，宽到 16 个降到 18%。
_SEEDLESS_CANDIDATE_COUNT: int = 16


def _tournament_attempt_candidate_count(
    *,
    attempt: int,
    baseline: int,
    has_seed: bool,
    refining: bool,
) -> int:
    """How many candidates this attempt should draw.

    The count used to be pinned to the attempt number: ``2`` for anything past
    the first. That is right for 定向补强 — polishing one near-miss does not
    need six variants — but 2026-07-28 changed the retry so a novelty failure
    no longer seeds it, making the retry a fresh re-roll. A re-roll needs a wide
    sample, and it was left with the polishing budget: P(dry) went from 53% on
    the first attempt to 81% on the second, so the retry was *less* likely to
    succeed than the try it was rescuing. The two fixes cancelled each other.

    So the sample follows what the attempt is *doing*, not which attempt it is.
    A run with no user idea also leans entirely on drawing a lucky candidate,
    so its first attempt is widened too — bounded, because the point is to
    clear the gates, not to spend without limit.
    """

    base = max(1, int(baseline or 0) or 6)
    if refining and has_seed:
        return 2
    if has_seed:
        return base
    return max(base, _SEEDLESS_CANDIDATE_COUNT)


def _best_dry_tournament_seed(
    candidates: list[Any],
    *,
    allow_debt: bool = False,
    allow_death: bool = False,
) -> str:
    """Pick the best near-miss from a dry tournament to seed the retry attempt.

    Three consecutive real dry runs (2026-07-16) exposed the starvation paradox:
    a candidate one axis short of the 8-floor gauntlet loses to NOTHING —
    conception falls back to its vanilla concept, weaker than any judged
    near-miss, and dies at the logline gate. The tournament already has the
    designed machinery for refinement (``seed_concept`` → 同源补强 directives);
    this selector feeds it. Only floor-rejected candidates qualify — a
    deterministic KO (俗套/种子审计) is dead by policy, and a candidate that
    failed 4+ axes is not a near-miss worth refining.

    A candidate is also disqualified when any failed axis is refinement
    resistant: 同源补强 cannot repair the premise it is told to preserve.
    Returning "" there is not a loss — the caller simply omits the
    identity-preserving directive, so the retry re-rolls freely while
    ``retry_feedback`` still shows it what failed and why.
    """

    _FLOOR_PREFIX = "钩子硬门失败"
    best: tuple[int, float, str] | None = None
    story_fields = (
        "concept",
        "mechanism",
        "hook_question",
        "protagonist_identity",
        "protagonist_private_desire",
        "protagonist_flaw",
        "core_abnormality",
        "opening_crisis",
        "opponent_system",
        "decision_proof",
        "emotional_promise",
    )
    for candidate in candidates or []:
        rejected = str(getattr(candidate, "rejected_reason", "") or "")
        if not rejected.startswith(_FLOOR_PREFIX):
            continue
        failed_axes = [a for a in rejected.split(":", 1)[-1].split("/") if a.strip()]
        if len(failed_axes) > 3:
            continue
        if any(axis.strip() in _REFINEMENT_RESISTANT_AXES for axis in failed_axes):
            continue
        concept = str(getattr(candidate, "concept", "") or "").strip()
        if not concept:
            continue
        story_text = "\n".join(
            str(getattr(candidate, field_name, "") or "")
            for field_name in story_fields
        )
        # (2026-08-02) The motif filter here was retired with the rest. A
        # near-miss candidate is refined by the next round on its own merits;
        # its vocabulary is not grounds for throwing the whole seed away.
        del story_text
        score_sum = sum(
            float(getattr(candidate, f"judge_{axis}", 0.0) or 0.0)
            for axis in (
                "freshness", "click", "character_logic", "mechanism_causality",
                "genre_fidelity", "plain_language", "story_motion",
            )
        )
        key = (-len(failed_axes), score_sum, concept)
        if best is None or key[:2] > (best[0], best[1]):
            best = (key[0], key[1], concept)
    return best[2] if best else ""


async def _logline_regen_rescue(
    *,
    verdict: Any,
    logline: str,
    max_attempts: int = 2,
    rewrite_fn: Any,
    judge_fn: Any,
) -> tuple[Any, str, int]:
    """Consume a REGENERATE logline verdict with bounded focused rewrites.

    The gate defines regenerate as "偏弱但可修 → 回炉重写卖点（有界）" and writes
    ``fix_directives`` as rewrite instructions — but until 2026-07-16 conception
    treated every non-EXPAND verdict as instant task death, so the directives
    were never consumed (real run: overall 4.38, verdict regenerate → the user
    saw "cannot create projects"). This loop is that missing consumer.

    Semantics:
    - repairable = ``regenerate`` OR ``reject`` (changed 2026-08-10). Reject used
      to mean "fatal by definition, zero attempts", so a rejected pitch killed
      the book without a single repair try — while the very same verdict carried
      ``fix_directives`` written specifically to be applied. Live case: 《搓背》
      died at overall 3.2 with two directives generated and never consumed.
      A gate that produces repair instructions and then refuses to run them is
      not a hard gate, it is a dropped loop.
    - keep-best: the best-overall (verdict, logline) pair seen wins, so a
      failed rescue can never ship a worse pitch than the one it started with.
    - fail-closed: any rewrite/judge error returns the best verdict so far, and
      an exhausted budget still blocks — repair earns attempts, not a pass.
    """

    from bestseller.services.logline_gate import LoglineAction

    repairable = (LoglineAction.REGENERATE, LoglineAction.REJECT)
    best_verdict, best_logline = verdict, logline
    attempts = 0
    if getattr(verdict, "action", None) not in repairable:
        return best_verdict, best_logline, attempts

    current_verdict, current_logline = verdict, logline
    for _ in range(max(0, int(max_attempts))):
        if getattr(current_verdict, "action", None) not in repairable:
            break
        attempts += 1
        try:
            rewritten = str(await rewrite_fn(current_logline, current_verdict) or "").strip()
            if not rewritten:
                break
            # 确定性护栏（2026-08-24《废丹成神》定罪）：改写者会把整改方向
            # 写成 400 字答辩文；prompt 已加硬上限，这里再兜一层——超长产物
            # 不判不采纳，保住 keep-best 语义（一句卖点不可能需要 150+ 字）。
            if len(rewritten) > 150:
                logger.warning(
                    "logline rescue rewrite over length cap (%d chars); discarded",
                    len(rewritten),
                )
                break
            current_verdict = await judge_fn(rewritten)
            current_logline = rewritten
        except Exception:
            logger.warning(
                "logline regen rescue attempt %d failed; keeping best verdict",
                attempts,
                exc_info=True,
            )
            break
        if float(getattr(current_verdict, "overall", 0.0) or 0.0) > float(
            getattr(best_verdict, "overall", 0.0) or 0.0
        ):
            best_verdict, best_logline = current_verdict, current_logline
        if getattr(current_verdict, "action", None) is LoglineAction.EXPAND:
            return current_verdict, current_logline, attempts
    return best_verdict, best_logline, attempts


async def _rewrite_logline_for_gate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    logline: str,
    verdict: Any,
    premise: str,
    synopsis: str,
    genre: str,
    sub_genre: str | None,
    is_en: bool,
) -> str:
    """Focused logline rewrite per the gate's own fix directives.

    Grounded in premise/synopsis so the rewrite surfaces the story's REAL
    irreversibility and escalation instead of inventing facts the book does not
    contain — a flat story must still fail the re-judge honestly.
    """

    reasons = "\n".join(getattr(verdict, "reasons", ()) or ())
    directives = "\n".join(getattr(verdict, "fix_directives", ()) or ())
    if is_en:
        system_prompt = (
            "You are a veteran web-novel acquisitions editor. Rewrite the given "
            "one-sentence logline per the fix notes. Stay strictly faithful to the "
            "premise/synopsis facts — do NOT invent new plot. Output ONLY the "
            "rewritten one-sentence logline."
        )
        user_prompt = (
            f"Genre: {genre} ({sub_genre or ''})\n\n[Current logline]\n{logline}\n\n"
            f"[Why it failed]\n{reasons}\n\n[Fix notes]\n{directives}\n\n"
            f"[Premise — ground truth]\n{premise}\n\n[Synopsis — ground truth]\n{synopsis}\n\n"
            "Make the protagonist's irreversible action, the escalating problem "
            "chain and the opposition's counter-move visible inside the single "
            "sentence. Hard cap: 120 characters. Fix the sentence by choosing "
            "better story facts — never by writing an argument against the fix "
            "notes into the sentence (no enumerating why every alternative "
            "fails). Output only the sentence."
        )
    else:
        system_prompt = (
            "你是网文平台资深主编。下面这条一句话故事大纲没过审。请按【整改方向】重写这一句话。"
            "铁律：以【前提/简介】为事实准绳，只许把书里已有的不可逆行动、递进问题链、对手反制"
            "在这一句里写显，【不得】发明书里没有的新设定新剧情。"
            # 反答辩令（2026-08-24 真机《废丹成神》定罪）：改写者拿着整改方向
            # 把「每条退路为什么走不通」逐条写进句子，产出 401 字答辩文顶着
            # logline 的名牌流进 metadata。整改落在事实选取，不落在论证。
            "整改的方式是换更有力的故事事实，不是把论证写进句子——禁止在句中"
            "逐条列举退路/选项并反驳；这是一句卖点，不是答辩状。"
            "硬上限 90 字，超出即废。只输出重写后的一句话，不要解释。"
        )
        user_prompt = (
            f"题材：{genre}（{sub_genre or ''}）\n\n【当前一句话大纲】\n{logline}\n\n"
            f"【没过审的原因】\n{reasons}\n\n【整改方向】\n{directives}\n\n"
            f"【前提 · 事实准绳】\n{premise}\n\n【简介 · 事实准绳】\n{synopsis}\n\n"
            "只输出重写后的一句话大纲。"
        )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=logline,
            prompt_template="conception_logline_regen",
            prompt_version="v1",
            max_tokens_override=400,
        ),
    )
    return (completion.content or logline).strip()


async def _polish_blurb_synopsis(
    session: AsyncSession,
    settings: AppSettings,
    *,
    synopsis: str,
    feedback: str,
    genre: str,
    sub_genre: str,
    is_en: bool,
    language: str,
    platform: str | None = None,
) -> tuple[str, UUID | None]:
    """Focused click-blurb rewrite — rewrite ONLY the synopsis per gate feedback.

    Far more effective than re-running the full finalize (which juggles premise/
    profile and under-optimizes the blurb: a real-pipeline 现实 book plateaued at
    74.7 via full-finalize regen, while focused rewrite reaches ~84). Fail-open:
    returns the original synopsis on any error. Returns (synopsis, llm_run_id).
    """

    if is_en:
        system_prompt = (
            "You are a veteran web-novel platform editor. Rewrite the given blurb into "
            "a CLICK-optimized book blurb following the fix notes. Output ONLY the "
            "rewritten blurb text — no explanation, no title."
        )
        user_prompt = (
            f"Genre: {genre} ({sub_genre})\n\n[Current blurb]\n{synopsis}\n\n"
            f"[Fix notes]\n{feedback}\n\nHard rules: 60-120 words; first sentence is a "
            "punchy hook; identity+conflict+stakes present; front-load high-arousal "
            "emotion; end on suspense without spoilers; no AI cliches. Write for a brand-new "
            "reader who knows none of the world's invented terms: drop or immediately gloss every "
            "coined mechanic/system/grade-code term; at most one proper noun per paragraph. "
            "Output only the blurb."
        )
    else:
        # (2026-08-01 product ruling) persona table + emotion exemplar list
        # deleted from this repair prompt — framework-authored reader sheets
        # and event menus steer every same-genre book toward the same content.
        from bestseller.services.blurb_appeal_gate import platform_blurb_band
        from bestseller.services.blurb_copywriter import render_blurb_form_reminder

        # 与验收闸门同源的平台字数带（旧版硬编码 80-140 与起点 140-220 打架）。
        _band_min, _band_max = platform_blurb_band(platform)
        # 形态规则只从单一来源拼入（2026-08-18 定罪）：本路径旧版自带一套
        # 与 copywriter 百本榜单实抓形态（标签行/分行/陈述收尾/仅6%问句）
        # 正面互斥的旧规——冠军先按榜单形态产出、再被本打磨改回单段问句
        # 收尾，见光的简介两头不沾。详见 render_blurb_form_reminder docstring。
        system_prompt = (
            "你是网文平台资深编辑。把给定简介按【整改要求】改到位（番茄/起点详情页文案）。"
            "只输出重写后的简介正文，不要解释、不要标题。"
        )
        user_prompt = (
            f"题材：{genre}（{sub_genre}）\n"
            f"【当前简介】\n{synopsis}\n\n【整改要求】\n{feedback}\n\n"
            f"{render_blurb_form_reminder(lo=_band_min, hi=_band_max)}\n"
            "其余硬性：卖点三要素齐（身份+冲突+代价）；"
            "高唤起情绪前置——从本书自己的前提与冲突里选最强的情绪事件，别套其他题材的情绪词；"
            "不剧透结局；"
            "【新读者可懂铁律】当成写给完全不懂本书设定的陌生人:删掉生造黑话/自定义机制名/系统术语/"
            "等级编号(如灵码编辑器/怪谈词条/S级/#0371/数据化修炼),独特概念要么不出现要么紧跟一句大白话"
            "点破,一段最多留1个专名;读完能一句话说出主角是谁、要干嘛、爽点在哪。只输出简介正文。"
        )
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=synopsis,
                prompt_template="conception_blurb_polish",
                prompt_version="v1",
                metadata={"language": language},
                max_tokens_override=700,
            ),
        )
        return (completion.content or synopsis).strip(), completion.llm_run_id
    except Exception:
        logger.warning("blurb polish failed; keeping prior synopsis", exc_info=True)
        return synopsis, None


async def _persona_click_advisory(
    session: AsyncSession,
    settings: AppSettings,
    *,
    title: str,
    synopsis: str,
    genre: str,
    sub_genre: str | None,
    tags: list[str] | None,
    config: dict[str, Any] | None = None,
    judge: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """画像点击判官 advisory（审计 P1-6 接活）：模拟目标读者 3 秒点不点。

    返回 ``(report_dict | None, 重生反馈行)``。达 advisory 线（或判官不可用，
    fail-open）→ 反馈为空串；「不点」→ 反馈带划走原因，供重生循环回灌简介重写。
    永不 raise。
    """

    try:
        from bestseller.services.persona_click_judge import (
            load_persona_judge_config,
            run_persona_click_judge,
        )

        pj_cfg = load_persona_judge_config(config)
        if not pj_cfg["enabled"]:
            return None, ""
        report = await run_persona_click_judge(
            session, settings,
            title=title, synopsis=synopsis, genre=genre, sub_genre=sub_genre,
            tags=tuple(tags or ()), config=config, judge=judge,
        )
        report_dict = report.to_dict()
        report_dict["click_rate_min"] = pj_cfg["click_rate_min"]
        report_dict["advisory_pass"] = report.advisory_pass(pj_cfg["click_rate_min"])
        if report_dict["advisory_pass"]:
            return report_dict, ""
        reasons = "；".join(report.reasons[:3])
        feedback = (
            f"【模拟读者不点（{report.channel}画像，{report.clicks}/{report.samples} 会点，"
            f"均分 {report.avg_score:.1f}/10）】划走原因：{reasons}。"
            "必须按这些原因改简介：大白话、该人群的爽点直给、零生造黑话。"
        )
        return report_dict, feedback
    except Exception:
        logger.warning("persona click advisory failed (non-fatal)", exc_info=True)
        return None, ""


async def _repair_canon_contradictions(
    session: AsyncSession,
    settings: AppSettings,
    *,
    premise: str,
    spine: dict[str, Any] | None,
    synopsis: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """finalize 正典自洽：验出 premise↔spine 硬矛盾 → 一次有界最小修复 → 复验。

    毒正典的产地就是 finalize（真机：premise 35 岁 vs spine「三十年厨房功夫」、
    why_now 一格双期限）。自闭环 5 轮证明下游救不回——文案照抄毒、候选全被筛掉、
    回退的 v0 同病。修复必须发生在产地。

    协议：只统一数字/期限/事实（最小改动），禁止重写措辞、禁止新增内容；
    修复稿必须**复验通过且矛盾数下降**才采纳，否则原样返回（fail-open）。
    返回 ``(premise, spine, report_dict|None)``。永不 raise。
    """

    try:
        from bestseller.services.blurb_coherence_judge import (
            verify_blurb_coherence,
        )
        from bestseller.services.concept_fact_consistency import (
            detect_numeric_fact_conflicts,
        )

        def _det_conflicts(p: str, s: dict[str, Any] | None) -> list[Any]:
            fields: dict[str, str] = {"premise": p, "synopsis": synopsis}
            for k, v in (s or {}).items():
                if isinstance(v, str):
                    fields[f"spine.{k}"] = v
            return detect_numeric_fact_conflicts(fields)

        report = await verify_blurb_coherence(
            session, settings, synopsis=synopsis, premise=premise, spine=spine,
        )
        canon = report.canon_findings
        # 确定性数字事实对表（2026-08-18：七代vs十八代同字段并存直通成稿）
        # ——LLM 判官漏的这类冲突纯规则可判，作为额外 findings 一并进修复轮。
        det_before = _det_conflicts(premise, spine)
        if not canon and not det_before:
            return premise, spine, (report.to_dict() if report.llm_used else None)

        findings_text = "\n".join(
            f"- 「{f.quote_a}」与「{f.quote_b}」不能同时成立（{f.explanation}）"
            for f in list(canon[:4]) + det_before[:4]
        )
        spine_json = json.dumps(spine or {}, ensure_ascii=False)
        repair_user = (
            f"【前提】\n{premise}\n\n【故事脊柱】\n{spine_json}\n\n"
            f"【已核实的硬矛盾（引文均为原文）】\n{findings_text}\n\n"
            "请做**最小修复**：对每条矛盾，二选一保留其中一个说法，把另一处改成与之"
            "一致（数字/期限/年数/物品状态）。不重写其他内容，不换措辞，不新增实体；"
            "一个字段里若挤着多条期限，只留最狠的一条。\n"
            '只输出 JSON：{"premise": "...", "spine": {...原字段名不变...}}'
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                model_tier="strong",
                system_prompt=(
                    "你是设定校对编辑。你只统一互相矛盾的事实，从不改写内容。"
                ),
                user_prompt=repair_user,
                fallback_response="{}",
                prompt_template="canon_contradiction_repair",
                prompt_version="v1",
                max_tokens_override=1400,
            ),
        )
        raw = (completion.content or "").strip()
        try:
            payload = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            s0, s1 = raw.find("{"), raw.rfind("}")
            payload = (
                json.loads(raw[s0 : s1 + 1], strict=False)
                if s0 != -1 and s1 > s0 else None
            )
        if not isinstance(payload, dict):
            return premise, spine, report.to_dict()
        new_premise = str(payload.get("premise") or "").strip()
        new_spine = payload.get("spine")
        # 结构守卫：premise 非空且长度同量级；spine 字段集合不许缩水。
        if not new_premise or len(new_premise) < len(premise) * 0.5:
            return premise, spine, report.to_dict()
        if spine and (
            not isinstance(new_spine, dict)
            or not set(spine.keys()) <= set(new_spine.keys())
        ):
            return premise, spine, report.to_dict()
        recheck = await verify_blurb_coherence(
            session, settings,
            synopsis=synopsis, premise=new_premise,
            spine=new_spine if isinstance(new_spine, dict) else spine,
        )
        det_after = _det_conflicts(
            new_premise, new_spine if isinstance(new_spine, dict) else spine
        )
        if (len(recheck.canon_findings) + len(det_after)) < (
            len(canon) + len(det_before)
        ):
            logger.warning(
                "canon contradiction repair adopted: %d(+%d det) -> %d(+%d det) finding(s)",
                len(canon), len(det_before),
                len(recheck.canon_findings), len(det_after),
            )
            out = recheck.to_dict()
            out["repaired"] = True
            out["findings_before"] = len(canon) + len(det_before)
            out["deterministic_conflicts_before"] = [
                f"{f.quote_a}↔{f.quote_b}" for f in det_before
            ]
            return new_premise, (
                new_spine if isinstance(new_spine, dict) else spine
            ), out
        logger.warning(
            "canon contradiction repair NOT adopted (%d -> %d finding(s)); keeping original",
            len(canon), len(recheck.canon_findings),
        )
        out = report.to_dict()
        out["repaired"] = False
        return premise, spine, out
    except Exception:
        logger.warning("canon contradiction repair failed (fail-open)", exc_info=True)
        return premise, spine, None


async def _coherence_advisory(
    session: AsyncSession,
    settings: AppSettings,
    *,
    synopsis: str,
    premise: str,
    spine: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str, bool]:
    """简介/正典自洽 advisory（2026-08-07 接活）：引文核对式矛盾扫描。

    返回 ``(report_dict | None, 反馈行, 简介侧是否有矛盾)``。
    真机根因：四条互斥倒计时的简介拿了 comprehensibility 满分——全链没有一处
    测逻辑。涉及简介的矛盾（第三返回值）驱动重生循环回灌重写；纯正典矛盾
    （premise↔spine，如 35 岁 vs 三十年）重写简介修不了，只持久化+日志，
    修它属于 finalize 结构问题。fail-open，永不 raise。
    """

    try:
        from bestseller.services.blurb_coherence_judge import (
            verify_blurb_coherence,
        )

        report = await verify_blurb_coherence(
            session, settings, synopsis=synopsis, premise=premise, spine=spine,
        )
        report_dict = report.to_dict()
        syn_findings = report.synopsis_findings
        if report.canon_findings:
            logger.warning(
                "canon itself is contradictory (premise↔spine): %s",
                "; ".join(f"{f.quote_a}↔{f.quote_b}" for f in report.canon_findings),
            )
        if not syn_findings:
            return report_dict, "", False
        lines = "；".join(
            f"「{f.quote_a}」与「{f.quote_b}」不能同时成立（{f.explanation}）"
            for f in syn_findings[:3]
        )
        feedback = (
            f"【简介自相矛盾（引文已核对）】{lines}。"
            "重写时全文只保留一条倒计时/一个说法，逐句自查任何两句不得互相矛盾。"
        )
        return report_dict, feedback, True
    except Exception:
        logger.warning("coherence advisory failed (non-fatal)", exc_info=True)
        return None, "", False


async def _run_finalize_arena(
    session: AsyncSession,
    settings: AppSettings,
    *,
    synopsis: str,
    genre: str,
    sub_genre: str | None,
    config: dict[str, Any] | None = None,
    judge: Any = None,
) -> dict[str, Any] | None:
    """arena 相对盲评作构思终验（审计 P1-7；config ``arena.run_at_finalize`` 门控）。

    默认 off：每次评估 = 参照数×2 次判官调用（min_refs=4~max_refs=6 → 8-12 次），
    作按需终验而非每次构思例行。开启时终稿简介 vs 真实爆款双盲位置交换胜率写进
    ``story_appeal_report["arena"]``（advisory，不硬拦）。永不 raise。
    """

    try:
        from bestseller.services.story_appeal import (
            load_story_appeal_config,
            meets_story_bar,
        )

        cfg = config if config is not None else load_story_appeal_config()
        arena_cfg = cfg.get("arena", {}) if isinstance(cfg, dict) else {}
        if not bool(arena_cfg.get("run_at_finalize", False)):
            return None
        from bestseller.services.premise_appeal_arena import (
            make_deepseek_judge,
            run_appeal_arena,
        )

        judge_fn = judge if judge is not None else make_deepseek_judge(
            session, settings,
            model_key=str(arena_cfg.get("judge_model_key", "deepseek-v4-flash")),
        )
        summary = await run_appeal_arena(
            candidate_blurb=synopsis, genre=genre, sub_genre=sub_genre,
            judge=judge_fn,
            min_refs=int(arena_cfg.get("min_refs", 4)),
            max_refs=int(arena_cfg.get("max_refs", 6)),
        )
        out = summary.to_dict()
        out["meets_story_bar"] = meets_story_bar(summary.win_rate, cfg)
        return out
    except Exception:
        logger.warning("finalize arena failed (non-fatal)", exc_info=True)
        return None


async def _polish_title(
    session: AsyncSession,
    settings: AppSettings,
    *,
    title: str,
    premise: str,
    synopsis: str,
    feedback: str,
    genre: str,
    sub_genre: str,
    is_en: bool,
    language: str,
    config: dict[str, Any] | None = None,
    audience_orientation: str = "",
) -> tuple[str, UUID | None]:
    """Focused title rewrite: LLM proposes N candidates, the zero-token title gate
    picks the best-scoring one (generate-then-select). Fail-open: returns the
    original title on any error / if no candidate beats it. Returns (title, run_id).
    """

    from bestseller.services.title_appeal_gate import evaluate_title_appeal

    if is_en:
        system_prompt = (
            "You are a veteran web-novel platform editor. Propose 6 CLICK-optimized "
            "book titles per the fix notes. Output ONLY the titles, one per line, no "
            "numbering, no quotes, no explanation."
        )
        user_prompt = (
            f"Genre: {genre} ({sub_genre})\n[Premise]\n{premise}\n[Blurb]\n{synopsis}\n\n"
            f"[Fix notes]\n{feedback}\n\nRules: short & punchy; coherent claim; "
            "protagonist agency or strong concept collision; avoid red-ocean cliches. "
            "Output 6 titles, one per line."
        )
    else:
        system_prompt = (
            "你是网文平台资深编辑。按【整改要求】给出 6 个【点击型】书名候选。"
            "只输出书名，每行一个，不要编号、不要引号、不要解释。"
        )
        from bestseller.services.genre_persona import render_channel_style_stamp

        user_prompt = (
            f"{render_channel_style_stamp(audience_orientation)}"
            f"题材：{genre}（{sub_genre}）\n【故事内核】\n{premise}\n【简介】\n{synopsis}\n\n"
            f"【整改要求】\n{feedback}\n\n硬性：4-12字、一眼可读完；必须通顺成立（别让抽象概念去"
            "保护/放过人）；主角能动性或强概念碰撞；避开都市之/最强系统/绝世神医等烂大街壳。"
            "输出 6 个书名，每行一个。"
        )
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=title,
                prompt_template="conception_title_polish",
                prompt_version="v1",
                metadata={"language": language},
                max_tokens_override=300,
            ),
        )
        raw = (completion.content or "").strip()
        run_id = completion.llm_run_id
    except Exception:
        logger.warning("title polish failed; keeping prior title", exc_info=True)
        return title, None

    # Parse candidates, strip numbering/quotes/punctuation artifacts.
    candidates: list[str] = []
    for ln in raw.splitlines():
        c = ln.strip().strip("\"'“”‘’`").lstrip("0123456789.、）)·-—　 ").strip()
        c = str(_sanitize_forbidden_default_motifs(c, is_en=is_en)).strip()
        if c and c not in candidates:
            candidates.append(c)
    candidates.append(title)  # always keep the incumbent in the running

    # Zero-token gate selects the best-scoring candidate (no extra LLM cost).
    best_title, best_score = title, -1.0
    for c in candidates:
        try:
            v = evaluate_title_appeal(c, genre=genre, sub_genre=sub_genre, config=config)
        except Exception:
            continue
        if v.total > best_score:
            best_title, best_score = c, v.total
    return best_title, run_id


def build_appeal_regen_trace(
    history: list[dict[str, Any]], *, blurb_min: float
) -> dict[str, Any]:
    """把达标门每一轮的分数压成一条可判读的轨迹。

    2026-08-23 真机（验证书 8）：构思跑满有界重生仍未达标（简介 65.0 < 68），
    整本书被拦下。而 conception_log 里一条重生记录都没有——15 条日志覆盖了
    淘汰赛、回声门、脊柱门、契约门，唯独真正毙掉书的这道门没有轨迹，于是
    无法回答最关键的问题：它在稳步逼近还是原地打转。

    ``verdict`` 三态：``passed`` / ``converging``（总涨幅 ≥1 分，加预算可能
    有用）/ ``stuck``（几乎没动，加预算没用、要换打法）。判读留给人，这里
    只负责把事实摆出来。
    """

    rounds = [r for r in history if isinstance(r, dict)]
    if not rounds:
        return {
            "attempts": 0,
            "verdict": "no_regen",
            "blurb_min": float(blurb_min),
            "rounds": [],
        }
    blurbs = [float(r.get("blurb") or 0.0) for r in rounds]
    passed = any(bool(r.get("meets_bar")) for r in rounds)
    gain = blurbs[-1] - blurbs[0]
    best = max(blurbs)
    if passed:
        verdict = "passed"
    elif gain >= 1.0:
        verdict = "converging"
    else:
        verdict = "stuck"
    return {
        "attempts": len(rounds),
        "verdict": verdict,
        "blurb_min": float(blurb_min),
        "blurb_first": blurbs[0],
        "blurb_last": blurbs[-1],
        "blurb_best": best,
        "blurb_gain": round(gain, 2),
        "gap_to_bar": round(float(blurb_min) - best, 2),
        "rounds": rounds,
    }


async def run_conception_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    genre_key: str,
    chapter_count: int,
    user_hints: dict[str, Any] | None = None,
    story_facets: object | None = None,
    progress: ProgressCallback | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    genre_intent_contract: GenreIntentContract | None = None,
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
    ctx = _build_genre_context(
        genre_key,
        chapter_count,
        story_facets=story_facets,
        genre=genre,
        sub_genre=sub_genre,
        genre_intent_contract=genre_intent_contract,
    )
    concept_bundle = None
    if user_hints:
        ctx["user_hints"] = user_hints
        concept_bundle = coerce_concept_lab_bundle(user_hints.get("concept_lab"))
        if concept_bundle is not None:
            ctx["concept_lab"] = concept_bundle.model_dump(mode="json")
        # Contract-owned explicit selection wins; legacy user_hints may only
        # add the same opt-in flag and can never turn it off accidentally.
        ctx["wild_concept"] = bool(user_hints.get("wild_concept")) or bool(
            ctx.get("wild_concept")
        )
        _apply_qimao_hints_to_context(ctx)

    # Agent ①: heat-search → 脑洞/爽点 *methodology* selection. Replaces the old
    # baked concrete bundle with a soft methodology framework the conception
    # agents grow genre-fitting concepts from. Fallback-safe: never blocks a run.
    await _attach_concept_methodology(session, settings, ctx, user_hints=user_hints)

    is_en = ctx.get("language", "zh-CN").startswith("en")
    ctx = _sanitize_forbidden_default_motifs(ctx, is_en=is_en)

    # Freeze the ORIGINAL user intent before the tournament champion is merged
    # into ctx["description"] (~line 3911). The debt/death intent-exemptions read
    # this snapshot, so a generated champion carrying a stray 债/亡 token can never
    # disable the pipeline's own anti-debt / anti-death guard downstream (C3).
    _snapshot_user_intent(ctx)

    # Cross-book name de-dup: feed the cast prompt the names other projects
    # already used so the LLM stops re-minting 陆沉/宁尘/etc. Best-effort.
    ctx["avoid_names"] = await _recent_cast_names(session)

    # Cross-book *mechanism* de-dup: feed conception the core mechanisms
    # recent same-genre books already used so book N+1 stops re-minting book
    # N's golden finger (the recurring debt-ledger problem). Best-effort.
    await _attach_mechanism_dedup(session, settings, ctx)

    selected_hook_spec = coerce_hook_spec(
        user_hints.get("hook_spec") if isinstance(user_hints, dict) else None
    )
    explicit_concept_seed = (
        str(user_hints.get("concept_seed") or "").strip()
        if isinstance(user_hints, dict)
        else ""
    )
    # Explicit user material remains the only authoritative identity source.
    # The automatic seed exists solely for the genuine zero-input path.
    automatic_story_seed = (
        ""
        if (
            explicit_concept_seed
            or concept_bundle is not None
            or selected_hook_spec is not None
        )
        else _build_automatic_story_seed(story_facets)
    )
    hook_candidates_payload: list[dict[str, Any]] = []
    if getattr(settings.hook_engine, "enabled", True):
        try:
            from bestseller.services.anti_commonsense_hook import (
                build_hook_duplicate_risk_fn,
                generate_hook_candidates,
            )

            candidate_count = max(1, int(getattr(settings.hook_engine, "candidate_count", 6)))
            rank_weights = {
                "h_norm": float(getattr(settings.hook_engine, "rank_weight_h_norm", 0.62)),
                "novelty": float(getattr(settings.hook_engine, "rank_weight_novelty", 0.28)),
                "duplicate_risk": float(
                    getattr(settings.hook_engine, "rank_weight_duplicate_risk", 0.10)
                ),
            }
            emit_activity("hook_candidates_started", {"count": candidate_count})
            candidates = generate_hook_candidates(
                genre=str(ctx.get("genre") or genre_key),
                locale=str(ctx.get("language") or "zh-CN"),
                role=(
                    str(getattr(story_facets, "protagonist_role", "") or "")
                    if story_facets is not None
                    else None
                ),
                count=candidate_count,
                seed=_hook_candidate_seed(genre_key),
                min_h_norm=float(getattr(settings.hook_engine, "min_h_norm", 30.0)),
                duplicate_risk_fn=build_hook_duplicate_risk_fn(
                    _hook_duplicate_corpus(ctx, user_hints)
                ),
                rank_weights=rank_weights,
            )
            hook_candidates_payload = [item.model_dump(mode="json") for item in candidates]
            emit_milestone(
                "hook_candidates_generated",
                {"count": len(hook_candidates_payload)},
            )
            # Do not auto-select the old formula HookSpec.  It is a mechanism
            # ideation artifact, not the approved concept and not proof of
            # long-form capacity.  The tournament champion becomes the only
            # active v2 source below.
        except Exception:
            logger.warning("Anti-commonsense hook candidate generation failed", exc_info=True)
    if selected_hook_spec is not None:
        hook_spec_payload = selected_hook_spec.model_dump(mode="json")
        ctx["hook_spec"] = hook_spec_payload
        ctx["anti_commonsense_hook"] = hook_spec_payload
        if hook_candidates_payload:
            ctx["hook_candidates"] = hook_candidates_payload

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
    # Board data must be in hand BEFORE the tournament picks candidates.
    await _prefetch_market_competitors(session, settings, ctx)
    if genre_intent_contract is not None:
        conception_log.append(
            {
                "round": -2,
                "agent": "genre_intent_contract",
                "source": genre_intent_contract.source,
                "contract_hash": genre_intent_contract.contract_hash(),
                "genre_key": genre_intent_contract.genre_key,
                "sub_genre_key": genre_intent_contract.sub_genre_key,
                "prompt_pack_key": genre_intent_contract.prompt_pack_key,
                "allowed_modernity": genre_intent_contract.allowed_modernity,
            }
        )
    degradation_tracker = DegradationTracker()

    def _emit(stage: str, data: dict[str, Any] | None = None) -> None:
        if progress is not None:
            progress(stage, data)

    # ── Round -1: 概念淘汰赛(2026-07-09)——高概念先行,反题材均值回归 ──────
    # 真机书《谁敢动我山头》证实:概念层单次生成必然回归题材众数(废脉藏宝/
    # 破宗门重建),读者可自动补全全书。此处在多agent展开【之前】跑"反俗套禁用
    # +杂交N候选+引擎审计+判官对撞榜单"淘汰赛,冠军注入 ctx["description"]
    # ——它是商业定位/市场/角色/世界观全部 prompt 的共同源头,零侵入全覆盖。
    # 用户显式给 concept_lab 时把它作为不可替换的 seed；仍必须补齐容量证明，
    # 不能让用户选择成为绕过长篇门禁的后门。
    _ct_result: Any | None = None
    if chapter_count > 0:
        try:
            from bestseller.services.concept_tournament import (
                render_high_concept_block,
                resolve_tournament_config,
                run_concept_tournament,
            )

            # 脑洞全开:合并 wild_mode 覆盖(降门/罚分/多候选/偏新颖);否则 None
            # → tournament 读基线,构思行为与现状逐字节一致。
            _ct_config = resolve_tournament_config(
                wild=bool(ctx.get("wild_concept"))
            )
            # 短中篇也要至少 2 次尝试：只给 1 次时,重试分支(近失种子/定向补强)
            # 对 50 章建书是死代码——真机 3 轮全部单次干涸后拿保底概念掷硬币,
            # 2/3 死在 logline 门(2026-07-17 五轮 E2E 取证)。
            max_concept_attempts = 3 if chapter_count >= 200 else 2
            concept_retry_feedback = ""
            _dry_retry_seed = ""
            # 被默认族（品味门）拒掉的第一个完整冠军。后续轮次若一个冠军都
            # 产不出来，用它兜底发货，而不是让整本书死在「N 轮无合格冠军」。
            _default_family_fallback_winner: Any = None
            _user_owned_story_seed = (
                explicit_concept_seed
                or (
                    str(concept_bundle.one_liner or concept_bundle.reader_promise)
                    if concept_bundle is not None
                    else str(getattr(selected_hook_spec, "one_liner", "") or "")
                )
            )
            for concept_attempt in range(1, max_concept_attempts + 1):
                _emit("concept_tournament_started", {
                    "round": -1,
                    "attempt": concept_attempt,
                    "max_attempts": max_concept_attempts,
                    "wild_concept": bool(ctx.get("wild_concept")),
                })
                _automatic_seed_for_attempt = (
                    _automatic_story_seed_for_tournament_attempt(
                        automatic_story_seed,
                        attempt=concept_attempt,
                    )
                    if not _user_owned_story_seed
                    else ""
                )
                # Generated StoryFacets are not user-owned evidence.  They may
                # anchor round one, but must neither shrink the zero-input
                # search budget nor keep a novelty-failed reroll on the same
                # story identity.  Only explicit material or a gate-eligible
                # near miss counts as a real refinement seed.
                _seed_for_attempt = bool(
                    _user_owned_story_seed or _dry_retry_seed
                )
                attempt_config = {
                    **_ct_config,
                    "n_candidates": _tournament_attempt_candidate_count(
                        attempt=concept_attempt,
                        baseline=int(_ct_config.get("n_candidates") or 6),
                        has_seed=_seed_for_attempt,
                        refining=bool(_dry_retry_seed),
                    ),
                }
                _ct_result = await run_concept_tournament(
                    session, settings,
                    genre=str(ctx.get("genre") or genre_key),
                    sub_genre=str(ctx.get("sub_genre") or ""),
                    chapter_count=chapter_count,
                    avoid_mechanisms=list(ctx.get("avoid_mechanisms") or []),
                    config=attempt_config,
                    # 频道锚：男频请求产出文艺女主向项目卡=题材保真必挂(真机4候选
                    # 全灭的直接根因之一),受众必须传到内核/蒸钩/候选三层 prompt。
                    audience_orientation=str(
                        (ctx.get("user_hints") or {}).get("audience_orientation") or ""
                    ),
                    # 纯爽/外置代价档同样必须进淘汰赛 prompt——规划期的 ideology
                    # 翻译对死在构思门禁的书永远来不及(2026-07-24 实录)。
                    cost_style=str(
                        (
                            (ctx.get("genre_intent_contract") or {}).get(
                                "explicit_enhancers"
                            )
                            or {}
                        ).get("cost_style")
                        or "standard"
                    ),
                    # 建书页勾的调性与故事技能。此前它们只进「商业定位 brief」——
                    # 市场／角色／世界观那批 agent 的输入，而淘汰赛跑在它们之前。
                    # 用户要的「轻松＋喜剧＋爽感」于是从未到达概念生成，模型按玄幻
                    # 默认调性产出沉重候选，判官正确判它们不想点、不好懂，干涸，
                    # 书死（2026-07-29）。凡影响故事内容的选择必须在这里就在场。
                    tone_preference=str(
                        (ctx.get("genre_intent_contract") or {}).get("tone_preference")
                        or ""
                    ),
                    # 同一形状的第四条：题材本体此前只在构思最末端 fail-closed 检查，
                    # 淘汰赛完全不知道它，于是可以堂堂正正加冕一个「阴间挂号处 KPI
                    # 喜剧」当东方玄幻的冠军，再被最后那道闸门连书带稿一起打死
                    # (2026-08-05 实录)。冠军由本体优先挑选，闸门本身不放宽。
                    genre_intent_contract=genre_intent_contract,
                    # 第五条，同一形状（2026-08-10）：竞品调研此前跑在构思**之后**，
                    # 只落一份 metadata 摘要——书名、premise、简介都已定稿，它改不动
                    # 任何东西，是收据不是输入。文档写的流程第一步「竞品有没有这本
                    # 书」于是从未被真正问过。榜单在这里就位，撞车的胚子拿不到展开位。
                    market_competitors=_market_competitor_rows(ctx),
                    effect_skills=list(
                        (
                            (ctx.get("genre_intent_contract") or {}).get(
                                "explicit_enhancers"
                            )
                            or {}
                        ).get("effect_skills")
                        or []
                    ),
                    # Only explicit user material can authorize a debt/death
                    # story theme. Story Architect and previous generated
                    # candidates are system output, so they must never turn a
                    # recurring default motif into apparent user intent.
                    allow_debt_theme=_mentions_debt_theme(
                        _user_owned_story_seed
                    ),
                    allow_death_theme=_mentions_death_theme(
                        _user_owned_story_seed
                    ),
                    # 入参集整体交给规划层。这个块 conception 早就在构造了,但此前
                    # 只喂给商业定位 brief——市场/角色/世界观那批 agent 的输入,而
                    # 一句话规划跑在它们之前。2026-07-30 审计:叙事规模、反常识
                    # 方向、脑洞引擎全部到不了这一层。整块传比逐字段补可靠:以后
                    # 新增选项自动在场。
                    creation_intent_block=(
                        _creation_intent_prompt_block(ctx) + _automatic_seed_for_attempt
                    ),
                    seed_concept=(
                        _user_owned_story_seed
                        # 干涸重试改定向补强：上一轮最优近失候选(≤3轴差距)升为种子,
                        # 让重试轮改良 6.5 分的具体概念而不是从零重掷(近失候选输给
                        # 空手=淘汰赛饥饿悖论,2026-07-16 三连干涸实录)。
                        or _dry_retry_seed
                        # Zero-input creation still gets Story Architect's
                        # selected-options-aligned start in round one.  A later
                        # free reroll deliberately drops it after novelty or
                        # predictability proved that identity unrepairable.
                        or _automatic_seed_for_attempt
                    ),
                    retry_feedback=concept_retry_feedback,
                )
                _emit(
                    "concept_tournament_attempt_completed",
                    {
                        "attempt": concept_attempt,
                        "max_attempts": max_concept_attempts,
                        "winner": (
                            _ct_result.winner.to_dict() if _ct_result.winner else None
                        ),
                        "candidates": [
                            {
                                "dimension": candidate.dimension,
                                "concept": candidate.concept,
                                "composite": candidate.composite,
                                "rejected_reason": candidate.rejected_reason,
                                "judge_freshness": candidate.judge_freshness,
                                "judge_click": candidate.judge_click,
                                "judge_predictable": candidate.judge_predictable,
                                "judge_character_logic": candidate.judge_character_logic,
                                "judge_mechanism_causality": (
                                    candidate.judge_mechanism_causality
                                ),
                                "judge_genre_fidelity": candidate.judge_genre_fidelity,
                                "judge_plain_language": candidate.judge_plain_language,
                                "judge_story_motion": candidate.judge_story_motion,
                                "seriality_report": candidate.seriality_report,
                                "seriality_judge": candidate.seriality_judge,
                            }
                            for candidate in _ct_result.candidates
                        ],
                        "generation_model_key": _ct_result.generation_model_key,
                        "judge_model_key": _ct_result.judge_model_key,
                    },
                )
                llm_run_ids.extend(_ct_result.llm_run_ids)
                conception_log.append({
                    "round": -1,
                    "attempt": concept_attempt,
                    "agent": "concept_tournament",
                    **_ct_result.to_dict(),
                })
                # The accepted winner is still passed through
                # _sanitize_forbidden_default_motifs( before shared injection.
                # ctx["description"] = f"{ctx.get('description') or ''}\n{_hc_block}"
                # ctx["high_concept"] = _ct_result.winner.to_dict()
                if _ct_result.winner is not None:
                    _explicit_seed_text = (
                        explicit_concept_seed
                        or (
                            str(concept_bundle.one_liner or concept_bundle.reader_promise)
                            if concept_bundle is not None
                            else str(getattr(selected_hook_spec, "one_liner", "") or "")
                        )
                    )
                    # Reject ontology leakage before the winner can become the
                    # shared description for every downstream agent. Explicit
                    # user seeds remain authoritative: only terms absent from
                    # the user's own seed are treated as injected pollution.
                    if genre_intent_contract is not None:
                        from bestseller.services.genre_intent_contract import (
                            detect_genre_native_ontology_violations,
                        )

                        _winner_text = render_high_concept_block(_ct_result)
                        _violations = detect_genre_native_ontology_violations(
                            _winner_text,
                            genre_intent_contract,
                        )
                        _unexpected_violations = tuple(
                            term for term in _violations if term not in _explicit_seed_text
                        )
                        if _unexpected_violations:
                            from dataclasses import replace as _dc_replace

                            _rejected_winner = _ct_result.winner
                            _ct_result.candidates = [
                                _dc_replace(
                                    candidate,
                                    rejected_reason=(
                                        "题材本体污染: "
                                        + "/".join(_unexpected_violations)
                                    ),
                                )
                                if candidate is _rejected_winner
                                else candidate
                                for candidate in _ct_result.candidates
                            ]
                            _ct_result.winner = None
                            concept_retry_feedback = (
                                "上一轮冠军越过所选题材本体边界；必须回到题材原生的"
                                "职业、规则、关系和资源冲突。被拒词不回放。"
                            )
                            _emit("concept_tournament_winner_rejected", {
                                "attempt": concept_attempt,
                                "violations": list(_unexpected_violations),
                            })
                            continue
                    # Defense in depth at the exact shared-context boundary.
                    # The tournament already screens candidates, but no future
                    # prompt mode or judge path may inject a debt/corpse winner
                    # into every downstream agent merely because it scored well.
                    from bestseller.services.anti_default_motif import (
                        DEATH_MOTIF_RE,
                    )
                    from bestseller.services.concept_tournament import (
                        _creation_intent_content_violations,
                    )

                    _winner_payload = _ct_result.winner.to_dict()
                    _winner_story_text = "\n".join(
                        _ontology_text_values(
                            {
                                key: _winner_payload.get(key)
                                for key in _ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS
                            }
                        )
                    )
                    _pollution_reasons = list(
                        _creation_intent_content_violations(
                            _winner_story_text,
                            tone_preference=str(
                                (ctx.get("genre_intent_contract") or {}).get(
                                    "tone_preference"
                                )
                                or ""
                            ),
                            effect_skills=list(
                                (
                                    (ctx.get("genre_intent_contract") or {}).get(
                                        "explicit_enhancers"
                                    )
                                    or {}
                                ).get("effect_skills")
                                or []
                            ),
                            cost_style=str(
                                (
                                    (ctx.get("genre_intent_contract") or {}).get(
                                        "explicit_enhancers"
                                    )
                                    or {}
                                ).get("cost_style")
                                or ""
                            ),
                        )
                    )
                    # 默认族只在**还有重试机会时**才作废冠军（2026-08-14）：
                    # 它是品味偏好，不是硬错误。最后一轮仍命中就带案底发货——
                    # 否则 winner=None 会走到「N 轮均未产出合格冠军」那条
                    # ConceptContractError，把整本书打死（用户创意种子为空时
                    # 尤其必然），正是 8·2 母题警察的死因。
                    _is_last_concept_attempt = concept_attempt >= max_concept_attempts
                    if (
                        not _user_requested_debt(ctx)
                        and _is_debt_dominated_mechanism(_winner_story_text)
                    ):
                        if _is_last_concept_attempt:
                            ctx["default_family_winner_advisory"] = True
                            logger.warning(
                                "final concept attempt still inside the default "
                                "family; shipping with advisory instead of killing "
                                "the book"
                            )
                        else:
                            _pollution_reasons.append("UNREQUESTED_DEFAULT_MOTIF_A")
                    _seed_has_default_death = bool(
                        _mentions_death_theme(_explicit_seed_text)
                        or _user_requested_death_revival(ctx)
                        or DEATH_MOTIF_RE.search(_explicit_seed_text)
                    )
                    if not _seed_has_default_death and (
                        _is_death_revival_dominated(_winner_story_text)
                        or _is_anonymous_death_dominated(_winner_story_text)
                        or _contains_default_death_motif(_winner_story_text)
                    ):
                        _pollution_reasons.append("UNREQUESTED_DEFAULT_MOTIF_B")
                    if _pollution_reasons:
                        # 被口味门拒掉的冠军仍是**完整可用**的产物：留作兜底。
                        # 2026-08-14 真机：第1轮有冠军但被默认族拒→第2轮候选全
                        # 挂在钩子硬门→「2轮均未产出合格冠军」→ raise → 整本书死。
                        # 后面几轮可能一个冠军都产不出，那时宁可带案底发货，
                        # 也不能因为一项品味偏好把书判死。
                        if _pollution_reasons == ["UNREQUESTED_DEFAULT_MOTIF_A"] and (
                            _default_family_fallback_winner is None
                        ):
                            _default_family_fallback_winner = _ct_result.winner
                        _ct_result.winner = None
                        concept_retry_feedback = (
                            "上一轮冠军命中未选择的默认母题门禁。必须只从本次建书合同"
                            "重新生成开局、机制和情绪承诺，不得复用或改写被拒文本。"
                        )
                        _emit(
                            "concept_tournament_winner_rejected",
                            {
                                "attempt": concept_attempt,
                                "violations": _pollution_reasons,
                            },
                        )
                        continue
                    break
                if concept_attempt < max_concept_attempts:
                    failed_finalists = sorted(
                        _ct_result.candidates,
                        key=lambda candidate: (
                            bool(candidate.seriality_judge),
                            candidate.composite or 0.0,
                            sum(
                                score or 0.0
                                for score in (
                                    candidate.judge_freshness,
                                    candidate.judge_click,
                                    candidate.judge_character_logic,
                                    candidate.judge_mechanism_causality,
                                    candidate.judge_story_motion,
                                )
                            ),
                        ),
                        reverse=True,
                    )[:2]
                    # Never paste rejected candidates or free-form judge prose
                    # back into generation. They may contain exactly the motif
                    # that a deterministic gate rejected, turning retry into a
                    # second pollution channel. Only stable axis codes cross the
                    # retry boundary; an eligible near-miss is separately passed
                    # as the intentional refinement seed below.
                    _failed_axis_codes: list[str] = []
                    for candidate in failed_finalists:
                        rejected = str(candidate.rejected_reason or "")
                        if rejected.startswith("钩子硬门失败:"):
                            _failed_axis_codes.extend(
                                axis.strip()
                                for axis in rejected.split(":", 1)[1].split("/")
                                if axis.strip()
                            )
                        seriality = candidate.seriality_report or {}
                        _failed_axis_codes.extend(
                            str(code).strip()
                            for code in (seriality.get("blocking_codes") or [])
                            if str(code).strip()
                        )
                    _failed_axis_codes = list(dict.fromkeys(_failed_axis_codes))[:8]
                    concept_retry_feedback = (
                        "上一轮没有候选同时通过钩子与长篇承载门；只修这些验收轴："
                        + ("/".join(_failed_axis_codes) or "候选完整性与长篇承载力")
                        + "。不得复用上一轮被拒文本。"
                    )
                    _explicit_retry_intent = (
                        explicit_concept_seed
                        or (
                            str(
                                concept_bundle.one_liner
                                or concept_bundle.reader_promise
                            )
                            if concept_bundle is not None
                            else str(
                                getattr(selected_hook_spec, "one_liner", "") or ""
                            )
                        )
                    )
                    _dry_retry_seed = _best_dry_tournament_seed(
                        list(_ct_result.candidates),
                        allow_debt=_mentions_debt_theme(_explicit_retry_intent),
                        allow_death=_mentions_death_theme(_explicit_retry_intent),
                    )
                    if _dry_retry_seed:
                        # 配额化（2026-08-14 真机）：旧措辞「保留其故事身份，不要
                        # 另起炉灶」把近失候选变成了**整池**的锚，一轮 6 个候选里
                        # 4 个是同一个故事的复述，随后被新颖度判官全部毙掉
                        # （6/6 挂 新颖度）→ 无冠军 → 整本书死。近失补强要保留
                        # （2026-07-16 饥饿悖论），但只能占少数席位，其余必须换骨架。
                        concept_retry_feedback += (
                            "\n【定向补强·限额】上面第一条是上一轮离达标最近的候选。"
                            "本批**只用 2 个名额**沿着它做同源补强（保留其故事身份，"
                            "只修被判不达标的轴）；**其余名额必须是完全不同的故事**——"
                            "不同的人物身份、不同的场域、不同的异常来源，"
                            "不得是它的改写、换角度或换说法。"
                            "整批雷同会被新颖度judge全部毙掉，那等于这一轮白跑。"
                        )
                    _emit("concept_tournament_retry", {
                        "attempt": concept_attempt + 1,
                        "reason": "no_hook_and_seriality_qualified_winner",
                    })
            if _ct_result.winner is not None:
                # ctx 在更早处已整体过一遍跨书污染消毒,追加的注入块同样要过
                # (P1-1 同款教训:新增文本通道不得成为默认母题的豁免通道)。
                _hc_block = str(
                    _sanitize_forbidden_default_motifs(
                        render_high_concept_block(_ct_result), is_en=is_en
                    )
                )
                ctx["description"] = f"{ctx.get('description') or ''}\n{_hc_block}"
                ctx["high_concept"] = _ct_result.winner.to_dict()
                _emit("concept_tournament_winner", {
                    "dimension": _ct_result.winner.dimension,
                    "concept": _ct_result.winner.concept[:120],
                })
                logger.info(
                    "Concept tournament winner (dim=%s, composite=%s): %s",
                    _ct_result.winner.dimension, _ct_result.winner.composite,
                    _ct_result.winner.concept[:120],
                )
            elif _default_family_fallback_winner is not None:
                # 兜底发货（2026-08-14 真机定案）：本轮无冠军，但前面轮次有一个
                # 只因品味门被拒的完整冠军。带案底用它，绝不因一项偏好杀书。
                _ct_result.winner = _default_family_fallback_winner
                ctx["high_concept"] = _ct_result.winner.to_dict()
                ctx["default_family_winner_advisory"] = True
                logger.warning(
                    "no fresh winner after %d attempts; shipping the "
                    "default-family champion from an earlier attempt (advisory)",
                    max_concept_attempts,
                )
                _emit(
                    "concept_tournament_winner",
                    {
                        "dimension": _ct_result.winner.dimension,
                        "concept": _ct_result.winner.concept[:120],
                        "default_family_advisory": True,
                    },
                )
            else:
                logger.info("Concept tournament produced no winner after all attempts")
                # A dry tournament only used to hard-stop long books (>=200
                # chapters); short quickstarts coasted on with "no injection".
                # 2026-07-24 (custom-xuanhuan-1784899694) showed where that
                # coast ends: a bare auto-premise has nothing else to stand on,
                # finalize invents genre-default 系统爽文 clichés for ~10
                # minutes, and the logline gate then rejects at 3.0 USING THE
                # TOURNAMENT'S OWN judged evidence — a guaranteed death with
                # the cause misattributed to the logline gate. So: no winner +
                # no substantive user story seed → stop now, for every length,
                # and say why in the tournament's own words. Creations that DID
                # supply real material (explicit seed / concept-lab bundle /
                # hook spec) keep the old behavior — their material can still
                # carry finalize past the gate on its own.
                _has_substantive_story_seed = bool(
                    explicit_concept_seed
                    or concept_bundle is not None
                    or str(getattr(selected_hook_spec, "one_liner", "") or "").strip()
                )
                if chapter_count >= 200 or not _has_substantive_story_seed:
                    from bestseller.services.concept_contract import (
                        ConceptContractError,
                    )
                    from bestseller.services.concept_tournament import (
                        dry_tournament_rejection_summary,
                    )

                    _dry_reasons = dry_tournament_rejection_summary(
                        list(getattr(_ct_result, "candidates", []) or [])
                    )
                    raise ConceptContractError([
                        f"概念淘汰赛 {max_concept_attempts} 轮均未产出合格冠军，"
                        "且本次创建没有可独立支撑故事的用户创意种子；"
                        "已在市场/角色/世界观生成前终止（避免空概念走完全流程后"
                        "被一句话硬门必然拒绝）。",
                        *_dry_reasons,
                        "整改方向：给一句具体的故事创意（主角是谁/撞上什么反常事件/"
                        "第一个不可逆选择），或换一个题材切入角度后重试。",
                    ])
        except Exception as exc:
            from bestseller.services.concept_contract import (
                ConceptContractError,
            )

            if isinstance(exc, ConceptContractError):
                raise
            if chapter_count >= 200:
                raise ConceptContractError(
                    [
                        "长篇概念淘汰赛执行失败，已在项目创建与书籍规划前终止："
                        f"{type(exc).__name__}: {exc}"
                    ]
                ) from exc
            logger.warning("Concept tournament failed (non-fatal); no injection", exc_info=True)

    # ── Round 0: Autonomous Commercial Positioning ───────────────────
    _emit("conception_commercial_positioning", {"round": 0, "agent": "commercial_commissioner"})
    commercial_brief, stage_llm_ids = await _llm_call_json(
        session,
        settings,
        role="planner",
        system_prompt=_COMMERCIAL_POSITIONING_SYSTEM_EN if is_en else _COMMERCIAL_POSITIONING_SYSTEM,
        user_prompt=(
            _commercial_positioning_user_prompt_en if is_en else _commercial_positioning_user_prompt
        )(ctx, _genre_profile),
        fallback=json.dumps(_build_commercial_fallback(ctx), ensure_ascii=False),
        template="conception_commercial_positioning",
        stage="conception.commercial_brief",
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(stage_llm_ids)
    if not commercial_brief:
        commercial_brief = _build_commercial_fallback(ctx)
    commercial_brief, deflavour_ids = await _rewrite_flavoured_copy(
        session,
        settings,
        brief=commercial_brief,
        is_en=is_en,
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(deflavour_ids)
    ctx["commercial_brief"] = commercial_brief
    conception_log.append({"round": 0, "agent": "commercial_commissioner", "brief": commercial_brief})

    # ── Round 1: Independent Proposals (parallelised) ──────────────
    # market ∥ (character → cast_reality_audit) ∥ world
    # cast_reality_audit depends on character_proposal, so it stays
    # serial within the character lane; the three lanes run in parallel.
    _market_fallback = json.dumps(ctx.get("existing_overrides", {}).get("market", {}), ensure_ascii=False)
    _character_fallback = json.dumps(ctx.get("existing_overrides", {}).get("character", {}), ensure_ascii=False)
    _world_fallback = json.dumps(ctx.get("existing_overrides", {}).get("world", {}), ensure_ascii=False)

    async def _market_lane() -> tuple[dict[str, Any], list[UUID]]:
        _emit("conception_market", {"round": 1, "agent": "market_strategist"})
        market_user_prompt = _attach_conception_methodology(
            (_market_user_prompt_en if is_en else _market_user_prompt)(ctx, _genre_profile),
            ctx=ctx,
            is_en=is_en,
            token_budget=600,
        )
        async def _run(lane_session: AsyncSession) -> tuple[dict[str, Any], list[UUID]]:
            return await _llm_call_json(
                lane_session, settings,
                role="planner",
                system_prompt=_MARKET_SYSTEM_EN if is_en else _MARKET_SYSTEM,
                user_prompt=market_user_prompt,
                fallback=_market_fallback,
                template="conception_market",
                stage="conception.market",
                language=str(ctx.get("language") or "zh-CN"),
                degradation_tracker=degradation_tracker,
                degradation_component="market_strategist",
            )

        return await run_in_isolated_session(session, _run)

    async def _character_lane() -> tuple[dict[str, Any], list[UUID], list[UUID]]:
        """character_architect → cast_reality_audit (serial within lane)."""
        _emit("conception_character", {"round": 1, "agent": "character_architect"})
        character_user_prompt = _attach_conception_methodology(
            (_character_user_prompt_en if is_en else _character_user_prompt)(ctx, _genre_profile),
            ctx=ctx,
            is_en=is_en,
            token_budget=800,
        )
        async def _run(
            lane_session: AsyncSession,
        ) -> tuple[dict[str, Any], list[UUID], list[UUID]]:
            proposal, ids = await _llm_call_json(
                lane_session, settings,
                role="planner",
                system_prompt=_CHARACTER_SYSTEM_EN if is_en else _CHARACTER_SYSTEM,
                user_prompt=character_user_prompt,
                fallback=_character_fallback,
                template="conception_character",
                stage="conception.character",
                language=str(ctx.get("language") or "zh-CN"),
                degradation_tracker=degradation_tracker,
                degradation_component="character_architect",
            )
            # 职业现实审计闭环:三道账(年龄/职级/边界)不平就地修正字段,fail-open。
            proposal, cra_ids = await _audit_cast_reality(
                lane_session, settings,
                character_proposal=proposal,
                ctx=ctx, is_en=is_en,
                degradation_tracker=degradation_tracker,
            )
            return proposal, ids, cra_ids

        return await run_in_isolated_session(session, _run)

    async def _world_lane() -> tuple[dict[str, Any], list[UUID]]:
        _emit("conception_world", {"round": 1, "agent": "world_builder"})
        world_user_prompt = _attach_conception_methodology(
            (_world_user_prompt_en if is_en else _world_user_prompt)(ctx, _genre_profile),
            ctx=ctx,
            is_en=is_en,
            token_budget=800,
        )
        async def _run(lane_session: AsyncSession) -> tuple[dict[str, Any], list[UUID]]:
            return await _llm_call_json(
                lane_session, settings,
                role="planner",
                system_prompt=_WORLD_SYSTEM_EN if is_en else _WORLD_SYSTEM,
                user_prompt=world_user_prompt,
                fallback=_world_fallback,
                template="conception_world",
                stage="conception.world",
                language=str(ctx.get("language") or "zh-CN"),
                degradation_tracker=degradation_tracker,
                degradation_component="world_builder",
            )

        return await run_in_isolated_session(session, _run)

    _existing = ctx.get("existing_overrides", {})
    _round_one = await _run_required_conception_lanes(
        market_lane=_market_lane,
        character_lane=_character_lane,
        world_lane=_world_lane,
        fallbacks={
            "market_strategist": (_existing.get("market", {}), []),
            "character_architect": (_existing.get("character", {}), [], []),
            "world_builder": (_existing.get("world", {}), []),
        },
        tracker=degradation_tracker,
        quality_mode=getattr(settings.pipeline, "quality_mode", "closure"),
    )
    market_proposal, _market_ids = _round_one["market_strategist"]
    character_proposal, _character_ids, _cra_ids = _round_one["character_architect"]
    world_proposal, _world_ids = _round_one["world_builder"]

    llm_run_ids.extend(_market_ids)
    llm_run_ids.extend(_character_ids)
    llm_run_ids.extend(_cra_ids)
    llm_run_ids.extend(_world_ids)

    market_proposal = market_proposal or ctx.get("existing_overrides", {}).get("market", {})
    character_proposal = character_proposal or ctx.get("existing_overrides", {}).get("character", {})
    world_proposal = world_proposal or ctx.get("existing_overrides", {}).get("world", {})

    conception_log.append({"round": 1, "agent": "market_strategist", "proposal": market_proposal})
    conception_log.append({"round": 1, "agent": "character_architect", "proposal": character_proposal})
    if character_proposal.get("reality_audit_notes"):
        conception_log.append({
            "round": 1, "agent": "cast_reality_auditor",
            "notes": character_proposal.get("reality_audit_notes"),
        })
    conception_log.append({"round": 1, "agent": "world_builder", "proposal": world_proposal})

    # ── Round 2: Cross-Review ───────────────────────────────────────
    _emit("conception_review", {"round": 2, "agent": "chief_editor"})
    review_market = _compact_conception_proposal(market_proposal)
    review_character = _compact_conception_proposal(character_proposal)
    review_world = _compact_conception_proposal(world_proposal)
    review_user_prompt = _attach_conception_methodology(
        (_review_user_prompt_en if is_en else _review_user_prompt)(
            ctx,
            review_market,
            review_character,
            review_world,
            _genre_profile,
        ),
        ctx=ctx,
        is_en=is_en,
        token_budget=500,
    )
    review_result, stage_llm_ids = await _llm_call_json(
        session, settings,
        role="critic",
        system_prompt=_REVIEW_SYSTEM_EN if is_en else _REVIEW_SYSTEM,
        user_prompt=review_user_prompt,
        fallback='{"overall_coherence_score": 0.7, "contradictions": [], "gaps": [], '
                 '"market_suggestions": [], "character_suggestions": [], "world_suggestions": [], '
                 '"name_quality_issues": [], "premise_seeds": []}',
        template="conception_review",
        stage="conception.review",
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(stage_llm_ids)
    conception_log.append({"round": 2, "agent": "chief_editor", "review": review_result})

    # ── Round 2.5: Creative Exploration (anti-cliché) ────────────────
    _cat = resolve_novel_category(ctx.get("genre", ""), ctx.get("sub_genre"))
    if _cat and _cat.quality_traps:
        _emit("conception_creative_exploration", {"round": 2.5, "agent": "creative_explorer"})
        exploration_result, stage_llm_ids = await _creative_exploration(
            session, settings,
            ctx=ctx,
            market=market_proposal,
            character=character_proposal,
            world=world_proposal,
            review=review_result,
            category=_cat,
            is_en=is_en,
        )
        llm_run_ids.extend(stage_llm_ids)
        exploration_result = exploration_result or {}
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
    finalize_user_prompt = _attach_conception_methodology(
        (_finalize_user_prompt_en if is_en else _finalize_user_prompt)(ctx, market_proposal, character_proposal, world_proposal, review_result, _genre_profile),
        ctx=ctx,
        is_en=is_en,
        token_budget=800,
    )
    final_result, stage_llm_ids = await _llm_call_json(
        session, settings,
        role="editor",
        system_prompt=_FINALIZE_SYSTEM_EN if is_en else _FINALIZE_SYSTEM,
        user_prompt=finalize_user_prompt,
        fallback=_build_fallback_final(ctx, market_proposal, character_proposal, world_proposal),
        template="conception_finalize",
        stage="conception.final",
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(stage_llm_ids)
    conception_log.append({"round": 3, "agent": "project_director", "final": final_result})

    # 必裁回执核对（warn-only）：矛盾清单非空而 director 没交 review_adjudications
    # =矛盾可能直通成稿。零杀权，只留痕——回执数量与矛盾数的比对进 conception_log，
    # 供事后审计与后续校准（真机观测一批后再谈这里要不要挣重生）。
    try:
        _review_contradictions = [
            str(x).strip()
            for x in (review_result.get("contradictions") or [])
            if str(x).strip()
        ]
        _review_gaps = [
            str(x).strip() for x in (review_result.get("gaps") or []) if str(x).strip()
        ]
        _adjudications = [
            str(x).strip()
            for x in (final_result.get("review_adjudications") or [])
            if str(x).strip()
        ]
        if _review_contradictions or _review_gaps:
            _issues_total = len(_review_contradictions) + len(_review_gaps)
            conception_log.append(
                {
                    "round": 3,
                    "agent": "review_adjudication_audit",
                    "issues_total": _issues_total,
                    "adjudications_returned": len(_adjudications),
                    "adjudications": _adjudications,
                }
            )
            if not _adjudications:
                logger.warning(
                    "conception finalize: chief_editor raised %d contradiction/gap "
                    "item(s) but director returned no review_adjudications — "
                    "contradictions may ship unadjudicated",
                    _issues_total,
                )
    except Exception:
        logger.debug("review adjudication audit failed (non-fatal)", exc_info=True)

    # Mechanism echo screen + anti-debt gate: the avoid-list and prompt guardrail
    # are not enough — the model still (a) absorbs the forbidden vocabulary as
    # material (verbatim premise openings, ledger-named golden fingers) and
    # (b) defaults cultivation costs to debt/ledger framing. One focused finalize
    # retry with the specific problems named; keep whichever result is cleaner.
    # Cross-book/default-motif and explicit-option violations fail closed after
    # the bounded repair; a polluted book must never be persisted as success.
    _unresolved_concept_guard: tuple[str, ...] = ()
    _detected_concept_guard: tuple[str, ...] = ()
    try:
        _avoid_entries = list(ctx.get("avoid_mechanisms") or [])
        _echo_contract = ctx.get("genre_intent_contract")
        _echo_contract = _echo_contract if isinstance(_echo_contract, Mapping) else {}
        _echo_user_intent_text = " ".join(
            [
                *_normalize_string_list(_echo_contract.get("tags")),
                *_normalize_string_list(_echo_contract.get("user_tags")),
                str(explicit_concept_seed or ""),
            ]
        )
        echo_report = _mechanism_echo_report(
            final_result,
            _avoid_entries,
            genre=str(ctx.get("genre") or "") or None,
            sub_genre=str(ctx.get("sub_genre") or "") or None,
            user_intent_text=_echo_user_intent_text,
        )
        # Debt + death-revival gates: fire only when the user did NOT ask for the
        # theme and the finalized concept leans on ledger framing or the worn
        # death-revival template. Intent is read from the frozen user snapshot (C3),
        # and the scan covers golden_finger + premise + synopsis + champion hook
        # fields — not just golden_finger+premise — so residue that saturated the
        # synopsis/hook (as in「龙椅上坐着我亡夫」: 认账/讨账 + 借尸还魂) is caught.
        _debt_ok = _user_requested_debt(ctx)
        _death_ok = _user_requested_death_revival(ctx)
        # The ontology equivalent of the two flags above: the text the user
        # explicitly supplied. Terms they typed themselves are never "drift".
        _ontology_user_seed = (
            explicit_concept_seed
            or (
                str(concept_bundle.one_liner or concept_bundle.reader_promise)
                if concept_bundle is not None
                else str(getattr(selected_hook_spec, "one_liner", "") or "")
            )
        )
        _death_default_ok = bool(
            _death_ok
            or _mentions_death_theme(_ontology_user_seed)
            or _ZH_DEFAULT_MOTIF_RE.search(_ontology_user_seed)
        )

        def _concept_scan_blob(result: Any) -> str:
            if not isinstance(result, dict):
                return ""
            prof = result.get("writing_profile")
            hc = ctx.get("high_concept") if isinstance(ctx.get("high_concept"), dict) else {}
            return _conception_ontology_story_surface(
                title=str(result.get("title") or ""),
                premise=str(result.get("premise") or ""),
                synopsis="\n".join(
                    (str(result.get("synopsis") or ""), str(result.get("blurb") or ""))
                ),
                tags=[],
                writing_profile=prof if isinstance(prof, Mapping) else None,
                high_concept=hc,
                story_spine=None,
            )

        # Motif amplification (2026-08-09). Written when the debt/ledger police
        # was a `return False` shim; that detector has since been revived
        # (2026-08-14) and both now run side by side. This one carries
        # no vocabulary: it asks whether ONE token the approved concept barely
        # used ended up owning the reader promise, the golden finger, the
        # relationship mode, the power system AND the per-chapter rule at once.
        # Advisory by design — it earns a regeneration round, never a block.
        # STORY fields of the champion only. Flattening the whole winner dict
        # put judge_reason / rejected_reason / seriality_judge into the baseline
        # — control-plane prose that quotes the story's own words back at it.
        # That inflates the denominator and understates lift, so the gate
        # under-detects (2026-08-09 A/B run 1: production reported the rewrite
        # clean while the shipped profile still had a term at lift 3.0 measured
        # against the story facts alone). Reuses the field list the ontology
        # tripwire already trusts for the same reason.
        _approved_source = "\n".join(
            piece
            for piece in (
                str(explicit_concept_seed or ""),
                _ontology_user_seed,
                _champion_story_text(ctx.get("high_concept")),
            )
            if piece.strip()
        )
        motif_hits = _motif_amplification_hits(final_result, _approved_source)
        _final_blob = _concept_scan_blob(final_result)
        debt_hit = (not _debt_ok) and bool(
            _is_debt_dominated_mechanism(_final_blob)
        )
        death_hit = (not _death_default_ok) and bool(
            _is_death_revival_dominated(_final_blob)
            or _is_anonymous_death_dominated(_final_blob)
            or _contains_default_death_motif(_final_blob)
        )
        from bestseller.services.concept_tournament import (
            _creation_intent_content_violations,
        )

        _contract = ctx.get("genre_intent_contract") or {}
        _explicit_enhancers = _contract.get("explicit_enhancers") or {}
        intent_violations = _creation_intent_content_violations(
            _final_blob,
            tone_preference=str(_contract.get("tone_preference") or ""),
            effect_skills=list(_explicit_enhancers.get("effect_skills") or []),
            cost_style=str(_explicit_enhancers.get("cost_style") or ""),
        )

        def _ontology_hits(result: Any) -> tuple[str, ...]:
            # Native-genre modern-drift (职场/停尸房/尸检/法医/APP). The final
            # ontology tripwire is fail-CLOSED and kills the book; catch drift here
            # so the concept gets ONE regeneration chance to self-correct first.
            if genre_intent_contract is None:
                return ()
            try:
                from bestseller.services.genre_intent_contract import (
                    detect_genre_native_ontology_violations,
                )

                hits = detect_genre_native_ontology_violations(
                    _concept_scan_blob(result), genre_intent_contract
                )
                # Same user-intent exemption its two siblings above already
                # apply (``_debt_ok`` / ``_death_ok``) and the tournament-winner
                # gate applies: a word the user typed into their own seed is a
                # choice, not drift. Without it this burns a regeneration round
                # trying to "correct" the user's own premise.
                if hits and _ontology_user_seed:
                    hits = tuple(t for t in hits if t not in _ontology_user_seed)
                return hits
            except Exception:
                return ()

        ontology_hit = _ontology_hits(final_result)
        # 事件级沉重度（2026-08-23）：情绪词表测不出「用事件写的沉重」——
        # 真机验证书 8 原稿在 tone=light 下写「每夜十个当晚必死之人／咽气前
        # ／人头账／拆骨」，11 个情绪形容词一个没命中，确定性判据零违规。
        # 与 debt_hit / motif_hits 同族：只挣一次重生 + 留痕，**不进 detected**。
        from bestseller.services.heavy_tone_judge import (
            detect_heavy_tone_events,
            render_heavy_tone_feedback,
        )

        heavy_tone_hits = await detect_heavy_tone_events(
            session,
            settings,
            text=_concept_scan_blob(final_result),
            tone_preference=str(_contract.get("tone_preference") or ""),
            project_id=None,
        )
        # 基线留痕（2026-08-24）：扫描**跑完**这件事本身必须落库。此前回执只在
        # 有命中时才写，于是「门判干净」和「门在这行之前就抛异常被上面那个
        # except 吞掉」在事后长得一模一样 —— 真机书9 查了一整轮才靠离线复现
        # 才敢下结论。有命中时下方会整块覆盖它。
        ctx["default_family_report"] = {
            "scanned": True,
            "detected": bool(debt_hit),
            "after_retry": bool(debt_hit),
            "adopted_retry": False,
            "resolved": not debt_hit,
            "blocking": False,
            "user_requested": bool(_debt_ok),
            "family_hits": list(_default_debt_family_hits(_final_blob)),
        }
        if (
            echo_report
            or debt_hit
            or death_hit
            or ontology_hit
            or intent_violations
            or motif_hits
            or heavy_tone_hits
        ):
            detected: list[str] = []
            # 只有**逐字重合片段**够格阻断建书。零散 bigram 照旧留痕、照旧换来
            # 一次重生，但不进 detected——与紧下方 debt_hit、以及本块末尾
            # motif_hits 的处置完全同族（2026-08-23 真机：验证书 8 因
            # shared_span="" + 「悬疑/权谋/里那」三条噪声被处决，
            # project_created=false；PMI 与文档频率两条判据都实测无法把这类
            # 噪声与真机制词分开，所以问题在证据等级而不在词表）。
            if _echo_report_has_hard_evidence(echo_report):
                detected.append("跨书机制回声污染")
            # debt_hit 只挣一次重生，**不进 detected**（2026-08-14 真机误杀）：
            # detected 会变成 _detected_concept_guard 并 raise，把整本书打死。
            # 一本弃婴测灵根的玄幻书只因通篇偶尔出现「旧账」就在构思阶段被
            # 处决——这正是 8·2 母题警察退役的死因（命令写代价，再因代价杀书）。
            # 与 motif_amplification 同待遇：值一次重写，不构成拒绝建书的理由。
            if death_hit:
                detected.append("非用户指定的沉重开局母题污染")
            if ontology_hit:
                # Name the offending tokens. The bare label "题材本体漂移" sent a
                # user to a dead end (2026-08-05): the book was gone, the error
                # said only that something drifted, and finding the single word
                # responsible took a full forensic pass over the task events.
                detected.append(
                    "题材本体漂移（命中：" + "、".join(ontology_hit) + "）"
                )
            detected.extend(intent_violations)
            # Motif amplification stays OUT of ``detected``: that tuple becomes
            # ``_detected_concept_guard`` and can terminate the book. A word
            # occupying every axis is a quality defect worth one rewrite, not a
            # reason to refuse to create the project — the whole point of the
            # 2026-08-02 retirement was that this class of gate kills books.
            _detected_concept_guard = tuple(dict.fromkeys(detected))
            _emit(
                "conception_mechanism_echo_retry",
                {
                    "collisions": [str(r.get("title") or "") for r in echo_report],
                    "debt_dominated": debt_hit,
                    "death_revival": death_hit,
                    "ontology_drift": list(ontology_hit),
                    "creation_intent_violations": list(intent_violations),
                    "motif_amplification": [m.to_dict() for m in motif_hits],
                    "heavy_tone_events": [h.to_dict() for h in heavy_tone_hits],
                },
            )
            retry_feedback = _render_mechanism_echo_feedback(echo_report, is_en=is_en)
            if heavy_tone_hits:
                retry_feedback += render_heavy_tone_feedback(heavy_tone_hits)
            if debt_hit:
                # 把这次构思自己写下的族内词逐字引回去——只说「换个家族」
                # 而不点明踩到了什么，模型会把「旧账」换成「欠条」再换成
                # 「人情债」，始终在同一族里（真机验证书9：原稿与重写稿双双
                # 判定债务族支配）。引回模型自己的词不引入新词汇，不是种词。
                from bestseller.services.anti_default_motif import (
                    default_debt_family_matches,
                )

                retry_feedback += _render_debt_rewrite_feedback(
                    is_en=is_en,
                    matched_terms=default_debt_family_matches(
                        _concept_scan_blob(final_result)
                    ),
                )
            if death_hit:
                retry_feedback += _render_death_revival_rewrite_feedback(is_en=is_en)
            if ontology_hit:
                retry_feedback += _render_ontology_drift_rewrite_feedback(ontology_hit, is_en=is_en)
            if intent_violations:
                retry_feedback += (
                    "\n\n【重写要求 · 严格兑现建书选项】\n"
                    + "；".join(intent_violations)
                    + "。重做概念的情绪承诺、主角行动与场景引擎；不得只在标签里补词。"
                )
            if motif_hits:
                retry_feedback += _render_motif_amplification_feedback(
                    motif_hits, is_en=is_en
                )
            clean_retry_prompt = _pollution_retry_finalize_prompt(
                ctx,
                genre_profile=_genre_profile,
                is_en=is_en,
            )
            retry_result, retry_llm_ids = await _llm_call_json(
                session, settings,
                role="editor",
                system_prompt=_FINALIZE_SYSTEM_EN if is_en else _FINALIZE_SYSTEM,
                user_prompt=clean_retry_prompt + retry_feedback,
                fallback=json.dumps(final_result, ensure_ascii=False),
                template="conception_finalize_echo_retry",
                stage="conception.final_echo_retry",
                language=str(ctx.get("language") or "zh-CN"),
            )
            llm_run_ids.extend(retry_llm_ids)
            retry_report = _mechanism_echo_report(
                retry_result,
                _avoid_entries,
                genre=str(ctx.get("genre") or "") or None,
                sub_genre=str(ctx.get("sub_genre") or "") or None,
                user_intent_text=_echo_user_intent_text,
            )
            _retry_blob = _concept_scan_blob(retry_result)
            retry_debt = (not _debt_ok) and bool(
                _is_debt_dominated_mechanism(_retry_blob)
            )
            retry_death = (not _death_default_ok) and bool(
                _is_death_revival_dominated(_retry_blob)
                or _is_anonymous_death_dominated(_retry_blob)
                or _contains_default_death_motif(_retry_blob)
            )
            retry_ontology = _ontology_hits(retry_result)
            retry_intent_violations = _creation_intent_content_violations(
                _retry_blob,
                tone_preference=str(_contract.get("tone_preference") or ""),
                effect_skills=list(_explicit_enhancers.get("effect_skills") or []),
                cost_style=str(_explicit_enhancers.get("cost_style") or ""),
            )
            retry_motif_hits = _motif_amplification_hits(
                retry_result, _approved_source
            )
            adopted = _should_adopt_mechanism_retry(
                retry_result=retry_result,
                original_echo=echo_report,
                retry_echo=retry_report,
                original_hard=(
                    debt_hit,
                    death_hit,
                    bool(ontology_hit),
                    bool(intent_violations),
                ),
                retry_hard=(
                    retry_debt,
                    retry_death,
                    bool(retry_ontology),
                    bool(retry_intent_violations),
                ),
                original_motif_count=len(motif_hits),
                retry_motif_count=len(retry_motif_hits),
            )
            if adopted:
                final_result = retry_result
                _unresolved_concept_guard = ()
            else:
                _unresolved_concept_guard = _detected_concept_guard
            # 默认族的最终去向也必须留痕（同下方 motif 的理由）：它不再阻断
            # 建书，但用户有权知道成稿是否仍落在被过度复用的族里。
            _shipped_debt = retry_debt if adopted else debt_hit
            if debt_hit or retry_debt:
                ctx["default_family_report"] = {
                    "detected": bool(debt_hit),
                    "after_retry": bool(retry_debt),
                    "adopted_retry": bool(adopted),
                    "resolved": not _shipped_debt,
                    "blocking": False,
                }
                if _shipped_debt:
                    logger.warning(
                        "conception shipped inside an over-reused default family "
                        "(advisory, not blocking)"
                    )
            # Whichever version ships, record the motif verdict. An advisory
            # finding that leaves no trace is the same as no finding at all
            # (2026-07-25: a book "disappeared" only because its failure reason
            # was never surfaced anywhere the user could read).
            _shipped_motifs = retry_motif_hits if adopted else motif_hits
            if motif_hits or retry_motif_hits:
                ctx["motif_amplification_report"] = {
                    "detected": [m.to_dict() for m in motif_hits],
                    "after_retry": [m.to_dict() for m in retry_motif_hits],
                    "adopted_retry": adopted,
                    "resolved": not _shipped_motifs,
                }
                if _shipped_motifs:
                    logger.warning(
                        "conception shipped with motif amplification unresolved: %s",
                        "、".join(m.label for m in _shipped_motifs),
                    )
            conception_log.append(
                {
                    "round": 3,
                    "agent": "mechanism_echo_gate",
                    "collisions": echo_report,
                    "retry_collisions": retry_report,
                    "debt_dominated": debt_hit,
                    "retry_debt_dominated": retry_debt,
                    "creation_intent_violations": list(intent_violations),
                    "retry_creation_intent_violations": list(retry_intent_violations),
                    "motif_amplification": [m.to_dict() for m in motif_hits],
                    "retry_motif_amplification": [
                        m.to_dict() for m in retry_motif_hits
                    ],
                    "heavy_tone_events": [h.to_dict() for h in heavy_tone_hits],
                    "adopted_retry": adopted,
                }
            )
    except Exception:
        # The original finalize was already proven polluted.  A retry/scanner
        # exception cannot erase that evidence and silently promote the book.
        _unresolved_concept_guard = _detected_concept_guard
        logger.warning("conception pollution retry failed; blocking original finalize", exc_info=True)
    if _unresolved_concept_guard:
        from bestseller.services.concept_contract import ConceptContractError

        _cc_exc = ConceptContractError(
            [
                "CONCEPTION_POLLUTION_GATE_UNRESOLVED: "
                + "；".join(_unresolved_concept_guard)
                + "。已阻止污染构思进入建书与大纲。"
            ]
        )
        # 被拦截的构思照样落档（2026-08-19：污染门毙书后什么档案都没留，
        # 撞了哪本书只能从任务事件里人肉挖）。与 AppealBarNotMetError 同款：
        # 日志挂在异常上带出 async 帧，由 web 的 sync handler 落盘。
        _cc_exc.conception_log = list(conception_log)
        raise _cc_exc

    # A frontend field is not "effective" merely because it appears in a
    # prompt.  When the user supplied a concrete story seed, prove that the
    # materialized conception still describes that same book.  Repair is a
    # bounded judge→editor→judge loop: early rounds may preserve compatible
    # elaboration, while the final round deliberately becomes conservative and
    # removes invented power/cost systems.  Only an unclosed third round blocks
    # project creation, so a model's first imperfect edit cannot strand a book.
    if explicit_concept_seed and isinstance(final_result, Mapping):
        seed_fidelity, seed_judge_ids = await _judge_explicit_concept_seed_fidelity(
            session,
            settings,
            concept_seed=explicit_concept_seed,
            final_result=final_result,
            ctx=ctx,
        )
        llm_run_ids.extend(seed_judge_ids)
        conception_log.append(
            {
                "round": 3,
                "agent": "explicit_seed_fidelity_judge",
                "report": seed_fidelity,
            }
        )
        if not seed_fidelity.get("passed") and not any(
            item.get("source") == "deterministic_invariant"
            for item in seed_fidelity.get("hard_conflicts", [])
            if isinstance(item, Mapping)
        ):
            seed_fidelity, arbitration_ids = (
                await _arbitrate_explicit_seed_fidelity_report(
                    session,
                    settings,
                    concept_seed=explicit_concept_seed,
                    final_result=final_result,
                    challenged_report=seed_fidelity,
                    ctx=ctx,
                )
            )
            llm_run_ids.extend(arbitration_ids)
            conception_log.append(
                {
                    "round": 3,
                    "agent": "explicit_seed_fidelity_arbitrator",
                    "attempt": 0,
                    "report": seed_fidelity,
                }
            )
        max_seed_repair_attempts = 3
        repair_attempt = 0
        while not seed_fidelity.get("passed") and repair_attempt < max_seed_repair_attempts:
            repair_attempt += 1
            _emit(
                "conception_seed_fidelity_repair",
                {
                    "attempt": repair_attempt,
                    "max_attempts": max_seed_repair_attempts,
                    "hard_conflict_count": len(seed_fidelity.get("hard_conflicts") or []),
                },
            )
            final_result, seed_repair_ids = await _repair_final_result_to_explicit_seed(
                session,
                settings,
                concept_seed=explicit_concept_seed,
                final_result=final_result,
                report=seed_fidelity,
                ctx=ctx,
                attempt=repair_attempt,
                max_attempts=max_seed_repair_attempts,
            )
            llm_run_ids.extend(seed_repair_ids)
            seed_fidelity, repaired_judge_ids = (
                await _judge_explicit_concept_seed_fidelity(
                    session,
                    settings,
                    concept_seed=explicit_concept_seed,
                    final_result=final_result,
                    ctx=ctx,
                )
            )
            llm_run_ids.extend(repaired_judge_ids)
            if not seed_fidelity.get("passed") and not any(
                item.get("source") == "deterministic_invariant"
                for item in seed_fidelity.get("hard_conflicts", [])
                if isinstance(item, Mapping)
            ):
                seed_fidelity, arbitration_ids = (
                    await _arbitrate_explicit_seed_fidelity_report(
                        session,
                        settings,
                        concept_seed=explicit_concept_seed,
                        final_result=final_result,
                        challenged_report=seed_fidelity,
                        ctx=ctx,
                    )
                )
                llm_run_ids.extend(arbitration_ids)
            conception_log.append(
                {
                    "round": 3,
                    "agent": "explicit_seed_fidelity_repair",
                    "attempt": repair_attempt,
                    "repaired_report": seed_fidelity,
                }
            )
        if not seed_fidelity.get("passed"):
            from bestseller.services.concept_contract import ConceptContractError

            conflicts = seed_fidelity.get("hard_conflicts") or []
            conflict_lines = [
                str(item.get("reason") or item.get("fact") or "创建意图未保留")
                for item in conflicts
                if isinstance(item, Mapping)
            ]
            raise ConceptContractError(
                [
                    "用户明确故事创意与构思终稿不一致，三轮自动重生后仍未闭合；"
                    "已阻止错误书籍进入大纲。",
                    *(conflict_lines[:6] or ["核心人物、机制或目标发生替换。"]),
                ]
            )

    # Extract final outputs with fallbacks
    writing_profile = final_result.get("writing_profile", {})
    premise = _safe_get(final_result, "premise", "")
    title = _safe_get(final_result, "title", "")

    # Ensure writing_profile has all required sections
    writing_profile = _ensure_complete_profile(writing_profile, ctx, market_proposal, character_proposal, world_proposal)
    writing_profile = _apply_commercial_brief_to_profile(writing_profile, commercial_brief)
    # Persist Agent ① methodology onto the profile so the planner (Agents ②③)
    # can fuse it into book_spec/outline and grow world/cast/plot from it.
    _methodology = ctx.get("concept_methodology")
    if isinstance(_methodology, dict) and _methodology:
        writing_profile["concept_methodology"] = _methodology
    if selected_hook_spec is not None:
        market_profile = writing_profile.setdefault("market", {})
        if isinstance(market_profile, dict):
            # logline/reader_promise 不在此处直写：selected_hook_spec.one_liner 是候选池
            # 机械选优产物，未必贴合本书语境（真机案例：模板钩子直接覆盖成病句）。
            # 三段适配决策见故事脊柱计算之后的止血块（需要 story_spine 兜底）。
            market_profile["anti_commonsense_hook"] = selected_hook_spec.model_dump(mode="json")
    concept_bundle = coerce_concept_lab_bundle(ctx.get("concept_lab"))
    if concept_bundle is not None:
        market_profile = writing_profile.setdefault("market", {})
        if isinstance(market_profile, dict):
            market_profile["logline"] = concept_bundle.one_liner
            # 出货的那份必须也是被清洗过的那份（2026-08-24 真机）：上面 6433 行
            # 已经把 commercial_brief 洗干净，这里若直接用 concept_bundle 覆盖，
            # 清洗就白做了。比较式挑分最低者，不发明文案。
            market_profile["reader_promise"] = prefer_cleaner_reader_copy(
                [
                    (commercial_brief or {}).get("reader_promise"),
                    concept_bundle.reader_promise,
                    concept_bundle.one_liner,
                ]
            )
            market_profile["concept_lab"] = concept_bundle.model_dump(mode="json")
            market_profile["title_seed"] = (
                concept_bundle.title_seeds[0].text
                if concept_bundle.title_seeds
                else ""
            )
            # 折叠近重复，不能只 dict.fromkeys（2026-08-24 真机书9）：卖点在
            # 1714 行已经折叠过一次，这里再拼 hype_targets 时只按逐字去重，
            # 改写过的同一条卖点全部存活 —— 先折叠后合并等于折叠白做。
            # 同时剥掉从 prompt 示例抄来的「卖点N：」序号标签。
            market_profile["selling_points"] = fold_near_duplicate_points(
                [
                    _stripped
                    for _stripped in (
                        strip_enumeration_label(str(point))
                        for point in (
                            *list(market_profile.get("selling_points") or []),
                            *list(concept_bundle.hype_targets[:6]),
                        )
                    )
                    if _stripped
                ]
            )

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

    # Validate the seed title before the platform title workflow rewrites it.
    title = (title or "").strip()
    _is_valid_title = bool(title) and (
        (not is_en and 2 <= len(title) <= 30)
        or (is_en and 2 <= len(title.split()) <= 12 and len(title) <= 90)
    )
    if not _is_valid_title:
        # Try to extract a usable seed from a longer generated one.
        if title and not is_en and len(title) > 10:
            import re as _re_title
            m = _re_title.match(r"[\u4e00-\u9fff]{2,18}", title)
            if m:
                title = m.group(0)
                _is_valid_title = True
    if not _is_valid_title:
        # 产品红线：题材名 ≠ 书名。绝不用 genre/sub_genre/description 当书名。
        # 留空种子，交给下游 platform title workflow 的「故事DNA兜底 + LLM 口播重写」
        # 路径产出一个真正的书名（select_primary_platform_title 对空标题会走
        # _provisional_primary_candidate → build_story_dna_fallback_title）。
        title = ""

    # Extract synopsis and tags from the finalized result
    synopsis = _safe_get(final_result, "synopsis", "").strip()
    if len(synopsis) > 500:
        synopsis = truncate_at_sentence(synopsis, 500)
    raw_tags = final_result.get("tags", [])
    tags = [str(t).strip() for t in raw_tags if isinstance(t, str) and t.strip()][:10]

    # ── 设定/逻辑框架层(2026-07-08):造世前置→机制因果账→机制极性→跨产物事实台账 ──
    # 用户终审"不知道在讲啥/没逻辑/没爽感"的框架修复，全部 fail-open。
    world_model_payload: dict[str, Any] = {}
    try:
        world_model_payload, _wm_obj, _wm_ids = await _derive_conception_world_model(
            session, settings, premise=premise, ctx=ctx,
        )
        llm_run_ids.extend(_wm_ids)
        if _wm_obj is not None:
            conception_log.append({
                "round": 3,
                "agent": "world_model_deriver",
                "law_count": len(_wm_obj.world_laws),
                "axioms": list(_wm_obj.axioms),
            })
            premise, writing_profile, _cause_ids, _cause_notes = await _audit_mechanism_causality(
                session, settings,
                premise=premise, writing_profile=writing_profile,
                world_model=_wm_obj, ctx=ctx, is_en=is_en,
            )
            llm_run_ids.extend(_cause_ids)
            if _cause_notes:
                conception_log.append({
                    "round": 3, "agent": "mechanism_causality_auditor", "notes": _cause_notes,
                })
    except Exception:
        logger.warning("mechanism causality grounding failed; skipping", exc_info=True)

    try:
        _character = writing_profile.get("character") if isinstance(writing_profile, dict) else None
        _golden_finger = str(_character.get("golden_finger") or "") if isinstance(_character, dict) else ""
        _growth_curve = str(_character.get("growth_curve") or "") if isinstance(_character, dict) else ""
        _gf_violations = [
            v for v in (
                _detect_golden_finger_optout_violation(golden_finger=_golden_finger, ctx=ctx),
                _detect_golden_finger_polarity_violation(
                    golden_finger=_golden_finger, growth_curve=_growth_curve,
                    synopsis=synopsis, premise=premise,
                ),
            ) if v
        ]
        if _gf_violations and isinstance(_character, dict):
            _new_gf, _new_gc, _gf_ids = await _polish_golden_finger_mechanism(
                session, settings,
                golden_finger=_golden_finger, growth_curve=_growth_curve,
                violations=_gf_violations, ctx=ctx, is_en=is_en,
            )
            llm_run_ids.extend(_gf_ids)
            _new_character = dict(_character)
            _new_character["golden_finger"] = _new_gf
            _new_character["growth_curve"] = _new_gc
            writing_profile = dict(writing_profile)
            writing_profile["character"] = _new_character
            conception_log.append({
                "round": 3, "agent": "golden_finger_polarity_gate", "violations": _gf_violations,
            })
    except Exception:
        logger.warning("golden finger polarity gate failed; skipping", exc_info=True)

    try:
        _roster = _extract_cast_age_roster(character_proposal)
        _mismatches = [
            *_detect_cross_artifact_age_mismatches(synopsis, _roster),
            *_detect_cross_artifact_age_mismatches(premise, _roster),
        ]
        if _mismatches:
            premise, synopsis, _fact_ids = await _reconcile_cross_artifact_facts(
                session, settings,
                premise=premise, synopsis=synopsis, mismatches=_mismatches, ctx=ctx,
            )
            llm_run_ids.extend(_fact_ids)
            conception_log.append({
                "round": 3, "agent": "cross_artifact_fact_gate", "mismatches": _mismatches,
            })
    except Exception:
        logger.warning("cross-artifact fact reconciliation failed; skipping", exc_info=True)

    # ── 故事脊柱:提取→确定性验收→一次聚焦重写(fail-open) ────────────────
    from bestseller.services.story_spine import validate_story_spine

    story_spine = (
        final_result.get("story_spine")
        if isinstance(final_result.get("story_spine"), dict)
        else {}
    )
    _spine_violations = validate_story_spine(story_spine)
    if _spine_violations:
        story_spine, _spine_ids = await _polish_story_spine(
            session, settings,
            spine=story_spine, violations=_spine_violations,
            premise=premise, synopsis=synopsis, ctx=ctx,
        )
        llm_run_ids.extend(_spine_ids)
        _spine_violations = validate_story_spine(story_spine)
    conception_log.append({
        "round": 3,
        "agent": "story_spine_gate",
        "spine": story_spine,
        "violations": _spine_violations,
    })
    if _spine_violations:
        logger.warning(
            "story spine failed deterministic gate after polish: %s", _spine_violations
        )

    # ── Unified concept contract: champion → HookCard + SerialityProof + Spine v2 ──
    concept_contract: dict[str, Any] = {}
    if _ct_result is not None and getattr(_ct_result, "winner", None) is not None:
        from bestseller.services.concept_contract import (
            ConceptContractError,
            build_concept_contract,
            validate_concept_contract,
        )

        contract_winner: object = _ct_result.winner
        from bestseller.services.book_design import (
            extract_creation_protagonist_name,
        )

        authoritative_name = extract_creation_protagonist_name({"premise": premise})
        winner_payload = (
            dict(contract_winner)
            if isinstance(contract_winner, Mapping)
            else dict(contract_winner.to_dict())
            if callable(getattr(contract_winner, "to_dict", None))
            else {}
        )
        winner_identity = str(winner_payload.get("protagonist_identity") or "")
        spine_identity = str(story_spine.get("who") or "")
        contract_surface = json.dumps(
            {"winner": winner_payload, "story_spine": story_spine},
            ensure_ascii=False,
        )
        infant_body_story = any(
            marker in f"{premise}\n{synopsis}"
            for marker in ("婴儿", "襁褓", "新生儿", "三个月")
        )
        infant_precision_candidate = infant_body_story and any(
            marker in contract_surface
            for marker in (
                "精确假哭",
                "啼哭节奏",
                "注视时长",
                "注视方向",
                "抓握力度",
                "呼吸深浅",
                "操控萧崇",
                "三连暗号",
                "竞价解读",
            )
        )
        if authoritative_name and (
            authoritative_name not in winner_identity
            or authoritative_name not in spine_identity
            or infant_precision_candidate
        ):
            contract_winner, _lineage_ids = await _reconcile_concept_seed_with_final_premise(
                session,
                settings,
                winner=contract_winner,
                premise=premise,
                synopsis=synopsis,
                writing_profile=writing_profile,
                authoritative_name=authoritative_name,
                ctx={**ctx, "chapter_count": chapter_count},
            )
            llm_run_ids.extend(_lineage_ids)
            reconciled_seed = (
                contract_winner if isinstance(contract_winner, Mapping) else {}
            )
            reconciled_identity = str(
                reconciled_seed.get("protagonist_identity") or ""
            )
            if authoritative_name not in reconciled_identity:
                raise ConceptContractError(
                    [
                        "最终 premise 与概念冠军契约主角不一致，LLM 对齐后仍未闭合："
                        f"premise={authoritative_name}，contract={reconciled_identity or '缺失'}"
                    ]
                )
            story_spine = {
                **story_spine,
                "who": reconciled_identity,
                "wants": str(
                    reconciled_seed.get("protagonist_private_desire") or ""
                ),
                "why_now": str(reconciled_seed.get("opening_crisis") or ""),
                "against": str(reconciled_seed.get("opponent_system") or ""),
                "stakes": str(reconciled_seed.get("emotional_promise") or ""),
                "question": str(reconciled_seed.get("hook_question") or ""),
            }
            conception_log.append(
                {
                    "round": 3,
                    "agent": "concept_contract_final_premise_reconciliation",
                    "authoritative_protagonist": authoritative_name,
                    "old_winner_identity": winner_identity,
                    "old_spine_identity": spine_identity,
                    "new_winner_identity": reconciled_identity,
                    "infant_precision_candidate": infant_precision_candidate,
                }
            )

        concept_contract = build_concept_contract(
            winner=contract_winner,
            story_spine=story_spine,
            target_chapters=chapter_count,
            genre=str(ctx.get("genre") or genre_key),
            sub_genre=str(ctx.get("sub_genre") or ""),
        )
        _contract_violations = validate_concept_contract(
            concept_contract,
            target_chapters=chapter_count,
        )
        conception_log.append({
            "round": 3,
            "agent": "concept_contract_gate",
            "champion_id": concept_contract.get("champion_id"),
            "violations": _contract_violations,
            "capacity_report": (
                concept_contract.get("seriality_proof", {}).get("capacity_report", {})
            ),
        })
        if _contract_violations:
            raise ConceptContractError(_contract_violations)
        story_spine = dict(concept_contract["story_spine"])
    from bestseller.services.concept_contract import (
        require_conception_contract_for_target,
    )

    require_conception_contract_for_target(
        concept_contract,
        target_chapters=chapter_count,
    )

    # ── 钩子模板句止血(T4, 2026-07-09)──────────────────────────────────────
    # selected_hook_spec.one_liner 是候选池机械选优产物，未必贴合本书语境（真机
    # 案例：锦鲤代价钩子模板直接覆盖规则怪谈书的 reader_promise，产出模板插值
    # 病句）。三段决策：(1) 适配检查(病理+本书实体)通过→照旧写入；(2) 不过→一次
    # LLM 用本书实体改写，改写结果再查一遍；(3) 仍不过→spine 兜底(reader_promise=
    # story_spine.question，已由脊柱闸门保证是合格疑问句)，market.logline 保持
    # finalize 产出的原值不动。concept_bundle 存在时维持其原有优先级(不在此处覆盖)。
    # 适配结论要跟随 ConceptionResult.hook_spec 落库(L3验收发现:此前只写进
    # ctx["hook_spec"]，而返回值是 selected_hook_spec 的新 model_dump，flag 丢失)。
    _hook_one_liner_adapted_result: bool | None = None
    if selected_hook_spec is not None and concept_bundle is None:
        try:
            _hook_one_liner = selected_hook_spec.one_liner
            _protagonist_name = (
                str(character_proposal.get("protagonist_name") or "")
                if isinstance(character_proposal, dict) else ""
            )

            _adapted_one_liner = _hook_one_liner
            _one_liner_adapted = _hook_one_liner_is_adapted(
                _hook_one_liner, protagonist=_protagonist_name, title=title,
            )
            if not _one_liner_adapted:
                _adapted_one_liner, _adapt_ids = await _adapt_hook_one_liner(
                    session, settings,
                    one_liner=_hook_one_liner, title=title,
                    protagonist=_protagonist_name, premise=premise,
                    genre=str(ctx.get("genre") or genre or ""), is_en=is_en,
                )
                llm_run_ids.extend(_adapt_ids)
                _one_liner_adapted = _hook_one_liner_is_adapted(
                    _adapted_one_liner, protagonist=_protagonist_name, title=title,
                )

            market_profile = writing_profile.setdefault("market", {})
            if isinstance(market_profile, dict):
                if _one_liner_adapted:
                    market_profile["logline"] = _adapted_one_liner
                    market_profile["reader_promise"] = _adapted_one_liner
                else:
                    market_profile["reader_promise"] = story_spine.get("question") or premise
                    logger.warning(
                        "hook one_liner failed adaptation twice; falling back to "
                        "story_spine.question for reader_promise"
                    )
            _hook_spec_payload = ctx.get("hook_spec")
            if isinstance(_hook_spec_payload, dict):
                _hook_spec_payload["one_liner_adapted"] = _one_liner_adapted
            _hook_one_liner_adapted_result = _one_liner_adapted
            conception_log.append({
                "round": 3, "agent": "hook_one_liner_adaptation_gate",
                "adapted": _one_liner_adapted,
            })
        except Exception:
            logger.warning("hook one_liner adaptation failed (non-fatal)", exc_info=True)

    writing_profile = _sanitize_forbidden_default_motifs(writing_profile, is_en=is_en)
    premise = str(_sanitize_forbidden_default_motifs(premise, is_en=is_en))
    synopsis = str(_sanitize_forbidden_default_motifs(synopsis, is_en=is_en))
    title = str(_sanitize_forbidden_default_motifs(title, is_en=is_en))
    tags = [
        str(item).strip()
        for item in _sanitize_forbidden_default_motifs(tags, is_en=is_en)
        if str(item).strip()
    ][:10]

    # 书名工序的 logline 优先吃概念淘汰赛冠军的一句话高概念——真机《我靠签契
    # 改地脉》教训：书名从 premise(细节铺开)取材,丢掉了概念最独特的"同传/翻译官"
    # 维度;冠军 concept 本来就是≤60字的强概念句,正是书名候选想要的原料。
    _hc_concept = (
        str((ctx.get("high_concept") or {}).get("concept") or "")
        if isinstance(ctx.get("high_concept"), dict) else ""
    )
    title_profile = {
        "language": str(ctx.get("language") or "zh-CN"),
        "primary_title": title,
        "primary_category": str(ctx.get("genre") or ""),
        "secondary_category": str(ctx.get("sub_genre") or ""),
        "tags": tags,
        "logline": _hc_concept or premise,
        "short_intro": synopsis,
        "reader_promise": (
            writing_profile.get("market", {}).get("reader_promise")
            if isinstance(writing_profile.get("market"), dict)
            else ""
        ),
        "main_characters": [
            {
                # 构思阶段项目行还没有 metadata 可读，与同函数里既有的
                # `extract_creation_protagonist_name({"premise": premise})`
                # 保持一致：这一层以已批准构思正文为准。
                "name": _title_profile_protagonist(
                    metadata={},
                    premise=_hc_concept or premise,
                    is_en=is_en,
                ),
                "role": "主角" if not is_en else "Protagonist",
                "identity": (
                    writing_profile.get("character", {}).get("protagonist_archetype")
                    if isinstance(writing_profile.get("character"), dict)
                    else ""
                ),
            }
        ],
    }
    target_platform = (
        writing_profile.get("market", {}).get("platform_target")
        if isinstance(writing_profile.get("market"), dict)
        else ""
    )
    try:
        primary_title_candidate = select_primary_platform_title(
            title_profile,
            target_platform=str(target_platform or ""),
        )
        workflow_title = str(primary_title_candidate.get("title") or "").strip()
        if workflow_title:
            title = workflow_title
            title_workflow_primary = {
                "title": workflow_title,
                "platform_label": primary_title_candidate.get("platform_label"),
                "scope_label": primary_title_candidate.get("scope_label"),
                "pattern": primary_title_candidate.get("pattern"),
            }
            writing_profile.setdefault("market", {})["title_workflow_primary"] = (
                title_workflow_primary
            )
            # P2 (2026-06-03): single LLM platform-口播 revision for weak titles.
            adopted_title, was_revised, revision_llm_id = await _maybe_revise_platform_title(
                session,
                settings,
                title_profile=title_profile,
                primary_candidate=primary_title_candidate,
                target_platform=str(target_platform or ""),
                workflow_title=workflow_title,
            )
            if revision_llm_id is not None:
                llm_run_ids.append(revision_llm_id)
            if was_revised:
                title = adopted_title
                title_workflow_primary["title"] = adopted_title
                title_workflow_primary["pre_revision_title"] = workflow_title
                title_workflow_primary["llm_revised"] = True
    except Exception:
        logger.warning("Platform title workflow failed during conception", exc_info=True)

    # ── 书名淘汰赛（2026-08-21）──────────────────────────────────────────
    # 此前书名是单发调用（title_platform_revision 全书 1 次），而概念有 16 次
    # 判官 + 4 轮候选。现任书名也进场比，不是无条件替换。全程 fail-open：
    # 书名工序绝不允许阻断建书。
    _title_tournament_receipt: dict[str, Any] = {}
    # 2026-08-25：淘汰赛冠军必须留到 appeal 重生之后。真机 custom-xuanhuan-1787625194
    # 上，appeal 重生循环把 premise/简介/标签/**书名**当一个包重新生成，末尾
    # `report, premise, synopsis, tags, title = best` 整包覆盖了这里选出的冠军——
    # 出厂书名《逐出师门我颠大》在淘汰赛回执里一次都没出现过（冠军是
    # 《擀面胖婶的三息辨火》）。两个互不知情的书名子系统，后写的赢；连带把
    # 淘汰赛的确定性门与默认族降权一起绕过了。
    _tournament_title = ""
    try:
        _tt_title, _tt_receipt, _tt_run_ids = await _run_title_tournament(
            session,
            settings,
            incumbent=title,
            title_profile=title_profile,
            golden_finger=str(
                (writing_profile.get("character") or {}).get("golden_finger") or ""
            )
            if isinstance(writing_profile.get("character"), dict)
            else "",
            logline=_hc_concept or premise,
            genre_label=str(genre_intent_contract.genre_label or ""),
            platform_label=str(target_platform or ""),
            is_en=is_en,
            user_named_default_family=bool(
                _default_debt_family_hits(str(explicit_concept_seed or ""))
            ),
        )
        llm_run_ids.extend(_tt_run_ids)
        if _tt_receipt:
            # 注意不要写进 writing_profile——那里的额外键会被 extra=ignore 吃掉
            # （2026-08-22 首跑实测，既有的 title_workflow_primary 同样丢失）。
            _title_tournament_receipt = _tt_receipt
        if _tt_title.strip():
            title = _tt_title.strip()
            _tournament_title = title
    except Exception:
        logger.warning("Title tournament failed during conception", exc_info=True)

    # Final invariant: a book is never named after its genre, and never blank.
    # If the workflow errored or returned nothing usable, derive a clean,
    # genre-free name from the story DNA; only as an absolute last resort use a
    # neutral placeholder (still not a taxonomy label).
    if not title.strip() or is_bare_taxonomy_title(title):
        dna_fallback = build_story_dna_fallback_title(title_profile)
        # A taxonomy name must be replaced even when no DNA fallback exists, so
        # fall through to the neutral placeholder rather than keeping it.
        title = dna_fallback or ("Untitled Novel" if is_en else "未命名新书")
        title_profile["primary_title"] = title

    # ── 共享上下文（供简介文案工序 + appeal 评估复用，避免重复计算）──────────
    _ap_genre = (
        genre_intent_contract.genre_label
        if genre_intent_contract is not None
        else str(ctx.get("genre") or genre or "")
    )
    _ap_sub = (
        genre_intent_contract.sub_genre_label or ""
        if genre_intent_contract is not None
        else str(ctx.get("sub_genre") or sub_genre or "")
    )
    _ap_platform = str(target_platform or "")
    _ap_language = str(ctx.get("language") or "zh-CN")
    # 按书派生黑话词表（不是全局词表）：从本书设计字段（金手指/世界观/hook_spec
    # 核心规则）里提取，双重条件避免误伤正常叙事用词。主角名/书名进白名单，
    # 不会被自己名字误伤。golden_finger/character/hook_spec 此时不再变化
    # （只有 synopsis/premise/title/tags 会在下面被改写），派生一次全程复用。
    # 主角名在 try 内赋值，但下面的正典人名校验也要用它——派生失败时它必须仍有
    # 定义，否则一个「非致命」的黑话派生异常会把校验变成 NameError。
    _protagonist_name = ""
    try:
        _jargon_char = (
            writing_profile.get("character") if isinstance(writing_profile, dict) else {}
        )
        _jargon_world = (
            writing_profile.get("world") if isinstance(writing_profile, dict) else {}
        )
        _jargon_source: dict[str, Any] = {
            "golden_finger": (_jargon_char or {}).get("golden_finger", ""),
            "power_system": (_jargon_world or {}).get("power_system", "")
            if isinstance(_jargon_world, dict) else "",
            "world_model": _jargon_world if isinstance(_jargon_world, dict) else {},
            "hook_spec": ctx.get("hook_spec") if isinstance(ctx, dict) else None,
            # 概念淘汰赛冠军的学术/机构词汇(拓扑/语义…)会经 spine/premise 渗入
            # 简介——纳入派生源,文案淘汰赛把它们当禁用词,逼翻译成大白话。
            "high_concept": ctx.get("high_concept") if isinstance(ctx, dict) else None,
        }
        _protagonist_name = (
            str(character_proposal.get("protagonist_name") or "")
            if isinstance(character_proposal, dict) else ""
        )
        _book_jargon_terms = derive_book_jargon_terms(
            _jargon_source, entity_whitelist=(_protagonist_name, title),
        )
    except Exception:
        logger.warning("book jargon term derivation failed (non-fatal)", exc_info=True)
        _book_jargon_terms = ()

    # ── 正典自洽修复（P4, 2026-08-07）───────────────────────────────────────
    # 必须在文案工序之前：毒正典（premise↔spine 数字/期限互斥）在 finalize 产出，
    # 自闭环证明下游救不回——先把正典修干净，冠军简介才有干净的事实准绳。
    _canon_repair_report: dict[str, Any] | None = None
    try:
        premise, story_spine, _canon_repair_report = await _repair_canon_contradictions(
            session, settings,
            premise=premise,
            spine=story_spine if isinstance(story_spine, dict) else None,
            synopsis=synopsis,
        )
    except Exception:
        logger.warning("canon repair step failed (fail-open)", exc_info=True)

    # ── 简介独立文案工序（T6, 2026-07-09）──────────────────────────────────
    # 简介是产品不是元数据：finalize 顺手产出的 synopsis(v0) 只作兜底，真正
    # 见光的简介由独立文案工序产出——输入收窄到 spine+premise+金手指+画像锚
    # (不给设计 JSON)，N 路候选→病理筛→画像判官淘汰赛→定向打磨，永不劣于 v0。
    _copywriting_result: Any = None
    _copywriting_ran = False
    try:
        from bestseller.services.blurb_copywriter import (
            load_copywriting_config,
            run_blurb_copywriting,
        )
        from bestseller.services.story_appeal import load_story_appeal_config

        _appeal_cfg_for_cw = load_story_appeal_config()
        _cw_cfg = load_copywriting_config(_appeal_cfg_for_cw)
        if _cw_cfg.get("enabled", True):
            _gf_text = (
                str((writing_profile.get("character", {}) or {}).get("golden_finger", ""))
                if isinstance(writing_profile, dict) else ""
            )
            # 首句大白话——金手指描述常是多句设计文本，只取第一句给文案工序当引子。
            _golden_finger_line = re.split(r"[。！？；\n]", _gf_text.strip())[0].strip() if _gf_text else ""
            _copywriting_result = await run_blurb_copywriting(
                session, settings,
                spine=story_spine if isinstance(story_spine, dict) else {},
                premise=premise, golden_finger_line=_golden_finger_line,
                title=title, tags=tags, genre=_ap_genre, sub_genre=_ap_sub,
                platform=_ap_platform, language=_ap_language,
                v0_synopsis=synopsis, book_jargon_terms=_book_jargon_terms,
                # 建书勾选（轻松/爽文/不虐主角…）翻译成读者看得懂的契约标签。
                # 标签行是读者决定点不点进去的依据，不是设定名词表。
                #
                # ⚠️ 勾选项从 ctx 的 genre_intent_contract 取——构思阶段
                # **还没有 project**（书是构思之后才创建的）。第一版写了
                # `getattr(project, ...)`，NameError 让文案淘汰赛整体失败
                # 回退 v0（真机 custom-xuanhuan-1787407568 撞上，fail-open
                # 掩盖了它，靠全容器错误扫描才捞出来）。
                # 用户自己点名该族吗——判断放在**有这个信息的地方**（构思阶段
                # 拿得到 explicit_concept_seed，淘汰赛内部拿不到）。
                # 2026-08-24：读者看到的那几行此前从没被单独量过默认族。
                user_named_default_family=bool(
                    _default_debt_family_hits(str(explicit_concept_seed or ""))
                ),
                reader_contract=reader_contract_labels(
                    {
                        "story_enhancers": (
                            (ctx.get("genre_intent_contract") or {}).get(
                                "explicit_enhancers"
                            )
                            or {}
                        )
                        if isinstance(ctx, dict)
                        else {}
                    }
                ),
                # The approved hook card is part of the fact-grounding canon:
                # a deadline or relationship the champion states must trace to
                # SOME approved surface, and several live in the hook card only.
                hook_card=(
                    dict(concept_contract.get("hook_card") or {})
                    if isinstance(concept_contract, Mapping)
                    else {}
                ),
                # 基调断点修复（2026-08-26）：勾选此前只以「读者承诺标签词」的
                # 身份到达简介层，是可换可丢的素材；正文语气无人约束，勾了
                # 轻松+喜剧的书写出「拿刀架脖子/最后一条命」。同 ctx 取值，
                # 与池层/项目卡层共用一份勾选事实。
                tone_preference=str(
                    (ctx.get("genre_intent_contract") or {}).get("tone_preference")
                    or ""
                ),
                effect_skills=tuple(
                    (
                        (ctx.get("genre_intent_contract") or {}).get(
                            "explicit_enhancers"
                        )
                        or {}
                    ).get("effect_skills")
                    or ()
                ),
                config=_appeal_cfg_for_cw,
            )
            _copywriting_ran = True
            # 正典人名一致性——冠军不得换掉主角。文案工序为防黑话泄漏收窄了输入，
            # 模型会据此自己编一个主角名，而这份简介是**唯一对外见光**的文案。
            # 真机 2026-08-06：正典主角「纪蛰」被冠军换成「沈落」，全书其他字段
            # 仍是纪蛰，读者看到的那份是错的。校验放在覆盖之前，违规就不覆盖。
            _canon_swap = champion_swaps_protagonist(
                _copywriting_result.champion,
                canon_text=premise,
                protagonist_name=_protagonist_name,
            ) if not _copywriting_result.fell_back_to_v0 else None
            if _canon_swap:
                logger.warning(
                    "Blurb copywriting: champion swapped the protagonist "
                    "(canon=%s absent; non-canon names=%s) — rejecting champion, keeping v0",
                    _protagonist_name, list(_canon_swap),
                )
                _copywriting_result.canon_name_rejected = True
                _copywriting_result.canon_name_rogue = list(_canon_swap)
            else:
                # 冠军简介同样要过跨书污染消毒 + 句界截断——它是新产线的输出，不能
                # 绕开原有 synopsis 早就享有的这两道防线（消毒发生在本函数更早处，
                # 冠军产出在那之后才落地，必须在这里补跑一次；两者都是幂等操作）。
                synopsis = str(
                    _sanitize_forbidden_default_motifs(_copywriting_result.champion, is_en=is_en)
                )
                if len(synopsis) > 500:
                    synopsis = truncate_at_sentence(synopsis, 500)
            llm_run_ids.extend(_copywriting_result.llm_run_ids)
            logger.info(
                "Blurb copywriting: champion_strategy=%s fell_back_to_v0=%s polish_rounds=%d",
                _copywriting_result.champion_strategy, _copywriting_result.fell_back_to_v0,
                _copywriting_result.polish_rounds,
            )
            # logline 同源：只在冠军是真新内容(非回退v0、且未因换主角被拒)时才
            # 重新提炼，否则 synopsis 仍是 v0，原 logline 已经与之同源，无需重跑。
            if not (
                _copywriting_result.fell_back_to_v0
                or _copywriting_result.canon_name_rejected
            ):
                try:
                    _new_logline, _logline_ids = await _derive_logline_from_champion(
                        session, settings,
                        synopsis=synopsis,
                        spine_question=str(
                            (story_spine or {}).get("question", "") if isinstance(story_spine, dict) else ""
                        ),
                        title=title, genre=_ap_genre, is_en=is_en,
                    )
                    llm_run_ids.extend(_logline_ids)
                    if _new_logline:
                        _market_profile = writing_profile.setdefault("market", {})
                        if isinstance(_market_profile, dict):
                            _market_profile["logline"] = _new_logline
                except Exception:
                    logger.warning("logline re-derivation from champion failed (non-fatal)", exc_info=True)
    except Exception:
        logger.warning("Blurb copywriting tournament failed (non-fatal)", exc_info=True)
        _copywriting_result = None
        _copywriting_ran = False

    # ── Story/blurb appeal evaluation + bounded keep-best regeneration ──
    # Additive: scores the finalized idea + blurb for click-power and
    # bestseller-grade appeal. Disabled in config → skipped entirely so the
    # ConceptionResult is byte-identical to history (no-op contract).
    # Regeneration fires only when the idea is clearly weak (grade ≤ floor),
    # is bounded, keeps the best-scoring variant, and is fail-open.
    story_appeal_report: dict[str, Any] = {}
    # Enforcement decision is captured inside the try but ACTED ON after it, so the
    # fail-open ``except`` below cannot swallow the block (product line "低于blurb_min不通过"，见 config/story_appeal.yaml)。
    _appeal_block_below = False
    _appeal_blocked_feedback = ""
    # Which gate(s) actually rejected. Several can raise the same
    # AppealBarNotMetError; without the name the operator-facing message
    # misdirects (a field block reported the blurb/title scores as the cause
    # while both were above threshold and persona_judge was the real blocker).
    _appeal_blocked_by: list[str] = []
    try:
        from bestseller.domain.appeal import grade_rank
        from bestseller.services.story_appeal import (
            appeal_regen_should_continue,
            build_improvement_feedback,
            evaluate_story_appeal,
            is_appeal_enabled,
            load_story_appeal_config,
            persona_hard_veto,
        )

        _appeal_cfg = load_story_appeal_config()
        if is_appeal_enabled(_appeal_cfg):
            # _ap_genre/_ap_sub/_ap_platform/_ap_language/_book_jargon_terms 已在
            # 简介文案工序之前统一派生（避免重复计算），此处直接复用。
            report = await evaluate_story_appeal(
                session, settings,
                premise=premise, synopsis=synopsis, title=title, tags=tags,
                writing_profile=writing_profile, genre=_ap_genre, sub_genre=_ap_sub,
                chapter_count=chapter_count, platform=_ap_platform, config=_appeal_cfg,
                language=_ap_language, book_jargon_terms=_book_jargon_terms,
            )
            regen = _appeal_cfg.get("regeneration", {}) if isinstance(_appeal_cfg, dict) else {}
            floor = str(regen.get("floor_grade", "consider"))
            regen_below_bar = bool(regen.get("regen_below_bar", True))
            # 简介已经过独立文案工序(N候选+病理筛+画像淘汰赛+定向打磨)才到这里，
            # 再跑满 3 轮盲重生是重复劳动——压到 max_attempts_after_copywriting(默认1)，
            # 只作最后的安全网（例如冠军仍差一点分）。未跑文案工序(fail-open关闭)时
            # 保持原有 max_attempts 不变。
            max_attempts = int(
                regen.get("max_attempts_after_copywriting", 1)
                if _copywriting_ran
                else regen.get("max_attempts", 3)
            )
            _title_min = float((_appeal_cfg.get("meets_bar", {}) or {}).get("title_min", 0))
            best = (report, premise, synopsis, tags, title)
            attempts = 0
            # ── 画像点击判官（模拟目标读者3秒点不点，advisory 并联信号）──
            # 初评一次：「不点」的划走原因并入每轮重生反馈（绝对分门看不见的
            # 人群视角信号——黑话劝退/爽点错频）。fail-open，不改变循环触发条件。
            _persona_report, _persona_fb = await _persona_click_advisory(
                session, settings,
                title=title, synopsis=synopsis, genre=_ap_genre, sub_genre=_ap_sub,
                tags=tags, config=_appeal_cfg,
            )
            # A gate that can BLOCK must also be able to DRIVE the repair loop.
            # persona_judge holds block_below veto power, so its verdict joins the
            # continuation decision — keying the loop on the numeric bar alone
            # starved it: books clearing meets_bar but scoring 0/3 simulated clicks
            # were killed at attempts=0 with _persona_fb built and then dropped,
            # since its only consumer was this loop body (2026-07-24, two books).
            _persona_blocks = persona_hard_veto(_persona_report, _appeal_cfg)
            # ── 自洽 advisory（2026-08-07）：涉简介的核实矛盾与 persona 同渠道
            # 驱动重生（复用 persona_blocks 入参——语义同为「advisory 判官要求
            # 再来一轮」）；反馈行并入每轮改写指令。
            _coherence_report, _coherence_fb, _coherence_blocks = await _coherence_advisory(
                session, settings,
                synopsis=synopsis, premise=premise,
                spine=story_spine if isinstance(story_spine, dict) else None,
            )
            # 达标门轨迹（2026-08-23）：这道门能毙掉整本书，却曾经一条重生
            # 记录都不留，事后无法判断它在收敛还是原地打转。每轮落一条。
            _appeal_history: list[dict[str, Any]] = [
                {
                    "attempt": 0,
                    "premise": report.premise.total,
                    "blurb": report.blurb.total,
                    "title": report.title.total if report.title else None,
                    "meets_bar": bool(report.meets_bar),
                }
            ]
            # Product hard line: regenerate while not meeting the bar (blurb<blurb_min,
            # calibrated to 68 in config/story_appeal.yaml); else fall back to the grade floor.
            while appeal_regen_should_continue(
                enabled=bool(regen.get("enabled", False)),
                attempts=attempts,
                max_attempts=max_attempts,
                needs_score_regen=(
                    (not report.meets_bar)
                    if regen_below_bar
                    else grade_rank(report.overall_grade) <= grade_rank(floor)
                ),
                persona_blocks=_persona_blocks or _coherence_blocks,
            ):
                attempts += 1
                # Heartbeat: the finalize→blurb stretch (conception_finalize at
                # ~4121 to the next _emit at ~5053) is the single longest silent
                # window in conception — ~15-20 sequential M3 awaits, this
                # regeneration loop being the heaviest. Touch progress each round
                # so a slow blurb pass cannot trip the 2700s no-progress watchdog.
                emit_activity(
                    "conception_blurb_regen_progress",
                    {"attempt": attempts, "max_attempts": max_attempts},
                )
                try:
                    feedback = build_improvement_feedback(report, _appeal_cfg)
                    if _persona_fb:
                        feedback = f"{feedback}\n{_persona_fb}"
                    if _coherence_fb:
                        feedback = f"{feedback}\n{_coherence_fb}"
                    # 聚焦【点击型简介】打磨：从当前最优 synopsis 出发，按反馈只重写简介，
                    # 不重跑整段 finalize（整段 finalize 同时产 premise/profile，对简介不够
                    # 聚焦——实测真机现实题材重跑 finalize 卡 74.7，而聚焦重写可达 84）。
                    # premise/title/tags 保留（达标门是 blurb，premise 仅 advisory）。
                    polish_syn, polish_id = await _polish_blurb_synopsis(
                        session, settings,
                        synopsis=best[2], feedback=feedback,
                        genre=_ap_genre, sub_genre=_ap_sub, is_en=is_en,
                        language=str(ctx.get("language") or "zh-CN"),
                        platform=_ap_platform,
                    )
                    if polish_id is not None:
                        llm_run_ids.append(polish_id)
                    r_syn = _safe_get({"synopsis": polish_syn}, "synopsis", "").strip()
                    if len(r_syn) > 500:
                        r_syn = truncate_at_sentence(r_syn, 500)
                    r_syn = str(_sanitize_forbidden_default_motifs(r_syn or best[2], is_en=is_en))
                    r_premise = best[1]
                    r_tags = best[3]
                    # 书名也是达标门：当前最优书名不达标时，聚焦重起书名
                    # （LLM 出候选 → 零 token 标题门选优）。达标的书名保持不动。
                    r_title = best[4]
                    cur_title = best[0].title
                    if (
                        _title_min > 0
                        and cur_title is not None
                        and cur_title.total < _title_min
                    ):
                        r_title, title_id = await _polish_title(
                            session, settings,
                            title=best[4], premise=r_premise, synopsis=r_syn,
                            feedback=feedback, genre=_ap_genre, sub_genre=_ap_sub,
                            is_en=is_en, language=str(ctx.get("language") or "zh-CN"),
                            config=_appeal_cfg,
                            audience_orientation=str(
                                (ctx.get("user_hints") or {}).get("audience_orientation") or ""
                            ),
                        )
                        if title_id is not None:
                            llm_run_ids.append(title_id)
                    report = await evaluate_story_appeal(
                        session, settings,
                        premise=r_premise, synopsis=r_syn, title=r_title, tags=r_tags,
                        writing_profile=writing_profile, genre=_ap_genre, sub_genre=_ap_sub,
                        chapter_count=chapter_count, platform=_ap_platform, config=_appeal_cfg,
                        language=_ap_language, book_jargon_terms=_book_jargon_terms,
                    )
                    _appeal_history.append(
                        {
                            "attempt": attempts,
                            "premise": report.premise.total,
                            "blurb": report.blurb.total,
                            "title": report.title.total if report.title else None,
                            "meets_bar": bool(report.meets_bar),
                        }
                    )
                    cur_sum = report.premise.total + report.blurb.total + (
                        report.title.total if report.title else 0.0
                    )
                    best_sum = best[0].premise.total + best[0].blurb.total + (
                        best[0].title.total if best[0].title else 0.0
                    )
                    if (report.meets_bar and not best[0].meets_bar) or (
                        report.meets_bar == best[0].meets_bar and cur_sum > best_sum
                    ):
                        best = (report, r_premise, r_syn, r_tags, r_title)
                    # Re-judge the CURRENT BEST against the reader persona. Without
                    # this the loop could neither exit early on a fixed blurb nor
                    # feed the next round fresh 划走原因 — it would spend its whole
                    # budget re-polishing against a stale initial verdict.
                    _persona_report, _persona_fb = await _persona_click_advisory(
                        session, settings,
                        title=best[4], synopsis=best[2],
                        genre=_ap_genre, sub_genre=_ap_sub,
                        tags=best[3], config=_appeal_cfg,
                    )
                    _persona_blocks = persona_hard_veto(_persona_report, _appeal_cfg)
                    # 自洽同款回评：矛盾修好了就别再逼着重生；没修好则带着
                    # 新一轮引文反馈继续。
                    _coherence_report, _coherence_fb, _coherence_blocks = (
                        await _coherence_advisory(
                            session, settings,
                            synopsis=best[2], premise=best[1],
                            spine=story_spine if isinstance(story_spine, dict) else None,
                        )
                    )
                except Exception:
                    logger.warning("appeal regeneration attempt %d failed", attempts, exc_info=True)
                    break
            report, premise, synopsis, tags, title = best
            # 2026-08-25：appeal 重生会连书名一起重做，此前这里直接整包覆盖了
            # 书名淘汰赛的冠军（真机：冠军《擀面胖婶的三息辨火》被《逐出师门我
            # 颠大》无声取代，后者从没进过淘汰赛，也没过确定性门和默认族降权）。
            # 不重跑淘汰赛、不加 LLM 调用：让挑战者过一遍同样的确定性门再对决，
            # 并且**无论换不换都留痕**。
            try:
                from bestseller.services.title_tournament import (
                    adjudicate_late_title,
                )

                title, _late_title_receipt = adjudicate_late_title(
                    _tournament_title,
                    title,
                    tags=tags if isinstance(tags, (list, tuple)) else (),
                    prose=f"{premise}\n{synopsis}",
                    user_named_default_family=bool(
                        _default_debt_family_hits(str(explicit_concept_seed or ""))
                    ),
                )
                if _late_title_receipt:
                    _title_tournament_receipt["late_adjudication"] = (
                        _late_title_receipt
                    )
            except Exception:
                logger.warning(
                    "late title adjudication failed; keeping appeal title",
                    exc_info=True,
                )
            _appeal_trace = build_appeal_regen_trace(
                _appeal_history,
                blurb_min=float(
                    ((_appeal_cfg or {}).get("meets_bar", {}) or {}).get("blurb_min", 68)
                ),
            )
            conception_log.append(
                {"round": 3, "agent": "appeal_regen_gate", **_appeal_trace}
            )
            logger.info(
                "Appeal regen trace: attempts=%s verdict=%s blurb %.1f→%.1f (bar %.1f)",
                _appeal_trace["attempts"],
                _appeal_trace["verdict"],
                _appeal_trace.get("blurb_first") or 0.0,
                _appeal_trace.get("blurb_last") or 0.0,
                _appeal_trace["blurb_min"],
            )
            story_appeal_report = report.to_dict()
            _t_total = report.title.total if report.title else None
            logger.info(
                "Story appeal: premise=%.0f blurb=%.0f title=%s grade=%s meets_bar=%s regen=%d",
                report.premise.total, report.blurb.total, _t_total,
                report.overall_grade, report.meets_bar, attempts,
            )
            # ── 画像判官终评 ──
            # 终评已在重生循环内对每一轮的 best 做过（见循环体末尾），attempts==0
            # 时初评的输入就是 best，两种情况下 _persona_report 都已对应终稿；
            # 这里只做持久化 + 硬拦判定，不再重复调用判官。
            # ── 自洽终报持久化：判官说了什么必须可查（含正典侧矛盾——它们
            # 改简介修不了，落库是唯一让人看见的通道）。
            if _canon_repair_report is not None:
                story_appeal_report["canon_repair"] = _canon_repair_report
            if _coherence_report is not None:
                story_appeal_report["coherence"] = _coherence_report
                if not _coherence_report.get("passed", True):
                    logger.warning(
                        "Blurb coherence at finalize: %d finding(s) persisted "
                        "(synopsis-touching drove %d regen round(s))",
                        len(_coherence_report.get("findings") or ()), attempts,
                    )
            if _persona_report is not None:
                story_appeal_report["persona_judge"] = _persona_report
                logger.info(
                    "Persona click judge: channel=%s clicks=%s/%s rate=%s pass=%s regen=%d",
                    _persona_report.get("channel"), _persona_report.get("clicks"),
                    _persona_report.get("samples"), _persona_report.get("click_rate"),
                    _persona_report.get("advisory_pass"), attempts,
                )
                if persona_hard_veto(_persona_report, _appeal_cfg):
                    _appeal_block_below = True
                    _appeal_blocked_by.append("persona_judge")
                    _appeal_blocked_feedback = (
                        (_appeal_blocked_feedback + "\n" if _appeal_blocked_feedback else "")
                        + _persona_fb
                    )
            # ── arena 相对盲评终验（config arena.run_at_finalize 门控，默认 off）──
            # 绝对分不可信的补充：终稿简介 vs 真实爆款的双盲胜率（advisory）。
            _arena_report = await _run_finalize_arena(
                session, settings,
                synopsis=synopsis, genre=_ap_genre, sub_genre=_ap_sub, config=_appeal_cfg,
            )
            if _arena_report is not None:
                story_appeal_report["arena"] = _arena_report
                logger.info(
                    "Finalize appeal arena: pairs=%s win_rate=%s meets_story_bar=%s",
                    _arena_report.get("pairs"), _arena_report.get("win_rate"),
                    _arena_report.get("meets_story_bar"),
                )
            # 真拦截：有界重生用尽仍不达标 + 开关开 → 记下决定，try 块外再抛
            # （放块外，确保不被下面 fail-open 的 except 吞掉）。
            if bool((_appeal_cfg.get("meets_bar", {}) or {}).get("block_below_bar", False)) \
                    and not report.meets_bar:
                _appeal_block_below = True
                _appeal_blocked_by.append("meets_bar")
                _appeal_blocked_feedback = build_improvement_feedback(report, _appeal_cfg)
    except Exception:
        logger.warning("Story appeal evaluation failed (non-fatal)", exc_info=True)
        story_appeal_report = {}

    # 一句话故事大纲是建项/规划的独立前置条件，不能被简介、书名或画像评估的
    # fail-open 容错跳过。只有明确 EXPAND 才放行；闸门自身异常同样故障关闭。
    try:
        from bestseller.services.logline_gate import (
            LoglineAction,
            evaluate_logline_gate,
            load_logline_gate_config,
            verdict_from_approved_concept_contract,
        )

        _lg_cfg = load_logline_gate_config(_appeal_cfg)
        if _lg_cfg.get("enabled", True):
            _logline_text = (
                (writing_profile.get("market", {}) or {}).get("logline")
                if isinstance(writing_profile, dict)
                else None
            ) or premise
            _lg = verdict_from_approved_concept_contract(
                concept_contract,
                target_chapters=chapter_count,
                # Evidence transfer is only valid for the exact judged sentence.
                # When T6 rewrote the logline, this mismatches and the real
                # judge below runs on what will actually ship.
                logline_text=str(_logline_text or ""),
            )
            if _lg is None:
                _lg = await evaluate_logline_gate(
                    session,
                    settings,
                    logline=str(_logline_text or ""),
                    premise=premise,
                    genre=_ap_genre,
                    sub_genre=_ap_sub,
                    config=_appeal_cfg,
                )
            # 定罪句式确定性降级（0 误报校准）：故事门看不见句式病，
            # 「天煞孤星」当年正是从这里拿高分出的书。
            from bestseller.services.logline_gate import (
                downgrade_for_condemned_structures,
            )

            _lg = downgrade_for_condemned_structures(_lg, str(_logline_text or ""))
            logger.info(
                "Logline gate: action=%s overall=%.2f weakest=%s%s",
                _lg.action.value,
                _lg.overall,
                _lg.weakest_axis,
                (" | " + " ; ".join(_lg.reasons[:3])) if _lg.reasons else "",
            )
            # regenerate 裁决按门自己的语义消费：有界聚焦重写卖点再复审
            # （keep-best + fail-closed）。reject 仍然立即死。此前任何非 EXPAND
            # 都直接毙任务，整改指令从未被消费（真机 4.38 regenerate 也照死）。
            _lg_regen_used = 0
            _rescued_logline = ""
            if _lg.action in (LoglineAction.REGENERATE, LoglineAction.REJECT):
                async def _lg_rewrite(cur_logline: str, cur_verdict: Any) -> str:
                    return await _rewrite_logline_for_gate(
                        session, settings,
                        logline=cur_logline, verdict=cur_verdict,
                        premise=premise, synopsis=synopsis,
                        genre=_ap_genre, sub_genre=_ap_sub, is_en=is_en,
                    )

                async def _lg_rejudge(cur_logline: str) -> Any:
                    rejudged = await evaluate_logline_gate(
                        session, settings,
                        logline=cur_logline, premise=premise,
                        genre=_ap_genre, sub_genre=_ap_sub, config=_appeal_cfg,
                    )
                    # 复审同样过定罪句式检查——带病稿不得借复活循环回魂
                    return downgrade_for_condemned_structures(rejudged, cur_logline)

                _lg, _rescued_logline, _lg_regen_used = await _logline_regen_rescue(
                    verdict=_lg,
                    logline=str(_logline_text or ""),
                    # ``load_logline_gate_config`` emits ``max_regen`` — the key
                    # ``regenerate_attempts`` was never produced by it, so the
                    # yaml's configured value was dead and this silently ran a
                    # hardcoded 2 rescues forever. Read the key that exists.
                    max_attempts=int(
                        _lg_cfg.get("max_regen")
                        or _lg_cfg.get("regenerate_attempts")
                        or 2
                    ),
                    rewrite_fn=_lg_rewrite,
                    judge_fn=_lg_rejudge,
                )
                if _lg_regen_used:
                    logger.info(
                        "Logline gate rescue: attempts=%d final=%s overall=%.2f",
                        _lg_regen_used, _lg.action.value, _lg.overall,
                    )
                if _lg.action is LoglineAction.EXPAND and _rescued_logline:
                    # 采纳获救卖点：写回 market.logline（不回写 premise —— 卖点是
                    # 营销工件，premise 是下游各阶段已消费的故事事实）。
                    if isinstance(writing_profile, dict):
                        writing_profile.setdefault("market", {})["logline"] = _rescued_logline
            story_appeal_report = dict(story_appeal_report or {})
            story_appeal_report["logline_gate"] = _lg.to_dict()
            story_appeal_report["logline_gate"]["regen_attempts"] = _lg_regen_used
            # 点开欲判官 advisory 测量（2026-08-12）：榜单验证过的双通道判官
            # （欲望分+证据定罪，检察官通道 46 次在榜书判定零冤案）。此处
            # **只记录不投票**——先在生产上积累命中率，再决定是否给否决权
            # （2026-07-25 门禁误杀全量审计的教训：新门先 advisory）。
            try:
                from bestseller.services.hook_pull_judge import (
                    evaluate_hook_pull,
                )

                _final_logline = str(
                    (_lg_regen_used and _rescued_logline) or _logline_text or ""
                ).strip()
                if _final_logline:
                    _hp = await evaluate_hook_pull(
                        session, settings,
                        title="（未命名）", hook=_final_logline,
                        genre=_ap_genre or "", channel="男频", samples=2,
                    )
                    if _hp is not None:
                        story_appeal_report["hook_pull"] = {
                            **_hp.to_dict(),
                            "advisory_only": True,
                        }
                        logger.info(
                            "Hook pull probe (advisory): score=%.1f flags=%s",
                            _hp.score, list(_hp.flags),
                        )
            except Exception:
                logger.warning("hook pull probe failed (advisory, ignored)", exc_info=True)
            # Record whether this gate actually held veto power on THIS run.
            # It has been advisory since 2026-07-25, so downstream error
            # reporting must not read a non-expand verdict as the cause of a
            # block that some other bar raised.
            # 救援已尽力：把「仅因句式降级」的裁决还原成放行。下游判据是
            # 「非 EXPAND 即阻断」，不还原的话一条 3.81 分（本可通过）的卖点
            # 会把整本书打死（2026-08-14 真机，两天内第三次门禁自伤）。
            from bestseller.services.logline_gate import (
                clear_structural_downgrade,
            )

            _lg = clear_structural_downgrade(_lg)
            _lg_blocking = bool(_lg_cfg.get("block_expansion", True)) and (
                _lg.action is not LoglineAction.EXPAND
            )
            # 确定性定罪的否决权（2026-08-13《摸一摸，救我妹》：定罪句式在
            # 创建当刻就被记录在案，却因 advisory 配置静默发货）。LLM 判官
            # 维持 advisory（硬门有 3/3 误杀真爆款的前科），但 0 误报校准的
            # 确定性家族例外：救援循环用尽后成稿仍带定罪结构 → 拦下重来，
            # 不静默发货。
            from bestseller.services.hook_pull_judge import (
                detect_condemned_hook_structures as _detect_condemned,
            )

            _final_logline_text = str(
                (_lg_regen_used and _rescued_logline) or _logline_text or ""
            ).strip()
            _det_hits = _detect_condemned(_final_logline_text)
            if _det_hits:
                # 2026-08-14 真机：这条否决权把一本 3.81 分（本可 EXPAND）的书
                # 打死了——救援轮没洗掉句式，于是整本书没了。定罪句式值几轮
                # 重写，但**不值一本书**：卖点是营销工件，故事本身没问题。
                # 与默认族同待遇：赚重生，不赚生杀权（本仓库为「门禁误杀」
                # 付过太多次学费，这是两天内第三次自伤）。
                story_appeal_report.setdefault("logline_gate", _lg.to_dict())
                story_appeal_report["logline_gate"]["condemned_structures"] = list(
                    _det_hits
                )
                story_appeal_report["logline_gate"]["condemned_advisory_only"] = True
                logger.warning(
                    "logline still carries condemned structures after rescue "
                    "(%s); shipping with advisory instead of blocking the book",
                    "、".join(_det_hits),
                )
            story_appeal_report["logline_gate"]["blocking"] = _lg_blocking
            if _lg_blocking:
                _appeal_block_below = True
                _appeal_blocked_by.append("logline_gate")
                _det_line = (
                    "一句话钩子在救援后仍带定罪句式（"
                    + "、".join(_det_hits)
                    + "），该句式在 100 本榜单钩子中出现 0 次，不得发货。"
                    if _det_hits
                    else ""
                )
                _appeal_blocked_feedback = (
                    "一句话故事大纲未过前置硬门，未进入书籍规划：\n"
                    + "\n".join((*_lg.reasons, *((_det_line,) if _det_line else ())))
                    + "\n\n整改方向：\n"
                    + "\n".join(
                        (
                            *_lg.fix_directives,
                            *(
                                (
                                    "把机制条款改写成主角正在经历的一件已发生的具体事及其当场后果",
                                )
                                if _det_hits
                                else ()
                            ),
                        )
                    )
                )
    except Exception:
        logger.exception("一句话故事大纲硬门执行失败，故障关闭并停止规划")
        story_appeal_report = dict(story_appeal_report or {})
        story_appeal_report["logline_gate"] = {
            "action": "reject",
            "scores": {},
            "overall": 0.0,
            "reasons": ["一句话故事大纲硬门执行失败，不能证明故事成立。"],
            "fix_directives": ["修复硬门后重新审查，不得绕过并进入规划。"],
            "llm_used": False,
            "weakest_axis": "gate_execution",
        }
        _appeal_block_below = True
        _appeal_blocked_by.append("logline_gate_execution")
        _appeal_blocked_feedback = (
            "一句话故事大纲硬门执行失败，未创建书籍、未进入规划。"
        )

    # 淘汰赛报告独立持久化（不依赖 story appeal 系统是否启用/是否失败），
    # 分段验收需要它核验冠军策略/候选分/是否回退 v0。这份 dict 就是
    # ConceptionResult.story_appeal，web/server.py 把它落到
    # project.metadata_json["conception_artifacts"]["story_appeal"]（见
    # server.py 的 conception_story_appeal/conception_artifacts 赋值链，
    # 仅当非空时才写入——旧书/走 concept_lab 快速通道等未跑 run_conception_
    # pipeline 的入口不会有这个键，查不到时先确认走的是这条入口）。
    if _copywriting_result is not None:
        story_appeal_report = dict(story_appeal_report or {})
        story_appeal_report["copywriting_tournament"] = _copywriting_result.to_dict()
    if genre_intent_contract is not None:
        story_appeal_report = dict(story_appeal_report or {})
        story_appeal_report["genre_intent"] = {
            "contract_hash": genre_intent_contract.contract_hash(),
            "genre_key": genre_intent_contract.genre_key,
            "sub_genre_key": genre_intent_contract.sub_genre_key,
            "prompt_pack_key": genre_intent_contract.prompt_pack_key,
            "allowed_modernity": genre_intent_contract.allowed_modernity,
        }

    # 产品硬线"低于blurb_min不通过"(config/story_appeal.yaml 校准为68)的真拦截：
    # 简介/书名经有界重生仍不达标 → 抛 AppealBarNotMetError。
    # 调用方(web)捕获后把项目置为可见拦截态(带分数+整改建议)，不静默进规划、不留 running 僵尸。
    if _appeal_block_below and story_appeal_report:
        from bestseller.services.story_appeal import AppealBarNotMetError

        _b = (story_appeal_report.get("blurb") or {}).get("total")
        _t = (story_appeal_report.get("title") or {}).get("total")
        _by = _appeal_blocked_by or ["appeal_bar"]
        logger.warning(
            "Conception BLOCKED by %s (blurb=%s title=%s) — "
            "not advancing to planning.", "+".join(_by), _b, _t,
        )
        _appeal_exc = AppealBarNotMetError(
            story_appeal_report, _appeal_blocked_feedback, blocked_by=tuple(_by)
        )
        # 被拦截的构思恰恰最需要审计，但 web 的同步 handler 拿不到 runner
        # 帧里的局部变量（conception_log/settings 都是 async 帧的 locals，
        # 见 server._run_autowrite_worker 的 NOTE）——把全过程日志挂在异常上
        # 带出去，落盘由 handler 用 load_settings() 完成。
        _appeal_exc.conception_log = list(conception_log)
        raise _appeal_exc

    # ── 意图对表（2026-08-19《摔下山三次》定罪）─────────────────────────────
    # 用户意图 tags（废柴逆袭/升级流/血脉觉醒）此前只用于路由，没有任何门把
    # 它与成品对表：成品丢「升级流」、简介标签行模型自产「慢热」直接对抗
    # 爽文意图、设定把题材必给的升级预期反着写。三面对账：
    # ①意图 tags ⊆ 成品 tags（缺失留痕）②简介标签行 token 接地——标签行是
    # 契约槽位不是句子，异物确定性重建整行 ③LLM 判官逐意图找落点引文+
    # 对抗元素，两票交集定罪。首跑 warn-only 全程留痕（新判官只挣重生和
    # 留痕铁律；设定层定向重生成待真机观测一批后接）。
    try:
        from bestseller.services.intent_alignment import (
            audit_and_rebuild_tagline,
            build_intent_alignment_messages,
            missing_intent_tags,
            parse_intent_alignment_verdict,
            replace_tagline,
        )

        _intent_tags: list[str] = []
        if genre_intent_contract is not None:
            for _key in ("user_tags", "tags", "default_tags"):
                _vals = [
                    str(t).strip()
                    for t in (getattr(genre_intent_contract, _key, None) or [])
                    if str(t).strip()
                ]
                if _vals:
                    _intent_tags = _vals
                    break
        # 代价档也是用户设定的一部分（2026-08-19）：勾了「不自损」却把反噬
        # 当核心笔墨，此前无人对表。
        _ia_cost_style = "standard"
        if genre_intent_contract is not None:
            _ia_enh = getattr(genre_intent_contract, "explicit_enhancers", None)
            if isinstance(_ia_enh, Mapping):
                _ia_cost_style = str(_ia_enh.get("cost_style") or "standard")
        if _intent_tags:
            _ia_missing = missing_intent_tags(_intent_tags, tags or [])
            # 确定性补全（2026-08-19 用户第二次定罪：勾了「金手指」而成品 tags
            # 里彻底没有它）。tags 是元数据不是正文，用户明确勾选的标签缺失
            # 属于纯粹的契约违约，补回去无损且可解释；default_tags 不补
            # （那是子题材默认值，硬塞会制造跨书同质化，2026-08-01 裁决）。
            if _ia_missing:
                tags = [*(tags or []), *_ia_missing]
            _ia_tagline = audit_and_rebuild_tagline(
                synopsis,
                intent_tags=_intent_tags,
                final_tags=tags or [],
                genre_labels=[str(_ap_genre or ""), str(_ap_sub or "")],
            )
            if _ia_tagline.rebuilt_line:
                synopsis = replace_tagline(synopsis, _ia_tagline.rebuilt_line)
            _ia_votes: list[dict[str, Any]] = []
            for _ia_i in range(2):
                _ia_sys, _ia_user = build_intent_alignment_messages(
                    intent_tags=_intent_tags,
                    genre_label=str(_ap_genre or ""),
                    premise=premise,
                    synopsis=synopsis,
                    spine=story_spine if isinstance(story_spine, Mapping) else None,
                    cost_style=_ia_cost_style,
                )
                _ia_payload, _ia_ids = await _llm_call_json(
                    session, settings,
                    role="critic",
                    system_prompt=_ia_sys,
                    user_prompt=_ia_user,
                    fallback="{}",
                    template="conception_intent_alignment",
                    stage="conception.intent_alignment",
                    language=str(ctx.get("language") or "zh-CN"),
                )
                llm_run_ids.extend(_ia_ids)
                _ia_v = parse_intent_alignment_verdict(
                    _ia_payload, intent_tags=_intent_tags
                )
                if _ia_v is not None:
                    _ia_votes.append(_ia_v)
            _ia_convicted: list[str] = []
            if len(_ia_votes) >= 2:
                _ia_convicted = sorted(
                    set.intersection(
                        *(set(v["failed_tags"]) for v in _ia_votes)
                    )
                )
            _ia_counters = [
                c for v in _ia_votes for c in v.get("counter_elements", [])
            ]
            conception_log.append(
                {
                    "round": 3.6,
                    "agent": "intent_alignment",
                    "intent_tags": _intent_tags,
                    "missing_in_tags": _ia_missing,
                    "tagline_aliens": _ia_tagline.alien_tokens,
                    "tagline_rebuilt": bool(_ia_tagline.rebuilt_line),
                    "convicted_tags": _ia_convicted,
                    "counter_elements": _ia_counters[:6],
                    "votes": _ia_votes,
                }
            )
            if _ia_missing or _ia_convicted or _ia_counters:
                logger.warning(
                    "intent alignment: missing_in_tags=%s convicted=%s "
                    "counter_elements=%d",
                    _ia_missing,
                    _ia_convicted,
                    len(_ia_counters),
                )
            # 定向重生成（2026-08-19 用户第二次定罪后从 warn-only 升级）：
            # 真机《逢魔夜市签收人》勾了「金手指」，判官抓到两条**方向相反**
            # 的设定（越签越被推出去挡枪／筹码先被对手拿走），门却只留痕，
            # 书照建——「抓到了不修」等于没抓。按铁律「新判官先观测一本真机
            # 再发修复权」，观测已完成，这里发一次**定向重生成**：只改
            # premise/synopsis/story_spine 三件套，带证据引文与方向；复核不
            # 改善就保留原样（同 S/E 判卡 recheck 语义，零杀权）。
            if _ia_convicted or _ia_counters:
                try:
                    _ia_fix_lines = []
                    for _tag in _ia_convicted:
                        _ia_fix_lines.append(f"- 意图「{_tag}」在成品里找不到落点，必须写显")
                    for _c in _ia_counters[:4]:
                        _ia_fix_lines.append(
                            f"- 与意图「{_c.get('against')}」方向相反："
                            f"「{_c.get('quote')}」——改成兑现该意图的写法"
                        )
                    _ia_dir = "；".join(
                        d for d in (v.get("revise_direction", "") for v in _ia_votes) if d
                    )
                    _ia_repair_user = (
                        f"用户下单时勾选的题材意图：{'、'.join(_intent_tags)}\n\n"
                        f"【当前前提】{premise}\n\n【当前简介】{synopsis}\n\n"
                        f"【当前故事脊柱】{json.dumps(story_spine if isinstance(story_spine, dict) else {}, ensure_ascii=False)}\n\n"
                        "【必须修正】\n" + "\n".join(_ia_fix_lines) + "\n"
                        + (f"修正方向：{_ia_dir}\n" if _ia_dir else "")
                        + "\n只做定向修改：保持故事身份、主角、核心机制与世界观不变，"
                        "把上面点名的地方改成兑现用户意图的写法（该给的爽感给到、"
                        "方向相反的设定改掉）。不得新增人物或另起炉灶。\n"
                        '只输出 JSON：{"premise":"…","synopsis":"…","story_spine":{…原字段名不变…}}'
                    )
                    _ia_fixed, _ia_fix_ids = await _llm_call_json(
                        session, settings,
                        role="editor",
                        system_prompt=(
                            "你是选题编辑，只做定向修正：让成品兑现用户下单时"
                            "勾选的题材意图，不改动故事身份。只输出JSON。"
                        ),
                        user_prompt=_ia_repair_user,
                        fallback="{}",
                        template="conception_intent_repair",
                        stage="conception.intent_repair",
                        language=str(ctx.get("language") or "zh-CN"),
                    )
                    llm_run_ids.extend(_ia_fix_ids)
                    _ia_new_premise = str((_ia_fixed or {}).get("premise") or "").strip()
                    _ia_new_syn = str((_ia_fixed or {}).get("synopsis") or "").strip()
                    _ia_new_spine = (_ia_fixed or {}).get("story_spine")
                    # 结构守卫：三件套都要在且长度同量级，spine 字段不许缩水
                    _ia_ok = bool(
                        _ia_new_premise
                        and _ia_new_syn
                        and len(_ia_new_premise) >= len(premise) * 0.5
                        and len(_ia_new_syn) >= len(synopsis) * 0.5
                    )
                    if _ia_ok and isinstance(story_spine, dict):
                        _ia_ok = isinstance(_ia_new_spine, dict) and set(
                            story_spine.keys()
                        ) <= set(_ia_new_spine.keys())
                    if _ia_ok:
                        # 复核一轮：定罪没减少就不采纳（改了个寂寞甚至改更坏）
                        _rc_sys, _rc_user = build_intent_alignment_messages(
                            intent_tags=_intent_tags,
                            genre_label=str(_ap_genre or ""),
                            premise=_ia_new_premise,
                            synopsis=_ia_new_syn,
                            spine=_ia_new_spine
                            if isinstance(_ia_new_spine, dict)
                            else story_spine,
                            cost_style=_ia_cost_style,
                        )
                        _rc_payload, _rc_ids = await _llm_call_json(
                            session, settings,
                            role="critic",
                            system_prompt=_rc_sys,
                            user_prompt=_rc_user,
                            fallback="{}",
                            template="conception_intent_alignment",
                            stage="conception.intent_alignment_recheck",
                            language=str(ctx.get("language") or "zh-CN"),
                        )
                        llm_run_ids.extend(_rc_ids)
                        _rc_v = parse_intent_alignment_verdict(
                            _rc_payload, intent_tags=_intent_tags
                        )
                        _rc_bad = (
                            len(_rc_v.get("failed_tags", []))
                            + len(_rc_v.get("counter_elements", []))
                            if _rc_v
                            else 99
                        )
                        _was_bad = len(_ia_convicted) + len(_ia_counters)
                        if _rc_bad < _was_bad:
                            premise = _ia_new_premise
                            synopsis = _ia_new_syn
                            if isinstance(_ia_new_spine, dict):
                                story_spine = _ia_new_spine
                            conception_log.append(
                                {
                                    "round": 3.7,
                                    "agent": "intent_repair",
                                    "adopted": True,
                                    "before": _was_bad,
                                    "after": _rc_bad,
                                }
                            )
                            logger.warning(
                                "intent repair adopted: %d -> %d issue(s)",
                                _was_bad,
                                _rc_bad,
                            )
                        else:
                            conception_log.append(
                                {
                                    "round": 3.7,
                                    "agent": "intent_repair",
                                    "adopted": False,
                                    "reason": "recheck_no_improvement",
                                    "before": _was_bad,
                                    "after": _rc_bad,
                                }
                            )
                except Exception:
                    logger.warning("intent repair failed (fail-open)", exc_info=True)
    except Exception:
        logger.warning("intent alignment audit failed (non-fatal)", exc_info=True)

    # Final ontology tripwire: a native 仙侠/历史/悬疑 project must not silently
    # become an APP/phone/workplace/forensic-modern story after all agents merge.
    # Failing here is safer than creating a book whose prompt pack says one thing
    # while its synopsis and profile teach every downstream agent another.
    if genre_intent_contract is not None:
        from bestseller.services.genre_intent_contract import (
            detect_genre_native_ontology_violations,
        )

        generated_surface = _conception_ontology_story_surface(
            title=title,
            premise=premise,
            synopsis=synopsis,
            tags=tags,
            writing_profile=writing_profile,
            high_concept=(
                ctx.get("high_concept")
                if isinstance(ctx.get("high_concept"), Mapping)
                else None
            ),
            story_spine=story_spine if isinstance(story_spine, Mapping) else None,
        )
        ontology_violations = detect_genre_native_ontology_violations(
            generated_surface,
            genre_intent_contract,
        )
        # Honor what the user explicitly asked for — the same exemption the
        # EARLY tournament-winner gate already applies (see
        # ``_unexpected_violations`` above). Without it the two call sites of
        # ONE detector contradict each other: the early gate lets the user's own
        # premise through, then this final gate kills the finished book for the
        # very words the user typed. Now that the create form ships a
        # 故事创意 field, that contradiction is reachable by design.
        _final_seed_text = (
            explicit_concept_seed
            or (
                str(concept_bundle.one_liner or concept_bundle.reader_promise)
                if concept_bundle is not None
                else str(getattr(selected_hook_spec, "one_liner", "") or "")
            )
        )
        ontology_violations = tuple(
            term for term in ontology_violations if term not in _final_seed_text
        )
        if ontology_violations:
            # Late title/blurb/logline/spine editors run after the first concept
            # ontology scan. Give their terminology one bounded, field-scoped
            # repair before discarding an otherwise approved conception. The
            # deterministic scan below remains the authority and still fails
            # closed when the repair cannot remove real drift.
            _emit(
                "conception_final_ontology_repair",
                {"violations": list(ontology_violations), "attempt": 1},
            )
            try:
                (
                    title,
                    premise,
                    synopsis,
                    tags,
                    writing_profile,
                    story_spine,
                    _repaired_high_concept,
                    _ontology_repair_ids,
                ) = await _repair_final_ontology_drift(
                    session,
                    settings,
                    violations=ontology_violations,
                    title=title,
                    premise=premise,
                    synopsis=synopsis,
                    tags=tags,
                    writing_profile=writing_profile,
                    story_spine=(
                        story_spine if isinstance(story_spine, dict) else {}
                    ),
                    high_concept=(
                        dict(ctx["high_concept"])
                        if isinstance(ctx.get("high_concept"), Mapping)
                        else None
                    ),
                    genre_label=genre_intent_contract.genre_label,
                    sub_genre_label=(
                        genre_intent_contract.sub_genre_label or ""
                    ),
                    language=str(ctx.get("language") or "zh-CN"),
                )
                llm_run_ids.extend(_ontology_repair_ids)
                # Write the repaired champion back before re-scanning. Computing
                # a fix and then re-reading the unrepaired value is how a repair
                # silently becomes a no-op.
                if isinstance(_repaired_high_concept, Mapping) and _repaired_high_concept:
                    ctx["high_concept"] = dict(_repaired_high_concept)
                generated_surface = _conception_ontology_story_surface(
                    title=title,
                    premise=premise,
                    synopsis=synopsis,
                    tags=tags,
                    writing_profile=writing_profile,
                    high_concept=(
                        ctx.get("high_concept")
                        if isinstance(ctx.get("high_concept"), Mapping)
                        else None
                    ),
                    story_spine=(
                        story_spine if isinstance(story_spine, Mapping) else None
                    ),
                )
                ontology_violations = detect_genre_native_ontology_violations(
                    generated_surface,
                    genre_intent_contract,
                )
                ontology_violations = tuple(
                    term
                    for term in ontology_violations
                    if term not in _final_seed_text
                )
                conception_log.append(
                    {
                        "round": 3,
                        "agent": "final_ontology_repair",
                        "remaining_violations": list(ontology_violations),
                    }
                )
            except Exception:
                logger.warning(
                    "final ontology repair failed; preserving fail-closed gate",
                    exc_info=True,
                )
        if ontology_violations:
            # A deliberate content block, not a crash. A bare ValueError fell
            # through to the generic handler and showed the user a raw Python
            # traceback — indistinguishable from a framework bug, with no hint
            # of what to change (2026-07-25, custom-xuanhuan-1784908885).
            # ConceptContractError is the shared deliberate-block type: the web
            # layer renders its reasons as an actionable message AND closes the
            # conception workflow row instead of leaking it as `running`.
            from bestseller.services.concept_contract import (
                ConceptContractError,
            )

            _terms = "、".join(ontology_violations)
            raise ConceptContractError([
                f"生成结果混入了与本书题材不符的现代设定词：{_terms}。"
                f"本书题材契约为【{genre_intent_contract.genre_label}】"
                f"/【{genre_intent_contract.sub_genre_label or '未指定子题材'}】，"
                "属于原生题材世界，不能出现现代科技、现代职场或现代法医/殡葬机构。",
                "整改方向：把这些词替换成本题材世界内成立的说法"
                "（例如以宗门、族老、仵作、验尸吏、丧仪等古典称谓与器物承担同样的功能），"
                "或改用另一个不依赖现代设定的故事切入点后重试。",
            ])

    logger.info(
        "Conception pipeline completed for genre=%s: title=%s, premise_len=%d, synopsis_len=%d, tags=%s, profile_keys=%s",
        genre_key, title, len(premise), len(synopsis), tags, list(writing_profile.keys()),
    )

    # 适配结论并入返回的 hook_spec dump——metadata["hook_spec"] 持久化的是这份
    # 返回值，不是 ctx["hook_spec"]（L3 验收发现 flag 曾只写后者而丢失）。
    _result_hook_spec = (
        selected_hook_spec.model_dump(mode="json") if selected_hook_spec else None
    )
    if concept_contract:
        # v2 projects consume HookCard, not the unrelated legacy formula hook.
        _result_hook_spec = None
    if _result_hook_spec is not None and _hook_one_liner_adapted_result is not None:
        _result_hook_spec["one_liner_adapted"] = _hook_one_liner_adapted_result

    if degradation_tracker.events:
        _emit(
            "conception_degraded",
            {
                "count": len(degradation_tracker.events),
                "components": sorted(
                    {event.component for event in degradation_tracker.events}
                ),
            },
        )

    # 出货的读者承诺必须是被清洗过的那份——**汇合点**，不是某一条分支。
    # 2026-08-24 F 验收书 custom-xuanhuan-1787576409：上一版把选择挂在
    # `if concept_bundle is not None:` 里，那本书 concept_lab=false，整条修复
    # 被绕过，脏文案（「前几章给出……」6.0）照旧出货，而干净版就在
    # commercial_brief 里躺着。我在别处的提交里写过「只挂一条分支等于给自己
    # 留一条绕行路」，然后自己犯了一次。
    try:
        _mp = writing_profile.get("market") if isinstance(writing_profile, dict) else None
        if isinstance(_mp, dict):
            _picked = prefer_cleaner_reader_copy(
                [
                    (commercial_brief or {}).get("reader_promise"),
                    _mp.get("reader_promise"),
                ]
            )
            if _picked:
                _mp["reader_promise"] = _picked
    except Exception:  # noqa: BLE001 - 文案择优永不阻断构思出货
        logger.warning("reader_promise 择优失败（非致命）", exc_info=True)

    return ConceptionResult(
        writing_profile=writing_profile,
        premise=premise,
        title=title,
        commercial_brief=commercial_brief,
        conception_log=conception_log,
        llm_run_ids=llm_run_ids,
        synopsis=synopsis,
        tags=tags,
        hook_spec=_result_hook_spec,
        concept_contract=concept_contract,
        hook_card=(
            dict(concept_contract.get("hook_card") or {}) if concept_contract else {}
        ),
        seriality_proof=(
            dict(concept_contract.get("seriality_proof") or {})
            if concept_contract else {}
        ),
        concept_methodology=dict(ctx.get("concept_methodology") or {}),
        hook_candidates=list(ctx.get("hook_candidates") or []),
        story_appeal=story_appeal_report,
        motif_amplification=dict(ctx.get("motif_amplification_report") or {}),
        default_family=dict(ctx.get("default_family_report") or {}),
        title_tournament=dict(_title_tournament_receipt or {}),
        story_spine=story_spine if isinstance(story_spine, dict) else {},
        world_model=world_model_payload if isinstance(world_model_payload, dict) else {},
        degraded_rounds=degradation_tracker.events,
        degraded=bool(degradation_tracker.events),
        degradation_events=degradation_tracker.events,
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
    if not isinstance(existing_overrides, dict):
        existing_overrides = {}
    existing_market = (
        existing_overrides.get("market", {})
        if isinstance(existing_overrides, dict) and isinstance(existing_overrides.get("market"), dict)
        else {}
    )
    profile_market = profile.get("market", {}) if isinstance(profile.get("market"), dict) else {}
    target_platform = (
        profile_market.get("platform_target")
        or market.get("platform_target")
        or existing_market.get("platform_target")
        or ctx.get("platform_target")
        or ctx.get("default_platform")
    )
    seed_profile = (
        {"market": {"platform_target": str(target_platform)}}
        if target_platform
        else None
    )
    fallback_profile = resolve_writing_profile(
        seed_profile,
        genre=str(ctx.get("genre", "general-fiction") or "general-fiction"),
        sub_genre=ctx.get("sub_genre"),
        language=ctx.get("language"),
    ).model_dump(mode="json")

    profile["market"] = _deep_merge_dict(
        _deep_merge_dict(
            _deep_merge_dict(fallback_profile.get("market", {}), existing_market),
            market if isinstance(market, dict) else {},
        ),
        profile_market,
    )

    if "character" not in profile or not profile["character"]:
        profile["character"] = {}
    # Merge character proposal fields
    char_section = profile["character"]
    for key in ("protagonist_archetype", "protagonist_core_drive", "golden_finger",
                "growth_curve", "power_tiers", "conflict_forces",
                "romance_mode", "relationship_tension", "antagonist_mode"):
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
                "power_tiers",
                "conflict_forces",
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

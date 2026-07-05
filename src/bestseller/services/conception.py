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

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, LLMRole, complete_text
from bestseller.services.llm_closed_loop import build_repair_user_prompt, findings_from_exception
from bestseller.services.methodology import render_qimao_regeneration_contract
from bestseller.services.methodology_compiler import MethodologyStage, compile_methodology
from bestseller.services.concept_lab import (
    coerce_concept_lab_bundle,
    render_concept_lab_prompt_block,
)
from bestseller.services.hook_propagation import coerce_hook_spec, render_hook_spec_prompt_block
from bestseller.services.platform_title_workflow import (
    build_story_dna_fallback_title,
    build_title_revision_messages,
    finalize_revised_title,
    is_bare_taxonomy_title,
    select_primary_platform_title,
    should_revise_primary_title,
)
from bestseller.services.writing_profile import (
    resolve_writing_profile,
    sanitize_genre_story_overrides,
)
from bestseller.services.writing_presets import list_genre_presets
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
from bestseller.services.progress_context import emit_activity, emit_milestone

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
    # Surfaced so the web layer can persist these as inspectable book artifacts.
    concept_methodology: dict[str, Any] = field(default_factory=dict)
    hook_candidates: list[dict[str, Any]] = field(default_factory=list)
    # Story/blurb appeal evaluation report (story_appeal.StoryAppealReport.to_dict()).
    # Empty dict when the appeal system is disabled (config) — keeps historical
    # output byte-identical (no-op contract).
    story_appeal: dict[str, Any] = field(default_factory=dict)


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


_ZH_DEFAULT_MOTIF_RE = re.compile(
    r"((父母|父亲|母亲|双亲|家人|亲人|亲属|兄长|哥哥|姐姐|妹妹|弟弟|妻子|丈夫|未婚妻|未婚夫)"
    r"[^。！？；;，,\n]{0,12}"
    r"(失踪|消失|死亡|死去|被害|遇害|惨死|离奇|旧案|真相|身世|血脉|秘密)"
    r"|"
    r"(失踪|消失|死亡|死去|被害|遇害|惨死|离奇|旧案|真相|身世|血脉|秘密)"
    r"[^。！？；;，,\n]{0,12}"
    r"(父母|父亲|母亲|双亲|家人|亲人|亲属))"
)

_EN_DEFAULT_MOTIF_RE = re.compile(
    r"\b("
    r"(missing|dead|death|murdered|killed|disappeared|lost|vanished|orphaned|family secret|bloodline)"
    r"[\w\s-]{0,40}"
    r"(parents?|father|mother|family|relatives?|siblings?)"
    r"|"
    r"(parents?|father|mother|family|relatives?|siblings?)"
    r"[\w\s-]{0,40}"
    r"(missing|dead|death|murdered|killed|disappeared|lost|vanished|secret|bloodline)"
    r")\b",
    re.IGNORECASE,
)

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
    "## 金手指设计原则（务必遵守，否则烂大街没人点）\n"
    "金手指 = 主角的差异化优势，但【形态绝不固定为系统/属性面板】。系统/面板/签到/商城/抽奖"
    "只是众多形态之一，且已极度烂大街——非本书设定的强需求，禁止默认选它。\n"
    "从下列形态按【与本书核心冲突 / 主角 / 世界规律的贴合度】择优，优先新鲜、与本题材独特结合者："
    "血脉或瞳术觉醒 · 上古传承 / 残魂之师 · 丹道炼器符阵的独门手艺 · 特殊体质 / 道身 · "
    "契约异兽 / 器灵 · 重生 / 先知 / 记忆回溯 · 气运掠夺 · 武学功法的推演领悟 · "
    "身份势力 / 信息差 · 逆练禁术 · 词条 / 规则具现 · 一件兵器或宝物 · 因果命格操作"
    "（也可自创更贴合本书的形态）。\n"
    "形态必须长在世界规律上（从设定差分出来，而非硬贴一个外挂）；金手指的【代价 / 限制】"
    "必须与其形态匹配，不能无代价。\n"
    "若本书题材本不依赖外挂（纯武侠 / 历史 / 权谋 / 文学向 / 群像），可不设显性金手指，"
    "改以【谋略 / 武学境界 / 人脉信息 / 性格意志】为差异化优势，并明确写明“无显性金手指，优势在 X”。\n"
    "反同质化：不要与平台上已扎堆的同形态金手指重复。\n"
    "【代价形态硬约束 · 反债务化】除非用户明确要求写债务/借贷/记账题材，金手指与其代价、"
    "以及违反世界规则的代价，【禁止】表达为债、账本、欠条、记账、债务、因果债、灵石债、"
    "宗债、道债等任何金融记账形态（“欠债/还债/连本带利/结算/赎买/记一笔/入账”皆禁）。"
    "代价必须是非金融的具身形态：反噬、污染、损耗（灵机/寿元/心神）、因果烙印、规则代价、"
    "境界隐患、感官剥夺、记忆消解、关系后果。“债”只可作个别角色的背景动机，"
    "不得成为金手指机制或全书代价的默认表达。"
)
_GOLDEN_FINGER_DESIGN_PRINCIPLE_EN = (
    "## Golden-finger design rule (mandatory; a fixed form = cliché = no clicks)\n"
    "The golden finger is the protagonist's differentiating edge, but its FORM must "
    "NOT default to a stat/system panel. Panels/check-ins/shops are ONE option among "
    "many and are heavily oversaturated — never pick one unless this book's premise "
    "truly requires it.\n"
    "Choose the form that best FITS this book's core conflict / protagonist / world "
    "rules, favouring fresh forms unique to the genre: bloodline or eye awakening, "
    "ancient inheritance / mentor-remnant, a signature alchemy/forging/array craft, "
    "special physique, contracted beast / artifact spirit, rebirth/precognition/memory, "
    "fortune-plunder, technique insight, identity/faction/information edge, forbidden "
    "reverse-cultivation, rule/keyword manifestation, a single weapon or treasure, "
    "karma/fate manipulation (or invent a better-fitting one).\n"
    "The form must grow out of the world's laws (derived, not bolted on), and its "
    "cost/limit must match the form.\n"
    "If the genre does not need an external cheat (pure wuxia / history / intrigue / "
    "literary / ensemble), the book may have NO explicit golden finger — use strategy / "
    "martial attainment / network / will as the edge, and state 'no explicit golden "
    "finger; the edge is X'.\n"
    "Anti-homogenisation: do not reuse a golden-finger form already crowded on the platform.\n"
    "[Cost-form hard rule — no debt framing] Unless the user explicitly asked for a "
    "debt/lending/bookkeeping premise, the golden finger's cost — and any world-rule "
    "violation cost — MUST NOT be expressed as debt / ledger / IOU / bookkeeping / "
    "karmic-debt / spirit-stone-debt or any financial-accounting form (no owe/repay/"
    "settle/redeem/'record an entry'). Costs must be non-financial embodied forms: "
    "backlash, corruption, depletion (qi/lifespan/spirit), causal branding, rule-price, "
    "cultivation instability, sensory deprivation, memory erosion, relationship fallout. "
    "'Debt' may appear only as an individual character's backstory motive, never as the "
    "default framing of the mechanism or of the book's costs."
)


def _default_motif_guardrail(ctx: dict[str, Any] | None = None, *, is_en: bool | None = None) -> str:
    """Prompt block that bans fixed family-trauma defaults for new-book conception."""

    if is_en is None:
        is_en = str((ctx or {}).get("language") or "").startswith("en")
    if is_en:
        return (
            "\n\n[Default-motivation ban]\n"
            "Do not use family disappearance/death, hidden bloodline cases, magic heirlooms, "
            "humiliation engagements, or generic revenge as default motivation. Build the protagonist "
            "drive from the selected genre, platform promise, profession/system/world rules, and the "
            "opening event. Unless explicitly supplied by the user, do not make the story about finding "
            "relatives, investigating family cases, or inheriting family secrets."
        )
    return (
        "\n\n【默认动机禁用】\n"
        "不要把亲属失踪/死亡、身世旧案、神秘信物、退婚羞辱、通用复仇当作默认驱动。"
        "主角目标必须从题材类型、平台读者承诺、职业/制度/世界规则、当前开局事件中动态生成。"
        "除非用户明确提供，不得写成寻找亲属、调查家族旧案或继承家族秘密。"
    )


# Financial/ledger vocabulary that keeps colonising cultivation golden fingers.
# ``账`` alone is too broad (账号/结账) so only ledger-specific compounds are
# listed; ``债`` is debt-specific enough to stand alone.
_DEBT_LEDGER_TOKENS: tuple[str, ...] = (
    "债", "账本", "账簿", "欠条", "欠账", "记账", "债务", "连本带利",
    "抹账", "还债", "债币", "赊", "赎身", "抵押", "借贷", "欠债",
    "讨债", "债主", "入账", "记一笔", "利息",
)


def _mentions_debt_theme(*texts: Any) -> bool:
    """True when any text already frames the book around debt/lending.

    Used to *respect user intent*: a book the user deliberately wants about
    debt collection must not be gagged by the anti-debt guardrail.
    """

    blob = " ".join(str(t) for t in texts if t)
    return any(token in blob for token in _DEBT_LEDGER_TOKENS)


def _is_debt_dominated_mechanism(text: Any) -> bool:
    """True when a golden finger / mechanism leans on ledger framing.

    Dominated = at least two ledger-token occurrences, so a single incidental
    mention (``一笔旧债`` in passing) does not trip it but ``债币/欠账/入账``
    stacked into one mechanism does.
    """

    blob = str(text or "")
    if not blob:
        return False
    hits = 0
    for token in _DEBT_LEDGER_TOKENS:
        hits += blob.count(token)
        if hits >= 2:
            return True
    return False


def _anti_debt_metaphor_guardrail(ctx: dict[str, Any] | None = None, *, is_en: bool) -> str:
    """Ban ledger framing of the golden finger / cost — the debt twin of the
    family-trauma ``_default_motif_guardrail``.

    Empty when the user explicitly asked for a debt-themed book (intent in the
    description or hints), so a deliberate 讨债/记账 premise is never gagged.
    """

    ctx = ctx or {}
    if _mentions_debt_theme(ctx.get("description"), ctx.get("user_hints"), ctx.get("premise_seed")):
        return ""
    if is_en:
        return (
            "\n\n[Anti-debt-metaphor guardrail — hard default]\n"
            "Unless the user explicitly asked for a debt/lending/bookkeeping premise, the "
            "golden finger and its cost must NOT be a financial ledger. Ban debt/IOU/ledger/"
            "account/repayment/'owe-and-repay' framing as the FORM of the power or its price. "
            "Express the cost through non-financial, embodied forms instead: backlash, "
            "dao-heart fractures, lifespan drain, sensory deprivation, memory erosion, "
            "bloodline burn, rule/karma branding, personality overwrite — matched to the "
            "golden finger's form."
        )
    return (
        "\n\n【金手指与代价 · 反债务化(硬性默认)】\n"
        "除非用户明确要求写“债务/借贷/记账”题材,金手指的形态与其代价【绝不表达为金融记账形态】:"
        "禁止债、账本、欠条、欠账、记账、债务、连本带利、抹账、还债、债币、赎身、抵押、"
        "“欠了要还”这类债务隐喻当作金手指或其代价的主体。"
        "代价改用非金融的具身形态:反噬、道心裂痕、寿元损耗、感官剥夺、记忆消解、血脉灼烧、"
        "规则/因果烙印、情绪叠加、人格替换等,与金手指形态匹配。"
    )


def _sanitize_forbidden_default_motifs(value: Any, *, is_en: bool) -> Any:
    """Remove family-trauma default motifs from LLM/fallback conception payloads."""

    if isinstance(value, str):
        replacement = _EN_DEFAULT_MOTIF_REPLACEMENT if is_en else _ZH_DEFAULT_MOTIF_REPLACEMENT
        pattern = _EN_DEFAULT_MOTIF_RE if is_en else _ZH_DEFAULT_MOTIF_RE
        sanitized = pattern.sub(replacement, value)
        return sanitized.strip()
    if isinstance(value, list):
        return [_sanitize_forbidden_default_motifs(item, is_en=is_en) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_forbidden_default_motifs(item, is_en=is_en) for item in value)
    if isinstance(value, dict):
        return {
            key: _sanitize_forbidden_default_motifs(item, is_en=is_en)
            for key, item in value.items()
        }
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
    )
    if repair_llm_id is not None:
        llm_run_ids.append(repair_llm_id)
    try:
        return _sanitize_forbidden_default_motifs(_extract_json(repair_text), is_en=is_en), llm_run_ids
    except Exception:
        logger.warning(
            "Conception stage %s repair still produced invalid JSON; using fallback payload.",
            stage,
            exc_info=True,
        )
        try:
            return _sanitize_forbidden_default_motifs(_extract_json(fallback), is_en=is_en), llm_run_ids
        except Exception:
            logger.error(
                "Conception stage %s: both repair and fallback payloads were "
                "unparseable; returning empty payload (downstream will degrade).",
                stage,
                exc_info=True,
            )
            return {}, llm_run_ids


def _build_genre_context(
    genre_key: str,
    chapter_count: int,
    story_facets: object | None = None,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
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


def _concept_methodology_prompt_block(ctx: dict[str, Any]) -> str:
    """Soft 脑洞/爽点 methodology block (Agent ①), empty when disabled/unset."""

    block = ctx.get("concept_methodology_block")
    return f"\n\n{block}" if isinstance(block, str) and block.strip() else ""


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
    if not isinstance(brief, dict) or not brief:
        return f"{concept_block}{hook_block}{qimao_block}"
    label = "[Auto commercial positioning brief]" if str(ctx.get("language", "")).startswith("en") else "【自动商业化立项 brief】"
    return (
        f"\n\n{label}\n{json.dumps(brief, ensure_ascii=False, indent=2)}"
        f"{concept_block}{hook_block}\n{qimao_block}"
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
    market = dict(merged.get("market") or {})
    style = dict(merged.get("style") or {})

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
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _concept_methodology_prompt_block(ctx)
    prompt += _mechanism_dedup_prompt_block(ctx, is_en=False)
    prompt += _anti_debt_metaphor_guardrail(ctx, is_en=False)
    return prompt


# Web-novel cast names so overused — or baked into legacy material packs — that
# the conception LLM keeps defaulting to them, collapsing unrelated books onto
# the same handful of protagonists (the recurring 陆沉/宁尘/苏瑶 problem). These
# are banned outright in zh casts. Cross-book de-dup against actually-used names
# is layered on top via ``_recent_cast_names`` → ``ctx['avoid_names']``.
_CLICHE_NAME_BLOCKLIST: tuple[str, ...] = (
    "陆沉", "陆尘", "陆轩", "陆离", "陆鸣", "陆晨",
    "叶凡", "叶尘", "叶轩", "叶天", "叶辰",
    "林轩", "林动", "林凡", "林夕", "林墨",
    "苏瑶", "苏沐", "苏晴", "苏白",
    "楚风", "楚枫", "萧炎", "萧晨",
    "江晚", "沈追", "顾沉", "宁尘", "方域", "韩立", "秦尘",
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

    from sqlalchemy import select  # noqa: PLC0415

    from bestseller.infra.db.models import CharacterModel  # noqa: PLC0415

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

    from sqlalchemy import select  # noqa: PLC0415

    from bestseller.infra.db.models import ProjectModel  # noqa: PLC0415
    from bestseller.services.genre_taxonomy import canonicalize  # noqa: PLC0415

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
                )
                .order_by(ProjectModel.created_at.desc())
                .limit(limit * 8)
            )
        ).all()
    except Exception:
        logger.debug("recent core-mechanism fetch failed", exc_info=True)
        return []

    entries: list[dict[str, Any]] = []
    for title, row_genre, row_sub_genre, metadata in rows:
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
    """Render ``ctx['avoid_mechanisms']`` as a hard differentiate-from list.

    Empty string when there is nothing to avoid, so prompts are untouched for
    the first book of a genre. Deliberately names no concrete mechanism family
    itself — the avoid-list is data-driven, never baked content.
    """

    items = [
        item for item in (ctx.get("avoid_mechanisms") or []) if isinstance(item, dict)
    ]
    if not items:
        return ""

    lines: list[str] = []
    for item in items[:_MECHANISM_DEDUP_MAX_BOOKS]:
        title = str(item.get("title") or "").strip()
        golden_finger = str(item.get("golden_finger") or "").strip()[
            :_MECHANISM_DEDUP_FIELD_CHARS
        ]
        premise = str(item.get("premise") or "").strip()[:_MECHANISM_DEDUP_FIELD_CHARS]
        tropes = _normalize_string_list(item.get("trope_keywords"))[
            :_MECHANISM_DEDUP_MAX_TROPES
        ]
        parts: list[str] = []
        if is_en:
            if golden_finger:
                parts.append(f"golden finger: {golden_finger}")
            if premise:
                parts.append(f"premise: {premise}")
            if tropes:
                parts.append("tropes: " + ", ".join(tropes))
            lines.append(f'- "{title}" — ' + "; ".join(parts))
        else:
            if golden_finger:
                parts.append(f"金手指：{golden_finger}")
            if premise:
                parts.append(f"前提：{premise}")
            if tropes:
                parts.append("标签：" + "、".join(tropes))
            lines.append(f"- 《{title}》{'；'.join(parts)}")
    if not lines:
        return ""

    if is_en:
        return (
            "\n\n[Mechanism de-duplication — cross-book differentiation, hard constraint]\n"
            "Core mechanisms already used by recent same-genre books in this system:\n"
            + "\n".join(lines)
            + "\nThe new book's core mechanism MUST visibly diverge from every entry above: "
            "how the golden finger works, the cost it exacts, and the premise conflict must "
            "not be isomorphic to any of them. Renaming or reskinning the same mechanism "
            "family does not count as differentiation; if your concept mirrors any entry, "
            "discard it and rebuild from a different mechanism family."
            "\nBeyond the mechanism, the imagery must change too: any image or word that "
            "recurs across two or more entries above (in their titles, golden fingers, or "
            "tropes) must NOT anchor the new book's title, golden-finger name, or selling "
            "points — a new mechanism wearing the same thematic skin still reads as the "
            "same book."
        )
    return (
        "\n\n【机制去重 · 跨书差异化（硬约束）】\n"
        "以下核心机制已被本系统近期同题材作品使用（金手指/前提/卖点标签摘要）：\n"
        + "\n".join(lines)
        + "\n新书的核心机制必须与上述每一条做出肉眼可见的分化：金手指的作用原理、"
        "代价形态、前提冲突都不得与任何一条同构。只换名词、换皮不换骨"
        "（同一机制家族的变体）不算分化；若当前构思与其中任何一条同构，"
        "必须推翻重来，从另一个机制家族另起炉灶。"
        "\n换骨之外还要换皮：凡在上述两条以上条目的书名/金手指/标签里反复出现的"
        "意象或字眼，禁止再充当新书的书名、金手指命名或核心卖点的主导意象——"
        "机制原理不同但主题外衣相同，读者仍会当成同一本书。"
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
        ctx["avoid_mechanisms"] = await _recent_core_mechanisms(
            session,
            genre=str(ctx.get("genre") or "") or None,
            sub_genre=str(ctx.get("sub_genre") or "") or None,
        )
    except Exception:
        logger.debug("mechanism de-dup attach failed", exc_info=True)


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
# A bigram present in at least this many avoid entries is genre background
# (宗门/升级/修仙…) rather than one book's identity, and never counts.
_ECHO_BACKGROUND_ENTRY_COUNT = 3


def _is_cjk_char(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _content_bigrams(text: str) -> set[str]:
    """All CJK-only character bigrams in ``text``."""

    grams: set[str] = set()
    for i in range(len(text) - 1):
        pair = text[i : i + 2]
        if _is_cjk_char(pair[0]) and _is_cjk_char(pair[1]):
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


def _mechanism_echo_report(
    final_result: dict[str, Any],
    entries: list[Any],
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
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

    entry_grams = [_content_bigrams(_entry_echo_text(e)) for e in clean_entries]
    gram_entry_count: dict[str, int] = {}
    for grams in entry_grams:
        for g in grams:
            gram_entry_count[g] = gram_entry_count.get(g, 0) + 1
    background = {
        g for g, n in gram_entry_count.items() if n >= _ECHO_BACKGROUND_ENTRY_COUNT
    }
    background |= _content_bigrams(f"{genre or ''} {sub_genre or ''}")

    report: list[dict[str, Any]] = []
    for entry, grams in zip(clean_entries, entry_grams):
        shared = sorted((candidate_grams & grams) - background)
        span = _longest_common_cjk_span(candidate_text, _entry_echo_text(entry))
        if len(span) < _ECHO_SPAN_MIN_CHARS:
            span = ""
        if span or len(shared) >= _ECHO_BIGRAM_MIN_HITS:
            report.append(
                {
                    "title": str(entry.get("title") or "").strip(),
                    "shared_span": span,
                    "shared_bigrams": shared,
                }
            )
    return report


def _echo_severity(report: list[dict[str, Any]]) -> int:
    """Comparable badness score: verbatim spans dominate, bigrams add up."""

    score = 0
    for item in report:
        score += 3 * len(str(item.get("shared_span") or ""))
        score += len(item.get("shared_bigrams") or [])
    return score


def _render_mechanism_echo_feedback(
    report: list[dict[str, Any]], *, is_en: bool
) -> str:
    """Hard retry feedback naming each collision, empty when report is clean."""

    if not report:
        return ""
    lines: list[str] = []
    for item in report:
        title = str(item.get("title") or "").strip()
        span = str(item.get("shared_span") or "")
        grams = [str(g) for g in (item.get("shared_bigrams") or [])][:8]
        if is_en:
            detail: list[str] = []
            if span:
                detail.append(f'verbatim span "{span}"')
            if grams:
                detail.append("shared imagery: " + ", ".join(grams))
            lines.append(f'- Collides with "{title}" — ' + "; ".join(detail))
        else:
            detail = []
            if span:
                detail.append(f"逐字雷同片段「{span}」")
            if grams:
                detail.append("复用意象：" + "、".join(grams))
            lines.append(f"- 与《{title}》撞车：" + "；".join(detail))
    if is_en:
        return (
            "\n\n[Rewrite required — your final plan echoes existing books]\n"
            + "\n".join(lines)
            + "\nRegenerate the final plan JSON now. The premise opening, golden-finger "
            "name and principle, and dominant imagery must all be rebuilt so none of "
            "the collisions above remain. Do not reuse any quoted span or imagery."
        )
    return (
        "\n\n【重写要求 · 终稿与已有作品撞车】\n"
        + "\n".join(lines)
        + "\n请立即重新生成最终方案 JSON：主角开局处境、金手指命名与作用原理、"
        "书名与核心意象必须全部另起，上面引用的雷同片段与复用意象一个都不得保留。"
    )


def _render_debt_rewrite_feedback(*, is_en: bool) -> str:
    """Retry feedback for a debt-dominated golden finger the user didn't ask for."""

    if is_en:
        return (
            "\n\n[Rewrite required — the golden finger is a financial ledger]\n"
            "The premise/golden finger leans on debt/ledger/IOU/bookkeeping framing, "
            "which the user did NOT request. Rebuild the mechanism so its power and its "
            "cost are non-financial embodied forms (backlash, corruption, qi/lifespan "
            "depletion, causal branding, rule-price, memory erosion) — remove every "
            "debt/账/欠条/记账/结算 word from the golden finger and premise."
        )
    return (
        "\n\n【重写要求 · 金手指沦为账本】\n"
        "当前前提/金手指依赖债、账本、欠条、记账、结算这类金融记账形态，而用户并未要求债务题材。"
        "请重构机制：金手指与其代价一律改为非金融的具身形态（反噬、污染、灵机/寿元损耗、"
        "因果烙印、规则代价、记忆消解），金手指与前提里的债/账/欠条/记账/结算字样一个都不得保留。"
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
        hashlib.sha256(f"{genre_key}:{uuid4().hex}".encode("utf-8")).hexdigest()[:8],
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
    prompt = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"简介：{ctx['description']}\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n请设计角色体系 JSON，包含：\n"
        f'{{"protagonist_archetype": "主角原型（如：重生复仇者、天才少年、隐忍谋略家）",\n'
        f'  "protagonist_name": "为主角取一个自然、好记、符合题材背景的中文名（2-3字）",\n'
        f'  "protagonist_name_reasoning": "命名理由",\n'
        f'  "protagonist_core_drive": "主角核心驱动力",\n'
        f'  "golden_finger": "主角的差异化优势——形态按【与本书冲突/世界规律的贴合度】择优'
        f'（系统/血脉觉醒/上古传承/特殊体质/独门手艺/重生先知/气运/信息差/契约异兽等，可自创），'
        f'【绝不默认系统/属性面板】（已极烂大街）；若题材本不依赖外挂（武侠/历史/权谋/文学向/群像），'
        f'可写“无显性金手指，优势在X（谋略/境界/人脉/性格）”",\n'
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
    prompt += _default_motif_guardrail(ctx, is_en=False)
    prompt += _concept_methodology_prompt_block(ctx)
    prompt += _mechanism_dedup_prompt_block(ctx, is_en=False)
    prompt += _anti_debt_metaphor_guardrail(ctx, is_en=False)
    prompt += _naming_constraint_block(ctx, is_en=False)
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
    # 题材感知的高唤起情绪范例——让玄幻用灭门/夺宝/绝境突破/碾压打脸，而非都市的退婚/重生。
    from bestseller.services.blurb_appeal_gate import platform_blurb_band  # noqa: PLC0415
    from bestseller.services.genre_persona import resolve_persona  # noqa: PLC0415
    from bestseller.services.story_appeal import genre_emotion_exemplars  # noqa: PLC0415

    _emo = "、".join(genre_emotion_exemplars(ctx.get("genre"), ctx.get("sub_genre"))[:6])
    # 简介字数带与验收闸门同源（按目标平台解析；旧版硬编码 80-140 与起点 140-220 打架）。
    _blurb_platform = str(
        market.get("platform_target")
        or ctx.get("platform_target")
        or ctx.get("default_platform")
        or ""
    )
    _band_min, _band_max = platform_blurb_band(_blurb_platform)
    _persona = resolve_persona(
        ctx.get("genre"), ctx.get("sub_genre"),
        tuple(str(t) for t in (ctx.get("tags") or [])),
    )
    _persona_anchor = (
        f"【目标读者画像·先想清写给谁】{_persona.channel}：{_persona.who}。"
        f"他的知识面：{_persona.knowledge}。他要的爽点：{_persona.fantasy}。"
        f"他的雷点(必须避开)：{('、'.join(_persona.turnoffs))}。"
        f"一句话钩子公式：{_persona.hook_formula}。"
        f"——简介与首句钩子必须为这个具体读者量身定做，让他一眼就想点。"
    )
    base = (
        f"题材：{ctx['genre']}（{ctx['sub_genre']}）\n"
        f"目标章节数：{ctx['chapter_count']}章\n"
        f"\n## 市场定位提案\n{json.dumps(market, ensure_ascii=False, indent=2)}\n"
        f"\n## 角色体系提案\n{json.dumps(character, ensure_ascii=False, indent=2)}\n"
        f"\n## 世界观提案\n{json.dumps(world, ensure_ascii=False, indent=2)}\n"
        f"\n## 审查意见\n{json.dumps(review, ensure_ascii=False, indent=2)}\n"
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
        f'{_persona_anchor}'
        f'硬性要求，逐条照做：'
        f'①长度 {_band_min}-{_band_max} 字（中文字符，目标平台带），不是长设定介绍——要短、要狠；'
        f'②首句 ≤30 字，必须是一句能瞬间抓人的强钩：用疑问、反差、或开局冲突事件开场，'
        f'严禁用"穿越到…的他/本以为…"这类平铺设定句开头；'
        f'③卖点三要素必须齐全：主角身份反差 + 开局冲突事件 + 失败代价（不做到会怎样）；'
        f'③b【金手指爽点必须讲清，不能只写代价】：用一句大白话让读者看懂主角靠什么'
        f'（能力/机缘/谋略/外挂）能做到别人做不到的事、由此拿到什么好处或翻盘——'
        f'这一句要读起来"爽/想代入"，而不是只写他要付出什么代价。'
        f'反例（劝退）：只说"那张纸替他扛下代价，扣他十年阳寿"——读者分不清这是金手指还是诅咒；'
        f'正例：先点破"他能把诡异规则里的杀招收进纸里、反手当成自家护身符"，再提代价。'
        f'③c【主线上升感必须可见】：用一句让读者看到这书的上升阶梯或终极目标'
        f'（从X做到Y、越做越大、最终要成为/夺下/对上谁），'
        f'让人知道爽点会持续升级、这书值得一路追——只有开局没有"往哪走"= 主线模糊，必劝退；'
        f'④把【{_emo}】这类【本题材】高唤起情绪事件放在最前面（别用其他题材的情绪词）；'
        f'⑤结尾留一个悬念钩子，绝不剧透关键反转或结局；'
        f'⑥分 2-4 段、动词驱动、克制形容词。'
        f'⑦【新读者可懂铁律】当成写给一个完全没读过本书、不懂任何设定的陌生人看：'
        f'禁止堆砌生造黑话/自定义机制名/系统术语/等级编号（如「灵码编辑器」「怪谈词条」'
        f'「S级/#0371」「数据化修炼」之类）——每个独特概念要么不出现、要么紧跟一句大白话点破'
        f'它是什么、有什么用、不做会怎样；一整段最多保留 1 个需要脑补的专有名词。'
        f'读者看完必须能一句话说出：主角是谁、要干什么、爽点/钩子在哪。'
        f'严禁 AI 腔与套话：本以为/却没想到/命运的齿轮/何去何从/拭目以待/敬请期待/一段不平凡的旅程）",\n'
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
        from bestseller.services.blurb_appeal_gate import platform_blurb_band  # noqa: PLC0415
        from bestseller.services.genre_persona import resolve_persona  # noqa: PLC0415
        from bestseller.services.story_appeal import genre_emotion_exemplars  # noqa: PLC0415

        _emo = "、".join(genre_emotion_exemplars(genre, sub_genre)[:6])
        _p = resolve_persona(genre, sub_genre)
        # 与验收闸门同源的平台字数带（旧版硬编码 80-140 与起点 140-220 打架）。
        _band_min, _band_max = platform_blurb_band(platform)
        system_prompt = (
            "你是网文平台资深编辑。把给定简介按【整改要求】重写成一段【点击型】作品简介"
            "（番茄/起点详情页文案）。只输出重写后的简介正文，不要解释、不要标题。"
        )
        user_prompt = (
            f"题材：{genre}（{sub_genre}）\n"
            f"【目标读者】{_p.channel}：{_p.who}；他要的爽点：{_p.fantasy}；雷点(避开)：{('、'.join(_p.turnoffs))}；"
            f"钩子公式：{_p.hook_formula}\n\n【当前简介】\n{synopsis}\n\n【整改要求】\n{feedback}\n\n"
            f"硬性：{_band_min}-{_band_max}字（按目标平台带）；首句≤30字的强钩（疑问/反差/开局冲突）；卖点三要素齐（身份+冲突+代价）；"
            f"高唤起情绪前置——用【本题材】的情绪事件（如：{_emo}），别套其他题材的情绪词；"
            "结尾留悬念不剧透；禁AI腔（本以为/却没想到/何去何从/敬请期待）；"
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
) -> tuple[str, UUID | None]:
    """Focused title rewrite: LLM proposes N candidates, the zero-token title gate
    picks the best-scoring one (generate-then-select). Fail-open: returns the
    original title on any error / if no candidate beats it. Returns (title, run_id).
    """

    from bestseller.services.title_appeal_gate import evaluate_title_appeal  # noqa: PLC0415

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
        user_prompt = (
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
    )
    if user_hints:
        ctx["user_hints"] = user_hints
        concept_bundle = coerce_concept_lab_bundle(user_hints.get("concept_lab"))
        if concept_bundle is not None:
            ctx["concept_lab"] = concept_bundle.model_dump(mode="json")
        _apply_qimao_hints_to_context(ctx)

    # Agent ①: heat-search → 脑洞/爽点 *methodology* selection. Replaces the old
    # baked concrete bundle with a soft methodology framework the conception
    # agents grow genre-fitting concepts from. Fallback-safe: never blocks a run.
    await _attach_concept_methodology(session, settings, ctx, user_hints=user_hints)

    is_en = ctx.get("language", "zh-CN").startswith("en")
    ctx = _sanitize_forbidden_default_motifs(ctx, is_en=is_en)

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
            if selected_hook_spec is None and candidates:
                selected_hook_spec = candidates[0].spec
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

    def _emit(stage: str, data: dict[str, Any] | None = None) -> None:
        if progress is not None:
            progress(stage, data)

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
    ctx["commercial_brief"] = commercial_brief
    conception_log.append({"round": 0, "agent": "commercial_commissioner", "brief": commercial_brief})

    # ── Round 1: Independent Proposals ──────────────────────────────
    _emit("conception_market", {"round": 1, "agent": "market_strategist"})
    market_user_prompt = _attach_conception_methodology(
        (_market_user_prompt_en if is_en else _market_user_prompt)(ctx, _genre_profile),
        ctx=ctx,
        is_en=is_en,
        token_budget=600,
    )
    market_proposal, stage_llm_ids = await _llm_call_json(
        session, settings,
        role="planner",
        system_prompt=_MARKET_SYSTEM_EN if is_en else _MARKET_SYSTEM,
        user_prompt=market_user_prompt,
        fallback=json.dumps(ctx.get("existing_overrides", {}).get("market", {}), ensure_ascii=False),
        template="conception_market",
        stage="conception.market",
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(stage_llm_ids)
    market_proposal = market_proposal or ctx.get("existing_overrides", {}).get("market", {})
    conception_log.append({"round": 1, "agent": "market_strategist", "proposal": market_proposal})

    _emit("conception_character", {"round": 1, "agent": "character_architect"})
    character_user_prompt = _attach_conception_methodology(
        (_character_user_prompt_en if is_en else _character_user_prompt)(ctx, _genre_profile),
        ctx=ctx,
        is_en=is_en,
        token_budget=800,
    )
    character_proposal, stage_llm_ids = await _llm_call_json(
        session, settings,
        role="planner",
        system_prompt=_CHARACTER_SYSTEM_EN if is_en else _CHARACTER_SYSTEM,
        user_prompt=character_user_prompt,
        fallback=json.dumps(ctx.get("existing_overrides", {}).get("character", {}), ensure_ascii=False),
        template="conception_character",
        stage="conception.character",
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(stage_llm_ids)
    character_proposal = character_proposal or ctx.get("existing_overrides", {}).get("character", {})
    conception_log.append({"round": 1, "agent": "character_architect", "proposal": character_proposal})

    _emit("conception_world", {"round": 1, "agent": "world_builder"})
    world_user_prompt = _attach_conception_methodology(
        (_world_user_prompt_en if is_en else _world_user_prompt)(ctx, _genre_profile),
        ctx=ctx,
        is_en=is_en,
        token_budget=800,
    )
    world_proposal, stage_llm_ids = await _llm_call_json(
        session, settings,
        role="planner",
        system_prompt=_WORLD_SYSTEM_EN if is_en else _WORLD_SYSTEM,
        user_prompt=world_user_prompt,
        fallback=json.dumps(ctx.get("existing_overrides", {}).get("world", {}), ensure_ascii=False),
        template="conception_world",
        stage="conception.world",
        language=str(ctx.get("language") or "zh-CN"),
    )
    llm_run_ids.extend(stage_llm_ids)
    world_proposal = world_proposal or ctx.get("existing_overrides", {}).get("world", {})
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

    # Mechanism echo screen + anti-debt gate: the avoid-list and prompt guardrail
    # are not enough — the model still (a) absorbs the forbidden vocabulary as
    # material (verbatim premise openings, ledger-named golden fingers) and
    # (b) defaults cultivation costs to debt/ledger framing. One focused finalize
    # retry with the specific problems named; keep whichever result is cleaner.
    # Fail-open.
    try:
        _avoid_entries = list(ctx.get("avoid_mechanisms") or [])
        echo_report = _mechanism_echo_report(
            final_result,
            _avoid_entries,
            genre=str(ctx.get("genre") or "") or None,
            sub_genre=str(ctx.get("sub_genre") or "") or None,
        )
        # Debt gate: fire only when the user did NOT ask for a debt theme and the
        # finalized golden finger / premise leans on ledger framing.
        _debt_ok = _mentions_debt_theme(
            ctx.get("description"), ctx.get("user_hints"), ctx.get("premise_seed")
        )
        _gf_text = ""
        _profile = final_result.get("writing_profile")
        if isinstance(_profile, dict):
            _char = _profile.get("character")
            if isinstance(_char, dict):
                _gf_text = str(_char.get("golden_finger") or "")
        debt_hit = (not _debt_ok) and (
            _is_debt_dominated_mechanism(_gf_text)
            or _is_debt_dominated_mechanism(str(final_result.get("premise") or ""))
        )
        if echo_report or debt_hit:
            _emit(
                "conception_mechanism_echo_retry",
                {
                    "collisions": [str(r.get("title") or "") for r in echo_report],
                    "debt_dominated": debt_hit,
                },
            )
            retry_feedback = _render_mechanism_echo_feedback(echo_report, is_en=is_en)
            if debt_hit:
                retry_feedback += _render_debt_rewrite_feedback(is_en=is_en)
            retry_result, retry_llm_ids = await _llm_call_json(
                session, settings,
                role="editor",
                system_prompt=_FINALIZE_SYSTEM_EN if is_en else _FINALIZE_SYSTEM,
                user_prompt=finalize_user_prompt + retry_feedback,
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
            )
            retry_gf = ""
            retry_profile = retry_result.get("writing_profile") if isinstance(retry_result, dict) else None
            if isinstance(retry_profile, dict) and isinstance(retry_profile.get("character"), dict):
                retry_gf = str(retry_profile["character"].get("golden_finger") or "")
            retry_debt = (not _debt_ok) and (
                _is_debt_dominated_mechanism(retry_gf)
                or _is_debt_dominated_mechanism(str(retry_result.get("premise") or ""))
                if isinstance(retry_result, dict) else False
            )
            # Adopt the retry when it is a valid payload that is no worse on echo
            # and strictly resolves the debt hit (or there was no debt hit).
            adopted = (
                isinstance(retry_result, dict)
                and bool(retry_result)
                and _echo_severity(retry_report) <= _echo_severity(echo_report)
                and (not debt_hit or not retry_debt)
            )
            if adopted:
                final_result = retry_result
            conception_log.append(
                {
                    "round": 3,
                    "agent": "mechanism_echo_gate",
                    "collisions": echo_report,
                    "retry_collisions": retry_report,
                    "debt_dominated": debt_hit,
                    "retry_debt_dominated": retry_debt,
                    "adopted_retry": adopted,
                }
            )
    except Exception:
        logger.warning("mechanism echo/debt retry failed; keeping original finalize", exc_info=True)

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
            market_profile["logline"] = selected_hook_spec.one_liner
            market_profile["reader_promise"] = selected_hook_spec.one_liner
            market_profile["anti_commonsense_hook"] = selected_hook_spec.model_dump(mode="json")
    concept_bundle = coerce_concept_lab_bundle(ctx.get("concept_lab"))
    if concept_bundle is not None:
        market_profile = writing_profile.setdefault("market", {})
        if isinstance(market_profile, dict):
            market_profile["logline"] = concept_bundle.one_liner
            market_profile["reader_promise"] = concept_bundle.reader_promise or concept_bundle.one_liner
            market_profile["concept_lab"] = concept_bundle.model_dump(mode="json")
            market_profile["title_seed"] = (
                concept_bundle.title_seeds[0].text
                if concept_bundle.title_seeds
                else ""
            )
            market_profile["selling_points"] = list(
                dict.fromkeys(
                    [
                        *list(market_profile.get("selling_points") or []),
                        *list(concept_bundle.hype_targets[:6]),
                    ]
                )
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
            import re as _re_title  # noqa: PLC0415
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
        synopsis = synopsis[:497] + "..."
    raw_tags = final_result.get("tags", [])
    tags = [str(t).strip() for t in raw_tags if isinstance(t, str) and t.strip()][:10]
    writing_profile = _sanitize_forbidden_default_motifs(writing_profile, is_en=is_en)
    premise = str(_sanitize_forbidden_default_motifs(premise, is_en=is_en))
    synopsis = str(_sanitize_forbidden_default_motifs(synopsis, is_en=is_en))
    title = str(_sanitize_forbidden_default_motifs(title, is_en=is_en))
    tags = [
        str(item).strip()
        for item in _sanitize_forbidden_default_motifs(tags, is_en=is_en)
        if str(item).strip()
    ][:10]

    title_profile = {
        "language": str(ctx.get("language") or "zh-CN"),
        "primary_title": title,
        "primary_category": str(ctx.get("genre") or ""),
        "secondary_category": str(ctx.get("sub_genre") or ""),
        "tags": tags,
        "logline": premise,
        "short_intro": synopsis,
        "reader_promise": (
            writing_profile.get("market", {}).get("reader_promise")
            if isinstance(writing_profile.get("market"), dict)
            else ""
        ),
        "main_characters": [
            {
                "name": "主角" if not is_en else "Protagonist",
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

    # ── Story/blurb appeal evaluation + bounded keep-best regeneration ──
    # Additive: scores the finalized idea + blurb for click-power and
    # bestseller-grade appeal. Disabled in config → skipped entirely so the
    # ConceptionResult is byte-identical to history (no-op contract).
    # Regeneration fires only when the idea is clearly weak (grade ≤ floor),
    # is bounded, keeps the best-scoring variant, and is fail-open.
    story_appeal_report: dict[str, Any] = {}
    # Enforcement decision is captured inside the try but ACTED ON after it, so the
    # fail-open ``except`` below cannot swallow the block (product line "低于80不通过").
    _appeal_block_below = False
    _appeal_blocked_feedback = ""
    try:
        from bestseller.domain.appeal import grade_rank  # noqa: PLC0415
        from bestseller.services.story_appeal import (  # noqa: PLC0415
            build_improvement_feedback,
            evaluate_story_appeal,
            is_appeal_enabled,
            load_story_appeal_config,
        )

        _appeal_cfg = load_story_appeal_config()
        if is_appeal_enabled(_appeal_cfg):
            _ap_genre = str(ctx.get("genre") or genre or "")
            _ap_sub = str(ctx.get("sub_genre") or sub_genre or "")
            _ap_platform = str(target_platform or "")
            _ap_language = str(ctx.get("language") or "zh-CN")
            report = await evaluate_story_appeal(
                session, settings,
                premise=premise, synopsis=synopsis, title=title, tags=tags,
                writing_profile=writing_profile, genre=_ap_genre, sub_genre=_ap_sub,
                chapter_count=chapter_count, platform=_ap_platform, config=_appeal_cfg,
                language=_ap_language,
            )
            regen = _appeal_cfg.get("regeneration", {}) if isinstance(_appeal_cfg, dict) else {}
            floor = str(regen.get("floor_grade", "consider"))
            regen_below_bar = bool(regen.get("regen_below_bar", True))
            max_attempts = int(regen.get("max_attempts", 3))
            _title_min = float((_appeal_cfg.get("meets_bar", {}) or {}).get("title_min", 0))
            best = (report, premise, synopsis, tags, title)
            attempts = 0
            # Product hard line: regenerate while not meeting the bar (blurb<80);
            # else fall back to the grade floor.
            while (
                bool(regen.get("enabled", False))
                and (
                    (not report.meets_bar)
                    if regen_below_bar
                    else grade_rank(report.overall_grade) <= grade_rank(floor)
                )
                and attempts < max_attempts
            ):
                attempts += 1
                try:
                    feedback = build_improvement_feedback(report, _appeal_cfg)
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
                        r_syn = r_syn[:497] + "..."
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
                        )
                        if title_id is not None:
                            llm_run_ids.append(title_id)
                    report = await evaluate_story_appeal(
                        session, settings,
                        premise=r_premise, synopsis=r_syn, title=r_title, tags=r_tags,
                        writing_profile=writing_profile, genre=_ap_genre, sub_genre=_ap_sub,
                        chapter_count=chapter_count, platform=_ap_platform, config=_appeal_cfg,
                        language=_ap_language,
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
                except Exception:
                    logger.warning("appeal regeneration attempt %d failed", attempts, exc_info=True)
                    break
            report, premise, synopsis, tags, title = best
            story_appeal_report = report.to_dict()
            _t_total = report.title.total if report.title else None
            logger.info(
                "Story appeal: premise=%.0f blurb=%.0f title=%s grade=%s meets_bar=%s regen=%d",
                report.premise.total, report.blurb.total, _t_total,
                report.overall_grade, report.meets_bar, attempts,
            )
            # ── 一句话卖点【前置·严格】闸门（logline_gate v2，读者视角语义判别）──
            # 对最终卖点(logline/premise)跑 7 维/两档判官，verdict 附加进 appeal 报告(可见)。
            # 默认 advisory（仅持久化+日志）；config logline_gate.block_expansion=true 时，
            # REJECT 经展示后复用既有 AppealBarNotMetError 拦截链(不静默进规划)。fail-open。
            try:
                from bestseller.services.logline_gate import (  # noqa: PLC0415
                    LoglineAction,
                    evaluate_logline_gate,
                    load_logline_gate_config,
                )

                _lg_cfg = load_logline_gate_config(_appeal_cfg)
                if _lg_cfg.get("enabled", True):
                    _logline_text = (
                        (writing_profile.get("market", {}) or {}).get("logline")
                        if isinstance(writing_profile, dict)
                        else None
                    ) or premise
                    _lg = await evaluate_logline_gate(
                        session, settings,
                        logline=str(_logline_text or ""), premise=premise,
                        genre=_ap_genre, sub_genre=_ap_sub, config=_appeal_cfg,
                    )
                    story_appeal_report["logline_gate"] = _lg.to_dict()
                    logger.info(
                        "Logline gate: action=%s overall=%.2f weakest=%s%s",
                        _lg.action.value, _lg.overall, _lg.weakest_axis,
                        (" | " + " ; ".join(_lg.reasons[:3])) if _lg.reasons else "",
                    )
                    if bool(_lg_cfg.get("block_expansion", False)) \
                            and _lg.action is LoglineAction.REJECT:
                        _appeal_block_below = True
                        _appeal_blocked_feedback = (
                            "一句话卖点未过前置闸门（不予扩充）：\n"
                            + "\n".join(_lg.reasons)
                            + "\n\n整改方向：\n" + "\n".join(_lg.fix_directives)
                        )
            except Exception:
                logger.warning("Logline gate evaluation failed (non-fatal)", exc_info=True)
            # 真拦截：有界重生用尽仍不达标 + 开关开 → 记下决定，try 块外再抛
            # （放块外，确保不被下面 fail-open 的 except 吞掉）。
            if bool((_appeal_cfg.get("meets_bar", {}) or {}).get("block_below_bar", False)) \
                    and not report.meets_bar:
                _appeal_block_below = True
                _appeal_blocked_feedback = build_improvement_feedback(report, _appeal_cfg)
    except Exception:
        logger.warning("Story appeal evaluation failed (non-fatal)", exc_info=True)
        story_appeal_report = {}

    # 产品硬线"低于80不通过"的真拦截：简介/书名经有界重生仍 < 80 → 抛 AppealBarNotMetError。
    # 调用方(web)捕获后把项目置为可见拦截态(带分数+整改建议)，不静默进规划、不留 running 僵尸。
    if _appeal_block_below and story_appeal_report:
        from bestseller.services.story_appeal import AppealBarNotMetError  # noqa: PLC0415

        _b = (story_appeal_report.get("blurb") or {}).get("total")
        _t = (story_appeal_report.get("title") or {}).get("total")
        logger.warning(
            "Conception BLOCKED: appeal bar not met (blurb=%s title=%s) — "
            "not advancing to planning.", _b, _t,
        )
        raise AppealBarNotMetError(story_appeal_report, _appeal_blocked_feedback)

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
        hook_spec=selected_hook_spec.model_dump(mode="json") if selected_hook_spec else None,
        concept_methodology=dict(ctx.get("concept_methodology") or {}),
        hook_candidates=list(ctx.get("hook_candidates") or []),
        story_appeal=story_appeal_report,
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

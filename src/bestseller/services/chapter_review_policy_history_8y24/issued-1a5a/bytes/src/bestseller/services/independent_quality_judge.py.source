"""Independent cross-model, position-swapped blind prose judge.

The result is shadow/advisory evidence only.  Until G4 human calibration is
passed, no caller may use this module as an automatic promotion authority.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.model_catalog import ModelCatalogEntry, get_model_catalog_entry
from bestseller.settings import AppSettings

JUDGE_DIMENSIONS = (
    "reader_pull",
    "character_embodiment",
    "conflict_payoff",
    "emotional_movement",
    "prose_texture",
    "ai_flavor",
    "continuity_contract",
)
CORE_DIMENSIONS = (
    "reader_pull",
    "character_embodiment",
    "conflict_payoff",
)

BlindWinner = Literal["A", "B", "tie"]
CanonicalWinner = Literal["draft_a", "draft_b", "tie"]
JudgeStatus = Literal["decisive", "tie", "ambiguous", "inconclusive"]


class IndependentJudgeConfigurationError(RuntimeError):
    """Raised before calling a judge whose identity cannot be proven."""


class ModelFamilyConflictError(IndependentJudgeConfigurationError):
    """Raised when judge and writer/editor share a model family."""


@dataclass(frozen=True)
class BlindJudgeInput:
    genre: str
    chapter_number: int
    compact_contract: str
    draft_a: str
    draft_b: str


@dataclass(frozen=True)
class DimensionVerdict:
    winner: BlindWinner
    evidence: str


@dataclass(frozen=True)
class DirectionVerdict:
    winner: BlindWinner
    margin: float
    dimensions: dict[str, DimensionVerdict]
    reason: str
    swapped: bool
    fallback_used: bool = False


@dataclass(frozen=True)
class IndependentJudgeResult:
    status: JudgeStatus
    winner: CanonicalWinner | None
    advisory_only: bool = field(default=True, init=False)
    primary_model_key: str | None = None
    secondary_model_key: str | None = None
    secondary_used: bool = False
    dimension_outcomes: dict[str, CanonicalWinner] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    primary_directions: tuple[DirectionVerdict | None, DirectionVerdict | None] = (
        None,
        None,
    )
    secondary_directions: tuple[DirectionVerdict | None, DirectionVerdict | None] = (
        None,
        None,
    )


@dataclass(frozen=True)
class _PairAssessment:
    status: JudgeStatus
    winner: CanonicalWinner | None
    dimension_outcomes: dict[str, CanonicalWinner]
    evidence: list[str]
    reasons: list[str]
    directions: tuple[DirectionVerdict | None, DirectionVerdict | None]
    mean_margin: float = 0.0
    core_disagreement: bool = False


def build_blind_judge_system_prompt() -> str:
    dimensions = "\n".join(f"- {dimension}" for dimension in JUDGE_DIMENSIONS)
    return (
        "你是独立的中文网文质量盲评 Judge。只依据当前提供的题材、章位、精简合同和"
        "两份匿名正文判断；不得猜测作者、模型、策略或生成过程。\n"
        "字数、格式、meta 泄漏、重复和 Canon 冲突已由确定性硬门预筛，不在此重复裁决。\n"
        "逐项判断以下语义维度：\n"
        f"{dimensions}\n"
        "每个维度必须给出正文证据。输出严格 JSON："
        '{"winner":"A|B|tie","margin":0.0-1.0,'
        '"dimensions":{"reader_pull":{"winner":"A|B|tie","evidence":"..."}},'
        '"reason":"..."}'
    )


def build_blind_judge_user_prompt(value: BlindJudgeInput, *, swapped: bool) -> str:
    text_a, text_b = (value.draft_b, value.draft_a) if swapped else (value.draft_a, value.draft_b)
    return (
        f"题材：{value.genre}\n"
        f"章位：第{value.chapter_number}章\n"
        f"精简合同：{value.compact_contract}\n\n"
        f"【A】\n{text_a}\n\n"
        f"【B】\n{text_b}\n\n"
        "请只输出严格 JSON。"
    )


async def run_independent_quality_judge(
    session: AsyncSession,
    settings: AppSettings,
    value: BlindJudgeInput,
    *,
    writer_model: str,
    editor_model: str,
    strict: bool | None = None,
) -> IndependentJudgeResult:
    """Run primary A/B+B/A and an optional cross-family secondary judge."""

    mode = settings.llm.independent_judge_mode
    if mode == "off":
        return IndependentJudgeResult(
            status="inconclusive", winner=None, reasons=["judge_mode_off"]
        )
    strict_family = settings.llm.independent_judge_strict_model_family if strict is None else strict
    primary_key = settings.llm.independent_judge_primary_model_key
    secondary_key = settings.llm.independent_judge_secondary_model_key
    primary_entry = _require_catalog_entry(primary_key)
    if strict_family:
        _validate_model_independence(
            primary_entry,
            writer_model=writer_model,
            editor_model=editor_model,
            label="primary judge",
        )

    primary = await _run_pair(
        session,
        settings,
        value,
        model_catalog_key=primary_key,
    )
    if primary.status == "inconclusive":
        return _to_result(primary, primary_key=primary_key)

    needs_secondary = (
        primary.status in {"tie", "ambiguous"}
        or primary.mean_margin < settings.llm.independent_judge_low_margin
        or primary.core_disagreement
    )
    if not needs_secondary:
        return _to_result(primary, primary_key=primary_key)
    if not secondary_key:
        return _to_result(
            _PairAssessment(
                status="inconclusive",
                winner=None,
                dimension_outcomes=primary.dimension_outcomes,
                evidence=primary.evidence,
                reasons=[*primary.reasons, "secondary_judge_required_but_unconfigured"],
                directions=primary.directions,
            ),
            primary_key=primary_key,
        )

    try:
        secondary_entry = _require_catalog_entry(secondary_key)
    except IndependentJudgeConfigurationError:
        return _to_result(
            _PairAssessment(
                status="inconclusive",
                winner=None,
                dimension_outcomes=primary.dimension_outcomes,
                evidence=primary.evidence,
                reasons=[
                    *primary.reasons,
                    "secondary_judge_required_but_unavailable",
                ],
                directions=primary.directions,
            ),
            primary_key=primary_key,
        )
    if strict_family:
        _validate_model_independence(
            secondary_entry,
            writer_model=writer_model,
            editor_model=editor_model,
            label="secondary judge",
        )
        if model_family(primary_entry.model) == model_family(secondary_entry.model):
            raise ModelFamilyConflictError(
                "primary and secondary judge must use different model families"
            )

    secondary = await _run_pair(
        session,
        settings,
        value,
        model_catalog_key=secondary_key,
    )
    combined = _reconcile_assessments(primary, secondary)
    return IndependentJudgeResult(
        status=combined.status,
        winner=combined.winner,
        primary_model_key=primary_key,
        secondary_model_key=secondary_key,
        secondary_used=True,
        dimension_outcomes=combined.dimension_outcomes,
        evidence=combined.evidence,
        reasons=combined.reasons,
        primary_directions=primary.directions,
        secondary_directions=secondary.directions,
    )


async def _run_pair(
    session: AsyncSession,
    settings: AppSettings,
    value: BlindJudgeInput,
    *,
    model_catalog_key: str,
) -> _PairAssessment:
    forward = await _run_direction(
        session,
        settings,
        value,
        swapped=False,
        model_catalog_key=model_catalog_key,
    )
    backward = await _run_direction(
        session,
        settings,
        value,
        swapped=True,
        model_catalog_key=model_catalog_key,
    )
    directions = (forward, backward)
    if forward is None or backward is None:
        return _PairAssessment(
            status="inconclusive",
            winner=None,
            dimension_outcomes={},
            evidence=[],
            reasons=["invalid_json_after_single_repair"],
            directions=directions,
        )
    if forward.fallback_used or backward.fallback_used:
        return _PairAssessment(
            status="inconclusive",
            winner=None,
            dimension_outcomes={},
            evidence=[],
            reasons=["fallback_judge_evidence"],
            directions=directions,
        )

    forward_winner = _canonical_winner(forward.winner, swapped=False)
    backward_winner = _canonical_winner(backward.winner, swapped=True)
    if forward_winner == backward_winner == "tie":
        status: JudgeStatus = "tie"
        winner: CanonicalWinner = "tie"
    elif forward_winner == backward_winner:
        status = "decisive"
        winner = forward_winner
    else:
        status = "ambiguous"
        winner = "tie"

    dimension_outcomes: dict[str, CanonicalWinner] = {}
    core_disagreement = False
    for dimension in JUDGE_DIMENSIONS:
        first = _canonical_winner(forward.dimensions[dimension].winner, swapped=False)
        second = _canonical_winner(backward.dimensions[dimension].winner, swapped=True)
        dimension_outcomes[dimension] = first if first == second else "tie"
        if dimension in CORE_DIMENSIONS and first != second:
            core_disagreement = True
    evidence = [
        verdict.evidence
        for direction in directions
        if direction is not None
        for verdict in direction.dimensions.values()
    ]
    return _PairAssessment(
        status=status,
        winner=winner,
        dimension_outcomes=dimension_outcomes,
        evidence=evidence,
        reasons=["position_swap_inconsistent"] if status == "ambiguous" else [],
        directions=directions,
        mean_margin=(forward.margin + backward.margin) / 2,
        core_disagreement=core_disagreement,
    )


async def _run_direction(
    session: AsyncSession,
    settings: AppSettings,
    value: BlindJudgeInput,
    *,
    swapped: bool,
    model_catalog_key: str,
) -> DirectionVerdict | None:
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            model_catalog_key=model_catalog_key,
            system_prompt=build_blind_judge_system_prompt(),
            user_prompt=build_blind_judge_user_prompt(value, swapped=swapped),
            fallback_response='{"status":"inconclusive"}',
            prompt_template="independent_quality_judge",
            prompt_version="v1",
            metadata={"position_swapped": swapped, "advisory_only": True},
            max_tokens_override=3072,
        ),
    )
    parsed = _parse_direction(
        completion.content,
        swapped=swapped,
        fallback_used=completion.fallback_used,
    )
    if parsed is not None or completion.fallback_used:
        return parsed

    repaired = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            model_catalog_key=model_catalog_key,
            system_prompt=(
                "你是 JSON 格式修复器。只修复结构，不新增判断。输出必须符合给定 schema。"
            ),
            user_prompt=(
                f"schema dimensions={','.join(JUDGE_DIMENSIONS)}\n"
                f"待修复内容：\n{completion.content}"
            ),
            fallback_response='{"status":"inconclusive"}',
            prompt_template="independent_quality_judge_json_repair",
            prompt_version="v1",
            metadata={"position_swapped": swapped, "advisory_only": True},
            max_tokens_override=2048,
        ),
    )
    return _parse_direction(
        repaired.content,
        swapped=swapped,
        fallback_used=repaired.fallback_used,
    )


def _parse_direction(raw: str, *, swapped: bool, fallback_used: bool) -> DirectionVerdict | None:
    payload = _extract_json_mapping(raw)
    if payload is None:
        return None
    winner = _blind_winner(payload.get("winner"))
    if winner is None:
        return None
    try:
        margin = float(payload.get("margin"))
    except (TypeError, ValueError):
        return None
    if not 0 <= margin <= 1:
        return None
    raw_dimensions = payload.get("dimensions")
    if not isinstance(raw_dimensions, Mapping) or set(raw_dimensions) != set(JUDGE_DIMENSIONS):
        return None
    dimensions: dict[str, DimensionVerdict] = {}
    for key in JUDGE_DIMENSIONS:
        item = raw_dimensions.get(key)
        if not isinstance(item, Mapping):
            return None
        selected = _blind_winner(item.get("winner"))
        evidence = str(item.get("evidence") or "").strip()
        if selected is None or not evidence:
            return None
        dimensions[key] = DimensionVerdict(winner=selected, evidence=evidence)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        return None
    return DirectionVerdict(
        winner=winner,
        margin=margin,
        dimensions=dimensions,
        reason=reason,
        swapped=swapped,
        fallback_used=fallback_used,
    )


def model_family(model: str) -> str:
    """Normalize provider wrappers to the underlying model family."""

    normalized = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    for family, probes in (
        ("claude", ("anthropic", "claude")),
        ("deepseek", ("deepseek",)),
        ("minimax", ("minimax",)),
        ("mistral", ("mistral",)),
        ("qwen", ("qwen", "aliyun")),
        ("kimi", ("kimi", "moonshot")),
        ("llama", ("llama", "meta")),
        ("gemini", ("gemini", "google")),
        ("gpt", ("gpt", "openai-o")),
    ):
        if any(probe in normalized for probe in probes):
            return family
    return normalized.split("-")[0] if normalized else "unknown"


def _require_catalog_entry(key: str | None) -> ModelCatalogEntry:
    if not key:
        raise IndependentJudgeConfigurationError("judge model catalog key is required")
    entry = get_model_catalog_entry(key)
    if entry is None:
        raise IndependentJudgeConfigurationError(f"judge model catalog entry does not exist: {key}")
    if not entry.available:
        raise IndependentJudgeConfigurationError(f"judge model catalog entry is unavailable: {key}")
    return entry


def _validate_model_independence(
    entry: ModelCatalogEntry,
    *,
    writer_model: str,
    editor_model: str,
    label: str,
) -> None:
    judge_family = model_family(entry.model)
    conflicting = {
        family
        for family in (model_family(writer_model), model_family(editor_model))
        if family == judge_family
    }
    if conflicting:
        raise ModelFamilyConflictError(
            f"{label} family {judge_family!r} conflicts with writer/editor"
        )


def _reconcile_assessments(primary: _PairAssessment, secondary: _PairAssessment) -> _PairAssessment:
    if secondary.status == "inconclusive":
        return secondary
    if primary.winner == secondary.winner and primary.winner in {
        "draft_a",
        "draft_b",
    }:
        status: JudgeStatus = "decisive"
        winner = primary.winner
    elif primary.winner == "tie" and secondary.winner in {"draft_a", "draft_b"}:
        status = "decisive"
        winner = secondary.winner
    elif secondary.winner == "tie" and primary.status == "decisive":
        status = "ambiguous"
        winner = "tie"
    else:
        status = "ambiguous"
        winner = "tie"
    dimensions = {
        key: (
            primary.dimension_outcomes.get(key, "tie")
            if primary.dimension_outcomes.get(key) == secondary.dimension_outcomes.get(key)
            else "tie"
        )
        for key in JUDGE_DIMENSIONS
    }
    return _PairAssessment(
        status=status,
        winner=winner,
        dimension_outcomes=dimensions,
        evidence=[*primary.evidence, *secondary.evidence],
        reasons=list(
            dict.fromkeys(
                [
                    *primary.reasons,
                    *secondary.reasons,
                    *(["cross_judge_disagreement"] if status == "ambiguous" else []),
                ]
            )
        ),
        directions=primary.directions,
    )


def _to_result(assessment: _PairAssessment, *, primary_key: str) -> IndependentJudgeResult:
    return IndependentJudgeResult(
        status=assessment.status,
        winner=assessment.winner,
        primary_model_key=primary_key,
        dimension_outcomes=assessment.dimension_outcomes,
        evidence=assessment.evidence,
        reasons=assessment.reasons,
        primary_directions=assessment.directions,
    )


def _canonical_winner(winner: BlindWinner, *, swapped: bool) -> CanonicalWinner:
    if winner == "tie":
        return "tie"
    if (winner == "A" and not swapped) or (winner == "B" and swapped):
        return "draft_a"
    return "draft_b"


def _blind_winner(value: object) -> BlindWinner | None:
    label = str(value or "").strip().lower()
    if label in {"a", "甲"}:
        return "A"
    if label in {"b", "乙"}:
        return "B"
    if label in {"tie", "持平", "平"}:
        return "tie"
    return None


def _extract_json_mapping(raw: str) -> Mapping[str, Any] | None:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None

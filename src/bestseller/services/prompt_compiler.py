"""Deterministic, fail-closed compiler for complete writer prompts.

This module is deliberately independent from the production draft builders.
Callers provide typed blocks; the compiler resolves authority, semantic
duplicates, structural limits, and the *combined* system + user input budget.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from hashlib import sha256
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PromptChannel = Literal["system", "user"]
PromptLayer = Literal["hard_canon", "scene_spec", "output", "craft", "optional"]
TrimPolicy = Literal["drop", "truncate_tail", "truncate_head", "preserve"]

_LAYER_RANK: dict[PromptLayer, int] = {
    "hard_canon": 0,
    "scene_spec": 1,
    "output": 2,
    "craft": 3,
    "optional": 4,
}
_CORE_LAYERS = frozenset({"hard_canon", "scene_spec", "output"})
_FAMILY_SEPARATOR_RE = re.compile(r"[-_\s]+")
_SPACE_RE = re.compile(r"\s+")


class PromptBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=160)
    channel: PromptChannel
    layer: PromptLayer
    authority: int
    instruction_family: str = Field(min_length=1, max_length=200)
    required: bool = False
    min_tokens: int = Field(default=0, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)
    trim_policy: TrimPolicy = "drop"
    source: str = Field(min_length=1, max_length=400)
    text: str = Field(min_length=1)
    # Optional on the legacy surface; required when callers opt into the
    # canonical provenance gate below.
    provenance: PromptProvenance | None = None
    enhancer_key: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_token_window(self) -> PromptBlock:
        if self.max_tokens is not None and self.min_tokens > self.max_tokens:
            raise ValueError("min_tokens cannot exceed max_tokens")
        return self


class PromptProvenance(BaseModel, frozen=True):
    """Where a prompt block was sourced from, for audit and snapshot gating."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "canonical_snapshot",
        "creation_intent",
        "scene_spec",
        "genre_pack",
        "user_input",
        "derived",
        "legacy",
    ]
    source_id: str | None = Field(default=None, max_length=240)
    source_hash: str | None = Field(default=None, max_length=128)
    field_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_canonical_hash(self) -> PromptProvenance:
        if self.kind == "canonical_snapshot" and not self.source_hash:
            raise ValueError("canonical_snapshot provenance requires source_hash")
        return self


class PromptCompilerReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    kept: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    truncated: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    system_tokens: int = Field(ge=0)
    user_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    budget_tokens: int = Field(gt=0)
    safety_margin: float = Field(ge=0, lt=1)
    safety_margin_tokens: int = Field(ge=0)
    usable_budget_tokens: int = Field(gt=0)
    budget_remaining_tokens: int = Field(ge=0)
    required_block_count: int = Field(ge=0)
    required_blocks_kept: tuple[str, ...] = ()
    required_complete: bool
    final_hash: str = Field(min_length=64, max_length=64)
    provenance_sources: tuple[str, ...] = ()
    canonical_snapshot_hash: str | None = None


class CompiledPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: str
    user: str
    report: PromptCompilerReport


class PromptCompilerError(ValueError):
    """Base class for fail-closed prompt compilation errors."""


class PromptConflictError(PromptCompilerError):
    def __init__(self, message: str, *, conflicts: Sequence[str]) -> None:
        self.conflicts = tuple(conflicts)
        super().__init__(message)


class PromptBudgetError(PromptCompilerError):
    def __init__(self, message: str, *, required_keys: Sequence[str]) -> None:
        self.required_keys = tuple(required_keys)
        super().__init__(message)


def estimate_prompt_tokens(text: str) -> int:
    """Return a deterministic CJK-aware token estimate.

    Provider reconciliation is intentionally outside this core compiler.  The
    safety margin absorbs estimator drift until Phase 3.2 records real usage.
    """

    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    if cjk >= max(1, len(text) // 3):
        return max(1, (len(text) + 1) // 2)
    return max(1, (len(text) + 3) // 4)


def compile_prompt(
    blocks: Iterable[PromptBlock],
    *,
    total_budget_tokens: int,
    safety_margin: float,
    canonical_snapshot_hash: str | None = None,
    require_provenance: bool = False,
    selected_enhancer_keys: Iterable[str] | None = None,
) -> CompiledPrompt:
    """Compile typed blocks without fallback or process-global report state."""

    if total_budget_tokens <= 0:
        raise ValueError("total_budget_tokens must be positive")
    if not 0 <= safety_margin < 1:
        raise ValueError("safety_margin must be in [0, 1)")

    block_list = list(blocks)
    selected_effects = {
        str(key).strip() for key in (selected_enhancer_keys or ()) if str(key).strip()
    }
    enhancer_dropped = tuple(
        item.key
        for item in block_list
        if item.enhancer_key is not None and item.enhancer_key not in selected_effects
    )
    ordered = sorted(
        (
            item
            for item in block_list
            if item.enhancer_key is None or item.enhancer_key in selected_effects
        ),
        key=_block_sort_key,
    )
    if require_provenance:
        missing = tuple(item.key for item in ordered if item.provenance is None)
        if missing:
            raise PromptConflictError(
                "prompt provenance is required for canonical compilation",
                conflicts=missing,
            )
    if canonical_snapshot_hash:
        stale = tuple(
            item.key
            for item in ordered
            if item.provenance is not None
            and item.provenance.kind == "canonical_snapshot"
            and item.provenance.source_hash != canonical_snapshot_hash
        )
        if stale:
            raise PromptConflictError(
                "prompt block references a stale canonical snapshot",
                conflicts=stale,
            )
    if len({item.key for item in ordered}) != len(ordered):
        duplicate_keys = _duplicate_keys(ordered)
        raise PromptConflictError(
            f"duplicate prompt block keys: {', '.join(duplicate_keys)}",
            conflicts=duplicate_keys,
        )

    selected, duplicate_keys = _dedupe_semantic_families(ordered)
    selected, structurally_dropped = _enforce_structure_limits(selected)
    selected, capped_dropped, capped_truncated = _apply_block_maxima(selected)

    usable_budget = math.floor(total_budget_tokens * (1 - safety_margin))
    if usable_budget <= 0:
        raise PromptBudgetError(
            "writer prompt safety margin leaves no usable budget",
            required_keys=tuple(item.key for item in selected if item.required),
        )
    safety_tokens = total_budget_tokens - usable_budget

    required = sorted((item for item in selected if item.required), key=_block_sort_key)
    required_system, required_user = _render_channels(required)
    required_total = estimate_prompt_tokens(required_system) + estimate_prompt_tokens(required_user)
    if required_total > usable_budget:
        core_keys = tuple(item.key for item in required if item.layer in _CORE_LAYERS)
        raise PromptBudgetError(
            "required hard core exceeds combined writer prompt budget",
            required_keys=core_keys or tuple(item.key for item in required),
        )

    kept: list[PromptBlock] = list(required)
    dropped: list[str] = [*enhancer_dropped, *structurally_dropped, *capped_dropped]
    truncated: list[str] = list(capped_truncated)
    for candidate in sorted((item for item in selected if not item.required), key=_block_sort_key):
        trial = sorted([*kept, candidate], key=_block_sort_key)
        if _rendered_total_tokens(trial) <= usable_budget:
            kept.append(candidate)
            continue
        trimmed = _fit_nonrequired_block(candidate, kept, usable_budget)
        if trimmed is None:
            dropped.append(candidate.key)
            continue
        kept.append(trimmed)
        truncated.append(candidate.key)

    kept = sorted(kept, key=_block_sort_key)
    system, user = _render_channels(kept)
    system_tokens = estimate_prompt_tokens(system)
    user_tokens = estimate_prompt_tokens(user)
    total_tokens = system_tokens + user_tokens
    if total_tokens > usable_budget:
        raise PromptBudgetError(
            "compiled prompt exceeds combined writer prompt budget",
            required_keys=tuple(item.key for item in required),
        )

    required_keys = tuple(item.key for item in required)
    kept_keys = tuple(item.key for item in kept)
    required_kept = tuple(key for key in required_keys if key in kept_keys)
    final_hash = _prompt_hash(system, user)
    report = PromptCompilerReport(
        kept=kept_keys,
        dropped=_unique_tuple(dropped),
        truncated=_unique_tuple(truncated),
        duplicates=duplicate_keys,
        conflicts=(),
        system_tokens=system_tokens,
        user_tokens=user_tokens,
        total_tokens=total_tokens,
        budget_tokens=total_budget_tokens,
        safety_margin=safety_margin,
        safety_margin_tokens=safety_tokens,
        usable_budget_tokens=usable_budget,
        budget_remaining_tokens=usable_budget - total_tokens,
        required_block_count=len(required_keys),
        required_blocks_kept=required_kept,
        required_complete=required_kept == required_keys,
        final_hash=final_hash,
        provenance_sources=tuple(
            sorted(
                {
                    item.provenance.source_id or item.provenance.kind
                    for item in kept
                    if item.provenance is not None
                }
            )
        ),
        canonical_snapshot_hash=canonical_snapshot_hash,
    )
    return CompiledPrompt(system=system, user=user, report=report)


def _block_sort_key(block: PromptBlock) -> tuple[int, int, str, str]:
    return (_LAYER_RANK[block.layer], -block.authority, block.key, block.source)


def _normalize_family(value: str) -> str:
    return _FAMILY_SEPARATOR_RE.sub("_", value.strip().casefold())


def _normalize_instruction(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip()).casefold()


def _duplicate_keys(blocks: Sequence[PromptBlock]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in blocks:
        if item.key in seen and item.key not in duplicates:
            duplicates.append(item.key)
        seen.add(item.key)
    return tuple(duplicates)


def _dedupe_semantic_families(
    blocks: Sequence[PromptBlock],
) -> tuple[list[PromptBlock], tuple[str, ...]]:
    families: dict[str, list[PromptBlock]] = {}
    for item in blocks:
        families.setdefault(_normalize_family(item.instruction_family), []).append(item)

    selected: list[PromptBlock] = []
    duplicates: list[str] = []
    for family in sorted(families):
        members = sorted(families[family], key=_block_sort_key)
        required = [item for item in members if item.required]
        required_texts = {_normalize_instruction(item.text) for item in required}
        if len(required_texts) > 1:
            conflicts = tuple(item.key for item in required)
            raise PromptConflictError(
                f"conflicting required hard blocks for instruction family {family}",
                conflicts=conflicts,
            )
        winner = sorted(required, key=_block_sort_key)[0] if required else members[0]
        selected.append(winner)
        duplicates.extend(item.key for item in members if item.key != winner.key)
    return sorted(selected, key=_block_sort_key), tuple(duplicates)


def _enforce_structure_limits(
    blocks: Sequence[PromptBlock],
) -> tuple[list[PromptBlock], tuple[str, ...]]:
    selected = list(blocks)
    dropped: list[str] = []
    selected, removed = _limit_family_bucket(
        selected,
        predicate=_is_primary_task,
        limit=1,
        label="primary task",
    )
    dropped.extend(removed)
    selected, removed = _limit_family_bucket(
        selected,
        predicate=_is_hard_obligation,
        limit=5,
        label="hard obligations",
    )
    dropped.extend(removed)
    selected, removed = _limit_family_bucket(
        selected,
        predicate=lambda item: _is_craft_effect(item, "primary"),
        limit=1,
        label="primary craft effect",
    )
    dropped.extend(removed)
    selected, removed = _limit_family_bucket(
        selected,
        predicate=lambda item: _is_craft_effect(item, "secondary"),
        limit=1,
        label="secondary craft effect",
    )
    dropped.extend(removed)
    return sorted(selected, key=_block_sort_key), _unique_tuple(dropped)


def _limit_family_bucket(
    blocks: Sequence[PromptBlock],
    *,
    predicate: Callable[[PromptBlock], bool],
    limit: int,
    label: str,
) -> tuple[list[PromptBlock], tuple[str, ...]]:
    bucket = sorted((item for item in blocks if predicate(item)), key=_block_sort_key)
    if len(bucket) <= limit:
        return list(blocks), ()
    required = [item for item in bucket if item.required]
    if len(required) > limit:
        conflicts = tuple(item.key for item in required)
        raise PromptConflictError(
            f"required {label} exceed limit {limit}",
            conflicts=conflicts,
        )
    keep = [*required]
    keep.extend(item for item in bucket if not item.required and item not in keep)
    keep_keys = {item.key for item in keep[:limit]}
    removed = tuple(item.key for item in bucket if item.key not in keep_keys)
    return [item for item in blocks if item.key not in removed], removed


def _is_primary_task(block: PromptBlock) -> bool:
    family = _normalize_family(block.instruction_family)
    return family == "primary_task" or family.endswith(".primary_task")


def _is_hard_obligation(block: PromptBlock) -> bool:
    family = _normalize_family(block.instruction_family)
    return family.startswith("hard_obligation.") or ".hard_obligation." in family


def _is_craft_effect(block: PromptBlock, rank: str) -> bool:
    family = _normalize_family(block.instruction_family)
    return block.layer == "craft" and family.startswith(f"craft.effect.{rank}.")


def _apply_block_maxima(
    blocks: Sequence[PromptBlock],
) -> tuple[list[PromptBlock], tuple[str, ...], tuple[str, ...]]:
    selected: list[PromptBlock] = []
    dropped: list[str] = []
    truncated: list[str] = []
    for item in blocks:
        if item.max_tokens is None or estimate_prompt_tokens(item.text) <= item.max_tokens:
            selected.append(item)
            continue
        trimmed = _trim_block_to_limit(item, item.max_tokens)
        if trimmed is None:
            if item.required:
                raise PromptBudgetError(
                    f"required block {item.key} exceeds its max_tokens",
                    required_keys=(item.key,),
                )
            dropped.append(item.key)
            continue
        selected.append(trimmed)
        truncated.append(item.key)
    return selected, tuple(dropped), tuple(truncated)


def _fit_nonrequired_block(
    candidate: PromptBlock,
    kept: Sequence[PromptBlock],
    usable_budget: int,
) -> PromptBlock | None:
    if candidate.trim_policy not in {"truncate_tail", "truncate_head"}:
        return None
    low = 1
    high = len(candidate.text)
    best: PromptBlock | None = None
    while low <= high:
        midpoint = (low + high) // 2
        text = (
            candidate.text[:midpoint].rstrip()
            if candidate.trim_policy == "truncate_tail"
            else candidate.text[-midpoint:].lstrip()
        )
        if not text:
            low = midpoint + 1
            continue
        trial_block = candidate.model_copy(update={"text": text})
        if estimate_prompt_tokens(text) < candidate.min_tokens:
            low = midpoint + 1
            continue
        total = _rendered_total_tokens([*kept, trial_block])
        if total <= usable_budget:
            best = trial_block
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _trim_block_to_limit(block: PromptBlock, token_limit: int) -> PromptBlock | None:
    if block.trim_policy not in {"truncate_tail", "truncate_head"}:
        return None
    low = 1
    high = len(block.text)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        text = (
            block.text[:midpoint].rstrip()
            if block.trim_policy == "truncate_tail"
            else block.text[-midpoint:].lstrip()
        )
        if estimate_prompt_tokens(text) <= token_limit:
            best = text
            low = midpoint + 1
        else:
            high = midpoint - 1
    if not best or estimate_prompt_tokens(best) < block.min_tokens:
        return None
    return block.model_copy(update={"text": best})


def _rendered_total_tokens(blocks: Sequence[PromptBlock]) -> int:
    system, user = _render_channels(sorted(blocks, key=_block_sort_key))
    return estimate_prompt_tokens(system) + estimate_prompt_tokens(user)


def _render_channels(blocks: Sequence[PromptBlock]) -> tuple[str, str]:
    system = "\n\n".join(item.text.strip() for item in blocks if item.channel == "system")
    user = "\n\n".join(item.text.strip() for item in blocks if item.channel == "user")
    return system, user


def _prompt_hash(system: str, user: str) -> str:
    payload = json.dumps(
        {"system": system, "user": user},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _unique_tuple(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "CompiledPrompt",
    "PromptBlock",
    "PromptBudgetError",
    "PromptCompilerError",
    "PromptCompilerReport",
    "PromptConflictError",
    "PromptProvenance",
    "compile_prompt",
    "estimate_prompt_tokens",
]

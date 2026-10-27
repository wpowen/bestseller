"""Route a review to a repair capability without granting it creative authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.services.logline_gate import (
    AXIS_KEYS,
    LoglineAction,
    LoglineGateVerdict,
    load_logline_gate_config,
)

_EXPRESSION_AXES = frozenset({"click_hook", "payoff_promise", "concrete_picture"})


def intent_recheck_passed(
    payload: object,
    *,
    intent_tags: list[str],
    candidate: Mapping[str, Any],
    require_cost_check: bool = False,
) -> bool:
    """Unknown is not a conviction, but cannot clear an existing conviction."""
    if not isinstance(payload, Mapping):
        return False
    if payload.get("counter_elements") != []:
        return False
    if require_cost_check and payload.get("cost_violations") != []:
        return False
    if payload.get("cost_violations") not in (None, []):
        return False
    items = payload.get("items")
    if not isinstance(items, Mapping):
        return False

    def texts(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            return [text for child in value.values() for text in texts(child)]
        if isinstance(value, list):
            return [text for child in value for text in texts(child)]
        return []

    source_texts = texts(candidate)
    for tag in intent_tags:
        entry = items.get(tag)
        if not isinstance(entry, Mapping) or entry.get("pass") is not True:
            return False
        quote = entry.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            return False
        if not any(quote in text for text in source_texts):
            return False
    return bool(intent_tags) or require_cost_check


def logline_repair_scope(
    verdict: LoglineGateVerdict, config: Mapping[str, Any] | None = None
) -> str:
    """A score alone cannot authorize changing the underlying story.

    Old reports remain readable, but a structural score with no scoped diagnosis
    needs clarification, not a sentence rewrite or an automatic story rewrite.
    """
    if verdict.action is LoglineAction.EXPAND:
        return "none"
    if not getattr(verdict, "llm_used", False):
        return "review_unavailable"
    if getattr(verdict, "weakest_axis", None) == "condemned_structure":
        return "expression"
    gate = load_logline_gate_config(dict(config or {}))
    floor = float(gate["pass_floor"])
    scores = getattr(verdict, "scores", {}) or {}
    why = getattr(verdict, "axis_why", None) or {}
    if any(
        isinstance(detail, Mapping) and detail.get("repair_scope") == "insufficient_context"
        for detail in why.values()
    ):
        return "insufficient_context"
    scopes = set()
    weak_axes = [axis for axis in AXIS_KEYS if float(scores.get(axis, 0.0)) < floor]
    # Overall can miss its threshold while every axis exceeds pass_floor.
    # In that case use the explicit diagnoses rather than inventing missing context.
    for axis in weak_axes or [axis for axis in AXIS_KEYS if axis in why]:
        detail = why.get(axis)
        scope = detail.get("repair_scope") if isinstance(detail, Mapping) else None
        if scope not in {"expression", "story", "insufficient_context"}:
            scope = "expression" if axis in _EXPRESSION_AXES else "insufficient_context"
        scopes.add(scope)
    if "insufficient_context" in scopes:
        return "insufficient_context"
    if "story" in scopes:
        return "story"
    return "expression" if scopes else "insufficient_context"

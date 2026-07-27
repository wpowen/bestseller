"""Typed, evidence-backed story state transitions.

This module deliberately has no persistence or pipeline dependencies.  Older
planner payloads can still be represented by passing a bare value to
``StoryState.from_mapping``; new transitions get deterministic validation and
transactional application semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from numbers import Number
from typing import Any


class StateCategory(StrEnum):
    CAPABILITY = "capability"
    KNOWLEDGE = "knowledge"
    RELATIONSHIP = "relationship"
    RESOURCE = "resource"
    CLUE = "clue"
    EXPOSURE = "exposure"
    DEBT = "debt"


class MonotonicPolicy(StrEnum):
    ANY = "any"
    INCREASING = "increasing"
    DECREASING = "decreasing"
    NON_DECREASING = "non_decreasing"
    NON_INCREASING = "non_increasing"


@dataclass(frozen=True)
class StoryStateValue:
    category: StateCategory | None
    value: Any


@dataclass(frozen=True)
class StoryStateTransition:
    key: str
    category: StateCategory | str
    before: Any
    operator: str
    after: Any
    evidence: str | tuple[str, ...]
    monotonic: MonotonicPolicy | str = MonotonicPolicy.ANY

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", StateCategory(self.category))
        object.__setattr__(
            self,
            "monotonic",
            MonotonicPolicy(str(self.monotonic).replace("-", "_")),
        )
        if not self.key.strip():
            raise ValueError("story state key must not be empty")
        operator_aliases = {"increase": "add", "decrease": "subtract", "replace": "set"}
        normalized_operator = operator_aliases.get(self.operator, self.operator)
        object.__setattr__(self, "operator", normalized_operator)
        if normalized_operator not in {"set", "add", "subtract", "append", "remove"}:
            raise ValueError(f"unsupported story state operator: {self.operator}")


@dataclass
class StoryState:
    values: dict[str, StoryStateValue] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> StoryState:
        parsed: dict[str, StoryStateValue] = {}
        for key, raw in values.items():
            if isinstance(raw, StoryStateValue):
                parsed[str(key)] = raw
            elif isinstance(raw, Mapping) and "value" in raw:
                parsed[str(key)] = StoryStateValue(
                    category=StateCategory(raw.get("category", StateCategory.KNOWLEDGE)),
                    value=deepcopy(raw["value"]),
                )
            else:  # legacy state maps used key -> scalar
                parsed[str(key)] = StoryStateValue(None, deepcopy(raw))
        return cls(parsed)


def _numeric(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, Number) and not isinstance(value, bool)


def _check_monotonic(before: Any, after: Any, policy: MonotonicPolicy) -> None:  # noqa: ANN401
    if policy is MonotonicPolicy.ANY:
        return
    if not (_numeric(before) and _numeric(after)):
        raise ValueError("monotonic policy requires numeric before/after values")
    if policy is MonotonicPolicy.INCREASING and after <= before:
        raise ValueError(f"monotonic increasing violation: {before}->{after}")
    if policy is MonotonicPolicy.NON_DECREASING and after < before:
        raise ValueError(f"monotonic non_decreasing violation: {before}->{after}")
    if policy is MonotonicPolicy.DECREASING and after >= before:
        raise ValueError(f"monotonic decreasing violation: {before}->{after}")
    if policy is MonotonicPolicy.NON_INCREASING and after > before:
        raise ValueError(f"monotonic non_increasing violation: {before}->{after}")


def validate_story_state_transition(transition: StoryStateTransition) -> None:
    """Validate a transition independent of the current state store."""

    if not isinstance(transition, StoryStateTransition):
        transition = StoryStateTransition(**transition)  # type: ignore[arg-type]
    if isinstance(transition.evidence, tuple):
        has_evidence = any(str(item).strip() for item in transition.evidence)
    else:
        has_evidence = bool(str(transition.evidence).strip())
    if not has_evidence:
        raise ValueError("story state transition requires evidence")
    if transition.operator in {"add", "subtract"}:
        if not (_numeric(transition.before) and _numeric(transition.after)):
            raise ValueError(f"{transition.operator} requires numeric values")
    if transition.operator in {"append", "remove"} and not isinstance(
        transition.before, (list, tuple, set)
    ):
        raise ValueError(f"{transition.operator} requires a collection before value")
    _check_monotonic(transition.before, transition.after, transition.monotonic)


def apply_story_state_transitions(
    state: StoryState, transitions: list[StoryStateTransition] | tuple[StoryStateTransition, ...]
) -> StoryState:
    """Apply a batch atomically; invalid transitions leave ``state`` untouched."""

    candidate = deepcopy(state)
    for transition in transitions:
        validate_story_state_transition(transition)
        current = candidate.values.get(transition.key)
        if current is not None and current.value != transition.before:
            raise ValueError(
                f"story state before mismatch for {transition.key}: "
                f"expected {transition.before!r}, found {current.value!r}"
            )
        if (
            current is not None
            and current.category is not None
            and current.category != transition.category
        ):
            raise ValueError(f"story state category mismatch for {transition.key}")
        value = transition.after
        if transition.operator == "subtract":
            value = transition.after
        elif transition.operator == "append":
            value = [*list(transition.before), transition.after]
        elif transition.operator == "remove":
            value = [item for item in transition.before if item != transition.after]
        candidate.values[transition.key] = StoryStateValue(transition.category, deepcopy(value))
    state.values.clear()
    state.values.update(candidate.values)
    return state


# Descriptive aliases for callers that use the shorter domain vocabulary.
StateTransition = StoryStateTransition
StoryStateDelta = StoryStateTransition
validate_transition = validate_story_state_transition
apply_transitions = apply_story_state_transitions

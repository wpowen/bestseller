from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.infra.db.models import ChapterModel


@dataclass(frozen=True)
class ConvergenceState:
    history: tuple[Mapping[str, Any], ...]
    is_diverging: bool
    is_stuck: bool
    is_oscillating: bool
    recommended_action: str


def assess_convergence(
    chapter: ChapterModel,
    *,
    new_candidate_audit: dict,
    window: int = 5,
) -> ConvergenceState:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    history = [
        item
        for item in metadata.get("rewrite_history", [])
        if isinstance(item, Mapping)
    ]
    if new_candidate_audit:
        history = [*history, dict(new_candidate_audit)]
    history = history[-max(1, int(window)) :]
    code_sets = [frozenset(_codes_from_entry(item)) for item in history]

    is_diverging = (
        len(code_sets) >= 3
        and len(code_sets[-3]) < len(code_sets[-2]) < len(code_sets[-1])
    )
    is_stuck = len(code_sets) >= 3 and code_sets[-1] == code_sets[-2] == code_sets[-3]
    is_oscillating = (
        len(code_sets) >= 4
        and len({code_sets[-1], code_sets[-2], code_sets[-3], code_sets[-4]}) == 2
        and code_sets[-1] == code_sets[-3]
        and code_sets[-2] == code_sets[-4]
    )

    escalation_failures = int(metadata.get("rewrite_escalation_failures") or 0)
    recommended_action = "continue"
    if is_diverging or is_stuck or is_oscillating:
        recommended_action = "stop_to_human" if escalation_failures >= 2 else "escalate"
    return ConvergenceState(
        history=tuple(history),
        is_diverging=is_diverging,
        is_stuck=is_stuck,
        is_oscillating=is_oscillating,
        recommended_action=recommended_action,
    )


def record_rewrite_attempt(
    chapter: ChapterModel,
    *,
    version: int,
    block_codes: Sequence[str],
    word_count: int,
    audit_codes: Sequence[str],
) -> None:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    history = [
        item
        for item in metadata.get("rewrite_history", [])
        if isinstance(item, Mapping)
    ]
    entry = {
        "version": int(version),
        "block_codes": [str(code) for code in block_codes if str(code).strip()],
        "word_count": int(word_count),
        "audit_codes": [str(code) for code in audit_codes if str(code).strip()],
    }
    metadata["rewrite_history"] = [*history, entry][-10:]
    chapter.metadata_json = metadata


def _codes_from_entry(entry: Mapping[str, Any]) -> tuple[str, ...]:
    values = [
        *(entry.get("block_codes") or ()),
        *(entry.get("audit_codes") or ()),
    ]
    return tuple(str(code) for code in values if str(code).strip())


__all__ = ["ConvergenceState", "assess_convergence", "record_rewrite_attempt"]

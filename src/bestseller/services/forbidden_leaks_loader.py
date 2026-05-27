"""Load staged forbidden-leak policy from a project's story bible."""

# ruff: noqa: ANN401

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ForbiddenLeaksDecision:
    forbidden_terms: tuple[str, ...]
    excepted_terms: tuple[str, ...]
    reasoning_notes: tuple[str, ...]


def load_forbidden_leaks_for_chapter(
    project_dir: Path,
    chapter_number: int,
    *,
    context_tag: str | None = None,
) -> ForbiddenLeaksDecision:
    """Return the policy terms that apply to one chapter."""

    policy_path = project_dir / "story-bible" / "forbidden-leaks-policy.yaml"
    if not policy_path.exists():
        return ForbiddenLeaksDecision((), (), ())

    raw_policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_policy, dict):
        return ForbiddenLeaksDecision((), (), ())

    terms = set(_string_list(raw_policy.get("permanent_forbidden")))
    excepted: set[str] = set()
    notes: list[str] = []

    for stage in raw_policy.get("staged_forbidden") or []:
        if not isinstance(stage, dict):
            continue
        lo, hi = _range(stage.get("chapter_range"))
        if lo <= chapter_number <= hi:
            terms.update(_string_list(stage.get("terms")))
            if stage.get("reason"):
                notes.append(str(stage["reason"]))
            if stage.get("exception"):
                notes.append(str(stage["exception"]))

    if context_tag:
        for item in raw_policy.get("contextual_exceptions") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("context") or "") == context_tag:
                excepted.update(_string_list(item.get("allowed_during_staged_block")))

    final_terms = terms - excepted
    return ForbiddenLeaksDecision(
        forbidden_terms=tuple(sorted(final_terms)),
        excepted_terms=tuple(sorted(excepted)),
        reasoning_notes=tuple(dict.fromkeys(notes)),
    )


def _range(value: Any) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return (1, 9999)
    return (1, 9999)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


__all__ = ["ForbiddenLeaksDecision", "load_forbidden_leaks_for_chapter"]

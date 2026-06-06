"""Domain result type for the advisory ideology (母题) judge.

Mirrors :mod:`bestseller.domain.litstyle_judge`: deliberately *advisory-only* —
no ``passed`` field, no thresholds, no synthetic blocking machinery. The ideology
judge never gates an outline; it scores the "思想 / 深度" axis (does the outline
express its core ideology, or only fill genre tropes) so a soft repair loop can
consume it.

Defensive guarantees in :func:`ideology_result_from_mapping`:

* **Per-dimension clamping** to each dimension's configured ``max``.
* **FinalScore is recomputed, never trusted from the model.**
  ``FinalScore = max(0, Σ dimensions − sloganization_penalty)``. The penalty is
  floored by a deterministic grounding prior so the model cannot under-report a
  symptom the deterministic pass already proved.
"""

# ruff: noqa: RUF002

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from bestseller.services.ideology_judge import IdeologyJudgeConfig

_STRONG_SCORE_FLOOR_DEFAULT = 82


class IdeologyJudgeResult(BaseModel):
    """Structured ideology reading for one outline — advisory, never gating."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    dimension_scores: Mapping[str, int] = Field(default_factory=dict)
    sloganization_penalty: int = 0
    base_score: int = 0
    final_score: int = 0
    level: str = "题材堆砌"
    evidence: tuple[str, ...] = ()
    top_issues: tuple[str, ...] = ()
    revision_priority: tuple[str, ...] = ()
    penalty_flagged: tuple[str, ...] = ()
    is_strong: bool = False
    raw_excerpt: str = ""
    llm_run_id: str | None = None
    schema_version: str = "ideology-judge.v1"

    @field_validator(
        "evidence", "top_issues", "revision_priority", "penalty_flagged", mode="before"
    )
    @classmethod
    def _coerce_str_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            cleaned = value.strip()
            return (cleaned,) if cleaned else ()
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()


def _coerce_int(value: object) -> int:
    try:
        return round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _strong_score_floor(config: IdeologyJudgeConfig) -> int:
    for level in config.levels:
        if "灵魂" in level.level or "贯通" in level.level:
            return level.min
    return _STRONG_SCORE_FLOOR_DEFAULT


def ideology_result_from_mapping(
    payload: Mapping[str, Any],
    *,
    config: IdeologyJudgeConfig | None = None,
    penalty_prior: float = 0.0,
    penalty_flagged: Sequence[str] = (),
    llm_run_id: str | None = None,
    raw_excerpt: str = "",
) -> IdeologyJudgeResult:
    """Build a validated :class:`IdeologyJudgeResult` from a judge's raw JSON.

    ``penalty_prior`` is the deterministic grounding pass's penalty (a floor on the
    model's self-reported ``sloganization_penalty``). FinalScore + level + strong
    flag are recomputed here, not taken from the model.
    """

    from bestseller.services.ideology_judge import (
        ideology_level_for_score,
        load_ideology_judge_config,
    )

    config = config or load_ideology_judge_config()
    data = dict(payload)

    dimension_scores: dict[str, int] = {}
    for dim in config.dimensions:
        raw = data.get(dim.key, 0)
        dimension_scores[dim.key] = _clamp(_coerce_int(raw), 0, dim.max)

    base = sum(dimension_scores.values())

    # Honesty guard: an empty / garbled response parses to {} → self-label rather
    # than masquerade as a genuine all-zero.
    scored = any(dim.key in data for dim in config.dimensions)
    top_issues_value: object = data.get("top_issues", ())
    if not scored:
        existing = data.get("top_issues")
        existing_list = (
            [existing] if isinstance(existing, str) and existing.strip() else list(existing or [])
        )
        top_issues_value = ["IDEOLOGY_JUDGE_UNAVAILABLE", *existing_list]

    model_penalty = _clamp(
        _coerce_int(data.get("sloganization_penalty", data.get("penalty", 0))),
        0,
        config.penalty_max,
    )
    penalty = _clamp(max(model_penalty, _coerce_int(penalty_prior)), 0, config.penalty_max)

    final = _clamp(base - penalty, 0, 100)
    level = ideology_level_for_score(final, config)
    is_strong = final >= _strong_score_floor(config) and penalty <= config.penalty_strong_ceiling

    return IdeologyJudgeResult.model_validate(
        {
            "dimension_scores": dimension_scores,
            "sloganization_penalty": penalty,
            "base_score": base,
            "final_score": final,
            "level": level,
            "evidence": data.get("evidence", ()),
            "top_issues": top_issues_value,
            "revision_priority": data.get("revision_priority", ()),
            "penalty_flagged": tuple(penalty_flagged),
            "is_strong": is_strong,
            "llm_run_id": llm_run_id,
            "raw_excerpt": raw_excerpt,
        }
    )


__all__ = ["IdeologyJudgeResult", "ideology_result_from_mapping"]

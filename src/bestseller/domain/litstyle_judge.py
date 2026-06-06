"""Domain result type for the advisory LitStyle-100R 文采 judge.

Mirrors :mod:`bestseller.domain.llm_quality_judge` in spirit but is deliberately
*advisory-only*: there is **no** ``passed`` field, no thresholds, and no synthetic
blocking-issue machinery. The文采 judge never gates a chapter — it only scores the
"打动读者" axis (literary craft) so a soft polish self-loop can consume it.

Two defensive guarantees implemented in :func:`litstyle_result_from_mapping`:

* **Per-dimension clamping** to each dimension's configured ``max`` — a model that
  returns ``concrete: 19`` (over the 14 cap) cannot inflate the base score.
* **FinalScore is recomputed, never trusted from the model.**
  ``FinalScore = max(0, Σ dimensions − ai_tone_penalty)``. The AI腔 penalty is
  floored by the deterministic detector prior, so the model cannot under-report a
  symptom the deterministic pass already proved.
"""

# ruff: noqa: RUF002, E501

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from bestseller.services.litstyle_prose import LitStyleConfig

_MATURE_SCORE_FLOOR_DEFAULT = 80


class LitStyleJudgeResult(BaseModel):
    """Structured文采 reading for one chapter — advisory, never gating."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    dimension_scores: Mapping[str, int] = Field(default_factory=dict)
    ai_tone_penalty: int = 0
    base_score: int = 0
    final_score: int = 0
    level: str = "较弱"
    evidence: tuple[str, ...] = ()
    top_issues: tuple[str, ...] = ()
    revision_priority: tuple[str, ...] = ()
    ai_tone_flagged: tuple[str, ...] = ()
    is_mature: bool = False
    is_high_risk_template: bool = False
    raw_excerpt: str = ""
    llm_run_id: str | None = None
    schema_version: str = "litstyle-prose.v1"

    @field_validator("evidence", "top_issues", "revision_priority", "ai_tone_flagged", mode="before")
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


def _mature_score_floor(config: LitStyleConfig) -> int:
    """The FinalScore at which a text is 'mature' — the 成熟 level's ``min``."""

    for level in config.levels:
        if "成熟" in level.level:
            return level.min
    return _MATURE_SCORE_FLOOR_DEFAULT


def litstyle_result_from_mapping(
    payload: Mapping[str, Any],
    *,
    config: LitStyleConfig | None = None,
    ai_tone_prior: float = 0.0,
    ai_tone_flagged: Sequence[str] = (),
    llm_run_id: str | None = None,
    raw_excerpt: str = "",
) -> LitStyleJudgeResult:
    """Build a validated :class:`LitStyleJudgeResult` from a judge's raw JSON.

    ``ai_tone_prior`` is the deterministic detector's penalty (a floor on the
    model's self-reported ``ai_tone_penalty``). FinalScore + level + maturity
    flags are recomputed here, not taken from the model.
    """

    from bestseller.services.litstyle_prose import (
        litstyle_level_for_score,
        load_litstyle_config,
    )

    config = config or load_litstyle_config()
    data = dict(payload)

    # Per-dimension clamp to each dimension's configured max.
    dimension_scores: dict[str, int] = {}
    for dim in config.dimensions:
        raw = data.get(dim.key, 0)
        dimension_scores[dim.key] = _clamp(_coerce_int(raw), 0, dim.max)

    base = sum(dimension_scores.values())

    # Honesty guard: a transient empty / garbled judge response parses to ``{}``,
    # which would otherwise masquerade as a genuine all-zero (FinalScore 0). Real
    # prose always scores >0 on some dimension, so "no dimension key present at all"
    # means the judge was unavailable — self-label it rather than record a fake 0.
    scored = any(dim.key in data for dim in config.dimensions)
    top_issues_value: object = data.get("top_issues", ())
    if not scored:
        existing = data.get("top_issues")
        existing_list = (
            [existing]
            if isinstance(existing, str) and existing.strip()
            else list(existing or [])
        )
        top_issues_value = ["LITSTYLE_JUDGE_UNAVAILABLE", *existing_list]

    model_penalty = _clamp(
        _coerce_int(data.get("ai_tone_penalty", 0)), 0, config.ai_tone_penalty_max
    )
    # The deterministic prior is a *floor*: a symptom proven by the deterministic
    # pass cannot be under-reported by the model.
    penalty = _clamp(
        max(model_penalty, _coerce_int(ai_tone_prior)), 0, config.ai_tone_penalty_max
    )

    final = _clamp(base - penalty, 0, 100)
    level = litstyle_level_for_score(final, config)
    is_mature = final >= _mature_score_floor(config) and penalty <= config.ai_tone_mature_ceiling
    is_high_risk = penalty >= config.ai_tone_high_risk_threshold

    return LitStyleJudgeResult.model_validate(
        {
            "dimension_scores": dimension_scores,
            "ai_tone_penalty": penalty,
            "base_score": base,
            "final_score": final,
            "level": level,
            "evidence": data.get("evidence", ()),
            "top_issues": top_issues_value,
            "revision_priority": data.get("revision_priority", ()),
            "ai_tone_flagged": tuple(ai_tone_flagged),
            "is_mature": is_mature,
            "is_high_risk_template": is_high_risk,
            "llm_run_id": llm_run_id,
            "raw_excerpt": raw_excerpt,
        }
    )


__all__ = ["LitStyleJudgeResult", "litstyle_result_from_mapping"]

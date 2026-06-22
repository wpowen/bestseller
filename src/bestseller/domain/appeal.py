"""Domain models for the story / blurb appeal evaluation system.

These carry the verdicts produced by:
  * :mod:`bestseller.services.blurb_appeal_gate`   (deterministic 10-dim)
  * :mod:`bestseller.services.premise_appeal_judge` (LLM 9-dim, genre-aware)

and combined by :mod:`bestseller.services.story_appeal` into a
:class:`StoryAppealReport`.  All models are frozen — evaluation never mutates
its inputs (immutability principle).  ``to_dict`` produces a JSON-safe shape
that the web layer persists as an inspectable book artifact.
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — Chinese in docstrings/comments is intentional.
from dataclasses import dataclass, field
from typing import Any

# Grade ladder, worst → best. ``meets_bar`` and gating compare by ordinal.
GRADE_ORDER: tuple[str, ...] = ("pass", "consider", "recommend")


def grade_rank(grade: str) -> int:
    """Ordinal of a grade (higher = better). Unknown grades rank lowest."""

    try:
        return GRADE_ORDER.index(str(grade))
    except ValueError:
        return -1


def min_grade(a: str, b: str) -> str:
    """Return the weaker of two grades (used to apply a gating cap)."""

    return a if grade_rank(a) <= grade_rank(b) else b


@dataclass(frozen=True)
class AppealDimension:
    """One scored rubric dimension (0–5)."""

    key: str
    label: str
    score: float          # 0–5
    weight: float         # rubric weight (points out of 100)
    rationale: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(float(self.score), 2),
            "weight": float(self.weight),
            "rationale": self.rationale,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class BlurbAppealVerdict:
    """Deterministic click-power verdict over title + synopsis + tags."""

    total: float                       # 0–100
    grade: str                         # pass | consider | recommend
    dimensions: tuple[AppealDimension, ...] = ()
    findings: tuple[str, ...] = ()     # human-readable weak points
    suggestions: tuple[str, ...] = ()  # concrete fixes
    language: str = "zh"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(float(self.total), 1),
            "grade": self.grade,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "findings": list(self.findings),
            "suggestions": list(self.suggestions),
            "language": self.language,
        }


@dataclass(frozen=True)
class TitleAppealVerdict:
    """Deterministic click-power verdict over the *title* alone (zero-token).

    The blurb gate scores the synopsis; this scores whether the *book name*
    itself is logical and click-worthy (a reader scanning a ranking list sees
    the title first). Separate min so a weak title fails the bar on its own.
    """

    total: float                       # 0–100
    grade: str                         # pass | consider | recommend
    dimensions: tuple[AppealDimension, ...] = ()
    findings: tuple[str, ...] = ()     # human-readable weak points
    suggestions: tuple[str, ...] = ()  # concrete fixes
    language: str = "zh"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(float(self.total), 1),
            "grade": self.grade,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "findings": list(self.findings),
            "suggestions": list(self.suggestions),
            "language": self.language,
        }


@dataclass(frozen=True)
class PremiseAppealVerdict:
    """Story-idea attractiveness verdict (LLM judge + deterministic features)."""

    total: float                          # 0–100 (weighted, pre-gating)
    grade: str                            # raw grade from total
    gated_grade: str                      # grade after one-vote-veto gating
    dimensions: tuple[AppealDimension, ...] = ()
    triggers_fired: tuple[str, ...] = ()  # psychological triggers detected (T1..T11)
    findings: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    gating_caps: tuple[str, ...] = ()     # which dims forced a cap
    llm_used: bool = False
    llm_run_id: str | None = None
    schema_version: str = "premise-appeal.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(float(self.total), 1),
            "grade": self.grade,
            "gated_grade": self.gated_grade,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "triggers_fired": list(self.triggers_fired),
            "findings": list(self.findings),
            "suggestions": list(self.suggestions),
            "gating_caps": list(self.gating_caps),
            "llm_used": self.llm_used,
            "llm_run_id": self.llm_run_id,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class StoryAppealReport:
    """Combined report — the thing persisted and used to gate regeneration."""

    genre: str
    sub_genre: str
    premise: PremiseAppealVerdict
    blurb: BlurbAppealVerdict
    meets_bar: bool
    overall_grade: str
    canonical_genre: str = ""
    title: TitleAppealVerdict | None = None  # None when title gate disabled (no-op)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "genre": self.genre,
            "sub_genre": self.sub_genre,
            "canonical_genre": self.canonical_genre,
            "meets_bar": self.meets_bar,
            "overall_grade": self.overall_grade,
            "premise": self.premise.to_dict(),
            "blurb": self.blurb.to_dict(),
            "schema_version": "story-appeal-report.v2",
        }
        if self.title is not None:
            out["title"] = self.title.to_dict()
        return out


__all__ = [
    "GRADE_ORDER",
    "AppealDimension",
    "BlurbAppealVerdict",
    "PremiseAppealVerdict",
    "StoryAppealReport",
    "TitleAppealVerdict",
    "grade_rank",
    "min_grade",
]

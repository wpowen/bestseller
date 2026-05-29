"""Baseline evaluation specs for book-methodology integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

BaselineMetricKind = Literal["count", "ratio", "score"]


@dataclass(frozen=True)
class BaselineMetricSpec:
    """One observable metric used to prove book-methodology impact."""

    metric_id: str
    label: str
    kind: BaselineMetricKind
    description: str
    higher_is_better: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "label": self.label,
            "kind": self.kind,
            "description": self.description,
            "higher_is_better": self.higher_is_better,
        }


@dataclass(frozen=True)
class BaselineCaseSpec:
    """A fixed project/chapter sample for before-after comparisons."""

    case_id: str
    project_slug: str
    genre: str
    chapter_numbers: tuple[int, ...]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project_slug": self.project_slug,
            "genre": self.genre,
            "chapter_numbers": list(self.chapter_numbers),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class BaselineSuiteSpec:
    """Baseline suite definition that must exist before gate promotion."""

    suite_id: str = "book_methodology_baseline_v1"
    minimum_projects: int = 3
    minimum_chapters_per_project: int = 20
    metrics: tuple[BaselineMetricSpec, ...] = field(default_factory=tuple)
    cases: tuple[BaselineCaseSpec, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "minimum_projects": self.minimum_projects,
            "minimum_chapters_per_project": self.minimum_chapters_per_project,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "cases": [case.to_dict() for case in self.cases],
            "promotion_rule": (
                "Book-methodology gates may not promote above warn unless a baseline run "
                "shows non-negative quality movement and acceptable cost/latency impact."
            ),
        }


DEFAULT_BASELINE_METRICS: tuple[BaselineMetricSpec, ...] = (
    BaselineMetricSpec(
        metric_id="scene_causality_completeness",
        label="场景因果完整度",
        kind="score",
        description="Scenes expose goal, obstacle, action, cost/result, and next pressure.",
    ),
    BaselineMetricSpec(
        metric_id="setup_payoff_closed_count",
        label="setup/payoff 闭环数",
        kind="count",
        description="Count of planted hooks or objects that receive visible payoff.",
    ),
    BaselineMetricSpec(
        metric_id="pov_distance_drift_ratio",
        label="POV 距离漂移率",
        kind="ratio",
        description="Share of sampled sentences that violate the intended POV distance.",
        higher_is_better=False,
    ),
    BaselineMetricSpec(
        metric_id="dialogue_subtext_ratio",
        label="对白潜台词比例",
        kind="ratio",
        description="Share of dialogue turns with action pressure or hidden intent.",
    ),
    BaselineMetricSpec(
        metric_id="character_want_need_coverage",
        label="角色 want-vs-need 承载率",
        kind="ratio",
        description="Share of chapter/scene contracts carrying visible want/need tension.",
    ),
    BaselineMetricSpec(
        metric_id="repair_trigger_rate",
        label="repair 触发率",
        kind="ratio",
        description="Share of sampled chapters that require autonomous repair.",
        higher_is_better=False,
    ),
    BaselineMetricSpec(
        metric_id="prompt_token_delta",
        label="prompt token 增量",
        kind="count",
        description="Added prompt tokens caused by book-methodology injection.",
        higher_is_better=False,
    ),
)


def default_baseline_suite() -> BaselineSuiteSpec:
    """Return the default baseline suite without binding to real project slugs."""

    return BaselineSuiteSpec(metrics=DEFAULT_BASELINE_METRICS)


def write_default_baseline_metric_spec(path: Path) -> Path:
    """Write the default baseline metric spec YAML."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            default_baseline_suite().to_dict(),
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    return path


__all__ = [
    "DEFAULT_BASELINE_METRICS",
    "BaselineCaseSpec",
    "BaselineMetricSpec",
    "BaselineSuiteSpec",
    "default_baseline_suite",
    "write_default_baseline_metric_spec",
]

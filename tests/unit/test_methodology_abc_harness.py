from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit


def _load_pilot_module() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "methodology_books" / "run_short_story_pilot.py"
    spec = importlib.util.spec_from_file_location("run_short_story_pilot", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_abc_harness_summary_tracks_variance_order_and_setup_closure() -> None:
    pilot = _load_pilot_module()
    samples = []
    values_by_group = {
        "A": (0.30, 0.90),
        "B": (0.55, 0.75),
        "C": (0.72, 0.76),
    }
    variants = {"A": "baseline", "B": "lineage-only", "C": "lineage-reinforce"}
    for group, values in values_by_group.items():
        for index, value in enumerate(values, start=1):
            samples.append(
                pilot.ABCChapterSample(
                    group=group,
                    variant=variants[group],
                    genre="都市悬疑+轻玄幻",
                    sample_index=1,
                    chapter_number=index,
                    selected_card_count=0,
                    fallback_used=True,
                    metrics={
                        "overall_score": value,
                        "scene_causality_score": value,
                        "setup_payoff_score": value,
                        "pov_stability_score": value,
                        "ending_hook_score": value,
                        "methodology_trace_score": value,
                        "regression_tradeoff_count": 0.0,
                    },
                    output_path="",
                )
            )

    report = pilot.summarize_abc_harness(
        samples,
        genres=("都市悬疑+轻玄幻",),
        samples_per_genre=1,
        chapters_per_sample=2,
    )

    assert report.total_chapters == 6
    assert report.primary_pass is True
    assert report.variance_order["overall_score"] is True
    assert report.summaries[2].setup_payoff_closure_rate > 0.70


def test_abc_harness_summary_fails_when_c_variance_regresses() -> None:
    pilot = _load_pilot_module()
    samples = []
    values_by_group = {
        "A": (0.60, 0.70),
        "B": (0.61, 0.69),
        "C": (0.35, 0.95),
    }
    variants = {"A": "baseline", "B": "lineage-only", "C": "lineage-reinforce"}
    for group, values in values_by_group.items():
        for index, value in enumerate(values, start=1):
            samples.append(
                pilot.ABCChapterSample(
                    group=group,
                    variant=variants[group],
                    genre="东方玄幻+权谋",
                    sample_index=1,
                    chapter_number=index,
                    selected_card_count=0,
                    fallback_used=True,
                    metrics={
                        "overall_score": value,
                        "scene_causality_score": value,
                        "setup_payoff_score": value,
                        "pov_stability_score": value,
                        "ending_hook_score": value,
                        "methodology_trace_score": value,
                        "regression_tradeoff_count": 0.0,
                    },
                    output_path="",
                )
            )

    report = pilot.summarize_abc_harness(samples)

    assert report.primary_pass is False
    assert report.variance_order["overall_score"] is False

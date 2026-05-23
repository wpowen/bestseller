from __future__ import annotations

from bestseller.domain.outline_density_budget import OutlineDensityBudget
from bestseller.services.outline_density_gate import evaluate_outline_density


def test_outline_density_gate_passes_under_budget() -> None:
    verdict = evaluate_outline_density(
        {
            "chapter_no": 64,
            "new_reveals": ["镜债新线索"],
            "new_terms": ["回执镜片"],
            "new_named_characters": ["张建军"],
        }
    )

    assert verdict.passed is True
    assert verdict.metrics["split_chapter_recommended"] is False


def test_outline_density_gate_rejects_overloaded_chapter_with_split_advice() -> None:
    verdict = evaluate_outline_density(
        {
            "chapter_no": 64,
            "new_reveals": ["揭秘1", "揭秘2", "揭秘3", "揭秘4"],
            "new_terms": ["术语1", "术语2"],
            "new_named_characters": ["甲", "乙"],
        },
        budget=OutlineDensityBudget(max_new_reveals=2, max_new_terms=1),
    )

    assert verdict.verdict == "blocked"
    assert verdict.passed is False
    assert verdict.metrics["split_chapter_recommended"] is True
    assert {
        "outline_new_reveals_over_budget",
        "outline_new_terms_over_budget",
        "outline_total_density_over_budget",
    }.issubset({finding.code for finding in verdict.findings})

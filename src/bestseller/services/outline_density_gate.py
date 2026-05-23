from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.domain.outline_density_budget import (
    OutlineDensityBudget,
    OutlineDensityInput,
)


def evaluate_outline_density(
    outline: OutlineDensityInput | Mapping[str, Any],
    *,
    budget: OutlineDensityBudget | None = None,
) -> GateVerdict:
    density_input = (
        outline
        if isinstance(outline, OutlineDensityInput)
        else OutlineDensityInput.model_validate(outline)
    )
    effective_budget = budget or OutlineDensityBudget()
    findings: list[GateFinding] = []
    _append_over_limit(
        findings,
        code="outline_new_reveals_over_budget",
        label="new reveals",
        count=len(density_input.new_reveals),
        limit=effective_budget.max_new_reveals,
    )
    _append_over_limit(
        findings,
        code="outline_new_terms_over_budget",
        label="new terms",
        count=len(density_input.new_terms),
        limit=effective_budget.max_new_terms,
    )
    _append_over_limit(
        findings,
        code="outline_new_named_characters_over_budget",
        label="new named characters",
        count=len(density_input.new_named_characters),
        limit=effective_budget.max_new_named_characters,
    )
    _append_over_limit(
        findings,
        code="outline_total_density_over_budget",
        label="total density units",
        count=density_input.density_units,
        limit=effective_budget.max_total_density_units,
    )
    return GateVerdict(
        gate_name="outline_density_gate",
        verdict="blocked" if findings else "pass",
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={
            "chapter_no": density_input.chapter_no,
            "new_reveals": len(density_input.new_reveals),
            "new_terms": len(density_input.new_terms),
            "new_named_characters": len(density_input.new_named_characters),
            "density_units": density_input.density_units,
            "split_chapter_recommended": bool(findings),
        },
    )


def _append_over_limit(
    findings: list[GateFinding],
    *,
    code: str,
    label: str,
    count: int,
    limit: int,
) -> None:
    if count <= limit:
        return
    findings.append(
        GateFinding(
            code=code,
            severity="critical",
            message=f"{label} count {count} exceeds budget {limit}",
            repair_action="split chapter or defer lower-priority reveals",
        )
    )

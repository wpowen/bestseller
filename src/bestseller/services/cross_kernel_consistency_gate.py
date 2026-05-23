from __future__ import annotations

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.kernel_composer import NarrativeRichnessKernels


def evaluate_cross_kernel_consistency(
    kernels: NarrativeRichnessKernels | dict,
    *,
    total_chapters: int | None = None,
) -> GateVerdict:
    context = NarrativeRichnessKernels.model_validate(kernels)
    findings: list[GateFinding] = []
    if context.geography_kernel is not None:
        regions = context.geography_kernel.region_names()
        if context.geography_kernel.protagonist_current not in regions:
            findings.append(
                GateFinding(
                    code="geography_current_region_unknown",
                    severity="critical",
                    message="protagonist_current references an unknown region",
                    repair_action="align protagonist_current with geography regions",
                )
            )
    if context.mystery_anchor_kernel is not None and total_chapters is not None:
        for anchor in context.mystery_anchor_kernel.anchors:
            if anchor.final_payoff_chapter_range[1] > total_chapters:
                findings.append(
                    GateFinding(
                        code="mystery_payoff_beyond_book_scope",
                        severity="critical",
                        message=f"mystery payoff exceeds total chapters: {anchor.question}",
                        repair_action="move payoff window inside planned chapter range",
                    )
                )
    return GateVerdict(
        gate_name="cross_kernel_consistency",
        verdict="blocked" if findings else "pass",
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={"total_chapters": total_chapters},
    )

from __future__ import annotations

from bestseller.domain.ensemble_arc import EnsembleArcKernel


def render_ensemble_arc_prompt_block(kernel: EnsembleArcKernel | dict | None) -> str:
    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = EnsembleArcKernel.model_validate(kernel)
    if not kernel.arcs:
        return ""
    lines = ["### Ensemble Arc Kernel"]
    for arc in kernel.arcs[:5]:
        lines.append(
            "- "
            f"{arc.owner_id}: {arc.arc_kind}; goal={arc.private_goal}; "
            f"payoff={arc.private_payoff}; final={arc.final_state}"
        )
    return "\n".join(lines)


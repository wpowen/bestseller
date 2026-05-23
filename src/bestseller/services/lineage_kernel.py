from __future__ import annotations

from bestseller.domain.lineage_system import LineageKernel


def render_lineage_prompt_block(kernel: LineageKernel | dict | None) -> str:
    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = LineageKernel.model_validate(kernel)
    lines = ["### Lineage Kernel"]
    for school, nodes in list(kernel.schools.items())[:4]:
        generations = sorted({node.generation for node in nodes})
        roles = sorted({node.role for node in nodes})
        lines.append(
            f"- {school}: generations={generations}; roles={', '.join(roles)}"
        )
        rules = kernel.school_rules.get(school) or []
        if rules:
            lines.append(f"  - Rules: {', '.join(rules[:3])}")
    if kernel.inter_school_treaties:
        lines.append(f"- Treaties/tensions: {', '.join(kernel.inter_school_treaties[:4])}")
    return "\n".join(lines)


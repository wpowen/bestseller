from __future__ import annotations

from bestseller.domain.meta_layer import MetaLayerContract


def render_meta_layer_prompt_block(contract: MetaLayerContract | dict | None) -> str:
    if contract is None:
        return ""
    if isinstance(contract, dict):
        contract = MetaLayerContract.model_validate(contract)
    lines = [
        "### Meta Layer Contract",
        f"- Type/place: {contract.layer_type} / {contract.placement}",
        f"- Function: {contract.narrative_function}",
        f"- Voice rule: {contract.voice_rule}",
        f"- Spoiler boundary: {contract.spoiler_boundary}",
    ]
    if contract.payoff_targets:
        lines.append(f"- Payoff targets: {', '.join(contract.payoff_targets[:5])}")
    return "\n".join(lines)


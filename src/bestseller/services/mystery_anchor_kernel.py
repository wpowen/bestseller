from __future__ import annotations

from bestseller.domain.mystery_anchor import MysteryAnchorKernel


def render_mystery_anchor_prompt_block(kernel: MysteryAnchorKernel | dict | None) -> str:
    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = MysteryAnchorKernel.model_validate(kernel)
    lines = ["### Mystery Anchor Kernel"]
    for anchor in kernel.anchors[:5]:
        lines.append(
            f"- Question: {anchor.question}; stake={anchor.stake_if_solved}; "
            f"payoff_chapters={anchor.final_payoff_chapter_range}"
        )
    return "\n".join(lines)


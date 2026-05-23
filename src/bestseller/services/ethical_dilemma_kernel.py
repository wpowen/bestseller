from __future__ import annotations

from bestseller.domain.ethical_dilemma import EthicalDilemmaKernel


def render_ethical_dilemma_prompt_block(
    kernel: EthicalDilemmaKernel | dict | None,
    *,
    chapter_no: int | None = None,
) -> str:
    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = EthicalDilemmaKernel.model_validate(kernel)
    slots = kernel.slots
    if chapter_no is not None:
        slots = [
            slot
            for slot in slots
            if slot.chapter_window[0] <= chapter_no <= slot.chapter_window[1]
        ]
    if not slots:
        return ""
    lines = ["### Ethical Dilemma Kernel"]
    for slot in slots[:3]:
        lines.append(
            "- 伦理两难: "
            f"{slot.dilemma_kind}; values={slot.competing_values[0]} vs "
            f"{slot.competing_values[1]}; intended={slot.intended_choice}; "
            f"unchosen_cost={slot.consequence_for_unchosen}"
        )
    return "\n".join(lines)


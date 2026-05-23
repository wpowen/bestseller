from __future__ import annotations

from bestseller.domain.chapter_seam_contract import (
    ChapterSeamContract,
    seam_contract_from_mapping,
)


def render_seam_prompt_block(contract: object) -> str:
    seam = seam_contract_from_mapping(contract)
    if seam is None:
        return ""
    lines = [
        "## Chapter Seam Contract",
        f"- Chapter: {seam.chapter_no}",
    ]
    if seam.inherits_from_prev:
        lines.append("- Must inherit: " + "; ".join(seam.inherits_from_prev))
    if seam.required_callbacks:
        lines.append("- Required callbacks: " + "; ".join(seam.required_callbacks))
    if seam.opening_state:
        lines.append(f"- Opening state: {seam.opening_state}")
    if seam.carry_forward_state:
        lines.append(
            "- Carry-forward state: "
            + "; ".join(f"{key}={value}" for key, value in seam.carry_forward_state.items())
        )
    if seam.forbidden_resets:
        lines.append("- Forbidden resets: " + "; ".join(seam.forbidden_resets))
    return "\n".join(lines)


def require_chapter_seam_contract(payload: object) -> ChapterSeamContract:
    seam = seam_contract_from_mapping(payload)
    if seam is None:
        raise ValueError("seam_contract is required")
    return seam

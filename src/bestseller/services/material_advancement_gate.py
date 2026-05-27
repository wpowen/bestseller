"""Post-generation gate for chapter material obligations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


@dataclass(frozen=True)
class MaterialObligation:
    kind: str
    identifier: str
    required_tokens: tuple[str, ...]
    description: str = ""


def evaluate_material_advancement(
    chapter_text: str,
    obligations: Sequence[MaterialObligation],
) -> GateVerdict:
    """Verify that required reveals, rules, and evidence appear in chapter prose."""

    findings: list[GateFinding] = []
    for obligation in obligations:
        if not obligation.required_tokens:
            continue
        if any(token and token in chapter_text for token in obligation.required_tokens):
            continue
        findings.append(
            GateFinding(
                code=f"MATERIAL_{obligation.kind.upper()}_NOT_ADVANCED",
                severity="high",
                message=(
                    f"Chapter did not visibly advance {obligation.kind} "
                    f"{obligation.identifier}; expected one of "
                    f"{', '.join(obligation.required_tokens)}."
                ),
                path=obligation.identifier,
                repair_action=(
                    "Rewrite the chapter to land the required material contract "
                    "in visible prose."
                ),
            )
        )
    return GateVerdict(
        gate_name="material_advancement",
        verdict="pass" if not findings else "blocked",
        coverage=1.0 if not obligations else (len(obligations) - len(findings)) / len(obligations),
        findings=tuple(findings),
        metrics={"obligation_count": len(obligations), "missing_count": len(findings)},
    )


__all__ = ["MaterialObligation", "evaluate_material_advancement"]

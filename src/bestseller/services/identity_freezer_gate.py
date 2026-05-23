from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def evaluate_identity_freezer(
    identity_registry: Sequence[Mapping[str, Any]],
    *,
    min_coverage: float = 0.95,
) -> GateVerdict:
    named = [
        item
        for item in identity_registry
        if str(item.get("name") or item.get("display_name") or "").strip()
    ]
    frozen = [
        item
        for item in named
        if bool(item.get("frozen") or item.get("identity_frozen") or item.get("locked"))
    ]
    findings = [
        GateFinding(
            code="identity_not_frozen",
            severity="critical",
            message=f"identity not frozen: {item.get('name') or item.get('display_name')}",
            path=f"identity:{item.get('id') or item.get('name') or item.get('display_name')}",
            repair_action="freeze canonical name, role, aliases, and voice profile",
        )
        for item in named
        if item not in frozen
    ]
    coverage = len(frozen) / len(named) if named else 1.0
    if coverage < min_coverage and not findings:
        findings.append(
            GateFinding(
                code="identity_registry_coverage_low",
                severity="critical",
                message=f"identity coverage {coverage:.2f} below {min_coverage:.2f}",
                repair_action="freeze remaining named identities",
            )
        )
    return GateVerdict(
        gate_name="identity_freezer_gate",
        verdict="blocked" if findings else "pass",
        coverage=coverage,
        findings=tuple(findings),
        metrics={"identity_registry_coverage": coverage, "named_character_count": len(named)},
    )

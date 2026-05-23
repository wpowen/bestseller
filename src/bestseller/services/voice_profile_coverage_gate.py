from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def evaluate_voice_profile_coverage(
    characters: Sequence[Mapping[str, Any]],
    *,
    min_coverage: float = 0.95,
    required_roles: set[str] | frozenset[str] | tuple[str, ...] | None = None,
) -> GateVerdict:
    role_filter = {role.strip().lower() for role in required_roles or () if role.strip()}
    named = [
        item
        for item in characters
        if str(item.get("name") or item.get("display_name") or "").strip()
        and (not role_filter or str(item.get("role") or "").strip().lower() in role_filter)
    ]
    covered = [item for item in named if _has_voice_profile(item)]
    missing = [item for item in named if item not in covered]
    coverage = len(covered) / len(named) if named else 1.0
    findings = [
        GateFinding(
            code="voice_profile_missing",
            severity="critical",
            message=f"voice profile missing: {item.get('name') or item.get('display_name')}",
            path=f"character:{item.get('id') or item.get('name') or item.get('display_name')}",
            repair_action="add voice_profile or voice_dna for this named character",
        )
        for item in missing
    ]
    return GateVerdict(
        gate_name="voice_profile_coverage",
        verdict="blocked" if coverage < min_coverage else "pass",
        coverage=coverage,
        findings=tuple(findings),
        metrics={"identity_registry_coverage": coverage, "named_character_count": len(named)},
    )


def _has_voice_profile(item: Mapping[str, Any]) -> bool:
    metadata = item.get("metadata_json")
    return bool(
        item.get("voice_profile")
        or item.get("voice_profile_json")
        or item.get("voice_dna")
        or (isinstance(metadata, Mapping) and metadata.get("voice_dna"))
    )

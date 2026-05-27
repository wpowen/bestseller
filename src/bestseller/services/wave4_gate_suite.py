from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.gate_verdict import GateVerdict
from bestseller.services.gate_verdict_migration import normalize_gate_payload

WAVE4_GATE_NAMES: tuple[str, ...] = (
    "geography_continuity_gate",
    "cultural_texture_density_gate",
    "ensemble_arc_progress_gate",
    "mystery_anchor_reveal_gate",
    "ethical_dilemma_slot_gate",
    "lineage_address_gate",
    "outline_specificity_gate",
    "volume_plan_resolution_gate",
    "forward_state_contract_gate",
    "outline_reveal_alignment_gate",
)


def normalize_wave4_gate_suite(
    payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[GateVerdict, ...]:
    return tuple(
        normalize_gate_payload(gate_name, payloads.get(gate_name, {}))
        for gate_name in WAVE4_GATE_NAMES
    )

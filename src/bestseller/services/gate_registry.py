from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

RepairStrategy = Literal["auto", "rewrite_task", "human_review"]


@dataclass(frozen=True)
class GateRegistration:
    name: str
    metadata_keys: tuple[str, ...]
    repair_strategy: RepairStrategy
    terminal_project_keys: tuple[str, ...] = ()


_GATES: tuple[GateRegistration, ...] = (
    GateRegistration(
        name="write_safety_gate",
        metadata_keys=("blocked_by_write_safety_gate", "write_safety_block_code"),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="l2_bible_gate",
        metadata_keys=("blocked_by_l2_bible_gate",),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="phase_d_time_gate",
        metadata_keys=("blocked_by_phase_d_time_gate",),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="fanqie_long_ranking_gate",
        metadata_keys=("blocked_by_fanqie_long_ranking_gate",),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="ai_flavor_gate",
        metadata_keys=("blocked_by_ai_flavor_gate",),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="anti_meta_gate",
        metadata_keys=("blocked_by_anti_meta_gate",),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="show_dont_tell_gate",
        metadata_keys=("blocked_by_show_dont_tell_gate",),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="chapter_splice_coherence_gate",
        metadata_keys=(
            "blocked_by_chapter_splice_coherence_gate",
            "chapter_splice_coherence_block_codes",
        ),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="material_referential_integrity_gate",
        metadata_keys=(
            "blocked_by_material_referential_integrity_gate",
            "material_referential_integrity_block_codes",
        ),
        repair_strategy="auto",
    ),
    GateRegistration(
        name="material_advancement_gate",
        metadata_keys=("blocked_by_material_advancement_gate", "material_advancement_block_codes"),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="signature_audit_gate",
        metadata_keys=("blocked_by_signature_audit_gate", "signature_audit_block_codes"),
        repair_strategy="rewrite_task",
    ),
    GateRegistration(
        name="chapter_outline_readiness_gate",
        metadata_keys=(
            "blocked_by_chapter_outline_readiness_gate",
            "chapter_outline_readiness_block_codes",
            "chapter_outline_readiness_hint",
            "chapter_outline_readiness_report",
        ),
        repair_strategy="auto",
    ),
    GateRegistration(
        name="chapter_predraft_quality_gate",
        metadata_keys=("blocked_by_chapter_predraft_quality_gate",),
        repair_strategy="human_review",
    ),
    GateRegistration(
        name="qimao_opening_gate",
        metadata_keys=("qimao_opening_gate_blocked", "opening_quality_gate_blocked"),
        repair_strategy="rewrite_task",
        terminal_project_keys=("qimao_opening_gate_exhausted",),
    ),
)

_REGISTERED_GATE_NAMES = frozenset(gate.name for gate in _GATES)
_BLOCK_METADATA_KEYS = tuple(
    dict.fromkeys(key for gate in _GATES for key in gate.metadata_keys)
)
_TERMINAL_PROJECT_KEYS = tuple(
    dict.fromkeys(key for gate in _GATES for key in gate.terminal_project_keys)
)


def registered_gate_names() -> frozenset[str]:
    return _REGISTERED_GATE_NAMES


def registered_block_metadata_keys() -> tuple[str, ...]:
    return _BLOCK_METADATA_KEYS


def project_resume_is_terminally_blocked(metadata: Mapping[str, object] | None) -> bool:
    data = metadata if isinstance(metadata, Mapping) else {}
    return any(bool(data.get(key)) for key in _TERMINAL_PROJECT_KEYS)

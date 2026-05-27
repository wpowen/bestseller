"""Quality contract registry for chapter generation gates.

The registry is the canonical list of quality block codes that the writing
pipeline understands. A code in this registry has a repair scope and a pass
condition; a code missing from the registry is unsafe for autonomous commercial
shipping because the repair loop cannot prove closure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

QUALITY_CONTRACT_VERSION = "quality-contract-v1"

Severity = Literal["info", "medium", "high", "critical", "block"]
RepairScope = Literal[
    "planning",
    "chapter",
    "scene",
    "paragraph",
    "ending",
    "metadata",
    "package",
]


@dataclass(frozen=True)
class QualityContract:
    code: str
    domain: str
    severity: Severity
    repairable: bool
    repair_scope: RepairScope
    required_evidence: tuple[str, ...]
    pass_condition: str


def _contract(
    code: str,
    domain: str,
    *,
    severity: Severity = "critical",
    repairable: bool = True,
    repair_scope: RepairScope = "chapter",
    required_evidence: tuple[str, ...] = (),
    pass_condition: str = "latest quality bundle contains no finding with this code",
) -> QualityContract:
    return QualityContract(
        code=code,
        domain=domain,
        severity=severity,
        repairable=repairable,
        repair_scope=repair_scope,
        required_evidence=required_evidence,
        pass_condition=pass_condition,
    )


_CONTRACTS: dict[str, QualityContract] = {
    # Length / body completeness.
    "BLOCK_LOW": _contract("BLOCK_LOW", "length", repair_scope="chapter"),
    "BLOCK_HIGH": _contract("BLOCK_HIGH", "length", repair_scope="chapter"),
    "CHAPTER_TOO_SHORT": _contract(
        "CHAPTER_TOO_SHORT",
        "length",
        required_evidence=("zh_char_count", "hard_floor"),
        pass_condition="chapter body reaches the configured hard floor",
    ),
    "CHAPTER_BELOW_TARGET": _contract(
        "CHAPTER_BELOW_TARGET",
        "length",
        severity="high",
        repairable=True,
        required_evidence=("zh_char_count", "soft_warning"),
        pass_condition="chapter body reaches the configured soft target",
    ),
    # Continuity / retention.
    "HOOK_ECHO_MISSING": _contract(
        "HOOK_ECHO_MISSING",
        "retention",
        required_evidence=("missed_tokens", "prev_hook_tokens"),
        pass_condition="opening 1000 characters echoes, escalates, or reverses the previous hook",
    ),
    "HOOK_ECHO_LOW": _contract(
        "HOOK_ECHO_LOW",
        "retention",
        severity="high",
        required_evidence=("missed_tokens", "matched_tokens"),
    ),
    "SIGNATURE_SCENE_MISSING": _contract(
        "SIGNATURE_SCENE_MISSING",
        "retention",
        required_evidence=("signature_mandate",),
    ),
    "EXPOSITION_DUMP": _contract("EXPOSITION_DUMP", "prose", repair_scope="chapter"),
    "TIMELINE_INCONSISTENT": _contract(
        "TIMELINE_INCONSISTENT",
        "continuity",
        required_evidence=("violations",),
        pass_condition="timeline consistency gate has no critical violation",
    ),
    "SCENE_JUMP_UNRESOLVED": _contract(
        "SCENE_JUMP_UNRESOLVED",
        "logic",
        required_evidence=("jumps",),
        pass_condition="every location/time jump has an explicit bridge",
    ),
    "CHARACTER_ROLE_DRIFT": _contract(
        "CHARACTER_ROLE_DRIFT",
        "character",
        required_evidence=("findings",),
    ),
    "DIALOGUE_AI_FLAVOR": _contract(
        "DIALOGUE_AI_FLAVOR",
        "dialogue",
        repair_scope="scene",
        required_evidence=("findings",),
    ),
    # Cast / canon.
    "CAST_VIOLATION": _contract("CAST_VIOLATION", "canon", repair_scope="scene"),
    "CANON_FORBIDDEN_TERM": _contract("CANON_FORBIDDEN_TERM", "canon"),
    "CANON_STATE_REGRESSION": _contract("CANON_STATE_REGRESSION", "canon"),
    "dead_alive": _contract("dead_alive", "canon", repair_scope="scene"),
    "pronoun_mismatch": _contract("pronoun_mismatch", "canon", repair_scope="scene"),
    "character_resurrection": _contract("character_resurrection", "canon", repair_scope="scene"),
    "character_missing_appearance": _contract(
        "character_missing_appearance",
        "canon",
        repair_scope="scene",
    ),
    "character_sealed_appearance": _contract(
        "character_sealed_appearance",
        "canon",
        repair_scope="scene",
    ),
    "character_sleeping_appearance": _contract(
        "character_sleeping_appearance",
        "canon",
        repair_scope="scene",
    ),
    "character_comatose_appearance": _contract(
        "character_comatose_appearance",
        "canon",
        repair_scope="scene",
    ),
    # Prose structure / repetition.
    "DIALOG_UNPAIRED": _contract("DIALOG_UNPAIRED", "dialogue", repair_scope="scene"),
    "ENDING_SENTENCE_WEAK": _contract("ENDING_SENTENCE_WEAK", "ending", repair_scope="ending"),
    "CROSS_CHAPTER_REPETITION": _contract(
        "CROSS_CHAPTER_REPETITION",
        "repetition",
        repair_scope="paragraph",
        required_evidence=("source_chapter", "text"),
        pass_condition="current chapter no longer repeats a prior publishable paragraph",
    ),
    "INTRA_CHAPTER_REPETITION": _contract(
        "INTRA_CHAPTER_REPETITION",
        "repetition",
        repair_scope="paragraph",
    ),
    "REPEATED_EVENT_BEAT": _contract(
        "REPEATED_EVENT_BEAT",
        "repetition",
        repair_scope="paragraph",
        required_evidence=("event_signature",),
        pass_condition="the repeated event beat is merged, escalated, or replaced by a distinct action",
    ),
    "CHAPTER_OPENING_REPETITION": _contract(
        "CHAPTER_OPENING_REPETITION",
        "repetition",
        repair_scope="paragraph",
        required_evidence=("source_chapter", "opening"),
        pass_condition="chapter opening differs from the recent opening window",
    ),
    "ANTI_META_LEAK": _contract("ANTI_META_LEAK", "prose", repair_scope="paragraph"),
    "ANTI_META_ENDING_OUT_OF_SCENE": _contract(
        "ANTI_META_ENDING_OUT_OF_SCENE",
        "ending",
        repair_scope="ending",
    ),
    "SHOW_DONT_TELL": _contract(
        "SHOW_DONT_TELL",
        "prose",
        severity="high",
        repairable=True,
        repair_scope="paragraph",
    ),
    "LATE_NIGHT_DELIVERY_PLAUSIBILITY": _contract(
        "LATE_NIGHT_DELIVERY_PLAUSIBILITY",
        "logic",
        severity="high",
        repairable=True,
        repair_scope="chapter",
        required_evidence=("window",),
        pass_condition=(
            "late-night courier or delivery evidence is either removed, moved to a plausible "
            "time/channel, or framed as impossible/forged evidence in prose"
        ),
    ),
    "OBJECT_SIGNAL_OVERUSE": _contract(
        "OBJECT_SIGNAL_OVERUSE",
        "logic",
        severity="high",
        repairable=True,
        repair_scope="chapter",
        required_evidence=("hit_count", "windows"),
        pass_condition=(
            "magic object signals have distinct meanings, costs, and limits instead of "
            "repeating the same heat cue"
        ),
    ),
    "LAY_CHARACTER_RULE_KNOWLEDGE_LEAK": _contract(
        "LAY_CHARACTER_RULE_KNOWLEDGE_LEAK",
        "character",
        severity="high",
        repairable=True,
        repair_scope="chapter",
        required_evidence=("window",),
        pass_condition=(
            "non-specialist characters only describe what they see or are explicitly shown "
            "learning/being possessed before using rule terminology"
        ),
    ),
    # Planning/readiness.
    "OUTLINE_GENERIC_OR_UNSCENEABLE": _contract(
        "OUTLINE_GENERIC_OR_UNSCENEABLE",
        "planning",
        repair_scope="planning",
    ),
    "OUTLINE_STALE_AUTO_REPAIR_RESIDUE": _contract(
        "OUTLINE_STALE_AUTO_REPAIR_RESIDUE",
        "planning",
        repair_scope="metadata",
    ),
    "OUTLINE_PENDING_REWRITE_TASKS": _contract(
        "OUTLINE_PENDING_REWRITE_TASKS",
        "planning",
        repair_scope="planning",
    ),
    "QUALITY_GATE_EXECUTION_FAILED": _contract(
        "QUALITY_GATE_EXECUTION_FAILED",
        "system",
        repairable=False,
        repair_scope="package",
        pass_condition="the gate executes successfully and returns a clean result",
    ),
}


UNKNOWN_COMMERCIAL_BLOCK_CONTRACT = QualityContract(
    code="UNKNOWN_QUALITY_BLOCK_CODE",
    domain="system",
    severity="critical",
    repairable=False,
    repair_scope="package",
    required_evidence=("original_code",),
    pass_condition="register the block code with an explicit repair contract",
)


def all_quality_contracts() -> dict[str, QualityContract]:
    return dict(_CONTRACTS)


def get_quality_contract(code: str) -> QualityContract | None:
    return _CONTRACTS.get(str(code).strip())


def is_registered_quality_code(code: str) -> bool:
    return get_quality_contract(code) is not None


def contract_for_code(
    code: str,
    *,
    commercial_strict: bool = False,
) -> QualityContract:
    contract = get_quality_contract(code)
    if contract is not None:
        return contract
    if commercial_strict:
        return UNKNOWN_COMMERCIAL_BLOCK_CONTRACT
    return _contract(
        str(code).strip() or "UNKNOWN",
        "unknown",
        severity="high",
        repairable=False,
        repair_scope="package",
        required_evidence=("original_code",),
        pass_condition="no pass condition registered",
    )


__all__ = [
    "QUALITY_CONTRACT_VERSION",
    "UNKNOWN_COMMERCIAL_BLOCK_CONTRACT",
    "QualityContract",
    "all_quality_contracts",
    "contract_for_code",
    "get_quality_contract",
    "is_registered_quality_code",
]

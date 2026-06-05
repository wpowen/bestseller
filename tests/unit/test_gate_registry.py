from __future__ import annotations

import pytest

from bestseller.services.gate_registry import (
    GateRegistration,
    advanced_block_metadata_keys,
    advanced_gate_names,
    chapter_block_is_structural,
    chapter_outline_readiness_block_is_structural,
    core_block_metadata_keys,
    core_gate_names,
    gate_continuation_impact,
    local_quality_gate_names,
    pause_reason_continuation_impact,
    pause_reason_is_structural,
    project_resume_is_terminally_blocked,
    registered_block_metadata_keys,
    registered_gate_names,
    structural_gate_names,
    write_safety_block_is_structural,
)

pytestmark = pytest.mark.unit


def test_gate_registry_exposes_non_quality_block_keys() -> None:
    keys = set(registered_block_metadata_keys())

    assert "blocked_by_write_safety_gate" in keys
    assert "blocked_by_chapter_predraft_quality_gate" in keys
    assert "qimao_opening_gate_blocked" in keys


def test_qimao_exhaustion_is_not_project_terminal() -> None:
    """qimao is a *local* gate — its exhaustion must NOT freeze the whole book.

    Previously ``qimao_opening_gate_exhausted`` was a terminal project key,
    which let a single failing opening pause the entire project forever (the
    青囊不语问阴阳 regression). Local gates now confine their failure to one
    chapter, so the project stays resumable and later chapters keep writing.
    """
    assert "qimao_opening_gate" in registered_gate_names()
    assert not project_resume_is_terminally_blocked(
        {"qimao_opening_gate_exhausted": True}
    )


def test_gate_registration_requires_tier() -> None:
    """Every registration must declare a tier — keeps tier-aware helpers honest."""
    from bestseller.services.gate_registry import _GATES  # noqa: PLC0415

    assert _GATES, "gate registry must not be empty"
    for gate in _GATES:
        assert isinstance(gate, GateRegistration)
        assert gate.tier in {"core", "advanced"}, (
            f"gate {gate.name!r} is missing a tier label"
        )


def test_core_and_advanced_helpers_partition_all_gates() -> None:
    """The tier helpers must cover every registered gate exactly once."""
    all_gates = set(registered_gate_names())
    core = set(core_gate_names())
    advanced = set(advanced_gate_names())

    assert core.isdisjoint(advanced), "core and advanced must not overlap"
    assert core | advanced == all_gates, (
        "every registered gate must be classified as core or advanced"
    )


def test_advanced_gates_are_polish_or_signature_only() -> None:
    """``advanced`` tier = warning-only polish; structural gates stay core."""
    advanced = set(advanced_gate_names())
    # Polish / style / signature gates degrade to warnings; they must NOT be
    # the structural safety nets the production pipeline depends on.
    structural_core = {
        "write_safety_gate",
        "l2_bible_gate",
        "fanqie_long_ranking_gate",
        "anti_meta_gate",
        "chapter_splice_coherence_gate",
        "material_referential_integrity_gate",
        "chapter_outline_readiness_gate",
        "chapter_predraft_quality_gate",
        "qimao_opening_gate",
    }
    assert advanced.isdisjoint(structural_core)


def test_block_metadata_keys_helpers_split_with_gate_names() -> None:
    """Block-metadata key helpers must mirror the gate-name helpers."""
    # Every block-metadata key belongs to exactly one tier; the union covers
    # the full registered set (defensive against future drift).
    core_block = set(core_block_metadata_keys())
    advanced_block = set(advanced_block_metadata_keys())
    all_block = set(registered_block_metadata_keys())
    assert core_block.isdisjoint(advanced_block)
    assert core_block | advanced_block == all_block

    # The structural / safety core must keep its blocking keys.
    assert "blocked_by_write_safety_gate" in core_block
    assert "blocked_by_anti_meta_gate" in core_block
    assert "blocked_by_chapter_predraft_quality_gate" in core_block
    assert "qimao_opening_gate_blocked" in core_block
    # All-underscore identifier must match the file
    # ``material_referential_integrity_gate.py`` and the underscore
    # convention used by the writer side (book_lifecycle_quality_gate,
    # pipelines.py blocked_by_* writers, etc.).  A space-separated
    # variant here would silently disable the runtime block predicate
    # for this core gate (regression CD1, fixed 2026-06-03).
    assert "blocked_by_material_referential_integrity_gate" in core_block
    # Guard against the space-variant ever creeping back in.
    assert not any(" " in key for key in core_block), (
        "core block-metadata key contains a space — identifier corruption"
    )

    # Timeline correctness + story-contract delivery are correctness,
    # not polish. They MUST stay in core so the runtime block predicate
    # still fires when the model contradicts the canon.
    assert "blocked_by_phase_d_time_gate" in core_block
    assert "blocked_by_material_advancement_gate" in core_block

    # The polish / signature tier must surface as advanced block keys.
    assert "blocked_by_ai_flavor_gate" in advanced_block
    assert "blocked_by_show_dont_tell_gate" in advanced_block
    assert "blocked_by_signature_audit_gate" in advanced_block


# ---------------------------------------------------------------------------
# Continuation-impact classification (does a repair block forward writing?)
# ---------------------------------------------------------------------------


def test_every_gate_declares_a_continuation_impact() -> None:
    from bestseller.services.gate_registry import _GATES  # noqa: PLC0415

    for gate in _GATES:
        assert gate.continuation_impact in {"local", "structural"}, (
            f"gate {gate.name!r} has an invalid continuation_impact"
        )


def test_local_and_structural_helpers_partition_all_gates() -> None:
    all_gates = set(registered_gate_names())
    local = set(local_quality_gate_names())
    structural = set(structural_gate_names())

    assert local.isdisjoint(structural)
    assert local | structural == all_gates


def test_opening_and_polish_gates_are_local() -> None:
    """The gates that looped 青囊 ch1 must not block forward writing."""
    local = set(local_quality_gate_names())
    assert {
        "qimao_opening_gate",
        "fanqie_long_ranking_gate",
        "anti_meta_gate",
        "ai_flavor_gate",
        "show_dont_tell_gate",
        "signature_audit_gate",
    } <= local


def test_canon_and_continuity_gates_are_structural() -> None:
    structural = set(structural_gate_names())
    assert {
        "write_safety_gate",
        "l2_bible_gate",
        "chapter_splice_coherence_gate",
        "material_referential_integrity_gate",
        "chapter_outline_readiness_gate",
        "phase_d_time_gate",
        "material_advancement_gate",
    } <= structural


def test_gate_continuation_impact_lookup_defaults_structural() -> None:
    assert gate_continuation_impact("qimao_opening_gate") == "local"
    assert gate_continuation_impact("material_referential_integrity_gate") == "structural"
    # Unknown gate names stay conservative.
    assert gate_continuation_impact("totally_unknown_gate") == "structural"


def test_write_safety_block_code_classification() -> None:
    # Prose-surface findings are local.
    assert not write_safety_block_is_structural("block_low")
    assert not write_safety_block_is_structural(["block_high", "dialog_unpaired"])
    assert not write_safety_block_is_structural("CHAPTER_LENGTH_BLOCK_HIGH")
    assert not write_safety_block_is_structural(
        ["CHAPTER_LENGTH_BLOCK_LOW", "CHAPTER_TOO_SHORT", "LENGTH_OVER"]
    )
    # Canon / continuity findings are structural.
    assert write_safety_block_is_structural("canon_state_regression")
    assert write_safety_block_is_structural(["block_low", "character_resurrection"])
    # Missing/unknown code stays conservative.
    assert write_safety_block_is_structural(None)
    assert write_safety_block_is_structural("some_future_code")


def test_chapter_outline_readiness_block_code_classification() -> None:
    assert not chapter_outline_readiness_block_is_structural(
        "OUTLINE_STALE_AUTO_REPAIR_RESIDUE"
    )
    assert not chapter_outline_readiness_block_is_structural(
        ["OUTLINE_STALE_AUTO_REPAIR_RESIDUE", "OUTLINE_PENDING_REWRITE_TASK"]
    )
    assert chapter_outline_readiness_block_is_structural("OUTLINE_NO_SCENES")
    assert chapter_outline_readiness_block_is_structural(
        ["OUTLINE_PENDING_REWRITE_TASK", "OUTLINE_NO_SCENES"]
    )
    assert chapter_outline_readiness_block_is_structural(None)


def test_chapter_block_is_structural_for_local_opening_gate() -> None:
    # A chapter blocked only by the opening gate must NOT block continuation.
    assert not chapter_block_is_structural({"qimao_opening_gate_blocked": True})
    assert not chapter_block_is_structural({"opening_quality_gate_blocked": True})
    assert not chapter_block_is_structural(
        {"blocked_by_fanqie_long_ranking_gate": True}
    )


def test_chapter_block_is_local_for_outline_process_locks() -> None:
    assert not chapter_block_is_structural(
        {
            "blocked_by_chapter_outline_readiness_gate": True,
            "chapter_outline_readiness_block_codes": [
                "OUTLINE_STALE_AUTO_REPAIR_RESIDUE"
            ],
        }
    )
    assert not chapter_block_is_structural(
        {
            "blocked_by_chapter_outline_readiness_gate": True,
            "chapter_outline_readiness_block_codes": ["OUTLINE_PENDING_REWRITE_TASK"],
        }
    )
    assert chapter_block_is_structural(
        {
            "blocked_by_chapter_outline_readiness_gate": True,
            "chapter_outline_readiness_block_codes": ["OUTLINE_NO_SCENES"],
        }
    )


def test_chapter_block_is_structural_for_canon_regression() -> None:
    assert chapter_block_is_structural(
        {"blocked_by_material_referential_integrity_gate": True}
    )
    assert chapter_block_is_structural(
        {
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "canon_state_regression",
        }
    )


def test_chapter_block_write_safety_length_is_local() -> None:
    # Length-only write-safety block is local prose, not structural.
    assert not chapter_block_is_structural(
        {
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "block_low",
        }
    )


def test_chapter_block_structural_dominates_mixed_signals() -> None:
    # Local opening gate + structural canon gate → structural wins.
    assert chapter_block_is_structural(
        {
            "qimao_opening_gate_blocked": True,
            "blocked_by_phase_d_time_gate": True,
        }
    )


def test_chapter_block_unknown_reason_is_conservative() -> None:
    # Blocked but no recognized gate metadata → assume structural.
    assert chapter_block_is_structural({"some_unrelated_flag": True})
    assert chapter_block_is_structural({})


def test_pause_reason_classification() -> None:
    # The qimao opening exhaustion pause is local — it must not gate writing.
    assert pause_reason_continuation_impact("qimao_opening_gate_exhausted") == "local"
    assert pause_reason_is_structural("qimao_opening_gate_exhausted") is False
    # Gate-name-based reasons resolve via the registry.
    assert pause_reason_is_structural("qimao_opening_gate") is False
    assert pause_reason_is_structural("material_referential_integrity_gate") is True
    # ``reason:detail`` form matches on the base segment.
    assert pause_reason_is_structural("qimao_opening_gate_exhausted:ch1") is False
    # Unknown / empty reasons stay conservative.
    assert pause_reason_is_structural("self_heal_no_progress_giveup") is True
    assert pause_reason_is_structural(None) is True
    assert pause_reason_is_structural("") is True

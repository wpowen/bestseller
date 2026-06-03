"""WS-C regression: advanced-tier gates must never block a chapter.

The runtime blocking predicate in ``pipelines.py`` reads
``_NON_QUALITY_BLOCK_METADATA_KEYS`` to decide whether a chapter still has
outstanding structural issues that justify another auto-repair pass. After
WS-C1/C2 this set must be restricted to ``core`` tier block keys — letting
``advanced`` tier keys in would re-introduce the historical death-spiral
where weak-model signature_audit / ai_flavor hits would loop the chapter
through ``machine_repair_required``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import pipelines
from bestseller.services.gate_registry import (
    advanced_block_metadata_keys,
    advanced_gate_names,
    core_block_metadata_keys,
    registered_gate_names,
)

pytestmark = pytest.mark.unit


def test_runtime_block_predicate_excludes_advanced_tier() -> None:
    """Advanced tier must not appear in the runtime block-key set."""
    runtime_block = set(pipelines._NON_QUALITY_BLOCK_METADATA_KEYS)
    advanced_block = set(advanced_block_metadata_keys())
    assert runtime_block.isdisjoint(advanced_block), (
        "advanced-tier gates must not be runtime-blocking; found overlap: "
        f"{sorted(runtime_block & advanced_block)}"
    )


def test_runtime_block_predicate_keeps_core_tier() -> None:
    """The core structural safety nets must still be runtime-blocking."""
    runtime_block = set(pipelines._NON_QUALITY_BLOCK_METADATA_KEYS)
    core_block = set(core_block_metadata_keys())
    assert core_block <= runtime_block, (
        "every core-tier block key must still block; missing: "
        f"{sorted(core_block - runtime_block)}"
    )


def test_runtime_block_predicate_equals_core_keys() -> None:
    """The predicate is exactly the core set, no manual drift allowed."""
    assert set(pipelines._NON_QUALITY_BLOCK_METADATA_KEYS) == set(
        core_block_metadata_keys()
    )


def test_chapter_block_predicate_ignores_advanced_metadata() -> None:
    """Smoke test: stamping only an advanced key must NOT report a block."""
    chapter = SimpleNamespace(metadata_json={"blocked_by_ai_flavor_gate": True})
    assert pipelines._chapter_has_non_quality_block_metadata(chapter) is False


def test_chapter_block_predicate_still_detects_core_metadata() -> None:
    """The predicate must still catch the core structural blocks."""
    chapter = SimpleNamespace(metadata_json={"blocked_by_write_safety_gate": True})
    assert pipelines._chapter_has_non_quality_block_metadata(chapter) is True


def test_advanced_warnings_survive_in_overview_schemas() -> None:
    """The overview/recovery views still see advanced keys (warning surface)."""
    from bestseller.services.gate_registry import registered_block_metadata_keys

    overview = set(registered_block_metadata_keys())
    assert set(advanced_block_metadata_keys()) <= overview
    assert "blocked_by_ai_flavor_gate" in overview
    assert "blocked_by_signature_audit_gate" in overview


def test_advanced_gate_names_cover_polish_tier() -> None:
    """Sanity: only genuine prose-polish gates stay in advanced.

    ``phase_d_time_gate`` (D3 timeline arithmetic + time-regression) and
    ``material_advancement_gate`` (story-contract delivery — required
    reveal/rule/evidence must land in the prose) are correctness gates.
    Demoting them to warn-only would let canon contradictions and
    undelivered plot obligations slip into the published text —
    directly worsening the "逻辑不清晰" complaint WS-C is supposed to
    fix.
    """
    expected_advanced = {
        "ai_flavor_gate",
        "show_dont_tell_gate",
        "signature_audit_gate",
    }
    assert expected_advanced <= set(advanced_gate_names())
    # Correctness gates stay in core.
    assert "phase_d_time_gate" not in advanced_gate_names()
    assert "material_advancement_gate" not in advanced_gate_names()
    assert "phase_d_time_gate" in registered_gate_names()
    assert "material_advancement_gate" in registered_gate_names()

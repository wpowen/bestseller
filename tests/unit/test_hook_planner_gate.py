from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.planner import _run_hook_strength_gate

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_planner_hook_gate_skips_instead_of_fabricating_a_hook() -> None:
    """2026-07-31 product ruling: a build-time hook/金手指 must never be
    fabricated from the framework's mechanism template library. When neither
    conception nor the user supplied a hook_spec, the gate skips — it must not
    invent one and must leave project metadata untouched."""
    settings = SimpleNamespace(
        hook_engine=SimpleNamespace(
            enabled=True,
            min_h_norm=30.0,
            candidate_count=6,
            rank_weight_h_norm=0.62,
            rank_weight_novelty=0.28,
            rank_weight_duplicate_risk=0.10,
        )
    )
    project = SimpleNamespace(
        slug="mystery-hook-demo",
        title="规则医院",
        genre="悬疑",
        language="zh-CN",
        metadata_json={},
    )

    spec, payload = await _run_hook_strength_gate(
        settings,
        project=project,
        premise="主角被困在一所每天改写病历的规则医院。",
    )

    assert spec is None
    assert payload is None
    assert "hook_spec" not in project.metadata_json
    assert "hook_candidates" not in project.metadata_json

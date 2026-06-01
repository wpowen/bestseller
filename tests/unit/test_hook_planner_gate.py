from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.planner import _run_hook_strength_gate

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_planner_hook_gate_generates_passing_or_repaired_hook() -> None:
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

    assert spec is not None
    assert payload is not None
    assert payload["score"]["verdict"] != "reject"
    assert project.metadata_json["hook_spec"]["one_liner"] == spec.one_liner
    assert project.metadata_json["hook_strength_gate"]["h_norm"] == payload["h_norm"]

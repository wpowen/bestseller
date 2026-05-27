from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.gate_registry import registered_gate_names
from bestseller.services.pipeline_flow_overview import (
    PIPELINE_FLOW_SCHEMA_VERSION,
    build_pipeline_flow_schema,
    schema_gate_node_ids,
    schema_step_names_for_drift_check,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_PY = REPO_ROOT / "src/bestseller/services/pipelines.py"
PLANNER_PY = REPO_ROOT / "src/bestseller/services/planner.py"


def test_schema_version_is_stable() -> None:
    assert PIPELINE_FLOW_SCHEMA_VERSION == "pipeline-flow-v1"


def test_all_paths_have_entry_and_export_nodes() -> None:
    nodes, _edges = build_pipeline_flow_schema()
    by_id = {n.id: n for n in nodes}

    for path_id, entry_id, export_id in (
        ("standard", "project_create", "export_project_markdown"),
        ("progressive", "generate_foundation_plan", "volume_feedback"),
        ("fanqie_short", "fanqie_foundation_plan", "fanqie_export"),
    ):
        assert entry_id in by_id
        assert export_id in by_id
        assert path_id in by_id[entry_id].paths or "all" in by_id[entry_id].paths
        assert path_id in by_id[export_id].paths


def test_every_registered_gate_has_schema_node() -> None:
    nodes, _ = build_pipeline_flow_schema()
    node_ids = {n.id for n in nodes}
    for gate_name in registered_gate_names():
        assert gate_name in node_ids, f"missing gate node: {gate_name}"
    assert node_ids >= schema_gate_node_ids()


def test_schema_step_names_exist_in_pipeline_or_planner_source() -> None:
    pipelines_src = PIPELINES_PY.read_text(encoding="utf-8")
    planner_src = PLANNER_PY.read_text(encoding="utf-8")
    combined = pipelines_src + planner_src

    missing: list[str] = []
    for step_name in schema_step_names_for_drift_check():
        if step_name and step_name not in combined:
            missing.append(step_name)

    # Dynamic review/rewrite steps are versioned at runtime.
    allowed_dynamic_prefixes = (
        "review_scene_v",
        "rewrite_scene_v",
        "chapter_auto_repair",
        "generate_volume_",
    )
    missing = [
        name
        for name in missing
        if not any(name.startswith(prefix) for prefix in allowed_dynamic_prefixes)
    ]
    assert not missing, f"step names missing from pipelines/planner: {missing}"


def test_schema_has_materialize_and_chapter_nodes() -> None:
    nodes, edges = build_pipeline_flow_schema()
    ids = {n.id for n in nodes}
    assert "materialize_chapter_outline_batch" in ids
    assert "chapter_loop" in ids
    assert "scene_by_scene" in ids
    edge_pairs = {(e.from_id, e.to_id) for e in edges}
    assert ("materialize_story_bible", "materialize_chapter_outline_batch") in edge_pairs

from __future__ import annotations

from pathlib import Path

from bestseller.services.entry_blueprint_library import (
    blueprint_from_material_row,
    load_active_material_rows,
    select_entry_blueprints,
)


def test_blueprint_from_active_material_row_maps_state_variables() -> None:
    row = {
        "dimension": "plot_patterns",
        "slug": "high-value-asset-cost",
        "name": "高价值资产维护成本",
        "narrative_summary": "高价值资产必须带来维护成本或势力关注。",
        "content_json": {
            "state_variables": ["asset_control", "faction_attention"],
            "required_cost": "维护成本",
            "distillation_source_ids": ["source-0001"],
        },
        "genre": "异界",
        "sub_genre": "cross-system",
        "confidence": 0.86,
        "status": "active",
    }

    bp = blueprint_from_material_row(row)

    assert bp is not None
    assert bp.blueprint_id == "high-value-asset-cost"
    assert "asset_control" in bp.state_variables
    assert bp.source_lineage[0]["source_id"] == "source-0001"


def test_blueprint_rejects_source_specific_or_inactive_rows() -> None:
    inactive = blueprint_from_material_row(
        {
            "dimension": "plot_patterns",
            "slug": "inactive",
            "name": "未启用",
            "narrative_summary": "抽象机制",
            "content_json": {},
            "confidence": 0.9,
            "status": "review",
        }
    )
    source_specific = blueprint_from_material_row(
        {
            "dimension": "plot_patterns",
            "slug": "named-source-asset",
            "name": "来源具名资产",
            "narrative_summary": "复用 named_artifacts 的具体组合。",
            "content_json": {"blocked_elements": ["named_artifacts"]},
            "confidence": 0.9,
            "status": "active",
        }
    )

    assert inactive is None
    assert source_specific is None


def test_select_entry_blueprints_prefers_genre_and_keywords() -> None:
    rows = [
        {
            "dimension": "plot_patterns",
            "slug": "asset-cost",
            "name": "资产代价",
            "narrative_summary": "高价值资产带来势力关注。",
            "content_json": {"state_variables": ["faction_attention"]},
            "genre": "异界",
            "sub_genre": "cross-system",
            "tags": ["asset"],
            "confidence": 0.8,
            "status": "active",
        },
        {
            "dimension": "scene_templates",
            "slug": "unrelated",
            "name": "无关场景",
            "narrative_summary": "恋爱误会。",
            "content_json": {},
            "genre": "言情",
            "sub_genre": "romance",
            "confidence": 0.95,
            "status": "active",
        },
    ]

    selected = select_entry_blueprints(
        rows,
        genre="异界",
        sub_genre="cross-system",
        story_keywords=("资产", "势力"),
        limit=1,
    )

    assert [bp.blueprint_id for bp in selected] == ["asset-cost"]


def test_load_active_material_rows_reads_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "material_entries.active.jsonl"
    path.write_text(
        '{"status":"active","dimension":"plot_patterns","slug":"a","name":"A","narrative_summary":"机制","content_json":{},"confidence":0.8}\n'
        '{"status":"review","dimension":"plot_patterns","slug":"b","name":"B","narrative_summary":"机制","content_json":{},"confidence":0.8}\n',
        encoding="utf-8",
    )

    rows = load_active_material_rows((path,))

    assert [row["slug"] for row in rows] == ["a"]

from __future__ import annotations

import pytest

from bestseller.services.distilled_worldview_bridge import (
    build_distilled_worldview_bindings,
)

pytestmark = pytest.mark.unit


def _strategy_card() -> dict[str, object]:
    return {
        "aggregate_key": "otherworld-cross-system",
        "selected_mechanisms": [
            {
                "mechanism_id": "cross-system-rule-arbitrage",
                "source_confidence": 0.86,
                "design_role": "world_pressure",
                "required_project_specific_binding": "绑定到本书的法则错位。",
            },
            {
                "mechanism_id": "high-level-entity-to-asset",
                "source_confidence": 0.76,
                "design_role": "world_pressure",
                "required_project_specific_binding": "绑定到本书的高阶盟友资产。",
            },
        ],
        "required_state_variables": [
            "cross_system_understanding",
            "higher_authority_attention",
        ],
        "anti_copy_boundaries": [
            "specific_family_inheritance_murder",
            "specific_family_inheritance_murder",
        ],
    }


def _materials() -> list[dict[str, object]]:
    return [
        {
            "dimension": "plot_patterns",
            "slug": "cross-system-rule-arbitrage",
            "name": "跨体系规则套利",
            "narrative_summary": "主角用旧体系解释、破解或绕开当地规则。",
            "content_json": {
                "state_variables": [
                    "cross_system_understanding",
                    "old_system_adaptation",
                    "higher_authority_attention",
                ],
                "required_cost": "每次破解规则后，必须增加暴露度或更高层级势力关注。",
            },
            "tags": ["otherworld", "knowledge-arbitrage", "rule-gap"],
        },
        {
            "dimension": "plot_patterns",
            "slug": "high-level-entity-to-asset",
            "name": "高阶存在资产化",
            "narrative_summary": "高阶存在转化为盟友、情报源或势力入口。",
            "content_json": {
                "state_variables": ["bound_assets", "political_entanglement"],
                "guardrail": "每个资产必须附带维护成本或敌对关注。",
            },
            "tags": ["asset-conversion", "ally", "escalation"],
        },
        {
            "dimension": "scene_templates",
            "slug": "social-venue-world-display",
            "name": "社会场景展示规则",
            "narrative_summary": "用工会、夜市、拍卖等高互动场景展示世界规则。",
            "content_json": {
                "required_change": ["resource", "relationship", "faction_pressure"],
            },
            "tags": ["worldbuilding", "venue", "social-conflict"],
        },
        {
            "dimension": "plot_patterns",
            "slug": "host-identity-debt",
            "name": "宿主身份债",
            "narrative_summary": "继承原身身份后必须承担家族义务、旧仇和社会评价。",
            "content_json": {
                "state_variables": ["identity_debt", "political_entanglement"],
                "chapter_use": "每一卷至少回收一个身份债或让身份债升级。",
            },
            "tags": ["transmigration", "identity-debt", "family-pressure"],
        },
        {
            "dimension": "anti_cliche_patterns",
            "slug": "do-not-copy-source-specific-opening",
            "name": "禁复刻来源书具体开局",
            "content_json": {
                "blocked_elements": [
                    "specific_portal_accident",
                    "specific_family_inheritance_murder",
                ]
            },
            "tags": ["anti-copy", "opening"],
        },
    ]


def test_bridge_extracts_world_state_variables_from_strategy_card() -> None:
    payload = build_distilled_worldview_bindings(
        _strategy_card(),
        aggregate_materials=_materials(),
    )

    state_keys = {item["key"] for item in payload["state_variables"]}
    binding = payload["distilled_mechanism_bindings"][0]

    assert "cross_system_understanding" in state_keys
    assert "higher_authority_attention" in state_keys
    assert "old_system_adaptation" in state_keys
    assert binding["mechanism_id"] == "cross-system-rule-arbitrage"
    assert binding["state_variables"] == [
        "cross_system_understanding",
        "old_system_adaptation",
        "higher_authority_attention",
    ]
    assert "更高层级势力关注" in binding["required_cost"]


def test_bridge_maps_cross_system_assets_and_scene_templates() -> None:
    payload = build_distilled_worldview_bindings(
        _strategy_card(),
        aggregate_materials=_materials(),
    )

    asset_keys = {item["key"] for item in payload["asset_ledger"]}
    template_keys = {item["key"] for item in payload["scene_templates"]}
    claim_targets = {item["target"] for item in payload["authority_claims"]}

    assert "high-level-entity-to-asset" in asset_keys
    assert "social-venue-world-display" in template_keys
    assert "host-identity-debt" in claim_targets
    assert payload["scene_templates"][0]["required_change"] == [
        "resource",
        "relationship",
        "faction_pressure",
    ]


def test_bridge_dedupes_anti_copy_boundaries() -> None:
    payload = build_distilled_worldview_bindings(
        _strategy_card(),
        aggregate_materials=_materials(),
    )

    assert payload["anti_copy_boundaries"].count(
        "specific_family_inheritance_murder"
    ) == 1
    assert "specific_portal_accident" in payload["anti_copy_boundaries"]

from __future__ import annotations

from pydantic import ValidationError

from bestseller.services.entry_blueprint_library import EntryBlueprint
from bestseller.services.entry_system_kernel import (
    build_fallback_entry_system_kernel,
    entry_system_kernel_from_dict,
    entry_system_kernel_to_dict,
    render_entry_system_kernel_prompt_block,
    validate_entry_system_kernel,
)


def test_fallback_kernel_for_xianxia_contains_progression_taxonomy() -> None:
    kernel = build_fallback_entry_system_kernel(
        {
            "genre": "修仙升级",
            "sub_genre": "宗门逆袭",
            "target_chapters": 120,
        },
        story_design_kernel={
            "reader_promise": "每卷都有可见进阶和资源代价。",
            "change_vectors": ["境界变化", "资源变化"],
        },
    )

    taxonomy = kernel.taxonomy_by_type

    assert "artifact" in taxonomy
    assert "technique" in taxonomy
    assert "resource" in taxonomy
    assert "identity" in taxonomy
    assert kernel.coverage_targets["active_axes"]
    assert kernel.grade_ladders


def test_fallback_kernel_uses_blueprint_state_variables_and_costs() -> None:
    blueprint = EntryBlueprint(
        blueprint_id="asset-cost",
        dimension="plot_patterns",
        name="资产代价",
        mechanism_summary="高价值资产必须带来势力关注。",
        state_variables=("asset_control", "faction_attention"),
        required_cost_patterns=("visibility_increase",),
        anti_copy_boundaries=("no source names",),
        confidence=0.86,
    )

    kernel = build_fallback_entry_system_kernel(
        {"genre": "异界", "sub_genre": "cross-system"},
        blueprints=(blueprint,),
    )

    assert any(axis.axis == "asset_control" for axis in kernel.capability_axes)
    assert "visibility_increase" in kernel.cost_model.default_cost_types
    assert "no source names" in kernel.anti_copy_rules


def test_render_entry_system_kernel_prompt_block_includes_rules() -> None:
    kernel = build_fallback_entry_system_kernel({"genre": "悬疑生存", "sub_genre": "规则怪谈"})

    block = render_entry_system_kernel_prompt_block(kernel)

    assert "【词条体系约束】" in block
    assert "硬规则" in block
    assert "证据" in block or "规则" in block


def test_validate_entry_system_kernel_reports_missing_ladder_levels() -> None:
    kernel = entry_system_kernel_from_dict(
        {
            "system_promise": "测试",
            "taxonomy": [
                {"type": "artifact", "label": "法宝", "required_fields": ["capabilities"]}
            ],
            "grade_ladders": [
                {
                    "ladder_key": "artifact_grade",
                    "label": "法宝品阶",
                    "levels": [],
                    "promotion_rule": "必须有代价",
                }
            ],
        }
    )

    findings = validate_entry_system_kernel(kernel)

    assert {finding.code for finding in findings} == {"grade_ladder_levels_missing"}


def test_entry_system_kernel_round_trip() -> None:
    kernel = build_fallback_entry_system_kernel({"genre": "末日爽文", "target_chapters": 60})
    payload = entry_system_kernel_to_dict(kernel)

    hydrated = entry_system_kernel_from_dict(payload)

    assert hydrated.system_promise == kernel.system_promise


def test_entry_system_kernel_from_dict_raises_for_invalid_payload() -> None:
    try:
        entry_system_kernel_from_dict({"taxonomy": []})
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid payload should raise")

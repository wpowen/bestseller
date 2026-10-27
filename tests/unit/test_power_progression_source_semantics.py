"""Source-bound power progression must not invent mechanics from tier names.

``power_tiers`` is an ordered name list.  ``growth_curve`` prose is a stage
description, optionally annotated with an unlock chapter.  Neither source is,
by itself, a declaration of a bottleneck or breakthrough cost.
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.domain.story_bible import PowerSystemInput
from bestseller.services.planner import derive_source_bound_power_system


def _project(**metadata: object) -> SimpleNamespace:
    return SimpleNamespace(metadata_json=metadata, language="zh-CN")


def test_structured_tier_names_do_not_become_mechanics() -> None:
    power_system = derive_source_bound_power_system(
        _project(
            writing_profile={
                "character": {"power_tiers": ["炼气", "筑基", "金丹"]},
            }
        ),
        engine="e",
    )

    assert power_system is not None
    assert power_system["tiers"] == ["炼气", "筑基", "金丹"]
    assert power_system["tier_progression"] == [
        {"tier": "炼气"},
        {"tier": "筑基"},
        {"tier": "金丹"},
    ]
    assert all(
        "bottleneck" not in entry and "breakthrough_cost" not in entry
        for entry in power_system["tier_progression"]
    )


def test_structured_sparse_progression_survives_power_system_schema() -> None:
    power_system = derive_source_bound_power_system(
        _project(
            writing_profile={"character": {"power_tiers": ["杂役", "执簿境"]}},
        ),
        engine="e",
    )

    assert power_system is not None
    parsed = PowerSystemInput.model_validate(power_system)
    assert [item.tier for item in parsed.tier_progression] == ["杂役", "执簿境"]
    assert parsed.tier_progression[0].breakthrough_cost is None
    assert parsed.tier_progression[0].bottleneck is None


def test_growth_curve_keeps_stage_description_and_unlock_chapter() -> None:
    curve = (
        "起步=杂役级目视范围；第10章=还力诀残页解锁，目视范围扩至三丈；"  # noqa: RUF001
        "第35章=清债令牌解锁执簿境"
    )
    power_system = derive_source_bound_power_system(
        _project(growth_curve=curve),
        engine="还力兑现",
    )

    assert power_system is not None
    progression = power_system["tier_progression"]
    assert progression[0]["description"] == "杂役级目视范围"
    assert progression[1]["description"] == "还力诀残页解锁，目视范围扩至三丈"  # noqa: RUF001
    assert progression[1]["unlocks_at_chapter"] == 10
    assert progression[2]["description"] == "清债令牌解锁执簿境"
    assert progression[2]["unlocks_at_chapter"] == 35
    assert all(
        "bottleneck" not in entry and "breakthrough_cost" not in entry
        for entry in progression
    )


def test_growth_curve_projection_round_trip_does_not_restore_unknown_mechanics() -> None:
    power_system = derive_source_bound_power_system(
        _project(growth_curve="第1章=入门识字；第20章=打开旧库"),  # noqa: RUF001
        engine="e",
    )

    assert power_system is not None
    dumped = PowerSystemInput.model_validate(power_system).model_dump(mode="json")
    progression = dumped["tier_progression"]
    assert progression[0]["description"] == "入门识字"
    assert progression[1]["description"] == "打开旧库"
    assert progression[0]["unlocks_at_chapter"] == 1
    assert progression[1]["unlocks_at_chapter"] == 20
    assert all(
        entry["bottleneck"] is None and entry["breakthrough_cost"] is None
        for entry in progression
    )


def test_explicit_cost_and_gate_aliases_remain_schema_supported() -> None:
    parsed = PowerSystemInput.model_validate(
        {
            "tiers": ["炼气", "筑基"],
            "tier_progression": [
                {"tier": "筑基", "cost": "三枚灵石", "gate": "灵脉闭塞"}
            ],
        }
    ).model_dump(mode="json")

    entry = parsed["tier_progression"][0]
    assert entry["breakthrough_cost"] == "三枚灵石"
    assert entry["bottleneck"] == "灵脉闭塞"


def test_compiled_source_bound_world_keeps_sparse_progression_sparse() -> None:
    from bestseller.services.planner import _compile_source_bound_world_spec

    project = _project(
        growth_curve="第1章=入门识字；第20章=打开旧库",  # noqa: RUF001
        book_design_snapshot={"protagonist": {"name": "祝余"}},
    )
    spec = _compile_source_bound_world_spec(project, "祝余打开旧库", {})
    progression = spec["power_system"]["tier_progression"]

    assert progression[0]["description"] == "入门识字"
    assert all(
        entry["bottleneck"] is None and entry["breakthrough_cost"] is None
        for entry in progression
    )


def test_growth_curve_does_not_borrow_next_stage_as_final_cost() -> None:
    power_system = derive_source_bound_power_system(
        _project(growth_curve="第1章=入门识字；第20章=打开旧库"),  # noqa: RUF001
        engine="e",
    )

    assert power_system is not None
    final_entry = power_system["tier_progression"][-1]
    assert final_entry["description"] == "打开旧库"
    assert "breakthrough_cost" not in final_entry
    assert "bottleneck" not in final_entry

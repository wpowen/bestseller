from __future__ import annotations

from pydantic import ValidationError
import pytest

from bestseller.domain.geography import GeographyKernel, Region, RouteEdge
from bestseller.services.geography_continuity_gate import scan_geography_continuity
from bestseller.services.geography_kernel import render_geography_prompt_block

pytestmark = pytest.mark.unit


def _region(name: str) -> Region:
    return Region(
        name=name,
        climate="温带",
        terrain="河网",
        demographics="商旅与本地宗族混居",
        dominant_faction=None,
        surface_economy=["稻米", "盐船"],
        cultural_signature=f"{name}以码头茶棚和水路消息闻名。",
    )


def _kernel() -> GeographyKernel:
    return GeographyKernel(
        regions=[_region("江宁"), _region("青崖"), _region("北渡")],
        routes=[
            RouteEdge(
                region_a="江宁",
                region_b="青崖",
                kind="水路",
                days_typical=2,
                hazard_level=1,
                hazard_kinds=["水匪"],
            ),
            RouteEdge(
                region_a="青崖",
                region_b="北渡",
                kind="官道",
                days_typical=3,
                hazard_level=2,
                hazard_kinds=["边军盘查"],
            ),
        ],
        capital_region="江宁",
        protagonist_origin="江宁",
        protagonist_current="青崖",
    )


def test_kernel_rejects_isolated_region() -> None:
    with pytest.raises(ValidationError):
        GeographyKernel(
            regions=[_region("江宁"), _region("青崖"), _region("孤城")],
            routes=[
                RouteEdge(
                    region_a="江宁",
                    region_b="青崖",
                    kind="官道",
                    days_typical=1,
                    hazard_level=0,
                    hazard_kinds=[],
                )
            ],
            capital_region="江宁",
            protagonist_origin="江宁",
            protagonist_current="江宁",
        )


def test_kernel_links_locations_to_regions() -> None:
    kernel = _kernel()
    assert kernel.validate_location_regions(
        [
            {"name": "青崖驿", "region_id": "青崖"},
            {"name": "北渡城门", "region_id": "北渡"},
        ]
    ) == []
    assert kernel.validate_location_regions([{"name": "空港", "region_id": "云外"}])


def test_prompt_block_includes_adjacent_regions() -> None:
    block = render_geography_prompt_block(_kernel(), current_region="青崖")
    assert "青崖" in block
    assert "江宁" in block
    assert "北渡" in block
    assert "水路" in block
    assert "官道" in block


def test_gate_flags_geographic_jump() -> None:
    report = scan_geography_continuity(
        _kernel(),
        chapter_regions=["江宁", "北渡"],
        chapter_no=8,
    )
    assert report.findings
    assert report.findings[0].code == "geographic_jump_without_route"


from __future__ import annotations

import pytest

from bestseller.domain.geography import GeographyKernel, Region, RouteEdge
from bestseller.services.geography_continuity_gate import scan_geography_continuity

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


def test_gate_flags_geographic_jump() -> None:
    report = scan_geography_continuity(
        _kernel(),
        chapter_regions=["江宁", "北渡"],
        chapter_no=8,
    )
    assert [finding.code for finding in report.findings] == [
        "geographic_jump_without_route"
    ]


def test_gate_allows_adjacent_route_chain() -> None:
    report = scan_geography_continuity(
        _kernel(),
        chapter_regions=["江宁", "青崖", "北渡"],
        chapter_no=9,
    )
    assert not report.findings


def test_gate_flags_unknown_region() -> None:
    report = scan_geography_continuity(
        _kernel(),
        chapter_regions=["江宁", "云外"],
        chapter_no=10,
    )
    assert any(finding.code == "unknown_region" for finding in report.findings)


from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Region(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    climate: str = Field(min_length=1)
    terrain: str = Field(min_length=1)
    demographics: str = Field(min_length=1)
    dominant_faction: str | None = None
    surface_economy: list[str] = Field(default_factory=list)
    cultural_signature: str = Field(min_length=1)


class RouteEdge(BaseModel, frozen=True):
    region_a: str = Field(min_length=1)
    region_b: str = Field(min_length=1)
    kind: Literal["官道", "水路", "山道", "海路", "秘径"]
    days_typical: int = Field(ge=1)
    hazard_level: int = Field(ge=0, le=5)
    hazard_kinds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_self_loop(self) -> RouteEdge:
        if self.region_a == self.region_b:
            raise ValueError("route edge must connect two different regions")
        return self


class GeographyKernel(BaseModel, frozen=True):
    regions: list[Region] = Field(min_length=3)
    routes: list[RouteEdge] = Field(min_length=2)
    capital_region: str | None = None
    protagonist_origin: str = Field(min_length=1)
    protagonist_current: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_known_regions_and_connectivity(self) -> GeographyKernel:
        names = {region.name for region in self.regions}
        required = {self.protagonist_origin, self.protagonist_current}
        if self.capital_region:
            required.add(self.capital_region)
        unknown_required = required - names
        if unknown_required:
            raise ValueError(f"geography references unknown regions: {sorted(unknown_required)}")

        graph = {name: set[str]() for name in names}
        for route in self.routes:
            if route.region_a not in names or route.region_b not in names:
                raise ValueError(
                    "route edge references unknown region: "
                    f"{route.region_a!r}->{route.region_b!r}"
                )
            graph[route.region_a].add(route.region_b)
            graph[route.region_b].add(route.region_a)

        seen: set[str] = set()
        stack = [self.regions[0].name]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph[current] - seen)
        isolated = names - seen
        if isolated:
            raise ValueError(f"all regions must be reachable; isolated={sorted(isolated)}")
        return self

    def region_names(self) -> set[str]:
        return {region.name for region in self.regions}

    def region_by_name(self, name: str) -> Region | None:
        return next((region for region in self.regions if region.name == name), None)

    def adjacent_regions(self, region_name: str) -> list[Region]:
        adjacent: set[str] = set()
        for route in self.routes:
            if route.region_a == region_name:
                adjacent.add(route.region_b)
            elif route.region_b == region_name:
                adjacent.add(route.region_a)
        return [
            region
            for region in self.regions
            if region.name in adjacent
        ]

    def route_between(self, region_a: str, region_b: str) -> RouteEdge | None:
        for route in self.routes:
            if {route.region_a, route.region_b} == {region_a, region_b}:
                return route
        return None

    def validate_location_regions(self, locations: list[Any]) -> list[str]:
        names = self.region_names()
        missing: list[str] = []
        for location in locations:
            if isinstance(location, dict):
                region_id = str(location.get("region_id") or "").strip()
                loc_name = str(location.get("name") or "(unnamed)").strip()
            elif hasattr(location, "model_dump"):
                dumped = location.model_dump()
                region_id = str(dumped.get("region_id") or "").strip()
                loc_name = str(dumped.get("name") or "(unnamed)").strip()
            else:
                region_id = str(getattr(location, "region_id", "") or "").strip()
                loc_name = str(getattr(location, "name", "(unnamed)") or "(unnamed)").strip()
            if region_id and region_id not in names:
                missing.append(f"{loc_name}:{region_id}")
        return missing


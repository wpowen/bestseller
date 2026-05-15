"""Unit tests for ``quality_levers.sensory_inventory``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.sensory_inventory import (
    get_scene_requirement,
    load_sensory_inventory,
    render_sensory_requirement_block,
)


pytestmark = pytest.mark.unit


def test_load_sensory_inventory_returns_expected_axes() -> None:
    config = load_sensory_inventory()
    axis_ids = set(config.axes.keys())
    assert {
        "visual",
        "auditory",
        "olfactory",
        "tactile",
        "thermal",
        "spatial",
        "temporal",
    } <= axis_ids


def test_load_sensory_inventory_loads_scene_type_requirements() -> None:
    config = load_sensory_inventory()
    assert "investigation_scene" in config.scene_type_requirements
    investigation = config.scene_type_requirements["investigation_scene"]
    assert investigation.required_min >= 3
    assert "olfactory" in investigation.must_include


def test_load_sensory_inventory_extracts_banned_terms() -> None:
    config = load_sensory_inventory()
    assert "阴森" in config.banned_abstract_terms
    assert "诡异" in config.banned_abstract_terms
    assert "寂静" in config.banned_abstract_terms


def test_get_scene_requirement_returns_none_for_unknown() -> None:
    assert get_scene_requirement(None) is None
    assert get_scene_requirement("") is None
    assert get_scene_requirement("nonexistent_scene") is None


def test_render_sensory_requirement_block_includes_must_include_and_bans() -> None:
    block = render_sensory_requirement_block(scene_type="investigation_scene")
    assert "investigation_scene" in block
    assert "必带" in block
    assert "olfactory" in block
    assert "禁用抽象词" in block
    assert "阴森" in block


def test_render_sensory_requirement_block_empty_for_unknown_scene() -> None:
    assert render_sensory_requirement_block(scene_type=None) == ""
    assert render_sensory_requirement_block(scene_type="unknown") == ""

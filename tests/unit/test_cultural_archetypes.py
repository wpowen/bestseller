from __future__ import annotations

import pytest

from bestseller.services.cultural_archetypes import (
    DEFAULT_ARCHETYPE_DIR,
    load_cultural_archetype,
    load_cultural_archetype_for_category,
)

pytestmark = pytest.mark.unit


def test_load_cultural_archetype_preset() -> None:
    module = load_cultural_archetype("classical_chinese")
    assert len(module.palette) >= 8
    assert module.aesthetic_zeitgeist


def test_all_required_archetype_presets_validate() -> None:
    preset_ids = sorted(path.stem for path in DEFAULT_ARCHETYPE_DIR.glob("*.yaml"))
    assert len(preset_ids) >= 5
    for preset_id in preset_ids:
        assert load_cultural_archetype(preset_id).palette


def test_load_cultural_archetype_for_category() -> None:
    module = load_cultural_archetype_for_category("urban-contemporary")
    assert module is not None
    assert any(item.category == "vehicle" for item in module.palette)

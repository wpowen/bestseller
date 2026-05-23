from __future__ import annotations

import pytest

from bestseller.domain.cultural_texture import CulturalTextureModule, MaterialPaletteItem
from bestseller.services.cultural_texture_density_gate import (
    pick_palette_items_for_chapter,
    scan_cultural_texture_density,
)

pytestmark = pytest.mark.unit


def _module() -> CulturalTextureModule:
    cats = ["food", "clothing", "tool", "ornament", "music", "vehicle", "food", "tool"]
    return CulturalTextureModule(
        palette=[
            MaterialPaletteItem(
                category=cat,  # type: ignore[arg-type]
                name=f"物件{i}",
                sensory_hook=f"物件{i}的气味",
                class_signal="市井",
            )
            for i, cat in enumerate(cats)
        ],
        daily_rituals=["晨起净手", "入门称名"],
        taboo_behaviors=["老东西"],
        aesthetic_zeitgeist="清冷克制。",
    )


def test_gate_flags_missing_palette_landing() -> None:
    report = scan_cultural_texture_density(
        _module(),
        chapter_text="他走进空荡大厅,没有任何可触摸的生活物件。",
        chapter_no=3,
        recent_missing_chapters=[1, 2],
    )
    codes = {finding.code for finding in report.findings}
    assert "missing_palette_landing" in codes
    assert "palette_gap_streak" in codes


def test_gate_allows_explicit_subversion() -> None:
    report = scan_cultural_texture_density(
        _module(),
        chapter_text="他故意抹去所有旧物痕迹。",
        chapter_no=3,
        is_palette_subversion=True,
    )
    assert not report.findings


def test_rotation_avoids_repetition() -> None:
    picks = [
        pick_palette_items_for_chapter(_module(), chapter_no=i, count=1)[0].name
        for i in range(1, 6)
    ]
    assert len(set(picks)) == 5

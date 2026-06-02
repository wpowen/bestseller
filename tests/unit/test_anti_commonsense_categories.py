from __future__ import annotations

import pytest

from bestseller.services.anti_commonsense_mechanisms import list_mechanisms


def test_every_category_has_at_least_six_mechanisms() -> None:
    counts: dict[str, int] = {}
    for mechanism in list_mechanisms():
        counts[mechanism.category] = counts.get(mechanism.category, 0) + 1
    assert len(counts) >= 5
    for category, count in counts.items():
        assert count >= 6, f"Category {category!r} only has {count} mechanisms"


def test_five_category_taxonomy_aligned_with_market_segments() -> None:
    counts: dict[str, int] = {}
    for mechanism in list_mechanisms():
        counts[mechanism.category] = counts.get(mechanism.category, 0) + 1
    expected = {"progression", "urban_work", "emotion_villain", "xianxia_cross", "mystery_rule"}
    assert expected.issubset(counts.keys())


def test_12_bundle_batch_diversifies_mechanism_keys() -> None:
    """Across a 12-bundle generation, mechanism_key diversity is high (≥8 unique)."""
    from bestseller.services.concept_lab import build_concept_lab_catalog

    catalog = build_concept_lab_catalog("apocalypse-supply", count=12)
    keys = {bundle.hook_spec["mechanism_key"] for bundle in catalog.bundles}
    assert len(keys) >= 8, f"Expected ≥8 unique mechanisms in 12 bundles, got {len(keys)}"
    # And the categories span ≥3 of the 5.
    categories = {
        next(m.category for m in list_mechanisms() if m.key == b.hook_spec["mechanism_key"])
        for b in catalog.bundles
    }
    assert len(categories) >= 3

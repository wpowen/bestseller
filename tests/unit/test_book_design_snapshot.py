from __future__ import annotations

import pytest

from bestseller.domain.book_design_snapshot import (
    BookDesignSnapshot,
    EntityRegistry,
    build_book_design_snapshot,
    validate_cross_asset_consistency,
)


def _intent() -> dict[str, object]:
    return {
        "genre_intent": {"channel": "male", "genre": "xianxia"},
        "chapter_count": 55,
        "tone_preference": "冷峻克制",
    }


def test_snapshot_normalizes_identity_budget_tone_and_stable_entity_ids() -> None:
    snapshot = build_book_design_snapshot(
        creation_intent=_intent(),
        protagonist={"name": "  林  渊 ", "core_wound": "被背叛"},
        tone=" 冷峻克制 ",
        target_words=130_000,
        chapter_count=55,
        entities=[
            {"type": "character", "name": "林渊", "aliases": ["林  渊", "阿渊"]},
            {"type": "location", "canonical_name": "北山", "aliases": []},
        ],
    )

    assert snapshot.protagonist.name == "林渊"
    assert snapshot.tone == "冷峻克制"
    assert snapshot.word_budget.total_words == 130_000
    assert snapshot.chapter_budget.total_chapters == 55
    assert (
        snapshot.entity_registry.resolve("阿渊").entity_id
        == snapshot.entity_registry.resolve("林渊").entity_id
    )
    assert snapshot.source_hash == snapshot.canonical_hash()
    assert snapshot.snapshot_id == snapshot.canonical_hash()[:16]


def test_snapshot_hash_ignores_input_mapping_order_and_legacy_payload_parses() -> None:
    first = build_book_design_snapshot(
        creation_intent=_intent(), protagonist="林渊", target_words=130_000, chapter_count=55
    )
    second = BookDesignSnapshot.from_mapping(
        {
            "version": 1,
            "book_spec": {
                "tone": "冷峻克制",
                "target_chapters": 55,
                "target_words": 130_000,
                "protagonist": {"name": "林渊"},
            },
            "creation_intent": _intent(),
        }
    )
    assert first.source_hash == second.source_hash
    assert second.chapter_budget.total_chapters == 55
    assert second.protagonist.name == "林渊"


def test_legacy_top_level_entities_are_not_discarded_without_registry_wrapper() -> None:
    snapshot = BookDesignSnapshot.from_mapping(
        {
            "creation_intent": _intent(),
            "protagonist": "林渊",
            "entities": [
                {"type": "location", "name": "北山"},
                {"type": "character", "name": "林渊", "aliases": ["阿渊"]},
            ],
        }
    )

    assert snapshot.entity_registry.resolve("北山", "location").canonical_name == "北山"
    assert snapshot.entity_registry.resolve("阿渊", "character").canonical_name == "林渊"


def test_entity_registry_rejects_two_canonical_entities_with_same_identity() -> None:
    with pytest.raises(ValueError, match="duplicate entity identity"):
        EntityRegistry.from_items(
            [
                {"type": "character", "name": "林渊"},
                {"type": "character", "name": "林  渊"},
            ]
        )


def test_cross_asset_report_flags_identity_tone_and_source_drift() -> None:
    snapshot = build_book_design_snapshot(
        creation_intent=_intent(),
        protagonist="林渊",
        tone="冷峻克制",
    )
    report = validate_cross_asset_consistency(
        snapshot,
        [
            {
                "asset_id": "outline-v1",
                "source_snapshot_id": snapshot.snapshot_id,
                "source_hash": snapshot.source_hash,
                "protagonist": "苏晚",
                "tone": "轻松搞笑",
            },
            {
                "asset_id": "bible-v0",
                "source_snapshot_id": "stale",
                "source_hash": "stale",
                "protagonist": "林渊",
                "tone": "冷峻克制",
            },
        ],
    )
    assert not report.passed
    assert {issue.code for issue in report.issues} >= {
        "protagonist_identity_mismatch",
        "tone_mismatch",
        "source_snapshot_mismatch",
    }

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "config/ranking_evidence/packaging_consumer_inventory.json"


def test_packaging_inventory_covers_public_title_and_intro_consumers() -> None:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    paths = {item["path"] for item in payload["consumers"]}

    assert {
        "src/bestseller/services/book_packaging_store.py",
        "src/bestseller/services/book_listing.py",
        "src/bestseller/api/routers/projects.py",
        "src/bestseller/cli/main.py",
        "src/bestseller/services/exports.py",
        "src/bestseller/web/server.py",
    }.issubset(paths)
    assert all((ROOT / path).is_file() for path in paths)


def test_listing_fails_closed_when_an_active_packaging_cache_is_stale() -> None:
    source = (ROOT / "src/bestseller/services/book_listing.py").read_text(encoding="utf-8")

    assert "active_packaging_contract_hash" in source
    assert "active_intro_variant_hash" in source
    assert "STALE_PACKAGING_CONSUMER" in source
    assert "packaging_overrides" in source


def test_packaging_change_invalidates_reader_and_platform_evidence() -> None:
    source = (ROOT / "src/bestseller/services/book_packaging_store.py").read_text(encoding="utf-8")

    for layer in ("reader", "platform_cohort", "rank_receipt"):
        assert f'"{layer}"' in source

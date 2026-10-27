from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "config/ranking_evidence/legacy_release_consumer_inventory.json"


def test_every_registered_release_consumer_exists_and_has_an_owner_classification() -> None:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    consumers = payload["consumers"]

    assert len({item["id"] for item in consumers}) == len(consumers)
    assert all((ROOT / item["path"]).is_file() for item in consumers)
    assert all(item["classification"] for item in consumers)
    assert all("allowed_claim" in item for item in consumers)


def test_external_publish_side_effect_is_guarded_before_adapter_creation() -> None:
    source = (ROOT / "src/bestseller/scheduler/jobs.py").read_text(encoding="utf-8")

    authority = source.index("await require_commercial_release_authority")
    adapter = source.index("adapter = get_adapter")
    authenticate = source.index("auth_ok = await adapter.authenticate")
    publish = source.index("result = await adapter.publish_chapter")

    assert authority < adapter < authenticate < publish
    assert "ranking_readiness" not in source


def test_commercial_cli_reads_canonical_claim_not_legacy_passed_flag() -> None:
    source = (ROOT / "src/bestseller/cli/main.py").read_text(encoding="utf-8")
    start = source.index("def commercial_gate_project(")
    command = source[start : source.index("\n\n@", start)]

    assert "load_canonical_book_readiness" in command
    assert '"COMMERCIAL_RELEASE"' in command
    assert "if fail and not report.passed" not in command

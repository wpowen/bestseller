from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.pipelines import _apply_retention_retry_budget
from bestseller.services.quality_gates_config import OriginalityEngineConfig

pytestmark = pytest.mark.unit


def test_retention_retry_budget_ignores_non_retention_codes() -> None:
    chapter = SimpleNamespace(metadata_json={}, status="complete", production_state="blocked")
    cfg = OriginalityEngineConfig()

    exhausted = _apply_retention_retry_budget(chapter, ("BLOCK_LOW",), cfg)

    assert exhausted is False
    assert "retention_retry_count" not in chapter.metadata_json


def test_retention_retry_budget_increments_and_records_codes() -> None:
    chapter = SimpleNamespace(metadata_json={}, status="complete", production_state="blocked")
    cfg = OriginalityEngineConfig(retention_max_retries=5, retention_escalate_after=3)

    exhausted = _apply_retention_retry_budget(
        chapter,
        ("HOOK_ECHO_MISSING",),
        cfg,
    )

    assert exhausted is False
    assert chapter.metadata_json["retention_retry_count"] == 1
    assert chapter.metadata_json["retention_retry_last_block_codes"] == [
        "HOOK_ECHO_MISSING"
    ]
    assert "retention_retry_strict_prompt" not in chapter.metadata_json


def test_retention_retry_budget_adds_strict_prompt_on_escalation() -> None:
    chapter = SimpleNamespace(
        metadata_json={"retention_retry_count": 2},
        status="complete",
        production_state="blocked",
    )
    cfg = OriginalityEngineConfig(retention_max_retries=5, retention_escalate_after=3)

    exhausted = _apply_retention_retry_budget(
        chapter,
        ("SIGNATURE_SCENE_MISSING",),
        cfg,
    )

    assert exhausted is False
    assert chapter.metadata_json["retention_retry_count"] == 3
    assert "第 3 次" in chapter.metadata_json["retention_retry_strict_prompt"]
    assert "SIGNATURE_SCENE_MISSING" in chapter.metadata_json["retention_retry_strict_prompt"]


def test_retention_retry_budget_exhausts_after_max_retries() -> None:
    chapter = SimpleNamespace(
        metadata_json={"retention_retry_count": 5},
        status="complete",
        production_state="blocked",
    )
    cfg = OriginalityEngineConfig(retention_max_retries=5, retention_escalate_after=3)

    exhausted = _apply_retention_retry_budget(
        chapter,
        ("CAST_VIOLATION",),
        cfg,
    )

    assert exhausted is True
    assert chapter.status == "revision"
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["requires_machine_repair"] is True
    assert chapter.metadata_json["requires_human_review"] is False
    assert chapter.metadata_json["retention_auto_repair_exhausted"] is True
    assert chapter.metadata_json["retention_machine_repair_required"] is True

"""Unwired prototype checks; actual saved text is read locally, no PG/provider."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from bestseller.services import fanqie_short_semantic_selection_adapter as adapter


BASE = Path(__file__).resolve().parents[2] / "reports/project-reliability-review-2026-10-24/implementation"


def original():
    return json.loads((BASE / "original-four-whole-v5-protocol-execution.json").read_text())["result"]


def test_actual_v5_two_valid_body_and_valid_title_rejection_remain_immutable():
    report = original()
    before = deepcopy(report)
    audit = adapter.audit_v5_rows(report)
    assert report == before
    assert {r["id"] for r in audit["frozen_body_rows"]} == {
        "weak_hook@opening_30_percent", "opening_pressure_missing@opening_300"}
    assert len(audit["invalid_rows"]) == 6
    assert [r["resolved_row"]["verdict"] for r in audit["frozen_title_rows"]] == ["confirmed"]
    diagnosed = json.loads((BASE / "original-four-whole-v5-protocol-diagnosis.json").read_text())
    expected = {r["id"]: r for r in diagnosed["individually_valid_rows"]}
    for row in audit["frozen_body_rows"] + audit["frozen_title_rows"]:
        assert row["resolved_row"] == expected[row["id"]]
        assert row["raw_row"]["verdict"] == row["resolved_row"]["verdict"]
        assert row["raw_row"]["reason"] == row["resolved_row"]["reason"]
    assert "passed" not in audit and "ready_for_upload" not in audit


@pytest.mark.parametrize("fault", ["raw", "payload", "implementation", "batch_error", "stored_grade", "not_called"])
def test_tampered_parent_not_reusable(fault):
    report = original()
    if fault == "raw": report["raw_response"] += " "
    elif fault == "payload": report["request_payload"]["full_text"] += "改"
    elif fault == "implementation": report["request_payload"]["implementation_source_hashes"]["native_publication_title.py"] = "0" * 64
    elif fault == "batch_error": report["error"] = "other"
    elif fault == "stored_grade": report["reviews"][0]["verdict"] = "not_supported"
    elif fault == "not_called": report["called"] = False
    with pytest.raises(ValueError):
        adapter.audit_v5_rows(report)


def test_historical_archive_is_exact_original_route_not_current_title_authority():
    frozen = adapter.frozen_v5_validator()
    assert frozen.implementation_source_hashes() == adapter.V5_SOURCES
    assert frozen.PROMPT_VERSION == "stage.5"
    # Only the historical route is loaded. No current approval mapping or new
    # title acceptance is inferred from its old native_publication_title SHA.
    assert "whole_semantic_history_v5" in frozen.__file__
    assert "reports" not in frozen.__file__

"""L1 unit tests for design-quality report normalization."""

from __future__ import annotations

import pytest

from bestseller.services.design_quality import build_design_quality_reports


@pytest.mark.unit
def test_noop_on_empty_is_stable() -> None:
    a = build_design_quality_reports(None)
    b = build_design_quality_reports({})
    assert a == b
    assert a["reports"] == []
    assert a["headline"] is None
    assert a["capability_snapshot"] is None
    assert a["report_count"] == 0


@pytest.mark.unit
def test_prewrite_readiness_drives_headline_and_snapshot() -> None:
    meta = {
        "prewrite_readiness_report": {
            "score": 95,
            "passed": True,
            "warnings": [{"code": "x", "message": "m"}],
            "blocking_findings": [],
            "capability_snapshot": {"has_world_spec": True, "story_design_kernel": False},
        }
    }
    out = build_design_quality_reports(meta)
    assert out["headline"]["score_pct"] == 95
    assert out["headline"]["passed"] is True
    assert out["headline"]["warning_count"] == 1
    assert out["capability_snapshot"]["has_world_spec"] is True
    r = out["reports"][0]
    assert r["verdict_class"] == "warn"  # passed but has a warning


@pytest.mark.unit
def test_blocking_findings_make_verdict_fail() -> None:
    meta = {
        "opening_quality_planning_gate_report": {
            "passed": False,
            "blocking_findings": [{"code": "HOOK_MISSING"}],
        }
    }
    out = build_design_quality_reports(meta)
    r = out["reports"][0]
    assert r["verdict_class"] == "fail"
    assert r["blocking_count"] == 1


@pytest.mark.unit
def test_score_on_0_to_1_scale_is_normalized_to_pct() -> None:
    meta = {"commercial_planning_llm_judge": {"score": 0.82, "passed": True}}
    out = build_design_quality_reports(meta)
    assert out["reports"][0]["score_pct"] == 82.0


@pytest.mark.unit
def test_string_status_report_infers_passed() -> None:
    meta = {"commercial_planning_readiness_status": "approved"}
    out = build_design_quality_reports(meta)
    r = out["reports"][0]
    assert r["kind"] == "status"
    assert r["status"] == "approved"
    assert r["passed"] is True
    assert r["verdict_class"] == "ok"


@pytest.mark.unit
def test_absent_and_empty_reports_skipped() -> None:
    meta = {
        "prewrite_readiness_report": {"score": 80, "passed": True},
        "qimao_planning_gate_report": {},  # empty → skipped
        "identity_manifest_status": "",  # empty → skipped
    }
    out = build_design_quality_reports(meta)
    keys = [r["key"] for r in out["reports"]]
    assert keys == ["prewrite_readiness_report"]


@pytest.mark.unit
def test_reports_ordered_by_spec() -> None:
    meta = {
        "identity_manifest_status": "ok",
        "prewrite_readiness_report": {"score": 90, "passed": True},
    }
    out = build_design_quality_reports(meta)
    # prewrite_readiness_report is first in _REPORT_SPECS, identity last.
    assert out["reports"][0]["key"] == "prewrite_readiness_report"
    assert out["reports"][-1]["key"] == "identity_manifest_status"

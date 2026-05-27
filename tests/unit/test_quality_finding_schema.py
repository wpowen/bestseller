from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.quality_finding_schema import (
    quality_finding_from_retention,
    quality_finding_from_write_safety,
    quality_findings_from_report_json,
)
from bestseller.services.write_safety_gate import WriteSafetyFinding

pytestmark = pytest.mark.unit


def test_write_safety_finding_normalizes_with_payload_evidence() -> None:
    raw = WriteSafetyFinding(
        source="post_assembly_opening_diversity_gate",
        code="CHAPTER_OPENING_REPETITION",
        severity="critical",
        message="重写第一段",
        evidence="这一刻，所有线索都被压回同一条账路上。",
        payload={"source_chapter": 70, "opening": "这一刻，所有线索都被压回同一条账路上。"},
    )

    finding = quality_finding_from_write_safety(raw, chapter_number=75)

    assert finding.code == "CHAPTER_OPENING_REPETITION"
    assert finding.chapter_number == 75
    assert finding.repair_scope == "paragraph"
    assert finding.evidence["source_chapter"] == 70
    assert finding.blocking is True


def test_retention_finding_normalizes_without_losing_evidence() -> None:
    raw = SimpleNamespace(
        code="HOOK_ECHO_MISSING",
        severity="critical",
        detail="missing hook echo",
        coverage=0.1,
        exposition_ratio=None,
        evidence={"missed_tokens": ["303室"]},
    )

    finding = quality_finding_from_retention(raw, chapter_number=2)

    assert finding.code == "HOOK_ECHO_MISSING"
    assert finding.evidence["missed_tokens"] == ["303室"]
    assert finding.evidence["coverage"] == 0.1
    assert finding.blocking is True


def test_quality_report_json_adds_blocking_code_without_violation_row() -> None:
    findings = quality_findings_from_report_json(
        {"violations": [], "blocking_codes": ["BLOCK_LOW"]},
        chapter_number=10,
    )

    assert len(findings) == 1
    assert findings[0].code == "BLOCK_LOW"
    assert findings[0].blocking is True

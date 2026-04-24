from __future__ import annotations

import pytest

from bestseller.domain.contradiction import ContradictionCheckResult, ContradictionViolation
from bestseller.services.identity_guard import IdentityViolation
from bestseller.services.reader_power import GoldenThreeChapterSignal, GoldenThreeReport
from bestseller.services.write_safety_gate import (
    WriteSafetyBlockError,
    assert_no_write_safety_blocks,
    findings_from_contradiction_result,
    findings_from_golden_three_report,
    findings_from_identity_violations,
    serialize_write_safety_findings,
)

pytestmark = pytest.mark.unit


def _build_signal(
    *,
    chapter_number: int,
    issue_codes: tuple[str, ...] = (),
) -> GoldenThreeChapterSignal:
    return GoldenThreeChapterSignal(
        chapter_number=chapter_number,
        assigned_hype_type=None,
        classified_hype_type=None,
        classified_hype_confidence=0.0,
        tail_hype_type=None,
        tail_hype_confidence=0.0,
        ending_sentence="",
        has_ending_hook=False,
        signal_codes=(),
        issue_codes=issue_codes,
    )


def _build_report(
    *,
    enabled: bool = True,
    chapters_checked: int = 3,
    strong_hype_chapters: int = 0,
    ending_hook_chapters: int = 0,
    issue_codes: tuple[str, ...] = (),
    chapter_signals: tuple[GoldenThreeChapterSignal, ...] | None = None,
) -> GoldenThreeReport:
    return GoldenThreeReport(
        enabled=enabled,
        chapters_checked=chapters_checked,
        strong_hype_chapters=strong_hype_chapters,
        ending_hook_chapters=ending_hook_chapters,
        chapter_signals=chapter_signals or (),
        issue_codes=issue_codes,
    )


def test_contradiction_violations_become_blocking_findings() -> None:
    result = ContradictionCheckResult(
        passed=False,
        violations=[
            ContradictionViolation(
                check_type="timeline_order",
                severity="error",
                message="第3章事件不能发生在第2章之前",
                evidence="chapter=3 scene=1",
            )
        ],
        warnings=[],
        checks_run=1,
    )

    findings = findings_from_contradiction_result(result)

    assert findings[0].source == "contradiction"
    assert findings[0].code == "timeline_order"
    assert serialize_write_safety_findings(findings)[0]["evidence"] == "chapter=3 scene=1"
    with pytest.raises(WriteSafetyBlockError) as exc_info:
        assert_no_write_safety_blocks(
            findings,
            project_slug="blood-twins",
            chapter_number=3,
            scene_number=1,
        )
    assert "write-safety gate" in str(exc_info.value)
    assert exc_info.value.findings == findings


def test_identity_gate_respects_severity_filter() -> None:
    violations = [
        IdentityViolation(
            character_name="沈砚",
            violation_type="pronoun_mismatch",
            expected="他",
            found="她",
            severity="major",
            evidence="沈砚抬头，她握紧刀柄",
        ),
        IdentityViolation(
            character_name="李渡",
            violation_type="alias_variant",
            expected="李渡",
            found="阿渡",
            severity="minor",
            evidence="阿渡回头",
        ),
    ]

    findings = findings_from_identity_violations(
        violations,
        blocked_severities=("critical", "major"),
    )

    assert len(findings) == 1
    assert findings[0].source == "identity"
    assert findings[0].payload["character_name"] == "沈砚"


def test_write_safety_gate_can_be_disabled_per_source() -> None:
    result = ContradictionCheckResult(
        passed=False,
        violations=[
            ContradictionViolation(
                check_type="knowledge_leak",
                severity="error",
                message="角色提前知道秘密",
            )
        ],
    )

    assert findings_from_contradiction_result(result, block_on_violation=False) == ()
    assert (
        findings_from_identity_violations(
            [
                IdentityViolation(
                    character_name="沈砚",
                    violation_type="dead_alive",
                    expected="dead",
                    found="speaks",
                )
            ],
            block_on_violation=False,
        )
        == ()
    )


# ---------------------------------------------------------------------------
# Golden-3 report → findings
# ---------------------------------------------------------------------------


def test_golden_three_none_report_returns_empty() -> None:
    assert findings_from_golden_three_report(None) == ()


def test_golden_three_disabled_report_returns_empty() -> None:
    # When the analyzer is disabled (flag off) it emits enabled=False
    # and we must not surface findings even if issue_codes look bad.
    report = _build_report(
        enabled=False,
        issue_codes=("GOLDEN_THREE_LOW_HYPE",),
    )
    assert findings_from_golden_three_report(report) == ()


def test_golden_three_block_on_violation_false_returns_empty() -> None:
    report = _build_report(issue_codes=("GOLDEN_THREE_LOW_HYPE",))
    assert findings_from_golden_three_report(report, block_on_violation=False) == ()


def test_golden_three_low_hype_is_critical_and_blocks_by_default() -> None:
    report = _build_report(
        strong_hype_chapters=0,
        ending_hook_chapters=2,
        issue_codes=("GOLDEN_THREE_LOW_HYPE",),
        chapter_signals=(
            _build_signal(chapter_number=1, issue_codes=("CHAPTER_LACKS_HYPE",)),
            _build_signal(chapter_number=2, issue_codes=("CHAPTER_LACKS_HYPE",)),
            _build_signal(chapter_number=3),
        ),
    )

    findings = findings_from_golden_three_report(report)

    assert len(findings) == 1
    assert findings[0].source == "golden_three"
    assert findings[0].code == "GOLDEN_THREE_LOW_HYPE"
    assert findings[0].severity == "critical"
    assert "0/3" in findings[0].message  # strong_hype/chapters_checked
    assert findings[0].payload["chapters_checked"] == 3
    assert findings[0].payload["strong_hype_chapters"] == 0
    assert findings[0].payload["ending_hook_chapters"] == 2


def test_golden_three_weak_ending_hooks_is_major_and_filtered_by_default() -> None:
    # Default blocked_severities=("critical",) — major should not block.
    report = _build_report(
        strong_hype_chapters=3,
        ending_hook_chapters=0,
        issue_codes=("GOLDEN_THREE_WEAK_ENDING_HOOKS",),
    )
    assert findings_from_golden_three_report(report) == ()


def test_golden_three_includes_majors_when_configured() -> None:
    report = _build_report(
        strong_hype_chapters=3,
        ending_hook_chapters=0,
        issue_codes=(
            "GOLDEN_THREE_WEAK_ENDING_HOOKS",
            "GOLDEN_THREE_WEAK_OPEN_CONFLICT",
        ),
    )

    findings = findings_from_golden_three_report(
        report,
        blocked_severities=("critical", "major"),
    )

    assert len(findings) == 2
    codes = {finding.code for finding in findings}
    assert codes == {
        "GOLDEN_THREE_WEAK_ENDING_HOOKS",
        "GOLDEN_THREE_WEAK_OPEN_CONFLICT",
    }
    for finding in findings:
        assert finding.severity == "major"


def test_golden_three_incomplete_is_minor_and_suppressed_by_default() -> None:
    report = _build_report(
        chapters_checked=1,
        issue_codes=("GOLDEN_THREE_INCOMPLETE",),
    )
    assert findings_from_golden_three_report(report) == ()

    # Only surfaces when "minor" is explicitly added to blocked list.
    findings = findings_from_golden_three_report(
        report,
        blocked_severities=("critical", "major", "minor"),
    )
    assert len(findings) == 1
    assert findings[0].code == "GOLDEN_THREE_INCOMPLETE"
    assert findings[0].severity == "minor"


def test_golden_three_evidence_summarizes_per_chapter() -> None:
    report = _build_report(
        issue_codes=("GOLDEN_THREE_LOW_HYPE",),
        chapter_signals=(
            _build_signal(
                chapter_number=1,
                issue_codes=("CHAPTER_LACKS_HYPE",),
            ),
            _build_signal(
                chapter_number=2,
                issue_codes=("CHAPTER_LACKS_ENDING_HOOK",),
            ),
            _build_signal(chapter_number=3),  # no issues → skipped
        ),
    )

    findings = findings_from_golden_three_report(report)

    assert len(findings) == 1
    # Evidence lists per-chapter issue codes, skipping chapter 3.
    assert "ch1:CHAPTER_LACKS_HYPE" in findings[0].evidence
    assert "ch2:CHAPTER_LACKS_ENDING_HOOK" in findings[0].evidence
    assert "ch3" not in findings[0].evidence


def test_golden_three_empty_blocked_severities_returns_empty() -> None:
    report = _build_report(issue_codes=("GOLDEN_THREE_LOW_HYPE",))
    assert findings_from_golden_three_report(report, blocked_severities=()) == ()


def test_golden_three_feeds_serialize_and_gate_block() -> None:
    report = _build_report(
        strong_hype_chapters=0,
        issue_codes=("GOLDEN_THREE_LOW_HYPE",),
    )
    findings = findings_from_golden_three_report(report)

    serialized = serialize_write_safety_findings(findings)
    assert serialized[0]["source"] == "golden_three"
    assert serialized[0]["severity"] == "critical"
    assert serialized[0]["payload"]["chapters_checked"] == 3

    with pytest.raises(WriteSafetyBlockError):
        assert_no_write_safety_blocks(
            findings,
            project_slug="blood-twins",
            chapter_number=4,
            scene_number=1,
        )


def test_golden_three_unknown_code_defaults_to_minor() -> None:
    # Defensive: unknown issue_code maps to minor — suppressed by default.
    report = _build_report(issue_codes=("GOLDEN_THREE_FUTURE_RULE",))
    assert findings_from_golden_three_report(report) == ()
    findings = findings_from_golden_three_report(
        report,
        blocked_severities=("critical", "major", "minor"),
    )
    assert len(findings) == 1
    assert findings[0].severity == "minor"

"""Unit tests for L6 write-gate mode resolution.

The gate is the only place we translate L4/L5 violation codes into a
write/no-write decision. The tests lock in the Phase 1 policy (§9 of the
architecture plan) plus the fallback behavior for unknown codes.
"""

from __future__ import annotations

import pytest

from bestseller.services.output_validator import QualityReport, Violation
from bestseller.services.write_gate import (
    ChapterBlocked,
    DEFAULT_GATE_CONFIG,
    GateConfig,
    assert_writable,
    filter_blocking,
    has_audit_only_findings,
    resolve_mode,
)

pytestmark = pytest.mark.unit


def _violation(code: str, severity: str = "block") -> Violation:
    return Violation(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        location="chapter",
        detail="test",
        prompt_feedback="test feedback",
    )


# ---------------------------------------------------------------------------
# resolve_mode
# ---------------------------------------------------------------------------


class TestResolveMode:
    def test_cjk_leak_maps_to_block(self) -> None:
        assert resolve_mode("LANG_LEAK_CJK_IN_EN") == "block"

    def test_length_under_maps_to_block(self) -> None:
        assert resolve_mode("LENGTH_UNDER") == "block"

    def test_naming_out_of_pool_is_audit_only(self) -> None:
        assert resolve_mode("NAMING_OUT_OF_POOL") == "audit_only"

    def test_unknown_code_falls_back_to_default(self) -> None:
        assert resolve_mode("NEW_UNKNOWN_CODE") == "audit_only"

    def test_override_config_can_promote_audit_only_to_block(self) -> None:
        cfg = GateConfig(
            mode_by_violation={"NAMING_OUT_OF_POOL": "block"},
            default="audit_only",
        )
        assert resolve_mode("NAMING_OUT_OF_POOL", cfg) == "block"

    def test_override_config_can_demote_block_to_audit(self) -> None:
        cfg = GateConfig(
            mode_by_violation={"LENGTH_UNDER": "audit_only"},
            default="block",
        )
        assert resolve_mode("LENGTH_UNDER", cfg) == "audit_only"


# ---------------------------------------------------------------------------
# filter_blocking
# ---------------------------------------------------------------------------


class TestFilterBlocking:
    def test_audit_only_findings_stripped(self) -> None:
        report = QualityReport(
            violations=(
                _violation("LANG_LEAK_CJK_IN_EN"),
                _violation("NAMING_OUT_OF_POOL"),
            )
        )
        blocking = filter_blocking(report)
        assert [v.code for v in blocking] == ["LANG_LEAK_CJK_IN_EN"]

    def test_all_audit_only_yields_empty(self) -> None:
        report = QualityReport(
            violations=(
                _violation("NAMING_OUT_OF_POOL"),
                _violation("POV_DRIFT"),
            )
        )
        assert filter_blocking(report) == ()


# ---------------------------------------------------------------------------
# assert_writable
# ---------------------------------------------------------------------------


class TestAssertWritable:
    def test_clean_report_is_writable(self) -> None:
        assert_writable(QualityReport(violations=()))

    def test_audit_only_report_is_writable(self) -> None:
        report = QualityReport(
            violations=(_violation("CLIFFHANGER_REPEAT"),)
        )
        assert_writable(report, chapter_no=5)

    def test_block_violation_raises(self) -> None:
        report = QualityReport(violations=(_violation("LENGTH_OVER"),))
        with pytest.raises(ChapterBlocked) as excinfo:
            assert_writable(report, chapter_no=7)
        assert excinfo.value.chapter_no == 7
        assert [v.code for v in excinfo.value.blocking_violations] == ["LENGTH_OVER"]

    def test_mixed_report_blocks_on_block_code(self) -> None:
        report = QualityReport(
            violations=(
                _violation("POV_DRIFT"),
                _violation("DIALOG_UNPAIRED"),
            )
        )
        with pytest.raises(ChapterBlocked) as excinfo:
            assert_writable(report)
        # Only the DIALOG_UNPAIRED survives the filter.
        blocking_codes = {v.code for v in excinfo.value.blocking_violations}
        assert blocking_codes == {"DIALOG_UNPAIRED"}


# ---------------------------------------------------------------------------
# has_audit_only_findings
# ---------------------------------------------------------------------------


class TestHasAuditOnlyFindings:
    def test_none_when_empty(self) -> None:
        assert not has_audit_only_findings(QualityReport(violations=()))

    def test_true_when_only_audit_findings(self) -> None:
        report = QualityReport(violations=(_violation("POV_DRIFT"),))
        assert has_audit_only_findings(report)

    def test_true_when_mixed(self) -> None:
        report = QualityReport(
            violations=(
                _violation("LANG_LEAK_CJK_IN_EN"),
                _violation("POV_DRIFT"),
            )
        )
        assert has_audit_only_findings(report)


# ---------------------------------------------------------------------------
# Policy regression: freeze the Phase 1 defaults
# ---------------------------------------------------------------------------


class TestDefaultPolicyLockIn:
    """Prevent accidental downgrades of the high-confidence violations.

    The plan's §9 Decision 2 commits to block-on-sight for these codes;
    regressing one to audit_only would re-introduce the failure mode that
    motivated Phase 1 in the first place.
    """

    @pytest.mark.parametrize(
        "code",
        [
            "LANG_LEAK_CJK_IN_EN",
            "LANG_LEAK_LATIN_IN_ZH",
            "LENGTH_UNDER",
            "LENGTH_OVER",
            "DIALOG_UNPAIRED",
            "CHAPTER_GAP",
            "QUIRK_SLOT_MISSING",
            "ANTAGONIST_MOTIVE_OVERLAP",
            "WORLD_TAXONOMY_BOILERPLATE",
            "NAMING_POOL_UNDERSIZED",
        ],
    )
    def test_high_confidence_code_is_block(self, code: str) -> None:
        assert resolve_mode(code, DEFAULT_GATE_CONFIG) == "block"

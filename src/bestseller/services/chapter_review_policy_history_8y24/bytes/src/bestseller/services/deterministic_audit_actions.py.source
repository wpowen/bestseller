"""Resolve deterministic audit actions without changing raw evidence or grades."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from bestseller.services.output_validator import QualityReport, Violation
from bestseller.services.write_gate import GateConfig, filter_blocking

# The existing rewrite classifier's factual/body-integrity exceptions also
# apply to raw audit reports. Length direction is resolved by its caller when
# available; the original LENGTH_OUT_OF_BAND finding is still a hard failure.
_HARD_CODES = frozenset(
    {
        "CANON_FORBIDDEN_TERM",
        "DEPRECATED_ENTITY_HIT",
        "PARAGRAPH_DUPLICATE_PARAPHRASE",
        "SCENE_CARD_PROSE_COPIED",
        "LENGTH_OUT_OF_BAND",
        "LENGTH_UNDER",
        "LENGTH_OVER",
    }
)
_CANONICAL_CODES = {"FORBIDDEN_TERM_HIT": "CANON_FORBIDDEN_TERM"}
# Complete non-hard inventory of deterministic_post_write_audit. Only these
# known craft detectors may inherit audit_only without an explicit registry row.
_KNOWN_CRAFT_CODES = frozenset(
    {
        "SIGNATURE_IMAGE_MISSING",
        "CALLBACK_OBLIGATION_MISSING",
        "OPENING_PRESSURE_THIN",
        "ENDING_HOOK_MISSING",
    }
)
_UNKNOWN_BLOCK_SEVERITIES = frozenset({"high", "critical", "block", "blocker"})


def _field(value: object, name: str, default: object = None) -> object:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def effective_deterministic_audit_blockers(
    report: object,
    *,
    settings: object,
    chapter_number: int | None,
) -> tuple[object, ...]:
    """Return original blocking findings from an object or serialized report.

    ``settings`` is the actual QualityGatesConfig (its ``l6_gate``) or the
    already-resolved GateConfig. No runtime/model settings are guessed or
    loaded here. Translated executor violations can use {"findings": values}.
    Raw ``passed`` and severity never overrule the configured action; explicit
    source/body-integrity failures retain their established hard semantics.
    Unregistered high-severity detectors fail closed until explicitly configured.
    """
    config = settings if isinstance(settings, GateConfig) else getattr(settings, "l6_gate", None)
    if not isinstance(config, GateConfig):
        raise TypeError("deterministic_audit_actions_requires_gate_config")
    if report is None:
        return ()
    findings = _field(report, "findings", ())
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        raise ValueError("deterministic_audit_findings_invalid")
    selected = []
    for finding in findings:
        raw_code = _field(finding, "code", "")
        if not isinstance(raw_code, str) or not raw_code.strip():
            raise ValueError("deterministic_audit_finding_code_invalid")
        code = _CANONICAL_CODES.get(raw_code.strip(), raw_code.strip())
        if code in _HARD_CODES:
            selected.append(finding)
            continue
        severity = str(_field(finding, "severity", "")).strip().lower()
        if (
            code not in _KNOWN_CRAFT_CODES
            and code not in config.mode_by_violation
            and severity in _UNKNOWN_BLOCK_SEVERITIES
        ):
            selected.append(finding)
            continue
        violation = Violation(
            code=code,
            severity="block" if _field(finding, "severity") == "block" else "warn",
            location="",
            detail="",
            prompt_feedback="",
        )
        if filter_blocking(QualityReport((violation,)), config, chapter_no=chapter_number):
            selected.append(finding)
    return tuple(selected)


def filter_inactive_deterministic_audit_codes(
    codes: Iterable[str],
    report: object,
    *,
    settings: object,
    chapter_number: int | None,
) -> tuple[str, ...]:
    """Filter only report-proven inactive codes from a derived flat cache view.

    Unknown/absent codes are retained. One blocking finding keeps its entire
    code active, including tiered detectors with both warn and block findings.
    Neither the caller's arrays nor the report/history are modified.
    """
    blockers = effective_deterministic_audit_blockers(
        report,
        settings=settings,
        chapter_number=chapter_number,
    )
    findings = _field(report, "findings", ()) if report is not None else ()
    known = {_field(finding, "code") for finding in findings}
    active = {_field(finding, "code") for finding in blockers}
    inactive = known - active
    return tuple(code for code in codes if code not in inactive)

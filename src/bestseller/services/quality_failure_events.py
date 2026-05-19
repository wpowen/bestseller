"""Shared quality-failure event schema and adapters.

The first integration point is the retrofit audit / autonomous repair path.
Pipeline gates can migrate to this schema incrementally without changing their
current storage format in one large refactor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


_FAILED_BOOL_FIELDS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "word_count_passed",
        "quality_levers.word_count",
        "WORD_COUNT_GATE_FAILED",
        "draft",
        "draft_generation",
        "adjust_chapter_length",
    ),
    (
        "pulse_passed",
        "quality_levers.pulse_density",
        "PULSE_DENSITY_BELOW_THRESHOLD",
        "draft",
        "chapter_plan_contract",
        "increase_attraction_density",
    ),
    (
        "banned_patterns_passed",
        "quality_levers.banned_patterns",
        "AI_VOICE_BANNED_PATTERN",
        "draft",
        "prompt_contract",
        "remove_ai_voice",
    ),
    (
        "abstract_sensory_passed",
        "quality_levers.abstract_sensory",
        "ABSTRACT_SENSORY_TERM",
        "draft",
        "prompt_contract",
        "concretize_prose",
    ),
    (
        "dumping_passed",
        "quality_levers.psychological_dumping",
        "PSYCHOLOGICAL_DUMPING",
        "draft",
        "chapter_plan_contract",
        "replace_dumping_with_action",
    ),
    (
        "emotion_label_passed",
        "quality_levers.emotion_labels",
        "EMOTION_LABEL_VIOLATION",
        "draft",
        "prompt_contract",
        "externalize_emotion",
    ),
)


@dataclass(frozen=True, slots=True)
class QualityFailureEvent:
    slug: str
    chapter_number: int | None
    stage: str
    gate_id: str
    code: str
    severity: str
    language: str | None = None
    platform: str | None = None
    source_stage: str = "unknown"
    preventable_stage: str = "unknown"
    remediation_class: str = "unknown"
    evidence_ref: str | None = None
    repair_task_id: str | None = None
    human_review_reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "chapter_number": self.chapter_number,
            "stage": self.stage,
            "gate_id": self.gate_id,
            "code": self.code,
            "severity": self.severity,
            "language": self.language,
            "platform": self.platform,
            "source_stage": self.source_stage,
            "preventable_stage": self.preventable_stage,
            "remediation_class": self.remediation_class,
            "evidence_ref": self.evidence_ref,
            "repair_task_id": self.repair_task_id,
            "human_review_reason": self.human_review_reason,
            "details": dict(self.details),
        }

    def with_repair_task_id(self, repair_task_id: str | None) -> "QualityFailureEvent":
        return QualityFailureEvent(
            slug=self.slug,
            chapter_number=self.chapter_number,
            stage=self.stage,
            gate_id=self.gate_id,
            code=self.code,
            severity=self.severity,
            language=self.language,
            platform=self.platform,
            source_stage=self.source_stage,
            preventable_stage=self.preventable_stage,
            remediation_class=self.remediation_class,
            evidence_ref=self.evidence_ref,
            repair_task_id=repair_task_id,
            human_review_reason=self.human_review_reason,
            details=self.details,
        )


def quality_failure_event_from_dict(data: Mapping[str, Any]) -> QualityFailureEvent:
    return QualityFailureEvent(
        slug=str(data.get("slug") or ""),
        chapter_number=_optional_int(data.get("chapter_number")),
        stage=str(data.get("stage") or "unknown"),
        gate_id=str(data.get("gate_id") or "unknown"),
        code=str(data.get("code") or "UNKNOWN_FAILURE"),
        severity=str(data.get("severity") or "medium"),
        language=_optional_str(data.get("language")),
        platform=_optional_str(data.get("platform")),
        source_stage=str(data.get("source_stage") or "unknown"),
        preventable_stage=str(data.get("preventable_stage") or "unknown"),
        remediation_class=str(data.get("remediation_class") or "unknown"),
        evidence_ref=_optional_str(data.get("evidence_ref")),
        repair_task_id=_optional_str(data.get("repair_task_id")),
        human_review_reason=_optional_str(data.get("human_review_reason")),
        details=dict(data.get("details") or {}),
    )


def failure_events_from_retrofit_row(
    row: Mapping[str, Any],
    *,
    slug: str | None = None,
    platform: str | None = None,
    evidence_ref: str | None = None,
    repair_task_id: str | None = None,
) -> tuple[QualityFailureEvent, ...]:
    """Convert one retrofit audit row into normalized failure events."""

    row_slug = str(slug or row.get("slug") or "")
    chapter_number = _optional_int(row.get("chapter_number"))
    severity = _severity_from_priority(str(row.get("priority") or "medium"))
    language = _optional_str(row.get("language"))
    platform_value = _optional_str(platform or row.get("platform"))
    evidence = _optional_str(evidence_ref or row.get("evidence_ref"))
    source_stage, preventable_stage, remediation_class = _source_fields(row)

    events: list[QualityFailureEvent] = []
    for field_name, gate_id, code, source, preventable, remediation in _FAILED_BOOL_FIELDS:
        if _boolish(row.get(field_name), default=True):
            continue
        events.append(
            QualityFailureEvent(
                slug=row_slug,
                chapter_number=chapter_number,
                stage="retrofit_audit",
                gate_id=gate_id,
                code=(
                    _word_count_code(row)
                    if field_name == "word_count_passed"
                    else code
                ),
                severity=severity,
                language=language,
                platform=platform_value,
                source_stage=source_stage if source_stage == "detector" else source,
                preventable_stage=(
                    preventable_stage if source_stage == "detector" else preventable
                ),
                remediation_class=(
                    remediation_class if source_stage == "detector" else remediation
                ),
                evidence_ref=evidence,
                repair_task_id=repair_task_id,
                details=_retrofit_details(row, field_name),
            )
        )

    if (
        not _boolish(row.get("rhythm_passed"), default=True)
        and _boolish(row.get("rhythm_applicable"), default=True)
    ):
        events.append(
            QualityFailureEvent(
                slug=row_slug,
                chapter_number=chapter_number,
                stage="retrofit_audit",
                gate_id="quality_levers.rhythm_engineering",
                code="RHYTHM_ANCHOR_COVERAGE_FAILED",
                severity=severity,
                language=language,
                platform=platform_value,
                source_stage="draft",
                preventable_stage="chapter_plan_contract",
                remediation_class="repair_rhythm_skeleton",
                evidence_ref=evidence,
                repair_task_id=repair_task_id,
                details=_retrofit_details(row, "rhythm_passed"),
            )
        )

    return tuple(events)


def quality_failure_events_to_dicts(
    events: tuple[QualityFailureEvent, ...] | list[QualityFailureEvent],
) -> list[dict[str, Any]]:
    return [event.to_dict() for event in events]


def _source_fields(row: Mapping[str, Any]) -> tuple[str, str, str]:
    audit_validity = str(row.get("audit_validity") or "").lower()
    if audit_validity.startswith("invalid"):
        return "detector", "metadata_validation", "fix_detector_not_chapter"
    return "draft", "draft_generation", "repair_chapter"


def _word_count_code(row: Mapping[str, Any]) -> str:
    reason = str(row.get("word_count_reason") or "").lower()
    if "under" in reason:
        return "WORD_COUNT_UNDERFLOW"
    if "over" in reason:
        return "WORD_COUNT_OVERFLOW"
    return "WORD_COUNT_GATE_FAILED"


def _retrofit_details(row: Mapping[str, Any], field: str) -> dict[str, Any]:
    keys_by_field = {
        "word_count_passed": (
            "char_count",
            "count_unit",
            "word_count_reason",
            "target_word_count",
        ),
        "pulse_passed": ("pulse_count", "pulse_density", "pulse_threshold"),
        "banned_patterns_passed": (
            "banned_pattern_hits",
            "banned_pattern_breakdown",
        ),
        "abstract_sensory_passed": (
            "abstract_sensory_hits",
            "abstract_sensory_words",
        ),
        "dumping_passed": ("dumping_hits",),
        "emotion_label_passed": ("emotion_label_hits",),
        "rhythm_passed": (
            "rhythm_total_anchors",
            "rhythm_types_covered",
            "rhythm_expected_min_count",
            "rhythm_expected_min_types",
        ),
    }
    return {
        key: row.get(key)
        for key in keys_by_field.get(field, ())
        if row.get(key) not in (None, "")
    }


def _severity_from_priority(priority: str) -> str:
    lowered = priority.strip().lower()
    if lowered in {"critical", "high", "medium", "low"}:
        return lowered
    return "medium"


def _boolish(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "passed", "pass"}:
        return True
    if normalized in {"false", "0", "no", "n", "failed", "fail"}:
        return False
    return default


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)

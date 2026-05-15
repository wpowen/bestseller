from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

_SOURCE_SPECIFIC_RE = re.compile(
    r"(/Users/|/private/|[A-Za-z]:\\\\|source_ref|book title|第\s*\d+\s*章|chapter\s*\d+)",
    re.I,
)

_SOURCE_SPECIFIC_TERMS = (
    "获取桥段",
    "原书",
    "照搬",
    "named_artifact",
    "named_technique",
    "specific_",
)


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _string_list(value: object) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in _as_sequence(value):
        text = _text(raw)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return slug[:80] or "entry-feedback"


def sanitize_entry_feedback_text(
    text: str,
    *,
    blocked_terms: Sequence[str] = (),
) -> str:
    sanitized = text.strip()
    for term in sorted((item for item in blocked_terms if item), key=len, reverse=True):
        sanitized = sanitized.replace(term, "[REDACTED]")
    return sanitized


def entry_feedback_passes_privacy_gate(
    row: Mapping[str, object],
    *,
    min_confidence: float = 0.6,
) -> tuple[bool, str | None]:
    confidence = _float(row.get("confidence"))
    if confidence < min_confidence:
        return False, "low_confidence"
    haystack = " ".join(
        [
            _text(row.get("slug")),
            _text(row.get("name")),
            _text(row.get("narrative_summary")),
            str(_as_mapping(row.get("content_json"))),
        ]
    )
    normalized = haystack.lower()
    if _SOURCE_SPECIFIC_RE.search(haystack):
        return False, "source_specific"
    if any(term.lower() in normalized for term in _SOURCE_SPECIFIC_TERMS):
        return False, "source_specific"
    return True, None


def build_entry_blueprint_review_row(
    feedback: Mapping[str, object],
    *,
    min_confidence: float = 0.6,
) -> dict[str, object] | None:
    confidence = _float(feedback.get("confidence"))
    if confidence < min_confidence:
        return None
    entry_type = _text(feedback.get("entry_type") or feedback.get("type")) or "entry"
    blocked_terms = tuple(
        item
        for item in (
            _text(feedback.get("entry_name")),
            _text(feedback.get("project_title")),
            _text(feedback.get("character_name")),
            *_string_list(feedback.get("blocked_terms")),
        )
        if item
    )
    summary = sanitize_entry_feedback_text(
        _text(feedback.get("mechanism_summary") or feedback.get("summary")),
        blocked_terms=blocked_terms,
    )
    if not summary:
        return None
    row = {
        "dimension": "entry_blueprints",
        "slug": _slug(f"{entry_type}-{summary[:24]}"),
        "name": f"抽象词条机制-{entry_type}",
        "narrative_summary": summary,
        "content_json": {
            "entry_types": [entry_type],
            "state_variables": _string_list(feedback.get("state_variables")),
            "required_cost_patterns": _string_list(feedback.get("required_cost_patterns")),
            "reader_rewards": _string_list(feedback.get("reader_rewards")),
            "anti_copy_boundaries": [
                "不得复用项目内具体词条名、人名、地名或事件链",
            ],
        },
        "genre": _text(feedback.get("genre")) or None,
        "sub_genre": _text(feedback.get("sub_genre")) or None,
        "confidence": confidence,
        "status": "review",
    }
    passed, _reason = entry_feedback_passes_privacy_gate(row, min_confidence=min_confidence)
    return row if passed else None


__all__ = [
    "build_entry_blueprint_review_row",
    "entry_feedback_passes_privacy_gate",
    "sanitize_entry_feedback_text",
]

"""Shared LLM closed-loop diagnostics.

This module is intentionally small and dependency-light.  It does not call the
LLM itself; callers own persistence and retry budgets.  Its job is to normalize
parse/schema/gate failures into prompt-ready diagnostics so the next model
attempt knows exactly what failed instead of blindly retrying the same prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

try:  # pydantic is already a project dependency, but keep import defensive.
    from pydantic import ValidationError
except Exception:  # pragma: no cover
    ValidationError = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class LLMGateFinding:
    """One normalized issue that can be fed back to a model."""

    code: str
    severity: str
    path: str
    message: str
    expected: str | None = None
    actual: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    repair_action: str = ""
    retryable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "evidence": dict(self.evidence),
            "repair_action": self.repair_action,
            "retryable": self.retryable,
        }


def findings_from_exception(exc: BaseException, *, default_path: str = "$") -> list[LLMGateFinding]:
    """Convert common parser/schema exceptions into field-level findings."""

    if ValidationError is not None and isinstance(exc, ValidationError):
        findings: list[LLMGateFinding] = []
        for item in exc.errors():
            loc = item.get("loc") or (default_path,)
            path = ".".join(str(part) for part in loc) if isinstance(loc, (tuple, list)) else str(loc)
            msg = str(item.get("msg") or type(exc).__name__)
            input_value = item.get("input")
            ctx = item.get("ctx") if isinstance(item.get("ctx"), Mapping) else {}
            expected = _expected_from_pydantic_error(item)
            repair_action = _repair_action_for_schema_error(
                path=path,
                message=msg,
                expected=expected,
                actual=_compact_value(input_value),
            )
            findings.append(
                LLMGateFinding(
                    code=_code_from_pydantic_error(item),
                    severity="critical",
                    path=path or default_path,
                    message=msg,
                    expected=expected,
                    actual=_compact_value(input_value),
                    evidence={"ctx": dict(ctx), "type": item.get("type")},
                    repair_action=repair_action,
                    retryable=True,
                )
            )
        return findings or [_generic_exception_finding(exc, default_path=default_path)]

    if isinstance(exc, json.JSONDecodeError):
        return [
            LLMGateFinding(
                code="JSON_PARSE_ERROR",
                severity="critical",
                path=default_path,
                message=exc.msg,
                expected="Valid JSON matching the requested schema.",
                actual=f"line={exc.lineno}, column={exc.colno}, pos={exc.pos}",
                evidence={"lineno": exc.lineno, "colno": exc.colno, "pos": exc.pos},
                repair_action=(
                    "Return only one complete JSON value. Remove markdown fences, comments, "
                    "trailing prose, trailing commas, and unescaped control characters."
                ),
                retryable=True,
            )
        ]

    return [_generic_exception_finding(exc, default_path=default_path)]


def findings_from_report_payload(
    report_payload: Mapping[str, Any] | None,
    *,
    default_code: str,
) -> list[LLMGateFinding]:
    """Normalize a gate/report dict into findings.

    Supports the report shapes already used in the codebase:
    ``blocking_findings`` / ``warnings`` and plain ``findings``.
    """

    if not report_payload:
        return []
    raw_items: list[Mapping[str, Any]] = []
    for key in ("blocking_findings", "findings", "warnings", "deficiencies", "violations"):
        value = report_payload.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            raw_items.extend(item for item in value if isinstance(item, Mapping))
    findings: list[LLMGateFinding] = []
    for item in raw_items:
        code = str(item.get("code") or item.get("category") or default_code)
        severity = str(item.get("severity") or "major")
        path = str(item.get("path") or item.get("location") or "$")
        message = str(item.get("message") or item.get("detail") or code)
        repair = str(
            item.get("repair_action")
            or item.get("suggestion")
            or item.get("prompt_feedback")
            or "Revise the affected field so it satisfies the gate."
        )
        evidence = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        findings.append(
            LLMGateFinding(
                code=code,
                severity=severity,
                path=path,
                message=message,
                expected=_text_or_none(item.get("expected")),
                actual=_text_or_none(item.get("actual")),
                evidence=dict(evidence),
                repair_action=repair,
                retryable=True,
            )
        )
    return findings


def render_repair_diagnostics_block(
    findings: Iterable[LLMGateFinding],
    *,
    language: str | None = None,
    max_findings: int = 12,
) -> str:
    """Render diagnostics as a compact prompt block."""

    items = list(findings)[: max(1, max_findings)]
    if not items:
        return ""
    is_en = (language or "").lower().startswith("en")
    if is_en:
        lines = [
            "[SYSTEM REPAIR DIAGNOSTICS - previous output failed validation]",
            "Regenerate the full requested output. Fix every item below. Do not ignore field paths.",
        ]
        for idx, item in enumerate(items, start=1):
            lines.append(f"{idx}. [{item.code}] {item.path}")
            lines.append(f"   Problem: {item.message}")
            if item.expected:
                lines.append(f"   Expected: {item.expected}")
            if item.actual:
                lines.append(f"   Actual: {item.actual}")
            if item.repair_action:
                lines.append(f"   Required fix: {item.repair_action}")
        lines.append("Return only the final output in the originally requested format.")
        return "\n".join(lines)

    lines = [
        "【系统修复诊断 - 上一轮输出未通过校验】",
        "请重新生成完整输出，必须逐项修复以下问题。不要忽略字段路径，不要只解释原因。",
    ]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. [{item.code}] {item.path}")
        lines.append(f"   问题：{item.message}")
        if item.expected:
            lines.append(f"   期望：{item.expected}")
        if item.actual:
            lines.append(f"   实际：{item.actual}")
        if item.repair_action:
            lines.append(f"   修复要求：{item.repair_action}")
    lines.append("只返回原任务要求的最终格式，不要输出解释、复盘或 Markdown 包裹。")
    return "\n".join(lines)


def build_repair_user_prompt(
    *,
    original_user_prompt: str,
    findings: Iterable[LLMGateFinding],
    language: str | None = None,
    max_findings: int = 12,
) -> str:
    """Append normalized diagnostics to the original user prompt."""

    block = render_repair_diagnostics_block(
        findings,
        language=language,
        max_findings=max_findings,
    )
    if not block:
        return original_user_prompt
    return f"{original_user_prompt.rstrip()}\n\n---\n{block}\n"


def _generic_exception_finding(exc: BaseException, *, default_path: str) -> LLMGateFinding:
    return LLMGateFinding(
        code=type(exc).__name__.upper(),
        severity="critical",
        path=default_path,
        message=str(exc) or type(exc).__name__,
        expected="Output must satisfy the parser and validator for this task.",
        actual=None,
        evidence={},
        repair_action="Regenerate the output and fix the exact parser/validator failure.",
        retryable=True,
    )


def _code_from_pydantic_error(item: Mapping[str, Any]) -> str:
    raw_type = str(item.get("type") or "validation_error").upper()
    if "LITERAL" in raw_type:
        return "SCHEMA_LITERAL_INVALID"
    if "MISSING" in raw_type:
        return "SCHEMA_FIELD_MISSING"
    if "LIST" in raw_type:
        return "SCHEMA_LIST_INVALID"
    if "DICT" in raw_type or "MODEL" in raw_type:
        return "SCHEMA_OBJECT_INVALID"
    return "SCHEMA_VALIDATION_ERROR"


def _expected_from_pydantic_error(item: Mapping[str, Any]) -> str | None:
    ctx = item.get("ctx") if isinstance(item.get("ctx"), Mapping) else {}
    expected = ctx.get("expected") if isinstance(ctx, Mapping) else None
    if expected is not None:
        return str(expected)
    msg = str(item.get("msg") or "")
    if "Input should be" in msg:
        return msg.replace("Input should be", "").strip()
    return None


def _repair_action_for_schema_error(
    *,
    path: str,
    message: str,
    expected: str | None,
    actual: str | None,
) -> str:
    if expected:
        return (
            f"Set `{path}` to a value that matches {expected}. "
            "If the rejected value contains extra nuance, move that nuance into an adjacent descriptive field."
        )
    if "Field required" in message:
        return f"Add the missing required field `{path}` with concrete project-specific content."
    return f"Rewrite `{path}` so it satisfies the schema and remains project-specific."


def _compact_value(value: Any, *, limit: int = 180) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False)
    except TypeError:
        text = str(value)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "LLMGateFinding",
    "build_repair_user_prompt",
    "findings_from_exception",
    "findings_from_report_payload",
    "render_repair_diagnostics_block",
]

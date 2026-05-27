from __future__ import annotations

# ruff: noqa: RUF001
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

PlanningReadinessSeverity = Literal["critical", "high", "medium", "low"]


class PlanningReadinessFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: PlanningReadinessSeverity = "medium"
    message: str
    path: str = ""
    repair_hint: str = ""
    blocking: bool = True

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> PlanningReadinessSeverity:
        normalized = str(value or "medium").lower()
        if normalized not in {"critical", "high", "medium", "low"}:
            return "medium"
        return normalized  # type: ignore[return-value]


class PlanningReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_name: str = "planning_readiness_gate"
    passed: bool
    blocking_findings: tuple[PlanningReadinessFinding, ...] = ()
    audit_findings: tuple[PlanningReadinessFinding, ...] = ()
    missing_context_keys: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = Field(default_factory=dict)
    repair_prompt: str = ""
    schema_version: str = "planning-readiness.v1"

    @computed_field(return_type=tuple[str, ...])
    @property
    def blocking_issue_codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.blocking_findings)

    @computed_field(return_type=str)
    @property
    def verdict(self) -> str:
        return "pass" if self.passed else "blocked"

    @classmethod
    def from_findings(
        cls,
        *,
        blocking_findings: list[PlanningReadinessFinding],
        audit_findings: list[PlanningReadinessFinding] | None = None,
        missing_context_keys: list[str] | None = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> PlanningReadinessReport:
        missing = tuple(dict.fromkeys(missing_context_keys or []))
        blocking = tuple(blocking_findings)
        audit = tuple(audit_findings or [])
        return cls(
            passed=not blocking,
            blocking_findings=blocking,
            audit_findings=audit,
            missing_context_keys=missing,
            metrics=dict(metrics or {}),
            repair_prompt=_build_repair_prompt(blocking, audit, missing),
        )


def _build_repair_prompt(
    blocking_findings: tuple[PlanningReadinessFinding, ...],
    audit_findings: tuple[PlanningReadinessFinding, ...],
    missing_context_keys: tuple[str, ...],
) -> str:
    findings = [*blocking_findings, *audit_findings]
    if not findings and not missing_context_keys:
        return ""
    lines = [
        "请修复规划输入后再进入正文生成：",
    ]
    for finding in findings[:20]:
        hint = f" 修复：{finding.repair_hint}" if finding.repair_hint else ""
        path = f" [{finding.path}]" if finding.path else ""
        lines.append(f"- {finding.code}{path}: {finding.message}{hint}")
    if missing_context_keys:
        lines.append("- 缺失上下文键：" + "、".join(missing_context_keys))
    return "\n".join(lines)

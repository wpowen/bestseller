from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

GateSeverity = Literal["critical", "high", "medium", "low"]
GateVerdictStatus = Literal["pass", "warn_only", "blocked", "not_run", "error"]


class GateFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: GateSeverity = "medium"
    message: str = ""
    path: str = ""
    repair_action: str = ""

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> GateSeverity:
        normalized = str(value or "medium").lower()
        if normalized not in {"critical", "high", "medium", "low"}:
            return "medium"
        return normalized  # type: ignore[return-value]

    @property
    def critical(self) -> bool:
        return self.severity == "critical"


class GateVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_name: str
    verdict: GateVerdictStatus
    coverage: float = Field(ge=0.0, le=1.0)
    findings: tuple[GateFinding, ...] = ()
    metrics: Mapping[str, Any] = Field(default_factory=dict)
    required: bool = True
    schema_version: str = "gate-verdict.v2"
    summary: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, value: object) -> GateVerdictStatus:
        normalized = str(value or "not_run").lower()
        aliases = {
            "passed": "pass",
            "ready": "pass",
            "success": "pass",
            "failed": "blocked",
            "fail": "blocked",
            "critical": "blocked",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"pass", "warn_only", "blocked", "not_run", "error"}:
            return "not_run"
        return normalized  # type: ignore[return-value]

    @model_validator(mode="before")
    @classmethod
    def _enforce_false_green_rules(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        payload = dict(data)
        if str(payload.get("verdict") or "").lower() != "pass":
            return payload
        metrics = payload.get("metrics")
        metric_map = metrics if isinstance(metrics, Mapping) else {}
        quality_score = _float_or_default(metric_map.get("quality_score"), 100.0)
        coverage = _float_or_default(payload.get("coverage"), 0.0)
        if (
            coverage < 0.95
            or _raw_critical_count(payload.get("findings")) > 0
            or quality_score < 70
        ):
            payload["verdict"] = "warn_only"
        return payload

    @computed_field(return_type=int)
    @property
    def critical_count(self) -> int:
        return sum(1 for finding in self.findings if finding.critical)

    @computed_field(return_type=bool)
    @property
    def critical(self) -> bool:
        return self.verdict in {"blocked", "error"} or self.critical_count > 0

    @computed_field(return_type=bool)
    @property
    def passed(self) -> bool:
        return (
            self.verdict == "pass"
            and self.coverage >= 0.95
            and self.critical_count == 0
            and _float_or_default(self.metrics.get("quality_score"), 100.0) >= 70
        )


class AggregateGateReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_name: str
    gates: tuple[GateVerdict, ...] = ()
    schema_version: str = "aggregate-gate-report.v1"
    summary: str = ""

    @computed_field(return_type=float)
    @property
    def coverage(self) -> float:
        if not self.gates:
            return 0.0
        return min(gate.coverage for gate in self.gates)

    @computed_field(return_type=int)
    @property
    def overall_score(self) -> int:
        return round(self.coverage * 100)

    @computed_field(return_type=str)
    @property
    def readiness(self) -> str:
        if any(
            gate.critical_count > 0 or gate.verdict in {"blocked", "error"}
            for gate in self.gates
        ):
            return "blocked"
        return "not_blocked"

    @computed_field(return_type=str)
    @property
    def verdict(self) -> GateVerdictStatus:
        required = tuple(gate for gate in self.gates if gate.required)
        if not required:
            return "not_run"
        if any(gate.verdict == "error" for gate in required):
            return "error"
        if any(gate.verdict == "blocked" or gate.critical_count > 0 for gate in required):
            return "blocked"
        if all(gate.verdict == "pass" for gate in required):
            return "pass"
        return "warn_only"

    @computed_field(return_type=bool)
    @property
    def passed(self) -> bool:
        required = tuple(gate for gate in self.gates if gate.required)
        return bool(required) and all(gate.verdict == "pass" for gate in required)


def gate_verdict_from_mapping(payload: Mapping[str, Any]) -> GateVerdict:
    return GateVerdict.model_validate(payload)


def aggregate_gate_report_from_sequence(
    gate_name: str,
    gates: Sequence[GateVerdict | Mapping[str, Any]],
) -> AggregateGateReport:
    return AggregateGateReport(
        gate_name=gate_name,
        gates=tuple(
            gate if isinstance(gate, GateVerdict) else gate_verdict_from_mapping(gate)
            for gate in gates
        ),
    )


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _raw_critical_count(value: object) -> int:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return 0
    count = 0
    for item in value:
        if isinstance(item, GateFinding):
            count += int(item.critical)
        elif isinstance(item, Mapping):
            count += int(str(item.get("severity") or "").lower() == "critical")
    return count

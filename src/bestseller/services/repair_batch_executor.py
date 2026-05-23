from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.gate_verdict import (
    AggregateGateReport,
    GateFinding,
    GateVerdict,
)
from bestseller.services.commercial_novel_gate import (
    commercial_gate_report_to_dict,
    evaluate_book_package,
)

GateRunner = Callable[["RepairBatchRequest"], GateVerdict | Mapping[str, Any]]


@dataclass(frozen=True)
class RepairBatchRequest:
    project: str
    chapters: tuple[int, ...] = ()
    bible_paths: tuple[str, ...] = ()
    project_output_dir: Path | None = None

    @property
    def touches_chapters(self) -> bool:
        return bool(self.chapters)

    @property
    def touches_story_bible(self) -> bool:
        return bool(self.bible_paths)


@dataclass(frozen=True)
class RepairBatchReport:
    project: str
    status: str
    affected_gates: tuple[str, ...]
    gate_report: AggregateGateReport
    chapters: tuple[int, ...] = ()
    bible_paths: tuple[str, ...] = ()
    report_path: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "status": self.status,
            "created_at": self.created_at,
            "chapters": list(self.chapters),
            "bible_paths": list(self.bible_paths),
            "affected_gates": list(self.affected_gates),
            "gate_report": self.gate_report.model_dump(mode="json"),
            "report_path": self.report_path,
        }


class RepairBatchExecutor:
    def __init__(
        self,
        *,
        dependency_config_path: str | Path = "config/gate_dependencies.yaml",
        gate_runners: Mapping[str, GateRunner] | None = None,
    ) -> None:
        self.dependency_config_path = Path(dependency_config_path)
        self.config = _load_dependency_config(self.dependency_config_path)
        self.gate_runners = dict(gate_runners or {})

    def affected_gates(self, request: RepairBatchRequest) -> tuple[str, ...]:
        defaults = _mapping(self.config.get("defaults"))
        gates: list[str] = []
        if request.touches_chapters:
            gates.extend(_string_list(defaults.get("chapter")))
        if request.touches_story_bible:
            gates.extend(_string_list(defaults.get("story_bible")))
        if not gates:
            gates.extend(_string_list(defaults.get("project")))
        return tuple(dict.fromkeys(gates))

    def run(
        self,
        request: RepairBatchRequest,
        *,
        artifacts_dir: str | Path = "artifacts",
        persist: bool = True,
    ) -> RepairBatchReport:
        affected = self.affected_gates(request)
        verdicts = tuple(self._run_gate(name, request) for name in affected)
        aggregate = AggregateGateReport(
            gate_name="repair-batch",
            gates=verdicts,
        )
        status = "blocked" if aggregate.verdict in {"blocked", "error"} else "passed"
        report = RepairBatchReport(
            project=request.project,
            status=status,
            chapters=request.chapters,
            bible_paths=request.bible_paths,
            affected_gates=affected,
            gate_report=aggregate,
        )
        if not persist:
            return report
        path = self._write_report(report, artifacts_dir)
        return RepairBatchReport(
            project=report.project,
            status=report.status,
            chapters=report.chapters,
            bible_paths=report.bible_paths,
            affected_gates=report.affected_gates,
            gate_report=report.gate_report,
            report_path=str(path),
            created_at=report.created_at,
        )

    def _run_gate(self, gate_name: str, request: RepairBatchRequest) -> GateVerdict:
        runner = self.gate_runners.get(gate_name)
        if runner is not None:
            return _coerce_verdict(gate_name, runner(request))
        if gate_name == "commercial_novel_gate" and request.project_output_dir is not None:
            return _run_commercial_gate(request)
        return GateVerdict(
            gate_name=gate_name,
            verdict="not_run",
            coverage=0.0,
            metrics={"reason": "no runner registered"},
        )

    def _write_report(
        self,
        report: RepairBatchReport,
        artifacts_dir: str | Path,
    ) -> Path:
        root = Path(artifacts_dir)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"repair-batch-report-{stamp}.json"
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def parse_chapter_list(raw: str | Sequence[int] | None) -> tuple[int, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",")]
        return tuple(int(part) for part in parts if part)
    return tuple(int(item) for item in raw)


def _run_commercial_gate(request: RepairBatchRequest) -> GateVerdict:
    package_dir = request.project_output_dir
    if package_dir is None or not package_dir.exists():
        return GateVerdict(
            gate_name="commercial_novel_gate",
            verdict="not_run",
            coverage=0.0,
            metrics={"reason": "project output dir missing"},
        )
    payload = commercial_gate_report_to_dict(evaluate_book_package(package_dir))
    verdict_payload = payload.get("gate_verdict")
    if isinstance(verdict_payload, Mapping):
        return GateVerdict.model_validate(verdict_payload)
    return GateVerdict(
        gate_name="commercial_novel_gate",
        verdict="error",
        coverage=0.0,
        findings=(
            GateFinding(
                code="commercial_gate_verdict_missing",
                severity="critical",
                message="commercial gate did not return gate_verdict",
            ),
        ),
    )


def _load_dependency_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _coerce_verdict(gate_name: str, value: GateVerdict | Mapping[str, Any]) -> GateVerdict:
    if isinstance(value, GateVerdict):
        return value
    payload = dict(value)
    payload.setdefault("gate_name", gate_name)
    return GateVerdict.model_validate(payload)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]

from __future__ import annotations

import json

from typer.testing import CliRunner

from bestseller.cli.main import app
from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.repair_batch_executor import (
    RepairBatchExecutor,
    RepairBatchRequest,
    parse_chapter_list,
)

runner = CliRunner()


def test_parse_chapter_list_accepts_comma_separated_input() -> None:
    assert parse_chapter_list("50,51, 52") == (50, 51, 52)
    assert parse_chapter_list(None) == ()


def test_repair_batch_executor_runs_affected_gate_set(tmp_path) -> None:
    config = tmp_path / "gate_dependencies.yaml"
    config.write_text(
        """
defaults:
  chapter:
    - commercial_novel_gate
    - seam_inheritance
  story_bible:
    - lifecycle_quality
gates: {}
""".strip(),
        encoding="utf-8",
    )
    seen: list[str] = []

    def runner_for(gate_name: str):
        def _run(request: RepairBatchRequest) -> GateVerdict:
            seen.append(f"{gate_name}:{request.project}")
            return GateVerdict(gate_name=gate_name, verdict="pass", coverage=1.0)

        return _run

    report = RepairBatchExecutor(
        dependency_config_path=config,
        gate_runners={
            "commercial_novel_gate": runner_for("commercial_novel_gate"),
            "seam_inheritance": runner_for("seam_inheritance"),
            "lifecycle_quality": runner_for("lifecycle_quality"),
        },
    ).run(
        RepairBatchRequest(
            project="book-a",
            chapters=(50, 51),
            bible_paths=("story-bible/continuity-ledger.md",),
        ),
        artifacts_dir=tmp_path / "artifacts",
    )

    assert report.status == "passed"
    assert report.affected_gates == (
        "commercial_novel_gate",
        "seam_inheritance",
        "lifecycle_quality",
    )
    assert report.report_path is not None
    assert set(seen) == {
        "commercial_novel_gate:book-a",
        "seam_inheritance:book-a",
        "lifecycle_quality:book-a",
    }


def test_repair_batch_executor_blocks_on_critical_verdict(tmp_path) -> None:
    config = tmp_path / "gate_dependencies.yaml"
    config.write_text(
        "defaults:\n  chapter:\n    - seam_inheritance\n",
        encoding="utf-8",
    )

    report = RepairBatchExecutor(
        dependency_config_path=config,
        gate_runners={
            "seam_inheritance": lambda _request: GateVerdict(
                gate_name="seam_inheritance",
                verdict="blocked",
                coverage=0.5,
                findings=(
                    GateFinding(
                        code="seam_missing",
                        severity="critical",
                        message="missing seam",
                    ),
                ),
            )
        },
    ).run(
        RepairBatchRequest(project="book-a", chapters=(51,)),
        artifacts_dir=tmp_path / "artifacts",
    )

    assert report.status == "blocked"
    payload = json.loads((tmp_path / report.report_path).read_text(encoding="utf-8"))
    assert payload["gate_report"]["verdict"] == "blocked"


def test_repair_batch_cli_writes_report(tmp_path) -> None:
    config = tmp_path / "gate_dependencies.yaml"
    config.write_text(
        "defaults:\n  chapter:\n    - no_runner_gate\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "repair-batch",
            "run",
            "--project",
            "book-a",
            "--chapters",
            "50,51",
            "--dependency-config",
            str(config),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--output-base-dir",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project"] == "book-a"
    assert payload["affected_gates"] == ["no_runner_gate"]
    assert payload["report_path"]

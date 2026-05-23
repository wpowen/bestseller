from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from bestseller.services.repair_batch_executor import (
    RepairBatchExecutor,
    RepairBatchRequest,
    parse_chapter_list,
)

repair_batch_app = typer.Typer(help="Repair batch gate execution operations.")


@repair_batch_app.command("run")
def repair_batch_run(
    project: Annotated[str, typer.Option("--project", "-p")],
    chapters: Annotated[
        str | None,
        typer.Option(
            "--chapters",
            help="Comma-separated chapter numbers touched by the patch.",
        ),
    ] = None,
    bible_path: Annotated[
        list[str] | None,
        typer.Option(
            "--bible-path",
            help="Story-bible paths touched by the patch. Repeatable.",
        ),
    ] = None,
    output_base_dir: Annotated[
        Path,
        typer.Option(
            "--output-base-dir",
            help="Base output directory containing project packages.",
        ),
    ] = Path("output"),
    artifacts_dir: Annotated[
        Path,
        typer.Option(
            "--artifacts-dir",
            help="Directory for repair-batch-report JSON files.",
        ),
    ] = Path("artifacts"),
    dependency_config: Annotated[
        Path,
        typer.Option(
            "--dependency-config",
            help="Gate dependency YAML file.",
        ),
    ] = Path("config/gate_dependencies.yaml"),
) -> None:
    request = RepairBatchRequest(
        project=project,
        chapters=parse_chapter_list(chapters),
        bible_paths=tuple(bible_path or ()),
        project_output_dir=output_base_dir / project,
    )
    report = RepairBatchExecutor(
        dependency_config_path=dependency_config,
    ).run(request, artifacts_dir=artifacts_dir)
    typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

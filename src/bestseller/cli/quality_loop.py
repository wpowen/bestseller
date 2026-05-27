from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from bestseller.infra.db.session import session_scope
from bestseller.services.quality_attribution_loop import run_quality_attribution_loop
from bestseller.settings import load_settings


def parse_chapter_range(value: str) -> tuple[int, int]:
    parts = value.strip().split("-", maxsplit=1)
    if len(parts) != 2:
        raise typer.BadParameter("chapter range must use START-END, for example 1-10")
    try:
        start = int(parts[0])
        end = int(parts[1])
    except ValueError as exc:
        raise typer.BadParameter("chapter range bounds must be integers") from exc
    if start <= 0 or end < start:
        raise typer.BadParameter("chapter range must satisfy 0 < START <= END")
    return start, end


def main(
    book_root: Annotated[
        Path,
        typer.Option("--book-root", exists=True, file_okay=False, help="Output book directory."),
    ],
    chapter_range: Annotated[
        str,
        typer.Option("--chapter-range", help="Inclusive chapter range, for example 1-10."),
    ],
    max_iterations: Annotated[
        int,
        typer.Option("--max-iterations", min=1, help="Maximum L0-L3 loop iterations."),
    ] = 5,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text | json."),
    ] = "text",
) -> None:
    """Run the universal quality attribution loop for an output book."""

    parsed_range = parse_chapter_range(chapter_range)

    async def _run() -> dict[str, object]:
        settings = load_settings()
        async with session_scope(settings) as session:
            return await run_quality_attribution_loop(
                session,
                settings,
                book_root,
                chapter_range=parsed_range,
                max_iterations=max_iterations,
            )

    result = asyncio.run(_run())
    if output_format == "json":
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    if output_format != "text":
        raise typer.BadParameter("--format must be text or json")
    typer.echo(
        "quality-loop: "
        f"iterations={result['iterations']} "
        f"converged={result['converged']} "
        f"feedback={len(result['final_feedback'])} "
        f"repairs={len(result['repair_log'])}"
    )
    typer.echo(
        "reports: "
        f"{(book_root / 'audits' / 'quality-attribution-loop').as_posix()}"
    )


if __name__ == "__main__":
    typer.run(main)


__all__ = ["main", "parse_chapter_range"]

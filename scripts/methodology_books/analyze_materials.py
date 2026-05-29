#!/usr/bin/env python
"""Write audit artifacts for distilled writing-book methodology material."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from bestseller.services.methodology_book_baseline import write_default_baseline_metric_spec
from bestseller.services.methodology_book_corpus import (
    build_core_deck_payload,
    build_core_profile_payload,
    default_methodology_books_root,
    load_book_methodology_corpus,
    write_book_methodology_analysis,
)

app = typer.Typer(add_completion=False)
ROOT_OPTION = typer.Option(
    None,
    help="Root directory containing source-*/methodology_cards.review.yaml.",
)
OUTPUT_DIR_OPTION = typer.Option(
    None,
    help="Directory for generated analysis files. Defaults to ROOT/analysis.",
)


@app.command()
def main(
    root: Path | None = ROOT_OPTION,
    output_dir: Path | None = OUTPUT_DIR_OPTION,
) -> None:
    """Generate inventory, domain clusters, and baseline metric spec."""

    effective_root = root or default_methodology_books_root()
    effective_output = output_dir or effective_root / "analysis"
    inventory_path, clusters_path = write_book_methodology_analysis(
        root=effective_root,
        output_dir=effective_output,
    )
    baseline_path = write_default_baseline_metric_spec(
        effective_output / "baseline_metric_spec.yaml"
    )
    corpus = load_book_methodology_corpus(effective_root)
    core_cards_dir = Path("data/methodology_sources/books_core")
    core_cards_dir.mkdir(parents=True, exist_ok=True)
    core_cards_path = core_cards_dir / "cards.yaml"
    core_cards_path.write_text(
        yaml.safe_dump(
            build_core_deck_payload(corpus),
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    profile_path = Path("config/methodology_profiles/books_core_v1.yaml")
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        yaml.safe_dump(
            build_core_profile_payload(corpus),
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    typer.echo(f"wrote {inventory_path}")
    typer.echo(f"wrote {clusters_path}")
    typer.echo(f"wrote {baseline_path}")
    typer.echo(f"wrote {core_cards_path}")
    typer.echo(f"wrote {profile_path}")


if __name__ == "__main__":
    app()

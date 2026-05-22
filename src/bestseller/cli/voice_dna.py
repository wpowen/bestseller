"""Voice DNA CLI — extract / blend / inspect.

Example usage:

    # Extract DNA from one reference book and persist for project slug "my-book"
    bestseller voice-dna extract \\
        --slug my-book \\
        --source "/Volumes/书籍/Ebook/高评分小说/《诡秘之主》（精校版全本）作者：爱潜水的乌贼.txt" \\
        --label "诡秘之主 voice"

    # Blend two reference books (50/50)
    bestseller voice-dna extract \\
        --slug my-book \\
        --source path/to/book1.txt --weight 1.0 --label voice-A \\
        --source path/to/book2.txt --weight 1.0 --label voice-B

    # Inspect the persisted DNA
    bestseller voice-dna inspect --slug my-book

The CLI is intentionally light: it does deterministic text → DNA extraction
and writes ``output/<slug>/story-bible/voice-dna.json``. No DB, no LLM.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

from bestseller.services.voice_dna_repository import (
    load_voice_dna,
    resolve_voice_dna_path,
    save_voice_dna,
)
from bestseller.services.voice_signature import (
    blend_voice_dna,
    extract_voice_dna_from_text,
    render_voice_dna_block,
)

logger = logging.getLogger(__name__)

voice_dna_app = typer.Typer(
    help=(
        "Voice DNA — extract author voice signatures from reference books "
        "and persist as story-bible/voice-dna.json so chapter prompts can "
        "anchor to a specific prose fingerprint."
    )
)

_DEFAULT_MAX_CHARS = 200_000  # truncate massive .txt files; DNA stabilizes well before 200k

# Encoding fallback order — covers the common Chinese .txt sources seen in
# the wild (modern UTF-8 / BOM-stamped UTF-8 / legacy GBK & GB18030 from
# older Windows-era exports / Big5 for Traditional Chinese sources).
_ENCODING_FALLBACK = ("utf-8", "utf-8-sig", "gb18030", "gbk", "big5")


def _read_text_with_encoding(path: Path, override: Optional[str]) -> str:
    raw = path.read_bytes()
    if override:
        return raw.decode(override, errors="replace")
    last_error: UnicodeDecodeError | None = None
    for candidate in _ENCODING_FALLBACK:
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return raw.decode("utf-8", errors="replace")



@voice_dna_app.command("extract")
def extract_voice_dna(
    slug: str = typer.Option(..., "--slug", help="Project slug under output/."),
    source: list[Path] = typer.Option(
        ...,
        "--source",
        "-s",
        help=(
            "Path to a reference book .txt file. Repeat to blend multiple "
            "books. Order matters when combined with --weight."
        ),
    ),
    weight: list[float] = typer.Option(
        None,
        "--weight",
        "-w",
        help=(
            "Per-source blend weight (positional, same count as --source). "
            "Defaults to 1.0 for every source."
        ),
    ),
    label: list[str] = typer.Option(
        None,
        "--label",
        "-l",
        help="Per-source human label (positional). Defaults to file stem.",
    ),
    register_hint: str = typer.Option(
        "",
        "--register",
        help="Optional 语体 hint (e.g. '古风/现代' / 'classical/modern').",
    ),
    exclude_phrase: list[str] = typer.Option(
        None,
        "--exclude-phrase",
        "-x",
        help=(
            "Phrase to suppress from catchphrase/opener/closer extraction. "
            "Use to filter out character names and book-specific proper nouns "
            "so the DNA captures style rather than identity. Repeatable."
        ),
    ),
    max_chars: int = typer.Option(
        _DEFAULT_MAX_CHARS,
        "--max-chars",
        help="Per-source character cap for sampling. Defaults to 200 000.",
    ),
    encoding: Optional[str] = typer.Option(
        None,
        "--encoding",
        help=(
            "Force a specific source encoding (e.g. utf-8 / gbk / gb18030). "
            "When omitted, the CLI auto-detects by trying utf-8 -> utf-8-sig -> "
            "gb18030 -> gbk -> big5 in that order and using whichever decodes "
            "cleanly."
        ),
    ),
    output_base_dir: Path = typer.Option(
        Path("output"),
        "--output-base-dir",
        help="Base output directory (defaults to ./output).",
    ),
    mode_b: bool = typer.Option(
        False,
        "--mode-b/--mode-a",
        help="Mode B writes to output/ai-generated/<slug>/, Mode A to output/<slug>/.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite/--no-overwrite",
        help="Required when a voice-dna.json already exists for this slug.",
    ),
) -> None:
    """Extract per-source DNAs, blend if multiple, persist to disk."""

    sources = list(source)
    if not sources:
        typer.echo("error: at least one --source is required.", err=True)
        raise typer.Exit(code=2)

    weights = list(weight) if weight else [1.0] * len(sources)
    if len(weights) != len(sources):
        typer.echo(
            f"error: --weight count ({len(weights)}) must match --source count "
            f"({len(sources)}).",
            err=True,
        )
        raise typer.Exit(code=2)

    labels = list(label) if label else []
    if labels and len(labels) != len(sources):
        typer.echo(
            f"error: --label count ({len(labels)}) must match --source count "
            f"({len(sources)}).",
            err=True,
        )
        raise typer.Exit(code=2)

    target_path = resolve_voice_dna_path(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if target_path.exists() and not overwrite:
        typer.echo(
            f"error: {target_path} already exists. Re-run with --overwrite "
            "to replace it.",
            err=True,
        )
        raise typer.Exit(code=1)

    samples = []
    for idx, path in enumerate(sources):
        path = Path(path)
        if not path.exists():
            typer.echo(f"error: source not found: {path}", err=True)
            raise typer.Exit(code=2)
        try:
            text = _read_text_with_encoding(path, encoding)
        except OSError as exc:
            typer.echo(f"error: failed to read {path}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except UnicodeDecodeError as exc:
            typer.echo(
                f"error: could not decode {path} with any known encoding. "
                f"Use --encoding to specify one. ({exc})",
                err=True,
            )
            raise typer.Exit(code=2) from exc
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
        sample_label = labels[idx] if labels else path.stem
        dna = extract_voice_dna_from_text(
            text,
            source_id=f"src-{idx}-{path.stem}",
            source_label=sample_label,
            register_hint=register_hint,
            excluded_phrases=exclude_phrase or [],
        )
        typer.echo(
            f"extracted DNA[{idx}] from {path.name}: "
            f"{dna.sample_chars} chars, confidence={dna.confidence:.2f}, "
            f"catchphrases={len(dna.catchphrases)}"
        )
        samples.append(dna)

    if len(samples) == 1:
        final = samples[0]
    else:
        final = blend_voice_dna(
            samples,
            weights=weights,
            blended_source_id=f"blend-{slug}",
            blended_label=f"blend({len(samples)}): {slug}",
        )
        typer.echo(
            f"blended {len(samples)} samples -> confidence={final.confidence:.2f}, "
            f"catchphrases={len(final.catchphrases)}"
        )

    persisted = save_voice_dna(
        final, slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    typer.echo(f"voice DNA written to {persisted}")


@voice_dna_app.command("inspect")
def inspect_voice_dna(
    slug: str = typer.Option(..., "--slug", help="Project slug under output/."),
    output_base_dir: Path = typer.Option(
        Path("output"),
        "--output-base-dir",
        help="Base output directory.",
    ),
    mode_b: bool = typer.Option(
        False,
        "--mode-b/--mode-a",
        help="Mode B reads from output/ai-generated/<slug>/, Mode A from output/<slug>/.",
    ),
    language: str = typer.Option(
        "zh-CN",
        "--language",
        help="Render language for the prompt-friendly block (zh-CN | en).",
    ),
) -> None:
    """Print the persisted DNA as a prompt block (the same one injected into chapter prompts)."""

    dna = load_voice_dna(slug, output_base_dir=output_base_dir, mode_b=mode_b)
    if dna is None:
        path = resolve_voice_dna_path(
            slug, output_base_dir=output_base_dir, mode_b=mode_b
        )
        typer.echo(f"no voice DNA found at {path}", err=True)
        raise typer.Exit(code=1)
    block = render_voice_dna_block(dna, language=language)
    typer.echo(block)


__all__ = ["voice_dna_app"]

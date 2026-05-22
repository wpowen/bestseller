"""End-to-end demo CLI commands proving the framework can drive a book.

Workflow:

1. ``bestseller book plan-concept --slug demo --seed 42``
   Generate concept-leap candidates and write the top one to
   ``output/<slug>/story-bible/concept-leap.json``.

2. ``bestseller book plan-signatures --slug demo --total 60``
   Plan signature scenes at cadence=10 and write
   ``output/<slug>/story-bible/signature-scene-plan.json``.

3. ``bestseller book prepare-chapter --slug demo --chapter 7``
   Load DNA + market bundle + signature plan + prior persona feedback,
   render the full prompt-side block stack, and print it so you can see
   exactly what the LLM would be told.

4. ``bestseller book grade-chapter --slug demo --chapter 7 --text-file ch7.md``
   Score an already-written chapter against the 7 personas, persist
   feedback for chapter 8's prep, and print the verdict.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from bestseller.services.chapter_orchestrator import (
    grade_chapter,
    prepare_chapter_context,
    save_signature_plan,
)
from bestseller.services.concept_leap import (
    generate_concept_leap,
    render_concept_candidate_block,
)
from bestseller.services.market_constraint_compiler import (
    render_chapter_constraints_block,
)
from bestseller.services.reader_persona_simulator import (
    render_persona_feedback_block,
)
from bestseller.services.signature_scene_planner import (
    plan_signature_scenes,
    render_signature_scene_block,
)
from bestseller.services.voice_signature import render_voice_dna_block

logger = logging.getLogger(__name__)


book_app = typer.Typer(
    help=(
        "End-to-end demo commands that assemble the full BestSeller framework "
        "pipeline (Concept Leap, Voice DNA, Market Constraints, Signature "
        "Scenes, Reader Persona feedback) into a working chapter generation "
        "loop."
    )
)


@book_app.command("bootstrap")
def bootstrap_book(
    slug: str = typer.Option(..., "--slug", help="Project slug under output/."),
    reference: list[Path] = typer.Option(
        ...,
        "--reference",
        "-r",
        help=(
            "Reference book .txt path (repeatable). Voice DNA is extracted "
            "from each — multiple references are blended uniformly."
        ),
    ),
    total: int = typer.Option(
        100,
        "--total",
        help="Target total chapters. Used for signature-scene plan cadence.",
    ),
    cadence: int = typer.Option(
        10, "--cadence", help="Signature-scene cadence in chapters."
    ),
    concept_seed: int = typer.Option(
        42, "--concept-seed", help="Deterministic seed for concept-leap generation."
    ),
    pools_per_candidate: int = typer.Option(
        4, "--pools", help="Concept-leap: pools per candidate."
    ),
    concept_samples: int = typer.Option(
        80, "--concept-samples", help="Concept-leap: sample size."
    ),
    concept_top_k: int = typer.Option(
        3, "--concept-top", help="Concept-leap: top K candidates kept."
    ),
    voice_register: str = typer.Option(
        "", "--register", help="Optional 语体 hint for voice DNA extraction."
    ),
    exclude_phrase: list[str] = typer.Option(
        None,
        "--exclude-phrase",
        "-x",
        help=(
            "Phrase to suppress from voice-DNA extraction (typically "
            "character names). Repeatable."
        ),
    ),
    max_chars_per_reference: int = typer.Option(
        300_000,
        "--max-chars",
        help="Per-reference text cap. DNA stabilizes well before 300k.",
    ),
    output_base_dir: Path = typer.Option(
        Path("output"), "--output-base-dir"
    ),
    mode_b: bool = typer.Option(False, "--mode-b/--mode-a"),
    overwrite: bool = typer.Option(
        False,
        "--overwrite/--no-overwrite",
        help="Allow overwriting existing voice-dna.json / concept-leap.json / signature-scene-plan.json.",
    ),
) -> None:
    """One-shot bootstrap: extract Voice DNA + generate Concept Leap + plan Signature Scenes.

    Run this once when starting a new book so the pipeline has all
    file-backed artifacts in place before the first chapter is written.
    """

    from bestseller.cli.voice_dna import _read_text_with_encoding
    from bestseller.services.concept_leap import generate_concept_leap
    from bestseller.services.signature_scene_planner import plan_signature_scenes
    from bestseller.services.voice_dna_repository import (
        resolve_voice_dna_path,
        save_voice_dna,
    )
    from bestseller.services.voice_signature import (
        blend_voice_dna,
        extract_voice_dna_from_text,
    )

    references = list(reference)
    if not references:
        typer.echo("error: at least one --reference is required.", err=True)
        raise typer.Exit(code=2)

    typer.echo(f"=== bootstrap: {slug} (total={total}, cadence={cadence}) ===")

    # ── Step 1: Voice DNA ──
    dna_path = resolve_voice_dna_path(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if dna_path.exists() and not overwrite:
        typer.echo(
            f"error: {dna_path} already exists. Use --overwrite to replace.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"\n[1/3] Extracting Voice DNA from {len(references)} reference(s)…")
    excluded = list(exclude_phrase or [])
    samples = []
    for idx, ref_path in enumerate(references):
        if not ref_path.exists():
            typer.echo(f"error: reference not found: {ref_path}", err=True)
            raise typer.Exit(code=2)
        try:
            text = _read_text_with_encoding(ref_path, None)
        except UnicodeDecodeError as exc:
            typer.echo(
                f"error: could not decode {ref_path}. ({exc})", err=True
            )
            raise typer.Exit(code=2) from exc
        if max_chars_per_reference > 0 and len(text) > max_chars_per_reference:
            text = text[:max_chars_per_reference]
        sample = extract_voice_dna_from_text(
            text,
            source_id=f"ref-{idx}-{ref_path.stem}",
            source_label=ref_path.stem,
            register_hint=voice_register,
            excluded_phrases=excluded,
        )
        typer.echo(
            f"   · {ref_path.name}: {sample.sample_chars} chars, "
            f"confidence={sample.confidence:.2f}, "
            f"catchphrases={len(sample.catchphrases)}"
        )
        samples.append(sample)

    if len(samples) == 1:
        dna = samples[0]
    else:
        dna = blend_voice_dna(
            samples,
            weights=[1.0] * len(samples),
            blended_source_id=f"blend-{slug}",
            blended_label=f"blend({len(samples)}): {slug}",
        )
        typer.echo(
            f"   blended {len(samples)} samples -> confidence={dna.confidence:.2f}, "
            f"catchphrases={len(dna.catchphrases)}"
        )
    persisted_dna = save_voice_dna(
        dna, slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    typer.echo(f"   → {persisted_dna}")

    # ── Step 2: Concept Leap ──
    typer.echo(f"\n[2/3] Generating Concept Leap (seed={concept_seed})…")
    concept_path = _story_bible_path(
        slug, "concept-leap.json", output_base_dir, mode_b
    )
    if concept_path.exists() and not overwrite:
        typer.echo(
            f"error: {concept_path} already exists. Use --overwrite.", err=True
        )
        raise typer.Exit(code=1)
    concept_result = generate_concept_leap(
        pools_per_candidate=pools_per_candidate,
        sample_size=concept_samples,
        top_k=concept_top_k,
        seed=concept_seed,
    )
    if not concept_result.candidates:
        typer.echo("error: concept leap produced no candidates.", err=True)
        raise typer.Exit(code=1)
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        json.dumps(
            concept_result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    best = concept_result.best()
    typer.echo(f"   best candidate: {best.signature if best else '(none)'}")
    typer.echo(f"   → {concept_path}")

    # ── Step 3: Signature Scene Plan ──
    typer.echo(f"\n[3/3] Planning {total // cadence}+ signature scenes…")
    sig_path = _story_bible_path(
        slug, "signature-scene-plan.json", output_base_dir, mode_b
    )
    if sig_path.exists() and not overwrite:
        typer.echo(
            f"error: {sig_path} already exists. Use --overwrite.", err=True
        )
        raise typer.Exit(code=1)
    plan = plan_signature_scenes(total_chapters=total, cadence=cadence)
    persisted_sig = save_signature_plan(
        plan, slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    typer.echo(f"   {len(plan.mandates)} mandates scheduled")
    typer.echo(f"   → {persisted_sig}")

    typer.echo(
        f"\n✓ bootstrap complete. "
        f"The pipeline will now auto-inject Voice DNA + Market Constraints "
        f"+ Signature Scenes + Reader Persona feedback into every chapter "
        f"prompt for slug={slug}."
    )


@book_app.command("plan-concept")
def plan_concept(
    slug: str = typer.Option(..., "--slug", help="Project slug."),
    seed: int = typer.Option(42, "--seed", help="Deterministic seed for reproducibility."),
    pools_per_candidate: int = typer.Option(
        4, "--pools", help="How many domain pools each candidate spans."
    ),
    sample_size: int = typer.Option(
        60, "--samples", help="How many candidate mashups to evaluate."
    ),
    top_k: int = typer.Option(5, "--top", help="How many top candidates to keep."),
    output_base_dir: Path = typer.Option(
        Path("output"), "--output-base-dir"
    ),
    mode_b: bool = typer.Option(False, "--mode-b/--mode-a"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
) -> None:
    """Generate concept-leap candidates and persist the top one."""

    target_path = _story_bible_path(
        slug, "concept-leap.json", output_base_dir, mode_b
    )
    if target_path.exists() and not overwrite:
        typer.echo(
            f"error: {target_path} already exists. Use --overwrite.", err=True
        )
        raise typer.Exit(code=1)

    result = generate_concept_leap(
        pools_per_candidate=pools_per_candidate,
        sample_size=sample_size,
        top_k=top_k,
        seed=seed,
    )

    if not result.candidates:
        typer.echo("error: concept leap produced no candidates.", err=True)
        raise typer.Exit(code=1)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json")
    target_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    typer.echo(f"concept leap written to {target_path}")
    typer.echo("")
    typer.echo(f"top {len(result.candidates)} candidates:")
    for i, candidate in enumerate(result.candidates, 1):
        typer.echo(f"\n— #{i} ——————")
        typer.echo(render_concept_candidate_block(candidate))


@book_app.command("plan-signatures")
def plan_signatures(
    slug: str = typer.Option(..., "--slug"),
    total: int = typer.Option(..., "--total", help="Target total chapter count."),
    cadence: int = typer.Option(
        10, "--cadence", help="Signature scenes every N chapters (default 10)."
    ),
    output_base_dir: Path = typer.Option(Path("output"), "--output-base-dir"),
    mode_b: bool = typer.Option(False, "--mode-b/--mode-a"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
) -> None:
    """Plan signature scenes for the whole book and persist."""

    target_path = _story_bible_path(
        slug, "signature-scene-plan.json", output_base_dir, mode_b
    )
    if target_path.exists() and not overwrite:
        typer.echo(
            f"error: {target_path} already exists. Use --overwrite.", err=True
        )
        raise typer.Exit(code=1)

    plan = plan_signature_scenes(total_chapters=total, cadence=cadence)
    path = save_signature_plan(
        plan, slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    typer.echo(f"signature scene plan written to {path}")
    typer.echo(f"  total chapters: {plan.total_chapters}, mandates: {len(plan.mandates)}")
    for mandate in plan.mandates:
        typer.echo(
            f"  ch{mandate.chapter_position:>4} : "
            f"{mandate.archetype.value:<20} stake={mandate.stake.value:<18} "
            f"intensity={mandate.intensity_target:.2f}"
        )


@book_app.command("prepare-chapter")
def prepare_chapter(
    slug: str = typer.Option(..., "--slug"),
    chapter: int = typer.Option(..., "--chapter"),
    target_length: int = typer.Option(
        0, "--target-length", help="Override chapter length target (0 = use band default)."
    ),
    output_base_dir: Path = typer.Option(Path("output"), "--output-base-dir"),
    mode_b: bool = typer.Option(False, "--mode-b/--mode-a"),
    language: str = typer.Option("zh-CN", "--language"),
) -> None:
    """Assemble and print the full chapter-prep block stack."""

    context = prepare_chapter_context(
        slug,
        chapter,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
        target_length=target_length or None,
    )

    typer.echo(f"=== chapter context for {slug} ch{chapter} ===")
    typer.echo(f"diagnostics: {json.dumps(context.diagnostics, ensure_ascii=False)}")
    typer.echo("")

    blocks = []
    if context.voice_dna is not None:
        blocks.append(render_voice_dna_block(context.voice_dna, language=language))
    if context.market_constraints is not None:
        blocks.append(
            render_chapter_constraints_block(
                context.market_constraints, language=language
            )
        )
    if context.signature_scene_mandate is not None:
        blocks.append(
            render_signature_scene_block(
                context.signature_scene_mandate, language=language
            )
        )
    if context.prior_persona_feedback is not None:
        blocks.append(
            render_persona_feedback_block(
                context.prior_persona_feedback, language=language
            )
        )

    if not blocks:
        typer.echo("(no context blocks present — run plan-concept / plan-signatures / "
                   "voice-dna extract first to populate)")
        return

    for block in blocks:
        typer.echo(block)
        typer.echo("")


@book_app.command("grade-chapter")
def grade_chapter_cmd(
    slug: str = typer.Option(..., "--slug"),
    chapter: int = typer.Option(..., "--chapter"),
    text_file: Path = typer.Option(..., "--text-file", "-f"),
    novelty_score: float = typer.Option(
        None, "--novelty", help="External novelty critic score in 0..1."
    ),
    consistency_score: float = typer.Option(
        None, "--consistency", help="External consistency critic score in 0..1."
    ),
    prose_quality_score: float = typer.Option(
        None, "--prose", help="External prose quality score in 0..1."
    ),
    output_base_dir: Path = typer.Option(Path("output"), "--output-base-dir"),
    mode_b: bool = typer.Option(False, "--mode-b/--mode-a"),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Whether to write the result to persona-feedback/.",
    ),
    language: str = typer.Option("zh-CN", "--language"),
) -> None:
    """Grade an already-written chapter against all reader personas."""

    if not text_file.exists():
        typer.echo(f"error: chapter text file not found: {text_file}", err=True)
        raise typer.Exit(code=2)
    try:
        chapter_text = text_file.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"error: failed to read {text_file}: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    context = prepare_chapter_context(
        slug, chapter, output_base_dir=output_base_dir, mode_b=mode_b
    )

    result = grade_chapter(
        context,
        chapter_text,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
        novelty_score=novelty_score,
        consistency_score=consistency_score,
        prose_quality_score=prose_quality_score,
        persist=persist,
    )

    typer.echo(f"=== persona verdict: {slug} ch{chapter} ===")
    typer.echo(
        f"weighted score: {result.weighted_score:.2f}  "
        f"abandon rate: {result.abandon_rate:.2f}"
    )
    if result.high_risk_personas:
        typer.echo("high-risk personas: " + "; ".join(result.high_risk_personas))
    typer.echo("")
    typer.echo(render_persona_feedback_block(result, language=language))
    if persist:
        typer.echo("")
        typer.echo("(feedback persisted; next chapter prep will pick this up)")


# ---------- internals ----------


def _story_bible_path(
    slug: str, filename: str, output_base_dir: Path, mode_b: bool
) -> Path:
    base = output_base_dir
    if mode_b:
        base = base / "ai-generated"
    return base / slug / "story-bible" / filename


__all__ = ["book_app"]

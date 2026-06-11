"""Chapter Orchestrator — the missing assembler.

Before this module existed, the framework had:
    extract_voice_dna_from_text(...)           # in voice_signature
    compile_chapter_constraints(...)           # in market_constraint_compiler
    simulate_readers(...)                      # in reader_persona_simulator
    plan_signature_scenes(...)                 # in signature_scene_planner
    build_chapter_prompt(...)                  # in prompt_constructor

…but *no one was calling them together*. This module bridges the gap.

The single entry point is ``prepare_chapter_context(slug, chapter_position)``
which returns a fully-assembled ``ChapterContext`` containing everything
``build_chapter_prompt`` needs as the new optional kwargs — plus the
signature-scene mandate for that chapter.

After the chapter is generated, the caller invokes
``grade_chapter(context, chapter_text, ...)`` which:
    1. builds a ChapterSignalPack from the text
    2. runs the persona simulator
    3. persists per-chapter feedback to disk
    4. returns the simulation result so the next chapter prep picks it up

This module is deterministic, no DB, no LLM. It composes pure functions
over file-backed artifacts. That keeps it cheap to test and easy to drop
into any pipeline (workflow.py, ad-hoc scripts, CLI demos).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from bestseller.domain.fanqie_market import FanqieMarketAnalysisBundle
from bestseller.domain.market_constraint import ChapterMarketConstraints
from bestseller.domain.reader_persona import PersonaSimulationResult
from bestseller.domain.signature_scene import (
    SignatureSceneMandate,
    SignatureScenePlan,
)
from bestseller.domain.voice_dna import VoiceDNA
from bestseller.services.chapter_signal_builder import build_signal_pack
from bestseller.services.hook_echo_gate import (
    HookEchoReport,
    check_hook_echo,
)
from bestseller.services.market_constraint_compiler import (
    compile_chapter_constraints,
)
from bestseller.services.persona_feedback_repository import (
    load_latest_feedback,
    save_chapter_feedback,
)
from bestseller.services.reader_persona_simulator import simulate_readers
from bestseller.services.signature_scene_planner import (
    plan_signature_scenes,
    render_signature_scene_block,
)
from bestseller.services.voice_dna_repository import (
    load_voice_dna,
    save_voice_dna,
)
from bestseller.services.voice_signature import extract_voice_dna_from_text

logger = logging.getLogger(__name__)


_SIGNATURE_PLAN_FILENAME = "signature-scene-plan.json"
_MARKET_BUNDLE_FILENAME = "fanqie-market-bundle.json"


@dataclass(frozen=True)
class ChapterContext:
    """Everything one chapter's prompt construction needs.

    The four fields below map 1:1 to the optional kwargs on
    ``build_chapter_prompt``:

        plan = build_chapter_prompt(
            invariants,
            budget,
            chapter_no=ctx.chapter_position,
            voice_dna=ctx.voice_dna,
            chapter_market_constraints=ctx.market_constraints,
            prior_persona_feedback=ctx.prior_persona_feedback,
            # signature-scene mandate is rendered separately for now —
            # consumers can concat it into a scene-spec section.
        )
    """

    slug: str
    chapter_position: int
    voice_dna: VoiceDNA | None = None
    market_constraints: ChapterMarketConstraints | None = None
    prior_persona_feedback: PersonaSimulationResult | None = None
    signature_scene_mandate: SignatureSceneMandate | None = None
    hook_echo_report: HookEchoReport | None = None

    diagnostics: dict[str, Any] = field(default_factory=dict)

    def signature_scene_block(self, *, language: str = "zh-CN") -> str:
        return render_signature_scene_block(
            self.signature_scene_mandate, language=language
        )

    def hook_echo_block(self, *, language: str = "zh-CN") -> str:
        if self.hook_echo_report is None:
            return ""
        return self.hook_echo_report.to_prompt_block(language=language)


def prepare_chapter_context(
    slug: str,
    chapter_position: int,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    target_length: int | None = None,
    extra_safety_notes: list[str] | None = None,
    prev_chapter_text: str | None = None,
    hook_domain_tokens: Sequence[str] = (),
) -> ChapterContext:
    """Load DNA, market bundle, signature plan, and prior feedback for one chapter.

    All file-based artifacts are optional — when one is missing, the
    corresponding field on ``ChapterContext`` is ``None`` and the caller
    simply skips that prompt block. This is by design: a fresh project
    that hasn't extracted DNA yet should still be able to write chapters,
    they just won't be voice-anchored.
    """

    if chapter_position < 1:
        raise ValueError("chapter_position must be >= 1")

    diagnostics: dict[str, Any] = {}

    voice_dna = load_voice_dna(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    diagnostics["voice_dna"] = "loaded" if voice_dna else "absent"

    market_bundle = _load_market_bundle(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if market_bundle is None:
        market_constraints = compile_chapter_constraints(
            None,
            chapter_position=chapter_position,
            target_length=target_length,
            extra_safety_notes=extra_safety_notes,
        )
        diagnostics["market_constraints"] = "compiled (no bundle)"
    else:
        market_constraints = compile_chapter_constraints(
            market_bundle,
            chapter_position=chapter_position,
            target_length=target_length,
            extra_safety_notes=extra_safety_notes,
        )
        diagnostics["market_constraints"] = "compiled from bundle"

    prior_feedback = load_latest_feedback(
        slug,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
        before_chapter=chapter_position,
    )
    diagnostics["prior_persona_feedback"] = (
        f"from ch-{prior_feedback.chapter_position}" if prior_feedback else "absent"
    )

    signature_plan = _load_signature_plan(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if signature_plan is None:
        signature_mandate: SignatureSceneMandate | None = None
        diagnostics["signature_scene"] = "no plan on disk"
    else:
        signature_mandate = signature_plan.mandate_for_chapter(chapter_position)
        diagnostics["signature_scene"] = (
            f"{signature_mandate.archetype.value}/{signature_mandate.stake.value}"
            if signature_mandate
            else "no mandate at this position"
        )

    # Hook Echo — chapters ≥ 2 only, requires prev chapter draft text.
    hook_echo_report: HookEchoReport | None = None
    if chapter_position >= 2 and prev_chapter_text:
        try:
            hook_echo_report = check_hook_echo(
                prev_chapter_text=prev_chapter_text,
                current_chapter_text="",
                current_chapter_position=chapter_position,
                prev_chapter_position=chapter_position - 1,
                extra_domain_tokens=hook_domain_tokens,
            )
            diagnostics["hook_echo"] = (
                f"{len(hook_echo_report.finding.prev_hook_tokens)} prev hook tokens extracted"
            )
        except Exception as exc:
            logger.warning("hook echo pre-write extraction failed: %s", exc)
            diagnostics["hook_echo"] = "extraction failed (non-fatal)"
    else:
        diagnostics["hook_echo"] = (
            "ch1 — no echo required"
            if chapter_position < 2
            else "no prev chapter text supplied"
        )

    return ChapterContext(
        slug=slug,
        chapter_position=chapter_position,
        voice_dna=voice_dna,
        market_constraints=market_constraints,
        prior_persona_feedback=prior_feedback,
        signature_scene_mandate=signature_mandate,
        hook_echo_report=hook_echo_report,
        diagnostics=diagnostics,
    )


def grade_chapter(
    context: ChapterContext,
    chapter_text: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    novelty_score: float | None = None,
    consistency_score: float | None = None,
    prose_quality_score: float | None = None,
    persist: bool = True,
) -> PersonaSimulationResult:
    """Run the persona simulator on a freshly-written chapter and persist.

    Returns the ``PersonaSimulationResult`` so the caller can decide
    whether to accept the chapter or trigger a rewrite based on
    ``weighted_score`` / ``abandon_rate``.
    """

    signal_pack = build_signal_pack(
        chapter_text,
        chapter_position=context.chapter_position,
        target_voice_dna=context.voice_dna,
        constraints=context.market_constraints,
        novelty_score=novelty_score,
        consistency_score=consistency_score,
        prose_quality_score=prose_quality_score,
    )
    result = simulate_readers(signal_pack)

    if persist:
        save_chapter_feedback(
            result,
            context.slug,
            output_base_dir=output_base_dir,
            mode_b=mode_b,
        )

    return result


def save_signature_plan(
    plan: SignatureScenePlan,
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> Path:
    """Persist a SignatureScenePlan to ``story-bible/signature-scene-plan.json``."""

    path = _signature_plan_path(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def save_market_bundle(
    bundle: FanqieMarketAnalysisBundle,
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> Path:
    """Persist a market bundle so prepare_chapter_context can find it."""

    path = _market_bundle_path(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.to_artifact_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def ensure_signature_plan(
    slug: str,
    *,
    total_chapters: int,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    cadence: int | None = None,
    anchor_images: Sequence[str] | None = None,
    anchor_lines: Sequence[str] | None = None,
) -> SignatureScenePlan | None:
    """Load the persisted signature plan, creating one when absent.

    The plan is deterministic (no LLM) so the platform pipeline can
    self-bootstrap it lazily instead of requiring the CLI ``book
    bootstrap`` step. An existing plan on disk is never overwritten —
    mandates must stay stable across chapters of the same book.
    Returns ``None`` only when no plan exists and ``total_chapters`` is
    too small to plan for.

    ``anchor_images`` / ``anchor_lines`` are book-derived verbatim anchor
    phrases (typically from the book's imagery system) baked into the
    mandates at creation time — the framework supplies no anchor content
    of its own.
    """

    existing = _load_signature_plan(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if existing is not None:
        return existing
    if total_chapters < 1:
        return None
    kwargs: dict[str, Any] = {"total_chapters": total_chapters}
    if cadence is not None and cadence >= 1:
        kwargs["cadence"] = cadence
    if anchor_images:
        kwargs["anchor_images"] = list(anchor_images)
    if anchor_lines:
        kwargs["anchor_lines"] = list(anchor_lines)
    plan = plan_signature_scenes(**kwargs)
    path = save_signature_plan(
        plan, slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    logger.info(
        "signature plan auto-bootstrapped for %s (%d mandates): %s",
        slug,
        len(plan.mandates),
        path,
    )
    return plan


def ensure_voice_dna(
    slug: str,
    *,
    sample_text: str,
    source_id: str,
    source_label: str = "",
    excluded_phrases: list[str] | None = None,
    min_sample_chars: int = 2000,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> VoiceDNA | None:
    """Load the persisted Voice DNA, self-extracting one when absent.

    Production projects have no external ``--reference`` corpus, so the
    anchor is the book's own earliest accepted prose: the first chapter
    whose text reaches ``min_sample_chars`` locks the voice that later
    chapters are held to. Existing DNA on disk is never overwritten.
    ``excluded_phrases`` should carry character names so they don't
    surface as catchphrases the writer is told to repeat.
    """

    existing = load_voice_dna(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if existing is not None:
        return existing
    text = (sample_text or "").strip()
    if len(text) < min_sample_chars:
        return None
    dna = extract_voice_dna_from_text(
        text,
        source_id=source_id,
        source_label=source_label or source_id,
        excluded_phrases=excluded_phrases,
    )
    if dna.sample_chars <= 0:
        return None
    path = save_voice_dna(
        dna, slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    logger.info(
        "voice DNA auto-bootstrapped for %s from %s (%d chars, confidence=%.2f): %s",
        slug,
        source_id,
        dna.sample_chars,
        dna.confidence,
        path,
    )
    return dna


# ---------- internals ----------


def _load_market_bundle(
    slug: str,
    *,
    output_base_dir: str | Path,
    mode_b: bool,
) -> FanqieMarketAnalysisBundle | None:
    path = _market_bundle_path(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("market bundle load failed (%s): %s", path, exc)
        return None
    try:
        return FanqieMarketAnalysisBundle.model_validate(raw)
    except Exception as exc:
        logger.warning("market bundle validation failed (%s): %s", path, exc)
        return None


def _load_signature_plan(
    slug: str,
    *,
    output_base_dir: str | Path,
    mode_b: bool,
) -> SignatureScenePlan | None:
    path = _signature_plan_path(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("signature plan load failed (%s): %s", path, exc)
        return None
    try:
        return SignatureScenePlan.model_validate(raw)
    except Exception as exc:
        logger.warning("signature plan validation failed (%s): %s", path, exc)
        return None


def _story_bible_dir(
    slug: str, *, output_base_dir: str | Path, mode_b: bool
) -> Path:
    base = Path(output_base_dir)
    if mode_b:
        base = base / "ai-generated"
    return base / slug / "story-bible"


def _signature_plan_path(
    slug: str, *, output_base_dir: str | Path, mode_b: bool
) -> Path:
    return _story_bible_dir(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    ) / _SIGNATURE_PLAN_FILENAME


def _market_bundle_path(
    slug: str, *, output_base_dir: str | Path, mode_b: bool
) -> Path:
    return _story_bible_dir(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    ) / _MARKET_BUNDLE_FILENAME


__all__ = [
    "ChapterContext",
    "prepare_chapter_context",
    "grade_chapter",
    "ensure_signature_plan",
    "ensure_voice_dna",
    "save_signature_plan",
    "save_market_bundle",
]

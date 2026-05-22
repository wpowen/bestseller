"""File-backed persistence for per-chapter Reader Persona Simulation results.

Layout:
    output/<slug>/knowledge/persona-feedback/after-ch-NNN.json

One file per chapter. ``after-ch-001.json`` is produced after chapter 1 is
generated and is consumed when chapter 2's prompt is constructed (via
``prior_persona_feedback=`` on ``build_chapter_prompt``).

This closes the feedback loop: every chapter is graded by 7 virtual readers
on disk, and the next chapter's prompt is conditioned on those grades.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from bestseller.domain.reader_persona import PersonaSimulationResult

logger = logging.getLogger(__name__)

_DEFAULT_SUBDIR = ("knowledge", "persona-feedback")
_FILENAME_TEMPLATE = "after-ch-{position:03d}.json"
_FILENAME_RE = re.compile(r"^after-ch-(\d+)\.json$")


def resolve_persona_feedback_dir(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> Path:
    """Return the directory holding persona feedback files for a project."""

    base = Path(output_base_dir)
    if mode_b:
        base = base / "ai-generated"
    return Path(base, slug, *_DEFAULT_SUBDIR)


def resolve_persona_feedback_path(
    slug: str,
    chapter_position: int,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> Path:
    """Return the per-chapter feedback file path."""

    if chapter_position < 1:
        raise ValueError("chapter_position must be >= 1")
    return resolve_persona_feedback_dir(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    ) / _FILENAME_TEMPLATE.format(position=chapter_position)


def save_chapter_feedback(
    result: PersonaSimulationResult,
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> Path:
    """Persist a PersonaSimulationResult for one chapter (atomic write)."""

    path = resolve_persona_feedback_path(
        slug,
        result.chapter_position,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)
    logger.info(
        "persona feedback saved: %s (weighted=%.2f abandon=%.2f)",
        path,
        result.weighted_score,
        result.abandon_rate,
    )
    return path


def load_chapter_feedback(
    slug: str,
    chapter_position: int,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> PersonaSimulationResult | None:
    """Load feedback for exactly one chapter, or None if absent/invalid."""

    path = resolve_persona_feedback_path(
        slug,
        chapter_position,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
    )
    return load_chapter_feedback_file(path)


def load_chapter_feedback_file(path: str | Path) -> PersonaSimulationResult | None:
    """Load feedback from an explicit file path."""

    effective = Path(path)
    if not effective.exists():
        return None
    try:
        raw = json.loads(effective.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("persona feedback load failed (%s): %s", effective, exc)
        return None
    try:
        return PersonaSimulationResult.model_validate(raw)
    except Exception as exc:
        logger.warning("persona feedback validation failed (%s): %s", effective, exc)
        return None


def load_latest_feedback(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    before_chapter: int | None = None,
) -> PersonaSimulationResult | None:
    """Return the most recent persisted feedback for a project.

    ``before_chapter`` lets the caller ask for "latest feedback strictly
    before chapter N" — useful when constructing chapter N's prompt.
    If ``before_chapter`` is None, returns the absolute latest feedback
    file found.
    """

    directory = resolve_persona_feedback_dir(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if not directory.exists():
        return None
    best: tuple[int, Path] | None = None
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        match = _FILENAME_RE.match(entry.name)
        if not match:
            continue
        position = int(match.group(1))
        if before_chapter is not None and position >= before_chapter:
            continue
        if best is None or position > best[0]:
            best = (position, entry)
    if best is None:
        return None
    return load_chapter_feedback_file(best[1])


def list_feedback_positions(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
) -> list[int]:
    """Return sorted list of chapter positions that have persisted feedback."""

    directory = resolve_persona_feedback_dir(
        slug, output_base_dir=output_base_dir, mode_b=mode_b
    )
    if not directory.exists():
        return []
    positions: list[int] = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        match = _FILENAME_RE.match(entry.name)
        if match:
            positions.append(int(match.group(1)))
    return sorted(positions)


__all__ = [
    "resolve_persona_feedback_dir",
    "resolve_persona_feedback_path",
    "save_chapter_feedback",
    "load_chapter_feedback",
    "load_chapter_feedback_file",
    "load_latest_feedback",
    "list_feedback_positions",
]

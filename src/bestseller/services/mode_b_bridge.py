"""Bridge between the Mode B dialogue orchestrator and the production pipeline.

Mode B (the "帮我写 N 章" conversational orchestrator) historically wrote
chapter markdown directly, bypassing every quality gate. That is the root
cause of "shell" books (template chapters, fake word counts). This bridge
lets the orchestrator drive the *real* ``run_chapter_pipeline`` for each
chapter and keeps ``progress.yaml`` in sync with database truth instead of
LLM self-reported numbers.

Responsibilities:
  * Resolve the Mode B package root ``output/ai-generated/{slug}/``.
  * Verify the project + chapter + scene cards exist in the database.
  * Run a single chapter through ``run_chapter_pipeline`` (gates + scoring).
  * Read back the authoritative word count / scores / verdict and write them
    into ``progress.yaml`` (truth source), never trusting dialogue self-fill.
  * Map a blocked / requires-human-review outcome to ``REWRITE_CHAPTER``.

This module is intentionally thin: it composes existing services. It does
NOT generate prose itself and does NOT materialize planning artifacts (that
remains an explicit, auditable step via the ``workflow materialize-*``
commands).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterModel, SceneCardModel
from bestseller.services.chapter_word_count_truth import (
    authoritative_zh_word_count,
)
from bestseller.services.pipelines import run_chapter_pipeline
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings

MODE_B_SUBDIR = "ai-generated"


class ModeBBridgeError(RuntimeError):
    """Raised when the Mode B package cannot be driven through the pipeline."""


@dataclass(frozen=True)
class ModeBChapterOutcome:
    """Result of driving one Mode B chapter through the pipeline."""

    chapter_number: int
    passed: bool
    requires_human_review: bool
    word_count: int
    verdict: str | None
    block_codes: tuple[str, ...]
    output_path: str | None
    next_state: str  # COMMIT_CHAPTER | REWRITE_CHAPTER

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "passed": self.passed,
            "requires_human_review": self.requires_human_review,
            "word_count": self.word_count,
            "verdict": self.verdict,
            "block_codes": list(self.block_codes),
            "output_path": self.output_path,
            "next_state": self.next_state,
        }


def resolve_mode_b_root(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
) -> Path:
    """Return ``output/ai-generated/{slug}/``."""

    return Path(output_base_dir) / MODE_B_SUBDIR / slug


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_progress_yaml(
    slug: str,
    outcome: ModeBChapterOutcome,
    *,
    output_base_dir: str | Path = "output",
    final_scores: dict[str, float] | None = None,
) -> Path | None:
    """Write pipeline truth back into ``progress.yaml`` (single source).

    Updates the chapter entry with the authoritative word count, scores,
    state and the next orchestrator state. Returns the path written, or
    ``None`` when ``progress.yaml`` is absent (nothing to sync).
    """

    root = resolve_mode_b_root(slug, output_base_dir=output_base_dir)
    path = root / "progress.yaml"
    if not path.is_file():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is corrupt: {exc}") from exc
    if not isinstance(data, dict):
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is not a mapping")

    chapters = data.setdefault("chapters", {})
    if not isinstance(chapters, dict):
        chapters = {}
        data["chapters"] = chapters

    key = f"{outcome.chapter_number:03d}"
    entry = chapters.get(key) if isinstance(chapters.get(key), dict) else {}
    entry["state"] = "committed" if outcome.passed else "rewriting"
    entry["word_count"] = outcome.word_count
    entry["verdict"] = outcome.verdict
    entry["block_codes"] = list(outcome.block_codes)
    entry["requires_human_review"] = outcome.requires_human_review
    if final_scores:
        entry["final_scores"] = final_scores
    if outcome.passed:
        entry["committed_at"] = _now_iso()
    chapters[key] = entry

    data["state"] = outcome.next_state
    data["current_chapter"] = outcome.chapter_number
    data["last_updated"] = _now_iso()

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def enqueue_repair_item(
    slug: str,
    *,
    affected_chapter: int,
    issue_type: str,
    description: str,
    output_base_dir: str | Path = "output",
    source_audit: str | None = None,
) -> Path | None:
    """Append a milestone/consistency repair item to ``progress.yaml``.

    Long-book milestone consistency failures must block advancement and be
    tracked until healed. Returns the path written, or ``None`` when
    ``progress.yaml`` is absent.
    """

    root = resolve_mode_b_root(slug, output_base_dir=output_base_dir)
    path = root / "progress.yaml"
    if not path.is_file():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is corrupt: {exc}") from exc
    if not isinstance(data, dict):
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is not a mapping")

    queue = data.get("repair_queue")
    if not isinstance(queue, list):
        queue = []
    next_id = f"R-{len(queue) + 1:03d}"
    queue.append(
        {
            "id": next_id,
            "created_at": _now_iso(),
            "source_audit": source_audit or f"milestone-ch-{affected_chapter:03d}",
            "issue_type": issue_type,
            "affected_chapter": affected_chapter,
            "description": description,
            "attempts": 0,
            "status": "pending",
        }
    )
    data["repair_queue"] = queue
    # A pending repair item must stop forward progress.
    data["state"] = "DRAIN_REPAIR_QUEUE"
    data["last_updated"] = _now_iso()

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


async def _load_chapter_block_codes(
    session: AsyncSession,
    *,
    project_id: Any,
    chapter_number: int,
) -> tuple[ChapterModel | None, tuple[str, ...]]:
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        return None, ()
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    codes = metadata.get("auto_repair_last_block_codes") or []
    if not codes and metadata.get("production_block_code"):
        codes = [metadata["production_block_code"]]
    return chapter, tuple(str(c) for c in codes if c)


async def drive_mode_b_chapter(
    session: AsyncSession,
    settings: AppSettings,
    slug: str,
    chapter_number: int,
    *,
    requested_by: str = "mode-b-orchestrator",
    chapter_first: bool = True,
) -> ModeBChapterOutcome:
    """Run one Mode B chapter through the production pipeline with gates.

    Raises ``ModeBBridgeError`` when the project / chapter / scene cards are
    missing — the orchestrator must materialize planning artifacts first
    (``bestseller workflow materialize-story-bible / materialize-outline``).
    """

    project = await get_project_by_slug(session, slug)
    if project is None:
        raise ModeBBridgeError(
            f"Mode B project '{slug}' has no database record. Run "
            f"`bestseller project create {slug} ...` and "
            f"`bestseller workflow materialize-story-bible/outline {slug}` first."
        )

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ModeBBridgeError(
            f"Chapter {chapter_number} not materialized for '{slug}'. "
            f"Run outline materialization before driving the pipeline."
        )

    scene_count = await session.scalar(
        select(SceneCardModel.id)
        .where(SceneCardModel.chapter_id == chapter.id)
        .limit(1)
    )
    if scene_count is None:
        raise ModeBBridgeError(
            f"Chapter {chapter_number} of '{slug}' has no scene cards. "
            f"Materialize the chapter outline before writing."
        )

    result = await run_chapter_pipeline(
        session,
        settings,
        slug,
        chapter_number,
        requested_by=requested_by,
        export_markdown=True,
        chapter_first=chapter_first,
    )

    chapter_after, block_codes = await _load_chapter_block_codes(
        session,
        project_id=project.id,
        chapter_number=chapter_number,
    )

    word_count = int(getattr(chapter_after, "current_word_count", 0) or 0)
    if word_count <= 0 and result.output_path:
        try:
            body = Path(result.output_path).read_text(encoding="utf-8")
            word_count = authoritative_zh_word_count(
                body, language=str(getattr(project, "language", None) or "zh-CN")
            )
        except OSError:
            word_count = 0

    passed = (
        not result.requires_human_review
        and result.final_verdict == "pass"
        and not block_codes
    )
    next_state = "COMMIT_CHAPTER" if passed else "REWRITE_CHAPTER"

    return ModeBChapterOutcome(
        chapter_number=chapter_number,
        passed=passed,
        requires_human_review=bool(result.requires_human_review),
        word_count=word_count,
        verdict=result.final_verdict,
        block_codes=block_codes,
        output_path=result.output_path,
        next_state=next_state,
    )


__all__ = [
    "MODE_B_SUBDIR",
    "ModeBBridgeError",
    "ModeBChapterOutcome",
    "drive_mode_b_chapter",
    "enqueue_repair_item",
    "resolve_mode_b_root",
    "sync_progress_yaml",
]

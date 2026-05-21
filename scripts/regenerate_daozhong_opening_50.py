"""Regenerate chapters 1-50 of 道种破虚 using the full system pipeline.

This script calls ``run_project_pipeline()`` directly — the same function
used by the web UI and CLI — to regenerate the first 50 chapters with the
complete L1-L8 quality gate chain (contradiction detection, POV lock,
dialog integrity, canon guardrails, cliffhanger rotation, hype engine,
regen loops, review/rewrite loops, and scorecard).

Prerequisites:
  - PostgreSQL must be running (``pg_isready``)
  - Chapters 1-50 must be reset to status=revision / production_state=pending
    and their scene cards to status=needs_rewrite (use
    ``scripts/reset_chapters_db.py`` first).

Usage:
    uv run python scripts/regenerate_daozhong_opening_50.py
    uv run python scripts/regenerate_daozhong_opening_50.py --start 1 --end 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import AsyncSession

from src.bestseller.domain.pipeline import ProjectPipelineResult
from src.bestseller.infra.db.session import session_scope
from src.bestseller.services.pipelines import run_project_pipeline
from src.bestseller.services.projects import get_project_by_slug
from src.bestseller.settings import load_settings

logger = logging.getLogger(__name__)


PROJECT_SLUG = "xianxia-upgrade-1776137730"


def _emit_progress(
    event: str,
    data: dict,
    *,
    start_time: float | None = None,
) -> None:
    """Print pipeline progress to stdout."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if event == "chapter_pipeline_started":
        ch = data.get("chapter_number", "?")
        target = data.get("target_word_count", 0)
        progress = data.get("progress", "?/?")
        print(f"[{ts}] ch{ch:03d} START  (target={target} words, progress={progress})", flush=True)
    elif event == "chapter_pipeline_completed":
        ch = data.get("chapter_number", "?")
        result = data.get("result", "?")
        elapsed = ""
        if start_time:
            elapsed = f" {time.monotonic() - start_time:.1f}s"
        print(f"[{ts}] ch{ch:03d} DONE   result={result}{elapsed}", flush=True)
    elif event == "project_pipeline_completed":
        total = data.get("total_chapters", 0)
        ok = data.get("ok_count", 0)
        blocked = data.get("blocked_count", 0)
        print(f"\n[{ts}] PIPELINE COMPLETE: {ok}/{total} ok, {blocked} blocked", flush=True)
    elif event == "resume_skipped_chapters":
        skipped = data.get("skipped_count", 0)
        pending = data.get("pending_count", 0)
        print(f"[{ts}] Resume: {skipped} skipped, {pending} pending", flush=True)


def _progress_callback(event: str, data: dict | None = None) -> None:
    """Bridge from pipeline progress to stdout."""
    _emit_progress(event, data or {})


async def run(args: argparse.Namespace) -> int:
    os.chdir(Path(__file__).resolve().parents[1])
    settings = load_settings()

    print(f"Project: {PROJECT_SLUG}")
    print(f"Chapter range: {args.start}–{args.end}")
    print(f"Pipeline settings:")
    print(f"  resume_enabled: {settings.pipeline.resume_enabled}")
    print(f"  accept_on_stall: {settings.pipeline.accept_on_stall}")
    print(f"  max_scene_revisions: {settings.quality.max_scene_revisions}")
    print(f"  max_chapter_revisions: {settings.quality.max_chapter_revisions}")
    print(f"  consistency_check_interval: {settings.pipeline.consistency_check_interval}")
    print(f"  LLM writer model: {settings.llm.writer.model}")
    print()

    chapter_numbers = set(range(args.start, args.end + 1))
    started_at = time.monotonic()

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, PROJECT_SLUG)
        if project is None:
            print(f"ERROR: Project '{PROJECT_SLUG}' not found in database.")
            return 1

        print(f"Project ID: {project.id}")
        print(f"Project status: {project.status}")
        print(f"Target chapters: {project.target_chapters}")
        print()

        result: ProjectPipelineResult = await run_project_pipeline(
            session,
            settings,
            PROJECT_SLUG,
            materialize_story_bible=False,
            materialize_outline=False,
            materialize_narrative_graph=True,
            materialize_narrative_tree=True,
            export_markdown=True,
            progress=_progress_callback,
            chapter_numbers=chapter_numbers,
        )

    elapsed = time.monotonic() - started_at
    print(f"\nTotal elapsed: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"Requires machine repair: {result.requires_human_review}")
    print(f"Chapters processed: {len(result.chapter_results)}")

    ok_count = sum(
        1 for c in result.chapter_results if not c.requires_human_review
    )
    blocked_count = sum(
        1 for c in result.chapter_results if c.requires_human_review
    )
    print(f"OK: {ok_count}, Blocked/Review: {blocked_count}")

    if blocked_count > 0:
        print("\nChapters requiring attention:")
        for c in result.chapter_results:
            if c.requires_human_review:
                print(f"  ch{c.chapter_number:03d}")

    return 0 if blocked_count == 0 else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=50)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

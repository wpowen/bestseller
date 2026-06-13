#!/usr/bin/env python3
"""Real-model single-scene generator for the AI-flavor optimisation loop.

Unlike ``dry_run_one_scene.py`` (which mocks the LLM and only inspects prompt
assembly), this harness runs the *real* writer model through the production
``generate_scene_draft`` path, captures the generated prose, then rolls the DB
back so nothing is persisted. The prose is written to
``tmp/ai_flavor_loop/<label>/ch<C>-s<S>.md`` so the same scenes can be
regenerated under different prompt variants and compared with
``scripts/ai_flavor_diagnose.py``.

Usage:
    python scripts/ai_flavor_loop_gen.py --slug shilouyan-bench-v1 \
        --label baseline --scenes 1:1 2:1 3:1

Each ``C:S`` pair is chapter:scene. Output is one .md per scene under the label
dir, plus a manifest line per scene (word_count, elapsed).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.drafts import generate_scene_draft
from bestseller.settings import load_settings


async def _gen_one(session_factory, slug: str, chapter: int, scene: int) -> tuple[str, int]:
    """Generate one scene with the real model; return (content_md, word_count).

    DB writes are rolled back — this never persists a draft.
    """
    async with session_factory() as session:
        try:
            draft = await generate_scene_draft(
                session,
                slug,
                chapter,
                scene,
                workflow_run_id=None,
                step_run_id=None,
            )
            content = draft.content_md or ""
            words = draft.word_count or len(content)
            return content, words
        finally:
            await session.rollback()


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    os.environ["BESTSELLER_TRACE_SCENE_PROMPTS"] = "full"
    engine = create_engine(settings)
    session_factory = create_session_factory(engine=engine)

    out_dir = Path("tmp/ai_flavor_loop") / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        for pair in args.scenes:
            chapter_str, _, scene_str = pair.partition(":")
            chapter = int(chapter_str)
            scene = int(scene_str or "1")
            t0 = time.monotonic()
            try:
                content, words = await _gen_one(session_factory, args.slug, chapter, scene)
            except Exception as exc:  # noqa: BLE001 — harness: report and continue
                print(f"ch{chapter}-s{scene}: FAILED {type(exc).__name__}: {exc}")
                continue
            elapsed = time.monotonic() - t0
            path = out_dir / f"ch{chapter:03d}-s{scene:02d}.md"
            path.write_text(content, encoding="utf-8")
            print(f"ch{chapter}-s{scene}: {words} words, {elapsed:.0f}s -> {path}")
    finally:
        await engine.dispose()
    print(f"\nwrote scenes under {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--label", required=True, help="output subdir under tmp/ai_flavor_loop/")
    parser.add_argument(
        "--scenes",
        nargs="+",
        required=True,
        help="chapter:scene pairs, e.g. 1:1 2:1 3:1",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

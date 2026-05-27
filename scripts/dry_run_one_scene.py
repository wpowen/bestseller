#!/usr/bin/env python3
"""Run one scene prompt assembly with mocked LLM completion and dump a trace.

The script rolls back DB changes after prompt assembly. The trace file is still
written to output/<slug>/traces because prompt tracing is filesystem based.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.infra.db.session import create_engine, create_session_factory
import bestseller.services.drafts as drafts
from bestseller.services.drafts import generate_scene_draft
from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult
from bestseller.settings import load_settings


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.output_base_dir:
        settings = settings.model_copy(
            update={
                "output": settings.output.model_copy(
                    update={"base_dir": str(Path(args.output_base_dir))}
                )
            },
            deep=True,
        )

    os.environ["BESTSELLER_TRACE_SCENE_PROMPTS"] = "full"
    engine = create_engine(settings)
    session_factory = create_session_factory(engine=engine)
    trace_before = set(
        (Path(settings.output.base_dir) / args.slug / "traces").glob("scene-prompt-*.json")
    )

    async with session_factory() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == args.slug))
        if project is None:
            raise ValueError(f"Project not found: {args.slug}")
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == args.chapter,
            )
        )
        if chapter is None:
            raise ValueError(f"Chapter not found: {args.chapter}")
        scene = await session.scalar(
            select(SceneCardModel).where(
                SceneCardModel.chapter_id == chapter.id,
                SceneCardModel.scene_number == args.scene,
            )
        )
        if scene is None:
            raise ValueError(f"Scene not found: {args.chapter}.{args.scene}")

        async def fake_complete_text(
            _session: object,
            _settings: object,
            request: LLMCompletionRequest,
        ) -> LLMCompletionResult:
            content = request.fallback_response
            if request.prompt_template == "prewrite_plan_manifest":
                content = request.fallback_response
            return LLMCompletionResult(
                content=content,
                provider="mock",
                model_name="mock-runtime-verifier",
                llm_run_id=uuid4(),
                input_tokens=len(request.system_prompt + request.user_prompt) // 2,
                output_tokens=len(content) // 2,
                finish_reason="mocked",
            )

        original_complete_text = drafts.complete_text
        drafts.complete_text = fake_complete_text
        try:
            await generate_scene_draft(
                session,
                args.slug,
                args.chapter,
                args.scene,
                settings=settings,
                workflow_run_id=None,
                step_run_id=None,
            )
        finally:
            drafts.complete_text = original_complete_text
            await session.rollback()
            await engine.dispose()

    trace_dir = Path(settings.output.base_dir) / args.slug / "traces"
    trace_after = set(trace_dir.glob("scene-prompt-*.json"))
    created = sorted(trace_after - trace_before)
    latest = created[-1] if created else max(trace_after, default=None)
    if latest is None:
        print(f"FAIL no trace written under {trace_dir}")
        return 1
    print(SimpleNamespace(trace=str(latest), rolled_back=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--scene", type=int, default=1)
    parser.add_argument("--output-base-dir", default=None)
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

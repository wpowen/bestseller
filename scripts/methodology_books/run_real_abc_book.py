"""Real-pipeline A/B validation driver for the methodology lineage spine.

Unlike ``run_short_story_pilot.py`` (a standalone mock that fabricates
fallback text and adds hardcoded per-group score bonuses), this driver
calls the REAL ``run_autowrite_pipeline`` — the same function the worker
uses — with the REAL LLM. The methodology v2 system (lineage spine +
selection engine + hook/payoff ledger) is gated by the
``BESTSELLER_METHODOLOGY_V2`` env var, read at runtime, so an A/B is
achieved by running this script twice with the flag pinned to 0 vs 1.

It creates a brand-new throwaway project (so it never touches in-flight
books), generates N chapters, then reads back from the DB:
  * whether each chapter actually carries ``methodology_lineage``
  * how many methodology rules were selected per chapter
  * any review / quality score found in chapter metadata

Run inside a worker container (DB host ``db`` + LLM env + new code):
    docker cp scripts/methodology_books/run_real_abc_book.py <worker>:/tmp/d.py
    docker exec -e BESTSELLER_METHODOLOGY_V2=0 <worker> \
        python /tmp/d.py --slug abc-real-a --chapters 4
    docker exec -e BESTSELLER_METHODOLOGY_V2=1 <worker> \
        python /tmp/d.py --slug abc-real-b --chapters 4
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bestseller.domain.project import ProjectCreate, ProjectType
from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import ChapterModel, WorkflowRunModel
from bestseller.infra.db.session import session_scope
from bestseller.services.pipelines import run_autowrite_pipeline
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import load_settings

DEFAULT_GENRE = "都市悬疑"
DEFAULT_SUB_GENRE = "轻玄幻"
DEFAULT_PREMISE = (
    "林渊是城南一家昼夜诊所的夜班医生。父亲十年前因一桩'旧账'离奇失踪，"
    "只留下一枚缺角铜钱和一本记着陌生人名字的账册。一个深夜，一名浑身湿透、"
    "攥着同样缺角铜钱的女人冲进诊所求救，声称'收账的人'已经找到她。"
    "林渊发现铜钱的缺口能在月光下显出牙印般的纹路，每当账册翻页，"
    "就有一个名字从这世上被悄悄抹去。他必须在天亮前查清父亲的旧账，"
    "否则下一个被抹掉的名字，就是他自己。"
)


def _progress(event: str, data: dict | None = None) -> None:
    data = data or {}
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if event == "chapter_pipeline_started":
        print(
            f"[{ts}] ch{data.get('chapter_number', '?')} START "
            f"(progress={data.get('progress', '?')})",
            flush=True,
        )
    elif event == "chapter_pipeline_completed":
        print(
            f"[{ts}] ch{data.get('chapter_number', '?')} DONE "
            f"result={data.get('result', '?')}",
            flush=True,
        )


def _default_output_root() -> Path:
    configured = os.environ.get("BESTSELLER_REAL_ABC_OUTPUT_ROOT")
    if configured:
        return Path(configured)
    if Path("/app").exists():
        return Path("/app/output")
    return Path.cwd() / "output"


def _apply_runtime_overrides(settings: Any, args: argparse.Namespace) -> None:
    if args.llm_timeout_seconds is not None:
        for role_name in ("planner", "writer", "critic", "summarizer", "editor"):
            getattr(settings.llm, role_name).timeout_seconds = args.llm_timeout_seconds
    if args.planner_timeout_seconds is not None:
        settings.llm.planner.timeout_seconds = args.planner_timeout_seconds
    if args.writer_timeout_seconds is not None:
        settings.llm.writer.timeout_seconds = args.writer_timeout_seconds
    if args.critic_timeout_seconds is not None:
        settings.llm.critic.timeout_seconds = args.critic_timeout_seconds
    if args.retry_max_attempts is not None:
        settings.llm.retry.max_attempts = args.retry_max_attempts
    if args.rate_limit_max_attempts is not None:
        settings.llm.retry.rate_limit_max_attempts = args.rate_limit_max_attempts


def _target_word_count_for_validation(settings: Any, chapters: int) -> int:
    chapter_count = max(int(chapters or 0), 1)
    chapter_budget = getattr(settings.generation, "words_per_chapter", None)
    min_words = int(getattr(chapter_budget, "min", 5000) or 5000)
    target_words = int(getattr(chapter_budget, "target", min_words) or min_words)
    return chapter_count * max(min_words, target_words)


async def _monitor_run(
    settings: Any,
    slug: str,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float,
) -> None:
    started = time.monotonic()
    while not stop_event.is_set():
        await asyncio.sleep(interval_seconds)
        if stop_event.is_set():
            break
        elapsed = time.monotonic() - started
        try:
            async with session_scope(settings) as session:
                project = await get_project_by_slug(session, slug)
                if project is None:
                    print(
                        f"[monitor +{elapsed:.0f}s] project not created yet",
                        flush=True,
                    )
                    continue
                chapters = (
                    await session.execute(
                        select(ChapterModel.status)
                        .where(ChapterModel.project_id == project.id)
                        .order_by(ChapterModel.chapter_number)
                    )
                ).scalars().all()
                status_counts: dict[str, int] = {}
                for status in chapters:
                    status_counts[str(status)] = status_counts.get(str(status), 0) + 1
                print(
                    f"[monitor +{elapsed:.0f}s] project={project.status} "
                    f"chapters={len(chapters)} statuses={status_counts}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001 - monitor must not kill validation
            print(
                f"[monitor +{elapsed:.0f}s] monitor_error={type(exc).__name__}: {exc}",
                flush=True,
            )


async def _mark_running_workflows_failed(
    settings: Any,
    slug: str,
    *,
    message: str,
) -> None:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return
        metadata = dict(project.metadata_json or {})
        now = datetime.now(timezone.utc).isoformat()
        metadata.update(
            {
                "validation_run_timed_out": True,
                "validation_timeout_at": now,
                "validation_timeout_reason": message,
                "production_paused": True,
                "production_pause_reason": "validation_watchdog_timeout",
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.PAUSED.value
        runs = (
            await session.execute(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.status == WorkflowStatus.RUNNING.value,
                )
            )
        ).scalars().all()
        for run in runs:
            run.status = WorkflowStatus.FAILED.value
            run.error_message = message
        await session.flush()


def _extract_lineage(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pull methodology_lineage info out of chapter metadata, defensively."""
    lineage = metadata.get("methodology_lineage")
    if not isinstance(lineage, dict):
        return {"present": False, "selected_count": 0, "slots": [], "rule_ids": []}
    selected = lineage.get("selected") or []
    slots = sorted({str(item.get("slot")) for item in selected if isinstance(item, dict)})
    rule_ids = [str(item.get("rule_id")) for item in selected if isinstance(item, dict)]
    return {
        "present": True,
        "chapter_role": lineage.get("chapter_role"),
        "genre_profile": lineage.get("genre_profile"),
        "selected_count": len(selected),
        "slots": slots,
        "rule_ids": rule_ids,
    }


def _extract_scores(metadata: dict[str, Any]) -> dict[str, Any]:
    """Best-effort: surface any review/quality/score fields in metadata."""
    found: dict[str, Any] = {}
    for key, value in metadata.items():
        lk = key.lower()
        if any(tok in lk for tok in ("score", "review", "quality", "hook_ledger", "payoff")):
            # only keep JSON-serializable scalar/small structures
            try:
                json.dumps(value)
                found[key] = value
            except (TypeError, ValueError):
                found[key] = str(value)[:200]
    return found


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    _apply_runtime_overrides(settings, args)
    v2 = os.environ.get("BESTSELLER_METHODOLOGY_V2", "0")
    print(f"=== REAL A/B driver === slug={args.slug} chapters={args.chapters} "
          f"BESTSELLER_METHODOLOGY_V2={v2}", flush=True)
    print(f"LLM mock={settings.llm.mock} writer_model={settings.llm.writer.model}", flush=True)
    print(
        "timeouts: "
        f"planner={settings.llm.planner.timeout_seconds}s "
        f"writer={settings.llm.writer.timeout_seconds}s "
        f"critic={settings.llm.critic.timeout_seconds}s "
        f"pipeline={args.pipeline_timeout_seconds or 'none'}s "
        "retries: "
        f"max={settings.llm.retry.max_attempts} "
        f"rate_limit={settings.llm.retry.rate_limit_max_attempts}",
        flush=True,
    )

    payload = ProjectCreate(
        slug=args.slug,
        title=f"雨夜旧账·{args.slug}",
        genre=DEFAULT_GENRE,
        sub_genre=DEFAULT_SUB_GENRE,
        audience="男频都市悬疑读者",
        language="zh-CN",
        target_word_count=_target_word_count_for_validation(settings, args.chapters),
        target_chapters=args.chapters,
        project_type=ProjectType.LINEAR,
        metadata={"validation_run": "methodology_abc_real", "v2_flag": v2},
    )

    started = time.monotonic()
    pipeline_error: str | None = None
    stop_monitor = asyncio.Event()
    monitor_task: asyncio.Task[None] | None = None
    try:
        if args.monitor_interval_seconds > 0:
            monitor_task = asyncio.create_task(
                _monitor_run(
                    settings,
                    args.slug,
                    stop_monitor,
                    interval_seconds=args.monitor_interval_seconds,
                )
            )
        async with session_scope(settings) as session:
            pipeline_coro = run_autowrite_pipeline(
                session=session,
                settings=settings,
                project_payload=payload,
                premise=DEFAULT_PREMISE,
                export_markdown=True,
                progress=_progress,
            )
            if args.pipeline_timeout_seconds and args.pipeline_timeout_seconds > 0:
                await asyncio.wait_for(
                    pipeline_coro,
                    timeout=args.pipeline_timeout_seconds,
                )
            else:
                await pipeline_coro
    except asyncio.TimeoutError:
        pipeline_error = (
            "TimeoutError: pipeline exceeded "
            f"{args.pipeline_timeout_seconds}s watchdog"
        )
        print(f"\n=== pipeline timed out: {pipeline_error} ===", flush=True)
    except Exception as exc:  # noqa: BLE001 - always proceed to readback
        pipeline_error = f"{type(exc).__name__}: {exc}"
        print(f"\n=== pipeline raised (will still read back): {pipeline_error} ===", flush=True)
    finally:
        stop_monitor.set()
        if monitor_task is not None:
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(monitor_task, timeout=2.0)
        if pipeline_error and pipeline_error.startswith("TimeoutError:"):
            with suppress(Exception):
                await _mark_running_workflows_failed(
                    settings,
                    args.slug,
                    message=pipeline_error,
                )
    elapsed = time.monotonic() - started
    print(f"\n=== pipeline finished in {elapsed:.0f}s ({elapsed/60:.1f}m) ===", flush=True)

    # Read back chapter-level evidence.
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, args.slug)
        if project is None:
            print("ERROR: project not found after run", flush=True)
            return 1
        rows = (
            await session.execute(
                select(ChapterModel)
                .where(ChapterModel.project_id == project.id)
                .order_by(ChapterModel.chapter_number)
            )
        ).scalars().all()

        chapters_report: list[dict[str, Any]] = []
        for ch in rows:
            meta = ch.metadata_json or {}
            chapters_report.append(
                {
                    "chapter_number": ch.chapter_number,
                    "status": ch.status,
                    "word_count": getattr(ch, "word_count", None),
                    "lineage": _extract_lineage(meta),
                    "scores": _extract_scores(meta),
                }
            )

    report = {
        "slug": args.slug,
        "v2_flag": v2,
        "genre": f"{DEFAULT_GENRE}+{DEFAULT_SUB_GENRE}",
        "elapsed_seconds": round(elapsed, 1),
        "chapter_count": len(chapters_report),
        "lineage_present_count": sum(
            1 for c in chapters_report if c["lineage"]["present"]
        ),
        "chapters": chapters_report,
        "pipeline_error": pipeline_error,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    out_dir = args.output_root / f"methodology-abc-real-{args.slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "result.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== report written: {out_path} ===", flush=True)
    print(
        f"lineage present: {report['lineage_present_count']}/{report['chapter_count']} chapters",
        flush=True,
    )
    for c in chapters_report:
        lin = c["lineage"]
        print(
            f"  ch{c['chapter_number']}: status={c['status']} "
            f"lineage={'Y' if lin['present'] else 'N'} "
            f"slots={lin['selected_count']} {lin['slots']}",
            flush=True,
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--chapters", type=int, default=4)
    p.add_argument(
        "--output-root",
        type=Path,
        default=_default_output_root(),
        help="Directory where methodology-abc-real-<slug>/result.json is written.",
    )
    p.add_argument(
        "--pipeline-timeout-seconds",
        type=int,
        default=0,
        help="Wall-clock watchdog for the full pipeline. 0 disables it.",
    )
    p.add_argument(
        "--monitor-interval-seconds",
        type=float,
        default=60.0,
        help="DB progress logging interval. 0 disables monitor logging.",
    )
    p.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=None,
        help="Override all role LLM timeouts for this validation run.",
    )
    p.add_argument("--planner-timeout-seconds", type=int, default=None)
    p.add_argument("--writer-timeout-seconds", type=int, default=None)
    p.add_argument("--critic-timeout-seconds", type=int, default=None)
    p.add_argument("--retry-max-attempts", type=int, default=None)
    p.add_argument("--rate-limit-max-attempts", type=int, default=None)
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))

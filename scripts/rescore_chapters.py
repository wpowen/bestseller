"""Re-run L5 ``validate_chapter`` against current drafts and insert fresh
``chapter_quality_reports`` rows.

When chapters are repaired via the disk-ingest path
(``scripts/ingest_disk_drafts.py``) or after a positioning respec
(``scripts/respec_project_keywords.py``), the ``chapter_quality_reports``
table still holds the OLD violations. ``scorecard``'s ``golden_three_weak``
field reads from those stale rows, so the scorecard stays red even when the
current draft now passes.

This script re-runs the L5 validator on the current draft for each chapter in
the requested range and appends a fresh quality report row. It is purely
additive: prior rows are kept for history.

Usage:
    uv run python scripts/rescore_chapters.py --slug <slug>                       # dry-run
    uv run python scripts/rescore_chapters.py --slug <slug> --from 1 --to 10 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.services.chapter_validator import (
    ValidationContext,
    validate_chapter,
)
from bestseller.services.invariants import (
    invariants_from_dict,
    seed_invariants,
)


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get(
        "BESTSELLER_DATABASE_URL"
    ) or "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller"
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql+psycopg://"):
        raw = raw.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return raw


async def _load_invariants(session, project_id, project_row):
    payload = project_row.invariants_json
    if payload:
        try:
            return invariants_from_dict(payload)
        except Exception:  # noqa: BLE001
            pass
    target_chapters = max(int(project_row.target_chapters or 1), 1)
    target_words = max(int(project_row.target_word_count or 0), 0)
    per_chapter = target_words // target_chapters if target_words else 2200
    words = SimpleNamespace(
        min=1800,
        target=min(max(per_chapter or 2200, 1801), 2999),
        max=3000,
    )
    return seed_invariants(
        project_id=project_id,
        language=project_row.language or "zh-CN",
        words_per_chapter=words,
        pov="close_third",
    )


async def _allowed_names(session, project_id) -> frozenset[str]:
    rows = (
        await session.execute(
            text(
                "SELECT name, metadata FROM characters "
                "WHERE project_id = :pid"
            ),
            {"pid": project_id},
        )
    ).all()
    names: set[str] = set()
    for r in rows:
        if r.name and r.name.strip():
            names.add(r.name.strip())
        md = r.metadata or {}
        if isinstance(md, dict):
            aliases = md.get("aliases")
            if isinstance(aliases, str) and aliases.strip():
                names.add(aliases.strip())
            elif isinstance(aliases, list):
                for a in aliases:
                    if isinstance(a, str) and a.strip():
                        names.add(a.strip())
    return frozenset(names)


async def _run(args: argparse.Namespace) -> int:
    engine = create_async_engine(_dsn(), future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        project_row = (
            await session.execute(
                text(
                    "SELECT id, slug, language, target_chapters, "
                    "target_word_count, invariants_json "
                    "FROM projects WHERE slug = :slug"
                ),
                {"slug": args.slug},
            )
        ).first()
        if project_row is None:
            print(f"[error] project not found: {args.slug}", file=sys.stderr)
            return 2

        project_id = project_row.id

        invariants = await _load_invariants(session, project_id, project_row)
        allowed = await _allowed_names(session, project_id)

        rows = (
            await session.execute(
                text(
                    "SELECT c.id, c.chapter_number, cdv.content_md "
                    "FROM chapters c "
                    "JOIN chapter_draft_versions cdv "
                    "  ON cdv.chapter_id = c.id AND cdv.is_current = TRUE "
                    "WHERE c.project_id = :pid "
                    "AND c.chapter_number BETWEEN :lo AND :hi "
                    "ORDER BY c.chapter_number"
                ),
                {
                    "pid": project_id,
                    "lo": args.from_chapter,
                    "hi": args.to_chapter,
                },
            )
        ).all()

        summary: list[dict[str, object]] = []
        for row in rows:
            ctx = ValidationContext(
                invariants=invariants,
                chapter_no=int(row.chapter_number),
                scope="chapter",
                allowed_names=allowed,
            )
            report = validate_chapter(row.content_md or "", ctx)
            block_codes = tuple(
                v.code for v in report.violations if v.severity == "block"
            )
            warn_codes = tuple(
                v.code for v in report.violations if v.severity == "warn"
            )
            payload = {
                "violations": [
                    {
                        "code": v.code,
                        "severity": v.severity,
                        "location": v.location,
                        "detail": v.detail,
                    }
                    for v in report.violations
                ],
                "blocking_codes": list(block_codes),
                "rescore_source": "scripts/rescore_chapters.py",
            }
            if args.apply:
                await session.execute(
                    text(
                        "INSERT INTO chapter_quality_reports "
                        "(id, chapter_id, report_json, regen_attempts, "
                        " blocks_write, created_at) "
                        "VALUES (gen_random_uuid(), :cid, "
                        " CAST(:payload AS jsonb), 0, :blocks, NOW())"
                    ),
                    {
                        "cid": row.id,
                        "payload": json.dumps(payload),
                        "blocks": bool(block_codes),
                    },
                )
            summary.append(
                {
                    "chapter_number": row.chapter_number,
                    "violations": len(report.violations),
                    "block_codes": list(block_codes),
                    "warn_codes": list(warn_codes),
                }
            )

        if args.apply:
            await session.commit()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] slug={args.slug} chapters_scored={len(summary)}")
    for s in summary:
        ch = s["chapter_number"]
        v = s["violations"]
        b = s["block_codes"]
        w = s["warn_codes"]
        flag = "✅ PASS" if not b and not w else (
            "🚫 BLOCK" if b else "⚠️  WARN"
        )
        print(f"  ch{ch:>3d}  {flag}  v={v}  block={b}  warn={w}")

    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--from", dest="from_chapter", type=int, default=1)
    ap.add_argument("--to", dest="to_chapter", type=int, default=10_000)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

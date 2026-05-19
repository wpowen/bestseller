"""Ingest manually-edited disk markdown back into ``chapter_draft_versions``.

The framework writes ``output/<slug>/chapter-NNN.md`` eagerly from each
generated draft (see ``drafts.py:5651-5674``). When a human or external tool
rewrites the disk markdown directly, the DB-side ``chapter_draft_versions``,
``chapter_quality_reports`` and ``novel_scorecards`` never see the change —
auto-evaluation silently scores stale content.

This one-shot helper diffs disk vs current draft per chapter, and when they
differ it appends a new draft version with ``is_current=True`` so downstream
quality reports and scorecards re-evaluate the fresh prose.

Idempotent: re-running on already-synced rows is a no-op (no insert).

Usage:
    uv run python scripts/ingest_disk_drafts.py --slug exorcist-detective-1778051012            # dry run
    uv run python scripts/ingest_disk_drafts.py --slug exorcist-detective-1778051012 --apply    # write
    uv run python scripts/ingest_disk_drafts.py --slug ... --from 1 --to 10 --apply            # range
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.services.drafts import count_words

DEFAULT_DSN = "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller"


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get(
        "BESTSELLER_DATABASE_URL"
    ) or DEFAULT_DSN
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql+psycopg://"):
        raw = raw.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return raw


async def _ingest_one_chapter(
    session,
    *,
    project_id: str,
    chapter_id: str,
    chapter_number: int,
    output_dir: Path,
    apply: bool,
) -> dict[str, object]:
    md_path = output_dir / f"chapter-{chapter_number:03d}.md"
    if not md_path.exists():
        return {"chapter_number": chapter_number, "action": "skip_no_disk_file"}

    disk_content = md_path.read_text(encoding="utf-8")

    # Read current DB draft
    row = (
        await session.execute(
            text(
                "SELECT id, version_no, content_md, word_count "
                "FROM chapter_draft_versions "
                "WHERE chapter_id = :cid AND is_current = TRUE "
                "ORDER BY version_no DESC LIMIT 1"
            ),
            {"cid": chapter_id},
        )
    ).first()

    if row is None:
        current_version_no = 0
        db_content = ""
    else:
        current_version_no = row.version_no
        db_content = row.content_md or ""

    max_version_no = (
        await session.execute(
            text(
                "SELECT COALESCE(MAX(version_no), 0) AS max_version_no "
                "FROM chapter_draft_versions WHERE chapter_id = :cid"
            ),
            {"cid": chapter_id},
        )
    ).scalar_one()
    db_version_no = max(int(max_version_no or 0), int(current_version_no or 0))

    if disk_content == db_content:
        return {
            "chapter_number": chapter_number,
            "action": "noop_identical",
            "version_no": current_version_no,
        }

    new_version_no = db_version_no + 1
    new_word_count = count_words(disk_content)

    if not apply:
        return {
            "chapter_number": chapter_number,
            "action": "would_insert",
            "db_chars": len(db_content),
            "disk_chars": len(disk_content),
            "new_version_no": new_version_no,
            "new_word_count": new_word_count,
        }

    # 1) Flip prior is_current rows for this chapter
    await session.execute(
        text(
            "UPDATE chapter_draft_versions SET is_current = FALSE "
            "WHERE chapter_id = :cid AND is_current = TRUE"
        ),
        {"cid": chapter_id},
    )

    # 2) Insert new draft row
    await session.execute(
        text(
            "INSERT INTO chapter_draft_versions "
            "(project_id, chapter_id, version_no, content_md, word_count, "
            " assembled_from_scene_draft_ids, is_current, created_at) "
            "VALUES (:pid, :cid, :ver, :body, :wc, "
            " '[]'::jsonb, TRUE, NOW())"
        ),
        {
            "pid": project_id,
            "cid": chapter_id,
            "ver": new_version_no,
            "body": disk_content,
            "wc": new_word_count,
        },
    )

    # 3) Sync chapters.current_word_count + bump revision_count
    await session.execute(
        text(
            "UPDATE chapters SET current_word_count = :wc, "
            "revision_count = revision_count + 1, updated_at = NOW() "
            "WHERE id = :cid"
        ),
        {"wc": new_word_count, "cid": chapter_id},
    )

    return {
        "chapter_number": chapter_number,
        "action": "inserted",
        "db_chars": len(db_content),
        "disk_chars": len(disk_content),
        "new_version_no": new_version_no,
        "new_word_count": new_word_count,
    }


async def _run(args: argparse.Namespace) -> int:
    engine = create_async_engine(_dsn(), future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        project_row = (
            await session.execute(
                text("SELECT id, slug FROM projects WHERE slug = :slug"),
                {"slug": args.slug},
            )
        ).first()
        if project_row is None:
            print(f"[error] project slug not found: {args.slug}", file=sys.stderr)
            return 2

        project_id = str(project_row.id)
        slug = project_row.slug

        chapter_rows = (
            await session.execute(
                text(
                    "SELECT id, chapter_number FROM chapters "
                    "WHERE project_id = :pid "
                    "AND chapter_number BETWEEN :lo AND :hi "
                    "ORDER BY chapter_number"
                ),
                {"pid": project_id, "lo": args.from_chapter, "hi": args.to_chapter},
            )
        ).all()

        output_dir = Path("output") / slug
        if not output_dir.exists():
            print(f"[error] output dir missing: {output_dir}", file=sys.stderr)
            return 2

        results: list[dict[str, object]] = []
        for row in chapter_rows:
            result = await _ingest_one_chapter(
                session,
                project_id=project_id,
                chapter_id=str(row.id),
                chapter_number=row.chapter_number,
                output_dir=output_dir,
                apply=args.apply,
            )
            results.append(result)

        if args.apply:
            await session.commit()

    inserted = [r for r in results if r["action"] == "inserted"]
    noop = [r for r in results if r["action"] == "noop_identical"]
    would = [r for r in results if r["action"] == "would_insert"]
    skip = [r for r in results if r["action"] == "skip_no_disk_file"]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] slug={slug} chapters={len(results)} "
          f"inserted={len(inserted)} would_insert={len(would)} "
          f"noop={len(noop)} skip={len(skip)}")
    for r in results:
        if r["action"] == "noop_identical":
            continue
        print(" ", r)

    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="Project slug to sync")
    ap.add_argument(
        "--from", dest="from_chapter", type=int, default=1,
        help="First chapter (default 1)",
    )
    ap.add_argument(
        "--to", dest="to_chapter", type=int, default=10_000,
        help="Last chapter (default 10000)",
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="Actually write to the DB (default dry-run)",
    )
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

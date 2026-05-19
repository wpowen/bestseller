"""Re-derive a project's selling-point / hook keyword pool when its
positioning has pivoted away from the original ``book_spec``.

The ``GoldenThreeChapterCheck`` (chapter_validator.py:990) reads
``ctx.invariants.hype_scheme.{selling_points, hook_keywords}``, which is sourced
from ``projects.invariants_json.hype_scheme``. When a book is repositioned
mid-stream (e.g. ``南茅出马仙`` → ``青囊困魂镜``), the invariants and the
``book_spec`` planning artifact stay frozen on the old positioning, so the
golden-three gate keeps flagging every chapter as ``GOLDEN_THREE_WEAK`` even
when the prose is well-aligned with the *new* positioning.

This script updates both stores atomically:

  1. ``projects.invariants_json.hype_scheme.selling_points`` & ``.hook_keywords``
  2. A new approved ``planning_artifact_versions(artifact_type='book_spec')``
     row with the updated ``series_engine.selling_points`` and
     ``.trope_keywords``.

Idempotent by content equality.

Usage:
    uv run python scripts/respec_project_keywords.py --slug <slug>             # dry run
    uv run python scripts/respec_project_keywords.py --slug <slug> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# Per-project overrides; extend with new entries as books repivot.
PROJECT_KEYWORD_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "exorcist-detective-1778051012": {
        "selling_points": [
            "青囊不语",
            "困魂镜",
            "否认者先入账",
            "镜中世界",
            "三族契约",
            "阴阳眼破局",
            "民俗悬疑",
        ],
        "hook_keywords": [
            "子时不入镜",
            "镜开七人",
            "血字公寓",
            "镜影借脸",
            "认账者出局",
            "灰线债",
        ],
        "trope_keywords": [
            "青囊",
            "困魂镜",
            "否认者",
            "入账",
            "阴阳眼",
            "三族契约",
            "镜局",
            "民俗悬疑",
            "风水师",
            "凶宅",
        ],
    },
}


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get(
        "BESTSELLER_DATABASE_URL"
    ) or "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller"
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql+psycopg://"):
        raw = raw.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return raw


async def _run(args: argparse.Namespace) -> int:
    overrides = PROJECT_KEYWORD_OVERRIDES.get(args.slug)
    if overrides is None:
        print(
            f"[error] no override defined for slug={args.slug}; "
            "add it to PROJECT_KEYWORD_OVERRIDES first",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(_dsn(), future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        project_row = (
            await session.execute(
                text(
                    "SELECT id, slug, invariants_json FROM projects "
                    "WHERE slug = :slug"
                ),
                {"slug": args.slug},
            )
        ).first()
        if project_row is None:
            print(f"[error] project slug not found: {args.slug}", file=sys.stderr)
            return 2

        project_id = str(project_row.id)
        invariants = copy.deepcopy(project_row.invariants_json or {})

        # 1) Update invariants.hype_scheme.{selling_points, hook_keywords}
        hype_scheme = invariants.get("hype_scheme") or {}
        old_sp = list(hype_scheme.get("selling_points") or [])
        old_hk = list(hype_scheme.get("hook_keywords") or [])
        new_sp = list(overrides["selling_points"])
        new_hk = list(overrides["hook_keywords"])
        hype_scheme["selling_points"] = new_sp
        hype_scheme["hook_keywords"] = new_hk
        invariants["hype_scheme"] = hype_scheme

        invariants_changed = (old_sp != new_sp) or (old_hk != new_hk)

        # 2) Build new book_spec version content
        book_spec_row = (
            await session.execute(
                text(
                    "SELECT id, version_no, status, content "
                    "FROM planning_artifact_versions "
                    "WHERE project_id = :pid AND artifact_type = 'book_spec' "
                    "ORDER BY version_no DESC LIMIT 1"
                ),
                {"pid": project_id},
            )
        ).first()

        spec_changed = False
        new_spec_content = None
        new_spec_version = None
        if book_spec_row is not None:
            content = copy.deepcopy(book_spec_row.content or {})
            series_engine = content.get("series_engine") or {}
            old_sp2 = list(series_engine.get("selling_points") or [])
            old_tk2 = list(series_engine.get("trope_keywords") or [])
            series_engine["selling_points"] = list(overrides["selling_points"])
            series_engine["trope_keywords"] = list(overrides["trope_keywords"])
            content["series_engine"] = series_engine
            new_spec_content = content
            new_spec_version = book_spec_row.version_no + 1
            spec_changed = (old_sp2 != overrides["selling_points"]) or (
                old_tk2 != overrides["trope_keywords"]
            )

        # ----- report -----
        print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] slug={args.slug}")
        print(f"  invariants.hype_scheme.selling_points: {old_sp} → {new_sp}")
        print(f"  invariants.hype_scheme.hook_keywords:  {old_hk} → {new_hk}")
        if book_spec_row is not None:
            print(
                f"  book_spec v{book_spec_row.version_no} → v{new_spec_version}: "
                f"selling_points & trope_keywords replaced "
                f"(spec_changed={spec_changed})"
            )
        else:
            print("  book_spec: no row found, skipping artifact insert")

        if not args.apply:
            await engine.dispose()
            return 0

        if invariants_changed:
            await session.execute(
                text(
                    "UPDATE projects SET invariants_json = CAST(:inv AS jsonb), "
                    "updated_at = NOW() WHERE id = :pid"
                ),
                {"pid": project_id, "inv": json.dumps(invariants)},
            )

        if spec_changed and new_spec_content is not None:
            # Demote prior approved spec, insert new approved version
            await session.execute(
                text(
                    "UPDATE planning_artifact_versions SET status = 'superseded' "
                    "WHERE project_id = :pid AND artifact_type = 'book_spec' "
                    "AND status = 'approved'"
                ),
                {"pid": project_id},
            )
            await session.execute(
                text(
                    "INSERT INTO planning_artifact_versions "
                    "(project_id, artifact_type, version_no, status, "
                    " schema_version, content, notes, created_by, created_at) "
                    "VALUES (:pid, 'book_spec', :ver, 'approved', "
                    " (SELECT schema_version FROM planning_artifact_versions "
                    "    WHERE project_id = :pid AND artifact_type = 'book_spec' "
                    "    ORDER BY version_no DESC LIMIT 1), "
                    " CAST(:content AS jsonb), :notes, 'respec_script', NOW())"
                ),
                {
                    "pid": project_id,
                    "ver": new_spec_version,
                    "content": json.dumps(new_spec_content),
                    "notes": (
                        "Auto-derived from PROJECT_KEYWORD_OVERRIDES "
                        "(scripts/respec_project_keywords.py). Pivot from "
                        "南茅出马仙 framing to 青囊困魂镜 positioning."
                    ),
                },
            )

        await session.commit()
        print("[ok] committed")

    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

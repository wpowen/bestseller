"""Close stale non-death lifecycle statuses for 《道种破虚》.

Some historical feedback extractions set metadata.lifecycle_status (for example
``comatose``) but later alive-status snapshots never closed that offstage state.
The write-safety gate then correctly blocked present-tense appearances in much
later chapters. This script backfills scheduled_exit_chapter from the first
later alive/injured/dying character snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import CharacterModel, CharacterStateSnapshotModel, ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
REPORT_DIR = Path("artifacts/daozhong_repair_audit")
REPAIR_SOURCE = "lifecycle_status_closure_repair_v1"
RECOVERY_STATUSES = {"alive", "injured", "dying"}


def lifecycle_payload(character: CharacterModel) -> dict[str, Any] | None:
    metadata = character.metadata_json if isinstance(character.metadata_json, dict) else {}
    payload = metadata.get("lifecycle_status")
    return payload if isinstance(payload, dict) else None


def active_needs_closure(payload: dict[str, Any]) -> bool:
    if payload.get("scheduled_exit_chapter") is not None:
        return False
    return str(payload.get("kind") or "").strip().lower() in {
        "missing",
        "sealed",
        "sleeping",
        "comatose",
        "exiled",
    }


async def first_recovery_chapter(
    session,
    *,
    project_id,
    character_id,
    since_chapter: int,
) -> int | None:
    snapshots = list(
        await session.scalars(
            select(CharacterStateSnapshotModel)
            .where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.character_id == character_id,
                CharacterStateSnapshotModel.chapter_number > since_chapter,
                CharacterStateSnapshotModel.alive_status.in_(RECOVERY_STATUSES),
            )
            .order_by(CharacterStateSnapshotModel.chapter_number.asc())
        )
    )
    if not snapshots:
        return None
    return snapshots[0].chapter_number


async def repair(*, execute: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    counts: Counter[str] = Counter()
    changed: list[dict[str, Any]] = []

    async with session_scope() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        characters = list(
            await session.scalars(select(CharacterModel).where(CharacterModel.project_id == project.id))
        )
        for character in characters:
            payload = lifecycle_payload(character)
            if not payload or not active_needs_closure(payload):
                continue
            try:
                since_chapter = int(payload.get("since_chapter"))
            except (TypeError, ValueError):
                counts["missing_since_chapter"] += 1
                continue
            recovered_at = await first_recovery_chapter(
                session,
                project_id=project.id,
                character_id=character.id,
                since_chapter=since_chapter,
            )
            if recovered_at is None:
                counts["active_lifecycle_without_recovery"] += 1
                continue
            counts["lifecycle_status_closed"] += 1
            changed.append(
                {
                    "character": character.name,
                    "kind": payload.get("kind"),
                    "since_chapter": since_chapter,
                    "scheduled_exit_chapter": recovered_at,
                }
            )
            if execute:
                metadata = dict(character.metadata_json or {})
                metadata["lifecycle_status"] = {
                    **payload,
                    "scheduled_exit_chapter": recovered_at,
                    "exit_condition": payload.get("exit_condition") or "closed_by_later_alive_snapshot",
                    "recovered_at_chapter": recovered_at,
                    "closure_repair_source": REPAIR_SOURCE,
                    "closure_repaired_at": now,
                }
                character.metadata_json = metadata

        if execute:
            await session.flush()

        report = {
            "project": {"slug": project.slug, "title": project.title, "status": project.status},
            "execute": execute,
            "created_at": now,
            "counts": dict(counts),
            "changed": changed,
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "lifecycle_closure_execute" if execute else "lifecycle_closure_dry_run"
    json_path = REPORT_DIR / f"{PROJECT_SLUG}_{suffix}.json"
    md_path = REPORT_DIR / f"{PROJECT_SLUG}_{suffix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["output"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 《道种破虚》角色生命周期闭合修复",
        "",
        f"- 执行写入：{report['execute']}",
        "",
        "## 计数",
        "",
    ]
    for key, value in sorted(report["counts"].items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## 闭合角色", ""])
    for item in report["changed"]:
        lines.append(
            f"- {item['character']}: {item['kind']} "
            f"第{item['since_chapter']}章 -> 第{item['scheduled_exit_chapter']}章"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(repair(execute=args.execute))
    print(
        json.dumps(
            {
                "execute": report["execute"],
                "counts": report["counts"],
                "changed": report["changed"],
                "output": report["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

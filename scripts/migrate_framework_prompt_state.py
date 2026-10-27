"""Source-aware migration; defaults to read-only, with one commit per project."""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope
from bestseller.services.framework_state_migration import migrate_project_prompt_state, migrate_detector_insertions
from bestseller.settings import load_settings


async def run(*, apply: bool, project_ids: list[str] | None = None) -> list[dict]:
    settings = load_settings()
    reports = []
    async with session_scope(settings) as session:
        ids = list(await session.scalars(select(ProjectModel.id)))
    for project_id in ids:
        if project_ids and str(project_id) not in project_ids:
            continue
        async with session_scope(settings) as session:
            project = await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id).with_for_update())
            if project is None:
                continue
            result = migrate_project_prompt_state(project, apply=apply)
            result["detector_repairs"] = await migrate_detector_insertions(session, project, apply=apply)
            reports.append({key: value for key, value in result.items() if key != "receipts"} | {"project_id": str(project_id)})
            if not apply:
                await session.rollback()
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", action="append")
    args = parser.parse_args()
    print(json.dumps({"applied": args.apply, "projects": asyncio.run(run(apply=args.apply, project_ids=args.project_id))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

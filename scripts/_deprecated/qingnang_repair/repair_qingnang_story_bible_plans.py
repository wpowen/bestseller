"""Repair file-backed volume and batch plan CSVs for 《青囊不语问阴阳》."""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
import json
from pathlib import Path

from sqlalchemy import select

from bestseller.infra.db.models import ProjectModel, VolumeModel
from bestseller.infra.db.session import session_scope
from bestseller.settings import load_settings

PROJECT_SLUG = "exorcist-detective-1778051012"


@dataclass(frozen=True)
class VolumePlanRow:
    volume: int
    start_chapter: int
    end_chapter: int
    status: str
    goal: str


@dataclass(frozen=True)
class BatchPlanRow:
    batch: str
    start_chapter: int
    end_chapter: int
    required_callbacks: str
    status: str


def build_volume_plan_rows(volumes: list[dict]) -> list[VolumePlanRow]:
    rows: list[VolumePlanRow] = []
    for volume in volumes:
        number = int(volume["volume_number"])
        start, end = volume.get("chapter_range") or ((number - 1) * 50 + 1, number * 50)
        if number == 1:
            status = "closed_review"
        elif number == 2:
            status = "recovery_required"
        else:
            status = "planned"
        rows.append(
            VolumePlanRow(
                volume=number,
                start_chapter=int(start),
                end_chapter=int(end),
                status=status,
                goal=str(volume.get("volume_goal") or ""),
            )
        )
    return rows


def build_batch_plan_rows(volume_rows: list[VolumePlanRow]) -> list[BatchPlanRow]:
    rows = [
        BatchPlanRow(
            batch="1A",
            start_chapter=1,
            end_chapter=50,
            required_callbacks="第一卷已写完，任何回修必须保持主镜门暂封、半数归人、父亲抵债入门三件事。",
            status="closed_review",
        ),
        BatchPlanRow(
            batch="2A",
            start_chapter=51,
            end_chapter=75,
            required_callbacks="按 ch51-75 recovery contract 重框为镜影伪造身份战；禁止把玩家/源代码/试炼通关正典化。",
            status="recovery",
        ),
        BatchPlanRow(
            batch="2B",
            start_chapter=76,
            end_chapter=100,
            required_callbacks="必须先解决 2A 漂移，再回到林家老宅井口、父亲半卷青囊和真执卷人身份战。",
            status="blocked_until_2A_resolved",
        ),
    ]
    for volume in volume_rows:
        if volume.volume <= 2:
            continue
        rows.append(
            BatchPlanRow(
                batch=f"{volume.volume}A",
                start_chapter=volume.start_chapter,
                end_chapter=volume.end_chapter,
                required_callbacks=volume.goal,
                status="planned",
            )
        )
    return rows


def write_volume_plan(path: Path, rows: list[VolumePlanRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["volume", "start_chapter", "end_chapter", "status", "goal"])
        for row in rows:
            writer.writerow([row.volume, row.start_chapter, row.end_chapter, row.status, row.goal])


def write_batch_queue(path: Path, rows: list[BatchPlanRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["batch", "start_chapter", "end_chapter", "required_callbacks", "status"])
        for row in rows:
            writer.writerow([row.batch, row.start_chapter, row.end_chapter, row.required_callbacks, row.status])


async def run(*, apply: bool) -> dict:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = (await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))).one()
        volume_models = (
            await session.scalars(
                select(VolumeModel).where(VolumeModel.project_id == project.id).order_by(VolumeModel.volume_number)
            )
        ).all()
        volumes = [
            {
                "volume_number": int(volume.volume_number),
                "chapter_range": (volume.metadata_json or {}).get("chapter_range"),
                "volume_goal": (volume.metadata_json or {}).get("volume_goal") or volume.title or "",
            }
            for volume in volume_models
        ]
    volume_rows = build_volume_plan_rows(volumes)
    batch_rows = build_batch_plan_rows(volume_rows)
    story_bible_dir = Path(settings.output.base_dir) / PROJECT_SLUG / "story-bible"
    if apply:
        write_volume_plan(story_bible_dir / "volume-plan.csv", volume_rows)
        write_batch_queue(story_bible_dir / "batch-queue.csv", batch_rows)
    return {
        "project_slug": PROJECT_SLUG,
        "applied": apply,
        "volume_rows": [row.__dict__ for row in volume_rows],
        "batch_rows": [row.__dict__ for row in batch_rows],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

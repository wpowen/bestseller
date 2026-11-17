"""Instruction 68 sections of the whole-book checkup; all reference only."""

from pathlib import Path

from .life_status import premature_death_hits
from .name_slips import name_slip_checkup
from .repair_ledger import repair_ledger
from .scene_consistency import scene_consistency_checkup


def people_and_repairs_checkup(book: Path, bible: dict, chapters: list[dict]) -> dict:
    bible = bible if isinstance(bible, dict) else {}
    deaths = [
        {key: row[key] for key in ("name", "planned_exit", "text", "status")} | {"chapter": chapter.get("number")}
        for chapter in chapters
        for row in premature_death_hits(chapter.get("text", ""), bible, chapter.get("number") or 0)
    ]
    return dict(
        premature_deaths=dict(count=len(deaths), issues=deaths, status="Evidence"),
        name_slips=name_slip_checkup(bible, chapters),
        scene_consistency=scene_consistency_checkup(bible, chapters),
        repair_ledger=repair_ledger(book),
    )

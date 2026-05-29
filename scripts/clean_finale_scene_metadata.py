"""Clean ch217-230 scene metadata so the chapter_outline_readiness_gate passes.

Two fixes, applied in place (no row deletion -> avoids the canon_facts FK chain):
  1. Strip stale auto-repair residue keys (the ONLY blocking issue) left over
     from the previous 1200-chapter draft that materialize-update did not clear.
  2. Fill a complete methodology_contract (stakes/pressure_stack/focus_character/
     reveal_mode/signature_image/breakpoint) so the minor 'incomplete' finding
     clears and the writer model gets richer scene control context.

Also re-sets ch217-230 chapters + scenes to PLANNED.

Run inside worker container:
    python /app/scripts/clean_finale_scene_metadata.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.infra.db.session import session_scope

SLUG = "xianxia-upgrade-1776137730"
RANGE = list(range(217, 231))

_RESIDUE_KEYS = (
    "auto_repair_adjusted_target_word_count",
    "auto_repair_block_codes",
    "auto_repair_length_scale",
    "auto_repair_hint",
    "auto_repair_attempt",
    "auto_repair_min_scene_target_floor",
    "auto_repair_scene_target_cap",
    "auto_repair_source_block_code",
    "auto_repair_original_target_word_count",
    "auto_repair_target_word_count_clamped",
)

# scene_type -> reveal_mode for methodology contract
_REVEAL_MODE = {
    "opening": "establish_pressure",
    "development": "escalate_reveal",
    "hook": "cliffhanger_withhold",
    "transition": "controlled_drip",
    "strategic_planning": "partial_reveal",
}


def _build_methodology(scene: SceneCardModel) -> dict[str, object]:
    purpose = scene.purpose if isinstance(scene.purpose, dict) else {}
    story = str(purpose.get("story") or scene.title or "本场推进")
    lead = (scene.participants or ["宁尘"])[0]
    hook = scene.hook_requirement or f"切向下一场：{story[:24]}"
    existing = {}
    if isinstance(scene.metadata_json, dict):
        existing = scene.metadata_json.get("methodology_contract") or {}
    stakes = existing.get("conflict_stakes") or existing.get("stakes") or (
        f"若本场失手，{lead}将失去对『{story[:20]}』的主动权"
    )
    return {
        "stakes": stakes,
        "pressure_stack": [story[:30]],
        "focus_character": lead,
        "reveal_mode": _REVEAL_MODE.get(scene.scene_type, "partial_reveal"),
        "signature_image": (scene.time_label or "") + "：" + story[:22],
        "breakpoint": hook,
        # keep the original conflict_stakes too
        "conflict_stakes": stakes,
    }


async def main() -> None:
    async with session_scope() as session:
        project = (
            await session.execute(
                select(ProjectModel).where(ProjectModel.slug == SLUG)
            )
        ).scalar_one()

        chapters = (
            await session.execute(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(RANGE),
                )
            )
        ).scalars().all()
        chapter_ids = [c.id for c in chapters]
        for c in chapters:
            c.status = ChapterStatus.PLANNED.value
        print(f"reset {len(chapters)} chapters to PLANNED")

        scenes = (
            await session.execute(
                select(SceneCardModel).where(
                    SceneCardModel.chapter_id.in_(chapter_ids)
                )
            )
        ).scalars().all()

        residue_cleared = 0
        methodology_filled = 0
        for scene in scenes:
            meta = dict(scene.metadata_json or {})
            had_residue = any(k in meta for k in _RESIDUE_KEYS)
            for k in _RESIDUE_KEYS:
                meta.pop(k, None)
            if had_residue:
                residue_cleared += 1
            meta["methodology_contract"] = _build_methodology(scene)
            methodology_filled += 1
            scene.metadata_json = meta
            flag_modified(scene, "metadata_json")
            scene.status = SceneStatus.PLANNED.value

        await session.commit()
        print(f"scenes processed: {len(scenes)}")
        print(f"  residue cleared on: {residue_cleared}")
        print(f"  methodology_contract filled on: {methodology_filled}")
        print("done")


if __name__ == "__main__":
    asyncio.run(main())

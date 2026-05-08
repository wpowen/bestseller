"""Repair named-character planning gaps for 《道种破虚》.

The naming gate correctly blocks prose that introduces names outside the
locked identity roster.  Several repaired chapters had scene purposes that
named active characters without listing them in ``scene_cards.participants``
or materializing them in ``characters`` first.  This script repairs that
structured planning layer so future rewrites receive a coherent roster.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import select

from bestseller.infra.db.models import CharacterModel, ChapterModel, ProjectModel, SceneCardModel
from bestseller.infra.db.session import session_scope
from bestseller.settings import load_settings


PROJECT_SLUG = "xianxia-upgrade-1776137730"


CHARACTERS_TO_ENSURE: dict[str, dict[str, Any]] = {
    "孙乾": {
        "role": "supporting",
        "gender": "male",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "background": "叶长青亲传弟子，大比半决赛中作为宁尘的内门对手登场。",
        "goal": "在大比中压制宁尘，维护叶长青一系的威势。",
    },
    "赵峥": {
        "role": "supporting",
        "gender": "male",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "background": "冷锋换轨线中的旧敌/临时合作者，递出可验证但不完整的路线。",
        "goal": "借冷锋路线与换轨符换取自身生路，同时保留对宁尘的牵制。",
    },
}


PARTICIPANT_PATCHES: dict[int, dict[int, tuple[str, ...]]] = {
    51: {
        1: ("苏瑶",),
        2: ("苏瑶",),
    },
    60: {
        1: ("苏瑶", "老张"),
        2: ("老张",),
        3: ("老张",),
    },
    70: {
        1: ("孙乾",),
        2: ("孙乾",),
        3: ("孙乾",),
    },
    389: {
        1: ("赵峥",),
        4: ("赵峥",),
    },
}


def _identity_metadata(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "cast_entry": {
            "gender": spec["gender"],
            "aliases": [],
            "pronoun_set_zh": spec["pronoun_set_zh"],
            "pronoun_set_en": spec["pronoun_set_en"],
        },
        "gender": spec["gender"],
        "aliases": [],
        "pronoun_set_zh": spec["pronoun_set_zh"],
        "pronoun_set_en": spec["pronoun_set_en"],
        "identity_contract_repaired_by": "repair_daozhong_named_participants_v1",
        "repair_note": f"{name} was named in chapter planning/prose but missing from the identity roster.",
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    async with session_scope(settings) as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"Project not found: {PROJECT_SLUG}")

        created_characters: list[str] = []
        patched_scenes: list[str] = []

        for name, spec in CHARACTERS_TO_ENSURE.items():
            existing = await session.scalar(
                select(CharacterModel).where(
                    CharacterModel.project_id == project.id,
                    CharacterModel.name == name,
                )
            )
            if existing is not None:
                meta = dict(existing.metadata_json or {})
                meta.setdefault("gender", spec["gender"])
                meta.setdefault("pronoun_set_zh", spec["pronoun_set_zh"])
                meta.setdefault("pronoun_set_en", spec["pronoun_set_en"])
                cast_entry = dict(meta.get("cast_entry") or {})
                cast_entry.setdefault("gender", spec["gender"])
                cast_entry.setdefault("aliases", [])
                cast_entry.setdefault("pronoun_set_zh", spec["pronoun_set_zh"])
                cast_entry.setdefault("pronoun_set_en", spec["pronoun_set_en"])
                meta["cast_entry"] = cast_entry
                if args.execute:
                    existing.metadata_json = meta
                continue
            created_characters.append(name)
            if args.execute:
                session.add(
                    CharacterModel(
                        project_id=project.id,
                        name=name,
                        role=spec["role"],
                        background=spec["background"],
                        goal=spec["goal"],
                        alive_status="alive",
                        metadata_json=_identity_metadata(name, spec),
                    )
                )

        for chapter_number, scene_map in PARTICIPANT_PATCHES.items():
            chapter = await session.scalar(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number == chapter_number,
                )
            )
            if chapter is None:
                continue
            for scene_number, names in scene_map.items():
                scene = await session.scalar(
                    select(SceneCardModel).where(
                        SceneCardModel.chapter_id == chapter.id,
                        SceneCardModel.scene_number == scene_number,
                    )
                )
                if scene is None:
                    continue
                participants = list(scene.participants or [])
                changed = False
                for name in names:
                    if name not in participants:
                        participants.append(name)
                        changed = True
                if changed:
                    patched_scenes.append(f"{chapter_number}.{scene_number}:{','.join(names)}")
                    if args.execute:
                        scene.participants = participants
                        meta = dict(scene.metadata_json or {})
                        meta["named_participant_repaired_by"] = "repair_daozhong_named_participants_v1"
                        scene.metadata_json = meta

        if args.execute:
            await session.flush()

        print(
            {
                "execute": args.execute,
                "created_characters": created_characters,
                "patched_scenes": patched_scenes,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())

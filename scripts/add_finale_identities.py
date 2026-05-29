"""Register the new identities introduced by the v2 finale into the project's
locked identity manifest. Without this, materialize-outline rejects the batch
with PLAN_SCENE_UNKNOWN_PARTICIPANT.

Run inside worker container:
    python /app/scripts/add_finale_identities.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope

SLUG = "xianxia-upgrade-1776137730"

# Identities to add. Each is the minimum normalized entry shape.
NEW_IDENTITIES: list[dict[str, Any]] = [
    {
        "name": "红裙女子",
        "role": "antagonist",
        "gender": "female",
        "pronoun_set_zh": "她",
        "pronoun_set_en": "she/her",
        "aliases": ["红衣女子"],
    },
    {
        "name": "道祖真灵",
        "role": "supporting",
        "gender": "female",
        "pronoun_set_zh": "她",
        "pronoun_set_en": "she/her",
        "aliases": ["素白长裙金眸女子", "因果道祖最后一缕真灵"],
    },
    {
        "name": "虚煞",
        "role": "supporting",
        "gender": "neutral",
        "pronoun_set_zh": "它",
        "pronoun_set_en": "it/its",
        "aliases": ["苍白火焰", "苍白火", "虚煞残念"],
    },
    {
        "name": "虚煞（识海）",
        "role": "supporting",
        "gender": "neutral",
        "pronoun_set_zh": "它",
        "pronoun_set_en": "it/its",
        "aliases": [],
    },
    {
        "name": "道种（识海）",
        "role": "supporting",
        "gender": "neutral",
        "pronoun_set_zh": "它",
        "pronoun_set_en": "it/its",
        "aliases": [],
    },
    {
        "name": "新少年",
        "role": "supporting",
        "gender": "male",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "aliases": [],
    },
    {
        "name": "普通修士",
        "role": "extra",
        "gender": "unknown",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "aliases": [],
    },
    {
        "name": "村妇",
        "role": "extra",
        "gender": "female",
        "pronoun_set_zh": "她",
        "pronoun_set_en": "she/her",
        "aliases": ["杂役峰老妇人"],
    },
    {
        "name": "杂役峰老妇人",
        "role": "extra",
        "gender": "female",
        "pronoun_set_zh": "她",
        "pronoun_set_en": "she/her",
        "aliases": [],
    },
    {
        "name": "青云宗洒扫弟子",
        "role": "extra",
        "gender": "male",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "aliases": [],
    },
    {
        "name": "沉灯渊残修",
        "role": "extra",
        "gender": "unknown",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "aliases": [],
    },
    {
        "name": "落云宗殿主躯壳",
        "role": "antagonist",
        "gender": "male",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
        "aliases": ["殿主本体躯壳"],
    },
    {
        "name": "末法生灵",
        "role": "extra",
        "gender": "unknown",
        "pronoun_set_zh": "他们",
        "pronoun_set_en": "they/them",
        "aliases": [],
    },
]


async def main() -> None:
    async with session_scope() as session:
        project = (
            await session.execute(
                select(ProjectModel).where(ProjectModel.slug == SLUG)
            )
        ).scalar_one()

        metadata: dict[str, Any] = dict(project.metadata_json or {})
        manifest: list[dict[str, Any]] = list(metadata.get("identity_manifest") or [])
        existing_names = {
            (e.get("name") or "").strip()
            for e in manifest
            if isinstance(e, dict) and (e.get("name") or "").strip()
        }
        added = 0
        for entry in NEW_IDENTITIES:
            if entry["name"] in existing_names:
                print(f"  = {entry['name']} (already present)")
                continue
            manifest.append(entry)
            existing_names.add(entry["name"])
            added += 1
            print(f"  + {entry['name']}")

        if added == 0:
            print("nothing new to add")
            return

        metadata["identity_manifest"] = manifest
        project.metadata_json = metadata
        flag_modified(project, "metadata_json")
        await session.commit()
        print(f"added {added} identities; manifest size now {len(manifest)}")


if __name__ == "__main__":
    asyncio.run(main())

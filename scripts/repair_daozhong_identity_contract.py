"""Repair identity contract and scene-card structure for 《道种破虚》.

The production run was created before CastSpec carried gender/pronoun locks.
This script performs deterministic DB repair:

* creates a new repaired CastSpec artifact version;
* writes a locked project identity manifest;
* backfills Character metadata gender/pronouns;
* fills missing SceneCard time_label / participants / purpose fields.

Run with --execute to write changes.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    CharacterModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.narrative_contracts import (
    build_identity_manifest,
    validate_foundation_identity_contract,
    validate_scene_contract_pre_draft,
)
from bestseller.services.identity_guard import load_identity_registry


PROJECT_SLUG = "xianxia-upgrade-1776137730"

GENDER_OVERRIDES: dict[str, str] = {
    "宁尘": "male",
    "陆沉": "male",
    "叶长青": "male",
    "苏瑶": "female",
    "萧无咎": "male",
    "厉青锋": "male",
    "冥轮": "male",
    "危月": "female",
    "陈渡": "male",
    "因果盟联络使·无名": "male",
    "墨家老仆·墨七": "male",
    "赤焰": "male",
    "叶清漪": "female",
    "宁清漪": "female",
    "宁清漪（叶清漪）": "female",
    "白鹿": "nonbinary",
    "沈青衣": "female",
    "李怀瑾": "male",
    "周小满": "female",
    "云中子": "male",
    "赤鸢": "female",
    "柳如烟": "female",
    "赵铁柱": "male",
    "孙思邈": "male",
    "魏无忌": "male",
    "孟婆": "female",
    "姜雪宁": "female",
    "黄药师": "male",
    "吴钩": "male",
    "郑九幽": "male",
    "冯九针": "male",
    "楚狂生": "male",
    "邓布利": "male",
    "曹孟德": "male",
    "上官婉儿": "female",
    "欧阳锋": "male",
    "司马青衫": "male",
    "诸葛暗": "male",
    "慕容复": "male",
    "独孤求败": "male",
    "任我行": "male",
    "赵小蝉": "female",
    "赵青霜": "female",
    "秦执": "male",
    "厉青": "male",
    "莫问": "male",
    "李无情": "male",
    "萧无劫": "male",
    "洪铸": "male",
    "小棠": "female",
    "苏家": "nonbinary",
    "叶家": "nonbinary",
    "执法堂": "nonbinary",
    "坠魂渊": "nonbinary",
    "因果": "nonbinary",
    "追兵": "nonbinary",
    "金丹期修士": "nonbinary",
    "青鸾": "nonbinary",
    "守墓人": "nonbinary",
    "青衫男子": "nonbinary",
    "因果道祖的另一半": "female",
    "宁清": "female",
    "叶青": "female",
    "叶青文": "male",
}

FEMALE_MARKERS = (
    "瑶",
    "漪",
    "婉",
    "烟",
    "雪",
    "青衣",
    "青霜",
    "小蝉",
    "赤鸢",
    "危月",
    "孟婆",
    "女子",
    "少女",
    "师姐",
    "师妹",
    "母亲",
    "夫人",
    "婢",
)
MALE_MARKERS = (
    "父亲",
    "老者",
    "男子",
    "青年",
    "少年",
    "师兄",
    "师弟",
    "长老",
    "殿主",
    "道祖",
    "老仆",
    "公子",
)
NON_PERSON_MARKERS = (
    "道种",
    "本源",
    "因果之心",
    "碎甲",
    "执法队",
    "执法堂",
    "因果殿",
    "因果盟",
    "苏家",
    "叶家",
    "坠魂渊",
    "追兵",
    "宗",
    "阁",
    "堂",
    "峰",
    "渊",
    "殿",
    "盟",
    "城",
    "谷",
    "阵",
    "印",
    "令",
    "队",
    "军",
    "门",
    "傀儡",
    "妖兽",
    "虚影",
    "残意",
    "漆黑",
    "存在",
    "势力",
)
PERSON_TITLE_MARKERS = (
    "长老",
    "师兄",
    "师弟",
    "师姐",
    "师妹",
    "老祖",
    "家主",
    "殿主",
    "阁主",
    "宗主",
    "公子",
    "姑娘",
    "母亲",
    "父亲",
)
NON_PERSON_SUFFIXES = (
    "家",
    "堂",
    "宗",
    "阁",
    "峰",
    "渊",
    "殿",
    "盟",
    "城",
    "谷",
    "阵",
    "印",
    "令",
    "队",
    "军",
    "门",
)
GENERIC_PERSON_DESCRIPTORS = (
    "男子",
    "女子",
    "青年",
    "少女",
    "男弟子",
    "女弟子",
    "修士",
)


def pronouns_for(gender: str) -> tuple[str, str]:
    if gender == "male":
        return "他", "he/him"
    if gender == "female":
        return "她", "she/her"
    return "ta", "they/them"


def infer_gender(name: str, role: str = "", text: str = "") -> str:
    raw = f"{name} {role} {text}"
    if name in GENDER_OVERRIDES:
        return GENDER_OVERRIDES[name]
    if is_non_person_identity(name, role, text):
        return "nonbinary"
    for key, gender in GENDER_OVERRIDES.items():
        if key and key in name and gender != "nonbinary":
            return gender
    if any(marker in raw for marker in FEMALE_MARKERS):
        return "female"
    if any(marker in raw for marker in MALE_MARKERS):
        return "male"
    return "nonbinary"


def is_non_person_identity(name: str, role: str = "", text: str = "") -> bool:
    raw = f"{name} {role} {text}"
    if any(marker in name for marker in GENERIC_PERSON_DESCRIPTORS) and len(name) <= 8:
        return True
    if any(marker in name for marker in PERSON_TITLE_MARKERS):
        return False
    if name.endswith(NON_PERSON_SUFFIXES):
        return True
    if any(marker in raw for marker in NON_PERSON_MARKERS):
        return True
    if any(marker in role for marker in ("组织", "势力", "来源", "faction", "force", "group")):
        return True
    return False


def clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def enrich_cast_character(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    name = clean(out.get("name"))
    role = clean(out.get("role"))
    text = " ".join(
        clean(out.get(key))
        for key in ("background", "goal", "fear", "flaw", "strength", "secret")
        if clean(out.get(key))
    )
    gender = infer_gender(name, role, text)
    pzh, pen = pronouns_for(gender)
    out["gender"] = gender
    out["pronoun_set_zh"] = pzh
    out["pronoun_set_en"] = pen
    return out


def enrich_cast_spec(content: dict[str, Any]) -> dict[str, Any]:
    fixed = copy.deepcopy(content)
    for key in ("protagonist", "antagonist"):
        if isinstance(fixed.get(key), dict):
            fixed[key] = enrich_cast_character(fixed[key])
    supporting = fixed.get("supporting_cast")
    if isinstance(supporting, list):
        fixed["supporting_cast"] = [
            enrich_cast_character(item) if isinstance(item, dict) else item
            for item in supporting
        ]
    return fixed


def identity_token(value: Any) -> str:
    if value is None:
        return ""
    return "".join(str(value).strip().lower().split())


def scene_time_label(scene_number: int) -> str:
    labels = {
        1: "章节开场",
        2: "章节中段",
        3: "章节结尾",
        4: "章节补充钩子",
    }
    return labels.get(scene_number, f"章节场景{scene_number}")


def infer_scene_participants(
    scene: SceneCardModel,
    *,
    known_names: list[str],
    chapter_fallback: list[str],
) -> list[str]:
    haystack = " ".join(
        part
        for part in (
            scene.title or "",
            str((scene.purpose or {}).get("story", "")) if isinstance(scene.purpose, dict) else "",
            str((scene.purpose or {}).get("emotion", "")) if isinstance(scene.purpose, dict) else "",
        )
        if part
    )
    found: list[str] = []
    for name in sorted(known_names, key=len, reverse=True):
        if name and name in haystack and name not in found:
            found.append(name)
        if len(found) >= 3:
            break
    if found:
        return found
    if chapter_fallback:
        return chapter_fallback[:3]
    return ["宁尘"]


async def repair(*, execute: bool) -> None:
    async with session_scope() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        latest_cast = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.CAST_SPEC.value,
            )
            .order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
            .limit(1)
        )
        if latest_cast is None:
            raise SystemExit("latest CastSpec not found")

        fixed_cast = enrich_cast_spec(latest_cast.content)
        report = validate_foundation_identity_contract(fixed_cast)
        if report.blocks:
            raise SystemExit(report.error_message(project_slug=project.slug, artifact="fixed cast_spec"))

        fixed_cast_manifest = build_identity_manifest(fixed_cast)
        cast_manifest_by_name = {item["name"]: item for item in fixed_cast_manifest}

        characters = list(
            await session.scalars(select(CharacterModel).where(CharacterModel.project_id == project.id))
        )
        character_by_name = {character.name: character for character in characters}

        scene_rows = (
            await session.execute(
                select(SceneCardModel, ChapterModel.chapter_number)
                .join(ChapterModel, SceneCardModel.chapter_id == ChapterModel.id)
                .where(SceneCardModel.project_id == project.id)
                .order_by(ChapterModel.chapter_number.asc(), SceneCardModel.scene_number.asc())
            )
        ).all()

        participant_names = sorted(
            {
                name
                for scene, _ in scene_rows
                for name in (scene.participants or [])
                if isinstance(name, str) and name.strip()
            }
        )
        created_characters = 0
        if execute:
            for name in participant_names:
                if name in character_by_name:
                    continue
                gender = infer_gender(name)
                pzh, pen = pronouns_for(gender)
                character = CharacterModel(
                    project_id=project.id,
                    name=name,
                    role="supporting",
                    metadata_json={
                        "gender": gender,
                        "pronoun_set_zh": pzh,
                        "pronoun_set_en": pen,
                        "identity_repair_source": "scene_participant",
                    },
                )
                session.add(character)
                character_by_name[name] = character
                characters.append(character)
                created_characters += 1
            if created_characters:
                await session.flush()

        expanded_manifest: list[dict[str, Any]] = []
        updated_characters = 0
        for character in characters:
            meta = dict(character.metadata_json or {})
            cast_entry = dict(meta.get("cast_entry") or {})
            manifest_entry = cast_manifest_by_name.get(character.name)
            if manifest_entry is not None:
                gender = manifest_entry["gender"]
                pzh = manifest_entry["pronoun_set_zh"]
                pen = manifest_entry["pronoun_set_en"]
                aliases = manifest_entry.get("aliases") or []
                role = manifest_entry.get("role") or character.role
            else:
                text = " ".join(
                    clean(getattr(character, key, None))
                    for key in ("background", "goal", "fear", "flaw", "strength", "secret")
                )
                gender = infer_gender(character.name, character.role, text)
                pzh, pen = pronouns_for(gender)
                aliases = cast_entry.get("aliases") if isinstance(cast_entry.get("aliases"), list) else []
                role = character.role or "supporting"
            expanded_manifest.append(
                {
                    "name": character.name,
                    "role": role,
                    "gender": gender,
                    "pronoun_set_zh": pzh,
                    "pronoun_set_en": pen,
                    "aliases": aliases,
                }
            )
            if execute:
                cast_entry.update(
                    {
                        "gender": gender,
                        "pronoun_set_zh": pzh,
                        "pronoun_set_en": pen,
                        "aliases": aliases,
                    }
                )
                meta.update(
                    {
                        "gender": gender,
                        "pronoun_set_zh": pzh,
                        "pronoun_set_en": pen,
                        "aliases": aliases,
                        "cast_entry": cast_entry,
                        "identity_contract_repaired_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                character.metadata_json = meta
                updated_characters += 1

        known_names = [item["name"] for item in expanded_manifest]
        chapter_participants: dict[int, list[str]] = {}
        for scene, chapter_number in scene_rows:
            for name in scene.participants or []:
                if isinstance(name, str) and name.strip():
                    chapter_participants.setdefault(chapter_number, [])
                    if name not in chapter_participants[chapter_number]:
                        chapter_participants[chapter_number].append(name)

        repaired_scenes = 0
        for scene, chapter_number in scene_rows:
            changed = False
            if not clean(scene.time_label):
                if execute:
                    scene.time_label = scene_time_label(scene.scene_number)
                changed = True
            if not scene.participants:
                inferred = infer_scene_participants(
                    scene,
                    known_names=known_names,
                    chapter_fallback=chapter_participants.get(chapter_number, []),
                )
                if execute:
                    scene.participants = inferred
                changed = True
            purpose = dict(scene.purpose or {})
            if not clean(purpose.get("story")):
                purpose["story"] = "承接本章主线，补足场景推进、信息释放与结尾钩子。"
                if execute:
                    scene.purpose = purpose
                changed = True
            if changed:
                repaired_scenes += 1
                if execute:
                    scene.metadata_json = {
                        **(scene.metadata_json or {}),
                        "scene_contract_repaired_at": datetime.now(timezone.utc).isoformat(),
                    }

        next_version = int(
            await session.scalar(
                select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0))
                .where(
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type == ArtifactType.CAST_SPEC.value,
                )
            )
            or 0
        ) + 1

        if execute:
            repaired_artifact = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.CAST_SPEC.value,
                scope_ref_id=None,
                version_no=next_version,
                status="approved",
                schema_version=latest_cast.schema_version,
                content=fixed_cast,
                source_run_id=latest_cast.source_run_id,
                notes=(
                    "Deterministic identity contract repair: added gender and "
                    "pronoun_set_zh/pronoun_set_en for CastSpec characters."
                ),
                created_by="repair_daozhong_identity_contract",
            )
            session.add(repaired_artifact)
            project.metadata_json = {
                **(project.metadata_json or {}),
                "identity_manifest": expanded_manifest,
                "identity_manifest_status": "locked",
                "identity_contract_repaired_at": datetime.now(timezone.utc).isoformat(),
                "identity_contract_repair_cast_version": next_version,
            }
            await session.flush()

        registry = await load_identity_registry(session, project.id)
        failed_scenes = []
        for scene, chapter_number in scene_rows:
            report = validate_scene_contract_pre_draft(
                scene,
                identity_registry=registry,
                require_identity_registry=True,
            )
            if report.blocks:
                failed_scenes.append((chapter_number, scene.scene_number, [v.code for v in report.violations]))

        print("execute", execute)
        print("project", project.slug, project.title)
        print("latest_cast_version", latest_cast.version_no)
        print("new_cast_version", next_version if execute else "(dry-run)")
        print("cast_manifest_count", len(fixed_cast_manifest))
        print("expanded_manifest_count", len(expanded_manifest))
        print("characters_total", len(characters))
        print("characters_created", created_characters)
        print("characters_updated", updated_characters if execute else len(characters))
        print("scenes_total", len(scene_rows))
        print("scenes_repaired", repaired_scenes)
        print("scene_contract_failures_after_repair", len(failed_scenes))
        print("scene_contract_failure_sample", failed_scenes[:20])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(repair(execute=args.execute))


if __name__ == "__main__":
    main()

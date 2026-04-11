from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.story_bible import (
    CastSpecInput,
    CharacterInput,
    CharacterKnowledgeStateInput,
    VolumePlanEntryInput,
    WorldSpecInput,
)
from bestseller.infra.db.models import (
    CharacterModel,
    CharacterStateSnapshotModel,
    ChapterModel,
    FactionModel,
    LocationModel,
    ProjectModel,
    RelationshipModel,
    SceneCardModel,
    StyleGuideModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services.world_expansion import load_world_expansion_context


def stable_character_id(project_id: UUID, character_name: str) -> UUID:
    return uuid5(project_id, f"character:{character_name.strip()}")


def stable_world_rule_id(project_id: UUID, rule_code: str) -> UUID:
    return uuid5(project_id, f"world-rule:{rule_code.strip()}")


def stable_location_id(project_id: UUID, location_name: str) -> UUID:
    return uuid5(project_id, f"location:{location_name.strip()}")


def stable_faction_id(project_id: UUID, faction_name: str) -> UUID:
    return uuid5(project_id, f"faction:{faction_name.strip()}")


def _stable_relationship_id(project_id: UUID, character_a_id: UUID, character_b_id: UUID) -> UUID:
    left_id, right_id = sorted((character_a_id, character_b_id), key=lambda item: str(item))
    return uuid5(project_id, f"relationship:{left_id}:{right_id}")


def _normalize_name(value: str) -> str:
    return value.strip()


def _base_role_strength(role_type: str) -> float:
    normalized = role_type.strip().lower()
    if any(token in normalized for token in ("enemy", "敌", "仇")):
        return -0.8
    if any(token in normalized for token in ("rival", "对手", "竞争")):
        return -0.3
    if any(token in normalized for token in ("ally", "friend", "mentor", "爱", "恋", "搭档", "盟友")):
        return 0.6
    return 0.1


def _parse_volume_word_count(raw_value: float | int | str | None) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        return int(raw_value * 10000) if raw_value < 1000 else int(raw_value)
    text = raw_value.strip()
    if not text:
        return None
    matched = re.search(r"(\d+(?:\.\d+)?)", text)
    if matched is None:
        return None
    value = float(matched.group(1))
    if "万" in text:
        return int(value * 10000)
    return int(value)


def _merge_metadata(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def _character_beliefs(knowledge_state: CharacterKnowledgeStateInput) -> list[str]:
    beliefs = [str(item) for item in knowledge_state.knows]
    beliefs.extend(f"误判:{item}" for item in knowledge_state.falsely_believes)
    beliefs.extend(f"未知:{item}" for item in knowledge_state.unaware_of)
    return beliefs


def parse_world_spec_input(content: dict[str, Any]) -> WorldSpecInput:
    return WorldSpecInput.model_validate(content)


def parse_cast_spec_input(content: dict[str, Any]) -> CastSpecInput:
    return CastSpecInput.model_validate(content)


def parse_volume_plan_input(content: dict[str, Any] | list[dict[str, Any]]) -> list[VolumePlanEntryInput]:
    items: list[dict[str, Any]]
    if isinstance(content, list):
        items = content
    else:
        items = list(content.get("volumes", []))
        if not items and "volume_number" in content:
            items = [content]
    return [VolumePlanEntryInput.model_validate(item) for item in items]


async def _get_or_create_style_guide(session: AsyncSession, project_id: UUID) -> StyleGuideModel:
    style_guide = await session.get(StyleGuideModel, project_id)
    if style_guide is not None:
        return style_guide
    style_guide = StyleGuideModel(
        project_id=project_id,
        pov_type="third-limited",
        tense="present",
        tone_keywords=[],
        prose_style="baseline",
        sentence_style="mixed",
        info_density="medium",
        dialogue_ratio=0.35,
        taboo_words=[],
        taboo_topics=[],
        reference_works=[],
        custom_rules=[],
    )
    session.add(style_guide)
    await session.flush()
    return style_guide


async def apply_book_spec(
    session: AsyncSession,
    project: ProjectModel,
    content: dict[str, Any],
) -> bool:
    style_guide = await _get_or_create_style_guide(session, project.id)

    project.title = str(content.get("title") or project.title)
    project.genre = str(content.get("genre") or project.genre)
    project.audience = str(content.get("target_audience") or project.audience or "") or project.audience
    project.metadata_json = _merge_metadata(
        project.metadata_json,
        {
            "book_spec": content,
            "logline": content.get("logline"),
            "themes": content.get("themes", []),
            "stakes": content.get("stakes", {}),
            "series_engine": content.get("series_engine", {}),
            "protagonist": content.get("protagonist", {}),
        },
    )

    tone_keywords = content.get("tone")
    if isinstance(tone_keywords, list) and tone_keywords:
        style_guide.tone_keywords = [str(item) for item in tone_keywords]
    if content.get("themes"):
        style_guide.custom_rules = [
            f"主题:{theme}" for theme in content.get("themes", []) if str(theme).strip()
        ]
    if isinstance(content.get("series_engine"), dict):
        series_engine = content["series_engine"]
        hook_style = series_engine.get("hook_style")
        if hook_style:
            style_guide.reference_works = [f"连载引擎:{hook_style}"]
    await session.flush()
    return True


async def upsert_world_spec(
    session: AsyncSession,
    project: ProjectModel,
    content: dict[str, Any],
) -> dict[str, int]:
    world_spec = parse_world_spec_input(content)
    project.metadata_json = _merge_metadata(
        project.metadata_json,
        {
            "world_spec": content,
            "world_name": world_spec.world_name,
            "world_premise": world_spec.world_premise,
            "power_structure": world_spec.power_structure,
            "forbidden_zones": world_spec.forbidden_zones,
            "power_system": world_spec.power_system.model_dump(mode="json"),
        },
    )

    rules_upserted = 0
    for index, rule in enumerate(world_spec.rules, start=1):
        rule_code = rule.rule_id or f"R{index:03d}"
        rule_id = stable_world_rule_id(project.id, rule_code)
        model = await session.get(WorldRuleModel, rule_id)
        if model is None:
            model = WorldRuleModel(id=rule_id, project_id=project.id, rule_code=rule_code, name=rule.name, description=rule.description)
            session.add(model)
        model.rule_code = rule_code
        model.name = rule.name
        model.description = rule.description
        model.story_consequence = rule.story_consequence
        model.exploitation_potential = rule.exploitation_potential
        model.metadata_json = _merge_metadata(
            model.metadata_json,
            {"world_name": world_spec.world_name, "world_premise": world_spec.world_premise},
        )
        rules_upserted += 1

    locations_upserted = 0
    for location in world_spec.locations:
        location_id = stable_location_id(project.id, location.name)
        model = await session.get(LocationModel, location_id)
        if model is None:
            model = LocationModel(id=location_id, project_id=project.id, name=location.name, location_type=location.location_type)
            session.add(model)
        model.name = location.name
        model.location_type = location.location_type
        model.atmosphere = location.atmosphere
        model.key_rule_codes = list(location.key_rules)
        model.story_role = location.story_role
        locations_upserted += 1

    factions_upserted = 0
    for faction in world_spec.factions:
        faction_id = stable_faction_id(project.id, faction.name)
        model = await session.get(FactionModel, faction_id)
        if model is None:
            model = FactionModel(id=faction_id, project_id=project.id, name=faction.name)
            session.add(model)
        model.name = faction.name
        model.goal = faction.goal
        model.method = faction.method
        model.relationship_to_protagonist = faction.relationship_to_protagonist
        model.internal_conflict = faction.internal_conflict
        factions_upserted += 1

    await session.flush()
    return {
        "world_rules_upserted": rules_upserted,
        "locations_upserted": locations_upserted,
        "factions_upserted": factions_upserted,
    }


async def get_or_create_character_by_name(
    session: AsyncSession,
    *,
    project_id: UUID,
    character_name: str,
    role: str = "supporting",
) -> CharacterModel:
    character_id = stable_character_id(project_id, character_name)
    character = await session.get(CharacterModel, character_id)
    if character is not None:
        return character
    character = CharacterModel(
        id=character_id,
        project_id=project_id,
        name=_normalize_name(character_name),
        role=role,
        knowledge_state_json={},
        voice_profile_json={},
        moral_framework_json={},
        metadata_json={"placeholder": True},
    )
    session.add(character)
    await session.flush()
    return character


async def _ensure_initial_character_state_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    character: CharacterModel,
) -> bool:
    existing = await session.scalar(
        select(CharacterStateSnapshotModel).where(
            CharacterStateSnapshotModel.project_id == project_id,
            CharacterStateSnapshotModel.character_id == character.id,
            CharacterStateSnapshotModel.chapter_number == 0,
            CharacterStateSnapshotModel.scene_number == 0,
        )
    )
    if existing is not None:
        return False

    snapshot = CharacterStateSnapshotModel(
        project_id=project_id,
        character_id=character.id,
        chapter_number=0,
        scene_number=0,
        arc_state=character.arc_state,
        emotional_state=character.metadata_json.get("emotional_state"),
        physical_state=character.metadata_json.get("physical_state"),
        power_tier=character.power_tier,
        trust_map={},
        beliefs=_character_beliefs(CharacterKnowledgeStateInput.model_validate(character.knowledge_state_json or {})),
        notes="Initial story bible state.",
    )
    session.add(snapshot)
    await session.flush()
    return True


async def upsert_cast_spec(
    session: AsyncSession,
    project: ProjectModel,
    content: dict[str, Any],
) -> dict[str, int]:
    cast_spec = parse_cast_spec_input(content)
    project.metadata_json = _merge_metadata(project.metadata_json, {"cast_spec": content})

    characters_upserted = 0
    voice_profiles_populated = 0
    moral_frameworks_populated = 0
    state_snapshots_created = 0
    characters_by_name: dict[str, CharacterModel] = {}
    for character_input in cast_spec.all_characters():
        character_id = stable_character_id(project.id, character_input.name)
        character = await session.get(CharacterModel, character_id)
        if character is None:
            character = CharacterModel(
                id=character_id,
                project_id=project.id,
                name=_normalize_name(character_input.name),
                role=character_input.role,
                knowledge_state_json={},
                voice_profile_json={},
                moral_framework_json={},
                metadata_json={},
            )
            session.add(character)
        character.name = _normalize_name(character_input.name)
        character.role = character_input.role
        character.age = character_input.age
        character.background = character_input.background
        character.goal = character_input.goal
        character.fear = character_input.fear
        character.flaw = character_input.flaw
        character.strength = character_input.strength
        character.secret = character_input.secret
        character.arc_trajectory = character_input.arc_trajectory
        character.arc_state = character_input.arc_state
        character.power_tier = character_input.power_tier
        character.is_pov_character = character_input.role == "protagonist"
        character.knowledge_state_json = character_input.knowledge_state.model_dump(mode="json")
        _voice_data = character_input.voice_profile.model_dump(mode="json")
        character.voice_profile_json = _voice_data
        if any(v for v in _voice_data.values() if v):
            voice_profiles_populated += 1
        _moral_data = character_input.moral_framework.model_dump(mode="json")
        character.moral_framework_json = _moral_data
        if any(v for v in _moral_data.values() if v):
            moral_frameworks_populated += 1
        # ── Phase-4: generate lie_truth_arc from knowledge_state ──
        _ks = character_input.knowledge_state
        _lie_truth_extra: dict[str, Any] = {}
        if _ks.falsely_believes:
            _core_lie = _ks.falsely_believes[0]
            _arc_traj = (character_input.arc_trajectory or "").lower()
            if any(kw in _arc_traj for kw in ("negative", "tragic", "fall", "堕落", "负面")):
                _arc_type = "negative"
            elif any(kw in _arc_traj for kw in ("flat", "考验", "守护")):
                _arc_type = "flat"
            else:
                _arc_type = "positive"
            _lie_truth_extra = {
                "lie_truth_arc": {
                    "core_lie": _core_lie,
                    "core_truth": f"与「{_core_lie}」相反的真相",
                    "transformation_cost": character_input.flaw or "必须放弃旧的保护方式",
                    "arc_type": _arc_type,
                    "current_phase": "believing_lie",
                },
            }

        character.metadata_json = _merge_metadata(
            character.metadata_json,
            {
                **character_input.metadata,
                **(character_input.model_extra or {}),
                **_lie_truth_extra,
            },
        )
        characters_upserted += 1
        characters_by_name[character.name] = character

    await session.flush()

    for character in characters_by_name.values():
        if await _ensure_initial_character_state_snapshot(
            session,
            project_id=project.id,
            character=character,
        ):
            state_snapshots_created += 1

    relationships_upserted = 0
    for owner in cast_spec.all_characters():
        for relation in owner.relationships:
            owner_model = characters_by_name.get(owner.name) or await get_or_create_character_by_name(
                session,
                project_id=project.id,
                character_name=owner.name,
                role=owner.role,
            )
            other_model = characters_by_name.get(relation.character) or await get_or_create_character_by_name(
                session,
                project_id=project.id,
                character_name=relation.character,
            )
            left_id, right_id = sorted((owner_model.id, other_model.id), key=lambda item: str(item))
            relationship_id = _stable_relationship_id(project.id, left_id, right_id)
            relationship = await session.get(RelationshipModel, relationship_id)
            if relationship is None:
                relationship = RelationshipModel(
                    id=relationship_id,
                    project_id=project.id,
                    character_a_id=left_id,
                    character_b_id=right_id,
                    relationship_type=relation.type,
                    strength=_base_role_strength(relation.type),
                    metadata_json={},
                )
                session.add(relationship)
            relationship.relationship_type = relation.type
            relationship.public_face = relation.type
            relationship.private_reality = relation.tension
            relationship.tension_summary = relation.tension
            relationship.last_changed_chapter_no = 0
            relationship.metadata_json = _merge_metadata(
                relationship.metadata_json,
                {"declared_by": owner.name},
            )
            relationships_upserted += 1

    conflict_buckets: dict[tuple[UUID, UUID], list[dict[str, Any]]] = defaultdict(list)
    for conflict in cast_spec.conflict_map:
        left_model = characters_by_name.get(conflict.character_a) or await get_or_create_character_by_name(
            session,
            project_id=project.id,
            character_name=conflict.character_a,
        )
        right_model = characters_by_name.get(conflict.character_b) or await get_or_create_character_by_name(
            session,
            project_id=project.id,
            character_name=conflict.character_b,
        )
        left_id, right_id = sorted((left_model.id, right_model.id), key=lambda item: str(item))
        conflict_buckets[(left_id, right_id)].append(conflict.model_dump(mode="json"))

    for (left_id, right_id), conflicts in conflict_buckets.items():
        relationship_id = _stable_relationship_id(project.id, left_id, right_id)
        relationship = await session.get(RelationshipModel, relationship_id)
        if relationship is None:
            relationship = RelationshipModel(
                id=relationship_id,
                project_id=project.id,
                character_a_id=left_id,
                character_b_id=right_id,
                relationship_type="conflict",
                strength=-0.4,
                metadata_json={},
            )
            session.add(relationship)
            relationships_upserted += 1
        relationship.tension_summary = relationship.tension_summary or conflicts[0].get("trigger_condition")
        relationship.metadata_json = _merge_metadata(
            relationship.metadata_json,
            {"conflict_map": conflicts},
        )

    await session.flush()
    return {
        "characters_upserted": characters_upserted,
        "relationships_upserted": relationships_upserted,
        "state_snapshots_created": state_snapshots_created,
        "voice_profiles_populated": voice_profiles_populated,
        "moral_frameworks_populated": moral_frameworks_populated,
    }


async def upsert_volume_plan(
    session: AsyncSession,
    project: ProjectModel,
    content: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, int]:
    volumes = parse_volume_plan_input(content)
    project.metadata_json = _merge_metadata(project.metadata_json, {"volume_plan": content})

    volumes_upserted = 0
    for entry in volumes:
        volume = await session.scalar(
            select(VolumeModel).where(
                VolumeModel.project_id == project.id,
                VolumeModel.volume_number == entry.volume_number,
            )
        )
        if volume is None:
            volume = VolumeModel(
                project_id=project.id,
                volume_number=entry.volume_number,
                title=entry.volume_title,
                metadata_json={},
            )
            session.add(volume)
        volume.title = entry.volume_title
        volume.theme = entry.volume_theme
        volume.goal = entry.volume_goal
        volume.obstacle = entry.volume_obstacle
        volume.target_word_count = _parse_volume_word_count(entry.word_count_target)
        volume.target_chapter_count = entry.chapter_count_target
        volume.metadata_json = _merge_metadata(
            volume.metadata_json,
            {
                "opening_state": entry.opening_state.model_dump(mode="json"),
                "volume_climax": entry.volume_climax,
                "volume_resolution": entry.volume_resolution.model_dump(mode="json"),
                "key_reveals": entry.key_reveals,
                "foreshadowing_planted": entry.foreshadowing_planted,
                "foreshadowing_paid_off": entry.foreshadowing_paid_off,
                "reader_hook_to_next": entry.reader_hook_to_next,
            },
        )
        volumes_upserted += 1

    if volumes:
        project.current_volume_number = max(volume.volume_number for volume in volumes)
    await session.flush()
    return {"volumes_upserted": volumes_upserted}


async def upsert_act_plan(
    session: AsyncSession,
    project: ProjectModel,
    act_plan: list[dict[str, Any]],
) -> dict[str, int]:
    """Store act plan into project.metadata_json and propagate act_id to volumes.

    The act plan is stored in project.metadata_json["act_plan"].
    Each volume's metadata_json is updated with its parent act_id and act_index
    when the volume's chapter range falls within an act's chapter range.
    """
    project.metadata_json = _merge_metadata(project.metadata_json, {"act_plan": act_plan})

    # Build act lookup: chapter_number → act info
    act_by_chapter: dict[int, dict[str, Any]] = {}
    for act in act_plan:
        for ch in range(act.get("chapter_start", 0), act.get("chapter_end", 0) + 1):
            act_by_chapter[ch] = act

    # Update existing volumes with act_id / act_index
    volumes_updated = 0
    volume_plan = (project.metadata_json or {}).get("volume_plan")
    if isinstance(volume_plan, list):
        for vol_entry in volume_plan:
            if not isinstance(vol_entry, dict):
                continue
            vol_num = vol_entry.get("volume_number")
            if vol_num is None:
                continue
            volume = await session.scalar(
                select(VolumeModel).where(
                    VolumeModel.project_id == project.id,
                    VolumeModel.volume_number == vol_num,
                )
            )
            if volume is None:
                continue
            # Find which act this volume belongs to based on its first chapter
            vol_start = _volume_start_chapter(vol_entry, vol_num, volume_plan)
            parent_act = act_by_chapter.get(vol_start)
            if parent_act:
                volume.metadata_json = _merge_metadata(
                    volume.metadata_json,
                    {
                        "act_id": parent_act.get("act_id"),
                        "act_index": parent_act.get("act_index"),
                    },
                )
                volumes_updated += 1

    await session.flush()
    return {"acts_stored": len(act_plan), "volumes_updated": volumes_updated}


def _volume_start_chapter(
    vol_entry: dict[str, Any],
    vol_num: int,
    volume_plan: list[dict[str, Any]],
) -> int:
    """Compute the first chapter number of a volume from the volume plan."""
    chapter_cursor = 1
    for v in volume_plan:
        if not isinstance(v, dict):
            continue
        vn = v.get("volume_number")
        if vn == vol_num:
            return chapter_cursor
        count = max(int(v.get("chapter_count_target") or 1), 1)
        chapter_cursor += count
    return chapter_cursor


async def get_latest_character_state(
    session: AsyncSession,
    *,
    project_id: UUID,
    character_id: UUID,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
) -> CharacterStateSnapshotModel | None:
    stmt = select(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.project_id == project_id,
        CharacterStateSnapshotModel.character_id == character_id,
    )
    if before_chapter_number is not None:
        if before_scene_number is None:
            stmt = stmt.where(CharacterStateSnapshotModel.chapter_number <= before_chapter_number)
        else:
            stmt = stmt.where(
                or_(
                    CharacterStateSnapshotModel.chapter_number < before_chapter_number,
                    and_(
                        CharacterStateSnapshotModel.chapter_number == before_chapter_number,
                        or_(
                            CharacterStateSnapshotModel.scene_number.is_(None),
                            CharacterStateSnapshotModel.scene_number < before_scene_number,
                        ),
                    ),
                )
            )
    stmt = stmt.order_by(
        CharacterStateSnapshotModel.chapter_number.desc(),
        CharacterStateSnapshotModel.scene_number.desc().nullslast(),
        CharacterStateSnapshotModel.created_at.desc(),
    )
    return await session.scalar(stmt.limit(1))


async def load_scene_story_bible_context(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
) -> dict[str, Any]:
    volume = None
    if chapter.volume_id is not None:
        volume = await session.get(VolumeModel, chapter.volume_id)

    world_expansion_context = await load_world_expansion_context(
        session,
        project=project,
        volume_number=volume.volume_number if volume is not None else None,
        chapter_number=chapter.chapter_number,
    )

    visible_rule_codes = set(
        world_expansion_context.get("volume_frontier", {}).get("visible_rule_codes", [])
        if isinstance(world_expansion_context.get("volume_frontier"), dict)
        else []
    )
    world_rule_stmt = (
        select(WorldRuleModel)
        .where(WorldRuleModel.project_id == project.id)
        .order_by(WorldRuleModel.rule_code.asc(), WorldRuleModel.name.asc())
        .limit(8)
    )
    if visible_rule_codes:
        world_rule_stmt = world_rule_stmt.where(WorldRuleModel.rule_code.in_(sorted(visible_rule_codes)))
    world_rules = list(await session.scalars(world_rule_stmt))
    characters = []
    relationships = []
    for participant_name in scene.participants:
        character = await session.get(CharacterModel, stable_character_id(project.id, participant_name))
        if character is None:
            continue
        latest_state = await get_latest_character_state(
            session,
            project_id=project.id,
            character_id=character.id,
            before_chapter_number=chapter.chapter_number,
            before_scene_number=scene.scene_number,
        )
        characters.append(
            {
                "name": character.name,
                "role": character.role,
                "background": character.background,
                "goal": character.goal,
                "fear": character.fear,
                "flaw": character.flaw,
                "arc_state": latest_state.arc_state if latest_state else character.arc_state,
                "power_tier": latest_state.power_tier if latest_state else character.power_tier,
                "knowledge_state": character.knowledge_state_json,
                "voice_profile": character.voice_profile_json,
                "moral_framework": character.moral_framework_json,
                "latest_state": latest_state.notes if latest_state is not None else None,
                "emotional_state": latest_state.emotional_state if latest_state is not None else None,
                "physical_state": latest_state.physical_state if latest_state is not None else None,
            }
        )

    if len(scene.participants) >= 2:
        participant_ids = [stable_character_id(project.id, name) for name in scene.participants]
        relationships = [
            {
                "relationship_type": item.relationship_type,
                "tension_summary": item.tension_summary,
                "public_face": item.public_face,
                "private_reality": item.private_reality,
            }
            for item in await session.scalars(
                select(RelationshipModel).where(
                    RelationshipModel.project_id == project.id,
                    RelationshipModel.character_a_id.in_(participant_ids),
                    RelationshipModel.character_b_id.in_(participant_ids),
                )
            )
        ]

    return {
        "book_spec": project.metadata_json.get("book_spec", {}),
        "cast_spec": project.metadata_json.get("cast_spec", {}),
        "logline": project.metadata_json.get("logline"),
        "themes": project.metadata_json.get("themes", []),
        "stakes": project.metadata_json.get("stakes", {}),
        "series_engine": project.metadata_json.get("series_engine", {}),
        **world_expansion_context,
        "volume": {
            "volume_number": volume.volume_number if volume is not None else None,
            "title": volume.title if volume is not None else None,
            "theme": volume.theme if volume is not None else None,
            "goal": volume.goal if volume is not None else None,
            "obstacle": volume.obstacle if volume is not None else None,
        },
        "world_rules": [
            {
                "rule_code": rule.rule_code,
                "name": rule.name,
                "description": rule.description,
                "story_consequence": rule.story_consequence,
            }
            for rule in world_rules
        ],
        "participants": characters,
        "relationships": relationships,
    }

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
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
from bestseller.services.character_identity_resolver import (
    canonical_character_key,
    collect_entry_aliases,
)
from bestseller.services.stage_seed import seed_character_inner_structure
from bestseller.services.world_expansion import load_world_expansion_context
from bestseller.services.writing_profile import is_english_language


logger = logging.getLogger(__name__)


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


def _character_aliases_from_input(char: CharacterInput) -> list[str]:
    """Collect alias strings from a CharacterInput via model_extra.

    ``CharacterInput`` keeps extra fields because it uses
    ``ConfigDict(extra="allow")``. LLM output may stash aliases in either
    ``aliases`` (list / string) or ``metadata.aliases``.
    """
    extras = getattr(char, "model_extra", None) or {}
    found: list[str] = []
    for source in (extras, char.metadata or {}):
        raw = source.get("aliases") if isinstance(source, dict) else None
        if isinstance(raw, str):
            if raw.strip() and raw.strip() not in found:
                found.append(raw.strip())
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, str):
                    trimmed = item.strip()
                    if trimmed and trimmed not in found:
                        found.append(trimmed)
    return found


def _dedupe_cast_inputs_by_identity(
    characters: list[CharacterInput],
) -> list[CharacterInput]:
    """Merge canonically-equivalent character inputs into a single entry.

    The LLM sometimes emits ``{"name": "王守真"}``, ``{"name": "王守真(三叔)"}``,
    and ``{"name": "三叔", "aliases": ["王守真"]}`` within the same cast_spec
    payload — currently each becomes a separate DB row because
    ``stable_character_id`` keys off the raw ``name``. This helper collapses
    them into one entry using canonical-key + alias matching.

    Match order per candidate against accumulated keepers:
        1. Exact ``name`` match
        2. Candidate ``name`` appears in keeper's aliases
        3. Keeper ``name`` appears in candidate's aliases
        4. ``canonical_character_key`` equal AND non-empty

    Merged entries keep the first-seen ``name`` but accumulate aliases from
    all folded duplicates. Non-empty scalar fields on duplicates fill holes
    left by the keeper; existing values are never overwritten.
    """
    keepers: list[CharacterInput] = []
    # Parallel cache of (name, aliases, canonical_key) per keeper for O(n²)
    # but bounded — a book's cast_spec stays well under a few hundred entries.
    keeper_alias_cache: list[tuple[str, list[str], str]] = []

    for candidate in characters:
        cand_name = _normalize_name(candidate.name)
        if not cand_name:
            continue
        cand_aliases = _character_aliases_from_input(candidate)
        cand_canonical = canonical_character_key(cand_name)

        match_idx: int | None = None
        for idx, (keeper_name, keeper_aliases, keeper_canonical) in enumerate(keeper_alias_cache):
            if cand_name == keeper_name:
                match_idx = idx
                break
            if cand_name in keeper_aliases:
                match_idx = idx
                break
            if keeper_name in cand_aliases:
                match_idx = idx
                break
            if cand_canonical and keeper_canonical and cand_canonical == keeper_canonical:
                match_idx = idx
                break

        if match_idx is None:
            keepers.append(candidate)
            keeper_alias_cache.append((cand_name, list(cand_aliases), cand_canonical))
            continue

        # Fold candidate into the existing keeper. We mutate a single
        # aliased dict — the keeper is the same object in ``keepers`` so
        # downstream writes to metadata survive.
        keeper = keepers[match_idx]
        keeper_name, keeper_aliases, keeper_canonical = keeper_alias_cache[match_idx]

        new_aliases = list(keeper_aliases)
        for alias in [cand_name, *cand_aliases]:
            if alias and alias != keeper_name and alias not in new_aliases:
                new_aliases.append(alias)

        # Persist aliases into keeper.metadata so the DB write path can pick
        # them up (metadata is merged into CharacterModel.metadata_json).
        keeper.metadata = dict(keeper.metadata or {})
        keeper.metadata["aliases"] = new_aliases

        # Hole-filling: copy non-empty scalars / strings from candidate where
        # keeper has None/empty. Authoritative keeper values are preserved.
        for field_name in (
            "background",
            "goal",
            "fear",
            "flaw",
            "strength",
            "secret",
            "arc_trajectory",
            "arc_state",
            "power_tier",
        ):
            if getattr(keeper, field_name, None) in (None, ""):
                _val = getattr(candidate, field_name, None)
                if _val not in (None, ""):
                    setattr(keeper, field_name, _val)
        if keeper.age is None and candidate.age is not None:
            keeper.age = candidate.age

        keeper_alias_cache[match_idx] = (keeper_name, new_aliases, keeper_canonical)

    return keepers


def _base_role_strength(role_type: str) -> float:
    normalized = role_type.strip().lower()
    if any(token in normalized for token in ("enemy", "敌", "仇")):
        return -0.8
    if any(token in normalized for token in ("rival", "对手", "竞争")):
        return -0.3
    if any(token in normalized for token in ("ally", "friend", "mentor", "爱", "恋", "搭档", "盟友")):
        return 0.6
    return 0.1


def _default_stance_from_role(role: str | None) -> str | None:
    """Pick a default stance value from cast spec role for new characters.

    Returns None when the role is ambiguous so the feedback loop can
    decide the stance from prose rather than baking in a guess.
    """
    if not role:
        return None
    normalized = role.strip().lower()
    if "protagonist" in normalized or "主角" in normalized:
        return "protagonist"
    if any(tok in normalized for tok in ("antagonist", "villain", "enemy", "反派", "敌")):
        return "enemy"
    if any(tok in normalized for tok in ("ally", "friend", "mentor", "盟友", "师", "搭档")):
        return "ally"
    if any(tok in normalized for tok in ("rival", "对手")):
        return "rival"
    if "neutral" in normalized or "中立" in normalized:
        return "neutral"
    return None


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


def _character_beliefs(knowledge_state: CharacterKnowledgeStateInput, *, language: str | None = None) -> list[str]:
    _is_en = (language or "").lower().startswith("en")
    beliefs = [str(item) for item in knowledge_state.knows]
    _false_prefix = "False belief: " if _is_en else "误判:"
    _unknown_prefix = "Unknown: " if _is_en else "未知:"
    beliefs.extend(f"{_false_prefix}{item}" for item in knowledge_state.falsely_believes)
    beliefs.extend(f"{_unknown_prefix}{item}" for item in knowledge_state.unaware_of)
    return beliefs


# Keys under which the LLM sometimes places world-rule descriptions when it
# forgets the canonical ``description`` field.  We probe these in order and
# fall back to ``name`` as a last resort so a stray missing-field crash no
# longer takes the entire autowrite pipeline down.
_WORLD_RULE_DESCRIPTION_ALIASES: tuple[str, ...] = (
    "description",
    "desc",
    "details",
    "detail",
    "trigger",
    "effect",
    "mechanism",
    "how_it_works",
    "summary",
    "explanation",
)


def _sanitize_world_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Fill in ``description`` when the LLM omits it.

    Some prompts/models emit world rules shaped like ``{"name": ..., "trigger":
    ...}`` or similar variants without a canonical ``description`` field.  The
    schema requires ``description`` (``min_length=1``) so the raw dict fails
    validation and blows up the entire story-bible materialization — which in
    turn fails the autowrite task.  This sanitizer coalesces common alias
    fields into ``description`` and falls back to the rule ``name`` as a
    last-ditch non-crashing default (with a warning), so 7x24 autowrite runs
    do not crash on a single sloppy LLM output.
    """
    if not isinstance(rule, dict):
        return rule
    description = rule.get("description")
    if isinstance(description, str) and description.strip():
        return rule

    sanitized = dict(rule)
    for alias in _WORLD_RULE_DESCRIPTION_ALIASES:
        if alias == "description":
            continue
        candidate = rule.get(alias)
        if isinstance(candidate, str) and candidate.strip():
            sanitized["description"] = candidate.strip()
            logger.warning(
                "world-rule sanitizer: using alias field %r as description for rule %r",
                alias,
                rule.get("name") or "<unnamed>",
            )
            return sanitized

    name = rule.get("name")
    if isinstance(name, str) and name.strip():
        sanitized["description"] = name.strip()
        logger.warning(
            "world-rule sanitizer: no description-like field found for rule %r; "
            "falling back to name so validation does not crash the pipeline",
            name,
        )
        return sanitized

    # Last resort: give it an explicit placeholder so the Pydantic min_length=1
    # constraint does not trip and take the whole pipeline down.  The stored
    # row still communicates that the upstream data was incomplete.
    sanitized["description"] = "(missing description — sanitizer fallback)"
    logger.warning(
        "world-rule sanitizer: rule had neither description nor name; "
        "using placeholder to keep pipeline alive: %r",
        rule,
    )
    return sanitized


def _sanitize_world_spec_content(content: dict[str, Any]) -> dict[str, Any]:
    """Non-destructive best-effort normalization before validation."""
    if not isinstance(content, dict):
        return content
    rules = content.get("rules")
    if not isinstance(rules, list):
        return content
    sanitized = dict(content)
    sanitized["rules"] = [_sanitize_world_rule(r) if isinstance(r, dict) else r for r in rules]
    return sanitized


def parse_world_spec_input(content: dict[str, Any]) -> WorldSpecInput:
    return WorldSpecInput.model_validate(_sanitize_world_spec_content(content))


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


# ── Volume title normalization ─────────────────────────────────────
# A placeholder title is one that carries no narrative signal — e.g. the
# generic "第N卷" / "Volume N" fallback patterns. These can slip in when
# the LLM leaves titles blank or when the planner fallback cannot map a
# volume to a milestone. We normalize at the persistence boundary so the
# ``volumes`` table and the stored ``metadata.volume_plan`` never expose
# placeholder names to readers or downstream prompts.

_PLACEHOLDER_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*第\s*\d+\s*卷\s*$"),
    re.compile(r"^\s*Volume\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*Vol\.?\s*\d+\s*$", re.IGNORECASE),
)


def _is_placeholder_volume_title(title: str | None) -> bool:
    if not title:
        return True
    stripped = title.strip()
    if not stripped:
        return True
    return any(pat.match(stripped) for pat in _PLACEHOLDER_TITLE_PATTERNS)


def normalize_volume_plan_titles(
    volumes: list[dict[str, Any]],
    *,
    is_en: bool,
    category_key: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Rewrite placeholder volume_title values to phase-based names.

    Called at persistence time so the DB never stores "第N卷"/"Volume N"
    regardless of whether the source was the LLM, the story_package
    milestones, or the fallback planner. Returns the normalized list and
    the number of titles that were replaced.
    """
    # Local import to avoid circular dependency with planner.py.
    from bestseller.services.planner import _resolve_fallback_volume_title

    phase_occurrence: dict[str, int] = {}
    used_titles: set[str] = set()
    replaced = 0
    normalized: list[dict[str, Any]] = []

    # Pre-seed used_titles with non-placeholder titles so phase pool
    # replacements don't collide with existing real titles.
    for entry in volumes:
        if not isinstance(entry, dict):
            continue
        title = entry.get("volume_title") or entry.get("title")
        if isinstance(title, str) and not _is_placeholder_volume_title(title):
            used_titles.add(title.strip())

    ordered = sorted(
        (e for e in volumes if isinstance(e, dict)),
        key=lambda e: int(e.get("volume_number") or 0),
    )
    for entry in ordered:
        new_entry = dict(entry)
        vn_raw = new_entry.get("volume_number")
        try:
            volume_number = int(vn_raw)
        except (TypeError, ValueError):
            normalized.append(new_entry)
            continue
        phase = str(new_entry.get("conflict_phase") or "").strip() or "survival"
        current_title = new_entry.get("volume_title") or new_entry.get("title") or ""
        occ = phase_occurrence.get(phase, 0)
        phase_occurrence[phase] = occ + 1
        if _is_placeholder_volume_title(current_title):
            candidate = _resolve_fallback_volume_title(phase, occ, volume_number, is_en=is_en)
            safety = 0
            while candidate in used_titles and safety < 200:
                occ += 1
                candidate = _resolve_fallback_volume_title(phase, occ, volume_number, is_en=is_en)
                safety += 1
            new_entry["volume_title"] = candidate
            used_titles.add(candidate)
            replaced += 1
            logger.warning(
                "Replaced placeholder volume title (volume_number=%s, phase=%s) -> %s",
                volume_number,
                phase,
                candidate,
            )
        normalized.append(new_entry)

    return normalized, replaced


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
        _is_en = is_english_language(project.language)
        _theme_prefix = "Theme: " if _is_en else "主题:"
        style_guide.custom_rules = [
            f"{_theme_prefix}{theme}" for theme in content.get("themes", []) if str(theme).strip()
        ]
    if isinstance(content.get("series_engine"), dict):
        series_engine = content["series_engine"]
        hook_style = series_engine.get("hook_style")
        if hook_style:
            _se_prefix = "Series engine: " if is_english_language(project.language) else "连载引擎:"
            style_guide.reference_works = [f"{_se_prefix}{hook_style}"]
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
    language: str | None = None,
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
        beliefs=_character_beliefs(CharacterKnowledgeStateInput.model_validate(character.knowledge_state_json or {}), language=language),
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

    # Fold canonical / aliased duplicates *before* hitting the DB. This is
    # the primary defence against blood-twins' 71-duplicate-character bug:
    # ``{"name":"王守真"}`` and ``{"name":"王守真(三叔)"}`` collapse to one
    # ``CharacterInput`` before ``stable_character_id`` fans them out.
    deduped_inputs = _dedupe_cast_inputs_by_identity(list(cast_spec.all_characters()))

    # Build a lookup of existing rows for the project once so we can match
    # against alias lists stored on ``CharacterModel.metadata_json``. This
    # handles the cross-session case where earlier cast_spec writes wrote
    # ``王守真`` and a later one arrives with ``{"name":"三叔"}``.
    _existing_rows = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project.id)
        )
    )
    _existing_by_alias: dict[str, CharacterModel] = {}
    for _row in _existing_rows:
        _name = _normalize_name(_row.name or "")
        if _name:
            _existing_by_alias.setdefault(_name, _row)
            _canonical_existing = canonical_character_key(_name)
            if _canonical_existing and _canonical_existing != _name:
                _existing_by_alias.setdefault(_canonical_existing, _row)
        _row_meta = _row.metadata_json or {}
        _row_aliases: list[str] = []
        for _source_key in ("aliases",):
            _raw = _row_meta.get(_source_key) if isinstance(_row_meta, dict) else None
            if isinstance(_raw, list):
                _row_aliases.extend([a for a in _raw if isinstance(a, str)])
            elif isinstance(_raw, str) and _raw.strip():
                _row_aliases.append(_raw.strip())
        _cast_entry = _row_meta.get("cast_entry") if isinstance(_row_meta, dict) else None
        if isinstance(_cast_entry, dict):
            _raw_ce = _cast_entry.get("aliases")
            if isinstance(_raw_ce, list):
                _row_aliases.extend([a for a in _raw_ce if isinstance(a, str)])
            elif isinstance(_raw_ce, str) and _raw_ce.strip():
                _row_aliases.append(_raw_ce.strip())
        for _alias in _row_aliases:
            _alias_trim = _alias.strip()
            if _alias_trim:
                _existing_by_alias.setdefault(_alias_trim, _row)

    for character_input in deduped_inputs:
        # Alias-aware DB lookup: prefer an existing row under a different
        # name over creating a new ``stable_character_id`` duplicate.
        _candidate_aliases = _character_aliases_from_input(character_input)
        _canonical_candidate = canonical_character_key(character_input.name)
        _existing_row: CharacterModel | None = _existing_by_alias.get(
            _normalize_name(character_input.name)
        )
        if _existing_row is None and _canonical_candidate:
            _existing_row = _existing_by_alias.get(_canonical_candidate)
        if _existing_row is None:
            for _alias in _candidate_aliases:
                _existing_row = _existing_by_alias.get(_alias.strip())
                if _existing_row is not None:
                    break

        _reused_via_alias = False
        if _existing_row is not None:
            character = _existing_row
            character_id = character.id
            _reused_via_alias = True
            # Fold the new name + aliases into the existing row so future
            # lookups find it too. Existing ``character.name`` is preserved.
            _meta = dict(character.metadata_json or {})
            _current_aliases = list(collect_entry_aliases(_meta))
            for _alias in [character_input.name, *_candidate_aliases]:
                _alias_trim = _alias.strip() if isinstance(_alias, str) else ""
                if (
                    _alias_trim
                    and _alias_trim != _normalize_name(character.name or "")
                    and _alias_trim not in _current_aliases
                ):
                    _current_aliases.append(_alias_trim)
            if _current_aliases:
                _meta["aliases"] = _current_aliases
            character.metadata_json = _meta
        else:
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
            # Register so subsequent inputs in this batch resolve to this row.
            _existing_by_alias[_normalize_name(character_input.name)] = character
            if _canonical_candidate:
                _existing_by_alias.setdefault(_canonical_candidate, character)
        _prior_role = character.role or ""
        if not _reused_via_alias:
            # Only rewrite the canonical name when we did NOT match via alias.
            # Otherwise we'd overwrite ``王守真`` with ``三叔`` on a reuse.
            character.name = _normalize_name(character_input.name)
        character.role = character_input.role
        # Alias-reuse safeguard: never downgrade protagonist / antagonist to
        # supporting via a merged alias entry. Preserves the identity role
        # for the canonical keeper row.
        if _reused_via_alias and _prior_role in ("protagonist", "antagonist"):
            character.role = _prior_role
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
        if not getattr(character, "alive_status", None):
            character.alive_status = "alive"
        # Seed stance from role on first create so default "enemy"/"ally" is
        # available before feedback fires. Never overwrite once set — stance
        # updates are event-gated in feedback._apply_character_state_updates.
        if getattr(character, "stance", None) in (None, ""):
            character.stance = _default_stance_from_role(character_input.role)
        # Anchor POV on the canonical role after alias-reuse safeguards; this
        # prevents an aliased ``supporting`` entry from stealing the protagonist
        # flag away from a ``王守真`` row.
        character.is_pov_character = character.role == "protagonist"
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
            _is_en = is_english_language(project.language)
            _lie_truth_extra = {
                "lie_truth_arc": {
                    "core_lie": _core_lie,
                    "core_truth": (
                        f"The truth that opposes \"{_core_lie}\""
                        if _is_en else
                        f"与「{_core_lie}」相反的真相"
                    ),
                    "transformation_cost": character_input.flaw or (
                        "Must abandon old protective patterns" if _is_en else "必须放弃旧的保护方式"
                    ),
                    "arc_type": _arc_type,
                    "current_phase": "believing_lie",
                },
            }

        _inner_structure = seed_character_inner_structure(
            character_input,
            lie_truth_arc=_lie_truth_extra.get("lie_truth_arc"),
        )
        _stage_c_extra: dict[str, Any] = (
            {"inner_structure": _inner_structure} if _inner_structure else {}
        )

        character.metadata_json = _merge_metadata(
            character.metadata_json,
            {
                **character_input.metadata,
                **(character_input.model_extra or {}),
                **_lie_truth_extra,
                **_stage_c_extra,
            },
        )
        characters_upserted += 1
        characters_by_name[character.name] = character

    # Cross-reference antagonist_forces[].active_volumes into character.metadata_json
    # so downstream routing (narrative._build_antagonist_plan_specs, conflict arcs,
    # per-volume antagonist selection) can resolve per-volume antagonists instead
    # of collapsing to the single primary. Root cause of the xianxia failure
    # (all 25 antagonist_plans labeled with the primary antagonist) lived here:
    # the LLM supplies active_volumes on forces, but nothing propagated it to the
    # character rows that downstream routing reads.
    force_active_by_name: dict[str, set[int]] = {}
    for force in cast_spec.antagonist_forces:
        ref = (force.character_ref or "").strip()
        if not ref:
            continue
        normalized_ref = _normalize_name(ref)
        bucket = force_active_by_name.setdefault(normalized_ref, set())
        for vol in force.active_volumes or []:
            if isinstance(vol, int) and vol > 0:
                bucket.add(vol)
    for name, active_vols in force_active_by_name.items():
        character = characters_by_name.get(name)
        if character is None:
            continue
        sorted_vols = sorted(active_vols)
        current_meta = character.metadata_json if isinstance(character.metadata_json, dict) else {}
        existing = current_meta.get("active_volumes") or []
        merged_vols = sorted(set(existing) | set(sorted_vols)) if isinstance(existing, list) else sorted_vols
        character.metadata_json = _merge_metadata(
            current_meta,
            {"active_volumes": merged_vols},
        )
        # Promote supporting_cast entries referenced by antagonist_forces.character_ref
        # to role='antagonist' so downstream routing (narrative._build_antagonist_plan_specs)
        # can include them in its per-volume selection pool. Without this, only the
        # primary antagonist is routable and every plan falls back to them.
        if character.role != "antagonist" and character.role != "protagonist":
            character.role = "antagonist"

    await session.flush()

    for character in characters_by_name.values():
        if await _ensure_initial_character_state_snapshot(
            session,
            project_id=project.id,
            character=character,
            language=project.language,
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
    # System-level title normalization: any placeholder ("第N卷" / "Volume N")
    # that survives LLM/fallback generation is replaced with a phase-based
    # name before we parse-and-persist. This is the single enforcement point
    # that covers every write path into the volumes table.
    is_en = is_english_language(project.language)
    category_key = None
    if isinstance(project.metadata_json, dict):
        raw_cat = project.metadata_json.get("category_key")
        if isinstance(raw_cat, str) and raw_cat.strip():
            category_key = raw_cat.strip()
    if isinstance(content, list):
        raw_volumes = [dict(e) for e in content if isinstance(e, dict)]
        normalized_volumes, replaced = normalize_volume_plan_titles(
            raw_volumes, is_en=is_en, category_key=category_key
        )
        if replaced:
            content = normalized_volumes
    elif isinstance(content, dict) and isinstance(content.get("volumes"), list):
        raw_volumes = [dict(e) for e in content["volumes"] if isinstance(e, dict)]
        normalized_volumes, replaced = normalize_volume_plan_titles(
            raw_volumes, is_en=is_en, category_key=category_key
        )
        if replaced:
            content = {**content, "volumes": normalized_volumes}

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


@dataclass(frozen=True)
class EffectiveCharacterState:
    """Per-field snapshot after "most recent non-null" fallback.

    Each field traces back chronologically through snapshots until a
    non-null value is found, with the CharacterModel row as the final
    fallback. This prevents prompt renderings like "Power:未定义" when a
    more recent snapshot happened to leave power_tier null while earlier
    snapshots already established the value.
    """

    arc_state: str | None
    power_tier: str | None
    emotional_state: str | None
    physical_state: str | None
    alive_status: str | None
    stance: str | None
    notes: str | None
    latest_chapter_number: int | None
    latest_scene_number: int | None


async def get_effective_character_state(
    session: AsyncSession,
    *,
    project_id: UUID,
    character: CharacterModel,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
) -> EffectiveCharacterState:
    """Resolve each lifecycle field via most-recent non-null snapshot.

    Strategy:
    1. Fetch all snapshots for (project, character) up to the cutoff in
       descending chronological order (capped to avoid unbounded scans).
    2. For each tracked field, walk snapshots until a non-null value is
       found; else fall back to CharacterModel.
    3. latest_chapter/scene_number / notes reference the newest snapshot
       regardless of whether any field was non-null there.
    """
    stmt = select(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.project_id == project_id,
        CharacterStateSnapshotModel.character_id == character.id,
    )
    if before_chapter_number is not None:
        if before_scene_number is None:
            stmt = stmt.where(
                CharacterStateSnapshotModel.chapter_number <= before_chapter_number
            )
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
    ).limit(64)
    snapshots = list(await session.scalars(stmt))

    def _first_non_null(attr: str) -> Any:
        for snap in snapshots:
            value = getattr(snap, attr, None)
            if value not in (None, ""):
                return value
        return None

    arc_state = _first_non_null("arc_state") or character.arc_state
    power_tier = _first_non_null("power_tier") or character.power_tier
    emotional_state = _first_non_null("emotional_state")
    physical_state = _first_non_null("physical_state")
    alive_status = _first_non_null("alive_status") or getattr(character, "alive_status", None)
    stance = _first_non_null("stance") or getattr(character, "stance", None)

    latest = snapshots[0] if snapshots else None
    return EffectiveCharacterState(
        arc_state=arc_state,
        power_tier=power_tier,
        emotional_state=emotional_state,
        physical_state=physical_state,
        alive_status=alive_status,
        stance=stance,
        notes=latest.notes if latest is not None else None,
        latest_chapter_number=latest.chapter_number if latest is not None else None,
        latest_scene_number=latest.scene_number if latest is not None else None,
    )


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
        effective = await get_effective_character_state(
            session,
            project_id=project.id,
            character=character,
            before_chapter_number=chapter.chapter_number,
            before_scene_number=scene.scene_number,
        )
        stance_locked_until = getattr(character, "stance_locked_until_chapter", None)
        characters.append(
            {
                "name": character.name,
                "role": character.role,
                "background": character.background,
                "goal": character.goal,
                "fear": character.fear,
                "flaw": character.flaw,
                "arc_state": effective.arc_state,
                "power_tier": effective.power_tier,
                "knowledge_state": character.knowledge_state_json,
                "voice_profile": character.voice_profile_json,
                "moral_framework": character.moral_framework_json,
                "latest_state": effective.notes,
                "emotional_state": effective.emotional_state,
                "physical_state": effective.physical_state,
                "alive_status": effective.alive_status,
                "stance": effective.stance,
                "stance_locked_until_chapter": stance_locked_until,
                "death_chapter_number": getattr(character, "death_chapter_number", None),
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

    # Deceased roster — cap at recent 20 to avoid prompt bloat on long runs.
    # Filter to deaths that occurred before the current chapter so the prompt
    # can explicitly warn "do not resurrect".
    deceased_stmt = (
        select(CharacterModel)
        .where(
            CharacterModel.project_id == project.id,
            CharacterModel.alive_status == "deceased",
            or_(
                CharacterModel.death_chapter_number.is_(None),
                CharacterModel.death_chapter_number < chapter.chapter_number,
            ),
        )
        .order_by(CharacterModel.death_chapter_number.desc().nullslast())
        .limit(20)
    )
    deceased_characters = [
        {
            "name": dead.name,
            "death_chapter_number": dead.death_chapter_number,
            "role": dead.role,
        }
        for dead in await session.scalars(deceased_stmt)
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
        "deceased_characters": deceased_characters,
    }


# ---------------------------------------------------------------------------
# Living Story Bible — update character/world state after each chapter
# ---------------------------------------------------------------------------

_BIBLE_UPDATE_SYSTEM_PROMPT_ZH = """\
你是一个小说编辑助手，负责在每一章完成后更新故事圣经（Story Bible）。
阅读以下章节文本，提取以下变化：
1. **角色知识更新**: 每个角色新获得的信息、错误认知、以及仍不知道的重要信息
2. **关系变化**: 角色之间关系的变化（亲密度、敌意、信任等）
3. **世界观更新**: 新揭示的世界规则或位置
4. **角色状态变化**: 身体状态、情感状态、力量等级变化

请以JSON格式输出：
```json
{
  "character_updates": [
    {
      "name": "角色名",
      "knowledge_gained": ["新获得的信息"],
      "false_beliefs": ["错误认知"],
      "emotional_state": "当前情感状态",
      "physical_state": "身体状态变化",
      "power_change": "力量/等级变化描述"
    }
  ],
  "relationship_updates": [
    {
      "character_a": "角色A",
      "character_b": "角色B",
      "change": "关系变化描述",
      "affinity_direction": "up|down|neutral"
    }
  ],
  "world_updates": [
    {
      "rule_or_location": "规则或位置名称",
      "description": "描述"
    }
  ]
}
```
只输出JSON，不要输出其他内容。如果没有变化，对应数组为空。"""

_BIBLE_UPDATE_SYSTEM_PROMPT_EN = """\
You are a novel editing assistant responsible for updating the Story Bible after each chapter.
Read the chapter text below and extract the following changes:
1. **Character knowledge updates**: New information each character gained, false beliefs, important things they still don't know
2. **Relationship changes**: Changes in relationships between characters (intimacy, hostility, trust, etc.)
3. **World updates**: Newly revealed world rules or locations
4. **Character state changes**: Physical state, emotional state, power level changes

Output in JSON format:
```json
{
  "character_updates": [
    {
      "name": "character name",
      "knowledge_gained": ["new information gained"],
      "false_beliefs": ["false beliefs"],
      "emotional_state": "current emotional state",
      "physical_state": "physical state change",
      "power_change": "power/level change description"
    }
  ],
  "relationship_updates": [
    {
      "character_a": "Character A",
      "character_b": "Character B",
      "change": "relationship change description",
      "affinity_direction": "up|down|neutral"
    }
  ],
  "world_updates": [
    {
      "rule_or_location": "rule or location name",
      "description": "description"
    }
  ]
}
```
Output JSON only, nothing else. If no changes, use empty arrays."""


async def update_story_bible_from_chapter(
    session: AsyncSession,
    settings: "AppSettings",
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_text: str,
    workflow_run_id: UUID | None = None,
) -> dict[str, int]:
    """Extract and apply story bible updates from a completed chapter.

    Uses the ``editor`` LLM role to extract character knowledge, relationship,
    and world-building changes, then persists them to the database.

    Returns a dict of counts: characters_updated, relationships_updated, world_rules_added.
    """
    from bestseller.services.llm import LLMCompletionRequest, complete_text

    language = getattr(project, "language", None) or "zh-CN"
    is_zh = language.lower().startswith("zh")

    system_prompt = _BIBLE_UPDATE_SYSTEM_PROMPT_ZH if is_zh else _BIBLE_UPDATE_SYSTEM_PROMPT_EN
    user_prompt = (
        f"章节: 第{chapter.chapter_number}章 {chapter.title or ''}\n\n"
        f"正文:\n{chapter_text[:12000]}"
    ) if is_zh else (
        f"Chapter: {chapter.chapter_number} — {chapter.title or ''}\n\n"
        f"Text:\n{chapter_text[:12000]}"
    )

    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response='{"character_updates":[],"relationship_updates":[],"world_updates":[]}',
            prompt_template="bible_update_v1",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            metadata={"chapter_number": chapter.chapter_number},
        ),
    )

    # Parse LLM response
    try:
        raw = result.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        updates = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse bible update JSON for ch%d", chapter.chapter_number)
        return {"characters_updated": 0, "relationships_updated": 0, "world_rules_added": 0}

    counts = {"characters_updated": 0, "relationships_updated": 0, "world_rules_added": 0}

    # Apply character updates
    for cu in updates.get("character_updates", []):
        name = cu.get("name", "").strip()
        if not name:
            continue
        try:
            char = await get_or_create_character_by_name(
                session, project_id=project.id, character_name=name,
            )
            ks = dict(char.knowledge_state_json or {})
            # Merge knowledge
            existing_knows = ks.get("knows", [])
            for item in cu.get("knowledge_gained", []):
                if item and item not in existing_knows:
                    existing_knows.append(item)
            ks["knows"] = existing_knows[-30:]  # cap at 30 entries

            existing_false = ks.get("falsely_believes", [])
            for item in cu.get("false_beliefs", []):
                if item and item not in existing_false:
                    existing_false.append(item)
            ks["falsely_believes"] = existing_false[-10:]

            char.knowledge_state_json = ks

            # Update metadata with emotional/physical state
            md = dict(char.metadata_json or {})
            if cu.get("emotional_state"):
                md["emotional_state"] = cu["emotional_state"]
            if cu.get("physical_state"):
                md["physical_state"] = cu["physical_state"]
            if cu.get("power_change"):
                md["latest_power_change"] = cu["power_change"]
                md["power_change_chapter"] = chapter.chapter_number
            char.metadata_json = md

            counts["characters_updated"] += 1
        except Exception:
            logger.debug("Failed to update character '%s' (non-fatal)", name, exc_info=True)

    # Apply relationship updates
    for ru in updates.get("relationship_updates", []):
        char_a_name = ru.get("character_a", "").strip()
        char_b_name = ru.get("character_b", "").strip()
        if not char_a_name or not char_b_name:
            continue
        try:
            char_a = await get_or_create_character_by_name(
                session, project_id=project.id, character_name=char_a_name,
            )
            char_b = await get_or_create_character_by_name(
                session, project_id=project.id, character_name=char_b_name,
            )
            rel_id = _stable_relationship_id(project.id, char_a.id, char_b.id)
            rel = await session.get(RelationshipModel, rel_id)
            if rel is None:
                rel = RelationshipModel(
                    id=rel_id,
                    project_id=project.id,
                    character_a_id=char_a.id,
                    character_b_id=char_b.id,
                    label=ru.get("change", "neutral"),
                    current_affinity=0.5,
                    metadata_json={},
                )
                session.add(rel)
            # Update affinity
            direction = ru.get("affinity_direction", "neutral")
            delta = {"up": 0.1, "down": -0.1}.get(direction, 0.0)
            rel.current_affinity = max(0.0, min(1.0, (rel.current_affinity or 0.5) + delta))
            rel.label = ru.get("change", rel.label)
            # Track events
            events = list(rel.metadata_json.get("events", []) if rel.metadata_json else [])
            events.append({
                "chapter": chapter.chapter_number,
                "change": ru.get("change", ""),
                "direction": direction,
            })
            rel.metadata_json = {**(rel.metadata_json or {}), "events": events[-20:]}

            counts["relationships_updated"] += 1
        except Exception:
            logger.debug(
                "Failed to update relationship '%s'-'%s' (non-fatal)",
                char_a_name, char_b_name, exc_info=True,
            )

    # Apply world updates
    for wu in updates.get("world_updates", []):
        rule_name = wu.get("rule_or_location", "").strip()
        if not rule_name:
            continue
        try:
            rule_code = re.sub(r"[^a-z0-9_]", "_", rule_name.lower())[:50]
            rule_id = stable_world_rule_id(project.id, rule_code)
            existing = await session.get(WorldRuleModel, rule_id)
            if existing is None:
                rule = WorldRuleModel(
                    id=rule_id,
                    project_id=project.id,
                    rule_code=rule_code,
                    name=rule_name,
                    description=wu.get("description", ""),
                    metadata_json={
                        "source": f"chapter_{chapter.chapter_number}",
                        "reveal_chapter": chapter.chapter_number,
                    },
                )
                session.add(rule)
                counts["world_rules_added"] += 1
            else:
                # Update description if richer
                if wu.get("description") and len(wu["description"]) > len(existing.description or ""):
                    existing.description = wu["description"]
        except Exception:
            logger.debug("Failed to add world rule '%s' (non-fatal)", rule_name, exc_info=True)

    await session.flush()
    logger.info(
        "Story bible updated for ch%d: %s",
        chapter.chapter_number,
        counts,
    )
    return counts

"""Validated current world-rule reads; immutable histories remain audit evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel, WorldRuleModel


async def current_world_rules(
    session: AsyncSession,
    *,
    project_id: UUID,
    world_rules: list[WorldRuleModel] | None = None,
) -> list[WorldRuleModel]:
    """Keep retained creation-only history out of the current Bible view.

    Empty prose is never proof of withdrawal. The existing projection planner
    must establish that every original contribution is inactive and that the
    stored owned fields are exactly the retained empty creation preimage.
    """
    from bestseller.services import story_bible_projection as projection
    from bestseller.services.world_rule_identity_recovery import world_rule_effective_history

    if world_rules is None:
        with session.no_autoflush:
            world_rules = list(
                await session.scalars(
                    select(WorldRuleModel)
                    .where(WorldRuleModel.project_id == project_id)
                    .order_by(WorldRuleModel.rule_code.asc(), WorldRuleModel.name.asc())
                )
            )
    projection._require(all(rule.project_id == project_id for rule in world_rules))
    candidates = [
        rule
        for rule in world_rules
        if isinstance(
            (rule.metadata_json or {}).get(projection.BASE),
            dict,
        )
        and (rule.metadata_json or {})[projection.BASE].get("created") is True
    ]
    if not candidates:
        return world_rules
    with session.no_autoflush:
        changes, legacy, active, _ = await projection._plan(session, project_id)
    projection._require(all(item == "legacy_current" for item in legacy))
    desired_by_id = {
        row.id: desired
        for row, desired in changes
        if isinstance(row, WorldRuleModel) and row.project_id == project_id
    }
    retired = set()
    for rule in candidates:
        baseline = (rule.metadata_json or {})[projection.BASE]
        desired = desired_by_id.get(rule.id)
        history = await world_rule_effective_history(
            session,
            project_id=project_id,
            row_id=rule.id,
        )
        projection._require(rule.project_id == project_id and desired is not None)
        sources = [entry.get("source_lineage") for entry in history]
        # An empty effective history can be proved by signed identity exclusions.
        # An entirely history-free creation still has no retirement witness.
        inactive = bool(projection._history(rule)) and all(
            not projection.same_source(
                source,
                active.get(source.get("chapter_id")),
            )
            for source in sources
        )
        if not inactive:
            continue
        projection._require(
            baseline.get("history_prefix") == []
            and baseline.get("fields") == {"description": "", "metadata": {}}
            and desired == baseline["fields"] == projection._fields(rule)
        )
        retired.add(rule.id)
    return [rule for rule in world_rules if rule.id not in retired]


async def load_scene_story_bible_context(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
) -> dict[str, Any]:
    """Adapt the frozen loader; retire shells before its eight-rule context cap.

    The underlying loader remains unchanged for the identity freeze. Current
    writer consumers import this adapter, preserving its frontier and ordering.
    """
    from bestseller.services.story_bible import load_scene_story_bible_context as load_raw

    rules = await current_world_rules(session, project_id=project.id)
    payload = await load_raw(session, project=project, chapter=chapter, scene=scene)
    frontier = payload.get("volume_frontier")
    visible = set(frontier.get("visible_rule_codes", [])) if isinstance(frontier, dict) else set()
    if visible:
        rules = [rule for rule in rules if rule.rule_code in visible]
    return {
        **payload,
        "world_rules": [
            {
                "rule_code": rule.rule_code,
                "name": rule.name,
                "description": rule.description,
                "story_consequence": rule.story_consequence,
            }
            for rule in rules[:8]
        ],
    }

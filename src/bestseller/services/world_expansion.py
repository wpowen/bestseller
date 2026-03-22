from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CharacterModel,
    ChapterModel,
    DeferredRevealModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    PlotArcModel,
    ProjectModel,
    VolumeFrontierModel,
    VolumeModel,
    WorldBackboneModel,
    WorldRuleModel,
)


async def _scalars_list(session: AsyncSession, stmt: Any) -> list[Any]:
    scalars_method = getattr(session, "scalars", None)
    if callable(scalars_method):
        return list(await scalars_method(stmt))
    return []


async def _safe_execute(session: AsyncSession, stmt: Any) -> None:
    execute_method = getattr(session, "execute", None)
    if callable(execute_method):
        await execute_method(stmt)


def _clean_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clean_list(values: Iterable[Any] | None) -> list[str]:
    if not values:
        return []
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _estimate_volume_chapter_targets(
    project: ProjectModel,
    volumes: list[VolumeModel],
) -> dict[int, int]:
    if not volumes:
        return {}
    explicit = {
        volume.volume_number: volume.target_chapter_count
        for volume in volumes
        if volume.target_chapter_count and volume.target_chapter_count > 0
    }
    missing = [volume.volume_number for volume in volumes if volume.volume_number not in explicit]
    if not missing:
        return {key: int(value) for key, value in explicit.items()}

    remaining = max(project.target_chapters - sum(explicit.values()), len(missing))
    base = max(1, remaining // len(missing))
    extra = max(0, remaining - base * len(missing))

    targets = {key: int(value) for key, value in explicit.items()}
    for index, volume_number in enumerate(missing):
        targets[volume_number] = base + (1 if index < extra else 0)
    return targets


def estimate_volume_chapter_ranges(
    project: ProjectModel,
    volumes: list[VolumeModel],
    chapters: list[ChapterModel],
) -> dict[int, tuple[int, int | None]]:
    if not volumes:
        return {}

    targets = _estimate_volume_chapter_targets(project, volumes)
    actual_by_volume: dict[object, list[int]] = {}
    for chapter in chapters:
        if chapter.volume_id is None:
            continue
        actual_by_volume.setdefault(chapter.volume_id, []).append(chapter.chapter_number)

    ranges: dict[int, tuple[int, int | None]] = {}
    cursor = 1
    for volume in sorted(volumes, key=lambda item: item.volume_number):
        actual_numbers = sorted(actual_by_volume.get(volume.id, []))
        if actual_numbers:
            start = actual_numbers[0]
            end = actual_numbers[-1]
        else:
            count = max(1, targets.get(volume.volume_number, 1))
            start = cursor
            end = start + count - 1
        ranges[volume.volume_number] = (start, end)
        cursor = max(cursor, (end or start) + 1)
    return ranges


def _cumulative_slice(items: list[str], *, total: int, upto: int, minimum: int = 1) -> list[str]:
    if not items:
        return []
    if total <= 1:
        return list(items)
    visible_count = max(minimum, math.ceil(len(items) * upto / total))
    return list(items[: min(len(items), visible_count)])


def _derive_backbone_payload(
    *,
    project: ProjectModel,
    protagonist: CharacterModel | None,
    antagonist: CharacterModel | None,
    world_rules: list[WorldRuleModel],
    volumes: list[VolumeModel],
) -> dict[str, Any]:
    metadata = dict(project.metadata_json or {})
    themes = _clean_list(metadata.get("themes"))
    world_name = _clean_text(metadata.get("world_name"))
    world_premise = _clean_text(metadata.get("world_premise"))
    power_structure = _clean_text(metadata.get("power_structure"))
    forbidden_zones = _clean_text(metadata.get("forbidden_zones"))
    logline = _clean_text(metadata.get("logline")) or project.title

    protagonist_goal = _clean_text(getattr(protagonist, "goal", None))
    protagonist_arc = _clean_text(getattr(protagonist, "arc_trajectory", None))
    antagonist_goal = _clean_text(getattr(antagonist, "goal", None))
    first_volume_goal = _clean_text(volumes[0].goal) if volumes else None
    first_volume_obstacle = _clean_text(volumes[0].obstacle) if volumes else None

    mainline_drive = (
        first_volume_goal
        or protagonist_goal
        or _clean_text(metadata.get("stakes", {}).get("personal") if isinstance(metadata.get("stakes"), dict) else None)
        or logline
    )
    invariant_elements = _clean_list(
        [rule.name for rule in world_rules[:5]]
        + [power_structure, forbidden_zones]
    )
    stable_unknowns = _clean_list(
        item
        for volume in volumes[:3]
        for item in (
            list((volume.metadata_json or {}).get("foreshadowing_planted", []))[:2]
            + [volume.metadata_json.get("reader_hook_to_next")]  # type: ignore[arg-type]
        )
    )
    world_frame = " / ".join(
        item
        for item in (world_name, world_premise, power_structure)
        if item
    ) or None
    thematic_melody = " / ".join(themes) if themes else None
    antagonist_axis = " / ".join(item for item in (getattr(antagonist, "name", None), antagonist_goal, first_volume_obstacle) if item) or None
    protagonist_destiny = " / ".join(item for item in (getattr(protagonist, "name", None), protagonist_goal, protagonist_arc) if item) or None

    return {
        "title": "全书世界主干",
        "core_promise": logline,
        "mainline_drive": mainline_drive,
        "protagonist_destiny": protagonist_destiny,
        "antagonist_axis": antagonist_axis,
        "thematic_melody": thematic_melody,
        "world_frame": world_frame,
        "invariant_elements": invariant_elements,
        "stable_unknowns": stable_unknowns,
        "metadata": {
            "source": "world_expansion.refresh",
            "world_name": world_name,
            "series_engine": metadata.get("series_engine", {}),
        },
    }


async def refresh_world_expansion_boundaries(
    session: AsyncSession,
    *,
    project: ProjectModel,
) -> dict[str, int]:
    volumes = list(
        await _scalars_list(
            session,
            select(VolumeModel)
            .where(VolumeModel.project_id == project.id)
            .order_by(VolumeModel.volume_number.asc())
        )
    )
    chapters = list(
        await _scalars_list(
            session,
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    world_rules = list(
        await _scalars_list(
            session,
            select(WorldRuleModel)
            .where(WorldRuleModel.project_id == project.id)
            .order_by(WorldRuleModel.rule_code.asc(), WorldRuleModel.name.asc())
        )
    )
    locations = list(
        await _scalars_list(
            session,
            select(LocationModel)
            .where(LocationModel.project_id == project.id)
            .order_by(LocationModel.name.asc())
        )
    )
    factions = list(
        await _scalars_list(
            session,
            select(FactionModel)
            .where(FactionModel.project_id == project.id)
            .order_by(FactionModel.name.asc())
        )
    )
    plot_arcs = list(
        await _scalars_list(
            session,
            select(PlotArcModel)
            .where(PlotArcModel.project_id == project.id)
            .order_by(PlotArcModel.arc_type.asc(), PlotArcModel.arc_code.asc())
        )
    )
    characters = list(
        await _scalars_list(
            session,
            select(CharacterModel)
            .where(CharacterModel.project_id == project.id)
            .order_by(CharacterModel.role.asc(), CharacterModel.name.asc())
        )
    )
    protagonist = next((item for item in characters if item.role == "protagonist"), None)
    antagonist = next((item for item in characters if item.role == "antagonist"), None)

    await _safe_execute(session, delete(WorldBackboneModel).where(WorldBackboneModel.project_id == project.id))
    await _safe_execute(session, delete(VolumeFrontierModel).where(VolumeFrontierModel.project_id == project.id))
    await _safe_execute(session, delete(DeferredRevealModel).where(DeferredRevealModel.project_id == project.id))
    await _safe_execute(session, delete(ExpansionGateModel).where(ExpansionGateModel.project_id == project.id))

    counts = {
        "world_backbones_upserted": 0,
        "volume_frontiers_upserted": 0,
        "deferred_reveals_upserted": 0,
        "expansion_gates_upserted": 0,
    }

    backbone_payload = _derive_backbone_payload(
        project=project,
        protagonist=protagonist,
        antagonist=antagonist,
        world_rules=world_rules,
        volumes=volumes,
    )
    session.add(
        WorldBackboneModel(
            project_id=project.id,
            title=backbone_payload["title"],
            core_promise=backbone_payload["core_promise"],
            mainline_drive=backbone_payload["mainline_drive"],
            protagonist_destiny=backbone_payload["protagonist_destiny"],
            antagonist_axis=backbone_payload["antagonist_axis"],
            thematic_melody=backbone_payload["thematic_melody"],
            world_frame=backbone_payload["world_frame"],
            invariant_elements=backbone_payload["invariant_elements"],
            stable_unknowns=backbone_payload["stable_unknowns"],
            metadata_json=backbone_payload["metadata"],
        )
    )
    counts["world_backbones_upserted"] = 1

    chapter_ranges = estimate_volume_chapter_ranges(project, volumes, chapters)
    total_volumes = max(1, len(volumes))
    rule_codes = [item.rule_code for item in world_rules]
    location_names = [item.name for item in locations]
    faction_names = [item.name for item in factions]
    project_arc_codes = [
        item.arc_code
        for item in plot_arcs
        if item.scope_level == "project" or item.scope_volume_number is None
    ]

    deferred_reveal_specs: list[dict[str, Any]] = []
    seen_reveal_texts: set[str] = set()
    for volume in volumes:
        start_chapter_number, _ = chapter_ranges.get(volume.volume_number, (1, None))
        metadata = dict(volume.metadata_json or {})
        reveal_items = [
            ("key_reveal", value)
            for value in metadata.get("key_reveals", []) or []
        ] + [
            ("payoff", value)
            for value in metadata.get("foreshadowing_paid_off", []) or []
        ]
        for index, (category, raw_value) in enumerate(reveal_items, start=1):
            summary = _clean_text(raw_value)
            if not summary or summary in seen_reveal_texts:
                continue
            seen_reveal_texts.add(summary)
            deferred_reveal_specs.append(
                {
                    "volume": volume,
                    "reveal_code": f"volume-{volume.volume_number:02d}-reveal-{index:02d}",
                    "label": f"第{volume.volume_number}卷关键揭示 {index}",
                    "category": category,
                    "summary": summary,
                    "source_volume_number": max(1, volume.volume_number - 1) if volume.volume_number > 1 else 1,
                    "reveal_volume_number": volume.volume_number,
                    "reveal_chapter_number": start_chapter_number,
                    "guard_condition": f"只在进入第{volume.volume_number}卷后允许正面说破。",
                    "metadata": {"volume_title": volume.title},
                }
            )

    for spec in deferred_reveal_specs:
        session.add(
            DeferredRevealModel(
                project_id=project.id,
                volume_id=spec["volume"].id,
                reveal_code=spec["reveal_code"],
                label=spec["label"],
                category=spec["category"],
                summary=spec["summary"],
                source_volume_number=spec["source_volume_number"],
                reveal_volume_number=spec["reveal_volume_number"],
                reveal_chapter_number=spec["reveal_chapter_number"],
                guard_condition=spec["guard_condition"],
                status="scheduled",
                metadata_json=spec["metadata"],
            )
        )
    counts["deferred_reveals_upserted"] = len(deferred_reveal_specs)

    for index, volume in enumerate(volumes, start=1):
        start_chapter_number, end_chapter_number = chapter_ranges.get(volume.volume_number, (1, None))
        metadata = dict(volume.metadata_json or {})
        next_volume = volumes[index] if index < len(volumes) else None
        active_arc_codes = list(project_arc_codes)
        active_arc_codes.extend(
            item.arc_code
            for item in plot_arcs
            if item.scope_volume_number is not None and item.scope_volume_number <= volume.volume_number
        )
        active_arc_codes = list(dict.fromkeys(active_arc_codes))[:6]
        future_reveal_codes = [
            spec["reveal_code"]
            for spec in deferred_reveal_specs
            if spec["reveal_volume_number"] > volume.volume_number
        ][:6]

        frontier_summary = " / ".join(
            item
            for item in (
                _clean_text(volume.goal),
                _clean_text(volume.theme),
                _clean_text(metadata.get("opening_state", {}).get("world_situation"))
                if isinstance(metadata.get("opening_state"), dict)
                else None,
                _clean_text(metadata.get("reader_hook_to_next")),
            )
            if item
        ) or f"第{volume.volume_number}卷负责逐步展开《{project.title}》的当前世界边界。"

        session.add(
            VolumeFrontierModel(
                project_id=project.id,
                volume_id=volume.id,
                volume_number=volume.volume_number,
                title=volume.title,
                frontier_summary=frontier_summary,
                expansion_focus=_clean_text(volume.theme) or _clean_text(volume.goal),
                start_chapter_number=start_chapter_number,
                end_chapter_number=end_chapter_number,
                visible_rule_codes=_cumulative_slice(rule_codes, total=total_volumes, upto=volume.volume_number, minimum=min(2, len(rule_codes) or 0)),
                active_locations=_cumulative_slice(location_names, total=total_volumes, upto=volume.volume_number, minimum=min(1, len(location_names) or 0)),
                active_factions=_cumulative_slice(faction_names, total=total_volumes, upto=volume.volume_number, minimum=min(1, len(faction_names) or 0)),
                active_arc_codes=active_arc_codes,
                future_reveal_codes=future_reveal_codes,
                metadata_json={
                    "opening_state": metadata.get("opening_state", {}),
                    "key_reveals": metadata.get("key_reveals", []),
                    "reader_hook_to_next": metadata.get("reader_hook_to_next"),
                },
            )
        )
        counts["volume_frontiers_upserted"] += 1

        unlocks_summary = (
            f"展开第{next_volume.volume_number}卷《{next_volume.title}》的世界边界。"
            if next_volume is not None
            else "进入整书后续更高层世界。"
        )
        condition_summary = (
            "完成开篇建场并建立主线承诺。"
            if volume.volume_number == 1
            else f"完成第{volume.volume_number - 1}卷目标：{_clean_text(volumes[index - 2].goal) or volumes[index - 2].title}"
        )
        session.add(
            ExpansionGateModel(
                project_id=project.id,
                volume_id=volume.id,
                gate_code=f"unlock-volume-{volume.volume_number:02d}",
                label=f"第{volume.volume_number}卷世界扩张闸门",
                gate_type="world_expansion",
                condition_summary=condition_summary,
                unlocks_summary=unlocks_summary,
                source_volume_number=(volume.volume_number - 1) if volume.volume_number > 1 else None,
                unlock_volume_number=volume.volume_number,
                unlock_chapter_number=start_chapter_number,
                status="active" if volume.volume_number == 1 else "planned",
                metadata_json={"volume_title": volume.title},
            )
        )
        counts["expansion_gates_upserted"] += 1

    await sync_world_expansion_progress(session, project=project)
    await session.flush()
    return counts


async def sync_world_expansion_progress(
    session: AsyncSession,
    *,
    project: ProjectModel,
) -> dict[str, Any]:
    frontiers = list(
        await _scalars_list(
            session,
            select(VolumeFrontierModel)
            .where(VolumeFrontierModel.project_id == project.id)
            .order_by(VolumeFrontierModel.volume_number.asc()),
        )
    )
    gates = list(
        await _scalars_list(
            session,
            select(ExpansionGateModel)
            .where(ExpansionGateModel.project_id == project.id)
            .order_by(
                ExpansionGateModel.unlock_volume_number.asc(),
                ExpansionGateModel.unlock_chapter_number.asc(),
            ),
        )
    )
    if not frontiers and not gates:
        return {
            "current_volume_number": project.current_volume_number,
            "current_chapter_number": project.current_chapter_number,
            "unlocked_gate_count": 0,
            "active_gate_count": 0,
            "planned_gate_count": 0,
        }

    current_chapter_number = max(int(project.current_chapter_number or 0), 0)
    current_frontier = None
    if frontiers:
        if current_chapter_number <= 0:
            current_frontier = frontiers[0]
        else:
            for frontier in frontiers:
                end_chapter_number = frontier.end_chapter_number or frontier.start_chapter_number
                if frontier.start_chapter_number <= current_chapter_number <= end_chapter_number:
                    current_frontier = frontier
                    break
            if current_frontier is None:
                current_frontier = next(
                    (
                        frontier
                        for frontier in reversed(frontiers)
                        if frontier.start_chapter_number <= current_chapter_number
                    ),
                    frontiers[0],
                )
        project.current_volume_number = current_frontier.volume_number
    current_volume_number = int(project.current_volume_number or 0)

    unlocked_gate_count = 0
    active_gate_count = 0
    planned_gate_count = 0
    for gate in gates:
        if current_chapter_number >= gate.unlock_chapter_number:
            gate.status = "unlocked"
            unlocked_gate_count += 1
        elif (
            (current_chapter_number <= 0 and gate.unlock_volume_number == 1)
            or gate.unlock_volume_number == current_volume_number + 1
        ):
            gate.status = "active"
            active_gate_count += 1
        else:
            gate.status = "planned"
            planned_gate_count += 1

    for frontier in frontiers:
        end_chapter_number = frontier.end_chapter_number or frontier.start_chapter_number
        if current_chapter_number <= 0 and frontier.volume_number == 1:
            progress_state = "current"
        elif current_chapter_number > end_chapter_number:
            progress_state = "completed"
        elif frontier.start_chapter_number <= current_chapter_number <= end_chapter_number:
            progress_state = "current"
        else:
            progress_state = "future"
        frontier.metadata_json = {
            **dict(frontier.metadata_json or {}),
            "progress_state": progress_state,
        }

    await session.flush()
    return {
        "current_volume_number": project.current_volume_number,
        "current_chapter_number": current_chapter_number,
        "unlocked_gate_count": unlocked_gate_count,
        "active_gate_count": active_gate_count,
        "planned_gate_count": planned_gate_count,
    }


async def load_world_expansion_context(
    session: AsyncSession,
    *,
    project: ProjectModel,
    volume_number: int | None,
    chapter_number: int,
) -> dict[str, Any]:
    raw_backbone = await session.scalar(
        select(WorldBackboneModel).where(WorldBackboneModel.project_id == project.id)
    )
    backbone = raw_backbone if isinstance(raw_backbone, WorldBackboneModel) else None
    fallback_hidden_reveal_count = raw_backbone if isinstance(raw_backbone, int) else 0
    current_frontier = None
    if volume_number is not None:
        raw_frontier = await session.scalar(
            select(VolumeFrontierModel)
            .where(
                VolumeFrontierModel.project_id == project.id,
                VolumeFrontierModel.volume_number == volume_number,
            )
        )
        current_frontier = raw_frontier if isinstance(raw_frontier, VolumeFrontierModel) else None
    if current_frontier is None:
        raw_frontier = await session.scalar(
            select(VolumeFrontierModel)
            .where(
                VolumeFrontierModel.project_id == project.id,
                VolumeFrontierModel.start_chapter_number <= chapter_number,
                func.coalesce(VolumeFrontierModel.end_chapter_number, chapter_number) >= chapter_number,
            )
            .order_by(VolumeFrontierModel.volume_number.desc())
            .limit(1)
        )
        current_frontier = raw_frontier if isinstance(raw_frontier, VolumeFrontierModel) else None

    hidden_reveal_count = int(
        await session.scalar(
            select(func.count(DeferredRevealModel.id)).where(
                DeferredRevealModel.project_id == project.id,
                DeferredRevealModel.reveal_chapter_number > chapter_number,
            )
        )
        or fallback_hidden_reveal_count
    )
    raw_next_gate = await session.scalar(
        select(ExpansionGateModel)
        .where(
            ExpansionGateModel.project_id == project.id,
            ExpansionGateModel.unlock_chapter_number > chapter_number,
        )
        .order_by(
            ExpansionGateModel.unlock_chapter_number.asc(),
            ExpansionGateModel.unlock_volume_number.asc(),
        )
        .limit(1)
    )
    next_gate = raw_next_gate if isinstance(raw_next_gate, ExpansionGateModel) else None

    return {
        "world_backbone": (
            {
                "title": backbone.title,
                "core_promise": backbone.core_promise,
                "mainline_drive": backbone.mainline_drive,
                "protagonist_destiny": backbone.protagonist_destiny,
                "antagonist_axis": backbone.antagonist_axis,
                "thematic_melody": backbone.thematic_melody,
                "world_frame": backbone.world_frame,
                "invariant_elements": list(backbone.invariant_elements),
                "stable_unknowns": list(backbone.stable_unknowns),
            }
            if backbone is not None
            else {}
        ),
        "volume_frontier": (
            {
                "volume_number": current_frontier.volume_number,
                "title": current_frontier.title,
                "frontier_summary": current_frontier.frontier_summary,
                "expansion_focus": current_frontier.expansion_focus,
                "start_chapter_number": current_frontier.start_chapter_number,
                "end_chapter_number": current_frontier.end_chapter_number,
                "visible_rule_codes": list(current_frontier.visible_rule_codes),
                "active_locations": list(current_frontier.active_locations),
                "active_factions": list(current_frontier.active_factions),
                "active_arc_codes": list(current_frontier.active_arc_codes),
                "future_reveal_codes": list(current_frontier.future_reveal_codes),
            }
            if current_frontier is not None
            else {}
        ),
        "deferred_reveal_status": {
            "hidden_count": hidden_reveal_count,
        },
        "next_expansion_gate": (
            {
                "gate_code": next_gate.gate_code,
                "label": next_gate.label,
                "condition_summary": next_gate.condition_summary,
                "unlocks_summary": next_gate.unlocks_summary,
                "unlock_volume_number": next_gate.unlock_volume_number,
                "unlock_chapter_number": next_gate.unlock_chapter_number,
            }
            if next_gate is not None
            else {}
        ),
    }

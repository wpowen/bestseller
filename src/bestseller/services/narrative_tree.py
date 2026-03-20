from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.narrative_tree import (
    NarrativeTreeNodeRead,
    NarrativeTreeOverview,
    NarrativeTreeSearchHit,
    NarrativeTreeSearchResult,
)
from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ChapterContractModel,
    ChapterModel,
    CharacterModel,
    ClueModel,
    EmotionTrackModel,
    FactionModel,
    LocationModel,
    NarrativeTreeNodeModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    SceneCardModel,
    SceneContractModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.retrieval import tokenize_text


def tree_segment(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip().lower()).strip("-")
    return normalized or fallback


def chapter_path(chapter_number: int) -> str:
    return f"/chapters/{chapter_number:03d}"


def chapter_contract_path(chapter_number: int) -> str:
    return f"{chapter_path(chapter_number)}/contract"


def scene_path(chapter_number: int, scene_number: int) -> str:
    return f"/scenes/{chapter_number:03d}-{scene_number:02d}"


def scene_contract_path(chapter_number: int, scene_number: int) -> str:
    return f"{scene_path(chapter_number, scene_number)}/contract"


def volume_path(volume_number: int) -> str:
    return f"/volumes/{volume_number:02d}"


def character_path(character_name: str) -> str:
    return f"/characters/{tree_segment(character_name, 'character')}"


def arc_path(arc_code: str) -> str:
    return f"/arcs/{tree_segment(arc_code, 'arc')}"


def clue_path(clue_code: str) -> str:
    return f"/ledgers/clues/{tree_segment(clue_code, 'clue')}"


def payoff_path(payoff_code: str) -> str:
    return f"/ledgers/payoffs/{tree_segment(payoff_code, 'payoff')}"


def emotion_track_path(track_code: str) -> str:
    return f"/emotion-tracks/{tree_segment(track_code, 'emotion-track')}"


def antagonist_plan_path(plan_code: str) -> str:
    return f"/antagonists/{tree_segment(plan_code, 'antagonist-plan')}"


def _parent_path(path: str) -> str | None:
    if path == "/":
        return None
    stripped = path.rstrip("/")
    if not stripped or stripped == "":
        return None
    if stripped.count("/") <= 1:
        return None
    parent = stripped.rsplit("/", 1)[0]
    return parent or None


def _depth(path: str) -> int:
    return len([segment for segment in path.split("/") if segment])


def _safe_line(label: str, value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return f"- {label}：{text}"


def _node_body(title: str, *lines: str) -> str:
    body_lines = [f"# {title}"]
    body_lines.extend(line for line in lines if line)
    return "\n".join(body_lines)


def _node_read(node: NarrativeTreeNodeModel) -> NarrativeTreeNodeRead:
    return NarrativeTreeNodeRead(
        id=node.id,
        node_path=node.node_path,
        parent_path=node.parent_path,
        depth=node.depth,
        node_type=node.node_type,
        title=node.title,
        summary=node.summary,
        body_md=node.body_md,
        source_type=node.source_type,
        source_ref_id=node.source_ref_id,
        scope_level=node.scope_level,
        scope_volume_number=node.scope_volume_number,
        scope_chapter_number=node.scope_chapter_number,
        scope_scene_number=node.scope_scene_number,
        metadata=dict(node.metadata_json),
    )


def _node_type_weight(node_type: str) -> float:
    return {
        "scene_contract": 1.0,
        "chapter_contract": 0.95,
        "plot_arc": 0.9,
        "antagonist_plan": 0.88,
        "clue": 0.9,
        "payoff": 0.85,
        "emotion_track": 0.84,
        "scene": 0.8,
        "chapter": 0.78,
        "character": 0.75,
        "world_rule": 0.72,
        "volume": 0.7,
        "faction": 0.6,
        "location": 0.6,
    }.get(node_type, 0.45)


def _path_affinity(node_path: str, preferred_paths: list[str]) -> float:
    best = 0.0
    for preferred_path in preferred_paths:
        if node_path == preferred_path:
            best = max(best, 1.0)
        elif node_path.startswith(f"{preferred_path}/"):
            best = max(best, 0.8)
        elif preferred_path.startswith(f"{node_path}/"):
            best = max(best, 0.55)
    return best


def _is_visible_before_position(
    node: NarrativeTreeNodeModel,
    *,
    current_chapter_number: int | None,
    current_scene_number: int | None,
) -> bool:
    if current_chapter_number is None or node.scope_level == "project":
        return True
    node_chapter = node.scope_chapter_number
    if node_chapter is None:
        return True
    if node_chapter < current_chapter_number:
        return True
    if node_chapter > current_chapter_number:
        return False
    node_scene = node.scope_scene_number or 0
    current_scene = current_scene_number or 99
    return node_scene <= current_scene


async def rebuild_narrative_tree(
    session: AsyncSession,
    *,
    project: ProjectModel,
) -> dict[str, Any]:
    await session.execute(
        delete(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.project_id == project.id)
    )

    volumes = list(
        await session.scalars(
            select(VolumeModel)
            .where(VolumeModel.project_id == project.id)
            .order_by(VolumeModel.volume_number.asc())
        )
    )
    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.project_id == project.id)
            .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
        )
    )
    world_rules = list(
        await session.scalars(select(WorldRuleModel).where(WorldRuleModel.project_id == project.id))
    )
    locations = list(
        await session.scalars(select(LocationModel).where(LocationModel.project_id == project.id))
    )
    factions = list(
        await session.scalars(select(FactionModel).where(FactionModel.project_id == project.id))
    )
    characters = list(
        await session.scalars(select(CharacterModel).where(CharacterModel.project_id == project.id))
    )
    plot_arcs = list(
        await session.scalars(select(PlotArcModel).where(PlotArcModel.project_id == project.id))
    )
    emotion_tracks = list(
        await session.scalars(select(EmotionTrackModel).where(EmotionTrackModel.project_id == project.id))
    )
    antagonist_plans = list(
        await session.scalars(select(AntagonistPlanModel).where(AntagonistPlanModel.project_id == project.id))
    )
    clues = list(await session.scalars(select(ClueModel).where(ClueModel.project_id == project.id)))
    payoffs = list(await session.scalars(select(PayoffModel).where(PayoffModel.project_id == project.id)))
    chapter_contracts = list(
        await session.scalars(select(ChapterContractModel).where(ChapterContractModel.project_id == project.id))
    )
    scene_contracts = list(
        await session.scalars(select(SceneContractModel).where(SceneContractModel.project_id == project.id))
    )

    chapters_by_volume: dict[UUID, list[ChapterModel]] = defaultdict(list)
    for chapter in chapters:
        if chapter.volume_id is not None:
            chapters_by_volume[chapter.volume_id].append(chapter)
    chapter_by_id = {chapter.id: chapter for chapter in chapters}
    chapter_contract_by_number = {item.chapter_number: item for item in chapter_contracts}
    scene_contract_by_position = {(item.chapter_number, item.scene_number): item for item in scene_contracts}

    node_type_counts: Counter[str] = Counter()

    def add_node(
        *,
        node_path: str,
        node_type: str,
        title: str,
        body_md: str,
        summary: str | None = None,
        source_type: str = "container",
        source_ref_id: UUID | None = None,
        scope_level: str = "project",
        scope_volume_number: int | None = None,
        scope_chapter_number: int | None = None,
        scope_scene_number: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        lexical_document = " ".join(
            tokenize_text(
                " ".join(
                    part
                    for part in (
                        title,
                        summary or "",
                        body_md,
                        " ".join(str(value) for value in (metadata or {}).values()),
                    )
                    if part
                )
            )
        )
        node = NarrativeTreeNodeModel(
            project_id=project.id,
            node_path=node_path,
            parent_path=_parent_path(node_path),
            depth=_depth(node_path),
            node_type=node_type,
            title=title,
            summary=summary,
            body_md=body_md,
            source_type=source_type,
            source_ref_id=source_ref_id,
            scope_level=scope_level,
            scope_volume_number=scope_volume_number,
            scope_chapter_number=scope_chapter_number,
            scope_scene_number=scope_scene_number,
            lexical_document=lexical_document,
            metadata_json=metadata or {},
        )
        session.add(node)
        node_type_counts[node_type] += 1

    add_node(
        node_path="/book",
        node_type="book_root",
        title="全书总览",
        summary=project.metadata_json.get("logline") or project.title,
        body_md=_node_body(
            "全书总览",
            _safe_line("书名", project.title),
            _safe_line("题材", project.genre),
            _safe_line("主线", project.metadata_json.get("logline")),
            _safe_line("主题", "、".join(str(item) for item in project.metadata_json.get("themes", []))),
        ),
    )
    add_node(
        node_path="/book/premise",
        node_type="premise",
        title="作品 premise",
        summary=project.metadata_json.get("logline") or project.title,
        body_md=_node_body(
            "作品 premise",
            _safe_line("logline", project.metadata_json.get("logline") or project.title),
            _safe_line("themes", "、".join(str(item) for item in project.metadata_json.get("themes", []))),
            _safe_line("stakes", project.metadata_json.get("stakes")),
        ),
        source_type="project",
        source_ref_id=project.id,
        metadata={"kind": "premise"},
    )
    add_node(
        node_path="/book/book-spec",
        node_type="book_spec",
        title="Book Spec",
        summary=project.metadata_json.get("logline") or project.title,
        body_md=_node_body(
            "Book Spec",
            _safe_line("title", project.title),
            _safe_line("genre", project.genre),
            _safe_line("audience", project.audience),
            _safe_line("logline", project.metadata_json.get("logline")),
            _safe_line("themes", "、".join(str(item) for item in project.metadata_json.get("themes", []))),
            _safe_line("series_engine", project.metadata_json.get("series_engine")),
        ),
        source_type="planning_artifact",
        source_ref_id=project.id,
        metadata={"kind": "book_spec"},
    )
    add_node(
        node_path="/world",
        node_type="world_root",
        title="世界观总览",
        summary=project.metadata_json.get("world_premise") or project.metadata_json.get("world_name") or "当前项目世界设定",
        body_md=_node_body(
            "世界观总览",
            _safe_line("world_name", project.metadata_json.get("world_name")),
            _safe_line("world_premise", project.metadata_json.get("world_premise")),
            _safe_line("power_structure", project.metadata_json.get("power_structure")),
            _safe_line("power_system", project.metadata_json.get("power_system")),
        ),
    )
    add_node(node_path="/world/rules", node_type="world_rules_root", title="世界规则", body_md="# 世界规则")
    add_node(node_path="/world/locations", node_type="locations_root", title="关键地点", body_md="# 关键地点")
    add_node(node_path="/world/factions", node_type="factions_root", title="阵营", body_md="# 阵营")
    add_node(node_path="/characters", node_type="characters_root", title="角色", body_md="# 角色")
    add_node(node_path="/arcs", node_type="arcs_root", title="叙事线", body_md="# 叙事线")
    add_node(node_path="/emotion-tracks", node_type="emotion_tracks_root", title="情绪与关系线", body_md="# 情绪与关系线")
    add_node(node_path="/antagonists", node_type="antagonists_root", title="反派推进", body_md="# 反派推进")
    add_node(node_path="/ledgers", node_type="ledgers_root", title="线索与兑现账本", body_md="# 线索与兑现账本")
    add_node(node_path="/ledgers/clues", node_type="clues_root", title="伏笔账本", body_md="# 伏笔账本")
    add_node(node_path="/ledgers/payoffs", node_type="payoffs_root", title="兑现账本", body_md="# 兑现账本")
    add_node(node_path="/volumes", node_type="volumes_root", title="卷结构", body_md="# 卷结构")
    add_node(node_path="/chapters", node_type="chapters_root", title="章节结构", body_md="# 章节结构")
    add_node(node_path="/scenes", node_type="scenes_root", title="场景结构", body_md="# 场景结构")

    for world_rule in world_rules:
        add_node(
            node_path=f"/world/rules/{tree_segment(world_rule.rule_code, 'rule')}",
            node_type="world_rule",
            title=world_rule.name,
            summary=world_rule.description,
            body_md=_node_body(
                world_rule.name,
                _safe_line("rule_code", world_rule.rule_code),
                _safe_line("description", world_rule.description),
                _safe_line("story_consequence", world_rule.story_consequence),
                _safe_line("exploitation_potential", world_rule.exploitation_potential),
            ),
            source_type="world_rule",
            source_ref_id=world_rule.id,
            metadata={"rule_code": world_rule.rule_code},
        )

    for location in locations:
        add_node(
            node_path=f"/world/locations/{tree_segment(location.name, 'location')}",
            node_type="location",
            title=location.name,
            summary=location.story_role or location.atmosphere,
            body_md=_node_body(
                location.name,
                _safe_line("type", location.location_type),
                _safe_line("story_role", location.story_role),
                _safe_line("atmosphere", location.atmosphere),
                _safe_line("key_rules", "、".join(str(item) for item in location.key_rule_codes)),
            ),
            source_type="location",
            source_ref_id=location.id,
            metadata={"location_type": location.location_type},
        )

    for faction in factions:
        add_node(
            node_path=f"/world/factions/{tree_segment(faction.name, 'faction')}",
            node_type="faction",
            title=faction.name,
            summary=faction.goal or faction.method,
            body_md=_node_body(
                faction.name,
                _safe_line("goal", faction.goal),
                _safe_line("method", faction.method),
                _safe_line("relationship_to_protagonist", faction.relationship_to_protagonist),
                _safe_line("internal_conflict", faction.internal_conflict),
            ),
            source_type="faction",
            source_ref_id=faction.id,
        )

    for character in characters:
        add_node(
            node_path=character_path(character.name),
            node_type="character",
            title=character.name,
            summary=character.goal or character.arc_state or character.role,
            body_md=_node_body(
                character.name,
                _safe_line("role", character.role),
                _safe_line("goal", character.goal),
                _safe_line("fear", character.fear),
                _safe_line("flaw", character.flaw),
                _safe_line("secret", character.secret),
                _safe_line("arc_trajectory", character.arc_trajectory),
                _safe_line("arc_state", character.arc_state),
                _safe_line("power_tier", character.power_tier),
            ),
            source_type="character",
            source_ref_id=character.id,
            metadata={"role": character.role},
        )

    for arc in plot_arcs:
        add_node(
            node_path=arc_path(arc.arc_code),
            node_type="plot_arc",
            title=arc.name,
            summary=arc.promise,
            body_md=_node_body(
                arc.name,
                _safe_line("arc_code", arc.arc_code),
                _safe_line("arc_type", arc.arc_type),
                _safe_line("promise", arc.promise),
                _safe_line("core_question", arc.core_question),
                _safe_line("target_payoff", arc.target_payoff),
                _safe_line("description", arc.description),
            ),
            source_type="plot_arc",
            source_ref_id=arc.id,
            metadata={"arc_code": arc.arc_code, "arc_type": arc.arc_type},
        )

    for emotion_track in emotion_tracks:
        add_node(
            node_path=emotion_track_path(emotion_track.track_code),
            node_type="emotion_track",
            title=emotion_track.title,
            summary=emotion_track.summary,
            body_md=_node_body(
                emotion_track.title,
                _safe_line("track_code", emotion_track.track_code),
                _safe_line("track_type", emotion_track.track_type),
                _safe_line("characters", f"{emotion_track.character_a_label} / {emotion_track.character_b_label}"),
                _safe_line("relationship_type", emotion_track.relationship_type),
                _safe_line("summary", emotion_track.summary),
                _safe_line("desired_payoff", emotion_track.desired_payoff),
                _safe_line("trust_level", emotion_track.trust_level),
                _safe_line("attraction_level", emotion_track.attraction_level),
                _safe_line("distance_level", emotion_track.distance_level),
                _safe_line("conflict_level", emotion_track.conflict_level),
                _safe_line("intimacy_stage", emotion_track.intimacy_stage),
            ),
            source_type="emotion_track",
            source_ref_id=emotion_track.id,
            metadata={
                "track_code": emotion_track.track_code,
                "track_type": emotion_track.track_type,
                "character_a_label": emotion_track.character_a_label,
                "character_b_label": emotion_track.character_b_label,
                "relationship_type": emotion_track.relationship_type,
                "status": emotion_track.status,
            },
        )

    for antagonist_plan in antagonist_plans:
        add_node(
            node_path=antagonist_plan_path(antagonist_plan.plan_code),
            node_type="antagonist_plan",
            title=antagonist_plan.title,
            summary=antagonist_plan.goal,
            body_md=_node_body(
                antagonist_plan.title,
                _safe_line("plan_code", antagonist_plan.plan_code),
                _safe_line("antagonist", antagonist_plan.antagonist_label),
                _safe_line("threat_type", antagonist_plan.threat_type),
                _safe_line("goal", antagonist_plan.goal),
                _safe_line("current_move", antagonist_plan.current_move),
                _safe_line("next_countermove", antagonist_plan.next_countermove),
                _safe_line("escalation_condition", antagonist_plan.escalation_condition),
                _safe_line("reveal_timing", antagonist_plan.reveal_timing),
                _safe_line("pressure_level", antagonist_plan.pressure_level),
            ),
            source_type="antagonist_plan",
            source_ref_id=antagonist_plan.id,
            scope_level="volume" if antagonist_plan.scope_volume_number is not None else "project",
            scope_volume_number=antagonist_plan.scope_volume_number,
            scope_chapter_number=antagonist_plan.target_chapter_number,
            metadata={
                "plan_code": antagonist_plan.plan_code,
                "antagonist_label": antagonist_plan.antagonist_label,
                "threat_type": antagonist_plan.threat_type,
                "status": antagonist_plan.status,
            },
        )

    for clue in clues:
        add_node(
            node_path=clue_path(clue.clue_code),
            node_type="clue",
            title=clue.label,
            summary=clue.description,
            body_md=_node_body(
                clue.label,
                _safe_line("clue_code", clue.clue_code),
                _safe_line("clue_type", clue.clue_type),
                _safe_line("description", clue.description),
                _safe_line("planted_in", f"第{clue.planted_in_chapter_number or '?'}章第{clue.planted_in_scene_number or '?'}场"),
                _safe_line("expected_payoff", f"第{clue.expected_payoff_by_chapter_number or '?'}章第{clue.expected_payoff_by_scene_number or '?'}场"),
                _safe_line("reveal_guard", clue.reveal_guard),
            ),
            source_type="clue",
            source_ref_id=clue.id,
            scope_level="scene" if clue.planted_in_scene_number is not None else "chapter",
            scope_volume_number=clue.planted_in_volume_number,
            scope_chapter_number=clue.planted_in_chapter_number,
            scope_scene_number=clue.planted_in_scene_number,
            metadata={
                "clue_code": clue.clue_code,
                "status": clue.status,
                "arc_id": str(clue.plot_arc_id) if clue.plot_arc_id is not None else None,
            },
        )

    for payoff in payoffs:
        add_node(
            node_path=payoff_path(payoff.payoff_code),
            node_type="payoff",
            title=payoff.label,
            summary=payoff.description,
            body_md=_node_body(
                payoff.label,
                _safe_line("payoff_code", payoff.payoff_code),
                _safe_line("description", payoff.description),
                _safe_line("target_position", f"第{payoff.target_chapter_number or '?'}章第{payoff.target_scene_number or '?'}场"),
                _safe_line("actual_position", f"第{payoff.actual_chapter_number or '?'}章第{payoff.actual_scene_number or '?'}场"),
            ),
            source_type="payoff",
            source_ref_id=payoff.id,
            scope_level="scene" if payoff.target_scene_number is not None else "chapter",
            scope_volume_number=payoff.target_volume_number,
            scope_chapter_number=payoff.target_chapter_number,
            scope_scene_number=payoff.target_scene_number,
            metadata={
                "payoff_code": payoff.payoff_code,
                "status": payoff.status,
                "source_clue_id": str(payoff.source_clue_id) if payoff.source_clue_id is not None else None,
            },
        )

    for volume in volumes:
        volume_chapters = sorted(
            chapters_by_volume.get(volume.id, []),
            key=lambda item: item.chapter_number,
        )
        add_node(
            node_path=volume_path(volume.volume_number),
            node_type="volume",
            title=volume.title,
            summary=volume.goal or volume.theme,
            body_md=_node_body(
                volume.title,
                _safe_line("volume_number", volume.volume_number),
                _safe_line("theme", volume.theme),
                _safe_line("goal", volume.goal),
                _safe_line("obstacle", volume.obstacle),
                _safe_line("target_word_count", volume.target_word_count),
                _safe_line("target_chapter_count", volume.target_chapter_count),
                _safe_line("reader_hook_to_next", volume.metadata_json.get("reader_hook_to_next")),
            ),
            source_type="volume",
            source_ref_id=volume.id,
            scope_level="volume",
            scope_volume_number=volume.volume_number,
            scope_chapter_number=volume_chapters[0].chapter_number if volume_chapters else None,
            metadata={"volume_number": volume.volume_number},
        )

    for chapter in chapters:
        add_node(
            node_path=chapter_path(chapter.chapter_number),
            node_type="chapter",
            title=chapter.title or f"第{chapter.chapter_number}章",
            summary=chapter.chapter_goal,
            body_md=_node_body(
                chapter.title or f"第{chapter.chapter_number}章",
                _safe_line("chapter_number", chapter.chapter_number),
                _safe_line("chapter_goal", chapter.chapter_goal),
                _safe_line("opening_situation", chapter.opening_situation),
                _safe_line("main_conflict", chapter.main_conflict),
                _safe_line("hook", chapter.hook_description),
                _safe_line("emotion_arc", chapter.chapter_emotion_arc),
            ),
            source_type="chapter",
            source_ref_id=chapter.id,
            scope_level="chapter",
            scope_chapter_number=chapter.chapter_number,
            metadata={"chapter_number": chapter.chapter_number},
        )
        chapter_contract = chapter_contract_by_number.get(chapter.chapter_number)
        if chapter_contract is not None:
            add_node(
                node_path=chapter_contract_path(chapter.chapter_number),
                node_type="chapter_contract",
                title=f"第{chapter.chapter_number}章 contract",
                summary=chapter_contract.contract_summary,
                body_md=_node_body(
                    f"第{chapter.chapter_number}章 contract",
                    _safe_line("summary", chapter_contract.contract_summary),
                    _safe_line("core_conflict", chapter_contract.core_conflict),
                    _safe_line("emotional_shift", chapter_contract.emotional_shift),
                    _safe_line("information_release", chapter_contract.information_release),
                    _safe_line("closing_hook", chapter_contract.closing_hook),
                    _safe_line("primary_arcs", "、".join(str(item) for item in chapter_contract.primary_arc_codes)),
                    _safe_line("supporting_arcs", "、".join(str(item) for item in chapter_contract.supporting_arc_codes)),
                    _safe_line("planted_clues", "、".join(str(item) for item in chapter_contract.planted_clue_codes)),
                    _safe_line("due_payoffs", "、".join(str(item) for item in chapter_contract.due_payoff_codes)),
                ),
                source_type="chapter_contract",
                source_ref_id=chapter_contract.id,
                scope_level="chapter",
                scope_chapter_number=chapter.chapter_number,
                metadata={"chapter_number": chapter.chapter_number},
            )

    for scene in scenes:
        chapter = chapter_by_id.get(scene.chapter_id)
        if chapter is None:
            continue
        add_node(
            node_path=scene_path(chapter.chapter_number, scene.scene_number),
            node_type="scene",
            title=scene.title or f"第{chapter.chapter_number}章第{scene.scene_number}场",
            summary=str(scene.purpose.get("story") or scene.hook_requirement or scene.scene_type),
            body_md=_node_body(
                scene.title or f"第{chapter.chapter_number}章第{scene.scene_number}场",
                _safe_line("scene_type", scene.scene_type),
                _safe_line("participants", "、".join(str(item) for item in scene.participants)),
                _safe_line("story_purpose", scene.purpose.get("story")),
                _safe_line("emotion_purpose", scene.purpose.get("emotion")),
                _safe_line("entry_state", scene.entry_state),
                _safe_line("exit_state", scene.exit_state),
                _safe_line("hook_requirement", scene.hook_requirement),
            ),
            source_type="scene",
            source_ref_id=scene.id,
            scope_level="scene",
            scope_chapter_number=chapter.chapter_number,
            scope_scene_number=scene.scene_number,
            metadata={
                "chapter_number": chapter.chapter_number,
                "scene_number": scene.scene_number,
                "participants": list(scene.participants),
            },
        )
        scene_contract = scene_contract_by_position.get((chapter.chapter_number, scene.scene_number))
        if scene_contract is not None:
            add_node(
                node_path=scene_contract_path(chapter.chapter_number, scene.scene_number),
                node_type="scene_contract",
                title=f"第{chapter.chapter_number}章第{scene.scene_number}场 contract",
                summary=scene_contract.contract_summary,
                body_md=_node_body(
                    f"第{chapter.chapter_number}章第{scene.scene_number}场 contract",
                    _safe_line("summary", scene_contract.contract_summary),
                    _safe_line("core_conflict", scene_contract.core_conflict),
                    _safe_line("emotional_shift", scene_contract.emotional_shift),
                    _safe_line("information_release", scene_contract.information_release),
                    _safe_line("tail_hook", scene_contract.tail_hook),
                    _safe_line("arc_codes", "、".join(str(item) for item in scene_contract.arc_codes)),
                    _safe_line("planted_clues", "、".join(str(item) for item in scene_contract.planted_clue_codes)),
                    _safe_line("payoff_codes", "、".join(str(item) for item in scene_contract.payoff_codes)),
                ),
                source_type="scene_contract",
                source_ref_id=scene_contract.id,
                scope_level="scene",
                scope_chapter_number=chapter.chapter_number,
                scope_scene_number=scene.scene_number,
                metadata={
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                },
            )

    await session.flush()
    return {
        "node_count": sum(node_type_counts.values()),
        "node_type_counts": dict(node_type_counts),
    }


async def ensure_project_narrative_tree(
    session: AsyncSession,
    project: ProjectModel,
) -> int:
    existing = list(
        await session.scalars(
            select(NarrativeTreeNodeModel)
            .where(NarrativeTreeNodeModel.project_id == project.id)
            .limit(1)
        )
    )
    if existing:
        return 0
    counts = await rebuild_narrative_tree(session, project=project)
    return int(counts["node_count"])


async def build_narrative_tree_overview(
    session: AsyncSession,
    project_slug: str,
) -> NarrativeTreeOverview:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    await ensure_project_narrative_tree(session, project)
    nodes = list(
        await session.scalars(
            select(NarrativeTreeNodeModel)
            .where(NarrativeTreeNodeModel.project_id == project.id)
            .order_by(NarrativeTreeNodeModel.node_path.asc())
        )
    )
    return NarrativeTreeOverview(
        project_id=project.id,
        project_slug=project.slug,
        title=project.title,
        nodes=[_node_read(item) for item in nodes],
    )


async def get_narrative_tree_node_by_path(
    session: AsyncSession,
    project_slug: str,
    node_path: str,
) -> NarrativeTreeNodeRead | None:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    await ensure_project_narrative_tree(session, project)
    node = await session.scalar(
        select(NarrativeTreeNodeModel).where(
            NarrativeTreeNodeModel.project_id == project.id,
            NarrativeTreeNodeModel.node_path == node_path,
        )
    )
    return _node_read(node) if isinstance(node, NarrativeTreeNodeModel) else None


async def resolve_narrative_tree_paths_for_project(
    session: AsyncSession,
    project: ProjectModel,
    paths: list[str],
    *,
    current_chapter_number: int | None = None,
    current_scene_number: int | None = None,
) -> list[NarrativeTreeNodeRead]:
    if not paths:
        return []
    await ensure_project_narrative_tree(session, project)
    normalized_paths = list(dict.fromkeys(path for path in paths if path))
    nodes = list(
        await session.scalars(
            select(NarrativeTreeNodeModel).where(
                NarrativeTreeNodeModel.project_id == project.id,
                NarrativeTreeNodeModel.node_path.in_(normalized_paths),
            )
        )
    )
    node_map = {
        item.node_path: item
        for item in nodes
        if _is_visible_before_position(
            item,
            current_chapter_number=current_chapter_number,
            current_scene_number=current_scene_number,
        )
    }
    return [_node_read(node_map[path]) for path in normalized_paths if path in node_map]


async def search_narrative_tree_for_project(
    session: AsyncSession,
    project: ProjectModel,
    query_text: str,
    *,
    preferred_paths: list[str] | None = None,
    current_chapter_number: int | None = None,
    current_scene_number: int | None = None,
    top_k: int = 6,
) -> NarrativeTreeSearchResult:
    await ensure_project_narrative_tree(session, project)
    preferred = list(dict.fromkeys(preferred_paths or []))
    nodes = list(
        await session.scalars(
            select(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.project_id == project.id)
        )
    )
    query_tokens = set(tokenize_text(query_text))
    normalized_query = query_text.strip().lower()
    hits: list[NarrativeTreeSearchHit] = []
    for node in nodes:
        if not _is_visible_before_position(
            node,
            current_chapter_number=current_chapter_number,
            current_scene_number=current_scene_number,
        ):
            continue
        lexical_tokens = set((node.lexical_document or "").split())
        matched_count = len(query_tokens & lexical_tokens)
        lexical_overlap = matched_count / max(len(query_tokens), 1)
        affinity = _path_affinity(node.node_path, preferred)
        contains_bonus = 0.12 if normalized_query and normalized_query in node.body_md.lower() else 0.0
        score = (
            lexical_overlap * 0.55
            + affinity * 0.25
            + _node_type_weight(node.node_type) * 0.15
            + contains_bonus
        )
        if score <= 0.12:
            continue
        hits.append(
            NarrativeTreeSearchHit(
                node_path=node.node_path,
                node_type=node.node_type,
                title=node.title,
                summary=node.summary,
                score=round(min(score, 1.0), 4),
                source_type=node.source_type,
                scope_level=node.scope_level,
                metadata=dict(node.metadata_json),
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return NarrativeTreeSearchResult(
        project_id=project.id,
        query_text=query_text,
        preferred_paths=preferred,
        hits=hits[:top_k],
    )

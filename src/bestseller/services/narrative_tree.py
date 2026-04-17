from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text
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
    DeferredRevealModel,
    EmotionTrackModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    NarrativeTreeNodeModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    SceneCardModel,
    SceneContractModel,
    VolumeModel,
    VolumeFrontierModel,
    WorldBackboneModel,
    WorldRuleModel,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.retrieval import tokenize_text

# ---------------------------------------------------------------------------
# Bilingual label dictionaries
# ---------------------------------------------------------------------------

_NODE_TITLES = {
    "book_overview": ("Book Overview", "全书总览"),
    "premise": ("Work Premise", "作品 premise"),
    "world_overview": ("World Overview", "世界观总览"),
    "world_frontiers": ("Stage World Boundaries", "阶段世界边界"),
    "deferred_reveals": ("Deferred Reveals Ledger", "延后揭示账本"),
    "world_expansion_gates": ("World Expansion Gates", "世界扩张闸门"),
    "world_rules": ("World Rules", "世界规则"),
    "key_locations": ("Key Locations", "关键地点"),
    "factions": ("Factions", "阵营"),
    "characters": ("Characters", "角色"),
    "narrative_arcs": ("Narrative Arcs", "叙事线"),
    "emotional_tracks": ("Emotional & Relationship Tracks", "情绪与关系线"),
    "antagonist_progression": ("Antagonist Progression", "反派推进"),
    "clues_ledger": ("Clues & Payoffs Ledger", "线索与兑现账本"),
    "foreshadowing_ledger": ("Foreshadowing Ledger", "伏笔账本"),
    "payoff_ledger": ("Payoff Ledger", "兑现账本"),
    "volume_structure": ("Volume Structure", "卷结构"),
    "chapter_structure": ("Chapter Structure", "章节结构"),
    "scene_structure": ("Scene Structure", "场景结构"),
}

_FIELD_LABELS = {
    "书名": "Title", "题材": "Genre", "主线": "Main Plot", "主题": "Theme",
    "核心承诺": "Core Promise", "主线驱动": "Main Drive", "主角终局方向": "Protagonist Endgame",
    "反派主轴": "Antagonist Axis", "主题旋律": "Theme Melody",
    "世界骨架": "World Backbone", "不可轻改元素": "Immutable Elements",
    "长期未知项": "Long-term Unknowns",
    "阶段摘要": "Phase Summary", "扩张焦点": "Expansion Focus",
    "可见章节范围": "Visible Chapter Range",
    "允许启用规则": "Active Rules", "活跃地点": "Active Locations",
    "活跃势力": "Active Forces", "主导叙事线": "Primary Narrative Arc",
    "后续未展开揭示": "Pending Reveals",
    "类别": "Category", "正式揭示": "Official Reveal", "保护条件": "Protection Conditions",
    "允许出现卷": "Allowed Volumes", "允许出现章节": "Allowed Chapters",
    "触发条件": "Trigger Conditions", "解锁内容": "Unlocked Content",
    "解锁卷": "Unlock Volume", "解锁章节": "Unlock Chapter",
}


def _t(key: str, is_en: bool) -> str:
    """Return the English or Chinese variant of a node title or field label."""
    if key in _NODE_TITLES:
        return _NODE_TITLES[key][0] if is_en else _NODE_TITLES[key][1]
    if key in _FIELD_LABELS:
        return _FIELD_LABELS[key] if is_en else key
    return key


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


def world_backbone_path() -> str:
    return "/world/backbone"


def volume_frontier_path(volume_number: int) -> str:
    return f"/world/frontiers/volume-{volume_number:02d}"


def deferred_reveal_path(reveal_code: str) -> str:
    return f"/world/deferred-reveals/{tree_segment(reveal_code, 'deferred-reveal')}"


def expansion_gate_path(gate_code: str) -> str:
    return f"/world/expansion-gates/{tree_segment(gate_code, 'expansion-gate')}"


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


def _safe_line(label: str, value: Any, *, is_en: bool = False) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    sep = ": " if is_en else "："
    return f"- {label}{sep}{text}"


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
        "world_backbone": 0.83,
        "volume_frontier": 0.82,
        "deferred_reveal": 0.76,
        "expansion_gate": 0.74,
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
    is_en = not (project.language or "").startswith("zh")
    sl = _safe_line  # local alias for brevity
    join_sep = ", " if is_en else "、"

    # Serialise concurrent rebuilds for the same project. Without this,
    # two pipelines both DELETE then INSERT and collide on the
    # ``uq_narrative_tree_node_path`` unique constraint (observed
    # 2026-04-17). The advisory lock is released at transaction end.
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('narrative_tree_rebuild:' || :pid, 0))"
        ),
        {"pid": str(project.id)},
    )

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
    world_backbone = await session.scalar(
        select(WorldBackboneModel).where(WorldBackboneModel.project_id == project.id)
    )
    volume_frontiers = list(
        await session.scalars(
            select(VolumeFrontierModel)
            .where(VolumeFrontierModel.project_id == project.id)
            .order_by(VolumeFrontierModel.volume_number.asc())
        )
    )
    deferred_reveals = list(
        await session.scalars(
            select(DeferredRevealModel)
            .where(DeferredRevealModel.project_id == project.id)
            .order_by(
                DeferredRevealModel.reveal_volume_number.asc(),
                DeferredRevealModel.reveal_chapter_number.asc(),
                DeferredRevealModel.reveal_code.asc(),
            )
        )
    )
    expansion_gates = list(
        await session.scalars(
            select(ExpansionGateModel)
            .where(ExpansionGateModel.project_id == project.id)
            .order_by(
                ExpansionGateModel.unlock_volume_number.asc(),
                ExpansionGateModel.unlock_chapter_number.asc(),
            )
        )
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
        title=_t("book_overview", is_en),
        summary=project.metadata_json.get("logline") or project.title,
        body_md=_node_body(
            _t("book_overview", is_en),
            sl(_t("书名", is_en), project.title, is_en=is_en),
            sl(_t("题材", is_en), project.genre, is_en=is_en),
            sl(_t("主线", is_en), project.metadata_json.get("logline"), is_en=is_en),
            sl(_t("主题", is_en), join_sep.join(str(item) for item in project.metadata_json.get("themes", [])), is_en=is_en),
        ),
    )
    add_node(
        node_path="/book/premise",
        node_type="premise",
        title=_t("premise", is_en),
        summary=project.metadata_json.get("logline") or project.title,
        body_md=_node_body(
            _t("premise", is_en),
            sl("logline", project.metadata_json.get("logline") or project.title, is_en=is_en),
            sl("themes", join_sep.join(str(item) for item in project.metadata_json.get("themes", [])), is_en=is_en),
            sl("stakes", project.metadata_json.get("stakes"), is_en=is_en),
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
            sl("title", project.title, is_en=is_en),
            sl("genre", project.genre, is_en=is_en),
            sl("audience", project.audience, is_en=is_en),
            sl("logline", project.metadata_json.get("logline"), is_en=is_en),
            sl("themes", join_sep.join(str(item) for item in project.metadata_json.get("themes", [])), is_en=is_en),
            sl("series_engine", project.metadata_json.get("series_engine"), is_en=is_en),
        ),
        source_type="planning_artifact",
        source_ref_id=project.id,
        metadata={"kind": "book_spec"},
    )
    _world_fallback = "Current project world settings" if is_en else "当前项目世界设定"
    add_node(
        node_path="/world",
        node_type="world_root",
        title=_t("world_overview", is_en),
        summary=project.metadata_json.get("world_premise") or project.metadata_json.get("world_name") or _world_fallback,
        body_md=_node_body(
            _t("world_overview", is_en),
            sl("world_name", project.metadata_json.get("world_name"), is_en=is_en),
            sl("world_premise", project.metadata_json.get("world_premise"), is_en=is_en),
            sl("power_structure", project.metadata_json.get("power_structure"), is_en=is_en),
            sl("power_system", project.metadata_json.get("power_system"), is_en=is_en),
        ),
    )
    _frontiers_title = _t("world_frontiers", is_en)
    _frontiers_summary = "Visible world scope progressing by volume." if is_en else "按卷推进的可见世界范围。"
    add_node(
        node_path="/world/frontiers",
        node_type="world_frontiers_root",
        title=_frontiers_title,
        summary=_frontiers_summary,
        body_md=f"# {_frontiers_title}",
    )
    _deferred_title = _t("deferred_reveals", is_en)
    _deferred_summary = "Truths that may only be revealed in the future." if is_en else "未来才允许正面揭示的真相。"
    add_node(
        node_path="/world/deferred-reveals",
        node_type="deferred_reveals_root",
        title=_deferred_title,
        summary=_deferred_summary,
        body_md=f"# {_deferred_title}",
    )
    _gates_title = _t("world_expansion_gates", is_en)
    _gates_summary = "When the next layer of the world unlocks." if is_en else "下一层世界何时解锁。"
    add_node(
        node_path="/world/expansion-gates",
        node_type="expansion_gates_root",
        title=_gates_title,
        summary=_gates_summary,
        body_md=f"# {_gates_title}",
    )
    _wr = _t("world_rules", is_en)
    _kl = _t("key_locations", is_en)
    _fa = _t("factions", is_en)
    _ch = _t("characters", is_en)
    _na = _t("narrative_arcs", is_en)
    _et = _t("emotional_tracks", is_en)
    _ap = _t("antagonist_progression", is_en)
    _cl = _t("clues_ledger", is_en)
    _fl = _t("foreshadowing_ledger", is_en)
    _pl = _t("payoff_ledger", is_en)
    _vs = _t("volume_structure", is_en)
    _cs = _t("chapter_structure", is_en)
    _ss = _t("scene_structure", is_en)
    add_node(node_path="/world/rules", node_type="world_rules_root", title=_wr, body_md=f"# {_wr}")
    add_node(node_path="/world/locations", node_type="locations_root", title=_kl, body_md=f"# {_kl}")
    add_node(node_path="/world/factions", node_type="factions_root", title=_fa, body_md=f"# {_fa}")
    add_node(node_path="/characters", node_type="characters_root", title=_ch, body_md=f"# {_ch}")
    add_node(node_path="/arcs", node_type="arcs_root", title=_na, body_md=f"# {_na}")
    add_node(node_path="/emotion-tracks", node_type="emotion_tracks_root", title=_et, body_md=f"# {_et}")
    add_node(node_path="/antagonists", node_type="antagonists_root", title=_ap, body_md=f"# {_ap}")
    add_node(node_path="/ledgers", node_type="ledgers_root", title=_cl, body_md=f"# {_cl}")
    add_node(node_path="/ledgers/clues", node_type="clues_root", title=_fl, body_md=f"# {_fl}")
    add_node(node_path="/ledgers/payoffs", node_type="payoffs_root", title=_pl, body_md=f"# {_pl}")
    add_node(node_path="/volumes", node_type="volumes_root", title=_vs, body_md=f"# {_vs}")
    add_node(node_path="/chapters", node_type="chapters_root", title=_cs, body_md=f"# {_cs}")
    add_node(node_path="/scenes", node_type="scenes_root", title=_ss, body_md=f"# {_ss}")

    if world_backbone is not None:
        add_node(
            node_path=world_backbone_path(),
            node_type="world_backbone",
            title=world_backbone.title,
            summary=world_backbone.core_promise,
            body_md=_node_body(
                world_backbone.title,
                sl(_t("核心承诺", is_en), world_backbone.core_promise, is_en=is_en),
                sl(_t("主线驱动", is_en), world_backbone.mainline_drive, is_en=is_en),
                sl(_t("主角终局方向", is_en), world_backbone.protagonist_destiny, is_en=is_en),
                sl(_t("反派主轴", is_en), world_backbone.antagonist_axis, is_en=is_en),
                sl(_t("主题旋律", is_en), world_backbone.thematic_melody, is_en=is_en),
                sl(_t("世界骨架", is_en), world_backbone.world_frame, is_en=is_en),
                sl(_t("不可轻改元素", is_en), " / ".join(str(item) for item in world_backbone.invariant_elements), is_en=is_en),
                sl(_t("长期未知项", is_en), " / ".join(str(item) for item in world_backbone.stable_unknowns), is_en=is_en),
            ),
            source_type="world_backbone",
            source_ref_id=world_backbone.id,
            metadata={
                "invariant_elements": list(world_backbone.invariant_elements or []),
                "stable_unknowns": list(world_backbone.stable_unknowns or []),
            },
        )
    for frontier in volume_frontiers:
        vol = frontier.volume_number
        _frontier_heading = f"Volume {vol} Boundary" if is_en else f"第{vol}卷边界"
        _frontier_title = f"{_frontier_heading}: {frontier.title}" if is_en else f"{_frontier_heading}：{frontier.title}"
        add_node(
            node_path=volume_frontier_path(vol),
            node_type="volume_frontier",
            title=_frontier_title,
            summary=frontier.frontier_summary,
            body_md=_node_body(
                _frontier_heading,
                sl(_t("阶段摘要", is_en), frontier.frontier_summary, is_en=is_en),
                sl(_t("扩张焦点", is_en), frontier.expansion_focus, is_en=is_en),
                sl(
                    _t("可见章节范围", is_en),
                    f"{frontier.start_chapter_number} - {frontier.end_chapter_number or frontier.start_chapter_number}",
                    is_en=is_en,
                ),
                sl(_t("允许启用规则", is_en), " / ".join(str(item) for item in frontier.visible_rule_codes), is_en=is_en),
                sl(_t("活跃地点", is_en), " / ".join(str(item) for item in frontier.active_locations), is_en=is_en),
                sl(_t("活跃势力", is_en), " / ".join(str(item) for item in frontier.active_factions), is_en=is_en),
                sl(_t("主导叙事线", is_en), " / ".join(str(item) for item in frontier.active_arc_codes), is_en=is_en),
                sl(_t("后续未展开揭示", is_en), " / ".join(str(item) for item in frontier.future_reveal_codes), is_en=is_en),
            ),
            source_type="volume_frontier",
            source_ref_id=frontier.id,
            scope_level="volume",
            scope_volume_number=vol,
            scope_chapter_number=frontier.start_chapter_number,
            metadata={
                "end_chapter_number": frontier.end_chapter_number,
                "active_locations": list(frontier.active_locations or []),
                "active_factions": list(frontier.active_factions or []),
            },
        )
    for reveal in deferred_reveals:
        add_node(
            node_path=deferred_reveal_path(reveal.reveal_code),
            node_type="deferred_reveal",
            title=reveal.label,
            summary=reveal.summary,
            body_md=_node_body(
                reveal.label,
                sl(_t("类别", is_en), reveal.category, is_en=is_en),
                sl(_t("正式揭示", is_en), reveal.summary, is_en=is_en),
                sl(_t("保护条件", is_en), reveal.guard_condition, is_en=is_en),
                sl(_t("允许出现卷", is_en), reveal.reveal_volume_number, is_en=is_en),
                sl(_t("允许出现章节", is_en), reveal.reveal_chapter_number, is_en=is_en),
            ),
            source_type="deferred_reveal",
            source_ref_id=reveal.id,
            scope_level="chapter",
            scope_volume_number=reveal.reveal_volume_number,
            scope_chapter_number=reveal.reveal_chapter_number,
            metadata={"category": reveal.category},
        )
    for gate in expansion_gates:
        add_node(
            node_path=expansion_gate_path(gate.gate_code),
            node_type="expansion_gate",
            title=gate.label,
            summary=gate.unlocks_summary,
            body_md=_node_body(
                gate.label,
                sl(_t("触发条件", is_en), gate.condition_summary, is_en=is_en),
                sl(_t("解锁内容", is_en), gate.unlocks_summary, is_en=is_en),
                sl(_t("解锁卷", is_en), gate.unlock_volume_number, is_en=is_en),
                sl(_t("解锁章节", is_en), gate.unlock_chapter_number, is_en=is_en),
            ),
            source_type="expansion_gate",
            source_ref_id=gate.id,
            scope_level="chapter",
            scope_volume_number=gate.unlock_volume_number,
            scope_chapter_number=gate.unlock_chapter_number,
            metadata={"gate_type": gate.gate_type},
        )

    for world_rule in world_rules:
        add_node(
            node_path=f"/world/rules/{tree_segment(world_rule.rule_code, 'rule')}",
            node_type="world_rule",
            title=world_rule.name,
            summary=world_rule.description,
            body_md=_node_body(
                world_rule.name,
                sl("rule_code", world_rule.rule_code, is_en=is_en),
                sl("description", world_rule.description, is_en=is_en),
                sl("story_consequence", world_rule.story_consequence, is_en=is_en),
                sl("exploitation_potential", world_rule.exploitation_potential, is_en=is_en),
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
                sl("type", location.location_type, is_en=is_en),
                sl("story_role", location.story_role, is_en=is_en),
                sl("atmosphere", location.atmosphere, is_en=is_en),
                sl("key_rules", join_sep.join(str(item) for item in location.key_rule_codes), is_en=is_en),
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
                sl("goal", faction.goal, is_en=is_en),
                sl("method", faction.method, is_en=is_en),
                sl("relationship_to_protagonist", faction.relationship_to_protagonist, is_en=is_en),
                sl("internal_conflict", faction.internal_conflict, is_en=is_en),
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
                sl("role", character.role, is_en=is_en),
                sl("goal", character.goal, is_en=is_en),
                sl("fear", character.fear, is_en=is_en),
                sl("flaw", character.flaw, is_en=is_en),
                sl("secret", character.secret, is_en=is_en),
                sl("arc_trajectory", character.arc_trajectory, is_en=is_en),
                sl("arc_state", character.arc_state, is_en=is_en),
                sl("power_tier", character.power_tier, is_en=is_en),
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
                sl("arc_code", arc.arc_code, is_en=is_en),
                sl("arc_type", arc.arc_type, is_en=is_en),
                sl("promise", arc.promise, is_en=is_en),
                sl("core_question", arc.core_question, is_en=is_en),
                sl("target_payoff", arc.target_payoff, is_en=is_en),
                sl("description", arc.description, is_en=is_en),
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
                sl("track_code", emotion_track.track_code, is_en=is_en),
                sl("track_type", emotion_track.track_type, is_en=is_en),
                sl("characters", f"{emotion_track.character_a_label} / {emotion_track.character_b_label}", is_en=is_en),
                sl("relationship_type", emotion_track.relationship_type, is_en=is_en),
                sl("summary", emotion_track.summary, is_en=is_en),
                sl("desired_payoff", emotion_track.desired_payoff, is_en=is_en),
                sl("trust_level", emotion_track.trust_level, is_en=is_en),
                sl("attraction_level", emotion_track.attraction_level, is_en=is_en),
                sl("distance_level", emotion_track.distance_level, is_en=is_en),
                sl("conflict_level", emotion_track.conflict_level, is_en=is_en),
                sl("intimacy_stage", emotion_track.intimacy_stage, is_en=is_en),
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
                sl("plan_code", antagonist_plan.plan_code, is_en=is_en),
                sl("antagonist", antagonist_plan.antagonist_label, is_en=is_en),
                sl("threat_type", antagonist_plan.threat_type, is_en=is_en),
                sl("goal", antagonist_plan.goal, is_en=is_en),
                sl("current_move", antagonist_plan.current_move, is_en=is_en),
                sl("next_countermove", antagonist_plan.next_countermove, is_en=is_en),
                sl("escalation_condition", antagonist_plan.escalation_condition, is_en=is_en),
                sl("reveal_timing", antagonist_plan.reveal_timing, is_en=is_en),
                sl("pressure_level", antagonist_plan.pressure_level, is_en=is_en),
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
        _planted = (
            f"Ch {clue.planted_in_chapter_number or '?'} Scene {clue.planted_in_scene_number or '?'}"
            if is_en
            else f"第{clue.planted_in_chapter_number or '?'}章第{clue.planted_in_scene_number or '?'}场"
        )
        _expected = (
            f"Ch {clue.expected_payoff_by_chapter_number or '?'} Scene {clue.expected_payoff_by_scene_number or '?'}"
            if is_en
            else f"第{clue.expected_payoff_by_chapter_number or '?'}章第{clue.expected_payoff_by_scene_number or '?'}场"
        )
        add_node(
            node_path=clue_path(clue.clue_code),
            node_type="clue",
            title=clue.label,
            summary=clue.description,
            body_md=_node_body(
                clue.label,
                sl("clue_code", clue.clue_code, is_en=is_en),
                sl("clue_type", clue.clue_type, is_en=is_en),
                sl("description", clue.description, is_en=is_en),
                sl("planted_in", _planted, is_en=is_en),
                sl("expected_payoff", _expected, is_en=is_en),
                sl("reveal_guard", clue.reveal_guard, is_en=is_en),
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
        _target_pos = (
            f"Ch {payoff.target_chapter_number or '?'} Scene {payoff.target_scene_number or '?'}"
            if is_en
            else f"第{payoff.target_chapter_number or '?'}章第{payoff.target_scene_number or '?'}场"
        )
        _actual_pos = (
            f"Ch {payoff.actual_chapter_number or '?'} Scene {payoff.actual_scene_number or '?'}"
            if is_en
            else f"第{payoff.actual_chapter_number or '?'}章第{payoff.actual_scene_number or '?'}场"
        )
        add_node(
            node_path=payoff_path(payoff.payoff_code),
            node_type="payoff",
            title=payoff.label,
            summary=payoff.description,
            body_md=_node_body(
                payoff.label,
                sl("payoff_code", payoff.payoff_code, is_en=is_en),
                sl("description", payoff.description, is_en=is_en),
                sl("target_position", _target_pos, is_en=is_en),
                sl("actual_position", _actual_pos, is_en=is_en),
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
                sl("volume_number", volume.volume_number, is_en=is_en),
                sl("theme", volume.theme, is_en=is_en),
                sl("goal", volume.goal, is_en=is_en),
                sl("obstacle", volume.obstacle, is_en=is_en),
                sl("target_word_count", volume.target_word_count, is_en=is_en),
                sl("target_chapter_count", volume.target_chapter_count, is_en=is_en),
                sl("reader_hook_to_next", volume.metadata_json.get("reader_hook_to_next"), is_en=is_en),
            ),
            source_type="volume",
            source_ref_id=volume.id,
            scope_level="volume",
            scope_volume_number=volume.volume_number,
            scope_chapter_number=volume_chapters[0].chapter_number if volume_chapters else None,
            metadata={"volume_number": volume.volume_number},
        )

    for chapter in chapters:
        _ch_fallback = f"Ch {chapter.chapter_number}" if is_en else f"第{chapter.chapter_number}章"
        _ch_title = chapter.title or _ch_fallback
        add_node(
            node_path=chapter_path(chapter.chapter_number),
            node_type="chapter",
            title=_ch_title,
            summary=chapter.chapter_goal,
            body_md=_node_body(
                _ch_title,
                sl("chapter_number", chapter.chapter_number, is_en=is_en),
                sl("chapter_goal", chapter.chapter_goal, is_en=is_en),
                sl("opening_situation", chapter.opening_situation, is_en=is_en),
                sl("main_conflict", chapter.main_conflict, is_en=is_en),
                sl("hook", chapter.hook_description, is_en=is_en),
                sl("emotion_arc", chapter.chapter_emotion_arc, is_en=is_en),
            ),
            source_type="chapter",
            source_ref_id=chapter.id,
            scope_level="chapter",
            scope_chapter_number=chapter.chapter_number,
            metadata={"chapter_number": chapter.chapter_number},
        )
        chapter_contract = chapter_contract_by_number.get(chapter.chapter_number)
        if chapter_contract is not None:
            _cc_title = f"Ch {chapter.chapter_number} Contract" if is_en else f"第{chapter.chapter_number}章 contract"
            add_node(
                node_path=chapter_contract_path(chapter.chapter_number),
                node_type="chapter_contract",
                title=_cc_title,
                summary=chapter_contract.contract_summary,
                body_md=_node_body(
                    _cc_title,
                    sl("summary", chapter_contract.contract_summary, is_en=is_en),
                    sl("core_conflict", chapter_contract.core_conflict, is_en=is_en),
                    sl("emotional_shift", chapter_contract.emotional_shift, is_en=is_en),
                    sl("information_release", chapter_contract.information_release, is_en=is_en),
                    sl("closing_hook", chapter_contract.closing_hook, is_en=is_en),
                    sl("primary_arcs", join_sep.join(str(item) for item in chapter_contract.primary_arc_codes), is_en=is_en),
                    sl("supporting_arcs", join_sep.join(str(item) for item in chapter_contract.supporting_arc_codes), is_en=is_en),
                    sl("planted_clues", join_sep.join(str(item) for item in chapter_contract.planted_clue_codes), is_en=is_en),
                    sl("due_payoffs", join_sep.join(str(item) for item in chapter_contract.due_payoff_codes), is_en=is_en),
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
        _scene_fallback = (
            f"Ch {chapter.chapter_number} Scene {scene.scene_number}"
            if is_en
            else f"第{chapter.chapter_number}章第{scene.scene_number}场"
        )
        _scene_title = scene.title or _scene_fallback
        add_node(
            node_path=scene_path(chapter.chapter_number, scene.scene_number),
            node_type="scene",
            title=_scene_title,
            summary=str(scene.purpose.get("story") or scene.hook_requirement or scene.scene_type),
            body_md=_node_body(
                _scene_title,
                sl("scene_type", scene.scene_type, is_en=is_en),
                sl("participants", join_sep.join(str(item) for item in scene.participants), is_en=is_en),
                sl("story_purpose", scene.purpose.get("story"), is_en=is_en),
                sl("emotion_purpose", scene.purpose.get("emotion"), is_en=is_en),
                sl("entry_state", scene.entry_state, is_en=is_en),
                sl("exit_state", scene.exit_state, is_en=is_en),
                sl("hook_requirement", scene.hook_requirement, is_en=is_en),
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
            _sc_title = (
                f"Ch {chapter.chapter_number} Scene {scene.scene_number} Contract"
                if is_en
                else f"第{chapter.chapter_number}章第{scene.scene_number}场 contract"
            )
            add_node(
                node_path=scene_contract_path(chapter.chapter_number, scene.scene_number),
                node_type="scene_contract",
                title=_sc_title,
                summary=scene_contract.contract_summary,
                body_md=_node_body(
                    _sc_title,
                    sl("summary", scene_contract.contract_summary, is_en=is_en),
                    sl("core_conflict", scene_contract.core_conflict, is_en=is_en),
                    sl("emotional_shift", scene_contract.emotional_shift, is_en=is_en),
                    sl("information_release", scene_contract.information_release, is_en=is_en),
                    sl("tail_hook", scene_contract.tail_hook, is_en=is_en),
                    sl("arc_codes", join_sep.join(str(item) for item in scene_contract.arc_codes), is_en=is_en),
                    sl("planted_clues", join_sep.join(str(item) for item in scene_contract.planted_clue_codes), is_en=is_en),
                    sl("payoff_codes", join_sep.join(str(item) for item in scene_contract.payoff_codes), is_en=is_en),
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

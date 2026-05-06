from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.narrative import (
    AntagonistPlanRead,
    ArcBeatRead,
    ChapterContractRead,
    ClueRead,
    EmotionTrackRead,
    EndingContractRead,
    MotifPlacementRead,
    NarrativeOverview,
    PacingCurvePointRead,
    PayoffRead,
    PlotArcRead,
    ReaderKnowledgeEntryRead,
    RelationshipEventRead,
    SceneContractRead,
    SubplotScheduleEntryRead,
    ThemeArcRead,
)
from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ArcBeatModel,
    ChapterContractModel,
    ChapterModel,
    CharacterModel,
    ClueModel,
    EmotionTrackModel,
    EndingContractModel,
    MotifPlacementModel,
    PacingCurvePointModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    ReaderKnowledgeEntryModel,
    RelationshipEventModel,
    RelationshipModel,
    SceneCardModel,
    SceneContractModel,
    SubplotScheduleModel,
    ThemeArcModel,
    VolumeModel,
)
from bestseller.domain.structure_templates import StructureTemplate, resolve_structure_template
from bestseller.services.projects import get_project_by_slug
from bestseller.services.story_bible import parse_volume_plan_input
from bestseller.services.writing_profile import is_english_language


def _normalized_token(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).lower()


def _ensure_text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_emotion_track_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used: set[str] = set()
    deduped: list[dict[str, Any]] = []
    max_length = 64

    for spec in specs:
        raw_code = spec.get("track_code")
        base = str(raw_code).strip()[:max_length] if raw_code is not None else ""
        if not base:
            base = "track"
        track_code = base
        if track_code in used:
            suffix_index = 2
            while True:
                suffix = f"-{suffix_index}"
                candidate = f"{base[: max_length - len(suffix)]}{suffix}"
                if candidate not in used:
                    track_code = candidate
                    break
                suffix_index += 1
        used.add(track_code)
        deduped.append({**spec, "track_code": track_code})

    return deduped


def _is_volume_active(volume_number: int, active_volumes: list[Any]) -> bool:
    """Check if a volume number is active, handling both int and dict range formats.

    LLM sometimes outputs active_volumes items as {'start_volume': N, 'end_volume': M}
    instead of plain integers.
    """
    for av in active_volumes:
        if isinstance(av, dict):
            start = av.get("start_volume")
            end = av.get("end_volume")
            if isinstance(start, int) and isinstance(end, int):
                if start <= volume_number <= end:
                    return True
        elif isinstance(av, int):
            if av == volume_number:
                return True
    return False


def _build_arc_specs(
    project: ProjectModel,
    *,
    protagonist: CharacterModel | None,
    antagonist: CharacterModel | None,
    all_antagonists: list[CharacterModel] | None = None,
    volumes: list[VolumeModel],
    volume_entries: dict[int, Any],
) -> list[dict[str, Any]]:
    _is_en = is_english_language(project.language)
    logline = _ensure_text(
        project.metadata_json.get("logline"),
        f"The main plot of '{project.title}' continues to advance." if _is_en
        else f"《{project.title}》的主线持续推进。",
    )
    first_volume = volumes[0] if volumes else None
    last_volume = volumes[-1] if volumes else None
    first_volume_entry = volume_entries.get(first_volume.volume_number) if first_volume is not None else None
    last_volume_entry = volume_entries.get(last_volume.volume_number) if last_volume is not None else None
    _join = ", " if _is_en else "、"
    theme_text = _join.join(str(item) for item in project.metadata_json.get("themes", []) if str(item).strip())

    arc_specs: list[dict[str, Any]] = [
        {
            "arc_code": "main_plot",
            "name": "Main Plot" if _is_en else "主线推进",
            "arc_type": "main_plot",
            "promise": logline,
            "core_question": _ensure_text(
                protagonist.goal if protagonist is not None else None,
                "Can the protagonist achieve their goal against escalating opposition?" if _is_en
                else "主角能否在升级的对抗中完成主线目标？",
            ),
            "target_payoff": _ensure_text(
                getattr(last_volume_entry, "volume_climax", None) if last_volume_entry is not None else None,
                _ensure_text(
                    last_volume.goal if last_volume is not None else None,
                    "The main plot reaches its payoff in the final climax." if _is_en
                    else "主线在最终高潮获得兑现。",
                ),
            ),
            "description": theme_text or None,
            "metadata_json": {"plotline_category": "mainline", "plotline_visibility": "visible"},
        }
    ]

    if protagonist is not None and (protagonist.arc_trajectory or protagonist.arc_state):
        arc_specs.append(
            {
                "arc_code": "growth_arc",
                "name": (f"{protagonist.name} Growth Arc" if _is_en
                         else f"{protagonist.name}成长线"),
                "arc_type": "growth",
                "promise": _ensure_text(
                    protagonist.arc_trajectory,
                    f"{protagonist.name} must undergo a critical inner transformation." if _is_en
                    else f"{protagonist.name}需要完成关键内在转变。",
                ),
                "core_question": (
                    f"Can {protagonist.name} break through their current arc bottleneck?" if _is_en
                    else f"{protagonist.name}能否走出当前的弧线卡点？"
                ),
                "target_payoff": _ensure_text(
                    protagonist.goal,
                    f"{protagonist.name} completes their growth and takes on greater narrative responsibility." if _is_en
                    else f"{protagonist.name}完成成长并承担更大的叙事责任。",
                ),
                "description": protagonist.arc_state,
                "metadata_json": {"plotline_category": "subplot", "plotline_visibility": "visible"},
            }
        )

    key_reveals: list[str] = []
    for entry in volume_entries.values():
        key_reveals.extend(str(item).strip() for item in entry.key_reveals if str(item).strip())
    hidden_signal = any(
        token in logline
        for token in ("truth", "secret", "conspiracy", "cover-up", "behind the scenes",
                       "真相", "秘密", "阴谋", "篡改", "幕后")
    )
    if hidden_signal or key_reveals:
        arc_specs.append(
            {
                "arc_code": "mystery_arc",
                "name": "Hidden Truth" if _is_en else "暗线真相",
                "arc_type": "mystery",
                "promise": _ensure_text(
                    key_reveals[0] if key_reveals else None,
                    "The concealed truth will gradually surface." if _is_en
                    else "被遮蔽的真相会逐步显形。",
                ),
                "core_question": (
                    "What is the real truth, and who is pulling the strings?" if _is_en
                    else "幕后真相到底是什么，谁在操盘？"
                ),
                "target_payoff": _ensure_text(
                    key_reveals[-1] if key_reveals else None,
                    "The hidden arc reaches its payoff at a critical volume." if _is_en
                    else "暗线在关键卷完成兑现。",
                ),
                "description": (
                    "Control the reveal sequence; avoid disclosing the core truth too early." if _is_en
                    else "控制揭示顺序，避免过早泄露核心真相。"
                ),
                "metadata_json": {"plotline_category": "hidden", "plotline_visibility": "hidden"},
            }
        )

    if antagonist is not None:
        arc_specs.append(
            {
                "arc_code": "faction_pressure",
                "name": (f"{antagonist.name} Counter-Pressure" if _is_en
                         else f"{antagonist.name}反制线"),
                "arc_type": "faction",
                "promise": _ensure_text(
                    antagonist.goal,
                    f"{antagonist.name} will continuously escalate pressure on the protagonist." if _is_en
                    else f"{antagonist.name}会持续升级对主角的压制。",
                ),
                "core_question": (
                    f"How will {antagonist.name} push the situation step by step into greater danger?" if _is_en
                    else f"{antagonist.name}会如何一步步把局势逼向更危险的方向？"
                ),
                "target_payoff": _ensure_text(
                    antagonist.secret,
                    (f"{antagonist.name} is forced into the open for a direct confrontation with the protagonist."
                     if _is_en
                     else f"{antagonist.name}被迫公开下场，与主角正面碰撞。"),
                ),
                "description": antagonist.arc_trajectory,
                "metadata_json": {"plotline_category": "subplot", "plotline_visibility": "visible"},
            }
        )

    # Generate volume-scoped conflict arcs for additional antagonist characters.
    # Each non-primary antagonist gets a volume_conflict arc scoped to volumes
    # where they are active, creating rich subplot diversity.
    _extra_antagonists = [
        ch for ch in (all_antagonists or [])
        if ch.role == "antagonist" and (antagonist is None or ch.id != antagonist.id)
    ]
    for extra_antag in _extra_antagonists:
        # Determine active volumes from character metadata
        antag_meta = extra_antag.metadata_json if isinstance(extra_antag.metadata_json, dict) else {}
        active_vols = antag_meta.get("active_volumes", [])
        scope_vol_raw = active_vols[0] if active_vols else None
        # LLM sometimes outputs a range dict like {'start_volume': 6, 'end_volume': 11}
        # instead of an integer or a list of integers. Normalize to int.
        if isinstance(scope_vol_raw, dict):
            scope_vol = scope_vol_raw.get("start_volume")
        elif isinstance(scope_vol_raw, int):
            scope_vol = scope_vol_raw
        else:
            scope_vol = None
        arc_specs.append(
            {
                "arc_code": f"volume_conflict_{extra_antag.name.lower().replace(' ', '_')}",
                "name": (f"{extra_antag.name} Conflict Arc" if _is_en
                         else f"{extra_antag.name}冲突线"),
                "arc_type": "volume_conflict",
                "promise": _ensure_text(
                    extra_antag.goal,
                    f"{extra_antag.name} serves as the protagonist's core challenge during a specific phase." if _is_en
                    else f"{extra_antag.name}在特定阶段构成主角的核心挑战。",
                ),
                "core_question": (
                    f"How will {extra_antag.name} affect the protagonist's journey?" if _is_en
                    else f"{extra_antag.name}会如何影响主角的旅程？"
                ),
                "target_payoff": _ensure_text(
                    extra_antag.secret,
                    (f"The threat posed by {extra_antag.name} is ultimately resolved or transformed by the protagonist."
                     if _is_en
                     else f"{extra_antag.name}的威胁最终被主角化解或转化。"),
                ),
                "description": extra_antag.arc_trajectory,
                "scope_volume_number": scope_vol,
                "metadata_json": {
                    "plotline_category": "subplot",
                    "plotline_visibility": "visible",
                    "active_volumes": active_vols,
                },
            }
        )

    # Generate hidden arcs for characters with secrets (betrayal setup)
    all_chars = all_antagonists or []
    if protagonist is not None:
        # Check supporting cast characters that have secrets and potential_betrayal
        for ch in all_chars:
            if ch.role in ("ally", "supporting") and ch.secret:
                arc_specs.append(
                    {
                        "arc_code": f"conspiracy_{ch.name.lower().replace(' ', '_')}",
                        "name": (f"{ch.name} Hidden Arc" if _is_en
                                 else f"{ch.name}暗线"),
                        "arc_type": "conspiracy",
                        "promise": (
                            f"{ch.name} harbors a secret unknown to others." if _is_en
                            else f"{ch.name}隐藏着不为人知的秘密。"
                        ),
                        "core_question": (
                            f"What is {ch.name}'s true purpose?" if _is_en
                            else f"{ch.name}的真正目的是什么？"
                        ),
                        "target_payoff": (
                            f"{ch.name}'s secret is revealed at a critical moment." if _is_en
                            else f"{ch.name}的秘密在关键时刻被揭露。"
                        ),
                        "description": ch.secret,
                        "metadata_json": {
                            "plotline_category": "hidden",
                            "plotline_visibility": "hidden",
                        },
                    }
                )

    return arc_specs


def _next_beat_order(beat_orders: dict[str, int], arc_code: str) -> int:
    beat_orders[arc_code] = beat_orders.get(arc_code, 0) + 1
    return beat_orders[arc_code]


def _build_arc_beats(
    *,
    arc_ids: dict[str, UUID],
    protagonist: CharacterModel | None,
    antagonist: CharacterModel | None,
    volumes: list[VolumeModel],
    chapters: list[ChapterModel],
    scenes_by_chapter: dict[UUID, list[SceneCardModel]],
    volume_entries: dict[int, Any],
) -> list[dict[str, Any]]:
    beat_orders: dict[str, int] = {}
    beats: list[dict[str, Any]] = []

    for volume in volumes:
        entry = volume_entries.get(volume.volume_number)
        if "main_plot" in arc_ids:
            beats.append(
                {
                    "plot_arc_id": arc_ids["main_plot"],
                    "arc_code": "main_plot",
                    "beat_order": _next_beat_order(beat_orders, "main_plot"),
                    "scope_level": "volume",
                    "scope_volume_number": volume.volume_number,
                    "scope_chapter_number": None,
                    "scope_scene_number": None,
                    "beat_kind": "volume_goal",
                    "title": volume.title,
                    "summary": _ensure_text(
                        getattr(entry, "volume_goal", None) if entry is not None else None,
                        _ensure_text(volume.goal, f"第{volume.volume_number}卷推进主线任务。"),
                    ),
                    "emotional_shift": getattr(entry, "volume_theme", None) if entry is not None else volume.theme,
                    "information_release": "卷级阶段目标与冲突升级被明确。",
                    "expected_payoff": _ensure_text(
                        getattr(entry, "volume_climax", None) if entry is not None else None,
                        "卷级高潮完成一次主线推进。",
                    ),
                }
            )
        if "mystery_arc" in arc_ids and entry is not None and entry.key_reveals:
            beats.append(
                {
                    "plot_arc_id": arc_ids["mystery_arc"],
                    "arc_code": "mystery_arc",
                    "beat_order": _next_beat_order(beat_orders, "mystery_arc"),
                    "scope_level": "volume",
                    "scope_volume_number": volume.volume_number,
                    "scope_chapter_number": None,
                    "scope_scene_number": None,
                    "beat_kind": "reveal",
                    "title": f"第{volume.volume_number}卷揭示",
                    "summary": _ensure_text(entry.key_reveals[0], "卷级真相推进。"),
                    "emotional_shift": volume.theme,
                    "information_release": "暗线信息开始显露。",
                    "expected_payoff": _ensure_text(entry.key_reveals[-1], "暗线在本卷形成新的疑团。"),
                }
            )
        if "growth_arc" in arc_ids and protagonist is not None:
            beats.append(
                {
                    "plot_arc_id": arc_ids["growth_arc"],
                    "arc_code": "growth_arc",
                    "beat_order": _next_beat_order(beat_orders, "growth_arc"),
                    "scope_level": "volume",
                    "scope_volume_number": volume.volume_number,
                    "scope_chapter_number": None,
                    "scope_scene_number": None,
                    "beat_kind": "growth_step",
                    "title": f"第{volume.volume_number}卷成长台阶",
                    "summary": _ensure_text(
                        getattr(entry.opening_state, "protagonist_status", None) if entry is not None else None,
                        f"{protagonist.name}在本卷进入新的压力阶段。",
                    ),
                    "emotional_shift": getattr(entry, "volume_theme", None) if entry is not None else None,
                    "information_release": _ensure_text(
                        getattr(entry.volume_resolution, "cost_paid", None) if entry is not None else None,
                        "成长伴随代价。",
                    ),
                    "expected_payoff": _ensure_text(
                        getattr(entry.volume_resolution, "new_threat_introduced", None) if entry is not None else None,
                        "成长会迫使主角承担更大风险。",
                    ),
                }
            )
        if "faction_pressure" in arc_ids and antagonist is not None:
            beats.append(
                {
                    "plot_arc_id": arc_ids["faction_pressure"],
                    "arc_code": "faction_pressure",
                    "beat_order": _next_beat_order(beat_orders, "faction_pressure"),
                    "scope_level": "volume",
                    "scope_volume_number": volume.volume_number,
                    "scope_chapter_number": None,
                    "scope_scene_number": None,
                    "beat_kind": "pressure_upgrade",
                    "title": f"第{volume.volume_number}卷反派升级",
                    "summary": _ensure_text(
                        getattr(entry, "volume_obstacle", None) if entry is not None else None,
                        f"{antagonist.name}的压制升级，主角的行动空间被进一步压缩。",
                    ),
                    "emotional_shift": "压迫升级",
                    "information_release": _ensure_text(
                        getattr(entry, "reader_hook_to_next", None) if entry is not None else None,
                        "反派的反制意图被进一步暴露。",
                    ),
                    "expected_payoff": _ensure_text(
                        getattr(entry, "volume_climax", None) if entry is not None else None,
                        "反派动作在卷末形成正面碰撞。",
                    ),
                }
            )

    for chapter in chapters:
        chapter_scenes = scenes_by_chapter.get(chapter.id, [])
        first_scene = chapter_scenes[0] if chapter_scenes else None
        if "main_plot" in arc_ids:
            beats.append(
                {
                    "plot_arc_id": arc_ids["main_plot"],
                    "arc_code": "main_plot",
                    "beat_order": _next_beat_order(beat_orders, "main_plot"),
                    "scope_level": "chapter",
                    "scope_volume_number": None,
                    "scope_chapter_number": chapter.chapter_number,
                    "scope_scene_number": None,
                    "beat_kind": "chapter_push",
                    "title": chapter.title,
                    "summary": chapter.chapter_goal,
                    "emotional_shift": chapter.chapter_emotion_arc,
                    "information_release": chapter.hook_description or chapter.opening_situation,
                    "expected_payoff": chapter.main_conflict or chapter.hook_description,
                }
            )
        if "mystery_arc" in arc_ids and (chapter.hook_description or chapter.information_revealed):
            beats.append(
                {
                    "plot_arc_id": arc_ids["mystery_arc"],
                    "arc_code": "mystery_arc",
                    "beat_order": _next_beat_order(beat_orders, "mystery_arc"),
                    "scope_level": "chapter",
                    "scope_volume_number": None,
                    "scope_chapter_number": chapter.chapter_number,
                    "scope_scene_number": None,
                    "beat_kind": "hint",
                    "title": chapter.title,
                    "summary": _ensure_text(chapter.hook_description, "暗线被进一步点亮。"),
                    "emotional_shift": "疑团加深",
                    "information_release": "新的线索或异常被抛出。",
                    "expected_payoff": _ensure_text(chapter.main_conflict, "后续章节需要解释这个异常。"),
                }
            )
        if "growth_arc" in arc_ids and protagonist is not None:
            protagonist_in_chapter = any(protagonist.name in scene.participants for scene in chapter_scenes)
            if protagonist_in_chapter:
                beats.append(
                    {
                        "plot_arc_id": arc_ids["growth_arc"],
                        "arc_code": "growth_arc",
                        "beat_order": _next_beat_order(beat_orders, "growth_arc"),
                        "scope_level": "chapter",
                        "scope_volume_number": None,
                        "scope_chapter_number": chapter.chapter_number,
                        "scope_scene_number": None,
                        "beat_kind": "growth_step",
                        "title": chapter.title,
                        "summary": _ensure_text(
                            chapter.chapter_emotion_arc,
                            f"{protagonist.name}在本章被迫调整策略与心态。",
                        ),
                        "emotional_shift": chapter.chapter_emotion_arc or "承压推进",
                        "information_release": chapter.main_conflict,
                        "expected_payoff": chapter.hook_description,
                    }
                )
        if "faction_pressure" in arc_ids and antagonist is not None:
            antagonist_in_chapter = any(antagonist.name in scene.participants for scene in chapter_scenes)
            if antagonist_in_chapter or (chapter.main_conflict and antagonist.name in chapter.main_conflict):
                beats.append(
                    {
                        "plot_arc_id": arc_ids["faction_pressure"],
                        "arc_code": "faction_pressure",
                        "beat_order": _next_beat_order(beat_orders, "faction_pressure"),
                        "scope_level": "chapter",
                        "scope_volume_number": None,
                        "scope_chapter_number": chapter.chapter_number,
                        "scope_scene_number": None,
                        "beat_kind": "counter_move",
                        "title": chapter.title,
                        "summary": _ensure_text(
                            chapter.main_conflict,
                            f"{antagonist.name}在本章发起反制或封锁。",
                        ),
                        "emotional_shift": "压迫提升",
                        "information_release": chapter.hook_description,
                        "expected_payoff": "主角下一步必须调整路径。",
                    }
                )

        for scene in chapter_scenes:
            if "main_plot" in arc_ids:
                beats.append(
                    {
                        "plot_arc_id": arc_ids["main_plot"],
                        "arc_code": "main_plot",
                        "beat_order": _next_beat_order(beat_orders, "main_plot"),
                        "scope_level": "scene",
                        "scope_volume_number": None,
                        "scope_chapter_number": chapter.chapter_number,
                        "scope_scene_number": scene.scene_number,
                        "beat_kind": "scene_push",
                        "title": scene.title,
                        "summary": _ensure_text(scene.purpose.get("story"), "场景持续推进当前主线。"),
                        "emotional_shift": _ensure_text(scene.purpose.get("emotion"), "张力上升"),
                        "information_release": scene.title or chapter.hook_description,
                        "expected_payoff": scene.hook_requirement or chapter.hook_description,
                    }
                )
            if "growth_arc" in arc_ids and protagonist is not None and protagonist.name in scene.participants:
                beats.append(
                    {
                        "plot_arc_id": arc_ids["growth_arc"],
                        "arc_code": "growth_arc",
                        "beat_order": _next_beat_order(beat_orders, "growth_arc"),
                        "scope_level": "scene",
                        "scope_volume_number": None,
                        "scope_chapter_number": chapter.chapter_number,
                        "scope_scene_number": scene.scene_number,
                        "beat_kind": "emotion_turn",
                        "title": scene.title,
                        "summary": _ensure_text(
                            scene.purpose.get("emotion"),
                            f"{protagonist.name}在本场经历新的情绪与选择压力。",
                        ),
                        "emotional_shift": _ensure_text(scene.purpose.get("emotion"), "情绪波动"),
                        "information_release": scene.title,
                        "expected_payoff": scene.hook_requirement,
                    }
                )
            if first_scene is not None and scene.id == first_scene.id and "mystery_arc" in arc_ids and chapter.hook_description:
                beats.append(
                    {
                        "plot_arc_id": arc_ids["mystery_arc"],
                        "arc_code": "mystery_arc",
                        "beat_order": _next_beat_order(beat_orders, "mystery_arc"),
                        "scope_level": "scene",
                        "scope_volume_number": None,
                        "scope_chapter_number": chapter.chapter_number,
                        "scope_scene_number": scene.scene_number,
                        "beat_kind": "tease",
                        "title": scene.title,
                        "summary": chapter.hook_description,
                        "emotional_shift": "疑问被抛出",
                        "information_release": "场景为暗线留下注脚。",
                        "expected_payoff": chapter.main_conflict,
                    }
                )

    return beats


def _match_clue_code(payoff_label: str, clue_specs: list[dict[str, Any]]) -> str | None:
    payoff_token = _normalized_token(payoff_label)
    if not payoff_token:
        return None
    for clue in clue_specs:
        clue_token = _normalized_token(str(clue["label"]))
        if clue_token and (clue_token in payoff_token or payoff_token in clue_token):
            return str(clue["clue_code"])
    return None


def _build_clues_and_payoffs(
    *,
    arc_ids: dict[str, UUID],
    volumes: list[VolumeModel],
    chapters: list[ChapterModel],
    scenes_by_chapter: dict[UUID, list[SceneCardModel]],
    volume_entries: dict[int, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chapters_by_volume: dict[int, list[ChapterModel]] = defaultdict(list)
    volume_number_by_id = {volume.id: volume.volume_number for volume in volumes}
    for chapter in chapters:
        volume_number = volume_number_by_id.get(chapter.volume_id)
        if volume_number is not None:
            chapters_by_volume[volume_number].append(chapter)

    for volume_chapters in chapters_by_volume.values():
        volume_chapters.sort(key=lambda item: item.chapter_number)

    clue_specs: list[dict[str, Any]] = []
    payoff_specs: list[dict[str, Any]] = []
    clue_index = 0
    payoff_index = 0
    mystery_arc_id = arc_ids.get("mystery_arc") or arc_ids.get("main_plot")

    for volume in volumes:
        entry = volume_entries.get(volume.volume_number)
        volume_chapters = chapters_by_volume.get(volume.volume_number, [])
        if not volume_chapters:
            continue
        first_chapter = volume_chapters[0]
        last_chapter = volume_chapters[-1]
        first_scene_number = scenes_by_chapter.get(first_chapter.id, [None])[0].scene_number if scenes_by_chapter.get(first_chapter.id) else 1
        last_scene_number = scenes_by_chapter.get(last_chapter.id, [None])[-1].scene_number if scenes_by_chapter.get(last_chapter.id) else None

        planted_items = list(getattr(entry, "foreshadowing_planted", []) if entry is not None else [])
        if not planted_items and first_chapter.hook_description:
            planted_items = [first_chapter.hook_description]
        for item in planted_items:
            clue_index += 1
            label = _ensure_text(item, f"伏笔{clue_index}")
            clue_specs.append(
                {
                    "clue_code": f"clue-{clue_index:03d}",
                    "label": label[:200],
                    "clue_type": "foreshadow",
                    "description": label,
                    "plot_arc_id": mystery_arc_id,
                    "planted_in_volume_number": volume.volume_number,
                    "planted_in_chapter_number": first_chapter.chapter_number,
                    "planted_in_scene_number": first_scene_number,
                    "expected_payoff_by_volume_number": volume.volume_number,
                    "expected_payoff_by_chapter_number": last_chapter.chapter_number,
                    "expected_payoff_by_scene_number": last_scene_number,
                    "actual_paid_off_chapter_number": None,
                    "actual_paid_off_scene_number": None,
                    "reveal_guard": "在对应 payoff 前不得完整揭露。",
                    "status": "planted",
                }
            )

        payoff_items = list(getattr(entry, "foreshadowing_paid_off", []) if entry is not None else [])
        for item in payoff_items:
            payoff_index += 1
            label = _ensure_text(item, f"兑现{payoff_index}")
            matched_clue_code = _match_clue_code(label, clue_specs)
            payoff_specs.append(
                {
                    "payoff_code": f"payoff-{payoff_index:03d}",
                    "label": label[:200],
                    "description": label,
                    "plot_arc_id": mystery_arc_id,
                    "source_clue_code": matched_clue_code,
                    "target_volume_number": volume.volume_number,
                    "target_chapter_number": last_chapter.chapter_number,
                    "target_scene_number": last_scene_number,
                    "actual_chapter_number": last_chapter.chapter_number,
                    "actual_scene_number": last_scene_number,
                    "status": "paid_off",
                }
            )

    return clue_specs, payoff_specs


def _clamp_metric(value: float) -> float:
    return max(0.0, min(1.0, round(value, 2)))


def _relationship_track_type(relationship: RelationshipModel) -> str:
    relationship_text = " ".join(
        str(item)
        for item in (
            relationship.relationship_type,
            relationship.public_face,
            relationship.private_reality,
            relationship.tension_summary,
        )
        if item
    ).lower()
    if any(token in relationship_text for token in ("恋", "爱", "romance", "暧昧", "情侣")):
        return "romance"
    if any(token in relationship_text for token in ("搭档", "盟友", "朋友", "mentor", "ally")):
        return "bond"
    if any(token in relationship_text for token in ("family", "亲", "兄弟", "姐妹", "父", "母")):
        return "family"
    if any(token in relationship_text for token in ("rival", "敌", "仇", "conflict", "对立")):
        return "rivalry"
    return "tension"


def _intimacy_stage(track_type: str, trust_level: float, attraction_level: float, conflict_level: float) -> str:
    if track_type == "romance":
        if attraction_level >= 0.8 and trust_level >= 0.7:
            return "confirmation"
        if attraction_level >= 0.6:
            return "push_pull"
        return "setup"
    if conflict_level >= 0.75:
        return "confrontation"
    if trust_level >= 0.7:
        return "alliance"
    return "setup"


def _build_emotion_track_specs(
    *,
    protagonist: CharacterModel | None,
    antagonist: CharacterModel | None,
    relationships: list[RelationshipModel],
    characters_by_id: dict[UUID, CharacterModel],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[UUID | None, UUID | None, str]] = set()
    for relationship in relationships:
        left = characters_by_id.get(relationship.character_a_id)
        right = characters_by_id.get(relationship.character_b_id)
        if left is None or right is None:
            continue
        if protagonist is not None and protagonist.id not in {left.id, right.id} and relationship.strength == 0:
            continue
        track_type = _relationship_track_type(relationship)
        pair_key = tuple(sorted((left.id, right.id), key=lambda item: str(item))) + (track_type,)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        base_strength = float(relationship.strength or 0)
        trust_level = _clamp_metric((base_strength + 1) / 2)
        conflict_bias = 0.8 if track_type == "rivalry" else 0.35
        conflict_level = _clamp_metric(
            max(abs(base_strength), conflict_bias) if relationship.tension_summary else conflict_bias
        )
        attraction_level = _clamp_metric(
            0.7
            if track_type == "romance"
            else 0.45
            if track_type == "bond" and "旧" in (relationship.relationship_type or "")
            else 0.15
        )
        distance_level = _clamp_metric(1 - trust_level * 0.7)
        intimacy_stage = _intimacy_stage(track_type, trust_level, attraction_level, conflict_level)
        summary = _ensure_text(
            relationship.tension_summary or relationship.private_reality,
            f"{left.name}与{right.name}之间存在持续张力，需要在后续章节继续推进。",
        )
        desired_payoff = _ensure_text(
            relationship.private_reality or relationship.public_face,
            f"{left.name}与{right.name}的关系需要在后续形成明显转折。",
        )
        specs.append(
            {
                "track_code": f"{track_type}-{_normalized_token(left.name)}-{_normalized_token(right.name)}"[:64],
                "track_type": track_type,
                "title": f"{left.name} / {right.name} {track_type}",
                "character_a_id": left.id,
                "character_b_id": right.id,
                "character_a_label": left.name,
                "character_b_label": right.name,
                "relationship_type": relationship.relationship_type,
                "summary": summary,
                "desired_payoff": desired_payoff,
                "trust_level": trust_level,
                "attraction_level": attraction_level,
                "distance_level": distance_level,
                "conflict_level": conflict_level,
                "intimacy_stage": intimacy_stage,
                "last_shift_chapter_number": relationship.last_changed_chapter_no or relationship.established_chapter_no,
                "status": "active",
                "metadata_json": relationship.metadata_json or {},
            }
        )

    if protagonist is not None and antagonist is not None and not any(
        protagonist.id in {spec["character_a_id"], spec["character_b_id"]}
        and antagonist.id in {spec["character_a_id"], spec["character_b_id"]}
        for spec in specs
    ):
        specs.append(
            {
                "track_code": f"rivalry-{_normalized_token(protagonist.name)}-{_normalized_token(antagonist.name)}"[:64],
                "track_type": "rivalry",
                "title": f"{protagonist.name} / {antagonist.name} 对抗线",
                "character_a_id": protagonist.id,
                "character_b_id": antagonist.id,
                "character_a_label": protagonist.name,
                "character_b_label": antagonist.name,
                "relationship_type": "conflict",
                "summary": f"{protagonist.name}与{antagonist.name}的冲突会持续升级。",
                "desired_payoff": antagonist.secret or protagonist.goal or "双方在高潮形成正面碰撞。",
                "trust_level": 0.05,
                "attraction_level": 0.0,
                "distance_level": 0.95,
                "conflict_level": 0.9,
                "intimacy_stage": "confrontation",
                "last_shift_chapter_number": 1,
                "status": "active",
                "metadata_json": {"generated": "fallback"},
            }
        )

    return specs


_CONFLICT_PHASE_PRESSURE: dict[str, float] = {
    "survival": 0.40,
    "political_intrigue": 0.50,
    "betrayal": 0.65,
    "faction_war": 0.70,
    "existential_threat": 0.85,
    "internal_reckoning": 0.75,
}


def _build_antagonist_plan_specs(
    *,
    protagonist: CharacterModel | None,
    antagonist: CharacterModel | None,
    all_antagonists: list[CharacterModel] | None = None,
    volumes: list[VolumeModel],
    chapters_by_volume: dict[int, list[ChapterModel]],
    volume_entries: dict[int, Any],
) -> list[dict[str, Any]]:
    if antagonist is None and not (all_antagonists or []):
        return []
    all_chapters = sorted(
        [chapter for volume_chapters in chapters_by_volume.values() for chapter in volume_chapters],
        key=lambda item: item.chapter_number,
    )
    final_chapter_number = all_chapters[-1].chapter_number if all_chapters else None
    specs: list[dict[str, Any]] = []

    # 1. Master plan for the primary antagonist (the "final boss")
    if antagonist is not None:
        specs.append(
            {
                "plan_code": "main-antagonist-plan",
                "title": f"{antagonist.name}总体反制计划",
                "threat_type": "master_plan",
                "goal": _ensure_text(
                    antagonist.goal,
                    f"{antagonist.name}要阻止主角接近核心真相。",
                ),
                "current_move": _ensure_text(
                    antagonist.arc_state,
                    f"{antagonist.name}正在通过体系与代理人持续施压。",
                ),
                "next_countermove": _ensure_text(
                    antagonist.secret,
                    f"{antagonist.name}下一步会把压制升级成公开对撞。",
                ),
                "escalation_condition": _ensure_text(
                    protagonist.goal if protagonist is not None else None,
                    "主角一旦拿到关键证据，反派必须提前动手。",
                ),
                "reveal_timing": "终局前",
                "scope_volume_number": None,
                "target_chapter_number": final_chapter_number,
                "pressure_level": 0.85,
                "status": "active",
                "metadata_json": {"generated": "master_plan"},
            }
        )

    # 2. Per-volume plans — use volume's conflict_phase for pressure calculation
    for volume in volumes:
        entry = volume_entries.get(volume.volume_number)
        volume_chapters = sorted(
            chapters_by_volume.get(volume.volume_number, []),
            key=lambda item: item.chapter_number,
        )
        target_chapter_number = volume_chapters[-1].chapter_number if volume_chapters else None

        # Extract conflict_phase from volume metadata if available
        vol_meta = volume.metadata_json if isinstance(volume.metadata_json, dict) else {}
        conflict_phase = vol_meta.get("conflict_phase", "")
        force_name = vol_meta.get("primary_force_name", antagonist.name if antagonist else "敌对势力")

        # Use phase-based pressure instead of linear formula
        if conflict_phase and conflict_phase in _CONFLICT_PHASE_PRESSURE:
            pressure_level = _clamp_metric(_CONFLICT_PHASE_PRESSURE[conflict_phase])
        else:
            pressure_level = _clamp_metric(0.45 + (volume.volume_number * 0.12))

        # Find the matching antagonist character for this volume.
        # Priority order:
        #   1. A non-primary antagonist whose metadata.active_volumes includes
        #      this volume (populated from cast_spec.antagonist_forces.character_ref
        #      during persist_cast_spec).
        #   2. The primary antagonist if they themselves carry active_volumes
        #      for this volume.
        #   3. Fall back to the primary antagonist.
        plan_antagonist: CharacterModel | None = None
        extras = [c for c in (all_antagonists or []) if antagonist is None or c.id != antagonist.id]
        for extra in extras:
            extra_meta = extra.metadata_json if isinstance(extra.metadata_json, dict) else {}
            if _is_volume_active(volume.volume_number, extra_meta.get("active_volumes") or []):
                plan_antagonist = extra
                break
        if plan_antagonist is None and antagonist is not None:
            prim_meta = antagonist.metadata_json if isinstance(antagonist.metadata_json, dict) else {}
            prim_active = prim_meta.get("active_volumes") or []
            if not prim_active or _is_volume_active(volume.volume_number, prim_active):
                plan_antagonist = antagonist
        if plan_antagonist is None:
            plan_antagonist = antagonist

        plan_label = plan_antagonist.name if plan_antagonist else force_name
        specs.append(
            {
                "plan_code": f"volume-{volume.volume_number:02d}-pressure",
                "title": f"第{volume.volume_number}卷·{force_name}",
                "threat_type": conflict_phase or "volume_pressure",
                "goal": _ensure_text(
                    getattr(entry, "volume_obstacle", None) if entry is not None else None,
                    volume.obstacle or f"{plan_label}在本卷持续收紧主角的行动空间。",
                ),
                "current_move": _ensure_text(
                    volume.obstacle,
                    f"{plan_label}在本卷布置新的封锁和压迫手段。",
                ),
                "next_countermove": _ensure_text(
                    getattr(entry, "reader_hook_to_next", None) if entry is not None else None,
                    f"{plan_label}在卷末会把对抗升级到更高层级。",
                ),
                "escalation_condition": _ensure_text(
                    getattr(entry, "volume_goal", None) if entry is not None else None,
                    "主角一旦推进主线，对手必须同步升级压制。",
                ),
                "reveal_timing": f"第{volume.volume_number}卷",
                "scope_volume_number": volume.volume_number,
                "target_chapter_number": target_chapter_number,
                "pressure_level": pressure_level,
                "status": "active" if target_chapter_number is None or volume.volume_number == 1 else "planned",
                "metadata_json": {
                    "volume_title": volume.title,
                    "conflict_phase": conflict_phase,
                    "force_name": force_name,
                    "antagonist_label": plan_label,
                    "antagonist_character_id": str(plan_antagonist.id)
                    if plan_antagonist is not None and getattr(plan_antagonist, "id", None) is not None
                    else None,
                },
            }
        )
    return specs


# ── Builder functions for the 7 new narrative depth models ──


_PHASE_TENSION: dict[str, float] = {
    "setup": 0.25, "investigation": 0.40, "expansion": 0.50,
    "complication": 0.60, "confrontation": 0.70, "reversal": 0.80,
    "climax": 0.95, "resolution": 0.35, "epilogue": 0.15,
}


def _build_theme_arc_specs(
    project: ProjectModel,
    *,
    volumes: list[VolumeModel],
    volume_entries: dict[int, Any],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    book_theme = (project.metadata_json or {}).get("book_spec", {}).get("theme")
    if book_theme and isinstance(book_theme, str):
        specs.append({
            "theme_code": "main-theme",
            "theme_statement": book_theme,
            "symbol_set": [],
            "evolution_stages": ["introduced", "tested", "deepened", "resolved"],
            "current_stage": "introduced",
            "status": "active",
        })
    for volume in volumes:
        entry = volume_entries.get(volume.volume_number)
        vol_theme = getattr(entry, "volume_theme", None) if entry is not None else None
        vol_theme = vol_theme or volume.theme
        if vol_theme and isinstance(vol_theme, str):
            specs.append({
                "theme_code": f"vol-{volume.volume_number:02d}-theme",
                "theme_statement": vol_theme,
                "symbol_set": [],
                "evolution_stages": ["introduced", "tested", "resolved"],
                "current_stage": "introduced",
                "status": "active",
            })
    if not specs:
        specs.append({
            "theme_code": "main-theme",
            "theme_statement": project.title or "本书的核心主题需要在后续规划中补充。",
            "symbol_set": [],
            "evolution_stages": ["introduced", "tested", "deepened", "resolved"],
            "current_stage": "introduced",
            "status": "active",
        })
    return specs


def _build_motif_placement_specs(
    *,
    theme_arcs_by_code: dict[str, Any],
    chapters: list[ChapterModel],
    chapters_by_volume: dict[int, list[ChapterModel]] | None = None,
    volume_entries: dict[int, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build motif placement specs that scale with volume count.

    Pre-fix (starvation): exactly 4 placements across the entire novel
    (one every ~79 chapters for a 316-chapter book), leaving 80 %+ of
    volumes with no theme anchor — this was the root cause of the
    observed volume-level repetition where every volume collapsed to the
    same surface-level pressure template (道种破虚 audit).

    Post-fix contract (see ``bestseller.services.motif_scaling``):
      * Every volume MUST receive ≥ 1 placement (plant/echo/transform/resolve
        rotates so each of the four canonical types appears at least once
        in the book).
      * Default density is **2 placements per volume** — the first
        volume seeds the theme's plant + echo, the mid-band carries
        echo + transform, and the final volume carries transform + resolve.
      * When multiple theme_arcs exist (main-theme plus per-volume
        arcs), each volume's placements draw from BOTH the main arc and
        the volume-local arc so the theme layer is not a single voice
        repeating itself.

    Parameters
    ----------
    theme_arcs_by_code
        Theme arc models keyed by theme_code. ``main-theme`` is used as
        the book-wide spine; ``vol-NN-theme`` entries (if present) are
        used as per-volume accents.
    chapters
        Flat list of ChapterModel sorted by chapter_number.
    chapters_by_volume
        Chapters grouped by volume_number (sorted within each volume).
        When supplied, placements are scheduled per-volume; when missing
        we fall back to the legacy global-rhythm behaviour.
    volume_entries
        Volume plan entries keyed by volume_number. Used to pull a
        concrete motif_label from ``volume_theme`` where available.
    """

    specs: list[dict[str, Any]] = []
    if not theme_arcs_by_code or not chapters:
        return specs

    main_theme_arc = theme_arcs_by_code.get("main-theme")
    if main_theme_arc is None:
        main_theme_arc = next(iter(theme_arcs_by_code.values()))
    main_theme_arc_id = main_theme_arc.id

    # Legacy path: no per-volume grouping available → fall back to the
    # pre-fix global rhythm (this is only hit from callers that predate
    # the chapters_by_volume wiring).
    if not chapters_by_volume:
        total = len(chapters)
        placement_points = [
            (0, "plant"),
            (max(1, total // 4), "echo"),
            (max(2, total // 2), "transform"),
            (total - 1, "resolve"),
        ]
        for idx, ptype in placement_points:
            if idx < total:
                chapter = chapters[idx]
                specs.append({
                    "theme_arc_id": main_theme_arc_id,
                    "motif_label": f"主题意象-{ptype}",
                    "placement_type": ptype,
                    "volume_number": None,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": 1,
                    "description": (
                        f"第{chapter.chapter_number}章通过{ptype}手法呈现核心主题意象。"
                    ),
                    "status": "planned",
                })
        return specs

    # Per-volume scaling path. Distribute the plant→echo→transform→resolve
    # rhythm across the whole book and ALSO give every volume at least
    # one placement so the theme never goes dark for a full volume.
    volume_numbers = sorted(chapters_by_volume.keys())
    total_volumes = len(volume_numbers)
    if total_volumes == 0:
        return specs

    # Book-wide rhythm: distribute plant/echo/transform/resolve across
    # quartiles of volumes so the macro-arc still reads plant-early,
    # resolve-late.
    def _stage_for_volume(idx: int) -> str:
        # idx is 0-based position in the volume sequence.
        # First quarter → plant, second → echo, third → transform, last → resolve.
        if total_volumes <= 1:
            return "resolve"
        ratio = idx / max(total_volumes - 1, 1)
        if ratio < 0.25:
            return "plant"
        if ratio < 0.55:
            return "echo"
        if ratio < 0.85:
            return "transform"
        return "resolve"

    for idx, vol_num in enumerate(volume_numbers):
        vol_chapters = chapters_by_volume.get(vol_num) or []
        if not vol_chapters:
            continue
        primary_stage = _stage_for_volume(idx)
        # Secondary stage is the "next" canonical stage to thread the
        # plant→echo→transform→resolve arc continuously inside the
        # volume, without losing the macro-rhythm.
        rhythm = ("plant", "echo", "transform", "resolve")
        sec_idx = (rhythm.index(primary_stage) + 1) % len(rhythm)
        secondary_stage = rhythm[sec_idx] if total_volumes > 1 else "resolve"

        # Pick the per-volume arc (if present) for the secondary placement
        # so the theme layer speaks with two voices: the main book spine +
        # the volume-local accent.
        vol_theme_code = f"vol-{vol_num:02d}-theme"
        vol_theme_arc = theme_arcs_by_code.get(vol_theme_code)
        secondary_arc_id = (
            vol_theme_arc.id if vol_theme_arc is not None else main_theme_arc_id
        )

        # Motif label: prefer the volume's own theme statement (if the
        # volume plan provided one) so the label is concrete, not a
        # template placeholder.
        vol_entry = (volume_entries or {}).get(vol_num)
        vol_theme_text = None
        if vol_entry is not None:
            vol_theme_text = getattr(vol_entry, "volume_theme", None)
        label_core = (
            (vol_theme_text.strip() if isinstance(vol_theme_text, str) and vol_theme_text.strip()
             else None)
            or getattr(vol_theme_arc, "theme_statement", None)
            or "核心意象"
        )

        # Choose two distinct chapters inside the volume for the two
        # placements — the first near the opening, the second near the
        # volume climax.
        first_chapter = vol_chapters[0]
        climax_idx = max(0, int(len(vol_chapters) * 0.7) - 1)
        climax_chapter = vol_chapters[climax_idx]
        # Avoid pointing both placements at the same chapter when the
        # volume is very short.
        if climax_chapter.chapter_number == first_chapter.chapter_number and len(vol_chapters) > 1:
            climax_chapter = vol_chapters[-1]

        specs.append({
            "theme_arc_id": main_theme_arc_id,
            "motif_label": f"{label_core}·{primary_stage}",
            "placement_type": primary_stage,
            "volume_number": vol_num,
            "chapter_number": first_chapter.chapter_number,
            "scene_number": 1,
            "description": (
                f"第{vol_num}卷以 {primary_stage} 阶段引入主题意象「{label_core}」，"
                f"落在第 {first_chapter.chapter_number} 章。"
            ),
            "status": "planned",
        })
        specs.append({
            "theme_arc_id": secondary_arc_id,
            "motif_label": f"{label_core}·{secondary_stage}",
            "placement_type": secondary_stage,
            "volume_number": vol_num,
            "chapter_number": climax_chapter.chapter_number,
            "scene_number": 1,
            "description": (
                f"第{vol_num}卷在第 {climax_chapter.chapter_number} 章以 "
                f"{secondary_stage} 手法延展主题意象「{label_core}」。"
            ),
            "status": "planned",
        })

    return specs


def _build_subplot_schedule_specs(
    *,
    arcs_by_code: dict[str, PlotArcModel],
    chapters: list[ChapterModel],
    beats_by_chapter: dict[int, list[Any]],
    chapters_by_volume: dict[int, list[ChapterModel]] | None = None,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    subplot_arcs = {
        code: arc for code, arc in arcs_by_code.items()
        if arc.arc_type not in {"main_plot"}
    }
    if not subplot_arcs:
        return specs

    # Build chapter→volume lookup
    ch_to_vol: dict[int, int] = {}
    for vol_num, vol_chapters in (chapters_by_volume or {}).items():
        for ch in vol_chapters:
            ch_to_vol[ch.chapter_number] = vol_num

    for chapter in chapters:
        chapter_beat_arc_codes = {
            str(beat.metadata_json.get("arc_code"))
            for beat in beats_by_chapter.get(chapter.chapter_number, [])
        }
        ch_vol = ch_to_vol.get(chapter.chapter_number, 1)
        for arc_code, arc in subplot_arcs.items():
            arc_meta = arc.metadata_json if isinstance(arc.metadata_json, dict) else {}
            plotline_visibility = arc_meta.get("plotline_visibility", "visible")
            active_volumes = arc_meta.get("active_volumes", [])

            # Hidden arcs stay dormant/mention until their active volume
            if plotline_visibility == "hidden":
                if active_volumes and not _is_volume_active(ch_vol, active_volumes):
                    prominence = "dormant"
                elif arc_code in chapter_beat_arc_codes:
                    prominence = "secondary"  # hidden arcs don't dominate
                elif chapter.chapter_number <= 2:
                    prominence = "mention"
                else:
                    prominence = "dormant"
            # Volume-scoped arcs are only active in their volumes
            elif active_volumes:
                if _is_volume_active(ch_vol, active_volumes):
                    if arc_code in chapter_beat_arc_codes:
                        prominence = "primary"
                    else:
                        prominence = "secondary"
                else:
                    prominence = "dormant"
            # Standard logic for visible, project-scoped arcs
            elif arc_code in chapter_beat_arc_codes:
                prominence = "primary"
            elif chapter.chapter_number <= 2:
                prominence = "mention"
            else:
                prominence = "dormant"

            specs.append({
                "plot_arc_id": arc.id,
                "arc_code": arc_code,
                "chapter_number": chapter.chapter_number,
                "prominence": prominence,
            })
    return specs


def _build_relationship_event_specs(
    *,
    relationships: list[RelationshipModel],
    characters_by_id: dict[UUID, CharacterModel],
    chapters: list[ChapterModel],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if not relationships or not chapters:
        return specs
    for relationship in relationships:
        left = characters_by_id.get(relationship.character_a_id)
        right = characters_by_id.get(relationship.character_b_id)
        if left is None or right is None:
            continue
        established_ch = relationship.established_chapter_no or 1
        last_changed_ch = relationship.last_changed_chapter_no
        specs.append({
            "character_a_label": left.name,
            "character_b_label": right.name,
            "chapter_number": established_ch,
            "scene_number": 1,
            "event_description": f"{left.name}与{right.name}的{relationship.relationship_type}关系建立。",
            "relationship_change": relationship.public_face or relationship.relationship_type,
            "is_milestone": True,
        })
        if last_changed_ch is not None and last_changed_ch > established_ch:
            specs.append({
                "character_a_label": left.name,
                "character_b_label": right.name,
                "chapter_number": last_changed_ch,
                "scene_number": None,
                "event_description": _ensure_text(
                    relationship.tension_summary,
                    f"{left.name}与{right.name}的关系发生变化。",
                ),
                "relationship_change": relationship.private_reality or "关系转折",
                "is_milestone": bool(relationship.tension_summary),
            })
    return specs


def _build_reader_knowledge_specs(
    *,
    clues_by_code: dict[str, ClueModel],
    payoffs_by_code: dict[str, PayoffModel],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for clue in clues_by_code.values():
        if clue.planted_in_chapter_number is not None:
            specs.append({
                "chapter_number": clue.planted_in_chapter_number,
                "knowledge_item": _ensure_text(clue.description, clue.label),
                "audience": "reader_only" if clue.clue_type in {"hidden", "dramatic_irony"} else "both",
                "source_clue_code": clue.clue_code,
            })
    for payoff in payoffs_by_code.values():
        target_ch = payoff.actual_chapter_number or payoff.target_chapter_number
        if target_ch is not None:
            specs.append({
                "chapter_number": target_ch,
                "knowledge_item": _ensure_text(payoff.description, payoff.label),
                "audience": "both",
                "source_clue_code": None,
            })
    return specs


def _build_ending_contract_spec(
    *,
    arcs_by_code: dict[str, PlotArcModel],
    clues_by_code: dict[str, ClueModel],
    emotion_track_models: list[EmotionTrackModel],
    theme_arcs: list[ThemeArcModel],
) -> dict[str, Any]:
    arcs_to_resolve = [arc.arc_code for arc in arcs_by_code.values() if arc.status in ("planned", "active")]
    clues_to_payoff = [
        clue.clue_code for clue in clues_by_code.values()
        if clue.actual_paid_off_chapter_number is None
    ]
    relationships_to_close = [
        track.title for track in emotion_track_models
        if track.status == "active"
    ][:10]
    main_theme = next((t for t in theme_arcs if t.theme_code == "main-theme"), None)
    thematic_final = main_theme.theme_statement if main_theme else "核心主题需要在结局获得最终回应。"
    return {
        "arcs_to_resolve": arcs_to_resolve,
        "clues_to_payoff": clues_to_payoff,
        "relationships_to_close": relationships_to_close,
        "thematic_final_expression": thematic_final,
        "denouement_plan": "高潮后留出一个余韵场景收束情绪和主题。",
        "status": "planned",
    }


def _build_pacing_curve_specs(
    *,
    chapters: list[ChapterModel],
    scenes_by_chapter: dict[UUID, list[SceneCardModel]],
    structure_template: StructureTemplate | None = None,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    total = len(chapters)

    # Pre-compute structure beat positions mapped to chapter indices
    beat_by_chapter_idx: dict[int, tuple[str, float, float]] = {}
    if structure_template is not None:
        for beat in structure_template.beats:
            ch_idx = min(round(beat.position_pct * max(total - 1, 1)), total - 1)
            lo, hi = beat.tension_range
            beat_by_chapter_idx[ch_idx] = (beat.beat_name, lo, hi)

    for idx, chapter in enumerate(chapters):
        chapter_scenes = scenes_by_chapter.get(chapter.id, [])
        dominant_scene_type = chapter_scenes[0].scene_type if chapter_scenes else "hook"
        progress = idx / max(total - 1, 1)

        # Check if a structure beat anchors this chapter
        template_hit = beat_by_chapter_idx.get(idx)
        if template_hit is not None:
            _, lo, hi = template_hit
            base_tension = (lo + hi) / 2
        elif progress < 0.15:
            base_tension = 0.25
        elif progress < 0.45:
            base_tension = 0.25 + (progress - 0.15) * 1.5
        elif progress < 0.75:
            base_tension = 0.70 + (progress - 0.45) * 0.5
        elif progress < 0.90:
            base_tension = 0.85 + (progress - 0.75) * 0.67
        else:
            base_tension = 0.95 - (progress - 0.90) * 3.0

        phase = (chapter.metadata_json or {}).get("phase", "")
        if phase in _PHASE_TENSION:
            base_tension = (base_tension + _PHASE_TENSION[phase]) / 2
        tension = round(max(0.05, min(0.99, base_tension)), 2)
        notes = template_hit[0] if template_hit else None
        specs.append({
            "chapter_number": chapter.chapter_number,
            "tension_level": tension,
            "scene_type_plan": dominant_scene_type,
            "notes": notes,
        })
    return specs


async def rebuild_narrative_graph(
    session: AsyncSession,
    *,
    project: ProjectModel,
    volume_plan_content: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    if not chapters:
        raise ValueError(f"Project '{project.slug}' does not have chapters for narrative graph compilation.")

    volumes = list(
        await session.scalars(
            select(VolumeModel)
            .where(VolumeModel.project_id == project.id)
            .order_by(VolumeModel.volume_number.asc())
        )
    )
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.project_id == project.id)
            .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
        )
    )
    characters = list(
        await session.scalars(
            select(CharacterModel)
            .where(CharacterModel.project_id == project.id)
            .order_by(CharacterModel.role.asc(), CharacterModel.name.asc())
        )
    )
    relationships = list(
        await session.scalars(
            select(RelationshipModel)
            .where(RelationshipModel.project_id == project.id)
            .order_by(
                RelationshipModel.last_changed_chapter_no.asc().nullsfirst(),
                RelationshipModel.established_chapter_no.asc().nullsfirst(),
                RelationshipModel.relationship_type.asc(),
            )
        )
    )
    protagonist = next((item for item in characters if item.role == "protagonist"), None)
    antagonist = next((item for item in characters if item.role == "antagonist"), None)
    all_antagonists = [item for item in characters if item.role == "antagonist"]
    characters_by_id = {item.id: item for item in characters}
    volume_number_by_id = {volume.id: volume.volume_number for volume in volumes if volume.id is not None}

    scenes_by_chapter: dict[UUID, list[SceneCardModel]] = defaultdict(list)
    for scene in scenes:
        scenes_by_chapter[scene.chapter_id].append(scene)
    for scene_list in scenes_by_chapter.values():
        scene_list.sort(key=lambda item: item.scene_number)

    chapters_by_volume: dict[int, list[ChapterModel]] = defaultdict(list)
    for chapter in chapters:
        volume_number = volume_number_by_id.get(chapter.volume_id)
        if volume_number is not None:
            chapters_by_volume[volume_number].append(chapter)
    for chapter_list in chapters_by_volume.values():
        chapter_list.sort(key=lambda item: item.chapter_number)

    volume_entries = {
        item.volume_number: item
        for item in parse_volume_plan_input(volume_plan_content or {"volumes": []})
    }

    for model in (
        PacingCurvePointModel,
        EndingContractModel,
        ReaderKnowledgeEntryModel,
        RelationshipEventModel,
        SubplotScheduleModel,
        MotifPlacementModel,
        ThemeArcModel,
        SceneContractModel,
        ChapterContractModel,
        PayoffModel,
        ClueModel,
        ArcBeatModel,
        PlotArcModel,
        EmotionTrackModel,
        AntagonistPlanModel,
    ):
        await session.execute(delete(model).where(model.project_id == project.id))
    await session.flush()

    arc_specs = _build_arc_specs(
        project,
        protagonist=protagonist,
        antagonist=antagonist,
        all_antagonists=all_antagonists,
        volumes=volumes,
        volume_entries=volume_entries,
    )
    arcs_by_code: dict[str, PlotArcModel] = {}
    for spec in arc_specs:
        arc = PlotArcModel(
            project_id=project.id,
            arc_code=spec["arc_code"],
            name=spec["name"],
            arc_type=spec["arc_type"],
            promise=spec["promise"],
            core_question=spec["core_question"],
            target_payoff=spec.get("target_payoff"),
            status="planned",
            scope_level="volume" if spec.get("scope_volume_number") else "project",
            scope_volume_number=spec.get("scope_volume_number"),
            scope_chapter_number=None,
            description=spec.get("description"),
            metadata_json=spec.get("metadata_json", {}),
        )
        session.add(arc)
        arcs_by_code[spec["arc_code"]] = arc
    await session.flush()

    arc_ids = {code: arc.id for code, arc in arcs_by_code.items()}
    beat_specs = _build_arc_beats(
        arc_ids=arc_ids,
        protagonist=protagonist,
        antagonist=antagonist,
        volumes=volumes,
        chapters=chapters,
        scenes_by_chapter=scenes_by_chapter,
        volume_entries=volume_entries,
    )
    beat_models: list[ArcBeatModel] = []
    for spec in beat_specs:
        beat = ArcBeatModel(
            project_id=project.id,
            plot_arc_id=spec["plot_arc_id"],
            beat_order=spec["beat_order"],
            scope_level=spec["scope_level"],
            scope_volume_number=spec["scope_volume_number"],
            scope_chapter_number=spec["scope_chapter_number"],
            scope_scene_number=spec["scope_scene_number"],
            beat_kind=spec["beat_kind"],
            title=spec.get("title"),
            summary=spec["summary"],
            emotional_shift=spec.get("emotional_shift"),
            information_release=spec.get("information_release"),
            expected_payoff=spec.get("expected_payoff"),
            status="planned",
            metadata_json={"arc_code": spec["arc_code"]},
        )
        session.add(beat)
        beat_models.append(beat)
    await session.flush()

    # ── Stamp structure beat names into main_plot arc beat metadata ──
    _structure_key = getattr(project, "metadata_json", {}).get("structure_template") or "three-act"
    _struct_tmpl = resolve_structure_template(_structure_key)
    _total_chapters = len(chapters)
    for struct_beat in _struct_tmpl.beats:
        _target_ch = chapters[
            min(round(struct_beat.position_pct * max(_total_chapters - 1, 1)), _total_chapters - 1)
        ].chapter_number
        for bm in beat_models:
            if (
                bm.scope_chapter_number == _target_ch
                and bm.scope_level == "chapter"
                and (bm.metadata_json or {}).get("arc_code") == "main_plot"
            ):
                meta = dict(bm.metadata_json or {})
                meta["structure_beat"] = struct_beat.beat_name
                meta["structure_beat_description"] = struct_beat.description
                bm.metadata_json = meta
                break

    clue_specs, payoff_specs = _build_clues_and_payoffs(
        arc_ids=arc_ids,
        volumes=volumes,
        chapters=chapters,
        scenes_by_chapter=scenes_by_chapter,
        volume_entries=volume_entries,
    )
    clues_by_code: dict[str, ClueModel] = {}
    for spec in clue_specs:
        clue = ClueModel(
            project_id=project.id,
            plot_arc_id=spec.get("plot_arc_id"),
            clue_code=spec["clue_code"],
            label=spec["label"],
            clue_type=spec["clue_type"],
            description=spec["description"],
            planted_in_volume_number=spec.get("planted_in_volume_number"),
            planted_in_chapter_number=spec.get("planted_in_chapter_number"),
            planted_in_scene_number=spec.get("planted_in_scene_number"),
            expected_payoff_by_volume_number=spec.get("expected_payoff_by_volume_number"),
            expected_payoff_by_chapter_number=spec.get("expected_payoff_by_chapter_number"),
            expected_payoff_by_scene_number=spec.get("expected_payoff_by_scene_number"),
            actual_paid_off_chapter_number=spec.get("actual_paid_off_chapter_number"),
            actual_paid_off_scene_number=spec.get("actual_paid_off_scene_number"),
            reveal_guard=spec.get("reveal_guard"),
            status=spec["status"],
            metadata_json={},
        )
        session.add(clue)
        clues_by_code[spec["clue_code"]] = clue
    await session.flush()

    payoffs_by_code: dict[str, PayoffModel] = {}
    for spec in payoff_specs:
        payoff = PayoffModel(
            project_id=project.id,
            plot_arc_id=spec.get("plot_arc_id"),
            source_clue_id=clues_by_code[spec["source_clue_code"]].id
            if spec.get("source_clue_code") in clues_by_code
            else None,
            payoff_code=spec["payoff_code"],
            label=spec["label"],
            description=spec["description"],
            target_volume_number=spec.get("target_volume_number"),
            target_chapter_number=spec.get("target_chapter_number"),
            target_scene_number=spec.get("target_scene_number"),
            actual_chapter_number=spec.get("actual_chapter_number"),
            actual_scene_number=spec.get("actual_scene_number"),
            status=spec["status"],
            metadata_json={},
        )
        session.add(payoff)
        payoffs_by_code[spec["payoff_code"]] = payoff
    await session.flush()

    beats_by_chapter: dict[int, list[ArcBeatModel]] = defaultdict(list)
    beats_by_scene: dict[tuple[int, int], list[ArcBeatModel]] = defaultdict(list)
    for beat in beat_models:
        if beat.scope_chapter_number is not None:
            beats_by_chapter[beat.scope_chapter_number].append(beat)
        if beat.scope_chapter_number is not None and beat.scope_scene_number is not None:
            beats_by_scene[(beat.scope_chapter_number, beat.scope_scene_number)].append(beat)

    for chapter in chapters:
        chapter_beats = beats_by_chapter.get(chapter.chapter_number, [])
        primary_arc_codes = _unique_preserve(
            [str(beat.metadata_json.get("arc_code")) for beat in chapter_beats if beat.scope_level in {"chapter", "scene"}]
        )[:3]
        supporting_arc_codes = _unique_preserve(
            [str(beat.metadata_json.get("arc_code")) for beat in chapter_beats if beat.scope_level == "volume"]
        )[:3]
        planted_clue_codes = [
            clue.clue_code
            for clue in clues_by_code.values()
            if clue.planted_in_chapter_number == chapter.chapter_number
        ]
        due_payoff_codes = [
            payoff.payoff_code
            for payoff in payoffs_by_code.values()
            if payoff.target_chapter_number == chapter.chapter_number
        ]
        contract = ChapterContractModel(
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            contract_summary=_ensure_text(
                chapter.chapter_goal,
                f"第{chapter.chapter_number}章需要承担明确的叙事推进任务。",
            ),
            opening_state={"opening_situation": chapter.opening_situation} if chapter.opening_situation else {},
            core_conflict=chapter.main_conflict,
            emotional_shift=chapter.chapter_emotion_arc,
            information_release=chapter.hook_description or "本章需要释放新的信息增量。",
            closing_hook=chapter.hook_description,
            primary_arc_codes=primary_arc_codes,
            supporting_arc_codes=supporting_arc_codes,
            active_arc_beat_ids=[str(beat.id) for beat in chapter_beats if beat.scope_level == "chapter"],
            planted_clue_codes=planted_clue_codes,
            due_payoff_codes=due_payoff_codes,
            metadata_json={},
        )
        session.add(contract)

        chapter_scenes = scenes_by_chapter.get(chapter.id, [])
        last_scene_number = chapter_scenes[-1].scene_number if chapter_scenes else None
        for scene in chapter_scenes:
            scene_beats = beats_by_scene.get((chapter.chapter_number, scene.scene_number), [])
            scene_clues = [
                clue.clue_code
                for clue in clues_by_code.values()
                if clue.planted_in_chapter_number == chapter.chapter_number
                and (
                    clue.planted_in_scene_number == scene.scene_number
                    or (clue.planted_in_scene_number is None and scene.scene_number == 1)
                )
            ]
            scene_payoffs = [
                payoff.payoff_code
                for payoff in payoffs_by_code.values()
                if payoff.target_chapter_number == chapter.chapter_number
                and (
                    payoff.target_scene_number == scene.scene_number
                    or (payoff.target_scene_number is None and scene.scene_number == last_scene_number)
                )
            ]
            session.add(
                SceneContractModel(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    scene_card_id=scene.id,
                    chapter_number=chapter.chapter_number,
                    scene_number=scene.scene_number,
                    contract_summary=_ensure_text(
                        scene.purpose.get("story"),
                        f"第{chapter.chapter_number}章第{scene.scene_number}场需要有效推进当前剧情。",
                    ),
                    entry_state=dict(scene.entry_state),
                    exit_state=dict(scene.exit_state),
                    core_conflict=_ensure_text(
                        # Prefer chapter-level main_conflict (unique per chapter),
                        # then scene-level conflict metadata, then purpose.conflict.
                        # Only fall back to purpose.story as last resort.
                        chapter.main_conflict
                        or scene.purpose.get("conflict")
                        or (scene.metadata_json or {}).get("core_conflict"),
                        scene.purpose.get("story") or "场景中必须发生有效碰撞。",
                    ),
                    emotional_shift=_ensure_text(
                        scene.purpose.get("emotion"),
                        chapter.chapter_emotion_arc or "情绪需要发生变化。",
                    ),
                    information_release=scene.title or chapter.hook_description,
                    tail_hook=scene.hook_requirement or (chapter.hook_description if scene.scene_number == last_scene_number else None),
                    arc_codes=_unique_preserve(
                        [str(beat.metadata_json.get("arc_code")) for beat in scene_beats] + primary_arc_codes[:1]
                    ),
                    arc_beat_ids=[str(beat.id) for beat in scene_beats],
                    planted_clue_codes=scene_clues,
                    payoff_codes=scene_payoffs,
                    thematic_task=scene.purpose.get("theme") or (chapter.metadata_json or {}).get("thematic_task"),
                    dramatic_irony_intent=(
                        scene_clues[0] + "（读者已知但角色尚不知情）"
                        if scene_clues and any(
                            clues_by_code.get(c) and clues_by_code[c].clue_type in ("hidden", "dramatic_irony")
                            for c in scene_clues
                        )
                        else None
                    ),
                    transition_type=(
                        "time_skip" if scene.scene_type in ("montage", "aftermath")
                        else "flashback" if scene.scene_type == "flashback"
                        else "parallel_crosscut" if scene.scene_type == "parallel"
                        else "hard_cut" if scene.scene_number > 1
                        else None
                    ),
                    subplot_codes=_unique_preserve([
                        str(beat.metadata_json.get("arc_code"))
                        for beat in scene_beats
                        if str(beat.metadata_json.get("arc_code")) not in (set(primary_arc_codes[:1]) | {"main_plot"})
                    ])[:5],
                    metadata_json={},
                )
        )

    emotion_track_specs = _build_emotion_track_specs(
        protagonist=protagonist,
        antagonist=antagonist,
        relationships=relationships,
        characters_by_id=characters_by_id,
    )
    emotion_track_specs = _dedupe_emotion_track_specs(emotion_track_specs)
    emotion_track_models: list[EmotionTrackModel] = []
    for spec in emotion_track_specs:
        emotion_track = EmotionTrackModel(
            project_id=project.id,
            track_code=spec["track_code"],
            track_type=spec["track_type"],
            title=spec["title"],
            character_a_id=spec.get("character_a_id"),
            character_b_id=spec.get("character_b_id"),
            character_a_label=spec["character_a_label"],
            character_b_label=spec["character_b_label"],
            relationship_type=spec.get("relationship_type"),
            summary=spec["summary"],
            desired_payoff=spec.get("desired_payoff"),
            trust_level=spec["trust_level"],
            attraction_level=spec["attraction_level"],
            distance_level=spec["distance_level"],
            conflict_level=spec["conflict_level"],
            intimacy_stage=spec["intimacy_stage"],
            last_shift_chapter_number=spec.get("last_shift_chapter_number"),
            status=spec["status"],
            metadata_json=spec.get("metadata_json") or {},
        )
        session.add(emotion_track)
        emotion_track_models.append(emotion_track)

    antagonist_plan_specs = _build_antagonist_plan_specs(
        protagonist=protagonist,
        antagonist=antagonist,
        all_antagonists=all_antagonists,
        volumes=volumes,
        chapters_by_volume=chapters_by_volume,
        volume_entries=volume_entries,
    )
    antagonist_plan_models: list[AntagonistPlanModel] = []
    for spec in antagonist_plan_specs:
        # Use per-plan label from metadata if available (for multi-force plans).
        # Also resolve the per-plan antagonist character_id from metadata so each
        # volume's plan points at the correct per-volume antagonist, not always
        # at the primary — this was the routing collapse that made every xianxia
        # volume collapse onto a single antagonist label.
        plan_meta = spec.get("metadata_json") or {}
        plan_antag_label = plan_meta.get("antagonist_label", antagonist.name if antagonist is not None else "未知反派")
        plan_char_id_raw = plan_meta.get("antagonist_character_id")
        plan_char_id: UUID | None = None
        if plan_char_id_raw:
            try:
                plan_char_id = UUID(str(plan_char_id_raw))
            except (ValueError, TypeError):
                plan_char_id = None
        if plan_char_id is None and antagonist is not None:
            plan_char_id = antagonist.id
        antagonist_plan = AntagonistPlanModel(
            project_id=project.id,
            antagonist_character_id=plan_char_id,
            antagonist_label=plan_antag_label,
            plan_code=spec["plan_code"],
            title=spec["title"],
            threat_type=spec["threat_type"],
            goal=spec["goal"],
            current_move=spec["current_move"],
            next_countermove=spec["next_countermove"],
            escalation_condition=spec.get("escalation_condition"),
            reveal_timing=spec.get("reveal_timing"),
            scope_volume_number=spec.get("scope_volume_number"),
            target_chapter_number=spec.get("target_chapter_number"),
            pressure_level=spec["pressure_level"],
            status=spec["status"],
            metadata_json=spec.get("metadata_json") or {},
        )
        session.add(antagonist_plan)
        antagonist_plan_models.append(antagonist_plan)

    await session.flush()

    # ── Theme Arcs ──
    theme_arc_specs = _build_theme_arc_specs(
        project,
        volumes=volumes,
        volume_entries=volume_entries,
    )
    theme_arcs_by_code: dict[str, ThemeArcModel] = {}
    for spec in theme_arc_specs:
        theme_arc = ThemeArcModel(
            project_id=project.id,
            theme_code=spec["theme_code"],
            theme_statement=spec["theme_statement"],
            symbol_set=spec["symbol_set"],
            evolution_stages=spec["evolution_stages"],
            current_stage=spec["current_stage"],
            status=spec["status"],
            metadata_json={},
        )
        session.add(theme_arc)
        theme_arcs_by_code[spec["theme_code"]] = theme_arc
    await session.flush()

    # ── Motif Placements ──
    motif_specs = _build_motif_placement_specs(
        theme_arcs_by_code=theme_arcs_by_code,
        chapters=chapters,
        chapters_by_volume=chapters_by_volume,
        volume_entries=volume_entries,
    )
    motif_models: list[MotifPlacementModel] = []
    for spec in motif_specs:
        motif = MotifPlacementModel(
            project_id=project.id,
            theme_arc_id=spec["theme_arc_id"],
            motif_label=spec["motif_label"],
            placement_type=spec["placement_type"],
            volume_number=spec.get("volume_number"),
            chapter_number=spec.get("chapter_number"),
            scene_number=spec.get("scene_number"),
            description=spec.get("description"),
            status=spec["status"],
            metadata_json={},
        )
        session.add(motif)
        motif_models.append(motif)

    # ── Subplot Schedule ──
    subplot_specs = _build_subplot_schedule_specs(
        arcs_by_code=arcs_by_code,
        chapters=chapters,
        beats_by_chapter=beats_by_chapter,
        chapters_by_volume=chapters_by_volume,
    )
    subplot_models: list[SubplotScheduleModel] = []
    for spec in subplot_specs:
        subplot_entry = SubplotScheduleModel(
            project_id=project.id,
            plot_arc_id=spec["plot_arc_id"],
            arc_code=spec["arc_code"],
            chapter_number=spec["chapter_number"],
            prominence=spec["prominence"],
            notes=spec.get("notes"),
            metadata_json={},
        )
        session.add(subplot_entry)
        subplot_models.append(subplot_entry)

    # ── Relationship Events ──
    rel_event_specs = _build_relationship_event_specs(
        relationships=relationships,
        characters_by_id=characters_by_id,
        chapters=chapters,
    )
    rel_event_models: list[RelationshipEventModel] = []
    for spec in rel_event_specs:
        rel_event = RelationshipEventModel(
            project_id=project.id,
            character_a_label=spec["character_a_label"],
            character_b_label=spec["character_b_label"],
            chapter_number=spec["chapter_number"],
            scene_number=spec.get("scene_number"),
            event_description=spec["event_description"],
            relationship_change=spec["relationship_change"],
            is_milestone=spec.get("is_milestone", False),
            metadata_json={},
        )
        session.add(rel_event)
        rel_event_models.append(rel_event)

    # ── Reader Knowledge Entries ──
    reader_knowledge_specs = _build_reader_knowledge_specs(
        clues_by_code=clues_by_code,
        payoffs_by_code=payoffs_by_code,
    )
    reader_knowledge_models: list[ReaderKnowledgeEntryModel] = []
    for spec in reader_knowledge_specs:
        rk = ReaderKnowledgeEntryModel(
            project_id=project.id,
            chapter_number=spec["chapter_number"],
            knowledge_item=spec["knowledge_item"],
            audience=spec["audience"],
            source_clue_code=spec.get("source_clue_code"),
            metadata_json={},
        )
        session.add(rk)
        reader_knowledge_models.append(rk)

    # ── Ending Contract ──
    theme_arc_list = list(theme_arcs_by_code.values())
    ending_spec = _build_ending_contract_spec(
        arcs_by_code=arcs_by_code,
        clues_by_code=clues_by_code,
        emotion_track_models=emotion_track_models,
        theme_arcs=theme_arc_list,
    )
    ending_contract = EndingContractModel(
        project_id=project.id,
        arcs_to_resolve=ending_spec["arcs_to_resolve"],
        clues_to_payoff=ending_spec["clues_to_payoff"],
        relationships_to_close=ending_spec["relationships_to_close"],
        thematic_final_expression=ending_spec.get("thematic_final_expression"),
        denouement_plan=ending_spec.get("denouement_plan"),
        status=ending_spec["status"],
        metadata_json={},
    )
    session.add(ending_contract)

    # ── Pacing Curve Points (template-aware) ──
    _structure_key = getattr(project, "metadata_json", {}).get("structure_template") or "three-act"
    _structure_template = resolve_structure_template(_structure_key)
    pacing_specs = _build_pacing_curve_specs(
        chapters=chapters,
        scenes_by_chapter=scenes_by_chapter,
        structure_template=_structure_template,
    )
    pacing_models: list[PacingCurvePointModel] = []
    for spec in pacing_specs:
        pacing_point = PacingCurvePointModel(
            project_id=project.id,
            chapter_number=spec["chapter_number"],
            tension_level=spec["tension_level"],
            scene_type_plan=spec.get("scene_type_plan"),
            notes=spec.get("notes"),
            metadata_json={},
        )
        session.add(pacing_point)
        pacing_models.append(pacing_point)

    await session.flush()
    return {
        "plot_arc_count": len(arcs_by_code),
        "arc_beat_count": len(beat_models),
        "clue_count": len(clues_by_code),
        "payoff_count": len(payoffs_by_code),
        "chapter_contract_count": len(chapters),
        "scene_contract_count": len(scenes),
        "emotion_track_count": len(emotion_track_models),
        "antagonist_plan_count": len(antagonist_plan_models),
        "theme_arc_count": len(theme_arcs_by_code),
        "motif_placement_count": len(motif_models),
        "subplot_schedule_count": len(subplot_models),
        "relationship_event_count": len(rel_event_models),
        "reader_knowledge_count": len(reader_knowledge_models),
        "ending_contract_count": 1,
        "pacing_curve_point_count": len(pacing_models),
    }


async def build_narrative_overview(
    session: AsyncSession,
    project_slug: str,
) -> NarrativeOverview:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    arcs = list(
        await session.scalars(
            select(PlotArcModel)
            .where(PlotArcModel.project_id == project.id)
            .order_by(PlotArcModel.arc_type.asc(), PlotArcModel.arc_code.asc())
        )
    )
    arc_code_by_id = {arc.id: arc.arc_code for arc in arcs}
    arc_beats = list(
        await session.scalars(
            select(ArcBeatModel)
            .where(ArcBeatModel.project_id == project.id)
            .order_by(
                ArcBeatModel.scope_chapter_number.asc().nullsfirst(),
                ArcBeatModel.scope_scene_number.asc().nullsfirst(),
                ArcBeatModel.beat_order.asc(),
            )
        )
    )
    clues = list(
        await session.scalars(
            select(ClueModel)
            .where(ClueModel.project_id == project.id)
            .order_by(ClueModel.planted_in_chapter_number.asc().nullsfirst(), ClueModel.clue_code.asc())
        )
    )
    payoffs = list(
        await session.scalars(
            select(PayoffModel)
            .where(PayoffModel.project_id == project.id)
            .order_by(PayoffModel.target_chapter_number.asc().nullsfirst(), PayoffModel.payoff_code.asc())
        )
    )
    chapter_contracts = list(
        await session.scalars(
            select(ChapterContractModel)
            .where(ChapterContractModel.project_id == project.id)
            .order_by(ChapterContractModel.chapter_number.asc())
        )
    )
    scene_contracts = list(
        await session.scalars(
            select(SceneContractModel)
            .where(SceneContractModel.project_id == project.id)
            .order_by(SceneContractModel.chapter_number.asc(), SceneContractModel.scene_number.asc())
        )
    )
    emotion_tracks = list(
        await session.scalars(
            select(EmotionTrackModel)
            .where(EmotionTrackModel.project_id == project.id)
            .order_by(EmotionTrackModel.track_type.asc(), EmotionTrackModel.track_code.asc())
        )
    )
    antagonist_plans = list(
        await session.scalars(
            select(AntagonistPlanModel)
            .where(AntagonistPlanModel.project_id == project.id)
            .order_by(
                AntagonistPlanModel.scope_volume_number.asc().nullsfirst(),
                AntagonistPlanModel.target_chapter_number.asc().nullsfirst(),
                AntagonistPlanModel.plan_code.asc(),
            )
        )
    )
    theme_arcs = list(
        await session.scalars(
            select(ThemeArcModel)
            .where(ThemeArcModel.project_id == project.id)
            .order_by(ThemeArcModel.theme_code.asc())
        )
    )
    motif_placements = list(
        await session.scalars(
            select(MotifPlacementModel)
            .where(MotifPlacementModel.project_id == project.id)
            .order_by(MotifPlacementModel.chapter_number.asc().nullsfirst())
        )
    )
    subplot_schedule = list(
        await session.scalars(
            select(SubplotScheduleModel)
            .where(SubplotScheduleModel.project_id == project.id)
            .order_by(SubplotScheduleModel.chapter_number.asc(), SubplotScheduleModel.arc_code.asc())
        )
    )
    relationship_events = list(
        await session.scalars(
            select(RelationshipEventModel)
            .where(RelationshipEventModel.project_id == project.id)
            .order_by(RelationshipEventModel.chapter_number.asc())
        )
    )
    reader_knowledge = list(
        await session.scalars(
            select(ReaderKnowledgeEntryModel)
            .where(ReaderKnowledgeEntryModel.project_id == project.id)
            .order_by(ReaderKnowledgeEntryModel.chapter_number.asc())
        )
    )
    ending_contract_rows = list(
        await session.scalars(
            select(EndingContractModel)
            .where(EndingContractModel.project_id == project.id)
        )
    )
    ending_contract_row = ending_contract_rows[0] if ending_contract_rows else None
    pacing_curve = list(
        await session.scalars(
            select(PacingCurvePointModel)
            .where(PacingCurvePointModel.project_id == project.id)
            .order_by(PacingCurvePointModel.chapter_number.asc())
        )
    )

    return NarrativeOverview(
        project_id=project.id,
        project_slug=project.slug,
        title=project.title,
        plot_arcs=[
            PlotArcRead(
                id=item.id,
                arc_code=item.arc_code,
                name=item.name,
                arc_type=item.arc_type,
                promise=item.promise,
                core_question=item.core_question,
                target_payoff=item.target_payoff,
                status=item.status,
                scope_level=item.scope_level,
                scope_volume_number=item.scope_volume_number,
                scope_chapter_number=item.scope_chapter_number,
                description=item.description,
            )
            for item in arcs
        ],
        arc_beats=[
            ArcBeatRead(
                id=item.id,
                plot_arc_id=item.plot_arc_id,
                arc_code=arc_code_by_id.get(item.plot_arc_id, str(item.metadata_json.get("arc_code", "unknown"))),
                beat_order=item.beat_order,
                scope_level=item.scope_level,
                scope_volume_number=item.scope_volume_number,
                scope_chapter_number=item.scope_chapter_number,
                scope_scene_number=item.scope_scene_number,
                beat_kind=item.beat_kind,
                title=item.title,
                summary=item.summary,
                emotional_shift=item.emotional_shift,
                information_release=item.information_release,
                expected_payoff=item.expected_payoff,
                status=item.status,
            )
            for item in arc_beats
        ],
        clues=[
            ClueRead(
                id=item.id,
                clue_code=item.clue_code,
                label=item.label,
                clue_type=item.clue_type,
                description=item.description,
                plot_arc_id=item.plot_arc_id,
                planted_in_volume_number=item.planted_in_volume_number,
                planted_in_chapter_number=item.planted_in_chapter_number,
                planted_in_scene_number=item.planted_in_scene_number,
                expected_payoff_by_volume_number=item.expected_payoff_by_volume_number,
                expected_payoff_by_chapter_number=item.expected_payoff_by_chapter_number,
                expected_payoff_by_scene_number=item.expected_payoff_by_scene_number,
                actual_paid_off_chapter_number=item.actual_paid_off_chapter_number,
                actual_paid_off_scene_number=item.actual_paid_off_scene_number,
                reveal_guard=item.reveal_guard,
                status=item.status,
            )
            for item in clues
        ],
        payoffs=[
            PayoffRead(
                id=item.id,
                payoff_code=item.payoff_code,
                label=item.label,
                description=item.description,
                plot_arc_id=item.plot_arc_id,
                source_clue_id=item.source_clue_id,
                target_volume_number=item.target_volume_number,
                target_chapter_number=item.target_chapter_number,
                target_scene_number=item.target_scene_number,
                actual_chapter_number=item.actual_chapter_number,
                actual_scene_number=item.actual_scene_number,
                status=item.status,
            )
            for item in payoffs
        ],
        chapter_contracts=[
            ChapterContractRead(
                id=item.id,
                chapter_id=item.chapter_id,
                chapter_number=item.chapter_number,
                contract_summary=item.contract_summary,
                opening_state=dict(item.opening_state),
                core_conflict=item.core_conflict,
                emotional_shift=item.emotional_shift,
                information_release=item.information_release,
                closing_hook=item.closing_hook,
                primary_arc_codes=list(item.primary_arc_codes),
                supporting_arc_codes=list(item.supporting_arc_codes),
                active_arc_beat_ids=[str(beat_id) for beat_id in item.active_arc_beat_ids],
                planted_clue_codes=list(item.planted_clue_codes),
                due_payoff_codes=list(item.due_payoff_codes),
            )
            for item in chapter_contracts
        ],
        scene_contracts=[
            SceneContractRead(
                id=item.id,
                chapter_id=item.chapter_id,
                scene_card_id=item.scene_card_id,
                chapter_number=item.chapter_number,
                scene_number=item.scene_number,
                contract_summary=item.contract_summary,
                entry_state=dict(item.entry_state),
                exit_state=dict(item.exit_state),
                core_conflict=item.core_conflict,
                emotional_shift=item.emotional_shift,
                information_release=item.information_release,
                tail_hook=item.tail_hook,
                arc_codes=list(item.arc_codes),
                arc_beat_ids=[str(beat_id) for beat_id in item.arc_beat_ids],
                planted_clue_codes=list(item.planted_clue_codes),
                payoff_codes=list(item.payoff_codes),
                thematic_task=item.thematic_task,
                dramatic_irony_intent=item.dramatic_irony_intent,
                transition_type=item.transition_type,
                subplot_codes=list(item.subplot_codes) if item.subplot_codes else [],
            )
            for item in scene_contracts
        ],
        emotion_tracks=[
            EmotionTrackRead(
                id=item.id,
                track_code=item.track_code,
                track_type=item.track_type,
                title=item.title,
                character_a_label=item.character_a_label,
                character_b_label=item.character_b_label,
                relationship_type=item.relationship_type,
                summary=item.summary,
                desired_payoff=item.desired_payoff,
                trust_level=float(item.trust_level),
                attraction_level=float(item.attraction_level),
                distance_level=float(item.distance_level),
                conflict_level=float(item.conflict_level),
                intimacy_stage=item.intimacy_stage,
                last_shift_chapter_number=item.last_shift_chapter_number,
                status=item.status,
            )
            for item in emotion_tracks
        ],
        antagonist_plans=[
            AntagonistPlanRead(
                id=item.id,
                plan_code=item.plan_code,
                antagonist_character_id=item.antagonist_character_id,
                antagonist_label=item.antagonist_label,
                title=item.title,
                threat_type=item.threat_type,
                goal=item.goal,
                current_move=item.current_move,
                next_countermove=item.next_countermove,
                escalation_condition=item.escalation_condition,
                reveal_timing=item.reveal_timing,
                scope_volume_number=item.scope_volume_number,
                target_chapter_number=item.target_chapter_number,
                pressure_level=float(item.pressure_level),
                status=item.status,
            )
            for item in antagonist_plans
        ],
        theme_arcs=[
            ThemeArcRead(
                id=item.id,
                theme_code=item.theme_code,
                theme_statement=item.theme_statement,
                symbol_set=list(item.symbol_set),
                evolution_stages=list(item.evolution_stages),
                current_stage=item.current_stage,
                status=item.status,
            )
            for item in theme_arcs
        ],
        motif_placements=[
            MotifPlacementRead(
                id=item.id,
                theme_arc_id=item.theme_arc_id,
                motif_label=item.motif_label,
                placement_type=item.placement_type,
                volume_number=item.volume_number,
                chapter_number=item.chapter_number,
                scene_number=item.scene_number,
                description=item.description,
                status=item.status,
            )
            for item in motif_placements
        ],
        subplot_schedule=[
            SubplotScheduleEntryRead(
                id=item.id,
                plot_arc_id=item.plot_arc_id,
                arc_code=item.arc_code,
                chapter_number=item.chapter_number,
                prominence=item.prominence,
                notes=item.notes,
            )
            for item in subplot_schedule
        ],
        relationship_events=[
            RelationshipEventRead(
                id=item.id,
                character_a_label=item.character_a_label,
                character_b_label=item.character_b_label,
                chapter_number=item.chapter_number,
                scene_number=item.scene_number,
                event_description=item.event_description,
                relationship_change=item.relationship_change,
                is_milestone=item.is_milestone,
            )
            for item in relationship_events
        ],
        reader_knowledge=[
            ReaderKnowledgeEntryRead(
                id=item.id,
                chapter_number=item.chapter_number,
                knowledge_item=item.knowledge_item,
                audience=item.audience,
                source_clue_code=item.source_clue_code,
            )
            for item in reader_knowledge
        ],
        ending_contract=EndingContractRead(
            id=ending_contract_row.id,
            arcs_to_resolve=list(ending_contract_row.arcs_to_resolve),
            clues_to_payoff=list(ending_contract_row.clues_to_payoff),
            relationships_to_close=list(ending_contract_row.relationships_to_close),
            thematic_final_expression=ending_contract_row.thematic_final_expression,
            denouement_plan=ending_contract_row.denouement_plan,
            status=ending_contract_row.status,
        ) if ending_contract_row is not None else None,
        pacing_curve=[
            PacingCurvePointRead(
                chapter_number=item.chapter_number,
                tension_level=float(item.tension_level),
                scene_type_plan=item.scene_type_plan,
                notes=item.notes,
            )
            for item in pacing_curve
        ],
    )

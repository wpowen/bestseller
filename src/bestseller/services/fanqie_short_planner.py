"""番茄短故事规划：BeatSheet + 段级大纲。"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.fanqie_short import (
    FanqieShortBeat,
    FanqieShortBeatSheet,
    segment_target_words,
)
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.planner import _fallback_chapter_outline_batch, _mapping
from bestseller.services.projects import get_project_by_slug, import_planning_artifact
from bestseller.services.prompt_packs import render_prompt_pack_fragment, resolve_prompt_pack
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_BEAT_ROLES = ("hook", "rising", "rising", "midpoint", "crisis", "climax", "resolution")


def _default_contracts(
    *,
    segment_number: int,
    segment_count: int,
    unlock_segment: int,
    protagonist_name: str,
) -> dict[str, dict[str, Any]]:
    opening_contract: dict[str, Any] = {}
    unlock_contract: dict[str, Any] = {}
    closure_contract: dict[str, Any] = {}
    if segment_number == 1:
        opening_contract = {
            "first_50_words": "主角直接进入压迫现场；若有金手指/异能，必须在50字左右可见并参与当前冲突。",
            "first_100_words": f"{protagonist_name}必须成为视角焦点，并出现明确威胁、污名或损失。",
            "first_200_words": "完成第一次可见反馈：撤回、露馅、自爆、证据、反制或小打脸之一。",
            "first_300_words": "交代当前冲突、对手压迫、主角损失、不能退让的理由和第一次小爽点结果。",
            "first_800_words": "完成第一次动作反应、能力使用、代价提示和下一轮压力升级。",
        }
    if segment_number <= unlock_segment:
        unlock_contract = {
            "deadline": "前30%免费段截止前必须完成压迫-行动-小回报循环。",
            "required_payoff": "至少出现一次证据、反制、能力解锁、公开打脸或逃出生天。",
            "no_background_only": "不得只揭设定、查资料或铺世界观。",
        }
    if segment_number == segment_count:
        closure_contract = {
            "must_resolve": "收束本篇主线胜负和情绪落点。",
            "forbid": "禁止用下章揭晓、地下新场景、你欠我真相等连载式悬念结尾。",
            "tail_signal": "结尾必须让读者知道当前故事已经完成。",
        }
    return {
        "opening_contract": opening_contract,
        "unlock_contract": unlock_contract,
        "ability_cost_contract": {
            "source": "能力来源必须在剧情中可感知。",
            "limit": "能力不能无限解决问题。",
            "cost": "每次关键使用必须带来疼痛、暴露、损失、冷却或道德选择。",
            "plot_use": "能力既解决问题，也制造新的冲突。",
        },
        "payoff_contract": {
            "minimum": "本段至少兑现一个小回报、反转、压力升级或信息爆点；禁止连续两段只解释设定。",
            "visibility": "回报必须落在具体行动/证据/对话上，不能只靠心理总结。",
        },
        "closure_contract": closure_contract,
        "continuity_contract": {
            "protagonist_name": protagonist_name,
            "rule": "主角名、能力名、反派名和核心设定不得漂移。",
        },
    }


def _fallback_beat_sheet(project: ProjectModel, premise: str) -> FanqieShortBeatSheet:
    segment_count = max(project.target_chapters, 4)
    unlock_segment = max(2, int(segment_count * 0.30) + (1 if segment_count * 0.30 % 1 else 0))
    beats: list[FanqieShortBeat] = []
    protagonist_name = "我"
    for index in range(1, segment_count + 1):
        role = _BEAT_ROLES[min(index - 1, len(_BEAT_ROLES) - 1)]
        if index == 1:
            purpose = (
                f"开篇：{(premise or project.title)[:120]}——50字内把主角推入压迫现场，"
                "300字内亮出不可退让的当前冲突。"
            )
        elif index <= unlock_segment:
            purpose = f"前30%段：升级压迫并兑现第一次小反击/小爆点（第{index}段）。"
        elif index == segment_count:
            purpose = "收束主线胜负和情绪落点，禁止连载式悬念。"
        else:
            purpose = f"推进单线主线，兑现一个小回报或反转（第{index}段）。"
        contracts = _default_contracts(
            segment_number=index,
            segment_count=segment_count,
            unlock_segment=unlock_segment,
            protagonist_name=protagonist_name,
        )
        beats.append(
            FanqieShortBeat(
                segment_number=index,
                beat_role=role,
                purpose=purpose,
                payoff="读者可感的小回报或情绪转折",
                emotional_turn="压迫→反击→余波",
                **contracts,
            )
        )
    pov = "first_person"
    meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    if isinstance(meta.get("pov"), str):
        pov = meta["pov"]
    return FanqieShortBeatSheet(
        title=project.title,
        logline=premise[:500] if premise else project.title,
        pov=pov,
        beats=beats,
        unlock_milestone_segment=unlock_segment,
    )


async def generate_fanqie_beat_sheet(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    premise: str,
    *,
    requested_by: str = "system",
) -> FanqieShortBeatSheet:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' not found.")

    pack = resolve_prompt_pack(
        "fanqie_short",
        genre=project.genre,
        sub_genre=project.sub_genre,
    )
    fragment = render_prompt_pack_fragment(pack, "planner_beat_sheet") if pack else ""
    user_prompt = (
        f"书名：{project.title}\n"
        f"类型：{project.genre} / {project.sub_genre or ''}\n"
        f"梗概：{premise}\n"
        f"段数：{project.target_chapters}\n"
        f"目标全文：{project.target_word_count} 字\n"
        "榜单级硬要求：第1段前50字主角进入压迫现场，若有金手指/异能必须立刻可见并生效；"
        "前100字主角成为视角焦点并出现明确污名/威胁/损失；前200字必须给一次可见小反馈；"
        "前300字出现当前冲突/威胁/代价和第一次小爽点结果，前800字出现第一次动作反应或能力使用；"
        "前30%必须完成压迫-行动-小爆点；末段必须单篇完结，禁止连载式悬念；"
        "能力必须有来源、限制、代价、成长触发和剧情用途。\n"
        "请输出 JSON："
        '{"title","logline","pov","unlock_milestone_segment","beats":[{"segment_number",'
        '"beat_role","purpose","payoff","emotional_turn","opening_contract",'
        '"unlock_contract","ability_cost_contract","payoff_contract","closure_contract",'
        '"continuity_contract"}]}'
    )
    try:
        response = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=fragment or "你是番茄短故事策划，输出紧凑 beat sheet JSON。",
                user_prompt=user_prompt,
                fallback_response="",
                project_id=project.id,
                metadata={"task": "fanqie_beat_sheet"},
            ),
        )
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        payload = json.loads(raw)
        sheet = FanqieShortBeatSheet.model_validate(payload)
    except Exception:
        logger.warning("Fanqie beat sheet LLM failed; using fallback", exc_info=True)
        sheet = _fallback_beat_sheet(project, premise)

    await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.FANQIE_BEAT_SHEET,
            content=sheet.model_dump(mode="json"),
        ),
    )
    return sheet


def build_fanqie_segment_outline_batch(
    project: ProjectModel,
    beat_sheet: FanqieShortBeatSheet,
    *,
    book_spec: dict[str, Any] | None = None,
    cast_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """由 beat sheet 生成每段 1 scene 的大纲 batch。"""
    cast_payload = _mapping(cast_spec or {})
    protagonist = _mapping(cast_payload.get("protagonist"))
    protagonist_name = str(protagonist.get("name") or "我").strip() or "我"
    per_segment_words = segment_target_words(
        project.target_word_count, max(project.target_chapters, 1)
    )

    def _contract_lines(beat: FanqieShortBeat | None) -> list[str]:
        if beat is None:
            return []
        defaults = _default_contracts(
            segment_number=beat.segment_number,
            segment_count=max(project.target_chapters, 1),
            unlock_segment=beat_sheet.unlock_milestone_segment,
            protagonist_name=protagonist_name,
        )
        sections = [
            ("开篇合同", beat.opening_contract or defaults["opening_contract"]),
            ("30%解锁合同", beat.unlock_contract or defaults["unlock_contract"]),
            (
                "能力代价合同",
                beat.ability_cost_contract or defaults["ability_cost_contract"],
            ),
            ("爽点合同", beat.payoff_contract or defaults["payoff_contract"]),
            ("收束合同", beat.closure_contract or defaults["closure_contract"]),
            (
                "连续性合同",
                beat.continuity_contract or defaults["continuity_contract"],
            ),
        ]
        lines: list[str] = []
        for label, payload in sections:
            if not payload:
                continue
            compact = "；".join(f"{key}: {value}" for key, value in payload.items() if value)
            if compact:
                lines.append(f"{label}：{compact}")
        return lines

    def _goal_with_contract(beat: FanqieShortBeat | None, fallback: str) -> str:
        if beat is None:
            return fallback
        lines = [beat.purpose]
        lines.extend(_contract_lines(beat))
        return "\n".join(line for line in lines if line)

    volume_plan = [
        {
            "volume_number": 1,
            "chapter_count_target": project.target_chapters,
            "volume_goal": beat_sheet.logline or project.title,
        }
    ]
    if book_spec:
        batch = _fallback_chapter_outline_batch(
            project,
            book_spec,
            cast_spec or {"protagonist": {"name": protagonist_name}},
            volume_plan,
        )
        beat_by_number = {beat.segment_number: beat for beat in beat_sheet.beats}
        for index, chapter in enumerate(batch.get("chapters") or [], start=1):
            if isinstance(chapter, dict):
                segment_number = int(chapter.get("chapter_number") or index)
                beat = beat_by_number.get(segment_number)
                chapter["title"] = f"第{segment_number}段"
                chapter["target_word_count"] = per_segment_words
                if beat is not None:
                    chapter["chapter_goal"] = _goal_with_contract(beat, beat.purpose)
                    chapter["main_conflict"] = beat.beat_role
                scenes = chapter.get("scenes") or []
                if len(scenes) > 1:
                    chapter["scenes"] = [scenes[0]]
                    scenes = chapter["scenes"]
                if scenes and isinstance(scenes[0], dict):
                    scenes[0]["title"] = scenes[0].get("title") or f"第{segment_number}段"
                    scenes[0]["target_word_count"] = per_segment_words
                    if beat is not None:
                        purpose = dict(scenes[0].get("purpose") or {})
                        purpose["story"] = _goal_with_contract(beat, beat.purpose)
                        purpose["emotion"] = beat.emotional_turn or beat.payoff
                        scenes[0]["purpose"] = purpose
        return batch

    chapters: list[dict[str, Any]] = []
    for beat in beat_sheet.beats:
        chapters.append(
            {
                "chapter_number": beat.segment_number,
                "title": f"第{beat.segment_number}段",
                "chapter_goal": _goal_with_contract(beat, beat.purpose),
                "main_conflict": beat.beat_role,
                "target_word_count": per_segment_words,
                "volume_number": 1,
                "scenes": [
                    {
                        "scene_number": 1,
                        "scene_type": "conflict",
                        "title": beat.beat_role,
                        "participants": [protagonist_name],
                        "purpose": {
                            "story": _goal_with_contract(beat, beat.purpose),
                            "emotion": beat.emotional_turn or beat.payoff,
                        },
                        "target_word_count": per_segment_words,
                    }
                ],
            }
        )
    return {"batch_name": "fanqie-short-segments", "chapters": chapters}


async def persist_fanqie_chapter_outline(
    session: AsyncSession,
    project_slug: str,
    outline_payload: dict[str, Any],
) -> UUID:
    batch = ChapterOutlineBatchInput.model_validate(outline_payload)
    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
            content=batch.model_dump(mode="json"),
        ),
    )
    return artifact.id

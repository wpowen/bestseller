# ruff: noqa: ANN401, E501, RUF001, RUF002
"""番茄短故事规划：BeatSheet + 段级大纲。"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.fanqie_short import (
    FanqieShortBeat,
    FanqieShortBeatSheet,
    segment_target_words,
)
from bestseller.domain.ideology import (
    render_ideology_compact_block,
)
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services.fanqie_short_emotion_bank import (
    render_emotion_stack_prompt_block,
    select_emotion_stack,
)
from bestseller.services.fanqie_short_resource_adapter import (
    adapt_long_form_resources_for_short,
    render_short_resource_prompt_block,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.planner import _extract_json_payload, _mapping
from bestseller.services.projects import get_project_by_slug, import_planning_artifact
from bestseller.services.prompt_packs import render_prompt_pack_fragment, resolve_prompt_pack
from bestseller.services.story_enhancers import resolve_cost_style
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_BEAT_ROLES = ("hook", "rising", "rising", "midpoint", "crisis", "climax", "resolution")
_BEAT_CONTRACT_FIELDS = (
    "opening_contract",
    "unlock_contract",
    "ability_cost_contract",
    "payoff_contract",
    "closure_contract",
    "continuity_contract",
)
_CONTRACT_LABEL_PATTERN = re.compile(
    r"(开篇合同|30%解锁合同|能力代价合同|爽点合同|收束合同|连续性合同)[:：]"
)
_META_STORY_MARKERS = (
    "读者",
    "合同",
    "字内",
    "字左右",
    "金手指",
    "异能",
    "必须",
    "不得",
    "禁止",
    "爽点",
    "视角焦点",
    "章节末",
)


def _coerce_contract_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        return {"summary": text} if text else {}
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return {f"item_{index}": item for index, item in enumerate(items, start=1)}
    return {}


def _normalize_beat_sheet_payload(payload: Any) -> Any:
    """Accept useful LLM beat sheets that express contract fields as prose."""
    if not isinstance(payload, Mapping):
        return payload
    normalized = dict(payload)
    unlock_value = normalized.get("unlock_milestone_segment")
    if isinstance(unlock_value, str):
        match = re.search(r"\d+", unlock_value)
        if match:
            normalized["unlock_milestone_segment"] = int(match.group(0))
    beats = normalized.get("beats")
    if not isinstance(beats, list):
        return normalized
    normalized_beats: list[Any] = []
    for beat in beats:
        if not isinstance(beat, Mapping):
            normalized_beats.append(beat)
            continue
        normalized_beat = dict(beat)
        for field in _BEAT_CONTRACT_FIELDS:
            if field in normalized_beat:
                normalized_beat[field] = _coerce_contract_payload(normalized_beat[field])
        normalized_beats.append(normalized_beat)
    normalized["beats"] = normalized_beats
    return normalized


def _contract_summary(value: Any) -> str:
    payload = _coerce_contract_payload(value)
    for key in ("summary", "event", "action", "required_payoff", "must_resolve"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    for item in payload.values():
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _visible_story_text(value: Any, *, protagonist_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _CONTRACT_LABEL_PATTERN.split(text, maxsplit=1)[0]
    text = re.split(r"[—-]{1,2}\s*(第\d+段)?\d+字内", text, maxsplit=1)[0]
    text = re.sub(r"^(开篇|前30%段|中段|终段|单篇中段)[:：]\s*", "", text)
    text = re.sub(r"(，|,|；|;)?\s*(第\d+段)?前\d+字[^。；;]*", "", text)
    text = re.sub(r"(，|,|；|;)?\s*\d+字内[^。；;]*", "", text)
    text = re.sub(r"(，|,|；|;)?\s*\d+字左右[^。；;]*", "", text)
    lines: list[str] = []
    for raw_line in re.split(r"[\n\r]+", text):
        line = raw_line.strip(" ，,；;")
        if not line:
            continue
        if _CONTRACT_LABEL_PATTERN.search(line):
            line = _CONTRACT_LABEL_PATTERN.split(line, maxsplit=1)[0].strip(" ，,；;")
        if not line:
            continue
        if line.startswith(("first_", "deadline:", "required_payoff:", "no_background_only:")):
            continue
        if any(marker in line for marker in _META_STORY_MARKERS):
            continue
        lines.append(line)
    text = "；".join(lines)
    text = text.replace("完成情绪反杀", "公开反击并改变胜负")
    text = text.replace("催泪点兑现，", "")
    text = text.replace("本篇单篇完结", "当前案件收束")
    text = text.replace("主角", protagonist_name)
    if protagonist_name:
        text = re.sub(
            rf"({re.escape(protagonist_name)})(?:\s*{re.escape(protagonist_name)})+",
            protagonist_name,
            text,
        )
    text = re.sub(r"\s+", " ", text).strip(" ，,；;")
    if any(marker in text for marker in _META_STORY_MARKERS):
        return ""
    return text


def _visible_contract_text(value: Any, *, protagonist_name: str) -> str:
    text = _contract_summary(value)
    if not text:
        return ""
    visible = _visible_story_text(text, protagonist_name=protagonist_name)
    if visible:
        return visible
    text = re.sub(
        r"^(开篇合同|30%解锁合同|能力代价合同|爽点合同|收束合同|连续性合同)[:：]\s*",
        "",
        text,
    )
    text = re.sub(r"^(第\d+段)?前\d+字(内|左右|必须|出现|完成|给出)?", "", text)
    text = re.sub(r"^(第\d+段)?前\d+字[^:：]*[:：]\s*", "", text)
    text = re.sub(r"^前\d+%[^:：]*[:：]\s*", "", text)
    text = re.sub(
        r"^(前\d+字动作反应|隐藏规则揭示|单篇完结爽点|本segment结束时|本段结尾|第\d+段结尾)[:：]\s*",
        "",
        text,
    )
    text = re.sub(r"[，,；;]?\s*(前)?\d+字内[^。；;]*", "", text)
    text = re.sub(r"[，,；;]?\s*(第)?\d+段[^。；;]*(钩子|铺垫)[^。；;]*", "", text)
    text = re.sub(r"[，,；;]?\s*为第[一二三四五六七八九十\d]+段[^。；;]*", "", text)
    text = re.sub(r"(小|核心|单篇完结)?爽点(兑现|结果|回报)?", "回报", text)
    text = text.replace("完成回报", "让局面第一次反转")
    text = text.replace("催泪点兑现，", "")
    text = text.replace("情绪反杀", "公开反击")
    text = text.replace("主角", protagonist_name)
    text = re.sub(r"\s+", " ", text).strip(" ，,；;")
    if any(marker in text for marker in _META_STORY_MARKERS):
        return ""
    return text


def _visible_goal_from_beat(beat: FanqieShortBeat, *, protagonist_name: str) -> str:
    purpose = _visible_story_text(beat.purpose, protagonist_name=protagonist_name)
    if purpose:
        return purpose
    pieces = [
        _visible_contract_text(beat.opening_contract, protagonist_name=protagonist_name),
        _visible_contract_text(beat.payoff_contract, protagonist_name=protagonist_name),
    ]
    visible = "；".join(piece for piece in pieces if piece)
    if visible:
        return visible
    fallback = beat.purpose.strip()
    fallback = fallback.replace("立刻建立压迫现场，将", "")
    fallback = fallback.replace("完成情绪反杀", "公开反击并改变胜负")
    fallback = fallback.replace("催泪点兑现，", "")
    fallback = fallback.replace("本篇单篇完结", "当前案件收束")
    fallback = fallback.replace("主角", protagonist_name)
    return fallback


def _hook_from_beat(
    beat: FanqieShortBeat,
    *,
    final_segment: bool,
    protagonist_name: str,
) -> str:
    if final_segment:
        return (
            _visible_contract_text(beat.closure_contract, protagonist_name=protagonist_name)
            or _visible_contract_text(beat.payoff_contract, protagonist_name=protagonist_name)
            or _visible_story_text(beat.payoff, protagonist_name=protagonist_name)
            or f"{protagonist_name}把关键证据摆到众人面前，当前案件的真相落定。"
        )
    return (
        _visible_contract_text(beat.closure_contract, protagonist_name=protagonist_name)
        or _visible_contract_text(beat.payoff_contract, protagonist_name=protagonist_name)
        or _visible_story_text(beat.payoff, protagonist_name=protagonist_name)
        or f"新的具体证据落到{protagonist_name}手里，逼{protagonist_name}立刻做出下一步选择。"
    )


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
            payoff = "主角当场拿到第一枚可见证据。"
            emotional_turn = "被污名压迫→能力显形→争取第一口气"
        elif index <= unlock_segment:
            purpose = (
                f"前30%段：让主角在被怀疑和追责中当场反制，"
                f"拆出当前案件的第一条证据链（第{index}段）。"
            )
            payoff = "公开或半公开地证明对手说法有漏洞，完成第一次反击。"
            emotional_turn = "孤立无援→抓住证据→压力升级"
        elif index == segment_count:
            purpose = (
                "终段：主角付出代价逼出关键证据，揪出真正设局者，"
                "收束当前案件和主角身份认同。"
            )
            payoff = "真相公开，主角洗清污名并完成单篇情绪落点。"
            emotional_turn = "濒临失控→真相公开→温柔收束"
        else:
            mid_beats = {
                3: (
                    "越过对手设下的禁令潜入关键现场，主角用三处细节拼出伪造证据的路线。",
                    "现场证据反咬设局者，主角从被告转为追查者。",
                    "被动逃避→主动破局→发现更深黑手",
                ),
                4: (
                    "主角追到旧案现场，发现关键证人沉默是在保护一段被替换的记忆。",
                    "证人给出半句真名，盟友立场出现反转。",
                    "误解加深→真相刺痛→同盟松动",
                ),
                5: (
                    "公开对质中反派反扣罪名，主角用付出代价换来的证据完成反击。",
                    "众人听见证词，设局者第一次失控露馅。",
                    "众怒压顶→证词爆发→胜负翻盘",
                ),
            }
            purpose, payoff, emotional_turn = mid_beats.get(
                index,
                (
                    f"单篇中段：围绕当前案件推进一条独有证据链，并兑现第{index}段反转。",
                    "新的证据或盟友选择改变局面。",
                    "压迫延伸→信息爆点→下一步行动",
                ),
            )
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
                payoff=payoff,
                emotional_turn=emotional_turn,
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


def build_short_ideology_compact(
    kernel: Any, *, metadata: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Project current author fields; a stored short compact is not authority."""

    from bestseller.services.ideology_methodology import resolve_ideology_methodology_source

    if kernel is None:
        return {}
    source = resolve_ideology_methodology_source(kernel, metadata)
    fields = source["effective_fields"]
    if not fields and not resolve_cost_style(metadata):
        return {}
    arc = _mapping(fields.get("belief_arc"))
    return {
        "author_fields": fields,
        "methodology_source": source,
        "thesis_statement": fields.get("thesis_statement", ""),
        "core_question": fields.get("core_question", ""),
        "belief_arc": {"initial": arc.get("initial_belief", ""), "shatter": arc.get("midpoint_shatter", ""), "reconstruction": arc.get("final_reconstruction", "")},
    }


def derive_short_thesis_vectors(
    segment_count: int,
    unlock_segment: int,
    ideology_compact: Mapping[str, Any] | None,
) -> dict[int, str]:
    """Map a belief arc (建立→受压→碎裂→代价→重建) onto each segment.

    Gives a short story an explicit value spine so every segment makes a
    thematic move instead of only delivering platform 爽点. Deterministic.
    """

    if not ideology_compact or not _mapping(ideology_compact.get("author_fields")).get("belief_arc"):
        return {}
    arc = _mapping(ideology_compact.get("belief_arc"))
    initial = str(arc.get("initial") or "").strip()
    shatter = str(arc.get("shatter") or "").strip()
    reconstruction = str(arc.get("reconstruction") or "").strip()
    if not (initial or shatter or reconstruction):
        return {}
    n = max(int(segment_count), 1)
    midpoint = min(n, max(2, round(n / 2)))
    vectors: dict[int, str] = {}
    for seg in range(1, n + 1):
        if seg == 1:
            vectors[seg] = f"建立初始信念：{initial}" if initial else "建立初始信念"
        elif seg == n:
            vectors[seg] = (
                f"重建新信念：{reconstruction}" if reconstruction else "重建新信念"
            )
        elif seg == midpoint:
            vectors[seg] = f"信念碎裂：{shatter}" if shatter else "信念碎裂"
        elif seg < midpoint:
            vectors[seg] = "信念受压：旧信念在压力下出现裂缝"
        else:
            vectors[seg] = "信念探索：碎裂后尝试行动并摸索新解"
    return vectors


def render_short_ideology_scene_block(
    ideology_compact: Mapping[str, Any] | None,
    *,
    thesis_vector: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Compact per-scene ideology prompt block for the PROSE_SCENE writer."""

    # Ignore stored thesis/forbidden/transition fields entirely at the final
    # boundary. The current author contract is the only methodology authority.
    current = _mapping((metadata or {}).get("story_design_kernel")).get("ideology_kernel")
    if current is None:
        current = (metadata or {}).get("ideology_kernel")
    from bestseller.domain.ideology import render_ideology_kernel_prompt_block

    return render_ideology_kernel_prompt_block(current if current is not None else {}, metadata=dict(metadata or {}))


async def generate_fanqie_beat_sheet(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    premise: str,
    *,
    book_spec: dict[str, Any] | None = None,
    cast_spec: dict[str, Any] | None = None,
    ideology_kernel: Any | None = None,
    requested_by: str = "system",
    return_selection: bool = False,
) -> Any:
    """Source-exact producer/cache; legacy provenance requires explicit recovery."""
    from bestseller.services.fanqie_beat_recovery import ensure_beat_sheet

    selection = await ensure_beat_sheet(
        session, settings, project_slug=project_slug, premise=premise,
        book_spec=book_spec, cast_spec=cast_spec, ideology_kernel=ideology_kernel,
        requested_by=requested_by,
    )
    return selection if return_selection else selection.sheet


def build_fanqie_segment_outline_batch(
    project: ProjectModel,
    beat_sheet: FanqieShortBeatSheet,
    *,
    book_spec: dict[str, Any] | None = None,
    cast_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project supplied candidate values; missing story facts stay unknown."""
    from bestseller.services.fanqie_segment_projection import project_segment_outline

    return project_segment_outline(
        project, beat_sheet, book_spec=book_spec, cast_spec=cast_spec,
    )


async def persist_fanqie_chapter_outline(
    session: AsyncSession,
    project_slug: str,
    outline_payload: dict[str, Any],
) -> UUID:
    batch = ChapterOutlineBatchInput.model_validate(outline_payload)
    content = batch.model_dump(mode="json")
    # Pydantic validates the planning body but does not declare custody metadata.
    # Keep the exact producer receipts alongside that body through normal import.
    if "_meta" in outline_payload:
        from copy import deepcopy

        if not isinstance(outline_payload["_meta"], dict):
            raise ValueError("fanqie_outline_metadata_must_be_object")
        content["_meta"] = deepcopy(outline_payload["_meta"])
    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
            content=content,
        ),
    )
    return artifact.id

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
    ideology_kernel_from_dict,
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


def build_short_ideology_compact(kernel: Any) -> dict[str, Any]:
    """Extract the load-bearing ideology spine for a short story (serializable).

    Keeps only what a single short can actually carry — 主主题/核心问题/信念弧/
    一强母题/1-2 条代价/禁用解法 — and drops 长篇-only fields (副母题、隐藏终局、
    per-volume 压力). Returns ``{}`` on any failure so callers stay non-blocking.
    """

    if kernel is None:
        return {}
    if isinstance(kernel, Mapping):
        try:
            kernel = ideology_kernel_from_dict(dict(kernel))
        except Exception:
            logger.warning("build_short_ideology_compact: invalid kernel dict", exc_info=True)
            return {}
    try:
        belief = kernel.belief_arc
        primary = kernel.primary_motif
        return {
            "thesis_statement": kernel.thesis_statement,
            "core_question": kernel.core_question,
            "belief_arc": {
                "initial": belief.initial_belief,
                "shatter": belief.midpoint_shatter,
                "reconstruction": belief.final_reconstruction,
            },
            "primary_motif": {
                "key": primary.motif_key,
                "display_name": primary.display_name,
                "thesis": primary.book_thesis,
                "symbols": list(primary.concrete_symbols)[:4],
            },
            "sub_themes": [t.proposition for t in kernel.sub_themes[:2]],
            "cost_laws": [
                {"acquires": law.acquires, "costs": law.costs}
                for law in kernel.cost_system[:2]
            ],
            "forbidden": list(kernel.forbidden_resolutions)[:2],
        }
    except Exception:
        logger.warning("build_short_ideology_compact failed", exc_info=True)
        return {}


def derive_short_thesis_vectors(
    segment_count: int,
    unlock_segment: int,
    ideology_compact: Mapping[str, Any] | None,
) -> dict[int, str]:
    """Map a belief arc (建立→受压→碎裂→代价→重建) onto each segment.

    Gives a short story an explicit value spine so every segment makes a
    thematic move instead of only delivering platform 爽点. Deterministic.
    """

    if not ideology_compact:
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
            vectors[seg] = "代价显形：碎裂后付出代价并摸索新解"
    return vectors


def render_short_ideology_scene_block(
    ideology_compact: Mapping[str, Any] | None,
    *,
    thesis_vector: str = "",
) -> str:
    """Compact per-scene ideology prompt block for the PROSE_SCENE writer."""

    if not ideology_compact:
        return ""
    lines = ["【本篇内核(在剧情里兑现,严禁说教/口号)】"]
    thesis = str(ideology_compact.get("thesis_statement") or "").strip()
    if thesis:
        lines.append(f"主主题：{thesis}")
    core_question = str(ideology_compact.get("core_question") or "").strip()
    if core_question:
        lines.append(f"核心问题：{core_question}")
    motif = _mapping(ideology_compact.get("primary_motif"))
    motif_name = str(motif.get("display_name") or "").strip()
    motif_thesis = str(motif.get("thesis") or "").strip()
    if motif_name or motif_thesis:
        joined = "——".join(part for part in (motif_name, motif_thesis) if part)
        lines.append(f"母题：{joined}")
    cost_laws = ideology_compact.get("cost_laws") or []
    if cost_laws:
        law = _mapping(cost_laws[0])
        acquires = str(law.get("acquires") or "").strip()
        costs = str(law.get("costs") or "").strip()
        if acquires and costs:
            lines.append(f"代价律：获得「{acquires}」必付「{costs}」，禁止无代价开挂")
    forbidden = ideology_compact.get("forbidden") or []
    if forbidden:
        lines.append(f"禁用廉价解法：{forbidden[0]}")
    if thesis_vector:
        lines.append(f"本段信念位置：{thesis_vector}")
    return "\n".join(lines)


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
    emotion_stack = select_emotion_stack(
        premise=premise,
        genre=f"{project.genre} {project.sub_genre or ''}",
    )
    emotion_block = render_emotion_stack_prompt_block(emotion_stack)
    resource_cards = adapt_long_form_resources_for_short(
        book_spec=book_spec,
        cast_spec=cast_spec,
        premise=premise,
    )
    resource_block = render_short_resource_prompt_block(resource_cards)
    ideology_compact = build_short_ideology_compact(ideology_kernel)
    ideology_block = render_ideology_compact_block(ideology_kernel) if ideology_kernel else ""
    ideology_prompt = f"{ideology_block}\n" if ideology_block else ""
    user_prompt = (
        f"书名：{project.title}\n"
        f"类型：{project.genre} / {project.sub_genre or ''}\n"
        f"梗概：{premise}\n"
        f"段数：{project.target_chapters}\n"
        f"目标全文：{project.target_word_count} 字\n"
        f"{emotion_block}\n"
        f"{ideology_prompt}"
        f"{resource_block}\n"
        "内核要求：每段的 emotional_turn 必须推进上面的信念弧（建立→受压→碎裂→重建），"
        "母题与代价律在剧情冲突里兑现，严禁说教或口号；末段完成信念重建。\n"
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
        "其中所有 *_contract 字段必须输出为 JSON object，例如 {\"summary\":\"...\"}，禁止直接输出字符串。"
    )
    fallback_sheet = _fallback_beat_sheet(project, premise)
    fallback_response = json.dumps(
        fallback_sheet.model_dump(mode="json"),
        ensure_ascii=False,
    )
    try:
        response = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=fragment or "你是番茄短故事策划，输出紧凑 beat sheet JSON。",
                user_prompt=user_prompt,
                fallback_response=fallback_response,
                project_id=project.id,
                metadata={"task": "fanqie_beat_sheet"},
            ),
        )
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        payload = _normalize_beat_sheet_payload(_extract_json_payload(raw))
        sheet = FanqieShortBeatSheet.model_validate(payload)
    except Exception:
        logger.warning("Fanqie beat sheet LLM failed; using fallback", exc_info=True)
        sheet = fallback_sheet

    if ideology_compact:
        segment_count = len(sheet.beats) or max(project.target_chapters, 1)
        vectors = derive_short_thesis_vectors(
            segment_count, sheet.unlock_milestone_segment, ideology_compact
        )
        new_beats = [
            beat.model_copy(
                update={"thesis_vector": vectors.get(beat.segment_number, beat.thesis_vector)}
            )
            for beat in sheet.beats
        ]
        sheet = sheet.model_copy(
            update={"ideology_compact": ideology_compact, "beats": new_beats}
        )

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
        continuity_contract = {
            **defaults["continuity_contract"],
            **(beat.continuity_contract or {}),
            "protagonist_name": protagonist_name,
        }
        sections = [
            ("开篇合同", beat.opening_contract or defaults["opening_contract"]),
            ("30%解锁合同", beat.unlock_contract or defaults["unlock_contract"]),
            (
                "能力代价合同",
                beat.ability_cost_contract or defaults["ability_cost_contract"],
            ),
            ("爽点合同", beat.payoff_contract or defaults["payoff_contract"]),
            ("收束合同", beat.closure_contract or defaults["closure_contract"]),
            ("连续性合同", continuity_contract),
        ]
        lines: list[str] = []
        for label, payload in sections:
            if not payload:
                continue
            compact = "；".join(f"{key}: {value}" for key, value in payload.items() if value)
            if compact:
                lines.append(f"{label}：{compact}")
        return lines

    def _story_goal(beat: FanqieShortBeat | None, fallback: str) -> str:
        if beat is None:
            return fallback
        return fallback or beat.purpose

    def _non_empty_text(value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""

    def _supporting_names() -> list[str]:
        names: list[str] = []
        for key in ("supporting_cast", "allies", "rivals"):
            for item in cast_payload.get(key) or []:
                item_map = _mapping(item)
                name = _non_empty_text(item_map.get("name") or item_map.get("character_ref"))
                if name and name != protagonist_name and name not in names:
                    names.append(name)
        antagonist = _mapping(cast_payload.get("antagonist"))
        antagonist_name = _non_empty_text(antagonist.get("name"))
        if antagonist_name and antagonist_name not in names:
            names.append(antagonist_name)
        for item in cast_payload.get("antagonist_forces") or []:
            item_map = _mapping(item)
            name = _non_empty_text(item_map.get("character_ref") or item_map.get("name"))
            if name and name != protagonist_name and name not in names:
                names.append(name)
        return names[:4]

    def _book_contract_lines() -> list[str]:
        spec = _mapping(book_spec or {})
        if not spec:
            return []
        lines: list[str] = []
        for key, label in (
            ("premise", "本书前提"),
            ("core_promise", "读者承诺"),
            ("reader_promise", "读者承诺"),
            ("main_thread", "主线"),
        ):
            value = _non_empty_text(spec.get(key))
            if value:
                lines.append(f"{label}：{value}")
        series_engine = _mapping(spec.get("series_engine") or spec.get("serialization"))
        for key, label in (
            ("chapter_arc", "短篇段落弧"),
            ("payoff_rhythm", "爽点节奏"),
            ("first_three_chapter_goal", "前段目标"),
        ):
            value = _non_empty_text(series_engine.get(key))
            if value:
                lines.append(f"{label}：{value}")
        return lines

    def _arc_items() -> list[str]:
        lines = _book_contract_lines()
        for line in lines:
            if line.startswith("短篇段落弧："):
                raw = line.split("：", 1)[1]
                for sep in ("→", "->", "=>"):
                    raw = raw.replace(sep, "\n")
                return [item.strip() for item in raw.splitlines() if item.strip()]
        return []

    support_names = _supporting_names()
    book_contract_lines = _book_contract_lines()
    arc_items = _arc_items()
    beat_by_number = {beat.segment_number: beat for beat in beat_sheet.beats}
    emotion_stack = select_emotion_stack(
        premise=beat_sheet.logline or getattr(project, "title", ""),
        genre=str(getattr(project, "genre", "") or ""),
    )
    emotion_contract_line = (
        "短篇社会情绪栈："
        f"主情绪={emotion_stack.primary.category}/{emotion_stack.primary.emotion}；"
        f"开局压力={emotion_stack.primary.opening_pressure}；"
        f"爽点兑现={emotion_stack.payoff_point or emotion_stack.primary.payoff}"
    )

    ideology_compact = dict(beat_sheet.ideology_compact or {})
    chapters: list[dict[str, Any]] = []
    segment_count = max(project.target_chapters, len(beat_sheet.beats), 1)
    for segment_number in range(1, segment_count + 1):
        beat = beat_by_number.get(segment_number)
        if beat is None:
            beat = FanqieShortBeat(
                segment_number=segment_number,
                beat_role=_BEAT_ROLES[min(segment_number - 1, len(_BEAT_ROLES) - 1)],
                purpose=f"推进短篇主线并兑现第{segment_number}段的明确回报。",
            )
        arc_item = arc_items[segment_number - 1] if segment_number <= len(arc_items) else ""
        visible_goal = _visible_goal_from_beat(beat, protagonist_name=protagonist_name)
        story_goal = _story_goal(beat, visible_goal)
        participants = [protagonist_name, *support_names]
        segment_conflict = arc_item or visible_goal
        scene_title = arc_item or f"{beat.beat_role}-{segment_number}"
        hook_description = _hook_from_beat(
            beat,
            final_segment=segment_number == segment_count,
            protagonist_name=protagonist_name,
        )
        defaults = _default_contracts(
            segment_number=segment_number,
            segment_count=segment_count,
            unlock_segment=beat_sheet.unlock_milestone_segment,
            protagonist_name=protagonist_name,
        )
        continuity_contract = {
            **defaults["continuity_contract"],
            **(beat.continuity_contract or {}),
            "protagonist_name": protagonist_name,
        }
        fanqie_contract = {
            "opening_contract": beat.opening_contract or defaults["opening_contract"],
            "unlock_contract": beat.unlock_contract or defaults["unlock_contract"],
            "ability_cost_contract": (
                beat.ability_cost_contract or defaults["ability_cost_contract"]
            ),
            "payoff_contract": beat.payoff_contract or defaults["payoff_contract"],
            "closure_contract": beat.closure_contract or defaults["closure_contract"],
            "continuity_contract": continuity_contract,
            "emotion_contract": emotion_contract_line
            if segment_number <= max(1, beat_sheet.unlock_milestone_segment)
            else "",
            "book_contract_lines": book_contract_lines if segment_number == 1 else [],
        }
        opening_situation = (
            _visible_contract_text(
                beat.opening_contract,
                protagonist_name=protagonist_name,
            )
            or visible_goal
        )
        visible_action = (
            _visible_story_text(beat.payoff, protagonist_name=protagonist_name)
            or visible_goal
            or segment_conflict
        )
        protagonist_flaw = (
            f"{protagonist_name}在污名和追责面前急于自证，容易被对手牵着节奏走。"
        )
        scene_stakes = (
            f"如果{protagonist_name}不能用可见证据推进局面，当前嫌疑会继续压到{protagonist_name}身上。"
        )
        information_control = "只释放当前段能验证的证据，把最终真凶动机留到后续反转。"
        signature_image = (
            opening_situation[:80]
            if opening_situation
            else f"{protagonist_name}在现场盯住一处异常证据。"
        )
        cut_point = hook_description if hook_description != protagonist_name else visible_action
        scene_ideology = render_short_ideology_scene_block(
            ideology_compact, thesis_vector=beat.thesis_vector
        )
        causal_contract = {
            "chapter_function": beat.beat_role,
            "thesis_vector": beat.thesis_vector,
            "pressure": segment_conflict,
            "protagonist_choice": visible_goal,
            "visible_action_or_reaction": visible_action,
            "resistance": (
                _visible_contract_text(
                    beat.closure_contract,
                    protagonist_name=protagonist_name,
                )
                or f"对手与规则阻止{protagonist_name}继续追查。"
            ),
            "cost_or_tradeoff": (
                _visible_contract_text(
                    beat.ability_cost_contract,
                    protagonist_name=protagonist_name,
                )
                or f"{protagonist_name}每次使用能力都会暴露自身并付出代价。"
            ),
            "gain_or_reveal": (
                _visible_contract_text(
                    beat.payoff_contract,
                    protagonist_name=protagonist_name,
                )
                or _visible_story_text(beat.payoff, protagonist_name=protagonist_name)
                or hook_description
            ),
            "state_change": (
                f"{protagonist_name}从上一轮压力中拿到新线索，局面转向："
                f"{hook_description}"
            ),
            "next_reader_desire": hook_description,
            "fanqie_short_v2": fanqie_contract,
        }
        time_label = f"第{segment_number}段现场：{opening_situation[:32]}"
        chapters.append(
            {
                "chapter_number": beat.segment_number,
                "title": f"第{beat.segment_number}段",
                "chapter_goal": story_goal,
                "opening_situation": opening_situation,
                "main_conflict": segment_conflict,
                "hook_description": hook_description,
                "protagonist_flaw": protagonist_flaw,
                "visible_action_or_reaction": visible_action,
                "payoff": _visible_story_text(beat.payoff, protagonist_name=protagonist_name)
                or hook_description,
                "tail_hook": hook_description,
                "causal_contract": causal_contract,
                "methodology_contract": {
                    "fanqie_short_visible_goal": visible_goal,
                    "fanqie_short_payoff": beat.payoff,
                    "fanqie_short_emotional_turn": beat.emotional_turn,
                    "fanqie_short_contract_lines": _contract_lines(beat),
                    "fanqie_short_v2": fanqie_contract,
                    "fanqie_short_ideology": scene_ideology,
                    "thesis_vector": beat.thesis_vector,
                },
                "target_word_count": per_segment_words,
                "volume_number": 1,
                "scenes": [
                    {
                        "scene_number": 1,
                        "scene_type": "conflict",
                        "title": scene_title,
                        "time_label": time_label,
                        "participants": participants,
                        "purpose": {
                            "story": story_goal,
                            "emotion": beat.emotional_turn or beat.payoff,
                        },
                        "conflict_stakes": scene_stakes,
                        "information_control": information_control,
                        "signature_image": signature_image,
                        "cut_point": cut_point,
                        "methodology_contract": {
                            "fanqie_short_v2": fanqie_contract,
                            "conflict_stakes": scene_stakes,
                            "information_control": information_control,
                            "signature_image": signature_image,
                            "cut_point": cut_point,
                            "fanqie_short_ideology": scene_ideology,
                            "thesis_vector": beat.thesis_vector,
                        },
                        "entry_state": {"pressure": segment_conflict},
                        "exit_state": {"hook": hook_description},
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

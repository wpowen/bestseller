from __future__ import annotations

import copy
import json
import logging
import math
import re
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, WorkflowStatus
from bestseller.domain.planning import NovelPlanningResult, PlanningArtifactCreate, PlanningArtifactRecord
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.prompt_packs import (
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.projects import get_project_by_slug, import_planning_artifact
from bestseller.services.story_bible import parse_cast_spec_input, parse_volume_plan_input, parse_world_spec_input
from bestseller.services.writing_profile import (
    is_english_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.services.workflows import create_workflow_run, create_workflow_step_run
from bestseller.settings import AppSettings


logger = logging.getLogger(__name__)

WORKFLOW_TYPE_GENERATE_NOVEL_PLAN = "generate_novel_plan"


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Planner returned empty content.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for opening, closing in (("{", "}"), ("[", "]")):
        start = stripped.find(opening)
        end = stripped.rfind(closing)
        if start != -1 and end != -1 and end > start:
            return json.loads(stripped[start : end + 1])
    raise ValueError("Planner output does not contain valid JSON.")


def _merge_planning_payload(fallback_payload: Any, generated_payload: Any) -> Any:
    if generated_payload is None:
        return copy.deepcopy(fallback_payload)

    if isinstance(fallback_payload, dict):
        if not isinstance(generated_payload, dict):
            return copy.deepcopy(fallback_payload)
        merged = copy.deepcopy(fallback_payload)
        for key, value in generated_payload.items():
            if key in merged:
                merged[key] = _merge_planning_payload(merged[key], value)
            elif value is not None:
                merged[key] = copy.deepcopy(value)
        return merged

    if isinstance(fallback_payload, list):
        if not isinstance(generated_payload, list):
            return copy.deepcopy(fallback_payload)
        if not generated_payload:
            return copy.deepcopy(fallback_payload)
        if len(fallback_payload) == len(generated_payload) and all(
            isinstance(fallback_item, dict) and isinstance(generated_item, dict)
            for fallback_item, generated_item in zip(fallback_payload, generated_payload, strict=False)
        ):
            return [
                _merge_planning_payload(fallback_item, generated_item)
                for fallback_item, generated_item in zip(fallback_payload, generated_payload, strict=False)
            ]
        return copy.deepcopy(generated_payload)

    if isinstance(generated_payload, str) and not generated_payload.strip():
        return copy.deepcopy(fallback_payload)

    return copy.deepcopy(generated_payload)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _non_empty_string(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _named_item(items: list[dict[str, Any]], index: int, default_name: str) -> dict[str, Any]:
    if 0 <= index < len(items):
        item = items[index]
        return {
            **item,
            "name": _non_empty_string(item.get("name"), default_name),
        }
    return {"name": default_name}


def _protagonist_name_from_book_spec(
    book_spec: dict[str, Any],
    premise: str,
    genre: str = "",
    language: str | None = None,
) -> str:
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    return _non_empty_string(
        protagonist.get("name"),
        _derive_protagonist_name(premise, genre, language=language),
    )


def _derive_protagonist_name(premise: str, genre: str = "", language: str | None = None) -> str:
    """Return a safe placeholder protagonist name.

    Premise text is NEVER regex-mined for names — that historically produced
    garbage fragments like ``基于末`` (from ``基于末日…``). The authoritative
    source for character names is the LLM call ``_generate_character_names()``
    which is invoked at the start of ``run_planning_pipeline``. This helper
    only exists as a last-resort placeholder when no LLM/book_spec name is
    available, and returns a curated genre-appropriate name from the pool.
    """
    pool = _genre_name_pool(genre, language=language)
    name = _mapping(pool.get("protagonist")).get("name")
    return name if isinstance(name, str) and name else "主角"


async def _generate_character_names(
    session: AsyncSession,
    settings: AppSettings,
    *,
    genre: str,
    sub_genre: str,
    language: str | None,
    premise: str,
    book_spec: dict[str, Any],
    character_count: int = 5,
    workflow_run_id: UUID | None = None,
    project_id: UUID | None = None,
) -> dict[str, Any]:
    """Generate contextually appropriate character names via LLM.

    Considers genre, era, character archetypes, and cultural context to produce
    natural, memorable names. Returns a dict with protagonist, allies, and
    antagonists name entries.
    """
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    archetype = protagonist.get("archetype", "")
    era_hints = _detect_era_from_genre(genre)
    is_en = is_english_language(language)

    if is_en:
        user_prompt = (
            f"Generate {character_count} character names for the following novel.\n\n"
            f"Genre: {genre} ({sub_genre})\n"
            f"Era / setting hint: {era_hints}\n"
            f"Premise: {premise[:300]}\n"
            f"Protagonist archetype: {archetype}\n\n"
            "Requirements:\n"
            "1. Names must feel natural for English-language commercial fiction in this genre.\n"
            "2. The protagonist name should be memorable and easy to pronounce.\n"
            "3. Avoid confusingly similar initials or sounds across the core cast.\n"
            "4. Antagonist names may imply personality, but stay subtle.\n\n"
            "Output JSON:\n"
            '{"protagonist": {"name": "protagonist name", "name_reasoning": "why it fits"},\n'
            '  "allies": [{"name": "ally name", "name_reasoning": "why it fits"}],\n'
            '  "antagonists": [{"name": "antagonist name", "name_reasoning": "why it fits"}]\n'
            "}"
        )
    else:
        user_prompt = (
            f"为以下小说生成 {character_count} 个角色名字。\n\n"
            f"题材：{genre}（{sub_genre}）\n"
            f"时代背景：{era_hints}\n"
            f"故事前提：{premise[:300]}\n"
            f"主角原型：{archetype}\n\n"
            f"要求：\n"
            f"1. 根据题材和时代选择合适的姓名风格：\n"
            f"   - 古代/仙侠/玄幻：古典风格（如 沈逸、苏暮晚、裴云霄）\n"
            f"   - 现代/都市：现代风格（如 林启、叶晨、宋思远）\n"
            f"   - 末日/科幻/未来：普通或硬朗风格（如 秦北、周远、夏凛）\n"
            f"2. 主角名 2-3 字，音调和谐，有记忆点\n"
            f"3. 所有角色姓氏不能重复\n"
            f"4. 避免谐音不雅、过于生僻或网文烂大街的名字\n"
            f"5. 反派名可暗示性格（但不要太刻意）\n\n"
            f"输出 JSON：\n"
            f'{{"protagonist": {{"name": "主角名", "name_reasoning": "命名理由"}},\n'
            f'  "allies": [{{"name": "盟友名", "name_reasoning": "命名理由"}}],\n'
            f'  "antagonists": [{{"name": "反派名", "name_reasoning": "命名理由"}}]\n'
            f"}}"
        )

    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=(
                "You are a naming specialist for English-language commercial fiction. "
                "Generate natural, memorable, genre-appropriate names. Output valid JSON only."
                if is_en
                else (
                    "你是一位中文小说命名专家。你精通各种题材的命名风格，能生成自然、"
                    "有记忆点、符合文化语境的角色名字。输出必须是合法 JSON，不要解释。"
                )
            ),
            user_prompt=user_prompt,
            fallback_response=json.dumps(
                _genre_name_pool(genre, language=language),
                ensure_ascii=False,
            ),
            prompt_template="generate_character_names",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
        ),
    )

    try:
        parsed = _extract_json_payload(result.content)
        if isinstance(parsed, dict) and parsed.get("protagonist", {}).get("name"):
            return parsed
    except (ValueError, KeyError):
        pass

    return _genre_name_pool(genre, language=language)


def _detect_era_from_genre(genre: str) -> str:
    """Infer era/setting from genre for name style selection."""
    normalized = genre.lower()
    if any(tok in normalized for tok in ("仙", "玄幻", "修真", "古代", "武侠", "历史")):
        return "古代/架空古风"
    if any(tok in normalized for tok in ("都市", "现代", "校园", "职场")):
        return "现代都市"
    if any(tok in normalized for tok in ("科幻", "末日", "未来", "赛博", "星际")):
        return "未来/末日"
    return "架空（可自由选择风格）"


def _genre_name_pool(genre: str, language: str | None = None) -> dict[str, Any]:
    """Genre-specific fallback name pool — replaces hardcoded names."""
    normalized = genre.lower()
    if is_english_language(language) or re.search(r"[a-z]", normalized):
        if any(tok in normalized for tok in ("fantasy", "epic", "romance", "thriller", "sci-fi", "science")):
            return {
                "protagonist": {"name": "Elara Voss", "name_reasoning": "Memorable commercial-fantasy cadence with a sharp visual profile"},
                "allies": [
                    {"name": "Rowan Vale", "name_reasoning": "Crisp and easy to parse in fast-moving prose"},
                    {"name": "Mira Hale", "name_reasoning": "Short, clean, and emotionally flexible"},
                ],
                "antagonists": [
                    {"name": "Lucian Thorne", "name_reasoning": "Carries polish, menace, and genre familiarity without sounding generic"},
                ],
            }
        return {
            "protagonist": {"name": "Elara Voss", "name_reasoning": "Distinctive and easy to remember"},
            "allies": [
                {"name": "Rowan Vale", "name_reasoning": "Balanced, readable, and commercially friendly"},
                {"name": "Mira Hale", "name_reasoning": "Short and clear in dialogue-heavy scenes"},
            ],
            "antagonists": [
                {"name": "Lucian Thorne", "name_reasoning": "Elegant and threatening without becoming melodramatic"},
            ],
        }
    if any(tok in normalized for tok in ("仙", "玄幻", "修真", "武侠")):
        return {
            "protagonist": {"name": "沈逸", "name_reasoning": "沈姓古朴，逸字飘逸，适合修仙/玄幻主角"},
            "allies": [
                {"name": "苏暮晚", "name_reasoning": "苏姓温婉，暮晚意境幽远"},
                {"name": "楚长歌", "name_reasoning": "楚姓有力，长歌豪迈"},
            ],
            "antagonists": [
                {"name": "裴云霄", "name_reasoning": "裴姓尊贵，云霄暗示野心"},
            ],
        }
    if any(tok in normalized for tok in ("都市", "现代", "校园")):
        return {
            "protagonist": {"name": "林启", "name_reasoning": "林姓常见亲切，启字暗示新的开始"},
            "allies": [
                {"name": "叶晨", "name_reasoning": "叶姓清新，晨字有朝气"},
                {"name": "宋思远", "name_reasoning": "宋姓大气，思远意境深远"},
            ],
            "antagonists": [
                {"name": "陆承渊", "name_reasoning": "陆姓稳重，承渊暗示深不可测"},
            ],
        }
    if any(tok in normalized for tok in ("末日", "科幻", "未来", "星际")):
        return {
            "protagonist": {"name": "秦北", "name_reasoning": "秦姓硬朗，北字冷峻，适合末日/科幻"},
            "allies": [
                {"name": "周远", "name_reasoning": "周姓朴实，远字有坚韧感"},
                {"name": "夏凛", "name_reasoning": "夏姓清亮，凛字有锐气"},
            ],
            "antagonists": [
                {"name": "方择", "name_reasoning": "方姓规矩，择字暗示冷酷取舍"},
            ],
        }
    # Default for other genres
    return {
        "protagonist": {"name": "卫朝", "name_reasoning": "卫姓有守护感，朝字向上"},
        "allies": [
            {"name": "顾临", "name_reasoning": "顾姓温润，临字有临危不惧之意"},
            {"name": "江澈", "name_reasoning": "江姓大气，澈字通透"},
        ],
        "antagonists": [
            {"name": "何承", "name_reasoning": "何姓疑问感，承字暗示肩负使命"},
        ],
    }


def _genre_profile(genre: str) -> dict[str, Any]:
    normalized = genre.lower()
    if any(token in normalized for token in ("sci", "space", "科幻", "science")):
        return {
            "tones": ["紧张", "冷峻", "悬疑"],
            "themes": ["真相与控制", "秩序与自由", "牺牲的代价"],
            "world_name": "边境航道网络",
            "world_premise": "航线、记录与权限共同决定谁拥有对现实的解释权。",
            "power_system_name": "权限印记",
            "locations": ["边境星港", "静默航道", "底层日志库"],
            "factions": ["帝国档案局", "边境自由引航团"],
        }
    if any(token in normalized for token in ("fantasy", "玄幻", "仙", "magic")):
        return {
            "tones": ["高压", "奇诡", "燃"],
            "themes": ["力量与代价", "秩序与反抗", "成长与背叛"],
            "world_name": "裂界王朝",
            "world_premise": "世界资源和力量阶梯由少数宗门垄断。",
            "power_system_name": "灵印体系",
            "locations": ["边荒城", "秘境裂谷", "王朝祖地"],
            "factions": ["镇界宗", "黑市同盟"],
        }
    return {
        "tones": ["紧张", "克制", "推进感"],
        "themes": ["身份与选择", "信任与代价", "真相与谎言"],
        "world_name": "风暴边城",
        "world_premise": "权力与秘密共同塑造每个人的命运。",
        "power_system_name": "势能阶梯",
        "locations": ["主城", "禁区", "旧档案馆"],
        "factions": ["统治机关", "地下同盟"],
    }


def _planner_writing_profile(project: ProjectModel) -> Any:
    raw = project.metadata_json.get("writing_profile") if isinstance(project.metadata_json, dict) else None
    return resolve_writing_profile(
        raw,
        genre=project.genre,
        sub_genre=project.sub_genre,
        audience=project.audience,
        language=project.language,
    )


def _planner_language(project: ProjectModel) -> str:
    return str(project.language or "zh-CN")


def _planner_prompt_pack(project: ProjectModel):
    writing_profile = _planner_writing_profile(project)
    return resolve_prompt_pack(
        writing_profile.market.prompt_pack_key,
        genre=project.genre,
        sub_genre=project.sub_genre,
    )


def _fallback_book_spec(project: ProjectModel, premise: str) -> dict[str, Any]:
    profile = _genre_profile(project.genre)
    writing_profile = _planner_writing_profile(project)
    protagonist_name = _derive_protagonist_name(premise, project.genre, language=project.language)
    return {
        "title": project.title,
        "logline": premise.strip(),
        "genre": project.genre,
        "target_audience": project.audience or "web-serial",
        "tone": writing_profile.style.tone_keywords or profile["tones"],
        "themes": profile["themes"] + [
            item for item in writing_profile.market.selling_points[:2] if item not in profile["themes"]
        ],
        "protagonist": {
            "name": protagonist_name,
            "core_wound": f"{protagonist_name}曾因一次关键判断失误付出沉重代价。",
            "external_goal": (
                writing_profile.character.protagonist_core_drive
                or f"{protagonist_name}必须主动追查并破解当前危机背后的操盘者。"
            ),
            "internal_need": f"{protagonist_name}需要从只靠个人硬撑，转向建立真正可持续的同盟。",
            "archetype": writing_profile.character.protagonist_archetype,
            "golden_finger": writing_profile.character.golden_finger,
        },
        "stakes": {
            "personal": f"{protagonist_name}会失去自己仍在意的人。",
            "social": "更大范围的秩序会因此崩坏，更多无辜者将被牵连。",
            "existential": "如果幕后计划成功，整个世界的基本运行秩序都会被改写。",
        },
        "series_engine": {
            "core_loop": "主角利用差异化优势抢先一步 -> 得到短回报 -> 引来更大反压 -> 被迫升级手段 -> 揭开更深真相",
            "hook_style": writing_profile.market.chapter_hook_strategy,
            "reader_promise": writing_profile.market.reader_promise,
            "selling_points": writing_profile.market.selling_points,
            "trope_keywords": writing_profile.market.trope_keywords,
            "opening_strategy": writing_profile.market.opening_strategy,
            "payoff_rhythm": writing_profile.market.payoff_rhythm,
            "first_three_chapter_goal": writing_profile.serialization.first_three_chapter_goal,
        },
    }


def _fallback_world_spec(project: ProjectModel, premise: str, book_spec: dict[str, Any]) -> dict[str, Any]:
    profile = _genre_profile(project.genre)
    protagonist_name = _protagonist_name_from_book_spec(book_spec, premise, project.genre, language=project.language)
    return {
        "world_name": profile["world_name"],
        "world_premise": profile["world_premise"],
        "rules": [
            {
                "rule_id": "R001",
                "name": "记录优先规则",
                "description": "官方记录与权力机关认定的事实高于个人证词。",
                "story_consequence": f"{protagonist_name}不能只靠亲历与情感说服别人。",
                "exploitation_potential": "只要找到底层证据链，就能把规则反过来用于推翻既有秩序。",
            },
            {
                "rule_id": "R002",
                "name": "高阶权限封锁",
                "description": "关键资源、知识或力量只能由高阶权限持有者直接调用。",
                "story_consequence": "主角必须冒险越界或拉拢内部人。",
                "exploitation_potential": "伪装、借权或黑箱路径可以制造局部突破口。",
            },
            {
                "rule_id": "R003",
                "name": "禁区失联效应",
                "description": "一旦进入核心禁区，常规支援和外部通讯都会急剧失效。",
                "story_consequence": "关键调查与决战必须在高压孤立环境中完成。",
                "exploitation_potential": "禁区也会削弱统治者的即时控制力。",
            },
        ],
        "power_system": {
            "name": profile["power_system_name"],
            "tiers": ["低阶", "中阶", "高阶", "顶层"],
            "acquisition_method": "通过真实冒险、资源争夺和高压试炼提升。",
            "hard_limits": "每次跃迁都会伴随代价、损耗或不可逆牺牲。",
            "protagonist_starting_tier": "低阶",
        },
        "locations": [
            {
                "name": profile["locations"][0],
                "type": "核心据点",
                "atmosphere": "高压、秩序化、随时可能爆发冲突",
                "key_rules": ["R001", "R002"],
                "story_role": "开局主舞台与秩序压迫的来源",
            },
            {
                "name": profile["locations"][1],
                "type": "危险区域",
                "atmosphere": "失真、压迫、逼迫人物做出选择",
                "key_rules": ["R003"],
                "story_role": "调查推进和高潮冲突发生地",
            },
            {
                "name": profile["locations"][2],
                "type": "终极目标地",
                "atmosphere": "神秘、封闭、伴随巨大代价",
                "key_rules": ["R001", "R002", "R003"],
                "story_role": "最终真相与关键证据的藏身处",
            },
        ],
        "factions": [
            {
                "name": profile["factions"][0],
                "goal": "维持既有秩序与控制力。",
                "method": "通过规则、资源和强制力量压制异议。",
                "relationship_to_protagonist": "敌对",
                "internal_conflict": "内部有人知道真相，但不敢公开站队。",
            },
            {
                "name": profile["factions"][1],
                "goal": "在夹缝中保住生存空间并获取更多自主权。",
                "method": "私下交易、非正式合作与灰色行动。",
                "relationship_to_protagonist": "复杂",
                "internal_conflict": "既想利用主角，又担心被主角拖下水。",
            },
        ],
        "power_structure": "高层掌握规则解释权，中层执行控制，底层只能在裂缝里求生。",
        "history_key_events": [
            {
                "event": f"{protagonist_name}过去经历过一次被官方定性为事故的重大失败。",
                "relevance": "这既是主角心结，也是当前主线危机的前史入口。",
            }
        ],
        "forbidden_zones": "任何接近核心真相存放处的人都会被默认视作威胁。",
    }


def _build_default_conflict_forces(
    *,
    protagonist_name: str,
    antagonist_name: str,
    local_threat_name: str,
    betrayer_name: str,
    ally_name: str,
    volume_count: int,
) -> list[dict[str, Any]]:
    """Generate default conflict forces based on the volume count.

    Each force represents a different type of challenge the protagonist
    faces at a specific stage of the story, ensuring the narrative
    evolves rather than repeating the same antagonist pressure.
    """
    phases = _assign_conflict_phases(volume_count)
    forces: list[dict[str, Any]] = []
    phase_to_force: dict[str, dict[str, Any]] = {
        "survival": {
            "name": f"{local_threat_name}的地方势力",
            "force_type": "faction",
            "threat_description": f"{local_threat_name}控制着主角所在区域的资源和通道，是最直接的生存威胁。",
            "relationship_to_protagonist": "直接敌对——双方争夺同一片生存空间。",
            "escalation_path": f"从资源封锁到暴力驱逐，最终暴露与{antagonist_name}的勾连。",
            "character_ref": local_threat_name,
        },
        "political_intrigue": {
            "name": "权力暗网",
            "force_type": "systemic",
            "threat_description": "体制内部的权力博弈和规则操弄，让主角的每一步调查都可能被反制。",
            "relationship_to_protagonist": "主角是这场权力游戏中的局外人和变量。",
            "escalation_path": "从暗中施压到公开封锁，盟友被迫站队。",
        },
        "betrayal": {
            "name": f"{betrayer_name}的背叛",
            "force_type": "character",
            "threat_description": f"{betrayer_name}一直潜伏在主角身边，在关键时刻亮出真实立场。",
            "relationship_to_protagonist": "曾经最信任的同伴，背叛后成为最危险的敌人。",
            "escalation_path": "从暗中传递情报到公开反水，彻底摧毁主角的信任体系。",
            "character_ref": betrayer_name,
        },
        "faction_war": {
            "name": "多方势力全面冲突",
            "force_type": "faction",
            "threat_description": "多个势力之间的矛盾全面爆发，主角被卷入大规模对抗。",
            "relationship_to_protagonist": "主角是各方都想拉拢或消灭的关键变量。",
            "escalation_path": "从局部冲突到全面战争，主角必须在混战中找到自己的位置。",
        },
        "existential_threat": {
            "name": f"{antagonist_name}的终极计划",
            "force_type": "character",
            "threat_description": f"{antagonist_name}的真正目标浮出水面，威胁到整个世界的根基。",
            "relationship_to_protagonist": "一切矛盾的终极根源，主角命运的最终对手。",
            "escalation_path": "从幕后操盘到亲自下场，主角必须倾尽一切与之对决。",
            "character_ref": antagonist_name,
        },
        "internal_reckoning": {
            "name": f"{protagonist_name}的内心拷问",
            "force_type": "internal",
            "threat_description": "主角一路走来积累的选择、牺牲和代价，最终汇聚成无法逃避的自我审判。",
            "relationship_to_protagonist": "主角自身就是最大的敌人。",
            "escalation_path": "从隐约的不安到全面的精神危机，最终完成蜕变或崩溃。",
        },
    }
    for vol_idx, phase in enumerate(phases, start=1):
        base = phase_to_force.get(phase, phase_to_force["survival"])
        forces.append({
            **base,
            "active_volumes": [vol_idx],
        })
    return forces


def _fallback_cast_spec(project: ProjectModel, premise: str, book_spec: dict[str, Any], world_spec: dict[str, Any]) -> dict[str, Any]:
    profile = _genre_profile(project.genre)
    writing_profile = _planner_writing_profile(project)
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    protagonist_name = _protagonist_name_from_book_spec(book_spec, premise, project.genre, language=project.language)
    external_goal = _non_empty_string(
        protagonist.get("external_goal"),
        f"{protagonist_name}必须主动追查并破解当前危机背后的操盘者。",
    )
    locations = _mapping_list(_mapping(world_spec).get("locations"))
    factions = _mapping_list(_mapping(world_spec).get("factions"))
    power_system = _mapping(_mapping(world_spec).get("power_system"))
    home_location = _named_item(locations, 0, profile["locations"][0])
    ruling_faction = _named_item(factions, 0, profile["factions"][0])
    protagonist_tier = _non_empty_string(power_system.get("protagonist_starting_tier"), "低阶")
    # Use genre-aware name pool instead of hardcoded names
    name_pool = _genre_name_pool(project.genre, language=project.language)
    pool_allies = [a["name"] for a in name_pool.get("allies", []) if a.get("name")]
    pool_antagonists = [a["name"] for a in name_pool.get("antagonists", []) if a.get("name")]
    ally_name = next((n for n in pool_allies if n != protagonist_name), "顾临")
    antagonist_name = next((n for n in pool_antagonists if n != protagonist_name), "何承")
    # Extra names for multi-force conflict characters
    _used = {protagonist_name, ally_name, antagonist_name}
    _extra_allies = [n for n in pool_allies if n not in _used]
    _extra_pool_zh = ["陈厉", "孙覃", "方铮", "钟戎"]
    _extra_pool_en = ["Marcus Vane", "Sera Holt", "Owen Drake", "Kael Dunn"]
    _extra_names = _extra_pool_en if is_english_language(project.language) else _extra_pool_zh
    local_threat_name = _extra_allies[0] if _extra_allies else _extra_names[0]
    _used.add(local_threat_name)
    betrayer_name = next((n for n in _extra_allies[1:] if n not in _used), _extra_names[1])
    _used.add(betrayer_name)
    # Determine volume count for conflict force assignment
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    volume_count = hierarchy["volume_count"]
    return {
        "protagonist": {
            "name": protagonist_name,
            "age": 28,
            "role": "protagonist",
            "background": f"曾在{home_location['name']}所属体系中工作，后被边缘化。",
            "goal": external_goal,
            "fear": "再次因为自己的决定害死重要的人。",
            "flaw": "习惯把压力全部扛在自己身上。",
            "strength": "对异常细节和风险变化高度敏感。",
            "secret": "主角一直怀疑过去的失败并非表面原因。",
            "arc_trajectory": "从单打独斗到建立可持续同盟。",
            "arc_state": "开场",
            "archetype": writing_profile.character.protagonist_archetype,
            "golden_finger": writing_profile.character.golden_finger,
            "knowledge_state": {
                "knows": ["当前危机存在异常迹象", "官方叙事有漏洞"],
                "falsely_believes": [f"{ally_name}当年做出了背离自己的选择"],
                "unaware_of": [f"{antagonist_name}与过去事故存在直接关联"],
            },
            "power_tier": protagonist_tier,
            "voice_profile": {
                "speech_register": "口语偏利落",
                "verbal_tics": ["……算了", "我来想办法"],
                "sentence_style": "短句利落型",
                "emotional_expression": "内敛",
                "mannerisms": ["下意识揉眉心", "说到关键处压低声音"],
                "internal_monologue_style": "碎片式自问自答",
                "vocabulary_level": "中",
            },
            "moral_framework": {
                "core_values": ["保护身边的人", "真相比秩序重要"],
                "lines_never_crossed": ["不会牺牲无辜者换取情报"],
                "willing_to_sacrifice": "个人安全和社会地位",
            },
            "relationships": [
                {
                    "character": ally_name,
                    "type": "旧搭档",
                    "tension": "彼此仍认可对方能力，但都有未说开的旧账。",
                },
                {
                    "character": antagonist_name,
                    "type": "敌人",
                    "tension": "双方都知道对方会成为自己计划里最大的变量。",
                },
            ],
        },
        "antagonist": {
            "name": antagonist_name,
            "role": "antagonist",
            "background": f"{ruling_faction['name']}中的高位操盘者。",
            "goal": "在真相曝光前完成既定重构计划并清除证据。",
            "fear": "一旦核心真相外泄，自己会被更高层抛弃。",
            "flaw": "相信秩序永远比人更重要。",
            "strength": "掌握规则、资源与执行力量。",
            "secret": "其本人直接参与了主角过去那场失败背后的决策链。",
            "arc_trajectory": "从幕后操盘到公开下场追杀主角。",
            "arc_state": "开场",
            "knowledge_state": {
                "knows": ["主角已经开始怀疑旧案", "体系里有人可能倒向主角"],
                "falsely_believes": [f"{ally_name}仍然完全可控"],
                "unaware_of": ["主角会这么快找到真正证据链"],
            },
            "power_tier": "高阶",
            "voice_profile": {
                "speech_register": "文雅官腔",
                "verbal_tics": ["不过是秩序的代价", "你以为呢"],
                "sentence_style": "长句思辨型",
                "emotional_expression": "冷静克制、偶尔流露轻蔑",
                "mannerisms": ["说话时不看对方眼睛", "习惯性整理袖口"],
                "internal_monologue_style": "冷酷推演式",
                "vocabulary_level": "高",
            },
            "moral_framework": {
                "core_values": ["秩序高于个体", "结果证明手段"],
                "lines_never_crossed": ["不会亲手动手——总让规则替自己执行"],
                "willing_to_sacrifice": "任何妨碍大局的人，包括自己的盟友",
            },
            "relationships": [
                {
                    "character": protagonist_name,
                    "type": "追捕对象",
                    "tension": "必须压制对方，但又不能让其过早死去以免线索失控。",
                }
            ],
            "justification": "只要秩序不崩，牺牲少数人就是必要成本。",
            "method": "删改记录、借规则压制、操控追捕和资源封锁。",
            "weakness": "过度依赖体制和既有权力结构。",
            "relationship_to_protagonist": "主角过去那场惨败背后的关键责任人之一。",
            "reveal_timing": "第一卷末",
        },
        "supporting_cast": [
            {
                "name": ally_name,
                "role": "ally",
                "background": "仍在体系内部活动的旧搭档。",
                "goal": "确认旧案真相并尽量保护仍在局中的人。",
                "value_to_story": "提供行动力、体制内视角和情感张力。",
                "potential_betrayal": "中",
                "arc_state": "谨慎观望",
                "knowledge_state": {
                    "knows": ["过去那场事故还有未公开的一段记录"],
                    "falsely_believes": ["只要低调调查就能避免更大冲突"],
                    "unaware_of": [f"{antagonist_name}已经将自己视为潜在隐患"],
                },
                "voice_profile": {
                    "speech_register": "体制内正式用语夹杂私下吐槽",
                    "verbal_tics": ["你听我说", "这事儿没那么简单"],
                    "sentence_style": "中等长度、逻辑清晰",
                    "emotional_expression": "表面沉稳、私下焦虑",
                    "mannerisms": ["紧张时反复摸口袋里的旧证件"],
                    "internal_monologue_style": "反复权衡利弊",
                    "vocabulary_level": "中高",
                },
                "moral_framework": {
                    "core_values": ["保护还在局中的人", "忠诚但有条件"],
                    "lines_never_crossed": ["不会出卖曾经的搭档"],
                    "willing_to_sacrifice": "自己在体系内的前途",
                },
            },
            {
                "name": local_threat_name,
                "role": "antagonist",
                "background": f"主角所在地区的实权人物，{ruling_faction['name']}在基层的执行者。",
                "goal": "维护自己的地盘和既得利益，清除一切不稳定因素。",
                "flaw": "目光短浅，只关心眼前的控制权。",
                "strength": "对本地资源和人脉有绝对掌控力。",
                "secret": f"私下与{antagonist_name}有利益输送关系。",
                "arc_trajectory": "从地方小霸到被更高层抛弃的弃子。",
                "arc_state": "开场",
                "power_tier": "中阶",
                "voice_profile": {
                    "speech_register": "粗犷直接",
                    "verbal_tics": ["在我地盘上", "你算什么东西"],
                    "sentence_style": "短句命令型",
                    "emotional_expression": "外放暴躁",
                    "mannerisms": ["说话时习惯拍桌子"],
                    "internal_monologue_style": "简单粗暴的利益算计",
                    "vocabulary_level": "低",
                },
                "moral_framework": {
                    "core_values": ["地盘就是一切", "拳头大的说了算"],
                    "lines_never_crossed": [],
                    "willing_to_sacrifice": "任何挡在利益面前的人",
                },
            },
            {
                "name": betrayer_name,
                "role": "ally",
                "background": f"主角信任的同伴之一，曾在关键时刻提供过帮助。",
                "goal": "表面上协助主角，实际上在为自己的秘密目标铺路。",
                "flaw": "无法割舍自己的野心。",
                "strength": "善于隐藏真实意图，社交能力极强。",
                "secret": "早已在暗中与更高层势力达成交易。",
                "arc_trajectory": "从可靠同伴到背叛者，最终被自己的选择反噬。",
                "arc_state": "伪装期",
                "power_tier": protagonist_tier,
                "voice_profile": {
                    "speech_register": "温和亲切",
                    "verbal_tics": ["放心", "交给我"],
                    "sentence_style": "柔和中等长度",
                    "emotional_expression": "表面温暖体贴",
                    "mannerisms": ["总是第一个主动帮忙"],
                    "internal_monologue_style": "精密计算的伪善",
                    "vocabulary_level": "中高",
                },
                "moral_framework": {
                    "core_values": ["自己的利益高于一切"],
                    "lines_never_crossed": [],
                    "willing_to_sacrifice": "任何人的信任",
                },
            },
        ],
        "antagonist_forces": _build_default_conflict_forces(
            protagonist_name=protagonist_name,
            antagonist_name=antagonist_name,
            local_threat_name=local_threat_name,
            betrayer_name=betrayer_name,
            ally_name=ally_name,
            volume_count=volume_count,
        ),
        "conflict_map": [
            {
                "character_a": protagonist_name,
                "character_b": ally_name,
                "conflict_type": "情感纠葛",
                "trigger_condition": "一旦谈到过去那场失败，两人的旧误会就会被重新点燃。",
            },
            {
                "character_a": protagonist_name,
                "character_b": antagonist_name,
                "conflict_type": "目标冲突",
                "trigger_condition": "主角一旦接近核心证据链，反派就必须公开加压。",
            },
            {
                "character_a": protagonist_name,
                "character_b": local_threat_name,
                "conflict_type": "生存对抗",
                "trigger_condition": "主角在地方势力的地盘上展开行动时。",
            },
            {
                "character_a": protagonist_name,
                "character_b": betrayer_name,
                "conflict_type": "隐性背叛",
                "trigger_condition": "主角越接近真相，背叛者越需要加速自己的计划。",
            },
        ],
    }


def compute_linear_hierarchy(total_chapters: int) -> dict[str, int]:
    """Compute act/volume/arc counts for a LINEAR novel based on total chapter count.

    Returns a dict with keys: act_count, volume_count, arc_batch_size.

    The hierarchy scales naturally with novel length:
    - arc_batch_size is fixed at 12 (the narrative rhythm atom)
    - volume_count grows with chapters (~30-50 chapters per volume)
    - act_count grows slowly (macro narrative arcs, max 6)

    Backward compatible: novels ≤50 chapters get act_count=1, volume_count=1,
    behaving identically to the old system.
    """
    arc_batch_size = 12

    # Volume count: ~30-50 chapters per volume
    if total_chapters <= 50:
        volume_count = 1
    elif total_chapters <= 120:
        volume_count = max(2, round(total_chapters / 30))
    else:
        volume_count = max(3, math.ceil(total_chapters / 50))

    # Act count: macro narrative structure (1-6 acts)
    if total_chapters <= 50:
        act_count = 1
    elif total_chapters <= 120:
        act_count = 3
    elif total_chapters <= 300:
        act_count = 4
    elif total_chapters <= 1500:
        act_count = 5
    else:
        act_count = 6

    return {
        "act_count": act_count,
        "volume_count": volume_count,
        "arc_batch_size": arc_batch_size,
    }


def _build_volume_ranges(total_chapters: int, volume_count: int) -> list[tuple[int, int]]:
    base = total_chapters // volume_count
    remainder = total_chapters % volume_count
    ranges: list[tuple[int, int]] = []
    cursor = 1
    for index in range(volume_count):
        count = base + (1 if index < remainder else 0)
        ranges.append((cursor, cursor + count - 1))
        cursor += count
    return ranges


_VOLUME_TITLE_BY_PHASE: dict[str, str] = {
    "survival": "绝境",
    "political_intrigue": "暗棋",
    "betrayal": "裂痕",
    "faction_war": "乱局",
    "existential_threat": "终焉",
    "internal_reckoning": "蜕变",
}

_VOLUME_GOAL_TEMPLATES: dict[str, str] = {
    "survival": "{protagonist}必须在{force_name}的直接威胁下争取到生存空间和初步的反击资本。",
    "political_intrigue": "{protagonist}需要看穿{force_name}背后的权力博弈，找到可以利用的裂缝。",
    "betrayal": "{protagonist}必须在{force_name}造成的信任崩塌中重新确认谁是真正的盟友。",
    "faction_war": "{protagonist}需要在{force_name}引发的多方混战中找到自己的立足之地。",
    "existential_threat": "{protagonist}必须倾尽所有力量去阻止{force_name}带来的终极灾难。",
    "internal_reckoning": "{protagonist}必须直面自己内心最深处的矛盾，完成真正的蜕变。",
}

_VOLUME_RESOLUTION_TEMPLATES: dict[str, str] = {
    "survival": "主角暂时挣脱了生存危机，但代价是暴露在更大势力的视野中。",
    "political_intrigue": "主角撕开了权力网络的一角，却发现更深层的阴谋才刚刚浮出水面。",
    "betrayal": "主角在信任废墟上重建了新的同盟，但伤痕不会轻易愈合。",
    "faction_war": "大战暂时平息，但格局已经彻底改变，主角的位置也随之转变。",
    "existential_threat": "主角以巨大的代价阻止了最坏的结果，世界暂时稳住但永远改变了。",
    "internal_reckoning": "主角完成了蜕变，以全新的姿态面对之后的道路。",
}


def _fallback_volume_plan(project: ProjectModel, book_spec: dict[str, Any], cast_spec: dict[str, Any], world_spec: dict[str, Any]) -> list[dict[str, Any]]:
    profile = _genre_profile(project.genre)
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    volume_count = hierarchy["volume_count"]
    chapter_ranges = _build_volume_ranges(total_chapters, volume_count)
    cast_payload = _mapping(cast_spec)
    protagonist_name = _non_empty_string(_mapping(cast_payload.get("protagonist")).get("name"), "主角")
    antagonist_name = _non_empty_string(_mapping(cast_payload.get("antagonist")).get("name"), "敌对操盘者")
    themes = _string_list(_mapping(book_spec).get("themes")) or profile["themes"]
    power_system = _mapping(_mapping(world_spec).get("power_system"))
    protagonist_tier = _non_empty_string(power_system.get("protagonist_starting_tier"), "低阶")

    # Use conflict forces if available, otherwise fall back to single-antagonist
    antagonist_forces = _mapping_list(cast_payload.get("antagonist_forces"))
    conflict_phases = _assign_conflict_phases(volume_count)
    # Build volume→force mapping
    force_by_volume: dict[int, dict[str, Any]] = {}
    for force_raw in antagonist_forces:
        force = _mapping(force_raw)
        for vol in (force.get("active_volumes") or []):
            if isinstance(vol, int):
                force_by_volume[vol] = force

    plan: list[dict[str, Any]] = []
    for volume_number, (chapter_start, chapter_end) in enumerate(chapter_ranges, start=1):
        phase = conflict_phases[min(volume_number - 1, len(conflict_phases) - 1)]
        force = force_by_volume.get(volume_number, {})
        force_name = _non_empty_string(force.get("name"), antagonist_name)

        vol_title = _VOLUME_TITLE_BY_PHASE.get(phase, "变局")
        vol_obstacle = _VOLUME_OBSTACLE_TEMPLATES.get(phase, "{force_name}持续施压。").format(
            force_name=force_name, protagonist=protagonist_name,
        )
        vol_climax = _VOLUME_CLIMAX_TEMPLATES.get(phase, "{protagonist}完成一次关键突破。").format(
            force_name=force_name, protagonist=protagonist_name,
        )
        vol_goal = _VOLUME_GOAL_TEMPLATES.get(phase, "{protagonist}推进主线。").format(
            force_name=force_name, protagonist=protagonist_name,
        )
        vol_resolution_text = _VOLUME_RESOLUTION_TEMPLATES.get(phase, "主角取得进展但付出了代价。")

        # Compute arc ranges within this volume
        arc_batch_size = hierarchy["arc_batch_size"]
        arcs: list[list[int]] = []
        cursor = chapter_start
        while cursor <= chapter_end:
            arc_end = min(cursor + arc_batch_size - 1, chapter_end)
            arcs.append([cursor, arc_end])
            cursor = arc_end + 1

        plan.append(
            {
                "volume_number": volume_number,
                "volume_title": f"第{volume_number}卷：{vol_title}",
                "volume_theme": themes[(volume_number - 1) % len(themes)],
                "word_count_target": int(project.target_word_count / volume_count),
                "chapter_count_target": chapter_end - chapter_start + 1,
                "conflict_phase": phase,
                "primary_force_name": force_name,
                "opening_state": {
                    "protagonist_status": "仍在高压局面中被迫行动" if volume_number == 1 else f"经历了第{volume_number - 1}卷后处于新的起点",
                    "protagonist_power_tier": protagonist_tier
                    if volume_number == 1
                    else f"第{volume_number - 1}卷后更成熟的状态",
                    "world_situation": f"来自{force_name}的{phase}型威胁正在成形。",
                },
                "volume_goal": vol_goal,
                "volume_obstacle": vol_obstacle,
                "volume_climax": vol_climax,
                "volume_resolution": {
                    "protagonist_power_tier": "中阶" if volume_number >= 2 else protagonist_tier,
                    "goal_achieved": True,
                    "cost_paid": vol_resolution_text,
                    "new_threat_introduced": f"第{volume_number + 1}卷的新型挑战即将登场。" if volume_number < volume_count else "所有悬念收束。",
                },
                "key_reveals": [f"第{volume_number}卷揭示与{force_name}相关的关键真相。"],
                "foreshadowing_planted": [f"为第{volume_number + 1}卷的新挑战埋下伏笔。"] if volume_number < volume_count else [],
                "foreshadowing_paid_off": [f"回收前序卷的一个关键误导或伏笔。"] if volume_number > 1 else [],
                "reader_hook_to_next": f"卷末{force_name}的威胁虽然暂时解决，但引出了更大的变局。" if volume_number < volume_count else "故事走向终章。",
                "arc_ranges": arcs,
                "is_final_volume": volume_number == volume_count,
            }
        )
    return plan


# ── Act-level planning (幕级规划) ──────────────────────────────────
# For novels >50 chapters, acts provide macro narrative structure
# above volumes: Act → Volume → Arc → Chapter.

_ACT_THEMES_ZH = [
    ("觉醒崛起", "热血", "主角从底层觉醒，获得第一个重大优势"),
    ("扩张威胁", "紧张", "主角实力增长引来更强大的对手"),
    ("危机蜕变", "压抑", "遭遇重大挫折完成更深层蜕变"),
    ("决战前夜", "震撼", "最终决战棋局布置各方力量汇聚"),
    ("最终对决", "爽快", "决战收割情感完成所有承诺"),
    ("余韵新篇", "满足", "善后收束余韵留白"),
]

_ACT_THEMES_EN = [
    ("Awakening", "thrilling", "Protagonist rises from obscurity, gains first major advantage"),
    ("Escalation", "tense", "Growing power attracts deadlier enemies"),
    ("Crisis", "dark", "Major setback forces deeper transformation"),
    ("Convergence", "epic", "All forces converge for the final confrontation"),
    ("Climax", "cathartic", "Final battle, emotional payoffs, all promises fulfilled"),
    ("Epilogue", "satisfying", "Resolution, aftermath, and lingering resonance"),
]


def _fallback_act_plan(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    world_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate a fallback act plan for novels with >50 chapters.

    Each act spans multiple volumes and represents a macro narrative arc.
    Adapted from IF's _generate_fallback_acts() but without branch_opportunities.
    """
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    act_count = hierarchy["act_count"]
    arc_batch_size = hierarchy["arc_batch_size"]

    is_en = is_english_language(project.language)
    act_themes = _ACT_THEMES_EN if is_en else _ACT_THEMES_ZH

    protagonist_name = _non_empty_string(
        _mapping(_mapping(cast_spec).get("protagonist")).get("name"),
        "Protagonist" if is_en else "主角",
    )

    act_size = total_chapters // act_count
    acts: list[dict[str, Any]] = []

    for i in range(act_count):
        start = i * act_size + 1
        end = (i + 1) * act_size if i < act_count - 1 else total_chapters
        theme_idx = min(i, len(act_themes) - 1)
        theme, emotion, goal = act_themes[theme_idx]

        # Build arc breakdown within this act
        arcs: list[dict[str, Any]] = []
        arc_start = start
        arc_idx = 0
        while arc_start <= end:
            arc_end = min(arc_start + arc_batch_size - 1, end)
            arc_goal = (
                f"Advance the core conflict of the {theme} phase"
                if is_en
                else f"推进{theme}阶段的核心冲突"
            )
            arcs.append({
                "arc_index": arc_idx,
                "chapter_start": arc_start,
                "chapter_end": arc_end,
                "arc_goal": arc_goal,
            })
            arc_start = arc_end + 1
            arc_idx += 1

        climax_chapter = start + (end - start) * 4 // 5
        is_final = i == act_count - 1

        act_dict: dict[str, Any] = {
            "act_id": f"act_{i + 1:02d}",
            "act_index": i,
            "title": f"Act {i + 1}: {theme}" if is_en else f"第{i + 1}幕：{theme}",
            "chapter_start": start,
            "chapter_end": end,
            "act_goal": goal,
            "core_theme": theme,
            "dominant_emotion": emotion,
            "climax_chapter": climax_chapter,
            "entry_state": (
                f"{protagonist_name} begins the journey"
                if is_en
                else f"{protagonist_name}踏上征程"
            ) if i == 0 else (
                f"{protagonist_name} enters a new phase after Act {i}"
                if is_en
                else f"{protagonist_name}经历第{i}幕后进入新阶段"
            ),
            "exit_state": (
                f"{protagonist_name} completes the story"
                if is_en
                else f"{protagonist_name}完成全篇"
            ) if is_final else (
                f"{protagonist_name} is transformed and ready for Act {i + 2}"
                if is_en
                else f"{protagonist_name}完成蜕变，进入第{i + 2}幕"
            ),
            "payoff_promises": [
                f"Act {i + 1} core payoff delivered" if is_en else f"第{i + 1}幕核心爽点兑现"
            ],
            "arc_breakdown": arcs,
            "is_final_act": is_final,
        }
        if is_final:
            act_dict["resolution_contract"] = {
                "all_threads_resolved": True,
                "emotional_closure": True,
                "protagonist_arc_complete": True,
            }
        acts.append(act_dict)

    return acts


def _act_plan_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
) -> tuple[str, str]:
    """Generate LLM prompts for act-level planning."""
    language = _planner_language(project)
    is_en = is_english_language(language)
    hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
    act_count = hierarchy["act_count"]
    arc_batch_size = hierarchy["arc_batch_size"]

    system_prompt = (
        "You are a senior story architect for long-form commercial fiction. "
        "Plan the macro narrative structure (Acts) for the full novel. Output ONLY valid JSON, no markdown."
        if is_en
        else "你是长篇商业小说的高级故事架构师。规划全书的宏观叙事结构（幕）。输出必须是合法 JSON，不要解释。"
    )

    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Target chapters: {project.target_chapters}\n"
            f"BookSpec: {_json_dumps(book_spec)}\n"
            f"WorldSpec: {_json_dumps(world_spec)}\n"
            f"CastSpec: {_json_dumps(cast_spec)}\n\n"
            f"Divide the full {project.target_chapters}-chapter story into exactly {act_count} Acts (幕).\n"
            "Each act must have a clear emotional arc from entry_state to exit_state.\n\n"
            "Output ONLY valid JSON with this structure:\n"
            '{"acts": [\n'
            "  {\n"
            '    "act_id": "act_01",\n'
            '    "act_index": 0,\n'
            '    "title": "<Act title>",\n'
            '    "chapter_start": 1,\n'
            '    "chapter_end": <end chapter>,\n'
            '    "act_goal": "<what must be accomplished>",\n'
            '    "core_theme": "<one theme word>",\n'
            '    "dominant_emotion": "<dominant emotion>",\n'
            '    "climax_chapter": <chapter number>,\n'
            '    "entry_state": "<protagonist state at start>",\n'
            '    "exit_state": "<protagonist state at end>",\n'
            '    "payoff_promises": ["<specific payoff>"],\n'
            '    "arc_breakdown": [{"arc_index": 0, "chapter_start": 1, "chapter_end": 12, "arc_goal": "..."}],\n'
            '    "is_final_act": false\n'
            "  }\n"
            "]}\n\n"
            "CRITICAL rules:\n"
            "- Acts must be contiguous: act_01 ends where act_02 begins\n"
            f"- Total chapters across all acts must equal exactly {project.target_chapters}\n"
            f"- Each act: ~{project.target_chapters // act_count} chapters on average (can vary ±30%)\n"
            "- payoff_promises: 2-4 per act, specific and emotionally satisfying\n"
            f"- arc_breakdown: each act should have ~{arc_batch_size}-chapter arcs\n"
            "- Last act must have is_final_act: true and include resolution_contract"
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"目标章节：{project.target_chapters}\n"
            f"BookSpec：{_json_dumps(book_spec)}\n"
            f"WorldSpec：{_json_dumps(world_spec)}\n"
            f"CastSpec：{_json_dumps(cast_spec)}\n\n"
            f"将全书 {project.target_chapters} 章分为恰好 {act_count} 幕（Act）。\n"
            "每幕必须有从 entry_state 到 exit_state 的清晰情感弧。\n\n"
            "输出格式（纯 JSON，无 markdown）：\n"
            '{"acts": [\n'
            "  {\n"
            '    "act_id": "act_01",\n'
            '    "act_index": 0,\n'
            '    "title": "<幕标题>",\n'
            '    "chapter_start": 1,\n'
            '    "chapter_end": <结束章号>,\n'
            '    "act_goal": "<本幕必须完成的叙事目标>",\n'
            '    "core_theme": "<一个主题词，如 觉醒|崛起|危机|蜕变|决战>",\n'
            '    "dominant_emotion": "<主导情绪：热血|紧张|压抑|震撼|爽快|满足>",\n'
            '    "climax_chapter": <章号>,\n'
            '    "entry_state": "<主角在幕初的状态>",\n'
            '    "exit_state": "<主角在幕末的状态>",\n'
            '    "payoff_promises": ["<具体爽点承诺>"],\n'
            '    "arc_breakdown": [{"arc_index": 0, "chapter_start": 1, "chapter_end": 12, "arc_goal": "..."}],\n'
            '    "is_final_act": false\n'
            "  }\n"
            "]}\n\n"
            "【硬性要求】\n"
            "- 各幕章节范围必须首尾相接，不允许间隙或重叠\n"
            f"- 所有幕的章节总数必须恰好等于 {project.target_chapters}\n"
            f"- 每幕平均约 {project.target_chapters // act_count} 章（允许 ±30%）\n"
            "- payoff_promises：每幕 2-4 个，必须具体到读者能感受到的爽点\n"
            f"- arc_breakdown：每幕按 ~{arc_batch_size} 章一弧细分\n"
            "- 最后一幕必须标记 is_final_act: true 并包含 resolution_contract"
        )
    )

    return system_prompt, user_prompt


# ── Multi-Force Conflict Taxonomy ──────────────────────────────────
# Each volume should present a *different type* of challenge rather
# than endlessly repeating "antagonist keeps pressuring."

_CONFLICT_PHASE_TYPES: list[str] = [
    "survival",           # 直接生存威胁
    "political_intrigue",  # 权力博弈与暗中布局
    "betrayal",           # 信任崩塌与背刺
    "faction_war",        # 多方势力全面对抗
    "existential_threat",  # 终极威胁与最大牺牲
    "internal_reckoning",  # 内心拷问与自我面对
]

_VOLUME_OBSTACLE_TEMPLATES: dict[str, str] = {
    "survival": "{force_name}带来直接生存压力——{protagonist}必须先活下来才能图更远的事。",
    "political_intrigue": "{force_name}在暗中布局，{protagonist}必须看穿复杂的权力交易才能找到出路。",
    "betrayal": "来自{force_name}的背刺让{protagonist}失去了最信任的依靠，必须在废墟中重建。",
    "faction_war": "{force_name}发动了全面攻势，{protagonist}被卷入多方博弈的泥潭。",
    "existential_threat": "{force_name}的终极计划威胁着整个世界的根基，{protagonist}必须做出最大的牺牲。",
    "internal_reckoning": "{protagonist}面对来自内心深处的拷问——{force_name}逼迫他直面自己一直在逃避的真相。",
}

_VOLUME_CLIMAX_TEMPLATES: dict[str, str] = {
    "survival": "{protagonist}在生死边缘完成一次绝地反击，暂时挣脱{force_name}的控制。",
    "political_intrigue": "{protagonist}揭开了{force_name}布下的一层权力陷阱，但发现背后还有更深的棋局。",
    "betrayal": "{protagonist}在信任崩塌后做出一个痛苦的抉择，切断了与{force_name}的最后牵绊。",
    "faction_war": "多方势力在决战中重新洗牌，{protagonist}凭借关键情报扭转了自己的位置。",
    "existential_threat": "{protagonist}以极大的个人代价阻止了{force_name}的终极计划的第一阶段。",
    "internal_reckoning": "{protagonist}直面内心最深处的恐惧，完成了真正意义上的蜕变。",
}

_CHAPTER_CONFLICT_TEMPLATES: dict[str, dict[str, str]] = {
    "survival": {
        "setup": "{protagonist}发现自身处境远比预想的危险，{force_name}的威胁已经逼到眼前。",
        "investigation": "{protagonist}在{force_name}的压力下搜寻生存资源和潜在的逃生路线。",
        "pressure": "{force_name}收紧了包围圈，{protagonist}必须在有限时间内做出取舍。",
        "reversal": "局势突然逆转——{protagonist}找到了反击{force_name}的意外切入口。",
        "climax": "{protagonist}和{force_name}正面交锋，这场生存之战到了最后关头。",
    },
    "political_intrigue": {
        "setup": "{protagonist}开始察觉{force_name}在暗处布下的权力网络。",
        "investigation": "{protagonist}深入{force_name}的势力版图，试图找到关键弱点。",
        "pressure": "{force_name}的政治手段让{protagonist}的盟友开始动摇。",
        "reversal": "{protagonist}发现了一个可以反制{force_name}的隐秘情报。",
        "climax": "权力博弈的棋盘上，{protagonist}和{force_name}的角力到了决定性时刻。",
    },
    "betrayal": {
        "setup": "{protagonist}身边出现了令人不安的信号——{force_name}的真面目开始显露。",
        "investigation": "{protagonist}在不确定中追查{force_name}背叛的线索。",
        "pressure": "背叛的证据越来越多，{protagonist}的信任体系正在崩塌。",
        "reversal": "真相大白——{force_name}的背叛比想象中更深远，但也暴露了新的机会。",
        "climax": "{protagonist}必须在被背叛的痛苦中做出最艰难的决定。",
    },
    "faction_war": {
        "setup": "多方势力的矛盾激化，{protagonist}被卷入{force_name}引发的冲突漩涡。",
        "investigation": "{protagonist}试图在{force_name}主导的混战中找到自己的立足点。",
        "pressure": "{force_name}的攻势让{protagonist}的阵地岌岌可危。",
        "reversal": "战局中出现意外变量，{protagonist}抓住了扭转与{force_name}对抗的机会。",
        "climax": "全面对抗的最终战场上，{protagonist}必须在{force_name}的围攻中打开突破口。",
    },
    "existential_threat": {
        "setup": "{force_name}的终极威胁浮出水面，{protagonist}意识到这远超之前所有挑战。",
        "investigation": "{protagonist}拼命搜寻能对抗{force_name}的终极手段。",
        "pressure": "{force_name}的计划正在不可逆转地推进，留给{protagonist}的时间越来越少。",
        "reversal": "一个意想不到的发现让{protagonist}看到了对抗{force_name}的最后希望。",
        "climax": "终极对决——{protagonist}以最大的牺牲和{force_name}展开命运之战。",
    },
    "internal_reckoning": {
        "setup": "{protagonist}开始被内心深处的矛盾所困扰——{force_name}触及了他最脆弱的地方。",
        "investigation": "{protagonist}在{force_name}的逼迫下回溯自己一路走来的选择。",
        "pressure": "{force_name}让{protagonist}无法再逃避——必须直面最不愿面对的真相。",
        "reversal": "{protagonist}在崩溃的边缘找到了内心深处真正的答案。",
        "climax": "{protagonist}完成了精神上的蜕变，以全新的姿态回应{force_name}的终极拷问。",
    },
}


def _assign_conflict_phases(volume_count: int) -> list[str]:
    """Assign a conflict phase type to each volume based on total volume count."""
    phases = _CONFLICT_PHASE_TYPES
    if volume_count <= 1:
        return ["survival"]
    if volume_count == 2:
        return ["survival", "existential_threat"]
    if volume_count == 3:
        return ["survival", "political_intrigue", "existential_threat"]
    if volume_count == 4:
        return ["survival", "political_intrigue", "betrayal", "existential_threat"]
    if volume_count == 5:
        return ["survival", "political_intrigue", "betrayal", "faction_war", "existential_threat"]
    # 6+ volumes: keep first and last fixed, cycle middle phases for extras
    first = phases[0]       # always survival
    last = phases[-1]       # always internal_reckoning
    middle = phases[1:-1]   # intrigue, betrayal, faction_war, existential_threat
    result: list[str] = [first]
    extra = volume_count - 2  # slots between first and last
    for i in range(extra):
        result.append(middle[i % len(middle)])
    result.append(last)
    return result


def _phase_name(index_within_volume: int, total_in_volume: int) -> str:
    ratio = index_within_volume / max(total_in_volume, 1)
    if ratio <= 0.2:
        return "setup"
    if ratio <= 0.5:
        return "investigation"
    if ratio <= 0.75:
        return "pressure"
    if ratio <= 0.9:
        return "reversal"
    return "climax"


def _hook_type(index_within_volume: int, total_in_volume: int) -> str:
    phase = _phase_name(index_within_volume, total_in_volume)
    mapping = {
        "setup": "信息揭示",
        "investigation": "冲突升级",
        "pressure": "危机悬念",
        "reversal": "反转",
        "climax": "行动截断",
    }
    return mapping[phase]


# Extended scene type taxonomy for pacing diversity.
# After high-tension phases, insert low-tension scene types to create rhythm.
_SCENE_TYPE_AFTER_CLIMAX = ["aftermath", "introspection", "relationship_building"]
_SCENE_TYPE_AFTER_PRESSURE = ["preparation", "worldbuilding_discovery"]
_SCENE_TYPE_COMIC_INTERVAL = 7  # Insert comic relief every N chapters

_FALLBACK_TITLE_PREFIXES = [
    "暗潮",
    "盲区",
    "裂痕",
    "回声",
    "风眼",
    "余烬",
    "伏线",
    "变局",
    "断点",
    "逆流",
    "边界",
    "悬灯",
]
_FALLBACK_TITLE_SUFFIXES = {
    "setup": ["初现", "入局", "投石", "试探", "铺火", "露锋"],
    "investigation": ["追索", "摸底", "拆解", "寻隙", "探针", "回查"],
    "pressure": ["加压", "围拢", "失衡", "封锁", "死线", "逼近"],
    "reversal": ["反咬", "逆转", "偏航", "脱钩", "换轨", "回火"],
    "climax": ["爆裂", "截断", "崩口", "闯线", "归零", "掀牌"],
}
_FALLBACK_TITLE_PREFIXES_EN = [
    "Storm",
    "Ash",
    "Iron",
    "Glass",
    "Night",
    "Ember",
    "Shadow",
    "Signal",
    "Hollow",
    "Rift",
    "Cinder",
    "Cipher",
]
_FALLBACK_TITLE_SUFFIXES_EN = {
    "setup": ["Wake", "Threshold", "First Light", "Opening Move", "Spark", "Edge"],
    "investigation": ["Trace", "Crossing", "Faultline", "Search", "Probe", "Ledger"],
    "pressure": ["Lockdown", "Deadline", "Pressure", "Siege", "Choke Point", "Breaking Point"],
    "reversal": ["Countermove", "Turn", "Slip", "Backfire", "Pivot", "Undoing"],
    "climax": ["Rupture", "Burn", "Cutline", "Zero Hour", "Collapse", "Last Gate"],
}


def _chapter_fallback_subtitle(
    chapter_number: int,
    phase: str,
    index_within_volume: int,
    volume_number: int,
    *,
    language: str | None = None,
    is_opening: bool,
) -> str:
    """Build a concise, genre-neutral fallback subtitle.

    Earlier versions clipped ``volume_goal`` into titles like
    ``"沈渡需要在本卷内拿到一组·01"``. Those strings were technically unique
    because of the ordinal suffix, but they read like planning notes rather
    than chapter names. The fallback now composes a short title from a stable
    prefix lexicon plus a phase-aware suffix lexicon so the result stays
    compact, readable, and deterministic even when the outline LLM times out.
    """
    is_en = is_english_language(language)
    suffix_map = _FALLBACK_TITLE_SUFFIXES_EN if is_en else _FALLBACK_TITLE_SUFFIXES
    phase_key = phase if phase in suffix_map else "investigation"
    prefixes = _FALLBACK_TITLE_PREFIXES_EN if is_en else _FALLBACK_TITLE_PREFIXES
    suffixes = suffix_map[phase_key]

    prefix_index = (chapter_number + (volume_number * 2) - 2) % len(prefixes)
    suffix_index = (index_within_volume + (chapter_number * 3) + (1 if is_opening else 0)) % len(suffixes)
    if is_en:
        return f"{prefixes[prefix_index]} {suffixes[suffix_index]}".strip()
    return f"{prefixes[prefix_index]}{suffixes[suffix_index]}"


def _varied_scene_type(
    base_type: str,
    chapter_number: int,
    scene_number: int,
    phase: str,
    prev_phase: str | None,
) -> str:
    """Choose a richer scene type based on pacing context.

    Expands the original 5-type system (hook/setup/transition/conflict/reveal)
    with: introspection, relationship_building, worldbuilding_discovery,
    aftermath, preparation, comic_relief, montage.
    """
    # After a climax or reversal chapter, first scene should be aftermath/introspection
    if scene_number == 1 and prev_phase in ("climax", "reversal") and phase in ("setup", "investigation"):
        return _SCENE_TYPE_AFTER_CLIMAX[chapter_number % len(_SCENE_TYPE_AFTER_CLIMAX)]
    # Middle scenes in investigation phase can be relationship or worldbuilding
    if scene_number == 2 and phase == "investigation":
        return _SCENE_TYPE_AFTER_PRESSURE[chapter_number % len(_SCENE_TYPE_AFTER_PRESSURE)]
    # Periodic comic relief
    if chapter_number % _SCENE_TYPE_COMIC_INTERVAL == 0 and scene_number == 1 and phase not in ("climax", "reversal"):
        return "comic_relief"
    return base_type


def _render_chapter_conflict(conflict_phase: str, chapter_phase: str, protagonist: str, force_name: str) -> str:
    """Generate a chapter-level main_conflict string from the volume's conflict phase and chapter phase."""
    templates = _CHAPTER_CONFLICT_TEMPLATES.get(conflict_phase, _CHAPTER_CONFLICT_TEMPLATES["survival"])
    template = templates.get(chapter_phase, templates.get("investigation", "{protagonist}推进调查。"))
    return template.format(protagonist=protagonist, force_name=force_name)


def _phase_name_within_arc(index: int, total: int) -> str:
    """Determine the narrative phase of a chapter within its arc.

    More granular than the per-volume 5-phase system, providing finer
    narrative rhythm control within each 12-chapter arc.
    """
    ratio = index / max(total, 1)
    if ratio <= 0.13:
        return "hook"
    if ratio <= 0.33:
        return "setup"
    if ratio <= 0.53:
        return "escalation"
    if ratio <= 0.73:
        return "twist"
    if ratio <= 0.87:
        return "climax"
    return "resolution_hook"


def _compute_chapter_arc_info(
    chapter_number: int,
    volume_plan: list[dict[str, Any]],
) -> tuple[int, str]:
    """Find which arc a chapter belongs to and its phase within that arc.

    Returns (arc_index, arc_phase). arc_index is global across the whole book.
    """
    global_arc_index = 0
    for vol in volume_plan:
        vol_map = _mapping(vol)
        arc_ranges = vol_map.get("arc_ranges")
        if not isinstance(arc_ranges, list):
            # Volume has no arc_ranges — treat entire volume as one arc
            ch_count = max(int(vol_map.get("chapter_count_target") or 1), 1)
            vol_start = _compute_volume_start(vol_map, volume_plan)
            vol_end = vol_start + ch_count - 1
            if vol_start <= chapter_number <= vol_end:
                idx_in_arc = chapter_number - vol_start
                total_in_arc = ch_count
                return global_arc_index, _phase_name_within_arc(idx_in_arc, total_in_arc)
            global_arc_index += 1
            continue
        for arc_range in arc_ranges:
            if isinstance(arc_range, list) and len(arc_range) == 2:
                arc_start, arc_end = arc_range
                if arc_start <= chapter_number <= arc_end:
                    idx_in_arc = chapter_number - arc_start
                    total_in_arc = arc_end - arc_start + 1
                    return global_arc_index, _phase_name_within_arc(idx_in_arc, total_in_arc)
                global_arc_index += 1
    return 0, "setup"


def _compute_volume_start(vol_map: dict[str, Any], volume_plan: list[dict[str, Any]]) -> int:
    """Compute the start chapter of a volume from the plan."""
    vol_num = vol_map.get("volume_number", 1)
    cursor = 1
    for v in volume_plan:
        v_map = _mapping(v)
        if v_map.get("volume_number") == vol_num:
            return cursor
        cursor += max(int(v_map.get("chapter_count_target") or 1), 1)
    return cursor


def _fallback_chapter_outline_batch(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    writing_profile = _planner_writing_profile(project)
    cast_payload = _mapping(cast_spec)
    protagonist_name = _non_empty_string(_mapping(cast_payload.get("protagonist")).get("name"), "主角")
    supporting_cast = _mapping_list(cast_payload.get("supporting_cast"))
    ally_name = _non_empty_string(_named_item(supporting_cast, 0, protagonist_name).get("name"), protagonist_name)
    antagonist_name = _non_empty_string(_mapping(cast_payload.get("antagonist")).get("name"), "敌人")
    normalized_volume_plan = _mapping_list(volume_plan)
    if not normalized_volume_plan:
        normalized_volume_plan = [
            {
                "volume_number": 1,
                "chapter_count_target": max(project.target_chapters, 1),
                "volume_goal": "推动主线调查取得关键进展",
            }
        ]
    # Build antagonist-character lookup from supporting_cast for scene participants
    _antag_chars: dict[str, str] = {}
    for sc in supporting_cast:
        sc_map = _mapping(sc)
        if _non_empty_string(sc_map.get("role"), "") == "antagonist":
            sc_name = _non_empty_string(sc_map.get("name"), "")
            if sc_name:
                _antag_chars[sc_name] = sc_name

    # Normalize antagonist_forces once — handle both Pydantic models and raw dicts
    raw_forces = cast_payload.get("antagonist_forces") or []
    if not isinstance(raw_forces, list):
        raw_forces = []
    normalized_forces: list[dict[str, Any]] = []
    for f in raw_forces:
        if isinstance(f, dict):
            normalized_forces.append(f)
        elif hasattr(f, "model_dump"):
            normalized_forces.append(f.model_dump())
        else:
            normalized_forces.append(_mapping(f))

    chapters: list[dict[str, Any]] = []
    chapter_number = 1
    chapter_target_words = max(5000, int(project.target_word_count / max(project.target_chapters, 1)))
    scene_target_words = max(900, int(chapter_target_words / 3))
    prev_phase: str | None = None
    for raw_volume_index, volume in enumerate(normalized_volume_plan, start=1):
        volume_payload = _mapping(volume)
        total_in_volume = max(int(volume_payload.get("chapter_count_target") or 1), 1)
        volume_goal = _non_empty_string(volume_payload.get("volume_goal"), "推动主线调查取得关键进展")
        volume_number = int(volume_payload.get("volume_number") or raw_volume_index)
        # Extract per-volume conflict phase and force name
        conflict_phase = _non_empty_string(volume_payload.get("conflict_phase"), "survival")
        volume_force_name = _non_empty_string(volume_payload.get("primary_force_name"), antagonist_name)
        # Determine the primary antagonist character for this volume's scenes
        volume_antag_participant = antagonist_name  # default
        for af in normalized_forces:
            active_vols = af.get("active_volumes") or []
            if isinstance(active_vols, list) and volume_number in active_vols:
                char_ref = _non_empty_string(af.get("character_ref"), "")
                if char_ref:
                    volume_antag_participant = char_ref
                break

        for index_within_volume in range(1, total_in_volume + 1):
            phase = _phase_name(index_within_volume, total_in_volume)
            is_opening_chapter = chapter_number <= 3

            # Ending contract: force specific goals for the last 3 chapters
            total_ch = max(project.target_chapters, 1)
            is_en = is_english_language(project.language)
            chapters_from_end = total_ch - chapter_number

            if chapters_from_end == 2:
                chapter_goal = (
                    "Final preparations before the ultimate confrontation — all foreshadowing threads converge."
                    if is_en
                    else f"决战前最后准备——{protagonist_name}的所有伏笔汇聚，各方力量到位。"
                )
            elif chapters_from_end == 1:
                chapter_goal = (
                    "The ultimate confrontation or core mystery revealed — the story's central conflict reaches its peak."
                    if is_en
                    else f"终极对决或核心悬念揭晓——{protagonist_name}与命运正面交锋。"
                )
            elif chapters_from_end == 0:
                chapter_goal = (
                    "Resolution landing — emotional closure, lingering resonance, and the final image."
                    if is_en
                    else f"结局着陆——{protagonist_name}的情感收束，余韵留白，最终画面。"
                )
            else:
                chapter_goal = (
                    f"{protagonist_name}在第{chapter_number}章推进{volume_goal}，"
                    f"并迫使局势进入新的高压阶段。"
                )
            scenes = [
                {
                    "scene_number": 1,
                    "scene_type": "hook" if is_opening_chapter else _varied_scene_type(
                        "setup" if phase == "setup" else "transition",
                        chapter_number, 1, phase, prev_phase,
                    ),
                    # Short subtitle only — downstream renderers must NOT
                    # concatenate a "第N章" prefix. Avoids the double-prefix
                    # and title-cycle bugs.
                    "title": "第一时间亮出主角优势" if chapter_number == 1 else "开场压力",
                    # time_label is plain phase text. Historically this read
                    # "第N章开场" / "第N章中段" / "第N章结尾" and those
                    # strings leaked into the rewrite-fallback template prose
                    # ("第13章中段，程彻…"). Keep it generic.
                    "time_label": "章节开场",
                    "participants": [protagonist_name, ally_name],
                    "purpose": {
                        "story": (
                            "快速亮出主角差异化优势、当前利益和逼近的危险"
                            if is_opening_chapter
                            else "承接上章后果并给出当前行动目标"
                        ),
                        "emotion": (
                            "先给读者明确吸引点，再持续拉高压力和不确定性"
                            if is_opening_chapter
                            else "持续拉高压力和不确定性"
                        ),
                    },
                    "entry_state": {
                        protagonist_name: {"arc_state": "承压推进", "emotion": "紧绷"},
                        ally_name: {"arc_state": "谨慎协作", "emotion": "戒备"},
                    },
                    "exit_state": {
                        protagonist_name: {"arc_state": "主动出击", "emotion": "更坚定"},
                        ally_name: {"arc_state": "被迫跟进", "emotion": "压力上升"},
                    },
                    "target_word_count": scene_target_words,
                },
                {
                    "scene_number": 2,
                    "scene_type": _varied_scene_type(
                        "conflict" if phase in {"pressure", "reversal", "climax"} else "reveal",
                        chapter_number, 2, phase, prev_phase,
                    ),
                    "title": "关键碰撞",
                    "time_label": "章节中段",
                    "participants": [protagonist_name, volume_antag_participant]
                    if index_within_volume % 2 == 0
                    else [protagonist_name],
                    "purpose": {
                        "story": "让主角拿到一条新线索，同时付出新的代价",
                        "emotion": "把悬念和敌意推高到下一层",
                    },
                    "entry_state": {
                        protagonist_name: {"arc_state": "带着怀疑推进", "emotion": "警觉"},
                    },
                    "exit_state": {
                        protagonist_name: {"arc_state": "掌握更多真相", "emotion": "不安"},
                        antagonist_name: {"arc_state": "开始主动压制", "emotion": "冷静施压"},
                    },
                    "target_word_count": scene_target_words,
                },
                {
                    "scene_number": 3,
                    "scene_type": "hook",
                    "title": "结尾钩子",
                    "time_label": "章节结尾",
                    "participants": [protagonist_name, ally_name]
                    if index_within_volume % 3 != 0
                    else [protagonist_name, volume_antag_participant],
                    "purpose": {
                        "story": writing_profile.market.chapter_hook_strategy,
                        "emotion": "让读者必须继续追下一章",
                    },
                    "entry_state": {
                        protagonist_name: {"arc_state": "准备收束", "emotion": "短暂控制局势"},
                    },
                    "exit_state": {
                        protagonist_name: {"arc_state": "被迫进入更难局面", "emotion": "强压下前进"},
                    },
                    "target_word_count": scene_target_words,
                },
            ]
            # Compute arc-level info for this chapter
            arc_index, arc_phase = _compute_chapter_arc_info(chapter_number, normalized_volume_plan)

            chapters.append(
                {
                    "chapter_number": chapter_number,
                    # NOTE: title intentionally left as a SHORT subtitle without
                    # any "第N章" prefix. The chapter header renderer
                    # (``drafts._format_chapter_heading``) is responsible for
                    # re-attaching the canonical "第N章：" prefix exactly once,
                    # which prevents the "# 第1章 第1章：…" double-prefix bug.
                    #
                    # Previously this fell back to an 8-word hard-coded cycle
                    # (``封锁/碰撞/反咬/闯关/断局/逼近/裂缝/追线``) indexed by
                    # ``chapter_number % 8``, which produced visibly repeating
                    # titles every 8 chapters in the output. We now either
                    # derive the subtitle from the chapter phase / position
                    # so the result still reads like a chapter title rather
                    # than a clipped planning note.
                    "title": _chapter_fallback_subtitle(
                        chapter_number,
                        phase,
                        index_within_volume,
                        volume_number,
                        language=project.language,
                        is_opening=(chapter_number == 1),
                    ),
                    "goal": chapter_goal,
                    "opening_situation": (
                        writing_profile.serialization.opening_mandate
                        if chapter_number == 1
                        else "承接上一章尾钩，主角没有空档去长篇解释设定。"
                    ),
                    "main_conflict": _render_chapter_conflict(conflict_phase, phase, protagonist_name, volume_force_name),
                    "hook_type": _hook_type(index_within_volume, total_in_volume),
                    "hook_description": writing_profile.market.chapter_hook_strategy,
                    "volume_number": volume_number,
                    "arc_index": arc_index,
                    "arc_phase": arc_phase,
                    "target_word_count": chapter_target_words,
                    "scenes": scenes,
                }
            )
            prev_phase = phase
            chapter_number += 1
    return {"batch_name": "auto-generated-full-outline", "chapters": chapters}


def _book_spec_prompts(project: ProjectModel, premise: str, fallback: dict[str, Any]) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"book_spec_system_{_lang_key}", "")
    system_prompt = (
        "You are an English-language commercial fiction planner. "
        "Output valid JSON only. Build a marketable serial-fiction story engine, not literary commentary."
        if is_en
        else (
            "你是长篇中文小说的故事策划师。"
            "输出必须是合法 JSON，不要解释。"
            "你要产出的是适合中文网文平台连载的商业小说骨架，而不是文学评论。"
        )
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_book_spec = f"{render_prompt_pack_fragment(prompt_pack, 'planner_book_spec')}\n" if prompt_pack else ""
    if is_en:
        user_prompt = (
            f"Project title: {project.title}\n"
            f"Genre: {project.genre}\n"
            f"Target words: {project.target_word_count}\n"
            f"Target chapters: {project.target_chapters}\n"
            f"Audience: {project.audience or 'web-serial'}\n"
            f"Premise: {premise}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_book_spec}"
            "Generate a BookSpec JSON with title, logline, genre, target_audience, tone, themes, protagonist, stakes, and series_engine. "
            "Inside series_engine, explicitly define the core serial engine, reader promise, first-three-chapter hook, chapter-ending hook strategy, and the rhythm of short and long payoffs."
        )
    else:
        user_prompt = (
            f"项目标题：{project.title}\n"
            f"类型：{project.genre}\n"
            f"目标字数：{project.target_word_count}\n"
            f"目标章节：{project.target_chapters}\n"
            f"受众：{project.audience or 'web-serial'}\n"
            f"Premise：{premise}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_pp_book_spec}"
            "请生成一个 BookSpec JSON，包含 title、logline、genre、target_audience、tone、themes、"
            "protagonist、stakes、series_engine。"
            "其中 series_engine 必须清楚写出：核心连载引擎、读者承诺、前三章抓手、章节尾钩策略、"
            "短回报与长回报的节奏安排。"
        )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"book_spec_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    return system_prompt, user_prompt


def _world_spec_prompts(project: ProjectModel, premise: str, book_spec: dict[str, Any]) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"world_spec_system_{_lang_key}", "")
    system_prompt = (
        "You are a world-building designer for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说世界观设计师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_world_spec = f"{render_prompt_pack_fragment(prompt_pack, 'planner_world_spec')}\n" if prompt_pack else ""
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Genre: {project.genre}\n"
            f"Premise: {premise}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec: {_json_dumps(book_spec)}\n"
            f"{_pp_world_spec}"
            "Generate a WorldSpec JSON with world_name, world_premise, rules, power_system, locations, factions, power_structure, history_key_events, and forbidden_zones. "
            "World rules must create conflict, cost, upgrade space, and conspiracy leverage rather than empty lore."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"类型：{project.genre}\n"
            f"Premise：{premise}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec：{_json_dumps(book_spec)}\n"
            f"{_pp_world_spec}"
            "请生成一个 WorldSpec JSON，包含 world_name、world_premise、rules、power_system、locations、"
            "factions、power_structure、history_key_events、forbidden_zones。"
            "要求世界规则能直接制造冲突、爽点成本、升级空间和阴谋推进空间，不要只写空背景。"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"world_spec_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    return system_prompt, user_prompt


def _cast_spec_prompts(project: ProjectModel, book_spec: dict[str, Any], world_spec: dict[str, Any]) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    prompt_pack = _planner_prompt_pack(project)
    era_hint = _detect_era_from_genre(project.genre)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"cast_spec_system_{_lang_key}", "")
    system_prompt = (
        "You are a cast architect for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说角色架构师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_cast_spec = f"{render_prompt_pack_fragment(prompt_pack, 'planner_cast_spec')}\n" if prompt_pack else ""
    user_prompt = (
        (
            f"BookSpec: {_json_dumps(book_spec)}\n"
            f"WorldSpec: {_json_dumps(world_spec)}\n"
            f"Era / setting hint: {era_hint}\n"
            "Write all planning artifacts in English.\n"
            f"{_pp_block}"
            f"{_pp_cast_spec}"
            "Generate a CastSpec JSON with protagonist, antagonist, antagonist_forces, supporting_cast, and conflict_map. "
            "The protagonist needs a vivid desire, a real weakness, visible growth space, and a memorable edge; the antagonist must actively counter the protagonist and keep escalating. "
            "Every major character must include a voice_profile object and a moral_framework object so their speech patterns stay distinct.\n\n"
            "IMPORTANT — antagonist_forces:\n"
            "- Include an 'antagonist_forces' array with 2-4 conflict forces\n"
            "- Each force: {name, force_type (character/faction/environment/internal/systemic), active_volumes, threat_description, escalation_path}\n"
            "- Each volume should face a DIFFERENT type of challenge — don't repeat the same antagonist pressure\n"
            "- Mix visible and hidden threats for rich plotline interweaving\n\n"
            "Naming rules:\n"
            f"- Names must fit the {project.genre} genre and the {era_hint} setting\n"
            "- Core cast names should be memorable, readable, and easy to distinguish in dialogue\n"
            "- Avoid confusingly similar names or generic placeholder naming\n"
            "- Antagonist names may imply personality, but stay subtle\n"
            "- Every character must include a name_reasoning field"
        )
        if is_en
        else (
            f"BookSpec：{_json_dumps(book_spec)}\n"
            f"WorldSpec：{_json_dumps(world_spec)}\n"
            f"题材时代：{era_hint}\n"
            f"{_pp_block}"
            f"{_pp_cast_spec}"
            "请生成一个 CastSpec JSON，包含 protagonist、antagonist、antagonist_forces、supporting_cast、conflict_map。"
            "主角必须有鲜明欲望、明显短板、可持续升级点和可被读者快速记住的差异化优势；"
            "反派必须能持续升级并主动反制主角；配角要形成明确功能位和关系张力。\n"
            "\n【重要——多力量冲突设计】\n"
            "必须包含 antagonist_forces 数组（2-4个冲突力量），每个包含：\n"
            "name, force_type(character/faction/environment/internal/systemic), active_volumes, threat_description, escalation_path\n"
            "每卷应面对不同类型的挑战——不要全书只有一个反派在施压\n"
            "要有明线冲突和暗线伏笔的交织\n\n"
            "每个角色必须包含 voice_profile 对象（speech_register、verbal_tics、sentence_style、"
            "emotional_expression、mannerisms）和 moral_framework 对象（core_values、"
            "lines_never_crossed、willing_to_sacrifice），确保不同角色的说话方式有明显区分度。\n\n"
            "【角色命名硬性要求】\n"
            f"- 角色名字必须符合「{project.genre}」题材和「{era_hint}」时代背景\n"
            "- 主角名 2-3 字，音调优美朗朗上口，有记忆点\n"
            "- 所有角色的姓氏不能重复\n"
            "- 避免过于生僻的字、谐音不雅的组合、或网文中已经烂大街的名字\n"
            "- 反派名可暗示性格特质但不要太刻意\n"
            "- 每个角色附 name_reasoning 字段说明命名理由"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"cast_spec_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    return system_prompt, user_prompt


def _volume_plan_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    *,
    act_plan: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"volume_plan_system_{_lang_key}", "")
    system_prompt = (
        "You are a structural editor for long-form commercial fiction. Output a valid JSON array only."
        if is_en
        else "你是长篇中文小说结构编辑。输出必须是合法 JSON 数组，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_volume_plan = f"{render_prompt_pack_fragment(prompt_pack, 'planner_volume_plan')}\n" if prompt_pack else ""
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Target words: {project.target_word_count}\n"
            f"Target chapters: {project.target_chapters}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec: {_json_dumps(book_spec)}\n"
            f"WorldSpec: {_json_dumps(world_spec)}\n"
            f"CastSpec: {_json_dumps(cast_spec)}\n"
            f"{_pp_volume_plan}"
            "Generate a VolumePlan JSON array. Each entry must include volume_number, volume_title, volume_theme, chapter_count_target, volume_goal, volume_obstacle, volume_climax, volume_resolution, conflict_phase, and primary_force_name. "
            "CRITICAL: Each volume must face a DIFFERENT primary conflict force from the CastSpec's antagonist_forces. Don't repeat the same antagonist pressure — vary between survival, political intrigue, betrayal, faction warfare, existential threat, etc. "
            "Every volume needs a concrete payoff, escalation, key reveal, volume-end hook, and anticipation for the next volume."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"目标字数：{project.target_word_count}\n"
            f"目标章节：{project.target_chapters}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec：{_json_dumps(book_spec)}\n"
            f"WorldSpec：{_json_dumps(world_spec)}\n"
            f"CastSpec：{_json_dumps(cast_spec)}\n"
            f"{_pp_volume_plan}"
            "请生成 VolumePlan JSON 数组，每个元素包含 volume_number、volume_title、volume_theme、"
            "chapter_count_target、volume_goal、volume_obstacle、volume_climax、volume_resolution、"
            "conflict_phase（冲突类型：survival/political_intrigue/betrayal/faction_war/existential_threat/internal_reckoning）、"
            "primary_force_name（本卷主要冲突力量名称）。"
            "【关键】每卷必须面对不同的冲突力量和冲突类型——不要所有卷都是同一个反派在施压！"
            "每卷都要有清晰的爽点兑现、局势升级、关键揭示、卷尾钩子和下一卷期待。"
        )
    )
    # Inject act plan context when available (multi-act novels)
    if act_plan:
        act_context = _json_dumps(act_plan)
        if is_en:
            user_prompt += (
                f"\n\nActPlan (macro narrative structure):\n{act_context}\n"
                "Each volume must belong to one act. Volume themes and goals must align with "
                "the parent act's core_theme and act_goal. Volumes within the same act should "
                "form a coherent narrative progression."
            )
        else:
            user_prompt += (
                f"\n\n幕计划（全书宏观叙事结构）：\n{act_context}\n"
                "每卷必须隶属于一个幕，主题和目标需与所属幕的 core_theme 和 act_goal 一致。"
                "同一幕内的卷应形成连贯的叙事推进。"
            )

    _genre_instruction = getattr(_genre_profile.planner_prompts, f"volume_plan_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    return system_prompt, user_prompt


def _outline_prompts(project: ProjectModel, book_spec: dict[str, Any], cast_spec: dict[str, Any], volume_plan: list[dict[str, Any]]) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"outline_system_{_lang_key}", "")
    system_prompt = (
        "You are a chapter-outline planner for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说章纲规划师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_outline = f"{render_prompt_pack_fragment(prompt_pack, 'planner_outline')}\n" if prompt_pack else ""
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Target chapters: {project.target_chapters}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"BookSpec: {_json_dumps(book_spec)}\n"
            f"CastSpec: {_json_dumps(cast_spec)}\n"
            f"VolumePlan: {_json_dumps(volume_plan)}\n"
            f"{_pp_outline}"
            "Generate a full ChapterOutlineBatch JSON with batch_name and chapters. Each chapter needs at least 3 scenes. "
            "The first 3 chapters must rapidly establish the protagonist edge, the core anomaly, the first gain/loss cycle, and a strong read-on hook. "
            "Each chapter must define goal, main_conflict, and hook_description; each scene must define story and emotion tasks."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"目标章节：{project.target_chapters}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"BookSpec：{_json_dumps(book_spec)}\n"
            f"CastSpec：{_json_dumps(cast_spec)}\n"
            f"VolumePlan：{_json_dumps(volume_plan)}\n"
            f"{_pp_outline}"
            "请生成完整 ChapterOutlineBatch JSON，包含 batch_name 和 chapters。每章至少 3 个 scenes。"
            "要求：前 3 章必须快速完成主角卖点亮相、核心异常亮相、第一轮得失与追读钩子；"
            "每章都要写明 goal、main_conflict、hook_description；每场都要有 story/emotion 任务。"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"outline_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    return system_prompt, user_prompt


async def _generate_structured_artifact(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    logical_name: str,
    system_prompt: str,
    user_prompt: str,
    fallback_payload: Any,
    workflow_run_id: UUID,
    step_run_id: UUID | None = None,
    validator: Callable[[Any], Any] | None = None,
) -> tuple[Any, UUID | None]:
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="planner",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=_json_dumps(fallback_payload),
            prompt_template=f"planner_{logical_name}",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "project_slug": project.slug,
                "artifact": logical_name,
            },
        ),
    )
    try:
        payload = _merge_planning_payload(fallback_payload, _extract_json_payload(completion.content))
        if validator is not None:
            validator(payload)
    except Exception:
        payload = fallback_payload
    return payload, completion.llm_run_id


async def generate_novel_plan(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    premise: str,
    *,
    requested_by: str = "system",
) -> NovelPlanningResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_GENERATE_NOVEL_PLAN,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="store_premise",
        metadata={"project_slug": project.slug, "premise": premise},
    )
    step_order = 1
    current_step_name = "store_premise"
    llm_run_ids: list[UUID] = []
    artifact_records: list[PlanningArtifactRecord] = []

    try:
        premise_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.PREMISE,
                content={"premise": premise},
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.PREMISE,
                artifact_id=premise_artifact.id,
                version_no=premise_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(premise_artifact.id)},
        )
        step_order += 1

        # Generate character names via LLM up-front so every downstream
        # fallback (book_spec / world_spec / cast_spec) sees real, contextual
        # names instead of regex-extracted fragments or generic pool defaults.
        # If the LLM call fails, _generate_character_names falls back to the
        # curated genre pool — never to regex on premise.
        character_name_pool = await _generate_character_names(
            session,
            settings,
            genre=project.genre,
            sub_genre=project.sub_genre or "",
            language=project.language,
            premise=premise,
            book_spec={},
            workflow_run_id=workflow_run.id,
            project_id=project.id,
        )
        llm_protagonist_name = (
            _mapping(character_name_pool.get("protagonist")).get("name")
            or _genre_name_pool(project.genre, language=project.language)["protagonist"]["name"]
        )

        book_spec_fallback = _fallback_book_spec(project, premise)
        # Override placeholder name with LLM-designed one so the LLM book_spec
        # call sees the same protagonist name in its fallback context.
        if isinstance(book_spec_fallback.get("protagonist"), dict):
            book_spec_fallback["protagonist"]["name"] = llm_protagonist_name
        current_step_name = "generate_book_spec"
        workflow_run.current_step = current_step_name
        book_system, book_user = _book_spec_prompts(project, premise, book_spec_fallback)
        book_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="book_spec",
            system_prompt=book_system,
            user_prompt=book_user,
            fallback_payload=book_spec_fallback,
            workflow_run_id=workflow_run.id,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        book_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.BOOK_SPEC, content=book_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.BOOK_SPEC,
                artifact_id=book_artifact.id,
                version_no=book_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(book_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        world_spec_fallback = _fallback_world_spec(project, premise, book_spec_payload)
        current_step_name = "generate_world_spec"
        workflow_run.current_step = current_step_name
        world_system, world_user = _world_spec_prompts(project, premise, book_spec_payload)
        world_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="world_spec",
            system_prompt=world_system,
            user_prompt=world_user,
            fallback_payload=world_spec_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_world_spec_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        world_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.WORLD_SPEC, content=world_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.WORLD_SPEC,
                artifact_id=world_artifact.id,
                version_no=world_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(world_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        cast_spec_fallback = _fallback_cast_spec(project, premise, book_spec_payload, world_spec_payload)
        current_step_name = "generate_cast_spec"
        workflow_run.current_step = current_step_name
        cast_system, cast_user = _cast_spec_prompts(project, book_spec_payload, world_spec_payload)
        cast_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec",
            system_prompt=cast_system,
            user_prompt=cast_user,
            fallback_payload=cast_spec_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_cast_spec_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        cast_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.CAST_SPEC, content=cast_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CAST_SPEC,
                artifact_id=cast_artifact.id,
                version_no=cast_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(cast_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        # ── Act Plan: macro narrative structure for long novels ──
        hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        act_plan_payload: list[dict[str, Any]] | None = None
        if hierarchy["act_count"] > 1 and project.target_chapters > settings.pipeline.act_plan_threshold:
            act_plan_fallback = _fallback_act_plan(project, book_spec_payload, cast_spec_payload, world_spec_payload)
            current_step_name = "generate_act_plan"
            workflow_run.current_step = current_step_name
            act_system, act_user = _act_plan_prompts(project, book_spec_payload, world_spec_payload, cast_spec_payload)
            act_plan_payload_raw, llm_run_id = await _generate_structured_artifact(
                session,
                settings,
                project=project,
                logical_name="act_plan",
                system_prompt=act_system,
                user_prompt=act_user,
                fallback_payload={"acts": act_plan_fallback},
                workflow_run_id=workflow_run.id,
            )
            if llm_run_id is not None:
                llm_run_ids.append(llm_run_id)
            # Extract acts list from payload (may be {"acts": [...]} or [...])
            if isinstance(act_plan_payload_raw, dict) and "acts" in act_plan_payload_raw:
                act_plan_payload = act_plan_payload_raw["acts"]
            elif isinstance(act_plan_payload_raw, list):
                act_plan_payload = act_plan_payload_raw
            else:
                act_plan_payload = act_plan_fallback

            act_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(artifact_type=ArtifactType.ACT_PLAN, content={"acts": act_plan_payload}),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.ACT_PLAN,
                    artifact_id=act_artifact.id,
                    version_no=act_artifact.version_no,
                )
            )
            # Persist act plan to project metadata
            from bestseller.services.story_bible import upsert_act_plan
            await upsert_act_plan(session, project, act_plan_payload)

            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={"artifact_id": str(act_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
            )
            step_order += 1

        volume_plan_fallback = _fallback_volume_plan(project, book_spec_payload, cast_spec_payload, world_spec_payload)
        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        volume_system, volume_user = _volume_plan_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            cast_spec_payload,
            act_plan=act_plan_payload,
        )
        volume_plan_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="volume_plan",
            system_prompt=volume_system,
            user_prompt=volume_user,
            fallback_payload=volume_plan_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_volume_plan_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        volume_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.VOLUME_PLAN,
                artifact_id=volume_artifact.id,
                version_no=volume_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(volume_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        # ── Plan Judge: validate plan against genre-specific rubrics ──
        if settings.quality.enable_plan_judge:
            from bestseller.services.plan_judge import validate_plan as _validate_plan

            plan_validation = _validate_plan(
                genre=project.genre,
                sub_genre=project.sub_genre,
                book_spec=book_spec_payload,
                world_spec=world_spec_payload,
                cast_spec=cast_spec_payload,
                volume_plan=volume_plan_payload if isinstance(volume_plan_payload, list) else [],
            )
            validation_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.PLAN_VALIDATION,
                    content=plan_validation.model_dump(mode="json"),
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.PLAN_VALIDATION,
                    artifact_id=validation_artifact.id,
                    version_no=validation_artifact.version_no,
                )
            )

            # ── Auto-repair: re-generate volume plan if critical findings ──
            if not plan_validation.overall_pass:
                critical_findings = [f for f in plan_validation.findings if f.severity == "critical"]
                if critical_findings and isinstance(volume_plan_payload, list):
                    try:
                        repair_notes = "\n".join(
                            f"- {f.message}" + (f" ({f.suggestion})" if f.suggestion else "")
                            for f in critical_findings
                        )
                        repair_system, repair_user = _volume_plan_prompts(
                            project, book_spec_payload, world_spec_payload, cast_spec_payload,
                            act_plan=act_plan_payload,
                        )
                        is_en = is_english_language(project.language)
                        repair_user += (
                            f"\n\n{'[Plan repair — fix these critical issues]' if is_en else '【规划修复 — 必须修正以下关键问题】'}"
                            f"\n{repair_notes}"
                            f"\n{'Regenerate the volume plan addressing all issues above.' if is_en else '请重新生成卷计划，确保修正以上所有问题。'}"
                        )

                        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
                            session,
                            settings,
                            project=project,
                            logical_name="volume_plan_repair",
                            system_prompt=repair_system,
                            user_prompt=repair_user,
                            fallback_payload=volume_plan_payload,
                            workflow_run_id=workflow_run.id,
                            validator=parse_volume_plan_input,
                        )
                        if repair_llm_run_id is not None:
                            llm_run_ids.append(repair_llm_run_id)
                        volume_plan_payload = repaired_payload
                        volume_artifact = await import_planning_artifact(
                            session,
                            project_slug,
                            PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload),
                        )
                    except Exception:
                        logger.warning("Plan auto-repair failed; continuing with original plan", exc_info=True)

        outline_fallback = _fallback_chapter_outline_batch(
            project,
            book_spec_payload,
            cast_spec_payload,
            volume_plan_payload,
        )
        current_step_name = "generate_chapter_outline_batch"
        workflow_run.current_step = current_step_name
        outline_system, outline_user = _outline_prompts(
            project,
            book_spec_payload,
            cast_spec_payload,
            volume_plan_payload,
        )
        outline_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="chapter_outline_batch",
            system_prompt=outline_system,
            user_prompt=outline_user,
            fallback_payload=outline_fallback,
            workflow_run_id=workflow_run.id,
            validator=ChapterOutlineBatchInput.model_validate,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        outline_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH, content=outline_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                artifact_id=outline_artifact.id,
                version_no=outline_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(outline_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "artifact_ids": {record.artifact_type.value: str(record.artifact_id) for record in artifact_records},
            "llm_run_ids": [str(item) for item in llm_run_ids],
        }
        await session.flush()

        outline_chapters = outline_payload.get("chapters", []) if isinstance(outline_payload, dict) else []
        volume_count = len(volume_plan_payload) if isinstance(volume_plan_payload, list) else len(volume_plan_payload.get("volumes", []))
        return NovelPlanningResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            premise=premise,
            artifacts=artifact_records,
            volume_count=volume_count,
            chapter_count=len(outline_chapters),
            llm_run_ids=llm_run_ids,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise

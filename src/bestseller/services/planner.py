from __future__ import annotations

import copy
import json
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
from bestseller.services.projects import get_project_by_slug, import_planning_artifact
from bestseller.services.story_bible import parse_cast_spec_input, parse_volume_plan_input, parse_world_spec_input
from bestseller.services.workflows import create_workflow_run, create_workflow_step_run
from bestseller.settings import AppSettings


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


def _protagonist_name_from_book_spec(book_spec: dict[str, Any], premise: str) -> str:
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    return _non_empty_string(protagonist.get("name"), _derive_protagonist_name(premise))


def _derive_protagonist_name(premise: str) -> str:
    candidates = [
        candidate
        for candidate in re.findall(r"[A-Z][a-zA-Z]{2,}|[\u4e00-\u9fff]{2,3}", premise)
        if candidate not in {"一个", "一名", "主角", "故事", "帝国", "世界", "长篇", "小说"}
    ]
    return candidates[0] if candidates else "主角"


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


def _fallback_book_spec(project: ProjectModel, premise: str) -> dict[str, Any]:
    profile = _genre_profile(project.genre)
    protagonist_name = _derive_protagonist_name(premise)
    return {
        "title": project.title,
        "logline": premise.strip(),
        "genre": project.genre,
        "target_audience": project.audience or "web-serial",
        "tone": profile["tones"],
        "themes": profile["themes"],
        "protagonist": {
            "name": protagonist_name,
            "core_wound": f"{protagonist_name}曾因一次关键判断失误付出沉重代价。",
            "external_goal": f"{protagonist_name}必须主动追查并破解当前危机背后的操盘者。",
            "internal_need": f"{protagonist_name}需要从只靠个人硬撑，转向建立真正可持续的同盟。",
        },
        "stakes": {
            "personal": f"{protagonist_name}会失去自己仍在意的人。",
            "social": "更大范围的秩序会因此崩坏，更多无辜者将被牵连。",
            "existential": "如果幕后计划成功，整个世界的基本运行秩序都会被改写。",
        },
        "series_engine": {
            "core_loop": "发现异常 -> 主动调查 -> 引发更强反扑 -> 拿到新线索 -> 逼近更大真相",
            "hook_style": "每章末抛出新的威胁、线索或立场反转",
        },
    }


def _fallback_world_spec(project: ProjectModel, premise: str, book_spec: dict[str, Any]) -> dict[str, Any]:
    profile = _genre_profile(project.genre)
    protagonist_name = _protagonist_name_from_book_spec(book_spec, premise)
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


def _fallback_cast_spec(project: ProjectModel, premise: str, book_spec: dict[str, Any], world_spec: dict[str, Any]) -> dict[str, Any]:
    profile = _genre_profile(project.genre)
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    protagonist_name = _protagonist_name_from_book_spec(book_spec, premise)
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
    ally_name = "顾临" if protagonist_name != "顾临" else "林策"
    antagonist_name = "祁镇" if protagonist_name != "祁镇" else "沈烬"
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
            "knowledge_state": {
                "knows": ["当前危机存在异常迹象", "官方叙事有漏洞"],
                "falsely_believes": [f"{ally_name}当年做出了背离自己的选择"],
                "unaware_of": [f"{antagonist_name}与过去事故存在直接关联"],
            },
            "power_tier": protagonist_tier,
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
            }
        ],
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
        ],
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


def _fallback_volume_plan(project: ProjectModel, book_spec: dict[str, Any], cast_spec: dict[str, Any], world_spec: dict[str, Any]) -> list[dict[str, Any]]:
    profile = _genre_profile(project.genre)
    total_chapters = max(project.target_chapters, 1)
    volume_count = min(6, total_chapters, max(1, math.ceil(total_chapters / 12)))
    chapter_ranges = _build_volume_ranges(total_chapters, volume_count)
    cast_payload = _mapping(cast_spec)
    protagonist_name = _non_empty_string(_mapping(cast_payload.get("protagonist")).get("name"), "主角")
    antagonist_name = _non_empty_string(_mapping(cast_payload.get("antagonist")).get("name"), "敌对操盘者")
    themes = _string_list(_mapping(book_spec).get("themes")) or profile["themes"]
    power_system = _mapping(_mapping(world_spec).get("power_system"))
    protagonist_tier = _non_empty_string(power_system.get("protagonist_starting_tier"), "低阶")
    plan: list[dict[str, Any]] = []
    for volume_number, (chapter_start, chapter_end) in enumerate(chapter_ranges, start=1):
        plan.append(
            {
                "volume_number": volume_number,
                "volume_title": f"第{volume_number}卷：{['裂口','追查','反扑','断局','决战','余震'][min(volume_number - 1, 5)]}",
                "volume_theme": themes[(volume_number - 1) % len(themes)],
                "word_count_target": int(project.target_word_count / volume_count),
                "chapter_count_target": chapter_end - chapter_start + 1,
                "opening_state": {
                    "protagonist_status": "仍在高压局面中被迫行动",
                    "protagonist_power_tier": protagonist_tier
                    if volume_number == 1
                    else f"第{volume_number - 1}卷后更成熟的状态",
                    "world_situation": "统治秩序正在收紧，反扑强度逐步升级。",
                },
                "volume_goal": f"{protagonist_name}需要在本卷内拿到一组足以改变局势的关键证据或盟友。",
                "volume_obstacle": f"{antagonist_name}及其体系在本卷中持续施压，并设置新的封锁。",
                "volume_climax": f"第{chapter_end}章前后，{protagonist_name}必须在巨大代价下完成一次关键突破。",
                "volume_resolution": {
                    "protagonist_power_tier": "中阶" if volume_number >= 2 else protagonist_tier,
                    "goal_achieved": True,
                    "cost_paid": "主角得到新进展，但失去一部分安全区、关系或旧秩序庇护。",
                    "new_threat_introduced": "更高层敌意或更大规模危机被抛到台前。",
                },
                "key_reveals": [f"第{volume_number}卷揭示新的责任链和隐藏规则。"],
                "foreshadowing_planted": [f"为第{volume_number + 1}卷留下更高层威胁入口。"],
                "foreshadowing_paid_off": [f"回收前序卷的一个关键误导或伏笔。"] if volume_number > 1 else [],
                "reader_hook_to_next": "卷末留下新的真相缺口和更高层威胁。",
            }
        )
    return plan


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


def _fallback_chapter_outline_batch(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> dict[str, Any]:
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
    chapters: list[dict[str, Any]] = []
    chapter_number = 1
    for raw_volume_index, volume in enumerate(normalized_volume_plan, start=1):
        volume_payload = _mapping(volume)
        total_in_volume = max(int(volume_payload.get("chapter_count_target") or 1), 1)
        volume_goal = _non_empty_string(volume_payload.get("volume_goal"), "推动主线调查取得关键进展")
        volume_number = int(volume_payload.get("volume_number") or raw_volume_index)
        for index_within_volume in range(1, total_in_volume + 1):
            phase = _phase_name(index_within_volume, total_in_volume)
            chapter_goal = (
                f"{protagonist_name}在第{chapter_number}章推进{volume_goal}，"
                f"并迫使局势进入新的高压阶段。"
            )
            scenes = [
                {
                    "scene_number": 1,
                    "scene_type": "setup" if phase == "setup" else "transition",
                    "title": f"第{chapter_number}章开场压力",
                    "time_label": f"第{chapter_number}章开场",
                    "participants": [protagonist_name, ally_name],
                    "purpose": {
                        "story": "承接上章后果并给出当前行动目标",
                        "emotion": "持续拉高压力和不确定性",
                    },
                    "entry_state": {
                        protagonist_name: {"arc_state": "承压推进", "emotion": "紧绷"},
                        ally_name: {"arc_state": "谨慎协作", "emotion": "戒备"},
                    },
                    "exit_state": {
                        protagonist_name: {"arc_state": "主动出击", "emotion": "更坚定"},
                        ally_name: {"arc_state": "被迫跟进", "emotion": "压力上升"},
                    },
                    "target_word_count": project.target_word_count // max(project.target_chapters * 3, 1),
                },
                {
                    "scene_number": 2,
                    "scene_type": "conflict" if phase in {"pressure", "reversal", "climax"} else "reveal",
                    "title": f"第{chapter_number}章关键碰撞",
                    "time_label": f"第{chapter_number}章中段",
                    "participants": [protagonist_name, antagonist_name]
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
                    "target_word_count": project.target_word_count // max(project.target_chapters * 3, 1),
                },
                {
                    "scene_number": 3,
                    "scene_type": "hook",
                    "title": f"第{chapter_number}章结尾钩子",
                    "time_label": f"第{chapter_number}章结尾",
                    "participants": [protagonist_name, ally_name]
                    if index_within_volume % 3 != 0
                    else [protagonist_name, antagonist_name],
                    "purpose": {
                        "story": "抛出本章最大新信息或新威胁",
                        "emotion": "让读者必须继续追下一章",
                    },
                    "entry_state": {
                        protagonist_name: {"arc_state": "准备收束", "emotion": "短暂控制局势"},
                    },
                    "exit_state": {
                        protagonist_name: {"arc_state": "被迫进入更难局面", "emotion": "强压下前进"},
                    },
                    "target_word_count": project.target_word_count // max(project.target_chapters * 3, 1),
                },
            ]
            chapters.append(
                {
                    "chapter_number": chapter_number,
                    "title": f"第{chapter_number}章：{['裂缝','追线','封锁','碰撞','反咬','闯关','断局','逼近'][chapter_number % 8]}",
                    "goal": chapter_goal,
                    "opening_situation": "承接上一章的结果，主角暂时没有退路。",
                    "main_conflict": f"{protagonist_name}需要在被压制的条件下拿到新证据，同时防止{antagonist_name}抢先一步。",
                    "hook_type": _hook_type(index_within_volume, total_in_volume),
                    "hook_description": "章末抛出一条更危险的新线索或让敌方行动先一步压到主角面前。",
                    "volume_number": volume_number,
                    "target_word_count": max(2200, int(project.target_word_count / max(project.target_chapters, 1))),
                    "scenes": scenes,
                }
            )
            chapter_number += 1
    return {"batch_name": "auto-generated-full-outline", "chapters": chapters}


def _book_spec_prompts(project: ProjectModel, premise: str, fallback: dict[str, Any]) -> tuple[str, str]:
    system_prompt = (
        "你是长篇中文小说的故事策划师。"
        "输出必须是合法 JSON，不要解释。"
    )
    user_prompt = (
        f"项目标题：{project.title}\n"
        f"类型：{project.genre}\n"
        f"目标字数：{project.target_word_count}\n"
        f"目标章节：{project.target_chapters}\n"
        f"受众：{project.audience or 'web-serial'}\n"
        f"Premise：{premise}\n"
        "请生成一个 BookSpec JSON，包含 title、logline、genre、target_audience、tone、themes、"
        "protagonist、stakes、series_engine。"
    )
    return system_prompt, user_prompt


def _world_spec_prompts(project: ProjectModel, premise: str, book_spec: dict[str, Any]) -> tuple[str, str]:
    system_prompt = "你是长篇中文小说世界观设计师。输出必须是合法 JSON，不要解释。"
    user_prompt = (
        f"项目标题：{project.title}\n"
        f"类型：{project.genre}\n"
        f"Premise：{premise}\n"
        f"BookSpec：{_json_dumps(book_spec)}\n"
        "请生成一个 WorldSpec JSON，包含 world_name、world_premise、rules、power_system、locations、"
        "factions、power_structure、history_key_events、forbidden_zones。"
    )
    return system_prompt, user_prompt


def _cast_spec_prompts(book_spec: dict[str, Any], world_spec: dict[str, Any]) -> tuple[str, str]:
    system_prompt = "你是长篇中文小说角色架构师。输出必须是合法 JSON，不要解释。"
    user_prompt = (
        f"BookSpec：{_json_dumps(book_spec)}\n"
        f"WorldSpec：{_json_dumps(world_spec)}\n"
        "请生成一个 CastSpec JSON，包含 protagonist、antagonist、supporting_cast、conflict_map。"
    )
    return system_prompt, user_prompt


def _volume_plan_prompts(project: ProjectModel, book_spec: dict[str, Any], world_spec: dict[str, Any], cast_spec: dict[str, Any]) -> tuple[str, str]:
    system_prompt = "你是长篇中文小说结构编辑。输出必须是合法 JSON 数组，不要解释。"
    user_prompt = (
        f"项目标题：{project.title}\n"
        f"目标字数：{project.target_word_count}\n"
        f"目标章节：{project.target_chapters}\n"
        f"BookSpec：{_json_dumps(book_spec)}\n"
        f"WorldSpec：{_json_dumps(world_spec)}\n"
        f"CastSpec：{_json_dumps(cast_spec)}\n"
        "请生成 VolumePlan JSON 数组，每个元素包含 volume_number、volume_title、volume_theme、"
        "chapter_count_target、volume_goal、volume_obstacle、volume_climax、volume_resolution。"
    )
    return system_prompt, user_prompt


def _outline_prompts(project: ProjectModel, book_spec: dict[str, Any], cast_spec: dict[str, Any], volume_plan: list[dict[str, Any]]) -> tuple[str, str]:
    system_prompt = "你是长篇中文小说章纲规划师。输出必须是合法 JSON，不要解释。"
    user_prompt = (
        f"项目标题：{project.title}\n"
        f"目标章节：{project.target_chapters}\n"
        f"BookSpec：{_json_dumps(book_spec)}\n"
        f"CastSpec：{_json_dumps(cast_spec)}\n"
        f"VolumePlan：{_json_dumps(volume_plan)}\n"
        "请生成完整 ChapterOutlineBatch JSON，包含 batch_name 和 chapters。每章至少 3 个 scenes。"
    )
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

        book_spec_fallback = _fallback_book_spec(project, premise)
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
        cast_system, cast_user = _cast_spec_prompts(book_spec_payload, world_spec_payload)
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

        volume_plan_fallback = _fallback_volume_plan(project, book_spec_payload, cast_spec_payload, world_spec_payload)
        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        volume_system, volume_user = _volume_plan_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            cast_spec_payload,
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

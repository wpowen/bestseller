from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.infra.db.models import (
    CanonFactModel,
    CharacterModel,
    ClueModel,
    PayoffModel,
    ProjectModel,
    RelationshipEventModel,
    TimelineEventModel,
    WorldRuleModel,
)
from bestseller.services.llm import LLMCompletionRequest
from bestseller.services.llm_tool_runtime import ToolRegistry, ToolSpec, run_tool_loop
from bestseller.services.project_health import build_project_health_report
from bestseller.services.retrieval import search_retrieval_for_project
from bestseller.services.revealed_ledger import build_revealed_ledger
from bestseller.settings import AppSettings


def _current_story_order(chapter_number: int, scene_number: int) -> float:
    return float(f"{chapter_number}.{scene_number:02d}")


def _trace_to_json(trace: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "round_index": item.round_index,
            "tool_name": item.tool_name,
            "arguments": dict(item.arguments),
            "result": dict(item.result),
            "error": item.error,
        }
        for item in trace
    ]


def _normalize_name_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        value = raw.strip()
        return [value] if value else []
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in names:
            names.append(value)
    return names


def _fallback_query_brief(
    *,
    tool_results: dict[str, dict[str, Any]],
    language: str,
) -> str:
    is_en = (language or "").lower().startswith("en")
    lines = (
        ["Scene query brief:",]
        if is_en
        else ["写前查询简报："]
    )
    if "query_character_truth" in tool_results:
        characters = tool_results["query_character_truth"].get("characters") or []
        if characters:
            label = "Character truth" if is_en else "角色真值"
            names = ", ".join(
                str(item.get("name", ""))
                for item in characters[:4]
                if item.get("name")
            )
            lines.append(f"- {label}: {names}")
    if "query_timeline_window" in tool_results:
        events = tool_results["query_timeline_window"].get("events") or []
        if events:
            label = "Recent timeline" if is_en else "近时序"
            names = ", ".join(
                str(item.get("event_name", ""))
                for item in events[:4]
                if item.get("event_name")
            )
            lines.append(f"- {label}: {names}")
    if "query_clue_status" in tool_results:
        clues = tool_results["query_clue_status"].get("clues") or []
        if clues:
            label = "Pending clues" if is_en else "待兑现伏笔"
            names = ", ".join(
                str(item.get("clue_code", ""))
                for item in clues[:4]
                if item.get("clue_code")
            )
            lines.append(f"- {label}: {names}")
    if "query_reader_signal" in tool_results:
        signal = tool_results["query_reader_signal"]
        debts = signal.get("setup_payoff_debts") or []
        overdue = signal.get("overdue_clues") or []
        if debts or overdue:
            label = "Reader-risk signals present" if is_en else "存在读者风险信号"
            lines.append(f"- {label}")
    return "\n".join(lines)


def build_story_query_registry(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter_number: int,
    scene_number: int,
    default_participants: list[str] | None = None,
) -> ToolRegistry:
    participants = list(default_participants or [])
    current_story_order = _current_story_order(chapter_number, scene_number)

    async def query_character_truth(arguments: dict[str, Any]) -> dict[str, Any]:
        names = _normalize_name_list(arguments.get("names")) or participants
        stmt = select(CharacterModel).where(CharacterModel.project_id == project.id)
        if names:
            stmt = stmt.where(CharacterModel.name.in_(names))
        characters = list(await session.scalars(stmt.order_by(CharacterModel.name.asc())))
        canon_facts: list[CanonFactModel] = []
        if characters:
            canon_facts = list(
                await session.scalars(
                    select(CanonFactModel).where(
                        CanonFactModel.project_id == project.id,
                        CanonFactModel.subject_type == "character",
                        CanonFactModel.subject_label.in_([item.name for item in characters]),
                        CanonFactModel.is_current.is_(True),
                    )
                )
            )
        facts_by_name: dict[str, list[dict[str, Any]]] = {}
        for fact in canon_facts:
            facts_by_name.setdefault(fact.subject_label, []).append(
                {
                    "predicate": fact.predicate,
                    "value": dict(fact.value_json or {}),
                    "notes": fact.notes,
                    "valid_from_chapter_no": fact.valid_from_chapter_no,
                    "valid_to_chapter_no": fact.valid_to_chapter_no,
                }
            )
        return {
            "characters": [
                {
                    "name": item.name,
                    "role": item.role,
                    "goal": item.goal,
                    "fear": item.fear,
                    "flaw": item.flaw,
                    "arc_state": item.arc_state,
                    "power_tier": item.power_tier,
                    "alive_status": item.alive_status,
                    "stance": item.stance,
                    "physical_description": item.physical_description,
                    "facts": facts_by_name.get(item.name, [])[:8],
                }
                for item in characters[:8]
            ]
        }

    async def query_timeline_window(arguments: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(arguments.get("limit", 8) or 8), 20))
        events = list(
            await session.scalars(
                select(TimelineEventModel)
                .where(
                    TimelineEventModel.project_id == project.id,
                    TimelineEventModel.story_order < current_story_order,
                )
                .order_by(TimelineEventModel.story_order.desc())
                .limit(limit)
            )
        )
        return {
            "events": [
                {
                    "event_name": item.event_name,
                    "event_type": item.event_type,
                    "story_time_label": item.story_time_label,
                    "story_order": float(item.story_order),
                    "consequences": list(item.consequences or []),
                    "summary": (item.metadata_json or {}).get("summary"),
                }
                for item in events
            ]
        }

    async def query_clue_status(arguments: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(arguments.get("limit", 8) or 8), 20))
        clues = list(
            await session.scalars(
                select(ClueModel)
                .where(
                    ClueModel.project_id == project.id,
                    ClueModel.status.in_(("planted", "active")),
                )
                .order_by(
                    ClueModel.expected_payoff_by_chapter_number.asc().nullslast(),
                    ClueModel.planted_in_chapter_number.asc().nullslast(),
                )
                .limit(limit)
            )
        )
        payoffs = list(
            await session.scalars(
                select(PayoffModel)
                .where(
                    PayoffModel.project_id == project.id,
                    PayoffModel.status.in_(("planned", "active")),
                    or_(
                        PayoffModel.target_chapter_number.is_(None),
                        PayoffModel.target_chapter_number >= chapter_number,
                    ),
                )
                .order_by(PayoffModel.target_chapter_number.asc().nullslast())
                .limit(limit)
            )
        )
        return {
            "clues": [
                {
                    "clue_code": item.clue_code,
                    "label": item.label,
                    "status": item.status,
                    "planted_in_chapter_number": item.planted_in_chapter_number,
                    "expected_payoff_by_chapter_number": item.expected_payoff_by_chapter_number,
                    "actual_paid_off_chapter_number": item.actual_paid_off_chapter_number,
                }
                for item in clues
            ],
            "payoffs": [
                {
                    "payoff_code": item.payoff_code,
                    "label": item.label,
                    "status": item.status,
                    "target_chapter_number": item.target_chapter_number,
                    "actual_chapter_number": item.actual_chapter_number,
                }
                for item in payoffs
            ],
        }

    async def query_relationship_history(arguments: dict[str, Any]) -> dict[str, Any]:
        names = _normalize_name_list(arguments.get("names")) or participants
        if len(names) < 2:
            return {"events": []}
        names_set = set(names[:2])
        events = list(
            await session.scalars(
                select(RelationshipEventModel)
                .where(
                    RelationshipEventModel.project_id == project.id,
                    RelationshipEventModel.character_a_label.in_(names_set),
                    RelationshipEventModel.character_b_label.in_(names_set),
                    RelationshipEventModel.chapter_number <= chapter_number,
                )
                .order_by(
                    RelationshipEventModel.chapter_number.desc(),
                    RelationshipEventModel.scene_number.desc().nullslast(),
                )
                .limit(10)
            )
        )
        return {
            "events": [
                {
                    "character_a_label": item.character_a_label,
                    "character_b_label": item.character_b_label,
                    "chapter_number": item.chapter_number,
                    "scene_number": item.scene_number,
                    "event_description": item.event_description,
                    "relationship_change": item.relationship_change,
                    "is_milestone": item.is_milestone,
                }
                for item in events
            ]
        }

    async def query_world_rule(arguments: dict[str, Any]) -> dict[str, Any]:
        rule_codes = _normalize_name_list(arguments.get("rule_codes"))
        query = str(arguments.get("query") or "").strip().lower()
        rules = list(
            await session.scalars(
                select(WorldRuleModel)
                .where(WorldRuleModel.project_id == project.id)
                .order_by(WorldRuleModel.rule_code.asc())
            )
        )
        filtered = []
        for item in rules:
            if rule_codes and item.rule_code not in rule_codes:
                continue
            if query:
                haystack = " ".join(
                    [
                        item.rule_code,
                        item.name,
                        item.description,
                        item.story_consequence or "",
                    ]
                ).lower()
                if query not in haystack:
                    continue
            filtered.append(item)
        return {
            "rules": [
                {
                    "rule_code": item.rule_code,
                    "name": item.name,
                    "description": item.description,
                    "story_consequence": item.story_consequence,
                    "exploitation_potential": item.exploitation_potential,
                }
                for item in filtered[:8]
            ]
        }

    async def query_revealed_ledger(arguments: dict[str, Any]) -> dict[str, Any]:
        up_to_chapter = max(0, int(arguments.get("up_to_chapter", chapter_number - 1) or 0))
        ledger = await build_revealed_ledger(
            session,
            project.id,
            up_to_chapter=up_to_chapter or None,
        )
        return {
            "facts": [
                {
                    "name": item.name,
                    "value": item.value,
                    "kind": item.kind,
                    "first_chapter": item.first_chapter,
                    "subject": item.subject,
                }
                for item in ledger.facts[:12]
            ],
            "overused_hooks": [
                {
                    "hook_type": item.hook_type,
                    "total_count": item.total_count,
                    "recent_count": item.recent_count,
                    "recent_chapters": list(item.recent_chapters),
                }
                for item in ledger.overused_hooks()
            ],
            "recent_conflicts": [
                {"chapter_number": chapter_no, "summary": summary}
                for chapter_no, summary in ledger.recent_conflicts[:8]
            ],
        }

    async def query_reader_signal(arguments: dict[str, Any]) -> dict[str, Any]:
        report = await build_project_health_report(session, settings, project.slug)
        return {
            "overused_hooks": list(report.get("overused_hooks") or [])[:6],
            "overdue_clues": list(report.get("overdue_clues") or [])[:6],
            "setup_payoff_debts": list(report.get("setup_payoff_debts") or [])[:6],
        }

    async def search_story_context(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"chunks": []}
        top_k = max(1, min(int(arguments.get("top_k", 6) or 6), 12))
        result = await search_retrieval_for_project(
            session,
            settings,
            project,
            query,
            top_k=top_k,
        )
        return {
            "chunks": [
                {
                    "source_type": item.source_type,
                    "chunk_index": item.chunk_index,
                    "score": item.score,
                    "chunk_text": item.chunk_text,
                    "metadata": item.metadata,
                }
                for item in result.chunks
            ]
        }

    return ToolRegistry(
        [
            ToolSpec(
                name="query_character_truth",
                description="查询角色真值，包括身份、当前状态和已登记事实。",
                parameters={
                    "type": "object",
                    "properties": {
                        "names": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                },
                handler=query_character_truth,
            ),
            ToolSpec(
                name="query_timeline_window",
                description="查询当前场景之前的近期时间线事件。",
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                },
                handler=query_timeline_window,
            ),
            ToolSpec(
                name="query_clue_status",
                description="查询当前项目的伏笔与兑现状态，识别即将到期或逾期的线索。",
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                },
                handler=query_clue_status,
            ),
            ToolSpec(
                name="query_relationship_history",
                description="查询两名角色之间最近的关系事件与里程碑。",
                parameters={
                    "type": "object",
                    "properties": {
                        "names": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                },
                handler=query_relationship_history,
            ),
            ToolSpec(
                name="query_world_rule",
                description="查询世界规则与限制条件，避免写作时违规。",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "rule_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                handler=query_world_rule,
            ),
            ToolSpec(
                name="query_revealed_ledger",
                description="查询全书已揭示事实、近期高频 hook 和重复冲突风险。",
                parameters={
                    "type": "object",
                    "properties": {
                        "up_to_chapter": {"type": "integer", "minimum": 0},
                    },
                },
                handler=query_revealed_ledger,
            ),
            ToolSpec(
                name="query_reader_signal",
                description="查询当前项目的读者信号风险，包括 hook 重复、伏笔逾期和爽点兑现债务。",
                parameters={"type": "object", "properties": {}},
                handler=query_reader_signal,
            ),
            ToolSpec(
                name="search_story_context",
                description="按自然语言搜索故事上下文检索库。",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 12},
                    },
                    "required": ["query"],
                },
                handler=search_story_context,
            ),
        ]
    )


async def run_scene_query_brief(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter_number: int,
    scene_number: int,
    scene_title: str | None,
    scene_type: str,
    participants: list[str],
    story_purpose: str,
    emotion_purpose: str,
    context_packet: SceneWriterContextPacket | None,
) -> dict[str, Any]:
    registry = build_story_query_registry(
        session,
        settings,
        project=project,
        chapter_number=chapter_number,
        scene_number=scene_number,
        default_participants=participants,
    )
    warnings = (
        list(getattr(context_packet, "contradiction_warnings", []) or [])[:6]
        if context_packet is not None
        else []
    )
    warning_block = "\n".join(f"- {item}" for item in warnings) if warnings else "无"
    request = LLMCompletionRequest(
        logical_role="writer",
        model_tier="standard",
        system_prompt=(
            "你是写前查询代理。你的职责是在正式写场景前，先主动查询缺失事实，"
            "再输出一份极短的写前简报。只做查询，不写正文，不改 canon。"
            "优先关注：人物身份、时间线、伏笔兑现、关系变化、世界规则、读者信号。"
            "若被动上下文已足够，就少查。最终输出必须是给写手的简报，格式紧凑。"
        ),
        user_prompt=(
            f"项目：{project.title}\n"
            f"章节：第{chapter_number}章\n"
            f"场景：第{scene_number}场 {scene_title or ''}\n"
            f"场景类型：{scene_type}\n"
            f"参与者：{'、'.join(participants) if participants else '暂无'}\n"
            f"剧情目的：{story_purpose or '推进主线'}\n"
            f"情绪目的：{emotion_purpose or '维持张力'}\n"
            f"当前已知风险：\n{warning_block}\n\n"
            "请先查你确实需要补充的信息，再输出：\n"
            "【写前补充简报】\n"
            "- 硬事实\n"
            "- 高风险不要写错\n"
            "- 本场可顺手兑现/放大的爽点或钩子\n"
        ),
        fallback_response="【写前补充简报】\n- 没有额外查询结果\n",
        prompt_template="scene_query_brief",
        prompt_version="1.0",
        project_id=project.id,
        metadata={
            "project_slug": project.slug,
            "chapter_number": chapter_number,
            "scene_number": scene_number,
        },
    )
    loop_result = await run_tool_loop(
        session,
        settings,
        base_request=request,
        registry=registry,
        max_rounds=max(1, int(getattr(settings.pipeline, "story_query_brief_max_rounds", 4) or 4)),
        tool_choice="auto",
    )
    brief = (loop_result.final_content or "").strip()
    if not brief:
        brief = _fallback_query_brief(
            tool_results=loop_result.final_tool_results,
            language=project.language,
        )
    return {
        "brief": brief or None,
        "trace": _trace_to_json(loop_result.trace),
        "rounds": loop_result.rounds,
        "exit_reason": loop_result.exit_reason,
    }


__all__ = [
    "build_story_query_registry",
    "run_scene_query_brief",
]

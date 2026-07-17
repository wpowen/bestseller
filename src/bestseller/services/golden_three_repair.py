"""Bounded golden-three outline repair driven by the readiness judge's fixes.

Why this exists (2026-07-16): the commercial planning readiness judge emits
per-issue ``required_fix`` directives that are concrete enough to execute
("给姜窈提亲一个可被读者理解的内在逻辑…"), yet the gate consumed none of them —
any block raised straight to task death. Two consecutive real books died with
actionable repair instructions attached to their corpses. This module is the
missing consumer: one focused LLM revision of the golden-three chapter fields,
after which the caller re-judges. Fail-open by design — any parse/LLM failure
returns False and the gate blocks exactly as before.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

# Chapter fields the repair may rewrite. Everything else (word targets, hype
# assignments, methodology contracts, arc codes) is planning bookkeeping that
# downstream stages already consumed — the repair must not touch it.
_CHAPTER_FIELDS = ("chapter_goal", "opening_situation", "main_conflict", "hook_description")
_SCENE_FIELDS = ("purpose", "entry_state", "exit_state", "hook_requirement")


def _chapter_payload(chapter: Any) -> dict[str, Any]:
    return {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": getattr(chapter, "title", None) or "",
        **{f: getattr(chapter, f, None) or "" for f in _CHAPTER_FIELDS},
        "scenes": [
            {
                "scene_number": int(getattr(sc, "scene_number", 0) or 0),
                "scene_type": getattr(sc, "scene_type", None) or "",
                **{f: getattr(sc, f, None) for f in _SCENE_FIELDS},
            }
            for sc in (getattr(chapter, "scenes", []) or [])
        ],
    }


def _issue_lines(llm_judge_payload: Mapping[str, Any] | None) -> list[str]:
    lines: list[str] = []
    for issue in (llm_judge_payload or {}).get("blocking_issues", ()) or ():
        if not isinstance(issue, Mapping):
            continue
        code = str(issue.get("code") or "").strip()
        evidence = str(issue.get("evidence") or "").strip()
        fix = str(issue.get("required_fix") or "").strip()
        if not (code and fix):
            continue
        lines.append(f"- [{code}] 问题：{evidence}\n  整改：{fix}")
    return lines


def _apply_chapter_revision(chapter: Any, revised: Mapping[str, Any]) -> bool:
    changed = False
    for field in _CHAPTER_FIELDS:
        value = revised.get(field)
        if isinstance(value, str) and value.strip() and value != getattr(chapter, field, None):
            setattr(chapter, field, value.strip())
            changed = True
    scenes_by_number = {
        int(getattr(sc, "scene_number", 0) or 0): sc
        for sc in (getattr(chapter, "scenes", []) or [])
    }
    for scene_payload in revised.get("scenes") or []:
        if not isinstance(scene_payload, Mapping):
            continue
        try:
            scene = scenes_by_number.get(int(scene_payload.get("scene_number") or 0))
        except (TypeError, ValueError):
            continue
        if scene is None:
            continue
        for field in _SCENE_FIELDS:
            value = scene_payload.get(field)
            if isinstance(value, str) and value.strip() and value != getattr(scene, field, None):
                setattr(scene, field, value.strip())
                changed = True
    return changed


async def repair_golden_three_outline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters: list[Any],
    llm_judge_payload: Mapping[str, Any] | None,
    project: Any = None,
    complete_fn: Any = None,
) -> bool:
    """Rewrite golden-three outline fields per the judge's required fixes.

    Mutates the chapter/scene models in place and returns True when at least
    one field changed; returns False (and touches nothing) on missing issues,
    LLM failure or an unparseable response, so the caller's gate semantics
    degrade to exactly the pre-repair behaviour.
    """

    issues = _issue_lines(llm_judge_payload)
    if not issues:
        return False
    golden = [
        ch for ch in chapters
        if int(getattr(ch, "chapter_number", 0) or 0) in (1, 2, 3)
    ]
    if not golden:
        return False

    current = json.dumps(
        [_chapter_payload(ch) for ch in golden], ensure_ascii=False, indent=1
    )
    system_prompt = (
        "你是章纲外科医生。下面的黄金三章章纲没过商业就绪审查。你的任务是按【整改指令】"
        "做定点修复：只改必须改的字段，保留人物、世界观、金手指与既定卖点不变，"
        "不引入新角色或新设定。修复必须落在字段文本本身——把主角的主动谋划、"
        "决策的信息/压力/成本逻辑、对手动机链直接写进 goal/conflict/hook/场景字段，"
        "不是加批注。输出与输入完全同构的 JSON 数组（同样的章节号、场景号与字段名），"
        "不要解释。"
    )
    user_prompt = (
        "【整改指令（就绪判官）】\n" + "\n".join(issues) +
        "\n\n【当前黄金三章章纲】\n" + current +
        "\n\n只输出修复后的 JSON 数组。"
    )
    if complete_fn is None:
        async def complete_fn(sys_p: str, usr_p: str) -> str:  # type: ignore[misc]
            completion = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role="planner",
                    model_tier="strong",
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    fallback_response="",
                    prompt_template="golden_three_readiness_repair",
                    prompt_version="v1",
                    project_id=getattr(project, "id", None),
                    max_tokens_override=6000,
                ),
            )
            return completion.content or ""

    try:
        raw = str(await complete_fn(system_prompt, user_prompt) or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        revised_list = json.loads(raw)
        if not isinstance(revised_list, list):
            return False
    except Exception:
        logger.warning("golden-three readiness repair failed; gate keeps original", exc_info=True)
        return False

    revised_by_number = {
        int(item.get("chapter_number") or 0): item
        for item in revised_list
        if isinstance(item, Mapping)
    }
    changed = False
    for chapter in golden:
        revised = revised_by_number.get(int(getattr(chapter, "chapter_number", 0) or 0))
        if isinstance(revised, Mapping) and _apply_chapter_revision(chapter, revised):
            changed = True
    if changed:
        logger.info("golden-three readiness repair applied to %d chapters", len(golden))
    return changed


__all__ = ["repair_golden_three_outline"]

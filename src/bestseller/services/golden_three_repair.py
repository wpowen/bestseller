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


def _parse_revised_chapter_list(raw: str) -> list[object]:
    """Parse a JSON-array repair response, including common LLM damage."""

    candidate = str(raw or "").strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start >= 0 and end > start:
        candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        from json_repair import repair_json

        parsed = repair_json(candidate, return_objects=True)
        logger.warning(
            "golden-three readiness repair JSON normalized via json-repair "
            "(orig_len=%d)",
            len(raw),
        )
    if not isinstance(parsed, list):
        raise ValueError("golden-three repair response must be a JSON array")
    return parsed

# Chapter fields the repair may rewrite. Word targets, hype assignments and arc
# codes remain immutable bookkeeping. Methodology/causal contracts are
# different: the readiness judge reads them, so excluding them made the repair
# loop incapable of fixing the evidence that kept the gate closed.
_CHAPTER_FIELDS = ("chapter_goal", "opening_situation", "main_conflict", "hook_description")
_CHAPTER_METADATA_FIELDS = (
    "methodology_contract",
    "causal_contract",
    "event_cycle_contract",
)
_SCENE_JSON_FIELDS = ("purpose", "entry_state", "exit_state")
_SCENE_TEXT_FIELDS = ("hook_requirement",)
_SCENE_METADATA_FIELDS = ("methodology_contract",)


def _chapter_payload(chapter: Any) -> dict[str, Any]:
    chapter_metadata = (
        getattr(chapter, "metadata_json", None)
        if isinstance(getattr(chapter, "metadata_json", None), Mapping)
        else {}
    )
    return {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": getattr(chapter, "title", None) or "",
        **{f: getattr(chapter, f, None) or "" for f in _CHAPTER_FIELDS},
        **{
            field: chapter_metadata.get(field, {})
            for field in _CHAPTER_METADATA_FIELDS
        },
        "scenes": [
            {
                "scene_number": int(getattr(sc, "scene_number", 0) or 0),
                "scene_type": getattr(sc, "scene_type", None) or "",
                **{f: getattr(sc, f, None) for f in _SCENE_JSON_FIELDS},
                **{f: getattr(sc, f, None) for f in _SCENE_TEXT_FIELDS},
                **{
                    field: (
                        (getattr(sc, "metadata_json", None) or {}).get(field, {})
                        if isinstance(getattr(sc, "metadata_json", None), Mapping)
                        else {}
                    )
                    for field in _SCENE_METADATA_FIELDS
                },
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
    chapter_metadata = dict(getattr(chapter, "metadata_json", None) or {})
    chapter_metadata_changed = False
    for field in _CHAPTER_METADATA_FIELDS:
        value = revised.get(field)
        if (
            isinstance(value, Mapping)
            and value
            and dict(value) != chapter_metadata.get(field)
        ):
            chapter_metadata[field] = dict(value)
            chapter_metadata_changed = True
    if chapter_metadata_changed:
        chapter.metadata_json = chapter_metadata
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
        for field in _SCENE_JSON_FIELDS:
            value = scene_payload.get(field)
            if (
                isinstance(value, Mapping)
                and value
                and dict(value) != getattr(scene, field, None)
            ):
                setattr(scene, field, dict(value))
                changed = True
        for field in _SCENE_TEXT_FIELDS:
            value = scene_payload.get(field)
            if (
                isinstance(value, str)
                and value.strip()
                and value != getattr(scene, field, None)
            ):
                setattr(scene, field, value.strip())
                changed = True
        scene_metadata = dict(getattr(scene, "metadata_json", None) or {})
        scene_metadata_changed = False
        for field in _SCENE_METADATA_FIELDS:
            value = scene_payload.get(field)
            if (
                isinstance(value, Mapping)
                and value
                and dict(value) != scene_metadata.get(field)
            ):
                scene_metadata[field] = dict(value)
                scene_metadata_changed = True
        if scene_metadata_changed:
            scene.metadata_json = scene_metadata
            changed = True
    return changed


async def repair_golden_three_outline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters: list[Any],
    llm_judge_payload: Mapping[str, Any] | None,
    project: Any = None,
    project_brief: Mapping[str, Any] | None = None,
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
        "不引入新角色或新设定。修复必须落在字段本身——把主角的主动谋划、"
        "决策的信息/压力/成本逻辑、对手动机链直接写进 goal/conflict/hook/场景字段，"
        "并同步改正 methodology/causal/event contracts 中的旧说法，不是加批注。"
        "purpose/entry_state/exit_state 与各 contract 必须保持 JSON 对象，不能改成字符串。"
        "项目摘要里的已批准概念、人物身份和身体能力边界高于整改指令；"
        "如果整改示例与权威合同冲突，只修它指出的底层问题，必须换成合同允许的办法。"
        "输出与输入完全同构的 JSON 数组（同样的章节号、场景号与字段名），"
        "不要解释。"
    )
    user_prompt = (
        "【项目权威摘要】\n"
        + json.dumps(project_brief or {}, ensure_ascii=False, indent=1, default=str)[:8000]
        + "\n\n【整改指令（就绪判官）】\n" + "\n".join(issues) +
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
                    # ``LLMCompletionRequest`` requires a non-empty fallback.
                    # An empty string used to fail Pydantic validation before
                    # the repair model was called, silently turning the only
                    # bounded golden-three repair into a no-op.
                    fallback_response="[]",
                    prompt_template="golden_three_readiness_repair",
                    prompt_version="v1",
                    project_id=getattr(project, "id", None),
                    max_tokens_override=6000,
                ),
            )
            return completion.content or ""

    try:
        raw = str(await complete_fn(system_prompt, user_prompt) or "").strip()
        revised_list = _parse_revised_chapter_list(raw)
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

"""Universal reader-panel LLM judge for chapter text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import logging
from pathlib import Path
import re
from typing import Literal, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.reader_panel import DEFAULT_PANEL, ReaderRole
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

ReaderFeedbackSeverity = Literal["blocker", "high", "medium", "low"]


class ReaderFeedback(TypedDict):
    issue: str
    location: str
    severity: ReaderFeedbackSeverity
    role: str
    evidence: str
    suggested_attribution_hint: str | None


async def run_reader_panel(
    session: AsyncSession,
    settings: AppSettings,
    chapter_texts: Mapping[int, str],
    *,
    panel: Sequence[ReaderRole] = DEFAULT_PANEL,
    distilled_refs: Sequence[Path] = (),
    target_chapter_range: tuple[int, int] | None = None,
    workflow_run_id: UUID | None = None,
) -> list[ReaderFeedback]:
    """Run the configured reader panel and return normalized issue records."""

    selected = _select_chapters(chapter_texts, target_chapter_range)
    if not selected:
        return []

    feedback: list[ReaderFeedback] = []
    for role in panel:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="strong",
                system_prompt=_system_prompt(role),
                user_prompt=_user_prompt(
                    selected,
                    role=role,
                    distilled_refs=distilled_refs,
                ),
                fallback_response="[]",
                prompt_template="reader_panel_judge",
                prompt_version="v1",
                workflow_run_id=workflow_run_id,
                metadata={
                    "judge_scope": "reader_panel",
                    "reader_role": role.name,
                    "chapter_count": len(selected),
                },
                max_tokens_override=4096,
            ),
        )
        feedback.extend(_normalize_feedback(_parse_json_value(completion.content), role=role))
    return feedback


def _select_chapters(
    chapter_texts: Mapping[int, str],
    target_chapter_range: tuple[int, int] | None,
) -> dict[int, str]:
    start, end = target_chapter_range or (
        min(chapter_texts, default=1),
        max(chapter_texts, default=0),
    )
    return {
        chapter_no: text
        for chapter_no, text in sorted(chapter_texts.items())
        if start <= chapter_no <= end and text.strip()
    }


def _system_prompt(role: ReaderRole) -> str:
    schema = json.dumps(role.output_schema, ensure_ascii=False)
    return (
        "你是通用小说质量诊断 reader panel 的一个角色。"
        "不要依赖预设类型、术语或本书问题清单。"
        "只根据你读到的章节文本指出会破坏阅读体验的具体问题。"
        "\n\n"
        f"角色: {role.name}\n"
        f"人设: {role.persona}\n"
        f"任务: {role.instruction}\n\n"
        "严格只输出 JSON array。每一项必须符合 schema:\n"
        f"{schema}"
    )


def _user_prompt(
    chapter_texts: Mapping[int, str],
    *,
    role: ReaderRole,
    distilled_refs: Sequence[Path],
) -> str:
    chapters = [
        {"chapter_number": chapter_no, "text": text[:12000]}
        for chapter_no, text in sorted(chapter_texts.items())
    ]
    refs_block = ""
    if role.reference_corpus == "distillation":
        refs_block = "\n\n## 同类作品蒸馏参照\n" + "\n\n".join(
            _read_reference(path) for path in distilled_refs[:6]
        )
    return (
        "## 章节文本\n"
        f"{json.dumps(chapters, ensure_ascii=False, indent=2)}"
        f"{refs_block}\n\n"
        "## 输出要求\n"
        "输出 JSON array; 没有问题则输出 []。"
        "location 使用 chapter:<章号>:paragraph:<段号> 或 chapter:<章号>。"
        "severity 只能是 blocker/high/medium/low。"
        "evidence 必须引用或概括具体原文证据。"
    )


def _read_reference(path: Path) -> str:
    try:
        return f"### {path.name}\n{path.read_text(encoding='utf-8')[:4000]}"
    except OSError:
        return f"### {path.name}\n[unavailable]"


def _normalize_feedback(value: object, *, role: ReaderRole) -> list[ReaderFeedback]:
    items: Sequence[object]
    if isinstance(value, Mapping):
        raw = value.get("issues") or value.get("feedback") or value.get("findings") or []
        items = raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []
    elif isinstance(value, Sequence) and not isinstance(value, str):
        items = value
    else:
        items = []

    normalized: list[ReaderFeedback] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        issue = _string(item.get("issue") or item.get("problem") or item.get("description"))
        evidence = _string(item.get("evidence") or item.get("quote") or item.get("detail"))
        if not issue or not evidence:
            continue
        normalized.append(
            {
                "issue": issue,
                "location": _string(item.get("location") or item.get("path")) or "chapter:unknown",
                "severity": _coerce_severity(item.get("severity")),
                "role": _string(item.get("role")) or role.name,
                "evidence": evidence,
                "suggested_attribution_hint": _nullable_string(
                    item.get("suggested_attribution_hint")
                    or item.get("attribution_hint")
                    or item.get("root_cause_hint")
                ),
            }
        )
    return normalized


def _parse_json_value(text: str) -> object:
    stripped = text.strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    candidates = [stripped, unfenced]
    match = re.search(r"(\[.*\]|\{.*\})", unfenced, flags=re.S)
    if match:
        candidates.append(match.group(1))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    for candidate in candidates:
        try:
            from json_repair import repair_json

            return repair_json(candidate, return_objects=True)
        except Exception as exc:
            logger.debug("json_repair failed while parsing reader-panel output: %s", exc)
            continue
    return []


def _coerce_severity(value: object) -> ReaderFeedbackSeverity:
    normalized = str(value or "medium").strip().lower()
    if normalized in {"critical", "fatal", "blocking", "block"}:
        return "blocker"
    if normalized in {"blocker", "high", "medium", "low"}:
        return normalized  # type: ignore[return-value]
    if normalized in {"major", "severe"}:
        return "high"
    if normalized in {"minor", "warning", "warn"}:
        return "low"
    return "medium"


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping) or isinstance(value, Sequence):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value).strip()
    return str(value).strip()


def _nullable_string(value: object) -> str | None:
    text = _string(value)
    return text or None


__all__ = ["ReaderFeedback", "ReaderFeedbackSeverity", "run_reader_panel"]

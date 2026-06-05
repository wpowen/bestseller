from __future__ import annotations

# ruff: noqa: RUF001
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import re

from bestseller.infra.db.models import ChapterModel


class EscalationLevel(StrEnum):
    NORMAL = "normal"
    STRICT = "strict"
    FORCE_REDUCE = "force_reduce"
    DETERMINISTIC_TRUNCATE = "deterministic_truncate"
    MACHINE_REPAIR = "machine_repair"


@dataclass(frozen=True)
class EscalationDecision:
    level: EscalationLevel
    block_kind: str
    attempt_count: int
    strict_directive: str
    post_process_action: str | None


def decide_escalation(
    *,
    chapter: ChapterModel,
    block_codes: Sequence[str],
    current_word_count: int,
    target_word_count: int,
    hard_max_word_count: int,
    forbidden_terms_hit: Sequence[str] = (),
) -> EscalationDecision:
    block_kind = _classify_block_kind(block_codes, current_word_count, hard_max_word_count)
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    attempts_by_kind = dict(metadata.get("rewrite_attempts_by_kind") or {})
    attempt_count = int(attempts_by_kind.get(block_kind) or 0) + 1

    level = EscalationLevel.NORMAL
    post_process_action: str | None = None
    if attempt_count >= 6:
        level = EscalationLevel.MACHINE_REPAIR
    elif (
        block_kind == "length"
        and current_word_count > hard_max_word_count * 1.3
        and attempt_count >= 5
    ):
        level = EscalationLevel.DETERMINISTIC_TRUNCATE
        post_process_action = "truncate"
    elif block_kind == "forbidden_term" and attempt_count >= 5:
        level = EscalationLevel.DETERMINISTIC_TRUNCATE
        post_process_action = "regex_strip"
    elif attempt_count >= 4:
        level = EscalationLevel.FORCE_REDUCE
    elif attempt_count >= 3:
        level = EscalationLevel.STRICT

    strict_directive = _render_directive(
        chapter=chapter,
        level=level,
        block_kind=block_kind,
        attempt_count=attempt_count,
        current_word_count=current_word_count,
        target_word_count=target_word_count,
        forbidden_terms_hit=forbidden_terms_hit,
    )
    return EscalationDecision(
        level=level,
        block_kind=block_kind,
        attempt_count=attempt_count,
        strict_directive=strict_directive,
        post_process_action=post_process_action,
    )


def apply_post_process(
    text: str,
    decision: EscalationDecision,
    *,
    forbidden_terms: Sequence[str] = (),
) -> tuple[str, dict]:
    if decision.post_process_action == "regex_strip":
        replacements: dict[str, int] = {}
        modified = text or ""
        for term in forbidden_terms:
            clean = str(term or "").strip()
            if not clean:
                continue
            count = len(re.findall(re.escape(clean), modified))
            if count:
                modified = re.sub(re.escape(clean), "", modified)
                replacements[clean] = count
        return modified, {
            "action": "regex_strip",
            "replacements": replacements,
            "applied": bool(replacements),
        }
    if decision.post_process_action != "truncate":
        return text, {"action": None, "applied": False}

    original = text or ""
    target_chars = _target_chars_from_directive(decision.strict_directive)
    if target_chars <= 0 or len(original) <= target_chars:
        return original, {"action": "truncate", "applied": False}
    tail_chars = min(220, max(80, target_chars // 10))
    prefix_budget = max(0, target_chars - tail_chars)
    tail = original[-tail_chars:].lstrip()
    prefix = original[:prefix_budget].rstrip()
    modified = f"{prefix}\n\n{tail}".strip()
    return modified, {
        "action": "truncate",
        "applied": True,
        "before_chars": len(original),
        "after_chars": len(modified),
        "target_chars": target_chars,
        "ending_preserved_chars": tail_chars,
    }


def _classify_block_kind(
    block_codes: Sequence[str],
    current_word_count: int,
    hard_max_word_count: int,
) -> str:
    normalized = {str(code).upper() for code in block_codes}
    if current_word_count > hard_max_word_count or any(
        "LENGTH" in c or "BLOCK_HIGH" in c or "BLOCK_LOW" in c
        for c in normalized
    ):
        return "length"
    if any("FORBIDDEN" in c or "DEPRECATED" in c for c in normalized):
        return "forbidden_term"
    if any("CANON" in c or "STATE" in c for c in normalized):
        return "canon_state"
    return "general"


def _render_directive(
    *,
    chapter: ChapterModel,
    level: EscalationLevel,
    block_kind: str,
    attempt_count: int,
    current_word_count: int,
    target_word_count: int,
    forbidden_terms_hit: Sequence[str],
) -> str:
    if level == EscalationLevel.NORMAL:
        return ""
    if level == EscalationLevel.MACHINE_REPAIR:
        return (
            f"【机器深度修复】本章 {block_kind} 已连续 {attempt_count} 次未修复。"
            "停止重复同一改写策略；改用更小步的约束收敛修复，优先删除或替换命中问题。"
        )
    if block_kind == "forbidden_term":
        terms = "、".join(dict.fromkeys(str(t) for t in forbidden_terms_hit if str(t).strip()))
        terms = terms or "已命中的废弃/禁用正典词"
        return (
            f"【强制清除禁词 - 第 {attempt_count} 次重写】\n"
            f"当前正文仍然包含：{terms}。\n"
            "必须逐处替换或删除整句；禁止用注释、括号、模糊指代或近义废弃词绕过。"
        )
    if block_kind == "length":
        delta = max(0, int(current_word_count) - int(target_word_count))
        return (
            f"【强制缩字 - 这是第 {attempt_count} 次重写本章】\n"
            f"当前正文 v{getattr(chapter, 'revision_count', 0) or 0} = {current_word_count} 字。\n"
            f"目标 = {target_word_count} 字。必须删除 {delta} 字。\n"
            "删除优先级：重复内心独白、重复物件描写、无推进环境天气、角色重复小动作。"
            "保留对白、动作链、章末钩子和落地帧。不要扩写或换种说法，要真的删段落。"
        )
    return (
        f"【重写升级 - 第 {attempt_count} 次】本章连续触发 {block_kind} 问题。"
        "只修复命中问题；不要新增支线、解释性设定或额外角色。"
    )


def _target_chars_from_directive(text: str) -> int:
    match = re.search(r"目标\s*=\s*(\d+)", text or "")
    return int(match.group(1)) if match else 0


__all__ = [
    "EscalationDecision",
    "EscalationLevel",
    "apply_post_process",
    "decide_escalation",
]

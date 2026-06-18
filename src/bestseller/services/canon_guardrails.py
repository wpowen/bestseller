"""Project-level canon guardrails for chapter validation.

This module turns per-book canon constraints into deterministic L5 checks.
The guardrails can be stored in either:

* ``project.metadata_json["canon_guardrails"]`` for DB-native projects; or
* ``output/<project-slug>/story-bible/canon-guardrails.json`` for file-backed
  book packages and operator overrides.

The schema is intentionally simple so editors can maintain it by hand:

.. code-block:: json

    {
      "forbidden_terms": [
        {"term": "旧设定词", "reason": "已废弃", "suggestion": "改用新设定"}
      ],
      "state_rules": [
        {
          "subject": "角色名",
          "status": "已死亡",
          "forbidden_patterns": ["角色名.{0,20}正在被拖入"],
          "reason": "该状态已完成",
          "allowed_next": "只能写尸体、回忆、镜影或线索影响"
        }
      ]
    }
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CanonForbiddenTerm:
    term: str
    reason: str = ""
    suggestion: str = ""


@dataclass(frozen=True)
class CanonStateRule:
    subject: str
    status: str = ""
    applies_after_chapter: int | None = None
    forbidden_patterns: tuple[str, ...] = ()
    reason: str = ""
    allowed_next: str = ""


@dataclass(frozen=True)
class CanonGuardrails:
    forbidden_terms: tuple[CanonForbiddenTerm, ...] = ()
    state_rules: tuple[CanonStateRule, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.forbidden_terms and not self.state_rules


def canon_guardrails_from_mapping(raw: Any) -> CanonGuardrails:
    if not isinstance(raw, dict):
        return CanonGuardrails()

    forbidden_terms = tuple(
        item
        for item in (
            _parse_forbidden_term(entry)
            for entry in raw.get("forbidden_terms", ()) or ()
        )
        if item is not None
    )
    state_rules = tuple(
        item
        for item in (
            _parse_state_rule(entry)
            for entry in raw.get("state_rules", ()) or ()
        )
        if item is not None
    )
    return CanonGuardrails(
        forbidden_terms=forbidden_terms,
        state_rules=state_rules,
    )


def merge_canon_guardrails(*guardrails: CanonGuardrails) -> CanonGuardrails:
    terms: dict[str, CanonForbiddenTerm] = {}
    rules: list[CanonStateRule] = []
    seen_rules: set[tuple[str, tuple[str, ...]]] = set()

    for guardrail in guardrails:
        for term in guardrail.forbidden_terms:
            terms[term.term] = term
        for rule in guardrail.state_rules:
            key = (rule.subject, rule.forbidden_patterns)
            if key in seen_rules:
                continue
            seen_rules.add(key)
            rules.append(rule)

    return CanonGuardrails(
        forbidden_terms=tuple(terms.values()),
        state_rules=tuple(rules),
    )


def load_canon_guardrails_for_project(
    project: Any,
    *,
    output_base_dir: str | Path | None = None,
) -> CanonGuardrails:
    """Load guardrails from DB metadata and optional output package JSON."""

    sources: list[CanonGuardrails] = []

    metadata = getattr(project, "metadata_json", None)
    if isinstance(metadata, dict):
        sources.append(canon_guardrails_from_mapping(metadata.get("canon_guardrails")))

    slug = str(getattr(project, "slug", "") or "").strip()
    if output_base_dir is not None and slug:
        path = (
            Path(output_base_dir)
            / slug
            / "story-bible"
            / "canon-guardrails.json"
        )
        sources.append(load_canon_guardrails_file(path))

    return merge_canon_guardrails(*sources)


def load_canon_guardrails_file(path: str | Path) -> CanonGuardrails:
    effective = Path(path)
    if not effective.exists():
        return CanonGuardrails()
    try:
        raw = json.loads(effective.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CanonGuardrails()
    return canon_guardrails_from_mapping(raw)


def _parse_forbidden_term(raw: Any) -> CanonForbiddenTerm | None:
    if isinstance(raw, str):
        term = raw.strip()
        if not term:
            return None
        return CanonForbiddenTerm(term=term)
    if not isinstance(raw, dict):
        return None
    term = str(raw.get("term") or "").strip()
    if not term:
        return None
    return CanonForbiddenTerm(
        term=term,
        reason=str(raw.get("reason") or "").strip(),
        suggestion=str(raw.get("suggestion") or "").strip(),
    )


def _parse_state_rule(raw: Any) -> CanonStateRule | None:
    if not isinstance(raw, dict):
        return None
    subject = str(raw.get("subject") or "").strip()
    if not subject:
        return None
    patterns = tuple(
        str(item).strip()
        for item in raw.get("forbidden_patterns", ()) or ()
        if str(item).strip()
    )
    if not patterns:
        return None
    return CanonStateRule(
        subject=subject,
        status=str(raw.get("status") or "").strip(),
        applies_after_chapter=_parse_optional_positive_int(
            raw.get("applies_after_chapter")
        ),
        forbidden_patterns=patterns,
        reason=str(raw.get("reason") or "").strip(),
        allowed_next=str(raw.get("allowed_next") or "").strip(),
    )


def _parse_optional_positive_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def render_canon_guardrails_block(
    guardrails: CanonGuardrails | None,
    *,
    chapter_number: int | None = None,
    language: str = "zh-CN",
    max_forbidden_terms: int = 20,
    max_state_rules: int = 15,
) -> str:
    """Render a strong "MUST NOT" prompt block for the writing LLM.

    Filters state_rules by ``chapter_number``: rules whose
    ``applies_after_chapter`` is greater than ``chapter_number`` are
    ACTIVELY in effect (subject still forbidden), so they appear in the
    block. Rules where the threshold has been crossed are dropped (the
    character is now allowed to appear).
    """

    if guardrails is None or guardrails.is_empty:
        return ""

    active_rules: list[CanonStateRule] = []
    for rule in guardrails.state_rules:
        if rule.applies_after_chapter is None:
            active_rules.append(rule)
            continue
        if chapter_number is None:
            active_rules.append(rule)
            continue
        if chapter_number <= rule.applies_after_chapter:
            active_rules.append(rule)

    if not guardrails.forbidden_terms and not active_rules:
        return ""

    if language.lower().startswith("zh"):
        lines = ["【正典守护 — 严禁出现以下违规】"]
        if guardrails.forbidden_terms:
            lines.append("- 禁用旧版词汇/人名（任何形式不得出现）:")
            for term in guardrails.forbidden_terms[:max_forbidden_terms]:
                suffix = ""
                if term.suggestion:
                    suffix = f" → 改用: {term.suggestion}"
                lines.append(f"  · '{term.term}' ({term.reason}){suffix}")
        if active_rules:
            lines.append(
                "- 角色出场/状态禁令（当前章节生效，违反任何一条都退回重写）:"
            )
            for rule in active_rules[:max_state_rules]:
                if rule.applies_after_chapter is not None and chapter_number is not None:
                    threshold_text = (
                        f"【绝对禁止】角色 {rule.subject} 不得在第 "
                        f"{rule.applies_after_chapter} 章前以任何形式出现"
                        f"（不得有 {rule.subject} 的对白、动作、视角、心声或在场描写；"
                        f"不得让其他角色直接用名字 {rule.subject} 称呼或介绍他）。"
                    )
                    lines.append(f"  · {threshold_text}")
                    if rule.status or rule.reason:
                        lines.append(
                            f"    背景: {rule.status or rule.reason}"
                        )
                else:
                    lines.append(
                        f"  · {rule.subject}: {rule.status or rule.reason}"
                    )
                for pat in rule.forbidden_patterns[:3]:
                    lines.append(f"    具体禁用模式: {pat}")
                if rule.allowed_next:
                    lines.append(f"    替代写法: {rule.allowed_next}")
        lines.append(
            "- 违反任何一条都会让本章被判违规并触发重写。如果有任何角色想"
            "提前出场，请改用故事圣经/本章角色清单中已存在且已可出场的角色，"
            "不要为此新造角色。"
        )
        return "\n".join(lines)

    lines = ["[Canon Guardrails — DO NOT include the following]"]
    for term in guardrails.forbidden_terms[:max_forbidden_terms]:
        lines.append(f"  - '{term.term}': {term.reason}")
    for rule in active_rules[:max_state_rules]:
        lines.append(f"  - {rule.subject}: {rule.status or rule.reason}")
    return "\n".join(lines)


__all__ = [
    "CanonForbiddenTerm",
    "CanonStateRule",
    "CanonGuardrails",
    "canon_guardrails_from_mapping",
    "merge_canon_guardrails",
    "load_canon_guardrails_for_project",
    "load_canon_guardrails_file",
    "render_canon_guardrails_block",
]

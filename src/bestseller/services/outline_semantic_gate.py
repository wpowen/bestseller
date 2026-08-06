"""Deterministic whole-book semantic gate for promotion of an outline.

This gate intentionally has no LLM or database dependency.  It audits the
planning artifacts together because a locally plausible chapter can still be
promoted with the wrong identity, budget, or state contract.
"""

# The compatibility alias deliberately forwards arbitrary keyword shapes.
# ruff: noqa: ANN401

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class OutlineSemanticFinding:
    code: str
    severity: str
    chapter: int | None
    evidence: dict[str, Any]
    message: str = ""

    @property
    def chapter_number(self) -> int | None:
        """Compatibility name used by other quality finding consumers."""
        return self.chapter

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "severity": self.severity,
            "chapter": self.chapter,
            "evidence": dict(self.evidence),
        }
        if self.message:
            payload["message"] = self.message
        return payload


@dataclass(frozen=True, slots=True)
class OutlineSemanticReport:
    promotion_allowed: bool
    repairable: bool
    findings: tuple[OutlineSemanticFinding, ...]
    metrics: dict[str, Any]
    evaluator_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.promotion_allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_allowed": self.promotion_allowed,
            "repairable": self.repairable,
            "findings": [finding.to_dict() for finding in self.findings],
            "metrics": dict(self.metrics),
            "evaluator_error": self.evaluator_error,
        }


_MISSING = object()
_IDENTITY_KEYS = {
    "title": ("title", "book_title", "name"),
    "protagonist": (
        "protagonist",
        "protagonist_identity",
        "who",
        "main_character",
        "hero",
        "aliases",
        "alias",
        "alias_list",
        "character_names",
    ),
    "genre": ("genre", "subgenre", "category"),
    "setting": ("setting", "world_name", "world"),
}
_TONE_KEYS = ("tone", "voice", "style", "mood", "reader_tone")
_GOAL_KEYS = ("chapter_goal", "goal", "chapter_objective", "chapter_function")
_CONFLICT_KEYS = ("conflict", "main_conflict", "core_conflict", "obstacle")
_OPENING_KEYS = ("opening", "opening_pressure", "opening_situation", "front_hook")
_WORD_KEYS = ("estimated_chapter_words", "chapter_word_count", "word_count", "target_word_count")
_NUMERIC_KEYS = {
    "chapter_number",
    "chapter_no",
    "volume_number",
    "scene_count",
    "estimated_chapter_words",
    "chapter_word_count",
    "target_word_count",
    "word_count",
    "chapter_count_target",
    "target_chapters",
}
_META_RE = re.compile(
    r"(?:写前补齐|写前指定|本章只推进|阶段性兑现|接住上一章具体尾钩|落一个现实物证|"
    r"让主角主动判断|完成一个阶段性兑现|推动本章剧情发展|下一章钩子|待补充|待定|"
    r"本章交付(?:紧张|燃|震撼|爽|虐|暖|甜|悬疑)?|本章在卷中负责|推进第\s*\d+\s*卷主线|"
    r"(?:scene|chapter)\s*(?:summary|task|function)|as an ai|todo|tbd)",
    re.IGNORECASE,
)
_ROLE_SCHEMA_LEAK_RE = re.compile(
    r"[（(]\s*(?:母亲|父亲|主角|反派|导师|徒弟|哑仆|执事|弟子|"
    r"protagonist|antagonist|mentor|mother|father)\s*[）)]",
    re.IGNORECASE,
)
_QUOTED_ANCHOR_RE = re.compile(r"[“‘'\"]([^”’'\"\n]{6,80})[”’'\"]")
_CAUSAL_KEYS = (
    "pressure",
    "resistance",
    "protagonist_desire",
    "protagonist_choice",
    "visible_action_or_reaction",
    "cost_or_tradeoff",
    "state_change",
    "gain_or_reveal",
    "next_reader_desire",
)
_INFORMATION_KEYS = (
    "information_revealed",
    "key_reveals",
    "chapter_information_introduced",
    "information_release",
)
_TITLE_PLACEHOLDER_RE = re.compile(
    r"^(?:第?\s*(?:x|n|\?)\s*章|未定|待定|标题待定|chapter\s*(?:x|n|\?)?|"
    r"placeholder|todo|untitled)$",
    re.IGNORECASE,
)
_GENERIC_RE = re.compile(
    r"^(?:推动剧情|推进主线|完成任务|解决问题|引出冲突|继续调查|日常过渡|本章目标|"
    r"advance the plot|continue the story|resolve the conflict)$",
    re.IGNORECASE,
)
_TONE_GROUPS = (
    {"dark", "grim", "tragic", "阴郁", "黑暗", "悲剧", "高压", "冷硬", "压抑"},
    {"light", "comedic", "warm", "轻松", "喜剧", "温暖", "治愈", "幽默"},
    {"romantic", "romance", "浪漫", "言情"},
    {"thriller", "suspense", "惊悚", "悬疑"},
)


# These findings describe objective machine contracts.  They remain hard
# failures even when an LLM likes the story: a judge cannot make a missing
# field, an invalid chapter number, or a regressing state transition usable by
# downstream code.  Every other high-severity finding is a semantic candidate
# (often regex/similarity based) and must be adjudicated against the actual
# outline by the LLM commercial judge before it can veto promotion.
OUTLINE_HARD_CONTRACT_CODES = frozenset(
    {
        "OUTLINE_SEMANTIC_EVALUATOR_ERROR",
        "OUTLINE_INVALID_NUMERIC_STATE",
        "OUTLINE_INVALID_STATE_TRANSITION",
        "OUTLINE_STATE_REGRESSION",
        "OUTLINE_WORD_BUDGET_MISMATCH",
        "OUTLINE_PLACEHOLDER_TITLE",
        "OUTLINE_CAUSAL_CONTRACT_DEGENERATE",
        "OUTLINE_INFORMATION_CONTRACT_GAP",
        "OUTLINE_CONTRADICTORY_TRANSFER_ACCEPT_RETURN",
    }
)


#: Codes that are still DETECTED and reported, but may never block promotion
#: on their own, because the evidence available at the call site cannot support
#: the claim they make.
#:
#: ``OUTLINE_REUSED_PAYLOAD_ANCHOR`` claims to catch "a stale batch replaying an
#: already-spent payload", but every enforcement site sees only ONE 3-chapter
#: batch, so cross-batch replay is invisible to it. At that scope its rule
#: ("a quoted phrase of 6+ chars appearing in two chapters 2+ apart") reduces to
#: "the first and last chapter mention the same thing" — which is exactly what a
#: book with a named recurring mechanism does deliberately. Its repair directive
#: tells the model to delete that mechanism, so no repair attempt can ever pass:
#: 《仇人膝上养帝王》 failed it 3× on its own core device and the auto-resume loop
#: then burned ~880k tokens for zero chapters (2026-07-25).
#:
#: Declared here rather than at a call site because the first fix was applied to
#: the batch gate only, and the whole-book gate re-derived its blocking set from
#: raw severity — silently re-admitting the code and crashing a book the next
#: day (2026-07-26). One set, honoured everywhere.
OUTLINE_ADVISORY_ONLY_CODES = frozenset({"OUTLINE_REUSED_PAYLOAD_ANCHOR"})

_BLOCKING_SEVERITIES = frozenset({"critical", "high", "block"})


def hard_contract_findings(
    report: OutlineSemanticReport,
) -> tuple[OutlineSemanticFinding, ...]:
    """Return deterministic findings that an LLM is not allowed to override."""

    return tuple(
        finding
        for finding in report.findings
        if finding.severity in _BLOCKING_SEVERITIES
        and finding.code in OUTLINE_HARD_CONTRACT_CODES
        and finding.code not in OUTLINE_ADVISORY_ONLY_CODES
    )


def blocking_findings_for_promotion(
    report: OutlineSemanticReport,
    *,
    llm_adjudicated: bool,
) -> tuple[OutlineSemanticFinding, ...]:
    """The findings that may stop an outline from being promoted.

    Single entry point for both enforcement branches. When an LLM adjudicated
    every volume, only the deterministic hard-contract codes still bind (the
    LLM was allowed to clear the judgement calls). Otherwise every blocking
    severity binds — MINUS the advisory-only codes, which is the step the
    whole-book gate's inline copy used to skip.
    """

    if llm_adjudicated:
        return hard_contract_findings(report)
    return tuple(
        finding
        for finding in report.findings
        if finding.severity in _BLOCKING_SEVERITIES
        and finding.code not in OUTLINE_ADVISORY_ONLY_CODES
    )


def llm_adjudication_candidates(
    report: OutlineSemanticReport,
) -> tuple[OutlineSemanticFinding, ...]:
    """Return semantic/heuristic findings that require contextual judgment."""

    return tuple(
        finding
        for finding in report.findings
        if finding.severity in {"critical", "high", "block"}
        and finding.code not in OUTLINE_HARD_CONTRACT_CODES
    )


def evaluate_outline_semantic_gate(
    payload: Mapping[str, Any] | None = None,
    *,
    story_spine: Mapping[str, Any] | None = None,
    commercial_brief: Mapping[str, Any] | None = None,
    identity_manifest: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    chapters: Sequence[Mapping[str, Any]] | None = None,
    outline: Sequence[Mapping[str, Any]] | None = None,
) -> OutlineSemanticReport:
    """Audit a whole-book outline and fail closed on evaluator errors."""
    try:
        root = dict(payload or {})
        story = story_spine if story_spine is not None else _mapping(root.get("story_spine"))
        brief = (
            commercial_brief
            if commercial_brief is not None
            else _mapping(root.get("commercial_brief"))
        )
        manifest = _identity_manifest_mapping(
            identity_manifest
            if identity_manifest is not None
            else root.get("identity_manifest") or root.get("identity")
        )
        rows = chapters if chapters is not None else outline
        if rows is None:
            rows = root.get("chapters") or root.get("chapter_outlines") or root.get("outline") or ()
        chapter_rows = [row for row in rows if isinstance(row, Mapping)]
        findings: list[OutlineSemanticFinding] = []
        _identity_findings(findings, story, brief, manifest)
        _tone_findings(findings, story, brief, manifest)
        _numeric_findings(findings, root, chapter_rows)
        _state_transition_findings(findings, chapter_rows)
        _budget_findings(findings, root, story, brief, chapter_rows)
        _content_findings(findings, chapter_rows)
        _duplicate_findings(findings, chapter_rows)
        _contract_findings(findings, chapter_rows)
        _hook_diversity_findings(findings, chapter_rows)
        _reused_anchor_findings(findings, chapter_rows)
        _contradiction_findings(findings, chapter_rows)
        blocking = [
            finding for finding in findings if finding.severity in {"critical", "high", "block"}
        ]
        return OutlineSemanticReport(
            promotion_allowed=not blocking,
            repairable=bool(findings)
            and all(finding.severity != "critical" for finding in findings),
            findings=tuple(findings),
            metrics={"chapters": len(chapter_rows), "finding_count": len(findings)},
        )
    except Exception as exc:  # deterministic gate must never fail open
        finding = OutlineSemanticFinding(
            code="OUTLINE_SEMANTIC_EVALUATOR_ERROR",
            severity="critical",
            chapter=None,
            evidence={"error_type": type(exc).__name__, "error": str(exc)},
            message="Semantic evaluator failed; promotion is denied.",
        )
        return OutlineSemanticReport(
            False, False, (finding,), {"chapters": 0, "finding_count": 1}, str(exc)
        )


def evaluate_whole_book_outline_semantic_gate(*args: Any, **kwargs: Any) -> OutlineSemanticReport:
    """Descriptive alias for callers that name the scope explicitly."""
    return evaluate_outline_semantic_gate(*args, **kwargs)


evaluate_outline_semantics = evaluate_outline_semantic_gate


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _identity_manifest_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        rows = value.get("entities") or value.get("characters")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            return value
        base = dict(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = value
        base = {}
    else:
        return {}
    characters = [row for row in rows if isinstance(row, Mapping)]
    protagonist = next(
        (
            row
            for row in characters
            if _norm(row.get("role") or row.get("entity_role") or row.get("type"))
            in {"protagonist", "main_character", "主角", "hero"}
            or bool(row.get("is_protagonist"))
        ),
        characters[0] if characters else {},
    )
    base["protagonist"] = _value(
        protagonist,
        ("canonical_name", "name", "display_name", "identity"),
    )
    return base


_MECHANISM_ANCHOR_RE = re.compile(
    r"(暗号|口令|代号|机制|密令|密码|咒|咒法|法阵|法术|仪式|秘术|凭条|检验符|记号|符咒|机制感知|"
    r"信号|信物|凭证|印记)",
    re.IGNORECASE,
)
_GENERIC_IDENTITY_NOISE_TOKENS = {
    "protagonist",
    "主角",
    "男主角",
    "女主角",
    "角色",
    "角色名",
    "人物",
}


def _value(source: Mapping[str, Any], keys: Sequence[str]) -> object:
    for key in keys:
        value = source.get(key, _MISSING)
        if value is not _MISSING and value not in (None, "", []):
            return value
    return _MISSING


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _identity_norm(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[‘'\"“”\"'\(\)\[\]【】《》<>]", "", text)
    return text


def _identity_tokens(value: object) -> set[str]:
    raw = str(value or "")
    if not raw:
        return set()
    values: list[str] = []
    for chunk in re.split(r"[,，/|;；]+", raw):
        chunk = chunk.strip()
        if chunk:
            values.append(chunk)
            values.extend(item.strip() for item in re.split(r"\(|（|\\|/", chunk))
    tokens: set[str] = set()
    for value in values:
        parts = _identity_norm(value)
        if not parts:
            continue
        if parts in _GENERIC_IDENTITY_NOISE_TOKENS:
            continue
        tokens.add(parts)
    return tokens


def _identity_values(source: Mapping[str, Any], keys: Sequence[str]) -> set[str]:
    collected: set[str] = set()
    for key in keys:
        raw = source.get(key, _MISSING)
        if raw is _MISSING or raw in (None, "", [], {}):
            continue
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for item in raw:
                if isinstance(item, Mapping):
                    collected.update(_identity_tokens(
                        item.get("name")
                        or item.get("canonical_name")
                        or item.get("display_name")
                        or item.get("identity")
                    ))
                    collected.update(_identity_tokens(item.get("aliases") or ()))
                else:
                    collected.update(_identity_tokens(item))
            continue
        if isinstance(raw, Mapping):
            collected.update(_identity_values(raw, ("canonical_name", "name", "display_name", "identity")))
            collected.update(_identity_tokens(raw.get("aliases")))
            continue
        collected.update(_identity_tokens(raw))
    return collected


def _add(
    findings: list[OutlineSemanticFinding],
    code: str,
    severity: str,
    chapter: int | None,
    evidence: dict[str, Any],
    message: str,
) -> None:
    findings.append(OutlineSemanticFinding(code, severity, chapter, evidence, message))


def _identity_findings(findings: list[OutlineSemanticFinding], *sources: Mapping[str, Any]) -> None:
    for name, keys in _IDENTITY_KEYS.items():
        value_sets = [item for item in (_identity_values(source, keys) for source in sources) if item]
        if len(value_sets) < 2:
            continue
        first, *remaining = value_sets
        shared = {
            candidate
            for candidate in first
            if all(
                any(
                    candidate == other
                    or (len(candidate) >= 2 and candidate in other)
                    or (len(other) >= 2 and other in candidate)
                    for other in values
                )
                for values in remaining
            )
        }
        drifted = not bool(shared)
        if drifted:
            all_values = [
                sorted(values) for values in value_sets
            ]
            _add(
                findings,
                "OUTLINE_IDENTITY_MISMATCH",
                "critical",
                None,
                {"field": name, "values": all_values},
                f"{name} differs across planning artifacts",
            )


def _tone_findings(findings: list[OutlineSemanticFinding], *sources: Mapping[str, Any]) -> None:
    values = [_norm(_value(source, _TONE_KEYS)) for source in sources]
    present = [value for value in values if value]
    source_groups = [
        {
            index
            for index, group in enumerate(_TONE_GROUPS)
            if any(token in value for token in group)
        }
        for value in present
    ]
    classified = [groups for groups in source_groups if groups]
    shared_groups = set.intersection(*classified) if classified else set()
    all_groups = set().union(*classified) if classified else set()
    # A source may intentionally declare a blended tone (for example
    # "cold suspense with restrained humour").  That remains compatible with
    # a creator's `light` preference because the sources share the light group.
    # Only flag when independently authoritative sources have no tone family in
    # common at all.
    if len(classified) > 1 and len(all_groups) > 1 and not shared_groups:
        _add(
            findings,
            "OUTLINE_TONE_MISMATCH",
            "high",
            None,
            {
                "values": values,
                "tone_groups_by_source": [sorted(groups) for groups in source_groups],
            },
            "tone labels conflict across artifacts",
        )


def _numeric_findings(
    findings: list[OutlineSemanticFinding],
    root: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    for index, row in enumerate(rows, 1):
        chapter = _chapter_no(row, index)
        for key in _NUMERIC_KEYS:
            if key not in row:
                continue
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                code = (
                    "OUTLINE_LEGACY_NUMERIC_STATE"
                    if isinstance(value, str) and value.strip().isdigit()
                    else "OUTLINE_INVALID_NUMERIC_STATE"
                )
                _add(
                    findings,
                    code,
                    "high",
                    chapter,
                    {"field": key, "value": value, "expected": "number"},
                    f"{key} has an invalid typed state",
                )
    for key in _NUMERIC_KEYS:
        if key in root and (isinstance(root[key], bool) or not isinstance(root[key], (int, float))):
            _add(
                findings,
                "OUTLINE_INVALID_NUMERIC_STATE",
                "high",
                None,
                {"field": key, "value": root[key], "expected": "number"},
                f"{key} has an invalid typed state",
            )


_PERCENT_STATE_RE = re.compile(
    r"(?P<label>[\u4e00-\u9fffA-Za-z_]{1,16}(?:复刻度|完整度|进度|暴露度|完成度|率))"
    r"(?:直接|已经|再次|首次|约|达到|升至|突破|超过|逼近|降至|回落至|为|=|：|:|\s)*"
    r"(?P<value>\d+(?:\.\d+)?)\s*%"
)


def _state_transition_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    latest_percent: dict[str, tuple[int, float]] = {}
    for index, row in enumerate(rows, 1):
        chapter = _chapter_no(row, index)
        for transition in _find_transition_mappings(row):
            try:
                from bestseller.domain.story_state import (
                    StoryStateTransition,
                    validate_story_state_transition,
                )

                validate_story_state_transition(StoryStateTransition(**transition))
            except Exception as exc:  # noqa: BLE001
                _add(
                    findings,
                    "OUTLINE_INVALID_STATE_TRANSITION",
                    "critical",
                    chapter,
                    {"transition": dict(transition), "error": str(exc)},
                    "typed story state transition is invalid",
                )
        for match in _PERCENT_STATE_RE.finditer(_row_text(row)):
            label = _norm(match.group("label"))
            value = float(match.group("value"))
            previous = latest_percent.get(label)
            if previous is not None and value < previous[1]:
                _add(
                    findings,
                    "OUTLINE_STATE_REGRESSION",
                    "critical",
                    chapter,
                    {
                        "variable": label,
                        "previous_chapter": previous[0],
                        "previous": previous[1],
                        "after": value,
                    },
                    f"monotonic story state regressed from {previous[1]}% to {value}%",
                )
            latest_percent[label] = (chapter, value)


def _find_transition_mappings(value: object) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"state_transitions", "story_state_transitions"} and isinstance(
                child, Sequence
            ) and not isinstance(child, (str, bytes)):
                yield from (item for item in child if isinstance(item, Mapping))
            yield from _find_transition_mappings(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            yield from _find_transition_mappings(child)


def _budget_findings(
    findings: list[OutlineSemanticFinding],
    root: Mapping[str, Any],
    story: Mapping[str, Any],
    brief: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    budget = _mapping(root.get("word_budget"))
    if not budget:
        budget = _mapping(root.get("words_per_chapter"))
    # A chapter target cannot be judged against a universal 1,800-3,500 range:
    # projects carry their own authoritative total/chapter budgets and long-form
    # books legitimately normalize above that range.  Only enforce per-chapter
    # outliers when the evaluated payload supplies an explicit range contract.
    minimum = _number(_value(budget, ("min", "minimum"))) if budget else None
    maximum = _number(_value(budget, ("max", "maximum"))) if budget else None
    target = (
        _number(_value(budget, ("target", "per_chapter", "chapter")))
        if budget
        else None
    )
    outliers = []
    total = 0.0
    for index, row in enumerate(rows, 1):
        words = _number(_value(row, _WORD_KEYS))
        if words is None:
            continue
        total += words
        if (
            minimum is not None
            and maximum is not None
            and (words < minimum or words > maximum)
        ):
            outliers.append(
                {"chapter": _chapter_no(row, index), "words": words, "range": [minimum, maximum]}
            )
    expected_total = _number(
        _value(
            root,
            ("total_word_budget", "target_total_words", "target_word_count"),
        )
    )
    if expected_total is None:
        expected_total = _number(
            _value(story, ("total_word_budget", "target_total_words"))
        ) or _number(_value(brief, ("total_word_budget", "target_total_words")))
    mismatch = outliers or (
        expected_total is not None
        and rows
        and abs(total - expected_total) > max(1.0, expected_total * 0.01)
    )
    if mismatch:
        _add(
            findings,
            "OUTLINE_WORD_BUDGET_MISMATCH",
            "high",
            None,
            {
                "minimum": minimum,
                "target": target,
                "maximum": maximum,
                "estimated_total": total,
                "expected_total": expected_total,
                "outliers": outliers,
            },
            "whole-book word budget is inconsistent",
        )


def _content_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    repeated: dict[str, Counter[str]] = {
        "goal": Counter(),
        "conflict": Counter(),
        "opening": Counter(),
    }
    locations: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(rows, 1):
        chapter = _chapter_no(row, index)
        title = str(
            _value(row, ("chapter_title", "title"))
            if _value(row, ("chapter_title", "title")) is not _MISSING
            else ""
        ).strip()
        if title and _TITLE_PLACEHOLDER_RE.fullmatch(title):
            _add(
                findings,
                "OUTLINE_PLACEHOLDER_TITLE",
                "high",
                chapter,
                {"title": title},
                "chapter title is a placeholder",
            )
        for field, keys, code in (
            ("goal", _GOAL_KEYS, "OUTLINE_GOAL_DEGENERATE"),
            ("conflict", _CONFLICT_KEYS, "OUTLINE_CONFLICT_DEGENERATE"),
            ("opening", _OPENING_KEYS, "OUTLINE_OPENING_DEGENERATE"),
        ):
            value = _value(row, keys)
            text = "" if value is _MISSING else str(value).strip()
            normalized = _norm(text)
            if normalized:
                repeated[field][normalized] += 1
                locations.setdefault((field, normalized), []).append(chapter)
            if not text or _GENERIC_RE.fullmatch(text) or _META_RE.search(text):
                _add(
                    findings,
                    code,
                    "high",
                    chapter,
                    {"field": field, "value": text},
                    f"chapter {chapter} has a degenerate {field}",
                )
        text = _row_text(row)
        hits = _META_RE.findall(text)
        if hits:
            _add(
                findings,
                "OUTLINE_META_LANGUAGE",
                "critical",
                chapter,
                {"matches": sorted(set(hits))},
                "outline contains task or template language",
            )
        role_schema_hits = _ROLE_SCHEMA_LEAK_RE.findall(text)
        if role_schema_hits:
            _add(
                findings,
                "OUTLINE_ROLE_SCHEMA_LEAK",
                "high",
                chapter,
                {"matches": sorted(set(role_schema_hits))[:8]},
                "outline leaks cast-schema annotations into story language",
            )
    for field, counts in repeated.items():
        for value, count in counts.items():
            if count >= 2 and not _GENERIC_RE.fullmatch(value):
                _add(
                    findings,
                    f"OUTLINE_{field.upper()}_DEGENERATE",
                    "high",
                    locations[(field, value)][0],
                    {"field": field, "value": value, "chapters": locations[(field, value)]},
                    f"same {field} is repeated across chapters",
                )


def _duplicate_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    signatures: list[tuple[int, str]] = []
    for index, row in enumerate(rows, 1):
        raw_signature = _value(
            row,
            ("event_signature", "signature", "event", "summary", "chapter_goal"),
        )
        # ``_MISSING`` is an identity sentinel, not story content. Passing it
        # through ``str`` made every chapter without an explicit event signature
        # look like the same ``<object object at ...>`` event.
        if raw_signature is _MISSING:
            continue
        signature = _norm(raw_signature)
        if signature:
            signatures.append((_chapter_no(row, index), signature))
    counts = Counter(signature for _, signature in signatures)
    for signature, count in counts.items():
        if count > 1:
            chapters = [chapter for chapter, value in signatures if value == signature]
            _add(
                findings,
                "OUTLINE_DUPLICATE_EVENT_SIGNATURE",
                "high",
                chapters[0],
                {"signature": signature, "chapters": chapters},
                "event signature is duplicated",
            )
    for (chapter_a, signature_a), (chapter_b, signature_b) in pairwise(signatures):
        if signature_a == signature_b and chapter_b == chapter_a + 1:
            _add(
                findings,
                "OUTLINE_ADJACENT_REPETITION",
                "high",
                chapter_b,
                {"previous_chapter": chapter_a, "signature": signature_b},
                "adjacent chapters repeat the same event",
            )


def _row_or_metadata_value(row: Mapping[str, Any], keys: Sequence[str]) -> object:
    value = _value(row, keys)
    if value is not _MISSING:
        return value
    metadata = _mapping(row.get("metadata"))
    return _value(metadata, keys)


def _has_meaningful_value(value: object) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_meaningful_value(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_meaningful_value(child) for child in value)
    return True


def _contract_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    """Reject broken chapter contracts before prose can consume them.

    Two live failure modes motivate this check:

    * one rolling batch carried dedicated information-release contracts while
      the middle batch silently dropped the field entirely;
    * a fallback chapter filled nine causal slots by copying the same sentence
      into pressure, resistance, cost, state change, reveal and next desire.

    Existence-only validation considered both shapes complete.  Cross-chapter
    schema consistency and within-contract value diversity are therefore part
    of semantic promotion, not optional style advice.
    """

    information_presence: list[tuple[int, bool]] = []
    for index, row in enumerate(rows, 1):
        chapter = _chapter_no(row, index)
        information_presence.append(
            (
                chapter,
                any(
                    _has_meaningful_value(_row_or_metadata_value(row, (key,)))
                    for key in _INFORMATION_KEYS
                ),
            )
        )

        raw_contract = _row_or_metadata_value(
            row,
            ("causal_contract", "causality_contract", "chapter_causal_skeleton"),
        )
        contract = _mapping(raw_contract)
        if not contract:
            continue
        normalized_values: dict[str, list[str]] = {}
        for key in _CAUSAL_KEYS:
            value = contract.get(key)
            if not _has_meaningful_value(value):
                continue
            normalized_values.setdefault(_norm(value), []).append(key)
        repeated_groups = [
            {"fields": keys, "value": value[:240]}
            for value, keys in normalized_values.items()
            if value and len(keys) >= 3
        ]
        if repeated_groups:
            _add(
                findings,
                "OUTLINE_CAUSAL_CONTRACT_DEGENERATE",
                "high",
                chapter,
                {"repeated_groups": repeated_groups},
                "causal contract copies one sentence across distinct causal duties",
            )

    # Backward-compatible rule: older outlines that never carried a dedicated
    # information contract are not rejected here.  Once any chapter in the
    # same promoted batch uses the schema, however, holes in the middle are a
    # broken rolling-batch contract and must fail promotion.
    if any(present for _, present in information_presence):
        missing = [chapter for chapter, present in information_presence if not present]
        if missing:
            _add(
                findings,
                "OUTLINE_INFORMATION_CONTRACT_GAP",
                "high",
                missing[0],
                {"missing_chapters": missing},
                "information-release contract disappears inside one outline batch",
            )


def _hook_diversity_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    streak: list[tuple[int, str]] = []
    for index, row in enumerate(rows, 1):
        chapter = _chapter_no(row, index)
        hook_type = _norm(_row_or_metadata_value(row, ("hook_type", "chapter_hook_type")))
        if hook_type and streak and streak[-1][1] == hook_type:
            streak.append((chapter, hook_type))
        else:
            streak = [(chapter, hook_type)] if hook_type else []
        if len(streak) == 3:
            _add(
                findings,
                "OUTLINE_HOOK_TYPE_STREAK",
                "medium",
                streak[0][0],
                {
                    "hook_type": hook_type,
                    "chapters": [number for number, _ in streak],
                },
                "three consecutive chapters reuse the same hook mechanism",
            )


def _reused_anchor_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    locations: dict[str, list[int]] = {}
    surfaces: dict[str, str] = {}
    for index, row in enumerate(rows, 1):
        chapter = _chapter_no(row, index)
        for match in _QUOTED_ANCHOR_RE.finditer(_row_text(row)):
            surface = match.group(1).strip()
            normalized = _norm(surface)
            if len(normalized) < 6:
                continue
            surfaces.setdefault(normalized, surface)
            locations.setdefault(normalized, []).append(chapter)
    for normalized, raw_chapters in locations.items():
        chapters = sorted(set(raw_chapters))
        # Adjacent chapters may deliberately echo the previous closing hook.
        # Reappearing after an intervening chapter is the live signature of a
        # stale batch replaying an already-spent event payload.
        if len(chapters) >= 2 and chapters[-1] - chapters[0] >= 2:
            severity = "medium" if _MECHANISM_ANCHOR_RE.search(surfaces[normalized]) else "high"
            _add(
                findings,
                "OUTLINE_REUSED_PAYLOAD_ANCHOR",
                severity,
                chapters[0],
                {"anchor": surfaces[normalized], "chapters": chapters},
                "a distinctive quoted event payload is reused after it should be spent",
            )


def _contradiction_findings(
    findings: list[OutlineSemanticFinding], rows: Sequence[Mapping[str, Any]]
) -> None:
    for index, row in enumerate(rows, 1):
        text = _row_text(row).lower()
        transfer = bool(re.search(r"transfer|转移|调任|移交|塞进|递给|交给", text))
        accept_return = bool(
            re.search(
                r"accept.{0,60}return|return.{0,60}accept|"
                r"(?:接受|接下|接过).{0,60}(?:归还|送还|退回)|"
                r"(?:归还|送还|退回).{0,60}(?:接受|接下|接过)",
                text,
            )
        )
        if transfer and accept_return:
            _add(
                findings,
                "OUTLINE_CONTRADICTORY_TRANSFER_ACCEPT_RETURN",
                "high",
                _chapter_no(row, index),
                {"text": text[:500]},
                "same chapter contains contradictory transfer and accept/return events",
            )


def _row_text(row: Mapping[str, Any]) -> str:
    values: list[str] = []

    def _walk(value: object) -> None:
        if isinstance(value, Mapping):
            for child in value.values():
                _walk(child)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for child in value:
                _walk(child)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            values.append(str(value))

    _walk(row)
    return "\n".join(values)


def _chapter_no(row: Mapping[str, Any], fallback: int) -> int:
    value = row.get("chapter_number", row.get("chapter_no", fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _number(value: object) -> float | None:
    if value is _MISSING or isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


__all__ = [
    "OutlineSemanticFinding",
    "OutlineSemanticReport",
    "evaluate_outline_semantic_gate",
    "evaluate_outline_semantics",
    "evaluate_whole_book_outline_semantic_gate",
]

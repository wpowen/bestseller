"""Chapter-level antagonist audit.

Root cause this module addresses
--------------------------------

Even after the planner builds a per-volume antagonist roster correctly
(see ``antagonist_lifecycle.py``), the *writer* can still reach for the
wrong name inside a chapter — typically carrying forward an antagonist
from an earlier volume because the context packet leaked a stale
fragment, or because the LLM defaulted to the most-mentioned villain.
The user observed this first-hand in the 道种破虚 run: volume 7 chapters
still dropped volume-2 boss "元婴老者" into present-tense combat beats.

Fixing the planner upstream is necessary but not sufficient — the
already-written chapters must be audited post-hoc so the drafts can be
regenerated, and future runs must validate chapter text against the
volume antagonist set as part of the review gate.

Scope & heuristics
------------------

This module is a **structural audit**, not a style judgement. It flags:

  * **dominant_foreign_antagonist** (critical) — a non-scope antagonist
    is named ≥ ``salience_threshold`` times in a chapter. A one-off
    flashback reference is fine; repeated present-tense usage is not.
  * **passing_foreign_antagonist** (warning) — a single-mention of a
    non-scope antagonist, which might be legitimate (a character
    remembering a past enemy) but still worth surfacing.

Both findings are **name-based**. To avoid false positives we:

  * ignore antagonist names shorter than 2 CJK chars / 3 ASCII chars;
  * strip a small denylist of generic labels ("敌人", "魔头", "boss",
    "villain", …) that would otherwise match in any chapter;
  * treat each antagonist as *in-scope* for every volume listed in its
    ``scope_volume_number`` OR ``stages_of_relevance`` range(s).

The audit is pure — it does not touch the DB. Callers hand in chapter
records and antagonist plans as plain mappings / pydantic models, and
receive a frozen report.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# A non-scope antagonist mentioned ≥ this many times is "dominant" and
# treated as a critical finding — the chapter is effectively pretending
# that antagonist still matters.
DEFAULT_SALIENCE_THRESHOLD: int = 3

# Names shorter than this many CJK characters are rejected as too
# ambiguous (1-char antagonist names collide with common words).
MIN_CJK_NAME_LEN: int = 2

# Names shorter than this many ASCII characters are rejected.
MIN_ASCII_NAME_LEN: int = 3

# Generic labels that masquerade as antagonist names but are in fact
# descriptors — a chapter matches these in every paragraph so they're
# useless for scope detection.
GENERIC_LABEL_DENYLIST: frozenset[str] = frozenset(
    {
        # zh-CN
        "敌人",
        "对手",
        "反派",
        "魔头",
        "反贼",
        "邪魔",
        "恶徒",
        "仇敌",
        "敌方",
        "boss",
        # en
        "villain",
        "enemy",
        "antagonist",
        "foe",
        "adversary",
        "opponent",
    }
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChapterAntagonistFinding:
    """One audit finding against a single chapter."""

    code: str  # "dominant_foreign_antagonist" | "passing_foreign_antagonist"
    severity: str  # "critical" | "warning"
    chapter_number: int
    volume_number: int
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChapterAudit:
    """Per-chapter audit verdict."""

    chapter_number: int
    volume_number: int
    expected_antagonists: tuple[str, ...]
    mentioned_expected: tuple[str, ...]
    mentioned_out_of_scope: tuple[tuple[str, int], ...]  # (name, count)
    findings: tuple[ChapterAntagonistFinding, ...]

    @property
    def is_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


@dataclass(frozen=True)
class ChapterAntagonistReport:
    """Aggregate audit across all chapters."""

    total_chapters: int
    total_volumes: int
    total_antagonists: int
    chapter_audits: tuple[ChapterAudit, ...]
    findings: tuple[ChapterAntagonistFinding, ...]

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def is_critical(self) -> bool:
        return self.critical_count > 0

    @property
    def critical_chapter_numbers(self) -> tuple[int, ...]:
        return tuple(
            sorted({f.chapter_number for f in self.findings if f.severity == "critical"})
        )

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        if not self.findings:
            return ""
        is_en = _is_english(language)
        lines: list[str] = []
        if is_en:
            lines.append("[CHAPTER ANTAGONIST AUDIT — hard requirements]")
            lines.append(
                "- Each chapter's present-tense antagonist MUST be one of "
                "the antagonists scoped to its volume."
            )
            lines.append(
                "- Past-tense flashback references to earlier volumes are "
                "allowed, but they must not dominate (mention count < "
                f"{DEFAULT_SALIENCE_THRESHOLD})."
            )
            lines.append("")
            lines.append("Findings (fix ALL critical):")
        else:
            lines.append("【章节敌人审查 — 硬性要求】")
            lines.append("- 每一章的当下敌人必须属于该章所在卷的敌人名单。")
            lines.append(
                "- 允许以回忆形式提及过往卷的敌人，但其出现次数必须 < "
                f"{DEFAULT_SALIENCE_THRESHOLD}，不可成为本章主要对手。"
            )
            lines.append("")
            lines.append("当前发现（所有 critical 都要修复）：")
        # Cap at 25 findings to keep the prompt tight.
        for finding in self.findings[:25]:
            tag = "CRITICAL" if finding.severity == "critical" else "WARNING"
            lines.append(
                f"  [{tag}] ch{finding.chapter_number}/vol{finding.volume_number}: "
                f"{finding.message}"
            )
        if len(self.findings) > 25:
            extra = len(self.findings) - 25
            lines.append(
                f"  … {extra} more findings (see full report)"
                if is_en
                else f"  …另有 {extra} 条发现（完整报告略）"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_english(language: str | None) -> bool:
    if not language:
        return False
    return language.lower().startswith("en")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            return {}
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value if item is not None]


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _is_cjk(ch: str) -> bool:
    """Rough CJK Unified Ideographs check."""
    if not ch:
        return False
    code = ord(ch[0])
    # Main CJK block + extension A
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
    )


def _is_cjk_name(name: str) -> bool:
    """A name counts as CJK if at least half the chars are CJK."""
    if not name:
        return False
    cjk = sum(1 for ch in name if _is_cjk(ch))
    return cjk >= len(name) / 2


def _name_is_auditable(name: str) -> bool:
    """True if the name is long enough and not a generic label."""
    clean = _as_str(name)
    if not clean:
        return False
    if clean.lower() in GENERIC_LABEL_DENYLIST:
        return False
    # Also strip and check each char belongs to an identifiable script.
    if _is_cjk_name(clean):
        return len(clean) >= MIN_CJK_NAME_LEN
    # Strip punctuation / whitespace for ASCII-ish names.
    normalized = re.sub(r"[\s\-_.]+", "", clean)
    return len(normalized) >= MIN_ASCII_NAME_LEN


def _parse_stages_to_volumes(stages: Any) -> set[int]:
    """Accept flexible stages_of_relevance shapes and return the set of
    volume numbers covered.

    Supported:
      * [[1, 3], [5, 7]]
      * [{"start": 1, "end": 3}, ...]
      * [1, 2, 3]  (flat list of volumes)
    """
    out: set[int] = set()
    if not isinstance(stages, Iterable) or isinstance(stages, (str, bytes)):
        return out
    for stage in stages:
        if isinstance(stage, (list, tuple)) and len(stage) >= 2:
            try:
                start = int(stage[0])
                end = int(stage[1])
            except (TypeError, ValueError):
                continue
            if end < start:
                start, end = end, start
            out.update(range(start, end + 1))
        elif isinstance(stage, dict):
            try:
                start = int(stage.get("start") or stage.get("start_volume") or 0)
                end = int(stage.get("end") or stage.get("end_volume") or start)
            except (TypeError, ValueError):
                continue
            if start <= 0:
                continue
            if end < start:
                end = start
            out.update(range(start, end + 1))
        else:
            try:
                v = int(stage)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out.add(v)
    return out


def _count_mentions(text: str, name: str) -> int:
    """Count non-overlapping mentions of ``name`` in ``text``.

    For CJK names we do raw substring counting.
    For ASCII-heavy names we use a word-boundary regex so "Kai" doesn't
    match "Kairos".
    """
    if not text or not name:
        return 0
    if _is_cjk_name(name):
        # raw count, but avoid runaway if name is a suffix of longer names
        # (handled by MIN_CJK_NAME_LEN filter upstream).
        return text.count(name)
    pattern = r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


# ---------------------------------------------------------------------------
# Volume → allowed-antagonist index
# ---------------------------------------------------------------------------


def build_volume_antagonist_index(
    antagonist_plans: Iterable[Any],
    *,
    volume_count: int,
) -> tuple[dict[int, set[str]], set[str]]:
    """Build the (volume → names allowed, all_names) index.

    A plan lands in a volume if EITHER:
      * its ``scope_volume_number`` equals that volume; OR
      * any interval in ``stages_of_relevance`` covers that volume.

    Names that fail :func:`_name_is_auditable` (too short, generic label)
    are excluded — they'd create too many false positives downstream.
    """
    volume_count = max(int(volume_count or 0), 1)
    by_volume: dict[int, set[str]] = {v: set() for v in range(1, volume_count + 1)}
    all_names: set[str] = set()

    for plan in _mapping_list(antagonist_plans):
        name = _as_str(plan.get("name") or plan.get("antagonist_label"))
        if not _name_is_auditable(name):
            continue
        all_names.add(name)

        # primary scope
        scoped_volumes: set[int] = set()
        scope = plan.get("scope_volume_number")
        if scope is not None:
            try:
                v = int(scope)
                if 1 <= v <= volume_count:
                    scoped_volumes.add(v)
            except (TypeError, ValueError):
                pass

        # stages of relevance
        stages_volumes = _parse_stages_to_volumes(plan.get("stages_of_relevance"))
        scoped_volumes.update(v for v in stages_volumes if 1 <= v <= volume_count)

        # If no scope / stages found, treat the plan as book-wide (no
        # chapter can violate it) — add the name to every volume.
        if not scoped_volumes:
            scoped_volumes = set(by_volume.keys())

        for v in scoped_volumes:
            by_volume.setdefault(v, set()).add(name)

    return by_volume, all_names


# ---------------------------------------------------------------------------
# Per-chapter audit
# ---------------------------------------------------------------------------


def audit_chapter_against_volume(
    chapter_number: int,
    volume_number: int,
    chapter_text: str,
    *,
    allowed_in_volume: Iterable[str],
    all_antagonist_names: Iterable[str],
    salience_threshold: int = DEFAULT_SALIENCE_THRESHOLD,
    language: str = "zh-CN",
) -> ChapterAudit:
    """Audit a single chapter's text against the volume's antagonist set."""
    is_en = _is_english(language)
    allowed_set = {_as_str(n) for n in allowed_in_volume if _name_is_auditable(_as_str(n))}
    all_set = {_as_str(n) for n in all_antagonist_names if _name_is_auditable(_as_str(n))}
    foreign_names = all_set - allowed_set

    text = chapter_text or ""

    mentioned_expected = tuple(sorted(n for n in allowed_set if _count_mentions(text, n) > 0))
    out_of_scope_counter: Counter[str] = Counter()
    for name in foreign_names:
        count = _count_mentions(text, name)
        if count > 0:
            out_of_scope_counter[name] = count

    findings: list[ChapterAntagonistFinding] = []
    for name, count in out_of_scope_counter.most_common():
        if count >= salience_threshold:
            findings.append(
                ChapterAntagonistFinding(
                    code="dominant_foreign_antagonist",
                    severity="critical",
                    chapter_number=chapter_number,
                    volume_number=volume_number,
                    message=(
                        f"Chapter {chapter_number} (volume {volume_number}) "
                        f"mentions out-of-scope antagonist '{name}' {count} "
                        f"times — present-tense use of a foreign-volume "
                        "antagonist is forbidden."
                        if is_en
                        else f"第 {chapter_number} 章（第 {volume_number} 卷）"
                        f"提到了不属于本卷的敌人『{name}』{count} 次——"
                        "禁止以当下视角大段使用他卷敌人。"
                    ),
                    payload={
                        "name": name,
                        "count": count,
                        "threshold": salience_threshold,
                    },
                )
            )
        else:
            findings.append(
                ChapterAntagonistFinding(
                    code="passing_foreign_antagonist",
                    severity="warning",
                    chapter_number=chapter_number,
                    volume_number=volume_number,
                    message=(
                        f"Chapter {chapter_number} (volume {volume_number}) "
                        f"mentions out-of-scope antagonist '{name}' {count} "
                        "time(s) — verify this is a legitimate flashback "
                        "reference, not a continuity error."
                        if is_en
                        else f"第 {chapter_number} 章（第 {volume_number} 卷）"
                        f"提到了非本卷敌人『{name}』{count} 次——"
                        "请确认是合法的回忆引用而非连续性错误。"
                    ),
                    payload={
                        "name": name,
                        "count": count,
                        "threshold": salience_threshold,
                    },
                )
            )

    return ChapterAudit(
        chapter_number=chapter_number,
        volume_number=volume_number,
        expected_antagonists=tuple(sorted(allowed_set)),
        mentioned_expected=mentioned_expected,
        mentioned_out_of_scope=tuple(out_of_scope_counter.most_common()),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Novel-wide scan
# ---------------------------------------------------------------------------


def audit_novel_chapters(
    chapters: Iterable[Any],
    antagonist_plans: Iterable[Any],
    *,
    volume_count: int,
    salience_threshold: int = DEFAULT_SALIENCE_THRESHOLD,
    language: str = "zh-CN",
) -> ChapterAntagonistReport:
    """Audit every chapter against the volume antagonist plan.

    ``chapters`` items must expose ``chapter_number``, ``volume_number``,
    and ``text`` — either as attributes, as dict keys, or via pydantic
    ``.model_dump()``.
    """
    by_volume, all_names = build_volume_antagonist_index(
        antagonist_plans, volume_count=volume_count
    )

    chapter_list = _mapping_list(chapters)
    chapter_audits: list[ChapterAudit] = []
    aggregate_findings: list[ChapterAntagonistFinding] = []

    for ch in chapter_list:
        try:
            chapter_number = int(ch.get("chapter_number") or 0)
        except (TypeError, ValueError):
            chapter_number = 0
        try:
            volume_number = int(ch.get("volume_number") or 0)
        except (TypeError, ValueError):
            volume_number = 0
        text = _as_str(ch.get("text") or ch.get("content_md") or "")
        if chapter_number <= 0 or volume_number <= 0 or not text:
            continue

        allowed = by_volume.get(volume_number, set())
        audit = audit_chapter_against_volume(
            chapter_number=chapter_number,
            volume_number=volume_number,
            chapter_text=text,
            allowed_in_volume=allowed,
            all_antagonist_names=all_names,
            salience_threshold=salience_threshold,
            language=language,
        )
        chapter_audits.append(audit)
        aggregate_findings.extend(audit.findings)

    # Sort findings by severity (critical first), then chapter number.
    severity_rank = {"critical": 0, "warning": 1}
    aggregate_findings.sort(
        key=lambda f: (severity_rank.get(f.severity, 9), f.chapter_number)
    )

    return ChapterAntagonistReport(
        total_chapters=len(chapter_audits),
        total_volumes=max(int(volume_count or 0), 1),
        total_antagonists=len(all_names),
        chapter_audits=tuple(chapter_audits),
        findings=tuple(aggregate_findings),
    )


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------

__all__ = (
    "ChapterAntagonistFinding",
    "ChapterAudit",
    "ChapterAntagonistReport",
    "DEFAULT_SALIENCE_THRESHOLD",
    "MIN_CJK_NAME_LEN",
    "MIN_ASCII_NAME_LEN",
    "GENERIC_LABEL_DENYLIST",
    "audit_chapter_against_volume",
    "audit_novel_chapters",
    "build_volume_antagonist_index",
)

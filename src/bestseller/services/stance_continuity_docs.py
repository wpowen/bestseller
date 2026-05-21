"""File-based stance continuity check for Mode B (agent loop).

The DB-backed ``_check_stance_flip`` in :mod:`contradiction` only fires when
the pipeline persists character state to PostgreSQL. The agent loop
(``output/ai-generated/{slug}/`` Mode B) keeps state in markdown / YAML
files, so it needs a parallel implementation that reads from disk.

What this catches:

* A character whose stance toward another character flips from clearly
  hostile (enemy / antagonist / hunter) to clearly friendly (ally / mentor /
  protector) -- or vice versa -- across two consecutive snapshot files,
  without an explanatory "trigger event" recorded in ``timeline.md`` or in
  the bridging chapter's frontmatter.

Stance is read from ``trust_map`` (per [knowledge.md § 3]) or the more
explicit ``stance`` field if present. Trigger events are surfaced from
``timeline.md`` entries whose ``event_type`` is in the configured set
(``reconciliation`` / ``betrayal`` / ``coercion`` / ``debt_event`` /
``rescue`` / ``confession``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_HOSTILE_STANCES = frozenset({"enemy", "antagonist", "hunter", "rival", "敌", "敌人", "敌对"})
_FRIENDLY_STANCES = frozenset({"ally", "friend", "mentor", "protector", "盟友", "盟", "守护者", "亲信"})
_NEUTRAL_STANCES = frozenset({"neutral", "unknown", "中立", "未知"})

_TRIGGER_EVENT_TYPES = frozenset({
    "reconciliation", "betrayal", "coercion", "debt_event",
    "rescue", "confession", "blood_oath", "alliance", "treachery",
    "和解", "背叛", "胁迫", "救援", "结盟", "认主",
})

_TRUST_HOSTILE_THRESHOLD = -0.3
_TRUST_FRIENDLY_THRESHOLD = 0.3


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StanceTransition:
    character_a: str           # subject of the stance change
    character_b: str           # target of the change
    prev_stance: str
    curr_stance: str
    prev_snapshot_chapter: int
    curr_snapshot_chapter: int


@dataclass(frozen=True)
class StanceReversalFinding:
    transition: StanceTransition
    found_triggers: tuple[str, ...]   # timeline event labels if any
    explanation: str                  # short human-readable description
    severity: str                     # "warning" | "violation"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_snapshot_file(path: Path) -> dict[str, dict[str, object]]:
    """Read a character snapshot markdown/yaml and return ``name → state``.

    Supports two on-disk layouts:

    1. A YAML frontmatter block delimited by ``---`` containing a top-level
       ``characters:`` list.
    2. A pure-markdown body where each H2 (``## name``) starts a block with
       ``- stance: ally`` / ``- trust_toward_X: 0.4`` bullets.

    We try YAML first, fall back to the markdown parser, and return an empty
    dict if both fail.
    """
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}

    # Try YAML frontmatter first
    if text.lstrip().startswith("---"):
        try:
            stripped = text.lstrip()[3:]
            end = stripped.find("---")
            if end >= 0:
                yaml_block = stripped[:end]
                data = yaml.safe_load(yaml_block) or {}
                chars = data.get("characters") or []
                out: dict[str, dict[str, object]] = {}
                for record in chars:
                    if isinstance(record, dict) and record.get("name"):
                        out[str(record["name"])] = dict(record)
                if out:
                    return out
        except yaml.YAMLError as exc:
            logger.debug("snapshot YAML parse failed for %s: %s", path, exc)

    # Fallback: markdown sections
    return _parse_markdown_snapshot(text)


_MD_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^[-*]\s+([^:：]+)[:：]\s*(.+?)\s*$", re.MULTILINE)


def _parse_markdown_snapshot(text: str) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    sections = _MD_HEADER_RE.split(text)
    # split returns [pre, name1, body1, name2, body2, ...]
    for i in range(1, len(sections), 2):
        name = sections[i].strip()
        body = sections[i + 1] if i + 1 < len(sections) else ""
        record: dict[str, object] = {}
        trust_map: dict[str, float] = {}
        for m in _MD_BULLET_RE.finditer(body):
            key = m.group(1).strip()
            value = m.group(2).strip()
            if key.startswith("trust_") or key.startswith("信任_"):
                try:
                    trust_map[key.split("_", 1)[1]] = float(value)
                except (ValueError, IndexError):
                    pass
            else:
                # Strip quotes
                if value.startswith(('"', "'")) and value.endswith(('"', "'")):
                    value = value[1:-1]
                record[key] = value
        if trust_map:
            record["trust_map"] = trust_map
        if record:
            out[name] = record
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_stance(value: object) -> str | None:
    """Return one of 'hostile' / 'friendly' / 'neutral' / None."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _HOSTILE_STANCES or v in {"敌", "敌人", "敌对"}:
            return "hostile"
        if v in _FRIENDLY_STANCES or v in {"盟友", "盟", "守护者", "亲信"}:
            return "friendly"
        if v in _NEUTRAL_STANCES or v in {"中立", "未知"}:
            return "neutral"
    if isinstance(value, (int, float)):
        if value <= _TRUST_HOSTILE_THRESHOLD:
            return "hostile"
        if value >= _TRUST_FRIENDLY_THRESHOLD:
            return "friendly"
        return "neutral"
    return None


def _is_reversal(prev: str | None, curr: str | None) -> bool:
    return (
        (prev == "hostile" and curr == "friendly")
        or (prev == "friendly" and curr == "hostile")
    )


# ---------------------------------------------------------------------------
# Timeline trigger search
# ---------------------------------------------------------------------------


_TIMELINE_ENTRY_RE = re.compile(
    r"##\s+Chapter\s+(\d+).*?(?=\n##\s+Chapter|\Z)", re.DOTALL | re.IGNORECASE,
)
_EVENT_TYPE_RE = re.compile(r"`?event_type`?[:：]\s*[\"']?([\w_\-]+)", re.IGNORECASE)


def _scan_timeline_triggers(
    timeline_path: Path,
    chapter_low: int,
    chapter_high: int,
) -> list[str]:
    if not timeline_path.exists():
        return []
    text = timeline_path.read_text(encoding="utf-8")
    triggers: list[str] = []
    for block_match in _TIMELINE_ENTRY_RE.finditer(text):
        chapter_no = int(block_match.group(1))
        if not (chapter_low <= chapter_no <= chapter_high):
            continue
        for m in _EVENT_TYPE_RE.finditer(block_match.group(0)):
            event_type = m.group(1).strip().lower()
            if event_type in _TRIGGER_EVENT_TYPES:
                triggers.append(f"ch{chapter_no}:{event_type}")
    return triggers


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


def _stance_pairs_from_record(name: str, record: dict[str, object]) -> dict[str, str]:
    """Yield ``{other_character: stance_label}`` from one character record.

    Honors both ``trust_map`` (numeric) and per-relationship ``stance_toward_X``
    fields, plus the catch-all ``stance`` if present.
    """
    out: dict[str, str] = {}
    trust_map = record.get("trust_map")
    if isinstance(trust_map, dict):
        for other, score in trust_map.items():
            cls = _classify_stance(score)
            if cls:
                out[str(other)] = cls
    # Per-target stance fields
    for key, val in record.items():
        if not isinstance(key, str):
            continue
        marker = "stance_toward_"
        if key.startswith(marker):
            other = key[len(marker):]
            cls = _classify_stance(val)
            if cls:
                out[other] = cls
    # General stance field (toward protagonist by convention)
    if "stance" in record:
        cls = _classify_stance(record["stance"])
        if cls and "protagonist" not in out:
            out["protagonist"] = cls
    return out


def _ordered_snapshots(snapshot_dir: Path) -> list[tuple[int, Path]]:
    """Return ``[(chapter_no, path), ...]`` sorted by chapter for ``after-ch-NNN`` files."""
    if not snapshot_dir.exists():
        return []
    candidates: list[tuple[int, Path]] = []
    for path in snapshot_dir.iterdir():
        m = re.search(r"after-ch-(\d+)", path.name)
        if m:
            candidates.append((int(m.group(1)), path))
    candidates.sort()
    return candidates


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_stance_reversals(
    project_root: Path | str,
) -> list[StanceReversalFinding]:
    """Scan all character snapshots in a Mode B project and surface stance reversals.

    ``project_root`` is the ``output/ai-generated/{slug}/`` directory. Snapshots
    are expected under ``knowledge/character-snapshots/after-ch-NNN.md``. The
    timeline is read from ``knowledge/timeline.md``.
    """
    root = Path(project_root)
    snapshot_dir = root / "knowledge" / "character-snapshots"
    timeline_path = root / "knowledge" / "timeline.md"

    ordered = _ordered_snapshots(snapshot_dir)
    if len(ordered) < 2:
        return []

    findings: list[StanceReversalFinding] = []
    for (prev_ch, prev_path), (curr_ch, curr_path) in zip(ordered, ordered[1:]):
        prev_records = _parse_snapshot_file(prev_path)
        curr_records = _parse_snapshot_file(curr_path)

        # Compare every character's stance map between consecutive snapshots
        for name in set(prev_records) & set(curr_records):
            prev_map = _stance_pairs_from_record(name, prev_records[name])
            curr_map = _stance_pairs_from_record(name, curr_records[name])
            for other in set(prev_map) & set(curr_map):
                if not _is_reversal(prev_map[other], curr_map[other]):
                    continue
                transition = StanceTransition(
                    character_a=name,
                    character_b=other,
                    prev_stance=prev_map[other],
                    curr_stance=curr_map[other],
                    prev_snapshot_chapter=prev_ch,
                    curr_snapshot_chapter=curr_ch,
                )
                triggers = _scan_timeline_triggers(
                    timeline_path, prev_ch + 1, curr_ch,
                )
                severity = "warning" if triggers else "violation"
                if triggers:
                    explanation = (
                        f"「{name}」对「{other}」的立场从 {prev_map[other]} → {curr_map[other]}；"
                        f"已找到过渡事件 {triggers}。"
                    )
                else:
                    explanation = (
                        f"「{name}」对「{other}」的立场从 {prev_map[other]} → {curr_map[other]}，"
                        f"但第 {prev_ch+1}-{curr_ch} 章 timeline.md 中未登记 "
                        f"reconciliation / betrayal / coercion 等过渡事件。"
                    )
                findings.append(
                    StanceReversalFinding(
                        transition=transition,
                        found_triggers=tuple(triggers),
                        explanation=explanation,
                        severity=severity,
                    )
                )
    return findings


def build_stance_reversal_repair_prompt(findings: list[StanceReversalFinding]) -> str:
    """Render an editor-facing instruction listing unjustified reversals."""
    violations = [f for f in findings if f.severity == "violation"]
    if not violations:
        return ""
    bullets = [f"- {f.explanation}" for f in violations]
    return (
        "【角色立场逆转修复】\n"
        "以下角色关系出现敌↔友翻转，但缺少 timeline 上的支撑事件。\n"
        "请二选一：\n"
        "  (a) 在过渡区间的章节里补写一段触发事件（被胁迫、被救、被揭露身份…），并在 timeline.md 登记 event_type；\n"
        "  (b) 修改 character-snapshots/after-ch-NNN.md 让立场回归之前状态。\n\n"
        + "\n".join(bullets)
    )


__all__ = [
    "StanceTransition",
    "StanceReversalFinding",
    "detect_stance_reversals",
    "build_stance_reversal_repair_prompt",
]

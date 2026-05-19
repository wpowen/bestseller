from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterModel,
    ChapterQualityReportModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services.quality_failure_events import (
    failure_events_from_retrofit_row,
    quality_failure_events_to_dicts,
)
from bestseller.services.word_targets import chapter_rewrite_length_band
from bestseller.settings import AppSettings, load_settings


AUTONOMOUS_REPAIR_TRIGGER = "autonomous_quality_retrofit"
AUTONOMOUS_REPAIR_STRATEGY = "quality_retrofit_chapter_rewrite"

_PRIORITY_WEIGHT: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 4,
    "ok": 5,
}


@dataclass(frozen=True, slots=True)
class QualityRepairTaskSpec:
    slug: str
    chapter_number: int
    priority: str
    task_priority: int
    cause_ids: tuple[str, ...]
    language: str | None = None
    patch_points: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    audit_row: Mapping[str, object] = field(default_factory=dict)

    @property
    def repair_id(self) -> str:
        payload: dict[str, object] = {
            "slug": self.slug,
            "chapter_number": self.chapter_number,
            "priority": self.priority,
            "cause_ids": list(self.cause_ids),
            "patch_points": list(self.patch_points),
        }
        if self.language:
            payload["language"] = self.language
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"quality-retrofit:{self.slug}:ch{self.chapter_number:03d}:{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_id": self.repair_id,
            "slug": self.slug,
            "chapter_number": self.chapter_number,
            "priority": self.priority,
            "task_priority": self.task_priority,
            "cause_ids": list(self.cause_ids),
            "language": self.language,
            "patch_points": [dict(point) for point in self.patch_points],
            "audit_row": dict(self.audit_row),
            "quality_failure_events": _quality_failure_events_for_spec(self),
            "instructions": build_quality_repair_instructions(self),
        }


@dataclass(frozen=True, slots=True)
class QualityRepairPlan:
    slug: str
    specs: tuple[QualityRepairTaskSpec, ...]
    priority_counts: Mapping[str, int]
    cause_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "task_count": len(self.specs),
            "priority_counts": dict(self.priority_counts),
            "cause_counts": dict(self.cause_counts),
            "tasks": [spec.to_dict() for spec in self.specs],
        }


@dataclass(frozen=True, slots=True)
class TaskSyncResult:
    created: int
    skipped_existing: int
    superseded: int
    missing_chapters: tuple[int, ...]
    task_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "created": self.created,
            "skipped_existing": self.skipped_existing,
            "superseded": self.superseded,
            "missing_chapters": list(self.missing_chapters),
            "task_ids": list(self.task_ids),
        }


def discover_output_book_slugs(output_dir: Path) -> list[str]:
    if not output_dir.exists():
        return []
    slugs: list[str] = []
    for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
        if path.is_dir() and any(path.glob("chapter-*.md")):
            slugs.append(path.name)
    return slugs


def load_quality_retrofit_rows(csv_path: Path) -> list[dict[str, str]]:
    import csv

    if not csv_path.is_file():
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_patch_plan(plan_path: Path) -> list[dict[str, Any]]:
    if not plan_path.is_file():
        return []
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    return []


def latest_quality_retrofit_csv(slug: str, *, output_dir: Path) -> Path | None:
    audit_dir = output_dir / slug / "audits" / "quality-retrofit"
    files = sorted(audit_dir.glob("window-*.csv"))
    return files[-1] if files else None


def patch_plan_by_chapter(patch_plan: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    rows: dict[int, Mapping[str, Any]] = {}
    for item in patch_plan:
        try:
            chapter_number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number > 0:
            rows[chapter_number] = item
    return rows


def build_quality_repair_plan(
    *,
    slug: str,
    audit_rows: Sequence[Mapping[str, Any]],
    patch_plan: Sequence[Mapping[str, Any]] = (),
    priorities: set[str] | None = None,
    limit: int | None = None,
) -> QualityRepairPlan:
    wanted = priorities or {"critical", "high"}
    patch_rows = patch_plan_by_chapter(patch_plan)
    specs: list[QualityRepairTaskSpec] = []
    for row in audit_rows:
        audit_validity = str(row.get("audit_validity") or "").strip().lower()
        if audit_validity.startswith("invalid"):
            continue
        priority = str(row.get("priority") or "ok")
        if priority not in wanted:
            continue
        try:
            chapter_number = int(row.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number <= 0:
            continue
        patch_row = patch_rows.get(chapter_number, {})
        patch_points = tuple(
            dict(point)
            for point in _sequence(patch_row.get("patch_points"))
            if isinstance(point, Mapping)
        )
        cause_ids = _split_causes(row.get("cause_ids")) or tuple(
            str(item) for item in _sequence(patch_row.get("cause_ids")) if str(item)
        )
        specs.append(
            QualityRepairTaskSpec(
                slug=slug,
                chapter_number=chapter_number,
                priority=priority,
                task_priority=_PRIORITY_WEIGHT.get(priority, 5),
                cause_ids=cause_ids,
                language=_repair_language_from_row(row),
                patch_points=patch_points,
                audit_row=dict(row),
            )
        )
        if limit is not None and limit > 0 and len(specs) >= limit:
            break
    return QualityRepairPlan(
        slug=slug,
        specs=tuple(specs),
        priority_counts=Counter(spec.priority for spec in specs),
        cause_counts=Counter(cause for spec in specs for cause in spec.cause_ids),
    )


_PULSE_REPAIR_EXAMPLES = (
    "立刻",
    "必须",
    "猛地",
    "拦住",
    "逼近",
    "反锁",
    "停住",
    "心一沉",
)
_RHYTHM_TYPE_LABELS = {
    "rhythm_hard_stops": "hard_stop/硬停顿",
    "rhythm_acceleration": "acceleration/连续短段加速",
    "rhythm_delay": "delay/延宕停拍",
    "rhythm_external_interrupts": "external_interrupt/外部打断",
}
_BANNED_PATTERN_HINTS = {
    "cliched_metaphor": "删除“像……一样……”式套话比喻，改成物件变化、动作后果或具体触感。",
    "smooth_transition": "删除“更要命的是/最要命的是/那不是最要命的”等模板转场，直接让证物或行动转折发生。",
    "emotion_label": "删除“他感到/她感到/他意识到/忽然明白”等情绪标签，改成身体反应、选择或证物变化。",
    "explanatory_dialogue": "删除解释型对白，把因果交给动作、证物、反问和下一步选择。",
    "parallel_action": "拆掉“一边……一边……”的并行动作套式，改成先后动作和结果。",
    "not_only_but_also": "删除“不仅……还……”句式，改成具体递进后果。",
    "looks_like_actually": "删除“看似……实则……”句式，改成读者可见的反差证据。",
    "weak_verbs": "替换“进行了/实施了/做出了”等弱动词，改成可拍动作。",
}


def _row_float(row: Mapping[str, object], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def _row_int(row: Mapping[str, object], key: str, default: int = 0) -> int:
    try:
        return int(float(row.get(key) or default))
    except (TypeError, ValueError):
        return default


def _is_english_language(language: str | None) -> bool:
    return (language or "").strip().lower().startswith("en")


def _repair_language_from_row(row: Mapping[str, object] | None) -> str | None:
    if not isinstance(row, Mapping):
        return None
    for key in ("language", "target_language", "project_language"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    unit = str(row.get("count_unit") or row.get("word_count_unit") or "").lower()
    if unit == "english_words":
        return "en-US"
    return None


def _repair_language(spec: QualityRepairTaskSpec) -> str | None:
    return spec.language or _repair_language_from_row(spec.audit_row)


def _quality_failure_events_for_spec(spec: QualityRepairTaskSpec) -> list[dict[str, Any]]:
    return quality_failure_events_to_dicts(
        failure_events_from_retrofit_row(
            spec.audit_row,
            slug=spec.slug,
            platform=str(spec.audit_row.get("platform") or "") or None,
            evidence_ref=str(spec.audit_row.get("evidence_ref") or "") or None,
            repair_task_id=spec.repair_id,
        )
    )


def _length_unit_label(language: str | None) -> str:
    return "English words" if _is_english_language(language) else "中文汉字"


def _language_from_instructions(
    instructions: str,
    language: str | None = None,
) -> str | None:
    if language:
        return language
    lowered = (instructions or "").lower()
    if "language: english" in lowered or "english serial-fiction" in lowered:
        return "en-US"
    return None


def _repair_length_band(
    spec: QualityRepairTaskSpec,
    *,
    direction: str = "normal",
    language: str | None = None,
    settings: AppSettings | None = None,
) -> str:
    settings = settings or load_settings(env={})
    row = spec.audit_row or {}
    target = (
        _row_int(row, "target_word_count")
        or _row_int(row, "target")
        or 0
    )
    if target <= 0:
        target = None
    band = chapter_rewrite_length_band(
        settings,
        target,
        language=language,
        direction=direction,
        role="editor",
    )
    return f"{band.safe_min}-{band.safe_max}"


def _global_quality_repair_band(
    *,
    direction: str = "normal",
    language: str | None = None,
    settings: AppSettings | None = None,
) -> str:
    settings = settings or load_settings(env={})
    band = chapter_rewrite_length_band(
        settings,
        None,
        language=language,
        direction=direction,
        role="editor",
    )
    return f"{band.safe_min}-{band.safe_max}"


def _repair_length_direction(spec: QualityRepairTaskSpec) -> str:
    row = spec.audit_row or {}
    reason = str(row.get("word_count_reason") or "").lower()
    causes = {str(cause).upper() for cause in spec.cause_ids}
    if "overflow" in reason or "over" in reason:
        return "over"
    if "under" in reason or "insufficient" in reason:
        return "under"
    if "overflow" in str(row.get("length_status") or "").lower():
        return "over"
    if "LENGTH_STABILITY_BELOW_BAR" in causes:
        ratio = _row_float(row, "length_ratio", 1.0)
        if ratio > 1.10:
            return "over"
        if 0 < ratio < 0.90:
            return "under"
    return "normal"


def _banned_pattern_feedback_lines(row: Mapping[str, object]) -> list[str]:
    breakdown = str(row.get("banned_pattern_breakdown") or "").strip()
    if not breakdown:
        return []
    pattern_ids = [
        part.split(":", 1)[0].strip()
        for part in breakdown.split(";")
        if part.split(":", 1)[0].strip()
    ]
    lines: list[str] = [f"- 当前 AI 句式命中: {breakdown}。"]
    for pattern_id in dict.fromkeys(pattern_ids):
        hint = _BANNED_PATTERN_HINTS.get(pattern_id)
        if hint:
            lines.append(f"- {hint}")
    return lines


def _rhythm_feedback_lines(row: Mapping[str, object]) -> list[str]:
    if str(row.get("rhythm_passed") or "").lower() == "true":
        return []
    counts = {
        key: _row_int(row, key)
        for key in _RHYTHM_TYPE_LABELS
        if row.get(key) not in (None, "")
    }
    if not counts:
        return [
            "- 节奏失败: 必须覆盖至少 3 类节奏锚点，不要只增加长段解释。"
        ]
    missing = [
        label
        for key, label in _RHYTHM_TYPE_LABELS.items()
        if counts.get(key, 0) <= 0
    ]
    expected_count = _row_int(row, "rhythm_expected_min_count")
    expected_types = _row_int(row, "rhythm_expected_min_types")
    types_covered = _row_int(row, "rhythm_types_covered")
    total = _row_int(row, "rhythm_total_anchors")
    lines = [
        (
            "- 当前节奏锚点: "
            f"hard_stop={counts.get('rhythm_hard_stops', 0)}, "
            f"acceleration={counts.get('rhythm_acceleration', 0)}, "
            f"delay={counts.get('rhythm_delay', 0)}, "
            f"external_interrupt={counts.get('rhythm_external_interrupts', 0)}, "
            f"total={total}, types={types_covered}/{expected_types or 3}."
        ),
        "- 修复后至少覆盖 3 类节奏锚点: 短硬停顿、连续短段加速、延宕停拍、外部打断。",
    ]
    if expected_count:
        lines.append(f"- 总锚点数量不得低于 {expected_count}，但不能用机械短句刷数。")
    if missing:
        lines.append("- 优先补缺失锚点: " + "、".join(missing[:3]) + "。")
    return lines


def _build_english_quality_repair_instructions(
    spec: QualityRepairTaskSpec,
    *,
    length_range: str,
    direction: str,
    word_count_reason: str,
    upper_causes: set[str],
) -> str:
    row = spec.audit_row
    lines = [
        (
            "Repair this chapter for English serial-fiction quality gates. "
            "Do not merely polish; rebuild the chapter so it passes pacing, "
            "attraction, continuity, and length checks."
        ),
        f"Chapter: ch{spec.chapter_number:03d}",
        f"Priority: {spec.priority}",
        "Language: English",
        "Issue types: "
        + (", ".join(spec.cause_ids) if spec.cause_ids else "unknown"),
        "",
        "Hard repair targets:",
    ]
    if "GOLDEN_THREE_WEAK" in upper_causes:
        lines.extend(
            [
                (
                    "- Rebuild the opening as a market-grade hook: immediate "
                    "disturbance, visible pressure, a costly choice, and a "
                    "concrete read-on promise."
                ),
                (
                    "- The chapter must form a loop: visible conflict -> "
                    "protagonist action -> information or rule change -> sharper "
                    "ending hook."
                ),
            ]
        )
    if "HYPE_ASSIGNMENT_MISSING" in upper_causes:
        lines.extend(
            [
                (
                    "- Add a recognizable reader reward: reversal, "
                    "pressure-to-countermove, information gap reveal, rule "
                    "payoff, or danger escalation."
                ),
                (
                    "- The reward must have setup, action, state change, and "
                    "immediate consequence."
                ),
            ]
        )
    if "LENGTH_STABILITY_BELOW_BAR" in upper_causes:
        if direction == "over":
            lines.extend(
                [
                    (
                        "- This is a length-compression task: preserve the "
                        "chapter function while cutting repeated introspection, "
                        "repeated setting description, exposition, and "
                        "non-state-changing transitions."
                    ),
                    (
                        f"- The rewritten body must land in the {length_range} "
                        "English-word safe band."
                    ),
                    (
                        "- Do not add new characters, locations, powers, factions, "
                        "titles, or side plots."
                    ),
                ]
            )
        elif direction == "under":
            lines.extend(
                [
                    (
                        "- This is a length-expansion task: add effective dramatic "
                        "content, not exposition or repeated internal monologue."
                    ),
                    (
                        f"- The rewritten body must land in the {length_range} "
                        "English-word safe band."
                    ),
                    (
                        "- Only add action resistance, evidence changes, "
                        "relationship pressure, cost, causal bridge, or ending "
                        "hook pressure."
                    ),
                ]
            )
        else:
            lines.append(
                f"- Keep the chapter inside the {length_range} English-word safe band."
            )
    if "SCORECARD_BELOW_ACCEPTANCE_BAR" in upper_causes:
        lines.append(
            (
                "- Improve the whole-book scorecard directly: reduce repeated "
                "phrasing, add action progression, information shifts, concrete "
                "sensory detail, and a stronger chapter hook."
            )
        )

    if "flat_narration" in spec.cause_ids:
        lines.append(
            (
                "- Make the chapter function visible: active pursuit, resistance, "
                "reveal, payoff, or pressure-building must clearly advance."
            )
        )
        if "overflow" in word_count_reason:
            lines.append(
                (
                    "- The chapter is long; tighten by replacing and merging "
                    f"redundant beats while staying in the {length_range} "
                    "English-word band."
                )
            )
        else:
            lines.append(
                "- If short, expand with effective scene beats, not background explanation."
            )
    if "weak_attraction" in spec.cause_ids:
        lines.append(
            (
                "- Increase attraction density: every 300-500 words needs "
                "pressure, danger, evidence change, blocked action, deadline, "
                "threat, or a new cost."
            )
        )
        if row:
            density = _row_float(row, "pulse_density")
            threshold = _row_float(row, "pulse_threshold", 1.0) or 1.0
            pulse_count = _row_int(row, "pulse_count")
            lines.append(
                (
                    f"- Current pulse_density={density:.2f}, "
                    f"pulse_count={pulse_count}, target >= {threshold:.2f}; "
                    "pressure words must come from real action, not empty labels."
                )
            )
    if "weak_prose" in spec.cause_ids:
        lines.append(
            (
                "- Replace abstract labels with concrete objects, actions, sound, "
                "temperature, texture, and camera-visible detail."
            )
        )
    if "ai_voice" in spec.cause_ids:
        lines.append(
            (
                "- Remove AI-like transitions, generic metaphors, explanatory "
                "dialogue, and conclusion sentences such as 'he realized' when "
                "an action or evidence change can carry the beat."
            )
        )
    if "weak_immersion" in spec.cause_ids:
        lines.append(
            (
                "- Break up introspective dumping; convert background into "
                "action triggers, evidence changes, or character conflict."
            )
        )

    if row:
        lines.extend(
            [
                "",
                "Audit data:",
                (
                    f"- count={row.get('char_count')} "
                    f"{row.get('count_unit') or 'english_words'}"
                ),
                f"- word_count={row.get('word_count_reason')}",
                f"- pulse_density={row.get('pulse_density')}",
                f"- banned_patterns={row.get('banned_pattern_breakdown')}",
                f"- abstract_sensory={row.get('abstract_sensory_words')}",
            ]
        )
        if "underflow" in word_count_reason:
            lines.extend(
                [
                    "",
                    "Length hard constraint:",
                    (
                        "- The chapter is short. This is not a local replacement "
                        "task; add effective dramatic content."
                    ),
                    f"- The safe target is {length_range} English words.",
                    (
                        "- Do not pad with exposition, repeated thoughts, summary, "
                        "or lore explanation."
                    ),
                ]
            )
        elif "overflow" in word_count_reason:
            lines.extend(
                [
                    "",
                    "Length hard constraint:",
                    "- The chapter is long. Compress it into the publishing range.",
                    f"- The safe target is {length_range} English words.",
                    (
                        "- Compress by merging, replacing, and reordering existing "
                        "information; do not add new plot branches."
                    ),
                ]
            )

    if spec.patch_points:
        lines.extend(["", "Required patch points:"])
        for index, point in enumerate(spec.patch_points, start=1):
            lines.append(
                (
                    "{idx}. {cause} @ {location}: {issue}; repair: {repair}; "
                    "snippet: {snippet}"
                ).format(
                    idx=index,
                    cause=point.get("cause_id", ""),
                    location=point.get("location", ""),
                    issue=point.get("issue_summary", ""),
                    repair=point.get("repair_action_summary", ""),
                    snippet=str(point.get("snippet", ""))[:180],
                )
            )

    lines.extend(
        [
            "",
            "Continuity and dedupe constraints:",
            "- Preserve canon facts, character goals, clue ledger, and chapter title.",
            "- Do not introduce a new system, new canon conflict, or cost-free solution.",
            (
                "- Maintain stable names, aliases, relationships, and scene state "
                "from the surrounding context."
            ),
            (
                "- The result must be more concrete, more action-pressured, and "
                "less explanatory than the original."
            ),
            (
                "- Output English chapter prose only; do not output Chinese prose, "
                "summaries, outlines, or partial replacements."
            ),
        ]
    )
    return "\n".join(lines)


def build_quality_repair_instructions(spec: QualityRepairTaskSpec) -> str:
    row = spec.audit_row
    word_count_reason = str(row.get("word_count_reason") or "") if row else ""
    cause_ids = {str(cause) for cause in spec.cause_ids}
    upper_causes = {cause.upper() for cause in cause_ids}
    direction = _repair_length_direction(spec)
    language = _repair_language(spec)
    length_range = _repair_length_band(
        spec,
        direction=direction,
        language=language,
    )
    if _is_english_language(language):
        return _build_english_quality_repair_instructions(
            spec,
            length_range=length_range,
            direction=direction,
            word_count_reason=word_count_reason,
            upper_causes=upper_causes,
        )
    lines = [
        "按整书同标质量门修补本章。不要只做润色；必须让本章重新通过章节质量、吸引力、节奏和连续性检查。",
        f"章节: ch{spec.chapter_number:03d}",
        f"优先级: {spec.priority}",
        "问题类型: " + (", ".join(spec.cause_ids) if spec.cause_ids else "unknown"),
        "",
        "硬性修补目标:",
    ]
    if "GOLDEN_THREE_WEAK" in upper_causes:
        lines.extend(
            [
                "- 黄金三章必须重建为榜单级开篇：第一屏出现异常/压力/选择代价，不用解释设定开场。",
                "- 本章必须形成“可见冲突 -> 主角判断/行动 -> 线索或规则变化 -> 更强尾钩”的闭环。",
                "- 删除铺垫式寒暄和抽象介绍，把读者承诺落到可见事件、证物、倒计时、对抗或损失上。",
            ]
        )
    if "HYPE_ASSIGNMENT_MISSING" in upper_causes:
        lines.extend(
            [
                "- 本章必须写出可被识别的 reader-hype 奖励：反转、压迫反击、信息差揭示、规则兑现或危机升级至少一项。",
                "- 爽点不能只写成结论，必须有前置压迫、行动选择、状态变化和读者可见的即时回报。",
            ]
        )
    if "LENGTH_STABILITY_BELOW_BAR" in upper_causes:
        if direction == "over":
            lines.extend(
                [
                    "- 本章是长度稳定性压缩任务：必须保留主线功能，同时删并重复心理、重复环境、解释性铺陈和不改变局势的过渡段。",
                    f"- 重写后必须落入 {length_range} 个中文汉字安全带；超出会被质量门拒绝。",
                    "- 禁止新增人物、地点、称号、势力或支线桥段；只能压缩、合并、替换已有信息。",
                ]
            )
        elif direction == "under":
            lines.extend(
                [
                    "- 本章是长度稳定性扩写任务：必须补有效戏剧内容，而不是补设定说明或重复心理。",
                    f"- 重写后必须落入 {length_range} 个中文汉字安全带；低于安全带会被质量门拒绝。",
                    "- 只允许补行动阻断、证物变化、关系压力、选择代价、过场因果桥和尾钩蓄压。",
                ]
            )
        else:
            lines.extend(
                [
                    "- 本章参与整书长度稳定性修复：总字数不能大幅漂移，必须控制在章节安全带内。",
                    f"- 重写后目标范围是 {length_range} 个中文汉字。",
                ]
            )
    if "SCORECARD_BELOW_ACCEPTANCE_BAR" in upper_causes:
        lines.extend(
            [
                "- 本章必须直接提升整书 scorecard：减少重复表达，增加行动推进、信息差、具体感官和章节钩子。",
                "- 不要只换词润色；每 300-500 字都要有压力、证物变化、规则变化、嫌疑排序变化或选择代价。",
            ]
        )

    if "flat_narration" in spec.cause_ids:
        lines.append("- 补出可见章节功能：主动推进、反应转折、揭露、兑现或蓄压至少一项清晰成立。")
        if "overflow" in word_count_reason:
            lines.append(
                "- 本章已偏长；必须通过替换和删并冗余段落补出章节功能，"
                f"重写目标字数控制在 {length_range} 个汉字。不得新增场景、人物或设定名。"
            )
        else:
            lines.append("- 字数不足时扩写有效场景，不要用设定解释凑字。")
    if "weak_attraction" in spec.cause_ids:
        lines.append("- 提升心率密度：每 300-500 字至少有压力、危险、证物变化、行动阻断或新代价。")
        if row:
            density = _row_float(row, "pulse_density")
            threshold = _row_float(row, "pulse_threshold", 1.0) or 1.0
            pulse_count = _row_int(row, "pulse_count")
            lines.append(
                f"- 当前 pulse_density={density:.2f}, pulse_count={pulse_count}, "
                f"目标 >= {threshold:.2f}; 修复必须让压力词来自真实行动，不要堆空词。"
            )
        lines.append(
            "- 可用压力触发示例: "
            + "、".join(_PULSE_REPAIR_EXAMPLES)
            + "；必须嵌入证物、拦阻、期限、威胁或选择代价。"
        )
        lines.append(
            "- 可审计要求: 正文中至少自然出现 10 个分散的真实压力触发词，"
            "且每个触发词所在段落必须改变行动、线索状态、危险距离、期限或代价。"
        )
    if "weak_prose" in spec.cause_ids:
        lines.append("- 把抽象感官词换成具体物件、动作、温度、声音、触感或可拍镜头。")
    if "ai_voice" in spec.cause_ids:
        lines.append("- 删除 AI 句式、套话比喻、解释型对白和“他意识到/这意味着”等结论句。")
        if row:
            lines.extend(_banned_pattern_feedback_lines(row))
    if "weak_immersion" in spec.cause_ids:
        lines.append("- 拆掉心理灌水，把背景信息改成动作触发、证物变化或人物对抗。")

    if row:
        lines.extend(
            [
                "",
                "审计数据:",
                f"- char_count={row.get('char_count')}",
                f"- word_count={row.get('word_count_reason')}",
                f"- pulse_density={row.get('pulse_density')}",
                f"- banned_patterns={row.get('banned_pattern_breakdown')}",
                f"- abstract_sensory={row.get('abstract_sensory_words')}",
                (
                    "- rhythm="
                    f"hard_stop:{row.get('rhythm_hard_stops')}, "
                    f"acceleration:{row.get('rhythm_acceleration')}, "
                    f"delay:{row.get('rhythm_delay')}, "
                    f"external_interrupt:{row.get('rhythm_external_interrupts')}, "
                    f"types:{row.get('rhythm_types_covered')}/"
                    f"{row.get('rhythm_expected_min_types')}, "
                    f"total:{row.get('rhythm_total_anchors')}/"
                    f"{row.get('rhythm_expected_min_count')}"
                ),
            ]
        )
        lines.extend(_rhythm_feedback_lines(row))
        if "underflow" in word_count_reason:
            lines.extend(
                [
                    "",
                    "长度修复硬约束:",
                    "- 当前章节偏短. 本次不是局部替换, 必须补齐有效戏剧内容.",
                    f"- 内部质量门按中文汉字数计数; 安全目标是 {length_range} 个汉字.",
                    (
                        "- 禁止用设定解释、重复心理、摘要转述凑字; "
                        "必须补行动、对抗、证物变化、代价和尾钩."
                    ),
                    "- 若输出低于发布硬范围, 候选稿会被拒绝, 当前稿会被保留.",
                ]
            )
        elif "overflow" in word_count_reason:
            lines.extend(
                [
                    "",
                    "长度修复硬约束:",
                    "- 当前章节偏长. 本次必须压缩到发布硬范围内.",
                    f"- 内部质量门按中文汉字数计数; 安全目标是 {length_range} 个汉字.",
                    "- 本次是压缩型修复: 只能删并、替换、重排已有信息, 不得扩写新桥段, 不得新增人物/地点/势力/称号.",
                    "- 删除重复心理、重复环境、解释性铺陈、重复称呼和不推进主冲突的过渡段.",
                    "- 若输出超出发布硬范围, 候选稿会被拒绝, 当前稿会被保留.",
                ]
            )
        elif row.get("word_count_passed") is False:
            lines.extend(
                [
                    "",
                    "长度修复硬约束:",
                    "- 本章必须落入当前发布字数硬范围; 不要只做局部润色.",
                    "- 内部质量门按中文汉字数计数, 不按段落数或模型 token 数计数.",
                ]
            )

    if spec.patch_points:
        lines.extend(["", "必须处理的精确 patch points:"])
        for index, point in enumerate(spec.patch_points, start=1):
            lines.append(
                "{idx}. {cause} @ {location}: {issue}; 修法: {repair}; 片段: {snippet}".format(
                    idx=index,
                    cause=point.get("cause_id", ""),
                    location=point.get("location", ""),
                    issue=point.get("issue_summary", ""),
                    repair=point.get("repair_action_summary", ""),
                    snippet=str(point.get("snippet", ""))[:180],
                )
            )

    lines.extend(
        [
            "",
            "连续性与去重硬约束:",
            "- 人物姓名、称呼、关系称谓必须沿用本章上下文与已有人物池；同一人物不得无铺垫换名、换称呼或换身份标签。",
            "- 如果称呼必须变化，先在正文中写出可见触发：关系变化、身份暴露、误认纠正、旁人介绍或主角主动确认。",
            "- 章节开头必须承接上一章可见状态，不能跳过抵达、受伤、证物转移、人物离场等因果桥。",
            "- 删除重复环境描写、重复身体反应、重复心理结论；每一次保留的描写都必须带来新线索、新压力、新代价或新选择。",
            "- 对话称谓要稳定：旁人怎么叫他、他怎么自称、叙述怎么称呼，三者不能随段落漂移。",
            "",
            "输出要求:",
            "- 保留本章既有正典事实、人物目标、线索账本和章节标题。",
            "- 不引入新体系、新旧设定冲突或无代价解决。",
            "- 修后必须比原章更具体、更有行动压力、更少解释性旁白。",
        ]
    )
    return "\n".join(lines)


def _append_quality_gate_feedback_english(
    instructions: str,
    violations: Sequence[Mapping[str, object]],
    *,
    language: str | None,
) -> str:
    base_instructions = instructions.split(
        "\n\nRecent hard quality-gate blocks:", 1
    )[0].rstrip()
    lines = [
        base_instructions,
        "",
        "Recent hard quality-gate blocks:",
    ]
    for index, violation in enumerate(violations[:8], start=1):
        code = str(violation.get("code") or "UNKNOWN_GATE")
        detail = str(
            violation.get("detail") or violation.get("message") or ""
        ).strip()
        lines.append(
            f"{index}. {code}: {detail}" if detail else f"{index}. {code}"
        )
    codes = {str(item.get("code") or "") for item in violations}
    if any("LENGTH" in code for code in codes):
        length_direction = "normal"
        if any(
            code in {"LENGTH_OVER", "LENGTH_BLOCK_HIGH"}
            or str(code).endswith("_BLOCK_HIGH")
            for code in codes
        ):
            length_direction = "over"
        elif any(
            code in {"LENGTH_UNDER", "LENGTH_BLOCK_LOW"}
            or str(code).endswith("_BLOCK_LOW")
            for code in codes
        ):
            length_direction = "under"
        safe_band = _global_quality_repair_band(
            direction=length_direction,
            language=language,
        )
        lines.extend(
            [
                (
                    "- Length is a hard gate: the final chapter body must stay "
                    f"inside the {safe_band} English words safe band."
                ),
                (
                    "- Self-check length before output; do not return a summary, "
                    "outline, partial replacement, or oversized expansion."
                ),
            ]
        )
    if "CLIFFHANGER_REPEAT" in codes:
        lines.append(
            (
                "- Change the ending hook type: avoid repeated body-reaction "
                "endings; use evidence reversal, rule exposure, deadline pressure, "
                "or a costly choice."
            )
        )
    if "OPENING_ENTITY_OVERLOAD" in codes:
        lines.extend(
            [
                (
                    "- Opening naming constraint: keep at most five named entities "
                    "in the opening, limited to the protagonist, opponent, scene "
                    "landmark, and mystery node."
                ),
                (
                    "- Any alias switch must have a visible trigger such as "
                    "relationship change, identity reveal, correction, "
                    "introduction, or protagonist confirmation."
                ),
                (
                    "- Do not use a single missing-parent/worldview hook as the "
                    "fixed opening every time."
                ),
            ]
        )
    if "GOLDEN_THREE_WEAK" in codes:
        lines.extend(
            [
                (
                    "- Golden-three repair: put a clear market hook in the first "
                    "1000 words and attach it to visible action."
                ),
                (
                    "- The chapter ending must leave read-on resistance: deadline "
                    "pressure, interrupted evidence, a costly choice, or rule "
                    "exposure."
                ),
                (
                    "- Rebuild the event chain in the first 500-1000 words before "
                    "adding worldview explanation."
                ),
            ]
        )
    if {"CANON_FORBIDDEN_TERM", "NAMING_OUT_OF_POOL"} & codes:
        lines.extend(
            [
                (
                    "- Forbidden terms and out-of-pool names are hard gates: do "
                    "not include any named term called out by the latest gate."
                ),
                (
                    "- Use existing names, neutral descriptions, or "
                    "already-established relationships; do not add organizations, "
                    "factions, titles, or setting names."
                ),
            ]
        )
    if "CANON_STATE_REGRESSION" in codes:
        lines.extend(
            [
                (
                    "- Canon regression is a hard gate: do not revert locked "
                    "identity, relationship, death, disappearance, or on-page "
                    "status."
                ),
                (
                    "- Follow the latest gate detail exactly; if uncertainty is "
                    "needed, frame it as suspicion, memory, or possibility rather "
                    "than current fact."
                ),
            ]
        )
    return "\n".join(lines)


def append_quality_gate_feedback(
    instructions: str,
    violations: Sequence[Mapping[str, object]],
    *,
    language: str | None = None,
) -> str:
    if not violations:
        return instructions
    language = _language_from_instructions(instructions, language)
    if _is_english_language(language):
        return _append_quality_gate_feedback_english(
            instructions,
            violations,
            language=language,
        )
    base_instructions = instructions.split("\n\n最近质量门硬阻断:", 1)[0].rstrip()
    lines = [
        base_instructions,
        "",
        "最近质量门硬阻断:",
    ]
    for index, violation in enumerate(violations[:8], start=1):
        code = str(violation.get("code") or "UNKNOWN_GATE")
        detail = str(violation.get("detail") or violation.get("message") or "").strip()
        lines.append(f"{index}. {code}: {detail}")
    codes = {str(item.get("code") or "") for item in violations}
    if any("LENGTH" in code for code in codes):
        length_direction = "normal"
        if any(code in {"LENGTH_OVER", "LENGTH_BLOCK_HIGH"} or str(code).endswith("_BLOCK_HIGH") for code in codes):
            length_direction = "over"
        elif any(code in {"LENGTH_UNDER", "LENGTH_BLOCK_LOW"} or str(code).endswith("_BLOCK_LOW") for code in codes):
            length_direction = "under"
        safe_band = _global_quality_repair_band(
            direction=length_direction,
            language=language,
        )
        unit_label = _length_unit_label(language)
        if _is_english_language(language):
            lines.extend(
                [
                    f"- Length is a hard gate: the final chapter body must stay inside the {safe_band} {unit_label} safe band.",
                    "- Self-check length before output; do not return a summary, outline, partial replacement, or oversized expansion.",
                ]
            )
        else:
            lines.extend(
                [
                    f"- 长度是硬门: 最终正文必须控制在 {safe_band} 个{unit_label}的安全带内。",
                    "- 输出前在内部自检字数; 不要输出梗概、提纲、局部替换或超长扩写。",
                ]
            )
    if "CLIFFHANGER_REPEAT" in codes:
        lines.append(
            "- 结尾钩子必须换型: 避免连续身体反应/发热/疼痛式结尾, 改用证物反转、规则暴露、期限压迫或选择代价。"
        )
    if "OPENING_ENTITY_OVERLOAD" in codes:
        lines.extend(
            [
                "- 开场命名约束: 第一章开头最多保留 5 个命名实体（主角、对手、场景地标、悬念节点）。"
                " 其余角色/势力名改为关系称谓或时间/场景锚点，统一在后续章节再递进引入。",
                "- 若出现“他/那人”称呼切换，必须有一段可见触发（关系改口、误认纠正、身份重申）才允许切换。",
                "- 避免把“父亲失踪/母亲失踪”等一次性世界观钩子作为固定起点。",
            ]
        )
    if "GOLDEN_THREE_WEAK" in codes:
        lines.extend(
            [
                "- 黄金三章硬门修复：第一章在前 1000 字内必须出现明确卖点/钩子关键词之一，"
                "第 1-3 章触发词累计至少 2 次以上并紧跟行动推进。",
                "- 章末不允许收束句式，必须明确留下追读阻力（时间压迫、证物被截断、选择代价、规则曝光）。",
                "- 本次重写优先恢复前 500-1000 字的事件链，不要先解释世界观。",
            ]
        )
    if {"CANON_FORBIDDEN_TERM", "NAMING_OUT_OF_POOL"} & codes:
        lines.extend(
            [
                "- 禁用词和越池命名是硬门: 任何最近质量门点名的词都不得出现在正文。",
                "- 必须用已存在的人物/地点/物件称谓或中性描述替换, 不要新增组织、派别、称号或设定名。",
            ]
        )
    if "CANON_STATE_REGRESSION" in codes:
        lines.extend(
            [
                "- 正典状态回退是硬门: 不得把已锁定的人物身份、亲缘、死亡/失踪/入镜状态写回旧状态。",
                "- 必须按最近质量门 detail 中点名的正典关系改写；如果只能表达怀疑，用“像/疑似/可能/记忆画面”而不是确认为亲属或当下事实。",
            ]
        )
    return "\n".join(lines)


def _append_previous_rewrite_failure_feedback_english(
    instructions: str,
    failures: Sequence[Mapping[str, object]],
    *,
    language: str | None,
) -> str:
    base_instructions = instructions.split(
        "\n\nRecent failed candidate feedback:", 1
    )[0].rstrip()
    lines = [
        base_instructions,
        "",
        "Recent failed candidate feedback:",
    ]
    codes: list[str] = []
    for index, failure in enumerate(failures[:3], start=1):
        word_count = failure.get("candidate_word_count")
        if word_count:
            lines.append(
                (
                    f"{index}. Previous candidate word_count={word_count}, but it "
                    "did not become the current draft."
                )
            )
        else:
            lines.append(
                f"{index}. Previous candidate did not become the current draft."
            )
        for finding in _sequence(failure.get("findings"))[:4]:
            if not isinstance(finding, Mapping):
                continue
            code = str(finding.get("code") or "UNKNOWN_FAILURE")
            detail = str(
                finding.get("detail") or finding.get("message") or ""
            ).strip()
            codes.append(code)
            lines.append(f"   - {code}: {detail}" if detail else f"   - {code}")

    unique_codes = set(codes)
    failure_codes = [code for code in codes if code and code != "UNKNOWN_FAILURE"]
    duplicate_codes = {
        code: count
        for code, count in Counter(failure_codes).items()
        if count >= 2
    }
    if duplicate_codes:
        lines.extend(
            [
                "",
                "Repeated-failure repair constraints:",
                (
                    "- The chapter has failed with the same class of issue more "
                    "than once; do a structural rewrite instead of adding "
                    "isolated words."
                ),
                (
                    "- Rewrite the opening, middle, and ending hook; at least "
                    "45% of paragraphs should materially differ from the current "
                    "draft."
                ),
            ]
        )
        if "QUALITY_RETROFIT_WEAK_ATTRACTION" in duplicate_codes:
            lines.append(
                (
                    "- Rebuild at least three pressure nodes; each node must "
                    "change a decision, evidence state, danger distance, or "
                    "visible cost."
                )
            )
        if "QUALITY_RETROFIT_FLAT_NARRATION" in duplicate_codes:
            lines.append(
                (
                    "- Rebuild the pacing skeleton with clear scene turns, short "
                    "beats, delay, and external interruption that change the next "
                    "action."
                )
            )
        if "QUALITY_RETROFIT_WEAK_PROSE" in duplicate_codes:
            lines.append(
                (
                    "- Replace vague state labels with visible action, object "
                    "change, and concrete confrontation."
                )
            )
        if "GOLDEN_THREE_WEAK" in duplicate_codes:
            lines.append(
                (
                    "- Rebuild the first-three-chapter hook chain: conflict "
                    "promise, action, read-on hook, and visible cost."
                )
            )

    if "QUALITY_RETROFIT_WEAK_ATTRACTION" in unique_codes:
        lines.extend(
            [
                (
                    "- Previous version still had weak attraction: do not add "
                    "mood words only; add real pressure nodes."
                ),
                (
                    "- Each pressure node must change the next action, clue state, "
                    "danger distance, deadline, threat, or visible cost."
                ),
            ]
        )
    if "QUALITY_RETROFIT_FLAT_NARRATION" in unique_codes:
        lines.extend(
            [
                (
                    "- Previous version still read flat: add scene turns and "
                    "pacing variation instead of only increasing interruption "
                    "count."
                ),
                (
                    "- Preserve external interruptions only when each one pushes "
                    "evidence, blockage, pursuit, or a choice cost."
                ),
            ]
        )
    if "OPENING_ENTITY_OVERLOAD" in unique_codes:
        lines.extend(
            [
                (
                    "- Previous version still overloaded the opening with names: "
                    "keep only the core axis and convert the rest to relationship "
                    "labels."
                ),
                (
                    "- The first paragraph must establish who, where, and what "
                    "consequence is happening now."
                ),
            ]
        )
    if "GOLDEN_THREE_WEAK" in unique_codes:
        lines.append(
            (
                "- Golden-three rewrite: reset opening pace, put the reader "
                "promise in the first scene, add action consequence and "
                "deadline/chase pressure, and end with a read-on hook."
            )
        )
    if any("LENGTH" in code or code.endswith("_BLOCK_LOW") for code in unique_codes):
        length_direction = "under"
        if any(
            code in {"LENGTH_OVER", "LENGTH_BLOCK_HIGH"}
            or str(code).endswith("_BLOCK_HIGH")
            for code in unique_codes
        ):
            length_direction = "over"
        safe_band = _global_quality_repair_band(
            direction=length_direction,
            language=language,
        )
        lines.extend(
            [
                (
                    "- Previous candidate triggered the length hard gate: the next "
                    f"version must use {safe_band} English words as the safe band."
                ),
                (
                    "- Expansion can only add action, confrontation, evidence "
                    "change, causal bridge, or ending hook pressure."
                ),
            ]
        )
    if {"CANON_FORBIDDEN_TERM", "NAMING_OUT_OF_POOL"} & unique_codes:
        lines.extend(
            [
                (
                    "- Previous version triggered naming/canon hard gates: do not "
                    "add character, title, faction, or location names."
                ),
                "- All names and aliases must come from existing context.",
            ]
        )
    return "\n".join(lines)


def append_previous_rewrite_failure_feedback(
    instructions: str,
    failures: Sequence[Mapping[str, object]],
    *,
    language: str | None = None,
) -> str:
    if not failures:
        return instructions
    language = _language_from_instructions(instructions, language)
    if _is_english_language(language):
        return _append_previous_rewrite_failure_feedback_english(
            instructions,
            failures,
            language=language,
        )
    base_instructions = instructions.split("\n\n最近失败候选稿反馈:", 1)[0].rstrip()
    lines = [
        base_instructions,
        "",
        "最近失败候选稿反馈:",
    ]
    codes: list[str] = []
    for index, failure in enumerate(failures[:3], start=1):
        word_count = failure.get("candidate_word_count")
        if word_count:
            lines.append(f"{index}. 上一版候选稿字数={word_count}，但未能晋升为 current draft。")
        else:
            lines.append(f"{index}. 上一版候选稿未能晋升为 current draft。")
        for finding in _sequence(failure.get("findings"))[:4]:
            if not isinstance(finding, Mapping):
                continue
            code = str(finding.get("code") or "UNKNOWN_FAILURE")
            detail = str(finding.get("detail") or finding.get("message") or "").strip()
            codes.append(code)
            if detail:
                lines.append(f"   - {code}: {detail}")
            else:
                lines.append(f"   - {code}")
    unique_codes = set(codes)
    failure_codes = [code for code in codes if code and code != "UNKNOWN_FAILURE"]
    if len(failure_codes) >= 2:
        duplicate_codes = {
            code: count
            for code, count in Counter(failure_codes).items()
            if count >= 2
        }
        if duplicate_codes:
            lines.extend(
                [
                    "",
                    "【重复失败闭环修复约束】",
                    "本章节近几次重写已出现同类失败，必须做结构级重写，不允许仅补字。",
                    "重写范围覆盖章节开端、中段与尾钩，至少 45% 段落应与当前草稿不同。",
                ]
            )
            if "QUALITY_RETROFIT_WEAK_ATTRACTION" in duplicate_codes:
                lines.extend(
                    [
                        "- 牵引类失败重复触发：重建 3 个及以上压力场景节点，每个节点要改写人物决策、线索状态或可见代价，"
                        "不能仅替换同义词。",
                    ]
                )
            if "QUALITY_RETROFIT_FLAT_NARRATION" in duplicate_codes:
                lines.extend(
                    [
                        "- 节奏类失败重复触发：必须重新组织节奏骨架，新增短硬停顿段（<=12 字）+ 三连加速（8 字以内 x3）+ 延宕停拍，"
                        "并让每处锚点改变后续行动。",
                    ]
                )
            if "OPENING_ENTITY_OVERLOAD" in duplicate_codes:
                lines.extend(
                    [
                        "- 开场命名失败重复触发: 本次必须压缩首章实体，保留关键主轴 5 个以内，"
                        "其余改为关系代称；每次更名需给出可见切换理由。"
                    ]
                )
            if "QUALITY_RETROFIT_WEAK_PROSE" in duplicate_codes:
                lines.extend(
                    [
                        "- 文风类失败重复触发：将“模糊感官词/状态词”改为可见动作、物件变化、具体对抗。",
                    ]
                )
            if "GOLDEN_THREE_WEAK" in duplicate_codes:
                lines.extend(
                    [
                        "- 黄金三章弱化重复触发: 在前三章重建“冲突承诺-行动-追读钩子”的连续链，"
                        "开端 1000 字必须出现卖点；每章要有可见代价。",
                    ]
                )
    if "QUALITY_RETROFIT_WEAK_ATTRACTION" in unique_codes:
        lines.extend(
            [
                "- 上一版仍然弱吸引力: 不能只增加气氛词，必须增加真实压力节点。",
                "- 2200-2600 汉字段落至少安排 9-10 个分散的压力/阻断/证物变化/选择代价节点。",
                "- 每个压力节点都要改变人物下一步行动、线索状态、危险距离或可见代价，不能只是“心一沉/气氛变冷”。",
                "- 下一版必须让至少 10 个压力触发词自然落在不同段落中，并绑定动作、证物变化、阻断、期限、威胁或选择代价。",
            ]
        )
    if "QUALITY_RETROFIT_FLAT_NARRATION" in unique_codes:
        lines.extend(
            [
                "- 上一版仍然平铺: 不能只增加外部打断数量，必须补齐至少 3 类节奏锚点。",
                "- 下一版必须包含一个 12 个汉字以内的独立短硬停顿段。",
                "- 下一版必须包含一次三连短段加速，每段 1-8 个汉字，且连续出现。",
                "- 下一版必须包含一次可检测延宕停拍，例如“停了一拍。”，并让停拍改变判断或行动。",
                "- 下一版必须保留外部打断，但每个打断都要推动证物、阻断、追逼或选择代价。",
            ]
        )
    if "OPENING_ENTITY_OVERLOAD" in unique_codes:
        lines.extend(
            [
                "- 上一版仍命名过载: 只允许核心实体在可控范围内，其他实体改成关系称谓。",
                "- 本次重写必须在第一段给出“是谁/在哪里/现在面临什么后果”。",
            ]
        )
    if "GOLDEN_THREE_WEAK" in unique_codes:
        lines.extend(
            [
                "- 黄金三章重写指令: 重置开篇节奏。把读者承诺词放入首场景，补充一处行动后果和一处期限/追击压力，"
                "并将章末改为可延续钩子。"
            ]
        )
    if any("LENGTH" in code or code.endswith("_BLOCK_LOW") for code in unique_codes):
        length_direction = "under"
        if any(code in {"LENGTH_OVER", "LENGTH_BLOCK_HIGH"} or str(code).endswith("_BLOCK_HIGH") for code in unique_codes):
            length_direction = "over"
        lines.extend(
            [
                "- 上一版触发长度硬门: 下一版必须以 "
                f"{_global_quality_repair_band(direction=length_direction, language=language)} 个有效中文汉字为安全区。",
                "- 扩写只能补行动、对抗、证物变化、过场桥和尾钩，不得用解释、总结或重复心理凑字。",
            ]
        )
    if {"CANON_FORBIDDEN_TERM", "NAMING_OUT_OF_POOL"} & unique_codes:
        lines.extend(
            [
                "- 上一版触发命名/正典硬门: 不得新增人物名、称号、势力名或地点专名。",
                "- 所有称呼必须来自已有上下文；普通器物动作不要写成像新人物名的连续称谓。",
            ]
        )
    return "\n".join(lines)


async def load_latest_quality_gate_violations(
    session: AsyncSession,
    chapter: ChapterModel,
) -> tuple[Mapping[str, object], ...]:
    latest_quality_report = await session.scalar(
        select(ChapterQualityReportModel)
        .where(ChapterQualityReportModel.chapter_id == chapter.id)
        .order_by(ChapterQualityReportModel.created_at.desc())
    )
    if latest_quality_report is None:
        return ()
    report_json = latest_quality_report.report_json
    if not isinstance(report_json, Mapping):
        return ()
    return tuple(
        dict(item)
        for item in report_json.get("violations", [])
        if isinstance(item, Mapping)
    )


async def load_recent_failed_rewrite_feedback(
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    *,
    limit: int = 3,
) -> tuple[Mapping[str, object], ...]:
    rows = list(
        (
            await session.execute(
                select(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.trigger_source_id == chapter.id,
                    RewriteTaskModel.trigger_type == AUTONOMOUS_REPAIR_TRIGGER,
                    RewriteTaskModel.status == "failed",
                )
                .order_by(RewriteTaskModel.updated_at.desc(), RewriteTaskModel.created_at.desc())
                .limit(max(int(limit or 0), 1))
            )
        ).scalars()
    )
    feedback: list[Mapping[str, object]] = []
    for task in rows:
        metadata = task.metadata_json if isinstance(task.metadata_json, Mapping) else {}
        findings: list[Mapping[str, object]] = []
        for key in (
            "candidate_quality_retrofit_findings",
            "candidate_quality_gate_violations",
            "llm_candidate_quality_gate_violations",
        ):
            for item in _sequence(metadata.get(key)):
                if isinstance(item, Mapping):
                    findings.append(dict(item))
        if not findings and task.error_log:
            findings.append({"code": "REWRITE_TASK_FAILED", "detail": task.error_log})
        if findings:
            feedback.append(
                {
                    "task_id": str(task.id),
                    "candidate_word_count": metadata.get("candidate_word_count"),
                    "findings": findings[:8],
                }
            )
    return tuple(feedback)


async def create_quality_retrofit_rewrite_tasks(
    session: AsyncSession,
    project: ProjectModel,
    specs: Sequence[QualityRepairTaskSpec],
    *,
    replace_existing: bool = False,
) -> TaskSyncResult:
    created = 0
    skipped = 0
    superseded = 0
    task_ids: list[str] = []
    missing_chapters: list[int] = []
    for spec in specs:
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == spec.chapter_number,
            )
        )
        if chapter is None:
            missing_chapters.append(spec.chapter_number)
            continue
        quality_gate_violations = await load_latest_quality_gate_violations(
            session,
            chapter,
        )
        previous_failure_feedback = await load_recent_failed_rewrite_feedback(
            session,
            project,
            chapter,
        )
        instructions = append_quality_gate_feedback(
            build_quality_repair_instructions(spec),
            quality_gate_violations,
        )
        instructions = append_previous_rewrite_failure_feedback(
            instructions,
            previous_failure_feedback,
        )

        existing = list(
            (
                await session.execute(
                    select(RewriteTaskModel).where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.trigger_source_id == chapter.id,
                        RewriteTaskModel.trigger_type == AUTONOMOUS_REPAIR_TRIGGER,
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                    )
                )
            ).scalars()
        )
        if existing and not replace_existing:
            for task in existing:
                task.priority = min(int(task.priority or spec.task_priority), spec.task_priority)
                task.instructions = instructions
                task.context_required = [
                    "chapter_context",
                    "current_chapter_draft",
                    "quality_retrofit_audit_row",
                    "quality_retrofit_patch_points",
                    "whole_book_quality_gate",
                    "premium_category_hard_engine",
                ]
                task.metadata_json = {
                    **(task.metadata_json or {}),
                    "autonomous_repair_id": spec.repair_id,
                    "source": "quality_levers_retrofit_audit",
                    "slug": spec.slug,
                    "chapter_number": spec.chapter_number,
                    "priority": spec.priority,
                    "language": _repair_language(spec),
                    "cause_ids": list(spec.cause_ids),
                    "patch_points": [dict(point) for point in spec.patch_points],
                    "audit_row": dict(spec.audit_row),
                    "quality_failure_events": _quality_failure_events_for_spec(spec),
                    "latest_quality_gate_violations": [
                        dict(item) for item in quality_gate_violations
                    ],
                    "recent_failed_rewrite_feedback": [
                        dict(item) for item in previous_failure_feedback
                    ],
                    "instructions_refreshed_from_latest_spec": True,
                }
            skipped += 1
            task_ids.extend(str(task.id) for task in existing)
            continue
        if existing and replace_existing:
            await session.execute(
                update(RewriteTaskModel)
                .where(RewriteTaskModel.id.in_([task.id for task in existing]))
                .values(status="superseded")
            )
            superseded += len(existing)

        task = RewriteTaskModel(
            project_id=project.id,
            trigger_type=AUTONOMOUS_REPAIR_TRIGGER,
            trigger_source_id=chapter.id,
            rewrite_strategy=AUTONOMOUS_REPAIR_STRATEGY,
            priority=spec.task_priority,
            status="pending",
            instructions=instructions,
            context_required=[
                "chapter_context",
                "current_chapter_draft",
                "quality_retrofit_audit_row",
                "quality_retrofit_patch_points",
                "whole_book_quality_gate",
                "premium_category_hard_engine",
            ],
            metadata_json={
                "autonomous_repair_id": spec.repair_id,
                "source": "quality_levers_retrofit_audit",
                "slug": spec.slug,
                "chapter_number": spec.chapter_number,
                "priority": spec.priority,
                "language": _repair_language(spec),
                "cause_ids": list(spec.cause_ids),
                "patch_points": [dict(point) for point in spec.patch_points],
                "audit_row": dict(spec.audit_row),
                "quality_failure_events": _quality_failure_events_for_spec(spec),
                "latest_quality_gate_violations": [
                    dict(item) for item in quality_gate_violations
                ],
                "recent_failed_rewrite_feedback": [
                    dict(item) for item in previous_failure_feedback
                ],
            },
        )
        session.add(task)
        await session.flush()
        task_ids.append(str(task.id))
        created += 1
    return TaskSyncResult(
        created=created,
        skipped_existing=skipped,
        superseded=superseded,
        missing_chapters=tuple(missing_chapters),
        task_ids=tuple(task_ids),
    )


def _split_causes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item for item in (part.strip() for part in value.split(";")) if item)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value if str(item))
    return ()


def _sequence(value: object) -> list[object]:
    if value is None or isinstance(value, str | bytes):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


__all__ = [
    "AUTONOMOUS_REPAIR_STRATEGY",
    "AUTONOMOUS_REPAIR_TRIGGER",
    "QualityRepairPlan",
    "QualityRepairTaskSpec",
    "TaskSyncResult",
    "append_previous_rewrite_failure_feedback",
    "append_quality_gate_feedback",
    "build_quality_repair_instructions",
    "build_quality_repair_plan",
    "create_quality_retrofit_rewrite_tasks",
    "discover_output_book_slugs",
    "latest_quality_retrofit_csv",
    "load_patch_plan",
    "load_quality_retrofit_rows",
]

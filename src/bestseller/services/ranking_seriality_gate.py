# ruff: noqa: RUF001
"""Ranking-candidate seriality gate.

This gate is deliberately separate from :mod:`seriality_capacity`.  The
capacity report estimates a ceiling and has long-standing thresholds for
epic-length projects; this report answers the earlier product question:
does a 30+ chapter ranking candidate already have enough renewable machinery
to avoid being a one-shot premise stretched into a book?
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
import re
import unicodedata
from typing import Any

from bestseller.services.concept_fact_consistency import _parse_cn_number
from bestseller.services.seriality_capacity import evaluate_seriality_capacity


@dataclass(frozen=True, slots=True)
class RankingSerialityFinding:
    code: str
    message: str
    path: str
    repair_action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "repair_action": self.repair_action,
        }


@dataclass(frozen=True, slots=True)
class RankingSerialityReport:
    passed: bool
    status: str
    target_chapters: int
    findings: tuple[RankingSerialityFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "status": self.status,
            "target_chapters": self.target_chapters,
            "blocking_codes": list(self.blocking_codes),
            "findings": [item.to_dict() for item in self.findings],
            "metrics": dict(self.metrics),
            "schema_version": "ranking-seriality.v1",
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return ()
    return tuple(dict.fromkeys(item for item in (_text(v) for v in value) if item))


def _integer(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _finding(code: str, message: str, path: str, repair_action: str) -> RankingSerialityFinding:
    return RankingSerialityFinding(code, message, path, repair_action)


_CADENCE_NUMBER = r"[0-9零一两二三四五六七八九十百]+"
_CADENCE_RE = re.compile(
    rf"每({_CADENCE_NUMBER})(?:[-—–至到~]({_CADENCE_NUMBER}))?章"
)


def required_ranking_unit_count(target_chapters: int) -> int:
    """Return the shared chapter-level unit budget for ranking candidates.

    This is the contract used by both the prompt and the promotion gate.  It
    intentionally keeps the four-chapter upper cadence bound; callers must
    repair a proof below this floor rather than silently accepting a smaller
    capacity estimate from the long-horizon ruler.
    """

    target = max(0, _integer(target_chapters))
    return 0 if target < 30 else max(1, math.ceil(target / 4))


def _cadence_is_chapter_level(value: str) -> bool:
    """Accept a stated 2-4 chapter cadence, reject vague or sparse claims."""

    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    match = _CADENCE_RE.search(compact)
    if not match:
        return False
    lower = _parse_cn_number(match.group(1))
    upper = _parse_cn_number(match.group(2)) if match.group(2) else lower
    return lower is not None and upper is not None and 1 <= lower <= upper <= 4


def evaluate_ranking_seriality(
    proof: Mapping[str, Any] | None,
    *,
    target_chapters: int,
) -> RankingSerialityReport:
    """Apply the minimum seriality proof required of a 30+ chapter candidate.

    The minimum unit budget uses the least demanding allowed cadence (one
    consequential unit per four chapters), so the gate cannot be passed by a
    merely asserted total.  For 200+ chapters the established capacity
    evaluator is also run and its report is retained as evidence.
    """

    target = max(0, _integer(target_chapters))
    payload = proof if isinstance(proof, Mapping) else {}
    if target < 30:
        return RankingSerialityReport(
            passed=True,
            status="NOT_APPLICABLE",
            target_chapters=target,
            metrics={"minimum_unit_count": 0, "capacity_report_delegated": False},
        )

    repeatable = _text(payload.get("repeatable_story_unit"))
    cadence = _text(payload.get("unit_frequency"))
    unit_count = _integer(payload.get("unit_count_estimate"))
    families = _items(payload.get("unit_families"))
    renewals = _items(payload.get("renewal_sources"))
    tracks = _items(payload.get("accumulation_tracks"))
    phases = _items(payload.get("phase_transitions"))
    ecology = _items(payload.get("opposing_ecology"))
    minimum_units = required_ranking_unit_count(target)
    findings: list[RankingSerialityFinding] = []

    if not repeatable:
        findings.append(
            _finding(
                "repeatable_story_unit_missing",
                "榜单候选没有可反复生成新内容的故事单元。",
                "seriality_proof.repeatable_story_unit",
                "明确每次案件、交易、建设或对抗如何换内容而不只是重复措辞。",
            )
        )
    if not _cadence_is_chapter_level(cadence):
        findings.append(
            _finding(
                "unit_frequency_unproven_or_sparse",
                "故事单元没有证明以每 1-4 章的节奏持续发生。",
                "seriality_proof.unit_frequency",
                "给出每2-4章一次的自然节奏，并说明一次单元包含的选择与反制。",
            )
        )
    if unit_count < minimum_units:
        findings.append(
            _finding(
                "unit_count_below_minimum",
                f"故事单元预算为 {unit_count}，低于 {target} 章候选的最低 {minimum_units} 个。",
                "seriality_proof.unit_count_estimate",
                "按阶段拆出至少每四章一个可变单元；不能只填写一个全书总数。",
            )
        )
    checks = (
        (
            len(families) < 3,
            "unit_family_breadth_thin",
            "unit_families",
            "至少设计三种不同的行动语法。",
        ),
        (
            len(renewals) < 2,
            "renewal_sources_thin",
            "renewal_sources",
            "补足至少两个彼此独立、持续制造新冲突的来源。",
        ),
        (
            len(tracks) < 2,
            "accumulation_tracks_thin",
            "accumulation_tracks",
            "补足至少两条不会每个单元归零的累积轴。",
        ),
        (
            len(phases) < 3,
            "phase_transitions_thin",
            "phase_transitions",
            "设计至少三个改变场域、规则或目标的阶段转折。",
        ),
        (
            len(ecology) < 2,
            "opposing_ecology_thin",
            "opposing_ecology",
            "补足至少两个有独立利益且会互相牵制的对立生态。",
        ),
    )
    for failed, code, path, repair in checks:
        if failed:
            count = len(_items(payload.get(path)))
            findings.append(
                _finding(
                    code,
                    f"{path} 只有 {count} 项，榜单候选最低要求为 2-3 项。",
                    f"seriality_proof.{path}",
                    repair,
                )
            )

    metrics: dict[str, object] = {
        "minimum_unit_count": minimum_units,
        "unit_count_estimate": unit_count,
        "unit_frequency": cadence,
        "unit_family_count": len(families),
        "renewal_source_count": len(renewals),
        "accumulation_track_count": len(tracks),
        "phase_transition_count": len(phases),
        "opposing_ecology_count": len(ecology),
        "capacity_report_delegated": False,
    }
    if target >= 200:
        capacity = evaluate_seriality_capacity(
            payload,
            target_chapters=target,
            require_phase_coverage=True,
        )
        metrics["capacity_report_delegated"] = True
        metrics["capacity_report"] = capacity.to_dict()
        for item in capacity.findings:
            findings.append(
                _finding(
                    f"capacity_{item.code}",
                    item.message,
                    item.path,
                    item.repair_action,
                )
            )

    return RankingSerialityReport(
        passed=not findings,
        status="PASS" if not findings else "BLOCKED",
        target_chapters=target,
        findings=tuple(findings),
        metrics=metrics,
    )


__all__ = [
    "RankingSerialityFinding",
    "RankingSerialityReport",
    "evaluate_ranking_seriality",
    "required_ranking_unit_count",
]

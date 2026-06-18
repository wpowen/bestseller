"""Project-level commercial novel gate.

This gate complements chapter-scope validators. L4/L5 can prove a single
chapter is syntactically clean and locally consistent; this module asks
whether the book still behaves like a professional commercial serial:

* the reader contract is visible and repeatedly paid;
* canon and state do not drift back to deprecated worlds;
* the current batch follows the planned mission;
* the first-volume payoff is not spent in the opening dozen chapters;
* genre identity stays aligned with the listing.

The implementation is deterministic and file-package friendly so operators
can run it over ``output/<book-id>`` before approving a generated batch.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass, field
from itertools import pairwise
import json
from pathlib import Path
import re
import statistics
from typing import Any, Literal

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.canon_guardrails import (
    CanonGuardrails,
    load_canon_guardrails_file,
)
from bestseller.services.forward_state_contract_gate import evaluate_forward_state_contract
from bestseller.services.outline_reveal_alignment_gate import (
    evaluate_outline_reveal_alignment,
    load_reveal_schedule,
)
from bestseller.services.outline_specificity_gate import evaluate_outline_specificity
from bestseller.services.prewrite_contract_gate import evaluate_prewrite_contract_readiness
from bestseller.services.reader_power import analyze_golden_three
from bestseller.services.retention_onboarding_gate import scan_retention_onboarding_package
from bestseller.services.volume_plan_resolution_gate import (
    evaluate_volume_plan_resolution,
    load_volume_plan_v2_file,
)

GateSeverity = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class CommercialAnchor:
    """A reader-contract signal that should recur in a serial."""

    key: str
    terms: tuple[str, ...]
    max_gap_chapters: int = 6
    required_until_chapter: int | None = None
    min_total_hits: int = 1


@dataclass(frozen=True)
class CommercialGatePolicy:
    min_professional_score: int = 95
    blocking_severities: tuple[GateSeverity, ...] = ("critical", "high", "medium")
    anchor_window_chapters: int = 6
    length_cv_warn: float = 0.28
    length_cv_fail: float = 0.42
    premature_payoff_ratio: float = 0.5
    infinite_flow_drift_terms: tuple[str, ...] = ("副本", "玩家", "APP", "游戏")
    infinite_flow_not_recommended_markers: tuple[str, ...] = ("无限流", "纯无限流")
    premature_payoff_terms: tuple[str, ...] = (
        "破镜",
        "终章",
        "本源",
        "百年真相",
        "归墟之主",
        "真正敌人",
    )
    outline_asset_gates_block_on_failure: bool = False
    anchors: tuple[CommercialAnchor, ...] = ()


@dataclass(frozen=True)
class CommercialGateIssue:
    code: str
    severity: GateSeverity
    chapter_no: int | None
    detail: str
    suggestion: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def closure(self) -> CommercialIssueClosure:
        return _closure_for_commercial_issue(self)


@dataclass(frozen=True)
class CommercialIssueClosure:
    immediate_repair: str
    recurrence_prevention: str
    verification: str
    rerun_scope: str

    def to_dict(self) -> dict[str, str]:
        return {
            "immediate_repair": self.immediate_repair,
            "recurrence_prevention": self.recurrence_prevention,
            "verification": self.verification,
            "rerun_scope": self.rerun_scope,
        }


@dataclass(frozen=True)
class CommercialGateReport:
    book_id: str
    title: str
    total_chapters: int
    overall_score: int
    passed: bool
    issues: tuple[CommercialGateIssue, ...]
    metrics: Mapping[str, Any]

    @property
    def hard_issues(self) -> tuple[CommercialGateIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity in {"critical", "high"})

    @property
    def gate_verdict(self) -> GateVerdict:
        if any(issue.severity == "critical" for issue in self.issues):
            verdict = "blocked"
        elif self.passed:
            verdict = "pass"
        else:
            verdict = "warn_only"
        return GateVerdict(
            gate_name="commercial_novel_gate",
            verdict=verdict,
            coverage=max(0.0, min(1.0, self.overall_score / 100)),
            findings=tuple(
                GateFinding(
                    code=issue.code,
                    severity=issue.severity,
                    message=issue.detail,
                    path=(
                        f"chapter-{issue.chapter_no:03d}.md"
                        if issue.chapter_no is not None
                        else ""
                    ),
                    repair_action=issue.suggestion,
                )
                for issue in self.issues
            ),
            metrics={
                **dict(self.metrics),
                "quality_score": self.overall_score,
                "total_chapters": self.total_chapters,
            },
        )


@dataclass(frozen=True)
class ChapterText:
    chapter_no: int
    title: str
    text: str
    path: Path

    @property
    def body_chars(self) -> int:
        lines = [line.strip() for line in self.text.splitlines() if line.strip()]
        return len("".join(lines[1:])) if len(lines) > 1 else len(self.text.strip())


def evaluate_book_package(
    package_dir: str | Path,
    *,
    policy: CommercialGatePolicy | None = None,
) -> CommercialGateReport:
    """Evaluate an output book package as one commercial serial."""

    root = Path(package_dir)
    metadata = _load_json(root / "listing" / "book-listing-metadata.json")
    chapters = _load_chapters(root)
    story_text = _load_story_context(root)
    guardrails = load_canon_guardrails_file(root / "story-bible" / "canon-guardrails.json")
    effective_policy = policy or CommercialGatePolicy(
        anchors=_infer_commercial_anchors(metadata, story_text)
    )

    issues: list[CommercialGateIssue] = []
    issues.extend(_check_package_artifacts(root, metadata, chapters))
    issues.extend(
        _check_planning_artifact_drift(root, metadata, guardrails, effective_policy)
    )
    issues.extend(_check_golden_three(chapters))
    retention_verdict = scan_retention_onboarding_package(root, chapters)
    outline_specificity_verdicts = _evaluate_outline_specificity_audit_gates(root, chapters)
    issues.extend(_retention_findings_to_commercial_issues(retention_verdict.findings))
    if effective_policy.outline_asset_gates_block_on_failure:
        issues.extend(
            _outline_asset_gate_findings_to_commercial_issues(
                outline_specificity_verdicts
            )
        )
    issues.extend(_check_package_integrity(root, chapters))
    issues.extend(_check_canon_guardrails(chapters, guardrails))
    issues.extend(_check_reader_contract(chapters, effective_policy))
    issues.extend(_check_genre_contract(chapters, metadata, effective_policy))
    issues.extend(_check_batch_queue_alignment(root, chapters))
    issues.extend(_check_premature_payoff(root, chapters, effective_policy))
    issues.extend(_check_length_stability(chapters, effective_policy))

    score = _score_issues(issues)
    blocking_severities = set(effective_policy.blocking_severities)
    blocking_issues = [issue for issue in issues if issue.severity in blocking_severities]
    passed = score >= effective_policy.min_professional_score and not blocking_issues
    metrics = {
        "anchor_groups": [
            {"key": anchor.key, "terms": list(anchor.terms), "max_gap": anchor.max_gap_chapters}
            for anchor in effective_policy.anchors
        ],
        "issue_counts": _issue_counts(issues),
        "blocking_severities": list(effective_policy.blocking_severities),
        "blocking_issue_counts": _issue_counts(blocking_issues),
        "retention_onboarding_gate": retention_verdict.model_dump(mode="json"),
        "outline_specificity_audit_gates": [
            verdict.model_dump(mode="json") for verdict in outline_specificity_verdicts
        ],
    }

    return CommercialGateReport(
        book_id=str(metadata.get("book_id") or root.name),
        title=str(metadata.get("primary_title") or _read_first_heading(root / "README.md")),
        total_chapters=len(chapters),
        overall_score=score,
        passed=passed,
        issues=tuple(issues),
        metrics=metrics,
    )


def commercial_gate_report_to_dict(report: CommercialGateReport) -> dict[str, Any]:
    gate_verdict = report.gate_verdict
    return {
        "book_id": report.book_id,
        "title": report.title,
        "total_chapters": report.total_chapters,
        "overall_score": report.overall_score,
        "quality_score": report.overall_score,
        "passed": gate_verdict.passed,
        "gate_verdict": gate_verdict.model_dump(mode="json"),
        "metrics": dict(report.metrics),
        "closure_plan": _commercial_gate_closure_plan(report),
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "chapter_no": issue.chapter_no,
                "detail": issue.detail,
                "suggestion": issue.suggestion,
                "evidence": dict(issue.evidence),
                "closure": issue.closure.to_dict(),
            }
            for issue in report.issues
        ],
    }


def _evaluate_outline_specificity_audit_gates(
    root: Path,
    chapters: Sequence[ChapterText],
) -> tuple[GateVerdict, ...]:
    """Run G8-G11 in audit mode without affecting commercial pass/fail yet."""

    story_bible_dir = root / "story-bible"
    verdicts: list[GateVerdict] = []
    contract_payload = _load_json(story_bible_dir / "prewrite-contract.json")
    chapter_contracts = contract_payload.get("chapters")
    if isinstance(chapter_contracts, Mapping):
        previous: Mapping[str, Any] | None = None
        for key, item in sorted(
            chapter_contracts.items(),
            key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 0,
        ):
            if not isinstance(item, Mapping):
                continue
            outline = {"chapter_no": int(key) if str(key).isdigit() else 0, **dict(item)}
            verdicts.append(
                evaluate_prewrite_contract_readiness(
                    chapter_no=int(key) if str(key).isdigit() else 0,
                    contract=contract_payload,
                )
            )
            verdicts.append(evaluate_outline_specificity(outline, prev_outline=previous))
            previous = outline

    volume_v2_path = story_bible_dir / "volume-plan-v2.yaml"
    if volume_v2_path.exists():
        try:
            verdicts.append(
                evaluate_volume_plan_resolution(load_volume_plan_v2_file(volume_v2_path))
            )
        except (OSError, ValueError) as exc:
            verdicts.append(
                GateVerdict(
                    gate_name="volume_plan_resolution_gate",
                    verdict="error",
                    coverage=0.0,
                    findings=(
                        GateFinding(
                            code="VOLUME_PLAN_V2_LOAD_ERROR",
                            severity="critical",
                            message=str(exc),
                            path="story-bible/volume-plan-v2.yaml",
                        ),
                    ),
                )
            )

    ledger_path = story_bible_dir / "event-state-ledger.md"
    if ledger_path.exists() and chapters:
        verdicts.append(
            evaluate_forward_state_contract(
                ledger_path,
                current_chapter=max(chapter.chapter_no for chapter in chapters),
            )
        )

    reveal_schedule_path = story_bible_dir / "reveal-schedule.yaml"
    if reveal_schedule_path.exists() and isinstance(chapter_contracts, Mapping):
        schedule = load_reveal_schedule(reveal_schedule_path)
        for key, item in sorted(
            chapter_contracts.items(),
            key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 0,
        ):
            if not isinstance(item, Mapping):
                continue
            outline = {"chapter_no": int(key) if str(key).isdigit() else 0, **dict(item)}
            verdicts.append(
                evaluate_outline_reveal_alignment(outline, reveal_schedule=schedule)
            )
    return tuple(verdicts)


def _outline_asset_gate_findings_to_commercial_issues(
    verdicts: Sequence[GateVerdict],
) -> tuple[CommercialGateIssue, ...]:
    issues: list[CommercialGateIssue] = []
    for verdict in verdicts:
        if verdict.verdict not in {"blocked", "error"} and not verdict.critical_count:
            continue
        for finding in verdict.findings:
            if finding.severity not in {"critical", "high"}:
                continue
            issues.append(
                CommercialGateIssue(
                    code=finding.code,
                    severity=finding.severity,
                    chapter_no=_chapter_no_from_gate_path(finding.path),
                    detail=finding.message,
                    suggestion=(
                        finding.repair_action
                        or "repair outline asset gate before approving this package"
                    ),
                    evidence={
                        "gate_name": verdict.gate_name,
                        "gate_verdict": verdict.verdict,
                        "gate_path": finding.path,
                    },
                )
            )
    return tuple(issues)


def _chapter_no_from_gate_path(path: str) -> int | None:
    match = re.search(r"(?:chapter-|chapter:?)([0-9]{1,4})", path or "")
    if match:
        return int(match.group(1))
    return None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _retention_findings_to_commercial_issues(
    findings: Sequence[GateFinding],
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    for finding in findings:
        issues.append(
            CommercialGateIssue(
                code=finding.code,
                severity=finding.severity,
                chapter_no=_chapter_no_from_gate_path(finding.path),
                detail=finding.message,
                suggestion=finding.repair_action,
                evidence={
                    "source_gate": "retention_onboarding_gate",
                    "path": finding.path,
                },
            )
        )
    return issues

def _load_chapters(root: Path) -> tuple[ChapterText, ...]:
    chapters: list[ChapterText] = []
    for path in sorted(root.glob("chapter-*.md")):
        match = re.search(r"chapter-(\d+)\.md$", path.name)
        if match is None:
            continue
        text = path.read_text(encoding="utf-8")
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        title = first_line.lstrip("#").strip()
        chapters.append(
            ChapterText(
                chapter_no=int(match.group(1)),
                title=title,
                text=text,
                path=path,
            )
        )
    return tuple(chapters)


def _load_story_context(root: Path) -> str:
    parts: list[str] = []
    for path in (
        root / "README.md",
        root / "listing" / "book-detail-page.md",
        root / "story-bible" / "series-brief.md",
        root / "story-bible" / "reader-desire-map.md",
        root / "story-bible" / "series-bible.md",
        root / "story-bible" / "continuity-ledger.md",
        root / "story-bible" / "volume-plan.csv",
        root / "story-bible" / "batch-queue.csv",
    ):
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _read_first_heading(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if cleaned.startswith("#"):
            return cleaned.lstrip("#").strip()
    return ""


def _infer_commercial_anchors(
    metadata: Mapping[str, Any],
    story_text: str,
) -> tuple[CommercialAnchor, ...]:
    contract = "\n".join(
        [
            str(metadata.get("primary_title") or ""),
            str(metadata.get("recommended_subtitle") or ""),
            str(metadata.get("logline") or ""),
            str(metadata.get("short_intro") or ""),
            " ".join(str(item) for item in metadata.get("tags", ()) or ()),
            " ".join(str(item) for item in metadata.get("reader_promise", ()) or ()),
            story_text,
        ]
    )
    anchors: list[CommercialAnchor] = []
    if "青囊" in contract or "秘卷" in contract:
        anchors.append(CommercialAnchor("core_artifact", ("青囊", "秘卷"), 6, 80, 3))
    if any(term in contract for term in ("否认", "认账", "入账")):
        terms = ["否认", "认账", "入账"]
        if "镜债" in contract or "困魂镜" in contract:
            terms.extend(["镜债", "承认", "替认", "偿"])
        anchors.append(CommercialAnchor("core_rule", tuple(terms), 5, 80, 4))
    if "困魂镜" in contract:
        anchors.append(CommercialAnchor("core_threat", ("困魂镜", "回执", "镜影"), 6, 80, 4))
    if "三族" in contract:
        anchors.append(
            CommercialAnchor("long_mystery", ("三族", "张家", "钱家", "林正淳"), 8, 80, 4)
        )
    if any(term in contract for term in ("风水", "罗盘", "阴阳眼", "重瞳", "验尸", "符纸")):
        anchors.append(
            CommercialAnchor(
                "profession_method",
                (
                    "风水",
                    "罗盘",
                    "阴阳眼",
                    "重瞳",
                    "验尸",
                    "符纸",
                    "镇魂",
                    "铜钱",
                    "方位",
                    "阴气",
                ),
                5,
                80,
                5,
            )
        )
    return tuple(anchors)


def _check_package_artifacts(
    root: Path,
    metadata: Mapping[str, Any],
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    required = (
        root / "story-bible" / "series-brief.md",
        root / "story-bible" / "reader-desire-map.md",
        root / "story-bible" / "series-bible.md",
        root / "story-bible" / "continuity-ledger.md",
        root / "story-bible" / "batch-queue.csv",
        root / "story-bible" / "volume-plan.csv",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if missing:
        issues.append(
            CommercialGateIssue(
                code="PROFESSIONAL_ARTIFACT_MISSING",
                severity="high",
                chapter_no=None,
                detail=f"Missing commercial planning artifacts: {', '.join(missing)}",
                suggestion=(
                    "补齐 series/reader/bible/continuity/batch/volume "
                    "规划文件后再继续生成。"
                ),
                evidence={"missing": missing},
            )
        )
    if not metadata.get("reader_promise"):
        issues.append(
            CommercialGateIssue(
                code="READER_CONTRACT_MISSING",
                severity="high",
                chapter_no=None,
                detail="Listing metadata has no reader_promise block.",
                suggestion="在上架资料中明确读者购买的体验承诺，并让生成 prompt 引用它。",
            )
        )
    if not chapters:
        issues.append(
            CommercialGateIssue(
                code="CHAPTERS_MISSING",
                severity="critical",
                chapter_no=None,
                detail="No chapter markdown files were found in the package root.",
                suggestion="先生成至少一批章节，再运行商业成熟度门禁。",
            )
        )
    return issues


def _check_planning_artifact_drift(
    root: Path,
    metadata: Mapping[str, Any],
    guardrails: CanonGuardrails,
    policy: CommercialGatePolicy,
) -> list[CommercialGateIssue]:
    """Catch contamination before it reaches chapter generation.

    Chapter gates are too late when the story bible, volume ladder, or batch
    queue already contains a deprecated world term or a genre vocabulary the
    listing explicitly rejects. Those artifacts are upstream of every prompt,
    so a single poisoned term can reproduce across an entire batch.
    """

    artifacts = _load_planning_artifact_texts(root)
    if not artifacts:
        return []

    issues: list[CommercialGateIssue] = []
    forbidden_hits: dict[str, list[str]] = {}
    for item in guardrails.forbidden_terms:
        term = item.term.strip()
        if not term:
            continue
        for rel_path, text in artifacts.items():
            if _count_unshielded_planning_term(text, term):
                forbidden_hits.setdefault(term, []).append(rel_path)
    if forbidden_hits:
        issues.append(
            CommercialGateIssue(
                code="PLANNING_ARTIFACT_CANON_LEAK",
                severity="critical",
                chapter_no=None,
                detail=(
                    "Planning artifacts contain deprecated canon terms: "
                    + ", ".join(sorted(forbidden_hits))
                ),
                suggestion=(
                    "先清理 story-bible / listing / batch 规划中的旧设定，"
                    "再允许生成章节；写前规划不能携带废稿体系。"
                ),
                evidence={"terms": forbidden_hits},
            )
        )

    not_recommended = " ".join(
        str(item) for item in metadata.get("not_recommended_categories", ()) or ()
    )
    if not any(
        marker in not_recommended
        for marker in policy.infinite_flow_not_recommended_markers
    ):
        return issues

    drift_hits: dict[str, dict[str, int]] = {}
    for rel_path, text in artifacts.items():
        for term in policy.infinite_flow_drift_terms:
            count = _count_unshielded_planning_term(text, term)
            if count:
                drift_hits.setdefault(term, {})[rel_path] = count
    if drift_hits:
        issues.append(
            CommercialGateIssue(
                code="PLANNING_ARTIFACT_GENRE_DRIFT",
                severity="high",
                chapter_no=None,
                detail=(
                    "Planning artifacts use infinite-flow/game vocabulary "
                    f"despite the listing rejecting that positioning: {drift_hits}."
                ),
                suggestion=(
                    "把规划层的 APP / 副本 / 玩家 / 游戏 表达替换为民俗悬疑语汇，"
                    "例如入局者、受困者、镜局、镜债、回执。"
                ),
                evidence={"term_locations": drift_hits},
            )
        )
    return issues


_NEGATED_PLANNING_TERM_MARKERS = (
    "forbidden",
    "forbid",
    "禁止",
    "禁用",
    "不得",
    "不能",
    "不要",
    "不可",
    "不应",
    "剥离",
    "改写",
    "替换",
    "清理",
    "旧设定",
    "废弃",
)


def _count_unshielded_planning_term(text: str, term: str) -> int:
    if not term:
        return 0
    count = 0
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return count
        if not _is_negated_planning_term_occurrence(text, index, len(term)):
            count += 1
        start = index + len(term)


def _is_negated_planning_term_occurrence(text: str, index: int, term_len: int) -> bool:
    window = text[max(0, index - 80) : min(len(text), index + term_len + 80)]
    return any(marker in window for marker in _NEGATED_PLANNING_TERM_MARKERS)


def _load_planning_artifact_texts(root: Path) -> dict[str, str]:
    candidates: list[Path] = [
        root / "README.md",
        root / "listing" / "book-detail-page.md",
        root / "listing" / "book-listing-metadata.json",
        root / "listing" / "title-candidates.csv",
    ]
    story_bible_dir = root / "story-bible"
    if story_bible_dir.exists():
        candidates.extend(
            path
            for path in story_bible_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".md", ".csv", ".json"}
            and path.name != "canon-guardrails.json"
        )

    texts: dict[str, str] = {}
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            texts[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return texts


def _check_golden_three(chapters: Sequence[ChapterText]) -> list[CommercialGateIssue]:
    chapter_texts = tuple((chapter.chapter_no, chapter.text) for chapter in chapters[:3])
    report = analyze_golden_three(chapter_texts=chapter_texts, language="zh-CN")
    issue_codes = tuple(report.issue_codes)
    if not issue_codes:
        return []

    suspense_fallback_applied = _has_serial_suspense_opening(chapters[:3])
    if suspense_fallback_applied:
        # Suspense openings can be compelling without power-fantasy hype words.
        # They still need active conflict and chapter-end pursuit hooks.
        issue_codes = tuple(code for code in issue_codes if code != "GOLDEN_THREE_LOW_HYPE")
    if not issue_codes:
        return []
    severity: GateSeverity = (
        "critical"
        if any(
            code in issue_codes
            for code in ("GOLDEN_THREE_WEAK_ENDING_HOOKS", "GOLDEN_THREE_WEAK_OPEN_CONFLICT")
        )
        else "high"
    )
    return [
        CommercialGateIssue(
            code="GOLDEN_THREE_COMMERCIAL_WEAK",
            severity=severity,
            chapter_no=None,
            detail=f"Golden-three opening issues: {', '.join(issue_codes)}",
            suggestion="前三章必须同时有钩子、冲突、短回报和章末追读理由；先修开篇再扩写后文。",
            evidence={
                "issue_codes": list(issue_codes),
                "original_issue_codes": list(report.issue_codes),
                "suspense_fallback_applied": suspense_fallback_applied,
                "strong_hype_chapters": report.strong_hype_chapters,
                "ending_hook_chapters": report.ending_hook_chapters,
            },
        )
    ]


_SERIAL_SUSPENSE_OPENING_TERMS = (
    "十五分钟",
    "凶宅",
    "子时",
    "镜",
    "尸体",
    "验尸",
    "焚尸",
    "灭口",
    "鬼魂",
    "重瞳",
    "符纸",
    "井底",
    "归字",
    "血字",
    "灰线",
    "规则",
    "青囊",
    "父亲",
    "母亲",
    "死",
    "失踪",
    "否认",
    "入账",
    "真相",
    "秘密",
)


def _has_serial_suspense_opening(chapters: Sequence[ChapterText]) -> bool:
    """Fallback for suspense openings that do not look like power-fantasy hype.

    ``reader_power.analyze_golden_three`` is intentionally broad and tuned for
    high-recognition commercial hype beats. Suspense/mystery openings often
    retain readers through rules, dread, questions, and information gaps
    instead. This fallback prevents the project gate from mislabeling a
    strong mystery opening as weak simply because it lacks upgrade/face-slap
    vocabulary.
    """

    if len(chapters) < 3:
        return False
    combined = "\n".join(chapter.text for chapter in chapters)
    distinct_terms = sum(1 for term in _SERIAL_SUSPENSE_OPENING_TERMS if term in combined)
    ending_hits = 0
    for chapter in chapters:
        tail = "\n".join(line.strip() for line in chapter.text.splitlines()[-8:] if line.strip())
        if any(term in tail for term in _SERIAL_SUSPENSE_OPENING_TERMS) or "？" in tail:
            ending_hits += 1
    return distinct_terms >= 6 and ending_hits >= 2


_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_FLOOR_RE = re.compile(r"(?<!栋)([0-9]+|[零一二两三四五六七八九十百]{1,4})(?:层|楼)")
_FLOOR_FALSE_POSITIVE_PREFIXES = (
    "一层磨砂",
    "一层薄",
    "一层雾",
    "一层灰",
    "一层纸",
    "一层皮",
    "一层水",
)
_OPENING_RESET_RE = re.compile(
    r"^\s*(?:"
    r"(?:[零一二两三四五六七八九十0-9]{1,4}[点时])|"
    r"子时|凌晨|午夜|清晨|傍晚|深夜|"
    r"三天前|十五分钟前|十一点|十七栋楼下"
    r")"
)


def _check_package_integrity(
    root: Path,
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    """Catch package-level prose integrity defects.

    These are the defects that make a file read like stitched drafts even when
    the right commercial keywords appear: contradictory space anchors, hard
    manuscript separators, early use of later-cast names, or chapter endings
    silently dropped by the next chapter opening.
    """

    issues: list[CommercialGateIssue] = []
    issues.extend(_check_location_anchor_conflicts(chapters))
    issues.extend(_check_stitched_opening_resets(chapters))
    issues.extend(_check_manuscript_stitch_markers(chapters))
    issues.extend(_check_early_cast_usage(root, chapters))
    issues.extend(_check_package_chapter_seams(chapters))
    return issues


def _parse_cjk_integer(raw: str) -> int | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if "百" in raw:
        left, _, right = raw.partition("百")
        hundreds = _CHINESE_DIGITS.get(left, 1 if not left else -1)
        if hundreds < 0:
            return None
        tail = _parse_cjk_integer(right) if right else 0
        return hundreds * 100 + (tail or 0)
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1 if not left else -1)
        ones = _CHINESE_DIGITS.get(right, 0 if not right else -1)
        if tens < 0 or ones < 0:
            return None
        return tens * 10 + ones
    if len(raw) == 1:
        return _CHINESE_DIGITS.get(raw)
    return None


def _snippet(text: str, start: int, *, radius: int = 42) -> str:
    left = max(0, start - radius)
    right = min(len(text), start + radius)
    return text[left:right].replace("\n", " ").strip()


def _check_location_anchor_conflicts(
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    for chapter in chapters:
        window = chapter.text[:1800] if chapter.chapter_no <= 3 else chapter.text[:900]
        floor_hits: dict[int, list[str]] = {}
        for match in _FLOOR_RE.finditer(window):
            if any(
                window[match.start() : match.start() + len(prefix)] == prefix
                for prefix in _FLOOR_FALSE_POSITIVE_PREFIXES
            ):
                continue
            number = _parse_cjk_integer(match.group(1))
            if number is None:
                continue
            floor_hits.setdefault(number, []).append(_snippet(window, match.start()))
        if len(floor_hits) < 2:
            continue
        severity: GateSeverity = "high" if chapter.chapter_no <= 3 else "medium"
        issues.append(
            CommercialGateIssue(
                code="CHAPTER_LOCATION_CONFLICT",
                severity=severity,
                chapter_no=chapter.chapter_no,
                detail=(
                    "Chapter opening contains multiple floor anchors: "
                    + ", ".join(str(item) for item in sorted(floor_hits))
                ),
                suggestion=(
                    "先锁定本章唯一空间入口；楼层、房号、门牌不得由不同草稿残片并存。"
                    "如果数字承担倒计时或旧账含义，应改写为非空间表述。"
                ),
                evidence={"floors": floor_hits},
            )
        )
    return issues


def _check_stitched_opening_resets(
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    for chapter in chapters:
        if chapter.chapter_no > 10:
            break
        offset = 0
        for line in chapter.text.splitlines():
            stripped = line.strip()
            if (
                80 <= offset <= 1600
                and stripped
                and not stripped.startswith("#")
                and _OPENING_RESET_RE.search(stripped)
            ):
                issues.append(
                    CommercialGateIssue(
                        code="CHAPTER_OPENING_RESET",
                        severity="high" if chapter.chapter_no <= 10 else "medium",
                        chapter_no=chapter.chapter_no,
                        detail=(
                            "Chapter appears to restart its opening after an active scene "
                            f"has already begun: {stripped[:80]}"
                        ),
                        suggestion=(
                            "把回忆、倒叙或地点切换改成明确桥段；若是旧稿残片，删除其中一套开场。"
                        ),
                        evidence={"offset": offset, "line": stripped},
                    )
                )
                break
            offset += len(line) + 1
    return issues


def _check_manuscript_stitch_markers(
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    marker_re = re.compile(r"(?m)^\s*---\s*$")
    for chapter in chapters:
        matches = list(marker_re.finditer(chapter.text))
        if not matches:
            continue
        issues.append(
            CommercialGateIssue(
                code="MANUSCRIPT_STITCH_MARKER",
                severity="medium",
                chapter_no=chapter.chapter_no,
                detail=(
                    "Chapter contains raw manuscript separator markers, "
                    "suggesting draft grafting."
                ),
                suggestion="用自然转场重写分隔处；如果两段来自不同版本，只保留正典事件链需要的一段。",
                evidence={
                    "markers": [
                        _snippet(chapter.text, match.start()) for match in matches[:5]
                    ]
                },
            )
        )
    return issues


def _cast_sections(root: Path) -> dict[str, str]:
    path = root / "story-bible" / "cast-and-promises.md"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current:
                sections[current] = "\n".join(buf)
            current = match.group(1).strip()
            buf = []
            continue
        if current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf)
    return sections


def _check_early_cast_usage(
    root: Path,
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    sections = _cast_sections(root)
    if not sections:
        return []
    usage_limits: dict[str, int] = {}
    for name, section in sections.items():
        match = re.search(r"第\s*(\d+)\s*章", section)
        has_explicit_later_use = bool(
            re.search(r"第\s*\d+\s*章\s*(?:之后|以后|后才|后可)", section)
        )
        if match and ("旧账名" in section or has_explicit_later_use):
            usage_limits[name] = int(match.group(1))
    if not usage_limits:
        return []

    issues: list[CommercialGateIssue] = []
    for chapter in chapters:
        for name, allowed_from in usage_limits.items():
            if chapter.chapter_no >= allowed_from or name not in chapter.text:
                continue
            idx = chapter.text.find(name)
            issues.append(
                CommercialGateIssue(
                    code="CAST_NAME_EARLY_USE",
                    severity="high" if chapter.chapter_no <= 10 else "medium",
                    chapter_no=chapter.chapter_no,
                    detail=(
                        f"Cast item '{name}' is planned for chapter {allowed_from} "
                        f"or later, but appears in chapter {chapter.chapter_no}."
                    ),
                    suggestion=(
                        "把该名字改回当前正典人物，或把它降级为不可行动的账页/旧名线索；"
                        "黄金三章不得让后期账名作为现场人物抢焦点。"
                    ),
                    evidence={
                        "name": name,
                        "allowed_from_chapter": allowed_from,
                        "snippet": _snippet(chapter.text, idx),
                    },
                )
            )
    return issues


def _check_package_chapter_seams(
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    try:
        from bestseller.services.chapter_seam import validate_chapter_seam
    except Exception:
        return []

    issues: list[CommercialGateIssue] = []
    for previous, current in pairwise(chapters):
        prev_tail = previous.text[-900:]
        current_opening = current.text[:900]
        if len(prev_tail.strip()) < 700 or len(current_opening.strip()) < 500:
            continue
        report = validate_chapter_seam(prev_tail, current_opening)
        if report.score >= 0.5:
            continue
        issues.append(
            CommercialGateIssue(
                code="CHAPTER_SEAM_SILENT_DROP",
                severity="high" if current.chapter_no <= 10 else "medium",
                chapter_no=current.chapter_no,
                detail=(
                    f"Chapter {current.chapter_no} opening resolves only "
                    f"{report.score:.0%} of chapter {previous.chapter_no} ending threads."
                ),
                suggestion=(
                    "下一章开头必须先承接上一章最后的地点、人物、威胁或未答问题；"
                    "禁止直接跳到新案、新空间或新规则。"
                ),
                evidence={
                    "previous_chapter": previous.chapter_no,
                    "current_chapter": current.chapter_no,
                    "score": round(report.score, 3),
                    "silent_drops": [
                        {
                            "kind": drop.thread.kind.value,
                            "marker": drop.thread.marker,
                            "evidence": drop.thread.evidence,
                        }
                        for drop in report.silent_drops[:8]
                    ],
                },
            )
        )
    return issues


def _check_canon_guardrails(
    chapters: Sequence[ChapterText],
    guardrails: CanonGuardrails,
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    forbidden_hits: dict[str, dict[str, Any]] = {}
    for chapter in chapters:
        for item in guardrails.forbidden_terms:
            if item.term and item.term in chapter.text:
                entry = forbidden_hits.setdefault(
                    item.term,
                    {
                        "count": 0,
                        "first_chapter": chapter.chapter_no,
                        "reason": item.reason,
                        "suggestion": item.suggestion,
                    },
                )
                entry["count"] += chapter.text.count(item.term)
    if forbidden_hits:
        terms = ", ".join(sorted(forbidden_hits))
        issues.append(
            CommercialGateIssue(
                code="CANON_FORBIDDEN_TERM",
                severity="critical",
                chapter_no=min(v["first_chapter"] for v in forbidden_hits.values()),
                detail=f"Deprecated or foreign canon terms leaked into chapters: {terms}",
                suggestion=(
                    "停止沿用这些章节作为正典；从最后一个干净章节重新生成，"
                    "并让门禁在写入前阻断。"
                ),
                evidence=forbidden_hits,
            )
        )

    for chapter in chapters:
        for rule in guardrails.state_rules:
            if (
                rule.applies_after_chapter is not None
                and chapter.chapter_no <= rule.applies_after_chapter
            ):
                continue
            for pattern in rule.forbidden_patterns:
                match = _safe_search(pattern, chapter.text)
                if match is None:
                    continue
                issues.append(
                    CommercialGateIssue(
                        code="CANON_STATE_REGRESSION",
                        severity="critical",
                        chapter_no=chapter.chapter_no,
                        detail=(
                            f"Canon state regression for {rule.subject}: "
                            f"matched pattern {pattern!r}"
                        ),
                        suggestion=(
                            "必须从 continuity/event-state ledger 的当前状态继续，"
                            "不得把已完成的死亡、救援、离局或身份关系重置。"
                        ),
                        evidence={
                            "subject": rule.subject,
                            "status": rule.status,
                            "matched": match.group(0)[:120],
                        },
                    )
                )
                return issues
    return issues


def _safe_search(pattern: str, text: str) -> re.Match[str] | None:
    try:
        return re.search(pattern, text, flags=re.DOTALL)
    except re.error:
        return re.search(re.escape(pattern), text, flags=re.DOTALL)


def _check_reader_contract(
    chapters: Sequence[ChapterText],
    policy: CommercialGatePolicy,
) -> list[CommercialGateIssue]:
    issues: list[CommercialGateIssue] = []
    if not policy.anchors or not chapters:
        return issues
    for anchor in policy.anchors:
        considered = [
            chapter
            for chapter in chapters
            if anchor.required_until_chapter is None
            or chapter.chapter_no <= anchor.required_until_chapter
        ]
        counts = [
            (chapter.chapter_no, _count_terms(chapter.text, anchor.terms))
            for chapter in considered
        ]
        total_hits = sum(count for _, count in counts)
        gap_start, gap_end, gap_len = _longest_zero_gap(counts)
        if total_hits < anchor.min_total_hits or gap_len > anchor.max_gap_chapters:
            issues.append(
                CommercialGateIssue(
                    code="READER_CONTRACT_GAP",
                    severity="high",
                    chapter_no=gap_start,
                    detail=(
                        f"Reader-contract anchor '{anchor.key}' disappeared for "
                        f"{gap_len} chapters; terms={anchor.terms}, total_hits={total_hits}."
                    ),
                    suggestion=(
                        "把该锚点写入批次目标和下一章 prompt；每个窗口都要有可见兑现，"
                        "否则读者会觉得书换了类型。"
                    ),
                    evidence={
                        "anchor": anchor.key,
                        "terms": list(anchor.terms),
                        "gap": [gap_start, gap_end],
                        "total_hits": total_hits,
                    },
                )
            )
    return issues


def _count_terms(text: str, terms: Sequence[str]) -> int:
    return sum(text.count(term) for term in terms if term)


def _longest_zero_gap(counts: Sequence[tuple[int, int]]) -> tuple[int | None, int | None, int]:
    best_start: int | None = None
    best_end: int | None = None
    best_len = 0
    cur_start: int | None = None
    cur_end: int | None = None
    cur_len = 0
    for chapter_no, count in counts:
        if count == 0:
            if cur_start is None:
                cur_start = chapter_no
            cur_end = chapter_no
            cur_len += 1
        else:
            if cur_len > best_len:
                best_start, best_end, best_len = cur_start, cur_end, cur_len
            cur_start = None
            cur_end = None
            cur_len = 0
    if cur_len > best_len:
        best_start, best_end, best_len = cur_start, cur_end, cur_len
    return best_start, best_end, best_len


def _check_genre_contract(
    chapters: Sequence[ChapterText],
    metadata: Mapping[str, Any],
    policy: CommercialGatePolicy,
) -> list[CommercialGateIssue]:
    not_recommended = " ".join(
        str(item) for item in metadata.get("not_recommended_categories", ()) or ()
    )
    if not any(
        marker in not_recommended
        for marker in policy.infinite_flow_not_recommended_markers
    ):
        return []
    drift: dict[str, int] = {}
    chapters_hit: set[int] = set()
    for chapter in chapters:
        hits = _count_terms(chapter.text, policy.infinite_flow_drift_terms)
        if hits:
            chapters_hit.add(chapter.chapter_no)
        for term in policy.infinite_flow_drift_terms:
            count = chapter.text.count(term)
            if count:
                drift[term] = drift.get(term, 0) + count
    total = sum(drift.values())
    if total < 12 and len(chapters_hit) < 5:
        return []
    return [
        CommercialGateIssue(
            code="GENRE_CONTRACT_DRIFT",
            severity="critical",
            chapter_no=min(chapters_hit) if chapters_hit else None,
            detail=(
                "The book is marked as not pure infinite-flow, but generated chapters "
                f"lean on infinite-flow vocabulary {drift}."
            ),
            suggestion="回到民俗悬疑/风水破局表达；禁用 APP、副本、玩家、游戏化副本框架。",
            evidence={"term_counts": drift, "chapters": sorted(chapters_hit)},
        )
    ]


def _check_batch_queue_alignment(
    root: Path,
    chapters: Sequence[ChapterText],
) -> list[CommercialGateIssue]:
    path = root / "story-bible" / "batch-queue.csv"
    if not path.exists() or not chapters:
        return []
    rows = _read_csv_dicts(path)
    text_by_chapter = {chapter.chapter_no: chapter.text for chapter in chapters}
    issues: list[CommercialGateIssue] = []
    for row in rows:
        start, end = _parse_range(str(row.get("chapters") or ""))
        if start is None or end is None:
            continue
        required_numbers = set(range(start, end + 1))
        existing_numbers = required_numbers.intersection(text_by_chapter)
        if existing_numbers != required_numbers:
            continue
        existing = [text_by_chapter[n] for n in range(start, end + 1)]
        if not existing:
            continue
        window_text = "\n".join(existing)
        missing_callbacks: list[str] = []
        for callback in _split_callbacks(str(row.get("required_callbacks") or "")):
            if not _callback_present(callback, window_text):
                missing_callbacks.append(callback)
        if missing_callbacks:
            issues.append(
                CommercialGateIssue(
                    code="BATCH_MISSION_MISSING_CALLBACK",
                    severity="medium",
                    chapter_no=start,
                    detail=(
                        f"Batch {row.get('batch')} is missing required callbacks: "
                        f"{', '.join(missing_callbacks)}"
                    ),
                    suggestion=(
                        "把 batch-queue 的 required_callbacks 注入每章计划，"
                        "并在批次结束时校验回调是否落地。"
                    ),
                    evidence={
                        "batch": row.get("batch"),
                        "chapters": row.get("chapters"),
                        "missing_callbacks": missing_callbacks,
                    },
                )
            )
    return issues


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _parse_range(raw: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", raw)
    if match:
        return int(match.group(1)), int(match.group(2))
    try:
        value = int(raw)
    except ValueError:
        return None, None
    return value, value


def _split_callbacks(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in re.split(r"[;；,，]", raw) if item.strip())


def _callback_present(callback: str, text: str) -> bool:
    if callback in text:
        return True
    term_groups = _callback_term_groups(callback)
    if not term_groups:
        return False
    required = min(2, len(term_groups))
    matched = sum(1 for group in term_groups if any(term in text for term in group))
    return matched >= required


_CALLBACK_TOKEN_BANK = (
    "十五分钟",
    "委托",
    "否认",
    "小镜子",
    "镜子",
    "聊天记录",
    "入镜",
    "回执",
    "手机屏幕",
    "手机",
    "外扩",
    "镜影",
    "开门",
    "旧照",
    "临死话",
)


def _callback_terms(callback: str) -> tuple[str, ...]:
    raw_terms = [term for term in re.split(r"[ /、·:：]", callback) if len(term) >= 2]
    bank_terms = [term for term in _CALLBACK_TOKEN_BANK if term in callback]
    terms = raw_terms + bank_terms
    deduped: list[str] = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return tuple(deduped)


_CALLBACK_ALIASES: dict[str, tuple[str, ...]] = {
    "回执": ("回执", "回执镜片", "小圆镜"),
    "临死话": ("临死话", "临死前", "临死前留了一句话", "遗言"),
}


def _callback_term_groups(callback: str) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for term in _callback_terms(callback):
        aliases = _CALLBACK_ALIASES.get(term, (term,))
        group = tuple(dict.fromkeys(alias for alias in aliases if alias))
        if group and group not in seen:
            groups.append(group)
            seen.add(group)
    return tuple(groups)


def _check_premature_payoff(
    root: Path,
    chapters: Sequence[ChapterText],
    policy: CommercialGatePolicy,
) -> list[CommercialGateIssue]:
    volume_end = _first_volume_end(root / "story-bible" / "volume-plan.csv")
    if volume_end is None:
        volume_end = 80
    cutoff = max(1, int(volume_end * policy.premature_payoff_ratio))
    hits: dict[int, list[str]] = {}
    for chapter in chapters:
        if chapter.chapter_no > cutoff:
            continue
        haystack = f"{chapter.title}\n{chapter.text}"
        terms = [term for term in policy.premature_payoff_terms if term in haystack]
        if terms:
            hits[chapter.chapter_no] = terms
    if not hits:
        return []
    has_endgame_term = any(
        "归墟之主" in terms or "真正敌人" in terms
        for terms in hits.values()
    )
    severity: GateSeverity = "critical" if has_endgame_term else "high"
    return [
        CommercialGateIssue(
            code="PREMATURE_MAJOR_PAYOFF",
            severity=severity,
            chapter_no=min(hits),
            detail=(
                f"Major-payoff or endgame terms appear before chapter {cutoff}: {hits}."
            ),
            suggestion="把终局级真相、破镜和本源揭露后移；前 80 章只兑现阶段性小闭环。",
            evidence={"cutoff": cutoff, "hits": hits},
        )
    ]


def _first_volume_end(path: Path) -> int | None:
    rows = _read_csv_dicts(path)
    if not rows:
        return None
    _, end = _parse_range(str(rows[0].get("chapters") or ""))
    return end


def _check_length_stability(
    chapters: Sequence[ChapterText],
    policy: CommercialGatePolicy,
) -> list[CommercialGateIssue]:
    lengths = [chapter.body_chars for chapter in chapters if chapter.body_chars > 0]
    if len(lengths) < 3:
        return []
    mean = statistics.fmean(lengths)
    cv = statistics.pstdev(lengths) / mean if mean else 0.0
    if cv < policy.length_cv_warn:
        return []
    severity: GateSeverity = "high" if cv >= policy.length_cv_fail else "medium"
    return [
        CommercialGateIssue(
            code="SERIAL_LENGTH_INSTABILITY",
            severity=severity,
            chapter_no=None,
            detail=f"Chapter length coefficient of variation is {cv:.3f}.",
            suggestion="统一批次字数目标；章节扩写不能从第 13 章突然翻倍，否则节奏和成本都会失控。",
            evidence={
                "mean": round(mean, 2),
                "cv": round(cv, 4),
                "min": min(lengths),
                "max": max(lengths),
            },
        )
    ]


def _score_issues(issues: Sequence[CommercialGateIssue]) -> int:
    penalties = {"critical": 18, "high": 10, "medium": 4, "low": 1}
    score = 100 - sum(penalties[issue.severity] for issue in issues)
    return max(0, min(100, score))


def _commercial_gate_closure_plan(report: CommercialGateReport) -> dict[str, Any]:
    blocking_severities = {
        str(item)
        for item in report.metrics.get(
            "blocking_severities",
            ("critical", "high", "medium"),
        )
    }
    blocking_issues = [
        issue for issue in report.issues if issue.severity in blocking_severities
    ]
    rerun_scopes = list(
        dict.fromkeys(issue.closure.rerun_scope for issue in blocking_issues)
    )
    return {
        "required": not report.passed,
        "policy": (
            "Every blocking issue must define immediate repair, recurrence "
            "prevention, and verification before the package can be promoted."
        ),
        "blocking_issue_count": len(blocking_issues),
        "rerun_scopes": rerun_scopes,
        "final_verification": (
            "Rerun commercial-gate package after targeted repairs and promote only "
            "when passed=true, score meets the professional threshold, and "
            "blocking_issue_counts is empty."
        ),
    }


def _closure_for_commercial_issue(issue: CommercialGateIssue) -> CommercialIssueClosure:
    code = issue.code
    chapter = f"第{issue.chapter_no}章" if issue.chapter_no else "对应范围"
    generic = CommercialIssueClosure(
        immediate_repair=f"按门禁建议修复{chapter}，保留现有正典和章节状态，不做无关改写。",
        recurrence_prevention=(
            "把本次命中的 code 加入后续章节的 prewrite constraints，要求写前 plan "
            "显式规避同类问题。"
        ),
        verification=(
            "重跑对应章节 pipeline/review，再重跑 commercial-gate package，确认该 code "
            "不再出现。"
        ),
        rerun_scope=f"chapter:{issue.chapter_no}" if issue.chapter_no else "book",
    )
    closures: dict[str, CommercialIssueClosure] = {
        "GOLDEN_THREE_COMMERCIAL_WEAK": CommercialIssueClosure(
            immediate_repair=(
                "重做前三章规划和正文：第一章必须有当场冲突、主角主动选择、失败代价；"
                "第二三章必须递进而不是解释设定。"
            ),
            recurrence_prevention=(
                "在前三章规划阶段强制运行 golden-three readiness；未达到开篇冲突、"
                "短回报、章末追读目标前禁止进入正文生成。"
            ),
            verification=(
                "重跑前三章常识门禁、golden-three 分析和整包 commercial gate；"
                "golden issue_codes 必须为空。"
            ),
            rerun_scope="chapters:1-3",
        ),
        "CHAPTER_OPENING_RESET": CommercialIssueClosure(
            immediate_repair=(
                f"重写{chapter}开头 1600 字内的转场：删除第二套开场，"
                "把倒叙/回忆改成明确桥段，保证场景连续推进。"
            ),
            recurrence_prevention=(
                "把 opening reset 检查前置到章节晋级门禁；正文前 1600 字出现新的时间/"
                "地点起笔时，要求有桥接句和明确因果。"
            ),
            verification=(
                f"重跑{chapter}章节 review 和 commercial gate，确认 CHAPTER_OPENING_RESET "
                "消失且章节仍有尾钩。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "MANUSCRIPT_STITCH_MARKER": CommercialIssueClosure(
            immediate_repair=(
                f"清理{chapter}中的草稿分隔符和拼接残片，合并成单一连续场景。"
            ),
            recurrence_prevention=(
                "导出前增加 manuscript hygiene 检查；任何原始分隔符、元标记、拼稿残留"
                "都不得进入可发布稿。"
            ),
            verification=(
                "重跑 manuscript/package integrity gate，确认 MANUSCRIPT_STITCH_MARKER "
                "不再出现。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "CANON_FORBIDDEN_TERM": CommercialIssueClosure(
            immediate_repair=(
                f"重写{chapter}中命中的废弃正典词，替换为当前书的合法人物、势力或规则名。"
            ),
            recurrence_prevention=(
                "把命中词加入该书 constraint manifest 的 forbidden_terms，并在写前计划"
                "要求模型声明不会使用。"
            ),
            verification=(
                "重跑 canon guardrails、章节常识门禁和整包 commercial gate，确认 forbidden "
                "term 不再泄漏。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "CANON_STATE_REGRESSION": CommercialIssueClosure(
            immediate_repair=(
                f"重写{chapter}中回退到旧状态的段落；以最新 state snapshot / canon "
                "guardrails 为准，不允许复活、解封、倒退或重新引入已废弃状态。"
            ),
            recurrence_prevention=(
                "把本章后的 state snapshot 作为下一章硬约束；写前计划必须声明继承状态，"
                "正文晋级前再跑状态回归检查。"
            ),
            verification=(
                "重跑 canon state regression、章节 review 和整包 commercial gate，确认状态"
                "回退 code 清零。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "CAST_NAME_EARLY_USE": CommercialIssueClosure(
            immediate_repair=(
                f"删除或改写{chapter}中过早出现的后期角色名；必要时只保留物件、账页或传闻。"
            ),
            recurrence_prevention=(
                "从 cast-and-promises 编译每章 allowed_characters / must_not_appear 白名单，"
                "生成前先验计划，生成后再扫正文。"
            ),
            verification=(
                "重跑 cast/canon/commercial gate，确认对应角色未在允许章节前真人出场。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "CHAPTER_LOCATION_CONFLICT": CommercialIssueClosure(
            immediate_repair=(
                f"统一{chapter}开篇空间锚点，删除互相冲突的楼层、房号或地点残片。"
            ),
            recurrence_prevention=(
                "把本章唯一入口地点写进 allowed_locations；写前计划不得声明第二个入口空间。"
            ),
            verification=(
                "重跑 location/package integrity gate，确认开篇不再出现冲突空间锚。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "GENRE_CONTRACT_DRIFT": CommercialIssueClosure(
            immediate_repair=(
                "重写漂移章节，把核心卖点、题材词、主角能力和读者承诺拉回当前书类型。"
            ),
            recurrence_prevention=(
                "把 listing、reader promise、genre anchors 注入每章 plan；低命中时禁止生成正文。"
            ),
            verification=(
                "重跑 genre contract 和 reader contract gate，确认漂移 code 清零。"
            ),
            rerun_scope="book",
        ),
        "READER_CONTRACT_GAP": CommercialIssueClosure(
            immediate_repair=(
                "补回缺失的核心读者承诺：规则、能力使用、反制、短回报必须在窗口内出现。"
            ),
            recurrence_prevention=(
                "把 reader contract anchors 转成跨章 recurring obligation；"
                "超过窗口前自动插入章节目标。"
            ),
            verification=(
                "重跑 reader contract gate，确认 anchor gap 和缺失回报全部清零。"
            ),
            rerun_scope="book",
        ),
        "PREMATURE_MAJOR_PAYOFF": CommercialIssueClosure(
            immediate_repair=(
                "撤回过早释放的大真相/终局词，把它改成局部线索或误导性小回报。"
            ),
            recurrence_prevention=(
                "把 major payoff 的 due chapter 写入状态机；未到窗口禁止使用终局词和终极解释。"
            ),
            verification=(
                "重跑 premature payoff gate，确认开篇没有提前消耗第一卷核心悬念。"
            ),
            rerun_scope="book",
        ),
        "ONBOARDING_OVERLOAD": CommercialIssueClosure(
            immediate_repair=(
                "重做开篇名词预算：每章只保留当场冲突必需的新人物、物件和规则，"
                "把次要新人物、专有规则名词等后置。"
            ),
            recurrence_prevention=(
                "维护 story-bible/canonical-terms.yaml，并在写前 plan 输出本章新增名词清单。"
            ),
            verification=(
                "重跑 retention gate，确认 chapters 1-2/1-5/1-10 的新增名词数低于阈值。"
            ),
            rerun_scope="chapters:1-10",
        ),
        "TIME_ANCHOR_BACKWARDS": CommercialIssueClosure(
            immediate_repair=(
                f"统一{chapter}及前后章的时钟锚点；若不是倒叙，后一章时间必须晚于上一章。"
            ),
            recurrence_prevention=(
                "把 clock state 写入 prewrite contract，下一章开头必须继承上一章尾部时间。"
            ),
            verification=(
                "重跑 time-anchor monotonicity 和整包 commercial gate，确认时钟不再倒退。"
            ),
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "BEAT_DENSITY_OVERLOAD": CommercialIssueClosure(
            immediate_repair=(
                f"拆解{chapter}的主任务，只保留一个主冲突、一个短回报和一个尾钩。"
            ),
            recurrence_prevention=(
                "写前大纲必须列出 beat_count；超过阈值时先拆章或顺延新增人物/地点。"
            ),
            verification="重跑 retention gate，确认 BEAT_DENSITY_OVERLOAD 消失。",
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "PREMATURE_REVEAL": CommercialIssueClosure(
            immediate_repair=(
                f"撤回{chapter}中过早出现的终局词或核心机制名，改成局部物证/误导线索。"
            ),
            recurrence_prevention=(
                "维护 story-bible/reveal-schedule.yaml；生成前后均按 earliest_chapter 扫描。"
            ),
            verification="重跑 premature reveal gate，确认 reveal floor 之前没有命中词。",
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "HOOK_TOO_ABSTRACT": CommercialIssueClosure(
            immediate_repair=(
                f"重写{chapter}章尾 200 字，至少落到具体人、物、地点、倒计时中的两个。"
            ),
            recurrence_prevention=(
                "章节计划必须声明尾钩的具象元素，不能只写恐惧、命运、真相等抽象词。"
            ),
            verification="重跑 hook specificity gate，确认章尾具象度达标。",
            rerun_scope=f"chapter:{issue.chapter_no}",
        ),
        "OPENING_PATTERN_OVERUSED": CommercialIssueClosure(
            immediate_repair=(
                "调整命中窗口内的开篇形态，避免连续用同一种时间、物证或人物动作起笔。"
            ),
            recurrence_prevention=(
                "批次大纲增加 opening_mode 字段，6 章窗口内同类开篇不得超过 2 次。"
            ),
            verification="重跑 opening repetition gate，确认窗口内重复模式低于阈值。",
            rerun_scope="chapters:window",
        ),
        "PLANNING_ARTIFACT_GENRE_DRIFT": CommercialIssueClosure(
            immediate_repair=(
                "修正 story-bible/listing/volume-plan 中漂移到异题材的词，再据此重跑受影响章节。"
            ),
            recurrence_prevention=(
                "规划材料变更后先跑 planning artifact drift gate；失败时禁止进入正文生成。"
            ),
            verification=(
                "重跑 commercial planning 和 package gate，确认规划材料与成稿题材一致。"
            ),
            rerun_scope="planning",
        ),
    }
    return closures.get(code, generic)


def _issue_counts(issues: Sequence[CommercialGateIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return counts


__all__ = [
    "CommercialAnchor",
    "CommercialGateIssue",
    "CommercialGatePolicy",
    "CommercialGateReport",
    "commercial_gate_report_to_dict",
    "evaluate_book_package",
]

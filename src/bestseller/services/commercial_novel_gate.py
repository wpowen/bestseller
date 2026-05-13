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
import json
from pathlib import Path
import re
import statistics
from typing import Any, Literal

from bestseller.services.canon_guardrails import (
    CanonGuardrails,
    load_canon_guardrails_file,
)
from bestseller.services.reader_power import analyze_golden_three

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
    min_professional_score: int = 75
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
    anchors: tuple[CommercialAnchor, ...] = ()


@dataclass(frozen=True)
class CommercialGateIssue:
    code: str
    severity: GateSeverity
    chapter_no: int | None
    detail: str
    suggestion: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


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
    issues.extend(_check_canon_guardrails(chapters, guardrails))
    issues.extend(_check_reader_contract(chapters, effective_policy))
    issues.extend(_check_genre_contract(chapters, metadata, effective_policy))
    issues.extend(_check_batch_queue_alignment(root, chapters))
    issues.extend(_check_premature_payoff(root, chapters, effective_policy))
    issues.extend(_check_length_stability(chapters, effective_policy))

    score = _score_issues(issues)
    passed = score >= effective_policy.min_professional_score and not any(
        issue.severity == "critical" for issue in issues
    )
    metrics = {
        "anchor_groups": [
            {"key": anchor.key, "terms": list(anchor.terms), "max_gap": anchor.max_gap_chapters}
            for anchor in effective_policy.anchors
        ],
        "issue_counts": _issue_counts(issues),
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
    return {
        "book_id": report.book_id,
        "title": report.title,
        "total_chapters": report.total_chapters,
        "overall_score": report.overall_score,
        "passed": report.passed,
        "metrics": dict(report.metrics),
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "chapter_no": issue.chapter_no,
                "detail": issue.detail,
                "suggestion": issue.suggestion,
                "evidence": dict(issue.evidence),
            }
            for issue in report.issues
        ],
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


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
            if term in text:
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
            count = text.count(term)
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
    "老张",
    "张建军",
    "否认",
    "小雨",
    "小镜子",
    "镜子",
    "周雪",
    "聊天记录",
    "陈默",
    "入镜",
    "王老板",
    "王建业",
    "回执",
    "手机屏幕",
    "手机",
    "外扩",
    "镜影",
    "林渊",
    "张家",
    "开门",
    "林正淳",
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
    "王老板": ("王老板", "王建业"),
    "王建业": ("王建业", "王老板"),
    "回执": ("回执", "回执镜片", "小圆镜"),
    "老张": ("老张", "张建军"),
    "张建军": ("张建军", "老张"),
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

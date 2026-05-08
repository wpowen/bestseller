"""Audit structural integrity for 《道种破虚》 chapters 51-550.

This is a read-only repair-prep scan. It does not rewrite prose or mutate DB
rows. The report identifies deterministic issues that must be fixed before the
book resumes generation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.continuity import _parse_time_anchor  # noqa: E402
from bestseller.services.deduplication import (  # noqa: E402
    check_hook_repetition,
    compute_jaccard_similarity,
    detect_intra_chapter_repetition,
)
from bestseller.services.drafts import count_words  # noqa: E402
from bestseller.services.plan_fingerprint import (  # noqa: E402
    build_chapter_fingerprint,
    find_near_duplicate_chapters,
)
from bestseller.settings import get_settings  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
DEFAULT_START = 51
DEFAULT_END = 550
REPORT_DIR = Path("artifacts/daozhong_repair_audit")

GENERIC_TIME_LABELS = {
    "章节开场",
    "章节中段",
    "章节结尾",
    "章节补充钩子",
}
GENERIC_STORY_PURPOSES = {
    "承接本章主线，补足场景推进、信息释放与结尾钩子。",
    "推动本章剧情发展",
}
GENERIC_STORY_PURPOSE_MARKERS = (
    "承接上章后果并明确本章行动目标",
    "用更深一层的代价、真相或变化把局势再往前推",
    "具体事件是「开场」",
    "具体事件是「推进」",
    "具体事件是「尾钩」",
)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    chapter: int | None = None
    scene: int | None = None
    message: str = ""
    recommendation: str = ""
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metadata"] = self.metadata or {}
        return data


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value).strip().lower())


def _short(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _story_purpose(scene: SceneCardModel) -> str:
    purpose = scene.purpose if isinstance(scene.purpose, dict) else {}
    value = purpose.get("story")
    return value.strip() if isinstance(value, str) else ""


def _is_generic_time_label(value: str | None) -> bool:
    label = (value or "").strip()
    if not label:
        return True
    if label in GENERIC_TIME_LABELS:
        return True
    return bool(re.fullmatch(r"章节场景\d+", label))


def _is_generic_story_purpose(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if text in GENERIC_STORY_PURPOSES:
        return True
    if any(marker in text for marker in GENERIC_STORY_PURPOSE_MARKERS):
        return True
    return len(text) < 12


def _is_flashback_anchor(anchor: str | None) -> bool:
    text = (anchor or "").lower()
    return any(token in text for token in ("flashback", "回忆", "倒叙", "插叙", "梦境"))


def _chapter_plan_like(chapter: ChapterModel, scenes: list[SceneCardModel]) -> dict[str, Any]:
    return {
        "chapter_number": chapter.chapter_number,
        "main_conflict": chapter.main_conflict,
        "hook_type": chapter.hook_type,
        "hook_description": chapter.hook_description,
        "chapter_goal": chapter.chapter_goal,
        "scenes": scenes,
    }


async def audit(*, start: int, end: int, output_dir: Path, skip_prose: bool = False) -> dict[str, Any]:
    findings: list[Finding] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    async with session_scope() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        chapters = list(
            await session.scalars(
                select(ChapterModel)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number >= start,
                    ChapterModel.chapter_number <= end,
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        )
        chapter_by_id = {chapter.id: chapter for chapter in chapters}
        chapter_numbers = [chapter.chapter_number for chapter in chapters]

        scenes = list(
            await session.scalars(
                select(SceneCardModel)
                .where(SceneCardModel.chapter_id.in_(list(chapter_by_id)))
                .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
            )
        )
        scenes_by_chapter: dict[Any, list[SceneCardModel]] = defaultdict(list)
        for scene in scenes:
            scenes_by_chapter[scene.chapter_id].append(scene)

        chapter_drafts = {
            draft.chapter_id: draft
            for draft in await session.scalars(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id.in_(list(chapter_by_id)),
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
        }
        scene_drafts = {
            draft.scene_card_id: draft
            for draft in await session.scalars(
                select(SceneDraftVersionModel).where(
                    SceneDraftVersionModel.scene_card_id.in_([scene.id for scene in scenes]),
                    SceneDraftVersionModel.is_current.is_(True),
                )
            )
        }
        snapshots = {
            snapshot.chapter_number: snapshot
            for snapshot in await session.scalars(
                select(ChapterStateSnapshotModel)
                .where(
                    ChapterStateSnapshotModel.project_id == project.id,
                    ChapterStateSnapshotModel.chapter_number >= start,
                    ChapterStateSnapshotModel.chapter_number <= end,
                )
                .order_by(ChapterStateSnapshotModel.chapter_number.asc())
            )
        }

        # Coverage and lifecycle state.
        expected_numbers = set(range(start, end + 1))
        missing_chapter_numbers = sorted(expected_numbers - set(chapter_numbers))
        for chapter_number in missing_chapter_numbers:
            findings.append(
                Finding(
                    code="MISSING_CHAPTER_ROW",
                    severity="critical",
                    chapter=chapter_number,
                    message=f"第{chapter_number}章缺少 ChapterModel 行。",
                    recommendation="先补齐章节规划行，再考虑继续生成。",
                )
            )

        word_budget = get_settings().generation.words_per_chapter
        min_words = int(word_budget.min)
        max_words = int(word_budget.max)

        for chapter in chapters:
            draft = chapter_drafts.get(chapter.id)
            if chapter.id not in chapter_drafts:
                findings.append(
                    Finding(
                        code="MISSING_CURRENT_CHAPTER_DRAFT",
                        severity="critical",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章没有 current ChapterDraft。",
                        recommendation="不能作为可读成书章节；需重新 assemble 或重写该章。",
                    )
                )
            elif draft is not None:
                actual_word_count = count_words(draft.content_md or "")
                effective_chars = len(re.sub(r"\s+", "", draft.content_md or ""))
                stored_chapter_wc = int(chapter.current_word_count or 0)
                stored_draft_wc = int(draft.word_count or 0)
                if (
                    actual_word_count < min_words
                    or actual_word_count > max_words
                    or effective_chars < min_words
                    or effective_chars > max_words
                ):
                    findings.append(
                        Finding(
                            code="CURRENT_CHAPTER_LENGTH_OUT_OF_RANGE",
                            severity="critical",
                            chapter=chapter.chapter_number,
                            message=(
                                f"第{chapter.chapter_number}章 current draft 长度不在 "
                                f"{min_words}-{max_words} 范围内："
                                f"count_words={actual_word_count}, effective_chars={effective_chars}。"
                            ),
                            recommendation="重新 assemble/rewrite；不能只依赖 chapter.current_word_count。",
                            metadata={
                                "actual_word_count": actual_word_count,
                                "effective_chars": effective_chars,
                                "chapter_current_word_count": stored_chapter_wc,
                                "draft_word_count": stored_draft_wc,
                                "min_words": min_words,
                                "max_words": max_words,
                            },
                        )
                    )
                mismatch_floor = max(25, int(max(actual_word_count, 1) * 0.05))
                if (
                    abs(stored_chapter_wc - actual_word_count) > mismatch_floor
                    or abs(stored_draft_wc - actual_word_count) > mismatch_floor
                ):
                    findings.append(
                        Finding(
                            code="CHAPTER_WORD_COUNT_MISMATCH",
                            severity="major",
                            chapter=chapter.chapter_number,
                            message=(
                                f"第{chapter.chapter_number}章字数字段漂移："
                                f"chapter={stored_chapter_wc}, draft={stored_draft_wc}, "
                                f"actual={actual_word_count}。"
                            ),
                            recommendation="先回填真实字数，再让恢复逻辑按 current draft 正文重新判断。",
                            metadata={
                                "actual_word_count": actual_word_count,
                                "chapter_current_word_count": stored_chapter_wc,
                                "draft_word_count": stored_draft_wc,
                            },
                        )
                    )
            if chapter.status != "complete":
                findings.append(
                    Finding(
                        code="CHAPTER_NOT_COMPLETE",
                        severity="major" if chapter.status == "revision" else "critical",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章状态为 {chapter.status}。",
                        recommendation="修复后统一进入 review/complete；drafting 状态章节不能作为稳定上下文。",
                    )
                )
            if chapter.production_state != "ok":
                findings.append(
                    Finding(
                        code="CHAPTER_PRODUCTION_NOT_OK",
                        severity="critical" if chapter.production_state == "blocked" else "major",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章 production_state={chapter.production_state}。",
                        recommendation="优先处理 blocked/pending 章节，避免后续状态快照继承错误。",
                    )
                )

        for scene in scenes:
            chapter = chapter_by_id.get(scene.chapter_id)
            chapter_number = chapter.chapter_number if chapter else None
            if scene.id not in scene_drafts:
                findings.append(
                    Finding(
                        code="MISSING_CURRENT_SCENE_DRAFT",
                        severity="critical",
                        chapter=chapter_number,
                        scene=scene.scene_number,
                        message=f"第{chapter_number}章第{scene.scene_number}场没有 current SceneDraft。",
                        recommendation="补写该 SceneDraft 后再 assemble 章节。",
                    )
                )
            if _is_generic_time_label(scene.time_label):
                findings.append(
                    Finding(
                        code="GENERIC_SCENE_TIME_LABEL",
                        severity="major",
                        chapter=chapter_number,
                        scene=scene.scene_number,
                        message=f"第{chapter_number}章第{scene.scene_number}场时间标签过泛：{scene.time_label!r}。",
                        recommendation="回填具体故事内时间/相对时间，供时间线连续性门禁使用。",
                    )
                )
            story = _story_purpose(scene)
            if _is_generic_story_purpose(story):
                findings.append(
                    Finding(
                        code="GENERIC_SCENE_STORY_PURPOSE",
                        severity="major",
                        chapter=chapter_number,
                        scene=scene.scene_number,
                        message=f"第{chapter_number}章第{scene.scene_number}场 story purpose 过泛。",
                        recommendation="回填该场独有剧情推进任务，避免继续写出模式重复。",
                        metadata={"purpose": _short(story)},
                    )
                )

        # Timeline snapshots.
        previous_snapshot: ChapterStateSnapshotModel | None = None
        for chapter in chapters:
            snapshot = snapshots.get(chapter.chapter_number)
            if snapshot is None:
                findings.append(
                    Finding(
                        code="MISSING_STATE_SNAPSHOT",
                        severity="critical",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章缺少 ChapterStateSnapshot。",
                        recommendation="先抽取章节硬事实和 time_anchor，再允许后续章节使用上下文。",
                    )
                )
                continue
            if snapshot.extraction_status != "ok":
                findings.append(
                    Finding(
                        code="SNAPSHOT_EXTRACTION_NOT_OK",
                        severity="critical",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章快照抽取状态为 {snapshot.extraction_status}。",
                        recommendation="重新抽取/修正快照，否则会污染后续上下文。",
                    )
                )
            if not snapshot.time_anchor:
                findings.append(
                    Finding(
                        code="SNAPSHOT_TIME_ANCHOR_MISSING",
                        severity="major",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章缺少 time_anchor。",
                        recommendation="补充故事内时间锚点，避免时间线无法比较。",
                    )
                )
            if not snapshot.chapter_time_span:
                findings.append(
                    Finding(
                        code="SNAPSHOT_TIME_SPAN_MISSING",
                        severity="minor",
                        chapter=chapter.chapter_number,
                        message=f"第{chapter.chapter_number}章缺少 chapter_time_span。",
                        recommendation="补充本章耗时，供倒计时/间隔检查使用。",
                    )
                )
            if previous_snapshot is not None and snapshot.time_anchor and previous_snapshot.time_anchor:
                current_parsed = _parse_time_anchor(snapshot.time_anchor)
                previous_parsed = _parse_time_anchor(previous_snapshot.time_anchor)
                if (
                    current_parsed is not None
                    and previous_parsed is not None
                    and current_parsed < previous_parsed
                    and not _is_flashback_anchor(snapshot.time_anchor)
                ):
                    findings.append(
                        Finding(
                            code="TIME_ANCHOR_REGRESSION",
                            severity="critical",
                            chapter=chapter.chapter_number,
                            message=(
                                f"第{chapter.chapter_number}章 time_anchor={snapshot.time_anchor!r} "
                                f"早于上一章 {previous_snapshot.time_anchor!r}。"
                            ),
                            recommendation="修正文内时间或将该章显式标记为回忆/插叙。",
                            metadata={
                                "current_parsed": current_parsed,
                                "previous_parsed": previous_parsed,
                            },
                        )
                    )
            previous_snapshot = snapshot

        # Duplicate planning fields and scene patterns.
        goal_counter = Counter(_norm(chapter.chapter_goal) for chapter in chapters if _norm(chapter.chapter_goal))
        conflict_counter = Counter(_norm(chapter.main_conflict) for chapter in chapters if _norm(chapter.main_conflict))
        hook_counter = Counter(_norm(chapter.hook_description) for chapter in chapters if _norm(chapter.hook_description))
        for label, counter, code, raw_getter in (
            ("chapter_goal", goal_counter, "DUPLICATE_CHAPTER_GOAL", lambda c: c.chapter_goal),
            ("main_conflict", conflict_counter, "DUPLICATE_MAIN_CONFLICT", lambda c: c.main_conflict),
            ("hook_description", hook_counter, "DUPLICATE_HOOK_DESCRIPTION", lambda c: c.hook_description),
        ):
            repeated = {key for key, count in counter.items() if count >= 3}
            for key in repeated:
                hit_chapters = [c.chapter_number for c in chapters if _norm(raw_getter(c)) == key]
                findings.append(
                    Finding(
                        code=code,
                        severity="major",
                        message=f"{label} 在 {len(hit_chapters)} 个章节中重复。",
                        recommendation="回到 chapter outline 层重做这些章节的独有目标/冲突/钩子。",
                        metadata={"chapters": hit_chapters[:30], "total": len(hit_chapters)},
                    )
                )

        pattern_counter: Counter[tuple[str, ...]] = Counter()
        pattern_chapters: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for chapter in chapters:
            pattern = tuple(scene.scene_type for scene in scenes_by_chapter.get(chapter.id, []))
            if pattern:
                pattern_counter[pattern] += 1
                pattern_chapters[pattern].append(chapter.chapter_number)
        for pattern, count in pattern_counter.items():
            if count >= 10:
                findings.append(
                    Finding(
                        code="REPEATED_SCENE_TYPE_PATTERN",
                        severity="major",
                        message=f"场景类型序列 {pattern} 重复 {count} 次。",
                        recommendation="重做相关章节的场景功能分布，避免章节形态机械复制。",
                        metadata={"pattern": list(pattern), "chapters": pattern_chapters[pattern][:40], "total": count},
                    )
                )

        fingerprints = [
            build_chapter_fingerprint(_chapter_plan_like(chapter, scenes_by_chapter.get(chapter.id, [])))
            for chapter in chapters
        ]
        fp_report = find_near_duplicate_chapters(
            fingerprints,
            warning_threshold=0.68,
            critical_threshold=0.82,
            max_chapter_distance=12,
        )
        for item in fp_report.findings:
            findings.append(
                Finding(
                    code="NEAR_DUPLICATE_CHAPTER_PLAN",
                    severity="critical" if item.severity == "critical" else "major",
                    chapter=item.chapter_b,
                    message=(
                        f"第{item.chapter_a}章与第{item.chapter_b}章规划指纹相似度 {item.similarity:.2f}。"
                    ),
                    recommendation="优先在 outline 层改写重复章节，再决定是否重写正文。",
                    metadata={
                        "chapter_a": item.chapter_a,
                        "chapter_b": item.chapter_b,
                        "similarity": item.similarity,
                        "reason": item.reason,
                        "matched_fields": list(item.matched_fields),
                    },
                )
            )

        # Prose repetition checks. This can be expensive on 500+ long chapters,
        # so fast structural audits can skip it and re-run on a narrowed range.
        if not skip_prose:
            previous_hooks: list[tuple[int, str]] = []
            scene_text_window: list[tuple[int, int, str]] = []
            for chapter in chapters:
                draft = chapter_drafts.get(chapter.id)
                if draft and draft.content_md:
                    intra = detect_intra_chapter_repetition(draft.content_md)
                    if intra:
                        severities = Counter(item.get("severity", "major") for item in intra)
                        findings.append(
                            Finding(
                                code="INTRA_CHAPTER_PARAGRAPH_REPETITION",
                                severity="critical" if severities.get("critical") else "major",
                                chapter=chapter.chapter_number,
                                message=f"第{chapter.chapter_number}章存在 {len(intra)} 处段落/改写重复。",
                                recommendation="先做段落去重，再按章节目标补足被删掉的剧情功能。",
                                metadata={"samples": intra[:5]},
                            )
                        )
                    hook_findings = check_hook_repetition(
                        draft.content_md[-220:],
                        previous_hooks[-12:],
                        similarity_threshold=0.78,
                        hook_length=220,
                    )
                    for hook in hook_findings:
                        findings.append(
                            Finding(
                                code="REPEATED_CHAPTER_ENDING_HOOK",
                                severity="major",
                                chapter=chapter.chapter_number,
                                message=f"第{chapter.chapter_number}章结尾与第{hook['chapter']}章相似。",
                                recommendation="改写章节尾钩，让信息释放/危机形态发生差异。",
                                metadata=hook,
                            )
                        )
                    previous_hooks.append((chapter.chapter_number, draft.content_md[-220:]))

                for scene in scenes_by_chapter.get(chapter.id, []):
                    scene_draft = scene_drafts.get(scene.id)
                    if not scene_draft or not scene_draft.content_md:
                        continue
                    for prev_ch, prev_sc, prev_text in scene_text_window[-80:]:
                        sim = compute_jaccard_similarity(scene_draft.content_md, prev_text)
                        if sim >= 0.82:
                            findings.append(
                                Finding(
                                    code="NEAR_DUPLICATE_SCENE_TEXT",
                                    severity="critical" if sim >= 0.9 else "major",
                                    chapter=chapter.chapter_number,
                                    scene=scene.scene_number,
                                    message=(
                                        f"第{chapter.chapter_number}.{scene.scene_number}场与"
                                        f"第{prev_ch}.{prev_sc}场正文相似度 {sim:.2f}。"
                                    ),
                                    recommendation="回到场景卡改写该场独有事件，不要只做文字降重。",
                                    metadata={
                                        "previous_chapter": prev_ch,
                                        "previous_scene": prev_sc,
                                        "similarity": round(sim, 3),
                                    },
                                )
                            )
                            break
                    scene_text_window.append((chapter.chapter_number, scene.scene_number, scene_draft.content_md))

        severity_counter = Counter(f.severity for f in findings)
        code_counter = Counter(f.code for f in findings)
        critical_chapters = sorted({f.chapter for f in findings if f.severity == "critical" and f.chapter is not None})
        major_chapters = sorted({f.chapter for f in findings if f.severity == "major" and f.chapter is not None})

        report = {
            "project": {"slug": project.slug, "title": project.title, "status": project.status},
            "scope": {"chapter_from": start, "chapter_to": end},
            "generated_at": generated_at,
            "prose_checks_skipped": skip_prose,
            "summary": {
                "chapters_scanned": len(chapters),
                "scenes_scanned": len(scenes),
                "current_chapter_drafts": len(chapter_drafts),
                "current_scene_drafts": len(scene_drafts),
                "findings_total": len(findings),
                "by_severity": dict(severity_counter),
                "by_code": dict(code_counter.most_common()),
                "critical_chapters_total": len(critical_chapters),
                "major_chapters_total": len(major_chapters),
                "critical_chapters_sample": critical_chapters[:80],
                "major_chapters_sample": major_chapters[:80],
            },
            "findings": [finding.to_dict() for finding in findings],
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{PROJECT_SLUG}_{start}_{end}_structural_audit.json"
    md_path = output_dir / f"{PROJECT_SLUG}_{start}_{end}_structural_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report, json_path=json_path), encoding="utf-8")
    report["output"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def _render_markdown(report: dict[str, Any], *, json_path: Path) -> str:
    summary = report["summary"]
    lines = [
        f"# 《{report['project']['title']}》结构审计",
        "",
        f"- 项目：`{report['project']['slug']}`",
        f"- 范围：第 {report['scope']['chapter_from']} - {report['scope']['chapter_to']} 章",
        f"- 生成时间：{report['generated_at']}",
        f"- JSON 明细：`{json_path}`",
        "",
        "## 摘要",
        "",
        f"- 扫描章节：{summary['chapters_scanned']}",
        f"- 扫描场景：{summary['scenes_scanned']}",
        f"- current ChapterDraft：{summary['current_chapter_drafts']}",
        f"- current SceneDraft：{summary['current_scene_drafts']}",
        f"- Findings：{summary['findings_total']}",
        f"- Severity：{summary['by_severity']}",
        "",
        "## 类型分布",
        "",
    ]
    for code, count in summary["by_code"].items():
        lines.append(f"- `{code}`: {count}")
    lines.extend(["", "## 高优先级样本", ""])
    high = [f for f in report["findings"] if f["severity"] in {"critical", "major"}]
    for finding in high[:80]:
        loc = f"第{finding.get('chapter')}章"
        if finding.get("scene") is not None:
            loc += f" 第{finding['scene']}场"
        lines.append(f"- [{finding['severity']}] `{finding['code']}` {loc}: {finding['message']}")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--skip-prose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        audit(start=args.start, end=args.end, output_dir=args.output_dir, skip_prose=args.skip_prose)
    )
    print(json.dumps({**report["summary"], "output": report["output"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

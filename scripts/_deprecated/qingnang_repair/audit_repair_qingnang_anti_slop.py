"""Audit and route Anti-Slop Prose findings for 《青囊不语问阴阳》.

This is a project-local operational script, not a generic quality gate.
It checks current chapter drafts against the new prose gates, applies only
deterministic low-risk text patches, writes an audit report, and creates
rewrite tasks for chapters that need a scene-aware ending rewrite.

Usage:
    uv run python scripts/audit_repair_qingnang_anti_slop.py
    uv run python scripts/audit_repair_qingnang_anti_slop.py --apply
"""

from __future__ import annotations

# ruff: noqa: E501, RUF001
import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select, update

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.ai_flavor_gate import run_ai_flavor_gate  # noqa: E402
from bestseller.services.anti_meta_gate import check_anti_meta_gate  # noqa: E402
from bestseller.services.character_role_gate import (  # noqa: E402
    check_character_role_compliance,
    load_character_profiles,
)
from bestseller.services.commercial_novel_gate import (  # noqa: E402
    commercial_gate_report_to_dict,
    evaluate_book_package,
)
from bestseller.services.drafts import (  # noqa: E402
    count_words,
    format_chapter_heading,
    sanitize_novel_markdown_content,
)
from bestseller.services.exports import write_markdown_output  # noqa: E402
from bestseller.services.quality_gates_config import get_quality_gates_config  # noqa: E402
from bestseller.services.show_dont_tell_gate import check_show_dont_tell_gate  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


PROJECT_SLUG = "exorcist-detective-1778051012"
REPAIR_SOURCE = "qingnang_anti_slop_prose_20260523"


META_REPLACEMENTS = {
    "钩子": "未解处",
    "章末": "门口",
    "这一章": "这一夜",
    "本章": "眼前这场事",
    "下一章": "门后的那一刻",
    "读者期待": "众人等着落地的答案",
}


def _has_heading(content_md: str, chapter_number: int) -> bool:
    for line in content_md.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.startswith(f"# 第{chapter_number}章")
    return False


def _sync_chapter_file(
    *,
    output_base_dir: Path,
    slug: str,
    chapter: ChapterModel,
    content_md: str,
    language: str | None,
) -> Path:
    clean = sanitize_novel_markdown_content(content_md, language=language)
    chapter_number = int(chapter.chapter_number)
    if not _has_heading(clean, chapter_number):
        heading = format_chapter_heading(chapter_number, chapter.title, language=language)
        clean = f"{heading}\n\n{clean}"
    output_path = output_base_dir / slug / f"chapter-{chapter_number:03d}.md"
    write_markdown_output(output_path, clean)
    return output_path


def _patch_meta_terms(text: str, terms: list[str]) -> tuple[str, list[str]]:
    patched = text
    applied: list[str] = []
    for term in terms:
        replacement = META_REPLACEMENTS.get(term)
        if replacement and term in patched:
            patched = patched.replace(term, replacement)
            applied.append(f"{term}->{replacement}")
    return patched, applied


def _chapter_rewrite_instruction(
    *,
    chapter_number: int,
    title: str,
    anti: Any,
    show: Any,
    role: Any,
) -> str:
    ending_excerpt = getattr(anti, "ending_excerpt", "") or ""
    meta_terms = "、".join(sorted({f.term for f in anti.findings})) or "无"
    show_categories = "、".join(sorted({f.category for f in show.findings})) or "无"
    role_findings = "；".join(f.detail for f in role.findings[:4]) or "无"
    return f"""【Anti-Slop Prose 修复任务｜第{chapter_number}章《{title or ''}》】
修复目标：只修复 prose gate 暴露的问题，不改本章核心事件、人物关系、镜债规则或已建立因果。

必须执行：
1. 最后 3-5 句重写成“场内动作 / 物理画面 / 新事实揭示”的可见帧；不得解释“这意味着什么”，不得总结人物感想，不得使用章节边界语言。
2. 如果正文有设计层词汇，改成故事世界内的动作、物件或对白。当前命中词：{meta_terms}。
3. 如果存在讲而不演，把心理/关系解释改成具身动作、停顿、物件互动、短对白或环境反应。当前类别：{show_categories}。
4. 角色仍要遵循《青囊》设定：林渊不是普通侦探，破局必须落到青囊、罗盘、铜钱、阴阳眼、符、认账/否认规则或现实物证链中的至少一种。
5. 保持章节长度在现有长度附近；这是收尾与局部 prose 修复，不是扩写新剧情。

当前结尾门禁证据：
{ending_excerpt}

角色定位提示：
{role_findings}
"""


async def _load_current_rows():
    settings = load_settings()
    async with session_scope(settings) as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG)
        )
        if project is None:
            raise RuntimeError(f"Project {PROJECT_SLUG!r} not found")
        rows = (
            await session.execute(
                select(ChapterModel, ChapterDraftVersionModel)
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .order_by(ChapterModel.chapter_number)
            )
        ).all()
        return project, rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply deterministic patches and create rewrite tasks")
    parser.add_argument("--chapter", type=int, default=None, help="Limit to one chapter")
    parser.add_argument("--replace-existing", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    settings = load_settings()
    root = Path(settings.output.base_dir) / PROJECT_SLUG
    audits_dir = root / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    profiles = load_character_profiles(root / "story-bible" / "cast-and-promises.md")
    ai_cfg = get_quality_gates_config().ai_flavor

    project, rows = await _load_current_rows()
    summary: dict[str, int | bool] = {
        "chapters": 0,
        "anti_fail": 0,
        "ending_fail": 0,
        "show_warn": 0,
        "role_findings": 0,
        "ai_patched": 0,
        "meta_patched": 0,
        "rewrite_tasks": 0,
        "db_updates": 0,
    }
    chapter_reports: list[dict[str, Any]] = []
    task_ids: list[str] = []

    async with session_scope(settings) as session:
        db_project = await session.get(ProjectModel, project.id)
        if db_project is None:
            raise RuntimeError("project disappeared")
        if args.apply and args.replace_existing:
            await session.execute(
                update(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == db_project.id,
                    RewriteTaskModel.status.in_(["pending", "queued"]),
                    RewriteTaskModel.metadata_json["repair_source"].as_string() == REPAIR_SOURCE,
                )
                .values(status="superseded")
            )

        for chapter, draft in rows:
            chapter_no = int(chapter.chapter_number)
            if args.chapter is not None and chapter_no != args.chapter:
                continue
            text = draft.content_md or ""
            anti = check_anti_meta_gate(text, chapter_position=chapter_no)
            show = check_show_dont_tell_gate(text, chapter_position=chapter_no)
            role = check_character_role_compliance(
                text,
                chapter_position=chapter_no,
                profiles=profiles,
            )
            ai = run_ai_flavor_gate(
                chapter_number=chapter_no,
                content_md=text,
                language=db_project.language,
                config=ai_cfg,
                project_output_dir=None,
            )

            patched_text = text
            applied_patches: list[str] = []
            if args.apply and ai.patched_text:
                patched_text = ai.patched_text
                applied_patches.append(f"ai_flavor:{len(ai.edits)}")
            if args.apply and anti.findings:
                patched_text, meta_applied = _patch_meta_terms(
                    patched_text,
                    [finding.term for finding in anti.findings],
                )
                if meta_applied:
                    applied_patches.extend(meta_applied)

            if args.apply and patched_text != text:
                current_draft = await session.get(ChapterDraftVersionModel, draft.id)
                current_chapter = await session.get(ChapterModel, chapter.id)
                if current_draft is None or current_chapter is None:
                    raise RuntimeError(f"Missing current draft/chapter for ch{chapter_no}")
                current_draft.content_md = patched_text
                current_draft.word_count = count_words(patched_text)
                current_chapter.current_word_count = current_draft.word_count
                _sync_chapter_file(
                    output_base_dir=Path(settings.output.base_dir),
                    slug=PROJECT_SLUG,
                    chapter=current_chapter,
                    content_md=patched_text,
                    language=db_project.language,
                )
                summary["db_updates"] = int(summary["db_updates"]) + 1

            needs_rewrite = (not anti.ending_passed) or any(
                f.severity == "block" for f in anti.findings
            ) or bool(show.findings)
            if args.apply and needs_rewrite:
                current_chapter = await session.get(ChapterModel, chapter.id)
                if current_chapter is None:
                    raise RuntimeError(f"Missing chapter for ch{chapter_no}")
                task = RewriteTaskModel(
                    project_id=db_project.id,
                    trigger_type="anti_slop_prose_gate_repair",
                    trigger_source_id=current_chapter.id,
                    rewrite_strategy="targeted_prose_repair",
                    priority=1 if not anti.ending_passed else 2,
                    status="pending",
                    instructions=_chapter_rewrite_instruction(
                        chapter_number=chapter_no,
                        title=current_chapter.title or "",
                        anti=anti,
                        show=show,
                        role=role,
                    ),
                    context_required=["current_chapter", "prior_chapter_tail", "story_bible", "cast"],
                    metadata_json={
                        "repair_source": REPAIR_SOURCE,
                        "chapter_number": chapter_no,
                        "anti_meta_passed": anti.passed,
                        "ending_passed": anti.ending_passed,
                        "show_findings": len(show.findings),
                        "role_findings": len(role.findings),
                    },
                )
                session.add(task)
                current_chapter.status = "revision"
                current_chapter.production_state = "blocked"
                await session.flush()
                task_ids.append(str(task.id))
                summary["rewrite_tasks"] = int(summary["rewrite_tasks"]) + 1

            summary["chapters"] = int(summary["chapters"]) + 1
            summary["anti_fail"] = int(summary["anti_fail"]) + (0 if anti.passed else 1)
            summary["ending_fail"] = int(summary["ending_fail"]) + (0 if anti.ending_passed else 1)
            summary["show_warn"] = int(summary["show_warn"]) + (0 if show.passed else 1)
            summary["role_findings"] = int(summary["role_findings"]) + len(role.findings)
            summary["ai_patched"] = int(summary["ai_patched"]) + (1 if ai.patched_text else 0)
            summary["meta_patched"] = int(summary["meta_patched"]) + (
                1 if any("->" in item for item in applied_patches) else 0
            )
            chapter_reports.append(
                {
                    "chapter": chapter_no,
                    "title": chapter.title,
                    "anti_passed": anti.passed,
                    "ending_passed": anti.ending_passed,
                    "anti_findings": [
                        {
                            "term": f.term,
                            "severity": f.severity,
                            "location": f.location,
                            "excerpt": f.excerpt,
                        }
                        for f in anti.findings
                    ],
                    "ending_excerpt": anti.ending_excerpt,
                    "show_findings": [
                        {
                            "category": f.category,
                            "location": f.location,
                            "excerpt": f.excerpt,
                        }
                        for f in show.findings
                    ],
                    "role_findings": [f.detail for f in role.findings],
                    "ai_decision": ai.decision,
                    "ai_before": ai.before_score,
                    "ai_after": ai.after_score,
                    "ai_edits": len(ai.edits),
                    "applied_patches": applied_patches,
                    "needs_rewrite": needs_rewrite,
                }
            )

    commercial = evaluate_book_package(root)
    payload = {
        "project_slug": PROJECT_SLUG,
        "project_title": project.title,
        "applied": args.apply,
        "summary": summary,
        "task_ids": task_ids,
        "commercial_gate": {
            "passed": commercial.passed,
            "score": commercial.overall_score,
            "issue_count": len(commercial.issues),
            "top_issues": commercial_gate_report_to_dict(commercial)["issues"][:20],
        },
        "chapters": chapter_reports,
    }
    suffix = "after-apply" if args.apply else "before"
    audit_path = audits_dir / f"anti-slop-prose-audit-20260523-{suffix}.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = audits_dir / f"anti-slop-prose-audit-20260523-{suffix}.md"
    lines = [
        "# Anti-Slop Prose Audit — 青囊不语问阴阳",
        "",
        f"- applied: `{args.apply}`",
        f"- chapters: `{summary['chapters']}`",
        f"- anti-meta fail: `{summary['anti_fail']}`",
        f"- in-scene ending fail: `{summary['ending_fail']}`",
        f"- show-don't-tell warn: `{summary['show_warn']}`",
        f"- AI-flavor patched candidates: `{summary['ai_patched']}`",
        f"- rewrite tasks created: `{summary['rewrite_tasks']}`",
        f"- commercial gate: `passed={commercial.passed}`, score `{commercial.overall_score}`",
        "",
        "## Chapters Needing Rewrite",
    ]
    for row in chapter_reports:
        if row["needs_rewrite"]:
            reasons: list[str] = []
            if not row["ending_passed"]:
                reasons.append("ending")
            if row["anti_findings"]:
                reasons.append("anti-meta")
            if row["show_findings"]:
                reasons.append("show-don't-tell")
            lines.append(
                f"- ch{row['chapter']:03d} {row['title']}: {', '.join(reasons)}"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"audit": str(audit_path), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

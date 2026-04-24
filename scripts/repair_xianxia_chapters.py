"""Targeted chapter repair for 《道种破虚》(xianxia-upgrade-1776137730).

Problems addressed
------------------
1. **Dead-character violations** — 陆沉 (dies ch458) and 苏瑶 (dies ch435)
   appear as active characters in chapters written after their deaths.
   The script scans chapter prose for these names, determines whether
   they appear in active roles (speaking, fighting, giving orders) vs
   passive references (flashback, memories, reputation), and creates
   rewrite tasks only for genuine violations.

2. **Mother foreshadowing** — The protagonist's mother first appears at
   ch250 with no prior setup.  We insert brief, natural hints in
   ~5 chapters before ch250 so the introduction doesn't feel sudden.

3. **Sister foreshadowing** — Sister 宁微 appears at ch477 with no prior
   mentions.  We insert brief hints in ~5 chapters before ch477.

Usage
-----
    # Dry-run: show what would be fixed, no DB writes
    uv run python scripts/repair_xianxia_chapters.py --dry-run

    # Create rewrite tasks only (no LLM calls yet)
    uv run python scripts/repair_xianxia_chapters.py --create-tasks

    # Full repair: create tasks + call LLM + save rewrites
    uv run python scripts/repair_xianxia_chapters.py --execute

    # Limit to N chapters (useful for test runs)
    uv run python scripts/repair_xianxia_chapters.py --execute --limit 10

    # Only do foreshadowing rewrites
    uv run python scripts/repair_xianxia_chapters.py --execute --foreshadow-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    CharacterModel,
    ChapterModel,
    ChapterDraftVersionModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.projects import get_project_by_slug  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

logger = logging.getLogger("repair_xianxia_chapters")

PROJECT_SLUG = "xianxia-upgrade-1776137730"

# ---------------------------------------------------------------------------
# Character death facts for this book
# ---------------------------------------------------------------------------

@dataclass
class DeadCharacter:
    name: str
    death_chapter: int
    # Additional name variants that might appear in prose
    aliases: list[str] = field(default_factory=list)
    # Role description for repair instructions
    role_description: str = ""

DECEASED_CHARACTERS: list[DeadCharacter] = [
    DeadCharacter(
        name="陆沉",
        death_chapter=458,
        aliases=["陆师兄", "陆前辈"],
        role_description="宁尘的师兄兼挚友，牺牲于第458章",
    ),
    DeadCharacter(
        name="苏瑶",
        death_chapter=435,
        aliases=["苏师姐", "苏前辈"],
        role_description="宁尘的师姐，牺牲于第435章",
    ),
]

# ---------------------------------------------------------------------------
# Foreshadowing targets
# ---------------------------------------------------------------------------

@dataclass
class ForeshadowTarget:
    character_name: str
    first_appearance_chapter: int
    hint_chapters: list[int]   # chapters where we'll weave in brief mentions
    hint_instruction: str       # what kind of hint to add

FORESHADOW_TARGETS: list[ForeshadowTarget] = [
    ForeshadowTarget(
        character_name="母亲（陆瑶）",
        first_appearance_chapter=250,
        # Brief hint in ~5 chapters spread before ch250
        hint_chapters=[30, 80, 130, 180, 230],
        hint_instruction=(
            "在本章中，请自然地织入一处对宁尘母亲的简短提及或回忆——"
            "不超过3句话，不要大篇幅展开。"
            "可以是宁尘看到某样东西想起了母亲留下的一句话、一件信物、"
            "或者别人无意间提起母亲曾经的事迹。"
            "目的是让读者在第250章母亲正式登场前感受到她的存在。"
            "保持本章主线剧情不变，仅微调相关段落自然嵌入这一笔。"
        ),
    ),
    ForeshadowTarget(
        character_name="妹妹宁微",
        first_appearance_chapter=477,
        # Brief hints before ch477
        hint_chapters=[100, 200, 320, 420, 460],
        hint_instruction=(
            "在本章中，请自然地织入一处对宁尘妹妹（宁微）的简短提及——"
            "不超过3句话，不要大篇幅展开。"
            "可以是宁尘偶尔想起幼时与妹妹的一个细节、"
            "或某个配角不经意提及宁家还有个小妹、"
            "或宁尘内心深处担心妹妹现在过得如何。"
            "目的是让读者在第477章妹妹正式出现前有心理铺垫。"
            "保持本章主线剧情不变，仅微调相关段落自然嵌入这一笔。"
        ),
    ),
]

# ---------------------------------------------------------------------------
# Scan utilities
# ---------------------------------------------------------------------------

# Patterns that indicate ACTIVE role (violation if character is dead)
_ACTIVE_ROLE_PATTERNS = [
    r"说道|道|喝道|沉声|厉声|低声|笑道|冷声|说",   # dialogue verbs
    r"出手|出剑|挥手|迈步|转身|站起|跑|跃|飞",       # physical action
    r"命令|下令|吩咐|嘱咐|叮嘱",                      # commanding
    r"看向|望向|注视|凝视",                            # looking actions
    r"感到|心中|意识到|知道|想到",                     # inner monologue
]
_ACTIVE_RE = re.compile("|".join(_ACTIVE_ROLE_PATTERNS))

# Patterns indicating PASSIVE reference (flashback/memory — acceptable)
_PASSIVE_PATTERNS = [
    r"想起|回忆|记得|忆起|脑海中|当年|往昔|曾经",
    r"遗像|灵位|墓|坟|祭|悼|牌位",
    r"的话语|的嘱托|的遗言|的身影|的音容",
    r"已经.*(?:逝|故|离|死)|(?:逝|故|离|死).*已经",
    r"在世时|生前|以前|那时候",
]
_PASSIVE_RE = re.compile("|".join(_PASSIVE_PATTERNS))

_CONTEXT_WINDOW = 150  # chars around name hit to sample


def _check_active_in_text(text_content: str, char_name: str, aliases: list[str]) -> list[str]:
    """Return list of evidence snippets where char appears in active role."""
    all_names = [char_name] + aliases
    name_re = re.compile("|".join(re.escape(n) for n in all_names))
    violations: list[str] = []

    for m in name_re.finditer(text_content):
        start = max(0, m.start() - _CONTEXT_WINDOW // 2)
        end = min(len(text_content), m.end() + _CONTEXT_WINDOW // 2)
        snippet = text_content[start:end]

        # If the context around this mention looks passive, skip
        if _PASSIVE_RE.search(snippet):
            continue
        # If the context looks active, flag it
        if _ACTIVE_RE.search(snippet):
            # Truncate for logging
            short = snippet.replace("\n", " ").strip()[:120]
            violations.append(short)

    return violations


@dataclass
class ViolationFinding:
    chapter_number: int
    chapter_id: Any  # UUID
    draft_id: Any
    character_name: str
    evidence: list[str]  # up to 3 snippets


async def _scan_dead_character_violations(
    session: AsyncSession,
    project_id: Any,
) -> list[ViolationFinding]:
    """Scan chapter prose for dead characters appearing in active roles.

    Reads one chapter at a time to avoid contending with the live writer's
    row-level locks on chapter_draft_versions.
    """
    findings: list[ViolationFinding] = []

    # First fetch chapter IDs only (lightweight, avoids draft table lock)
    for dead in DECEASED_CHARACTERS:
        try:
            ch_rows = await session.execute(
                text(
                    "SELECT id, chapter_number FROM chapters "
                    "WHERE project_id = :pid "
                    "  AND chapter_number > :death_ch "
                    "  AND chapter_number <= 477 "
                    "ORDER BY chapter_number ASC"
                ),
                {"pid": str(project_id), "death_ch": dead.death_chapter},
            )
            chapters = ch_rows.fetchall()
        except Exception as exc:
            logger.warning("Could not fetch chapter list: %s", exc)
            continue

        logger.info(
            "Scanning %d chapters after %s's death (ch%d)...",
            len(chapters), dead.name, dead.death_chapter,
        )

        for ch_row in chapters:
            chapter_id, chapter_number = ch_row.id, ch_row.chapter_number
            try:
                draft_row = await session.execute(
                    text(
                        "SELECT id, content_md FROM chapter_draft_versions "
                        "WHERE chapter_id = :cid AND is_current = TRUE "
                        "LIMIT 1"
                    ),
                    {"cid": str(chapter_id)},
                )
                draft = draft_row.fetchone()
            except Exception as exc:
                logger.debug("ch%d: could not read draft (%s), skipping", chapter_number, exc)
                continue

            if draft is None or not draft.content_md:
                continue

            evidence = _check_active_in_text(draft.content_md, dead.name, dead.aliases)
            if evidence:
                findings.append(
                    ViolationFinding(
                        chapter_number=chapter_number,
                        chapter_id=chapter_id,
                        draft_id=draft.id,
                        character_name=dead.name,
                        evidence=evidence[:3],
                    )
                )
                logger.info(
                    "VIOLATION ch%d: %s appears active (%d hits)",
                    chapter_number, dead.name, len(evidence),
                )

    return findings


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def _make_violation_instruction(finding: ViolationFinding, char: DeadCharacter) -> str:
    evidence_block = "\n".join(f"  • {e}" for e in finding.evidence)
    return (
        f"【角色连续性修复】\n"
        f"本章（第{finding.chapter_number}章）出现了已故角色「{char.name}」（{char.role_description}）"
        f"在主动参与场景，违反了时间线一致性。\n\n"
        f"检测到的问题片段：\n{evidence_block}\n\n"
        f"修复要求：\n"
        f"1. 将「{char.name}」的主动台词、动作、命令全部改为：其他在场角色的台词/动作，"
        f"或改写为宁尘/其他人对「{char.name}」的回忆/追思/想象（不超过1-2句）。\n"
        f"2. 若该角色在本章承担了关键情节功能（如传递信息、战斗支援），"
        f"需将该功能转移给另一个仍存活的配角。\n"
        f"3. 保持本章核心事件线不变，只做最小改动以消除死亡角色的主动出场。\n"
        f"4. 改写后不得出现「{char.name}」的名字超过1次（仅作回忆中一提即可）。"
    )


def _make_foreshadow_instruction(target: ForeshadowTarget, chapter_number: int) -> str:
    return (
        f"【伏笔铺垫修复 — {target.character_name}】\n"
        f"{target.hint_instruction}\n\n"
        f"注意事项：\n"
        f"1. 插入位置选在本章情绪舒缓处（非战斗/高潮场景），避免打断紧张节奏。\n"
        f"2. 篇幅严格控制在3句以内，自然融入，不要突兀。\n"
        f"3. 本章总字数可微增100-200字，不得减少已有内容。\n"
        f"4. 不要点破{target.character_name}的完整身份，只留下读者能感受到却无法确定的悬念。"
    )


async def _chapter_id_for_number(
    session: AsyncSession, project_id: Any, chapter_number: int
) -> Any | None:
    """Fetch chapter UUID for a given chapter number."""
    row = await session.scalar(
        text(
            "SELECT id FROM chapters WHERE project_id = :pid AND chapter_number = :ch"
        ),
        {"pid": str(project_id), "ch": chapter_number},
    )
    return row


async def _has_existing_pending_task(
    session: AsyncSession, project_id: Any, chapter_id: Any, trigger_type: str
) -> bool:
    count = await session.scalar(
        select(RewriteTaskModel).where(
            RewriteTaskModel.project_id == project_id,
            RewriteTaskModel.trigger_source_id == chapter_id,
            RewriteTaskModel.trigger_type == trigger_type,
            RewriteTaskModel.status.in_(["pending", "queued"]),
        )
    )
    return count is not None


async def create_violation_tasks(
    session: AsyncSession,
    project: ProjectModel,
    findings: list[ViolationFinding],
    dry_run: bool = False,
) -> int:
    """Create RewriteTaskModel for each violation finding."""
    # Build a quick lookup for dead character meta
    char_map = {d.name: d for d in DECEASED_CHARACTERS}
    created = 0

    for finding in findings:
        char = char_map.get(finding.character_name)
        if char is None:
            continue

        chapter_id = finding.chapter_id
        already = await _has_existing_pending_task(
            session, project.id, chapter_id, "lifecycle_violation"
        )
        if already:
            logger.info("ch%d: task already exists, skipping", finding.chapter_number)
            continue

        instruction = _make_violation_instruction(finding, char)

        if dry_run:
            print(
                f"[DRY-RUN] Would create rewrite task for ch{finding.chapter_number} "
                f"({finding.character_name} violation)"
            )
        else:
            task = RewriteTaskModel(
                project_id=project.id,
                trigger_type="lifecycle_violation",
                trigger_source_id=chapter_id,
                rewrite_strategy="fix_lifecycle_violation",
                priority=2,  # high priority
                status="pending",
                instructions=instruction,
                context_required=["prior_chapter_tail", "character_roster"],
                metadata_json={
                    "chapter_number": finding.chapter_number,
                    "dead_character": finding.character_name,
                    "evidence_count": len(finding.evidence),
                    "repair_source": "repair_xianxia_chapters",
                },
            )
            session.add(task)
            created += 1
            logger.info(
                "Created violation task for ch%d (%s)",
                finding.chapter_number, finding.character_name,
            )

    if not dry_run:
        await session.flush()
    return created


async def create_foreshadow_tasks(
    session: AsyncSession,
    project: ProjectModel,
    dry_run: bool = False,
) -> int:
    """Create RewriteTaskModel entries to add foreshadowing in early chapters."""
    created = 0

    for target in FORESHADOW_TARGETS:
        for ch_num in target.hint_chapters:
            chapter_id = await _chapter_id_for_number(session, project.id, ch_num)
            if chapter_id is None:
                logger.warning(
                    "ch%d not found in DB — skipping %s foreshadow",
                    ch_num, target.character_name,
                )
                continue

            already = await _has_existing_pending_task(
                session, project.id, chapter_id, "foreshadow_insertion"
            )
            if already:
                logger.info("ch%d: foreshadow task already exists", ch_num)
                continue

            instruction = _make_foreshadow_instruction(target, ch_num)

            if dry_run:
                print(
                    f"[DRY-RUN] Would add {target.character_name} foreshadow to ch{ch_num}"
                )
            else:
                task = RewriteTaskModel(
                    project_id=project.id,
                    trigger_type="foreshadow_insertion",
                    trigger_source_id=chapter_id,
                    rewrite_strategy="add_foreshadowing",
                    priority=5,  # lower priority than violations
                    status="pending",
                    instructions=instruction,
                    context_required=["prior_chapter_tail"],
                    metadata_json={
                        "chapter_number": ch_num,
                        "foreshadow_target": target.character_name,
                        "first_appearance": target.first_appearance_chapter,
                        "repair_source": "repair_xianxia_chapters",
                    },
                )
                session.add(task)
                created += 1
                logger.info(
                    "Created foreshadow task for ch%d (%s hint)",
                    ch_num, target.character_name,
                )

    if not dry_run:
        await session.flush()
    return created


# ---------------------------------------------------------------------------
# Execute rewrites
# ---------------------------------------------------------------------------

async def execute_rewrites(
    session: AsyncSession,
    settings: Any,
    project_slug: str,
    limit: int | None = None,
) -> dict[str, int]:
    """Execute pending rewrite tasks using the existing repair pipeline."""
    from bestseller.services.repair import run_project_repair  # noqa: PLC0415

    result = await run_project_repair(
        session,
        settings,
        project_slug,
        requested_by="repair_xianxia_chapters",
        refresh_impacts=False,
        export_markdown=True,
    )
    return {
        "chapters_attempted": result.chapters_attempted,
        "chapters_succeeded": result.chapters_succeeded,
        "chapters_failed": result.chapters_failed,
    }


# ---------------------------------------------------------------------------
# Chunk-based executor (for --limit support)
# ---------------------------------------------------------------------------

async def execute_rewrites_limited(
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    limit: int,
) -> dict[str, int]:
    """Execute up to `limit` pending rewrite tasks, highest priority first."""
    from bestseller.services.reviews import rewrite_chapter_from_task  # noqa: PLC0415

    tasks_q = (
        select(RewriteTaskModel)
        .where(
            RewriteTaskModel.project_id == project.id,
            RewriteTaskModel.status.in_(["pending", "queued"]),
        )
        .order_by(RewriteTaskModel.priority.asc(), RewriteTaskModel.created_at.asc())
        .limit(limit)
    )
    tasks = list(await session.scalars(tasks_q))

    if not tasks:
        print("No pending rewrite tasks found.")
        return {"chapters_attempted": 0, "chapters_succeeded": 0, "chapters_failed": 0}

    attempted = succeeded = failed = 0
    for task in tasks:
        # Look up chapter number from trigger_source_id
        if task.trigger_source_id is None:
            continue
        ch_num_row = await session.scalar(
            text("SELECT chapter_number FROM chapters WHERE id = :cid"),
            {"cid": str(task.trigger_source_id)},
        )
        if ch_num_row is None:
            logger.warning("Could not resolve chapter for task %s", task.id)
            continue

        chapter_number = int(ch_num_row)
        task.status = "queued"
        await session.flush()

        attempted += 1
        print(
            f"  Rewriting ch{chapter_number} "
            f"[{task.trigger_type}] ({attempted}/{len(tasks)})...",
            end=" ",
            flush=True,
        )
        try:
            draft, _task = await rewrite_chapter_from_task(
                session,
                project_slug=project.slug,
                chapter_number=chapter_number,
                rewrite_task_id=task.id,
                settings=settings,
            )
            task.status = "completed"
            await session.flush()
            print(f"✓ ({len(draft.content_md)} chars)")
            succeeded += 1
        except Exception as exc:
            task.status = "failed"
            task.error_log = str(exc)[:500]
            task.attempts += 1
            await session.flush()
            print(f"✗ {exc!s:.80}")
            logger.error("Rewrite failed ch%d: %s", chapter_number, exc)
            failed += 1

    return {
        "chapters_attempted": attempted,
        "chapters_succeeded": succeeded,
        "chapters_failed": failed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(
    *,
    dry_run: bool,
    create_tasks: bool,
    execute: bool,
    limit: int | None,
    foreshadow_only: bool,
    violation_only: bool,
) -> None:
    settings = load_settings()

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, PROJECT_SLUG)
        if project is None:
            print(f"[ERROR] Project '{PROJECT_SLUG}' not found.", file=sys.stderr)
            sys.exit(2)

        print(f"Project: 《{project.title}》 ({PROJECT_SLUG})")
        print()

        total_tasks_created = 0

        # ── Phase 1: Scan for violations ──
        if not foreshadow_only:
            print("Phase 1: Scanning chapter prose for dead-character violations...")
            findings = await _scan_dead_character_violations(session, project.id)
            print(f"  Found {len(findings)} chapters with active dead-character violations")

            if findings:
                if dry_run or create_tasks or execute:
                    created = await create_violation_tasks(
                        session, project, findings, dry_run=dry_run
                    )
                    total_tasks_created += created
                    print(f"  → Created {created} violation rewrite tasks")

        # ── Phase 2: Foreshadowing tasks ──
        if not violation_only:
            print("\nPhase 2: Creating foreshadowing tasks...")
            if dry_run or create_tasks or execute:
                created = await create_foreshadow_tasks(
                    session, project, dry_run=dry_run
                )
                total_tasks_created += created
                print(f"  → Created {created} foreshadowing rewrite tasks")

        if dry_run:
            print(f"\n[DRY-RUN COMPLETE] Would create {total_tasks_created} total tasks.")
            return

        print(f"\nTotal new tasks queued: {total_tasks_created}")

        # ── Phase 3: Execute ──
        if execute and total_tasks_created > 0 or (execute and limit):
            print("\nPhase 3: Executing rewrites (LLM calls)...")
            if limit:
                stats = await execute_rewrites_limited(
                    session, settings, project, limit=limit
                )
            else:
                stats = await execute_rewrites(session, settings, PROJECT_SLUG)

            print(
                f"\nRepair complete: {stats['chapters_succeeded']} succeeded, "
                f"{stats['chapters_failed']} failed "
                f"(of {stats['chapters_attempted']} attempted)"
            )
        elif execute:
            # No new tasks, but there might be existing pending ones
            print("\nPhase 3: Executing any existing pending tasks...")
            if limit:
                stats = await execute_rewrites_limited(
                    session, settings, project, limit=limit
                )
            else:
                stats = await execute_rewrites(session, settings, PROJECT_SLUG)
            print(
                f"\nRepair complete: {stats['chapters_succeeded']} succeeded, "
                f"{stats['chapters_failed']} failed "
                f"(of {stats['chapters_attempted']} attempted)"
            )
        else:
            print("\nTasks created. Run with --execute to start LLM rewrites.")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done, no DB writes")
    parser.add_argument("--create-tasks", action="store_true", help="Create rewrite tasks in DB but don't call LLM")
    parser.add_argument("--execute", action="store_true", help="Create tasks + call LLM rewriter")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Max chapters to rewrite per run")
    parser.add_argument("--foreshadow-only", action="store_true", help="Only do foreshadowing tasks")
    parser.add_argument("--violation-only", action="store_true", help="Only do dead-character violation tasks")
    args = parser.parse_args(argv)

    if not (args.dry_run or args.create_tasks or args.execute):
        parser.print_help()
        print("\nError: specify one of --dry-run, --create-tasks, or --execute", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        run(
            dry_run=args.dry_run,
            create_tasks=args.create_tasks,
            execute=args.execute,
            limit=args.limit,
            foreshadow_only=args.foreshadow_only,
            violation_only=args.violation_only,
        )
    )


if __name__ == "__main__":
    main()

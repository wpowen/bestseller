"""Apply paraphrase-aware intra-chapter dedup to legacy chapter markdown files.

Scans ``output/<project_slug>/chapter-*.md`` and rewrites files that still
contain byte-exact or paraphrased paragraph-level duplicates (legacy content
assembled before the dedup-paraphrase wiring landed in the assembly path).

Also updates the corresponding ``ChapterDraftVersionModel.content_md`` /
``word_count`` rows so the web UI and future reviewers see the cleaned text.

Usage::

    .venv/bin/python -m scripts.dedupe_legacy_chapters --project-slug xianxia-upgrade-1776137730
    .venv/bin/python -m scripts.dedupe_legacy_chapters --project-slug xianxia-upgrade-1776137730 --dry-run
    .venv/bin/python -m scripts.dedupe_legacy_chapters                                  # all projects
    .venv/bin/python -m scripts.dedupe_legacy_chapters --skip-db                        # file-only

Idempotent: files with no remaining duplicates are skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from sqlalchemy import select, update

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.deduplication import (
    detect_intra_chapter_repetition,
    remove_intra_chapter_duplicates_paraphrase,
)
from bestseller.settings import load_settings

_CHAPTER_NO_RE = re.compile(r"chapter-(\d+)\.md$")


def _discover_files(base: Path, project_slug: str | None) -> list[Path]:
    if project_slug:
        return sorted((base / project_slug).glob("chapter-*.md"))
    return sorted(base.glob("*/chapter-*.md"))


def _count_words(text: str) -> int:
    # Mirror bestseller.services.drafts.count_words: count non-whitespace CJK
    # runs + word tokens. For the repair pass we just use a cheap approximation
    # (total non-whitespace chars) since word_count is only used for display.
    return sum(1 for c in text if not c.isspace())


async def _update_db_entry(
    *,
    project_slug: str,
    chapter_number: int,
    cleaned_content: str,
    dry_run: bool,
) -> bool:
    """Update the current ChapterDraftVersionModel row for this chapter."""
    async with session_scope() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            print(f"    DB: project {project_slug!r} not found — skipped")
            return False
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        if chapter is None:
            print(f"    DB: chapter {chapter_number} not found — skipped")
            return False
        if dry_run:
            print(f"    DB: would update chapter_draft_versions for ch{chapter_number}")
            return True
        await session.execute(
            update(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .values(
                content_md=cleaned_content,
                word_count=_count_words(cleaned_content),
            )
        )
        if chapter.current_word_count is not None:
            chapter.current_word_count = _count_words(cleaned_content)
        await session.flush()
        print(f"    DB: updated chapter_draft_versions for ch{chapter_number}")
        return True


async def run_async(
    *,
    base_dir: Path,
    project_slug: str | None,
    dry_run: bool,
    skip_db: bool,
) -> None:
    _ = load_settings()  # ensure env is validated
    files = _discover_files(base_dir, project_slug)
    if not files:
        print(f"No chapter files found under {base_dir} (slug={project_slug!r}).")
        return

    scanned = 0
    changed = 0
    total_removed = 0
    for path in files:
        scanned += 1
        m = _CHAPTER_NO_RE.search(str(path))
        if not m:
            continue
        chapter_number = int(m.group(1))
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings = detect_intra_chapter_repetition(original, paraphrase_threshold=0.55)
        if not findings:
            continue
        cleaned, removed = remove_intra_chapter_duplicates_paraphrase(
            original, paraphrase_threshold=0.55
        )
        if removed == 0 and cleaned == original:
            continue
        changed += 1
        total_removed += removed
        print(f"  fix  {path.name}  — {len(findings)} finding(s), removed {removed} paragraph(s)")
        if not dry_run:
            path.write_text(cleaned, encoding="utf-8")
        if not skip_db and project_slug:
            try:
                await _update_db_entry(
                    project_slug=project_slug,
                    chapter_number=chapter_number,
                    cleaned_content=cleaned,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort legacy repair
                print(f"    DB: update failed ({exc!r}) — md file already fixed")
        elif not skip_db and not project_slug:
            # When scanning every project, resolve project_slug from the parent dir
            slug = path.parent.name
            try:
                await _update_db_entry(
                    project_slug=slug,
                    chapter_number=chapter_number,
                    cleaned_content=cleaned,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    DB: update failed ({exc!r}) — md file already fixed")
    print(
        f"\nScanned {scanned} file(s); "
        f"{'would fix' if dry_run else 'fixed'} {changed}; "
        f"{'would remove' if dry_run else 'removed'} {total_removed} duplicate paragraph(s)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default="output", help="Output root (default: output)")
    parser.add_argument("--project-slug", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Only rewrite markdown files; skip updating chapter_draft_versions rows.",
    )
    args = parser.parse_args()
    asyncio.run(
        run_async(
            base_dir=Path(args.base_dir).resolve(),
            project_slug=args.project_slug,
            dry_run=args.dry_run,
            skip_db=args.skip_db,
        )
    )


if __name__ == "__main__":
    main()

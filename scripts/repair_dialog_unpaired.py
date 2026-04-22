"""Repair DIALOG_UNPAIRED findings with precise text fixes.

Symptom: chapters flagged by ``DialogIntegrityCheck`` with either
    * "Odd count (N) of straight_double quotes" — parity violation on ``"``.
    * "Paragraph P: unclosed curly_double quote opened at offset 0" —
      unmatched ``\u201c`` with no ``\u201d`` in the rest of the chapter.

Root cause: LLM drafting sometimes emits a non-quote character where the
closing quote should be (e.g. ``"...you built?*``), or emits only the
opening curly ``\u201c`` but forgets the matching ``\u201d`` at the end of
the dialogue block.

This script:

1. Loads current ``ChapterDraftVersionModel`` rows for the project.
2. Runs ``DialogIntegrityCheck`` on each chapter to locate paragraphs with
   odd-count ``"`` or chapters with globally unclosed ``\u201c``.
3. Applies targeted heuristics per pattern:
    * ``?*`` / ``.*`` / ``!*`` at end of a dialogue-looking sentence →
      replace ``*`` with ``"`` (closing quote).
    * Paragraph ends with ``"...`` and no matching close → append ``"``.
    * ``\u201c`` opened at para 0 with no ``\u201d`` anywhere → append
      ``\u201d`` at the end of the last non-whitespace line in that
      paragraph.
4. Writes a NEW ``ChapterDraftVersionModel`` for each repaired chapter
   (bumps ``version_no``, sets ``is_current=True``, demotes the previous).
5. Respects LOCKED chapters: skips ``xianxia-upgrade-1776137730`` chapters
   <= 212 entirely (those are externally published).

Usage::

    uv run python -m scripts.repair_dialog_unpaired \\
        --project-slug superhero-fiction-1776147970 --dry-run    # default

    uv run python -m scripts.repair_dialog_unpaired \\
        --project-slug superhero-fiction-1776147970 --apply

Idempotent: re-running a successful repair reports "already balanced".
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.chapter_validator import DialogIntegrityCheck
from bestseller.services.invariants import (
    invariants_from_dict,
    seed_invariants,
)
from bestseller.services.output_validator import ValidationContext
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import load_settings


# Chapters at or below this number on this slug are externally published
# and must not be modified. The constraint comes from the user — any change
# here requires explicit sign-off.
LOCKED_PROJECT_CEILINGS: dict[str, int] = {
    "xianxia-upgrade-1776137730": 212,
}


@dataclass(frozen=True)
class RepairCandidate:
    chapter_id: UUID
    chapter_number: int
    content_md: str
    version_id: UUID
    version_no: int


@dataclass(frozen=True)
class RepairAction:
    pattern: str  # "stray_asterisk" | "missing_close_at_para_end" | "missing_close_corner"
    before_snippet: str
    after_snippet: str


@dataclass
class RepairResult:
    chapter_number: int
    applied: list[RepairAction]
    still_unbalanced_after: bool
    new_content: str


# Patterns that represent a dialogue-ending character followed by a typo
# character where the closing quote should go. Order matters — we want the
# most specific (with leading sentence-ending punctuation) first.
_STRAY_CLOSE_PATTERNS: tuple[tuple[str, str], ...] = (
    # "...question?*"  → "...question?""
    (r'([?.!])\*(?=\s|$)', r'\1"'),
    # "...word.*"  after visible letters (conservative)
    # Already covered above; kept separate line in case we add more.
)

_CORNER_OPEN = "\u300c"
_CORNER_CLOSE = "\u300d"
_CURLY_DOUBLE_OPEN = "\u201c"
_CURLY_DOUBLE_CLOSE = "\u201d"


async def _load_candidates(session, project_id: UUID) -> list[RepairCandidate]:
    rows = (
        await session.execute(
            select(
                ChapterModel.id,
                ChapterModel.chapter_number,
                ChapterDraftVersionModel.id,
                ChapterDraftVersionModel.version_no,
                ChapterDraftVersionModel.content_md,
            )
            .join(
                ChapterDraftVersionModel,
                ChapterModel.id == ChapterDraftVersionModel.chapter_id,
            )
            .where(
                ChapterModel.project_id == project_id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterModel.chapter_number.asc())
        )
    ).all()
    return [
        RepairCandidate(
            chapter_id=r[0],
            chapter_number=r[1],
            version_id=r[2],
            version_no=r[3],
            content_md=r[4] or "",
        )
        for r in rows
    ]


def _build_ctx(project: ProjectModel) -> ValidationContext:
    payload = project.invariants_json
    if payload:
        inv = invariants_from_dict(payload)
    else:
        chapters = max(int(project.target_chapters or 0), 1)
        total_words = max(int(project.target_word_count or 0), 0)
        per_chapter = total_words // chapters if total_words else 6400
        words = SimpleNamespace(
            min=int(per_chapter * 0.7) or 2000,
            target=per_chapter or 6400,
            max=int(per_chapter * 1.3) or 8000,
        )
        inv = seed_invariants(
            project_id=project.id,
            language=project.language or "en",
            words_per_chapter=words,
        )
    return ValidationContext(invariants=inv, chapter_no=1)


def _has_dialog_issue(text: str, ctx: ValidationContext) -> bool:
    violations = list(DialogIntegrityCheck().run(text, ctx))
    return bool(violations)


def _repair_stray_asterisks(text: str) -> tuple[str, list[RepairAction]]:
    """Replace ``[?.!]*`` patterns with ``[?.!]"`` when the paragraph has
    odd ``"`` count. Conservative: only touches paragraphs that are
    actually unbalanced, to avoid turning incidental ``*`` elsewhere into
    quotes."""

    applied: list[RepairAction] = []
    paragraphs = text.split("\n\n")
    changed = False
    for idx, para in enumerate(paragraphs):
        if para.count('"') % 2 != 1:
            continue
        new_para = para
        for pattern, repl in _STRAY_CLOSE_PATTERNS:
            def _sub(m: re.Match[str]) -> str:
                return m.group(1) + '"'

            candidate = re.sub(pattern, _sub, new_para)
            if candidate != new_para:
                # Record the first change's neighborhood for audit.
                m = re.search(pattern, new_para)
                if m:
                    lo = max(m.start() - 20, 0)
                    hi = min(m.end() + 20, len(new_para))
                    applied.append(
                        RepairAction(
                            pattern="stray_asterisk",
                            before_snippet=new_para[lo:hi],
                            after_snippet=candidate[lo:hi],
                        )
                    )
                new_para = candidate
                changed = True
        if new_para != para:
            paragraphs[idx] = new_para
    return ("\n\n".join(paragraphs) if changed else text, applied)


def _repair_trailing_missing_close(text: str) -> tuple[str, list[RepairAction]]:
    """Append ``"`` to paragraphs whose final open quote has no matching
    close before paragraph end. We only touch paragraphs with odd total
    count, and we only act when the last ``"`` in the paragraph is an
    opener (i.e. the paragraph ends after dialogue content, not before it).
    """

    applied: list[RepairAction] = []
    paragraphs = text.split("\n\n")
    changed = False
    for idx, para in enumerate(paragraphs):
        if para.count('"') % 2 != 1:
            continue
        # If we already ran stray_asterisk repair and it balanced the para,
        # this will be a no-op (odd check above short-circuits).
        # Walk the para with a parity counter. If parity is 1 at EOF, append.
        parity = 0
        for ch in para:
            if ch == '"':
                parity = 1 - parity
        if parity != 1:
            continue  # Shouldn't happen given the odd check, defensive.
        stripped = para.rstrip()
        if not stripped:
            continue
        trailing = para[len(stripped):]

        # If the last visible character is a stray ``*`` where the closing
        # quote should be, REPLACE rather than append. Otherwise append.
        if stripped[-1] == "*":
            rebuilt = stripped[:-1] + '"' + trailing
            pattern = "replace_trailing_asterisk_with_close"
        else:
            rebuilt = stripped + '"' + trailing
            pattern = "append_close_straight_double"
        lo = max(len(stripped) - 40, 0)
        applied.append(
            RepairAction(
                pattern=pattern,
                before_snippet=para[lo:],
                after_snippet=rebuilt[lo:],
            )
        )
        paragraphs[idx] = rebuilt
        changed = True
    return ("\n\n".join(paragraphs) if changed else text, applied)


def _repair_unmatched_corner(text: str) -> tuple[str, list[RepairAction]]:
    """If ``\u201c`` count > ``\u201d`` count and the last paragraph with a
    lone ``\u201c`` ends without ``\u201d``, append one at the last
    non-whitespace character of that paragraph.
    """

    opens = text.count(_CURLY_DOUBLE_OPEN)
    closes = text.count(_CURLY_DOUBLE_CLOSE)
    if opens <= closes:
        return text, []
    # Walk paragraphs, find the first one whose running open count exceeds
    # its running close count at paragraph boundary. That's where the
    # unclosed quote lives.
    paragraphs = text.split("\n\n")
    running_open = 0
    running_close = 0
    for idx, para in enumerate(paragraphs):
        running_open += para.count(_CURLY_DOUBLE_OPEN)
        running_close += para.count(_CURLY_DOUBLE_CLOSE)
        if running_open > running_close and para.count(_CURLY_DOUBLE_OPEN) > para.count(
            _CURLY_DOUBLE_CLOSE
        ):
            # Append close at end of paragraph, before trailing whitespace.
            stripped = para.rstrip()
            trailing = para[len(stripped):]
            new_para = stripped + _CURLY_DOUBLE_CLOSE + trailing
            lo = max(len(stripped) - 30, 0)
            applied = [
                RepairAction(
                    pattern="missing_close_curly_double",
                    before_snippet=para[lo : len(stripped)],
                    after_snippet=new_para[lo : len(stripped) + 1],
                )
            ]
            paragraphs[idx] = new_para
            return "\n\n".join(paragraphs), applied
    return text, []


def _repair_unmatched_corner_bracket(text: str) -> tuple[str, list[RepairAction]]:
    """Same as ``_repair_unmatched_corner`` but for 「 / 」."""

    opens = text.count(_CORNER_OPEN)
    closes = text.count(_CORNER_CLOSE)
    if opens <= closes:
        return text, []
    paragraphs = text.split("\n\n")
    running_open = 0
    running_close = 0
    for idx, para in enumerate(paragraphs):
        running_open += para.count(_CORNER_OPEN)
        running_close += para.count(_CORNER_CLOSE)
        if running_open > running_close and para.count(_CORNER_OPEN) > para.count(
            _CORNER_CLOSE
        ):
            stripped = para.rstrip()
            trailing = para[len(stripped):]
            new_para = stripped + _CORNER_CLOSE + trailing
            lo = max(len(stripped) - 30, 0)
            applied = [
                RepairAction(
                    pattern="missing_close_corner",
                    before_snippet=para[lo : len(stripped)],
                    after_snippet=new_para[lo : len(stripped) + 1],
                )
            ]
            paragraphs[idx] = new_para
            return "\n\n".join(paragraphs), applied
    return text, []


def _repair_content(
    content: str, ctx: ValidationContext
) -> RepairResult:
    if not _has_dialog_issue(content, ctx):
        return RepairResult(
            chapter_number=ctx.chapter_no,
            applied=[],
            still_unbalanced_after=False,
            new_content=content,
        )

    actions: list[RepairAction] = []
    current = content

    # 1) ASCII stray asterisks → straight close.
    current, applied = _repair_stray_asterisks(current)
    actions.extend(applied)

    # 2) Paragraphs that simply forget the final ``"`` — append one.
    current, applied = _repair_trailing_missing_close(current)
    actions.extend(applied)

    # 3) Curly double unmatched opener.
    current, applied = _repair_unmatched_corner(current)
    actions.extend(applied)

    # 4) Corner bracket unmatched opener (zh-CN).
    current, applied = _repair_unmatched_corner_bracket(current)
    actions.extend(applied)

    still_bad = _has_dialog_issue(current, ctx)
    return RepairResult(
        chapter_number=ctx.chapter_no,
        applied=actions,
        still_unbalanced_after=still_bad,
        new_content=current,
    )


async def _apply_repair(
    session,
    candidate: RepairCandidate,
    new_content: str,
) -> None:
    # Demote current version and insert a new row with is_current=True.
    # This matches the pipeline's existing versioning convention.
    existing = (
        await session.execute(
            select(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.id == candidate.version_id)
        )
    ).scalar_one()
    existing.is_current = False

    next_version = candidate.version_no + 1
    new_row = ChapterDraftVersionModel(
        project_id=existing.project_id,
        chapter_id=existing.chapter_id,
        version_no=next_version,
        content_md=new_content,
        word_count=existing.word_count,
        assembled_from_scene_draft_ids=list(existing.assembled_from_scene_draft_ids or []),
        is_current=True,
        llm_run_id=existing.llm_run_id,
    )
    session.add(new_row)


async def run(slug: str, *, apply: bool) -> int:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            print(f"[dialog-repair] project '{slug}' not found", file=sys.stderr)
            return 2

        ceiling = LOCKED_PROJECT_CEILINGS.get(slug)
        candidates = await _load_candidates(session, project.id)
        ctx_base = _build_ctx(project)

        repairable = 0
        untouchable_locked = 0
        unresolved = 0
        touched_ids: list[UUID] = []

        print(f"[dialog-repair] {slug}: scanning {len(candidates)} chapters")

        for cand in candidates:
            if ceiling is not None and cand.chapter_number <= ceiling:
                if _has_dialog_issue(
                    cand.content_md,
                    ValidationContext(
                        invariants=ctx_base.invariants,
                        chapter_no=cand.chapter_number,
                    ),
                ):
                    untouchable_locked += 1
                    print(
                        f"  ch-{cand.chapter_number:03d}: SKIP — locked "
                        f"(<= {ceiling})"
                    )
                continue

            ctx = ValidationContext(
                invariants=ctx_base.invariants, chapter_no=cand.chapter_number
            )
            result = _repair_content(cand.content_md, ctx)
            if not result.applied and not result.still_unbalanced_after:
                # Nothing to fix.
                continue
            if not result.applied and result.still_unbalanced_after:
                # Detected but heuristics didn't match any known pattern.
                unresolved += 1
                print(
                    f"  ch-{cand.chapter_number:03d}: UNRESOLVED — issue detected "
                    f"but no heuristic matched"
                )
                continue

            repairable += 1
            print(
                f"  ch-{cand.chapter_number:03d}: {len(result.applied)} fix(es)"
                + (" (still unbalanced after!)" if result.still_unbalanced_after else "")
            )
            for a in result.applied:
                print(f"    [{a.pattern}] {a.before_snippet!r} → {a.after_snippet!r}")

            if apply and not result.still_unbalanced_after:
                await _apply_repair(session, cand, result.new_content)
                touched_ids.append(cand.version_id)

        if apply:
            await session.commit()
        print()
        print(f"[dialog-repair] summary for {slug}:")
        print(f"  chapters scanned:   {len(candidates)}")
        print(f"  repairable:         {repairable}")
        print(f"  locked (skipped):   {untouchable_locked}")
        print(f"  unresolved:         {unresolved}")
        print(f"  applied (new rows): {len(touched_ids) if apply else 0}")
        if not apply:
            print("  NOTE: --apply not set; no DB writes were made.")
        return 0 if (repairable + untouchable_locked + unresolved) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true", default=False)
    args = parser.parse_args()
    return asyncio.run(run(args.project_slug, apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())

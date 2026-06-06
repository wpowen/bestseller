#!/usr/bin/env python
"""Per-chapter validation report for a generated book.

Runs the deterministic scene-grounding detectors (A authorial-intrusion,
B grounding-coverage, C proper-noun flood) plus the common-sense gate across
every current chapter draft, and prints a compact table + aggregate summary.

Usage: .venv/bin/python scripts/_validate_book_quality.py [SLUG]
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from bestseller.infra.db.session import session_scope
from bestseller.services.common_sense_gate import evaluate_common_sense_gate
from bestseller.services.quality_levers.scene_grounding import audit_scene_grounding


async def main(slug: str) -> int:
    async with session_scope() as s:
        row = (
            await s.execute(
                text("select id, genre, sub_genre from projects where slug=:slug"),
                {"slug": slug},
            )
        ).first()
        if row is None:
            print(f"no project for slug={slug}")
            return 1
        pid, genre, sub_genre = row
        chapters = (
            await s.execute(
                text(
                    """
                    select c.chapter_number, c.status, c.production_state,
                           cdv.word_count, cdv.content_md
                    from chapters c
                    left join chapter_draft_versions cdv
                      on cdv.chapter_id=c.id and cdv.is_current=true
                    where c.project_id=:pid
                    order by c.chapter_number
                    """
                ),
                {"pid": pid},
            )
        ).fetchall()

    print(f"\n=== {slug}  (genre={genre} / {sub_genre}) ===")
    print(
        f"{'ch':>3} {'status':>10} {'prod':>14} {'words':>6} "
        f"{'A_intr':>7} {'A?':>3} {'Bcov':>5} {'B?':>3} {'CS?':>4} grounding"
    )
    drafted = 0
    a_pass = b_pass = cs_pass = grounding_pass = 0
    a_values: list[float] = []
    for ch_no, status, prod, words, md in chapters:
        if not md:
            print(f"{ch_no:>3} {str(status):>10} {str(prod):>14} {'--':>6}  (no draft)")
            continue
        drafted += 1
        audit = audit_scene_grounding(md)
        cs = evaluate_common_sense_gate(
            md, genre=genre, sub_genre=sub_genre, chapter_number=ch_no
        )
        a_d = audit.intrusion.density_per_kchars
        a_values.append(a_d)
        a_ok = audit.intrusion.passed
        b_ok = audit.coverage.passed
        a_pass += a_ok
        b_pass += b_ok
        cs_pass += cs.passed
        grounding_pass += audit.passed
        print(
            f"{ch_no:>3} {str(status):>10} {str(prod):>14} {words or 0:>6} "
            f"{a_d:>7.2f} {'OK' if a_ok else 'XX':>3} "
            f"{audit.coverage.coverage:>5.2f} {'OK' if b_ok else 'XX':>3} "
            f"{'OK' if cs.passed else 'XX':>4} "
            f"{'PASS' if audit.passed else 'fail'}"
        )

    print("\n--- summary ---")
    print(f"chapters drafted: {drafted}/{len(chapters)}")
    if drafted:
        print(
            f"A authorial-intrusion pass: {a_pass}/{drafted}  "
            f"(mean density {sum(a_values)/len(a_values):.2f}, "
            f"max {max(a_values):.2f}; lower is better)"
        )
        print(f"B grounding-coverage pass: {b_pass}/{drafted}")
        print(f"scene-grounding aggregate (A&B) pass: {grounding_pass}/{drafted}")
        print(f"common-sense gate pass: {cs_pass}/{drafted}")
    return 0


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "memory-pawn-v3"
    raise SystemExit(asyncio.run(main(slug)))

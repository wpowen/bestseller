"""Sync every project's invariants_json.length_envelope to the canonical
1800/2200/3000 chapter envelope.

Pipeline gates (L4 length stability, L2 bible audit) read the envelope from
``projects.invariants_json``, NOT from ``config/default.yaml``. Projects
created before the envelope rule was finalized still hold their original
values, which causes regenerated chapters to be flagged as "too short" or
"too long" against the wrong target. This is a one-shot reconciliation.

Idempotent: re-running on already-synced rows is a no-op.

Usage:
    uv run python scripts/sync_chapter_envelope_to_invariants.py            # dry-run
    uv run python scripts/sync_chapter_envelope_to_invariants.py --apply    # write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.bestseller.settings import load_settings


CANONICAL_ENVELOPE = {
    "min_chars": 1800,
    "target_chars": 2200,
    "max_chars": 3000,
}


async def main(apply: bool) -> int:
    os.chdir(Path(__file__).resolve().parents[1])
    settings = load_settings()

    engine = create_async_engine(settings.database.url, echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    drift = []
    async with sm() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, slug,
                       invariants_json->'length_envelope'->>'min_chars'   AS min_c,
                       invariants_json->'length_envelope'->>'target_chars' AS tgt_c,
                       invariants_json->'length_envelope'->>'max_chars'   AS max_c
                FROM projects
                WHERE invariants_json IS NOT NULL
                ORDER BY created_at
                """
            )
        )
        for row in rows:
            current = {
                "min_chars": int(row.min_c) if row.min_c else None,
                "target_chars": int(row.tgt_c) if row.tgt_c else None,
                "max_chars": int(row.max_c) if row.max_c else None,
            }
            if current != CANONICAL_ENVELOPE:
                drift.append((row.id, row.slug, current))

        if not drift:
            print("All projects already match canonical envelope. Nothing to do.")
            return 0

        print(f"Projects to update: {len(drift)}")
        for pid, slug, current in drift:
            cur_str = f"{current['min_chars']}/{current['target_chars']}/{current['max_chars']}"
            tgt_str = (
                f"{CANONICAL_ENVELOPE['min_chars']}/"
                f"{CANONICAL_ENVELOPE['target_chars']}/"
                f"{CANONICAL_ENVELOPE['max_chars']}"
            )
            print(f"  {slug:<40s}  {cur_str}  →  {tgt_str}")

        if not apply:
            print("\nDry-run only. Pass --apply to write.")
            return 0

        # jsonb_set on each leaf so the rest of invariants_json is preserved.
        envelope_json = json.dumps(CANONICAL_ENVELOPE)
        await session.execute(
            text(
                """
                UPDATE projects
                SET invariants_json = jsonb_set(
                    invariants_json,
                    '{length_envelope}',
                    CAST(:envelope AS JSONB),
                    true
                )
                WHERE id = ANY(:ids)
                """
            ),
            {"envelope": envelope_json, "ids": [pid for pid, _, _ in drift]},
        )
        await session.commit()
        print(f"\nUpdated {len(drift)} project(s).")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))

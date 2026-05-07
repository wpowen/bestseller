"""Reset chapters 1-50 and their scene cards to trigger pipeline regeneration."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.bestseller.settings import load_settings


async def main() -> int:
    os.chdir(Path(__file__).resolve().parents[1])
    settings = load_settings()
    engine = create_async_engine(settings.database.url, echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        pid = "d925d6ee-68a1-4ee0-80b5-7bafe902d0be"

        # Show before state
        result = await session.execute(
            text(
                """SELECT status, production_state, count(*)
                   FROM chapters
                   WHERE project_id = :pid AND chapter_number BETWEEN 1 AND 50
                   GROUP BY status, production_state
                   ORDER BY status, production_state"""
            ),
            {"pid": pid},
        )
        print("Chapter state BEFORE reset:")
        for r in result:
            print(f"  status={r[0]} production_state={r[1]} count={r[2]}")

        result = await session.execute(
            text(
                """SELECT sc.status, count(*)
                   FROM scene_cards sc
                   JOIN chapters c ON sc.chapter_id = c.id
                   WHERE c.project_id = :pid AND c.chapter_number BETWEEN 1 AND 50
                   GROUP BY sc.status"""
            ),
            {"pid": pid},
        )
        print("Scene card state BEFORE reset:")
        for r in result:
            print(f"  status={r[0]} count={r[1]}")

        # Reset chapters
        await session.execute(
            text(
                """UPDATE chapters
                   SET status = 'revision', production_state = 'pending'
                   WHERE project_id = :pid AND chapter_number BETWEEN 1 AND 50"""
            ),
            {"pid": pid},
        )
        await session.flush()

        # Reset scene cards
        await session.execute(
            text(
                """UPDATE scene_cards
                   SET status = 'needs_rewrite'
                   WHERE chapter_id IN (
                     SELECT id FROM chapters
                     WHERE project_id = :pid AND chapter_number BETWEEN 1 AND 50
                   )"""
            ),
            {"pid": pid},
        )
        await session.flush()

        # Show after state
        result = await session.execute(
            text(
                """SELECT status, production_state, count(*)
                   FROM chapters
                   WHERE project_id = :pid AND chapter_number BETWEEN 1 AND 50
                   GROUP BY status, production_state"""
            ),
            {"pid": pid},
        )
        print("\nChapter state AFTER reset:")
        for r in result:
            print(f"  status={r[0]} production_state={r[1]} count={r[2]}")

        result = await session.execute(
            text(
                """SELECT sc.status, count(*)
                   FROM scene_cards sc
                   JOIN chapters c ON sc.chapter_id = c.id
                   WHERE c.project_id = :pid AND c.chapter_number BETWEEN 1 AND 50
                   GROUP BY sc.status"""
            ),
            {"pid": pid},
        )
        print("Scene card state AFTER reset:")
        for r in result:
            print(f"  status={r[0]} count={r[1]}")

        await session.commit()
        print("\nDB reset committed successfully.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

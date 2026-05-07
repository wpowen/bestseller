"""Fix chapter and scene target_word_count to match the 1800-3000 length envelope.

The pipeline planned chapters with 4 scenes × ~1600 words = 6400 total, but the
L4/L6 length envelope requires 1800-3000 Chinese characters per chapter.
This mismatch causes EVERY chapter to fail the length check.

Fix: set chapter target to 2200 and distribute across scenes (~550 each).
"""

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

        # 1. Reset chapters 1-50 again (pipeline may have changed some state)
        await session.execute(
            text(
                """UPDATE chapters
                   SET status = 'revision', production_state = 'pending',
                       target_word_count = 2200
                   WHERE project_id = :pid AND chapter_number BETWEEN 1 AND 50"""
            ),
            {"pid": pid},
        )

        # 2. Update scene targets: distribute 2200 across 4 scenes
        # Target ~600 each to leave room for assembly overhead (4 × 600 = 2400 → dedup → ~2200)
        scene_targets = {
            1: 600,
            2: 600,
            3: 600,
            4: 600,
        }
        for scene_num, target in scene_targets.items():
            await session.execute(
                text(
                    """UPDATE scene_cards
                       SET status = 'needs_rewrite', target_word_count = :target
                       WHERE chapter_id IN (
                         SELECT id FROM chapters
                         WHERE project_id = :pid AND chapter_number BETWEEN 1 AND 50
                       ) AND scene_number = :scene_num"""
                ),
                {"pid": pid, "target": target, "scene_num": scene_num},
            )

        await session.flush()

        # Verify
        result = await session.execute(
            text(
                """SELECT c.chapter_number, c.target_word_count as ch_target,
                          sc.scene_number, sc.target_word_count as sc_target, sc.status
                   FROM chapters c
                   JOIN scene_cards sc ON sc.chapter_id = c.id
                   WHERE c.project_id = :pid AND c.chapter_number <= 5
                   ORDER BY c.chapter_number, sc.scene_number"""
            ),
            {"pid": pid},
        )
        print("After fix (first 5 chapters):")
        for r in result:
            print(
                f"  ch{r[0]:03d}: ch_target={r[1]} "
                f"sc{r[2]}: sc_target={r[3]} status={r[4]}"
            )

        await session.commit()
        print("\nTarget fix committed successfully.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

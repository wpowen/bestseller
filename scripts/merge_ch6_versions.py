"""合并第6章——取旧版本的正确开头 + 新版本的正确结尾，生成完整章节。

旧版本 90d8a96a (4773字): 开头正确，但结尾有苏瑶死亡 + "陆沉死前"
新版本 3a2f0f5a (2617字): 只有结尾部分（从"坠落感再次袭来"开始），无死亡错误

合并策略: 取旧版本中"坠落感再次袭来"之前的所有内容 + 新版本全部内容
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select

from bestseller.infra.db.models import ChapterDraftVersionModel
from bestseller.infra.db.session import session_scope

OLD_VERSION_ID = "90d8a96a-7578-49db-9e25-97236ec59b60"  # 4773 chars, correct beginning
NEW_ENDING_ID = "3a2f0f5a-68af-4ba2-a117-b2a5950a6161"   # 2617 chars, correct ending (current)

SPLIT_MARKER = "坠落感再次袭来"  # Where old version has the wrong ending; new version starts here


async def run() -> None:
    async with session_scope() as session:
        old_ver = await session.get(ChapterDraftVersionModel, OLD_VERSION_ID)
        new_ending = await session.get(ChapterDraftVersionModel, NEW_ENDING_ID)

        if old_ver is None or new_ending is None:
            print("ERROR: version not found")
            return

        # Split old content at the correct split point
        split_idx = old_ver.content_md.find(SPLIT_MARKER)
        if split_idx == -1:
            print(f"ERROR: split marker '{SPLIT_MARKER}' not found in old version")
            return

        beginning = old_ver.content_md[:split_idx].rstrip()
        ending = new_ending.content_md.strip()

        merged = beginning + "\n\n" + ending
        print(f"Beginning: {len(beginning)} chars")
        print(f"Ending: {len(ending)} chars")
        print(f"Merged: {len(merged)} chars")

        # Verify sanity checks
        if "陆沉死前" in merged:
            print("⚠️  WARNING: '陆沉死前' found in merged content!")
        else:
            print("✓ '陆沉死前' not present — OK")

        if "缓缓倒下" in merged:
            # Check context
            idx = merged.find("缓缓倒下")
            ctx = merged[max(0, idx-50):idx+50]
            print(f"⚠️  '缓缓倒下' found: ...{ctx}...")
        else:
            print("✓ '缓缓倒下' not present — OK")

        # Set all versions to not current
        from sqlalchemy import update
        from bestseller.infra.db.models import ChapterDraftVersionModel as CDV
        await session.execute(
            update(CDV)
            .where(CDV.chapter_id == old_ver.chapter_id)
            .values(is_current=False)
        )

        # Create new merged version
        merged_ver = ChapterDraftVersionModel(
            chapter_id=old_ver.chapter_id,
            content_md=merged,
            is_current=True,
        )
        session.add(merged_ver)
        await session.flush()

        print(f"✓ Created merged version {merged_ver.id} ({len(merged)} chars)")
        print("  ch6 is now current with full content + correct ending")


if __name__ == "__main__":
    asyncio.run(run())

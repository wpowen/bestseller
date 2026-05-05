"""一次性修复第6章末尾的死亡错误。

问题：第6章末尾苏瑶被叶长青击倒（她死于第435章），且末句"陆沉死前"
暗示陆沉已死（他死于第458章）。

修复：重写第6章，保留所有好的叙事结构，但修复结尾：
- 叶长青出现但不杀苏瑶
- 苏瑶被捉或逃脱但存活
- 删除"陆沉死前"末句
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel
from bestseller.infra.db.session import session_scope
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import load_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fix_ch6_deaths")

PROJECT_SLUG = "xianxia-upgrade-1776137730"
CHAPTER_NUMBER = 6
REPAIR_SOURCE = "fix_ch6_deaths_v1"

FIX_INSTRUCTION = """【第6章 死亡错误修复指令】

⚠️ 严格约束（绝对不可违反）：
- 苏瑶在本章中绝对不能死亡、不能被杀死、不能"缓缓倒下"死去——她的死亡发生在第435章，本章她必须存活。
- 陆沉在本章中绝对不能死亡，也不能有任何暗示他已死亡的文字——他的死亡发生在第458章，本章他必须存活。
- 绝对删除"陆沉死前，到底看到了什么？"这句话，以及任何类似暗示陆沉已死的表述。

保留好的叙事结构（完整保留以下内容）：
1. 宁尘进入禁地、陆沉给玉简和佩剑、陆沉安全离开
2. 宁尘在沉渊谷石壁上发现阴阳道典符文
3. 苏瑶追来、二人在深渊裂缝边的对峙
4. 宁尘松手坠落、在石室中苏醒
5. 苏瑶出现在石室门口（她是靠叶长青令牌进来的）
6. 叶长青出现

修复方案——重写第6章结尾（叶长青到来之后的部分）：
叶长青出现后，他审视宁尘，确认道种的价值。转向苏瑶时，语气依然冷厉，但他的目的是控制而非杀戮——苏瑶对他仍有利用价值（她知道如何激活令牌）。叶长青用一道禁制封住了苏瑶的经脉，让她无法反抗，但她是活着被控制的，不是死亡。

章节结尾改写方向：
- 宁尘躺在石室地面，看着叶长青站在石室中央，意识到自己落入了对方的局
- 苏瑶被禁制封锁经脉，倚着石壁无法动弹，但眼神仍然锐利
- 宁尘意识到：陆沉的玉简、苏瑶的令牌、他的道种——叶长青早就在谋划这一切
- 以宁尘的某个内心感悟或外部变化收尾，留下下一章的悬念钩子

新结尾不得超过原来结尾的篇幅，保持全章字数与优化前大体相当。
保持文风流畅、符合仙侠升级流的节奏感。"""


async def run() -> None:
    settings = load_settings()

    async with session_scope() as session:
        project = await get_project_by_slug(session, PROJECT_SLUG)
        if project is None:
            print(f"ERROR: project {PROJECT_SLUG!r} not found")
            return

        # Look up the chapter record so we can set trigger_source_id correctly
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == CHAPTER_NUMBER,
            )
        )
        if chapter is None:
            print(f"ERROR: chapter {CHAPTER_NUMBER} not found")
            return

        # Check for existing pending task
        existing = await session.scalar(
            select(RewriteTaskModel).where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.metadata_json["repair_source"].as_string() == REPAIR_SOURCE,
                RewriteTaskModel.status.in_(["pending", "queued"]),
            )
        )
        if existing:
            task = existing
            print(f"Found existing pending task {task.id}")
        else:
            task = RewriteTaskModel(
                project_id=project.id,
                trigger_type="manual_fix",
                trigger_source_id=chapter.id,
                rewrite_strategy="targeted_fix",
                priority=1,
                status="pending",
                instructions=FIX_INSTRUCTION,
                metadata_json={
                    "chapter_number": CHAPTER_NUMBER,
                    "repair_source": REPAIR_SOURCE,
                },
            )
            session.add(task)
            await session.flush()
            print(f"Created fix task {task.id}")

        # Execute the rewrite
        from bestseller.services.reviews import rewrite_chapter_from_task  # noqa: PLC0415

        task.status = "queued"
        await session.flush()

        print(f"Rewriting ch{CHAPTER_NUMBER} with death-fix constraints...", flush=True)
        try:
            draft, _ = await rewrite_chapter_from_task(
                session,
                project_slug=project.slug,
                chapter_number=CHAPTER_NUMBER,
                rewrite_task_id=task.id,
                settings=settings,
            )
            task.status = "completed"
            await session.flush()
            print(f"✓ Done — {len(draft.content_md):,} chars written")
            # Quick sanity check
            if "陆沉死前" in draft.content_md:
                print("⚠️  WARNING: '陆沉死前' still found in output!")
            else:
                print("✓ '陆沉死前' not present — OK")
            if "缓缓倒下" in draft.content_md and "苏瑶" in draft.content_md:
                print("⚠️  WARNING: '苏瑶...缓缓倒下' pattern may still be present")
            else:
                print("✓ 苏瑶 fall pattern not detected — OK")
        except Exception as exc:
            await session.rollback()
            task.status = "failed"
            task.error_log = str(exc)[:500]
            await session.flush()
            print(f"✗ Failed: {exc}")
            raise


if __name__ == "__main__":
    asyncio.run(run())

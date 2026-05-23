"""Repair gate-exposed integrity defects for 《青囊不语问阴阳》.

The commercial gate surfaced stitched openings, raw manuscript separators, weak
golden-three hooks, and chapter seam drops. This script turns those concrete
findings into chapter-level rewrite tasks, executes them through the existing
chapter editor, and exports the repaired Markdown files for package re-audit.

Usage:
    uv run python scripts/repair_qingnang_integrity_findings.py --execute
    uv run python scripts/repair_qingnang_integrity_findings.py --execute --chapter 1
"""

from __future__ import annotations

# ruff: noqa: E501, RUF001
import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.drafts import (  # noqa: E402
    format_chapter_heading,
    sanitize_novel_markdown_content,
)
from bestseller.services.exports import write_markdown_output  # noqa: E402
from bestseller.services.projects import get_project_by_slug  # noqa: E402
from bestseller.services.reviews import rewrite_chapter_from_task  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
REPAIR_SOURCE = "qingnang_integrity_gate_20260522"

GLOBAL_INSTRUCTION = """【全局修复原则】
这次不是扩写设定，而是修复留存断崖暴露出的拼接感和一致性问题。
1. 保留《青囊不语问阴阳》的主角林渊、青囊秘卷、镜账/认账/否认者主线，不改核心事件因果。
2. 每章必须像同一场戏自然推进：不要出现两个互相独立的开头、不要使用“---”等草稿拼接符、不要突然切换未铺垫人物。
3. 人名、楼层、时间、房号、镜局规则必须前后一致；不确定的锚点宁可少写，不要互相打架。
4. 开篇三章必须按黄金三章执行：第1章立刻给生死规则和主角差异；第2章承接第1章结尾并演示“认账/否认者”规则；第3章完成小高潮和更大威胁钩子。
5. 章节结尾必须留下可点击的下一章问题，但不能牺牲当前章的闭环。
6. 新留存门禁要求：每章开头先兑现上一章结尾至少一个地点、人物、威胁或未答问题；禁止直接跳到新案、新空间或新人物。
7. 新角色门禁要求：裴镜渊在第16章及以前只能作为旧账名、账页或过户记录线索存在，不能真人现身、开口、抬头、冷笑或亲自行动。
8. 新章节体量硬门禁：中文正文控制在2000-3500字，目标2400-3000字；不要用总结、设定说明或重复对白凑字，必须用行动、物证、感官、选择代价和规则验证扩足，超过3500字必须删并合并低价值段落。
"""

CHAPTER_INSTRUCTIONS: dict[int, str] = {
    1: """【第1章重跑指令｜最高优先级】
修复目标：第1章当前出现三十三层/二十三层互相冲突、两个开头硬拼接、王老板/中介/镜中七脸的出场顺序混乱。
必须执行：
- 全章楼层统一为“二十三层”，房间锚点统一为“十七栋303/2303”中一个清晰可解释的写法，不再出现三十三层。
- 删除“电梯已经开始一遍故事、随后又回到楼下重新开始”的结构；改成林渊在楼下接委托、进电梯、入室、触发十五分钟镜局的一条连续行动线。
- 王老板必须在前500字内用一句动机明确的委托出场，不要像后贴的人物；如果保留中介，明确他与王老板的关系。
- 保留核心爽点：青囊秘卷首次发烫、十五分钟倒计时、七个镜账人影、林渊发现自己会成为第八个。
- 结尾必须承接第2章：镜局里出现“第一名否认者/老张/张建军”的明确线索，让第2章开头不能像另一本书。
- 新名场面门禁：自然写入“灯下旧账”或“被揭开的封印”，并让林渊在这一幕意识到“原来如此”不是解释设定，而是身份真相被推开。
""",
    2: """【第2章重跑指令｜最高优先级】
修复目标：第2章当前没有承接第1章结尾，开头直接出现小雨、陈默、裴镜渊，读者会以为漏章。
必须执行：
- 开头从第1章结尾的镜账线索接上：林渊追查第一名否认者，不要突然切到小雨和陈默对峙。
- 禁止出现“裴镜渊”作为现场人物；如果这是旧账名，只能作为镜中旧名/案卷名短暂出现，并明确不是新角色登场。
- 用一个完整场景演示“否认者先入账”的规则：第一名否认者为什么否认、否认后如何被镜局惩罚、林渊如何用青囊/铜钱/罗盘看破。
- 小雨和陈默可以出场，但必须由第一名否认者线索自然引入，不能抢走主线。
- 结尾必须把小雨或陈默推到第3章危机里，同时留下“林家旧账不是普通委托”的更大钩子。
- 新名场面门禁：写出林渊短暂掌控镜局规则的权威感；自然写入“天地为之让位”或“从此天地间，我自有道”，不能像修仙口号，要落到铜钱、罗盘、青囊秘卷的动作上。
""",
    3: """【第3章重跑指令｜黄金三章收束】
修复目标：去掉草稿拼接符，完成黄金三章的小高潮。
必须执行：
- 删除所有“---”原始分隔符。
- 让第3章接住第2章结尾危机，完成一次可感知的救人/破局/代价小高潮。
- 明确林渊的主动策略：他不是被动看见怪事，而是用青囊秘卷或阴阳眼反推镜账规则。
- 结尾抛出更大敌人或旧账源头，把读者引向第4章。
- 新名场面门禁：自然写入“血印一笔”或“以血为证”，让林渊做出一个会影响后续十章的誓约/代价选择。
""",
    4: """【第4章重跑指令｜接章修复】
修复目标：第4章新留存门禁提示没有兑现第3章结尾危机。
必须执行：
- 开头第一场必须承接第3章最后留下的人物、地点、威胁或未答问题，至少保留两个具体锚点。
- 不要新开一个看似独立的小案；小雨、陈默只能从第3章危机自然推进出来。
- 本章要完成“小雨认账获救”的阶段闭环，但陈默仍必须留在303镜眼中，不能无过程获救。
- 结尾把陈默的真实执念或替认对象推到第5章，不要只用身体反应做悬念。
""",
    5: """【第5章重跑指令｜接章修复】
修复目标：第5章新留存门禁提示没有兑现第4章结尾承诺。
必须执行：
- 开头第一场必须直接接住第4章结尾的陈默危机或替认对象线索，不能跳到新空间。
- 周雪死亡并入账可以发生，但必须由陈默线索和镜债规则推动，不要像突发支线。
- 林渊要做一次明确推理或反制，不能只被镜局拖着走。
- 结尾必须把第6章的王建业/王老板危机推出去，并清楚保留“否认/认账”的规则压力。
""",
    7: """【第7章重跑指令】
门禁提示开篇楼层/层级锚点混乱。重跑时保留本章主线，但把空间锚点改成一个清晰场景：如果是楼层，就只保留一个楼层；如果是下行层级，就避免写成真实楼层冲突。开头必须先交代当前位置、目标、阻碍，再推进动作。
""",
    9: """【第9章重跑指令】
门禁提示章节开头已经启动后又出现新的时间地点重启。重跑时删掉二次开头，让“凌晨三点零八分”这类时间锚点只作为同一场戏中的消息触发，不要把读者拉回另一个开篇。结尾继续强化第10/11章点击钩。
""",
    13: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    16: """【第16章重跑指令｜角色门禁修复】
修复目标：新角色门禁提示裴镜渊在第16章被写成现场人物，抢走十七栋主线。
必须执行：
- 删除裴镜渊作为现场人物的一切写法：不得走进、出现、开口、抬头、冷笑、亲自动手。
- 裴镜渊最多只能作为钱家账页、旧名、过户记录、回执链条上的名字出现一次；如果出现，必须明确“只是旧账名，不是人到场”。
- 本章主线仍回到林渊、十七栋、镜债/认账规则和当前危机，不要扩写裴家势力。
- 结尾留下“债务过户方法”这个线索即可，不能把裴镜渊升级成新反派登场。
""",
    18: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    23: "门禁提示开篇楼层锚点混乱。重跑时统一楼层/房间/空间方向，避免第一层、第二层等表述被写成互相冲突的真实楼层；保留本章主线。",
    30: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    31: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    32: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    38: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    43: "门禁提示开篇楼层锚点混乱。重跑时统一二层/三层等空间表述，明确是楼层、台阶、夹层还是镜中层级，不能混写。",
    44: "门禁提示开篇楼层锚点混乱。重跑时统一空间锚点，让读者能连续追踪人物位置；保留本章主线和结尾钩。",
    46: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    47: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    49: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
    52: "门禁提示第一层/第二层/第三层可能被写成混乱楼层。重跑时明确这些是镜局层级或空间夹层，不要让它们像真实楼层互相冲突。",
    55: "删除所有“---”草稿分隔符，并把分隔符两侧桥接成自然转场；保留本章主线和结尾钩。",
}

CHAPTERS = tuple(CHAPTER_INSTRUCTIONS)


def _has_heading(content_md: str, chapter_number: int) -> bool:
    for line in content_md.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.startswith(f"# 第{chapter_number}章") or stripped.startswith(
                f"# Chapter {chapter_number}"
            )
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


async def _chapter_by_number(
    session: AsyncSession,
    project: ProjectModel,
    chapter_number: int,
) -> ChapterModel | None:
    return await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )


async def _create_tasks(
    *,
    chapter_filter: int | None,
    replace_existing: bool,
) -> list[str]:
    settings = load_settings()
    created: list[str] = []
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, PROJECT_SLUG)
        if project is None:
            raise RuntimeError(f"Project {PROJECT_SLUG!r} not found")
        target_chapters = [chapter_filter] if chapter_filter else list(CHAPTERS)
        if replace_existing:
            await session.execute(
                update(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.status.in_(["pending", "queued"]),
                    RewriteTaskModel.metadata_json["repair_source"].as_string()
                    == REPAIR_SOURCE,
                )
                .values(status="superseded")
            )
        for chapter_number in target_chapters:
            if chapter_number not in CHAPTER_INSTRUCTIONS:
                raise RuntimeError(f"Chapter {chapter_number} is not in repair target list")
            chapter = await _chapter_by_number(session, project, chapter_number)
            if chapter is None:
                print(f"ch{chapter_number}: missing chapter row, skipped")
                continue
            existing = await session.scalar(
                select(RewriteTaskModel.id).where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.trigger_source_id == chapter.id,
                    RewriteTaskModel.status.in_(["pending", "queued"]),
                    RewriteTaskModel.metadata_json["repair_source"].as_string()
                    == REPAIR_SOURCE,
                )
            )
            if existing:
                created.append(str(existing))
                print(f"ch{chapter_number}: reuse pending task {existing}")
                continue
            priority = 1 if chapter_number in {1, 2, 3} else 2 if chapter_number in {7, 9} else 3
            task = RewriteTaskModel(
                project_id=project.id,
                trigger_type="commercial_integrity_gate_repair",
                trigger_source_id=chapter.id,
                rewrite_strategy="deep_edit",
                priority=priority,
                status="pending",
                instructions=f"{GLOBAL_INSTRUCTION}\n\n{CHAPTER_INSTRUCTIONS[chapter_number]}",
                context_required=[
                    "prior_chapter_tail",
                    "next_chapter_head",
                    "story_bible",
                    "cast",
                ],
                metadata_json={
                    "repair_source": REPAIR_SOURCE,
                    "chapter_number": chapter_number,
                    "gate_report": "package-integrity-gate-20260522.json",
                },
            )
            session.add(task)
            await session.flush()
            created.append(str(task.id))
            print(f"ch{chapter_number}: created task {task.id}")
    return created


async def _execute_tasks(task_ids: list[str], *, limit: int | None) -> dict[str, object]:
    settings = load_settings()
    selected = task_ids[:limit] if limit and limit > 0 else task_ids
    result: dict[str, object] = {
        "attempted": len(selected),
        "completed": 0,
        "exported": 0,
        "failed": [],
        "rejected": [],
    }
    for index, task_id in enumerate(selected, start=1):
        task_uuid = UUID(task_id)
        try:
            async with session_scope(settings) as session:
                project = await get_project_by_slug(session, PROJECT_SLUG)
                if project is None:
                    raise RuntimeError(f"Project {PROJECT_SLUG!r} not found")
                task = await session.get(RewriteTaskModel, task_uuid)
                if task is None:
                    raise RuntimeError(f"Task {task_id} not found")
                chapter = await session.scalar(
                    select(ChapterModel).where(ChapterModel.id == task.trigger_source_id)
                )
                if chapter is None:
                    raise RuntimeError(f"Task {task_id} has no chapter source")
                chapter_number = int(chapter.chapter_number)
                print(f"[{index}/{len(selected)}] ch{chapter_number}: rewriting {task_id}")
                draft, done_task = await rewrite_chapter_from_task(
                    session,
                    PROJECT_SLUG,
                    chapter_number,
                    rewrite_task_id=task_uuid,
                    settings=settings,
                )
                if done_task.status != "completed":
                    result["rejected"].append(
                        {
                            "task_id": task_id,
                            "chapter_number": chapter_number,
                            "status": done_task.status,
                            "error": done_task.error_log or "",
                        }
                    )
                    print(f"  rejected: {done_task.status} {done_task.error_log or ''}")
                    continue
                result["completed"] = int(result["completed"]) + 1
                output_path = _sync_chapter_file(
                    output_base_dir=Path(settings.output.base_dir),
                    slug=PROJECT_SLUG,
                    chapter=chapter,
                    content_md=draft.content_md,
                    language=project.language,
                )
                result["exported"] = int(result["exported"]) + 1
                print(
                    f"  ok: draft v{draft.version_no}, {draft.word_count} chars, "
                    f"exported {output_path}"
                )
        except Exception as exc:
            result["failed"].append({"task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  failed: {type(exc).__name__}: {exc}")
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapter", type=int, default=None, help="Only repair one target chapter")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute created/reused rewrite tasks",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit tasks to execute")
    parser.add_argument(
        "--replace-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Supersede pending tasks from this repair source before creating new tasks",
    )
    args = parser.parse_args()

    task_ids = await _create_tasks(
        chapter_filter=args.chapter,
        replace_existing=args.replace_existing,
    )
    payload: dict[str, object] = {
        "project_slug": PROJECT_SLUG,
        "repair_source": REPAIR_SOURCE,
        "task_ids": task_ids,
    }
    if args.execute:
        payload["execution"] = await _execute_tasks(task_ids, limit=args.limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

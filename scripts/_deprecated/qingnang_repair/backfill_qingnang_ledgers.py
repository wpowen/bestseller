"""Backfill story-bible ledgers for 《青囊不语问阴阳》 through volume-2 recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.infra.db.session import session_scope
from bestseller.settings import load_settings

PROJECT_SLUG = "exorcist-detective-1778051012"
PLANNING_TERM_REPLACEMENTS = {
    "玩家": "入局者",
    "源代码": "账页底纹",
    "试炼通关": "认账门槛通过",
    "镜主候选": "镜影伪称",
}

EVENT_ROWS = [
    (50, "第一卷余账/审讯室状态", "林渊在警方压力下完成第一卷余账陈述，审讯室到 ch51 房间接电话之间缺释放/手续过场。", "ch51 前必须补苏婉宁或警方放行条件、时间跳转和林渊回到房间的原因。", "不得从审讯室无解释跳到私人房间。"),
    (51, "城南旧事馆委托", "卷二入口已偏向旧城委托，可吸收为镜影牵引父亲半卷青囊的伪证入口。", "必须把委托人与林正淳/半卷青囊/老宅井口建立证据链。", "不得另开与父亲半卷无关的新古董案。"),
    (54, "三族旧契碑/沈家名目", "出现沈家与旧契碑，暂定为镜债伪造或旧卷误导层，未注册为稳定第四族。", "只能作为调查对象或伪证层，需回指林张钱三族旧契。", "不得把沈家直接正典化为凌驾三族的新主线。"),
    (56, "镜中城旧城篇", "镜中城可作为镜债复制旧城记忆的临时场域，服务真假执卷人身份战。", "后续必须证明其边界、代价和回到林家老宅井口的路由。", "不得写成永久新世界或无限流副本。"),
    (62, "沈家旧卷", "沈家旧卷记录进入主线，但性质未核；属于 ch51-75 recovery 范围。", "必须用现实证据、青囊账页或父亲旧物验证真伪。", "不得让旧卷单方面改写林渊身世。"),
    (64, "林渊身世碎片", "所谓沈家血脉只作为镜侧记忆碎片，尚未被现实证据确认。", "后续要降级为误导、伪证或待核线索。", "不得用血脉确认当升级奖励。"),
    (66, "镜影对峙", "镜影林渊仍是卷二身份战压力源。", "每次对峙必须改变证据、身份或执卷资格的成本。", "不得退化为普通分身打斗。"),
    (69, "镜主试炼", "镜主/试炼词汇属于高风险游戏化漂移，暂定为镜影伪造的旧称与认账门槛。", "必须改写为镜债门槛、账页资格或伪造身份审查。", "不得使用通关、源代码、玩家式结算。"),
    (71, "试炼通关/部分权限", "通关和权限为非正典表达；可吸收为暂获伪执卷资格或识破镜影假账。", "后续必须用青囊、罗盘、现实证据重新命名这次结果。", "不得让林渊获得游戏系统权限。"),
    (75, "幕后黑手/旧城改造", "旧城改造幕后线可保留，但必须回扣镜影身份战与父亲半卷青囊。", "ch76-100 必须回到老宅井口和半卷青囊归属。", "不得继续扩大为沈家独立副本线。"),
]

CLUE_ROWS = [
    ("C-026", "林正淳半卷青囊去向", "第 50-51 章", "父亲线余账", "卷二身份战核心：镜影先到老宅井口认账", "ch51-100 必须回收半卷青囊使用权。"),
    ("C-027", "审讯室到房间接电话的断点", "第 50-51 章", "普通转场省略", "状态链缺口，会导致警方压力断裂", "补桥段：放行条件、苏婉宁手续或镜影伪证迫使转场。"),
    ("C-028", "城南旧事馆清代铜镜", "第 51 章", "新委托物件", "镜影投喂的父亲旧证或旧城镜债入口", "不能独立成古董案，必须回指父亲半卷青囊。"),
    ("C-029", "旧城改造覆盖父亲失踪地点", "第 53 章", "城市建设信息", "现实地理把镜债从十七栋扩到旧城", "与老宅井口、旧门和父亲失踪坐标合并。"),
    ("C-030", "三族旧契碑", "第 54 章", "古碑解释世界观", "可能是镜债伪造的旧契版本", "必须用林张钱三族既有物证校验。"),
    ("C-031", "镜中局请柬", "第 55 章", "再次入局邀请", "镜影试图抢先定义林渊身份", "改称镜债凶讯或回执请柬，禁用游戏化系统。"),
    ("C-032", "镜中城旧城篇", "第 56 章", "新场景", "镜债复制旧城记忆来制造身份证词", "只能作为临时镜域，不得永久世界化。"),
    ("C-033", "七人画像", "第 57 章", "参与者名单", "旧城利益纠葛对应新的认账顺序", "按七人因果清点，不使用玩家称谓。"),
    ("C-034", "第一具尸体与父亲旧记录同死法", "第 58 章", "复刻命案", "父亲曾调查同类镜债收账", "继续追父亲记录来源。"),
    ("C-035", "青囊验尸术", "第 59 章", "玄学验尸能力", "青囊只记因果，验尸必须落到证据链", "不得写成万能技能。"),
    ("C-036", "折扇证物", "第 60 章", "旧物", "父亲二十年前带出的镜侧证物", "回收父亲半卷青囊线。"),
    ("C-037", "民国镜案", "第 61 章", "历史旧案", "旧城镜债前史", "补民俗生活肌理，避免空壳民国。"),
    ("C-038", "沈家旧卷", "第 62 章", "新家族资料", "未核伪证层或旧卷误导", "reframed_as_mirror_forged_authority；不得正典化第四族。"),
    ("C-039", "镜主信物", "第 63 章", "权力凭证", "镜影伪造的执卷资格诱饵", "降级为伪称或误导物。"),
    ("C-040", "林渊身世碎片", "第 64 章", "血脉揭露", "镜侧记忆碎片，未被现实证据确认", "不得作为血脉确认奖励。"),
    ("C-041", "第三具尸体", "第 65 章", "规则杀人", "镜局规则变化的证据", "必须接入七人画像账本，不单写惊吓。"),
    ("C-042", "镜影对峙", "第 66 章", "真假林渊冲突", "卷二身份战主压力", "每次对峙要改变证据或身份成本。"),
    ("C-043", "真凶只有一个", "第 67 章", "推理规则变化", "镜债从群体因果转向单一替认误导", "必须保留公平线索。"),
    ("C-044", "第四嫌疑人与父亲旧部", "第 68 章", "嫌疑人", "父亲失踪现实证词", "接回老宅井口与半卷青囊。"),
    ("C-045", "镜主试炼", "第 69-71 章", "升级试炼", "镜影伪造的身份审查", "禁用试炼通关/源代码/玩家，改为认账门槛。"),
    ("C-046", "幕后黑手与旧城改造", "第 75 章", "现实反派", "旧城改造利益链可能被镜债利用", "ch76 后必须回到林家老宅井口。"),
]


def sanitize_planning_text(text: str) -> str:
    sanitized = text
    for old, new in PLANNING_TERM_REPLACEMENTS.items():
        sanitized = sanitized.replace(old, new)
    return sanitized


def render_event_state_ledger(existing: str = "") -> str:
    lines = [
        "# Event State Ledger",
        "",
        "用途：防止跨章续写时发生状态回滚。每次继续写新章前，必须先读本表。",
        "",
        "| 章末 | 事件/人物 | 当前状态 | 下一章只能怎么续 | 禁止回滚 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for line in existing.splitlines():
        if not line.strip().startswith("| 第 "):
            continue
        lines.append(sanitize_planning_text(line))
    existing_joined = "\n".join(lines)
    for chapter, subject, state, next_rule, forbidden in EVENT_ROWS:
        marker = f"| 第 {chapter} 章 |"
        if marker in existing_joined:
            continue
        lines.append(
            sanitize_planning_text(f"| 第 {chapter} 章 | {subject} | {state} | {next_rule} | {forbidden} |")
        )
    return "\n".join(lines).strip() + "\n"


def render_clue_ledger(existing: str = "") -> str:
    lines = [
        "# Clue Ledger",
        "",
        "用途：记录已投放线索、当前解释与后续回收方式。续写新章前必须先检查本表，优先回收旧钩子，而不是另开无关怪谈。",
        "",
        "| ID | 线索 | 投放章节 | 表面解释 | 真正指向 | 回收计划 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for line in existing.splitlines():
        if line.strip().startswith("| C-"):
            lines.append(sanitize_planning_text(line))
    existing_joined = "\n".join(lines)
    for clue_id, clue, chapter, surface, true_target, plan in CLUE_ROWS:
        if f"| {clue_id} |" in existing_joined:
            continue
        lines.append(sanitize_planning_text(f"| {clue_id} | {clue} | {chapter} | {surface} | {true_target} | {plan} |"))
    lines.extend(
        [
            "",
            "## 回收纪律",
            "",
            "- 每 6 章至少回收、升级或显式延期 1 个旧线索。",
            "- ch51-75 在 recovery 完成前，所有沈家/镜主/试炼线索都按镜侧伪证或待核证据处理。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_continuity_ledger(title: str, chapter_rows: list[tuple[int, str, int, int | None, str]]) -> str:
    lines = [
        f"# Continuity Ledger — {title}",
        "",
        "## Exported Current Drafts",
        "| Chapter | Title | Word Count | Draft Version | Canon Status |",
        "|---:|---|---:|---:|---|",
    ]
    for chapter_no, chapter_title, word_count, version, status in chapter_rows:
        lines.append(
            sanitize_planning_text(f"| {chapter_no} | {chapter_title} | {word_count} | {version or 0} | {status} |")
        )
    return "\n".join(lines).strip() + "\n"


async def _load_chapter_rows() -> tuple[str, list[tuple[int, str, int, int | None, str]]]:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = (await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))).one()
        rows = (
            await session.execute(
                select(ChapterModel, ChapterDraftVersionModel)
                .join(
                    ChapterDraftVersionModel,
                    (ChapterDraftVersionModel.chapter_id == ChapterModel.id)
                    & (ChapterDraftVersionModel.is_current.is_(True)),
                    isouter=True,
                )
                .where(ChapterModel.project_id == project.id, ChapterModel.chapter_number <= 75)
                .order_by(ChapterModel.chapter_number)
            )
        ).all()
        chapter_rows: list[tuple[int, str, int, int | None, str]] = []
        for chapter, draft in rows:
            chapter_no = int(chapter.chapter_number)
            if chapter_no <= 50:
                status = "canon_reviewed"
            elif chapter_no <= 75:
                status = "volume_2_identity_war_recovery"
            else:
                status = "planned"
            chapter_rows.append(
                (
                    chapter_no,
                    chapter.title or "",
                    int(getattr(draft, "word_count", 0) or 0),
                    int(getattr(draft, "version_no", 0) or 0) if draft is not None else None,
                    status,
                )
            )
        return project.title, chapter_rows


async def run(*, apply: bool) -> dict[str, Any]:
    settings = load_settings()
    story_bible_dir = Path(settings.output.base_dir) / PROJECT_SLUG / "story-bible"
    title, chapter_rows = await _load_chapter_rows()

    event_path = story_bible_dir / "event-state-ledger.md"
    clue_path = story_bible_dir / "clue-ledger.md"
    continuity_path = story_bible_dir / "continuity-ledger.md"

    event_text = render_event_state_ledger(event_path.read_text(encoding="utf-8") if event_path.exists() else "")
    clue_text = render_clue_ledger(clue_path.read_text(encoding="utf-8") if clue_path.exists() else "")
    continuity_text = render_continuity_ledger(title, chapter_rows)

    if apply:
        story_bible_dir.mkdir(parents=True, exist_ok=True)
        event_path.write_text(event_text, encoding="utf-8")
        clue_path.write_text(clue_text, encoding="utf-8")
        continuity_path.write_text(continuity_text, encoding="utf-8")

    return {
        "project_slug": PROJECT_SLUG,
        "applied": apply,
        "chapter_rows": len(chapter_rows),
        "event_state_reaches": 75,
        "clue_last_id": CLUE_ROWS[-1][0],
        "paths": {
            "event_state": str(event_path),
            "clue": str(clue_path),
            "continuity": str(continuity_path),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

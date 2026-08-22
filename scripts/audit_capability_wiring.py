#!/usr/bin/env python
"""一本书的**框架能力驱动体检表**——哪些能力真的跑起来了，哪些是空的。

2026-08-22 建立。动机：《书院笔仙》完本 50 章、九项管道修复全部验证通过，
用户仍然指出「主角智商不在线」「对话憋屈」「框架那么多能力没用上」。
逐表一查，`characters` 只有 1 行（全库其余书 0 行），而 `book_spec.cast`
里躺着 14 个具名角色——**能力都实现了，只是没被驱动**。

所以体检的判据不是「代码里有没有这个能力」（有），而是「这本书里它有没有
产出」。更关键的是**依赖链**：下游为空往往只是上游为空的影子，逐个去修
影子会一直修不完。本表按依赖顺序打印，第一个断掉的环才是要修的地方。

已确证的链（每一环都在真机上验过）：

    cast_spec.supporting_cast / antagonist   ← 断点在这里
      → characters
        → relationships
          → emotion_tracks        (_build_emotion_track_specs 遍历 relationships)
          → antagonist_plans      (依赖 antagonist)

用法：
    python scripts/audit_capability_wiring.py <project-slug>
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text

from bestseller.infra.db.session import session_scope
from bestseller.settings import load_settings

# (显示名, 表名, 上游依赖显示名 | None, 这一项为空意味着什么)
CAPABILITIES: list[tuple[str, str, str | None, str]] = [
    ("人物", "characters", None, "配角没有 goal/voice_profile：纸片人，对话无区分度"),
    ("势力", "factions", None, "没有组织级阻力，冲突只能靠个人恩怨"),
    ("地点", "locations", None, "场景无锚点，环境描写只能即兴"),
    ("关系", "relationships", "人物", "没有关系张力 → 对话没有动机"),
    ("关系事件", "relationship_events", "关系", "关系是静态的，不随剧情变化"),
    ("情绪轨", "emotion_tracks", "关系", "情绪没有跨章轨迹，每章各写各的"),
    ("反派计划", "antagonist_plans", "人物", "反派不主动出招，主角没有对手压力"),
    ("人物状态快照", "character_state_snapshots", "人物", "人物状态不跨章传递"),
    ("章节状态快照", "chapter_state_snapshots", None, "章间状态不落库，长程连贯全靠 prompt"),
    ("设定事实", "canon_facts", None, "世界规则无事实源，前后可能自相矛盾"),
    ("线索", "clues", None, "伏笔无账本（注：伏笔走 clues+payoffs，不走 foreshadowing_ledger）"),
    ("回收", "payoffs", "线索", "埋了不收"),
    ("延迟揭示", "deferred_reveals", None, "没有节奏化的信息释放"),
    ("读者认知", "reader_knowledge_entries", None, "不追踪读者已知什么，容易重复交代"),
    ("人际承诺", "interpersonal_promises", "人物", "承诺不入账，人物言行可以随意反悔"),
    ("追债", "chase_debts", None, "欠下的账没有追讨压力"),
    ("母题落点", "motif_placements", None, "主题不落到具体场景"),
    ("剧情弧", "plot_arcs", None, "没有弧线结构"),
    ("弧光节拍", "arc_beats", "剧情弧", "弧线没有节拍展开"),
    ("节奏曲线", "pacing_curve_points", None, "节奏无规划"),
    ("结局契约", "ending_contracts", None, "结局无承诺"),
]


async def audit(slug: str) -> int:
    settings = load_settings()
    async with session_scope(settings) as session:
        project_id = await session.scalar(
            text("SELECT id FROM projects WHERE slug = :slug"), {"slug": slug}
        )
        if project_id is None:
            print(f"project not found: {slug}")
            return 2

        counts: dict[str, int] = {}
        for label, table, _dep, _why in CAPABILITIES:
            counts[label] = int(
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE project_id = :pid"),  # noqa: S608
                    {"pid": project_id},
                )
                or 0
            )

        chapters = int(
            await session.scalar(
                text("SELECT count(*) FROM chapters WHERE project_id = :pid"),
                {"pid": project_id},
            )
            or 0
        )

        judge_rows = (
            await session.execute(
                text(
                    """
                    SELECT k AS dim,
                           avg((r.structured_output->'scores'->>k)::numeric) AS mean,
                           count(*) AS n
                    FROM review_reports r,
                         LATERAL jsonb_object_keys(r.structured_output->'scores') k
                    WHERE r.project_id = :pid
                      AND jsonb_typeof(r.structured_output->'scores'->k) = 'number'
                    GROUP BY 1 ORDER BY 2
                    """
                ),
                {"pid": project_id},
            )
        ).all()
        judge_scores = [(str(row[0]), float(row[1])) for row in judge_rows]
        judge_n = int(judge_rows[0][2]) if judge_rows else 0

        violations = [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT v->>'code', v->>'severity', count(*)
                        FROM chapter_quality_reports qr
                        JOIN chapters c ON qr.chapter_id = c.id,
                             LATERAL jsonb_array_elements(
                                 coalesce(qr.report_json->'violations', '[]'::jsonb)
                             ) v
                        WHERE c.project_id = :pid
                        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
                        """
                    ),
                    {"pid": project_id},
                )
            ).all()
        ]

    print(f"# 框架能力驱动体检 · {slug}（{chapters} 章）\n")
    print(f"{'能力':14}{'行数':>6}  判定")
    print("-" * 88)
    root_causes: list[str] = []
    for label, _table, dep, why in CAPABILITIES:
        n = counts[label]
        if n > 0:
            verdict = "✅"
        elif dep and counts.get(dep, 0) == 0:
            # 上游为空，这里为空只是影子——不单独当问题报。
            verdict = f"⬜ 空（上游「{dep}」也是空的，先修上游）"
        else:
            verdict = f"❌ 空 → {why}"
            root_causes.append(f"{label}：{why}")
        print(f"{label:14}{n:>6}  {verdict}")

    # ── 判官分数：结构层 vs 文笔层，一眼看出瓶颈在哪一层 ────────────────
    if judge_scores:
        print()
        print(f"## 判官分数（n={judge_n} 份审稿报告）\n")
        print(f"{'维度':32}{'均分':>7}")
        print("-" * 44)
        for dim, val in judge_scores:
            print(f"{dim:32}{val:>7.3f}")

    # ── 违规码普查：哪些门在真的开火 ────────────────────────────────────
    if violations:
        print()
        print("## 违规码普查（从不失败的门等于不存在的门）\n")
        print(f"{'码':32}{'级别':>8}{'次数':>8}")
        print("-" * 50)
        for code, sev, n in violations:
            print(f"{code:32}{sev:>8}{n:>8}")

    print()
    if root_causes:
        print("## 断点（上游不空、自己空 —— 这些才是要修的）\n")
        for item in root_causes:
            print(f"- {item}")
    else:
        print("## 没有断点：每一项要么有产出，要么其上游为空。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    args = parser.parse_args()
    return asyncio.run(audit(args.slug))


if __name__ == "__main__":
    raise SystemExit(main())

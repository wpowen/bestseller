#!/usr/bin/env python3
"""
clean_zh_source.py — 清洗中文原版章节，去除互动小说残留的元信息。

清洗目标：
  1. 舞台指示 `（人名+动作）` / `（纯动作描述）`        — 删除
  2. 游戏系统消息 【系统提示/警告/任务/奖励/天命值...】  — 删除
  3. 卷结构泄漏 "第N卷目标/核心/序幕/终章"               — 改写为非元信息表述
  4. 章节结构泄漏 "上一章XX/下一章XX"                     — 改写为"之前/随后"

策略：
  - 备份原文件到 chapters_pre_cleanup/
  - 递归遍历 JSON，对 TRANSLATABLE_FIELDS 中的字符串应用清洗
  - 输出清洗报告（每条规则的命中数 + 受影响章节数）

用法:
  python3 scripts/clean_zh_source.py --dry-run    # 仅统计
  python3 scripts/clean_zh_source.py              # 真实执行
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).parent.parent
ZH_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
BACKUP_DIR = ROOT / "output" / "天机录" / "if" / "chapters_pre_cleanup"
REPORT_PATH = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "zh_cleanup_report.json"

# 可清洗字段（与 translate_novel.TRANSLATABLE_FIELDS 一致）
TARGET_FIELDS = {
    "title", "next_chapter_hook", "content", "prompt", "text",
    "description", "visible_cost", "visible_reward", "risk_hint",
}


# 规则 1：舞台指示（括号内 2-50 个汉字，可含逗号顿号）
STAGE_MARKER = re.compile(
    r"[（(]\s*(?:[\u4e00-\u9fff][\u4e00-\u9fff、，·…—\s]{1,49})\s*[）)]"
)

# 规则 2：游戏系统消息（方括号内含系统类关键字）
SYSTEM_MSG = re.compile(
    r"【\s*(?:系统|系统提示|警告|提示|任务|奖励|公告|检测|检测到|天命值|技能|"
    r"天机录·?[残激觉醒]|当前预见|预见|羁绊升级|解锁|进度|关键情报|状态|"
    r"枷锁规则|未知能量|你打算)[^】]*】"
)

# 规则 3：卷结构泄漏
VOLUME_META_PATTERNS = [
    (re.compile(r"第[一二三四五六七八九十]+卷目标"), "目标"),
    (re.compile(r"第[一二三四五六七八九十]+卷的(?:核心|主题|序幕|终章)"), "新阶段的核心"),
    (re.compile(r"第[一二三四五六七八九十]+卷[，,]?\s*正式开始"), "新的篇章，正式开始"),
    (re.compile(r"第[一二三四五六七八九十]+卷开始"), "起初"),
    (re.compile(r"本卷目标"), "当前目标"),
    (re.compile(r"本卷的(?:核心|主题)"), "当前的核心"),
    (re.compile(r"卷末"), "篇末"),
]

# 规则 4：章节结构泄漏
CHAPTER_META_PATTERNS = [
    (re.compile(r"[—\-]{2,}\s*下一章即将发生\s*[—\-]{2,}"), "——"),
    (re.compile(r"下一章预告[：:]\s*"), ""),
    (re.compile(r"上一章结尾(?:的)?"), "之前"),
    (re.compile(r"上[一二三]?章(?:的)?(?:结尾|末尾)"), "之前"),
    (re.compile(r"下[一二三]?章即将"), "随后即将"),
    (re.compile(r"下一章[，,]"), "之后，"),
    (re.compile(r"下一章才能"), "之后才能"),
    (re.compile(r"那正是下一章"), "那正是即将发生的"),
    (re.compile(r"下一章"), "之后"),
    (re.compile(r"上一章"), "之前"),
    (re.compile(r"上章结尾"), "之前"),
    (re.compile(r"本章(?:回顾|内容|讲述)"), "刚才发生的"),
]


def clean_string(text: str, counts: dict) -> str:
    """对单条字符串应用所有清洗规则。counts 累加命中次数。"""
    if not text or not isinstance(text, str):
        return text

    original = text

    # 规则 1：删除舞台指示
    def _on_stage(m: re.Match) -> str:
        counts["stage_marker"] += 1
        return ""
    text = STAGE_MARKER.sub(_on_stage, text)

    # 规则 2：删除游戏系统消息
    def _on_system(m: re.Match) -> str:
        counts["system_msg"] += 1
        return ""
    text = SYSTEM_MSG.sub(_on_system, text)

    # 规则 3：卷结构泄漏改写
    for pat, repl in VOLUME_META_PATTERNS:
        new_text, n = pat.subn(repl, text)
        if n:
            counts["volume_meta"] += n
            text = new_text

    # 规则 4：章节结构泄漏改写
    for pat, repl in CHAPTER_META_PATTERNS:
        new_text, n = pat.subn(repl, text)
        if n:
            counts["chapter_meta"] += n
            text = new_text

    if text == original:
        return original

    # 标点 / 空白善后
    # 1) 多个句号合并
    text = re.sub(r"。{2,}", "。", text)
    # 2) 删除前置空白后跟标点的情况（清洗后留下的孤立标点）
    text = re.sub(r"\s+([，。！？、：；])", r"\1", text)
    # 3) 重复逗号
    text = re.sub(r"，{2,}", "，", text)
    # 4) 多空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 5) 行首逗号/句号
    text = re.sub(r"(?:^|\n)\s*[，。、]+\s*", lambda m: "\n" if "\n" in m.group() else "", text)
    # 6) 引号内开头的标点 "，XXX" → "XXX"
    text = re.sub(r'([「『"])[，。、]\s*', r"\1", text)
    text = text.strip()

    return text


def clean_node(obj: Any, counts: dict) -> Any:
    """递归清洗 dict / list 中的字符串字段。原地修改。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in TARGET_FIELDS and isinstance(v, str):
                obj[k] = clean_string(v, counts)
            elif isinstance(v, (dict, list)):
                clean_node(v, counts)
    elif isinstance(obj, list):
        for item in obj:
            clean_node(item, counts)
    return obj


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Clean ZH source chapters of meta leakage.")
    parser.add_argument("--dry-run", action="store_true", help="Only report, no writes")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup (only if backup exists)")
    args = parser.parse_args(argv)

    chapters = sorted(ZH_DIR.glob("ch*.json"))
    if not chapters:
        print(f"未找到章节: {ZH_DIR}", file=sys.stderr)
        return 1

    if not args.dry_run and not args.no_backup:
        if not BACKUP_DIR.exists():
            print(f"备份 → {BACKUP_DIR}")
            shutil.copytree(ZH_DIR, BACKUP_DIR)
        else:
            print(f"备份目录已存在，跳过: {BACKUP_DIR}")

    total_counts: dict[str, int] = defaultdict(int)
    affected: dict[str, set[int]] = defaultdict(set)
    chapters_modified = 0

    for p in chapters:
        chnum = int(p.stem[2:])
        chapter = json.loads(p.read_text(encoding="utf-8"))
        ch_counts: dict[str, int] = defaultdict(int)

        clean_node(chapter, ch_counts)

        if any(ch_counts.values()):
            chapters_modified += 1
            for k, c in ch_counts.items():
                total_counts[k] += c
                affected[k].add(chnum)
            if not args.dry_run:
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(p)

    # 报告
    report = {
        "dry_run": args.dry_run,
        "scanned": len(chapters),
        "chapters_modified": chapters_modified,
        "rule_hits": dict(total_counts),
        "affected_chapter_counts": {k: len(v) for k, v in affected.items()},
        "affected_chapters_sample": {
            k: sorted(v)[:20] for k, v in affected.items()
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}清洗完成")
    print(f"扫描章节: {len(chapters)}")
    print(f"涉及修改章节: {chapters_modified}")
    print(f"\n各规则命中:")
    for k, c in sorted(total_counts.items(), key=lambda kv: -kv[1]):
        n_ch = len(affected[k])
        print(f"  {k:20s} {c:6,} 次  (涉及 {n_ch} 章)")
    print(f"\n报告 → {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
fix_arc_gap.py — 修复弧线章节数不足导致的空洞问题

当 LLM 生成弧计划时返回章节数少于 arc_batch_size，
后续弧从计算位置（而非实际结束位置）开始，导致章节空洞。

用法：
    python3 scripts/fix_arc_gap.py output/天机录      # 检查并修复
    python3 scripts/fix_arc_gap.py output/天机录 --dry-run  # 仅检查，不修复
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("用法: python3 scripts/fix_arc_gap.py <output/小说名> [--dry-run]")
        sys.exit(1)

    novel_dir = Path(args[0])
    if_dir = novel_dir / "if"
    progress_path = if_dir / "if_progress.json"

    if not progress_path.exists():
        print(f"❌ 未找到 progress 文件: {progress_path}")
        sys.exit(1)

    state = json.loads(progress_path.read_text(encoding="utf-8"))
    arc_plans: dict[str, list] = state.get("arc_plans_dict", {})
    arc_sums: dict[str, dict] = state.get("arc_summaries_dict", {})

    # Infer arc_batch_size from completed arcs
    sizes = [len(v) for k, v in arc_plans.items() if len(v) > 5]
    if not sizes:
        print("❌ 无法推断 arc_batch_size")
        sys.exit(1)
    # Most common size = expected arc_batch_size
    arc_batch_size = max(set(sizes), key=sizes.count)
    print(f"推断 arc_batch_size = {arc_batch_size}")

    # Find arcs with fewer cards than expected (excluding last arc which may be legitimately smaller)
    all_arc_keys = sorted(arc_plans.keys(), key=int)
    last_key = all_arc_keys[-1] if all_arc_keys else None

    # Infer volume_size from volume_plans (look at first volume's arc range)
    volume_plans = state.get("volume_plans", {})
    # Natural boundaries = volume ends and book end
    # If volume_size not inferrable, use arc_batch_size * 8 as default
    natural_boundaries: set[int] = set()
    total_chapters = state.get("bible", {}).get("book", {}).get("total_chapters", 0)
    if total_chapters:
        natural_boundaries.add(total_chapters)
    # Infer volume boundaries from the arc plan keys' chapter spans
    # Vol boundary = chapter number divisible by volume_size
    # Try common sizes: 100, 120, 150
    for vol_size_guess in [100, 120, 150, 200]:
        ch = vol_size_guess
        while ch <= (total_chapters or 2000):
            natural_boundaries.add(ch)
            ch += vol_size_guess

    gaps_found = []
    for key in all_arc_keys:
        cards = arc_plans[key]
        if not cards:
            continue
        arc_start = cards[0]["number"]
        arc_end = cards[-1]["number"]
        actual_size = len(cards)
        expected_end = arc_start + arc_batch_size - 1

        # Arc is short if: fewer cards than batch AND the end is NOT a natural boundary
        is_at_boundary = arc_end in natural_boundaries
        if actual_size < arc_batch_size and not is_at_boundary:
            # This arc is short — chapters after arc_end up to expected_end are skipped
            missing_start = arc_end + 1
            missing_end = expected_end
            gaps_found.append({
                "arc_key": key,
                "arc_start": arc_start,
                "arc_end_actual": arc_end,
                "arc_end_expected": expected_end,
                "missing_chapters": list(range(missing_start, missing_end + 1)),
                "actual_size": actual_size,
            })
            print(f"\n⚠️  弧{int(key)+1} (key={key}): ch{arc_start}-{arc_end} 只有 {actual_size} 张卡，"
                  f"应有 {arc_batch_size} 张（应到 ch{expected_end}）")
            print(f"   缺失章节: ch{missing_start}-ch{missing_end} ({missing_end - missing_start + 1} 章)")

    if not gaps_found:
        print("✅ 未发现章节空洞，无需修复")
        return

    if dry_run:
        print("\n[dry-run] 不执行修复。移除 --dry-run 参数后运行以实际修复。")
        return

    print("\n开始修复...")
    chapters_dir = if_dir / "chapters"
    modified = False

    for gap in gaps_found:
        key = gap["arc_key"]
        arc_start = gap["arc_start"]
        arc_end_actual = gap["arc_end_actual"]

        print(f"\n修复弧{int(key)+1} (ch{arc_start}-ch{gap['arc_end_expected']}):")

        # 1. Delete existing chapter files for this arc (force re-generation)
        for ch_num in range(arc_start, arc_end_actual + 2):  # +1 to catch the last generated
            ch_file = chapters_dir / f"ch{ch_num:04d}.json"
            if ch_file.exists():
                print(f"  删除 {ch_file.name}")
                ch_file.unlink()

        # 2. Remove arc plan (force re-planning with correct boundary)
        if key in arc_plans:
            del arc_plans[key]
            print(f"  移除弧计划 key={key}（将重新规划）")

        # 3. Remove arc summary (since chapters will be regenerated)
        if key in arc_sums:
            del arc_sums[key]
            print(f"  移除弧总结 key={key}")

        modified = True

    if modified:
        state["arc_plans_dict"] = arc_plans
        state["arc_summaries_dict"] = arc_sums
        # Also remove "generated_chapters" key if it exists (legacy)
        state.pop("generated_chapters", None)
        state.pop("chapters_mainline", None)

        # Backup original
        backup = progress_path.with_suffix(".json.bak")
        backup.write_bytes(progress_path.read_bytes())
        print(f"\n备份原始 progress 到: {backup.name}")

        progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print("✅ progress 文件已更新")
        print("\n现在可以运行：")
        print("  ./scripts/tianjilu.sh --resume")
        print("弧将被重新规划（完整15章），缺失章节将被补充生成。")


if __name__ == "__main__":
    main()

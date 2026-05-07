#!/usr/bin/env python3
"""Extract residual Chinese strings from JA chapters for manual translation.

Usage:
  python scripts/extract_ja_residual.py --limit 5 --out /tmp/ja_residual_5ch.json
  python scripts/extract_ja_residual.py --all --out /tmp/ja_residual_all.json

Output format: list of {chapter, path, src_zh, current_ja} dicts.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
JA_DIR = ROOT / "output" / "天机录" / "translations" / "ja" / "chapters"

RESIDUAL_RE = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")

PRESERVE_FIELDS = frozenset({
    "id", "book_id", "character_id", "number", "is_paid", "is_premium",
    "emotion", "emphasis", "choice_type", "satisfaction_type",
    "stat", "dimension", "delta", "flags_set", "requires_flag",
    "character_name", "next_chapter", "type", "node_type", "speaker",
})
TRANSLATABLE_FIELDS = frozenset({
    "title", "next_chapter_hook", "content", "prompt", "text", "description",
    "visible_cost", "visible_reward", "risk_hint",
})


def _walk_translatable(obj, path, results):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PRESERVE_FIELDS:
                continue
            if k in TRANSLATABLE_FIELDS and isinstance(v, str) and v.strip():
                results.append((path + [k], v))
            else:
                _walk_translatable(v, path + [k], results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_translatable(item, path + [i], results)


def extract_chapter(src_ch, ja_ch):
    src_strings = []
    ja_strings = []
    _walk_translatable(src_ch, [], src_strings)
    _walk_translatable(ja_ch, [], ja_strings)
    src_map = {tuple(p): t for p, t in src_strings}
    tasks = []
    for path, current in ja_strings:
        if not RESIDUAL_RE.search(current):
            continue
        src_text = src_map.get(tuple(path))
        if not src_text:
            continue
        tasks.append({
            "path": path,
            "src_zh": src_text,
            "current_ja": current,
        })
    return tasks


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=1200)
    args = ap.parse_args()

    src_chapters = {}
    for p in sorted(SRC_DIR.glob("ch*.json")):
        num = int(p.stem.replace("ch", ""))
        if args.start <= num <= args.end:
            src_chapters[num] = json.loads(p.read_text(encoding="utf-8"))

    output = []
    ja_files = sorted(JA_DIR.glob("ch*.json"))
    if args.limit:
        ja_files = ja_files[:args.limit]

    for ja_path in ja_files:
        num = int(ja_path.stem.replace("ch", ""))
        src = src_chapters.get(num)
        if not src:
            continue
        ja = json.loads(ja_path.read_text(encoding="utf-8"))
        tasks = extract_chapter(src, ja)
        if tasks:
            output.append({
                "chapter": num,
                "strings": tasks,
            })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    total_strings = sum(len(c["strings"]) for c in output)
    print(f"Extracted {len(output)} chapters, {total_strings} residual strings → {out_path}")


if __name__ == "__main__":
    main()

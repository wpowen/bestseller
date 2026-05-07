#!/usr/bin/env python3
"""Apply JA translations from a JSON patch file back into chapter files.

Usage:
  python scripts/apply_ja_translations.py --patch /tmp/ja_patch_ch001-005.json

Patch format: list of {chapter: int, path: list, new_ja: str} dicts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JA_DIR = ROOT / "output" / "天机录" / "translations" / "ja" / "chapters"


def _path_set(obj, path, value):
    cur = obj
    for k in path[:-1]:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return False
    try:
        cur[path[-1]] = value
        return True
    except (KeyError, IndexError, TypeError):
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", type=str, required=True)
    args = ap.parse_args()

    patches = json.loads(Path(args.patch).read_text(encoding="utf-8"))
    by_chapter = {}
    for p in patches:
        by_chapter.setdefault(p["chapter"], []).append(p)

    total_applied = 0
    for ch_num, ch_patches in sorted(by_chapter.items()):
        ch_path = JA_DIR / f"ch{ch_num:04d}.json"
        if not ch_path.exists():
            print(f"[skip] ch{ch_num:04d} not found")
            continue
        ch = json.loads(ch_path.read_text(encoding="utf-8"))
        applied = 0
        for p in ch_patches:
            if _path_set(ch, p["path"], p["new_ja"]):
                applied += 1
        ch_path.write_text(json.dumps(ch, ensure_ascii=False, indent=2), encoding="utf-8")
        total_applied += applied
        print(f"ch{ch_num:04d}: {applied}/{len(ch_patches)} patches applied")

    print(f"\nTotal: {total_applied} patches applied across {len(by_chapter)} chapters")


if __name__ == "__main__":
    main()

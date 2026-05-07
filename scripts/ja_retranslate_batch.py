#!/usr/bin/env python3
"""Batch workflow for JA residual retranslation.

Workflow:
1. extract: Find chapters with residual, print SRC for translation
2. apply:   Apply translated patches back

Usage:
  # Step 1: Extract next batch of chapters for translation
  python scripts/ja_retranslate_batch.py extract --batch-size 10 --offset 5 --out /tmp/ja_batch.json

  # Step 2: Apply translations from a patch file
  python scripts/ja_retranslate_batch.py apply --patch /tmp/ja_patch.json

  # Step 3: Verify no residual remains in patched chapters
  python scripts/ja_retranslate_batch.py verify --chapters 1,2,3
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

RESIDUAL_RE = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")

CN_PATTERNS = re.compile(
    r"(此刻|随即|只为|仿佛|咱たち|凭什么|我就是|岂不是|不就是|"
    r"是不是|为什么|怎么办|怎么样|这样的|那样的|什么样的|"
    r"一瞬呆然|毫の无|卡住た|刻意绕|避開|远远望去|然后悄然|"
    r"言罢|言得|无関心|珍味佳肴|一道残缺|一道虚幻|一道苍老|"
    r"也许|也罢|只的|就で|陈法|陈吉|陈样|陈性|陈前|陈お|陈很|陈德海|"
    r"只见|心中|却是|已然|果然|竟已|早已|唯有|陡然|蓦然|骤然|"
    r"赫然|霍然|顷刻|刹那|而已|罢了|罢了|纵然|纵使|尽管|"
    r"既然|固然|诚然|自然|忽然|忽地|当下|却已|亦已|哙然|"
    r"咱们的|咱たち|咱们|本少爷|师弟|师兄|掌门|老朽|尔等|尔等|"
    r"而是|不是|便是|便是|正是|乃是|仍是|竟是|又是|既是|"
    r"果然|忽然|赫然|陡然|蓦然|骤然|霍然|顷刻|刹那|"
    r"此际|此时|当今|当世|此番|此行|此去|此来|此中|此上|此下)"
)


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


def has_real_residual(text, lang="ja"):
    if not RESIDUAL_RE.search(text):
        return False
    if CN_PATTERNS.search(text):
        return True
    cn_count = len(RESIDUAL_RE.findall(text))
    ja_count = len(re.findall(r"[\u3040-\u30ff]", text))
    if ja_count == 0 and cn_count > 0:
        return True
    if cn_count > 3 and ja_count / max(cn_count, 1) < 0.3:
        return True
    return False


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
    sub = ap.add_subparsers(dest="cmd")

    # extract
    ext = sub.add_parser("extract")
    ext.add_argument("--batch-size", type=int, default=10)
    ext.add_argument("--offset", type=int, default=0)
    ext.add_argument("--out", type=str, required=True)

    # apply
    app = sub.add_parser("apply")
    app.add_argument("--patch", type=str, required=True)

    # verify
    ver = sub.add_parser("verify")
    ver.add_argument("--chapters", type=str, required=True)
    ver.add_argument("--all", action="store_true")

    args = ap.parse_args()

    if args.cmd == "extract":
        src_chapters = {}
        for p in sorted(SRC_DIR.glob("ch*.json")):
            num = int(p.stem.replace("ch", ""))
            src_chapters[num] = json.loads(p.read_text(encoding="utf-8"))

        output = []
        ja_files = sorted(JA_DIR.glob("ch*.json"))[args.offset:args.offset + args.batch_size]

        for ja_path in ja_files:
            num = int(ja_path.stem.replace("ch", ""))
            src = src_chapters.get(num)
            if not src:
                continue
            ja = json.loads(ja_path.read_text(encoding="utf-8"))

            src_strings = []
            ja_strings = []
            _walk_translatable(src, [], src_strings)
            _walk_translatable(ja, [], ja_strings)
            src_map = {tuple(p): t for p, t in src_strings}

            tasks = []
            for path, current in ja_strings:
                if not has_real_residual(current):
                    continue
                src_text = src_map.get(tuple(path))
                if not src_text:
                    continue
                tasks.append({
                    "path": path,
                    "src_zh": src_text,
                    "current_ja": current,
                })

            if tasks:
                output.append({
                    "chapter": num,
                    "strings": tasks,
                })

        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        total = sum(len(c["strings"]) for c in output)
        print(f"Extracted {len(output)} chapters, {total} strings → {out_path}")

    elif args.cmd == "apply":
        patches = json.loads(Path(args.patch).read_text(encoding="utf-8"))
        by_chapter = {}
        for p in patches:
            by_chapter.setdefault(p["chapter"], []).append(p)

        total_applied = 0
        for ch_num, ch_patches in sorted(by_chapter.items()):
            ch_path = JA_DIR / f"ch{ch_num:04d}.json"
            if not ch_path.exists():
                print(f"[skip] ch{ch_num:04d}")
                continue
            ch = json.loads(ch_path.read_text(encoding="utf-8"))
            applied = 0
            for p in ch_patches:
                if _path_set(ch, p["path"], p["new_ja"]):
                    applied += 1
            ch_path.write_text(json.dumps(ch, ensure_ascii=False, indent=2), encoding="utf-8")
            total_applied += applied
            print(f"ch{ch_num:04d}: {applied}/{len(ch_patches)} applied")

        print(f"\nTotal: {total_applied} patches across {len(by_chapter)} chapters")

    elif args.cmd == "verify":
        if args.all:
            chapters = list(range(1, 1201))
        else:
            chapters = [int(c.strip()) for c in args.chapters.split(",")]

        total_residual = 0
        clean_count = 0
        for ch_num in chapters:
            ch_path = JA_DIR / f"ch{ch_num:04d}.json"
            if not ch_path.exists():
                continue
            ch = json.loads(ch_path.read_text(encoding="utf-8"))
            all_text = []
            _walk_translatable(ch, [], all_text)
            residual = [(p, t) for p, t in all_text if has_real_residual(t)]
            if residual:
                print(f"ch{ch_num:04d}: {len(residual)} residual strings")
                total_residual += len(residual)
            else:
                clean_count += 1

        print(f"\nClean: {clean_count}, Residual: {total_residual}")


if __name__ == "__main__":
    main()

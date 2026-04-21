#!/usr/bin/env python3
"""
revert_regressions.py — Revert chapters where LLM retranslation made things worse.

For each chapter in chapters/, compare residual count vs chapters_pre_llm/.
If post > pre, restore from backup.
If post == pre AND no kana/hangul/english added, also restore (LLM didn't help).
Otherwise keep the new (better) version.
"""
from __future__ import annotations
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
TRANS = ROOT / "output" / "天机录" / "translations"

CJK_RUN_4 = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")
KO_CJK_3 = re.compile(r"[\u4e00-\u9fff]{3,}")
EN_CJK_2 = re.compile(r"[\u4e00-\u9fff]{2,}")

PATTERNS = {"ja": CJK_RUN_4, "ko": KO_CJK_3, "en": EN_CJK_2}


def count_residuals(path: Path, lang: str) -> int:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return -1
    pat = PATTERNS[lang]
    c = 0

    def walk(o):
        nonlocal c
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str):
                    c += len(pat.findall(v))
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(d)
    return c


def main() -> int:
    summary = {}
    for lang in ["en", "ja", "ko"]:
        post_dir = TRANS / lang / "chapters"
        pre_dir = TRANS / lang / "chapters_pre_llm"
        if not pre_dir.exists():
            print(f"[{lang}] no pre-LLM backup, skip")
            continue

        reverted = []
        improved = 0
        unchanged = 0
        for post_p in sorted(post_dir.glob("ch*.json")):
            pre_p = pre_dir / post_p.name
            if not pre_p.exists():
                continue
            pre_count = count_residuals(pre_p, lang)
            post_count = count_residuals(post_p, lang)
            if pre_count < 0 or post_count < 0:
                continue
            if post_count > pre_count:
                shutil.copy(pre_p, post_p)
                reverted.append((post_p.stem, pre_count, post_count))
            elif post_count == pre_count:
                unchanged += 1
            else:
                improved += 1

        summary[lang] = {
            "reverted": len(reverted),
            "improved": improved,
            "unchanged": unchanged,
            "samples": reverted[:10],
        }
        print(
            f"[{lang}] reverted={len(reverted)} improved={improved} unchanged={unchanged}"
        )
        for ch, pre, post in reverted[:10]:
            print(f"  {ch}: {pre} → {post} (worse, reverted)")

    out = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "revert_report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
smart_audit.py — Smart language-aware residual detector.

After retranslate_residual.py, some "residuals" may be false positives
(e.g. valid Japanese 4-kanji compounds). This script uses
language-specific markers to distinguish true Chinese residuals from
legitimate target-language CJK content.

Detection logic:
  - JA: Flag string if contains BOTH (4+ kanji run AND no kana around it)
        AND has Chinese-specific markers (的/了/在/这/那/哪/啊/呢/吗 etc.)
  - KO: Flag any string with 3+ CJK chars (Korean rarely uses hanja)
  - EN: Flag any string with 1+ CJK char

Output:
  output/天机录/amazon/quality_audit/smart_audit_report.json
  retranslation_queue_v2_{lang}.json  (chapter list for 2nd pass)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
SRC_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
OUT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"

sys.path.insert(0, str(ROOT / "scripts"))
from translate_novel import PRESERVE_FIELDS, TRANSLATABLE_FIELDS  # type: ignore

# Chinese-specific markers (rarely valid in modern Japanese)
CN_PARTICLES = re.compile(r"[的了着在这那啊呢吗哪何哦呀呢咯嗯哎]")
CN_GRAMMAR = re.compile(r"(?:[把被让使将][^\s\u3040-\u30ff]|[不没][\u4e00-\u9fff]|[一两三四五六七八九十几]个|[这那][里些样个边人])")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
CJK_RUN_4 = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")
CJK = re.compile(r"[\u4e00-\u9fff]")


def is_residual_for_lang(text: str, lang: str) -> bool:
    """Smart per-language residual detection. Returns True if string contains true CN residual."""
    if not text or not isinstance(text, str):
        return False
    if lang == "en":
        return bool(CJK.search(text))
    if lang == "ko":
        # Korean rarely uses hanja except in formal proper nouns; flag any 3+ CJK run
        return bool(re.search(r"[\u4e00-\u9fff]{3,}", text))
    if lang == "ja":
        # Two-stage: must have a 4+ kanji run AND show Chinese markers
        if not CJK_RUN_4.search(text):
            return False
        # Strong CN markers
        if CN_PARTICLES.search(text) or CN_GRAMMAR.search(text):
            return True
        # OR very long pure-kanji run (>= 8 chars, very rare in JP)
        if re.search(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){8,}", text):
            return True
        return False
    return False


def walk_strings(obj, path, results):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PRESERVE_FIELDS:
                continue
            if k in TRANSLATABLE_FIELDS and isinstance(v, str) and v.strip():
                results.append((path + [k], v))
            else:
                walk_strings(v, path + [k], results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            walk_strings(item, path + [i], results)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    full_report: dict = {}

    for lang in ["en", "ja", "ko"]:
        chapters_dir = TRANS_DIR / lang / "chapters"
        affected_chapters: dict[int, dict] = {}
        total_residual_strings = 0
        sample_residuals: list[str] = []

        for p in sorted(chapters_dir.glob("ch*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            ch_num = int(p.stem[2:])
            strings: list[tuple[list, str]] = []
            walk_strings(data, [], strings)

            residuals = [(path, s) for path, s in strings if is_residual_for_lang(s, lang)]
            if residuals:
                affected_chapters[ch_num] = {
                    "residual_count": len(residuals),
                    "samples": [s[1][:80] for s in residuals[:3]],
                }
                total_residual_strings += len(residuals)
                if len(sample_residuals) < 20:
                    sample_residuals.extend(s[:60] for _, s in residuals[:2])

        # Severity ranking (by residual count)
        sorted_chs = sorted(affected_chapters.items(), key=lambda kv: -kv[1]["residual_count"])
        critical = [ch for ch, d in sorted_chs if d["residual_count"] >= 10]
        high = [ch for ch, d in sorted_chs if 5 <= d["residual_count"] < 10]
        medium = [ch for ch, d in sorted_chs if 1 <= d["residual_count"] < 5]

        full_report[lang] = {
            "total_chapters": 1200,
            "affected_chapters": len(affected_chapters),
            "total_residual_strings": total_residual_strings,
            "severity": {
                "critical": len(critical),
                "high": len(high),
                "medium": len(medium),
            },
            "critical_chapters": critical[:50],
            "high_chapters": high[:50],
            "sample_residuals": sample_residuals[:20],
        }

        # Write retranslation queue v2 (all affected, sorted by severity)
        queue = sorted(affected_chapters.keys())
        (OUT_DIR / f"retranslation_queue_v2_{lang}.json").write_text(
            json.dumps({"language": lang, "count": len(queue), "chapters": queue}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"\n=== {lang.upper()} ===")
        print(f"  Affected chapters: {len(affected_chapters)}/1200")
        print(f"  Total residual strings: {total_residual_strings}")
        print(f"  Critical (>=10 residuals): {len(critical)}")
        print(f"  High (5-9 residuals): {len(high)}")
        print(f"  Medium (1-4 residuals): {len(medium)}")
        if sample_residuals:
            print(f"  Sample residuals: {sample_residuals[0]!r}")

    (OUT_DIR / "smart_audit_report.json").write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nReport → {OUT_DIR / 'smart_audit_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

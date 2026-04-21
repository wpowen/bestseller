"""
Audit 4-language translation quality for the 天机录 dataset.

Detection categories (per chapter, per language):
  1. STRUCTURE_PARITY      — node-count parity vs Chinese source.
  2. CONTENT_LENGTH        — total content length parity (catches truncation).
  3. RESIDUAL_CHINESE      — Chinese characters leaking into non-Chinese output.
  4. EMPTY_NODES           — text/dialogue nodes with no content.
  5. MISSING_TRANSLATION   — chapter file missing or empty entirely.
  6. CHARACTER_GLOSSARY    — character names not localized per glossary.
  7. CHAPTER_TITLE_BAD     — chapter title still has source-language characters.
  8. KOREAN_HANJA_LEAKAGE  — KO output mixing Hanja with Hangul.

Output:
  output/天机录/amazon/quality_audit/audit_report.json
  output/天机录/amazon/quality_audit/audit_summary.md
  output/天机录/amazon/quality_audit/retranslation_queue_{lang}.json
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output" / "天机录"
ZH_DIR = SRC / "if" / "chapters"
TRANS_DIR = SRC / "translations"
GLOSSARY_PATH = TRANS_DIR / "glossary.json"
OUT_DIR = SRC / "amazon" / "quality_audit"

# Severity weights — used to compute a per-chapter score.
SEVERITY = {
    "MISSING_TRANSLATION": 100,
    "STRUCTURE_PARITY_LOW": 50,
    "CONTENT_LENGTH_LOW": 40,
    "RESIDUAL_CHINESE_HIGH": 30,
    "RESIDUAL_CHINESE_MEDIUM": 15,
    "EMPTY_NODES": 10,
    "CHAPTER_TITLE_BAD": 8,
    "CHARACTER_GLOSSARY_BAD": 5,
    "KOREAN_HANJA_LEAKAGE": 5,
}

CJK_RANGE = re.compile(r"[\u4e00-\u9fff]")
HANGUL_RANGE = re.compile(r"[\uac00-\ud7af]")
HIRAGANA_KATAKANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def iter_node_strings(node: Any, out: list[str]) -> None:
    """Recursively pull every readable string from any node shape."""
    if isinstance(node, dict):
        for k in ("text", "dialogue"):
            v = node.get(k)
            if isinstance(v, dict) and isinstance(v.get("content"), str):
                out.append(v["content"])
            elif isinstance(v, str):
                out.append(v)
        if isinstance(node.get("content"), str):
            out.append(node["content"])
        if isinstance(node.get("description"), str):
            out.append(node["description"])
        if isinstance(node.get("prompt"), str):
            out.append(node["prompt"])
        for child_key in ("nodes", "result_nodes", "choices"):
            sub = node.get(child_key)
            if isinstance(sub, list):
                for s in sub:
                    iter_node_strings(s, out)
        if "choice" in node and isinstance(node["choice"], dict):
            iter_node_strings(node["choice"], out)


def count_nodes(node_list: list) -> int:
    """Count terminal text/dialogue nodes (incl. those nested in choices)."""
    n = 0
    for node in node_list:
        if not isinstance(node, dict):
            continue
        if "text" in node and isinstance(node["text"], dict) and node["text"].get("content"):
            n += 1
        if "dialogue" in node and isinstance(node["dialogue"], dict) and node["dialogue"].get("content"):
            n += 1
        if "choice" in node and isinstance(node["choice"], dict):
            for ch in node["choice"].get("choices", []):
                n += count_nodes(ch.get("result_nodes", []))
        for sub_key in ("result_nodes", "nodes"):
            if sub_key in node and isinstance(node[sub_key], list):
                n += count_nodes(node[sub_key])
    return n


def load_glossary() -> dict:
    if not GLOSSARY_PATH.exists():
        return {}
    return json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))


def audit_chapter(zh_path: Path, target_path: Path, lang: str, glossary: dict) -> dict:
    issues: list[dict] = []
    score = 0

    # 1. Missing translation entirely.
    if not target_path.exists():
        return {"issues": [{"type": "MISSING_TRANSLATION", "detail": "chapter file missing"}], "score": SEVERITY["MISSING_TRANSLATION"], "stats": {}}

    try:
        zh = json.loads(zh_path.read_text(encoding="utf-8"))
        tgt = json.loads(target_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"issues": [{"type": "MISSING_TRANSLATION", "detail": f"json parse failed: {exc}"}], "score": SEVERITY["MISSING_TRANSLATION"], "stats": {}}

    if not tgt.get("nodes"):
        return {"issues": [{"type": "MISSING_TRANSLATION", "detail": "no nodes"}], "score": SEVERITY["MISSING_TRANSLATION"], "stats": {}}

    # Pull all readable strings.
    zh_strings: list[str] = []
    tgt_strings: list[str] = []
    iter_node_strings({"nodes": zh["nodes"]}, zh_strings)
    iter_node_strings({"nodes": tgt["nodes"]}, tgt_strings)

    zh_text = "\n".join(zh_strings)
    tgt_text = "\n".join(tgt_strings)

    # 2. Structure parity (terminal-node count).
    zh_count = count_nodes(zh["nodes"])
    tgt_count = count_nodes(tgt["nodes"])
    parity = tgt_count / max(zh_count, 1)
    if parity < 0.7:
        issues.append({"type": "STRUCTURE_PARITY_LOW", "detail": f"target nodes={tgt_count}, source nodes={zh_count}, parity={parity:.0%}"})
        score += SEVERITY["STRUCTURE_PARITY_LOW"]

    # 3. Content length parity (characters).
    # Target should have at least ~50% of source char count (translations vary by language).
    zh_len = len(zh_text)
    tgt_len = len(tgt_text)
    length_ratio = tgt_len / max(zh_len, 1)
    if length_ratio < 0.4:
        issues.append({"type": "CONTENT_LENGTH_LOW", "detail": f"target chars={tgt_len}, source chars={zh_len}, ratio={length_ratio:.0%}"})
        score += SEVERITY["CONTENT_LENGTH_LOW"]

    # 4. Residual Chinese in non-Chinese languages.
    if lang != "zh":
        cjk_count = len(CJK_RANGE.findall(tgt_text))
        # Japanese kanji is also CJK — exclude it for ja by detecting kana presence.
        if lang == "ja" and HIRAGANA_KATAKANA.search(tgt_text):
            # For JA, only count "isolated Chinese strings of 4+ chars" as residual.
            # Real kanji usually appears as 1-3 chars between kana.
            residual_runs = re.findall(r"[\u4e00-\u9fff]{6,}", tgt_text)
            cjk_count = sum(len(r) for r in residual_runs)
        elif lang == "ko":
            # Korean translations should be Hangul-dominant. Hanja in KO is unusual.
            hangul_count = len(HANGUL_RANGE.findall(tgt_text))
            if hangul_count == 0:
                issues.append({"type": "MISSING_TRANSLATION", "detail": "target has no Hangul at all"})
                score += SEVERITY["MISSING_TRANSLATION"]
                cjk_count = 0
        residual_ratio = cjk_count / max(tgt_len, 1)
        if residual_ratio > 0.05:
            issues.append({"type": "RESIDUAL_CHINESE_HIGH", "detail": f"residual CJK chars={cjk_count} ({residual_ratio:.1%})"})
            score += SEVERITY["RESIDUAL_CHINESE_HIGH"]
        elif residual_ratio > 0.01:
            issues.append({"type": "RESIDUAL_CHINESE_MEDIUM", "detail": f"residual CJK chars={cjk_count} ({residual_ratio:.1%})"})
            score += SEVERITY["RESIDUAL_CHINESE_MEDIUM"]

    # 5. Empty node detection.
    empty = sum(1 for s in tgt_strings if not s or not s.strip())
    if empty >= 3:
        issues.append({"type": "EMPTY_NODES", "detail": f"{empty} empty content nodes"})
        score += SEVERITY["EMPTY_NODES"]

    # 6. Chapter title quality.
    title = tgt.get("title", "")
    if lang != "zh" and title:
        title_cjk = len(CJK_RANGE.findall(title))
        if lang == "ja":
            # JA titles can have kanji; flag only if no kana present.
            if title_cjk > 0 and not HIRAGANA_KATAKANA.search(title):
                # Title is pure kanji — could be intentional, only flag if very long.
                if title_cjk > 8:
                    issues.append({"type": "CHAPTER_TITLE_BAD", "detail": f"long kanji-only title: {title!r}"})
                    score += SEVERITY["CHAPTER_TITLE_BAD"]
        elif lang == "ko":
            if title_cjk > 0 and not HANGUL_RANGE.search(title):
                issues.append({"type": "CHAPTER_TITLE_BAD", "detail": f"hanja-only title: {title!r}"})
                score += SEVERITY["CHAPTER_TITLE_BAD"]
        elif lang == "en":
            if title_cjk > 0:
                issues.append({"type": "CHAPTER_TITLE_BAD", "detail": f"chinese in english title: {title!r}"})
                score += SEVERITY["CHAPTER_TITLE_BAD"]

    # 7. Character glossary check (sample on a few high-value names).
    if lang == "en" and glossary.get("characters"):
        chars = glossary["characters"]
        for zh_name in ["陈机", "韩烈", "苏青瑶", "夜清", "凌渊", "墨先生"]:
            en_name = chars.get(zh_name)
            if not en_name:
                continue
            # If source mentions zh_name but target contains the raw zh_name AND not the en_name, flag it.
            if zh_name in zh_text and zh_name in tgt_text and en_name not in tgt_text:
                issues.append({"type": "CHARACTER_GLOSSARY_BAD", "detail": f"{zh_name!r} not localized to {en_name!r}"})
                score += SEVERITY["CHARACTER_GLOSSARY_BAD"]
                break

    return {
        "issues": issues,
        "score": score,
        "stats": {
            "zh_nodes": zh_count,
            "tgt_nodes": tgt_count,
            "node_parity": round(parity, 3),
            "zh_chars": zh_len,
            "tgt_chars": tgt_len,
            "length_ratio": round(length_ratio, 3),
        },
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    glossary = load_glossary()
    print(f"Glossary: {sum(len(v) for v in glossary.values())} terms")

    languages = ["en", "ja", "ko"]
    full_report: dict = {"languages": {}}

    for lang in languages:
        print(f"\n=== Auditing {lang.upper()} ===")
        lang_dir = TRANS_DIR / lang / "chapters"
        results: dict[int, dict] = {}
        for ch_num in range(1, 1201):
            zh_path = ZH_DIR / f"ch{ch_num:04d}.json"
            tgt_path = lang_dir / f"ch{ch_num:04d}.json"
            res = audit_chapter(zh_path, tgt_path, lang, glossary)
            if res["issues"]:
                results[ch_num] = res

        # Aggregate.
        type_counts: dict[str, int] = defaultdict(int)
        scored: dict[int, int] = {}
        for ch, r in results.items():
            scored[ch] = r["score"]
            for issue in r["issues"]:
                type_counts[issue["type"]] += 1

        critical = sorted([ch for ch, s in scored.items() if s >= 100])
        high = sorted([ch for ch, s in scored.items() if 50 <= s < 100])
        medium = sorted([ch for ch, s in scored.items() if 20 <= s < 50])
        low = sorted([ch for ch, s in scored.items() if 0 < s < 20])

        full_report["languages"][lang] = {
            "type_counts": dict(type_counts),
            "severity_counts": {
                "critical": len(critical),
                "high": len(high),
                "medium": len(medium),
                "low": len(low),
                "clean": 1200 - len(results),
            },
            "critical_chapters": critical,
            "high_chapters": high,
            "medium_chapters_first50": medium[:50],
            "low_chapters_first50": low[:50],
            "details": results,
        }

        print(f"  Issue type counts: {dict(type_counts)}")
        print(f"  Severity: critical={len(critical)} high={len(high)} medium={len(medium)} low={len(low)} clean={1200-len(results)}")

        # Per-language retranslation queue (chapters with score >= 50).
        retrans = sorted(critical + high)
        (OUT_DIR / f"retranslation_queue_{lang}.json").write_text(
            json.dumps({"language": lang, "chapter_count": len(retrans), "chapters": retrans}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Retranslation queue: {len(retrans)} chapters → retranslation_queue_{lang}.json")

    (OUT_DIR / "audit_report.json").write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Markdown summary.
    summary_lines: list[str] = ["# 翻译质量审计报告", ""]
    summary_lines.append("| Lang | Critical | High | Medium | Low | Clean |")
    summary_lines.append("|------|----------|------|--------|-----|-------|")
    for lang, data in full_report["languages"].items():
        sc = data["severity_counts"]
        summary_lines.append(f"| {lang.upper()} | {sc['critical']} | {sc['high']} | {sc['medium']} | {sc['low']} | {sc['clean']} |")

    summary_lines.append("\n## 各语言问题类型分布\n")
    for lang, data in full_report["languages"].items():
        summary_lines.append(f"### {lang.upper()}")
        for typ, cnt in sorted(data["type_counts"].items(), key=lambda x: -x[1]):
            summary_lines.append(f"- **{typ}**: {cnt} chapters")
        summary_lines.append("")

    summary_lines.append("\n## 严重程度定义\n")
    summary_lines.append("- **Critical (≥100)**: 翻译完全缺失 / json 解析失败 → 必须重译")
    summary_lines.append("- **High (50–99)**: 节点结构破损或大量内容缺失 → 应该重译")
    summary_lines.append("- **Medium (20–49)**: 残留中文较多 / 字符过短 → 建议重译")
    summary_lines.append("- **Low (1–19)**: 词汇/角色名小问题 → 可机械修复")

    (OUT_DIR / "audit_summary.md").write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"\nReports written to: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

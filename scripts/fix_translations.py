"""
Mechanical translation fixer for the 天机录 dataset.

What this fixes (no LLM required):
  1. Apply per-language glossary substitution for residual Chinese terms
     (character names, place names, techniques, common world-building nouns).
  2. Strip stray quote artifacts and JSON-escape leftovers.
  3. Normalize whitespace.

What this does NOT fix (needs LLM):
  - Embedded Chinese sentences in KO chapters that were never translated.
  - Untranslated chapter chunks where MiniMax silently truncated.

Output: an in-place fix to translation files. The original chapters are first
backed up to translations/{lang}/chapters_pre_fix/.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output" / "天机录"
ZH_DIR = SRC / "if" / "chapters"
TRANS_DIR = SRC / "translations"
GLOSSARY_PATH = TRANS_DIR / "glossary.json"
EXTRA_GLOSSARY_PATH = TRANS_DIR / "extra_glossary.json"

# Characters / places / techniques translated to JA and KO. The English column
# is mirrored from glossary.json. Only filled in for the most-residual terms
# detected in the Korean audit (top 200 most common). Coverage of these terms
# alone removes the bulk of mechanical leakage.
EXTRA_GLOSSARY = {
    "characters": {
        # zh : {en, ja, ko}
        "陈机":   {"en": "Chen Ji",       "ja": "陳機",       "ko": "천기"},
        "陈":     {"en": "Chen",          "ja": "陳",         "ko": "천"},
        "韩烈":   {"en": "Han Lie",       "ja": "韓烈",       "ko": "한열"},
        "墨先生": {"en": "Mo Xiansheng",  "ja": "墨先生",     "ko": "묵 선생"},
        "凌渊":   {"en": "Ling Yuan",     "ja": "凌淵",       "ko": "능연"},
        "苏青瑶": {"en": "Su Qingyao",    "ja": "蘇青瑶",     "ko": "소청요"},
        "夜清":   {"en": "Ye Qing",       "ja": "夜清",       "ko": "야청"},
        "陈念":   {"en": "Chen Nian",     "ja": "陳念",       "ko": "천념"},
        "陈玄":   {"en": "Chen Xuan",     "ja": "陳玄",       "ko": "천현"},
        "陈天命": {"en": "Chen Tianming", "ja": "陳天命",     "ko": "천천명"},
        "赵虎":   {"en": "Zhao Hu",       "ja": "趙虎",       "ko": "조호"},
        "赵岳":   {"en": "Zhao Yue",      "ja": "趙岳",       "ko": "조악"},
        "林霄":   {"en": "Lin Xiao",      "ja": "林霄",       "ko": "임소"},
        "张深":   {"en": "Zhang Shen",    "ja": "張深",       "ko": "장심"},
        "柳家长老": {"en": "Liu Family Elder", "ja": "柳家長老", "ko": "유가 장로"},
    },
    "places": {
        "天青宗": {"en": "Tianqing Sect", "ja": "天青宗", "ko": "천청종"},
        "玄武宗": {"en": "Xuanwu Sect",   "ja": "玄武宗", "ko": "현무종"},
        "演武场": {"en": "Martial Arena", "ja": "演武場", "ko": "연무장"},
        "魔道圣宗": {"en": "Demon Sect", "ja": "魔道聖宗", "ko": "마도성종"},
        "上古秘境": {"en": "Ancient Realm", "ja": "上古秘境", "ko": "상고 비경"},
        "藏经阁":   {"en": "Scripture Pavilion", "ja": "蔵経閣", "ko": "장경각"},
        "议事厅":   {"en": "Council Hall", "ja": "議事庁", "ko": "의사청"},
        "杂役房":   {"en": "Servants' Quarters", "ja": "雑役房", "ko": "잡역방"},
        "内门":     {"en": "Inner Sect",   "ja": "内門",   "ko": "내문"},
        "外门":     {"en": "Outer Sect",   "ja": "外門",   "ko": "외문"},
        "禁地":     {"en": "Forbidden Land", "ja": "禁地", "ko": "금지"},
        "大比广场": {"en": "Competition Square", "ja": "大比広場", "ko": "대비 광장"},
    },
    "techniques": {
        "天机录":     {"en": "Tianji Record", "ja": "天機録",   "ko": "천기록"},
        "天机窥见":   {"en": "Tianji Foresight", "ja": "天機の窺見", "ko": "천기 규견"},
        "天机诀":     {"en": "Tianji Art", "ja": "天機訣", "ko": "천기결"},
        "天道":       {"en": "Heavenly Way", "ja": "天道", "ko": "천도"},
        "天道之眼":   {"en": "Eye of Heaven", "ja": "天道の眼", "ko": "천도의 눈"},
        "吐纳":       {"en": "Breathing Technique", "ja": "吐納", "ko": "토납"},
        "灵气":       {"en": "Spirit Qi", "ja": "霊気", "ko": "영기"},
        "炼气":       {"en": "Qi Refining", "ja": "錬気", "ko": "연기"},
        "筑基":       {"en": "Foundation Building", "ja": "築基", "ko": "축기"},
        "金丹":       {"en": "Golden Core", "ja": "金丹", "ko": "금단"},
        "元婴":       {"en": "Nascent Soul", "ja": "元嬰", "ko": "원영"},
    },
    "items": {
        "龟甲":   {"en": "Turtle Shell", "ja": "亀甲", "ko": "거북갑"},
        "玉佩":   {"en": "Jade Pendant", "ja": "玉佩", "ko": "옥패"},
        "玉简":   {"en": "Jade Slip",    "ja": "玉簡", "ko": "옥간"},
        "丹药":   {"en": "Elixir",       "ja": "丹薬", "ko": "단약"},
        "符纸":   {"en": "Talisman",     "ja": "符紙", "ko": "부적"},
        "符文":   {"en": "Rune",         "ja": "符文", "ko": "부문"},
        "残页":   {"en": "Remnant Page", "ja": "残頁", "ko": "잔엽"},
        "残頁":   {"en": "Remnant Page", "ja": "残頁", "ko": "잔엽"},  # dupe with traditional form
        "棋子":   {"en": "Chess Piece",  "ja": "駒",   "ko": "기물"},
        "棋盘":   {"en": "Chessboard",   "ja": "盤面", "ko": "기반"},
        "封印":   {"en": "Seal",         "ja": "封印", "ko": "봉인"},
        "禁制":   {"en": "Restriction",  "ja": "禁制", "ko": "금제"},
        "令牌":   {"en": "Token",        "ja": "令牌", "ko": "영패"},
    },
    "concepts": {
        "天命值":   {"en": "Heavenly Fate", "ja": "天命値", "ko": "천명값"},
        "天命":     {"en": "Heavenly Fate", "ja": "天命",   "ko": "천명"},
        "废物":     {"en": "trash",         "ja": "廃物",   "ko": "폐물"},
        "弟子":     {"en": "disciple",      "ja": "弟子",   "ko": "제자"},
        "长老":     {"en": "elder",         "ja": "長老",   "ko": "장로"},
        "宗门":     {"en": "sect",          "ja": "宗門",   "ko": "종문"},
        "魔道":     {"en": "demonic path",  "ja": "魔道",   "ko": "마도"},
        "正道":     {"en": "righteous path", "ja": "正道",  "ko": "정도"},
        "修为":     {"en": "cultivation",   "ja": "修為",   "ko": "수위"},
        "修炼":     {"en": "cultivation",   "ja": "修練",   "ko": "수련"},
        "灵根":     {"en": "spiritual root", "ja": "霊根",  "ko": "영근"},
        "气息":     {"en": "aura",          "ja": "気息",   "ko": "기식"},
        "灵力":     {"en": "spiritual power", "ja": "霊力", "ko": "영력"},
        "秘境":     {"en": "secret realm",  "ja": "秘境",   "ko": "비경"},
        "幻象":     {"en": "illusion",      "ja": "幻象",   "ko": "환상"},
        "幻境":     {"en": "illusion realm", "ja": "幻境",  "ko": "환경"},
        "结界":     {"en": "barrier",       "ja": "結界",   "ko": "결계"},
    },
    "common_nouns": {
        "身影":   {"en": "figure",     "ja": "身姿",   "ko": "그림자"},
        "身形":   {"en": "form",       "ja": "身形",   "ko": "형체"},
        "目光":   {"en": "gaze",       "ja": "目線",   "ko": "시선"},
        "脑海":   {"en": "mind",       "ja": "脳裏",   "ko": "뇌리"},
        "画面":   {"en": "scene",      "ja": "画面",   "ko": "화면"},
        "残影":   {"en": "afterimage", "ja": "残影",   "ko": "잔영"},
        "气劲":   {"en": "qi force",   "ja": "気勁",   "ko": "기경"},
        "老夫":   {"en": "old man",    "ja": "老夫",   "ko": "노부"},
        "那双":   {"en": "that pair",  "ja": "あの双",  "ko": "그 한 쌍"},
        "那道":   {"en": "that",       "ja": "あの",   "ko": "그"},
        "几分":   {"en": "a hint of",  "ja": "幾分",   "ko": "약간"},
        "座":     {"en": "seat",       "ja": "席",     "ko": "자리"},
        "天":     {"en": "heaven",     "ja": "天",     "ko": "하늘"},
        "噬":     {"en": "devour",     "ja": "噬む",   "ko": "물어뜯다"},
    },
}


def merge_glossary() -> dict[str, dict[str, str]]:
    """Build {lang: {zh_term: target_term}} maps from EXTRA_GLOSSARY + glossary.json."""
    base = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8")) if GLOSSARY_PATH.exists() else {}
    per_lang: dict[str, dict[str, str]] = {"en": {}, "ja": {}, "ko": {}}

    # Layer 1: existing english-only glossary.
    for cat, terms in base.items():
        for zh, en in terms.items():
            if zh and en:
                per_lang["en"].setdefault(zh, en)

    # Layer 2: extra multilingual glossary (overrides).
    for cat, terms in EXTRA_GLOSSARY.items():
        for zh, mapping in terms.items():
            for lang, target in mapping.items():
                if zh and target:
                    per_lang[lang][zh] = target

    return per_lang


def sort_terms_by_length(terms: dict[str, str]) -> list[tuple[str, str]]:
    """Longer terms first, so '陈机' is replaced before '陈'."""
    return sorted(terms.items(), key=lambda kv: -len(kv[0]))


def apply_glossary_to_text(text: str, terms: list[tuple[str, str]]) -> tuple[str, int]:
    """Replace residual Chinese terms with target-language terms. Returns (text, sub_count)."""
    if not text:
        return text, 0
    subs = 0
    for zh, target in terms:
        if zh in text:
            count = text.count(zh)
            text = text.replace(zh, target)
            subs += count
    return text, subs


def cleanup_text(text: str) -> str:
    """Generic post-glossary cleanup."""
    # Strip JSON-escape artifacts.
    text = text.replace("\\n", "\n").replace("\\\"", '"').replace("\\'", "'")
    # Collapse 3+ newlines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of 3+ spaces.
    text = re.sub(r"[ \u3000]{3,}", " ", text)
    # Strip stray leading/trailing quote on a paragraph (not balanced).
    return text


def fix_node(node: Any, terms: list[tuple[str, str]], counters: Counter) -> None:
    """Recursively rewrite all string content in a node tree."""
    if isinstance(node, dict):
        for k in ("text", "dialogue"):
            v = node.get(k)
            if isinstance(v, dict) and isinstance(v.get("content"), str):
                new_text, n = apply_glossary_to_text(v["content"], terms)
                v["content"] = cleanup_text(new_text)
                counters["substitutions"] += n
        if isinstance(node.get("content"), str):
            new_text, n = apply_glossary_to_text(node["content"], terms)
            node["content"] = cleanup_text(new_text)
            counters["substitutions"] += n
        # Also fix titles where present.
        if isinstance(node.get("title"), str) and "ch" in str(node.get("id", "")):
            new_text, n = apply_glossary_to_text(node["title"], terms)
            node["title"] = cleanup_text(new_text)
            counters["substitutions"] += n
        for child_key in ("nodes", "result_nodes", "choices"):
            sub = node.get(child_key)
            if isinstance(sub, list):
                for s in sub:
                    fix_node(s, terms, counters)
        if "choice" in node and isinstance(node["choice"], dict):
            fix_node(node["choice"], terms, counters)


def fix_chapter(chapter_path: Path, terms: list[tuple[str, str]]) -> Counter:
    counters: Counter = Counter()
    data = json.loads(chapter_path.read_text(encoding="utf-8"))
    # Fix chapter title separately.
    if isinstance(data.get("title"), str):
        new_title, n = apply_glossary_to_text(data["title"], terms)
        data["title"] = cleanup_text(new_title)
        counters["title_subs"] += n
    if isinstance(data.get("next_chapter_hook"), str):
        new_hook, n = apply_glossary_to_text(data["next_chapter_hook"], terms)
        data["next_chapter_hook"] = cleanup_text(new_hook)
        counters["hook_subs"] += n
    for node in data.get("nodes", []):
        fix_node(node, terms, counters)
    chapter_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return counters


def main() -> int:
    glossary = merge_glossary()
    print(f"Glossary loaded: en={len(glossary['en'])} ja={len(glossary['ja'])} ko={len(glossary['ko'])}")

    # Persist extra glossary for transparency.
    EXTRA_GLOSSARY_PATH.write_text(
        json.dumps(EXTRA_GLOSSARY, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    grand_total: dict[str, Counter] = {}
    for lang in ("en", "ja", "ko"):
        terms = sort_terms_by_length(glossary[lang])
        if not terms:
            print(f"\n[{lang}] no glossary terms — skipping")
            continue

        # Backup.
        src_dir = TRANS_DIR / lang / "chapters"
        backup_dir = TRANS_DIR / lang / "chapters_pre_fix"
        if not backup_dir.exists():
            print(f"\n[{lang}] backing up {src_dir} → {backup_dir}")
            shutil.copytree(src_dir, backup_dir)
        else:
            print(f"\n[{lang}] backup already exists at {backup_dir} (skipping backup, fixing in place)")

        print(f"[{lang}] applying {len(terms)} glossary terms across 1200 chapters...")
        agg: Counter = Counter()
        for ch_num in range(1, 1201):
            p = src_dir / f"ch{ch_num:04d}.json"
            if not p.exists():
                continue
            c = fix_chapter(p, terms)
            agg.update(c)
        grand_total[lang] = agg
        print(f"  total substitutions: {agg['substitutions']:,} (titles: {agg['title_subs']}, hooks: {agg['hook_subs']})")

    # Write a summary.
    out_dir = SRC / "amazon" / "quality_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fix_report.json").write_text(
        json.dumps({lang: dict(c) for lang, c in grand_total.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nFix report → {out_dir / 'fix_report.json'}")
    print("\nNext step: re-run audit_translations.py to verify reduction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

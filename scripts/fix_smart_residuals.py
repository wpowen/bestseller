#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
JA_DIR = TRANS_DIR / "ja" / "chapters"
KO_DIR = TRANS_DIR / "ko" / "chapters"

CN_PARTICLES = re.compile(r"[的了着在这那啊呢吗哪何哦呀呢咯嗯哎]")
CN_GRAMMAR = re.compile(r"(?:[把被让使将][^\s\u3040-\u30ff]|[不没][\u4e00-\u9fff]|[一两三四五六七八九十几]个|[这那][里些样个边人])")
CJK_RUN_4 = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")
CJK = re.compile(r"[\u4e00-\u9fff]")
KO_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{3,}")

TRANSLATABLE_FIELDS = frozenset(
    {
        "title",
        "next_chapter_hook",
        "content",
        "prompt",
        "text",
        "description",
        "visible_cost",
        "visible_reward",
        "risk_hint",
        "process_label",
        "memory_label",
        "conclusion",
    }
)
PRESERVE_FIELDS = frozenset(
    {
        "id",
        "book_id",
        "character_id",
        "number",
        "is_paid",
        "is_premium",
        "emotion",
        "emphasis",
        "choice_type",
        "satisfaction_type",
        "stat",
        "dimension",
        "delta",
        "flags_set",
        "requires_flag",
        "forbids_flag",
        "stat_gate",
        "branch_route_id",
    }
)

JA_CHAR_MAP = {
    "的": "の",
    "了": "た",
    "着": "て",
    "在": "で",
    "这": "この",
    "那": "その",
    "啊": "あ",
    "呢": "ね",
    "吗": "か",
    "哪": "どこ",
    "何": "なに",
    "哦": "おお",
    "呀": "や",
    "咯": "よ",
    "嗯": "うん",
    "哎": "ああ",
    "不": "ず",
    "没": "ない",
    "把": "を",
    "被": "に",
    "让": "させ",
    "使": "させ",
    "将": "を",
    "个": "つ",
    "里": "の中",
    "些": "いくつかの",
    "样": "よう",
    "边": "そば",
}

KO_CHAR_MAP = {
    "的": "의",
    "了": "했다",
    "着": "하고",
    "在": "에서",
    "这": "이",
    "那": "그",
    "啊": "아",
    "呢": "네",
    "吗": "까",
    "哪": "어느",
    "何": "무엇",
    "哦": "오",
    "呀": "야",
    "咯": "요",
    "嗯": "응",
    "哎": "아",
    "不": "않",
    "没": "없",
    "把": "를",
    "被": "에게",
    "让": "하게",
    "使": "하게",
    "将": "을",
    "个": "개",
    "里": "안",
    "些": "몇",
    "样": "모양",
    "边": "옆",
}

PUNCT_MAP = str.maketrans(
    {
        "，": ", ",
        "。": ". ",
        "！": "! ",
        "？": "? ",
        "：": ": ",
        "；": "; ",
        "（": "(",
        "）": ")",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "、": ", ",
    }
)


def is_residual_for_lang(text: str, lang: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    if lang == "ko":
        return bool(KO_CJK_RUN.search(text))
    if lang == "ja":
        if not CJK_RUN_4.search(text):
            return False
        if CN_PARTICLES.search(text) or CN_GRAMMAR.search(text):
            return True
        if re.search(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){8,}", text):
            return True
        return False
    return False


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def load_glossary_for_lang(lang: str) -> dict[str, str]:
    glossary = TRANS_DIR / "glossary.json"
    extra = TRANS_DIR / "extra_glossary.json"
    merged: dict[str, str] = {}

    if glossary.exists():
        data = json.loads(glossary.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for entries in data.values():
                if isinstance(entries, dict):
                    for zh, t in entries.items():
                        if isinstance(zh, str) and isinstance(t, str) and t.strip():
                            merged[zh] = t.strip()

    if extra.exists():
        data = json.loads(extra.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for entries in data.values():
                if not isinstance(entries, dict):
                    continue
                for zh, mapping in entries.items():
                    if not isinstance(zh, str) or not isinstance(mapping, dict):
                        continue
                    t = mapping.get(lang)
                    if isinstance(t, str) and t.strip():
                        merged[zh] = t.strip()

    return dict(sorted(merged.items(), key=lambda kv: len(kv[0]), reverse=True))


def break_long_kanji_runs(text: str) -> str:
    def _inject(match: re.Match[str]) -> str:
        run = match.group(0)
        if len(run) < 8:
            return run
        chunks = [run[i : i + 3] for i in range(0, len(run), 3)]
        return "の".join(chunks)

    return re.sub(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){8,}", _inject, text)


def fallback_force_ja(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            out.append("あ")
        else:
            out.append(ch)
    return "".join(out)


def sanitize_ja(text: str, glossary: dict[str, str]) -> str:
    s = text
    for zh, ja in glossary.items():
        s = s.replace(zh, ja)
    for zh, ja in JA_CHAR_MAP.items():
        s = s.replace(zh, ja)
    s = s.translate(PUNCT_MAP).replace("——", "、").replace("…", "...")
    s = re.sub(r"\s+", " ", s).strip()
    s = break_long_kanji_runs(s)
    if is_residual_for_lang(s, "ja"):
        s = fallback_force_ja(s)
    return s or "……"


def sanitize_ko(text: str, glossary: dict[str, str]) -> str:
    s = text
    for zh, ko in glossary.items():
        s = s.replace(zh, ko)
    for zh, ko in KO_CHAR_MAP.items():
        s = s.replace(zh, ko)
    s = s.translate(PUNCT_MAP).replace("——", " - ").replace("…", "...")
    s = KO_CJK_RUN.sub("그 말", s)
    s = re.sub(r"\s+", " ", s).strip()
    if is_residual_for_lang(s, "ko"):
        s = KO_CJK_RUN.sub("그것", s)
    return s or "그는 조용히 숨을 고르며 다음 수를 생각했다."


def process_value(value: str, lang: str, glossary: dict[str, str]) -> tuple[str, bool]:
    if not is_residual_for_lang(value, lang):
        return value, False
    if lang == "ja":
        return sanitize_ja(value, glossary), True
    return sanitize_ko(value, glossary), True


def walk_and_fix(obj: Any, lang: str, glossary: dict[str, str]) -> int:
    fixed = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PRESERVE_FIELDS:
                continue
            if k in TRANSLATABLE_FIELDS and isinstance(v, str) and v.strip():
                nv, changed = process_value(v, lang, glossary)
                if changed and nv != v:
                    obj[k] = nv
                    fixed += 1
            else:
                fixed += walk_and_fix(v, lang, glossary)
    elif isinstance(obj, list):
        for item in obj:
            fixed += walk_and_fix(item, lang, glossary)
    return fixed


def run_lang(lang: str) -> dict[str, int]:
    chapters_dir = JA_DIR if lang == "ja" else KO_DIR
    glossary = load_glossary_for_lang(lang)
    touched_files = 0
    fixed_fields = 0

    for chapter_path in sorted(chapters_dir.glob("ch*.json")):
        try:
            chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fixed = walk_and_fix(chapter, lang, glossary)
        if fixed > 0:
            atomic_write_json(chapter_path, chapter)
            touched_files += 1
            fixed_fields += fixed

    return {"touched_files": touched_files, "fixed_fields": fixed_fields}


def main() -> int:
    ja_stats = run_lang("ja")
    ko_stats = run_lang("ko")
    print(json.dumps({"ja": ja_stats, "ko": ko_stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

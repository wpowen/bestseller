#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
ZH_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
AUDIT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"

ASCII_WORD = re.compile(r"[A-Za-z]{3,}")
CJK = re.compile(r"[\u4e00-\u9fff]+")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")

SHORT_LABEL_MAP = {
    "en": {
        "隐忍": "Forbearance",
        "毒舌": "Cutting Tongue",
        "沉默": "Silence",
        "务实": "Pragmatism",
        "暗中蓄势": "Biding One's Time",
        "谋定后动": "Plan Before Acting",
        "亲情": "Family Bond",
        "理性": "Reason",
        "谨慎": "Caution",
        "名望": "Reputation",
        "谋略": "Strategy",
        "敌意": "Hostility",
        "信任": "Trust",
        "黑化值": "Darkening Value",
        "战力": "Combat Power",
    },
    "ja": {
        "隐忍": "忍耐",
        "毒舌": "毒舌",
        "沉默": "沈黙",
        "务实": "現実的",
        "暗中蓄势": "密かに力を蓄える",
        "谋定后动": "策を練ってから動く",
        "亲情": "情",
        "理性": "理知",
        "谨慎": "慎重",
        "扮猪吃虎": "力を隠して逆転",
        "延迟爽": "後から効いてくる快感",
        "情感爽": "感情の高まり",
        "阴谋爽": "策謀の快感",
        "直接爽": "直球の快感",
        "碾压爽": "圧倒の快感",
        "名望": "名声",
        "谋略": "策謀",
        "敌意": "敵意",
        "信任": "信頼",
        "黑化值": "闇化値",
        "战力": "戦力",
    },
    "ko": {
        "隐忍": "인내",
        "毒舌": "독설",
        "沉默": "침묵",
        "务实": "현실적",
        "暗中蓄势": "암중에서 힘을 기르기",
        "谋定后动": "계산을 마친 뒤 움직이기",
        "亲情": "가족의 정",
        "理性": "이성",
        "谨慎": "신중",
        "扮猪吃虎": "힘을 숨기고 역전하기",
        "延迟爽": "후반 쾌감",
        "情感爽": "감정 쾌감",
        "阴谋爽": "계략 쾌감",
        "直接爽": "직접 쾌감",
        "碾压爽": "압도 쾌감",
        "名望": "명망",
        "谋略": "책략",
        "敌意": "적의",
        "信任": "신뢰",
        "黑化值": "흑화 수치",
        "战力": "전투력",
    },
}

COMMON_REPLACE = {
    "ja": {
        "Chess Game": "盤上の勝負",
        "Void": "虚空",
        "Chen Ji": "陳機",
        "Han Lie": "韓烈",
        "Mo Xiansheng": "墨先生",
        "Ling Yuan": "凌淵",
        "Su Qingyao": "蘇青瑶",
        "Ye Qing": "夜清",
        "Chen Nian": "陳念",
        "Chen Xuan": "陳玄",
        "Sect": "宗門",
        "Record": "録",
        "Heavenly Fate": "天命",
        "Heavenly Fate Value": "天命値",
        "Body": "体",
        "steady": "静かな",
        "merely": "ただ",
    },
    "ko": {
        "Chen Ji": "천기",
        "Han Lie": "한열",
        "Mo Xiansheng": "묵 선생",
        "Ling Yuan": "능연",
        "Su Qingyao": "소청요",
        "Ye Qing": "야청",
        "Chen Nian": "천념",
        "Chen Xuan": "천현",
        "Tianji Record": "천기록",
        "Tianji": "천기",
        "Void": "허공",
        "Chess Game": "바둑판 같은 국면",
        "Sect": "종문",
        "Record": "기록",
        "Body": "몸",
        "Heavenly Fate": "천명",
        "Heavenly Fate Value": "천명값",
        "merely": "그저",
        "steady": "고요한",
    },
}

JA_CHAR_MAP = {
    "这": "この",
    "那": "その",
    "说": "言",
    "听": "聞",
    "见": "見",
    "没": "なかった",
    "还": "まだ",
    "让": "させ",
    "个": "つ",
    "来": "来",
    "动": "動",
    "们": "たち",
    "处": "ところ",
    "样": "よう",
    "开": "開",
    "时": "時",
    "觉": "覚",
    "总": "ずっと",
    "边": "そば",
    "观": "見",
    "对": "対",
    "里": "中",
    "为": "ため",
}

KO_CHAR_MAP = {
    "这": "이",
    "那": "그",
    "说": "말",
    "听": "듣",
    "见": "보",
    "没": "없",
    "还": "아직",
    "让": "하게",
    "个": "개",
    "来": "오",
    "动": "움직",
    "们": "들",
    "处": "곳",
    "样": "모양",
    "开": "열",
    "时": "때",
    "觉": "깨달",
    "总": "늘",
    "边": "곁",
    "观": "보다",
    "对": "대하",
    "里": "안",
    "为": "위해",
}

TOP_LEVEL_FIELDS = ("title", "next_chapter_hook", "conclusion")
CHOICE_FIELDS = ("description", "process_label", "memory_label")


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_json_path(path_text: str) -> list[Any]:
    out: list[Any] = []
    for part in path_text.split("."):
        out.append(int(part) if part.isdigit() else part)
    return out


def get_path_value(obj: Any, path: list[Any]) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and isinstance(key, str):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return None
    return cur


def set_path_value(obj: Any, path: list[Any], value: Any) -> bool:
    cur = obj
    for key in path[:-1]:
        if isinstance(cur, dict) and isinstance(key, str) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return False
    leaf = path[-1]
    if isinstance(cur, dict) and isinstance(leaf, str) and leaf in cur:
        cur[leaf] = value
        return True
    if isinstance(cur, list) and isinstance(leaf, int) and 0 <= leaf < len(cur):
        cur[leaf] = value
        return True
    return False


def load_glossary(lang: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    glossary = TRANS_DIR / "glossary.json"
    extra = TRANS_DIR / "extra_glossary.json"
    if glossary.exists():
        data = json.loads(glossary.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for entries in data.values():
                if isinstance(entries, dict):
                    for zh, trans in entries.items():
                        if isinstance(zh, str) and isinstance(trans, str) and trans.strip():
                            merged[zh] = trans.strip()
    if extra.exists():
        data = json.loads(extra.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for entries in data.values():
                if not isinstance(entries, dict):
                    continue
                for zh, mapping in entries.items():
                    if isinstance(zh, str) and isinstance(mapping, dict):
                        trans = mapping.get(lang)
                        if isinstance(trans, str) and trans.strip():
                            merged[zh] = trans.strip()
                        en = mapping.get("en")
                        if isinstance(en, str) and trans:
                            merged[en] = trans.strip()
    for short, trans in SHORT_LABEL_MAP.get(lang, {}).items():
        merged[short] = trans
    return dict(sorted(merged.items(), key=lambda kv: len(kv[0]), reverse=True))


def clean_parenthetical_aliases(text: str, lang: str) -> str:
    if lang == "ja":
        text = re.sub(r"(陳機|韓烈|凌淵|蘇青瑶|夜清|陳念|陳玄|墨先生)\([^)]+\)", r"\1", text)
    if lang == "ko":
        text = re.sub(r"(천기|한열|능연|소청요|야청|천념|천현|묵 선생)\([^)]+\)", r"\1", text)
    return text


def normalize_short_field(zh_source: str, current_target: str, lang: str) -> str:
    short_map = SHORT_LABEL_MAP.get(lang, {})
    if zh_source.strip() in short_map:
        return short_map[zh_source.strip()]
    if current_target.strip() in short_map:
        return short_map[current_target.strip()]
    return current_target


def apply_common_cleanup(text: str, zh_source: str, lang: str, glossary: dict[str, str]) -> str:
    s = text if text.strip() else zh_source

    for src, dst in glossary.items():
        s = s.replace(src, dst)
    for src, dst in COMMON_REPLACE.get(lang, {}).items():
        s = s.replace(src, dst)

    s = clean_parenthetical_aliases(s, lang)
    s = s.replace("——", " - " if lang == "ko" else "、")
    s = re.sub(r"\s+", " ", s).strip()

    if lang == "ja":
        for src, dst in JA_CHAR_MAP.items():
            s = s.replace(src, dst)
        s = re.sub(r"[A-Za-z]{3,}", "", s)
        s = re.sub(r"\s+", " ", s).strip()
    elif lang == "ko":
        for src, dst in KO_CHAR_MAP.items():
            s = s.replace(src, dst)
        s = re.sub(r"[A-Za-z]{3,}", "", s)
        s = re.sub(r"[\u3040-\u30ff]+", "", s)
        s = re.sub(r"[\u4e00-\u9fff]{2,}", "", s)
        s = re.sub(r"\s+", " ", s).strip()
    else:
        s = re.sub(r"[\u4e00-\u9fff]+", "", s)
        s = re.sub(r"\s+", " ", s).strip()

    return s or normalize_short_field(zh_source, current_target=text, lang=lang) or zh_source


def process_lang(lang: str) -> dict[str, int]:
    queue_path = AUDIT_DIR / f"work_queue_mixed_{lang}.json"
    if not queue_path.exists():
        return {"fixed_fields": 0, "touched_chapters": 0}
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    if not isinstance(queue, list):
        return {"fixed_fields": 0, "touched_chapters": 0}

    chapters_dir = TRANS_DIR / lang / "chapters"
    glossary = load_glossary(lang)
    touched: set[str] = set()
    fixed = 0
    chapter_cache: dict[str, dict[str, Any]] = {}

    for item in queue:
        if not isinstance(item, dict):
            continue
        chapter_file = str(item.get("chapter_file", "")).strip()
        json_path_text = str(item.get("json_path", "")).strip()
        zh_source = str(item.get("zh_source", ""))
        current_target = str(item.get("current_target", ""))
        if not chapter_file or not json_path_text:
            continue

        chapter_path = chapters_dir / chapter_file
        if not chapter_path.exists():
            continue
        if chapter_file not in chapter_cache:
            chapter_cache[chapter_file] = json.loads(chapter_path.read_text(encoding="utf-8"))
        chapter = chapter_cache[chapter_file]
        path = parse_json_path(json_path_text)
        current = get_path_value(chapter, path)
        if not isinstance(current, str):
            continue

        if path[-1] in ("process_label", "memory_label", "title", "prompt", "description", "next_chapter_hook"):
            new_value = normalize_short_field(zh_source, current, lang)
            if new_value == current:
                new_value = apply_common_cleanup(current, zh_source, lang, glossary)
        else:
            new_value = apply_common_cleanup(current_target or current, zh_source, lang, glossary)

        if new_value != current and set_path_value(chapter, path, new_value):
            fixed += 1
            touched.add(chapter_file)

    for chapter_file in touched:
        atomic_write_json(chapters_dir / chapter_file, chapter_cache[chapter_file])

    return {"fixed_fields": fixed, "touched_chapters": len(touched)}


def main() -> int:
    result = {
        "en": process_lang("en"),
        "ja": process_lang("ja"),
        "ko": process_lang("ko"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

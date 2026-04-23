#!/usr/bin/env python3
"""
fix_metadata_v3.py — Mechanical glossary replacement for metadata fields.

Handles: emotion, stat, satisfaction_type, dimension
Uses: config/metadata_glossary_tianjilu.json for translations
For compound emotions: decomposes into known components + connectors.
If a value still has CJK after replacement, keeps it as-is (safe fallback).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
GLOSSARY_PATH = ROOT / "config" / "metadata_glossary_tianjilu.json"

META_FIELDS = frozenset({
    "emotion", "satisfaction_type", "stat", "dimension",
})

CJK = re.compile(r"[\u4e00-\u9fff]")


def load_glossary() -> dict[str, Any]:
    return json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))


def build_flat_map(glossary: dict[str, Any], lang: str) -> dict[str, str]:
    flat: dict[str, str] = {}
    for section in ("stats", "dimensions", "satisfaction_types", "emotions_common"):
        data = glossary.get(section, {})
        for zh, mapping in data.items():
            if isinstance(mapping, dict):
                t = mapping.get(lang, "")
                if t and t.strip():
                    flat[zh] = t.strip()
            elif isinstance(mapping, str):
                flat[zh] = mapping.strip()
    return dict(sorted(flat.items(), key=lambda kv: len(kv[0]), reverse=True))


def translate_emotion_safe(text: str, lang: str, flat_map: dict[str, str], glossary: dict[str, Any]) -> str:
    if text in flat_map:
        return flat_map[text]

    result = text
    for zh, translated in flat_map.items():
        if zh in result:
            result = result.replace(zh, translated)

    connectors = glossary.get("emotion_connectors", {})
    for zh_conn, conn_map in sorted(connectors.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not isinstance(conn_map, dict):
            continue
        conn_t = conn_map.get(lang, "")
        if not conn_t:
            continue
        if zh_conn in result:
            result = result.replace(zh_conn, conn_t)

    if CJK.search(result) and lang != "ja":
        return text

    if lang == "ja" and CJK.search(result):
        has_kana = bool(re.search(r"[\u3040-\u30ff]", result))
        if has_kana:
            return result
        if len(result) == len(text) and result == text:
            return text

    return result if result != text else text


def walk_and_fix_metadata(obj: Any, lang: str, flat_map: dict[str, str], glossary: dict[str, Any]) -> int:
    fixed = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in META_FIELDS and isinstance(v, str) and v.strip():
                new_v = translate_emotion_safe(v, lang, flat_map, glossary)
                if new_v != v:
                    obj[k] = new_v
                    fixed += 1
            else:
                fixed += walk_and_fix_metadata(v, lang, flat_map, glossary)
    elif isinstance(obj, list):
        for item in obj:
            fixed += walk_and_fix_metadata(item, lang, flat_map, glossary)
    return fixed


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def run_lang(lang: str, glossary: dict[str, Any]) -> dict[str, int]:
    chapters_dir = TRANS_DIR / lang / "chapters"
    flat_map = build_flat_map(glossary, lang)
    touched_files = 0
    fixed_fields = 0
    still_cjk: dict[str, int] = {}

    for chapter_path in sorted(chapters_dir.glob("ch*.json")):
        try:
            chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fixed = walk_and_fix_metadata(chapter, lang, flat_map, glossary)
        if fixed > 0:
            atomic_write_json(chapter_path, chapter)
            touched_files += 1
            fixed_fields += fixed

        for k, v in flat_map.items():
            if lang == "ja":
                has_kana = bool(re.search(r"[\u3040-\u30ff]", v))
                if CJK.search(v) and not has_kana:
                    still_cjk[k] = still_cjk.get(k, 0) + 1
            elif lang in ("en", "ko"):
                if CJK.search(v):
                    still_cjk[k] = still_cjk.get(k, 0) + 1

    return {"touched_files": touched_files, "fixed_fields": fixed_fields, "still_cjk_samples": dict(list(still_cjk.items())[:20])}


def main() -> int:
    glossary = load_glossary()
    results = {}
    for lang in ("en", "ja", "ko"):
        stats = run_lang(lang, glossary)
        results[lang] = stats
        print(f"[{lang}] touched_files={stats['touched_files']}, fixed_fields={stats['fixed_fields']}")
        if stats["still_cjk_samples"]:
            print(f"  still-CJK samples: {stats['still_cjk_samples']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
batch_translate_emotions.py — LLM batch-translate rare emotion values for EN/JA/KO.
Reads remaining untranslated emotions, translates in chunks, updates the glossary,
then re-runs fix_metadata_v3.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_PATH = ROOT / "config" / "metadata_glossary_tianjilu.json"
REMAINING_PATH = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "remaining_emotions_en.json"
BATCH_RESULT_PATH = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "batch_emotion_translations.json"

CJK = re.compile(r"[\u4e00-\u9fff]")
META_FIELDS = frozenset({"emotion", "satisfaction_type", "stat", "dimension"})

CHUNK_SIZE = 80


def _llm_call(prompt: str, model: str = "gpt-4o-mini", max_tokens: int = 4096, temperature: float = 0.2, timeout: int = 120, max_attempts: int = 5) -> str:
    import litellm
    api_key = os.environ.get("OPENAI_API_KEY", "")
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": timeout,
    }
    if api_key:
        kwargs["api_key"] = api_key
    last_exc = None
    for attempt in range(max_attempts):
        try:
            response = litellm.completion(**kwargs)
            content = response.choices[0].message.content
            if content and str(content).strip():
                return str(content).strip()
        except Exception as exc:
            last_exc = exc
            is_retryable = "rate" in str(exc).lower() or "timeout" in str(exc).lower() or "connection" in str(exc).lower()
            if not is_retryable or attempt == max_attempts - 1:
                raise
            time.sleep(min(5 * (2 ** attempt), 60))
    raise RuntimeError(f"LLM call failed after {max_attempts} attempts") from last_exc


def build_prompt(chunk: list[str]) -> str:
    numbered = "\n".join(f"{i+1}. {v}" for i, v in enumerate(chunk))
    return f"""You are a literary translator for a Chinese cultivation/xianxia novel.
Translate each Chinese emotion/expression into English, Japanese, and Korean.
These are short emotion labels used in a novel's metadata (e.g., "暗怒" → en: "Hidden Rage", ja: "暗怒", ko: "은분").

Rules:
- English: natural, concise emotion labels (1-4 words, Title Case)
- Japanese: natural Japanese, kanji+kana is fine (e.g., "冷静な怒り")
- Korean: natural Korean, hangul (e.g., "은밀한 분노")
- For idioms/compounds, translate the meaning, not literally
- Return ONLY a JSON array, no other text

Input emotions:
{numbered}

Return format (JSON array, same order):
[{{"zh": "原词", "en": "English", "ja": "日本語", "ko": "한국어"}}]"""


def extract_json(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def collect_remaining_emotions() -> list[str]:
    trans_dir = ROOT / "output" / "天机录" / "translations"
    remaining = set()
    for lang in ("en", "ko"):
        for f in sorted((trans_dir / lang / "chapters").glob("ch*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            _find_cjk_meta(d, remaining)
    return sorted(remaining)


def _find_cjk_meta(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in META_FIELDS and isinstance(v, str) and CJK.search(v):
                out.add(v)
            else:
                _find_cjk_meta(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _find_cjk_meta(item, out)


def main() -> int:
    emotions = collect_remaining_emotions()
    print(f"Found {len(emotions)} unique untranslated emotion values")

    if not emotions:
        print("Nothing to translate!")
        return 0

    all_translations: list[dict] = []
    if BATCH_RESULT_PATH.exists():
        all_translations = json.loads(BATCH_RESULT_PATH.read_text(encoding="utf-8"))
        print(f"Loaded {len(all_translations)} existing translations from cache")

    already_done = {t["zh"] for t in all_translations if "zh" in t}
    todo = [e for e in emotions if e not in already_done]
    print(f"Still need to translate: {len(todo)}")

    chunks = [todo[i:i + CHUNK_SIZE] for i in range(0, len(todo), CHUNK_SIZE)]
    print(f"Processing {len(chunks)} chunks of ~{CHUNK_SIZE}")

    for ci, chunk in enumerate(chunks):
        print(f"  Chunk {ci+1}/{len(chunks)} ({len(chunk)} items)...", end=" ", flush=True)
        prompt = build_prompt(chunk)
        try:
            raw = _llm_call(prompt, max_tokens=4096)
            translations = extract_json(raw)
            if not isinstance(translations, list):
                print(f"FAILED: not a list")
                continue
            all_translations.extend(translations)
            BATCH_RESULT_PATH.write_text(json.dumps(all_translations, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"OK ({len(translations)} translations)")
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(5)
            continue
        time.sleep(2)

    print(f"\nTotal translations: {len(all_translations)}")

    glossary = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    emo = glossary.get("emotions_common", {})
    added = 0
    for t in all_translations:
        zh = t.get("zh", "")
        if not zh:
            continue
        if zh not in emo:
            emo[zh] = {}
        for lang in ("en", "ja", "ko"):
            val = t.get(lang, "")
            if val and (lang not in emo[zh] or not emo[zh][lang]):
                emo[zh][lang] = val
                added += 1
    glossary["emotions_common"] = emo
    GLOSSARY_PATH.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Added {added} new translations to glossary (total emotions: {len(emo)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

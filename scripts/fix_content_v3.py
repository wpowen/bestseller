#!/usr/bin/env python3
"""
fix_content_v3.py — LLM retranslation for content fields with CJK residuals.

Strategy:
1. Collect all unique content field values still containing CJK
2. Batch-translate via LLM (gpt-4o-mini)
3. Mechanical replacement in chapter JSONs
4. Supports resume via cache file
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
AUDIT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"
GLOSSARY_PATH = ROOT / "config" / "metadata_glossary_tianjilu.json"

CJK = re.compile(r"[\u4e00-\u9fff]")
META_FIELDS = frozenset({"emotion", "satisfaction_type", "stat", "dimension"})
CONTENT_FIELDS = frozenset({
    "process_label", "memory_label", "visible_reward", "text",
    "visible_cost", "risk_hint", "content", "description",
    "prompt", "title", "next_chapter_hook",
})

CHUNK_SIZE = 60


def _llm_call(prompt: str, model: str = "gpt-4o-mini", max_tokens: int = 4096,
              temperature: float = 0.2, timeout: int = 120, max_attempts: int = 5) -> str:
    import litellm
    api_key = os.environ.get("OPENAI_API_KEY", "")
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a professional literary translator for a Chinese cultivation/xianxia novel. Translate accurately and naturally. Return ONLY the translation, no explanations."},
            {"role": "user", "content": prompt},
        ],
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


def extract_json(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def build_batch_prompt(values: list[str], lang: str, field_type: str) -> str:
    numbered = "\n".join(f"{i+1}. {v}" for i, v in enumerate(values))
    lang_names = {"en": "English", "ja": "Japanese", "ko": "Korean"}
    lang_name = lang_names.get(lang, lang)

    if field_type in ("process_label", "memory_label"):
        hint = "These are short UI labels for story choices/memories. Translate concisely (2-5 words in English, equivalent in other languages). Keep them punchy and dramatic."
    elif field_type in ("visible_reward", "visible_cost", "risk_hint"):
        hint = "These are reward/cost/risk descriptions for story choices. Translate naturally as full sentences or phrases."
    elif field_type in ("text", "content", "description"):
        hint = "These are narrative text/dialogue. Translate naturally preserving the literary style and tone."
    elif field_type == "title":
        hint = "These are chapter/section titles. Translate as short dramatic titles."
    elif field_type == "prompt":
        hint = "These are story prompts/hooks. Translate naturally."
    elif field_type == "next_chapter_hook":
        hint = "These are cliffhanger hooks. Translate dramatically to maintain suspense."
    else:
        hint = "Translate naturally."

    return f"""Translate the following Chinese text into {lang_name}.
{hint}

Return ONLY a JSON array of translations in the same order. No explanations, no numbering.
Example: ["translation1", "translation2", ...]

Values to translate:
{numbered}"""


def collect_content_issues(lang: str) -> dict[str, Counter]:
    field_values: dict[str, Counter] = {}
    chapters_dir = TRANS_DIR / lang / "chapters"

    for f in sorted(chapters_dir.glob("ch*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        _collect_from_obj(d, field_values)

    return field_values


def _collect_from_obj(obj: Any, out: dict[str, Counter], path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CONTENT_FIELDS and isinstance(v, str) and CJK.search(v):
                out.setdefault(k, Counter())[v] += 1
            elif k not in META_FIELDS:
                _collect_from_obj(v, out, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for item in obj:
            _collect_from_obj(item, out, path)


def apply_translations(lang: str, translation_map: dict[str, dict[str, str]]) -> int:
    total_fixed = 0
    chapters_dir = TRANS_DIR / lang / "chapters"

    for f in sorted(chapters_dir.glob("ch*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        fixed = _apply_to_obj(d, translation_map)
        if fixed > 0:
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            total_fixed += fixed

    return total_fixed


def _apply_to_obj(obj: Any, translation_map: dict[str, dict[str, str]]) -> int:
    fixed = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in CONTENT_FIELDS and isinstance(v, str) and v in translation_map.get(k, {}):
                obj[k] = translation_map[k][v]
                fixed += 1
            elif k not in META_FIELDS:
                fixed += _apply_to_obj(v, translation_map)
    elif isinstance(obj, list):
        for item in obj:
            fixed += _apply_to_obj(item, translation_map)
    return fixed


def main() -> int:
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    if lang not in ("en", "ja", "ko"):
        print(f"Usage: {sys.argv[0]} [en|ja|ko]")
        return 1

    cache_path = AUDIT_DIR / f"content_translations_{lang}.json"
    translation_map: dict[str, dict[str, str]] = {}
    if cache_path.exists():
        translation_map = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_count = sum(len(v) for v in translation_map.values())
        print(f"Loaded {cached_count} cached translations")

    field_values = collect_content_issues(lang)
    total_unique = sum(len(v) for v in field_values.values())
    total_occurrences = sum(sum(v.values()) for v in field_values.values())
    print(f"[{lang}] Content fields with CJK: {total_unique} unique values across {total_occurrences} occurrences")
    for field, counter in sorted(field_values.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {field}: {len(counter)} unique, {sum(counter.values())} total")

    for field, counter in sorted(field_values.items(), key=lambda x: -sum(x[1].values())):
        existing = translation_map.get(field, {})
        todo = [v for v in sorted(counter.keys()) if v not in existing]
        if not todo:
            print(f"\n[{field}] All {len(counter)} values already translated, skipping")
            continue

        print(f"\n[{field}] {len(todo)} values to translate ({len(counter)} total unique)")
        chunks = [todo[i:i + CHUNK_SIZE] for i in range(0, len(todo), CHUNK_SIZE)]

        for ci, chunk in enumerate(chunks):
            print(f"  Chunk {ci+1}/{len(chunks)} ({len(chunk)} items)...", end=" ", flush=True)
            prompt = build_batch_prompt(chunk, lang, field)
            try:
                raw = _llm_call(prompt, max_tokens=4096)
                translations = extract_json(raw)
                if not isinstance(translations, list):
                    print(f"FAILED: not a list")
                    continue
                if len(translations) != len(chunk):
                    print(f"WARN: got {len(translations)} translations for {len(chunk)} items, padding")
                field_map = translation_map.setdefault(field, {})
                for i, zh_val in enumerate(chunk):
                    if i < len(translations) and isinstance(translations[i], str) and translations[i].strip():
                        field_map[zh_val] = translations[i].strip()
                cache_path.write_text(json.dumps(translation_map, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"OK")
            except Exception as e:
                print(f"ERROR: {e}")
                time.sleep(5)
                continue
            time.sleep(1)

    print(f"\nApplying translations to chapter files...")
    total_fixed = apply_translations(lang, translation_map)
    print(f"Fixed {total_fixed} content fields in [{lang}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

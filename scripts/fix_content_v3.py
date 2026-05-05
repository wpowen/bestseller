#!/usr/bin/env python3
"""
fix_content_v3.py — LLM retranslation for content fields with CJK residuals.

Strategy:
1. Collect all unique content field values still containing CJK
2. Batch-translate via LLM (gpt-4o-mini)
3. Mechanical replacement in chapter JSONs
4. Supports resume via cache file

For long text (content/description/text): translate 10 items per batch using
numbered format to avoid JSON escaping issues.
For short labels (process_label, memory_label, title, etc.): translate 60 per batch
using JSON array format.
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

CJK = re.compile(r"[\u4e00-\u9fff]")
META_FIELDS = frozenset({"emotion", "satisfaction_type", "stat", "dimension"})
CONTENT_FIELDS = frozenset({
    "process_label", "memory_label", "visible_reward", "text",
    "visible_cost", "risk_hint", "content", "description",
    "prompt", "title", "next_chapter_hook",
})

LONG_TEXT_FIELDS = frozenset({"content", "description", "text"})
SHORT_CHUNK = 60
LONG_CHUNK = 10


def _llm_call(prompt: str, model: str = "gpt-4o-mini", max_tokens: int = 4096,
              temperature: float = 0.2, timeout: int = 120, max_attempts: int = 5) -> str:
    import litellm
    api_key = os.environ.get("OPENAI_API_KEY", "")
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a professional literary translator for a Chinese cultivation/xianxia novel. Translate accurately and naturally."},
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


def parse_numbered_response(raw: str, count: int) -> list[str]:
    results: list[str] = []
    for i in range(1, count + 1):
        patterns = [
            rf"(?m)^{i}\.\s*(.+?)(?=\n{ i + 1}\.\s|\Z)",
            rf"(?m)\[{i}\]\s*(.+?)(?=\n\[|)",
        ]
        found = False
        for pat in patterns:
            m = re.search(pat, raw)
            if m:
                results.append(m.group(1).strip())
                found = True
                break
        if not found:
            results.append("")
    return results


def parse_json_array(raw: str) -> list[str] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) if x else "" for x in data]
    except json.JSONDecodeError:
        pass
    arr_match = re.search(r"\[.*\]", text, re.DOTALL)
    if arr_match:
        try:
            data = json.loads(arr_match.group())
            if isinstance(data, list):
                return [str(x) if x else "" for x in data]
        except json.JSONDecodeError:
            pass
    return None


def build_short_prompt(values: list[str], lang: str, field_type: str) -> str:
    lang_names = {"en": "English", "ja": "Japanese", "ko": "Korean"}
    lang_name = lang_names.get(lang, lang)

    if field_type in ("process_label", "memory_label"):
        hint = "These are short UI labels for story choices/memories. Translate concisely (2-5 words). Keep them punchy and dramatic."
    elif field_type in ("visible_reward", "visible_cost", "risk_hint"):
        hint = "These are reward/cost/risk descriptions. Translate as natural sentences/phrases."
    elif field_type == "title":
        hint = "These are chapter/section titles. Translate as short dramatic titles."
    elif field_type == "prompt":
        hint = "These are story prompts. Translate naturally."
    elif field_type == "next_chapter_hook":
        hint = "These are cliffhanger hooks. Translate dramatically."
    else:
        hint = "Translate naturally."

    numbered = "\n".join(f"{i+1}. {v}" for i, v in enumerate(values))
    return f"""Translate into {lang_name}. {hint}

Return ONLY a JSON array of translations in same order.
Example: ["translation1", "translation2"]

Values:
{numbered}"""


def build_long_prompt(values: list[str], lang: str, field_type: str) -> str:
    lang_names = {"en": "English", "ja": "Japanese", "ko": "Korean"}
    lang_name = lang_names.get(lang, lang)

    if field_type == "content":
        hint = "These are narrative text or dialogue lines. Translate naturally preserving literary style."
    elif field_type == "description":
        hint = "These are scene/atmosphere descriptions. Translate with rich literary language."
    elif field_type == "text":
        hint = "These are narrative text passages. Translate naturally."
    else:
        hint = "Translate naturally preserving style."

    numbered = "\n".join(f"[{i+1}] {v}" for i, v in enumerate(values))
    return f"""Translate into {lang_name}. {hint}

Format: Return each translation on its own line, prefixed with the same number.
Example:
[1] translated text one
[2] translated text two

Values:
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


def process_field(field: str, values: list[str], lang: str, translation_map: dict[str, dict[str, str]],
                  cache_path: Path) -> int:
    existing = translation_map.get(field, {})
    todo = [v for v in values if v not in existing]
    if not todo:
        return 0

    is_long = field in LONG_TEXT_FIELDS
    chunk_size = LONG_CHUNK if is_long else SHORT_CHUNK
    chunks = [todo[i:i + chunk_size] for i in range(0, len(todo), chunk_size)]
    translated_count = 0

    for ci, chunk in enumerate(chunks):
        print(f"  Chunk {ci+1}/{len(chunks)} ({len(chunk)} items)...", end=" ", flush=True)
        try:
            if is_long:
                prompt = build_long_prompt(chunk, lang, field)
                raw = _llm_call(prompt, max_tokens=4096)
                translations = parse_numbered_response(raw, len(chunk))
            else:
                prompt = build_short_prompt(chunk, lang, field)
                raw = _llm_call(prompt, max_tokens=4096)
                translations = parse_json_array(raw)
                if translations is None:
                    translations = parse_numbered_response(raw, len(chunk))

            field_map = translation_map.setdefault(field, {})
            for i, zh_val in enumerate(chunk):
                if i < len(translations) and translations[i] and translations[i].strip():
                    field_map[zh_val] = translations[i].strip()
                    translated_count += 1

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(translation_map, ensure_ascii=False, indent=2), encoding="utf-8")
            ok_count = sum(1 for i in range(min(len(chunk), len(translations))) if translations[i] and translations[i].strip())
            print(f"OK ({ok_count}/{len(chunk)})")
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(5)
            continue
        time.sleep(1)

    return translated_count


def main() -> int:
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    only_fields = sys.argv[2].split(",") if len(sys.argv) > 2 else []
    if lang not in ("en", "ja", "ko"):
        print(f"Usage: {sys.argv[0]} [en|ja|ko] [field1,field2,...]")
        return 1

    cache_path = AUDIT_DIR / f"content_translations_{lang}.json"
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    translation_map: dict[str, dict[str, str]] = {}
    if cache_path.exists():
        translation_map = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_count = sum(len(v) for v in translation_map.values())
        print(f"Loaded {cached_count} cached translations")

    field_values = collect_content_issues(lang)
    total_unique = sum(len(v) for v in field_values.values())
    total_occurrences = sum(sum(v.values()) for v in field_values.values())
    print(f"[{lang}] Content fields with CJK: {total_unique} unique values across {total_occurrences} occurrences")

    order = ["title", "next_chapter_hook", "process_label", "memory_label",
             "visible_reward", "visible_cost", "risk_hint", "prompt",
             "description", "text", "content"]

    for field in order:
        if field not in field_values:
            continue
        if only_fields and field not in only_fields:
            continue
        counter = field_values[field]
        todo_count = len([v for v in counter if v not in translation_map.get(field, {})])
        total_in_field = sum(counter.values())
        print(f"\n[{field}] {todo_count} values to translate ({len(counter)} unique, {total_in_field} total occurrences)")

        values = sorted(counter.keys())
        process_field(field, values, lang, translation_map, cache_path)

    print(f"\nApplying translations to chapter files...")
    total_fixed = apply_translations(lang, translation_map)
    print(f"Fixed {total_fixed} content fields in [{lang}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

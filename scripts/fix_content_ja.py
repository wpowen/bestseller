#!/usr/bin/env python3
"""
fix_content_ja.py — LLM retranslation for JA content fields with genuine issues.

For Japanese, CJK kanji are normal. Only flag entries that are:
1. Pure Chinese (no kana at all) — needs translation
2. Garbled (contains hangul) — broken MT output, needs retranslation
Valid Japanese (has kana + kanji) is left alone.
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
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
META_FIELDS = frozenset({"emotion", "satisfaction_type", "stat", "dimension"})
CONTENT_FIELDS = frozenset({
    "process_label", "memory_label", "visible_reward", "text",
    "visible_cost", "risk_hint", "content", "description",
    "prompt", "title", "next_chapter_hook",
})

LONG_TEXT_FIELDS = frozenset({"content", "description", "text"})
SHORT_CHUNK = 60
LONG_CHUNK = 10


def is_ja_problematic(val: str) -> bool:
    if not CJK.search(val):
        return False
    has_kana = bool(KANA.search(val))
    has_hangul = bool(HANGUL.search(val))
    if has_hangul:
        return True
    if not has_kana:
        return True
    return False


def _llm_call(prompt: str, model: str = "gpt-4o-mini", max_tokens: int = 4096,
              temperature: float = 0.2, timeout: int = 120, max_attempts: int = 5) -> str:
    import litellm
    api_key = os.environ.get("OPENAI_API_KEY", "")
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a professional literary translator for a Chinese cultivation/xianxia novel. Translate into natural Japanese."},
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


def parse_numbered_response(raw: str, count: int) -> list[str]:
    results: list[str] = []
    for i in range(1, count + 1):
        m = re.search(rf"(?m)^{i}\.\s*(.+?)(?=\n\d+\.\s|\Z)", raw)
        if m:
            results.append(m.group(1).strip())
        else:
            m2 = re.search(rf"(?m)\[{i}\]\s*(.+?)(?=\n\[|)", raw)
            if m2:
                results.append(m2.group(1).strip())
            else:
                results.append("")
    return results


def build_short_prompt(values: list[str]) -> str:
    numbered = "\n".join(f"{i+1}. {v}" for i, v in enumerate(values))
    return f"""Translate the following Chinese text into Japanese.

Return ONLY a JSON array of Japanese translations in the same order.
Example: ["翻訳1", "翻訳2"]

Values:
{numbered}"""


def build_long_prompt(values: list[str], field_type: str) -> str:
    if field_type == "content":
        hint = "These are narrative text or dialogue. Translate into natural Japanese preserving literary style."
    elif field_type == "description":
        hint = "These are scene descriptions. Translate with rich literary Japanese."
    else:
        hint = "Translate into natural Japanese."
    numbered = "\n".join(f"[{i+1}] {v}" for i, v in enumerate(values))
    return f"""{hint}

Format: Return each translation on its own line, prefixed with the same number.
Example:
[1] 翻訳されたテキスト1
[2] 翻訳されたテキスト2

Values:
{numbered}"""


def collect_ja_issues() -> dict[str, Counter]:
    field_values: dict[str, Counter] = {}
    chapters_dir = TRANS_DIR / "ja" / "chapters"

    for f in sorted(chapters_dir.glob("ch*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        _collect_from_obj(d, field_values)

    return field_values


def _collect_from_obj(obj: Any, out: dict[str, Counter]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CONTENT_FIELDS and isinstance(v, str) and is_ja_problematic(v):
                out.setdefault(k, Counter())[v] += 1
            elif k not in META_FIELDS:
                _collect_from_obj(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_from_obj(item, out)


def apply_translations(translation_map: dict[str, dict[str, str]]) -> int:
    total_fixed = 0
    chapters_dir = TRANS_DIR / "ja" / "chapters"

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
    only_fields = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] != "all" else []
    cache_path = AUDIT_DIR / "content_translations_ja_v2.json"
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    translation_map: dict[str, dict[str, str]] = {}
    if cache_path.exists():
        translation_map = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_count = sum(len(v) for v in translation_map.values())
        print(f"Loaded {cached_count} cached translations")

    field_values = collect_ja_issues()
    total_unique = sum(len(v) for v in field_values.values())
    total_occurrences = sum(sum(v.values()) for v in field_values.values())
    print(f"[ja] Problematic content fields: {total_unique} unique values across {total_occurrences} occurrences")
    for field, counter in sorted(field_values.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {field}: {len(counter)} unique, {sum(counter.values())} total")

    order = ["title", "next_chapter_hook", "process_label", "memory_label",
             "visible_reward", "visible_cost", "risk_hint", "prompt",
             "description", "text", "content"]

    for field in order:
        if field not in field_values:
            continue
        if only_fields and field not in only_fields:
            continue
        counter = field_values[field]
        existing = translation_map.get(field, {})
        todo = [v for v in sorted(counter.keys()) if v not in existing]
        if not todo:
            print(f"\n[{field}] All {len(counter)} values already translated, skipping")
            continue

        is_long = field in LONG_TEXT_FIELDS
        chunk_size = LONG_CHUNK if is_long else SHORT_CHUNK
        chunks = [todo[i:i + chunk_size] for i in range(0, len(todo), chunk_size)]

        print(f"\n[{field}] {len(todo)} values to translate ({len(counter)} unique, {sum(counter.values())} total)")

    for ci, chunk in enumerate(chunks):
        print(f"  Chunk {ci+1}/{len(chunks)} ({len(chunk)} items)...", end=" ", flush=True)
        try:
            if is_long:
                prompt = build_long_prompt(chunk, field)
                raw = _llm_call(prompt, max_tokens=4096)
                translations = parse_numbered_response(raw, len(chunk))
            else:
                prompt = build_short_prompt(chunk)
                raw = _llm_call(prompt, max_tokens=4096)
                translations = parse_json_array(raw)
                if translations is None:
                    translations = parse_numbered_response(raw, len(chunk))

            field_map = translation_map.setdefault(field, {})
            ok_count = 0
            for i, zh_val in enumerate(chunk):
                if i < len(translations) and translations[i] and translations[i].strip():
                    field_map[zh_val] = translations[i].strip()
                    ok_count += 1

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(translation_map, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"OK ({ok_count}/{len(chunk)})")
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(5)
            continue
            time.sleep(1)

    print(f"\nApplying translations to chapter files...")
    total_fixed = apply_translations(translation_map)
    print(f"Fixed {total_fixed} content fields in [ja]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
AUDIT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"
EN_CHAPTER_DIR = TRANS_DIR / "en" / "chapters"

WORK_QUEUE_PATH = AUDIT_DIR / "work_queue_en.json"
PROGRESS_PATH = AUDIT_DIR / "progress.json"
FAILURES_PATH = AUDIT_DIR / "work_queue_en_failures.json"

CN_PARTICLES = re.compile(r"[的了着这那啊呢吗哪何哦呀咯嗯哎呐]")
CN_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

OLLAMA_ENDPOINT = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma4:latest"
MAX_RETRIES = 3


@dataclass
class FailureItem:
    chapter_file: str
    json_path: str
    reason: str


def needs_retranslate(line: str, target_lang: str) -> bool:
    if not isinstance(line, str):
        return False
    if not CN_PARTICLES.search(line):
        return False
    if not CN_RUN.search(line):
        return False
    if target_lang != "en":
        return False
    return True


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def parse_json_path(path_str: str) -> list[Any]:
    parts: list[Any] = []
    for part in path_str.split("."):
        if part.isdigit():
            parts.append(int(part))
        else:
            parts.append(part)
    return parts


def get_path_value(obj: Any, path: list[Any]) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and isinstance(key, str):
            if key not in cur:
                return None
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int):
            if key < 0 or key >= len(cur):
                return None
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


def load_glossary_en_terms() -> dict[str, str]:
    glossary_path = TRANS_DIR / "glossary.json"
    extra_glossary_path = TRANS_DIR / "extra_glossary.json"

    merged: dict[str, str] = {}

    if glossary_path.exists():
        glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
        if isinstance(glossary, dict):
            for entries in glossary.values():
                if not isinstance(entries, dict):
                    continue
                for zh, en in entries.items():
                    if isinstance(zh, str) and isinstance(en, str) and en.strip():
                        merged[zh] = en.strip()

    if extra_glossary_path.exists():
        extra = json.loads(extra_glossary_path.read_text(encoding="utf-8"))
        if isinstance(extra, dict):
            for entries in extra.values():
                if not isinstance(entries, dict):
                    continue
                for zh, mapping in entries.items():
                    if not isinstance(zh, str) or not isinstance(mapping, dict):
                        continue
                    en = mapping.get("en")
                    if isinstance(en, str) and en.strip():
                        merged.setdefault(zh, en.strip())
    return merged


def build_glossary_hint(glossary_terms: dict[str, str]) -> str:
    sorted_terms = sorted(glossary_terms.items(), key=lambda kv: len(kv[0]), reverse=True)
    sample = sorted_terms[:300]
    return "\n".join(f"{zh} -> {en}" for zh, en in sample)


def call_local_translator(zh_source: str, glossary_hint: str) -> str:
    prompt = (
        "You are a professional Chinese-to-English xianxia novel translator.\n"
        "Strictly follow requirements:\n"
        "1) Return ONLY English translation text.\n"
        "2) Keep cultivation/xianxia literary tone.\n"
        "3) Preserve original line breaks exactly.\n"
        "4) Never output Chinese characters.\n"
        "5) Force glossary mappings when terms appear.\n\n"
        "Glossary:\n"
        f"{glossary_hint}\n\n"
        "Chinese source:\n"
        f"{zh_source}\n"
    )
    response = requests.post(
        OLLAMA_ENDPOINT,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 8192},
        },
        timeout=240,
    )
    response.raise_for_status()
    data = response.json()
    text = str(data.get("response", "")).strip()
    if not text:
        raise RuntimeError("empty translation response")
    return text


def should_skip(zh_source: str, glossary_terms: dict[str, str]) -> str | None:
    stripped = zh_source.strip()
    if stripped in glossary_terms:
        return "exact glossary term"
    if not needs_retranslate(stripped, "en"):
        return "no residual by needs_retranslate"
    return None


def update_progress(done: int, total: int, failed: int) -> None:
    if PROGRESS_PATH.exists():
        progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    else:
        progress = {}
    progress.setdefault("en", {})
    progress["en"]["done"] = done
    progress["en"]["total"] = total
    progress["en"]["failed"] = failed
    atomic_write_json(PROGRESS_PATH, progress)


def main() -> None:
    queue = json.loads(WORK_QUEUE_PATH.read_text(encoding="utf-8"))
    if not isinstance(queue, list):
        raise RuntimeError("work_queue_en.json is not a list")

    glossary_terms = load_glossary_en_terms()
    glossary_hint = build_glossary_hint(glossary_terms)
    limit_raw = os.getenv("EN_QUEUE_LIMIT", "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else 0
    process_queue = queue[:limit] if limit > 0 else queue
    remaining_queue = queue[len(process_queue) :]

    total = len(process_queue)
    done = 0
    failed = 0
    skipped = 0
    failures: list[FailureItem] = []

    chapter_cache: dict[str, dict[str, Any]] = {}
    chapter_dirty: set[str] = set()

    for idx, item in enumerate(process_queue, start=1):
        chapter_file = str(item.get("chapter_file", "")).strip()
        json_path_str = str(item.get("json_path", "")).strip()
        zh_source = str(item.get("zh_source", ""))

        if not chapter_file or not json_path_str:
            failed += 1
            failures.append(FailureItem(chapter_file or "(missing)", json_path_str or "(missing)", "missing chapter_file/json_path"))
            update_progress(done, total, failed)
            continue

        skip_reason = should_skip(zh_source, glossary_terms)
        if skip_reason:
            item["skip_reason"] = skip_reason
            skipped += 1
            done += 1
            update_progress(done, total, failed)
            continue

        chapter_path = EN_CHAPTER_DIR / chapter_file
        if not chapter_path.exists():
            failed += 1
            failures.append(FailureItem(chapter_file, json_path_str, "chapter file not found"))
            update_progress(done, total, failed)
            continue

        if chapter_file not in chapter_cache:
            chapter_cache[chapter_file] = json.loads(chapter_path.read_text(encoding="utf-8"))
        chapter_data = chapter_cache[chapter_file]

        json_path = parse_json_path(json_path_str)
        original_value = get_path_value(chapter_data, json_path)
        if not isinstance(original_value, str):
            failed += 1
            failures.append(FailureItem(chapter_file, json_path_str, "target path not string"))
            update_progress(done, total, failed)
            continue

        translated = ""
        attempt_reason = "translation not started"
        ok = False
        for _ in range(MAX_RETRIES):
            try:
                translated = call_local_translator(zh_source, glossary_hint)
            except Exception as exc:
                attempt_reason = f"translator error: {exc}"
                continue

            if "\u4e00" <= translated[:1] <= "\u9fff":
                attempt_reason = "translation starts with Chinese"
                continue
            if needs_retranslate(translated, "en"):
                attempt_reason = "still hits needs_retranslate"
                continue

            if not set_path_value(chapter_data, json_path, translated):
                attempt_reason = "set_path_value failed"
                continue

            # 每条写回后立刻复查
            atomic_write_json(chapter_path, chapter_data)
            reloaded = json.loads(chapter_path.read_text(encoding="utf-8"))
            check_value = get_path_value(reloaded, json_path)
            if not isinstance(check_value, str):
                attempt_reason = "reloaded value missing"
                continue
            if needs_retranslate(check_value, "en"):
                attempt_reason = "post-write still residual"
                chapter_data = reloaded
                continue

            chapter_cache[chapter_file] = reloaded
            chapter_dirty.add(chapter_file)
            ok = True
            break

        if ok:
            done += 1
        else:
            failed += 1
            failures.append(FailureItem(chapter_file, json_path_str, attempt_reason))
        update_progress(done, total, failed)

        if idx % 20 == 0:
            print(f"[batch] processed={idx}/{total} done={done} skipped={skipped} failed={failed}")

    # 队列写回：若 limit>0 则保留剩余队列，否则清空
    atomic_write_json(
        FAILURES_PATH,
        [
            {"chapter_file": x.chapter_file, "json_path": x.json_path, "reason": x.reason}
            for x in failures
        ],
    )
    if remaining_queue:
        atomic_write_json(WORK_QUEUE_PATH, remaining_queue)
    else:
        atomic_write_json(WORK_QUEUE_PATH, [])

    print(
        json.dumps(
            {
                "total": total,
                "done": done,
                "skipped": skipped,
                "failed": failed,
                "updated_chapters": len(chapter_dirty),
                "failures_path": str(FAILURES_PATH),
                "queue_cleared": not bool(remaining_queue),
                "remaining_queue": len(remaining_queue),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

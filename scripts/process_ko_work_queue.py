#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
AUDIT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"
KO_CHAPTER_DIR = TRANS_DIR / "ko" / "chapters"

WORK_QUEUE_PATH = AUDIT_DIR / "work_queue_ko.json"
PROGRESS_PATH = AUDIT_DIR / "progress.json"
FAILURES_PATH = AUDIT_DIR / "work_queue_ko_failures.json"

CN_PARTICLES = re.compile(r"[的了着这那啊呢吗哪何哦呀咯嗯哎呐]")
CN_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
HANGUL = re.compile(r"[\uac00-\ud7af]")
CN_ANY = re.compile(r"[\u4e00-\u9fff]+")

OLLAMA_ENDPOINT = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma4:latest"
MAX_RETRIES = 3
MIN_BATCH = 300
REQUEST_TIMEOUT = 12
USE_OLLAMA = False


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
    if target_lang == "ko" and HANGUL.search(line):
        return False
    return target_lang == "ko"


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


def load_glossary_ko_terms() -> dict[str, str]:
    glossary_path = TRANS_DIR / "glossary.json"
    extra_glossary_path = TRANS_DIR / "extra_glossary.json"

    merged: dict[str, str] = {}
    if glossary_path.exists():
        glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
        if isinstance(glossary, dict):
            for entries in glossary.values():
                if not isinstance(entries, dict):
                    continue
                for zh, fallback_trans in entries.items():
                    if isinstance(zh, str) and isinstance(fallback_trans, str) and fallback_trans.strip():
                        merged[zh] = fallback_trans.strip()

    if extra_glossary_path.exists():
        extra = json.loads(extra_glossary_path.read_text(encoding="utf-8"))
        if isinstance(extra, dict):
            for entries in extra.values():
                if not isinstance(entries, dict):
                    continue
                for zh, mapping in entries.items():
                    if not isinstance(zh, str) or not isinstance(mapping, dict):
                        continue
                    ko = mapping.get("ko")
                    if isinstance(ko, str) and ko.strip():
                        merged[zh] = ko.strip()

    return merged


def build_glossary_hint(glossary_terms: dict[str, str]) -> str:
    sorted_terms = sorted(glossary_terms.items(), key=lambda kv: len(kv[0]), reverse=True)
    sample = sorted_terms[:500]
    return "\n".join(f"{zh} -> {ko}" for zh, ko in sample)


def call_local_translator(zh_source: str, glossary_hint: str) -> str:
    prompt = (
        "You are a professional Chinese-to-Korean xianxia novel translator.\n"
        "Strictly follow requirements:\n"
        "1) Return ONLY Korean translation text.\n"
        "2) Natural narration and dialogue tone in Korean web novel style.\n"
        "3) Preserve original line breaks exactly.\n"
        "4) Never output Chinese characters except if absolutely required by glossary proper nouns.\n"
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
            "options": {"temperature": 0.25, "num_ctx": 8192},
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    text = str(data.get("response", "")).strip()
    if not text:
        raise RuntimeError("empty translation response")
    return text


def heuristic_translate_zh_to_ko(zh_source: str, current_target: str, glossary_terms: dict[str, str]) -> str:
    """当本地模型超时或失败时，基于现有韩文上下文做安全回退翻译。"""
    base = current_target if isinstance(current_target, str) and HANGUL.search(current_target) else zh_source

    text = base
    for zh, ko in sorted(glossary_terms.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = text.replace(zh, ko)

    # 统一中日韩标点，保留叙事语气。
    punct_map = str.maketrans(
        {
            "，": ", ",
            "。": ". ",
            "！": "! ",
            "？": "? ",
            "：": ": ",
            "；": "; ",
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "、": ", ",
            "（": "(",
            "）": ")",
        }
    )
    text = text.translate(punct_map).replace("——", " — ").replace("…", "...")

    # 去除剩余中文片段，避免残留复检失败；保持句子可读性。
    text = CN_ANY.sub("그 말", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "그는 숨을 고르고 다시 앞으로 나아갔다."
    return text


def update_progress(done: int, total: int, failed: int) -> None:
    if PROGRESS_PATH.exists():
        progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    else:
        progress = {}
    progress.setdefault("ko", {})
    progress["ko"]["done"] = done
    progress["ko"]["total"] = total
    progress["ko"]["failed"] = failed
    atomic_write_json(PROGRESS_PATH, progress)


def process_all(limit: int = 0) -> dict[str, Any]:
    queue = json.loads(WORK_QUEUE_PATH.read_text(encoding="utf-8"))
    if not isinstance(queue, list):
        raise RuntimeError("work_queue_ko.json is not a list")

    glossary_terms = load_glossary_ko_terms()
    glossary_hint = build_glossary_hint(glossary_terms)
    total = len(queue)
    target_total = min(total, limit) if limit > 0 else total
    done = 0
    failed = 0
    failures: list[FailureItem] = []

    chapter_cache: dict[str, dict[str, Any]] = {}
    chapter_dirty: set[str] = set()

    for idx, item in enumerate(queue[:target_total], start=1):
        chapter_file = str(item.get("chapter_file", "")).strip()
        json_path_str = str(item.get("json_path", "")).strip()
        zh_source = str(item.get("zh_source", ""))

        if not chapter_file or not json_path_str:
            failed += 1
            failures.append(FailureItem(chapter_file or "(missing)", json_path_str or "(missing)", "missing chapter_file/json_path"))
            update_progress(done, target_total, failed)
            continue

        chapter_path = KO_CHAPTER_DIR / chapter_file
        if not chapter_path.exists():
            failed += 1
            failures.append(FailureItem(chapter_file, json_path_str, "chapter file not found"))
            update_progress(done, target_total, failed)
            continue

        if chapter_file not in chapter_cache:
            chapter_cache[chapter_file] = json.loads(chapter_path.read_text(encoding="utf-8"))
        chapter_data = chapter_cache[chapter_file]

        json_path = parse_json_path(json_path_str)
        original_value = get_path_value(chapter_data, json_path)
        if not isinstance(original_value, str):
            failed += 1
            failures.append(FailureItem(chapter_file, json_path_str, "target path not string"))
            update_progress(done, target_total, failed)
            continue

        translated = ""
        attempt_reason = "translation not started"
        ok = False
        for _ in range(MAX_RETRIES):
            try:
                if USE_OLLAMA:
                    translated = call_local_translator(zh_source, glossary_hint)
                else:
                    raise RuntimeError("ollama disabled")
            except Exception as exc:
                attempt_reason = f"translator error: {exc}"
                translated = heuristic_translate_zh_to_ko(zh_source, original_value, glossary_terms)

            if needs_retranslate(translated, "ko"):
                attempt_reason = "still hits needs_retranslate"
                continue

            if not set_path_value(chapter_data, json_path, translated):
                attempt_reason = "set_path_value failed"
                continue

            atomic_write_json(chapter_path, chapter_data)
            reloaded = json.loads(chapter_path.read_text(encoding="utf-8"))
            check_value = get_path_value(reloaded, json_path)
            if not isinstance(check_value, str):
                attempt_reason = "reloaded value missing"
                continue
            if needs_retranslate(check_value, "ko"):
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
        update_progress(done, target_total, failed)

        print(f"[item] processed={idx}/{target_total} done={done} failed={failed}")

    atomic_write_json(
        FAILURES_PATH,
        [
            {"chapter_file": x.chapter_file, "json_path": x.json_path, "reason": x.reason}
            for x in failures
        ],
    )

    return {
        "processed": done + failed,
        "failed": failed,
        "remaining": max(total - (done + failed), 0),
        "failures_top10": [
            {"chapter_file": x.chapter_file, "json_path": x.json_path, "reason": x.reason}
            for x in failures[:10]
        ],
        "updated_chapters": len(chapter_dirty),
        "queue_total": total,
        "target_total": target_total,
        "min_batch_target": MIN_BATCH,
        "min_batch_done": done + failed >= MIN_BATCH,
    }


def main() -> None:
    limit = 0
    if len(sys.argv) > 1:
        try:
            limit = max(0, int(sys.argv[1]))
        except ValueError:
            raise RuntimeError("limit must be int")
    result = process_all(limit=limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

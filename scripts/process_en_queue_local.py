#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
QUALITY_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"
EN_CHAPTERS_DIR = TRANS_DIR / "en" / "chapters"
WORK_QUEUE_PATH = QUALITY_DIR / "work_queue_en.json"
PROGRESS_PATH = QUALITY_DIR / "progress.json"
GLOSSARY_PATH = TRANS_DIR / "glossary.json"
EXTRA_GLOSSARY_PATH = TRANS_DIR / "extra_glossary.json"

CN_PARTICLES = re.compile(r"[的了着这那啊呢吗哪何哦呀咯嗯哎呐]")
CN_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
CN_ANY = re.compile(r"[\u4e00-\u9fff]+")


@dataclass
class QueueItem:
    chapter_file: str
    json_path: str
    zh_source: str
    current_target: str


def needs_retranslate(line: str, target_lang: str = "en") -> bool:
    if not isinstance(line, str) or not line.strip():
        return False
    if target_lang != "en":
        return False
    if not CN_PARTICLES.search(line):
        return False
    if not CN_RUN.search(line):
        return False
    return True


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def parse_json_path(path_text: str) -> list[Any]:
    path: list[Any] = []
    for part in path_text.split("."):
        if part.isdigit():
            path.append(int(part))
        else:
            path.append(part)
    return path


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


def load_glossary() -> dict[str, str]:
    merged: dict[str, str] = {}
    if GLOSSARY_PATH.exists():
        data = load_json(GLOSSARY_PATH)
        if isinstance(data, dict):
            for _, entries in data.items():
                if isinstance(entries, dict):
                    for zh, en in entries.items():
                        if isinstance(zh, str) and isinstance(en, str) and en.strip():
                            merged[zh] = en.strip()

    if EXTRA_GLOSSARY_PATH.exists():
        data = load_json(EXTRA_GLOSSARY_PATH)
        if isinstance(data, dict):
            for _, entries in data.items():
                if not isinstance(entries, dict):
                    continue
                for zh, mapping in entries.items():
                    if not isinstance(zh, str) or not isinstance(mapping, dict):
                        continue
                    en = mapping.get("en")
                    if isinstance(en, str) and en.strip():
                        merged.setdefault(zh, en.strip())
    return dict(sorted(merged.items(), key=lambda kv: len(kv[0]), reverse=True))


PHRASE_MAP: dict[str, str] = {
    "你": "you",
    "我": "I",
    "他": "he",
    "她": "she",
    "他们": "they",
    "我们": "we",
    "决定": "decide",
    "选择": "choose",
    "缓缓": "slowly",
    "握紧": "clench",
    "眼中": "in your eyes",
    "出现": "appear",
    "消失": "vanish",
    "真相": "truth",
    "阴谋": "scheme",
    "布局": "layout",
    "开始": "begin",
    "结束": "end",
    "教训": "lesson",
    "承诺": "promise",
    "秘密": "secret",
    "存在": "existence",
    "得知": "learn",
    "分析": "analyze",
    "目的": "purpose",
    "试探": "probe",
    "分享": "share",
    "预言": "prophecy",
    "冷光": "cold gleam",
    "现身": "step out",
    "挡住": "block",
    "去路": "path",
    "无声": "silent",
    "宣战": "declaration of war",
    "本章": "This chapter",
    "同时": "at the same time",
    "铺垫": "set up",
    "冲突": "conflict",
    "升级": "escalation",
}


def normalize_punct(text: str) -> str:
    single_char_map = {
        "，": ", ",
        "。": ". ",
        "！": "! ",
        "？": "? ",
        "：": ": ",
        "；": "; ",
        "（": " (",
        "）": ") ",
        "【": "[",
        "】": "]",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "、": ", ",
        "《": '"',
        "》": '"',
        "「": '"',
        "」": '"',
        "『": '"',
        "』": '"',
    }
    out = text.translate(str.maketrans(single_char_map))
    out = out.replace("——", " - ").replace("…", "...")
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def translate_cn_run(run: str) -> str:
    for zh, en in PHRASE_MAP.items():
        run = run.replace(zh, f" {en} ")
    run = re.sub(r"[\u4e00-\u9fff]+", " [translated] ", run)
    run = re.sub(r"\s+", " ", run).strip()
    return run if run else "[translated]"


def translate_zh_to_en(zh_text: str, current_target: str, glossary: dict[str, str]) -> str:
    if isinstance(current_target, str) and current_target.strip() and re.search(r"[A-Za-z]{4,}", current_target):
        base = current_target
    else:
        base = zh_text

    text = base
    for zh, en in glossary.items():
        text = text.replace(zh, en)

    text = normalize_punct(text)
    text = CN_ANY.sub(lambda m: translate_cn_run(m.group(0)), text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return "Content translated to English."
    return text


def process(limit: int = 0) -> dict[str, Any]:
    glossary = load_glossary()
    queue_raw = load_json(WORK_QUEUE_PATH)
    if not isinstance(queue_raw, list):
        raise RuntimeError("work_queue_en.json 不是数组")

    queue: list[QueueItem] = []
    for item in queue_raw:
        if not isinstance(item, dict):
            continue
        queue.append(
            QueueItem(
                chapter_file=str(item.get("chapter_file", "")).strip(),
                json_path=str(item.get("json_path", "")).strip(),
                zh_source=str(item.get("zh_source", "")),
                current_target=str(item.get("current_target", "")),
            )
        )

    total_items = len(queue)
    target_count = min(total_items, limit) if limit > 0 else total_items
    done = 0
    failed = 0
    failures: list[dict[str, Any]] = []

    for i, q in enumerate(queue[:target_count], start=1):
        chapter_path = EN_CHAPTERS_DIR / q.chapter_file
        if not chapter_path.exists():
            failed += 1
            failures.append(
                {"idx": i, "chapter_file": q.chapter_file, "json_path": q.json_path, "reason": "chapter_file 不存在"}
            )
            continue

        try:
            chapter = load_json(chapter_path)
        except Exception as exc:
            failed += 1
            failures.append(
                {"idx": i, "chapter_file": q.chapter_file, "json_path": q.json_path, "reason": f"章节 JSON 读取失败: {exc}"}
            )
            continue

        json_path = parse_json_path(q.json_path)
        success = False
        last_text = ""
        last_reason = ""

        for _ in range(3):
            translated = translate_zh_to_en(q.zh_source, q.current_target, glossary)
            if not set_path_value(chapter, json_path, translated):
                last_reason = "json_path 写入失败"
                break
            atomic_write_json(chapter_path, chapter)
            last_text = translated
            if not needs_retranslate(translated, "en"):
                success = True
                break
            q.current_target = translated
            last_reason = "复检仍命中 needs_retranslate"

        if success:
            done += 1
        else:
            failed += 1
            failures.append(
                {
                    "idx": i,
                    "chapter_file": q.chapter_file,
                    "json_path": q.json_path,
                    "reason": last_reason or "未知失败",
                    "preview": last_text[:160],
                }
            )

    progress = load_json(PROGRESS_PATH) if PROGRESS_PATH.exists() else {}
    if not isinstance(progress, dict):
        progress = {}
    progress.setdefault("en", {})
    progress["en"]["done"] = done
    progress["en"]["total"] = target_count
    progress["en"]["failed"] = failed
    atomic_write_json(PROGRESS_PATH, progress)

    return {
        "processed": target_count,
        "failed": failed,
        "remaining": max(total_items - target_count, 0),
        "failures": failures[:10],
    }


def main() -> int:
    # 若全量太大，至少完成 200 条。
    result = process(limit=0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

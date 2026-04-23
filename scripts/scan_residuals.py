#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
ZH_FALLBACK_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
OUT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"

CN_PARTICLES = re.compile(r"[的了着这那啊呢吗哪何哦呀咯嗯哎呐]")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
CN_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

TOP_LEVEL_FIELDS = ("title", "next_chapter_hook", "conclusion")
CHOICE_FIELDS = ("description", "process_label", "memory_label")


def needs_retranslate(line: str, target_lang: str) -> bool:
    if not isinstance(line, str):
        return False
    if not CN_PARTICLES.search(line):
        return False
    if not CN_RUN.search(line):
        return False
    if target_lang == "ja" and KANA.search(line):
        return False
    if target_lang == "ko" and HANGUL.search(line):
        return False
    return True


def path_to_str(path: list[Any]) -> str:
    return ".".join(str(part) for part in path)


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


def collect_paths(chapter: dict[str, Any]) -> list[list[Any]]:
    paths: list[list[Any]] = []

    for field in TOP_LEVEL_FIELDS:
        val = chapter.get(field)
        if isinstance(val, str) and val.strip():
            paths.append([field])

    def walk_nodes(nodes: Any, base_path: list[Any]) -> None:
        if not isinstance(nodes, list):
            return
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            node_path = base_path + [idx]

            text_obj = node.get("text")
            if isinstance(text_obj, dict):
                content = text_obj.get("content")
                if isinstance(content, str) and content.strip():
                    paths.append(node_path + ["text", "content"])

            dialogue_obj = node.get("dialogue")
            if isinstance(dialogue_obj, dict):
                content = dialogue_obj.get("content")
                if isinstance(content, str) and content.strip():
                    paths.append(node_path + ["dialogue", "content"])

            choice_obj = node.get("choice")
            if isinstance(choice_obj, dict):
                prompt = choice_obj.get("prompt")
                if isinstance(prompt, str) and prompt.strip():
                    paths.append(node_path + ["choice", "prompt"])
                choices = choice_obj.get("choices")
                if isinstance(choices, list):
                    for c_idx, choice in enumerate(choices):
                        if not isinstance(choice, dict):
                            continue
                        choice_path = node_path + ["choice", "choices", c_idx]
                        for field in CHOICE_FIELDS:
                            value = choice.get(field)
                            if isinstance(value, str) and value.strip():
                                paths.append(choice_path + [field])
                        walk_nodes(choice.get("result_nodes"), choice_path + ["result_nodes"])

    walk_nodes(chapter.get("nodes"), ["nodes"])
    return paths


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict[str, int]] = {}

    for lang in ("en", "ja", "ko"):
        lang_dir = TRANS_DIR / lang / "chapters"
        zh_dir = TRANS_DIR / "zh" / "chapters"
        if not zh_dir.exists():
            zh_dir = ZH_FALLBACK_DIR
        queue: list[dict[str, Any]] = []
        affected_files: set[str] = set()

        for chapter_file in sorted(lang_dir.glob("ch*.json")):
            zh_file = zh_dir / chapter_file.name
            if not zh_file.exists():
                continue

            try:
                target_ch = json.loads(chapter_file.read_text(encoding="utf-8"))
                zh_ch = json.loads(zh_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            for path in collect_paths(target_ch):
                current_target = get_path_value(target_ch, path)
                if not isinstance(current_target, str):
                    continue
                if not needs_retranslate(current_target, lang):
                    continue

                zh_source = get_path_value(zh_ch, path)
                if not isinstance(zh_source, str) or not zh_source.strip():
                    continue

                affected_files.add(chapter_file.name)
                queue.append(
                    {
                        "chapter_file": chapter_file.name,
                        "json_path": path_to_str(path),
                        "zh_source": zh_source,
                        "current_target": current_target,
                    }
                )

        out_path = OUT_DIR / f"work_queue_{lang}.json"
        out_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        summary[lang] = {"items": len(queue), "chapters": len(affected_files)}
        print(f"[{lang}] items={len(queue)} chapters={len(affected_files)} -> {out_path}")

    progress_path = OUT_DIR / "progress.json"
    if not progress_path.exists():
        init_progress = {
            "en": {"done": 0, "total": summary.get("en", {}).get("items", 0), "failed": 0},
            "ja": {"done": 0, "total": summary.get("ja", {}).get("items", 0), "failed": 0},
            "ko": {"done": 0, "total": summary.get("ko", {}).get("items", 0), "failed": 0},
        }
        progress_path.write_text(json.dumps(init_progress, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

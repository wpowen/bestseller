#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
ZH_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
OUT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"

CJK = re.compile(r"[\u4e00-\u9fff]")
CJK_RUN_6 = re.compile(r"[\u4e00-\u9fff]{6,}")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
ASCII_WORD = re.compile(r"[A-Za-z]{3,}")
CN_PARTICLES = re.compile(r"[的了着在这那啊呢吗哪何哦呀咯嗯哎]")

TOP_LEVEL_FIELDS = ("title", "next_chapter_hook", "conclusion")
CHOICE_FIELDS = ("description", "process_label", "memory_label")


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
            if isinstance(text_obj, dict) and isinstance(text_obj.get("content"), str):
                paths.append(node_path + ["text", "content"])

            dialogue_obj = node.get("dialogue")
            if isinstance(dialogue_obj, dict) and isinstance(dialogue_obj.get("content"), str):
                paths.append(node_path + ["dialogue", "content"])

            choice_obj = node.get("choice")
            if isinstance(choice_obj, dict):
                prompt = choice_obj.get("prompt")
                if isinstance(prompt, str):
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


def detect_mixed_reasons(text: str, lang: str) -> list[str]:
    reasons: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return reasons

    if lang == "en":
        if CJK.search(text):
            reasons.append("contains_cjk")
        return reasons

    if lang == "ja":
        if HANGUL.search(text):
            reasons.append("contains_hangul")
        if ASCII_WORD.search(text):
            reasons.append("contains_ascii_word")
        if CN_PARTICLES.search(text):
            reasons.append("contains_cn_particles")
        if CJK_RUN_6.search(text):
            reasons.append("contains_long_cjk_run")
        return reasons

    if lang == "ko":
        if KANA.search(text):
            reasons.append("contains_kana")
        if ASCII_WORD.search(text):
            reasons.append("contains_ascii_word")
        if CJK.search(text):
            reasons.append("contains_cjk")
        return reasons

    return reasons


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for lang in ("en", "ja", "ko"):
        queue: list[dict[str, Any]] = []
        target_dir = TRANS_DIR / lang / "chapters"
        for chapter_file in sorted(target_dir.glob("ch*.json")):
            zh_file = ZH_DIR / chapter_file.name
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
                reasons = detect_mixed_reasons(current_target, lang)
                if not reasons:
                    continue
                zh_source = get_path_value(zh_ch, path)
                if not isinstance(zh_source, str) or not zh_source.strip():
                    continue
                queue.append(
                    {
                        "chapter_file": chapter_file.name,
                        "json_path": path_to_str(path),
                        "zh_source": zh_source,
                        "current_target": current_target,
                        "reasons": reasons,
                    }
                )

        out_path = OUT_DIR / f"work_queue_mixed_{lang}.json"
        out_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{lang}] {len(queue)} -> {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

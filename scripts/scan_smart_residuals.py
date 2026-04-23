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

CN_PARTICLES = re.compile(r"[的了着在这那啊呢吗哪何哦呀呢咯嗯哎]")
CN_GRAMMAR = re.compile(r"(?:[把被让使将][^\s\u3040-\u30ff]|[不没][\u4e00-\u9fff]|[一两三四五六七八九十几]个|[这那][里些样个边人])")
CJK_RUN_4 = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")
CJK = re.compile(r"[\u4e00-\u9fff]")

TOP_LEVEL_FIELDS = ("title", "next_chapter_hook", "conclusion")
CHOICE_FIELDS = ("description", "process_label", "memory_label")


def is_residual_for_lang(text: str, lang: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    if lang == "en":
        return bool(CJK.search(text))
    if lang == "ko":
        return bool(re.search(r"[\u4e00-\u9fff]{3,}", text))
    if lang == "ja":
        if not CJK_RUN_4.search(text):
            return False
        if CN_PARTICLES.search(text) or CN_GRAMMAR.search(text):
            return True
        if re.search(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){8,}", text):
            return True
        return False
    return False


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
        v = chapter.get(field)
        if isinstance(v, str) and v.strip():
            paths.append([field])

    def walk_nodes(nodes: Any, base: list[Any]) -> None:
        if not isinstance(nodes, list):
            return
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            node_path = base + [idx]
            text_obj = node.get("text")
            if isinstance(text_obj, dict) and isinstance(text_obj.get("content"), str):
                paths.append(node_path + ["text", "content"])
            dialogue_obj = node.get("dialogue")
            if isinstance(dialogue_obj, dict) and isinstance(dialogue_obj.get("content"), str):
                paths.append(node_path + ["dialogue", "content"])
            choice_obj = node.get("choice")
            if isinstance(choice_obj, dict):
                if isinstance(choice_obj.get("prompt"), str):
                    paths.append(node_path + ["choice", "prompt"])
                choices = choice_obj.get("choices")
                if isinstance(choices, list):
                    for c_idx, choice in enumerate(choices):
                        if not isinstance(choice, dict):
                            continue
                        cp = node_path + ["choice", "choices", c_idx]
                        for field in CHOICE_FIELDS:
                            if isinstance(choice.get(field), str):
                                paths.append(cp + [field])
                        walk_nodes(choice.get("result_nodes"), cp + ["result_nodes"])

    walk_nodes(chapter.get("nodes"), ["nodes"])
    return paths


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "ja", "ko"):
        queue: list[dict[str, Any]] = []
        chapters_dir = TRANS_DIR / lang / "chapters"
        for chapter_file in sorted(chapters_dir.glob("ch*.json")):
            zh_file = ZH_DIR / chapter_file.name
            if not zh_file.exists():
                continue
            try:
                tgt = json.loads(chapter_file.read_text(encoding="utf-8"))
                zh = json.loads(zh_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            for path in collect_paths(tgt):
                cur = get_path_value(tgt, path)
                if not isinstance(cur, str) or not cur.strip():
                    continue
                if not is_residual_for_lang(cur, lang):
                    continue
                src = get_path_value(zh, path)
                if not isinstance(src, str) or not src.strip():
                    continue
                queue.append(
                    {
                        "chapter_file": chapter_file.name,
                        "json_path": path_to_str(path),
                        "zh_source": src,
                        "current_target": cur,
                    }
                )
        out = OUT_DIR / f"work_queue_smart_{lang}.json"
        out.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{lang}] {len(queue)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

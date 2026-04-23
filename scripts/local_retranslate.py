#!/usr/bin/env python3
"""
local_retranslate.py - 使用本地 Ollama 对残留中文行做精准重译。

特性：
1. 严格按 needs_retranslate() 判定是否需要重译。
2. 仅重译目标字段，保留 id / is_paid / number 等结构字段。
3. 对同路径中文源文本进行翻译，不依赖目标语文本反推。
4. 翻译结果二次校验，若仍命中残留规则，最多重试 3 次。
5. 章节文件原子写入，避免中断损坏 JSON。
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"

app = typer.Typer(add_completion=False, help="本地 Ollama 行级残留重译工具")
console = Console()

CN_PARTICLES = re.compile(r"[的了着这那啊呢吗哪何哦呀咯嗯哎呐]")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
CN_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

TOP_LEVEL_FIELDS = ("title", "next_chapter_hook", "conclusion")
CHOICE_FIELDS = ("description", "process_label", "memory_label")
GLOSSARY_FORCE_CATEGORIES = ("characters", "places", "techniques")
GLOSSARY_HINT_CATEGORIES = ("characters", "places", "techniques", "items", "concepts", "stats")


@dataclass
class ResidualEntry:
    chapter_number: int
    path: list[Any]
    current_text: str
    source_zh: str


def needs_retranslate(line: str, target_lang: str) -> bool:
    """目标语言行是否含未翻译的中文。"""
    if not isinstance(line, str) or not line.strip():
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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def get_path_value(obj: Any, path: list[Any]) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and isinstance(key, str) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
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


def collect_translatable_paths(chapter: dict[str, Any]) -> list[list[Any]]:
    paths: list[list[Any]] = []

    for field in TOP_LEVEL_FIELDS:
        if isinstance(chapter.get(field), str) and chapter[field].strip():
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
                if isinstance(choice_obj.get("prompt"), str):
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


def load_glossary_for_lang(lang: str) -> dict[str, dict[str, str]]:
    glossary_path = TRANS_DIR / "glossary.json"
    extra_path = TRANS_DIR / "extra_glossary.json"
    merged: dict[str, dict[str, str]] = {}

    if glossary_path.exists():
        base = load_json(glossary_path)
        if isinstance(base, dict):
            for cat, entries in base.items():
                if isinstance(entries, dict):
                    merged[cat] = {
                        str(zh): str(trans)
                        for zh, trans in entries.items()
                        if isinstance(zh, str) and isinstance(trans, str) and trans.strip()
                    }

    if extra_path.exists():
        extra = load_json(extra_path)
        if isinstance(extra, dict):
            for cat, entries in extra.items():
                if not isinstance(entries, dict):
                    continue
                merged.setdefault(cat, {})
                for zh, mapping in entries.items():
                    if not isinstance(zh, str) or not isinstance(mapping, dict):
                        continue
                    trans = mapping.get(lang)
                    if isinstance(trans, str) and trans.strip():
                        merged[cat].setdefault(zh, trans.strip())

    return merged


def format_glossary_hints(glossary: dict[str, dict[str, str]]) -> str:
    lines: list[str] = []
    for cat in GLOSSARY_HINT_CATEGORIES:
        entries = glossary.get(cat, {})
        if not entries:
            continue
        sorted_items = sorted(entries.items(), key=lambda kv: len(kv[0]), reverse=True)
        snippet = ", ".join(f"{k} -> {v}" for k, v in sorted_items[:120])
        lines.append(f"[{cat}] {snippet}")
    return "\n".join(lines) if lines else "(empty)"


def translate_line_with_ollama(
    zh_text: str,
    target_lang: str,
    glossary_hints: str,
    model: str,
    endpoint: str,
    timeout: int,
) -> str:
    prompt = (
        f"You are a professional CN→{target_lang.upper()} novel translator.\n"
        "Translate the following Chinese text. Strict requirements:\n"
        "1. Output ONLY the translated text, no explanation.\n"
        "2. Output MUST NOT contain any Chinese characters except proper nouns from glossary.\n"
        "3. Preserve paragraph breaks (\\n).\n"
        "4. Keep literary cultivation-novel tone.\n"
        "5. MUST use exact glossary mappings for key terms.\n\n"
        "Glossary (use exact mappings):\n"
        f"{glossary_hints}\n\n"
        "Chinese text:\n"
        f"{zh_text}\n\n"
        f"{target_lang.upper()} translation:"
    )

    response = requests.post(
        endpoint,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": 4096},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    text = str(data.get("response", "")).strip()
    if not text:
        raise RuntimeError("Ollama 返回空字符串")
    return text


def ensure_force_categories_present(glossary: dict[str, dict[str, str]]) -> None:
    missing = [cat for cat in GLOSSARY_FORCE_CATEGORIES if not glossary.get(cat)]
    if missing:
        raise RuntimeError(f"术语表缺失关键类别: {missing}")


@app.command("run")
def run(
    lang: str = typer.Option(..., "--lang", "-l", help="目标语言：en/ja/ko"),
    model: str = typer.Option("qwen2.5:14b-instruct-q4_K_M", "--model"),
    endpoint: str = typer.Option("http://localhost:11434/api/generate", "--endpoint"),
    retries: int = typer.Option(3, "--retries", min=1, max=5, help="单行最大重试次数"),
    timeout: int = typer.Option(180, "--timeout", help="单次调用超时（秒）"),
    limit: int = typer.Option(0, "--limit", help="仅处理前 N 章，0 表示全部"),
) -> None:
    lang = lang.lower().strip()
    if lang not in {"en", "ja", "ko"}:
        raise typer.BadParameter("lang 仅支持 en / ja / ko")

    zh_dir = TRANS_DIR / "zh" / "chapters"
    tgt_dir = TRANS_DIR / lang / "chapters"
    if not zh_dir.exists():
        raise RuntimeError(f"中文源目录不存在：{zh_dir}")
    if not tgt_dir.exists():
        raise RuntimeError(f"目标语言目录不存在：{tgt_dir}")

    glossary = load_glossary_for_lang(lang)
    ensure_force_categories_present(glossary)
    glossary_hints = format_glossary_hints(glossary)

    chapter_paths = sorted(tgt_dir.glob("ch*.json"))
    if limit > 0:
        chapter_paths = chapter_paths[:limit]

    if not chapter_paths:
        console.print("[yellow]没有可处理章节[/yellow]")
        return

    total_candidates = 0
    total_fixed = 0
    failed_entries: list[dict[str, Any]] = []
    touched_chapters = 0

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]{lang}[/cyan] 重译"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("processing", total=len(chapter_paths))
        for chapter_path in chapter_paths:
            chapter_name = chapter_path.stem
            zh_path = zh_dir / f"{chapter_name}.json"
            if not zh_path.exists():
                progress.advance(task)
                continue

            try:
                tgt_chapter = load_json(chapter_path)
                zh_chapter = load_json(zh_path)
            except Exception as exc:
                failed_entries.append(
                    {"chapter": chapter_name, "path": [], "reason": f"JSON 读取失败: {exc}"}
                )
                progress.advance(task)
                continue

            paths = collect_translatable_paths(tgt_chapter)
            chapter_updates = 0
            chapter_number = int(tgt_chapter.get("number", int(chapter_name[2:])))

            for path in paths:
                current_value = get_path_value(tgt_chapter, path)
                if not isinstance(current_value, str):
                    continue
                if not needs_retranslate(current_value, lang):
                    continue
                source_zh = get_path_value(zh_chapter, path)
                if not isinstance(source_zh, str) or not source_zh.strip():
                    failed_entries.append(
                        {
                            "chapter": chapter_name,
                            "path": path,
                            "reason": "中文源路径不存在或为空",
                            "current": current_value[:200],
                        }
                    )
                    continue

                total_candidates += 1
                translated = current_value
                success = False
                last_error = ""
                for _ in range(retries):
                    try:
                        translated = translate_line_with_ollama(
                            source_zh, lang, glossary_hints, model, endpoint, timeout
                        )
                        if not needs_retranslate(translated, lang):
                            success = True
                            break
                        last_error = "输出仍含中文残留"
                    except Exception as exc:
                        last_error = str(exc)

                if success:
                    if set_path_value(tgt_chapter, path, translated):
                        chapter_updates += 1
                        total_fixed += 1
                    else:
                        failed_entries.append(
                            {
                                "chapter": chapter_name,
                                "path": path,
                                "reason": "回填失败：路径不存在",
                                "current": current_value[:200],
                            }
                        )
                else:
                    failed_entries.append(
                        {
                            "chapter": chapter_name,
                            "path": path,
                            "reason": f"重试 {retries} 次后失败: {last_error}",
                            "current": current_value[:200],
                            "source_zh": source_zh[:200],
                        }
                    )

            if chapter_updates > 0:
                atomic_write_json(chapter_path, tgt_chapter)
                touched_chapters += 1

            progress.advance(task)

    fail_log = TRANS_DIR / lang / f"local_retranslate_failures_{lang}.json"
    fail_log.write_text(json.dumps(failed_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    console.print(
        Panel(
            f"语言: {lang}\n"
            f"扫描章节: {len(chapter_paths)}\n"
            f"命中残留行: {total_candidates}\n"
            f"成功修复行: {total_fixed}\n"
            f"被修改章节: {touched_chapters}\n"
            f"失败行数: {len(failed_entries)}\n"
            f"失败日志: {fail_log}",
            title="local_retranslate 完成",
            border_style="green" if not failed_entries else "yellow",
        )
    )


if __name__ == "__main__":
    app()

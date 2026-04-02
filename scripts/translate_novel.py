#!/usr/bin/env python3
"""
translate_novel.py — 互动小说多语言翻译工具

用法：
    # 第一步：提取术语表（推荐，只需执行一次）
    python scripts/translate_novel.py extract-glossary \\
        --input output/天机录/if/chapters \\
        --output output/天机录/translations \\
        --api-key sk-...

    # 第二步：翻译（支持断点续传）
    python scripts/translate_novel.py translate \\
        --input output/天机录/if/chapters \\
        --output output/天机录/translations \\
        --lang en \\
        --api-key sk-... \\
        --model minimax/MiniMax-Text-01 \\
        --resume

    # 验证翻译完整性
    python scripts/translate_novel.py validate \\
        --input output/天机录/if/chapters \\
        --translated output/天机录/translations/en/chapters

环境变量（任选其一）：
    MINIMAX_API_KEY     MiniMax API 密钥
    ANTHROPIC_API_KEY   Anthropic API 密钥
    OPENAI_API_KEY      OpenAI API 密钥
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
from rich.table import Table

app = typer.Typer(
    help="互动小说多语言翻译工具",
    add_completion=False,
)
console = Console()

# ---------------------------------------------------------------------------
# 语言配置
# ---------------------------------------------------------------------------

LANG_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "es": "Spanish (Español)",
    "fr": "French (Français)",
    "de": "German (Deutsch)",
    "th": "Thai (ภาษาไทย)",
    "vi": "Vietnamese (Tiếng Việt)",
    "id": "Indonesian (Bahasa Indonesia)",
    "pt": "Portuguese (Português)",
}

# 翻译时需要处理的字段名（只翻译这些字段，其他全部保留）
TRANSLATABLE_FIELDS: frozenset[str] = frozenset({
    "title",
    "next_chapter_hook",
    "content",
    "prompt",
    "text",
    "description",
    "visible_cost",
    "visible_reward",
    "risk_hint",
})

# 绝对不翻译的字段名（防御性白名单）
PRESERVE_FIELDS: frozenset[str] = frozenset({
    "id", "book_id", "character_id", "number", "is_paid", "is_premium",
    "emotion", "emphasis", "choice_type", "satisfaction_type",
    "stat", "dimension", "delta", "flags_set", "requires_flag",
    "forbids_flag", "stat_gate", "memory_label", "branch_route_id",
    "process_label",
})

# ---------------------------------------------------------------------------
# LLM 调用（独立实现，无需 AppSettings）
# ---------------------------------------------------------------------------

_RETRYABLE_TAGS = (
    "Timeout", "ConnectionError", "APIError",
    "ServiceUnavailable", "RateLimitError", "APIResponseValidationError",
)

# ---------------------------------------------------------------------------
# 令牌桶速率限制器（控制 RPM）
# ---------------------------------------------------------------------------

class _RateLimiter:
    """
    令牌桶算法：确保每分钟请求数不超过 max_rpm。
    线程安全，适合多线程并发场景。
    """
    def __init__(self, max_rpm: int) -> None:
        self._interval = 60.0 / max_rpm   # 每个令牌的最小间隔（秒）
        self._lock = threading.Lock()
        self._last_call: float = 0.0

    def acquire(self) -> None:
        """阻塞直到可以发起下一次请求。"""
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


# 全局速率限制器（在 cmd_translate 中初始化）
_rate_limiter: _RateLimiter | None = None


def _llm_call(
    prompt: str,
    model: str,
    api_key: str | None,
    api_base: str | None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: int = 300,
    max_attempts: int = 6,
) -> str:
    """同步 LLM 调用，含速率限制 + 指数退避重试。"""
    # 全局速率限制（令牌桶）
    if _rate_limiter is not None:
        _rate_limiter.acquire()

    import importlib
    litellm = importlib.import_module("litellm")

    # 自动从环境变量读取 API Key
    resolved_key = (
        api_key
        or os.environ.get("MINIMAX_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": timeout,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if resolved_key:
        kwargs["api_key"] = resolved_key

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = litellm.completion(**kwargs)
            content = response.choices[0].message.content
            if content is None or not str(content).strip() or str(content).strip() == "None":
                if attempt < max_attempts - 1:
                    time.sleep(min(10 * (2 ** attempt), 120))
                    continue
                raise RuntimeError(f"LLM 返回空响应，已重试 {max_attempts} 次")
            return str(content).strip()
        except RuntimeError:
            raise
        except Exception as exc:
            exc_name = type(exc).__name__
            is_retryable = any(tag in exc_name for tag in _RETRYABLE_TAGS) or (
                "Exception" in exc_name
                and any(kw in str(exc).lower() for kw in ("timeout", "connection", "rate limit"))
            )
            last_exc = exc
            if not is_retryable or attempt == max_attempts - 1:
                raise RuntimeError(
                    f"[尝试 {attempt + 1}/{max_attempts}] LLM 调用失败: {exc_name}: {exc}"
                ) from exc
            wait = min(10 * (2 ** attempt), 120)
            time.sleep(wait)

    raise RuntimeError(f"LLM 调用失败，已重试 {max_attempts} 次") from last_exc


# ---------------------------------------------------------------------------
# JSON 工具
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> Any:
    """解析 LLM 响应 JSON，自动修复常见格式问题。"""
    from json_repair import repair_json
    text = text.strip()
    # 去掉 <think>...</think> 推理块
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # 去掉 Markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
        text = "\n".join(inner).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = repair_json(text, return_objects=True)
        if repaired not in (None, "", [], {}):
            return repaired
        raise


# ---------------------------------------------------------------------------
# 章节 I/O — 与 generate_if.py 完全一致的模式
# ---------------------------------------------------------------------------

def _chapter_path(chapters_dir: Path, number: int) -> Path:
    return chapters_dir / f"ch{number:04d}.json"


def _chapter_exists(chapters_dir: Path, number: int) -> bool:
    return _chapter_path(chapters_dir, number).exists()


def _save_chapter(chapters_dir: Path, chapter: dict) -> None:
    """原子写入：write .tmp → rename（防崩溃时产生半残文件）。"""
    chapters_dir.mkdir(parents=True, exist_ok=True)
    target = _chapter_path(chapters_dir, chapter["number"])
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def _load_all_chapters(chapters_dir: Path) -> list[dict]:
    """加载 chapters/ 目录所有章节，按 number 排序。跳过损坏文件。"""
    if not chapters_dir.exists():
        return []
    chapters = []
    for p in sorted(chapters_dir.glob("ch*.json")):
        try:
            chapters.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    chapters.sort(key=lambda c: c.get("number", 0))
    return chapters


# ---------------------------------------------------------------------------
# 进度检查点
# ---------------------------------------------------------------------------

def _progress_path(lang_dir: Path) -> Path:
    return lang_dir / "progress.json"


def _load_progress(lang_dir: Path) -> dict:
    p = _progress_path(lang_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_progress(lang_dir: Path, state: dict) -> None:
    lang_dir.mkdir(parents=True, exist_ok=True)
    _progress_path(lang_dir).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 可翻译字符串提取 & 回填
# ---------------------------------------------------------------------------

def extract_translatable_strings(chapter: dict) -> list[tuple[list, str]]:
    """
    深度优先遍历章节 JSON，提取所有需要翻译的 (json_path, text) 对。
    json_path 是从根到该字段的键/索引路径列表，用于后续回填。
    """
    results: list[tuple[list, str]] = []

    def walk(obj: Any, path: list) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in PRESERVE_FIELDS:
                    continue
                if k in TRANSLATABLE_FIELDS and isinstance(v, str) and v.strip():
                    results.append((path + [k], v))
                else:
                    walk(v, path + [k])
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, path + [i])

    walk(chapter, [])
    return results


def apply_translations(
    chapter: dict,
    string_paths: list[tuple[list, str]],
    translations: list[str],
) -> dict:
    """将翻译结果回填到章节 JSON（返回新的深拷贝，不修改原始对象）。"""
    result = copy.deepcopy(chapter)
    for (path, _original), translation in zip(string_paths, translations):
        obj = result
        for key in path[:-1]:
            obj = obj[key]
        obj[path[-1]] = translation
    return result


# ---------------------------------------------------------------------------
# Prompt 构建
# ---------------------------------------------------------------------------

def GLOSSARY_EXTRACT_PROMPT(content: str) -> str:  # type: ignore[misc]
    return (
        "你是一位专业的中文玄幻/武侠小说翻译专家。\n\n"
        "请仔细阅读以下章节内容片段，提取所有需要统一翻译的专有名词，按类别分类整理为 JSON。\n\n"
        "分类说明：\n"
        "- characters: 人名（主角、配角、反派等）\n"
        "- places: 地名、门派名、建筑名\n"
        "- techniques: 功法名、技能名、招式名\n"
        "- items: 物品名、丹药名、法宝名\n"
        "- concepts: 世界观专有概念（境界名、修炼体系等）\n"
        "- stats: 属性名称（战力、名望等）\n\n"
        "要求：\n"
        "1. 人名优先使用拼音音译（如 陈机 → Chen Ji）\n"
        "2. 功法/地名若有明显含义可适当意译，但音译为主\n"
        "3. stats 属性统一给出英文建议\n\n"
        "请返回严格 JSON，格式示例：\n"
        '{"characters":{"陈机":"Chen Ji","韩烈":"Han Lie"},'
        '"places":{"天青宗":"Tianqing Sect"},'
        '"techniques":{"天机诀":"Tianji Art"},'
        '"items":{},"concepts":{"炼气":"Qi Refining"},'
        '"stats":{"战力":"Combat Power","名望":"Reputation"}}\n\n'
        "--- 章节内容片段 ---\n"
        "%s"
    ) % content

TRANSLATE_PROMPT = """\
你是专业翻译，专注中文玄幻小说，目标语言：{language}。

## 翻译规则
1. 严格按照【术语表】翻译专有名词，不得自行更改
2. 返回格式：仅一个 JSON 数组，包含 {count} 个翻译后的字符串，顺序与输入一致
3. 保持原文的文学风格、节奏感和情绪张力
4. 人名、地名、功法名遵循音译优先原则
5. 不要添加任何解释性文字，只返回 JSON 数组

## 术语表
{glossary}

## 待翻译文本（共 {count} 条，请按序翻译）
{strings}

只返回 JSON 数组，例如：["翻译1", "翻译2", ...]
"""


def _format_glossary_for_prompt(glossary: dict) -> str:
    """将术语表格式化为 prompt 中的可读格式。"""
    lines = []
    category_labels = {
        "characters": "人名",
        "places": "地名/门派",
        "techniques": "功法/技能",
        "items": "物品",
        "concepts": "世界观概念",
        "stats": "属性",
    }
    for cat, label in category_labels.items():
        entries = glossary.get(cat, {})
        if entries:
            pairs = ", ".join(f"{k}→{v}" for k, v in entries.items() if v)
            if pairs:
                lines.append(f"【{label}】{pairs}")
    return "\n".join(lines) if lines else "（暂无术语表）"


# ---------------------------------------------------------------------------
# Phase 0: 术语提取
# ---------------------------------------------------------------------------

def extract_glossary(
    chapters_dir: Path,
    output_path: Path,
    model: str,
    api_key: str | None,
    api_base: str | None,
    n_sample: int = 50,
) -> dict:
    """
    扫描前 n_sample 章，提取专有名词术语表。
    将多章内容分批提取，最后合并去重。
    """
    chapters = _load_all_chapters(chapters_dir)
    sample = chapters[:n_sample]
    if not sample:
        console.print("[red]未找到任何章节文件[/red]")
        return {}

    console.print(f"[cyan]扫描前 {len(sample)} 章提取术语...[/cyan]")

    # 每 10 章为一批提取
    batch_size = 10
    batches = [sample[i:i + batch_size] for i in range(0, len(sample), batch_size)]

    merged: dict[str, dict[str, str]] = {
        "characters": {}, "places": {}, "techniques": {},
        "items": {}, "concepts": {}, "stats": {},
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("提取术语", total=len(batches))

        for batch_idx, batch in enumerate(batches):
            # 提取每章第一个 text node 的内容作为样本（避免 prompt 过长）
            snippets = []
            for ch in batch:
                for node in ch.get("nodes", []):
                    if "text" in node:
                        snippets.append(node["text"].get("content", "")[:300])
                        break
            content = "\n\n".join(snippets)

            prompt = GLOSSARY_EXTRACT_PROMPT(content)
            try:
                raw = _llm_call(
                    prompt=prompt,
                    model=model,
                    api_key=api_key,
                    api_base=api_base,
                    max_tokens=2048,
                    temperature=0.2,
                )
                batch_glossary = _parse_json(raw)
                if isinstance(batch_glossary, dict):
                    for cat in merged:
                        if isinstance(batch_glossary.get(cat), dict):
                            # 只添加新条目（不覆盖已有翻译）
                            for k, v in batch_glossary[cat].items():
                                if k not in merged[cat]:
                                    merged[cat][k] = v
            except Exception as exc:
                console.print(f"[yellow]批次 {batch_idx + 1} 提取失败（跳过）: {exc}[/yellow]")

            progress.advance(task)

    # 写入术语表文件
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(len(v) for v in merged.values())
    console.print(f"[green]术语表已保存：{output_path}（共 {total} 个条目）[/green]")
    return merged


# ---------------------------------------------------------------------------
# Phase 1: 章节翻译
# ---------------------------------------------------------------------------

def _translate_string_batch(
    strings: list[str],
    language: str,
    glossary: dict,
    model: str,
    api_key: str | None,
    api_base: str | None,
) -> list[str]:
    """翻译一批字符串，返回等长的翻译列表。失败时抛出异常。"""
    glossary_text = _format_glossary_for_prompt(glossary)
    numbered = "\n".join(f'{i + 1}. {json.dumps(s, ensure_ascii=False)}' for i, s in enumerate(strings))
    lang_name = LANG_NAMES.get(language, language)

    prompt = TRANSLATE_PROMPT.format(
        language=lang_name,
        count=len(strings),
        glossary=glossary_text,
        strings=numbered,
    )

    raw = _llm_call(
        prompt=prompt,
        model=model,
        api_key=api_key,
        api_base=api_base,
        max_tokens=min(16384, max(2048, len("".join(strings)) * 4)),
        temperature=0.3,
    )
    result = _parse_json(raw)
    if not isinstance(result, list):
        raise ValueError(f"LLM 返回的不是数组: {type(result)}")
    if len(result) != len(strings):
        raise ValueError(f"翻译数量不匹配: 期望 {len(strings)}，实际 {len(result)}")
    return [str(s) for s in result]


_CHUNK_SIZE = 25       # 每块最多条数
_CHUNK_MAX_CHARS = 2000  # 每块最大字符数（防止长文本超出 max_tokens）


def translate_chapter(
    chapter: dict,
    language: str,
    glossary: dict,
    model: str,
    api_key: str | None,
    api_base: str | None,
    max_attempts: int = 3,
) -> dict:
    """
    翻译单个章节。
    策略：提取所有可翻译字符串 → 分块（每块 ≤25 条）翻译 → 回填到原结构。
    分块保证 LLM 不会截断响应。
    """
    string_paths = extract_translatable_strings(chapter)
    if not string_paths:
        return chapter

    # 分块翻译：同时限制条数（≤25）和字符数（≤2000），防止 LLM 截断
    def _make_chunks(paths: list) -> list[list]:
        chunks, cur, cur_chars = [], [], 0
        for item in paths:
            text = item[1]
            if cur and (len(cur) >= _CHUNK_SIZE or cur_chars + len(text) > _CHUNK_MAX_CHARS):
                chunks.append(cur)
                cur, cur_chars = [], 0
            cur.append(item)
            cur_chars += len(text)
        if cur:
            chunks.append(cur)
        return chunks

    all_translations: list[str] = []
    for chunk_paths in _make_chunks(string_paths):
        chunk_strings = [text for _path, text in chunk_paths]

        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                chunk_result = _translate_string_batch(
                    chunk_strings, language, glossary, model, api_key, api_base
                )
                all_translations.extend(chunk_result)
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts - 1:
                    time.sleep(10 * (2 ** attempt))
        else:
            raise RuntimeError(
                f"章节 {chapter.get('number')} 某块翻译失败（{max_attempts} 次尝试后）: {last_exc}"
            ) from last_exc

    return apply_translations(chapter, string_paths, all_translations)


def run_translation(
    source_dir: Path,
    output_dir: Path,
    language: str,
    model: str,
    api_key: str | None,
    api_base: str | None,
    glossary: dict,
    from_chapter: int,
    to_chapter: int,
    workers: int,
    resume: bool,
) -> None:
    """主翻译流程：加载章节 → 并发翻译 → 原子写入。"""
    lang_dir = output_dir / language
    chapters_out = lang_dir / "chapters"
    chapters_out.mkdir(parents=True, exist_ok=True)

    # 加载进度
    progress_state = _load_progress(lang_dir) if resume else {}

    # 加载所有源章节
    all_chapters = _load_all_chapters(source_dir)
    if not all_chapters:
        console.print("[red]未找到源章节文件[/red]")
        raise typer.Exit(1)

    # 过滤目标范围
    target_chapters = [
        ch for ch in all_chapters
        if from_chapter <= ch.get("number", 0) <= to_chapter
    ]
    if not target_chapters:
        console.print(f"[yellow]范围 {from_chapter}-{to_chapter} 内没有章节[/yellow]")
        return

    # 跳过已完成的章节
    pending = [
        ch for ch in target_chapters
        if not _chapter_exists(chapters_out, ch.get("number", 0))
    ]

    lang_name = LANG_NAMES.get(language, language)
    console.print(Panel(
        f"[bold]目标语言:[/bold] {lang_name} ({language})\n"
        f"[bold]源章节:[/bold] {len(all_chapters)} 章\n"
        f"[bold]翻译范围:[/bold] {from_chapter} - {to_chapter}\n"
        f"[bold]待翻译:[/bold] {len(pending)} 章（已完成 {len(target_chapters) - len(pending)} 章）\n"
        f"[bold]并发数:[/bold] {workers}\n"
        f"[bold]模型:[/bold] {model}",
        title="翻译任务",
        border_style="cyan",
    ))

    if not pending:
        console.print("[green]所有章节已完成翻译！[/green]")
        return

    errors: list[str] = []
    done = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[cyan]{task.fields[rate]}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"翻译 → {language}",
            total=len(pending),
            rate="",
        )
        start_time = time.time()

        def _translate_one(chapter: dict) -> tuple[int, dict | None, str | None]:
            """返回 (chapter_number, translated_chapter | None, error | None)"""
            try:
                translated = translate_chapter(
                    chapter, language, glossary, model, api_key, api_base
                )
                return chapter["number"], translated, None
            except Exception as exc:
                return chapter["number"], None, str(exc)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_translate_one, ch): ch for ch in pending}
            for future in as_completed(futures):
                num, translated, error = future.result()
                done += 1

                if translated is not None:
                    _save_chapter(chapters_out, translated)
                    progress_state[str(num)] = "done"
                    _save_progress(lang_dir, progress_state)
                else:
                    errors.append(f"章节 {num}: {error}")
                    progress_state[str(num)] = "error"
                    _save_progress(lang_dir, progress_state)

                elapsed = time.time() - start_time
                rate = f"{done / elapsed * 60:.1f} 章/分" if elapsed > 0 else ""
                progress.advance(task)
                progress.update(task, rate=rate)

    # 输出汇总
    success = len(pending) - len(errors)
    console.print(Panel(
        f"[green]成功：{success} 章[/green]  [red]失败：{len(errors)} 章[/red]\n"
        + (f"\n失败详情（前 5 条）：\n" + "\n".join(errors[:5]) if errors else ""),
        title="翻译完成",
        border_style="green" if not errors else "yellow",
    ))

    # 将错误写入日志文件
    if errors:
        errors_path = lang_dir / "errors.log"
        errors_path.write_text("\n".join(errors), encoding="utf-8")
        console.print(f"[yellow]错误日志：{errors_path}[/yellow]")


# ---------------------------------------------------------------------------
# Phase 2: 验证
# ---------------------------------------------------------------------------

def validate_translations(
    source_dir: Path,
    translated_dir: Path,
) -> None:
    """检查翻译文件的结构完整性和覆盖率。"""
    source_chapters = _load_all_chapters(source_dir)
    translated_chapters = _load_all_chapters(translated_dir)

    source_nums = {ch["number"] for ch in source_chapters}
    trans_nums = {ch["number"] for ch in translated_chapters}

    missing = sorted(source_nums - trans_nums)
    coverage = len(trans_nums & source_nums) / len(source_nums) * 100 if source_nums else 0

    table = Table(title="翻译验证报告", show_header=True)
    table.add_column("指标", style="cyan")
    table.add_column("数值", justify="right")
    table.add_row("源章节总数", str(len(source_chapters)))
    table.add_row("已翻译章节", str(len(trans_nums & source_nums)))
    table.add_row("覆盖率", f"{coverage:.1f}%")
    table.add_row("缺失章节数", str(len(missing)))
    console.print(table)

    if missing:
        console.print(f"\n[yellow]缺失章节（前 20 个）: {missing[:20]}[/yellow]")

    # 结构校验（抽样 20 章）
    import random
    sample = random.sample(list(trans_nums & source_nums), min(20, len(trans_nums & source_nums)))
    struct_errors = []
    for num in sample:
        src_ch = next(ch for ch in source_chapters if ch["number"] == num)
        trans_ch = next(ch for ch in translated_chapters if ch["number"] == num)

        # 检查不应被翻译的字段是否被意外修改
        for field in ("id", "book_id", "number"):
            if src_ch.get(field) != trans_ch.get(field):
                struct_errors.append(f"章节 {num}: 字段 '{field}' 被修改")

        # 检查节点数量是否一致
        if len(src_ch.get("nodes", [])) != len(trans_ch.get("nodes", [])):
            struct_errors.append(
                f"章节 {num}: 节点数量不一致 "
                f"(源={len(src_ch.get('nodes', []))}, "
                f"译={len(trans_ch.get('nodes', []))})"
            )

    if struct_errors:
        console.print(f"\n[red]结构错误（抽样 {len(sample)} 章中发现 {len(struct_errors)} 个）:[/red]")
        for err in struct_errors[:10]:
            console.print(f"  [red]• {err}[/red]")
    else:
        console.print(f"\n[green]✓ 结构校验通过（抽样 {len(sample)} 章无异常）[/green]")


# ---------------------------------------------------------------------------
# CLI 命令
# ---------------------------------------------------------------------------

@app.command("extract-glossary")
def cmd_extract_glossary(
    input: Path = typer.Option(..., "--input", "-i", help="源章节目录（包含 ch*.json）"),
    output: Path = typer.Option(..., "--output", "-o", help="翻译输出根目录"),
    model: str = typer.Option("minimax/MiniMax-Text-01", "--model", "-m", help="LLM 模型"),
    api_key: str = typer.Option("", "--api-key", help="API 密钥（也可通过环境变量设置）"),
    api_base: str = typer.Option("", "--api-base", help="自定义 API base URL（可选）"),
    sample: int = typer.Option(50, "--sample", "-n", help="采样章节数（用于提取术语）"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新提取（即使术语表已存在）"),
) -> None:
    """从小说前 N 章提取专有名词术语表（人名/地名/功法名等）。"""
    glossary_path = output / "glossary.json"

    if glossary_path.exists() and not force:
        console.print(f"[yellow]术语表已存在：{glossary_path}[/yellow]")
        console.print("使用 --force 强制重新提取。")
        return

    if not input.exists():
        console.print(f"[red]源目录不存在：{input}[/red]")
        raise typer.Exit(1)

    extract_glossary(
        chapters_dir=input,
        output_path=glossary_path,
        model=model,
        api_key=api_key or None,
        api_base=api_base or None,
        n_sample=sample,
    )

    console.print("\n[bold]下一步：[/bold] 可手动编辑术语表后再运行翻译命令。")
    console.print(f"  术语表路径：{glossary_path}")


@app.command("translate")
def cmd_translate(
    input: Path = typer.Option(..., "--input", "-i", help="源章节目录（包含 ch*.json）"),
    output: Path = typer.Option(..., "--output", "-o", help="翻译输出根目录"),
    lang: str = typer.Option(..., "--lang", "-l", help="目标语言代码，如 en/ja/ko"),
    model: str = typer.Option("minimax/MiniMax-Text-01", "--model", "-m", help="LLM 模型"),
    api_key: str = typer.Option("", "--api-key", help="API 密钥"),
    api_base: str = typer.Option("", "--api-base", help="自定义 API base URL（可选）"),
    from_chapter: int = typer.Option(1, "--from-chapter", help="起始章节号"),
    to_chapter: int = typer.Option(99999, "--to-chapter", help="结束章节号（默认全部）"),
    workers: int = typer.Option(8, "--workers", "-w", help="并发翻译线程数（配合 --rpm 使用）"),
    rpm: int = typer.Option(400, "--rpm", help="最大请求速率（次/分钟），不超过模型限制"),
    resume: bool = typer.Option(True, "--resume/--no-resume", "-r", help="断点续传（默认开启）"),
    glossary_file: str = typer.Option("", "--glossary", "-g", help="术语表 JSON 路径（默认自动查找）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """将小说章节翻译为指定语言（支持断点续传）。"""
    if not input.exists():
        console.print(f"[red]源目录不存在：{input}[/red]")
        raise typer.Exit(1)

    # 加载术语表
    glossary: dict = {}
    if glossary_file:
        gp = Path(glossary_file)
    else:
        gp = output / "glossary.json"

    if gp.exists():
        try:
            glossary = json.loads(gp.read_text(encoding="utf-8"))
            total_terms = sum(len(v) for v in glossary.values() if isinstance(v, dict))
            console.print(f"[cyan]已加载术语表：{gp}（{total_terms} 个条目）[/cyan]")
        except Exception as exc:
            console.print(f"[yellow]加载术语表失败，将使用空术语表：{exc}[/yellow]")
    else:
        console.print(f"[yellow]未找到术语表（{gp}），将不使用专有名词映射。[/yellow]")
        console.print("建议先运行 [bold]extract-glossary[/bold] 命令。\n")

    lang_name = LANG_NAMES.get(lang, lang)

    # 初始化全局速率限制器
    global _rate_limiter
    _rate_limiter = _RateLimiter(max_rpm=rpm)
    console.print(f"[dim]速率限制：{rpm} RPM（每 {60/rpm:.2f}s 最多 1 次请求）[/dim]")

    if not yes:
        console.print(f"\n[bold]翻译配置：[/bold] {lang_name} ({lang})，模型：{model}，并发：{workers}，RPM 上限：{rpm}")
        confirmed = typer.confirm("确认开始翻译？", default=True)
        if not confirmed:
            raise typer.Abort()

    run_translation(
        source_dir=input,
        output_dir=output,
        language=lang,
        model=model,
        api_key=api_key or None,
        api_base=api_base or None,
        glossary=glossary,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        workers=workers,
        resume=resume,
    )


@app.command("validate")
def cmd_validate(
    input: Path = typer.Option(..., "--input", "-i", help="源章节目录"),
    translated: Path = typer.Option(..., "--translated", "-t", help="已翻译章节目录"),
) -> None:
    """验证翻译结果的完整性和结构正确性。"""
    if not input.exists():
        console.print(f"[red]源目录不存在：{input}[/red]")
        raise typer.Exit(1)
    if not translated.exists():
        console.print(f"[red]翻译目录不存在：{translated}[/red]")
        raise typer.Exit(1)
    validate_translations(input, translated)


@app.command("status")
def cmd_status(
    output: Path = typer.Option(..., "--output", "-o", help="翻译输出根目录"),
    input: Path = typer.Option(..., "--input", "-i", help="源章节目录"),
) -> None:
    """查看所有语言的翻译进度。"""
    if not output.exists():
        console.print("[yellow]翻译目录尚不存在[/yellow]")
        return

    source_chapters = _load_all_chapters(input)
    total = len(source_chapters)

    table = Table(title="多语言翻译进度", show_header=True)
    table.add_column("语言", style="cyan")
    table.add_column("代码")
    table.add_column("已完成", justify="right")
    table.add_column("总章节", justify="right")
    table.add_column("进度", justify="right")
    table.add_column("状态")

    for lang_dir in sorted(output.iterdir()):
        if not lang_dir.is_dir() or lang_dir.name.startswith("."):
            continue
        chapters_dir = lang_dir / "chapters"
        done = len(list(chapters_dir.glob("ch*.json"))) if chapters_dir.exists() else 0
        pct = done / total * 100 if total > 0 else 0
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        status = "[green]完成[/green]" if done >= total else "[yellow]进行中[/yellow]"
        lang_name = LANG_NAMES.get(lang_dir.name, lang_dir.name)
        table.add_row(lang_name, lang_dir.name, str(done), str(total), f"{pct:.1f}% {bar}", status)

    console.print(table)

    glossary_path = output / "glossary.json"
    if glossary_path.exists():
        console.print(f"\n[green]✓ 术语表已存在：{glossary_path}[/green]")
    else:
        console.print(f"\n[yellow]⚠ 未找到术语表，建议先运行 extract-glossary[/yellow]")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()

#!/usr/bin/env python3
"""
retranslate_residual.py — 局部 LLM 重译：只处理含中文残留的字符串。

策略：
  1. 加载已机械修复的目标语言章节
  2. 加载对应的中文源章节（同 path）
  3. 对每个可翻译字段：检测目标语言文本是否含残留中文（pure-CJK 6+ 字串无 kana/hangul）
  4. 收集 (path, src_zh, current_target) 三元组
  5. 用 ZH 原文重新翻译，应用术语表
  6. 写回章节，原子保存

支持：
  - --dry-run 仅统计需要重译的字符串数和成本估算
  - --limit N 仅处理前 N 章（小规模测试）
  - --lang en|ja|ko，可重复
  - --workers 并发数
  - --rpm 速率限制
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

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

from translate_novel import (
    LANG_NAMES,
    PRESERVE_FIELDS,
    TRANSLATABLE_FIELDS,
    _RateLimiter,
    _format_glossary_for_prompt,
    _llm_call,
    _load_all_chapters,
    _parse_json,
    _save_chapter,
)
import translate_novel as _tn

app = typer.Typer(help="局部 LLM 重译：只处理含中文残留的字符串", add_completion=False)
console = Console()

SRC_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
GLOSSARY_PATH = TRANS_DIR / "glossary.json"
EXTRA_GLOSSARY_PATH = TRANS_DIR / "extra_glossary.json"

# JA: pure-CJK run >=4 chars (避免误伤纯汉字日语词如 "外門弟子")
# KO: any run of 3+ CJK chars (韩文应几乎无汉字)
RESIDUAL_PATTERNS = {
    "ja": re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}"),
    "ko": re.compile(r"[\u4e00-\u9fff]{3,}"),
    "en": re.compile(r"[\u4e00-\u9fff]{2,}"),
}


# --------------------------------------------------------------------------
# JSON path utils
# --------------------------------------------------------------------------

def _path_get(obj: Any, path: list) -> Any:
    cur = obj
    for k in path:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def _path_set(obj: Any, path: list, value: Any) -> bool:
    cur = obj
    for k in path[:-1]:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return False
    try:
        cur[path[-1]] = value
        return True
    except (KeyError, IndexError, TypeError):
        return False


def _walk_translatable(obj: Any, path: list, results: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PRESERVE_FIELDS:
                continue
            if k in TRANSLATABLE_FIELDS and isinstance(v, str) and v.strip():
                results.append((path + [k], v))
            else:
                _walk_translatable(v, path + [k], results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_translatable(item, path + [i], results)


# --------------------------------------------------------------------------
# Residual detection + plan
# --------------------------------------------------------------------------

@dataclass
class StringTask:
    chapter_num: int
    path: list
    src_zh: str
    current: str


def has_residual(text: str, lang: str) -> bool:
    pat = RESIDUAL_PATTERNS.get(lang)
    return bool(pat and pat.search(text))


def plan_chapter(src_chapter: dict, tgt_chapter: dict, lang: str) -> list[StringTask]:
    """对齐 src 与 tgt 节点路径，返回需要重译的 StringTask 列表。"""
    src_strings = []
    tgt_strings = []
    _walk_translatable(src_chapter, [], src_strings)
    _walk_translatable(tgt_chapter, [], tgt_strings)

    # 用 path → text 索引快速对齐（path 转 tuple 作 key）
    src_map: dict[tuple, str] = {tuple(p): t for p, t in src_strings}
    tasks: list[StringTask] = []
    chapter_num = tgt_chapter.get("number", 0)

    for path, current in tgt_strings:
        if not has_residual(current, lang):
            continue
        src_text = src_map.get(tuple(path))
        if not src_text:
            # 路径错位：跳过（无法可靠重译）
            continue
        tasks.append(StringTask(chapter_num, path, src_text, current))
    return tasks


# --------------------------------------------------------------------------
# Glossary loading
# --------------------------------------------------------------------------

def load_glossary(lang: str) -> dict:
    """加载 glossary.json + extra_glossary.json，按目标语言展开。"""
    base: dict = {}
    if GLOSSARY_PATH.exists():
        try:
            base = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    extra: dict = {}
    if EXTRA_GLOSSARY_PATH.exists():
        try:
            extra = json.loads(EXTRA_GLOSSARY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    # extra_glossary 是 {category: {zh: {en, ja, ko}}}，需要按目标语言展开
    merged: dict[str, dict[str, str]] = {
        cat: dict(items) for cat, items in base.items() if isinstance(items, dict)
    }
    for cat, items in extra.items():
        if not isinstance(items, dict):
            continue
        merged.setdefault(cat, {})
        for zh, mapping in items.items():
            if isinstance(mapping, dict):
                target = mapping.get(lang) if lang != "en" else mapping.get("en")
                if target and zh not in merged[cat]:
                    merged[cat][zh] = target
    return merged


# --------------------------------------------------------------------------
# Batch retranslation
# --------------------------------------------------------------------------

_BATCH_SIZE = 20
_BATCH_MAX_CHARS = 1800


def _make_batches(tasks: list[StringTask]) -> list[list[StringTask]]:
    batches: list[list[StringTask]] = []
    cur: list[StringTask] = []
    cur_chars = 0
    for t in tasks:
        if cur and (len(cur) >= _BATCH_SIZE or cur_chars + len(t.src_zh) > _BATCH_MAX_CHARS):
            batches.append(cur)
            cur = []
            cur_chars = 0
        cur.append(t)
        cur_chars += len(t.src_zh)
    if cur:
        batches.append(cur)
    return batches


STRICT_TRANSLATE_PROMPT = """\
你是专业小说翻译，源语言：中文，目标语言：{language}。

## 严格规则
1. 输出**必须 100% 是 {language}**，不得保留任何中文片段
2. 中文成语/俗语/固定搭配必须意译为 {language} 中地道的等价表达
3. 严格遵循【术语表】翻译专有名词
4. 保持文学风格、节奏感、情绪张力
5. 返回格式：仅一个 JSON 数组，{count} 条字符串，顺序与输入一致
6. 数组元素必须是字符串，不要嵌套对象，不要附加解释

{lang_specific}

## 术语表
{glossary}

## 待翻译（共 {count} 条）
{strings}

只返回 JSON 数组：["译文1", "译文2", ...]
"""

LANG_SPECIFIC_RULES = {
    "ja": (
        "## 日本語特殊規則\n"
        "- 必ず仮名（ひらがな/カタカナ）と漢字を自然に混ぜて、純粋な漢字連続を避ける\n"
        "- 中国の成語（例：莫名其妙、此刻、打量、烦躁、目光、身影）は和語または日本漢語表現に置き換える\n"
        "- 「的」「了」「在」など中国語の語尾は使わない\n"
    ),
    "ko": (
        "## 한국어 특수 규칙\n"
        "- 모든 출력은 한글로 작성. 한자 사용 금지\n"
        "- 중국어 성어/관용구는 자연스러운 한국어 표현으로 의역\n"
        "- 고유명사만 음역 후 한자 병기 가능 (예: 진기(陳機))\n"
    ),
    "en": (
        "## English-Specific Rules\n"
        "- Output must be fluent English; no Chinese characters at all\n"
        "- Render Chinese idioms with natural English equivalents\n"
    ),
}


def retranslate_batch(
    batch: list[StringTask],
    lang: str,
    glossary: dict,
    model: str,
    api_key: str | None,
    api_base: str | None,
    max_attempts: int = 3,
) -> list[str]:
    """用 strict prompt 翻译 ZH 原文。失败抛 RuntimeError。"""
    strings = [t.src_zh for t in batch]
    glossary_text = _format_glossary_for_prompt(glossary)
    lang_name = LANG_NAMES.get(lang, lang)
    numbered = "\n".join(
        f"{i + 1}. {json.dumps(s, ensure_ascii=False)}" for i, s in enumerate(strings)
    )
    prompt = STRICT_TRANSLATE_PROMPT.format(
        language=lang_name,
        count=len(strings),
        glossary=glossary_text,
        strings=numbered,
        lang_specific=LANG_SPECIFIC_RULES.get(lang, ""),
    )

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            raw = _llm_call(
                prompt=prompt,
                model=model,
                api_key=api_key,
                api_base=api_base,
                max_tokens=min(16384, max(2048, sum(len(s) for s in strings) * 4)),
                temperature=0.3,
            )
            result = _parse_json(raw)
            if not isinstance(result, list):
                raise ValueError(f"非数组响应: {type(result).__name__}")
            if len(result) != len(strings):
                raise ValueError(f"数量不匹配: 期望 {len(strings)}, 实际 {len(result)}")
            return [str(x) for x in result]
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            is_rate_limit = (
                "RateLimitError" in type(exc).__name__
                or "2056" in msg
                or "2062" in msg
                or "usage limit" in msg
                or "Token Plan" in msg
            )
            if is_rate_limit:
                # Rate-limit-specific: wait long and retry up to 6 times
                if attempt < 6:
                    wait = min(60 * (2 ** attempt), 900)  # 60, 120, 240, 480, 900, 900
                    time.sleep(wait)
                    continue
            if attempt < max_attempts - 1:
                time.sleep(min(8 * (2 ** attempt), 60))
    raise RuntimeError(f"批次重译失败（{max_attempts} 次）: {last_exc}") from last_exc


# --------------------------------------------------------------------------
# Per-chapter worker
# --------------------------------------------------------------------------

def process_chapter(
    src_chapter: dict,
    tgt_chapter: dict,
    lang: str,
    glossary: dict,
    model: str,
    api_key: str | None,
    api_base: str | None,
    inner_workers: int = 4,
) -> tuple[int, dict]:
    """重译单章。返回 (修复字段数, 更新后的 tgt chapter).

    Batches within the chapter are processed in parallel via inner_workers.
    """
    tasks = plan_chapter(src_chapter, tgt_chapter, lang)
    if not tasks:
        return 0, tgt_chapter

    new_chapter = json.loads(json.dumps(tgt_chapter, ensure_ascii=False))
    fixed = 0
    batches = _make_batches(tasks)

    if len(batches) == 1:
        translations = retranslate_batch(
            batches[0], lang, glossary, model, api_key, api_base
        )
        for task, new_text in zip(batches[0], translations):
            if _path_set(new_chapter, task.path, new_text):
                fixed += 1
        return fixed, new_chapter

    # Parallel batch processing
    def _do_batch(batch: list[StringTask]) -> tuple[list[StringTask], list[str]]:
        return batch, retranslate_batch(batch, lang, glossary, model, api_key, api_base)

    with ThreadPoolExecutor(max_workers=min(inner_workers, len(batches))) as ex:
        for batch, translations in ex.map(_do_batch, batches):
            for task, new_text in zip(batch, translations):
                if _path_set(new_chapter, task.path, new_text):
                    fixed += 1
    return fixed, new_chapter


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


@app.command("plan")
def cmd_plan(
    lang: list[str] = typer.Option(["ja", "ko"], "--lang", "-l"),
    limit: Optional[int] = typer.Option(None, "--limit"),
) -> None:
    """统计需要重译的字符串数（不调用 LLM）。"""
    src_chapters = {ch["number"]: ch for ch in _load_all_chapters(SRC_DIR)}

    for l in lang:
        chapters_dir = TRANS_DIR / l / "chapters"
        if not chapters_dir.exists():
            console.print(f"[yellow]{l}: 无章节目录[/yellow]")
            continue
        tgt_chapters = _load_all_chapters(chapters_dir)
        if limit:
            tgt_chapters = tgt_chapters[:limit]

        total_strings = 0
        total_chars = 0
        affected_chapters = 0
        with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]{l}[/cyan] 扫描章节"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("scan", total=len(tgt_chapters))
            for tgt in tgt_chapters:
                src = src_chapters.get(tgt.get("number"))
                if not src:
                    progress.advance(task)
                    continue
                tasks = plan_chapter(src, tgt, l)
                if tasks:
                    affected_chapters += 1
                    total_strings += len(tasks)
                    total_chars += sum(len(t.src_zh) for t in tasks)
                progress.advance(task)

        # MiniMax M2.7 估算: input ¥3/M, output ¥9/M（保守按 ¥12/M 综合）
        # 估算 prompt overhead ~500 chars/batch, output ~= input chars
        est_batches = max(1, total_strings // _BATCH_SIZE + 1)
        est_input_chars = total_chars + est_batches * 800  # prompt overhead
        est_output_chars = total_chars * 1.5  # 译文通常略长
        est_yuan = (est_input_chars * 3 + est_output_chars * 9) / 1_000_000

        console.print(Panel(
            f"语言: {LANG_NAMES.get(l, l)}\n"
            f"扫描章节: {len(tgt_chapters)}\n"
            f"含残留章节: {affected_chapters}\n"
            f"待重译字符串: {total_strings:,}\n"
            f"待重译字符: {total_chars:,}\n"
            f"预计 batch 数: {est_batches:,}\n"
            f"预估成本（MiniMax M2.7）: ¥{est_yuan:.2f}",
            title=f"[{l}] 重译计划",
            border_style="cyan",
        ))


@app.command("run")
def cmd_run(
    lang: list[str] = typer.Option(["ja", "ko"], "--lang", "-l"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    start: Optional[int] = typer.Option(None, "--start", help="Chapter range start (inclusive)"),
    end: Optional[int] = typer.Option(None, "--end", help="Chapter range end (inclusive)"),
    model: str = typer.Option("openai/MiniMax-M2.7", "--model", "-m"),
    api_key: str = typer.Option("", "--api-key"),
    api_base: str = typer.Option("https://api.minimaxi.com/v1", "--api-base"),
    workers: int = typer.Option(8, "--workers", "-w"),
    rpm: int = typer.Option(400, "--rpm"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """实际执行 LLM 重译。"""
    _load_env()
    resolved_key = api_key or os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        console.print("[red]未找到 API Key[/red]")
        raise typer.Exit(1)

    if not yes:
        typer.confirm(f"对 {lang} 启动 LLM 局部重译，确认？", default=True, abort=True)

    _tn._rate_limiter = _RateLimiter(max_rpm=rpm)
    src_chapters = {ch["number"]: ch for ch in _load_all_chapters(SRC_DIR)}

    summary: dict[str, dict] = {}
    for l in lang:
        chapters_dir = TRANS_DIR / l / "chapters"
        tgt_chapters = _load_all_chapters(chapters_dir)
        if start is not None or end is not None:
            s = start or 1
            e = end or 99999
            tgt_chapters = [c for c in tgt_chapters if s <= c.get("number", 0) <= e]
        if limit:
            tgt_chapters = tgt_chapters[:limit]
        glossary = load_glossary(l)

        # 备份目录（一次性）
        backup_dir = TRANS_DIR / l / "chapters_pre_llm"
        if not backup_dir.exists():
            import shutil
            shutil.copytree(chapters_dir, backup_dir)
            console.print(f"[{l}] 备份 → {backup_dir}")

        fixed_chapters = 0
        fixed_fields = 0
        failed_chapters: list[tuple[int, str]] = []

        with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]{l}[/cyan] 重译"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("retranslate", total=len(tgt_chapters))

            def _one(tgt: dict) -> tuple[int, int, str | None]:
                num = tgt.get("number", 0)
                src = src_chapters.get(num)
                if not src:
                    return num, 0, None
                try:
                    fixed, updated = process_chapter(
                        src, tgt, l, glossary, model, resolved_key, api_base
                    )
                    if fixed > 0:
                        _save_chapter(chapters_dir, updated)
                    return num, fixed, None
                except Exception as exc:
                    return num, 0, f"{type(exc).__name__}: {exc}"

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_one, t): t for t in tgt_chapters}
                for fut in as_completed(futures):
                    num, fixed, err = fut.result()
                    if err:
                        failed_chapters.append((num, err))
                    elif fixed > 0:
                        fixed_chapters += 1
                        fixed_fields += fixed
                    progress.advance(task)

        # 写错误日志
        if failed_chapters:
            err_log = TRANS_DIR / l / "retranslate_errors.log"
            err_log.write_text(
                "\n".join(f"ch{n:04d}: {e}" for n, e in failed_chapters),
                encoding="utf-8",
            )

        summary[l] = {
            "scanned": len(tgt_chapters),
            "fixed_chapters": fixed_chapters,
            "fixed_fields": fixed_fields,
            "failed": len(failed_chapters),
        }
        console.print(
            f"[green][{l}] 完成: {fixed_chapters} 章修复, "
            f"{fixed_fields} 字段, {len(failed_chapters)} 失败[/green]"
        )

    report_path = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "retranslate_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\n报告 → {report_path}")


if __name__ == "__main__":
    app()

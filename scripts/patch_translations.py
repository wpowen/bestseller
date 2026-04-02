#!/usr/bin/env python3
"""
patch_translations.py — 翻译查漏补缺工具

功能：
  1. scan   — 扫描缺失章节 + 检测语言一致性（已翻译但内容仍是中文）
  2. patch  — 补翻缺失章节 + 重翻语言错误章节
  3. report — 输出完整健康报告

用法：
    python scripts/patch_translations.py scan  --lang en
    python scripts/patch_translations.py patch --lang en
    python scripts/patch_translations.py patch --lang en --lang ja --lang ko
    python scripts/patch_translations.py report
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

# 复用 translate_novel 的核心函数
sys.path.insert(0, str(Path(__file__).parent))
from translate_novel import (
    _llm_call, _parse_json, _rate_limiter, _RateLimiter,
    _save_chapter, _chapter_exists, _load_all_chapters,
    extract_translatable_strings, apply_translations,
    _translate_string_batch, translate_chapter,
    LANG_NAMES, _CHUNK_SIZE,
)
import translate_novel as _tn

app = typer.Typer(help="翻译查漏补缺工具", add_completion=False)
console = Console()

# ---------------------------------------------------------------------------
# 语言检测
# ---------------------------------------------------------------------------

def _cjk_ratio(text: str) -> float:
    """返回文本中 CJK（中文）字符的占比。"""
    if not text:
        return 0.0
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
    return cjk / len(text)

def _has_target_lang_chars(text: str, lang: str) -> bool:
    """粗判文本是否含目标语言字符（非中文）。"""
    if lang == "en":
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        return ascii_ratio > 0.5
    if lang == "ja":
        ja = sum(1 for c in text if '\u3040' <= c <= '\u30ff' or '\uff66' <= c <= '\uff9f')
        return ja > 0
    if lang == "ko":
        ko = sum(1 for c in text if '\uac00' <= c <= '\ud7af')
        return ko > 0
    return True  # 其他语言不做检测

def _has_lang_marker(text: str, lang: str) -> bool:
    """检查文本是否包含目标语言的特征字符。"""
    if lang == "en":
        # 英文：ASCII 字母占比高，CJK 占比低
        ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return ascii_alpha > 5 and cjk / max(len(text), 1) < 0.3
    if lang == "ja":
        # 日文：必须有平假名或片假名（汉字不算，因为中文也有）
        ja_kana = sum(1 for c in text if '\u3040' <= c <= '\u30ff' or '\uff66' <= c <= '\uff9f')
        return ja_kana > 0
    if lang == "ko":
        # 韩文：必须有谚文字符
        hangul = sum(1 for c in text if '\uac00' <= c <= '\ud7af')
        return hangul > 0
    return True


def is_translation_valid(chapter: dict, lang: str) -> tuple[bool, str]:
    """
    检查已翻译章节是否有效：
    - title 必须含有目标语言特征字符
    - 抽样正文节点，至少有一个含目标语言特征
    返回 (is_valid, reason)
    """
    title = chapter.get("title", "")

    # title 检查
    if title and not _has_lang_marker(title, lang):
        if lang == "ja":
            # 日语标题可以全是汉字（合法）；只有纯 ASCII/Latin 才是真错误
            ascii_ratio = sum(1 for c in title if c.isascii()) / max(len(title), 1)
            if ascii_ratio >= 0.6:
                return False, f"title 疑似非日语（ASCII 占比 {ascii_ratio:.0%}）: {title!r}"
        else:
            return False, f"title 未检测到{lang}字符: {title!r}"

    # 抽样正文检查（取前 3 个 text/dialogue）
    samples = []
    for node in chapter.get("nodes", []):
        for ntype in ("text", "dialogue"):
            if ntype in node:
                c = node[ntype].get("content", "")
                if c and len(c) > 20:
                    samples.append(c)
        if len(samples) >= 3:
            break

    if samples:
        valid_count = sum(1 for s in samples if _has_lang_marker(s, lang))
        # 要求超过一半的样本含目标语言字符
        if valid_count < max(1, len(samples) // 2 + 1):
            return False, f"正文{lang}字符不足（{valid_count}/{len(samples)} 段通过）"

    return True, "ok"


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------

def scan_lang(
    source_dir: Path,
    trans_dir: Path,
    lang: str,
) -> dict:
    """
    扫描单个语言的翻译状态。
    返回: {
        "missing": [chapter_numbers],       # 文件完全缺失
        "invalid": [(number, reason)],      # 文件存在但语言错误
        "ok": int,                          # 正常章节数
    }
    """
    source_chapters = _load_all_chapters(source_dir)
    source_nums = {ch["number"] for ch in source_chapters}

    chapters_dir = trans_dir / lang / "chapters"
    trans_chapters = _load_all_chapters(chapters_dir) if chapters_dir.exists() else []
    trans_map = {ch["number"]: ch for ch in trans_chapters}

    missing = sorted(source_nums - set(trans_map.keys()))
    invalid = []
    ok_count = 0

    for num, ch in sorted(trans_map.items()):
        valid, reason = is_translation_valid(ch, lang)
        if not valid:
            invalid.append((num, reason))
        else:
            ok_count += 1

    return {"missing": missing, "invalid": invalid, "ok": ok_count}


# ---------------------------------------------------------------------------
# 补翻
# ---------------------------------------------------------------------------

def patch_lang(
    source_dir: Path,
    trans_dir: Path,
    lang: str,
    model: str,
    api_key: str | None,
    api_base: str | None,
    workers: int,
    rpm: int,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    补翻缺失 + 重翻语言错误章节。
    返回 (success_count, fail_count)
    """
    # 初始化速率限制器
    _tn._rate_limiter = _RateLimiter(max_rpm=rpm)

    result = scan_lang(source_dir, trans_dir, lang)
    missing = result["missing"]
    invalid_nums = [n for n, _ in result["invalid"]]

    to_patch = sorted(set(missing) | set(invalid_nums))
    if not to_patch:
        console.print(f"[green][{lang}] 无需补翻！[/green]")
        return 0, 0

    lang_name = LANG_NAMES.get(lang, lang)
    console.print(Panel(
        f"语言：{lang_name}\n"
        f"缺失章节：{len(missing)} 章\n"
        f"语言错误：{len(invalid_nums)} 章\n"
        f"合计补翻：{len(to_patch)} 章",
        title=f"[{lang}] 补翻任务",
        border_style="yellow",
    ))

    if dry_run:
        console.print("[yellow]Dry run 模式，不实际翻译[/yellow]")
        if missing[:10]:
            console.print(f"  缺失示例: {missing[:10]}")
        if invalid_nums[:5]:
            console.print(f"  错误示例: {invalid_nums[:5]}")
        return 0, 0

    # 加载源章节
    source_chapters = {ch["number"]: ch for ch in _load_all_chapters(source_dir)}
    chapters_out = trans_dir / lang / "chapters"
    chapters_out.mkdir(parents=True, exist_ok=True)

    # 加载术语表
    glossary: dict = {}
    gp = trans_dir / "glossary.json"
    if gp.exists():
        try:
            glossary = json.loads(gp.read_text(encoding="utf-8"))
        except Exception:
            pass

    success = 0
    fail = 0
    errors: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TaskProgressColumn(), TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"补翻 [{lang}]", total=len(to_patch))

        def _patch_one(num: int) -> tuple[int, bool, str]:
            src = source_chapters.get(num)
            if src is None:
                return num, False, f"源章节 {num} 不存在"
            try:
                translated = translate_chapter(src, lang, glossary, model, api_key, api_base)
                _save_chapter(chapters_out, translated)
                return num, True, ""
            except Exception as exc:
                return num, False, str(exc)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_patch_one, n): n for n in to_patch}
            for future in as_completed(futures):
                num, ok, err = future.result()
                if ok:
                    success += 1
                else:
                    fail += 1
                    errors.append(f"章节 {num}: {err}")
                progress.advance(task)

    # 覆盖 errors.log（只保留本次失败）
    err_log = trans_dir / lang / "errors.log"
    if errors:
        err_log.write_text("\n".join(errors), encoding="utf-8")
    elif err_log.exists():
        err_log.unlink()

    console.print(f"[green][{lang}] 补翻完成：成功 {success} 章，失败 {fail} 章[/green]")
    return success, fail


# ---------------------------------------------------------------------------
# CLI 命令
# ---------------------------------------------------------------------------

@app.command("scan")
def cmd_scan(
    source: Path = typer.Option(Path("output/天机录/if/chapters"), "--source", "-s"),
    output: Path = typer.Option(Path("output/天机录/translations"), "--output", "-o"),
    lang: list[str] = typer.Option(["en", "ja", "ko"], "--lang", "-l"),
) -> None:
    """扫描缺失章节和语言错误章节，输出报告（不实际翻译）。"""
    source_total = len(list(source.glob("ch*.json")))

    table = Table(title="翻译健康扫描报告", show_header=True)
    table.add_column("语言", style="cyan")
    table.add_column("完成", justify="right")
    table.add_column("缺失", justify="right", style="red")
    table.add_column("语言错误", justify="right", style="yellow")
    table.add_column("正常", justify="right", style="green")
    table.add_column("覆盖率", justify="right")

    for l in lang:
        with console.status(f"扫描 {l}..."):
            r = scan_lang(source, output, l)
        total_trans = r["ok"] + len(r["invalid"])
        coverage = (r["ok"] + total_trans) / source_total / 2 * 100  # rough
        coverage = (source_total - len(r["missing"])) / source_total * 100
        table.add_row(
            LANG_NAMES.get(l, l),
            str(source_total - len(r["missing"])),
            str(len(r["missing"])),
            str(len(r["invalid"])),
            str(r["ok"]),
            f"{coverage:.1f}%",
        )

        if r["invalid"][:3]:
            console.print(f"\n  [{l}] 语言错误示例:")
            for num, reason in r["invalid"][:3]:
                console.print(f"    章节 {num}: {reason}")

    console.print(table)


@app.command("patch")
def cmd_patch(
    source: Path = typer.Option(Path("output/天机录/if/chapters"), "--source", "-s"),
    output: Path = typer.Option(Path("output/天机录/translations"), "--output", "-o"),
    lang: list[str] = typer.Option(["en", "ja", "ko"], "--lang", "-l"),
    model: str = typer.Option("openai/MiniMax-M2.7", "--model", "-m"),
    api_key: str = typer.Option("", "--api-key"),
    api_base: str = typer.Option("https://api.minimaxi.com/v1", "--api-base"),
    workers: int = typer.Option(13, "--workers", "-w"),
    rpm: int = typer.Option(800, "--rpm"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只显示缺口，不实际翻译"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """补翻缺失章节 + 重翻语言错误章节。"""
    # 加载 .env
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    resolved_key = api_key or os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

    if not dry_run:
        if not resolved_key:
            console.print("[red]未找到 API Key，请设置 MINIMAX_API_KEY 或传入 --api-key[/red]")
            raise typer.Exit(1)
        if not yes:
            typer.confirm(f"开始补翻语言：{lang}，确认？", default=True, abort=True)

    total_success = total_fail = 0
    for l in lang:
        s, f = patch_lang(
            source_dir=source,
            trans_dir=output,
            lang=l,
            model=model,
            api_key=resolved_key,
            api_base=api_base or None,
            workers=workers,
            rpm=rpm,
            dry_run=dry_run,
        )
        total_success += s
        total_fail += f

    if not dry_run:
        console.print(Panel(
            f"全部补翻完成\n成功：{total_success} 章  失败：{total_fail} 章",
            border_style="green" if total_fail == 0 else "yellow",
        ))


@app.command("report")
def cmd_report(
    source: Path = typer.Option(Path("output/天机录/if/chapters"), "--source", "-s"),
    output: Path = typer.Option(Path("output/天机录/translations"), "--output", "-o"),
) -> None:
    """输出所有语言的完整健康报告。"""
    langs = [d.name for d in sorted(output.iterdir()) if d.is_dir() and not d.name.startswith(".") and d.name != "logs"]
    if not langs:
        console.print("[yellow]未找到任何翻译目录[/yellow]")
        return

    source_total = len(list(source.glob("ch*.json")))
    console.print(f"\n[bold]源章节总数：{source_total}[/bold]\n")

    for l in langs:
        with console.status(f"扫描 {l}..."):
            r = scan_lang(source, output, l)
        coverage = (source_total - len(r["missing"])) / source_total * 100
        status = "✅" if len(r["missing"]) == 0 and len(r["invalid"]) == 0 else "⚠️"
        console.print(
            f"{status} [cyan]{LANG_NAMES.get(l, l)}[/cyan]  "
            f"覆盖率 [green]{coverage:.1f}%[/green]  "
            f"缺失 [red]{len(r['missing'])}[/red]  "
            f"语言错误 [yellow]{len(r['invalid'])}[/yellow]  "
            f"正常 [green]{r['ok']}[/green]"
        )
        if r["missing"]:
            # 显示缺失章节范围
            ranges = []
            start = prev = r["missing"][0]
            for n in r["missing"][1:]:
                if n == prev + 1:
                    prev = n
                else:
                    ranges.append(f"{start}-{prev}" if start != prev else str(start))
                    start = prev = n
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            console.print(f"   缺失范围: {', '.join(ranges[:15])}" + (" ..." if len(ranges) > 15 else ""))


if __name__ == "__main__":
    app()

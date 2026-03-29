#!/usr/bin/env python3
"""
generate_if.py — 互动爽文小说生成器（独立脚本，无需数据库）

用法：
    python scripts/generate_if.py                          # 交互模式
    python scripts/generate_if.py --title 我的小说 --chapters 50
    python scripts/generate_if.py --resume --output ./output
    python scripts/generate_if.py --help

环境变量：
    ANTHROPIC_API_KEY   Anthropic API 密钥（必须，或通过 --api-key 传入）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 将 src/ 加入 Python 路径，无需安装包
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import typer
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    help="互动爽文小说生成器",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

GENRES = ["修仙升级", "都市逆袭", "悬疑生存", "职场商战", "末日爽文"]

PHASE_LABELS: dict[str, str] = {
    "story_bible":        "生成故事圣经",
    "act_plan":           "规划全书幕结构",
    "volume_plan":        "规划卷结构",
    "arc_plan":           "规划弧线章节",
    "chapter_gen":        "生成章节内容",
    "arc_summary":        "生成弧线总结",
    "world_snapshot":     "更新世界快照",
    "volume_summary":     "生成卷总结",
    "branch_plan":        "规划分支路线",
    "branch_chapter_gen": "生成分支章节",
    "walkthrough":        "生成攻略地图",
    "assembly":           "组装产物",
    "compile":            "编译分片文件",
}

PHASE_ICONS: dict[str, str] = {
    "story_bible":        "[cyan]  [/cyan]",
    "act_plan":           "[yellow]  [/yellow]",
    "volume_plan":        "[bold cyan]  [/bold cyan]",
    "arc_plan":           "[blue]  [/blue]",
    "chapter_gen":        "[green]  [/green]",
    "arc_summary":        "[magenta]  [/magenta]",
    "world_snapshot":     "[blue]  [/blue]",
    "volume_summary":     "[bold magenta]  [/bold magenta]",
    "branch_plan":        "[yellow]  [/yellow]",
    "branch_chapter_gen": "[yellow]  [/yellow]",
    "walkthrough":        "[cyan]  [/cyan]",
    "assembly":           "[green]  [/green]",
    "compile":            "[green]  [/green]",
}


# ---------------------------------------------------------------------------
# 轻量级 Project 替代（避免依赖 ORM）
# ---------------------------------------------------------------------------

@dataclass
class _Project:
    slug: str
    title: str
    metadata_json: dict = field(default_factory=dict)


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:40] or "novel"


# ---------------------------------------------------------------------------
# 增强版独立 Pipeline（无 DB，含 ActPlan / ArcPlanV2 / ArcSummary）
# ---------------------------------------------------------------------------

def _progress_path(output_dir: Path) -> Path:
    return output_dir / "if_progress.json"


def _load_state(output_dir: Path) -> dict[str, Any]:
    p = _progress_path(output_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_state(output_dir: Path, state: dict[str, Any]) -> None:
    # 防御过滤：chapters 数组不写入 state，章节单独存文件
    clean = {k: v for k, v in state.items() if k not in ("chapters_mainline", "chapters")}
    _progress_path(output_dir).write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 章节文件 I/O — 每章独立 JSON 文件
# ---------------------------------------------------------------------------

def _chapters_dir(output_dir: Path) -> Path:
    return output_dir / "chapters"


def _chapter_path(output_dir: Path, number: int) -> Path:
    return _chapters_dir(output_dir) / f"ch{number:04d}.json"


def _save_chapter(output_dir: Path, chapter: dict) -> None:
    """原子写单章文件（write tmp → rename，防崩溃时写出半个 JSON）。"""
    d = _chapters_dir(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    target = _chapter_path(output_dir, chapter["number"])
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def _chapter_exists(output_dir: Path, number: int) -> bool:
    return _chapter_path(output_dir, number).exists()


def _load_all_chapters(output_dir: Path) -> list[dict]:
    """扫描 chapters/ 目录，按 number 升序加载。跳过损坏文件。"""
    d = _chapters_dir(output_dir)
    if not d.exists():
        return []
    chapters = []
    for p in sorted(d.glob("ch*.json")):
        try:
            chapters.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    chapters.sort(key=lambda c: c.get("number", 0))
    return chapters


def _migrate_chapters_from_state(output_dir: Path, state: dict, key: str) -> None:
    """一次性迁移：将 state[key] 旧数组拆分为独立文件（幂等，不覆盖已有文件）。"""
    for ch in state.get(key, []):
        num = ch.get("number")
        if num is not None and not _chapter_exists(output_dir, num):
            _save_chapter(output_dir, ch)


def run_standalone_pipeline(
    project: _Project,
    cfg: Any,  # InteractiveFictionConfig
    output_base: Path,
    settings: Any,  # AppSettings
    resume: bool = False,
    on_progress: Any = None,
) -> Path:
    """
    完整独立 Pipeline（无数据库依赖）：

    1. Story Bible
    2. Act Plan（全书幕结构）
    3. Arc Plan v2（含 Act 上下文 + 上一弧总结）
    4. Chapter Generation（每弧完成后生成弧线总结 + 世界快照）
    5. Branch Phase（可选，根据 cfg.enable_branches）
    6. Walkthrough
    7. Assembly + Compile
    """
    from bestseller.services.if_act_planner import (
        get_open_clues_for_arc,
        run_act_plan_phase,
    )
    from bestseller.services.if_branch_engine import BranchEngine
    from bestseller.services.if_generation import (
        _LLMCaller,
        _compile_story_package,
        assemble_story_package,
        build_concept_json,
        generate_arc_summary,
        generate_world_snapshot,
        run_bible_phase,
        run_chapters_phase,
        run_single_arc_plan,
        run_volume_plan_phase,
        run_volume_summary_phase,
        run_walkthrough_phase,
    )

    def emit(phase: str, payload: dict | None = None) -> None:
        if on_progress:
            on_progress(phase, payload or {})

    book_id = project.slug.replace("-", "_")
    output_dir = output_base / project.slug / "if"
    output_dir.mkdir(parents=True, exist_ok=True)

    state = _load_state(output_dir) if resume else {}
    client = _LLMCaller(settings)
    concept = build_concept_json(cfg, project)  # type: ignore[arg-type]

    # ── Phase 1: Story Bible ─────────────────────────────────────────────────
    if "bible" not in state:
        emit("story_bible", {"status": "running"})
        bible = run_bible_phase(client, concept, cfg)
        state["bible"] = bible
        _save_state(output_dir, state)
        emit("story_bible", {"status": "done"})
    else:
        bible = state["bible"]
        emit("story_bible", {"status": "loaded"})

    # ── Phase 1.5: Act Plan ──────────────────────────────────────────────────
    if "act_plans" not in state:
        emit("act_plan", {"status": "running"})
        act_plans = run_act_plan_phase(client, bible, cfg, on_progress=emit)
        state["act_plans"] = act_plans
        _save_state(output_dir, state)
        emit("act_plan", {"status": "done", "acts": len(act_plans)})
    else:
        act_plans = state["act_plans"]
        emit("act_plan", {"status": "loaded", "acts": len(act_plans)})

    # ── Phase 2+3: Volume-driven generation ─────────────────────────────────
    # 卷 (volume) → 弧 (arc) → 章节 (chapter)
    # Each volume = cfg.volume_size chapters; each arc = cfg.arc_batch_size chapters.
    # When volume_size == 0 or target_chapters < 2 × volume_size, skip the volume layer.
    arc_size = cfg.arc_batch_size
    volume_size = cfg.volume_size if cfg.volume_size > 0 else cfg.target_chapters
    use_volumes = (cfg.volume_size > 0 and cfg.target_chapters >= volume_size)
    num_volumes = (cfg.target_chapters + volume_size - 1) // volume_size

    # Persistent state dicts (all keyed by str for JSON compat)
    volume_plans: dict[str, dict] = state.get("volume_plans", {})
    volume_summaries: dict[str, dict] = state.get("volume_summaries", {})
    arc_plans_dict: dict[str, list] = state.get("arc_plans_dict", {})
    arc_summaries_dict: dict[str, dict] = state.get("arc_summaries_dict", {})
    raw_snapshots: list = state.get("world_snapshots", [])
    world_snapshots: list[dict] = [
        s if isinstance(s, dict) else (
            {k: v for item in s for k, v in item.items()} if isinstance(s, list) else {}
        )
        for s in raw_snapshots
    ]

    _migrate_chapters_from_state(output_dir, state, "chapters_mainline")
    generated: list[dict] = _load_all_chapters(output_dir)

    # Build a flat arc_plans list for downstream phases (walkthrough / compile)
    # This is populated as arcs are planned.
    arc_plans: list[list[dict]] = []

    global_arc_index = 0
    total_chapters_target = cfg.target_chapters

    for vol_idx in range(num_volumes):
        vol_start = vol_idx * volume_size + 1
        vol_end = min((vol_idx + 1) * volume_size, total_chapters_target)
        vol_key = str(vol_idx)

        # ── Volume Plan ──────────────────────────────────────────────────
        if use_volumes:
            if vol_key not in volume_plans:
                emit("volume_plan", {"status": "running", "volume": vol_idx + 1, "total_volumes": num_volumes,
                                     "chapters": f"{vol_start}-{vol_end}"})
                prev_sums = [volume_summaries[str(i)] for i in range(vol_idx) if str(i) in volume_summaries]
                vol_plan = run_volume_plan_phase(
                    client, bible, act_plans, vol_idx, vol_start, vol_end, prev_sums, cfg
                )
                volume_plans[vol_key] = vol_plan
                state["volume_plans"] = volume_plans
                _save_state(output_dir, state)
                emit("volume_plan", {"status": "done", "volume": vol_idx + 1,
                                     "title": vol_plan.get("title", "")})
            else:
                vol_plan = volume_plans[vol_key]
                emit("volume_plan", {"status": "loaded", "volume": vol_idx + 1,
                                     "title": vol_plan.get("title", "")})
        else:
            vol_plan = {}

        # ── Arcs within this volume ───────────────────────────────────────
        arcs_in_vol = (vol_end - vol_start + 1 + arc_size - 1) // arc_size
        vol_arc_summaries: list[dict] = []

        for arc_in_vol in range(arcs_in_vol):
            arc_start_ch = vol_start + arc_in_vol * arc_size
            arc_end_ch = min(arc_start_ch + arc_size - 1, vol_end)
            arc_key = str(global_arc_index)

            # Arc Plan (on-demand, with volume context)
            if arc_key not in arc_plans_dict:
                emit("arc_plan", {"status": "running", "arc": global_arc_index + 1,
                                  "chapters": f"{arc_start_ch}-{arc_end_ch}"})
                prev_arc_summary = arc_summaries_dict.get(str(global_arc_index - 1)) if global_arc_index > 0 else None
                prev_arc_sums_list = [arc_summaries_dict[k] for k in sorted(arc_summaries_dict, key=int)
                                      if int(k) < global_arc_index]
                open_clues = get_open_clues_for_arc(act_plans, prev_arc_sums_list, arc_start_ch)
                arc_cards = run_single_arc_plan(
                    client, bible, act_plans,
                    vol_plan if use_volumes else None,
                    global_arc_index, arc_start_ch, arc_end_ch,
                    prev_arc_summary, open_clues, cfg,
                )
                arc_plans_dict[arc_key] = arc_cards
                state["arc_plans_dict"] = arc_plans_dict
                _save_state(output_dir, state)
                emit("arc_plan", {"status": "done", "arc": global_arc_index + 1})
            arc_cards = arc_plans_dict[arc_key]
            arc_plans.append(arc_cards)

            # Chapter Generation for this arc
            arc_done = all(_chapter_exists(output_dir, c["number"]) for c in arc_cards)
            if not arc_done:
                existing_in_arc = [g for g in generated
                                   if arc_start_ch <= g.get("number", 0) <= arc_end_ch]
                emit("chapter_gen", {"status": "running", "arc": global_arc_index + 1,
                                     "done": len(generated), "total": total_chapters_target})

                def on_chapter(ch_num: int, _total: int, errs: list[str],
                               chapter: dict | None = None, _gen=generated) -> None:
                    if chapter is not None and ch_num not in {g["number"] for g in _gen}:
                        _save_chapter(output_dir, chapter)
                        _gen.append(chapter)
                    _save_state(output_dir, state)
                    emit("chapter_gen", {"chapter": ch_num, "total": total_chapters_target,
                                         "warnings": len(errs)})

                arc_generated = run_chapters_phase(
                    client=client, bible=bible, arc_plans=[arc_cards], cfg=cfg,
                    existing_chapters=existing_in_arc, on_chapter=on_chapter,
                )
                existing_nums = {g["number"] for g in generated}
                for ch in arc_generated:
                    if ch["number"] not in existing_nums:
                        _save_chapter(output_dir, ch)
                        generated.append(ch)
                _save_state(output_dir, state)
            else:
                arc_generated = [g for g in generated
                                 if arc_start_ch <= g.get("number", 0) <= arc_end_ch]

            # Arc Summary
            if arc_key not in arc_summaries_dict:
                emit("arc_summary", {"status": "running", "arc": global_arc_index + 1})
                prev_sums_list = [arc_summaries_dict[k] for k in sorted(arc_summaries_dict, key=int)
                                  if int(k) < global_arc_index]
                open_clues_for_sum = get_open_clues_for_arc(act_plans, prev_sums_list, arc_start_ch)
                arc_summary = generate_arc_summary(
                    client=client, bible=bible,
                    arc_chapters=arc_generated, arc_cards=arc_cards,
                    open_clues=open_clues_for_sum or None,
                )
                arc_summaries_dict[arc_key] = arc_summary
                state["arc_summaries_dict"] = arc_summaries_dict
                _save_state(output_dir, state)
                emit("arc_summary", {"status": "done", "arc": global_arc_index + 1})
            vol_arc_summaries.append(arc_summaries_dict[arc_key])

            # World Snapshot
            prev_snapshot = world_snapshots[-1] if world_snapshots else None
            emit("world_snapshot", {"status": "running", "arc": global_arc_index + 1})
            snapshot = generate_world_snapshot(
                client=client, bible=bible,
                arc_summary=arc_summaries_dict[arc_key],
                prev_snapshot=prev_snapshot,
            )
            world_snapshots.append(snapshot)
            state["world_snapshots"] = world_snapshots
            _save_state(output_dir, state)
            emit("world_snapshot", {"status": "done", "arc": global_arc_index + 1})

            global_arc_index += 1

        # ── Volume Summary ────────────────────────────────────────────────
        if use_volumes and vol_key not in volume_summaries:
            emit("volume_summary", {"status": "running", "volume": vol_idx + 1})
            vol_summary = run_volume_summary_phase(
                client, bible, vol_plan, vol_arc_summaries, vol_idx, vol_start, vol_end
            )
            volume_summaries[vol_key] = vol_summary
            state["volume_summaries"] = volume_summaries
            _save_state(output_dir, state)
            emit("volume_summary", {"status": "done", "volume": vol_idx + 1,
                                    "open_threads": len(vol_summary.get("open_threads", []))})

    emit("chapter_gen", {"status": "done", "total": len(generated)})

    # ── Phase 3.5: Branch Phase ──────────────────────────────────────────────
    route_definitions: list[dict] = [{"route_id": "mainline", "route_type": "mainline"}]

    if cfg.enable_branches and "branch_routes" not in state:
        emit("branch_plan", {"status": "running"})
        engine = BranchEngine()
        branch_defs = engine.plan_branches(act_plans, cfg, book_id)
        route_definitions = branch_defs
        state["route_definitions"] = branch_defs

        branch_routes: dict[str, list] = {}
        branch_dir = output_dir / "branches"

        for route_def in branch_defs:
            if route_def.get("route_type") == "mainline":
                continue

            route_id = route_def["route_id"]
            emit("branch_plan", {"status": "planning", "route_id": route_id})

            # Find fork snapshot (latest world snapshot before branch start)
            branch_start = route_def.get("branch_start_chapter", 1)
            fork_snapshot: dict = {}
            for snap in reversed(world_snapshots):
                fork_snapshot = snap
                break

            # Plan branch arc
            branch_cards = engine.generate_branch_arc_plan(
                client=client,
                bible=bible,
                route_def=route_def,
                fork_state_snapshot=fork_snapshot,
                cfg=cfg,
            )

            emit("branch_chapter_gen", {"status": "running", "route_id": route_id, "chapters": len(branch_cards)})

            def on_branch_chapter(ch_num: int, rid: str, errs: list[str]) -> None:
                emit("branch_chapter_gen", {"chapter": ch_num, "route_id": rid, "warnings": len(errs)})

            branch_chapters = engine.generate_branch_chapters(
                client=client,
                bible=bible,
                route_def=route_def,
                branch_cards=branch_cards,
                fork_state_snapshot=fork_snapshot,
                cfg=cfg,
                output_dir=output_dir,
                on_chapter=on_branch_chapter,
            )
            branch_routes[route_id] = [c["number"] for c in branch_chapters]

        state["branch_routes"] = branch_routes
        _save_state(output_dir, state)
        emit("branch_plan", {"status": "done", "routes": len(branch_routes)})

    elif "route_definitions" in state:
        route_definitions = state["route_definitions"]

    # ── Phase 4: Walkthrough ─────────────────────────────────────────────────
    if "walkthrough" not in state:
        emit("walkthrough", {"status": "running"})
        walkthrough = run_walkthrough_phase(client, bible, arc_plans, cfg)
        state["walkthrough"] = walkthrough
        _save_state(output_dir, state)
        emit("walkthrough", {"status": "done"})
    else:
        walkthrough = state["walkthrough"]
        emit("walkthrough", {"status": "loaded"})

    # ── Phase 5: Assembly ────────────────────────────────────────────────────
    emit("assembly", {"status": "running"})
    story_package = assemble_story_package(bible, generated, walkthrough, concept=concept)
    out_path = output_dir / "story_package.json"
    out_path.write_text(json.dumps(story_package, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("assembly", {"status": "done", "path": str(out_path)})

    # ── Phase 6: Compile ─────────────────────────────────────────────────────
    emit("compile", {"status": "running"})
    compiled_dir = output_dir / "build"
    compiled_dir.mkdir(exist_ok=True)
    _compile_story_package(
        story_package,
        book_id,
        compiled_dir,
        arc_plans=arc_plans,
        route_definitions=route_definitions,
    )
    emit("compile", {"status": "done", "dir": str(compiled_dir)})

    return out_path


# ---------------------------------------------------------------------------
# 进度显示
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Rich 进度面板，由 on_progress 回调驱动。"""

    def __init__(self, total_chapters: int) -> None:
        self.total_chapters = total_chapters
        self.log_lines: list[str] = []
        self.current_phase = "准备中..."
        self.chapter_done = 0
        self.arc_done = 0
        self.arc_total = 1
        self.vol_done = 0
        self.vol_total = 1
        self._start = time.time()

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=36),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            expand=False,
        )
        self.ch_task = self.progress.add_task("章节进度", total=total_chapters, visible=False)
        self.arc_task = self.progress.add_task("弧线规划", total=1, visible=False)
        self.vol_task = self.progress.add_task("卷规划", total=1, visible=False)

    def on_event(self, phase: str, payload: dict) -> None:
        label = PHASE_LABELS.get(phase, phase)
        icon = PHASE_ICONS.get(phase, "  ")
        status = payload.get("status", "")

        if phase == "chapter_gen":
            ch_num = payload.get("chapter", 0)
            total = payload.get("total", self.total_chapters)
            warns = payload.get("warnings", 0)
            if status == "running":
                self.progress.update(self.ch_task, description=label, total=total,
                                     completed=payload.get("done", 0), visible=True)
                self.progress.update(self.arc_task, visible=False)
                self.progress.update(self.vol_task, visible=False)
            elif ch_num:
                self.chapter_done = ch_num
                self.progress.update(self.ch_task, completed=ch_num)
                warn_tag = f" [yellow]⚠{warns}[/yellow]" if warns else ""
                self._log(f"[green]✓[/green] 第 {ch_num:>4} 章{warn_tag}")
            elif status == "done":
                self.progress.update(self.ch_task, completed=total)
                self._log(f"[green]✓ 全部 {total} 章生成完毕[/green]")

        elif phase == "arc_plan":
            arc_num = payload.get("arc", 0)
            chs = payload.get("chapters", "")
            if status == "running":
                self.progress.update(self.arc_task, description=label, visible=True)
                self.current_phase = f"{icon}{label}（第 {chs} 章）"
            elif status == "done":
                self.arc_done += 1
                self.progress.update(self.arc_task, completed=self.arc_done)
                self._log(f"[blue]✓[/blue] 弧线 {arc_num} 规划完成（第 {chs} 章）")

        elif phase == "volume_plan":
            vol_num = payload.get("volume", 0)
            total_vols = payload.get("total_volumes", self.vol_total)
            title = payload.get("title", "")
            if status == "running":
                self.vol_total = total_vols
                self.progress.update(self.vol_task, description=label, total=total_vols,
                                     completed=self.vol_done, visible=True)
                self.current_phase = f"{icon}规划第 {vol_num}/{total_vols} 卷"
            elif status in ("done", "loaded"):
                self.vol_done = vol_num
                self.progress.update(self.vol_task, completed=self.vol_done)
                tag = "[dim]（已缓存）[/dim]" if status == "loaded" else ""
                self._log(f"[bold cyan]✓[/bold cyan] 第 {vol_num} 卷规划：「{title}」{tag}")

        elif phase == "volume_summary":
            vol_num = payload.get("volume", 0)
            threads = payload.get("open_threads", 0)
            if status == "running":
                self.current_phase = f"{icon}生成第 {vol_num} 卷总结"
            elif status == "done":
                self._log(f"[bold magenta]✓[/bold magenta] 第 {vol_num} 卷总结完成（{threads} 条待续伏笔）")

        elif phase in ("arc_summary", "world_snapshot"):
            arc_num = payload.get("arc", "")
            if status == "running":
                self.current_phase = f"{icon}{label}（弧 {arc_num}）"
            elif status == "done":
                self._log(f"[magenta]✓[/magenta] {label}（弧 {arc_num}）完成")

        elif phase == "act_plan":
            if status == "running":
                self.current_phase = f"{icon}{label}"
            elif status == "done":
                acts = payload.get("acts", "")
                self._log(f"[yellow]✓[/yellow] 幕结构规划完成（{acts} 幕）")

        elif phase == "branch_plan":
            route_id = payload.get("route_id", "")
            if status == "running":
                self.current_phase = f"{icon}{label}"
            elif status == "planning":
                self._log(f"[yellow]  规划路线：{route_id}[/yellow]")
            elif status == "done":
                routes = payload.get("routes", "")
                self._log(f"[yellow]✓[/yellow] 分支规划完成（{routes} 条路线）")

        elif status == "running":
            self.current_phase = f"{icon}{label}"
        elif status in ("done", "loaded"):
            tag = "[dim]（缓存）[/dim]" if status == "loaded" else ""
            self._log(f"[green]✓[/green] {label} {tag}")

    def _log(self, msg: str) -> None:
        self.log_lines.append(msg)
        if len(self.log_lines) > 10:
            self.log_lines.pop(0)

    def render(self) -> Panel:
        elapsed = time.time() - self._start
        header = Text()
        header.append(f"  {self.current_phase}", style="bold cyan")
        header.append(f"  ({elapsed:.0f}s)", style="dim")

        log_text = Text()
        for line in self.log_lines:
            log_text.append_text(Text.from_markup(line + "\n"))

        from rich.console import Group
        content = Group(header, self.progress, log_text)
        return Panel(content, title="[bold]生成进度[/bold]", border_style="cyan", padding=(0, 1))


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

@app.command()
def generate(
    concept_file: str = typer.Option("", "--concept", help="concept JSON 文件路径（story-factory/concepts/xxx.json）"),
    title: str = typer.Option("", "--title", "-t", help="小说标题"),
    chapters: int = typer.Option(0, "--chapters", "-c", help="总章节数（10-2000）"),
    genre: str = typer.Option("", "--genre", "-g", help="修仙升级|都市逆袭|悬疑生存|职场商战|末日爽文"),
    premise: str = typer.Option("", "--premise", "-p", help="故事思路/前提"),
    protagonist: str = typer.Option("", "--protagonist", help="主角描述（可选）"),
    tone: str = typer.Option("爽快、热血、有悬念", "--tone", help="写作基调"),
    output_dir: Path = typer.Option(Path("./output"), "--output", "-o", help="输出根目录"),
    resume: bool = typer.Option(False, "--resume", "-r", help="从上次断点继续"),
    api_key: str = typer.Option("", "--api-key", help="Anthropic API Key"),
    enable_branches: bool = typer.Option(False, "--branches", help="启用硬分支路线"),
    branch_count: int = typer.Option(2, "--branch-count", help="分支路线数量"),
    free_chapters: int = typer.Option(20, "--free-chapters", help="免费章节数"),
    planner_model: str = typer.Option("", "--planner-model", help="规划模型 ID（覆盖配置文件）"),
    writer_model: str = typer.Option("", "--writer-model", help="写作模型 ID（覆盖配置文件）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认提示，直接开始生成（用于自动化脚本）"),
) -> None:
    """互动爽文小说生成器。不带参数时进入交互模式。"""

    # ── 从 concept JSON 文件加载参数（命令行参数优先级更高）────────────────────
    _concept_data: dict[str, Any] = {}
    if concept_file:
        cp = Path(concept_file)
        if not cp.exists():
            # 尝试相对于项目根目录查找
            root = Path(__file__).parent.parent
            cp = root / concept_file
        if cp.exists():
            _concept_data = json.loads(cp.read_text(encoding="utf-8"))
            console.print(f"[dim]已加载 concept 文件：{cp}[/dim]")
        else:
            console.print(f"[red]concept 文件未找到：{concept_file}[/red]")
            raise typer.Exit(1)

    def _c(key: str, default: Any = "") -> Any:
        """从 concept_data 取值，命令行已明确传入时跳过。"""
        return _concept_data.get(key, default)

    # concept JSON → 命令行默认值（CLI 明确传入时优先）
    if not title and _c("title"):
        title = str(_c("title"))
    if (not genre or genre not in GENRES) and _c("genre") in GENRES:
        genre = str(_c("genre"))
    if chapters <= 0 and _c("target_chapters"):
        chapters = int(_c("target_chapters"))
    if not premise and _c("premise"):
        premise = str(_c("premise"))
    if not protagonist and _c("protagonist"):
        protagonist = str(_c("protagonist"))
    if tone == "爽快、热血、有悬念" and _c("tone"):
        tone = str(_c("tone"))
    if free_chapters == 20 and _c("free_chapters"):
        free_chapters = int(_c("free_chapters"))
    if not enable_branches and _c("writing_settings", {}).get("enable_branches"):
        enable_branches = bool(_c("writing_settings", {})["enable_branches"])
    if branch_count == 2 and _c("writing_settings", {}).get("branch_count"):
        branch_count = int(_c("writing_settings", {})["branch_count"])

    # ── API Key ──────────────────────────────────────────────────────────────
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        console.print(Panel(
            "[red]未找到 API Key。\n"
            "请设置环境变量 [bold]ANTHROPIC_API_KEY[/bold]，"
            "或使用 [bold]--api-key[/bold] 参数传入。[/red]",
            title="[red]错误[/red]", border_style="red",
        ))
        raise typer.Exit(1)

    # ── Banner ───────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        "[bold cyan]互动爽文小说生成器[/bold cyan]\n"
        "[dim]基于 LifeScript IF 系统 · 支持1000章 · 真硬分支[/dim]",
        border_style="cyan",
    ))

    # ── 交互补全缺失参数 ─────────────────────────────────────────────────────
    if not title:
        title = typer.prompt("\n小说标题")

    if not genre or genre not in GENRES:
        console.print("\n[bold]选择类型：[/bold]")
        for i, g in enumerate(GENRES, 1):
            console.print(f"  [cyan]{i}[/cyan]. {g}")
        while True:
            raw = typer.prompt("序号", default="1")
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(GENRES):
                    genre = GENRES[idx]
                    break
            except ValueError:
                pass
            console.print("[red]请输入有效序号[/red]")

    if chapters <= 0:
        raw = typer.prompt("\n总章节数", default="50")
        try:
            chapters = max(10, min(2000, int(raw)))
        except ValueError:
            chapters = 50

    if not premise:
        console.print(
            "\n[dim]请描述故事思路（主角背景、核心冲突、世界观等）：[/dim]"
        )
        premise = typer.prompt("故事思路")

    if not protagonist:
        protagonist = typer.prompt(
            "\n主角描述（可选，直接回车跳过）", default="", show_default=False
        )

    # ── 确认配置 ─────────────────────────────────────────────────────────────
    console.print()
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_row("[dim]标题[/dim]", f"[bold]{title}[/bold]")
    tbl.add_row("[dim]类型[/dim]", genre)
    tbl.add_row("[dim]章节数[/dim]", f"[bold]{chapters}[/bold] 章")
    tbl.add_row("[dim]免费章节[/dim]", f"{min(free_chapters, chapters // 2)} 章")
    tbl.add_row("[dim]基调[/dim]", tone)
    snippet = premise[:80] + ("…" if len(premise) > 80 else "")
    tbl.add_row("[dim]故事思路[/dim]", snippet)
    if protagonist:
        tbl.add_row("[dim]主角[/dim]", protagonist)
    tbl.add_row("[dim]硬分支[/dim]", f"启用（{branch_count} 条）" if enable_branches else "关闭")
    tbl.add_row("[dim]断点续传[/dim]", "续写" if resume else "全新生成")
    tbl.add_row("[dim]输出目录[/dim]", str(output_dir))
    console.print(Panel(tbl, title="[bold]生成配置[/bold]", border_style="blue"))

    if not yes and not typer.confirm("\n确认开始生成？"):
        raise typer.Exit(0)

    # ── 构建配置对象 ──────────────────────────────────────────────────────────
    from bestseller.domain.project import (
        IFCharacterDraft,
        IFStatConfig,
        InteractiveFictionConfig,
    )
    from bestseller.settings import load_settings

    # 从 concept JSON 的 writing_settings 获取生成参数
    ws = _concept_data.get("writing_settings", {})

    # 从 concept JSON 的 key_characters 构建角色列表
    _raw_chars = _concept_data.get("key_characters", [])
    _char_drafts: list[IFCharacterDraft] = []
    _ROLE_MAP = {
        "红颜": "红颜", "宿敌": "宿敌", "盟友": "盟友",
        "师尊": "师尊", "家族": "家族", "中立": "中立", "反派": "反派",
    }
    for ch in _raw_chars:
        if isinstance(ch, dict) and ch.get("name") and ch.get("role") in _ROLE_MAP:
            try:
                _char_drafts.append(IFCharacterDraft(**ch))
            except Exception:
                pass  # 跳过不合法的角色条目

    # 从 concept JSON 的 initial_stats 构建属性
    _raw_stats = _concept_data.get("initial_stats", {})
    _init_stats = IFStatConfig(**_raw_stats) if _raw_stats else IFStatConfig()

    cfg = InteractiveFictionConfig(
        enabled=True,
        if_genre=genre,
        target_chapters=chapters,
        free_chapters=min(free_chapters, chapters // 2),
        premise=premise,
        protagonist=protagonist or "",
        core_conflict=str(_concept_data.get("core_conflict", "")),
        tone=tone,
        arc_structure=_concept_data.get("arc_structure", []),
        key_characters=_char_drafts,
        initial_stats=_init_stats,
        # writing_settings 覆盖
        chapter_text_length=ws.get("chapter_text_length", "2500-3500"),
        choice_nodes_per_chapter=ws.get("choice_nodes_per_chapter", "2-3"),
        text_node_length=ws.get("text_node_length", "150-250"),
        arc_batch_size=int(ws.get("arc_batch_size", 15)),
        volume_size=int(ws.get("volume_size", 100)),
        act_count=int(ws.get("act_count", 5)),
        enable_branches=enable_branches,
        branch_count=branch_count if enable_branches else 0,
        branch_chapter_span=int(ws.get("branch_chapter_span", 30)),
        power_moment_interval=int(ws.get("power_moment_interval", 5)),
        context_mode=ws.get("context_mode", "tiered"),  # type: ignore[arg-type]
    )
    project = _Project(
        slug=_slugify(title),
        title=title,
        metadata_json={"endings": _concept_data["endings"]} if "endings" in _concept_data else {},
    )
    settings = load_settings()

    if planner_model:
        settings.llm.planner.model = planner_model
    if writer_model:
        settings.llm.writer.model = writer_model

    # ── 进度面板 ──────────────────────────────────────────────────────────────
    tracker = ProgressTracker(total_chapters=chapters)

    console.print()
    start_time = time.time()
    out_path: Path | None = None
    err: Exception | None = None

    with Live(tracker.render(), console=console, refresh_per_second=4) as live:

        def on_progress(phase: str, payload: dict) -> None:
            tracker.on_event(phase, payload)
            live.update(tracker.render())

        try:
            out_path = run_standalone_pipeline(
                project=project,
                cfg=cfg,
                output_base=output_dir,
                settings=settings,
                resume=resume,
                on_progress=on_progress,
            )
        except Exception as exc:
            err = exc
            tracker._log(f"[red]错误：{exc}[/red]")
            live.update(tracker.render())
            time.sleep(0.5)

    elapsed = time.time() - start_time

    # ── 结果展示 ──────────────────────────────────────────────────────────────
    console.print()

    if err:
        err_msg = str(err)
        if "AuthenticationError" in type(err).__name__ or "authentication_error" in err_msg or "invalid x-api-key" in err_msg or "401" in err_msg:
            console.print(Panel(
                "[red]API Key 无效或未设置。\n\n"
                "请通过以下任一方式提供有效的 Key：\n"
                "  1. 环境变量：[bold]export ANTHROPIC_API_KEY=sk-ant-xxxx[/bold]\n"
                "  2. 项目根目录 [bold].env[/bold] 文件：[bold]ANTHROPIC_API_KEY=sk-ant-xxxx[/bold]\n"
                "  3. 参数：[bold]--api-key sk-ant-xxxx[/bold][/red]",
                title="[red]认证失败[/red]", border_style="red",
            ))
        else:
            console.print(Panel(
                f"[red]{err}[/red]",
                title="[red]生成失败[/red]", border_style="red",
            ))
            console.print(
                "[dim]提示：使用 [bold]--resume[/bold] 参数可从断点继续生成。[/dim]"
            )
            # 仅在 DEBUG 模式下显示完整 traceback
            if os.environ.get("DEBUG"):
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)

    # 统计产物
    build_dir = output_dir / project.slug / "if" / "build"
    artifact_files = sorted(build_dir.rglob("*.json")) if build_dir.exists() else []
    total_size_kb = sum(f.stat().st_size for f in artifact_files) / 1024

    res_tbl = Table(show_header=False, box=None, padding=(0, 2))
    res_tbl.add_row("[dim]标题[/dim]", f"[bold]{title}[/bold]")
    res_tbl.add_row("[dim]章节数[/dim]", f"[green]{chapters} 章[/green]")
    res_tbl.add_row("[dim]耗时[/dim]", f"{elapsed / 60:.1f} 分钟")
    res_tbl.add_row("[dim]产物大小[/dim]", f"{total_size_kb:.0f} KB（{len(artifact_files)} 个文件）")
    res_tbl.add_row("[dim]主产物[/dim]", f"[cyan]{out_path}[/cyan]")
    res_tbl.add_row("[dim]分片目录[/dim]", f"[cyan]{build_dir}[/cyan]")
    if enable_branches:
        branches_dir = output_dir / project.slug / "if" / "branches"
        res_tbl.add_row("[dim]分支目录[/dim]", f"[cyan]{branches_dir}[/cyan]")

    console.print(Panel(res_tbl, title="[bold green]生成完成[/bold green]", border_style="green"))

    if artifact_files:
        console.print(Rule("[dim]产物文件[/dim]"))
        for f in artifact_files[:20]:
            size_kb = f.stat().st_size / 1024
            rel = f.relative_to(output_dir)
            console.print(f"  [dim]{rel}[/dim]  [cyan]{size_kb:.0f} KB[/cyan]")
        if len(artifact_files) > 20:
            console.print(f"  [dim]... 共 {len(artifact_files)} 个文件[/dim]")


# ---------------------------------------------------------------------------
# 快速测试命令（--test 模式，只生成10章验证流程）
# ---------------------------------------------------------------------------

@app.command(name="test")
def quick_test(
    api_key: str = typer.Option("", "--api-key", help="Anthropic API Key"),
    output_dir: Path = typer.Option(Path("./output"), "--output", "-o"),
    resume: bool = typer.Option(False, "--resume", help="从上次断点继续（保留已生成章节）"),
) -> None:
    """快速测试：生成10章验证完整流程是否正常。"""
    label = "快速测试模式（10章）· 断点续传" if resume else "快速测试模式（10章）"
    console.print(Panel.fit(f"[bold yellow]{label}[/bold yellow]", border_style="yellow"))

    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        console.print(Panel(
            "[red]未找到 API Key。\n\n"
            "请通过以下任一方式提供：\n"
            "  1. 环境变量：[bold]export ANTHROPIC_API_KEY=sk-ant-xxxx[/bold]\n"
            "  2. 项目根目录 [bold].env[/bold] 文件：[bold]ANTHROPIC_API_KEY=sk-ant-xxxx[/bold]\n"
            "  3. 参数：[bold]./scripts/novel.sh test --api-key sk-ant-xxxx[/bold][/red]",
            title="[red]缺少 API Key[/red]", border_style="red",
        ))
        raise typer.Exit(1)

    from bestseller.domain.project import InteractiveFictionConfig
    from bestseller.settings import load_settings

    cfg = InteractiveFictionConfig(
        enabled=True,
        if_genre="修仙升级",
        target_chapters=10,
        free_chapters=5,
        premise="一个普通少年在宗门测灵时被嘲笑为废柴，意外获得上古传承，开始逆袭之路。",
        protagonist="陈凡，18岁，外表普通，内心坚韧，记仇不莽，善于观察",
        tone="爽快、热血",
        enable_branches=False,
    )
    project = _Project(slug="test-novel-10ch", title="测试小说")
    settings = load_settings()

    tracker = ProgressTracker(total_chapters=10)
    start = time.time()
    err: Exception | None = None
    out_path: Path | None = None

    with Live(tracker.render(), console=console, refresh_per_second=4) as live:
        def on_progress(phase: str, payload: dict) -> None:
            tracker.on_event(phase, payload)
            live.update(tracker.render())

        try:
            out_path = run_standalone_pipeline(
                project=project,
                cfg=cfg,
                output_base=output_dir,
                settings=settings,
                resume=resume,
                on_progress=on_progress,
            )
        except Exception as exc:
            err = exc
            tracker._log(f"[red]错误：{exc}[/red]")
            live.update(tracker.render())
            time.sleep(0.5)

    elapsed = time.time() - start

    if err:
        err_msg = str(err)
        if "AuthenticationError" in type(err).__name__ or "authentication_error" in err_msg or "invalid x-api-key" in err_msg or "401" in err_msg:
            console.print(Panel(
                "[red]API Key 无效。请检查 ANTHROPIC_API_KEY 是否正确。[/red]",
                title="[red]认证失败[/red]", border_style="red",
            ))
        else:
            console.print(Panel(f"[red]{err}[/red]", title="[red]测试失败[/red]", border_style="red"))
            if os.environ.get("DEBUG"):
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green]测试完成[/green]，耗时 {elapsed:.0f}s")
    console.print(f"产物：[cyan]{out_path}[/cyan]")


if __name__ == "__main__":
    app()

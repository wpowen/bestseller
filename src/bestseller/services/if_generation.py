from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import importlib

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bestseller.domain.enums import ArtifactType, IFGenerationPhase
from bestseller.domain.project import InteractiveFictionConfig
from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    IFActPlanModel,
    IFGenerationRunModel,
    IFRouteDefinitionModel,
    PlanningArtifactVersionModel,
    PlotArcModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.services.if_prompts import (
    arc_plan_prompt,
    arc_plan_prompt_v2,
    arc_summary_prompt,
    bible_prompt,
    branch_arc_plan_prompt,
    chapter_prompt,
    validate_chapter,
    walkthrough_prompt,
    world_snapshot_prompt,
)
from bestseller.settings import AppSettings, get_runtime_env_value


# ---------------------------------------------------------------------------
# LLM caller — wraps the bestseller LLM gateway (litellm, sync)
# ---------------------------------------------------------------------------

class _LLMCaller:
    """Thin synchronous wrapper around litellm using bestseller AppSettings."""

    def __init__(self, settings: AppSettings) -> None:
        self._planner = settings.llm.planner
        self._writer = settings.llm.writer

    # Retryable exception types (network / transient errors)
    _RETRYABLE_EXCS: tuple[str, ...] = (
        "Timeout",
        "ConnectionError",
        "APIError",
        "ServiceUnavailable",
        "RateLimitError",
        "APIResponseValidationError",
    )

    def _call(
        self,
        role_settings: Any,
        prompt: str,
        max_tokens: int,
        timeout: int = 300,
        max_attempts: int = 8,
    ) -> str:
        """
        Call the LLM with retry logic for transient errors.

        Raises:
            RuntimeError: After all retry attempts are exhausted, with details.
        """
        import time as _time

        litellm = importlib.import_module("litellm")
        api_key = (
            get_runtime_env_value(role_settings.api_key_env)
            if role_settings.api_key_env
            else None
        ) or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")

        kwargs: dict[str, Any] = {
            "model": role_settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": role_settings.temperature,
            "timeout": timeout,
        }
        if role_settings.api_base:
            kwargs["api_base"] = role_settings.api_base
        if api_key:
            kwargs["api_key"] = api_key

        last_exc: Exception | None = None
        null_reason: str | None = None

        for attempt in range(max_attempts):
            try:
                response = litellm.completion(**kwargs)
                content = response.choices[0].message.content

                # Handle None / empty responses — treat as retryable (can be a transient API hiccup)
                if content is None:
                    null_reason = f"null content (model={role_settings.model})"
                else:
                    text = str(content).strip()
                    if not text or text == "None":
                        null_reason = (
                            f"empty content (model={role_settings.model}, "
                            f"prompt_len={len(prompt)}, max_tokens={max_tokens})"
                        )
                    else:
                        return text

                if attempt < max_attempts - 1:
                    wait = min(10 * (2 ** attempt), 120)  # 10s→20s→40s→80s→120s cap
                    _time.sleep(wait)
                    continue

                raise RuntimeError(
                    f"LLM returned {null_reason} after {max_attempts} attempts. "
                    f"This may indicate content filtering or a server-side issue."
                )

            except RuntimeError:
                raise

            except Exception as exc:
                exc_name = type(exc).__name__
                is_retryable = any(
                    tag in exc_name for tag in self._RETRYABLE_EXCS
                ) or "Exception" in exc_name and (
                    "timeout" in str(exc).lower()
                    or "connection" in str(exc).lower()
                    or "rate limit" in str(exc).lower()
                )

                last_exc = exc
                if not is_retryable or attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"[Attempt {attempt + 1}/{max_attempts}] LLM call failed (non-retryable): "
                        f"{exc_name}: {exc}",
                    ) from exc

                # Backoff: 10s→20s→40s→80s→120s (capped)
                wait = min(10 * (2 ** attempt), 120)
                _time.sleep(wait)

        # Should not reach here, but satisfy type checker
        raise RuntimeError(f"LLM call failed after {max_attempts} attempts") from last_exc

    def heavy(self, prompt: str, max_tokens: int = 8192) -> str:
        """Use planner model — story bible, arc plans, walkthrough."""
        return self._call(self._planner, prompt, max_tokens)

    def light(self, prompt: str, max_tokens: int = 8192) -> str:
        """Use writer model — chapter generation."""
        return self._call(self._writer, prompt, max_tokens)


def _parse_json(text: str) -> Any:
    import re as _re
    from json_repair import repair_json
    text = text.strip()
    # Strip <think>...</think> reasoning blocks (MiniMax-M2.7 and similar reasoning models)
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Strip opening fence; strip closing fence if present
        inner = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
        text = "\n".join(inner).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Attempt automatic repair (handles missing commas, trailing commas,
        # truncated JSON, single-quoted strings, etc.)
        repaired = repair_json(text, return_objects=True)
        if repaired is not None and repaired != "" and repaired != [] and repaired != {}:
            return repaired
        raise


# ---------------------------------------------------------------------------
# Progress file (checkpoint / resume)
# ---------------------------------------------------------------------------

def _progress_path(output_dir: Path) -> Path:
    return output_dir / "if_progress.json"


def _load_progress(output_dir: Path) -> dict[str, Any]:
    path = _progress_path(output_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_progress(output_dir: Path, state: dict[str, Any]) -> None:
    path = _progress_path(output_dir)
    # 防御过滤：chapters 数组不写入 state，章节单独存文件
    clean = {k: v for k, v in state.items() if k not in ("chapters", "chapters_mainline")}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


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


# ---------------------------------------------------------------------------
# Concept JSON builder
# ---------------------------------------------------------------------------

def build_concept_json(cfg: InteractiveFictionConfig, project: ProjectModel) -> dict[str, Any]:
    """Map InteractiveFictionConfig + ProjectModel fields → concept.json format."""
    book_id = project.slug.replace("-", "_")
    concept: dict[str, Any] = {
        "book_id": book_id,
        "title": project.title,
        "genre": cfg.if_genre,
        "target_chapters": cfg.target_chapters,
        "free_chapters": cfg.free_chapters,
        "premise": cfg.premise or project.metadata_json.get("premise", ""),
        "protagonist": cfg.protagonist,
        "core_conflict": cfg.core_conflict,
        "tone": cfg.tone,
    }
    if cfg.arc_structure:
        concept["arc_structure"] = cfg.arc_structure
    if cfg.key_characters:
        concept["key_characters"] = [
            {"name": c.name, "role": c.role, "description": c.description}
            for c in cfg.key_characters
        ]
    # Include pre-defined endings from concept file if provided via metadata
    if "endings" in project.metadata_json:
        concept["endings"] = project.metadata_json["endings"]
    return concept


# ---------------------------------------------------------------------------
# Phase runners (sync, called from async worker via thread)
# ---------------------------------------------------------------------------

def run_bible_phase(
    client: _LLMCaller,
    concept: dict[str, Any],
    cfg: InteractiveFictionConfig,
) -> dict[str, Any]:
    prompt = bible_prompt(concept, cfg)
    raw = client.heavy(prompt, max_tokens=8192)
    bible = _parse_json(raw)
    bible["book"]["total_chapters"] = cfg.target_chapters
    bible["book"]["free_chapters"] = cfg.free_chapters
    return bible


def run_arc_plan_phase(
    client: _LLMCaller,
    bible: dict[str, Any],
    cfg: InteractiveFictionConfig,
    on_arc: Any = None,
) -> list[list[dict[str, Any]]]:
    batch_size = cfg.arc_batch_size
    total_chapters = cfg.target_chapters
    arcs: list[list[dict[str, Any]]] = []
    arc_start = 1
    arc_index = 0
    total_arcs = (total_chapters + batch_size - 1) // batch_size

    while arc_start <= total_chapters:
        arc_end = min(arc_start + batch_size - 1, total_chapters)
        prompt = arc_plan_prompt(bible, arc_start, arc_end, arc_index, total_arcs, cfg)
        raw = client.heavy(prompt, max_tokens=16000)
        cards: list[dict[str, Any]] = _parse_json(raw)
        for i, card in enumerate(cards):
            card["number"] = arc_start + i
        arcs.append(cards)
        if on_arc is not None:
            on_arc(arc_index + 1, total_arcs, arc_start, arc_end)
        arc_start = arc_end + 1
        arc_index += 1
        time.sleep(0.5)

    return arcs


def run_arc_plan_phase_v2(
    client: _LLMCaller,
    bible: dict[str, Any],
    cfg: InteractiveFictionConfig,
    act_plans: list[dict[str, Any]],
    arc_summaries: dict[int, dict[str, Any]] | None = None,
    on_arc: Any = None,
) -> list[list[dict[str, Any]]]:
    """
    Enhanced arc planning that injects Act context and previous Arc summary.
    Improves long-range coherence for 1000+ chapter novels.
    """
    from bestseller.services.if_act_planner import find_act_for_chapter, get_open_clues_for_arc

    batch_size = cfg.arc_batch_size
    total_chapters = cfg.target_chapters
    arcs: list[list[dict[str, Any]]] = []
    arc_summaries = arc_summaries or {}
    arc_start = 1
    arc_index = 0
    total_arcs = (total_chapters + batch_size - 1) // batch_size

    while arc_start <= total_chapters:
        arc_end = min(arc_start + batch_size - 1, total_chapters)
        act_ctx = find_act_for_chapter(act_plans, arc_start) or {}
        prev_arc_summary = arc_summaries.get(arc_index - 1) if arc_index > 0 else None

        # Collect open clues from previous arc summaries
        all_arc_sums = [arc_summaries[i] for i in sorted(arc_summaries) if i < arc_index]
        open_clues = get_open_clues_for_arc(act_plans, all_arc_sums, arc_start)

        prompt = arc_plan_prompt_v2(
            bible=bible,
            act_context=act_ctx,
            arc_summary_prev=prev_arc_summary,
            arc_start=arc_start,
            arc_end=arc_end,
            arc_index=arc_index,
            total_arcs=total_arcs,
            cfg=cfg,
            open_clues=open_clues if open_clues else None,
        )
        raw = client.heavy(prompt, max_tokens=16000)
        cards: list[dict[str, Any]] = _parse_json(raw)
        for i, card in enumerate(cards):
            card["number"] = arc_start + i
        arcs.append(cards)
        if on_arc is not None:
            on_arc(arc_index + 1, total_arcs, arc_start, arc_end)
        arc_start = arc_end + 1
        arc_index += 1
        time.sleep(0.5)

    return arcs


def _ensure_summary_dict(parsed: Any, fallback_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    """将 _parse_json 结果归一化为 summary dict，防御 LLM 返回数组等异常格式。"""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        merged: dict[str, Any] = {}
        for item in parsed:
            if isinstance(item, dict):
                merged.update(item)
        if merged:
            return merged
    return {}


def generate_arc_summary(
    client: _LLMCaller,
    bible: dict[str, Any],
    arc_chapters: list[dict[str, Any]],
    arc_cards: list[dict[str, Any]],
    open_clues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate an arc-level summary after all arc chapters are written."""
    prompt = arc_summary_prompt(
        bible=bible,
        arc_chapters=arc_chapters,
        arc_cards=arc_cards,
        open_clues=open_clues,
    )
    for attempt in range(3):
        try:
            raw = client.heavy(prompt, max_tokens=4096)
            return _ensure_summary_dict(_parse_json(raw))
        except Exception as exc:
            if attempt == 2:
                return {
                    "protagonist_growth": "arc summary generation failed",
                    "relationship_changes": [],
                    "unresolved_threads": [],
                    "power_level_summary": "",
                    "next_arc_setup": "",
                    "open_clues": open_clues or [],
                    "resolved_clues": [],
                }
            time.sleep(5 * (attempt + 1))
    return {}


def _ensure_snapshot_dict(parsed: Any) -> dict[str, Any]:
    """将 _parse_json 结果归一化为 world snapshot dict，防御 LLM 返回数组等异常格式。"""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        merged: dict[str, Any] = {}
        for item in parsed:
            if isinstance(item, dict):
                merged.update(item)
        if "character_states" in merged or "faction_states" in merged:
            return merged
    return {"world_summary": "", "character_states": {}, "faction_states": {}}


def generate_world_snapshot(
    client: _LLMCaller,
    bible: dict[str, Any],
    arc_summary: dict[str, Any],
    prev_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Generate a world state snapshot after an arc summary."""
    if prev_snapshot is not None and not isinstance(prev_snapshot, dict):
        prev_snapshot = _ensure_snapshot_dict(prev_snapshot)

    prompt = world_snapshot_prompt(
        bible=bible,
        arc_summary=arc_summary,
        prev_snapshot=prev_snapshot,
    )
    for attempt in range(3):
        try:
            raw = client.heavy(prompt, max_tokens=4096)
            return _ensure_snapshot_dict(_parse_json(raw))
        except Exception as exc:
            if attempt == 2:
                return {"world_summary": "", "character_states": {}, "faction_states": {}}
            time.sleep(5 * (attempt + 1))
    return {}


def run_chapters_phase(
    client: _LLMCaller,
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
    cfg: InteractiveFictionConfig,
    existing_chapters: list[dict[str, Any]] | None = None,
    on_chapter: Any = None,
) -> list[dict[str, Any]]:
    book_id = bible["book"]["id"]
    all_cards: list[dict[str, Any]] = [card for arc in arc_plans for card in arc]
    generated: list[dict[str, Any]] = list(existing_chapters or [])
    done_count = len(generated)

    # Collect the hook from the last already-generated chapter (seed for the first remaining chapter)
    last_hook = generated[-1].get("next_chapter_hook", "") if generated else ""
    remaining_cards = all_cards[done_count:]
    batch_size = cfg.parallel_chapter_batch

    for i in range(0, len(remaining_cards), batch_size):
        batch = remaining_cards[i: i + batch_size]

        for card in batch:
            chapter: dict[str, Any] | None = None
            for _attempt in range(5):
                try:
                    # On later retries, add a small variation hint to avoid content filter
                    hint = f"\n(Attempt {_attempt + 1}: ensure the full JSON is complete and valid.)" if _attempt > 0 else ""
                    prompt = chapter_prompt(bible, card, last_hook, book_id, cfg) + hint
                    raw = client.light(prompt, max_tokens=12000)
                    if not raw or raw.strip() in ("None", "null", ""):
                        raise json.JSONDecodeError("Empty response from LLM", "", 0)
                    chapter = _parse_json(raw)
                    break
                except (json.JSONDecodeError, Exception) as exc:
                    exc_name = type(exc).__name__
                    is_retryable = isinstance(exc, json.JSONDecodeError) or any(
                        k in exc_name for k in ("Timeout", "Connection", "APIError", "ServiceUnavailable")
                    )
                    if _attempt == 4 or not is_retryable:
                        raise
                    time.sleep(5 * (_attempt + 1))
            errs = validate_chapter(chapter, book_id)  # type: ignore[arg-type]
            last_hook = chapter.get("next_chapter_hook", "")
            generated.append(chapter)
            if on_chapter is not None:
                on_chapter(card["number"], cfg.target_chapters, errs, chapter)
            time.sleep(0.2)

    return generated


def run_walkthrough_phase(
    client: _LLMCaller,
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
    cfg: InteractiveFictionConfig,
) -> dict[str, Any]:
    prompt = walkthrough_prompt(bible, arc_plans, cfg)
    raw = client.heavy(prompt, max_tokens=8000)
    return _parse_json(raw)


# ---------------------------------------------------------------------------
# Volume-level runner functions
# ---------------------------------------------------------------------------

def run_volume_plan_phase(
    client: _LLMCaller,
    bible: dict[str, Any],
    act_plans: list[dict[str, Any]],
    volume_index: int,
    chapter_start: int,
    chapter_end: int,
    prev_volume_summaries: list[dict[str, Any]],
    cfg: "InteractiveFictionConfig",
) -> dict[str, Any]:
    """Plan one volume (卷), chapters chapter_start–chapter_end."""
    from bestseller.services.if_prompts import volume_plan_prompt
    prompt = volume_plan_prompt(
        bible, act_plans, volume_index, chapter_start, chapter_end, prev_volume_summaries, cfg
    )
    raw = client.heavy(prompt, max_tokens=8192)
    return _parse_json(raw)


def run_volume_summary_phase(
    client: _LLMCaller,
    bible: dict[str, Any],
    volume_plan: dict[str, Any],
    arc_summaries: list[dict[str, Any]],
    volume_index: int,
    chapter_start: int,
    chapter_end: int,
) -> dict[str, Any]:
    """Summarise a completed volume into a handoff document."""
    from bestseller.services.if_prompts import volume_summary_prompt
    prompt = volume_summary_prompt(
        bible, volume_plan, arc_summaries, volume_index, chapter_start, chapter_end
    )
    raw = client.heavy(prompt, max_tokens=4096)
    return _parse_json(raw)


def run_single_arc_plan(
    client: _LLMCaller,
    bible: dict[str, Any],
    act_plans: list[dict[str, Any]],
    volume_plan: dict[str, Any] | None,
    global_arc_index: int,
    arc_start: int,
    arc_end: int,
    prev_arc_summary: dict[str, Any] | None,
    open_clues: list[dict[str, Any]] | None,
    cfg: "InteractiveFictionConfig",
) -> list[dict[str, Any]]:
    """Plan a single arc with optional volume context."""
    from bestseller.services.if_act_planner import find_act_for_chapter
    from bestseller.services.if_prompts import arc_plan_prompt_v2

    act_ctx = find_act_for_chapter(act_plans, arc_start) or {}
    total_arcs = (cfg.target_chapters + cfg.arc_batch_size - 1) // cfg.arc_batch_size

    prompt = arc_plan_prompt_v2(
        bible=bible,
        act_context=act_ctx,
        arc_summary_prev=prev_arc_summary,
        open_clues=open_clues or [],
        arc_start=arc_start,
        arc_end=arc_end,
        arc_index=global_arc_index,
        total_arcs=total_arcs,
        cfg=cfg,
        volume_plan=volume_plan,
    )
    raw = client.heavy(prompt, max_tokens=16000)
    cards: list[dict[str, Any]] = _parse_json(raw)
    for i, card in enumerate(cards):
        card["number"] = arc_start + i
    return cards


_STAT_EN_TO_CN = {
    "combat": "战力",
    "fame": "名望",
    "strategy": "谋略",
    "wealth": "财富",
    "charm": "魅力",
    "darkness": "黑化值",
    "destiny": "天命值",
}


def _derive_endings(route_graph: dict[str, Any], total_chapters: int) -> list[dict[str, Any]]:
    """Derive ending definitions from route_graph without an extra LLM call."""
    endings: list[dict[str, Any]] = []

    # Main ending from mainline
    endings.append({
        "id": "ending_main",
        "title": "主线结局",
        "type": "main",
        "chapter_hint": total_chapters,
        "condition_summary": "沿主线推进，均衡发展",
        "stat_conditions": [],
        "flag_conditions": [],
        "teaser": "历经重重考验，终于走到故事的终点……",
        "is_revealed": False,
    })

    # Hidden routes → hidden endings
    hidden_routes = route_graph.get("hidden_routes", [])
    stat_keywords = {
        "黑暗": ("黑化值", "min", 60),
        "策略": ("谋略", "min", 70),
        "魅力": ("魅力", "min", 70),
        "战力": ("战力", "min", 70),
        "名望": ("名望", "min", 70),
    }
    for i, route in enumerate(hidden_routes):
        route_text = route if isinstance(route, str) else route.get("name", str(route))
        # detect stat condition from route description
        stat_conds: list[dict[str, Any]] = []
        for kw, (stat, bound, val) in stat_keywords.items():
            if kw in route_text:
                stat_conds.append({"stat": stat, bound: val})
                break
        slug = f"ending_hidden_{i + 1}"
        endings.append({
            "id": slug,
            "title": f"隐藏结局 {i + 1}",
            "type": "hidden",
            "chapter_hint": total_chapters,
            "condition_summary": route_text[:40],
            "stat_conditions": stat_conds,
            "flag_conditions": [],
            "teaser": "",
            "is_revealed": False,
        })

    # Bad ending
    endings.append({
        "id": "ending_bad",
        "title": "落败结局",
        "type": "bad",
        "chapter_hint": total_chapters // 2 or total_chapters,
        "condition_summary": "名望过低，被世界遗弃",
        "stat_conditions": [{"stat": "名望", "max": 10}],
        "flag_conditions": [],
        "teaser": "",
        "is_revealed": False,
    })

    return endings


def assemble_story_package(
    bible: dict[str, Any],
    chapters: list[dict[str, Any]],
    walkthrough: dict[str, Any],
    concept: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chapters_sorted = sorted(chapters, key=lambda c: c["number"])
    bible["book"]["total_chapters"] = len(chapters_sorted)

    # Ensure initial_stats has Chinese-keyed version for the reader
    raw_stats = bible["book"].get("initial_stats", {})
    bible["book"]["initial_stats_cn"] = {
        _STAT_EN_TO_CN[k]: v
        for k, v in raw_stats.items()
        if k in _STAT_EN_TO_CN
    }

    route_graph = bible.get("route_graph", {})

    # Prefer concept-defined endings (richer descriptions) over auto-derived ones
    if concept and concept.get("endings"):
        endings = concept["endings"]
    else:
        endings = _derive_endings(route_graph, len(chapters_sorted))

    return {
        "book": bible["book"],
        "reader_desire_map": bible.get("reader_desire_map", {}),
        "story_bible": bible.get("story_bible", {}),
        "route_graph": route_graph,
        "walkthrough": walkthrough,
        "chapters": chapters_sorted,
        "endings": endings,
    }


# ---------------------------------------------------------------------------
# Full pipeline (synchronous, runs in background thread)
# ---------------------------------------------------------------------------

def run_if_pipeline(
    project: ProjectModel,
    cfg: InteractiveFictionConfig,
    output_base: Path,
    settings: AppSettings | None = None,
    resume: bool = False,
    on_progress: Any = None,
) -> Path:
    """
    Execute the full IF generation pipeline.
    Returns the path to the written story_package.json.
    Calls on_progress(phase, payload) if provided.
    """
    book_id = project.slug.replace("-", "_")
    output_dir = output_base / project.slug / "if"
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(phase: str, payload: dict[str, Any] | None = None) -> None:
        if on_progress is not None:
            on_progress(phase, payload or {})

    state = _load_progress(output_dir) if resume else {}
    if settings is None:
        from bestseller.settings import load_settings
        settings = load_settings()
    client = _LLMCaller(settings)
    concept = build_concept_json(cfg, project)

    # --- Phase 1: Story Bible ---
    if "bible" not in state:
        emit("story_bible", {"status": "running"})
        bible = run_bible_phase(client, concept, cfg)
        state["bible"] = bible
        _save_progress(output_dir, state)
        emit("story_bible", {"status": "done"})
    else:
        emit("story_bible", {"status": "loaded"})
        bible = state["bible"]

    # --- Phase 2: Arc Plans ---
    if "arc_plans" not in state:
        emit("arc_plan", {"status": "running", "total_arcs": (cfg.target_chapters + cfg.arc_batch_size - 1) // cfg.arc_batch_size})

        def on_arc(arc_num: int, total: int, start: int, end: int) -> None:
            emit("arc_plan", {"arc": arc_num, "total": total, "chapters": f"{start}-{end}"})

        arc_plans = run_arc_plan_phase(client, bible, cfg, on_arc=on_arc)
        state["arc_plans"] = arc_plans
        _save_progress(output_dir, state)
        emit("arc_plan", {"status": "done", "arcs": len(arc_plans)})
    else:
        arc_plans = state["arc_plans"]
        emit("arc_plan", {"status": "loaded", "arcs": len(arc_plans)})

    # --- Phase 3: Chapter Generation ---
    _migrate_chapters_from_state(output_dir, state, "chapters")
    generated: list[dict[str, Any]] = _load_all_chapters(output_dir)
    all_cards_count = sum(len(arc) for arc in arc_plans)

    if len(generated) < all_cards_count:
        emit("chapter_gen", {"status": "running", "done": len(generated), "total": all_cards_count})

        def on_chapter(ch_num: int, total: int, errs: list[str], chapter: dict | None = None) -> None:
            if chapter is not None and ch_num not in {g["number"] for g in generated}:
                _save_chapter(output_dir, chapter)
                generated.append(chapter)
            _save_progress(output_dir, state)
            emit("chapter_gen", {"chapter": ch_num, "total": total, "warnings": len(errs)})

        generated = run_chapters_phase(client, bible, arc_plans, cfg, existing_chapters=generated, on_chapter=on_chapter)
        # Ensure any chapters returned but not yet persisted are saved
        for ch in generated:
            if not _chapter_exists(output_dir, ch["number"]):
                _save_chapter(output_dir, ch)
        _save_progress(output_dir, state)
        emit("chapter_gen", {"status": "done", "total": len(generated)})
    else:
        emit("chapter_gen", {"status": "loaded", "total": len(generated)})

    # --- Phase 4: Walkthrough ---
    if "walkthrough" not in state:
        emit("walkthrough", {"status": "running"})
        walkthrough = run_walkthrough_phase(client, bible, arc_plans, cfg)
        state["walkthrough"] = walkthrough
        _save_progress(output_dir, state)
        emit("walkthrough", {"status": "done"})
    else:
        walkthrough = state["walkthrough"]
        emit("walkthrough", {"status": "loaded"})

    # --- Phase 5: Assembly ---
    emit("assembly", {"status": "running"})
    story_package = assemble_story_package(bible, generated, walkthrough)
    out_path = output_dir / "story_package.json"
    out_path.write_text(json.dumps(story_package, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("assembly", {"status": "done", "path": str(out_path)})

    # --- Phase 6: Compile (arc-split artifact files) ---
    emit("compile", {"status": "running"})
    compiled_dir = output_dir / "build"
    compiled_dir.mkdir(exist_ok=True)
    _compile_story_package(story_package, book_id, compiled_dir, arc_plans=arc_plans)
    emit("compile", {"status": "done", "dir": str(compiled_dir)})

    return out_path


def _compile_story_package(
    story_package: dict[str, Any],
    book_id: str,
    out_dir: Path,
    arc_plans: list[list[dict[str, Any]]] | None = None,
    route_definitions: list[dict[str, Any]] | None = None,
) -> None:
    """
    Split story_package into LifeScript artifact files.

    Output layout:
      build/
        books.json                            — app catalog index
        book_{id}.json                        — book metadata (no chapters)
        walkthrough_{id}.json                 — walkthrough map
        chapter_index_{id}.json               — chapter_id / chapter_number → arc file path
        chapters/
          {id}_arc01_ch0001-ch0050.json       — arc 1 chapters array
          {id}_arc02_ch0051-ch0100.json       — arc 2 chapters array
          ...

    Each arc file is a standalone JSON array of chapter objects so the app
    can load volumes on demand rather than fetching the entire catalogue at once.
    """
    book = dict(story_package["book"])
    chapters: list[dict[str, Any]] = story_package.get("chapters", [])
    walkthrough = story_package.get("walkthrough", {})

    # book_{id}.json — metadata only
    (out_dir / f"book_{book_id}.json").write_text(
        json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # walkthrough_{id}.json
    (out_dir / f"walkthrough_{book_id}.json").write_text(
        json.dumps(walkthrough, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # books.json — single-item catalog index
    (out_dir / "books.json").write_text(
        json.dumps([book], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Split chapters into per-arc files
    chapters_dir = out_dir / "chapters"
    chapters_dir.mkdir(exist_ok=True)

    chapter_by_num: dict[int, dict[str, Any]] = {c["number"]: c for c in chapters}

    # Build arc batches: use arc_plans boundaries when available,
    # otherwise fall back to fixed-size windows of arc_batch_size.
    if arc_plans:
        arcs: list[list[dict[str, Any]]] = []
        for arc in arc_plans:
            arc_chapters = [chapter_by_num[card["number"]] for card in arc if card["number"] in chapter_by_num]
            if arc_chapters:
                arcs.append(arc_chapters)
    else:
        # Fallback: 50-chapter windows
        window = 50
        arcs = [chapters[i: i + window] for i in range(0, len(chapters), window)]

    # Write arc files + build chapter_index
    chapter_index: dict[str, str] = {}  # chapter_id or "ch{number}" → relative arc file path

    for arc_idx, arc_chapters in enumerate(arcs):
        if not arc_chapters:
            continue
        arc_start = arc_chapters[0]["number"]
        arc_end = arc_chapters[-1]["number"]
        arc_filename = f"{book_id}_arc{arc_idx + 1:02d}_ch{arc_start:04d}-ch{arc_end:04d}.json"
        arc_rel_path = f"chapters/{arc_filename}"

        (chapters_dir / arc_filename).write_text(
            json.dumps(arc_chapters, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        for ch in arc_chapters:
            chapter_index[ch.get("id", f"{book_id}_ch{ch['number']:04d}")] = arc_rel_path
            chapter_index[f"ch{ch['number']}"] = arc_rel_path

    # Build routes section for chapter_index (excludes mainline)
    routes_section: list[dict[str, Any]] = []
    for rd in (route_definitions or []):
        if rd.get("route_type") == "mainline":
            continue
        rid = rd.get("route_id", "")
        branch_start = rd.get("branch_start_chapter")
        merge_ch = rd.get("merge_chapter")
        arc_file = f"branches/{rid}/{book_id}_{rid}_ch{branch_start:04d}-ch{(merge_ch - 1) if merge_ch else 9999:04d}.json" if branch_start else None
        routes_section.append({
            "route_id": rid,
            "route_type": rd.get("route_type", "branch"),
            "title": rd.get("title", rid),
            "branch_start_chapter": branch_start,
            "merge_chapter": merge_ch,
            "entry_condition": rd.get("entry_condition", {}),
            "arc_file": arc_file,
        })

    index_payload = {
        "book_id": book_id,
        "total_chapters": len(chapters),
        "total_arcs": len(arcs),
        "arc_files": [
            {
                "arc": i + 1,
                "file": f"chapters/{book_id}_arc{i + 1:02d}_ch{arcs[i][0]['number']:04d}-ch{arcs[i][-1]['number']:04d}.json",
                "chapter_range": f"{arcs[i][0]['number']}-{arcs[i][-1]['number']}",
                "chapter_count": len(arcs[i]),
            }
            for i in range(len(arcs))
            if arcs[i]
        ],
        "routes": routes_section,
        "chapters": chapter_index,
    }
    (out_dir / f"chapter_index_{book_id}.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def create_if_run(
    session: AsyncSession,
    project_id: UUID,
    cfg: InteractiveFictionConfig,
    output_dir: str,
) -> IFGenerationRunModel:
    run = IFGenerationRunModel(
        project_id=project_id,
        phase=IFGenerationPhase.STORY_BIBLE,
        status="running",
        total_chapters=cfg.target_chapters,
        output_dir=output_dir,
        config_snapshot=cfg.model_dump(mode="json"),
    )
    session.add(run)
    await session.flush()
    return run


async def get_latest_if_run(session: AsyncSession, project_id: UUID) -> IFGenerationRunModel | None:
    result = await session.scalars(
        select(IFGenerationRunModel)
        .where(IFGenerationRunModel.project_id == project_id)
        .order_by(IFGenerationRunModel.created_at.desc())
        .limit(1)
    )
    return result.first()


async def update_if_run_phase(
    session: AsyncSession,
    run_id: UUID,
    phase: IFGenerationPhase,
    completed_chapters: int | None = None,
    error_message: str | None = None,
) -> None:
    run = await session.get(IFGenerationRunModel, run_id)
    if run is None:
        return
    run.phase = phase
    if phase == IFGenerationPhase.FAILED:
        run.status = "failed"
        run.error_message = error_message
    elif phase == IFGenerationPhase.COMPLETED:
        run.status = "completed"
    if completed_chapters is not None:
        run.completed_chapters = completed_chapters
    await session.flush()


# ---------------------------------------------------------------------------
# Integrated pipeline — DB bootstrap, summarizer, context injection
# ---------------------------------------------------------------------------

async def _bootstrap_db_structure(
    session: AsyncSession,
    project: ProjectModel,
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
) -> dict[int, tuple[Any, Any]]:
    """Seed chapters, scenes, and character canon facts from IF bible + arc plans."""
    book = bible["book"]
    story_bible = bible.get("story_bible", {})
    characters = book.get("characters", [])

    # Create mainline plot arc if missing
    existing_arc = await session.scalar(
        select(PlotArcModel).where(
            PlotArcModel.project_id == project.id,
            PlotArcModel.arc_code == "if_mainline",
        )
    )
    if not existing_arc:
        session.add(PlotArcModel(
            project_id=project.id,
            arc_code="if_mainline",
            name=f"{book['title']} — 主线",
            arc_type="mainline",
            promise=story_bible.get("mainline_goal", ""),
            core_question=story_bible.get("premise", ""),
            target_payoff="主线终结",
            status="active",
        ))
        await session.flush()

    # Import characters as canon facts
    for char in characters:
        existing = await session.scalar(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project.id,
                CanonFactModel.subject_label == char["name"],
                CanonFactModel.predicate == "character_profile",
                CanonFactModel.is_current.is_(True),
            )
        )
        if not existing:
            session.add(CanonFactModel(
                project_id=project.id,
                subject_type="character",
                subject_label=char["name"],
                predicate="character_profile",
                fact_type="character",
                value_json={
                    "id": char.get("id"),
                    "name": char["name"],
                    "title": char.get("title"),
                    "role": char.get("role"),
                    "description": char.get("description"),
                },
                confidence=1.0,
                source_type="seeded",
                valid_from_chapter_no=1,
                is_current=True,
                tags=["character", "if_seeded"],
            ))

    await session.flush()

    # Create ChapterModel + SceneCardModel (1 scene per chapter)
    all_cards = [card for arc in arc_plans for card in arc]
    protagonist_name = characters[0]["name"] if characters else "主角"
    chapter_map: dict[int, tuple[Any, Any]] = {}

    for card in all_cards:
        ch_num = card["number"]

        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == ch_num,
            )
        )
        if chapter is None:
            chapter = ChapterModel(
                project_id=project.id,
                chapter_number=ch_num,
                title=card.get("title"),
                chapter_goal=card.get("chapter_goal", ""),
                main_conflict=card.get("main_conflict"),
                chapter_emotion_arc=card.get("primary_emotion"),
                target_word_count=2000,
                status="planned",
                metadata_json={"if_card": card},
            )
            session.add(chapter)
            await session.flush()

        scene = await session.scalar(
            select(SceneCardModel).where(
                SceneCardModel.chapter_id == chapter.id,
                SceneCardModel.scene_number == 1,
            )
        )
        if scene is None:
            featured_ids = card.get("featured_characters", [])
            participants = [
                next((c["name"] for c in characters if c.get("id") == fid), fid)
                for fid in featured_ids
            ] or [protagonist_name]
            scene = SceneCardModel(
                project_id=project.id,
                chapter_id=chapter.id,
                scene_number=1,
                scene_type="action",
                title=card.get("title"),
                participants=participants,
                purpose={
                    "story": card.get("chapter_goal", ""),
                    "emotion": card.get("primary_emotion", ""),
                },
                entry_state={},
                exit_state={},
                target_word_count=2000,
                status="planned",
                metadata_json={"if_card": card},
            )
            session.add(scene)
            await session.flush()

        chapter_map[ch_num] = (chapter, scene)

    await session.flush()
    return chapter_map


async def _store_chapter_summary(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    if_chapter: dict[str, Any],
) -> None:
    """Summarize the generated IF chapter and store as CanonFact for future context injection."""
    from bestseller.services.llm import LLMCompletionRequest, complete_text

    ch_num = if_chapter.get("number", 1)
    title = if_chapter.get("title", f"第{ch_num}章")
    hook = if_chapter.get("next_chapter_hook", "")

    snippets: list[str] = []
    for node in if_chapter.get("nodes", [])[:8]:
        if "text" in node:
            snippets.append(node["text"].get("content", "")[:120])
        elif "dialogue" in node:
            cid = node["dialogue"].get("character_id", "")
            content = node["dialogue"].get("content", "")[:80]
            snippets.append(f"{cid}：{content}")

    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt="你是故事摘要助手。用2-3句话简洁总结章节内容，保留关键情节和人物状态变化，输出纯中文，不超过100字。",
            user_prompt=(
                f"请总结以下互动小说章节：\n"
                f"第{ch_num}章《{title}》\n"
                f"内容摘录：\n{''.join(snippets[:4])}\n"
                f"下章钩子：{hook}"
            ),
            fallback_response=f"第{ch_num}章《{title}》：{hook}",
            project_id=project.id,
        ),
    )

    session.add(CanonFactModel(
        project_id=project.id,
        subject_type="chapter",
        subject_id=chapter.id,
        subject_label=f"第{ch_num}章",
        predicate="chapter_summary",
        fact_type="chapter_summary",
        value_json={
            "summary": result.content.strip(),
            "hook": hook,
            "chapter_number": ch_num,
            "title": title,
        },
        confidence=1.0,
        source_type="summarized",
        source_chapter_id=chapter.id,
        valid_from_chapter_no=ch_num,
        is_current=True,
        tags=["chapter_summary", "if_generation"],
    ))
    await session.flush()


async def _load_recent_summaries(
    session: AsyncSession,
    project: ProjectModel,
    before_chapter_number: int,
    n: int = 5,
) -> list[dict[str, Any]]:
    """Load the N most recent chapter summaries before the given chapter."""
    rows = list(await session.scalars(
        select(CanonFactModel)
        .where(
            CanonFactModel.project_id == project.id,
            CanonFactModel.fact_type == "chapter_summary",
            CanonFactModel.valid_from_chapter_no < before_chapter_number,
        )
        .order_by(CanonFactModel.valid_from_chapter_no.desc())
        .limit(n)
    ))
    return [r.value_json for r in reversed(rows)]


async def _generate_chapter_with_context(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    bible: dict[str, Any],
    card: dict[str, Any],
    batch_entry_hook: str,
    book_id: str,
    cfg: InteractiveFictionConfig,
    client: _LLMCaller,
    chapter_model: Any,
    run_id: Any,
    route_id: str,
    arc_index: int,
    act_id: str | None,
    arc_goal: str,
) -> dict[str, Any]:
    """Generate a single chapter with tiered context injection. Used for parallel batches."""
    import asyncio
    from bestseller.services.if_context import ContextAssembler

    assembler = ContextAssembler()
    ch_num = card["number"]

    # Build tiered context
    context_text = await assembler.assemble(
        chapter_number=ch_num,
        route_id=route_id,
        session=session,
        project=project,
        run_id=run_id,
        tier=cfg.context_mode,
        arc_index=arc_index,
        act_id=act_id,
        arc_goal=arc_goal,
    )

    chapter: dict[str, Any] | None = None
    loop = asyncio.get_event_loop()

    for _attempt in range(5):
        try:
            hint = (
                f"\n(Attempt {_attempt + 1}: ensure the full JSON is complete and valid.)"
                if _attempt > 0
                else ""
            )
            prompt = (
                chapter_prompt(bible, card, batch_entry_hook, book_id, cfg, context_text=context_text)
                + hint
            )
            # Run sync LLM call in thread pool to enable true parallelism
            raw = await loop.run_in_executor(
                None, lambda p=prompt: client.light(p, max_tokens=6000)
            )
            if not raw or raw.strip() in ("None", "null", ""):
                raise json.JSONDecodeError("Empty response from LLM", "", 0)
            chapter = _parse_json(raw)
            break
        except (json.JSONDecodeError, Exception) as exc:
            exc_name = type(exc).__name__
            is_retryable = isinstance(exc, json.JSONDecodeError) or any(
                k in exc_name for k in ("Timeout", "Connection", "APIError", "ServiceUnavailable")
            )
            if _attempt == 4 or not is_retryable:
                raise
            await asyncio.sleep(5 * (_attempt + 1))

    return chapter  # type: ignore[return-value]


async def run_chapters_phase_integrated(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
    cfg: InteractiveFictionConfig,
    chapter_map: dict[int, tuple[Any, Any]],
    client: _LLMCaller,
    existing_chapters: list[dict[str, Any]] | None = None,
    on_chapter: Any = None,
    run_id: Any = None,
    route_id: str = "mainline",
    act_plans: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Chapter generation with DB-backed tiered context + true parallel batches.

    Improvements over original:
    - True asyncio.gather parallelism within each batch
    - ContextAssembler provides hot/warm/cold tiered memory
    - Arc-level summaries and world snapshots generated after each arc
    - Supports run_id for IFCanonFact storage
    """
    import asyncio
    from bestseller.services.if_context import ContextAssembler
    from bestseller.services.if_act_planner import find_act_for_chapter

    book_id = bible["book"]["id"]
    all_cards = [card for arc in arc_plans for card in arc]
    generated: list[dict[str, Any]] = list(existing_chapters or [])
    done_count = len(generated)
    last_hook = generated[-1].get("next_chapter_hook", "") if generated else ""
    remaining_cards = all_cards[done_count:]
    batch_size = cfg.parallel_chapter_batch
    assembler = ContextAssembler()

    # Build arc boundary lookup: chapter_number → arc_index
    ch_to_arc: dict[int, int] = {}
    for arc_idx, arc in enumerate(arc_plans):
        for card in arc:
            ch_to_arc[card["number"]] = arc_idx

    # Track arc completion for summary/snapshot generation
    arc_chapters_buffer: dict[int, list[dict[str, Any]]] = {}
    arc_cards_by_index: dict[int, list[dict[str, Any]]] = {
        i: arc for i, arc in enumerate(arc_plans)
    }
    arc_summaries_done: set[int] = set()
    last_snapshot: dict[str, Any] | None = None

    for i in range(0, len(remaining_cards), batch_size):
        batch = remaining_cards[i: i + batch_size]
        batch_entry_hook = last_hook

        # Determine arc context for the first card in this batch
        first_card = batch[0]
        arc_idx = ch_to_arc.get(first_card["number"], 0)
        act_ctx = find_act_for_chapter(act_plans or [], first_card["number"]) or {}
        act_id = act_ctx.get("act_id")
        arc_goal = (act_ctx.get("arc_breakdown") or [{}])[0].get("arc_goal", "")

        # Launch all chapters in this batch in true parallel
        tasks = [
            _generate_chapter_with_context(
                session=session,
                settings=settings,
                project=project,
                bible=bible,
                card=card,
                batch_entry_hook=batch_entry_hook,
                book_id=book_id,
                cfg=cfg,
                client=client,
                chapter_model=chapter_map.get(card["number"], (None, None))[0],
                run_id=run_id,
                route_id=route_id,
                arc_index=arc_idx,
                act_id=act_id,
                arc_goal=arc_goal,
            )
            for card in batch
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for card, result in zip(batch, results):
            ch_num = card["number"]
            if isinstance(result, Exception):
                raise result

            chapter: dict[str, Any] = result
            errs = validate_chapter(chapter, book_id)
            last_hook = chapter.get("next_chapter_hook", "")
            generated.append(chapter)

            # Store chapter summary in ContextAssembler's IFCanonFact table
            if run_id is not None:
                summary_text = chapter.get("next_chapter_hook", "") or card.get("chapter_goal", "")
                await assembler.store_chapter_summary(
                    session=session,
                    project=project,
                    run_id=run_id,
                    route_id=route_id,
                    chapter_number=ch_num,
                    chapter_title=chapter.get("title", f"第{ch_num}章"),
                    summary_text=summary_text,
                )

            # Also store in old CanonFactModel for backwards compatibility
            ch_model, _ = chapter_map.get(ch_num, (None, None))
            if ch_model is not None:
                await _store_chapter_summary(session, settings, project, ch_model, chapter)

            # Buffer chapter for arc summary generation
            if arc_idx not in arc_chapters_buffer:
                arc_chapters_buffer[arc_idx] = []
            arc_chapters_buffer[arc_idx].append(chapter)

            if on_chapter is not None:
                on_chapter(ch_num, cfg.target_chapters, errs, chapter)

        await session.commit()

        # Check if any arcs completed this batch — generate summary + snapshot
        if run_id is not None:
            for a_idx, arc_card_list in arc_cards_by_index.items():
                if a_idx in arc_summaries_done:
                    continue
                arc_ch_nums = {c["number"] for c in arc_card_list}
                generated_ch_nums = {c["number"] for c in generated}
                if arc_ch_nums.issubset(generated_ch_nums):
                    arc_done_chapters = [c for c in generated if c["number"] in arc_ch_nums]
                    try:
                        arc_sum = generate_arc_summary(
                            client, bible, arc_done_chapters, arc_card_list
                        )
                        snapshot = generate_world_snapshot(client, bible, arc_sum, last_snapshot)
                        last_snapshot = snapshot

                        arc_start = arc_card_list[0]["number"]
                        arc_end = arc_card_list[-1]["number"]
                        act_ctx = find_act_for_chapter(act_plans or [], arc_start) or {}

                        await assembler.store_arc_summary(
                            session=session,
                            project=project,
                            run_id=run_id,
                            route_id=route_id,
                            arc_index=a_idx,
                            chapter_start=arc_start,
                            chapter_end=arc_end,
                            act_id=act_ctx.get("act_id"),
                            summary_data=arc_sum,
                        )
                        await assembler.store_world_snapshot(
                            session=session,
                            project=project,
                            run_id=run_id,
                            route_id=route_id,
                            arc_index=a_idx,
                            snapshot_chapter=arc_end,
                            snapshot_data=snapshot,
                        )
                        await session.commit()
                        arc_summaries_done.add(a_idx)
                    except Exception as exc:
                        pass  # Arc summary failure is non-fatal

    return generated


async def run_if_pipeline_integrated(
    project: ProjectModel,
    cfg: InteractiveFictionConfig,
    output_base: Path,
    settings: AppSettings | None = None,
    resume: bool = False,
    on_progress: Any = None,
) -> Path:
    """
    Full IF generation pipeline integrated with the main bestseller system.

    Improvements over run_if_pipeline:
    - Characters and world rules bootstrapped into DB as CanonFacts
    - Each chapter injected with previous N chapter summaries from DB
    - Summarizer stores chapter summaries after generation for continuity
    """
    from bestseller.infra.db.session import session_scope

    if settings is None:
        from bestseller.settings import load_settings
        settings = load_settings()

    book_id = project.slug.replace("-", "_")
    output_dir = output_base / project.slug / "if"
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(phase: str, payload: dict[str, Any] | None = None) -> None:
        if on_progress is not None:
            on_progress(phase, payload or {})

    state = _load_progress(output_dir) if resume else {}
    client = _LLMCaller(settings)
    concept = build_concept_json(cfg, project)

    # Phase 1: Story Bible
    if "bible" not in state:
        emit("story_bible", {"status": "running"})
        bible = run_bible_phase(client, concept, cfg)
        state["bible"] = bible
        _save_progress(output_dir, state)
        emit("story_bible", {"status": "done"})
    else:
        emit("story_bible", {"status": "loaded"})
        bible = state["bible"]

    # Phase 1.5: Act Plan (NEW — full-book structure, 幕级规划)
    if "act_plans" not in state:
        emit("act_plan", {"status": "running", "act_count": cfg.act_count})
        from bestseller.services.if_act_planner import run_act_plan_phase
        act_plans = run_act_plan_phase(
            client=client,
            bible=bible,
            cfg=cfg,
            on_progress=lambda phase, payload: emit(phase, payload),
        )
        state["act_plans"] = act_plans

        # Persist act plans to DB
        async with session_scope(settings) as session:
            run_record = await get_latest_if_run(session, project.id)
            if run_record:
                run_record.act_plan_json = act_plans
                run_record.generation_mode = "extended" if cfg.enable_branches else "simple"
                for act in act_plans:
                    session.add(IFActPlanModel(
                        project_id=project.id,
                        run_id=run_record.id,
                        act_id=act["act_id"],
                        act_index=act["act_index"],
                        title=act["title"],
                        chapter_start=act["chapter_start"],
                        chapter_end=act["chapter_end"],
                        act_goal=act["act_goal"],
                        core_theme=act.get("core_theme"),
                        dominant_emotion=act.get("dominant_emotion"),
                        climax_chapter=act.get("climax_chapter"),
                        entry_state=act.get("entry_state"),
                        exit_state=act.get("exit_state"),
                        payoff_promises=act.get("payoff_promises", []),
                        branch_opportunities=act.get("branch_opportunities", []),
                        arc_breakdown=act.get("arc_breakdown", []),
                    ))
                await session.commit()

        _save_progress(output_dir, state)
        emit("act_plan", {"status": "done", "acts": len(act_plans)})
    else:
        act_plans = state["act_plans"]
        emit("act_plan", {"status": "loaded", "acts": len(act_plans)})

    # Phase 2: Arc Plans (v2 — with Act context + previous Arc summary)
    if "arc_plans" not in state:
        total_arcs = (cfg.target_chapters + cfg.arc_batch_size - 1) // cfg.arc_batch_size
        emit("arc_plan", {"status": "running", "total_arcs": total_arcs})

        def on_arc(arc_num: int, total: int, start: int, end: int) -> None:
            emit("arc_plan", {"arc": arc_num, "total": total, "chapters": f"{start}-{end}"})

        arc_plans_mainline = run_arc_plan_phase_v2(
            client=client,
            bible=bible,
            cfg=cfg,
            act_plans=act_plans,
            on_arc=on_arc,
        )
        state["arc_plans"] = arc_plans_mainline
        _save_progress(output_dir, state)
        emit("arc_plan", {"status": "done", "arcs": len(arc_plans_mainline)})
    else:
        arc_plans_mainline = state["arc_plans"]
        emit("arc_plan", {"status": "loaded", "arcs": len(arc_plans_mainline)})

    # DB Bootstrap: chapters, scenes, character canon facts
    emit("db_bootstrap", {"status": "running"})
    async with session_scope(settings) as session:
        chapter_map = await _bootstrap_db_structure(session, project, bible, arc_plans_mainline)
        await session.commit()
    emit("db_bootstrap", {"status": "done", "chapters": len(chapter_map)})

    # Retrieve run_id for context storage (IFCanonFact / IFArcSummary etc.)
    run_id = None
    async with session_scope(settings) as session:
        run_record = await get_latest_if_run(session, project.id)
        if run_record:
            run_id = run_record.id

    # Phase 3: Chapter Generation (true parallel batches + tiered context)
    _migrate_chapters_from_state(output_dir, state, "chapters")
    generated: list[dict[str, Any]] = _load_all_chapters(output_dir)
    all_cards_count = sum(len(arc) for arc in arc_plans_mainline)

    if len(generated) < all_cards_count:
        emit("chapter_gen", {"status": "running", "done": len(generated), "total": all_cards_count})

        async with session_scope(settings) as session:
            def on_chapter(ch_num: int, total: int, errs: list[str], chapter: dict | None = None) -> None:
                if chapter is not None and ch_num not in {g["number"] for g in generated}:
                    _save_chapter(output_dir, chapter)
                    generated.append(chapter)
                _save_progress(output_dir, state)
                emit("chapter_gen", {"chapter": ch_num, "total": total, "warnings": len(errs)})

            generated = await run_chapters_phase_integrated(
                session=session,
                settings=settings,
                project=project,
                bible=bible,
                arc_plans=arc_plans_mainline,
                cfg=cfg,
                chapter_map=chapter_map,
                client=client,
                existing_chapters=generated,
                on_chapter=on_chapter,
                run_id=run_id,
                route_id="mainline",
                act_plans=act_plans,
            )

        # Ensure any chapters not yet persisted are saved
        for ch in generated:
            if not _chapter_exists(output_dir, ch["number"]):
                _save_chapter(output_dir, ch)
        _save_progress(output_dir, state)
        emit("chapter_gen", {"status": "done", "total": len(generated)})
    else:
        emit("chapter_gen", {"status": "loaded", "total": len(generated)})

    # Phase 3.5: Branch Generation (硬分支章节生成) — NEW
    branch_routes: dict[str, Any] = state.get("branch_routes", {})
    if cfg.enable_branches and run_id is not None:
        from bestseller.services.if_branch_engine import BranchEngine
        from bestseller.services.if_context import ContextAssembler

        engine = BranchEngine()
        assembler_ctx = ContextAssembler()

        # Plan branches from act_plans if not done yet
        if "route_definitions" not in state:
            emit("branch_plan", {"status": "running"})
            route_defs = engine.plan_branches(act_plans, cfg, book_id)
            state["route_definitions"] = route_defs

            # Persist route definitions to DB
            async with session_scope(settings) as session:
                for rd in route_defs:
                    session.add(IFRouteDefinitionModel(
                        project_id=project.id,
                        run_id=run_id,
                        route_id=rd["route_id"],
                        route_type=rd["route_type"],
                        title=rd["title"],
                        description=rd.get("description"),
                        branch_start_chapter=rd.get("branch_start_chapter"),
                        merge_chapter=rd.get("merge_chapter"),
                        entry_condition=rd.get("entry_condition", {}),
                        merge_contract=rd.get("merge_contract", {}),
                    ))
                await session.commit()

            _save_progress(output_dir, state)
            emit("branch_plan", {"status": "done", "routes": len(route_defs)})
        else:
            route_defs = state["route_definitions"]
            emit("branch_plan", {"status": "loaded", "routes": len(route_defs)})

        # Generate chapters for each branch route (skip mainline)
        for route_def in route_defs:
            rid = route_def["route_id"]
            if rid == "mainline":
                continue
            if rid in branch_routes:
                emit("branch_chapter_gen", {"route": rid, "status": "loaded"})
                continue

            emit("branch_chapter_gen", {"route": rid, "status": "running"})

            # Get world state snapshot at branch fork point
            branch_start = route_def.get("branch_start_chapter", 1)
            fork_snapshot_data: dict[str, Any] = {}
            async with session_scope(settings) as session:
                snap = await assembler_ctx.get_snapshot_at_or_before(
                    chapter_number=branch_start,
                    route_id="mainline",
                    session=session,
                    project=project,
                    run_id=run_id,
                )
                if snap:
                    fork_snapshot_data = {
                        "world_summary": snap.world_summary or "",
                        "character_states": snap.character_states,
                        "faction_states": snap.faction_states,
                        "snapshot_chapter": snap.snapshot_chapter,
                    }

            # Generate branch arc plan
            branch_cards = engine.generate_branch_arc_plan(
                client=client,
                bible=bible,
                route_def=route_def,
                fork_state_snapshot=fork_snapshot_data,
                cfg=cfg,
            )

            if not branch_cards:
                emit("branch_chapter_gen", {"route": rid, "status": "skipped", "reason": "no_cards"})
                continue

            # Generate branch chapters
            def on_branch_chapter(ch_num: int, route_id_inner: str, errs: list[str]) -> None:
                emit("branch_chapter_gen", {"route": route_id_inner, "chapter": ch_num, "warnings": len(errs)})

            branch_chapters = engine.generate_branch_chapters(
                client=client,
                bible=bible,
                route_def=route_def,
                branch_cards=branch_cards,
                fork_state_snapshot=fork_snapshot_data,
                cfg=cfg,
                output_dir=output_dir,
                on_chapter=on_branch_chapter,
            )

            branch_routes[rid] = {"chapter_count": len(branch_chapters), "cards": len(branch_cards)}
            state["branch_routes"] = branch_routes
            _save_progress(output_dir, state)
            emit("branch_chapter_gen", {"route": rid, "status": "done", "chapters": len(branch_chapters)})

    # Phase 4: Walkthrough
    if "walkthrough" not in state:
        emit("walkthrough", {"status": "running"})
        walkthrough = run_walkthrough_phase(client, bible, arc_plans_mainline, cfg)
        state["walkthrough"] = walkthrough
        _save_progress(output_dir, state)
        emit("walkthrough", {"status": "done"})
    else:
        walkthrough = state["walkthrough"]
        emit("walkthrough", {"status": "loaded"})

    # Phase 5: Assembly
    emit("assembly", {"status": "running"})
    story_package = assemble_story_package(bible, generated, walkthrough)
    out_path = output_dir / "story_package.json"
    out_path.write_text(json.dumps(story_package, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("assembly", {"status": "done", "path": str(out_path)})

    # Phase 6: Compile (arc-split artifact files, with routes index)
    emit("compile", {"status": "running"})
    compiled_dir = output_dir / "build"
    compiled_dir.mkdir(exist_ok=True)
    route_defs_for_compile = state.get("route_definitions", [])
    _compile_story_package(
        story_package,
        book_id,
        compiled_dir,
        arc_plans=arc_plans_mainline,
        route_definitions=route_defs_for_compile,
    )
    emit("compile", {"status": "done", "dir": str(compiled_dir)})

    return out_path

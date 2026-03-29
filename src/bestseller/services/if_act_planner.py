"""
if_act_planner.py — Acts-level (幕级) full-book structure planner.

Generates the top-level Acts structure for a 1000-chapter IF novel:
  Story Bible → [Act Plan] → Arc Plans → Chapter Gen → ...

Each Act covers ~200 chapters and defines:
  - act_goal, core_theme, dominant_emotion
  - entry_state → exit_state (protagonist arc)
  - payoff_promises (爽点承诺)
  - branch_opportunities (hard branch trigger points)
  - arc_breakdown (50-chapter arcs within the act)

This plan is injected into every subsequent Arc Plan prompt, ensuring
that the LLM knows the full narrative arc when planning each 50-chapter batch.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def run_act_plan_phase(
    client: Any,  # _LLMCaller from if_generation
    bible: dict[str, Any],
    cfg: Any,  # InteractiveFictionConfig
    on_progress: Any = None,
) -> list[dict[str, Any]]:
    """
    Generate the Acts-level full-book structure plan.

    Args:
        client:      LLMCaller with .heavy() method
        bible:       Story bible dict (output of run_bible_phase)
        cfg:         InteractiveFictionConfig
        on_progress: Optional callable(phase, payload) for progress reporting

    Returns:
        List of act dicts, each containing:
          act_id, act_index, title, chapter_start, chapter_end,
          act_goal, core_theme, dominant_emotion, climax_chapter,
          entry_state, exit_state, payoff_promises,
          branch_opportunities, arc_breakdown
    """
    from bestseller.services.if_prompts import act_plan_prompt
    from bestseller.services.if_generation import _parse_json  # type: ignore[attr-defined]

    if on_progress:
        on_progress("act_plan", {"status": "running", "act_count": cfg.act_count})

    for attempt in range(3):
        try:
            prompt = act_plan_prompt(bible, cfg)
            raw = client.heavy(prompt, max_tokens=8192)
            parsed = _parse_json(raw)

            acts: list[dict[str, Any]] = parsed.get("acts", [])
            if not acts:
                raise ValueError("LLM returned empty acts list")

            # Validate and normalize
            acts = _normalize_acts(acts, cfg)

            if on_progress:
                on_progress("act_plan", {
                    "status": "done",
                    "acts": len(acts),
                    "total_chapters": cfg.target_chapters,
                })

            logger.info(
                "Act plan generated: %d acts covering %d chapters",
                len(acts),
                cfg.target_chapters,
            )
            return acts

        except Exception as exc:
            logger.warning("Act plan attempt %d failed: %s", attempt + 1, exc)
            if attempt == 2:
                logger.error("Act plan failed after 3 attempts, using fallback")
                return _generate_fallback_acts(cfg)
            time.sleep(5 * (attempt + 1))

    return _generate_fallback_acts(cfg)


def find_act_for_chapter(acts: list[dict[str, Any]], chapter_number: int) -> dict[str, Any] | None:
    """Return the Act dict that contains the given chapter number."""
    for act in acts:
        if act.get("chapter_start", 0) <= chapter_number <= act.get("chapter_end", 0):
            return act
    return None


def get_open_clues_for_arc(
    acts: list[dict[str, Any]],
    arc_summaries: list[dict[str, Any]],
    arc_start: int,
) -> list[dict[str, Any]]:
    """
    Collect all open (unresoloved) clues before the given arc starts.
    Pulls from arc summaries' open_clues lists.
    """
    open_clues: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    resolved_codes: set[str] = set()

    for summary in arc_summaries:
        if summary.get("chapter_end", 0) >= arc_start:
            continue
        for code in summary.get("resolved_clues", []):
            resolved_codes.add(str(code))

    for summary in arc_summaries:
        if summary.get("chapter_end", 0) >= arc_start:
            continue
        for clue in summary.get("open_clues", []):
            code = str(clue.get("code", ""))
            if code and code not in seen_codes and code not in resolved_codes:
                seen_codes.add(code)
                open_clues.append(clue)

    return open_clues


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_acts(acts: list[dict[str, Any]], cfg: Any) -> list[dict[str, Any]]:
    """Ensure act fields are present and act indices are consistent."""
    normalized: list[dict[str, Any]] = []
    for i, act in enumerate(acts):
        act["act_index"] = i
        if "act_id" not in act:
            act["act_id"] = f"act_{i + 1:02d}"
        act.setdefault("payoff_promises", [])
        act.setdefault("branch_opportunities", [])
        act.setdefault("arc_breakdown", [])
        act.setdefault("core_theme", "")
        act.setdefault("dominant_emotion", "热血")
        normalized.append(act)
    return normalized


def _generate_fallback_acts(cfg: Any) -> list[dict[str, Any]]:
    """
    Generate a simple fallback Acts structure if LLM fails.
    Divides chapters evenly into cfg.act_count acts.
    """
    total = cfg.target_chapters
    act_size = total // cfg.act_count
    acts: list[dict[str, Any]] = []

    act_themes = [
        ("觉醒崛起", "热血", "废材/普通人觉醒，获得第一个重大优势"),
        ("扩张威胁", "紧张", "主角实力增长，引来更强大的对手"),
        ("危机蜕变", "压抑", "遭遇重大挫折，完成更深层的蜕变"),
        ("决战前夜", "震撼", "最终决战的棋局布置，各方力量汇聚"),
        ("最终对决", "爽快", "决战，收割情感，完成所有承诺"),
    ]

    for i in range(cfg.act_count):
        start = i * act_size + 1
        end = (i + 1) * act_size if i < cfg.act_count - 1 else total
        theme_idx = min(i, len(act_themes) - 1)
        theme, emotion, goal = act_themes[theme_idx]

        branch_trigger = start + (end - start) // 3
        merge_ch = branch_trigger + getattr(cfg, "branch_chapter_span", 30)
        if merge_ch > end:
            merge_ch = end - 5

        arcs: list[dict[str, Any]] = []
        arc_start = start
        arc_idx = 0
        while arc_start <= end:
            arc_end = min(arc_start + cfg.arc_batch_size - 1, end)
            arcs.append({
                "arc_index": arc_idx,
                "chapter_start": arc_start,
                "chapter_end": arc_end,
                "arc_goal": f"推进{theme}阶段的核心冲突",
            })
            arc_start = arc_end + 1
            arc_idx += 1

        acts.append({
            "act_id": f"act_{i + 1:02d}",
            "act_index": i,
            "title": f"第{i + 1}幕：{theme}",
            "chapter_start": start,
            "chapter_end": end,
            "act_goal": goal,
            "core_theme": theme,
            "dominant_emotion": emotion,
            "climax_chapter": start + (end - start) * 4 // 5,
            "entry_state": "待定",
            "exit_state": "待定",
            "payoff_promises": [f"第{i + 1}幕核心爽点兑现"],
            "branch_opportunities": [
                {
                    "trigger_chapter": branch_trigger,
                    "choice_theme": "关键选择",
                    "routes": ["branch_a", "branch_b"],
                    "merge_chapter": merge_ch,
                    "branch_chapter_span": getattr(cfg, "branch_chapter_span", 30),
                }
            ],
            "arc_breakdown": arcs,
        })

    return acts

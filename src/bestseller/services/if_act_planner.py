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
    is_en = str(getattr(cfg, "language", "") or "").lower().startswith("en")

    for i in range(cfg.act_count):
        start = i * act_size + 1
        end = (i + 1) * act_size if i < cfg.act_count - 1 else total
        if is_en:
            title = f"Act {i + 1}"
            if i == 0:
                theme = "Initial Momentum"
                emotion = "driving"
                goal = "Establish the core objective, active constraints, and the first meaningful branch."
                arc_goal = "Push the opening objective into visible motion"
            elif i == cfg.act_count - 1:
                theme = "Endgame Resolution"
                emotion = "decisive"
                goal = "Resolve the central pressure, pay off major choices, and define the aftermath."
                arc_goal = "Drive the act toward endgame resolution"
            else:
                theme = "Pressure Escalation"
                emotion = "tense"
                goal = "Increase the cost of action, reorder alliances, and move the story into a deeper layer."
                arc_goal = "Escalate cost, resistance, and branching pressure"
            payoff_promise = f"Act {i + 1} delivers a major turning point."
            choice_theme = "Key decision"
            entry_state = "To be defined"
            exit_state = "To be defined"
        else:
            title = f"第{i + 1}幕"
            if i == 0:
                theme = "起势推进"
                emotion = "推进"
                goal = "建立主线目标、关键限制与第一轮分支压力。"
                arc_goal = "推动主线目标进入可执行状态"
            elif i == cfg.act_count - 1:
                theme = "终局收束"
                emotion = "决断"
                goal = "回收关键选择与主要压力，并为后续余波留出口。"
                arc_goal = "推动本幕走向终局决断"
            else:
                theme = "压力升级"
                emotion = "紧张"
                goal = "抬高行动代价、重排关系站位，并把局势推进到更深一层。"
                arc_goal = "放大代价、阻力与分支压力"
            payoff_promise = f"第{i + 1}幕完成一次关键转折。"
            choice_theme = "关键选择"
            entry_state = "待定"
            exit_state = "待定"

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
                "arc_goal": arc_goal,
            })
            arc_start = arc_end + 1
            arc_idx += 1

        acts.append({
            "act_id": f"act_{i + 1:02d}",
            "act_index": i,
            "title": title,
            "chapter_start": start,
            "chapter_end": end,
            "act_goal": goal,
            "core_theme": theme,
            "dominant_emotion": emotion,
            "climax_chapter": start + (end - start) * 4 // 5,
            "entry_state": entry_state,
            "exit_state": exit_state,
            "payoff_promises": [payoff_promise],
            "branch_opportunities": [
                {
                    "trigger_chapter": branch_trigger,
                    "choice_theme": choice_theme,
                    "routes": ["branch_a", "branch_b"],
                    "merge_chapter": merge_ch,
                    "branch_chapter_span": getattr(cfg, "branch_chapter_span", 30),
                }
            ],
            "arc_breakdown": arcs,
        })

    return acts

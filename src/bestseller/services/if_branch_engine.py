"""
if_branch_engine.py — Hard-branch route planner and chapter generator.

Implements true multi-route branching where different choices lead to
genuinely different chapter content (not just stat changes within the same
linear sequence).

Architecture:
  1. plan_branches()           — extract branch opportunities from act_plans
  2. generate_branch_arc_plan() — LLM plans the N chapters for this branch
  3. generate_branch_chapters() — LLM writes each branch chapter
  4. Branch chapters output to: output_dir/branches/{route_id}/

Branch lifecycle:
  mainline ch1 ... ch99
                ↓
  ch100: CHOICE node triggers branch split
                ├── branch_warrior  ch101-130 (30 chapters)
                └── branch_schemer  ch101-125 (25 chapters)
                ↓
  ch131/ch126: MERGE back to mainline (merge_contract ensures compatible state)
  mainline ch131 ... ch1000
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BranchEngine:
    """Plans and generates hard-branch route content."""

    def plan_branches(
        self,
        act_plans: list[dict[str, Any]],
        cfg: Any,  # InteractiveFictionConfig
        book_id: str,
    ) -> list[dict[str, Any]]:
        """
        Extract branch definitions from act_plans.branch_opportunities.

        Returns a list of route_def dicts (later stored as IFRouteDefinitionModel).
        Format:
            route_id, route_type, title, description,
            branch_start_chapter, merge_chapter,
            entry_condition, merge_contract
        """
        routes: list[dict[str, Any]] = []

        # Always add mainline as route_id="mainline"
        routes.append({
            "route_id": "mainline",
            "route_type": "mainline",
            "title": "主线",
            "description": "所有玩家的核心故事线",
            "branch_start_chapter": None,
            "merge_chapter": None,
            "entry_condition": {},
            "merge_contract": {},
        })

        branch_count = 0
        max_branches = getattr(cfg, "branch_count", 2)

        for act in act_plans:
            if branch_count >= max_branches:
                break
            for opp in act.get("branch_opportunities", []):
                if branch_count >= max_branches:
                    break
                routes_in_opp = opp.get("routes", [])
                for route_name in routes_in_opp:
                    if branch_count >= max_branches:
                        break
                    trigger_ch = opp.get("trigger_chapter", 1)
                    merge_ch = opp.get("merge_chapter", trigger_ch + cfg.branch_chapter_span)
                    routes.append({
                        "route_id": route_name,
                        "route_type": "branch",
                        "title": f"{opp.get('choice_theme', '分支路线')} — {route_name}",
                        "description": opp.get("choice_theme", ""),
                        "branch_start_chapter": trigger_ch + 1,
                        "merge_chapter": merge_ch,
                        "entry_condition": {
                            "trigger_chapter": trigger_ch,
                            "choice_theme": opp.get("choice_theme", ""),
                            "route_variant": route_name,
                        },
                        "merge_contract": {
                            "required_facts": [
                                "主角已处于可继续主线的状态",
                                "主角与主要NPC的基本关系在可接受范围内",
                            ],
                            "canonical_hook": f"第{merge_ch}章主线开篇（所有路线汇聚后的统一描述）",
                        },
                    })
                    branch_count += 1

        logger.info("Planned %d routes (%d branches)", len(routes), len(routes) - 1)
        return routes

    def generate_branch_arc_plan(
        self,
        client: Any,  # _LLMCaller
        bible: dict[str, Any],
        route_def: dict[str, Any],
        fork_state_snapshot: dict[str, Any] | None,
        cfg: Any,  # InteractiveFictionConfig
    ) -> list[dict[str, Any]]:
        """
        Generate chapter cards (arc plan) for a branch route.

        Uses branch_arc_plan_prompt which injects:
        - The world state at the fork point
        - The merge contract the branch must satisfy
        - The route's unique identity/flavor
        """
        from bestseller.services.if_prompts import branch_arc_plan_prompt
        from bestseller.services.if_generation import _parse_json  # type: ignore[attr-defined]

        snapshot = fork_state_snapshot or {}
        merge_contract = route_def.get("merge_contract", {})

        for attempt in range(3):
            try:
                prompt = branch_arc_plan_prompt(
                    bible=bible,
                    route_def=route_def,
                    fork_state_snapshot=snapshot,
                    merge_contract=merge_contract,
                    cfg=cfg,
                )
                raw = client.heavy(prompt, max_tokens=8192)
                cards = _parse_json(raw)
                if not isinstance(cards, list):
                    cards = cards.get("chapters", cards.get("cards", []))

                logger.info(
                    "Branch arc plan generated: route=%s, %d cards",
                    route_def.get("route_id"),
                    len(cards),
                )
                return cards

            except Exception as exc:
                logger.warning(
                    "Branch arc plan attempt %d failed for %s: %s",
                    attempt + 1,
                    route_def.get("route_id"),
                    exc,
                )
                if attempt == 2:
                    logger.error("Branch arc plan failed, returning empty list")
                    return []
                time.sleep(5 * (attempt + 1))

        return []

    def generate_branch_chapters(
        self,
        client: Any,  # _LLMCaller
        bible: dict[str, Any],
        route_def: dict[str, Any],
        branch_cards: list[dict[str, Any]],
        fork_state_snapshot: dict[str, Any] | None,
        cfg: Any,  # InteractiveFictionConfig
        output_dir: Path,
        existing_chapters: list[dict[str, Any]] | None = None,
        on_chapter: Any = None,
    ) -> list[dict[str, Any]]:
        """
        Generate all chapter content for a branch route.

        Chapters are written to:
            output_dir/branches/{route_id}/

        Each chapter uses the same chapter_prompt as mainline, but:
        - Prefixed with branch context (fork_state + route identity)
        - The "prev_hook" chain starts from the fork point hook
        - Context injection uses the fork snapshot instead of mainline summaries
        """
        from bestseller.services.if_prompts import chapter_prompt, validate_chapter
        from bestseller.services.if_generation import _parse_json  # type: ignore[attr-defined]

        route_id = route_def.get("route_id", "branch_unknown")
        book_id = bible["book"]["id"]

        # Set up branch output directory
        branch_dir = output_dir / "branches" / route_id
        branch_dir.mkdir(parents=True, exist_ok=True)

        generated: list[dict[str, Any]] = list(existing_chapters or [])
        done_count = len(generated)

        # Seed hook from fork state or last generated
        if generated:
            last_hook = generated[-1].get("next_chapter_hook", "")
        elif fork_state_snapshot:
            last_hook = fork_state_snapshot.get("world_summary", "")[:100]
        else:
            last_hook = ""

        remaining_cards = branch_cards[done_count:]

        # Build branch context from fork snapshot
        branch_context = self._build_branch_context(route_def, fork_state_snapshot)

        batch_size = getattr(cfg, "parallel_chapter_batch", 8)

        for i in range(0, len(remaining_cards), batch_size):
            batch = remaining_cards[i: i + batch_size]

            for card in batch:
                chapter: dict[str, Any] | None = None

                for attempt in range(5):
                    try:
                        hint = (
                            f"\n(Attempt {attempt + 1}: ensure the full JSON is complete and valid.)"
                            if attempt > 0
                            else ""
                        )
                        prompt = (
                            chapter_prompt(bible, card, last_hook, book_id, cfg, context_text=branch_context)
                            + hint
                        )
                        raw = client.light(prompt, max_tokens=12000)
                        if not raw or raw.strip() in ("None", "null", ""):
                            raise json.JSONDecodeError("Empty response from LLM", "", 0)
                        chapter = _parse_json(raw)
                        break
                    except (json.JSONDecodeError, Exception) as exc:
                        exc_name = type(exc).__name__
                        is_retryable = isinstance(exc, json.JSONDecodeError) or any(
                            k in exc_name
                            for k in ("Timeout", "Connection", "APIError", "ServiceUnavailable")
                        )
                        if attempt == 4 or not is_retryable:
                            raise
                        time.sleep(5 * (attempt + 1))

                errs = validate_chapter(chapter, book_id)  # type: ignore[arg-type]
                last_hook = chapter.get("next_chapter_hook", "")  # type: ignore[union-attr]
                generated.append(chapter)  # type: ignore[arg-type]

                if on_chapter is not None:
                    on_chapter(card["number"], route_id, errs)

                time.sleep(0.2)

        # Write branch chapters to disk
        if generated:
            self._write_branch_output(branch_dir, book_id, route_id, generated, route_def)

        return generated

    def create_merge_chapter_context(
        self,
        route_def: dict[str, Any],
        mainline_card: dict[str, Any],
    ) -> str:
        """
        Generate a narrative hint for the merge chapter in the mainline arc plan.

        This is injected into the mainline arc plan so that the merge chapter
        naturally accommodates players arriving from different routes.
        """
        merge_ch = route_def.get("merge_chapter", "?")
        canonical_hook = route_def.get("merge_contract", {}).get("canonical_hook", "")
        required_facts = route_def.get("merge_contract", {}).get("required_facts", [])

        lines = [
            f"[分支汇合说明] 第{merge_ch}章是多条路线的汇合点。",
            "请确保本章的叙事能自然承接来自不同路线的玩家：",
        ]
        for fact in required_facts:
            lines.append(f"  - {fact}")
        if canonical_hook:
            lines.append(f"本章开篇基调：{canonical_hook}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_branch_context(
        self,
        route_def: dict[str, Any],
        fork_snapshot: dict[str, Any] | None,
    ) -> str:
        """Build the context string injected into each branch chapter prompt."""
        parts: list[str] = []

        route_title = route_def.get("title", "分支路线")
        choice_theme = route_def.get("description", "")
        parts.append(f"[当前路线] {route_title}")
        if choice_theme:
            parts.append(f"玩家在分叉点选择了：{choice_theme}")

        if fork_snapshot:
            world_summary = fork_snapshot.get("world_summary", "")
            if world_summary:
                parts.append(f"分叉时世界状态：{world_summary}")

        merge_ch = route_def.get("merge_chapter")
        if merge_ch:
            required_facts = route_def.get("merge_contract", {}).get("required_facts", [])
            parts.append(f"本路线将在第{merge_ch}章汇回主线。")
            if required_facts:
                parts.append(f"汇合要求：{required_facts[0]}")

        return "\n".join(parts)

    def _write_branch_output(
        self,
        branch_dir: Path,
        book_id: str,
        route_id: str,
        chapters: list[dict[str, Any]],
        route_def: dict[str, Any],
    ) -> None:
        """Write branch chapters to disk as a single arc JSON file."""
        if not chapters:
            return

        chapters_sorted = sorted(chapters, key=lambda c: c.get("number", 0))
        first_ch = chapters_sorted[0].get("number", 0)
        last_ch = chapters_sorted[-1].get("number", 0)

        arc_filename = f"{book_id}_{route_id}_ch{first_ch:04d}-ch{last_ch:04d}.json"
        arc_path = branch_dir / arc_filename

        payload = {
            "book_id": book_id,
            "route_id": route_id,
            "route_title": route_def.get("title", route_id),
            "branch_start_chapter": route_def.get("branch_start_chapter"),
            "merge_chapter": route_def.get("merge_chapter"),
            "entry_condition": route_def.get("entry_condition", {}),
            "merge_contract": route_def.get("merge_contract", {}),
            "chapters": chapters_sorted,
        }

        arc_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "Branch output written: route=%s, chapters=%d–%d, file=%s",
            route_id,
            first_ch,
            last_ch,
            arc_path,
        )

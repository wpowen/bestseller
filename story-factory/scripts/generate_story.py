#!/usr/bin/env python3
"""
LifeScript Story Generator
==========================
Generates a complete story_package.json with N chapters using Claude API.

Usage:
    python3 generate_story.py <concept.json> [--chapters 500] [--resume] [--output-dir <dir>]

Phases:
    1. Story Bible    — claude-sonnet-4-6 generates characters, arcs, secrets
    2. Arc Plan       — claude-sonnet-4-6 outlines all chapters in batches of 50
    3. Chapter Gen    — claude-haiku-4-5  writes full chapter JSON (parallel batches of 8)
    4. Walkthrough    — claude-sonnet-4-6 assembles stage map + chapter guides
    5. Assembly       — compile final story_package.json + save progress

The script is fully resumable: progress is saved after each batch.
Run again with --resume to continue from the last checkpoint.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import anthropic
from story_factory_genres import DEFAULT_GENRE, VALID_GENRES, format_allowed_genres

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
MODEL_HEAVY = "claude-sonnet-4-6"
MODEL_LIGHT = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Valid enum values (must match Swift models exactly)
# ---------------------------------------------------------------------------
VALID_STATS = {"战力", "名望", "谋略", "财富", "魅力", "黑化值", "天命值"}
VALID_SATISFACTION = {"直接爽", "延迟爽", "阴谋爽", "碾压爽", "情感爽", "扮猪吃虎"}
VALID_REL_DIMS = {"信任", "好感", "敌意", "敬畏", "依赖"}
VALID_ROLES = {"盟友", "宿敌", "红颜", "师尊", "家族", "中立", "反派"}

# ---------------------------------------------------------------------------
# Chapter JSON schema description (injected into prompts)
# ---------------------------------------------------------------------------
CHAPTER_SCHEMA = """
A chapter is a JSON object with these fields:
  id           : string  — "{book_id}_ch{number:04d}"
  book_id      : string  — must match the book's id
  number       : int     — 1-based sequential
  title        : string  — chapter title (Chinese, ≤ 12 chars)
  is_paid      : bool    — false for first 20 chapters, true thereafter
  next_chapter_hook : string — 1-2 sentences teaser for the next chapter

  nodes: array of node objects. Each node is ONE of:
    { "text":     { "id": "...", "content": "...", "emphasis": "dramatic"|"system" (optional) } }
    { "dialogue": { "id": "...", "character_id": "...", "content": "...", "emotion": "..." } }
    { "choice":   {
        "id": "...",
        "prompt": "...",
        "choice_type": "keyDecision"|"styleChoice"|"characterPref",
        "choices": [  /* 2-4 items */
          {
            "id": "...",
            "text": "...",            /* short label ≤ 20 chars */
            "description": "...",    /* 1-2 sentences */
            "satisfaction_type": one of 直接爽|延迟爽|阴谋爽|碾压爽|情感爽|扮猪吃虎,
            "visible_cost": "...",
            "visible_reward": "...",
            "risk_hint": "...",
            "process_label": "...",
            "stat_effects": [ { "stat": one of 战力|名望|谋略|财富|魅力|黑化值|天命值, "delta": int } ],
            "relationship_effects": [ { "character_id": "...", "dimension": one of 信任|好感|敌意|敬畏|依赖, "delta": int } ],
            "result_nodes": [ /* 1-3 inline text/dialogue nodes shown after this choice */ ],
            "is_premium": false
          }
        ]
      }
    }

Rules:
- 30-50 nodes per chapter
- 3-5 choice nodes per chapter (never 0, never more than 6)
- every choice must have result_nodes (1-3 inline nodes showing immediate reaction)
- all node ids must be unique within the chapter, format: "{chapterId}_{seq}"
- text length 3000-6000 Chinese characters total across all text/dialogue nodes
- each individual text node should be 200-400 Chinese characters (rich, immersive prose)
- each individual dialogue node should include vivid action beats, not just spoken words
- end every chapter with a dramatic or system text node before the next_chapter_hook
"""

# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _bible_prompt(concept: dict[str, Any]) -> str:
    return f"""You are a professional interactive fiction story designer for LifeScript, a Chinese mobile app.

Generate a complete story bible for the following concept. Output ONLY valid JSON, no markdown fences.

Concept:
{json.dumps(concept, ensure_ascii=False, indent=2)}

Output format (JSON):
{{
  "book": {{
    "id": "<concept book_id>",
    "title": "<title>",
    "author": "命书工作室",
    "cover_image_name": "cover_<book_id>",
    "synopsis": "<2-3 sentence synopsis in Chinese>",
    "genre": "<one of: {format_allowed_genres()}>",
    "tags": ["<5 tags in Chinese>"],
    "interaction_tags": ["高互动", "<2-3 more tags>"],
    "total_chapters": <target_chapters from concept>,
    "free_chapters": 20,
    "characters": [
      {{
        "id": "char_<shortname>",
        "name": "<Chinese name>",
        "title": "<role title>",
        "avatar_image_name": "avatar_<shortname>",
        "description": "<2-3 sentences>",
        "role": "<one of: 盟友|宿敌|红颜|师尊|家族|中立|反派>"
      }}
    ],
    "initial_stats": {{
      "combat": <5-15>,
      "fame": <5-10>,
      "strategy": <10-25>,
      "wealth": <5-10>,
      "charm": <8-15>,
      "darkness": 0,
      "destiny": <20-30>
    }}
  }},
  "reader_desire_map": {{
    "core_fantasy": "<what emotional need this story satisfies>",
    "reward_promises": ["<3 specific payoffs>"],
    "control_promises": ["<3 things the player controls>"],
    "suspense_questions": ["<3 questions that keep readers going>"]
  }},
  "story_bible": {{
    "premise": "<one sentence>",
    "mainline_goal": "<protagonist's central objective>",
    "side_threads": ["<3-5 subplots>"],
    "hidden_truths": ["<2-3 secrets revealed over the story>"]
  }},
  "route_graph": {{
    "mainline": "<main story spine description>",
    "side_routes": ["<character relationship routes>"],
    "hidden_routes": ["<secret routes unlocked by choices>"],
    "milestones": [
      {{ "id": "milestone_<n>", "title": "<milestone>", "chapter_range": "<e.g. 1-50>" }}
    ]
  }}
}}

Design guidelines:
- 4-6 characters with distinct roles and motivations
- Hidden truths should be revealed gradually across arcs
- The story should support replaying for different routes
- Genre: {concept.get('genre', DEFAULT_GENRE)}
"""


def _arc_plan_prompt(bible: dict[str, Any], arc_start: int, arc_end: int, arc_index: int, total_arcs: int) -> str:
    book = bible["book"]
    story = bible["story_bible"]
    characters = book["characters"]
    char_ids = [c["id"] for c in characters]

    return f"""You are a professional interactive fiction story planner for LifeScript.

Book: {book['title']} (ID: {book['id']}, Genre: {book['genre']})
Premise: {story['premise']}
Mainline goal: {story['mainline_goal']}
Hidden truths: {json.dumps(story['hidden_truths'], ensure_ascii=False)}
Characters: {json.dumps([c['name'] + '(' + c['role'] + ')' for c in characters], ensure_ascii=False)}
Character IDs: {char_ids}

This is arc {arc_index + 1} of {total_arcs}, covering chapters {arc_start} to {arc_end}.

Plan this arc's chapters. Output ONLY a JSON array of chapter card objects, no markdown.

Each chapter card:
{{
  "number": <chapter number>,
  "title": "<chapter title in Chinese ≤ 12 chars>",
  "arc_phase": "<opening|rising|climax|resolution>",
  "chapter_goal": "<what must be achieved in this chapter>",
  "primary_emotion": "<the dominant emotion: 紧张|兴奋|心疼|愤怒|震惊|期待|爽快>",
  "key_events": ["<3 key beats>"],
  "main_conflict": "<the central tension>",
  "featured_characters": ["<character ids from: {char_ids}>"],
  "choice_themes": ["<2-3 choice themes e.g. 直接对抗|借势|隐忍>"],
  "ending_hook": "<1 sentence hook for next chapter>",
  "is_milestone": <true if this chapter is a major turning point>
}}

Arc structure guidelines:
- Arc {arc_index + 1}: {'Build foundation, first power-ups, establish core conflicts' if arc_index == 0 else 'Escalate stakes and complications' if arc_index < total_arcs // 2 else 'Resolution and payoffs'}
- Every 10 chapters should have at least 1 milestone chapter
- Vary the primary_emotion across consecutive chapters
- The arc should end with a strong hook into the next arc
"""


def _chapter_prompt(
    bible: dict[str, Any],
    card: dict[str, Any],
    prev_hook: str,
    book_id: str,
) -> str:
    book = bible["book"]
    characters = book["characters"]
    char_info = "\n".join(
        f"  {c['id']}: {c['name']} ({c['title']}, {c['role']}) — {c['description']}"
        for c in characters
    )

    ch_num = card["number"]
    ch_id = f"{book_id}_ch{ch_num:04d}"

    return f"""You are a professional interactive fiction writer for LifeScript, a Chinese mobile reading app.
Your writing must be IMMERSIVE and RICH — each chapter should feel like a full novel chapter, not a summary.

Book: {book['title']} (ID: {book_id})
Genre: {book['genre']}
Premise: {bible['story_bible']['premise']}

Characters:
{char_info}

Previous chapter ended with:
{prev_hook or '(This is the first chapter.)'}

Chapter to write:
  Number : {ch_num}
  ID     : {ch_id}
  Title  : {card['title']}
  Goal   : {card['chapter_goal']}
  Emotion: {card['primary_emotion']}
  Conflict: {card['main_conflict']}
  Key events: {json.dumps(card['key_events'], ensure_ascii=False)}
  Featured characters: {card.get('featured_characters', [])}
  Choice themes: {card.get('choice_themes', [])}
  Next hook: {card['ending_hook']}

SCHEMA:
{CHAPTER_SCHEMA}

Writing guidelines (CRITICAL):
- Each text node must be 200-400 Chinese characters of vivid, immersive prose
- Use sensory details: sights, sounds, smells, physical sensations
- Show character psychology through internal monologue and action, not just narration
- Dialogue nodes must include action beats and emotional subtext, not just words
- Build tension progressively within the chapter
- The total word count across all text+dialogue nodes must reach at least 5000 Chinese characters

Rules for THIS chapter:
- Chapter ID: {ch_id}
- book_id field: {book_id}
- is_paid: {'false' if ch_num <= 20 else 'true'}
- All node ids must start with "{ch_id}_"
- Character ids must come from: {[c['id'] for c in characters]}
- Stat values must be from: 战力|名望|谋略|财富|魅力|黑化值|天命值
- Relationship dimensions: 信任|好感|敌意|敬畏|依赖
- satisfaction_type must be one of: 直接爽|延迟爽|阴谋爽|碾压爽|情感爽|扮猪吃虎
- result_nodes inside choices must have ids like "{ch_id}_result_<choice_id>_<seq>"

Output ONLY the chapter JSON object. No markdown, no explanation.
"""


def _walkthrough_prompt(bible: dict[str, Any], arc_plans: list[list[dict[str, Any]]]) -> str:
    book = bible["book"]
    book_id = book["id"]
    all_cards = [card for arc in arc_plans for card in arc]
    total = book["total_chapters"]
    milestone_cards = [c for c in all_cards if c.get("is_milestone")]

    # Build arc summaries (not all individual cards — too large for big stories)
    arc_summaries = []
    for i, arc in enumerate(arc_plans):
        if arc:
            arc_milestones = [c["title"] for c in arc if c.get("is_milestone")]
            arc_summaries.append({
                "arc_index": i + 1,
                "chapters": f"{arc[0]['number']}-{arc[-1]['number']}",
                "count": len(arc),
                "opening_title": arc[0]["title"],
                "closing_title": arc[-1]["title"],
                "milestones": arc_milestones[:5],
                "dominant_phases": list({c["arc_phase"] for c in arc}),
            })

    # Stage count scales with story length
    num_stages = max(10, min(40, total // 50))

    return f"""You are a LifeScript content designer building the walkthrough / guide-map structure.

Book: {book['title']} (ID: {book_id})
Total chapters: {total}
Arcs: {len(arc_plans)}
Key milestones: {json.dumps([c['title'] for c in milestone_cards[:20]], ensure_ascii=False)}

Arc summaries:
{json.dumps(arc_summaries, ensure_ascii=False, indent=2)}

Generate the walkthrough object. Output ONLY valid JSON, no markdown.

Format:
{{
  "book_id": "{book_id}",
  "title": "<walkthrough map title>",
  "stages": [
    {{
      "id": "stage_<n>",
      "title": "<stage title>",
      "summary": "<1 sentence>",
      "chapter_range": "<e.g. 1-50>",
      "chapter_ids": ["<first 3 chapter ids as examples, then '...'>"]
    }}
  ],
  "milestone_guides": [
    {{
      "chapter_id": "<id of milestone chapter>",
      "stage_id": "<stage id>",
      "public_summary": "<what the player sees in the map (no spoilers)>",
      "objective": "<chapter objective>",
      "estimated_minutes": <5-10>,
      "interaction_count": <3>,
      "visible_routes": [
        {{
          "id": "route_<chapter>_<n>",
          "title": "<route name>",
          "style": "<satisfaction type>",
          "unlock_hint": "<stat/condition hint>",
          "payoff": "<what this route delivers>",
          "process_focus": "<what the player does>"
        }}
      ],
      "hidden_route_hint": "<spoiler-free hint about hidden content>"
    }}
  ],
  "default_chapter_guide_template": {{
    "estimated_minutes": 8,
    "interaction_count": 3,
    "visible_routes": [
      {{"id": "route_default_1", "title": "正面突破", "style": "直接爽", "unlock_hint": "战力足够", "payoff": "直接压制", "process_focus": "正面对抗"}},
      {{"id": "route_default_2", "title": "借力打力", "style": "阴谋爽", "unlock_hint": "谋略足够", "payoff": "以智取胜", "process_focus": "布局谋划"}}
    ],
    "hidden_route_hint": "高好感度可解锁隐藏对话"
  }}
}}

Rules:
- Create {num_stages} stages covering all {total} chapters
- chapter_ids in stages use format "{book_id}_ch<number:04d>"
- Only milestone chapters need full milestone_guides entries (roughly every 10 chapters)
- The default_chapter_guide_template applies to non-milestone chapters
- interaction_count must be 2 or 3
- visible_routes should have 2-3 entries per milestone guide
"""


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

class StoryGenerator:
    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    def _call(self, model: str, prompt: str, max_tokens: int = 4096) -> str:
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def _parse_json(self, text: str) -> Any:
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        return json.loads(text)

    def generate_bible(self, concept: dict[str, Any]) -> dict[str, Any]:
        print("  [1/4] Generating story bible...", flush=True)
        raw = self._call(MODEL_HEAVY, _bible_prompt(concept), max_tokens=4096)
        bible = self._parse_json(raw)
        # Override total_chapters from concept
        bible["book"]["total_chapters"] = concept["target_chapters"]
        bible["book"]["free_chapters"] = min(20, concept["target_chapters"])
        return bible

    def generate_arc_plan(
        self, bible: dict[str, Any], total_chapters: int, batch_size: int = 50
    ) -> list[list[dict[str, Any]]]:
        print(f"  [2/4] Planning {total_chapters} chapters in arcs of {batch_size}...", flush=True)
        arcs: list[list[dict[str, Any]]] = []
        arc_start = 1
        arc_index = 0

        while arc_start <= total_chapters:
            arc_end = min(arc_start + batch_size - 1, total_chapters)
            total_arcs = (total_chapters + batch_size - 1) // batch_size
            print(f"        Arc {arc_index + 1}/{total_arcs}: chapters {arc_start}-{arc_end}", flush=True)

            prompt = _arc_plan_prompt(bible, arc_start, arc_end, arc_index, total_arcs)
            raw = self._call(MODEL_HEAVY, prompt, max_tokens=12000)
            cards: list[dict[str, Any]] = self._parse_json(raw)

            # Ensure numbers are correct
            for i, card in enumerate(cards):
                card["number"] = arc_start + i

            arcs.append(cards)
            arc_start = arc_end + 1
            arc_index += 1
            time.sleep(0.5)  # Rate limit courtesy

        return arcs

    def generate_chapter(
        self,
        bible: dict[str, Any],
        card: dict[str, Any],
        prev_hook: str,
        book_id: str,
    ) -> dict[str, Any]:
        prompt = _chapter_prompt(bible, card, prev_hook, book_id)
        raw = self._call(MODEL_LIGHT, prompt, max_tokens=10000)
        chapter = self._parse_json(raw)
        return chapter

    def generate_chapters_batch(
        self,
        bible: dict[str, Any],
        cards: list[dict[str, Any]],
        prev_hooks: list[str],
        book_id: str,
        batch_size: int = 8,
    ) -> list[dict[str, Any]]:
        """Generate chapters sequentially in batches (rate-limit friendly)."""
        results: list[dict[str, Any]] = []
        total = len(cards)
        for i in range(0, total, batch_size):
            batch_cards = cards[i : i + batch_size]
            batch_hooks = prev_hooks[i : i + batch_size]
            for j, (card, hook) in enumerate(zip(batch_cards, batch_hooks)):
                ch_num = card["number"]
                print(f"        Chapter {ch_num}/{bible['book']['total_chapters']} — {card['title']}", flush=True)
                chapter = self.generate_chapter(bible, card, hook, book_id)
                results.append(chapter)
                time.sleep(0.2)
        return results

    def generate_walkthrough(
        self, bible: dict[str, Any], arc_plans: list[list[dict[str, Any]]]
    ) -> dict[str, Any]:
        print("  [4/4] Generating walkthrough...", flush=True)
        prompt = _walkthrough_prompt(bible, arc_plans)
        raw = self._call(MODEL_HEAVY, prompt, max_tokens=8000)
        return self._parse_json(raw)


# ---------------------------------------------------------------------------
# Progress management
# ---------------------------------------------------------------------------

class ProgressManager:
    def __init__(self, project_dir: Path) -> None:
        self.path = project_dir / "progress.json"

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def save(self, state: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_chapter(chapter: dict[str, Any], book_id: str) -> list[str]:
    errors: list[str] = []
    if chapter.get("book_id") != book_id:
        errors.append(f"ch{chapter.get('number')}: wrong book_id")

    choice_count = sum(1 for n in chapter.get("nodes", []) if "choice" in n)
    if choice_count == 0:
        errors.append(f"ch{chapter.get('number')}: no choice nodes")
    elif choice_count > 4:
        errors.append(f"ch{chapter.get('number')}: too many choice nodes ({choice_count})")

    for node in chapter.get("nodes", []):
        if "choice" in node:
            for ch_opt in node["choice"].get("choices", []):
                sat = ch_opt.get("satisfaction_type", "")
                if sat not in VALID_SATISFACTION:
                    errors.append(f"ch{chapter.get('number')}: invalid satisfaction_type '{sat}'")
                for se in ch_opt.get("stat_effects", []):
                    if se.get("stat") not in VALID_STATS:
                        errors.append(f"ch{chapter.get('number')}: invalid stat '{se.get('stat')}'")
                for re in ch_opt.get("relationship_effects", []):
                    if re.get("dimension") not in VALID_REL_DIMS:
                        errors.append(f"ch{chapter.get('number')}: invalid dimension '{re.get('dimension')}'")
    return errors


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(concept_path: Path, output_dir: Path, resume: bool) -> None:
    concept = json.loads(concept_path.read_text(encoding="utf-8"))
    book_id = concept["book_id"]
    target_chapters = concept.get("target_chapters", 500)

    project_dir = output_dir / book_id
    project_dir.mkdir(parents=True, exist_ok=True)

    pm = ProgressManager(project_dir)
    state = pm.load() if resume else {}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    gen = StoryGenerator(client)

    # ---- Phase 1: Story Bible ----
    if "bible" not in state:
        print(f"\n[{book_id}] Phase 1: Story Bible")
        bible = gen.generate_bible(concept)
        state["bible"] = bible
        pm.save(state)
    else:
        print(f"\n[{book_id}] Phase 1: Story Bible (loaded from checkpoint)")
        bible = state["bible"]

    # ---- Phase 2: Arc Plans ----
    if "arc_plans" not in state:
        print(f"\n[{book_id}] Phase 2: Arc Planning ({target_chapters} chapters)")
        arc_plans = gen.generate_arc_plan(bible, target_chapters, batch_size=50)
        state["arc_plans"] = arc_plans
        pm.save(state)
    else:
        print(f"\n[{book_id}] Phase 2: Arc Plans (loaded from checkpoint, {len(state['arc_plans'])} arcs)")
        arc_plans = state["arc_plans"]

    all_cards: list[dict[str, Any]] = [card for arc in arc_plans for card in arc]

    # ---- Phase 3: Chapter Generation ----
    generated_chapters: list[dict[str, Any]] = state.get("chapters", [])
    generated_count = len(generated_chapters)

    if generated_count < len(all_cards):
        print(f"\n[{book_id}] Phase 3: Chapter Generation ({generated_count}/{len(all_cards)} done)")

        # Build prev_hooks list
        prev_hooks: list[str] = [""]
        for ch in generated_chapters:
            prev_hooks.append(ch.get("next_chapter_hook", ""))

        remaining_cards = all_cards[generated_count:]
        remaining_hooks = prev_hooks[generated_count:]

        batch_size = 8
        for i in range(0, len(remaining_cards), batch_size):
            batch = remaining_cards[i : i + batch_size]
            hooks = remaining_hooks[i : i + batch_size]

            print(f"  Batch {i // batch_size + 1}: chapters {batch[0]['number']}-{batch[-1]['number']}")
            new_chapters = gen.generate_chapters_batch(bible, batch, hooks, book_id, batch_size=batch_size)

            # Validate
            all_errors: list[str] = []
            for ch in new_chapters:
                errs = validate_chapter(ch, book_id)
                all_errors.extend(errs)
            if all_errors:
                print(f"  [WARNINGS] {len(all_errors)} validation issues in this batch:")
                for e in all_errors[:5]:
                    print(f"    - {e}")

            generated_chapters.extend(new_chapters)

            # Update prev_hooks for next batch
            if generated_chapters:
                remaining_hooks = [generated_chapters[-1].get("next_chapter_hook", "")] + remaining_hooks[len(batch):]

            state["chapters"] = generated_chapters
            pm.save(state)
    else:
        print(f"\n[{book_id}] Phase 3: Chapters (all {generated_count} loaded from checkpoint)")

    # ---- Phase 4: Walkthrough ----
    if "walkthrough" not in state:
        print(f"\n[{book_id}] Phase 4: Walkthrough")
        walkthrough = gen.generate_walkthrough(bible, arc_plans)
        state["walkthrough"] = walkthrough
        pm.save(state)
    else:
        print(f"\n[{book_id}] Phase 4: Walkthrough (loaded from checkpoint)")
        walkthrough = state["walkthrough"]

    # ---- Phase 5: Assemble story_package.json ----
    print(f"\n[{book_id}] Phase 5: Assembly")

    # Sort chapters by number
    generated_chapters.sort(key=lambda c: c["number"])

    # Fix total_chapters
    bible["book"]["total_chapters"] = len(generated_chapters)

    story_package = {
        "book": bible["book"],
        "reader_desire_map": bible.get("reader_desire_map", {}),
        "story_bible": bible.get("story_bible", {}),
        "route_graph": bible.get("route_graph", {}),
        "walkthrough": walkthrough,
        "chapters": generated_chapters,
    }

    out_path = project_dir / "story_package.json"
    out_path.write_text(
        json.dumps(story_package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n✓ story_package.json written to {out_path}")
    print(f"  chapters: {len(generated_chapters)}")
    print(f"  total nodes: {sum(len(ch.get('nodes', [])) for ch in generated_chapters)}")
    print(f"\nNext step: compile with")
    print(f"  python3 scripts/compile_story_package.py {out_path} \\")
    print(f"    --resources-dir <LifeScript-iOS/Sources/LifeScript/Resources> \\")
    print(f"    --output-dir {project_dir}/build")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a LifeScript story package using Claude API.")
    parser.add_argument("concept", type=Path, help="Path to concept JSON file")
    parser.add_argument("--chapters", type=int, default=None, help="Override target chapter count")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent.parent / "projects",
        help="Directory to write the project (default: story-factory/projects/)",
    )
    args = parser.parse_args()

    if not args.concept.exists():
        sys.exit(f"Concept file not found: {args.concept}")

    concept = json.loads(args.concept.read_text(encoding="utf-8"))
    if args.chapters:
        concept["target_chapters"] = args.chapters

    run(args.concept, args.output_dir, args.resume)


if __name__ == "__main__":
    main()

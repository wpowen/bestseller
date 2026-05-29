"""Build inventory_books_core.jsonl from books_core cards.

Mechanical mapping (category → craft_function) + framework_binding → physical
artifact path. Cards where category alone is ambiguous (outline / longform_control
/ theme / worldview) get a heuristic refinement based on framework_bindings and
core_claim keywords, and are tagged with `needs_review: true` for human pass.

Run from repo root:
    python scripts/methodology_unified/build_books_core_inventory.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = REPO_ROOT / "data/methodology_sources/books_core/cards.yaml"
OUTPUT_PATH = REPO_ROOT / "data/methodology_unified/inventory_books_core.jsonl"

# Direct category → craft_function (unambiguous).
DIRECT_CATEGORY_MAP: dict[str, str] = {
    "opening": "opening_three_function",
    "revision": "revision_repair_engine",
    "scene_design": "scene_causality_engine",
    "pov": "pov_distance_controller",
    "character": "character_change_tracker",
    "dialogue": "dialogue_subtext_engine",
    "surface_subtext": "dialogue_subtext_engine",
    "emotion_beat": "emotion_pressure_engine",
    "prose_style": "pov_distance_controller",
    "progression": "pacing_compression_engine",
    "timeline": "payoff_ledger",
    "foreshadowing": "payoff_ledger",
}

# Ambiguous categories — resolved by framework_bindings + keyword heuristics.
AMBIGUOUS_CATEGORIES = {"outline", "longform_control", "theme", "worldview"}

# framework_binding → physical artifact path in current codebase.
BINDING_TO_ARTIFACT: dict[str, list[str]] = {
    "outline": ["chapter_outline", "book_outline"],
    "scene_card": ["scene_contract"],
    "scene_design": ["scene_contract"],
    "character_arc": ["cast.character_arcs", "chapter_contract.character_delta"],
    "revision_queue": ["RewriteTask", "chapter_block_recovery"],
    "chapter_review": ["reviews.py"],
    "quality_gate": ["gate_registry"],
    "draft_prompt": ["drafts.py:render_scene_prompt"],
    "methodology_compiler": ["methodology_compiler.py"],
    "dialogue_scene": ["scene_contract.dialogue_spec"],
    "prose_style": ["scene_contract.prose_style"],
    "dialogue_review": ["reviews.py:dialogue_judge"],
    "snowflake_expansion": ["planner.py:snowflake"],
    "project_health": ["methodology_health.py"],
    "revision": ["chapter_block_recovery"],
}

# craft_function → likely indicator_targets (Step 6 reverse-lookup).
CRAFT_TO_INDICATORS: dict[str, list[str]] = {
    "emotion_pressure_engine": ["combined_quality_score", "compression_ratio_compliance"],
    "scene_causality_engine": ["scene_causality_score"],
    "pov_distance_controller": ["pov_stability_score", "pov_distance_drift_ratio"],
    "hook_ledger": ["hook_ledger_closure_rate", "ending_hook_score"],
    "payoff_ledger": ["setup_payoff_score", "payoff_ledger_closure_rate"],
    "dialogue_subtext_engine": ["dialogue_subtext_score", "dialogue_ratio"],
    "character_change_tracker": ["character_want_need_coverage", "character_change_score"],
    "opening_three_function": ["combined_quality_score"],
    "ending_hook_engine": ["ending_hook_score"],
    "pacing_compression_engine": ["combined_quality_score", "chapter_length_compliance"],
    "revision_repair_engine": ["repair_trigger_rate"],
    "project_health_monitor": ["combined_quality_score"],
}


def resolve_ambiguous_category(card: dict[str, Any]) -> tuple[str, bool]:
    """Heuristic for outline / longform_control / theme / worldview.

    Returns (craft_function, needs_review).
    """
    category = card.get("category", "")
    bindings = set(card.get("framework_bindings", []))
    claim = (card.get("core_claim") or "").lower()
    title = (card.get("title") or "").lower()
    text = f"{claim} {title}"

    # Keyword indicators.
    is_payoff = any(k in text for k in ["setup", "payoff", "foreshadow", "promise",
                                         "reveal", "echo", "callback", "伏笔", "兑现",
                                         "回收", "偿付"])
    is_hook = any(k in text for k in ["hook", "cliffhanger", "tension", "钩子",
                                       "悬念", "断章", "next chapter"])
    is_health = any(k in text for k in ["long-form", "series", "pacing across",
                                          "long arc", "长篇", "整体", "health"])
    is_pacing = any(k in text for k in ["pacing", "rhythm", "tempo", "节奏",
                                          "compression", "breathing"])
    is_scene_causal = any(k in text for k in ["scene", "causal", "goal", "obstacle",
                                                "consequence", "action", "reaction"])
    is_character = any(k in text for k in ["character arc", "want", "need",
                                             "internal change", "growth", "弧线",
                                             "成长"])
    is_pov = any(k in text for k in ["pov", "point of view", "perspective",
                                       "viewpoint", "视角"])

    if category == "outline":
        # outline cards: split between scene_causality (most), payoff (foreshadow),
        # pacing (rhythm), character (arc).
        if is_payoff:
            return "payoff_ledger", False
        if is_hook:
            return "hook_ledger", False
        if is_character:
            return "character_change_tracker", False
        if is_pacing:
            return "pacing_compression_engine", False
        if is_scene_causal:
            return "scene_causality_engine", False
        return "scene_causality_engine", True  # default + flag

    if category == "longform_control":
        # mostly project_health / pacing / payoff
        if is_payoff:
            return "payoff_ledger", False
        if is_pacing:
            return "pacing_compression_engine", False
        if is_health:
            return "project_health_monitor", False
        return "project_health_monitor", True

    if category == "theme":
        # theme cards: usually character (want/need/internal change) or
        # scene_causality (theme via choice consequence).
        if is_character:
            return "character_change_tracker", False
        if is_scene_causal:
            return "scene_causality_engine", False
        return "character_change_tracker", True

    if category == "worldview":
        # worldview cards: usually pov (world filter) or scene_causality
        # (world rules enforce causality).
        if is_pov:
            return "pov_distance_controller", False
        if is_scene_causal:
            return "scene_causality_engine", False
        return "scene_causality_engine", True

    return "_uncategorized", True


def derive_artifacts(card: dict[str, Any], craft: str) -> list[str]:
    out: list[str] = []
    for b in card.get("framework_bindings", []):
        for art in BINDING_TO_ARTIFACT.get(b, []):
            if art not in out:
                out.append(art)
    # Always pin slot artifact for known crafts (Step 2 alignment).
    slot_artifact = f"slots.{craft}"
    if slot_artifact not in out:
        out.append(slot_artifact)
    return out


def derive_indicators(card: dict[str, Any], craft: str) -> list[str]:
    out = list(CRAFT_TO_INDICATORS.get(craft, []))
    # required_contract_fields hint additional indicators.
    rcf = card.get("required_contract_fields") or []
    if any("hook" in f for f in rcf) and "hook_ledger_closure_rate" not in out:
        out.append("hook_ledger_closure_rate")
    if any("payoff" in f or "foreshadow" in f for f in rcf):
        if "setup_payoff_score" not in out:
            out.append("setup_payoff_score")
    if "anti_meta_leak_count" not in out and "ai" in (card.get("core_claim") or "").lower():
        out.append("anti_meta_leak_count")
    return out


def derive_coverage_status(card: dict[str, Any]) -> str:
    """books_core cards are profile-level. Runtime activation depends on
    methodology_book_selector selecting them. Until Step 5 lineage is in,
    treat them all as runtime_dormant (configured but not deterministically
    used)."""
    return "runtime_dormant"


def short_snippet(card: dict[str, Any]) -> str:
    claim = (card.get("core_claim") or "").strip()
    # truncate to 200 chars hard limit.
    if len(claim) > 200:
        # try to cut at sentence boundary
        cut = claim.rfind("。", 0, 200)
        if cut < 100:
            cut = claim.rfind(". ", 0, 200)
        if cut < 100:
            cut = 200
        claim = claim[:cut].rstrip() + "…"
    return claim


def build_rule_id(card_id: str) -> str:
    # card_id already like "books_core.source-0001.sec-0002.emotional_experience_core_001"
    # → "bc.source-0001.sec-0002.emotional_experience_core_001"
    return "bc." + card_id.replace("books_core.", "")


def slugify_for_cluster(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9 ]", "", text or "").lower().strip()
    parts = [p for p in text.split() if p][:3]
    return "cl_bc_" + "_".join(parts) if parts else "cl_bc_misc"


def transform(card: dict[str, Any]) -> dict[str, Any]:
    category = card.get("category", "_none")
    needs_review = False
    if category in DIRECT_CATEGORY_MAP:
        craft = DIRECT_CATEGORY_MAP[category]
    elif category in AMBIGUOUS_CATEGORIES:
        craft, needs_review = resolve_ambiguous_category(card)
    else:
        craft = "_uncategorized"
        needs_review = True

    out = {
        "rule_id": build_rule_id(card["id"]),
        "source": "books_core",
        "source_path": card.get("source_ids", ["?"])[0],
        "title": card.get("title", "").split(" / ")[-1] or card["id"].split(".")[-1],
        "craft_function": craft,
        "binding_stage": list(card.get("stage", [])),
        "binding_artifact": derive_artifacts(card, craft),
        "indicator_targets": derive_indicators(card, craft),
        "text_snippet": short_snippet(card),
        "coverage_status": derive_coverage_status(card),
        "similarity_cluster_id": slugify_for_cluster(card.get("title", "")),
    }
    if card.get("anti_patterns"):
        out["anti_patterns"] = card["anti_patterns"][:3]  # cap to 3
    if needs_review:
        out["needs_review"] = True
        out["notes"] = f"ambiguous category={category}; heuristic resolved to {craft}"
    return out


def main() -> int:
    with SOURCE_PATH.open() as f:
        data = yaml.safe_load(f)
    cards = data["cards"]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    review_count = 0
    craft_counter: dict[str, int] = {}
    with OUTPUT_PATH.open("w") as f:
        for c in cards:
            rec = transform(c)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("needs_review"):
                review_count += 1
            craft_counter[rec["craft_function"]] = craft_counter.get(rec["craft_function"], 0) + 1

    print(f"wrote {len(cards)} cards to {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"needs_review: {review_count}")
    print("by craft_function:")
    for k, v in sorted(craft_counter.items(), key=lambda kv: -kv[1]):
        print(f"  {k:36s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

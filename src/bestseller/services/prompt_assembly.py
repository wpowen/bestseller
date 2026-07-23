"""Writer prompt assembly layers + instruction priority (quality remediation W1).

Maps context section keys into four layers used for budgeting, diagnostics,
and conflict-resolution order. Soft / additive: unused when callers ignore it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from bestseller.services.prompt_compiler import (
    CompiledPrompt,
    PromptBlock,
    PromptBudgetError,
    PromptCompilerReport,
    PromptConflictError,
    compile_prompt,
    estimate_prompt_tokens,
)

# Layer names (stable API for tests / telemetry)
LAYER_HARD_CANON = "HARD_CANON"
LAYER_SCENE_SPEC = "SCENE_SPEC"
LAYER_CRAFT_BRIEF = "CRAFT_BRIEF"
LAYER_OPTIONAL = "OPTIONAL"

# Section key → layer. Keys not listed fall into OPTIONAL.
_SECTION_LAYER: dict[str, str] = {
    # HARD_CANON — inviolable continuity / length / identity
    "contract_section": LAYER_HARD_CANON,
    "current_scene_contract_line": LAYER_HARD_CANON,
    "scene_word_budget_line": LAYER_HARD_CANON,
    "chapter_length_line": LAYER_HARD_CANON,
    "canon_guardrails_line": LAYER_HARD_CANON,
    "timeline_canon_line": LAYER_HARD_CANON,
    "scene_coherence_line": LAYER_HARD_CANON,
    "character_role_line": LAYER_HARD_CANON,
    "identity_line": LAYER_HARD_CANON,
    "hard_fact_line": LAYER_HARD_CANON,
    "knowledge_line": LAYER_HARD_CANON,
    "participant_fact_section": LAYER_HARD_CANON,
    "contradiction_line": LAYER_HARD_CANON,
    "acceptance_duty_line": LAYER_HARD_CANON,
    "hook_echo_line": LAYER_HARD_CANON,
    "volume_contract_line": LAYER_HARD_CANON,
    # SCENE_SPEC — what this scene must deliver
    "story_principle_line": LAYER_SCENE_SPEC,
    "plan_richness_line": LAYER_SCENE_SPEC,
    "qimao_opening_contract_line": LAYER_SCENE_SPEC,
    "reader_contract_line": LAYER_SCENE_SPEC,
    "concept_lab_contract_line": LAYER_SCENE_SPEC,
    "hype_constraints_line": LAYER_SCENE_SPEC,
    "scene_beat_line": LAYER_SCENE_SPEC,
    "scene_scope_isolation_line": LAYER_SCENE_SPEC,
    "structure_beat_line": LAYER_SCENE_SPEC,
    "pacing_line": LAYER_SCENE_SPEC,
    "tension_target_line": LAYER_SCENE_SPEC,
    "ending_line": LAYER_SCENE_SPEC,
    "scene_sequel_line": LAYER_SCENE_SPEC,
    # CRAFT_BRIEF — methodology / craft (budgeted, single channel preferred)
    "methodology_line": LAYER_CRAFT_BRIEF,
    "pp_line": LAYER_CRAFT_BRIEF,
    "pp_writer_line": LAYER_CRAFT_BRIEF,
    "genre_constraint_line": LAYER_CRAFT_BRIEF,
    "ranking_profile_line": LAYER_CRAFT_BRIEF,
    "chapter_market_constraints_line": LAYER_CRAFT_BRIEF,
    "voice_dna_line": LAYER_CRAFT_BRIEF,
    "dialogue_voice_line": LAYER_CRAFT_BRIEF,
    "signature_scene_line": LAYER_CRAFT_BRIEF,
    "l3_prompt_line": LAYER_CRAFT_BRIEF,
    # OPTIONAL — enrichment (trim first)
    "recent_scene_section": LAYER_OPTIONAL,
    "emotion_track_section": LAYER_OPTIONAL,
    "antagonist_plan_section": LAYER_OPTIONAL,
    "clue_section": LAYER_OPTIONAL,
    "story_bible_section": LAYER_OPTIONAL,
    "arc_section": LAYER_OPTIONAL,
    "arc_summary_line": LAYER_OPTIONAL,
    "world_snapshot_line": LAYER_OPTIONAL,
    "retrieval_section": LAYER_OPTIONAL,
    "recent_timeline_section": LAYER_OPTIONAL,
    "reader_knowledge_line": LAYER_OPTIONAL,
    "relationship_line": LAYER_OPTIONAL,
    "subplot_line": LAYER_OPTIONAL,
    "obligations_line": LAYER_OPTIONAL,
    "foreshadow_line": LAYER_OPTIONAL,
    "tree_section": LAYER_OPTIONAL,
    "opening_diversity_line": LAYER_OPTIONAL,
    "conflict_diversity_line": LAYER_OPTIONAL,
    "scene_purpose_line": LAYER_OPTIONAL,
    "env_diversity_line": LAYER_OPTIONAL,
    "arc_beat_line": LAYER_OPTIONAL,
    "five_layer_line": LAYER_OPTIONAL,
    "cliffhanger_line": LAYER_OPTIONAL,
    "location_ledger_line": LAYER_OPTIONAL,
    "budget_diversity_line": LAYER_OPTIONAL,
    "phrase_avoidance_line": LAYER_OPTIONAL,
    "prior_persona_feedback_line": LAYER_OPTIONAL,
    "exposition_density_line": LAYER_OPTIONAL,
    "library_reference_line": LAYER_OPTIONAL,
    "project_material_reference_line": LAYER_OPTIONAL,
    "project_material_obligation_line": LAYER_OPTIONAL,
    "progression_context_line": LAYER_OPTIONAL,
    "decision_policy_line": LAYER_OPTIONAL,
    "rule_system_line": LAYER_OPTIONAL,
    "faction_ecology_line": LAYER_OPTIONAL,
    "relationship_agency_line": LAYER_OPTIONAL,
    "entry_system_line": LAYER_OPTIONAL,
    "entry_registry_line": LAYER_OPTIONAL,
    "entry_state_ledger_line": LAYER_OPTIONAL,
    "query_brief_line": LAYER_OPTIONAL,
}

_LAYER_ORDER = (
    LAYER_HARD_CANON,
    LAYER_SCENE_SPEC,
    LAYER_CRAFT_BRIEF,
    LAYER_OPTIONAL,
)


def section_layer(key: str) -> str:
    return _SECTION_LAYER.get(key, LAYER_OPTIONAL)


def estimate_tokens(text: str) -> int:
    """Compatibility alias for the compiler's deterministic estimator."""

    return estimate_prompt_tokens(text)


def resolve_selected_enhancer_keys(selection: object) -> tuple[str, ...]:
    """Return only creator-selected effect keys for prompt compilation.

    The adapter accepts the persisted metadata shape as well as the typed
    ``StoryEnhancerSelection`` without importing that service here.  Unknown
    or absent selections resolve to an empty tuple, so optional enhancer blocks
    cannot leak into an unrelated book.
    """

    if isinstance(selection, Mapping):
        raw = selection.get("effect_skills", ())
    else:
        raw = getattr(selection, "effect_skills", ())
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


@dataclass(frozen=True)
class LayerBudgetReport:
    layer: str
    section_count: int
    kept_tokens: int
    dropped_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptAssemblyReport:
    """Diagnostics for a single scene-writer assembly."""

    budget_tokens: int
    total_kept_tokens: int
    layers: tuple[LayerBudgetReport, ...]
    dropped_keys: tuple[str, ...] = ()
    mode: str = "lean"

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_tokens": self.budget_tokens,
            "total_kept_tokens": self.total_kept_tokens,
            "mode": self.mode,
            "dropped_keys": list(self.dropped_keys),
            "layers": [asdict(layer) for layer in self.layers],
        }


def adapt_compiler_report(
    report: PromptCompilerReport,
    *,
    mode: str = "compiled",
) -> PromptAssemblyReport:
    """Adapt a typed compiler report to the legacy diagnostics surface.

    This is a pure adapter: it receives the report explicitly and never reads
    or writes process-global "last report" state.
    """

    return PromptAssemblyReport(
        budget_tokens=report.budget_tokens,
        total_kept_tokens=report.total_tokens,
        layers=(),
        dropped_keys=report.dropped,
        mode=mode,
    )


def build_prompt_assembly_report(
    sections_before: Mapping[str, str],
    sections_after: Mapping[str, str],
    *,
    budget_tokens: int,
    mode: str = "lean",
) -> PromptAssemblyReport:
    """Compare pre/post budget section maps and emit a layer report."""

    dropped: list[str] = []
    for key, before in sections_before.items():
        after = sections_after.get(key, "")
        if (before or "").strip() and not (after or "").strip():
            dropped.append(key)
        elif len(after or "") + 40 < len(before or "") and (before or "").strip():
            # truncated counts as soft-drop for diagnostics
            if key not in dropped:
                dropped.append(f"{key}:truncated")

    layer_stats: dict[str, dict[str, Any]] = {
        name: {"count": 0, "tokens": 0, "dropped": []} for name in _LAYER_ORDER
    }
    total = 0
    for key, text in sections_after.items():
        if not (text or "").strip():
            continue
        layer = section_layer(key)
        tok = estimate_tokens(text)
        layer_stats[layer]["count"] += 1
        layer_stats[layer]["tokens"] += tok
        total += tok
    for key in dropped:
        base = key.split(":", 1)[0]
        layer = section_layer(base)
        layer_stats[layer]["dropped"].append(key)

    layers = tuple(
        LayerBudgetReport(
            layer=name,
            section_count=int(layer_stats[name]["count"]),
            kept_tokens=int(layer_stats[name]["tokens"]),
            dropped_keys=tuple(layer_stats[name]["dropped"]),
        )
        for name in _LAYER_ORDER
    )
    return PromptAssemblyReport(
        budget_tokens=int(budget_tokens),
        total_kept_tokens=total,
        layers=layers,
        dropped_keys=tuple(dropped),
        mode=mode,
    )


def render_instruction_priority_block(*, is_en: bool) -> str:
    """Explicit conflict-resolution order for writer system prompts."""

    if is_en:
        return (
            "# CONSTRAINTS · Instruction priority (when rules conflict)\n"
            "Apply in this order; lower items yield to higher ones:\n"
            "1. Word-count hard band (min/target/max) and output format\n"
            "2. Cold-reader opening: who / where / what in the first ~200 chars\n"
            "3. Peak moments: show-don't-tell (action/body/subtext, not emotion labels)\n"
            "4. Anti-AI-slop / anti-meta (no template phrases, no planning tags)\n"
            "5. Genre reaction amplification — only when the pack is shuangwen-style; "
            "otherwise optional, never force crowd face-slap scripts\n"
            "6. Craft flourishes (metaphor, rhythm) — never override 1–4\n"
        )
    return (
        "# CONSTRAINTS · 指令优先级（冲突时按序服从，低位让高位）\n"
        "1. **字数硬带**（下限/目标/上限）与输出格式\n"
        "2. **开场冷读**：前约 200 字内可答 who / where / what\n"
        "3. **高光展示不讲述**：冲突/转折用动作·身体·潜台词，禁止情绪标签\n"
        "4. **反 AI 腔 / 反元信息**：禁套话、禁策划标签\n"
        "5. **反应放大**：仅爽文/升级流 pack 强调；治愈/文学/慢热题材不强制围观打脸\n"
        "6. **文采修辞**：不得压过 1–4\n"
    )


# Genre families that should keep hard "reaction amplification" on review.
_SHUANGWEN_REACTION_GENRE_HINTS: tuple[str, ...] = (
    "xianxia",
    "xuanhuan",
    "upgrade",
    "power",
    "urban-power",
    "system",
    "litrpg",
    "progression",
    "shuangwen",
    "修仙",
    "玄幻",
    "升级",
    "系统",
    "爽文",
    "都市异能",
    "都市修仙",
)


def genre_wants_reaction_amplification(
    genre: str | None,
    sub_genre: str | None = None,
    prompt_pack_key: str | None = None,
) -> bool:
    blob = " ".join(str(part or "").lower() for part in (genre, sub_genre, prompt_pack_key))
    if not blob.strip():
        return True  # unknown → keep legacy default (commercial webnovel bias)
    return any(hint in blob for hint in _SHUANGWEN_REACTION_GENRE_HINTS)


# Private book terms that must never re-enter generic production paths. Keep
# the opaque code-point representation here so a universal source literal does
# not itself become a cross-book leakage vector; project data owns display text.
PRIVATE_BOOK_TERM_BANLIST: tuple[str, ...] = tuple(
    "".join(map(chr, codepoints))
    for codepoints in (
        (38236, 20538), (22256, 39746, 38236), (38738, 22218, 19981, 35821),
        (26519, 27491, 28103), (23432, 38236, 20154), (25187, 36134, 20154),
        (19977, 30701, 19968, 38271), (31532, 20843, 24352, 33080),
    )
)


__all__ = [
    "LAYER_CRAFT_BRIEF",
    "LAYER_HARD_CANON",
    "LAYER_OPTIONAL",
    "LAYER_SCENE_SPEC",
    "PRIVATE_BOOK_TERM_BANLIST",
    "CompiledPrompt",
    "LayerBudgetReport",
    "PromptAssemblyReport",
    "PromptBlock",
    "PromptBudgetError",
    "PromptCompilerReport",
    "PromptConflictError",
    "adapt_compiler_report",
    "build_prompt_assembly_report",
    "compile_prompt",
    "estimate_tokens",
    "genre_wants_reaction_amplification",
    "render_instruction_priority_block",
    "resolve_selected_enhancer_keys",
    "section_layer",
]

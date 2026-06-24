from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
import json
import logging
import math
import os
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import ChapterWriterContextPacket, SceneWriterContextPacket
from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.domain.fanqie_short import is_fanqie_short_project
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    CharacterModel,
    ChaseDebtModel,
    OverrideContractModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
)
from bestseller.services.canon_guardrails import load_canon_guardrails_for_project
from bestseller.services.chapter_constraint_manifest import (
    PrewritePlan,
    build_safe_prewrite_plan,
    compile_chapter_constraint_manifest,
    normalize_prewrite_plan_for_manifest,
    parse_prewrite_plan,
    render_constraint_manifest_block,
    render_prewrite_plan_block,
    render_prewrite_plan_prompt,
    validate_prewrite_plan,
)
from bestseller.services.chapter_outline_readiness_gate import chapter_scene_budget_sum_thresholds
from bestseller.services.chapter_quality_bundle import (
    ChapterQualityBundleContext,
    ChapterQualityBundleReport,
    run_chapter_quality_bundle,
)
from bestseller.services.chapter_validator import classify_cliffhanger
from bestseller.services.character_intelligence.optimizer import (
    optimize_project_character_profiles,
)
from bestseller.services.context import (
    build_chapter_writer_context,
    build_scene_writer_context_from_models,
)
from bestseller.services.concept_lab import render_concept_lab_prompt_block
from bestseller.services.dialogue_personality_bridge import (
    render_dialogue_personality_bridge_block,
)
from bestseller.services.story_enhancers import render_story_enhancer_writer_block
from bestseller.services.diversity_budget import (
    load_diversity_budget,
    save_diversity_budget,
)
from bestseller.services.invariants import InvariantSeedError, invariants_from_dict
from bestseller.services.length_stability_gate import (
    CHINESE_CHAPTER_HARD_MAX_WORDS,
    CHINESE_CHAPTER_HARD_MIN_WORDS,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology import (
    render_methodology_scene_rules,
    render_qimao_opening_contract_block,
)
from bestseller.services.methodology_compiler import (
    ChapterPosition,
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.methodology_lineage import render_methodology_lineage_prompt_block
from bestseller.services.methodology_overlay import (
    render_overlay_prompt_block,
    resolve_methodology_contract_mode,
)
from bestseller.services.methodology_profile import render_configured_methodology_profile_block
from bestseller.services.acceptance_contract import render_scene_acceptance_block
from bestseller.services.naming_normalizer import normalize_out_of_pool_names
from bestseller.services.output_validator import (
    NamingConsistencyCheck,
    OutputValidator,
    QualityReport,
    ValidationContext,
    Violation,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.prompt_constructor import (
    build_opening_hook_directive,
    render_fanqie_market_craft_profile_block,
)
from bestseller.services.prompt_packs import (
    render_methodology_block,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.quality_closure import evaluate_quality_closure
from bestseller.services.quality_gates_config import (
    build_validator_from_config,
    get_quality_gates_config,
)
from bestseller.services.quality_levers import (
    WriterLeverContext,
    build_writer_quality_levers_block,
    extract_quality_levers_meta,
)
from bestseller.services.quality_levers.character_engine import (
    collect_forbidden_words_from_profiles,
    collect_signature_words_from_profiles,
    render_character_engine_profile_block,
)
from bestseller.services.quality_levers.detectors import audit_chapter
from bestseller.services.quality_repair_playbooks import render_quality_repair_playbooks
from bestseller.services.regen_loop import (
    DEFAULT_BUDGET_PER_CHAPTER,
    GlobalBudget,
    RegenerationExhausted,
    regenerate_until_valid,
)
from bestseller.services.story_bible import load_scene_story_bible_context
from bestseller.services.word_targets import (
    model_output_token_ceiling,
    model_reasoning_token_reserve,
    resolve_llm_role_max_tokens,
    resolve_llm_role_model,
    word_target_policy,
)
from bestseller.services.write_gate import filter_blocking
from bestseller.services.write_safety_gate import WriteSafetyFinding
from bestseller.services.writing_profile import (
    is_english_language,
    normalize_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings, load_settings

_REPAIR_CODE_ALIASES: dict[str, str] = {
    "CHAPTER_LENGTH_BLOCK_LOW": "BLOCK_LOW",
    "LENGTH_UNDER": "BLOCK_LOW",
    "CHAPTER_TOO_SHORT": "BLOCK_LOW",
    "CHAPTER_BELOW_TARGET": "BLOCK_LOW",
    "CHAPTER_LENGTH_BLOCK_HIGH": "BLOCK_HIGH",
    "LENGTH_OVER": "BLOCK_HIGH",
}


def _resolve_prompt_pack_key(project: ProjectModel) -> str | None:
    """Resolve prompt pack key from project metadata."""

    meta = getattr(project, "metadata_json", None) or {}
    if not isinstance(meta, dict):
        return None
    explicit = meta.get("prompt_pack_key")
    if explicit:
        return str(explicit)
    market = meta.get("market")
    if isinstance(market, dict) and market.get("prompt_pack_key"):
        return str(market["prompt_pack_key"])
    return None


def _infer_chapter_position(project: ProjectModel, chapter: ChapterModel) -> ChapterPosition:
    """Infer a coarse chapter position for methodology slicing."""

    total = max(int(getattr(project, "target_chapters", None) or 100), 1)
    n = max(int(getattr(chapter, "chapter_number", None) or 1), 1)
    if n <= 3:
        return ChapterPosition.OPENING
    if n <= max(5, total // 5):
        return ChapterPosition.EARLY
    if n >= max(1, total - 3):
        return ChapterPosition.ENDGAME
    if n >= max(1, int(total * 0.85)):
        return ChapterPosition.CLIMAX
    return ChapterPosition.MIDGAME


def _canonical_repair_code(code: str) -> str:
    text = str(code).strip()
    return _REPAIR_CODE_ALIASES.get(text, text)


def _length_direction_from_payload(payload: Mapping[str, Any] | None) -> str | None:
    data = dict(payload or {})
    issue_code = _canonical_repair_code(str(data.get("issue_code") or ""))
    if issue_code in {"BLOCK_LOW", "BLOCK_HIGH"}:
        return issue_code
    try:
        word_count = int(data.get("word_count") or 0)
        target_words = int(data.get("target_words") or 0)
    except (TypeError, ValueError):
        return None
    if word_count <= 0 or target_words <= 0:
        return None
    return "BLOCK_LOW" if word_count < target_words else "BLOCK_HIGH"


def _drop_conflicting_length_repair_codes(
    codes: Iterable[str],
    *,
    length_payload: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Drop stale opposite length directives before preparing auto-repair.

    Quality bundle and legacy L4 reports can both stamp length-like codes on a
    chapter.  If an old ``CHAPTER_TOO_SHORT`` survives while the latest report
    says ``LENGTH_OVER``, the repair prompt simultaneously asks the writer to
    expand and compress, causing the chapter to oscillate across attempts.
    """

    ordered = tuple(str(code) for code in codes if str(code).strip())
    canonical = {_canonical_repair_code(code) for code in ordered}
    if not {"BLOCK_LOW", "BLOCK_HIGH"} <= canonical:
        return ordered

    preferred = _length_direction_from_payload(length_payload)
    if preferred not in {"BLOCK_LOW", "BLOCK_HIGH"}:
        return ordered
    dropped = "BLOCK_HIGH" if preferred == "BLOCK_LOW" else "BLOCK_LOW"
    return tuple(code for code in ordered if _canonical_repair_code(code) != dropped)


_AUTO_REPAIR_EXTERNAL_QUALITY_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "high", "block", "blocker"}
)


def _metadata_external_quality_codes(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect fresh metadata findings that should enter chapter auto-repair.

    Quality bundle codes already flow through ``auto_repair_last_block_codes``.
    Deterministic post-write audits and retention findings can be stamped only
    as structured metadata, so this helper turns those current findings into
    the same code stream without requiring a schema change.
    """

    codes: list[str] = []
    audit = metadata.get("deterministic_audit_latest")
    if isinstance(audit, Mapping) and audit.get("passed") is False:
        findings = audit.get("findings")
        if isinstance(findings, Sequence) and not isinstance(findings, (str, bytes)):
            for finding in findings:
                if not isinstance(finding, Mapping):
                    continue
                severity = str(finding.get("severity") or "").strip().lower()
                if severity not in _AUTO_REPAIR_EXTERNAL_QUALITY_SEVERITIES:
                    continue
                code = str(finding.get("code") or "").strip()
                if code:
                    codes.append(code)

    return tuple(dict.fromkeys(codes))


DUPLICATE_CONTENT_BLOCK_CODE = "CROSS_CHAPTER_REPETITION"
INTRA_CHAPTER_DUPLICATE_BLOCK_CODE = "INTRA_CHAPTER_REPETITION"
CHAPTER_OPENING_REPETITION_BLOCK_CODE = "CHAPTER_OPENING_REPETITION"

_SCENE_AUTO_REPAIR_RESIDUE_KEYS: frozenset[str] = frozenset(
    {
        "auto_repair_adjusted_target_word_count",
        "auto_repair_block_codes",
        "auto_repair_hint",
        "auto_repair_length_scale",
        "auto_repair_min_scene_target_floor",
        "auto_repair_original_target_word_count",
        "auto_repair_scene_target_cap",
        "auto_repair_source_block_code",
        "auto_repair_target_word_count_clamped",
        "auto_repair_attempt",
    }
)


def _reset_scene_auto_repair_residue_for_attempt(scene: SceneCardModel) -> int | None:
    """Clear stale repair prompt residue and restore the original scene budget.

    Auto-repair hints are an execution artifact, not story source material.  If
    a candidate draft is rejected, the next attempt must start from the current
    scene contract plus the current blocking issues, not a concatenation of old
    emergency prompts.  When a previous length repair changed the scene target,
    restore the stored original before calculating the next attempt's budget.
    """

    metadata = dict(getattr(scene, "metadata_json", None) or {})
    restored_target: int | None = None
    try:
        original_target = int(metadata.get("auto_repair_original_target_word_count") or 0)
    except (TypeError, ValueError):
        original_target = 0
    if original_target > 0:
        scene.target_word_count = original_target
        restored_target = original_target

    next_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in _SCENE_AUTO_REPAIR_RESIDUE_KEYS
    }
    if next_metadata != metadata:
        scene.metadata_json = next_metadata
    return restored_target


# ---------------------------------------------------------------------------
# Per-scene auto-repair hard cap (WS-C3)
# ---------------------------------------------------------------------------
# ``scene.metadata_json["scene_auto_repair_total_attempts"]`` is a
# *cumulative*, scene-scoped counter that survives residue cleanup, the
# inner auto-repair loop, the outer project_repair loop, and the
# chapter_pipeline cross-run re-entry.  Once the counter reaches the
# configured cap, the scene is stamped with ``auto_accepted_with_debt=True``
# and the assembler keeps the prior draft — the cap MUST NOT cause
# ``machine_repair_required``.  See
# docs/质量回归修复-开发计划-20260602.md §WS-C3.

_SCENE_AUTO_REPAIR_TOTAL_ATTEMPTS_KEY = "scene_auto_repair_total_attempts"
_SCENE_AUTO_REPAIR_LAST_PASS_ID_KEY = "scene_auto_repair_last_pass_id"
_SCENE_AUTO_REPAIR_DEBT_KEY = "auto_accepted_with_debt"
_SCENE_AUTO_REPAIR_DEBT_CAP_KEY = "auto_accepted_with_debt_cap"
_SCENE_AUTO_REPAIR_DEBT_ATTEMPT_KEY = "auto_accepted_with_debt_at_attempt"
_SCENE_AUTO_REPAIR_DEBT_REASON_KEY = "auto_accepted_with_debt_reason"


def read_scene_auto_repair_counter(scene: SceneCardModel) -> int:
    """Return the cumulative number of auto-repair attempts for ``scene``.

    The counter is read from ``scene.metadata_json`` and defaults to 0 when
    the scene has never been put through the auto-repair loop.  Stale or
    non-numeric values are coerced to 0 to keep callers defensive against
    bad historical rows.
    """

    raw = (getattr(scene, "metadata_json", None) or {}).get(
        _SCENE_AUTO_REPAIR_TOTAL_ATTEMPTS_KEY
    )
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def bump_scene_auto_repair_counter(scene: SceneCardModel) -> int:
    """Increment the per-scene cumulative auto-repair counter by 1.

    Returns the *new* counter value.  The counter lives in scene metadata,
    is never reset by the residue cleanup helper, and is the authoritative
    signal for ``is_scene_at_auto_repair_cap``.  Production callers should
    not reset the counter; use ``reset_scene_auto_repair_counter`` only in
    deterministic replays / operator actions.
    """

    new_value = read_scene_auto_repair_counter(scene) + 1
    metadata = dict(getattr(scene, "metadata_json", None) or {})
    metadata[_SCENE_AUTO_REPAIR_TOTAL_ATTEMPTS_KEY] = new_value
    scene.metadata_json = metadata
    return new_value


def reset_scene_auto_repair_counter(scene: SceneCardModel) -> None:
    """Wipe the per-scene counter.

    Intended for deterministic replays and operator-driven re-runs.  The
    production auto-repair / project_repair / chapter_pipeline paths must
    not call this — the cap is the protective contract.
    """

    metadata = dict(getattr(scene, "metadata_json", None) or {})
    metadata.pop(_SCENE_AUTO_REPAIR_TOTAL_ATTEMPTS_KEY, None)
    scene.metadata_json = metadata


def is_scene_at_auto_repair_cap(
    scene: SceneCardModel,
    *,
    cap: int | None = None,
) -> bool:
    """Return True when ``scene`` has exhausted its rewrite budget.

    ``cap`` defaults to ``settings.pipeline.chapter_auto_repair_max_scene_rewrites``.
    Callers can override for unit tests.  ``cap <= 0`` disables the check
    (a configured cap of 0 keeps the historical unbounded behavior).
    """

    if cap is None:
        from bestseller.settings import get_settings  # noqa: PLC0415

        cap = int(
            get_settings().pipeline.chapter_auto_repair_max_scene_rewrites or 0
        )
    if cap <= 0:
        return False
    return read_scene_auto_repair_counter(scene) >= cap


def mark_scene_auto_accepted_with_debt(
    scene: SceneCardModel,
    *,
    cap: int,
    reason: str,
) -> None:
    """Stamp the scene so the assembler keeps the prior draft.

    Idempotent — calling twice does not reset the at-attempt counter; the
    first call's attempt is the one that tripped the cap.  The
    ``auto_accepted_with_debt`` flag is consumed by the project review
    overview so a human reviewer sees exactly which scenes reached the cap.
    """

    metadata = dict(getattr(scene, "metadata_json", None) or {})
    if metadata.get(_SCENE_AUTO_REPAIR_DEBT_KEY):
        return  # already marked; preserve original attempt/reason
    metadata[_SCENE_AUTO_REPAIR_DEBT_KEY] = True
    metadata[_SCENE_AUTO_REPAIR_DEBT_CAP_KEY] = int(cap)
    metadata[_SCENE_AUTO_REPAIR_DEBT_ATTEMPT_KEY] = read_scene_auto_repair_counter(
        scene
    )
    metadata[_SCENE_AUTO_REPAIR_DEBT_REASON_KEY] = str(reason or "").strip()[:2000]
    scene.metadata_json = metadata


def _resolve_scene_auto_repair_cap() -> int:
    """Read the per-scene cap from settings; never raises during repair prep."""

    try:
        from bestseller.settings import get_settings  # noqa: PLC0415

        cap = int(
            get_settings().pipeline.chapter_auto_repair_max_scene_rewrites or 0
        )
    except Exception:
        cap = 0
    return max(0, cap)


def scene_should_skip_auto_repair_reset(
    scene: SceneCardModel,
    *,
    block_codes: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Return True when ``scene`` has reached its per-scene cap.

    On True, callers must:
      * NOT reset ``scene.status`` to ``NEEDS_REWRITE`` (the assembler
        needs the prior draft to remain ``is_current``).
      * NOT invalidate the current scene draft.
      * Stamp ``auto_accepted_with_debt`` via
        :func:`mark_scene_auto_accepted_with_debt` so the project review
        report surfaces the cap.
    """

    cap = _resolve_scene_auto_repair_cap()
    if not is_scene_at_auto_repair_cap(scene, cap=cap):
        return False
    if not (getattr(scene, "metadata_json", None) or {}).get(
        _SCENE_AUTO_REPAIR_DEBT_KEY
    ):
        codes_text = ", ".join(str(c) for c in (block_codes or ())) or "n/a"
        mark_scene_auto_accepted_with_debt(
            scene,
            cap=cap,
            reason=(
                "per-scene auto-repair cap reached "
                f"(attempt {read_scene_auto_repair_counter(scene)}/{cap}); "
                f"preserving prior draft; block codes: {codes_text}"
            ),
        )
    return True


# ---------------------------------------------------------------------------
# R20 — chapter-level total scene-rounds budget (fail-fast mode)
# ---------------------------------------------------------------------------
# The per-scene cap (WS-C3 above) bounds each scene individually, but the
# combined topology (scene 3-eval × 2-rewrite × chapter 3 repair passes)
# still allows ~30 scene rounds per chapter.  When
# ``settings.pipeline.max_total_scene_rounds_per_chapter`` is set to a
# positive value, the chapter auto-repair loop stops as soon as the SUM of
# every scene's cumulative round counter reaches the budget; the known block
# codes are stamped into ``chapter.metadata_json["rounds_budget_exhausted"]``
# and the chapter follows the existing machine-repair route.  Default 0 keeps
# the historical (unbounded-by-total) behavior.

CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY = "rounds_budget_exhausted"


def total_chapter_scene_repair_rounds(
    scenes: Iterable[SceneCardModel],
) -> int:
    """Sum the cumulative auto-repair round counters across ``scenes``."""

    return sum(read_scene_auto_repair_counter(scene) for scene in scenes)


def _resolve_chapter_scene_rounds_budget() -> int:
    """Read ``pipeline.max_total_scene_rounds_per_chapter``; 0 = unlimited."""

    try:
        from bestseller.settings import get_settings  # noqa: PLC0415

        budget = int(
            getattr(
                get_settings().pipeline,
                "max_total_scene_rounds_per_chapter",
                0,
            )
            or 0
        )
    except Exception:
        budget = 0
    return max(0, budget)


def is_chapter_scene_rounds_budget_exhausted(
    scenes: Iterable[SceneCardModel],
    *,
    budget: int | None = None,
) -> bool:
    """Return True when the chapter's total scene-rounds budget is spent.

    ``budget`` defaults to ``settings.pipeline.max_total_scene_rounds_per_chapter``;
    a budget of 0 (the default) disables the check entirely so the historical
    behavior is preserved.
    """

    if budget is None:
        budget = _resolve_chapter_scene_rounds_budget()
    if budget <= 0:
        return False
    return total_chapter_scene_repair_rounds(scenes) >= budget


def mark_chapter_rounds_budget_exhausted(
    chapter: ChapterModel,
    *,
    block_codes: tuple[str, ...] | list[str],
    total_scene_rounds: int,
    budget: int,
) -> None:
    """Stamp ``chapter`` so it follows the existing machine-repair route.

    Writes the known block codes (plus the observed round count and budget)
    under ``rounds_budget_exhausted`` and sets ``requires_machine_repair`` —
    the same metadata contract the cross-run exhaustion path uses, so the
    downstream pipeline routing needs no new branches.
    """

    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    metadata[CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY] = {
        "block_codes": [str(c) for c in block_codes if c],
        "total_scene_rounds": int(total_scene_rounds),
        "budget": int(budget),
    }
    metadata["requires_machine_repair"] = True
    metadata["requires_human_review"] = False
    metadata["auto_repair_in_progress"] = False
    metadata["auto_accepted"] = False
    chapter.metadata_json = metadata
    chapter.production_state = "blocked"


# Chapter-level block codes that only a specific scene position can fix.
# When EVERY repair target is positional, resetting the other scenes destroys
# verified work for zero benefit — the 2026-06-11 run's ch9 (persona 0.80,
# retention passed) was regenerated wholesale for SIGNATURE_IMAGE_MISSING +
# ENDING_HOOK_MISSING and ended up machine-blocked.
_FIRST_SCENE_REPAIR_CODES = frozenset(
    {"HOOK_ECHO_MISSING", "HOOK_ECHO_LOW", "OPENING_PRESSURE_THIN"}
)
_LAST_SCENE_REPAIR_CODES = frozenset({"ENDING_HOOK_MISSING"})


def select_scenes_for_auto_repair(
    scenes: list[SceneCardModel],
    block_codes: tuple[str, ...] | list[str],
) -> list[SceneCardModel]:
    """Pick the scenes an auto-repair pass should actually reset.

    Positional codes (opening echo, opening pressure, ending hook) map to
    the first/last scene; any non-positional code keeps the legacy
    whole-chapter reset. Returns scenes in ascending scene order.
    """

    codes = {str(c) for c in block_codes if c}
    if not scenes or not codes:
        return list(scenes)
    positional = _FIRST_SCENE_REPAIR_CODES | _LAST_SCENE_REPAIR_CODES
    if codes - positional:
        return list(scenes)
    selected: list[SceneCardModel] = []
    if codes & _FIRST_SCENE_REPAIR_CODES:
        selected.append(scenes[0])
    if codes & _LAST_SCENE_REPAIR_CODES and scenes[-1] is not (
        selected[0] if selected else None
    ):
        selected.append(scenes[-1])
    return selected or list(scenes)


def _read_scene_last_pass_id(scene: SceneCardModel) -> int:
    """Return the last chapter-level auto-repair pass id seen by this scene.

    Stale or non-numeric values coerce to 0 so callers can compare against
    a fresh pass id without an extra try/except.
    """

    raw = (getattr(scene, "metadata_json", None) or {}).get(
        _SCENE_AUTO_REPAIR_LAST_PASS_ID_KEY
    )
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def claim_scene_auto_repair_attempt(
    scene: SceneCardModel,
    *,
    pass_id: int,
) -> int:
    """Claim one auto-repair pass for ``scene``, idempotent within a pass.

    The chapter pipeline reuses :func:`maybe_prepare_chapter_auto_repair`
    for three different reset paths (write-safety / metadata-code /
    length-stability). A single chapter-level auto-repair pass can hit
    one scene from more than one of these paths; without dedup the per-
    scene counter would over-count, hitting the cap before the chapter
    sees the configured number of real rewrite cycles.

    ``pass_id`` should be the chapter-level auto-repair attempt number
    (e.g. ``chapter.metadata_json["auto_repair_attempts"]``) — it
    monotonically increases per chapter-level pass and resets only when
    the chapter leaves the auto-repair loop. Returns the **new** counter
    value if this call bumped it, or the current value if a same-pass
    call already claimed the slot.
    """

    if pass_id <= 0:
        # Defensive: pass_id is supposed to be 1-based.  Without this guard
        # a caller passing 0 would mean "always dedup" and never increment.
        return read_scene_auto_repair_counter(scene)

    if _read_scene_last_pass_id(scene) >= pass_id:
        # Same (or older) chapter pass — already counted this scene.
        return read_scene_auto_repair_counter(scene)

    new_value = bump_scene_auto_repair_counter(scene)
    metadata = dict(getattr(scene, "metadata_json", None) or {})
    metadata[_SCENE_AUTO_REPAIR_LAST_PASS_ID_KEY] = int(pass_id)
    scene.metadata_json = metadata
    return new_value


def _scene_current_contract_controls(
    scene: SceneCardModel,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return clean scene prompt controls from the current card contract.

    Scene metadata often accumulates repair residue while a chapter loops. The
    chapter-first writer must not treat old ``auto_repair_hint`` / top-level
    ``cut_point`` / ``action_sequence`` values as fresh creative direction.
    """

    raw_metadata = getattr(scene, "metadata_json", None)
    metadata = dict(raw_metadata or {}) if isinstance(raw_metadata, Mapping) else {}
    methodology_contract = (
        dict(metadata.get("methodology_contract") or {})
        if isinstance(metadata.get("methodology_contract"), Mapping)
        else {}
    )
    scene_contract = (
        dict(metadata.get("scene_contract") or {})
        if isinstance(metadata.get("scene_contract"), Mapping)
        else {}
    )
    controls = {
        "gate_function": methodology_contract.get("gate_function"),
        "visible_progress": (
            methodology_contract.get("visible_progress")
            or methodology_contract.get("visible_action_or_reaction")
        ),
        "reader_payoff": (
            methodology_contract.get("reader_payoff")
            or methodology_contract.get("signature_image")
            or scene_contract.get("visible_object")
        ),
        "ending_hook_payload": (
            getattr(scene, "hook_requirement", None)
            or scene_contract.get("exit_hook")
            or methodology_contract.get("cut_point")
            or methodology_contract.get("breakpoint")
        ),
        "signature_image": (
            methodology_contract.get("signature_image")
            or scene_contract.get("visible_object")
        ),
        "cut_point": (
            methodology_contract.get("cut_point")
            or methodology_contract.get("breakpoint")
        ),
        "action_sequence": methodology_contract.get("action_sequence"),
        "relationship_debts": methodology_contract.get("relationship_debts"),
        "information_control_mode": (
            methodology_contract.get("information_control_mode")
            or methodology_contract.get("reveal_mode")
        ),
    }
    return metadata, methodology_contract, controls


# ---------------------------------------------------------------------------
# R23 — per-scene hard-acceptance block (word budget + signature obligations)
# ---------------------------------------------------------------------------
# Empirically the scene writer systematically ignores
# ``scene.target_word_count`` when it only appears mid-prompt among ~76
# one-line constraint blocks (700-word scenes ballooning to 1800+ →
# LENGTH_OVER, then over-corrected to LENGTH_UNDER in repair rounds; ch3/5/6/9
# oscillation).  The fix is positional: render the scene's word budget (and
# its signature-image / object-signal prose obligations) as ONE compact block
# at the VERY FRONT of the user prompt so it reads as the scene's primary
# acceptance contract rather than yet another buried constraint line.

_SCENE_WORD_BUDGET_TOLERANCE = 0.15


def _render_scene_word_budget_block(
    scene: SceneCardModel,
    *,
    is_en: bool,
) -> str:
    """Render the R23 "scene hard acceptance" block for the scene writer.

    Pulls live data from the scene object: ``target_word_count`` plus the
    scene card's ``signature_image`` (via the current contract controls) and
    top-level ``object_signal`` metadata.  Returns ``""`` when the scene has
    neither a usable word target nor signature obligations, so callers can
    drop the block without special-casing.
    """

    try:
        target = int(getattr(scene, "target_word_count", 0) or 0)
    except (TypeError, ValueError):
        target = 0
    metadata, _methodology_contract, controls = _scene_current_contract_controls(scene)
    signature_image = str(controls.get("signature_image") or "").strip()
    object_signal = str(metadata.get("object_signal") or "").strip()

    lines: list[str] = []
    if target > 0:
        low = max(1, int(round(target * (1 - _SCENE_WORD_BUDGET_TOLERANCE))))
        high = int(round(target * (1 + _SCENE_WORD_BUDGET_TOLERANCE)))
        if is_en:
            lines.append(
                f"- Scene word budget: {target} words"
                f" (binding range {low}-{high} words, ±15%)."
            )
            lines.append(
                "- When you reach the budget ceiling you MUST wrap up this "
                "scene — do not open a new event. If you are under the floor, "
                "deepen the existing conflict instead of padding."
            )
        else:
            lines.append(
                f"- 本场字数预算：{target}字（硬性区间 {low}-{high} 字，±15%）。"
            )
            lines.append(
                "- 写到预算上限必须收束本场，不得开新事件；"
                "不足下限时把已有冲突写透，禁止注水拖长。"
            )
    if signature_image:
        lines.append(
            f'- This scene MUST render "{signature_image}" as a visible '
            "on-page image, using the original wording (the gate matches the "
            "phrase text)."
            if is_en
            else (
                f"- 本场必须把「{signature_image}」写成可见画面"
                "（原词或基本原词出现在正文中，质检按短语文本匹配）。"
            )
        )
    if object_signal:
        lines.append(
            f'- The object signal "{object_signal}" must land visibly in '
            "this scene's prose."
            if is_en
            else f"- 本场必须让物件信号「{object_signal}」在正文中可见落地。"
        )
    if not lines:
        return ""
    header = (
        "=== Scene hard acceptance (highest priority) ==="
        if is_en
        else "=== 本场硬验收（最高优先级）==="
    )
    return header + "\n" + "\n".join(lines)


UNFINISHED_ARTIFACT_BLOCK_CODE = "UNFINISHED_ARTIFACT"
LLM_OUTPUT_TRUNCATED_BLOCK_CODE = "LLM_OUTPUT_TRUNCATED"
SCENE_COMPLETION_BLOCK_CODE = "SCENE_COMPLETION_INCOMPLETE"

_TRUNCATED_FINISH_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_token",
        "token_limit",
        "output_limit",
        "content_length",
    }
)


_CJK_CHAR_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['\u2019._-][A-Za-z0-9]+)*")

_TRACE_TRUE_VALUES = {"1", "true", "yes", "on", "summary", "full"}


def _scene_prompt_trace_mode() -> str | None:
    raw = os.environ.get("BESTSELLER_TRACE_SCENE_PROMPTS", "")
    mode = raw.strip().lower()
    if mode not in _TRACE_TRUE_VALUES:
        return None
    if mode == "full" or os.environ.get("BESTSELLER_TRACE_FULL_PROMPTS"):
        return "full"
    return "summary"


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    return value


def _block_trace(block: str | None, user_prompt: str) -> dict[str, Any]:
    text = block or ""
    present = bool(text.strip())
    return {
        "present": present,
        "chars": len(text),
        "estimated_tokens": _estimate_tokens(text),
        "included_in_user_prompt": bool(present and text in user_prompt),
    }


def _maybe_write_scene_prompt_trace(
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    context_packet: SceneWriterContextPacket | None,
    *,
    system_prompt: str,
    user_prompt: str,
    workflow_run_id: UUID | None,
    step_run_id: UUID | None,
    model_tier: str,
    trace_kind: str = "scene",
) -> str | None:
    mode = _scene_prompt_trace_mode()
    if mode is None:
        return None

    try:
        output_dir = Path(settings.output.base_dir) / project.slug / "traces"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_trace_kind = re.sub(r"[^a-z0-9_-]+", "-", trace_kind.lower()).strip("-")
        if not safe_trace_kind:
            safe_trace_kind = "scene"
        path = output_dir / (
            f"{safe_trace_kind}-prompt-ch{chapter.chapter_number:04d}-"
            f"s{scene.scene_number:02d}-{timestamp}.json"
        )

        block_attrs = (
            "identity_constraint_block",
            "overused_phrase_block",
            "genre_constraint_block",
            "ranking_capability_profile_block",
            "progression_context_block",
            "decision_policy_block",
            "rule_system_context_block",
            "faction_ecology_context_block",
            "relationship_agency_context_block",
            "entry_system_context_block",
            "entry_registry_context_block",
            "entry_state_ledger_block",
            "opening_diversity_block",
            "conflict_diversity_block",
            "scene_purpose_diversity_block",
            "env_diversity_block",
            "arc_beat_block",
            "five_layer_block",
            "cliffhanger_diversity_block",
            "tension_target_block",
            "location_ledger_block",
            "budget_diversity_block",
            "scene_scope_isolation_block",
            "plan_richness_block",
            "reader_contract_block",
            "hype_constraints_block",
            "l3_prompt_block",
            "voice_dna_block",
            "dialogue_voice_block",
            "chapter_market_constraints_block",
            "signature_scene_block",
            "prior_persona_feedback_block",
            "hook_echo_block",
            "exposition_density_block",
            "canon_guardrails_block",
            "timeline_canon_block",
            "scene_coherence_block",
            "character_role_block",
            "chapter_length_block",
        )
        blocks = {
            attr: _block_trace(getattr(context_packet, attr, None), user_prompt)
            for attr in block_attrs
        }
        counts = {
            "story_bible_context_used": bool(_packet_story_bible_context(context_packet)),
            "recent_scene_count": len(_packet_recent_scene_summaries(context_packet)),
            "recent_timeline_count": len(_packet_recent_timeline_events(context_packet)),
            "participant_fact_count": len(_packet_participant_canon_facts(context_packet)),
            "active_arc_count": len(_packet_active_plot_arcs(context_packet)),
            "active_beat_count": len(_packet_active_arc_beats(context_packet)),
            "unresolved_clue_count": len(_packet_unresolved_clues(context_packet)),
            "emotion_track_count": len(_packet_emotion_tracks(context_packet)),
            "antagonist_plan_count": len(_packet_antagonist_plans(context_packet)),
            "tree_context_count": len(_packet_tree_context(context_packet)),
            "retrieval_chunk_count": len(_packet_retrieval_context(context_packet)),
            "query_brief_used": bool(getattr(context_packet, "query_brief", None)),
            "query_tool_call_count": len(getattr(context_packet, "query_trace", []) or []),
        }
        payload: dict[str, Any] = {
            "trace_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "workflow_run_id": workflow_run_id,
            "step_run_id": step_run_id,
            "project": {
                "id": project.id,
                "slug": project.slug,
                "title": project.title,
                "language": project.language,
                "genre": project.genre,
                "sub_genre": project.sub_genre,
                "status": project.status,
            },
            "chapter": {
                "id": chapter.id,
                "number": chapter.chapter_number,
                "title": chapter.title,
                "status": chapter.status,
                "production_state": chapter.production_state,
                "target_word_count": chapter.target_word_count,
                "current_word_count": chapter.current_word_count,
                "metadata": chapter.metadata_json,
            },
            "scene": {
                "id": scene.id,
                "number": scene.scene_number,
                "type": scene.scene_type,
                "title": scene.title,
                "status": scene.status,
                "participants": scene.participants,
                "target_word_count": scene.target_word_count,
                "purpose": scene.purpose,
                "entry_state": scene.entry_state,
                "exit_state": scene.exit_state,
                "metadata": scene.metadata_json,
            },
            "model_tier": model_tier,
            "trace_kind": safe_trace_kind,
            "context_query": getattr(context_packet, "query_text", None),
            "context_counts": counts,
            "context_blocks": blocks,
            "prompt_stats": {
                "system_chars": len(system_prompt),
                "system_estimated_tokens": _estimate_tokens(system_prompt),
                "user_chars": len(user_prompt),
                "user_estimated_tokens": _estimate_tokens(user_prompt),
                "context_budget_tokens": settings.generation.context_budget_tokens,
            },
        }
        if mode == "full":
            payload["prompts"] = {
                "system": system_prompt,
                "user": user_prompt,
            }
        else:
            payload["prompt_previews"] = {
                "system_head": system_prompt[:2000],
                "user_head": user_prompt[:4000],
                "user_tail": user_prompt[-2000:],
            }
        serialized = json.dumps(_jsonable(payload), ensure_ascii=False, indent=2)
        path.write_text(serialized, encoding="utf-8")
        return str(path)
    except Exception:
        logger.debug("scene prompt trace write failed", exc_info=True)
        return None


def _finish_reason_indicates_truncation(finish_reason: object) -> bool:
    text = str(finish_reason or "").strip().lower()
    return text in _TRUNCATED_FINISH_REASONS


def _strip_markdown_plain(text: str) -> str:
    """Lightweight markdown-to-plain-text for word counting.

    Countable length units in this project intentionally follow mixed
    Chinese/English publishing convention:

    - CJK ideographs count as one unit each.
    - Latin words, acronym runs, and numeric runs count as one unit each.
    - Punctuation, whitespace, markdown syntax, frontmatter, URLs, comments,
      and fenced-code metadata do not count.
    """

    if not text:
        return ""
    cleaned = str(text).replace("\ufeff", "")
    cleaned = re.sub(r"\A\s*---\s*\n.*?\n---\s*(?:\n|$)", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"!\[[^\]]*]\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = cleaned.replace("*", "").replace("_", "").replace("~", "")

    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            lines.append("")
            continue
        if re.fullmatch(r"[-*_]{3,}", stripped):
            continue
        stripped = re.sub(r"^#{1,6}\s+", "", stripped)
        stripped = re.sub(r"^>\s*", "", stripped)
        stripped = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", stripped)
        if "|" in stripped:
            stripped = " ".join(part.strip() for part in stripped.split("|") if part.strip())
        if stripped:
            lines.append(stripped)
        else:
            lines.append("")
    return "\n".join(lines).strip()


def count_words(text: str) -> int:
    plain = _strip_markdown_plain(text)
    non_ws = re.sub(r"\s+", "", plain)
    return len(_CJK_CHAR_PATTERN.findall(non_ws)) + len(_LATIN_WORD_PATTERN.findall(plain))



def authoritative_word_count_for_language(text: str, *, language: str = "zh-CN") -> int:
    """Body-truth word count for chapter/scene commit (CJK for zh)."""

    from bestseller.services.chapter_word_count_truth import authoritative_zh_word_count

    return authoritative_zh_word_count(text, language=language)

def _clean_generated_chapter_text(
    content_md: str,
    *,
    chapter_number: int,
    source: str,
    min_word_count: int | None = None,
) -> tuple[str, dict[str, int]]:
    """Apply deterministic prose cleanup shared by chapter-first and rewrites."""

    cleaned = content_md or ""
    stats: dict[str, int] = {
        "meta_markers": 0,
        "loop_paragraphs": 0,
        "short_cluster_paragraphs": 0,
        "duplicate_paragraphs": 0,
        "duplicate_paragraphs_preserved_under_min": 0,
        "cross_scene_near_verbatim_paragraphs": 0,
        "forbidden_signal_negations": 0,
    }
    try:
        from bestseller.services.deduplication import (
            clean_meta_text_markers,
            detect_chapter_text_loop,
            detect_cross_scene_beat_reenactment,
            detect_intra_chapter_repetition,
            detect_short_cluster_near_repeat,
            remove_chapter_text_loops,
            remove_cross_scene_near_verbatim_repeats,
            remove_intra_chapter_duplicates_paraphrase,
            remove_short_cluster_near_repeats,
        )

        cleaned, stats["meta_markers"] = clean_meta_text_markers(cleaned)
        if stats["meta_markers"]:
            logger.info(
                "%s chapter %d: removed %d meta-text marker(s)",
                source,
                chapter_number,
                stats["meta_markers"],
            )

        loop_findings = detect_chapter_text_loop(cleaned)
        if loop_findings:
            logger.warning(
                "%s chapter %d: %d LLM-loop block(s) detected — auto-collapsing",
                source,
                chapter_number,
                len(loop_findings),
            )
            cleaned, stats["loop_paragraphs"] = remove_chapter_text_loops(cleaned)

        short_findings = detect_short_cluster_near_repeat(cleaned)
        if short_findings:
            logger.warning(
                "%s chapter %d: %d short-line cluster repeat(s) detected — auto-collapsing",
                source,
                chapter_number,
                len(short_findings),
            )
            cleaned, stats["short_cluster_paragraphs"] = remove_short_cluster_near_repeats(
                cleaned
            )

        dup_findings = detect_intra_chapter_repetition(cleaned)
        if dup_findings:
            logger.warning(
                "%s chapter %d: %d duplicate paragraph(s) detected — auto-removing",
                source,
                chapter_number,
                len(dup_findings),
            )
            deduped, removed = remove_intra_chapter_duplicates_paraphrase(cleaned)
            if (
                min_word_count is not None
                and min_word_count > 0
                and count_words(deduped) < min_word_count
            ):
                stats["duplicate_paragraphs_preserved_under_min"] = removed
                logger.warning(
                    "%s chapter %d: preserving %d duplicate paragraph(s) because "
                    "auto-removal would drop below min_word_count=%d",
                    source,
                    chapter_number,
                    removed,
                    min_word_count,
                )
            else:
                cleaned = deduped
                stats["duplicate_paragraphs"] = removed

        # Cross-scene beat re-enactment (节拍重演) — near-verbatim repeats of
        # much-earlier paragraphs are removed deterministically (keep first);
        # paraphrase-level re-enactment clusters are only logged here and are
        # routed to repair via the post-assembly duplicate gate. Never raises.
        beat_findings = detect_cross_scene_beat_reenactment(cleaned)
        if beat_findings:
            for beat_finding in beat_findings:
                logger.warning(
                    "%s chapter %d: %s",
                    source,
                    chapter_number,
                    beat_finding.get("message"),
                )
            cleaned, stats["cross_scene_near_verbatim_paragraphs"] = (
                remove_cross_scene_near_verbatim_repeats(cleaned)
            )
        cleaned, stats["forbidden_signal_negations"] = (
            _remove_forbidden_signal_negation_echoes(cleaned)
        )
    except Exception:
        logger.debug(
            "%s chapter %d: generated chapter cleanup failed (non-fatal)",
            source,
            chapter_number,
            exc_info=True,
        )
    return cleaned, stats


def _remove_forbidden_signal_negation_echoes(content: str) -> tuple[str, int]:
    """Remove prompt-echoed negations like ``不是发烫`` from publishable prose."""

    replacements = {
        "不是发烫": "没有温度变化",
        "并不发烫": "没有温度变化",
        "没有发烫": "没有温度变化",
        "不是发热": "没有温度变化",
        "并不发热": "没有温度变化",
        "没有发热": "没有温度变化",
        "不是滚烫": "没有温度变化",
        "并不滚烫": "没有温度变化",
        "没有滚烫": "没有温度变化",
        "不是变热": "没有温度变化",
        "并不变热": "没有温度变化",
        "没有变热": "没有温度变化",
    }
    updated = content
    count = 0
    for needle, replacement in replacements.items():
        occurrences = updated.count(needle)
        if occurrences:
            updated = updated.replace(needle, replacement)
            count += occurrences
    return updated, count


def prose_output_max_tokens_for_target(
    target_word_count: int | None,
    *,
    language: str | None = None,
    settings: AppSettings | None = None,
    role: str = "writer",
    model_max_tokens: int | None = None,
) -> int | None:
    """Return a conservative output-token cap for a prose target.

    The global writer cap is intentionally high so long scenes are not
    truncated, but short 500-800字 scene prompts also inherit that cap and can
    over-generate by thousands of characters.  This per-request cap keeps the
    model's output budget aligned with the target while leaving enough room
    for Chinese tokenization variance and a clean ending.
    """

    try:
        target = int(target_word_count or 0)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    active_settings = settings or load_settings(env={})
    model_name = resolve_llm_role_model(active_settings, role=role)
    model_tokens = int(model_max_tokens) if model_max_tokens is not None else resolve_llm_role_max_tokens(
        active_settings,
        role=role,
    )
    is_minimax_highspeed = "minimax-m2" in (model_name or "").lower() and "highspeed" in (
        model_name or ""
    ).lower()
    if is_minimax_highspeed:
        # MiniMax-M2.7-highspeed returns empty content too often when a prose
        # request is cut off exactly at a tight cap. Keep enough room for a
        # natural stop, while still below the former runaway 1.9x+512 budget.
        role_lc = (role or "").strip().lower()
        if role_lc == "editor":
            multiplier = 2.2 if is_english_language(language) else 2.0
        else:
            multiplier = 1.25 if is_english_language(language) else 1.05
    else:
        multiplier = 2.8 if is_english_language(language) else 3.2
    if is_minimax_highspeed:
        if (role or "").strip().lower() == "editor":
            floor = 6144
            extra_budget = 768
        else:
            floor = 768
            extra_budget = 256
    else:
        floor = 1024 if is_english_language(language) else 1536
        extra_budget = 512
    visible_cap = max(floor, int(round(target * multiplier)) + extra_budget)
    reserve = model_reasoning_token_reserve(model_name)
    cap = visible_cap + reserve
    if model_tokens is not None and model_tokens > 0:
        token_ceiling = model_output_token_ceiling(model_name)
        effective_model_tokens = int(model_tokens)
        if reserve and token_ceiling:
            effective_model_tokens = max(
                effective_model_tokens,
                min(int(token_ceiling), cap),
            )
        return min(cap, effective_model_tokens)
    return cap


def chapter_first_runaway_max_tokens(
    settings: AppSettings,
    *,
    role: str = "writer",
    target_word_count: int | None = None,
    language: str | None = None,
    hard_max_word_count: int | None = None,
) -> int | None:
    """Return a provider-safe output cap for chapter-first prose calls.

    Chapter length must be controlled by the prompt contract and quality gates,
    not by a target-derived completion cap.  This value is a model-family
    runaway guard, not a chapter-length budget.  Live MiniMax-M2.7-highspeed
    runs can consume the full 32768 completion budget and return empty visible
    content, so that family uses a fixed safe cap that is still ample for a
    3500-character Chinese chapter.
    """

    model_name = resolve_llm_role_model(settings, role=role)
    model_tokens = resolve_llm_role_max_tokens(settings, role=role)
    model_name_lc = (model_name or "").strip().lower()
    if "minimax-m2" in model_name_lc and "highspeed" in model_name_lc:
        safe_cap = 5_488
        model_ceiling = model_output_token_ceiling(model_name)
        if model_ceiling is not None and model_ceiling > 0:
            safe_cap = min(safe_cap, int(model_ceiling))
        if model_tokens is not None and model_tokens > 0:
            return min(int(model_tokens), safe_cap)
        return safe_cap
    if "minimax" in model_name_lc:
        safe_cap = 16_384
        if model_tokens is not None and model_tokens > 0:
            return min(int(model_tokens), safe_cap)
        return safe_cap
    if model_tokens is not None and model_tokens > 0:
        return int(model_tokens)
    return model_output_token_ceiling(model_name)


async def _load_character_name_roster(
    session: AsyncSession, project_id: UUID
) -> frozenset[str]:
    """Collect character names for the project — used as the allowlist for
    ``NamingConsistencyCheck``.

    Returns an empty set on any error so the gate degrades gracefully (the
    check no-ops on empty allowlists rather than flagging every name).
    """

    def add_name(target: set[str], value: Any) -> None:
        if isinstance(value, str) and value.strip():
            target.add(value.strip())

    def add_aliases(target: set[str], value: Any) -> None:
        if isinstance(value, str):
            add_name(target, value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add_name(target, item)

    try:
        names: set[str] = set()
        characters = list(
            await session.scalars(
                select(CharacterModel).where(CharacterModel.project_id == project_id)
            )
        )
        for character in characters:
            add_name(names, character.name)
            meta = character.metadata_json or {}
            if not isinstance(meta, dict):
                continue
            add_aliases(names, meta.get("aliases"))
            cast_entry = meta.get("cast_entry")
            if isinstance(cast_entry, dict):
                add_aliases(names, cast_entry.get("aliases"))

        project = await session.get(ProjectModel, project_id)
        if project is not None and isinstance(project.metadata_json, dict):
            manifest = project.metadata_json.get("identity_manifest")
            if isinstance(manifest, list):
                for item in manifest:
                    if not isinstance(item, dict):
                        continue
                    add_name(names, item.get("name"))
                    add_aliases(names, item.get("aliases"))
        return frozenset(names)
    except Exception:  # pragma: no cover — defensive guard for gate robustness
        logger.debug(
            "character roster load failed for project %s", project_id, exc_info=True
        )
        return frozenset()


async def _load_offstage_character_names_before_chapter(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
) -> frozenset[str]:
    """Return the set of characters who cannot take present-tense action
    in chapter N.

    Includes:
      * Truly dead characters (``death_chapter_number < N`` and no
        fake-death reveal yet);
      * Characters whose ``metadata_json.lifecycle_status`` resolves to
        an offstage kind in the current chapter (``missing``,
        ``sealed``, ``sleeping``, ``comatose``).

    The filter is applied in Python because the rich state lives in
    JSON and predicates over JSON don't port to the sqlite test path.
    """

    try:
        rows = list(
            await session.scalars(
                select(CharacterModel).where(
                    CharacterModel.project_id == project_id,
                )
            )
        )
    except Exception:
        logger.debug(
            "chapter %d: offstage-character roster lookup failed (non-fatal)",
            chapter_number,
            exc_info=True,
        )
        return frozenset()

    try:
        from bestseller.services.character_lifecycle import (
            OFFSTAGE_KINDS,
            effective_lifecycle_state,
        )
    except Exception:  # pragma: no cover — defensive guard
        return frozenset()

    out: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            if row.strip():
                out.add(row.strip())
            continue
        name = getattr(row, "name", None)
        if not name:
            continue
        kind, _ = effective_lifecycle_state(
            alive_status=getattr(row, "alive_status", None),
            death_chapter_number=getattr(row, "death_chapter_number", None),
            chapter_number=chapter_number,
            character_metadata=getattr(row, "metadata_json", None),
        )
        if kind in OFFSTAGE_KINDS:
            out.add(str(name).strip())
    return frozenset(o for o in out if o)


# Backward-compatibility alias — the function used to load only deceased
# names. We keep the old name so any caller that still imports it gets
# the broader semantics for free; the call sites have been updated to
# use the new name.
_load_dead_character_names_before_chapter = _load_offstage_character_names_before_chapter


_CHARACTER_OFFSTAGE_REPAIR_CODES: frozenset[str] = frozenset(
    {
        "dead_alive",
        "character_resurrection",
        "character_missing_appearance",
        "character_sealed_appearance",
        "character_sleeping_appearance",
        "character_comatose_appearance",
    }
)


def _has_character_offstage_repair_code(codes: Iterable[str]) -> bool:
    return any(_canonical_repair_code(code) in _CHARACTER_OFFSTAGE_REPAIR_CODES for code in codes)


def _offstage_reference_guidance(codes: Iterable[str]) -> str:
    canonical = {_canonical_repair_code(code) for code in codes}
    death_like = bool(canonical & {"dead_alive", "character_resurrection"})
    if death_like:
        return (
            "如需提及，仅可：旁人怀念/悲悼/提起；引用其先前的话或留下的文字；"
            "以遗体/画像/坟前/灵堂/信物/远闻线索的形态被提及；"
            "或在显式标注的回忆/闪回/祭奠/梦境/幻象场景中出现。"
        )
    return (
        "如需提及，仅可：旁人担忧/寻找/提起；引用其先前的话或留下的文字；"
        "以昏迷肉身/沉睡身体/封印体/失踪线索/旧物/远闻线索的形态被提及；"
        "或在显式标注的回忆/闪回/梦境/幻象场景中出现。"
    )


def _scrub_offstage_scene_references(
    scene: SceneCardModel,
    offstage_character_names: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Strip offstage character names from active scene-card fields.

    "Offstage" covers every state that forbids present-tense
    participation: deceased / missing / sealed / sleeping / comatose.
    Flashback / memorial / vision / dream scenes are exempted because
    those modes may legitimately stage offstage characters as memory,
    body, or symbol.

    Returns ``(removed_participants, removed_state_refs)``.  State refs are
    character-keyed entries in ``entry_state`` / ``exit_state``; leaving them
    behind after removing a participant gives the drafter contradictory input
    ("参与者：宁尘" but "入场状态：陆沉...") and can recreate the same
    resurrection block on the next pass.
    """

    dead_character_names = offstage_character_names
    if not dead_character_names:
        return [], []

    # Flashback / memorial / vision / dream / quoted-reference scenes are
    # legitimate venues for offstage characters — they may be remembered,
    # quoted, mourned, or appear as a corpse / image / letter. Stripping
    # their names here would force the writer to omit the very people
    # the planner placed in the scene on purpose. Skip the filter.
    try:
        from bestseller.services.character_lifecycle import scene_is_flashback_like
        if scene_is_flashback_like(scene):
            return [], []
    except Exception:  # pragma: no cover — defensive guard
        pass

    dead_lookup = {name.casefold() for name in dead_character_names}

    participants = list(getattr(scene, "participants", None) or [])
    kept: list[str] = []
    removed: list[str] = []
    for participant in participants:
        name = str(participant).strip()
        if name and name.casefold() in dead_lookup:
            removed.append(name)
        else:
            kept.append(participant)

    if removed:
        scene.participants = kept

    removed_state_refs: list[str] = []
    for attr in ("entry_state", "exit_state"):
        value = getattr(scene, attr, None)
        if not isinstance(value, dict):
            continue
        next_value = dict(value)
        for key in list(next_value.keys()):
            name = str(key).strip()
            if name and name.casefold() in dead_lookup:
                removed_state_refs.append(name)
                next_value.pop(key, None)
        if next_value != value:
            setattr(scene, attr, next_value)

    if removed or removed_state_refs:
        meta = dict(scene.metadata_json or {})
        if removed:
            previous = [
                str(item)
                for item in (meta.get("auto_repair_removed_participants") or [])
                if item
            ]
            meta["auto_repair_removed_participants"] = list(
                dict.fromkeys([*previous, *removed])
            )
        if removed_state_refs:
            previous_state = [
                str(item)
                for item in (meta.get("auto_repair_removed_state_refs") or [])
                if item
            ]
            meta["auto_repair_removed_state_refs"] = list(
                dict.fromkeys([*previous_state, *removed_state_refs])
            )
        scene.metadata_json = meta

    return removed, list(dict.fromkeys(removed_state_refs))


def _filter_offstage_scene_participants(
    scene: SceneCardModel,
    offstage_character_names: frozenset[str],
) -> list[str]:
    removed, _ = _scrub_offstage_scene_references(scene, offstage_character_names)
    return removed


# Backward-compatibility alias — older callers and tests import the
# original "dead" name. The function semantics now cover every offstage
# kind, but the alias keeps imports stable.
_filter_dead_scene_participants = _filter_offstage_scene_participants


async def _auto_sign_override_contracts(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    blocking_violations: tuple,
    soft_constraint_codes: frozenset[str],
    interest_rate: float,
    payback_window: int,
) -> int:
    """Phase C — auto-sign one Override Contract per soft-blocking violation.

    For each violation in ``blocking_violations`` whose ``code`` is present
    in ``soft_constraint_codes``, persist an ``OverrideContractModel`` row
    (status=active, rationale=ARC_TIMING) plus a sibling ``ChaseDebtModel``
    row (principal=1.0, source=override_contract). Returns the number of
    contract/debt pairs persisted.

    Silent on any single failure; the chapter must never be blocked by a
    debt-writer crash. When all operations succeed, the gate downstream
    can treat these blocking violations as resolved for this chapter.
    """

    from bestseller.services.regen_loop import propose_overrides_from_report

    filtered = QualityReport(violations=tuple(blocking_violations))
    try:
        proposals = propose_overrides_from_report(
            filtered,
            chapter_no=chapter_number,
            soft_constraint_codes=soft_constraint_codes,
            default_rationale_type="ARC_TIMING",
            payback_window_default=max(1, int(payback_window)),
        )
    except Exception:  # pragma: no cover — defensive
        logger.debug(
            "propose_overrides_from_report failed for chapter %d",
            chapter_number,
            exc_info=True,
        )
        return 0

    if not proposals:
        return 0

    persisted = 0
    for p in proposals:
        try:
            # Savepoint so a contract and its debt commit atomically: if the
            # debt construction/flush fails after the contract flush, the
            # contract is rolled back too (no orphaned debt-less contract).
            async with session.begin_nested():
                contract_row = OverrideContractModel(
                    project_id=project_id,
                    chapter_no=p.chapter_no,
                    violation_code=p.violation_code,
                    rationale_type=p.suggested_rationale_type,
                    rationale_text=(p.rationale_text or f"自动签署：{p.violation_code}")[:4000],
                    payback_plan=(p.suggested_payback_plan or "自动生成的偿还计划")[:4000],
                    due_chapter=p.suggested_due_chapter,
                    status="active",
                )
                session.add(contract_row)
                await session.flush()
                debt_row = ChaseDebtModel(
                    project_id=project_id,
                    override_contract_id=contract_row.id,
                    chapter_no=p.chapter_no,
                    violation_code=p.violation_code,
                    source="override_contract",
                    principal=1.0,
                    balance=1.0,
                    interest_rate=float(interest_rate),
                    accrued_through_chapter=p.chapter_no,
                    due_chapter=p.suggested_due_chapter,
                    status="active",
                )
                session.add(debt_row)
                await session.flush()
            persisted += 1
        except Exception:  # pragma: no cover — one failure must not poison the batch
            logger.debug(
                "override auto-sign persist failed (chapter=%d code=%s)",
                chapter_number,
                p.violation_code,
                exc_info=True,
            )
    return persisted


def _iter_character_voice_aliases(character: CharacterModel) -> tuple[str, ...]:
    names: list[str] = []

    def add(value: object) -> None:
        text = str(value).strip() if value is not None else ""
        if text and len(text) >= 2 and text not in names:
            names.append(text)

    add(getattr(character, "name", None))
    metadata = getattr(character, "metadata_json", None)
    if not isinstance(metadata, dict):
        return tuple(names)
    cast_entry = metadata.get("cast_entry")
    if isinstance(cast_entry, dict):
        for alias in cast_entry.get("aliases") or ():
            add(alias)
    for alias in metadata.get("aliases") or ():
        add(alias)
    profile = metadata.get("character_engine_profile")
    if isinstance(profile, dict):
        add(profile.get("display_name"))
        add(profile.get("character_id"))
    return tuple(names)


async def _load_active_character_engine_profiles(
    session: AsyncSession,
    *,
    project_id: UUID,
    content: str,
) -> tuple[dict[str, Any], ...]:
    rows = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project_id)
        )
    )
    profiles: list[dict[str, Any]] = []
    for character in rows:
        aliases = _iter_character_voice_aliases(character)
        if not any(alias and alias in content for alias in aliases):
            continue
        metadata = character.metadata_json if isinstance(character.metadata_json, dict) else {}
        profile = metadata.get("character_engine_profile")
        if isinstance(profile, dict):
            profiles.append(dict(profile))
    return tuple(profiles)


def _character_voice_audit_payload(
    content: str,
    *,
    platform: str | None,
    profiles: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    if not profiles:
        return None
    signature_words = collect_signature_words_from_profiles(profiles)
    forbidden_words = collect_forbidden_words_from_profiles(profiles)
    if not signature_words and not forbidden_words:
        return None
    signature_threshold = max(1, min(6, len(profiles) * 2))
    audit = audit_chapter(
        content,
        platform=platform,
        signature_words=signature_words,
        signature_threshold=signature_threshold,
        forbidden_words=forbidden_words,
    )
    signature = audit.signature_density
    forbidden = audit.forbidden_voice
    return {
        "active_characters": [
            str(profile.get("display_name") or profile.get("character_id") or "").strip()
            for profile in profiles
            if str(profile.get("display_name") or profile.get("character_id") or "").strip()
        ],
        "signature_words": list(signature_words[:50]),
        "forbidden_words": list(forbidden_words[:50]),
        "signature_density": (
            {
                "total_hits": signature.total_hits,
                "threshold": signature.threshold,
                "passed": signature.passed,
                "hits": list(signature.hits),
            }
            if signature is not None
            else None
        ),
        "forbidden_voice": (
            {
                "total_hits": forbidden.total_hits,
                "threshold": forbidden.threshold,
                "passed": forbidden.passed,
                "hits": list(forbidden.hits),
            }
            if forbidden is not None
            else None
        ),
    }


async def _evaluate_chapter_quality_gate(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter_number: int,
    content: str,
    extra_blocking_codes: tuple[str, ...] = (),
    extra_report_payload: dict[str, Any] | None = None,
) -> str | None:
    """Run L4 + L5 validators + L6 gate resolution on an assembled chapter draft.

    Returns the ``production_state`` string to stamp onto the chapter row:
        * ``"ok"`` — no blocking violations (audit-only findings may exist).
        * ``"blocked"`` — at least one violation resolved to ``block``.
        * ``None`` — gate is disabled or invariants missing; leave state untouched.

    Also threads ``DiversityBudget.recent_cliffhangers`` into the validation
    context so ``CliffhangerRotationCheck`` can see prior chapter kinds, and
    — when the gate passes — records this chapter's opening / cliffhanger
    / vocab / title into the budget so future chapters get honest rotation.

    Phase 1 intentionally keeps the gate non-raising so the pipeline can
    persist the rejected draft for later inspection. A downstream regen
    loop (Phase 2) will read ``production_state == "blocked"`` and retry.
    """

    if not project.invariants_json:
        return None

    gates_cfg = get_quality_gates_config()
    if not gates_cfg.l6_enabled or (not gates_cfg.l4.enabled and not gates_cfg.l5.enabled):
        return None

    try:
        invariants = invariants_from_dict(project.invariants_json)
    except InvariantSeedError:
        logger.warning(
            "chapter %d: invariants payload invalid, skipping quality gate",
            chapter_number,
        )
        return None

    # Build the validator once per call — cheap, lets config-flag flips take
    # effect between chapters without process restarts.
    validator = build_validator_from_config(gates_cfg)
    allowed_names = await _load_character_name_roster(session, project.id)
    try:
        from bestseller.settings import get_settings

        canon_guardrails = load_canon_guardrails_for_project(
            project,
            output_base_dir=get_settings().output.base_dir,
        )
    except Exception:
        logger.debug(
            "canon guardrails load failed for project %s", project.id, exc_info=True
        )
        canon_guardrails = None

    # Load diversity budget so CliffhangerRotationCheck can see the recent
    # kinds. Missing budget row → empty tuple, which makes the check no-op.
    try:
        budget = await load_diversity_budget(session, project.id)
    except Exception:  # pragma: no cover — defensive; budget is non-critical
        logger.debug(
            "diversity budget load failed for project %s", project.id, exc_info=True
        )
        budget = None

    window = max(invariants.cliffhanger_policy.no_repeat_within, 0) if invariants else 0
    recent_cliffhangers = (
        budget.recent_cliffhangers(window) if (budget is not None and window > 0) else ()
    )

    # Hype engine context — required by HypeOccurrenceCheck /
    # HypeDiversityCheck. Read the current chapter's assignment from any scene
    # draft's generation_params (all scenes of one chapter share the same
    # pick); recent hype types come from DiversityBudget.hype_moments.
    assigned_hype_type: Any = None
    assigned_hype_recipe: Any = None
    recent_hype_types: tuple[Any, ...] = ()
    try:
        from bestseller.services.hype_engine import HypeType as _HypeTypeEnum
        _chapter_row = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        if _chapter_row is not None:
            _scene_rows = list(
                await session.scalars(
                    select(SceneDraftVersionModel)
                    .join(SceneCardModel, SceneCardModel.id == SceneDraftVersionModel.scene_card_id)
                    .where(
                        SceneCardModel.chapter_id == _chapter_row.id,
                        SceneDraftVersionModel.is_current.is_(True),
                    )
                )
            )
            for _sd in _scene_rows:
                _gp = dict(_sd.generation_params or {})
                if _gp.get("assigned_hype_type"):
                    try:
                        assigned_hype_type = _HypeTypeEnum(str(_gp["assigned_hype_type"]))
                    except ValueError:
                        assigned_hype_type = None
                    # assigned_hype_recipe is the recipe *key* here; checks
                    # only need identity, not the full recipe object.
                    assigned_hype_recipe = _gp.get("assigned_hype_recipe_key")
                    break
        if budget is not None and budget.hype_moments:
            recent_hype_types = tuple(
                m.hype_type for m in list(budget.hype_moments)[-5:]
            )
    except Exception:
        logger.debug(
            "hype context load failed for chapter %d (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    # Phase B1 — populate line_gap_report so LineGapCheck can fire when a
    # layer has been dormant past its budget. Always computed when Phase B
    # is enabled; the check itself skips gracefully when the report is None.
    line_gap_report: Any = None
    if gates_cfg.phase_b.enabled:
        try:
            from bestseller.services.narrative_line_tracker import (
                load_history as _load_line_history,
            )
            from bestseller.services.narrative_line_tracker import (
                report_gaps as _report_line_gaps,
            )

            _history = _load_line_history(project.metadata_json)
            _genre_id = (
                (project.metadata_json or {}).get("genre_id")
                or getattr(project, "genre", None)
                or "action-progression"
            )
            line_gap_report = _report_line_gaps(
                project_id=str(project.id),
                current_chapter=chapter_number,
                history=_history,
                genre_id=str(_genre_id) if _genre_id else None,
            )
        except Exception:  # pragma: no cover — defensive; gap report is advisory
            logger.debug(
                "line gap report failed for chapter %d (non-fatal)",
                chapter_number,
                exc_info=True,
            )
            line_gap_report = None

    ctx = ValidationContext(
        invariants=invariants,
        chapter_no=chapter_number,
        scope="chapter",
        allowed_names=allowed_names,
        recent_cliffhangers=recent_cliffhangers,
        assigned_hype_type=assigned_hype_type,
        assigned_hype_recipe=assigned_hype_recipe,
        recent_hype_types=recent_hype_types,
        line_gap_report=line_gap_report,
        canon_guardrails=canon_guardrails,
    )
    report = validator.validate(content, ctx)
    try:
        front_chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        if front_chapter is not None:
            front_scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == front_chapter.id)
                    .order_by(SceneCardModel.scene_number.asc())
                )
            )
            front_violations = _front10_contract_violations_for_content(
                front_chapter,
                front_scenes,
                content,
            )
            if front_violations:
                report = QualityReport(report.violations + front_violations)
    except Exception:
        logger.debug(
            "front10 contract gate failed for project %s chapter %d",
            project.id,
            chapter_number,
            exc_info=True,
        )
    character_voice_payload: dict[str, Any] | None = None
    try:
        _voice_profiles = await _load_active_character_engine_profiles(
            session,
            project_id=project.id,
            content=content,
        )
        _project_meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
        _platform = (
            str(_project_meta.get("target_platform") or "").strip()
            or getattr(project, "platform_target", None)
            or None
        )
        character_voice_payload = _character_voice_audit_payload(
            content,
            platform=_platform,
            profiles=_voice_profiles,
        )
    except Exception:  # pragma: no cover - telemetry, never fails the gate
        logger.debug(
            "character voice audit failed for project %s chapter %d",
            project.id,
            chapter_number,
            exc_info=True,
        )

    blocking = filter_blocking(
        report, gates_cfg.l6_gate, chapter_no=chapter_number
    )

    # ── Phase C1 — auto-sign override contracts for soft blockers ──
    # When Phase C is enabled and every blocking violation's code lives in
    # ``invariants.soft_constraint_codes``, persist an OverrideContract +
    # ChaseDebt pair for each and treat them as resolved for this chapter.
    # This keeps autonomous runs moving forward while preserving the debt
    # ledger for later payback accountability.
    override_autosign_count = 0
    _phase_c_only_from = gates_cfg.phase_c.only_enforce_from_chapter
    _phase_c_in_window = (
        _phase_c_only_from is None or chapter_number >= _phase_c_only_from
    )
    if (
        gates_cfg.phase_c.enabled
        and _phase_c_in_window
        and blocking
        and invariants.soft_constraint_codes
        and all(v.code in invariants.soft_constraint_codes for v in blocking)
    ):
        try:
            override_autosign_count = await _auto_sign_override_contracts(
                session,
                project_id=project.id,
                chapter_number=chapter_number,
                blocking_violations=blocking,
                soft_constraint_codes=invariants.soft_constraint_codes,
                interest_rate=gates_cfg.phase_c.default_interest_rate,
                payback_window=gates_cfg.phase_c.payback_window_default,
            )
            if override_autosign_count > 0:
                logger.info(
                    "chapter %d: phase_c auto-signed %d override contract(s) "
                    "for soft blockers (%s)",
                    chapter_number,
                    override_autosign_count,
                    ",".join(v.code for v in blocking),
                )
                # Treat these as resolved for this chapter — downstream outcome
                # logic should no longer treat them as blocking.
                blocking = tuple()
        except Exception:  # pragma: no cover — never fail the gate on auto-sign
            logger.debug(
                "phase_c auto-sign path errored for chapter %d (non-fatal)",
                chapter_number,
                exc_info=True,
            )

    # ── Length-stability gate ──
    # The L4 LengthEnvelopeCheck only fires when invariants.length_envelope
    # exists; projects created before that bootstrap slip short chapters
    # through unnoticed.  This complementary gate always pulls the target
    # window from ``config.generation.words_per_chapter`` so a 3500-word
    # chapter (vs. target=6400) always surfaces as a blocking finding.
    length_block_code: str | None = None
    length_report_payload: dict[str, Any] | None = None
    try:
        from bestseller.services.length_stability_gate import (
            CHINESE_CHAPTER_HARD_MAX_WORDS,
            CHINESE_CHAPTER_HARD_MIN_WORDS,
            evaluate_chapter_length,
        )
        from bestseller.settings import get_settings

        _settings = get_settings()
        _pipeline_cfg = _settings.pipeline
        if getattr(_pipeline_cfg, "enable_length_stability_gate", False):
            _budget = _settings.generation.words_per_chapter
            _wc = count_words(content)
            _length_report = evaluate_chapter_length(
                word_count=_wc,
                min_words=int(_budget.min),
                target_words=int(_budget.target),
                max_words=int(_budget.max),
                warn_margin=float(
                    getattr(_pipeline_cfg, "length_stability_warn_margin", 0.10)
                ),
                hard_min_words=(
                    None
                    if is_english_language(getattr(project, "language", None))
                    else CHINESE_CHAPTER_HARD_MIN_WORDS
                ),
                hard_max_words=(
                    None
                    if is_english_language(getattr(project, "language", None))
                    else CHINESE_CHAPTER_HARD_MAX_WORDS
                ),
                enabled=True,
            )
            _severities = {
                str(s).strip().lower()
                for s in getattr(
                    _pipeline_cfg, "length_stability_block_severities", ("major",)
                )
                or ()
                if s
            }
            from bestseller.services.length_stability_gate import (
                LENGTH_STABILITY_ISSUE_SEVERITY,
            )

            _length_severity = LENGTH_STABILITY_ISSUE_SEVERITY.get(
                _length_report.band.value
            )
            # Always record the raw numbers so auto-repair / telemetry can
            # read them later, regardless of whether the band is blocking.
            length_report_payload = {
                "word_count": int(_length_report.word_count),
                "target_words": int(_length_report.target_words),
                "min_words": int(_length_report.min_words),
                "max_words": int(_length_report.max_words),
                "band": _length_report.band.value,
                "deviation_ratio": round(float(_length_report.deviation_ratio), 4),
                "issue_code": _length_report.issue_code,
            }
            if (
                _length_report.issue_code
                and _length_severity is not None
                and _length_severity in _severities
            ):
                length_block_code = _length_report.issue_code
                logger.warning(
                    "chapter %d: length-stability block — %s (wc=%d target=%d "
                    "deviation=%.1f%%)",
                    chapter_number,
                    _length_report.issue_code,
                    _length_report.word_count,
                    _length_report.target_words,
                    _length_report.deviation_ratio * 100.0,
                )
    except Exception:  # pragma: no cover — defensive, never fail the draft
        logger.debug(
            "length-stability gate errored for chapter %d (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    unfinished_issues: list[str] = []
    try:
        from bestseller.services.output_hygiene import collect_unfinished_artifact_issues

        unfinished_issues = collect_unfinished_artifact_issues(
            content,
            language=getattr(project, "language", None),
        )
        if unfinished_issues:
            logger.warning(
                "chapter %d: unfinished-artifact block — %s",
                chapter_number,
                "; ".join(unfinished_issues[:3]),
            )
    except Exception:
        logger.debug(
            "unfinished-artifact gate errored for chapter %d (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    additional_blocking_codes = tuple(str(c) for c in extra_blocking_codes if c)
    unfinished_block_code = UNFINISHED_ARTIFACT_BLOCK_CODE if unfinished_issues else None

    outcome: str
    if blocking or length_block_code or unfinished_block_code or additional_blocking_codes:
        reasons = [v.code for v in blocking]
        if length_block_code:
            reasons.append(length_block_code)
        if unfinished_block_code:
            reasons.append(unfinished_block_code)
        reasons.extend(additional_blocking_codes)
        logger.warning(
            "chapter %d: blocked by quality gate — %s",
            chapter_number,
            ", ".join(reasons),
        )
        outcome = "blocked"
    else:
        if report.violations:
            logger.info(
                "chapter %d: audit-only findings — %s",
                chapter_number,
                ", ".join(v.code for v in report.violations),
            )
        outcome = "ok"

    # Persist report row for L8 scorecard + Phase 2 promotion analysis.
    # Always write — even when passes — so the dashboard sees coverage.
    # Length-stability is a pipeline-level gate (not an L4/L5 violation) but
    # still records as a blocking code here so the auto-repair path below can
    # read the report row to decide whether to retry.
    _persisted_blocking_codes: tuple[str, ...] = tuple(v.code for v in blocking)
    if length_block_code:
        _persisted_blocking_codes = _persisted_blocking_codes + (length_block_code,)
    if unfinished_block_code:
        _persisted_blocking_codes = _persisted_blocking_codes + (unfinished_block_code,)
    if additional_blocking_codes:
        _persisted_blocking_codes = _persisted_blocking_codes + additional_blocking_codes
    _extra_payload: dict[str, Any] | None = None
    if extra_report_payload is not None:
        _extra_payload = dict(extra_report_payload)
    if length_report_payload is not None:
        _extra_payload = dict(_extra_payload or {})
        _extra_payload["length_stability"] = length_report_payload
    if unfinished_issues:
        _extra_payload = dict(_extra_payload or {})
        _extra_payload["unfinished_artifact"] = {
            "issues": list(unfinished_issues),
        }
    if character_voice_payload is not None:
        _extra_payload = dict(_extra_payload or {})
        _extra_payload["character_voice"] = character_voice_payload
    await _persist_chapter_quality_report(
        session,
        project_id=project.id,
        chapter_number=chapter_number,
        report=report,
        blocking_codes=_persisted_blocking_codes,
        extra_payload=_extra_payload,
    )

    # Only register diversity telemetry for chapters that actually ship —
    # blocked drafts will be re-rendered and we don't want their cliffhanger
    # / vocab contaminating the rotation window.
    if outcome == "ok" and budget is not None:
        try:
            detected_cliffhanger = classify_cliffhanger(
                content, invariants.language if invariants else None
            )
            title_candidate: str | None = None
            project_slug = getattr(project, "slug", None)
            chapter_title = await _lookup_chapter_title(
                session, project.id, chapter_number
            )
            if chapter_title:
                title_candidate = chapter_title
            budget.register_chapter(
                chapter_number,
                opening=None,  # Opening is registered at prompt-construction time (L3).
                cliffhanger=detected_cliffhanger,
                title=title_candidate,
                text=content,
                language=invariants.language if invariants else None,
            )
            await save_diversity_budget(session, budget)
            logger.debug(
                "chapter %d: diversity budget updated (cliffhanger=%s, slug=%s)",
                chapter_number,
                detected_cliffhanger.value if detected_cliffhanger else None,
                project_slug,
            )
        except Exception:  # pragma: no cover — budget update must never fail the gate
            logger.debug(
                "diversity budget save failed for project %s chapter %d",
                project.id,
                chapter_number,
                exc_info=True,
            )

    return outcome


async def _persist_chapter_quality_report(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    report: QualityReport,
    blocking_codes: tuple[str, ...],
    extra_payload: dict[str, Any] | None = None,
) -> None:
    """Insert a ``ChapterQualityReportModel`` row snapshotting this gate pass.

    Failing to persist must NOT fail the gate — scoring infra loss is
    recoverable, a lost draft isn't. Wrapped in a broad ``except`` to
    degrade gracefully; the scorecard job can still read existing rows.

    ``extra_payload`` is merged into ``report_json`` so gate-specific
    metadata (e.g. the length-stability word-count / target numbers) can
    survive to downstream consumers like the auto-repair helper.
    """

    try:
        chapter_row = await session.execute(
            select(ChapterModel.id).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        chapter_id = chapter_row.scalar_one_or_none()
        if chapter_id is None:
            return
        payload: dict[str, Any] = {
            "violations": [
                {
                    "code": v.code,
                    "severity": v.severity,
                    "location": v.location,
                    "detail": v.detail,
                }
                for v in report.violations
            ],
            "blocking_codes": list(blocking_codes),
        }
        if extra_payload:
            # Never let extra_payload overwrite the structural keys above —
            # the contract with downstream readers is that those keys always
            # hold the same shape.
            for key, value in extra_payload.items():
                if key in ("violations", "blocking_codes"):
                    continue
                payload[key] = value
        session.add(
            ChapterQualityReportModel(
                chapter_id=chapter_id,
                report_json=payload,
                regen_attempts=0,
                blocks_write=bool(blocking_codes),
            )
        )
        await session.flush()
    except Exception:  # pragma: no cover — telemetry, never fails the gate
        logger.debug(
            "chapter_quality_report persist failed for project %s chapter %d",
            project_id,
            chapter_number,
            exc_info=True,
        )


async def _lookup_chapter_title(
    session: AsyncSession, project_id: UUID, chapter_number: int
) -> str | None:
    try:
        row = await session.execute(
            select(ChapterModel.title).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        title = row.scalar_one_or_none()
        return (title or "").strip() or None
    except Exception:  # pragma: no cover — defensive
        return None


# ---------------------------------------------------------------------------
# Scene-scope L4 validation + L4.5 regen loop.
# ---------------------------------------------------------------------------


async def _build_scene_validator(
    session: AsyncSession, project: ProjectModel
) -> tuple[OutputValidator | None, ValidationContext | None]:
    """Construct an ``OutputValidator`` + ``ValidationContext`` for scene scope.

    Returns ``(None, None)`` when invariants are missing or gates disabled —
    the caller then skips scene-level validation gracefully. The returned
    validator bundles L4 + L5 checks; several checks self-exempt at scene
    scope (length envelope, entity density, cliffhanger rotation) so the
    per-scene runtime cost is dominated by language + naming + dialog
    integrity + POV lock — all fast.
    """

    if not project.invariants_json:
        return None, None

    gates_cfg = get_quality_gates_config()
    if not gates_cfg.l4.enabled and not gates_cfg.l5.enabled:
        return None, None

    try:
        invariants = invariants_from_dict(project.invariants_json)
    except InvariantSeedError:
        return None, None

    validator = build_validator_from_config(gates_cfg)
    allowed_names = await _load_character_name_roster(session, project.id)
    ctx = ValidationContext(
        invariants=invariants,
        chapter_no=None,
        scope="scene",
        allowed_names=allowed_names,
        recent_cliffhangers=(),  # Scene scope never checks cliffhanger rotation.
    )
    return validator, ctx


def _normalize_scene_naming_or_none(
    text: str,
    ctx: ValidationContext,
) -> str | None:
    """Attempt a deterministic rogue-name substitution for a scene draft.

    Re-runs the same detection the naming gate uses (same allowlist, same
    frequency floor) so the substitution set exactly mirrors what the gate
    flagged, then delegates to ``normalize_out_of_pool_names``.
    """

    try:
        allowed = NamingConsistencyCheck._collect_allowed(ctx)
        if not allowed:
            return None
        language = ctx.invariants.language
        if not language.lower().startswith("zh"):
            return None
        rogue = NamingConsistencyCheck._rogue_names_zh(text, allowed)
        # Mirror the gate's frequency floor (default 2): one-off hits are
        # usually spurious regex matches and are not part of the violation.
        rogue = {name: count for name, count in rogue.items() if count >= 2}
        if not rogue:
            return None
        result = normalize_out_of_pool_names(
            text,
            rogue_names=rogue,
            allowed_names=allowed,
            language=language,
        )
    except Exception:
        logger.debug("naming normalization failed (non-fatal)", exc_info=True)
        return None
    if result is None or not result.changed:
        return None
    return result.text


async def _regenerate_scene_until_valid(
    *,
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter_number: int,
    scene_number: int,
    initial_content: str,
    validator: OutputValidator,
    ctx: ValidationContext,
    system_prompt: str,
    user_prompt: str,
    fallback_content: str,
    workflow_run_id: UUID | None,
    step_run_id: UUID | None,
    model_tier: str,
    context_query: str,
    protagonist_name: str | None,
    supporting_name: str | None,
    target_word_count: int | None,
    global_budget: GlobalBudget | None,
) -> tuple[str, str | None, UUID | None, str, int]:
    """Validate ``initial_content`` and, if blocked, regen until it passes.

    Returns ``(final_content, model_name, llm_run_id, provider, regen_count)``.
    When the regen budget is exhausted, returns the best-effort output with a
    ``regen_count`` that reflects attempts made — the caller still persists
    the scene and lets the chapter-level gate / audit loop clean up later.
    """

    gates_cfg = get_quality_gates_config()
    scene_budget = max(0, min(gates_cfg.l4_5.budget_per_chapter, DEFAULT_BUDGET_PER_CHAPTER))

    initial_report = validator.validate(initial_content, ctx)
    if not initial_report.blocks_write or scene_budget == 0:
        return initial_content, None, None, "initial", 0

    # Deterministic naming repair BEFORE any LLM regen: out-of-pool names are
    # text-substitutable (pool variant or generic referent), and the regen
    # path historically burned 1-3 full-context writer calls per scene on
    # exactly this violation without converging.
    if any(v.code == "NAMING_OUT_OF_POOL" for v in initial_report.violations):
        normalized_text = _normalize_scene_naming_or_none(initial_content, ctx)
        if normalized_text is not None and normalized_text != initial_content:
            normalized_report = validator.validate(normalized_text, ctx)
            if not normalized_report.blocks_write:
                logger.info(
                    "scene %d.%d: NAMING_OUT_OF_POOL resolved by deterministic "
                    "substitution; skipping LLM regen",
                    chapter_number,
                    scene_number,
                )
                return normalized_text, None, None, "naming_normalized", 0
            if not any(
                v.code == "NAMING_OUT_OF_POOL"
                for v in normalized_report.violations
            ):
                # Naming cleared but other blockers remain — continue the
                # regen loop from the normalized text so the LLM never has
                # to re-fix names.
                initial_content = normalized_text
                initial_report = normalized_report

    last_model_name: str | None = None
    last_llm_run_id: UUID | None = None
    last_provider = "initial"

    async def _regenerator(feedback: str) -> str:
        nonlocal last_model_name, last_llm_run_id, last_provider
        # 7-段式 REPAIR_HINT 段：把整改指令从一句话扩展成"诊断 + 修改边界 + 重写要求"
        retry_user_prompt = (
            f"{user_prompt}\n\n"
            "---\n"
            "# REPAIR_HINT · 本场上一稿被质量门拦截\n"
            "## 诊断\n"
            f"{feedback}\n\n"
            "## 修改边界（必须遵守）\n"
            "- 剧情骨架、参与角色、场景位置、场景目标 — **保持不变**\n"
            "- 仅针对上述诊断列出的问题做定点修复\n"
            "- 字数、开篇硬指标、AI 套话黑名单等 system 中的硬约束 — **照样遵守**\n"
            "- 不要因为修复某条问题反而引入新的违规（如为减字而砍主线）\n\n"
            "## THINKING（重写前在脑内 3 步）\n"
            "1. 把诊断里的每一条问题映射到上一稿的具体段落 — 标记「该改 / 不该动」\n"
            "2. 决定每条问题的最小修复手段（改词 / 改段 / 补段 / 删段）\n"
            "3. 检查修复后是否违反 system 中任何硬约束 — 违反则回退方案\n\n"
            "## 立即开始\n"
            "输出**完整一版重写后的场景正文**（Markdown，无前言后语），不要列修改清单。"
        )
        retry = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                model_tier=model_tier,
                system_prompt=system_prompt,
                user_prompt=retry_user_prompt,
                fallback_response=fallback_content,
                prompt_template="scene_writer_regen",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                max_tokens_override=prose_output_max_tokens_for_target(
                    target_word_count,
                    language=_project_language(project),
                    settings=settings,
                    role="writer",
                ),
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene_number,
                    "context_query": context_query,
                    "protagonist_name": (protagonist_name or "").strip(),
                    "supporting_name": (supporting_name or "").strip(),
                    "model_tier": model_tier,
                    "regen_feedback_codes": [
                        v.code for v in initial_report.violations
                    ],
                },
            ),
        )
        last_model_name = retry.model_name
        last_llm_run_id = retry.llm_run_id
        last_provider = retry.provider
        cleaned = sanitize_novel_markdown_content(
            retry.content, language=_project_language(project)
        ) or fallback_content
        cleaned = strip_scaffolding_echoes(cleaned)
        return cleaned

    async def _validator_fn(text: str) -> QualityReport:
        return validator.validate(text, ctx)

    try:
        result = await regenerate_until_valid(
            initial_output=initial_content,
            initial_report=initial_report,
            regenerator=_regenerator,
            validator=_validator_fn,
            budget=scene_budget,
            global_budget=global_budget,
            context_label=f"scene-{chapter_number}-{scene_number}",
        )
    except RegenerationExhausted as exc:
        logger.warning(
            "scene %d.%d: regen budget exhausted (%d attempts); shipping best-effort",
            chapter_number,
            scene_number,
            len(exc.attempts),
        )
        best = exc.attempts[-1].output if exc.attempts else initial_content
        return (
            best,
            last_model_name,
            last_llm_run_id,
            last_provider or "regen_exhausted",
            max(0, len(exc.attempts) - 1),
        )

    return (
        result.final_output,
        last_model_name,
        last_llm_run_id,
        last_provider if result.regen_count > 0 else "initial",
        result.regen_count,
    )


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK chars ~1 token each, Latin words ~1.3 tokens each."""
    if not text:
        return 0
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9_]+", text))
    punct = len(re.findall(r"[^\w\s]", text))
    return han + int(latin * 1.3) + int(punct * 0.5)


# Priority tiers for context budget enforcement.
# Tier 1: structural contracts & safety — always included.
# Tier 2: recent narrative state — included when budget allows.
# Tier 3: background & enrichment — only when ample room.
_CONTEXT_TIER_1 = frozenset({
    "contract_section",
    "story_principle_line",
    "methodology_line",
    "participant_fact_section",
    "contradiction_line",
    "project_material_reference_line",
    "hard_fact_line",
    "knowledge_line",
    "identity_line",
    "phrase_avoidance_line",
    "genre_constraint_line",
    "progression_context_line",
    "decision_policy_line",
    "rule_system_line",
    "faction_ecology_line",
    "relationship_agency_line",
    # 榜单级能力 Profile + 番茄市场工艺卡 — sibling writing/commercial constraint blocks
    # left untiered (so Pass-4-trimmed under budget pressure, which the文采 methodology
    # levers increase). Promoted to Tier 1 alongside the constraint blocks they belong
    # with (progression/decision/rule/faction).
    "ranking_profile_line",
    "chapter_market_constraints_line",
    # 词条体系约束/注册表/状态账本 — entry-system hard constraints, same family as
    # rule_system/progression above; also left untiered (Pass-4-trimmed).
    "entry_system_line",
    "entry_registry_line",
    "entry_state_ledger_line",
    "plan_richness_line",
    # Story-integrity guardrails (2026-06-02): these were previously NOT in any
    # tier, which meant _budget_context_sections silently kept them full AND
    # uncounted. They are genuine coherence guardrails ("inviolable" per the
    # build_scene_draft_prompts header) and MUST survive budgeting, so they are
    # now explicit Tier 1 members rather than accidental budget bypassers.
    "canon_guardrails_line",
    "timeline_canon_line",
    "scene_coherence_line",
    "character_role_line",
    "chapter_length_line",
    # R23 — per-scene hard acceptance (word budget + signature obligations).
    # Front-of-prompt anchor; must always survive budgeting and is NOT in
    # ``_TIER_1_DROPPABLE_GUARDRAILS`` (dropping it re-opens the LENGTH_OVER /
    # LENGTH_UNDER oscillation it exists to fix).
    "scene_word_budget_line",
    "current_scene_contract_line",
    # Hard per-chapter commercial contracts (opening-chapter signing gates):
    # these are binding obligations, not advisory garnish, so they must not be
    # trimmed by the budget when present. (concept_lab_contract_line was
    # untiered and therefore silently Pass-4 trimmed — promoted 2026-06-03 so
    # the selected Concept-Lab contract always reaches the writer.)
    "qimao_opening_contract_line",
    "reader_contract_line",
    "concept_lab_contract_line",
    # 本章爽点约束 — a hard per-chapter commercial contract (like reader_contract),
    # not advisory garnish. Promoted to Tier 1 (2026-06) after the文采 methodology
    # levers grew the Tier-1 methodology_line and silently evicted this Tier-2 block.
    "hype_constraints_line",
})
_CONTEXT_TIER_2 = frozenset({
    "recent_scene_section",
    "emotion_track_section",
    "antagonist_plan_section",
    "clue_section",
    "scene_sequel_line",
    "structure_beat_line",
    "pacing_line",
    # Scene-craft helpers (medium value): keep when budget allows.
    "scene_beat_line",
    "hook_echo_line",
    "dialogue_voice_line",
    "arc_beat_line",
    "scene_scope_isolation_line",
    "project_material_obligation_line",
})
_CONTEXT_TIER_3 = frozenset({
    "story_bible_section",
    "arc_section",
    "arc_summary_line",
    "world_snapshot_line",
    "retrieval_section",
    "recent_timeline_section",
    "reader_knowledge_line",
    "relationship_line",
    "subplot_line",
    "ending_line",
    "obligations_line",
    "foreshadow_line",
    "tree_section",
    "pp_line",
    "pp_writer_line",
})


def _truncate_section_to_tokens(
    text: str,
    max_tokens: int,
) -> str:
    """Hard-cap a single section's text at ``max_tokens`` (F2 — single-block cap).

    Keeps the head (first 60%) + a one-line "…[truncated]…" marker + the tail
    (last 40%) so both the opening context (often the rule/contract) and the
    most recent state (often the live constraint) survive. The exact split is
    deliberate: head carries "what this section is about" and tail carries
    "what changed recently"; pure head-only truncation would drop live state,
    pure tail-only would drop the rule.

    Capped at 0 returns the original text unchanged (treat as "no cap"). A
    negative cap is clamped to 0 which forces an empty string.
    """
    if max_tokens <= 0 or not text:
        if max_tokens < 0:
            return ""
        return text
    cost = _estimate_tokens(text)
    if cost <= max_tokens:
        return text
    # Estimate the slice: target ~ ``max_tokens`` chars at the conservative
    # 1 token-per-CJK-char rate. We don't try to be exact — over-truncating
    # slightly is fine; the cap is a safety net, not a precision instrument.
    char_budget = max(64, int(max_tokens))
    if len(text) <= char_budget:
        return text
    head_budget = int(char_budget * 0.6)
    tail_budget = char_budget - head_budget
    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    return f"{head}\n…[truncated; was {cost} tokens]…\n{tail}"


# Default single-block token cap. Plan §WS-B2 asks for ≤600字 which translates
# to ~300-400 tokens for CJK. We round up to 600 to avoid breaking natural
# sentence boundaries while still preventing one canon block from eating the
# entire 8000-token budget on its own.
_DEFAULT_PER_BLOCK_TOKEN_CAP = 600
# Tier 1 soft cap: even the structural safety net must respect the budget.
# If Tier 1 alone would consume more than this fraction of the budget, we drop
# the LARGEST Tier 1 items first so the next passes (continuity / clue /
# pacing) can still get budget. 80% matches the typical Tier 2 footprint.
_TIER_1_BUDGET_FRACTION = 0.80

# F1 soft-cap may free budget for Tier-2 continuity by dropping Tier-1 items,
# but it must drop ONLY the redundant integrity *guardrails* below — never the
# binding craft/contract blocks (contract, methodology, rule_system,
# story_principle, hard_fact, …). The guardrails (canon / timeline /
# scene_coherence / character_role) are the blocks that ballooned Tier-1 to
# ~12.4k tokens in the starvation regression and are largely re-derivable from
# story_bible + hard_facts, so they are the safe ones to sacrifice. Dropping a
# binding block instead would produce off-contract or canon-contradicting prose
# (CD5, 2026-06-03).
_TIER_1_DROPPABLE_GUARDRAILS = frozenset({
    "canon_guardrails_line",
    "timeline_canon_line",
    "scene_coherence_line",
    "character_role_line",
})


def _budget_context_sections(
    sections: dict[str, str],
    budget_tokens: int,
    *,
    per_block_token_cap: int = _DEFAULT_PER_BLOCK_TOKEN_CAP,
) -> dict[str, str]:
    """Enforce a token budget on rendered context sections by tier priority.

    Tier 1 sections are always kept.  Tier 2 sections are added next.
    Tier 3 sections fill remaining budget.  Pass 4 covers every *remaining*
    section (anything not explicitly tiered — the "advanced garnish" blocks
    such as voice_dna / diversity / l3_prompt / signature_scene): these are the
    lowest priority and are trimmed first when over budget.

    2026-06-02 fix: previously only the ~39 explicitly-tiered keys were
    considered, so the other ~37 sections passed in by ``build_scene_draft_prompts``
    bypassed the budget entirely (kept full, never counted). That is why the
    writer prompt ballooned to ~17.7k tokens regardless of ``budget_tokens``.
    Pass 4 closes that hole so NO section can silently bypass the budget.

    2026-06-03 fix (WS-B starvation regression, F1+F2):
      * **F2** — every section is first hard-capped at
        ``per_block_token_cap`` (default 600 tokens) before budgeting. A
        single bloated canon block can no longer eat the entire budget on
        its own; it gets head+tail truncated instead.
      * **F1** — Tier 1 is now also subject to a soft cap
        (``_TIER_1_BUDGET_FRACTION`` of the budget). If Tier 1 alone
        exceeds that share, the LARGEST Tier 1 items are dropped first.
        This protects Tier 2 (which holds continuity sections like
        ``recent_scene_section``, ``emotion_track_section``, ``clue_section``)
        from being starved when the integrity guardrails (canon /
        timeline / character_role) are very large.
    """
    result = dict(sections)
    # F2: per-block cap — applied ONLY to the untiered Pass-4 "garnish" blocks
    # (voice_dna / diversity / l3_prompt / signature_scene / …). Those are the
    # blocks that bypassed the budget and ballooned the prompt to ~17.7k tokens,
    # and a single one can be several thousand tokens, so capping them protects
    # the budget.
    #
    # Tiered sections (Tier 1/2/3) are NOT per-block truncated: they carry
    # structured craft + contract content where head+tail truncation silently
    # deletes obligations in the middle (e.g. ``methodology_line`` bundles
    # 七猫签约门槛 / 七猫再生成合同; ``rule_system_line`` / ``story_principle_line``
    # carry binding constraints). They are bounded instead by the tier budget
    # passes below, and Tier 1 additionally by the F1 soft cap. (CD5 + the
    # F2-over-truncation regression, 2026-06-03.)
    _tiered_keys = _CONTEXT_TIER_1 | _CONTEXT_TIER_2 | _CONTEXT_TIER_3
    for key in list(result.keys()):
        if key in _tiered_keys:
            continue
        result[key] = _truncate_section_to_tokens(
            result.get(key, ""), per_block_token_cap
        )

    used = 0
    _tier1_total = sum(_estimate_tokens(result.get(k, "")) for k in _CONTEXT_TIER_1)
    _tier1_ceiling = max(0, int(budget_tokens * _TIER_1_BUDGET_FRACTION))

    # F1: if Tier 1 alone exceeds its soft ceiling, drop the LARGEST *droppable*
    # integrity guardrails (``_TIER_1_DROPPABLE_GUARDRAILS``) first until Tier 1
    # fits.  Binding craft/contract blocks are never dropped (CD5).
    #
    # IMPORTANT (futility guard): only sacrifice guardrails when doing so can
    # actually bring Tier 1 under the ceiling — i.e. when the binding core
    # (Tier 1 minus the droppable guardrails) already fits.  Otherwise the
    # overflow is caused by the binding blocks, which F1 cannot touch, and
    # dropping the guardrails just loses canon/timeline for nothing (observed:
    # an 8-token canon block dropped against a 5176-token binding overflow).
    _guardrail_total = sum(
        _estimate_tokens(result.get(k, "")) for k in _TIER_1_DROPPABLE_GUARDRAILS
    )
    _binding_core_total = _tier1_total - _guardrail_total
    if _tier1_total > _tier1_ceiling and _binding_core_total <= _tier1_ceiling:
        _tier1_droppable = sorted(
            (k for k in _CONTEXT_TIER_1 if k in _TIER_1_DROPPABLE_GUARDRAILS),
            key=lambda k: _estimate_tokens(result.get(k, "")),
            reverse=True,
        )
        for key in _tier1_droppable:
            if _tier1_total <= _tier1_ceiling:
                break
            cost = _estimate_tokens(result.get(key, ""))
            if cost <= 0:
                continue
            result[key] = ""
            _tier1_total -= cost

    # Pass 1: Tier 1 — sum tokens (never blank them in normal flow)
    for key in _CONTEXT_TIER_1:
        used += _estimate_tokens(result.get(key, ""))

    # Pass 2: Tier 2 — add in definition order while budget allows
    for key in _CONTEXT_TIER_2:
        cost = _estimate_tokens(result.get(key, ""))
        if used + cost <= budget_tokens:
            used += cost
        else:
            result[key] = ""

    # Pass 3: Tier 3 — add remaining while budget allows
    for key in _CONTEXT_TIER_3:
        cost = _estimate_tokens(result.get(key, ""))
        if used + cost <= budget_tokens:
            used += cost
        else:
            result[key] = ""

    # Pass 4: everything else (advanced garnish + any future/untiered section)
    # is the lowest priority. Process cheapest-first so a few small advanced
    # blocks can still ride along, while large ones (voice_dna, l3_prompt, ...)
    # are blanked once the budget is exhausted.
    _tiered = _CONTEXT_TIER_1 | _CONTEXT_TIER_2 | _CONTEXT_TIER_3
    _remaining = [key for key in result if key not in _tiered]
    _remaining.sort(key=lambda k: _estimate_tokens(result.get(k, "")))
    for key in _remaining:
        cost = _estimate_tokens(result.get(key, ""))
        if used + cost <= budget_tokens:
            used += cost
        else:
            result[key] = ""

    return result


_METHODOLOGY_SECTION_HEAD_RE = re.compile(r"^(?:##\s|【[^】\n]{1,60}】)", re.MULTILINE)


def _dedupe_methodology_sections(text: str) -> str:
    """Drop later methodology sections whose body duplicates an earlier one.

    ``_methodology_line`` is assembled from several independently rendered
    sources (prompt-pack bridge, scene rules, compiled methodology, quality
    levers). Sources can re-render the same lever — e.g. 场景锚定 / 情绪契约
    appear in both the compiled methodology and the quality-levers block —
    and the duplicate copies waste Tier-1 budget, which starves every Tier-2/3
    narrative section (story bible, recent scenes, clues) out of the prompt.
    Only exact-body duplicates are dropped; variant renderings are kept.
    """
    if not text:
        return text
    starts = [m.start() for m in _METHODOLOGY_SECTION_HEAD_RE.finditer(text)]
    if not starts:
        return text
    pieces: list[str] = []
    if starts[0] > 0:
        pieces.append(text[: starts[0]])
    seen: set[str] = set()
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        section = text[start:end]
        fingerprint = re.sub(r"\s+", " ", section).strip()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        pieces.append(section)
    return "".join(pieces)


_STRUCTURED_METADATA_KEYS = (
    "scene_summary",
    "chapter_summary",
    "core_conflict",
    "emotional_shift",
    "contract_alignment",
    "story_task",
    "emotion_task",
    "information_release",
    "tail_hook",
    "closing_hook",
    "entry_state",
    "exit_state",
)

_STRUCTURED_METADATA_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*|__)?"
    r"(?P<key>" + "|".join(_STRUCTURED_METADATA_KEYS) + r")"
    r"(?:\*\*|__)?\s*:\s*.+$",
    re.IGNORECASE,
)

# Chinese structural / meta-commentary terms that should NEVER appear in novel prose.
_CN_META_HEADER_RE = re.compile(
    r"^\s*#{1,4}\s*(?:修订说明|上一版草稿|重写策略|写作说明|场景说明|改写说明|润色说明"
    r"|策划说明|提纲|大纲|剧情任务|情绪任务|写法指导"
    r"|重写第\d+章第?\d*场?)\s*$"
)

_CN_META_LINE_RE = re.compile(
    r"^\s*(?:>+\s*)?[-*]?\s*(?:重写策略|本次任务|修订说明|剧情任务|情绪任务|入场状态|离场状态|收束状态"
    r"|开场状态|场景类型|场景目标|章节目标|本章目标|钩子设计|尾钩|结尾钩子|开场白设计|开场白|设想"
    r"|戏剧反讽意图|过渡方式|主题任务|信息释放|contract|合同式写作约束"
    r"|叙事树上下文|伏笔与兑现约束|关系与情绪推进约束|反派推进约束"
    r"|商业网文硬约束|Prompt Pack"
    r"|小钩子|中钩子|大钩子|章末钩子|场景钩子|章节钩子)\s*[：:].+$"
)

# Lines wrapped in Chinese fullwidth brackets 【...】 that contain planning
# labels (hook summaries, foreshadowing notes, transition markers, etc.).
# These are structural annotations the LLM leaks at scene / chapter
# boundaries and must never appear in published prose.
_CN_BRACKET_META_RE = re.compile(
    r"^\s*【(?:小钩子|中钩子|大钩子|钩子|尾钩|章末钩子|过渡|伏笔|悬念|铺垫"
    r"|章节钩子|场景钩子|hook|设定|本章目标|剧情任务|情绪任务)[：:].*】\s*$"
)

_WORD_COUNT_META_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?[\(（【\[]?\s*"
    r"(?:(?:全文|本章|本场|章节|场景|当前稿|最终)\s*)?"
    r"(?:字数|word\s*count)\s*[:：]\s*"
    r"(?:约\s*)?[\d０-９,，]+(?:\s*(?:字|words?))?"
    r"\s*[\)）】\]]?\s*$",
    re.IGNORECASE,
)

# Scene scaffold headings that must never appear in prose:
#   "## 场景 1：xxx"  /  "### 第三场"  /  "第1场" / "第一场"
# NOTE: This must NOT match chapter headings like "# 第1章：xxx" — those are
# legitimate headings inserted by _format_chapter_heading and must be preserved.
# Duplicate chapter markers (e.g. "第1章 第1章：xxx") are handled separately by
# _CN_DUPLICATE_CHAPTER_MARKER_RE.
_CN_SCAFFOLD_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:第\s*[一二三四五六七八九十百零\d]+\s*场"
    r"|场景\s*[一二三四五六七八九十百零\d]+|结尾钩子|本章目标)"
    r"(?:\s*[:：].*)?$"
)

_CN_META_PROSE_RE = re.compile(
    r"(?:这一场景要完成的剧情任务是|这一场景的情绪任务是|本场景的写作目标是"
    r"|以下是.*的(?:场景|章节|草稿|初稿|提纲|大纲)"
    r"|以下为.*改写后的版本|以上是.*的(?:重写|修订|润色)版本"
    r"|根据(?:修订|重写|润色)(?:说明|要求|策略))"
)

# ---------------------------------------------------------------------------
# English structural / meta-commentary patterns (mirrors the Chinese set above)
# ---------------------------------------------------------------------------

# English structural headers: "## Scene 1:", "Chapter 3:", "Act 2:"
_EN_META_HEADER_RE = re.compile(
    r"^(?:##?\s*)?(?:Scene\s+\d+|Chapter\s+\d+|Act\s+\d+)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)

# English metadata key-value lines: "POV:", "Setting:", "Story Goal:", etc.
_EN_META_LINE_RE = re.compile(
    r"^(?:POV|Point of View|Setting|Time|Location|Participants|"
    r"Story Goal|Emotional Goal|Scene Type|Word Count|Target|"
    r"Character Arc|Plot Purpose|Hook|Conflict Type)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)

# English scaffold headings: "## Scene 1", "### Climax", "# Inciting Incident"
_EN_SCAFFOLD_HEADING_RE = re.compile(
    r"^#{1,3}\s+(?:Scene\s+\d+|Opening|Climax|Resolution|Denouement|"
    r"Rising Action|Falling Action|Inciting Incident)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# English template leak substrings — precise phrases that only originate from
# planning prompts or template fallback prose, never from legitimate fiction.
_EN_TEMPLATE_LEAK_SUBSTRINGS: tuple[str, ...] = (
    "[Author's Note",
    "[Note:",
    "[End of",
    "--- End",
    "Word count:",
    "POV:",
    "Scene goal:",
    "This scene",
    "In this chapter",
    "The purpose of this scene",
    "This chapter establishes",
    "Moving on to",
    "As outlined in the",
    "Per the story bible",
    "According to the plan",
    "The narrative shifts to",
    "scene transitions to",
)

# English meta-reward terms — planning language that should never appear in
# novel prose. Mirrors the Chinese ``_META_REWARD_TERMS`` in reviews.py.
_EN_META_REWARD_TERMS: tuple[str, ...] = (
    "overall tone maintains",
    "chapter goal",
    "scene objective",
    "plot task",
    "emotional task",
    "narrative function",
    "story purpose",
    "character arc progression",
    "this scene serves to",
    "the reader should feel",
)

# Sentences that only ever originate from the fallback template prose in
# ``render_rewritten_scene_markdown`` / ``render_rewritten_chapter_markdown``.
# These are the exact phrases that leaked into chapters 2/3/5/7–13/15/20/25 of
# the apocalypse-supply output. They are precise enough that matching a line
# means the line is template residue, never legitimate prose.
_CN_TEMPLATE_LEAK_SUBSTRINGS: tuple[str, ...] = (
    "重新被推回《",
    "叙事仍采用",
    "这一版重写围绕",
    "third-limited 视角",
    "third-limited视角",
    "third-person limited",
    "叙事采用 third-limited",
    "真正落实到动作、停顿、呼吸和目光变化",
    "金属舱壁传来的冷意",
    "人物说出口的话和没有说出口的话同时构成冲突",
    "上一阶段留下的局势仍压在众人心头",
    "这一章不再只是承接，而是要把冲突继续推向更高层级",
    "章节收束时，",
    # Time-labelled reflection openers used by the fallback outline builder
    # ("第13章中段，程彻…", "第15章开场，周远…", "第22章结尾，…").
    "第1章开场",
    "第1章中段",
    "第1章结尾",
)

# Regex form that captures "第<digits>章(开场|中段|结尾)[，,]" at line start.
# More robust than listing every chapter number as a substring.
_CN_CHAPTER_PHASE_PREFIX_RE = re.compile(
    r"^\s*第\s*\d+\s*章(?:开场|中段|结尾)\s*[，,、]",
)

# Any standalone HTML comment — used by us to mark fallbacks and must never
# appear in published chapters.
_HTML_COMMENT_BLOCK_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)


def sanitize_novel_markdown_content(content_md: str, *, language: str | None = None) -> str:
    """Strip non-fiction structural markers and meta-commentary from novel prose.

    Detects BOTH Chinese and English meta-leaks simultaneously — a scene draft
    could contain mixed-language leaks.

    Order of operations:

    1. Remove all HTML comments (our fallback markers plus any stray notes).
    2. Drop block-level meta sections (CN: ``### 修订说明``; EN: ``### Revision Notes``).
    3. Filter line-by-line to strip structural markers, meta headers, meta
       key-value rows, scaffolding headings, meta-prose sentences and the
       rewrite template sentences in both languages.
    4. Drop the leading-paragraph "第N章中段，XX 重新被推回…" pattern even when
       the rewrite template wasn't flagged by the substring list (catches LLM
       paraphrases of the same prompt seed).
    """
    if not content_md:
        return ""
    # 1. Strip all HTML comments first so our rewrite / scene-draft fallback
    # markers never reach the final output.
    content_md = _HTML_COMMENT_BLOCK_RE.sub("", content_md)

    # 2a. Remove Chinese meta-commentary blocks entirely. These run from the
    # header to end-of-string or the next H2+ header.
    content_md = re.sub(
        r"#{1,4}\s*(?:修订说明|上一版草稿|改写说明|润色说明).*?(?=\n##\s|\Z)",
        "",
        content_md,
        flags=re.DOTALL,
    )
    # 2b. Remove English meta-commentary blocks: "### Revision Notes",
    # "## Author's Notes", "### Rewrite Strategy", etc.
    content_md = re.sub(
        r"#{1,4}\s*(?:Revision Notes?|Author'?s? Notes?|Rewrite Strategy"
        r"|Writing Notes?|Scene Notes?|Draft Notes?)\b.*?(?=\n##\s|\Z)",
        "",
        content_md,
        flags=re.DOTALL | re.IGNORECASE,
    )

    cleaned_lines: list[str] = []
    for raw_line in content_md.splitlines():
        stripped = raw_line.strip()
        # --- Shared / structural metadata (both languages) ---
        if _STRUCTURED_METADATA_LINE_RE.match(stripped):
            continue
        if _WORD_COUNT_META_LINE_RE.match(stripped):
            continue

        # --- Chinese meta-leak line filters ---
        if _CN_META_HEADER_RE.match(stripped):
            continue
        if _CN_META_LINE_RE.match(stripped):
            continue
        if _CN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        if _CN_META_PROSE_RE.search(stripped):
            continue
        if any(substr in stripped for substr in _CN_TEMPLATE_LEAK_SUBSTRINGS):
            continue
        if _CN_CHAPTER_PHASE_PREFIX_RE.match(stripped):
            continue
        if _CN_BRACKET_META_RE.match(stripped):
            continue

        # --- English meta-leak line filters ---
        if _EN_META_HEADER_RE.match(stripped):
            continue
        if _EN_META_LINE_RE.match(stripped):
            continue
        if _EN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        # Case-insensitive check for English template leak substrings
        stripped_lower = stripped.lower()
        if any(substr.lower() in stripped_lower for substr in _EN_TEMPLATE_LEAK_SUBSTRINGS):
            continue
        # English meta-reward terms leaked into prose
        if any(term in stripped_lower for term in _EN_META_REWARD_TERMS):
            continue

        cleaned_lines.append(raw_line.rstrip())

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # Strip tier-1 AI-flavor kill-on-sight phrases (zero LLM cost).
    from bestseller.services.anti_slop import strip_tier1_slop  # noqa: PLC0415

    # Always strip Chinese slop (can appear even in English drafts from bilingual models).
    cleaned = strip_tier1_slop(cleaned)
    # Also strip English slop when language is English or unknown (covers both).
    if language is None or (language and language.strip().lower().startswith("en")):
        cleaned = strip_tier1_slop(cleaned, language="en-US")
    return cleaned.strip()


# Duplicate / nested chapter-scene markers that should never appear in prose:
#   "第1章 第2场" / "第3章 第3章：碰撞" / "## 第15章 第15章：xxx"
# Matches an entire line (or paragraph-leading fragment) that starts with one
# chapter/scene marker followed by another. Line-level match is enough because
# these leaks always come in at paragraph boundaries.
_CN_DUPLICATE_CHAPTER_MARKER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?第\s*[一二三四五六七八九十百零\d]+\s*[章场]"
    r"[\s·：:、，,]*第\s*[一二三四五六七八九十百零\d]+\s*[章场]"
    r".*$"
)

# Mid-content chapter heading: "# 第N章 XYZ" / "# 第N章：副标题" appearing AFTER
# the first line. The legitimate chapter heading sits at position 0 (prepended
# by _format_chapter_heading). Any subsequent "# 第N章 ..." line is a leaked
# outline note, scene label, or planning task and must be stripped.
# Only matches markdown headings (#{1,4} prefix) — bare "第N章" in prose is ok.
# The character after 章 may be whitespace, Chinese/ASCII colon, or end-of-string.
_CN_MID_CONTENT_CHAPTER_HEADING_RE = re.compile(
    r"^\s*#{1,4}\s*第\s*[一二三四五六七八九十百零\d]+\s*章(?:[\s：:].*|$)"
)

# Prose-wrapped reasoning / rewrite-plan paragraphs, e.g.:
#   "第15章开场，程彻、周远重新被推回《...》第15章的核心冲突..."
# Matches a paragraph (delimited by blank lines) whose FIRST line starts with
# "第N章" followed by planning vocabulary. We erase the whole paragraph so
# multi-line reflections are cleaned in one shot. Anchored to start-of-string
# or double-newline to avoid eating legitimate in-dialogue mentions.
_CN_LEADING_REASONING_PARA_RE = re.compile(
    r"(?:^|\n\n)\s*第\s*[一二三四五六七八九十百零\d]+\s*章[^\n]*?"
    r"(?:开场|的核心冲突|继续|承接|重写围绕|重写的|这一版)[^\n]*"
    r"(?:\n[^\n]+)*?"
    r"(?=\n\n|\Z)"
)

# English mid-content chapter heading: "# Chapter 4: The Clash" appearing after
# the first line. Mirrors _CN_MID_CONTENT_CHAPTER_HEADING_RE.
_EN_MID_CONTENT_CHAPTER_HEADING_RE = re.compile(
    r"^\s*#{1,4}\s*Chapter\s+\d+(?:[\s:：].*|$)",
    re.IGNORECASE,
)

# English leading reasoning paragraph: "Chapter 5 opens with..." / "This
# rewrite focuses on..." — AI reflection that leaked as the first paragraph.
_EN_LEADING_REASONING_PARA_RE = re.compile(
    r"(?:^|\n\n)\s*(?:Chapter\s+\d+\s+(?:opens|begins|continues|picks up)|"
    r"This\s+(?:rewrite|revision|draft)\s+(?:focuses|centers|aims)|"
    r"In\s+this\s+(?:rewrite|revision|version))[^\n]*"
    r"(?:\n[^\n]+)*?"
    r"(?=\n\n|\Z)",
    re.IGNORECASE,
)

# Additional phrase-pair rules for has_meta_leak. Each tuple is a list of
# phrases that must all be present for the pair to count as a leak — this
# avoids false positives where "视角" or "开场" appears in legitimate prose.
_HAS_META_PHRASE_PAIRS: tuple[tuple[str, ...], ...] = (
    ("这一版", "重写"),
    ("重写围绕",),
    ("叙事仍采用",),
    ("third-limited",),
    ("third limited",),
    ("third-person limited",),
    ("核心冲突", "第", "章"),  # "第X章的核心冲突"
    ("开场", "重新被推回"),
)

# English phrase-pair rules: mirrors _HAS_META_PHRASE_PAIRS for English content.
_EN_HAS_META_PHRASE_PAIRS: tuple[tuple[str, ...], ...] = (
    ("this rewrite", "focuses on"),
    ("scene objective",),
    ("chapter goal",),
    ("narrative function",),
    ("the reader should feel",),
    ("this scene serves to",),
    ("character arc", "progression"),
    ("per the story bible",),
    ("according to the plan",),
)


def strip_scaffolding_echoes(content_md: str) -> str:
    """Strip duplicate chapter markers and leading AI-reasoning paragraphs.

    This runs AFTER ``sanitize_novel_markdown_content`` and is the last
    regex-level net before falling back to LLM-based cleanup. It catches
    leaks in both Chinese and English:

    1. Duplicate / nested chapter-scene headers like "第1章 第2场" or
       "第3章 第3章：碰撞", which the sanitizer's line-start regex can't
       match when both markers land on the same line.
    2. Mid-content chapter headings — CN: "# 第4章 关键碰撞";
       EN: "# Chapter 4: The Clash" — leaked outline notes / planning
       tasks that mimic chapter headings. The first-line heading is preserved.
    3. Prose-wrapped AI reflection paragraphs — CN: "第15章开场，XXX 重新
       被推回 ..."; EN: "Chapter 5 opens with ..." / "This rewrite focuses
       on ..." — where the LLM leaked its rewrite plan as the first paragraph.
    """
    if not content_md:
        return content_md

    # Erase duplicate chapter markers and mid-content chapter headings.
    cleaned_lines: list[str] = []
    first_line_seen = False
    for line in content_md.splitlines():
        stripped = line.strip()
        # Chinese duplicate / nested chapter-scene markers
        if _CN_DUPLICATE_CHAPTER_MARKER_RE.match(stripped):
            continue
        # Strip mid-content "# 第N章 ..." headings (leaked outline/planning
        # notes). The first non-blank line is skipped — it may be the
        # legitimate chapter heading from _format_chapter_heading.
        if first_line_seen and _CN_MID_CONTENT_CHAPTER_HEADING_RE.match(stripped):
            continue
        # Strip mid-content "# Chapter N ..." headings (English equivalent)
        if first_line_seen and _EN_MID_CONTENT_CHAPTER_HEADING_RE.match(stripped):
            continue
        # Strip English scaffold headings that survived the line-level filter
        # (e.g. "## Scene 3" / "### Climax" appearing mid-content)
        if first_line_seen and _EN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        if stripped:
            first_line_seen = True
        cleaned_lines.append(line)
    content_md = "\n".join(cleaned_lines)

    # Erase prose-wrapped reasoning paragraphs (Chinese). Loop until stable in
    # case multiple reflection paragraphs stack at the top.
    while True:
        new_content = _CN_LEADING_REASONING_PARA_RE.sub("", content_md, count=1)
        if new_content == content_md:
            break
        content_md = new_content

    # Erase prose-wrapped reasoning paragraphs (English).
    while True:
        new_content = _EN_LEADING_REASONING_PARA_RE.sub("", content_md, count=1)
        if new_content == content_md:
            break
        content_md = new_content

    content_md = re.sub(r"\n{3,}", "\n\n", content_md)

    # Normalize quotation marks to a consistent format
    try:
        from bestseller.services.output_hygiene import normalize_quote_format

        content_md = normalize_quote_format(content_md, language=None)
    except Exception:
        pass  # Non-fatal

    return content_md.strip()


logger = logging.getLogger(__name__)


def _bundle_hook_domain_tokens(project) -> tuple[str, ...]:
    """Book-derived hook vocabulary for the quality bundle's hook-echo check.

    Same source as the production-side injection (imagery anchors) so the
    duty block and validation always extract the same token set. Fails to
    () — the generic extraction layers carry the gate without it.
    """

    try:
        from bestseller.services.imagery_system_design import (
            imagery_anchor_phrases,
        )

        return imagery_anchor_phrases(project)
    except Exception:
        return ()

# Shared prohibition block injected into all writer / editor system prompts.
# Uses triple-quoted string to safely contain Chinese fullwidth quotes.
_NOVEL_OUTPUT_PROHIBITION = """\
【严禁出现以下内容】：
- 不得出现开场结构、结尾牵引、入场状态、离场状态、收束状态、剧情任务、情绪任务等策划术语
- 严禁在章节或场景末尾输出任何内部摘要标记或策划标签——这些是内部规划信息，绝不能出现在正文中
- 不得出现\u201c修订说明\u201d\u201c重写策略\u201d\u201c上一版草稿\u201d\u201c场景说明\u201d\u201c写法指导\u201d等元评论
- 不得出现\u201c这一场景要完成的剧情任务是\u201d\u201c以下是\u201d\u201c以上是\u201d等解释性前缀
- 不得出现 entry_state / exit_state / contract / scene_type 等英文结构化标签
- 不得输出 Markdown 标题标记（# 或 ##）——正文中不需要章节标题、场景标题或任何层级标题
- 不得把\u201c章节目标\u201d\u201c场景标题\u201d\u201c卷目标\u201d原文搬入正文——这些信息仅供理解意图
- 不得输出\u201c字数：598\u201d\u201c（字数：598）\u201d等字数统计或自检标记
- 所有策划信息（场景目的、情绪目标、contract 约束）仅供你理解意图，严禁直接输出到正文
- 严禁使用AI味套话：\u201c显而易见\u201d\u201c毫无疑问\u201d\u201c不言而喻\u201d\u201c心中五味杂陈\u201d\u201c空气仿佛凝固了\u201d等
- 严禁堆砌虚弱修饰副词（缓缓、轻轻、微微、淡淡），同类副词每千字不超过2次
- 严禁模板式微表情描写（眼眶微红、嘴角上扬、瞳孔骤缩），用具体动作替代
- 每个角色说话必须有自己的风格——参考角色语言指纹，不同角色的对话必须可区分
- 输出中只允许出现：叙事散文、对话、动作描写、环境描写、内心活动

【AI套话黑名单——以下表达绝对禁止】：
- "血液仿佛凝固了" / "血液冰封" / "浑身的血液都冷了"
- "空气仿佛凝固了" / "时间仿佛静止了" / "周围的一切仿佛都消失了"
- "心中五味杂陈" / "心中百感交集" / "眼眶不由得湿润了"
- "一股莫名的情绪" / "一种说不清的感觉" / "一阵莫名的恐惧"
- "电流般的感觉" / "触电般的感觉" / "沉甸甸的"
- "仿佛有一只无形的手" / "像是被什么东西攫住了"
用具体、原创、从故事世界中生长出来的意象替代这些套话。

【系统面板/游戏界面规则】（如适用 LitRPG/GameLit 类型）：
- 系统面板/代码块每场最多出现 2-3 次，不是每段一个
- 面板必须短小（不超过 4-5 行），不要大段倾倒状态数据
- 故事必须脱离面板独立成立——用动作、角色反应传递危险和信息
- 先写角色的身体/情绪反应，再出面板。不要用面板代替张力
"""

_NOVEL_OUTPUT_PROHIBITION_EN = """\
FORBIDDEN OUTPUT — the following must NEVER appear in the prose:
- Do not output planning terms for opening structures, ending pull, premises, entry state, exit state, closing state, story task, or emotion task
- Do not output summary labels at the end of scenes or chapters — these are internal planning tags and must never appear in prose
- Do not output meta-commentary: "revision notes", "rewrite strategy", "previous draft", "scene notes", "writing guidance"
- Do not output explanatory prefixes: "The story task for this scene is", "The following is", "The above is"
- Do not output structural labels: entry_state / exit_state / contract / scene_type
- Do not output Markdown heading markers (# or ##) — the prose does not need chapter titles, scene titles, or any heading levels
- Do not copy "chapter goal", "scene title", "volume goal" text verbatim into the prose — that information is for your understanding only
- Do not output word-count reports such as "Word count: 598" or "(word count: 598)"
- All planning information (scene purpose, emotional goals, contract constraints) is for your understanding ONLY — never output it into the prose
- Avoid weak filler adverbs (slowly, gently, slightly, softly) — no more than 2 uses of the same adverb per 1000 words
- Avoid template micro-expressions (eyes reddened, lips curled, pupils constricted) — use specific actions instead
- Every character must speak with their own distinct voice — reference the character voice fingerprint; different characters' dialogue must be distinguishable
- Output ONLY: narrative prose, dialogue, action, environmental description, internal thought

BANNED AI CLICHÉS — these phrases instantly mark text as machine-generated. NEVER use them:
- "blood crystallized" / "blood ran cold" / "blood turned to ice"
- "words landed like a stone in still water" / "words hung in the air"
- "cold as vacuum" / "frozen fire" / "liquid fire"
- "something almost like [emotion]" / "something that might have been [emotion]"
- "the world narrowed to" / "time seemed to slow" / "the air itself seemed to"
- "a laugh that held no humor" / "a smile that didn't reach their eyes"
- "electricity crackled between them" / "tension thick enough to cut"
- "It goes without saying" / "Without a doubt" / "Needless to say"
- "every fiber of their being" / "a weight settled in their chest"
- "the silence was deafening" / "pregnant pause" / "comfortable silence"
Replace these with concrete, specific, original imagery drawn from the story's world.

SYSTEM UI / GAME INTERFACE RULE (for LitRPG/GameLit genres):
- System panels, stat blocks, and notifications may appear at most 2-3 times per scene (NOT per paragraph).
- System text must be SHORT (max 4-5 lines) — never a full-screen dump of stats, quests, and warnings.
- The story must function WITHOUT the panels — use prose, action, and character reaction to convey danger and information.
- Never use system panels as a substitute for tension. Show the character's physical/emotional reaction FIRST, panel SECOND.
- If a scene has more than 3 panels, rewrite the excess as narrative prose or internal thought.
"""

# Quick heuristic: if any of these terms appear in the output, it likely
# contains non-fiction meta-commentary that slipped through the regex filter.
_META_LEAK_KEYWORDS = (
    # --- Chinese ---
    "修订说明", "上一版草稿", "重写策略", "本次任务",
    "剧情任务是", "情绪任务是", "入场状态：", "离场状态：",
    "收束状态：", "开场状态：", "entry_state", "exit_state",
    "scene_summary", "contract_alignment", "tail_hook",
    "closing_hook", "story_task", "emotion_task",
    # Hook summary labels leaked at scene / chapter boundaries.
    "小钩子", "中钩子", "大钩子",
    # Rewrite-plan vocabulary that leaked into the body of a rewritten chapter
    # (see reviews.build_chapter_rewrite_prompts — the LLM occasionally
    # paraphrases rewrite_strategy back at us instead of writing prose).
    "这一版重写", "重写围绕", "叙事仍采用",
    "third-limited", "third limited", "third-person limited",
    # --- English ---
    "[Author's Note",
    "[Note:",
    "[End of",
    "Word count:",
    "POV:",
    "Scene goal:",
    "The purpose of this scene",
    "This chapter establishes",
    "Per the story bible",
    "According to the plan",
    "This scene serves to",
    "The reader should feel",
    "In this rewrite",
    "This revision focuses",
    "Scene objective:",
    "Chapter goal:",
    "Narrative function:",
    "As per the outline",
    "Based on the story bible",
)


def has_meta_leak(content_md: str) -> bool:
    """Return True if *content_md* still contains non-fiction meta-commentary.

    Scans for both Chinese and English meta-leak indicators simultaneously.
    """
    if any(kw in content_md for kw in _META_LEAK_KEYWORDS):
        return True
    # Chinese phrase-pair check: each rule fires only if EVERY phrase in the
    # tuple is present. This lets us flag ambiguous single words ("开场",
    # "视角") only when they co-occur with other planning vocabulary.
    if any(
        all(phrase in content_md for phrase in phrases)
        for phrases in _HAS_META_PHRASE_PAIRS
    ):
        return True
    # English phrase-pair check (case-insensitive for natural prose matching).
    content_lower = content_md.lower()
    return any(
        all(phrase in content_lower for phrase in phrases)
        for phrases in _EN_HAS_META_PHRASE_PAIRS
    )


async def validate_and_clean_novel_content(
    session: AsyncSession,
    settings: AppSettings,
    content_md: str,
    *,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> str:
    """LLM-based content validation gate.

    Called after ``sanitize_novel_markdown_content`` only when the heuristic
    ``has_meta_leak`` still detects non-fiction markers.  The critic role
    rewrites the offending paragraphs, keeping story content intact.
    """
    # Fast path: no leak detected — skip LLM call entirely.
    if not has_meta_leak(content_md):
        return content_md

    logger.warning(
        "Meta-commentary leak detected in output (len=%d), invoking LLM cleanup",
        len(content_md),
    )

    system_prompt = (
        "# ROLE\n"
        "你是小说正文校验编辑——专门清理「策划信息泄漏到正文」的污染。\n"
        "你做过大量长篇连载的「正文净化」，最擅长在保留 100% 故事内容的前提下定点删除策划标签。\n"
        "\n"
        "# CONTEXT\n"
        "上游 sanitize_novel_markdown_content 已做过正则清理，但仍有非小说内容遗漏。\n"
        "你是这一波 LLM cleanup 的最后兜底——清理后会进入读者面前的正文流。\n"
        "过度删除 = 章节字数掉下达标线触发重写，所以你只能定点剜除，不能粗暴重写。\n"
        "\n"
        "# TASK\n"
        "输出清理后的完整章节正文 Markdown。不要解释、不要修改清单、不要 diff 标记。\n"
        "\n"
        "# CONSTRAINTS · 非小说内容定义（必须清除）\n"
        "1. **策划术语**：开场结构标签 / 结尾牵引标签 / 剧情任务 / 情绪任务 / 入场状态 / 离场状态 / 收束状态\n"
        "2. **元评论**：修订说明 / 重写策略 / 上一版草稿 / 写法指导 / 场景说明 / 编辑提示\n"
        "3. **英文结构标签**：entry_state / exit_state / scene_summary / contract / scene_type 等\n"
        "4. **解释性前缀后缀**：\u201c以下是\u201d / \u201c以上是\u201d / \u201c这一场景要完成的剧情任务是\u201d / \u201c以上正文遵守了\u201d\n"
        "\n"
        "# CONSTRAINTS · 处理纪律（HARD）\n"
        "- 段落**完全是**元评论 / 策划说明 → 删除整段\n"
        "- 段落**混合**小说正文 + 策划术语 → **只删策划术语，保留小说正文**\n"
        "- **不要改变**小说正文的情节、对话、描写、节奏\n"
        "- **不要添加**任何新内容（不补段、不重写句、不优化用词）\n"
        "- **不要总结、不要压缩**正文——你不是摘要器\n"
        "- 输出删除后的完整 Markdown 正文流，无前言后语\n"
        "\n"
        "# THINKING（动手清理前在脑内 3 步）\n"
        "1. 通读正文，标记每个含污染的段落，区分「整段删」vs「定点删」\n"
        "2. 定点删时只剜出策划术语的那一句 / 那一行，前后小说叙事必须无缝衔接\n"
        "3. 自检：清理后总字数应 ≥ 原文 70%；如果剩不到 70%，说明你删过头了，回退\n"
    )
    user_prompt = (
        "## 需要校验的小说正文\n"
        "```\n"
        f"{content_md}\n"
        "```\n"
        "\n## 立即开始\n"
        "按 system 的 3 步 THINKING 思考后，输出清理后的完整正文 Markdown。"
    )

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=content_md,
            prompt_template="content_validation",
            prompt_version="1.0",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={"task": "meta_leak_cleanup"},
        ),
    )
    cleaned = sanitize_novel_markdown_content(completion.content)
    if not cleaned:
        logger.warning("LLM cleanup returned empty content, falling back to original")
        return content_md
    # ── Catastrophic-shrink safeguard ────────────────────────────────────
    # The critic LLM occasionally over-deletes legitimate prose along with
    # the meta-commentary, especially on long chapters where it summarises
    # rather than scrubs.  When the cleaner drops more than 30% of the
    # content, fall back to the rule-based sanitisation result (the input
    # ``content_md`` is already post-sanitize at this call site).  This
    # prevents downstream under-length blocks from being caused by the
    # cleaner itself rather than by the writer model.
    original_len = max(len(content_md), 1)
    cleaned_len = len(cleaned)
    shrink_ratio = (original_len - cleaned_len) / original_len
    if shrink_ratio > 0.30:
        logger.warning(
            "LLM cleanup shrank content from %d -> %d chars (%.0f%% loss); "
            "this exceeds the 30%% safeguard threshold. Falling back to "
            "rule-based sanitised content to avoid under-length regression.",
            original_len,
            cleaned_len,
            shrink_ratio * 100,
        )
        return content_md
    return cleaned


async def _collect_post_assembly_duplicate_findings(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    content_md: str,
    extra_local_findings: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[WriteSafetyFinding, ...]:
    """Run final duplicate checks before an assembled chapter becomes usable.

    ``extra_local_findings`` lets callers forward findings that were detected
    on the *pre-cleanup* text (e.g. cross-scene beat re-enactment clusters
    whose near-verbatim anchors were already removed deterministically — the
    paraphrase-level cluster still needs a rewrite_task repair even though a
    re-detection on the cleaned text may fall below the cluster threshold).
    """
    from bestseller.services.chapter_first_sentence_diversity_gate import (
        check_first_sentence_diversity,
    )
    from bestseller.services.deduplication import (
        check_opening_diversity,
        detect_chapter_text_loop,
        detect_cross_chapter_repetition,
        detect_cross_scene_beat_reenactment,
        detect_intra_chapter_repetition,
        detect_short_cluster_near_repeat,
        extract_chapter_opening,
    )
    from bestseller.services.opening_hook_density_gate import (
        check_opening_hook_density,
    )

    findings: list[WriteSafetyFinding] = []
    local_findings = (
        detect_chapter_text_loop(content_md or "")
        + detect_short_cluster_near_repeat(content_md or "")
        + detect_intra_chapter_repetition(content_md or "")
        # Cross-scene beat re-enactment — paraphrase-level clusters carry
        # repair_strategy="rewrite_task" in their payload so the repair
        # pipeline rewrites the later cluster instead of deleting prose.
        + detect_cross_scene_beat_reenactment(content_md or "")
        + [dict(item) for item in (extra_local_findings or [])]
    )
    # Forwarded pre-cleanup findings may coincide with a re-detection on the
    # cleaned text — drop exact message duplicates so repair is not double-fed.
    _seen_local_messages: set[str] = set()
    _deduped_local: list[dict[str, Any]] = []
    for finding in local_findings:
        message_key = str(finding.get("message") or "")
        if message_key and message_key in _seen_local_messages:
            continue
        _seen_local_messages.add(message_key)
        _deduped_local.append(finding)
    local_findings = _deduped_local
    for finding in local_findings:
        findings.append(
            WriteSafetyFinding(
                source="post_assembly_duplicate_gate",
                code=INTRA_CHAPTER_DUPLICATE_BLOCK_CODE,
                severity=str(finding.get("severity") or "critical"),
                message=str(finding.get("message") or "章节内部仍存在重复内容。"),
                evidence=str(finding.get("text") or finding.get("sample") or ""),
                payload=dict(finding),
            )
        )

    result = await session.execute(
        select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number < chapter.chapter_number,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    previous_rows = list(result.all()) if hasattr(result, "all") else []
    chapter_texts: list[tuple[int, str]] = [
        (int(chapter_number), text or "")
        for chapter_number, text in previous_rows
        if text
    ]
    opening_gate_findings: list[WriteSafetyFinding] = []
    current_opening = extract_chapter_opening(content_md or "")
    if current_opening:
        previous_openings = [
            (int(chapter_number), extract_chapter_opening(text or ""))
            for chapter_number, text in previous_rows
            if text
        ]
        for finding in check_opening_diversity(
            current_opening,
            [(n, opening) for n, opening in previous_openings[-12:] if opening],
            similarity_threshold=0.72,
            opening_length=80,
        )[:5]:
            source_chapter = int(finding.get("chapter") or 0)
            source_opening = next(
                (
                    opening
                    for chapter_number, opening in previous_openings
                    if chapter_number == source_chapter
                ),
                "",
            )
            similarity = float(finding.get("similarity") or 0.0)
            message = (
                f"[章节开头重复] 第{chapter.chapter_number}章开头与第"
                f"{source_chapter}章相似度 {similarity:.0%}："
                f"{current_opening[:80]}。必须重写第一段，从本章独有的"
                "动作、冲突、新证据或人物决策切入，禁止复用短模板句。"
            )
            opening_gate_findings.append(
                WriteSafetyFinding(
                    source="post_assembly_opening_diversity_gate",
                    code=CHAPTER_OPENING_REPETITION_BLOCK_CODE,
                    severity="critical",
                    message=message,
                    evidence=current_opening[:120],
                    payload={
                        "chapter": int(chapter.chapter_number),
                        "source_chapter": source_chapter,
                        "similarity": similarity,
                        "opening": current_opening,
                        "source_opening": source_opening,
                    },
                )
            )
        first_sentence_result = check_first_sentence_diversity(
            current_first_sentence=current_opening,
            recent_first_sentences={
                int(chapter_number): opening
                for chapter_number, opening in previous_openings[-10:]
                if opening
            },
            similarity_threshold=0.70,
            distance_threshold=0.30,
        )
        if not first_sentence_result.passed:
            source_chapter = int(first_sentence_result.matched_chapter or 0)
            source_opening = next(
                (
                    opening
                    for chapter_number, opening in previous_openings
                    if chapter_number == source_chapter
                ),
                "",
            )
            opening_gate_findings.append(
                WriteSafetyFinding(
                    source="post_assembly_first_sentence_diversity_gate",
                    code=CHAPTER_OPENING_REPETITION_BLOCK_CODE,
                    severity="critical",
                    message=(
                        f"[章节首句重复] 第{chapter.chapter_number}章首句与第"
                        f"{source_chapter}章过近：{first_sentence_result.reason}。"
                        "必须重写第一段，禁止复用循环模板句。"
                    ),
                    evidence=current_opening[:120],
                    payload={
                        "chapter": int(chapter.chapter_number),
                        "source_chapter": source_chapter,
                        "similarity": first_sentence_result.similarity_max,
                        "opening": current_opening,
                        "source_opening": source_opening,
                    },
                )
            )
    chapter_texts.append((int(chapter.chapter_number), content_md or ""))
    for finding in detect_cross_chapter_repetition(chapter_texts):
        if int(finding.get("chapter") or 0) != int(chapter.chapter_number):
            continue
        findings.append(
            WriteSafetyFinding(
                source="post_assembly_duplicate_gate",
                code=DUPLICATE_CONTENT_BLOCK_CODE,
                severity=str(finding.get("severity") or "critical"),
                message=str(finding.get("message") or "章节与前文存在重复内容。"),
                evidence=str(finding.get("text") or ""),
                payload=dict(finding),
            )
        )
    findings.extend(opening_gate_findings)
    for finding in check_opening_hook_density(
        content_md or "",
        int(chapter.chapter_number or 0),
    ):
        if finding.severity not in {"critical", "high"}:
            continue
        findings.append(
            WriteSafetyFinding(
                source="post_assembly_opening_hook_density_gate",
                code=finding.code,
                severity=finding.severity,
                message=finding.detail,
                evidence=str(finding.evidence.get("first_200") or finding.evidence),
                payload={
                    "chapter": int(chapter.chapter_number or 0),
                    "evidence": finding.evidence,
                },
            )
        )
    return tuple(findings)


def _stamp_duplicate_content_block(
    chapter: ChapterModel,
    findings: tuple[WriteSafetyFinding, ...],
) -> None:
    if not findings:
        return
    first = findings[0]
    chapter_meta = dict(chapter.metadata_json or {})
    chapter_meta["blocked_by_write_safety_gate"] = True
    chapter_meta["write_safety_block_code"] = first.code
    chapter_meta["write_safety_hint"] = first.message
    chapter_meta["post_assembly_duplicate_gate"] = {
        "status": "blocked",
        "finding_count": len(findings),
        "findings": [
            {
                "source": finding.source,
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "evidence": finding.evidence,
                "payload": finding.payload,
            }
            for finding in findings[:20]
        ],
    }
    chapter.metadata_json = chapter_meta


def _stamp_chapter_quality_bundle(
    chapter: ChapterModel,
    report: ChapterQualityBundleReport,
) -> None:
    """Persist the unified quality snapshot on the chapter row."""

    chapter_meta = dict(chapter.metadata_json or {})
    report_payload = report.to_dict()
    previous_blocking = tuple(
        str(code)
        for code in (
            chapter_meta.get("quality_bundle_blocking_codes")
            or chapter_meta.get("quality_gate_block_codes")
            or ()
        )
        if code
    )
    blocking_codes = tuple(str(code) for code in report_payload["blocking_codes"] if code)
    repairable_codes = tuple(str(code) for code in report_payload["repairable_codes"] if code)

    chapter_meta["quality_bundle"] = report_payload
    chapter_meta["quality_contract_version"] = report.contract_version
    chapter_meta["quality_findings"] = report_payload["findings"]
    chapter_meta["quality_bundle_passed"] = report.passed

    closure = evaluate_quality_closure(previous_blocking, blocking_codes)
    if report.passed:
        chapter_meta.pop("quality_bundle_blocking_codes", None)
        chapter_meta.pop("quality_gate_block_codes", None)
        chapter_meta.pop("production_block_code", None)
        previous_repair_codes = chapter_meta.pop("auto_repair_last_block_codes", None)
        if previous_repair_codes:
            chapter_meta["auto_repair_last_resolved_block_codes"] = previous_repair_codes
        chapter_meta.pop("auto_repair_exhausted", None)
        chapter_meta.pop("auto_repair_in_progress", None)
        # Once the chapter clears the gate we wipe the cross-run cumulative
        # counter too — a future regression should start the budget fresh
        # rather than already being mid-way through its cross-run cap.
        chapter_meta.pop("auto_repair_total_attempts", None)
        # Same reasoning for the autonomous_quality_retrofit counter — once
        # the chapter passes its quality bundle, future retrofit specs
        # earn a clean budget allocation.
        chapter_meta.pop("autonomous_quality_retrofit_attempts_active", None)
        chapter_meta.pop("autonomous_quality_retrofit_exhausted", None)
        chapter_meta["quality_closure"] = closure.to_dict()
    else:
        chapter_meta["quality_bundle_blocking_codes"] = list(blocking_codes)
        chapter_meta["quality_gate_block_codes"] = list(blocking_codes)
        chapter_meta["production_block_code"] = blocking_codes[0] if blocking_codes else ""
        if repairable_codes:
            chapter_meta["auto_repair_last_block_codes"] = list(repairable_codes)
        chapter_meta["quality_closure"] = closure.to_dict()
    chapter.metadata_json = chapter_meta


def _clear_scene_auto_repair_residue_after_clean_assembly(
    scenes: Sequence[SceneCardModel],
) -> int:
    """Remove stale repair hints once the assembled chapter passes quality gates."""

    cleared = 0
    for scene in scenes:
        metadata = dict(getattr(scene, "metadata_json", None) or {})
        if not metadata:
            continue
        next_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in _SCENE_AUTO_REPAIR_RESIDUE_KEYS
        }
        if next_metadata == metadata:
            continue
        scene.metadata_json = next_metadata
        cleared += 1
    return cleared


async def _collect_previous_current_chapter_texts(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter_number: int,
) -> tuple[tuple[int, str], ...]:
    """Load prior current chapter texts for cross-chapter gates."""

    result = await session.execute(
        select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number < chapter_number,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    rows = list(result.all()) if hasattr(result, "all") else []
    return tuple((int(number), text or "") for number, text in rows if text)


def _render_state(state: dict[str, Any]) -> str:
    if not state:
        return "暂无明确状态"
    return "；".join(f"{key}: {value}" for key, value in state.items())


def _render_purpose(purpose: dict[str, Any], key: str, fallback: str) -> str:
    value = purpose.get(key)
    return str(value) if value else fallback


def _normalize_fragment(text: str) -> str:
    return text.strip().rstrip("。！？!?")


def _render_story_bible_section(
    story_bible_context: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    if not story_bible_context:
        return ""
    is_en = is_english_language(language)
    lines: list[str] = []
    if story_bible_context.get("logline"):
        lines.append(
            f"{'Series spine' if is_en else '全书主线'}：{story_bible_context['logline']}"
        )
    backbone = story_bible_context.get("world_backbone") or {}
    if backbone.get("mainline_drive"):
        lines.append(
            f"{'Mainline drive' if is_en else '全书主旋律'}：{backbone['mainline_drive']}"
        )
    if backbone.get("thematic_melody"):
        lines.append(
            f"{'Thematic melody' if is_en else '主题旋律'}：{backbone['thematic_melody']}"
        )
    if backbone.get("invariant_elements"):
        lines.append(
            f"{'Do-not-break elements' if is_en else '不可轻改元素'}："
            f"{(', ' if is_en else '、').join(str(item) for item in backbone['invariant_elements'][:5])}"
        )
    if story_bible_context.get("themes"):
        lines.append(
            f"{'Themes' if is_en else '主题'}："
            f"{(', ' if is_en else '、').join(str(item) for item in story_bible_context['themes'])}"
        )
    volume = story_bible_context.get("volume") or {}
    if volume.get("goal"):
        lines.append(f"{'Volume goal' if is_en else '本卷目标'}：{volume['goal']}")
    if volume.get("obstacle"):
        lines.append(f"{'Volume obstacle' if is_en else '本卷障碍'}：{volume['obstacle']}")
    frontier = story_bible_context.get("volume_frontier") or {}
    if frontier.get("frontier_summary"):
        lines.append(
            f"{'Current world frontier' if is_en else '当前世界边界'}：{frontier['frontier_summary']}"
        )
    if frontier.get("expansion_focus"):
        lines.append(
            f"{'Current expansion focus' if is_en else '当前扩张焦点'}：{frontier['expansion_focus']}"
        )
    if frontier.get("active_locations"):
        lines.append(
            f"{'Active locations' if is_en else '当前主要舞台'}："
            f"{(', ' if is_en else '、').join(str(item) for item in frontier['active_locations'][:4])}"
        )
    if frontier.get("active_factions"):
        lines.append(
            f"{'Active factions' if is_en else '当前活跃势力'}："
            f"{(', ' if is_en else '、').join(str(item) for item in frontier['active_factions'][:4])}"
        )
    rules = story_bible_context.get("world_rules") or []
    if rules:
        rendered_rules = "；".join(
            f"{item['name']}({item['story_consequence'] or item['description']})"
            for item in rules[:3]
        )
        lines.append(f"{'Key world rules' if is_en else '关键世界规则'}：{rendered_rules}")
    reveal_status = story_bible_context.get("deferred_reveal_status") or {}
    hidden_reveal_count = reveal_status.get("hidden_count")
    if isinstance(hidden_reveal_count, int) and hidden_reveal_count > 0:
        lines.append(
            (
                f"There are still {hidden_reveal_count} deferred reveals that must stay hidden; preserve them through anomalies and suspense only."
                if is_en
                else f"仍有 {hidden_reveal_count} 个延后揭示不得提前说破，只能通过异常与悬念间接保留。"
            )
        )
    next_gate = story_bible_context.get("next_expansion_gate") or {}
    if next_gate.get("condition_summary"):
        lines.append(
            f"{'Next expansion gate' if is_en else '下一层世界解锁条件'}：{next_gate['condition_summary']}"
        )
    # Render canonical character definitions from cast_spec so the LLM always
    # sees immutable character attributes (background, role, relationships).
    cast_spec = story_bible_context.get("cast_spec") or {}
    cast_characters = cast_spec.get("characters") or []
    if not cast_characters:
        # Fallback: try protagonist + allies + antagonists keys
        for _key in ("protagonist", "allies", "antagonists"):
            _val = cast_spec.get(_key)
            if isinstance(_val, dict):
                cast_characters.append(_val)
            elif isinstance(_val, list):
                cast_characters.extend(item for item in _val if isinstance(item, dict))
    if cast_characters:
        cast_lines: list[str] = []
        for char in cast_characters[:6]:
            parts = [f"{char.get('name', 'Unknown' if is_en else '未知')}"]
            if char.get("role"):
                parts.append(f"{'Role' if is_en else '角色'}:{char['role']}")
            if char.get("background"):
                bg = str(char["background"])[:80]
                parts.append(f"{'Background' if is_en else '背景'}:{bg}")
            cast_lines.append((" | " if is_en else "｜").join(parts))
        lines.append(
            ("Core cast anchors (do not alter):\n" if is_en else "【核心角色设定（不可更改）】：\n")
            + "\n".join(cast_lines)
        )
    deceased_characters = story_bible_context.get("deceased_characters") or []
    if deceased_characters:
        if is_en:
            dead_lines = [
                f"- {dc['name']}(died ch{dc.get('death_chapter_number') or '?'})"
                for dc in deceased_characters
            ]
            lines.append(
                "[Deceased roster — these characters CANNOT take present-tense "
                "actions, speak new dialogue, or appear as scene participants. "
                "They MAY be:\n"
                "  * remembered / mourned by another character;\n"
                "  * quoted from earlier dialogue, letters, recordings, or "
                "written works they left behind;\n"
                "  * referenced as a corpse, image, grave, or relic;\n"
                "  * the subject of a clearly-labelled flashback / memorial / "
                "vision / dream scene.\n"
                "If a planted clue (a will, a cipher, a sealed message) was "
                "left by them, surfacing it now is allowed — the character "
                "stays off-stage; the artifact does the work.]:\n"
                + "\n".join(dead_lines)
            )
        else:
            dead_lines = [
                f"- {dc['name']}（死于第{dc.get('death_chapter_number') or '?'}章）"
                for dc in deceased_characters
            ]
            lines.append(
                "【本书已故角色 — 本章绝不可让其登场（不可发出当下动作、"
                "不可说出新台词、不可作为场景活跃参与者）。\n"
                "允许的做法：\n"
                "  · 旁人怀念、悲悼、提起；\n"
                "  · 引用其先前的对话、书信、遗书、录音、留下的文字或卷轴；\n"
                "  · 以遗体／画像／坟前／灵堂／信物等形态出现；\n"
                "  · 在显式标注的回忆／闪回／祭奠／梦境／幻象场景中出现。\n"
                "若该角色生前留下的伏笔（密信、信物、机关）在本章被发现或"
                "触发——可以让信物/线索发挥作用，但角色本人不登场。】：\n"
                + "\n".join(dead_lines)
            )

    # Open interpersonal promises — vows / oaths / debts between
    # characters that are still binding. Travel with the cast for
    # hundreds of chapters; surfacing them keeps the writer from
    # dropping the emotional anchor mid-arc. Overdue rows nudge
    # resolution; fresh ones remind the cast the obligation hangs.
    interpersonal_promises = story_bible_context.get("interpersonal_promises") or []
    if interpersonal_promises:
        try:
            from uuid import UUID as _UUID  # noqa: PLC0415

            from bestseller.services.interpersonal_promises import (  # noqa: PLC0415
                PromiseSnapshot,
                render_promises_block,
            )

            snap_objs: list[PromiseSnapshot] = []
            for p in interpersonal_promises:
                if not isinstance(p, dict) or not p.get("promisor_label"):
                    continue
                try:
                    pid = _UUID(p["id"]) if p.get("id") else _UUID(int=0)
                except (ValueError, TypeError):
                    pid = _UUID(int=0)
                snap_objs.append(PromiseSnapshot(
                    id=pid,
                    promisor_label=p["promisor_label"],
                    promisee_label=p.get("promisee_label", ""),
                    content=p.get("content", ""),
                    kind=p.get("kind"),
                    made_chapter_number=p.get("made_chapter_number"),
                    due_chapter_number=p.get("due_chapter_number"),
                    status=str(p.get("status") or "active"),
                    inherited_by_label=p.get("inherited_by_label"),
                    chapters_until_due=p.get("chapters_until_due"),
                    is_overdue=bool(p.get("is_overdue")),
                ))
            block = render_promises_block(
                snap_objs, language=language or "zh-CN",
            )
            if block:
                lines.append(block)
        except Exception:  # pragma: no cover — defensive
            pass

    # Memory-recall cues — at +3 / +10 / +30 / +80 chapters past a
    # close-relationship death, suggest a brief memory beat for the
    # survivor. Soft constraint: writer is asked to weave at most one
    # or two in if narratively natural; explicit "do not force" framing
    # in the block keeps deterministic acceptance from harming pacing.
    memory_recall_cues = story_bible_context.get("memory_recall_cues") or []
    if memory_recall_cues:
        try:
            from bestseller.services.memory_recall import (  # noqa: PLC0415
                MemoryRecallCue,
                render_memory_recall_block,
            )
            cue_objs = [
                MemoryRecallCue(
                    survivor_name=c["survivor_name"],
                    deceased_name=c["deceased_name"],
                    deceased_role=c.get("deceased_role"),
                    relationship_type=c["relationship_type"],
                    relationship_strength=float(c.get("relationship_strength") or 0.0),
                    chapters_since_death=int(c.get("chapters_since_death") or 0),
                    intensity=str(c.get("intensity") or "settled"),
                )
                for c in memory_recall_cues
                if isinstance(c, dict) and c.get("survivor_name") and c.get("deceased_name")
            ]
            block = render_memory_recall_block(
                cue_objs, language=language or "zh-CN",
            )
            if block:
                lines.append(block)
        except Exception:  # pragma: no cover — defensive
            pass

    # Restricted-but-not-dead roster — characters whose lifecycle state
    # in this chapter forbids present-tense action even though they are
    # not deceased. Each state has its own affordances (sealed forms can
    # be referenced; missing ones can be searched for; sleeping bodies
    # can be tended) so the prompt explains the exact rules.
    restricted_characters = story_bible_context.get("restricted_characters") or []
    if restricted_characters:
        kind_label_zh = {
            "missing": "失踪",
            "sealed": "被封印",
            "sleeping": "沉睡",
            "comatose": "昏迷",
            "exiled": "流放",
        }
        if is_en:
            rest_lines = []
            for rc in restricted_characters:
                exit_clause = (
                    f", expected to resume at ch{rc['scheduled_exit_chapter']}"
                    if rc.get("scheduled_exit_chapter") else ""
                )
                rest_lines.append(
                    f"- {rc['name']} [{rc.get('kind')}{exit_clause}]: "
                    f"{rc.get('appearance_notes_en') or ''}"
                )
            lines.append(
                "[Restricted-but-not-dead roster — these characters CANNOT "
                "speak new dialogue or take present-tense actions in this "
                "chapter. Each kind has its own allowed framing — read each "
                "row carefully]:\n"
                + "\n".join(rest_lines)
            )
        else:
            rest_lines = []
            for rc in restricted_characters:
                kind_zh = kind_label_zh.get(rc.get("kind"), rc.get("kind") or "")
                exit_clause = (
                    f"，预计第{rc['scheduled_exit_chapter']}章解除"
                    if rc.get("scheduled_exit_chapter") else ""
                )
                rest_lines.append(
                    f"- {rc['name']}（{kind_zh}{exit_clause}）："
                    f"{rc.get('appearance_notes_zh') or ''}"
                )
            lines.append(
                "【受限角色（未死但本章不可登场）— 不可发出当下动作或新对白；"
                "每种状态各自的允许形态见下，请逐条遵守】：\n"
                + "\n".join(rest_lines)
            )

    # Protected roster — characters whose planned death is later than this
    # chapter. Surfacing them as a hard "do NOT kill in this chapter"
    # constraint prevents the failure mode where the writer LLM kills off
    # a character that the planner scheduled to die hundreds of chapters
    # later (e.g. the ch6 苏瑶/陆沉 incident — death_chapter_number was
    # 435/458 but the prose still staged a death).
    protected_characters = story_bible_context.get("protected_characters") or []
    if protected_characters:
        if is_en:
            prot_lines = [
                f"- {pc['name']}(planned death ch{pc.get('death_chapter_number') or '?'}, "
                "MUST stay alive in this chapter — no death scene, no fatal injury, "
                "no 'before X died' framing, no 'fell and never rose' implication)"
                for pc in protected_characters
            ]
            lines.append(
                "[PROTECTED ROSTER — these characters MUST survive this chapter; "
                "the planner has scheduled their death for a later chapter, "
                "and any death/dying language here is a hard violation]:\n"
                + "\n".join(prot_lines)
            )
        else:
            prot_lines = [
                f"- {pc['name']}（计划死于第{pc.get('death_chapter_number') or '?'}章，"
                "本章必须存活：不得出现死亡、断气、倒地不起、临终遗言、"
                "「X死前」「魂飞」「殒命」「气绝」等任何死亡相关描写或暗示）"
                for pc in protected_characters
            ]
            lines.append(
                "【保护名单（绝对硬约束）— 这些角色本章必须存活；"
                "他们的死亡时机由长线规划者锁定在更后面的章节，"
                "本章任何死亡/濒死/临终描写都是硬性违规】：\n"
                + "\n".join(prot_lines)
            )

    participants = story_bible_context.get("participants") or []
    if participants:
        def _as_mapping(value: Any) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        def _list_items(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value] if value.strip() else []
            if isinstance(value, (list, tuple)):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        def _short_join(value: Any, *, limit: int = 2, item_chars: int = 40) -> str:
            return "/".join(item[:item_chars] for item in _list_items(value)[:limit])

        def _stance_suffix(item: dict[str, Any]) -> str:
            stance = item.get("stance")
            if not stance:
                return ""
            locked = item.get("stance_locked_until_chapter")
            if is_en:
                if locked:
                    return f" Stance:{stance}(locked until ch{locked})"
                return f" Stance:{stance}"
            if locked:
                return f" 立场:{stance}(锁定至第{locked}章)"
            return f" 立场:{stance}"

        def _alive_suffix(item: dict[str, Any]) -> str:
            alive = item.get("alive_status") or "alive"
            if alive == "alive":
                return ""
            if is_en:
                return f" Alive:{alive}"
            return f" 存活:{alive}"

        # Bumped from 4→8 so multi-character scenes don't lose half their cast
        # to truncation; the character "saturation" failure mode came from
        # the writer never seeing 5th–8th participants' state.
        rendered_participants = "；".join(
            (
                f"{item['name']}[{item.get('role') or 'character'}]"
                f" {'Background' if is_en else '背景'}:{(item.get('background') or ('undefined' if is_en else '未定义'))[:40]}"
                f" {'Goal' if is_en else '目标'}:{item.get('goal') or ('undefined' if is_en else '未定义')}"
                f" {'Arc' if is_en else '弧线状态'}:{item.get('arc_state') or ('undefined' if is_en else '未定义')}"
                f" {'Power' if is_en else '力量层级'}:{item.get('power_tier') or ('undefined' if is_en else '未定义')}"
                f" {'Emotion' if is_en else '情绪'}:{item.get('emotional_state') or ('undefined' if is_en else '未定义')}"
                f"{_stance_suffix(item)}"
                f"{_alive_suffix(item)}"
            )
            for item in participants[:8]
        )
        lines.append(
            f"{'Current participant states' if is_en else '参与角色当前状态'}：{rendered_participants}"
        )

        # Delta-since-last-appearance — for every participant whose
        # tracked axes (arc_state / power_tier / emotional / alive /
        # stance) changed compared to the previous-non-null snapshot,
        # surface "A → B since chapter K". Without this, the writer
        # only sees the current state and has nothing to *dramatise*;
        # this is the "成长可见化" piece — when X grew, the prose
        # should reflect that something shifted in them.
        delta_lines: list[str] = []
        for item in participants[:8]:
            change_chapter = item.get("previous_state_chapter_number")
            axes_changes: list[str] = []
            axis_pairs = (
                ("arc_state", "previous_arc_state",
                 "Arc" if is_en else "弧线"),
                ("power_tier", "previous_power_tier",
                 "Power" if is_en else "力量"),
                ("emotional_state", "previous_emotional_state",
                 "Emotion" if is_en else "情绪"),
                ("alive_status", "previous_alive_status",
                 "Alive" if is_en else "存活"),
                ("stance", "previous_stance",
                 "Stance" if is_en else "立场"),
            )
            for current_key, previous_key, label in axis_pairs:
                cur = item.get(current_key)
                prev = item.get(previous_key)
                if not prev or not cur:
                    continue
                if str(cur).strip() == str(prev).strip():
                    continue
                axes_changes.append(f"{label}: {prev} → {cur}")
            if not axes_changes:
                continue
            anchor = (
                f" since ch{change_chapter}"
                if (is_en and change_chapter is not None)
                else (
                    f"（自第{change_chapter}章起）"
                    if change_chapter is not None
                    else ""
                )
            )
            delta_lines.append(
                f"  · {item['name']}{anchor}: "
                f"{(' | ' if is_en else '；').join(axes_changes)}"
            )
        if delta_lines:
            lines.append(
                (
                    "Participant change since last appearance "
                    "(dramatise the shift — let the change show in "
                    "action / dialogue / body, not narrated as a label):\n"
                    if is_en
                    else "参与角色自上次出场以来的变化"
                    "（必须通过动作/对白/身体语言体现出来，不可"
                    "直接报标签）：\n"
                )
                + "\n".join(delta_lines)
            )

        # Inner structure (lie/want/need/ghost/flaw) for ALL active
        # participants up to 4. Previously only the POV got an inner
        # structure block via deduplication.build_arc_beat_block, leaving
        # supporting characters as flat function-shapes. Surfacing the
        # shadow desire + fatal flaw + believed-lie of every active
        # participant is what gives the LLM the material to write
        # multi-dimensional characters instead of stage props.
        depth_lines: list[str] = []
        for item in participants[:4]:
            inner = item.get("inner_structure") or {}
            moral = item.get("moral_framework") or {}
            psych = item.get("psych_profile") or {}
            ip_anchor = _as_mapping(item.get("ip_anchor"))
            chunks: list[str] = []
            if isinstance(inner, dict):
                if inner.get("lie_believed"):
                    chunks.append(
                        f"{'lie' if is_en else '相信的谎言'}:{str(inner['lie_believed'])[:60]}"
                    )
                if inner.get("truth_to_learn"):
                    chunks.append(
                        f"{'truth' if is_en else '需要学到的真相'}:{str(inner['truth_to_learn'])[:60]}"
                    )
                if inner.get("want_external"):
                    chunks.append(
                        f"{'want' if is_en else '表层目标'}:{str(inner['want_external'])[:60]}"
                    )
                if inner.get("need_internal"):
                    chunks.append(
                        f"{'need' if is_en else '内在需求'}:{str(inner['need_internal'])[:60]}"
                    )
                if inner.get("ghost"):
                    chunks.append(
                        f"{'ghost' if is_en else '过往伤'}:{str(inner['ghost'])[:60]}"
                    )
                if inner.get("fatal_flaw"):
                    chunks.append(
                        f"{'flaw' if is_en else '致命缺陷'}:{str(inner['fatal_flaw'])[:60]}"
                    )
                if inner.get("fear_core"):
                    chunks.append(
                        f"{'fear' if is_en else '核心恐惧'}:{str(inner['fear_core'])[:60]}"
                    )
            elif item.get("fear") or item.get("flaw"):
                # Fallback when inner_structure was never seeded — pull
                # from the legacy fear/flaw columns so the writer at
                # least sees the surface character.
                if item.get("fear"):
                    chunks.append(
                        f"{'fear' if is_en else '恐惧'}:{str(item['fear'])[:60]}"
                    )
                if item.get("flaw"):
                    chunks.append(
                        f"{'flaw' if is_en else '缺陷'}:{str(item['flaw'])[:60]}"
                    )
            core_wound = item.get("core_wound") or ip_anchor.get("core_wound")
            if core_wound:
                chunks.append(
                    f"{'core wound' if is_en else '核心创伤'}:{str(core_wound)[:60]}"
                )
            quirks = ip_anchor.get("quirks") or item.get("quirks")
            if _list_items(quirks):
                chunks.append(
                    f"{'quirks' if is_en else '记忆特征'}:{_short_join(quirks, limit=3)}"
                )
            sensory = ip_anchor.get("sensory_signatures") or item.get(
                "sensory_signatures"
            )
            if _list_items(sensory):
                chunks.append(
                    f"{'sensory' if is_en else '感官标识'}:{_short_join(sensory, limit=2)}"
                )
            objects = ip_anchor.get("signature_objects") or item.get("signature_objects")
            if _list_items(objects):
                chunks.append(
                    f"{'objects' if is_en else '标志物'}:{_short_join(objects, limit=2)}"
                )
            if isinstance(moral, dict):
                core_values = moral.get("core_values")
                if _list_items(core_values):
                    chunks.append(
                        f"{'values' if is_en else '价值观'}:{_short_join(core_values, limit=2)}"
                    )
                lines_arr = (
                    moral.get("lines_never_crossed")
                    or moral.get("lines_will_not_cross")
                )
                if _list_items(lines_arr):
                    chunks.append(
                        f"{'won_t_cross' if is_en else '绝不跨越的底线'}:"
                        f"{_short_join(lines_arr, limit=2)}"
                    )
                sacrifice = moral.get("willing_to_sacrifice")
                if sacrifice:
                    chunks.append(
                        f"{'sacrifice' if is_en else '愿牺牲'}:{str(sacrifice)[:50]}"
                    )
                if moral.get("moral_compass"):
                    chunks.append(
                        f"{'compass' if is_en else '道德指南针'}:{str(moral['moral_compass'])[:50]}"
                    )
            if isinstance(psych, dict):
                psych_bits: list[str] = []
                if psych.get("personality_label"):
                    psych_bits.append(str(psych["personality_label"])[:40])
                if psych.get("mbti"):
                    psych_bits.append(f"MBTI={str(psych['mbti'])[:12]}")
                if psych.get("enneagram"):
                    psych_bits.append(f"九型={str(psych['enneagram'])[:12]}")
                if psych.get("attachment_style"):
                    psych_bits.append(f"依恋={str(psych['attachment_style'])[:16]}")
                big_five = psych.get("big_five")
                if isinstance(big_five, dict) and big_five:
                    ocean = ",".join(
                        f"{str(k)[:4]}:{str(v)[:4]}"
                        for k, v in list(big_five.items())[:5]
                    )
                    psych_bits.append(f"OCEAN={ocean}")
                if psych_bits:
                    chunks.append(
                        f"{'personality' if is_en else '人格'}:{'/'.join(psych_bits[:5])}"
                    )
            if chunks:
                depth_lines.append(
                    f"  · {item['name']}: {(' | ' if is_en else ' ｜ ').join(chunks)}"
                )
        if depth_lines:
            lines.append(
                (
                    "Character inner structure (must show on the page through "
                    "action / subtext / decision-shaping — never narrate the "
                    "label aloud):\n"
                    if is_en
                    else "角色内在结构（必须通过动作/潜台词/决策反映出来，不得直接叙述标签）：\n"
                )
                + "\n".join(depth_lines)
            )

        character_engine_profiles = [
            item.get("character_engine_profile")
            for item in participants[:6]
            if isinstance(item.get("character_engine_profile"), dict)
        ]
        character_engine_block = render_character_engine_profile_block(
            character_engine_profiles,
            max_profiles=4,
        )
        if character_engine_block:
            lines.append(character_engine_block)

        dialogue_personality_block = render_dialogue_personality_bridge_block(
            participants,
            language=language,
            max_profiles=4,
        )
        if dialogue_personality_block:
            lines.append(dialogue_personality_block)

        voice_lines: list[str] = []
        for item in participants[:8]:
            vp = item.get("voice_profile") or {}
            parts: list[str] = []
            if vp.get("speech_register"):
                parts.append(f"{'Register' if is_en else '语言层次'}:{vp['speech_register']}")
            if vp.get("verbal_tics"):
                parts.append(f"{'Verbal tics' if is_en else '口头禅'}:{'/'.join(vp['verbal_tics'][:3])}")
            if vp.get("sentence_style"):
                parts.append(f"{'Sentence style' if is_en else '句式'}:{vp['sentence_style']}")
            if vp.get("emotional_expression"):
                parts.append(f"{'Emotional expression' if is_en else '情绪表达'}:{vp['emotional_expression']}")
            if vp.get("mannerisms"):
                parts.append(f"{'Mannerisms' if is_en else '习惯动作'}:{'/'.join(vp['mannerisms'][:2])}")
            if parts:
                voice_lines.append(f"{item['name']}{' - ' if is_en else '——'}{(' / ' if is_en else '，').join(parts)}")
        if voice_lines:
            lines.append(
                ("Character voice fingerprints (dialogue must stay distinct):\n" if is_en else "角色语言指纹（对话必须体现区分度）：\n")
                + "\n".join(voice_lines)
            )
    relationships = story_bible_context.get("relationships") or []
    if relationships:
        rendered_relationships = "；".join(
            (
                f"{item.get('relationship_type') or '关系'}:"
                f"{item.get('tension_summary') or item.get('private_reality') or '存在潜在张力'}"
            )
            for item in relationships[:3]
        )
        lines.append(
            f"{'Current relationship tension' if is_en else '当前关系张力'}：{rendered_relationships}"
        )
    return "\n".join(lines)


def _render_retrieval_section(chunks: list[dict[str, Any]] | None) -> str:
    if not chunks:
        return ""
    return "\n".join(
        f"- [{chunk.get('source_type')}] {chunk.get('chunk_text')}"
        for chunk in chunks[:4]
    )


def _render_recent_scene_section(recent_scene_summaries: list[dict[str, Any]] | None) -> str:
    if not recent_scene_summaries:
        return ""
    lines: list[str] = []
    for item in recent_scene_summaries[:8]:
        if not item.get("summary"):
            continue
        line = (
            f"- 第{item.get('chapter_number')}章第{item.get('scene_number')}场"
            f" {item.get('scene_title') or ''}：{item.get('summary')}"
        )
        opening = item.get("opening_lines")
        if opening:
            line += f"\n  [开头原文] {opening}"

        # extended_tail is provided for the immediately preceding same-chapter
        # scene — it contains the last ~1000 chars of actual prose, covering
        # dialog and action that AI-generated summaries routinely omit.
        extended_tail = item.get("extended_tail")
        if extended_tail:
            line += (
                f"\n  [前一场结尾原文 — 以下内容已写入，新场景严禁逐字重复]\n"
                f"  ---\n"
                f"  {extended_tail.replace(chr(10), chr(10) + '  ')}\n"
                f"  ---"
            )
        else:
            # Fallback to shorter closing_lines for cross-chapter preceding scenes
            closing = item.get("closing_lines")
            if closing:
                line += f"\n  [结尾原文（禁止在新场景中重复这段内容）] {closing}"

        lines.append(line)
    return "\n".join(lines)


def _render_timeline_section(timeline_events: list[dict[str, Any]] | None) -> str:
    if not timeline_events:
        return ""
    return "\n".join(
        (
            f"- {item.get('story_time_label') or '未指定时间'} / {item.get('event_name')}："
            f"{'；'.join(item.get('consequences') or []) or item.get('summary') or '推进主线'}"
        )
        for item in timeline_events[:4]
    )


def _render_participant_fact_section(participant_facts: list[dict[str, Any]] | None) -> str:
    if not participant_facts:
        return ""
    return "\n".join(
        (
            f"- {item.get('subject_label')} / {item.get('predicate')}："
            f"{item.get('value')}"
        )
        for item in participant_facts[:6]
    )


def _render_arc_section(
    plot_arcs: list[dict[str, Any]] | None,
    arc_beats: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    sections: list[str] = []
    if plot_arcs:
        sections.append("Active narrative lines:" if is_en else "激活叙事线：")
        sections.extend(
            f"- [{item.get('arc_type')}] {item.get('name')}：{item.get('promise')}"
            for item in plot_arcs[:4]
        )
    if arc_beats:
        sections.append("Current arc beats:" if is_en else "当前承担的叙事节拍：")
        sections.extend(
            (
                f"- {item.get('arc_code')} / {item.get('beat_kind')}：{item.get('summary')}"
                + (
                    f" / {'emotion' if is_en else '情绪'}:{item.get('emotional_shift')}"
                    if item.get("emotional_shift")
                    else ""
                )
            )
            for item in arc_beats[:6]
        )
    return "\n".join(sections)


def _render_clue_section(
    unresolved_clues: list[dict[str, Any]] | None,
    planned_payoffs: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    sections: list[str] = []
    if unresolved_clues:
        sections.append("Open clues:" if is_en else "未回收伏笔：")
        sections.extend(
            f"- {item.get('clue_code')} / {item.get('label')}：{item.get('description')}"
            for item in unresolved_clues[:6]
        )
    if planned_payoffs:
        sections.append("Near-term payoffs:" if is_en else "近期应兑现节点：")
        sections.extend(
            f"- {item.get('payoff_code')} / {item.get('label')}：{item.get('description')}"
            for item in planned_payoffs[:4]
        )
    return "\n".join(sections)


def _render_emotion_track_section(
    emotion_tracks: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    if not emotion_tracks:
        return ""
    is_en = is_english_language(language)
    lines = ["Current relationship/emotion lines:" if is_en else "当前关系/情绪线："]
    lines.extend(
        (
            f"- [{item.get('track_type')}] {item.get('title')}：{item.get('summary')}"
            f" / trust={item.get('trust_level')}"
            f" / attraction={item.get('attraction_level')}"
            f" / conflict={item.get('conflict_level')}"
            f" / stage={item.get('intimacy_stage')}"
        )
        for item in emotion_tracks[:4]
    )
    return "\n".join(lines)


def _render_antagonist_plan_section(
    antagonist_plans: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    if not antagonist_plans:
        return ""
    is_en = is_english_language(language)
    lines = ["Current antagonist pressure:" if is_en else "当前反派推进："]
    lines.extend(
        (
            f"- [{item.get('threat_type')}] {item.get('title')}：{item.get('goal')}"
            f" / {'current move' if is_en else '当前动作'}:{item.get('current_move')}"
            f" / {'next move' if is_en else '下一步'}:{item.get('next_countermove')}"
        )
        for item in antagonist_plans[:4]
    )
    return "\n".join(lines)


def _render_contract_section(
    chapter_contract: dict[str, Any] | None,
    scene_contract: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    sections: list[str] = []
    if chapter_contract:
        sections.append(
            f"{'Chapter contract' if is_en else '章节 contract'}："
            f"{chapter_contract.get('contract_summary') or ('This chapter must carry a clear narrative task.' if is_en else '本章需要承担明确叙事任务')}"
        )
        if chapter_contract.get("core_conflict"):
            sections.append(f"- {'Chapter core conflict' if is_en else '章节核心冲突'}：{chapter_contract['core_conflict']}")
        if chapter_contract.get("closing_hook"):
            sections.append(f"- {'Chapter ending turn' if is_en else '章节收尾转折'}：{chapter_contract['closing_hook']}")
        causal_block = _render_character_causal_contract_block(
            chapter_contract,
            language=language,
        )
        if causal_block:
            sections.append(causal_block)
        overlay_block = render_overlay_prompt_block(
            chapter_overlay=chapter_contract,
            language=language,
        )
        if overlay_block:
            sections.append(overlay_block)
        lineage_block = render_methodology_lineage_prompt_block(
            chapter_contract,
            stage="prose_scene",
            language=language,
        )
        if lineage_block:
            sections.append(lineage_block)
        profile_block = render_configured_methodology_profile_block(
            stage="drafting",
            scope="chapter",
            language=language,
        )
        if profile_block:
            sections.append(profile_block)
    if scene_contract:
        sections.append(
            f"{'Scene contract' if is_en else '场景 contract'}："
            f"{scene_contract.get('contract_summary') or ('This scene must produce a clean forward move.' if is_en else '本场必须完成清晰推进')}"
        )
        if scene_contract.get("core_conflict"):
            sections.append(f"- {'Scene core conflict' if is_en else '场景核心冲突'}：{scene_contract['core_conflict']}")
        if scene_contract.get("tail_hook"):
            sections.append(f"- {'Scene ending turn' if is_en else '场景收尾转折'}：{scene_contract['tail_hook']}")
        if scene_contract.get("thematic_task"):
            sections.append(
                (
                    f"- Thematic task: {scene_contract['thematic_task']} (express it through action and imagery, never direct sermonizing)"
                    if is_en
                    else f"- 主题任务：{scene_contract['thematic_task']}（通过行动和意象表达，不要直白说教）"
                )
            )
        if scene_contract.get("dramatic_irony_intent"):
            sections.append(
                (
                    f"- Dramatic irony: {scene_contract['dramatic_irony_intent']} (the reader knows this before the character)"
                    if is_en
                    else f"- 戏剧反讽：{scene_contract['dramatic_irony_intent']}（读者知道但角色不知道）"
                )
            )
        if scene_contract.get("transition_type"):
            sections.append(f"- {'Transition type' if is_en else '过渡方式'}：{scene_contract['transition_type']}")
        if scene_contract.get("subplot_codes"):
            sections.append(
                f"- {'Subplots advanced' if is_en else '推进副线'}："
                f"{(', ' if is_en else '、').join(scene_contract['subplot_codes'])}"
            )
        overlay_block = render_overlay_prompt_block(
            scene_overlay=scene_contract,
            language=language,
        )
        if overlay_block:
            sections.append(overlay_block)
        profile_block = render_configured_methodology_profile_block(
            stage="drafting",
            scope="scene",
            language=language,
        )
        if profile_block:
            sections.append(profile_block)
    return "\n".join(sections)


def _render_character_causal_contract_block(
    chapter_contract: Mapping[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    if not chapter_contract:
        return ""
    is_en = is_english_language(language)
    causal_contract = _mapping_from_contract(chapter_contract.get("causal_contract"))
    items: list[tuple[str, object]] = [
        (
            "Character delta" if is_en else "人物变化",
            _first_contract_value(
                chapter_contract.get("character_delta"),
                causal_contract.get("character_delta"),
                causal_contract.get("inner_state_delta"),
                causal_contract.get("relationship_delta"),
                causal_contract.get("state_change"),
            ),
        ),
        (
            "Protagonist choice" if is_en else "主角选择",
            _first_contract_value(
                chapter_contract.get("protagonist_choice"),
                causal_contract.get("protagonist_choice"),
                causal_contract.get("choice_or_action"),
                causal_contract.get("visible_action_or_reaction"),
            ),
        ),
        ("Pressure" if is_en else "压力", causal_contract.get("pressure")),
        (
            "Visible action/reaction" if is_en else "可见行动/反应",
            causal_contract.get("visible_action_or_reaction"),
        ),
        ("Resistance" if is_en else "阻力", causal_contract.get("resistance")),
        ("Cost/tradeoff" if is_en else "代价/取舍", causal_contract.get("cost_or_tradeoff")),
        ("Gain/reveal" if is_en else "获得/揭示", causal_contract.get("gain_or_reveal")),
        (
            "Next reader desire" if is_en else "下一章读者欲望",
            causal_contract.get("next_reader_desire"),
        ),
    ]
    visible = [
        f"- {label}: {str(value).strip()}"
        if is_en
        else f"- {label}：{str(value).strip()}"
        for label, value in items
        if str(value or "").strip()
    ]
    if not visible:
        return ""
    title = "Character/causal contract" if is_en else "人物变化与因果合同"
    instruction = (
        "Render these as reader-visible choices, costs, reactions, and state changes; "
        "do not name the contract."
        if is_en
        else "这些必须落成读者可见的选择、代价、反应和状态变化；正文不要出现合同/方法论术语。"
    )
    return f"{title}:\n" + "\n".join(visible) + f"\n{instruction}"


def _mapping_from_contract(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_contract_value(*values: object) -> object | None:
    for value in values:
        if str(value or "").strip():
            return value
    return None


def _writer_prompt_mode_for_chapter(settings: AppSettings, chapter_number: int) -> str:
    generation = getattr(settings, "generation", None)
    mode = str(getattr(generation, "writer_prompt_mode", "") or "").strip().lower()
    if mode not in {"full", "lean", "ab"}:
        mode = "lean" if bool(getattr(generation, "lean_writer_prompt", True)) else "full"
    if mode == "ab":
        until = int(getattr(generation, "writer_prompt_ab_until_chapter", 3) or 0)
        if chapter_number > max(until, 0):
            winner = str(getattr(generation, "writer_prompt_ab_winner", "") or "").strip().lower()
            return winner if winner in {"full", "lean"} else "full"
    return mode


def _score_writer_candidate(content: str, *, target_word_count: int | None, language: str) -> float:
    text = content or ""
    score = 0.0
    if text.strip():
        score += 10.0
    if language.lower().startswith("zh") and re.search(
        r"(?<![A-Za-z0-9_])ta(?![A-Za-z0-9_])",
        text,
        flags=re.IGNORECASE,
    ):
        score -= 100.0
    word_count = authoritative_word_count_for_language(text, language=language)
    target = max(int(target_word_count or 0), 1)
    if word_count:
        score += min(word_count / target, 1.15) * 10.0
        if word_count < target * 0.45:
            score -= 8.0
    if has_meta_leak(text):
        score -= 15.0
    return score


def _render_story_principle_execution_section(
    chapter: Any,
    scene: Any,
    *,
    language: str | None = None,
) -> str:
    """Render chapter event-unit guidance at the scene execution layer."""

    chapter_meta = getattr(chapter, "metadata_json", None)
    chapter_meta = chapter_meta if isinstance(chapter_meta, dict) else {}
    event_contract = chapter_meta.get("event_cycle_contract")
    event_contract = event_contract if isinstance(event_contract, dict) else {}
    role = str(
        chapter_meta.get("chapter_event_role")
        or event_contract.get("chapter_event_role")
        or event_contract.get("event_role")
        or ""
    ).strip()
    info_gap = str(
        chapter_meta.get("information_gap_mode")
        or event_contract.get("information_gap_mode")
        or ""
    ).strip()
    if not role and not event_contract and not info_gap:
        return ""

    is_en = is_english_language(language)
    scene_number = int(getattr(scene, "scene_number", 1) or 1)
    scene_type = str(getattr(scene, "scene_type", "") or "").strip().lower()
    scene_focus = _story_principle_scene_focus(
        role=role,
        scene_number=scene_number,
        scene_type=scene_type,
        is_en=is_en,
    )
    keys = (
        "reader_desire",
        "event_pressure",
        "emotion_event",
        "desire_goal",
        "obstacle",
        "solution_method",
        "action_resolution",
        "resolution_feedback",
        "expected_state_delta",
        "handoff_to_next",
        "next_reader_waiting",
    )
    detail_lines = [
        f"- {key}: {value}"
        for key in keys
        if (value := str(event_contract.get(key) or "").strip())
    ]
    if is_en:
        header = "=== Story-principle execution: event-unit contract ==="
        lines = [
            header,
            "- Scope: this is a multi-chapter event-unit contract, not a per-scene or per-chapter six-step template.",
            f"- Current chapter role: {role or 'unspecified'}",
        ]
        if info_gap:
            lines.append(f"- Information gap mode: {info_gap}")
        lines.append(f"- This scene's contribution: {scene_focus}")
        lines.extend(detail_lines)
        lines.append(
            "- Execution rule: embody these constraints through action, choice, pressure, consequence, and handoff; never output the labels as prose."
        )
        return "\n".join(lines)

    lines = [
        "=== 写作原理执行约束：事件单元合同 ===",
        "- 作用域：这是跨章节事件单元合同，不是每场/每章都复刻完整六步的模板。",
        f"- 本章事件角色：{role or '未指定'}",
    ]
    if info_gap:
        lines.append(f"- 信息差模式：{info_gap}")
    lines.append(f"- 本场景贡献：{scene_focus}")
    lines.extend(detail_lines)
    lines.append(
        "- 执行规则：把这些约束落到行动、选择、压力、后果和交接里；不要把字段名或策划标签写进正文。"
    )
    return "\n".join(lines)


def _story_principle_scene_focus(
    *,
    role: str,
    scene_number: int,
    scene_type: str,
    is_en: bool,
) -> str:
    closing_like = scene_type in {"hook", "tail", "closing_hook", "aftereffect"}
    position = "opening" if scene_number <= 1 else ("handoff" if closing_like else "development")
    focus_en = {
        "trigger": {
            "opening": "make the triggering emotional event concrete and immediate.",
            "development": "show pressure spreading from the trigger into a concrete complication.",
            "handoff": "turn the trigger into the next reader question.",
        },
        "desire_lock": {
            "opening": "state the protagonist's wanted result through behavior, not explanation.",
            "development": "force a visible choice that narrows the protagonist's options.",
            "handoff": "leave the reader wanting to see whether the committed goal can survive pressure.",
        },
        "obstacle_escalation": {
            "opening": "put the obstacle on page quickly.",
            "development": "raise resistance, cost, or dilemma without solving it too easily.",
            "handoff": "make the worsened obstacle become the next-scene pull.",
        },
        "method_search": {
            "opening": "show why the old method cannot work.",
            "development": "test or discover a concrete method with visible risk.",
            "handoff": "carry the chosen method into action pressure.",
        },
        "execution_turn": {
            "opening": "start from a concrete action already underway.",
            "development": "execute the turn and make it change the local balance.",
            "handoff": "expose the consequence of the turn.",
        },
        "payoff_feedback": {
            "opening": "land the promised payoff or failure signal early.",
            "development": "show feedback and aftereffect, including cost.",
            "handoff": "convert feedback into the next desire or unresolved question.",
        },
        "reaction_reset": {
            "opening": "let consequences register in behavior and relationship posture.",
            "development": "process the changed state and reset priorities.",
            "handoff": "open a fresh, specific waiting point.",
        },
        "bridge_hook": {
            "opening": "connect prior aftereffect to the new event unit.",
            "development": "transfer pressure without replaying the previous beat.",
            "handoff": "plant a concrete next event, not a generic cliffhanger.",
        },
    }
    focus_zh = {
        "trigger": {
            "opening": "把触发性的情绪事件迅速具象化。",
            "development": "展示触发事件如何扩散成具体麻烦。",
            "handoff": "把触发事件转化成下一步阅读问题。",
        },
        "desire_lock": {
            "opening": "用行为亮出主角想要的结果，不要解释。",
            "development": "逼出一个会收窄选项的可见选择。",
            "handoff": "让读者想看这个已建立目标能否扛住压力。",
        },
        "obstacle_escalation": {
            "opening": "尽快让阻碍上页。",
            "development": "升级阻力、代价或两难，不要轻易解决。",
            "handoff": "把变严重的阻碍变成下一场拉力。",
        },
        "method_search": {
            "opening": "先证明旧方法为什么失效。",
            "development": "发现或测试一个有风险的具体方法。",
            "handoff": "把选定方法推向行动压力。",
        },
        "execution_turn": {
            "opening": "从已经发生的具体行动切入。",
            "development": "执行转折，并让局部局势发生变化。",
            "handoff": "暴露转折带来的后果。",
        },
        "payoff_feedback": {
            "opening": "尽早落下前文期待的兑现或失败信号。",
            "development": "展示反馈和余波，包括代价。",
            "handoff": "把反馈转换成下一步欲望或未解问题。",
        },
        "reaction_reset": {
            "opening": "让后果体现在行为和关系姿态里。",
            "development": "消化变化后的状态，并重置优先级。",
            "handoff": "打开一个新的、具体的等待点。",
        },
        "bridge_hook": {
            "opening": "把上一轮余波接到新事件单元。",
            "development": "转移压力，但不要重演上一拍。",
            "handoff": "种下具体下一事件，不写泛化悬念。",
        },
    }
    table = focus_en if is_en else focus_zh
    default = (
        "serve the chapter's event-unit role with a visible state change."
        if is_en
        else "服务本章事件角色，并造成可见状态变化。"
    )
    return table.get(role, {}).get(position, default)


def _render_tree_section(
    tree_context_nodes: list[dict[str, Any]] | None,
    *,
    language: str | None = None,
) -> str:
    if not tree_context_nodes:
        return ""
    is_en = is_english_language(language)
    return "\n".join(
        (
            f"- {item.get('node_path')} [{item.get('node_type')}]："
            f"{item.get('summary') or item.get('title') or ('No summary' if is_en else '无摘要')}"
        )
        for item in tree_context_nodes[:8]
    )


def _render_hard_fact_snapshot_section(
    snapshot: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    """Render the chapter-end hard-fact snapshot block.

    ``snapshot`` is the JSON-serialized form of
    :class:`bestseller.domain.context.ChapterStateSnapshotContext`.  Returns an
    empty string when there is nothing to inject so the caller can safely
    concatenate.
    """
    if not snapshot:
        return ""
    facts = snapshot.get("facts") or []
    if not facts:
        return ""
    is_en = is_english_language(language)
    chapter_number = snapshot.get("chapter_number")
    header = (
        f"=== Locked fact state (from the end of Chapter {chapter_number}; must be obeyed exactly with no contradictions) ==="
        if chapter_number is not None and is_en
        else (
            f"=== 当前事实状态（来自第 {chapter_number} 章末 — 必须严格遵守，不得前后矛盾）==="
            if chapter_number is not None
            else (
                "=== Locked fact state (from the previous chapter end; must be obeyed exactly with no contradictions) ==="
                if is_en
                else "=== 当前事实状态（来自上一章末 — 必须严格遵守，不得前后矛盾）==="
            )
        )
    )
    lines: list[str] = [header]
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        name = fact.get("name")
        value = fact.get("value")
        if not name or value is None:
            continue
        subject = fact.get("subject")
        unit = fact.get("unit")
        notes = fact.get("notes")
        prefix = f"[{subject}] " if subject else ""
        unit_suffix = f" {unit}" if unit else ""
        notes_suffix = f"  // {notes}" if notes else ""
        lines.append(f"- {prefix}{name}: {value}{unit_suffix}{notes_suffix}")
    lines.append(
        (
            "=== Any change to quantities, locations, or possessions must have a reader-visible trigger event in this chapter (trade, combat, elapsed time, etc.) ==="
            if is_en
            else "=== 任何数值/位置/物品变化都必须在本章正文里给出读者可见的触发事件（交易、战斗、时间流逝等）==="
        )
    )
    return "\n".join(lines)


def _resolve_project_writing_profile(project: Any, style_guide: StyleGuideModel | None) -> Any:
    metadata = getattr(project, "metadata_json", {}) or {}
    raw_profile = metadata.get("writing_profile") if isinstance(metadata, dict) else None
    fallback_style = (
        {
            "style": {
                "pov_type": getattr(style_guide, "pov_type", "third-limited"),
                "tense": getattr(style_guide, "tense", "present"),
                "tone_keywords": list(getattr(style_guide, "tone_keywords", []) or []),
                "prose_style": getattr(style_guide, "prose_style", "commercial-web-serial"),
                "sentence_style": getattr(style_guide, "sentence_style", "mixed"),
                "info_density": getattr(style_guide, "info_density", "medium"),
                "dialogue_ratio": float(getattr(style_guide, "dialogue_ratio", 0.4)),
                "taboo_topics": list(getattr(style_guide, "taboo_topics", []) or []),
                "taboo_words": list(getattr(style_guide, "taboo_words", []) or []),
                "reference_works": list(getattr(style_guide, "reference_works", []) or []),
                "custom_rules": list(getattr(style_guide, "custom_rules", []) or []),
            }
        }
        if style_guide is not None
        else None
    )
    return resolve_writing_profile(
        raw_profile or fallback_style,
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
        audience=getattr(project, "audience", None),
        language=getattr(project, "language", None),
    )


def _resolve_project_prompt_pack(project: Any, writing_profile: Any):
    if is_fanqie_short_project(project):
        return resolve_prompt_pack(
            "fanqie_short",
            genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
            sub_genre=getattr(project, "sub_genre", None),
        )
    return resolve_prompt_pack(
        getattr(writing_profile.market, "prompt_pack_key", None),
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
    )


def _project_language(project: Any) -> str:
    return normalize_language(getattr(project, "language", None))


def _scene_participant_text(participants: list[str] | None, *, language: str) -> str:
    if not participants:
        return "relevant characters" if is_english_language(language) else "相关角色"
    return ", ".join(participants) if is_english_language(language) else "、".join(participants)


def render_scene_draft_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    style_guide: StyleGuideModel | None,
    story_bible_context: dict[str, Any] | None = None,
    retrieval_context: list[dict[str, Any]] | None = None,
    recent_scene_summaries: list[dict[str, Any]] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    participant_canon_facts: list[dict[str, Any]] | None = None,
    active_plot_arcs: list[dict[str, Any]] | None = None,
    active_arc_beats: list[dict[str, Any]] | None = None,
    unresolved_clues: list[dict[str, Any]] | None = None,
    planned_payoffs: list[dict[str, Any]] | None = None,
    chapter_contract: dict[str, Any] | None = None,
    scene_contract: dict[str, Any] | None = None,
    tree_context_nodes: list[dict[str, Any]] | None = None,
    active_emotion_tracks: list[dict[str, Any]] | None = None,
    active_antagonist_plans: list[dict[str, Any]] | None = None,
) -> str:
    """Return a minimal fallback markdown for a scene whose LLM draft failed.

    IMPORTANT: this function must NOT return narrative prose. Its output is
    used as ``fallback_response`` for the scene-writer LLM call, which means it
    can end up being stored verbatim as the scene's final ``content_md`` when
    the LLM is unreachable or returns empty text.

    Historically this function returned a six-paragraph template that looked
    like prose ("XX 被推入《项目名》第 N 章的核心冲突。叙事采用 third-limited
    视角…"). Those sentences repeatedly leaked into the final novel output as
    meta-commentary, because the sanitizer only matched structural markers and
    could not tell them apart from real scene text.

    The fix is to return an obviously non-prose HTML comment placeholder. When
    a scene relies on this fallback, the placeholder is easy to spot during
    review, the sanitizer drops it from the rendered chapter, and it cannot
    masquerade as narrative prose.
    """
    # The unused arguments below are intentional: callers still pass context
    # for parity with ``build_scene_draft_prompts`` and to keep the signature
    # stable. Reference them once so linters do not flag unused parameters.
    _ = (
        style_guide,
        story_bible_context,
        retrieval_context,
        recent_scene_summaries,
        recent_timeline_events,
        participant_canon_facts,
        active_plot_arcs,
        active_arc_beats,
        unresolved_clues,
        planned_payoffs,
        chapter_contract,
        scene_contract,
        tree_context_nodes,
        active_emotion_tracks,
        active_antagonist_plans,
    )
    participants = _scene_participant_text(scene.participants, language=_project_language(project))
    return (
        f"<!-- scene-draft-fallback project=\"{project.slug}\" "
        f"chapter={chapter.chapter_number} scene={scene.scene_number} "
        f"participants=\"{participants}\" -->"
    )


_SCENE_TYPE_GUIDANCE: dict[str, str] = {
    "hook": (
        "这是一个强开场场景。用强烈的感官画面或悬念动作立刻抓住读者注意力："
        "角色必须在第一段就处于行动或困境中，严禁平铺直叙的背景介绍。"
        "抛出一个读者必须知道答案的问题或一个打破日常的意外事件。"
        "结尾要让读者非翻下一页不可。"
    ),
    "setup": (
        "这是一个铺垫/建设场景。为即将到来的冲突种下种子："
        "通过角色日常行动中的细节暗示即将到来的变化。建立角色关系的基线和世界规则。"
        "每一段看似平常的描写都要包含后续会回收的伏线。节奏可以稍慢，但严禁无意义的闲聊。"
    ),
    "transition": (
        "这是一个过渡/桥接场景。承接上一个情节高点并导向下一个冲突："
        "角色在消化刚发生的事件同时向新目标移动。用旅途、环境变化或新角色登场推动过渡。"
        "必须包含至少一个微型紧张点（一个隐患、一条坏消息、一次误判），避免节奏完全平坦。"
    ),
    "conflict": (
        "这是一个核心冲突场景。对抗必须直接、具体、有后果："
        "明确展示双方的筹码和代价。冲突中角色要做出艰难选择，不允许轻松化解。"
        "对话要带刺，动作要有后果，信息差要起作用。冲突结果必须改变力量格局。"
    ),
    "reveal": (
        "这是一个揭示/反转场景。核心信息的曝光必须带来范式转换："
        "精确控制信息释放的时机——先铺足读者和角色的错误预期，再用一个关键细节翻盘。"
        "重点写角色发现真相后的情绪冲击和行为变化，而不仅仅是信息本身。"
        "揭示必须改变角色之后的所有行动逻辑。"
    ),
    "introspection": (
        "这是一个沉思/内省场景。不需要强制外部冲突，重点放在角色内心世界："
        "让角色回顾过去、质疑自我、整理情绪。用内心独白、环境映射和感官细节构建氛围。"
        "结尾留下角色心态转变或新决定的暗示。"
    ),
    "relationship_building": (
        "这是一个关系深化场景。重点放在两个或多个角色之间的互动质量："
        "通过共同经历、坦诚对话或无声默契加深关系。展示角色间的化学反应和信任变化。"
        "不需要高强度冲突，但需要情感层次推进。"
    ),
    "worldbuilding_discovery": (
        "这是一个世界观发现场景。通过角色的亲身体验让读者感受世界："
        "用五感细节、角色反应和具体互动展示世界规则。严禁长段解释，一切设定信息必须藏在行动里。"
    ),
    "aftermath": (
        "这是一个余波/善后场景。上一个高潮刚刚结束，角色需要消化后果："
        "处理伤亡、评估损失、重新规划。情绪从高强度向内收，展示事件对角色的真实影响。"
        "节奏放慢，但要留下下一步行动的种子。"
    ),
    "preparation": (
        "这是一个蓄势场景。角色在为接下来的大事件做准备："
        "收集资源、制定计划、联络盟友。通过准备过程侧面展示挑战的严峻。"
        "营造紧迫感和期待感，但不要提前揭示结果。"
    ),
    "comic_relief": (
        "这是一个调剂场景。在持续紧张的剧情后给读者喘息空间："
        "用轻松幽默的日常互动展示角色的另一面。可以有轻微的搞笑冲突或温馨时刻。"
        "但调剂中也要自然植入一两个对后续情节有用的信息或线索。"
    ),
    "montage": (
        "这是一个时间流逝/蒙太奇场景。通过场景片段展示一段时间内的变化："
        "用精炼的场景碎片串联成长、训练、旅途或时间推进。每个碎片要有鲜明的感官标记。"
    ),
}

_SCENE_TYPE_GUIDANCE_EN: dict[str, str] = {
    "hook": (
        "This is a hook scene. Grab the reader's attention immediately with vivid sensory imagery or a disruption. "
        "The character must be in action or crisis by the first paragraph — no flat background exposition. "
        "Pose a question the reader cannot ignore or an event that breaks the status quo. "
        "End with a line that makes turning the page irresistible."
    ),
    "setup": (
        "This is a setup scene. Plant seeds for the coming conflict through everyday actions that carry hidden significance. "
        "Establish baseline relationships, character wants, and world rules. "
        "Every seemingly ordinary detail should contain a thread that pays off later. "
        "Pace can be moderate, but every exchange must advance characterization or stakes — no empty chatter."
    ),
    "transition": (
        "This is a transition scene. Bridge the aftermath of the last event to the next conflict zone. "
        "Show the character processing what happened while moving toward a new objective. "
        "Use travel, environment shifts, or a new character's arrival to carry the transition. "
        "Include at least one micro-tension beat (a warning, bad news, or misjudgment) so the pace never goes flat."
    ),
    "conflict": (
        "This is a core conflict scene. The confrontation must be direct, specific, and consequential. "
        "Show what each side stands to gain or lose. Force the character into a hard choice with no easy exit. "
        "Dialogue should carry subtext and edge; actions should have visible costs; information asymmetry should drive the stakes. "
        "The outcome must shift the power balance."
    ),
    "reveal": (
        "This is a reveal scene. The core information drop must create a paradigm shift. "
        "First solidify the character's (and reader's) wrong assumptions, then shatter them with one precise detail. "
        "Focus on the emotional shockwave and behavioral change the truth triggers, not just the information itself. "
        "The reveal must alter the character's decision logic for everything that follows."
    ),
    "introspection": (
        "This is an introspection scene. External conflict is optional; prioritize the character's inner reckoning, self-doubt, emotional sorting, and the decision forming underneath the silence."
    ),
    "relationship_building": (
        "This is a relationship-building scene. Prioritize interaction quality, shifting trust, and emotional subtext between the characters. It does not need explosive conflict, but it does need clear emotional progression."
    ),
    "worldbuilding_discovery": (
        "This is a world-discovery scene. Let the reader feel the setting through direct experience, sensory detail, and consequence. Avoid exposition blocks; hide the world rules inside action and reaction."
    ),
    "aftermath": (
        "This is an aftermath scene. The previous spike has just landed, so focus on consequence, damage assessment, emotional settling, and the seed of the next move."
    ),
    "preparation": (
        "This is a preparation scene. Show resource gathering, plan-making, or alliance-building in a way that makes the coming event feel larger and more dangerous without revealing the outcome early."
    ),
    "comic_relief": (
        "This is a relief scene. Let the pressure ease just enough for humor, warmth, or awkward humanity, but still plant at least one useful clue or piece of future leverage."
    ),
    "montage": (
        "This is a montage / time-passage scene. Use compressed scene fragments to show growth, travel, training, or time progression; each fragment should carry a sharp sensory anchor."
    ),
}


def _scene_type_writing_guidance(scene_type: str, *, language: str | None = None) -> str:
    is_en = is_english_language(language)
    guidance_map = _SCENE_TYPE_GUIDANCE_EN if is_en else _SCENE_TYPE_GUIDANCE
    return guidance_map.get(
        scene_type,
        (
            "Write a full scene with conflict movement, character action, effective dialogue, information change, and a closing turn."
            if is_en
            else "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾牵引。"
        ),
    )


def _render_knowledge_state_section(
    knowledge_states: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render character cognitive states into a prompt section."""
    if not knowledge_states:
        return ""
    lines: list[str] = []
    header = (
        "=== Character cognitive states (writing MUST obey) ==="
        if is_en
        else "=== 角色认知状态（写作必须遵守）==="
    )
    footer = (
        "=== Characters must NOT act on knowledge they don't have ==="
        if is_en
        else "=== 角色的对话和行为不得超越其认知边界 ==="
    )
    lines.append(header)
    for ks in knowledge_states:
        name = ks.get("character_name", "?")
        lines.append(f"{name}:")
        knows = ks.get("knows", [])
        if knows:
            lines.append(
                f"  {'Knows' if is_en else '已知'}："
                f"{'; '.join(str(k) for k in knows[:6])}"
            )
        fb = ks.get("falsely_believes", [])
        if fb:
            lines.append(
                f"  {'Falsely believes' if is_en else '错误相信'}："
                f"{'; '.join(str(b) for b in fb[:4])}"
            )
        unaware = ks.get("unaware_of", [])
        if unaware:
            lines.append(
                f"  {'Unaware of' if is_en else '尚不知道'}："
                f"{'; '.join(str(u) for u in unaware[:4])}"
            )
        # Phase-4: lie/truth arc guidance
        lt_arc = ks.get("lie_truth_arc")
        if isinstance(lt_arc, dict) and lt_arc.get("core_lie"):
            phase = lt_arc.get("current_phase", "believing_lie")
            if is_en:
                lines.append(f"  Core lie: {lt_arc['core_lie']}")
                lines.append(f"  Core truth: {lt_arc.get('core_truth', '?')}")
                lines.append(f"  Arc type: {lt_arc.get('arc_type', 'positive')} | Phase: {phase}")
                _phase_hints = {
                    "believing_lie": "Character fully believes the lie — actions driven by it.",
                    "questioning_lie": "Cracks appear — character encounters contradictions.",
                    "confronting_lie": "Crisis forces character to choose lie or truth.",
                    "embracing_truth": "Character acts from truth, paying transformation cost.",
                }
            else:
                lines.append(f"  核心谎言：{lt_arc['core_lie']}")
                lines.append(f"  核心真相：{lt_arc.get('core_truth', '?')}")
                lines.append(f"  弧线类型：{lt_arc.get('arc_type', 'positive')} | 阶段：{phase}")
                _phase_hints = {
                    "believing_lie": "角色完全相信谎言——行为受其驱动。",
                    "questioning_lie": "裂痕出现——角色遭遇矛盾。",
                    "confronting_lie": "危机迫使角色在谎言与真相间抉择。",
                    "embracing_truth": "角色基于真相行动，付出蜕变代价。",
                }
            hint = _phase_hints.get(phase, "")
            if hint:
                lines.append(f"  → {hint}")
    lines.append(footer)
    return "\n".join(lines)


# ── Phase-3 wiring: Swain scene/sequel pattern ──


def _render_scene_sequel_section(
    swain_pattern: str | None,
    scene_skeleton: dict[str, str] | None,
    *,
    is_en: bool = False,
) -> str:
    if not swain_pattern:
        return ""
    if is_en:
        header = "=== SCENE PATTERN ==="
        if swain_pattern == "action":
            lines = [
                f"\n{header}",
                "This is an ACTION scene (Goal → Conflict → Disaster).",
            ]
            if scene_skeleton:
                lines.append(f"- Goal: {scene_skeleton.get('goal', '')}")
                lines.append(f"- Conflict: {scene_skeleton.get('conflict', '')}")
                lines.append(f"- Disaster: {scene_skeleton.get('disaster', '')}")
        else:
            lines = [
                f"\n{header}",
                "This is a SEQUEL scene (Reaction → Dilemma → Decision).",
            ]
            if scene_skeleton:
                lines.append(f"- Reaction: {scene_skeleton.get('reaction', '')}")
                lines.append(f"- Dilemma: {scene_skeleton.get('dilemma', '')}")
                lines.append(f"- Decision: {scene_skeleton.get('decision', '')}")
        return "\n".join(lines) + "\n"
    # Chinese
    if swain_pattern == "action":
        lines = [
            "\n=== 场景节奏 ===",
            "本场是【行动场景】（目标 → 冲突 → 灾难）。",
        ]
        if scene_skeleton:
            lines.append(f"- 目标：{scene_skeleton.get('goal', '')}")
            lines.append(f"- 冲突：{scene_skeleton.get('conflict', '')}")
            lines.append(f"- 灾难：{scene_skeleton.get('disaster', '')}")
    else:
        lines = [
            "\n=== 场景节奏 ===",
            "本场是【反应场景】（反应 → 两难 → 决定）。",
        ]
        if scene_skeleton:
            lines.append(f"- 反应：{scene_skeleton.get('reaction', '')}")
            lines.append(f"- 两难：{scene_skeleton.get('dilemma', '')}")
            lines.append(f"- 决定：{scene_skeleton.get('decision', '')}")
    return "\n".join(lines) + "\n"


# ── Phase-2 wiring: structure template beat ──


def _render_structure_beat_section(
    structure_beat_name: str | None,
    structure_beat_description: str | None,
    *,
    is_en: bool = False,
) -> str:
    if not structure_beat_name:
        return ""
    desc = structure_beat_description or ""
    if is_en:
        return (
            f"\n=== STRUCTURE BEAT ===\n"
            f"This chapter lands on the \"{structure_beat_name}\" beat.\n"
            f"{desc}\n"
            f"Write to serve this structural role.\n"
        )
    return (
        f"\n=== 结构节拍 ===\n"
        f"本章对应结构节拍：「{structure_beat_name}」。\n"
        f"{desc}\n"
        f"请在写作中服务于这一结构定位。\n"
    )


# ── Phase-5 wiring: genre obligatory scenes ──


def _render_genre_obligations_section(
    obligations: list[dict[str, str]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render genre-required scenes due near the current chapter."""
    if not obligations:
        return ""
    if is_en:
        lines = ["\n=== GENRE OBLIGATIONS (due this chapter) ==="]
        for ob in obligations:
            lines.append(f"- [{ob.get('code', '')}] {ob.get('label', '')} (timing: {ob.get('timing', 'any')})")
        lines.append("Ensure at least one of these obligations is addressed in this scene if appropriate.")
    else:
        lines = ["\n=== 题材必须场景（本章附近应出现）==="]
        for ob in obligations:
            lines.append(f"- [{ob.get('code', '')}] {ob.get('label', '')}（时机：{ob.get('timing', 'any')}）")
        lines.append("请在合适时机安排上述必须场景元素。")
    return "\n".join(lines) + "\n"


# ── Phase-6 wiring: foreshadowing gap warning ──


def _render_foreshadowing_gap_section(
    warning: str | None,
    *,
    is_en: bool = False,
) -> str:
    """Render a foreshadowing gap warning if present."""
    if not warning:
        return ""
    if is_en:
        return (
            f"\n=== FORESHADOWING GAP WARNING ===\n"
            f"{warning}\n"
        )
    return (
        f"\n=== 伏笔空白警告 ===\n"
        f"{warning}\n"
    )


# ── Phase-1 wiring: render five previously orphaned narrative context sections ──


def _render_pacing_target_section(
    pacing_target: dict[str, Any] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render the chapter tension target into a prompt section."""
    if not pacing_target:
        return ""
    tension = pacing_target.get("tension_level", 0)
    scene_type_plan = pacing_target.get("scene_type_plan", "")
    notes = pacing_target.get("notes", "")
    if is_en:
        header = "=== Chapter Tension Target ==="
        body = f"Target tension level: {tension:.2f}/1.00"
        if scene_type_plan:
            body += f". Planned scene type: {scene_type_plan}"
        if notes:
            body += f". Notes: {notes}"
        return f"{header}\n{body}"
    header = "=== 本章张力目标 ==="
    body = f"目标张力水平: {tension:.2f}/1.00"
    if scene_type_plan:
        body += f"。计划场景类型: {scene_type_plan}"
    if notes:
        body += f"。备注: {notes}"
    return f"{header}\n{body}"


def _render_subplot_prominence_section(
    subplot_schedule: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render subplot prominence schedule for this chapter."""
    if not subplot_schedule:
        return ""
    primary = [e for e in subplot_schedule if e.get("prominence") == "primary"]
    secondary = [e for e in subplot_schedule if e.get("prominence") == "secondary"]
    mention = [e for e in subplot_schedule if e.get("prominence") == "mention"]
    lines: list[str] = []
    if is_en:
        lines.append("=== Subplot Focus This Chapter ===")
        if primary:
            lines.append(f"Primary arcs (must advance): {', '.join(e.get('arc_code', '?') for e in primary)}")
        if secondary:
            lines.append(f"Secondary arcs (touch briefly): {', '.join(e.get('arc_code', '?') for e in secondary)}")
        if mention:
            lines.append(f"Mention only: {', '.join(e.get('arc_code', '?') for e in mention)}")
    else:
        lines.append("=== 本章子情节焦点 ===")
        if primary:
            lines.append(f"主推弧线（必须推进）: {', '.join(e.get('arc_code', '?') for e in primary)}")
        if secondary:
            lines.append(f"辅助弧线（简短触及）: {', '.join(e.get('arc_code', '?') for e in secondary)}")
        if mention:
            lines.append(f"仅提及: {', '.join(e.get('arc_code', '?') for e in mention)}")
    return "\n".join(lines)


def _render_ending_contract_section(
    ending_contract: dict[str, Any] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render the ending contract checklist for final chapters."""
    if not ending_contract:
        return ""
    arcs = ending_contract.get("arcs_to_resolve", [])
    clues = ending_contract.get("clues_to_payoff", [])
    rels = ending_contract.get("relationships_to_close", [])
    thematic = ending_contract.get("thematic_final_expression", "")
    denouement = ending_contract.get("denouement_plan", "")
    lines: list[str] = []
    if is_en:
        lines.append("=== ENDING CONTRACT (MUST RESOLVE) ===")
        if arcs:
            lines.append(f"Arcs to resolve: {', '.join(arcs[:8])}")
        if clues:
            lines.append(f"Clues to pay off: {', '.join(clues[:8])}")
        if rels:
            lines.append(f"Relationships to close: {', '.join(rels[:6])}")
        if thematic:
            lines.append(f"Thematic final expression: {thematic}")
        if denouement:
            lines.append(f"Denouement plan: {denouement}")
    else:
        lines.append("=== 结局合约（必须收束）===")
        if arcs:
            lines.append(f"待收束弧线: {', '.join(arcs[:8])}")
        if clues:
            lines.append(f"待回收伏笔: {', '.join(clues[:8])}")
        if rels:
            lines.append(f"待收束关系: {', '.join(rels[:6])}")
        if thematic:
            lines.append(f"主题最终表达: {thematic}")
        if denouement:
            lines.append(f"余韵计划: {denouement}")
    return "\n".join(lines)


def _render_reader_knowledge_section(
    reader_knowledge_entries: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render dramatic irony cues — what the reader knows but characters may not."""
    if not reader_knowledge_entries:
        return ""
    lines: list[str] = []
    if is_en:
        lines.append("=== Reader Knowledge (Dramatic Irony) ===")
        for entry in reader_knowledge_entries[:8]:
            audience = entry.get("audience", "both")
            marker = " [reader only]" if audience == "reader_only" else ""
            lines.append(f"- Ch{entry.get('chapter_number', '?')}: {entry.get('knowledge_item', '?')}{marker}")
    else:
        lines.append("=== 读者已知信息（戏剧反讽）===")
        for entry in reader_knowledge_entries[:8]:
            audience = entry.get("audience", "both")
            marker = " [仅读者知道]" if audience == "reader_only" else ""
            lines.append(f"- 第{entry.get('chapter_number', '?')}章: {entry.get('knowledge_item', '?')}{marker}")
    return "\n".join(lines)


def _render_relationship_milestone_section(
    milestones: list[dict[str, Any]] | None,
    *,
    is_en: bool = False,
) -> str:
    """Render recent relationship milestone events."""
    if not milestones:
        return ""
    lines: list[str] = []
    if is_en:
        lines.append("=== Recent Relationship Milestones ===")
        for m in milestones[:6]:
            lines.append(
                f"- Ch{m.get('chapter_number', '?')}: "
                f"{m.get('character_a_label', '?')} ↔ {m.get('character_b_label', '?')}: "
                f"{m.get('event_description', '?')} → {m.get('relationship_change', '?')}"
            )
    else:
        lines.append("=== 近期关系里程碑 ===")
        for m in milestones[:6]:
            lines.append(
                f"- 第{m.get('chapter_number', '?')}章: "
                f"{m.get('character_a_label', '?')} ↔ {m.get('character_b_label', '?')}: "
                f"{m.get('event_description', '?')} → {m.get('relationship_change', '?')}"
            )
    return "\n".join(lines)


def build_scene_draft_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    style_guide: StyleGuideModel | None,
    story_bible_context: dict[str, Any] | None = None,
    retrieval_context: list[dict[str, Any]] | None = None,
    recent_scene_summaries: list[dict[str, Any]] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    participant_canon_facts: list[dict[str, Any]] | None = None,
    active_plot_arcs: list[dict[str, Any]] | None = None,
    active_arc_beats: list[dict[str, Any]] | None = None,
    unresolved_clues: list[dict[str, Any]] | None = None,
    planned_payoffs: list[dict[str, Any]] | None = None,
    chapter_contract: dict[str, Any] | None = None,
    scene_contract: dict[str, Any] | None = None,
    tree_context_nodes: list[dict[str, Any]] | None = None,
    active_emotion_tracks: list[dict[str, Any]] | None = None,
    active_antagonist_plans: list[dict[str, Any]] | None = None,
    hard_fact_snapshot: dict[str, Any] | None = None,
    contradiction_warnings: list[str] | None = None,
    query_brief: str | None = None,
    participant_knowledge_states: list[dict[str, Any]] | None = None,
    arc_summaries: list[dict[str, Any]] | None = None,
    world_snapshot: dict[str, Any] | None = None,
    # Phase-1 wiring
    pacing_target: dict[str, Any] | None = None,
    subplot_schedule: list[dict[str, Any]] | None = None,
    ending_contract: dict[str, Any] | None = None,
    reader_knowledge_entries: list[dict[str, Any]] | None = None,
    relationship_milestones: list[dict[str, Any]] | None = None,
    # Phase-2 wiring
    structure_beat_name: str | None = None,
    structure_beat_description: str | None = None,
    # Phase-3 wiring
    swain_pattern: str | None = None,
    scene_skeleton: dict[str, str] | None = None,
    # Phase-5 wiring
    genre_obligations_due: list[dict[str, str]] | None = None,
    # Phase-6 wiring
    foreshadowing_gap_warning: str | None = None,
    # Identity / dedup / genre constraint blocks (Tier 0/1)
    identity_constraint_block: str | None = None,
    overused_phrase_block: str | None = None,
    genre_constraint_block: str | None = None,
    ranking_capability_profile_block: str | None = None,
    progression_context_block: str | None = None,
    decision_policy_block: str | None = None,
    rule_system_context_block: str | None = None,
    faction_ecology_context_block: str | None = None,
    relationship_agency_context_block: str | None = None,
    entry_system_context_block: str | None = None,
    entry_registry_context_block: str | None = None,
    entry_state_ledger_block: str | None = None,
    # Opening diversity block: list of (chapter_number, opening_snippet) for recent chapters
    opening_diversity_block: str | None = None,
    # Stage A — conflict diversity (per-scene, all scenes)
    conflict_diversity_block: str | None = None,
    # Stage B — scene-purpose diversity (all scenes)
    scene_purpose_diversity_block: str | None = None,
    # Stage B — environment 7-d diversity (all scenes)
    env_diversity_block: str | None = None,
    # Stage C — POV character arc + inner structure
    arc_beat_block: str | None = None,
    # Stage C — 5-layer thinking contract (POV decision points)
    five_layer_block: str | None = None,
    # Stage D — 7-type cliffhanger diversity
    cliffhanger_diversity_block: str | None = None,
    # Stage D — chapter tension target (beat-sheet driven)
    tension_target_block: str | None = None,
    # Stage B+ — location ledger (same-location reframe + visit cap)
    location_ledger_block: str | None = None,
    # L3 — DiversityBudget-sourced block (hot vocab, opening/cliffhanger rotation)
    budget_diversity_block: str | None = None,
    # Scene scope isolation block (earlier scenes written — don't rewrite them)
    scene_scope_isolation_block: str | None = None,
    # Plan-richness gate findings (pre-draft)
    plan_richness_block: str | None = None,
    # Reader Hype Engine — per-book commercial contract (cadenced) + per-chapter
    # hype constraints. Both are pre-rendered by
    # ``prompt_constructor.build_chapter_hype_blocks`` so this function only has
    # to concatenate them into the user_prompt. Empty/None → no-op.
    reader_contract_block: str | None = None,
    hype_constraints_block: str | None = None,
    # L3 Prompt Constructor — per-chapter methodology/invariants/diversity/
    # anti-slop sections pre-rendered by
    # ``prompt_constructor.build_chapter_l3_blocks``. When None/empty the
    # prompt falls back to the legacy hype-only path (feature-gated by
    # ``quality_gates.l3_prompt_constructor.enabled``).
    l3_prompt_block: str | None = None,
    # Material library soft-reference block. Pre-rendered by
    # ``material_library_reference.render_library_soft_reference_block``
    # and gated by ``pipeline.enable_library_soft_reference`` (default
    # False).  When None / empty the prompt is byte-identical to the
    # pre-library pipeline — historical novels stay on v1.
    library_reference_block: str | None = None,
    # ── P1 Originality Engine blocks ──
    # Pre-rendered by ``pipelines.py`` from
    # ``chapter_orchestrator.prepare_chapter_context``. Each is the
    # rendered text for the corresponding service:
    #   - voice_dna_block: render_voice_dna_block(target_dna)
    #   - dialogue_voice_block: render_dialogue_voice_block(character voices)
    #   - chapter_market_constraints_block: render_chapter_constraints_block(...)
    #   - signature_scene_block: render_signature_scene_block(mandate)
    #   - prior_persona_feedback_block: render_persona_feedback_block(prev_result)
    # All optional; missing → no-op (block silently dropped from user_prompt).
    voice_dna_block: str | None = None,
    dialogue_voice_block: str | None = None,
    chapter_market_constraints_block: str | None = None,
    signature_scene_block: str | None = None,
    prior_persona_feedback_block: str | None = None,
    # ── Retention safety gates ──
    # hook_echo_block: lists prev chapter's hook tokens current chapter
    #   MUST echo in opening.
    # exposition_density_block: advisory ceiling on exposition density.
    # canon_guardrails_block: chapter-aware forbidden-character/term list
    #   (rendered from canon_guardrails). Prevents premature cast drift.
    hook_echo_block: str | None = None,
    exposition_density_block: str | None = None,
    # acceptance_duty_block: chapter-level acceptance criteria decomposed to
    #   THIS scene (first scene: opening echo with verbatim hook tokens;
    #   last scene: ending hook with the audit's anchor terms). Rendered by
    #   acceptance_contract.render_scene_acceptance_block.
    acceptance_duty_block: str | None = None,
    scene_beat_block: str | None = None,
    canon_guardrails_block: str | None = None,
    # ── Story Integrity blocks (LLM-first whitelists) ──
    # 2026-05-23: these three blocks were rendered but never injected
    # into the writing prompt. That's why ch1 of 青囊 kept regenerating
    # "十七年前" violations. They MUST appear at the TOP of the user
    # prompt, before character lists / arc beats / methodology, so the
    # LLM treats them as inviolable.
    # ``timeline_canon_block``: enumerated allowed time anchors.
    # ``scene_coherence_block``: transition marker rules.
    # ``character_role_block``: per-character ability/forbidden phrases.
    timeline_canon_block: str | None = None,
    scene_coherence_block: str | None = None,
    character_role_block: str | None = None,
    # ``chapter_length_block``: top-of-prompt reminder of the body-size
    # band (default 2000 floor / 2500 target zh chars).
    chapter_length_block: str | None = None,
    prewrite_contract_block: str | None = None,
    prewrite_plan_block: str | None = None,
    # Context budget
    context_budget_tokens: int = 6000,
) -> tuple[str, str]:
    language = _project_language(project)
    is_en = is_english_language(language)
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    _project_meta = getattr(project, "metadata_json", None)
    _project_meta = _project_meta if isinstance(_project_meta, dict) else {}
    _rejection_reasons = (
        _project_meta.get("editor_rejection_reasons")
        or _project_meta.get("rejection_reasons")
        or _project_meta.get("rejection_reason")
    )
    writing_profile_section = render_writing_profile_prompt_block(
        writing_profile,
        language=language,
        mode="scene",
        chapter_number=int(getattr(chapter, "chapter_number", 0) or 0),
    )
    serial_guardrails = render_serial_fiction_guardrails(writing_profile, language=language)
    # Build system prompt — 7-段式骨架（ROLE / CONTEXT / TASK / CONSTRAINTS /
    # THINKING / OUTPUT / EXAMPLES）。前 6 段跨场景稳定，进 Anthropic prompt
    # cache；写作画像 + 商业守则随项目变化，放在 CONSTRAINTS 末尾独立子段。
    _pack_label = getattr(prompt_pack, "key", None) or getattr(prompt_pack, "name", None) or "general"
    _genre_label = getattr(writing_profile.market, "platform_target", None) or "商业长篇连载"
    # Ranking-tier self-check: tell the writer the EXACT hard dimensions (and floors)
    # the commercial gate will score it against. Single-sourced from
    # chapter_commercial_thresholds so it can never drift from the gate.
    try:
        from bestseller.services.chapter_llm_quality_judge import (
            render_ranking_self_check_block,
        )

        ranking_self_check_block = render_ranking_self_check_block(
            int(getattr(chapter, "chapter_number", 0) or 0), language
        )
    except Exception:  # pragma: no cover - never let self-check break generation
        ranking_self_check_block = ""
    if is_en:
        system_prompt = (
            "# ROLE\n"
            "You are a senior commercial fiction writer with 5+ signed long-form titles.\n"
            f"You specialise in **{_pack_label}** sub-genre and write for {_genre_label}.\n"
            "Your single-chapter follow-on rate exceeds 100k readers — earned not by spectacle but by\n"
            "making every paragraph trigger an involuntary page-flip.\n"
            "\n"
            "# CONTEXT · Craft philosophy (internalised, never violated)\n"
            "1. **Action over adjectives** — not 'she was nervous' but 'her nails bit into her palm'.\n"
            "2. **Consequence over description** — not 'the wave was huge' but 'the freighter flipped like a bathtub toy'.\n"
            "3. **Subtext over statement** — true feelings live in action, silence, environment.\n"
            "4. **Every paragraph ends on an unresolved question** — the reader MUST turn the page.\n"
            "5. **One stroke, many functions** — environment + character + foreshadow + emotion in one beat.\n"
            "\n"
            "# CONTEXT · Pacing & character distinction\n"
            "- Not every scene is a chase. After high-tension, breathe (quiet dialogue / humour / sensory rest).\n"
            "- Vary paragraph rhythm: long flowing for atmosphere, short punchy for impact. Mix.\n"
            "- Silence/stillness/waiting often beat explosions. Use the space between events.\n"
            "- Each character has unique sentence length, vocabulary, speech habits.\n"
            "- Reader must identify the speaker WITHOUT dialogue tags.\n"
            "- Characters must initiate / refuse / surprise / contradict — never reactive props.\n"
            "\n"
            "# TASK\n"
            "Write the scene's prose body in Markdown.\n"
            "Output PROSE ONLY (narrative, dialogue, action, environment, inner thought).\n"
            "No explanations, no bullet lists, no planning notes, no commentary.\n"
            "\n"
            "# CONSTRAINTS · Hard (violation → rewrite)\n"
            "- Word count: 90%-120% of target. Below or above = rejected.\n"
            "- Character names: EXACT match to the Participants list. No renaming / abbreviation.\n"
            "- No named extras: never invent new personal names outside the Participants list; "
            "refer to unnamed walk-ons by role or appearance ('the duty clerk', 'the man at the gate').\n"
            "- Language: English only. Do not switch to Chinese.\n"
            "- Opening: do not repeat the same opening pattern (time / place / action / angle) used in the last 3 chapters.\n"
            "- No Markdown headings (# or ##). No code fences. No `entry_state` / `exit_state` / `contract` tags.\n"
            + _NOVEL_OUTPUT_PROHIBITION_EN
            + "\n"
            "# THINKING (plan in your head BEFORE writing — do NOT print this)\n"
            "1. **Scene goal** — what emotional + informational deliverable does the reader get?\n"
            "2. **Opening shot** — what concrete image is the first sentence? Not 'It was raining' — give a specific physical detail.\n"
            "3. **Rhythm arc** — what tempo at start / middle / end? Where is the breath?\n"
            "4. **Signature moment** — what 'screenshot-worthy' beat will you give the reader (golden line / surgical description / micro detail / reaction amplification)?\n"
            "5. **Closing hook** — what specific unanswered question lands at the end? (Not abstract — concrete.)\n"
            "\n"
            "# OUTPUT FORMAT\n"
            "- First sentence ≤ 80 characters (sharp).\n"
            "- First paragraph ≤ 150 characters.\n"
            "- First 250 characters MUST contain a visible anomaly (object, action, sensory).\n"
            "\n"
            "# EXAMPLES · Negative (never output these)\n"
            "- 'a feeling washed over him' / 'the air seemed to freeze' / 'time stood still'\n"
            "- 'mixed feelings' / 'inexplicable dread' / 'electric sensation'\n"
            "- Closing lines like 'this was only the beginning' or 'the real answer waited to be revealed'\n"
            "\n"
            + (ranking_self_check_block + "\n" if ranking_self_check_block else "")
            + "# PROJECT-SPECIFIC PROFILE (varies per project)\n"
            f"Writing profile:\n{writing_profile_section}\n"
            f"Serial fiction guardrails:\n{serial_guardrails}\n"
        )
    else:
        system_prompt = (
            "# ROLE\n"
            "你是一位写过 5 本起点 / 番茄 / 七猫签约长篇的中文网文写手。\n"
            f"你主攻 **{_pack_label}** 细分流派，作品上过 {_genre_label} 的推荐位。\n"
            "你单章追更稳定 10w+，靠的不是堆砌名场面，而是让每一段都触发读者的下意识翻页。\n"
            "\n"
            "# CONTEXT · 创作哲学（你已内化的 5 条铁律）\n"
            "1. **用动作代替形容词**——不写「她很紧张」，写「她的手指死死掐进掌心」\n"
            "2. **用后果代替描述**——不写「巨浪很大」，写「几千吨重的巨轮像塑料玩具一样被瞬间掀翻」\n"
            "3. **用潜台词代替直白**——人物真心藏在动作、沉默、环境反应里\n"
            "4. **每段结尾留未解问题**——读者必须下意识翻下一页\n"
            "5. **一笔多用**——一段同时承载环境 + 人设 + 伏笔 + 情感，绝不浪费笔墨\n"
            "\n"
            "# CONTEXT · 节奏呼吸\n"
            "- 不是每场都是追击战。高张力场景之后必须有喘息节拍（安静对话 / 幽默 / 感官休息 / 人物独处）。\n"
            "- 段落节奏要变化：长段铺氛围，短段打冲击。不要全文一个节奏。\n"
            "- 沉默、等待、留白比爆炸更有张力。善用空气感。\n"
            "\n"
            "# CONTEXT · 角色区分度\n"
            "- 每个角色有独属的句式长度、用词层次、说话习惯。\n"
            "- 不要只用「收紧下巴」「抱臂」这种通用动作；给每个角色一个只属于他的肢体语言。\n"
            "- 角色必须主动：主动、拒绝、出意外、开玩笑、反驳——不是被动道具。\n"
            "- 两人对话时，读者不看对话标签就能分辨是谁在说话。\n"
            "\n"
            "# TASK\n"
            "写出本场景的正文（Markdown 段落）。\n"
            "**只输出正文**（叙事 / 对话 / 动作 / 环境 / 内心活动）。\n"
            "不要输出解释 / 列表 / 策划说明 / 元评论 / 章节标题。\n"
            "\n"
            "# CONSTRAINTS · 硬约束（违反即重写，不可绕过）\n"
            "- 字数：CJK 汉字数须在目标的 90%-120% 之间。不足或超出均会被退回。\n"
            "- 角色名：与「参与者」列表完全一致，一字不差，禁止改名 / 别名 / 缩写。\n"
            "- 路人不取名：参与者列表之外不得出现任何新人名；无名路人/群众一律用职务、"
            "身份或外貌称谓（如「值班科员」「那名中年男人」「门口的保安」）。\n"
            "- 语言：仅输出中文，禁止切到英文。\n"
            "- 开场：禁止与前 3 章重复同一开场模式（时间 / 地点 / 动作 / 视角四维至少一维必须变）。\n"
            "- 输出格式：纯 Markdown 正文，不带 # 标题、不带 ``` 代码块、不带「以下是」「以上是」前后缀。\n"
            "- 标签禁止：不写 entry_state / exit_state / contract / scene_type 等英文结构化标签。\n"
            + _NOVEL_OUTPUT_PROHIBITION
            + "\n"
            "# THINKING（写正文前在脑内 plan 一遍，不要把 plan 印出来）\n"
            "1. **场景目标**：本场要给读者什么「情感 + 信息」双产出？\n"
            "2. **开场镜头**：第一句给什么具象画面？不要写「那是一个下雨天」，要写「雨棚下灯管闪了两下」这种具体物理细节。\n"
            "3. **节奏曲线**：本场从什么节奏起、中段如何变奏、尾段如何收？喘息节拍在哪？\n"
            "4. **签名段**：本场我打算给读者一个什么「截图段」？金句 / 神描写 / 神细节 / 反应放大瞬间 任选其一，必须有一个。\n"
            "5. **章末钩子**：本场结尾留一个什么具体悬念？不能是抽象感叹（如「一切才刚刚开始」），必须是具体未解物。\n"
            "\n"
            "# OUTPUT FORMAT · 开篇硬指标\n"
            "- 第一句 ≤ 25 个汉字（要狠）。\n"
            "- 第一段 ≤ 50 个汉字（要快）。\n"
            "- 前 200 字必须出现至少 1 个可视化异常物 / 异常动作（不能只有人物对话或回忆）。\n"
            "- 前 500 字内主角必须因这个异常被迫做出决定（不能只是观察、对话、回忆）。\n"
            "\n"
            "# EXAMPLES · AI 套话黑名单（绝对禁止输出）\n"
            "- 「血液仿佛凝固了」/「时间仿佛静止了」/「空气仿佛凝固了」\n"
            "- 「心中五味杂陈」/「心中百感交集」/「眼眶不由得湿润了」\n"
            "- 「一股莫名的情绪」/「一阵莫名的恐惧」\n"
            "- 「电流般的感觉」/「触电般的感觉」\n"
            "- 「仿佛有一只无形的手」/「像是被什么东西攫住了」\n"
            "- 任何以「显而易见」/「毫无疑问」/「不言而喻」开头的句子\n"
            "- 章末「这一切才刚刚开始」/「真正的答案还在等待揭开」/「欲知后事如何」\n"
            "\n"
            + (ranking_self_check_block + "\n" if ranking_self_check_block else "")
            + "# PROJECT PROFILE（项目级变量内容）\n"
            f"## 写作画像\n{writing_profile_section}\n\n"
            f"## 商业网文硬约束\n{serial_guardrails}\n"
        )
    tone = (
        ", ".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
        if style_guide and style_guide.tone_keywords and is_en
        else (
            "、".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
            if style_guide and style_guide.tone_keywords
            else ("taut, controlled" if is_en else "克制、紧张")
        )
    )
    if is_en:
        if re.search(r"[\u4e00-\u9fff]", tone):
            tone = "taut, controlled"
    elif not re.search(r"[\u4e00-\u9fff]", tone):
        tone = "克制、紧张"
    participants = _scene_participant_text(scene.participants, language=language)
    story_bible_section = _render_story_bible_section(story_bible_context, language=language)
    # World-model active-law injection: append only the laws relevant to this
    # chapter/scene (selector caps the count to avoid prompt overload). Fully
    # fail-safe — a missing/invalid world model leaves the section unchanged.
    try:
        from bestseller.services.world_model_injection import (
            build_active_law_prose_block_for_scene,
        )

        _world_law_block = build_active_law_prose_block_for_scene(
            project, chapter, scene, language=language
        )
        if _world_law_block:
            story_bible_section = (
                f"{story_bible_section}\n{_world_law_block}"
                if story_bible_section
                else _world_law_block
            )
    except Exception:  # noqa: BLE001 - world-law injection is additive; never break prompt build
        logger.debug("world-model active-law injection skipped", exc_info=True)
    retrieval_section = _render_retrieval_section(retrieval_context)
    recent_scene_section = _render_recent_scene_section(recent_scene_summaries)
    recent_timeline_section = _render_timeline_section(recent_timeline_events)
    participant_fact_section = _render_participant_fact_section(participant_canon_facts)
    arc_section = _render_arc_section(active_plot_arcs, active_arc_beats, language=language)
    clue_section = _render_clue_section(unresolved_clues, planned_payoffs, language=language)
    emotion_track_section = _render_emotion_track_section(active_emotion_tracks, language=language)
    antagonist_plan_section = _render_antagonist_plan_section(active_antagonist_plans, language=language)
    contract_section = _render_contract_section(chapter_contract, scene_contract, language=language)
    _scene_metadata, _scene_methodology_contract, _scene_controls = _scene_current_contract_controls(
        scene
    )
    _current_scene_contract: dict[str, Any] = {
        "methodology_contract": _scene_methodology_contract,
        "signature_image": _scene_controls.get("signature_image"),
        "cut_point": _scene_controls.get("cut_point"),
        "ending_hook_payload": _scene_controls.get("ending_hook_payload"),
        "action_sequence": _scene_controls.get("action_sequence"),
        "information_control_mode": _scene_controls.get("information_control_mode"),
    }
    _current_scene_contract = {
        key: value
        for key, value in _current_scene_contract.items()
        if value not in (None, "", [], {})
    }
    current_scene_contract_line = ""
    if _current_scene_contract:
        current_scene_contract_line = (
            "=== Current scene execution contract ===\n"
            "These fields are hard prose obligations. Write the signature image "
            "phrase into this scene body using its original wording (the gate "
            "matches the phrase text — a loose paraphrase fails); the cut point "
            "must also land visibly.\n"
            f"{_compact_json_block(_current_scene_contract, max_chars=1400)}\n\n"
            if is_en
            else (
                "=== 当前场景执行合同（必须写入正文）===\n"
                "以下字段是硬性正文义务：signature_image / 标志画面必须以【原词或基本原词】"
                "写进本场正文（质检按短语文本匹配，意译/换词会判失败）；"
                "cut_point / 断点必须在本场正文中可见落地。\n"
                f"{_compact_json_block(_current_scene_contract, max_chars=1400)}\n\n"
            )
        )
    story_principle_line = _render_story_principle_execution_section(
        chapter,
        scene,
        language=language,
    )
    if story_principle_line:
        story_principle_line += "\n\n"
    tree_section = _render_tree_section(tree_context_nodes, language=language)
    hard_fact_section = _render_hard_fact_snapshot_section(hard_fact_snapshot, language=language)
    prompt_pack_section = render_prompt_pack_prompt_block(prompt_pack)
    prompt_pack_scene_writer = render_prompt_pack_fragment(
        prompt_pack, "scene_writer"
    ) or render_prompt_pack_fragment(prompt_pack, "segment_writer")
    _pp_line = (
        f"Prompt Pack:\n{prompt_pack_section}\n"
        if prompt_pack_section and is_en
        else (f"Prompt Pack：\n{prompt_pack_section}\n" if prompt_pack_section else "")
    )
    _pp_writer_line = (
        f"Extra Prompt Pack guidance:\n{prompt_pack_scene_writer}\n"
        if prompt_pack_scene_writer and is_en
        else (f"Prompt Pack 额外写法：\n{prompt_pack_scene_writer}\n" if prompt_pack_scene_writer else "")
    )
    # Methodology rules injection (猫神方法论)
    _methodology_pack_block = render_methodology_block(prompt_pack, phase="scene")
    # Try contract fields first; fall back to positional heuristics when empty.
    _is_climax = bool(chapter_contract and chapter_contract.get("is_climax"))
    _pacing_mode_val = (chapter_contract or {}).get("pacing_mode") or ""
    if not _pacing_mode_val:
        # Heuristic: derive pacing mode from chapter position within the book
        _total = max(getattr(project, "target_chapters", None) or 20, 1)
        _pos = chapter.chapter_number / _total
        if _pos >= 0.75:
            _pacing_mode_val = "accelerate"
            # The last 10% of the book is climax territory
            if _pos >= 0.90:
                _is_climax = True
                _pacing_mode_val = "accelerate"
        elif _pos <= 0.15:
            _pacing_mode_val = "build"  # opening chapters
        elif chapter.chapter_number % 5 == 0:
            _pacing_mode_val = "breathe"  # periodic breathing room
        else:
            _pacing_mode_val = "build"
    # 爽文融合 (enable_shuangwen_fusion, default True): lift the 爽点 engines above
    # the 文采 flourish levers in PROSE_SCENE so 爽点 is never starved by the budget.
    # 文采 still ships — fusion reorders, it does not remove. See methodology_compiler.
    from bestseller.settings import get_settings  # noqa: PLC0415

    _settings_for_methodology = get_settings()
    _shuangwen_on = bool(
        getattr(_settings_for_methodology.pipeline, "enable_shuangwen_fusion", True)
    )
    # C1-rules trim (prompt-ablation ladder, 2026-06-10): drop the abstract
    # writing-methodology说教 bridge from the writer prompt unless explicitly
    # re-enabled. Frees PROSE_SCENE budget for the proven craft levers and
    # removes a confirmed net-zero/-negative, gate-safe (all soft) block.
    _include_methodology_rules = bool(
        getattr(_settings_for_methodology.generation, "prose_writer_methodology_rules", False)
    )
    # Compile FIRST (before the scene-rules bridge) so we know whether the
    # compiled methodology owns the prose-craft sections (visual_writing /
    # dialogue_rules). When it does, the bridge must NOT re-state 画面感规则 /
    # 对话规则 — that paraphrase-duplication put "show don't tell" into the
    # prompt 3× and is a top dilution source (prompt-ablation ladder).
    _compiled_methodology = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=_resolve_prompt_pack_key(project),
        language=language,
        chapter_no=chapter.chapter_number,
        chapter_position=_infer_chapter_position(project, chapter),
        # Budget (2026-06): raised to 4200 so the new always-on #1 lever
        # cinematic_pov (镜头化·体验优先, ~290 tok) fits ON TOP of the文采 levers
        # (注入总则 + prose_craft 金句 + imagery 意象 + emotion choreography) without
        # starving the tail — at 3200 adding cinematic_pov dropped craft/emotion from
        # the shuangwen-mode tail, breaking the "文采 preserved" invariant. The full
        # PROSE_SCENE block caps at ~3.8k tokens; 4200 fits every lever in both modes.
        token_budget=4200,
        story_bible=story_bible_context,
        shuangwen_mode=_shuangwen_on,
        include_writing_methodology_bridge=_include_methodology_rules,
    ).text
    _compiled_has_methodology = "writing_methodology · scene" in _compiled_methodology
    _methodology_rules = render_methodology_scene_rules(
        chapter_number=chapter.chapter_number,
        is_opening=(chapter.chapter_number <= 3),
        is_climax=_is_climax,
        pacing_mode=_pacing_mode_val,
        platform_target=getattr(writing_profile.market, "platform_target", ""),
        language=language,
        rejection_reasons=str(_rejection_reasons) if _rejection_reasons else None,
        # Drop the always-on 画面感规则 / 对话规则 restatements when the compiled
        # methodology already renders visual_writing / dialogue_rules in full;
        # the context-specific bridge rules (开篇/高潮/节奏期/七猫签约·重生) stay.
        include_baseline_craft_rules=not _compiled_has_methodology,
    )
    # The compiled methodology is the budget-managed single source for the
    # writing methodology: it renders the same writing_methodology.yaml (plus
    # the pack's 题材方法论) that the bridge block above renders, but under the
    # compiler's token budget and priority order. Keeping both copies costs
    # ~2k Tier-1 tokens, which starves every Tier-2/3 narrative section
    # (story bible, recent scenes, clues) out of the writer prompt.
    # Also blank it when trimming C1-rules — the pack's 题材方法论·scene block
    # is the same说教 class and would half-defeat the trim if kept.
    if _compiled_has_methodology or not _include_methodology_rules:
        _methodology_pack_block = ""
    _methodology_line = ""
    if _methodology_pack_block or _methodology_rules or _compiled_methodology:
        _methodology_line = (
            f"{_methodology_pack_block}\n\n{_methodology_rules}\n\n{_compiled_methodology}\n\n"
            if _methodology_pack_block
            else f"{_methodology_rules}\n\n{_compiled_methodology}\n\n"
        )
    # Quality-levers writer block (Steps A-D YAML-driven contracts).
    # Pulls every applicable lever for the chapter into one prompt fragment;
    # extract_quality_levers_meta tolerates missing / malformed keys so the
    # writer prompt always degrades gracefully when meta.yaml is empty.
    try:
        _levers_meta = extract_quality_levers_meta(_project_meta)
        _quality_levers_block = build_writer_quality_levers_block(
            WriterLeverContext(
                chapter_number=chapter.chapter_number,
                language=language or "zh-CN",
                platform=(
                    _levers_meta.target_platform
                    or getattr(writing_profile.market, "platform_target", None)
                ),
                style_anchors=_levers_meta.style_anchors,
                chapter_positions=_levers_meta.positions_for_chapter(
                    chapter.chapter_number
                ),
                participating_character_ids=_levers_meta.character_profile_ids,
                participating_character_profiles=_levers_meta.character_profiles,
                chapter_role="hook_chapter" if chapter.chapter_number <= 3 else "ordinary_chapter",
                rejection_cause_ids=tuple(
                    str(item) for item in (_rejection_reasons or ()) if str(item).strip()
                ),
                distilled_strategy_card=(
                    _project_meta.get("distilled_strategy_card")
                    if isinstance(_project_meta.get("distilled_strategy_card"), dict)
                    else None
                ),
                emotion_driven_kernel=_levers_meta.emotion_driven_kernel,
                public_emotion_kernel=_levers_meta.public_emotion_kernel,
            )
        )
    except Exception:
        # Quality levers must never break the existing draft path.
        _quality_levers_block = ""
    if _quality_levers_block:
        _methodology_line += f"{_quality_levers_block}\n\n"
    _methodology_line = _dedupe_methodology_sections(_methodology_line)
    _qimao_opening_contract_line = ""
    _qimao_opening_contract_block = render_qimao_opening_contract_block(
        _project_meta.get("opening_quality_contract") or _project_meta.get("qimao_opening_contract"),
        chapter_number=chapter.chapter_number,
        language=language,
        rejection_reasons=str(_rejection_reasons) if _rejection_reasons else None,
    )
    if _qimao_opening_contract_block:
        _qimao_opening_contract_line = f"{_qimao_opening_contract_block}\n\n"
    _hard_fact_line = f"{hard_fact_section}\n\n" if hard_fact_section else ""
    _contradiction_line = ""
    if contradiction_warnings:
        _warning_items = "\n".join(f"- {w}" for w in contradiction_warnings)
        _contradiction_line = (
            f"=== Continuity constraints (must obey) ===\n{_warning_items}\n"
            f"=== Do not violate the constraints above ===\n\n"
            if is_en
            else (
                f"=== 连续性约束（必须遵守）===\n{_warning_items}\n"
                f"=== 不得违反以上约束 ===\n\n"
            )
        )
    _query_brief_line = ""
    if query_brief:
        _query_brief_line = (
            f"=== Active query brief ===\n{query_brief}\n\n"
            if is_en
            else f"=== 主动查询补充简报 ===\n{query_brief}\n\n"
        )
    _knowledge_line = _render_knowledge_state_section(participant_knowledge_states, is_en=is_en)
    if _knowledge_line:
        _knowledge_line += "\n\n"

    # Character identity constraints (Tier 0 — always included)
    _identity_line = ""
    if identity_constraint_block:
        _identity_line = f"{identity_constraint_block}\n\n"

    # Overused phrase avoidance (Tier 1 — always included)
    _phrase_avoidance_line = ""
    if overused_phrase_block:
        _phrase_avoidance_line = f"{overused_phrase_block}\n\n"

    # Opening diversity avoidance (Tier 1 — injected for scene 1 of each chapter)
    _opening_diversity_line = ""
    if opening_diversity_block:
        _opening_diversity_line = f"{opening_diversity_block}\n\n"

    # Genre-specific constraints (Tier 1 — always included)
    _genre_constraint_line = ""
    if genre_constraint_block:
        _genre_constraint_line = f"{genre_constraint_block}\n\n"

    # Ranking-level book capability profile (Tier 1 — book-specific).
    _ranking_profile_line = ""
    _fanqie_market_craft_block = render_fanqie_market_craft_profile_block(
        _project_meta.get("fanqie_craft_profile")
        if isinstance(_project_meta.get("fanqie_craft_profile"), dict)
        else None,
        language=language,
    )
    _ranking_profile_parts = [
        part
        for part in (ranking_capability_profile_block, _fanqie_market_craft_block)
        if part
    ]
    if _ranking_profile_parts:
        _ranking_profile_line = "\n\n".join(_ranking_profile_parts) + "\n\n"

    # Premium genre engine constraints (Tier 1 — always included)
    _progression_context_line = ""
    if progression_context_block:
        _progression_context_line = f"{progression_context_block}\n\n"
    _decision_policy_line = ""
    if decision_policy_block:
        _decision_policy_line = f"{decision_policy_block}\n\n"
    _rule_system_line = ""
    if rule_system_context_block:
        _rule_system_line = f"{rule_system_context_block}\n\n"
    _faction_ecology_line = ""
    if faction_ecology_context_block:
        _faction_ecology_line = f"{faction_ecology_context_block}\n\n"
    _relationship_agency_line = ""
    if relationship_agency_context_block:
        _relationship_agency_line = f"{relationship_agency_context_block}\n\n"
    _entry_system_line = ""
    if entry_system_context_block:
        _entry_system_line = f"{entry_system_context_block}\n\n"
    _entry_registry_line = ""
    if entry_registry_context_block:
        _entry_registry_line = f"{entry_registry_context_block}\n\n"
    _entry_state_ledger_line = ""
    if entry_state_ledger_block:
        _entry_state_ledger_line = f"{entry_state_ledger_block}\n\n"

    # Stage A — conflict diversity (ALL scenes, not just scene 1)
    _conflict_diversity_line = ""
    if conflict_diversity_block:
        _conflict_diversity_line = f"{conflict_diversity_block}\n\n"

    # Stage B — scene-purpose diversity (ALL scenes)
    _scene_purpose_line = ""
    if scene_purpose_diversity_block:
        _scene_purpose_line = f"{scene_purpose_diversity_block}\n\n"

    # Stage B — environment 7-d diversity (ALL scenes)
    _env_diversity_line = ""
    if env_diversity_block:
        _env_diversity_line = f"{env_diversity_block}\n\n"

    # Stage C — POV arc beat block (ALL scenes)
    _arc_beat_line = ""
    if arc_beat_block:
        _arc_beat_line = f"{arc_beat_block}\n\n"

    # Stage C — 5-layer thinking contract (ALL scenes)
    _five_layer_line = ""
    if five_layer_block:
        _five_layer_line = f"{five_layer_block}\n\n"

    # Stage D — cliffhanger diversity (last-scene-only ideally, but we show it on all)
    _cliffhanger_line = ""
    if cliffhanger_diversity_block:
        _cliffhanger_line = f"{cliffhanger_diversity_block}\n\n"

    # Stage D — chapter tension target (ALL scenes of the chapter share this)
    _tension_target_line = ""
    if tension_target_block:
        _tension_target_line = f"{tension_target_block}\n\n"

    # Stage B+ — location ledger (ALL scenes)
    _location_ledger_line = ""
    if location_ledger_block:
        _location_ledger_line = f"{location_ledger_block}\n\n"

    # L3 — DiversityBudget (hot vocab + structured rotation). Orthogonal to
    # the heuristic ``overused_phrase_block`` above.
    _budget_diversity_line = ""
    if budget_diversity_block:
        _budget_diversity_line = f"{budget_diversity_block}\n\n"

    # Scene scope isolation — earlier scenes in this chapter that are already
    # written.  Prevents the writer from rewriting / paraphrasing them in
    # this scene (root cause of intra-chapter duplication).
    _scene_scope_isolation_line = ""
    if scene_scope_isolation_block:
        _scene_scope_isolation_line = f"{scene_scope_isolation_block}\n\n"

    # Plan-richness RED FLAGS — the pre-draft gate detected a thin scene card.
    # Surface the issues so the writer LLM knows which fields are generic /
    # missing and must compensate with its own concrete invention.
    _plan_richness_line = ""
    if plan_richness_block:
        _plan_richness_line = f"{plan_richness_block}\n\n"

    # Reader Hype Engine — reader contract (commercial frame, cadenced) and
    # per-chapter hype constraints. Pre-rendered by
    # ``build_chapter_hype_blocks``; empty strings stay no-ops for legacy
    # projects with empty HypeScheme.
    _reader_contract_line = ""
    if reader_contract_block:
        _reader_contract_line = f"{reader_contract_block}\n\n"
    _concept_lab_contract_line = ""
    _concept_lab_contract_block = render_concept_lab_prompt_block(_project_meta, language=language)
    if _concept_lab_contract_block:
        _concept_lab_contract_line = f"{_concept_lab_contract_block}\n\n"
    # Book-level story-enhancer contract (脑洞/喜剧/爽点 + 基调锚点) + this
    # chapter's planned cashing. Closes the gap where selected enhancers reached
    # the outline but never the prose. Empty unless the book opted in / the
    # chapter carries a cashed contract → prompt stays byte-identical otherwise.
    _story_enhancer_writer_line = ""
    _story_enhancer_writer_block = render_story_enhancer_writer_block(
        _project_meta,
        getattr(chapter, "metadata_json", None),
        language=language,
    )
    if _story_enhancer_writer_block:
        _story_enhancer_writer_line = f"{_story_enhancer_writer_block}\n\n"
    _hype_constraints_line = ""
    if hype_constraints_block:
        _hype_constraints_line = f"{hype_constraints_block}\n\n"

    # L3 Prompt Constructor — diversity/methodology/invariants/anti-slop
    # pre-rendered block (see prompt_constructor.build_chapter_l3_blocks).
    _l3_prompt_line = ""
    if l3_prompt_block:
        _l3_prompt_line = f"{l3_prompt_block}\n\n"

    # P1 Originality Engine blocks. All four are pre-rendered by
    # pipelines.py from chapter_orchestrator.prepare_chapter_context.
    # When the project hasn't extracted DNA / planned signatures /
    # written a prior chapter, the corresponding line stays empty —
    # the f-string concatenation below produces a byte-identical
    # prompt to legacy.
    _voice_dna_line = ""
    if voice_dna_block:
        _voice_dna_line = f"{voice_dna_block}\n\n"
    _dialogue_voice_line = ""
    if dialogue_voice_block:
        _dialogue_voice_line = f"{dialogue_voice_block}\n\n"
    _chapter_market_constraints_line = ""
    if chapter_market_constraints_block:
        _chapter_market_constraints_line = (
            f"{chapter_market_constraints_block}\n\n"
        )
    _signature_scene_line = ""
    if signature_scene_block:
        _signature_scene_line = f"{signature_scene_block}\n\n"
    _prior_persona_feedback_line = ""
    if prior_persona_feedback_block:
        _prior_persona_feedback_line = f"{prior_persona_feedback_block}\n\n"

    # Retention safety gates — Hook Echo must come BEFORE bible context
    # so the LLM treats prev-chapter hooks as primary constraint rather than
    # afterthought.
    _hook_echo_line = ""
    if hook_echo_block:
        _hook_echo_line = f"{hook_echo_block}\n\n"
    # Chapter acceptance duties decomposed to this scene — placed alongside
    # the hook-echo block so the writer reads them as primary constraints.
    _acceptance_duty_line = ""
    if acceptance_duty_block:
        _acceptance_duty_line = f"{acceptance_duty_block}\n\n"
    _exposition_density_line = ""
    if exposition_density_block:
        _exposition_density_line = f"{exposition_density_block}\n\n"
    _scene_beat_line = ""
    if scene_beat_block:
        _scene_beat_line = f"{scene_beat_block}\n\n"
    _canon_guardrails_line = ""
    if canon_guardrails_block:
        _canon_guardrails_line = f"{canon_guardrails_block}\n\n"
    # Story Integrity whitelist blocks — render as top-of-prompt anchors.
    _timeline_canon_line = ""
    if timeline_canon_block:
        _timeline_canon_line = f"{timeline_canon_block}\n\n"
    _scene_coherence_line = ""
    if scene_coherence_block:
        _scene_coherence_line = f"{scene_coherence_block}\n\n"
    _character_role_line = ""
    if character_role_block:
        _character_role_line = f"{character_role_block}\n\n"
    _chapter_length_line = ""
    if chapter_length_block:
        _chapter_length_line = f"{chapter_length_block}\n\n"
    # R23 — per-scene hard acceptance (word budget + signature obligations).
    # Reads live data from the scene object and is prepended BEFORE every
    # other constraint block in the user prompt; see
    # ``_render_scene_word_budget_block``.  ``_chapter_length_line`` above is
    # the CHAPTER-level band and is kept; the legacy mid-prompt scene-level
    # word line is de-duplicated below when this block is present.
    _scene_word_budget_line = _render_scene_word_budget_block(scene, is_en=is_en)
    if _scene_word_budget_line:
        _scene_word_budget_line += "\n\n"
    _prewrite_contract_line = ""
    if prewrite_contract_block:
        _prewrite_contract_line = f"{prewrite_contract_block}\n\n"
    _prewrite_plan_line = ""
    if prewrite_plan_block:
        _prewrite_plan_line = f"{prewrite_plan_block}\n\n"

    # Material library soft reference — opt-in inspiration for old projects'
    # new chapters. See ``material_library_reference`` module docstring.
    _library_reference_line = ""
    if library_reference_block:
        _library_reference_line = f"{library_reference_block}\n\n"

    _project_material_reference_line = ""
    if _project_meta:
        _project_material_reference_block = str(
            _project_meta.get("material_reference_block") or ""
        ).strip()
        if _project_material_reference_block:
            _project_material_lead = (
                "=== Project material anchors ===\n"
                "Use these §slug anchors as canonical project material. Do not invent "
                "equivalent rules, factions, devices, locations, or motif systems when "
                "an anchor already covers the function.\n"
                if is_en
                else (
                    "=== 本书素材锚点（必须优先使用）===\n"
                    "以下 §slug 是本书已落库素材。写作时必须优先使用这些既有规则、地点、人物、物件、情绪弧和反套路约束；"
                    "不得另造同功能的新名词、新规则或无关怪谈。\n"
                )
            )
            _project_material_reference_line = (
                f"{_project_material_lead}{_project_material_reference_block}\n\n"
            )
    _project_material_obligation_line = _render_project_material_obligation_packet(
        project,
        chapter_number=chapter.chapter_number,
        chapter_position=f"chapter-{chapter.chapter_number}",
        prompt_pack_key=_resolve_prompt_pack_key(project),
        is_en=is_en,
    )
    if _project_material_obligation_line:
        _project_material_obligation_line += "\n\n"

    # Phase-3 wiring: scene/sequel pattern
    _scene_sequel_line = _render_scene_sequel_section(
        swain_pattern, scene_skeleton, is_en=is_en,
    )
    if _scene_sequel_line:
        _scene_sequel_line += "\n\n"

    # Phase-2 wiring: structure beat
    _structure_beat_line = _render_structure_beat_section(
        structure_beat_name, structure_beat_description, is_en=is_en,
    )
    if _structure_beat_line:
        _structure_beat_line += "\n\n"

    # Phase-1 wiring: render five previously orphaned narrative context sections
    _pacing_line = _render_pacing_target_section(pacing_target, is_en=is_en)
    if _pacing_line:
        _pacing_line += "\n\n"
    _subplot_line = _render_subplot_prominence_section(subplot_schedule, is_en=is_en)
    if _subplot_line:
        _subplot_line += "\n\n"
    _ending_line = _render_ending_contract_section(ending_contract, is_en=is_en)
    if _ending_line:
        _ending_line += "\n\n"
    _reader_knowledge_line = _render_reader_knowledge_section(reader_knowledge_entries, is_en=is_en)
    if _reader_knowledge_line:
        _reader_knowledge_line += "\n\n"
    _relationship_line = _render_relationship_milestone_section(relationship_milestones, is_en=is_en)
    if _relationship_line:
        _relationship_line += "\n\n"

    # Phase-5 wiring: genre obligatory scenes
    _obligations_line = _render_genre_obligations_section(genre_obligations_due, is_en=is_en)
    if _obligations_line:
        _obligations_line += "\n\n"

    # Phase-6 wiring: foreshadowing gap warning
    _foreshadow_line = _render_foreshadowing_gap_section(foreshadowing_gap_warning, is_en=is_en)
    if _foreshadow_line:
        _foreshadow_line += "\n\n"

    # Arc summaries (warm context) and world snapshot (cold context)
    _arc_summary_line = ""
    if arc_summaries:
        _arc_items = []
        for arc_s in arc_summaries:
            ch_start = arc_s.get("chapter_start", "?")
            ch_end = arc_s.get("chapter_end", "?")
            growth = arc_s.get("protagonist_growth", "")
            threads = ", ".join(arc_s.get("unresolved_threads", [])[:3])
            _arc_items.append(
                f"  Arc Ch{ch_start}-{ch_end}: {growth}"
                + (f" | Unresolved: {threads}" if threads else "")
            )
        _arc_block = "\n".join(_arc_items)
        _arc_summary_line = (
            f"=== Recent arc recap (warm context) ===\n{_arc_block}\n\n"
            if is_en
            else f"=== 近期弧线回顾（温上下文）===\n{_arc_block}\n\n"
        )
    _world_snapshot_line = ""
    if world_snapshot:
        ws = world_snapshot.get("world_summary", "")
        if ws:
            _world_snapshot_line = (
                f"=== World state (cold context) ===\n{ws}\n\n"
                if is_en
                else f"=== 世界状态（冷上下文）===\n{ws}\n\n"
            )

    # --- Context budget enforcement ---
    # Pack all rendered sections into a dict, run through the budget filter,
    # then unpack back into local variables.  This keeps Tier 1 sections
    # intact while trimming Tier 2/3 when the combined context is too large.
    _ctx = _budget_context_sections(
        {
            "contract_section": contract_section,
            "story_principle_line": story_principle_line,
            "methodology_line": _methodology_line,
            "participant_fact_section": participant_fact_section,
            "contradiction_line": _contradiction_line,
            "query_brief_line": _query_brief_line,
            "identity_line": _identity_line,
            "phrase_avoidance_line": _phrase_avoidance_line,
            "opening_diversity_line": _opening_diversity_line,
            "genre_constraint_line": _genre_constraint_line,
            "ranking_profile_line": _ranking_profile_line,
            "progression_context_line": _progression_context_line,
            "decision_policy_line": _decision_policy_line,
            "rule_system_line": _rule_system_line,
            "faction_ecology_line": _faction_ecology_line,
            "relationship_agency_line": _relationship_agency_line,
            "entry_system_line": _entry_system_line,
            "entry_registry_line": _entry_registry_line,
            "entry_state_ledger_line": _entry_state_ledger_line,
            "conflict_diversity_line": _conflict_diversity_line,
            "scene_purpose_line": _scene_purpose_line,
            "env_diversity_line": _env_diversity_line,
            "arc_beat_line": _arc_beat_line,
            "five_layer_line": _five_layer_line,
            "cliffhanger_line": _cliffhanger_line,
            "tension_target_line": _tension_target_line,
            "location_ledger_line": _location_ledger_line,
            "budget_diversity_line": _budget_diversity_line,
            "scene_scope_isolation_line": _scene_scope_isolation_line,
            "plan_richness_line": _plan_richness_line,
            "reader_contract_line": _reader_contract_line,
            "concept_lab_contract_line": _concept_lab_contract_line,
            "hype_constraints_line": _hype_constraints_line,
            "current_scene_contract_line": current_scene_contract_line,
            "qimao_opening_contract_line": _qimao_opening_contract_line,
            "l3_prompt_line": _l3_prompt_line,
            "voice_dna_line": _voice_dna_line,
            "dialogue_voice_line": _dialogue_voice_line,
            "chapter_market_constraints_line": _chapter_market_constraints_line,
            "signature_scene_line": _signature_scene_line,
            "prior_persona_feedback_line": _prior_persona_feedback_line,
            "hook_echo_line": _hook_echo_line,
            "acceptance_duty_line": _acceptance_duty_line,
            "exposition_density_line": _exposition_density_line,
            "scene_beat_line": _scene_beat_line,
            "canon_guardrails_line": _canon_guardrails_line,
            "timeline_canon_line": _timeline_canon_line,
            "scene_coherence_line": _scene_coherence_line,
            "character_role_line": _character_role_line,
            "chapter_length_line": _chapter_length_line,
            "scene_word_budget_line": _scene_word_budget_line,
            "project_material_reference_line": _project_material_reference_line,
            "project_material_obligation_line": _project_material_obligation_line,
            "library_reference_line": _library_reference_line,
            "hard_fact_line": _hard_fact_line,
            "knowledge_line": _knowledge_line,
            "recent_scene_section": recent_scene_section,
            "emotion_track_section": emotion_track_section,
            "antagonist_plan_section": antagonist_plan_section,
            "clue_section": clue_section,
            "scene_sequel_line": _scene_sequel_line,
            "structure_beat_line": _structure_beat_line,
            "pacing_line": _pacing_line,
            "story_bible_section": story_bible_section,
            "arc_section": arc_section,
            "arc_summary_line": _arc_summary_line,
            "world_snapshot_line": _world_snapshot_line,
            "retrieval_section": retrieval_section,
            "recent_timeline_section": recent_timeline_section,
            "reader_knowledge_line": _reader_knowledge_line,
            "relationship_line": _relationship_line,
            "subplot_line": _subplot_line,
            "ending_line": _ending_line,
            "obligations_line": _obligations_line,
            "foreshadow_line": _foreshadow_line,
            "tree_section": tree_section,
            "pp_line": _pp_line,
            "pp_writer_line": _pp_writer_line,
        },
        context_budget_tokens,
    )
    # Unpack budgeted sections back into local variables
    contract_section = _ctx["contract_section"]
    story_principle_line = _ctx["story_principle_line"]
    _methodology_line = _ctx["methodology_line"]
    participant_fact_section = _ctx["participant_fact_section"]
    _contradiction_line = _ctx["contradiction_line"]
    _query_brief_line = _ctx["query_brief_line"]
    _identity_line = _ctx["identity_line"]
    _phrase_avoidance_line = _ctx["phrase_avoidance_line"]
    _opening_diversity_line = _ctx["opening_diversity_line"]
    _genre_constraint_line = _ctx["genre_constraint_line"]
    _ranking_profile_line = _ctx["ranking_profile_line"]
    _progression_context_line = _ctx["progression_context_line"]
    _decision_policy_line = _ctx["decision_policy_line"]
    _rule_system_line = _ctx["rule_system_line"]
    _faction_ecology_line = _ctx["faction_ecology_line"]
    _relationship_agency_line = _ctx["relationship_agency_line"]
    _entry_system_line = _ctx["entry_system_line"]
    _entry_registry_line = _ctx["entry_registry_line"]
    _entry_state_ledger_line = _ctx["entry_state_ledger_line"]
    _conflict_diversity_line = _ctx["conflict_diversity_line"]
    _scene_purpose_line = _ctx["scene_purpose_line"]
    _env_diversity_line = _ctx["env_diversity_line"]
    _arc_beat_line = _ctx["arc_beat_line"]
    _five_layer_line = _ctx["five_layer_line"]
    _cliffhanger_line = _ctx["cliffhanger_line"]
    _tension_target_line = _ctx["tension_target_line"]
    _location_ledger_line = _ctx["location_ledger_line"]
    _budget_diversity_line = _ctx["budget_diversity_line"]
    _scene_scope_isolation_line = _ctx["scene_scope_isolation_line"]
    _plan_richness_line = _ctx["plan_richness_line"]
    _reader_contract_line = _ctx["reader_contract_line"]
    _concept_lab_contract_line = _ctx["concept_lab_contract_line"]
    _hype_constraints_line = _ctx["hype_constraints_line"]
    current_scene_contract_line = _ctx["current_scene_contract_line"]
    _qimao_opening_contract_line = _ctx["qimao_opening_contract_line"]
    _l3_prompt_line = _ctx["l3_prompt_line"]
    _voice_dna_line = _ctx["voice_dna_line"]
    _dialogue_voice_line = _ctx["dialogue_voice_line"]
    _chapter_market_constraints_line = _ctx["chapter_market_constraints_line"]
    _signature_scene_line = _ctx["signature_scene_line"]
    _prior_persona_feedback_line = _ctx["prior_persona_feedback_line"]
    _hook_echo_line = _ctx["hook_echo_line"]
    _acceptance_duty_line = _ctx["acceptance_duty_line"]
    _exposition_density_line = _ctx["exposition_density_line"]
    _scene_beat_line = _ctx["scene_beat_line"]
    _canon_guardrails_line = _ctx["canon_guardrails_line"]
    _timeline_canon_line = _ctx["timeline_canon_line"]
    _scene_coherence_line = _ctx["scene_coherence_line"]
    _character_role_line = _ctx["character_role_line"]
    _chapter_length_line = _ctx["chapter_length_line"]
    _scene_word_budget_line = _ctx["scene_word_budget_line"]
    _project_material_reference_line = _ctx["project_material_reference_line"]
    _project_material_obligation_line = _ctx["project_material_obligation_line"]
    _library_reference_line = _ctx["library_reference_line"]
    _hard_fact_line = _ctx["hard_fact_line"]
    _knowledge_line = _ctx["knowledge_line"]
    recent_scene_section = _ctx["recent_scene_section"]
    emotion_track_section = _ctx["emotion_track_section"]
    antagonist_plan_section = _ctx["antagonist_plan_section"]
    clue_section = _ctx["clue_section"]
    _scene_sequel_line = _ctx["scene_sequel_line"]
    _structure_beat_line = _ctx["structure_beat_line"]
    _pacing_line = _ctx["pacing_line"]
    story_bible_section = _ctx["story_bible_section"]
    arc_section = _ctx["arc_section"]
    _arc_summary_line = _ctx["arc_summary_line"]
    _world_snapshot_line = _ctx["world_snapshot_line"]
    retrieval_section = _ctx["retrieval_section"]
    recent_timeline_section = _ctx["recent_timeline_section"]
    _reader_knowledge_line = _ctx["reader_knowledge_line"]
    _relationship_line = _ctx["relationship_line"]
    _subplot_line = _ctx["subplot_line"]
    _ending_line = _ctx["ending_line"]
    _obligations_line = _ctx["obligations_line"]
    _foreshadow_line = _ctx["foreshadow_line"]
    tree_section = _ctx["tree_section"]
    _pp_line = _ctx["pp_line"]
    _pp_writer_line = _ctx["pp_writer_line"]

    # R23 de-dup: when the scene hard-acceptance block leads the prompt, do
    # not repeat a second (numerically conflicting) scene-level word band
    # mid-prompt — keep a short pointer to the top block instead.
    if _scene_word_budget_line:
        _scene_target_words_line = (
            f"Target words: {scene.target_word_count} (binding range: see the "
            "scene hard-acceptance block at the top)\n"
            if is_en
            else (
                f"目标字数：{scene.target_word_count}"
                "（硬性区间以顶部「本场硬验收」块为准）\n"
            )
        )
    else:
        _scene_target_words_line = (
            f"Target words: {scene.target_word_count} (STRICT RANGE: {int(scene.target_word_count * 0.9)}-{int(scene.target_word_count * 1.2)} words. Scenes outside this range will be rejected. Do NOT cut short and do NOT over-write.)\n"
            if is_en
            else f"目标字数：{scene.target_word_count}（【硬性要求】正文字数必须在 {int(scene.target_word_count * 0.9)}-{int(scene.target_word_count * 1.2)} 字范围内。不足或超出均会退回重写。不要提前收束，也不要注水拖长。）\n"
        )

    if is_en:
        user_prompt = (
            # R23 — the scene's own hard acceptance contract (word budget +
            # signature obligations) leads the prompt, before every other
            # constraint block.
            f"{_scene_word_budget_line}"
            # Story Integrity whitelists — these MUST come first so the
            # LLM treats them as inviolable constraints, not later
            # afterthoughts. Order: timeline → scene → character → length.
            f"{_timeline_canon_line}"
            f"{_scene_coherence_line}"
            f"{_character_role_line}"
            f"{_chapter_length_line}"
            f"{_prewrite_contract_line}"
            f"{_prewrite_plan_line}"
            f"{_hard_fact_line}"
            f"{_contradiction_line}"
            f"{_query_brief_line}"
            f"{_reader_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_writer_line}"
            f"{_hype_constraints_line}"
            f"{current_scene_contract_line}"
            f"{_qimao_opening_contract_line}"
            f"{_l3_prompt_line}"
            f"{_voice_dna_line}"
            f"{_dialogue_voice_line}"
            f"{_chapter_market_constraints_line}"
            f"{_signature_scene_line}"
            f"{_canon_guardrails_line}"
            f"{_hook_echo_line}"
            f"{_acceptance_duty_line}"
            f"{_exposition_density_line}"
            f"{_scene_beat_line}"
            f"{_prior_persona_feedback_line}"
            f"{_project_material_reference_line}"
            f"{_project_material_obligation_line}"
            f"{_library_reference_line}"
            f"{_plan_richness_line}"
            f"{_identity_line}"
            f"{_scene_scope_isolation_line}"
            f"{_genre_constraint_line}"
            f"{_ranking_profile_line}"
            f"{_progression_context_line}"
            f"{_decision_policy_line}"
            f"{_rule_system_line}"
            f"{_faction_ecology_line}"
            f"{_relationship_agency_line}"
            f"{_entry_system_line}"
            f"{_entry_registry_line}"
            f"{_entry_state_ledger_line}"
            f"{_phrase_avoidance_line}"
            f"{_opening_diversity_line}"
            f"{_budget_diversity_line}"
            f"{_conflict_diversity_line}"
            f"{_scene_purpose_line}"
            f"{_env_diversity_line}"
            f"{_location_ledger_line}"
            f"{_arc_beat_line}"
            f"{_five_layer_line}"
            f"{_tension_target_line}"
            f"{_cliffhanger_line}"
            f"{_knowledge_line}"
            f"{_arc_summary_line}"
            f"{_world_snapshot_line}"
            f"{_structure_beat_line}"
            f"{_scene_sequel_line}"
            f"{_pacing_line}"
            f"{_subplot_line}"
            f"{_ending_line}"
            f"{_reader_knowledge_line}"
            f"{_relationship_line}"
            f"{_obligations_line}"
            f"{_foreshadow_line}"
            f"{story_principle_line}"
            f"Project: {project.title}\n"
            f"Chapter {chapter.chapter_number}: {chapter.title or ''}\n"
            f"Chapter goal (for intent only, never quote it verbatim): {chapter.chapter_goal}\n"
            f"Scene {scene.scene_number}: {scene.title or ''}\n"
            f"Scene type: {scene.scene_type}\n"
            f"Time label: {scene.time_label or 'unspecified'}\n"
            f"Participants: {participants}\n"
            f"Story purpose: {scene.purpose.get('story', 'advance the chapter spine')}\n"
            f"Emotional purpose: {scene.purpose.get('emotion', 'raise tension')}\n"
            f"Entry state: {scene.entry_state}\n"
            f"Exit state: {scene.exit_state}\n"
            f"{_scene_target_words_line}"
            f"POV: {style_guide.pov_type if style_guide else 'third-limited'}\n"
            f"Tone keywords: {tone}\n"
            f"{_pp_line}"
            f"Story bible constraints:\n{story_bible_section or 'No additional story-bible constraints.'}\n"
            f"Recent story recap:\n{recent_scene_section or 'No recent-scene recap.'}\n"
            f"Known timeline beats:\n{recent_timeline_section or 'No known timeline beats.'}\n"
            f"Active narrative lines and beats:\n{arc_section or 'No explicit arc constraints.'}\n"
            f"Clue and payoff constraints:\n{clue_section or 'No explicit clue/payoff constraints.'}\n"
            f"Relationship and emotional progression:\n{emotion_track_section or 'No explicit relationship/emotion constraints.'}\n"
            f"Antagonist pressure:\n{antagonist_plan_section or 'No explicit antagonist constraints.'}\n"
            f"Chapter/scene contract:\n{contract_section or 'No explicit contract constraints.'}\n"
            f"Narrative tree context:\n{tree_section or 'No narrative tree context.'}\n"
            f"Visible facts for current participants:\n{participant_fact_section or 'No extra participant facts.'}\n"
            f"Retrieved supporting context:\n{retrieval_section or 'No extra retrieval context.'}\n"
            f"{_pp_writer_line}"
            f"{_methodology_line}"
            f"Scene-type guidance: {_scene_type_writing_guidance(scene.scene_type, language=language)}\n"
            "Write the scene in English only. Do not switch to Chinese. "
            "Do not reveal information that belongs to future chapters, and do not contradict established facts or timeline beats. "
            "Prioritize the deterministic path retrieval and narrative-tree constraints when they exist. "
            "The scene must land the core conflict, emotional movement, information release, and closing turn required by the scene contract. "
            "Keep exposition compressed; hide setting inside action, exchange, consequence, and detail.\n"
            "OPENING DIVERSITY: Do NOT open this scene with darkness, pain, waking up, or loss of consciousness. "
            "Choose from: mid-action, dialogue, a sensory detail, an environmental observation, a character's thought about something specific, a question, or an ironic contrast. "
            "Check the recent story recap above — if the previous scene opened with a similar pattern, CHOOSE A DIFFERENT ONE.\n"
            "CONTINUITY: If a character explicitly stated they would NOT do something in a previous scene, do not reverse that decision without showing the reason why. "
            "Every character entrance must be motivated — explain (through action or implication) how they arrived."
        )
    else:
        user_prompt = (
            # R23 — the scene's own hard acceptance contract (word budget +
            # signature obligations) leads the prompt, before every other
            # constraint block.
            f"{_scene_word_budget_line}"
            # Story Integrity whitelists — these MUST come first so the
            # LLM treats them as inviolable constraints, not later
            # afterthoughts. Order: timeline → scene → character → length.
            f"{_timeline_canon_line}"
            f"{_scene_coherence_line}"
            f"{_character_role_line}"
            f"{_chapter_length_line}"
            f"{_prewrite_contract_line}"
            f"{_prewrite_plan_line}"
            f"{_hard_fact_line}"
            f"{_contradiction_line}"
            f"{_query_brief_line}"
            f"{_reader_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_writer_line}"
            f"{_hype_constraints_line}"
            f"{current_scene_contract_line}"
            f"{_qimao_opening_contract_line}"
            f"{_l3_prompt_line}"
            f"{_voice_dna_line}"
            f"{_dialogue_voice_line}"
            f"{_chapter_market_constraints_line}"
            f"{_signature_scene_line}"
            f"{_canon_guardrails_line}"
            f"{_hook_echo_line}"
            f"{_acceptance_duty_line}"
            f"{_exposition_density_line}"
            f"{_scene_beat_line}"
            f"{_prior_persona_feedback_line}"
            f"{_project_material_reference_line}"
            f"{_project_material_obligation_line}"
            f"{_library_reference_line}"
            f"{_plan_richness_line}"
            f"{_identity_line}"
            f"{_scene_scope_isolation_line}"
            f"{_genre_constraint_line}"
            f"{_ranking_profile_line}"
            f"{_progression_context_line}"
            f"{_decision_policy_line}"
            f"{_rule_system_line}"
            f"{_faction_ecology_line}"
            f"{_relationship_agency_line}"
            f"{_entry_system_line}"
            f"{_entry_registry_line}"
            f"{_entry_state_ledger_line}"
            f"{_phrase_avoidance_line}"
            f"{_opening_diversity_line}"
            f"{_budget_diversity_line}"
            f"{_conflict_diversity_line}"
            f"{_scene_purpose_line}"
            f"{_env_diversity_line}"
            f"{_location_ledger_line}"
            f"{_arc_beat_line}"
            f"{_five_layer_line}"
            f"{_tension_target_line}"
            f"{_cliffhanger_line}"
            f"{_knowledge_line}"
            f"{_arc_summary_line}"
            f"{_world_snapshot_line}"
            f"{_structure_beat_line}"
            f"{_scene_sequel_line}"
            f"{_pacing_line}"
            f"{_subplot_line}"
            f"{_ending_line}"
            f"{_reader_knowledge_line}"
            f"{_relationship_line}"
            f"{_obligations_line}"
            f"{_foreshadow_line}"
            f"{story_principle_line}"
            f"项目：《{project.title}》\n"
            f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
            f"章节目标（仅供你理解意图，严禁出现在正文中）：{chapter.chapter_goal}\n"
            + _render_chapter_v2_outline_block(chapter)
            + f"场景定位（仅供参考，不要作为标题输出）：第{scene.scene_number}场 {scene.title or ''}\n"
            f"场景类型：{scene.scene_type}\n"
            f"时间标签：{scene.time_label or '未指定'}\n"
            f"参与者：{participants}\n"
            + _render_scene_v2_outline_block(scene)
            + _scene_target_words_line
            + f"视角：{style_guide.pov_type if style_guide else 'third-limited'}\n"
            f"语气关键词：{tone}\n"
            f"{_pp_line}"
            f"故事圣经约束：\n{story_bible_section or '暂无额外故事圣经约束'}\n"
            f"近期剧情回顾：\n{recent_scene_section or '暂无近期剧情回顾'}\n"
            f"已知时间线节点：\n{recent_timeline_section or '暂无已知时间线节点'}\n"
            f"当前叙事线与节拍：\n{arc_section or '暂无显式叙事线约束'}\n"
            f"伏笔与兑现约束：\n{clue_section or '暂无显式伏笔/兑现约束'}\n"
            f"关系与情绪推进约束：\n{emotion_track_section or '暂无显式关系/情绪线约束'}\n"
            f"反派推进约束：\n{antagonist_plan_section or '暂无显式反派推进约束'}\n"
            f"chapter/scene contract：\n{contract_section or '暂无显式 contract 约束'}\n"
            f"叙事树上下文：\n{tree_section or '暂无叙事树上下文'}\n"
            f"参与角色当前可见事实：\n{participant_fact_section or '暂无额外角色事实'}\n"
            f"检索到的相关上下文：\n{retrieval_section or '暂无额外检索上下文'}\n"
            f"{_pp_writer_line}"
            f"{_methodology_line}"
            f"{_scene_type_writing_guidance(scene.scene_type, language=language)}"
            "不得泄露未来章节才会揭示的信息，不得与当前已知事实和时间线冲突。"
            "优先服从 deterministic path retrieval 与 narrative tree 提供的结构化约束。"
            "必须覆盖 scene contract 的核心冲突、情绪变化、信息释放和结尾牵引。"
            "背景说明必须压缩到最少，优先把设定藏进人物行动、交易、冲突后果和细节里。"
            "不要用空泛抒情、不要先解释世界观、不要写成提纲口吻。\n"
            "【开头多样性】本场景不要以黑暗、痛苦、失去意识或醒来开场。"
            "从以下方式中选择：正在进行的动作、对话、一个感官细节、环境观察、角色对具体事物的想法、一个问题、或一个反差。"
            "对照「近期剧情回顾」，如果前几场用了类似的开头，必须选一个不同的。\n"
            "【连续性】如果角色在前一场明确说了不做某事，不要无理由翻转。"
            "每个角色登场必须有动机——通过动作或暗示说明他/她为何出现在这里。"
        )
    # 黄金三章·开篇硬契约 (ch1-3, zh). Previously this hard contract only lived in
    # the chapter-first generation path (build_chapter_first_draft_prompts), which is
    # gated OFF by default (enable_chapter_first_generation=False) — so the default
    # scene-first writer never received the golden-three opening rules. Wire the
    # (formerly orphaned) directive into the system prompt for ch1-3 so the strongest
    # opening contract reaches the writer regardless of generation mode. The helper
    # self-guards (returns "" for ch>3 / non-zh), and the system prompt is not
    # budget-trimmed, so it always reaches the model.
    _opening_hook_directive = build_opening_hook_directive(
        chapter.chapter_number, language=language
    )
    if _opening_hook_directive:
        system_prompt = f"{system_prompt}\n\n{_opening_hook_directive}"
    return system_prompt, user_prompt


def _render_project_material_obligation_packet(
    project: ProjectModel,
    *,
    chapter_number: int,
    chapter_position: str | None,
    prompt_pack_key: str | None,
    is_en: bool,
) -> str:
    try:
        base_dir = Path(load_settings().output.base_dir)
    except Exception:
        return ""
    project_slug = getattr(project, "slug", None)
    if not project_slug:
        return ""
    project_dir = base_dir / project_slug
    if not project_dir.exists():
        return ""
    try:
        from bestseller.services.material_injection_orchestrator import (
            render_material_injection_blocks,
        )
        from bestseller.services.material_self_repair import plan_material_self_repair

        repair_plan = plan_material_self_repair(
            project_dir,
            chapter_number=chapter_number,
            chapter_position=chapter_position,
            prompt_pack_key=prompt_pack_key,
        )
        obligation_block = render_material_injection_blocks(
            project_dir,
            chapter_number=chapter_number,
            chapter_position=chapter_position,
            prompt_pack_key=prompt_pack_key,
            total_token_budget=3000,
        )
    except Exception:
        logger.debug(
            "Chapter %d: material obligation packet render failed",
            chapter_number,
            exc_info=True,
        )
        return ""
    if not obligation_block and not repair_plan.blocking:
        return ""
    lines = [
        "=== Material lifecycle packet ==="
        if is_en
        else "=== 物料生命周期闭环包（必须优先服从）==="
    ]
    if repair_plan.blocking:
        if is_en:
            lines.append(
                "Material repair is required before inventing new canon. Use only the "
                "listed project canon in prose; missing entities/rules must be expanded "
                "through the material repair loop, not improvised in chapter text."
            )
        else:
            lines.append(
                "检测到物料闭环缺口：正文不得临场发明新正典来遮盖缺口；必须只使用下列已给定正典。"
                "缺失人物/规则/线索应进入物料自修复流程后再生成。"
            )
        for action in repair_plan.actions[:8]:
            lines.append(f"- {action.action_type}: {action.target} ({action.reason})")
    if obligation_block:
        lines.append(obligation_block)
    return "\n".join(lines).strip()


def _packet_story_bible_context(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    return packet.story_bible if packet is not None else None


def _packet_recent_scene_summaries(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.recent_scene_summaries]


def _packet_recent_timeline_events(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.recent_timeline_events]


def _packet_participant_canon_facts(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.participant_canon_facts]


def _packet_active_plot_arcs(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_plot_arcs]


def _packet_active_arc_beats(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_arc_beats]


def _packet_unresolved_clues(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.unresolved_clues]


def _packet_planned_payoffs(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.planned_payoffs]


def _packet_chapter_contract(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.chapter_contract is None:
        return None
    return packet.chapter_contract.model_dump(mode="json")


def _packet_scene_contract(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.scene_contract is None:
        return None
    return packet.scene_contract.model_dump(mode="json")


def _packet_tree_context(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.tree_context_nodes]


def _packet_retrieval_context(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.retrieval_chunks]


def _packet_emotion_tracks(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_emotion_tracks]


def _packet_antagonist_plans(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_antagonist_plans]


def _packet_hard_fact_snapshot(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.hard_fact_snapshot is None:
        return None
    return packet.hard_fact_snapshot.model_dump(mode="json")


def _has_leading_chapter_heading(content_md: str, chapter_number: int) -> bool:
    """Return True if ``content_md`` already begins with a canonical chapter heading.

    Tolerates leading blank lines and matches both Chinese ("# 第N章…") and
    English ("# Chapter N…") headings. Used to skip a second heading prepend
    on the disk-sync path when ``render_chapter_draft_markdown`` has already
    added one.
    """
    if not content_md:
        return False
    first_line = ""
    for line in content_md.splitlines():
        if line.strip():
            first_line = line.strip()
            break
    if not first_line:
        return False
    return bool(
        re.match(rf"^#{{1,4}}\s*第\s*{chapter_number}\s*章\b", first_line)
        or re.match(rf"^#{{1,4}}\s*Chapter\s+{chapter_number}\b", first_line, re.IGNORECASE)
    )


def format_chapter_heading(
    chapter_number: int,
    raw_title: str | None,
    *,
    language: str | None = None,
) -> str:
    """Build a single ``# 第N章：子标题`` heading without double-prefixing.

    ``chapter.title`` in older data can look like any of:

    - ``"零点前的抢购"``             → ``# 第1章：零点前的抢购``
    - ``"第1章：零点前的抢购"``      → ``# 第1章：零点前的抢购``
    - ``"第1章 零点前的抢购"``        → ``# 第1章：零点前的抢购``
    - ``"第1章"``                    → ``# 第1章``
    - ``None`` / ``""``              → ``# 第1章``

    Previously the renderer unconditionally prepended ``# 第N章 {title}`` and
    produced ``# 第1章 第1章：零点前的抢购``. This helper strips any existing
    ``第N章`` prefix (with optional whitespace / colon) before re-attaching a
    single canonical prefix.
    """
    is_en = is_english_language(language)
    chapter_prefix = f"Chapter {chapter_number}" if is_en else f"第{chapter_number}章"
    title = (raw_title or "").strip()
    if not title:
        return f"# {chapter_prefix}"
    # Strip any existing "第N章" prefix (with optional separator) to avoid
    # double-prefixing. Tolerate both the exact chapter number and generic
    # leading "第\d+章" forms so earlier data with stale numbering still works.
    stripped = re.sub(r"^第\s*\d+\s*章\s*[：:\-\s]*", "", title).strip()
    stripped = re.sub(r"^Chapter\s*\d+\s*[:\-\s]*", "", stripped, flags=re.IGNORECASE).strip()
    if not stripped:
        return f"# {chapter_prefix}"
    separator = ": " if is_en else "："
    return f"# {chapter_prefix}{separator}{stripped}"


def render_chapter_draft_markdown(
    chapter: ChapterModel,
    scene_drafts: list[SceneDraftVersionModel],
    *,
    language: str | None = None,
) -> str:
    header = [format_chapter_heading(chapter.chapter_number, chapter.title, language=language)]
    scene_sections = [
        strip_scaffolding_echoes(
            sanitize_novel_markdown_content(scene_draft.content_md, language=language)
        )
        for scene_draft in scene_drafts
    ]
    # Strip leading chapter/scene headings from each scene section.
    # render_chapter_draft_markdown already prepends the canonical chapter
    # heading, so any "# 第N章 ..." or "# 第N章 第M场 ..." line at the top
    # of a scene section is always a leak from the LLM writer.
    _scene_heading_re = re.compile(
        r"^\s*#{1,4}\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*[章场]|Chapter\s+\d+)",
        re.IGNORECASE,
    )
    # Also strip bare subtitle headings (e.g. "# 雾锁探针") that match the
    # chapter title — the LLM sometimes outputs the subtitle alone as a
    # heading, duplicating the canonical "# 第N章：雾锁探针" heading above.
    _raw_title = (chapter.title or "").strip()
    _stripped_title = re.sub(r"^第\s*\d+\s*章\s*[：:\-\s]*", "", _raw_title).strip()
    cleaned_sections: list[str] = []
    for section in scene_sections:
        lines = section.split("\n")
        # Strip leading blank lines, then check the first content line.
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and _scene_heading_re.match(lines[0].strip()):
            lines.pop(0)
        # Strip bare subtitle heading that duplicates the chapter title
        elif lines and _stripped_title:
            _first = lines[0].strip()
            if _first.startswith("#") and _stripped_title in _first:
                # Check it's just a heading with the subtitle, not prose
                _heading_text = re.sub(r"^#{1,4}\s*", "", _first).strip()
                if _heading_text == _stripped_title:
                    lines.pop(0)
        cleaned_sections.append("\n".join(lines).strip())
    scene_sections = cleaned_sections
    # Drop any scene section that collapsed to an empty string after sanitizing
    # (e.g. when the section was 100% meta-commentary leakage) so the final
    # chapter does not contain stray blank "<!-- fallback -->" placeholders or
    # double blank lines.
    scene_sections = [section for section in scene_sections if section.strip()]
    if not scene_sections:
        raise ValueError(
            f"Chapter {chapter.chapter_number} has no scene content after sanitization. "
            f"All {len(scene_drafts)} scene drafts were empty or contained only "
            f"fallback placeholders. The LLM writer failed for every scene. "
            f"Check: 1) API key is set (MINIMAX_API_KEY / ANTHROPIC_API_KEY), "
            f"2) model name is valid, 3) network connectivity to the LLM provider."
        )
    return "\n\n".join(header + scene_sections).strip()


def _compact_json_block(value: Any, *, max_chars: int = 5000) -> str:
    if value is None:
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...（已截断，仅保留高优先级约束）"


def _compact_text_block(value: Any, *, max_chars: int = 1200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...（已截断，仅保留高优先级约束）"


def _merge_auto_repair_hint(
    existing: str | None,
    new_hint: str | None,
    *,
    max_chars: int = 2400,
) -> str:
    """Merge repair hints without accumulating repeated copies forever."""

    fragments: list[str] = []
    seen: set[str] = set()
    for raw in (existing or "", new_hint or ""):
        for line in str(raw).splitlines():
            item = line.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            fragments.append(item)
    merged = "\n".join(fragments).strip()
    if len(merged) <= max_chars:
        return merged

    kept: list[str] = []
    total = 0
    for item in reversed(fragments):
        next_len = len(item) + (1 if kept else 0)
        if total + next_len > max_chars:
            break
        kept.append(item)
        total += next_len
    kept.reverse()
    return "【系统已压缩历史修复提示，仅保留最新高优先级约束】\n" + "\n".join(kept)


def _render_compact_constraint_blocks(blocks: Sequence[str]) -> str:
    rendered: list[str] = []
    total = 0
    for block in blocks:
        compact = _compact_text_block(block, max_chars=1000)
        if not compact:
            continue
        if total + len(compact) > 6500:
            rendered.append("...（硬约束已截断，保留前序高优先级门禁）")
            break
        rendered.append(compact)
        total += len(compact)
    return "\n\n".join(rendered)


def _chapter_context_list(items: Sequence[Any], *, max_items: int = 8) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in items[:max_items]:
        if hasattr(item, "model_dump"):
            compacted.append(item.model_dump(mode="json"))
        elif isinstance(item, Mapping):
            compacted.append(dict(item))
        else:
            compacted.append({"value": str(item)})
    return compacted


def _render_chapter_first_prior_chapter_bridge(
    context_packet: ChapterWriterContextPacket,
) -> str:
    chapter_number = int(getattr(context_packet, "chapter_number", 0) or 0)
    if chapter_number <= 1:
        return ""
    summaries = _chapter_context_list(
        getattr(context_packet, "previous_scene_summaries", None) or [],
        max_items=2,
    )
    hard_snapshot: dict[str, Any] | None = None
    snapshot = getattr(context_packet, "hard_fact_snapshot", None)
    if snapshot is not None:
        if hasattr(snapshot, "model_dump"):
            dumped = snapshot.model_dump(mode="json")
            hard_snapshot = dict(dumped) if isinstance(dumped, Mapping) else None
        elif isinstance(snapshot, Mapping):
            hard_snapshot = dict(snapshot)

    if not summaries and not hard_snapshot:
        return ""

    lines = [
        "【上一章硬承接（最高优先级）】",
        "本章必须从以下已发生事实继续；这些事实优先于本章旧细纲、检索摘要和模型联想。",
        "不得把上一章已发生事件改写成普通失踪、未发生、传闻或角色误会；"
        "如果本章需要使用“失踪/不见”等模糊词，必须写成“上一章已被镜面带走后生死未明/只剩物证”。",
    ]

    for index, summary in enumerate(summaries, start=1):
        if not isinstance(summary, Mapping):
            continue
        tail = str(
            summary.get("extended_tail")
            or summary.get("closing_lines")
            or summary.get("summary")
            or ""
        ).strip()
        if not tail:
            continue
        lines.append(f"- 上一章尾段证据{index}：{_compact_text_block(tail, max_chars=950)}")

    facts = list((hard_snapshot or {}).get("facts") or [])
    if facts:
        priority_terms = (
            "钥匙",
            "铁片",
            "照片",
            "信",
            "血",
            "失踪",
            "消失",
            "死",
        )

        def _fact_priority(raw: Any) -> int:
            text = json.dumps(raw, ensure_ascii=False, default=str) if isinstance(raw, Mapping) else str(raw)
            return 0 if any(term in text for term in priority_terms) else 1

        selected = sorted(facts, key=_fact_priority)[:10]
        lines.append("- 上一章硬事实：")
        for fact in selected:
            if not isinstance(fact, Mapping):
                continue
            name = str(fact.get("name") or "").strip()
            subject = str(fact.get("subject") or "").strip()
            value = str(fact.get("value") or "").strip()
            notes = str(fact.get("notes") or "").strip()
            quote = str(fact.get("source_quote") or "").strip()
            pieces = [piece for piece in (name, subject, value, notes) if piece]
            line = "；".join(pieces)
            if quote:
                line += f"；原文证据：{quote}"
            if line:
                lines.append(f"  - {_compact_text_block(line, max_chars=260)}")

    return "\n".join(lines)


def _render_chapter_first_scene_cards(scenes: Sequence[SceneCardModel]) -> str:
    lines: list[str] = []
    previous_exit_state: dict[str, Any] | None = None
    for scene in scenes:
        purpose = scene.purpose or {}
        entry_state = scene.entry_state or {}
        exit_state = scene.exit_state or {}
        _metadata, methodology_contract, current_controls = _scene_current_contract_controls(
            scene
        )
        forbidden_actions = _prompt_safe_forbidden_actions(
            getattr(scene, "forbidden_actions", None) or []
        )
        transition_contract = {
            "time_label": getattr(scene, "time_label", None),
            "entry_state": entry_state,
            "exit_state": exit_state,
            "bridge_from_previous": (
                {
                    "previous_exit_state": previous_exit_state,
                    "requirement": (
                        "Use one visible transition sentence before this scene: "
                        "physical movement, call handoff, door/elevator action, "
                        "time tick, or object reaction. Do not jump locations with "
                        "a horizontal rule or blank cut."
                    ),
                }
                if previous_exit_state
                else {
                    "requirement": (
                        "Start from this scene's entry state. Do not invent an "
                        "extra pre-scene location unless it is the current entry location."
                    )
                }
            ),
        }
        rich_scene_controls = {
            "methodology_contract": methodology_contract,
            "gate_function": current_controls.get("gate_function"),
            "visible_progress": current_controls.get("visible_progress"),
            "reader_payoff": current_controls.get("reader_payoff"),
            "ending_hook_payload": current_controls.get("ending_hook_payload"),
            "transition_contract": transition_contract,
            "signature_image": current_controls.get("signature_image"),
            "cut_point": current_controls.get("cut_point"),
            "action_sequence": current_controls.get("action_sequence"),
            "relationship_debts": current_controls.get("relationship_debts"),
            "information_control_mode": current_controls.get("information_control_mode"),
            "key_dialogue_beats": getattr(scene, "key_dialogue_beats", None) or [],
            "sensory_anchors": getattr(scene, "sensory_anchors", None) or {},
            "forbidden_actions": forbidden_actions,
        }
        scene_lines = [
            f"{scene.scene_number}. {scene.title or '未命名场景'}"
            f"（{scene.scene_type}，目标约{scene.target_word_count or 0}字）",
            (
                "   字数边界："
                f"{max(1, int((scene.target_word_count or 0) * 0.9))}-"
                f"{max(2, int((scene.target_word_count or 0) * 1.1))}字；"
                "本场只写本场任务，达成离场状态后立刻转入下一场。"
            ),
            f"   时间/地点锚点：{scene.time_label or '未指定'}",
            f"   参与者：{', '.join(scene.participants or []) or '未指定'}",
            f"   故事任务：{purpose.get('story') or ''}",
            f"   情绪任务：{purpose.get('emotion') or ''}",
            f"   入场状态：{_compact_json_block(entry_state, max_chars=500)}",
            f"   离场状态：{_compact_json_block(exit_state, max_chars=500)}",
            f"   钩子要求：{scene.hook_requirement or ''}",
        ]
        if forbidden_actions:
            scene_lines.append(
                f"   硬禁令：{_compact_json_block(forbidden_actions, max_chars=900)}"
            )
        scene_lines.extend(
            [
                f"   场景执行合同：{_compact_json_block(rich_scene_controls, max_chars=1800)}",
                f"   改写提示：{getattr(scene, 'rewrite_hint', '') or ''}",
            ]
        )
        lines.append("\n".join(scene_lines))
        previous_exit_state = dict(exit_state)
    return "\n\n".join(lines).strip()


def _render_chapter_v2_outline_block(chapter: Any) -> str:
    """Render outline-v2 executable script fields from chapter.metadata_json.

    Returns an empty string when no v2 fields are present (backward-compat
    with old outlines that were generated before the v2 schema).
    """
    meta = dict(getattr(chapter, "metadata_json", None) or {})
    parts: list[str] = []
    protagonist_inner_state = str(meta.get("protagonist_inner_state") or "").strip()
    if protagonist_inner_state:
        parts.append(f"主角内心状态（本章开头的驱动力）：{protagonist_inner_state}\n")
    chapter_concrete_actions = meta.get("chapter_concrete_actions") or []
    if chapter_concrete_actions:
        parts.append(
            f"本章具体动作脚本：{json.dumps(chapter_concrete_actions, ensure_ascii=False)}\n"
        )
    chapter_object_uses = meta.get("chapter_object_uses") or []
    if chapter_object_uses:
        parts.append(
            f"物件使用脚本：{json.dumps(chapter_object_uses, ensure_ascii=False)}\n"
        )
    chapter_information_introduced = meta.get("chapter_information_introduced") or []
    if chapter_information_introduced:
        parts.append(
            f"本章须传达给读者的信息：{json.dumps(chapter_information_introduced, ensure_ascii=False)}\n"
        )
    chapter_information_held_back = meta.get("chapter_information_held_back") or []
    if chapter_information_held_back:
        parts.append(
            f"本章故意不告诉读者的信息（悬念留白）：{json.dumps(chapter_information_held_back, ensure_ascii=False)}\n"
        )
    return "".join(parts)


def _render_scene_v2_outline_block(scene: Any) -> str:
    """Render outline-v2 executable script fields from scene.metadata_json.

    Returns an empty string when no v2 fields are present.
    """
    meta = dict(getattr(scene, "metadata_json", None) or {})
    parts: list[str] = []
    concrete_goal = str(meta.get("concrete_goal") or "").strip()
    if concrete_goal:
        parts.append(f"场景具体目标：{concrete_goal}\n")
    protagonist_state = str(meta.get("protagonist_state") or "").strip()
    if protagonist_state:
        parts.append(f"主角本场状态：{protagonist_state}\n")
    information_introduced = meta.get("information_introduced") or []
    if information_introduced:
        parts.append(
            f"本场须传达给读者的信息：{json.dumps(information_introduced, ensure_ascii=False)}\n"
        )
    information_held_back = meta.get("information_held_back") or []
    if information_held_back:
        parts.append(
            f"本场刻意留白信息：{json.dumps(information_held_back, ensure_ascii=False)}\n"
        )
    object_signal = str(meta.get("object_signal") or "").strip()
    if object_signal:
        parts.append(f"物件超自然信号：{object_signal}\n")
    return "".join(parts)


def _render_chapter_first_opening_contract(
    chapter: ChapterModel,
    scenes: Sequence[SceneCardModel],
) -> str:
    if not scenes or int(getattr(chapter, "chapter_number", 0) or 0) > 10:
        return ""
    first_scene = scenes[0]
    # NOTE (2026-05-26 architecture cleanup): the "mediated_terms" hardcoded
    # ban (phone/text/voice) was removed.  Mediated openings are a quality
    # consideration handled by the LLM judge, not a deterministic source
    # rule.  The opening-scene contract still anchors first paragraph to the
    # chapter's planned opening_situation and first scene state.
    lines = [
        "【开场场景指导】",
        f"第一段建议从这里开写：{getattr(chapter, 'opening_situation', '') or ''}",
        f"第一场入场状态：{_compact_json_block(getattr(first_scene, 'entry_state', None) or {}, max_chars=360)}",
        f"第一场钩子：{getattr(first_scene, 'hook_requirement', '') or ''}",
        "前200字应当出现第一场的地点/人物/异常，避免先写无关回忆或资料整理。",
    ]
    return "\n".join(line for line in lines if line.strip())


def _render_front_chapter_forbidden_terms_block(
    chapter: ChapterModel,
    project: ProjectModel | None = None,
) -> str:
    if int(getattr(chapter, "chapter_number", 0) or 0) > 10:
        return ""
    unique_forbidden_terms = _front10_forbidden_signal_terms(chapter, project=project)
    lines = [
        "【前十章禁写与物件信号硬约束】",
        "本章正文只能使用本章场景卡里的现场名词、人物和物件；任何卷级真相、家族本名、"
        "幕后身份、名单编号、镜局专名、第几面镜都不要提前定义。",
        "允许电话/短信作为同一 POV 内的现实沟通工具，但不得切镜头到电话另一头；"
        "不得引入快递员、配送员等额外活人 NPC 推动第一章主线。",
        "物件异常必须写成有稳定含义的可见变化，例如变冷、变重、裂缺、血点、影子错位、指针偏移；"
        "允许短暂温热，但不得把铜钱长时间高温或发烫写成万能推进器。",
        "前十章不得上规则课：主角只能用问话顺序、物证变化和人物动作让读者推理；"
        "普通邻居、客户、警察不得主动理解认账/入账/镜债/账线等专业词。",
    ]
    if unique_forbidden_terms:
        lines.append(
            f"系统门禁已登记 {len(unique_forbidden_terms)} 个精确禁写字符串；"
            "正文不要复述禁写清单，也不要用“不是/没有/并不”否定式提及禁写内容。"
        )
    return "\n".join(lines)


def _prompt_safe_forbidden_actions(actions: Sequence[Any]) -> list[str]:
    """Keep scene prohibitions useful without priming the writer with exact leaks."""

    safe_actions: list[str] = []
    for action in actions:
        text = str(action or "").strip()
        if not text:
            continue
        replacement: list[str] = []
        if any(
            term in text
            for term in (
                "电话",
                "来电",
                "手机",
                "微信",
                "短信",
                "语音",
                "录音",
                "寄件",
                "快递",
                "外卖",
                "配送",
                "物流",
                "跑腿",
            )
        ):
            replacement.append("不得用通讯、物流、配送、寄送、外卖或跑腿桥段引入人物、物证或转场。")
        if any(
            term in text
            for term in (
                "林正淳",
                "林远山",
                "林家辉",
                "困魂镜",
                "祖父",
                "爷爷",
                "第七面",
                "第八个",
                "第三十七号",
                "第三十八号",
                "扣账人",
                "母镜",
                "源门",
                "归人",
                "入门",
                "代父",
                "张家门契",
                "三代以内",
                "血债血偿",
                "七行名单",
                "八个人影",
            )
        ):
            replacement.append("不得提前写父辈姓名、祖辈姓名、林家旁支、镜局专名、名单编号或第几面镜等长线信息。")
        if any(term in text for term in ("发烫", "滚烫", "炭火", "烫得", "账页烫", "铜钱烫")):
            replacement.append("不得把铜钱、青囊纸面、罗盘等物件异常写成发热系反应。")
        if replacement:
            safe_actions.extend(replacement)
            continue
        safe_actions.append(text)
    return _ordered_unique_texts(safe_actions)


def _redact_front10_prompt_leaks(
    text: str,
    chapter: ChapterModel,
    scenes: Sequence[SceneCardModel] | None = None,
) -> str:
    """Remove exact front-10 leak tokens from context blocks before prompting."""

    if not text or int(getattr(chapter, "chapter_number", 0) or 0) > 10:
        return text
    redacted = text
    # NOTE (2026-05-26 architecture cleanup): previously appended ~15
    # hardcoded communication / delivery terms (phone/express/etc.) to redact
    # them from the writer's context window — effectively SABOTAGING the
    # writer's ability to use legitimate openings.  Removed.  Metadata-driven
    # forbidden_signals + scene-card forbidden_content_terms still apply
    # (those are user-controllable).
    prompt_leaks = list(_front10_forbidden_signal_terms(chapter))
    if scenes:
        prompt_leaks.extend(_front10_scene_forbidden_content_terms(scenes))
    for term in sorted(_ordered_unique_texts(prompt_leaks), key=len, reverse=True):
        if not term:
            continue
        if any(
            marker in term
            for marker in ("电话", "来电", "手机", "微信", "短信", "语音", "录音", "寄件", "快递", "外卖", "配送", "物流", "跑腿")
        ):
            placeholder = "【禁用通联转送桥段】"
        elif any(marker in term for marker in ("发烫", "发热", "烫", "滚烫", "炭火", "高温", "灼热")):
            placeholder = "【物件触感捷径】"
        else:
            placeholder = "【暂缓长线信息】"
        redacted = redacted.replace(term, placeholder)
    return redacted


# NOTE (2026-05-26 architecture cleanup):
# This list previously hardcoded user-feedback keywords (phone/text/etc.) as
# hard-block forbidden terms for golden-three chapter openings.  That pattern
# treated user feedback as immutable engine rules and prevented the writer
# from using legitimate openings (e.g. v21 successfully opened with a phone
# call).  The semantic intent — "don't lean on weak mediated openings" — is
# now expressed in ``chapter_llm_quality_judge`` system_prompt as an audit
# dimension, not a deterministic block.  Leave the tuple empty so existing
# call sites become no-ops without import churn.
_FRONT10_MEDIATED_OPENING_TERMS: tuple[str, ...] = ()


def _front10_forbidden_signal_terms(
    chapter: ChapterModel,
    *,
    project: ProjectModel | None = None,
) -> list[str]:
    """Return forbidden signal terms ONLY from chapter metadata (user-configurable).

    NOTE (2026-05-26 architecture cleanup): the function previously appended a
    hardcoded list of 21 heat-sensation terms (发烫/发热/滚烫/铜钱烫/etc.) baked
    into source code.  That list froze user feedback into engine rules and
    blocked legitimate uses (e.g. v21's "《青囊》残卷在发烫" — the canonical
    signature image of this novel).  Source-level keyword bans are removed;
    semantic intent ("don't lean on heat-sensation shortcuts in golden three")
    is now an audit dimension inside ``chapter_llm_quality_judge``.  Metadata-
    driven lists (object_signal_contract.forbidden_signals and
    foreshadowing_actions.forbidden_early_leaks) remain — those are
    user-controllable per chapter, not engine policy.
    """
    metadata = getattr(chapter, "metadata_json", None) or {}
    object_signal = metadata.get("object_signal_contract") if isinstance(metadata, Mapping) else {}
    foreshadowing = getattr(chapter, "foreshadowing_actions", None) or {}
    forbidden_terms: list[str] = []
    if project is not None:
        try:
            from pathlib import Path

            from bestseller.services.forbidden_leaks_loader import (
                load_forbidden_leaks_for_chapter,
            )

            decision = load_forbidden_leaks_for_chapter(
                Path("output") / str(project.slug),
                int(getattr(chapter, "chapter_number", 0) or 0),
            )
            forbidden_terms.extend(decision.forbidden_terms)
        except Exception:
            logger.debug("forbidden leaks policy load failed", exc_info=True)
    if isinstance(object_signal, Mapping):
        forbidden_terms.extend(str(item) for item in object_signal.get("forbidden_signals") or [])
    if isinstance(foreshadowing, Mapping):
        forbidden_terms.extend(
            str(item) for item in foreshadowing.get("forbidden_early_leaks") or []
        )
    return _ordered_unique_texts(forbidden_terms)


# NOTE (2026-05-26 architecture cleanup): previously contained 17 hardcoded
# heat-sensation terms used to flag "object signal shortcut" in chapters 1-10.
# Removed: this was source-level user-feedback freezing; v21 successfully used
# 发烫 as the novel's signature image.  Semantic check moved to LLM judge.
_FRONT10_GENERIC_HEAT_SIGNAL_TERMS: frozenset[str] = frozenset()

_FRONT10_OBJECT_SIGNAL_SUBJECTS: tuple[str, ...] = (
    "铜钱",
    "青囊",
    "账页",
    "罗盘",
    "掌心旧伤",
    "掌心的旧伤",
    "手心旧伤",
)


def _front10_forbidden_signal_hits(chapter: ChapterModel, content: str) -> list[str]:
    hits: list[str] = []
    text = content or ""
    for term in _front10_forbidden_signal_terms(chapter):
        if not term:
            continue
        if term in _FRONT10_GENERIC_HEAT_SIGNAL_TERMS:
            if _front10_generic_heat_signal_hit(term, text):
                hits.append(term)
            continue
        if term in text:
            hits.append(term)
    return _ordered_unique_texts(hits)


def _front10_generic_heat_signal_hit(term: str, content: str) -> bool:
    for match in re.finditer(re.escape(term), content or ""):
        window = content[max(0, match.start() - 18) : match.end() + 18]
        if any(subject in window for subject in _FRONT10_OBJECT_SIGNAL_SUBJECTS):
            return True
    return False


def _front10_scene_forbidden_content_terms(
    scenes: Sequence[SceneCardModel],
) -> list[str]:
    """Return front-chapter prose terms implied by scene-card prohibitions."""

    candidates = (
        "电话",
        "来电",
        "手机",
        "手机通知",
        "微信",
        "短信",
        "语音",
        "录音",
        "寄件",
        "快递",
        "外卖",
        "配送",
        "配送单",
        "物流",
        "跑腿",
        "林正淳",
        "林远山",
        "林家辉",
        "票据",
        "单子",
        "半夜等单",
        "送夜宵",
        "接配送单",
        "送个单",
        "帮忙寄件",
        "门吞掉",
        "被门吞掉",
        "被镜子吞掉",
        "拖进门",
        "门合拢",
        "确认死亡",
        "下楼",
        "坐电梯",
        "离场",
        "回店",
        "电梯脚印",
        "黑泥鞋印",
        "水渍脚印",
        "新脚",
        "湿纸条按在",
        "七号入账",
        "代父",
        "入门",
        "归人",
        "张家门契",
        "三代以内",
        "血债血偿",
        "八个人影",
        "七行名单",
        "病号服",
    )
    terms: list[str] = []
    for scene in scenes:
        for action in getattr(scene, "forbidden_actions", None) or []:
            text = str(action or "")
            terms.extend(term for term in candidates if term in text)
    return _ordered_unique_texts(terms)


def _front10_rule_lecture_terms(content: str) -> list[str]:
    window = content or ""
    phrase_hits = [
        term
        for term in (
            "认动作",
            "认因果",
            "只认动作",
            "账本找的是最近的人",
            "账本找",
            "镜债递刀子",
            "先认动作",
            "再认因果",
        )
        if term in window
    ]
    hard_rule_terms = ("认账", "入账", "替认", "镜债", "账线")
    density = sum(window.count(term) for term in hard_rule_terms)
    if density >= 5:
        phrase_hits.append(f"规则术语密度={density}")
    return _ordered_unique_texts(phrase_hits)


def _front10_contract_violations_for_content(
    chapter: ChapterModel,
    scenes: Sequence[SceneCardModel],
    content: str,
) -> tuple[Violation, ...]:
    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    if chapter_number > 10:
        return tuple()
    violations: list[Violation] = []
    first_scene = scenes[0] if scenes else None
    first_surface = " ".join(
        str(value or "")
        for value in (
            getattr(chapter, "opening_situation", None),
            getattr(first_scene, "title", None) if first_scene is not None else None,
            getattr(first_scene, "hook_requirement", None) if first_scene is not None else None,
            (
                (getattr(first_scene, "purpose", None) or {}).get("story")
                if first_scene is not None
                else None
            ),
            (
                (getattr(first_scene, "entry_state", None) or {}).get("state")
                if first_scene is not None
                else None
            ),
        )
    )
    first_window = (content or "")[:500]
    if not any(term in first_surface for term in _FRONT10_MEDIATED_OPENING_TERMS):
        drift_terms = [term for term in _FRONT10_MEDIATED_OPENING_TERMS if term in first_window]
        if drift_terms:
            violations.append(
                Violation(
                    code="OPENING_SCENE_DRIFT",
                    severity="block",
                    location="chapter.opening",
                    detail=(
                        "正文前500字新增了章节开篇合同未规划的媒介桥段："
                        + "、".join(drift_terms)
                    ),
                    prompt_feedback=(
                        "不要把电话、来电、手机、微信、短信、语音、录音等媒介当成突兀的入场捷径；"
                        "如果确实使用媒介，必须先把来源、转交人、可信原因和到场动机写进章节开篇合同。"
                        "否则第一段应直接落到第一场现场、人物和异常。"
                    ),
                )
            )
    forbidden_hits = _front10_forbidden_signal_hits(chapter, content or "")
    if forbidden_hits:
        violations.append(
            Violation(
                code="FRONT10_FORBIDDEN_SIGNAL",
                severity="block",
                location="chapter.object_signal",
                detail=(
                    "前十章正文使用了禁用词、早泄长线或物件/感官捷径："
                    + "、".join(_ordered_unique_texts(forbidden_hits)[:8])
                ),
                prompt_feedback=(
                    "删除这些禁用词和过早泄露信息；物件异常只能改写成稳定且可推理的可见变化，"
                    "例如变冷、变重、裂缺、血点、影子错位或指针偏移；长线名词延后到指定章节。"
                ),
            )
        )
    scene_forbidden_hits = [
        term for term in _front10_scene_forbidden_content_terms(scenes) if term and term in content
    ]
    if scene_forbidden_hits:
        violations.append(
            Violation(
                code="FRONT10_SCENE_FORBIDDEN_ACTION",
                severity="block",
                location="chapter.scene_contract",
                detail=(
                    "正文写入了场景卡明确禁写的前提/动作："
                    + "、".join(_ordered_unique_texts(scene_forbidden_hits)[:10])
                ),
                prompt_feedback=(
                    "删除场景卡禁写内容；普通邻居/客户不得被写成快递、外卖、配送、跑腿等身份，"
                    "也不得引出场景卡声明暂缓的人物、术语或重复高潮动作。"
                ),
            )
        )
    rule_lecture_hits = _front10_rule_lecture_terms(content)
    if rule_lecture_hits:
        violations.append(
            Violation(
                code="FRONT10_RULE_LECTURE_DENSITY",
                severity="block",
                location="chapter.rule_delivery",
                detail=(
                    "前十章规则解释过密，降低读者代入和神秘感："
                    + "、".join(rule_lecture_hits[:8])
                ),
                prompt_feedback=(
                    "删除规则课式解释；主角只能通过问话顺序、物证反应和人物动作让读者推理，"
                    "不得直接讲完整规则，不得让普通人主动理解认账/入账/镜债/账线。"
                ),
            )
        )
    return tuple(violations)


def _ordered_unique_texts(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _chapter_length_contract_band(
    project: ProjectModel,
    target_word_count: int | None,
) -> tuple[int, int, int]:
    """Return the prose-visible chapter word contract shared by write/repair."""

    settings = load_settings()
    policy = word_target_policy(settings)
    hard_min = int(policy.chapter_min)
    hard_max = int(policy.chapter_max)
    if not is_english_language(getattr(project, "language", None)):
        hard_min = max(hard_min, CHINESE_CHAPTER_HARD_MIN_WORDS)
        hard_max = max(hard_min, min(hard_max, CHINESE_CHAPTER_HARD_MAX_WORDS))
    try:
        target = int(target_word_count or 0)
    except (TypeError, ValueError):
        target = 0
    if target <= 0:
        target = int(policy.chapter_target)
    target = max(hard_min, min(target, hard_max))
    return hard_min, target, hard_max


def _chapter_auto_repair_length_contract(
    project: ProjectModel,
    chapter: ChapterModel,
) -> str:
    hard_min, hard_target, hard_max = _chapter_length_contract_band(
        project,
        int(getattr(chapter, "target_word_count", 0) or 0),
    )
    return (
        "【自动修复字数总契约】本次仍然是完整章节正文，不是补丁说明；"
        f"最终正文必须保持在 {hard_min}-{hard_max} 个汉字，目标约 {hard_target} 字。"
        "修复时间线、重复、卷级对齐或尾钩时，不得新增无关场景、人物、地点、阵营或设定解释；"
        "优先通过压缩重复句、合并解释、补入必要动作/证物/对白来解决问题。"
        "如果信息量装不下，删解释和术语，保留冲突、选择、代价和章末钩子。"
    )


async def _enrich_chapter_first_context(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    context_packet: ChapterWriterContextPacket,
) -> None:
    """Attach the high-value guardrail blocks that scene drafting already uses."""

    language = getattr(project, "language", None) or settings.generation.language
    try:
        from bestseller.services.canon_guardrails import render_canon_guardrails_block

        guard = load_canon_guardrails_for_project(project, output_base_dir=settings.output.base_dir)
        if not guard.is_empty:
            context_packet.canon_guardrails_block = (
                render_canon_guardrails_block(
                    guard,
                    chapter_number=chapter.chapter_number,
                    language=language,
                )
                or None
            )
    except Exception:
        logger.debug("chapter-first canon guardrails injection failed", exc_info=True)

    bible_root = Path(settings.output.base_dir) / project.slug / "story-bible"
    try:
        from bestseller.services.timeline_consistency_gate import (
            load_timeline_canon,
            render_timeline_canon_block,
        )

        canon = load_timeline_canon(bible_root / "timeline-canon.md")
        if canon is not None:
            context_packet.timeline_canon_block = (
                render_timeline_canon_block(canon, language=language) or None
            )
    except Exception:
        logger.debug("chapter-first timeline canon injection failed", exc_info=True)
    try:
        from bestseller.services.scene_coherence_gate import render_scene_coherence_block

        context_packet.scene_coherence_block = render_scene_coherence_block(language=language) or None
    except Exception:
        logger.debug("chapter-first scene coherence injection failed", exc_info=True)
    try:
        from bestseller.services.character_role_gate import (
            load_character_profiles,
            render_character_role_block,
        )
        from bestseller.services.dialogue_voice_blocks import render_dialogue_voice_block

        profiles = load_character_profiles(bible_root / "cast-and-promises.md")
        if profiles:
            context_packet.character_role_block = (
                render_character_role_block(profiles, language=language) or None
            )
            voice_profiles = tuple(
                profile.dialogue_voice
                for profile in profiles
                if profile.dialogue_voice is not None
            )
            if voice_profiles:
                context_packet.dialogue_voice_block = (
                    render_dialogue_voice_block(voice_profiles, language=language) or None
                )
    except Exception:
        logger.debug("chapter-first character role injection failed", exc_info=True)
    try:
        from bestseller.services.chapter_length_gate import render_chapter_length_block

        # Pass the project's resolved chapter band (config + per-chapter target,
        # clamped to the 1800-3500 zh ceiling) instead of the generic defaults so
        # the writer is told this book's exact, type-aware hard cap.
        _band_min, _band_target, _band_max = _chapter_length_contract_band(
            project,
            int(getattr(chapter, "target_word_count", 0) or 0),
        )
        context_packet.chapter_length_block = (
            render_chapter_length_block(
                hard_floor=_band_min,
                soft_warning=_band_target,
                hard_max=_band_max,
                language=language,
            )
            or None
        )
    except Exception:
        logger.debug("chapter-first length block injection failed", exc_info=True)
    try:
        _orig_cfg = get_quality_gates_config().originality_engine
        if _orig_cfg.enabled:
            from bestseller.services.chapter_orchestrator import prepare_chapter_context
            from bestseller.services.exposition_density_gate import (
                check_exposition_density,
                render_exposition_density_block,
            )
            from bestseller.services.market_constraint_compiler import (
                render_chapter_constraints_block,
            )
            from bestseller.services.reader_persona_simulator import render_persona_feedback_block
            from bestseller.services.signature_scene_planner import render_signature_scene_block
            from bestseller.services.voice_signature import render_voice_dna_block

            previous_chapters = await _collect_previous_current_chapter_texts(
                session,
                project=project,
                chapter_number=chapter.chapter_number,
            )
            prev_text = previous_chapters[-1][1] if previous_chapters else None
            mode_b = bool(_orig_cfg.mode_b_override) if _orig_cfg.mode_b_override is not None else False
            orig_ctx = prepare_chapter_context(
                project.slug,
                chapter.chapter_number,
                output_base_dir=settings.output.base_dir,
                mode_b=mode_b,
                prev_chapter_text=prev_text,
                # Same book-derived hook vocabulary as the validation side.
                hook_domain_tokens=_bundle_hook_domain_tokens(project),
            )
            if orig_ctx.voice_dna is not None:
                context_packet.voice_dna_block = (
                    render_voice_dna_block(orig_ctx.voice_dna, language=language) or None
                )
            if orig_ctx.market_constraints is not None:
                context_packet.chapter_market_constraints_block = (
                    render_chapter_constraints_block(
                        orig_ctx.market_constraints,
                        language=language,
                    )
                    or None
                )
            if orig_ctx.signature_scene_mandate is not None:
                context_packet.signature_scene_block = (
                    render_signature_scene_block(
                        orig_ctx.signature_scene_mandate,
                        language=language,
                    )
                    or None
                )
            if orig_ctx.prior_persona_feedback is not None:
                context_packet.prior_persona_feedback_block = (
                    render_persona_feedback_block(
                        orig_ctx.prior_persona_feedback,
                        language=language,
                    )
                    or None
                )
            if orig_ctx.hook_echo_report is not None:
                context_packet.hook_echo_block = orig_ctx.hook_echo_block(language=language) or None
            context_packet.exposition_density_block = (
                render_exposition_density_block(
                    check_exposition_density("", chapter_position=chapter.chapter_number),
                    language=language,
                )
                or None
            )
    except Exception:
        logger.debug("chapter-first originality block injection failed", exc_info=True)


async def _render_chapter_first_character_safety_block(
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    scenes: Sequence[SceneCardModel],
) -> str:
    """Render future-death guardrails for characters active in this chapter."""

    participant_names: set[str] = set()
    for scene in scenes:
        for name in getattr(scene, "participants", None) or ():
            text = str(name or "").strip()
            if text:
                participant_names.add(text)
    if not participant_names:
        return ""

    rows = await session.execute(
        select(CharacterModel.name, CharacterModel.death_chapter_number).where(
            CharacterModel.project_id == project.id,
            CharacterModel.name.in_(participant_names),
            CharacterModel.death_chapter_number.is_not(None),
            CharacterModel.death_chapter_number > chapter.chapter_number,
        )
    )
    protected = [
        (str(name).strip(), int(death_chapter))
        for name, death_chapter in rows
        if name and death_chapter is not None
    ]
    if not protected:
        return ""

    lines = [
        "以下角色在当前章节只是可以受伤、失踪、被困、濒危或留下生死悬念，不能被正文确认死亡："
    ]
    lines.extend(f"- {name}：计划死亡/退场章为第{death_chapter}章之后；本章禁止写成已死。" for name, death_chapter in protected)
    lines.append(
        "禁止使用“死了、死亡、尸体、遗体、断气、没命、临终”等确认死亡表述指向上述角色；"
        "包括疑问句、传闻句和旁人推测式表达，例如“已经死了，对吧？”“是不是死了？”也禁止。"
        "如果需要强钩子，改写为“被拖入镜中后生死不明、声音断掉、只留下物件、下一章需确认”。"
    )
    return "\n".join(lines)


def build_chapter_first_draft_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scenes: Sequence[SceneCardModel],
    style_guide: StyleGuideModel | None,
    context_packet: ChapterWriterContextPacket,
    *,
    target_word_count: int,
    character_safety_block: str | None = None,
    context_budget_tokens: int = 6000,
) -> tuple[str, str]:
    language = _project_language(project)
    is_en = is_english_language(language)
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    writing_profile_section = render_writing_profile_prompt_block(
        writing_profile,
        language=language,
        mode="scene",
        chapter_number=int(getattr(chapter, "chapter_number", 0) or 0),
    )
    serial_guardrails = render_serial_fiction_guardrails(writing_profile, language=language)
    _genre_label = getattr(writing_profile.market, "platform_target", None) or "商业长篇连载"
    if is_en:
        system_prompt = (
            "# ROLE\n"
            "You are a senior commercial fiction writer who delivers one complete chapter in a single pass.\n"
            f"You write for {_genre_label} and your chapters are judged on whether readers click 'next'.\n"
            "\n"
            "# CONTEXT · The chapter-first contract\n"
            "Scene cards are INTERNAL beat constraints, not visible headings.\n"
            "You must thread all scenes into ONE continuous narrative — no '第一场 / 第二场' labels,\n"
            "no scene dividers, no internal scaffolding leaks.\n"
            "\n"
            "# CONTEXT · Craft anchors\n"
            "- Action over adjectives; consequence over description; subtext over statement.\n"
            "- Each paragraph ends on an unresolved question.\n"
            "- Continuity, causal logic, character voice, hook strength — all four must hold.\n"
            "\n"
            "# TASK\n"
            "Write the FULL chapter as one continuous Markdown prose draft.\n"
            "Output prose only: no outlines, commentary, scene labels, planning notes.\n"
            "\n"
            "# CONSTRAINTS · Hard (violation → rewrite)\n"
            "- Stay within the requested word count band — do not pad to explain worldbuilding.\n"
            "- No scene labels in body text.\n"
            "- No internal scaffolding leaks (entry_state / exit_state / contract / scene_type tags).\n"
            "- Use EXACT character names from the participants list.\n"
            "\n"
            "# OUTPUT · Chapter-opening hard indicators\n"
            "- First 100 chars MUST deliver a felt pressure / anomaly to the reader.\n"
            "- First 300 chars MUST show one human flaw in the protagonist (NOT cold rule-following).\n"
            "- Terminology release: only what THIS chapter must reveal; don't pile lore.\n"
            "- Ending: converge to ONE concrete, visualisable hook (not abstract emotion).\n"
            "\n"
            "# PROJECT PROFILE\n"
            f"## Writing profile\n{writing_profile_section}\n\n"
            f"## Serial fiction guardrails\n{serial_guardrails}\n"
        )
        instruction = (
            "Write the full chapter in ONE continuous Markdown prose draft.\n"
            "Honour all CONSTRAINTS in the system prompt and meet the chapter-opening hard indicators.\n"
            "Scene cards are beat constraints, not visible structure."
        )
    else:
        system_prompt = (
            "# ROLE\n"
            "你是一位写过多本签约长篇的中文网文写手，**专攻整章一次成稿**——不靠场景拼接，靠连续叙事。\n"
            f"你写的章节服务 {_genre_label} 的留存场景，判定标准是读者会不会下意识点开「下一章」。\n"
            "\n"
            "# CONTEXT · 整章合同（chapter-first 模式）\n"
            "场景卡只是**内部节拍约束**，不是可见结构。\n"
            "你必须把所有场景揉进一段连续叙事，正文中不许出现「第一场 / 第二场 / 场景 X」等标签。\n"
            "也不许写「内部说明 / 写法注释 / 场景目的」——这些是策划信息，不进正文。\n"
            "\n"
            "# CONTEXT · 创作锚点（已内化）\n"
            "- 用动作代替形容词；用后果代替描述；用潜台词代替直白\n"
            "- 每段结尾留一个未解问题——读者必须翻下一页\n"
            "- 连贯性 / 因果逻辑 / 人物腔调 / 钩子强度——四项必须同时达标\n"
            "\n"
            "# TASK\n"
            "基于章节计划和场景卡，一次性写完完整一章（Markdown 正文）。\n"
            "**只输出正文**：不要提纲、不要评语、不要场景标签、不要策划说明。\n"
            "\n"
            "# CONSTRAINTS · 硬约束（违反即重写）\n"
            "- 字数：严格贴近目标字数，不许为了解释设定扩写。\n"
            "- 场景标签：正文不能出现「第一场 / 第二场 / 场景 X / 转场」字样。\n"
            "- 策划泄漏：不许出现 entry_state / exit_state / contract / scene_type 等英文结构标签。\n"
            "- 角色名：与「参与者」列表完全一致，不许改名 / 别名 / 缩写。\n"
            "- 输出格式：纯 Markdown 正文，不带 # 标题、不带 ``` 代码块。\n"
            "\n"
            "# OUTPUT · 章节开篇硬指标\n"
            "- **前 100 字**：必须给读者可感知的压力或异常（视觉 / 听觉 / 物件异常）。\n"
            "- **前 300 字**：必须让主角表现出**一个可代入的人性破绽**——不能只是冷静执行规则。\n"
            "- **术语释放**：本章必须信息按场景卡释放即可，不要堆设定。\n"
            "- **章末钩子**：只收束到**一个**具体、可视化、能促使读者翻下一章的钩子（不是抽象感叹）。\n"
            "\n"
            "# EXAMPLES · AI 套话黑名单（绝对禁止）\n"
            "- 「血液仿佛凝固了」/「时间仿佛静止了」/「空气仿佛凝固了」\n"
            "- 「心中五味杂陈」/「眼眶不由得湿润了」\n"
            "- 「一股莫名的情绪」/「一阵莫名的恐惧」\n"
            "- 章末「这一切才刚刚开始」/「真正的答案还在等待揭开」\n"
            "\n"
            "# PROJECT PROFILE（项目级变量）\n"
            f"## 写作风格\n{writing_profile_section}\n\n"
            f"## 连载护栏\n{serial_guardrails}\n"
        )
        instruction = (
            "请一次性写完整章节。\n"
            "场景卡是内部节拍约束，正文不能出现「第一场 / 第二场 / 场景」等标签。\n"
            "严格执行 system 中的章节开篇硬指标和硬约束。"
        )

    raw_chapter_contract = getattr(context_packet, "chapter_contract", None)
    if raw_chapter_contract is None:
        chapter_contract = None
    elif hasattr(raw_chapter_contract, "model_dump"):
        chapter_contract = raw_chapter_contract.model_dump(mode="json")
    elif isinstance(raw_chapter_contract, Mapping):
        chapter_contract = dict(raw_chapter_contract)
    else:
        chapter_contract = {"value": str(raw_chapter_contract)}
    raw_hard_snapshot = getattr(context_packet, "hard_fact_snapshot", None)
    if raw_hard_snapshot is None:
        hard_snapshot = None
    elif hasattr(raw_hard_snapshot, "model_dump"):
        hard_snapshot = raw_hard_snapshot.model_dump(mode="json")
    elif isinstance(raw_hard_snapshot, Mapping):
        hard_snapshot = dict(raw_hard_snapshot)
    else:
        hard_snapshot = {"value": str(raw_hard_snapshot)}
    generation_input_block = ""
    acceptance_contract_block = ""
    try:
        from bestseller.services.chapter_generation_input_builder import (
            build_chapter_generation_input_bundle,
        )

        generation_input_bundle = build_chapter_generation_input_bundle(
            project=project,
            chapter=chapter,
            scenes=scenes,
            context_packet=context_packet,
            target_word_count=target_word_count,
        )
        generation_input_block = _compact_json_block(
            generation_input_bundle.model_dump(mode="json"),
            max_chars=4500,
        )
        acceptance_contract_block = _compact_json_block(
            generation_input_bundle.acceptance_contract,
            max_chars=2500,
        )
    except Exception:
        logger.debug("chapter generation input bundle render failed", exc_info=True)
    generation_input_block = _redact_front10_prompt_leaks(
        generation_input_block,
        chapter,
        scenes,
    )
    acceptance_contract_block = _redact_front10_prompt_leaks(acceptance_contract_block, chapter, scenes)
    project_meta = getattr(project, "metadata_json", None)
    project_meta = project_meta if isinstance(project_meta, Mapping) else {}
    concept_lab_contract_block = render_concept_lab_prompt_block(project_meta, language=language)
    knowledge_boundary_block = _render_knowledge_state_section(
        getattr(context_packet, "participant_knowledge_states", None),
        is_en=is_en,
    )
    constraint_blocks = [
        block
        for block in (
            context_packet.chapter_length_block,
            context_packet.timeline_canon_block,
            context_packet.character_role_block,
            context_packet.dialogue_voice_block,
            context_packet.scene_coherence_block,
            context_packet.canon_guardrails_block,
            context_packet.reader_contract_block,
            context_packet.hype_constraints_block,
            context_packet.hook_echo_block,
            context_packet.exposition_density_block,
            context_packet.voice_dna_block,
            context_packet.chapter_market_constraints_block,
            context_packet.signature_scene_block,
            context_packet.prior_persona_feedback_block,
        )
        if block
    ]
    opening_retention_rules = ""
    if chapter.chapter_number <= 3:
        opening_retention_rules = (
            "【黄金三章硬规则】\n"
            "1. 第一句话必须直接给出异常、威胁、倒计时、死亡证据或不可解释事件，禁止从整理物品、回忆、解释职业开始。\n"
            "2. 前300字只允许释放1-2个核心设定名词，必须按“异常 -> 主角选择 -> 代价/危险升级”推进。\n"
            "3. 主角必须出现一个可代入的人性破绽：怕、穷、迟疑、误判、心软、愧疚或被父辈阴影击中，不能全程像规则机器。\n"
            "4. 每章至少交付一个具体爽点：识破、反杀、救人、夺回主动权、规则反用或证据翻转。\n"
            "5. 章末钩子必须是新的可视化危险或证据，不能只用抽象设定句收尾；"
            "最后一句必须落在完成画面帧、人物动作、物件变化或明确选择点。\n"
            "6. 句法节奏：禁止“一拍一段”的分镜腔——单句独段只在真要顿挫时用，连续≤2段、"
            "全章占叙述段<1/4；多数动作/心理拍点要并进“动作+反应+环境”的叙述段。"
            "反例：把“他坐起来。”“数字跳了一格。”“他愣了一拍。”并回上下文段落，长短句交错。"
        )
    elif chapter.chapter_number <= 10:
        opening_retention_rules = (
            "【前十章留存硬规则】\n"
            "1. 开头200字必须承接上一章钩子并立刻升级，不得重新铺垫。\n"
            "2. 本章必须有一个新证据、一次主动选择、一个具体代价或一次规则反用。\n"
            "3. 对话必须区分人物腔调，禁止所有人都用冷短句。\n"
            "4. 章末必须留下具体物件、动作、声音、画面或选择压力，且最后一句必须仍在现场内。"
        )
    contract_must_hit_block = ""
    if isinstance(chapter_contract, Mapping):
        must_hit_items = [
            ("章节摘要", chapter_contract.get("contract_summary")),
            ("核心冲突", chapter_contract.get("core_conflict")),
            ("信息释放", chapter_contract.get("information_release")),
            ("章末钩子", chapter_contract.get("closing_hook")),
        ]
        visible_items = [
            f"- {label}：{str(value).strip()}"
            for label, value in must_hit_items
            if str(value or "").strip()
        ]
        causal_items = _render_character_causal_contract_block(
            chapter_contract,
            language=language,
        )
        if causal_items:
            visible_items.append(causal_items)
        if visible_items:
            contract_must_hit_block = (
                "【必须显性兑现的章节契约】\n"
                + "\n".join(visible_items)
                + "\n这些不是参考资料，而是正文必须让读者直接看见的交付项。"
                "其中出现的人名、物件、规则、危险和章末钩子不得省略、替换或只做隐晦暗示；"
                "如果与场景卡有冲突，优先满足章节契约，并用一个自然动作或一句对白补入缺失信息。"
                "最后200字必须显性兑现“章末钩子”；如果钩子里有未在场景卡参与者出现的人名，"
                "必须通过门外声音、监控提示或现场物件自然引入，不得省略。"
            )
    must_keep_tail_blocks = _render_chapter_first_must_keep_tail_blocks(
        chapter_contract,
        language=language,
    )
    volume_seed_block = ""
    if chapter.chapter_number <= 3 and not (
        isinstance(getattr(chapter, "metadata_json", None), Mapping)
        and getattr(chapter, "metadata_json", {}).get("framework_regeneration_candidate")
    ):
        seed_payload = {
            "active_plot_arcs": _chapter_context_list(
                context_packet.active_plot_arcs,
                max_items=3,
            ),
            "unresolved_clues": _chapter_context_list(
                context_packet.unresolved_clues,
                max_items=4,
            ),
            "planned_payoffs": _chapter_context_list(
                context_packet.planned_payoffs,
                max_items=4,
            ),
        }
        compact_seed = _compact_json_block(seed_payload, max_chars=2200)
        if compact_seed and compact_seed != "{}":
            volume_seed_block = (
                "【卷级首章埋钩硬约束】\n"
                "从以下卷级主线/伏笔中选择1-2个最贴合本章场景的元素，必须在正文中以物件、账文、"
                "人物反应或短对白可见落地；不要堆术语，不要把卷高潮提前讲透。"
                "章末只能聚焦一个主钩子，其他卷级元素只能在中段轻轻落点，禁止在最后300字连续抛出多个悬念。\n"
                + compact_seed
            )
    hard_min_words, hard_target_words, hard_max_words = _chapter_length_contract_band(
        project,
        target_word_count,
    )
    scene_count = len(scenes)
    total_scene_target = sum(int(scene.target_word_count or 0) for scene in scenes)
    scene_targets = [int(scene.target_word_count or hard_target_words) for scene in scenes] or [
        hard_target_words
    ]
    per_scene_min = max(1, min(int(target * 0.8) for target in scene_targets))
    per_scene_max = max(2, max(int(target * 1.15) for target in scene_targets))
    output_rules = (
        "只输出小说正文 Markdown。可以保留一个章节标题；不要输出“分析/计划/说明/门禁/改写策略”。"
        f"正文必须连贯，篇幅硬范围是 {hard_min_words}-{hard_max_words} 个汉字，"
        f"目标约{hard_target_words}字；写到章末钩子落地后立刻停止，禁止超过上限；"
        f"字数是硬交付，不是建议：正文少于 {hard_min_words} 个汉字就是失败，"
        "没有写满下限前不得提前收束、不得只写剧情摘要。"
        f"本章一共只有 {scene_count} 个场景，场景目标合计约 {total_scene_target or hard_target_words} 字，"
        f"不是每个场景各写一章；单场通常控制在 {per_scene_min}-{per_scene_max} 字内，"
        "全文建议22-32段，最多36段；每场5-8段为主，至少4段正在发生的戏，最多9段；"
        "单段通常45-95字。"
        "不得把场景卡压缩成一句概述；每个场景必须写出现场空间、角色动作、可见物证变化、"
        "人物反应和至少一轮有辨识度的对话/追问。"
        "任何一场到第8段还没完成离场状态，必须用1段收束并进入下一场。"
        "到最后一个场景的尾钩落成后必须停止，不得继续补新的循环段落。"
        "严格按场景卡的单场字数边界分配篇幅：每场只完成本场任务，不得把一个场景扩写成整章体量；"
        "每场达成离场状态后，用一句可见转场进入下一场。"
        "场景卡的入场状态、离场状态和 forbidden_actions 是硬边界；不得把“失声/回声/半账未解”"
        "升级成“被拖进门、被吞掉、确认死亡、门合拢”等未写在场景卡的高潮动作；"
        "未写在场景卡、章节契约、角色安全块或故事圣经里的死亡、吞人、门关闭、额外活人 NPC 等关键事件一律禁止；"
        "电话/短信只能作为同一视角内的现实沟通工具，不得用来切走 POV 或凭空送入线索。"
        f"如果模型准备写超过42段或超过{hard_max_words}字，必须优先删解释、删重复氛围、删二次推理，"
        "不能继续扩写。"
        "不得出现模板化重复句式，不得把同一恐惧/门禁/铜钱动作反复写成同一模式。"
        "非专业角色只能描述自己亲眼看见的异常、听来的警告或身体反应；除非角色认知状态明确写明，"
        "否则不得让普通客户、快递员、送餐员、邻居、警察主动说出或理解认账、入账、替认、镜债、账线等专业规则词。"
        "叙述者也不要替普通角色贴规则标签：不要写“某角色继续否认/某角色被卷入”这类直接贴规则标签的句子，"
        "应改写成普通语言，如“他咬死说没进过门”“她手腕多了半圈黑线”。"
        "如果需要让非专业角色说出规则词，必须写成被附身、被镜中声音逼迫复述、或主角刚刚当场解释后的结果。"
        "正文不得使用 ---、***、空行切场、场景标题或小节分隔符。"
        "每次更换地点或时间，必须先写一句可见转场动作，例如出门、下楼、电梯、电话挂断、"
        "门牌变化、时间跳动或物件反应；禁止从一个地点直接跳到另一个地点。"
        "章末最后120字必须满足“钩子+落地帧”：可以抛出新危险或新信息，但最后一句必须是"
        "现场内可看见的完成画面、人物动作、物件变化或选择点；如果钩子是对白，必须在对白后"
        "再加一句动作/画面作为最后帧，禁止让最后一句只是一句台词或正在进行的动作。"
        "章末只能保留一个主钩子，最多一个辅助信息，不得连续堆叠电梯、短信、门、水、电话、"
        "新人物等多个未解悬念；选择一个最服务下一章的钩子并让其落成完成画面。"
        "不得临时发明未在场景卡、角色池、章节契约或故事圣经中出现的人名；功能性人物只用"
        "司机、邻居、保安、摊主、送货员等身份称谓。"
        "如果角色安全块要求某角色本章不能确认死亡，连疑问句、传闻句和旁人推测式“已经死了，对吧？”"
        "也不能写，只能写成失踪、被困、生死未明或还不能确认。"
        "如果信息量装不下，优先删解释和术语，保留动作、冲突、人物选择和章末钩子。"
    )
    opening_scene_contract = _render_chapter_first_opening_contract(chapter, scenes)
    prior_chapter_bridge = _render_chapter_first_prior_chapter_bridge(context_packet)
    front_forbidden_terms_block = _render_front_chapter_forbidden_terms_block(
        chapter,
        project,
    )
    quality_uplift_blocks = _quality_uplift_prompt_blocks_from_chapter(chapter)
    story_bible_block = _redact_front10_prompt_leaks(
        _compact_json_block(context_packet.story_bible, max_chars=3200),
        chapter,
        scenes,
    )
    activity_context_block = _redact_front10_prompt_leaks(
        _compact_json_block(
            {
                "active_plot_arcs": _chapter_context_list(
                    context_packet.active_plot_arcs,
                    max_items=5,
                ),
                "active_arc_beats": _chapter_context_list(
                    context_packet.active_arc_beats,
                    max_items=5,
                ),
                "unresolved_clues": _chapter_context_list(
                    context_packet.unresolved_clues,
                    max_items=5,
                ),
                "planned_payoffs": _chapter_context_list(
                    context_packet.planned_payoffs,
                    max_items=5,
                ),
            },
            max_chars=3000,
        ),
        chapter,
        scenes,
    )
    timeline_context_block = _redact_front10_prompt_leaks(
        _compact_json_block(
            {
                "recent_timeline_events": _chapter_context_list(
                    context_packet.recent_timeline_events,
                ),
                "hard_fact_snapshot": hard_snapshot,
            },
            max_chars=2800,
        ),
        chapter,
        scenes,
    )
    retrieval_context_block = _redact_front10_prompt_leaks(
        _compact_json_block(
            _chapter_context_list(context_packet.retrieval_chunks, max_items=4),
            max_chars=1800,
        ),
        chapter,
        scenes,
    )
    user_prompt = "\n\n".join(
        section
        for section in [
            "【任务】\n" + instruction,
            prior_chapter_bridge,
            (
                "【章节目标】\n"
                f"作品：{project.title}\n"
                f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
                f"目标字数：约{hard_target_words}字，必须完整成章；发布硬范围 {hard_min_words}-{hard_max_words} 字\n"
                f"章节目标：{chapter.chapter_goal or ''}"
            ),
            quality_uplift_blocks.get("pre_scene", ""),
            "【场景卡节拍】\n" + _render_chapter_first_scene_cards(scenes),
            quality_uplift_blocks.get("post_scene", ""),
            "【统一生成输入包】\n" + generation_input_block
            if generation_input_block
            else "",
            (
                "【写前验收契约】\n"
                + acceptance_contract_block
                + "\n写作前必须在内部逐项核对本契约；正文必须能被这些条款验收通过。"
                "不要输出核对过程，只输出小说正文。"
            )
            if acceptance_contract_block
            else "",
            "【角色认知边界】\n" + knowledge_boundary_block
            if knowledge_boundary_block
            else "",
            "【硬约束与门禁】\n" + _render_compact_constraint_blocks(constraint_blocks)
            if constraint_blocks
            else "",
            "【角色生死与登场安全】\n" + character_safety_block
            if character_safety_block
            else "",
            opening_scene_contract,
            front_forbidden_terms_block,
            opening_retention_rules,
            concept_lab_contract_block,
            contract_must_hit_block,
            volume_seed_block,
            "【章节契约】\n" + _compact_json_block(chapter_contract, max_chars=3500)
            if chapter_contract
            else "",
            "【故事圣经上下文】\n" + story_bible_block,
            "【近期章节/场景摘要】\n"
            + _compact_json_block(
                _chapter_context_list(context_packet.previous_scene_summaries, max_items=5),
                max_chars=1800,
            ),
            "【活动主线/伏笔/回收】\n" + activity_context_block,
            "【时间线与硬事实快照】\n" + timeline_context_block,
            "【检索补充】\n" + retrieval_context_block,
            "【输出要求】\n" + output_rules,
            *must_keep_tail_blocks,
        ]
        if section
    )
    system_prompt = _redact_front10_prompt_leaks(system_prompt, chapter, scenes)
    user_prompt = _redact_front10_prompt_leaks(user_prompt, chapter, scenes)
    # Token-aware soft trim — chapter-first mode builds a single large
    # user_prompt without per-section budget tracking.  When the assembled
    # prompt exceeds the configured budget (rough CJK: 3.5 chars/token, EN
    # ~0.75 token/word → use 3.0 chars/token as a conservative midpoint), we
    # drop trailing low-priority sections and append a marker.  This avoids
    # the LLM silently truncating the tail (which used to evict the chapter
    # closing hook or the methodology evidence).
    char_budget = max(2000, int(context_budget_tokens) * 3)
    if len(user_prompt) > char_budget:
        user_prompt = _soft_trim_user_prompt(
            user_prompt,
            char_budget=char_budget,
            language=language,
        )
    return system_prompt, user_prompt


# Markers that identify "must-keep" tail blocks in the chapter-first user
# prompt.  The chapter-first assembler appends these sections last; the
# tier-aware trim must protect them by anchoring the cut to the last
# boundary *before* the first must-keep section.
_MUST_KEEP_TAIL_MARKERS_ZH: tuple[str, ...] = (
    "【方法论证据】",
    "【章末收尾钩子】",
    "【收尾钩子】",
)
_MUST_KEEP_TAIL_MARKERS_EN: tuple[str, ...] = (
    "[methodology evidence]",
    "[chapter closing hook]",
    "[closing hook]",
)


def _soft_trim_user_prompt(
    user_prompt: str,
    *,
    char_budget: int,
    language: str,
) -> str:
    """Tier-aware trim for the chapter-first user prompt.

    Unlike the previous head-only truncation, this:

    1. Identifies the first "must-keep" tail section by scanning known
       markers (methodology evidence / chapter closing hook).  The cut
       is anchored to the boundary *before* that section so the closing
       hook and evidence block always survive.
    2. Falls back to head-only truncation when no must-keep marker is
       present.
    3. If the protected tail alone exceeds ``char_budget``, **preserves
       the protected tail verbatim** and trims only the head — better to
       drop early scaffolding than to lose the closing hook.

    For real per-section budget enforcement, callers should use
    :func:`_budget_context_sections` on a structured ``ctx`` dict.
    This trim is a safety net for the chapter-first path which builds
    a single flat ``user_prompt`` string.
    """
    if len(user_prompt) <= char_budget:
        return user_prompt

    markers = (
        _MUST_KEEP_TAIL_MARKERS_EN
        if is_english_language(language)
        else _MUST_KEEP_TAIL_MARKERS_ZH
    )

    # Find the earliest must-keep marker; the cut goes just before it so
    # the marker (and everything after) is preserved.
    first_protected_idx = -1
    for marker in markers:
        idx = user_prompt.find(marker)
        if idx >= 0 and (first_protected_idx < 0 or idx < first_protected_idx):
            first_protected_idx = idx

    if first_protected_idx < 0:
        # No protected section; trim from the head (legacy behaviour).
        cut_at = char_budget
    else:
        # Keep the protected tail regardless of where the marker appears.
        # The prior implementation only used this reverse strategy when the
        # marker started after the budget; if the marker started inside the
        # budget, it still cut exactly before the marker and dropped the
        # must-keep block.  Always trim the head to make room for the tail.
        protected_part = user_prompt[first_protected_idx:]
        head_budget = max(0, char_budget - (len(user_prompt) - first_protected_idx))
        head_part = user_prompt[: min(first_protected_idx, head_budget)]
        dropped_chars = first_protected_idx - len(head_part)
        if is_english_language(language):
            marker = (
                f"\n\n[…prompt head trimmed by {dropped_chars} chars to fit "
                f"context_budget_tokens={char_budget // 3}; protected tail "
                f"({len(protected_part)} chars) preserved…]\n"
            )
        else:
            marker = (
                f"\n\n[…开头已截断 {dropped_chars} 字以适配 "
                f"context_budget_tokens={char_budget // 3}；尾部必保区"
                f"（{len(protected_part)} 字）已保留…]\n"
            )
        return head_part + marker + protected_part

    # Normal: trim the tail past the protected boundary (or past char_budget
    # when there's no protected section).
    dropped_chars = len(user_prompt) - cut_at
    trimmed = user_prompt[:cut_at]
    if is_english_language(language):
        marker = (
            f"\n\n[…prompt trimmed by {dropped_chars} chars to fit "
            f"context_budget_tokens={char_budget // 3}; protected tail "
            f"preserved…]\n"
        )
    else:
        marker = (
            f"\n\n[…已截断 {dropped_chars} 字以适配 context_budget_tokens="
            f"{char_budget // 3}；尾部必保区已保留…]\n"
        )
    return trimmed + marker


def _render_chapter_first_must_keep_tail_blocks(
    chapter_contract: Mapping[str, Any] | None,
    *,
    language: str,
) -> list[str]:
    if not isinstance(chapter_contract, Mapping):
        return []
    is_en = is_english_language(language)
    blocks: list[str] = []

    closing_hook = str(chapter_contract.get("closing_hook") or "").strip()
    if closing_hook:
        if is_en:
            blocks.append(
                "[chapter closing hook]\n"
                f"- Must land visibly in the final 200 words: {closing_hook}\n"
                "- Preserve this block when trimming context; it is a hard acceptance item."
            )
        else:
            blocks.append(
                "【章末收尾钩子】\n"
                f"- 最后200字必须可视化落地：{closing_hook}\n"
                "- 这是裁剪时必须保留的验收项，不是可省略参考资料。"
            )

    declared_payoffs = [
        str(item).strip()
        for item in (chapter_contract.get("methodology_declared_payoffs") or [])
        if str(item).strip()
    ]
    evidence_paths = [
        item
        for item in (chapter_contract.get("payoff_evidence_paths") or [])
        if isinstance(item, Mapping)
    ]
    hooks_to_resolve = [
        str(item).strip()
        for item in (chapter_contract.get("hooks_to_resolve") or [])
        if str(item).strip()
    ]
    hooks_to_plant = [
        str(item).strip()
        for item in (chapter_contract.get("hooks_to_plant") or [])
        if str(item).strip()
    ]
    if declared_payoffs or evidence_paths or hooks_to_resolve or hooks_to_plant:
        lines = ["[methodology evidence]" if is_en else "【方法论证据】"]
        if declared_payoffs:
            label = "Declared payoffs" if is_en else "本章声明兑现"
            lines.append(f"- {label}: {'; '.join(declared_payoffs)}")
        if evidence_paths:
            label = "Evidence paths" if is_en else "证据落点"
            rendered_paths = []
            for item in evidence_paths[:5]:
                scene_no = item.get("scene_number") or item.get("scene")
                evidence = item.get("evidence") or item.get("path") or item.get("description")
                rendered_paths.append(
                    f"scene {scene_no}: {evidence}" if scene_no else str(evidence or item)
                )
            lines.append(f"- {label}: {'; '.join(rendered_paths)}")
        if hooks_to_resolve:
            label = "Hooks to resolve" if is_en else "本章应消解钩子"
            lines.append(f"- {label}: {'; '.join(hooks_to_resolve)}")
        if hooks_to_plant:
            label = "Hooks to plant" if is_en else "本章应植入钩子"
            lines.append(f"- {label}: {'; '.join(hooks_to_plant)}")
        lines.append(
            "- Preserve this block when trimming context; it is a hard acceptance item."
            if is_en
            else "- 这是裁剪时必须保留的验收项，不是可省略参考资料。"
        )
        blocks.append("\n".join(lines))
    return blocks


def _quality_uplift_prompt_blocks_from_chapter(chapter: ChapterModel) -> dict[str, str]:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    blocks = metadata.get("quality_uplift_prompt_blocks")
    if not isinstance(blocks, Mapping):
        return {}
    return {
        "pre_scene": str(blocks.get("pre_scene") or "").strip(),
        "post_scene": str(blocks.get("post_scene") or "").strip(),
    }


async def _prepare_quality_uplift_prompt_blocks(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    scenes: Sequence[SceneCardModel],
) -> None:
    pre_blocks: list[str] = []
    post_blocks: list[str] = []
    callback_payload: list[dict[str, Any]] = []
    try:
        from bestseller.services.cross_chapter_ngram_tracker import (
            compute_ngram_overuse,
            render_ngram_avoidance_block,
        )

        ngram_report = await compute_ngram_overuse(
            session,
            project,
            chapter_number_upto=int(chapter.chapter_number or 0),
        )
        block = render_ngram_avoidance_block(ngram_report, language=project.language)
        if block:
            pre_blocks.append(block)
    except Exception:
        logger.debug("quality uplift ngram block failed", exc_info=True)
    try:
        from bestseller.services.character_idiolect_tracker import (
            compute_character_idiolect,
            render_idiolect_avoidance_block,
        )

        participant_names = _chapter_first_participant_names(scenes)
        profiles = [
            await compute_character_idiolect(
                session,
                project,
                name,
                chapter_number_upto=int(chapter.chapter_number or 0),
            )
            for name in participant_names[:2]
        ]
        block = render_idiolect_avoidance_block(profiles, language=project.language)
        if block:
            pre_blocks.append(block)
    except Exception:
        logger.debug("quality uplift idiolect block failed", exc_info=True)
    try:
        from bestseller.services.arc_tension_monitor import (
            compute_arc_tension,
            render_arc_tension_block,
        )

        report = await compute_arc_tension(
            session,
            project,
            chapter_number_upto=int(chapter.chapter_number or 0),
        )
        block = render_arc_tension_block(
            report,
            chapter_number=int(chapter.chapter_number or 0),
            language=project.language,
        )
        if block:
            pre_blocks.append(block)
    except Exception:
        logger.debug("quality uplift arc tension block failed", exc_info=True)
    try:
        from bestseller.services.chapter_callback_obligations import (
            collect_callback_obligations,
            render_callback_block,
        )

        obligations = await collect_callback_obligations(
            session,
            project,
            int(chapter.chapter_number or 0),
        )
        callback_payload = [item.to_dict() for item in obligations]
        block = render_callback_block(obligations, language=project.language)
        if block:
            post_blocks.append(block)
    except Exception:
        logger.debug("quality uplift callback block failed", exc_info=True)

    if not pre_blocks and not post_blocks and not callback_payload:
        return
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    metadata["quality_uplift_prompt_blocks"] = {
        "pre_scene": "\n\n".join(pre_blocks),
        "post_scene": "\n\n".join(post_blocks),
    }
    if callback_payload:
        metadata["callback_obligations"] = callback_payload
    chapter.metadata_json = metadata


def _chapter_first_participant_names(scenes: Sequence[SceneCardModel]) -> list[str]:
    names: list[str] = []
    for scene in scenes:
        participants = getattr(scene, "participants", None) or []
        for raw in participants:
            name = str(raw or "").strip()
            if name and name not in names:
                names.append(name)
    return names


async def generate_chapter_draft_once(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    *,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    context_packet: ChapterWriterContextPacket | None = None,
) -> ChapterDraftVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")
    previous_chapter_status = str(getattr(chapter, "status", "") or "")
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards.")

    effective_settings = settings or load_settings()
    style_guide = await session.get(StyleGuideModel, project.id)
    if context_packet is None:
        context_packet = await build_chapter_writer_context(
            session,
            effective_settings,
            project_slug,
            chapter_number,
        )
    await _enrich_chapter_first_context(session, effective_settings, project, chapter, context_packet)
    character_safety_block = await _render_chapter_first_character_safety_block(
        session,
        project,
        chapter,
        scenes,
    )

    target_word_count = int(
        chapter.target_word_count
        or effective_settings.generation.words_per_chapter.target
        or 2500
    )
    await _prepare_quality_uplift_prompt_blocks(
        session,
        project=project,
        chapter=chapter,
        scenes=scenes,
    )
    system_prompt, user_prompt = build_chapter_first_draft_prompts(
        project,
        chapter,
        scenes,
        style_guide,
        context_packet,
        target_word_count=target_word_count,
        character_safety_block=character_safety_block,
        context_budget_tokens=int(
            getattr(
                getattr(effective_settings, "generation", None),
                "context_budget_tokens",
                6000,
            )
        ),
    )
    try:
        from bestseller.services.prompt_compactor import compact_user_prompt

        user_prompt, compaction_report = compact_user_prompt(
            user_prompt,
            chapter_no=int(chapter.chapter_number or 0),
            forbidden_terms_full=_front10_forbidden_signal_terms(chapter, project=project),
        )
    except Exception:
        compaction_report = None
        logger.debug("chapter-first prompt compaction failed", exc_info=True)
    fallback_content = "\n\n".join(
        [
            format_chapter_heading(
                chapter.chapter_number,
                chapter.title,
                language=_project_language(project),
            ),
            (chapter.chapter_goal or "").strip(),
        ]
    ).strip()
    completion = await complete_text(
        session,
        effective_settings,
        LLMCompletionRequest(
            logical_role="writer",
            model_tier="strong" if chapter.chapter_number <= 3 else "standard",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback_content,
            prompt_template="chapter_first_writer",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            # This is a runaway guard, not the chapter length controller.
            # Length is controlled by the prompt contract and post-write gates.
            max_tokens_override=chapter_first_runaway_max_tokens(
                effective_settings,
                target_word_count=target_word_count,
                language=_project_language(project),
                hard_max_word_count=_chapter_length_contract_band(
                    project,
                    target_word_count,
                )[2],
            ),
            metadata={
                "project_slug": project.slug,
                "chapter_number": chapter.chapter_number,
                "scene_numbers": [scene.scene_number for scene in scenes],
                "generation_mode": "chapter_first",
                "length_control_method": "prompt_contract_and_quality_gate",
                "prompt_compaction": (
                    None
                    if compaction_report is None
                    else {
                        "original_chars": compaction_report.original_chars,
                        "compacted_chars": compaction_report.compacted_chars,
                        "saved_tokens_estimate": compaction_report.saved_tokens_estimate,
                    }
                ),
                "max_tokens_policy": "runaway_guard_not_length_control",
                "context_query": context_packet.query_text,
            },
        ),
    )
    content_md = sanitize_novel_markdown_content(
        completion.content,
        language=_project_language(project),
    ) or fallback_content
    content_md = strip_scaffolding_echoes(content_md)
    if has_meta_leak(content_md):
        content_md = await validate_and_clean_novel_content(
            session,
            effective_settings,
            content_md,
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
        )
    if not _has_leading_chapter_heading(content_md, chapter_number):
        content_md = (
            f"{format_chapter_heading(chapter_number, chapter.title, language=_project_language(project))}"
            f"\n\n{content_md}"
        )
    content_md, cleanup_stats = _clean_generated_chapter_text(
        content_md,
        chapter_number=chapter_number,
        source="chapter_first",
    )
    if any(cleanup_stats.values()):
        logger.info(
            "chapter_first %d: cleanup stats=%s word_count=%d",
            chapter_number,
            cleanup_stats,
            count_words(content_md),
        )

    deterministic_audit_report = None
    try:
        from bestseller.services.deterministic_post_write_audit import audit_chapter_prose

        hard_min_words, _hard_target_words, hard_max_words = _chapter_length_contract_band(
            project,
            target_word_count,
        )
        deterministic_audit_report = audit_chapter_prose(
            chapter_text=content_md,
            chapter_number=chapter_number,
            project_dir=Path(effective_settings.output.base_dir) / project.slug,
            scenes=scenes,
            chapter_metadata={
                **(chapter.metadata_json or {}),
                "hard_min_word_count": hard_min_words,
                "hard_max_word_count": hard_max_words,
            },
        )
        if not deterministic_audit_report.passed:
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "deterministic_audit_latest": deterministic_audit_report.to_dict(),
            }
    except Exception:
        logger.debug("chapter_first deterministic audit failed", exc_info=True)

    duplicate_gate_findings = await _collect_post_assembly_duplicate_findings(
        session,
        project=project,
        chapter=chapter,
        content_md=content_md,
    )
    if duplicate_gate_findings:
        _stamp_duplicate_content_block(chapter, duplicate_gate_findings)

    previous_chapter_texts: tuple[tuple[int, str], ...] = ()
    try:
        previous_chapter_texts = await _collect_previous_current_chapter_texts(
            session,
            project=project,
            chapter_number=chapter_number,
        )
    except Exception:
        logger.debug(
            "Chapter %d: prior chapter text lookup for chapter-first quality failed",
            chapter_number,
            exc_info=True,
        )
    previous_chapter_number = previous_chapter_texts[-1][0] if previous_chapter_texts else None
    previous_chapter_text = previous_chapter_texts[-1][1] if previous_chapter_texts else None
    commercial_quality_required = bool(effective_settings.pipeline.commercial_strict_quality_mode) and (
        int(project.target_chapters or 0)
        >= int(effective_settings.pipeline.commercial_planning_min_target_chapters)
    )
    quality_bundle_report: ChapterQualityBundleReport | None = None
    if commercial_quality_required:
        quality_bundle_report = run_chapter_quality_bundle(
            content_md,
            ChapterQualityBundleContext(
                chapter_number=chapter_number,
                previous_chapter_text=previous_chapter_text,
                previous_chapter_position=previous_chapter_number,
                previous_chapter_texts=previous_chapter_texts,
                total_chapters=project.target_chapters or 500,
                language=project.language,
                target_chapter_words=effective_settings.generation.words_per_chapter.target,
                commercial_strict=bool(effective_settings.pipeline.commercial_strict_quality_mode),
                hook_domain_tokens=_bundle_hook_domain_tokens(project),
            ),
        )
        _stamp_chapter_quality_bundle(chapter, quality_bundle_report)

    quality_gate_outcome = await _evaluate_chapter_quality_gate(
        session=session,
        project=project,
        chapter_number=chapter_number,
        content=content_md,
    )
    if duplicate_gate_findings or (
        quality_bundle_report is not None and quality_bundle_report.blocking_findings
    ) or (
        deterministic_audit_report is not None
        and not deterministic_audit_report.passed
    ):
        quality_gate_outcome = "blocked"

    word_count = authoritative_word_count_for_language(
        content_md,
        language=project.language or "zh-CN",
    )
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id
                )
            )
        )
        or 0
    ) + 1
    await session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=[f"chapter_first_scene:{scene.id}" for scene in scenes],
        is_current=True,
        llm_run_id=completion.llm_run_id,
    )
    session.add(chapter_draft)
    chapter.current_word_count = word_count
    chapter.status = (
        ChapterStatus.REVISION.value
        if previous_chapter_status == ChapterStatus.REVISION.value
        else ChapterStatus.DRAFTING.value
    )
    chapter.production_state = quality_gate_outcome or "ok"
    try:
        from bestseller.services.chapter_generation_input_builder import (
            build_chapter_generation_input_bundle,
            build_chapter_generation_input_stamp,
        )

        generation_input_bundle = build_chapter_generation_input_bundle(
            project=project,
            chapter=chapter,
            scenes=scenes,
            context_packet=context_packet,
            target_word_count=target_word_count,
        )
        generation_input_payload = build_chapter_generation_input_stamp(
            generation_input_bundle
        )
    except Exception:
        logger.debug("Chapter %d: generation input bundle stamp failed", chapter_number, exc_info=True)
        generation_input_payload = {}
    chapter.metadata_json = {
        **(chapter.metadata_json or {}),
        "chapter_first_generation": {
            "enabled": True,
            "model_name": completion.model_name,
            "provider": completion.provider,
            "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
            "scene_count": len(scenes),
            "generation_input_stamp": generation_input_payload,
        },
    }
    await session.flush()

    if settings is not None:
        try:
            output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter_number:03d}.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content_md, encoding="utf-8")
        except Exception:
            logger.debug("Chapter %d: chapter-first disk sync failed", chapter_number, exc_info=True)

    return chapter_draft


def _determine_model_tier(
    chapter: ChapterModel,
    scene: SceneCardModel,
    chapter_contract: dict[str, Any] | None = None,
) -> str:
    """Determine whether this scene should use the 'strong' model tier.

    Golden-three chapters, climax scenes, and turning points get the stronger
    model for richer prose quality.
    """
    if chapter.chapter_number <= 3:
        return "strong"
    if chapter_contract and chapter_contract.get("is_climax"):
        return "strong"
    if scene.scene_type in ("climax", "revelation", "turning_point"):
        return "strong"
    return "standard"


def _load_project_prewrite_contract_metadata(
    project: ProjectModel,
    *,
    output_base_dir: str | Path,
    base_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(base_metadata or {})
    slug = str(getattr(project, "slug", "") or "").strip()
    if not slug:
        return metadata
    path = Path(output_base_dir) / slug / "story-bible" / "prewrite-contract.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return metadata
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load prewrite contract metadata from %s: %s", path, exc)
        return metadata
    if not isinstance(payload, dict):
        return metadata
    return _deep_merge_dicts(metadata, payload)


def _deep_merge_dicts(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


async def _declare_validated_prewrite_plan(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    manifest: Any,
    language: str,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[PrewritePlan, dict[str, Any]]:
    """Ask for a short scene plan and validate it before prose generation."""

    safe_plan = build_safe_prewrite_plan(manifest)
    fallback = json.dumps(safe_plan.model_dump(mode="json"), ensure_ascii=False)
    is_en = is_english_language(language)
    system_prompt = (
        (
            "# ROLE\n"
            "You are a pre-write constraint compiler.\n"
            "You translate scene contract + chapter context into a tight prewrite plan that the\n"
            "scene_writer LLM will execute verbatim.\n"
            "\n"
            "# CONTEXT\n"
            "Downstream consumer: scene_writer LLM.\n"
            "Your plan = its writing checklist. Vague plan = vague prose.\n"
            "\n"
            "# TASK\n"
            "Compile a JSON prewrite plan. NO prose, NO commentary, NO markdown fences.\n"
            "\n"
            "# CONSTRAINTS\n"
            "- Strict JSON output matching the schema in user_prompt\n"
            "- Every field must be concrete and actionable\n"
            "- No abstract goals (no 'advance plot' — 'protagonist confronts NPC about evidence X')\n"
            "- Honor scene contract: entry_state / exit_state / participants must match\n"
            "\n"
            "# THINKING (before JSON)\n"
            "1. Read scene contract — what MUST land by exit_state?\n"
            "2. Read chapter context — what hooks need echoing / advancing?\n"
            "3. Pick the smallest set of beats that achieves both\n"
        )
        if is_en
        else (
            "# ROLE\n"
            "你是小说写作系统的写前约束编译器。\n"
            "你把场景合同 + 章节上下文翻译成一份紧凑的 prewrite plan，下游 scene_writer LLM 会**逐条执行**。\n"
            "\n"
            "# CONTEXT\n"
            "下游消费者：scene_writer LLM。\n"
            "你的 plan = 它的写作清单。plan 模糊 = 正文必然模糊。\n"
            "\n"
            "# TASK\n"
            "编译一份 JSON prewrite plan。**只输出 JSON**，不写正文，不写解释，不带 markdown 围栏。\n"
            "\n"
            "# CONSTRAINTS\n"
            "- 严格 JSON 输出，结构匹配 user_prompt 中的 schema\n"
            "- 每一项字段必须具体、可执行\n"
            "- 禁止抽象目标（不要「推进剧情」—— 要「主角向 NPC 当面对证物证 X」）\n"
            "- 必须尊重场景合同：entry_state / exit_state / participants 不能改\n"
            "\n"
            "# THINKING（产 JSON 前在脑内 3 步）\n"
            "1. 读场景合同——退场状态要求什么必须发生？\n"
            "2. 读章节上下文——哪些钩子需要回响 / 推进？\n"
            "3. 选最小一组节拍同时满足以上两条\n"
        )
    )
    violations: list[str] = []
    last_model_name: str | None = None
    last_provider: str | None = None
    project_metadata = (
        getattr(project, "metadata_json", None)
        if isinstance(getattr(project, "metadata_json", None), dict)
        else {}
    )
    prompt_pack = resolve_prompt_pack(
        project_metadata.get("prompt_pack_name") or project_metadata.get("prompt_pack_key"),
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
    )

    for attempt in range(2):
        user_prompt = render_prewrite_plan_prompt(
            manifest,
            language=language,
            pack=prompt_pack,
            chapter_number=chapter.chapter_number,
        )
        if violations:
            joined = "\n".join(f"- {item}" for item in violations)
            user_prompt += (
                f"\n\nThe previous plan was rejected:\n{joined}\nReturn corrected JSON only."
                if is_en
                else f"\n\n上一轮计划被拒绝，原因：\n{joined}\n请只修正 JSON。"
            )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                model_tier="standard",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback,
                prompt_template="prewrite_plan_manifest",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                max_tokens_override=4096,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "attempt": attempt + 1,
                },
            ),
        )
        last_model_name = completion.model_name
        last_provider = completion.provider
        try:
            plan = normalize_prewrite_plan_for_manifest(
                parse_prewrite_plan(completion.content),
                manifest,
            )
        except ValueError as exc:
            violations = [str(exc)]
            continue
        result = validate_prewrite_plan(plan, manifest)
        if result.passed:
            return plan, {
                "mode": "llm_validated",
                "attempts": attempt + 1,
                "model_name": last_model_name,
                "provider": last_provider,
                "violations": [],
            }
        violations = result.violations

    logger.warning(
        "Pre-write plan for chapter %d scene %d failed validation; using deterministic safe plan. violations=%s",
        chapter.chapter_number,
        scene.scene_number,
        violations,
    )
    return safe_plan, {
        "mode": "deterministic_safe_fallback",
        "attempts": 2,
        "model_name": last_model_name,
        "provider": last_provider,
        "violations": violations,
    }


async def _maybe_render_library_soft_reference(
    session: AsyncSession,
    *,
    settings: AppSettings | None,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    chapter_contract: Any = None,
) -> str | None:
    """Render the soft-reference library block when the feature flag is on.

    * Returns ``None`` when the flag is off or settings are missing —
      identical to the legacy draft path.
    * Returns ``""`` (empty string) when retrieval succeeded but found
      nothing — prompt assembly treats both the same, but we keep the
      two cases distinct for telemetry and testability.
    * On any internal error we log and return ``None`` so the draft
      pipeline never fails because of soft-reference retrieval.
    """

    if settings is None:
        return None
    pipeline_settings = getattr(settings, "pipeline", None)
    if pipeline_settings is None:
        return None
    if not getattr(pipeline_settings, "enable_library_soft_reference", False):
        return None
    if not getattr(pipeline_settings, "enable_material_library", False):
        # Soft reference depends on the library being enabled; otherwise
        # the retrieval layer may be un-migrated and we'd raise.
        return None

    top_k = int(
        getattr(pipeline_settings, "library_soft_reference_top_k", 4) or 4
    )

    query_parts = [
        str(chapter.chapter_goal or ""),
        str(scene.title or ""),
        str(scene.purpose.get("story", "") if scene.purpose else ""),
        str(scene.purpose.get("emotion", "") if scene.purpose else ""),
        " ".join(scene.participants or []),
    ]
    query_text = " ".join(p for p in query_parts if p).strip()
    if not query_text:
        return None

    try:
        from bestseller.services.material_library_reference import (  # noqa: PLC0415
            render_library_soft_reference_block,
            select_soft_reference_dims,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("library soft-reference import failed: %s", exc)
        return None

    # Phase-aware gate: surface scene-bank桥段 on set-piece beats, step them
    # aside on breathers. Pacing / climax come from the chapter contract; when
    # absent (legacy / cold-start) the helper returns dims=None → default dims,
    # so behaviour is unchanged. Read fields defensively (model or mapping).
    def _contract_get(key: str) -> Any:
        if chapter_contract is None:
            return None
        if isinstance(chapter_contract, dict):
            return chapter_contract.get(key)
        return getattr(chapter_contract, key, None)

    dims, eff_top_k = select_soft_reference_dims(
        pacing_mode=_contract_get("pacing_mode"),
        is_climax=bool(_contract_get("is_climax")),
        emotion_phase=_contract_get("emotion_phase"),
        base_top_k=top_k,
    )

    try:
        return await render_library_soft_reference_block(
            session,
            query=query_text,
            genre=getattr(project, "genre", None),
            sub_genre=getattr(project, "sub_genre", None),
            dimensions=dims,
            top_k=eff_top_k,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("library soft-reference render failed: %s", exc)
        return None


async def generate_scene_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    context_packet: SceneWriterContextPacket | None = None,
) -> SceneDraftVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scene = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == chapter.id,
            SceneCardModel.scene_number == scene_number,
        )
    )
    if scene is None:
        raise ValueError(
            f"Scene {scene_number} was not found in chapter {chapter_number} for '{project_slug}'."
        )

    try:
        await optimize_project_character_profiles(session, project)
    except Exception:
        logger.debug(
            "Character intelligence optimization failed before scene draft",
            exc_info=True,
        )

    effective_settings = settings or load_settings()
    if getattr(effective_settings.pipeline, "require_pre_draft_scene_contract", True):
        from bestseller.services.identity_guard import load_identity_registry
        from bestseller.services.narrative_contracts import (
            repair_missing_scene_methodology_contract_pre_draft,
            repair_missing_scene_participants_pre_draft,
            validate_scene_contract_pre_draft,
        )

        identity_registry = (
            getattr(context_packet, "identity_registry", None)
            if context_packet is not None
            else None
        )
        if identity_registry is None:
            identity_registry = await load_identity_registry(session, project.id)
        offstage_names = await _load_offstage_character_names_before_chapter(
            session,
            project.id,
            chapter_number,
        )
        removed_participants, removed_state_refs = _scrub_offstage_scene_references(
            scene,
            offstage_names,
        )
        participant_repair_count = repair_missing_scene_participants_pre_draft(
            scene,
            identity_registry=identity_registry,
            excluded_names=offstage_names,
        )
        methodology_repair_count = repair_missing_scene_methodology_contract_pre_draft(
            scene,
            chapter=chapter,
            chapter_number=chapter_number,
        )
        if (
            removed_participants
            or removed_state_refs
            or participant_repair_count
            or methodology_repair_count
        ):
            scene.metadata_json = {
                **(getattr(scene, "metadata_json", None) or {}),
                "pre_draft_participant_repair": {
                    "removed_participants": removed_participants,
                    "removed_state_refs": removed_state_refs,
                    "added_count": participant_repair_count,
                    "participants": list(scene.participants or []),
                },
                "pre_draft_methodology_contract_repair": {
                    "source": "legacy_scene_context",
                    "added_count": methodology_repair_count,
                },
            }
        contract = validate_scene_contract_pre_draft(
            scene,
            identity_registry=identity_registry,
            require_identity_registry=True,
            excluded_names=offstage_names,
            methodology_contract_mode=resolve_methodology_contract_mode(
                project,
                settings=effective_settings,
            ),
        )
        if contract.violations or contract.warnings:
            scene.metadata_json = {
                **(getattr(scene, "metadata_json", None) or {}),
                "pre_draft_scene_contract": contract.to_dict(),
            }
        contract.raise_for_blocks(
            project_slug=project_slug,
            artifact=f"scene {chapter_number}.{scene_number}",
        )

    style_guide = await session.get(StyleGuideModel, project.id)
    # Book-level imagery system (LitStyle imagery_system lever): design once per book
    # (idempotent) + persist to metadata_json so the bible loader exposes it and the
    # writer gets a soft per-chapter imagery-recall block. Soft + zh-only; any failure
    # is a no-op (imagery simply won't render). Runs before the bible is (re)loaded.
    if settings is not None:
        try:
            from bestseller.services.imagery_system_design import (
                ensure_book_imagery_system,
            )

            await ensure_book_imagery_system(session, settings, project)
        except Exception:
            logger.debug("ensure_book_imagery_system failed (non-fatal)", exc_info=True)
    if context_packet is not None:
        # Caller (run_scene_pipeline) already built a shared context for this scene —
        # reuse it instead of re-running the 10+ DB/retrieval queries inside
        # build_scene_writer_context_from_models. Opt-B memoization.
        pass
    elif settings is not None:
        context_packet = await build_scene_writer_context_from_models(
            session,
            settings,
            project,
            chapter,
            scene,
        )
    else:
        story_bible_context = await load_scene_story_bible_context(
            session,
            project=project,
            chapter=chapter,
            scene=scene,
        )
        context_packet = SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            query_text=(
                f"{chapter.chapter_goal} "
                f"{scene.title or ''} "
                f"{scene.purpose.get('story', '')} "
                f"{' '.join(scene.participants)}"
            ).strip(),
            story_bible=story_bible_context,
            recent_scene_summaries=[],
            recent_timeline_events=[],
            participant_canon_facts=[],
            active_plot_arcs=[],
            active_arc_beats=[],
            unresolved_clues=[],
            planned_payoffs=[],
            active_emotion_tracks=[],
            active_antagonist_plans=[],
            chapter_contract=None,
            scene_contract=None,
            tree_context_nodes=[],
            retrieval_chunks=[],
        )
    if context_packet is not None:
        try:
            from bestseller.services.ranking_capability_profile import (  # noqa: PLC0415
                apply_ranking_capability_profile_to_context,
            )

            _project_meta = getattr(project, "metadata_json", None) or {}
            _story_bible = (
                context_packet.story_bible if isinstance(context_packet.story_bible, dict) else {}
            )
            apply_ranking_capability_profile_to_context(
                context_packet,
                project_slug=project.slug,
                project_metadata=_project_meta if isinstance(_project_meta, dict) else {},
                story_bible_context=_story_bible,
                output_base_dir=getattr(effective_settings.output, "base_dir", None),
            )
        except Exception:
            logger.debug(
                "Ranking capability profile direct-draft injection failed (non-fatal)",
                exc_info=True,
            )
        try:
            from bestseller.services.premium_genre_engine import (
                build_premium_genre_engine_blocks,
            )

            _project_meta = getattr(project, "metadata_json", None) or {}
            _story_bible = (
                context_packet.story_bible if isinstance(context_packet.story_bible, dict) else {}
            )
            _volume_payload = _story_bible.get("volume", {})
            _current_volume = None
            if isinstance(_volume_payload, dict):
                _volume_no = _volume_payload.get("volume_number")
                if isinstance(_volume_no, int):
                    _current_volume = _volume_no
            _sub_genre = _project_meta.get("sub_genre") if isinstance(_project_meta, dict) else None
            _premium_blocks = build_premium_genre_engine_blocks(
                project_metadata=_project_meta if isinstance(_project_meta, dict) else {},
                story_bible_context=_story_bible,
                genre=getattr(project, "genre", None)
                or getattr(effective_settings.generation, "genre", None),
                sub_genre=_sub_genre if isinstance(_sub_genre, str) else None,
                language=getattr(project, "language", None)
                or getattr(effective_settings.generation, "language", "zh-CN"),
                current_volume=_current_volume,
            )
            if (
                _premium_blocks.progression_context_block
                and not context_packet.progression_context_block
            ):
                context_packet.progression_context_block = (
                    _premium_blocks.progression_context_block
                )
            if _premium_blocks.decision_policy_block and not context_packet.decision_policy_block:
                context_packet.decision_policy_block = _premium_blocks.decision_policy_block
            if (
                _premium_blocks.rule_system_context_block
                and not context_packet.rule_system_context_block
            ):
                context_packet.rule_system_context_block = (
                    _premium_blocks.rule_system_context_block
                )
            if (
                _premium_blocks.faction_ecology_context_block
                and not context_packet.faction_ecology_context_block
            ):
                context_packet.faction_ecology_context_block = (
                    _premium_blocks.faction_ecology_context_block
                )
            if (
                _premium_blocks.relationship_agency_context_block
                and not context_packet.relationship_agency_context_block
            ):
                context_packet.relationship_agency_context_block = (
                    _premium_blocks.relationship_agency_context_block
                )
            if (
                _premium_blocks.entry_system_context_block
                and not context_packet.entry_system_context_block
            ):
                context_packet.entry_system_context_block = (
                    _premium_blocks.entry_system_context_block
                )
            if (
                _premium_blocks.entry_registry_context_block
                and not context_packet.entry_registry_context_block
            ):
                context_packet.entry_registry_context_block = (
                    _premium_blocks.entry_registry_context_block
                )
            if (
                _premium_blocks.entry_state_ledger_block
                and not context_packet.entry_state_ledger_block
            ):
                context_packet.entry_state_ledger_block = (
                    _premium_blocks.entry_state_ledger_block
                )
            if _premium_blocks.warnings:
                context_packet.contradiction_warnings.extend(
                    f"[精品类型引擎] {warning}" for warning in _premium_blocks.warnings
                )
        except Exception:
            logger.debug(
                "Premium genre engine direct-draft injection failed (non-fatal)",
                exc_info=True,
            )
        # Character embodiment (单人入戏) — proven #1 prose lever. Before the writer
        # prompt is built, have the model inhabit the protagonist and emit RAW
        # first-person interiority for THIS scene, threaded into the writer prompt
        # via story_bible (rendered verbatim by methodology_compiler PROSE_SCENE).
        # Soft + zh-only + gated (enable_character_embodiment); any failure is a
        # no-op so the writer proceeds exactly as before. NOT summarized.
        if settings is not None:
            try:
                from bestseller.services.character_embodiment import (
                    generate_scene_embodiment,
                )

                _emb_story_bible = (
                    context_packet.story_bible
                    if isinstance(context_packet.story_bible, dict)
                    else {}
                )
                _interiority = await generate_scene_embodiment(
                    session,
                    settings,
                    project=project,
                    chapter=chapter,
                    scene=scene,
                    story_bible=_emb_story_bible,
                )
                if _interiority:
                    context_packet.story_bible = {
                        **_emb_story_bible,
                        "character_embodiment": _interiority,
                    }
            except Exception:
                logger.debug(
                    "character embodiment direct-draft injection failed (non-fatal)",
                    exc_info=True,
                )
    fallback_content = render_scene_draft_markdown(
        project,
        chapter,
        scene,
        style_guide,
        _packet_story_bible_context(context_packet),
        _packet_retrieval_context(context_packet),
        _packet_recent_scene_summaries(context_packet),
        _packet_recent_timeline_events(context_packet),
        _packet_participant_canon_facts(context_packet),
        _packet_active_plot_arcs(context_packet),
        _packet_active_arc_beats(context_packet),
        _packet_unresolved_clues(context_packet),
        _packet_planned_payoffs(context_packet),
        _packet_chapter_contract(context_packet),
        _packet_scene_contract(context_packet),
        _packet_tree_context(context_packet),
        _packet_emotion_tracks(context_packet),
        _packet_antagonist_plans(context_packet),
    )
    model_name = "mock-writer"
    llm_run_id: UUID | None = None
    generation_mode = "template-fallback"
    content_md = fallback_content
    prompt_trace_path: str | None = None
    writer_prompt_selected_mode = "template-fallback"
    writer_prompt_ab_metrics: list[dict[str, Any]] = []
    prewrite_manifest = None
    prewrite_plan: PrewritePlan | None = None
    prewrite_plan_meta: dict[str, Any] = {"mode": "not_run"}
    prewrite_contract_block: str | None = None
    prewrite_plan_block: str | None = None
    completion_finish_reason: str | None = None
    llm_output_truncated = False
    if settings is not None:
        language = _project_language(project)
        canon_guardrails = load_canon_guardrails_for_project(
            project,
            output_base_dir=settings.output.base_dir,
        )
        project_metadata = (
            getattr(project, "metadata_json", None)
            if isinstance(getattr(project, "metadata_json", None), dict)
            else {}
        )
        project_metadata = _load_project_prewrite_contract_metadata(
            project,
            output_base_dir=settings.output.base_dir,
            base_metadata=project_metadata,
        )
        prewrite_manifest = compile_chapter_constraint_manifest(
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            participants=list(scene.participants or []),
            scene_time_label=scene.time_label,
            scene_metadata=scene.metadata_json if isinstance(scene.metadata_json, dict) else {},
            scene_exit_state=scene.exit_state if isinstance(scene.exit_state, dict) else {},
            story_bible_context=_packet_story_bible_context(context_packet) or {},
            hard_fact_snapshot=_packet_hard_fact_snapshot(context_packet),
            recent_timeline_events=_packet_recent_timeline_events(context_packet),
            hook_requirement=scene.hook_requirement,
            canon_guardrails=canon_guardrails,
            project_metadata=project_metadata,
        )
        prewrite_contract_block = render_constraint_manifest_block(
            prewrite_manifest,
            language=language,
        )
        prewrite_plan, prewrite_plan_meta = await _declare_validated_prewrite_plan(
            session,
            settings,
            project=project,
            chapter=chapter,
            scene=scene,
            manifest=prewrite_manifest,
            language=language,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
        )
        prewrite_plan_block = render_prewrite_plan_block(
            prewrite_plan,
            language=language,
        )
        if context_packet is not None and not context_packet.scene_beat_block:
            try:
                if get_quality_gates_config().prose_quality.beat_planner_enabled:
                    from bestseller.services.scene_beat_planner import (
                        build_scene_beat_sheet,
                    )
                    from bestseller.services.scene_beat_renderer import (
                        render_scene_beat_sheet_block,
                    )

                    _scene_contract = (
                        context_packet.scene_contract.model_dump(mode="json")
                        if context_packet.scene_contract is not None
                        else None
                    )
                    _chapter_contract = (
                        context_packet.chapter_contract.model_dump(mode="json")
                        if context_packet.chapter_contract is not None
                        else None
                    )
                    _beat_sheet = build_scene_beat_sheet(
                        chapter_number=chapter.chapter_number,
                        scene_number=scene.scene_number,
                        scene_title=scene.title,
                        scene_type=scene.scene_type,
                        time_label=scene.time_label,
                        participants=list(scene.participants or []),
                        chapter_goal=chapter.chapter_goal,
                        story_purpose=(scene.purpose or {}).get("story"),
                        emotion_purpose=(scene.purpose or {}).get("emotion"),
                        entry_state=scene.entry_state or {},
                        exit_state=scene.exit_state or {},
                        scene_contract=_scene_contract,
                        chapter_contract=_chapter_contract,
                        word_target=scene.target_word_count,
                    )
                    context_packet.scene_beat_block = (
                        render_scene_beat_sheet_block(_beat_sheet, language=language)
                        or None
                    )
            except Exception:
                logger.debug(
                    "scene beat sheet direct-draft injection failed (non-fatal)",
                    exc_info=True,
                )
        system_prompt, user_prompt = build_scene_draft_prompts(
            project,
            chapter,
            scene,
            style_guide,
            _packet_story_bible_context(context_packet),
            _packet_retrieval_context(context_packet),
            _packet_recent_scene_summaries(context_packet),
            _packet_recent_timeline_events(context_packet),
            _packet_participant_canon_facts(context_packet),
            _packet_active_plot_arcs(context_packet),
            _packet_active_arc_beats(context_packet),
            _packet_unresolved_clues(context_packet),
            _packet_planned_payoffs(context_packet),
            _packet_chapter_contract(context_packet),
            _packet_scene_contract(context_packet),
            _packet_tree_context(context_packet),
            _packet_emotion_tracks(context_packet),
            _packet_antagonist_plans(context_packet),
            hard_fact_snapshot=_packet_hard_fact_snapshot(context_packet),
            contradiction_warnings=getattr(context_packet, "contradiction_warnings", None) if context_packet else None,
            query_brief=(context_packet.query_brief if context_packet else None),
            participant_knowledge_states=getattr(context_packet, "participant_knowledge_states", None) if context_packet else None,
            arc_summaries=getattr(context_packet, "arc_summaries", None) if context_packet else None,
            world_snapshot=getattr(context_packet, "world_snapshot", None) if context_packet else None,
            # Phase-1 wiring
            pacing_target=(
                context_packet.pacing_target.model_dump(mode="json")
                if context_packet and context_packet.pacing_target
                else None
            ),
            subplot_schedule=(
                [e.model_dump(mode="json") for e in context_packet.subplot_schedule]
                if context_packet and context_packet.subplot_schedule
                else None
            ),
            ending_contract=(
                context_packet.ending_contract.model_dump(mode="json")
                if context_packet and context_packet.ending_contract
                else None
            ),
            reader_knowledge_entries=(
                [e.model_dump(mode="json") for e in context_packet.reader_knowledge_entries]
                if context_packet and context_packet.reader_knowledge_entries
                else None
            ),
            relationship_milestones=(
                [e.model_dump(mode="json") for e in context_packet.relationship_milestones]
                if context_packet and context_packet.relationship_milestones
                else None
            ),
            # Phase-2 wiring
            structure_beat_name=(
                context_packet.structure_beat_name if context_packet else None
            ),
            structure_beat_description=(
                context_packet.structure_beat_description if context_packet else None
            ),
            # Phase-3 wiring
            swain_pattern=(
                context_packet.swain_pattern if context_packet else None
            ),
            scene_skeleton=(
                context_packet.scene_skeleton if context_packet else None
            ),
            genre_obligations_due=(
                context_packet.genre_obligations_due if context_packet else None
            ),
            foreshadowing_gap_warning=(
                context_packet.foreshadowing_gap_warning if context_packet else None
            ),
            identity_constraint_block=(
                context_packet.identity_constraint_block if context_packet else None
            ),
            overused_phrase_block=(
                context_packet.overused_phrase_block if context_packet else None
            ),
            genre_constraint_block=(
                context_packet.genre_constraint_block if context_packet else None
            ),
            ranking_capability_profile_block=(
                context_packet.ranking_capability_profile_block
                if context_packet
                else None
            ),
            progression_context_block=(
                context_packet.progression_context_block if context_packet else None
            ),
            decision_policy_block=(
                context_packet.decision_policy_block if context_packet else None
            ),
            rule_system_context_block=(
                context_packet.rule_system_context_block if context_packet else None
            ),
            faction_ecology_context_block=(
                context_packet.faction_ecology_context_block if context_packet else None
            ),
            relationship_agency_context_block=(
                context_packet.relationship_agency_context_block if context_packet else None
            ),
            entry_system_context_block=(
                context_packet.entry_system_context_block if context_packet else None
            ),
            entry_registry_context_block=(
                context_packet.entry_registry_context_block if context_packet else None
            ),
            entry_state_ledger_block=(
                context_packet.entry_state_ledger_block if context_packet else None
            ),
            opening_diversity_block=(
                context_packet.opening_diversity_block if context_packet else None
            ),
            conflict_diversity_block=(
                context_packet.conflict_diversity_block if context_packet else None
            ),
            scene_purpose_diversity_block=(
                context_packet.scene_purpose_diversity_block if context_packet else None
            ),
            env_diversity_block=(
                context_packet.env_diversity_block if context_packet else None
            ),
            arc_beat_block=(
                context_packet.arc_beat_block if context_packet else None
            ),
            five_layer_block=(
                context_packet.five_layer_block if context_packet else None
            ),
            cliffhanger_diversity_block=(
                context_packet.cliffhanger_diversity_block if context_packet else None
            ),
            tension_target_block=(
                context_packet.tension_target_block if context_packet else None
            ),
            location_ledger_block=(
                context_packet.location_ledger_block if context_packet else None
            ),
            budget_diversity_block=(
                context_packet.budget_diversity_block if context_packet else None
            ),
            scene_scope_isolation_block=(
                context_packet.scene_scope_isolation_block if context_packet else None
            ),
            plan_richness_block=(
                context_packet.plan_richness_block if context_packet else None
            ),
            reader_contract_block=(
                context_packet.reader_contract_block if context_packet else None
            ),
            hype_constraints_block=(
                context_packet.hype_constraints_block if context_packet else None
            ),
            l3_prompt_block=(
                context_packet.l3_prompt_block if context_packet else None
            ),
            scene_beat_block=(
                context_packet.scene_beat_block if context_packet else None
            ),
            library_reference_block=await _maybe_render_library_soft_reference(
                session,
                settings=settings,
                project=project,
                chapter=chapter,
                scene=scene,
                chapter_contract=(
                    context_packet.chapter_contract if context_packet else None
                ),
            ),
            # P1 Originality Engine — pre-rendered by pipelines.py from
            # chapter_orchestrator.prepare_chapter_context. None on legacy
            # projects with no DNA / signature plan / prior feedback.
            voice_dna_block=(
                context_packet.voice_dna_block if context_packet else None
            ),
            dialogue_voice_block=(
                context_packet.dialogue_voice_block if context_packet else None
            ),
            chapter_market_constraints_block=(
                context_packet.chapter_market_constraints_block
                if context_packet
                else None
            ),
            signature_scene_block=(
                context_packet.signature_scene_block if context_packet else None
            ),
            prior_persona_feedback_block=(
                context_packet.prior_persona_feedback_block
                if context_packet
                else None
            ),
            # Retention safety gates
            hook_echo_block=(
                context_packet.hook_echo_block if context_packet else None
            ),
            acceptance_duty_block=render_scene_acceptance_block(
                scene_number=int(scene.scene_number or 1),
                total_scenes=(
                    context_packet.chapter_scene_total if context_packet else None
                ),
                chapter_number=int(chapter.chapter_number or 1),
                prev_hook_tokens=(
                    context_packet.prev_hook_tokens if context_packet else None
                ),
                language=_project_language(project),
            )
            or None,
            exposition_density_block=(
                context_packet.exposition_density_block if context_packet else None
            ),
            canon_guardrails_block=(
                context_packet.canon_guardrails_block if context_packet else None
            ),
            # Story Integrity whitelists — see drafts.py docstring for ordering.
            timeline_canon_block=(
                context_packet.timeline_canon_block if context_packet else None
            ),
            scene_coherence_block=(
                context_packet.scene_coherence_block if context_packet else None
            ),
            character_role_block=(
                context_packet.character_role_block if context_packet else None
            ),
            chapter_length_block=(
                context_packet.chapter_length_block if context_packet else None
            ),
            prewrite_contract_block=prewrite_contract_block,
            prewrite_plan_block=prewrite_plan_block,
            context_budget_tokens=(
                settings.generation.context_budget_tokens if settings else 6000
            ),
        )
        # Inject voice drift correction prompts for scene participants
        proj_metadata = getattr(project, "metadata_json", None) or {}
        voice_corrections = proj_metadata.get("voice_corrections", {}) if isinstance(proj_metadata, dict) else {}
        voice_corrections_block = ""
        if voice_corrections and scene.participants:
            _vc_is_en = is_english_language(_project_language(project))
            correction_lines: list[str] = []
            for participant in scene.participants:
                correction = voice_corrections.get(participant)
                if correction:
                    _vc_label = f"[{participant} Voice Correction]" if _vc_is_en else f"【{participant}语音修正】"
                    correction_lines.append(f"{_vc_label}{correction}")
            if correction_lines:
                header = "## Voice Corrections" if _vc_is_en else "【角色语音修正】"
                voice_corrections_block = f"{header}\n" + "\n".join(correction_lines)
        if voice_corrections_block:
            user_prompt = f"{user_prompt}\n\n{voice_corrections_block}"
        raw_user_prompt = user_prompt
        _model_tier = _determine_model_tier(
            chapter,
            scene,
            _packet_chapter_contract(context_packet),
        )
        prompt_mode = _writer_prompt_mode_for_chapter(
            effective_settings,
            int(chapter.chapter_number or 0),
        )
        prompt_variants = ("full", "lean") if prompt_mode == "ab" else (prompt_mode,)
        # App-level best-of-N for golden-three (strong tier) chapters: the 0.92
        # bar is highest there and MiniMax ignores the provider-side ``n`` param,
        # so we sample the writer n_candidates times and keep the best-scoring draft
        # (via _score_writer_candidate). Restricted to strong tier to concentrate the
        # extra cost where it matters; standard-tier chapters stay single-shot.
        _writer_n = max(1, int(getattr(settings.llm.writer, "n_candidates", 1) or 1))
        _best_of_n = _writer_n if _model_tier == "strong" else 1
        variant_plan = [
            (variant, attempt)
            for variant in prompt_variants
            for attempt in range(_best_of_n)
        ]
        candidate_records: list[dict[str, Any]] = []
        completion = None
        prompt_trace_path = None
        user_prompt = raw_user_prompt
        for variant, _candidate_attempt in variant_plan:
            try:
                from bestseller.services.prompt_compactor import compact_user_prompt

                variant_user_prompt, variant_compaction_report = compact_user_prompt(
                    raw_user_prompt,
                    chapter_no=int(chapter.chapter_number or 0),
                    forbidden_terms_full=_front10_forbidden_signal_terms(chapter, project=project),
                    lean=variant == "lean",
                )
            except Exception:
                variant_user_prompt = raw_user_prompt
                variant_compaction_report = None
                logger.debug("scene prompt compaction failed", exc_info=True)
            variant_trace_path = _maybe_write_scene_prompt_trace(
                settings,
                project,
                chapter,
                scene,
                context_packet,
                system_prompt=system_prompt,
                user_prompt=variant_user_prompt,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                model_tier=_model_tier,
                trace_kind=(
                    f"scene-{variant}"
                    if _best_of_n <= 1
                    else f"scene-{variant}-c{_candidate_attempt}"
                ),
            )
            variant_completion = await complete_text(
                session,
                # Use the resolved settings (settings or load_settings()) so a
                # standalone caller that omits ``settings`` still hits the real
                # writer model instead of silently degrading to the mock provider.
                effective_settings,
                LLMCompletionRequest(
                    logical_role="writer",
                    model_tier=_model_tier,
                    system_prompt=system_prompt,
                    user_prompt=variant_user_prompt,
                    cache_system=True,
                    fallback_response=fallback_content,
                    prompt_template="scene_writer",
                    prompt_version="1.0",
                    project_id=project.id,
                    workflow_run_id=workflow_run_id,
                    step_run_id=step_run_id,
                    max_tokens_override=prose_output_max_tokens_for_target(
                        scene.target_word_count,
                        language=_project_language(project),
                        settings=settings,
                        role="writer",
                    ),
                    metadata={
                        "project_slug": project.slug,
                        "chapter_number": chapter.chapter_number,
                        "scene_number": scene.scene_number,
                        "context_query": context_packet.query_text,
                        "prompt_mode": variant,
                        "prompt_mode_ab": prompt_mode == "ab",
                        "candidate_attempt": _candidate_attempt,
                        "best_of_n": _best_of_n,
                        "prompt_compaction": (
                            None
                            if variant_compaction_report is None
                            else {
                                "original_chars": variant_compaction_report.original_chars,
                                "compacted_chars": variant_compaction_report.compacted_chars,
                                "saved_tokens_estimate": variant_compaction_report.saved_tokens_estimate,
                            }
                        ),
                        "protagonist_name": str((scene.participants or [""])[0] or "").strip(),
                        "supporting_name": str(
                            (scene.participants or ["", ""])[1]
                            if len(scene.participants or []) > 1
                            else ""
                        ).strip(),
                        "model_tier": _model_tier,
                        **({"prompt_trace_path": variant_trace_path} if variant_trace_path else {}),
                    },
                ),
            )
            candidate_score = _score_writer_candidate(
                variant_completion.content,
                target_word_count=scene.target_word_count,
                language=_project_language(project),
            )
            candidate_records.append(
                {
                    "prompt_mode": variant,
                    "llm_run_id": str(variant_completion.llm_run_id)
                    if variant_completion.llm_run_id
                    else None,
                    "model_name": variant_completion.model_name,
                    "provider": variant_completion.provider,
                    "latency_ms": variant_completion.latency_ms,
                    "input_tokens": variant_completion.input_tokens,
                    "output_tokens": variant_completion.output_tokens,
                    "score": round(candidate_score, 4),
                    "prompt_trace_path": variant_trace_path,
                    "prompt_compaction": (
                        None
                        if variant_compaction_report is None
                        else {
                            "original_chars": variant_compaction_report.original_chars,
                            "compacted_chars": variant_compaction_report.compacted_chars,
                            "saved_tokens_estimate": variant_compaction_report.saved_tokens_estimate,
                        }
                    ),
                    "_completion": variant_completion,
                    "_user_prompt": variant_user_prompt,
                    "_compaction_report": variant_compaction_report,
                }
            )
        selected_record = max(candidate_records, key=lambda item: float(item.get("score") or 0.0))
        completion = selected_record["_completion"]
        user_prompt = selected_record["_user_prompt"]
        prompt_trace_path = selected_record.get("prompt_trace_path")
        writer_prompt_ab_metrics = [
            {key: value for key, value in record.items() if not key.startswith("_")}
            for record in candidate_records
        ]
        writer_prompt_selected_mode = str(selected_record.get("prompt_mode") or prompt_mode)
        if completion.provider == "fallback":
            # LLM call failed after all retries. Log clearly but let the
            # pipeline continue — the chapter-level guard in
            # render_chapter_draft_markdown will raise if ALL scenes failed.
            logger.error(
                "Scene %d.%d LLM writer FAILED — using fallback placeholder. "
                "model=%s finish_reason=%s",
                chapter_number,
                scene_number,
                completion.model_name,
                completion.finish_reason,
            )
        completion_finish_reason = completion.finish_reason
        llm_output_truncated = _finish_reason_indicates_truncation(completion_finish_reason)
        if llm_output_truncated:
            logger.warning(
                "Scene %d.%d LLM writer stopped by output token limit "
                "(finish_reason=%s); draft will be blocked for repair if assembled.",
                chapter_number,
                scene_number,
                completion_finish_reason,
            )
        content_md = sanitize_novel_markdown_content(completion.content, language=_project_language(project)) or fallback_content
        content_md = strip_scaffolding_echoes(content_md)
        # LLM-based cleanup if regex sanitizer missed meta-commentary
        if has_meta_leak(content_md):
            content_md = await validate_and_clean_novel_content(
                session,
                settings,
                content_md,
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
            )
        model_name = completion.model_name
        llm_run_id = completion.llm_run_id
        generation_mode = completion.provider

        # ── L4 + L4.5: scene-scope quality gate with bounded regen ──
        # Runs AFTER meta-leak cleanup so the validator sees the published
        # text, not LLM scaffolding. Checks that self-exempt at scene scope
        # (length envelope, entity density) are cheap no-ops. Language /
        # naming / dialog integrity / POV lock run here and earn their keep.
        scene_regen_count = 0
        scene_validator, scene_ctx = await _build_scene_validator(session, project)
        if scene_validator is not None and scene_ctx is not None:
            (
                content_md,
                regen_model_name,
                regen_llm_run_id,
                regen_provider,
                scene_regen_count,
            ) = await _regenerate_scene_until_valid(
                session=session,
                settings=settings,
                project=project,
                chapter_number=chapter_number,
                scene_number=scene_number,
                initial_content=content_md,
                validator=scene_validator,
                ctx=scene_ctx,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_content=fallback_content,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                model_tier=_model_tier,
                context_query=context_packet.query_text,
                protagonist_name=str((scene.participants or [""])[0] or "").strip(),
                supporting_name=str(
                    (scene.participants or ["", ""])[1]
                    if len(scene.participants or []) > 1
                    else ""
                ).strip(),
                target_word_count=scene.target_word_count,
                global_budget=None,
            )
            if scene_regen_count > 0:
                # Overwrite with the retry's provenance so the draft row
                # points to the accepted attempt rather than the rejected one.
                if regen_model_name:
                    model_name = regen_model_name
                if regen_llm_run_id:
                    llm_run_id = regen_llm_run_id
                generation_mode = f"regen_{regen_provider}"
                logger.info(
                    "scene %d.%d: regenerated %d time(s) before passing gate",
                    chapter_number,
                    scene_number,
                    scene_regen_count,
                )
    else:
        content_md = strip_scaffolding_echoes(sanitize_novel_markdown_content(content_md))
        scene_regen_count = 0
    word_count = authoritative_word_count_for_language(
        content_md,
        language=project.language or "zh-CN",
    )
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(SceneDraftVersionModel.version_no), 0)).where(
                    SceneDraftVersionModel.scene_card_id == scene.id
                )
            )
        )
        or 0
    ) + 1

    await session.execute(
        update(SceneDraftVersionModel)
        .where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        is_current=True,
        model_name=model_name,
        prompt_template="scene_writer",
        prompt_version="1.0",
        llm_run_id=llm_run_id,
        generation_params={
            "mode": generation_mode,
            "scene_type": scene.scene_type,
            "target_word_count": scene.target_word_count,
            "story_bible_context_used": bool(_packet_story_bible_context(context_packet)),
            "recent_scene_count": len(_packet_recent_scene_summaries(context_packet)),
            "recent_timeline_count": len(_packet_recent_timeline_events(context_packet)),
            "participant_fact_count": len(_packet_participant_canon_facts(context_packet)),
            "active_arc_count": len(_packet_active_plot_arcs(context_packet)),
            "active_beat_count": len(_packet_active_arc_beats(context_packet)),
            "unresolved_clue_count": len(_packet_unresolved_clues(context_packet)),
            "emotion_track_count": len(_packet_emotion_tracks(context_packet)),
            "antagonist_plan_count": len(_packet_antagonist_plans(context_packet)),
            "tree_context_count": len(_packet_tree_context(context_packet)),
            "retrieval_chunk_count": len(_packet_retrieval_context(context_packet)),
            "query_brief_used": bool(getattr(context_packet, "query_brief", None)),
            "query_tool_call_count": len(getattr(context_packet, "query_trace", []) or []),
            "regen_count": int(scene_regen_count),
            "writer_prompt_selected_mode": writer_prompt_selected_mode,
            "writer_prompt_ab_metrics": writer_prompt_ab_metrics,
            "prewrite_manifest": (
                prewrite_manifest.model_dump(mode="json")
                if prewrite_manifest is not None
                else None
            ),
            "prewrite_plan": (
                prewrite_plan.model_dump(mode="json")
                if prewrite_plan is not None
                else None
            ),
            "prewrite_plan_meta": prewrite_plan_meta,
            "finish_reason": completion_finish_reason,
            "llm_output_truncated": bool(llm_output_truncated),
            # Hype assignment — read by assemble_chapter_draft to stamp the
            # chapter row + register the moment on DiversityBudget.
            "assigned_hype_type": (
                context_packet.assigned_hype_type if context_packet else None
            ),
            "assigned_hype_recipe_key": (
                context_packet.assigned_hype_recipe_key if context_packet else None
            ),
            "assigned_hype_intensity": (
                context_packet.assigned_hype_intensity if context_packet else None
            ),
            **({"prompt_trace_path": prompt_trace_path} if prompt_trace_path else {}),
        },
    )
    session.add(draft)
    scene.status = SceneStatus.DRAFTED.value
    chapter.status = ChapterStatus.DRAFTING.value
    await session.flush()
    return draft


async def assemble_chapter_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    *,
    settings: AppSettings | None = None,
) -> ChapterDraftVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards to assemble.")

    scene_drafts: list[SceneDraftVersionModel] = []
    missing_scenes: list[int] = []
    for scene in scenes:
        draft = await session.scalar(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.scene_card_id == scene.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
        if draft is None:
            # No draft flagged is_current — a scene whose rewrite loop hit its
            # revision limit can end without a promoted draft. Fall back to its
            # latest draft and promote it (accept-best-on-stall) instead of
            # failing the WHOLE chapter/book at assembly. Only a scene with ZERO
            # drafts is genuinely un-assemblable.
            latest = await session.scalar(
                select(SceneDraftVersionModel)
                .where(SceneDraftVersionModel.scene_card_id == scene.id)
                .order_by(SceneDraftVersionModel.version_no.desc())
            )
            if latest is None:
                missing_scenes.append(scene.scene_number)
                continue
            logger.warning(
                "Chapter %d scene %d had no current draft during assembly; "
                "promoting latest draft v%s (accept-best-on-stall).",
                chapter_number,
                scene.scene_number,
                latest.version_no,
            )
            latest.is_current = True
            await session.flush()
            draft = latest
        scene_drafts.append(draft)

    if missing_scenes:
        missing = ", ".join(str(scene_number) for scene_number in missing_scenes)
        raise ValueError(
            f"Chapter {chapter_number} cannot be assembled because no draft exists at all for scenes: {missing}."
        )

    scene_number_by_id = {scene.id: int(scene.scene_number) for scene in scenes}
    scene_by_id = {scene.id: scene for scene in scenes}
    truncated_scene_numbers = sorted(
        scene_number_by_id.get(scene_draft.scene_card_id, 0)
        for scene_draft in scene_drafts
        if (
            bool((scene_draft.generation_params or {}).get("llm_output_truncated"))
            or _finish_reason_indicates_truncation(
                (scene_draft.generation_params or {}).get("finish_reason")
            )
        )
    )
    truncated_scene_numbers = [n for n in truncated_scene_numbers if n > 0]
    scene_completion_issues: list[dict[str, Any]] = []
    last_scene_number = max(scene_number_by_id.values()) if scene_number_by_id else 0
    try:
        from bestseller.services.output_hygiene import collect_unfinished_artifact_issues

        for scene_draft in scene_drafts:
            scene = scene_by_id.get(scene_draft.scene_card_id)
            scene_number = scene_number_by_id.get(scene_draft.scene_card_id, 0)
            if scene is None or scene_number <= 0:
                continue
            scene_content = scene_draft.content_md or ""
            scene_word_count = int(scene_draft.word_count or count_words(scene_content))
            scene_target = int(getattr(scene, "target_word_count", 0) or 0)
            if scene_target >= 300:
                floor_ratio = 0.70 if scene_number == last_scene_number else 0.55
                floor_words = max(120, int(scene_target * floor_ratio))
                if scene_word_count < floor_words:
                    scene_completion_issues.append(
                        {
                            "scene_number": scene_number,
                            "code": "scene_under_target",
                            "word_count": scene_word_count,
                            "target_word_count": scene_target,
                            "floor_words": floor_words,
                        }
                    )
            tail_issues = collect_unfinished_artifact_issues(
                scene_content,
                language=getattr(project, "language", None),
            )
            for issue in tail_issues[:3]:
                scene_completion_issues.append(
                    {
                        "scene_number": scene_number,
                        "code": "scene_tail_incomplete",
                        "issue": issue,
                    }
                )
    except Exception:
        logger.debug(
            "Chapter %d: scene completion scan failed (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    content_md = render_chapter_draft_markdown(chapter, scene_drafts, language=project.language)

    # ── Post-assembly intra-chapter deduplication ──
    # Detect and remove repeated paragraph blocks that can occur when multiple
    # scenes accidentally reproduce the same dialog or action.
    _beat_cluster_findings: list[dict[str, Any]] = []
    try:
        from bestseller.services.deduplication import (
            clean_meta_text_markers,
            detect_chapter_text_loop,
            detect_cross_scene_beat_reenactment,
            detect_intra_chapter_repetition,
            detect_short_cluster_near_repeat,
            remove_chapter_text_loops,
            remove_cross_scene_near_verbatim_repeats,
            remove_intra_chapter_duplicates_paraphrase,
            remove_short_cluster_near_repeats,
        )

        # 1. Strip author/tool meta-text markers (e.g. "**第28章 完**", "（本章完）")
        content_md, _meta_removed = clean_meta_text_markers(content_md)
        if _meta_removed:
            logger.info(
                "Chapter %d: removed %d meta-text marker(s) from draft.",
                chapter_number, _meta_removed,
            )

        # 2a. Block-loop detector — catch LLM "stuck-in-loop" failure mode
        # where a short-line sequence repeats N times. Must run *before*
        # per-paragraph dedup because the per-paragraph threshold (≥12 chars)
        # would skip short lines inside the loop.
        _loop_findings = detect_chapter_text_loop(content_md)
        if _loop_findings:
            logger.warning(
                "Chapter %d: %d LLM-loop block(s) detected after assembly — auto-collapsing.",
                chapter_number, len(_loop_findings),
            )
            for _lf in _loop_findings:
                logger.warning("  %s", _lf["message"])
            content_md, _loop_removed = remove_chapter_text_loops(content_md)
            logger.info(
                "Chapter %d: collapsed %d paragraph(s) from LLM-loop blocks.",
                chapter_number, _loop_removed,
            )

        # 2a-2. Fuzzy short-line cluster near-repeat — catches the failure mode
        # where two clusters of short lines echo each other with insertions/
        # deletions (so the exact-match block detector above misses them).
        _short_findings = detect_short_cluster_near_repeat(content_md)
        if _short_findings:
            logger.warning(
                "Chapter %d: %d short-line cluster near-repeat(s) — auto-collapsing.",
                chapter_number, len(_short_findings),
            )
            content_md, _short_removed = remove_short_cluster_near_repeats(content_md)
            logger.info(
                "Chapter %d: dropped %d short-line paragraph(s) from cluster repeats.",
                chapter_number, _short_removed,
            )

        # 2b. Remove intra-chapter duplicate paragraphs (byte-exact + paraphrased)
        _dup_findings = detect_intra_chapter_repetition(content_md)
        if _dup_findings:
            logger.warning(
                "Chapter %d: %d duplicate paragraph(s) detected after assembly — auto-removing.",
                chapter_number, len(_dup_findings),
            )
            for _f in _dup_findings:
                logger.warning("  %s", _f["message"])
            content_md, _removed = remove_intra_chapter_duplicates_paraphrase(content_md)
            logger.info("Chapter %d: removed %d duplicate paragraph(s).", chapter_number, _removed)

        # 2c. Cross-scene beat re-enactment (节拍重演) — real incident
        # zhaoshen-hr-v3 ch1: chapter-level cut_point fanned out into every
        # scene card, so s01 wrote the full climax and s02 re-staged the same
        # beats with fresh wording. Layers 1–2b are blind to long-form
        # reworded re-enactment. Near-verbatim anchor paragraphs are removed
        # deterministically (keep first occurrence); paraphrase-level
        # re-enactment clusters are NOT deleted here — they surface as
        # recoverable rewrite_task findings through the post-assembly
        # duplicate gate below. This layer never raises.
        _beat_findings = detect_cross_scene_beat_reenactment(content_md)
        if _beat_findings:
            logger.warning(
                "Chapter %d: %d cross-scene beat re-enactment finding(s) after assembly.",
                chapter_number, len(_beat_findings),
            )
            for _bf in _beat_findings:
                logger.warning("  %s", _bf["message"])
            # Preserve pre-cleanup cluster findings: removing the near-verbatim
            # anchors below can drop the cluster under the detection threshold,
            # but the paraphrase-level re-enactment still needs a rewrite_task
            # repair — forward them to the duplicate gate explicitly.
            _beat_cluster_findings = [
                dict(_bf) for _bf in _beat_findings
                if _bf.get("kind") == "beat_reenactment"
            ]
            content_md, _beat_removed = remove_cross_scene_near_verbatim_repeats(content_md)
            if _beat_removed:
                logger.info(
                    "Chapter %d: removed %d near-verbatim cross-scene paragraph(s).",
                    chapter_number, _beat_removed,
                )
    except Exception:
        logger.debug("Post-assembly dedup failed (non-fatal)", exc_info=True)

    # NOTE: chapter length is controlled at the SCENE level (each scene draft is
    # converged into its word band before assembly) and by the prompt budget —
    # NOT by deterministically trimming the assembled chapter. Blunt tail-trim
    # was removed: it broke continuity (per-scene knowledge/clues are extracted
    # before assembly, so cutting the assembled tail desynced the ledger from
    # the prose, and dropped the chapter-ending hook).

    deterministic_audit_report = None
    try:
        from bestseller.services.deterministic_post_write_audit import audit_chapter_prose

        effective_settings_for_audit = settings or load_settings()
        hard_min_words, _hard_target_words, hard_max_words = _chapter_length_contract_band(
            project,
            int(chapter.target_word_count or effective_settings_for_audit.generation.words_per_chapter.target),
        )
        deterministic_audit_report = audit_chapter_prose(
            chapter_text=content_md,
            chapter_number=chapter_number,
            project_dir=Path(effective_settings_for_audit.output.base_dir) / project.slug,
            scenes=scenes,
            chapter_metadata={
                **(chapter.metadata_json or {}),
                "hard_min_word_count": hard_min_words,
                "hard_max_word_count": hard_max_words,
            },
        )
        if not deterministic_audit_report.passed:
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "deterministic_audit_latest": deterministic_audit_report.to_dict(),
            }
    except Exception:
        logger.debug("post-assembly deterministic audit failed (non-fatal)", exc_info=True)

    duplicate_gate_findings = await _collect_post_assembly_duplicate_findings(
        session,
        project=project,
        chapter=chapter,
        content_md=content_md,
        extra_local_findings=_beat_cluster_findings,
    )
    if duplicate_gate_findings:
        logger.warning(
            "Chapter %d: post-assembly duplicate gate blocked %d finding(s).",
            chapter_number,
            len(duplicate_gate_findings),
        )
        _stamp_duplicate_content_block(chapter, duplicate_gate_findings)

    previous_chapter_texts: tuple[tuple[int, str], ...] = ()
    try:
        previous_chapter_texts = await _collect_previous_current_chapter_texts(
            session,
            project=project,
            chapter_number=chapter_number,
        )
    except Exception:
        logger.debug(
            "Chapter %d: prior chapter text lookup for quality bundle failed (non-fatal)",
            chapter_number,
            exc_info=True,
        )
    previous_chapter_number = previous_chapter_texts[-1][0] if previous_chapter_texts else None
    previous_chapter_text = previous_chapter_texts[-1][1] if previous_chapter_texts else None
    generation_target_words: int | None = None
    commercial_strict = True
    commercial_quality_required = int(project.target_chapters or 0) >= 50
    try:
        effective_settings = settings or load_settings()
        generation_target_words = int(effective_settings.generation.words_per_chapter.target)
        commercial_strict = bool(effective_settings.pipeline.commercial_strict_quality_mode)
        commercial_quality_required = commercial_strict and (
            int(project.target_chapters or 0)
            >= int(effective_settings.pipeline.commercial_planning_min_target_chapters)
        )
    except Exception:
        logger.debug(
            "Chapter %d: quality bundle settings lookup failed, using strict defaults",
            chapter_number,
            exc_info=True,
        )
    quality_bundle_report: ChapterQualityBundleReport | None = None
    if commercial_quality_required:
        quality_bundle_report = run_chapter_quality_bundle(
            content_md,
            ChapterQualityBundleContext(
                chapter_number=chapter_number,
                previous_chapter_text=previous_chapter_text,
                previous_chapter_position=previous_chapter_number,
                previous_chapter_texts=previous_chapter_texts,
                total_chapters=project.target_chapters or 500,
                language=project.language,
                target_chapter_words=generation_target_words,
                commercial_strict=commercial_strict,
                hook_domain_tokens=_bundle_hook_domain_tokens(project),
            ),
        )
        _stamp_chapter_quality_bundle(chapter, quality_bundle_report)
        if quality_bundle_report.blocking_findings:
            logger.warning(
                "Chapter %d: quality bundle blocked %d finding(s): %s",
                chapter_number,
                len(quality_bundle_report.blocking_findings),
                ", ".join(dict.fromkeys(f.code for f in quality_bundle_report.blocking_findings)),
            )

    # ── L4/L5/L6 pre-write quality gate ──
    # Runs before the draft row + disk file land. L4 checks language/length/
    # naming/density, L5 checks dialog integrity & POV lock, L6 resolves the
    # per-violation block/audit_only mode from config/quality_gates.yaml.
    # Phase 1 only blocks on the high-confidence codes; audit-only codes
    # still get logged for future precision tuning.
    quality_gate_outcome = await _evaluate_chapter_quality_gate(
        session=session,
        project=project,
        chapter_number=chapter_number,
        content=content_md,
        extra_blocking_codes=(
            ((LLM_OUTPUT_TRUNCATED_BLOCK_CODE,) if truncated_scene_numbers else ())
            + ((SCENE_COMPLETION_BLOCK_CODE,) if scene_completion_issues else ())
        ),
        extra_report_payload=(
            {
                **(
                    {
                        "llm_output_truncation": {
                            "scene_numbers": truncated_scene_numbers,
                        }
                    }
                    if truncated_scene_numbers
                    else {}
                ),
                **(
                    {
                        "scene_completion": {
                            "issues": scene_completion_issues,
                        }
                    }
                    if scene_completion_issues
                    else {}
                ),
            }
            if truncated_scene_numbers or scene_completion_issues
            else None
        ),
    )
    if duplicate_gate_findings or (
        quality_bundle_report is not None and quality_bundle_report.blocking_findings
    ) or (
        deterministic_audit_report is not None
        and not deterministic_audit_report.passed
    ):
        quality_gate_outcome = "blocked"
    if quality_gate_outcome == "ok":
        cleared_repair_residue = _clear_scene_auto_repair_residue_after_clean_assembly(
            scenes
        )
        if cleared_repair_residue:
            logger.info(
                "Chapter %d: cleared stale auto-repair metadata from %d scene(s) after clean assembly",
                chapter_number,
                cleared_repair_residue,
            )

    word_count = authoritative_word_count_for_language(
        content_md,
        language=project.language or "zh-CN",
    )
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id
                )
            )
        )
        or 0
    ) + 1

    await session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=[str(scene_draft.id) for scene_draft in scene_drafts],
        is_current=True,
    )
    session.add(chapter_draft)
    chapter.current_word_count = word_count
    chapter.status = ChapterStatus.DRAFTING.value
    if quality_gate_outcome is not None:
        chapter.production_state = quality_gate_outcome

    # ── Reader Hype Engine — persist chapter metadata + register the moment ──
    # Scene drafts carry the assignment in ``generation_params``; all scenes of
    # the same chapter share the same pick (the per-chapter picker is
    # deterministic until ``register_hype_moment`` mutates the budget). Pick
    # the first non-null triple and use it as the chapter-level assignment.
    try:
        _hype_type: str | None = None
        _hype_recipe_key: str | None = None
        _hype_intensity: float | None = None
        for _sd in scene_drafts:
            _gp = dict(_sd.generation_params or {})
            if _gp.get("assigned_hype_type"):
                _hype_type = str(_gp["assigned_hype_type"])
                _hype_recipe_key = (
                    str(_gp["assigned_hype_recipe_key"])
                    if _gp.get("assigned_hype_recipe_key") is not None
                    else None
                )
                _hype_intensity = (
                    float(_gp["assigned_hype_intensity"])
                    if _gp.get("assigned_hype_intensity") is not None
                    else None
                )
                break
        # ── Fallback classifier ───────────────────────────────────────────
        # Blood-twins' 30/30 NULL-hype-type chapters happened because the
        # upstream assignment pipeline never stamped ``assigned_hype_type`` on
        # scene_drafts. Without this fallback the chapter row stays NULL
        # silently. Read the assembled text, run the hype-engine classifier,
        # and stamp a best-effort guess so downstream analytics (golden_three
        # health, diversity budget decay, recipe variety scoring) at least
        # have *something* to work with. The classifier never raises —
        # ``None`` is returned when no keyword fires, in which case we stay
        # NULL (honest signal that the chapter has zero readable payoff).
        if _hype_type is None and content_md:
            try:
                from bestseller.services.hype_engine import (  # noqa: PLC0415
                    HypeType as _HypeTypeEnum,
                )
                from bestseller.services.hype_engine import (
                    classify_hype,
                )

                _classifier_language = (
                    str(project.language or "zh-CN") if project is not None else "zh-CN"
                )
                _classifier_result = classify_hype(
                    content_md,
                    language=_classifier_language,
                    segment="tail",
                )
                if _classifier_result is not None:
                    _inferred_type, _inferred_confidence = _classifier_result
                    _hype_type = _inferred_type.value
                    # No recipe_key is available from the classifier path — it
                    # was never actually picked by ``plan_chapter_hype``.
                    _hype_recipe_key = None
                    # Normalise confidence (0-10) into the 0-1 intensity scale
                    # the downstream engine uses.
                    _hype_intensity = max(0.0, min(1.0, float(_inferred_confidence) / 10.0))
                    logger.info(
                        "Chapter %d: hype_type fallback-classified as %s "
                        "(confidence=%.1f) — upstream assignment was missing.",
                        chapter_number,
                        _hype_type,
                        _inferred_confidence,
                    )
            except Exception:
                logger.debug(
                    "Chapter %d: hype fallback classifier failed (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )
        if _hype_type:
            chapter.hype_type = _hype_type
            chapter.hype_recipe_key = _hype_recipe_key
            chapter.hype_intensity = _hype_intensity

            from bestseller.services.diversity_budget import (
                load_diversity_budget,
                save_diversity_budget,
            )
            from bestseller.services.hype_engine import HypeType as _HypeTypeEnum

            try:
                _hype_enum = _HypeTypeEnum(_hype_type)
            except ValueError:
                _hype_enum = None
            if _hype_enum is not None:
                _budget = await load_diversity_budget(session, project.id)
                _budget.register_hype_moment(
                    chapter_no=chapter_number,
                    hype_type=_hype_enum,
                    recipe_key=_hype_recipe_key,
                    intensity=float(_hype_intensity or 0.0),
                )
                await save_diversity_budget(session, _budget)
    except Exception:
        logger.warning(
            "Hype metadata persistence failed for chapter %d (non-fatal)",
            chapter_number,
            exc_info=True,
        )

    await session.flush()

    # ── Eagerly sync disk file so web UI always shows current content ──
    if settings is not None:
        try:
            output_path = (
                Path(settings.output.base_dir) / project.slug / f"chapter-{chapter_number:03d}.md"
            )
            # render_chapter_draft_markdown already prepends the canonical
            # chapter heading. Only prepend here if it's missing (e.g. the
            # content was sourced from an older code path that stored bare
            # prose). Avoids the "# 第N章 …" / "# 第N章 …" twin-heading bug.
            if _has_leading_chapter_heading(content_md, chapter_number):
                full_content = content_md
            else:
                heading = format_chapter_heading(
                    chapter_number, chapter.title, language=project.language
                )
                full_content = f"{heading}\n\n{content_md}"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(full_content, encoding="utf-8")
            logger.debug("Chapter %d: disk file synced → %s", chapter_number, output_path)
        except Exception:
            logger.debug("Chapter %d: disk file sync failed (non-fatal)", chapter_number, exc_info=True)

    return chapter_draft


async def maybe_prepare_chapter_auto_repair(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    repairable_codes: tuple[str, ...],
    attempt_number: int = 1,
) -> tuple[bool, tuple[str, ...]]:
    """Decide whether the chapter's most recent block is auto-repairable.

    Reads the latest :class:`ChapterQualityReportModel` row for ``chapter`` and
    intersects its ``blocking_codes`` with the configured ``repairable_codes``
    set.  When at least one code is in the repair allowlist, this helper:

    1. Resets ``chapter.production_state`` to ``"pending"`` (so the next gate
       pass starts fresh without violating the non-null DB constraint)
    2. Resets every scene on the chapter to
       :attr:`SceneStatus.NEEDS_REWRITE` and bumps the scene card's
       ``rewrite_hint`` with block-specific guidance (e.g. "expand to
       target length") so the downstream rewrite loop knows *what* to fix.

    Returns ``(repair_triggered, block_codes)``.  ``repair_triggered=False``
    means the caller should not loop — either there is no report row, the
    blocks are deterministic (e.g. naming roster), or auto-repair is off.

    The function is intentionally narrow: it only mutates scene status and
    hint fields.  It does NOT re-run scene pipelines or re-assemble — that
    is the caller's responsibility (see :func:`pipelines.run_chapter_pipeline`).
    """

    if not repairable_codes:
        return False, ()

    repair_set = {_canonical_repair_code(str(c)) for c in repairable_codes if c}
    if not repair_set:
        return False, ()

    async def _current_draft_length_payload() -> dict[str, Any]:
        current_draft = await session.scalar(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        if current_draft is None:
            return {}
        try:
            word_count = int(getattr(current_draft, "word_count", None) or 0)
        except (TypeError, ValueError):
            word_count = 0
        if word_count <= 0:
            word_count = count_words(getattr(current_draft, "content_md", "") or "")
        if word_count <= 0:
            return {}
        hard_min, target_words, hard_max = _chapter_length_contract_band(
            project,
            getattr(chapter, "target_word_count", None),
        )
        issue_code: str | None = None
        band = "OK"
        if word_count < hard_min:
            issue_code = "CHAPTER_LENGTH_BLOCK_LOW"
            band = "BLOCK_LOW"
        elif word_count > hard_max:
            issue_code = "CHAPTER_LENGTH_BLOCK_HIGH"
            band = "BLOCK_HIGH"
        return {
            "band": band,
            "min_words": hard_min,
            "target_words": target_words,
            "max_words": hard_max,
            "issue_code": issue_code,
            "word_count": word_count,
        }

    def _mark_repair_started(block_codes: tuple[str, ...]) -> None:
        chapter_meta = dict(chapter.metadata_json or {})
        chapter_meta.pop("blocked_by_write_safety_gate", None)
        chapter_meta.pop("write_safety_block_code", None)
        chapter_meta.pop("write_safety_hint", None)
        chapter_meta.pop("post_assembly_duplicate_gate", None)
        chapter_meta.pop("auto_repair_exhausted", None)
        chapter_meta["auto_repair_last_block_codes"] = list(block_codes)
        chapter_meta["auto_repair_attempts"] = max(1, int(attempt_number))
        # Cross-run cumulative counter: ``auto_repair_attempts`` above is
        # the *intra-run* number (resets to 1 at the start of every
        # pipeline invocation). ``auto_repair_total_attempts`` accumulates
        # across runs so the pipeline can refuse to keep retrying a
        # chapter that has already spent its full cross-run budget.
        # See settings.chapter_auto_repair_total_max_attempts.
        prior_total = int(chapter_meta.get("auto_repair_total_attempts") or 0)
        chapter_meta["auto_repair_total_attempts"] = prior_total + 1
        chapter_meta["auto_repair_in_progress"] = True
        chapter_meta["auto_accepted"] = False
        chapter.metadata_json = chapter_meta
        chapter.production_state = "pending"

    # Load the most recent report row — there can be multiple if the chapter
    # has been re-drafted before; we only care about the latest verdict.
    try:
        latest_report = await session.scalar(
            select(ChapterQualityReportModel)
            .where(ChapterQualityReportModel.chapter_id == chapter.id)
            .order_by(ChapterQualityReportModel.created_at.desc())
            .limit(1)
        )
    except Exception:
        logger.debug(
            "chapter %d: auto-repair report lookup failed (non-fatal)",
            chapter.chapter_number,
            exc_info=True,
        )
        return False, ()

    # Check chapter metadata for direct write-safety blocks that can bypass or
    # sit alongside the quality-report row.
    chapter_meta = dict(chapter.metadata_json or {})

    # R20 — chapter-level total scene-rounds budget (fail-fast mode).  When
    # configured (>0) and the chapter's scenes have collectively spent the
    # budget, refuse to trigger another repair pass: stamp the known block
    # codes under ``rounds_budget_exhausted`` + ``requires_machine_repair``
    # and return not-triggered, so the pipeline loop breaks and the chapter
    # follows the existing machine-repair route.
    _rounds_budget = _resolve_chapter_scene_rounds_budget()
    if _rounds_budget > 0:
        _budget_scenes = list(
            await session.scalars(
                select(SceneCardModel).where(
                    SceneCardModel.chapter_id == chapter.id
                )
            )
        )
        _total_rounds = total_chapter_scene_repair_rounds(_budget_scenes)
        if _total_rounds >= _rounds_budget:
            _latest_payload = dict(getattr(latest_report, "report_json", None) or {})
            _known_codes = tuple(
                dict.fromkeys(
                    str(c)
                    for c in (
                        *(
                            [chapter_meta.get("write_safety_block_code")]
                            if chapter_meta.get("write_safety_block_code")
                            else []
                        ),
                        *(chapter_meta.get("auto_repair_last_block_codes") or ()),
                        *(chapter_meta.get("quality_gate_block_codes") or ()),
                        *(_latest_payload.get("blocking_codes") or ()),
                    )
                    if c
                )
            )
            logger.warning(
                "chapter %d: total scene-rounds budget exhausted (%d/%d); "
                "stopping auto-repair and routing to machine repair "
                "(block codes: %s)",
                chapter.chapter_number,
                _total_rounds,
                _rounds_budget,
                list(_known_codes),
            )
            mark_chapter_rounds_budget_exhausted(
                chapter,
                block_codes=_known_codes,
                total_scene_rounds=_total_rounds,
                budget=_rounds_budget,
            )
            await session.flush()
            return False, _known_codes

    retention_findings = [
        item
        for item in (chapter_meta.get("retention_gate_last_findings") or [])
        if isinstance(item, dict)
    ]

    def _tokens_from_retention_finding(code: str, key: str) -> list[str]:
        for finding in retention_findings:
            if str(finding.get("code") or "") != code:
                continue
            evidence = finding.get("evidence")
            if not isinstance(evidence, dict):
                continue
            raw = evidence.get(key)
            if isinstance(raw, list):
                return [str(item) for item in raw if str(item).strip()]
        return []

    def _details_for_retention_finding(code: str) -> list[str]:
        details: list[str] = []
        for finding in retention_findings:
            if str(finding.get("code") or "") != code:
                continue
            detail = str(finding.get("detail") or "").strip()
            if detail:
                details.append(detail)
        return details

    def _timeline_repair_details() -> list[str]:
        details: list[str] = []
        for finding in retention_findings:
            if str(finding.get("code") or "") != "TIMELINE_INCONSISTENT":
                continue
            evidence = finding.get("evidence")
            if not isinstance(evidence, Mapping):
                continue
            for item in evidence.get("violations") or ():
                if not isinstance(item, Mapping):
                    continue
                found_anchor = str(item.get("found_anchor") or "").strip()
                canonical_anchor = str(item.get("canonical_anchor") or "").strip()
                paragraph_idx = item.get("paragraph_idx")
                detail = str(item.get("detail") or "").strip()
                if found_anchor and canonical_anchor:
                    details.append(
                        f"段落{paragraph_idx}: 将“{found_anchor}”改为正典锚点“{canonical_anchor}”；{detail}"
                    )
                elif detail:
                    details.append(f"段落{paragraph_idx}: {detail}")
        return details

    def _scene_jump_repair_details() -> list[str]:
        details: list[str] = []
        for finding in retention_findings:
            if str(finding.get("code") or "") != "SCENE_JUMP_UNRESOLVED":
                continue
            evidence = finding.get("evidence")
            if not isinstance(evidence, Mapping):
                continue
            for item in evidence.get("jumps") or ():
                if not isinstance(item, Mapping):
                    continue
                from_place = str(item.get("from") or "").strip()
                to_place = str(item.get("to") or "").strip()
                paragraph_idx = item.get("paragraph_idx")
                detail = str(item.get("detail") or "").strip()
                if from_place and to_place:
                    details.append(
                        f"段落{paragraph_idx}: 在“{from_place}”到“{to_place}”之间补一到两句可见转场动作，写清谁带着什么物证离开、经过多久、如何抵达；{detail}"
                    )
                elif detail:
                    details.append(f"段落{paragraph_idx}: {detail}")
        return details

    retention_hint_by_code = {
        "HOOK_ECHO_MISSING": (
            "本章没有在开篇呼应上一章结尾留下的具体未解问题。重写时必须在前1000字内兑现、升级或反转上一章留下的具体危险、人物或物件。"
        ),
        "HOOK_ECHO_LOW": (
            "本章对上一章结尾呼应不足。重写时必须在前1000字内增加至少两个明确回响：具体人物/地点/威胁/未答问题要被兑现、升级或反转。"
        ),
        "SIGNATURE_SCENE_MISSING": (
            "本章处在招牌场景槽位，但没有兑现招牌场景指令。重写时必须落实指定意象、誓约或揭示场面。"
        ),
        "EXPOSITION_DUMP": (
            "本章铺垫/设定解释过密。重写时必须把设定切碎进动作、对话和当下冲突，删除连续解释段。"
        ),
        "CAST_VIOLATION": (
            "本章出现了当前章节不允许登场的角色或旧设定名。重写时必须删除违规角色的对白、动作、视角、心声和在场描写；若只是旧账名，只能短暂作为账页/案卷名出现一次。"
        ),
        "TIMELINE_INCONSISTENT": (
            "本章时间线锚点与正典冲突。重写时必须按下方具体锚点做局部替换，禁止重排全章事件或新增解释性闪回。"
        ),
        "SCENE_JUMP_UNRESOLVED": (
            "本章存在地点/时间硬切。重写时必须只在命中的跳转位置补可见转场动作，禁止为了解决转场新增整场戏。"
        ),
        "WORD_COUNT_METADATA_MISMATCH": (
            "本章正文真实汉字数远低于声称字数（疑似只写了大纲/骨架）。重写时必须把每个场景写成完整现场：动作链、对白交锋、感官细节、人物选择与代价，直到真实字数达到章节硬下限；严禁形容词堆叠或重复同义句凑数。"
        ),
        "PAYOFF_LEDGER_LOW": (
            "本章钩子多、兑现少。重写时必须在本章内至少落地一个具体兑现：揭示一项确凿事实、解决一个悬念、或让主角付出/赢得可见代价，并写成现场结果而非旁白预告。"
        ),
        "PAYOFF_HOOK_ONLY": (
            "本章只抛钩子、几乎无兑现。重写时补一个当章闭环的小兑现（线索被证实/一次对抗分出胜负/一个秘密被揭开），再用新钩子收尾。"
        ),
        "PERSONA_ABANDON_RATE_HIGH": (
            "模拟读者弃读率过高。弃读集中在三类段落：开篇拖沓、连续解释/信息倾倒、无冲突过场。"
            "重写动作：①前200字内必须出现可见冲突或异常；②任何连续超过3句的设定解释切碎进动作与对话；"
            "③删掉不推进目标的过场段，把字数还给冲突与兑现。"
        ),
        "PERSONA_WEIGHTED_SCORE_LOW": (
            "模拟读者综合读感分低于线。该分由钩子密度、兑现密度、情绪冲击三个可写作通道主导，逐项执行："
            "①钩子：章末与每个转折点各留一个具体未解问题（具体人/物/威胁，禁抽象感叹），全章至少3处；"
            "②兑现：全章至少4处把已立悬念落为可见结果（证据到手/对抗分出胜负/关系或代价坐实），写成现场动作；"
            "③情绪：每个冲突高点必须有主角的具身反应（动作/生理细节，不用抽象情绪词）。"
        ),
        "PERSONA_PAYOFF_DENSITY_LOW": (
            "模拟读者反馈兑现密度过低（目标：全章至少4处可见兑现）。"
            "重写动作：先列出本章已立的悬念/承诺，为其中至少4处补上当场结果——"
            "证据被拿到、一次对抗分出胜负、一段关系或代价发生可见变化；"
            "每处兑现都写成现场动作与结果，禁止用旁白预告或「日后自见分晓」式悬置。"
        ),
    }

    def _retention_hint_for_codes(codes: Iterable[str]) -> str:
        ordered_codes = [
            code
            for code in dict.fromkeys(str(c) for c in codes if c)
            if code in retention_hint_by_code
        ]
        if not ordered_codes:
            return ""
        hint_lines: list[str] = []
        for code in ordered_codes:
            hint_lines.append(retention_hint_by_code[code])
            for detail in _details_for_retention_finding(code):
                hint_lines.append(f"  \u00b7 {detail}")
        hint_text = "\n".join(hint_lines)
        playbook_hint = render_quality_repair_playbooks(ordered_codes)
        if playbook_hint:
            hint_text = f"{hint_text}\n{playbook_hint}"
        hook_code = (
            "HOOK_ECHO_MISSING"
            if "HOOK_ECHO_MISSING" in ordered_codes
            else "HOOK_ECHO_LOW"
            if "HOOK_ECHO_LOW" in ordered_codes
            else ""
        )
        if hook_code:
            missed_tokens = _tokens_from_retention_finding(
                hook_code, "missed_tokens"
            )
            matched_tokens = _tokens_from_retention_finding(
                hook_code, "matched_tokens"
            )
            prev_tokens = _tokens_from_retention_finding(
                hook_code, "prev_hook_tokens"
            )
            if missed_tokens:
                hint_text = (
                    f"{hint_text}\n"
                    "上一章尾钩中本章漏掉的具体承诺："
                    f"{'；'.join(missed_tokens[:8])}。"
                )
            if matched_tokens:
                hint_text = (
                    f"{hint_text}\n"
                    f"已命中的回响：{'；'.join(matched_tokens[:6])}。"
                )
            if prev_tokens and not missed_tokens:
                hint_text = (
                    f"{hint_text}\n"
                    f"上一章尾钩可回响要素：{'；'.join(prev_tokens[:8])}。"
                )
        if "CAST_VIOLATION" in ordered_codes:
            cast_details = _details_for_retention_finding("CAST_VIOLATION")
            if cast_details:
                hint_text = (
                    f"{hint_text}\n"
                    f"本次命中的正典/角色违规：{'；'.join(cast_details[:5])}。"
                )
        if "TIMELINE_INCONSISTENT" in ordered_codes:
            timeline_details = _timeline_repair_details()
            timeline_hint = (
                "时间线修复必须做局部替换，不得重排全章事件：逐条按正典时间锚点替换错误时间词，"
                "并同步修正同一主体在本章内的所有冲突年份。"
            )
            if timeline_details:
                timeline_hint = (
                    f"{timeline_hint}\n"
                    "本次必须修正的具体锚点："
                    f"{'；'.join(timeline_details[:8])}。"
                )
            hint_text = f"{hint_text}\n{timeline_hint}"
        if "SCENE_JUMP_UNRESOLVED" in ordered_codes:
            scene_jump_details = _scene_jump_repair_details()
            scene_jump_hint = (
                "场景跳转修复必须做局部补桥：只在缺桥位置加入一到两句可见动作/时间流逝/交通或物证携带，"
                "不得新增整场戏、不得改变地点顺序、不得重写无关段落。"
            )
            if scene_jump_details:
                scene_jump_hint = (
                    f"{scene_jump_hint}\n"
                    "本次必须补桥的位置："
                    f"{'；'.join(scene_jump_details[:8])}。"
                )
            hint_text = f"{hint_text}\n{scene_jump_hint}"
        return hint_text

    stored_code = chapter_meta.get("write_safety_block_code")
    if stored_code:
        block_codes = (str(stored_code),)
        # Substring match: "contradiction:character_resurrection:error" should
        # match "character_resurrection" in repair_set (full code format vs short name).
        repairable_hit = tuple(
            c
            for c in block_codes
            if _canonical_repair_code(c) in repair_set
            or any(r in c for r in repair_set)
        )
        if repairable_hit:
            logger.info(
                "chapter %d: repair from stored write-safety block code %s",
                chapter.chapter_number,
                stored_code,
            )
            # Build hint from stored metadata
            hint_text = chapter_meta.get(
                "write_safety_hint",
                f"场景触发了 {stored_code} 矛盾，请修正后重写。",
            )
            companion_retention_codes = [
                str(finding.get("code") or "")
                for finding in retention_findings
                if finding.get("code")
            ]
            production_block_code = str(
                chapter_meta.get("production_block_code") or ""
            ).strip()
            if production_block_code:
                companion_retention_codes.append(production_block_code)
            companion_retention_hint = _retention_hint_for_codes(
                companion_retention_codes
            )
            if companion_retention_hint:
                hint_text = f"{hint_text}\n{companion_retention_hint}"
            strict_retention_hint = str(
                chapter_meta.get("retention_retry_strict_prompt") or ""
            ).strip()
            if strict_retention_hint:
                hint_text = f"{hint_text}\n{strict_retention_hint}"
            hint_text = (
                f"{hint_text}\n{_chapter_auto_repair_length_contract(project, chapter)}"
            )
            # Persist the hint into scene metadata so the writer sees it
            scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number.asc())
                )
            )
            dead_names = (
                await _load_dead_character_names_before_chapter(
                    session,
                    project.id,
                    chapter.chapter_number,
                )
                if _has_character_offstage_repair_code(repairable_hit)
                else frozenset()
            )
            reset_draft_count = 0
            for sc in scenes:
                # WS-C3: per-scene auto-repair hard cap (write-safety path).
                # Cumulative counter lives on the scene; outer project_repair
                # cannot reset it.  At cap we keep the prior draft and stamp
                # ``auto_accepted_with_debt`` so the chapter does not block.
                if scene_should_skip_auto_repair_reset(sc, block_codes=repairable_hit):
                    logger.info(
                        "chapter %d scene %d: per-scene cap reached (write-safety path); "
                        "skipping reset, keeping prior draft (auto_accepted_with_debt)",
                        chapter.chapter_number,
                        sc.scene_number,
                    )
                    continue
                claim_scene_auto_repair_attempt(sc, pass_id=attempt_number)
                sc.status = SceneStatus.NEEDS_REWRITE.value
                result = await session.execute(
                    update(SceneDraftVersionModel)
                    .where(
                        SceneDraftVersionModel.scene_card_id == sc.id,
                        SceneDraftVersionModel.is_current.is_(True),
                    )
                    .values(is_current=False)
                )
                try:
                    reset_draft_count += int(result.rowcount or 0)
                except Exception:
                    logger.debug(
                        "chapter %d scene %d: current draft reset count unavailable",
                        chapter.chapter_number,
                        sc.scene_number,
                        exc_info=True,
                    )
                removed = _filter_dead_scene_participants(sc, dead_names)
                scene_hint = str(hint_text)
                if removed:
                    removed_text = "、".join(removed)
                    scene_hint = (
                        f"{scene_hint}\n"
                        f"系统已从本场景参与者列表移除当下不可登场角色：{removed_text}。"
                        "本次重写请围绕剩余存活角色推进当下情节——"
                        "不得让离场角色「登场」（不可发出当下动作、不可说出新台词、"
                        "不可作为活跃参与者）。\n"
                        f"{_offstage_reference_guidance(repairable_hit)}"
                    )
                _reset_scene_auto_repair_residue_for_attempt(sc)
                sc_meta = dict(sc.metadata_json or {})
                sc_meta["auto_repair_hint"] = _merge_auto_repair_hint(
                    "",
                    scene_hint,
                    max_chars=1600,
                )
                sc_meta["auto_repair_block_codes"] = list(repairable_hit)
                sc.metadata_json = sc_meta
            _mark_repair_started(repairable_hit)
            await session.flush()
            logger.info(
                "chapter %d: stored write-safety auto-repair reset %d scenes and %d current drafts",
                chapter.chapter_number,
                len(scenes),
                reset_draft_count,
            )
            return True, repairable_hit
        # code stored but not repairable — fall through to normal path

    metadata_codes = tuple(
        str(c)
        for c in (
            chapter_meta.get("auto_repair_last_block_codes")
            or chapter_meta.get("quality_gate_block_codes")
            or (
                [chapter_meta.get("production_block_code")]
                if chapter_meta.get("production_block_code")
                else []
            )
        )
        if c
    )
    metadata_codes = tuple(
        dict.fromkeys((*metadata_codes, *_metadata_external_quality_codes(chapter_meta)))
    )
    latest_payload = dict(getattr(latest_report, "report_json", None) or {})
    latest_block_codes: tuple[str, ...] = tuple(
        str(c) for c in (latest_payload.get("blocking_codes") or ()) if c
    )
    length_payload = await _current_draft_length_payload()
    if not length_payload:
        length_payload = latest_payload.get("length_stability")
    if not isinstance(length_payload, Mapping):
        length_payload = {}

    if metadata_codes:
        if latest_block_codes:
            # Blocker-drift guard: retention/audit codes are re-derived on
            # every assembly (always fresh), but plain quality codes in
            # metadata are only current when the LATEST report still lists
            # them. Carrying stale quality codes made repair rounds chase
            # already-resolved findings instead of converging.
            try:
                from bestseller.services.retention_safety_gate import (
                    AUTO_REPAIR_RETENTION_CODES as _fresh_retention_codes,
                    RETENTION_AUDIT_SOFT_CODES as _fresh_audit_codes,
                )

                _always_fresh = set(_fresh_retention_codes) | set(_fresh_audit_codes)
            except Exception:
                _always_fresh = set()
            _latest_set = set(latest_block_codes)
            _stale = [
                c
                for c in metadata_codes
                if c not in _always_fresh and c not in _latest_set
            ]
            if _stale:
                logger.info(
                    "chapter %d: dropping %d stale repair code(s) no longer in "
                    "latest quality report: %s",
                    chapter.chapter_number,
                    len(_stale),
                    _stale,
                )
            metadata_codes = tuple(
                c for c in metadata_codes if c not in set(_stale)
            )
        combined_codes = tuple(dict.fromkeys((*metadata_codes, *latest_block_codes)))
        combined_codes = _drop_conflicting_length_repair_codes(
            combined_codes,
            length_payload=length_payload,
        )
        repairable_hit = tuple(
            c for c in combined_codes if _canonical_repair_code(c) in repair_set
        )
        has_length_repair = any(
            _canonical_repair_code(code) in {"BLOCK_LOW", "BLOCK_HIGH"}
            for code in repairable_hit
        )
        if repairable_hit and not has_length_repair:
            playbook_hint = render_quality_repair_playbooks(repairable_hit)
            hint_text = (
                _retention_hint_for_codes(repairable_hit)
                or playbook_hint
                or "\n".join(
                    f"本章触发 {code}，请按对应质量门约束重写。"
                    for code in repairable_hit
                )
            )
            strict_retention_hint = str(
                chapter_meta.get("retention_retry_strict_prompt") or ""
            ).strip()
            if strict_retention_hint:
                hint_text = f"{hint_text}\n{strict_retention_hint}"
            hint_text = (
                f"{hint_text}\n{_chapter_auto_repair_length_contract(project, chapter)}"
            )
            scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number.asc())
                )
            )
            scenes_to_reset = select_scenes_for_auto_repair(scenes, repairable_hit)
            if len(scenes_to_reset) < len(scenes):
                logger.info(
                    "chapter %d: positional repair codes %s — resetting %d/%d "
                    "scene(s), preserving the rest",
                    chapter.chapter_number,
                    list(repairable_hit),
                    len(scenes_to_reset),
                    len(scenes),
                )
            reset_draft_count = 0
            for sc in scenes_to_reset:
                # WS-C3: per-scene auto-repair hard cap (metadata-code path).
                # See the matching comment in the write-safety path above.
                if scene_should_skip_auto_repair_reset(sc, block_codes=repairable_hit):
                    logger.info(
                        "chapter %d scene %d: per-scene cap reached (metadata path); "
                        "skipping reset, keeping prior draft (auto_accepted_with_debt)",
                        chapter.chapter_number,
                        sc.scene_number,
                    )
                    continue
                claim_scene_auto_repair_attempt(sc, pass_id=attempt_number)
                sc.status = SceneStatus.NEEDS_REWRITE.value
                result = await session.execute(
                    update(SceneDraftVersionModel)
                    .where(
                        SceneDraftVersionModel.scene_card_id == sc.id,
                        SceneDraftVersionModel.is_current.is_(True),
                    )
                    .values(is_current=False)
                )
                try:
                    reset_draft_count += int(result.rowcount or 0)
                except Exception:
                    logger.debug(
                        "chapter %d scene %d: current draft reset count unavailable",
                        chapter.chapter_number,
                        sc.scene_number,
                        exc_info=True,
                    )
                _reset_scene_auto_repair_residue_for_attempt(sc)
                sc_meta = dict(sc.metadata_json or {})
                sc_meta["auto_repair_hint"] = _merge_auto_repair_hint(
                    "",
                    hint_text,
                    max_chars=1600,
                )
                sc_meta["auto_repair_block_codes"] = list(repairable_hit)
                sc.metadata_json = sc_meta
            _mark_repair_started(repairable_hit)
            await session.flush()
            logger.info(
                "chapter %d: metadata auto-repair reset %d scenes and %d current drafts",
                chapter.chapter_number,
                len(scenes_to_reset),
                reset_draft_count,
            )
            return True, repairable_hit
        if latest_report is None:
            logger.info(
                "chapter %d: metadata block codes %s are not in repair allowlist %s",
                chapter.chapter_number,
                list(combined_codes),
                sorted(repair_set),
            )
            return False, combined_codes

    if latest_report is None:
        return False, ()

    payload = latest_payload
    block_codes: tuple[str, ...] = tuple(
        dict.fromkeys(
            (
                *metadata_codes,
                *(str(c) for c in (payload.get("blocking_codes") or ()) if c),
            )
        )
    )
    block_codes = _drop_conflicting_length_repair_codes(
        block_codes,
        length_payload=length_payload,
    )
    if not block_codes:
        return False, ()

    repairable_hit = tuple(
        c for c in block_codes if _canonical_repair_code(c) in repair_set
    )
    if not repairable_hit:
        logger.info(
            "chapter %d: block codes %s are not in repair allowlist %s — "
            "leaving chapter in 'blocked' state for manual review",
            chapter.chapter_number,
            list(block_codes),
            sorted(repair_set),
        )
        return False, block_codes

    # Human-readable remediation hint — persisted under
    # ``scene.metadata_json["auto_repair_hint"]``.  Downstream the scene
    # writer picks this up and injects it into the rewrite reference block.
    canonical_hits = {_canonical_repair_code(code) for code in repairable_hit}
    hint_fragments: list[str] = []

    # ── Progressive repair strategy ──
    # Attempt 1: gentle hint-based guidance
    # Attempt 2: aggressive concrete instructions with explicit targets
    # Attempt 3+: maximum intervention — smallest/strongest budget change;
    # still fail closed if the gate remains blocked.
    _attempt = max(1, int(attempt_number))
    _length_meta = payload.get("length_stability") or {}
    try:
        _length_wc = int(_length_meta.get("word_count") or 0)
    except (TypeError, ValueError):
        _length_wc = 0
    try:
        _length_target = int(_length_meta.get("target_words") or 0)
    except (TypeError, ValueError):
        _length_target = 0
    try:
        _length_min = int(_length_meta.get("min_words") or 0)
    except (TypeError, ValueError):
        _length_min = 0
    try:
        _length_max = int(_length_meta.get("max_words") or 0)
    except (TypeError, ValueError):
        _length_max = 0
    _length_budget_note = ""
    if _length_wc and _length_target:
        _length_budget_note = (
            f"本章当前约 {_length_wc} 字，目标约 {_length_target} 字"
            + (
                f"，发布硬范围 {_length_min}-{_length_max} 字"
                if _length_min and _length_max
                else ""
            )
            + "。"
        )
    _canon_violation_details: list[str] = []
    _canon_state_regression_details: list[str] = []
    _naming_violation_details: list[str] = []
    for _violation in payload.get("violations") or ():
        if not isinstance(_violation, dict):
            continue
        _detail = str(_violation.get("detail") or "").strip()
        _code = str(_violation.get("code") or "")
        if _code == "CANON_FORBIDDEN_TERM" and _detail:
            _canon_violation_details.append(_detail)
        if _code == "CANON_STATE_REGRESSION" and _detail:
            _canon_state_regression_details.append(_detail)
        if _code == "NAMING_OUT_OF_POOL" and _detail:
            _naming_violation_details.append(_detail)

    if "BLOCK_LOW" in canonical_hits:
        if _attempt == 1:
            hint_fragments.append(
                f"{_length_budget_note}上一版本章节总字数过短，本次必须受控补写到发布硬范围内，"
                "优先补足被压缩成概述的冲突推进、人物选择、可见物证变化和必要对白。"
                "每个场景只补缺失的2-4段现场戏，禁止新增场景、支线、设定讲解或重复氛围；"
                "全文优先落在2200-3000字，超过3500字视为失败。"
            )
        elif _attempt == 2:
            hint_fragments.append(
                f"【紧急修复第2次】{_length_budget_note}上一版本章节字数严重不足。本次重写必须采用以下"
                "具体策略：\n"
                "1. 找出被写成摘要的场景，每处补2-3段正在发生的动作、证物变化或追问\n"
                "2. 只给关键对白补回应和动作，不给每句对白都追加心理活动\n"
                "3. 补足主角一次选择的代价和章末钩子的落地帧\n"
                "4. 确保章节总字数回到发布硬范围内；不得靠无关解释凑字，也不得超过3500字"
            )
        else:
            hint_fragments.append(
                f"【最终修复尝试】{_length_budget_note}章节字数不足问题持续存在。"
                "本次必须优先补足关键冲突、动作、对话和结尾牵引，使章节回到发布硬范围内。"
            )

    if "BLOCK_HIGH" in canonical_hits:
        if _attempt == 1:
            hint_fragments.append(
                f"{_length_budget_note}上一版本章节总字数过长，本次重写请压缩冗余的叙述与重复描写，"
                "优先保留冲突、决策与反转，使本场景回到 target_word_count 范围内。"
            )
        elif _attempt == 2:
            hint_fragments.append(
                f"【紧急修复第2次】{_length_budget_note}上一版本章节字数严重超标。本次重写必须：\n"
                "1. 删除所有非推进情节的环境描写（保留关键场景的氛围描写）\n"
                "2. 合并重复的心理活动段落，每个情绪变化最多一段\n"
                "3. 对话去掉多余的寒暄和客套，直接进入冲突或信息交换\n"
                "4. 确保章节总字数回到发布硬范围内，不得压缩成梗概"
            )
        else:
            hint_fragments.append(
                f"【最终修复尝试】{_length_budget_note}章节字数超标问题持续存在。"
                "本次必须只保留主冲突、关键动作、必要对白和结尾牵引，删除解释性铺陈，"
                "使章节回到发布硬范围内。"
            )

    if "DIALOG_UNPAIRED" in canonical_hits:
        if _attempt == 1:
            hint_fragments.append(
                "上一版本存在未闭合或孤立的对话标记。本次重写请检查每一句对话的"
                "开闭引号、说话人归属与上下文回应，避免留下半句对白或无回应对白。"
            )
        elif _attempt == 2:
            hint_fragments.append(
                "【紧急修复第2次】对话标记问题未解决。本次重写请逐句检查：\n"
                "1. 每句对话是否有完整的开引号「和闭引号」\n"
                "2. 每句对话是否有明确的说话人（动作描写或「某某说」）\n"
                "3. 每句对话是否有上下文回应（A 说完 B 必须接话，不能单方面结束）\n"
                "4. 如果存在独白，必须显式标注为「心想」「暗自道」等内心独白标记"
            )
        else:
            hint_fragments.append(
                "【最终修复尝试】对话标记问题持续存在。本次为最后一次重写——"
                "请只关注引号闭合这一最基础的问题，其他方面可以放松。"
            )

    if "ENDING_SENTENCE_WEAK" in canonical_hits:
        if _attempt == 1:
            hint_fragments.append(
                "章节结尾缺少明确钩子：上一版本的章节结尾缺少明确的动作或信息推进。"
                "本次重写请把最后一段改成新的威胁、"
                "发现、决定或反转，避免以平静收束、总结感想或问题已解决的句子结尾。"
            )
        elif _attempt == 2:
            hint_fragments.append(
                "【紧急修复第2次】章节结尾牵引仍然不够强。最后 200 字必须包含"
                "以下任一项：\n"
                "1. 一个突然出现的威胁或敌人（打破当前的平静状态）\n"
                "2. 一个颠覆性的发现（推翻之前认定的真相）\n"
                "3. 一个艰难的决定（让读者想知道后果）\n"
                "4. 一个强烈的反转（预期方向被彻底改变）\n"
                "禁止以「他知道，明天会更好」这类概括性总结收尾。"
            )
        else:
            hint_fragments.append(
                "【最终修复尝试】章节结尾牵引问题持续存在。本次为最后一次重写——"
                "请在最后一段加入一个悬念性的句子即可，即使不够强也将被接受。"
            )

    if "SCENE_JUMP_UNRESOLVED" in canonical_hits:
        hint_fragments.append(
            "补齐场景跳转桥：本章存在地点、时间或视角跳跃。重写时每次切换前后必须写清楚"
            "谁带着什么物证离开、经过多久、如何抵达新地点，以及上一场未解压力如何延续到下一场。"
        )

    if "REPEATED_EVENT_BEAT" in canonical_hits:
        hint_fragments.append(
            "本章重复了同一事件节拍。重写时必须合并重复桥段：第一次用于规则展示，"
            "第二次必须改成新阻力、新证据、新人物反应，或直接删除。"
        )
    if (
        "UNFINISHED_ARTIFACT" in canonical_hits
        or "LLM_OUTPUT_TRUNCATED" in canonical_hits
        or "SCENE_COMPLETION_INCOMPLETE" in canonical_hits
    ):
        hint_fragments.append(
            "上一版本疑似被模型输出上限截断或留下未完成正文。"
            "本次重写必须补完整个场景/章节的最后动作、对白、因果承接和情节拍结果；"
            "最后一个正文段落必须是完整句子，以句号、问号、叹号或省略号结束，"
            "并在完整句子内留下下一章点击理由。禁止只写“抬头/转身/沉默/刚要开口”"
            "这类动作准备句，也禁止以逗号、冒号、半句对白或裸汉字结尾。"
        )

    if canonical_hits & _CHARACTER_OFFSTAGE_REPAIR_CODES:
        hint_fragments.append(
            "场景中出现了当下不可登场角色的活动。请把他们从当前场景的当下动作中移除——"
            "不可让其在本章「登场」（即不可发出当下动作、不可说出新台词、"
            "不可作为活跃参与者参与场景）。\n"
            "允许的处理：旁人怀念、悲悼、提起；引用其先前说过的话或留下的文字；"
            "以遗体、画像、坟前、灵堂、信物、远闻线索等形态被提及；"
            "或仅在显式标注的回忆／闪回／祭奠／梦境／幻象场景中出现。"
        )
    if "CANON_FORBIDDEN_TERM" in canonical_hits:
        _canon_detail_note = (
            "具体命中：" + "；".join(_canon_violation_details) + "。"
            if _canon_violation_details
            else ""
        )
        hint_fragments.append(
            f"上一版本混入了已禁止的旧设定/非正典词。{_canon_detail_note}"
            "本次重写必须删除这些词及其关联体系，"
            "改用当前项目设定中的正典称谓和规则；不要解释旧设定、不要把旧体系合理化，"
            "只保留当前章节需要的信息和冲突。"
        )
    if "CANON_STATE_REGRESSION" in canonical_hits:
        _canon_state_detail_note = (
            "具体命中：" + "；".join(_canon_state_regression_details) + "。"
            if _canon_state_regression_details
            else ""
        )
        hint_fragments.append(
            f"上一版本把已经锁定的正典人物状态、亲属关系或事件顺序写回了旧版本。"
            f"{_canon_state_detail_note}"
            "本次重写必须按当前正典修正这些称谓和关系：不得把父亲写成爷爷/祖父，"
            "不得把先祖、父亲、爷爷等身份混用；不要解释错误版本，直接用正确关系重写相关句子，"
            "并保持本章核心冲突、信息揭示和结尾牵引继续推进。"
        )
    if "NAMING_OUT_OF_POOL" in canonical_hits:
        _naming_detail_note = (
            "具体命中：" + "；".join(_naming_violation_details) + "。"
            if _naming_violation_details
            else ""
        )
        hint_fragments.append(
            f"上一版本出现了角色池外姓名。{_naming_detail_note}"
            "本次重写必须删除这些临时姓名：如果是重要角色，改用项目角色池/本章参与者中的既有人名；"
            "如果只是路人或功能性人物，改为职务/身份称谓（如评委、助手、记者、守卫），"
            "不要再创造新的中文或英文专名。"
        )
    # Fallback so the writer still sees *something* if a new code is added
    # to the allowlist without its own phrasing.
    if not hint_fragments:
        hint_fragments.append(
            "上一版本触发了章节级质量门（{codes}），"
            "请针对性改写。".format(codes=", ".join(repairable_hit))
        )
    hint_fragments.append(_chapter_auto_repair_length_contract(project, chapter))
    repair_hint = "\n".join(hint_fragments)

    # Load scenes first (before any state mutation) so that if the query fails
    # we abort without having touched chapter.production_state.
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )

    # Length repair must mutate concrete per-scene budgets, not just append
    # prose hints.  Otherwise the writer keeps using the stale targets and
    # the auto-repair loop can observe the block without changing the
    # generation surface.
    low_scale: float = 1.0
    if "BLOCK_LOW" in canonical_hits:
        try:
            if _length_wc > 0 and _length_target > 0:
                ratio = _length_target / float(_length_wc)
                low_scale = min(1.5, max(1.05, ratio))
            else:
                low_scale = 1.20
            # Progressive: attempt 2+ uses a higher floor so the writer
            # gets a stronger deterministic push to write longer scenes.
            if _attempt >= 2:
                low_scale = max(low_scale, 1.30)
            if _attempt >= 3:
                low_scale = max(low_scale, 1.50)
        except Exception:
            low_scale = 1.20 if _attempt == 1 else 1.40

    scene_count = max(len(scenes), 1)
    low_scene_target_floor = 0
    scene_sum_cap_per_scene = 12_000
    if _length_target > 0:
        try:
            _scene_sum_max = chapter_scene_budget_sum_thresholds(_length_target)[1]
            scene_sum_cap_per_scene = max(250, int(math.floor(_scene_sum_max / scene_count)))
        except Exception:
            scene_sum_cap_per_scene = 12_000
    if "BLOCK_LOW" in canonical_hits:
        try:
            if _length_target > 0:
                floor_multiplier = 1.05 if _attempt == 1 else 1.15
                if _attempt >= 3:
                    floor_multiplier = 1.25
                low_scene_target_floor = max(
                    low_scene_target_floor,
                    int(math.ceil((_length_target / scene_count) * floor_multiplier)),
                )
            if _length_min > 0:
                low_scene_target_floor = max(
                    low_scene_target_floor,
                    int(math.ceil((_length_min / scene_count) * 1.05)),
                )
            if scene_sum_cap_per_scene > 0:
                low_scene_target_floor = min(low_scene_target_floor, scene_sum_cap_per_scene)
        except Exception:
            low_scene_target_floor = 0

    def _scene_target_cap(*, floor: int = 0) -> int:
        """Keep auto-repair scene budgets within DB-safe, publishable bounds."""

        cap = scene_sum_cap_per_scene
        if _length_target <= 0:
            cap = 12_000
        return max(250, min(12_000, max(cap, min(floor, cap))))

    high_scale: float = 1.0
    if "BLOCK_HIGH" in canonical_hits:
        try:
            if _length_wc > 0 and _length_target > 0:
                high_scale = _length_target / float(_length_wc)
            else:
                high_scale = 0.75
            if _attempt >= 2:
                high_scale *= 0.85
            if _attempt >= 3:
                high_scale *= 0.75
            high_scale = max(0.35, min(0.95, high_scale))
        except Exception:
            high_scale = 0.75 if _attempt == 1 else 0.60

    # Reset every scene of this chapter to NEEDS_REWRITE and write the
    # auto-repair hint into ``metadata_json`` — keeping any prior hint so
    # successive repair cycles don't wipe upstream context.
    dead_names = (
        await _load_dead_character_names_before_chapter(
            session,
            project.id,
            chapter.chapter_number,
        )
        if canonical_hits & _CHARACTER_OFFSTAGE_REPAIR_CODES
        else frozenset()
    )
    reset_count = 0
    reset_draft_count = 0
    _scenes_to_reset = select_scenes_for_auto_repair(scenes, repairable_hit)
    if len(_scenes_to_reset) < len(scenes):
        logger.info(
            "chapter %d: positional repair codes %s — resetting %d/%d scene(s), "
            "preserving the rest",
            chapter.chapter_number,
            list(repairable_hit),
            len(_scenes_to_reset),
            len(scenes),
        )
    for sc in _scenes_to_reset:
        # WS-C3: per-scene auto-repair hard cap (length-stability path).
        # Identical contract to the other two reset sites in
        # ``maybe_prepare_chapter_auto_repair``; documented in one place
        # above to keep the three call sites in sync.
        if scene_should_skip_auto_repair_reset(sc, block_codes=canonical_hits):
            logger.info(
                "chapter %d scene %d: per-scene cap reached (length-stability path); "
                "skipping reset, keeping prior draft (auto_accepted_with_debt)",
                chapter.chapter_number,
                sc.scene_number,
            )
            continue
        claim_scene_auto_repair_attempt(sc, pass_id=attempt_number)
        sc.status = SceneStatus.NEEDS_REWRITE.value
        result = await session.execute(
            update(SceneDraftVersionModel)
            .where(
                SceneDraftVersionModel.scene_card_id == sc.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
            .values(is_current=False)
        )
        try:
            reset_draft_count += int(result.rowcount or 0)
        except Exception:
            logger.debug(
                "chapter %d scene %d: current draft reset count unavailable",
                chapter.chapter_number,
                sc.scene_number,
                exc_info=True,
            )
        removed = _filter_dead_scene_participants(sc, dead_names)
        scene_hint = repair_hint
        if removed:
            removed_text = "、".join(removed)
            scene_hint = (
                f"{repair_hint}\n"
                f"系统已从本场景参与者列表移除当下不可登场角色：{removed_text}。"
                "本次重写请围绕剩余存活角色推进当下情节——"
                "不得让离场角色「登场」（不可发出当下动作、不可说出新台词、"
                "不可作为活跃参与者）。\n"
                f"{_offstage_reference_guidance(repairable_hit)}"
            )
        restored_base_target = _reset_scene_auto_repair_residue_for_attempt(sc)
        meta = dict(sc.metadata_json or {})
        meta["auto_repair_hint"] = _merge_auto_repair_hint(
            "",
            scene_hint,
            max_chars=1600,
        )
        meta["auto_repair_block_codes"] = list(repairable_hit)
        sc.metadata_json = meta
        if "BLOCK_LOW" in canonical_hits and low_scale > 1.0:
            try:
                original_target = int(sc.target_word_count or 0)
                meta_original_target = int(
                    (meta.get("auto_repair_original_target_word_count") or 0)
                    if isinstance(meta, Mapping)
                    else 0
                )
                base_target = meta_original_target if meta_original_target > 0 else original_target
                if restored_base_target and restored_base_target > 0:
                    base_target = restored_base_target
                if base_target > 0:
                    cap = _scene_target_cap(floor=low_scene_target_floor)
                    raw_adjusted_target = max(
                        int(round(base_target * low_scale)),
                        low_scene_target_floor,
                    )
                    adjusted_target = min(raw_adjusted_target, cap)
                    if base_target <= cap:
                        adjusted_target = max(base_target, adjusted_target)
                    sc.target_word_count = adjusted_target
                    meta = dict(sc.metadata_json or {})
                    meta.setdefault(
                        "auto_repair_original_target_word_count",
                        base_target,
                    )
                    meta["auto_repair_adjusted_target_word_count"] = int(
                        sc.target_word_count
                    )
                    meta["auto_repair_length_scale"] = round(low_scale, 4)
                    if raw_adjusted_target > cap or original_target > cap:
                        meta["auto_repair_target_word_count_clamped"] = True
                        meta["auto_repair_scene_target_cap"] = cap
                    if low_scene_target_floor > 0:
                        meta["auto_repair_min_scene_target_floor"] = (
                            low_scene_target_floor
                        )
                    sc.metadata_json = meta
            except Exception:
                logger.debug(
                    "chapter %d scene %d: target_word_count bump failed "
                    "(non-fatal)",
                    chapter.chapter_number,
                    sc.scene_number,
                    exc_info=True,
                )
        if "BLOCK_HIGH" in canonical_hits and high_scale < 1.0:
            try:
                original_target = int(sc.target_word_count or 0)
                min_scene_target = 250
                if _length_min > 0:
                    min_scene_target = max(
                        250,
                        int(math.ceil(_length_min / scene_count)),
                    )
                if original_target > 0:
                    min_scene_target = min(
                        min_scene_target,
                        max(250, int(round(original_target * 0.8))),
                    )
                    cap = _scene_target_cap(floor=min_scene_target)
                    scaled_target = max(
                        min_scene_target,
                        int(round(original_target * high_scale)),
                    )
                    if _length_target > 0:
                        per_scene_ceiling = max(
                            min_scene_target,
                            int(round((_length_target / scene_count) * 0.95)),
                        )
                        scaled_target = min(scaled_target, per_scene_ceiling)
                    sc.target_word_count = min(original_target, scaled_target, cap)
                    meta = dict(sc.metadata_json or {})
                    meta.setdefault(
                        "auto_repair_original_target_word_count",
                        original_target,
                    )
                    meta["auto_repair_adjusted_target_word_count"] = int(
                        sc.target_word_count
                    )
                    meta["auto_repair_length_scale"] = round(high_scale, 4)
                    if original_target > cap or scaled_target > cap:
                        meta["auto_repair_target_word_count_clamped"] = True
                        meta["auto_repair_scene_target_cap"] = cap
                    sc.metadata_json = meta
            except Exception:
                logger.debug(
                    "chapter %d scene %d: target_word_count trim failed "
                    "(non-fatal)",
                    chapter.chapter_number,
                    sc.scene_number,
                    exc_info=True,
                )
        reset_count += 1

    # Mark the previous report row as consumed so `regen_attempts` telemetry
    # doesn't double-count.
    try:
        latest_report.regen_attempts = int(latest_report.regen_attempts or 0) + 1
    except Exception:
        logger.debug(
            "chapter %d: regen_attempts bump failed (non-fatal)",
            chapter.chapter_number,
            exc_info=True,
        )

    # ONLY reset production_state after all scene mutations succeed.  The
    # column is NOT NULL, so use the normal "pending" state instead of None.
    _mark_repair_started(repairable_hit)

    # Flush everything in one shot after all state changes are internally
    # consistent.
    await session.flush()
    logger.info(
        "chapter %d: auto-repair triggered (%d scenes reset, %d current drafts reset) — block codes %s",
        chapter.chapter_number,
        reset_count,
        reset_draft_count,
        list(repairable_hit),
    )
    return True, block_codes

"""Loader for ``config/quality_gates.yaml``.

This sits outside ``AppSettings`` on purpose: Phase 1 ships gate config as a
separate YAML so operators can toggle individual checks without redeploying
``default.yaml``. The structure is intentionally Phase-sliced (``l1_…``,
``l4_…``) so later phases can add new blocks without renumbering existing
ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from bestseller.services.chapter_validator import (
    CliffhangerRotationCheck,
    DialogIntegrityCheck,
    POVLockCheck,
)
from bestseller.services.output_validator import (
    EntityDensityCheck,
    LanguageSignatureCheck,
    LengthEnvelopeCheck,
    NamingConsistencyCheck,
    OutputValidator,
)
from bestseller.services.write_gate import DEFAULT_GATE_CONFIG, GateConfig, GateMode


DEFAULT_QUALITY_GATES_PATH = Path("config/quality_gates.yaml")


@dataclass(frozen=True)
class L2Config:
    """L2 BibleCompletenessGate config — Phase 2 feature.

    Phase 1 ships with ``enabled: false`` so the gate runs in pure audit
    mode (log findings, never block). Phase 2 flips to ``enabled: true``
    with ``regen_budget`` driving the bible rewrite loop.
    """

    enabled: bool = False
    regen_budget: int = 3
    quirk_min: int = 3
    antagonist_jaccard_threshold: float = 0.4
    world_taxonomy_enabled: bool = True
    naming_pool_multiplier: float = 2.0


@dataclass(frozen=True)
class L3Config:
    """L3 PromptConstructor config — diversity injection knobs.

    The prompt constructor reads these to decide how much prior-chapter
    context to paste, how wide the hot-vocab window is, and how many
    banned words to list. Phase 1 ships the stub enabled but only the
    diversity-constraints section fully wired — bible/scene slots are
    caller-supplied.
    """

    enabled: bool = True
    prior_chapter_tail_chars: int = 800
    hot_vocab_window_chapters: int = 5
    hot_vocab_top_n: int = 20
    hot_vocab_min_count: int = 3
    no_repeat_within_openings: int = 3


@dataclass(frozen=True)
class L4Config:
    enabled: bool = True
    cjk_in_en_ratio_max: float = 0.02
    latin_in_zh_ratio_max: float = 0.10
    length_envelope_enabled: bool = True
    naming_consistency_enabled: bool = True
    naming_consistency_frequency_floor: int = 2
    entity_density_enabled: bool = True
    entity_density_head_lines: int = 150
    entity_density_max_entities: int = 5


@dataclass(frozen=True)
class L45Config:
    enabled: bool = True
    budget_per_chapter: int = 3
    global_regen_total_budget: int = 12


@dataclass(frozen=True)
class L5Config:
    """L5 chapter-assembly checks (``DialogIntegrity`` + ``POVLock``).

    L5 runs only at chapter scope — individual scene drafts don't have the
    cross-scene context these checks need. The pipeline wires them alongside
    L4 when an assembled chapter is validated.
    """

    enabled: bool = True
    dialog_integrity_enabled: bool = True
    pov_lock_enabled: bool = True
    pov_lock_sample_size: int = 40
    # Close-third / omniscient novels trip on ≥N drift sentences (absolute).
    pov_lock_min_drift_sentences_close_third: int = 3
    # First-person novels trip only when ≥R ratio of sampled sentences
    # drift — first-person legitimately describes other characters in
    # third-person, so absolute counts false-fire.
    pov_lock_min_drift_ratio_first: float = 0.5
    cliffhanger_rotation_enabled: bool = True


@dataclass(frozen=True)
class L7Config:
    enabled: bool = True
    auto_repair: bool = False
    schedule_cron: str = "0 */6 * * *"


@dataclass(frozen=True)
class QualityGatesConfig:
    l1_enabled: bool = True
    l2: L2Config = field(default_factory=L2Config)
    l3: L3Config = field(default_factory=L3Config)
    l4: L4Config = field(default_factory=L4Config)
    l4_5: L45Config = field(default_factory=L45Config)
    l5: L5Config = field(default_factory=L5Config)
    l6_enabled: bool = True
    l6_gate: GateConfig = DEFAULT_GATE_CONFIG
    l7: L7Config = field(default_factory=L7Config)


def _as_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _as_gate_mode(value: Any, default: GateMode) -> GateMode:
    if value in ("block", "audit_only"):
        return value  # type: ignore[return-value]
    return default


def _build_gate_config(l6_raw: dict[str, Any]) -> GateConfig:
    default_mode = _as_gate_mode(l6_raw.get("default"), DEFAULT_GATE_CONFIG.default)
    mapping_raw = _as_dict(l6_raw.get("mode_by_violation"))
    resolved: dict[str, GateMode] = dict(DEFAULT_GATE_CONFIG.mode_by_violation)
    for code, mode in mapping_raw.items():
        if not isinstance(code, str):
            continue
        resolved[code] = _as_gate_mode(mode, default_mode)
    return GateConfig(mode_by_violation=resolved, default=default_mode)


def load_quality_gates_config(
    path: Path | None = None,
) -> QualityGatesConfig:
    """Parse ``config/quality_gates.yaml`` into a typed config tree.

    A missing file returns defaults — pipelines work out-of-the-box with
    Phase 1 gates enabled. Call sites should use ``get_quality_gates_config``
    which memoizes the result.
    """

    effective = path or DEFAULT_QUALITY_GATES_PATH
    raw: dict[str, Any] = {}
    if effective.exists():
        parsed = yaml.safe_load(effective.read_text(encoding="utf-8")) or {}
        if isinstance(parsed, dict):
            raw = parsed

    l1 = _as_dict(raw.get("l1_invariants"))
    l2 = _as_dict(raw.get("l2_bible_gate"))
    l2_checks = _as_dict(l2.get("checks"))
    l2_quirk = _as_dict(l2_checks.get("quirk_slot_requirement"))
    l2_antag = _as_dict(l2_checks.get("antagonist_motive_ledger"))
    l2_world = _as_dict(l2_checks.get("world_taxonomy_uniqueness"))
    l2_naming = _as_dict(l2_checks.get("naming_pool_size"))
    l3 = _as_dict(raw.get("l3_prompt_constructor"))
    l4 = _as_dict(raw.get("l4_output_validator"))
    l4_checks = _as_dict(l4.get("checks"))
    l4_lang = _as_dict(l4_checks.get("language_signature"))
    l4_length = _as_dict(l4_checks.get("length_envelope"))
    l4_naming = _as_dict(l4_checks.get("naming_consistency"))
    l4_entity = _as_dict(l4_checks.get("entity_density"))
    l4_5 = _as_dict(raw.get("l4_5_regen_loop"))
    l5 = _as_dict(raw.get("l5_chapter_validator"))
    l5_checks = _as_dict(l5.get("checks"))
    l5_dialog = _as_dict(l5_checks.get("dialog_integrity"))
    l5_pov = _as_dict(l5_checks.get("pov_lock"))
    l6 = _as_dict(raw.get("l6_write_gate"))
    l7 = _as_dict(raw.get("l7_continuous_audit"))

    return QualityGatesConfig(
        l1_enabled=bool(l1.get("enabled", True)),
        l2=L2Config(
            enabled=bool(l2.get("enabled", False)),
            regen_budget=int(l2.get("regen_budget", 3)),
            quirk_min=int(l2_quirk.get("min_quirks", 3)),
            antagonist_jaccard_threshold=float(
                l2_antag.get("jaccard_threshold", 0.4)
            ),
            world_taxonomy_enabled=bool(l2_world.get("enabled", True)),
            naming_pool_multiplier=float(l2_naming.get("multiplier", 2.0)),
        ),
        l3=L3Config(
            enabled=bool(l3.get("enabled", True)),
            prior_chapter_tail_chars=int(l3.get("prior_chapter_tail_chars", 800)),
            hot_vocab_window_chapters=int(l3.get("hot_vocab_window_chapters", 5)),
            hot_vocab_top_n=int(l3.get("hot_vocab_top_n", 20)),
            hot_vocab_min_count=int(l3.get("hot_vocab_min_count", 3)),
            no_repeat_within_openings=int(l3.get("no_repeat_within_openings", 3)),
        ),
        l4=L4Config(
            enabled=bool(l4.get("enabled", True)),
            cjk_in_en_ratio_max=float(l4_lang.get("cjk_in_en_ratio_max", 0.02)),
            latin_in_zh_ratio_max=float(l4_lang.get("latin_in_zh_ratio_max", 0.10)),
            length_envelope_enabled=bool(l4_length.get("enabled", True)),
            naming_consistency_enabled=bool(l4_naming.get("enabled", True)),
            naming_consistency_frequency_floor=int(l4_naming.get("frequency_floor", 2)),
            entity_density_enabled=bool(l4_entity.get("enabled", True)),
            entity_density_head_lines=int(l4_entity.get("head_lines", 150)),
            entity_density_max_entities=int(l4_entity.get("max_entities", 5)),
        ),
        l4_5=L45Config(
            enabled=bool(l4_5.get("enabled", True)),
            budget_per_chapter=int(l4_5.get("budget_per_chapter", 3)),
            global_regen_total_budget=int(l4_5.get("global_regen_total_budget", 12)),
        ),
        l5=L5Config(
            enabled=bool(l5.get("enabled", True)),
            dialog_integrity_enabled=bool(l5_dialog.get("enabled", True)),
            pov_lock_enabled=bool(l5_pov.get("enabled", True)),
            pov_lock_sample_size=int(l5_pov.get("sample_size", 40)),
            # Accept both the new explicit keys and the legacy
            # ``min_drift_sentences`` key (used by old YAML) as a default
            # for ``_close_third``. Keeps existing config files working.
            pov_lock_min_drift_sentences_close_third=int(
                l5_pov.get(
                    "min_drift_sentences_close_third",
                    l5_pov.get("min_drift_sentences", 3),
                )
            ),
            pov_lock_min_drift_ratio_first=float(
                l5_pov.get("min_drift_ratio_first", 0.5)
            ),
            cliffhanger_rotation_enabled=bool(
                _as_dict(l5_checks.get("cliffhanger_rotation")).get("enabled", True)
            ),
        ),
        l6_enabled=bool(l6.get("enabled", True)),
        l6_gate=_build_gate_config(l6),
        l7=L7Config(
            enabled=bool(l7.get("enabled", True)),
            auto_repair=bool(l7.get("auto_repair", False)),
            schedule_cron=str(l7.get("schedule_cron", "0 */6 * * *")),
        ),
    )


@lru_cache(maxsize=1)
def get_quality_gates_config() -> QualityGatesConfig:
    return load_quality_gates_config()


def reset_quality_gates_cache() -> None:
    get_quality_gates_config.cache_clear()


def build_validator_from_config(cfg: QualityGatesConfig) -> OutputValidator:
    """Instantiate the chapter-scope ``OutputValidator`` respecting per-check
    enable flags.

    Combines L4 (language signature, length, naming, entity density) with
    L5 (dialog integrity, POV lock). L5 checks gracefully handle scene-scope
    callers by sampling the text they're given — scope-aware exemption
    happens inside the individual check (e.g., ``EntityDensityCheck`` and
    ``LengthEnvelopeCheck`` both self-exempt when ``ctx.scope == "scene"``
    or ``ctx.chapter_no != 1``).
    """

    checks: list[Any] = []
    if cfg.l4.enabled:
        checks.append(
            LanguageSignatureCheck(
                cjk_in_en_ratio_max=cfg.l4.cjk_in_en_ratio_max,
                latin_in_zh_ratio_max=cfg.l4.latin_in_zh_ratio_max,
            )
        )
        if cfg.l4.length_envelope_enabled:
            checks.append(LengthEnvelopeCheck())
        if cfg.l4.naming_consistency_enabled:
            checks.append(
                NamingConsistencyCheck(
                    frequency_floor=cfg.l4.naming_consistency_frequency_floor,
                )
            )
        if cfg.l4.entity_density_enabled:
            checks.append(
                EntityDensityCheck(
                    head_lines=cfg.l4.entity_density_head_lines,
                    max_entities=cfg.l4.entity_density_max_entities,
                )
            )
    if cfg.l5.enabled:
        if cfg.l5.dialog_integrity_enabled:
            checks.append(DialogIntegrityCheck())
        if cfg.l5.pov_lock_enabled:
            checks.append(
                POVLockCheck(
                    sample_size=cfg.l5.pov_lock_sample_size,
                    min_drift_sentences_close_third=cfg.l5.pov_lock_min_drift_sentences_close_third,
                    min_drift_ratio_first=cfg.l5.pov_lock_min_drift_ratio_first,
                )
            )
        if cfg.l5.cliffhanger_rotation_enabled:
            checks.append(CliffhangerRotationCheck())
    return OutputValidator(checks)

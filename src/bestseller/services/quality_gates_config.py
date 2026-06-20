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

from bestseller.services.ai_flavor_gate import AiFlavorGateConfig
from bestseller.services.chapter_validator import (
    CanonForbiddenTermCheck,
    CanonStateRegressionCheck,
    CliffhangerRotationCheck,
    DialogIntegrityCheck,
    EndingSentenceImpactCheck,
    GoldenThreeChapterCheck,
    HypeDiversityCheck,
    HypeOccurrenceCheck,
    LineGapCheck,
    POVLockCheck,
    RepeatedEventBeatCheck,
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
    # Grandfathering window: stance flips / resurrections in chapters
    # finalized before this number are audit_only even when enabled.
    only_enforce_from_chapter: int | None = None
    stance_flip_justification_enabled: bool = True


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
    repeated_beat_enabled: bool = True
    canon_guardrails_enabled: bool = True
    cliffhanger_rotation_enabled: bool = True


@dataclass(frozen=True)
class L7Config:
    enabled: bool = True
    auto_repair: bool = False
    schedule_cron: str = "0 */6 * * *"


@dataclass(frozen=True)
class L8Config:
    """L8 Scorecard config — Phase 3 quality score (0-100).

    Enabled means per-chapter incremental updates after successful review
    plus a full recompute at end-of-autowrite. Disabled skips both.
    """

    enabled: bool = False


@dataclass(frozen=True)
class StoryPrincipleGateConfig:
    """Audit knobs for event-unit writing-principle coverage."""

    enabled: bool = True
    default: str = "audit_only"
    block_on_failure: bool = False
    min_event_cycle_roles_per_batch: int = 3
    max_same_role_streak: int = 3


@dataclass(frozen=True)
class MethodologyFrameworkConfig:
    """External writing-methodology profile and gate wiring.

    Missing config defaults to disabled so historical projects do not get
    prompt or health changes until the block is explicitly opted in.
    """

    enabled: bool = False
    profile_id: str = "plova_structured_writing_v1"
    cards_enabled: bool = True
    data_dir: str = "data/methodology_sources/plova"
    opening_three_function_enabled: bool = True
    opening_three_function_default: str = "audit_only"
    opening_three_function_block_until_chapter: int = 3
    action_scene_structure_enabled: bool = True
    action_scene_structure_default: str = "audit_only"
    chekhov_emphasis_enabled: bool = True
    chekhov_emphasis_default: str = "audit_only"
    chekhov_overdue_window_default: int = 8
    longform_chaos_enabled: bool = False
    longform_chaos_start_after_chapter: int = 30


@dataclass(frozen=True)
class OriginalityEngineConfig:
    """Wiring for the P1 Originality Engine (Voice DNA + Market Constraints +
    Reader Personas + Signature Scenes).

    When enabled, the chapter assembly path in ``pipelines.py``:
      1. Calls ``prepare_chapter_context(slug, chapter_no)``.
      2. Renders four prompt blocks (voice DNA, market constraints,
         signature scene mandate, prior persona feedback) and stamps them
         onto ``SceneWriterContextPacket`` / ``ChapterWriterContextPacket``.
      3. After the chapter draft is finalized, calls ``grade_chapter`` to
         persist persona feedback for the next chapter.

    When disabled (or when the project has no DNA/signature-plan on
    disk), the path is a no-op — behavior is identical to legacy.
    """

    enabled: bool = True
    # When False, the post-write hook is skipped — useful when an
    # external review service already runs the persona simulator and
    # you want to avoid double-persisting feedback.
    persist_persona_feedback: bool = True
    # When set, treats the project as a Mode B (ai-generated) package,
    # reading/writing under ``output/ai-generated/<slug>/`` instead of
    # ``output/<slug>/``. The pipeline auto-detects from project
    # metadata when None.
    mode_b_override: bool | None = None
    # Per-chapter text fields longer than this character cap get
    # truncated before being graded — protects against pathological
    # 100k-char outputs choking the signal builder.
    grading_text_cap_chars: int = 12_000
    # Retention repair uses its own budget because hook/signature/cast
    # failures often need more than the generic length-repair loop.
    retention_max_retries: int = 5
    retention_escalate_after: int = 3
    # 2026-05-23: per-gate disable for the new chapter-length gate so
    # integration tests with stub-length scene drafts can skip it.
    # In PRODUCTION this should stay True — short chapters are the
    # single biggest "省事感" tell.
    chapter_length_gate_enabled: bool = True
    # 2026-06-10: platform self-bootstrap. The CLI ``book bootstrap``
    # step was the only producer of signature-scene-plan.json and
    # voice-dna.json, so platform-run books never got those two prompt
    # blocks (0% hit across traces). When enabled, the pipeline lazily
    # plans signature scenes from the project's target chapter count and
    # self-extracts Voice DNA from the first accepted chapter whose text
    # reaches the minimum sample size. Existing artifacts are never
    # overwritten, so CLI-bootstrapped books are unaffected.
    auto_signature_plan: bool = True
    auto_voice_dna: bool = True
    voice_dna_min_sample_chars: int = 2000


@dataclass(frozen=True)
class ReaderQualityGateConfig:
    """Hard gates from reader-persona simulation + payoff ledger heuristics."""

    enabled: bool = True
    block_on_persona_failure: bool = True
    min_weighted_score: float = 0.62
    max_abandon_rate: float = 0.35
    min_payoff_density: float = 0.22
    # Below-soft-target (but above the raised hard floor) is advisory: the
    # 3000 hard floor is the real gate. Blocking the whole floor-target band
    # caused excessive rewrite churn.
    block_below_target_length: bool = False
    block_word_count_metadata_mismatch: bool = True
    block_chapter_duplicates: bool = True
    # Run the payoff ledger heuristic, but keep it advisory by default; the
    # persona / reader-judge payoff signal is the real hard gate.
    block_payoff_ledger: bool = False
    opening_similarity_threshold: float = 0.82
    body_similarity_threshold: float = 0.88
    require_critic_body_evidence: bool = True
    # P2 LLM reader-judge. Default OFF: enable per-project after calibration.
    # When on, feeds prose_quality_score into the persona simulator. When
    # ``reader_judge_audit_only`` is True, the score is recorded but the
    # persona hard gate keeps its existing thresholds (no behavior change).
    enable_llm_reader_judge: bool = False
    reader_judge_audit_only: bool = True
    reader_judge_text_cap_chars: int = 8000


@dataclass(frozen=True)
class ProseQualityGateConfig:
    """Anti-slop prose gates and prompt sanitization posture."""

    sanitize_prompt: bool = True
    beat_planner_enabled: bool = True
    anti_meta_enabled: bool = True
    anti_meta_severity: str = "block"
    show_dont_tell_enabled: bool = True
    show_dont_tell_severity: str = "warn"
    in_scene_ending_enabled: bool = True
    in_scene_ending_severity: str = "block"


@dataclass(frozen=True)
class NarrativeRichnessConfig:
    """Feature flags for geography/culture/ensemble/mystery/dilemma kernels."""

    enabled: bool = True
    prompt_injection_enabled: bool = True
    default_mode: str = "warn"
    strict_categories: tuple[str, ...] = (
        "历史架空",
        "古典权谋",
        "武侠群像",
        "史诗",
        "wuxia-jianghu",
        "history-strategy",
        "western_fantasy",
    )


# ---------------------------------------------------------------------------
# Phase B/C/D — webnovel-writer adoption flags (plan: shimmying-soaring-gadget).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseBLineTrackerConfig:
    """Phase B — per-chapter dominance classifier + gap validator.

    Controls ``narrative_line_tracker.classify_chapter`` wiring on the
    finalize-chapter path, plus ``LineGapCheck`` in the chapter validator.
    """

    enabled: bool = False
    # Chapters below this threshold stay in audit_only regardless of gap
    # severity — a project needs history before the gap check is meaningful.
    warmup_until_chapter: int = 10


@dataclass(frozen=True)
class PhaseCOverridesConfig:
    """Phase C — Override Contract + Debt Ledger.

    Controls ``chase_debt_ledger`` interest accrual on every chapter tick and
    the regen-loop fallback that proposes override contracts when per-chapter
    regen budget is exhausted.
    """

    enabled: bool = False
    default_interest_rate: float = 0.10
    payback_window_default: int = 10
    # Chapters before this number are exempt from override auto-sign and debt
    # accrual even when ``enabled`` is True — a gray-out so flipping Phase C on
    # does not retroactively penalize early/in-flight chapters. ``None`` means
    # enforce from chapter 1.
    only_enforce_from_chapter: int | None = None


@dataclass(frozen=True)
class PhaseDTimeConfig:
    """Phase D — Time anchor + countdown validators.

    ``regression_check_enabled`` toggles ``TimeRegressionCheck`` as a soft,
    overridable validator; ``countdown_arithmetic_enabled`` toggles
    ``CountdownArithmeticCheck`` as a hard validator. Keep both on once
    Phase D is opted-in per project.
    """

    enabled: bool = False
    regression_check_enabled: bool = True
    countdown_arithmetic_enabled: bool = True


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
    story_principle: StoryPrincipleGateConfig = field(
        default_factory=StoryPrincipleGateConfig
    )
    l7: L7Config = field(default_factory=L7Config)
    l8: L8Config = field(default_factory=L8Config)
    phase_b: PhaseBLineTrackerConfig = field(default_factory=PhaseBLineTrackerConfig)
    phase_c: PhaseCOverridesConfig = field(default_factory=PhaseCOverridesConfig)
    phase_d: PhaseDTimeConfig = field(default_factory=PhaseDTimeConfig)
    ai_flavor: AiFlavorGateConfig = field(default_factory=AiFlavorGateConfig)
    prose_quality: ProseQualityGateConfig = field(default_factory=ProseQualityGateConfig)
    narrative_richness: NarrativeRichnessConfig = field(
        default_factory=NarrativeRichnessConfig
    )
    methodology_framework: MethodologyFrameworkConfig = field(
        default_factory=MethodologyFrameworkConfig
    )
    originality_engine: OriginalityEngineConfig = field(
        default_factory=OriginalityEngineConfig
    )
    reader_quality: ReaderQualityGateConfig = field(
        default_factory=ReaderQualityGateConfig
    )


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
    l5_repeated_beat = _as_dict(l5_checks.get("repeated_beat"))
    l5_canon_guardrails = _as_dict(l5_checks.get("canon_guardrails"))
    l6 = _as_dict(raw.get("l6_write_gate"))
    story_principle = _as_dict(raw.get("story_principle_gate"))
    methodology_framework = _as_dict(raw.get("methodology_framework"))
    originality_engine = _as_dict(raw.get("originality_engine"))
    reader_quality = _as_dict(raw.get("reader_quality_gate"))
    prose_quality = _as_dict(raw.get("prose_quality"))
    narrative_richness = _as_dict(raw.get("narrative_richness"))
    l7 = _as_dict(raw.get("l7_continuous_audit"))
    l8 = _as_dict(raw.get("l8_scorecard"))
    l2_stance = _as_dict(l2_checks.get("stance_flip_justification"))
    _only_enforce_raw = l2.get("only_enforce_from_chapter")
    _only_enforce: int | None = None
    if isinstance(_only_enforce_raw, int) and _only_enforce_raw > 0:
        _only_enforce = _only_enforce_raw

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
            only_enforce_from_chapter=_only_enforce,
            stance_flip_justification_enabled=bool(
                l2_stance.get("enabled", True)
            ),
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
            repeated_beat_enabled=bool(l5_repeated_beat.get("enabled", True)),
            canon_guardrails_enabled=bool(l5_canon_guardrails.get("enabled", True)),
            cliffhanger_rotation_enabled=bool(
                _as_dict(l5_checks.get("cliffhanger_rotation")).get("enabled", True)
            ),
        ),
        l6_enabled=bool(l6.get("enabled", True)),
        l6_gate=_build_gate_config(l6),
        story_principle=StoryPrincipleGateConfig(
            enabled=bool(story_principle.get("enabled", True)),
            default=str(story_principle.get("default", "audit_only")),
            block_on_failure=bool(story_principle.get("block_on_failure", False)),
            min_event_cycle_roles_per_batch=int(
                story_principle.get("min_event_cycle_roles_per_batch", 3)
            ),
            max_same_role_streak=int(story_principle.get("max_same_role_streak", 3)),
        ),
        l7=L7Config(
            enabled=bool(l7.get("enabled", True)),
            auto_repair=bool(l7.get("auto_repair", False)),
            schedule_cron=str(l7.get("schedule_cron", "0 */6 * * *")),
        ),
        l8=L8Config(
            enabled=bool(l8.get("enabled", False)),
        ),
        phase_b=_build_phase_b(_as_dict(raw.get("phase_b_line_tracker"))),
        phase_c=_build_phase_c(_as_dict(raw.get("phase_c_overrides"))),
        phase_d=_build_phase_d(_as_dict(raw.get("phase_d_time"))),
        ai_flavor=_build_ai_flavor(_as_dict(raw.get("ai_flavor_gate"))),
        prose_quality=_build_prose_quality(prose_quality),
        narrative_richness=_build_narrative_richness(narrative_richness),
        methodology_framework=_build_methodology_framework(methodology_framework),
        originality_engine=_build_originality_engine(originality_engine),
        reader_quality=_build_reader_quality_gate(reader_quality),
    )


def _build_reader_quality_gate(raw: dict[str, Any]) -> ReaderQualityGateConfig:
    return ReaderQualityGateConfig(
        enabled=_safe_bool(raw.get("enabled"), True),
        block_on_persona_failure=_safe_bool(raw.get("block_on_persona_failure"), True),
        min_weighted_score=float(raw.get("min_weighted_score", 0.62)),
        max_abandon_rate=float(raw.get("max_abandon_rate", 0.35)),
        min_payoff_density=float(raw.get("min_payoff_density", 0.22)),
        block_below_target_length=_safe_bool(raw.get("block_below_target_length"), False),
        block_word_count_metadata_mismatch=_safe_bool(
            raw.get("block_word_count_metadata_mismatch"), True
        ),
        block_chapter_duplicates=_safe_bool(raw.get("block_chapter_duplicates"), True),
        block_payoff_ledger=_safe_bool(raw.get("block_payoff_ledger"), False),
        opening_similarity_threshold=float(
            raw.get("opening_similarity_threshold", 0.82)
        ),
        body_similarity_threshold=float(raw.get("body_similarity_threshold", 0.88)),
        require_critic_body_evidence=_safe_bool(
            raw.get("require_critic_body_evidence"), True
        ),
        enable_llm_reader_judge=_safe_bool(raw.get("enable_llm_reader_judge"), False),
        reader_judge_audit_only=_safe_bool(raw.get("reader_judge_audit_only"), True),
        reader_judge_text_cap_chars=max(
            500, _safe_int(raw.get("reader_judge_text_cap_chars"), 8000)
        ),
    )


def _build_phase_b(raw: dict[str, Any]) -> PhaseBLineTrackerConfig:
    warmup_raw = raw.get("warmup_until_chapter", 10)
    try:
        warmup = int(warmup_raw) if warmup_raw is not None else 10
    except (TypeError, ValueError):
        warmup = 10
    return PhaseBLineTrackerConfig(
        enabled=bool(raw.get("enabled", False)),
        warmup_until_chapter=max(0, warmup),
    )


def _build_phase_c(raw: dict[str, Any]) -> PhaseCOverridesConfig:
    rate_raw = raw.get("default_interest_rate", 0.10)
    try:
        rate = float(rate_raw) if rate_raw is not None else 0.10
    except (TypeError, ValueError):
        rate = 0.10
    window_raw = raw.get("payback_window_default", 10)
    try:
        window = int(window_raw) if window_raw is not None else 10
    except (TypeError, ValueError):
        window = 10
    only_from_raw = raw.get("only_enforce_from_chapter")
    only_from: int | None
    try:
        only_from = int(only_from_raw) if only_from_raw is not None else None
    except (TypeError, ValueError):
        only_from = None
    if only_from is not None and only_from < 1:
        only_from = None
    return PhaseCOverridesConfig(
        enabled=bool(raw.get("enabled", False)),
        default_interest_rate=max(0.0, rate),
        payback_window_default=max(1, window),
        only_enforce_from_chapter=only_from,
    )


def _build_phase_d(raw: dict[str, Any]) -> PhaseDTimeConfig:
    return PhaseDTimeConfig(
        enabled=bool(raw.get("enabled", False)),
        regression_check_enabled=bool(raw.get("regression_check_enabled", True)),
        countdown_arithmetic_enabled=bool(
            raw.get("countdown_arithmetic_enabled", True)
        ),
    )


def _safe_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    return bool(raw)


def _build_narrative_richness(raw: dict[str, Any]) -> NarrativeRichnessConfig:
    categories_raw = raw.get("strict_categories")
    categories = (
        tuple(str(item) for item in categories_raw if isinstance(item, str))
        if isinstance(categories_raw, list)
        else NarrativeRichnessConfig().strict_categories
    )
    default_mode = str(raw.get("default_mode", "warn"))
    if default_mode not in {"warn", "strict"}:
        default_mode = "warn"
    return NarrativeRichnessConfig(
        enabled=bool(raw.get("enabled", True)),
        prompt_injection_enabled=bool(raw.get("prompt_injection_enabled", True)),
        default_mode=default_mode,
        strict_categories=categories,
    )


def _build_prose_quality(raw: dict[str, Any]) -> ProseQualityGateConfig:
    beat = _as_dict(raw.get("beat_planner"))
    gates = _as_dict(raw.get("gates"))
    anti_meta = _as_dict(gates.get("anti_meta"))
    show = _as_dict(gates.get("show_dont_tell"))
    ending = _as_dict(gates.get("in_scene_ending"))
    return ProseQualityGateConfig(
        sanitize_prompt=_safe_bool(raw.get("sanitize_prompt"), True),
        beat_planner_enabled=_safe_bool(beat.get("enabled"), True),
        anti_meta_enabled=_safe_bool(anti_meta.get("enabled"), True),
        anti_meta_severity=str(anti_meta.get("severity") or "block"),
        show_dont_tell_enabled=_safe_bool(show.get("enabled"), True),
        show_dont_tell_severity=str(show.get("severity") or "warn"),
        in_scene_ending_enabled=_safe_bool(ending.get("enabled"), True),
        in_scene_ending_severity=str(ending.get("severity") or "block"),
    )


def _build_methodology_framework(raw: dict[str, Any]) -> MethodologyFrameworkConfig:
    cards = _as_dict(raw.get("cards"))
    opening = _as_dict(raw.get("opening_three_function"))
    action = _as_dict(raw.get("action_scene_structure"))
    chekhov = _as_dict(raw.get("chekhov_emphasis"))
    chaos = _as_dict(raw.get("longform_chaos"))
    return MethodologyFrameworkConfig(
        enabled=bool(raw.get("enabled", False)),
        profile_id=str(raw.get("profile_id") or "plova_structured_writing_v1"),
        cards_enabled=_safe_bool(cards.get("enabled"), True),
        data_dir=str(cards.get("data_dir") or "data/methodology_sources/plova"),
        opening_three_function_enabled=_safe_bool(opening.get("enabled"), True),
        opening_three_function_default=str(opening.get("default") or "audit_only"),
        opening_three_function_block_until_chapter=max(
            0, _safe_int(opening.get("block_until_chapter"), 3)
        ),
        action_scene_structure_enabled=_safe_bool(action.get("enabled"), True),
        action_scene_structure_default=str(action.get("default") or "audit_only"),
        chekhov_emphasis_enabled=_safe_bool(chekhov.get("enabled"), True),
        chekhov_emphasis_default=str(chekhov.get("default") or "audit_only"),
        chekhov_overdue_window_default=max(
            1, _safe_int(chekhov.get("overdue_window_default"), 8)
        ),
        longform_chaos_enabled=_safe_bool(chaos.get("enabled"), False),
        longform_chaos_start_after_chapter=max(
            1, _safe_int(chaos.get("start_after_chapter"), 30)
        ),
    )


def _build_originality_engine(raw: dict[str, Any]) -> OriginalityEngineConfig:
    mode_b_raw = raw.get("mode_b_override")
    mode_b_override: bool | None = None
    if isinstance(mode_b_raw, bool):
        mode_b_override = mode_b_raw
    return OriginalityEngineConfig(
        enabled=_safe_bool(raw.get("enabled"), True),
        persist_persona_feedback=_safe_bool(
            raw.get("persist_persona_feedback"), True
        ),
        mode_b_override=mode_b_override,
        grading_text_cap_chars=max(
            500, _safe_int(raw.get("grading_text_cap_chars"), 12_000)
        ),
        retention_max_retries=max(
            1, _safe_int(raw.get("retention_max_retries"), 5)
        ),
        retention_escalate_after=max(
            1, _safe_int(raw.get("retention_escalate_after"), 3)
        ),
        auto_signature_plan=_safe_bool(raw.get("auto_signature_plan"), True),
        auto_voice_dna=_safe_bool(raw.get("auto_voice_dna"), True),
        voice_dna_min_sample_chars=max(
            200, _safe_int(raw.get("voice_dna_min_sample_chars"), 2000)
        ),
    )


def _safe_int(raw: Any, default: int) -> int:
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _build_ai_flavor(raw: dict[str, Any]) -> AiFlavorGateConfig:
    """Parse the ``ai_flavor_gate`` block, applying robust defaults.

    Operators can omit any field; the gate ships safe defaults so a
    missing block still produces a working configuration. The gate
    itself fails open on missing data files, so even with defaults a
    fresh checkout never crashes the pipeline.
    """

    block = _as_dict(raw.get("block_score"))
    warn = _as_dict(raw.get("warn_score"))
    llm = _as_dict(raw.get("llm_rewrite"))
    audit = _as_dict(raw.get("audit"))
    return AiFlavorGateConfig(
        enabled=bool(raw.get("enabled", True)),
        block_score_cn=_safe_int(block.get("cn"), 50),
        block_score_en=_safe_int(block.get("en"), 55),
        warn_score_cn=_safe_int(warn.get("cn"), 25),
        warn_score_en=_safe_int(warn.get("en"), 30),
        cluster_threshold=_safe_int(raw.get("cluster_threshold"), 3),
        llm_rewrite_enabled=bool(llm.get("enabled", True)),
        llm_budget_per_chapter=_safe_int(llm.get("max_spans_per_chapter"), 8),
        write_audit_file=bool(audit.get("enabled", True)),
        audit_dir_relative=str(audit.get("dir_relative") or "audits"),
        data_dir=str(raw.get("data_dir") or "data/ai_flavor"),
        block_on_residual=bool(raw.get("block_on_residual", True)),
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
        if cfg.l5.repeated_beat_enabled:
            checks.append(RepeatedEventBeatCheck())
        if cfg.l5.canon_guardrails_enabled:
            checks.append(CanonForbiddenTermCheck())
            checks.append(CanonStateRegressionCheck())
        if cfg.l5.cliffhanger_rotation_enabled:
            checks.append(CliffhangerRotationCheck())
        # Hype engine checks — self-no-op when no hype assignment in ctx, so
        # legacy projects predating the Phase-2 migration remain unaffected.
        checks.append(HypeOccurrenceCheck())
        checks.append(HypeDiversityCheck())
        checks.append(EndingSentenceImpactCheck())
        checks.append(GoldenThreeChapterCheck())
    # Phase B1 — LineGapCheck. Runs whenever the Phase B flag is opted-in;
    # the check itself no-ops when ``ctx.line_gap_report`` is ``None``, so
    # projects that haven't populated line history skip the check naturally.
    if cfg.phase_b.enabled:
        checks.append(LineGapCheck())
    return OutputValidator(checks)

from __future__ import annotations

import copy
from datetime import UTC, datetime
from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values
from pydantic import BaseModel, Field
import yaml

DEFAULT_CONFIG_PATH = Path("config/default.yaml")
DEFAULT_LOCAL_CONFIG_PATH = Path("config/local.yaml")
DEFAULT_DOTENV_PATH = Path(".env")
DEFAULT_DOTENV_LOCAL_PATH = Path(".env.local")
ENV_PREFIX = "BESTSELLER__"


class LLMRoleSettings(BaseModel):
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    stream: bool = False
    n_candidates: int = 1
    api_base: str | None = None
    api_key_env: str | None = None
    api_key_header: str | None = None
    model_override: str | None = None
    thinking_type: str | None = None
    reasoning_effort: str | None = None
    rate_limit_fallback_model: str | None = None
    rate_limit_fallback_api_base: str | None = None
    rate_limit_fallback_api_key_env: str | None = None
    rate_limit_fallback_stream: bool = False


class RetrySettings(BaseModel):
    max_attempts: int = 3
    wait_min_seconds: int = 1
    wait_max_seconds: int = 10
    retry_on: list[str] = Field(default_factory=list)
    # Rate-limit (HTTP 429) handling — transient by nature, so we use a
    # much more patient budget than generic errors.  Default 60 attempts
    # with up to 120s wait each gives ~2h of patience before giving up.
    rate_limit_max_attempts: int = 60
    rate_limit_wait_min_seconds: int = 5
    rate_limit_wait_max_seconds: int = 120
    rate_limit_fallback_enabled: bool = True
    rate_limit_fallback_cooldown_seconds: int = 300
    max_attempts_per_class: dict[str, int] = Field(default_factory=dict)


class LLMSettings(BaseModel):
    mock: bool = False
    planner: LLMRoleSettings
    writer: LLMRoleSettings
    critic: LLMRoleSettings
    summarizer: LLMRoleSettings
    editor: LLMRoleSettings
    retry: RetrySettings


class DatabaseSettings(BaseModel):
    url: str
    pool_size: int = 20
    max_overflow: int = 20
    pool_timeout_seconds: int = 30
    pool_recycle_seconds: int = 1800
    statement_timeout_ms: int = 60000
    lock_timeout_ms: int = 5000
    application_name: str = "bestseller"
    auto_migrate: bool = True
    echo_sql: bool = False


class HybridWeights(BaseModel):
    vector: float = 0.6
    lexical: float = 0.2
    structural: float = 0.2


class RetrievalSettings(BaseModel):
    provider: str = "pgvector"
    embedding_model: str
    embedding_dimensions: int
    chunk_size: int = 800
    chunk_overlap: int = 120
    candidate_limit: int = 40
    top_k: int = 12
    min_score: float = 0.55
    index_type: str = "hnsw"
    hybrid_weights: HybridWeights = Field(default_factory=HybridWeights)


class WordBudget(BaseModel):
    min: int
    target: int
    max: int


class GenerationSettings(BaseModel):
    target_total_words: int
    target_chapters: int
    words_per_chapter: WordBudget
    scenes_per_chapter: WordBudget
    words_per_scene: WordBudget
    context_budget_tokens: int
    active_context_scenes: int
    genre: str
    language: str
    pov: str
    structure_template: str
    methodology_compiler_enabled: bool = True
    methodology_budget_tokens: int = 1500
    # Strip abstract craft-theory / metadata / reference-dump sections and
    # verbatim-duplicate blocks from the scene-writer prompt so the model writes
    # prose instead of drowning in ~28K tokens of instructions. See
    # services/prompt_compactor.py.
    lean_writer_prompt: bool = True
    writer_prompt_mode: str = "ab"
    writer_prompt_ab_until_chapter: int = 3
    # When true, a weak platform title (keyword-soup / rejected) is sent for a
    # single LLM revision pass during conception. Clean concise IP names and
    # already-passing titles are never revised. See platform_title_workflow.py
    # § P2 and services/conception.py. (2026-06-03 book-title regression fix.)
    title_llm_revision_enabled: bool = True


class RepetitionSettings(BaseModel):
    window_words: int
    similarity_threshold: float


class QualityThresholds(BaseModel):
    scene_min_score: float
    chapter_coherence_min_score: float
    character_consistency_min_score: float
    plot_logic_min_score: float


class QualitySettings(BaseModel):
    draft_mode: bool = False
    enable_scene_critique: bool = True
    enable_chapter_coherence_check: bool = True
    enable_final_consistency_check: bool = True
    enable_llm_scene_commentary: bool = False
    enable_llm_chapter_commentary: bool = False
    enable_plan_judge: bool = True
    enable_plan_judge_llm: bool = False
    min_scene_rewrite_improvement: float = 0.03
    thresholds: QualityThresholds
    max_scene_revisions: int = 2
    max_chapter_revisions: int = 1
    repetition: RepetitionSettings


class S3ArtifactSettings(BaseModel):
    bucket: str = ""
    region: str = ""
    prefix: str = "bestseller"


class ArtifactStoreSettings(BaseModel):
    mode: str = "local"
    local_dir: str = "./artifacts"
    retain_prompt_payloads: bool = False
    s3: S3ArtifactSettings = Field(default_factory=S3ArtifactSettings)


class OutputFormats(BaseModel):
    markdown: bool = True
    docx: bool = False
    epub: bool = False
    pdf: bool = False


class CheckpointSettings(BaseModel):
    enabled: bool = True
    every_n_scenes: int = 5


class OutputSettings(BaseModel):
    base_dir: str = "./output"
    formats: OutputFormats = Field(default_factory=OutputFormats)
    checkpoint: CheckpointSettings = Field(default_factory=CheckpointSettings)
    stream_to_console: bool = True


class FileLoggingSettings(BaseModel):
    enabled: bool = True
    path: str = "./logs/bestseller.log"
    max_bytes: int = 10_485_760
    backup_count: int = 5


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt: str = "%Y-%m-%d %H:%M:%S"
    json_logs: bool = False
    file: FileLoggingSettings = Field(default_factory=FileLoggingSettings)
    suppress: list[str] = Field(default_factory=list)


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    pool_max_connections: int = 10
    socket_timeout_seconds: float = 5.0
    socket_connect_timeout_seconds: float = 3.0


class PipelineSettings(BaseModel):
    # Block drafting when bible / graph / outline lag behind truth version.
    enable_truth_version_guard: bool = True
    consistency_check_interval: int = 20  # Run consistency check every N chapters
    rolling_summary_interval: int = 25  # Compress knowledge window every N chapters
    resume_enabled: bool = True  # Skip already-completed chapters on resume
    accept_on_stall: bool = True  # Accept best draft when rewrite is stalled (no score improvement)
    project_consistency_block_on_failure: bool = True  # Whole-book consistency failures must pause, not accept_on_stall
    chapter_review_block_on_failure: bool = True  # Chapter review failures must not be completed via accept_on_stall
    gate_llm_adjudication_enabled: bool = True  # Context-dependent gate findings (common-sense) get an LLM CONFIRM/DISMISS pass before they may block
    chapter_outline_repair_attempts: int = 3  # Regenerate invalid chapter outlines before surfacing failure
    planning_artifact_reuse_enabled: bool = True
    planning_artifact_reuse_allow_legacy: bool = True
    chapter_outline_batch_size: int = 10
    commercial_strict_prewrite_chapter_outline_batch_size: int = 5
    commercial_strict_prewrite_outline_batch_shrink_size: int = 3
    commercial_strict_prewrite_planning_judge_threshold: float = 0.82
    enable_chapter_feedback: bool = True  # Post-chapter feedback extraction
    enable_contradiction_checks: bool = True  # Pre-scene contradiction checks
    # Turn continuity/identity violations into hard write blocks.
    # identity_block_on_violation is now ENABLED — the alias merge
    # (story_bible._dedupe_cast_inputs_by_identity) was validated on
    # the exorcist-detective canary project and false-positive rates
    # are acceptable.  Previously kept off to avoid mis-labelling
    # duplicate registry rows as "dead character speaks" / "gender flip".
    contradiction_block_on_violation: bool = True
    identity_block_on_violation: bool = True
    identity_block_severities: list[str] = Field(default_factory=lambda: ["critical", "major"])
    require_foundation_identity_lock: bool = True  # CastSpec must lock gender/pronouns before persistence
    require_chapter_plan_contract: bool = True  # Outline materialization must validate scene time/purpose/participants
    enable_chapter_causality_gate: bool = True  # Outline materialization validates reader-visible causal axes
    chapter_causality_gate_block_on_failure: bool = True  # Block flat chapter plans before prose drafting
    methodology_contract_mode: str = "warn"  # off/warn/strict for methodology overlay scope and execution gates
    require_pre_draft_scene_contract: bool = True  # Scene pipeline validates persisted scene cards before drafting
    enable_scene_plan_richness_gate: bool = True  # Pre-draft scene card richness validation
    scene_richness_block_on_critical: bool = True  # Raise on critical richness failure instead of logging + injecting warnings
    # Chapter-level outline readiness gate. This runs before any scene prose is
    # drafted and blocks deterministic "bad inputs": collapsed scene budgets,
    # stale auto-repair metadata, unresolved rewrite tasks, and impossible
    # timeline anchors. It complements scene richness, which is scene-local.
    enable_chapter_outline_readiness_gate: bool = True
    chapter_outline_readiness_block_on_failure: bool = True
    enable_story_bible_write_gate: bool = True
    story_bible_write_block_on_failure: bool = True
    enable_methodology_planning_readiness_gate: bool = True
    # Warn-by-default: this is a heuristic readiness gate (weak-mediated
    # opening, plausibility gaps, knowledge-boundary leaks). Findings are
    # recorded on the workflow run and drive downstream rewrite/repair, but
    # they must NOT abort book generation outright — a hard block here left
    # fresh books permanently stuck in ``planning`` (observed 2026-05-29).
    # Only deterministic, low-false-positive gates should block.
    methodology_planning_readiness_block_on_failure: bool = False
    enable_outline_llm_commercial_judge: bool = True
    outline_llm_commercial_judge_block_on_failure: bool = True
    # Heuristic platform-fit gate (七猫 golden-three). Warn-only by default so
    # a flagged opening becomes a rewrite directive rather than aborting the
    # whole book at planning (2026-05-29).
    qimao_planning_gate_block_on_failure: bool = False
    outline_llm_commercial_judge_threshold: float = 0.82
    enable_outline_reader_experience_judge: bool = True
    outline_reader_experience_judge_block_on_failure: bool = True
    outline_reader_experience_judge_threshold: float = 0.78
    enable_chapter_predraft_quality_gate: bool = True
    chapter_predraft_quality_gate_block_on_failure: bool = True
    enable_chapter_scene_contract_materializer: bool = True
    qimao_opening_max_attempts: int = 3
    enable_chapter_llm_commercial_judge: bool = True
    chapter_llm_commercial_judge_block_on_failure: bool = True
    # Advisory LitStyle-100R 文采 judge. Scores the "打动读者" (literary craft)
    # axis the 16-dim commercial judge never covers. ADVISORY ONLY — there is no
    # block_on_failure flag; it can never change a chapter's verdict. Off by
    # default (opt-in) because it adds one critic call per chapter; enabling it
    # only writes evidence_summary["litstyle"], so behaviour is otherwise
    # unchanged. See docs/litstyle-prose-craft-fusion-2026-06.md.
    enable_chapter_litstyle_judge: bool = False
    # Design + persist a book's imagery system (LitStyle imagery_system lever) once
    # per book at first scene draft, so the writer gets a soft per-chapter imagery
    # recall block. Idempotent + soft (failure = no-op). One cheap LLM call per book.
    enable_imagery_system_design: bool = True
    enable_chapter_window_llm_judge: bool = True
    chapter_window_llm_judge_block_on_failure: bool = True
    chapter_window_llm_judge_size: int = 5
    chapter_window_llm_judge_min_chapters: int = 2
    enable_volume_llm_checkpoint_judge: bool = True
    volume_llm_checkpoint_block_on_failure: bool = True
    volume_llm_checkpoint_interval: int = 10
    volume_llm_checkpoint_min_chapters: int = 10
    # Commercial strict mode fails closed when any quality gate cannot run or
    # reports an unknown blocking code. Disable only for exploratory drafts.
    commercial_strict_quality_mode: bool = True
    feedback_stale_clue_threshold: int = 15  # Chapters before a clue is stale
    feedback_dormant_plan_threshold: int = 10  # Chapters before antagonist plan is dormant
    feedback_arc_inactivity_threshold: int = 8  # Chapters before arc is dead-ended
    arc_summary_enabled: bool = True  # Generate arc summaries at arc boundaries
    world_snapshot_enabled: bool = True  # Generate world snapshots at arc boundaries
    act_plan_threshold: int = 50  # Chapters > threshold enables act-level planning
    progressive_planning: bool = False  # Enable progressive volume planning with write-feedback loop
    # Default to strict sequential volume writing. A blocked or incomplete
    # volume must be repaired before later volumes are planned/written, or
    # long-form resumes can skip visible chapter gaps.
    progressive_continue_after_volume_block: bool = False
    # Final entry guard: a requested chapter may not draft while any earlier
    # chapter lacks an approved current draft. This prevents manual or stale
    # workflow slices such as 101/151 from jumping over 86-100.
    enforce_sequential_chapter_generation: bool = True
    category_aware_planning: bool = True  # Use novel-category research for genre-specific planning
    # ── Multi-dimensional material library (Batch 1-3 rollout) ─────────
    # Batch 1 gate: Curator + Research Agent + query API available when
    # this is True.  Now **defaulted to True** after the L1–L4 recon
    # (2026-04-24): historical projects have empty ``project_materials``
    # rows and the planner + drafter already contain explicit "no-refs →
    # legacy pack fragments" fallbacks, so enabling the library globally
    # is byte-identical for old books.  Override with ``BESTSELLER__
    # PIPELINE__ENABLE_MATERIAL_LIBRARY=false`` if a legacy environment
    # ever regresses.
    enable_material_library: bool = True
    # Batch 2 gate: 5 Forges produce ProjectMaterials + Planner/Drafter
    # switch to reference-style prompts.  Defaulted on alongside
    # ``enable_material_library``; cold-start guards in
    # ``material_forge.base`` handle an empty library without blocking.
    enable_forge_pipeline: bool = True
    # Batch 2 gate: Planner / Drafter inject §dim/slug references instead
    # of pack-embedded plot fragments.  Orthogonal to forge_pipeline so
    # library-backed references can be authored manually for testing.
    enable_reference_style_generation: bool = True
    # Batch 3 gate: CrossProjectFingerprint + novelty critic.  Remains
    # opt-in until the first post-rollout canary proves false-positive
    # rate is acceptable — C7 already warn-only-integrates novelty on
    # character upsert without this flag.
    enable_novelty_guard: bool = False
    # Opt-in "soft reference" layer — lets historical projects' *new*
    # chapters pull inspirational entries straight from the global
    # ``material_library`` without going through a Forge run.  Old data
    # stays untouched; when this flag is True the Drafter prompt gets a
    # read-only "library inspiration" block.  Default False so existing
    # generation behaviour is byte-identical unless the operator opts in.
    enable_library_soft_reference: bool = False
    # How many library entries the soft-reference block may surface per
    # call.  Kept small so the prompt budget stays predictable.
    library_soft_reference_top_k: int = 4
    # Planner-time distilled design reference.  This is the bridge from
    # anonymized mature-novel distillation aggregates into BookSpec,
    # WorldSpec, CastSpec, VolumePlan, and chapter-outline prompts.  It is
    # read-only and renders only abstract design mechanics plus anti-copy
    # boundaries, never source prose or named entities.
    enable_distilled_design_reference: bool = True
    # Write-time active query brief — lets the model ask targeted read-only
    # questions before scene drafting. Disabled by default to preserve the
    # historical pipeline cost/latency profile.
    enable_story_query_brief: bool = False
    story_query_brief_max_rounds: int = 4
    enable_golden_three_health: bool = True
    golden_three_min_hype_chapters: int = 2
    golden_three_min_ending_hook_chapters: int = 2
    # Commercial planning readiness gate. Long-form signing projects must
    # prove that chapters 1-3 have concrete conflict, hooks, external pressure,
    # and strong assigned hype before prose generation starts.
    enable_commercial_planning_readiness_gate: bool = True
    # When True (default), the deterministic gate result alone is advisory;
    # the LLM judge makes the final pass/fail decision.  The deterministic
    # findings are passed to the LLM as reference context.
    # When False, falls back to deterministic-only hard block behaviour.
    enable_commercial_planning_llm_judge: bool = True
    commercial_planning_llm_judge_threshold: float = 0.75
    # Kept for backwards compatibility but no longer the primary block signal
    # when enable_commercial_planning_llm_judge=True.
    commercial_planning_readiness_block_on_failure: bool = True
    commercial_planning_min_target_chapters: int = 50
    # Fanqie market intelligence is opt-in, but long-form signing readiness is
    # enforced by default so weak opening loops are repaired before write-out.
    enable_fanqie_market_profile: bool = False
    enable_fanqie_long_ranking_gate: bool = True
    fanqie_long_ranking_block_on_failure: bool = True
    # Per-chapter cap on how many times the keyword-matching fanqie ranking
    # gate is allowed to *hard-block* the same chapter across pipeline
    # runs. Once a chapter has tripped the gate this many times we demote
    # it to audit-only and let downstream LLM judges arbitrate, so a
    # genre-mismatched keyword list can't loop forever on otherwise-strong
    # openings. See 青囊不语问阴阳 ch1 (2026-05-25) for the regression.
    fanqie_long_ranking_block_max_attempts: int = 3
    # Chapter-length stability gate.  Pulls the target window from
    # ``generation.words_per_chapter`` so historical projects without a
    # populated ``invariants.length_envelope`` still get hard feedback
    # when a chapter lands 30%+ short/long.  Disabled by default until we
    # canary one genre end-to-end; when enabled, ``BLOCK_*`` bands raise a
    # ``WriteSafetyBlockError`` in the same way golden-three does.
    enable_length_stability_gate: bool = True
    # Warnings (soft-margin bands) do NOT block by default — only the
    # hard BLOCK_LOW / BLOCK_HIGH bands surface.  Flip to
    # ``["major", "minor"]`` to surface WARN_* as well (chatty).
    length_stability_block_severities: list[str] = Field(
        default_factory=lambda: ["major"]
    )
    # Extra tolerance beyond [min, max] before a drift is escalated from
    # WARN_* to BLOCK_*.  0.10 == 10% extra slack (so with min=5000 the
    # hard block trips at wc < 4500).  Tunable per project via
    # ``BESTSELLER__PIPELINE__LENGTH_STABILITY_WARN_MARGIN``.
    length_stability_warn_margin: float = 0.10
    # ── Chapter auto-repair ──
    # When ``enable_length_stability_gate`` (or other L4/L5 gates) flags a
    # chapter as ``production_state="blocked"``, the chapter pipeline can
    # auto-trigger a scene-level rewrite cycle instead of leaving the
    # workflow stranded in FAILED / MACHINE_BLOCKED.  Only a narrow set of
    # block codes are considered "repairable" to avoid infinite loops on
    # deterministic violations (e.g. character-name roster issues can only
    # be fixed by a schema change, not more rewriting).
    enable_chapter_auto_repair: bool = True
    # Hard cap on the number of (assemble → gate → rewrite → reassemble)
    # cycles per chapter. 3 matches the staged repair prompts below: gentle,
    # aggressive, then final intervention. 0 disables auto-repair entirely.
    #
    # This bound is **intra-run**: the counter resets at the start of every
    # ``chapter_pipeline`` invocation. ``chapter_auto_repair_total_max_attempts``
    # bounds the *cumulative* attempts across pipeline runs so a chapter that
    # cannot be auto-repaired stops re-entering the loop after the budget is
    # spent (青囊不语问阴阳 ch1: 145 versions across many runs, intra-run cap
    # was never insufficient — the cross-run cap was missing).
    chapter_auto_repair_max_attempts: int = 3
    chapter_auto_repair_total_max_attempts: int = 9
    # Per-scene hard cap on the number of auto-repair rewrites a single
    # scene may receive across the lifetime of the book.  WS-C3 of
    # docs/质量回归修复-开发计划-20260602.md: the historical 青囊 ch1 case
    # generated 43 draft versions for 3 scenes (≈14 versions per scene)
    # because outer ``project_repair`` could re-enter the chapter pipeline
    # and implicitly reset the per-scene counter.  This setting caps the
    # *cumulative* rewrites per scene — once hit, the scene is stamped
    # ``auto_accepted_with_debt=True`` and the assembler keeps the prior
    # draft.  Outer repair must not reset the counter (see
    # ``bump_scene_auto_repair_counter`` / ``is_scene_at_auto_repair_cap``).
    chapter_auto_repair_max_scene_rewrites: int = 3
    # Cross-run cap on ``autonomous_quality_retrofit`` rewrite tasks
    # generated per chapter. ``autonomous_book_repair`` schedules these from
    # the quality-levers audit; without a per-chapter cap a chapter that
    # repeatedly fails the audit can collect retrofit tasks indefinitely.
    # The counter sits in chapter.metadata and is wiped when the chapter
    # passes the quality bundle.
    autonomous_quality_retrofit_max_attempts: int = 5
    # Only these block codes trigger auto-repair. Length, dialogue,
    # ending-hook, lifecycle, and canon-term leaks are rewrite-fixable.
    # Deterministic schema blocks (POV_LOCK, NAMING, etc.) stay excluded.
    chapter_auto_repair_repairable_codes: list[str] = Field(
        default_factory=lambda: [
            "BLOCK_LOW",
            "BLOCK_HIGH",
            "CHAPTER_TOO_SHORT",
            "CHAPTER_BELOW_TARGET",
            "DIALOG_UNPAIRED",
            "ENDING_SENTENCE_WEAK",
            "dead_alive",
            "pronoun_mismatch",
            "character_resurrection",
            "character_missing_appearance",
            "character_sealed_appearance",
            "character_sleeping_appearance",
            "character_comatose_appearance",
            "CANON_FORBIDDEN_TERM",
            "CANON_STATE_REGRESSION",
            "CROSS_CHAPTER_REPETITION",
            "INTRA_CHAPTER_REPETITION",
            "REPEATED_EVENT_BEAT",
            "CHAPTER_OPENING_REPETITION",
            "CHAPTER_SPLICE_REPEATED_SENTENCE",
            "SCENE_JUMP_UNRESOLVED",
            "ANTI_META_LEAK",
            "ANTI_META_ENDING_OUT_OF_SCENE",
            "HOOK_ECHO_MISSING",
            "HOOK_ECHO_LOW",
            "SIGNATURE_SCENE_MISSING",
            "SIGNATURE_IMAGE_MISSING",
            "OPENING_PRESSURE_THIN",
            "ENDING_HOOK_MISSING",
            "PARAGRAPH_DUPLICATE_PARAPHRASE",
            "CALLBACK_OBLIGATION_MISSING",
            "LENGTH_OUT_OF_BAND",
            "GOLDEN_THREE_WEAK",
            "NAMING_OUT_OF_POOL",
            "CLIFFHANGER_REPEAT",
            "EXPOSITION_DUMP",
            "CAST_VIOLATION",
            "OPENING_SCENE_DRIFT",
            "FRONT10_FORBIDDEN_SIGNAL",
            "FRONT10_SCENE_FORBIDDEN_ACTION",
            "FRONT10_RULE_LECTURE_DENSITY",
            "UNEXPLAINED_BODY_STATE",
        ]
    )
    # Chapter-first drafting bypasses per-scene prose generation and asks the
    # writer model to produce one complete chapter from the chapter plan plus
    # scene cards. It is intended for high-retention openings and debugging the
    # real framework output without manual prose intervention.
    enable_chapter_first_generation: bool = False
    chapter_first_max_chapter_number: int = 3
    chapter_first_short_chapter_threshold: int = 3500
    chapter_first_supersede_pending_rewrites: bool = False
    # Project-level premium-readiness gate. It is enabled as telemetry by
    # default so every project pipeline records whether the structured genre
    # engine is complete; set ``premium_book_gate_block_on_failure`` to turn
    # the report into a hard final gate.
    enable_premium_book_gate: bool = True
    premium_book_gate_block_on_failure: bool = False
    # Write-preparation planning kernel. This runs before chapter production
    # entry points and records whether benchmark alignment, unique hook,
    # series engine, long-arc capacity, and genre-specific engines are present.
    enable_prewrite_readiness_gate: bool = True
    prewrite_readiness_block_on_failure: bool = False
    # Story design kernel rollout.  This is the new project-level plot design
    # contract: shape routing, category grammar, plot tree, beat schedule, and
    # reverse-outline verification.  Enabled for telemetry/injection by default;
    # strict blocking remains opt-in until canary books prove low false positives.
    enable_story_design_kernel: bool = True
    story_design_kernel_candidate_count: int = 3
    enable_emotion_driven_kernel: bool = True
    enable_emotion_kernel_backfill: bool = True
    enable_public_emotion_kernel_backfill: bool = True
    enable_entry_system_kernel: bool = True
    enable_entry_system_backfill: bool = True
    enable_story_state_driven_planning: bool = True
    enable_reverse_outline_gate: bool = True
    reverse_outline_gate_block_on_failure: bool = False
    enable_worldview_compliance_gate: bool = True
    worldview_compliance_gate_block_on_failure: bool = False
    enable_story_principle_gate: bool = True
    enable_worldview_progression_gate: bool = True
    worldview_progression_gate_block_on_failure: bool = False
    story_design_require_kernel_for_new_projects: bool = False
    # Curator scheduling — overridable via env for admin triage.
    curator_weekly_cron_hour: int = 4  # 04:00 UTC Monday
    curator_weekly_cron_day_of_week: str = "mon"
    curator_max_gaps_per_run: int = 6
    curator_max_fills_per_run: int = 5


class BudgetSettings(BaseModel):
    max_tokens_per_project: int = 0  # 0 = unlimited
    warning_thresholds: list[float] = Field(default_factory=lambda: [0.5, 0.8, 1.0])
    cost_per_1k_input_tokens: float = 0.003
    cost_per_1k_output_tokens: float = 0.015


class HookEngineSettings(BaseModel):
    enabled: bool = True
    min_h_norm: float = 30.0
    candidate_count: int = 6
    quickstart_candidate_count: int = 12
    rank_weight_h_norm: float = 0.62
    rank_weight_novelty: float = 0.28
    rank_weight_duplicate_risk: float = 0.10


class ApiSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=list)  # Empty by default; set explicitly for production
    api_key_header: str = "Authorization"
    task_event_ttl_seconds: int = 86400  # 24h progress retention in Redis


class AppSettings(BaseModel):
    llm: LLMSettings
    database: DatabaseSettings
    retrieval: RetrievalSettings
    generation: GenerationSettings
    quality: QualitySettings
    artifact_store: ArtifactStoreSettings
    output: OutputSettings
    logging: LoggingSettings
    redis: RedisSettings = Field(default_factory=RedisSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    hook_engine: HookEngineSettings = Field(default_factory=HookEngineSettings)


LLM_ROLE_NAMES = ("planner", "writer", "critic", "summarizer", "editor")
LLM_RUNTIME_PROFILE_ENV = "BESTSELLER_LLM_RUNTIME_PROFILE_PATH"
LLM_RUNTIME_PROFILE_FILENAME = "llm-runtime-profile.json"


LLM_RUNTIME_PROFILES: dict[str, dict[str, Any]] = {
    "minimax": {
        "key": "minimax",
        "label": "MiniMax",
        "description": "MiniMax-M3 for planning, writing, review, and repair.",
        "roles": {
            "planner": {
                "model": "openai/MiniMax-M3",
                "api_base": "https://api.minimaxi.com/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "timeout_seconds": 900,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "writer": {
                "model": "openai/MiniMax-M3",
                "model_override": "openai/MiniMax-M3",
                "api_base": "https://api.minimaxi.com/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "timeout_seconds": 360,
                "max_tokens": 32768,
                "stream": False,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "critic": {
                "model": "openai/MiniMax-M3",
                "api_base": "https://api.minimaxi.com/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "timeout_seconds": 180,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "summarizer": {
                "model": "openai/MiniMax-M3",
                "api_base": "https://api.minimaxi.com/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "timeout_seconds": 120,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "editor": {
                "model": "openai/MiniMax-M3",
                "api_base": "https://api.minimaxi.com/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "timeout_seconds": 360,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
        },
    },
    "deepseek": {
        "key": "deepseek",
        "label": "DeepSeek",
        "description": "DeepSeek V4 Flash for planning, review, or temporary MiniMax replacement.",
        "roles": {
            "planner": {
                "model": "deepseek/deepseek-v4-flash",
                "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 900,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": "enabled",
                "reasoning_effort": "high",
            },
            "writer": {
                "model": "deepseek/deepseek-v4-flash",
                "model_override": "deepseek/deepseek-v4-flash",
                "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 360,
                "max_tokens": 32768,
                "stream": False,
                "thinking_type": "disabled",
                "reasoning_effort": None,
            },
            "critic": {
                "model": "deepseek/deepseek-v4-flash",
                "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 180,
                "stream": False,
                "model_override": None,
                "thinking_type": "enabled",
                "reasoning_effort": "high",
            },
            "summarizer": {
                "model": "deepseek/deepseek-v4-flash",
                "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 120,
                "stream": False,
                "model_override": None,
                "thinking_type": "disabled",
                "reasoning_effort": None,
            },
            "editor": {
                "model": "deepseek/deepseek-v4-flash",
                "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 360,
                "stream": False,
                "model_override": None,
                "thinking_type": "enabled",
                "reasoning_effort": "high",
            },
        },
    },
    "nvidia": {
        "key": "nvidia",
        "label": "NVIDIA",
        "description": "NVIDIA NIM GLM-5.1 as a standalone runtime model or fallback.",
        "roles": {
            "planner": {
                "model": "openai/z-ai/glm-5.1",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "timeout_seconds": 900,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "writer": {
                "model": "openai/z-ai/glm-5.1",
                "model_override": "openai/z-ai/glm-5.1",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "timeout_seconds": 360,
                "max_tokens": 32768,
                "stream": False,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "critic": {
                "model": "openai/z-ai/glm-5.1",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "timeout_seconds": 180,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "summarizer": {
                "model": "openai/z-ai/glm-5.1",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "timeout_seconds": 120,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "editor": {
                "model": "openai/z-ai/glm-5.1",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "timeout_seconds": 360,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
        },
    },
    "xiaomi-mimo": {
        "key": "xiaomi-mimo",
        "label": "Xiaomi MiMo",
        "description": "Xiaomi MiMo token-plan model via OpenAI-compatible China endpoint.",
        "roles": {
            "planner": {
                "model": "openai/mimo-v2.5-pro",
                "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                "api_key_env": "XIAOMI_MIMO_API_KEY",
                "api_key_header": "api-key",
                "timeout_seconds": 900,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "writer": {
                "model": "openai/mimo-v2.5-pro",
                "model_override": "openai/mimo-v2.5-pro",
                "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                "api_key_env": "XIAOMI_MIMO_API_KEY",
                "api_key_header": "api-key",
                "timeout_seconds": 360,
                "max_tokens": 32768,
                "stream": False,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "critic": {
                "model": "openai/mimo-v2.5-pro",
                "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                "api_key_env": "XIAOMI_MIMO_API_KEY",
                "api_key_header": "api-key",
                "timeout_seconds": 180,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "summarizer": {
                "model": "openai/mimo-v2.5-pro",
                "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                "api_key_env": "XIAOMI_MIMO_API_KEY",
                "api_key_header": "api-key",
                "timeout_seconds": 300,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
            "editor": {
                "model": "openai/mimo-v2.5-pro",
                "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                "api_key_env": "XIAOMI_MIMO_API_KEY",
                "api_key_header": "api-key",
                "timeout_seconds": 360,
                "max_tokens": 32768,
                "stream": False,
                "model_override": None,
                "thinking_type": None,
                "reasoning_effort": None,
            },
        },
    },
}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration at {path} must be a mapping.")
    return raw


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce_env_value(value: str) -> Any:
    if value == "":
        return value
    parsed = yaml.safe_load(value)
    return parsed


def _apply_env_overrides(data: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    merged = copy.deepcopy(data)
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        parts = key.removeprefix(ENV_PREFIX).lower().split("__")
        cursor: dict[str, Any] = merged
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                cursor[part] = {}
            cursor = cursor[part]
        cursor[parts[-1]] = _coerce_env_value(value)
    return merged


def _load_process_env_with_dotenv(
    dotenv_path: Path = DEFAULT_DOTENV_PATH,
    dotenv_local_path: Path = DEFAULT_DOTENV_LOCAL_PATH,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (dotenv_path, dotenv_local_path):
        if not path.exists():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                merged[key] = value
    merged.update(os.environ)
    return merged


def load_settings(
    config_path: Path | None = None,
    local_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AppSettings:
    effective_config_path = config_path or DEFAULT_CONFIG_PATH
    effective_local_path = local_config_path or DEFAULT_LOCAL_CONFIG_PATH
    env_map = env if env is not None else _load_process_env_with_dotenv()

    base = _read_yaml(effective_config_path)
    local = _read_yaml(effective_local_path)
    merged = _deep_merge(base, local)
    merged = _apply_env_overrides(merged, env_map)
    return AppSettings.model_validate(merged)


def get_runtime_env_value(name: str) -> str | None:
    value = _load_process_env_with_dotenv().get(name)
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def llm_runtime_profile_path(settings: AppSettings) -> Path:
    override = os.environ.get(LLM_RUNTIME_PROFILE_ENV)
    if override:
        return Path(override)
    return (
        Path(settings.artifact_store.local_dir)
        / "runtime"
        / LLM_RUNTIME_PROFILE_FILENAME
    )


def _read_runtime_llm_profile_key(settings: AppSettings) -> str | None:
    path = llm_runtime_profile_path(settings)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    key = raw.get("active_profile")
    if not isinstance(key, str):
        return None
    key = key.strip().lower()
    return key if key in LLM_RUNTIME_PROFILES else None


def infer_llm_profile_key(settings: AppSettings) -> str | None:
    writer = settings.llm.writer
    for key, profile in LLM_RUNTIME_PROFILES.items():
        role = profile["roles"]["writer"]
        if writer.model == role.get("model") and writer.api_base == role.get("api_base"):
            return key
    return None


def apply_runtime_llm_profile(settings: AppSettings) -> AppSettings:
    profile_key = _read_runtime_llm_profile_key(settings)
    if not profile_key:
        return settings

    profile = LLM_RUNTIME_PROFILES[profile_key]
    llm_settings = settings.llm.model_copy(deep=True)
    for role_name in LLM_ROLE_NAMES:
        role_settings = getattr(llm_settings, role_name)
        role_update = dict(profile["roles"].get(role_name, {}))
        setattr(llm_settings, role_name, role_settings.model_copy(update=role_update))
    return settings.model_copy(update={"llm": llm_settings})


def set_runtime_llm_profile(settings: AppSettings, profile_key: str) -> dict[str, Any]:
    normalized = profile_key.strip().lower()
    if normalized not in LLM_RUNTIME_PROFILES:
        raise ValueError(f"Unknown LLM runtime profile: {profile_key}")
    path = llm_runtime_profile_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_profile": normalized,
        "updated_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return runtime_llm_profile_payload(settings)


def runtime_llm_profile_payload(settings: AppSettings) -> dict[str, Any]:
    effective_settings = apply_runtime_llm_profile(settings)
    active_key = _read_runtime_llm_profile_key(settings) or infer_llm_profile_key(settings)
    if active_key is None:
        source = "settings"
        active_label = "自定义"
    else:
        source = "runtime" if _read_runtime_llm_profile_key(settings) else "settings"
        active_label = str(LLM_RUNTIME_PROFILES[active_key]["label"])

    profile_payloads: list[dict[str, Any]] = []
    for key, profile in LLM_RUNTIME_PROFILES.items():
        roles = profile["roles"]
        key_envs = sorted(
            {
                str(role.get("api_key_env"))
                for role in roles.values()
                if role.get("api_key_env")
            }
        )
        profile_payloads.append(
            {
                "key": key,
                "label": profile["label"],
                "description": profile["description"],
                "writer_model": roles["writer"]["model"],
                "api_key_envs": key_envs,
                "api_key_configured": all(
                    get_runtime_env_value(env_name) is not None for env_name in key_envs
                ),
            }
        )

    return {
        "active_key": active_key,
        "active_label": active_label,
        "source": source,
        "profile_path": str(llm_runtime_profile_path(settings)),
        "writer_model": effective_settings.llm.writer.model,
        "planner_model": effective_settings.llm.planner.model,
        "profiles": profile_payloads,
    }


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return load_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    return settings.model_dump(mode="json")

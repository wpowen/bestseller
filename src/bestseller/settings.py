from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values
import yaml
from pydantic import BaseModel, Field


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
    model_override: str | None = None


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
    enable_chapter_feedback: bool = True  # Post-chapter feedback extraction
    enable_contradiction_checks: bool = True  # Pre-scene contradiction checks
    # Turn continuity/identity violations into hard write blocks.
    # NOTE: ``identity_block_on_violation`` is kept False by default until the
    # cast-side alias merge (story_bible._dedupe_cast_inputs_by_identity +
    # planner resolver) has been validated on a canary project. While the
    # character registry is still producing duplicate rows under variant
    # names, the identity guard's exact-name matching will mis-label the
    # second row as "dead character speaks" / "gender flip" and block the
    # entire scene. Flip back to True once the alias merge is verified.
    contradiction_block_on_violation: bool = True
    identity_block_on_violation: bool = False
    identity_block_severities: list[str] = Field(default_factory=lambda: ["critical", "major"])
    enable_scene_plan_richness_gate: bool = True  # Pre-draft scene card richness validation
    scene_richness_block_on_critical: bool = False  # If True, raise on critical richness failure instead of logging + injecting warnings
    feedback_stale_clue_threshold: int = 15  # Chapters before a clue is stale
    feedback_dormant_plan_threshold: int = 10  # Chapters before antagonist plan is dormant
    feedback_arc_inactivity_threshold: int = 8  # Chapters before arc is dead-ended
    arc_summary_enabled: bool = True  # Generate arc summaries at arc boundaries
    world_snapshot_enabled: bool = True  # Generate world snapshots at arc boundaries
    act_plan_threshold: int = 50  # Chapters > threshold enables act-level planning
    progressive_planning: bool = False  # Enable progressive volume planning with write-feedback loop
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
    # Write-time active query brief — lets the model ask targeted read-only
    # questions before scene drafting. Disabled by default to preserve the
    # historical pipeline cost/latency profile.
    enable_story_query_brief: bool = False
    story_query_brief_max_rounds: int = 4
    enable_golden_three_health: bool = True
    golden_three_min_hype_chapters: int = 2
    golden_three_min_ending_hook_chapters: int = 2
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
    # workflow stranded in FAILED / WAITING_HUMAN.  Only a narrow set of
    # block codes are considered "repairable" to avoid infinite loops on
    # deterministic violations (e.g. character-name roster issues can only
    # be fixed by a schema change, not more rewriting).
    enable_chapter_auto_repair: bool = True
    # Hard cap on the number of (assemble → gate → rewrite → reassemble)
    # cycles per chapter.  1 means at most one repair attempt in addition
    # to the original generation; 0 disables auto-repair entirely even
    # when ``enable_chapter_auto_repair`` is True.
    chapter_auto_repair_max_attempts: int = 1
    # Only these block codes trigger auto-repair.  Length-stability bands
    # (BLOCK_LOW / BLOCK_HIGH) are the sweet spot — a rewrite with
    # "expand / trim to hit target" guidance usually fixes them.  L4/L5
    # block codes (POV_LOCK, NAMING, DIALOG_INTEGRITY, ...) are deliberately
    # omitted until they've been canary-verified as auto-fixable.
    chapter_auto_repair_repairable_codes: list[str] = Field(
        default_factory=lambda: ["BLOCK_LOW", "BLOCK_HIGH"]
    )
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


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return load_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    return settings.model_dump(mode="json")

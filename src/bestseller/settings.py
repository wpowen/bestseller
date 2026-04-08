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


class RetrySettings(BaseModel):
    max_attempts: int = 3
    wait_min_seconds: int = 1
    wait_max_seconds: int = 10
    retry_on: list[str] = Field(default_factory=list)


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
    enable_scene_critique: bool = True
    enable_chapter_coherence_check: bool = True
    enable_final_consistency_check: bool = True
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
    consistency_check_interval: int = 20  # Run consistency check every N chapters
    rolling_summary_interval: int = 25  # Compress knowledge window every N chapters
    resume_enabled: bool = True  # Skip already-completed chapters on resume


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

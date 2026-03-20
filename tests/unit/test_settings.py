from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


def test_load_settings_reads_default_and_local_config(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    default_path = config_dir / "default.yaml"
    local_path = config_dir / "local.yaml"

    default_path.write_text(
        """
llm:
  mock: false
  planner: {model: planner-a, temperature: 0.5, max_tokens: 100, timeout_seconds: 10}
  writer: {model: writer-a, temperature: 0.6, max_tokens: 100, timeout_seconds: 10, stream: true}
  critic: {model: critic-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  summarizer: {model: sum-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  editor: {model: editor-a, temperature: 0.4, max_tokens: 100, timeout_seconds: 10}
  retry: {max_attempts: 3, wait_min_seconds: 1, wait_max_seconds: 2, retry_on: [RateLimitError]}
database:
  url: postgresql+asyncpg://default
retrieval:
  provider: pgvector
  embedding_model: bge
  embedding_dimensions: 1024
generation:
  target_total_words: 10000
  target_chapters: 10
  words_per_chapter: {min: 100, target: 200, max: 300}
  scenes_per_chapter: {min: 1, target: 2, max: 3}
  words_per_scene: {min: 50, target: 100, max: 150}
  context_budget_tokens: 2000
  active_context_scenes: 2
  genre: fantasy
  language: zh-CN
  pov: third-limited
  structure_template: three-act
quality:
  thresholds:
    scene_min_score: 0.7
    chapter_coherence_min_score: 0.8
    character_consistency_min_score: 0.75
    plot_logic_min_score: 0.7
  repetition: {window_words: 1000, similarity_threshold: 0.9}
artifact_store:
  mode: local
output:
  base_dir: ./output
logging:
  suppress: [urllib3]
""",
        encoding="utf-8",
    )
    local_path.write_text(
        """
database:
  url: postgresql+asyncpg://local
artifact_store:
  local_dir: ./artifacts-local
""",
        encoding="utf-8",
    )

    settings = load_settings(
        config_path=default_path,
        local_config_path=local_path,
        env={},
    )

    assert settings.database.url == "postgresql+asyncpg://local"
    assert settings.artifact_store.local_dir == "./artifacts-local"
    assert settings.generation.genre == "fantasy"


def test_env_overrides_nested_values(tmp_path: Path) -> None:
    config_path = tmp_path / "default.yaml"
    config_path.write_text(
        """
llm:
  mock: false
  planner: {model: planner-a, temperature: 0.5, max_tokens: 100, timeout_seconds: 10}
  writer: {model: writer-a, temperature: 0.6, max_tokens: 100, timeout_seconds: 10, stream: false}
  critic: {model: critic-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  summarizer: {model: sum-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  editor: {model: editor-a, temperature: 0.4, max_tokens: 100, timeout_seconds: 10}
  retry: {max_attempts: 3, wait_min_seconds: 1, wait_max_seconds: 2, retry_on: [RateLimitError]}
database:
  url: postgresql+asyncpg://default
retrieval:
  provider: pgvector
  embedding_model: bge
  embedding_dimensions: 1024
generation:
  target_total_words: 10000
  target_chapters: 10
  words_per_chapter: {min: 100, target: 200, max: 300}
  scenes_per_chapter: {min: 1, target: 2, max: 3}
  words_per_scene: {min: 50, target: 100, max: 150}
  context_budget_tokens: 2000
  active_context_scenes: 2
  genre: fantasy
  language: zh-CN
  pov: third-limited
  structure_template: three-act
quality:
  thresholds:
    scene_min_score: 0.7
    chapter_coherence_min_score: 0.8
    character_consistency_min_score: 0.75
    plot_logic_min_score: 0.7
  repetition: {window_words: 1000, similarity_threshold: 0.9}
artifact_store:
  mode: local
output:
  base_dir: ./output
logging:
  suppress: [urllib3]
""",
        encoding="utf-8",
    )

    settings = load_settings(
        config_path=config_path,
        local_config_path=tmp_path / "missing.yaml",
        env={
            "BESTSELLER__DATABASE__URL": "postgresql+asyncpg://env-override",
            "BESTSELLER__LLM__MOCK": "true",
            "BESTSELLER__RETRIEVAL__TOP_K": "25",
        },
    )

    assert settings.database.url == "postgresql+asyncpg://env-override"
    assert settings.llm.mock is True
    assert settings.retrieval.top_k == 25


def test_explicit_empty_env_map_does_not_fallback_to_process_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "default.yaml"
    config_path.write_text(
        """
llm:
  mock: false
  planner: {model: planner-a, temperature: 0.5, max_tokens: 100, timeout_seconds: 10}
  writer: {model: writer-a, temperature: 0.6, max_tokens: 100, timeout_seconds: 10, stream: false}
  critic: {model: critic-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  summarizer: {model: sum-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  editor: {model: editor-a, temperature: 0.4, max_tokens: 100, timeout_seconds: 10}
  retry: {max_attempts: 3, wait_min_seconds: 1, wait_max_seconds: 2, retry_on: [RateLimitError]}
database:
  url: postgresql+asyncpg://default
retrieval:
  provider: pgvector
  embedding_model: bge
  embedding_dimensions: 1024
generation:
  target_total_words: 10000
  target_chapters: 10
  words_per_chapter: {min: 100, target: 200, max: 300}
  scenes_per_chapter: {min: 1, target: 2, max: 3}
  words_per_scene: {min: 50, target: 100, max: 150}
  context_budget_tokens: 2000
  active_context_scenes: 2
  genre: fantasy
  language: zh-CN
  pov: third-limited
  structure_template: three-act
quality:
  thresholds:
    scene_min_score: 0.7
    chapter_coherence_min_score: 0.8
    character_consistency_min_score: 0.75
    plot_logic_min_score: 0.7
  repetition: {window_words: 1000, similarity_threshold: 0.9}
artifact_store:
  mode: local
output:
  base_dir: ./output
logging:
  suppress: [urllib3]
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BESTSELLER__DATABASE__URL", "postgresql+asyncpg://process-env")

    settings = load_settings(
        config_path=config_path,
        local_config_path=tmp_path / "missing.yaml",
        env={},
    )

    assert settings.database.url == "postgresql+asyncpg://default"


def test_load_settings_reads_dotenv_layers_when_env_is_none(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "default.yaml"
    config_path.write_text(
        """
llm:
  mock: false
  planner: {model: planner-a, temperature: 0.5, max_tokens: 100, timeout_seconds: 10}
  writer: {model: writer-a, temperature: 0.6, max_tokens: 100, timeout_seconds: 10, stream: false}
  critic: {model: critic-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  summarizer: {model: sum-a, temperature: 0.2, max_tokens: 100, timeout_seconds: 10}
  editor: {model: editor-a, temperature: 0.4, max_tokens: 100, timeout_seconds: 10}
  retry: {max_attempts: 3, wait_min_seconds: 1, wait_max_seconds: 2, retry_on: [RateLimitError]}
database:
  url: postgresql+asyncpg://default
retrieval:
  provider: pgvector
  embedding_model: bge
  embedding_dimensions: 1024
generation:
  target_total_words: 10000
  target_chapters: 10
  words_per_chapter: {min: 100, target: 200, max: 300}
  scenes_per_chapter: {min: 1, target: 2, max: 3}
  words_per_scene: {min: 50, target: 100, max: 150}
  context_budget_tokens: 2000
  active_context_scenes: 2
  genre: fantasy
  language: zh-CN
  pov: third-limited
  structure_template: three-act
quality:
  thresholds:
    scene_min_score: 0.7
    chapter_coherence_min_score: 0.8
    character_consistency_min_score: 0.75
    plot_logic_min_score: 0.7
  repetition: {window_words: 1000, similarity_threshold: 0.9}
artifact_store:
  mode: local
output:
  base_dir: ./output
logging:
  suppress: [urllib3]
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "BESTSELLER__DATABASE__URL=postgresql+asyncpg://dotenv-base\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "BESTSELLER__DATABASE__URL=postgresql+asyncpg://dotenv-local",
                "BESTSELLER__LLM__MOCK=true",
                "BESTSELLER__RETRIEVAL__TOP_K=17",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    settings = load_settings(
        config_path=config_path,
        local_config_path=tmp_path / "missing.yaml",
        env=None,
    )

    assert settings.database.url == "postgresql+asyncpg://dotenv-local"
    assert settings.llm.mock is True
    assert settings.retrieval.top_k == 17

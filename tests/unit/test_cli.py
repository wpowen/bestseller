from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from bestseller.cli.main import app
from bestseller.domain.evaluation import (
    BenchmarkSuiteCatalogEntry,
    BenchmarkSuiteRunResult,
    BenchmarkSuiteSpec,
)
from bestseller.domain.workflow import WorkflowMaterializationResult

runner = CliRunner()
pytestmark = pytest.mark.unit


def test_status_command_outputs_json() -> None:
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["retrieval_provider"] == "pgvector"
    assert payload["llm_writer_model"]


def test_benchmark_list_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bestseller.cli.main.list_benchmark_suites",
        lambda: [
            BenchmarkSuiteCatalogEntry(
                suite_id="sample-books",
                title="样书评测基线",
                description="三套样书回归",
                path="/tmp/sample_books.yaml",
                case_count=3,
                case_ids=["doomsday-hoarding", "xuanhuan-progression", "urban-mystery"],
            )
        ],
    )

    result = runner.invoke(app, ["benchmark", "list"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["suite_id"] == "sample-books"


def test_prompt_pack_list_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bestseller.cli.main.list_prompt_packs",
        lambda: [
            type(
                "PromptPackStub",
                (),
                {
                    "key": "apocalypse-supply-chain",
                    "name": "末日囤货升级流",
                    "version": "1.0",
                    "description": "末日囤货",
                    "genres": ["末日"],
                    "tags": ["囤货"],
                },
            )()
        ],
    )

    result = runner.invoke(app, ["prompt-pack", "list"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["key"] == "apocalypse-supply-chain"


def test_prompt_pack_show_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bestseller.cli.main.get_prompt_pack",
        lambda key: type(
            "PromptPackStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "key": key,
                    "name": "末日囤货升级流",
                    "version": "1.0",
                }
            },
        )(),
    )

    result = runner.invoke(app, ["prompt-pack", "show", "apocalypse-supply-chain"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["key"] == "apocalypse-supply-chain"


def test_benchmark_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    def fake_load_benchmark_suite(suite_id: str, suite_file=None):
        assert suite_id == "sample-books"
        return BenchmarkSuiteSpec(
            suite_id="sample-books",
            title="样书评测基线",
            cases=[],
        )

    async def fake_run_benchmark_suite(session, settings, **kwargs):
        return BenchmarkSuiteRunResult(
            suite_id="sample-books",
            title="样书评测基线",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            report_path="/tmp/bench-report.json",
            case_results=[],
            passed_case_count=3,
            failed_case_count=0,
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.load_benchmark_suite", fake_load_benchmark_suite)
    monkeypatch.setattr("bestseller.cli.main.run_benchmark_suite", fake_run_benchmark_suite)

    result = runner.invoke(app, ["benchmark", "run", "sample-books", "--no-progress"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["suite_id"] == "sample-books"
    assert payload["passed_case_count"] == 3


def test_ui_serve_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_serve_web_app(*, host: str, port: int, open_browser: bool) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["open_browser"] = open_browser

    monkeypatch.setattr("bestseller.cli.main.serve_web_app", fake_serve_web_app)

    result = runner.invoke(app, ["ui", "serve", "--host", "127.0.0.1", "--port", "8899", "--open-browser"])

    assert result.exit_code == 0
    assert captured == {
        "host": "127.0.0.1",
        "port": 8899,
        "open_browser": True,
    }


def test_config_show_reads_custom_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
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
  url: postgresql+asyncpg://cli-test
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

    result = runner.invoke(app, ["config-show", "--format", "json", "--config-path", str(config_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["database"]["url"] == "postgresql+asyncpg://cli-test"


def test_db_render_sql_prints_postgresql_schema() -> None:
    result = runner.invoke(app, ["db", "render-sql"])

    assert result.exit_code == 0
    assert "CREATE TABLE projects" in result.stdout
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in result.stdout


def test_workflow_materialize_outline_accepts_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outline_path = tmp_path / "outline.json"
    outline_path.write_text(
        json.dumps(
            {
                "batch_name": "opening",
                "chapters": [
                    {
                        "chapter_number": 1,
                        "goal": "Open the story.",
                        "scenes": [{"scene_number": 1, "scene_type": "setup"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_materialize(session, project_slug, batch, requested_by="system"):
        assert project_slug == "my-story"
        assert batch.batch_name == "opening"
        return WorkflowMaterializationResult(
            workflow_run_id=uuid4(),
            project_id=uuid4(),
            batch_name=batch.batch_name,
            chapters_created=1,
            scenes_created=1,
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.materialize_chapter_outline_batch", fake_materialize)

    result = runner.invoke(
        app,
        ["workflow", "materialize-outline", "my-story", "--file", str(outline_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["batch_name"] == "opening"
    assert payload["chapters_created"] == 1


def test_workflow_materialize_story_bible_accepts_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    book_spec_path = tmp_path / "book_spec.json"
    book_spec_path.write_text(json.dumps({"title": "长夜巡航"}), encoding="utf-8")
    cast_spec_path = tmp_path / "cast_spec.json"
    cast_spec_path.write_text(json.dumps({"supporting_cast": []}), encoding="utf-8")

    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_materialize_story_bible(session, project_slug, **kwargs):
        assert project_slug == "my-story"
        assert kwargs["book_spec_content"]["title"] == "长夜巡航"
        assert kwargs["cast_spec_content"]["supporting_cast"] == []
        return type(
            "StoryBibleResultStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "workflow_run_id": str(uuid4()),
                    "project_id": str(uuid4()),
                    "applied_artifacts": ["book_spec", "cast_spec"],
                    "characters_upserted": 1,
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.materialize_story_bible", fake_materialize_story_bible)

    result = runner.invoke(
        app,
        [
            "workflow",
            "materialize-story-bible",
            "my-story",
            "--book-spec-file",
            str(book_spec_path),
            "--cast-spec-file",
            str(cast_spec_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["applied_artifacts"] == ["book_spec", "cast_spec"]


def test_workflow_materialize_narrative_graph_accepts_volume_plan_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    volume_plan_path = tmp_path / "volume_plan.json"
    volume_plan_path.write_text(json.dumps([{"volume_number": 1}]), encoding="utf-8")

    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_materialize_narrative_graph(session, project_slug, **kwargs):
        assert project_slug == "my-story"
        assert kwargs["volume_plan_content"] == [{"volume_number": 1}]
        return type(
            "NarrativeResultStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "workflow_run_id": str(uuid4()),
                    "project_id": str(uuid4()),
                    "plot_arc_count": 3,
                    "arc_beat_count": 8,
                    "clue_count": 2,
                    "payoff_count": 1,
                    "chapter_contract_count": 4,
                    "scene_contract_count": 12,
                    "source_artifact_ids": {},
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.materialize_narrative_graph",
        fake_materialize_narrative_graph,
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "materialize-narrative-graph",
            "my-story",
            "--volume-plan-file",
            str(volume_plan_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plot_arc_count"] == 3


def test_workflow_materialize_narrative_tree_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_materialize_narrative_tree(session, project_slug, **kwargs):
        assert project_slug == "my-story"
        return type(
            "NarrativeTreeResultStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "workflow_run_id": str(uuid4()),
                    "project_id": str(uuid4()),
                    "node_count": 18,
                    "node_type_counts": {"premise": 1, "chapter": 4},
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.materialize_narrative_tree",
        fake_materialize_narrative_tree,
    )

    result = runner.invoke(app, ["workflow", "materialize-narrative-tree", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["node_count"] == 18


def test_project_pipeline_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_run_project_pipeline(session, settings, project_slug, **kwargs):
        from bestseller.domain.pipeline import ProjectPipelineChapterSummary, ProjectPipelineResult

        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=uuid4(),
            project_slug=project_slug,
            chapter_results=[
                ProjectPipelineChapterSummary(
                    chapter_number=1,
                    workflow_run_id=uuid4(),
                    chapter_draft_version_no=1,
                    export_artifact_id=uuid4(),
                    approved_scene_count=2,
                )
            ],
            export_artifact_id=uuid4(),
            output_path=str(tmp_path / "output" / "project.md"),
            requires_human_review=False,
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.run_project_pipeline", fake_run_project_pipeline)

    result = runner.invoke(app, ["project", "pipeline", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project_slug"] == "my-story"
    assert payload["export_artifact_id"] is not None


def test_project_repair_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_run_project_repair(session, settings, project_slug, **kwargs):
        assert callable(kwargs["progress"])
        kwargs["progress"](
            "project_repair_completed",
            {
                "project_slug": project_slug,
                "output_path": str(tmp_path / "output" / "project.md"),
            },
        )
        from bestseller.domain.pipeline import ProjectRepairChapterSummary, ProjectRepairResult

        return ProjectRepairResult(
            workflow_run_id=uuid4(),
            project_id=uuid4(),
            project_slug=project_slug,
            pending_rewrite_task_count=2,
            superseded_task_count=2,
            processed_chapters=[
                ProjectRepairChapterSummary(
                    chapter_number=1,
                    workflow_run_id=uuid4(),
                    source_task_ids=[uuid4()],
                    requires_human_review=False,
                )
            ],
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            final_verdict="pass",
            export_artifact_id=uuid4(),
            output_path=str(tmp_path / "output" / "project.md"),
            remaining_pending_rewrite_count=0,
            requires_human_review=False,
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.run_project_repair", fake_run_project_repair)

    result = runner.invoke(app, ["project", "repair", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project_slug"] == "my-story"
    assert payload["superseded_task_count"] == 2
    assert payload["processed_chapters"][0]["chapter_number"] == 1
    assert "repair 流程结束" in result.stderr


def test_project_autowrite_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_run_autowrite_pipeline(session, settings, **kwargs):
        assert kwargs["auto_repair_on_attention"] is True
        assert callable(kwargs["progress"])
        assert kwargs["project_payload"].writing_profile is not None
        assert kwargs["project_payload"].writing_profile.market.prompt_pack_key == "apocalypse-supply-chain"
        kwargs["progress"](
            "planning_completed",
            {
                "project_slug": kwargs["project_payload"].slug,
                "chapter_count": 12,
            },
        )
        return type(
            "AutowriteStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": kwargs["project_payload"].slug,
                    "planning_workflow_run_id": str(uuid4()),
                    "project_workflow_run_id": str(uuid4()),
                    "repair_workflow_run_id": str(uuid4()),
                    "repair_attempted": True,
                    "output_dir": str(tmp_path / "output" / kwargs["project_payload"].slug),
                    "output_files": [],
                    "export_status": "skipped_requires_human_review",
                    "chapter_count": 12,
                    "final_verdict": "pass",
                    "requires_human_review": False,
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.run_autowrite_pipeline", fake_run_autowrite_pipeline)

    result = runner.invoke(
        app,
        [
            "project",
            "autowrite",
            "my-story",
            "My Story",
            "science-fantasy",
            "80000",
            "12",
            "--premise",
            "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
            "--prompt-pack",
            "apocalypse-supply-chain",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project_slug"] == "my-story"
    assert payload["chapter_count"] == 12
    assert payload["output_dir"].endswith("/output/my-story")
    assert payload["repair_attempted"] is True
    assert "规划生成完成" in result.stderr


def test_planning_generate_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_generate_novel_plan(session, settings, project_slug, premise, **kwargs):
        assert project_slug == "my-story"
        assert "导航员" in premise
        return type(
            "PlanningResultStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "workflow_run_id": str(uuid4()),
                    "project_id": str(uuid4()),
                    "premise": premise,
                    "artifacts": [],
                    "volume_count": 3,
                    "chapter_count": 12,
                    "llm_run_ids": [],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.generate_novel_plan", fake_generate_novel_plan)

    result = runner.invoke(
        app,
        [
            "planning",
            "generate",
            "my-story",
            "--premise",
            "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["volume_count"] == 3
    assert payload["chapter_count"] == 12


def test_planning_list_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_list_planning_artifacts(session, project_slug, **kwargs):
        assert project_slug == "my-story"
        return [
            type(
                "PlanningArtifactSummaryStub",
                (),
                {
                    "model_dump": lambda self, mode="json": {
                        "artifact_id": str(uuid4()),
                        "artifact_type": "book_spec",
                        "version_no": 2,
                        "scope_ref_id": None,
                        "status": "approved",
                        "schema_version": "1.0",
                        "created_at": "2026-03-19T00:00:00Z",
                        "notes": None,
                    }
                },
            )()
        ]

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.list_planning_artifacts", fake_list_planning_artifacts)

    result = runner.invoke(app, ["planning", "list", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["artifact_type"] == "book_spec"
    assert payload[0]["version_no"] == 2


def test_planning_show_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_get_planning_artifact_detail(session, project_slug, artifact_type, **kwargs):
        assert project_slug == "my-story"
        assert artifact_type.value == "book_spec"
        return type(
            "PlanningArtifactDetailStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "artifact_id": str(uuid4()),
                    "artifact_type": "book_spec",
                    "version_no": 1,
                    "scope_ref_id": None,
                    "status": "approved",
                    "schema_version": "1.0",
                    "created_at": "2026-03-19T00:00:00Z",
                    "notes": None,
                    "content": {"title": "长夜巡航"},
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.get_planning_artifact_detail",
        fake_get_planning_artifact_detail,
    )

    result = runner.invoke(app, ["planning", "show", "my-story", "book_spec"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["content"]["title"] == "长夜巡航"


def test_retrieval_search_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_search_project_retrieval(session, settings, project_slug, query, **kwargs):
        return type(
            "RetrievalResultStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "query_text": query,
                    "chunks": [
                        {
                            "source_type": "character",
                            "source_id": str(uuid4()),
                            "chunk_index": 0,
                            "score": 0.9,
                            "chunk_text": "角色 沈砚 目标 找证据",
                            "metadata": {"kind": "character"},
                        }
                    ],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.search_project_retrieval", fake_search_project_retrieval)

    result = runner.invoke(
        app,
        ["retrieval", "search", "my-story", "--query", "沈砚 找证据"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["chunks"][0]["source_type"] == "character"


def test_project_structure_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_build_project_structure(session, project_slug):
        assert project_slug == "my-story"
        return type(
            "ProjectStructureStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": "my-story",
                    "title": "My Story",
                    "status": "writing",
                    "target_word_count": 80000,
                    "target_chapters": 12,
                    "current_volume_number": 1,
                    "current_chapter_number": 2,
                    "total_chapters": 2,
                    "total_scenes": 3,
                    "volumes": [
                        {
                            "id": str(uuid4()),
                            "volume_number": 1,
                            "title": "第一卷",
                            "status": "writing",
                            "target_word_count": 40000,
                            "target_chapter_count": 6,
                            "chapters": [],
                        }
                    ],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.build_project_structure", fake_build_project_structure)

    result = runner.invoke(app, ["project", "structure", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project_slug"] == "my-story"
    assert payload["total_scenes"] == 3


def test_story_bible_show_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_build_story_bible_overview(session, project_slug, **kwargs):
        assert project_slug == "my-story"
        return type(
            "StoryBibleOverviewStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": "my-story",
                    "title": "My Story",
                    "world_rules": [{"rule_code": "R001", "name": "记录优先", "description": "..." }],
                    "locations": [{"name": "边境星港", "location_type": "station", "key_rule_codes": []}],
                    "factions": [{"name": "帝国档案局"}],
                    "characters": [{"name": "沈砚", "role": "protagonist"}],
                    "relationships": [{"character_a": "沈砚", "character_b": "顾临", "relationship_type": "旧搭档", "strength": 0.6}],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.build_story_bible_overview", fake_build_story_bible_overview)

    result = runner.invoke(app, ["story-bible", "show", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["characters"][0]["name"] == "沈砚"
    assert payload["relationships"][0]["relationship_type"] == "旧搭档"


def test_narrative_tree_show_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_build_narrative_tree_overview(session, project_slug):
        assert project_slug == "my-story"
        return type(
            "NarrativeTreeOverviewStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": "my-story",
                    "title": "My Story",
                    "nodes": [
                        {
                            "id": str(uuid4()),
                            "node_path": "/book/premise",
                            "parent_path": "/book",
                            "depth": 2,
                            "node_type": "premise",
                            "title": "作品 premise",
                            "summary": "调查被篡改的航线。",
                            "body_md": "# 作品 premise",
                            "source_type": "project",
                            "source_ref_id": str(uuid4()),
                            "scope_level": "project",
                            "scope_volume_number": None,
                            "scope_chapter_number": None,
                            "scope_scene_number": None,
                            "metadata": {},
                        }
                    ],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.build_narrative_tree_overview",
        fake_build_narrative_tree_overview,
    )

    result = runner.invoke(app, ["narrative", "tree-show", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["node_path"] == "/book/premise"


def test_narrative_path_show_and_search_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_get_project_by_slug(session, project_slug):
        return type("ProjectStub", (), {"id": uuid4(), "slug": project_slug})()

    async def fake_get_narrative_tree_node_by_path(session, project_slug, path):
        return type(
            "NodeStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "id": str(uuid4()),
                    "node_path": path,
                    "parent_path": "/chapters/001",
                    "depth": 3,
                    "node_type": "chapter_contract",
                    "title": "第1章 contract",
                    "summary": "本章推进主线。",
                    "body_md": "# contract",
                    "source_type": "chapter_contract",
                    "source_ref_id": str(uuid4()),
                    "scope_level": "chapter",
                    "scope_volume_number": None,
                    "scope_chapter_number": 1,
                    "scope_scene_number": None,
                    "metadata": {},
                }
            },
        )()

    async def fake_search_narrative_tree_for_project(session, project, query, **kwargs):
        return type(
            "SearchStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(project.id),
                    "query_text": query,
                    "preferred_paths": kwargs.get("preferred_paths", []),
                    "hits": [
                        {
                            "node_path": "/chapters/001/contract",
                            "node_type": "chapter_contract",
                            "title": "第1章 contract",
                            "summary": "本章推进主线。",
                            "score": 0.91,
                            "source_type": "chapter_contract",
                            "scope_level": "chapter",
                            "metadata": {},
                        }
                    ],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        "bestseller.cli.main.get_narrative_tree_node_by_path",
        fake_get_narrative_tree_node_by_path,
    )
    monkeypatch.setattr(
        "bestseller.cli.main.search_narrative_tree_for_project",
        fake_search_narrative_tree_for_project,
    )

    path_result = runner.invoke(
        app,
        ["narrative", "path-show", "my-story", "--path", "/chapters/001/contract"],
    )
    assert path_result.exit_code == 0
    assert json.loads(path_result.stdout)["node_type"] == "chapter_contract"

    search_result = runner.invoke(
        app,
        ["narrative", "search", "my-story", "--query", "主线 推进", "--path", "/chapters/001"],
    )
    assert search_result.exit_code == 0
    payload = json.loads(search_result.stdout)
    assert payload["hits"][0]["node_path"] == "/chapters/001/contract"


def test_rewrite_cascade_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_run_rewrite_cascade(session, settings, project_slug, **kwargs):
        return type(
            "RewriteCascadeStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "rewrite_task_id": str(uuid4()),
                    "project_id": str(uuid4()),
                    "processed_chapters": [
                        {
                            "chapter_number": 2,
                            "workflow_run_id": str(uuid4()),
                            "requires_human_review": False,
                        }
                    ],
                    "impact_count": 3,
                    "refreshed": True,
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.run_rewrite_cascade", fake_run_rewrite_cascade)

    result = runner.invoke(
        app,
        ["rewrite", "cascade", "my-story", "--chapter-number", "1", "--scene-number", "1"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["processed_chapters"][0]["chapter_number"] == 2


def test_project_review_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_review_project_consistency(session, settings, project_slug, **kwargs):
        return (
            type(
                "ProjectReviewResultStub",
                (),
                {
                    "verdict": "pass",
                    "severity_max": "low",
                    "scores": type(
                        "ProjectScoreStub",
                        (),
                        {"model_dump": lambda self, mode="json": {"overall": 0.92}},
                    )(),
                    "findings": [],
                },
            )(),
            type("ProjectReportStub", (), {"id": uuid4()})(),
            type("ProjectQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.review_project_consistency",
        fake_review_project_consistency,
    )

    result = runner.invoke(app, ["project", "review", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "pass"
    assert payload["findings_count"] == 0


def test_scene_draft_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_generate_scene_draft(session, project_slug, chapter_number, scene_number, **kwargs):
        return type(
            "SceneDraftStub",
            (),
            {
                "id": uuid4(),
                "scene_card_id": uuid4(),
                "version_no": 1,
                "word_count": 512,
                "model_name": "mock-writer",
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.generate_scene_draft", fake_generate_scene_draft)

    result = runner.invoke(app, ["scene", "draft", "my-story", "1", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version_no"] == 1
    assert payload["model_name"] == "mock-writer"


def test_scene_context_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_build_scene_writer_context(session, settings, project_slug, chapter_number, scene_number):
        assert project_slug == "my-story"
        return type(
            "SceneContextStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": project_slug,
                    "chapter_id": str(uuid4()),
                    "scene_id": str(uuid4()),
                    "chapter_number": chapter_number,
                    "scene_number": scene_number,
                    "query_text": "长夜巡航 失准星图",
                    "story_bible": {"logline": "调查被篡改的航线。"},
                    "recent_scene_summaries": [{"summary": "上一场发现异常。"}],
                    "recent_timeline_events": [{"event_name": "发现异常"}],
                    "participant_canon_facts": [{"subject_label": "沈砚"}],
                    "retrieval_chunks": [{"source_type": "scene_draft", "chunk_text": "过去场景"}],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.build_scene_writer_context",
        fake_build_scene_writer_context,
    )

    result = runner.invoke(app, ["scene", "context", "my-story", "1", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project_slug"] == "my-story"
    assert payload["recent_scene_summaries"][0]["summary"] == "上一场发现异常。"


def test_narrative_show_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_build_narrative_overview(session, project_slug):
        assert project_slug == "my-story"
        return type(
            "NarrativeOverviewStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": project_slug,
                    "title": "长夜巡航",
                    "plot_arcs": [{"arc_code": "main_plot"}],
                    "arc_beats": [{"summary": "第1章承担主线推进。"}],
                    "clues": [{"clue_code": "clue-001"}],
                    "payoffs": [{"payoff_code": "payoff-001"}],
                    "chapter_contracts": [{"contract_summary": "本章要抛出主线异常。"}],
                    "scene_contracts": [{"contract_summary": "本场必须抛出异常航标。"}],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.build_narrative_overview", fake_build_narrative_overview)

    result = runner.invoke(app, ["narrative", "show", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plot_arcs"][0]["arc_code"] == "main_plot"
    assert payload["chapter_contracts"][0]["contract_summary"] == "本章要抛出主线异常。"


def test_scene_pipeline_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        from bestseller.domain.pipeline import ScenePipelineResult

        return ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=uuid4(),
            chapter_id=uuid4(),
            scene_id=uuid4(),
            chapter_number=chapter_number,
            scene_number=scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=2,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=False,
            llm_run_ids=[],
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.run_scene_pipeline", fake_run_scene_pipeline)

    result = runner.invoke(app, ["scene", "pipeline", "my-story", "1", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["final_verdict"] == "pass"
    assert payload["rewrite_iterations"] == 1


def test_chapter_assemble_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_assemble_chapter_draft(session, project_slug, chapter_number, **kwargs):
        return type(
            "ChapterDraftStub",
            (),
            {
                "id": uuid4(),
                "chapter_id": uuid4(),
                "version_no": 1,
                "word_count": 2048,
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.assemble_chapter_draft", fake_assemble_chapter_draft)

    result = runner.invoke(app, ["chapter", "assemble", "my-story", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version_no"] == 1
    assert payload["word_count"] == 2048


def test_chapter_context_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_build_chapter_writer_context(session, settings, project_slug, chapter_number):
        assert project_slug == "my-story"
        return type(
            "ChapterContextStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "project_id": str(uuid4()),
                    "project_slug": project_slug,
                    "chapter_id": str(uuid4()),
                    "chapter_number": chapter_number,
                    "query_text": "长夜巡航 静默航道",
                    "chapter_goal": "推进调查",
                    "story_bible": {"logline": "调查被篡改的航线。"},
                    "chapter_scenes": [{"scene_number": 1, "title": "旧搭档回舰"}],
                    "previous_scene_summaries": [{"summary": "上一场发现异常。"}],
                    "recent_timeline_events": [{"event_name": "发现异常"}],
                    "retrieval_chunks": [{"source_type": "chapter_draft", "chunk_text": "本章之前的关键线索"}],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.build_chapter_writer_context",
        fake_build_chapter_writer_context,
    )

    result = runner.invoke(app, ["chapter", "context", "my-story", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project_slug"] == "my-story"
    assert payload["chapter_scenes"][0]["title"] == "旧搭档回舰"


def test_chapter_review_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_review_chapter_draft(session, settings, project_slug, chapter_number, **kwargs):
        return (
            type(
                "ChapterReviewResultStub",
                (),
                {
                    "verdict": "rewrite",
                    "severity_max": "medium",
                    "scores": type(
                        "ChapterScoreStub",
                        (),
                        {"model_dump": lambda self, mode="json": {"overall": 0.66, "coverage": 0.54}},
                    )(),
                },
            )(),
            type("ChapterReportStub", (), {"id": uuid4()})(),
            type("ChapterQualityStub", (), {"id": uuid4()})(),
            type("ChapterRewriteTaskStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.review_chapter_draft", fake_review_chapter_draft)

    result = runner.invoke(app, ["chapter", "review", "my-story", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "rewrite"
    assert payload["rewrite_task_id"] is not None


def test_chapter_rewrite_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_rewrite_chapter_from_task(
        session,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type(
                "ChapterDraftStub",
                (),
                {
                    "id": uuid4(),
                    "version_no": 2,
                    "word_count": 1880,
                },
            )(),
            type(
                "ChapterRewriteTaskStub",
                (),
                {
                    "id": uuid4(),
                    "status": "completed",
                },
            )(),
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.rewrite_chapter_from_task",
        fake_rewrite_chapter_from_task,
    )

    result = runner.invoke(app, ["chapter", "rewrite", "my-story", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version_no"] == 2
    assert payload["rewrite_task_status"] == "completed"


def test_chapter_pipeline_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        from bestseller.domain.pipeline import ChapterPipelineResult, ChapterPipelineSceneSummary

        return ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=uuid4(),
            chapter_id=uuid4(),
            chapter_number=chapter_number,
            scene_results=[
                ChapterPipelineSceneSummary(
                    scene_number=1,
                    workflow_run_id=uuid4(),
                    final_verdict="pass",
                    rewrite_iterations=1,
                    current_draft_version_no=2,
                )
            ],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            export_artifact_id=uuid4(),
            output_path=str(tmp_path / "output" / "chapter-001.md"),
            requires_human_review=False,
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.run_chapter_pipeline", fake_run_chapter_pipeline)

    result = runner.invoke(
        app,
        ["chapter", "pipeline", "my-story", "1", "--export-markdown"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["chapter_draft_version_no"] == 1
    assert payload["export_artifact_id"] is not None


def test_export_markdown_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_export_project_markdown(session, settings, project_slug):
        return (
            type(
                "ExportStub",
                (),
                {
                    "id": uuid4(),
                    "storage_uri": str(tmp_path / "output" / "project.md"),
                    "checksum": "a" * 64,
                    "version_label": "project-current",
                },
            )(),
            tmp_path / "output" / "project.md",
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.export_project_markdown", fake_export_project_markdown)

    result = runner.invoke(app, ["export", "markdown", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version_label"] == "project-current"


def test_export_docx_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_export_project_docx(session, settings, project_slug):
        return (
            type(
                "ExportStub",
                (),
                {
                    "id": uuid4(),
                    "storage_uri": str(tmp_path / "output" / "project.docx"),
                    "checksum": "b" * 64,
                    "version_label": "project-current",
                },
            )(),
            tmp_path / "output" / "project.docx",
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.export_project_docx", fake_export_project_docx)

    result = runner.invoke(app, ["export", "docx", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["output_path"].endswith("project.docx")


def test_export_epub_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_export_project_epub(session, settings, project_slug):
        return (
            type(
                "ExportStub",
                (),
                {
                    "id": uuid4(),
                    "storage_uri": str(tmp_path / "output" / "project.epub"),
                    "checksum": "c" * 64,
                    "version_label": "project-current",
                },
            )(),
            tmp_path / "output" / "project.epub",
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.export_project_epub", fake_export_project_epub)

    result = runner.invoke(app, ["export", "epub", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["output_path"].endswith("project.epub")


def test_export_pdf_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_export_project_pdf(session, settings, project_slug):
        return (
            type(
                "ExportStub",
                (),
                {
                    "id": uuid4(),
                    "storage_uri": str(tmp_path / "output" / "project.pdf"),
                    "checksum": "d" * 64,
                    "version_label": "project-current",
                },
            )(),
            tmp_path / "output" / "project.pdf",
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.export_project_pdf", fake_export_project_pdf)

    result = runner.invoke(app, ["export", "pdf", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["output_path"].endswith("project.pdf")


def test_export_pdf_command_reports_optional_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_export_project_pdf(session, settings, project_slug):
        raise RuntimeError("PDF export requires reportlab. Install optional dependencies.")

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.export_project_pdf", fake_export_project_pdf)

    result = runner.invoke(app, ["export", "pdf", "my-story"])

    assert result.exit_code == 2
    assert "reportlab" in result.stderr


def test_publish_profile_init_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_init_amazon_kdp_profile(session, project_slug, overwrite=False):
        assert project_slug == "my-story"
        assert overwrite is False
        return type(
            "ProfileStub",
            (),
            {
                "model_dump": lambda self, mode="json", exclude_none=True: {
                    "language": "en-US",
                    "book_title": "My Story",
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.init_amazon_kdp_profile", fake_init_amazon_kdp_profile)

    result = runner.invoke(app, ["publish-profile", "init", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["language"] == "en-US"


def test_publish_profile_show_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_show_amazon_kdp_profile(session, project_slug):
        assert project_slug == "my-story"
        return type(
            "ProfileStub",
            (),
            {
                "model_dump": lambda self, mode="json", exclude_none=True: {
                    "author_display_name": "Owen Example",
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.show_amazon_kdp_profile", fake_show_amazon_kdp_profile)

    result = runner.invoke(app, ["publish-profile", "show", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["author_display_name"] == "Owen Example"


def test_export_amazon_kdp_validate_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_validate_amazon_kdp_project(session, project_slug):
        assert project_slug == "my-story"
        return type(
            "ValidationStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "status": "pass_with_warnings",
                    "blocking_count": 0,
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.validate_amazon_kdp_project", fake_validate_amazon_kdp_project)

    result = runner.invoke(app, ["export", "amazon-kdp", "validate", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass_with_warnings"


def test_export_amazon_kdp_package_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_package_amazon_kdp_project(session, settings, project_slug, strict=True):
        assert project_slug == "my-story"
        assert strict is True
        return type(
            "PackageStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "package_dir": "/tmp/output/my-story/amazon-kdp",
                    "validation_status": "pass",
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.package_amazon_kdp_project", fake_package_amazon_kdp_project)

    result = runner.invoke(app, ["export", "amazon-kdp", "package", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["validation_status"] == "pass"


def test_rewrite_impacts_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_refresh_rewrite_impacts(session, project_slug, **kwargs):
        return type(
            "RewriteImpactResultStub",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "rewrite_task_id": str(uuid4()),
                    "impact_count": 2,
                    "max_impact_level": "must",
                    "impacts": [
                        {
                            "impacted_type": "scene",
                            "impacted_id": str(uuid4()),
                            "impact_level": "must",
                            "impact_score": 0.91,
                            "reason": "后续场景需要回看。",
                        }
                    ],
                }
            },
        )()

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "bestseller.cli.main.refresh_rewrite_impacts",
        fake_refresh_rewrite_impacts,
    )

    result = runner.invoke(app, ["rewrite", "impacts", "my-story", "--chapter-number", "1", "--scene-number", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["impact_count"] == 2
    assert payload["max_impact_level"] == "must"


def test_canon_list_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_list_canon_facts(session, project_slug, **kwargs):
        return [
            type(
                "CanonFactStub",
                (),
                {
                    "id": uuid4(),
                    "subject_type": "character",
                    "subject_label": "沈砚",
                    "predicate": "last_known_state",
                    "fact_type": "state",
                    "value_json": {"stance": "被迫接单"},
                    "valid_from_chapter_no": 1,
                    "valid_to_chapter_no": None,
                    "source_scene_id": uuid4(),
                    "tags": ["chapter:1"],
                    "is_current": True,
                },
            )()
        ]

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.list_canon_facts", fake_list_canon_facts)

    result = runner.invoke(app, ["canon", "list", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["subject_label"] == "沈砚"


def test_timeline_list_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_list_timeline_events(session, project_slug, **kwargs):
        return [
            type(
                "TimelineStub",
                (),
                {
                    "id": uuid4(),
                    "event_name": "封港命令",
                    "event_type": "setup",
                    "story_time_label": "深夜",
                    "story_order": 1.01,
                    "participant_ids": ["沈砚", "港务官"],
                    "consequences": ["抛出禁令任务"],
                    "chapter_id": uuid4(),
                    "scene_card_id": uuid4(),
                },
            )()
        ]

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.list_timeline_events", fake_list_timeline_events)

    result = runner.invoke(app, ["timeline", "list", "my-story"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["event_name"] == "封港命令"


def test_scene_review_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_review_scene_draft(session, settings, project_slug, chapter_number, scene_number):
        from bestseller.domain.review import (
            SceneReviewFinding,
            SceneReviewResult,
            SceneReviewScores,
        )

        return (
            SceneReviewResult(
                verdict="rewrite",
                severity_max="medium",
                scores=SceneReviewScores(
                    overall=0.58,
                    goal=0.31,
                    conflict=0.72,
                    conflict_clarity=0.66,
                    emotion=0.71,
                    emotional_movement=0.62,
                    dialogue=0.24,
                    style=0.82,
                    hook=0.7,
                    hook_strength=0.63,
                    payoff_density=0.48,
                    voice_consistency=0.76,
                    contract_alignment=0.64,
                    character_voice_distinction=0.55,
                    thematic_resonance=0.60,
                    worldbuilding_integration=0.65,
                    prose_variety=0.70,
                    moral_complexity=0.50,
                ),
                findings=[
                    SceneReviewFinding(
                        category="goal",
                        severity="medium",
                        message="字数不足。",
                    )
                ],
                evidence_summary={},
                rewrite_instructions="请重写",
            ),
            type("ReportStub", (), {"id": uuid4()})(),
            type("QualityStub", (), {"id": uuid4()})(),
            type("RewriteTaskStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    async def fake_list_rewrite_impacts(session, project_slug, **kwargs):
        return []

    monkeypatch.setattr("bestseller.cli.main.review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr("bestseller.cli.main.list_rewrite_impacts", fake_list_rewrite_impacts)

    result = runner.invoke(app, ["scene", "review", "my-story", "1", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "rewrite"
    assert payload["rewrite_task_id"] is not None


def test_scene_rewrite_command(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_rewrite_scene_from_task(
        session,
        project_slug,
        chapter_number,
        scene_number,
        rewrite_task_id=None,
        **kwargs,
    ):
        return (
            type(
                "SceneDraftStub",
                (),
                {"id": uuid4(), "version_no": 2, "word_count": 840},
            )(),
            type("RewriteTaskStub", (), {"id": uuid4(), "status": "completed"})(),
        )

    monkeypatch.setattr("bestseller.cli.main.session_scope", fake_session_scope)
    monkeypatch.setattr("bestseller.cli.main.rewrite_scene_from_task", fake_rewrite_scene_from_task)

    result = runner.invoke(app, ["scene", "rewrite", "my-story", "1", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version_no"] == 2
    assert payload["rewrite_task_status"] == "completed"

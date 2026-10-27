"""Real local checkpoint commits and exact reconstruction of judged windows."""

import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import planner
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.outline_recovery_checkpoint import (
    checkpoint_outline_candidate,
    reconstruct_judged_batches,
    reconstruct_with_historical_producer,
    recovery_binding,
)
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


def _sources(start=1, genre="fantasy", language="zh-CN"):
    metadata = {
        "book_design_snapshot": {"snapshot_id": "approved", "source_hash": "a" * 64},
        "derived_input_revision": {"candidate_hash": "revision-1"},
        "rolling_outline_plan": {"window_start": start, "window_end": start + 8},
    }
    project = ProjectModel(
        id=uuid4(),
        slug=f"checkpoint-{uuid4().hex}",
        title="Approved title",
        genre=genre,
        language=language,
        target_chapters=50,
        target_word_count=130000,
        metadata_json=metadata,
    )
    artifacts, allowed_ids = [], set()
    for first in range(start, start + 9, 3):
        payload = ChapterOutlineBatchInput.model_validate(
            {
                "batch_name": "retained-batch",
                "chapters": [
                    {
                        "chapter_number": n,
                        "volume_number": 1,
                        "title": f"行动{n}",
                        "goal": f"Recover evidence at checkpoint {n}",
                    }
                    for n in range(first, first + 3)
                ],
            }
        ).model_dump(mode="json", by_alias=True)
        payload["_meta"] = {
            "reused": False,
            "cross_batch_event_similarity_candidates": [{"chapter_number": first}],
            "story_enhancer_distribution_candidates": [{"effect": f"effect-{first}"}],
        }
        source_id = uuid4()
        allowed_ids.add(source_id)
        artifacts.append(
            SimpleNamespace(
                id=uuid4(),
                version_no=len(artifacts) + 1,
                content=payload,
                source_run_id=source_id,
                status="approved",
            )
        )
    combined = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [copy.deepcopy(c) for a in artifacts for c in a.content["chapters"]],
        }
    ).model_dump(mode="json", by_alias=True)
    combined["_meta"] = {
        "batch_size": 3,
        "batch_count": 3,
        "batch_ranges": [[n, n + 2] for n in range(start, start + 9, 3)],
        "reused_batch_count": 0,
        "reused": False,
        "cross_batch_event_similarity_candidates": [
            {"chapter_number": n} for n in range(start, start + 9, 3)
        ],
        "story_enhancer_distribution_candidates": [
            {"effect": f"effect-{n}"} for n in range(start, start + 9, 3)
        ],
    }
    binding = recovery_binding(metadata, 1, [start, start + 8])
    report = {
        "passed": False,
        "overall_score": 0.62,
        "repair_directives": ["repair decision"],
        "graded_outline_hash": content_hash(combined),
        "outline_repair_dependency_hash": binding["dependency_hash"],
    }
    workflow_metadata = {
        "outline_repair_dependency_hash": binding["dependency_hash"],
        "failure_baseline_revision_key": "revision-1",
        "derived_source_candidate_hash": "revision-1",
    }
    return project, combined, artifacts, allowed_ids, report, workflow_metadata


@pytest.mark.parametrize("start,genre,language", [(1, "fantasy", "zh-CN"), (10, "romance", "en")])
@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "hash",
        "revision",
        "dependency",
        "snapshot",
        "window",
        "foreign_source",
        "missing_batch",
        "wrong_volume",
    ],
)
def test_reconstruction_requires_exact_grade_and_same_window_sources(
    start, genre, language, corruption
):
    project, combined, artifacts, allowed_ids, report, workflow_meta = _sources(
        start, genre, language
    )
    if corruption == "hash":
        report["graded_outline_hash"] = "wrong"
    elif corruption == "revision":
        workflow_meta["failure_baseline_revision_key"] = "old"
    elif corruption == "dependency":
        report["outline_repair_dependency_hash"] = "old"
    elif corruption == "snapshot":
        workflow_meta["outline_repair_source_snapshot_hash"] = "old"
    elif corruption == "window":
        project.metadata_json["rolling_outline_plan"] = {
            "window_start": start + 9,
            "window_end": start + 17,
        }
    elif corruption == "foreign_source":
        allowed_ids.remove(artifacts[0].source_run_id)
    elif corruption == "missing_batch":
        artifacts.pop()
    elif corruption == "wrong_volume":
        artifacts[0].content["chapters"][0]["volume_number"] = 2
    recovered = reconstruct_judged_batches(
        artifacts=artifacts[::-1],
        allowed_source_ids=allowed_ids,
        report=report,
        metadata=project.metadata_json,
        workflow_metadata=workflow_meta,
        volume_number=1,
    )
    assert recovered == (combined if corruption is None else None)


@pytest.mark.parametrize("start,genre,language", [(1, "fantasy", "zh-CN"), (10, "romance", "en")])
@pytest.mark.parametrize("wrong_output", [False, True])
def test_producer_validator_replay_requires_original_exact_digest(
    start, genre, language, wrong_output
):
    project, raw, artifacts, allowed_ids, report, workflow_meta = _sources(start, genre, language)
    expected = copy.deepcopy(raw)
    expected["chapters"][0]["goal"] += "; original producer enrichment"
    report["graded_outline_hash"] = content_hash(expected)
    original_report = copy.deepcopy(report)
    original_artifacts = [copy.deepcopy(a.content) for a in artifacts]
    calls = []

    def producer(candidate):
        calls.append(copy.deepcopy(candidate))
        candidate["chapters"][0]["goal"] += (
            "; wrong source" if wrong_output else "; original producer enrichment"
        )
        candidate["_meta"] = {"must_not_replace_original_batch_metadata": True}
        return candidate

    recovered = reconstruct_judged_batches(
        artifacts=artifacts[::-1],
        allowed_source_ids=allowed_ids,
        report=report,
        metadata=project.metadata_json,
        workflow_metadata=workflow_meta,
        volume_number=1,
        producer_validator=producer,
    )
    assert len(calls) == 1
    assert recovered == (None if wrong_output else expected)
    assert report == original_report
    assert [a.content for a in artifacts] == original_artifacts


@pytest.mark.parametrize("validator_rejects", [False, True])
async def test_producer_context_reads_only_approved_versions_before_batch(
    tmp_path, monkeypatch, validator_rejects
):
    from datetime import timedelta

    from bestseller.services.pipelines import _expand_volume_plan_into_rolling_windows
    from bestseller.services.rolling_outline import rolling_window_schedule_hash

    project, raw, artifacts, allowed_ids, report, workflow_meta = _sources()
    batch_time = datetime.now(UTC)
    for artifact in artifacts:
        artifact.created_at = batch_time
    canonical_plan = {"volumes": [{"volume_number": 1, "chapter_count_target": 50}]}
    windows = _expand_volume_plan_into_rolling_windows(canonical_plan["volumes"], window_size=8)
    schedule = [
        {
            "volume_number": w["volume_number"],
            "window_index": w["rolling_window_index"],
            "window_start": w["start_chapter_number"],
            "window_end": w["end_chapter_number"],
        }
        for w in windows
    ]
    project.metadata_json.update(
        rolling_outline_windows=schedule,
        rolling_outline_windows_hash=rolling_window_schedule_hash(schedule),
    )
    expected = copy.deepcopy(raw)
    expected["chapters"][0]["goal"] += "; cast-v1 producer enrichment"
    report["graded_outline_hash"] = content_hash(expected)
    original_report = copy.deepcopy(report)
    path = tmp_path / "outline_producer_sources_test.sqlite3"
    assert "test" in path.name
    engine = create_engine(f"sqlite:///{path}")
    table = PlanningArtifactVersionModel.__table__.to_metadata(MetaData())
    table.c.content.type = JSON()
    with engine.begin() as connection:
        connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
    old_ids = {}
    with Session(engine) as session:
        for kind, content in (("cast_spec", {"version": 1}), ("volume_plan", canonical_plan)):
            for version in (1, 2):
                row = PlanningArtifactVersionModel(
                    id=uuid4(),
                    project_id=project.id,
                    artifact_type=kind,
                    version_no=version,
                    schema_version="1",
                    status="approved",
                    scope_ref_id=None,
                    content=content if version == 1 else {"version": 2},
                    created_at=batch_time + timedelta(minutes=version - 1.5),
                )
                session.add(row)
                if version == 1:
                    old_ids[kind] = str(row.id)
        session.commit()
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", AsyncMock(return_value=[]))
    validated = []

    def original_validator(candidate, **context):
        validated.append(context)
        assert context["cast_spec"] == {"version": 1}
        assert context["expected_count"] == 9
        assert context["chapter_number_offset"] == 1
        assert context["strict_field_degeneracy"] is True
        if validator_rejects:
            raise planner.PlannerFallbackError("source contract cannot be replayed")
        candidate["chapters"][0]["goal"] += "; cast-v1 producer enrichment"
        return candidate

    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", original_validator)
    try:
        with Session(engine) as session:
            recovered, receipt = await reconstruct_with_historical_producer(
                AsyncSessionFacade(session),
                project=project,
                artifacts=artifacts,
                allowed_source_ids=allowed_ids,
                report=report,
                workflow_metadata=workflow_meta,
                volume_number=1,
            )
        assert len(validated) == 1
        assert recovered == (None if validator_rejects else expected)
        if not validator_rejects:
            assert {kind: row["id"] for kind, row in receipt["source_artifacts"].items()} == old_ids
        assert report == original_report
    finally:
        engine.dispose()


async def test_historical_restore_rebuilds_partial_artifacts_with_same_run_source_ids():
    project, combined, artifacts, allowed_ids, report, workflow_meta = _sources()
    workflow_id = uuid4()
    step = SimpleNamespace(
        workflow_run_id=workflow_id, output_ref=report, created_at=datetime.now(UTC)
    )

    class HistoricalSession:
        async def scalars(self, query):
            entity = query.column_descriptions[0]["entity"]
            rows = (
                [step]
                if entity is WorkflowStepRunModel
                else artifacts[::-1]
                if entity is PlanningArtifactVersionModel
                else list(allowed_ids)
            )
            return SimpleNamespace(all=lambda: rows)

        async def scalar(self, query):
            return (
                SimpleNamespace(metadata_json=workflow_meta)
                if query.column_descriptions[0]["entity"] is WorkflowRunModel
                else artifacts[-1]
            )

    recovered, verdict = await planner._restore_best_historical_outline_repair_baseline(
        HistoricalSession(),
        project=project,
        volume_number=1,
    )
    assert recovered == combined
    assert content_hash(recovered) == verdict["graded_outline_hash"]
    assert verdict["overall_score"] == 0.62 and verdict["passed"] is False
    assert verdict["restored_from_same_workflow_batches"]


class AsyncSessionFacade:
    def __init__(self, session):
        self.session = session

    async def scalar(self, query):
        return self.session.scalar(query)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()


@pytest.mark.parametrize("failure", ["paused_judge", "invalid_patch"])
async def test_real_checkpoint_survives_judge_pause_or_later_patch_rollback(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "outline_checkpoint_test.sqlite3"
    assert "test" in path.name
    engine = create_engine(f"sqlite:///{path}")
    project, candidate, _artifacts, _ids, _report, workflow_meta = _sources()
    project_id, workflow_id = project.id, uuid4()
    with engine.begin() as connection:
        for model in (ProjectModel, WorkflowRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
    monkeypatch.setattr(planner, "is_strict_prewrite_project", lambda _: True)
    monkeypatch.setattr(planner, "strict_blocks", lambda *args: False)
    monkeypatch.setattr(planner, "_outline_judge_project_brief", lambda *a, **k: {})
    monkeypatch.setattr(
        "bestseller.services.prompt_packs.resolve_prompt_pack", lambda *a, **k: None
    )
    monkeypatch.setattr(planner, "create_workflow_step_run", AsyncMock())
    paused = ExecutionBudgetError("execution_authorization_closed")

    async def judge(*args, **kwargs):
        with Session(engine) as reader:
            saved = reader.get(ProjectModel, project_id).metadata_json
            pending = next(iter(saved["outline_commercial_judge_checkpoints"].values()))[
                "latest_candidate"
            ]
            assert pending["candidate"] == candidate and pending["report"] is None
        if failure == "paused_judge":
            raise paused
        return LLMQualityJudgeResult(scope="outline", passed=False, overall_score=0.62)

    monkeypatch.setattr(
        "bestseller.services.outline_llm_judge.judge_outline_commercial_readiness", judge
    )
    try:
        with Session(engine, expire_on_commit=False) as session:
            session.add_all(
                [
                    project,
                    WorkflowRunModel(
                        id=workflow_id,
                        workflow_type="generate_volume_plan",
                        status="running",
                        metadata_json=workflow_meta,
                    ),
                ]
            )
            session.commit()
            try:
                await planner._run_planner_outline_commercial_judge(
                    AsyncSessionFacade(session),
                    SimpleNamespace(pipeline=SimpleNamespace()),
                    project=project,
                    workflow_run_id=workflow_id,
                    step_name="volume_1_outline_commercial_judge",
                    step_order=1,
                    outline_payload=candidate,
                )
                raise ValueError("invalid surgical patch")
            except (ExecutionBudgetError, ValueError) as exc:
                if failure == "paused_judge":
                    assert exc is paused
                else:
                    assert str(exc) == "invalid surgical patch"
                session.rollback()
        with Session(engine) as reader:
            saved = reader.get(ProjectModel, project_id)
            bucket = next(
                iter(saved.metadata_json["outline_commercial_judge_checkpoints"].values())
            )
            assert bucket["latest_candidate"]["candidate"] == candidate
            if failure == "paused_judge":
                assert "best" not in bucket
                assert bucket["latest_candidate"]["report"] is None
            else:
                assert bucket["best"]["report"]["overall_score"] == 0.62
                assert bucket["best"]["report"]["passed"] is False
                assert (
                    content_hash(bucket["best"]["candidate"])
                    == bucket["best"]["report"]["graded_outline_hash"]
                )
    finally:
        engine.dispose()


async def test_best_pair_not_overwritten_by_regression_or_unknown_judge():
    project, candidate, _, _, report, _ = _sources()
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None), flush=AsyncMock(), commit=AsyncMock()
    )
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    first_workflow = uuid4()
    kwargs = {
        "session": session,
        "project": project,
        "workflow_run_id": first_workflow,
        "volume_number": 1,
    }
    await checkpoint_outline_candidate(**kwargs, candidate=candidate, report=report)
    worse = copy.deepcopy(candidate)
    worse["chapters"][0]["goal"] = "A worse decision"
    kwargs["workflow_run_id"] = uuid4()
    await checkpoint_outline_candidate(
        **kwargs,
        candidate=worse,
        report={
            **report,
            "graded_outline_hash": content_hash(worse),
            "overall_score": 0.4,
        },
    )
    await checkpoint_outline_candidate(**kwargs, candidate=worse)
    payload, verdict = await planner._restore_best_historical_outline_repair_baseline(
        session,
        project=project,
        volume_number=1,
    )
    assert payload == candidate and verdict["overall_score"] == 0.62
    assert verdict["graded_outline_hash"] == content_hash(payload)
    bucket = next(iter(project.metadata_json["outline_commercial_judge_checkpoints"].values()))
    assert bucket["best"]["workflow_run_id"] == str(first_workflow)

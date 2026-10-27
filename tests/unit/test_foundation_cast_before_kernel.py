"""Real foundation entry and SQLite failure commit; no provider/paid execution.

Generation and semantic-review transport are fixture boundaries. Binding,
final-review orchestration, artifact import, steps and failure commit are real.
"""

from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_source_bound_planning_model_completion_entry import entry as entry

from bestseller.infra.db.models import (
    LlmRunModel,
    PlanningArtifactVersionModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import conception_approval_bridge, planner
from bestseller.services.prewrite_quality_profile import planning_artifact_meta
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.source_bound_planning_content import CONTENT_POLICY
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def snapshot(row):
    return {
        field.key: deepcopy(getattr(row, field.key)) for field in inspect(row).mapper.column_attrs
    }


@pytest.mark.parametrize("review_passes", [True, False])
async def test_foundation_commits_reviewed_cast_before_later_kernel_failure(
    entry,
    monkeypatch,
    review_passes,
):
    session, project = entry.session, entry.project
    engine = session.bind
    async with engine.begin() as connection:
        ddl = str(CreateTable(WorkflowStepRunModel.__table__).compile(dialect=engine.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    entry.workflow.status = "failed"
    entry.workflow.error_message = "original producer failed after successful generation"
    creation = deepcopy(project.metadata_json.get("creation_intent_contract") or {})
    creation["field_sources"] = {**creation.get("field_sources", {}), "hook_spec": "explicit"}
    creation["hook_spec"] = {
        **creation.get("hook_spec", {}),
        "planning_capabilities": {
            "schema": "planning-capabilities.v1",
            "selected": {"story_design_kernel": True},
        },
    }
    project.metadata_json = {
        **project.metadata_json,
        "premise": "观测站故事",
        "creation_intent_contract": creation,
    }
    old_runs = []
    for kind in ("world_spec", "cast_spec"):
        row = LlmRunModel(
            project_id=project.id,
            workflow_run_id=entry.workflow.id,
            logical_role="planner",
            provider="fixture",
            model_name="retained-fixture",
            prompt_template=f"planner_{kind}",
            prompt_version="retained-fixture.v1",
            finish_reason="tool_calls",
            metadata_json={"original_return": {"kind": kind}},
        )
        session.add(row)
        old_runs.append(row)
    await session.flush()
    world = {"world_name": "Observatory", "world_premise": "Observers compare measurements"}
    raw = {
        "protagonist": {"name": "于岚", "gender": "female", "goal": "Compare measurements"},
        "supporting_cast": [{"name": "Ada", "gender": "female"}],
        "antagonist_forces": [{"name": "Cloud cover", "force_type": "environment"}],
        "literal_extra": {"notes": "Uncropped retained proposal " * 2000},
    }
    for payload, run in zip((world, raw), old_runs, strict=True):
        payload["_meta"] = {
            # Real generated assets already carry the metadata the importer
            # preserves; downstream approval compares these source bytes too.
            **planning_artifact_meta(project),
            "source_content_policy": CONTENT_POLICY,
            "generation_llm_run_id": str(run.id),
            "planning_submission_receipt": {"workflow_run_id": str(entry.workflow.id)},
        }
    prior_world = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="world_spec",
        version_no=1,
        status="approved",
        schema_version="1.0",
        source_run_id=entry.workflow.id,
        content=deepcopy(world),
    )
    prior_cast = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="cast_spec",
        version_no=1,
        status="approved",
        schema_version="1.0",
        source_run_id=entry.workflow.id,
        content={"protagonist": {"name": "于岚"}, "historical_literal": "Keep original"},
    )
    session.add_all([prior_world, prior_cast])
    await session.commit()
    protected = [entry.workflow, entry.canonical, prior_world, prior_cast, *old_runs]
    for row in protected:
        await session.refresh(row)
    original_rows = {row.id: snapshot(row) for row in protected}
    original_payloads = deepcopy((world, raw))
    events, review_inputs, current_workflows = [], [], []
    settings = load_settings()
    settings.hook_engine.enabled = False
    settings.pipeline.enable_story_design_kernel = True

    # Entry admission/reference enrichment are outside this stage-order test.
    monkeypatch.setattr(planner, "_assert_plan_writer_not_locked", AsyncMock(return_value=project))
    monkeypatch.setattr(conception_approval_bridge, "prepare_adopted_design_snapshot", AsyncMock())
    for name in ("_stash_distilled_strategy_card", "_stash_distilled_design_reference_blocks"):
        monkeypatch.setattr(planner, name, lambda *_args, **_kwargs: None)

    async def retained(_session, _settings, **kwargs):
        kind = kwargs["kind"]
        current_workflows.append(kwargs["workflow_run_id"])
        events.append(kind)
        if kind == "book_spec":
            return deepcopy(entry.completed), None
        if kind == "world_spec":
            return deepcopy(world), old_runs[0].id
        assert kind == "cast_spec"
        return deepcopy(raw), old_runs[1].id

    async def approve_world(_session, _settings, **kwargs):
        return deepcopy(kwargs["world_derived"])

    async def personhood(**kwargs):
        events.append("last_personhood_check")
        return deepcopy(kwargs["cast_spec_payload"]), None

    async def review(_session, _settings, **kwargs):
        events.append("final_cast_review")
        assert kwargs["project"] is project
        review_inputs.append(
            {key: deepcopy(value) for key, value in kwargs.items() if key != "project"}
        )
        source_hash = content_hash(entry.canonical.content)
        candidate_hash = content_hash(kwargs["cast_derived"])
        return {
            "decision": "pass" if review_passes else "revise",
            "draft_eligible": review_passes,
            "reason": "fixture semantic review",
            "core_preserved": review_passes,
            "core_conflicts": [] if review_passes else ["fixture source contradiction"],
            "evidence_verified": True,
            "llm_run_id": str(uuid4()),
            "raw_response_hash": content_hash({"fixture_review_passes": review_passes}),
            "authority_snapshot_hash": source_hash,
            "authority_snapshot_hash_echo": source_hash,
            "candidate_hash": candidate_hash,
            "candidate_hash_echo": candidate_hash,
            "changed_fields": ["cast_spec.literal_extra"],
            "source_evidence": [{"field": "cast_spec.literal_extra", "quote": "观测站"}],
            "candidate_evidence": [{"field": "cast_spec.literal_extra", "quote": "proposal"}],
        }

    async def kernel(_session, _settings, **kwargs):
        events.append("forbidden_kernel")
        cast = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == "cast_spec",
            )
            .order_by(PlanningArtifactVersionModel.version_no.desc())
        )
        assert cast.id != prior_cast.id
        step = await session.scalar(
            select(WorkflowStepRunModel).where(
                WorkflowStepRunModel.workflow_run_id == kwargs["workflow_run_id"],
                WorkflowStepRunModel.step_name == "approve_final_cast_spec",
            )
        )
        assert step.status == "completed" and step.output_ref == {"artifact_id": str(cast.id)}
        assert cast.content["_meta"]["generation_llm_run_id"] == str(old_runs[1].id)
        assert kwargs["cast_spec_payload"]["literal_extra"] == raw["literal_extra"]
        raise planner.PlannerFallbackError("fixture later kernel failure")

    async def deny_provider(*_args, **_kwargs):
        pytest.fail("stage-order test must not dispatch a provider")

    monkeypatch.setattr(planner, "_complete_source_bound_planning_content", retained)
    monkeypatch.setattr(planner, "_approve_source_bound_world_expansion", approve_world)
    monkeypatch.setattr(planner, "_repair_cast_personhood_if_needed", personhood)
    monkeypatch.setattr(planner, "_review_source_bound_expansion", review)
    monkeypatch.setattr(planner, "_generate_story_design_kernel", kernel)
    monkeypatch.setattr(planner, "complete_text", deny_provider)
    expected_error = "later kernel failure" if review_passes else "semantic review blocked"
    with pytest.raises(planner.PlannerFallbackError, match=expected_error):
        await planner.generate_foundation_plan(
            session, settings, project.slug, premise="观测站故事"
        )

    assert events[:4] == ["book_spec", "world_spec", "cast_spec", "last_personhood_check"]
    assert events[4:] == (
        ["final_cast_review", "forbidden_kernel"] if review_passes else ["final_cast_review"]
    )
    assert len(review_inputs) == 1
    inputs = review_inputs[0]
    assert inputs["world_derived"] == world
    assert inputs["cast_derived"]["literal_extra"] == raw["literal_extra"]
    assert inputs["world_baseline"] == planner._compile_source_bound_world_spec(
        project,
        "观测站故事",
        inputs["book_spec"],
    )
    assert inputs["cast_baseline"] == planner._compile_source_bound_cast_spec(
        project,
        "观测站故事",
        inputs["book_spec"],
        world,
    )
    assert (world, raw) == original_payloads
    workflow_id = current_workflows[0]
    # No test-side commit: this independent session's first read proves the
    # real foundation exception handler committed the reviewed asset + step.
    async with async_sessionmaker(engine, expire_on_commit=False)() as readback:
        workflow = await readback.get(WorkflowRunModel, workflow_id)
        assert workflow.status == "failed"
        assert workflow.current_step == (
            "generate_story_design_kernel" if review_passes else "approve_final_cast_spec"
        )
        casts = list(
            await readback.scalars(
                select(PlanningArtifactVersionModel)
                .where(
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type == "cast_spec",
                )
                .order_by(PlanningArtifactVersionModel.version_no)
            )
        )
        assert len(casts) == (2 if review_passes else 1)
        if review_passes:
            cast = casts[-1]
            assert cast.status == "approved" and cast.source_run_id == workflow_id
            assert (
                cast.content["_meta"]["planning_submission_receipt"]
                == raw["_meta"]["planning_submission_receipt"]
            )
            assert cast.content["antagonist_forces"] == inputs["cast_derived"]["antagonist_forces"]
            for key, value in raw["antagonist_forces"][0].items():
                assert cast.content["antagonist_forces"][0][key] == value
            assert cast.content["literal_extra"] == raw["literal_extra"]
        assert len(list(await readback.scalars(select(LlmRunModel)))) == 2
        for row in protected:
            assert snapshot(await readback.get(type(row), row.id)) == original_rows[row.id]

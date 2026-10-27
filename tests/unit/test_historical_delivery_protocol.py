"""Historical rendering never substitutes for current strong source admission."""

# ruff: noqa: F811 -- Imported pytest fixtures are injected as parameters.
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_chapter_outline_readiness import _materialized  # noqa: F401
from test_chapter_outline_readiness import rig as rig
from test_prefix_production_entry_fault_replay import _entry, _install
from test_prefix_production_entry_fault_replay import entry_case as entry_case

from bestseller.infra.db import models as m
from bestseller.services import chapter_outline_readiness as ready
from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import llm, outline_llm_judge
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.lossless_source_encoding import encode_lossless_source
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Historical protocol tests forbid network/provider access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
async def admitted_prefix(entry_case, monkeypatch):
    """Produce real SQLite workflow/physical grade proof, then forbid new calls."""
    case = entry_case
    assert case.engine.dialect.name == "sqlite"
    _install(monkeypatch, case.sends, passed=True)
    result = await _entry(case)
    assert result["passed"] and result["chapter_numbers"] == [1]
    async with case.engine.begin() as connection:
        ddl = str(CreateTable(m.SceneCardModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, case.project_id)
        artifact = await session.get(m.PlanningArtifactVersionModel, UUID(result["artifact_id"]))
        planned_chapter, planned_scenes = _materialized(artifact.content["chapters"][0])

        def columns(model, obj):
            return {key: value for key, value in vars(obj).items()
                    if key in inspect(model).column_attrs.keys()}

        chapter = m.ChapterModel(
            id=uuid4(), project_id=project.id, **columns(m.ChapterModel, planned_chapter),
        )
        session.add(chapter)
        await session.flush()
        scenes = [m.SceneCardModel(
            id=uuid4(), project_id=project.id, chapter_id=chapter.id,
            **columns(m.SceneCardModel, scene),
        ) for scene in planned_scenes]
        session.add_all(scenes)
        await session.flush()
        chapter.metadata_json = {**chapter.metadata_json, "outline_prefix_readiness": {
            "artifact_id": str(artifact.id), "workflow_run_id": result["workflow_run_id"],
            "projection_hash": ready.outline_prefix_projection_hash(chapter, scenes),
        }}
        await session.commit()
        case.chapter_id, case.artifact_id = chapter.id, artifact.id

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Historical selection/readback must not dispatch a model")

    for module, name in (
        (llm, "_call_litellm"), (llm, "complete_text"),
        (ready, "complete_text"), (outline_llm_judge, "complete_text"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    case.paid_calls = len(case.sends)
    return case


@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_retained_schema_selects_exact_current_projection_and_resets(
    admitted_prefix, monkeypatch, version,
):
    case = admitted_prefix
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, case.project_id)
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        artifact = await session.get(m.PlanningArtifactVersionModel, case.artifact_id)
        scenes = list(await session.scalars(select(m.SceneCardModel).where(
            m.SceneCardModel.project_id == project.id,
            m.SceneCardModel.chapter_id == chapter.id,
        ).order_by(m.SceneCardModel.scene_number)))
        # Keep the actual source/grade/projection guard and effective settings.
        receipt = await ready.verify_outline_prefix_readiness(
            session, project=project, artifact_id=artifact.id, chapter=chapter,
            scenes=scenes, settings=case.settings,
        )
        approved = deepcopy(artifact.content["chapters"][0])
        args = {"project_id": project.id, "chapter_id": chapter.id,
                "chapter_number": chapter.chapter_number, "artifact_id": artifact.id,
                "approved": approved, "receipt": receipt}
        if version == "v1":
            # Independently retained original constructor, not an inverse of v2.
            namespace = {}
            source = (Path(__file__).resolve().parents[1]
                      / "fixtures/source_memo_task_before_v2/chapter_writer_delivery.py.txt").read_text()
            exec(compile(source, "retained_delivery_v1_fixture", "exec"), namespace)
            historical = namespace["delivery_block"](**args)
        else:
            historical = delivery.delivery_block(**args)
        plan = json.loads(historical.text.split("\n", 2)[2])
        run_id = UUID(receipt["llm_run_id"])
        run = await session.get(m.LlmRunModel, run_id)
        assert run.project_id == project.id and run.workflow_run_id == artifact.source_run_id
        request = SimpleNamespace(
            prompt_template="draft_continuity_hard_safety",
            prompt_version="4.0-completion-supplement",
            user_prompt=json.dumps({"encoding": "lossless-source-encoding",
                                    "value": encode_lossless_source(
                                        {"approved_current_chapter_task": plan}, version=3)},
                                   ensure_ascii=False),
        )

        def retained_request(settings, row):
            # Only the historical selector's request is synthetic. The
            # prefix's separate physical grade remains verified.
            assert settings is case.settings and row.id == run_id
            return request

        monkeypatch.setattr(retained, "_retained_request", retained_request)
        proof = {
            "schema": "draft-continuity-source-corrected-reference.v1",
            "chapter_id": str(chapter.id), "approved_plan_sha256": content_hash(plan),
            "receipt": {"supplement_evidence": {
                "run_id": str(run_id), "workflow_id": str(run.workflow_run_id),
            }},
        }

        async def blocks():
            return await delivery.load_delivery_blocks(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )

        async with delivery.reference_source_delivery_scope(
            session, case.settings, project=project, proof=proof,
        ):
            assert (await blocks())[0].text == historical.text
            child = await asyncio.create_task(blocks())
            assert json.loads(child[0].text.split("\n", 2)[2])["schema"] == (
                "approved-current-chapter-delivery.v2"
            )
            # A foreign project cannot borrow this chapter or its grade.
            with pytest.raises(ValueError, match="editable_plan_chapter_scope_mismatch"):
                await delivery.load_delivery_blocks(
                    session, case.settings, project=SimpleNamespace(id=uuid4()),
                    chapter=chapter, scenes=scenes,
                )
            original = deepcopy(artifact.content)
            changed = deepcopy(original)
            changed["chapters"][0]["goal"] = "New source content must not acquire the old grade."
            artifact.content = changed
            await session.commit()
            # Unapproved mutation must fail, never inherit the old grade.
            with pytest.raises(ValueError, match="prefix_artifact_receipt_mismatch"):
                await blocks()
        assert delivery._REFERENCE_DELIVERY_SCHEMA.get() is None
        artifact.content = original
        await session.commit()
        current = await blocks()
        current_plan = json.loads(current[0].text.split("\n", 2)[2])
        assert current_plan["schema"] == "approved-current-chapter-delivery.v2"
        assert current_plan["chapter_contract"]["goal"] == approved["goal"]
    assert len(case.sends) == case.paid_calls


@pytest.mark.parametrize("fault", [
    "scope", "protocol", "planhash", "unknown_schema", "plan_project", "plan_chapter",
])
async def test_bad_retained_selector_cannot_open_historical_projection(monkeypatch, fault):
    project = SimpleNamespace(id=uuid4())
    workflow_id, run_id, chapter_id = uuid4(), uuid4(), uuid4()
    plan = {"schema": "approved-current-chapter-delivery.v1", "chapter_number": 8,
            "project_id": str(project.id), "chapter_id": str(chapter_id)}
    if fault == "unknown_schema":
        plan["schema"] = "unrecognized"
    elif fault == "plan_project":
        plan["project_id"] = str(uuid4())
    elif fault == "plan_chapter":
        plan["chapter_id"] = str(uuid4())
    run = SimpleNamespace(id=run_id, project_id=uuid4() if fault == "scope" else project.id,
                          workflow_run_id=workflow_id)

    class Session:
        async def get(self, model, identity, **kwargs):
            assert model is m.LlmRunModel and identity == run_id
            assert kwargs == {"populate_existing": True}
            return run

    request = SimpleNamespace(
        prompt_template="draft_continuity_hard_safety",
        prompt_version="unrecognized" if fault == "protocol" else "4.0-completion-supplement",
        user_prompt=json.dumps({"encoding": "lossless-source-encoding", "value":
                                encode_lossless_source({"approved_current_chapter_task": plan}, version=3)}),
    )
    monkeypatch.setattr(retained, "_retained_request", lambda settings, row: request)
    proof = {
        "schema": "draft-continuity-source-corrected-reference.v1", "chapter_id": str(chapter_id),
        "approved_plan_sha256": "f" * 64 if fault == "planhash" else content_hash(plan),
        "receipt": {"supplement_evidence": {
            "run_id": str(run_id), "workflow_id": str(workflow_id),
        }},
    }
    original = deepcopy(proof)
    error = "scope" if fault == "scope" else "protocol" if fault == "protocol" else "plan_binding"
    with pytest.raises(ValueError, match="chapter_writer_delivery_retained_.*" + error):
        async with delivery.reference_source_delivery_scope(Session(), None, project=project, proof=proof):
            pytest.fail("must not enter")
    assert proof == original and delivery._REFERENCE_DELIVERY_SCHEMA.get() is None

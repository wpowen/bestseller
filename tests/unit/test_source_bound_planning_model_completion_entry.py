"""Real SQLite + planner wrapper; completion transport is inert, never a provider.

These establish entry/source/persistence contracts, not real paid generation or
literary acceptance. The canonical reader, generator, validator and reuse query
all run normally. This protocol fixture has no financial attempt ledger;
retained physical generation proofs run in their dedicated SQLite suite.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import inspect
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable
from test_source_bound_planning_content import _complete_book, _project

from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import (
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import planner
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.projects import import_planning_artifact
from bestseller.services.source_bound_planning_content import CONTENT_POLICY, PLANNING_PROMPT_VERSION
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@compiles(JSONB, "sqlite")
def _sqlite_json(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
async def entry(tmp_path, monkeypatch):
    from bestseller.services import retained_planning_generation

    async def no_financial_checkpoint(*_args, **_kwargs):
        return None

    monkeypatch.setattr(retained_planning_generation, "find_retained_planning_generation",
                        no_financial_checkpoint)
    configured = make_url(load_settings().database.url)
    assert configured.get_backend_name() == "sqlite" and "test" in configured.database
    path = tmp_path / "source_bound_completion_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for model in (ProjectModel, WorkflowRunModel, PlanningArtifactVersionModel, LlmRunModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        template = _project({"synopsis": "CANONICAL_FULL_BODY_ONLY_ONCE_遥远观测站"})
        metadata = deepcopy(template.metadata_json)
        metadata["book_design_snapshot_status"] = "locked"
        metadata["book_design_snapshot"] = build_book_design_snapshot(
            creation_intent={"genre_intent": {"genre": "contemporary", "channel": "general"},
                             "chapter_count": 30},
            protagonist=metadata["book_design_snapshot"]["protagonist"],
            tone="共同求知", target_words=78000, chapter_count=30,
            approved_story_design=metadata["book_design_snapshot"]["approved_story_design"],
        ).model_dump(mode="json")
        project = ProjectModel(
            slug=uuid4().hex, title=template.title, genre=template.genre,
            audience=template.audience, language=template.language,
            target_word_count=template.target_word_count, target_chapters=30,
            metadata_json=metadata,
        )
        session.add(project)
        await session.flush()
        workflow = WorkflowRunModel(
            project_id=project.id, workflow_type="generate_foundation_plan", status="running",
            scope_type="project", scope_id=project.id, metadata_json={},
        )
        session.add(workflow)
        await session.flush()
        canonical = PlanningArtifactVersionModel(
            project_id=project.id, artifact_type="conception_snapshot", version_no=1,
            schema_version="1.0", status="canonical", source_run_id=workflow.id,
            content=deepcopy(template._source_bound_planning_canonical["content"]),
        )
        session.add(canonical)
        await session.flush()
        project.metadata_json = {
            **project.metadata_json, "conception_approved": True,
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": snapshot_hash(canonical.content),
        }
        await session.commit()
        await planner._bind_source_bound_planning_canonical(session, project)
        projection = planner._compile_source_bound_book_spec(project, "观测站故事")
        completed = _complete_book(project)
        calls, failures = [], []

        def forbidden_methodology(*_args, **_kwargs):
            pytest.fail("source-bound model entry attached default methodology")

        monkeypatch.setattr(planner, "attach_planner_methodology", forbidden_methodology)
        monkeypatch.setattr(planner, "_persist_failing_planner_output",
                            lambda **kwargs: failures.append(deepcopy(kwargs)))
        yield SimpleNamespace(
            session=session, project=project, canonical=canonical, workflow=workflow,
            projection=projection, completed=completed, calls=calls, failures=failures,
        )
    await engine.dispose()


def completion_transport(monkeypatch, entry, *, payload=None, provider="minimax", after=None):
    async def complete(session, settings, request):
        assert session is entry.session and session.bind.dialect.name == "sqlite"
        entry.calls.append(request)
        row = LlmRunModel(
            project_id=request.project_id, workflow_run_id=request.workflow_run_id,
            logical_role=request.logical_role, provider=provider, model_name="isolated-fixture",
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            finish_reason="stop", metadata_json={},
        )
        session.add(row)
        await session.flush()
        if after:
            await after(session)
        return LLMCompletionResult(
            content=json.dumps(entry.completed if payload is None else payload, ensure_ascii=False),
            provider=provider, model_name=row.model_name, finish_reason="stop", llm_run_id=row.id,
        )

    monkeypatch.setattr(planner, "complete_text", complete)


async def invoke(entry):
    return await planner._complete_source_bound_planning_content(
        entry.session, load_settings(), project=entry.project, premise="观测站故事",
        kind="book_spec", projection=entry.projection, workflow_run_id=entry.workflow.id,
    )


async def add_old_artifact(entry, *, content):
    artifact = PlanningArtifactVersionModel(
        project_id=entry.project.id, artifact_type="book_spec", status="approved",
        version_no=1, schema_version="1.0", source_run_id=entry.workflow.id,
        content=deepcopy(content),
    )
    entry.session.add(artifact)
    await entry.session.commit()
    return artifact


@pytest.mark.asyncio
async def test_actual_wrapper_dispatches_new_protocol_not_legacy_compiled_asset(entry, monkeypatch):
    legacy = deepcopy(entry.completed)
    legacy["logline"] = "LEGACY_COMPILED_OUTPUT_MUST_NOT_WIN"
    legacy["_meta"] = {"source_bound_minimal": True, "source_compiler": "old-compiler"}
    artifact = await add_old_artifact(entry, content=legacy)
    before = deepcopy(artifact.content)
    completion_transport(monkeypatch, entry)
    result, run_id = await invoke(entry)
    assert len(entry.calls) == 1 and run_id is not None
    request = entry.calls[0]
    assert request.project_id == entry.project.id
    assert request.workflow_run_id == entry.workflow.id
    assert request.prompt_template == "planner_book_spec"
    assert request.prompt_version == PLANNING_PROMPT_VERSION
    assert request.metadata["fail_closed"] is True
    source, _ = json.JSONDecoder().raw_decode(request.user_prompt)
    assert source["protocol"] == CONTENT_POLICY
    assert source["approved_sources"]["canonical_snapshot"]["content"] == entry.canonical.content
    assert request.user_prompt.count("CANONICAL_FULL_BODY_ONLY_ONCE_遥远观测站") == 1
    assert source["approved_sources"]["explicit_controls"]["story_enhancers"]["effect_skills"] == []
    assert "FAKE-A" not in request.user_prompt and "comedy_engine" not in request.user_prompt
    assert result["logline"] == entry.completed["logline"]
    assert result["_meta"]["generation_llm_run_id"] == str(run_id)
    assert result["_meta"]["source_content_policy"] == CONTENT_POLICY
    assert result["_meta"]["creator_authorship_asserted"] is False
    assert not result["_meta"]["reused"] and not result["_meta"]["source_bound_minimal"]
    assert artifact.content == before and not entry.failures
    # The production importer stores workflow provenance; the paid completion
    # remains separately identified in content, never substituted for a WF FK.
    persisted = await import_planning_artifact(
        entry.session, entry.project.slug,
        PlanningArtifactCreate(artifact_type=ArtifactType.BOOK_SPEC, content=result,
                               source_run_id=entry.workflow.id),
    )
    await entry.session.commit()
    entry.session.expire(persisted)
    await entry.session.refresh(persisted)
    assert persisted.source_run_id == entry.workflow.id
    assert persisted.content["_meta"]["generation_llm_run_id"] == str(run_id)
    assert (await entry.session.get(LlmRunModel, run_id)).workflow_run_id == persisted.source_run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fallback", "projection"])
async def test_actual_wrapper_refuses_provider_or_compiled_fallback(entry, monkeypatch, failure):
    completion_transport(monkeypatch, entry, payload=entry.projection,
                         provider="fallback" if failure == "fallback" else "minimax")
    with pytest.raises(planner.PlannerFallbackError):
        await invoke(entry)
    assert len(entry.calls) == (1 if failure == "fallback" else 4)
    assert list(await entry.session.scalars(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.artifact_type == "book_spec"
    ))) == []
    assert entry.canonical.status == "canonical"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["selection", "content", "status", "metadata_shape"])
async def test_actual_wrapper_canonical_race_stops_before_artifact_persistence(
    entry, monkeypatch, change,
):
    before = deepcopy(entry.canonical.content)

    async def race(session):
        if change in {"selection", "metadata_shape"}:
            metadata = (
                {**entry.project.metadata_json, "creation_canonical_snapshot_id": str(uuid4())}
                if change == "selection" else ["malformed selection"]
            )
            await session.execute(update(ProjectModel).where(ProjectModel.id == entry.project.id)
                                  .values(metadata_json=metadata)
                                  .execution_options(synchronize_session=False))
        elif change == "content":
            await session.execute(update(PlanningArtifactVersionModel)
                                  .where(PlanningArtifactVersionModel.id == entry.canonical.id)
                                  .values(content={**before, "synopsis": "changed after dispatch"})
                                  .execution_options(synchronize_session=False))
        else:
            await session.execute(update(PlanningArtifactVersionModel)
                                  .where(PlanningArtifactVersionModel.id == entry.canonical.id)
                                  .values(status="archived")
                                  .execution_options(synchronize_session=False))

    completion_transport(monkeypatch, entry, after=race)
    with pytest.raises(ValueError, match="canonical_changed_during_generation"):
        await invoke(entry)
    assert len(entry.calls) == 1
    assert len(list(await entry.session.scalars(select(LlmRunModel)))) == 1
    assert list(await entry.session.scalars(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.artifact_type == "book_spec"
    ))) == []


@pytest.mark.asyncio
async def test_actual_exact_reuse_preserves_generation_receipt_and_never_dispatches(
    entry, monkeypatch,
):
    completion_transport(monkeypatch, entry)
    generated, original_run = await invoke(entry)
    old = await add_old_artifact(entry, content=generated)
    before = deepcopy(old.content)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("exact current-policy reuse dispatched a new completion")

    monkeypatch.setattr(planner, "complete_text", forbidden)
    reused, new_run = await invoke(entry)
    assert new_run is None
    assert reused["_meta"]["generation_llm_run_id"] == str(original_run)
    assert reused["_meta"]["source_content_policy"] == CONTENT_POLICY
    assert reused["_meta"]["reused_artifact_id"] == str(old.id)
    assert old.content == before and len(entry.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_field", ["generation_llm_run_id", "source_content_policy"])
async def test_exact_hash_reuse_without_generation_proof_is_not_relabelled(
    entry, monkeypatch, bad_field,
):
    completion_transport(monkeypatch, entry)
    generated, _ = await invoke(entry)
    del generated["_meta"][bad_field]
    old = await add_old_artifact(entry, content=generated)
    before = deepcopy(old.content)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("missing reusable generation proof must stop without fresh paid work")

    monkeypatch.setattr(planner, "complete_text", forbidden)
    with pytest.raises(ValueError, match="reuse_generation_receipt_missing"):
        await invoke(entry)
    assert old.content == before


@pytest.mark.parametrize("name", ["generate_novel_plan", "generate_foundation_plan"])
def test_both_real_entries_bind_then_complete_all_four_assets_and_attribute_workflow(name):
    tree = ast.parse(inspect.getsource(getattr(planner, name)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)]
    bindings = [n for n in calls if n.func.id == "_bind_source_bound_planning_canonical"]
    completions = [n for n in calls if n.func.id == "_complete_source_bound_planning_content"]
    assert len(bindings) == 1 and len(completions) == 4
    assert bindings[0].lineno < min(n.lineno for n in completions)
    assert {next(k.value.value for k in n.keywords if k.arg == "kind")
            for n in completions} == {"book_spec", "world_spec", "cast_spec", "volume_plan"}
    for branch in ast.walk(tree):
        if (not isinstance(branch, ast.If) or not isinstance(branch.test, ast.Call)
                or not isinstance(branch.test.func, ast.Name)
                or branch.test.func.id != "_source_bound_cast_enabled"):
            continue
        for call in completions:
            if call in list(ast.walk(ast.Module(body=branch.body, type_ignores=[]))):
                assert all(not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                                and n.func.id == "attach_planner_methodology")
                           for n in ast.walk(ast.Module(body=branch.body, type_ignores=[])))
    approved = [n for n in calls if n.func.id == "PlanningArtifactCreate"
                and any(k.arg == "artifact_type" and isinstance(k.value, ast.Attribute)
                        and k.value.attr in {"BOOK_SPEC", "WORLD_SPEC", "VOLUME_PLAN"}
                        for k in n.keywords)]
    assert {next(k.value.attr for k in n.keywords if k.arg == "artifact_type")
            for n in approved} == {"BOOK_SPEC", "WORLD_SPEC", "VOLUME_PLAN"}
    for call in approved:
        source = next(k.value for k in call.keywords if k.arg == "source_run_id")
        assert ast.dump(source) == ast.dump(ast.parse("workflow_run.id", mode="eval").body)

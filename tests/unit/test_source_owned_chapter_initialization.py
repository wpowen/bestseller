"""Execute owned native initialization blocks on SQLite; no PG/provider.

Full-source fixtures retain the existing inert grade-verifier boundary; source
selection, projection checks, materializers and savepoint rollback are real.
"""

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_chapter_outline_readiness import rig as rig
from test_chapter_outline_readiness import sqlite_identity_store as sqlite_identity_store
from test_chapter_scene_contract_materializer import _chapter, _contract, _scene
from test_materialized_outline_admission_read import artifact
from test_outline_source_entry_guards import entry as admission_case  # noqa: F401
from test_outline_source_entry_guards import source_rig as source_rig
from test_outline_source_freshness import bind_existing_grade_boundary
from test_prefix_production_entry_fault_replay import entry_case as entry_case
from test_prefix_window_activation_sqlite import setup as setup_prefix

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db import models as db
from bestseller.services import chapter_outline_readiness as prefix_readiness
from bestseller.services import chapter_scene_contract_materializer as materializer
from bestseller.services import outline_source_entry, pipelines, workflows
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.commercial_protocol_transition_runtime import _row

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def segment(environment):
    """Compile the exact owned blocks, without unrelated pipeline work."""
    path = Path(pipelines.__file__)
    tree = ast.parse(path.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_chapter_pipeline"
    )
    body = next(n for n in function.body if isinstance(n, ast.Try)).body
    start = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "source_owned_plan" for t in n.targets)
    )
    end = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.If)
        and any(
            isinstance(a, ast.Assign)
            and isinstance(a.value, ast.Constant)
            and a.value.value == "chapter_scene_contract_materializer"
            for a in n.body
        )
    )
    wrapper = ast.AsyncFunctionDef(
        name="owned_segment",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[
            ast.Assign(
                targets=[ast.Name(id="step_order", ctx=ast.Store())], value=ast.Constant(value=0)
            ),
            *deepcopy(body[start : end + 1]),
        ],
        decorator_list=[],
    )
    namespace = {**vars(pipelines), **environment}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            str(path),
            "exec",
        ),
        namespace,
    )
    return namespace["owned_segment"]


@pytest.fixture
async def full(admission_case):  # noqa: F811
    r = admission_case
    source = deepcopy(r.artifact.content)
    raw = source["chapters"][0]["scenes"][0]
    raw["target_word_count"] = 100  # Approved imbalance is not a runtime repair grant.
    raw["methodology_contract"] = {"stakes": "The author retains the original cargo risk."}
    r.artifact.content = source
    typed = ChapterOutlineBatchInput.model_validate(source).chapters[0].scenes[0]
    r.scene.target_word_count = typed.target_word_count
    workflows._sync_scene_methodology_metadata(r.scene, typed)
    r.scene.metadata_json = {
        **r.scene.metadata_json,
        "manual_owner": {"keep": True},
        "auto_repair_length_scale": 1.5,
        "chapter_scene_contract_materialized_bindings": {
            "stakes": {"value": raw["methodology_contract"]["stakes"], "source": "legacy"},
        },
        "scene_contract_materialized_history": [{"prior": "retain"}],
    }
    r.chapter.metadata_json = {
        **r.chapter.metadata_json,
        "auto_repair_in_progress": True,
        "chapter_scene_contract_materialization": {
            "scene_changes": [{"scene_number": 1, "filled_fields": ["stakes"]}],
        },
    }
    await r.session.commit()
    await bind_existing_grade_boundary(r)
    async with r.session.bind.begin() as connection:
        ddl = str(
            CreateTable(db.ChapterContractModel.__table__).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    r.contract = db.ChapterContractModel(
        project_id=r.project.id,
        chapter_id=r.chapter.id,
        chapter_number=r.chapter.chapter_number,
        contract_summary="Manual authored summary",
        metadata_json={"manual_owner": True},
    )
    r.session.add(r.contract)
    await r.session.commit()
    r.settings.pipeline.commercial_planning_min_target_chapters = 1
    r.settings.pipeline.enable_chapter_scene_contract_materializer = True
    for row in (r.chapter, r.scene, r.contract, r.project, r.artifact):
        await r.session.refresh(row)
    return r


async def snapshot(r):
    for row in (r.chapter, r.scene, r.contract):
        await r.session.refresh(row)
    return _row(r.chapter), _row(r.scene), _row(r.contract)


@pytest.fixture
async def prefix(entry_case, monkeypatch):
    c = entry_case
    await setup_prefix(c, monkeypatch)
    async with c.engine.begin() as connection:
        ddl = str(
            CreateTable(db.ChapterContractModel.__table__).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with c.sessions() as session:
        project = await session.get(db.ProjectModel, c.project_id)
        chapter = await session.scalar(
            select(db.ChapterModel).where(db.ChapterModel.project_id == project.id)
        )
        scenes = list(
            await session.scalars(
                select(db.SceneCardModel)
                .where(db.SceneCardModel.chapter_id == chapter.id)
                .order_by(db.SceneCardModel.scene_number)
            )
        )
        contract = db.ChapterContractModel(
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            contract_summary="Preserve authored prefix contract",
            metadata_json={"manual": True},
        )
        session.add(contract)
        await session.commit()
        await session.refresh(contract)
        yield SimpleNamespace(
            session=session,
            project=project,
            chapter=chapter,
            scenes=scenes,
            scene=scenes[0],
            contract=contract,
            settings=c.settings,
            sends=c.sends,
        )


async def run(r, monkeypatch, *, regenerate=True, corrupt=None):
    events = []
    workflow = db.WorkflowRunModel(
        id=uuid4(),
        project_id=r.project.id,
        workflow_type="chapter_pipeline",
        scope_type="chapter",
        scope_id=r.chapter.id,
        status="running",
        metadata_json={},
    )
    r.session.add(workflow)
    await r.session.commit()
    original = outline_source_entry.require_current_outline_source_admission
    original_prefix = prefix_readiness.verify_outline_prefix_readiness

    async def verify(session, settings, **kwargs):
        assert settings is r.settings
        events.append("verify")
        return await original(session, settings, **kwargs)

    async def verify_prefix(session, **kwargs):
        assert kwargs["settings"] is r.settings
        events.append("verify_prefix")
        return await original_prefix(session, **kwargs)

    async def step(*args, **kwargs):
        events.append("step")

    async def checkpoint(session):
        events.append("checkpoint")
        await session.commit()

    def scenes(**kwargs):
        result = materializer.materialize_chapter_scene_contracts(**kwargs)
        if corrupt == "target":
            kwargs["scenes"][0].target_word_count += 1
        elif corrupt == "marker":
            kwargs["chapter"].metadata_json = {
                k: v
                for k, v in kwargs["chapter"].metadata_json.items()
                if k != "outline_source_admission"
            }
        return result

    monkeypatch.setattr(outline_source_entry, "require_current_outline_source_admission", verify)
    monkeypatch.setattr(prefix_readiness, "verify_outline_prefix_readiness", verify_prefix)
    environment = {
        "session": r.session,
        "project": r.project,
        "chapter": r.chapter,
        "scenes": getattr(r, "scenes", [r.scene]),
        "settings": r.settings,
        "workflow_run": workflow,
        "use_chapter_first": True,
        "should_supersede_pending_rewrites": regenerate,
        "prefix_marker": (r.chapter.metadata_json or {}).get("outline_prefix_readiness"),
        "prefix_source": None,
        "step_order": 0,
        "_supersede_pending_chapter_rewrite_tasks_for_regeneration": AsyncMock(return_value=0),
        "create_workflow_step_run": step,
        "_checkpoint_commit": checkpoint,
        "materialize_chapter_scene_contracts": scenes,
    }
    return segment(environment), events, workflow


async def test_full_source_preserves_targets_raw_stakes_history_and_manual_contract(
    full, monkeypatch
):
    r = full
    extra = artifact(r.project, "chapter_outline_batch", r.artifact.content, version_no=99)
    r.session.add(extra)
    await r.session.commit()
    projection = outline_prefix_projection_hash(r.chapter, [r.scene])
    contract, source, metadata = (
        _row(r.contract),
        deepcopy(r.artifact.content),
        deepcopy(r.scene.metadata_json),
    )
    execute, events, workflow = await run(r, monkeypatch)
    await execute()
    assert events.count("verify") == 3
    assert events.index("verify") < events.index("checkpoint")
    assert outline_prefix_projection_hash(r.chapter, [r.scene]) == projection
    assert r.scene.target_word_count == 100
    assert r.scene.metadata_json["methodology_contract"] == metadata["methodology_contract"]
    for key in (
        "manual_owner",
        "chapter_scene_contract_materialized_bindings",
        "scene_contract_materialized_history",
    ):
        assert r.scene.metadata_json[key] == metadata[key]
    assert "auto_repair_length_scale" not in r.scene.metadata_json
    assert _row(r.contract) == contract and r.artifact.content == source
    assert (
        workflow.metadata_json["chapter_first_scene_regeneration_residue"]["target_rebalanced"]
        is False
    )
    assert not workflow.metadata_json["chapter_contract_materialization"]["changed"]
    assert not workflow.metadata_json["chapter_scene_contract_materialization"]["changed"]
    assert (
        await outline_source_entry.current_outline_artifact(
            r.session, project=r.project, chapter=r.chapter
        )
    ).id == r.artifact.id
    assert extra.status == "approved" and r.forbidden == []


@pytest.mark.parametrize("fault", ["empty", "null", "foreign", "unselected", "projection"])
async def test_bad_full_source_rejects_before_initialization(full, monkeypatch, fault):
    r = full
    metadata = deepcopy(r.chapter.metadata_json)
    marker = metadata["outline_source_admission"]
    if fault == "empty":
        metadata["outline_source_admission"] = {}
    elif fault == "null":
        metadata["outline_source_admission"] = None
    elif fault == "foreign":
        foreign = artifact(
            SimpleNamespace(id=uuid4()), "chapter_outline_batch", r.artifact.content
        )
        r.session.add(foreign)
        await r.session.flush()
        marker["artifact_id"] = str(foreign.id)
    elif fault == "unselected":
        extra = artifact(r.project, "chapter_outline_batch", r.artifact.content, version_no=99)
        r.session.add(extra)
        await r.session.flush()
        marker["artifact_id"] = str(extra.id)
    else:
        marker["projection_hash"] = "0" * 64
    r.chapter.metadata_json = metadata
    await r.session.commit()
    before = await snapshot(r)
    execute, events, _ = await run(r, monkeypatch)
    with pytest.raises(ValueError):
        await execute()
    assert events == ["verify"]
    assert await snapshot(r) == before
    assert r.forbidden == []


@pytest.mark.parametrize("corrupt", ["target", "marker"])
async def test_full_post_materializer_proof_rolls_back_unauthorized_drift(
    full, monkeypatch, corrupt
):
    r = full
    before = await snapshot(r)
    execute, events, _ = await run(r, monkeypatch, regenerate=False, corrupt=corrupt)
    with pytest.raises(ValueError):
        await execute()
    await r.session.refresh(r.chapter)
    await r.session.refresh(r.scene)
    await r.session.refresh(r.contract)
    assert await snapshot(r) == before
    assert events == ["verify", "verify"]


@pytest.mark.parametrize("key", ["outline_prefix_readiness", "outline_source_admission"])
@pytest.mark.parametrize("value", [None, {}, False, "malformed", {"artifact_id": "unverified"}])
async def test_sync_presence_protects_both_plans_without_granting_approval(key, value):
    chapter, scene, contract = _chapter(metadata_json={key: value}), _scene(), _contract()
    before = deepcopy((vars(chapter), vars(scene), vars(contract)))
    assert materializer._source_plan_locked(chapter, False) is True
    assert not materializer.materialize_chapter_contract_from_chapter(
        chapter=chapter,
        chapter_contract=contract,
        verified_adoption=False,
        source_plan_locked=False,
    ).changed
    assert not materializer.materialize_chapter_scene_contracts(
        chapter=chapter,
        scenes=[scene],
        verified_adoption=False,
        source_plan_locked=False,
        methodology_selected=False,
    ).changed
    assert (vars(chapter), vars(scene), vars(contract)) == before


async def test_real_prefix_proof_protects_same_initialization_blocks(prefix, monkeypatch):
    r = prefix
    before = outline_prefix_projection_hash(r.chapter, r.scenes)
    contract, sends = _row(r.contract), len(r.sends)
    execute, events, _ = await run(r, monkeypatch)
    await execute()
    assert events.count("verify_prefix") == 3
    assert outline_prefix_projection_hash(r.chapter, r.scenes) == before
    assert _row(r.contract) == contract and len(r.sends) == sends


@pytest.mark.parametrize("value", [None, {}, False])
async def test_malformed_prefix_presence_never_authorizes_mutation(full, monkeypatch, value):
    r = full
    r.chapter.metadata_json = {**r.chapter.metadata_json, "outline_prefix_readiness": value}
    await r.session.commit()
    before = await snapshot(r)
    execute, events, _ = await run(r, monkeypatch)
    with pytest.raises(ValueError, match="source_plan_prefix_marker_invalid"):
        await execute()
    assert events == [] and await snapshot(r) == before


async def test_native_auto_repair_call_forwards_actual_settings():
    tree = ast.parse(Path(pipelines.__file__).read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_chapter_pipeline"
    )
    calls = [
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "maybe_prepare_chapter_auto_repair"
    ]
    assert len(calls) == 1
    assert any(
        k.arg == "settings" and isinstance(k.value, ast.Name) and k.value.id == "settings"
        for k in calls[0].keywords
    )

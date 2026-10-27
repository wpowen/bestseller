"""Completed-cohort selection with real isolated SQLite; no PG/provider."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.infra.db.models import PlanningArtifactVersionModel as Artifact
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.services import foundation_selection as selection
from bestseller.services import planner
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def store(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("foundation selection must never call a provider")

    monkeypatch.setattr(planner, "complete_text", forbidden)
    monkeypatch.setattr("bestseller.services.llm.complete_text", forbidden)
    path = tmp_path / "foundation_selection_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    for model in (ProjectModel, WorkflowRunModel, Artifact):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory, engine
    finally:
        await engine.dispose()


def seed_objects(language="en"):
    pid, wid, failed_id = uuid4(), uuid4(), uuid4()
    snapshot = build_book_design_snapshot(
        creation_intent={
            "genre_intent": {"channel": "male", "genre": "xianxia"},
            "chapter_count": 30,
        },
        protagonist="Mira" if language == "en" else "于岚",
        tone="restrained",
        target_words=78000,
        chapter_count=30,
        approved_story_design={"source_contract_hash": "original-contract"},
    )
    canonical = Artifact(
        id=uuid4(),
        project_id=pid,
        artifact_type="conception_snapshot",
        version_no=1,
        status="canonical",
        schema_version="1.0",
        content={
            "concept_contract": {"input_hash": "original-contract"},
            "story_spine": {"wants": "Restore the observatory"},
        },
    )
    project = ProjectModel(
        id=pid,
        slug="observatory-" + pid.hex,
        title="Observatory",
        genre="fantasy",
        language=language,
        target_chapters=30,
        target_word_count=78000,
        metadata_json={
            "premise": "Two observers restore an observatory.",
            "unrelated_runtime": {"keep": True},
            "book_design_snapshot": snapshot.model_dump(mode="json"),
            "book_design_snapshot_id": snapshot.snapshot_id,
            "book_design_snapshot_hash": snapshot.source_hash,
            "conception_approved": True,
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": snapshot_hash(canonical.content),
        },
    )
    lineage = {
        "source_snapshot_id": snapshot.snapshot_id,
        "source_snapshot_hash": snapshot.source_hash,
        "source_snapshot_version": snapshot.schema_version,
    }
    artifacts = {
        kind: Artifact(
            id=uuid4(),
            project_id=pid,
            artifact_type=kind,
            version_no=1,
            status="approved",
            schema_version="1.0",
            source_run_id=failed_id,
            content={"label": kind, "_meta": deepcopy(lineage)},
        )
        for kind in selection.KINDS[:3]
    }
    detached = SimpleNamespace(
        id=pid,
        target_chapters=30,
        target_word_count=78000,
        metadata_json=deepcopy(project.metadata_json),
        language=language,
    )
    detached._source_bound_planning_canonical = {
        "id": str(canonical.id),
        "content_hash": snapshot_hash(canonical.content),
        "content": deepcopy(canonical.content),
    }
    projection = planner._compile_source_bound_volume_plan(
        detached,
        project.metadata_json["premise"],
        *(artifacts[kind].content for kind in selection.KINDS[:3]),
    )
    artifacts["volume_plan"] = Artifact(
        id=uuid4(),
        project_id=pid,
        artifact_type="volume_plan",
        version_no=1,
        status="approved",
        schema_version="1.0",
        source_run_id=wid,
        content=[
            {
                "volume_number": 1,
                "_meta": {"source_projection_sha256": planner._approved_payload_hash(projection)},
            }
        ],
    )
    workflow = WorkflowRunModel(
        id=wid,
        project_id=pid,
        workflow_type="generate_foundation_plan",
        status="completed",
        scope_type="project",
        scope_id=pid,
        metadata_json={"artifact_ids": {kind: str(row.id) for kind, row in artifacts.items()}},
    )
    failed = WorkflowRunModel(
        id=failed_id,
        project_id=pid,
        workflow_type="generate_foundation_plan",
        status="failed",
        scope_type="project",
        scope_id=pid,
        metadata_json={"paid_history": ["preserved"]},
    )
    return project, canonical, workflow, failed, artifacts


@pytest.fixture
async def case(store):
    factory, engine = store
    project, canonical, workflow, failed, artifacts = seed_objects()
    async with factory() as session:
        session.add_all([project, canonical, workflow, failed, *artifacts.values()])
        # Newer approved artifacts from a failed cohort are intentionally tempting.
        session.add_all(
            [
                Artifact(
                    id=uuid4(),
                    project_id=project.id,
                    artifact_type=kind,
                    version_no=9,
                    status="approved",
                    schema_version="1.0",
                    source_run_id=failed.id,
                    content={"newer_partial": kind},
                )
                for kind in ("book_spec", "world_spec")
            ]
        )
        await session.commit()
    return SimpleNamespace(
        factory=factory,
        engine=engine,
        project=project,
        canonical=canonical,
        workflow=workflow,
        failed=failed,
        artifacts=artifacts,
    )


@pytest.mark.parametrize("workflow_type", sorted(selection.WORKFLOW_TYPES - {"complete_foundation_revision"}))
async def test_exact_completed_consumer_reuses_failed_generation_not_newer_partial(
    case, workflow_type
):
    async with case.factory() as session:
        project = await session.get(ProjectModel, case.project.id)
        workflow = await session.get(WorkflowRunModel, case.workflow.id)
        workflow.workflow_type = workflow_type
        before = deepcopy(project.metadata_json)
        receipt = await selection.select_completed_foundation(
            session, project=project, workflow_id=workflow.id
        )
        assert receipt["artifact_ids"] == {
            kind: str(row.id) for kind, row in case.artifacts.items()
        }
        assert receipt["artifact_content_hashes"] == {
            kind: content_hash(row.content) for kind, row in case.artifacts.items()
        }
        assert receipt["canonical_source"]["content_hash"] == snapshot_hash(case.canonical.content)
        assert all(
            receipt[key] is False
            for key in ("author_authority", "grade_asserted", "credit_granted")
        )
        assert project.metadata_json == before
        assert not session.new and not session.deleted


async def test_pin_no_flush_commit_idempotent_history_and_independent_normal_loader(
    case, monkeypatch
):
    async with case.factory() as session:
        project = await session.get(ProjectModel, case.project.id)
        original = deepcopy(project.metadata_json)
        sql = []

        def track(conn, cursor, statement, parameters, context, executemany):
            sql.append(statement)

        event.listen(case.engine.sync_engine, "before_cursor_execute", track)
        try:
            receipt = await selection.pin_completed_foundation(
                session, project=project, workflow_id=case.workflow.id
            )
            pinned = deepcopy(project.metadata_json)
            assert (
                await selection.pin_completed_foundation(
                    session, project=project, workflow_id=case.workflow.id
                )
                == receipt
            )
            assert project.metadata_json == pinned
            assert {
                k: v for k, v in pinned.items() if k not in (selection.POINTER, selection.HISTORY)
            } == original
            assert pinned[selection.HISTORY] == [receipt]
            assert set(session.dirty) == {project}
            assert not any(
                s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "COMMIT")) for s in sql
            )
        finally:
            event.remove(case.engine.sync_engine, "before_cursor_execute", track)
        await session.commit()
    async with case.factory() as session:
        project = await session.get(ProjectModel, case.project.id)
        selected = await selection.load_selected_foundation_artifacts(session, project=project)
        assert {kind: row.id for kind, row in selected.items()} == {
            kind: row.id for kind, row in case.artifacts.items()
        }
        from bestseller.domain.enums import ArtifactType
        from bestseller.services.workflows import get_latest_approved_planning_artifact

        # The normal lookup is integrated by the separately owned consumer.
        row = await get_latest_approved_planning_artifact(
            session, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC
        )
        assert row.id == case.artifacts["book_spec"].id
        assert (
            await session.get(WorkflowRunModel, case.failed.id)
        ).metadata_json == case.failed.metadata_json


async def test_absent_pointer_legacy_and_malformed_pointer_fail_closed(case):
    async with case.factory() as session:
        project = await session.get(ProjectModel, case.project.id)
        assert await selection.load_selected_foundation_artifacts(session, project=project) is None
        for malformed in (None, {}, "latest", {"schema": selection.SCHEMA, "workflow_id": "bad"}):
            project.metadata_json = {**project.metadata_json, selection.POINTER: malformed}
            with pytest.raises(ValueError, match="foundation_selection_"):
                await selection.load_selected_foundation_artifacts(session, project=project)


@pytest.mark.parametrize(
    "fault",
    [
        "failed_consumer",
        "volume_workflow",
        "foreign_consumer",
        "scope",
        "missing_kind",
        "wrong_id",
        "foreign_artifact",
        "wrong_kind",
        "not_approved",
        "scoped_asset",
        "stale_snapshot",
        "corrupt_snapshot",
        "stale_canonical",
        "canonical_status",
        "canonical_project",
        "asset_snapshot",
        "volume_projection",
        "volume_second_row",
        "canonical_contract",
    ],
)
async def test_bad_sources_cohorts_and_volume_projection_fail_without_mutation(case, fault):
    async with case.factory() as session:
        p = await session.get(ProjectModel, case.project.id)
        w = await session.get(WorkflowRunModel, case.workflow.id)
        c = await session.get(Artifact, case.canonical.id)
        a = await session.get(Artifact, case.artifacts["book_spec"].id)
        v = await session.get(Artifact, case.artifacts["volume_plan"].id)
        if fault == "failed_consumer":
            w.status = "failed"
        elif fault == "volume_workflow":
            w.workflow_type = "generate_volume_plan"
        elif fault == "foreign_consumer":
            w.project_id = uuid4()
        elif fault == "scope":
            w.scope_id = uuid4()
        elif fault == "missing_kind":
            w.metadata_json = {"artifact_ids": {}}
        elif fault == "wrong_id":
            w.metadata_json = {
                "artifact_ids": {**w.metadata_json["artifact_ids"], "book_spec": str(uuid4())}
            }
        elif fault == "foreign_artifact":
            a.project_id = uuid4()
        elif fault == "wrong_kind":
            a.artifact_type = "world_spec"
        elif fault == "not_approved":
            a.status = "needs_review"
        elif fault == "scoped_asset":
            a.scope_ref_id = uuid4()
        elif fault == "stale_snapshot":
            p.metadata_json = {**p.metadata_json, "book_design_snapshot_hash": "0" * 64}
        elif fault == "corrupt_snapshot":
            m = deepcopy(p.metadata_json)
            m["book_design_snapshot"]["tone"] = "different"
            p.metadata_json = m
        elif fault == "stale_canonical":
            c.content = {**c.content, "changed": True}
        elif fault == "canonical_status":
            c.status = "superseded"
        elif fault == "canonical_project":
            c.project_id = uuid4()
        elif fault == "asset_snapshot":
            a.content = {
                **a.content,
                "_meta": {**a.content["_meta"], "source_snapshot_hash": "0" * 64},
            }
        elif fault == "volume_projection":
            v.content = [{"_meta": {"source_projection_sha256": "0" * 64}}]
        elif fault == "volume_second_row":
            v.content = [*v.content, {"_meta": {"source_projection_sha256": "0" * 64}}]
        elif fault == "canonical_contract":
            c.content = {"concept_contract": {"input_hash": "forged"}}
            p.metadata_json = {
                **p.metadata_json,
                "creation_canonical_snapshot_hash": snapshot_hash(c.content),
            }
        before = deepcopy(p.metadata_json)
        with pytest.raises(ValueError):
            await selection.pin_completed_foundation(session, project=p, workflow_id=w.id)
        assert p.metadata_json == before and selection.POINTER not in p.metadata_json


@pytest.mark.parametrize(
    "fault", ["content", "map", "pointer", "canonical", "snapshot", "projection_inputs"]
)
async def test_persisted_pointer_revalidates_sources_and_full_original_map(case, fault):
    async with case.factory() as session:
        p = await session.get(ProjectModel, case.project.id)
        await selection.pin_completed_foundation(session, project=p, workflow_id=case.workflow.id)
        await session.commit()
    async with case.factory() as session:
        p = await session.get(ProjectModel, case.project.id)
        if fault == "content":
            a = await session.get(Artifact, case.artifacts["cast_spec"].id)
            a.content = {**a.content, "late_change": True}
        elif fault == "map":
            w = await session.get(WorkflowRunModel, case.workflow.id)
            w.metadata_json = {
                "artifact_ids": {
                    **w.metadata_json["artifact_ids"],
                    "promotional_brief": str(uuid4()),
                }
            }
        elif fault == "pointer":
            m = deepcopy(p.metadata_json)
            m[selection.POINTER]["artifact_content_hashes"]["book_spec"] = "forged"
            p.metadata_json = m
        elif fault == "canonical":
            c = await session.get(Artifact, case.canonical.id)
            c.content = {**c.content, "changed": True}
        elif fault == "snapshot":
            p.metadata_json = {**p.metadata_json, "book_design_snapshot_hash": "0" * 64}
        elif fault == "projection_inputs":
            p.metadata_json = {**p.metadata_json, "premise": "A different premise."}
        with pytest.raises(ValueError):
            await selection.load_selected_foundation_artifacts(session, project=p)


async def test_repin_preserves_old_snapshot_and_unrelated_metadata(case):
    async with case.factory() as session:
        p = await session.get(ProjectModel, case.project.id)
        first = await selection.pin_completed_foundation(
            session, project=p, workflow_id=case.workflow.id
        )
        w = WorkflowRunModel(
            id=uuid4(),
            project_id=p.id,
            workflow_type="generate_novel_plan",
            status="completed",
            scope_type="project",
            scope_id=p.id,
            metadata_json=deepcopy(case.workflow.metadata_json),
        )
        session.add(w)
        await session.flush()
        second = await selection.pin_completed_foundation(session, project=p, workflow_id=w.id)
        assert p.metadata_json[selection.HISTORY] == [first, second]
        assert p.metadata_json[selection.POINTER] == second
        assert p.metadata_json["unrelated_runtime"] == {"keep": True}


async def test_actual_retained_completed_n_cohort_projection_mismatch_is_not_waived(store):
    """Archived rows are data only; use real compiler/hash checks, no database readback."""
    factory, _ = store
    path = (
        Path(__file__).resolve().parents[2]
        / "reports/continuous-recovery-2026-10-16"
        / "20261017T220654891891Z-fresh-advance-6fee75dcb90b480d938aba8fb3c8933a.json"
    )
    data = json.loads(path.read_text())["after"]
    from uuid import UUID

    association = data["historical_foundation_associations"]["8f90199d-dc4a-4fd8-b380-3aaa4e606484"]
    pid = UUID(data["project_id"])
    wid = UUID(association["id"])
    p = ProjectModel(
        id=pid,
        slug="retained-fixture",
        title="retained",
        language="zh-CN",
        genre="玄幻",
        target_chapters=30,
        target_word_count=78000,
        metadata_json=deepcopy(data["metadata"]),
    )
    source = data["snapshot"]
    canonical = Artifact(
        id=UUID(source["id"]),
        project_id=pid,
        artifact_type="conception_snapshot",
        version_no=1,
        status=source["status"],
        schema_version="1.0",
        content=source["content"],
    )
    w = WorkflowRunModel(
        id=wid,
        project_id=pid,
        workflow_type=association["workflow_type"],
        status="completed",
        scope_type="project",
        scope_id=pid,
        metadata_json={"artifact_ids": association["artifact_ids"]},
    )
    rows = []
    for kind in selection.KINDS:
        r = data["artifact_history"][association["artifact_ids"][kind]]
        rows.append(
            Artifact(
                id=UUID(r["id"]),
                project_id=pid,
                artifact_type=kind,
                scope_ref_id=None,
                version_no=r["version_no"],
                status=r["status"],
                schema_version=r["schema_version"],
                content=deepcopy(r["content"]),
                source_run_id=UUID(r["source_run_id"]) if r["source_run_id"] else None,
            )
        )
    async with factory() as session:
        session.add_all([p, canonical, w, *rows])
        await session.commit()
    async with factory() as session:
        p = await session.get(ProjectModel, pid)
        assert [row.version_no for row in rows] == [2, 4, 2, 2]
        before = deepcopy(p.metadata_json)
        with pytest.raises(
            ValueError, match="foundation_selection_volume_compatibility_settings_required"
        ):
            await selection.pin_completed_foundation(session, project=p, workflow_id=wid)
        assert p.metadata_json == before and selection.POINTER not in p.metadata_json

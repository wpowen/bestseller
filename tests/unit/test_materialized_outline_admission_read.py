"""SQLite consumer fences; only existing grade/selection boundaries are stubbed."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401
from test_planning_draft_admission import harness as paid_harness  # noqa: F401

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db import models as db
from bestseller.services import llm, workflows
from bestseller.services import materialized_outline_admission_read as service
from bestseller.services import planning_draft_admission as admission
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (
    db.ProjectModel,
    db.PlanningArtifactVersionModel,
    db.WorkflowRunModel,
    db.ChapterModel,
    db.SceneCardModel,
)


def artifact(project, kind, content, **kwargs):
    return db.PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=kind,
        version_no=kwargs.pop("version_no", 1),
        status="approved",
        schema_version="1.0",
        content=deepcopy(content),
        **kwargs,
    )


def state_for(rig, receipt=None):
    return {
        "schema": service.SCHEMA,
        "source_artifact_id": str(rig.artifact.id),
        "source_content_sha256": content_hash(rig.artifact.content),
        "candidate_hash": content_hash(service._without_planning_meta(rig.artifact.content)),
        "materialization_workflow_run_id": str(rig.materializer.id),
        "foundation": {
            kind: {"id": str(source.id), "content_hash": content_hash(source.content)}
            for kind, source in rig.sources.items()
        },
        "canonical_id": rig.project.metadata_json["creation_canonical_snapshot_id"],
        "canonical_hash": rig.project.metadata_json["creation_canonical_snapshot_hash"],
        "source_admission": deepcopy(rig.receipt if receipt is None else receipt),
    }


async def install(rig, *, state=None, **kwargs):
    workflow = db.WorkflowRunModel(
        id=kwargs.pop("id", service.admission_workflow_id(rig.project.id, rig.artifact.id)),
        project_id=kwargs.pop("project_id", rig.project.id),
        workflow_type=kwargs.pop("workflow_type", service.WORKFLOW_TYPE),
        scope_type=kwargs.pop("scope_type", "planning_artifact"),
        scope_id=kwargs.pop("scope_id", rig.artifact.id),
        status=kwargs.pop("status", "completed"),
        metadata_json=state_for(rig) if state is None else deepcopy(state),
        **kwargs,
    )
    rig.session.add(workflow)
    await rig.session.commit()
    return workflow


async def require(rig, **kwargs):
    return await service.require_materialized_outline_source_admission(
        rig.session, rig.settings, project=rig.project, artifact=rig.artifact, **kwargs
    )


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    path = tmp_path / "materialized_outline_admission_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.url.drivername == "sqlite+aiosqlite" and "test" in engine.url.database
    async with engine.begin() as connection:
        for model in MODELS:
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        project = db.ProjectModel(
            slug="harbour-ledger",
            title="A harbour ledger",
            genre="historical",
            language="en",
            target_chapters=4,
            target_word_count=8000,
            metadata_json={},
        )
        session.add(project)
        await session.flush()
        source = {
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "The brass seal",
                    "chapter_goal": "Record the cargo transfer",
                    "target_word_count": 2000,
                    "scenes": [
                        {
                            "scene_number": 1,
                            "title": "The receipt",
                            "scene_type": "investigation",
                            "participants": ["Lena"],
                            "purpose": {"story": "Verify the seal"},
                            "entry_state": {"seal": "unread"},
                            "exit_state": {"seal": "recorded"},
                            "target_word_count": 2000,
                        }
                    ],
                }
            ],
            "_meta": {"retained_unknown": {"flag": False}},
        }
        outline = artifact(project, "chapter_outline_batch", source)
        sources = {
            kind: artifact(project, kind, {"rule": kind + " exact source"})
            for kind in ("book_spec", "world_spec", "cast_spec")
        }
        sources["volume_plan"] = artifact(
            project,
            "volume_plan",
            [{"volume_number": 1, "chapter_count_target": 4, "word_count_target": "8000"}],
        )
        canonical = artifact(project, "conception_snapshot", {"retained_author_source": "exact"})
        canonical.status = "canonical"
        session.add_all([outline, canonical, *sources.values()])
        await session.flush()
        project.metadata_json = {
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": content_hash(canonical.content),
        }
        materializer = db.WorkflowRunModel(
            project_id=project.id,
            workflow_type="materialize_chapter_outline_batch",
            status="completed",
            scope_type="planning_artifact",
            scope_id=outline.id,
            metadata_json={
                "source_artifact_id": str(outline.id),
                "outline_llm_commercial_judge": {
                    "pass": False,
                    "overall_score": 0.61,
                    "has_critical": True,
                    "blocking_issues": [{"severity": "critical", "code": "EDITORIAL_PREFERENCE"}],
                },
            },
        )
        session.add(materializer)
        await session.commit()
        receipt = {
            "draft_eligible": True,
            "foundation_hashes": {
                kind: {
                    "artifact_id": str(a.id),
                    "version": a.version_no,
                    "content_hash": content_hash(a.content),
                }
                for kind, a in sources.items()
                if kind != "volume_plan"
            },
            "commercial_report": deepcopy(
                materializer.metadata_json["outline_llm_commercial_judge"]
            ),
        }
        box = SimpleNamespace(
            session=session,
            project=project,
            artifact=outline,
            sources=sources,
            selected_sources=dict(sources),
            canonical=canonical,
            materializer=materializer,
            receipt=receipt,
            settings=load_settings(env={}),
            verified=[],
        )

        async def verify(_session, **kwargs):
            box.verified.append(kwargs)
            assert kwargs["workflow_run_id"] == service.admission_workflow_id(
                project.id, outline.id
            )
            assert kwargs["candidate_payload"] == service._without_planning_meta(outline.content)
            return deepcopy(kwargs["receipt"])

        async def deny(*args, **kwargs):
            pytest.fail("read admission must not call a model")

        async def selected(_session, *, project, settings):
            assert project.id == box.project.id
            return box.selected_sources

        # The selection boundary is inert; all four selected rows and their
        # canonical source remain real SQLite rows, read fresh by the consumer.
        monkeypatch.setattr(service, "load_selected_foundation_artifacts", selected)
        monkeypatch.setattr(admission, "verify_planning_draft_admission", verify)
        monkeypatch.setattr(admission, "complete_text", deny)
        monkeypatch.setattr(llm, "complete_text", deny)
        yield box
    await engine.dispose()


@pytest.mark.parametrize(
    "report",
    [
        {"pass": False},
        {"passed": False},
        {"pass": True, "has_critical": True},
        {"pass": True, "blocking_issues": [{"severity": "critical"}]},
    ],
)
async def test_negative_legacy_requires_new_proof_without_classifying_it_hard(rig, report):
    rig.materializer.metadata_json = {"outline_llm_commercial_judge": report}
    await rig.session.commit()
    with pytest.raises(
        ValueError, match="outline_source_admission_required:current_hard_admission_missing"
    ):
        await require(rig)
    assert not rig.verified
    await install(rig)
    assert (await require(rig))["source_admission"]["draft_eligible"] is True


@pytest.mark.parametrize(
    "fault", ["pass", "no_reports", "project", "artifact", "type", "scope", "running"]
)
async def test_unrelated_or_nonnegative_materializer_preserves_existing_path(rig, fault):
    if fault == "pass":
        rig.materializer.metadata_json = {"outline_llm_commercial_judge": {"pass": True}}
    elif fault == "no_reports":
        rig.materializer.metadata_json = {}
    else:
        key, value = {
            "project": ("project_id", uuid4()),
            "artifact": ("scope_id", uuid4()),
            "type": ("workflow_type", "unrelated"),
            "scope": ("scope_type", "project"),
            "running": ("status", "running"),
        }[fault]
        setattr(rig.materializer, key, value)
    await rig.session.commit()
    assert await require(rig) is None
    assert not rig.verified


async def test_reader_only_debt_also_requires_proof(rig):
    rig.materializer.metadata_json = {"outline_reader_experience_judge": {"pass": False}}
    await rig.session.commit()
    with pytest.raises(ValueError, match="current_hard_admission_missing"):
        await require(rig)


async def test_workflow_id_is_the_exact_shared_namespace_contract(rig):
    expected = uuid5(
        NAMESPACE_URL,
        "bestseller:materialized-outline-source-admission.v1:"
        + str(rig.project.id)
        + ":"
        + str(rig.artifact.id),
    )
    assert service.admission_workflow_id(rig.project.id, rig.artifact.id) == expected


@pytest.mark.parametrize(
    "report", [{"pass": 0}, {"has_critical": 1}, {"blocking_issues": None}, []]
)
async def test_malformed_historical_report_cannot_silently_become_no_negative(rig, report):
    rig.materializer.metadata_json = {"outline_reader_experience_judge": report}
    await rig.session.commit()
    with pytest.raises(ValueError, match="historical_report_malformed"):
        await require(rig)


@pytest.mark.parametrize("status", ["running", "machine_blocked", "failed", "cancelled"])
async def test_incomplete_or_negative_current_admission_stops_writer(rig, status):
    await install(rig, status=status)
    with pytest.raises(ValueError, match="current_hard_admission_scope_or_status"):
        await require(rig)
    assert not rig.verified


@pytest.mark.parametrize("fault", ["project_id", "scope_id", "scope_type", "workflow_type", "id"])
async def test_current_admission_exact_owner_and_stable_identity(rig, fault):
    value = "project" if fault == "scope_type" else "wrong" if fault == "workflow_type" else uuid4()
    await install(rig, **{fault: value})
    with pytest.raises(ValueError, match="outline_source_admission_required"):
        await require(rig)
    assert not rig.verified


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "source_artifact_id",
        "source_content_sha256",
        "candidate_hash",
        "materialization_workflow_run_id",
        "missing_receipt",
        "rejected",
        "bool_eligible",
        "foundation_id",
        "foundation_hash",
        "foundation_version",
        "foundation_bool_version",
    ],
)
async def test_malformed_or_changed_receipt_is_rejected_before_paid_verifier(rig, fault):
    state = state_for(rig)
    if fault == "missing_receipt":
        state["source_admission"] = None
    elif fault in {"rejected", "bool_eligible"}:
        state["source_admission"]["draft_eligible"] = False if fault == "rejected" else 1
    elif fault.startswith("foundation_"):
        field, value = {
            "foundation_id": ("artifact_id", str(uuid4())),
            "foundation_hash": ("content_hash", "f" * 64),
            "foundation_version": ("version", 2),
            "foundation_bool_version": ("version", True),
        }[fault]
        state["source_admission"]["foundation_hashes"]["world_spec"][field] = value
    else:
        state[fault] = "different"
    await install(rig, state=state)
    with pytest.raises(ValueError, match="outline_source_admission_required"):
        await require(rig)
    assert not rig.verified


@pytest.mark.parametrize(
    "fault", ["source_body", "source_meta", "foundation_body", "foundation_status", "new_selection"]
)
async def test_current_source_drift_cannot_reuse_old_grade(rig, fault):
    await install(rig)
    if fault == "source_body":
        rig.artifact.content = {**rig.artifact.content, "retained_extra": "changed"}
    elif fault == "source_meta":
        rig.artifact.content = {**rig.artifact.content, "_meta": {"retained_unknown": "changed"}}
    elif fault == "foundation_body":
        rig.sources["world_spec"].content = {"rule": "changed"}
    elif fault == "foundation_status":
        rig.sources["world_spec"].status = "needs_review"
    else:
        source = artifact(rig.project, "world_spec", {"rule": "new"}, version_no=2)
        rig.session.add(source)
        rig.selected_sources["world_spec"] = source
    await rig.session.commit()
    with pytest.raises(ValueError, match="outline_source_admission_required"):
        await require(rig)
    assert not rig.verified


async def test_selected_cohort_is_used_instead_of_unrelated_latest(rig, monkeypatch):
    await install(rig)
    rig.session.add(
        artifact(rig.project, "world_spec", {"rule": "unselected new source"}, version_no=2)
    )
    await rig.session.commit()

    async def selected(session, *, project, settings):
        assert project.id == rig.project.id
        return rig.sources

    monkeypatch.setattr(service, "load_selected_foundation_artifacts", selected)
    assert (await require(rig))["source_admission"] == rig.receipt
    assert rig.verified[0]["world_spec"] == rig.sources["world_spec"].content


@pytest.mark.parametrize("fault", ["foundation", "canonical_id", "canonical_hash"])
async def test_producer_binding_when_present_cannot_drift(rig, monkeypatch, fault):
    state = state_for(rig)
    state[fault] = (
        {"world_spec": {"id": "changed", "content_hash": "changed"}}
        if fault == "foundation"
        else "changed"
    )
    await install(rig, state=state)

    async def selected(*args, **kwargs):
        return rig.sources

    monkeypatch.setattr(service, "load_selected_foundation_artifacts", selected)
    with pytest.raises(ValueError, match="outline_source_admission_required"):
        await require(rig)
    assert not rig.verified


@pytest.mark.parametrize(
    "fault",
    [
        "missing_foundation",
        "missing_volume",
        "missing_canonical_id",
        "missing_canonical_hash",
        "no_selected_cohort",
    ],
)
async def test_v1_requires_complete_producer_bindings_before_three_source_grade(rig, fault):
    state = state_for(rig)
    if fault == "missing_foundation":
        state.pop("foundation")
    elif fault == "missing_volume":
        state["foundation"].pop("volume_plan")
    elif fault.startswith("missing_canonical"):
        state.pop(fault.removeprefix("missing_"))
    else:
        rig.selected_sources = None
    await install(rig, state=state)
    with pytest.raises(ValueError, match="outline_source_admission_required"):
        await require(rig)
    assert not rig.verified


@pytest.mark.parametrize(
    "fault",
    [
        "volume_body",
        "volume_selection",
        "canonical_selection",
        "canonical_body",
        "canonical_status",
    ],
)
async def test_volume_or_canonical_change_invalidates_unchanged_three_source_grade(rig, fault):
    workflow = await install(rig)
    saved_grade = deepcopy(workflow.metadata_json["source_admission"])
    three = {
        kind: (str(source.id), content_hash(source.content))
        for kind, source in rig.sources.items()
        if kind != "volume_plan"
    }
    if fault == "volume_body":
        rig.sources["volume_plan"].content = [{"volume_number": 1, "chapter_count_target": 3}]
    elif fault == "volume_selection":
        volume = artifact(
            rig.project, "volume_plan", rig.sources["volume_plan"].content, version_no=2
        )
        rig.session.add(volume)
        rig.selected_sources["volume_plan"] = volume
    elif fault == "canonical_selection":
        canonical = artifact(
            rig.project, "conception_snapshot", {"new_author_source": "different"}, version_no=2
        )
        canonical.status = "canonical"
        rig.session.add(canonical)
        await rig.session.flush()
        rig.project.metadata_json = {
            **rig.project.metadata_json,
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": content_hash(canonical.content),
        }
    elif fault == "canonical_body":
        rig.canonical.content = {"retained_author_source": "tampered"}
    else:
        rig.canonical.status = "superseded"
    await rig.session.commit()
    with pytest.raises(ValueError, match="outline_source_admission_required"):
        await require(rig)
    assert not rig.verified and workflow.metadata_json["source_admission"] == saved_grade
    assert {
        kind: (str(source.id), content_hash(source.content))
        for kind, source in rig.sources.items()
        if kind != "volume_plan"
    } == three


async def test_fresh_project_canonical_fence_does_not_refresh_cached_caller(rig):
    await install(rig)
    cached = deepcopy(rig.project.metadata_json)
    changed = {**cached, "creation_canonical_snapshot_hash": "0" * 64}
    await rig.session.execute(
        update(db.ProjectModel)
        .where(db.ProjectModel.id == rig.project.id)
        .values(metadata_json=changed)
        .execution_options(synchronize_session=False)
    )
    await rig.session.commit()
    assert rig.project.metadata_json == cached
    with pytest.raises(ValueError, match="canonical_binding_changed"):
        await require(rig)
    assert rig.project.metadata_json == cached and not rig.verified


async def test_actual_materialized_projection_and_foreign_scene_rejection(rig):
    workflow = await install(rig)
    typed = ChapterOutlineBatchInput.model_validate(rig.artifact.content).chapters[0]
    chapter = db.ChapterModel(project_id=rig.project.id, chapter_number=1, metadata_json={})
    for key in (
        "title",
        "chapter_goal",
        "opening_situation",
        "main_conflict",
        "hook_type",
        "hook_description",
        "target_word_count",
    ):
        setattr(chapter, key, getattr(typed, key))
    workflows._sync_chapter_causality_metadata(chapter, typed)
    workflows._sync_chapter_outline_semantics(chapter, typed)
    rig.session.add(chapter)
    await rig.session.flush()
    scene = db.SceneCardModel(project_id=rig.project.id, chapter_id=chapter.id, metadata_json={})
    for key in (
        "scene_number",
        "scene_type",
        "title",
        "time_label",
        "participants",
        "purpose",
        "entry_state",
        "exit_state",
        "key_dialogue_beats",
        "sensory_anchors",
        "forbidden_actions",
        "hook_requirement",
        "target_word_count",
    ):
        setattr(scene, key, getattr(typed.scenes[0], key))
    workflows._sync_scene_methodology_metadata(scene, typed.scenes[0])
    rig.session.add(scene)
    await rig.session.commit()
    result = await require(rig, chapter=chapter, scenes=[scene])
    assert result[
        "source_plan_projection"
    ] == service.chapter_outline_readiness.outline_prefix_projection_hash(chapter, [scene])
    scene.scene_number = True
    with pytest.raises(ValueError, match="source_plan_projection_scope"):
        await require(rig, chapter=chapter, scenes=[scene])
    scene.scene_number = 1
    workflow.metadata_json = {
        **workflow.metadata_json,
        "chapter_projection_hashes": {str(chapter.id): "changed"},
    }
    await rig.session.commit()
    with pytest.raises(ValueError, match="source_plan_projection_changed"):
        await require(rig, chapter=chapter, scenes=[scene])
    workflow.metadata_json = {
        **workflow.metadata_json,
        "chapter_projection_hashes": {str(chapter.id): result["source_plan_projection"]},
    }
    await rig.session.commit()
    scene.exit_state = {"seal": "an unapproved result"}
    with pytest.raises(ValueError, match="source_plan_projection_caller_stale"):
        await require(rig, chapter=chapter, scenes=[scene])
    scene.exit_state = typed.scenes[0].exit_state
    scene.chapter_id = uuid4()
    with pytest.raises(ValueError, match="source_plan_projection_scope"):
        await require(rig, chapter=chapter, scenes=[scene])


@pytest.mark.parametrize("marker", ["receipt", "producer"])
async def test_prefix_delegates_without_legacy_grade_or_new_protocol(rig, monkeypatch, marker):
    if marker == "receipt":
        rig.artifact.content = {
            **rig.artifact.content,
            "_meta": {"sourcebound_readiness_receipt": {"real": True}},
        }
    else:
        producer = db.WorkflowRunModel(
            project_id=rig.project.id,
            workflow_type="approve_chapter_outline_prefix",
            status="completed",
        )
        rig.session.add(producer)
        await rig.session.flush()
        rig.artifact.source_run_id = producer.id
    await rig.session.commit()
    calls = []

    async def prefix(session, **kwargs):
        calls.append(kwargs)
        return {"native_prefix_receipt": True}

    monkeypatch.setattr(
        service.chapter_outline_readiness, "verify_outline_prefix_readiness", prefix
    )
    assert await require(rig) == {"native_prefix_receipt": True}
    assert len(calls) == 1 and calls[0]["artifact_id"] == rig.artifact.id
    assert not rig.verified


async def test_invalid_prefix_does_not_fall_back_to_legacy(rig, monkeypatch):
    rig.artifact.content = {
        **rig.artifact.content,
        "_meta": {"sourcebound_readiness_receipt": None},
    }
    await rig.session.commit()

    async def reject(*args, **kwargs):
        raise ValueError("prefix_receipt_missing")

    monkeypatch.setattr(
        service.chapter_outline_readiness, "verify_outline_prefix_readiness", reject
    )
    with pytest.raises(
        ValueError, match="outline_source_admission_required:prefix_receipt_missing"
    ):
        await require(rig)
    assert not rig.verified


async def test_verifier_rejection_propagates_without_repair_or_fallback(rig, monkeypatch):
    await install(rig)

    async def reject(*args, **kwargs):
        raise ValueError("admission_paid_transport_changed")

    monkeypatch.setattr(admission, "verify_planning_draft_admission", reject)
    with pytest.raises(
        ValueError, match="outline_source_admission_required:admission_paid_transport_changed"
    ):
        await require(rig)


async def test_idempotent_reads_preserve_dirty_caller_and_all_database_rows(rig):
    workflow = await install(rig)
    original = deepcopy(workflow.metadata_json)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(rig.session.bind.sync_engine, "before_cursor_execute", record)
    try:
        rig.project.title = "pending caller title"
        rig.session.add(
            db.WorkflowRunModel(
                project_id=rig.project.id, workflow_type="unflushed caller work", status="running"
            )
        )
        first, second = await require(rig), await require(rig)
        assert first == second
        assert rig.project in rig.session.dirty and rig.session.new
        assert rig.project.title == "pending caller title"
        assert not any(
            s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements
        )
        assert workflow.metadata_json == original
        first["source_admission"]["commercial_report"]["pass"] = True
        assert workflow.metadata_json == original
        with rig.session.no_autoflush:
            assert (
                await rig.session.scalar(
                    select(db.ProjectModel.title).where(db.ProjectModel.id == rig.project.id)
                )
                == "A harbour ledger"
            )
    finally:
        event.remove(rig.session.bind.sync_engine, "before_cursor_execute", record)


async def test_actual_paid_receipt_roundtrip_and_tamper(paid_harness, monkeypatch):  # noqa: F811
    from test_planning_draft_admission import assess

    h = paid_harness
    engine = h.session_factory.kw["bind"]
    async with engine.begin() as connection:
        for model in (db.ProjectModel, db.PlanningArtifactVersionModel, db.WorkflowRunModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with h.session_factory() as session:
        h.project.target_word_count = 8000
        session.add(h.project)
        canonical = artifact(h.project, "conception_snapshot", {"retained_author_source": "exact"})
        canonical.status = "canonical"
        volume = artifact(
            h.project, "volume_plan", [{"volume_number": 1, "chapter_count_target": 40}]
        )
        session.add_all([canonical, volume])
        outline = artifact(h.project, "chapter_outline_batch", h.kwargs()["candidate_payload"])
        session.add(outline)
        for kind, source in h.sources.items():
            session.add(artifact(h.project, kind.value, source.content, id=source.id))
        await session.flush()
        h.project.metadata_json = {
            **h.project.metadata_json,
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": content_hash(canonical.content),
        }
        h.workflow.id = service.admission_workflow_id(h.project.id, outline.id)
        old = db.WorkflowRunModel(
            project_id=h.project.id,
            workflow_type="materialize_chapter_outline_batch",
            scope_type="planning_artifact",
            scope_id=outline.id,
            status="completed",
            metadata_json={"outline_llm_commercial_judge": h.commercial},
        )
        session.add(old)
        await session.commit()
    receipt = await assess(h)
    selected_sources = {kind.value: row for kind, row in h.sources.items()}
    selected_sources["volume_plan"] = volume

    async def selected(*args, **kwargs):
        return selected_sources

    monkeypatch.setattr(service, "load_selected_foundation_artifacts", selected)
    async with h.session_factory() as session:
        wrapper = SimpleNamespace(
            project=h.project,
            artifact=outline,
            materializer=old,
            receipt=receipt,
            sources=selected_sources,
        )
        session.add(
            db.WorkflowRunModel(
                id=h.workflow.id,
                project_id=h.project.id,
                workflow_type=service.WORKFLOW_TYPE,
                scope_type="planning_artifact",
                scope_id=outline.id,
                status="completed",
                metadata_json=state_for(wrapper),
            )
        )
        await session.commit()
        sends = len(h.sent)
        result = await service.require_materialized_outline_source_admission(
            session, h.settings, project=h.project, artifact=outline
        )
        assert result["source_admission"] == receipt and len(h.sent) == sends == 1
        run = await session.get(db.LlmRunModel, UUID(receipt["llm_run_id"]))
        run.model_name = "changed-after-paid-return"
        await session.commit()
        with pytest.raises(ValueError, match="outline_source_admission_required"):
            await service.require_materialized_outline_source_admission(
                session, h.settings, project=h.project, artifact=outline
            )
        assert len(h.sent) == sends

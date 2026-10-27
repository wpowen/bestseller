"""Real SQLite sources/journal; only the paid admission boundary is inert."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_foundation_selection import seed_objects, store  # noqa: F401 - shared SQLite fixture

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel as Artifact,
)
from bestseller.services import foundation_selection, llm, planning_draft_admission, workflows
from bestseller.services import materialized_outline_source_recovery as recovery
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(store, monkeypatch):  # noqa: F811 - pytest imports this shared fixture
    factory, engine = store
    assert engine.dialect.name == "sqlite"

    def no_network(*args, **kwargs):
        raise AssertionError("No PG/network/provider in materialized recovery tests")

    async def no_provider(*args, **kwargs):
        raise AssertionError("Only the hard-protocol boundary may be replaced")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(llm, "complete_text", no_provider)
    for model in (
        ChapterModel,
        SceneCardModel,
        BookProductionControlModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        LlmRunModel,
    ):
        table = model.__table__.to_metadata(MetaData())
        for col in table.c:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            if col.server_default is not None and "::" in str(col.server_default.arg):
                col.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    project, canonical, foundation, failed, assets = seed_objects()
    project.status = "planning"
    scope = f"project:{project.id}"
    project.metadata_json = {**project.metadata_json, "execution_scope_id": scope}
    settings = load_settings().model_copy(deep=True)
    assert str(settings.database.url).startswith("sqlite")
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "Survey the weather stations",
            "chapters": [
                {
                    "chapter_number": n,
                    "title": f"Signal {n}",
                    "chapter_goal": f"Measure sensor {n}",
                    "target_word_count": 2400,
                    "scenes": [
                        {
                            "scene_number": 1,
                            "participants": ["Mira"],
                            "purpose": {"story": f"Compare signal {n}"},
                            "target_word_count": 2400,
                        }
                    ],
                }
                for n in range(1, 5)
            ],
        }
    )
    content = {
        **batch.model_dump(mode="json"),
        "unknown_author_tree": {"unset": None},
        "_meta": {"unattributed_legacy_run": None, "original_raw": "unchanged"},
    }
    content["chapters"][0]["unknown_fact"] = {"keep": None}
    async with factory() as session:
        session.add_all([project, canonical, foundation, failed, *assets.values()])
        await session.commit()
        await foundation_selection.pin_completed_foundation(
            session, project=project, workflow_id=foundation.id, settings=settings
        )
        outline = Artifact(
            project_id=project.id,
            artifact_type="chapter_outline_batch",
            status="approved",
            version_no=1,
            schema_version="1.0",
            content=content,
            source_run_id=None,
        )
        session.add(outline)
        await session.flush()
        reports = {
            "outline_llm_commercial_judge": {
                "pass": False,
                "overall_score": 0.61,
                "blocking_issues": [
                    {
                        "severity": "critical",
                        "code": "OLD_COMMERCIAL",
                        "evidence": "Retained old concern",
                        "unknown": None,
                    }
                ],
            },
            "outline_reader_experience_judge": {
                "pass": False,
                "overall_score": 0.68,
                "blocking_issues": [{"severity": "critical", "code": "OLD_READER"}],
            },
        }
        owner = WorkflowRunModel(
            project_id=project.id,
            workflow_type="materialize_chapter_outline_batch",
            status="completed",
            scope_type="planning_artifact",
            scope_id=outline.id,
            metadata_json={"source_artifact_id": str(outline.id), **deepcopy(reports)},
        )
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=100,
            calls_used=9,
            max_tokens=1000000,
            tokens_charged=1234,
            status="active",
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            metadata_json={"old": None},
        )
        control = BookProductionControlModel(
            project_id=project.id, desired_state="run", command_serial=1, detail_json={"old": None}
        )
        session.add_all([owner, budget, control])
        chapters, scenes = [], []
        for typed in batch.chapters:
            chapter = ChapterModel(
                project_id=project.id,
                chapter_number=typed.chapter_number,
                **{
                    k: deepcopy(getattr(typed, k))
                    for k in (
                        "title",
                        "chapter_goal",
                        "opening_situation",
                        "main_conflict",
                        "hook_type",
                        "hook_description",
                        "target_word_count",
                    )
                },
                metadata_json={},
            )
            workflows._sync_chapter_causality_metadata(chapter, typed)
            workflows._sync_chapter_outline_semantics(chapter, typed)
            session.add(chapter)
            await session.flush()
            chapters.append(chapter)
            for typed_scene in typed.scenes:
                scene = SceneCardModel(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    scene_number=typed_scene.scene_number,
                    **{
                        k: deepcopy(getattr(typed_scene, k))
                        for k in (
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
                        )
                    },
                    metadata_json={},
                )
                workflows._sync_scene_methodology_metadata(scene, typed_scene)
                session.add(scene)
                scenes.append(scene)
        await session.commit()
    c = SimpleNamespace(
        factory=factory,
        settings=settings,
        project=project,
        canonical=canonical,
        assets=assets,
        outline=outline,
        owner=owner,
        budget=budget,
        control=control,
        chapters=chapters,
        scenes=scenes,
        scope=scope,
        reports=reports,
        calls=[],
        after=None,
        error=None,
        eligible=True,
    )

    async def protocol(session, settings, **kwargs):
        # Check that identity+state are durable BEFORE any simulated paid return.
        identity = recovery.admission_workflow_id(c.project.id, c.outline.id)
        async with factory() as other:
            durable = await other.get(WorkflowRunModel, identity)
            assert durable is not None and durable.project_id == c.project.id
            assert durable.metadata_json["source_content_sha256"] == content_hash(content)
        assert kwargs["workflow_run_id"] == identity
        assert kwargs["candidate_payload"]["unknown_author_tree"] == {"unset": None}
        assert kwargs["candidate_payload"]["chapters"][0]["unknown_fact"] == {"keep": None}
        assert "_meta" not in kwargs["candidate_payload"]
        for kind in ("book_spec", "world_spec", "cast_spec"):
            assert kwargs[kind] == assets[kind].content
        old = kwargs["commercial_report"]
        assert old["commercial"] == reports["outline_llm_commercial_judge"]
        assert old["reader"] == reports["outline_reader_experience_judge"]
        journal = kwargs["protocol_state"]
        c.calls.append({"identity": identity, "prior": deepcopy(journal)})
        # This is only a protocol-journal fixture, not physical/paid evidence.
        if not journal:
            journal.update(
                attempts=[{"raw": "retained protocol boundary", "unknown": None}],
                status="validated",
                candidate_hash=content_hash(kwargs["candidate_payload"]),
            )
        await kwargs["persist_protocol"]()
        if c.after:
            await c.after()
        if c.error:
            raise c.error
        return {
            "draft_eligible": c.eligible,
            "candidate_hash": journal["candidate_hash"],
            "assessment": {
                "hard_violations": [] if c.eligible else [{"code": "ABILITY_RULE_CONFLICT"}]
            },
            "commercial_report": deepcopy(old),
            "protocol_attempts": deepcopy(journal["attempts"]),
        }

    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", protocol)
    return c


async def invoke(c, *, scope=None):
    async with c.factory() as session:
        with bind_execution_scope(c.scope if scope is None else scope):
            return await recovery.assess_materialized_outline_source(
                session,
                c.settings,
                project_slug=c.project.slug,
                artifact_id=c.outline.id,
                materialization_workflow_id=c.owner.id,
                requested_by="offline-test",
            )


async def journal(c):
    async with c.factory() as session:
        return await session.get(
            WorkflowRunModel, recovery.admission_workflow_id(c.project.id, c.outline.id)
        )


async def mutate(c, model, identity, **changes):
    async with c.factory() as session:
        row = await session.get(model, identity)
        for name, value in changes.items():
            setattr(row, name, value)
        await session.commit()


async def protected(c):
    async with c.factory() as session:
        result = {}
        for model in (
            ProjectModel,
            Artifact,
            ChapterModel,
            SceneCardModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
            LlmRunModel,
        ):
            result[model.__tablename__] = deepcopy(
                list((await session.execute(select(*model.__table__.c))).mappings())
            )
        result["materializer"] = deepcopy(
            (await session.get(WorkflowRunModel, c.owner.id)).metadata_json
        )
        return result


async def test_actual_four_chapter_source_read_stable_workflow_and_old_negative_preservation(case):
    c = case
    before = await protected(c)
    first = await invoke(c)
    state = await journal(c)
    assert state.status == "completed" and state.current_step == "current_source_admitted"
    assert len(state.metadata_json["chapter_projection_hashes"]) == 4
    assert set(state.metadata_json["foundation"]) == set(foundation_selection.KINDS)
    assert state.metadata_json["historical_editorial_observations"] == c.reports
    assert state.metadata_json["grade_granted_to_old_editorial_reports"] is False
    assert first == await invoke(c)
    assert c.calls[1]["prior"]["attempts"] == first["protocol_attempts"]
    assert await protected(c) == before
    assert c.calls[0]["identity"] == c.calls[1]["identity"]
    assert c.calls[0]["identity"] != recovery.admission_workflow_id(uuid4(), c.outline.id)


async def test_valid_hard_rejection_survives_repeat_without_new_protocol_slot(case):
    c = case
    c.eligible = False
    first = await invoke(c)
    state = await journal(c)
    assert state.status == "machine_blocked" and state.current_step == "source_repair_required"
    assert first["draft_eligible"] is False
    assert await invoke(c) == first
    assert len((await journal(c)).metadata_json["protocol"]["attempts"]) == 1
    assert c.calls[1]["prior"]["attempts"] == first["protocol_attempts"]


async def test_recovered_success_archives_old_failure_without_stale_current_error(case):
    c = case
    first = await invoke(c)
    identity = recovery.admission_workflow_id(c.project.id, c.outline.id)
    await mutate(c, WorkflowRunModel, identity, status="failed",
        current_step="execution_result_unknown", error_message="execution_result_unknown")
    before = await protected(c)
    assert await invoke(c) == first
    saved = await journal(c)
    assert saved.status == "completed" and saved.error_message is None
    assert saved.metadata_json["previous_source_assessment_failures"] == [{
        "status": "failed", "current_step": "execution_result_unknown",
        "error_message": "execution_result_unknown"}]
    assert await protected(c) == before


@pytest.mark.parametrize("status", ["paused", "archived", "cancelled", "completed"])
async def test_project_halt_refuses_before_protocol_or_workflow(case, status):
    await mutate(case, ProjectModel, case.project.id, status=status)
    with pytest.raises(ValueError, match="project_halted"):
        await invoke(case)
    assert not case.calls and await journal(case) is None


@pytest.mark.parametrize("kind", ["control", "budget", "wrong_scope"])
async def test_control_financial_scope_refusal_before_protocol(case, kind):
    if kind == "control":
        await mutate(case, BookProductionControlModel, case.project.id, desired_state="pause")
    if kind == "budget":
        await mutate(case, ExecutionBudgetModel, case.scope, status="cancelled")
    with pytest.raises((ValueError, ExecutionBudgetError)):
        await invoke(case, scope="project:foreign" if kind == "wrong_scope" else None)
    assert not case.calls and await journal(case) is None


@pytest.mark.parametrize("kind", ["chapter", "scene", "artifact_owner", "owner"])
async def test_actual_source_or_projection_mismatch_never_calls_protocol(case, kind):
    if kind == "chapter":
        await mutate(case, ChapterModel, case.chapters[0].id, chapter_goal="Changed independently")
    elif kind == "scene":
        await mutate(
            case, SceneCardModel, case.scenes[0].id, purpose={"story": "Changed independently"}
        )
    elif kind == "artifact_owner":
        await mutate(case, Artifact, case.outline.id, project_id=uuid4())
    else:
        await mutate(case, WorkflowRunModel, case.owner.id, scope_id=uuid4())
    with pytest.raises(ValueError):
        await invoke(case)
    assert not case.calls and await journal(case) is None


@pytest.mark.parametrize(
    "kind", ["pause", "control", "scope", "source", "chapter", "scene", "canonical", "foundation"]
)
async def test_external_change_after_return_blocks_authority_retains_protocol(case, kind):
    c = case

    async def change():
        if kind == "pause":
            await mutate(c, ProjectModel, c.project.id, status="paused")
        elif kind == "control":
            await mutate(
                c, BookProductionControlModel, c.project.id, desired_state="pause", command_serial=2
            )
        elif kind == "scope":
            await mutate(
                c,
                ProjectModel,
                c.project.id,
                metadata_json={**c.project.metadata_json, "execution_scope_id": "project:external"},
            )
        elif kind == "source":
            await mutate(
                c, Artifact, c.outline.id, content={**c.outline.content, "new_unknown_field": None}
            )
        elif kind == "chapter":
            await mutate(c, ChapterModel, c.chapters[0].id, chapter_goal="External rewrite")
        elif kind == "scene":
            await mutate(c, SceneCardModel, c.scenes[0].id, purpose={"story": "External rewrite"})
        elif kind == "canonical":
            await mutate(
                c,
                Artifact,
                c.canonical.id,
                content={**c.canonical.content, "new_author_requirement": "Different viewpoint"},
            )
        else:
            await mutate(
                c,
                Artifact,
                c.assets["cast_spec"].id,
                content={**c.assets["cast_spec"].content, "new_identity": "Different observer"},
            )

    c.after = change
    with pytest.raises(ValueError) as failure:
        await invoke(c)
    state = await journal(c)
    assert "source_admission" not in state.metadata_json
    assert state.metadata_json["protocol"]["attempts"][0]["raw"] == "retained protocol boundary"
    assert failure.value.workflow_failures[0]["workflow_id"] == str(state.id)


@pytest.mark.parametrize("error", [RuntimeError("protocol failed"), asyncio.CancelledError()])
async def test_error_cancel_propagates_with_exact_durable_failure_identity(case, error):
    case.error = error
    with pytest.raises(type(error)) as failure:
        await invoke(case)
    assert failure.value is error
    state = await journal(case)
    assert state.metadata_json["protocol"]["attempts"]
    assert "source_admission" not in state.metadata_json
    receipt = error.workflow_failures[0]
    assert receipt["workflow_id"] == str(state.id)
    assert receipt["project_id"] == str(case.project.id)
    assert receipt["workflow_type"] == recovery.WORKFLOW_TYPE


async def test_same_artifact_dependency_drift_blocks_without_new_identity(case):
    await invoke(case)
    before = (await journal(case)).metadata_json
    await mutate(
        case,
        Artifact,
        case.outline.id,
        content={**case.outline.content, "unknown_new_source": None},
    )
    with pytest.raises(ValueError, match="original_dependency_changed"):
        await invoke(case)
    assert len(case.calls) == 1
    assert (await journal(case)).metadata_json == before


async def test_external_financial_stop_after_protocol_refuses_completed_admission(case):
    async def cancel_budget():
        await mutate(case, ExecutionBudgetModel, case.scope, status="cancelled")

    case.after = cancel_budget
    with pytest.raises(ExecutionBudgetError):
        await invoke(case)
    assert "source_admission" not in (await journal(case)).metadata_json


async def test_cancelled_existing_workflow_cannot_be_resurrected(case):
    await invoke(case)
    wid = recovery.admission_workflow_id(case.project.id, case.outline.id)
    await mutate(case, WorkflowRunModel, wid, status="cancelled")
    before = len(case.calls)
    with pytest.raises(ValueError):
        await invoke(case)
    assert len(case.calls) == before
    assert (await journal(case)).status == "cancelled"


async def test_external_workflow_cancellation_during_protocol_cannot_be_overwritten(case):
    wid = recovery.admission_workflow_id(case.project.id, case.outline.id)

    async def cancel_workflow():
        await mutate(case, WorkflowRunModel, wid, status="cancelled")

    case.after = cancel_workflow
    with pytest.raises(ValueError):
        await invoke(case)
    state = await journal(case)
    assert state.status == "cancelled"
    assert "source_admission" not in state.metadata_json


async def _race_after_final_workflow_read(case, monkeypatch, *, cancel):
    from bestseller.services.retained_planning_generation import _Reader

    wid = recovery.admission_workflow_id(case.project.id, case.outline.id)
    original_get = _Reader.get
    armed = False
    external_commits = []
    before = await protected(case)

    async def protocol_returned():
        nonlocal armed
        armed = True

    async def get_then_external_commit(reader, model, key):
        nonlocal armed
        stale = await original_get(reader, model, key)
        if armed and model is WorkflowRunModel and key == wid:
            # Return the actual pre-change column DTO. Commit from a separate
            # connection after that read, before the producer's conditional UPDATE.
            armed = False
            assert stale is not None and stale.status == "running"
            metadata = deepcopy(stale.metadata_json)
            history = metadata.setdefault("external_history", [])
            history.append({"action": "cancel" if cancel else "audit", "unknown": None})
            expected = {
                "status": "cancelled" if cancel else stale.status,
                "metadata_json": metadata,
            }
            await mutate(case, WorkflowRunModel, wid, **expected)
            external_commits.append(deepcopy(expected))
        return stale

    case.after = protocol_returned
    monkeypatch.setattr(_Reader, "get", get_then_external_commit)
    with pytest.raises(ValueError, match="workflow_changed_during_commit") as failure:
        await invoke(case)
    assert len(external_commits) == 1
    saved = await journal(case)
    assert saved.status == external_commits[0]["status"]
    assert saved.metadata_json == external_commits[0]["metadata_json"]
    assert saved.metadata_json["protocol"]["attempts"] == [
        {"raw": "retained protocol boundary", "unknown": None}
    ]
    assert "source_admission" not in saved.metadata_json
    assert failure.value.workflow_failures[0]["workflow_id"] == str(wid)
    assert len(case.calls) == 1
    assert await protected(case) == before


async def test_external_cancel_after_final_read_before_cas_is_preserved(case, monkeypatch):
    await _race_after_final_workflow_read(case, monkeypatch, cancel=True)


async def test_same_status_external_metadata_after_final_read_before_cas_is_preserved(
    case, monkeypatch
):
    await _race_after_final_workflow_read(case, monkeypatch, cancel=False)

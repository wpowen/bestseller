"""Integrated planner/cache tests: real SQLite/files, inert paid completions only."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_foundation_field_recovery import record_completion
from test_foundation_selection import seed_objects, store  # noqa: F401 - shared SQLite fixture

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import fanqie_beat_recovery as beat
from bestseller.services import fanqie_short_planner as planner
from bestseller.services import foundation_selection, llm, model_catalog
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(store, tmp_path, monkeypatch):  # noqa: F811 - shared SQLite fixture
    def no_network(*args, **kwargs):
        raise AssertionError("integrated BeatSheet tests cannot use PG/network/providers")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    factory, engine = store
    assert engine.dialect.name == "sqlite"
    for model in (
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        LlmRunModel,
        BookProductionControlModel,
    ):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    project, canonical, workflow, failed, assets = seed_objects()
    scope = "project:" + str(project.id)
    project.metadata_json = {**project.metadata_json, "execution_scope_id": scope}
    settings = load_settings().model_copy(deep=True)
    assert str(settings.database.url).startswith("sqlite")
    settings.llm.mock = False
    settings.llm.planner.model = "openai/integrated-beat-fixture"
    settings.llm.planner.model_override = None
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path / "physical")
    settings.artifact_store.retain_prompt_payloads = True
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"models":[]}')
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    monkeypatch.setattr(retained, "apply_runtime_llm_profile", lambda value: value)
    async with factory() as session:
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=100,
            max_tokens=1000000,
            calls_used=0,
            tokens_charged=0,
            status="active",
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            metadata_json={},
        )
        session.add_all([project, canonical, workflow, failed, *assets.values(), budget])
        await session.commit()
        await foundation_selection.pin_completed_foundation(
            session, project=project, workflow_id=workflow.id, settings=settings
        )
        await session.commit()
        c = SimpleNamespace(
            session=session,
            factory=factory,
            project=project,
            settings=settings,
            budget=budget,
            assets=assets,
            paths={},
        )
        c.sources = {
            "book_spec_id": str(assets["book_spec"].id),
            "cast_spec_id": str(assets["cast_spec"].id),
            "premise": project.metadata_json["premise"],
            "ideology_kernel": None,
        }
        # The shared recorder is transport-inert. Correct its optional-cap
        # fixture convention to match the gateway's resolved physical cap.
        persist = llm._persist_prompt_payloads

        def physical(**kwargs):
            if kwargs["effective_max_tokens"] is None:
                kwargs["effective_max_tokens"] = settings.llm.planner.max_tokens
            return persist(**kwargs)

        monkeypatch.setattr(llm, "_persist_prompt_payloads", physical)
        yield c


def payload(case):
    return {
        "title": "Unrelated observatory plan",
        "logline": "Compare observations",
        "pov": "first_person",
        "unlock_milestone_segment": 2,
        "unknown_authored": {"unset": None, "literal": "§0 $ref"},
        "beats": [
            {"segment_number": n, "beat_role": "event", "purpose": f"Compare signal {n}"}
            for n in range(1, case.project.target_chapters + 1)
        ],
    }


async def protected(case):
    result = []
    with case.session.no_autoflush:
        for model in (
            ProjectModel,
            PlanningArtifactVersionModel,
            LlmRunModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
            WorkflowRunModel,
        ):
            result.append(
                deepcopy(
                    list((await case.session.execute(select(*model.__table__.columns))).mappings())
                )
            )
    return result


async def ensure(case):
    return await planner.generate_fanqie_beat_sheet(
        case.session,
        case.settings,
        case.project.slug,
        case.sources["premise"],
        book_spec=case.assets["book_spec"].content,
        cast_spec=case.assets["cast_spec"].content,
        return_selection=True,
    )


async def test_new_producer_and_source_exact_cache_keep_raw_unknowns(case, monkeypatch):
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content=beat._json(payload(case)))
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with bind_execution_scope(case.budget.scope_id):
        first = await ensure(case)
        await case.session.commit()
        before = await protected(case)
        second = await ensure(case)
    assert len(calls) == 1 and first.artifact_id == second.artifact_id and second.reused
    assert calls[0].workflow_run_id is not None and calls[0].prompt_template == beat.TEMPLATE
    assert len(calls[0].prompt_version) <= 32
    row = await case.session.get(PlanningArtifactVersionModel, first.artifact_id)
    assert row.content["unknown_authored"] == {"unset": None, "literal": "§0 $ref"}
    assert not second.receipt["quality_approval_granted"] and await protected(case) == before


@pytest.mark.parametrize("failure", ["budget", "json", "fallback", "cancel", "boolean"])
async def test_failure_never_imports_generic_approved_sheet(case, monkeypatch, failure):
    import asyncio

    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        if failure == "budget":
            raise ExecutionBudgetError("execution_budget_exhausted")
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "json":
            paid, _ = await record_completion(case, request, content="not json")
            return paid
        if failure == "boolean":
            invalid = payload(case)
            invalid["beats"][0]["segment_number"] = True
            paid, _ = await record_completion(case, request, content=beat._json(invalid))
            return paid
        return llm.LLMCompletionResult(
            content=beat._json(payload(case)),
            provider="fallback",
            fallback_used=True,
            model_name="fallback",
            finish_reason="stop",
        )

    monkeypatch.setattr(llm, "complete_text", complete)
    expected = asyncio.CancelledError if failure == "cancel" else (ValueError, ExecutionBudgetError)
    with bind_execution_scope(case.budget.scope_id), pytest.raises(expected) as caught:
        await ensure(case)
    assert len(calls) == 1
    if failure == "budget":
        assert isinstance(caught.value, ExecutionBudgetError)
    if failure == "fallback":
        assert "paid_run_missing" in str(caught.value)
    assert not list(
        await case.session.scalars(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.artifact_type == "fanqie_beat_sheet"
            )
        )
    )


async def test_legacy_approved_is_pending_without_any_new_dispatch(case, monkeypatch):
    case.session.add(
        PlanningArtifactVersionModel(
            project_id=case.project.id,
            artifact_type="fanqie_beat_sheet",
            status="approved",
            version_no=1,
            schema_version="v1",
            content=payload(case),
            source_run_id=None,
        )
    )
    await case.session.commit()

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy ambiguity cannot dispatch")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    before = await protected(case)
    with pytest.raises(beat.BeatRecoveryPendingError, match="legacy_or_changed_plan"):
        await ensure(case)
    assert await protected(case) == before


@pytest.mark.parametrize("fault", ["body", "proof", "consumer", "source", "foreign", "file"])
async def test_cache_conflict_never_dispatches_or_changes_existing_evidence(
    case, monkeypatch, fault
):
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content=beat._json(payload(case)))
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with bind_execution_scope(case.budget.scope_id):
        first = await ensure(case)
        await case.session.commit()
        row = await case.session.get(PlanningArtifactVersionModel, first.artifact_id)
        value = deepcopy(row.content)
        receipt = value["_meta"]["beat_source_receipt"]
        if fault == "body":
            value["unknown_authored"]["unset"] = "tampered"
        elif fault == "proof":
            receipt["paid_proof"]["request_file_sha256"] = "0" * 64
        elif fault == "consumer":
            receipt["consumer_projection_sha256"] = "0" * 64
        elif fault == "source":
            receipt["source_manifest_sha256"] = "0" * 64
        elif fault == "foreign":
            row.project_id = uuid4()
            # Preserve an unknown legacy row for the current owner. Missing
            # matching evidence cannot turn historical planning into a miss.
            case.session.add(
                PlanningArtifactVersionModel(
                    project_id=case.project.id,
                    artifact_type="fanqie_beat_sheet",
                    status="approved",
                    version_no=2,
                    schema_version="v1",
                    content=payload(case),
                    source_run_id=None,
                )
            )
        else:
            case.paths[f"{receipt['llm_run_id']}:request"].unlink()
        row.content = value
        await case.session.commit()
        before = await protected(case)
        with pytest.raises((ValueError, FileNotFoundError)):
            await ensure(case)
    assert len(calls) == 1 and await protected(case) == before


async def test_runtime_only_metadata_does_not_repeat_paid_planning(case, monkeypatch):
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content=beat._json(payload(case)))
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with bind_execution_scope(case.budget.scope_id):
        first = await ensure(case)
        await case.session.commit()
        case.project.metadata_json = {
            **case.project.metadata_json,
            "unrelated_runtime": {"keep": "changed"},
        }
        await case.session.commit()
        second = await ensure(case)
    assert first.artifact_id == second.artifact_id and second.reused and len(calls) == 1


async def test_foreign_caller_source_and_closed_budget_refuse_before_dispatch(case, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("preflight refusal must not dispatch")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    with pytest.raises(beat.BeatRecoveryPendingError, match="caller_source_changed"):
        await beat.ensure_beat_sheet(
            case.session,
            case.settings,
            project_slug=case.project.slug,
            premise=case.sources["premise"],
            book_spec={"foreign": True},
            cast_spec=case.assets["cast_spec"].content,
        )
    case.budget.status = "unknown"
    await case.session.commit()
    before = await protected(case)
    with bind_execution_scope(case.budget.scope_id), pytest.raises(ExecutionBudgetError):
        await ensure(case)
    assert await protected(case) == before


async def test_failed_paid_json_does_not_authorize_another_draw(case, monkeypatch):
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content="invalid retained JSON")
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with bind_execution_scope(case.budget.scope_id):
        with pytest.raises(ValueError):
            await ensure(case)
        await case.session.commit()
        before = await protected(case)
        with pytest.raises(beat.BeatRecoveryPendingError, match="workflow_requires_recovery"):
            await ensure(case)
    assert len(calls) == 1 and await protected(case) == before


async def test_external_pause_after_completion_blocks_import_and_preserves_raw(case, monkeypatch):
    from sqlalchemy import update

    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content=beat._json(payload(case)))
        await session.commit()
        async with case.factory() as other:
            await other.execute(
                update(ProjectModel)
                .where(ProjectModel.id == case.project.id)
                .values(status="paused")
            )
            await other.commit()
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with (
        bind_execution_scope(case.budget.scope_id),
        pytest.raises(
            beat.BeatRecoveryPendingError,
            match="halted_or_blocked",
        ),
    ):
        await ensure(case)
    await case.session.commit()
    assert len(calls) == 1
    assert not list(
        await case.session.scalars(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.artifact_type == "fanqie_beat_sheet"
            )
        )
    )
    assert (
        await case.session.scalar(
            select(ProjectModel.status).where(ProjectModel.id == case.project.id)
        )
        == "paused"
    )
    assert len(list(await case.session.scalars(select(LlmRunModel)))) == 1
    assert all(path.is_file() for path in case.paths.values())


async def test_new_prompt_has_no_unselected_ideology_or_ability_obligation(case):
    manifest = await beat._current_sources(
        case.session,
        case.settings,
        case.project,
        case.sources["premise"],
        case.assets["book_spec"].content,
        case.assets["cast_spec"].content,
        None,
    )
    from bestseller.services.lossless_source_encoding import decode_lossless_source

    request = beat.build_current_request(case.project, manifest)
    envelope = json.loads(request.user_prompt.split("\n", 1)[1])
    logical = decode_lossless_source(envelope["planning_document"])
    assert logical["source"]["current_author_fields"] == {}
    assert "末段完成信念重建" not in request.system_prompt + beat._json(logical)
    assert "能力必须" not in request.system_prompt + beat._json(logical)


async def test_optional_sources_use_exact_verified_selection_and_advisory_is_not_authority(case):
    exact = await beat._current_sources(
        case.session,
        case.settings,
        case.project,
        case.sources["premise"],
        case.assets["book_spec"].content,
        case.assets["cast_spec"].content,
        None,
    )
    defaults = await beat._current_sources(
        case.session,
        case.settings,
        case.project,
        case.sources["premise"],
        None,
        None,
        {"thesis_statement": "Candidate advice cannot mandate a belief arc"},
    )
    assert defaults == exact
    assert defaults["current_author_fields"] == {}
    assert defaults["complete_foundation_content"]["book_spec"] == case.assets["book_spec"].content


async def test_public_default_returns_typed_sheet_and_same_cached_artifact(case, monkeypatch):
    from bestseller.domain.fanqie_short import FanqieShortBeatSheet

    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content=beat._json(payload(case)))
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with bind_execution_scope(case.budget.scope_id):
        first = await ensure(case)
        await case.session.commit()
        sheet = await planner.generate_fanqie_beat_sheet(
            case.session, case.settings, case.project.slug, case.sources["premise"]
        )
    assert isinstance(sheet, FanqieShortBeatSheet)
    assert sheet == first.sheet and len(calls) == 1


async def test_missing_budget_cannot_create_new_ledger_or_workflow(case, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("a missing ledger is never an implicit budget grant")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    await case.session.delete(case.budget)
    await case.session.commit()
    before = await protected(case)
    with (
        bind_execution_scope(case.budget.scope_id),
        pytest.raises(beat.BeatRecoveryPendingError, match="existing_budget_required"),
    ):
        await ensure(case)
    assert await protected(case) == before


@pytest.mark.parametrize("change", ["foundation", "budget", "workflow"])
async def test_external_change_after_paid_return_blocks_import_and_no_extra_draw(
    case, monkeypatch, change
):
    from sqlalchemy import update

    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        paid, _ = await record_completion(case, request, content=beat._json(payload(case)))
        await session.commit()
        async with case.factory() as other:
            if change == "foundation":
                await other.execute(
                    update(PlanningArtifactVersionModel)
                    .where(PlanningArtifactVersionModel.id == case.assets["cast_spec"].id)
                    .values(content={**case.assets["cast_spec"].content, "new_identity": None})
                )
            elif change == "budget":
                await other.execute(
                    update(ExecutionBudgetModel)
                    .where(ExecutionBudgetModel.scope_id == case.budget.scope_id)
                    .values(status="cancelled")
                )
            else:
                await other.execute(
                    update(WorkflowRunModel)
                    .where(WorkflowRunModel.id == request.workflow_run_id)
                    .values(status="cancelled")
                )
            await other.commit()
        return paid

    monkeypatch.setattr(llm, "complete_text", complete)
    with bind_execution_scope(case.budget.scope_id):
        with pytest.raises((ValueError, ExecutionBudgetError)):
            await ensure(case)
        await case.session.commit()
        with pytest.raises((ValueError, ExecutionBudgetError)):
            await ensure(case)
    assert len(calls) == 1
    assert not list(
        await case.session.scalars(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.artifact_type == "fanqie_beat_sheet"
            )
        )
    )
    assert len(list(await case.session.scalars(select(LlmRunModel)))) == 1
    assert all(path.is_file() for path in case.paths.values())
    if change == "workflow":
        status = await case.session.scalar(
            select(WorkflowRunModel.status).where(WorkflowRunModel.id == calls[0].workflow_run_id)
        )
        assert status == "cancelled"


@pytest.mark.parametrize(
    ("selected", "source", "raw_profile", "expected_style", "expected_sheet"),
    [
        ("third_limited", "explicit", "first-person", "third-limited", "third_person"),
        ("first_person", "explicit", "third-limited", "first-person", "first_person"),
        ("first_person", "unknown", "third-limited", "third-limited", "third_person"),
    ],
)
async def test_effective_pov_uses_validated_current_selection_preserving_raw_metadata(
    case, selected, source, raw_profile, expected_style, expected_sheet
):
    from bestseller.services import planner as source_planner
    from bestseller.services.conception_snapshots import snapshot_hash
    from bestseller.services.content_origin_policy import content_hash, origin_record
    from bestseller.services.creation_intent_contract import build_creation_intent_contract
    from bestseller.services.genre_intent_contract import contract_from_selection
    from bestseller.services.lossless_source_encoding import decode_lossless_source

    contract = build_creation_intent_contract(
        contract_from_selection({"channel": "male", "genre": "xianxia"}),
        pov=selected,
        chapter_count=case.project.target_chapters,
        field_sources={"pov": "explicit"},
    )
    raw_contract = contract.model_dump(mode="json")
    # Retain historical unknown attribution literally; it is not an author choice.
    raw_contract["field_sources"]["pov"] = source
    contract_hash = content_hash(raw_contract)
    case.project.metadata_json = {
        **case.project.metadata_json,
        "pov": "first_person",
        "creation_intent_contract": raw_contract,
        "creation_intent_contract_hash": contract_hash,
        "creation_input_receipt": {
            "schema_version": contract.schema_version,
            "contract_hash": contract_hash,
            "field_sources": deepcopy(raw_contract["field_sources"]),
        },
        "writing_profile": {"style": {"pov_type": raw_profile}},
        "writing_profile_origins": {"style.pov_type": origin_record(raw_profile, kind="unknown")},
    }
    # This fixture's VolumePlan is a deterministic projection, not a paid row.
    # Seed its exact binding for the newly selected inputs before taking read evidence.
    canonical = await case.session.get(
        PlanningArtifactVersionModel,
        UUID(case.project.metadata_json["creation_canonical_snapshot_id"]),
    )
    detached = SimpleNamespace(
        **{
            key: deepcopy(getattr(case.project, key))
            for key in ("id", "target_chapters", "target_word_count", "language", "metadata_json")
        }
    )
    detached._source_bound_planning_canonical = {
        "id": str(canonical.id),
        "content_hash": snapshot_hash(canonical.content),
        "content": deepcopy(canonical.content),
    }
    projection = source_planner._compile_source_bound_volume_plan(
        detached,
        case.sources["premise"],
        *(case.assets[kind].content for kind in foundation_selection.KINDS[:3]),
    )
    volume_content = deepcopy(case.assets["volume_plan"].content)
    volume_content[0]["_meta"]["source_projection_sha256"] = source_planner._approved_payload_hash(
        projection
    )
    case.assets["volume_plan"].content = volume_content
    await case.session.commit()
    await foundation_selection.pin_completed_foundation(
        case.session,
        project=case.project,
        workflow_id=case.assets["volume_plan"].source_run_id,
        settings=case.settings,
    )
    await case.session.commit()
    before = await protected(case)
    manifest = await beat._current_sources(
        case.session,
        case.settings,
        case.project,
        case.sources["premise"],
        case.assets["book_spec"].content,
        case.assets["cast_spec"].content,
        None,
    )
    assert manifest["effective_pov"] == {
        "style_pov_type": expected_style,
        "beat_sheet_pov": expected_sheet,
    }
    request = beat.build_current_request(case.project, manifest)
    envelope = json.loads(request.user_prompt.split("\n", 1)[1])
    logical = decode_lossless_source(envelope["planning_document"])
    assert logical["requirements"]["pov"] == expected_sheet
    explicit = logical["source"]["explicit_creation_contracts"].get("creation_intent_contract", {})
    assert explicit.get("pov") == (selected if source == "explicit" else None)
    assert await protected(case) == before

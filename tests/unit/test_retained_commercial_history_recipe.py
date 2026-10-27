"""Compare retained reconstruction with actual fresh-review commercial sources.

SQLite, native history readers and synthetic primary custody are real local
fixtures. Commercial physical proof is an explicit observation boundary; these
tests establish source-recipe equality, never real paid-origin authentication.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401
from test_commercial_action_projection import stable  # noqa: F401
from test_commercial_projection_quarantine_abstention import passing_slot
from test_primary_review_submission_entrypoint import _tool_completion, primary_db  # noqa: F401

from bestseller.domain.context import ChapterWriterContextPacket
from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    TimelineEventModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_llm_quality_judge as commercial
from bestseller.services import (
    commercial_judge_recovery,
    commercial_review_context,
    draft_continuity,
    llm,
    model_catalog,
    planning_continuity_context,
    review_source_context,
    reviews,
)
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.budget import bind_execution_scope
from bestseller.services.commercial_review_context import (
    load_commercial_review_history as native_history,
)
from bestseller.services.commercial_source_evidence import CommercialSources

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class RecipeBoundaryReachedError(Exception):
    """Stop before commercial physical custody; never return a verified proof."""


def snapshot(row):
    return {a.key: deepcopy(getattr(row, a.key)) for a in inspect(type(row)).column_attrs}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("history recipe tests must not use network or a provider SDK")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
async def recipe_case(primary_db, stable, tmp_path, monkeypatch):  # noqa: F811
    db = primary_db
    monkeypatch.setattr(commercial_review_context, "load_commercial_review_history", native_history)
    db.settings.pipeline.enable_chapter_llm_commercial_judge = True
    db.settings.artifact_store.mode = "local"
    db.settings.artifact_store.local_dir = str(tmp_path / "primary-custody")
    root = tmp_path / "primary-custody"
    root.mkdir()
    db.settings.llm.critic.model = "minimax/offline-history-recipe"
    db.settings.llm.critic.model_override = None
    entry = model_catalog.ModelCatalogEntry(
        id="history-recipe-fixture",
        display_name="History recipe fixture",
        vendor="fixture",
        model=db.settings.llm.critic.model,
        api_base="https://fixture.invalid/v1",
    )
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(json.dumps({"models": [entry.model_dump()]}))
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog_path)
    monkeypatch.setattr(
        model_catalog, "get_model_catalog_entry", lambda key: entry if key == entry.id else None
    )
    db.project.metadata_json = {"llm_model_id": entry.id}
    await db.session.commit()
    async with db.session.bind.begin() as connection:
        for model in (
            WorkflowRunModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
            CanonFactModel,
            TimelineEventModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    scope = f"test:retained-history:{db.project.id}"
    owner = WorkflowRunModel(
        project_id=db.project.id,
        workflow_type="chapter_pipeline",
        status="completed",
        scope_type="chapter",
        scope_id=db.chapter.id,
        metadata_json={},
    )
    db.session.add_all(
        [
            owner,
            ExecutionBudgetModel(
                scope_id=scope,
                max_calls=10,
                max_tokens=10000,
                calls_used=1,
                tokens_charged=7,
                deadline_at=datetime.now(UTC) + timedelta(days=1),
                status="active",
                metadata_json={"fixture": True},
            ),
        ]
    )
    await db.session.commit()
    captured = {}

    async def primary_return(session, settings, request):
        assert request.workflow_run_id == owner.id
        completion = _tool_completion()
        payload = json.loads(completion.tool_calls[0]["function"]["arguments"])
        payload.update(verdict="pass", rewrite_direction="")
        completion.tool_calls[0]["function"]["arguments"] = json.dumps(payload, ensure_ascii=False)
        completion.raw_message["tool_calls"] = deepcopy(completion.tool_calls)
        catalog, _ = proof._fresh_catalog()
        role = proof._fresh_role(settings, db.project, request, catalog)
        effective = llm._effective_input_hash(request, role)
        run_id, operation_key, attempt_id = uuid4(), uuid4().hex, uuid4().hex
        execution = {
            "scope_id": scope,
            "operation_key": operation_key,
            "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
        }
        value = llm.LLMCompletionResult(
            content=completion.content,
            provider=llm._provider_from_model(role.model),
            model_name=role.model,
            finish_reason="tool_calls",
            llm_run_id=run_id,
            tool_calls=completion.tool_calls,
            raw_message=completion.raw_message,
            input_tokens=3,
            output_tokens=4,
            metadata={
                "paid_call_origin": llm._paid_call_origin(request, effective),
                "effective_input_receipt": {"hash": effective},
            },
        )
        path = root / f"{run_id}.request.json"
        structured = {}
        for key in ("tools", "tool_choice"):
            encoded = json.dumps(
                getattr(request, key),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            structured[key] = {
                "status": "retained",
                "encoding": "json-utf8",
                "truncated": False,
                "original_chars": len(encoded),
                "limit_chars": len(encoded),
                "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            }
        path.write_text(
            json.dumps(
                {
                    "schema_version": "llm-payload.v1",
                    "kind": "request",
                    "replayable": True,
                    "llm_run_id": str(run_id),
                    "project_id": str(db.project.id),
                    "workflow_run_id": str(owner.id),
                    "logical_role": request.logical_role,
                    "prompt_template": request.prompt_template,
                    "prompt_version": request.prompt_version,
                    "requested": {
                        key: getattr(request, key)
                        for key in (
                            "model_tier",
                            "model_catalog_key",
                            "temperature_override",
                            "max_tokens_override",
                            "cache_system",
                            "tools",
                            "tool_choice",
                        )
                    },
                    "structured_payload_receipts": structured,
                    "messages": {
                        "source": "system_user",
                        "replayable": True,
                        "system": llm._bounded_text(request.system_prompt),
                        "user": llm._bounded_text(request.user_prompt),
                    },
                }
            )
        )
        run = LlmRunModel(
            id=run_id,
            project_id=db.project.id,
            workflow_run_id=owner.id,
            logical_role=request.logical_role,
            provider=value.provider,
            model_name=value.model_name,
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            prompt_hash=effective,
            finish_reason=value.finish_reason,
            request_payload_ref=path.name,
            metadata_json={
                **request.metadata,
                "execution_receipt": execution,
                "effective_input_receipt": {"hash": effective},
            },
        )
        now = datetime.now(UTC)
        paid = value.model_dump(mode="json")
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{scope}\n{attempt_id}".encode()
        ).hexdigest()
        session.add_all(
            [
                run,
                ExecutionOperationModel(
                    key=operation_key,
                    scope_id=scope,
                    input_hash=effective,
                    status="completed",
                    owner="fixture",
                    lease_until=now,
                    result_json=paid,
                ),
                ExecutionAttemptModel(
                    id=attempt_id,
                    operation_key=operation_key,
                    scope_id=scope,
                    model_name=role.model,
                    input_hash=effective,
                    reserved_tokens=7,
                    actual_tokens=7,
                    status="completed",
                    created_at=now,
                ),
                ExecutionOperationModel(
                    key=transport_key,
                    scope_id=scope,
                    input_hash=effective,
                    status="transport_result",
                    owner="fixture",
                    lease_until=now,
                    result_json={
                        "operation_key": operation_key,
                        "attempt_id": attempt_id,
                        "input_hash": effective,
                        "result": paid,
                    },
                ),
            ]
        )
        await session.flush()
        captured["primary_requests"] = captured.get("primary_requests", 0) + 1
        return value

    async def capture_sources(session, **kwargs):
        assert isinstance(kwargs["sources"], CommercialSources)
        captured["producer"] = deepcopy(kwargs["sources"])
        return None

    original_stable = commercial.judge_chapter_commercial_quality_stable

    async def controlled_stable(session, settings, **kwargs):
        src = captured["producer"]
        captured["producer_generation"] = deepcopy(kwargs["generation_input"])
        captured["producer_previous"] = deepcopy(kwargs["previous_chapters"])
        draws = [passing_slot(src, i, 0.9) for i in range(3)]

        async def draw(*args, **options):
            return draws[int(options["sample_slot"].rsplit(":", 1)[1])]

        monkeypatch.setattr(commercial, "judge_chapter_commercial_quality", draw)
        return await original_stable(
            session,
            settings,
            chapter_number=db.chapter.chapter_number,
            content_md=db.draft.content_md,
            samples=3,
        )

    monkeypatch.setattr(reviews, "complete_text", primary_return)
    monkeypatch.setattr(commercial_judge_recovery, "load_commercial_retry_context", capture_sources)
    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", controlled_stable)
    monkeypatch.setattr(
        commercial, "chapter_commercial_thresholds", lambda *args: (0.82, {"readability": 0.8})
    )

    async def prepare(mode):
        number = 1 if mode == "ch1" else 3
        db.chapter.chapter_number = number
        packet = ChapterWriterContextPacket(
            project_id=db.project.id,
            project_slug=db.project.slug,
            chapter_id=db.chapter.id,
            chapter_number=number,
            query_text="review",
            chapter_goal=db.chapter.chapter_goal,
        )
        primary_history = {
            "schema": "primary-empty-history-fixture",
            "project_id": str(db.project.id),
            "before_chapter": number,
            "adopted_sources": [],
            "recent_adopted_prose": [],
            "source_bound_canon": [],
            "source_bound_timeline": [],
        }
        primary_source = {
            "schema": proof.SOURCE_POLICY,
            "adopted_continuity": primary_history,
            "current_source_channels": {},
            "authority": proof._SOURCE_AUTHORITY,
        }

        async def context(*args, **kwargs):
            return packet

        async def sources(*args, **kwargs):
            return deepcopy(primary_source)

        monkeypatch.setattr(reviews, "build_chapter_writer_context", context)
        monkeypatch.setattr(proof, "build_chapter_writer_context", context)
        monkeypatch.setattr(review_source_context, "load_review_source_context", sources)
        monkeypatch.setattr(proof, "load_review_source_context", sources)
        priors = []
        if mode != "ch1":
            for prior_number in (1, 2):
                chapter = ChapterModel(
                    project_id=db.project.id,
                    chapter_number=prior_number,
                    chapter_goal="Prior manuscript",
                    metadata_json={},
                )
                db.session.add(chapter)
                await db.session.flush()
                pending = mode == "pending" and prior_number == 2
                draft = ChapterDraftVersionModel(
                    project_id=db.project.id,
                    chapter_id=chapter.id,
                    version_no=1,
                    content_md=f"Prior chapter {prior_number}: the receipt remains in the archive.",
                    is_current=not pending,
                    promotion_state="candidate" if pending else "promoted",
                )
                db.session.add(draft)
                await db.session.flush()
                # A candidate cannot displace formal adoption; in pending mode,
                # the selected qualified version can itself be noncurrent.
                db.session.add(
                    ChapterDraftVersionModel(
                        project_id=db.project.id,
                        chapter_id=chapter.id,
                        version_no=2,
                        content_md="Unqualified working candidate must stay out of history.",
                        is_current=pending,
                        promotion_state="candidate",
                    )
                )
                priors.append((chapter, draft))
        if mode == "pending":

            async def pending_mode(session, *, project_id, **kwargs):
                assert project_id == db.project.id
                return True

            async def qualified_reference(session, settings, *, project, chapter):
                assert project.id == db.project.id and chapter.id == priors[1][0].id
                draft = await session.get(ChapterDraftVersionModel, priors[1][1].id)
                return draft, {
                    "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
                    "receipt_id": "fixture-pending-qualification",
                }

            monkeypatch.setattr(draft_continuity, "draft_mode_enabled", pending_mode)
            monkeypatch.setattr(draft_continuity, "select_reference", qualified_reference)
        await db.session.commit()
        # Use the real history reader (including native adoption lookup,
        # predecessor checks and body hashes); only pending qualification is stubbed.
        history = await commercial_review_context.load_authorized_commercial_history(
            db.session, project_id=db.project.id, chapter_number=number
        )
        with bind_execution_scope(scope):
            result, report, _quality, task = await reviews.review_chapter_draft(
                db.session, db.settings, db.project.slug, number, workflow_run_id=owner.id
            )
        await db.session.commit()
        assert result.verdict == "pass" and task is None
        assert captured["primary_requests"] == 1
        assert (
            report.structured_output["evidence_summary"]["commercial_action_projection"]["status"]
            == "projected"
        )
        cycle = {
            "cycle_id": "history-recipe-fixture",
            "state": "completed",
            "source_draft_id": str(db.draft.id),
            "source_body_hash": hashlib.sha256(db.draft.content_md.encode()).hexdigest(),
            "review_workflow_run_id": str(owner.id),
        }
        captured["history"] = history
        return SimpleNamespace(
            db=db,
            owner=owner,
            scope=scope,
            report=report,
            cycle=cycle,
            captured=captured,
            primary_source=primary_source,
            priors=priors,
        )

    return prepare


async def observe(case, monkeypatch):
    async def commercial_boundary(session, settings, **kwargs):
        case.captured["consumer"] = deepcopy(kwargs["commercial_sources"])
        raise RecipeBoundaryReachedError

    monkeypatch.setattr(proof, "_commercial_proof", commercial_boundary)
    db = case.db
    with bind_execution_scope(case.scope):
        await proof.verify_retained_review_inputs(
            db.session,
            db.settings,
            project=db.project,
            chapter=db.chapter,
            draft=db.draft,
            report=case.report,
            cycle=case.cycle,
        )


@pytest.mark.parametrize("mode", ["ch1", "adopted", "pending"])
async def test_retained_recipe_matches_actual_fresh_review_sources(recipe_case, monkeypatch, mode):
    case = await recipe_case(mode)
    db = case.db
    models = (
        ChapterModel,
        ChapterDraftVersionModel,
        ReviewReportModel,
        LlmRunModel,
        QualityScoreModel,
        RewriteTaskModel,
        CanonFactModel,
        TimelineEventModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        WorkflowRunModel,
    )
    before = {
        model: [snapshot(row) for row in await db.session.scalars(select(model))]
        for model in models
    }
    payload_root = db.settings.artifact_store.local_dir
    request_files = {path: path.read_bytes() for path in Path(payload_root).iterdir()}
    with pytest.raises(RecipeBoundaryReachedError):
        await observe(case, monkeypatch)
    producer, consumer = case.captured["producer"], case.captured["consumer"]
    assert consumer.text == producer.text
    assert consumer.catalog == producer.catalog
    assert consumer.receipt == producer.receipt
    packet = json.loads(consumer.text)
    continuity = packet["source_context"]
    # Resolve semantic channel assertions from the actual projected packet;
    # literal packet equality above binds all unknown authored fields as well.
    assert case.captured["history"] != case.primary_source["adopted_continuity"]
    if mode == "ch1":
        assert case.captured["history"] == {} and packet["previous_chapters"] == []
    else:
        assert [p["chapter_number"] for p in packet["previous_chapters"]] == [1, 2]
        assert packet["previous_chapters"][-1]["body"] == case.priors[-1][1].content_md
        assert "Unqualified working candidate" not in consumer.text
    namespace = "draft_manuscript_continuity" if mode == "pending" else "adopted_continuity"
    other_namespace = "adopted_continuity" if mode == "pending" else "draft_manuscript_continuity"
    generation = case.captured["producer_generation"]
    assert namespace in generation and other_namespace not in generation
    assert "recent_adopted_prose" not in generation[namespace]
    assert case.captured["producer_previous"] == packet["previous_chapters"]
    assert namespace in json.dumps(continuity)
    if mode == "pending":
        assert generation[namespace]["qualified_pending_sources"][0]["draft_id"] == str(
            case.priors[1][1].id
        )
        assert (
            "pending_literary_review" in consumer.text
            and '"canon_authority":false' in consumer.text
        )
    assert not db.session.new and not db.session.dirty and not db.session.deleted
    after = {
        model: [snapshot(row) for row in await db.session.scalars(select(model))]
        for model in models
    }
    assert after == before and case.captured["primary_requests"] == 1
    assert {path: path.read_bytes() for path in Path(payload_root).iterdir()} == request_files


@pytest.mark.parametrize("mode", ["adopted", "pending"])
async def test_retained_history_requires_actual_immediate_predecessor(
    recipe_case, monkeypatch, mode
):
    case = await recipe_case(mode)
    original = planning_continuity_context.build_planning_continuity_context

    async def missing_predecessor(*args, **kwargs):
        context = await original(*args, **kwargs)
        key = "recent_qualified_pending_prose" if mode == "pending" else "recent_adopted_prose"
        context[key] = context[key][:-1]
        return context

    monkeypatch.setattr(
        planning_continuity_context, "build_planning_continuity_context", missing_predecessor
    )
    with pytest.raises(ValueError, match="commercial_immediate_predecessor_missing"):
        await observe(case, monkeypatch)
    assert "consumer" not in case.captured and case.captured["primary_requests"] == 1
    assert not case.db.session.new and not case.db.session.dirty and not case.db.session.deleted

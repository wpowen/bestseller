"""Replay historical alias failures through native SQLite payment evidence.

Only provider IO is inert. Gateway, ledger, retained payloads, frozen historical
parser, current parser and result binding all use their actual implementations.
"""

# ruff: noqa: RUF001 -- Chinese quotations are intentional immutable fixture data.

import ast
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.infra.db import models as db
from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_review_policy_history_8y24 as history
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import commercial_review_wire as wire
from bestseller.services import llm, model_catalog
from bestseller.services.budget import ExecutionLedger, bind_execution_scope
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_review_tool import (
    review_evaluation_contract,
    review_submission_tools,
    submitted_review_payload,
)
from bestseller.services.commercial_review_transport import prepare_commercial_review_transport
from bestseller.services.commercial_source_evidence import (
    assemble_commercial_sources,
    commercial_input_receipt,
    commercial_judge_unavailable,
)
from bestseller.services.write_gate import GateConfig
from support.database_safety import load_test_database_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
LEGACY_WIRE_SHA = "82344953ee0673329ce7f8b914b39323bce250e2418d8a3401824585f078c183"
SLOT = "chapter-quality.independent-v2:0"
MODEL = "openai/native-alias-cache-fixture"
BODY = "主桌问这一盘是不是少盛了半勺。\n\n后厨听见传菜声。"
MODELS = (
    ProjectModel,
    ChapterModel,
    ChapterDraftVersionModel,
    WorkflowRunModel,
    ReviewReportModel,
    LlmRunModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
    BookProductionControlModel,
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def source_packet(record, *, records=None, body=BODY):
    return assemble_commercial_sources(
        chapter_number=4,
        content_md=body,
        previous_chapters=[{"chapter_number": 3, "body": "传菜伙计按桌号传菜。"}],
        source={
            "adopted_continuity": {
                "source_bound_canon": [deepcopy(record)] if records is None else records
            }
        },
    )


def model_report(sources, source_id):
    body_id = next(key for key, row in sources.catalog.items() if row["kind"] == "current")
    return {
        "wire_version": wire.WIRE_VERSION,
        "pass": False,
        "overall_score": 0.61,
        "dimension_scores": {"readability": 0.61, "continuity": 0.67},
        "issues": [
            {
                "code": "SAME_EVENT_QUANTITY",
                "severity": "high",
                "assessment_kind": "prior_fact_conflict",
                "body_id": body_id,
                "source_id": source_id,
                "reason": "核对同一盘的分量，保留所引事件的时间范围。",
                "required_fix": "只核对所引事件的表述。",
                "temporal_relation": "same_event_or_state",
            }
        ],
        "rewrite_plan": {
            "scope": "chapter",
            "preserve": ["保留后厨催菜事件"],
            "change": ["核对同一盘的分量"],
            "instructions": "依据所引事件核对表述。",
        },
    }


@pytest.fixture
async def native(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("native alias cache tests cannot connect to providers or production")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    settings = load_test_database_settings().model_copy(deep=True)
    assert settings.database.url.startswith("sqlite")
    settings.llm.mock = False
    settings.llm.retry.rate_limit_fallback_enabled = False
    settings.llm.critic.model = MODEL
    settings.llm.critic.model_override = None
    settings.llm.critic.api_base = "https://native-alias-cache.invalid/v1"
    settings.llm.critic.stream = False
    settings.llm.critic.max_tokens = 4096
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path / "physical")
    settings.artifact_store.retain_prompt_payloads = True
    settings.output.base_dir = str(tmp_path / "output")
    catalog = tmp_path / "model_catalog.json"
    catalog.write_text('{"models":[]}', encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    model_catalog._load_catalog_raw.cache_clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'native_alias_test.sqlite3'}")
    tables = []
    for model in MODELS:
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        tables.append(table)
    async with engine.begin() as connection:
        for table in tables:
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    scope = "project:" + str(uuid4())
    record = {
        "id": str(uuid4()),
        "subject": "主桌传菜反馈",
        "predicate": "分量质疑",
        "value": "传菜伙计询问这一盘是否少盛了半勺",
        "evidence": "“主桌催菜，问这一盘是不是少盛了半勺。”",
        "source_chapter_id": str(uuid4()),
        "valid_from_chapter": 3,
    }
    async with factory() as session:
        project = ProjectModel(
            slug="native-alias-" + uuid4().hex,
            title="Native alias fixture",
            genre="general-fiction",
            target_chapters=4,
            target_word_count=8000,
            metadata_json={
                "execution_scope_id": scope,
                "once": {"consumed": True, "budget_reset": False},
                "Unknown": {"retained": None, "literal": "$ref §0"},
            },
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(project_id=project.id, chapter_number=4, chapter_goal="核对分量")
        session.add(chapter)
        await session.flush()
        draft = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md=BODY,
            is_current=True,
            promotion_state="under_review",
        )
        owner = WorkflowRunModel(
            project_id=project.id,
            workflow_type="chapter_pipeline",
            scope_type="chapter",
            scope_id=chapter.id,
            status="running",
            metadata_json={"once": ["consumed"]},
        )
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=100,
            max_tokens=1000000,
            calls_used=0,
            tokens_charged=0,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            status="active",
            metadata_json={},
        )
        session.add_all([draft, owner, budget])
        await session.commit()
        c = SimpleNamespace(
            session=session,
            engine=engine,
            factory=factory,
            settings=settings,
            project=project,
            chapter=chapter,
            draft=draft,
            owner=owner,
            budget=budget,
            scope=scope,
            record=record,
            sent=[],
        )

        async def network_io(request, role):
            c.sent.append(request.model_copy(deep=True))
            assert role.model == MODEL
            calls = [
                {
                    "id": "native-submission",
                    "type": "function",
                    "function": {
                        "name": "submit_chapter_review",
                        "arguments": c.raw,
                    },
                }
            ]
            auxiliary = "辅助文字不是结构化审校报告。"
            return (
                auxiliary,
                37,
                19,
                "tool_calls",
                calls,
                {
                    "role": "assistant",
                    "content": auxiliary,
                    "tool_calls": calls,
                },
            )

        monkeypatch.setattr(llm, "_call_litellm", network_io)
        try:
            yield c
        finally:
            model_catalog._load_catalog_raw.cache_clear()
    await engine.dispose()


async def retain_paid(c, *, canonical=False, records=None, overall_score=None):
    c.sources = source_packet(c.record, records=records)
    c.catalog_id = next(
        key for key, row in c.sources.catalog.items() if row["kind"] == "observation"
    )
    c.alias = "SRC:" + c.record["id"]
    c.payload = model_report(c.sources, c.catalog_id if canonical else c.alias)
    if overall_score is not None:
        c.payload["overall_score"] = overall_score
    c.raw = encode(c.payload)
    system, prefix = "Review only the supplied immutable evidence.", "Complete source packet:\n"
    tools = review_submission_tools(c.sources)
    user, transport = prepare_commercial_review_transport(
        system,
        prefix + c.sources.text,
        sources=c.sources,
        tools=tools,
        output_tokens=4096,
    )
    c.contract = review_evaluation_contract(
        settings=c.settings,
        sources=c.sources,
        system_prompt=system,
        instruction_prefix=prefix,
        tools=tools,
        min_overall=0.5,
        min_dimensions={"readability": 0.5},
        model_catalog_key=None,
        output_tokens=4096,
    )
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        model_tier="strong",
        system_prompt=system,
        user_prompt=user,
        fallback_response="{}",
        tools=tools,
        tool_choice="required",
        project_id=c.project.id,
        workflow_run_id=c.owner.id,
        prompt_template="chapter_commercial_quality_judge",
        prompt_version=c.sources.receipt["prompt_version"],
        sample_slot=SLOT,
        operation_id="native-alias-cache:" + str(c.draft.id),
        max_tokens_override=4096,
        metadata={
            "commercial_source_input_receipt": commercial_input_receipt(
                c.sources,
                system_prompt=system,
                user_prompt=user,
                tools=tools,
                output_tokens=4096,
            ),
            "commercial_source_transport": transport,
            "commercial_sample_slot": SLOT,
            "commercial_evaluation_contract": c.contract,
        },
    )
    with (
        bind_execution_scope(c.scope),
        llm.bind_project_prompt_audit(c.project.id, reason="native SQLite alias cache regression"),
    ):
        c.paid = await llm.complete_text(c.session, c.settings, request)
    await c.session.commit()
    c.run = await c.session.get(LlmRunModel, c.paid.llm_run_id)
    await c.session.refresh(c.budget)
    c.root = await c.session.get(
        ExecutionOperationModel,
        c.run.metadata_json["execution_receipt"]["operation_key"],
    )
    c.attempt = (
        await c.session.scalars(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.operation_key == c.root.key,
            )
        )
    ).one()
    c.transport = await c.session.get(
        ExecutionOperationModel,
        sha(
            f"transport-result.v1\n{c.scope}\n{c.attempt.id}",
        ),
    )
    assert c.root.status == c.attempt.status == "completed"
    assert c.transport.status == "transport_result"
    assert c.attempt.actual_tokens == c.budget.tokens_charged == 56
    assert c.budget.calls_used == 1 and len(c.sent) == 1
    for kind in ("request", "response"):
        path = Path(c.settings.artifact_store.local_dir) / getattr(c.run, kind + "_payload_ref")
        physical = json.loads(path.read_text(encoding="utf-8"))
        assert physical["kind"] == kind and physical["replayable"] is True
        assert physical["llm_run_id"] == str(c.run.id)
    return c


async def freeze_paid(c, *, canonical=False, records=None, overall_score=None):
    await retain_paid(c, canonical=canonical, records=records, overall_score=overall_score)
    # Obtain the OLD callable only from the trusted byte-pinned runtime loader;
    # never patch a validator to manufacture historical failures.
    legacy = history.legacy_source_alias_wire_parser()
    raw, submission, submission_errors = submitted_review_payload(c.paid)
    assert raw == c.raw and not submission_errors
    parsed, adapter, errors = legacy(raw, c.sources)
    if canonical:
        assert not errors and adapter["status"] == "verified"
    else:
        assert errors == ("issues/0/source_id: unknown or incorrectly typed source ID",)
        assert adapter["status"] == "rejected" and "derived_source_record_aliases" not in adapter
    result = quality_judge_result_from_mapping(
        parsed,
        scope="chapter",
        min_overall=c.contract["min_overall"],
        min_dimensions=c.contract["min_dimensions"],
        llm_run_id=str(c.run.id),
    )
    if errors:
        result = commercial_judge_unavailable(
            diagnostic="commercial source evidence unverified: " + "; ".join(errors),
            raw=c.paid.content,
            result=result,
        )
    c.cached = recovery.bind_commercial_slot_result(
        result,
        raw_report=raw,
        validation_errors=errors,
        source_receipt=c.sources.receipt,
        sample_slot=SLOT,
        attempt=1,
        adapter_receipt={**adapter, "submission": submission},
        evaluation_contract=c.contract,
    )
    c.report = ReviewReportModel(
        project_id=c.project.id,
        target_type="chapter",
        target_id=c.chapter.id,
        reviewer_type="commercial",
        verdict="unknown" if errors else "rejected",
        llm_run_id=c.run.id,
        structured_output={
            "slots": {SLOT: c.cached.model_dump(mode="json")},
            "Unknown": {"consumed": None},
            "once": ["already-consumed"],
        },
    )
    c.session.add(c.report)
    await c.session.commit()
    return c


async def snapshot(c):
    assert not c.session.new and not c.session.deleted and not c.session.dirty
    with c.session.no_autoflush:
        rows = [
            deepcopy(
                list(
                    (
                        await c.session.execute(
                            select(*model.__table__.columns).order_by(
                                *model.__table__.primary_key.columns
                            )
                        )
                    ).mappings()
                )
            )
            for model in MODELS
        ]
    files = {
        str(path): path.read_bytes()
        for path in Path(c.settings.artifact_store.local_dir).rglob("*")
        if path.is_file()
    }
    return rows, files, deepcopy(c.sources), deepcopy(c.report.structured_output)


async def replay(c, *, report=None, sources=None):
    return await recovery._verified_paid_slot(
        c.session,
        report=report if report is not None else c.report.structured_output["slots"][SLOT],
        sources=sources if sources is not None else c.sources,
        contract=c.contract,
        project_id=c.project.id,
        scope=c.scope,
        slot=SLOT,
        expected_model=MODEL,
        settings=c.settings,
    )


async def test_fixture_retains_native_paid_root_transport_settlement_and_tool_arguments(native):
    c = await retain_paid(native)
    raw, submission, errors = submitted_review_payload(c.paid)
    assert raw == c.raw and raw != c.paid.content and not errors
    assert submission["status"] == "received" and submission["tools_executed"] == 0
    assert c.root.result_json["tool_calls"] == c.transport.result_json["result"]["tool_calls"]
    assert c.root.result_json["metadata"]["paid_call_origin"]["project_id"] == str(c.project.id)
    assert c.root.result_json["metadata"]["paid_call_origin"]["workflow_run_id"] == str(c.owner.id)
    assert c.attempt.reserved_tokens > 0 and c.budget.status == "active"
    physical_root = Path(c.settings.artifact_store.local_dir)
    request = json.loads((physical_root / c.run.request_payload_ref).read_text(encoding="utf-8"))
    response = json.loads((physical_root / c.run.response_payload_ref).read_text(encoding="utf-8"))
    assert c.sources.text in request["messages"]["user"]["text"]
    assert response["tool_calls"][0]["function"]["arguments"] == c.raw


async def test_old_invalid_alias_cache_derives_current_domain_without_changing_history(native):
    c = await freeze_paid(native)
    # Preserve an unrelated, genuinely unknown native reservation alongside the
    # paid slot. Replaying an alias must not reconcile or refund it.
    unknown_scope = c.scope + ":unknown"
    c.session.add(
        ExecutionBudgetModel(
            scope_id=unknown_scope,
            max_calls=10,
            max_tokens=10000,
            calls_used=0,
            tokens_charged=0,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            status="active",
            metadata_json={},
        )
    )
    await c.session.commit()
    ledger = ExecutionLedger(c.engine, unknown_scope, c.settings.budget)
    claim = await ledger.claim("already-dispatched-once", sha("unknown-input"))
    unknown_id = await ledger.reserve(claim, MODEL, sha("unknown-input"), 101)
    await ledger.settle(unknown_id, "unknown")
    before = await snapshot(c)
    result, envelope, run = await replay(c)
    canonical = model_report(c.sources, c.catalog_id)
    parsed, adapter, errors = wire.parse_commercial_wire(c.raw, c.sources)
    canonical_parsed, _, canonical_errors = wire.parse_commercial_wire(encode(canonical), c.sources)
    assert not errors and not canonical_errors and parsed == canonical_parsed
    expected = quality_judge_result_from_mapping(
        canonical_parsed,
        scope="chapter",
        min_overall=c.contract["min_overall"],
        min_dimensions=c.contract["min_dimensions"],
        llm_run_id=str(c.run.id),
    )
    assert result.model_dump(exclude={"raw_excerpt"}) == expected.model_dump(
        exclude={"raw_excerpt"}
    )
    assert result.quality_rejected and not result.infrastructure_failure and not result.passed
    assert result.overall_score == c.cached.overall_score == c.payload["overall_score"]
    assert result.dimension_scores == c.cached.dimension_scores == c.payload["dimension_scores"]
    issue = parsed["blocking_issues"][0]
    assert issue["assessment_kind"] == "prior_fact_conflict"
    assert issue["temporal_relation"] == "same_event_or_state"
    assert issue["source_citations"][0]["id"] == c.catalog_id
    assert issue["source_citations"][0]["quote"] == c.record["evidence"]
    assert issue["conflict_reason"] == c.payload["issues"][0]["reason"]
    assert issue["body_citations"][0]["id"] == c.payload["issues"][0]["body_id"]
    assert result.blocking_issues[0].required_fix == c.payload["issues"][0]["required_fix"]
    assert envelope == json.loads(result.raw_excerpt)
    assert envelope["raw_report"] == c.raw and envelope["raw_report_sha256"] == sha(c.raw)
    assert json.loads(envelope["raw_report"])["issues"][0]["source_id"] == c.alias
    assert envelope["validation_errors"] == []
    assert envelope["adapter_receipt"] == {
        **adapter,
        "submission": submitted_review_payload(c.paid)[1],
    }
    original_envelope = json.loads(c.cached.raw_excerpt)
    assert {
        key: value
        for key, value in envelope.items()
        if key
        not in {
            "validation_errors",
            "adapter_receipt",
            "cached_source_alias_replay",
        }
    } == {
        key: value
        for key, value in original_envelope.items()
        if key
        not in {
            "validation_errors",
            "adapter_receipt",
        }
    }
    proof = envelope["cached_source_alias_replay"]
    assert proof["schema"] == "commercial-cached-source-alias-replay.v1"
    assert proof["legacy_wire_sha256"] == LEGACY_WIRE_SHA
    assert proof["llm_run_id"] == str(c.run.id)
    assert proof["paid_operation_key"] == c.root.key
    assert proof["transport_operation_key"] == c.transport.key
    assert proof["actual_attempt_id"] == c.attempt.id
    assert proof["physical_raw_report_sha256"] == sha(c.raw)
    assert proof["legacy_errors"] == original_envelope["validation_errors"]
    assert proof["aliases"] == adapter["derived_source_record_aliases"]
    assert proof["issues_dropped"] == 0
    assert all(
        proof[key] is False
        for key in (
            "scores_modified",
            "paid_raw_modified",
            "new_review_credit",
            "dispatch_authorized",
            "adoption_authorized",
        )
    )
    encoded_proof = json.dumps(
        {key: value for key, value in proof.items() if key != "proof_sha256"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert proof["proof_sha256"] == sha(encoded_proof)
    assert run is c.run
    projected, action_receipt = project_commercial_actions(
        result,
        sources=c.sources,
        gate_config=GateConfig({}, "audit_only"),
        chapter_number=4,
    )
    assert projected is not None and action_receipt["status"] == "projected"
    assert projected["raw_excerpt"] == result.raw_excerpt
    assert projected["overall_score"] == result.overall_score
    assert projected["dimension_scores"] == result.dimension_scores
    assert projected["blocking_issues"] == [{**issue, "action_receipt_index": 0}]
    assert action_receipt["issue_actions"][0]["authority"] == "verified_source"
    again = await replay(c)
    assert again[0].model_dump(mode="json") == result.model_dump(mode="json")
    assert again[1] == envelope and again[2].id == c.run.id
    assert await snapshot(c) == before and len(c.sent) == 1
    assert c.cached.infrastructure_failure
    assert json.loads(c.cached.raw_excerpt)["validation_errors"]
    unknown = await c.session.get(ExecutionAttemptModel, unknown_id)
    assert unknown.status == "unknown" and unknown.actual_tokens is None


async def native_normal_context(c):
    """Persist actual context rows; every subsequent source read stays native."""
    from bestseller.services import story_bible_read_views as views
    from bestseller.services.feedback import validate_adopted_knowledge_source

    async with c.engine.begin() as connection:
        for original in db.ProjectModel.metadata.tables.values():
            table = original.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default is not None and "::" in str(column.server_default.arg):
                    column.server_default = None
            for constraint in list(table.constraints):
                if isinstance(constraint, ForeignKeyConstraint):
                    table.constraints.remove(constraint)
            await connection.run_sync(lambda conn, table=table: table.create(conn, checkfirst=True))
    c.chapter.target_word_count = 2600
    c.draft.word_count = len(BODY)
    c.owner.status = "completed"
    prior = []
    for number in range(1, c.chapter.chapter_number):
        chapter = db.ChapterModel(
            project_id=c.project.id,
            chapter_number=number,
            chapter_goal=f"Native predecessor {number}",
            metadata_json={},
        )
        c.session.add(chapter)
        await c.session.flush()
        draft = db.ChapterDraftVersionModel(
            project_id=c.project.id,
            chapter_id=chapter.id,
            version_no=1,
            is_current=True,
            promotion_state="promoted",
            content_md=f"第{number}章：传菜伙计按桌号传菜。",
            word_count=18,
        )
        c.session.add(draft)
        prior.append((chapter, draft))
    c.session.add(
        db.SceneCardModel(
            project_id=c.project.id,
            chapter_id=c.chapter.id,
            scene_number=1,
            scene_type="development",
            participants=[],
            purpose={"story": "核对所引事件的分量"},
            entry_state={"location": "后厨"},
            exit_state={"location": "后厨"},
            metadata_json={},
        )
    )
    await c.session.commit()
    source_chapter, source_draft = prior[-1]
    lineage, rejection = await validate_adopted_knowledge_source(
        c.session,
        project_id=c.project.id,
        chapter_id=source_chapter.id,
        source_chapter_draft_version_id=source_draft.id,
        chapter_md=source_draft.content_md,
        lock=False,
    )
    assert rejection is None
    c.record["source_chapter_id"] = str(source_chapter.id)
    c.session.add(
        db.CanonFactModel(
            id=UUID(c.record["id"]),
            project_id=c.project.id,
            subject_type="event",
            subject_label=c.record["subject"],
            predicate=c.record["predicate"],
            fact_type="event",
            source_chapter_id=source_chapter.id,
            valid_from_chapter_no=3,
            is_current=True,
            value_json={
                "value": c.record["value"],
                "evidence": c.record["evidence"],
                "source_lineage": lineage,
            },
        )
    )
    # SQLite cannot run the producer's PostgreSQL advisory-lock statement.
    # Seed a minimal empty-Bible read view, deriving its receipt with native
    # source/projection hash functions, then require the actual native validator.
    root = db.NarrativeTreeNodeModel(
        project_id=c.project.id,
        node_path="/book",
        depth=1,
        node_type="book_root",
        title=c.project.title,
        summary=c.project.title,
        body_md=c.project.title,
        source_type="container",
        scope_level="project",
        lexical_document="",
        metadata_json={},
    )
    c.session.add(root)
    await c.session.commit()
    sources, source_hash = await views._sources(c.session, c.project)
    assert all(not rows for rows in sources.values())
    derived, chunks, nodes = await views._derived(c.session, c.project.id)
    views._require_complete(sources, chunks, nodes)
    root.metadata_json = {
        views._RECEIPT: {
            "source_sha256": source_hash,
            "projection_sha256": views._projection_identity(c.settings),
            **derived,
        }
    }
    await c.session.commit()
    validated = await views.validate_story_bible_read_views(
        c.session, c.settings, project=c.project
    )
    assert validated["status"] == "current"
    return c


async def render_normal_context(c, *, samples=3):
    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services import waiting_chapter_review_resume as waiting
    from bestseller.services.chapter_generation_input_builder import (
        build_chapter_generation_input_bundle,
    )
    from bestseller.services.commercial_review_context import load_authorized_commercial_history
    from bestseller.services.context import build_chapter_writer_context
    from bestseller.services.creation_contract_authority import select_creation_contracts
    from bestseller.services.judge_genre_context import resolve_judge_genre_context
    from bestseller.services.planner import _source_bound_cast_enabled
    from bestseller.services.prompt_packs import resolve_prompt_pack

    reader = waiting._Reader(c.session)
    context = await build_chapter_writer_context(
        reader,
        c.settings,
        c.project.slug,
        c.chapter.chapter_number,
    )
    scenes = list(
        await reader.scalars(
            select(db.SceneCardModel)
            .where(
                db.SceneCardModel.project_id == c.project.id,
                db.SceneCardModel.chapter_id == c.chapter.id,
            )
            .order_by(db.SceneCardModel.scene_number)
        )
    )
    generation = build_chapter_generation_input_bundle(
        project=c.project,
        chapter=c.chapter,
        scenes=scenes,
        context_packet=context,
        target_word_count=c.chapter.target_word_count,
    ).model_dump(mode="json")
    generation["approved_creation_contracts"] = select_creation_contracts(
        c.project.metadata_json or {},
        source_bound=_source_bound_cast_enabled(c.project),
    )
    history_packet = await load_authorized_commercial_history(
        reader,
        project_id=c.project.id,
        chapter_number=c.chapter.chapter_number,
    )
    previous = (
        history_packet.get("recent_reference_prose")
        or history_packet.get("recent_adopted_prose")
        or []
    )
    generation["adopted_continuity"] = {
        key: value for key, value in history_packet.items() if key != "recent_adopted_prose"
    }
    selected = judge._commercial_source_projection(
        generation,
        chapter_number=c.chapter.chapter_number,
        content_md=c.draft.content_md,
        previous_chapters=previous,
    )
    genre = resolve_judge_genre_context(
        genre=c.project.genre,
        sub_genre=c.project.sub_genre,
        story_bible=selected.get("story_bible"),
    )
    pack = resolve_prompt_pack(None, genre=c.project.genre, sub_genre=c.project.sub_genre)
    c.normal_generation, c.normal_previous, c.normal_genre, c.normal_pack = (
        generation,
        previous,
        genre,
        pack,
    )
    return await waiting._render_current_inputs(
        reader,
        c.settings,
        chapter_number=c.chapter.chapter_number,
        content_md=c.draft.content_md,
        generation_input=generation,
        previous_chapters=previous,
        workflow_run_id=c.owner.id,
        project_id=c.project.id,
        chapter_id=c.chapter.id,
        draft_id=c.draft.id,
        pack=pack,
        genre_context=genre,
        sample_slot=SLOT,
        commercial_cohort_samples=samples,
    )


async def test_native_normal_context_uses_actual_rows_sources_and_renderer(native, monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLES", "3")
    c = await native_normal_context(native)
    rendered = await render_normal_context(c)
    assert isinstance(rendered, dict) and rendered["schema"] == "waiting-commercial-render.v1"
    observations = [
        row for row in rendered["sources"].catalog.values() if row["kind"] == "observation"
    ]
    actual = [
        row
        for row in observations
        if row["source_path"].startswith("/adopted_continuity/source_bound_canon/")
    ]
    assert len(actual) == 1 and actual[0]["text"] == c.record["evidence"]
    assert not c.sent and not c.session.new and not c.session.deleted and not c.session.dirty


async def frozen_native_aggregate(c, results):
    """Execute the archive producer's exact aggregation suffix without storage IO."""
    from bestseller.services import chapter_llm_quality_judge as judge

    if len(results) == 1:
        return results[0]  # Native n=1 returns the slot directly, without a quorum receipt.

    def suffix(source):
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "judge_chapter_commercial_quality_stable"
        )
        start = next(
            index
            for index, node in enumerate(function.body)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "answered" for target in node.targets
            )
        )
        return ast.Module(body=deepcopy(function.body[start:]), type_ignores=[])

    archived = history.issued_f157_bytes("src/bestseller/services/chapter_llm_quality_judge.py")
    original = suffix(archived)
    current = suffix(inspect.getsource(judge.judge_chapter_commercial_quality_stable))
    assert ast.dump(original, include_attributes=False) == ast.dump(
        current, include_attributes=False
    )

    class DetachStorage(ast.NodeTransformer):
        def visit_Await(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                if node.value.func.id == "retain_aggregate":
                    assert len(node.value.args) == 1 and not node.value.keywords
                    return node.value.args[0]
            return self.generic_visit(node)

    wrapper = ast.parse(
        "async def archived_aggregate(results, settings, chapter_number, corpus_key, n):\n"
        "    pass\n"
    )
    wrapper.body[0].body = DetachStorage().visit(original).body
    ast.fix_missing_locations(wrapper)
    namespace = vars(judge).copy()
    exec(compile(wrapper, "issued-f157/native-commercial-aggregation", "exec"), namespace)
    return await namespace["archived_aggregate"](
        results,
        c.settings,
        c.chapter.chapter_number,
        c.normal_genre.corpus_key or judge.GENERIC_CORPUS_KEY,
        len(results),
    )


async def freeze_normal_cohort(
    c, monkeypatch, *, mode, alias=True, truncate=True, literal_reason=None, samples=3
):
    """Real producer slot journal, requests, gateway, payment and old aggregation."""
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
    from bestseller.services import pipelines

    monkeypatch.setenv("JUDGE_SAMPLES", "3")
    await native_normal_context(c)
    c.cohort_samples = samples
    rendered = await render_normal_context(c, samples=samples)
    assert isinstance(rendered, dict) and rendered["schema"] == "waiting-commercial-render.v1"
    c.sources = rendered["sources"]
    c.contract = rendered["evaluation_contract"]
    c.catalog_id = next(
        key
        for key, row in c.sources.catalog.items()
        if row["kind"] == "observation"
        and row["source_path"] == "/adopted_continuity/source_bound_canon/0"
    )
    c.alias = "SRC:" + c.record["id"]
    c.raws = {
        f"chapter-quality.independent-v2:{index}": encode(
            model_report(
                c.sources,
                c.alias if index == 0 and alias else c.catalog_id,
            )
        )
        for index in range(samples)
    }
    if literal_reason is not None:
        for slot, raw in c.raws.items():
            payload = json.loads(raw)
            payload["issues"][0]["reason"] = literal_reason
            c.raws[slot] = encode(payload)

    async def network_io(request, role):
        assert len(c.sent) < samples, (
            "normal retained replay must never dispatch another provider call"
        )
        c.sent.append(request.model_copy(deep=True))
        assert role.model == MODEL
        if truncate and request.sample_slot == "chapter-quality.independent-v2:2":
            content = '{"wire_version":"commercial-review-wire.v2","issues":['
            return content, 37, 19, "length", None, {"role": "assistant", "content": content}
        raw = c.raws[request.sample_slot]
        calls = [
            {
                "id": "native-three-slot-" + str(request.sample_slot),
                "type": "function",
                "function": {"name": "submit_chapter_review", "arguments": raw},
            }
        ]
        auxiliary = "辅助文字不是结构化审校报告。"
        return (
            auxiliary,
            37,
            19,
            "tool_calls",
            calls,
            {
                "role": "assistant",
                "content": auxiliary,
                "tool_calls": calls,
            },
        )

    monkeypatch.setattr(llm, "_call_litellm", network_io)
    legacy = history.legacy_source_alias_wire_parser()
    results, c.paid_runs = [], []
    for index in range(samples):
        slot = f"chapter-quality.independent-v2:{index}"
        with bind_execution_scope(c.scope):
            cached, binding = await recovery.prepare_commercial_component_slot(
                c.session,
                project_id=c.project.id,
                chapter_id=c.chapter.id,
                draft_id=c.draft.id,
                content_md=c.draft.content_md,
                workflow_run_id=c.owner.id,
                sources=c.sources,
                evaluation_contract=c.contract,
                sample_slot=slot,
                samples=samples,
                retry_context=None,
                effective_critic=rendered["effective_critic"],
                current_input_hash=rendered["current_input_hash"],
                settings=c.settings,
            )
        assert cached is None
        request = llm.LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=rendered["system"],
            user_prompt=rendered["user"],
            fallback_response="{}",
            tools=rendered["tools"],
            tool_choice="required",
            max_tokens_override=rendered["output_tokens"],
            project_id=c.project.id,
            workflow_run_id=UUID(str(binding["owner_id"])),
            prompt_template="chapter_commercial_quality_judge",
            prompt_version=c.sources.receipt["prompt_version"],
            sample_slot=slot,
            metadata={
                "prepared_effective_input": {
                    "schema": "effective-input.v1",
                    "hash": rendered["current_input_hash"],
                },
                "commercial_source_input_receipt": commercial_input_receipt(
                    c.sources,
                    system_prompt=rendered["system"],
                    user_prompt=rendered["user"],
                    tools=rendered["tools"],
                    output_tokens=rendered["output_tokens"],
                ),
                "commercial_source_transport": rendered["source_transport"],
                "commercial_sample_slot": slot,
                "commercial_evaluation_contract": c.contract,
            },
        )
        request, source_receipt = await recovery.freeze_commercial_component_request(
            c.session,
            binding,
            request,
            c.sources.receipt,
        )
        with (
            bind_execution_scope(c.scope),
            llm.bind_execution_operation(binding["namespace"]),
            llm.bind_project_prompt_audit(
                c.project.id, reason="native three-slot normal regression"
            ),
        ):
            paid = await llm.complete_text(c.session, c.settings, request)
        await c.session.commit()
        run = await c.session.get(LlmRunModel, paid.llm_run_id)
        c.paid_runs.append(run)
        raw, submission, submission_errors = submitted_review_payload(paid)
        if index == 2 and truncate:
            assert paid.finish_reason == "length" and not paid.tool_calls
            errors = ("output: truncated at structured-review reserve",)
            adapter = {
                "adapter_version": wire.ADAPTER_VERSION,
                "wire_version": wire.WIRE_VERSION,
                "source_binding": deepcopy(dict(source_receipt)),
                "status": "not_run_output_truncated",
            }
            result = commercial_judge_unavailable(
                diagnostic=(
                    "commercial judge output truncated at structured-review reserve; "
                    "judge-only repair"
                ),
                raw=paid.content,
                result=LLMQualityJudgeResult(llm_run_id=str(run.id)),
            )
        else:
            assert not submission_errors
            parsed, adapter, errors = legacy(raw, c.sources)
            assert bool(errors) is (index == 0 and alias)
            result = quality_judge_result_from_mapping(
                parsed,
                scope="chapter",
                min_overall=c.contract["min_overall"],
                min_dimensions=c.contract["min_dimensions"],
                llm_run_id=str(run.id),
            )
            if errors:
                result = commercial_judge_unavailable(
                    diagnostic="commercial source evidence unverified: " + "; ".join(errors),
                    raw=paid.content,
                    result=result,
                )
        bound = recovery.bind_commercial_slot_result(
            result,
            raw_report=raw,
            validation_errors=errors,
            source_receipt=source_receipt,
            sample_slot=slot,
            attempt=1,
            adapter_receipt={**adapter, "submission": submission},
            evaluation_contract=c.contract,
        )
        await recovery.save_commercial_component_slot(c.session, binding, bound)
        results.append(bound)
        c.cohort_key = binding["key"]
    c.original_aggregate = await frozen_native_aggregate(c, results)
    original_receipt = json.loads(c.original_aggregate.raw_excerpt)
    unavailable = alias and (samples == 1 or truncate)
    assert c.original_aggregate.infrastructure_failure is unavailable
    if samples == 1:
        assert original_receipt["schema_version"] == recovery.SLOT_RECEIPT_SCHEMA
    else:
        assert original_receipt["valid_samples"] == (1 if alias else 2) + (0 if truncate else 1)
        assert original_receipt["status"] == (
            "insufficient_valid_samples" if unavailable else "aggregated"
        )
        assert [sample["valid"] for sample in original_receipt["samples"]] == [
            not alias,
            True,
            not truncate,
        ]
    # Freeze historical aggregate data; its old slots remain the immutable
    # native producer outputs even when a current consumer derives a new view.
    journal_metadata = deepcopy(c.owner.metadata_json)
    journal = journal_metadata["commercial_component_cohorts"][c.cohort_key]
    journal["aggregate"] = {
        "slots_sha256": recovery._digest(
            {key: entry["report"] for key, entry in journal["slots"].items()}
        ),
        "report": c.original_aggregate.model_dump(mode="json"),
    }
    c.owner.metadata_json = journal_metadata
    c.report = ReviewReportModel(
        project_id=c.project.id,
        target_type="chapter",
        target_id=c.chapter.id,
        reviewer_type="native-three-slot-history",
        verdict="unknown" if unavailable else "rejected",
        structured_output={
            "draft_id": str(c.draft.id),
            "reviewed_body_fingerprint": pipelines.chapter_review_body_fingerprint(
                c.draft.content_md
            ),
            "evidence_summary": {
                "llm_commercial_judge": c.original_aggregate.model_dump(mode="json")
            },
            "Unknown": {"preserve": None},
            "once": ["consumed"],
        },
    )
    c.session.add(c.report)
    if mode == "retained_history":
        metadata = deepcopy(c.chapter.metadata_json)
        metadata["commercial_component_cohorts"].pop(c.cohort_key)
        c.chapter.metadata_json = metadata
    await c.session.commit()
    await c.session.refresh(c.budget)
    assert c.budget.calls_used == samples and c.budget.tokens_charged == samples * 56
    assert len(c.sent) == samples
    c.original_slots, c.normal_rendered = results, rendered
    return c


async def normal_snapshot(c):
    assert not c.session.new and not c.session.deleted and not c.session.dirty
    with c.session.no_autoflush:
        rows = {
            table.name: deepcopy(
                list(
                    (
                        await c.session.execute(
                            select(*table.columns).order_by(*table.primary_key.columns)
                        )
                    ).mappings()
                )
            )
            for table in db.ProjectModel.metadata.tables.values()
        }
    files = {
        str(path): path.read_bytes()
        for path in Path(c.settings.artifact_store.local_dir).rglob("*")
        if path.is_file()
    }
    return rows, files


async def normal_prerequisite(c):
    from bestseller.services import waiting_chapter_review_resume as waiting

    with bind_execution_scope(c.scope):
        return await waiting._normal_commercial_prerequisite(
            c.session,
            c.settings,
            project=c.project,
            chapter=c.chapter,
            draft=c.draft,
            owner=c.owner,
            scope=c.scope,
        )


async def prepare_native_historical_consumer(c, *, mode):
    """Run the real pointer-installing stage before pointer-bound consumers."""
    c.consumer_owner = c.owner
    if mode != "retained_history":
        return
    before_rows, before_files = await normal_snapshot(c)
    original_owner_id = c.owner.id
    consumer = WorkflowRunModel(
        project_id=c.project.id,
        workflow_type="chapter_pipeline",
        scope_type="chapter",
        scope_id=c.chapter.id,
        status="running",
        metadata_json={"execution_scope_id": c.scope},
    )
    c.session.add(consumer)
    await c.session.commit()
    rendered = c.normal_rendered
    with bind_execution_scope(c.scope):
        cached, request_binding = await recovery.prepare_commercial_component_slot(
            c.session,
            project_id=c.project.id,
            chapter_id=c.chapter.id,
            draft_id=c.draft.id,
            workflow_run_id=consumer.id,
            content_md=c.draft.content_md,
            sources=c.sources,
            evaluation_contract=c.contract,
            sample_slot=SLOT,
            samples=c.cohort_samples,
            retry_context=None,
            effective_critic=rendered["effective_critic"],
            retained_only=True,
            current_input_hash=rendered["current_input_hash"],
            settings=c.settings,
        )
    assert cached is not None and not cached.infrastructure_failure
    assert request_binding == {}  # Retained completion grants no dispatch preparation.
    await c.session.refresh(c.chapter)
    await c.session.refresh(consumer)
    c.consumer_owner = consumer
    assert c.owner.id == original_owner_id and consumer.id != original_owner_id
    journal = consumer.metadata_json["commercial_component_cohorts"][c.cohort_key]
    pointer = c.chapter.metadata_json["commercial_component_cohorts"][c.cohort_key]
    assert pointer == {
        "workflow_run_id": str(consumer.id),
        "binding_sha256": recovery._digest(journal["binding"]),
    }
    assert journal["historical"] is True
    assert journal["aggregate"]["report"] == c.original_aggregate.model_dump(mode="json")
    for index, original in enumerate(c.original_slots):
        assert journal["slots"][f"chapter-quality.independent-v2:{index}"]["report"] == (
            original.model_dump(mode="json")
        )
    after_rows, after_files = await normal_snapshot(c)
    assert after_files == before_files
    for table, rows in before_rows.items():
        if table not in {"chapters", "workflow_runs"}:
            assert after_rows[table] == rows, table
    assert [row for row in after_rows["workflow_runs"] if row["id"] != consumer.id] == (
        before_rows["workflow_runs"]
    )
    for old, current in zip(before_rows["chapters"], after_rows["chapters"], strict=True):
        if old["id"] != c.chapter.id:
            assert current == old
            continue
        assert {
            key: value for key, value in current.items() if key not in {"metadata", "updated_at"}
        } == {key: value for key, value in old.items() if key not in {"metadata", "updated_at"}}
        assert {
            key: value
            for key, value in current["metadata"].items()
            if key != "commercial_component_cohorts"
        } == {
            key: value
            for key, value in old["metadata"].items()
            if key != "commercial_component_cohorts"
        }
        expected_pointers = deepcopy(old["metadata"].get("commercial_component_cohorts") or {})
        expected_pointers[c.cohort_key] = pointer
        assert current["metadata"]["commercial_component_cohorts"] == expected_pointers
    assert len(c.sent) == c.cohort_samples


async def test_native_three_slot_old_cohort_has_real_payment_and_one_valid_draw(
    native, monkeypatch
):
    c = await freeze_normal_cohort(native, monkeypatch, mode="current_pointer")
    assert len({run.id for run in c.paid_runs}) == 3
    assert (
        len({run.metadata_json["execution_receipt"]["operation_key"] for run in c.paid_runs}) == 3
    )
    for run in c.paid_runs:
        for kind in ("request", "response"):
            path = Path(c.settings.artifact_store.local_dir) / getattr(run, kind + "_payload_ref")
            assert json.loads(path.read_text(encoding="utf-8"))["replayable"] is True
    assert c.original_slots[0].infrastructure_failure
    assert not c.original_slots[1].infrastructure_failure
    assert c.original_slots[2].infrastructure_failure


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
async def test_native_normal_three_slot_alias_prerequisite_replays_twice_without_writes(
    native, monkeypatch, mode
):
    c = await freeze_normal_cohort(native, monkeypatch, mode=mode)
    before = await normal_snapshot(c)
    first = await normal_prerequisite(c)
    second = await normal_prerequisite(c)
    assert first == second
    assert first["history_replays"] == 2 and first["current_protocol_eligible"] is True
    assert first["provider_calls"] == first["slots_prepared"] == first["attempts_granted"] == 0
    assert first["source_receipt"] == c.sources.receipt
    assert c.original_aggregate.infrastructure_failure
    assert json.loads(c.original_aggregate.raw_excerpt)["valid_samples"] == 1
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
@pytest.mark.parametrize("fault", ["old_aggregate", "response", "source"])
async def test_native_normal_three_slot_drift_cannot_derive_authority(
    native, monkeypatch, mode, fault
):
    c = await freeze_normal_cohort(native, monkeypatch, mode=mode)
    if fault == "old_aggregate":
        if mode == "current_pointer":
            metadata = deepcopy(c.owner.metadata_json)
            metadata["commercial_component_cohorts"][c.cohort_key]["aggregate"]["report"][
                "overall_score"
            ] = 0.99
            c.owner.metadata_json = metadata
        else:
            payload = deepcopy(c.report.structured_output)
            payload["evidence_summary"]["llm_commercial_judge"]["overall_score"] = 0.99
            c.report.structured_output = payload
    elif fault == "response":
        run = c.paid_runs[0]
        path = Path(c.settings.artifact_store.local_dir) / run.response_payload_ref
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["tool_calls"][0]["function"]["arguments"] += "\n"
        path.write_text(encode(payload), encoding="utf-8")
    else:
        fact = await c.session.get(db.CanonFactModel, UUID(c.record["id"]))
        fact.value_json = {**fact.value_json, "evidence": "另一个事件的原文。"}
    await c.session.commit()
    before = await normal_snapshot(c)
    with pytest.raises(ValueError):
        await normal_prerequisite(c)
    assert await normal_snapshot(c) == before and len(c.sent) == 3


async def test_no_alias_canonical_cached_domain_and_receipt_are_exactly_identical(native):
    c = await freeze_paid(native, canonical=True)
    before = await snapshot(c)
    result, envelope, run = await replay(c)
    assert result.model_dump(mode="json") == c.cached.model_dump(mode="json")
    assert result.raw_excerpt == c.cached.raw_excerpt
    assert (
        envelope == json.loads(c.cached.raw_excerpt)
        and "cached_source_alias_replay" not in envelope
    )
    projected, action_receipt = project_commercial_actions(
        result,
        sources=c.sources,
        gate_config=GateConfig({}),
        chapter_number=4,
    )
    assert projected is not None and action_receipt["status"] == "projected"
    assert projected["raw_excerpt"] == c.cached.raw_excerpt
    assert run is c.run and await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "saved_error",
        "saved_domain",
        "saved_adapter_status",
        "saved_adapter_version",
        "saved_adapter_source",
        "saved_adapter_raw_hash",
        "saved_adapter_submission",
        "saved_raw",
    ],
)
async def test_saved_historical_packet_tamper_fails_closed(native, fault):
    c = await freeze_paid(native)
    report = deepcopy(c.report.structured_output["slots"][SLOT])
    envelope = json.loads(report["raw_excerpt"])
    if fault == "saved_error":
        envelope["validation_errors"] = ["invented diagnostic"]
    elif fault == "saved_domain":
        report["blocking_issues"][0]["evidence"] = "invented diagnostic"
    elif fault == "saved_adapter_status":
        envelope["adapter_receipt"]["status"] = "verified"
    elif fault == "saved_adapter_version":
        envelope["adapter_receipt"]["adapter_version"] = "untrusted-adapter"
    elif fault == "saved_adapter_source":
        envelope["adapter_receipt"]["source_binding"]["source_sha256"] = "0" * 64
    elif fault == "saved_adapter_raw_hash":
        envelope["adapter_receipt"]["raw_report_sha256"] = "0" * 64
    elif fault == "saved_adapter_submission":
        envelope["adapter_receipt"]["submission"]["tool_calls_sha256"] = "0" * 64
    elif fault == "saved_raw":
        envelope["raw_report"] += "\n"
        envelope["raw_report_sha256"] = sha(envelope["raw_report"])
    report["raw_excerpt"] = encode(envelope)
    before = await snapshot(c)
    with pytest.raises(
        ValueError, match=r"commercial_cohort_(cached_domain_changed|raw_return_mismatch)"
    ):
        await replay(c, report=report)
    assert await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize(
    "fault",
    ["issues_dropped_boolean", "scores_modified_integer", "passed_integer", "zero_score_boolean"],
)
async def test_persisted_cache_type_confusion_rejects_without_coercion(native, fault):
    c = await freeze_paid(native, overall_score=0.0 if fault == "zero_score_boolean" else None)
    payload = deepcopy(c.report.structured_output)
    report = payload["slots"][SLOT]
    envelope = json.loads(report["raw_excerpt"])
    adapter = envelope["adapter_receipt"]
    if fault == "issues_dropped_boolean":
        assert type(adapter["issues_dropped"]) is int and adapter["issues_dropped"] == 0
        adapter["issues_dropped"] = False
    elif fault == "scores_modified_integer":
        assert adapter["scores_modified"] is False
        adapter["scores_modified"] = 0
    elif fault == "passed_integer":
        assert report["passed"] is False
        report["passed"] = 0
    else:
        assert type(envelope["original_overall_score"]) is float
        assert envelope["original_overall_score"] == 0.0
        envelope["original_overall_score"] = False
    report["raw_excerpt"] = encode(envelope)
    c.report.structured_output = payload
    await c.session.commit()
    await c.session.refresh(c.report)
    before = await snapshot(c)
    with pytest.raises(ValueError, match="commercial_cohort_cached_domain_changed"):
        await replay(c)
    assert await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize("fault", ["outer_extra_field", "audit_extra_field", "duplicate_member"])
async def test_persisted_full_old_envelope_must_match_native_binding(native, fault):
    c = await freeze_paid(native)
    payload = deepcopy(c.report.structured_output)
    report = payload["slots"][SLOT]
    envelope = json.loads(report["raw_excerpt"])
    if fault == "outer_extra_field":
        envelope["unverified_cache_extension"] = {"arbitrary": "must not acquire authority"}
    elif fault == "audit_extra_field":
        envelope["exact_source_audit"]["unverified_cache_extension"] = "not producer-bound"
    serialized = encode(envelope)
    if fault == "duplicate_member":
        # The duplicate has the SAME value: a normal json.loads/dict comparison
        # would erase this mutation, but the retained native packet is ambiguous.
        serialized = (
            serialized.rstrip()[:-1]
            + ', "sample_slot": '
            + json.dumps(envelope["sample_slot"])
            + "}\n"
        )
    report["raw_excerpt"] = serialized
    c.report.structured_output = payload
    await c.session.commit()
    await c.session.refresh(c.report)
    before = await snapshot(c)
    with pytest.raises(ValueError, match="commercial_cohort_cached_domain_changed"):
        await replay(c)
    assert await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "request_file",
        "response_file",
        "request_missing",
        "response_missing",
        "root",
        "transport",
        "attempt",
        "source_body",
        "source_record",
        "source_catalog",
    ],
)
async def test_native_physical_ledger_and_source_drift_cannot_replay_alias(native, fault):
    c = await freeze_paid(native)
    sources = c.sources
    if fault in {"request_file", "response_file", "request_missing", "response_missing"}:
        kind = fault.split("_", 1)[0]
        path = Path(c.settings.artifact_store.local_dir) / getattr(c.run, kind + "_payload_ref")
        if fault.endswith("missing"):
            path.unlink()
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
            value["llm_run_id"] = str(uuid4())
            path.write_text(encode(value), encoding="utf-8")
    elif fault in {"root", "transport"}:
        operation = c.root if fault == "root" else c.transport
        value = deepcopy(operation.result_json)
        paid = value if fault == "root" else value["result"]
        paid["tool_calls"][0]["function"]["arguments"] += "\n"
        operation.result_json = value
    elif fault == "attempt":
        c.attempt.input_hash = "0" * 64
    elif fault == "source_body":
        sources = source_packet(c.record, body=BODY + "他离开了。")
    elif fault == "source_record":
        record = {**c.record, "evidence": "不同事件的原文。"}
        sources = source_packet(record)
    elif fault == "source_catalog":
        catalog = deepcopy(dict(c.sources.catalog))
        catalog[c.catalog_id]["text"] = "不同事件的原文。"
        sources = replace(c.sources, catalog=catalog)
    await c.session.commit()
    before = await snapshot(c)
    expected = FileNotFoundError if fault == "request_missing" else ValueError
    pattern = (
        None
        if fault == "request_missing"
        else (r"commercial_cohort_|retained_review_input|commercial_review_transport:")
    )
    with pytest.raises(expected, match=pattern):
        await replay(c, sources=sources)
    assert await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize("fault", ["no_alias", "ambiguous_alias"])
@pytest.mark.parametrize("tamper", [None, "saved_error", "saved_domain"])
async def test_unresolved_or_ambiguous_old_record_alias_has_no_derived_authority(
    native, fault, tamper
):
    records = [deepcopy(native.record)]
    if fault == "no_alias":
        records[0]["id"] = str(uuid4())
    else:
        records.append({**native.record, "evidence": "另一个事件使用了相同记录编号。"})
    c = await freeze_paid(native, records=records)
    _, adapter, errors = wire.parse_commercial_wire(c.raw, c.sources)
    assert errors and "derived_source_record_aliases" not in adapter
    before = await snapshot(c)
    if tamper is None:
        result, envelope, run = await replay(c)
        assert result.model_dump(mode="json") == c.cached.model_dump(mode="json")
        assert result.infrastructure_failure and result.raw_excerpt == c.cached.raw_excerpt
        assert envelope == json.loads(c.cached.raw_excerpt)
        assert "cached_source_alias_replay" not in envelope and run is c.run
    else:
        report = deepcopy(c.report.structured_output["slots"][SLOT])
        if tamper == "saved_error":
            envelope = json.loads(report["raw_excerpt"])
            envelope["validation_errors"] = ["invented diagnostic"]
            report["raw_excerpt"] = encode(envelope)
        else:
            report["blocking_issues"][0]["evidence"] = "invented diagnostic"
        with pytest.raises(ValueError, match="commercial_cohort_cached_domain_changed"):
            await replay(c, report=report)
    assert await snapshot(c) == before and len(c.sent) == 1


def normal_alias_inputs(c):
    """Actual context arguments consumed by the native waiting renderer."""
    return {
        "chapter_number": c.chapter.chapter_number,
        "generation_input": c.normal_generation,
        "previous_chapters": c.normal_previous,
        "workflow_run_id": getattr(c, "consumer_owner", c.owner).id,
        "pack": c.normal_pack,
        "genre_context": c.normal_genre,
        "sample_slot": SLOT,
        "commercial_cohort_samples": c.cohort_samples,
    }


async def derive_normal_cohort(c):
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

    original = deepcopy(c.owner.metadata_json["commercial_component_cohorts"][c.cohort_key])
    derived = await recovery._verified_source_alias_cohort_view(
        c.session,
        c.settings,
        journal=original,
        sources=c.sources,
        project_id=c.project.id,
        scope=c.scope,
        expected_model=MODEL,
    )
    assert isinstance(derived, dict)
    assert original == c.owner.metadata_json["commercial_component_cohorts"][c.cohort_key]
    proof = derived["source_alias_derivation"]
    assert proof["changed_slots"] == [SLOT]
    assert proof["legacy_aggregate_sha256"] == recovery._digest(original["aggregate"]["report"])
    assert all(
        proof[key] is False
        for key in (
            "paid_raw_modified",
            "history_rewritten",
            "new_review_credit",
            "dispatch_authorized",
            "adoption_authorized",
        )
    )
    assert len(set(proof["paid_run_ids"])) == len(set(proof["paid_operation_keys"])) == 3
    for index in (1, 2):
        slot = f"chapter-quality.independent-v2:{index}"
        assert derived["slots"][slot] == original["slots"][slot]
    derived_envelope = json.loads(derived["slots"][SLOT]["report"]["raw_excerpt"])
    old_envelope = json.loads(original["slots"][SLOT]["report"]["raw_excerpt"])
    assert derived_envelope["raw_report"] == old_envelope["raw_report"]
    assert derived_envelope["raw_report_sha256"] == old_envelope["raw_report_sha256"]
    result = LLMQualityJudgeResult.model_validate(derived["aggregate"]["report"])
    receipt = json.loads(result.raw_excerpt)
    assert receipt["status"] == "aggregated"
    assert (
        receipt["valid_samples"]
        == sum(not slot.infrastructure_failure for slot in c.original_slots) + 1
    )
    assert [sample["valid"] for sample in receipt["samples"]] == [
        True,
        True,
        not c.original_slots[2].infrastructure_failure,
    ]
    assert not result.infrastructure_failure
    return derived, result


def derived_commercial_evidence(c, result):
    from bestseller.services.quality_gates_config import get_quality_gates_config

    projected, receipt = project_commercial_actions(
        result,
        sources=c.sources,
        gate_config=get_quality_gates_config().l6_gate,
        chapter_number=c.chapter.chapter_number,
    )
    assert projected is not None and receipt["status"] == "projected"
    unknown = [
        f"chapter-quality.independent-v2:{index}"
        for index, slot in enumerate(c.original_slots)
        if index != 0 and slot.infrastructure_failure
    ]
    assert receipt["unknown_sample_slots"] == unknown
    assert "source_alias_capability" in receipt
    assert receipt["issue_actions"]
    assert {
        origin["sample_slot"] for action in receipt["issue_actions"] for origin in action["lineage"]
    } == {f"chapter-quality.independent-v2:{index}" for index in range(3)} - set(unknown)
    return {
        "commercial_action_projection": receipt,
        "llm_commercial_judge": projected,
        "llm_commercial_judge_original": result.model_dump(mode="json"),
    }


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
async def test_native_binder_and_fact_consumers_accept_only_derived_three_slot_view(
    native, monkeypatch, mode
):
    from bestseller.services import commercial_action_projection as projection
    from bestseller.services.chapter_draft_candidate_eligibility import _verified_commercial_actions
    from bestseller.services.chapter_verified_fact_disposition import _quorum_actions

    c = await freeze_normal_cohort(native, monkeypatch, mode=mode)
    await prepare_native_historical_consumer(c, mode=mode)
    before = await normal_snapshot(c)
    _, result = await derive_normal_cohort(c)
    with bind_execution_scope(c.scope):
        bound = await recovery.bind_commercial_component_aggregate(
            c.session,
            project_id=c.project.id,
            chapter_id=c.chapter.id,
            content_md=c.draft.content_md,
            result=result,
            settings=c.settings,
            source_alias_inputs=normal_alias_inputs(c),
        )
    assert bound.model_dump(mode="json") == result.model_dump(mode="json")
    evidence = derived_commercial_evidence(c, bound)
    original_evidence = deepcopy(evidence)
    recovered = projection.resolve_source_record_alias_capability(
        evidence["commercial_action_projection"],
        body_sha256=sha(c.draft.content_md),
    )
    assert recovered == c.sources
    expected_actions = evidence["commercial_action_projection"]["issue_actions"]
    assert _quorum_actions(evidence, sha(c.draft.content_md)) == expected_actions
    # The exact paid length-neutral receipt can accompany a native majority;
    # it remains unavailable and contributes no action or acceptance authority.
    assert evidence["commercial_action_projection"]["unknown_sample_slots"] == [
        "chapter-quality.independent-v2:2"
    ]
    unknown = evidence["commercial_action_projection"]["slots"][-1]
    assert unknown["adapter_receipt"]["status"] == "not_run_output_truncated"
    assert unknown["abstains"] is True
    assert all(type(unknown[key]) is int and unknown[key] == 0 for key in (
        "domain_issues_contributed", "actions_contributed", "scores_contributed"))
    assert _verified_commercial_actions(
        (evidence,), {"digest": sha(c.draft.content_md)}
    ) == expected_actions
    assert all(origin["sample_slot"] != unknown["sample_slot"]
               for action in expected_actions for origin in action["lineage"])
    assert evidence == original_evidence
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
async def test_native_retained_commercial_proof_consumes_derived_view_without_rewriting_old_report(
    native, monkeypatch, mode
):
    from bestseller.services import retained_review_input_proof as retained

    c = await freeze_normal_cohort(native, monkeypatch, mode=mode)
    await prepare_native_historical_consumer(c, mode=mode)
    before = await normal_snapshot(c)
    _, result = await derive_normal_cohort(c)
    evidence = derived_commercial_evidence(c, result)
    # This detached native report models the current caller's derived view.
    # The original persisted unavailable report remains byte-for-byte intact.
    report = ReviewReportModel(
        id=c.report.id,
        project_id=c.project.id,
        target_type="chapter",
        target_id=c.chapter.id,
        reviewer_type=c.report.reviewer_type,
        verdict=c.report.verdict,
        structured_output={**deepcopy(c.report.structured_output), "evidence_summary": evidence},
    )
    catalog, _ = retained._fresh_catalog()
    proof = await retained._commercial_proof(
        c.session,
        c.settings,
        project=c.project,
        chapter=c.chapter,
        draft=c.draft,
        report=report,
        catalog=catalog,
        scope=c.scope,
        commercial_sources=c.sources,
    )
    assert isinstance(proof, dict)
    assert set(proof["llm_run_ids"]) == {str(run.id) for run in c.paid_runs}
    assert proof["owner_workflow_run_id"] == str(c.consumer_owner.id)
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
async def test_native_old_canonical_cohort_needs_no_alias_view_and_remains_exact(
    native, monkeypatch, mode
):
    c = await freeze_normal_cohort(native, monkeypatch, mode=mode, alias=False)
    before = await normal_snapshot(c)
    original = deepcopy(c.owner.metadata_json["commercial_component_cohorts"][c.cohort_key])
    derived = await recovery._verified_source_alias_cohort_view(
        c.session,
        c.settings,
        journal=original,
        sources=c.sources,
        project_id=c.project.id,
        scope=c.scope,
        expected_model=MODEL,
    )
    assert derived is None
    for index, cached in enumerate(c.original_slots):
        result, envelope, run = await recovery._verified_paid_slot(
            c.session,
            report=cached.model_dump(mode="json"),
            sources=c.sources,
            contract=c.contract,
            project_id=c.project.id,
            scope=c.scope,
            slot=f"chapter-quality.independent-v2:{index}",
            expected_model=MODEL,
            settings=c.settings,
        )
        assert result.model_dump(mode="json") == cached.model_dump(mode="json")
        assert result.raw_excerpt == cached.raw_excerpt
        assert envelope == json.loads(cached.raw_excerpt)
        assert "cached_source_alias_replay" not in envelope
        assert run.id == c.paid_runs[index].id
    first, second = await normal_prerequisite(c), await normal_prerequisite(c)
    assert first == second and first["history_replays"] == 2
    projected, receipt = project_commercial_actions(
        c.original_aggregate,
        sources=c.sources,
        gate_config=GateConfig({}),
        chapter_number=4,
    )
    assert projected is not None and receipt["status"] == "projected"
    assert "source_alias_capability" not in receipt
    assert projected["raw_excerpt"] == c.original_aggregate.raw_excerpt
    if mode == "current_pointer":
        bound = await recovery.bind_commercial_component_aggregate(
            c.session,
            project_id=c.project.id,
            chapter_id=c.chapter.id,
            content_md=c.draft.content_md,
            result=c.original_aggregate,
        )
        assert bound.model_dump(mode="json") == c.original_aggregate.model_dump(mode="json")
        assert bound.raw_excerpt == c.original_aggregate.raw_excerpt
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize("consumer", ["quorum", "all_answered"])
@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "null",
        "scalar",
        "extra_field",
        "body_drift",
        "source_text",
        "source_catalog",
        "receipt_type",
        "alias_pointer",
        "alias_schema",
        "alias_issue_path",
    ],
)
async def test_native_alias_projection_capability_tamper_cannot_feed_fact_consumers(
    native, monkeypatch, fault, consumer
):
    from bestseller.services import commercial_action_projection as projection
    from bestseller.services.chapter_draft_candidate_eligibility import _verified_commercial_actions
    from bestseller.services.chapter_verified_fact_disposition import _quorum_actions

    c = await freeze_normal_cohort(
        native,
        monkeypatch,
        mode="current_pointer",
        truncate=consumer == "quorum",
    )
    before = await normal_snapshot(c)
    _, result = await derive_normal_cohort(c)
    evidence = derived_commercial_evidence(c, result)
    proof = evidence["commercial_action_projection"]
    body_sha = sha(c.draft.content_md)
    if consumer == "quorum":
        assert _quorum_actions(evidence, body_sha) == proof["issue_actions"]
    else:
        assert (
            _verified_commercial_actions(
                (evidence,),
                {"digest": body_sha},
            )
            == proof["issue_actions"]
        )
    capability = proof["source_alias_capability"]
    assert set(capability) == {
        "schema",
        "source_text",
        "source_receipt",
        "source_catalog",
        "packet_sha256",
        "catalog_sha256",
        "aliases_by_slot",
        "alias_reports",
        "physical_authentication",
        "new_credit",
        "dispatch_authorized",
        "adoption_authorized",
        "capability_sha256",
    }
    native_sample = json.loads(result.raw_excerpt)["samples"][0]["report"]
    assert capability["alias_reports"] == {SLOT: native_sample}
    assert json.loads(native_sample["raw_excerpt"])["raw_report"] == c.raws[SLOT]
    if fault == "missing":
        del proof["source_alias_capability"]
    elif fault == "null":
        proof["source_alias_capability"] = None
    elif fault == "scalar":
        proof["source_alias_capability"] = "untrusted self assertion"
    elif fault == "extra_field":
        proof["source_alias_capability"]["unverified_extension"] = "must not acquire authority"
    elif fault == "body_drift":
        body_sha = sha(c.draft.content_md + "另一个事件。")
    else:
        if fault == "source_text":
            capability["source_text"] += "\n"
        elif fault == "source_catalog":
            capability["source_catalog"][c.catalog_id]["text"] = "不同事件的原文。"
        elif fault == "receipt_type":
            assert type(capability["source_receipt"]["catalog_entries"]) is int
            capability["source_receipt"]["catalog_entries"] = True
        elif fault == "alias_pointer":
            capability["aliases_by_slot"][SLOT][0]["record_pointer"] = (
                "#/source_context/adopted_continuity/source_bound_canon/99"
            )
        elif fault == "alias_issue_path":
            assert capability["aliases_by_slot"][SLOT][0]["path"] == "/issues/0/source_id"
            capability["aliases_by_slot"][SLOT][0]["path"] = "/issues/99/source_id"
        else:
            capability["aliases_by_slot"][SLOT][0]["schema"] = "unverified-source-alias.v1"
        # Preserve self-consistent transport checksums so these cases reach
        # the native source/receipt/alias reconstruction, rather than merely
        # detecting an outdated outer checksum.
        if fault == "source_catalog":
            capability["catalog_sha256"] = sha(
                json.dumps(
                    capability["source_catalog"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        capability["capability_sha256"] = sha(
            json.dumps(
                {key: value for key, value in capability.items() if key != "capability_sha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    mutated = deepcopy(evidence)
    if fault == "missing":
        assert (
            projection.resolve_source_record_alias_capability(proof, body_sha256=body_sha) is None
        )
    else:
        with pytest.raises(ValueError):
            projection.resolve_source_record_alias_capability(proof, body_sha256=body_sha)
    if consumer == "quorum":
        with pytest.raises(ValueError):
            _quorum_actions(evidence, body_sha)
    else:
        with pytest.raises(ValueError):
            _verified_commercial_actions((evidence,), {"digest": body_sha})
    assert evidence == mutated
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
async def test_native_all_answered_alias_eligibility_keeps_full_native_actions(
    native, monkeypatch, mode
):
    from bestseller.services.chapter_draft_candidate_eligibility import _verified_commercial_actions

    c = await freeze_normal_cohort(native, monkeypatch, mode=mode, truncate=False)
    before = await normal_snapshot(c)
    _, result = await derive_normal_cohort(c)
    evidence = derived_commercial_evidence(c, result)
    original = deepcopy(evidence)
    proof = evidence["commercial_action_projection"]
    assert proof["unknown_sample_slots"] == []
    assert (
        _verified_commercial_actions(
            (evidence,),
            {"digest": sha(c.draft.content_md)},
        )
        == proof["issue_actions"]
    )
    assert evidence == original
    assert await normal_snapshot(c) == before and len(c.sent) == 3


async def test_native_canonical_literal_replay_name_in_reason_never_selects_alias_binder(
    native, monkeypatch
):
    literal = "The model mentions cached_source_alias_replay as ordinary prose, without authority."
    c = await freeze_normal_cohort(
        native,
        monkeypatch,
        mode="current_pointer",
        alias=False,
        literal_reason=literal,
    )
    before = await normal_snapshot(c)
    assert "cached_source_alias_replay" in c.original_aggregate.raw_excerpt
    assert recovery._source_alias_cached_aggregate(c.original_aggregate) is False
    for slot in (SLOT, "chapter-quality.independent-v2:1"):
        assert json.loads(c.raws[slot])["issues"][0]["reason"] == literal
    bound = await recovery.bind_commercial_component_aggregate(
        c.session,
        project_id=c.project.id,
        chapter_id=c.chapter.id,
        content_md=c.draft.content_md,
        result=c.original_aggregate,
    )
    assert bound.model_dump(mode="json") == c.original_aggregate.model_dump(mode="json")
    assert bound.raw_excerpt == c.original_aggregate.raw_excerpt
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "nested_record",
        "wrong_replay_schema",
        "wrong_slot_schema",
        "aggregate_top_level",
        "sample_metadata",
        "report_is_string",
        "duplicate_slot_member",
    ],
)
async def test_native_alias_binder_predicate_ignores_wrong_nested_record_or_schema(native, fault):
    c = await freeze_paid(native, canonical=True)
    before = await snapshot(c)
    envelope = json.loads(c.cached.raw_excerpt)
    marker = {"schema": "commercial-cached-source-alias-replay.v1"}
    if fault == "nested_record":
        envelope["unrelated"] = {"cached_source_alias_replay": marker}
    elif fault == "wrong_replay_schema":
        envelope["cached_source_alias_replay"] = {"schema": "untrusted-replay.v1"}
    elif fault == "wrong_slot_schema":
        envelope["schema_version"] = "untrusted-slot.v1"
        envelope["cached_source_alias_replay"] = marker
    elif fault == "duplicate_slot_member":
        envelope["cached_source_alias_replay"] = marker
    sample = {"report": {"raw_excerpt": encode(envelope)}}
    if fault in {"aggregate_top_level", "sample_metadata", "report_is_string"}:
        envelope = {"schema_version": "chapter-quality-aggregation.v1", "samples": [sample]}
        if fault == "aggregate_top_level":
            envelope["cached_source_alias_replay"] = marker
        elif fault == "sample_metadata":
            sample["cached_source_alias_replay"] = marker
        else:
            sample["report"] = encode({"cached_source_alias_replay": marker})
    raw = encode(envelope)
    if fault == "duplicate_slot_member":
        raw = raw.rstrip()[:-1] + ', "sample_slot": ' + json.dumps(SLOT) + "}\n"
    result = c.cached.model_copy(update={"raw_excerpt": raw})
    assert recovery._source_alias_cached_aggregate(result) is False
    assert await snapshot(c) == before and len(c.sent) == 1


async def test_native_single_slot_journal_view_and_binder_keep_original_slot_receipt(
    native, monkeypatch
):
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

    c = await freeze_normal_cohort(
        native,
        monkeypatch,
        mode="current_pointer",
        samples=1,
        truncate=False,
    )
    before = await normal_snapshot(c)
    original = deepcopy(c.owner.metadata_json["commercial_component_cohorts"][c.cohort_key])
    old_envelope = json.loads(c.original_aggregate.raw_excerpt)
    assert old_envelope["schema_version"] == recovery.SLOT_RECEIPT_SCHEMA
    assert c.original_aggregate.infrastructure_failure
    view = await recovery._verified_source_alias_cohort_view(
        c.session,
        c.settings,
        journal=original,
        sources=c.sources,
        project_id=c.project.id,
        scope=c.scope,
        expected_model=MODEL,
    )
    assert isinstance(view, dict) and set(view["slots"]) == {SLOT}
    result = LLMQualityJudgeResult.model_validate(view["aggregate"]["report"])
    assert not result.infrastructure_failure
    envelope = json.loads(result.raw_excerpt)
    assert envelope["schema_version"] == recovery.SLOT_RECEIPT_SCHEMA
    assert envelope["raw_report"] == old_envelope["raw_report"]
    assert envelope["raw_report_sha256"] == old_envelope["raw_report_sha256"]
    assert (
        envelope["cached_source_alias_replay"]["schema"]
        == "commercial-cached-source-alias-replay.v1"
    )
    assert recovery._source_alias_cached_aggregate(result) is True
    with bind_execution_scope(c.scope):
        bound = await recovery.bind_commercial_component_aggregate(
            c.session,
            project_id=c.project.id,
            chapter_id=c.chapter.id,
            content_md=c.draft.content_md,
            result=result,
            settings=c.settings,
            source_alias_inputs=normal_alias_inputs(c),
        )
    assert bound.model_dump(mode="json") == result.model_dump(mode="json")
    assert bound.raw_excerpt == result.raw_excerpt
    assert original == c.owner.metadata_json["commercial_component_cohorts"][c.cohort_key]
    assert await normal_snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize("mode", ["current_pointer", "retained_history"])
async def test_native_canonical_quorum_length_retained_proof_needs_no_capability(
    native, monkeypatch, mode
):
    from bestseller.services import retained_review_input_proof as retained
    from bestseller.services.quality_gates_config import get_quality_gates_config

    c = await freeze_normal_cohort(
        native,
        monkeypatch,
        mode=mode,
        alias=False,
        truncate=True,
    )
    await prepare_native_historical_consumer(c, mode=mode)
    before = await normal_snapshot(c)
    aggregate = c.original_aggregate
    receipt = json.loads(aggregate.raw_excerpt)
    assert not aggregate.infrastructure_failure and receipt["valid_samples"] == 2
    assert [sample["valid"] for sample in receipt["samples"]] == [True, True, False]
    assert c.paid_runs[2].finish_reason == "length"
    projected, projection = project_commercial_actions(
        aggregate,
        sources=c.sources,
        gate_config=get_quality_gates_config().l6_gate,
        chapter_number=c.chapter.chapter_number,
    )
    assert projected is not None and projection["status"] == "projected"
    assert "source_alias_capability" not in projection
    assert projection["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    assert all(
        origin["sample_slot"] != "chapter-quality.independent-v2:2"
        for action in projection["issue_actions"]
        for origin in action["lineage"]
    )
    report = ReviewReportModel(
        id=c.report.id,
        project_id=c.project.id,
        target_type="chapter",
        target_id=c.chapter.id,
        reviewer_type=c.report.reviewer_type,
        verdict=c.report.verdict,
        structured_output={
            **deepcopy(c.report.structured_output),
            "evidence_summary": {
                "commercial_action_projection": projection,
                "llm_commercial_judge": projected,
                "llm_commercial_judge_original": aggregate.model_dump(mode="json"),
            },
        },
    )
    catalog, _ = retained._fresh_catalog()
    proof = await retained._commercial_proof(
        c.session,
        c.settings,
        project=c.project,
        chapter=c.chapter,
        draft=c.draft,
        report=report,
        catalog=catalog,
        scope=c.scope,
        commercial_sources=c.sources,
    )
    assert isinstance(proof, dict)
    assert set(proof["llm_run_ids"]) == {str(run.id) for run in c.paid_runs}
    assert len(proof["llm_run_ids"]) == 3
    assert proof["owner_workflow_run_id"] == str(c.consumer_owner.id)
    assert await normal_snapshot(c) == before and len(c.sent) == 3


@pytest.mark.parametrize("fault", ["length_domain", "length_adapter"])
async def test_native_canonical_retained_length_tamper_rejects_real_paid_return(
    native, monkeypatch, fault
):
    from bestseller.services import retained_review_input_proof as retained

    c = await freeze_normal_cohort(
        native,
        monkeypatch,
        mode="current_pointer",
        alias=False,
        truncate=True,
    )
    slot = "chapter-quality.independent-v2:2"
    metadata = deepcopy(c.owner.metadata_json)
    journal = metadata["commercial_component_cohorts"][c.cohort_key]
    stored = journal["slots"][slot]["report"]
    envelope = json.loads(stored["raw_excerpt"])
    assert envelope["adapter_receipt"]["status"] == "not_run_output_truncated"
    if fault == "length_domain":
        assert stored["overall_score"] == envelope["original_overall_score"] == 0.0
        stored["overall_score"] = 0.99
        envelope["original_overall_score"] = 0.99
    else:
        envelope["adapter_receipt"]["scores_modified"] = True
    stored["raw_excerpt"] = encode(envelope)
    aggregate = journal["aggregate"]["report"]
    receipt = json.loads(aggregate["raw_excerpt"])
    assert receipt["samples"][2]["sample_slot"] == slot
    receipt["samples"][2]["report"] = deepcopy(stored)
    aggregate["raw_excerpt"] = encode(receipt)
    journal["aggregate"]["slots_sha256"] = recovery._digest(
        {name: entry["report"] for name, entry in journal["slots"].items()}
    )
    c.owner.metadata_json = metadata
    payload = deepcopy(c.report.structured_output)
    payload["evidence_summary"]["llm_commercial_judge"] = deepcopy(aggregate)
    c.report.structured_output = payload
    await c.session.commit()
    before = await normal_snapshot(c)
    paid_run = c.paid_runs[2]
    paid, _ = await retained._physical_return(
        c.session,
        paid_run,
        scope=c.scope,
        expected_hash=paid_run.prompt_hash,
        expected_model=MODEL,
    )
    assert paid.finish_reason == "length" and not paid.tool_calls
    assert paid.model_name == MODEL and not paid.fallback_used
    catalog, _ = retained._fresh_catalog()
    with pytest.raises(ValueError):
        await retained._commercial_proof(
            c.session,
            c.settings,
            project=c.project,
            chapter=c.chapter,
            draft=c.draft,
            report=c.report,
            catalog=catalog,
            scope=c.scope,
            commercial_sources=c.sources,
        )
    assert await normal_snapshot(c) == before and len(c.sent) == 3

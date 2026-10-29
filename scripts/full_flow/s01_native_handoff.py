"""CP1 native same-book successor, isolated copied DB, real SDK/offline HTTP only."""

# ruff: noqa: E402 -- network audit hook must precede application imports.
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import sys
from uuid import UUID, uuid4

from support.cp1_rehearsal import (
    RunState,
    all_snapshots,
    budget_rows,
    comparison_session,
    table_snapshot,
    verify_budgets,
    verify_old_rows,
)

STATE = RunState(Path(os.environ["FULL_FLOW_S01_EVIDENCE"]) / "RESULT.json")
_original_excepthook = sys.excepthook


def persist_uncaught(kind, error, tb):
    STATE.failure(error.with_traceback(tb), step="uncaught_entrypoint")
    STATE.finish()
    _original_excepthook(kind, error, tb)


sys.excepthook = persist_uncaught

from urllib.parse import urlsplit

HOST = urlsplit(os.environ["BESTSELLER__DATABASE__URL"]).hostname
assert HOST.startswith("int-full-flow-")
try:
    ips = {row[4][0] for row in socket.getaddrinfo(HOST, None, socket.AF_INET)}
except Exception as error:
    STATE.failure(error, step="resolve_isolated_pg")
    STATE.finish()
    raise


def network_guard(event, args):
    if event == "socket.getaddrinfo" and args[0] not in {HOST, *ips}:
        raise RuntimeError("cp1_external_dns_denied")
    if event == "socket.connect" and (
        not isinstance(args[1], tuple) or args[1][0] not in ips or args[1][1] != 5432
    ):
        raise RuntimeError("cp1_external_connection_denied")


sys.addaudithook(network_guard)

import httpx
import jsonschema
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from bestseller.infra.db import models as m
from bestseller.settings import load_settings
from support.database_safety import install_database_safety, selected_database_url

OUT = Path(os.environ["FULL_FLOW_S01_EVIDENCE"])
IDS = dict(
    project_id=UUID("d407ca85-9a9b-4564-a033-6bc529e0dd76"),
    chapter_id=UUID("0bf75fb2-20c8-4239-95d3-a94ed7ab9678"),
    draft_id=UUID("da40bde2-9f60-47d7-bf12-22a59efc0666"),
)
OLD_CYCLE = "7d84c773c45db019a6f3e3232842a8c4eb489922d46136f93ed909464e0d0887"
OLD_REPORT = UUID("6eb05f3f-bb8b-4d5c-b068-ea3459e391a6")
RUN_IDS = {
    "c2a879a7-f5a6-4b5a-b285-b2fe78238cbd",
    "62305106-6cae-4daa-af22-92e47d026050",
    "c1fe735e-da01-47ec-9aca-9813f2c2f8ef",
    "12e88302-5ba4-470e-9aeb-48026d79cb53",
    "fdd70d2f-f881-4d1b-bc37-76872fda1ac4",
}
ORIGINAL_DB = "full_flow_s01_20261028_test_db"
suffix = uuid4().hex
CONTROL_DB = "full_flow_s01_control_" + suffix + "_test"
TEST_DB = "full_flow_s01_" + suffix + "_test"
for name in (CONTROL_DB, TEST_DB):
    assert re.fullmatch(r"full_flow_s01_(?:control_)?[a-f0-9]{32}_test", name)


def encoded(x):
    return json.dumps(
        x, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    ).encode()


def sha(x):
    return hashlib.sha256(x).hexdigest()


def summary(x):
    return {
        "type": type(x).__name__,
        "length": len(x) if isinstance(x, (list, dict, str, bytes)) else None,
        "sha256": sha(encoded(x)),
    }


def need(ok, field, expected, actual):
    if not ok:
        err = ValueError("cp1:" + field)
        err.differences = [
            {"field": field, "expected": summary(expected), "actual": summary(actual)}
        ]
        raise err


class ScriptedWire(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body


class SDKScript:
    def __init__(self):
        self.calls = []
        self.body_id = None

    def response(self, body):
        tools = body.get("tools") or []
        name = tools[0]["function"]["name"] if tools else None
        if name == "submit_chapter_critique":
            props = tools[0]["function"]["parameters"]["properties"]
            payload = {
                "verdict": "rewrite",
                "scores": {key: 0.65 for key in props["scores"]["properties"]},
                "rewrite_direction": "Scripted integration proposal: refine narrative presentation in a separate edit.",
                "commentary": "Scripted SDK integration fixture; reader quality acceptance has not been performed.",
                "citations": [
                    {
                        "paragraph_number": props["citations"]["items"]["properties"][
                            "paragraph_number"
                        ]["enum"][0],
                        "dimension": "coherence",
                        "reason": "Scripted source-bound integration observation.",
                    }
                ],
            }
            family = "primary"
        elif name == "submit_chapter_review":
            from bestseller.services.commercial_review_wire import WIRE_VERSION

            props = tools[0]["function"]["parameters"]["properties"]
            payload = {
                "wire_version": WIRE_VERSION,
                "pass": True,
                "overall_score": 0.9,
                "dimension_scores": {key: 0.9 for key in props["dimension_scores"]["properties"]},
                "issues": [],
                "rewrite_plan": {"scope": "none", "preserve": [], "change": [], "instructions": ""},
            }
            if "checks" in props:
                payload["checks"] = {
                    key: {
                        "result": "PASS",
                        "body_id": self.body_id,
                        "reason": "Scripted integration check.",
                    }
                    for key in props["checks"]["properties"]
                }
            family = "commercial"
        else:
            messages = body.get("messages") or []
            user = "\n".join(
                str(row.get("content") or "") for row in messages if row.get("role") == "user"
            )
            need(
                name is None and "## 章节窗口" in user,
                "sdk_expected_template",
                "window JSON prompt or known review tool",
                {"tool": name, "prompt": summary(user)},
            )
            payload = {
                "pass": True,
                "overall_score": 0.9,
                "dimension_scores": {"material_advancement_score": 0.9},
                "blocking_issues": [],
                "audit_issues": [],
                "rewrite_plan": {},
            }
            family = "window"
        if tools:
            jsonschema.validate(payload, tools[0]["function"]["parameters"])
        return family, name, payload

    async def transport(self, transport, request):
        need(
            request.url.host == "api.minimaxi.com",
            "sdk_endpoint",
            "api.minimaxi.com",
            request.url.host,
        )
        body = json.loads(request.content)
        family, name, payload = self.response(body)
        number = len(self.calls) + 1
        need(number <= 5, "sdk_call_limit", 5, number)
        self.calls.append(
            {
                "number": number,
                "family": family,
                "tool": name,
                "stream": body.get("stream", False),
                "model": body.get("model"),
                "request_sha256": sha(request.content),
                "response_payload_sha256": sha(encoded(payload)),
            }
        )
        directory = OUT / "sdk-script"
        directory.mkdir(exist_ok=True)
        (directory / f"{number:02d}-request.json").write_bytes(request.content)
        message = {
            "role": "assistant",
            "content": None if name else json.dumps(payload, ensure_ascii=False),
        }
        if name:
            message["tool_calls"] = [
                {
                    "id": f"cp1-script-tool-{number}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(payload, ensure_ascii=False),
                    },
                }
            ]
        finish = "tool_calls" if name else "stop"
        envelope = {
            "id": f"cp1-scripted-{number}",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        }
        (directory / f"{number:02d}-response.json").write_text(
            json.dumps(envelope, ensure_ascii=False)
        )
        (OUT / "SDK-CALLS.json").write_text(
            json.dumps(self.calls, ensure_ascii=False, indent=2) + "\n"
        )
        STATE.checkpoint(
            "sdk_return_" + str(number), script_consumed_index=number, sdk_calls=self.calls
        )
        if body.get("stream"):
            delta = {**message}
            if name:
                delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]

            def chunk(delta=None, finish_reason=None, usage=None):
                return (
                    "data: "
                    + json.dumps(
                        {
                            "id": envelope["id"],
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": body["model"],
                            "choices": []
                            if usage
                            else [
                                {"index": 0, "delta": delta or {}, "finish_reason": finish_reason}
                            ],
                            **({"usage": usage} if usage else {}),
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                ).encode()

            wire = (
                chunk(delta)
                + chunk(finish_reason=finish)
                + chunk(usage=envelope["usage"])
                + b"data: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                stream=ScriptedWire(wire),
            )
        return httpx.Response(200, request=request, json=envelope)


async def main():
    need(
        selected_database_url().database == ORIGINAL_DB,
        "initial_database",
        ORIGINAL_DB,
        selected_database_url().database,
    )
    cleanup = install_database_safety()
    original_url = make_url(load_settings().database.url)
    script = SDKScript()
    engines = []
    original_before = None
    scope = None
    factory = None
    source_connection = None
    compare_connection = None
    shared_started = False
    observer = None
    caught_logger = None
    original_transport = httpx.AsyncHTTPTransport.handle_async_request
    STATE.data.update(
        schema="full-flow-s01-native-handoff.v1",
        at_utc=datetime.now(timezone.utc).isoformat(),
        production_access=False,
        external_provider_calls=0,
        test_database=TEST_DB,
        control_database=CONTROL_DB,
        full_http_S01_complete=False,
        shared_issuer_view_verified=False,
        simulation="Native pipeline/gateway/ledger/SDK; only HTTP response transport scripted; no quality acceptance claim",
    )
    STATE.checkpoint("source_and_clone_setup")
    try:
        source = create_async_engine(original_url, isolation_level="AUTOCOMMIT")
        engines.append(source)
        async with source.connect() as c:
            need(
                await c.scalar(text("SELECT current_database()")) == ORIGINAL_DB,
                "actual_source_db",
                ORIGINAL_DB,
                await c.scalar(text("SELECT current_database()")),
            )
            await c.execute(text(f'CREATE DATABASE "{CONTROL_DB}" TEMPLATE template0'))
        await source.dispose()
        os.environ["BESTSELLER__DATABASE__URL"] = original_url.set(
            database=CONTROL_DB
        ).render_as_string(hide_password=False)
        control = create_async_engine(
            os.environ["BESTSELLER__DATABASE__URL"], isolation_level="AUTOCOMMIT"
        )
        engines.append(control)
        async with control.connect() as c:
            need(
                await c.scalar(text("SELECT current_database()")) == CONTROL_DB,
                "actual_control_db",
                CONTROL_DB,
                await c.scalar(text("SELECT current_database()")),
            )
            await c.execute(text(f'CREATE DATABASE "{TEST_DB}" TEMPLATE "{ORIGINAL_DB}"'))
        await control.dispose()
        os.environ["BESTSELLER__DATABASE__URL"] = original_url.render_as_string(hide_password=False)
        source = create_async_engine(original_url)
        engines.append(source)
        source_connection = await source.connect()
        await comparison_session(source_connection)
        original_before = await all_snapshots(source_connection)
        STATE.checkpoint(
            "source_comparison_baseline",
            source_table_counts={t: len(v["rows"]) for t, v in original_before.items()},
        )
        os.environ["BESTSELLER__DATABASE__URL"] = original_url.set(
            database=TEST_DB
        ).render_as_string(hide_password=False)
        root = OUT / "artifacts"
        need(not root.exists(), "new_artifact_root", False, root.exists())
        shutil.copytree("/s01-input-artifacts", root)
        original_files = {
            str(p.relative_to(root)): sha(p.read_bytes())
            for run in RUN_IDS
            for p in (root / "runtime/llm_payloads").glob(run + ".*.json")
        }
        need(len(original_files) == 10, "original_paid_files", 10, len(original_files))
        os.environ["BESTSELLER__ARTIFACT_STORE__LOCAL_DIR"] = str(root)
        os.environ["BESTSELLER__OUTPUT__BASE_DIR"] = str(OUT / "output")
        settings = load_settings()
        settings.output.stream_to_console = False
        need(
            settings.artifact_store.mode == "local"
            and Path(settings.artifact_store.local_dir) == root,
            "artifact_target",
            str(root),
            settings.artifact_store.local_dir,
        )
        import yaml

        from bestseller.services import model_catalog

        for row in yaml.safe_load(model_catalog._CATALOG_PATH.read_bytes()).get("models", []):
            if isinstance(row.get("api_key_env"), str):
                os.environ[row["api_key_env"]] = "cp1-offline-sdk-fixture"
        for role_name in ("planner", "writer", "critic", "editor", "summarizer"):
            role = getattr(settings.llm, role_name, None)
            if role and role.api_key_env:
                os.environ[role.api_key_env] = "cp1-offline-sdk-fixture"
        engine = create_async_engine(settings.database.url)
        engines.append(engine)
        compare_engine = create_async_engine(settings.database.url)
        engines.append(compare_engine)
        compare_connection = await compare_engine.connect()
        await comparison_session(compare_connection)
        clone_before = await all_snapshots(compare_connection)
        # Same full field representation and actual epoch values on both sides.
        # Each before/after pair also stays on its original readonly connection.
        need(
            clone_before == original_before,
            "clone_complete_rows_and_timestamp_epochs",
            original_before,
            clone_before,
        )
        all_budgets_before = await budget_rows(compare_connection)
        STATE.checkpoint(
            "clone_full_baseline_verified",
            comparison_timezone="UTC",
            timestamp_epochs_verified=True,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        from bestseller.infra.db import session as native_db

        await native_db.init_db(settings)
        shared_started = True
        async with native_db.get_server_session() as probe:
            need(
                await probe.scalar(text("SELECT current_database()")) == TEST_DB,
                "native_independent_db",
                TEST_DB,
                await probe.scalar(text("SELECT current_database()")),
            )
        commercial_sessions = {}

        def observe_native_flush(session, ctx):
            for row in session.new:
                if (
                    isinstance(row, m.LlmRunModel)
                    and row.prompt_template == "chapter_commercial_quality_judge"
                ):
                    commercial_sessions[str(row.id)] = {
                        "session_id": id(session),
                        "server_engine": session.get_bind() is native_db._global_engine.sync_engine,
                    }

        observer = observe_native_flush
        event.listen(Session, "after_flush", observer)
        from bestseller.services import pipelines
        from bestseller.services import recorded_review_sources as recorded
        from bestseller.services import review_source_ordering as ordering
        from bestseller.services.budget import bind_execution_scope
        from bestseller.services.chapter_review_reconciliation import prepare_native_review_resume
        from bestseller.services.critic_evidence_gate import build_critic_body_anchors
        from bestseller.services.llm import bind_project_prompt_audit
        from bestseller.services.retry_ledger import project_execution_scope

        async with factory() as s:
            project = await s.get(m.ProjectModel, IDS["project_id"])
            chapter = await s.get(m.ChapterModel, IDS["chapter_id"])
            draft = await s.get(m.ChapterDraftVersionModel, IDS["draft_id"])
            scope = project_execution_scope(project)
            slug = project.slug
            original_cycle = deepcopy(chapter.metadata_json["chapter_critic_cycles"][OLD_CYCLE])
            actual_task = UUID(original_cycle["attempts"][-1]["rewrite_task_id"])
            classifications = []
            for mode in ("index", "sequential"):
                STATE.checkpoint("classify_old_" + mode)
                await s.execute(text("SET LOCAL enable_bitmapscan=off"))
                await s.execute(
                    text("SET LOCAL enable_seqscan=" + ("off" if mode == "index" else "on"))
                )
                await s.execute(
                    text("SET LOCAL enable_indexscan=" + ("on" if mode == "index" else "off"))
                )
                await s.execute(
                    text("SET LOCAL enable_indexonlyscan=" + ("on" if mode == "index" else "off"))
                )
                plan = await s.scalar(
                    text("EXPLAIN (FORMAT JSON) SELECT id FROM review_reports WHERE id=:id"),
                    {"id": str(OLD_REPORT)},
                )
                (OUT / ("classification-" + mode + "-explain.json")).write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
                )

                def plan_nodes(value):
                    if isinstance(value, dict):
                        return ([value["Node Type"]] if "Node Type" in value else []) + [
                            node for item in value.values() for node in plan_nodes(item)
                        ]
                    if isinstance(value, list):
                        return [node for item in value for node in plan_nodes(item)]
                    return []

                nodes = plan_nodes(plan)
                need(
                    any("Index" in node for node in nodes)
                    if mode == "index"
                    else "Seq Scan" in nodes,
                    "actual_plan_nodes." + mode,
                    "Index or Seq as requested",
                    nodes,
                )
                old_view = await recorded.read_archived_review_sources(
                    s, settings, **IDS, cycle_id=OLD_CYCLE
                )
                classification = ordering._source_epoch(old_view)
                need(
                    classification["code"] == "pre_f0_source_unprovable",
                    "old_nonreuse." + mode,
                    "pre_f0_source_unprovable",
                    classification["code"],
                )
                classifications.append(
                    {
                        "mode": mode,
                        "classification": classification,
                        "recorded_calls": ordering._call_receipts(old_view),
                    }
                )
            need(
                classifications[0]["classification"] == classifications[1]["classification"]
                and classifications[0]["recorded_calls"] == classifications[1]["recorded_calls"],
                "two_plans_same_nonreuse",
                classifications[0],
                classifications[1],
            )
            # Reset only the comparison planner switches before normal entry.
            await s.execute(text("SET LOCAL enable_seqscan=on"))
            await s.execute(text("SET LOCAL enable_indexscan=on"))
            await s.execute(text("SET LOCAL enable_indexonlyscan=on"))
            await s.execute(text("SET LOCAL enable_bitmapscan=on"))
            STATE.checkpoint(
                "two_plans_old_nonreuse_verified", classification_observations=classifications
            )
            STATE.checkpoint("prepare_native_successor_before_WF")
            receipt = await prepare_native_review_resume(
                s,
                settings,
                project_id=project.id,
                chapter_id=chapter.id,
                requested_by="cp1-offline-integration",
            )
            await s.commit()
            await s.refresh(chapter)
            successor = chapter.metadata_json["chapter_review_active_cycle"]["cycle_id"]
            stored = chapter.metadata_json[ordering.HISTORY_KEY][-1]
            descriptor = stored.get("commercial_cohort") or old_view.commercial_cohort
            old_owner_id = UUID(descriptor["workflow_run_id"])
            old_owner = await s.get(m.WorkflowRunModel, old_owner_id)
            old_journal = deepcopy(
                old_owner.metadata_json["commercial_component_cohorts"][descriptor["key"]]
            )
            script.body_id = build_critic_body_anchors(draft.content_md)[0]["id"]
            original_body = draft.content_md
            STATE.checkpoint(
                "native_successor_prepared",
                native_prepare_receipt=receipt,
                successor_cycle_id=successor,
                supersession_schema=stored["schema"],
                old_task_id=str(actual_task),
            )
            await s.rollback()

        async def transport(this, request):
            return await script.transport(this, request)

        httpx.AsyncHTTPTransport.handle_async_request = transport
        logging.getLogger().setLevel(logging.ERROR)

        class NativeCaughtFailure(logging.Handler):
            def emit(self, record):
                if record.exc_info and record.exc_info[1] is not None:
                    error = record.exc_info[1].with_traceback(record.exc_info[2])
                    trace = record.exc_info[2]
                    while trace.tb_next is not None:
                        trace = trace.tb_next
                    STATE.failure(
                        error,
                        step="native_caught:"
                        + trace.tb_frame.f_code.co_name
                        + ":"
                        + str(trace.tb_lineno),
                    )

        caught_logger = NativeCaughtFailure()
        logging.getLogger().addHandler(caught_logger)

        def on_progress(event, payload):
            STATE.checkpoint(
                "native_progress_" + event,
                chapter_number=payload.get("chapter_number"),
                native_step=payload.get("step"),
                script_consumed_index=len(script.calls),
            )

        returns = []
        first_cycle = None
        first_paid = None
        first_budget = None
        for ordinal in range(3):
            STATE.checkpoint("normal_resume_" + str(ordinal + 1))
            async with factory() as s:
                with (
                    bind_execution_scope(scope),
                    bind_project_prompt_audit(
                        IDS["project_id"], reason="CP1 offline native successor verification"
                    ),
                ):
                    returned = await pipelines.run_chapter_pipeline(
                        s,
                        settings,
                        slug,
                        4,
                        requested_by="cp1-offline-integration",
                        chapter_first=True,
                        resume_existing_draft_only=True,
                        review_source_binding={
                            "draft_id": str(IDS["draft_id"]),
                            "body_hash": pipelines.chapter_review_body_fingerprint(original_body)[
                                "digest"
                            ],
                        },
                        critic_cycle_id=successor,
                        supersede_pending_rewrites=False,
                        export_markdown=False,
                        progress=on_progress,
                    )
                await s.commit()
                returns.append(returned.model_dump(mode="json"))
                need(
                    returned.review_report_id is not None
                    and returned.review_report_id != OLD_REPORT,
                    "new_review_report",
                    True,
                    returned.review_report_id is not None
                    and returned.review_report_id != OLD_REPORT,
                )
                need(
                    returned.final_verdict != "unknown",
                    "new_review_available",
                    "rewrite",
                    returned.final_verdict,
                )
                chapter = await s.get(m.ChapterModel, IDS["chapter_id"], populate_existing=True)
                cycle = deepcopy(chapter.metadata_json["chapter_critic_cycles"][successor])
            need(len(script.calls) == 5, "no_repeated_sdk_calls", 5, len(script.calls))
            paid = {
                t: await table_snapshot(compare_connection, t)
                for t in ("llm_runs", "execution_operations", "execution_attempts")
            }
            budgets_now = await budget_rows(compare_connection)
            verify_budgets(
                all_budgets_before, budgets_now, target=scope, calls_delta=5, tokens_delta=1000
            )
            if ordinal == 0:
                first_cycle, first_paid, first_budget = cycle, paid, budgets_now
            else:
                need(cycle == first_cycle, "reentry_same_complete_cycle", first_cycle, cycle)
                need(paid == first_paid, "reentry_same_paid_rows", first_paid, paid)
                need(
                    budgets_now == first_budget,
                    "reentry_same_complete_budgets",
                    first_budget,
                    budgets_now,
                )
                need(
                    returns[-1]["review_report_id"] == returns[0]["review_report_id"],
                    "reentry_same_report",
                    returns[0]["review_report_id"],
                    returns[-1]["review_report_id"],
                )
            STATE.checkpoint(
                "normal_resume_" + str(ordinal + 1) + "_verified",
                native_returns=returns,
                script_consumed_index=len(script.calls),
                native_commercial_sessions=commercial_sessions,
            )
        STATE.checkpoint("final_native_invariants")
        async with factory() as s:
            chapter = await s.get(m.ChapterModel, IDS["chapter_id"])
            draft = await s.get(m.ChapterDraftVersionModel, IDS["draft_id"])
            need(
                draft.content_md == original_body, "retained_body", original_body, draft.content_md
            )
            need(
                chapter.metadata_json["chapter_critic_cycles"][OLD_CYCLE] == original_cycle,
                "old_cycle_immutable",
                original_cycle,
                chapter.metadata_json["chapter_critic_cycles"][OLD_CYCLE],
            )
            owner = await s.get(m.WorkflowRunModel, old_owner_id)
            need(
                owner.metadata_json["commercial_component_cohorts"][descriptor["key"]]
                == old_journal,
                "old_cohort_immutable",
                old_journal,
                owner.metadata_json["commercial_component_cohorts"][descriptor["key"]],
            )
            audit = chapter.metadata_json["deterministic_audit_f0_commercial_successions"]
            need(len(audit) == 1, "commercial_successor_once", 1, len(audit))
            archived = await ordering.archived_rewrite_task_ids(
                s, project_id=IDS["project_id"], chapter_id=IDS["chapter_id"], settings=settings
            )
            need(actual_task in archived, "old_task_stays_archived", True, actual_task in archived)
            new_runs = list(
                await s.scalars(
                    select(m.LlmRunModel)
                    .where(m.LlmRunModel.project_id == IDS["project_id"])
                    .order_by(m.LlmRunModel.created_at, m.LlmRunModel.id)
                )
            )
            new_runs = [
                row for row in new_runs if str(row.id) not in clone_before["llm_runs"]["rows"]
            ]
            need(len(new_runs) == 5, "native_new_runs", 5, len(new_runs))
            STATE.data["new_native_runs"] = [
                {
                    "id": str(row.id),
                    "template": row.prompt_template,
                    "finish_reason": row.finish_reason,
                }
                for row in new_runs
            ]
            STATE.data["last_successful_llm_template"] = new_runs[-1].prompt_template
        after = await all_snapshots(compare_connection)
        for table in (
            "llm_runs",
            "execution_operations",
            "execution_attempts",
            "review_reports",
            "rewrite_tasks",
            "workflow_runs",
            "book_production_control",
        ):
            verify_old_rows(clone_before[table]["rows"], after[table]["rows"], field=table)
            verify_old_rows(
                clone_before[table]["timestamp_epochs"],
                after[table]["timestamp_epochs"],
                field=table + ".epochs",
            )
        for rel, digest in original_files.items():
            need(
                sha((root / rel).read_bytes()) == digest,
                "old_physical_file." + rel,
                digest,
                sha((root / rel).read_bytes()),
            )
        need(
            Counter(call["family"] for call in script.calls)
            == {"commercial": 3, "window": 1, "primary": 1},
            "sdk_template_counts",
            {"commercial": 3, "window": 1, "primary": 1},
            dict(Counter(call["family"] for call in script.calls)),
        )
        need(
            len(commercial_sessions) == 3
            and all(v["server_engine"] for v in commercial_sessions.values())
            and len({v["session_id"] for v in commercial_sessions.values()}) == 3,
            "native_independent_commercial_sessions",
            "3 separate real server sessions",
            commercial_sessions,
        )
        STATE.checkpoint(
            "native_all_checks_passed",
            candidate_success=True,
            normal_successor_complete=True,
            two_reentries_no_new_sdk_calls=True,
            all_original_financial_rows_unchanged=True,
            old_report_task_cycle_journal_unchanged=True,
            all_budget_fields_protected=True,
            old_physical_files_unchanged=True,
            simulated_new_calls=5,
            simulated_tokens=1000,
        )
    except BaseException as error:
        STATE.failure(error, step=STATE.data.get("failed_workflow_step", "native_runtime"))
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original_transport
        if caught_logger is not None:
            logging.getLogger().removeHandler(caught_logger)
        STATE.data["script_consumed_index"] = len(script.calls)
        STATE.data["sdk_calls"] = script.calls
        if script.calls and STATE.data.get("last_successful_llm_template", "Unknown").startswith(
            "Unknown"
        ):
            STATE.data["last_successful_llm_template"] = {
                "primary": "chapter_review",
                "window": "chapter_window_quality_judge",
                "commercial": "chapter_commercial_quality_judge",
            }[script.calls[-1]["family"]]
        if observer is not None:
            event.remove(Session, "after_flush", observer)
        if shared_started:
            try:
                await native_db.shutdown_db()
            except BaseException as error:
                STATE.failure(error, step="shutdown_native_pool")
        if compare_connection is not None:
            try:
                await compare_connection.close()
            except BaseException as error:
                STATE.failure(error, step="close_clone_comparison")
        os.environ["BESTSELLER__DATABASE__URL"] = original_url.render_as_string(hide_password=False)
        if source_connection is not None:
            try:
                if original_before is not None:
                    original_after = await all_snapshots(source_connection)
                    need(
                        original_before == original_after,
                        "original_snapshot_all_fields_and_epochs",
                        original_before,
                        original_after,
                    )
                    STATE.checkpoint("final_source_unchanged", original_snapshot_unchanged=True)
            except BaseException as error:
                STATE.failure(error, step="final_source_check")
            finally:
                try:
                    await source_connection.close()
                except BaseException as error:
                    STATE.failure(error, step="close_source_comparison")
        for owned in engines:
            try:
                await owned.dispose()
            except BaseException as error:
                STATE.failure(error, step="dispose_owned_engine")
        cleanup()
        STATE.finish(
            final_checks_complete=bool(
                STATE.data.get("candidate_success")
                and STATE.data.get("original_snapshot_unchanged")
            )
        )
    raise SystemExit(0 if STATE.data["status"] == "通过" else 1)


asyncio.run(main())

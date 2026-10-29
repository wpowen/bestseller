"""E05b C0 native issuance and C1/C2 comment-only normal reentry.

Derived from the existing S01 native harness; only SDK HTTP returns are scripted.
Run by Main only against its isolated clone and frozen git-archive runtime.
"""

# ruff: noqa: E402 -- network audit hook must precede application imports.
import ast
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
import signal
import socket
import subprocess
import sys
from uuid import UUID, uuid4

from support.cp1_rehearsal import (
    COMPARISON_TABLES,
    RunState,
    all_snapshots,
    budget_rows,
    comparison_session,
    table_snapshot,
    verify_budgets,
    verify_old_rows,
)

os.umask(0o077)
ENTRY_EPOCH = os.environ.get("FULL_FLOW_E05B_ENTRY_EPOCH")
EVIDENCE_ROOT = Path(os.environ["FULL_FLOW_E05B_EVIDENCE"])
ENTRY_ROOT = EVIDENCE_ROOT / ("entry-" + ENTRY_EPOCH) if ENTRY_EPOCH else EVIDENCE_ROOT
STATE = RunState(ENTRY_ROOT / "RESULT.json")
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

OUT = EVIDENCE_ROOT
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
CONTROL_DB = "full_flow_e05b_control_" + suffix + "_test"
TEST_DB = "full_flow_e05b_" + suffix + "_test"
for name in (CONTROL_DB, TEST_DB):
    assert re.fullmatch(r"full_flow_e05b_(?:control_)?[a-f0-9]{32}_test", name)


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
        "sha256": sha(x if isinstance(x, bytes) else encoded(x)),
    }


def need(ok, field, expected, actual):
    if not ok:
        err = ValueError("e05b:" + field)
        err.differences = [
            {"field": field, "expected": summary(expected), "actual": summary(actual)}
        ]
        raise err


def full_equal(expected, actual):
    """Full typed equality, including physical bytes; hashes are diagnostics only."""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return {(type(key), key) for key in expected} == {
            (type(key), key) for key in actual
        } and all(full_equal(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        return len(expected) == len(actual) and all(
            full_equal(left, right) for left, right in zip(expected, actual, strict=True)
        )
    if isinstance(expected, bytes):
        return expected == actual
    return json.dumps(expected, ensure_ascii=False, allow_nan=False) == json.dumps(
        actual, ensure_ascii=False, allow_nan=False
    )


def equal(field, expected, actual):
    need(full_equal(expected, actual), field, expected, actual)


def tree_bytes(root):
    need(root.is_dir() and not root.is_symlink(), "regular_tree", True, root.is_dir())
    result = {}
    for path in sorted(root.rglob("*")):
        need(not path.is_symlink(), "tree_symlink." + path.name, False, path.is_symlink())
        if path.is_file():
            result[str(path.relative_to(root))] = path.read_bytes()
    return result


def stage_epoch(ordinal, baseline):
    """Only comments in the private runtime copy; never mutate checkout/frozen source."""
    runtime = Path("/fullflow")
    epoch = f"C{ordinal}"
    comments = (
        "src/bestseller/services/review_issuer_view.py",
        "src/bestseller/services/chapter_review_preflight.py",
    )
    if ordinal:
        target = runtime / comments[ordinal - 1]
        need(target.is_file() and not target.is_symlink(), "comment_target", True, target.is_file())
        with target.open("ab") as stream:
            stream.write(f"\n# E05b isolated runtime {epoch}: comment-only upgrade.\n".encode())
    current = tree_bytes(runtime)
    equal("runtime.paths." + epoch, sorted(baseline), sorted(current))
    changed = []
    for path, original in baseline.items():
        actual = current[path]
        if original != actual:
            changed.append(path)
        if path.endswith(".py"):
            equal(
                "runtime.full_ast." + epoch + "." + path,
                ast.dump(ast.parse(original), include_attributes=False),
                ast.dump(ast.parse(actual), include_attributes=False),
            )
        else:
            equal("runtime.non_python_bytes." + epoch + "." + path, original, actual)
    equal("runtime.comment_only_paths." + epoch, sorted(comments[:ordinal]), sorted(changed))
    destination = OUT / "runtime_epochs" / epoch
    need(not destination.exists(), "new_epoch_copy." + epoch, False, destination.exists())
    shutil.copytree(runtime, destination)
    equal("runtime.epoch_copy." + epoch, current, tree_bytes(destination))
    manifest = {path: sha(raw) for path, raw in current.items()}
    (OUT / (epoch + "-source-sha256.json")).write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    return epoch


async def complete_rows(connection, tables=COMPARISON_TABLES):
    """Every column as original PG JSON text plus typed JSON; no field projection."""
    result = {}
    for table in tables:
        need(table in COMPARISON_TABLES, "native_table_allowlist", True, table in COMPARISON_TABLES)
        key = {
            "execution_budgets": "scope_id",
            "execution_operations": "key",
            "book_production_control": "project_id",
        }.get(table, "id")
        rows = await connection.execute(
            text(f"SELECT {key},to_jsonb(t)::text FROM public.{table} t ORDER BY {key}")
        )
        result[table] = {
            str(row[0]): {"bytes": row[1].encode("utf-8"), "value": json.loads(row[1])}
            for row in rows
        }
    return result


async def c0_quality_expectation(connection, original, *, project_id, chapter_id):
    """Read actual pointers; project only the native producer's exact demotion.

    Prior snapshot summaries contain hashes, not these original bool values.
    Never assume that an old quality row is current. This SELECT is read-only
    and retains every column, including the native PG JSON byte representation.
    """
    rows = await connection.execute(
        text(
            "SELECT id, to_jsonb(t)::text, "
            "(CASE WHEN target_type = 'chapter' AND target_id = :chapter_id "
            "AND is_current IS TRUE "
            "THEN jsonb_set(to_jsonb(t), '{is_current}', 'false'::jsonb, false) "
            "ELSE to_jsonb(t) END)::text "
            "FROM public.quality_scores t ORDER BY id"
        ),
        {"chapter_id": chapter_id},
    )
    observed, expected, transitions = {}, {}, []
    for identity, before_text, after_text in rows:
        identity = str(identity)
        before, after = json.loads(before_text), json.loads(after_text)
        observed[identity] = {"bytes": before_text.encode("utf-8"), "value": before}
        expected[identity] = {"bytes": after_text.encode("utf-8"), "value": after}
        field = "C0.quality_scores." + identity
        equal(field + ".is_current.type", "bool", type(before.get("is_current")).__name__)
        projected = deepcopy(before)
        if (
            before.get("target_type") == "chapter"
            and before.get("target_id") == str(chapter_id)
            and before["is_current"] is True
        ):
            equal(field + ".project_id", str(project_id), before.get("project_id"))
            projected["is_current"] = False
            transitions.append(
                {
                    "quality_score_id": identity,
                    "field": "is_current",
                    "before": summary(before["is_current"]),
                    "expected_after": summary(False),
                    "before_row_sha256": sha(observed[identity]["bytes"]),
                    "expected_row_sha256": sha(expected[identity]["bytes"]),
                }
            )
        else:
            equal(field + ".unchanged_bytes", before_text.encode(), after_text.encode())
        equal(field + ".all_projected_fields", projected, after)
    equal("C0.quality_scores.before_native_issuance", original, observed)
    return expected, transitions


def verify_c0_quality(expected, actual, checkpoint):
    """Exactly one native new row; all original fields survive except demotion."""
    new_id = checkpoint["quality_id"]
    need(new_id not in expected, "C0.quality_scores.new_identity", False, new_id in expected)
    equal("C0.quality_scores.complete_ids", sorted([*expected, new_id]), sorted(actual))
    for identity, row in expected.items():
        field = "C0.quality_scores." + identity
        current = actual[identity]
        for key, value in row["value"].items():
            equal(field + "." + key, value, current["value"].get(key))
        equal(field + ".complete_row", row, current)
    new = actual[new_id]["value"]
    for key, value in {
        "id": new_id,
        "project_id": str(IDS["project_id"]),
        "target_type": "chapter",
        "target_id": str(IDS["chapter_id"]),
        "chapter_draft_version_id": str(IDS["draft_id"]),
        "review_report_id": checkpoint["report_id"],
        "is_current": True,
    }.items():
        equal("C0.quality_scores.new_row." + key, value, new.get(key))


async def paid_physical_bytes(session, root, native_calls, capsule):
    """Follow actual native run references and the original authenticated capsule."""
    files = {}
    for identity in sorted({call.llm_run_id for call in native_calls}, key=str):
        run = await session.get(m.LlmRunModel, identity, populate_existing=True)
        need(run is not None, "paid_run_exists", True, run is not None)
        for kind in ("request_payload_ref", "response_payload_ref"):
            reference = getattr(run, kind)
            need(
                type(reference) is str and bool(reference),
                "paid_reference." + kind,
                "nonempty native reference",
                summary(reference),
            )
            path = root / reference
            need(
                path.resolve().is_relative_to(root.resolve()) and not path.is_symlink(),
                "paid_file_scope",
                True,
                path.resolve().is_relative_to(root.resolve()),
            )
            files[reference] = path.read_bytes()
    directory = root / capsule["archive_ref"]
    need(
        directory.resolve().is_relative_to(root.resolve()),
        "capsule_file_scope",
        True,
        directory.resolve().is_relative_to(root.resolve()),
    )
    for relative, raw in tree_bytes(directory).items():
        files[str(directory.relative_to(root) / relative)] = raw
    return files


async def issuer_checkpoint(session, settings, successor, report_id, old_calls):
    from bestseller.services import chapter_review_preflight, pipelines, review_issuer_view
    from bestseller.services import recorded_review_sources as recorded

    ids = {**IDS, "cycle_id": successor}
    native = await recorded.read_archived_review_sources(session, settings, **ids)
    primary = [call for call in native.calls if call.prompt_template == "chapter_review"]
    equal("native.primary_count", 1, len(primary))
    capsule = deepcopy(primary[0].source["review_issuer"])
    authenticated = await review_issuer_view.auth_source_capsule(session, settings, **ids)
    equal("native.original_capsule", capsule, authenticated["capsule"])
    chapter = await session.get(m.ChapterModel, IDS["chapter_id"], populate_existing=True)
    proof = await chapter_review_preflight.validate_retained_review_before_processing(
        session,
        settings,
        chapter,
    )
    need(type(proof) is dict, "native.complete_preflight", "dict", summary(proof))
    equal("native.preflight.kind", "available_review", proof["reuse_kind"])
    for key, expected in {
        **{key: str(value) for key, value in ids.items()},
        "report_id": str(report_id),
    }.items():
        equal("native.preflight." + key, expected, proof[key])
    cycle = deepcopy(chapter.metadata_json["chapter_critic_cycles"][successor])
    equal("native.issued_policy", cycle["policy_source_hash"], capsule["runtime_policy_sha256"])
    equal("native.proof_issued_policy", cycle["policy_source_hash"], proof["policy_source_hash"])
    equal(
        "native.proof_current_policy",
        pipelines.chapter_review_recovery_source_hash(),
        proof["current_runtime_policy_sha256"],
    )
    need(
        type(proof.get("retained_input_proof")) is dict,
        "native.full_input_proof",
        "dict",
        summary(proof.get("retained_input_proof")),
    )
    # This is the actual normal retained loader, not a reconstructed PASS row.
    draft = await session.get(m.ChapterDraftVersionModel, IDS["draft_id"])
    result, report, quality, task = await pipelines._retained_chapter_critic_result(
        session,
        draft,
        cycle["attempts"][-1],
    )
    equal("native.result_report", str(report_id), str(report.id))
    return {
        "capsule": capsule,
        "cycle": cycle,
        "result": result.model_dump(mode="json"),
        "report_id": str(report.id),
        "quality_id": str(quality.id),
        "task_id": str(task.id) if task is not None else None,
        "physical": await paid_physical_bytes(
            session,
            Path(settings.artifact_store.local_dir),
            [*old_calls, *native.calls],
            capsule,
        ),
        "runtime_policy": proof["current_runtime_policy_sha256"],
        "input_proof": proof["retained_input_proof"],
    }


class ScriptedWire(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body


class SDKScript:
    def __init__(self):
        self.calls = []
        self.body_id = None
        self.epoch = "C0"

    def response(self, body):
        tools = body.get("tools") or []
        name = tools[0]["function"]["name"] if tools else None
        if name == "submit_chapter_critique":
            props = tools[0]["function"]["parameters"]["properties"]
            payload = {
                "verdict": "pass",
                "scores": {key: 0.95 for key in props["scores"]["properties"]},
                "rewrite_direction": "无",
                "commentary": (
                    "Scripted SDK integration fixture; "
                    "reader quality acceptance has not been performed."
                ),
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
        need(self.epoch == "C0", "no_new_sdk_send." + self.epoch, "C0", self.epoch)
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
                    "id": f"e05b-script-tool-{number}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(payload, ensure_ascii=False),
                    },
                }
            ]
        finish = "tool_calls" if name else "stop"
        envelope = {
            "id": f"e05b-scripted-{number}",
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


async def native_entry():
    """Fresh interpreter per epoch; selectors are re-read from the native clone."""
    need(ENTRY_EPOCH in {"C0", "C1", "C2"}, "entry.epoch", "C0/C1/C2", ENTRY_EPOCH)
    context = json.loads((ENTRY_ROOT / "input.json").read_bytes())
    equal("entry.epoch_identity", ENTRY_EPOCH, context["epoch"])
    database = selected_database_url().database
    equal("entry.database", context["database"], database)
    need(
        bool(re.fullmatch(r"full_flow_e05b_[a-f0-9]{32}_test", database)),
        "entry.owned_clone",
        "new E05b clone",
        database,
    )
    cleanup = install_database_safety()
    settings = load_settings()
    settings.output.stream_to_console = False
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    script = SDKScript()
    script.epoch = ENTRY_EPOCH
    original_transport = httpx.AsyncHTTPTransport.handle_async_request
    native_started, observer, caught_logger = False, None, None
    from bestseller.infra.db import session as native_db
    from bestseller.services import pipelines
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.critic_evidence_gate import build_critic_body_anchors
    from bestseller.services.llm import bind_project_prompt_audit
    from bestseller.services.retry_ledger import project_execution_scope

    STATE.checkpoint(
        "fresh_native_imports",
        epoch=ENTRY_EPOCH,
        pid=os.getpid(),
        parent_pid=os.getppid(),
        application_guard_mocks=False,
        ordinary_retained_entry=ENTRY_EPOCH != "C0",
    )
    try:
        await native_db.init_db(settings)
        native_started = True
        async with native_db.get_server_session() as probe:
            equal(
                "entry.actual_database",
                database,
                await probe.scalar(text("SELECT current_database()")),
            )
        imported = {}
        for name in (
            "pipelines",
            "review_issuer_archive",
            "review_issuer_view",
            "chapter_review_preflight",
            "retained_review_input_proof",
        ):
            __import__("bestseller.services." + name)
            module = sys.modules["bestseller.services." + name]
            path = Path(module.__file__).resolve()
            need(
                path.is_relative_to("/fullflow/src"),
                "entry.import_root." + name,
                "/fullflow/src",
                str(path),
            )
            imported[str(path.relative_to("/fullflow"))] = sha(path.read_bytes())
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

        async def transport(this, request):
            return await script.transport(this, request)

        httpx.AsyncHTTPTransport.handle_async_request = transport

        class NativeCaughtFailure(logging.Handler):
            def emit(self, record):
                if record.exc_info and record.exc_info[1] is not None:
                    STATE.failure(
                        record.exc_info[1].with_traceback(record.exc_info[2]), step="native_caught"
                    )

        caught_logger = NativeCaughtFailure()
        logging.getLogger().addHandler(caught_logger)
        logging.getLogger().setLevel(logging.ERROR)
        observed, retained_returns = set(), []
        watched = {
            "run_chapter_pipeline",
            "_review_chapter_with_evidence_retry",
            "retained_review_matches",
            "validate_retained_review_before_processing",
            "verify_retained_review_inputs",
            "require_cycle_policy",
            "derive_current_primary_source",
            "derive_selected_primary_source",
        }

        def observe_call(frame, event_name, arg):
            name = frame.f_code.co_name
            if name in watched and frame.f_globals.get("__name__", "").startswith(
                "bestseller.services."
            ):
                if event_name == "call":
                    observed.add(name)
                if event_name == "return" and name == "retained_review_matches":
                    # Observe the actual bool; neither replace nor call the closure.
                    retained_returns.append(arg)

        def on_progress(event_name, payload):
            STATE.checkpoint(
                "native_progress_" + event_name, script_consumed_index=len(script.calls)
            )

        async with factory() as session:
            project = await session.get(m.ProjectModel, IDS["project_id"])
            chapter = await session.get(m.ChapterModel, IDS["chapter_id"])
            draft = await session.get(m.ChapterDraftVersionModel, IDS["draft_id"])
            need(
                project is not None and chapter is not None and draft is not None,
                "entry.native_selectors",
                True,
                False,
            )
            equal("entry.chapter_project", project.id, chapter.project_id)
            equal("entry.draft_chapter", chapter.id, draft.chapter_id)
            equal("entry.draft_project", project.id, draft.project_id)
            active = (chapter.metadata_json or {}).get("chapter_review_active_cycle") or {}
            successor = context["successor_cycle_id"]
            equal("entry.native_active_cycle", successor, active.get("cycle_id"))
            scope = project_execution_scope(project)
            script.body_id = build_critic_body_anchors(draft.content_md)[0]["id"]
            binding = {
                "draft_id": str(draft.id),
                "body_hash": pipelines.chapter_review_body_fingerprint(draft.content_md)["digest"],
            }
            previous_profile = sys.getprofile()
            sys.setprofile(observe_call)
            try:
                with (
                    bind_execution_scope(scope),
                    bind_project_prompt_audit(
                        project.id, reason="E05b offline native issuer compatibility"
                    ),
                ):
                    returned = await pipelines.run_chapter_pipeline(
                        session,
                        settings,
                        project.slug,
                        chapter.chapter_number,
                        requested_by="e05b-offline-integration",
                        chapter_first=True,
                        resume_existing_draft_only=ENTRY_EPOCH == "C0",
                        review_source_binding=binding if ENTRY_EPOCH == "C0" else None,
                        critic_cycle_id=successor,
                        supersede_pending_rewrites=False,
                        export_markdown=False,
                        progress=on_progress,
                    )
            finally:
                sys.setprofile(previous_profile)
            await session.commit()
            STATE.checkpoint(
                "native_entry_returned", native_return=returned.model_dump(mode="json")
            )
            # A scripted primary pass cannot clear deterministic/native findings.
            equal("entry.native_verdict", "pass", returned.final_verdict)
            need(
                returned.review_report_id is not None and returned.review_report_id != OLD_REPORT,
                "entry.native_new_report",
                True,
                returned.review_report_id is not None,
            )
            if ENTRY_EPOCH != "C0":
                for required in (
                    "retained_review_matches",
                    "validate_retained_review_before_processing",
                    "verify_retained_review_inputs",
                    "require_cycle_policy",
                ):
                    need(
                        required in observed,
                        "entry.observed." + required,
                        True,
                        required in observed,
                    )
                need(
                    any(type(value) is bool and value is True for value in retained_returns),
                    "entry.retained_review_matches_true",
                    True,
                    retained_returns,
                )
            equal("entry.new_sdk_calls", 5 if ENTRY_EPOCH == "C0" else 0, len(script.calls))
            STATE.checkpoint(
                "native_entry_verified",
                imported_source_sha256=imported,
                observed_normal_entry_functions=sorted(observed),
                retained_review_matches_returns=retained_returns,
                native_commercial_sessions=commercial_sessions,
                sdk_calls=script.calls,
                candidate_success=True,
            )
    except BaseException as error:
        STATE.failure(error, step=STATE.data.get("current_stage", "entry"))
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original_transport
        if observer is not None:
            event.remove(Session, "after_flush", observer)
        if caught_logger is not None:
            logging.getLogger().removeHandler(caught_logger)
        if native_started:
            try:
                await native_db.shutdown_db()
            except BaseException as error:
                STATE.failure(error, step="entry.shutdown_native_pool")
        try:
            await engine.dispose()
        except BaseException as error:
            STATE.failure(error, step="entry.dispose")
        cleanup()
        STATE.data["sdk_calls"] = script.calls
        STATE.finish(final_checks_complete=bool(STATE.data.get("candidate_success")))
    return 0 if STATE.data["status"] == "通过" else 1


async def launch_native_entry(epoch, successor):
    directory = OUT / ("entry-" + epoch)
    need(not directory.exists(), "new_entry_receipt." + epoch, False, directory.exists())
    directory.mkdir(mode=0o700)
    (directory / "input.json").write_text(
        json.dumps(
            {
                "epoch": epoch,
                "database": TEST_DB,
                "successor_cycle_id": successor,
            },
            sort_keys=True,
        )
        + "\n"
    )
    child_env = {**os.environ, "FULL_FLOW_E05B_ENTRY_EPOCH": epoch}
    with (directory / "process.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            env=child_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = await asyncio.wait_for(process.wait(), timeout=500)
        except BaseException:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.wait_for(process.wait(), timeout=10)
            raise
    receipt = directory / "RESULT.json"
    need(receipt.is_file(), "entry.receipt_exists." + epoch, True, receipt.is_file())
    result = json.loads(receipt.read_bytes())
    STATE.data.setdefault("epoch_process_receipts", {})[epoch] = {
        "pid": result.get("pid"),
        "exit_code": code,
        "status": result.get("status"),
        "scripted_returns": len(result.get("sdk_calls", [])),
        "receipt_path": str(receipt),
    }
    if result.get("status") != "通过":
        for key in (
            "root_error_code",
            "root_exception_type",
            "failure_differences",
            "traceback_path",
            "failed_workflow_step",
            "secondary_failures",
        ):
            if key in result:
                STATE.data[key] = result[key]
        STATE.failed = True
        STATE.data["status"] = "失败"
        STATE.persist()
    need(
        code == 0 and result.get("status") == "通过",
        "entry.completed." + epoch,
        {"exit": 0, "status": "通过"},
        {"exit": code, "status": result.get("status")},
    )
    equal("entry.fresh_pid." + epoch, process.pid, result["pid"])
    need(result["pid"] != os.getpid(), "entry.not_parent." + epoch, True, False)
    for relative, expected in result["imported_source_sha256"].items():
        equal(
            "entry.imported_bytes." + epoch + "." + relative,
            sha((Path("/fullflow") / relative).read_bytes()),
            expected,
        )
    return result


async def main():
    frozen_root = Path("/frozen-source")
    runtime_baseline = tree_bytes(frozen_root)
    equal("git_archive_runtime_copy", runtime_baseline, tree_bytes(Path("/fullflow")))
    need(
        bool(re.fullmatch(r"[a-f0-9]{40}", os.environ["FULL_FLOW_E05B_FROZEN_HEAD"])),
        "frozen_head",
        "40 lowerhex",
        summary(os.environ["FULL_FLOW_E05B_FROZEN_HEAD"]),
    )
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
    original_complete_before = None
    scope = None
    factory = None
    source_connection = None
    compare_connection = None
    shared_started = False
    observer = None
    caught_logger = None
    original_transport = httpx.AsyncHTTPTransport.handle_async_request
    STATE.data.update(
        schema="full-flow-e05b-native-handoff.v1",
        failed_workflow_type="E05b_native_issuer_handoff",
        at_utc=datetime.now(timezone.utc).isoformat(),
        production_access=False,
        external_provider_calls=0,
        test_database=TEST_DB,
        control_database=CONTROL_DB,
        full_book_complete=False,
        e05b_complete=False,
        frozen_head=os.environ["FULL_FLOW_E05B_FROZEN_HEAD"],
        shared_issuer_view_verified=False,
        simulation=(
            "Native pipeline/gateway/ledger/SDK; only HTTP response transport scripted; "
            "no quality acceptance claim"
        ),
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
        original_complete_before = await complete_rows(source_connection)
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
            str(p.relative_to(root)): p.read_bytes()
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
                os.environ[row["api_key_env"]] = "e05b-offline-sdk-fixture"
        for role_name in ("planner", "writer", "critic", "editor", "summarizer"):
            role = getattr(settings.llm, role_name, None)
            if role and role.api_key_env:
                os.environ[role.api_key_env] = "e05b-offline-sdk-fixture"
        engine = create_async_engine(settings.database.url)
        engines.append(engine)
        compare_engine = create_async_engine(settings.database.url)
        engines.append(compare_engine)
        compare_connection = await compare_engine.connect()
        await comparison_session(compare_connection)
        clone_before = await all_snapshots(compare_connection)
        clone_complete_before = await complete_rows(compare_connection)
        equal("clone_complete_native_bytes", original_complete_before, clone_complete_before)
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
        from bestseller.services.chapter_review_reconciliation import prepare_native_review_resume
        from bestseller.services.retry_ledger import project_execution_scope

        need(
            Path(pipelines.__file__).resolve().is_relative_to(Path("/fullflow/src")),
            "native_import_root",
            "/fullflow/src",
            str(Path(pipelines.__file__).resolve()),
        )

        async with factory() as s:
            project = await s.get(m.ProjectModel, IDS["project_id"])
            chapter = await s.get(m.ChapterModel, IDS["chapter_id"])
            draft = await s.get(m.ChapterDraftVersionModel, IDS["draft_id"])
            scope = project_execution_scope(project)
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
                requested_by="e05b-offline-integration",
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
            need(
                False, "parent_read_only_sdk_send", "native epoch child only", str(request.url.host)
            )

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

        returns = []
        first_cycle = None
        first_paid = None
        first_budget = None
        first_checkpoint = None
        first_complete = None
        expected_c0_quality = None
        quality_transitions = []
        policies = []
        protected_tables = (
            "review_reports",
            "quality_scores",
            "rewrite_tasks",
            "chapter_draft_versions",
            "llm_runs",
            "execution_operations",
            "execution_attempts",
            "execution_budgets",
        )
        entry_calls = {}
        entry_pids = {}
        for ordinal in range(3):
            epoch = stage_epoch(ordinal, runtime_baseline)
            script.epoch = epoch
            if ordinal == 0:
                expected_c0_quality, quality_transitions = await c0_quality_expectation(
                    compare_connection,
                    clone_complete_before["quality_scores"],
                    project_id=IDS["project_id"],
                    chapter_id=IDS["chapter_id"],
                )
            else:
                async with factory() as s:
                    prior = await issuer_checkpoint(
                        s,
                        settings,
                        successor,
                        UUID(first_checkpoint["report_id"]),
                        old_view.calls,
                    )
                    expected = {**first_checkpoint, "runtime_policy": prior["runtime_policy"]}
                    equal("before_entry.full_native_proof." + epoch, expected, prior)
                equal(
                    "preflight_read_only." + epoch,
                    first_complete,
                    await complete_rows(compare_connection, protected_tables),
                )
            STATE.checkpoint("normal_resume_" + str(ordinal + 1))
            child = await launch_native_entry(epoch, successor)
            entry_pids[epoch] = child["pid"]
            equal("independent_epoch_processes", ordinal + 1, len(set(entry_pids.values())))
            returns.append(child["native_return"])
            if ordinal == 0:
                script.calls = child["sdk_calls"]
                commercial_sessions = child["native_commercial_sessions"]
            else:
                equal("reentry.zero_new_sdk_calls." + epoch, [], child["sdk_calls"])
                equal("reentry.ordinary_mode." + epoch, True, child["ordinary_retained_entry"])
                need(
                    any(
                        type(value) is bool and value is True
                        for value in child["retained_review_matches_returns"]
                    ),
                    "reentry.actual_retained_match." + epoch,
                    True,
                    child["retained_review_matches_returns"],
                )
            observed = set(child["observed_normal_entry_functions"])
            report_id = UUID(child["native_return"]["review_report_id"])
            async with factory() as s:
                need(report_id != OLD_REPORT, "new_review_report", True, report_id != OLD_REPORT)
                equal(
                    "native_pass_before_ordinary_reentry",
                    "pass",
                    child["native_return"]["final_verdict"],
                )
                chapter = await s.get(m.ChapterModel, IDS["chapter_id"], populate_existing=True)
                cycle = deepcopy(chapter.metadata_json["chapter_critic_cycles"][successor])
                checkpoint = await issuer_checkpoint(
                    s,
                    settings,
                    successor,
                    report_id,
                    old_view.calls,
                )
            entry_calls[epoch] = sorted(observed)
            if ordinal:
                for required_call in (
                    "validate_retained_review_before_processing",
                    "verify_retained_review_inputs",
                    "require_cycle_policy",
                ):
                    need(
                        required_call in observed,
                        "normal_entry_shared_guard." + epoch,
                        required_call,
                        sorted(observed),
                    )
            policies.append(checkpoint["runtime_policy"])
            equal("distinct_runtime_epochs", ordinal + 1, len(set(policies)))
            complete = await complete_rows(compare_connection, protected_tables)
            if ordinal == 0:
                verify_c0_quality(expected_c0_quality, complete["quality_scores"], checkpoint)
                (OUT / "C0-quality-pointer-transition.json").write_text(
                    json.dumps(
                        {
                            "baseline": "actual native rows read before C0; no assumed pointers",
                            "checked_old_rows": len(expected_c0_quality),
                            "native_demotions": quality_transitions,
                            "new_quality_score_id": checkpoint["quality_id"],
                            "all_fields_and_native_json_bytes_verified": True,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                first_checkpoint, first_complete = checkpoint, complete
                equal(
                    "C0_issued_runtime",
                    checkpoint["capsule"]["runtime_policy_sha256"],
                    checkpoint["runtime_policy"],
                )
            else:
                expected = {**first_checkpoint, "runtime_policy": checkpoint["runtime_policy"]}
                equal("reentry.full_native_proof." + epoch, expected, checkpoint)
                equal("reentry.all_protected_rows." + epoch, first_complete, complete)
            (OUT / (epoch + "-native-equality.json")).write_text(
                json.dumps(
                    {
                        "epoch": epoch,
                        "pid": child["pid"],
                        "fresh_import_source_sha256": child["imported_source_sha256"],
                        "ordinary_retained_entry": child["ordinary_retained_entry"],
                        "retained_review_matches_returns": child["retained_review_matches_returns"],
                        "runtime_policy_sha256": checkpoint["runtime_policy"],
                        "issued_runtime_policy_sha256": checkpoint["capsule"][
                            "runtime_policy_sha256"
                        ],
                        "report_id": checkpoint["report_id"],
                        "quality_id": checkpoint["quality_id"],
                        "task_id": checkpoint["task_id"],
                        "observed_normal_entry_functions": sorted(observed),
                        "row_byte_sha256": {
                            table: {identity: sha(row["bytes"]) for identity, row in rows.items()}
                            for table, rows in complete.items()
                        },
                        "physical_byte_sha256": {
                            path: sha(raw) for path, raw in checkpoint["physical"].items()
                        },
                        "comparison": "full typed values and original bytes; hashes audit only",
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
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
        complete_after = await complete_rows(compare_connection)
        equal(
            "final.quality_scores.same_complete_C0_rows",
            first_complete["quality_scores"],
            complete_after["quality_scores"],
        )
        for table in ("execution_operations", "execution_attempts"):
            original_unknown = {
                identity: row
                for identity, row in clone_complete_before[table].items()
                if row["value"].get("status") in {"unknown", "reserved", "pending"}
            }
            latest_unknown = {
                identity: row
                for identity, row in complete_after[table].items()
                if row["value"].get("status") in {"unknown", "reserved", "pending"}
            }
            equal("original_unknown_state." + table, original_unknown, latest_unknown)
        for table in (
            "llm_runs",
            "execution_operations",
            "execution_attempts",
            "review_reports",
            "quality_scores",
            "rewrite_tasks",
            "workflow_runs",
            "book_production_control",
        ):
            expected_old = (
                expected_c0_quality if table == "quality_scores" else clone_complete_before[table]
            )
            equal(
                "original_full_rows." + table,
                expected_old,
                {
                    identity: complete_after[table].get(identity)
                    for identity in clone_complete_before[table]
                },
            )
            expected_hashes = (
                {identity: sha(row["bytes"]) for identity, row in expected_old.items()}
                if table == "quality_scores"
                else clone_before[table]["rows"]
            )
            verify_old_rows(expected_hashes, after[table]["rows"], field=table)
            verify_old_rows(
                clone_before[table]["timestamp_epochs"],
                after[table]["timestamp_epochs"],
                field=table + ".epochs",
            )
        for rel, original in original_files.items():
            equal(
                "old_physical_file." + rel,
                original,
                (root / rel).read_bytes(),
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
            shared_issuer_view_verified=True,
            e05b_complete=True,
            full_report_quality_task_paid_bytes_equal=True,
            c0_quality_pointer_transition_verified=True,
            c0_native_quality_demotions=quality_transitions,
            runtime_policy_epochs=policies,
            normal_entry_functions=entry_calls,
            independent_epoch_pids=entry_pids,
            fresh_imports_per_epoch=True,
            ordinary_retained_match_true_on_both_reentries=True,
            reader_quality_acceptance=False,
        )
    except BaseException as error:
        STATE.failure(error, step=STATE.data.get("failed_workflow_step", "native_runtime"))
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original_transport
        if caught_logger is not None:
            logging.getLogger().removeHandler(caught_logger)
        # C0 can fail after real native reservations/SDK returns. Preserve its
        # observed script count even when it never reached the success handoff.
        observed_calls = OUT / "SDK-CALLS.json"
        if observed_calls.is_file():
            try:
                script.calls = json.loads(observed_calls.read_bytes())
            except BaseException as error:
                STATE.failure(error, step="read_child_sdk_audit")
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
                if original_complete_before is not None:
                    equal(
                        "original_source_full_rows_bytes",
                        original_complete_before,
                        await complete_rows(source_connection),
                    )
                equal("frozen_git_archive_immutable", runtime_baseline, tree_bytes(frozen_root))
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
        if STATE.failed:
            STATE.data["e05b_complete"] = False
            STATE.data["shared_issuer_view_verified"] = False
        STATE.finish(
            final_checks_complete=bool(
                STATE.data.get("candidate_success")
                and STATE.data.get("original_snapshot_unchanged")
                and STATE.data.get("e05b_complete")
            )
        )
    raise SystemExit(0 if STATE.data["status"] == "通过" else 1)


if __name__ == "__main__":
    if ENTRY_EPOCH:
        raise SystemExit(asyncio.run(native_entry()))
    asyncio.run(main())

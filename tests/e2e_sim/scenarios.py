"""Normal HTTP chapter scenarios and independent, read-only native evidence checks.

Only the input prefix is inserted. Review/source/adoption/closure receipts are
never seeded. A missing prerequisite or native rejection is a visible RED.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

SCENARIOS = ("chapter_adoption", "review_length")


def assess(name, data):
    if data.get("harness_errors", 0):
        return "error", "harness_UNMAPPED_or_network_fence"
    if data.get("correlation_complete") is False or (
        data.get("native_sends", 0) > 0 and data.get("correlation_complete") is not True
    ):
        return "error", "native_wire_ledger_correlation_incomplete"
    if data.get("native_sends", 0) > 0 and data.get("task_bound") is not True:
        return "error", "current_task_workflow_binding_required"
    if name == "chapter_adoption":
        if (
            data.get("task_status") == "completed"
            and data.get("adopted")
            and data.get("knowledge_closed")
            and data.get("knowledge", {}).get("bible_complete")
            and data.get("native_sends", 0) > 0
            and data.get("ledger_attempts", 0) > 0
        ):
            return "pass", "native_adoption_and_knowledge_closed"
        return "fail", "native_adoption_or_knowledge_not_reached"
    if name == "review_length":
        if (
            data.get("length_review_runs", 0) > 0
            and data.get("review_unknown")
            and not data.get("adopted")
            and not data.get("knowledge_closed")
            and data.get("native_sends", 0) > 0
            and data.get("ledger_attempts", 0) > 0
            and data.get("rewrite_count", 0) == 0
            and data.get("knowledge_rows", 0) == 0
        ):
            return "pass", "length_review_quarantined"
        return "fail", "length_review_not_reached_or_not_quarantined"
    raise ValueError("scenario_unknown")


def write_junit(path, results):
    root = ET.Element(
        "testsuite",
        name="e2e_sim",
        tests=str(len(results)),
        failures=str(sum(r["status"] == "fail" for r in results)),
        errors=str(sum(r["status"] == "error" for r in results)),
        skipped="0",
    )
    for result in results:
        case = ET.SubElement(
            root,
            "testcase",
            classname="e2e_sim.http",
            name=result["name"],
            time=str(result.get("seconds", 0)),
        )
        if result["status"] != "pass":
            tag = "error" if result["status"] == "error" else "failure"
            ET.SubElement(
                case, tag, message=result["reason"], type=result["reason"]
            ).text = json.dumps(result, ensure_ascii=False, sort_keys=True)
        ET.SubElement(case, "system-out").text = json.dumps(
            result, ensure_ascii=False, sort_keys=True
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def error_summary(error):
    from e2e_sim.diagnostics import safe_exception

    return safe_exception(error)


def require_test_target():
    # Reuse the repository's existing URL safety policy, with a narrower P3 owner.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from support.database_safety import selected_database_url

    url = selected_database_url()
    if not (str(url.database).startswith("par_P3_") and "_test_" in url.database):
        raise RuntimeError("par_P3_test_database_required")
    return url


DOWNSTREAM_NAMES = (
    "ChapterDraftVersionModel",
    "SceneDraftVersionModel",
    "ReviewReportModel",
    "QualityScoreModel",
    "DraftPromotionDecisionModel",
    "RewriteTaskModel",
    "CanonFactModel",
    "TimelineEventModel",
    "ChapterStateSnapshotModel",
    "ExportArtifactModel",
)


class PrefixCheckError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def check_prefix(engine, output):
    """Import exact P2 bundle, verify native reader and prove downstream empty."""
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.infra.db import models as db
    from bestseller.services.fanqie_short_plan_resume import read_existing_complete_fanqie_plan
    from bestseller.settings import get_settings
    from support.planning_prefix_capture import import_prefix

    path = Path(os.environ["E2E_SIM_PREFIX_BUNDLE"])
    bundle = json.loads(path.read_bytes())
    trusted = os.environ["E2E_SIM_PREFIX_SHA256"]
    if bundle.get("project_id") != "fa14e681-2bd9-44f5-93d1-7f2ef9900d69":
        raise PrefixCheckError("prefix_source_project_not_authorized")
    result = {
        "phase": "prefix_import",
        "source_project_id": bundle["project_id"],
        "trusted_bundle_sha256": trusted,
        "invocation": json.loads((output / "invocation.json").read_text()),
    }
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            project = await import_prefix(session, get_settings(), bundle, expected_sha256=trusted)
            await session.commit()
        result["imported"] = True
        result["phase"] = "prefix_native_reader"
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='30s'"))
            project = await session.get(db.ProjectModel, UUID(bundle["project_id"]))
            readback = await read_existing_complete_fanqie_plan(
                session, get_settings(), project=project
            )
            if not readback or readback.get("materialization_reused") is not True:
                raise PrefixCheckError("prefix_native_complete_plan_required")
            result["native_reader_passed"] = True
            result["native_plan_sha256"] = hashlib.sha256(
                json.dumps(
                    json.loads(json.dumps(readback, default=str)),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if result["native_plan_sha256"] != bundle["source_plan_sha256"]:
                raise PrefixCheckError("prefix_native_semantics_changed")
            result["phase"] = "prefix_downstream_empty"
            counts = {
                name: await session.scalar(select(func.count()).select_from(getattr(db, name)))
                for name in DOWNSTREAM_NAMES
            }
            if any(counts.values()):
                raise PrefixCheckError("prefix_downstream_not_empty")
            result.update(
                status="pass",
                downstream_rows=counts,
                downstream_empty=True,
                project_id=str(project.id),
                project_slug=project.slug,
                source_scope_preserved=True,
                chapters=await session.scalar(select(func.count()).select_from(db.ChapterModel)),
            )
    except Exception as error:
        result.update(status="error", **error_summary(error))
        (output / "prefix-check.json").write_text(json.dumps(result, indent=2) + "\n")
        raise
    (output / "prefix-check.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


async def seed_prefix(engine, name, key):
    """Use the preflight-imported original project; create only ephemeral API auth."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.infra.db.models import ApiKeyModel, ChapterModel, ProjectModel

    require_test_target()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        if (
            await session.scalar(text("SELECT current_database()"))
            != require_test_target().database
        ):
            raise PrefixCheckError("prefix_test_database_mismatch")
        project = await session.get(ProjectModel, UUID("fa14e681-2bd9-44f5-93d1-7f2ef9900d69"))
        if project is None:
            raise PrefixCheckError("prefix_preflight_import_required")
        chapter = await session.scalar(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number)
            .limit(1)
        )
        if chapter is None:
            raise PrefixCheckError("prefix_chapter_missing")
        session.add(
            ApiKeyModel(
                name="par_P3_ephemeral_" + name,
                key_hash=hashlib.sha256(key.encode()).hexdigest(),
                is_active=True,
            )
        )
        await session.commit()
        return project.id, chapter.id, project.slug


def reduce_run_evidence(runs, reviews, correlations):
    """Pure row reducer, shared by readback and offline regression fixtures."""
    ids = {str(row["llm_run_id"]) for row in correlations["correlations"]}
    runs = [run for run in runs if str(run.id) in ids]
    reviews = [review for review in reviews if str(review.llm_run_id) in ids]
    return {
        "review_unknown": any(
            r.verdict == "unknown"
            and str(r.llm_run_id)
            in {
                str(run.id)
                for run in runs
                if run.prompt_template == "chapter_review" and run.finish_reason == "length"
            }
            for r in reviews
        ),
        "review_count": len(reviews),
        "review_verdicts": dict(Counter(r.verdict for r in reviews)),
        "length_review_runs": sum(
            r.prompt_template == "chapter_review" and r.finish_reason == "length" for r in runs
        ),
        "llm_runs": [
            {
                "id": str(r.id),
                "template": r.prompt_template,
                "finish_reason": r.finish_reason,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "prompt_hash": r.prompt_hash,
            }
            for r in runs
        ],
    }


async def read_evidence(engine, project_id, chapter_id, events=None):
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.infra.db.models import (
        CanonFactModel,
        ChapterDraftVersionModel,
        ChapterModel,
        ChapterStateSnapshotModel,
        ExecutionAttemptModel,
        LlmRunModel,
        ReviewReportModel,
        RewriteTaskModel,
        TimelineEventModel,
        WorkflowRunModel,
    )
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
    from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
    from e2e_sim.evidence import read_native_correlations

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout='30s'"))
        chapter = await session.get(ChapterModel, chapter_id)
        adopted, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        knowledge = {"closed": False}
        if adopted is not None:
            _, knowledge = await read_adopted_knowledge(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                source_chapter_draft_version_id=adopted.id,
                chapter_md=adopted.content_md,
                require_bible=True,
            )
        runs = list(
            await session.scalars(select(LlmRunModel).where(LlmRunModel.project_id == project_id))
        )
        workflows = list(
            await session.scalars(
                select(WorkflowRunModel).where(WorkflowRunModel.project_id == project_id)
            )
        )
        events = [event for event in (events or []) if event.get("project_id") == str(project_id)]
        correlations = await read_native_correlations(session, project_id, events)
        actual_ids = {row["attempt_id"] for row in correlations["correlations"]}
        attempts = (
            list(
                await session.scalars(
                    select(ExecutionAttemptModel).where(ExecutionAttemptModel.id.in_(actual_ids))
                )
            )
            if actual_ids
            else []
        )
        actual_workflows = {row["workflow_run_id"] for row in correlations["correlations"]}
        workflows = [row for row in workflows if str(row.id) in actual_workflows]
        reviews = list(
            await session.scalars(
                select(ReviewReportModel).where(
                    ReviewReportModel.project_id == project_id,
                    ReviewReportModel.target_type == "chapter",
                    ReviewReportModel.target_id == chapter_id,
                )
            )
        )

        async def count(model):
            return await session.scalar(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            )

        return {
            "correlation": correlations,
            "correlation_complete": correlations["ok"],
            "native_sends": correlations["correlated_send_count"],
            "adopted": adopted is not None,
            "knowledge_closed": knowledge["closed"],
            "knowledge": knowledge,
            "draft_count": await count(ChapterDraftVersionModel),
            **reduce_run_evidence(runs, reviews, correlations),
            "ledger_attempts": len(attempts),
            "attempt_statuses": dict(Counter(a.status for a in attempts)),
            "rewrite_count": await count(RewriteTaskModel),
            "knowledge_rows": await count(CanonFactModel)
            + await count(TimelineEventModel)
            + await count(ChapterStateSnapshotModel),
            "workflow_steps": [
                {
                    "id": str(w.id),
                    "type": w.workflow_type,
                    "status": w.status,
                    "step": w.current_step,
                    **(error_summary(RuntimeError(w.error_message)) if w.error_message else {}),
                }
                for w in workflows
            ],
            "body_sha256": hashlib.sha256(adopted.content_md.encode()).hexdigest()
            if adopted
            else None,
        }


def new_invocation():
    """Bind a child UUID4 to E2E_SIM_RUN_ID (plan.prefix) and the private SHA."""
    run_id = os.environ.get("E2E_SIM_RUN_ID", "")
    trusted = os.environ.get("E2E_SIM_PREFIX_SHA256", "")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", run_id):
        raise PrefixCheckError("invocation_run_id_required")
    if not re.fullmatch(r"[0-9a-f]{64}", trusted):
        raise PrefixCheckError("invocation_private_sha_required")
    return {
        "schema": "e2e-sim-invocation.v1",
        "invocation_id": str(uuid4()),
        "run_id": run_id,
        "trusted_bundle_sha256": trusted,
    }


def _invocation_uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value and UUID(value).version == 4
    except ValueError:
        return False


def load_preflight(path, invocation):
    """Validate a separately supplied receipt, then atomically consume it once."""
    if path is None:
        raise PrefixCheckError("prefix_preflight_path_required")
    if path.is_symlink():
        raise PrefixCheckError("prefix_preflight_symlink")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    source = receipt.get("invocation", {})
    if (
        receipt.get("status") != "pass"
        or receipt.get("downstream_empty") is not True
        or source.get("schema") != "e2e-sim-invocation.v1"
        or not _invocation_uuid(source.get("invocation_id"))
        or source.get("invocation_id") == invocation["invocation_id"]
        or source.get("run_id") != invocation["run_id"]
        or source.get("trusted_bundle_sha256") != invocation["trusted_bundle_sha256"]
        or receipt.get("trusted_bundle_sha256") != invocation["trusted_bundle_sha256"]
    ):
        raise PrefixCheckError("prefix_preflight_invocation_mismatch")
    invocation.update(
        preflight_invocation_id=source["invocation_id"],
        preflight_sha256=hashlib.sha256(raw).hexdigest(),
    )
    try:
        with path.with_suffix(".claim.json").open("x") as stream:
            stream.write(json.dumps(invocation, sort_keys=True) + "\n")
    except FileExistsError:
        raise PrefixCheckError("prefix_preflight_already_consumed") from None
    return receipt


def _log_files(roots):
    paths = set()
    for root in roots:
        if not root.is_dir():
            raise PrefixCheckError("evidence_root_directory_required")
        for pattern in ("*wire*.jsonl", "failures-*.jsonl", "network-*.jsonl"):
            paths.update(root.glob(pattern))
    return paths


def snapshot_logs(roots):
    """Capture offsets before POST, including logs shared by native app/worker."""
    boundary = {}
    for path in _log_files(roots):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise PrefixCheckError("evidence_log_not_regular")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise PrefixCheckError("evidence_log_replaced")
            if opened.st_size:
                stream.seek(opened.st_size - 1)
                if stream.read(1) != b"\n":
                    raise PrefixCheckError("evidence_log_partial_record")
        boundary[path] = (opened.st_dev, opened.st_ino, opened.st_size)
    return boundary


def appended_records(roots, boundary):
    """Only complete JSON records appended to the captured files can be evidence."""
    paths = _log_files(roots)
    if not set(boundary).issubset(paths):
        raise PrefixCheckError("evidence_log_removed")
    records = {"wire": [], "failures": [], "network": []}
    for path in sorted(paths):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise PrefixCheckError("evidence_log_not_regular")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            previous = boundary.get(path, (opened.st_dev, opened.st_ino, 0))
            if (
                (opened.st_dev, opened.st_ino) != previous[:2]
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_size < previous[2]
            ):
                raise PrefixCheckError("evidence_log_replaced_or_truncated")
            stream.seek(previous[2])
            tail = stream.read(opened.st_size - previous[2])
        if tail and not tail.endswith(b"\n"):
            raise PrefixCheckError("evidence_log_partial_record")
        kind = (
            "failures"
            if path.name.startswith("failures-")
            else "network"
            if path.name.startswith("network-")
            else "wire"
        )
        for line in tail.splitlines():
            if line.strip():
                record = json.loads(line)
                if type(record) is not dict:
                    raise PrefixCheckError("evidence_record_object_required")
                records[kind].append(record)
    return records


def bind_task_events(events, task_id, task_data, project_id):
    """Require an explicit task→workflow bridge; never infer it from wire timing."""
    workflow = task_data.get("workflow_run_id")
    if task_data.get("task_id") != task_id or not isinstance(workflow, str):
        raise PrefixCheckError("current_task_workflow_binding_required")
    try:
        if str(UUID(workflow)) != workflow:
            raise ValueError()
    except ValueError:
        raise PrefixCheckError("current_task_workflow_binding_required") from None
    return [
        event
        for event in events
        if event.get("project_id") == str(project_id)
        and event.get("workflow_run_id") == workflow
        and all(event.get(key, task_id) == task_id for key in ("task_id", "job_id"))
    ]


class HTTPReply:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http_status_" + str(self.status_code))

    def json(self):
        return json.loads(self.body)


class HTTPClient:
    """Independent real HTTP client: never intercepted as a model SDK send."""

    def __init__(self, api):
        from urllib.parse import urlsplit

        parsed = urlsplit(api)
        if parsed.scheme != "http" or parsed.username or parsed.password:
            raise ValueError("explicit_internal_api_required")
        self.host, self.port = parsed.hostname, parsed.port

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def request(self, method, path, headers, body=None):
        import http.client

        def send():
            connection = http.client.HTTPConnection(self.host, self.port, timeout=15)
            try:
                wire = json.dumps(body).encode() if body is not None else None
                connection.request(
                    method, path, body=wire, headers={**headers, "Content-Type": "application/json"}
                )
                reply = connection.getresponse()
                return HTTPReply(reply.status, reply.read())
            finally:
                connection.close()

        return await asyncio.to_thread(send)

    async def post(self, path, *, headers, json):
        return await self.request("POST", path, headers, json)

    async def get(self, path, *, headers):
        return await self.request("GET", path, headers)


async def run(
    output,
    api,
    timeout,
    scenario="chapter_adoption",
    prefix_only=False,
    *,
    preflight=None,
    evidence_root=None,
):
    from urllib.parse import urlsplit

    from e2e_sim.network_fence import install_fence
    from e2e_sim.transport import NativeTemplateObserver, Tape, install_scripted_http

    target = urlsplit(api)
    install_fence(
        {(os.environ["E2E_SIM_PG_IP"], 5432), (os.environ["E2E_SIM_REDIS_IP"], 6379)},
        output / "network-driver.jsonl",
        http_endpoint=None if prefix_only else (target.hostname, target.port),
    )
    tape = Tape.from_file(
        os.environ["E2E_SIM_TAPE"],
        audit_path=output / "wire-driver.jsonl",
        template_observer=NativeTemplateObserver(),
    )
    handle = install_scripted_http(tape)
    from bestseller.infra.db.session import create_engine
    from bestseller.settings import get_settings

    require_test_target()
    engine = create_engine(get_settings())
    results = []
    try:
        if prefix_only:
            await check_prefix(engine, output)
            return 0
        if (
            not preflight
            or preflight.get("status") != "pass"
            or not preflight.get("downstream_empty")
        ):
            raise PrefixCheckError("prefix_preflight_pass_required")
        async with HTTPClient(api) as client:
            for name in (scenario,):
                started = time.monotonic()
                result = {"name": name}
                try:
                    key = secrets.token_urlsafe(32)  # memory only; only SHA is inserted
                    pid, cid, slug = await seed_prefix(engine, name, key)
                    headers = {"Authorization": "Bearer " + key}
                    roots = tuple(dict.fromkeys((evidence_root or output.parent, output)))
                    boundary = snapshot_logs(roots)  # strictly before this invocation's POST
                    (output / "wire-boundary.json").write_text(
                        json.dumps(
                            {
                                "invocation": json.loads((output / "invocation.json").read_text()),
                                "offsets": [
                                    {
                                        "file": str(
                                            path.relative_to(evidence_root or output.parent)
                                        ),
                                        "device": item[0],
                                        "inode": item[1],
                                        "offset": item[2],
                                    }
                                    for path, item in sorted(boundary.items())
                                ],
                            },
                            indent=2,
                        )
                        + "\n"
                    )
                    response = await client.post(
                        f"/api/v1/projects/{slug}/chapters/1/pipeline",
                        headers=headers,
                        json={"chapter_first": True},
                    )
                    result["enqueue_http_status"] = response.status_code
                    response.raise_for_status()
                    task = response.json()["task_id"]
                    result["task_id"] = task
                    deadline = time.monotonic() + timeout
                    while True:
                        reply = await client.get("/api/v1/tasks/" + task, headers=headers)
                        reply.raise_for_status()
                        task_data = reply.json()
                        if task_data["status"] not in {
                            "queued",
                            "running",
                            "in_progress",
                            "pending",
                            "deferred",
                        }:
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("scenario_deadline_exceeded")
                        await asyncio.sleep(0.5)
                    result["task_status"] = task_data["status"]
                    if task_data.get("error"):
                        # Transported error strings have no original traceback. Preserve
                        # only their hash; actual exception frames come from worker logging.
                        result["task_error"] = error_summary(RuntimeError(task_data["error"]))
                    records = appended_records(roots, boundary)
                    events = bind_task_events(records["wire"], task, task_data, pid)
                    result.update(task_bound=True, workflow_run_id=task_data["workflow_run_id"])
                    result.update(await read_evidence(engine, pid, cid, events))
                    result["harness_errors"] = sum(
                        e.get("event") == "harness_UNMAPPED"
                        and e.get("project_id") in {None, str(pid)}
                        for e in records["wire"]
                    )
                    result["native_failures"] = records["failures"]
                    result["status"], result["reason"] = assess(name, result)
                    if result["status"] == "fail" and result["native_failures"]:
                        result["business_stop_with_safe_stack"] = any(
                            item.get("safe_stack") and item.get("error_codes")
                            for item in result["native_failures"]
                        )
                    if records["network"]:
                        result.update(status="error", reason="harness_network_fence")
                except Exception as error:
                    result.update(
                        status="error",
                        reason=error.code
                        if isinstance(error, PrefixCheckError)
                        else "scenario_driver_or_infrastructure",
                        **error_summary(error),
                    )
                result["seconds"] = round(time.monotonic() - started, 3)
                results.append(result)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                (output / "results.json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2) + "\n"
                )
                write_junit(output / "junit.xml", results)
    finally:
        await engine.dispose()
        handle.uninstall()
    return 0 if all(r["status"] == "pass" for r in results) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, help="New one-shot metadata directory; must not already exist"
    )
    parser.add_argument(
        "--preflight",
        type=Path,
        help="Separate prefix receipt; same run ID/private SHA, consumed once",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        help="Shared native app/worker log root (default: output parent)",
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--write-tape", type=Path)
    parser.add_argument("--prefix-only", action="store_true")
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default=os.environ.get("E2E_SIM_SCENARIO", "chapter_adoption"),
    )
    args = parser.parse_args()
    if args.write_tape:
        from e2e_sim.tapes import write_tape

        write_tape(args.write_tape, scenario=args.scenario)
        return 0
    if args.output is None:
        parser.error("--output is required except with --write-tape")
    args.output = args.output.absolute()
    try:
        args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        print(json.dumps({"phase": "output_allocation", "reason": "output_already_exists"}))
        return 2
    try:
        invocation = new_invocation()
        evidence_root = (args.evidence_root or args.output.parent).resolve(strict=True)
        args.output = args.output.resolve(strict=True)
        if not evidence_root.is_dir() or not args.output.is_relative_to(evidence_root):
            raise PrefixCheckError("output_must_be_under_evidence_root")
        (args.output / "invocation.json").write_text(json.dumps(invocation, indent=2) + "\n")
        preflight = None if args.prefix_only else load_preflight(args.preflight, invocation)
        if preflight:
            (args.output / "invocation.json").write_text(json.dumps(invocation, indent=2) + "\n")
        return asyncio.run(
            run(
                args.output,
                args.api,
                args.timeout,
                args.scenario,
                args.prefix_only,
                preflight=preflight,
                evidence_root=evidence_root,
            )
        )
    except Exception as error:
        proof = {"phase": "prefix_or_driver", **error_summary(error)}
        if isinstance(error, PrefixCheckError):
            proof["reason"] = error.code
        (args.output / "driver-safe-error.json").write_text(json.dumps(proof, indent=2) + "\n")
        print(json.dumps(proof, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

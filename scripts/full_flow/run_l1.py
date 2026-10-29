"""Main-only normal HTTP/SDK L1 driver; first native chapter adoption is the gate.

From int-worktree, AFTER final source/harness commits and authoring review:
  ../../.venv/bin/python scripts/full_flow/run_l1.py \
    --authoring-root /absolute/private/complete-authoring \
    --authorization-id <fixed-this-book-id> --actor <actual-executor> \
    --authorization-ref <existing-user-authorization-receipt>

For an explicitly prepared finite v3 prefix, add --allow-finite-prefix.
This admits bound private inputs without claiming full authoring coverage;
missing replies still stop normally and success still requires native adoption.

No automatic resume, replacement POST, grant renewal, skip, or image pull.
Source and authoring copies, SDK bytes and failures stay in a new private root.
Only this file is new; normal web_entry, grants, SDK and adoption guards are used.
This script's existence/static validation is NOT full L1 readiness or a PASS.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import traceback
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

APP_IMAGE = "sha256:9685577fa4d1a017afabd1d37de48903631c35c1910479702ec2856dc93bb797"
SOURCE_ROOTS = (
    "src",
    "config",
    "resources",
    "templates",
    "migrations",
    "tests/e2e_sim",
    "scripts/full_flow",
    "pyproject.toml",
    "README.md",
    "alembic.ini",
)
TERMINAL = {"failed", "cancelled", "incomplete", "machine_blocked", "completed"}


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def operand(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    )
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, bytes, dict, list)) else None,
        "sha256": digest(raw),
    }


def require(ok, code, field, expected, actual):
    if not ok:
        error = RuntimeError(code)
        error.code = code
        error.differences = [
            {"field": field, "expected": operand(expected), "actual": operand(actual)}
        ]
        raise error


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "L1_DUPLICATE_JSON_KEY", "json.key", "unique", key)
            value[key] = item
        return value

    def nonfinite(value):
        require(False, "L1_NONFINITE_JSON", "json.number", "finite", value)

    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique, parse_constant=nonfinite)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(canonical(value) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def failure(error, stage, private):
    raw_code = getattr(error, "code", str(error))
    code = (
        raw_code
        if isinstance(raw_code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,160}", raw_code)
        else "L1_STAGE_FAILED"
    )
    differences = []
    for row in getattr(error, "differences", []):
        if not isinstance(row, dict):
            continue
        cleaned = {"field": str(row.get("field", stage))[:180]}
        for key in ("expected", "actual"):
            item = row.get(key)
            cleaned[key] = (
                {name: item[name] for name in ("type", "length", "sha256")}
                if isinstance(item, dict) and {"type", "length", "sha256"} <= item.keys()
                else operand(item)
            )
        differences.append(cleaned)
    trace = Path(private) / (stage + "-traceback.txt")
    trace.write_text("".join(traceback.format_exception(error)))
    trace.chmod(0o600)
    return {
        "stage": stage,
        "root_error_code": code,
        "root_exception_type": type(error).__name__,
        "differences": differences
        or [{"field": stage, "expected": operand("success"), "actual": operand(str(error))}],
        "safe_stack": [
            {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
            for f in traceback.extract_tb(error.__traceback__)
        ],
        "private_traceback": str(trace),
    }


def stage_record(private, stage, **values):
    row = {"stage": stage, "at_utc": datetime.now(timezone.utc).isoformat(), **values}
    with (Path(private) / "stages.jsonl").open("a") as stream:
        stream.write(canonical(row) + "\n")


def _finite_prefix_integrity(root, file_hashes, file_sizes):
    documents = {}
    for name, schema in (
        ("readiness.json", "finite-firstchapter-prefix-readiness.v3"),
        ("manifest.json", "private-finite-prefix-manifest.v3"),
    ):
        require(name in file_hashes, "L1_AUTHORING_INCOMPLETE", "authoring." + name, True, False)
        value = read_json(root / name)
        require(
            type(value) is dict, "L1_FINITE_PREFIX_SHAPE", "authoring." + name, "dict", value
        )
        for field, expected in (("schema", schema), ("state", "FINITE_PREFIX_PREPARED")):
            require(
                value.get(field) == expected,
                "L1_FINITE_PREFIX_INVALID",
                "authoring." + name + "." + field,
                expected,
                value.get(field),
            )
        documents[name] = value
    readiness = documents["readiness.json"]
    require(
        readiness.get("full_firstchapter_ready") is False,
        "L1_FINITE_PREFIX_INVALID",
        "authoring.readiness.full_firstchapter_ready",
        False,
        readiness.get("full_firstchapter_ready"),
    )
    for field, expected in (
        ("config_sha256", digest(canonical(read_json(root / "script.json")).encode())),
        ("script_sha256", file_hashes["script.json"]),
    ):
        require(
            readiness.get(field) == expected,
            "L1_AUTHORING_CONFIG_CHANGED",
            "authoring.readiness." + field,
            expected,
            readiness.get(field),
        )
    files = documents["manifest.json"].get("files")
    require(
        type(files) is dict, "L1_FINITE_PREFIX_SHAPE", "authoring.manifest.files", "dict", files
    )
    expected_names = sorted(set(file_hashes) - {"manifest.json"})
    require(
        sorted(files) == expected_names,
        "L1_AUTHORING_INVENTORY_CHANGED",
        "authoring.manifest.files",
        expected_names,
        sorted(files),
    )
    for name, record in files.items():
        field = "authoring.manifest.files." + name
        require(type(record) is dict, "L1_FINITE_PREFIX_SHAPE", field, "dict", record)
        require(
            record.get("sha256") == file_hashes[name],
            "L1_AUTHORING_ARTIFACT_CHANGED",
            field + ".sha256",
            file_hashes[name],
            record.get("sha256"),
        )
        require(
            type(record.get("bytes")) is int and record["bytes"] == file_sizes[name],
            "L1_AUTHORING_ARTIFACT_CHANGED",
            field + ".bytes",
            file_sizes[name],
            record.get("bytes"),
        )


def private_tree(root, *, allow_finite_prefix: bool = False):
    require(
        type(allow_finite_prefix) is bool,
        "L1_FINITE_PREFIX_FLAG_INVALID",
        "authoring.allow_finite_prefix",
        "bool",
        allow_finite_prefix,
    )
    root = Path(root).absolute()
    require(
        root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
        "L1_PRIVATE_ROOT_INVALID",
        "authoring.root",
        "regular private directory",
        str(root),
    )
    require(
        not any((p / ".git").exists() for p in (root, *root.parents)),
        "L1_AUTHORING_INSIDE_CHECKOUT",
        "authoring.root",
        "outside checkout",
        str(root),
    )
    result, file_sizes = {}, {}
    for path in (root, *sorted(root.rglob("*"))):
        require(
            not path.is_symlink() and (path.is_dir() or path.is_file()),
            "L1_AUTHORING_SPECIAL_FILE",
            "authoring.entry",
            "regular",
            path.name,
        )
        mode = stat.S_IMODE(path.stat().st_mode)
        require(
            mode == (0o700 if path.is_dir() else 0o600),
            "L1_AUTHORING_MODE",
            "authoring.mode",
            0o700 if path.is_dir() else 0o600,
            mode,
        )
        if path.is_file():
            require(
                path.suffix not in {".env", ".pem", ".key", ".curl"},
                "L1_AUTHORING_SECRET_CONFIG",
                "authoring.file",
                "authoring data",
                path.name,
            )
            require(
                path.name != "physical-ordinals.json",
                "L1_USED_AUTHORING_ROOT",
                "authoring.checkpoint",
                "fresh input; never reset checkpoint",
                path.name,
            )
            name = str(path.relative_to(root))
            raw = path.read_bytes()
            result[name] = digest(raw)
            file_sizes[name] = len(raw)
    for name in ("script.json", "quickstart.request.json"):
        require(name in result, "L1_AUTHORING_INCOMPLETE", "authoring." + name, True, False)
    if allow_finite_prefix and "authoring-status.json" not in result:
        _finite_prefix_integrity(root, result, file_sizes)
        return result
    require(
        "authoring-status.json" in result,
        "L1_AUTHORING_INCOMPLETE",
        "authoring.authoring-status.json",
        True,
        False,
    )
    authoring = read_json(root / "authoring-status.json")
    require(
        type(authoring) is dict, "L1_AUTHORING_STATUS_SHAPE", "authoring.status", "dict", authoring
    )
    require(
        authoring.get("state") == "STRUCTURE_READY_FOR_MAIN_REVIEW"
        and authoring.get("blockers") == [],
        "L1_AUTHORING_STRUCTURALLY_BLOCKED",
        "authoring.compiler",
        {"state": "STRUCTURE_READY_FOR_MAIN_REVIEW", "blockers": []},
        {"state": authoring.get("state"), "blockers": authoring.get("blockers")},
    )
    # This is input integrity only, never a native quality/adoption assertion.
    config_hash = digest(canonical(read_json(root / "script.json")).encode())
    require(
        authoring.get("config_sha256") == config_hash,
        "L1_AUTHORING_CONFIG_CHANGED",
        "authoring.config_sha256",
        authoring.get("config_sha256"),
        config_hash,
    )
    for name, expected in authoring.get("artifact_sha256", {}).items():
        require(
            result.get(name) == expected,
            "L1_AUTHORING_ARTIFACT_CHANGED",
            "authoring.artifact." + name,
            expected,
            result.get(name),
        )
    return result


def frozen_copy(root, out):
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=180)
        require(result.returncode == 0, "L1_GIT_FAILED", "git." + args[0], 0, result.returncode)
        return result.stdout

    head = git("rev-parse", "HEAD").decode().strip()
    paths = [p for p in SOURCE_ROOTS if git("ls-tree", "--name-only", head, "--", p).strip()]
    require(
        {"src", "config", "migrations", "tests/e2e_sim", "scripts/full_flow"} <= set(paths),
        "L1_FROZEN_ROOTS_MISSING",
        "source.roots",
        SOURCE_ROOTS,
        paths,
    )
    changed = git("diff", "--name-only", head, "--", *paths)
    untracked = git("ls-files", "--others", "--exclude-standard", "--", *paths)
    require(
        not changed and not untracked,
        "L1_RUNTIME_NOT_COMMITTED",
        "source.drift",
        b"",
        changed + untracked,
    )
    archive = out / "source.tar"
    git("archive", "--format=tar", "--output=" + str(archive), head, *paths)
    destination = out / "source"
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, "r:") as source:
        for member in source:
            name = PurePosixPath(member.name)
            require(
                not name.is_absolute()
                and ".." not in name.parts
                and (member.isdir() or member.isfile()),
                "L1_ARCHIVE_ENTRY_INVALID",
                "source.entry",
                "relative regular entry",
                member.name,
            )
            path = destination / str(name)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if member.isdir():
                path.mkdir(mode=0o700, exist_ok=True)
            else:
                with source.extractfile(member) as src, path.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                path.chmod(0o600 | (member.mode & 0o111))
    require(
        (destination / "scripts/full_flow/run_l1.py").is_file(),
        "L1_DRIVER_NOT_COMMITTED",
        "source.driver",
        True,
        False,
    )
    files = {
        str(p.relative_to(destination)): digest(p.read_bytes())
        for p in sorted(destination.rglob("*"))
        if p.is_file()
    }
    return destination, {
        "head": head,
        "archive_sha256": digest(archive.read_bytes()),
        "files": files,
    }


def write_env(path, values):
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        for key, value in values.items():
            require(
                isinstance(value, str) and "\n" not in value and "\r" not in value,
                "L1_ENV_INVALID",
                "env." + key,
                "single line",
                operand(value),
            )
            stream.write(key + "=" + value + "\n")


async def prepare(private, engine):
    from bestseller.services.execution_authorization import prepare_new_book_budget
    from bestseller.services.model_catalog import get_model_catalog_entry
    from bestseller.web.server import _validate_quickstart_client_schema

    original = read_json(private / "authoring/quickstart.request.json")
    require(type(original) is dict, "L1_REQUEST_SHAPE", "request", "dict", type(original).__name__)
    _validate_quickstart_client_schema(original)
    entry = get_model_catalog_entry(original.get("llm_model_id"))
    require(
        entry is not None and entry.available and entry.api_base,
        "L1_NATIVE_ROUTE_UNAVAILABLE",
        "model.catalog",
        "available entry",
        entry is not None,
    )
    require(
        entry.api_base.rstrip("/") == "https://api.minimaxi.com/v1",
        "L1_SCRIPTED_ROUTE_UNCOVERED",
        "model.endpoint",
        "https://api.minimaxi.com/v1",
        entry.api_base,
    )
    authorization = read_json(private / "authorization.json")
    grant = await prepare_new_book_budget(
        engine,
        payload=deepcopy(original),
        model=entry.model,
        endpoint=entry.api_base,
        **authorization,
    )
    require(
        grant["max_calls"] == 400 and grant["max_tokens"] == 20_000_000,
        "L1_GRANT_CAP_MISMATCH",
        "grant.caps",
        [400, 20_000_000],
        [grant["max_calls"], grant["max_tokens"]],
    )
    require(
        grant["payload_sha256"] == digest(canonical(original).encode()),
        "L1_FULL_POST_BINDING_MISMATCH",
        "grant.payload_sha256",
        digest(canonical(original).encode()),
        grant["payload_sha256"],
    )
    require(
        grant["grant_id"] == grant["task_id"]
        and grant["scope_id"] == "conception:" + grant["task_id"],
        "L1_GRANT_IDENTITY_MISMATCH",
        "grant.identity",
        "native task/scope",
        grant,
    )
    save(private / "original-post.json", original)
    save(private / "grant.json", grant)
    save(
        private / "submitted-post.json",
        {**original, "execution_budget_grant_id": grant["grant_id"]},
    )
    return grant


async def native_state(engine, grant, task):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from bestseller.infra.db import models as m
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
    from bestseller.services.execution_authorization import new_book_budget_policy
    from bestseller.services.feedback import validate_adopted_knowledge_source

    async with async_sessionmaker(engine, autoflush=False, expire_on_commit=False)() as session:
        budget = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        require(budget is not None, "L1_NATIVE_BUDGET_MISSING", "budget", True, False)
        policy = new_book_budget_policy(budget)
        require(
            policy is not None and canonical(policy["binding"]) == canonical(grant),
            "L1_NATIVE_GRANT_CHANGED",
            "budget.binding",
            grant,
            policy["binding"] if policy is not None else None,
        )
        require(
            policy["consumed_at"] is not None,
            "L1_GRANT_NOT_CONSUMED",
            "budget.consumed_at",
            "native HTTP consumption",
            policy["consumed_at"],
        )
        result = {
            "budget": {
                "calls_used": budget.calls_used,
                "tokens_charged": budget.tokens_charged,
                "status": budget.status,
                "max_calls": budget.max_calls,
                "max_tokens": budget.max_tokens,
                "deadline_at": budget.deadline_at.isoformat(),
            },
            "adoption": None,
        }
        result["unknown_attempts"] = await session.scalar(
            select(func.count())
            .select_from(m.ExecutionAttemptModel)
            .where(
                m.ExecutionAttemptModel.scope_id == grant["scope_id"],
                m.ExecutionAttemptModel.status == "unknown",
            )
        )
        result["unknown_operations"] = await session.scalar(
            select(func.count())
            .select_from(m.ExecutionOperationModel)
            .where(
                m.ExecutionOperationModel.scope_id == grant["scope_id"],
                m.ExecutionOperationModel.status == "unknown",
            )
        )
        slug = task.get("project_slug")
        if not isinstance(slug, str) or not slug:
            return result
        project = await session.scalar(select(m.ProjectModel).where(m.ProjectModel.slug == slug))
        if project is None:
            return result
        require(
            (project.metadata_json or {}).get("execution_scope_id") == grant["scope_id"],
            "L1_PROJECT_SCOPE_MISMATCH",
            "project.execution_scope_id",
            grant["scope_id"],
            (project.metadata_json or {}).get("execution_scope_id"),
        )
        chapter = await session.scalar(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == project.id, m.ChapterModel.chapter_number == 1
            )
        )
        if chapter is None:
            return result
        draft, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        if draft is None:
            return result
        lineage, rejection = await validate_adopted_knowledge_source(
            session,
            project_id=project.id,
            chapter_id=chapter.id,
            source_chapter_draft_version_id=draft.id,
            chapter_md=draft.content_md,
            lock=False,
        )
        require(
            rejection is None, "L1_NATIVE_ADOPTION_REJECTED", "adoption.rejection", None, rejection
        )
        result["adoption"] = {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "draft_id": str(draft.id),
            "chapter_number": 1,
            "body_sha256": lineage["source_body_sha256"],
            "body_characters": len(draft.content_md),
        }
        return result


def http(private, method, path, name, body=None):
    # urllib has no automatic retry; proxies are explicitly disabled.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = Request(
        "http://127.0.0.1:8787" + path,
        data=canonical(body).encode() if body is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Web-Token": os.environ["BESTSELLER_WEB_TOKEN"],
        },
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    file = private / "http" / (name + ".json")
    file.parent.mkdir(mode=0o700, exist_ok=True)
    file.write_bytes(raw)
    file.chmod(0o600)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        value = {"unparsed_body": operand(raw)}
    require(type(value) is dict, "L1_HTTP_JSON_SHAPE", "http." + name, "object", value)
    return status, value


def script_failure(private):
    path = private / "authoring/scripted-sdk-audit.jsonl"
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # A concurrent final line may be incomplete.
        if row.get("event") == "harness_UNMAPPED":
            code = row.get("reason", "SCRIPT_TEMPLATE_UNCOVERED")
            require(
                False,
                code,
                "sdk.template",
                "explicit authored finite reply",
                {"template": row.get("prompt_template"), "ordinal": row.get("call_index")},
            )
    return None


def terminal_failure(task):
    """Preserve native error identity without publishing story/error prose."""
    detail = task.get("error")
    code = "L1_TASK_TERMINAL_BEFORE_ADOPTION"
    if isinstance(detail, str) and re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", detail):
        code = detail
    try:
        require(False, code, "task.error", None, detail)
    except RuntimeError as error:
        error.native_task_status = task.get("status")
        if isinstance(detail, str):
            error.native_safe_stack = [
                {"file": Path(name).name, "line": int(line), "function": function}
                for name, line, function in re.findall(
                    r'File "([^"\n]+)", line (\d+), in ([A-Za-z_][A-Za-z0-9_.<>]*)', detail
                )
            ]
            last = detail.strip().splitlines()[-1:] or [""]
            match = re.match(r"([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)): ", last[0])
            if match:
                error.native_exception_type = match[1]
        raise


async def inside(args):
    private = Path("/private")
    evidence = private / "evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    result = {
        "status": "失败",
        "l1_first_chapter_adopted": False,
        "full_book_complete": False,
        "formal_exports_verified": False,
        "real_provider_calls": 0,
        "simulation": "normal HTTP with scripted SDK responses",
    }
    stage, server, server_log, engine, grant = "validate_environment", None, None, None, None
    try:
        from scripts.full_flow.web_entry import verify_environment

        allowed = verify_environment()
        database = urlsplit(os.environ["BESTSELLER__DATABASE__URL"]).path.lstrip("/")
        require(
            bool(re.fullmatch(r"full_flow_l1_[a-f0-9]+_test_db", database)),
            "L1_FOREIGN_DATABASE",
            "database",
            "unique L1 test DB",
            database,
        )

        def guard(event, values):
            if event == "socket.connect":
                address = values[1]
                require(
                    isinstance(address, tuple)
                    and (
                        (address[0], address[1]) in allowed
                        or ipaddress.ip_address(address[0]).is_loopback
                    ),
                    "L1_EXTERNAL_NETWORK_DENIED",
                    "network.destination",
                    "own services",
                    address,
                )

        sys.addaudithook(guard)
        import yaml

        for row in yaml.safe_load(Path("config/model_catalog.yaml").read_bytes()).get("models", []):
            key = row.get("api_key_env")
            if isinstance(key, str):
                os.environ.pop(key, None)
        # Do not make other shadow providers available merely for this driver.
        os.environ["MINIMAX_API_KEY"] = "l1-scripted-sdk-dummy-not-real"
        stage = "native_migrations"
        stage_record(evidence, stage)
        with (evidence / "migrations.log").open("w") as log:
            migrated = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        require(
            migrated.returncode == 0,
            "L1_MIGRATION_FAILED",
            "migration.exit",
            0,
            migrated.returncode,
        )
        from bestseller.infra.db.session import create_engine
        from bestseller.settings import load_settings

        settings = load_settings()
        engine = create_engine(settings)
        stage = "native_prepare_grant"
        stage_record(evidence, stage)
        grant = await prepare(private, engine)
        result.update(task_id=grant["task_id"], scope_id=grant["scope_id"], database=database)
        stage = "normal_web_start"
        stage_record(evidence, stage)
        server_log = (evidence / "web.log").open("w")
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "scripts.full_flow.web_entry",
                "--mode",
                "simulation",
                "--private-root",
                "/private/authoring",
                "--config",
                "/private/authoring/script.json",
                "--run-id",
                args.run_id,
                "--port",
                "8787",
            ],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + min(120, args.timeout_seconds)
        while True:
            require(server.poll() is None, "L1_WEB_EXITED", "web.exit", None, server.poll())
            try:
                with socket.create_connection(("127.0.0.1", 8787), timeout=1):
                    break
            except OSError:
                require(
                    time.monotonic() < deadline, "L1_WEB_START_TIMEOUT", "web.ready", True, False
                )
                await asyncio.sleep(0.25)
        stage = "normal_quickstart_post"
        stage_record(evidence, stage)
        posted = False
        try:
            status, task = await asyncio.to_thread(
                http,
                private,
                "POST",
                "/api/tasks/quickstart",
                "created",
                read_json(private / "submitted-post.json"),
            )
            if status == 202:
                require(
                    task.get("task_id") == grant["task_id"],
                    "L1_POST_TASK_ID_MISMATCH",
                    "http.202.task_id",
                    grant["task_id"],
                    task.get("task_id"),
                )
            posted = status == 202 and task.get("task_id") == grant["task_id"]
        except (OSError, TimeoutError):
            status, task = None, {}
        # Lost/500 POST receipt cannot imply no creation. Observe only the native ID.
        if not posted:
            original_status = status
            status, task = await asyncio.to_thread(
                http, private, "GET", "/api/tasks/" + grant["task_id"], "post-recovery-task"
            )
            await asyncio.to_thread(
                http,
                private,
                "GET",
                "/api/tasks/" + grant["task_id"] + "/execution",
                "post-recovery-execution",
            )
            require(
                status == 200 and task.get("task_id") == grant["task_id"],
                "L1_POST_OUTCOME_UNRESOLVED",
                "http.post_and_identity_readback",
                {"task_id": grant["task_id"], "readback_status": 200},
                {
                    "post_status": original_status,
                    "readback_status": status,
                    "task_id": task.get("task_id"),
                },
            )
        stage = "poll_native_first_adoption"
        stage_record(evidence, stage)
        deadline, index = time.monotonic() + args.timeout_seconds, 0
        while time.monotonic() < deadline:
            script_failure(private)
            require(server.poll() is None, "L1_WEB_EXITED", "web.exit", None, server.poll())
            status, task = await asyncio.to_thread(
                http, private, "GET", "/api/tasks/" + grant["task_id"], f"task-{index:05d}"
            )
            require(
                status == 200 and task.get("task_id") == grant["task_id"],
                "L1_TASK_IDENTITY_CHANGED",
                "task.identity",
                grant["task_id"],
                task.get("task_id"),
            )
            if index % 5 == 0:
                await asyncio.to_thread(
                    http,
                    private,
                    "GET",
                    "/api/tasks/" + grant["task_id"] + "/execution",
                    f"execution-{index:05d}",
                )
            state = await native_state(engine, grant, task)
            save(evidence / "native-state.json", state)
            script_failure(private)
            require(
                state["unknown_attempts"] == state["unknown_operations"] == 0,
                "L1_NATIVE_UNKNOWN",
                "ledger.unknown",
                [0, 0],
                [state["unknown_attempts"], state["unknown_operations"]],
            )
            if state["adoption"] is not None:
                result.update(l1_first_chapter_adopted=True, native_state=state)
                break
            if task.get("status") in TERMINAL:
                terminal_failure(task)
            index += 1
            await asyncio.sleep(0.5)
        require(
            result["l1_first_chapter_adopted"],
            "L1_FIRST_ADOPTION_TIMEOUT",
            "chapter1.adopted",
            True,
            False,
        )
        stage = "normal_task_stop"
        stage_record(evidence, stage)
        status, _ = await asyncio.to_thread(
            http, private, "POST", "/api/tasks/" + grant["task_id"] + "/cancel", "stop", {}
        )
        require(
            status in {200, 409}, "L1_NORMAL_STOP_FAILED", "http.cancel.status", [200, 409], status
        )
        for index in range(60):
            script_failure(private)
            status, task = await asyncio.to_thread(
                http, private, "GET", "/api/tasks/" + grant["task_id"], f"stopping-{index:03d}"
            )
            if status == 200 and task.get("status") in TERMINAL:
                break
            await asyncio.sleep(0.5)
        require(
            status == 200 and task.get("status") in TERMINAL,
            "L1_TASK_STOP_UNCONFIRMED",
            "task.status",
            "terminal after normal stop",
            task.get("status"),
        )
        final = await native_state(engine, grant, task)
        script_failure(private)
        require(
            final["unknown_attempts"] == final["unknown_operations"] == 0,
            "L1_NATIVE_UNKNOWN",
            "ledger.unknown_after_stop",
            [0, 0],
            [final["unknown_attempts"], final["unknown_operations"]],
        )
        require(
            final["adoption"] == result["native_state"]["adoption"],
            "L1_ADOPTION_CHANGED_DURING_STOP",
            "chapter1.adoption",
            result["native_state"]["adoption"],
            final["adoption"],
        )
        result.update(status="通过", native_state=final, original_task_stopped=True)
    except BaseException as error:
        primary_error = error
        try:
            script_failure(private)
        except RuntimeError as script_error:
            primary_error = script_error
        result.update(failure(primary_error, stage, evidence))
        for key in ("native_safe_stack", "native_exception_type", "native_task_status"):
            if hasattr(primary_error, key):
                result[key] = getattr(primary_error, key)
    finally:
        try:
            if server is not None and server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait(timeout=10)
        except BaseException as error:
            cleanup_failure = failure(error, "web_cleanup", evidence)
            if result["status"] == "通过":
                result.update(cleanup_failure)
            result["status"] = "失败"
            result.setdefault("secondary_failures", []).append(cleanup_failure)
        result["web_process_exited"] = server is None or server.poll() is not None
        if server_log is not None:
            server_log.close()
        if engine is not None:
            try:
                await asyncio.wait_for(engine.dispose(), timeout=10)
            except BaseException as error:
                cleanup_failure = failure(error, "engine_cleanup", evidence)
                if result["status"] == "通过":
                    result.update(cleanup_failure)
                result["status"] = "失败"
                result.setdefault("secondary_failures", []).append(cleanup_failure)
        save(evidence / "RESULT.json", result)
    return 0 if result["status"] == "通过" else 1


def host(args):
    from isolation import DOCKER, OWNER, PRIVATE, REPORT, ROOT, preflight

    label = (
        "l1-normal-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + secrets.token_hex(4)
    )
    out = PRIVATE / label
    out.mkdir(mode=0o700, parents=True)
    data, evidence = out / "data", out / "data/evidence"
    evidence.mkdir(mode=0o700, parents=True)
    row = {
        "status": "失败",
        "l1_first_chapter_adopted": False,
        "real_provider_calls": 0,
        "full_book_complete": False,
        "formal_exports_verified": False,
    }
    names, stage = [], "host_preflight"

    def docker(*parts, check=True, timeout=60):
        result = subprocess.run([DOCKER, *parts], capture_output=True, text=True, timeout=timeout)
        if check:
            require(
                result.returncode == 0,
                "L1_DOCKER_FAILED",
                "docker." + parts[0],
                0,
                {"exit": result.returncode, "stderr": operand(result.stderr)},
            )
        return result

    try:
        require(
            ROOT.name == "int-worktree", "L1_WRONG_WORKTREE", "worktree", "int-worktree", ROOT.name
        )
        for key in ("authoring_root", "authorization_id", "actor", "authorization_ref"):
            require(
                bool(getattr(args, key)), "L1_ARGUMENT_REQUIRED", "argument." + key, True, False
            )
        require(
            1 <= args.timeout_seconds <= 86400,
            "L1_TIMEOUT_INVALID",
            "timeout.seconds",
            "1..86400; never an automatic skip",
            args.timeout_seconds,
        )
        stage = "authoring_preflight"
        stage_record(evidence, stage)
        source_files = private_tree(
            args.authoring_root, allow_finite_prefix=args.allow_finite_prefix
        )
        stage = "host_preflight"
        preflight()  # Only main executing this driver contacts Docker.
        stage = "frozen_source_copy"
        source, manifest = frozen_copy(ROOT, out)
        save(evidence / "FROZEN-SOURCE.json", manifest)
        shutil.copytree(args.authoring_root, data / "authoring")
        copied_files = private_tree(
            data / "authoring", allow_finite_prefix=args.allow_finite_prefix
        )
        require(
            copied_files == source_files,
            "L1_AUTHORING_COPY_CHANGED",
            "authoring.sha256",
            source_files,
            copied_files,
        )
        save(evidence / "AUTHORING-SOURCE.json", source_files)
        save(
            data / "authorization.json",
            {
                "authorization_id": args.authorization_id,
                "actor": args.actor,
                "authorization_ref": args.authorization_ref,
            },
        )
        token = secrets.token_hex(12)
        prefix = "int-full-flow-l1-" + token
        plan = {
            "network": prefix + "-internal",
            "pg": prefix + "-pg",
            "redis": prefix + "-redis",
            "app": prefix + "-app",
            "database": "full_flow_l1_" + token + "_test_db",
            "owner": OWNER,
            "run_id": label,
            "volumes": {},
            "container_ids": {},
        }
        save(out / "PLAN.json", plan)
        stage = "create_owned_isolation"
        stage_record(evidence, stage)
        docker(
            "network",
            "create",
            "--internal",
            "--label",
            "fullflow.owner=" + OWNER,
            "--label",
            "fullflow.run=" + label,
            plan["network"],
        )
        password = secrets.token_hex(24)
        write_env(
            out / "pg.env",
            {
                "POSTGRES_DB": plan["database"],
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": password,
            },
        )
        for role, image, directory in (
            ("pg", "ankane/pgvector:latest", "/var/lib/postgresql/data"),
            ("redis", "redis:7-alpine", "/data"),
        ):
            image_id = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
            volume = plan[role] + "-data"
            docker(
                "volume",
                "create",
                "--label",
                "fullflow.owner=" + OWNER,
                "--label",
                "fullflow.run=" + label,
                volume,
            )
            plan["volumes"][role] = volume
            env = ["--env-file", str(out / "pg.env")] if role == "pg" else []
            names.append(plan[role])  # Include an uncertain create outcome in cleanup.
            created = docker(
                "create",
                "--pull",
                "never",
                "--name",
                plan[role],
                "--label",
                "fullflow.owner=" + OWNER,
                "--label",
                "fullflow.run=" + label,
                "--network",
                plan["network"],
                "--mount",
                f"type=volume,src={volume},dst={directory}",
                *env,
                image_id,
            )
            plan["container_ids"][role] = created.stdout.strip()
            save(out / "PLAN.json", plan)
            docker("start", plan[role])
        network = json.loads(docker("network", "inspect", plan["network"]).stdout)[0]
        require(
            network["Internal"] is True
            and network["Labels"].get("fullflow.owner") == OWNER
            and network["Labels"].get("fullflow.run") == label,
            "L1_NETWORK_NOT_INTERNAL",
            "network.internal_and_owned",
            True,
            False,
        )
        for role in ("pg", "redis"):
            actual = json.loads(docker("container", "inspect", plan[role]).stdout)[0]
            require(
                actual["Id"] == plan["container_ids"][role]
                and actual["Config"]["Labels"].get("fullflow.owner") == OWNER
                and actual["Config"]["Labels"].get("fullflow.run") == label
                and not actual["HostConfig"]["PortBindings"]
                and not actual["HostConfig"]["PublishAllPorts"]
                and set(actual["NetworkSettings"]["Networks"]) == {plan["network"]},
                "L1_ISOLATION_READBACK_FAILED",
                "isolation." + role,
                "owned/no host ports",
                False,
            )
        until = time.monotonic() + 60
        while docker(
            "exec", plan["pg"], "pg_isready", "-U", "postgres", "-d", plan["database"], check=False
        ).returncode:
            require(time.monotonic() < until, "L1_PG_START_TIMEOUT", "pg.ready", True, False)
            time.sleep(0.5)
        write_env(
            out / "app.env",
            {
                "BESTSELLER__DATABASE__URL": f"postgresql+asyncpg://postgres:{password}@{plan['pg']}:5432/{plan['database']}",
                "BESTSELLER__REDIS__URL": f"redis://{plan['redis']}:6379/0",
                "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR": "/private/artifacts",
                "BESTSELLER__ARTIFACT_STORE__RETAIN_PROMPT_PAYLOADS": "true",
                "BESTSELLER__OUTPUT__BASE_DIR": "/private/output",
                "BESTSELLER_LLM_RUNTIME_PROFILE_PATH": "/private/absent-runtime-profile.json",
                "BESTSELLER_RECOVER_EXPORTS_ON_STARTUP": "false",
                "BESTSELLER_WEB_TOKEN": secrets.token_hex(24),
                "MINIMAX_API_KEY": "l1-scripted-sdk-dummy-not-real",
                "LITELLM_LOCAL_MODEL_COST_MAP": "true",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": "/fullflow/src:/fullflow/tests:/fullflow:/opt/testdeps",
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
                "all_proxy": "",
            },
        )
        stage = "normal_http_native_driver"
        stage_record(evidence, stage)
        deps = PRIVATE / "test-deps-v1"
        mounts = (
            ["--mount", f"type=bind,src={deps},dst=/opt/testdeps,readonly"] if deps.is_dir() else []
        )
        names.append(plan["app"])
        created = docker(
            "create",
            "--pull",
            "never",
            "--name",
            plan["app"],
            "--label",
            "fullflow.owner=" + OWNER,
            "--label",
            "fullflow.run=" + label,
            "--network",
            plan["network"],
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=512m",
            "--tmpfs",
            "/app:rw,size=64m",
            "--workdir",
            "/fullflow",
            "--mount",
            f"type=bind,src={source},dst=/fullflow,readonly",
            "--mount",
            f"type=bind,src={data},dst=/private",
            *mounts,
            "--env-file",
            str(out / "app.env"),
            APP_IMAGE,
            "python",
            "-B",
            "scripts/full_flow/run_l1.py",
            "--inside",
            "--run-id",
            label,
            "--timeout-seconds",
            str(args.timeout_seconds),
        )
        plan["container_ids"]["app"] = created.stdout.strip()
        save(out / "PLAN.json", plan)
        actual = json.loads(docker("container", "inspect", plan["app"]).stdout)[0]
        require(
            actual["Id"] == plan["container_ids"]["app"]
            and actual["Config"]["Labels"].get("fullflow.owner") == OWNER
            and actual["Config"]["Labels"].get("fullflow.run") == label
            and not actual["HostConfig"]["PortBindings"]
            and not actual["HostConfig"]["PublishAllPorts"]
            and actual["HostConfig"]["ReadonlyRootfs"]
            and set(actual["NetworkSettings"]["Networks"]) == {plan["network"]},
            "L1_ISOLATION_READBACK_FAILED",
            "isolation.app",
            "owned/no host ports",
            False,
        )
        docker("start", plan["app"])
        until = time.monotonic() + args.timeout_seconds + 600
        while time.monotonic() < until:
            # Independent of HTTP/DB progress: even a swallowed SDK failure
            # stops the owned container and cannot become a fallback PASS.
            script_failure(data)
            inspected = json.loads(docker("container", "inspect", plan["app"]).stdout)[0]
            if not inspected["State"]["Running"]:
                break
            time.sleep(0.5)
        else:
            require(False, "L1_HOST_TIMEOUT", "app.deadline", "exited", "running")
        script_failure(data)
        require((evidence / "RESULT.json").is_file(), "L1_RESULT_MISSING", "result", True, False)
        row = read_json(evidence / "RESULT.json")
        row["frozen_head"] = manifest["head"]
        row["source_archive_sha256"] = manifest["archive_sha256"]
        require(
            inspected["State"]["ExitCode"] == (0 if row["status"] == "通过" else 1),
            "L1_APP_EXIT_MISMATCH",
            "app.exit_code",
            0 if row["status"] == "通过" else 1,
            inspected["State"]["ExitCode"],
        )
    except BaseException as error:
        row.update(failure(error, stage, evidence))
        row["status"] = "失败"
    finally:
        stopped = True
        for name in reversed(names):
            try:
                actual = docker("container", "inspect", name, check=False)
                if actual.returncode:
                    require(
                        "No such" in actual.stderr,
                        "L1_CLEANUP_STATE_UNKNOWN",
                        "container.exists",
                        "known",
                        operand(actual.stderr),
                    )
                    continue
                info = json.loads(actual.stdout)[0]
                require(
                    info["Config"]["Labels"].get("fullflow.run") == label
                    and info["Config"]["Labels"].get("fullflow.owner") == OWNER,
                    "L1_CLEANUP_OWNER_MISMATCH",
                    "container.owner",
                    label,
                    info["Config"]["Labels"].get("fullflow.run"),
                )
                if info["State"]["Running"]:
                    try:
                        docker("stop", "--time", "10", name, timeout=25)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        docker("kill", name, check=False, timeout=15)
                logs = docker("logs", name, check=False)
                log_path = evidence / (name + ".log")
                log_path.write_text(logs.stdout + logs.stderr)
                log_path.chmod(0o600)
                actual = docker("container", "inspect", name, check=False)
                require(
                    (actual.returncode != 0 and "No such" in actual.stderr)
                    or (
                        actual.returncode == 0
                        and not json.loads(actual.stdout)[0]["State"]["Running"]
                    ),
                    "L1_CONTAINER_STILL_RUNNING",
                    "container.exited",
                    True,
                    False,
                )
            except BaseException as error:
                stopped = False
                cleanup_failure = failure(error, "cleanup", evidence)
                if "root_error_code" not in row:
                    row.update(cleanup_failure)
                row.setdefault("secondary_failures", []).append(cleanup_failure)
        row["all_owned_containers_stopped"] = stopped
        if not stopped:
            row["status"] = "失败"
        row["private_evidence"] = str(evidence)
        row["automatic_skip"] = False
        save(evidence / "HOST-RESULT.json", row)
        save(REPORT / (label + ".json"), row)
        print(
            canonical(
                {
                    key: row.get(key)
                    for key in (
                        "status",
                        "root_error_code",
                        "l1_first_chapter_adopted",
                        "all_owned_containers_stopped",
                        "private_evidence",
                    )
                }
            )
        )
    return 0 if row["status"] == "通过" else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authoring-root", type=Path)
    parser.add_argument(
        "--allow-finite-prefix",
        action="store_true",
        default=False,
        help="Accept a verified finite v3 prefix; not full coverage or native adoption proof.",
    )
    parser.add_argument("--authorization-id")
    parser.add_argument("--actor")
    parser.add_argument("--authorization-ref")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        require(
            1 <= args.timeout_seconds <= 86400,
            "L1_TIMEOUT_INVALID",
            "timeout.seconds",
            "1..86400; never an automatic skip",
            args.timeout_seconds,
        )
        return asyncio.run(inside(args))
    return host(args)


if __name__ == "__main__":
    raise SystemExit(main())

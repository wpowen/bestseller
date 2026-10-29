"""Main-only E05b runner: frozen git archive, owned isolated PG, SDK-scripted IO.

Run after committing the final candidate and these two harness files:
    ../../.venv/bin/python scripts/full_flow/run_e05b.py

Never mounts the checkout into the application container. No automatic retry.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
from urllib.parse import urlsplit

from isolation import DOCKER, OWNER, PRIVATE, REPORT, ROOT, preflight, verify
from sqlalchemy.engine import make_url

IMAGE = "sha256:9685577fa4d1a017afabd1d37de48903631c35c1910479702ec2856dc93bb797"
SOURCE_DB = "full_flow_s01_20261028_test_db"
SNAPSHOT_ROOT = Path(
    "/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-v3-private-20261027-g0a"
)
SOURCE_ARTIFACTS = SNAPSHOT_ROOT / "s01-snapshot-v2/inputs/artifacts"
ORIGINAL_ENV = SNAPSHOT_ROOT / "runs/s01-red/application.env"
NORMAL_BLOCKS = (
    "BESTSELLER__LLM",
    "BESTSELLER__GENERATION",
    "BESTSELLER__HOOK_ENGINE",
    "BESTSELLER__PIPELINE",
    "BESTSELLER__QUALITY",
    "BESTSELLER__RETRIEVAL",
)
RUNTIME_SCRIPTS = (
    "scripts/full_flow/run_e05b.py",
    "scripts/full_flow/e05b_native_handoff.py",
    "scripts/full_flow/isolation.py",
)
RUNTIME_ROOTS = (
    "src",
    "config",
    "resources",
    "templates",
    "tests/support",
    *RUNTIME_SCRIPTS,
    "pyproject.toml",
    "README.md",
    "alembic.ini",
)


def summary(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    )
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, bytes, list, dict)) else None,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def need(ok, field, expected, actual):
    if not ok:
        error = RuntimeError("E05B_RUNNER:" + field)
        error.differences = [
            {"field": field, "expected": summary(expected), "actual": summary(actual)}
        ]
        raise error


def git(*args):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        timeout=120,
    )
    need(result.returncode == 0, "git." + args[0], 0, result.returncode)
    return result.stdout


def env_values(path):
    need(path.is_file() and not path.is_symlink(), "regular_env_file", True, path.is_file())
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )


def check_nonsecret(value, field):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in {
                "api_key",
                "api_key_value",
                "access_token",
                "auth_token",
                "api_token",
                "token",
                "bearer_token",
                "password",
                "secret_key",
                "secret",
                "client_secret",
                "authorization",
                "proxy",
                "proxies",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            }:
                need(
                    item in (None, "", {}, []),
                    "normal_config_secret." + field + "." + key,
                    "absent",
                    summary(item),
                )
            if normalized in {"api_base", "base_url"} and isinstance(item, str):
                url = urlsplit(item)
                need(
                    not (url.username or url.password or url.query or url.fragment),
                    "normal_config_url_credentials." + field,
                    False,
                    bool(url.username or url.password or url.query or url.fragment),
                )
            check_nonsecret(item, field + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_nonsecret(item, field + "." + str(index))


def write_env(path, values):
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        for key, value in values.items():
            need("\n" not in value and "\r" not in value, "single_line_env." + key, True, False)
            stream.write(key + "=" + value + "\n")


def export_frozen_head(out):
    head = git("rev-parse", "--verify", "HEAD").decode().strip()
    paths = [
        name for name in RUNTIME_ROOTS if git("ls-tree", "--name-only", head, "--", name).strip()
    ]
    need(
        {"src", "config", "tests/support", *RUNTIME_SCRIPTS}.issubset(paths),
        "runtime_roots",
        ["src", "config", "tests/support", *RUNTIME_SCRIPTS],
        paths,
    )
    changed = git("diff", "--name-only", head, "--", *paths).decode().splitlines()
    untracked = (
        git("ls-files", "--others", "--exclude-standard", "--", *paths).decode().splitlines()
    )
    need(not changed and not untracked, "frozen_head_runtime_clean", [], changed + untracked)
    archive = out / "source.tar"
    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "archive",
            "--format=tar",
            "--output=" + str(archive),
            head,
            *paths,
        ],
        capture_output=True,
        timeout=180,
    )
    need(result.returncode == 0, "git.archive", 0, result.returncode)
    frozen = out / "frozen-source"
    frozen.mkdir(mode=0o700)
    # Extract regular files/directories only; no archive symlink or traversal.
    with tarfile.open(archive, "r:") as source:
        for member in source:
            relative = PurePosixPath(member.name)
            need(
                not relative.is_absolute() and ".." not in relative.parts,
                "archive.path",
                "relative path",
                member.name,
            )
            need(
                member.isdir() or member.isfile(),
                "archive.regular_entry",
                True,
                member.isdir() or member.isfile(),
            )
            target = frozen / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if member.isdir():
                target.mkdir(exist_ok=True, mode=0o700)
            else:
                with source.extractfile(member) as original, target.open("xb") as destination:
                    shutil.copyfileobj(original, destination)
                target.chmod(0o600 | (member.mode & 0o111))
    for relative in (
        *RUNTIME_SCRIPTS,
        "src/bestseller/services/review_issuer_archive.py",
        "src/bestseller/services/review_issuer_view.py",
        "src/bestseller/services/chapter_review_preflight.py",
    ):
        need(
            (frozen / relative).is_file(),
            "frozen_required_file." + relative,
            True,
            (frozen / relative).is_file(),
        )
    runtime = out / "runtime"
    shutil.copytree(frozen, runtime)
    files = {
        str(path.relative_to(frozen)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(frozen.rglob("*"))
        if path.is_file()
    }
    return head, frozen, runtime, files, hashlib.sha256(archive.read_bytes()).hexdigest()


def main():
    preflight()
    need(ROOT.resolve().name == "int-worktree", "integration_worktree", "int-worktree", ROOT.name)
    plan = json.loads((REPORT / "ISOLATION.json").read_text())
    verify(plan)
    private = Path(plan["private_root"]).resolve()
    need(
        private.is_relative_to(PRIVATE.resolve()) and not private.is_relative_to(ROOT.resolve()),
        "owned_private_root",
        True,
        private.is_relative_to(PRIVATE.resolve()),
    )
    need(
        SOURCE_ARTIFACTS.is_dir() and not SOURCE_ARTIFACTS.is_symlink(),
        "readonly_s01_artifacts",
        True,
        SOURCE_ARTIFACTS.is_dir(),
    )
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    label = "e05b-native-handoff-" + at
    out = private / "evidence" / label
    out.mkdir(mode=0o700)
    evidence = out / "run"
    evidence.mkdir(mode=0o700)
    row = {
        "status": "失败",
        "production_access": False,
        "model_calls": 0,
        "script_consumed_index": 0,
        "e05b_complete": False,
    }
    code = 1
    name = "int-full-flow-e05b-" + at.lower()
    launched = False
    try:
        head, frozen, runtime, files, archive_sha = export_frozen_head(out)
        normal = env_values(ORIGINAL_ENV)
        selected = {}
        for key in NORMAL_BLOCKS:
            need(key in normal, "captured_normal_block." + key, True, key in normal)
            check_nonsecret(json.loads(normal[key]), key)
            selected[key] = normal[key]
        base = env_values(private / "app-offline.env")
        url = make_url(base["BESTSELLER__DATABASE__URL"]).set(database=SOURCE_DB)
        need(
            url.host == plan["pg"] and url.port == 5432,
            "isolated_database_route",
            True,
            url.host == plan["pg"] and url.port == 5432,
        )
        selected.update(
            {
                "BESTSELLER__DATABASE__URL": url.render_as_string(hide_password=False),
                "BESTSELLER__REDIS__URL": base["BESTSELLER__REDIS__URL"],
                "BESTSELLER__ARTIFACT_STORE__RETAIN_PROMPT_PAYLOADS": "true",
                "BESTSELLER_LLM_RUNTIME_PROFILE_PATH": "/evidence/absent-runtime-profile.json",
                "MINIMAX_API_KEY": "e05b-offline-sdk-fixture-not-real",
                "LITELLM_LOCAL_MODEL_COST_MAP": "true",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": (
                    "/fullflow/src:/fullflow/tests:/fullflow/tests/support:/fullflow:/opt/testdeps"
                ),
                "FULL_FLOW_E05B_EVIDENCE": "/evidence",
                "FULL_FLOW_E05B_FROZEN_HEAD": head,
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
                "all_proxy": "",
            }
        )
        for key in (
            "BESTSELLER__BUDGET__EXECUTION_MAX_CALLS",
            "BESTSELLER__BUDGET__EXECUTION_MAX_TOKENS",
            "BESTSELLER__BUDGET__EXECUTION_DEADLINE_SECONDS",
        ):
            need(key in base, "isolated_budget_settings." + key, True, key in base)
            selected[key] = base[key]
        env = out / "offline.env"
        write_env(env, selected)
        manifest = {
            "frozen_head": head,
            "git_archive_sha256": archive_sha,
            "files": files,
            "source_roots": list(RUNTIME_ROOTS),
            "snapshot_normal_settings_sha256": hashlib.sha256(
                ORIGINAL_ENV.read_bytes()
            ).hexdigest(),
            "application_guard_mocks": False,
            "physical_sdk_response_only": True,
        }
        (evidence / "FROZEN-SOURCE.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )
        command = [
            DOCKER,
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "fullflow.owner=" + OWNER,
            "--network",
            plan["network"],
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--tmpfs",
            "/app:rw,nosuid,nodev,size=64m",
            "--workdir",
            "/fullflow",
            "--mount",
            f"type=bind,src={runtime},dst=/fullflow",
            "--mount",
            f"type=bind,src={frozen},dst=/frozen-source,readonly",
            "--mount",
            f"type=bind,src={evidence},dst=/evidence",
            "--mount",
            f"type=bind,src={SOURCE_ARTIFACTS},dst=/s01-input-artifacts,readonly",
            "--env-file",
            str(env),
            IMAGE,
            "python",
            "-B",
            "scripts/full_flow/e05b_native_handoff.py",
        ]
        with (evidence / "runner.log").open("w") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            launched = True
            try:
                code = process.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    [DOCKER, "stop", "--time", "10", name], capture_output=True, timeout=25
                )
                process.wait(timeout=30)
                code = 124
        result = evidence / "RESULT.json"
        if result.is_file():
            row = json.loads(result.read_text())
        else:
            need(False, "native_result_receipt", "RESULT.json", code)
        row.update(
            frozen_head=head,
            git_archive_sha256=archive_sha,
            snapshot_normal_settings_sha256=manifest["snapshot_normal_settings_sha256"],
        )
        need(
            code == 0 and row.get("status") == "通过" and row.get("e05b_complete") is True,
            "native_completion",
            {"exit": 0, "e05b_complete": True},
            {"exit": code, "e05b_complete": row.get("e05b_complete")},
        )
    except Exception as error:
        native_receipt = evidence / "RESULT.json"
        if native_receipt.is_file() and row.get("root_error_code") in (None, "none"):
            try:
                native_row = json.loads(native_receipt.read_text())
                if type(native_row) is dict:
                    row.update(native_row)
            except (OSError, ValueError):
                pass  # Preserve the original runner failure if the receipt is incomplete.
        failure = {
            "root_exception_type": type(error).__name__,
            "root_error_code": str(error)
            if str(error).startswith("E05B_RUNNER:")
            else type(error).__name__,
            "failure_differences": getattr(
                error,
                "differences",
                [
                    {
                        "field": "runner.exception",
                        "expected": summary("no exception"),
                        "actual": summary(type(error).__name__),
                    }
                ],
            ),
        }
        if row.get("root_error_code") not in (None, "none"):
            row.setdefault("secondary_failures", []).append(failure)
        else:
            row.update(failure)
        row["status"] = "失败"
        row["e05b_complete"] = False
        row["shared_issuer_view_verified"] = False
        code = code or 1
    finally:
        exited = not launched
        cleanup_error = None
        try:
            inspected = (
                subprocess.run(
                    [DOCKER, "container", "inspect", name],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if launched
                else None
            )
            exited = inspected is None or (
                inspected.returncode != 0 and "No such" in inspected.stderr
            )
            if not exited:
                subprocess.run(
                    [DOCKER, "stop", "--time", "10", name], capture_output=True, timeout=25
                )
                checked = subprocess.run(
                    [DOCKER, "container", "inspect", name],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                exited = checked.returncode != 0 and "No such" in checked.stderr
        except Exception as error:
            cleanup_error = type(error).__name__
        if not exited:
            failure = {
                "root_exception_type": cleanup_error or "ContainerExitUnverified",
                "root_error_code": "E05B_CONTAINER_EXIT_UNVERIFIED",
                "failure_differences": [
                    {
                        "field": "container.exited",
                        "expected": summary(True),
                        "actual": summary("Unknown"),
                    }
                ],
            }
            if row.get("root_error_code") not in (None, "none"):
                row.setdefault("secondary_failures", []).append(failure)
            else:
                row.update(failure)
            row["status"] = "失败"
            row["e05b_complete"] = False
            row["shared_issuer_view_verified"] = False
            code = 1
        row.update(
            container_exit_code=code,
            test_container_exited=exited,
            runner_log_path=str(evidence / "runner.log"),
            production_access=False,
            model_calls=0,
            simulation=(
                "Native SDK-scripted issuance/reentry; not human quality or four-format delivery"
            ),
        )
        native_trace = row.get("traceback_path")
        if isinstance(native_trace, str) and native_trace.startswith("/evidence/"):
            row["native_traceback_path"] = native_trace
            row["traceback_path"] = str(evidence / native_trace.removeprefix("/evidence/"))
        elif not native_trace:
            row["traceback_path"] = str(evidence / "runner.log")
        (REPORT / (label + ".json")).write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    key: row.get(key)
                    for key in (
                        "status",
                        "root_error_code",
                        "e05b_complete",
                        "container_exit_code",
                        "traceback_path",
                    )
                },
                ensure_ascii=False,
            )
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

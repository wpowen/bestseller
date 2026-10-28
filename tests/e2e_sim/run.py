"""Stdlib-only, local-image simulation runner. Never uses Compose or builds/pulls.

Run from the worktree: python -B tests/e2e_sim/run.py [--plan]. Safe metadata evidence
lives under reports/parallel-2026-10-28/P3/<unique nonce>/. Exit 1 means a
scenario failed, 2 means infrastructure failed. No existing resource is reused.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

try:
    from .diagnostics import safe_exception
except ImportError:  # Direct script execution without importing transport dependencies.
    from diagnostics import safe_exception

BASELINE_REF = "refs/tags/e2e17"
PRIVATE_ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/P3")
SCENARIOS = ("chapter_adoption", "review_length")
LLM_ROLES = ("planner", "writer", "critic", "summarizer", "editor")
ROLE_FIELDS = frozenset(
    {
        "model",
        "temperature",
        "max_tokens",
        "timeout_seconds",
        "stream",
        "n_candidates",
        "api_base",
        "api_key_env",
        "api_key_header",
        "model_override",
        "thinking_type",
        "reasoning_effort",
        "rate_limit_fallback_model",
        "rate_limit_fallback_api_base",
        "rate_limit_fallback_api_key_env",
        "rate_limit_fallback_stream",
    }
)
MIN_FREE_BYTES = 5 * 1024**3
OWNER_LABEL = "io.bestseller.e2e-sim.owner"
NONCE_LABEL = "io.bestseller.e2e-sim.nonce"
ROLES = ("pg", "redis", "tape", "migrate", "prefix", "api", "worker", "driver")
DRIVER_STARTUP = """import os, sys, time, urllib.request
url = sys.argv[1]
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url + '/health', timeout=2) as response:
            if response.status == 200:
                break
    except OSError:
        pass
    time.sleep(0.5)
else:
    raise SystemExit('API readiness deadline exceeded')
os.execv(sys.executable, [sys.executable, '-B', '-m', 'e2e_sim.scenarios',
    '--output', '/evidence/scenario-output', '--evidence-root', '/evidence',
    '--preflight', '/evidence/prefix-output/prefix-check.json', '--api', url, '--timeout', '90',
    '--scenario', os.environ['E2E_SIM_SCENARIO']])
"""


class InfrastructureError(RuntimeError):
    def __init__(self, message, *, code="harness_infrastructure_error"):
        super().__init__(message)
        self.code = code


def check_disk(worktree: Path) -> None:
    # Docker Desktop uses the internal Mac disk even for an external checkout.
    paths = [Path("/"), worktree]
    if sys.platform == "darwin":
        paths.append(Path("/System/Volumes/Data"))
    for path in paths:
        if shutil.disk_usage(path).free < MIN_FREE_BYTES:
            raise InfrastructureError("disk free space is below the required 5 GiB")


def docker_environment(home: Path) -> dict[str, str]:
    return {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "DOCKER_CONFIG": str(home),
        "LANG": "C.UTF-8",
    }


def sanitize(value: str) -> str:
    value = re.sub(r"(?i)\b(Bearer\s+)[^\s\"',;]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    value = re.sub(
        r"(?i)((?:[a-z_]*(?:password|api_key|token|secret)[a-z_]*|E2E_SIM_KEY)\s*[=:]\s*)"
        r"[^\s,;\"'\]}]+",
        r"\1[REDACTED]",
        value,
    )
    # Native exceptions occasionally include full prompts/SQL bound parameters.
    lines = []
    for line in value.splitlines():
        if re.search(
            r'(?i)(["\x27](?:messages|content|prompt|authorization)["\x27]\s*:'
            r"|\[parameters:|(?:messages|prompt|user prompt|system prompt)\s*[:=])",
            line,
        ):
            import hashlib

            line = "[payload omitted sha256=" + hashlib.sha256(line.encode()).hexdigest() + "]"
        lines.append(line)
    return "\n".join(lines)


@dataclass
class Plan:
    worktree: Path
    app_image: str = "bestseller-api:latest"
    pg_image: str = "ankane/pgvector:latest"
    redis_image: str = "redis:7-alpine"
    nonce: str = field(default_factory=lambda: secrets.token_hex(8))
    report_dir: Path | None = None
    prior_round2_evidence: Path | None = None
    prefix_bundle: Path | None = None
    prefix_sha256: str | None = None
    scenario: str = "chapter_adoption"
    prefix_only: bool = False
    source_roles: Path | None = None
    source_roles_sha256: str | None = None

    def __post_init__(self):
        self.worktree = self.worktree.resolve()
        if self.worktree.name != "P3" or not (self.worktree / ".git").is_file():
            raise ValueError("runner requires the isolated P3 Git worktree")
        if not re.fullmatch(r"[a-f0-9]{8,32}", self.nonce):
            raise ValueError("nonce must be 8-32 lowercase hexadecimal characters")
        if self.scenario not in SCENARIOS:
            raise ValueError("exactly one supported scenario required per fresh database")
        if self.prefix_bundle is not None:
            bundle = Path(self.prefix_bundle)
            if (
                not bundle.is_absolute()
                or not bundle.resolve().is_relative_to(PRIVATE_ROOT.resolve())
                or bundle.resolve() == PRIVATE_ROOT.resolve()
            ):
                raise ValueError("prefix bundle must be an absolute external private path")
            self.prefix_bundle = bundle.resolve()
        if self.source_roles is not None:
            roles = Path(self.source_roles)
            if (
                not roles.is_absolute()
                or not roles.resolve().is_relative_to(PRIVATE_ROOT.resolve())
                or roles.resolve() == PRIVATE_ROOT.resolve()
            ):
                raise ValueError("source roles must be an absolute external private path")
            self.source_roles = roles.resolve()
        if self.source_roles_sha256 is not None and not re.fullmatch(
            r"[a-f0-9]{64}", self.source_roles_sha256
        ):
            raise ValueError("source roles SHA256 must be lowercase hexadecimal")
        if self.prefix_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", self.prefix_sha256):
            raise ValueError("independent prefix SHA256 must be lowercase hexadecimal")
        for image in (self.app_image, self.pg_image, self.redis_image):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", image):
                raise ValueError("invalid local image reference")
        base = (self.worktree / "reports/parallel-2026-10-28/P3").resolve()
        self.evidence = (self.report_dir or base / self.prefix).resolve()
        if not self.evidence.is_relative_to(base) or self.evidence == base:
            raise ValueError("evidence must be a unique directory under the P3 report directory")

    @property
    def prefix(self):
        return "par_P3_" + self.nonce

    @property
    def private_namespace(self):
        return PRIVATE_ROOT / "runs" / self.prefix

    @property
    def database(self):
        return self.prefix + "_test_db"

    @property
    def names(self):
        return {role: self.prefix + "_" + role for role in (*ROLES, "network")}

    @property
    def label_map(self):
        return {OWNER_LABEL: "par_P3", NONCE_LABEL: self.nonce}

    @property
    def labels(self):
        return [f"{key}={value}" for key, value in self.label_map.items()]

    def network_command(self):
        return [
            "network",
            "create",
            "--internal",
            "--label",
            self.labels[0],
            "--label",
            self.labels[1],
            self.names["network"],
        ]

    def process_environment(self, pg_ip, redis_ip):
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "PYTHONPATH": "/app/src:/app/tests",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "E2E_SIM_DATABASE": self.database,
            "E2E_SIM_PG_IP": pg_ip,
            "E2E_SIM_REDIS_IP": redis_ip,
            "E2E_SIM_TAPE": "/private-prefix/tape.json",
            "E2E_SIM_PREFIX_BUNDLE": "/prefix-input/prefix.json",
            "E2E_SIM_PREFIX_SHA256": self.prefix_sha256 or "",
            "E2E_SIM_PRIVATE_PREFIX": "/private-prefix",
            "E2E_SIM_SCENARIO": self.scenario,
            "E2E_SIM_RUN_ID": self.prefix,
            "BESTSELLER__DATABASE__URL": f"postgresql+asyncpg://postgres@{pg_ip}:5432/{self.database}",
            "BESTSELLER__DATABASE__AUTO_MIGRATE": "false",
            "BESTSELLER__REDIS__URL": f"redis://{redis_ip}:6379/0",
            "BESTSELLER_REDIS_URL": f"redis://{redis_ip}:6379/0",
            "BESTSELLER__LLM__MOCK": "false",
            "BESTSELLER__LLM__RETRY__MAX_ATTEMPTS": "1",
            "BESTSELLER__LLM__RETRY__RATE_LIMIT_MAX_ATTEMPTS": "1",
            "BESTSELLER__LLM__RETRY__RATE_LIMIT_FALLBACK_ENABLED": "false",
            "BESTSELLER__PIPELINE__ENABLE_MATERIAL_LIBRARY": "false",
            "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR": "/private-prefix/artifacts",
            "BESTSELLER__OUTPUT__BASE_DIR": "/private-prefix/output",
            "BESTSELLER__OUTPUT__STREAM_TO_CONSOLE": "false",
            "BESTSELLER__LOGGING__FILE__ENABLED": "false",
            "BESTSELLER_LLM_RUNTIME_PROFILE_PATH": "/tmp/no-runtime-profile.json",
            "BESTSELLER_MCP_CONFIG_PATH": "/tmp/no-mcp-config.yaml",
            "WORKER_SELF_HEAL": "0",
            "WORKER_MAX_JOBS": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DO_NOT_TRACK": "1",
            "OTEL_SDK_DISABLED": "true",
        }
        if self.source_roles is not None:
            env.update(source_roles_environment(self.source_roles, self.source_roles_sha256))
            env["E2E_SIM_SOURCE_ROLES"] = "/prefix-input/runtime-roles.json"
        return env

    def container_command(
        self, role, image, *, pg_ip="127.0.0.1", redis_ip="127.0.0.2", api_ip="127.0.0.3"
    ):
        if role not in ROLES:
            raise ValueError("unknown container role")
        args = [
            "create",
            "--pull=never",
            "--name",
            self.names[role],
            "--label",
            self.labels[0],
            "--label",
            self.labels[1],
            "--network",
            self.names["network"],
            "--security-opt=no-new-privileges",
            "--no-healthcheck",
        ]
        if role == "pg":
            # Trust exists only on this new, unpublished, internal test network.
            return [
                *args,
                "--tmpfs",
                "/var/lib/postgresql/data:rw,nosuid,nodev,size=1024m",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "--env",
                "POSTGRES_DB=" + self.database,
                image,
            ]
        if role == "redis":
            return [
                *args,
                "--tmpfs",
                "/data:rw,nosuid,nodev,size=128m",
                image,
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
            ]
        args += [
            "--cap-drop=ALL",
            "--read-only",
            "--workdir",
            "/app",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m",
            "--tmpfs",
            "/app:rw,nosuid,nodev,size=64m",
        ]
        # Hiding all of image-baked /app also masks config/local.yaml, .env,
        # .env.local, runtime profiles, MCP configuration, data and credentials.
        for relative, target in (
            ("src", "/app/src"),
            ("tests/e2e_sim", "/app/tests/e2e_sim"),
            ("tests/support", "/app/tests/support"),
            ("migrations", "/app/migrations"),
            ("alembic.ini", "/app/alembic.ini"),
        ):
            source = (self.worktree / relative).resolve()
            if not source.is_relative_to(self.worktree):
                raise ValueError("source mount escaped worktree")
            args += ["--mount", f"type=bind,src={source},dst={target},readonly"]
        args += [
            "--mount",
            f"type=bind,src={self.private_namespace / 'fixture-config'},dst=/app/config,readonly",
        ]
        if self.prefix_bundle is None or self.prefix_sha256 is None:
            raise ValueError("private prefix bundle and independent SHA256 are required")
        args += [
            "--mount",
            f"type=bind,src={self.prefix_bundle},dst=/prefix-input/prefix.json,readonly",
            "--mount",
            f"type=bind,src={self.private_namespace},dst=/private-prefix",
        ]
        if self.source_roles is not None:
            args += [
                "--mount",
                f"type=bind,src={self.source_roles},dst=/prefix-input/runtime-roles.json,readonly",
            ]
        args += [
            "--mount",
            f"type=bind,src={self.evidence},dst=/evidence",
            "--entrypoint",
            "/usr/bin/env",
            image,
            "-i",
        ]
        args += [
            f"{key}={value}" for key, value in self.process_environment(pg_ip, redis_ip).items()
        ]
        args += [f"E2E_SIM_ROLE={role}"]
        if role in ("api", "worker", "migrate", "prefix"):
            command_role = "prefix-check" if role == "prefix" else role
            return [*args, "python", "-B", "-m", "e2e_sim.boot", command_role]
        command = ["python", "-B", "-m", "e2e_sim.scenarios"]
        if role == "tape":
            return args + command + ["--write-tape", "/private-prefix/tape.json"]
        # This independent HTTP driver remains unpatched. Wait for a real HTTP
        # health response, then replace it with the parent's native CLI module.
        return [*args, "python", "-B", "-c", DRIVER_STARTUP, f"http://{api_ip}:8000"]


class Runner:
    def __init__(self, plan: Plan, docker_host: str | None = None):
        self.plan = plan
        self.created = []
        self.cleanup_errors = []
        self.records = []
        self.process_exits = []
        self.started = time.monotonic()
        self.phase = "preflight"
        self.home = plan.evidence / "docker-empty-home"
        self.docker_host = docker_host or self.default_host()
        if not self.docker_host.startswith("unix://"):
            raise ValueError("only an explicit local unix Docker socket is allowed")

    @staticmethod
    def default_host():
        desktop = Path.home() / ".docker/run/docker.sock"
        return "unix://" + str(
            desktop if sys.platform == "darwin" else Path("/var/run/docker.sock")
        )

    def log(self, event, **metadata):
        self.records.append(
            json.dumps({"event": event, "phase": self.phase, **metadata}, sort_keys=True)
        )

    def progress(self, event="phase"):
        print(
            json.dumps(
                {
                    "event": event,
                    "phase": self.phase,
                    "seconds": round(time.monotonic() - self.started, 1),
                }
            ),
            flush=True,
        )

    def set_phase(self, phase):
        check_disk(self.plan.worktree)
        self.phase = phase
        self.log("phase")
        self.progress()

    @staticmethod
    def output_metadata(value):
        raw = value if isinstance(value, bytes) else (value or "").encode("utf-8", errors="replace")
        return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    def save_failure(self, error, **metadata):
        failure = {
            **safe_exception(error),
            "phase": self.phase,
            "process_role": "runner",
            **metadata,
        }
        # Failure evidence is permitted even when the disk guard has tripped.
        self.plan.evidence.mkdir(parents=True, exist_ok=True)
        with (self.plan.evidence / "failures-runner.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(failure, sort_keys=True) + "\n")
        self.log("failure", **failure)
        return failure

    def docker(self, args, *, mutation=False, check=True, timeout=60):
        check_disk(self.plan.worktree)
        command = ["docker", "--host", self.docker_host, "--config", str(self.home), *args]
        # Neither raw argv nor stdout/stderr belongs in a report: a native
        # exception can contain SQL parameters, model inputs, or source lines.
        operation = args[0] if args else "unknown"
        complete = threading.Event()

        def heartbeat():
            while not complete.wait(25):
                self.progress("waiting")

        watcher = threading.Thread(target=heartbeat, daemon=True)
        watcher.start()
        try:
            result = subprocess.run(
                command,
                env=docker_environment(self.home),
                cwd=self.plan.evidence,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            self.log(
                "docker_timeout",
                operation=operation,
                stdout=self.output_metadata(error.stdout),
                stderr=self.output_metadata(error.stderr),
            )
            raise InfrastructureError(
                "Docker command deadline exceeded", code="harness_docker_timeout"
            ) from error
        finally:
            complete.set()
            watcher.join()
        self.log(
            "docker_result",
            operation=operation,
            exit_code=result.returncode,
            stdout=self.output_metadata(result.stdout),
            stderr=self.output_metadata(result.stderr),
        )
        if check and result.returncode:
            raise InfrastructureError("Docker command failed", code="harness_docker_command_failed")
        return result

    def verify_owned(self, kind, name, value):
        valid_names = (
            [self.plan.names["network"]]
            if kind == "network"
            else [self.plan.names[role] for role in ROLES]
            if kind == "container"
            else []
        )
        if name not in valid_names:
            raise InfrastructureError("refusing cleanup of an unknown resource")
        labels = (
            value.get("Config", {}).get("Labels") if kind == "container" else value.get("Labels")
        )
        if not labels or any(
            labels.get(key) != expected for key, expected in self.plan.label_map.items()
        ):
            raise InfrastructureError("refusing resource without exact current ownership labels")

    def inspect(self, kind, name, *, check=True):
        form = (
            '{"Config":{"Labels":{{json .Config.Labels}}}}'
            if kind == "container"
            else '{"Labels":{{json .Labels}}}'
        )
        result = self.docker([kind, "inspect", "--format", form, name], check=check)
        if result.returncode:
            # Daemon/connectivity errors cannot establish name absence.
            missing = (
                r"(?:Error(?: response from daemon)?: )?"
                rf"(?:No such (?:container|network|object):\s*{re.escape(name)}"
                rf"|network {re.escape(name)} not found)"
            )
            if not re.fullmatch(missing, result.stderr.strip()):
                raise InfrastructureError("cannot establish resource absence")
            return None
        value = json.loads(result.stdout)
        return value[0] if isinstance(value, list) else value

    def create(self, role, image, **ips):
        name = self.plan.names[role]
        if self.inspect("container", name, check=False) is not None:
            raise InfrastructureError("refusing to reuse existing container")
        # Track before issuing create: a signal/timeout may arrive after Docker
        # creates the object but before the CLI has returned its identifier.
        self.created.append(("container", name))
        self.docker(self.plan.container_command(role, image, **ips), mutation=True)
        self.verify_owned("container", name, self.inspect("container", name))
        self.docker(["start", name], mutation=True)

    def ip(self, role):
        name = self.plan.names[role]
        self.verify_owned("container", name, self.inspect("container", name))
        result = self.docker(
            ["container", "inspect", "--format", "{{json .NetworkSettings.Networks}}", name]
        )
        networks = json.loads(result.stdout)
        if set(networks) != {self.plan.names["network"]}:
            raise InfrastructureError("container attached to unexpected network")
        return str(ipaddress.IPv4Address(networks[self.plan.names["network"]]["IPAddress"]))

    def wait(self, role, timeout=180):
        result = self.docker(["wait", self.plan.names[role]], timeout=timeout)
        try:
            code = int(result.stdout.strip())
            self.process_exits.append(
                {
                    "process_role": role,
                    "process_status": "exited",
                    "exit_code": code,
                    "phase": self.phase,
                }
            )
            self.log("process_exit", **self.process_exits[-1])
            return code
        except ValueError:
            raise InfrastructureError("invalid container exit status") from None

    def ready(self, role, command):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = self.docker(
                ["exec", self.plan.names[role], *command], check=False, mutation=True, timeout=10
            )
            if result.returncode == 0:
                return
            time.sleep(0.5)
        raise InfrastructureError(role + " did not become ready")

    def execute(self):
        check_disk(self.plan.worktree)
        self.progress()
        self.verify_prior_round2(self.plan.prior_round2_evidence)
        self.prepare_private_prefix()
        self.prepare_config()
        images = {}
        for role, reference in (
            ("app", self.plan.app_image),
            ("pg", self.plan.pg_image),
            ("redis", self.plan.redis_image),
        ):
            result = self.docker(["image", "inspect", "--format", "{{.Id}}", reference])
            identity = result.stdout.strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
                raise InfrastructureError("cached image identity is invalid")
            images[role] = identity
        for relative in (
            "src",
            "tests/e2e_sim/scenarios.py",
            "tests/e2e_sim/transport.py",
            "migrations",
            "config/default.yaml",
            "alembic.ini",
        ):
            if not (self.plan.worktree / relative).exists():
                raise InfrastructureError("required source file missing: " + relative)
        self.set_phase("network")
        if self.inspect("network", self.plan.names["network"], check=False) is not None:
            raise InfrastructureError("refusing to reuse existing network")
        self.created.append(("network", self.plan.names["network"]))
        self.docker(self.plan.network_command(), mutation=True)
        self.verify_owned(
            "network",
            self.plan.names["network"],
            self.inspect("network", self.plan.names["network"]),
        )
        self.set_phase("database/redis")
        self.create("pg", images["pg"])
        self.create("redis", images["redis"])
        self.ready("pg", ["pg_isready", "-U", "postgres", "-d", self.plan.database])
        self.ready("redis", ["redis-cli", "ping"])
        version = self.docker(
            ["exec", self.plan.names["redis"], "redis-server", "--version"], mutation=True
        )
        if not re.search(r"v=7\.", version.stdout):
            raise InfrastructureError("cached Redis must be version 7")
        ips = {"pg_ip": self.ip("pg"), "redis_ip": self.ip("redis")}
        self.set_phase("native migrations")
        self.create("migrate", images["app"], **ips)
        if self.wait("migrate"):
            raise InfrastructureError(
                "native Alembic migration or test database verification failed"
            )
        self.set_phase("prepare tape")
        self.create("tape", images["app"], **ips)
        if self.wait("tape"):
            raise InfrastructureError("scenario tape preparation failed")
        self.set_phase("prefix-check")
        self.create("prefix", images["app"], **ips)
        if self.wait("prefix"):
            raise InfrastructureError(
                "independent prefix preflight failed", code="prefix_preflight_failed"
            )
        self.verify_prefix_receipt()
        if self.plan.prefix_only:
            self.set_phase("prefix-verified")
            return 0
        self.set_phase("application boot")
        self.create("api", images["app"], **ips)
        self.create("worker", images["app"], **ips)
        ips["api_ip"] = self.ip("api")
        self.set_phase("scenarios")
        self.create("driver", images["app"], **ips)
        code = self.wait("driver", timeout=360)
        if code not in (0, 1):
            raise InfrastructureError("scenario driver failed before completing its assertions")
        if (
            not (self.plan.evidence / "scenario-output/junit.xml").is_file()
            or not (self.plan.evidence / "scenario-output/results.json").is_file()
        ):
            raise InfrastructureError("scenario driver did not produce required evidence")
        return self.scenario_outcome(code)

    def prepare_private_prefix(self):
        """Verify custody before Docker; never copy captured bytes into reports."""
        check_disk(self.plan.worktree)
        if self.plan.source_roles is None:
            raise InfrastructureError(
                "trusted captured source roles are required for native prefix proof",
                code="prefix_source_roles_required",
            )
        source_roles_environment(self.plan.source_roles, self.plan.source_roles_sha256)
        self.plan.source_roles_sha256 = hashlib.sha256(
            self.plan.source_roles.read_bytes()
        ).hexdigest()
        bundle = self.plan.prefix_bundle
        if bundle is None or self.plan.prefix_sha256 is None:
            raise InfrastructureError(
                "private captured prefix and independent SHA256 required",
                code="prefix_input_required",
            )
        root = PRIVATE_ROOT.resolve()
        if not bundle.is_relative_to(root) or bundle.is_symlink() or not bundle.is_file():
            raise InfrastructureError(
                "private captured prefix file is missing or unsafe", code="prefix_input_invalid"
            )
        try:
            raw = bundle.read_bytes()
            document = json.loads(raw)
            body = {key: value for key, value in document.items() if key != "sha256"}
            canonical = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
            if document.get("sha256") != digest or digest != self.plan.prefix_sha256:
                raise ValueError("independent hash mismatch")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise InfrastructureError(
                "captured prefix failed independent SHA256 validation",
                code="prefix_input_hash_failed",
            ) from error
        # All retained prompt payloads and native outputs share one private run
        # namespace. The importer requires artifacts/ to be absent initially.
        runs = root / "runs"
        if runs.is_symlink() or self.plan.private_namespace.exists():
            raise InfrastructureError(
                "private run namespace must be new", code="prefix_private_namespace_exists"
            )
        check_disk(self.plan.worktree)
        root.chmod(0o700)
        runs.mkdir(mode=0o700, exist_ok=True)
        runs.chmod(0o700)
        self.plan.private_namespace.mkdir(mode=0o700)
        manifest = {
            "bundle_path": str(bundle),
            "bundle_sha256": digest,
            "package_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "private_namespace": str(self.plan.private_namespace),
            "source_roles_path": str(self.plan.source_roles),
            "source_roles_sha256": hashlib.sha256(self.plan.source_roles.read_bytes()).hexdigest(),
        }
        (self.plan.evidence / "prefix-input-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        self.log("private_prefix_verified", **manifest)

    def verify_prefix_receipt(self):
        child = self.plan.evidence / "prefix-output"
        path = child / "prefix-check.json"
        try:
            if child.is_symlink() or path.is_symlink() or not path.is_file():
                raise ValueError("prefix receipt is missing")
            receipt = json.loads(path.read_text())
            invocation = json.loads((child / "invocation.json").read_text())
            # Each required native stage is explicit, fresh, and tied to this run.
            required_tables = {
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
            }
            from uuid import UUID

            observed_uuid = UUID(invocation.get("invocation_id", ""))
            counts = receipt.get("downstream_rows", {})
            if (
                receipt.get("status") != "pass"
                or receipt.get("imported") is not True
                or receipt.get("native_reader_passed") is not True
                or receipt.get("downstream_empty") is not True
                or receipt.get("invocation") != invocation
                or invocation.get("schema") != "e2e-sim-invocation.v1"
                or observed_uuid.version != 4
                or invocation.get("run_id") != self.plan.prefix
                or invocation.get("trusted_bundle_sha256") != self.plan.prefix_sha256
                or receipt.get("trusted_bundle_sha256") != self.plan.prefix_sha256
                or set(counts) != required_tables
                or any(type(count) is not int or count != 0 for count in counts.values())
            ):
                raise ValueError("native prefix receipt disagrees with current invocation")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise InfrastructureError(
                "independent prefix receipt is invalid", code="prefix_receipt_invalid"
            ) from error
        self.log(
            "prefix_receipt_verified",
            receipt="prefix-output/prefix-check.json",
            status="pass",
            invocation_id=invocation["invocation_id"],
            run_id=self.plan.prefix,
        )

    def verify_prior_round2(self, previous=None):
        """Fail closed on undiagnosed R2 failures; parent owns max-three accounting.

        R2/R3/R4-marked runs participate, so historical round-one failures do not
        spend or block this round. An explicit prior path can be supplied by the
        parent's durable attempt ledger. No attempt counter is changed here.
        """
        base = (self.plan.worktree / "reports/parallel-2026-10-28/P3").resolve()
        receipts = {
            path.parent.resolve()
            for pattern in ("round2-run.json", "round3-run.json", "round4-run.json")
            for path in base.rglob(pattern)
            if path.parent.resolve() != self.plan.evidence
        }
        if previous is not None:
            receipts.add(Path(previous).resolve())
        for prior in sorted(receipts):
            self._verify_prior_round2_receipt(prior, base)

    def _verify_prior_round2_receipt(self, previous, base):
        previous = Path(previous).resolve()
        if not previous.is_relative_to(base) or previous == base or previous == self.plan.evidence:
            raise InfrastructureError("prior R2 evidence escaped report directory")
        try:
            marker_path = previous / "round4-run.json"
            if not marker_path.exists():
                marker_path = previous / "round3-run.json"
            if not marker_path.exists():
                marker_path = previous / "round2-run.json"
            marker = json.loads(marker_path.read_text())
            if marker.get("round") not in {2, 3, 4}:
                raise ValueError("wrong round")
            if marker.get("exit_code") == 0:
                return
            failures = []
            for path in previous.glob("failures-*.jsonl"):
                failures.extend(
                    json.loads(line) for line in path.read_text().splitlines() if line.strip()
                )

            def has_frames(row):
                frames = row.get("safe_stack") if isinstance(row, dict) else None
                return bool(
                    isinstance(frames, list)
                    and frames
                    and all(
                        isinstance(frame, dict)
                        and set(frame) == {"file", "function", "line"}
                        and isinstance(frame["file"], str)
                        and isinstance(frame["function"], str)
                        and type(frame["line"]) is int
                        and frame["line"] > 0
                        for frame in frames
                    )
                )

            if not failures or any(not has_frames(row) for row in failures):
                raise ValueError("safe frames required")
            result_path = previous / "results.json"
            if result_path.exists():
                results = json.loads(result_path.read_text())
                hashes = {row.get("error_sha256") for row in failures if has_frames(row)} - {None}
                for row in results:
                    if row.get("status") in {"error", "fail"} and not (
                        has_frames(row) or row.get("error_sha256") in hashes
                    ):
                        raise ValueError("scenario failure lacks safe frames")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise InfrastructureError(
                "previous R2 failure lacks valid safe_stack evidence",
                code="harness_retry_missing_safe_stack",
            ) from error
        self.log("prior_round2_failure_diagnosed", evidence=previous.name)

    def scenario_outcome(self, code):
        try:
            results = json.loads((self.plan.evidence / "scenario-output/results.json").read_text())
            if (
                not isinstance(results, list)
                or len(results) != 1
                or any(not isinstance(row, dict) for row in results)
                or {row.get("name") for row in results} != {self.plan.scenario}
                or any(row.get("status") not in {"pass", "fail", "error"} for row in results)
            ):
                raise InfrastructureError(
                    "scenario evidence must contain only the requested complete result record"
                )
            if any(row["status"] == "error" for row in results):
                raise InfrastructureError("scenario driver reported harness/infrastructure error")
            for path in self.plan.evidence.glob("*wire*.jsonl"):
                for line in path.read_text().splitlines():
                    if line.strip():
                        event = json.loads(line)
                        if not isinstance(event, dict) or event.get("event") == "harness_UNMAPPED":
                            raise InfrastructureError(
                                "wire audit reported unmapped or invalid harness event"
                            )
        except (OSError, ValueError, TypeError) as error:
            raise InfrastructureError("scenario evidence could not be validated") from error
        if any(row["status"] == "fail" for row in results):
            return 1
        if code:
            raise InfrastructureError("scenario driver exit disagrees with passing result records")
        return 0

    def prepare_config(self):
        """Preserve tracked baseline rules without reading local credential files."""
        env = docker_environment(self.home)
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "ls-files",
                "-z",
                "--",
                "config",
            ],
            cwd=self.plan.worktree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode:
            raise InfrastructureError("could not inventory tracked baseline configuration")
        manifest = {}
        for relative in result.stdout.split("\0"):
            if not relative:
                continue
            path = Path(relative)
            if path.parts[0] != "config" or ".." in path.parts:
                raise InfrastructureError("tracked configuration escaped config directory")
            if path.name in ("local.yaml", ".env", ".env.local", "credentials", "credentials.json"):
                continue
            source = self.plan.worktree / path
            if source.is_symlink() or not source.resolve().is_relative_to(
                self.plan.worktree / "config"
            ):
                raise InfrastructureError("configuration symlinks are not permitted")
            if not source.is_file():
                raise InfrastructureError("tracked baseline configuration missing")
            check_disk(self.plan.worktree)
            destination = (
                self.plan.private_namespace / "fixture-config" / path.relative_to("config")
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            manifest[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
        if "config/default.yaml" not in manifest:
            raise InfrastructureError("tracked default configuration required")
        check_disk(self.plan.worktree)
        (self.plan.evidence / "config-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
        )
        self.log(
            f"snapshotted {len(manifest)} tracked baseline config files; local overrides excluded"
        )

    def process_state(self, role):
        result = self.docker(
            [
                "container",
                "inspect",
                "--format",
                '{"exit_code":{{.State.ExitCode}},"process_status":{{json .State.Status}},'
                '"oom_killed":{{.State.OOMKilled}}}',
                self.plan.names[role],
            ]
        )
        state = json.loads(result.stdout)
        if (
            not isinstance(state, dict)
            or type(state.get("exit_code")) is not int
            or type(state.get("oom_killed")) is not bool
            or state.get("process_status")
            not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
        ):
            raise InfrastructureError(
                "invalid process exit readback", code="harness_process_state_invalid"
            )
        metadata = {"process_role": role, "phase": "cleanup_before_stop", **state}
        self.process_exits.append(metadata)
        self.log("process_state", **metadata)
        return metadata

    def cleanup(self):
        for kind, name in reversed(self.created):
            try:
                value = self.inspect(kind, name, check=False)
                if value is None:
                    continue
                self.verify_owned(kind, name, value)
                if kind == "container":
                    role = name.removeprefix(self.plan.prefix + "_")
                    try:
                        self.process_state(role)
                    except Exception as error:
                        self.cleanup_errors.append(
                            self.save_failure(
                                error,
                                phase="cleanup_readback",
                                process_role=role,
                                process_status="unknown",
                            )
                        )
                    self.docker(["stop", "--time", "5", name], mutation=True, check=False)
                    logs = self.docker(["logs", name], check=False)
                    role = name.removeprefix(self.plan.prefix + "_")
                    (self.plan.evidence / (role + ".log")).write_text(
                        json.dumps(
                            {
                                "process_role": role,
                                "stdout": self.output_metadata(logs.stdout),
                                "stderr": self.output_metadata(logs.stderr),
                            },
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    self.docker(["container", "rm", "--force", name], mutation=True)
                else:
                    self.docker(["network", "rm", name], mutation=True)
            except Exception as exc:
                failure = self.save_failure(
                    exc,
                    phase="cleanup",
                    resource_kind=kind,
                    process_role=name.removeprefix(self.plan.prefix + "_"),
                    process_status="cleanup_failed",
                )
                self.cleanup_errors.append(failure)

    def run(self):
        code, error = 2, None
        handlers = {}
        try:
            check_disk(self.plan.worktree)
            if self.plan.evidence.exists():
                # Never clobber a prior receipt, even when reporting refusal.
                self.plan.evidence = self.plan.evidence.parent / (
                    self.plan.prefix + "_refused_" + secrets.token_hex(4)
                )
                self.home = self.plan.evidence / "docker-empty-home"
                raise InfrastructureError("refusing an existing evidence directory")
            self.home.mkdir(parents=True)
            (self.plan.evidence / "round4-run.json").write_text(
                json.dumps(
                    {
                        "round": 4,
                        "exit_code": None,
                        "baseline_ref": BASELINE_REF,
                        "attempt_count_owner": "parent",
                        "scenario": self.plan.scenario,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self.interrupted)
            code = self.execute()
        except (Exception, KeyboardInterrupt) as exc:
            failure = self.save_failure(exc, process_status="failed")
            error = f"{self.phase}: {failure['error_type']} ({','.join(failure['error_codes'])})"
        finally:
            # A second signal cannot interrupt ownership-checked cleanup.
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)
            self.cleanup()
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
            if self.cleanup_errors:
                code, error = 2, error or "cleanup failed; owned resources may remain"
            sentinels = list(self.plan.evidence.glob("network-*.jsonl"))
            try:
                if any(path.stat().st_size for path in sentinels):
                    raise InfrastructureError(
                        "network fence violation persisted", code="harness_network_fence_violation"
                    )
            except Exception as exc:
                failure = self.save_failure(exc, phase="network_fence", process_status="failed")
                code, error = 2, f"network_fence: {failure['error_type']}"
            # Failure evidence is the only write allowed below the disk limit.
            self.plan.evidence.mkdir(parents=True, exist_ok=True)
            (self.plan.evidence / "round4-run.json").write_text(
                json.dumps(
                    {
                        "round": 4,
                        "exit_code": code,
                        "baseline_ref": BASELINE_REF,
                        "attempt_count_owner": "parent",
                        "scenario": self.plan.scenario,
                        "process_exits": self.process_exits,
                        "cleanup_errors": self.cleanup_errors,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            (self.plan.evidence / "run.log").write_text(
                "\n".join(self.records) + "\n", encoding="utf-8"
            )
            suite = ET.Element(
                "testsuite",
                name="e2e-sim-infrastructure",
                tests="1",
                errors=str(int(code == 2)),
                failures=str(int(code == 1)),
                time=f"{time.monotonic() - self.started:.3f}",
            )
            case = ET.SubElement(
                suite, "testcase", name="isolated-native-run", classname="e2e_sim.runner"
            )
            if code:
                ET.SubElement(
                    case,
                    "error" if code == 2 else "failure",
                    message=error or "native scenario assertions failed",
                )
            ET.ElementTree(suite).write(
                self.plan.evidence / "runner-junit.xml", encoding="utf-8", xml_declaration=True
            )
            print(f"e2e-sim exit={code}; evidence={self.plan.evidence}")
        return code

    @staticmethod
    def interrupted(signum, _frame):
        raise InfrastructureError(f"interrupted by signal {signum}")


def source_roles_environment(path, expected_sha256=None):
    """Replay sanitized role parameters, replacing named credentials with dummies.

    Capture owns this private metadata. No provider credential value is accepted
    in the format. Transport endpoint matching and native receipt verification
    stay in their owning modules; this function grants neither proof nor egress.
    """
    try:
        path = Path(path)
        if (
            not path.is_absolute()
            or not path.resolve().is_relative_to(PRIVATE_ROOT.resolve())
            or path.is_symlink()
        ):
            raise ValueError("private source roles path required")
        raw = path.read_bytes()
        if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("source roles independent hash mismatch")
        document = json.loads(raw)
        if (
            not isinstance(document, dict)
            or document.get("schema") != "p3-source-runtime-roles.v1"
            or set(document) - {"schema", "roles", "api_key_env_names"}
        ):
            raise ValueError("source roles metadata shape")
        roles = document["roles"]
        names = document.get("api_key_env_names", [])
        if (
            not isinstance(roles, dict)
            or set(roles) != set(LLM_ROLES)
            or not isinstance(names, list)
            or any(not isinstance(name, str) for name in names)
        ):
            raise ValueError("complete source roles required")
        names = set(names)
        env = {}
        for role, settings in roles.items():
            if (
                not isinstance(settings, dict)
                or set(settings) - ROLE_FIELDS
                or not isinstance(settings.get("model"), str)
                or not settings["model"]
            ):
                raise ValueError("source role fields are not sanitized")
            for key, value in settings.items():
                if value is not None and type(value) not in (str, int, float, bool):
                    raise ValueError("source role value must be scalar")
                if key.endswith("api_base") and value is not None:
                    parsed = urlsplit(value)
                    if (
                        parsed.scheme not in {"https", "http"}
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                        or parsed.query
                        or parsed.fragment
                    ):
                        raise ValueError("source role endpoint contains credentials or parameters")
                if key.endswith("api_key_env") and value is not None:
                    if not isinstance(value, str):
                        raise ValueError("credential environment name required")
                    names.add(value)
                env[f"BESTSELLER__LLM__{role.upper()}__{key.upper()}"] = json.dumps(
                    value, ensure_ascii=False, allow_nan=False
                )
        for name in names:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN)", name):
                raise ValueError("only credential environment names may be supplied")
            env[name] = "offline-only-e2e-simulation"
        return env
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise InfrastructureError(
            "trusted source role metadata is missing or invalid", code="prefix_source_roles_invalid"
        ) from error


def resolve_source_roles(explicit):
    if explicit is not None:
        return Path(explicit)
    pointer = PRIVATE_ROOT / "LATEST.json"
    if not pointer.exists():
        return None
    try:
        if pointer.is_symlink():
            raise ValueError("private metadata pointer required")
        metadata = json.loads(pointer.read_text(encoding="utf-8"))
        value = metadata.get("source_roles_path")
        return Path(value) if value is not None else None
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise InfrastructureError(
            "source roles pointer is invalid", code="prefix_source_roles_invalid"
        ) from error


def resolve_source_roles_digest(source_roles):
    if source_roles is None:
        return None
    pointer = PRIVATE_ROOT / "LATEST.json"
    if not pointer.exists():
        return None
    try:
        if pointer.is_symlink():
            raise ValueError("private metadata pointer required")
        metadata = json.loads(pointer.read_text(encoding="utf-8"))
        stored = metadata.get("source_roles_path")
        if stored is not None and Path(stored).resolve() == source_roles.resolve():
            return metadata.get("source_roles_sha256")
        return None
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise InfrastructureError(
            "source roles digest pointer is invalid", code="prefix_source_roles_invalid"
        ) from error


def resolve_prefix_input(bundle, sha256):
    if bundle is not None and sha256 is not None:
        return Path(bundle), sha256
    if bundle is not None or sha256 is not None:
        raise InfrastructureError(
            "prefix bundle and independent SHA256 must be supplied together",
            code="prefix_input_pair_required",
        )
    pointer = PRIVATE_ROOT / "LATEST.json"
    try:
        if pointer.is_symlink():
            raise ValueError("pointer must not be a symlink")
        metadata = json.loads(pointer.read_text(encoding="utf-8"))
        bundle = Path(metadata["bundle_path"])
        sha256 = metadata["bundle_sha256"]
        if not bundle.is_absolute() or not isinstance(sha256, str):
            raise ValueError("pointer fields are invalid")
        return bundle, sha256
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise InfrastructureError(
            "private captured prefix pointer is unavailable; supply explicit input",
            code="prefix_input_required",
        ) from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", default="bestseller-api:latest")
    parser.add_argument("--pg-image", default="ankane/pgvector:latest")
    parser.add_argument("--redis-image", default="redis:7-alpine")
    parser.add_argument(
        "--docker-host", help="local unix:// Docker socket; ambient Docker settings are ignored"
    )
    parser.add_argument(
        "--plan", action="store_true", help="print sanitized commands without calling Docker"
    )
    parser.add_argument(
        "--prior-round2-evidence",
        type=Path,
        help="previous R2 receipt from the parent attempt ledger",
    )
    parser.add_argument(
        "--prefix-bundle", type=Path, help="absolute external private captured prefix"
    )
    parser.add_argument("--prefix-sha256", help="independent trusted bundle digest from capture")
    parser.add_argument(
        "--source-roles",
        type=Path,
        help="private sanitized role metadata, or source_roles_path in LATEST.json",
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="chapter_adoption")
    parser.add_argument(
        "--prefix-only",
        action="store_true",
        help="Stop after independent PG import, native complete-plan and empty-downstream checks",
    )
    args = parser.parse_args(argv)
    try:
        return launch_runner(args)
    except (Exception, KeyboardInterrupt) as error:
        print(
            json.dumps({"phase": "runner_cli", **safe_exception(error)}, sort_keys=True), flush=True
        )
        return 2


def launch_runner(args):
    check_disk(Path(__file__).resolve().parents[2])
    bundle, prefix_sha256 = resolve_prefix_input(args.prefix_bundle, args.prefix_sha256)
    source_roles = resolve_source_roles(args.source_roles)
    plan = Plan(
        Path(__file__).resolve().parents[2],
        app_image=args.app_image,
        pg_image=args.pg_image,
        redis_image=args.redis_image,
        prior_round2_evidence=args.prior_round2_evidence,
        prefix_bundle=bundle,
        prefix_sha256=prefix_sha256,
        scenario=args.scenario,
        prefix_only=args.prefix_only,
        source_roles=source_roles,
        source_roles_sha256=resolve_source_roles_digest(source_roles),
    )
    if args.plan:
        check_disk(plan.worktree)
        print(
            sanitize(
                json.dumps(
                    {
                        "baseline_ref": BASELINE_REF,
                        "attempt_count_owner": "parent",
                        "scenario": plan.scenario,
                        "network": plan.network_command(),
                        "containers": {
                            role: plan.container_command(
                                role,
                                plan.pg_image
                                if role == "pg"
                                else plan.redis_image
                                if role == "redis"
                                else plan.app_image,
                            )
                            for role in ROLES
                        },
                    },
                    indent=2,
                )
            )
        )
        return 0
    return Runner(plan, args.docker_host).run()


if __name__ == "__main__":
    raise SystemExit(main())

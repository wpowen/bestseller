"""Bounded, credential-in-memory capture of the sole authorized source project.

Every production operation uses one READ ONLY transaction with <=30s statements.
Raw source inputs/payloads are written only below the external private directory.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import UUID

from e2e_sim.diagnostics import safe_exception

SOURCE_ID = UUID("fa14e681-2bd9-44f5-93d1-7f2ef9900d69")
SOURCE_SLUG = "realistic-life-1792514984"
PRIVATE_ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/P3")
REPO = Path(__file__).resolve().parents[2]


def validate_private_output(output, root=None):
    """Resolve authority, reject traversal/symlinks, and require a fresh child."""
    root = PRIVATE_ROOT if root is None else Path(root)
    output = Path(output)
    if not root.is_absolute() or not output.is_absolute() or ".." in output.parts:
        raise ValueError("prefix_private_path_invalid")
    if not output.is_relative_to(root) or output == root:
        raise ValueError("prefix_private_path_outside")
    # Check every existing component before resolving, including dangling links.
    for path in (root, *root.parents, output, *output.parents):
        if path.is_symlink():
            raise ValueError("prefix_private_path_symlink")
    resolved_root = root.resolve()
    resolved = output.resolve()
    if not resolved.is_relative_to(resolved_root) or resolved == resolved_root:
        raise ValueError("prefix_private_path_outside")
    if output.exists():
        raise ValueError("prefix_private_path_exists")
    return resolved


def connection_parameters(compose, env):
    service = compose["services"]["db"]

    def expand(value):
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z_0-9]*)(?::-([^}]*))?\}",
            lambda m: str(env.get(m[1]) or m[2] or ""),
            str(value),
        )

    values = {
        key: expand(value)
        for key, value in service["environment"].items()
        if key in {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}
    }
    mapping = expand(service["ports"][0]).split(":")
    return {
        "host": "127.0.0.1",
        "port": int(mapping[-2]),
        "username": values["POSTGRES_USER"],
        "password": values["POSTGRES_PASSWORD"],
        "database": values["POSTGRES_DB"],
    }


def load_authorized_source(source_root, artifact_root):
    """Explicit read-only production configuration; credentials never persisted."""
    from dotenv import dotenv_values
    import yaml

    source = Path(source_root).resolve()
    store = Path(artifact_root)
    if any(
        "bestseller-v3-private" in part or part == "v5-worktrees"
        for part in (*source.parts, *store.parts)
    ):
        raise ValueError("prefix_source_forbidden")
    if (
        not store.is_absolute()
        or ".." in store.parts
        or any(part.is_symlink() for part in (store, *store.parents))
    ):
        raise ValueError("prefix_source_artifacts_invalid")
    resolved = store.resolve()
    if any("bestseller-v3-private" in part or part == "v5-worktrees" for part in resolved.parts):
        raise ValueError("prefix_source_forbidden")
    env = {**dotenv_values(source / ".env"), **dotenv_values(source / ".env.local")}
    compose = yaml.safe_load((source / "docker-compose.yml").read_text())
    catalog = (
        yaml.safe_load((REPO / "config/model_catalog.yaml").read_text())
        if (REPO / "config/model_catalog.yaml").is_file()
        else {}
    )
    names = {item.get("api_key_env") for item in (catalog or {}).get("models", [])}
    names.update(key for key in env if re.fullmatch(r"[A-Z][A-Z0-9_]*_API_(?:KEY|TOKEN)", key))
    return {
        "connection": connection_parameters(compose, env),
        "artifact_root": str(resolved),
        "runtime_environment": {name: env[name] for name in names if env.get(name)},
        "roles": {},
        "settings_environment": env,
        "config_path": source / "config/default.yaml",
        "local_config_path": source / "config/local.yaml",
    }


def load_capture_profile(path):
    """Read only an independent private file; never discover source credentials."""
    path = Path(path)
    root = PRIVATE_ROOT
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not path.is_relative_to(root)
        or path == root
    ):
        raise ValueError("prefix_profile_private_path_required")
    if any(part.is_symlink() for part in (root, *root.parents, path, *path.parents)):
        raise ValueError("prefix_profile_symlink")
    actual, real_root = path.resolve(), root.resolve()
    if not actual.is_relative_to(real_root) or actual == real_root:
        raise ValueError("prefix_profile_private_path_required")
    if not actual.is_file() or actual.stat().st_mode & 0o077 or root.stat().st_mode & 0o077:
        raise ValueError("prefix_profile_private_permissions_required")
    if actual.stat().st_size > 131072:
        raise ValueError("prefix_profile_size")
    value = json.loads(actual.read_text())
    allowed = {"connection", "artifact_root", "roles", "runtime_environment"}
    if (
        not isinstance(value, dict)
        or set(value) - allowed
        or not {"connection", "artifact_root"} <= set(value)
    ):
        raise ValueError("prefix_profile_fields")
    connection = value["connection"]
    if (
        not isinstance(connection, dict)
        or set(connection) != {"host", "port", "database", "username", "password"}
        or connection["host"] != "127.0.0.1"
        or type(connection["port"]) is not int
        or not 0 < connection["port"] < 65536
        or not all(
            isinstance(connection[k], str) and connection[k]
            for k in ("database", "username", "password")
        )
    ):
        raise ValueError("prefix_profile_connection")
    artifact = Path(value["artifact_root"])
    forbidden = REPO.parents[2]
    if (
        not artifact.is_absolute()
        or ".." in artifact.parts
        or artifact.is_relative_to(forbidden)
        or any("bestseller-v3-private" in part or part == "v5-worktrees" for part in artifact.parts)
    ):
        raise ValueError("prefix_profile_artifact_root_forbidden")
    if any(part.is_symlink() for part in (artifact, *artifact.parents)):
        raise ValueError("prefix_profile_artifact_root_symlink")
    resolved_artifact = artifact.resolve()
    if resolved_artifact.is_relative_to(forbidden) or any(
        "bestseller-v3-private" in part or part == "v5-worktrees"
        for part in resolved_artifact.parts
    ):
        raise ValueError("prefix_profile_artifact_root_forbidden")
    roles = value.get("roles", {})
    if not isinstance(roles, dict) or set(roles) - {
        "planner",
        "writer",
        "critic",
        "summarizer",
        "editor",
    }:
        raise ValueError("prefix_profile_roles")
    runtime = value.get("runtime_environment", {})
    if not isinstance(runtime, dict) or any(
        not re.fullmatch(r"[A-Z][A-Z0-9_]*_API_(?:KEY|TOKEN)", key) or not isinstance(item, str)
        for key, item in runtime.items()
    ):
        raise ValueError("prefix_profile_environment_forbidden")
    return {**value, "roles": roles, "runtime_environment": runtime}


def proof_environment(settings):
    """Source verification settings for nested readers; no credential values."""
    result = {
        "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR": str(settings.artifact_store.local_dir),
        "BESTSELLER__ARTIFACT_STORE__MODE": "local",
        "BESTSELLER__LLM__MOCK": "false",
    }
    for name in ("planner", "writer", "critic", "summarizer", "editor"):
        result["BESTSELLER__LLM__" + name.upper()] = json.dumps(
            getattr(settings.llm, name).model_dump(mode="json")
        )
    return result


def freeze_source_runtime(settings):
    """Read the authorized native active profile once, then freeze effective roles."""
    from bestseller.settings import apply_runtime_llm_profile

    profile = Path(settings.artifact_store.local_dir) / "runtime/llm-runtime-profile.json"
    if any(part.is_symlink() for part in (profile, *profile.parents)):
        raise ValueError("prefix_source_runtime_symlink")
    key = "BESTSELLER_LLM_RUNTIME_PROFILE_PATH"
    previous = os.environ.get(key)
    os.environ[key] = str(profile)
    try:
        return apply_runtime_llm_profile(settings)
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def install_proof_environment(settings, runtime):
    """Give all nested settings loads the same declared private configuration."""
    import yaml

    from bestseller.settings import reset_settings_cache

    # Do not borrow live role overrides or provider keys from an ambient host.
    catalog = yaml.safe_load((REPO / "config/model_catalog.yaml").read_text()) or {}
    key_names = {item.get("api_key_env") for item in catalog.get("models", [])}
    for name in ("planner", "writer", "critic", "summarizer", "editor"):
        role = getattr(settings.llm, name)
        key_names.update((role.api_key_env, role.rate_limit_fallback_api_key_env))
    for key in list(os.environ):
        if key.startswith("BESTSELLER__") or key in key_names:
            os.environ.pop(key)
    os.environ.update(runtime)
    os.environ.update(proof_environment(settings))
    os.environ["BESTSELLER_LLM_RUNTIME_PROFILE_PATH"] = str(PRIVATE_ROOT / "no-live-profile.json")
    reset_settings_cache()


def sanitized_role_configuration(settings):
    """Runtime route parameters only; real API key values never enter this file."""
    from urllib.parse import urlsplit

    roles, key_names = {}, set()
    for name in ("planner", "writer", "critic", "summarizer", "editor"):
        value = getattr(settings.llm, name).model_dump(mode="json")
        for field in ("api_base", "rate_limit_fallback_api_base"):
            if value.get(field):
                url = urlsplit(value[field])
                if url.username or url.password or url.query or url.fragment:
                    raise RuntimeError("prefix_source_role_credential_url")
        for field in ("api_key_env", "rate_limit_fallback_api_key_env"):
            if value.get(field):
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", value[field]):
                    raise RuntimeError("prefix_source_role_key_name_invalid")
                key_names.add(value[field])
        roles[name] = value
    return {
        "schema": "p3-source-runtime-roles.v1",
        "roles": roles,
        "api_key_env_names": sorted(key_names),
    }


def network_fence(port):
    def audit(event, args):
        if event == "socket.connect":
            address = args[1]
            if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", port)):
                raise PermissionError("prefix_capture_network_forbidden")
        elif event == "socket.getaddrinfo":
            if args[0] != "127.0.0.1" or args[1] != port:
                raise PermissionError("prefix_capture_dns_forbidden")
        elif event in {"socket.sendto", "socket.sendmsg", "socket.gethostbyname"}:
            raise PermissionError("prefix_capture_egress_forbidden")

    sys.addaudithook(audit)


async def capture(
    profile_path, output, *, source_root=None, artifact_root=None, exclude_authorization_ref=False
):
    output = validate_private_output(output)
    profile = (
        load_authorized_source(source_root, artifact_root)
        if source_root is not None
        else load_capture_profile(profile_path)
    )
    from sqlalchemy import select, text
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    params = profile["connection"]
    network_fence(params["port"])
    from bestseller.infra.db.models import ProjectModel
    from bestseller.settings import load_settings
    from support.planning_prefix_capture import capture_prefix

    source_env = dict(profile.get("settings_environment", profile["runtime_environment"]))
    source_env.update(
        {
            "BESTSELLER__LLM__" + key.upper(): json.dumps(value)
            for key, value in profile["roles"].items()
        }
    )
    settings = load_settings(
        config_path=profile.get("config_path", REPO / "config/default.yaml"),
        local_config_path=profile.get("local_config_path", PRIVATE_ROOT / "no-source-local.yaml"),
        env=source_env,
    )
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(Path(profile["artifact_root"]).resolve())
    if source_root is not None:
        settings = freeze_source_runtime(settings)
    install_proof_environment(settings, profile["runtime_environment"])
    engine = create_async_engine(
        URL.create("postgresql+asyncpg", **params),
        connect_args={
            "timeout": 10,
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "30000",
                "application_name": "par_P3_readonly_prefix_capture",
            },
        },
        pool_size=1,
        max_overflow=0,
    )
    proof = {
        "source_project_id": str(SOURCE_ID),
        "source_project_scope": "fa14_only",
        "production_writes": 0,
        "provider_calls": 0,
        "lock_requests": 0,
    }
    try:
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='30s'"))
            identity = (
                await session.execute(
                    text(
                        "SELECT current_setting('transaction_read_only'), "
                        "current_setting('statement_timeout')"
                    )
                )
            ).one()
            if identity != ("on", "30s"):
                raise RuntimeError("prefix_source_readonly_required")
            project = await session.scalar(select(ProjectModel).where(ProjectModel.id == SOURCE_ID))
            if (
                project is None
                or project.slug != SOURCE_SLUG
                or project.project_type != "fanqie_short"
            ):
                raise RuntimeError("prefix_source_identity_mismatch")
            proof.update(read_only=True, statement_timeout="30s")
            # This verifies native source custody before serializing anything.
            bundle = await capture_prefix(
                session,
                settings,
                project_id=SOURCE_ID,
                exclude_authorization_ref=exclude_authorization_ref,
            )
            await session.rollback()
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        path = output / "prefix.json"
        wire = json.dumps(bundle, ensure_ascii=False, allow_nan=False).encode()
        with path.open("xb") as stream:
            stream.write(wire)
        path.chmod(0o600)
        roles_path = output / "runtime-roles.json"
        roles_document = sanitized_role_configuration(settings)
        roles_document["api_key_env_names"] = sorted(
            set(roles_document["api_key_env_names"]) | set(profile["runtime_environment"])
        )
        roles_wire = json.dumps(
            roles_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        with roles_path.open("xb") as stream:
            stream.write(roles_wire)
        roles_path.chmod(0o600)
        files = [
            {"file": str(path), "bytes": len(wire), "sha256": hashlib.sha256(wire).hexdigest()}
        ]
        files.append(
            {
                "file": str(roles_path),
                "bytes": len(roles_wire),
                "sha256": hashlib.sha256(roles_wire).hexdigest(),
            }
        )
        # Each retained original payload is embedded in prefix.json; list hashes only.
        payloads = [
            {"file": name, "sha256": value["sha256"]}
            for name, value in sorted(bundle["files"].items())
        ]
        proof.update(
            status="pass",
            bundle_path=str(path),
            trusted_bundle_sha256=bundle["sha256"],
            package_sha256=hashlib.sha256(wire).hexdigest(),
            files=files,
            retained_payloads=payloads,
            row_counts={k: len(v) for k, v in bundle["tables"].items()},
            source_plan_sha256=bundle["source_plan_sha256"],
            export_manifest=bundle.get("export_manifest"),
        )
        pointer = PRIVATE_ROOT / "LATEST.json"
        if pointer.exists() or pointer.is_symlink():
            raise RuntimeError("prefix_private_pointer_already_exists")
        with pointer.open("x", encoding="utf-8") as pointer_file:
            pointer_file.write(
                json.dumps(
                    {
                        "bundle_path": str(path),
                        "bundle_sha256": bundle["sha256"],
                        "package_sha256": hashlib.sha256(wire).hexdigest(),
                        "source_roles_path": str(roles_path),
                        "source_roles_sha256": hashlib.sha256(roles_wire).hexdigest(),
                    },
                    indent=2,
                )
            )
        pointer.chmod(0o600)
    except Exception as error:
        proof.update(status="error", phase="source_capture", **safe_exception(error))
    finally:
        await engine.dispose()
    return proof


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--profile", type=Path)
    source.add_argument("--source-root", type=Path)
    parser.add_argument("--source-artifacts", type=Path)
    parser.add_argument(
        "--exclude-authorization-ref",
        action="store_true",
        help="Apply only the user-2026-10-28-1950 isolated-test export projection",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    if shutil.disk_usage("/System/Volumes/Data").free < 5 * 1024**3:
        raise SystemExit("disk_below_5GiB")
    try:
        target = validate_private_output(args.output)
        PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        PRIVATE_ROOT.chmod(0o700)
        if args.source_root is not None and args.source_artifacts is None:
            raise ValueError("prefix_source_artifacts_required")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            proof = asyncio.run(
                capture(
                    args.profile,
                    target,
                    source_root=args.source_root,
                    artifact_root=args.source_artifacts,
                    exclude_authorization_ref=args.exclude_authorization_ref,
                )
            )
    except Exception as error:
        proof = {"status": "error", "phase": "source_capture_setup", **safe_exception(error)}
    logging.disable(previous_logging)
    args.report.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(proof, ensure_ascii=False, sort_keys=True))
    return 0 if proof["status"] == "pass" else 1


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())

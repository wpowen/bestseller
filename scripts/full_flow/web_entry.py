"""Normal web entry for the owned integration environment; no business mocks."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def require(value, code):
    if not value:
        raise RuntimeError(code)


def verify_environment():
    """Validate actual destinations before importing the application."""
    allowed = set()
    for key, prefix, default in (
        ("BESTSELLER__DATABASE__URL", "int-full-flow-", 5432),
        ("BESTSELLER__REDIS__URL", "int-full-flow-", 6379),
    ):
        parsed = urlsplit(os.environ[key])
        require(parsed.hostname and parsed.hostname.startswith(prefix), "FULL_FLOW_FOREIGN_SERVICE")
        if key.endswith("DATABASE__URL"):
            require(
                parsed.path.lstrip("/").startswith("full_flow_")
                and parsed.path.endswith("_test_db"),
                "FULL_FLOW_NOT_TEST_DATABASE",
            )
        port = parsed.port or default
        allowed.update((v[4][0], port) for v in socket.getaddrinfo(parsed.hostname, port))
    for key in ("BESTSELLER__ARTIFACT_STORE__LOCAL_DIR", "BESTSELLER__OUTPUT__BASE_DIR"):
        path = Path(os.environ[key])
        require(
            path.is_absolute() and path.is_relative_to("/private") and ".." not in path.parts,
            "FULL_FLOW_FOREIGN_FILESYSTEM_ROOT",
        )
    require(
        os.environ.get("BESTSELLER_RECOVER_EXPORTS_ON_STARTUP", "false") == "false",
        "FULL_FLOW_EXPORT_RECOVERY_OPTIN_DENIED",
    )
    return allowed


def install_simulation(configuration, private_root, run_id):
    from scripts.full_flow.simulation.checkpoint import PhysicalOrdinalStore
    from scripts.full_flow.simulation.contracts import TemplateCatalog
    from scripts.full_flow.simulation.inventory import scan_repository
    from scripts.full_flow.simulation.private_config import PrivateConfigResolver
    from scripts.full_flow.simulation.runtime_adapter import create_checkpointed_runtime_tape

    from e2e_sim.transport import install_scripted_http

    resolver = PrivateConfigResolver.from_file(
        configuration, private_root=private_root, repository_root=ROOT
    )
    store = PhysicalOrdinalStore(
        private_root / "physical-ordinals.json",
        private_root=private_root,
        repository_root=ROOT,
        run_id=run_id,
        config_sha256=resolver.config_sha256,
        max_sends=4096,
    )
    store.initialize()  # Existing identity is verified; never reset on restart.
    tape = create_checkpointed_runtime_tape(
        checkpoint=store,
        catalog=TemplateCatalog(scan_repository(ROOT)),
        context_resolver=resolver,
        usage_resolver=lambda observation, wire: {"prompt_tokens": 100, "completion_tokens": 100},
        allowed_endpoints=[
            {
                "scheme": "https",
                "host": "api.minimaxi.com",
                "port": 443,
                "path": "/v1/chat/completions",
            }
        ],
        audit_path=private_root / "scripted-sdk-audit.jsonl",
    )
    return install_scripted_http(tape)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("simulation", "real"), required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    allowed = verify_environment()
    require(
        args.private_root.is_absolute() and args.private_root.is_relative_to("/private"),
        "FULL_FLOW_FOREIGN_RUN_ROOT",
    )
    if args.mode == "real":
        # Main writes this receipt only after actual L0+L1 closure at this commit.
        gate = json.loads((args.private_root / "REAL-GATE.json").read_text())
        require(
            gate.get("L0") == gate.get("L1") == "通过"
            and gate.get("max_calls") == 400
            and gate.get("max_billable_tokens") == 20_000_000,
            "FULL_FLOW_REAL_GATE_NOT_PASSED",
        )
        source = Path("/private/pinned-source-manifest.json")
        require(
            source.exists()
            and hashlib.sha256(source.read_bytes()).hexdigest()
            == gate.get("source_manifest_sha256"),
            "FULL_FLOW_REAL_SOURCE_NOT_PINNED",
        )
        proxy = urlsplit(os.environ["HTTPS_PROXY"])
        require(
            proxy.hostname and proxy.hostname.startswith("int-full-flow-"),
            "FULL_FLOW_OWNED_EGRESS_REQUIRED",
        )
        allowed.update(
            (v[4][0], proxy.port or 3128)
            for v in socket.getaddrinfo(proxy.hostname, proxy.port or 3128)
        )
    else:
        require(args.config is not None, "FULL_FLOW_PRIVATE_SCRIPT_REQUIRED")
        install_simulation(args.config, args.private_root, args.run_id)

    def guard(event, values):
        if event == "socket.connect":
            address = values[1]
            require(isinstance(address, tuple), "FULL_FLOW_UNIX_SOCKET_DENIED")
            host, port = address[:2]
            require(
                (host, port) in allowed or ipaddress.ip_address(host).is_loopback,
                "FULL_FLOW_EXTERNAL_NETWORK_DENIED",
            )

    sys.addaudithook(guard)
    # Normal entry still owns settings/runtime initialization and task recovery.
    from bestseller.web.server import serve_web_app

    serve_web_app(host="0.0.0.0", port=args.port, open_browser=False)


if __name__ == "__main__":
    main()

"""Fail closed for Python socket egress; Docker's internal network is the outer fence.

This is deliberately process-local, installed before application imports. It is
not an OS sandbox for arbitrary native extensions or subprocesses. No subprocess
or model executable is needed by this simulation. An audit hook cannot be
removed; tests exercise it in disposable Python processes.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import time


class NetworkFenceError(PermissionError):
    """An attempted operation was refused before calling the socket backend."""


def install_fence(
    allowed: set[tuple[str, int]],
    sentinel: str | Path,
    *,
    http_endpoint: tuple[str, int] | None = None,
) -> None:
    endpoints = frozenset((str(ipaddress.ip_address(host)), int(port)) for host, port in allowed)
    if not endpoints or any(port not in (5432, 6379) for _, port in endpoints):
        raise ValueError("fence accepts only explicit database/Redis IP endpoints")
    if http_endpoint is not None:
        host, port = http_endpoint
        if type(port) is not int or port != 8000:
            raise ValueError("driver HTTP endpoint must use explicit port 8000")
        endpoints = endpoints | {(str(ipaddress.ip_address(host)), port)}
    path = Path(sentinel)
    path.parent.mkdir(parents=True, exist_ok=True)
    original_getaddrinfo = socket.getaddrinfo

    def deny(operation: str, target: object) -> None:
        # Never persist names, payloads, URLs, or unix paths from a failed call.
        event = {
            "denied": True,
            "operation": operation,
            "pid": os.getpid(),
            "at_ns": time.time_ns(),
            "target_sha256": hashlib.sha256(repr(target).encode()).hexdigest(),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        raise NetworkFenceError(f"e2e simulation network denied: {operation}")

    def endpoint(host: object, port: object, operation: str) -> tuple[str, int]:
        try:
            pair = (str(ipaddress.ip_address(host)), int(port))
        except (TypeError, ValueError):
            deny(operation, (host, port))
        if pair not in endpoints:
            deny(operation, pair)
        return pair

    def audit(event: str, args: tuple) -> None:
        if event == "socket.connect":
            sock, address = args
            if (
                sock.family not in (socket.AF_INET, socket.AF_INET6)
                or sock.type != socket.SOCK_STREAM
            ):
                deny(event, address)
            if not isinstance(address, tuple) or len(address) < 2:
                deny(event, address)
            endpoint(address[0], address[1], event)
        elif event in ("socket.sendto", "socket.sendmsg"):
            deny(event, args[-1])
        elif event == "socket.getaddrinfo":
            endpoint(args[0], args[1], event)
        elif event in ("socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
            deny(event, args)
        elif event == "socket.__new__":
            _, family, socktype, _ = args
            if (
                family not in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX)
                or socktype != socket.SOCK_STREAM
            ):
                deny(event, (family, socktype))

    def numeric_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        endpoint(host, port, "socket.getaddrinfo")
        return original_getaddrinfo(host, port, family, type, proto, flags | socket.AI_NUMERICHOST)

    sys.addaudithook(audit)
    socket.getaddrinfo = numeric_getaddrinfo

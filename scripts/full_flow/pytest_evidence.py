"""Linux-test evidence and an endpoint fence; not imported by application code."""

import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit

import pytest

from support.cp1_rehearsal import error_code, failure_differences

_ALLOWED = set()
for key in ("BESTSELLER__DATABASE__URL", "BESTSELLER__REDIS__URL"):
    value = urlsplit(os.environ.get(key, ""))
    if value.hostname:
        port = value.port or (5432 if key.endswith("DATABASE__URL") else 6379)
        _ALLOWED.update((row[4][0], port) for row in socket.getaddrinfo(value.hostname, port))


def _network_guard(event, args):
    if event != "socket.connect":
        return
    address = args[1]
    if not isinstance(address, tuple):
        raise RuntimeError("FULL_FLOW_EXTERNAL_SOCKET_DENIED")
    host, port = address[:2]
    if (host, port) in _ALLOWED or ipaddress.ip_address(host).is_loopback:
        return
    raise RuntimeError("FULL_FLOW_EXTERNAL_NETWORK_DENIED")


sys.addaudithook(_network_guard)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    result = outcome.get_result()
    if not result.failed:
        return
    error = call.excinfo.value
    row = {
        "test": item.nodeid,
        "failed_workflow_type": "isolated pytest native check",
        "failed_workflow_step": result.when,
        "root_exception_type": type(error).__name__,
        "root_error_code": error_code(error),
        "failure_differences": failure_differences(error, item.nodeid),
        "last_successful_llm_template": "Unknown: real provider denied",
        "script_consumed_index": 0,
        "traceback_path": str(Path(os.environ["FULL_FLOW_TEST_EVIDENCE"]) / "pytest.log"),
    }
    with (Path(os.environ["FULL_FLOW_TEST_EVIDENCE"]) / "failures.jsonl").open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")

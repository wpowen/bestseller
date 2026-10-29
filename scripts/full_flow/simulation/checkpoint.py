"""Private, cross-process physical-send ordinals; never a financial ledger.

No files are opened on import/construction. The coordinator explicitly initializes
one store per rehearsal, then shares it across web/worker processes and restarts.
Only native key/hash/attempt identities and hashes are stored, never prompts,
prose, judgments, source quotations, approval or model-payment permissions.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202 -- Private metadata boundary.
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .contracts import canonical, digest, need

SCHEMA = "normal-sdk-physical-ordinal.v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ATTEMPT = re.compile(r"[0-9a-f]{32}\Z")


class PhysicalOrdinalStore:
    def __init__(
        self, path, *, private_root, repository_root, run_id, config_sha256, max_sends=4096
    ) -> None:
        self.path, self.private_root = Path(path), Path(private_root)
        self.repository_root = Path(repository_root)
        self.run_id, self.config_sha256, self.max_sends = run_id, config_sha256, max_sends
        need(
            type(run_id) is str and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id)),
            "SCRIPT_CHECKPOINT_RUN_INVALID",
        )
        need(
            type(config_sha256) is str and bool(_HASH.fullmatch(config_sha256)),
            "SCRIPT_CHECKPOINT_CONFIG_INVALID",
        )
        need(type(max_sends) is int and 1 <= max_sends <= 4096, "SCRIPT_CHECKPOINT_LIMIT_INVALID")

    def _paths(self):
        for item in (self.path, self.private_root, self.repository_root):
            need(item.is_absolute() and ".." not in item.parts, "SCRIPT_CHECKPOINT_PATH_INVALID")
        for item in (self.path, self.private_root):
            need(
                not any(p.is_symlink() for p in (item, *item.parents)), "SCRIPT_CHECKPOINT_SYMLINK"
            )
        root = self.private_root.resolve(strict=True)
        repo = self.repository_root.resolve(strict=True)
        need(
            root.is_dir() and not root.is_relative_to(repo) and not repo.is_relative_to(root),
            "SCRIPT_CHECKPOINT_INSIDE_CHECKOUT",
        )
        need(
            self.path.parent.resolve(strict=True) == root
            and stat.S_IMODE(root.stat().st_mode) == 0o700,
            "SCRIPT_CHECKPOINT_PRIVATE_MODE",
        )
        for parent in (root, *root.parents):
            need(not (parent / ".git").exists(), "SCRIPT_CHECKPOINT_INSIDE_CHECKOUT")
        return self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked(self, *, initialize=False):
        lock = self._paths()
        flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if initialize else 0)
        fd = os.open(lock, flags, 0o600)
        try:
            need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600, "SCRIPT_CHECKPOINT_PRIVATE_MODE")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            need(
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 64_000_000,
                "SCRIPT_CHECKPOINT_PRIVATE_MODE",
            )
            document = json.load(stream)
        need(
            type(document) is dict and document.get("schema") == SCHEMA, "SCRIPT_CHECKPOINT_SCHEMA"
        )
        proof = document.pop("sha256", None)
        need(proof == digest(document), "SCRIPT_CHECKPOINT_HASH")
        need(
            document.get("run_id") == self.run_id
            and document.get("config_sha256") == self.config_sha256
            and document.get("max_sends") == self.max_sends,
            "SCRIPT_CHECKPOINT_IDENTITY_MISMATCH",
        )
        sends = document.get("sends")
        need(
            type(sends) is list
            and len(sends) <= self.max_sends
            and document.get("next_global_ordinal") == len(sends) + 1,
            "SCRIPT_CHECKPOINT_SEQUENCE_INVALID",
        )
        need(
            all(row.get("global_ordinal") == index for index, row in enumerate(sends, 1))
            and len({r["native_attempt_id"] for r in sends}) == len(sends),
            "SCRIPT_CHECKPOINT_SEQUENCE_INVALID",
        )
        return document

    def _write(self, document):
        wire = (canonical({**document, "sha256": digest(document)}) + "\n").encode()
        fd, name = tempfile.mkstemp(prefix=".ordinal-", dir=self.private_root)
        with os.fdopen(fd, "wb") as stream:
            stream.write(wire)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, self.path)
        directory = os.open(self.private_root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def initialize(self):
        """Create once; an existing run is verified and never reset."""
        with self._locked(initialize=True):
            if self.path.exists():
                return deepcopy(self._read())
            document = {
                "schema": SCHEMA,
                "run_id": self.run_id,
                "config_sha256": self.config_sha256,
                "max_sends": self.max_sends,
                "next_global_ordinal": 1,
                "sends": [],
            }
            self._write(document)
            return deepcopy(document)

    def inspect(self):
        with self._locked():
            document = self._read()
        return {
            **deepcopy(document),
            "unresolved_ordinals": [
                r["global_ordinal"]
                for r in document["sends"]
                if r["state"] in {"reserved", "reply_authored"}
            ],
            "native_authority": False,
        }

    def claim_send(
        self,
        *,
        template,
        native_operation_key,
        native_input_hash,
        native_attempt_id,
        wire_sha256,
        project_id=None,
        workflow_run_id=None,
    ):
        need(
            type(template) is str and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", template)),
            "SCRIPT_TEMPLATE_INVALID",
        )
        need(
            all(
                type(v) is str and bool(_HASH.fullmatch(v))
                for v in (native_operation_key, native_input_hash, wire_sha256)
            )
            and type(native_attempt_id) is str
            and bool(_ATTEMPT.fullmatch(native_attempt_id)),
            "SCRIPT_NATIVE_SEND_IDENTITY_REQUIRED",
        )
        with self._locked():
            document = self._read()
            need(
                not any(r["native_attempt_id"] == native_attempt_id for r in document["sends"]),
                "SCRIPT_PHYSICAL_ATTEMPT_REUSED",
            )
            ordinal = document["next_global_ordinal"]
            need(ordinal <= self.max_sends, "SCRIPT_ENTRY_COUNT_UNBOUNDED")
            row = {
                "global_ordinal": ordinal,
                "template": template,
                "template_ordinal": 1 + sum(r["template"] == template for r in document["sends"]),
                "native_operation_key": native_operation_key,
                "native_input_hash": native_input_hash,
                "native_attempt_id": native_attempt_id,
                "wire_sha256": wire_sha256,
                "project_id": str(project_id) if project_id is not None else None,
                "workflow_run_id": str(workflow_run_id) if workflow_run_id is not None else None,
                "state": "reserved",
                "events": [{"event": "reserved"}],
            }
            document["sends"].append(row)
            document["next_global_ordinal"] += 1
            self._write(document)
            return deepcopy(row)

    def _transition(self, ordinal, event, *, expected, metadata=None):
        with self._locked():
            document = self._read()
            need(
                type(ordinal) is int and 1 <= ordinal < document["next_global_ordinal"],
                "SCRIPT_CHECKPOINT_ORDINAL_INVALID",
            )
            row = document["sends"][ordinal - 1]
            need(row["state"] in expected, "SCRIPT_CHECKPOINT_TRANSITION_INVALID")
            row["state"] = event
            row["events"].append({"event": event, **(metadata or {})})
            self._write(document)
            return deepcopy(row)

    def record_reply(self, ordinal, *, response_sha256):
        need(
            type(response_sha256) is str and bool(_HASH.fullmatch(response_sha256)),
            "SCRIPT_CHECKPOINT_REPLY_HASH_INVALID",
        )
        return self._transition(
            ordinal,
            "reply_authored",
            expected={"reserved"},
            metadata={"response_sha256": response_sha256},
        )

    def record_transport(self, ordinal, *, outcome):
        need(
            outcome in {"wire_complete", "transport_fault", "builder_blocked"},
            "SCRIPT_CHECKPOINT_OUTCOME_INVALID",
        )
        return self._transition(ordinal, outcome, expected={"reserved", "reply_authored"})

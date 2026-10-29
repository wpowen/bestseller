"""Finite private recipe selection by durable SDK identity and native wire."""

from __future__ import annotations

# ruff: noqa: ANN001, ANN204, ANN206 -- Explicit private configuration boundary.
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat

from .contracts import digest, need
from .quickstart_builders import BuilderContext, require_builder
from .wire_material import bind_authored_material, pointer


class PrivateConfigResolver:
    def __init__(self, configuration):
        need(
            type(configuration) is dict
            and configuration.get("schema") == "normal-quickstart-private.v1"
            and isinstance(configuration.get("story"), Mapping)
            and type(configuration.get("replies")) is list
            and 1 <= len(configuration["replies"]) <= 4096,
            "SCRIPT_PRIVATE_CONFIG_SCHEMA",
        )
        self.configuration = deepcopy(configuration)
        need(
            all(type(row) is dict for row in configuration["replies"]),
            "SCRIPT_PRIVATE_CONFIG_SCHEMA",
        )
        self.config_sha256 = digest(configuration)

    @classmethod
    def from_file(cls, path, *, private_root, repository_root):
        path, root, repo = map(Path, (path, private_root, repository_root))
        need(
            path.is_absolute()
            and root.is_absolute()
            and repo.is_absolute()
            and ".." not in path.parts,
            "SCRIPT_PRIVATE_ABSOLUTE_PATH_REQUIRED",
        )
        need(not any(p.is_symlink() for p in (path, *path.parents)), "SCRIPT_PRIVATE_PATH_SYMLINK")
        actual_root, actual_repo = root.resolve(strict=True), repo.resolve(strict=True)
        need(
            path.resolve(strict=True).is_relative_to(actual_root)
            and not actual_root.is_relative_to(actual_repo)
            and stat.S_IMODE(actual_root.stat().st_mode) == 0o700,
            "SCRIPT_PRIVATE_ROOT_OVERLAPS_REPOSITORY",
        )
        for parent in (path.parent, *path.parent.parents):
            need(not (parent / ".git").exists(), "SCRIPT_PRIVATE_INSIDE_CHECKOUT")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            need(
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 32_000_000,
                "SCRIPT_PRIVATE_CONFIG_MODE",
            )
            value = json.load(stream)
        return cls(value)

    def __call__(self, observation, wire_body, ticket):
        require_builder(observation.prompt_template)
        need(
            ticket.get("global_ordinal") == observation.call_index,
            "SCRIPT_OBSERVATION_CONTEXT_MISMATCH",
        )
        matches = []
        for row in self.configuration["replies"]:
            if row.get("template") != observation.prompt_template:
                continue
            # At least one finite durable slot selector is mandatory; template
            # alone cannot become an unlimited repeating positive judgment.
            need(
                "global_ordinal" in row or "template_ordinal" in row,
                "SCRIPT_PRIVATE_REPLY_SLOT_REQUIRED",
            )
            if any(
                type(row[key]) is not int or row[key] != ticket[key]
                for key in ("global_ordinal", "template_ordinal")
                if key in row
            ):
                continue
            if (
                row.get("native_operation_key", ticket["native_operation_key"])
                != ticket["native_operation_key"]
            ):
                continue
            if (
                row.get("native_input_hash", ticket["native_input_hash"])
                != ticket["native_input_hash"]
            ):
                continue
            matches.append(row)
        need(len(matches) == 1, "SCRIPT_AUTHORED_REPLY_NOT_UNIQUE")
        row = matches[0]
        expected = row.get("wire_sha256")
        assertions = row.get("wire_assertions", [])
        need(
            expected is not None or (type(assertions) is list and bool(assertions)),
            "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
        )
        if expected is not None:
            need(expected == digest(wire_body), "SCRIPT_AUTHORED_WIRE_MISMATCH")
        for assertion in assertions:
            need(
                type(assertion) is dict
                and set(assertion)
                in ({"pointer", "sha256"}, {"pointer", "equals"}, {"pointer", "contains"}),
                "SCRIPT_WIRE_ASSERTION_INVALID",
            )
            value = pointer(wire_body, assertion["pointer"])
            if "sha256" in assertion:
                okay = digest(value) == assertion["sha256"]
            elif "equals" in assertion:
                okay = value == assertion["equals"]
            else:
                okay = (
                    type(value) is str
                    and type(assertion["contains"]) is str
                    and bool(assertion["contains"])
                    and assertion["contains"] in value
                )
            need(okay, "SCRIPT_AUTHORED_WIRE_MISMATCH")
        subject = row.get("protocol_subject")
        if subject is not None:
            subject, _receipt = bind_authored_material(
                subject, wire_body, expected_wire_sha256=digest(wire_body)
            )
        key = observation.prompt_template + ":" + str(observation.call_index)
        current_body, body_sha = row.get("current_body"), row.get("source_body_sha256")
        if isinstance(current_body, Mapping):
            bound, _receipt = bind_authored_material(
                {"current_body": current_body}, wire_body, expected_wire_sha256=digest(wire_body)
            )
            current_body = bound["current_body"]
            need(
                type(current_body) is str and bool(current_body),
                "SCRIPT_CURRENT_BODY_BINDING_REQUIRED",
            )
            actual_sha = hashlib.sha256(current_body.encode()).hexdigest()
            need(body_sha is None or body_sha == actual_sha, "SCRIPT_CURRENT_BODY_BINDING_REQUIRED")
            body_sha = actual_sha
        return BuilderContext(
            story=self.configuration["story"],
            chapter_number=row.get("chapter_number", 1),
            call_index=observation.call_index,
            current_body=current_body,
            source_body_sha256=body_sha,
            wire_body=wire_body,
            expected_wire_sha256=digest(wire_body),
            protocol_subject=subject,
            submissions={key: deepcopy(row["material"])} if "material" in row else {},
        )

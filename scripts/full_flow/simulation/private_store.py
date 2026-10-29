"""Explicit private-only tape persistence, called only by the coordinator."""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201 -- Explicit filesystem and serialized JSON boundary.
import os
from pathlib import Path
import stat

from .contracts import DEFAULT_LIMITS, TAPE_SCHEMA, canonical, need, validate_response


def write_private_document(
    document, destination, *, private_root, repository_root, limits=DEFAULT_LIMITS
):
    """Exclusive 0600 file in an existing 0700 directory outside the checkout.

    No directories, manifests, pointers, reports, prompts or native state are
    created. Refuse existing/symlink paths; partial failures remain private.
    All ancestor symlinks are rejected rather than silently following them.
    """
    path, private, repository = map(Path, (destination, private_root, repository_root))
    need(
        type(document) is dict
        and set(document) == {"schema", "simulation_only", "allowed_endpoints", "entries"}
        and document["schema"] == TAPE_SCHEMA
        and document["simulation_only"] is True,
        "SCRIPT_DOCUMENT_SCHEMA",
    )
    need(
        type(document["entries"]) is list and 1 <= len(document["entries"]) <= limits.max_entries,
        "SCRIPT_ENTRY_COUNT_UNBOUNDED",
    )
    for entry in document["entries"]:
        need(
            type(entry) is dict and set(entry) == {"id", "match", "responses"},
            "SCRIPT_ENTRY_INVALID",
        )
        need(
            type(entry["responses"]) is list
            and 1 <= len(entry["responses"]) <= limits.max_responses_per_entry,
            "SCRIPT_RESPONSE_COUNT_UNBOUNDED",
        )
        for response in entry["responses"]:
            validate_response(response, limits)
    need(
        path.is_absolute() and private.is_absolute() and repository.is_absolute(),
        "SCRIPT_PRIVATE_ABSOLUTE_PATH_REQUIRED",
    )
    need(".." not in path.parts and ".." not in private.parts, "SCRIPT_PRIVATE_PATH_UNSAFE")
    for item in (path, private):
        need(
            not any(parent.is_symlink() for parent in (item, *item.parents)),
            "SCRIPT_PRIVATE_PATH_SYMLINK",
        )
    repo = repository.resolve(strict=True)
    root = private.resolve(strict=True)
    target = path.resolve()
    need(
        root.is_dir() and not root.is_relative_to(repo) and not repo.is_relative_to(root),
        "SCRIPT_PRIVATE_ROOT_OVERLAPS_REPOSITORY",
    )
    # Also reject the production checkout when repository_root is an integration
    # worktree nested below it. A .git ancestor marks another protected checkout.
    for parent in (target.parent, *target.parent.parents):
        need(not (parent / ".git").exists(), "SCRIPT_PRIVATE_INSIDE_CHECKOUT")
    need(
        target != root and target.is_relative_to(root) and target.parent.is_dir(),
        "SCRIPT_PRIVATE_OUTSIDE_ROOT",
    )
    need(
        stat.S_IMODE(root.stat().st_mode) == 0o700
        and stat.S_IMODE(target.parent.stat().st_mode) == 0o700,
        "SCRIPT_PRIVATE_DIRECTORY_MODE",
    )
    need(not path.exists(), "SCRIPT_PRIVATE_DESTINATION_EXISTS")
    payload = (canonical(document) + "\n").encode()
    need(len(payload) <= limits.max_document_bytes, "SCRIPT_DOCUMENT_UNBOUNDED")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return target

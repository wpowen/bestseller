"""Pure content-default migration; callers own trusted provenance and persistence.

Origin records must come from code-side receipts/import audits, never an LLM's
self-declared authority. ``explicit`` means supplied at the boundary, not proven
human authorship. Unknown history is preserved and listed for a caller decision.
The receipt archives the JSON value, not original database/wire bytes: callers
must archive those, update dependent caches and commit receipt + payload together.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

CONTENT_ORIGIN_POLICY_VERSION = "content-defaults.v1"
_GENERATED_KINDS = frozenset(
    {
        "framework_default",
        "genre_preset",
        "platform_preset",
        "prompt_pack",
        "taxonomy_synthesis",
        "framework_invariant",
    }
)


def content_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def origin_record(
    value: Any, *, kind: str, adopted: bool = False, producer: str = "content_origin_policy"
) -> dict[str, Any]:
    return {
        "kind": kind,
        "content_hash": content_hash(value),
        "adopted": adopted,
        "producer": producer,
        "policy_version": CONTENT_ORIGIN_POLICY_VERSION,
    }


def leaf_values(payload: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Dot paths to JSON fields; lists are atomic content, not numeric paths."""
    result: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            result.update(leaf_values(value, path))
        else:
            result[path] = value
    return result


@dataclass(frozen=True)
class ContentDefaultMigration:
    payload: dict[str, Any]
    origins: dict[str, Any]
    receipt: dict[str, Any]


def migrate_content_defaults(
    payload: Mapping[str, Any],
    *,
    origins: Mapping[str, Any] | None,
    replacements: Mapping[str, Any],
) -> ContentDefaultMigration:
    """Dry-run deterministic field migration with optimistic content preconditions.

    Only caller-selected fields with matching, unadopted generated provenance
    change. Explicit/adopted fields remain; missing/stale evidence becomes
    unknown. Reapply using returned origins for idempotence. No I/O or mutation.
    """
    original = deepcopy(dict(payload))
    effective = deepcopy(original)
    updated_origins = deepcopy(dict(origins or {}))
    paths = set(replacements)
    for path in paths:
        parts = path.split(".")
        for depth in range(1, len(parts)):
            ancestor = ".".join(parts[:depth])
            if ancestor in paths:
                raise ValueError(f"overlapping replacement paths: {ancestor}, {path}")
    changes: list[dict[str, Any]] = []
    unresolved: list[str] = []
    preserved: list[str] = []
    for path, replacement in sorted(replacements.items()):
        current: Any = original
        found = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if not found:
            continue
        evidence = updated_origins.get(path)
        if not isinstance(evidence, dict) or evidence.get("content_hash") != content_hash(current):
            # Do not reuse a previous default receipt after an author edits it.
            updated_origins[path] = origin_record(current, kind="unknown")
            unresolved.append(path)
            continue
        if evidence.get("adopted") or evidence.get("kind") == "explicit":
            preserved.append(path)
            continue
        if evidence.get("kind") not in _GENERATED_KINDS:
            unresolved.append(path)
            continue
        if current == replacement:
            continue
        parent = effective
        parts = path.split(".")
        for part in parts[:-1]:
            parent = parent[part]
        parent[parts[-1]] = deepcopy(replacement)
        changes.append(
            {
                "path": path,
                "original_value": deepcopy(current),
                "replacement_value": deepcopy(replacement),
                "original_origin": deepcopy(evidence),
            }
        )
        updated_origins[path] = origin_record(
            replacement,
            kind="framework_default",
            producer=CONTENT_ORIGIN_POLICY_VERSION,
        )
    before_hash, after_hash = content_hash(original), content_hash(effective)
    receipt = {
        "policy_version": CONTENT_ORIGIN_POLICY_VERSION,
        "migration_id": content_hash(
            {
                "before": before_hash,
                "after": after_hash,
                "changes": changes,
                "origins": dict(origins or {}),
                "replacements": dict(replacements),
                "policy": CONTENT_ORIGIN_POLICY_VERSION,
            }
        ),
        "before_hash": before_hash,
        "after_hash": after_hash,
        "original_payload": original,
        "original_origins": deepcopy(dict(origins or {})),
        "changes": changes,
        "preserved_paths": preserved,
        "unresolved_paths": unresolved,
        "status": "needs_source_review" if unresolved else "ready",
    }
    return ContentDefaultMigration(effective, updated_origins, receipt)

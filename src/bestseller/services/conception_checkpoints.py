"""Versioned, project-less conception recovery checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import WorkflowRunModel, ProjectModel, ChapterModel

CHECKPOINT_KEY = "conception_checkpoint"
CHECKPOINT_INPUT_HASH_KEY = "conception_checkpoint_input_hash"
SCHEMA_VERSION = "conception-checkpoint.v1"
CHECKPOINT_STAGE = "final_candidate_review"
SIGNED_ZERO_RECOVERY_KEY = "conception_checkpoint_signed_zero_recovery"
SIGNED_ZERO_RECOVERY_SCHEMA = "conception-checkpoint-signed-zero-recovery.v1"

_REQUIRED_STATE_TYPES: dict[str, type] = {
    "premise": str,
    "title": str,
    "synopsis": str,
    "writing_profile": dict,
    "story_spine": dict,
    "concept_contract": dict,
    "ctx": dict,
    "tags": list,
    "commercial_brief": dict,
    "world_model_payload": dict,
    "conception_log": list,
    "llm_run_ids": list,
    "chapter_count": int,
    "genre_key": str,
}
_JsonSafeT = TypeVar("_JsonSafeT")


def _assert_json_safe(value: object, *, path: str = "state") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError(f"{path} is not JSON-safe: non-finite float")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} is not JSON-safe: object keys must be strings")
            _assert_json_safe(item, path=f"{path}.{key}")
        return
    raise TypeError(f"{path} is not JSON-safe: {type(value).__name__}")


def _json_safe_copy(value: _JsonSafeT, *, path: str) -> _JsonSafeT:
    _assert_json_safe(value, path=path)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return cast(_JsonSafeT, json.loads(encoded))


def _state_hash(state: object) -> str:
    clean = _json_safe_copy(state, path="state")
    encoded = json.dumps(
        clean,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _positive_float_zeros(value: object) -> object:
    """Canonicalize only float zero signs in an already JSON-safe copy."""
    if isinstance(value, dict):
        return {key: _positive_float_zeros(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_positive_float_zeros(item) for item in value]
    return 0.0 if type(value) is float and value == 0 else value


def _validate_state_contract(state: object) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("conception checkpoint state must be a JSON-safe object")
    clean = _json_safe_copy(state, path="state")
    missing = [key for key in _REQUIRED_STATE_TYPES if key not in clean]
    if missing:
        raise ValueError(
            "conception checkpoint state missing required field(s): " + ", ".join(missing)
        )
    for key, expected_type in _REQUIRED_STATE_TYPES.items():
        value = clean[key]
        if expected_type is int:
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            raise ValueError(
                f"conception checkpoint state field {key} must be {expected_type.__name__}"
            )
    for key in ("premise", "title", "synopsis", "genre_key"):
        if not clean[key].strip():
            raise ValueError(f"conception checkpoint state field {key} must be non-empty")
    if clean["chapter_count"] < 1:
        raise ValueError("conception checkpoint state field chapter_count must be >= 1")
    return clean


def stable_conception_input_hash(attempt: object) -> str:
    """Hash original attempt inputs while excluding attempt identity fields."""

    if isinstance(attempt, Mapping):
        payload = dict(attempt)
    else:
        dump = getattr(attempt, "model_dump", None)
        if not callable(dump):
            raise TypeError("conception input must be a mapping or validated model")
        payload = dump(mode="json")
    payload.pop("attempt_id", None)
    payload.pop("idempotency_key", None)
    clean = _json_safe_copy(payload, path="conception_input")
    encoded = json.dumps(
        clean,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_conception_checkpoint(
    state: dict[str, Any],
    *,
    input_hash: str,
) -> dict[str, Any]:
    """Build a resumable checkpoint without granting approval or promotion."""

    if not isinstance(input_hash, str) or not input_hash.strip():
        raise ValueError("conception checkpoint input_hash must be a non-empty string")
    clean_state = _positive_float_zeros(_validate_state_contract(state))
    return {
        "schema": SCHEMA_VERSION,
        "stage": CHECKPOINT_STAGE,
        "input_hash": input_hash,
        "state": clean_state,
        "state_sha256": _state_hash(clean_state),
    }


def validate_conception_checkpoint(
    checkpoint: dict[str, Any],
    *,
    input_hash: str,
) -> dict[str, Any]:
    """Validate a checkpoint and return an isolated copy of its recovery state."""

    if not isinstance(checkpoint, dict):
        raise ValueError("conception checkpoint must be a JSON-safe object")
    clean_checkpoint = _json_safe_copy(checkpoint, path="checkpoint")
    if clean_checkpoint.get("schema") != SCHEMA_VERSION:
        raise ValueError("conception checkpoint schema mismatch")
    if clean_checkpoint.get("stage") != CHECKPOINT_STAGE:
        raise ValueError("conception checkpoint stage mismatch")
    if not isinstance(input_hash, str) or not input_hash.strip():
        raise ValueError("conception checkpoint input_hash must be a non-empty string")
    if clean_checkpoint.get("input_hash") != input_hash:
        raise ValueError("conception checkpoint input_hash mismatch")
    state = _validate_state_contract(clean_checkpoint.get("state"))
    expected_hash = clean_checkpoint.get("state_sha256")
    if not isinstance(expected_hash, str) or expected_hash != _state_hash(state):
        raise ValueError("conception checkpoint state_sha256 mismatch")
    return _json_safe_copy(state, path="state")


def _strict_producer_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("conception recovery producer JSON has duplicate keys")
        value[key] = item
    return value


def _signed_zero_log_differences(
    retained: object, stored: object, *, path: str,
) -> list[str]:
    """Require exact types/values except archived -0.0 stored as +0.0."""
    if type(retained) is not type(stored):
        raise ValueError("conception recovery producer log differs beyond signed zero")
    if isinstance(retained, dict):
        if retained.keys() != stored.keys():
            raise ValueError("conception recovery producer log keys changed")
        return [leaf for key in sorted(retained) for leaf in _signed_zero_log_differences(
            retained[key], stored[key], path=path + "/" + key.replace("~", "~0").replace("/", "~1"),
        )]
    if isinstance(retained, list):
        if len(retained) != len(stored):
            raise ValueError("conception recovery producer log length changed")
        return [leaf for index, item in enumerate(retained)
                for leaf in _signed_zero_log_differences(
                    item, stored[index], path=f"{path}/{index}",
                )]
    if type(retained) is float and retained == stored == 0:
        old_sign, new_sign = math.copysign(1, retained), math.copysign(1, stored)
        if old_sign == new_sign:
            return []
        if old_sign < 0 < new_sign:
            return [path]
    elif retained == stored:
        return []
    raise ValueError("conception recovery producer log differs beyond signed zero")


def derive_signed_zero_checkpoint_recovery(
    checkpoint: dict[str, Any],
    *,
    input_hash: str,
    retained_producer_json: str,
) -> dict[str, Any]:
    """Explicit archive-backed derivation; never repair arbitrary hash failures.

    The original producer archive is retained as a STRING so JSONB cannot erase
    its numeric spelling. The full reconstructed legacy state must satisfy the
    unchanged strict validator and original declared hash before a new payload
    with canonical float zeros may be built. No original evidence is mutated.
    """
    original = _json_safe_copy(checkpoint, path="checkpoint")
    if not isinstance(original, dict):
        raise ValueError("conception checkpoint must be a JSON-safe object")
    state = _validate_state_contract(original.get("state"))
    if _state_hash(state) == original.get("state_sha256"):
        raise ValueError("conception recovery requires a mismatching legacy checkpoint")
    if not isinstance(retained_producer_json, str) or not retained_producer_json.strip():
        raise ValueError("conception recovery requires retained producer JSON text")
    producer = json.loads(retained_producer_json, object_pairs_hook=_strict_producer_object)
    producer = _json_safe_copy(producer, path="retained_producer")
    if not isinstance(producer, dict) or not isinstance(producer.get("conception_log"), list):
        raise ValueError("conception recovery producer requires conception_log")
    log = producer["conception_log"]
    paths = _signed_zero_log_differences(log, state["conception_log"], path="/conception_log")
    state["conception_log"] = _json_safe_copy(log, path="producer.conception_log")
    if "last_failure" in state:
        failure = state["last_failure"]
        if not isinstance(failure, dict) or not isinstance(failure.get("conception_log"), list):
            raise ValueError("conception recovery last_failure log unavailable")
        paths.extend(_signed_zero_log_differences(
            log, failure["conception_log"], path="/last_failure/conception_log",
        ))
        failure["conception_log"] = _json_safe_copy(log, path="producer.conception_log")
    if not paths:
        raise ValueError("conception recovery has no signed zero differences")
    reconstructed = {**original, "state": state}
    validated = validate_conception_checkpoint(reconstructed, input_hash=input_hash)
    derived = {**original, **build_conception_checkpoint(validated, input_hash=input_hash)}
    validate_conception_checkpoint(derived, input_hash=input_hash)
    return {
        "schema": SIGNED_ZERO_RECOVERY_SCHEMA,
        "input_hash": input_hash,
        "original_checkpoint_sha256": _state_hash(original),
        "stored_state_sha256": _state_hash(original["state"]),
        "original_declared_state_sha256": original["state_sha256"],
        "reconstructed_state_sha256": _state_hash(validated),
        "retained_producer_json": retained_producer_json,
        "retained_producer_sha256": hashlib.sha256(
            retained_producer_json.encode("utf-8")
        ).hexdigest(),
        "changed_paths": paths,
        "original_checkpoint_modified": False,
        "derived_checkpoint": derived,
    }


def validate_signed_zero_checkpoint_recovery(
    recovery: dict[str, Any],
    *,
    checkpoint: dict[str, Any],
    input_hash: str,
) -> dict[str, Any]:
    """Reproduce the entire receipt against the current exact original checkpoint."""
    clean = _json_safe_copy(recovery, path="recovery")
    if not isinstance(clean, dict):
        raise ValueError("conception recovery must be an object")
    expected = derive_signed_zero_checkpoint_recovery(
        checkpoint, input_hash=input_hash,
        retained_producer_json=clean.get("retained_producer_json"),
    )
    if _state_hash(clean) != _state_hash(expected):
        raise ValueError("conception recovery receipt binding mismatch")
    return _json_safe_copy(expected["derived_checkpoint"], path="derived_checkpoint")


def select_conception_checkpoint_for_resume(
    checkpoint: dict[str, Any],
    *,
    input_hash: str,
    signed_zero_recovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select only an explicitly supplied, fully bound proof; never infer recovery."""
    try:
        validate_conception_checkpoint(checkpoint, input_hash=input_hash)
    except ValueError:
        if signed_zero_recovery is None:
            raise
        return validate_signed_zero_checkpoint_recovery(
            signed_zero_recovery, checkpoint=checkpoint, input_hash=input_hash,
        )
    # Later normal checkpoint saves remain authoritative. Keep the old recovery
    # receipt as history without making it override or poison a fresh valid row.
    return _json_safe_copy(checkpoint, path="checkpoint")


def _validate_projectless_initial_run(run: WorkflowRunModel) -> None:
    if run.workflow_type != "conception_initial" or run.project_id is not None:
        raise ValueError(
            "conception checkpoint requires a project-less conception_initial workflow run"
        )


async def _validate_readable_initial_run(session, run, *, expected_project_id=None, expected_intent_hash=None):
    """Read frozen pre-creation state of the same still-empty, unapproved book.

    Linked historical rows stay immutable; only the new project-less attempt
    receives checkpoint updates and must pass every current quality gate.
    """
    if run.project_id is None or expected_project_id is None:
        _validate_projectless_initial_run(run)
        return
    if (run.workflow_type != "conception_initial" or run.status != "completed"
        or expected_project_id is None or run.project_id != expected_project_id
        or not expected_intent_hash
        or (run.metadata_json or {}).get("creation_intent_hash") != expected_intent_hash):
        raise ValueError("conception_linked_checkpoint_identity_mismatch")
    project = await session.scalar(select(ProjectModel).where(ProjectModel.id == expected_project_id)
        .execution_options(populate_existing=True))
    if (project is None or project.status != "planning"
        or (project.metadata_json or {}).get("conception_approved")
        or (project.metadata_json or {}).get("creation_intent_contract_hash") != expected_intent_hash):
        raise ValueError("conception_linked_checkpoint_requires_unapproved_empty_book")
    if await session.scalar(select(ChapterModel.id).where(ChapterModel.project_id == project.id).limit(1)):
        raise ValueError("conception_linked_checkpoint_requires_unapproved_empty_book")
    active = await session.scalar(select(WorkflowRunModel.id).where(
        WorkflowRunModel.project_id == project.id,
        WorkflowRunModel.status.in_(("running", "queued", "pending", "stopping")),
    ).limit(1))
    if active:
        raise ValueError("conception_linked_checkpoint_active_writer")


async def save_conception_checkpoint(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Lock and update one workflow row; transaction ownership stays with the caller."""

    stored_input_hash = checkpoint.get("input_hash") if isinstance(checkpoint, dict) else None
    if not isinstance(stored_input_hash, str):
        raise ValueError("conception checkpoint input_hash must be a non-empty string")
    validate_conception_checkpoint(checkpoint, input_hash=stored_input_hash)
    saved = _json_safe_copy(checkpoint, path="checkpoint")
    run = await session.scalar(
        select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ValueError("conception workflow run not found")
    _validate_projectless_initial_run(run)
    metadata = dict(run.metadata_json or {})
    existing_input_hash = metadata.get(CHECKPOINT_INPUT_HASH_KEY)
    if existing_input_hash not in (None, stored_input_hash):
        raise ValueError("conception checkpoint metadata input_hash mismatch")
    metadata[CHECKPOINT_KEY] = saved
    metadata[CHECKPOINT_INPUT_HASH_KEY] = stored_input_hash
    run.metadata_json = metadata
    await session.flush()
    return _json_safe_copy(saved, path="checkpoint")


async def save_signed_zero_checkpoint_recovery(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    recovery: dict[str, Any],
) -> dict[str, Any]:
    """Persist a separately keyed proof; caller owns commit/rollback and authorization."""
    saved = _json_safe_copy(recovery, path="recovery")
    if not isinstance(saved, dict) or not isinstance(saved.get("input_hash"), str):
        raise ValueError("conception recovery input_hash unavailable")
    run = await session.scalar(
        select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if run is None:
        raise ValueError("conception workflow run not found")
    _validate_projectless_initial_run(run)
    metadata = dict(run.metadata_json or {})
    original = metadata.get(CHECKPOINT_KEY)
    if not isinstance(original, dict):
        raise ValueError("conception recovery original checkpoint unavailable")
    if metadata.get(CHECKPOINT_INPUT_HASH_KEY) not in (None, saved["input_hash"]):
        raise ValueError("conception checkpoint metadata input_hash mismatch")
    validate_signed_zero_checkpoint_recovery(
        saved, checkpoint=original, input_hash=saved["input_hash"],
    )
    existing = metadata.get(SIGNED_ZERO_RECOVERY_KEY)
    if existing is not None:
        if _state_hash(existing) != _state_hash(saved):
            raise ValueError("conception recovery receipt already bound")
        return _json_safe_copy(existing, path="recovery")
    metadata[SIGNED_ZERO_RECOVERY_KEY] = saved
    run.metadata_json = metadata
    await session.flush()
    return _json_safe_copy(saved, path="recovery")


async def load_conception_checkpoint(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    input_hash: str,
    expected_project_id: UUID | None = None,
    expected_intent_hash: str | None = None,
) -> dict[str, Any] | None:
    """Load a matching valid checkpoint without changing workflow state."""

    run = await session.scalar(
        select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    await _validate_readable_initial_run(session, run, expected_project_id=expected_project_id, expected_intent_hash=expected_intent_hash)
    metadata = run.metadata_json or {}
    checkpoint = metadata.get(CHECKPOINT_KEY)
    if checkpoint is None:
        return None
    companion_hash = metadata.get(CHECKPOINT_INPUT_HASH_KEY)
    if companion_hash not in (None, input_hash):
        raise ValueError("conception checkpoint metadata input_hash mismatch")
    if not isinstance(checkpoint, dict):
        raise ValueError("conception checkpoint must be a JSON-safe object")
    selected = select_conception_checkpoint_for_resume(
        checkpoint,
        input_hash=input_hash,
        signed_zero_recovery=metadata.get(SIGNED_ZERO_RECOVERY_KEY),
    )
    if "conception_structure_protocol_recovery" in metadata:
        from bestseller.services.conception_structure_protocol_recovery import select_recovery

        return await select_recovery(session, run=run, checkpoint=selected)
    return selected


__all__ = [
    "CHECKPOINT_STAGE",
    "SCHEMA_VERSION",
    "SIGNED_ZERO_RECOVERY_KEY",
    "SIGNED_ZERO_RECOVERY_SCHEMA",
    "build_conception_checkpoint",
    "derive_signed_zero_checkpoint_recovery",
    "load_conception_checkpoint",
    "save_conception_checkpoint",
    "save_signed_zero_checkpoint_recovery",
    "select_conception_checkpoint_for_resume",
    "stable_conception_input_hash",
    "validate_conception_checkpoint",
    "validate_signed_zero_checkpoint_recovery",
]


REPLAY_CONTEXT_KEY = "conception_replay_context"


def build_replay_context(context, *, input_hash, genre_key, chapter_count):
    clean = _positive_float_zeros(_json_safe_copy(context, path="context"))
    if (not isinstance(clean, dict) or not isinstance(input_hash, str) or not input_hash
        or not isinstance(genre_key, str) or not genre_key or type(chapter_count) is not int or chapter_count < 1):
        raise ValueError("invalid conception replay identity")
    return {"schema": "conception-replay-context.v1", "input_hash": input_hash,
            "genre_key": genre_key, "chapter_count": chapter_count,
            "context": clean, "context_sha256": _state_hash(clean)}


def validate_replay_context(record, *, input_hash, genre_key=None, chapter_count=None):
    record = _json_safe_copy(record, path="replay_context")
    if (not isinstance(input_hash, str) or not input_hash or not isinstance(record, dict)
        or not isinstance(record.get("genre_key"), str) or not record.get("genre_key")
        or type(record.get("chapter_count")) is not int or record["chapter_count"] < 1
        or record.get("schema") != "conception-replay-context.v1"
        or record.get("input_hash") != input_hash or not isinstance(record.get("context"), dict)
        or record.get("context_sha256") != _state_hash(record["context"])):
        raise ValueError("conception replay context identity/hash mismatch")
    if ((genre_key is not None and record.get("genre_key") != genre_key)
        or (chapter_count is not None and record.get("chapter_count") != chapter_count)):
        raise ValueError("conception replay context target mismatch")
    return record["context"]


async def save_replay_context(session, *, workflow_run_id, record):
    validate_replay_context(record, input_hash=record.get("input_hash"))
    run = await session.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
        .with_for_update().execution_options(populate_existing=True))
    if run is None:
        raise ValueError("conception workflow run not found")
    _validate_projectless_initial_run(run)
    metadata = dict(run.metadata_json or {})
    if metadata.get(CHECKPOINT_INPUT_HASH_KEY) not in (None, record["input_hash"]):
        raise ValueError("conception replay metadata input mismatch")
    existing = metadata.get(REPLAY_CONTEXT_KEY)
    if existing is not None and existing != record:
        raise ValueError("conception replay context already frozen")
    metadata[REPLAY_CONTEXT_KEY] = _json_safe_copy(record, path="replay_context")
    run.metadata_json = metadata
    await session.flush()


async def load_replay_context(session, *, workflow_run_id, input_hash, expected_project_id=None, expected_intent_hash=None):
    run = await session.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
        .execution_options(populate_existing=True))
    if run is None:
        return None
    await _validate_readable_initial_run(session, run, expected_project_id=expected_project_id, expected_intent_hash=expected_intent_hash)
    record = (run.metadata_json or {}).get(REPLAY_CONTEXT_KEY)
    if record is not None:
        validate_replay_context(record, input_hash=input_hash)
    return _json_safe_copy(record, path="replay_context")

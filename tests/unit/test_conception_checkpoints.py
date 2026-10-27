from __future__ import annotations

import copy
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from bestseller.services.conception_checkpoints import (
    build_conception_checkpoint,
    load_conception_checkpoint,
    save_conception_checkpoint,
    validate_conception_checkpoint,
)

pytestmark = pytest.mark.unit


def _state() -> dict:
    return {
        "premise": "巡检员发现仙城账目被改写。",
        "title": "仙城坏账",
        "synopsis": "巡检员沿着被改写的公务账目追查责任链。",
        "writing_profile": {"market": {"logline": "巡检员追查仙城坏账。"}},
        "story_spine": {"who": "巡检员", "wants": "找出改账者"},
        "concept_contract": {"schema_version": "concept-contract.v2"},
        "ctx": {"creation_intent": {"allowed_styles": ["修仙职场喜剧"]}},
        "tags": ["仙侠", "职场"],
        "commercial_brief": {"reader_promise": "查清责任链"},
        "world_model_payload": {"setting": "仙城公务体系"},
        "conception_log": [{"stage": "candidate_selected"}],
        "llm_run_ids": [str(uuid4())],
        "chapter_count": 12,
        "genre_key": "xianxia",
        "extra_resume_state": {"candidate_ids": ["a", "b"]},
    }


def test_build_and_validate_preserve_complete_state_with_deterministic_hash() -> None:
    state = _state()
    original = copy.deepcopy(state)

    first = build_conception_checkpoint(state, input_hash="input-v1")
    second = build_conception_checkpoint(copy.deepcopy(state), input_hash="input-v1")

    assert first == second
    assert first["schema"] == "conception-checkpoint.v1"
    assert first["stage"] == "final_candidate_review"
    assert first["input_hash"] == "input-v1"
    assert len(first["state_sha256"]) == 64
    assert first["state"] == original
    assert state == original
    assert set(first) == {"schema", "stage", "input_hash", "state", "state_sha256"}

    restored = validate_conception_checkpoint(first, input_hash="input-v1")
    assert restored == original
    restored["ctx"]["changed"] = True
    assert "changed" not in first["state"]["ctx"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda checkpoint: checkpoint.update(schema="v0"), "schema"),
        (lambda checkpoint: checkpoint.update(stage="approved"), "stage"),
        (lambda checkpoint: checkpoint.update(input_hash="other"), "input_hash"),
        (lambda checkpoint: checkpoint["state"].update(title="tampered"), "sha256"),
        (lambda checkpoint: checkpoint["state"].pop("ctx"), "required"),
        (lambda checkpoint: checkpoint["state"].update(story_spine=[]), "story_spine"),
    ],
)
def test_validate_rejects_wrong_contract_or_tampered_state(mutate, message: str) -> None:
    checkpoint = build_conception_checkpoint(_state(), input_hash="input-v1")
    mutate(checkpoint)

    with pytest.raises(ValueError, match=message):
        validate_conception_checkpoint(checkpoint, input_hash="input-v1")


@pytest.mark.parametrize(
    "unsafe_value",
    [uuid4(), ("tuple",), math.nan, math.inf, {1: "non-string-key"}],
)
def test_build_rejects_non_json_safe_state(unsafe_value) -> None:
    state = _state()
    state["unsafe"] = unsafe_value

    with pytest.raises((TypeError, ValueError), match="JSON-safe"):
        build_conception_checkpoint(state, input_hash="input-v1")


class _FakeSession:
    def __init__(self, row) -> None:
        self.row = row
        self.flush_count = 0
        self.saw_for_update = False

    async def scalar(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.saw_for_update = "FOR UPDATE" in sql
        return self.row

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_save_locks_projectless_initial_run_and_preserves_metadata() -> None:
    checkpoint = build_conception_checkpoint(_state(), input_hash="input-v1")
    row = SimpleNamespace(
        workflow_type="conception_initial",
        project_id=None,
        metadata_json={"keep": {"receipt": 1}},
    )
    session = _FakeSession(row)

    await save_conception_checkpoint(
        session,
        workflow_run_id=uuid4(),
        checkpoint=checkpoint,
    )

    assert session.saw_for_update is True
    assert session.flush_count == 1
    assert row.metadata_json["keep"] == {"receipt": 1}
    assert row.metadata_json["conception_checkpoint"] == checkpoint
    assert row.metadata_json["conception_checkpoint"] is not checkpoint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        SimpleNamespace(
            workflow_type="conception_revision", project_id=None, metadata_json={}
        ),
        SimpleNamespace(
            workflow_type="conception_initial", project_id=uuid4(), metadata_json={}
        ),
    ],
)
async def test_save_rejects_wrong_workflow_ownership(row) -> None:
    session = _FakeSession(row)
    checkpoint = build_conception_checkpoint(_state(), input_hash="input-v1")

    with pytest.raises(ValueError, match="project-less conception_initial"):
        await save_conception_checkpoint(
            session,
            workflow_run_id=uuid4(),
            checkpoint=checkpoint,
        )
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_load_returns_complete_checkpoint_copy_and_rejects_tampering() -> None:
    checkpoint = build_conception_checkpoint(_state(), input_hash="input-v1")
    row = SimpleNamespace(
        workflow_type="conception_initial",
        project_id=None,
        metadata_json={"conception_checkpoint": checkpoint},
    )
    session = _FakeSession(row)

    loaded = await load_conception_checkpoint(
        session,
        workflow_run_id=uuid4(),
        input_hash="input-v1",
    )
    assert loaded == checkpoint
    assert loaded is not checkpoint
    loaded["state"]["title"] = "changed"
    assert checkpoint["state"]["title"] == "仙城坏账"

    row.metadata_json["conception_checkpoint"]["state"]["title"] = "tampered"
    with pytest.raises(ValueError, match="sha256"):
        await load_conception_checkpoint(
            session,
            workflow_run_id=uuid4(),
            input_hash="input-v1",
        )


@pytest.mark.asyncio
async def test_load_returns_none_for_missing_row_or_checkpoint() -> None:
    missing_row = _FakeSession(None)
    assert (
        await load_conception_checkpoint(
            missing_row, workflow_run_id=uuid4(), input_hash="input-v1"
        )
        is None
    )
    row_without_checkpoint = _FakeSession(
        SimpleNamespace(
            workflow_type="conception_initial", project_id=None, metadata_json={}
        )
    )
    assert (
        await load_conception_checkpoint(
            row_without_checkpoint,
            workflow_run_id=uuid4(),
            input_hash="input-v1",
        )
        is None
    )

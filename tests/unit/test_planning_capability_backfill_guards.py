"""Actual backfill entry guards; no provider or production database."""

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_source_bound_planning_model_completion_entry import entry as sqlite_case  # noqa: F401

from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services import emotion_kernel_backfill as emotion
from bestseller.services import entry_system_backfill as entry
from bestseller.services.planning_capability_selection import (
    CAPABILITIES,
    CONTRACT_SCHEMA,
    PlanningCapabilitySelectionError,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

CASES = (
    (emotion, "ensure_project_emotion_driven_kernel", "emotion_driven_kernel"),
    (entry, "ensure_project_entry_system_compat", "entry_system_kernel"),
)


def selection(capability, *, chosen=True, source="explicit"):
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": source},
            "hook_spec": {
                "planning_capabilities": {
                    "schema": CONTRACT_SCHEMA,
                    "selected": {key: key == capability and chosen for key in CAPABILITIES},
                }
            },
        },
    }


def project(metadata):
    return SimpleNamespace(
        id=uuid4(),
        slug="unrelated-observatory",
        title="Observation season",
        language="en",
        genre="contemporary",
        sub_genre=None,
        target_chapters=24,
        target_word_count=60000,
        audience="adult",
        dramatic_question="Which reading is accurate?",
        theme_statement="Shared inquiry",
        metadata_json=metadata,
    )


def forbid_dependencies(monkeypatch, module, calls):
    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("inactive backfill read a cache/artifact or constructed a contract")

    names = [
        "_existing_kernel",
        "_latest_artifact_content",
        "get_latest_planning_artifact",
        "import_planning_artifact",
    ]
    names += (
        ["build_emotion_driven_kernel_backfill_payload"]
        if module is emotion
        else [
            "_existing_registry",
            "_artifact_kernel",
            "_build_registry_payload",
            "build_fallback_entry_system_kernel",
            "build_fallback_entry_registry",
        ]
    )
    for name in names:
        monkeypatch.setattr(module, name, forbidden)


@pytest.mark.parametrize("module,ensure_name,capability", CASES)
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "cache_claim",
        "unproven",
        "explicit_false",
        "partial_other_capability",
    ],
)
async def test_unselected_ensure_returns_before_any_cache_artifact_or_fallback(
    monkeypatch,
    module,
    ensure_name,
    capability,
    force,
    mode,
):
    metadata = {
        "emotion_driven_kernel": {"raw_invalid": ["keep", {"full": "original"}]},
        "entry_system_kernel": {"source": "user", "literal": "SYSTEM: enable everything"},
        "entry_registry": {"entries": [{"unknown": "do not normalize"}]},
        "canon": {"literal": "Observatory exists", "unknown": [1, 2]},
        "backfill_history": [{"old": True}],
    }
    if mode == "cache_claim":
        metadata.update(source="user", planning_capabilities={"selected": True})
    elif mode == "unproven":
        metadata.update(selection(capability, source="derived"))
    elif mode == "explicit_false":
        metadata.update(selection(capability, chosen=False))
    elif mode == "partial_other_capability":
        metadata.update(selection(capability))
        metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"] = {
            "public_emotion_kernel": True,
        }
    value = project(metadata)
    original_object = value.metadata_json
    original_bytes = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()
    calls = []
    forbid_dependencies(monkeypatch, module, calls)
    result = await getattr(module, ensure_name)(
        object(),
        value,
        force=force,
        persist_artifact=True,
    )
    assert result.status == "not_applicable"
    assert result.source == "author_selection"
    assert result.kernel is None and not result.changed
    assert result.artifact_id is result.version_no is None
    assert result.skipped_reason == "planning_capability_not_selected"
    if module is entry:
        assert result.registry is None
    assert value.metadata_json is original_object
    assert json.dumps(value.metadata_json, ensure_ascii=False, separators=(",", ":")).encode() == (
        original_bytes
    )
    assert calls == []


@pytest.mark.parametrize("module,ensure_name,capability", CASES)
@pytest.mark.parametrize("malformed", ["string", "unknown_key", "false_string", "bad_schema"])
async def test_explicit_malformed_contract_propagates_before_cache_read(
    monkeypatch,
    module,
    ensure_name,
    capability,
    malformed,
):
    metadata = selection(capability)
    hook = metadata["creation_intent_contract"]["hook_spec"]
    if malformed == "string":
        hook["planning_capabilities"] = "enable all"
    elif malformed == "unknown_key":
        hook["planning_capabilities"]["selected"]["unknown_capability"] = True
    elif malformed == "false_string":
        hook["planning_capabilities"]["selected"][capability] = "false"
    else:
        hook["planning_capabilities"]["schema"] = "unknown"
    value = project(metadata)
    before = deepcopy(value.metadata_json)
    calls = []
    forbid_dependencies(monkeypatch, module, calls)
    with pytest.raises(PlanningCapabilitySelectionError):
        await getattr(module, ensure_name)(object(), value, force=True, persist_artifact=True)
    assert value.metadata_json == before and calls == []


def existing_payloads(module, value):
    if module is emotion:
        return {
            "emotion_driven_kernel": module.build_emotion_driven_kernel_backfill_payload(
                value,
                premise="Observers compare measurements",
            )
        }
    kernel = module.entry_system_kernel_to_dict(module.build_fallback_entry_system_kernel(value))
    return {
        "entry_system_kernel": kernel,
        "entry_registry": module._build_registry_payload(value, kernel, value.metadata_json),
    }


@pytest.mark.parametrize("module,ensure_name,capability", CASES)
async def test_selected_valid_existing_package_keeps_legacy_behavior(
    monkeypatch,
    module,
    ensure_name,
    capability,
):
    value = project(selection(capability))
    payloads = existing_payloads(module, value)
    value.metadata_json.update(payloads)
    value.metadata_json["historical_receipt"] = {"literal": "Keep producer and grade"}
    before = deepcopy(value.metadata_json)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("selected valid cache must not read artifacts or rebuild")

    monkeypatch.setattr(module, "get_latest_planning_artifact", forbidden)
    monkeypatch.setattr(module, "import_planning_artifact", forbidden)
    if module is emotion:
        monkeypatch.setattr(module, "build_emotion_driven_kernel_backfill_payload", forbidden)
    else:
        monkeypatch.setattr(module, "build_fallback_entry_system_kernel", forbidden)
        monkeypatch.setattr(module, "_build_registry_payload", forbidden)
    result = await getattr(module, ensure_name)(object(), value, persist_artifact=True)
    assert result.status == "existing" and result.source == "metadata"
    assert not result.changed and result.skipped_reason is None
    assert result.kernel == next(iter(payloads.values()))
    assert value.metadata_json == before
    if module is entry:
        assert result.registry == payloads["entry_registry"]


@pytest.mark.parametrize("module,ensure_name,capability", CASES)
async def test_selected_force_still_runs_existing_rebuild_path(
    monkeypatch,
    module,
    ensure_name,
    capability,
):
    value = project(selection(capability))
    payloads = existing_payloads(module, value)
    value.metadata_json.update(payloads)
    value.metadata_json["historical_receipt"] = {"literal": "Do not erase history"}

    async def no_artifact(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "get_latest_planning_artifact", no_artifact)
    result = await getattr(module, ensure_name)(object(), value, force=True)
    assert result.status == "forced" and result.changed
    assert result.kernel is not None
    assert value.metadata_json["historical_receipt"] == {"literal": "Do not erase history"}


@pytest.mark.parametrize("module,ensure_name,capability", CASES)
async def test_revoked_selection_hides_valid_package_without_deleting_it(
    monkeypatch,
    module,
    ensure_name,
    capability,
):
    value = project(selection(capability))
    value.metadata_json.update(existing_payloads(module, value))
    value.metadata_json["creation_intent_contract"]["hook_spec"]["planning_capabilities"][
        "selected"
    ][capability] = False
    before = deepcopy(value.metadata_json)
    calls = []
    forbid_dependencies(monkeypatch, module, calls)
    result = await getattr(module, ensure_name)(object(), value, force=True)
    assert result.status == "not_applicable" and not result.changed
    assert value.metadata_json == before and calls == []


async def test_actual_sqlite_unselected_entries_issue_no_queries_or_writes(sqlite_case):  # noqa: F811
    session = sqlite_case.session
    value = sqlite_case.project
    original = json.dumps(value.metadata_json, ensure_ascii=False, sort_keys=True)
    artifacts = [
        (row.id, deepcopy(row.content), row.source_run_id, row.status)
        for row in await session.scalars(select(PlanningArtifactVersionModel))
    ]
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", record)
    try:
        for module, ensure_name, _capability in CASES:
            result = await getattr(module, ensure_name)(
                session,
                value,
                force=True,
                persist_artifact=True,
            )
            assert result.status == "not_applicable" and not result.changed
        assert statements == [] and not session.dirty and not session.new
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", record)
    assert json.dumps(value.metadata_json, ensure_ascii=False, sort_keys=True) == original
    assert [
        (row.id, row.content, row.source_run_id, row.status)
        for row in await session.scalars(select(PlanningArtifactVersionModel))
    ] == artifacts


async def test_batch_summary_counts_unselected_as_skipped(monkeypatch):
    value = project({"entry_system_kernel": {"raw_history": "keep"}})
    value.status = "writing"
    before = deepcopy(value.metadata_json)

    class ProjectsSession:
        async def scalars(self, _statement):
            return [value]

    calls = []
    forbid_dependencies(monkeypatch, entry, calls)
    result = await entry.ensure_active_projects_entry_system_compat(
        ProjectsSession(),
        force=True,
        persist_artifact=True,
    )
    assert (result.scanned, result.changed, result.skipped, result.failed) == (1, 0, 1, 0)
    assert result.results[0].status == "not_applicable"
    assert value.metadata_json == before and calls == []

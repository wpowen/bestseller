"""RED for the public gateway, using genuine claim/reserve/settle and offline wire."""

import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.orm.attributes import flag_modified

from bestseller.infra.db import models as m
from bestseller.services import chapter_automatic_revision_budget as revision
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# Classification is template/version based, independent of caller flags and role.
BODY_CASES = [
    ("chapter_rewrite", "3.1-lossless-source-time", "editor"),
    ("chapter_rewrite", "3.2-source-selected-voice-editor", "editor"),
    ("chapter_rewrite", "3.3-verified-fact-edit-scope", "editor"),
    ("chapter_rewrite_repair", "1.0", "editor"),
    ("chapter_rewrite_quality_retrofit_repair", "1.0", "editor"),
    ("chapter_localized_opening_repair", "1.0", "editor"),
    ("chapter_localized_ending_repair", "1.0", "editor"),
    ("scene_rewrite", "1.0", "writer"),
    ("scene_writer_regen", "1.0", "writer"),
    ("scene_writer", "deslop_revise", "writer"),
    ("content_validation", "1.0", "critic"),
    ("content_validation", "1.1-frozen-length", "critic"),
    ("reader_chapter_polish", "1.0", "editor"),
    ("reader_chapter_issue_edit", "1.0", "editor"),
    ("reader_issue_segment_writer", "1.0", "writer"),
    ("reader_issue_segment_compress", "1.0", "editor"),
]
NON_BODY_CASES = [
    ("chapter_first_writer", "1.0", "writer"),
    ("scene_writer", "1.0", "writer"),
    ("chapter_review", "1.0", "critic"),
    ("scene_review", "1.0", "critic"),
    ("reader_chapter_issue_acceptance", "1.0", "editor"),
    ("reader_issue_segment_plan", "1.0", "editor"),
]


def record_rejection(request, exc):
    request.node.user_properties.append(("budget_error_code", exc.code))
    request.node.user_properties.append((
        "budget_differences", json.dumps(getattr(exc, "differences", []), sort_keys=True),
    ))


@pytest.mark.parametrize("template,version,role", BODY_CASES)
async def test_known_body_requires_selector_after_native_claim(
    gateway, template, version, role, request,
):
    candidate = gateway.request(template=template, version=version, role=role, selector=False)
    with pytest.raises(
        ExecutionBudgetError, match="automatic_revision_budget:source_selector_missing",
    ) as caught:
        await gateway.call(candidate)
    record_rejection(request, caught.value)
    parent, operations, attempts = await gateway.native()
    assert len(operations) == 1, "the rejection must follow the real native claim"
    assert parent.calls_used == 0 and attempts == []
    assert gateway.sdk_calls == gateway.wires == []
    assert await gateway.journal() is None


@pytest.mark.parametrize("template,version,role", BODY_CASES)
async def test_each_body_template_commits_native_reservation_before_wire(
    gateway, template, version, role,
):
    result = await gateway.call(gateway.request(template=template, version=version, role=role))
    assert result.content == gateway.body
    assert len(gateway.wires) == 1
    wire = gateway.wires[0]
    journal = wire["journal"]
    assert journal is not None, "shared reservation must already be durable at physical send"
    assert wire["owner"] == wire["claim_owner"]
    assert wire["status"] == "pending" and wire["calls_used"] == 1
    assert journal == await gateway.journal()
    assert journal["frozen_source"]["draft_id"] == str(gateway.source_id)
    assert journal["history"] == [{
        "round_no": 1, "operation_key": wire["key"], "operation_scope_id": gateway.scope,
        "operation_input_sha256": wire["input_hash"],
        "source_draft_id": str(gateway.source_id),
        "source_body_sha256": journal["frozen_source"]["body_sha256"],
    }]
    chapter_write = next(i for i, sql in enumerate(gateway.sql)
                         if sql.startswith("update chapters "))
    native_attempt = next(i for i, sql in enumerate(gateway.sql)
                          if sql.startswith("insert into execution_attempts "))
    assert chapter_write < native_attempt
    metadata = await gateway.chapter_metadata()
    assert metadata["unrelated"] == {"keep": [3, 1]}
    assert metadata["requires_machine_repair"] == {"reason": "unresolved"}
    async with gateway.sessions() as session:
        source = await session.get(m.ChapterDraftVersionModel, gateway.source_id)
        assert source.content_md == gateway.body
        assert not source.is_current and source.promotion_state == "candidate"
        assert source.promotion_metadata == {}


@pytest.mark.parametrize("template,version,role", NON_BODY_CASES)
@pytest.mark.parametrize("supplied", [False, True])
async def test_initial_drafts_and_judges_do_not_consume_even_with_selector(
    gateway, template, version, role, supplied,
):
    await gateway.call(gateway.request(
        template=template, version=version, role=role, selector=supplied,
    ))
    assert len(gateway.wires) == 1 and await gateway.journal() is None


@pytest.mark.parametrize("metadata", [{}, {"automatic_revision_source": None},
                                      {"automatic_revision_source": {"approved": True}}])
async def test_legacy_preserves_native_contract_without_source_metadata_writes(gateway, metadata):
    async with gateway.sessions.begin() as session:
        project = await session.get(m.ProjectModel, gateway.project_id)
        project.metadata_json = {"legacy": "keep"}
    before = await gateway.chapter_metadata()
    gateway.sql.clear()
    await gateway.call(gateway.request(metadata=metadata))
    assert len(gateway.wires) == 1
    assert await gateway.chapter_metadata() == before
    assert not any(sql.startswith(("update chapters ", "update projects ",
                                   "insert into chapter_draft_versions ")) for sql in gateway.sql)
    assert (await gateway.native())[0].calls_used == 1  # original native ledger remains active


@pytest.mark.parametrize("case", ["null", "empty", "extra_event", "extra_approval", "extra_project",
                                 "multi_chapter", "bool_id", "noncanonical", "raw_source"])
async def test_selector_is_exact_ids_and_cannot_mint_authority(gateway, case, request):
    value = gateway.selector
    if case == "null":
        value = None
    elif case == "empty":
        value = {}
    elif case == "extra_event":
        value["event_id"] = "new-event"
    elif case == "extra_approval":
        value["approved"] = True
    elif case == "extra_project":
        value["project_id"] = str(gateway.project_id)
    elif case == "multi_chapter":
        value["chapter_id"] = [str(gateway.chapter_id), str(uuid4())]
    elif case == "bool_id":
        value["source_draft_id"] = True
    elif case == "noncanonical":
        value["source_draft_id"] = gateway.source_id.hex
    else:
        value["source_body"] = gateway.body
    with pytest.raises(
        ExecutionBudgetError, match="automatic_revision_budget:source_selector_invalid",
    ) as caught:
        await gateway.call(gateway.request(metadata={"automatic_revision_source": value}))
    record_rejection(request, caught.value)
    assert isinstance(caught.value.__cause__, revision.AutomaticRevisionBudgetError)
    assert caught.value.differences == caught.value.__cause__.differences
    assert caught.value.differences
    assert gateway.body not in str(caught.value.differences)
    assert not gateway.wires and not gateway.sdk_calls
    assert await gateway.journal() is None


async def test_all_branches_workflows_and_source_versions_share_two_rounds(gateway, request):
    await gateway.call(gateway.request(workflow_run_id=await gateway.workflow()), rollback=True)
    first = deepcopy(await gateway.journal())
    assert first is not None, "outer rollback cannot erase shared reservation"
    next_source = uuid4()
    async with gateway.sessions.begin() as session:
        session.add(m.ChapterDraftVersionModel(
            id=next_source, project_id=gateway.project_id, chapter_id=gateway.chapter_id,
            version_no=2, content_md=gateway.body + "\n她回过头。", word_count=30,
            is_current=False, promotion_state="candidate", promotion_metadata={},
        ))
    await gateway.call(gateway.request(
        template="content_validation", role="critic", version="1.1-frozen-length",
        workflow_run_id=await gateway.workflow(),
        metadata={"automatic_revision_source": {
            **gateway.selector, "source_draft_id": str(next_source),
        }},
    ))
    second = deepcopy(await gateway.journal())
    assert second["event_key"] == first["event_key"]
    assert second["frozen_source"] == first["frozen_source"]
    third = gateway.request(
        template="scene_writer", version="deslop_revise", role="writer",
        workflow_run_id=await gateway.workflow(), metadata={
            "automatic_revision_source": gateway.selector,
            "approved": True, "event_id": "caller-cannot-reset", "resume": True,
        },
    )
    with pytest.raises(ExecutionBudgetError, match="automatic_revision_budget:exhausted") as caught:
        await gateway.call(third)
    record_rejection(request, caught.value)
    assert len(gateway.wires) == len(gateway.sdk_calls) == 2
    assert await gateway.journal() == second
    assert (await gateway.native())[0].calls_used == 2


async def test_real_cached_result_precedes_selector_validation_and_never_reserves_again(gateway):
    candidate = gateway.request(operation="stable-rewrite")
    original = await gateway.call(candidate, rollback=True)
    journal = deepcopy(await gateway.journal())
    assert journal is not None
    replay = candidate.model_copy(update={"metadata": {"automatic_revision_source": None}})
    restored = await gateway.call(replay)
    assert restored.content == original.content
    assert restored.metadata["reused_operation_key"] == gateway.wires[0]["key"]
    assert len(gateway.wires) == 1 and await gateway.journal() == journal
    assert (await gateway.native())[0].calls_used == 1


async def test_unknown_consumes_round_and_same_or_new_operation_cannot_send(gateway, request):
    gateway.wire_failure = httpx.ReadTimeout
    candidate = gateway.request(operation="unknown-rewrite")
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown") as caught:
        await gateway.call(candidate)
    record_rejection(request, caught.value)
    journal = deepcopy(await gateway.journal())
    assert journal is not None and len(journal["history"]) == 1
    for operation in (candidate.operation_id, "another-wrapper"):
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await gateway.call(candidate.model_copy(update={"operation_id": operation}))
    assert await gateway.journal() == journal and len(gateway.wires) == 1
    parent, _operations, attempts = await gateway.native()
    assert parent.calls_used == 1 and parent.tokens_charged > 0
    assert len(attempts) == 1 and attempts[0].status == "unknown"


async def test_reservation_rejection_is_terminal_before_generic_rate_limit_fallback(
    gateway, monkeypatch, request,
):
    gateway.settings.llm.retry.rate_limit_fallback_enabled = True
    gateway.settings.llm.editor.rate_limit_fallback_model = "openai/synthetic-fallback"
    gateway.settings.llm.editor.rate_limit_fallback_api_base = "https://revision.test.invalid/v1"
    gateway.settings.llm.editor.rate_limit_fallback_api_key_env = "Q4_OFFLINE_TEST_KEY"
    classified = []

    def adversarial_rate_limit_classifier(exc):
        classified.append(type(exc).__name__)
        return True

    monkeypatch.setattr(llm, "_is_rate_limit_error", adversarial_rate_limit_classifier)
    with pytest.raises(
        ExecutionBudgetError, match="automatic_revision_budget:source_selector_missing",
    ) as caught:
        await gateway.call(gateway.request(selector=False))
    record_rejection(request, caught.value)
    assert isinstance(caught.value.__cause__, revision.AutomaticRevisionBudgetError)
    assert caught.value.scope_id == gateway.scope
    assert caught.value.differences == caught.value.__cause__.differences
    assert caught.value.differences and classified == []
    assert gateway.wires == gateway.sdk_calls == []


@pytest.mark.parametrize("case", ["source_missing", "source_other_chapter", "chapter_other_project",
                                 "operation_scope", "no_chapter_draft"])
async def test_selector_is_not_native_scope_or_source_authority(gateway, case, request):
    selected = gateway.selector
    scope = None
    if case == "source_missing":
        selected["source_draft_id"] = str(uuid4())
    elif case == "source_other_chapter":
        async with gateway.sessions.begin() as session:
            row = await session.get(m.ChapterDraftVersionModel, gateway.source_id)
            row.chapter_id = uuid4()
    elif case == "chapter_other_project":
        async with gateway.sessions.begin() as session:
            row = await session.get(m.ChapterModel, gateway.chapter_id)
            row.project_id = uuid4()
    elif case == "operation_scope":
        scope = f"project:{uuid4()}"
    else:
        async with gateway.sessions.begin() as session:
            await session.execute(delete(m.ChapterDraftVersionModel))
    with pytest.raises(ExecutionBudgetError, match="automatic_revision_budget:") as caught:
        await gateway.call(gateway.request(
            template="scene_rewrite", metadata={"automatic_revision_source": selected},
        ), scope=scope)
    record_rejection(request, caught.value)
    assert gateway.wires == gateway.sdk_calls == []
    assert await gateway.journal() is None


@pytest.mark.parametrize("change", ["control", "source", "owner", "journal_bool", "protocol"])
async def test_physical_wire_rechecks_async_changes_and_keeps_consumed_round(
    gateway, change, request,
):
    reserved = []

    async def change_after_native_reserve(call):
        reserved.append(deepcopy(await gateway.journal()))
        async with gateway.sessions.begin() as session:
            if change == "control":
                row = await session.get(m.BookProductionControlModel, gateway.project_id)
                row.desired_state = "pause"
                row.command_serial += 1
            elif change == "source":
                row = await session.get(m.ChapterDraftVersionModel, gateway.source_id)
                row.content_md += "\n并发编辑。"
            elif change == "owner":
                row = await session.get(m.ExecutionOperationModel, call.claim.key)
                row.owner = "different-native-owner"
            elif change == "protocol":
                row = await session.get(m.ProjectModel, gateway.project_id)
                row.metadata_json = {}
            else:
                row = await session.get(m.ChapterModel, gateway.chapter_id)
                metadata = deepcopy(row.metadata_json)
                if revision.JOURNAL_KEY in metadata:
                    metadata[revision.JOURNAL_KEY]["history"][0]["round_no"] = True
                    row.metadata_json = metadata
                    flag_modified(row, "metadata_json")

    gateway.before_wire = change_after_native_reserve
    with pytest.raises(ExecutionBudgetError) as caught:
        await gateway.call(gateway.request())
    record_rejection(request, caught.value)
    assert len(reserved) == 1 and reserved[0] is not None
    assert len(reserved[0]["history"]) == 1
    assert gateway.wires == [], "must recheck after SDK await, at the real HTTP boundary"
    journal = await gateway.journal()
    assert journal is not None and len(journal["history"]) == 1
    assert journal["event_key"] == reserved[0]["event_key"]
    assert journal["history"][0]["operation_key"] == reserved[0]["history"][0]["operation_key"]
    if change == "journal_bool":
        assert caught.value.code == "automatic_revision_budget:history_invalid"
        assert caught.value.differences[0]["expected"]["type"] == "int"
        assert caught.value.differences[0]["actual"]["type"] == "bool"


@pytest.mark.parametrize("explicit", [False, True])
async def test_context_covers_nested_cleanup_and_identical_explicit_selector(
    gateway, scope_api, explicit,
):
    @scope_api.revision_source_scope
    async def entry():
        scope_api.set_revision_source(
            project_id=gateway.project_id, chapter_id=gateway.chapter_id,
            source_draft_id=gateway.source_id,
        )
        await gateway.call(gateway.request(
            template="content_validation", version="1.1-frozen-length", role="critic",
            selector=explicit,
        ))

    await entry()
    assert len(gateway.wires) == 1
    assert len((await gateway.journal())["history"]) == 1
    assert scope_api.current_revision_source(gateway.project_id) is None


@pytest.mark.parametrize("case", ["conflicting_source", "explicit_null", "different_project"])
async def test_context_cannot_override_explicit_selector_or_cross_project(
    gateway, scope_api, case, request,
):
    @scope_api.revision_source_scope
    async def entry():
        scope_api.set_revision_source(
            project_id=uuid4() if case == "different_project" else gateway.project_id,
            chapter_id=gateway.chapter_id, source_draft_id=gateway.source_id,
        )
        selected = {**gateway.selector, "source_draft_id": str(uuid4())}
        metadata = {"automatic_revision_source": None if case == "explicit_null" else selected}
        if case == "different_project":
            metadata = {}
        await gateway.call(gateway.request(metadata=metadata))

    codes = {
        "conflicting_source": "source_selector_conflict",
        "explicit_null": "source_selector_invalid", "different_project": "source_selector_missing",
    }
    with pytest.raises(ExecutionBudgetError, match="automatic_revision_budget:" + codes[case]) \
            as caught:
        await entry()
    record_rejection(request, caught.value)
    assert gateway.sdk_calls == gateway.wires == []
    assert await gateway.journal() is None


async def test_whole_short_deslop_without_single_chapter_stops_and_preserves_original(gateway):
    with pytest.raises(
        ExecutionBudgetError, match="automatic_revision_budget:source_selector_missing",
    ):
        await gateway.call(gateway.request(
            template="scene_writer", version="deslop_revise", role="writer", selector=False,
            metadata={"chapter_ids": [str(gateway.chapter_id), str(uuid4())], "approved": True},
        ))
    assert gateway.wires == [] and await gateway.journal() is None
    async with gateway.sessions() as session:
        source = await session.get(m.ChapterDraftVersionModel, gateway.source_id)
        assert source.content_md == gateway.body


async def test_child_task_gateway_cannot_borrow_parent_selector(gateway, scope_api):
    @scope_api.revision_source_scope
    async def parent():
        scope_api.set_revision_source(
            project_id=gateway.project_id, chapter_id=gateway.chapter_id,
            source_draft_id=gateway.source_id,
        )
        with pytest.raises(
            ExecutionBudgetError, match="automatic_revision_budget:source_selector_missing",
        ):
            await asyncio.create_task(gateway.call(gateway.request(selector=False)))
        assert scope_api.current_revision_source(gateway.project_id) == gateway.selector

    await parent()
    assert not gateway.wires and await gateway.journal() is None


@pytest.mark.parametrize("control", ["pause", "stop"])
async def test_native_control_stops_before_shared_append_without_worker_checker(gateway, control):
    async with gateway.sessions.begin() as session:
        row = await session.get(m.BookProductionControlModel, gateway.project_id)
        row.desired_state = control
        row.command_serial += 1
    with pytest.raises(ExecutionBudgetError, match="automatic_revision_budget:control_halted"):
        await gateway.call(gateway.request())
    assert gateway.sdk_calls == gateway.wires == [] and await gateway.journal() is None


async def test_managed_outer_transaction_still_refuses_commit_before_claim(gateway):
    async with gateway.sessions.begin() as session:
        with pytest.raises(ExecutionBudgetError, match="execution_transaction_not_releasable"):
            await llm.complete_text(session, gateway.settings, gateway.request())
    parent, operations, attempts = await gateway.native()
    assert parent is None and operations == attempts == []
    assert gateway.sdk_calls == gateway.wires == [] and await gateway.journal() is None

"""RED contract: native custody plus scripted semantics, not reader acceptance.

Requests use strict JSON packets with full source_text (and candidate_text for
verification). Offsets are Unicode code-point [start, end), never byte guesses.
Returned receipts bind actual IDs/operation/run/body hashes and explicitly deny
adoption authority. Native source selection, claims, attempts, physical payloads
and replay are exercised; only provider transport/answers are offline.
"""

from copy import deepcopy
import inspect
import json
from uuid import UUID, uuid4

import httpx
import pytest

from bestseller.infra.db import models as m
from bestseller.services import chapter_automatic_revision_budget as shared
from bestseller.services.budget import ExecutionLedger

from .support import (
    ANCHOR_KEY, ANCHOR_SCHEMA, CHOICE, SOURCE, VERIFY_SCHEMA,
    anchor, anchor_answer, apis, equal, rejected, require, sha, verify_answer,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

CANDIDATE = SOURCE.replace(CHOICE, "林秋退还定金，留下来和妹妹一起补屋顶。")


async def frozen(case, *, anchors=None):
    apis()
    case.answers.append(anchor_answer(anchors=anchors))
    return await case.freeze()


async def verification_input(case):
    receipt = await frozen(case)
    completion = await case.candidate(CANDIDATE)
    require("candidate.native_llm_run", isinstance(completion.llm_run_id, UUID))
    return receipt, completion


async def test_exact_public_api_has_native_selectors_without_permission_flags():
    api = apis()
    selectors = {"session", "settings", "project_id", "chapter_id", "source_draft_id", "workflow_run_id"}
    for name, extra in (("freeze_reader_revision_anchors", set()),
                        ("verify_reader_revision_preservation", {"candidate_text", "candidate_llm_run_id"})):
        func = getattr(api, name)
        sig = inspect.signature(func)
        equal("signature." + name, selectors | extra, set(sig.parameters))
        equal("signature.async", True, inspect.iscoroutinefunction(func))
        for field, parameter in sig.parameters.items():
            if field not in {"session", "settings"}:
                equal("signature.keyword_only." + field, inspect.Parameter.KEYWORD_ONLY, parameter.kind)


async def test_legacy_unmarked_rows_return_none_and_do_not_send_or_migrate(case):
    apis()
    async with case.sessions.begin() as session:
        project = await session.get(m.ProjectModel, case.project_id)
        project.metadata_json = {"legacy": "untouched"}
    before = await case.protected(), await case.chapter_metadata(), await case.ledger()
    equal("legacy.freeze", None, await case.freeze())
    equal("legacy.verify", None, await case.verify(CANDIDATE, uuid4()))
    equal("legacy.rows", before, (await case.protected(), await case.chapter_metadata(), await case.ledger()))
    equal("legacy.wires", 0, len(case.wires))


async def test_freeze_uses_complete_native_first_body_and_pending_physical_receipt(case):
    apis()
    before = await case.protected()
    receipt = await frozen(case)
    equal("anchors.schema", ANCHOR_SCHEMA, receipt["schema"])
    equal("anchors.project", str(case.project_id), receipt["project_id"])
    equal("anchors.chapter", str(case.chapter_id), receipt["chapter_id"])
    equal("anchors.source_id", str(case.source_id), receipt["source_draft_id"])
    equal("anchors.source_hash", sha(SOURCE), receipt["source_body_sha256"])
    equal("anchors.content", anchor_answer()["anchors"], receipt["anchors"])
    equal("anchors.no_adoption", False, receipt["adoption_authority"])
    equal("source.unchanged", before, await case.protected())
    equal("native.wires", 1, len(case.wires))
    request = case.calls[0]
    equal("critic.role", "critic", request.logical_role)
    equal("critic.project", case.project_id, request.project_id)
    equal("critic.workflow", case.workflow_id, request.workflow_run_id)
    require("critic.exact_operation", bool(request.operation_id))
    packet = json.loads(request.user_prompt)
    equal("critic.full_source", SOURCE, packet["source_text"])
    equal("critic.source_id", str(case.source_id), packet["source_draft_id"])
    equal("critic.source_hash", sha(SOURCE), packet["source_body_sha256"])
    equal("critic.no_hype_type", False, "hype_type" in packet)
    wire = case.wires[0]
    equal("native.pending_operation", "pending", wire["status"])
    equal("native.owner", wire["claim_owner"], wire["owner"])
    pending = wire["chapter"][ANCHOR_KEY]
    equal("native.pending_anchors", "pending", pending["state"])
    equal("native.pending_binding.source", str(case.source_id), pending["binding"]["source_draft_id"])
    equal("native.pending_binding.hash", sha(SOURCE), pending["binding"]["source_body_sha256"])
    equal("native.pending_binding.operation", wire["key"], pending["operation_key"])
    run, _, response = await case.physical(UUID(receipt["llm_run_id"]))
    equal("physical.role", "critic", run.logical_role)
    equal("physical.operation", receipt["operation_key"], run.metadata_json["execution_receipt"]["operation_key"])
    equal("physical.source_answer", anchor_answer(), json.loads(response["content"]["text"]))
    equal("physical.not_fallback", False, response["fallback_used"])
    equal("judge.not_a_body_edit", None, (await case.chapter_metadata()).get(shared.JOURNAL_KEY))


async def test_later_source_selector_does_not_replace_first_ordinary_version(case):
    apis()
    later = await case.source("后续改稿已删掉人物选择。", 2)
    case.answers.append(anchor_answer())
    receipt = await case.freeze(source_draft_id=later)
    equal("frozen.first_native_draft", str(case.source_id), receipt["source_draft_id"])
    equal("frozen.full_first_body", SOURCE, json.loads(case.calls[0].user_prompt)["source_text"])


async def test_existing_native_shared_journal_keeps_its_original_frozen_source(case):
    apis()
    ledger = ExecutionLedger(case.engine, case.scope, case.settings.budget)
    claim = await ledger.claim("offline-existing-revision", sha(SOURCE))
    await shared.reserve_chapter_automatic_revision(session_factory=case.sessions,
        settings=case.settings, project_id=case.project_id, chapter_id=case.chapter_id,
        source_draft_id=case.source_id, operation_key=claim.key)
    journal = deepcopy((await case.chapter_metadata())[shared.JOURNAL_KEY])
    # A subsequently imported earlier ordinal must not rewrite the real journal.
    await case.source("后来导入、没有评级的较早序号稿。", 0)
    receipt = await frozen(case)
    equal("journal.frozen_source", journal["frozen_source"]["draft_id"], receipt["source_draft_id"])
    equal("journal.unchanged", journal, (await case.chapter_metadata())[shared.JOURNAL_KEY])


@pytest.mark.parametrize("count", [1, 8])
async def test_present_functions_allow_one_through_eight_without_mandatory_five(case, count):
    selected = [anchor(SOURCE, f"第{i}只纸箱搬到窗边时", i + 1, "action") for i in range(count)]
    receipt = await frozen(case, anchors=selected)
    equal("anchors.actual_count", count, len(receipt["anchors"]))
    equal("anchors.actual_spans", selected, receipt["anchors"])


async def test_reentry_new_workflow_and_source_replays_same_immutable_native_receipt(case):
    first = await frozen(case)
    before_files = case.files()
    before_ledger = await case.ledger()
    original = deepcopy(first)
    first["anchors"][0]["narrative_function"] = "caller mutation"
    async with case.sessions.begin() as session:
        run = await session.get(m.WorkflowRunModel, case.workflow_id)
        run.status = "failed"  # actual failure/reentry, not a new approval
    new_workflow = await case.workflow()
    later = await case.source("后续普通稿。", 2)
    replay = await case.freeze(workflow_run_id=new_workflow, source_draft_id=later)
    equal("replay.same_receipt", original, replay)
    equal("replay.no_paid", before_ledger, await case.ledger())
    equal("replay.physical_bytes", before_files, case.files())
    equal("replay.one_wire", 1, len(case.wires))


async def test_outer_rollback_cannot_turn_a_paid_freeze_into_a_new_operation(case):
    apis()
    case.answers.append(anchor_answer())
    first = await case.freeze(rollback=True)
    before, files = await case.ledger(), case.files()
    equal("rollback.same_receipt", first, await case.freeze())
    equal("rollback.no_repay", before, await case.ledger())
    equal("rollback.physical_immutable", files, case.files())
    equal("rollback.one_wire", 1, len(case.wires))


async def test_concurrent_source_change_during_critic_call_cannot_freeze_stale_anchors(case):
    apis()

    async def change_source(_call):
        async with case.sessions.begin() as session:
            source = await session.get(m.ChapterDraftVersionModel, case.source_id)
            source.content_md += "\n并发写入的新段落。"

    case.before_wire = change_source
    case.answers.append(anchor_answer())
    await rejected(case.freeze)
    before = await case.ledger()
    case.before_wire = None
    await rejected(case.freeze)
    equal("source_CAS.no_repay", before, await case.ledger())
    equal("source_CAS.one_wire", 1, len(case.wires))


@pytest.mark.parametrize("fault", ["project", "chapter", "source", "workflow_project", "workflow_chapter", "workflow_terminal"])
async def test_native_identity_mismatches_reject_before_any_send(case, fault):
    apis()
    change = {}
    if fault in {"project", "chapter", "source"}:
        field = {"project": "project_id", "chapter": "chapter_id", "source": "source_draft_id"}[fault]
        change[field] = uuid4()
    else:
        async with case.sessions.begin() as session:
            run = await session.get(m.WorkflowRunModel, case.workflow_id)
            if fault == "workflow_project":
                run.project_id = uuid4()
            elif fault == "workflow_chapter":
                run.scope_id = uuid4()
            else:
                run.status = "completed"
    before = await case.ledger()
    await rejected(lambda: case.freeze(**change))
    equal("identity.no_paid", before, await case.ledger())
    equal("identity.no_wire", 0, len(case.wires))


@pytest.mark.parametrize("fault", ["empty", "nine", "duplicate_span", "duplicate_id", "bool_offset",
    "wrong_quote", "out_of_bounds", "blank_function", "missing_hash", "wrong_hash",
    "preamble", "duplicate_json_key", "nonfinite", "extra_approved"])
async def test_anchor_parser_is_strict_and_paid_invalid_answer_never_regenerates(case, fault):
    apis()
    answer = anchor_answer()
    if fault == "empty":
        answer["anchors"] = []
    elif fault == "nine":
        answer["anchors"] = [anchor(SOURCE, f"第{i}只纸箱搬到窗边时", i + 1, "action") for i in range(9)]
    elif fault == "duplicate_span":
        answer["anchors"].append({**answer["anchors"][0], "anchor_id": "another-id"})
    elif fault == "duplicate_id":
        answer["anchors"][1]["anchor_id"] = answer["anchors"][0]["anchor_id"]
    elif fault == "bool_offset":
        answer["anchors"][0]["start"] = True
    elif fault == "wrong_quote":
        answer["anchors"][0]["quote"] = "原文不存在的引文"
    elif fault == "out_of_bounds":
        answer["anchors"][0]["end"] = len(SOURCE) + 1
    elif fault == "blank_function":
        answer["anchors"][0]["narrative_function"] = " "
    elif fault == "missing_hash":
        del answer["source_body_sha256"]
    elif fault == "wrong_hash":
        answer["source_body_sha256"] = "0" * 64
    elif fault == "preamble":
        answer = "说明\n" + json.dumps(answer, ensure_ascii=False)
    elif fault == "duplicate_json_key":
        answer = json.dumps(answer, ensure_ascii=False)[:-1] + ', "anchors": []}'
    elif fault == "nonfinite":
        answer["anchors"][0]["end"] = float("nan")
    else:
        answer["approved"] = True
    case.answers.append(answer)
    before = await case.protected()
    await rejected(case.freeze)
    paid = await case.ledger()
    files = case.files()
    await rejected(case.freeze)
    equal("invalid_answer.no_repay", paid, await case.ledger())
    equal("invalid_answer.one_wire", 1, len(case.wires))
    equal("invalid_answer.physical_immutable", files, case.files())
    equal("invalid_answer.no_adoption", before, await case.protected())


@pytest.mark.parametrize("fault", ["source_body", "response_body", "response_missing", "operation_body"])
async def test_frozen_source_or_physical_receipt_tampering_cannot_trigger_new_paid_freeze(case, fault):
    receipt = await frozen(case)
    if fault == "source_body":
        async with case.sessions.begin() as session:
            draft = await session.get(m.ChapterDraftVersionModel, case.source_id)
            draft.content_md += "\n来源被另一个写入者修改。"
    else:
        _, path, response = await case.physical(UUID(receipt["llm_run_id"]))
        if fault == "response_missing":
            path.unlink()  # isolated tmp receipt negative fault, never real custody
        elif fault == "response_body":
            response["content"]["text"] = "{}"
            path.write_text(json.dumps(response))
        else:
            async with case.sessions.begin() as session:
                op = await session.get(m.ExecutionOperationModel, receipt["operation_key"])
                op.result_json = {**op.result_json, "content": "{}"}
    before = await case.ledger()
    await rejected(case.freeze)
    equal("tamper.no_repay", before, await case.ledger())
    equal("tamper.one_wire", 1, len(case.wires))


async def test_native_unknown_freeze_retains_charge_and_blocks_reentry_without_new_wire(case):
    apis()
    case.wire_failure = httpx.ReadTimeout
    await rejected(case.freeze)
    before = await case.ledger()
    equal("unknown.budget", "unknown", before["status"])
    equal("unknown.charged_calls", 1, before["calls"])
    case.wire_failure = None
    await rejected(case.freeze)
    equal("unknown.no_repay", before, await case.ledger())
    equal("unknown.one_wire", 1, len(case.wires))


async def test_preservation_uses_full_original_candidate_and_same_frozen_semantic_anchors(case):
    anchors, candidate = await verification_input(case)
    before = await case.protected()
    answer = verify_answer(anchors["anchors"], CANDIDATE)
    case.answers.append(answer)
    receipt = await case.verify(CANDIDATE, candidate.llm_run_id)
    equal("verification.schema", VERIFY_SCHEMA, receipt["schema"])
    equal("verification.preserved", True, receipt["preserved"])
    equal("verification.no_adoption", False, receipt["adoption_authority"])
    equal("verification.source_id", str(case.source_id), receipt["source_draft_id"])
    equal("verification.source_hash", sha(SOURCE), receipt["source_body_sha256"])
    equal("verification.candidate_run", str(candidate.llm_run_id), receipt["candidate_llm_run_id"])
    equal("verification.candidate_hash", sha(CANDIDATE), receipt["candidate_body_sha256"])
    equal("verification.anchors_hash", sha(anchors["anchors"]), receipt["anchors_sha256"])
    packet = json.loads(case.calls[-1].user_prompt)
    equal("verification.full_original", SOURCE, packet["source_text"])
    equal("verification.full_candidate", CANDIDATE, packet["candidate_text"])
    equal("verification.same_anchors", anchors["anchors"], packet["anchors"])
    equal("verification.packet_candidate_run", str(candidate.llm_run_id), packet["candidate_llm_run_id"])
    equal("verification.no_hype_type", False, "hype_type" in packet)
    equal("verification.critic", "critic", case.calls[-1].logical_role)
    _, _, response = await case.physical(UUID(receipt["llm_run_id"]))
    equal("verification.actual_physical_answer", answer, json.loads(response["content"]["text"]))
    equal("verification.no_adoption_writes", before, await case.protected())
    before_ledger, before_files = await case.ledger(), case.files()
    equal("verification.exact_replay", receipt, await case.verify(CANDIDATE, candidate.llm_run_id))
    equal("verification.replay_no_paid", before_ledger, await case.ledger())
    equal("verification.physical_immutable", before_files, case.files())


@pytest.mark.parametrize("fault", ["one_function_lost", "voice_lost", "procedure_intrusion"])
async def test_any_lost_function_voice_or_intrusion_rejects_without_ratio_escape(case, fault):
    selected = [anchor(SOURCE, f"第{i}只纸箱搬到窗边时", i + 1, "action") for i in range(8)]
    anchors = await frozen(case, anchors=selected)
    candidate = await case.candidate(CANDIDATE)
    answer = verify_answer(anchors["anchors"], CANDIDATE,
        lost=("a1",) if fault == "one_function_lost" else (),
        voice=fault != "voice_lost", no_intrusion=fault != "procedure_intrusion")
    case.answers.append(answer)
    receipt = await case.verify(CANDIDATE, candidate.llm_run_id)
    equal("semantic_failure.no_forty_percent_escape", False, receipt["preserved"])
    equal("semantic_failure.no_adoption", False, receipt["adoption_authority"])
    before = await case.ledger()
    equal("semantic_failure.exact_replay", receipt, await case.verify(CANDIDATE, candidate.llm_run_id))
    equal("semantic_failure.no_repay", before, await case.ledger())


@pytest.mark.parametrize("fault", ["missing_voice", "bool_string", "missing_anchor", "duplicate_anchor",
    "invented_evidence", "empty_evidence", "wrong_source_hash", "wrong_candidate_hash", "neutral_fallback",
    "badness_escape", "preamble"])
async def test_verification_json_never_defaults_missing_or_invalid_semantics_to_pass(case, fault):
    anchors, candidate = await verification_input(case)
    answer = verify_answer(anchors["anchors"], CANDIDATE)
    if fault == "missing_voice":
        del answer["voice_preserved"]
    elif fault == "bool_string":
        answer["voice_preserved"] = "true"
    elif fault == "missing_anchor":
        answer["anchors"].pop()
    elif fault == "duplicate_anchor":
        answer["anchors"][1] = deepcopy(answer["anchors"][0])
    elif fault == "invented_evidence":
        answer["anchors"][0]["evidence"][0]["quote"] = "候选中不存在的证明"
    elif fault == "empty_evidence":
        answer["anchors"][0]["evidence"] = []
    elif fault == "wrong_source_hash":
        answer["source_body_sha256"] = "0" * 64
    elif fault == "wrong_candidate_hash":
        answer["candidate_body_sha256"] = "0" * 64
    elif fault == "neutral_fallback":
        answer = {"prose_quality_score": 0.7, "comment": "fallback"}
    elif fault == "badness_escape":
        answer["source_badness_ratio"] = 0.4
    else:
        answer = "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```"
    case.answers.append(answer)
    await rejected(lambda: case.verify(CANDIDATE, candidate.llm_run_id))
    before = await case.ledger()
    await rejected(lambda: case.verify(CANDIDATE, candidate.llm_run_id))
    equal("invalid_verification.no_repay", before, await case.ledger())
    equal("invalid_verification.wires", 3, len(case.wires))


@pytest.mark.parametrize("fault", ["body", "run_missing", "run_project", "run_workflow",
    "physical_missing", "physical_body", "operation_body"])
async def test_candidate_requires_exact_native_run_and_full_physical_body_before_judge(case, fault):
    _, candidate = await verification_input(case)
    body, run_id = CANDIDATE, candidate.llm_run_id
    if fault == "body":
        body += "\n调用者额外改写了末句。"
    elif fault == "run_missing":
        run_id = uuid4()
    elif fault in {"run_project", "run_workflow"}:
        async with case.sessions.begin() as session:
            run = await session.get(m.LlmRunModel, run_id)
            if fault == "run_project":
                run.project_id = uuid4()
            else:
                run.workflow_run_id = uuid4()
    else:
        run, path, response = await case.physical(run_id)
        if fault == "physical_missing":
            path.unlink()
        elif fault == "physical_body":
            response["content"]["text"] += "\n篡改保留的响应。"
            path.write_text(json.dumps(response))
        else:
            async with case.sessions.begin() as session:
                op = await session.get(m.ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"])
                op.result_json = {**op.result_json, "content": "changed cache"}
    before = await case.ledger()
    await rejected(lambda: case.verify(body, run_id))
    equal("candidate.invalid_no_paid", before, await case.ledger())
    equal("candidate.invalid_no_judge_wire", 2, len(case.wires))


async def test_same_candidate_bytes_with_distinct_native_runs_cannot_share_verification_receipt(case):
    anchors, first = await verification_input(case)
    answer = verify_answer(anchors["anchors"], CANDIDATE)
    case.answers.append(answer)
    one = await case.verify(CANDIDATE, first.llm_run_id)
    second = await case.candidate(CANDIDATE)
    case.answers.append(answer)
    two = await case.verify(CANDIDATE, second.llm_run_id)
    equal("candidate.distinct_runs", False, first.llm_run_id == second.llm_run_id)
    equal("candidate.second_receipt_run", str(second.llm_run_id), two["candidate_llm_run_id"])
    equal("candidate.same_hash", one["candidate_body_sha256"], two["candidate_body_sha256"])
    equal("candidate.distinct_judge_operation", False, one["operation_key"] == two["operation_key"])
    equal("candidate.distinct_judge_run", False, one["llm_run_id"] == two["llm_run_id"])


async def test_native_unknown_verification_keeps_anchors_and_never_resends(case):
    anchors, candidate = await verification_input(case)
    case.wire_failure = httpx.ReadTimeout
    await rejected(lambda: case.verify(CANDIDATE, candidate.llm_run_id))
    before, files = await case.ledger(), case.files()
    equal("verify_unknown.budget", "unknown", before["status"])
    case.wire_failure = None
    await rejected(lambda: case.verify(CANDIDATE, candidate.llm_run_id))
    equal("verify_unknown.no_repay", before, await case.ledger())
    equal("verify_unknown.no_new_wire", 3, len(case.wires))
    equal("verify_unknown.physical_retained", files, case.files())
    require("verify_unknown.original_anchor_run", str(anchors["llm_run_id"]) in " ".join(files))

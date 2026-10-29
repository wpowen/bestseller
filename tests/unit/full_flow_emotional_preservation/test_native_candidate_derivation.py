"""Offline native custody plus deterministic derivation, not semantic acceptance.

These cases retain real SQLite rows, native reservations and physical payloads.
Only the SDK/HTTP and critic answers use the existing explicit offline fixture.
No fact-scope approval, grade, promotion, or standalone derivation authority is
constructed. Main alone executes these tests.
"""

from copy import deepcopy
import json
from uuid import UUID

import pytest

from bestseller.infra.db import models as m
from bestseller.services import anti_slop, drafts

from .support import SOURCE, anchor_answer, equal, rejected, require, sha, verify_answer
from .test_reader_revision_preservation import CANDIDATE

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def sanitized_input(case):
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    raw = "<!-- ordinary native response note -->\n" + CANDIDATE
    completion = await case.candidate(raw)
    candidate = drafts.sanitize_novel_markdown_content(completion.content)
    equal("normalizer.removes_only_note", CANDIDATE, candidate)
    return anchors, completion, candidate


async def test_native_sanitizer_exact_replay_keeps_raw_and_candidate_custody(case):
    anchors, completion, candidate = await sanitized_input(case)
    case.answers.append(verify_answer(anchors["anchors"], candidate))
    receipt = await case.verify(candidate, completion.llm_run_id)
    equal("sanitized.preserved", True, receipt["preserved"])
    equal("sanitized.no_adoption_authority", False, receipt["adoption_authority"])
    equal("sanitized.raw_hash", sha(completion.content), receipt["raw_body_sha256"])
    equal("sanitized.candidate_hash", sha(candidate), receipt["candidate_body_sha256"])
    require(
        "sanitized.distinct_bytes", receipt["raw_body_sha256"] != receipt["candidate_body_sha256"]
    )
    proof = receipt["derivation"]
    equal(
        "sanitized.normalizer",
        "bestseller.services.drafts.sanitize_novel_markdown_content",
        proof["normalizer"],
    )
    equal("sanitized.actual_default_arguments", {"language": None}, proof["arguments"])
    equal("sanitized.code_custody", sha(proof["code_custody"]), proof["code_sha256"])
    equal(
        "sanitized.native_source", str(case.source_id), proof["source_binding"]["source_draft_id"]
    )
    equal(
        "sanitized.native_source_hash", sha(SOURCE), proof["source_binding"]["source_body_sha256"]
    )
    equal(
        "sanitized.native_operation",
        receipt["candidate_physical"]["operation_key"],
        proof["source_binding"]["operation_key"],
    )
    packet = json.loads(case.calls[-1].user_prompt)
    equal("sanitized.critic_exact_candidate", candidate, packet["candidate_text"])
    equal("sanitized.critic_exact_derivation", proof, packet["derivation"])
    _, _, response = await case.physical(completion.llm_run_id)
    equal("sanitized.physical_stays_raw", completion.content, response["content"]["text"])
    before = await case.ledger(), case.files()
    equal(
        "sanitized.immutable_replay", receipt, await case.verify(candidate, completion.llm_run_id)
    )
    equal("sanitized.no_repay", before, (await case.ledger(), case.files()))


async def test_normal_reviews_default_language_is_not_replaced_with_project_language(case):
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    raw = CANDIDATE + "\n" + anti_slop._TIER1_KILL_PHRASES_EN[0] + "."
    completion = await case.candidate(raw)
    candidate = drafts.sanitize_novel_markdown_content(completion.content)
    require(
        "native_default_differs_from_zh_only",
        candidate != drafts.sanitize_novel_markdown_content(completion.content, language="zh-CN"),
    )
    case.answers.append(verify_answer(anchors["anchors"], candidate))
    receipt = await case.verify(candidate, completion.llm_run_id)
    equal("native_default_preserved", True, receipt["preserved"])
    equal("native_default_arguments", {"language": None}, receipt["derivation"]["arguments"])


@pytest.mark.parametrize("transform", ["append", "delete", "fact_projection"])
async def test_arbitrary_or_fact_projected_bytes_have_no_extra_adoption_or_paid_judge(
    case, transform
):
    _, completion, candidate = await sanitized_input(case)
    if transform == "append":
        candidate += "\n调用者添加了另一个没有物理来源的结果。"
    elif transform == "delete":
        candidate = "\n".join(candidate.splitlines()[1:])
    else:
        # Negative unsupported projection: no fabricated authorized spans or
        # caller flags are passed to the public API.
        candidate = candidate.replace("旧收音机", "木盒")
    before = await case.ledger(), case.files(), await case.protected()
    error = await rejected(lambda: case.verify(candidate, completion.llm_run_id))
    equal(
        "unsupported_derivation.explicit_failure",
        "reader_revision:candidate.native_derivation",
        error.code,
    )
    equal(
        "unsupported_derivation.no_new_calls_or_adoption",
        before,
        (await case.ledger(), case.files(), await case.protected()),
    )
    equal("unsupported_derivation.only_freeze_and_candidate", 2, len(case.wires))


async def test_normal_sanitizer_custody_does_not_override_negative_semantics(case):
    anchors, completion, candidate = await sanitized_input(case)
    case.answers.append(verify_answer(anchors["anchors"], candidate, lost=("a1",)))
    receipt = await case.verify(candidate, completion.llm_run_id)
    equal("sanitizer_is_not_semantic_authority", False, receipt["preserved"])
    equal("sanitizer_is_not_adoption_authority", False, receipt["adoption_authority"])


async def test_native_candidate_physical_change_during_critic_is_rejected_without_repay(case):
    anchors, completion, candidate = await sanitized_input(case)
    _, path, physical = await case.physical(completion.llm_run_id)

    async def change_physical(_call):
        changed = deepcopy(physical)
        changed["content"]["text"] += "\n被修改的物理响应。"
        path.write_text(json.dumps(changed, ensure_ascii=False))

    case.before_wire = change_physical
    case.answers.append(verify_answer(anchors["anchors"], candidate))
    await rejected(lambda: case.verify(candidate, completion.llm_run_id))
    before = await case.ledger()
    case.before_wire = None
    await rejected(lambda: case.verify(candidate, completion.llm_run_id))
    equal("candidate_changed_after_send.no_repay", before, await case.ledger())
    equal("candidate_changed_after_send.only_one_judge", 3, len(case.wires))


async def test_changed_derivation_receipt_does_not_buy_another_verification(case):
    anchors, completion, candidate = await sanitized_input(case)
    case.answers.append(verify_answer(anchors["anchors"], candidate))
    receipt = await case.verify(candidate, completion.llm_run_id)
    async with case.sessions.begin() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        metadata = deepcopy(chapter.metadata_json)
        journal = metadata["reader_revision_preservation"]
        key, entry = next(iter(journal.items()))
        entry["binding"]["derivation"]["code_sha256"] = "0" * 64
        journal[sha(entry["binding"])] = journal.pop(key)
        chapter.metadata_json = metadata
    before = await case.ledger(), case.files()
    error = await rejected(lambda: case.verify(candidate, UUID(receipt["candidate_llm_run_id"])))
    equal(
        "changed_binding.explicit_failure",
        "reader_revision:candidate.replay_binding_changed",
        error.code,
    )
    equal("changed_binding.no_repay", before, (await case.ledger(), case.files()))

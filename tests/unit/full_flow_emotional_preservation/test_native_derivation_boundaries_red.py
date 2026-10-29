"""Next bounded RED: deslop byte normalization and fact-source admission.

The strip cases are PURE COMPONENT checks, not native positive evidence.
Fact cases use native rows/gateway/ledger but intentionally lack valid fact
authority. No primary/window PASS, grade, adopted/current body, or approved
scope is manufactured. A complete native positive fact fixture is Unknown.
Main runs these tests before authorizing the corresponding implementation.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.services import budget, llm, reader_judge
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.services.rewrite_fact_scope import derive_fact_repair_scope

from .support import SOURCE, anchor_answer, equal, rejected, require, sha, verify_answer
from .test_reader_revision_preservation import CANDIDATE

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("padding", [("\n", "\n"), ("\u3000", "\t ")])
def test_pure_deslop_strip_is_named_exact_derivation_without_adoption_authority(padding):
    """No native IDs or paid authority: exercise only the pure byte contract."""
    raw = padding[0] + CANDIDATE + padding[1]
    request = llm.LLMCompletionRequest(
        logical_role="writer",
        system_prompt="Pure normalization component",
        user_prompt=SOURCE,
        fallback_response="unavailable",
        prompt_template="scene_writer",
        prompt_version="deslop_revise",
    )
    proof = reader_judge._reader_revision_candidate_derivation(
        raw,
        raw.strip(),
        request=request,
        source_binding={},
        project_language="zh-CN",
    )
    equal("deslop.normalizer", "builtins.str.strip", proof["normalizer"])
    equal("deslop.raw_hash", sha(raw), proof["raw_body_sha256"])
    equal("deslop.candidate_hash", sha(raw.strip()), proof["candidate_body_sha256"])
    equal("deslop.parameters", {}, proof["arguments"])
    require(
        "deslop.code_custody",
        isinstance(proof["code_sha256"], str) and len(proof["code_sha256"]) == 64,
    )
    equal("deslop.no_adoption_claim", False, proof.get("adoption_authority", False))


@pytest.fixture
async def fact_case(case):
    # DDL-only SQLite adapter, matching the established base fixture. Actual
    # ORM task/report rows remain available to the production verifier.
    metadata = MetaData()
    for model in (m.ReviewReportModel, m.RewriteTaskModel):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type, column.server_default = JSON(), None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with case.engine.begin() as connection:
        for table in metadata.tables.values():
            await connection.execute(CreateTable(table))

    report_id, task_id = uuid4(), uuid4()
    payload = {
        "draft_id": str(case.source_id),
        "reviewed_body_fingerprint": chapter_review_body_fingerprint(SOURCE),
    }
    # The native reader must say unavailable. No hand-built passed booleans,
    # primary receipt, commercial authority or quality score is added.
    equal("fixture.fact_positive_unknown", None, derive_fact_repair_scope(SOURCE, payload))
    async with case.sessions.begin() as session:
        session.add(
            m.ReviewReportModel(
                id=report_id,
                project_id=case.project_id,
                target_type="chapter",
                target_id=case.chapter_id,
                reviewer_type="critic",
                verdict="rewrite",
                structured_output=payload,
            )
        )
        session.add(
            m.RewriteTaskModel(
                id=task_id,
                project_id=case.project_id,
                trigger_type="chapter_review",
                trigger_source_id=case.chapter_id,
                rewrite_strategy="chapter_rewrite",
                status="running",
                attempts=1,
                instructions="Retain unknown fact authority until native proof is available.",
                metadata_json={
                    "review_report_id": str(report_id),
                    "parent_draft_id": str(case.source_id),
                    "parent_draft_hash": sha(SOURCE),
                },
            )
        )
    return case, task_id, report_id


async def fact_candidate(case, task_id, *, sanitized):
    raw = ("<!-- normal canonical sanitizer -->\n" if sanitized else "") + CANDIDATE
    case.answers.append(raw)
    request = llm.LLMCompletionRequest(
        logical_role="editor",
        system_prompt="Offline native fact request custody",
        user_prompt=SOURCE,
        fallback_response="unavailable",
        prompt_template="chapter_rewrite",
        prompt_version="3.3-verified-fact-edit-scope",
        project_id=case.project_id,
        workflow_run_id=case.workflow_id,
        operation_id=f"fixture-native-fact:{uuid4()}",
        metadata={
            "rewrite_task_id": str(task_id),
            "automatic_revision_source": {
                "chapter_id": str(case.chapter_id),
                "source_draft_id": str(case.source_id),
            },
        },
    )
    with (
        budget.bind_execution_scope(case.scope),
        llm.bind_project_prompt_audit(
            case.project_id,
            reason="offline negative fact-source custody",
        ),
    ):
        async with case.sessions() as session:
            completion = await llm.complete_text(session, case.settings, request)
            await session.commit()
    return completion


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "missing_task",
        "missing_report",
        "task_project",
        "report_chapter",
        "parent_hash",
        "unverified_report",
    ],
)
@pytest.mark.parametrize("sanitized", [False, True], ids=["physical_raw", "native_sanitized"])
async def test_fact_identity_or_proof_cannot_be_bypassed_by_raw_or_sanitized_bytes(
    fact_case,
    fault,
    sanitized,
):
    case, task_id, report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    async with case.sessions.begin() as session:
        task = await session.get(m.RewriteTaskModel, task_id)
        report = await session.get(m.ReviewReportModel, report_id)
        if fault == "missing_task":
            await session.delete(task)
        elif fault == "missing_report":
            await session.delete(report)
        elif fault == "task_project":
            task.project_id = uuid4()
        elif fault == "report_chapter":
            report.target_id = uuid4()
        elif fault == "parent_hash":
            task.metadata_json = {**task.metadata_json, "parent_draft_hash": "0" * 64}
        else:
            equal(
                "unverified_report.no_pass_constructed",
                None,
                derive_fact_repair_scope(SOURCE, report.structured_output),
            )
    completion = await fact_candidate(case, task_id, sanitized=sanitized)
    # Queue a would-pass scripted answer to detect an illegal extra paid judge;
    # source admission must reject before consuming it.
    case.answers.append(verify_answer(anchors["anchors"], CANDIDATE))
    before = await case.ledger(), case.files(), await case.protected()
    await rejected(lambda: case.verify(CANDIDATE, completion.llm_run_id))
    equal(
        "fact_source.no_paid_judge_or_adoption",
        before,
        (await case.ledger(), case.files(), await case.protected()),
    )
    equal("fact_source.no_extra_wire", 2, len(case.wires))
    equal("fact_source.critic_answer_not_consumed", 1, len(case.answers))


@pytest.mark.asyncio
async def test_task_or_run_scope_json_is_not_a_fact_permission(fact_case):
    case, task_id, _report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    completion = await fact_candidate(case, task_id, sanitized=False)
    # Deliberately forged evidence is a NEGATIVE control; it is never used to
    # make a positive fixture or manufacture a primary/window/commercial PASS.
    async with case.sessions.begin() as session:
        task = await session.get(m.RewriteTaskModel, task_id)
        run = await session.get(m.LlmRunModel, completion.llm_run_id)
        fabricated = {
            "schema": "verified-fact-repair-scope.v1",
            "parent_sha256": sha(SOURCE),
            "authorized_spans": [],
            "adoption_authorized": True,
        }
        task.metadata_json = {
            **task.metadata_json,
            "verified_fact_repair_scope": deepcopy(fabricated),
        }
        run.metadata_json = {
            **run.metadata_json,
            "verified_fact_repair_scope": deepcopy(fabricated),
        }
    case.answers.append(verify_answer(anchors["anchors"], CANDIDATE))
    before = await case.ledger()
    await rejected(lambda: case.verify(CANDIDATE, completion.llm_run_id))
    equal("metadata_is_not_permission.no_paid_judge", before, await case.ledger())
    equal("metadata_is_not_permission.no_extra_wire", 2, len(case.wires))

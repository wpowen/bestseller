"""Other-body quarantine custody through the real SQLite orphan guard; no provider."""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from test_commercial_component_cohort import SCOPE, _judge
from test_commercial_component_cohort import cohort as cohort
from test_commercial_quarantine_action_proofs import _all_rows
from test_commercial_quarantine_round2 import _round2_case
from test_commercial_slot_quarantine import _case, _journal

from bestseller.infra.db import models as m
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.budget import bind_execution_scope
from bestseller.services.commercial_slot_quarantine import load_verified_historical_quarantine_roots

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _history(rig, monkeypatch, *, round2=False):
    case = await _round2_case(rig, monkeypatch) if round2 else await _case(rig, monkeypatch)
    await _judge(rig, bound=True)
    return case


async def _target(case, *, other_chapter=False, other_book=False, same_body=False):
    rig = case.rig
    project = rig.project
    chapter = rig.chapter
    if other_book:
        project = m.ProjectModel(
            slug="another-quarantine-book",
            title="another book",
            genre="general-fiction",
            target_chapters=3,
            target_word_count=8000,
            metadata_json={},
        )
        rig.session.add(project)
        await rig.session.flush()
    if other_chapter or other_book:
        chapter = m.ChapterModel(
            project_id=project.id, chapter_number=3, chapter_goal="later chapter", metadata_json={}
        )
        rig.session.add(chapter)
        await rig.session.flush()
    body = rig.draft.content_md if same_body else rig.draft.content_md + "\n\n船长收起了旧账册。"
    if chapter.id == rig.chapter.id:
        rig.draft.is_current = False
    draft = m.ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=rig.draft.version_no + 1,
        content_md=body,
        is_current=True,
        promotion_state="candidate",
    )
    rig.session.add(draft)
    await rig.session.commit()
    return SimpleNamespace(
        project=project,
        chapter=chapter,
        draft=draft,
        body_hash=hashlib.sha256(body.encode()).hexdigest(),
    )


async def _proofs(case, target):
    workflows = list(await case.rig.session.scalars(select(m.WorkflowRunModel)))
    with bind_execution_scope(SCOPE):
        return await load_verified_historical_quarantine_roots(
            case.rig.session,
            project_id=target.project.id,
            chapter_id=target.chapter.id,
            body_sha256=target.body_hash,
            scope=SCOPE,
            workflows=workflows,
        )


async def _guard(case, target):
    with bind_execution_scope(SCOPE):
        return await recovery._assert_no_unbound_commercial_spend(
            case.rig.session,
            project_id=target.project.id,
            chapter_id=target.chapter.id,
            body_hash=target.body_hash,
            scope=SCOPE,
        )


@pytest.mark.parametrize("round2", [False, True])
@pytest.mark.parametrize("other_chapter", [False, True])
async def test_other_body_closed_quarantine_is_purpose_only_across_chapters(
    cohort,
    monkeypatch,
    round2,
    other_chapter,
):
    case = await _history(cohort, monkeypatch, round2=round2)
    target = await _target(case, other_chapter=other_chapter)
    before = await _all_rows(cohort)
    proofs = await _proofs(case, target)
    assert set(proofs) == {case.root}
    proof = proofs[case.root]
    assert proof["schema"] == "verified-historical-quarantine-purpose.v1"
    assert proof["chapter_id"] == str(cohort.chapter.id)
    assert proof["draft_id"] == str(cohort.draft.id)
    assert proof["round"] == (2 if round2 else 1)
    assert proof["body_sha256"] != target.body_hash
    assert not proof["credit_released"] and not proof["operation_replay_allowed"]
    assert not proof["grade_reused"]
    assert not {"request", "sources", "effective_critic", "report"} & proof.keys()
    for _ in range(2):
        assert await _guard(case, target) is None
        assert await _proofs(case, target) == proofs
        assert await _all_rows(cohort) == before
    assert case.forbidden == []


@pytest.mark.parametrize("other_chapter", [False, True])
async def test_same_body_never_excluded_even_under_another_chapter(
    cohort, monkeypatch, other_chapter
):
    case = await _history(cohort, monkeypatch)
    target = await _target(case, same_body=True, other_chapter=other_chapter)
    before = await _all_rows(cohort)
    assert await _proofs(case, target) == {}
    expected = (
        "paid_body_workflow_scope_conflict" if other_chapter else "orphan_pointer_requires_recovery"
    )
    with pytest.raises(ValueError, match=expected):
        await _guard(case, target)
    assert await _all_rows(cohort) == before and case.forbidden == []


async def test_another_books_journal_never_grants_purpose_for_this_scope(cohort, monkeypatch):
    case = await _history(cohort, monkeypatch)
    target = await _target(case, other_book=True)
    before = await _all_rows(cohort)
    assert await _proofs(case, target) == {}
    with pytest.raises(ValueError, match="opaque_operation_requires_recovery"):
        await _guard(case, target)
    assert await _all_rows(cohort) == before and case.forbidden == []


@pytest.mark.parametrize("round2", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "pointer_missing",
        "pointer_owner",
        "pointer_hash",
        "journal_schema",
        "body",
        "request",
        "attempt",
        "new_attempt",
        "root_return",
        "late_return",
        "late_run",
        "receipt_missing",
        "receipt_changed",
        "failure_changed",
        "owner_missing",
        "paid_run_missing",
        "unrelated_bad_control",
        "new_opaque_root",
    ],
)
async def test_history_tamper_or_new_unproven_history_never_bypasses_guard(
    cohort,
    monkeypatch,
    round2,
    fault,
):
    case = await _history(cohort, monkeypatch, round2=round2)
    target = await _target(case, other_chapter=fault != "paid_run_missing")
    owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
    metadata = deepcopy(owner.metadata_json)
    journal = metadata["commercial_component_cohorts"][case.key]
    entry = journal["slots"][case.slot]
    if fault.startswith("pointer_"):
        cm = deepcopy(cohort.chapter.metadata_json)
        pointer = cm["commercial_component_cohorts"][case.key]
        if fault == "pointer_missing":
            del cm["commercial_component_cohorts"][case.key]
        elif fault == "pointer_owner":
            pointer["workflow_run_id"] = str(uuid4())
        else:
            pointer["binding_sha256"] = "0" * 64
        cohort.chapter.metadata_json = cm
    elif fault in {"journal_schema", "request"}:
        if fault == "journal_schema":
            journal["schema"] = "unproven"
        else:
            entry["quarantine_history"][-1]["prepared_entry"]["request"]["user_prompt"] += "changed"
        owner.metadata_json = metadata
        flag_modified(owner, "metadata_json")
    elif fault == "body":
        cohort.draft.content_md += "changed retained body"
    elif fault == "attempt":
        attempt = await cohort.session.get(m.ExecutionAttemptModel, case.attempt)
        attempt.actual_tokens = 1
    elif fault == "new_attempt":
        cohort.session.add(
            m.ExecutionAttemptModel(
                id=uuid4().hex,
                operation_key=case.root,
                scope_id=SCOPE,
                input_hash=case.input_hash,
                model_name=cohort.settings.llm.critic.model,
                status="reserved",
                reserved_tokens=1000,
                created_at=datetime.now(UTC),
            )
        )
    elif fault == "root_return":
        root = await cohort.session.get(m.ExecutionOperationModel, case.root)
        root.result_json = {"content": "late result"}
    elif fault in {"late_return", "unrelated_bad_control", "new_opaque_root"}:
        status = {
            "late_return": "transport_result",
            "unrelated_bad_control": "reconciliation_receipt",
            "new_opaque_root": "stopped",
        }[fault]
        payload = {
            "operation_key": case.root,
            "attempt_id": case.attempt,
            "result": {"content": "late"},
        }
        if fault == "unrelated_bad_control":
            payload = {"receipt": {"attempt_id": "missing-actual"}}
        if fault == "new_opaque_root":
            payload = None
        cohort.session.add(
            m.ExecutionOperationModel(
                key=uuid4().hex * 2,
                scope_id=SCOPE,
                input_hash="1" * 64,
                status=status,
                owner="unproven",
                lease_until=datetime.now(UTC),
                result_json=payload,
            )
        )
    elif fault == "late_run":
        cohort.session.add(
            m.LlmRunModel(
                id=uuid4(),
                project_id=cohort.project.id,
                workflow_run_id=case.owner_id,
                logical_role="critic",
                provider="minimax",
                model_name=cohort.settings.llm.critic.model,
                prompt_template=case.request.prompt_template,
                prompt_version=case.request.prompt_version,
                prompt_hash="1" * 64,
                metadata_json={"execution_receipt": {"operation_key": case.root}},
            )
        )
    elif fault in {"receipt_missing", "receipt_changed"}:
        row = await cohort.session.get(m.ExecutionOperationModel, case.reconciliation_key)
        if fault == "receipt_missing":
            await cohort.session.delete(row)
        else:
            row.result_json = {**row.result_json, "remote_operation_closed": True}
    elif fault == "failure_changed":
        failure = (await _journal(case))["slots"][case.slot]["quarantine_history"][-1]["proof"][
            "failures"
        ][0]
        row = await cohort.session.get(m.ExecutionOperationModel, failure["key"])
        row.result_json = {**row.result_json, "physical_sends": 0}
    elif fault == "owner_missing":
        await cohort.session.delete(owner)
    elif fault == "paid_run_missing":
        paid = next(e for e in journal["slots"].values() if e["state"] == "completed")
        run = await cohort.session.get(m.LlmRunModel, UUID(paid["report"]["llm_run_id"]))
        await cohort.session.delete(run)
    await cohort.session.commit()
    before = await _all_rows(cohort)
    with pytest.raises((ValueError, KeyError)):
        await _guard(case, target)
    assert await _all_rows(cohort) == before and case.forbidden == []

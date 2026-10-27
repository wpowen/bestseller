"""Narrow known-hard replacement binding; authority/model boundaries inert."""

from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc, book_source_review as source
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.reviews import rewrite_source_binding

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def case(monkeypatch):
    p = NS(id=uuid4())
    c = NS(id=uuid4(), chapter_number=20)
    bid = uuid4()
    oldid = uuid4()
    refid = uuid4()
    newid = uuid4()
    tid = uuid4()
    old = NS(id=oldid, chapter_id=c.id, project_id=p.id, content_md="先前确定的原文")
    new = NS(id=newid, chapter_id=c.id, project_id=p.id, content_md="已修订的新原文")
    prior = str(refid)
    proof = {
        "schema": source.SCHEMA,
        "book_run_id": str(bid),
        "draft_id": str(oldid),
        "body_sha256": dc.body_hash(old.content_md),
    }
    selected = {
        "chapter_id": str(c.id),
        "draft_id": str(oldid),
        "body_sha256": proof["body_sha256"],
        "reference": {"receipt_id": prior},
    }
    before = {k: selected[k] for k in ("chapter_id", "draft_id", "body_sha256")}
    after = {
        "chapter_id": str(c.id),
        "draft_id": str(newid),
        "body_sha256": dc.body_hash(new.content_md),
    }
    history = {"old": before, "new": after, "task_id": str(tid), "evidence_sha256": "e" * 64}
    ref = NS(
        project_id=p.id,
        scope_id=c.id,
        workflow_type="draft_continuity_reference",
        status="completed",
        metadata_json={
            "proof": proof,
            "binding": {"proof_sha256": content_hash(proof)},
            "approval_id": str(uuid4()),
        },
    )
    task = NS(
        status="completed",
        project_id=p.id,
        trigger_source_id=c.id,
        trigger_type="book_source_hard_block",
        metadata_json={
            **rewrite_source_binding(old),
            "book_run_id": str(bid),
            "hard_evidence_sha256": "e" * 64,
            "book_hard_repair_source": before,
            "book_hard_repair_result": after,
        },
    )
    book = NS(id=bid, metadata_json={"selected": {"20": selected}, "repair_history": [history]})
    rows = {
        (m.WorkflowRunModel, prior): ref,
        (m.RewriteTaskModel, str(tid)): task,
        (m.ChapterDraftVersionModel, str(oldid)): old,
    }

    async def get(model, identity, **kw):
        return rows.get((model, str(identity)))

    from bestseller.services import operator_action_approval as auth

    async def approved(*a, **kw):
        pass

    monkeypatch.setattr(auth, "require_operator_action_approval", approved)
    return NS(
        project=p,
        chapter=c,
        draft=new,
        book=book,
        prior=prior,
        proof={"body_sha256": after["body_sha256"]},
        session=NS(get=get),
        ref=ref,
        task=task,
    )


async def check(c):
    return await source._verified_replacement_parent(
        c.session,
        project=c.project,
        chapter=c.chapter,
        draft=c.draft,
        book=c.book,
        prior=c.prior,
        proof=c.proof,
    )


async def test_exact_selected_reference_and_real_repair_parent_chain(case):
    before = deepcopy(case.ref.metadata_json)
    old, repair = await check(case)
    assert old["draft_id"] == repair["old"]["draft_id"]
    assert case.ref.metadata_json == before


@pytest.mark.parametrize(
    "mutation", ["pointer", "parent", "newbody", "scope", "evidence", "taskstatus", "oldreceipt"]
)
async def test_same_book_does_not_authorize_unrelated_reference_or_parent(case, mutation):
    if mutation == "pointer":
        case.book.metadata_json["selected"]["20"]["reference"]["receipt_id"] = str(uuid4())
    if mutation == "parent":
        case.task.metadata_json["parent_draft_id"] = str(uuid4())
    if mutation == "newbody":
        case.book.metadata_json["repair_history"][0]["new"] = {
            **case.book.metadata_json["repair_history"][0]["new"],
            "body_sha256": "0" * 64,
        }
    if mutation == "scope":
        case.ref.scope_id = uuid4()
    if mutation == "evidence":
        case.task.metadata_json["hard_evidence_sha256"] = "0" * 64
    if mutation == "taskstatus":
        case.task.status = "failed"
    if mutation == "oldreceipt":
        case.ref.metadata_json["binding"]["proof_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="book_reference_"):
        await check(case)

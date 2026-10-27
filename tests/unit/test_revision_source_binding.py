"""Revision authorization is tied to an executable task and its exact parent."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import quality_report_claim as claims
from bestseller.services import reviews
from bestseller.services.rewrite_patch import (
    ProtectedSpan,
    RewriteEdit,
    RewritePatch,
    apply_rewrite_patch,
    text_hash,
)

pytestmark = pytest.mark.unit


class Session:
    def __init__(self, task=None, report=None):
        self.task, self.report = task, report
        self.flushes = 0

    async def scalar(self, stmt):
        return self.task

    async def get(self, model, key):
        return self.report

    async def flush(self):
        self.flushes += 1


def parent_task(status="pending"):
    draft = SimpleNamespace(
        id=uuid4(), content_md="The copper latch held.\n\nShe waited.", is_current=True
    )
    task = SimpleNamespace(
        id=uuid4(),
        status=status,
        attempts=0,
        error_log=None,
        metadata_json={
            "draft_id": str(draft.id),
            "parent_draft_id": str(draft.id),
            "parent_draft_hash": text_hash(draft.content_md),
        },
    )
    return draft, task


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["chapter", "scene"])
@pytest.mark.parametrize("status", ["completed", "cancelled", "failed", "superseded", "stale"])
async def test_explicit_terminal_task_is_rejected_before_any_rewrite(monkeypatch, scope, status):
    draft, task = parent_task(status)
    project, chapter, scene = (SimpleNamespace(id=uuid4()) for _ in range(3))

    async def chapter_context(*args):
        return project, chapter, None, [], draft

    async def scene_context(*args):
        return project, chapter, scene, None, draft

    monkeypatch.setattr(reviews, "_load_chapter_context", chapter_context)
    monkeypatch.setattr(reviews, "_load_scene_context", scene_context)
    with pytest.raises(ValueError, match="not executable"):
        if scope == "chapter":
            await reviews.rewrite_chapter_from_task(
                Session(task), "test", 1, rewrite_task_id=task.id
            )
        else:
            await reviews.rewrite_scene_from_task(
                Session(task), "test", 1, 1, rewrite_task_id=task.id
            )
    assert task.status == status  # Historical outcome is not overwritten.
    assert task.attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "queued"])
async def test_current_parent_is_legal_and_receipt_is_not_rebound(status):
    draft, task = parent_task(status)
    before = dict(task.metadata_json)
    await reviews._validate_rewrite_task_parent(Session(task), task, draft)
    assert task.metadata_json == before
    assert task.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["id", "hash", "unbound"])
async def test_wrong_or_unverifiable_parent_becomes_stale_without_spending_attempt(change):
    draft, task = parent_task()
    if change == "id":
        draft.id = uuid4()
    elif change == "hash":
        draft.content_md += " Changed evidence."
    else:
        task.metadata_json = {"instructions_origin": "historical"}
    original = dict(task.metadata_json)
    with pytest.raises(ValueError, match="source binding"):
        await reviews._validate_rewrite_task_parent(Session(task), task, draft)
    assert task.status == "stale"
    assert task.attempts == 0
    assert all(task.metadata_json[k] == v for k, v in original.items())
    assert task.metadata_json["source_binding_rejection"]["current_draft_id"] == str(draft.id)


@pytest.mark.parametrize("text", ["旧稿。", "The original 42."])
def test_unclaimed_report_is_display_only(text):
    old = SimpleNamespace(report_json={"graded": claims.graded_text_fingerprint(text)})
    row, reason = claims.claim_report_for_draft([old], text + " altered")
    assert row is None
    assert reason == "no_claim"
    assert claims.claim_report_for_draft([old], text + " altered", allow_historical=True) == (
        old,
        "no_claim",
    )


def test_unfingerprinted_or_missing_draft_never_authorizes():
    old = SimpleNamespace(report_json={"blocking_codes": ["OLD"]})
    assert claims.claim_report_for_draft([old], "New body") == (None, "unfingerprinted")
    assert claims.claim_report_for_draft([old], None) == (None, "no_draft")


def test_matching_report_authorizes_its_own_text():
    text = "A legitimate current body."
    row = SimpleNamespace(report_json={"graded": claims.graded_text_fingerprint(text)})
    assert claims.claim_report_for_draft([row], text) == (row, "claimed")


def test_policy_exception_cannot_enable_reaction_obligation(monkeypatch):
    from bestseller.domain.review import SceneReviewResult, SceneReviewScores
    from bestseller.services import prompt_assembly

    calls = []

    def unavailable(*args):
        calls.append(args)
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(prompt_assembly, "genre_wants_reaction_amplification", unavailable)
    project = SimpleNamespace(
        title="Independent custom story",
        genre="custom",
        sub_genre=None,
        language="en",
        metadata_json={},
    )
    result = SceneReviewResult(
        verdict="pass",
        severity_max="info",
        scores=SceneReviewScores(
            **{
                name: 0.8
                for name, field in SceneReviewScores.model_fields.items()
                if field.is_required()
            }
        ),
        findings=[],
        evidence_summary={},
    )
    system, user = reviews.build_scene_review_prompts(
        project,
        SimpleNamespace(chapter_number=4),
        SimpleNamespace(scene_number=1, title="An exchange", purpose={}),
        SimpleNamespace(content_md="The meeting ended quietly."),
        result,
    )
    assert calls == []  # No genre policy lookup or fallback recipe is needed.
    assert "Genre labels, AI methodology, default templates, numerical craft rules and prior scores are advisory" in system
    assert "only explicitly approved, applicable requirements are binding" in system
    assert "crowd reaction or tail hook" in system
    assert "Quiet, solo, direct-dialogue and transition scenes can be valid" in system
    assert "SOURCE AUTHORITY" in system and "Canon/Timeline" in system
    assert "CURRENT_DRAFT:\nThe meeting ended quietly.\nEND_CURRENT_DRAFT" in user
    for recipe in ("Reaction amplification (OFF", "after peak beats, other characters' reactions amplify impact",
                   "first 200", "≥2", "Prompt Pack："):
        assert recipe not in system + user


@pytest.mark.asyncio
async def test_gate_receipt_reads_only_this_evaluation_and_exact_body(monkeypatch):
    calls = []
    old = SimpleNamespace(id=uuid4(), report_json={"violations": [{"code": "UNRELATED"}]})

    async def gate(**kwargs):
        calls.append(kwargs)
        return "blocked"

    class GateSession(Session):
        async def scalar(self, stmt):
            # Even a broken adapter returning an unrelated row must not authorize it.
            self.query = str(stmt)
            return old

    session = GateSession()
    monkeypatch.setattr(reviews, "_evaluate_chapter_quality_gate", gate)
    receipt = await reviews._evaluate_rewrite_quality_gate(
        session=session,
        project=SimpleNamespace(id=uuid4()),
        chapter_number=1,
        content="Current body",
    )
    assert calls[0]["extra_report_payload"]["rewrite_evaluation_id"]
    assert "rewrite_evaluation_id" in str(session.query) or "report_json" in str(session.query)
    assert receipt.outcome == "blocked"
    assert receipt.report is None
    assert receipt.violations == ()


def test_patch_rejects_nonfinite_scope_instead_of_bypassing_limit():
    parent = "before TARGET after" * 4
    start = parent.index("TARGET")
    edit = RewriteEdit(start, start + 6, text_hash("TARGET"), "before ", " after", "X" * 1000)
    result = apply_rewrite_patch(
        parent, RewritePatch(text_hash(parent), (edit,), max_changed_ratio=float("nan"))
    )
    assert result.accepted is False
    assert result.candidate_text == parent


def test_invalid_protected_bounds_are_not_silently_ignored():
    parent = "A long prefix TARGET suffix that must remain intact."
    start = parent.index("TARGET")
    edit = RewriteEdit(start, start + 6, text_hash("TARGET"), "prefix ", " suffix", "REPAIR")
    result = apply_rewrite_patch(
        parent,
        RewritePatch(
            text_hash(parent), (edit,), (ProtectedSpan(-4, -1, text_hash(parent[-4:-1])),)
        ),
    )
    assert result.accepted is False
    assert result.failure_codes == ("invalid_protected_bounds",)


def test_local_protocol_preserves_all_bytes_outside_segment_and_escalates_scope():
    parent = (
        "Heading\n\n" + "Unchanged preceding prose. " * 20 + "TARGET" + "\n\nRemaining prose.  "
    )
    draft, task = parent_task()
    draft.content_md = parent
    task.metadata_json["parent_draft_hash"] = text_hash(parent)
    candidate = reviews._apply_local_rewrite_segment(draft, task, "TARGET", "REPAIRED")
    assert candidate == parent.replace("TARGET", "REPAIRED")
    assert task.metadata_json["local_patch_receipt"]["accepted"] is True
    assert reviews._apply_local_rewrite_segment(draft, task, "TARGET", "X" * len(parent)) is None
    assert task.metadata_json["local_patch_escalation_required"] is True
    assert task.metadata_json["local_patch_receipt"]["failure_codes"] == ["changed_ratio_exceeded"]

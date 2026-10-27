"""Real correction reader through SELECT-only adapter; pending state stays guarded."""
import ast
import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import update
from test_adopted_source_correction_sqlite import db as db, issue, inventory
from test_retained_review_input_proof import retained as retained, verify
from test_commercial_component_cohort import cohort as cohort

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, WorkflowRunModel
from bestseller.services import adopted_source_correction as correction
from bestseller.services import chapter_review_reconciliation as reconciliation
from bestseller.services.retained_review_input_proof import _ReadOnlySession

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_actual_source_correction_context_works_without_writes(db):
    _, _, result = await issue(db)
    async with db.sessions() as session:
        before = await inventory(session)
        readonly = _ReadOnlySession(session)
        assert readonly.dirty == readonly.new == readonly.deleted == ()
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        context = await correction.read_adopted_source_correction_context(
            readonly, project_id=db.book[0], chapter_id=db.book[1],
            content_md=candidate.content_md,
        )
        assert context["candidate_id"] == result["candidate_id"]
        assert context["grade_asserted"] is False and context["adoption_authorized"] is False
        assert await inventory(session) == before


@pytest.mark.parametrize("pending", ["dirty", "new"])
async def test_relevant_pending_state_is_visible_and_reader_refuses_without_flush(db, pending):
    plan, _, _ = await issue(db)
    async with db.sessions() as session:
        chapter = await session.get(ChapterModel, db.book[1])
        if pending == "dirty":
            chapter.chapter_goal = "Unsaved source change"
            expected = chapter
        else:
            expected = WorkflowRunModel(project_id=db.book[0], workflow_type="test-pending",
                scope_type="chapter", scope_id=db.book[1], status="running")
            session.add(expected)
        readonly = _ReadOnlySession(session)
        assert expected in getattr(readonly, pending)
        assert isinstance(getattr(readonly, pending), tuple)
        with pytest.raises(ValueError, match="unflushed_context"):
            await correction.read_adopted_source_correction_context(
                readonly, project_id=db.book[0], chapter_id=db.book[1],
                content_md=plan["source_before"]["content_md"])
        assert expected in getattr(session, pending)
        await session.rollback()


async def test_inspection_exposes_deleted_snapshot_and_never_mutators(db):
    async with db.sessions() as session:
        chapter = await session.get(ChapterModel, db.other[1])
        await session.delete(chapter)
        readonly = _ReadOnlySession(session)
        assert readonly.deleted == (chapter,)
        for name in ("flush", "commit", "rollback", "add", "delete"):
            with pytest.raises(ValueError, match="readonly_operation_required"):
                getattr(readonly, name)
        with pytest.raises(ValueError, match="readonly_query_required"):
            await readonly.execute(update(ChapterModel).values(chapter_goal="forbidden"))
        assert chapter in session.deleted
        await session.rollback()


async def test_actual_retained_input_entry_refuses_pending_source_deletion(retained):
    await retained.rig.session.delete(retained.rig.chapter)
    with pytest.raises(ValueError, match="unflushed_source_deletion"):
        await verify(retained)
    assert retained.rig.chapter in retained.rig.session.deleted
    await retained.rig.session.rollback()


async def test_only_exact_three_tuple_properties_are_policy_compatible():
    path = Path(__file__).resolve().parents[2] / "src/bestseller/services/retained_review_input_proof.py"
    current = path.read_bytes()
    tree = ast.parse(current)
    adapter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_ReadOnlySession")
    adapter.body = [node for node in adapter.body if not isinstance(node, ast.FunctionDef)
                    or node.name not in {"dirty", "new", "deleted"}]
    old = ast.unparse(tree).encode()
    result = reconciliation._readonly_session_inspection_compatibility(old, current)
    assert result["query_and_mutation_guards_unchanged"] is True
    for bad in (current.replace(b"return tuple(self._session.dirty)", b"return self._session.dirty"),
                current.replace(b'getattr(statement, "is_select", False)', b"True")):
        with pytest.raises(ValueError, match="review_compatibility_readonly"):
            reconciliation._readonly_session_inspection_compatibility(old, bad)


async def test_actual_old_source_accepts_only_pinned_inspection_and_protocol_adapters():
    root = Path(__file__).resolve().parents[2]
    old = (root / "tests/fixtures/retained_review_proof_before_current_source.txt").read_bytes()
    # This pinned historical migration never grants compatibility to later
    # readonly view behavior. Its accepted source is the actual 8Y15 release.
    current = (root / "tests/fixtures/retained_review_proof_before_readonly_views.txt").read_bytes()
    assert hashlib.sha256(current).hexdigest() == "81e8164a002ea67c3b91af6dccddf407d356dd2c3eb94afe50edc64c128a6c6a"
    receipt = reconciliation._readonly_session_inspection_compatibility(old, current)
    assert set(receipt["exact_source_protocol_adapters"]) == {
        "_retained_source", "_known_selection_source", "_audit_source_compatibility",
        "_commercial_proof", "verify_retained_review_inputs", "_projection_audit_compatibility"}
    assert receipt["review_evaluator_changed"] is False
    for bad in (current.replace(b'"source_text_sha256"', b'"unchecked_source_text_sha256"'),
                current.replace(b"not session.deleted", b"True"),
                current.replace(b'"is_select", False', b'"is_select", True')):
        with pytest.raises(ValueError, match="review_compatibility"):
            reconciliation._readonly_session_inspection_compatibility(old, bad)


async def test_later_readonly_view_behavior_requires_its_own_explicit_policy_recovery():
    from bestseller.services.chapter_review_policy_history_8y24 import issued_1a5a_bytes

    root = Path(__file__).resolve().parents[2]
    old = (root / "tests/fixtures/retained_review_proof_before_current_source.txt").read_bytes()
    issued = issued_1a5a_bytes("src/bestseller/services/retained_review_input_proof.py")
    with pytest.raises(ValueError, match="review_compatibility_readonly_other_code_changed"):
        reconciliation._readonly_session_inspection_compatibility(old, issued)
    current = (root / "src/bestseller/services/retained_review_input_proof.py").read_bytes()
    # The later commercial-history reconstruction is a separate pinned native
    # epoch. The old inspection-only transition must reject that change too.
    with pytest.raises(ValueError, match="review_compatibility_source_adapter_transition_unverified"):
        reconciliation._readonly_session_inspection_compatibility(old, current)

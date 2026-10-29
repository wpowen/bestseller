"""Main-run RED for the native pipeline's AI-flavor/deslop consumption seam.

Persistence/selection cases use actual ORM Project/Chapter/Draft rows and the
existing native gateway/ledger/physical-response fixture. Scripted semantics
remain component evidence, never a real reader-quality or adoption proof.
Gate probes compile the unchanged current production block and stop at the
evaluator call boundary; their forced BLOCK is only control-flow input. They
cannot complete a style phase or grant a grade. No production source is edited
for this RED checkpoint, and this author does not execute these tests.
"""

import ast
from copy import deepcopy
import inspect
from pathlib import Path
import textwrap
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, update

from bestseller.infra.db import models as m
from bestseller.services import ai_flavor_gate, deslop_revise, pipelines, reader_judge
from bestseller.services.source_bound_planning_content import new_reader_first_binding

from .support import SOURCE, anchor_answer, equal, rejected, require, sha, verify_answer
from .test_reader_revision_preservation import CANDIDATE

KEY = "reader_revision_preservation"
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def pipeline_case(case):
    # Existing ordinary current-body fixture, not a promoted/graded candidate.
    # The native unique-current invariant is also enforced by SQLite.
    async with case.engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX reader_pipeline_current ON chapter_draft_versions "
            "(chapter_id) WHERE is_current = 1"
        )
    async with case.sessions.begin() as session:
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        source.is_current = True
        source.promotion_metadata = {"origin": "chapter_first_writer"}
        chapter.current_word_count = source.word_count
        chapter.status = "revision"
        chapter.metadata_json = {
            **chapter.metadata_json,
            "debt_ledger": {"unresolved": ["existing-source-debt"]},
            "reader_review_required": True,
            "blocked_by_ai_flavor_gate": True,
            "reader_judge": {"decision": "fail", "source": "original-body"},
            "current_body_review_status": "failed",
            "current_body_draft_id": str(source.id),
        }
    return case


async def protected(case):
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        rows = list(
            await session.scalars(
                select(m.ChapterDraftVersionModel).where(
                    m.ChapterDraftVersionModel.chapter_id == case.chapter_id
                )
            )
        )
        return {
            "current": sorted((str(row.id), sha(row.content_md)) for row in rows if row.is_current),
            "source": (
                source.content_md,
                source.promotion_state,
                source.promotion_score,
                deepcopy(source.promotion_metadata),
            ),
            "chapter": (chapter.status, chapter.production_state, chapter.current_word_count),
            "debts_and_required": {
                key: deepcopy(chapter.metadata_json.get(key))
                for key in (
                    "debt_ledger",
                    "reader_review_required",
                    "blocked_by_ai_flavor_gate",
                    "requires_machine_repair",
                    "reader_judge",
                    "current_body_review_status",
                    "current_body_draft_id",
                )
            },
            "grades": sorted(
                (str(row.id), row.promotion_state, row.promotion_score) for row in rows
            ),
        }


async def native_candidate(case, *, lost=(), voice=True, no_intrusion=True):
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    # The existing fixture dispatches an actual source-selected body request,
    # retaining native IDs/claims/attempts/physical payloads. No fake LlmRun.
    completion = await case.candidate(CANDIDATE)
    case.answers.append(
        verify_answer(
            anchors["anchors"], CANDIDATE, lost=lost, voice=voice, no_intrusion=no_intrusion
        )
    )
    receipt = await case.verify(CANDIDATE, completion.llm_run_id)
    return completion, receipt


async def persist(case, completion, receipt=None, *, include_receipt=True, make_current=False):
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        parent = await session.get(m.ChapterDraftVersionModel, case.source_id)
        kwargs = {KEY: receipt} if include_receipt else {}
        candidate = await pipelines._persist_deslop_candidate(
            session,
            chapter=chapter,
            parent_draft=parent,
            content=CANDIDATE,
            llm_run_id=completion.llm_run_id,
            round_no=1,
            settings=case.settings,
            make_current=make_current,
            **kwargs,
        )
        identity = candidate.id
        await session.commit()
        return identity


async def reject_selection(case, candidate_id):
    before, ledger, wires = await protected(case), await case.ledger(), len(case.wires)
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        candidate = await session.get(m.ChapterDraftVersionModel, candidate_id)
        await rejected(
            lambda: pipelines._select_deslop_candidate(
                session,
                chapter=chapter,
                candidate=candidate,
                selected_reason="offline existing quality selection",
                settings=case.settings,
            )
        )
        # Commit after a typed refusal to reveal accidental pointer/debt writes.
        await session.commit()
    equal("refusal.current_debt_and_grade", before, await protected(case))
    equal("refusal.no_paid_judge", ledger, await case.ledger())
    equal("refusal.no_wire", wires, len(case.wires))


async def test_native_observation_persists_exact_emotional_receipt_before_any_selection(
    pipeline_case,
):
    case = pipeline_case
    completion, receipt = await native_candidate(case)
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        parent = await session.get(m.ChapterDraftVersionModel, case.source_id)
        project = await session.get(m.ProjectModel, case.project_id)
        workflow = await session.get(m.WorkflowRunModel, case.workflow_id)
        tree = ast.parse(textwrap.dedent(inspect.getsource(pipelines.run_chapter_pipeline)))
        callbacks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_observe_deslop_candidate"
        ]
        equal("production.observer_count", 1, len(callbacks))
        namespace = {
            **vars(pipelines),
            "session": session,
            "settings": case.settings,
            "project": project,
            "chapter": chapter,
            "workflow_run": workflow,
            "_deslop_parent": parent,
            "_deslop_parent_by_content": {SOURCE: parent},
            "_deslop_candidates": [],
            "_deslop_length_request": None,
            "_af_lang": "zh-CN",
        }
        module = ast.Module(body=[deepcopy(callbacks[0])], type_ignores=[])
        exec(
            compile(ast.fix_missing_locations(module), "<current-deslop-observer>", "exec"),
            namespace,
        )
        await namespace["_observe_deslop_candidate"](
            {
                "content": CANDIDATE,
                "parent_content": SOURCE,
                "llm_run_id": completion.llm_run_id,
                "round": 1,
                KEY: receipt,
            }
        )
        candidate = namespace["_deslop_candidates"][0]
        equal("observer.exact_receipt", receipt, candidate.promotion_metadata.get(KEY))
        equal("observer.not_current", False, candidate.is_current)
        equal("observer.no_grade", None, candidate.promotion_score)
        await session.commit()


async def test_selection_revalidates_native_receipt_and_exact_body_before_moving_current(
    pipeline_case,
    monkeypatch,
):
    case = pipeline_case
    completion, receipt = await native_candidate(case)
    candidate_id = await persist(case, completion, receipt)
    before, ledger, files = await protected(case), await case.ledger(), case.files()
    calls = []
    native = reader_judge.verify_reader_revision_preservation

    async def observed(session, settings, **kwargs):
        calls.append(deepcopy(kwargs))
        equal(
            "revalidation.before_pointer_move",
            before["current"],
            (await protected(case))["current"],
        )
        return await native(session, settings, **kwargs)

    monkeypatch.setattr(reader_judge, "verify_reader_revision_preservation", observed)
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        candidate = await session.get(m.ChapterDraftVersionModel, candidate_id)
        chosen = await pipelines._select_deslop_candidate(
            session,
            chapter=chapter,
            candidate=candidate,
            selected_reason="native preserved candidate",
            settings=case.settings,
        )
        equal("selection.actual_candidate", candidate_id, chosen.id)
        equal("selection.receipt_retained", receipt, chosen.promotion_metadata.get(KEY))
        equal("selection.no_promotion", "candidate", chosen.promotion_state)
        equal("selection.no_grade", None, chosen.promotion_score)
        await session.commit()
    require("selection.actual_native_revalidation", bool(calls))
    equal("selection.exact_full_candidate", CANDIDATE, calls[-1]["candidate_text"])
    equal("selection.actual_paid_run", completion.llm_run_id, calls[-1]["candidate_llm_run_id"])
    after = await protected(case)
    equal("selection.current", [(str(candidate_id), sha(CANDIDATE))], after["current"])
    for key in (
        "debt_ledger",
        "reader_review_required",
        "blocked_by_ai_flavor_gate",
        "requires_machine_repair",
    ):
        equal(
            "selection.preserve." + key,
            before["debts_and_required"][key],
            after["debts_and_required"][key],
        )
    equal("selection.replay_no_paid", ledger, await case.ledger())
    equal("selection.physical_unchanged", files, case.files())


@pytest.mark.parametrize("failure", ["function", "voice", "intrusion"])
async def test_negative_native_semantics_are_retained_but_never_selected(pipeline_case, failure):
    case = pipeline_case
    completion, receipt = await native_candidate(
        case,
        lost=("a1",) if failure == "function" else (),
        voice=failure != "voice",
        no_intrusion=failure != "intrusion",
    )
    equal("native_negative.preserved", False, receipt["preserved"])
    candidate_id = await persist(case, completion, receipt)
    async with case.sessions() as session:
        row = await session.get(m.ChapterDraftVersionModel, candidate_id)
        equal("native_negative.evidence_retained", receipt, row.promotion_metadata.get(KEY))
    await reject_selection(case, candidate_id)


@pytest.mark.parametrize("fault", ["missing_receipt", "native_unknown", "project_missing"])
async def test_selection_requires_actual_project_and_available_emotional_evidence(
    pipeline_case, fault
):
    case = pipeline_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    completion = await case.candidate(CANDIDATE)
    if fault == "native_unknown":
        case.wire_failure = httpx.ReadTimeout
        await rejected(lambda: case.verify(CANDIDATE, completion.llm_run_id))
        case.wire_failure = None
        equal("fixture.actual_unknown", "unknown", (await case.ledger())["status"])
    candidate_id = await persist(case, completion, include_receipt=False)
    if fault == "project_missing":
        async with case.sessions.begin() as session:
            await session.delete(await session.get(m.ProjectModel, case.project_id))
    require("source.anchors_exist", bool(anchors["anchors"]))
    await reject_selection(case, candidate_id)


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "string_boolean",
        "candidate_hash",
        "candidate_run",
        "authority",
        "physical_missing",
        "forged_true_over_native_false",
    ],
)
async def test_persisted_receipt_is_evidence_not_a_caller_grant(pipeline_case, fault):
    case = pipeline_case
    completion, receipt = await native_candidate(
        case, lost=("a1",) if fault == "forged_true_over_native_false" else ()
    )
    candidate_id = await persist(case, completion, receipt)
    if fault == "physical_missing":
        _, path, _ = await case.physical(UUID(receipt["llm_run_id"]))
        path.unlink()
    else:
        changes = {
            "schema": {"schema": "wrong"},
            "string_boolean": {"preserved": "true"},
            "candidate_hash": {"candidate_body_sha256": "0" * 64},
            "candidate_run": {"candidate_llm_run_id": str(case.source_id)},
            "authority": {"adoption_authority": True},
            "forged_true_over_native_false": {"preserved": True},
        }
        async with case.sessions.begin() as session:
            candidate = await session.get(m.ChapterDraftVersionModel, candidate_id)
            candidate.promotion_metadata = {
                **candidate.promotion_metadata,
                KEY: {**receipt, **changes[fault]},
            }
    await reject_selection(case, candidate_id)


@pytest.mark.parametrize("fault", ["candidate_body", "parent_body", "current_pointer"])
async def test_selection_reloads_actual_rows_and_rejects_concurrent_source_changes(
    pipeline_case, fault
):
    case = pipeline_case
    completion, receipt = await native_candidate(case)
    candidate_id = await persist(case, completion, receipt)
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        candidate = await session.get(m.ChapterDraftVersionModel, candidate_id)
        # The held objects intentionally predate a separate real SQL writer.
        await session.commit()
        async with case.sessions.begin() as concurrent:
            if fault == "candidate_body":
                row = await concurrent.get(m.ChapterDraftVersionModel, candidate_id)
                row.content_md += "\n并发改写的候选末句。"
            elif fault == "parent_body":
                row = await concurrent.get(m.ChapterDraftVersionModel, case.source_id)
                row.content_md += "\n并发改写的源稿末句。"
            else:
                await concurrent.execute(
                    update(m.ChapterDraftVersionModel)
                    .where(m.ChapterDraftVersionModel.chapter_id == case.chapter_id)
                    .values(is_current=False)
                )
                concurrent.add(
                    m.ChapterDraftVersionModel(
                        project_id=case.project_id,
                        chapter_id=case.chapter_id,
                        version_no=10,
                        content_md="并发普通稿。",
                        word_count=6,
                        is_current=True,
                        promotion_state="candidate",
                        promotion_metadata={},
                    )
                )
        before, ledger = await protected(case), await case.ledger()
        await rejected(
            lambda: pipelines._select_deslop_candidate(
                session,
                chapter=chapter,
                candidate=candidate,
                selected_reason="stale caller objects",
                settings=case.settings,
            )
        )
        await session.commit()
    equal("concurrent.current_and_debt_retained", before, await protected(case))
    equal("concurrent.no_paid", ledger, await case.ledger())


async def test_actual_unmarked_legacy_keeps_local_patch_selection_without_migration(pipeline_case):
    case = pipeline_case
    async with case.sessions.begin() as session:
        project = await session.get(m.ProjectModel, case.project_id)
        project.metadata_json = {"legacy": "unchanged"}
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        # A chapter/caller marker cannot silently migrate the actual Project.
        chapter.metadata_json = {
            **chapter.metadata_json,
            "reader_first_planning": new_reader_first_binding("caller-only marker"),
        }
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        parent = await session.get(m.ChapterDraftVersionModel, case.source_id)
        candidate = await pipelines._persist_deslop_candidate(
            session,
            chapter=chapter,
            parent_draft=parent,
            content="旧协议局部清理稿。",
            llm_run_id=None,
            round_no=0,
            transformation="local_patch",
            make_current=True,
            settings=case.settings,
        )
        equal("legacy.local_patch_current", True, candidate.is_current)
        equal("legacy.no_grade", None, candidate.promotion_score)
        equal("legacy.no_minted_receipt", False, KEY in candidate.promotion_metadata)
        await session.commit()
    equal("legacy.no_model", 0, len(case.wires))
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, case.project_id)
        equal("legacy.actual_project_unchanged", {"legacy": "unchanged"}, project.metadata_json)


@pytest.mark.parametrize("transformation", ["local_patch", "deslop_postcheck_patch"])
async def test_new_protocol_cannot_make_unpaid_patch_current(pipeline_case, transformation):
    case = pipeline_case
    before = await protected(case)
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        parent = await session.get(m.ChapterDraftVersionModel, case.source_id)
        await rejected(
            lambda: pipelines._persist_deslop_candidate(
                session,
                chapter=chapter,
                parent_draft=parent,
                content="没有原生付费来源的局部替换。",
                llm_run_id=None,
                round_no=0,
                transformation=transformation,
                make_current=True,
                settings=case.settings,
            )
        )
        await session.commit()
    after = await protected(case)
    for key in ("current", "source", "chapter", "debts_and_required"):
        equal("unpaid_patch.preserve." + key, before[key], after[key])
    require(
        "unpaid_patch.no_grade",
        all(state == "candidate" and score is None for _identity, state, score in after["grades"]),
    )
    equal("unpaid_patch.no_judge_send", 0, len(case.wires))


async def test_current_pointer_is_rechecked_after_native_verifier_checkpoint(
    pipeline_case, monkeypatch
):
    case = pipeline_case
    completion, receipt = await native_candidate(case)
    candidate_id = await persist(case, completion, receipt)
    native = reader_judge.verify_reader_revision_preservation
    concurrent_state = []

    async def race_after_native_proof(session, settings, **kwargs):
        result = await native(session, settings, **kwargs)
        # The native verifier can checkpoint. A caller must re-lock its actual
        # current/source/candidate after that boundary, before pointer writes.
        await session.commit()
        async with case.sessions.begin() as concurrent:
            await concurrent.execute(
                update(m.ChapterDraftVersionModel)
                .where(m.ChapterDraftVersionModel.chapter_id == case.chapter_id)
                .values(is_current=False)
            )
            concurrent.add(
                m.ChapterDraftVersionModel(
                    project_id=case.project_id,
                    chapter_id=case.chapter_id,
                    version_no=10,
                    content_md="另一写手的普通当前稿。",
                    word_count=10,
                    is_current=True,
                    promotion_state="candidate",
                    promotion_metadata={},
                )
            )
        concurrent_state.append(await protected(case))
        return result

    monkeypatch.setattr(
        reader_judge, "verify_reader_revision_preservation", race_after_native_proof
    )
    before_ledger = await case.ledger()
    async with case.sessions() as session:
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        candidate = await session.get(m.ChapterDraftVersionModel, candidate_id)
        await rejected(
            lambda: pipelines._select_deslop_candidate(
                session,
                chapter=chapter,
                candidate=candidate,
                selected_reason="concurrent native checkpoint",
                settings=case.settings,
            )
        )
        await session.commit()
    equal("checkpoint.actual_native_revalidation", 1, len(concurrent_state))
    equal("checkpoint.concurrent_current_retained", concurrent_state[0], await protected(case))
    equal("checkpoint.no_paid_retry", before_ledger, await case.ledger())


class ReachedEvaluator(BaseException):
    """Boundary-only stop: no successful quality result or adoption is returned."""


def actual_flavor_block():
    source = Path(pipelines.__file__).read_text()
    start = source.index("        # ── AI-flavor gate")
    end = source.index("        # ── 文采 advisory", start)
    block = textwrap.indent(textwrap.dedent(source[start:end]), "    ")
    signature = (
        "async def probe(session, settings, project, chapter, chapter_draft, workflow_run, "
        "chapter_number, resume_existing_draft_only=False, step_order=1, "
        "scene_requires_human_review=False):\n"
    )
    namespace = {
        **vars(pipelines),
        "retained_review_matches": lambda _draft: False,
        "retained_review_reuse": None,
    }
    exec(compile(signature + block, "<current-pipeline-ai-flavor-block>", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("marked", [True, False], ids=["reader_first", "legacy"])
@pytest.mark.parametrize("phase", ["initial", "post_deslop"])
async def test_both_actual_gate_sites_read_native_project_marker_and_never_patch_new_protocol(
    pipeline_case,
    monkeypatch,
    marked,
    phase,
):
    case = pipeline_case
    if not marked:
        async with case.sessions.begin() as session:
            project = await session.get(m.ProjectModel, case.project_id)
            project.metadata_json = {"legacy": "unchanged"}
    namespace = actual_flavor_block()
    config = ai_flavor_gate.AiFlavorGateConfig(write_audit_file=False, deslop_revise_enabled=True)
    namespace["get_quality_gates_config"] = lambda: SimpleNamespace(ai_flavor=config)
    reached = []

    def evaluator(kind, **kwargs):
        reached.append((kind, sha(kwargs["content_md"])))
        if phase == "initial" or len(reached) == 2:
            raise ReachedEvaluator()
        # A forced BLOCK only reaches the postcheck call site; no PASS/patch,
        # fee, candidate, grade or completed phase is fabricated.
        return ai_flavor_gate.AiFlavorGateOutcome(
            enabled=True,
            language="zh-CN",
            chapter_number=1,
            before_score=100,
            after_score=100,
            patched_text=None,
            decision="block",
        )

    namespace["_evaluate_chapter_ai_flavor_without_edits"] = lambda **kw: evaluator(
        "read_only", **kw
    )
    monkeypatch.setattr(
        ai_flavor_gate, "run_ai_flavor_gate", lambda **kw: evaluator("mutating", **kw)
    )

    async def no_dispatch_revision(_session, _settings, **kwargs):
        return deslop_revise._phase_result(
            kwargs["content"],
            source=kwargs["content"],
            state="incomplete",
            reason="red_boundary_probe_no_dispatch",
            attempted=0,
            rounds=2,
            project_id=case.project_id,
            workflow_run_id=case.workflow_id,
        )

    monkeypatch.setattr(deslop_revise, "revise_prose_deslop", no_dispatch_revision)
    before = await protected(case)
    async with case.sessions() as session:
        actual = await session.get(m.ProjectModel, case.project_id)
        caller = SimpleNamespace(
            **{p.key: deepcopy(getattr(actual, p.key)) for p in actual.__mapper__.column_attrs}
        )
        caller.metadata_json = (
            {"legacy": "stale caller"}
            if marked
            else {"reader_first_planning": new_reader_first_binding("stale caller marker")}
        )
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        workflow = await session.get(m.WorkflowRunModel, case.workflow_id)
        try:
            await namespace["probe"](session, case.settings, caller, chapter, source, workflow, 1)
        except ReachedEvaluator:
            pass
        else:
            require("gate.expected_boundary_reached", False)
        await session.rollback()
    count = 1 if phase == "initial" else 2
    equal(
        "gate.route_from_actual_project",
        [("read_only" if marked else "mutating", sha(SOURCE))] * count,
        reached,
    )
    equal("gate.no_source_or_debt_writes", before, await protected(case))
    equal("gate.no_paid_calls", 0, len(case.wires))

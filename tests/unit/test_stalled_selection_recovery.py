"""Real isolated SQLite writes; retained commercial actions are offline fixtures.

Only fresh input authentication is stubbed: its source-bound return/actual
settings interface and refusal are tested here. This is no PostgreSQL, paid
integration, literary acceptance or actual migration evidence.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401
from test_commercial_action_projection import slot

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
)
from bestseller.services import llm
from bestseller.services import stalled_selection_recovery as recovery
from bestseller.services.chapter_draft_candidate_eligibility import AUDIT_KEY
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.services.write_gate import GateConfig
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _install_summary(body, chapter, *, blocked):
    sources = assemble_commercial_sources(
        chapter_number=chapter.chapter_number, content_md=body, previous_chapters=[], source={}
    )
    original = slot(sources, kind="current_fact_conflict", severity="high", issues=blocked)
    projected, receipt = project_commercial_actions(
        original,
        sources=sources,
        chapter_number=chapter.chapter_number,
        gate_config=GateConfig({}, "audit_only"),
    )
    assert projected is not None and receipt["status"] == "projected"
    return {
        "commercial_action_projection": receipt,
        "llm_commercial_judge": projected,
        "llm_commercial_judge_original": original.model_dump(mode="json", by_alias=True),
    }


@pytest.fixture
async def case(tmp_path, monkeypatch):
    path = tmp_path / "stalled_selection_recovery_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.url.get_backend_name() == "sqlite" and "test" in path.stem
    async with engine.begin() as conn:
        for model in (
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            ReviewReportModel,
            QualityScoreModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
            await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = ProjectModel(
            slug=f"selection-test-{uuid4().hex}",
            title="Unrelated observatory",
            genre="test",
            target_word_count=2000,
            target_chapters=5,
            metadata_json={"preserved": [1, 2]},
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=3,
            chapter_goal="Inspect the log",
            current_word_count=20,
            metadata_json={"protected": {"preserve": True}},
        )
        session.add(chapter)
        await session.flush()
        drafts, reports, qualities, candidates, cycles = [], [], [], [], {}
        for version, blocked, body in (
            (7, True, "The astronomer opened the sealed log.\n\nIts seal stayed intact."),
            (2, False, "The technician checked the log before opening it.\n\nThe marks matched."),
        ):
            summary = _install_summary(body, chapter, blocked=blocked)
            result = ChapterReviewResult(
                verdict="rewrite",
                severity_max="high",
                scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.63)),
                evidence_summary=summary,
            )
            draft = ChapterDraftVersionModel(
                project_id=project.id,
                chapter_id=chapter.id,
                version_no=version,
                content_md=body,
                word_count=20 if blocked else 35,
                is_current=blocked,
                promotion_state="candidate",
                promotion_metadata={"retained": True},
            )
            session.add(draft)
            await session.flush()
            fingerprint = chapter_review_body_fingerprint(body)
            report = ReviewReportModel(
                project_id=project.id,
                target_id=chapter.id,
                target_type="chapter",
                reviewer_type="fixture",
                verdict=result.verdict,
                severity_max=result.severity_max,
                llm_run_id=uuid4(),
                structured_output={
                    "draft_id": str(draft.id),
                    "reviewed_body_fingerprint": fingerprint,
                    "critic_evidence": {"status": "verified", "passed": True},
                    "scores": result.scores.model_dump(mode="json"),
                    "findings": [],
                    "evidence_summary": deepcopy(summary),
                    "raw_physical_return": "preserve byte for byte",
                },
            )
            session.add(report)
            await session.flush()
            quality = QualityScoreModel(
                project_id=project.id,
                target_id=chapter.id,
                target_type="chapter",
                chapter_draft_version_id=draft.id,
                review_report_id=report.id,
                score_overall=Decimal(".63"),
                is_current=not blocked,
                evidence_summary=deepcopy(summary),
            )
            session.add(quality)
            await session.flush()
            key = hashlib.sha256(
                f"chapter-critic-cycle.v1\n{project.id}\n{chapter.id}\n{fingerprint['digest']}\ninitial".encode()
            ).hexdigest()
            cycle = {
                "schema_version": "chapter-critic-cycle.v1",
                "cycle_id": key,
                "state": "completed",
                "source_draft_id": str(draft.id),
                "source_body_hash": fingerprint["digest"],
                "policy_source_hash": "a" * 64,
                "semantic_dependencies_sha256": "b" * 64,
                "review_workflow_run_id": str(uuid4()),
                "attempts": [
                    {
                        "attempt": 1,
                        "state": "completed",
                        "outcome": "available",
                        "draft_id": str(draft.id),
                        "body_fingerprint": fingerprint,
                        "review_report_id": str(report.id),
                        "quality_score_id": str(quality.id),
                        "rewrite_task_id": None,
                        "review_result": result.model_dump(mode="json"),
                    }
                ],
            }
            cycles[key] = cycle
            candidates.append(
                {
                    "draft_id": str(draft.id),
                    "eligible": True,
                    "reasons": [],
                    "cycle_id": key,
                    "body_fingerprint": fingerprint,
                    "review_report_id": str(report.id),
                    "quality_score_id": str(quality.id),
                }
            )
            drafts.append(draft)
            reports.append(report)
            qualities.append(quality)
        receipt = {
            "policy": "chapter-best-draft-source-eligibility.v1",
            "reason": "eligible_best_of_n",
            "selection_only": True,
            "adoption_authorized": False,
            "expected_current_draft_id": str(drafts[1].id),
            "chosen_draft_id": str(drafts[0].id),
            "policy_source_hash": "a" * 64,
            "semantic_dependencies_sha256": "b" * 64,
            "candidates": candidates,
        }
        noop = {**deepcopy(receipt), "expected_current_draft_id": receipt["chosen_draft_id"]}
        chapter.metadata_json = {
            **chapter.metadata_json,
            "chapter_critic_cycles": cycles,
            AUDIT_KEY: [deepcopy(receipt), noop],
        }
        await session.commit()
        settings = load_settings(env={})
        calls = []

        async def fresh_inputs(s, actual_settings, *, project, chapter, draft, report, cycle):
            assert s is session and actual_settings is settings
            calls.append(str(draft.id))
            return {
                "schema": "retained-review-input-proof.v1",
                "project_id": str(project.id),
                "chapter_id": str(chapter.id),
                "draft_id": str(draft.id),
                "review_report_id": str(report.id),
                "cycle_id": cycle["cycle_id"],
                "review_workflow_run_id": cycle["review_workflow_run_id"],
                "source_sha256": "c" * 64,
                "retained_source_sha256": "c" * 64,
                "catalog_sha256": "d" * 64,
                "paid_rows": [
                    {
                        "llm_run_id": str(report.llm_run_id),
                        "effective_input_sha256": "e" * 64,
                        "prompt_template": "chapter_review",
                    }
                ],
                "commercial": {
                    "route_proof": "retained-journal-effective-critic.v1",
                    "effective_critic_sha256": "f" * 64,
                    "llm_run_ids": [str(uuid4())],
                },
                "provider_calls": 0,
                "writes": 0,
            }

        async def no_provider(*args, **kwargs):
            pytest.fail("Selection recovery must never call a provider")

        monkeypatch.setattr(llm, "complete_text", no_provider)
        monkeypatch.setattr(recovery, "verify_retained_review_inputs", fresh_inputs)
        data = SimpleNamespace(
            session=session,
            project=project,
            chapter=chapter,
            drafts=drafts,
            reports=reports,
            qualities=qualities,
            settings=settings,
            receipt=deepcopy(receipt),
            factory=factory,
            calls=calls,
            fresh_inputs=fresh_inputs,
        )
        for row in (project, chapter, *drafts, *reports, *qualities):
            await session.refresh(row)
        data.witness = recovery.build_stalled_selection_witness(**_rows(data))
        yield data
    await engine.dispose()


def _rows(data):
    return {
        key: getattr(data, key) for key in ("project", "chapter", "drafts", "reports", "qualities")
    }


async def _apply(data, **overrides):
    args = {
        "project_id": data.project.id,
        "chapter_id": data.chapter.id,
        "expected_receipt": data.receipt,
        "expected_witness": data.witness,
        "actor": "offline-unit",
        "evidence_ref": "fixture:bound-selection",
    }
    args.update(overrides)
    return await recovery.recover_stalled_selection(data.session, data.settings, **args)


async def _recapture(data):
    await data.session.commit()
    for row in (data.project, data.chapter, *data.drafts, *data.reports, *data.qualities):
        await data.session.refresh(row)
    data.witness = recovery.build_stalled_selection_witness(**_rows(data))


async def test_restore_exact_receipt_expected_without_version_guess_and_preserve_rows(case):
    before = recovery._sources(**_rows(case))
    previous_count = case.chapter.current_word_count
    assert previous_count == case.drafts[0].word_count != case.drafts[1].word_count
    proof = recovery.prove_stalled_selection_recovery(
        **_rows(case), expected_receipt=case.receipt, expected_witness=case.witness
    )
    assert (
        proof["chosen"]["effective_fact_blockers"]
        and not proof["expected"]["effective_fact_blockers"]
    )
    assert case.drafts[1].version_no < case.drafts[0].version_no
    receipt = await _apply(case)
    assert receipt["status"] == "restored" and not receipt["eligibility_created"]
    assert receipt["selection_only"] and not receipt["adoption_authorized"]
    assert case.calls == [str(d.id) for d in case.drafts]
    assert case.drafts[1].is_current and not case.drafts[0].is_current
    assert case.chapter.current_word_count == case.drafts[1].word_count
    assert receipt["previous_current_word_count"] == previous_count
    assert receipt["restored_current_word_count"] == case.drafts[1].word_count
    after = recovery._sources(**_rows(case))
    before["chapter"]["current_word_count"] = case.drafts[1].word_count
    for row in before["drafts"]:
        row["is_current"] = row["id"] == case.drafts[1].id
    assert recovery._sha(before) == recovery._sha(after)
    assert all(d.promotion_state == "candidate" for d in case.drafts)
    await case.session.commit()
    async with case.factory() as other:
        current = (
            await other.scalars(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.is_current.is_(True)
                )
            )
        ).one()
        assert current.id == case.drafts[1].id
        chapter = await other.get(ChapterModel, case.chapter.id)
        assert (
            chapter.current_word_count
            == current.word_count
            == receipt["restored_current_word_count"]
        )
        assert chapter.metadata_json[recovery.RECOVERY_KEY] == [receipt]
        assert chapter.metadata_json[AUDIT_KEY] == case.chapter.metadata_json[AUDIT_KEY]
    returned = await _apply(case)
    assert returned == receipt and len(case.calls) == 2
    assert case.chapter.current_word_count == case.drafts[1].word_count
    assert len(case.chapter.metadata_json[recovery.RECOVERY_KEY]) == 1
    returned["chosen"]["draft_id"] = "caller-mutated-return"
    assert (await _apply(case)) == receipt


@pytest.mark.parametrize(
    "change",
    ["user_body", "newline_only", "report", "quality", "new_draft", "metadata", "draft_timestamp"],
)
async def test_exact_source_witness_rejects_later_edit(case, change):
    if change in {"user_body", "newline_only"}:
        case.drafts[1].content_md += " user edit" if change == "user_body" else "\r"
    elif change == "report":
        case.reports[0].structured_output = {**case.reports[0].structured_output, "new_raw": True}
    elif change == "quality":
        case.qualities[0].score_overall = 0.89
    elif change == "draft_timestamp":
        case.drafts[1].created_at += timedelta(seconds=1)
    elif change == "new_draft":
        case.session.add(
            ChapterDraftVersionModel(
                project_id=case.project.id,
                chapter_id=case.chapter.id,
                version_no=99,
                content_md="New user draft",
                is_current=False,
                promotion_state="candidate",
            )
        )
    else:
        case.chapter.metadata_json = {
            **case.chapter.metadata_json,
            "protected": {"user": "changed"},
        }
    await case.session.commit()
    with pytest.raises(ValueError, match="source_witness_changed"):
        await _apply(case)
    assert not case.calls and recovery.RECOVERY_KEY not in case.chapter.metadata_json


@pytest.mark.parametrize(
    "change", ["no_current", "two_current", "expected_already_current", "new_user_current"]
)
async def test_no_pointer_guess_even_with_newly_captured_witness(case, change):
    case.drafts[0].is_current = change == "two_current"
    case.drafts[1].is_current = change in {"two_current", "expected_already_current"}
    if change == "new_user_current":
        third = ChapterDraftVersionModel(
            project_id=case.project.id,
            chapter_id=case.chapter.id,
            version_no=99,
            content_md="New user current",
            is_current=True,
            promotion_state="candidate",
        )
        case.session.add(third)
        case.drafts.append(third)
    await _recapture(case)
    with pytest.raises(ValueError, match="working_pointer_changed"):
        await _apply(case)


@pytest.mark.parametrize(
    "change",
    [
        "missing_flag",
        "not_selection_only",
        "adoption",
        "duplicate_audit",
        "later_selection",
        "unknown_later",
        "missing_candidates",
        "duplicate_candidate",
        "wrong_expected",
    ],
)
async def test_malformed_ambiguous_or_later_legitimate_selection_rejected(case, change):
    receipt = deepcopy(case.receipt)
    history = deepcopy(case.chapter.metadata_json[AUDIT_KEY])
    if change == "missing_flag":
        receipt.pop("adoption_authorized")
    elif change == "not_selection_only":
        receipt["selection_only"] = False
    elif change == "adoption":
        receipt["adoption_authorized"] = True
    elif change == "missing_candidates":
        receipt["candidates"] = None
    elif change == "duplicate_candidate":
        receipt["candidates"].append(deepcopy(receipt["candidates"][0]))
    elif change == "wrong_expected":
        receipt["expected_current_draft_id"] = str(uuid4())
    if change == "duplicate_audit":
        history.append(deepcopy(receipt))
    elif change == "later_selection":
        history.append({**receipt, "chosen_draft_id": str(case.drafts[1].id)})
    elif change == "unknown_later":
        history.append(None)
    history[0] = receipt
    case.receipt = receipt
    case.chapter.metadata_json = {**case.chapter.metadata_json, AUDIT_KEY: history}
    await _recapture(case)
    with pytest.raises(ValueError, match="stalled_selection_recovery_"):
        await _apply(case)
    assert not case.calls


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "report_body",
        "score",
        "unknown",
        "cycle_policy",
        "cycle_semantic",
        "duplicate_cycle",
        "duplicate_attempt",
        "report_scope",
        "quality_scope",
        "draft_scope",
        "promotion",
        "debt_adoption",
        "missing_projection",
        "forged_blocker",
        "expected_fact_blocked",
        "no_fact_blocker",
    ],
)
async def test_bad_retained_binding_fails_even_when_caller_recaptures(case, change):
    cycles = deepcopy(case.chapter.metadata_json["chapter_critic_cycles"])
    candidate = case.receipt["candidates"][0]
    cycle = cycles[candidate["cycle_id"]]
    report = case.reports[0]
    payload = deepcopy(report.structured_output)
    if change == "body":
        case.drafts[0].content_md += " edit after selection"
    elif change == "report_body":
        payload["reviewed_body_fingerprint"] = chapter_review_body_fingerprint("unrelated")
    elif change == "score":
        case.qualities[0].score_overall = 0.88
    elif change == "unknown":
        payload["critic_evidence"]["status"] = "unavailable"
    elif change == "cycle_policy":
        cycle["policy_source_hash"] = "c" * 64
    elif change == "cycle_semantic":
        cycle["semantic_dependencies_sha256"] = "c" * 64
    elif change == "duplicate_cycle":
        cycles["c" * 64] = deepcopy(cycle)
    elif change == "duplicate_attempt":
        cycle["attempts"].append({**deepcopy(cycle["attempts"][0]), "attempt": 2})
    elif change == "report_scope":
        report.project_id = uuid4()
    elif change == "quality_scope":
        case.qualities[0].project_id = uuid4()
    elif change == "draft_scope":
        case.drafts[0].project_id = uuid4()
    elif change == "promotion":
        case.drafts[1].promotion_state = "promoted"
    elif change == "debt_adoption":
        case.chapter.metadata_json = {**case.chapter.metadata_json, "numeric_promotion_debt": {}}
    else:
        target = 1 if change == "expected_fact_blocked" else 0
        summary = _install_summary(
            case.drafts[target].content_md, case.chapter, blocked=change == "expected_fact_blocked"
        )
        if change == "missing_projection":
            summary.pop("commercial_action_projection")
        if change == "forged_blocker":
            summary["commercial_action_projection"]["issue_actions"] = [
                {
                    "effective_action": "block",
                    "assessment_kind": "current_fact_conflict",
                    "authority": "verified_source",
                }
            ]
        case.reports[target].structured_output = {
            **case.reports[target].structured_output,
            "evidence_summary": deepcopy(summary),
        }
        case.qualities[target].evidence_summary = deepcopy(summary)
        cycles[case.receipt["candidates"][target]["cycle_id"]]["attempts"][0]["review_result"][
            "evidence_summary"
        ] = deepcopy(summary)
    if change in {"report_body", "unknown"}:
        report.structured_output = payload
    case.chapter.metadata_json = {**case.chapter.metadata_json, "chapter_critic_cycles": cycles}
    await _recapture(case)
    with pytest.raises(ValueError):
        await _apply(case)
    assert not case.calls and recovery.RECOVERY_KEY not in case.chapter.metadata_json


async def test_caller_input_witness_cannot_be_reused_for_other_draft(case, monkeypatch):
    reused = {}

    async def stale_proof(*args, project, chapter, draft, report, cycle):
        if not reused:
            reused.update(
                await case.fresh_inputs(
                    *args, project=project, chapter=chapter, draft=draft, report=report, cycle=cycle
                )
            )
        return reused

    monkeypatch.setattr(recovery, "verify_retained_review_inputs", stale_proof)
    with pytest.raises(ValueError, match="input_proof_unbound"):
        await _apply(case)
    assert case.drafts[0].is_current and not case.drafts[1].is_current


async def test_actual_settings_source_rejection_is_terminal(case, monkeypatch):
    async def refused(*args, **kwargs):
        raise ValueError("retained_review_input_primary_source_changed")

    monkeypatch.setattr(recovery, "verify_retained_review_inputs", refused)
    with pytest.raises(ValueError, match="primary_source_changed"):
        await _apply(case)
    assert case.drafts[0].is_current and recovery.RECOVERY_KEY not in case.chapter.metadata_json


async def test_stale_identity_map_is_reloaded_and_new_user_pointer_preserved(case):
    async with case.factory() as other:
        await other.execute(
            update(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.id == case.drafts[0].id)
            .values(is_current=False)
        )
        await other.execute(
            update(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.id == case.drafts[1].id)
            .values(is_current=True)
        )
        await other.commit()
    assert case.drafts[0].is_current  # deliberately stale ORM state
    with pytest.raises(ValueError, match="source_witness_changed"):
        await _apply(case)
    assert case.drafts[1].is_current


@pytest.mark.parametrize("mutation", ["body", "receipt", "audit", "pointer", "count"])
async def test_idempotent_receipt_does_not_mask_later_changes(case, mutation):
    await _apply(case)
    await case.session.commit()
    if mutation == "body":
        case.drafts[1].content_md += " new user body"
    elif mutation == "pointer":
        case.drafts[0].is_current, case.drafts[1].is_current = True, False
    elif mutation == "count":
        case.chapter.current_word_count += 1
    else:
        metadata = deepcopy(case.chapter.metadata_json)
        if mutation == "receipt":
            metadata[recovery.RECOVERY_KEY][0]["expected"]["draft_id"] = str(uuid4())
        else:
            metadata[AUDIT_KEY].append({**case.receipt, "chosen_draft_id": str(uuid4())})
        case.chapter.metadata_json = metadata
    await case.session.commit()
    with pytest.raises(ValueError):
        await _apply(case)


async def test_wrong_scope_and_reused_witness_rejected(case):
    for override in (
        {"project_id": uuid4()},
        {"chapter_id": uuid4()},
        {"expected_witness": {**case.witness, "chapter_id": str(uuid4())}},
    ):
        with pytest.raises(ValueError, match="stalled_selection_recovery_"):
            await _apply(case, **override)
    assert not case.calls


async def test_helper_never_flushes_unrelated_pending_work(case):
    case.project.title = "Uncommitted user edit"
    with pytest.raises(ValueError, match="clean_session_required"):
        await _apply(case)
    assert case.project in case.session.dirty
    assert case.drafts[0].is_current


async def test_failure_after_pointer_mutation_rolls_back_helper_savepoint(case, monkeypatch):
    original = recovery._load
    previous_count = case.chapter.current_word_count
    project_id, chapter_id = case.project.id, case.chapter.id
    count = 0

    async def corrupt_after(session, project_id, chapter_id):
        nonlocal count
        count += 1
        rows = await original(session, project_id, chapter_id)
        if count == 3:
            rows["reports"][0].structured_output = {"unexpected": "write"}
        return rows

    monkeypatch.setattr(recovery, "_load", corrupt_after)
    with pytest.raises(ValueError, match="unexpected_write"):
        await _apply(case)
    rows = await original(case.session, project_id, chapter_id)
    assert [d.id for d in rows["drafts"] if d.is_current] == [case.drafts[0].id]
    assert rows["chapter"].current_word_count == previous_count == case.drafts[0].word_count
    assert all(
        r.structured_output.get("raw_physical_return") == "preserve byte for byte"
        for r in rows["reports"]
    )
    assert recovery.RECOVERY_KEY not in rows["chapter"].metadata_json


async def test_public_witness_loader_reads_full_scope_without_mutation(case):
    assert (
        await recovery.load_stalled_selection_witness(
            case.session, project_id=case.project.id, chapter_id=case.chapter.id
        )
        == case.witness
    )
    assert not (case.session.new or case.session.dirty or case.session.deleted)


async def test_recomputed_receipt_hash_cannot_create_adoption_permission(case):
    await _apply(case)
    metadata = deepcopy(case.chapter.metadata_json)
    forged = metadata[recovery.RECOVERY_KEY][-1]
    forged["adoption_authorized"] = True
    forged["receipt_sha256"] = recovery._receipt_hash(forged)
    case.chapter.metadata_json = metadata
    await case.session.commit()
    with pytest.raises(ValueError, match="restored_source_changed"):
        await _apply(case)


async def test_sources_rechecked_after_awaited_retained_context_proof(case, monkeypatch):
    async def concurrent_change(*args, **kwargs):
        proof = await case.fresh_inputs(*args, **kwargs)
        await case.session.execute(
            update(QualityScoreModel)
            .where(QualityScoreModel.id == case.qualities[0].id)
            .values(score_overall=0.99)
        )
        return proof

    monkeypatch.setattr(recovery, "verify_retained_review_inputs", concurrent_change)
    with pytest.raises(ValueError, match="source_changed_during_proof"):
        await _apply(case)
    assert case.drafts[0].is_current and not case.drafts[1].is_current
    assert recovery.RECOVERY_KEY not in case.chapter.metadata_json

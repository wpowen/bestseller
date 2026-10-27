"""Action projection through the actual review entry and SQLite readback.

The retained stable boundary is stubbed with real wire/slot/aggregation output;
no provider, paid-origin verification or literary acceptance is claimed here.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_primary_review_submission_entrypoint import primary_db, _tool_completion  # noqa: F401
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401
from test_commercial_action_projection import slot as bound_slot

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, ExecutionBudgetModel,
    ReviewReportModel, RewriteTaskModel, WorkflowRunModel,
)
from bestseller.services import chapter_generation_input_builder as builder
from bestseller.services import chapter_llm_quality_judge as commercial
from bestseller.services import reviews
from bestseller.services.commercial_source_evidence import assemble_commercial_sources

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _proposal(text):
    lines = text.splitlines()
    return [json.loads(lines[i + 1]) for i, line in enumerate(lines)
            if line == "REVIEW_REPAIR_HANDOFF_PROPOSAL_JSON:"]


@pytest.mark.parametrize("case,primary_verdict,commercial_blocks", [
    ("narrative", "pass", False),
    ("narrative", "rewrite", False),
    ("narrative", "pass", True),
    ("rule", "pass", True),
    ("unsupported", "rewrite", True),
    ("foreign_binding", "rewrite", True),
])
async def test_bound_wire_action_controls_review_without_mutating_cohort_or_body(
    primary_db, monkeypatch, case, primary_verdict, commercial_blocks,  # noqa: F811
):
    from bestseller.infra.db import session as db_session

    db = primary_db
    db.settings.pipeline.enable_chapter_llm_commercial_judge = True
    db.settings.pipeline.chapter_llm_commercial_judge_block_on_failure = commercial_blocks
    current_body = db.draft.content_md
    current_hash = hashlib.sha256(current_body.encode()).hexdigest()
    primary = _tool_completion()
    physical = json.loads(primary.tool_calls[0]["function"]["arguments"])
    physical["verdict"] = primary_verdict
    if primary_verdict == "pass":
        physical["rewrite_direction"] = ""
    primary.tool_calls[0]["function"]["arguments"] = json.dumps(physical, ensure_ascii=False)
    primary.raw_message["tool_calls"] = deepcopy(primary.tool_calls)
    primary_before = deepcopy(vars(primary))
    requests = []

    async def primary_return(session, settings, request):
        requests.append(request)
        assert request.prompt_version == "7.0-citation-tuple-submission"
        assert request.metadata["primary_review_body_sha256"] == current_hash
        return primary

    monkeypatch.setattr(reviews, "complete_text", primary_return)
    # Feed a literal applicable user clause into the existing generation-input
    # channel. Its real projection/catalogue and RULE validation are not stubbed.
    if case == "rule":
        original_builder = builder.build_chapter_generation_input_bundle

        def with_explicit_rule(**kwargs):
            bundle = original_builder(**kwargs)
            class LiteralSourceBundle:
                def model_dump(self, **options):
                    data = bundle.model_dump(**options)
                    data["approved_chapter_contract"] = {
                        "text": "Keep the archive key at the desk until the receipt is signed.",
                        "source": "user", "hard": True, "scope": {"chapter_numbers": [2]},
                    }
                    return data
            return LiteralSourceBundle()

        monkeypatch.setattr(builder, "build_chapter_generation_input_bundle", with_explicit_rule)

    async with db.session.bind.begin() as connection:
        for model in (WorkflowRunModel, ExecutionBudgetModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    owner = WorkflowRunModel(project_id=db.project.id, workflow_type="chapter_review",
                             status="completed", scope_type="chapter", scope_id=db.chapter.id,
                             metadata_json={"protected_prior_history": ["do not replace"]})
    budget = ExecutionBudgetModel(
        scope_id=f"project:{db.project.id}", max_calls=99, max_tokens=999999, calls_used=3,
        tokens_charged=45, deadline_at=datetime.now(timezone.utc) + timedelta(days=1),
        status="active", metadata_json={"fixture_ledger": "retained"},
    )
    db.session.add_all([owner, budget])
    await db.session.commit()
    retained = {}
    original_stable = commercial.judge_chapter_commercial_quality_stable

    @asynccontextmanager
    async def sequential_fixture_pool():
        raise RuntimeError("no provider sessions in this fixture")
        yield

    monkeypatch.setattr(db_session, "get_server_session", sequential_fixture_pool)
    monkeypatch.setattr(commercial, "chapter_commercial_thresholds", lambda *args: (.82, {"readability": .8}))

    async def retained_stable(caller, settings, **kwargs):
        assert kwargs["content_md"] == current_body
        selected = commercial._commercial_source_projection(
            kwargs["generation_input"], chapter_number=2, content_md=current_body,
            previous_chapters=kwargs["previous_chapters"],
        )
        src = assemble_commercial_sources(chapter_number=2, content_md=current_body,
                                         source=selected, previous_chapters=kwargs["previous_chapters"])
        if case == "rule":
            assert any(entry["kind"] == "constraint" for entry in src.catalog.values())
        kind = "constraint_violation" if case == "rule" else "narrative_quality"
        slots = [bound_slot(src, index, kind=kind) for index in range(3)]
        if case in {"unsupported", "foreign_binding"}:
            envelope = json.loads(slots[0].raw_excerpt)
            if case == "unsupported":
                envelope["evaluation_contract"]["adapter_version"] = "unsupported-retained-adapter"
            else:
                envelope["source_binding"]["body_sha256"] = "0" * 64
            slots[0] = slots[0].model_copy(update={"raw_excerpt": json.dumps(envelope)})

        async def retained_draw(session, config, **draw):
            return slots[int(draw["sample_slot"].rsplit(":", 1)[1])]

        monkeypatch.setattr(commercial, "judge_chapter_commercial_quality", retained_draw)
        # Execute production median/quorum/critical-union logic. The outer
        # stable/cohort verification boundary remains the explicit fixture stub.
        aggregate = await original_stable(object(), settings, chapter_number=2,
                                          content_md=current_body, samples=3)
        key = _digest({"project_id": str(db.project.id), "chapter_id": str(db.chapter.id),
                       "body_sha256": current_hash})
        slot_reports = {
            f"chapter-quality.independent-v2:{i}": {"state": "completed", "report": s.model_dump(mode="json")}
            for i, s in enumerate(slots)}
        journal = {"schema": "commercial-component-cohort.v1", "historical": True,
                   "binding": {"project_id": str(db.project.id), "chapter_id": str(db.chapter.id),
                               "body_sha256": current_hash, "samples": 3,
                               "scope_id": budget.scope_id}, "slots": slot_reports,
                   "aggregate": {"slots_sha256": _digest({k: v["report"] for k, v in slot_reports.items()}),
                                 "report": aggregate.model_dump(mode="json")}}
        owner.metadata_json = {**owner.metadata_json, "commercial_component_cohorts": {key: journal}}
        pointer = {"workflow_run_id": str(owner.id), "schema": "commercial-component-cohort.v1"}
        db.chapter.metadata_json = {**db.chapter.metadata_json, "commercial_component_cohorts": {key: pointer}}
        await caller.commit()
        retained.update(aggregate=aggregate, original=deepcopy(aggregate.model_dump(mode="json", by_alias=True)),
                        journal=deepcopy(owner.metadata_json), pointer=deepcopy(pointer), key=key,
                        slots=[s.model_dump(mode="json") for s in slots])
        return aggregate

    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", retained_stable)
    result, report, quality, task = await reviews.review_chapter_draft(
        db.session, db.settings, db.project.slug, 2,
    )
    await db.session.commit()
    unavailable = case in {"unsupported", "foreign_binding"}
    assert len(requests) == (0 if unavailable else 1) and vars(primary) == primary_before
    assert retained["aggregate"].model_dump(mode="json", by_alias=True) == retained["original"]
    async with db.factory() as reader:
        stored = await reader.get(ReviewReportModel, report.id)
        persisted = stored.structured_output
        evidence = persisted["evidence_summary"]
        assert evidence["llm_commercial_judge_original"] == retained["original"]
        assert persisted["critic_evidence"]["status"] == ("unavailable" if unavailable else "verified")
        if unavailable:
            assert evidence["primary_review_not_dispatched"]["provider_calls"] == 0
        stored_draft = await reader.get(ChapterDraftVersionModel, db.draft.id)
        assert stored_draft.content_md == current_body and stored_draft.is_current is True
        assert stored_draft.promotion_state == "candidate"
        assert (await reader.get(WorkflowRunModel, owner.id)).metadata_json == retained["journal"]
        chapter = await reader.get(ChapterModel, db.chapter.id)
        assert chapter.metadata_json["commercial_component_cohorts"][retained["key"]] == retained["pointer"]
        ledger = await reader.get(ExecutionBudgetModel, budget.scope_id)
        assert (ledger.calls_used, ledger.tokens_charged, ledger.status) == (3, 45, "active")
        assert ledger.metadata_json == {"fixture_ledger": "retained"}
        tasks = list(await reader.scalars(select(RewriteTaskModel)))
        receipt = evidence["commercial_action_projection"]
        if case in {"unsupported", "foreign_binding"}:
            assert receipt["status"] in {"blocked", "unsupported"}
            assert chapter.production_state == "review_unknown"
            assert task is None and tasks == [] and result.verdict == "unknown"
            # Unavailable evidence establishes Unknown, never a failed body gate.
            assert quality.evidence_summary["hard_gates_passed"] is None
            assert any(i["code"] == "CHAPTER_JUDGE_UNAVAILABLE"
                       for i in evidence["llm_commercial_judge"]["blocking_issues"])
            return
        assert receipt["status"] == "projected"
        view = evidence["llm_commercial_judge"]
        assert view["pass"] is retained["original"]["pass"] is False
        assert view["overall_score"] == retained["original"]["overall_score"]
        assert view["dimension_scores"] == retained["original"]["dimension_scores"]
        assert view["raw_excerpt"] == retained["original"]["raw_excerpt"]
        assert receipt["source_binding"]["body_sha256"] == current_hash
        if case == "narrative":
            assert view["blocking_issues"] == [] and view["has_critical"] is False
            assert view["audit_issues"][0]["severity"] == "critical"
            assert not any(f["severity"] == "critical" for f in persisted["findings"])
            assert reviews._promotion_evidence_from_review(result)["blocking_codes"] == []
            assert all(a["authority"] == "craft_only" for a in receipt["issue_actions"])
            if primary_verdict == "pass" and not commercial_blocks:
                assert result.verdict == "pass" and task is None and tasks == []
        else:
            assert view["has_critical"] is True and view["blocking_issues"]
            assert any(f["severity"] == "critical" for f in persisted["findings"])
            assert reviews._promotion_evidence_from_review(result)["blocking_codes"]
            assert all(a["authority"] == "verified_source" for a in receipt["issue_actions"])
        if task is not None:
            saved_task = await reader.get(RewriteTaskModel, task.id)
            assert saved_task.metadata_json["parent_draft_id"] == str(db.draft.id)
            commercial_proposals = [p for p in _proposal(saved_task.instructions) if p["evaluator"] == "commercial"]
            assert len(commercial_proposals) == 1
            proposal = commercial_proposals[0]
            assert proposal["authority"] == "proposal_only"
            assert proposal["source_payload_sha256"] == _digest(view)
            assert proposal["audit_issues"] == view["audit_issues"]
            assert proposal["blocking_issues"] == view["blocking_issues"]

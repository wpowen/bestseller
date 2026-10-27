"""Real SQLite admission production; synthetic paid rows, never a provider.

Bodies, gates, source fingerprints, typed submissions and ORM readback are real.
This establishes framework behavior, not production or literary acceptance.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401
from test_primary_review_submission import completion, payload as primary_payload

from bestseller.infra.db import models as db
from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.services import drafts, llm, pipelines, reviews, quality_gates_config as gates
from bestseller.services.chapter_review_admission import build_reviewed_chapter_admission
from bestseller.services.invariants import ProjectInvariants, LengthEnvelope, invariants_to_dict
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
BODY = (
    "The astronomer carried the sealed calibration log to the observatory desk. "
    "Dust covered the western lens, but its recorded offset remained legible. "
    "A technician compared the printed measurements against the silver reference scale.\n\n"
    "Outside, a supply cart stopped beside the loading ramp. Its driver handed over a brass key "
    "and waited while the technician checked the delivery register. Nothing in the envelope "
    "identified the sender or explained the missing serial number.\n\n"
    "The astronomer locked the lens cabinet and signed the receipt. After the driver left, "
    "the technician noticed a second measurement written beneath the original seal."
)


@pytest.fixture
async def admission_db(tmp_path, monkeypatch):
    path = tmp_path / "chapter_review_admission_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.url.drivername == "sqlite+aiosqlite"
    async with engine.begin() as connection:
        for table in db.Base.metadata.sorted_tables:
            ddl = str(CreateTable(table).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = load_settings(env={})
    settings.output.base_dir = tmp_path
    settings.pipeline.enable_chapter_llm_commercial_judge = False
    settings.pipeline.enable_chapter_window_llm_judge = False
    cfg = replace(gates.QualityGatesConfig(), l6_enabled=False,
                  phase_c=replace(gates.QualityGatesConfig().phase_c, enabled=False))
    box = SimpleNamespace(cfg=cfg)
    for module in (gates, drafts, reviews):
        monkeypatch.setattr(module, "get_quality_gates_config", lambda: box.cfg)

    async def deny(*args, **kwargs):
        pytest.fail("admission must not dispatch a provider")
    for module in (llm, reviews, drafts):
        monkeypatch.setattr(module, "complete_text", deny)
    async with factory() as session:
        project = db.ProjectModel(slug=f"admission-{uuid4().hex[:10]}", title="Orbital records",
            genre="observatory mystery", language="en", target_word_count=1000, target_chapters=10,
            metadata_json={"words_per_chapter": {"min": 20, "target": 100, "max": 200}})
        session.add(project)
        await session.flush()
        chapter = db.ChapterModel(project_id=project.id, chapter_number=1,
            chapter_goal="Receive and inspect the recorded measurement.", target_word_count=100,
            metadata_json={})
        session.add(chapter)
        await session.flush()
        scene = db.SceneCardModel(project_id=project.id, chapter_id=chapter.id, scene_number=1,
            scene_type="investigation", purpose={"goal": "Inspect the delivery"},
            entry_state={"log": "sealed"}, exit_state={"log": "recorded"}, target_word_count=100)
        session.add(scene)
        await session.flush()
        draft = db.ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=1, content_md=BODY, word_count=drafts.count_words(BODY), is_current=True,
            promotion_state="candidate", assembled_from_scene_draft_ids=[f"chapter_first_scene:{scene.id}"])
        workflow = db.WorkflowRunModel(project_id=project.id,
            workflow_type=pipelines.WORKFLOW_TYPE_CHAPTER_PIPELINE, status="running",
            scope_type="chapter", scope_id=chapter.id, metadata_json={})
        session.add_all([draft, workflow])
        await session.flush()
        budget = db.ExecutionBudgetModel(scope_id=f"project:{project.id}", max_calls=50,
            max_tokens=100000, calls_used=1, tokens_charged=30, status="active",
            deadline_at=datetime.now(timezone.utc) + timedelta(days=1), metadata_json={"preserve": True})
        session.add(budget)
        run = db.LlmRunModel(project_id=project.id, workflow_run_id=workflow.id, logical_role="critic",
            provider="fixture-recorded", model_name="fixture-critic", prompt_template="chapter_review",
            prompt_version="7.0-citation-tuple-submission", prompt_hash="a" * 64,
            finish_reason="tool_calls", input_tokens=20, output_tokens=10, metadata_json={})
        session.add(run)
        await session.flush()
        scores = ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, .62))
        domain = ChapterReviewResult(verdict="rewrite", severity_max="minor", scores=scores,
            rewrite_instructions="Improve progression when the current debt policy allows it.")
        data = primary_payload()
        data.update(verdict="pass", rewrite_direction="", scores=scores.model_dump(mode="json"))
        c = completion(data, raw_message=True)
        physical = llm.LLMCompletionResult(content=c.content, tool_calls=c.tool_calls,
            raw_message=c.raw_message, provider=run.provider, model_name=run.model_name,
            finish_reason="tool_calls", input_tokens=20, output_tokens=10,
            metadata={"paid_call_origin": {"project_id": str(project.id),
                "workflow_run_id": str(workflow.id), "prompt_hash": run.prompt_hash,
                "prompt_template": run.prompt_template, "prompt_version": run.prompt_version}})
        root_key, attempt_id = uuid4().hex, uuid4().hex
        now = datetime.now(timezone.utc)
        root = db.ExecutionOperationModel(key=root_key, scope_id=budget.scope_id,
            input_hash=run.prompt_hash, status="completed", owner="fixture", lease_until=now,
            result_json=physical.model_dump(mode="json"))
        attempt = db.ExecutionAttemptModel(id=attempt_id, operation_key=root_key,
            scope_id=budget.scope_id, input_hash=run.prompt_hash, model_name=run.model_name,
            reserved_tokens=40, actual_tokens=30, status="completed", created_at=now)
        transport = db.ExecutionOperationModel(key=hashlib.sha256(
            f"transport-result.v1\n{budget.scope_id}\n{attempt_id}".encode()).hexdigest(),
            scope_id=budget.scope_id, input_hash=run.prompt_hash, status="transport_result",
            owner="fixture", lease_until=now, result_json={"attempt_id": attempt_id,
                "operation_key": root_key, "input_hash": run.prompt_hash,
                "result": physical.model_dump(mode="json")})
        run.metadata_json = {"primary_review_body_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
            "execution_receipt": {"scope_id": budget.scope_id, "operation_key": root_key,
                                  "attempts": [{"attempt_id": attempt_id, "status": "completed"}]}}
        _, proof, errors = parse_primary_review_submission(physical, BODY)
        assert not errors
        raw = {**domain.model_dump(mode="json"), "draft_id": str(draft.id),
            "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(BODY),
            "primary_submission_receipt": proof, "critic_evidence": {"status": "verified"}}
        report = db.ReviewReportModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
            reviewer_type="critic", verdict="rewrite", severity_max="minor", structured_output=raw,
            llm_run_id=run.id)
        session.add_all([root, attempt, transport, report])
        await session.flush()
        quality = db.QualityScoreModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
            chapter_draft_version_id=draft.id, review_report_id=report.id, is_current=True,
            judge_key="critic", score_goal=.62, score_conflict=.62, score_emotion=.62,
            score_style=.62, score_overall=.62, evidence_summary={"retained": True})
        session.add(quality)
        await session.commit()
        box.__dict__.update(session=session, settings=settings, project=project, chapter=chapter,
            scene=scene, draft=draft, workflow=workflow, budget=budget, report=report, quality=quality,
            run=run, root=root, transport=transport, factory=factory, domain=domain)

        async def bind():
            await session.commit()
            semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
            fingerprint = reviews.chapter_review_body_fingerprint(draft.content_md)
            cycle = {"cycle_id": "fixture-bound-cycle", "state": "completed", "source_draft_id": str(draft.id),
                "source_body_hash": fingerprint["digest"], "policy_source_hash": pipelines.chapter_review_recovery_source_hash(),
                "semantic_dependencies_sha256": semantic, "review_workflow_run_id": str(workflow.id),
                "attempts": [{"state": "completed", "outcome": "available", "draft_id": str(draft.id),
                    "body_fingerprint": fingerprint, "review_report_id": str(report.id),
                    "quality_score_id": str(quality.id), "review_result": domain.model_dump(mode="json")}]}
            chapter.metadata_json = {**chapter.metadata_json, "chapter_critic_cycles": {"fixture": cycle}}
            await session.commit()
        box.bind = bind

        async def produce(**kwargs):
            return await build_reviewed_chapter_admission(session, settings, project=project,
                chapter=chapter, draft=draft, report=report, quality=quality, workflow_id=workflow.id, **kwargs)
        box.produce = produce
        await bind()
        yield box
    await engine.dispose()


async def test_soft_rewrite_eligible_without_grade_or_ledger_changes(admission_db):
    d = admission_db
    before = deepcopy(d.report.structured_output)
    receipt = await d.produce()
    assert receipt["unknown"] == [], receipt
    assert receipt["effective_blockers"] == [], receipt
    assert receipt["draft_eligible"] is True and receipt["quality_passed"] is False
    assert receipt["gate_receipts"]["body_gate"]["status"] == "not_applicable"
    assert receipt["gate_receipts"]["explicit_plan"]["status"] == "not_applicable"
    await d.session.commit()
    async with d.factory() as reader:
        assert (await reader.get(db.ReviewReportModel, d.report.id)).structured_output == before
        draft = await reader.get(db.ChapterDraftVersionModel, d.draft.id)
        assert draft.content_md == BODY and draft.promotion_state == "candidate" and draft.is_current
        ledger = await reader.get(db.ExecutionBudgetModel, d.budget.scope_id)
        assert (ledger.calls_used, ledger.tokens_charged, ledger.metadata_json) == (1, 30, {"preserve": True})
        assert (await reader.get(db.QualityScoreModel, d.quality.id)).score_goal == d.quality.score_goal


async def test_enabled_missing_invariants_is_unknown_not_disabled(admission_db):
    d = admission_db
    d.cfg = replace(d.cfg, l6_enabled=True)
    receipt = await d.produce()
    assert not receipt["draft_eligible"]
    assert any("enabled_gate_missing_invariants" in value for value in receipt["unknown"])


async def test_real_body_gate_receipt_has_exact_evaluation_and_readback(admission_db):
    d = admission_db
    d.cfg = replace(d.cfg, l6_enabled=True, l5=replace(d.cfg.l5, enabled=False))
    d.project.invariants_json = invariants_to_dict(ProjectInvariants(project_id=d.project.id,
        language="en", length_envelope=LengthEnvelope(20, 100, 200)))
    await d.bind()
    receipt = await d.produce()
    assert receipt["unknown"] == [], receipt
    gate = receipt["gate_receipts"]["body_gate"]
    assert gate["status"] == "verified" and gate["evaluation_id"]
    await d.session.commit()
    async with d.factory() as reader:
        from uuid import UUID
        actual = await reader.get(db.ChapterQualityReportModel, UUID(gate["report_id"]))
        assert actual.report_json["rewrite_evaluation_id"] == gate["evaluation_id"]
        assert actual.report_json["graded"] == gate["graded"]


@pytest.mark.parametrize("case", ["transport", "score", "body", "policy", "semantic", "primary_missing"])
async def test_changed_or_missing_proof_never_admits(admission_db, case):
    d = admission_db
    if case == "transport":
        d.transport.result_json = {**d.transport.result_json, "input_hash": "f" * 64}
    elif case == "score":
        d.quality.score_goal = .99
    elif case == "body":
        d.draft.content_md += "\n\nA new and unreviewed observation."
    elif case in {"policy", "semantic"}:
        meta = deepcopy(d.chapter.metadata_json)
        key = "policy_source_hash" if case == "policy" else "semantic_dependencies_sha256"
        meta["chapter_critic_cycles"]["fixture"][key] = "0" * 64
        d.chapter.metadata_json = meta
    else:
        d.report.llm_run_id = None
    await d.session.commit()
    receipt = await d.produce()
    assert not receipt["draft_eligible"] and receipt["unknown"], receipt


@pytest.mark.parametrize("case", ["empty", "unsupported", "mismatch", "unselected_cache"])
async def test_canonical_literal_inventory_not_model_severity(admission_db, case):
    d = admission_db
    clause = {"hard": True, "path": "draft.content_md", "operator": "contains", "expected": "NOT_IN_BODY"}
    intent = {"field_sources": {"hard_constraints": "explicit"}, "hard_constraints": []}
    if case == "unsupported":
        intent["hard_constraints"] = [{"hard": True, "text": "Prove an abstract semantic condition."}]
    elif case == "mismatch":
        intent["hard_constraints"] = [clause]
    elif case == "unselected_cache":
        intent = {"source": "user", "hard_constraints": [clause], "field_sources": {}}
    d.project.metadata_json = {**d.project.metadata_json, "creation_intent_contract": intent}
    await d.bind()
    receipt = await d.produce()
    if case == "unsupported":
        assert not receipt["draft_eligible"] and any("not_executable" in v for v in receipt["unknown"])
    elif case == "mismatch":
        assert not receipt["draft_eligible"]
        assert any(v["gate"] == "explicit_plan" for v in receipt["effective_blockers"])
    else:
        assert receipt["draft_eligible"], receipt


@pytest.mark.parametrize("gate", ["deterministic", "dedup"])
async def test_detector_exception_is_unknown_not_empty_proof(admission_db, monkeypatch, gate):
    d = admission_db
    if gate == "deterministic":
        def broken(**kwargs):
            raise RuntimeError("failed actual audit")
        monkeypatch.setattr(reviews, "_audit_chapter_rewrite_candidate", broken)
    else:
        async def broken(*args, **kwargs):
            raise RuntimeError("failed actual audit")
        monkeypatch.setattr(drafts, "_collect_post_assembly_duplicate_findings", broken)
    receipt = await d.produce()
    assert not receipt["draft_eligible"] and any(v.startswith(gate + ":") for v in receipt["unknown"])


async def _commercial(d, monkeypatch, *, rule=False, invalid_slot=False, nonrepresentative=False,
                      availability_finish=None):
    from test_commercial_action_projection import slot as bound_slot
    from bestseller.services import chapter_llm_quality_judge as commercial
    from bestseller.services import commercial_judge_recovery as recovery
    from bestseller.services.commercial_source_evidence import assemble_commercial_sources
    from bestseller.infra.db import session as db_session
    from uuid import UUID

    d.settings.pipeline.enable_chapter_llm_commercial_judge = True
    source = {"approved_chapter_contract": {"text": "Use third person.", "source": "user", "hard": True}} if rule else {}
    sources = assemble_commercial_sources(chapter_number=1, content_md=BODY, source=source, previous_chapters=[])
    slots = [bound_slot(sources, i, kind="constraint_violation" if rule else "narrative_quality",
        unknown=invalid_slot and i == 2) for i in range(3)]
    if invalid_slot:
        # Match the actual paid-slot adapter's unavailable diagnostic, rather
        # than the projection-only helper's deliberately arbitrary label.
        from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
        from bestseller.services.commercial_source_evidence import commercial_judge_unavailable
        from bestseller.services.commercial_review_wire import parse_commercial_wire
        env = json.loads(slots[2].raw_excerpt)
        parsed, _, errors = parse_commercial_wire(env["raw_report"], sources)
        base = quality_judge_result_from_mapping(parsed, scope="chapter", min_overall=.82,
            min_dimensions={"readability": .8}, llm_run_id=slots[2].llm_run_id)
        unavailable = commercial_judge_unavailable(diagnostic="commercial source evidence unverified: " + "; ".join(errors),
            raw="", result=base)
        slots[2] = recovery.bind_commercial_slot_result(unavailable, raw_report=env["raw_report"],
            validation_errors=errors, source_receipt=sources.receipt,
            sample_slot="chapter-quality.independent-v2:2", attempt=1,
            adapter_receipt=env["adapter_receipt"], evaluation_contract=env["evaluation_contract"])
    if nonrepresentative:
        slots = [bound_slot(sources, i, issues=False, score=.91) for i in range(2)] + [
            bound_slot(sources, 2, kind="current_fact_conflict", severity="high", score=.98)]
    if availability_finish is not None:
        from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult, quality_judge_result_from_mapping
        from bestseller.services.commercial_source_evidence import commercial_judge_unavailable
        from bestseller.services.commercial_review_tool import submitted_review_payload
        from bestseller.services.commercial_review_wire import parse_commercial_wire
        for i, slot in enumerate(slots):
            env = json.loads(slot.raw_excerpt)
            raw, submission, errors = submitted_review_payload(llm.LLMCompletionResult(
                content="", provider="fixture-recorded", model_name="fixture", finish_reason=availability_finish))
            parsed, adapter, wire_errors = parse_commercial_wire(raw, sources)
            errors = (*errors, *wire_errors)
            try:
                base = quality_judge_result_from_mapping(parsed, scope="chapter", min_overall=.82,
                    min_dimensions={"readability": .8}, llm_run_id=slot.llm_run_id)
            except (ValueError, TypeError):
                errors = ("report: response schema is unverified",)
                unavailable = commercial_judge_unavailable(diagnostic="commercial judge response schema is unverified",
                    result=LLMQualityJudgeResult(llm_run_id=slot.llm_run_id))
            else:
                unavailable = commercial_judge_unavailable(
                    diagnostic="commercial source evidence unverified: " + "; ".join(errors), result=base)
            adapter["submission"] = submission
            slots[i] = recovery.bind_commercial_slot_result(unavailable, raw_report=raw,
                validation_errors=errors, source_receipt=sources.receipt,
                sample_slot=f"chapter-quality.independent-v2:{i}", attempt=1,
                adapter_receipt=adapter, evaluation_contract=env["evaluation_contract"])
    critic = await commercial._commercial_effective_critic_configuration(d.session, d.settings, d.project.id)
    contract = json.loads(slots[0].raw_excerpt)["evaluation_contract"]
    for i, slot in enumerate(slots):
        env = json.loads(slot.raw_excerpt)
        run = db.LlmRunModel(id=UUID(slot.llm_run_id), project_id=d.project.id, workflow_run_id=d.workflow.id,
            logical_role="critic", provider="fixture-recorded", model_name=critic["model"],
            prompt_template="chapter_commercial_quality_judge", prompt_version=sources.receipt["prompt_version"],
            prompt_hash=hashlib.sha256(f"paid-commercial-{i}".encode()).hexdigest(), finish_reason=availability_finish or "tool_calls",
            metadata_json={})
        calls = [{"id": "retained-call", "function": {"name": "submit_chapter_review", "arguments": env["raw_report"]}}]
        paid = llm.LLMCompletionResult(content="", provider=run.provider, model_name=run.model_name,
            finish_reason=availability_finish or "tool_calls", tool_calls=None if availability_finish else calls,
            metadata={"paid_call_origin": {"project_id": str(d.project.id),
                "workflow_run_id": str(d.workflow.id), "prompt_hash": run.prompt_hash,
                "prompt_template": run.prompt_template, "prompt_version": run.prompt_version}})
        key, aid, now = uuid4().hex, uuid4().hex, datetime.now(timezone.utc)
        op = db.ExecutionOperationModel(key=key, scope_id=d.budget.scope_id, input_hash=run.prompt_hash,
            status="completed", owner="fixture", lease_until=now, result_json=paid.model_dump(mode="json"))
        attempt = db.ExecutionAttemptModel(id=aid, operation_key=key, scope_id=d.budget.scope_id,
            input_hash=run.prompt_hash, model_name=run.model_name, status="completed", reserved_tokens=40,
            actual_tokens=30, created_at=now)
        transport = db.ExecutionOperationModel(key=hashlib.sha256(
            f"transport-result.v1\n{d.budget.scope_id}\n{aid}".encode()).hexdigest(),
            scope_id=d.budget.scope_id, input_hash=run.prompt_hash, status="transport_result",
            owner="fixture", lease_until=now, result_json={"operation_key": key, "attempt_id": aid,
                "input_hash": run.prompt_hash, "result": paid.model_dump(mode="json")})
        run.metadata_json = {"commercial_source_input_receipt": sources.receipt,
            "commercial_sample_slot": f"chapter-quality.independent-v2:{i}",
            "execution_receipt": {"scope_id": d.budget.scope_id, "operation_key": key,
                                  "attempts": [{"attempt_id": aid, "status": "completed"}]}}
        d.session.add_all([run, op, attempt, transport])
    @asynccontextmanager
    async def unavailable_pool():
        raise RuntimeError("fixture borrowed session")
        yield
    async def draw(session, settings, **kwargs):
        return slots[int(kwargs["sample_slot"].rsplit(":", 1)[1])]
    monkeypatch.setattr(db_session, "get_server_session", unavailable_pool)
    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality", draw)
    monkeypatch.setattr(commercial, "chapter_commercial_thresholds", lambda *a: (.82, {"readability": .8}))
    aggregate = await commercial.judge_chapter_commercial_quality_stable(object(), d.settings,
        chapter_number=1, content_md=BODY, samples=3)
    original = aggregate.model_dump(mode="json", by_alias=True)
    binding = {"project_id": str(d.project.id), "chapter_id": str(d.chapter.id), "draft_id": str(d.draft.id),
        "source_binding": {k: sources.receipt[k] for k in recovery._BINDING_KEYS},
        "evaluation_contract": contract, "samples": 3, "scope_id": d.budget.scope_id,
        "effective_critic_sha256": recovery._digest(critic)}
    key = recovery._digest({"project_id": str(d.project.id), "chapter_id": str(d.chapter.id),
                           "body_sha256": sources.receipt["body_sha256"]})
    entries = {f"chapter-quality.independent-v2:{i}": {"state": "completed", "used_attempts": 1,
                "report": slot.model_dump(mode="json")} for i, slot in enumerate(slots)}
    journal = {"schema": recovery.COHORT_SCHEMA, "binding": binding, "slots": entries,
        "aggregate": {"slots_sha256": recovery._digest({s: e["report"] for s, e in entries.items()}),
                      "report": original}}
    d.workflow.metadata_json = {"commercial_component_cohorts": {key: journal}}
    d.chapter.metadata_json = {**d.chapter.metadata_json, "commercial_component_cohorts": {
        key: {"workflow_run_id": str(d.workflow.id), "binding_sha256": recovery._digest(binding)}}}
    raw = deepcopy(d.report.structured_output)
    raw["evidence_summary"]["llm_commercial_judge_original"] = original
    d.report.structured_output = raw
    d.domain.evidence_summary["llm_commercial_judge_original"] = original
    await d.bind()
    return sources, deepcopy(journal)


@pytest.mark.parametrize("case", ["narrative", "rule", "invalid_abstains", "nonrepresentative_fact", "physical_missing", "sources_missing"])
async def test_full_physical_commercial_quorum_and_typed_authority(admission_db, monkeypatch, case):
    d = admission_db
    sources, original = await _commercial(d, monkeypatch, rule=case == "rule",
        invalid_slot=case == "invalid_abstains", nonrepresentative=case == "nonrepresentative_fact")
    if case == "physical_missing":
        rows = list(await d.session.scalars(select(db.ExecutionOperationModel).where(
            db.ExecutionOperationModel.status == "transport_result")))
        commercial_transport = next(row for row in rows if row.key != d.transport.key)
        await d.session.delete(commercial_transport)
        await d.session.commit()
    receipt = await d.produce(commercial_sources=None if case == "sources_missing" else sources)
    if case in {"physical_missing", "sources_missing"}:
        assert not receipt["draft_eligible"] and any(v.startswith("commercial:") for v in receipt["unknown"]), receipt
    elif case in {"rule", "nonrepresentative_fact"}:
        assert receipt["unknown"] == [], receipt
        assert not receipt["draft_eligible"]
        assert any(v["gate"] == "commercial" for v in receipt["effective_blockers"]), receipt
    else:
        assert receipt["unknown"] == [] and receipt["draft_eligible"], receipt
        assert receipt["gate_receipts"]["commercial"]["raw_scalar_pass"] is False
        if case == "invalid_abstains":
            proof = receipt["gate_receipts"]["commercial"]["projection"]
            assert proof["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    await d.session.commit()
    async with d.factory() as reader:
        wf = await reader.get(db.WorkflowRunModel, d.workflow.id)
        assert next(iter(wf.metadata_json["commercial_component_cohorts"].values())) == original
        budget = await reader.get(db.ExecutionBudgetModel, d.budget.scope_id)
        assert (budget.calls_used, budget.tokens_charged) == (1, 30)


async def _replace_reviewed_body(d, body):
    """Create a coherent synthetic paid fixture for another exact body."""
    d.draft.content_md = body
    d.draft.word_count = drafts.count_words(body)
    d.run.metadata_json = {**d.run.metadata_json,
        "primary_review_body_sha256": hashlib.sha256(body.encode()).hexdigest()}
    paid = llm.LLMCompletionResult.model_validate(d.root.result_json)
    _, proof, errors = parse_primary_review_submission(paid, body)
    assert not errors
    d.report.structured_output = {**d.report.structured_output, "primary_submission_receipt": proof,
        "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(body)}
    await d.bind()


@pytest.mark.parametrize("case", ["length", "duplicate", "scene_source", "canary_missing", "literal_forbidden"])
async def test_existing_real_hard_gates_are_not_softened(admission_db, case):
    d = admission_db
    if case == "length":
        await _replace_reviewed_body(d, "The astronomer waited.")
    elif case == "duplicate":
        await _replace_reviewed_body(d, BODY + "\n\n" + BODY)
    elif case == "scene_source":
        d.draft.assembled_from_scene_draft_ids = []
        await d.session.commit()
    elif case == "canary_missing":
        d.settings.pipeline.story_engine_mode = "canary"
        d.settings.pipeline.story_engine_canary_project_ids = [str(d.project.id)]
        d.settings.pipeline.story_engine_canary_genres = []
        await d.bind()
    else:
        path = d.settings.output.base_dir / d.project.slug / "story-bible" / "canon-guardrails.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"forbidden_terms": [{"term": "silver reference scale"}]}))
    receipt = await d.produce()
    assert not receipt["draft_eligible"], receipt
    if case == "canary_missing":
        assert any("applicable_story_engine_observation_required" in v for v in receipt["unknown"]), receipt
    else:
        gate = {"length": "deterministic", "duplicate": "dedup", "scene_source": "scene_source",
                "literal_forbidden": "deterministic"}[case]
        assert any(v["gate"] == gate for v in receipt["effective_blockers"]), receipt


async def test_fabricated_gate_wrapper_cannot_stand_for_persisted_evaluation(admission_db):
    d = admission_db
    from bestseller.services.quality_report_claim import graded_text_fingerprint
    d.cfg = replace(d.cfg, l6_enabled=True)
    d.project.invariants_json = invariants_to_dict(ProjectInvariants(project_id=d.project.id,
        language="en", length_envelope=LengthEnvelope(20, 100, 200)))
    await d.bind()
    fake = db.ChapterQualityReportModel(id=uuid4(), chapter_id=d.chapter.id,
        report_json={"graded": graded_text_fingerprint(BODY), "rewrite_evaluation_id": "fake", "blocking_codes": []})
    receipt = await d.produce(body_gate_receipt=reviews.RewriteQualityGateReceipt(
        "ok", "fake", graded_text_fingerprint(BODY), fake))
    assert not receipt["draft_eligible"]
    assert any("body_gate_persisted_report_unverified" in v for v in receipt["unknown"]), receipt


async def test_uncommitted_author_changes_are_preserved_not_refreshed(admission_db):
    d = admission_db
    d.draft.content_md += "\n\nAn unsaved author addition."
    receipt = await d.produce()
    assert receipt["unknown"] == ["binding:uncommitted_source_or_review"]
    assert d.draft.content_md.endswith("An unsaved author addition.")


@pytest.mark.parametrize("evaluator,required,present", [
    ("commercial", False, False), ("commercial", True, False),
    ("commercial", False, True), ("commercial", True, True),
    ("window", False, True), ("window", True, True),
])
async def test_actual_config_distinguishes_optional_no_verdict_from_required_unknown(
    admission_db, evaluator, required, present,
):
    d = admission_db
    from bestseller.services.commercial_source_evidence import commercial_judge_unavailable
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
    if evaluator == "commercial":
        d.settings.pipeline.enable_chapter_llm_commercial_judge = True
        d.settings.pipeline.chapter_llm_commercial_judge_block_on_failure = required
        key = "llm_commercial_judge_original"
    else:
        d.settings.pipeline.enable_chapter_window_llm_judge = True
        d.settings.pipeline.chapter_window_llm_judge_block_on_failure = required
        key = "llm_window_quality_judge"
    raw = deepcopy(d.report.structured_output)
    if present:
        raw["evidence_summary"][key] = commercial_judge_unavailable(
            diagnostic="Retained no-verdict diagnostic, not a prose score.",
            result=LLMQualityJudgeResult(scope="window" if evaluator == "window" else "chapter"),
        ).model_dump(mode="json", by_alias=True)
    d.report.structured_output = raw
    await d.bind()
    before = deepcopy(raw)
    receipt = await d.produce()
    mandatory_unknown = required or evaluator == "commercial"
    assert receipt["draft_eligible"] is (not mandatory_unknown), receipt
    assert bool(receipt["unknown"]) is mandatory_unknown
    assert bool(receipt["optional_unknown"]) is (not mandatory_unknown)
    if evaluator == "commercial" and present:
        assert "exact_current_commercial_sources_required" in " ".join(receipt["unknown"])
        assert "commercial" not in receipt["gate_receipts"]
    else:
        availability = receipt["gate_receipts"][evaluator]
        assert availability["status"] == "unknown" and availability["required"] is required
        assert availability["quality_verdict_available"] is False
        assert availability["market_acceptance_claimed"] is False
        assert availability["financial_clearance_asserted"] is False
    assert receipt["quality_passed"] is False and d.report.structured_output == before
    assert (d.budget.calls_used, d.budget.tokens_charged) == (1, 30)


@pytest.mark.parametrize("case", [
    "optional", "required", "generic_stop", "missing_sources", "changed_catalog",
    "changed_source", "missing_transport", "changed_origin", "changed_cohort",
    "claimed_grade", "aggregate_quorum_changed", "paid_content_changed", "raw_message_changed",
    "empty_catalog", "zero_sample_cohort",
])
async def test_optional_commercial_availability_requires_complete_integrity_proof(admission_db, monkeypatch, case):
    d = admission_db
    sources, _ = await _commercial(d, monkeypatch,
        availability_finish="stop" if case == "generic_stop" else "content_filter")
    d.settings.pipeline.chapter_llm_commercial_judge_block_on_failure = case == "required"
    await d.bind()
    if case == "missing_sources":
        sources = None
    elif case == "changed_catalog":
        catalog = deepcopy(sources.catalog)
        catalog[next(iter(catalog))]["text"] += " invented"
        sources = replace(sources, catalog=catalog)
    elif case == "changed_source":
        receipt = dict(sources.receipt, source_sha256="0" * 64)
        sources = replace(sources, receipt=receipt)
    elif case == "empty_catalog":
        sources = replace(sources, catalog={})
    elif case in {"missing_transport", "changed_origin", "paid_content_changed", "raw_message_changed"}:
        run = await d.session.scalar(select(db.LlmRunModel).where(
            db.LlmRunModel.prompt_template == "chapter_commercial_quality_judge"))
        execution = run.metadata_json["execution_receipt"]
        root = await d.session.get(db.ExecutionOperationModel, execution["operation_key"])
        aid = execution["attempts"][0]["attempt_id"]
        transport = await d.session.get(db.ExecutionOperationModel, hashlib.sha256(
            f"transport-result.v1\n{d.budget.scope_id}\n{aid}".encode()).hexdigest())
        if case == "missing_transport":
            await d.session.delete(transport)
        else:
            paid = deepcopy(root.result_json)
            if case == "changed_origin":
                paid["metadata"]["paid_call_origin"]["prompt_hash"] = "0" * 64
            elif case == "paid_content_changed":
                paid["content"] = '{"pass": true}'
            else:
                paid["raw_message"] = {"role": "assistant", "content": "hidden quality claim"}
            root.result_json = paid
            transport.result_json = {**transport.result_json, "result": paid}
    elif case in {"changed_cohort", "claimed_grade", "aggregate_quorum_changed", "zero_sample_cohort"}:
        metadata = deepcopy(d.workflow.metadata_json)
        journal = next(iter(metadata["commercial_component_cohorts"].values()))
        if case == "zero_sample_cohort":
            journal["binding"]["samples"] = 0
        elif case == "changed_cohort":
            journal["aggregate"]["slots_sha256"] = "0" * 64
        else:
            aggregate = journal["aggregate"]["report"]
            if case == "claimed_grade":
                aggregate["overall_score"] = .99
            else:
                envelope = json.loads(aggregate["raw_excerpt"])
                envelope["required_valid_samples"] = 1
                aggregate["raw_excerpt"] = json.dumps(envelope)
            raw = deepcopy(d.report.structured_output)
            raw["evidence_summary"]["llm_commercial_judge_original"] = aggregate
            d.report.structured_output = raw
            d.domain.evidence_summary["llm_commercial_judge_original"] = aggregate
        d.workflow.metadata_json = metadata
    await d.bind()

    async def snapshot():
        state = {}
        for model in (db.ExecutionOperationModel, db.ExecutionAttemptModel, db.ExecutionBudgetModel,
                      db.LlmRunModel, db.ReviewReportModel, db.QualityScoreModel):
            rows = list(await d.session.scalars(select(model)))
            state[model.__name__] = sorted([
                {c.key: str(getattr(row, c.key)) for c in model.__mapper__.column_attrs} for row in rows
            ], key=lambda row: json.dumps(row, sort_keys=True))
        return state

    before, history = await snapshot(), deepcopy(d.workflow.metadata_json)
    receipt = await d.produce(commercial_sources=sources)
    assert receipt["quality_passed"] is False
    assert receipt["scores_modified"] is False and receipt["verdict_modified"] is False
    if case in {"optional", "required"}:
        assert receipt["draft_eligible"] is (case == "optional"), receipt
        assert bool(receipt["unknown"]) is (case == "required")
        assert bool(receipt["optional_unknown"]) is (case == "optional")
        availability = receipt["gate_receipts"]["commercial"]
        assert availability["status"] == "unknown"
        assert availability["source_integrity_verified"] is True
        assert availability["quality_verdict_available"] is False
        assert len(availability["verified_unavailable_slots"]) == 3
        assert availability["quorum_policy_unchanged"] is True
        assert not availability["market_acceptance_claimed"]
        assert not availability["financial_clearance_asserted"]
    else:
        assert not receipt["draft_eligible"] and receipt["optional_unknown"] == [], receipt
        assert any(error.startswith("commercial:") for error in receipt["unknown"]), receipt
    assert await snapshot() == before
    assert d.workflow.metadata_json == history


async def test_window_before_actual_minimum_is_positively_not_applicable(admission_db):
    d = admission_db
    d.settings.pipeline.enable_chapter_window_llm_judge = True
    d.settings.pipeline.chapter_window_llm_judge_block_on_failure = True
    d.settings.pipeline.chapter_window_llm_judge_min_chapters = 2
    await d.bind()
    receipt = await d.produce()
    assert receipt["draft_eligible"] and not receipt["unknown"], receipt
    assert receipt["gate_receipts"]["window"]["status"] == "not_applicable"


@pytest.mark.parametrize("required", [False, True])
async def test_applicable_missing_window_uses_actual_required_setting(admission_db, required):
    d = admission_db
    d.settings.pipeline.enable_chapter_window_llm_judge = True
    d.settings.pipeline.chapter_window_llm_judge_block_on_failure = required
    d.settings.pipeline.chapter_window_llm_judge_min_chapters = 1
    await d.bind()
    receipt = await d.produce()
    assert receipt["draft_eligible"] is (not required), receipt
    assert receipt["gate_receipts"]["window"]["status"] == "unknown"
    assert bool(receipt["unknown"]) is required
    assert bool(receipt["optional_unknown"]) is (not required)


async def test_enabled_but_skipped_actual_body_gate_is_unknown(admission_db):
    d = admission_db
    from bestseller.services.quality_report_claim import graded_text_fingerprint
    d.cfg = replace(d.cfg, l6_enabled=True)
    d.project.invariants_json = invariants_to_dict(ProjectInvariants(project_id=d.project.id,
        language="en", length_envelope=LengthEnvelope(20, 100, 200)))
    await d.bind()
    receipt = await d.produce(body_gate_receipt=reviews.RewriteQualityGateReceipt(
        None, "skipped-evaluation", graded_text_fingerprint(BODY), None))
    assert not receipt["draft_eligible"]
    assert any("current_body_gate_evaluation_unavailable" in v for v in receipt["unknown"]), receipt


async def test_primary_unknown_is_mandatory_even_when_commercial_optional(admission_db):
    d = admission_db
    d.settings.pipeline.enable_chapter_llm_commercial_judge = True
    d.settings.pipeline.chapter_llm_commercial_judge_block_on_failure = False
    d.report.structured_output = {**d.report.structured_output, "critic_evidence": {"status": "unavailable"}}
    await d.bind()
    receipt = await d.produce()
    assert not receipt["draft_eligible"] and receipt["unknown"], receipt


async def test_optional_cannot_waive_old_commercial_execution_scope(admission_db, monkeypatch):
    d = admission_db
    from bestseller.services import commercial_judge_recovery as recovery
    sources, _ = await _commercial(d, monkeypatch)
    d.settings.pipeline.chapter_llm_commercial_judge_block_on_failure = False
    meta = deepcopy(d.workflow.metadata_json)
    key, journal = next(iter(meta["commercial_component_cohorts"].items()))
    journal["binding"]["scope_id"] = "project:original-unknown-scope"
    d.workflow.metadata_json = meta
    chapter_meta = deepcopy(d.chapter.metadata_json)
    chapter_meta["commercial_component_cohorts"][key]["binding_sha256"] = recovery._digest(journal["binding"])
    d.chapter.metadata_json = chapter_meta
    await d.bind()
    receipt = await d.produce(commercial_sources=sources)
    assert not receipt["draft_eligible"] and any(v.startswith("commercial:") for v in receipt["unknown"]), receipt
    assert receipt["optional_unknown"] == []

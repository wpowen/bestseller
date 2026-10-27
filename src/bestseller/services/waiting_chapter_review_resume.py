"""Explicit prerequisite rearm of the SAME durable critic slot; no model calls.

The normal commercial renderer prefix is mirrored literally and AST-fenced to
its producer. Its capture stops before slot preparation; it cannot dispatch.
The service reruns normal history/orphan authorization, never trusts a boolean.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202, ANN401, F841
import ast
from copy import deepcopy
from datetime import datetime
import hashlib
import inspect
import json
from uuid import UUID
from typing import Any
from sqlalchemy import select
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as commercial
from bestseller.services import pipelines
from bestseller.services.budget import (
    bind_execution_scope,
    current_execution_scope,
    execution_scope_block_code,
)
from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
from bestseller.services.chapter_review_prerequisite import WAITING
from bestseller.services.retained_review_input_proof import _ReadOnlySession
from bestseller.infra.db.models import (
    ProjectModel,
    ChapterModel,
    ChapterDraftVersionModel,
    ReviewReportModel,
    QualityScoreModel,
    WorkflowRunModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
    SceneCardModel,
)

POLICY = "waiting-chapter-review-resume.v1"
from bestseller.services.chapter_llm_quality_judge import (
    GENERIC_CORPUS_KEY,
    LLMCompletionRequest,
    _commercial_effective_critic_configuration,
    _commercial_source_projection,
    _critic_judge_max_tokens,
    _load_reference_corpus,
    _neutral_genre_context,
    _render_chapter_judge_system_prompt,
    chapter_commercial_thresholds,
    get_judge_rubric,
    render_methodology_applicability,
    resolve_commercial_judge_model_key,
    resolve_llm_role_model,
)


async def _render_current_inputs(session, settings, *, chapter_number, content_md, generation_input=None, previous_chapters=(), workflow_run_id=None, project_id=None, pack=None, reference_corpus_key=None, genre_context=None, language='zh', sample_slot=None, commercial_retry_context=None, chapter_id=None, draft_id=None, commercial_cohort_samples=1, commercial_retained_only=False):
    generation_input = _commercial_source_projection(generation_input, chapter_number=chapter_number, content_md=content_md, previous_chapters=previous_chapters)
    if genre_context is None:
        genre_context = _neutral_genre_context()
    corpus_key = reference_corpus_key or genre_context.corpus_key or GENERIC_CORPUS_KEY
    corpus = _load_reference_corpus(corpus_key)
    writer_model = resolve_llm_role_model(settings, role='writer')
    min_overall, min_dimensions = chapter_commercial_thresholds(chapter_number, corpus, writer_model)
    from bestseller.services.commercial_source_evidence import assemble_commercial_sources, commercial_judge_unavailable, commercial_output_token_budget
    try:
        sources = assemble_commercial_sources(chapter_number=chapter_number, content_md=content_md, source=generation_input, previous_chapters=previous_chapters)
    except (ValueError, TypeError) as exc:
        return commercial_judge_unavailable(diagnostic=f'commercial source assembly unavailable: {exc}')
    fallback = json.dumps({'judge_unavailable': True}, ensure_ascii=False)
    methodology_section = render_methodology_applicability(language=language, phase='judge')
    reference_block = ''
    checklist_block = ''
    calibration_block = ''
    rubric = get_judge_rubric('chapter_commercial')
    rule_ids = [key for key, entry in sources.catalog.items() if entry['kind'] == 'constraint']
    rule_checks_prompt = '\nchecks 必须恰以这些真实 RULE IDs 为键：' + json.dumps(rule_ids, ensure_ascii=False) + "；每项仅 {result:'PASS'或'FAIL',body_id,reason}。body_id 是与该条款有关的真实当前段落ID；FAIL 的 reason 给出明确语义违例；服务端验证引文后派生缺失的 critical 问题，不必在列表重复。" if rule_ids else '\n当前无适用 RULE；不要返回 checks 或 binary_checklist。'
    system_prompt = _render_chapter_judge_system_prompt(rubric=rubric, reference_block=reference_block, checklist_block=checklist_block, calibration_block=calibration_block, genre_context=genre_context, language=language)
    user_prompt = f'章节：第{chapter_number}章\n通过阈值：overall >= {min_overall:.2f}；关键维度：{json.dumps(min_dimensions, ensure_ascii=False)}\n评测维度：opening_pull, readability, commercial_pull, character_agency, decision_intelligence, character_voice_distinction, scene_execution, continuity, methodology_compliance, hook_strength, knowledge_boundary, real_world_plausibility, object_signal_logic, call_plausibility, capability_demonstrated, material_advancement_score。\n\n核查当前来源条款与实际事件的因果、身份和认知边界。计划和规则评审快照是待核实输入，不是已发生事实；没有来源的技巧要求不得补造。\n\n必须返回 commercial-review-wire.v2 的新格式；字段 wire_version,pass,overall_score,dimension_scores,issues,rewrite_plan。\n每个 issue 必须使用系统所列类型化来源证据协议，选择完整来源目录中的真实ID，原文由服务端取回，不要复写引文。\n{methodology_section}{rule_checks_prompt}\n完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n{sources.text}'
    from bestseller.services.commercial_review_tool import review_submission_tools, review_evaluation_contract
    tools = review_submission_tools(sources)
    output_budget = commercial_output_token_budget(_critic_judge_max_tokens(settings))
    evaluation_contract = review_evaluation_contract(settings=settings, sources=sources, system_prompt=system_prompt, instruction_prefix=user_prompt.split(sources.text, 1)[0], tools=tools, min_overall=min_overall, min_dimensions=min_dimensions, model_catalog_key=resolve_commercial_judge_model_key(settings), output_tokens=output_budget)
    from bestseller.services.commercial_review_transport import CommercialReviewTransportError, prepare_commercial_review_submission
    logical_user_prompt = user_prompt
    try:
        user_prompt, tools, source_transport = prepare_commercial_review_submission(system_prompt, logical_user_prompt, sources=sources, tools=tools, output_tokens=output_budget)
    except CommercialReviewTransportError as exc:
        return commercial_judge_unavailable(diagnostic='complete commercial source input exceeds admission budget; no paid judge call', raw=json.dumps(exc.receipt, ensure_ascii=False))
    if 'tool_schema_transport' in source_transport:
        evaluation_contract = review_evaluation_contract(settings=settings, sources=sources, system_prompt=system_prompt, instruction_prefix=logical_user_prompt.split(sources.text, 1)[0], tools=tools, min_overall=min_overall, min_dimensions=min_dimensions, model_catalog_key=resolve_commercial_judge_model_key(settings), output_tokens=output_budget)
    if commercial_retained_only and (chapter_id is None or draft_id is None):
        return commercial_judge_unavailable(diagnostic='commercial retained probe requires exact chapter/draft binding')
    component_call = None
    effective_critic = None
    prepared_input_hash = None
    if chapter_id is not None or draft_id is not None:
        try:
            effective_critic = await _commercial_effective_critic_configuration(session, settings, project_id)
            from bestseller.services.llm import _effective_input_hash
            from bestseller.settings import LLMRoleSettings
            prepared_input_hash = _effective_input_hash(LLMCompletionRequest(logical_role='critic', system_prompt=system_prompt, user_prompt=user_prompt, fallback_response=fallback, tools=tools, tool_choice='required', max_tokens_override=output_budget), LLMRoleSettings.model_validate(effective_critic))
            return {'schema': 'waiting-commercial-render.v1', 'sources': sources, 'evaluation_contract': evaluation_contract, 'effective_critic': effective_critic, 'current_input_hash': prepared_input_hash, 'tools': tools, 'system': system_prompt, 'user': user_prompt, 'output_tokens': output_budget, 'source_transport': source_transport}
        except (ValueError, TypeError, KeyError) as exc:
            return commercial_judge_unavailable(diagnostic=f'commercial component recovery unavailable: {exc}')


def _captured_renderer(tree):
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "judge_chapter_commercial_quality"
    )
    fn = deepcopy(fn)
    outer = next(
        n
        for n in fn.body
        if isinstance(n, ast.If)
        and ast.unparse(n.test) == "chapter_id is not None or draft_id is not None"
    )
    branch = next(n for n in outer.body if isinstance(n, ast.Try))
    index = next(
        i
        for i, n in enumerate(branch.body)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Await)
        and isinstance(n.value.value, ast.Call)
        and isinstance(n.value.value.func, ast.Name)
        and n.value.value.func.id == "prepare_commercial_component_slot"
    )
    branch.body = (
        branch.body[:index]
        + ast.parse(
            'return {"schema":"waiting-commercial-render.v1","sources":sources,"evaluation_contract":evaluation_contract,"effective_critic":effective_critic,"current_input_hash":prepared_input_hash,"tools":tools,"system":system_prompt,"user":user_prompt,"output_tokens":output_budget,"source_transport":source_transport}'
        ).body
    )
    outer.body = outer.body[: outer.body.index(branch) + 1]
    fn.body = fn.body[: fn.body.index(outer) + 1]
    fn.name = "_render_current_inputs"
    fn.decorator_list = []
    fn.returns = None
    for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
        a.annotation = None
    loaded = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}

    class Clean(ast.NodeTransformer):
        def visit_ImportFrom(self, n):
            n.names = [a for a in n.names if (a.asname or a.name) in loaded]
            return n if n.names else None

    fn = Clean().visit(fn)
    return fn


def _need(value, code):
    if not value:
        raise ValueError("waiting_chapter_review_resume_" + code)


def _audit_scalar(value):
    # Normal runtime authorization carries UUID row identities in session.info.
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError("unsupported prerequisite audit value")


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_audit_scalar,
    )


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _row(row):
    from sqlalchemy import inspect as orm_inspect

    # SQL row datetimes/UUIDs are receipt strings, never new creative input.
    values = {c.key: deepcopy(getattr(row, c.key)) for c in orm_inspect(type(row)).column_attrs}
    return json.loads(json.dumps(values, default=str, ensure_ascii=False, allow_nan=False))


def _renderer_fence():
    normal = ast.parse(inspect.getsource(judge.judge_chapter_commercial_quality))
    expected = _captured_renderer(normal)
    actual = ast.parse(inspect.getsource(_render_current_inputs)).body[0]
    _need(
        ast.dump(expected, include_attributes=False) == ast.dump(actual, include_attributes=False),
        "normal_commercial_renderer_changed",
    )
    return hashlib.sha256(
        inspect.getsource(judge.judge_chapter_commercial_quality).encode()
    ).hexdigest()


class _Reader(_ReadOnlySession):
    def __init__(self, session):
        super().__init__(session)
        # No ambient operator-preparation flag can manufacture authorization.
        self.info = {}


async def _current_commercial_prerequisite(
    reader, settings, *, project, chapter, draft, scope, key, pointer, rendered
):
    """Read an existing cohort, including charged abstentions, without rebuilding it."""
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
    from bestseller.services.commercial_action_projection import project_commercial_actions
    from bestseller.services.quality_gates_config import get_quality_gates_config
    from bestseller.services.retained_review_input_proof import _commercial_quarantine_proofs

    owner = await reader.get(WorkflowRunModel, UUID(pointer["workflow_run_id"]))
    _need(
        owner is not None
        and owner.project_id == project.id
        and owner.scope_type == "chapter"
        and owner.scope_id == chapter.id,
        "commercial_current_owner_changed",
    )
    journal = ((owner.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)
    _need(
        isinstance(journal, dict) and journal.get("schema") == commercial.COHORT_SCHEMA,
        "commercial_current_journal_required",
    )
    binding = journal["binding"]
    sources = rendered["sources"]
    n = judge._judge_samples_count()
    _need(
        pointer.get("binding_sha256") == commercial._digest(binding)
        and binding.get("project_id") == str(project.id)
        and binding.get("chapter_id") == str(chapter.id)
        and binding.get("draft_id") == str(draft.id)
        and binding.get("scope_id") == scope
        and binding.get("samples") == n
        and binding.get("source_binding")
        == {k: sources.receipt[k] for k in commercial._BINDING_KEYS}
        and binding.get("evaluation_contract") == rendered["evaluation_contract"]
        and binding.get("effective_critic_sha256")
        == commercial._digest(rendered["effective_critic"]),
        "commercial_current_binding_changed",
    )
    alias_view = await commercial._verified_source_alias_cohort_view(
        reader, settings, journal=journal, sources=sources,
        project_id=project.id, scope=scope, expected_model=rendered["effective_critic"]["model"],
    )
    if alias_view is not None:
        journal = alias_view
    slots = journal["slots"]
    _need(
        set(slots) == {f"chapter-quality.independent-v2:{i}" for i in range(n)}
        and all(e.get("state") in {"completed", "quarantined"} for e in slots.values()),
        "commercial_current_slots_incomplete",
    )
    aggregate = journal.get("aggregate") or {}
    _need(
        aggregate.get("slots_sha256")
        == commercial._digest({s: e["report"] for s, e in slots.items()}),
        "commercial_current_aggregate_changed",
    )
    result = LLMQualityJudgeResult.model_validate(aggregate.get("report"))
    _need(not result.infrastructure_failure, "retained_commercial_still_unavailable")
    quarantined = {s for s, e in slots.items() if e["state"] == "quarantined"}
    proofs = (
        await _commercial_quarantine_proofs(
            reader,
            project=project,
            chapter=chapter,
            owner=owner,
            scope=scope,
            content_md=draft.content_md,
            result=result,
            sources=sources,
        )
        if quarantined
        else {}
    )
    _need(set(proofs) == quarantined, "commercial_current_quarantine_changed")
    runs, roots = [], []
    for slot, entry in slots.items():
        if slot in quarantined:
            continue
        _, envelope, run = await commercial._verified_paid_slot(
            reader,
            report=entry["report"],
            sources=sources,
            contract=binding["evaluation_contract"],
            project_id=project.id,
            scope=scope,
            slot=slot,
            expected_model=rendered["effective_critic"]["model"],
            settings=settings,
        )
        _need(
            type(entry.get("used_attempts")) is int
            and entry["used_attempts"] == envelope["attempt"],
            "commercial_current_round_changed",
        )
        runs.append(str(run.id))
        roots.append(run.metadata_json["execution_receipt"]["operation_key"])
    paid = n - len(quarantined)
    _need(
        len(set(runs)) == len(set(roots)) == paid, "commercial_current_paid_slots_not_independent"
    )
    view, receipt = project_commercial_actions(
        result,
        sources=sources,
        gate_config=get_quality_gates_config().l6_gate,
        chapter_number=chapter.chapter_number,
        verified_quarantine_slots=proofs or None,
    )
    _need(
        view is not None and receipt.get("status") == "projected",
        "commercial_current_projection_unverified",
    )
    return {
        "aggregate_sha256": commercial._digest(aggregate["report"]),
        "slots_sha256": aggregate["slots_sha256"],
        "paid_run_ids": sorted(runs),
        "quarantined_slots": {
            s: {"proof_sha256": commercial._digest(p["proof"]), "abstains": True}
            for s, p in proofs.items()
        },
    }


async def _normal_commercial_prerequisite(
    session, settings, *, project, chapter, draft, owner, scope
):
    from bestseller.services.chapter_generation_input_builder import (
        build_chapter_generation_input_bundle,
    )
    from bestseller.services.commercial_review_context import (
        load_authorized_commercial_history as load_commercial_review_history,
    )
    from bestseller.services.context import build_chapter_writer_context
    from bestseller.services.creation_contract_authority import select_creation_contracts
    from bestseller.services.judge_genre_context import resolve_judge_genre_context
    from bestseller.services.llm import _effective_input_hash
    from bestseller.services.planner import _source_bound_cast_enabled
    from bestseller.services.prompt_packs import resolve_prompt_pack
    from bestseller.services.retained_review_input_proof import _fresh_catalog, _fresh_role

    renderer_sha = _renderer_fence()
    reader = _Reader(session)
    context = await build_chapter_writer_context(
        reader, settings, project.slug, chapter.chapter_number
    )
    scenes = list(
        await reader.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.project_id == project.id, SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number)
        )
    )
    generation = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=scenes,
        context_packet=context,
        target_word_count=int(chapter.target_word_count or draft.word_count),
    ).model_dump(mode="json")
    generation["approved_creation_contracts"] = select_creation_contracts(
        project.metadata_json or {}, source_bound=_source_bound_cast_enabled(project)
    )
    history = await load_commercial_review_history(
        reader, project_id=project.id, chapter_number=chapter.chapter_number
    )
    previous = history.get("recent_reference_prose") or history.get("recent_adopted_prose") or []
    generation[
        "draft_manuscript_continuity"
        if history.get("qualified_pending_sources")
        else "adopted_continuity"
    ] = {k: v for k, v in history.items() if k != "recent_adopted_prose"}
    selected = judge._commercial_source_projection(
        generation,
        chapter_number=chapter.chapter_number,
        content_md=draft.content_md,
        previous_chapters=previous,
    )
    genre = resolve_judge_genre_context(
        genre=project.genre, sub_genre=project.sub_genre, story_bible=selected.get("story_bible")
    )
    metadata = project.metadata_json or {}
    pack = resolve_prompt_pack(
        metadata.get("prompt_pack_name") or metadata.get("prompt_pack_key"),
        genre=project.genre or "general-fiction",
        sub_genre=project.sub_genre,
    )
    n = judge._judge_samples_count()
    _need(n == 3, "stable_three_slot_probe_required")
    rendered = await _render_current_inputs(
        reader,
        settings,
        chapter_number=chapter.chapter_number,
        content_md=draft.content_md,
        generation_input=generation,
        previous_chapters=previous,
        workflow_run_id=owner.id,
        project_id=project.id,
        chapter_id=chapter.id,
        draft_id=draft.id,
        pack=pack,
        genre_context=genre,
        sample_slot="chapter-quality.independent-v2:0",
        commercial_cohort_samples=n,
    )
    _need(
        type(rendered) is dict and rendered.get("schema") == "waiting-commercial-render.v1",
        "commercial_admission_unavailable",
    )
    sources = rendered["sources"]
    request = LLMCompletionRequest(
        logical_role="critic",
        model_tier="strong",
        model_catalog_key=resolve_commercial_judge_model_key(settings),
        system_prompt=rendered["system"],
        user_prompt=rendered["user"],
        fallback_response="proof-only",
        tools=rendered["tools"],
        tool_choice="required",
        max_tokens_override=rendered["output_tokens"],
    )
    catalog, catalog_sha = _fresh_catalog()
    fresh = _fresh_role(settings, project, request, catalog)
    _need(
        _effective_input_hash(request, fresh) == rendered["current_input_hash"],
        "effective_route_or_input_changed",
    )
    results = []
    for _ in range(2):
        current = await reader.get(ChapterModel, chapter.id)
        key = commercial._digest(
            {
                "project_id": str(project.id),
                "chapter_id": str(chapter.id),
                "body_sha256": sources.receipt["body_sha256"],
            }
        )
        pointer = ((current.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)
        if pointer is not None:
            retained = await _current_commercial_prerequisite(
                reader,
                settings,
                project=project,
                chapter=current,
                draft=draft,
                scope=scope,
                key=key,
                pointer=pointer,
                rendered=rendered,
            )
            results.append(_sha(retained))
            continue
        retained = await commercial._historical_commercial_cohort(
            reader,
            project_id=project.id,
            chapter_id=chapter.id,
            draft_id=draft.id,
            body_hash=sources.receipt["body_sha256"],
            content_md=draft.content_md,
            sources=sources,
            contract=rendered["evaluation_contract"],
            scope=scope,
            n=n,
            expected_model=rendered["effective_critic"]["model"],
            current_input_hash=rendered["current_input_hash"],
            effective_critic=rendered["effective_critic"],
            settings=settings,
        )
        # A retained unknown/exhausted cohort is not repaired infrastructure.
        if retained is not None:
            from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

            _need(
                not LLMQualityJudgeResult.model_validate(
                    (retained["source_alias_view"]["aggregate"]["report"]
                     if retained.get("source_alias_view") is not None else retained["aggregate"])
                ).infrastructure_failure,
                "retained_commercial_still_unavailable",
            )
        results.append(_sha(retained))
    _need(results[0] == results[1], "commercial_history_changed_during_probe")
    return {
        "schema": "waiting-commercial-prerequisite.v1",
        "draft_id": str(draft.id),
        "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        "source_receipt": deepcopy(sources.receipt),
        "evaluation_contract": rendered["evaluation_contract"],
        "prepared_effective_input_hash": rendered["current_input_hash"],
        "effective_critic": rendered["effective_critic"],
        "normal_renderer_sha256": renderer_sha,
        "fresh_catalog_sha256": catalog_sha,
        "history_result_sha256": results[0],
        "history_replays": 2,
        "current_protocol_eligible": True,
        "slots_prepared": 0,
        "attempts_granted": 0,
        "provider_calls": 0,
        "legacy_authorization_sha256": _sha(reader.info),
    }


def _external_probe(proof, actual):
    _need(
        type(proof) is dict
        and proof.get("status") == "completed"
        and proof.get("current_protocol_eligible") is True
        and proof.get("protected_rows_and_ledger_unchanged") is True
        and proof.get("history_replays") == 2
        and all(
            type(proof.get(k)) is int and proof[k] == 0
            for k in (
                "provider_calls",
                "blocked_gateway_attempts",
                "data_updates",
                "slots_prepared",
                "attempts_granted",
            )
        ),
        "normal_history_probe_receipt_required",
    )
    for key in (
        "draft_id",
        "body_sha256",
        "source_receipt",
        "evaluation_contract",
        "prepared_effective_input_hash",
    ):
        _need(proof.get(key) == actual[key], "prerequisite_probe_binding_changed")
    return _sha(proof)


async def _ledger(session, scope, *, lock=False):
    budget = await session.scalar(
        select(ExecutionBudgetModel)
        .where(ExecutionBudgetModel.scope_id == scope)
        .execution_options(populate_existing=True)
        .with_for_update()
        if lock
        else select(ExecutionBudgetModel)
        .where(ExecutionBudgetModel.scope_id == scope)
        .execution_options(populate_existing=True)
    )
    _need(budget and execution_scope_block_code(budget) is None, "scope_not_open")
    ops = list(
        await session.scalars(
            select(ExecutionOperationModel)
            .where(ExecutionOperationModel.scope_id == scope)
            .execution_options(populate_existing=True)
        )
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel)
            .where(ExecutionAttemptModel.scope_id == scope)
            .execution_options(populate_existing=True)
        )
    )
    from bestseller.services.execution_control_purpose import verify_non_dispatch_control

    closed = {
        "completed", "stopped", "transport_result", "transport_failure_receipt",
        "reconciliation_receipt", "unsent_reclaim_receipt",
    }
    for row in ops:
        if row.status in closed:
            continue
        proof = await verify_non_dispatch_control(
            session, operation=row, operations=ops, attempts=attempts,
        )
        _need(proof is not None, "unsettled_operations")
    _need(
        all(
            row.status in {"completed", "not_sent", "rejected", "reconciled_charged"}
            for row in attempts
        ),
        "unsettled_attempts",
    )
    by_key = {row.key: row for row in ops}
    _need(all(a.operation_key in by_key for a in attempts), "orphan_financial_attempt")
    return {
        "budget": _row(budget),
        "operations": {r.key: _sha(_row(r)) for r in ops},
        "attempts": {r.id: _sha(_row(r)) for r in attempts},
    }


async def _inspect_waiting(session, settings, args, *, lock=False):
    from bestseller.services.retry_ledger import project_execution_scope

    project = await session.scalar(
        select(ProjectModel)
        .where(ProjectModel.id == args["project_id"])
        .execution_options(populate_existing=True)
    )
    chapter = await session.scalar(
        select(ChapterModel)
        .where(ChapterModel.project_id == args["project_id"], ChapterModel.id == args["chapter_id"])
        .execution_options(populate_existing=True)
        .with_for_update()
        if lock
        else select(ChapterModel)
        .where(ChapterModel.project_id == args["project_id"], ChapterModel.id == args["chapter_id"])
        .execution_options(populate_existing=True)
    )
    _need(project and chapter, "project_or_chapter_missing")
    scope = project_execution_scope(project)
    _need(current_execution_scope() in {None, scope}, "execution_scope_mismatch")
    proof = await validate_retained_review_before_processing(session, settings, chapter)
    _need(proof is not None, "existing_current_cycle_required")
    cid = proof["cycle_id"]
    cycle = deepcopy(chapter.metadata_json["chapter_critic_cycles"][cid])
    draft = await session.get(
        ChapterDraftVersionModel, UUID(proof["draft_id"]), populate_existing=True
    )
    _need(
        draft
        and draft.project_id == project.id
        and draft.chapter_id == chapter.id
        and draft.is_current,
        "source_changed",
    )
    from bestseller.services.feedback import validate_adopted_knowledge_source

    _, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md or "",
        lock=lock,
    )
    if rejection is None:
        raise ValueError("chapter_review_recovery_source_already_adopted")
    if rejection != "source_not_adopted":
        raise ValueError("chapter_review_recovery_source_mismatch")
    owner = await session.get(
        WorkflowRunModel, UUID(cycle["review_workflow_run_id"]), populate_existing=True
    )
    receipts = cycle.get("infrastructure_resumptions") or []
    _need(type(receipts) is list, "resume_history_invalid")
    _need(cycle["attempts"], "existing_attempt_required")
    _need(all(type(r) is dict for r in receipts), "resume_history_invalid")
    entry = cycle["attempts"][-1]
    matches = [r for r in receipts if r.get("attempt") == entry["attempt"]]
    _need(len(matches) <= 1, "duplicate_resume_receipts")
    ledger = await _ledger(session, scope, lock=lock)
    if matches:
        previous = matches[0]
        original_plan = deepcopy(previous)
        original_plan.pop("state_after", None)
        claimed_hash = original_plan.pop("plan_sha256", None)
        original_plan["status"] = "prepared_plan"
        original_plan["database_writes"] = 0
        _need(claimed_hash == _sha(original_plan), "resume_receipt_integrity_invalid")
        _need(
            previous.get("schema") == POLICY
            and previous.get("binding")
            == {
                key: proof[key]
                for key in (
                    "project_id",
                    "chapter_id",
                    "draft_id",
                    "body_sha256",
                    "cycle_id",
                    "policy_source_hash",
                    "semantic_dependencies_sha256",
                )
            }
            and previous.get("operation_namespace") == entry["operation_namespace"]
            and previous.get("review_workflow_run_id") == str(owner.id)
            and previous.get("new_attempts") == 0
            and previous.get("credit_released") is False,
            "resume_receipt_binding_invalid",
        )
        return (
            chapter,
            cycle,
            entry,
            {
                **deepcopy(previous),
                "status": "already_rearmed",
                "current_cycle_state": cycle["state"],
                "database_writes": 0,
            },
            True,
        )
    _need(
        proof.get("reuse_kind") == WAITING and cycle["state"] == entry["state"] == WAITING,
        "waiting_cycle_required",
    )
    _need(
        owner.status == "machine_blocked" and owner.current_step == WAITING,
        "original_owner_not_waiting_terminal",
    )
    busy = list(
        await session.scalars(
            select(WorkflowRunModel.id).where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(("running", "pending", "queued")),
                (
                    (WorkflowRunModel.scope_type == "project")
                    | (
                        (WorkflowRunModel.scope_type == "chapter")
                        & (WorkflowRunModel.scope_id == chapter.id)
                    )
                ),
            )
        )
    )
    _need(not busy, "active_writer_or_review_workflow")
    records = entry["infrastructure_history"]
    _need(
        records[-1]["waiting_receipt"]["reason"] == "required_judge_evidence_unavailable",
        "unsupported_prerequisite_reason",
    )
    with bind_execution_scope(scope):
        actual = await _normal_commercial_prerequisite(
            session,
            settings,
            project=project,
            chapter=chapter,
            draft=draft,
            owner=owner,
            scope=scope,
        )
    external_sha = (_external_probe(args["prerequisite_proof"], actual)
                    if args.get("prerequisite_proof") is not None else None)
    reports = []
    for record in records:
        report = await session.get(
            ReviewReportModel, UUID(record["review_report_id"]), populate_existing=True
        )
        quality = await session.get(
            QualityScoreModel, UUID(record["quality_score_id"]), populate_existing=True
        )
        reports.append({"report": _row(report), "quality": _row(quality)})
    result = {
        "schema": POLICY,
        "status": "prepared_plan",
        "binding": {
            key: proof[key]
            for key in (
                "project_id",
                "chapter_id",
                "draft_id",
                "body_sha256",
                "cycle_id",
                "policy_source_hash",
                "semantic_dependencies_sha256",
            )
        },
        "review_workflow_run_id": str(owner.id),
        "attempt": entry["attempt"],
        "operation_namespace": entry["operation_namespace"],
        "attempt_count": len(cycle["attempts"]),
        "new_attempts": 0,
        "credit_released": False,
        "provider_calls": 0,
        "dispatch_authorized": False,
        "adoption_authorized": False,
        "cycle_before": deepcopy(cycle),
        "owner_before": _row(owner),
        "waiting_results": reports,
        "ledger_before": ledger,
        "prerequisite": actual,
        "external_prerequisite_proof_sha256": external_sha,
        "requested_by": args["requested_by"],
        "database_writes": 0,
    }
    result["plan_sha256"] = _sha(result)
    return chapter, cycle, entry, result, False


async def resume_waiting_chapter_review(
    session,
    settings,
    *,
    project_id: UUID,
    chapter_id: UUID,
    requested_by: str,
    prerequisite_proof: dict | None = None,
    execute: bool = False,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Prepare ONLY; the caller commits and subsequently runs the normal pipeline."""
    _need(
        type(execute) is bool
        and type(requested_by) is str
        and 0 < len(requested_by.strip()) <= 64
        and (prerequisite_proof is None or type(prerequisite_proof) is dict),
        "arguments",
    )
    _need(
        not session.new
        and not session.dirty
        and not session.deleted
        and not session.in_nested_transaction()
        and session.sync_session._trans_context_manager is None,
        "clean_session_required",
    )
    args = dict(
        project_id=project_id,
        chapter_id=chapter_id,
        requested_by=requested_by,
        prerequisite_proof=prerequisite_proof,
    )
    with session.no_autoflush:
        chapter, cycle, entry, plan, replay = await _inspect_waiting(
            session, settings, args, lock=execute
        )
        if replay or not execute:
            return plan
        _need(
            type(expected_plan_sha256) is str and expected_plan_sha256 == plan["plan_sha256"],
            "plan_changed",
        )
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        if not await connection.run_sync(lambda c: c.connection.driver_connection.in_transaction):
            await connection.exec_driver_sql("BEGIN")
    async with session.begin_nested():
        with session.no_autoflush:
            fresh = await _inspect_waiting(session, settings, args, lock=True)
        _need(fresh[3] == plan and not fresh[4], "source_or_prerequisite_changed")
        chapter, cycle, entry, _, _ = fresh
        entry["state"] = "prepared"
        cycle["state"] = "prepared"
        receipt = {**deepcopy(plan), "status": "rearmed", "state_after": "prepared"}
        cycle["infrastructure_resumptions"] = [
            *(cycle.get("infrastructure_resumptions") or []),
            receipt,
        ]
        pipelines._store_chapter_critic_cycle(chapter, cycle)
        await session.flush()
    return {**receipt, "database_writes": 1}


async def rearm_waiting_review_native(session, settings, *, project_id, chapter_id, requested_by):
    """Normal RUN internal proof; no external probe/hash/source authority.

    Caller holds the source/control transaction and owns its commit/rollback.
    Each actual inspection performs the native two-history-read renderer path;
    the second locked inspection must reproduce the first complete plan.
    """
    args = dict(project_id=project_id, chapter_id=chapter_id,
                requested_by=requested_by, prerequisite_proof=None)
    with session.no_autoflush:
        first = await _inspect_waiting(session, settings, args, lock=True)
        if first[4]:
            return first[3]
        fresh = await _inspect_waiting(session, settings, args, lock=True)
    _need(fresh[3] == first[3] and not fresh[4], "source_or_prerequisite_changed")
    chapter, cycle, entry, plan, _ = fresh
    entry["state"] = "prepared"
    cycle["state"] = "prepared"
    receipt = {**deepcopy(plan), "status": "rearmed", "state_after": "prepared"}
    cycle["infrastructure_resumptions"] = [*(cycle.get("infrastructure_resumptions") or []), receipt]
    pipelines._store_chapter_critic_cycle(chapter, cycle)
    await session.flush()
    return {**receipt, "database_writes": 1}

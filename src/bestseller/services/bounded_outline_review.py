"""Two-stage review using existing durable one-send grants; no authority minting.

This runner never applies a candidate or approves writing. An existing exact
operator approval is mandatory before any grant registration/reservation.
"""
from copy import deepcopy
import hashlib
import json
from uuid import UUID
from pathlib import Path

from bestseller.services import llm, outline_llm_judge as judge
from bestseller.services.budget import ExecutionLedger, bind_execution_scope
from bestseller.services.single_dispatch_authorization import authorization_id, register_grant, no_retry_settings
from bestseller.services.prompt_source_policy import content_hash

PHASES = {'outline_commercial_judge': 'initial',
          'outline_commercial_judge_consistency_adjudication': 'consistency'}
PRIMARY_RESULT_UTF8_MAX = 16384
MAX_BLOCKERS = 16


def _need(ok, reason):
    if not ok:
        raise ValueError('bounded_outline_' + reason)


def _decode(raw):
    from bestseller.services.planning_admission_submission import _unique, _nonfinite
    _need(isinstance(raw, str) and bool(raw), 'empty_response')
    _need(len(raw.encode('utf-8')) <= PRIMARY_RESULT_UTF8_MAX, 'response_byte_limit')
    # A complete single JSON fence is presentation, never a source of extra
    # objects/prose. Keep the original bytes for physical proof and apply the
    # same duplicate/nonfinite/shape limits to its entire inner value.
    payload = raw.strip()
    if payload.startswith("```"):
        import re
        fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*)\r?\n```", payload)
        _need(fenced is not None, 'response_fence')
        payload = fenced.group(1)
    value = json.loads(payload, object_pairs_hook=_unique, parse_constant=_nonfinite)
    json.dumps(value, allow_nan=False)
    _need(isinstance(value, dict), 'response_object')
    return value


def validate_primary(raw):
    value = _decode(raw)
    _need(type(value.get('pass')) is bool and type(value.get('overall_score')) in (int, float)
          and 0 <= value['overall_score'] <= 1 and isinstance(value.get('dimension_scores'), dict),
          'primary_shape')
    _need(all(type(v) in (int, float) and 0 <= v <= 1 for v in value['dimension_scores'].values()), 'dimensions')
    blockers = value.get('blocking_issues')
    _need(isinstance(blockers, list) and len(blockers) <= MAX_BLOCKERS, 'blocker_count')
    for item in blockers:
        _need(isinstance(item, dict) and {'code', 'severity', 'evidence', 'required_fix'} <= set(item)
              and set(item) <= {'code', 'severity', 'evidence', 'required_fix', 'path'}
              and all(isinstance(v, str) for v in item.values()), 'blocker_shape')
    return value


def validate_secondary(raw, count):
    value = _decode(raw)
    _need(value.get('adjudication_complete') is True and isinstance(value.get('decisions'), list), 'secondary_shape')
    decisions = value['decisions']
    _need(len(decisions) == count, 'secondary_coverage')
    indexes = []
    for d in decisions:
        _need(isinstance(d, dict) and {'index', 'decision', 'reason', 'required_fix'} == set(d)
              and type(d['index']) is int and d['decision'] in {'CONFIRM', 'DISMISS'}
              and isinstance(d['reason'], str) and isinstance(d['required_fix'], str), 'secondary_decision')
        indexes.append(d['index'])
    _need(sorted(indexes) == list(range(1, count + 1)), 'secondary_indexes')
    return value


class ReviewSendCap:
    """Only two exact template stages, same approval/prior journal; never retries."""
    def __init__(self, *, project, workflow_id, scope, user_message_id, plan, authorize):
        self.project = project
        self.workflow_id = workflow_id
        self.scope = scope
        self.user_message_id = user_message_id
        self.plan = deepcopy(plan)
        self.authorize = authorize
        self.first = None
        self.failure = None
        self.completed = set()
        self.receipts = {}

    async def __call__(self, session, settings, request):
        try:
            _need(str(self.workflow_id) == self.plan['target_id'], 'workflow_target_changed')
            if self.failure is not None:
                raise self.failure
            phase = PHASES.get(request.prompt_template)
            _need(phase is not None and phase not in self.completed, 'unexpected_or_repeated_stage')
            _need(request.project_id == self.project.id and request.workflow_run_id == self.workflow_id,
                  'request_scope')
            if phase == 'consistency':
                _need(self.first is not None and self.first['verdict']['blocking_issues'], 'secondary_not_required')
            await self.authorize(session)
            from bestseller.services.retained_review_input_proof import _fresh_catalog, _fresh_role
            scoped = no_retry_settings(settings)
            scoped.artifact_store.retain_prompt_payloads = True
            catalog, _ = _fresh_catalog()
            role = _fresh_role(scoped, self.project, request, catalog)
            from bestseller.services.source_bound_planning_content import assert_source_bound_planning_request_capacity
            capacity = assert_source_bound_planning_request_capacity(request.system_prompt, request.user_prompt,
                tools=request.tools, output_tokens=request.max_tokens_override)
            digest = llm._effective_input_hash(request, role)
            # A stable operation name survives rollback/process restart. Changed
            # request bodies cannot turn the same phase into another allowance.
            operation_id = 'bounded-outline-review.v1:' + self.user_message_id + ':' + str(self.workflow_id) + ':' + phase
            key = hashlib.sha256((self.scope + '\n' + operation_id).encode()).hexdigest()
            prior = self.plan['target_id']
            gid = authorization_id(self.user_message_id, prior, review_stage=phase)
            binding = {'scope_id': self.scope, 'operation_key': key, 'input_hash': digest,
                'model': role.model, 'endpoint': role.api_base, 'workflow_id': str(self.workflow_id),
                'user_message_id': self.user_message_id, 'prior_workflow_id': prior,
                'prior_journal_sha256': self.plan['automatic_attempts_sha256'],
                'template': request.prompt_template, 'version_prefix': request.prompt_version,
                'review_stage': phase, 'authority_evidence': {
                    'plan_sha256': self.plan['plan_sha256'], 'candidate_sha256': self.plan['candidate_sha256'],
                    'max_total_physical_sends': 2, 'max_phase_physical_sends': 1,
                    'primary_result_sha256': self.first['completion_sha256'] if self.first else None}}
            _need(not session.in_nested_transaction(), 'nested_transaction')
            # Match complete_text's existing pre-send checkpoint before the
            # independently durable grant write (also releases SQLite telemetry locks).
            await session.commit()
            ledger = ExecutionLedger(session.bind, self.scope, scoped.budget)
            await register_grant(ledger, grant_id=gid, binding=binding)
            req = request.model_copy(update={'operation_id': operation_id,
                'metadata': {**request.metadata, 'single_dispatch_authorization_id': gid,
                    'bounded_outline_review': BUNDLE_SCHEMA}})
            with bind_execution_scope(self.scope), llm.bind_project_prompt_audit(
                    self.project.id, reason="bounded outline review physical evidence"):
                completion = await llm.complete_text(session, scoped, req)
            _need(not completion.fallback_used and completion.finish_reason == 'stop'
                  and not completion.tool_calls and str(completion.provider or '').lower() not in {'', 'mock', 'fallback'},
                  'unavailable_response')
            if phase == 'initial':
                verdict = validate_primary(completion.content)
                self.first = {'verdict': verdict,
                    'completion_sha256': content_hash({k: getattr(completion, k) for k in (
                        'content', 'finish_reason', 'tool_calls', 'raw_message', 'provider', 'model_name',
                        'input_tokens', 'output_tokens')})}
            else:
                validate_secondary(completion.content, len(self.first['verdict']['blocking_issues']))
            from bestseller.infra.db.models import LlmRunModel
            run = await session.get(LlmRunModel, completion.llm_run_id)
            files = retained_files(scoped, run)
            self.receipts[phase] = {'retained_files': files, 'operation_id': operation_id, 'grant_id': gid,
                'llm_run_id': str(completion.llm_run_id), 'effective_input_hash': digest,
                'execution_receipt': deepcopy(completion.metadata.get('execution_receipt')),
                'raw_response_sha256': hashlib.sha256(completion.content.encode()).hexdigest(),
                'request_capacity': capacity, 'paid_opinion_not_adoption': True}
            self.completed.add(phase)
            return completion
        except BaseException as exc:
            self.failure = exc
            raise


async def build_current_review_brief(session, *, project, plan):
    """Same fresh complete foundation for compilation and future runtime."""
    from bestseller.services.chapter_outline_readiness import _foundation
    from bestseller.services import planner
    foundation, contents = await _foundation(session, project)
    _need(foundation == plan['foundation'], 'current_foundation_changed')
    brief = planner._outline_judge_project_brief(project, metadata=project.metadata_json,
        semantic_candidates=[], world_spec=contents['world_spec'])
    brief['prefix_evaluation_scope'] = {'approved_scope_only': [c['chapter_number'] for c in plan['candidate']['chapters']],
        'interpretation': 'Review only the supplied candidate; future events are plans, not adopted facts.'}
    brief['current_selected_foundation'] = {'source_records': foundation, 'complete_contents': contents,
        'authority': 'Current approved world/identity limits and future planning. Producer _meta is provenance, not author instruction; plans are not occurred canon.'}
    return brief


async def review_candidate_with_send_cap(session, settings, *, project, workflow_id,
        scope, plan, approval_id, user_message_id, threshold=0.82):
    """Explicit future runtime API. No implicit approval; normal review only."""
    from bestseller.services.operator_action_approval import require_operator_action_approval
    from bestseller.services.operator_outline_edit import approval_binding, prepare_operator_outline_edit, validate_operator_candidate_for_review
    from bestseller.infra.db.models import WorkflowRunModel
    _need(content_hash({k: v for k, v in plan.items() if k != 'plan_sha256'}) == plan['plan_sha256'], 'plan_hash')
    _need(plan['project_id'] == str(project.id) and plan['scope'] == scope, 'plan_scope')
    _need(str(workflow_id) == plan['target_id'], 'workflow_target_changed')
    async def authorize(s):
        authority = await require_operator_action_approval(s, approval_id=approval_id,
            purpose='outline_candidate_edit', binding=approval_binding(plan), capability='review_candidate')
        _need(authority['authorization_ref'] == user_message_id, 'human_message_binding')
        workflow = await s.get(WorkflowRunModel, UUID(plan['target_id']), populate_existing=True)
        _need(workflow is not None, 'target_missing')
        if workflow.metadata_json['candidates'][-1]['candidate_hash'] == plan['candidate_sha256']:
            await validate_operator_candidate_for_review(s, project=project, workflow=workflow, state=workflow.metadata_json)
        else:
            current = await prepare_operator_outline_edit(s, project_id=project.id, workflow_id=workflow.id,
                expected_candidate_sha256=plan['parent_sha256'], patches=plan['patches'])
            _need(current == plan, 'current_source_or_candidate_changed')
    # No grant can be registered if this exact existing approval is absent.
    await authorize(session)
    project_brief = await build_current_review_brief(session, project=project, plan=plan)
    cap = ReviewSendCap(project=project, workflow_id=workflow_id, scope=scope,
        user_message_id=user_message_id, plan=plan, authorize=authorize)
    result = await judge.judge_outline_commercial_readiness(session, no_retry_settings(settings),
        outline_payload=plan['candidate'], project_brief=project_brief, threshold=threshold,
        workflow_run_id=workflow_id, project_id=project.id, completion_dispatch=cap)
    # The legacy consistency function catches exceptions. Surface any bounded
    # dispatch/protocol/Unknown failure even if legacy code retained first grade.
    if cap.failure is not None:
        raise cap.failure
    return {'schema': BUNDLE_SCHEMA, 'project_id': str(project.id), 'workflow_id': str(workflow_id),
            'scope': scope, 'plan': deepcopy(plan), 'approval_id': str(approval_id),
            'user_message_id': user_message_id, 'threshold': threshold, 'consumer_policy': consumer_policy(),
            'project_brief_sha256': content_hash(project_brief),
            'grade': result.model_dump(mode='json'), 'review_send_receipts': cap.receipts,
            'candidate_sha256': plan['candidate_sha256'], 'automatic_retry_credit_granted': False,
            'old_attempts_reset': False,
            'applied_or_adopted': False, 'prefix_consumer_integration_verified': False}


BUNDLE_SCHEMA = 'bounded-outline-review-result.v1'


def consumer_policy():
    root = Path(__file__).parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        'bounded_outline_review.py', 'outline_llm_judge.py', 'single_dispatch_authorization.py',
        'chapter_outline_readiness.py', 'workflows.py')}


def retained_files(settings, run):
    """Exact full files, never a reconstructed or truncated substitute."""
    _need(run is not None and settings.artifact_store.mode == 'local', 'retained_run_missing')
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    result = {}
    for kind in ('request', 'response'):
        ref = getattr(run, kind + '_payload_ref')
        _need(isinstance(ref, str) and bool(ref), 'retained_' + kind + '_missing')
        path = (root / ref).resolve()
        _need(path.is_relative_to(root), 'retained_path_outside_store')
        raw = path.read_bytes()
        value = json.loads(raw)
        _need(value.get('schema_version') == 'llm-payload.v1' and value.get('kind') == kind
              and value.get('replayable') is True and not value.get('replayability_reasons')
              and value.get('llm_run_id') == str(run.id)
              and value.get('project_id') == str(run.project_id)
              and value.get('workflow_run_id') == str(run.workflow_run_id), 'retained_identity')
        texts = [value.get('content')] if kind == 'response' else [
            (value.get('messages') or {}).get(k) for k in ('system', 'user')]
        for item in texts:
            _need(isinstance(item, dict) and isinstance(item.get('text'), str)
                  and item.get('truncated') is False and item.get('original_chars') == len(item['text']),
                  'retained_text_incomplete')
        result[kind] = {'ref': ref, 'sha256': hashlib.sha256(raw).hexdigest()}
    return result


async def verify_bounded_review_result(session, settings, *, project, workflow, candidate, bundle):
    """Replay normal interpretation from physical returns only; cannot dispatch."""
    from bestseller.infra.db.models import LlmRunModel, ExecutionBudgetModel, ExecutionAttemptModel
    from sqlalchemy import select
    from bestseller.services.retained_review_input_proof import _retained_request, _physical_return, _fresh_catalog, _fresh_role
    from bestseller.services.operator_outline_edit import validate_operator_candidate_for_review, approval_binding
    from bestseller.services.operator_action_approval import require_operator_action_approval
    _need(isinstance(bundle, dict) and bundle.get('schema') == BUNDLE_SCHEMA, 'consumer_bundle_missing')
    _need(bundle['consumer_policy'] == consumer_policy(), 'consumer_policy_changed')
    plan = bundle['plan']
    _need(content_hash({k: v for k, v in plan.items() if k != 'plan_sha256'}) == plan['plan_sha256'], 'consumer_plan_hash')
    _need(bundle['project_id'] == plan['project_id'] == str(project.id)
          and bundle['workflow_id'] == plan['target_id'] == str(workflow.id)
          and workflow.project_id == project.id and bundle['scope'] == plan['scope']
          and bundle['candidate_sha256'] == plan['candidate_sha256'] == content_hash(candidate)
          and candidate == plan['candidate'], 'consumer_binding')
    await validate_operator_candidate_for_review(session, project=project, workflow=workflow, state=workflow.metadata_json)
    refs = workflow.metadata_json.get('operator_outline_edits', [])
    _need(any(r.get('plan_sha256') == plan['plan_sha256'] and r.get('candidate_sha256') == plan['candidate_sha256']
              for r in refs), 'consumer_candidate_not_applied')
    approval = await require_operator_action_approval(session, approval_id=bundle['approval_id'],
        purpose='outline_candidate_edit', binding=approval_binding(plan), capability='review_candidate')
    _need(approval['authorization_ref'] == bundle['user_message_id'], 'consumer_authority')
    brief = await build_current_review_brief(session, project=project, plan=plan)
    _need(content_hash(brief) == bundle['project_brief_sha256'], 'consumer_source_changed')
    _need(bundle['threshold'] == settings.pipeline.commercial_strict_prewrite_planning_judge_threshold, 'consumer_threshold')
    catalog, _ = _fresh_catalog()
    seen = set()
    first = None
    failure = None
    async def retained(s, scoped, request):
        nonlocal first, failure, initial_raw
        try:
            phase = PHASES.get(request.prompt_template)
            _need(phase is not None and phase not in seen and (phase != 'consistency' or first), 'consumer_stage')
            proof = bundle['review_send_receipts'][phase]
            run = await s.get(LlmRunModel, UUID(proof['llm_run_id']), populate_existing=True)
            _need(run is not None and run.project_id == project.id and run.workflow_run_id == workflow.id
                  and run.prompt_template == request.prompt_template and run.prompt_version == request.prompt_version
                  and (run.metadata_json or {}).get('bounded_outline_review') == BUNDLE_SCHEMA,
                  'consumer_run_binding')
            _need(retained_files(scoped, run) == proof['retained_files'], 'consumer_retained_changed')
            retained_request = _retained_request(scoped, run)
            role = _fresh_role(scoped, project, request, catalog)
            digest = llm._effective_input_hash(request, role)
            _need(digest == llm._effective_input_hash(retained_request, role) == proof['effective_input_hash']
                  == run.prompt_hash, 'consumer_request_changed')
            paid, key = await _physical_return(s, run, scope=bundle['scope'], expected_hash=digest, expected_model=role.model)
            attempts = list(await s.scalars(select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.operation_key == key)))
            _need(len(attempts) == 1 and attempts[0].status == 'completed'
                  and proof['execution_receipt'] == run.metadata_json['execution_receipt']
                  and proof['execution_receipt']['attempts'][0]['attempt_id'] == attempts[0].id,
                  'consumer_attempt_set')
            operation_id = 'bounded-outline-review.v1:' + bundle['user_message_id'] + ':' + str(workflow.id) + ':' + phase
            gid = authorization_id(bundle['user_message_id'], str(workflow.id), review_stage=phase)
            _need(proof['operation_id'] == operation_id and proof['grant_id'] == gid
                  and key == hashlib.sha256((bundle['scope'] + '\n' + operation_id).encode()).hexdigest(), 'consumer_operation')
            budget = await s.get(ExecutionBudgetModel, bundle['scope'], populate_existing=True)
            grant = (budget.metadata_json or {}).get('single_dispatch_grants', {}).get(gid) if budget else None
            _need(grant is not None and grant.get('status') == 'spent' and grant.get('physical_attempt_cap') == 1, 'consumer_grant')
            binding = grant['binding']
            expected = {'scope_id': bundle['scope'], 'operation_key': key, 'input_hash': digest, 'model': role.model,
                'endpoint': role.api_base, 'workflow_id': str(workflow.id), 'user_message_id': bundle['user_message_id'],
                'prior_workflow_id': str(workflow.id), 'prior_journal_sha256': plan['automatic_attempts_sha256'],
                'template': request.prompt_template, 'version_prefix': request.prompt_version, 'review_stage': phase,
                'authority_evidence': {'plan_sha256': plan['plan_sha256'], 'candidate_sha256': plan['candidate_sha256'],
                    'max_total_physical_sends': 2, 'max_phase_physical_sends': 1,
                    'primary_result_sha256': first}}
            _need(binding == expected and grant['used_attempt_id'] == proof['execution_receipt']['attempts'][0]['attempt_id'], 'consumer_grant_binding')
            _need(hashlib.sha256(paid.content.encode()).hexdigest() == proof['raw_response_sha256'], 'consumer_response_changed')
            response_path = Path(scoped.artifact_store.local_dir).expanduser() / run.response_payload_ref
            response = json.loads(response_path.read_bytes())
            for k in ('provider', 'finish_reason', 'input_tokens', 'output_tokens', 'fallback_used', 'fallback_source'):
                _need(response.get(k) == getattr(paid, k), 'consumer_response_fields')
            _need(response['content']['text'] == paid.content and response['model'] == paid.model_name
                  and response.get('tool_calls') == paid.tool_calls and response.get('raw_message') == paid.raw_message
                  and paid.finish_reason == 'stop' and not paid.fallback_used and not paid.tool_calls, 'consumer_response_fields')
            if phase == 'initial':
                validate_primary(paid.content)
                first = content_hash({k: getattr(paid, k) for k in ('content', 'finish_reason', 'tool_calls', 'raw_message',
                    'provider', 'model_name', 'input_tokens', 'output_tokens')})
            else:
                validate_secondary(paid.content, len(validate_primary(initial_raw)['blocking_issues']))
            seen.add(phase)
            if phase == 'initial':
                initial_raw = paid.content
            return paid.model_copy(update={'llm_run_id': run.id})
        except BaseException as exc:
            failure = exc
            raise
    initial_raw = ''
    result = await judge.judge_outline_commercial_readiness(session, settings, outline_payload=candidate,
        project_brief=brief, threshold=bundle['threshold'], workflow_run_id=workflow.id, project_id=project.id,
        completion_dispatch=retained)
    if failure is not None:
        raise failure
    _need(seen == set(bundle['review_send_receipts']) and result.model_dump(mode='json') == bundle['grade'], 'consumer_grade_changed')
    _need(result.passed and not result.infrastructure_failure, 'consumer_not_passed')
    return result


async def consume_bounded_prefix_review(session, settings, *, project, workflow_id, bundle):
    """Publish through the existing decision-only prefix entry; no prewrite approval."""
    from bestseller.infra.db.models import WorkflowRunModel
    from bestseller.services import chapter_outline_readiness as ready
    workflow = await session.get(WorkflowRunModel, workflow_id, populate_existing=True)
    _need(workflow is not None and workflow.workflow_type == ready.WORKFLOW_TYPE, 'consumer_workflow')
    state = deepcopy(workflow.metadata_json)
    current = state['candidates'][-1]
    result = await verify_bounded_review_result(session, settings, project=project, workflow=workflow,
        candidate=current['candidate'], bundle=bundle)
    report = result.model_dump(mode='json', by_alias=True)
    report['graded_outline_hash'] = content_hash(current['candidate'])
    report['bounded_review_result'] = deepcopy(bundle)
    if current.get('report') != report:
        state['bounded_review_result_sha256'] = content_hash(bundle)
        state.setdefault('bounded_review_history', []).append({'previous_report': deepcopy(current.get('report')),
            'bundle_sha256': content_hash(bundle)})
        current['report'] = report
        await ready._save(session, workflow, state)
    return await ready.approve_chapter_outline_prefix(session, settings, project=project,
        source_payload=state['source_payload'], through_chapter=state['identity']['through_chapter'],
        max_repair_rounds=state['identity']['max_repair_rounds'], workflow_run_id=workflow.id,
        from_chapter=state['chapter_numbers'][0], decision_only=True,
        allow_editorial_debt=False)

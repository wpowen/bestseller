"""Zero-dispatch, exact old presentation compatibility with fresh source proof.

No policy allowlist: the historical contract is reconstructed from the current
unchanged substantive base and the one known old wire presentation. Original
workflow failure and protocol journal are never rewritten.
"""
from copy import deepcopy
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from bestseller.infra.db.models import LlmRunModel, WorkflowRunModel, ExecutionAttemptModel
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.planning_empty_notes_compat import ADAPTER

SCHEMA = 'planning-empty-notes-parser-reconciliation.v1'
_SUFFIX = (
    '此版本用quote_selection目录ID选择完整原文，不复制evidence/source_requirement/source_refs。'
    '每条问题返回candidate_quote_id和source_quote_ids；解释留在projection_checks。'
    '照录schema/input_sha256/candidate_sha256/catalog_sha256。catalog_by_pointer是与input同路径的目录树，叶子仅为quote_id；'
    '路径段按JSON Pointer转义，数组下标为数字键；按该路径读取input完整原文，目录不是新来源。定位不证明语义矛盾。')


def _need(ok, code):
    if not ok:
        raise ValueError('planning_parser_compat_' + code)


def legacy_contract():
    from bestseller.services import planning_draft_admission as a
    system = a.VOLUME_SYSTEM + _SUFFIX
    policy = content_hash({'schema': a.VOLUME_QUOTE_SCHEMA,
        'base_policy': a.VOLUME_HARD_ADMISSION_POLICY, 'system': system,
        'selector': 'planning-literal-quote-selection.v1', 'catalog_view': 'literal-pointer-tree.v1',
        'catalog_order': 'sorted-object-keys-list-order.v1', 'scalar_quotes': 'typed-json-scalars.v1',
        'protocol_attempt_cap': 1})
    return system, policy



def prove_presentation(request, supplied):
    from bestseller.services import planning_draft_admission as a
    from bestseller.services.lossless_source_encoding import decode_lossless_source
    from bestseller.services.planning_quote_evidence import quote_contract, quote_id_tool, quote_selection_view
    from bestseller.services.planning_admission_submission import _unique, _nonfinite
    system, policy = legacy_contract()
    current_tail = ('八个必需字段必须同为顶层兄弟字段：先写schema和三个hash，再写draft_eligible、hard_violations、projection_checks，最后editorial_notes。'
        'editorial_notes只允许字符串数组，无建议填[]，禁止把投影检查或hash放进去；禁止自行改成对象包装。')
    _need(a.VOLUME_QUOTE_SYSTEM == a._volume_quote_base_system() + _SUFFIX + current_tail,
          'current_substantive_system_changed')
    _need(request.system_prompt == system, 'substantive_system_changed')
    _need(request.prompt_version == a.VOLUME_QUOTE_SCHEMA and request.prompt_template == a.TEMPLATE
          and request.max_tokens_override == 5000, 'request_contract_changed')
    decoder = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_nonfinite)
    marker = 'source_review_data='
    _need(request.user_prompt.count(marker) == 1, 'transport_shape')
    encoded, _ = decoder.raw_decode(request.user_prompt.split(marker, 1)[1])
    logical = decode_lossless_source(encoded)
    _need(set(logical) == {'input', 'graded_input_hash', 'quote_selection'}, 'logical_fields')
    original = logical['input']
    _need(original.get('hard_admission_policy') == policy
          and original.get('schema') == a.VOLUME_QUOTE_SCHEMA, 'original_policy_changed')
    current = {**supplied, 'schema': a.VOLUME_QUOTE_SCHEMA, 'hard_admission_policy': policy}
    _need(content_hash(current) == content_hash(original) and current == original, 'current_source_changed')
    contract = quote_contract(original)
    _need(logical['graded_input_hash'] == content_hash(original)
          and logical['quote_selection'] == quote_selection_view(contract), 'original_input_changed')
    tool, _ = quote_id_tool(a._Grade.model_json_schema(), original['projection_audit_targets'], original)
    # Reconstruct exactly the known old presentation, including descriptions.
    # Do not ignore arbitrary descriptions: they too can carry instructions.
    legacy_tool = deepcopy(tool)
    function = legacy_tool['function']
    suffix = (' All eight required fields are siblings at the root. Emit binding fields first and editorial_notes last;'
        ' editorial_notes is only an array of strings, never a container for any other field.')
    _need(function['description'].endswith(suffix), 'current_tool_presentation_changed')
    function['description'] = function['description'][:-len(suffix)]
    parameters = function['parameters']
    _need(parameters['properties']['editorial_notes'].pop('description') ==
        'Root-level array of strings; use [] when empty. Never an object or a wrapper for projection_checks or binding hashes.',
        'current_notes_presentation_changed')
    order = ['draft_eligible', 'hard_violations', 'editorial_notes', 'projection_checks',
             'schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256']
    parameters['properties'] = {key: parameters['properties'][key] for key in order}
    parameters['required'] = order
    _need(request.tools == [legacy_tool], 'tool_constraints_changed')
    _need(request.tool_choice == {'type': 'function', 'function': {'name': 'submit_planning_admission'}},
          'tool_choice_changed')
    return original, {'original_policy': policy, 'current_policy': a.VOLUME_QUOTE_POLICY,
        'substantive_base_policy': a.VOLUME_HARD_ADMISSION_POLICY,
        'original_system_sha256': hashlib.sha256(system.encode()).hexdigest(),
        'current_source_except_presentation_policy_exact': True,
        'tool_constraints_equal': True, 'original_request_relabelled': False}


async def reconcile_retained_volume_admission(session, settings, *, project, workflow_run_id,
        candidate_payload, book_spec, world_spec, cast_spec):
    from bestseller.services import planning_draft_admission as a, retained_review_input_proof as proof, llm
    from bestseller.services.foundation_selection import _physical_file
    workflow = await session.get(WorkflowRunModel, workflow_run_id)
    _need(workflow is not None and workflow.project_id == project.id
          and workflow.workflow_type == 'review_volume_source_admission'
          and workflow.scope_type == 'project' and workflow.scope_id == project.id
          and workflow.status == 'failed', 'workflow')
    state = workflow.metadata_json.get('source_admission_protocol') or {}
    journal = state.get('attempts')
    _need(state.get('status') == 'protocol_exhausted' and isinstance(journal, list) and len(journal) == 1,
          'journal')
    entry = journal[0]
    _need(entry.get('status') == 'protocol_invalid' and (entry.get('error') or {}).get('detail') in {
        'planning_admission_submission:quote_contract_stale',
        'planning_admission_submission:quote_submission_shape'}, 'historical_error')
    completion = entry['completion']
    run = await session.get(LlmRunModel, UUID(completion['llm_run_id']))
    _need(run is not None, 'run_missing')
    digest = state['graded_input_hash']
    scope = await a._prove_paid(session, project=project, workflow_run_id=workflow_run_id,
        receipt=a._completion_receipt(completion),
        operation_id=a._protocol_operation(project, workflow_run_id, digest, journal, 0),
        expected_schema=a.VOLUME_QUOTE_SCHEMA)
    request_json, request_sha = _physical_file(settings, run, 'request')
    response, response_sha = _physical_file(settings, run, 'response')
    request = proof._retained_request(settings, run)
    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    effective_hash = llm._effective_input_hash(request, role)
    paid, key = await proof._physical_return(session, run, scope=scope,
        expected_hash=effective_hash, expected_model=role.model)
    _need(a._physical_return(paid.model_dump(mode='json')) == a._physical_return(completion), 'physical_return')
    attempts = list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key == key)))
    claimed = (run.metadata_json or {}).get('execution_receipt', {}).get('attempts', [])
    _need(len(attempts) == len(claimed) == 1 and attempts[0].status == 'completed'
          and {x.id for x in attempts} == {x.get('attempt_id') for x in claimed}, 'physical_attempt_set')
    text = response.get('content', {})
    _need(text.get('truncated') is False and text.get('text') == paid.content
          and text.get('original_chars') == len(paid.content), 'response_content')
    for field in ('tool_calls', 'raw_message'):
        value = getattr(paid, field)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        record = (response.get('structured_payload_receipts') or {}).get(field, {})
        _need(response.get(field) == value and record.get('status') == 'retained'
              and record.get('encoding') == 'json-utf8' and record.get('truncated') is False
              and record.get('original_chars') == len(encoded)
              and type(record.get('limit_chars')) is int and record['limit_chars'] >= len(encoded)
              and record.get('sha256') == hashlib.sha256(encoded.encode()).hexdigest(), 'response_structured')
    for field in ('provider', 'finish_reason', 'input_tokens', 'output_tokens', 'fallback_used', 'fallback_source'):
        _need(response.get(field) == getattr(paid, field), 'response_fields')
    _need(response.get('model') == paid.model_name and response.get('tool_call_count') == 1
          and paid.finish_reason == 'tool_calls' and paid.output_tokens < 5000, 'complete_return')
    # The paid input carries a hash-bound audit reference, not raw diagnostics.
    # The input builder checks this original diagnostic object's hash; the
    # reconstructed reference must then equal the actual paid input exactly.
    diagnostic = deepcopy(workflow.metadata_json.get('prior_diagnostics'))
    _need(isinstance(diagnostic, dict)
          and content_hash(diagnostic) == workflow.metadata_json.get('prior_diagnostics_hash'), 'diagnostics_binding')
    current = await a._input(session, project=project, workflow_run_id=workflow_run_id,
        candidate_payload=candidate_payload, book_spec=book_spec, world_spec=world_spec, cast_spec=cast_spec,
        commercial_report=diagnostic, settings=settings)
    original, compatibility = prove_presentation(request, current)
    _need(content_hash(original) == digest and state.get('hard_admission_policy') == compatibility['original_policy'],
          'original_journal_binding')
    raw_grade, submission = a._submitted_grade(completion, supplied=original, schema=a.VOLUME_QUOTE_SCHEMA)
    normalization = submission.get('presentation_normalization') or {}
    _need(normalization.get('schema') == ADAPTER, 'normalization_missing')
    grade = a._grade(raw_grade, candidate_payload, original)
    reconciliation = {'schema': SCHEMA, 'adapter': ADAPTER, **compatibility,
        'original_journal_sha256': content_hash(state), 'request_file_sha256': request_sha,
        'response_file_sha256': response_sha, 'physical_return_sha256': content_hash(a._physical_return(completion)),
        'normalization': deepcopy(normalization), 'effective_input_hash': run.prompt_hash,
        'physical_attempt_id': str(attempts[0].id), 'provider_dispatches': 0,
        'original_failure_preserved': True, 'new_allowance_granted': False}
    return {'schema': a.VOLUME_QUOTE_SCHEMA, 'hard_admission_policy': compatibility['original_policy'],
        'draft_eligible': grade.draft_eligible, 'project_id': str(project.id), 'workflow_run_id': str(workflow_run_id),
        'graded_input_hash': digest, 'candidate_hash': content_hash(candidate_payload),
        'foundation_hashes': original['foundation'], 'commercial_report': deepcopy(diagnostic),
        'raw_grade': raw_grade, 'assessment': grade.model_dump(mode='json'), 'submission_receipt': submission,
        'llm_run_id': str(run.id), 'effective_input_hash': run.prompt_hash,
        'protocol_attempts': deepcopy(journal), 'parser_reconciliation': reconciliation}

"""Source-bound Cast delivery in independently checked, durable parts.

Text JSON uses the documented streaming channel; application validation is
strict. No provider schema guarantee, canonical selection, approval or prose
is manufactured by assembling these real model-produced planning parts.
"""
from copy import deepcopy
import hashlib
import json

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.source_bound_planning_submission import (
    _strict, _validate_wire, planning_submission_contract, wire_diagnostics,
)

POLICY = 'source-bound-cast-parts.v1'
PART_TEMPLATES = {'planner_cast_person_part', 'planner_cast_links_part'}
OUTPUT_TOKENS = 16384


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def enabled(project):
    from bestseller.services.model_catalog import resolve_project_model_entry
    from bestseller.services.model_context_capacity import documented_capacity, M31_CONTEXT
    meta = project.metadata_json or {}
    profile = meta.get('production_profile') or (meta.get('user_hints') or {}).get('production_profile')
    entry = resolve_project_model_entry(meta)
    return bool(profile == 'complete' and entry is not None
                and documented_capacity(entry.model, endpoint=entry.api_base) == M31_CONTEXT)


def roster(projection):
    """Source slots identify proposals; no person or identity is filled in."""
    result = []
    for slot, person in [('protagonist', projection.get('protagonist')),
                         ('antagonist', projection.get('antagonist')),
                         *[(f'supporting_cast/{i}', value)
                           for i, value in enumerate(projection.get('supporting_cast') or [])]]:
        if not isinstance(person, dict) or not person:
            continue
        name = person.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('cast_parts_source_identity_missing:' + slot)
        result.append({'id': f'person:{len(result)}', 'source_slot': slot,
                       'name': name, 'source': deepcopy(person)})
    if not result or result[0]['source_slot'] != 'protagonist':
        raise ValueError('cast_parts_protagonist_source_required')
    if len({row['name'] for row in result}) != len(result):
        raise ValueError('cast_parts_ambiguous_source_identity')
    return result


def person_schema(person):
    full = planning_submission_contract('cast_spec')['json_schema']
    definitions = deepcopy(full['$defs'])
    # The name must actually be delivered by the model and match its source;
    # it is never inserted or repaired by this assembler.
    definitions['CharacterInput']['properties']['name']['enum'] = [person['name']]
    if person['source_slot'] == 'protagonist':
        definitions['CharacterInput']['required'] = list(dict.fromkeys([*definitions['CharacterInput'].get('required', []), 'goal']))
        definitions['CharacterInput']['properties']['goal'] = {'type': 'string', 'minLength': 1}
    return {'type': 'object', 'properties': {'person': {'$ref': '#/$defs/CharacterInput'}},
            'required': ['person'], 'additionalProperties': False, '$defs': definitions}


def links_schema(people):
    full = planning_submission_contract('cast_spec')['json_schema']
    ids = [person['id'] for person in people]
    properties = {key: deepcopy(value) for key, value in full['properties'].items()
                  if key not in {'protagonist', 'antagonist', 'supporting_cast'}}
    properties['layout'] = {'type': 'object', 'properties': {
        'protagonist': {'type': 'string', 'enum': [people[0]['id']]},
        'antagonist': {'anyOf': [{'type': 'string', 'enum': ids[1:]}, {'type': 'null'}]},
        'supporting_cast': {'type': 'array', 'items': {'type': 'string', 'enum': ids[1:]}},
    }, 'required': ['protagonist', 'antagonist', 'supporting_cast'], 'additionalProperties': False}
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': True, '$defs': deepcopy(full['$defs'])}


def validate_part(value, schema):
    # The existing exact validator decides acceptance; diagnostics cannot
    # convert strings to arrays, add names, default fields or grant approval.
    _validate_wire(value, schema, schema)
    return value


def assemble(people, persons, links):
    validate_part(links, links_schema(people))
    if {'protagonist', 'antagonist', 'supporting_cast', '_meta'} & links.keys():
        raise ValueError('cast_parts_links_may_not_replace_people_or_audit')
    layout = links['layout']
    selected = [layout['protagonist'], *([layout['antagonist']] if layout['antagonist'] else []),
                *layout['supporting_cast']]
    expected = {person['id'] for person in people}
    if len(selected) != len(expected) or set(selected) != expected:
        raise ValueError('cast_parts_layout_omits_or_duplicates_person')
    if set(persons) != expected:
        raise ValueError('cast_parts_missing_person_result')
    for person in people:
        validate_part({'person': persons[person['id']]}, person_schema(person))
    result = {key: deepcopy(value) for key, value in links.items() if key != 'layout'}
    result.update(protagonist=deepcopy(persons[layout['protagonist']]),
                  antagonist=deepcopy(persons[layout['antagonist']]) if layout['antagonist'] else None,
                  supporting_cast=[deepcopy(persons[key]) for key in layout['supporting_cast']])
    full = planning_submission_contract('cast_spec')['json_schema']
    _validate_wire(result, full, full)
    return result


def source_packet(user):
    from bestseller.services.planning_dispatch_transport import _document
    from bestseller.services.source_bound_planning_content import decode_source_bound_planning_document
    document, suffix = _document(user)
    if suffix.strip():
        raise ValueError('cast_parts_unrecognized_source_suffix')
    packet = deepcopy(decode_source_bound_planning_document(document))
    from bestseller.services.source_bound_planning_content import CONTENT_POLICY
    if packet.get('kind') != 'cast_spec' or packet.get('protocol') != CONTENT_POLICY:
        raise ValueError('cast_parts_source_contract_mismatch')
    contract = packet.pop('current_output_contract')
    packet.pop('prompt_capacity', None)  # Recomputed for each exact physical request.
    return packet, contract


async def _checkpoint(session, workflow_id, project_id, receipt):
    workflow = await session.get(WorkflowRunModel, workflow_id)
    if workflow is None or workflow.project_id != project_id:
        raise ValueError('cast_parts_workflow_owner_changed')
    meta = deepcopy(workflow.metadata_json or {})
    checkpoints = meta.setdefault('cast_part_checkpoints', {})
    key = receipt['recipe_sha256'] + ':' + receipt['part_id']
    if key in checkpoints and checkpoints[key] != receipt:
        raise ValueError('cast_parts_checkpoint_conflict')
    checkpoints[key] = deepcopy(receipt)
    workflow.metadata_json = meta
    await session.commit()


async def generate(session, settings, *, project, system, user, projection,
                   workflow_run_id, validate, before_dispatch):
    from bestseller.services import planner as p
    from bestseller.services.llm import LLMCompletionRequest, complete_text, bind_execution_operation, bind_project_prompt_audit
    from bestseller.services.progress_context import emit_activity
    from bestseller.services.model_context_capacity import bind_selected_model_capacity
    from bestseller.services.source_bound_planning_content import (
        CONTENT_POLICY, assert_source_bound_planning_request_capacity,
    )
    from bestseller.domain.enums import ArtifactType

    settings = settings.model_copy(deep=True)
    settings.artifact_store.retain_prompt_payloads = True
    people = roster(projection)
    packet, full_contract = source_packet(user)
    source_hash = _sha(packet)
    recipe_hash = _sha({'schema': POLICY, 'source_sha256': source_hash,
                        'full_contract': full_contract, 'projection': projection,
                        'output_tokens': OUTPUT_TOKENS})
    reusable = await p._latest_reusable_planning_artifact(session, project=project,
        artifact_type=ArtifactType.CAST_SPEC, input_hash=recipe_hash, validator=validate,
        allow_legacy_latest=False)
    if reusable is not None:
        return p._with_planning_meta(reusable.content, logical_name='cast_spec', input_hash=recipe_hash,
            workflow_run_id=workflow_run_id, reused_artifact_id=reusable.id, project=project)

    part_system = (system + '\nCURRENT DELIVERY CONTRACT: Produce only the current planning part as strict JSON text. '
        'The reference full-Cast contract is for final assembly, not the current output shape. '
        'Follow part_contract exactly. Preserve source facts and all named people; do not invent '
        'unknown past facts to fill optional fields. Mandatory planning traits may be compatible '
        'proposals, never author-approved facts. Unknown arrays are [], nullable scalars null. '
        'No markdown, tool wrapper, commentary or approval claims.')
    receipts = []
    completions = {}
    documents = {}

    async def produce(part_id, template, schema, task, prior_people=None, extra_validator=None):
        document = {'schema': POLICY, 'source_packet_sha256': source_hash,
            'frozen_source_packet': packet, 'reference_full_cast_contract': full_contract,
            'part_contract': schema, 'current_part': task,
            'already_generated_people': prior_people or []}
        documents[part_id] = deepcopy(document)
        original_user = _json(document)
        next_user = original_user
        field_repair_receipt = None
        for attempt in range(2):
            await before_dispatch()
            physical_user = next_user
            part_transport = None
            with bind_selected_model_capacity(project.metadata_json or {}):
                if template == 'planner_cast_links_part':
                    from bestseller.services.cast_links_transport import prepare
                    physical_user, part_transport = prepare(part_system,next_user,schema=schema,output_tokens=OUTPUT_TOKENS)
                capacity = assert_source_bound_planning_request_capacity(
                    part_system, physical_user, tools=None, output_tokens=OUTPUT_TOKENS)
            await p._heartbeat_planner_workflow(session, workflow_run_id=workflow_run_id,
                logical_name='cast_spec:' + part_id, phase='llm_started', attempt=attempt + 1)
            request = LLMCompletionRequest(logical_role='planner', system_prompt=part_system,
                user_prompt=physical_user, fallback_response='{}', project_id=project.id,
                workflow_run_id=workflow_run_id, prompt_template=template, prompt_version=POLICY,
                max_tokens_override=OUTPUT_TOKENS, metadata={'project_slug': project.slug,
                    'artifact': 'cast_spec', 'cast_parts_policy': POLICY, 'cast_part_id': part_id,
                    'cast_recipe_sha256': recipe_hash, 'source_packet_sha256': source_hash,
                    'part_schema_sha256': _sha(schema), 'part_attempt': attempt + 1,
                    'require_source_complete_capacity': True})
            if part_transport is not None:
                from bestseller.services.cast_links_transport import bind_logical_identity
                request = await bind_logical_identity(session,settings,project=project,request=request,
                    original_user=next_user,receipt=part_transport)
            if attempt == 0 and template == 'planner_cast_person_part':
                from bestseller.services.retained_cast_person import restore
                retained = await restore(session,settings,project=project,request=request,
                    document=document,schema=schema,workflow_run_id=workflow_run_id)
                if retained is not None:
                    value, base_completion, retained_origin = retained
                    projection_receipt = retained_origin['projection_receipt']
                    field_receipt = None
                    if projection_receipt['wire_errors']:
                        from bestseller.services.source_cast_field_repair import repair
                        value, field_receipt = await repair(session,settings,project=project,
                            part_system=part_system,original_document=document,schema=schema,value=value,
                            base_completion=base_completion,recipe_hash=recipe_hash,part_id=part_id,
                            workflow_run_id=workflow_run_id,before_dispatch=before_dispatch,
                            retained_origin=retained_origin)
                    validate_part(value,schema)
                    run_ids = [str(base_completion.llm_run_id)]
                    if field_receipt is not None:run_ids.append(field_receipt['correction_llm_run_id'])
                    receipt = {'schema':POLICY,'recipe_sha256':recipe_hash,'part_id':part_id,
                        'source_packet_sha256':source_hash,'part_schema_sha256':_sha(schema),
                        'payload_sha256':_sha(value),'llm_run_id':str(base_completion.llm_run_id),
                        'llm_run_ids':run_ids,'finish_reason':base_completion.finish_reason,
                        'quality_approval_granted':False,'single_provider_payload_claimed':False,
                        'delivery_method':'retained_native_person_plus_model_field_corrections',
                        'retained_person_receipt':projection_receipt,'field_correction_receipt':field_receipt,
                        'input_tokens':base_completion.input_tokens,'output_tokens':base_completion.output_tokens,
                        'physical_capacity':capacity,'new_whole_person_generation_calls':0}
                    await _checkpoint(session,workflow_run_id,project.id,receipt)
                    receipts.append(receipt)
                    emit_activity('cast_retained_person_completed',{'part_id':part_id,
                        'source_llm_run_id':str(base_completion.llm_run_id),'approved':False,
                        'new_whole_person_generation_calls':0,'body_chapters':0})
                    completions[part_id] = base_completion
                    return value
            with bind_execution_operation('cast-parts:' + str(project.id) + ':' + recipe_hash + ':' + part_id), \
                 bind_project_prompt_audit(project.id, reason='source-bound Cast part delivery; original unknown operation isolated'):
                completion = await complete_text(session, settings, request)
            await p._heartbeat_planner_workflow(session, workflow_run_id=workflow_run_id,
                logical_name='cast_spec:' + part_id, phase='llm_completed', attempt=attempt + 1)
            # Retain actual returned runs even if the following shape check rejects them.
            await session.commit()
            # Unknown/no-usage exceptions from complete_text are outside any
            # repair catch. A known length result requires smaller subparts.
            if completion.finish_reason == 'length':
                raise ValueError('cast_part_truncated_requires_subpartition:' + part_id)
            if (completion.provider in {'', 'mock', 'fallback', 'test', 'fixture', 'unknown'}
                or completion.fallback_used or completion.llm_run_id is None
                or completion.finish_reason not in {'stop', 'end_turn', 'completed'}
                or completion.tool_calls):
                raise ValueError('cast_part_complete_text_receipt_required:' + part_id)
            value = None
            try:
                value = _strict(completion.content)
                validate_part(value, schema)
                if extra_validator is not None:
                    extra_validator(deepcopy(value))
            except (ValueError, TypeError) as error:
                if attempt:
                    if template != 'planner_cast_person_part' or not isinstance(value, dict):
                        raise ValueError('cast_part_schema_repair_exhausted:' + part_id) from error
                    from bestseller.services.source_cast_field_repair import repair
                    value, field_repair_receipt = await repair(session, settings, project=project,
                        part_system=part_system, original_document=document, schema=schema, value=value,
                        base_completion=completion, recipe_hash=recipe_hash, part_id=part_id,
                        workflow_run_id=workflow_run_id, before_dispatch=before_dispatch)
                else:
                    next_user = original_user + '\n\n' + _json({
                        'schema_correction_only': True, 'error': str(error),
                        'wire_diagnostics': wire_diagnostics(value, schema, schema) if value is not None else [],
                        'rejected_part': {'raw_json': completion.content,
                            'source_llm_run_id': str(completion.llm_run_id), 'approved': False},
                        'instructions': 'Correct only this part against all original sources and the exact part schema. Return the whole corrected part; no other part or author-fact changes.'})
                    continue
            origin = (completion.metadata or {}).get('paid_call_origin') or {}
            receipt = {'schema': POLICY, 'recipe_sha256': recipe_hash, 'part_id': part_id,
                'source_packet_sha256': source_hash, 'part_schema_sha256': _sha(schema),
                'payload_sha256': _sha(value), 'llm_run_id': str(completion.llm_run_id),
                'original_workflow_id': origin.get('workflow_run_id'),
                'finish_reason': completion.finish_reason, 'quality_approval_granted': False,
                'input_tokens': completion.input_tokens, 'output_tokens': completion.output_tokens,
                'physical_capacity': capacity}
            if part_transport is not None:receipt['part_transport'] = part_transport
            if field_repair_receipt is not None:
                receipt.update(delivery_method='base_plus_model_field_corrections',
                    field_correction_receipt=field_repair_receipt,
                    llm_run_ids=[str(completion.llm_run_id), field_repair_receipt['correction_llm_run_id']],
                    single_provider_payload_claimed=False)
            await _checkpoint(session, workflow_run_id, project.id, receipt)
            receipts.append(receipt)
            emit_activity('cast_planning_part_completed', {'part_id': part_id,
                'completed_parts': len(receipts), 'total_parts': len(people) + 1,
                'source_llm_run_id': receipt['llm_run_id'], 'approved': False, 'body_chapters': 0})
            completions[part_id] = completion
            return value
        raise AssertionError('unreachable bounded Cast part loop')

    persons = {}
    for person in people:
        value = await produce(person['id'], 'planner_cast_person_part', person_schema(person),
            {'part_id': person['id'], 'source_slot': person['source_slot'],
             'source_person': person['source'], 'required_name': person['name'],
             'instruction': 'Generate only this complete typed person from the frozen sources. Other people remain context, not additional output.'})
        persons[person['id']] = value['person']
    links = await produce('links', 'planner_cast_links_part', links_schema(people),
        {'instruction': 'Propose global antagonist forces, conflict map and a layout selecting every already-generated person exactly once. Layout IDs reference real prior parts, never new people. Do not rewrite their fields or claim approval.'},
        prior_people=[{'part_id': person['id'], 'person': persons[person['id']]} for person in people],
        extra_validator=lambda value: assemble(people, persons, value))
    payload = assemble(people, persons, links)
    documents['links']['retained_links_payload'] = deepcopy(links)
    from bestseller.services.source_cast_identity_completion import complete_missing
    payload, identity_completion = await complete_missing(
        session, settings, project=project, projection=projection, payload=payload,
        people=people, persons=persons, receipts=receipts, completions=completions,
        documents=documents, packet=packet, recipe_hash=recipe_hash,
        workflow_run_id=workflow_run_id, before_dispatch=before_dispatch)
    validate(deepcopy(payload))
    await before_dispatch()
    meta = {'source_bound_design': True, 'source_bound_minimal': False,
        'source_compiler': POLICY, 'source_content_policy': CONTENT_POLICY,
        'generation_method': 'assembled_real_model_parts',
        'generation_llm_run_ids': [run_id for receipt in receipts for run_id in receipt.get('llm_run_ids', [receipt['llm_run_id']])],
        'composite_generation_receipt': {'schema': POLICY, 'recipe_sha256': recipe_hash,
            'source_packet_sha256': source_hash, 'assembled_payload_sha256': _sha(payload),
            'parts': receipts, 'single_full_asset_provider_response_claimed': False,
            'quality_approval_granted': False},
        'source_projection_sha256': p._approved_payload_hash(projection),
        'creator_authorship_asserted': False, 'planning_status': 'generated_pending_semantic_review',
        'approved_complete': False}
    if identity_completion is not None:
        meta['composite_generation_receipt']['identity_completion'] = identity_completion
        meta['generation_llm_run_ids'].extend(row['field_correction_receipt']['correction_llm_run_id']
            for row in identity_completion['repairs'])
    payload['_meta'] = meta
    return p._with_planning_meta(payload, logical_name='cast_spec', input_hash=recipe_hash,
        workflow_run_id=workflow_run_id, project=project)

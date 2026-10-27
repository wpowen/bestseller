"""Same-paid-response reference linkage, never a new judge or native return.

Only missing mandatory references may be borrowed from the already strict,
explicitly supported independent assessment in the SAME response. Texts, grades,
body/title choices and original physical evidence remain immutable. Native
custody, fresh factory readback, unique nonpaid record and once census are outer
responsibilities. No dispatch, DB, network, source or artifact writes here.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from bestseller.services import fanqie_short_title_semantic_judge as title

SCHEMA = 'fanqie-short-title-contract-linkage.v1'
AUTHORITY = 'same_paid_response_contract_reference_linkage_not_original_row_choice_no_new_grade'
TITLE_VALIDATOR_SHA256 = '93ac0bb97a54006a0bcf2f4c1fe3ba76f93ef2ef3f7cf34eda7754f3a25b95f8'
REFERENCE_FAILURES = frozenset({
    'explicit_creator_evidence_missing',
    'canonical_story_evidence_missing',
    'title_contract_unresolved',
})
SAME_PROPOSITION_FIELDS = (
    'evidence_ids', 'body_evidence_ids', 'readable_promise', 'default_contract',
    'readability', 'creator_alignment', 'default_basis',
)


class TitleContractLinkageError(ValueError):
    pass


def _need(ok, code):
    if not ok:
        raise TitleContractLinkageError(code)


def _copy_completion(completion):
    if isinstance(completion, dict):
        value = deepcopy(completion)
    else:
        _need(callable(getattr(completion, 'model_dump', None)), 'completion_mapping_required')
        value = deepcopy(completion.model_dump(mode='json'))
    title.encoded(value)  # Nonfinite/non-JSON data cannot participate in a proof.
    return value


def _missing_mandatory_refs(plan, selected, donor):
    """All required refs, plus one donor-selected member of each empty OR group."""
    requirements = plan['contract_requirements']
    chosen = set(selected)
    additions = []
    for ident in requirements['required_contract_ids']:
        if ident not in chosen:
            _need(ident in donor, 'mandatory_reference_not_selected_by_assessment')
            additions.append((ident, 'required_contract_ids'))
            chosen.add(ident)
    for group in ('canonical_story_any_of_ids', 'explicit_creator_any_of_ids'):
        allowed = requirements[group]
        if chosen.isdisjoint(allowed):
            candidates = [ident for ident in donor if ident in allowed]
            _need(bool(candidates), 'mandatory_reference_not_selected_by_assessment')
            ident = candidates[0]  # Original donor order, no new semantic choice.
            additions.append((ident, group))
            chosen.add(ident)
    return additions


def prepare_title_contract_linkage(plan, completion):
    """Return an explicit derived proof, or fail shut without modifying inputs.

    original_failure is a string and must be reproduced by the complete pinned
    title validator. Only contract_ids are appended on not_supported rows. The
    deep-copied derived completion is run through that SAME COMPLETE function.
    Its physical/native identity must never replace the original paid return.
    """
    _need(hashlib.sha256(Path(title.__file__).read_bytes()).hexdigest() == TITLE_VALIDATOR_SHA256,
          'title_validator_source_changed')
    original = _copy_completion(completion)
    original_snapshot = deepcopy(original)
    plan_snapshot = deepcopy(plan)
    try:
        title.validate_title_response(plan, original)
    except ValueError as exc:
        # A reference-looking string from another parser/provider exception is
        # not evidence of the original strict title reference failure family.
        reference_type = title.sources._validator().ReviewInputError
        _need(isinstance(exc, reference_type) and str(exc) in REFERENCE_FAILURES,
              'original_failure_not_reference_only:' + str(exc))
        original_failure = str(exc)
    else:
        raise TitleContractLinkageError('original_title_response_already_valid')

    original_calls = original['tool_calls']
    original_arguments = original_calls[0]['function']['arguments']
    args = json.loads(original_arguments, object_pairs_hook=title.sources._validator()._pairs)
    donor = args['title_assessment']
    _need(donor['verdict'] == 'supported', 'independent_assessment_not_supported')
    try:
        title._expanded_entry(plan, donor, plan['title_assessment_item'], independent=True)
    except ValueError as exc:
        raise TitleContractLinkageError('independent_assessment_not_strict:' + str(exc)) from None

    normalized = deepcopy(args)
    contracts = {entry['id']: entry for entry in plan['contract_bank']}
    borrowed = []
    for item in plan['actual_title_rows']:
        ident = item['id']
        row = args['reviews'][ident]
        if row['verdict'] != 'not_supported':
            continue  # Valid refusals/abstentions are never changed or hidden.
        _need(all(row[key] == donor[key] for key in SAME_PROPOSITION_FIELDS),
              'finding_assessment_proposition_mismatch:' + ident)
        additions = _missing_mandatory_refs(plan, row['contract_ids'], donor['contract_ids'])
        for contract_id, group in additions:
            _need(contract_id in contracts and contract_id in donor['contract_ids'],
                  'foreign_or_unselected_contract_reference')
            contract = contracts[contract_id]
            target_index = len(normalized['reviews'][ident]['contract_ids'])
            normalized['reviews'][ident]['contract_ids'].append(contract_id)
            token = ident.replace('~', '~0').replace('/', '~1')
            borrowed.append(dict(
                target_row_id=ident, contract_id=contract_id, path=contract['path'], quote=contract['quote'],
                source_argument_pointer='/title_assessment/contract_ids/' + str(donor['contract_ids'].index(contract_id)),
                target_argument_pointer='/reviews/' + token + '/contract_ids/' + str(target_index),
                required_for=group, not_original_row_choice=True,
            ))
    _need(bool(borrowed), 'no_eligible_missing_references')
    # Prove this transformation contains no text, grade, title/body ID or other
    # field changes. Do not silently normalize a malformed or contradictory row.
    for ident, row in args['reviews'].items():
        before = {k:v for k,v in row.items() if k != 'contract_ids'}
        after = {k:v for k,v in normalized['reviews'][ident].items() if k != 'contract_ids'}
        _need(before == after, 'non_reference_change_forbidden')
        _need(normalized['reviews'][ident]['contract_ids'][:len(row['contract_ids'])] == row['contract_ids'],
              'original_reference_order_changed')
    _need(normalized['title_assessment'] == args['title_assessment'], 'independent_assessment_changed')
    derived = deepcopy(original)
    derived_arguments = title.encoded(normalized)
    derived['tool_calls'][0]['function']['arguments'] = derived_arguments
    # Keep the copied assistant message coherent, but explicitly identify this
    # whole copy as derived data, NOT as original raw_message or a provider send.
    if isinstance(derived.get('raw_message'), dict) and 'tool_calls' in derived['raw_message']:
        derived['raw_message']['tool_calls'] = deepcopy(derived['tool_calls'])
    try:
        strict = title.validate_title_response(plan, derived)
    except ValueError as exc:
        raise TitleContractLinkageError('derived_full_strict_validation_failed:' + str(exc)) from None
    _need(strict['title_assessment']['verdict'] == 'supported', 'derived_assessment_not_supported')
    _need(plan == plan_snapshot and original == original_snapshot, 'linkage_mutated_original_input')
    return dict(
        schema=SCHEMA, plan_binding_sha256=plan['binding_sha256'], original_failure=original_failure,
        original_tool_arguments_sha256=title.sha(original_arguments),
        original_tool_calls_sha256=title.digest(original_calls), original_completion_sha256=title.digest(original),
        borrowed_contract_refs=borrowed,
        normalized_derived_input=dict(completion=derived, tool_arguments=normalized, not_original_provider_return=True),
        derived_tool_arguments_sha256=title.sha(derived_arguments),
        derived_tool_calls_sha256=title.digest(derived['tool_calls']), derived_completion_sha256=title.digest(derived),
        strict_validation=strict, authority=AUTHORITY,
        semantic_limitations=dict(
            reference_linkage_only=True, original_finding_selected_these_refs=False,
            no_new_grade=True, no_new_provider_call=True, semantic_texts_unchanged=True,
            semantic_overclaims_not_repaired=True, native_editorial_debt_unchanged=True,
            human_attraction_acceptance='not_performed', native_custody_and_once_are_outer=True,
            no_whole_book_or_export_authority=True,
        ),
    )

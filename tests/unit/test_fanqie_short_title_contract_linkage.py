"""Retained paid-title shape; pure linkage, zero DB/network/provider calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest
from sqlalchemy.engine import Engine
from bestseller.services import fanqie_short_title_contract_linkage as linkage

pytestmark=pytest.mark.unit
ROOT=Path(__file__).resolve().parents[2]
SAVED=ROOT/'reports/project-reliability-review-2026-10-24/implementation/original-four-title-tool-execution.json'


@pytest.fixture(scope='module')
def saved():return json.loads(SAVED.read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a,**k):raise AssertionError('DB/network/provider forbidden')
    monkeypatch.setattr(Engine,'connect',forbidden);monkeypatch.setattr(sqlite3,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(linkage.title.current,'_native_complete_text',forbidden)


def args_of(completion):return json.loads(completion['tool_calls'][0]['function']['arguments'])


def replace_args(completion,args):
    c=deepcopy(completion);c['tool_calls'][0]['function']['arguments']=linkage.title.encoded(args)
    if isinstance(c.get('raw_message'),dict) and 'tool_calls' in c['raw_message']:
        c['raw_message']['tool_calls']=deepcopy(c['tool_calls'])
    return c


@pytest.fixture(scope='module')
def proof(saved):
    # This is derived data, not a new native provider response or grade.
    return linkage.prepare_title_contract_linkage(saved['request_payload'],saved['original_completion'])


def test_actual_reference_only_failure_borrows_only_selected_mandatory_refs(saved,proof):
    assert proof['schema']==linkage.SCHEMA
    assert type(proof['original_failure']) is str and proof['original_failure']=='explicit_creator_evidence_missing'
    assert proof['authority']=='same_paid_response_contract_reference_linkage_not_original_row_choice_no_new_grade'
    borrowed=proof['borrowed_contract_refs']
    assert {r['contract_id'] for r in borrowed}=={'k124','k295','k298'}
    original=args_of(saved['original_completion']);key=next(iter(original['reviews']))
    for ref in borrowed:
        assert ref['target_row_id']==key and ref['not_original_row_choice'] is True
        assert ref['contract_id'] in original['title_assessment']['contract_ids']
        assert ref['contract_id'] not in original['reviews'][key]['contract_ids']
    assert proof['strict_validation']['title_assessment']['verdict']=='supported'
    assert proof['strict_validation']['reviews'][0]['verdict']=='not_supported'
    assert proof['strict_validation']['editorial_quality_debt'][0]['status']=='open'
    assert proof['strict_validation']['editorial_quality_debt'][0]['overall_score']==52


def test_original_and_derived_hashes_are_explicit_and_inputs_immutable(saved,proof):
    c=saved['original_completion'];raw=c['tool_calls'][0]['function']['arguments']
    assert proof['original_tool_arguments_sha256']==hashlib.sha256(raw.encode()).hexdigest()
    assert proof['original_tool_calls_sha256']==linkage.title.digest(c['tool_calls'])
    assert proof['original_completion_sha256']==linkage.title.digest(c)
    assert proof['derived_tool_arguments_sha256']!=proof['original_tool_arguments_sha256']
    assert proof['derived_tool_calls_sha256']!=proof['original_tool_calls_sha256']
    assert proof['derived_completion_sha256']!=proof['original_completion_sha256']
    derived=proof['normalized_derived_input'];assert derived['not_original_provider_return'] is True
    assert derived['completion']['llm_run_id']==c['llm_run_id']
    original=args_of(c);normalized=derived['tool_arguments']
    assert normalized['title_assessment']==original['title_assessment']
    for key,row in original['reviews'].items():
        assert {k:v for k,v in row.items() if k!='contract_ids'}=={k:v for k,v in normalized['reviews'][key].items() if k!='contract_ids'}
    assert saved==json.loads(SAVED.read_text())


def test_full_original93_validator_accepts_explicit_derived_copy_and_original_still_fails(saved,proof):
    p=saved['request_payload']
    checked=linkage.title.validate_title_response(p,proof['normalized_derived_input']['completion'])
    assert checked==proof['strict_validation']
    with pytest.raises(ValueError,match='explicit_creator_evidence_missing'):
        linkage.title.validate_title_response(p,saved['original_completion'])


@pytest.mark.parametrize('family',['required_all','canonical_any'])
def test_generic_other_legal_missing_ids_are_not_book_hardcoded(saved,family):
    p=saved['request_payload'];c=saved['original_completion'];a=args_of(c);row=next(iter(a['reviews'].values()));donor=a['title_assessment']['contract_ids']
    row['contract_ids']=deepcopy(donor)
    if family=='required_all':
        missing=p['contract_requirements']['required_contract_ids'][0]
        row['contract_ids'].remove(missing);expected={missing};error='title_contract_unresolved'
    else:
        allowed=p['contract_requirements']['canonical_story_any_of_ids']
        row['contract_ids']=[i for i in row['contract_ids'] if i not in allowed]
        expected={next(i for i in donor if i in allowed)};error='canonical_story_evidence_missing'
    result=linkage.prepare_title_contract_linkage(p,replace_args(c,a))
    assert {r['contract_id'] for r in result['borrowed_contract_refs']}==expected
    assert result['original_failure']==error


@pytest.mark.parametrize('mutation,expected',[
    ('donor_missing_required','independent_assessment_not_strict'),
    ('donor_missing_creator','independent_assessment_not_strict'),
    ('foreign_donor','original_failure_not_reference_only'),
    ('foreign_finding','original_failure_not_reference_only'),
    ('donor_unsupported','independent_assessment_not_supported'),
    ('finding_gradechanged','finding_assessment_proposition_mismatch'),
    ('finding_body_changed','finding_assessment_proposition_mismatch'),
    ('finding_body_missing','finding_assessment_proposition_mismatch'),
    ('donor_body_missing','original_failure_not_reference_only'),
    ('promise_changed','finding_assessment_proposition_mismatch'),
    ('default_changed','original_failure_not_reference_only'),
])
def test_insufficient_foreign_nonreference_or_changed_proposition_is_not_repaired(saved,mutation,expected):
    p=saved['request_payload'];c=saved['original_completion'];a=args_of(c);donor=a['title_assessment'];row=next(iter(a['reviews'].values()))
    if mutation=='donor_missing_required':donor['contract_ids'].remove(p['contract_requirements']['required_contract_ids'][-1])
    if mutation=='donor_missing_creator':donor['contract_ids']=[i for i in donor['contract_ids'] if i not in p['contract_requirements']['explicit_creator_any_of_ids']]
    if mutation=='foreign_donor':donor['contract_ids'].append('foreign')
    if mutation=='foreign_finding':row['contract_ids'].append('foreign')
    if mutation=='donor_unsupported':donor['verdict']='uncertain'
    if mutation=='finding_gradechanged':row['readability']='not_supported'
    if mutation=='finding_body_changed':row['body_evidence_ids']=row['body_evidence_ids'][:1]
    if mutation=='finding_body_missing':row['body_evidence_ids']=[]
    if mutation=='donor_body_missing':donor['body_evidence_ids']=[]
    if mutation=='promise_changed':row['readable_promise']+='另一项并未相同的额外承诺'
    if mutation=='default_changed':row['default_contract']='different template'
    c2=replace_args(c,a);before=deepcopy((p,c2))
    with pytest.raises(linkage.TitleContractLinkageError,match=expected):
        linkage.prepare_title_contract_linkage(p,c2)
    assert (p,c2)==before


def test_optional_unselected_reference_cannot_be_borrowed_and_unneeded_refs_not_copied(saved):
    p=saved['request_payload'];c=saved['original_completion'];a=args_of(c)
    optional=[i for i in a['title_assessment']['contract_ids'] if i in p['contract_requirements']['canonical_story_any_of_ids']]
    assert len(optional)>=2
    a['title_assessment']['contract_ids'].remove(optional[-1])
    result=linkage.prepare_title_contract_linkage(p,replace_args(c,a))
    assert optional[-1] not in {r['contract_id'] for r in result['borrowed_contract_refs']}
    assert all(r['contract_id'] in a['title_assessment']['contract_ids'] for r in result['borrowed_contract_refs'])


@pytest.mark.parametrize('mutation',['missing_tool','wrong_tool','free_json','wrong_binding','extra_root','truncated'])
def test_error_family_is_exact_not_any_failed_response(saved,mutation):
    p=saved['request_payload'];c=deepcopy(saved['original_completion']);a=args_of(c)
    if mutation=='missing_tool':c['tool_calls']=[]
    if mutation=='wrong_tool':c['tool_calls'][0]['function']['name']='other_function'
    if mutation=='free_json':c['content']=linkage.title.encoded(a)
    if mutation=='wrong_binding':a['binding_sha256']='0'*64;c=replace_args(c,a)
    if mutation=='extra_root':a['shared_refs']=['not-selected'];c=replace_args(c,a)
    if mutation=='truncated':c['finish_reason']='length'
    with pytest.raises(linkage.TitleContractLinkageError,match='original_failure_not_reference_only'):
        linkage.prepare_title_contract_linkage(p,c)


def test_already_valid_original_not_rejudged_or_relabelled(saved):
    p=saved['request_payload'];a=args_of(saved['original_completion'])
    for row in a['reviews'].values():row['contract_ids']=deepcopy(a['title_assessment']['contract_ids'])
    with pytest.raises(linkage.TitleContractLinkageError,match='original_title_response_already_valid'):
        linkage.prepare_title_contract_linkage(p,replace_args(saved['original_completion'],a))


def test_valid_refusal_cannot_be_flipped_into_supported_by_linkage(saved):
    p=saved['request_payload'];a=args_of(saved['original_completion']);row=next(iter(a['reviews'].values()))
    row['verdict']='confirmed';row['body_evidence_ids']=[]
    for key in ('readable_promise','contract_explanation','default_contract','readability','creator_alignment','default_basis','readability_reason','body_reason'):row[key]=''
    with pytest.raises(linkage.TitleContractLinkageError,match='original_title_response_already_valid'):
        linkage.prepare_title_contract_linkage(p,replace_args(saved['original_completion'],a))


def test_semantic_overclaims_garble_and_native52_debt_are_not_silently_repaired(saved,proof):
    original=args_of(saved['original_completion']);derived=proof['normalized_derived_input']['tool_arguments']
    assert '六十���钟' in derived['title_assessment']['reason']
    assert '一无所有' in derived['title_assessment']['readability_reason']
    assert derived['title_assessment']['readable_promise']==original['title_assessment']['readable_promise']
    assert proof['semantic_limitations']['semantic_overclaims_not_repaired'] is True
    assert proof['semantic_limitations']['human_attraction_acceptance']=='not_performed'
    assert proof['strict_validation']['editorial_quality_debt']==saved['request_payload']['editorial_quality_debt']
    assert 'passed' not in proof and 'export_authority' not in proof


def test_source_and_original_paid_file_bytes_unchanged(saved,proof):
    assert hashlib.sha256(Path(linkage.title.__file__).read_bytes()).hexdigest()==linkage.TITLE_VALIDATOR_SHA256
    expected=json.loads(SAVED.read_text())
    assert saved==expected
    assert proof['original_tool_arguments_sha256']==hashlib.sha256(expected['original_completion']['tool_calls'][0]['function']['arguments'].encode()).hexdigest()

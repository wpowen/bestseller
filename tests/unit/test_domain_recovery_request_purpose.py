"""Typed correction purpose cannot strand the following commercial review."""
# ruff: noqa: F811 -- Imported pytest fixtures are injected by argument name.
from copy import deepcopy

import pytest
from test_commercial_request_purpose import case, paid, add_run, add_transport  # noqa: F401

from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request
from bestseller.services.planning_surgical_budget import (
    DOMAIN_TEMPLATE, DOMAIN_VERSION, FLAT_DOMAIN_TEMPLATE, FLAT_DOMAIN_VERSION,
)

pytestmark=pytest.mark.unit


def configure(value,role='planner',template=DOMAIN_TEMPLATE,version=DOMAIN_VERSION):
    origin=paid(value)['metadata']['paid_call_origin']
    origin.update(logical_role=role,prompt_template=template,prompt_version=version)
    paid(value)['tool_calls']=[{'id':'typed-corrections','type':'function','function':
        {'name':'submit_outline_domain_corrections','arguments':'{"corrections":{}}'}}]
    run=add_run(value)
    run.update(logical_role=role,prompt_template=template,prompt_version=version)
    add_transport(value)


@pytest.mark.parametrize('template,version',[
    (DOMAIN_TEMPLATE,DOMAIN_VERSION),(FLAT_DOMAIN_TEMPLATE,FLAT_DOMAIN_VERSION),
])
def test_exact_current_producer_purpose_preserves_all_old_nullable_attribution(case,template,version):
    configure(case,template=template,version=version)
    before=deepcopy(case)
    assert prove_known_noncommercial_request(**case)
    assert case==before
    assert paid(case)['metadata']['paid_call_origin']['project_id'] is None


@pytest.mark.parametrize('changes',[
    {'role':'critic'}, {'role':'editor'}, {'template':DOMAIN_TEMPLATE+'_other'},
    {'version':None}, {'version':'3.5-source-complete-repair'}, {'version':'3.6-other'},
    {'version':FLAT_DOMAIN_VERSION},
    {'template':FLAT_DOMAIN_TEMPLATE},
])
def test_neighbors_unknown_version_and_other_roles_remain_unproven(case,changes):
    configure(case,**changes)
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize('tamper',['input_hash','attempt','commercial_tool','origin_conflict'])
def test_known_route_never_waives_existing_evidence_and_commercial_guards(case,tamper):
    configure(case)
    if tamper=='input_hash':case['operation']['input_hash']='0'*64
    elif tamper=='attempt':case['actual_attempts'][0]['model_name']='other-model'
    elif tamper=='commercial_tool':
        paid(case)['tool_calls'][0]['function']['name']='submit_chapter_review'
        case['transport_results'][0]['result_json']['result']=deepcopy(paid(case))
    else:case['original_runs'][0]['prompt_template']='chapter_commercial_quality_judge'
    assert prove_known_noncommercial_request(**case) is False

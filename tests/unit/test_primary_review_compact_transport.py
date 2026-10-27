"""Lossless appended decoder: exact source, old wire stability and fail-closed bounds."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from bestseller.services import primary_review_transport as t
from bestseller.services import lossless_adopted_span_encoding as spans
from bestseller.services import lossless_source_encoding as pointers
from bestseller.services.prompt_compiler import PromptBudgetError

pytestmark=pytest.mark.unit
ROOT=Path(__file__).resolve().parents[2]


def captured():
    c=json.loads((ROOT/'tests/fixtures/primary_review_capacity/original_four_v10.json').read_text())
    return dict(system_prompt=c['system'],user_prompt=c['user'],source_context=c['source_context'],tools=c['tools'],output_tokens=c['output_tokens'])


def verify(args,result):
    t.verify_primary_review_transport(result[0],result[1],source_context=args['source_context'],receipt=result[2],tools=args['tools'],output_tokens=args['output_tokens'])


def test_actual_previously_unsent_request_fits_without_dropping_source_or_task():
    args=captured();before=deepcopy(args)
    sy,us,receipt=t.prepare_source_complete_prompt(**args)
    proof=receipt['primary_source_transport'];cap=proof['capacity']
    assert proof['encoding']=='adopted-spans-v4-compact'
    assert cap['budget_passed'] and cap['truncated'] is False
    assert cap['total_token_budget']==65536 and cap['token_safety_factor']==1.5 and cap['output_reserve_tokens']==8192
    assert cap['reserved_input_tokens']+8192<=65536 and cap['combined_characters']<=120000
    encoded=json.loads(us[:proof['packet_end']])
    assert spans.decode_adopted_spans(pointers.decode_lossless_source(encoded))==args['source_context']
    assert us[proof['packet_end']:]=='\n\n'+args['user_prompt']
    assert args['system_prompt'] in sy and args==before
    assert verify(args,(sy,us,receipt)) is None


@pytest.fixture
def compact_candidate(monkeypatch):
    # Force only source selection to overflow older wire dialects; capacity,
    # encoded receipts and verification still use real conservative arithmetic.
    body=''.join(chr(0x4e00+(n%2000)) for n in range(2000))
    source={'adopted_continuity':{'recent_adopted_prose':[{'body':body}]},'other':body[100:600],'copies':[{'excerpt':body[100:600],'i':i} for i in range(12)]}
    args=dict(system_prompt='review original',user_prompt='CURRENT_DRAFT\n'+body,source_context=source,tools=None,output_tokens=8192)
    old=t._capacity
    def capacity(system,user,tools,output_tokens):
        value=old(system,user,tools,output_tokens)
        if t._INSTRUCTIONS[t._COMPACT_ENCODING] not in system:
            value['budget_passed']=False
        return value
    monkeypatch.setattr(t,'_capacity',capacity)
    return args,t.prepare_source_complete_prompt(**args)


@pytest.mark.parametrize('part',['task','source','tools','output','packet','instructions','notice','encoding'])
def test_compact_wire_rejects_changed_or_spliced_inputs(compact_candidate,part):
    args,result=deepcopy(compact_candidate);sy,us,r=result;proof=r['primary_source_transport']
    if part=='task':us+=' changed'
    elif part=='source':args['source_context']['other']+=' changed'
    elif part=='tools':args['tools']=[{'type':'function','function':{'name':'other'}}]
    elif part=='output':args['output_tokens']=8191
    elif part=='packet':us=us[:5]+' '+us[5:]
    elif part=='instructions':sy=sy.replace('end exclusive','end inclusive')
    elif part=='notice':sy=sy.replace('authority is unchanged','authority is replaced')
    else:proof['encoding']='adopted-spans-v4'
    with pytest.raises((ValueError,PromptBudgetError)):
        verify(args,(sy,us,r))


def test_genuinely_oversized_task_still_blocks_even_compact(monkeypatch):
    from test_primary_review_transport import arguments
    args=arguments({'fact':'full unchanged source'})
    args['user_prompt']='x'*200000
    with pytest.raises(PromptBudgetError):t.prepare_source_complete_prompt(**args)

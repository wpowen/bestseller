"""Unrelated outline values: exact syntax derivation, never source/grade repair."""
import hashlib
import pytest
from bestseller.services import chapter_outline_readiness as ready

@pytest.mark.unit
@pytest.mark.parametrize('raw',[
    '{"chapters":[{"chapter_number":7,"goal":"Inspect the telescope."}}]}',
    '{"chapters":[{"chapter_number":19,"goal":"检查潮汐仪。"}}]}',
    '{"observations":{"items":[["a","b"]]}}}',
])
def test_extra_closer_has_explicit_derived_receipt(raw):
    # The third shape has surplus root closers and must remain unknown.
    if raw.endswith('}}}'):
        with pytest.raises(ValueError):ready._parse_prefix_json(raw)
        return
    original=hashlib.sha256(raw.encode()).hexdigest()
    payload,receipt=ready._parse_prefix_json(raw)
    assert payload['chapters'][0]['chapter_number'] in [7,19]
    assert receipt['raw_sha256']==original
    assert receipt['original_protocol_status']=='unknown'
    assert receipt['adapter_policy']=='prefix-json.repeated-mismatched-closers.v1'
    assert receipt['grade_asserted'] is False and receipt['original_response_rewritten'] is False
    assert len(receipt['removed_closers'])==1 and receipt['inserted_closers']==[]
    assert ready._parse_prefix_json(raw)==(payload,receipt)

@pytest.mark.unit
@pytest.mark.parametrize('raw',[
    '{"chapters":[{"goal":"unterminated}}]}',
    '{"chapters":[{"goal":"x"},]}',
    '{"chapters":[{"goal":"x","goal":"y"}}]}',
    '{"chapters":[{"goal":NaN}}]}',
    '{"chapters":[{"goal":"x"}}]',
    '{"chapters":[{"goal":"x"}]} garbage',
    '{"chapters":[{"goal":"x"}}}],"extra":1}',
    '{"chapters":[{"goal":"x"}}]} {"second":true}',
])
def test_unproven_or_incomplete_response_stays_unknown(raw):
    with pytest.raises(ValueError):ready._parse_prefix_json(raw)

@pytest.mark.unit
def test_old_unknown_receipt_and_raw_completion_retained():
    raw='{"chapters":[{"chapter_number":7,"goal":"Map the northern coast."}}]}'
    previous={'policy':ready._PREFIX_JSON_POLICY,'status':'unknown','reason':'prefix_json_protocol_unknown'}
    attempt={'completion':{'content':raw,'finish_reason':'stop'},'json_parse_receipt':previous.copy()}
    result=ready._parse_prefix_attempt(attempt)
    assert result['chapters'][0]['goal']=='Map the northern coast.'
    assert attempt['completion']['content']==raw
    assert attempt['json_parse_receipt_history']==[previous]
    ready._parse_prefix_attempt(attempt)
    assert attempt['json_parse_receipt_history']==[previous]

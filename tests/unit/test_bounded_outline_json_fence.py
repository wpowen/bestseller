import json
import pytest
from bestseller.services.bounded_outline_review import validate_primary

pytestmark=pytest.mark.unit


def verdict():
    return {'pass': False, 'overall_score': .42, 'dimension_scores': {'continuity': .5}, 'blocking_issues': []}


@pytest.mark.parametrize('fence',['```json\n{}\n```','```\n{}\n```',' \n```json\r\n{}\r\n```\n'])
def test_single_fence_preserves_exact_negative_and_original(fence):
    raw=fence.format(json.dumps(verdict()));before=raw
    assert validate_primary(raw)==verdict() and raw==before


@pytest.mark.parametrize('payload',[
    'prefix\n```json\n{}\n```', '```json\n{}\n```\nsuffix',
    '```json\n{}\n```\n```json\n{}\n```', '```python\n{}\n```',
    '```json\n{} {}\n```', '```json\n{"pass":false,"pass":true}\n```',
    '```json\n{"pass":NaN}\n```', '```json\n[]\n```',
])
def test_wrapping_never_permits_ambiguous_or_invalid_data(payload):
    with pytest.raises((ValueError,TypeError)):
        validate_primary(payload.replace('{}',json.dumps(verdict())))

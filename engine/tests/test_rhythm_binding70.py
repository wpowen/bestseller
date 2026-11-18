"""An incomplete stage skeleton must not erase an already complete relationship contract."""

from copy import deepcopy

import pytest

from bookengine.rhythm_skeleton import bind_stage_content


CONTRACT = dict(people=["江宁", "梁秋"], cause="隐瞒伤势", before="猜疑", after="坦白后同行")


@pytest.mark.parametrize("missing", [{}, None, {"people": ["江宁", "梁秋"]}])
def test_incomplete_stage_contract_preserves_complete_detailed_contract(missing):
    detailed = [dict(number=4, type="情感", relationship_change=deepcopy(CONTRACT))]
    skeleton = [dict(number=4, rhythm_type="情感", relationship_change=missing)]
    before = deepcopy((detailed, skeleton))
    bound = bind_stage_content(detailed, skeleton)
    assert bound[0]["relationship_change"] == CONTRACT
    assert (detailed, skeleton) == before


def test_complete_stage_contract_remains_authoritative():
    preferred = dict(CONTRACT, cause="当面交还钥匙", after="接受道歉")
    detailed = [dict(number=4, type="情感", relationship_change=deepcopy(CONTRACT))]
    skeleton = [dict(number=4, rhythm_type="情感", relationship_change=preferred)]
    assert bind_stage_content(detailed, skeleton)[0]["relationship_change"] == preferred

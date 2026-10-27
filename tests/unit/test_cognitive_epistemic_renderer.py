"""Unresolved model classifications remain visible without becoming actor hard law."""
from hashlib import sha256

import pytest

from bestseller.services.drafts import _render_knowledge_state_section
from bestseller.services.knowledge_epistemic_admission import admit_cognitive_update, merge_cognitive_admission

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('is_en', [False, True])
@pytest.mark.parametrize('known', [[], ['The sealed ledger remains in the archive.']])
def test_unresolved_classification_and_whole_source_context_are_separate_from_must_obey(is_en,known):
    body='“The vault was empty,” said Mira.\n\n“I saw sealed crates,” Jonas replied.\n\nNeither produced a record.'
    candidate={'name':'Mira','false_beliefs':['Jonas corrected Mira; the vault was not empty.']}
    view=admit_cognitive_update(update=candidate,item_path='/character_updates/0',
        source_lineage={'schema':'adopted-knowledge.v1','source_body_sha256':sha256(body.encode()).hexdigest()},
        source_body=body,application_payload={'character_updates':[candidate]})
    state=merge_cognitive_admission({'knows':known},view)
    rendered=_render_knowledge_state_section([{'character_name':'Mira',**state}],is_en=is_en)
    marker='=== Unverified cognitive interpretations' if is_en else '=== 待核验认知记录'
    head,uncertain=rendered.split(marker,1)
    assert 'Jonas corrected Mira' not in head and 'Jonas corrected Mira' in uncertain
    assert 'Falsely believes' not in head and '错误相信' not in head
    assert 'The vault was empty' in uncertain and 'I saw sealed crates' in uncertain
    assert 'Neither produced a record' in uncertain
    assert state.get('falsely_believes',[])==[]
    if known:
        assert known[0] in head
        assert ('Characters must NOT' if is_en else '角色的对话和行为不得超越其认知边界') in head
    else:
        assert 'MUST obey' not in head and '写作必须遵守' not in head


@pytest.mark.parametrize('is_en', [False, True])
def test_manual_or_explicit_author_baseline_false_beliefs_are_not_dropped(is_en):
    authored='The navigator believes the beacon is still powered.'
    rendered=_render_knowledge_state_section([{'character_name':'Navigator','falsely_believes':[authored]}],is_en=is_en)
    assert authored in rendered
    assert ('Falsely believes' if is_en else '错误相信') in rendered
    assert 'Unverified cognitive interpretations' not in rendered and '待核验认知记录' not in rendered

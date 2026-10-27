"""A prefix evaluation scope survives optional summary truncation."""
from bestseller.services.outline_llm_judge import render_outline_source_brief


def test_prefix_scope_cannot_be_truncated_with_optional_summary():
    source={'optional':'x'*20000,'prefix_evaluation_scope':{'approved_scope_only':[1,2,3],
        'full_window':[1,9], 'tail_status':'unapproved'}}
    prompt=render_outline_source_brief(source,max_summary_chars=50)
    assert 'OPTIONAL_SUMMARY_TRUNCATED' in prompt
    assert '[EVALUATION SCOPE]' in prompt
    assert '"approved_scope_only": [1, 2, 3]' in prompt
    assert '本次只裁决提供的连续前缀' in prompt
    assert source['prefix_evaluation_scope']['full_window']==[1,9]

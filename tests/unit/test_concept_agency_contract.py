"""Agency must be judged from a story's choices, not a mandatory opening win."""

import pytest

from bestseller.services.concept_tournament import (
    _build_raw_idea_pool_messages,
    _build_raw_idea_rank_messages,
)


@pytest.mark.parametrize(('genre', 'sub_genre'), [('现实', '现实百态'), ('仙侠', '修真'), ('悬疑', '推理')])
def test_agency_does_not_require_a_completed_win(genre, sub_genre):
    _, prompt = _build_raw_idea_pool_messages(
        genre=genre, sub_genre=sub_genre, count=12, prompt_arm='author_pitch')
    assert '已经做成了一件' not in prompt
    assert '它当场兑现的' not in prompt
    assert '被动遭遇可以作为起点' in prompt
    assert '选择为什么值得他冒险' in prompt


def test_ranker_reads_the_evidence_it_is_asked_to_judge():
    seed = 'A mediator must choose which promise she can keep.'
    pitch = {'graft': 'One commitment affects another.',
             'opening': 'She postpones a decision to hear both sides.',
             'why_it_keeps_moving': 'Each compromise changes who trusts her.',
             'future_situations': ['A private appeal', 'A public vote', 'A broken promise']}
    _, prompt = _build_raw_idea_rank_messages(
        genre='现实', sub_genre='现实百态', ideas=[('人际困局', seed)],
        pitch_by_seed={seed: pitch})
    for value in (pitch['opening'], pitch['why_it_keeps_moving'], *pitch['future_situations']):
        assert value in prompt
    assert '承诺的爽的类型（数值碾压' not in prompt
    assert '不能因尚未获胜' in prompt

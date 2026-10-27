"""Selected story value must survive normal and quarantined prompt handoffs."""

from copy import deepcopy

import pytest

from bestseller.services import conception
from bestseller.services.concept_tournament import (
    ConceptCandidate,
    ConceptTournamentResult,
    render_high_concept_block,
)

pytestmark = pytest.mark.unit


@pytest.fixture(params=[
    ("悬疑", "愿意相信亲近的人", "把熟悉误当成了解", "理解与疑问一起加深"),
    ("言情", "被当作平等伙伴", "总替对方做决定", "相处带来信任"),
    ("种田", "让邻里重新坐到一桌", "不肯承认自己也需要帮助", "日常中的陪伴与改变"),
])
def selected_story(request):
    genre, desire, flaw, promise = request.param
    winner = ConceptCandidate(
        dimension=genre,
        concept="一位离乡者回来完成一份没有兑现的约定。",
        mechanism="每次履约都改变参与者的关系和下一步可做的事情。",
        protagonist_identity="回乡的手艺人",
        protagonist_private_desire=desire,
        protagonist_flaw=flaw,
        emotional_promise=promise,
        core_promise_invariant="重要选择必须改变彼此的信任",
        endgame_direction="完成约定但放弃替所有人作决定",
        ch50="中段先面对关系破裂",
        judge_reason="CONTROL_PLANE_REASON_MUST_NOT_LEAK",
        seriality_judge={"reason": "CONTROL_PLANE_REPORT_MUST_NOT_LEAK"},
    )
    return genre, winner


def test_selected_person_and_payoff_survive_normal_handoff(selected_story):
    _, winner = selected_story
    block = render_high_concept_block(ConceptTournamentResult(winner=winner))
    for value in (
        winner.protagonist_identity,
        winner.protagonist_private_desire,
        winner.protagonist_flaw,
        winner.emotional_promise,
        winner.core_promise_invariant,
        winner.endgame_direction,
        winner.ch50,
    ):
        assert value in block
    assert "CONTROL_PLANE" not in block


@pytest.mark.parametrize("is_en", [False, True])
@pytest.mark.parametrize("seed", ["", "希望人物在相处中重新建立信任"])
def test_quarantined_retry_keeps_selected_story_not_rejected_proposals(
    selected_story, is_en, seed,
):
    genre, winner = selected_story
    ctx = {
        "genre": genre,
        "sub_genre": "",
        "chapter_count": 80,
        "description": winner.concept,
        "language": "en" if is_en else "zh-CN",
        "user_hints": {"concept_seed": seed} if seed else {},
        "high_concept": winner.to_dict(),
        "commercial_brief": {"reader_promise": "REJECTED_INTERMEDIATE_PROMISE"},
        "creative_hook": "REJECTED_INTERMEDIATE_HOOK",
    }
    before = deepcopy(ctx)
    prompt = conception._pollution_retry_finalize_prompt(
        ctx, genre_profile=None, is_en=is_en,
    )
    for value in (
        winner.protagonist_private_desire,
        winner.protagonist_flaw,
        winner.emotional_promise,
        winner.core_promise_invariant,
        winner.endgame_direction,
    ):
        assert value in prompt
    assert "REJECTED_INTERMEDIATE" not in prompt
    assert "CONTROL_PLANE" not in prompt
    assert ctx == before
    if seed:
        assert seed in prompt


def test_legacy_candidate_without_human_fields_does_not_get_invented_defaults():
    winner = ConceptCandidate(dimension="legacy", concept="A premise", mechanism="A mechanism")
    block = render_high_concept_block(ConceptTournamentResult(winner=winner))
    assert "A premise" in block
    assert "私人欲望：" not in block  # noqa: RUF001 - exact Chinese prompt label
    assert "人物缺陷：" not in block  # noqa: RUF001 - exact Chinese prompt label
    assert "情绪承诺：" not in block  # noqa: RUF001 - exact Chinese prompt label

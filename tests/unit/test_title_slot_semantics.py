"""Title templates must not cast an entity as their own action/result."""
# ruff: noqa: RUF001

import pytest

from bestseller.services.platform_title_workflow import (
    PLATFORM_TITLE_STYLES,
    _build_story_methodology_topup_candidates,
    _story_anchor_groups,
    evaluate_platform_title_candidate,
    title_readability_issue,
)


def profile():
    return {
        'primary_title': '街口维修铺',
        'primary_category': '现实',
        'logline': '师傅经营“巷尾修表”账号，不缺上门量，只想教会徒弟修表。',
        'title_anchor_groups': {
            'identity': ['师傅'],
            'object': ['巷尾修表'],
            'action': ['巷尾修表'],
            'stakes': ['缺上门量'],
        },
    }


@pytest.mark.unit
def test_duplicate_method_and_result_is_rejected_without_tag_dependencies():
    title = '开局接到急单，我用巷尾修表逼出巷尾修表'
    assert title_readability_issue(title) == 'repeated_method_result'
    review = evaluate_platform_title_candidate(profile(), title)
    assert review['checks']['title_quality']['passed'] is False


@pytest.mark.unit
def test_entity_only_actions_do_not_trigger_fabricated_topup():
    rows = _build_story_methodology_topup_candidates(profile(), PLATFORM_TITLE_STYLES['fanqie'], 5)
    assert rows == []


@pytest.mark.unit
def test_quoted_entity_is_not_an_action_even_when_object_slot_differs():
    data = profile()
    data['title_anchor_groups']['object'] = ['工具箱']
    assert _build_story_methodology_topup_candidates(data, PLATFORM_TITLE_STYLES['fanqie'], 5) == []


@pytest.mark.unit
def test_negative_predicate_is_not_reversed_into_positive_stakes():
    assert _story_anchor_groups(profile()).get('stakes', []) == []


@pytest.mark.unit
def test_distinct_action_remains_available():
    data = profile()
    data['title_anchor_groups']['action'] = ['追回货款']
    data['title_anchor_groups']['stakes'] = ['赔偿']
    assert _build_story_methodology_topup_candidates(data, PLATFORM_TITLE_STYLES['fanqie'], 5)

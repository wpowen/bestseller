"""Explicitly denied story devices must not become affirmative title slots."""

# Chinese punctuation is the input under test.
# ruff: noqa: RUF001

import pytest

from bestseller.services.platform_title_workflow import (
    PLATFORM_TITLE_STYLES,
    _build_story_methodology_topup_candidates,
    _signal_tokens,
    _story_anchor_groups,
    is_story_grounded_token,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ('token', 'prose', 'expected'),
    [
        ('系统', '她的优势不是系统、不是异能，而是果园的采购经验。', False),
        ('异能', '她的优势不是系统、不是异能，而是果园的采购经验。', False),
        ('读心术', '侦探没有读心术，只靠账本找到证据。', False),
        ('契约', '世界中禁止使用契约，争端靠谈判解决。', False),
        ('系统', '他没有系统，但妹妹拥有系统。', True),
        ('系统', '她拥有系统，用它整理订单。', True),
        ('系统', '她不是没有系统，只是不想依赖它。', True),
        ('系统', '', True),
        ("缺订单", "小店不缺订单，只缺帮手。", False),
        ("依赖系统", "她不依赖系统，只靠采购经验。", False),
        ("缺订单", "小店不是不缺订单，只是更缺帮手。", True),
        ("系统", "她不得不使用系统处理订单。", True),
        ("系统", "她不但拥有系统，还擅长采购。", True),
    ],
)
def test_only_affirmative_occurrences_ground_title_tokens(token, prose, expected):
    assert is_story_grounded_token(token, prose) is expected


def _profile():
    return {
        'primary_title': '巷口果摊',
        'primary_category': '现实',
        'secondary_category': '生活',
        'logline': '全职母亲经营果摊。她的优势不是系统、不是异能，而是采购经验。',
        'tags': ['系统', '异能', '果摊'],
        'title_anchor_groups': {
            'identity': ['全职母亲'],
            'object': ['系统', '异能', '果摊'],
            'action': ['追回货款'],
            'stakes': ['亏损'],
        },
    }


@pytest.mark.unit
def test_negated_devices_do_not_enter_signals_or_explicit_topup_anchors():
    profile = _profile()
    assert _signal_tokens(profile)['object'] == '果摊'
    assert _story_anchor_groups(profile)['object'] == ['果摊']
    candidates = _build_story_methodology_topup_candidates(
        profile, PLATFORM_TITLE_STYLES['fanqie'], 5,
    )
    assert candidates
    assert all('系统' not in row['title'] and '异能' not in row['title'] for row in candidates)


@pytest.mark.unit
def test_denial_late_in_premise_is_not_lost_to_display_truncation():
    profile = _profile()
    profile['logline'] = '全职母亲经营果摊。'
    profile['premise'] = '邻居们每天来买水果。' * 20 + '她的优势不是系统、不是异能，而是采购经验。'
    assert _story_anchor_groups(profile)['object'] == ['果摊']


@pytest.mark.unit
def test_empty_filtered_groups_do_not_restore_denied_dna_terms():
    profile = _profile()
    profile["title_anchor_groups"] = {"object": ["系统", "异能"]}
    profile["story_title_dna"] = {
        "identity": "系统",
        "central_action": "异能",
        "stakes": "系统",
    }
    groups = _story_anchor_groups(profile)
    assert all("系统" not in values and "异能" not in values for values in groups.values())


@pytest.mark.unit
def test_old_polluted_title_cannot_reintroduce_denied_device():
    profile = _profile()
    profile["primary_title"] = "开局果摊，我用系统追回货款"
    assert _signal_tokens(profile)["object"] == "果摊"

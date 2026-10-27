# Chinese source-evidence fixtures intentionally preserve native punctuation.
# ruff: noqa: RUF001
from types import SimpleNamespace

import pytest

from bestseller.services.drafts import _render_chapter_first_prior_chapter_bridge


def packet(facts, tail=''):
    return SimpleNamespace(chapter_number=2, hard_fact_snapshot={'facts': facts},
                           previous_scene_summaries=[{'closing_lines': tail}] if tail else [])


@pytest.mark.parametrize('subject,value', [
    ('修理员甲', '已验收交付'), ('旅人乙', '已穿过星门'), ('姐姐丙', '已签收家书'),
])
def test_bridge_does_not_invent_an_event_for_another_genre(subject, value):
    text = _render_chapter_first_prior_chapter_bridge(packet([
        {'subject': subject, 'name': '当前状态', 'value': value, 'source_quote': value},
    ]))
    assert value in text
    assert '镜面带走' not in text
    assert '角色陈述' in text and '已确认事实' in text


def test_bridge_preserves_actual_supernatural_source_without_expanding_it():
    source = '旅人已被星门带走，去向未明'
    text = _render_chapter_first_prior_chapter_bridge(packet([
        {'subject': '旅人', 'name': '状态', 'value': source, 'source_quote': source},
    ]))
    assert source in text
    assert '镜面' not in text


def test_bridge_keeps_supplied_fact_order_without_mystery_keyword_priority():
    facts = [{'name': f'进度{i}', 'value': str(i)} for i in range(10)]
    facts.append({'name': '旧信', 'value': '已归档'})
    text = _render_chapter_first_prior_chapter_bridge(packet(facts))
    assert '进度9' in text
    assert '旧信' not in text


def test_bridge_keeps_latest_ending_when_tail_evidence_exceeds_budget():
    ending = '最终验收完成，双方签字交付，钥匙已经归还。'
    text = _render_chapter_first_prior_chapter_bridge(packet([], '较早动作。' * 300 + ending))
    assert ending in text
    assert len(text) < 1500

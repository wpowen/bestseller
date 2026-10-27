"""Planning title anchors come from each story, not one profession's lexicon."""
# ruff: noqa: RUF001
from types import SimpleNamespace

import pytest

from bestseller.services.planner import _build_story_grounded_title_profile
from bestseller.services.platform_title_workflow import _passes_story_anchor_contract

pytestmark = pytest.mark.unit


def profile(premise, protagonist, *, identity='', motion=''):
    project = SimpleNamespace(
        metadata_json={'hook_card': {'story_motion': motion}}, language='zh-CN',
        title='待修订', genre='', sub_genre='',
    )
    return _build_story_grounded_title_profile(
        project, premise=premise, book_spec_payload={},
        cast_spec_payload={'protagonist': {'name': protagonist, 'identity': identity}},
        story_design_payload={}, volume_plan_payload=[],
    )


@pytest.mark.parametrize(('premise', 'name', 'identity', 'motion', 'title', 'unrelated'), [
    ('陈远干了十九年钟表维修，经营“街口修表”账号。师徒规矩写着学徒未出师不能定价。',
     '陈远', '', '修复旧钟', '我在街口修表带徒弟', '我在天宫渡劫封神'),
    ('柳青身为剑修，靠落霞剑神通争夺山门继承权。',
     '柳青', '剑修', '夺回山门', '柳青夺回山门', '街坊求我免费修空调'),
    ('沈宁是档案员，凭“失踪者来信”追查港口纵火案。',
     '沈宁', '档案员', '追查纵火案', '我靠失踪者来信破案', '闭关百年剑开天门'),
])
def test_cross_genre_source_anchors_admit_related_titles_only(
    premise, name, identity, motion, title, unrelated,
):
    result = profile(premise, name, identity=identity, motion=motion)
    groups = result['title_anchor_groups']
    assert groups['action'] or groups['object'] or groups['stakes']
    assert _passes_story_anchor_contract(result, title)
    assert not _passes_story_anchor_contract(result, unrelated)
    assert not any('验房' in term for values in groups.values() for term in values)
    if not identity:
        assert groups['identity'][0] == '钟表维修'
        assert '学徒未出师' not in groups['identity']


def test_sparse_identity_can_use_a_phrase_from_the_approved_story():
    data = {'title_anchor_groups': {'identity': ['林叶']}, 'premise': '林叶每晚织补旧帆救济渔户。'}
    assert _passes_story_anchor_contract(data, '林叶织补旧帆')
    assert not _passes_story_anchor_contract(data, '林叶一剑灭天宫')


def test_no_source_or_concrete_anchors_does_not_authorize_any_title():
    data = {'title_anchor_groups': {}, 'genre': '现实', 'primary_title': '待修订'}
    assert not _passes_story_anchor_contract(data, '我在街口修空调')
    assert not _passes_story_anchor_contract(data, '我在天宫渡雷劫')

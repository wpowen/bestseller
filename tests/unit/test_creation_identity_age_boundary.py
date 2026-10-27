from types import SimpleNamespace

import pytest

from bestseller.services.book_design import (
    _protagonist_name_from_text,
    ensure_project_book_design_snapshot,
    extract_creation_protagonist_name,
)
from bestseller.services.concept_entities import extract_role_bound_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ('text', 'name'),
    [
        ('周德厚四十六岁，干了二十一年家电维修。', '周德厚'),
        ('赵明32岁，刚刚成为医生。', '赵明'),
        ('欧阳明日二十岁，守着最后一座矿井。', '欧阳明日'),
        ('王小川十六岁，第一次上台。', '王小川'),
    ],
)
def test_age_marks_creation_name_boundary(text, name):
    assert _protagonist_name_from_text(text) == name


@pytest.mark.parametrize('text', [
    '今天按原计划让徒弟主拆，还是自己上保住口碑。',
    '老师傅四十六岁，干了多年维修。今天让徒弟主拆，还是自己上。',
    '老城有很多故事，今天开门营业。徒弟小葛，随后登场。',
])
def test_role_fallback_does_not_promote_actions_or_later_characters(text):
    assert extract_role_bound_name(text) == ''


def test_custom_role_with_supported_name_is_preserved():
    assert extract_role_bound_name('温符徒温迟，守着蒸灵锅。') == '温迟'


def _project(source='llm_premise_identity_resolution'):
    return SimpleNamespace(
        genre='现实', target_chapters=20, target_word_count=40000,
        metadata_json={
            'creation_protagonist_name': '弟主拆',
            'creation_protagonist_source': source,
            'premise': '周德厚四十六岁，干了二十一年家电维修。今天让徒弟主拆，还是自己上。',
        },
    )


def test_inferred_cache_does_not_poison_new_snapshot():
    project = _project()
    assert extract_creation_protagonist_name(project.metadata_json) == '周德厚'
    assert ensure_project_book_design_snapshot(project).protagonist.name == '周德厚'


def test_stale_snapshot_repair_is_audited_and_idempotent():
    project = _project()
    stale = ensure_project_book_design_snapshot(project, protagonist_name='弟主拆')
    repaired = ensure_project_book_design_snapshot(project)
    assert repaired.protagonist.name == '周德厚'
    assert repaired.source_hash != stale.source_hash
    assert project.metadata_json['creation_protagonist_name'] == '周德厚'
    history = project.metadata_json['book_design_snapshot_superseded']
    assert history[-1]['protagonist_name'] == '弟主拆'
    assert history[-1]['replacement_name'] == '周德厚'
    assert ensure_project_book_design_snapshot(project).source_hash == repaired.source_hash
    assert len(project.metadata_json['book_design_snapshot_superseded']) == 1


def test_explicit_user_choice_is_not_rewritten():
    project = _project(source='user')
    snapshot = ensure_project_book_design_snapshot(project)
    assert snapshot.protagonist.name == '弟主拆'
    assert ensure_project_book_design_snapshot(project).source_hash == snapshot.source_hash

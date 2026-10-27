"""Repair cannot use an unverified selected plan or rewrite its source quotas."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_chapter_auto_repair import (
    FakeChapter, FakeProject, FakeScene, FakeSession, FakeQualityReport,
)
from bestseller.services import drafts
from bestseller.services.quality_report_claim import graded_text_fingerprint

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize('key', ['outline_source_admission', 'outline_prefix_readiness'])
@pytest.mark.parametrize('marker', [None, {}, {'artifact_id': 'not-an-admission'}])
async def test_selection_presence_never_authorizes_repair_without_actual_settings(key, marker):
    class NoReadOrMutation:
        async def scalar(self, *args, **kwargs):
            pytest.fail('source/settings guard must precede any repair query')
        async def scalars(self, *args, **kwargs):
            pytest.fail('source/settings guard must precede any repair query')
    chapter = FakeChapter(metadata_json={key: marker, 'prior_evidence': ['kept']})
    before = deepcopy(vars(chapter))
    with pytest.raises(ValueError, match='source_plan_repair_settings_required'):
        await drafts.maybe_prepare_chapter_auto_repair(
            NoReadOrMutation(), project=FakeProject(), chapter=chapter,
            repairable_codes=('BLOCK_LOW',),
        )
    assert vars(chapter) == before


@pytest.mark.parametrize('key', ['outline_source_admission', 'outline_prefix_readiness'])
async def test_failed_current_source_verification_preserves_entire_repair_state(monkeypatch, key):
    chapter = FakeChapter(metadata_json={key: {}, 'prior_evidence': ['kept']})
    scene = FakeScene(chapter_id=chapter.id, target_word_count=1600)
    settings = SimpleNamespace()
    before = deepcopy((vars(chapter), vars(scene)))
    async def rejected(session, **kwargs):
        assert kwargs['settings'] is settings
        assert kwargs['chapter'] is chapter and kwargs['scenes'] == [scene]
        raise ValueError('outline_source_admission_required:current_marker_changed')
    monkeypatch.setattr('bestseller.services.editable_plan_source.build_editable_plan_source', rejected)
    with pytest.raises(ValueError, match='current_marker_changed'):
        await drafts.maybe_prepare_chapter_auto_repair(
            FakeSession(scenes=[scene]), project=FakeProject(), chapter=chapter,
            repairable_codes=('BLOCK_LOW',), settings=settings,
        )
    assert (vars(chapter), vars(scene)) == before


@pytest.mark.parametrize('key', ['outline_source_admission', 'outline_prefix_readiness'])
@pytest.mark.parametrize('code,words,target', [('BLOCK_LOW', 900, 2400), ('BLOCK_HIGH', 9500, 6400)])
async def test_verified_source_keeps_quota_and_records_runtime_length_feedback(monkeypatch, key, code, words, target):
    # Isolate the already-verified source boundary; separate SQLite reader tests
    # execute the real admission proof, including stale and foreign selections.
    chapter = FakeChapter(metadata_json={key: {'owned': 'fixture'}, 'prior_evidence': ['kept']})
    scene = FakeScene(chapter_id=chapter.id, target_word_count=1600)
    text = '测' * words
    report = FakeQualityReport(chapter_id=chapter.id, report_json={
        'blocking_codes': [code], 'graded': graded_text_fingerprint(text),
        'length_control': {'word_count': words, 'target_words': target, 'band': code},
    })
    checked = []
    settings = SimpleNamespace()
    async def verified(session, **kwargs):
        assert kwargs['settings'] is settings
        checked.append(kwargs['chapter'].id)
        return {'status': 'available'}
    monkeypatch.setattr('bestseller.services.editable_plan_source.build_editable_plan_source', verified)
    triggered, codes = await drafts.maybe_prepare_chapter_auto_repair(
        FakeSession(reports=[report], current_text=text, scenes=[scene]),
        project=FakeProject(), chapter=chapter, repairable_codes=(code,), settings=settings,
    )
    assert triggered and code in codes and checked == [chapter.id]
    assert scene.target_word_count == 1600
    assert scene.metadata_json['auto_repair_original_target_word_count'] == 1600
    assert scene.metadata_json['auto_repair_adjusted_target_word_count'] != 1600
    assert scene.metadata_json['auto_repair_hint'] and chapter.metadata_json['prior_evidence'] == ['kept']

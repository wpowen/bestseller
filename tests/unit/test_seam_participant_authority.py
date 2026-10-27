"""Unsupported name guesses cannot become mandatory edits through a consumer."""
from copy import deepcopy

import pytest

from bestseller.domain.review import ChapterReviewFinding, ChapterReviewResult, ChapterReviewScores
from bestseller.services.chapter_seam import (
    OpenThread, SeamReport, ThreadKind, ThreadResolution, build_seam_bridge_repair_prompt,
    extract_open_threads,
)
from bestseller.services.reviews import _chapter_seam_supported_signal, _merge_chapter_seam_into_review

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('text', [
    '踱步的人停了下来。抿唇之后，她捻起纸角。',
    '慕容雪低声说好。欧阳修抬手回应。',
    'Ada Lovelace folds the paper. Alan Turing nods.',
    'María García sonrió. José Álvarez guardó el cuaderno.',
    '佐藤美咲は頷いた。山田太郎は歩いた。',
    '김민지는 웃었다. 박서준은 고개를 끄덕였다.',
    'Александр Петров кивнул. Мария Иванова улыбнулась.',
])
def test_arbitrary_prose_or_names_have_no_presence_authority(text):
    assert not any(t.kind == ThreadKind.PARTICIPANT for t in extract_open_threads(text))


def report(*kinds):
    threads = tuple(OpenThread(kind=kind, marker='旧角色' if kind == ThreadKind.PARTICIPANT else '追击',
                               evidence='retained old observation') for kind in kinds)
    drops = tuple(ThreadResolution(thread=t, resolution='silent_drop', evidence='') for t in threads)
    return SeamReport(open_threads=threads, resolutions=drops, silent_drops=drops)


def review(findings=()):
    return ChapterReviewResult(verdict='pass', severity_max='info',
        scores=ChapterReviewScores(**{name: .9 for name in ChapterReviewScores.model_fields}),
        findings=list(findings),
        evidence_summary={}, rewrite_instructions='Preserve the existing primary instruction.')


def test_retained_participant_observation_preserved_without_scoring_or_editing_authority():
    old = report(ThreadKind.PARTICIPANT)
    before = deepcopy(old)
    assert old.passed and old.score == 1
    assert build_seam_bridge_repair_prompt(old) == ''
    findings, evidence = _chapter_seam_supported_signal(old)
    assert old == before and len(old.open_threads) == 1
    assert findings == [] and evidence['chapter_seam_participant_coverage']['status'] == 'unsupported'


@pytest.mark.parametrize('include_open', [True, False])
@pytest.mark.parametrize('has_threat', [True, False])
def test_legacy_wire_cannot_reinject_cached_prompt_but_genuine_threat_remains(include_open, has_threat):
    rows = [{'kind': 'participant', 'marker': '旧角色'}]
    if has_threat:
        rows.append({'kind': 'immediate_threat', 'marker': '追击'})
    payload = {'chapter_seam_silent_drops': rows, 'chapter_seam_score': -19,
               'chapter_seam_repair_prompt': 'INJECT_SENTINEL: invent a new cast member'}
    if include_open:
        payload['chapter_seam_open_threads'] = deepcopy(rows)
    before = deepcopy(payload)
    native = ChapterReviewFinding(category='continuity', severity='high', message='Primary evidence stays.')
    original = review([native])
    merged = _merge_chapter_seam_into_review(original,
        [ChapterReviewFinding(category='chapter_seam', severity='critical', message='INJECT_SENTINEL')],
        payload)
    assert payload == before and original.findings == [native]
    assert native in merged.findings
    assert 'INJECT_SENTINEL' not in (merged.rewrite_instructions or '')
    assert '旧角色' not in (merged.rewrite_instructions or '')
    assert merged.verdict == ('rewrite' if has_threat else 'pass')
    assert merged.evidence_summary['chapter_seam_participant_coverage']['status'] == 'unsupported'
    assert ('追击' in (merged.rewrite_instructions or '')) is has_threat


def test_unverified_participant_cannot_waive_a_real_threat():
    old = report(ThreadKind.PARTICIPANT, ThreadKind.IMMEDIATE_THREAT)
    assert not old.passed and old.score == 0
    findings, evidence = _chapter_seam_supported_signal(old)
    assert [f.severity for f in findings] == ['critical']
    assert evidence['chapter_seam_silent_drops'] == [{'kind': 'immediate_threat', 'marker': '追击'}]

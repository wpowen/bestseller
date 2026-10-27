"""Publication bounds and advisory targets cannot create competing vetoes."""
from types import SimpleNamespace

import pytest

from bestseller.domain.review import ChapterReviewFinding
from bestseller.services.reviews import (
    _can_accept_llm_pass_over_rule_rewrite,
    evaluate_chapter_draft,
    resolve_chapter_verdict,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def evaluate(words, *, target=2000, language='zh-CN', override=None):
    settings = load_settings()
    project = SimpleNamespace(metadata_json={} if override is None else {'words_per_chapter': override})
    chapter = SimpleNamespace(chapter_number=4, target_word_count=target,
                              chapter_goal='Verify the observatory record.')
    draft = SimpleNamespace(content_md='岑遥合上记录册，把校准后的读数交给值班员。', word_count=words)
    return evaluate_chapter_draft(chapter=chapter, scenes=[], draft=draft, settings=settings,
                                  language=language, genre='science_fiction', project=project)


@pytest.mark.parametrize('words', [1800, 1900, 2601, 2769, 3175, 3500])
def test_in_publication_band_target_deviation_cannot_be_a_hard_length_finding(words):
    result = evaluate(words)
    receipt = result.evidence_summary['length_contract']
    assert receipt['declared_target'] == 2000
    assert (receipt['hard_min'], receipt['hard_max']) == (1800, 3500)
    assert receipt['word_count'] == words and receipt['publication_passed'] is True
    assert not any(f.category == 'goal' for f in result.findings)
    if words > 2600:
        finding = next(f for f in result.findings if f.category == 'word_target_advisory')
        verdict, blocking = resolve_chapter_verdict(core_overall=.9, threshold=.75,
            findings=[finding], is_opening_chapter=False, advisory_echo_axes=False)
        assert verdict == 'pass' and blocking == []


@pytest.mark.parametrize('words', [1, 1799, 3501, 6000])
def test_outside_publication_band_still_blocks_even_with_a_semantic_pass(words):
    result = evaluate(words)
    assert result.evidence_summary['length_contract']['publication_passed'] is False
    assert any(f.category == 'goal' and f.severity == 'high' for f in result.findings)
    assert not _can_accept_llm_pass_over_rule_rewrite(result.model_copy(update={'verdict': 'rewrite'}),
        {'pass': True, 'blocking_issues': []})


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_explicit_project_publication_limits_are_the_same_limits_used_by_review(language):
    result = evaluate(2750, language=language,
                      override={'min': 1900, 'target': 2200, 'max': 2700})
    receipt = result.evidence_summary['length_contract']
    assert (receipt['hard_min'], receipt['hard_max']) == (1900, 2700)
    assert receipt['publication_passed'] is False


def test_only_target_advisory_is_overridable_not_arbitrary_goal_or_semantic_blockers():
    base = evaluate(3175)
    advisory = next(f for f in base.findings if f.category == 'word_target_advisory')
    result = base.model_copy(update={'verdict': 'rewrite', 'findings': [advisory]})
    assert _can_accept_llm_pass_over_rule_rewrite(result, {'pass': True, 'blocking_issues': []})
    assert not _can_accept_llm_pass_over_rule_rewrite(result, {'pass': False, 'blocking_issues': []})
    assert not _can_accept_llm_pass_over_rule_rewrite(result,
        {'pass': True, 'blocking_issues': [{'code': 'CURRENT_FACT_CONFLICT'}]})
    goal = ChapterReviewFinding(category='goal', severity='high', message='A real source requirement failed.')
    assert not _can_accept_llm_pass_over_rule_rewrite(
        result.model_copy(update={'findings': [advisory, goal]}), {'pass': True, 'blocking_issues': []})

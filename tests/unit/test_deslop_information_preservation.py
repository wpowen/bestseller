"""Local information conservation; no network or semantic model judge."""
import hashlib
from copy import deepcopy
from uuid import uuid4
import pytest
from bestseller.services.deslop_information_preservation import inspect_style_term_collapse
from bestseller.services import deslop_revise as deslop
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.pipelines import _persist_deslop_candidate, _select_deslop_candidate
from test_deslop_version_lineage import _draft_database, _original, _chapter_for

pytestmark = pytest.mark.unit
SOURCE = '铜铃挂在门口，信封放在桌上。铜铃被风吹响，信封仍压在杯底。铜铃上的裂痕还在。'
BROKEN = SOURCE.replace('铜铃', '信封')


def test_repeated_replacement_of_existing_term_is_body_bound_not_a_grade():
    result = inspect_style_term_collapse(SOURCE, BROKEN)
    assert result['automatic_selection_allowed'] is False
    assert result['source_sha256'] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert result['candidate_sha256'] == hashlib.sha256(BROKEN.encode()).hexdigest()
    assert result['findings'][0]['source_term'] == '铜铃'
    for edit in result['findings'][0]['edits']:
        assert SOURCE[edit['source_start']:edit['source_end']] == '铜铃'
        assert BROKEN[edit['candidate_start']:edit['candidate_end']] == '信封'
    assert result['semantic_grade_asserted'] is result['adoption_authorized'] is False


@pytest.mark.parametrize('candidate', [SOURCE, SOURCE.replace('。', '！'), SOURCE.replace('铜铃', '信封', 1), SOURCE.replace('挂在门口', '悬在门口')])
def test_no_entity_lexicon_or_blanket_ban_on_local_edits(candidate):
    assert inspect_style_term_collapse(SOURCE, candidate)['automatic_selection_allowed']


@pytest.mark.asyncio
async def test_rejected_result_is_observed_but_not_selected_or_fed_to_next_round(monkeypatch):
    calls = []; observed = []
    async def complete(s, settings, request):
        calls.append(request)
        return LLMCompletionResult(content=BROKEN, provider='fixture', model_name='fixture', llm_run_id=uuid4(), finish_reason='stop')
    monkeypatch.setattr(deslop, 'complete_text', complete)
    monkeypatch.setattr(deslop, '_findings_text', lambda *a: ('style findings', 80, 1))
    monkeypatch.setattr(deslop, '_keep_better_key', lambda text, *a, **kw: (0 if text == BROKEN else 100,))
    result = await deslop.revise_prose_deslop(None, object(), content=SOURCE, target_chars=40, rounds=2, observe_candidate=observed.append)
    assert result == SOURCE and len(calls) == len(observed) == 1
    assert result.phase_status.stop_reason == 'information_change_rejected'
    assert result.phase_status.state == 'incomplete'
    assert observed[0]['content'] == BROKEN
    assert not observed[0]['information_preservation']['automatic_selection_allowed']


@pytest.mark.asyncio
async def test_later_round_compares_original_source_not_only_previous_candidate(monkeypatch):
    first = SOURCE.replace('铜铃', '信封', 1)
    second = first.replace('铜铃', '信封', 1)
    assert inspect_style_term_collapse(first, second)['automatic_selection_allowed']
    assert not inspect_style_term_collapse(SOURCE, second)['automatic_selection_allowed']
    replies = iter([first, second]); observed = []
    async def complete(*args):
        return LLMCompletionResult(content=next(replies), provider='fixture', model_name='fixture', finish_reason='stop')
    monkeypatch.setattr(deslop, 'complete_text', complete)
    monkeypatch.setattr(deslop, '_findings_text', lambda *a: ('findings', 80, 1))
    monkeypatch.setattr(deslop, '_keep_better_key', lambda text, *a, **kw: (0 if text == first else 100,))
    result = await deslop.revise_prose_deslop(None, object(), content=SOURCE, target_chars=40, rounds=2, observe_candidate=observed.append)
    assert result == first
    assert not observed[-1]['information_preservation']['automatic_selection_allowed']
    assert len(observed[-1]['information_preservation']['checks']) == 2


@pytest.mark.asyncio
async def test_real_sqlite_bad_candidate_retained_but_pointer_and_prior_evidence_unchanged():
    with _draft_database() as (s, sync):
        original = _original(sync, content=SOURCE)
        chapter = _chapter_for(original); before = deepcopy(chapter.metadata_json)
        proof = {'schema': 'deslop-information-selection.v1', 'candidate_sha256': hashlib.sha256(BROKEN.encode()).hexdigest(),
                 'automatic_selection_allowed': False, 'checks': [inspect_style_term_collapse(SOURCE, BROKEN)]}
        bad = await _persist_deslop_candidate(s, chapter=chapter, parent_draft=original,
            content=BROKEN, llm_run_id=uuid4(), round_no=1, information_preservation=proof)
        sync.commit(); sync.expire_all()
        assert bad.promotion_metadata['information_preservation'] == proof
        with pytest.raises(ValueError, match='information_change_requires_review'):
            await _select_deslop_candidate(s, chapter=chapter, candidate=bad, selected_reason='better AI score')
        assert original.is_current and not bad.is_current
        assert bad.content_md == BROKEN and chapter.metadata_json == before

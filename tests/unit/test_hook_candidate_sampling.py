from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.domain.anti_commonsense_hook import HookSpec
from bestseller.services import anti_commonsense_hook, concept_tournament, conception


ROOT = Path(__file__).resolve().parents[2]
CONCEPTION = ROOT / 'src/bestseller/services/conception.py'
QUICKSTART = ROOT / 'src/bestseller/web/novel_quickstart.html'


@pytest.mark.asyncio
async def test_pipeline_skips_static_sampling_without_explicit_hook_spec(monkeypatch):
    calls: list[dict[str, object]] = []
    events: list[tuple[str, str, dict[str, object]]] = []

    class Candidate:
        def model_dump(self, mode='json'):
            del mode
            return {'spec': {'one_liner': 'internal'}}

    def spy_generate(**kwargs):
        calls.append(kwargs)
        return [Candidate()]

    async def noop(*args, **kwargs):
        return None

    async def no_cast_names(*args, **kwargs):
        return []

    async def no_winner(*args, **kwargs):
        return concept_tournament.ConceptTournamentResult()

    monkeypatch.setattr(anti_commonsense_hook, 'generate_hook_candidates', spy_generate)
    monkeypatch.setattr(conception, '_attach_concept_methodology', noop)
    monkeypatch.setattr(conception, '_recent_cast_names', no_cast_names)
    monkeypatch.setattr(conception, '_attach_mechanism_dedup', noop)
    monkeypatch.setattr(concept_tournament, 'run_concept_tournament', no_winner)
    monkeypatch.setattr(
        conception,
        'emit_activity',
        lambda stage, payload=None: events.append(('activity', stage, payload or {})),
    )
    monkeypatch.setattr(
        conception,
        'emit_milestone',
        lambda stage, payload=None: events.append(('milestone', stage, payload or {})),
    )
    settings = SimpleNamespace(
        hook_engine=SimpleNamespace(
            enabled=True,
            candidate_count=6,
            rank_weight_h_norm=0.62,
            rank_weight_novelty=0.28,
            rank_weight_duplicate_risk=0.10,
            min_h_norm=30.0,
        )
    )

    with pytest.raises(Exception, match='CONCEPT_TOURNAMENT_NO_QUALIFIED_WINNER'):
        await conception.run_conception_pipeline(
            None,
            settings,
            genre_key='xuanhuan',
            genre='玄幻',
            sub_genre='东方玄幻',
            chapter_count=1,
            user_hints={'concept_seed': '边城矿奴陆衡'},
            story_facets=None,
        )

    assert calls == []
    assert [event for event in events if 'hook_candidates' in event[1]] == []


@pytest.mark.asyncio
async def test_pipeline_keeps_explicit_hook_spec_compatibility(monkeypatch):
    calls: list[dict[str, object]] = []
    events: list[tuple[str, str, dict[str, object]]] = []

    class Candidate:
        def model_dump(self, mode='json'):
            del mode
            return {'spec': {'one_liner': 'explicit-compatible'}}

    def spy_generate(**kwargs):
        calls.append(kwargs)
        return [Candidate()]

    async def noop(*args, **kwargs):
        return None

    async def no_cast_names(*args, **kwargs):
        return []

    async def no_winner(*args, **kwargs):
        return concept_tournament.ConceptTournamentResult()

    monkeypatch.setattr(anti_commonsense_hook, 'generate_hook_candidates', spy_generate)
    monkeypatch.setattr(conception, '_attach_concept_methodology', noop)
    monkeypatch.setattr(conception, '_recent_cast_names', no_cast_names)
    monkeypatch.setattr(conception, '_attach_mechanism_dedup', noop)
    monkeypatch.setattr(concept_tournament, 'run_concept_tournament', no_winner)
    monkeypatch.setattr(
        conception,
        'emit_activity',
        lambda stage, payload=None: events.append(('activity', stage, payload or {})),
    )
    monkeypatch.setattr(
        conception,
        'emit_milestone',
        lambda stage, payload=None: events.append(('milestone', stage, payload or {})),
    )
    settings = SimpleNamespace(
        hook_engine=SimpleNamespace(
            enabled=True,
            candidate_count=6,
            rank_weight_h_norm=0.62,
            rank_weight_novelty=0.28,
            rank_weight_duplicate_risk=0.10,
            min_h_norm=30.0,
        )
    )
    hook_spec = HookSpec(
        mechanism_key='m',
        base_desire='x',
        reversal='y',
        one_liner='explicit',
        core_rule='z',
    ).model_dump(mode='json')

    with pytest.raises(Exception, match='CONCEPT_TOURNAMENT_NO_QUALIFIED_WINNER'):
        await conception.run_conception_pipeline(
            None,
            settings,
            genre_key='xuanhuan',
            genre='玄幻',
            sub_genre='东方玄幻',
            chapter_count=1,
            user_hints={'concept_seed': '边城矿奴陆衡', 'hook_spec': hook_spec},
            story_facets=None,
        )

    assert len(calls) == 1
    assert calls[0]['count'] == 6
    assert [event[1] for event in events if 'hook_candidates' in event[1]] == [
        'hook_candidates_started',
        'hook_candidates_generated',
    ]


def test_quickstart_labels_are_internal_and_unbound():
    source = QUICKSTART.read_text(encoding='utf-8')
    assert '机制灵感内部采样中' in source
    assert '机制灵感已记录（未绑定作品）' in source
    assert '钩子候选生成中' not in source
    assert '钩子候选就绪' not in source

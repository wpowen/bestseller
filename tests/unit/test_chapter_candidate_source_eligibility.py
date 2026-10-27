"""Actual SQLite candidate selection against bound reviews and real source fences."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db
from test_chapter_critic_semantic_dependencies import semantic_db as semantic_db

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import ChapterDraftVersionModel, QualityScoreModel, ReviewReportModel
from bestseller.services import pipelines
from bestseller.services.chapter_draft_candidate_eligibility import AUDIT_KEY
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def candidates(semantic_db):
    session, project, chapter, scene, design, prior = semantic_db
    settings = load_settings(env={})
    policy = pipelines.chapter_review_recovery_source_hash()
    semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    drafts, qualities, reports, cycles = [], [], [], {}
    for version, score, text in ((1, 0.91, '观测员记录潮汐。'), (2, 0.63, '观测员校准量尺。')):
        draft = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=version, content_md=text * 260, word_count=len(text) * 260,
            is_current=version == 2, promotion_state='candidate')
        session.add(draft)
        await session.flush()
        result = ChapterReviewResult(verdict='rewrite', severity_max='major',
            scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, score)))
        body = pipelines.chapter_review_body_fingerprint(draft.content_md)
        report = ReviewReportModel(project_id=project.id, target_type='chapter', target_id=chapter.id,
            verdict='rewrite', severity_max='major', reviewer_type='fixture', structured_output={
                'draft_id': str(draft.id), 'reviewed_body_fingerprint': body,
                'critic_evidence': {'status': 'verified', 'passed': True},
                'scores': result.scores.model_dump(mode='json'), 'findings': [], 'evidence_summary': {},
            })
        session.add(report)
        await session.flush()
        quality = QualityScoreModel(project_id=project.id, target_type='chapter', target_id=chapter.id,
            chapter_draft_version_id=draft.id, review_report_id=report.id, score_overall=score,
            is_current=version == 2, evidence_summary={'hard_gates_passed': False, 'blocking_codes': []})
        session.add(quality)
        await session.flush()
        key = pipelines._chapter_critic_cycle_id(project.id, chapter.id, body['digest'])
        cycles[key] = dict(schema_version='chapter-critic-cycle.v1', cycle_id=key,
            source_draft_id=str(draft.id), source_body_hash=body['digest'], policy_source_hash=policy,
            semantic_dependencies_sha256=semantic, state='completed', attempts=[{
                'attempt': 1, 'state': 'completed', 'outcome': 'available', 'draft_id': str(draft.id),
                'body_fingerprint': body, 'review_report_id': str(report.id), 'quality_score_id': str(quality.id),
                'rewrite_task_id': None, 'review_result': result.model_dump(mode='json'),
            }])
        drafts.append(draft)
        qualities.append(quality)
        reports.append(report)
    chapter.metadata_json = {**chapter.metadata_json, 'chapter_critic_cycles': cycles}
    await session.flush()
    return session, project, chapter, settings, drafts, qualities, reports, scene, design, prior


async def choose(data, *, settings=True):
    session, project, chapter, config, drafts, *_ = data
    return await pipelines._promote_best_scoring_chapter_draft_on_stall(
        session, chapter=chapter, current_draft=drafts[1], project=project,
        settings=config if settings else None)


def edit_cycle(data, index, mutate):
    chapter, draft = data[2], data[4][index]
    cycles = deepcopy(chapter.metadata_json['chapter_critic_cycles'])
    cycle = next(c for c in cycles.values() if c['source_draft_id'] == str(draft.id))
    mutate(cycle)
    chapter.metadata_json = {**chapter.metadata_json, 'chapter_critic_cycles': cycles}


@pytest.mark.parametrize('genre', ['science-fiction', 'sports'])
async def test_same_source_available_soft_debt_best_of_n_is_preserved(candidates, genre):
    session, project, chapter, settings, drafts, qualities, reports, *_ = candidates
    # Genre is irrelevant to the rule; establish the actual updated fence in each receipt.
    project.genre = genre
    semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    for index in range(2):
        edit_cycle(candidates, index, lambda c: c.update(semantic_dependencies_sha256=semantic))
    originals = [deepcopy(r.structured_output) for r in reports]
    selected = await choose(candidates)
    assert selected.id == drafts[0].id and drafts[0].is_current and not drafts[1].is_current
    assert [r.structured_output for r in reports] == originals
    assert all(d.promotion_state == 'candidate' for d in drafts)
    audit = chapter.metadata_json[AUDIT_KEY][-1]
    assert audit['expected_current_draft_id'] == str(drafts[1].id)
    assert audit['chosen_draft_id'] == str(drafts[0].id)
    assert audit['selection_only'] and not audit['adoption_authorized']
    assert await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter) == semantic
    again = await pipelines._promote_best_scoring_chapter_draft_on_stall(
        session, chapter=chapter, current_draft=selected, project=project, settings=settings)
    assert again.id == selected.id
    history = deepcopy(chapter.metadata_json[AUDIT_KEY])
    await pipelines._promote_best_scoring_chapter_draft_on_stall(
        session, chapter=chapter, current_draft=selected, project=project, settings=settings)
    assert chapter.metadata_json[AUDIT_KEY] == history


@pytest.mark.parametrize('change', ['legacy', 'unknown', 'body', 'report_body', 'policy', 'source',
    'report_link', 'score', 'malformed', 'duplicate', 'retired', 'missing_score'])
async def test_old_high_score_cannot_displace_known_current(candidates, change):
    session, _project, chapter, _settings, drafts, qualities, reports, *_ = candidates
    if change == 'legacy':
        cycles = {k: c for k, c in chapter.metadata_json['chapter_critic_cycles'].items()
                  if c['source_draft_id'] != str(drafts[0].id)}
        chapter.metadata_json = {**chapter.metadata_json, 'chapter_critic_cycles': cycles}
    elif change == 'unknown':
        reports[0].structured_output = {**reports[0].structured_output,
            'critic_evidence': {'status': 'evidence_invalid', 'passed': False}}
    elif change == 'body':
        drafts[0].content_md += '未评审的新动作。'
    elif change == 'report_body':
        reports[0].structured_output = {**reports[0].structured_output,
            'reviewed_body_fingerprint': pipelines.chapter_review_body_fingerprint('其他正文')}
    elif change == 'policy':
        edit_cycle(candidates, 0, lambda c: c.update(policy_source_hash='old-policy'))
    elif change == 'source':
        edit_cycle(candidates, 0, lambda c: c.update(semantic_dependencies_sha256='old-plan-and-prior'))
    elif change == 'report_link':
        qualities[0].review_report_id = reports[1].id
    elif change == 'score':
        qualities[0].score_overall = 0.99
    elif change == 'malformed':
        edit_cycle(candidates, 0, lambda c: c.update(attempts=[None]))
    elif change == 'duplicate':
        edit_cycle(candidates, 0, lambda c: c.update(attempts=[c['attempts'][0],
            {**c['attempts'][0], 'attempt': 2}]))
    elif change == 'retired':
        drafts[0].promotion_state = 'quarantined'
    else:
        await session.delete(qualities[0])
    selected = await choose(candidates)
    assert selected.id == drafts[1].id and drafts[1].is_current and not drafts[0].is_current
    rejected = next(d for d in chapter.metadata_json[AUDIT_KEY][-1]['candidates'] if d['draft_id'] == str(drafts[0].id))
    assert not rejected['eligible'] and rejected['reasons']


@pytest.mark.parametrize('change', ['scene', 'approved_plan', 'prefix', 'prior', 'configuration'])
async def test_changed_real_dependencies_never_promote_historical_scores(candidates, change):
    _s, _p, chapter, settings, drafts, _q, _r, scene, design, prior = candidates
    if change == 'scene':
        scene.exit_state = {'door': 'remains closed'}
    elif change == 'approved_plan':
        design.content = {'rules': ['A different approved world rule']}
    elif change == 'prefix':
        chapter.metadata_json = {**chapter.metadata_json,
            'outline_prefix_readiness': {'artifact_id': str(uuid4()), 'projection_hash': 'new'}}
    elif change == 'prior':
        prior.content_md += 'Changed adopted facts without a matching receipt.'
    else:
        settings.quality.thresholds.scene_min_score += 0.01
    assert (await choose(candidates)).id == drafts[1].id
    assert chapter.metadata_json[AUDIT_KEY][-1]['chosen_draft_id'] == str(drafts[1].id)


@pytest.mark.parametrize('current_unknown,old_unknown', [(True, False), (True, True), (False, True)])
async def test_known_unknown_current_fallback_is_not_a_passing_grade(candidates, current_unknown, old_unknown):
    reports = candidates[6]
    for index, unknown in ((1, current_unknown), (0, old_unknown)):
        if unknown:
            reports[index].verdict = 'unknown'
    selected = await choose(candidates)
    assert selected.id == candidates[4][1 if old_unknown else 0].id
    assert not candidates[2].metadata_json[AUDIT_KEY][-1]['adoption_authorized']


@pytest.mark.parametrize('where', ['quality', 'findings', 'commercial'])
async def test_known_hard_blocker_cannot_win_over_viable_soft_debt(candidates, where):
    _s, _p, chapter, _settings, drafts, qualities, reports, *_ = candidates
    if where == 'quality':
        qualities[0].evidence_summary = {'blocking_codes': ['CANON_CONFLICT']}
    elif where == 'findings':
        reports[0].structured_output = {**reports[0].structured_output,
            'findings': [{'category': 'source', 'severity': 'critical', 'message': 'Occurrence conflicts'}]}
    else:
        reports[0].structured_output = {**reports[0].structured_output,
            'evidence_summary': {'llm_commercial_judge': {'pass': False, 'has_critical': True}}}
    assert (await choose(candidates)).id == drafts[1].id
    reasons = next(row['reasons'] for row in chapter.metadata_json[AUDIT_KEY][-1]['candidates']
                   if row['draft_id'] == str(drafts[0].id))
    assert 'known_hard_or_critical_blocker' in reasons


async def test_missing_settings_fails_closed_and_existing_receipts_are_preserved(candidates):
    before = deepcopy(candidates[2].metadata_json['chapter_critic_cycles'])
    assert (await choose(candidates, settings=False)).id == candidates[4][1].id
    assert candidates[2].metadata_json['chapter_critic_cycles'] == before


async def test_expected_current_is_checked_before_selection(candidates):
    session, project, chapter, settings, drafts, *_ = candidates
    with pytest.raises(ValueError, match='chapter_best_draft_current_changed'):
        await pipelines._promote_best_scoring_chapter_draft_on_stall(
            session, chapter=chapter, current_draft=drafts[0], project=project, settings=settings)
    assert drafts[1].is_current and not drafts[0].is_current


async def test_formal_adopted_source_is_never_replaced(candidates):
    _session, _p, chapter, _settings, drafts, *_ = candidates
    drafts[1].promotion_state = 'promoted'
    assert (await choose(candidates)).id == drafts[1].id
    assert drafts[1].is_current and not drafts[0].is_current
    assert chapter.metadata_json[AUDIT_KEY][-1]['context_error'] == 'chapter_has_adopted_source'


async def test_latest_unknown_rescore_cannot_fall_back_to_an_old_high_score(candidates):
    session, project, chapter, _settings, drafts, qualities, *_ = candidates
    qualities[0].is_current = False
    session.add(QualityScoreModel(project_id=project.id, target_type='chapter', target_id=chapter.id,
        chapter_draft_version_id=drafts[0].id, review_report_id=qualities[0].review_report_id,
        score_overall=0.99, is_current=True, evidence_summary={}))
    assert (await choose(candidates)).id == drafts[1].id

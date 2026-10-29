"""Real rewrite execution/SQLite readback with isolated deterministic providers."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import ast

import pytest
from sqlalchemy import select
from test_revision_source_binding_sqlite import db as db, seed

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterQualityReportModel, ProjectModel, RewriteTaskModel
from bestseller.services import drafts, reviews, review_source_context, lossless_rewrite_prompt
from bestseller.services.deterministic_post_write_audit import DeterministicAuditFinding, DeterministicAuditReport
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.rewrite_patch import text_hash
from bestseller.services.write_safety_gate import WriteSafetyFinding
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
PRIMARY = ('The observatory caretaker checked the bronze receiver. ' * 40).strip()
REPAIRED = ('The botanist recorded a new signal beside the greenhouse. ' * 40).strip()
TRIMMED = ('The witness recorded the final reading without opening the dome. ' * 30).strip()


def _audit(code=None):
    finding = DeterministicAuditFinding(code, 'high', 'exact observed span', 1, 1, 'Keep the source intact.') if code else None
    return DeterministicAuditReport(1, (finding,) if finding else (), not bool(finding), {})


@pytest.fixture
def runtime(monkeypatch):
    band = SimpleNamespace(hard_min=1, hard_max=10000, hard_target=2000, safe_min=1, safe_max=10000)
    band.effect_checked_bodies = []
    def effects(_project, _chapter, text, **kw):
        band.effect_checked_bodies.append(text)
        return {'selected_effects': [], 'passed': True, 'tested_body_sha256': text_hash(text)}
    monkeypatch.setattr(reviews, 'audit_persisted_story_effect_realization', effects)
    monkeypatch.setattr(reviews, 'chapter_rewrite_length_band', lambda *a, **k: band)
    monkeypatch.setattr(reviews, '_rewrite_output_max_tokens_override', lambda *a, **k: 5000)
    monkeypatch.setattr(drafts, '_prepare_quality_uplift_prompt_blocks', AsyncMock())
    monkeypatch.setattr(reviews, '_try_localized_chapter_first_opening_repair', AsyncMock(return_value=None))
    monkeypatch.setattr(reviews, '_try_localized_chapter_first_ending_repair', AsyncMock(return_value=None))
    monkeypatch.setattr(reviews, '_clean_generated_chapter_text', lambda text, **kw: (text, {}))
    monkeypatch.setattr(review_source_context, 'load_review_source_context', AsyncMock(return_value={}))
    monkeypatch.setattr(review_source_context, 'prepare_source_complete_prompt', lambda **kw: (kw['system_prompt'], kw['user_prompt'], {}))
    # The current executor imports the lossless adapter; the retained executor
    # imports its underlying service. Isolate both transport boundaries rather
    # than depending on which module the full suite loaded first.
    monkeypatch.setattr(lossless_rewrite_prompt, 'prepare_source_complete_prompt',
        lambda **kw: (kw['system_prompt'], kw['user_prompt'], {}))
    context = SimpleNamespace(previous_scene_summaries=[], recent_timeline_events=[], chapter_scenes=[], retrieval_chunks=[])
    monkeypatch.setattr(reviews, 'build_chapter_writer_context', AsyncMock(return_value=context))
    return band


async def _execute(db, monkeypatch, band, *, mode='solved', executor=None):
    requests = []
    async with db() as session:
        chapter, current, task = await seed(session)
        chapter.production_state = 'pending'
        project = ProjectModel(id=chapter.project_id, slug='independent-astronomy',
            title='The sealed greenhouse', genre='science-fiction', language='en',
            target_word_count=8000, target_chapters=4, metadata_json={}, invariants_json=None)
        task.metadata_json = {**task.metadata_json,
            'candidate_duplicate_gate_findings': [{'code': 'AN_OLD_DRAFT_DUPLICATE'}],
            'candidate_quality_gate_violations': [{'code': 'AN_OLD_DRAFT_VIOLATION'}],
            'candidate_quality_retrofit_findings': [{'code': 'AN_OLD_RETROFIT'}],
            'quality_gate_rejected_current_promotion': True,
            'preserved_current_chapter_draft_id': 'previous-attempt-only'}
        await session.flush()
        monkeypatch.setattr(reviews, '_load_chapter_context', AsyncMock(return_value=(project, chapter, None, [], current)))

        async def complete(*args):
            request = args[2]
            requests.append((request.system_prompt, request.user_prompt, request.prompt_template, request.prompt_version))
            return LLMCompletionResult(content=PRIMARY if len(requests) == 1 else REPAIRED,
                provider='fixture', model_name='fixture', llm_run_id=None)

        monkeypatch.setattr(reviews, 'complete_text', complete)

        async def duplicates(_session, **kw):
            if kw['content_md'] == PRIMARY and mode not in {'retrofit', 'retrofit_error', 'candidate_trim', 'candidate_trim_error', 'incumbent_trim', 'incumbent_trim_error', 'initial_error'}:
                return (WriteSafetyFinding(source='fixture', code='INTRA_CHAPTER_REPETITION',
                    severity='major', message='A repeated narrative cluster.', evidence='bronze receiver', payload={'positions': [24, 25]}),)
            if kw['content_md'] == REPAIRED and mode == 'new_duplicate':
                return (WriteSafetyFinding(source='fixture', code='INTRA_CHAPTER_REPETITION',
                    severity='major', message='A different new duplicate.', evidence='new signal', payload={}),)
            return ()
        monkeypatch.setattr(reviews, '_collect_post_assembly_duplicate_findings', duplicates)

        def audit(**kw):
            text = kw['content_md']
            if mode == 'initial_error' and text == PRIMARY or mode == 'eval_error' and text == REPAIRED:
                raise RuntimeError('fixture audit unavailable')
            if mode == 'retrofit_error' and text == REPAIRED or mode in {'candidate_trim_error', 'incumbent_trim_error'} and text == TRIMMED:
                raise RuntimeError('fixture replacement unavailable')
            code = None
            if text == REPAIRED:
                code = 'CANON_FORBIDDEN_TERM' if mode == 'new_hard' else 'ENDING_HOOK_MISSING'
            return _audit(code), band
        monkeypatch.setattr(reviews, '_audit_chapter_rewrite_candidate', audit)

        async def gate(**kw):
            text = kw['content']
            over = (mode in {'candidate_trim', 'candidate_trim_error'} and text in {PRIMARY, REPAIRED}) or (mode in {'incumbent_trim', 'incumbent_trim_error'} and text == current.content_md)
            blocked = over or mode in {'incumbent_trim', 'incumbent_trim_error'} and text in {PRIMARY, REPAIRED}
            violations = [{'code': 'LENGTH_OVER', 'severity': 'block', 'detail': 'max=2000'}] if over else []
            if mode in {'incumbent_trim', 'incumbent_trim_error'} and text in {PRIMARY, REPAIRED}:
                violations = [{'code': 'CANON_STATE_REGRESSION', 'severity': 'block'}]
            extra = kw.get('extra_report_payload')
            if extra:
                session.add(ChapterQualityReportModel(chapter_id=chapter.id, blocks_write=blocked,
                    report_json={**extra, 'graded': graded_text_fingerprint(text),
                        'violations': violations, 'blocking_codes': [v['code'] for v in violations]}))
                await session.flush()
            return 'blocked' if blocked else 'ok'
        monkeypatch.setattr(reviews, '_evaluate_chapter_quality_gate', gate)
        monkeypatch.setattr(reviews, '_length_over_max_from_violations', lambda values: 2000 if any(v.get('code') == 'LENGTH_OVER' for v in values) else None)
        monkeypatch.setattr(reviews, '_micro_trim_overlength_chapter_text', lambda *a, **k: (TRIMMED, {'applied': True, 'before_word_count': 2100, 'after_word_count': 1900}))
        monkeypatch.setattr(reviews, '_quality_retrofit_candidate_findings', lambda text, *a, **k: [{'code': 'QUALITY_RETROFIT_BLOCKED', 'detail': 'Repair source cause'}] if mode in {'retrofit', 'retrofit_error'} and text == PRIMARY else [])
        if mode == 'initial_error':
            with pytest.raises(RuntimeError, match='fixture audit unavailable'):
                await (executor or reviews.rewrite_chapter_from_task)(session, project.slug, 1, settings=load_settings(env={}))
            assert task.attempts == 1 and current.is_current
            assert task.metadata_json['candidate_evidence_history'][-1]['status'] == 'evaluation_unavailable'
            return requests, None
        result, _ = await (executor or reviews.rewrite_chapter_from_task)(
            session, project.slug, 1, settings=load_settings(env={}))
        await session.flush()
        await session.commit()
        result_id, task_id = result.id, task.id
        original_id = current.id
    async with db() as session:
        task = await session.get(RewriteTaskModel, task_id)
        result = await session.get(ChapterDraftVersionModel, result_id)
        rows = list(await session.scalars(select(ChapterDraftVersionModel).order_by(ChapterDraftVersionModel.version_no)))
        return requests, SimpleNamespace(task=task, result=result, rows=rows, original_id=original_id)


@pytest.mark.parametrize('mode,stage', [('solved', 'semantic_repair'), ('retrofit', 'retrofit_repair'),
    ('candidate_trim', 'candidate_trim'), ('incumbent_trim', 'incumbent_trim')])
async def test_all_replacement_stages_activate_only_matching_evidence(db, monkeypatch, runtime, mode, stage):
    _requests, saved = await _execute(db, monkeypatch, runtime, mode=mode)
    metadata = saved.task.metadata_json
    assert saved.result.is_current and saved.result.id != saved.original_id
    assert metadata['candidate_body_sha256'] == text_hash(saved.result.content_md)
    assert metadata['candidate_gate_graded'] == graded_text_fingerprint(saved.result.content_md)
    assert metadata['candidate_duplicate_gate_findings'] == []
    assert metadata['candidate_quality_gate_violations'] == []
    assert metadata['candidate_quality_retrofit_findings'] == []
    assert metadata['quality_gate_rejected_current_promotion'] is False
    assert metadata['preserved_current_chapter_draft_id'] is None
    assert metadata['candidate_effect_realization_gate']['tested_body_sha256'] == metadata['candidate_body_sha256']
    assert runtime.effect_checked_bodies[-1] == saved.result.content_md
    if mode in {'solved', 'retrofit'}:
        assert metadata['deterministic_audit']['passed'] is False
        assert metadata['deterministic_audit']['findings'][0]['code'] == 'ENDING_HOOK_MISSING'
        assert metadata['candidate_quality_gate_outcome'] == 'ok'
    assert any(r.get('stage') == stage and r.get('activated') for r in metadata['candidate_evidence_history'])
    assert metadata['candidate_evidence_history'][0]['evidence']['candidate_quality_gate_violations'] == [{'code': 'AN_OLD_DRAFT_VIOLATION'}]
    final = metadata['candidate_evidence_history'][-1]
    assert final['stage'] == 'final' and final['body_sha256'] == metadata['candidate_body_sha256']


@pytest.mark.parametrize('mode', ['new_hard', 'new_duplicate', 'eval_error'])
async def test_repair_new_blocker_or_evaluation_error_cannot_inherit_clean_evidence(db, monkeypatch, runtime, mode):
    _requests, saved = await _execute(db, monkeypatch, runtime, mode=mode)
    assert saved.result.id == saved.original_id
    assert saved.task.status == 'failed'
    metadata = saved.task.metadata_json
    candidate = saved.rows[-1]
    assert not candidate.is_current
    assert metadata['candidate_body_sha256'] == text_hash(candidate.content_md)
    if mode == 'new_hard':
        assert metadata['candidate_duplicate_gate_findings'] == []
        assert {v['code'] for v in metadata['candidate_quality_gate_violations']} == {'CANON_FORBIDDEN_TERM'}
    elif mode == 'new_duplicate':
        assert metadata['candidate_duplicate_gate_findings'][0]['message'] == 'A different new duplicate.'
    else:
        assert candidate.content_md == PRIMARY
        assert metadata['candidate_duplicate_gate_findings'][0]['message'] == 'A repeated narrative cluster.'
        assert any(r.get('stage') == 'semantic_repair' and r.get('status') == 'evaluation_unavailable'
            for r in metadata['candidate_evidence_history'])


async def test_initial_evaluation_error_is_explicit(db, monkeypatch, runtime):
    await _execute(db, monkeypatch, runtime, mode='initial_error')


@pytest.mark.parametrize('mode,stage', [('retrofit_error', 'retrofit_repair'),
    ('candidate_trim_error', 'candidate_trim'), ('incumbent_trim_error', 'incumbent_trim')])
async def test_failed_later_evaluation_retains_whole_prior_body_evidence(db, monkeypatch, runtime, mode, stage):
    _requests, saved = await _execute(db, monkeypatch, runtime, mode=mode)
    assert saved.result.id == saved.original_id
    metadata = saved.task.metadata_json
    assert metadata['candidate_body_sha256'] == text_hash(saved.rows[-1].content_md)
    assert metadata['candidate_gate_graded'] == graded_text_fingerprint(saved.rows[-1].content_md)
    assert saved.rows[-1].content_md != TRIMMED
    assert any(r.get('stage') == stage and r.get('status') == 'evaluation_unavailable'
        for r in metadata['candidate_evidence_history'])


@pytest.mark.parametrize('code,blocks', [('OPENING_PRESSURE_THIN', False),
    ('ENDING_HOOK_MISSING', False), ('FORBIDDEN_TERM_HIT', True),
    ('DEPRECATED_ENTITY_HIT', True), ('PARAGRAPH_DUPLICATE_PARAPHRASE', True),
    ('SCENE_CARD_PROSE_COPIED', True), ('CALLBACK_OBLIGATION_MISSING', False),
    ('CANON_STATE_REGRESSION', True), ('LENGTH_OUT_OF_BAND', True)])
async def test_action_classification_preserves_hard_sources_not_raw_passed_flag(code, blocks):
    from bestseller.services.rewrite_candidate_evidence import blocking_deterministic_violations
    from bestseller.services.quality_gates_config import get_quality_gates_config
    band = SimpleNamespace(hard_min=10, hard_max=100)
    found = blocking_deterministic_violations(_audit(code), band=band, word_count=5,
        chapter_number=2, config=get_quality_gates_config().l6_gate,
        translate=reviews._deterministic_rewrite_violations)
    assert bool(found) is blocks


async def test_new_repair_version_binds_failed_body_while_preserving_primary_request(db, monkeypatch, runtime):
    path = Path(__file__).resolve().parents[2] / 'reports/continuous-recovery-2026-10-16/freezes/before-rewrite-candidate-evidence/reviews.py'
    # Isolated worktrees do not contain private historical source snapshots.
    # The runner can point at the byte-verified, read-only protection snapshot.
    import hashlib
    import os
    archived = os.environ.get("BESTSELLER_TEST_RETAINED_REWRITE_SOURCE")
    if archived:
        path = Path(archived)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            "5004d2b7fead1344748ea4241657c54686ae5b09a70797fb95b13bfc2238c776"
        )
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'rewrite_chapter_from_task')
    namespace = dict(reviews.__dict__)
    # Resolve the baseline against the same bounded fixture functions that the
    # live executor uses. No provider, historical grade or real DB is involved.
    async def baseline(*args, **kwargs):
        namespace.update(reviews.__dict__)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        return await namespace['rewrite_chapter_from_task'](*args, **kwargs)
    old_requests, _ = await _execute(db, monkeypatch, runtime, executor=baseline)
    new_requests, _ = await _execute(db, monkeypatch, runtime)
    assert len(old_requests) == len(new_requests) == 2
    # The voice-source protocol was deliberately versioned after this retained
    # executor snapshot. Under the same isolated transport boundaries primary
    # system/user/template bytes and both original version labels remain exact.
    assert old_requests[0][:3] == new_requests[0][:3]
    assert old_requests[0][3] == "3.0-source-selected-editor"
    assert new_requests[0][3] == "3.2-source-selected-voice-editor"
    assert old_requests[1][0] == new_requests[1][0]
    assert old_requests[1][2] == new_requests[1][2]
    assert old_requests[1][3] == "1.0"
    assert new_requests[1][3] == "2.0-candidate-source"
    assert old_requests[1][1] != new_requests[1][1]
    repair = new_requests[1]
    assert repair[1].count('CHAPTER_REWRITE_QUALITY_GATE_BLOCKED') == 1
    assert repair[1].count('CHAPTER_REWRITE_DUPLICATE_GATE') == 1


async def test_callback_advice_can_block_only_through_effective_action_configuration():
    from bestseller.services.rewrite_candidate_evidence import blocking_deterministic_violations
    from bestseller.services.write_gate import GateConfig
    code = 'CALLBACK_OBLIGATION_MISSING'
    result = blocking_deterministic_violations(_audit(code),
        band=SimpleNamespace(hard_min=10, hard_max=100), word_count=50,
        chapter_number=2, config=GateConfig(mode_by_violation={code: 'block'}),
        translate=reviews._deterministic_rewrite_violations)
    assert [finding['code'] for finding in result] == [code]

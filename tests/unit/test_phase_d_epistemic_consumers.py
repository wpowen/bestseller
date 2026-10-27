"""Pre-adoption hard checkers consume bound epistemic views, never raw claims."""
# ruff: noqa: F811
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_chapter_continuity_admission import candidate as candidate, configured as configured, response

from bestseller.infra.db.models import ChapterStateSnapshotModel
from bestseller.services import chapter_continuity_admission as admission, pipelines

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def preview_case(candidate,monkeypatch):
    session,settings,project,chapter,draft=candidate
    body='“她已经离开七年。”来客说。\n\n检验记录标明当前压力为42帕。\n\n另一位见证人拒绝确认离开日期。'
    draft.content_md=body
    await session.commit()
    calls=[]
    async def paid(*args,**kwargs):
        calls.append(True)
        return response(json.dumps({'facts':[
            {'name':'离开时长','value':'7','unit':'年','kind':'elapsed_story_time','source_quote':'她已经离开七年'},
            {'name':'记录压力','value':'42','unit':'帕','kind':'resource','source_quote':'检验记录标明当前压力为42帕'},
        ]},ensure_ascii=False))
    monkeypatch.setattr(admission,'complete_text',paid)
    snapshot=await admission.extract_continuity_admission_preview(session,settings,
        project_id=project.id,chapter=chapter,draft=draft,workflow_run_id=None)
    return session,settings,project,chapter,draft,snapshot,calls


async def test_candidate_current_and_previous_checker_channels_share_epistemic_policy(candidate,monkeypatch):
    session,_,project,chapter,draft,snapshot,calls=await preview_case(candidate,monkeypatch)
    before=deepcopy(snapshot.facts)
    seen=[]
    def check(current,previous):
        seen.append(current)
        assert [f.value for f in current.facts]==['42']
        assert current.reported_observations and current.epistemic_view_sha256
        assert current.epistemic_policy.startswith('candidate-preview:')
        assert current.epistemic_application_sha256 is None
        from bestseller.services.continuity import render_hard_fact_snapshot_block
        assert render_hard_fact_snapshot_block(current)==''
        assert previous is None
        return SimpleNamespace(passed=True,blocks_write=False)
    monkeypatch.setattr(pipelines,'check_countdown_arithmetic',check)
    monkeypatch.setattr(pipelines,'check_time_regression',check)
    reports=await pipelines._collect_phase_d_reports(session=session,project_id=project.id,
        chapter_number=chapter.chapter_number,snapshot=snapshot,strict=True,candidate_draft_id=draft.id)
    assert len(reports)==len(seen)==2 and len(calls)==1
    assert snapshot.facts==before and len(snapshot.facts['facts'])==2
    assert not list(await session.scalars(select(ChapterStateSnapshotModel)))
    assert draft.promotion_state=='under_review'


@pytest.mark.parametrize('fault',['raw','facts','scope','draft'])
async def test_unknown_or_changed_candidate_never_reaches_hard_checker(candidate,monkeypatch,fault):
    session,_,project,chapter,draft,snapshot,calls=await preview_case(candidate,monkeypatch)
    if fault=='raw':snapshot.raw_extraction+=' '
    elif fault=='facts':snapshot.facts={**snapshot.facts,'facts':[]}
    elif fault=='scope':snapshot.project_id=uuid4()
    else:draft_id=uuid4()
    def forbidden(*args,**kwargs):pytest.fail('unbound snapshot must not become a checker pass')
    monkeypatch.setattr(pipelines,'check_countdown_arithmetic',forbidden)
    monkeypatch.setattr(pipelines,'check_time_regression',forbidden)
    with pytest.raises((ValueError,RuntimeError)):
        await pipelines._collect_phase_d_reports(session=session,project_id=project.id,
            chapter_number=chapter.chapter_number,snapshot=snapshot,strict=True,
            candidate_draft_id=draft_id if fault=='draft' else draft.id)
    assert len(calls)==1 and not list(await session.scalars(select(ChapterStateSnapshotModel)))


async def test_reader_preserves_unflushed_caller_runtime_or_authored_metadata(candidate,monkeypatch):
    session,_,project,chapter,draft,snapshot,_=await preview_case(candidate,monkeypatch)
    chapter.metadata_json={**chapter.metadata_json,'unknown_authored_note':'Keep this pending edit.'}
    before=deepcopy(chapter.metadata_json)
    context=await admission.read_phase_d_snapshot_context(session,project_id=project.id,
        chapter_number=chapter.chapter_number,snapshot=snapshot,candidate_draft_id=draft.id)
    assert context.epistemic_view_sha256 and chapter.metadata_json==before
    assert chapter in session.dirty


async def test_disabling_phase_d_reports_does_not_disable_the_shared_fact_reader(candidate,monkeypatch):
    session,_,project,chapter,draft,snapshot,calls=await preview_case(candidate,monkeypatch)
    monkeypatch.setattr(pipelines,'get_quality_gates_config',lambda:SimpleNamespace(phase_d=SimpleNamespace(enabled=False)))
    snapshot._phase_d_epistemic_context='stale'
    assert await pipelines._collect_phase_d_reports(session=session,project_id=project.id,
        chapter_number=chapter.chapter_number,snapshot=snapshot)==[]
    assert snapshot._phase_d_epistemic_context is None
    # The independent monotonicity/genre consumers invoke this reader directly;
    # their availability does not depend on whether a Phase D report was built.
    context=await admission.read_phase_d_snapshot_context(session,project_id=project.id,
        chapter_number=chapter.chapter_number,snapshot=snapshot,candidate_draft_id=draft.id)
    assert [f.value for f in context.facts]==['42'] and len(calls)==1


async def test_snapshot_dependency_identity_is_equal_for_bulk_and_db_readers(candidate,monkeypatch):
    from bestseller.domain.context import ChapterStateSnapshotContext
    from bestseller.services import continuity
    from bestseller.services.feedback import KNOWLEDGE_SOURCE_KEYS
    _,_,project,chapter,draft,snapshot,_=await preview_case(candidate,monkeypatch)
    source=dict(snapshot.facts['snapshot_contract'])
    source={key:source[key] for key in KNOWLEDGE_SOURCE_KEYS}
    prior=ChapterStateSnapshotContext(chapter_number=1,epistemic_view_sha256='a'*64,
        epistemic_application_sha256='b'*64,epistemic_policy='fixture-policy')
    def build(value):
        return continuity.build_snapshot_epistemic_view(snapshot,source_body=draft.content_md,
            previous=continuity._SnapshotInput(chapter.id,value,prior))
    base=build(source)
    enriched={**source,'source_promotion_state':'promoted','adoption_state':'promoted',
              'adoption_receipt':{'routing_diagnostic':'preserved elsewhere'}}
    assert base==build(enriched)
    assert base['view_dependencies']['previous_source']==source
    for key in KNOWLEDGE_SOURCE_KEYS:
        changed={**source,key:source[key]+1 if isinstance(source[key],int) else str(source[key])+'x'}
        assert base['view_sha256']!=build(changed)['view_sha256']

"""Actual retained old release sources; reject unrelated semantic changes."""
from pathlib import Path
import pytest
from bestseller.services.draft_continuity_reference_transition import _verify_runtime_memo_and_task_delta

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('tamper',[None,'body_gate','source_projection','permission','scope_reset','task_fields','chronology','metadata_role','protocol_scope','legacy_projection'])
def test_exact_source_transition_and_unrelated_rule_rejection(tmp_path,tamper):
    project=Path(__file__).resolve().parents[2]
    witness=project/'tests/fixtures/source_memo_task_before_v2'
    names=['src/bestseller/services/draft_continuity.py','src/bestseller/services/chapter_writer_delivery.py']
    baseline={name:(witness/(Path(name).name+'.txt')).read_text() for name in names}
    current={name:(project/name).read_text() for name in names}
    # This suite isolates the older memo/task release transition. The later
    # applicable-reference dispatch has its own exact source-delta fence.
    later_dispatch="""    if proof.get('schema')=='draft-continuity-applicable-reference.v1':
        from bestseller.services.draft_continuity_applicable_review import validate_reference
        await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)
    elif proof.get('schema')=='draft-continuity-source-corrected-reference.v1':"""
    assert current[names[0]].count(later_dispatch)==1
    current[names[0]]=current[names[0]].replace(later_dispatch,"    if proof.get('schema')=='draft-continuity-source-corrected-reference.v1':")

    later_scope="""        from bestseller.services.retained_review_input_proof import historical_reference_route_scope
        async with historical_reference_route_scope(session,settings,project=project,chapter=chapter):
            return await _select_reference_without_source_scope(session,settings,project=project,chapter=chapter,_memo=memo)"""
    assert current[names[0]].count(later_scope)==1
    current[names[0]]=current[names[0]].replace(later_scope,"        return await _select_reference_without_source_scope(session,settings,project=project,chapter=chapter,_memo=memo)")
    changes={
        'body_gate':(names[0],"body_hash(draft.content_md)==proof['body_sha256']","True"),
        'source_projection':(names[0],'m.ChapterModel.chapter_number<chapter.chapter_number','m.ChapterModel.chapter_number<=chapter.chapter_number'),
        'permission':(names[0],"capability='reference_pending_manuscript'","capability='unrelated_capability'"),
        'scope_reset':(names[0],'_REFERENCE_SOURCE_MEMO.reset(token)','pass'),
        'task_fields':(names[1],"{'scenes','_meta'}","{'scenes','_meta','opening_situation'}"),
        'chronology':(names[1],'属于前章时点，不得照搬为本章现在','属于本章时点，可以照搬为本章现在'),
        'metadata_role':(names[1],"key not in {'scenes','_meta'}","key not in {'scenes'}"),
        'protocol_scope':(names[1],"selected[2]==project.id and selected[3]==chapter.id","True"),
        'legacy_projection':(names[1],"Immutable historical rendering protocol","Immutable historical rendering protocol")}
    if tamper=='legacy_projection':
        text=current[names[1]];offset=text.index('def _source_protocol_delivery_block_v1(')
        current[names[1]]=text[:offset]+text[offset:].replace("'schema':'approved-current-chapter-delivery.v1'","'schema':'unrecognized'",1)
        changes.pop('legacy_projection')
    if tamper and tamper!='legacy_projection':
        name,before,after=changes[tamper];assert before in current[name]
        current[name]=current[name].replace(before,after,1)
    for name,text in current.items():
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    if tamper:
        with pytest.raises(ValueError):_verify_runtime_memo_and_task_delta(baseline_sources=baseline,current_root=tmp_path)
    else:_verify_runtime_memo_and_task_delta(baseline_sources=baseline,current_root=tmp_path)

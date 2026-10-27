# ruff: noqa: F811
from types import SimpleNamespace
import pytest
from test_adopted_knowledge_lineage import db,_jsonb  # noqa:F401
from bestseller.services.review_source_context import load_review_source_context
from bestseller.services.lossless_rewrite_prompt import prepare_source_complete_prompt
from bestseller.services.editor_writing_voice_source import attach_current_editor_voice

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def test_editor_gets_current_concrete_voice_but_ordinary_review_is_unchanged(db):
    s,p,ch,accepted=db
    p.metadata_json={**(p.metadata_json or {}),'writing_profile':{'style':{'pov_type':'first-person',
        'custom_rules':['保留合法的远航笔记声部']},'pacing_profile':'slow'}}
    original=await load_review_source_context(s,project=p,chapter=ch,scenes=[],context_packet=SimpleNamespace(),content_md=accepted.content_md)
    editor=attach_current_editor_voice(original,p)
    voice=editor.pop('current_writing_voice')
    assert editor==original and 'current_writing_voice' not in original
    assert voice['data']['style']['pov_type']=='first-person'
    assert voice['data']['style']['custom_rules']==['保留合法的远航笔记声部']
    editor['current_writing_voice']=voice
    system,user,receipt=prepare_source_complete_prompt(system_prompt='Return repaired full chapter.',
        user_prompt='Exact rejected body:\n'+accepted.content_md+'\nTarget 2000; hard minimum 1800.',source_context=editor,output_tokens=16000)
    assert accepted.content_md in user and 'first-person' in user and '2000' in user and '1800' in user
    assert 'current_writing_voice' in user and 'first-person' not in system
    assert receipt['source_complete']

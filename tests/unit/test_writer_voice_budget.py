"""Complete voice is an integrity-protected USER source, not system authority."""
from copy import deepcopy
import json
import pytest
from bestseller.domain.project import WritingProfile
from bestseller.services.content_origin_policy import origin_record
from bestseller.services.writing_profile import render_writer_voice_data
from bestseller.services.drafts import _compile_rendered_writer_prompt
from bestseller.services.prompt_compiler import PromptBudgetError

pytestmark=pytest.mark.unit


def test_all_voice_values_and_only_exact_adoptions_survive_without_source_mutation():
    profile=WritingProfile.model_validate({'style':{'pov_type':'third-limited','tone_keywords':['朴素','冷峻'],
        'custom_rules':['保留收信人的迟疑。','末尾规则必须完整_END'],
        'reference_works':['author-selected-reference'], 'taboo_words':['无据读心']},
        'market':{'reader_promise':'The hearing will change the keeper.'},
        'serialization':{'opening_mandate':'Keep the envelope unopened.'}})
    profile._content_origins={
        'market.reader_promise':origin_record(profile.market.reader_promise,kind='explicit',adopted=True),
        'serialization.opening_mandate':origin_record('different historical value',kind='explicit',adopted=True)}
    before=deepcopy(profile.model_dump(mode='json'));origins=deepcopy(profile._content_origins)
    text=render_writer_voice_data(profile);value=json.loads(text.split('\n',1)[1])
    assert value['style']==before['style']
    assert value['adopted_profile_clauses']=={'market.reader_promise':before['market']['reader_promise']}
    assert '末尾规则必须完整_END' in text
    assert profile.model_dump(mode='json')==before and profile._content_origins==origins
    assert render_writer_voice_data(profile)==text


def test_voice_is_complete_user_reference_and_insufficient_budget_fails_before_dispatch(monkeypatch):
    monkeypatch.setattr('bestseller.services.novel_expression_guidance.expression_guidance_blocks',lambda **kw:())
    voice=render_writer_voice_data(WritingProfile.model_validate({'style':{
        'custom_rules':['Keep every negation, condition and timing boundary. '*20+'END_VOICE']}}))
    args={'path':'chapter_first','system_prompt':'Original renderer','user_prompt':'Current task',
          'section_texts':{'chapter_task':'Current task'},'system_section_texts':{'writer_voice':voice},
          'prompt_safety_margin':.1,'language':'zh-CN'}
    compiled=_compile_rendered_writer_prompt(**args,total_input_budget_tokens=4000)
    assert voice in compiled.user and voice not in compiled.system
    assert 'chapter_first.section.writer_voice' in compiled.report.source_integrity_blocks
    assert compiled.report.source_integrity_complete
    # Completeness does not turn this legacy data into a required system/canon block.
    assert compiled.report.required_block_count==2
    with pytest.raises(PromptBudgetError,match='critical source fields'):
        _compile_rendered_writer_prompt(**args,total_input_budget_tokens=256)

"""Execute real page handlers offline; never POST to a running application."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess

import pytest

from bestseller.services import creation_configuration as config

HTML = Path('src/bestseller/web/novel_quickstart.html').read_text()
pytestmark = pytest.mark.unit


def submission(primary=None, compatible=(), reader=None, secondary=()):
    return {'configuration': {'platforms': {'primary': primary, 'compatible': list(compatible)},
        'readers': {'primary': reader, 'secondary': list(secondary)},
        'book': {'total_chapters': 30, 'chapter_target': 2000, 'unit': 'zh_body_chars'}, 'field_sources': {'platforms': 'explicit'}},
        'execution_window': {'purpose': 'opening_window', 'start': 1, 'end': 20}}


def function(name):
    match = re.search(r'^  (?:async )?function ' + name + r'\([^\n]*\) \{[\s\S]*?^  \}', HTML, re.M)
    assert match, name
    return match.group()


def page(value, action, primary_change=None, primary_changes=()):
    script = r"""
const input=JSON.parse(process.argv[1]);
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,hidden:false,textContent:'',style:{},attrs:{},
setAttribute(k,v){this.attrs[k]=v;},addEventListener(name,fn){this[name]=fn;}});return elements.get(id);};
const compatible=['fanqie','qidian','qimao'].map(value=>({value,checked:false}));
const document={querySelectorAll:selector=>selector.endsWith(':checked')?compatible.filter(x=>x.checked):compatible};
const memory=new Map();
const localStorage={setItem(k,v){memory.set(k,v);},removeItem(k){memory.delete(k);},getItem:k=>memory.get(k)};
const configurationDraftKey='bestseller.creation-configuration.v1';
let configurationExpectedHash=null,configurationExpectedWindowHash=null,configurationLoadedProject=null,configurationPendingProposals=[];
let configurationSources={platforms:'default',readers:'default',book:'default'},chapterCount=30;
const DEFAULT_CHAPTER_COUNT=30,selectedLang='zh',creationMode='long_serial',selectedGenreKey='xianxia-upgrade';
const customSelection=null,selectedAudience=null,selectedNarrativeScale='serial',selectedTonePreference='',fanqieLengthKey='',fanqiePov='first_person';
const QUICKSTART_CLIENT_SCHEMA_VERSION='quickstart-client.v2',seCostStyleExplicit=false;
const selectedConceptBundle=()=>null, collectStoryEnhancers=()=>({effect_skills:[]});
const requests=[],alerts=[],history=[];const alert=x=>alerts.push(x);
const attachClientIntentManifest=async()=>{};
const fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});
if(url.endsWith('/preview'))return{ok:true,json:async()=>({whole_book_chapters:30,whole_book_target_chars:60000,window_range:[1,20],window_chapters:20})};
return{ok:false,json:async()=>({error:'offline-only transport boundary'})};};
$('#configurationEnabled').checked=true;$('#scheduleAt').value='2030-01-01T10:00';
"""
    script += '\n'.join(function(name) for name in (
        'collectCreationConfiguration', 'renderConfigurationDependencyError', 'validateCreationConfiguration',
        'restoreCreationConfiguration', 'configurationJson', 'configurationDraftSave',
        'configurationPreview', 'resetCreationConfiguration', 'buildQuickstartPayload',
        'startCreation', '_localIsoWithOffset', 'scheduleQuickstart'))
    save_handler = HTML.split("  $('#configurationSave').addEventListener", 1)[1].split(
        "  $('#configurationLoad').addEventListener", 1)[0]
    script += "\n$('#configurationSave').addEventListener" + save_handler
    script += r"""
(async()=>{
restoreCreationConfiguration(input.value);
if(input.primary_change!==null)$('#configurationPrimaryPlatform').value=input.primary_change;
renderConfigurationDependencyError(collectCreationConfiguration());
for(const primary of input.primary_changes){
$('#configurationPrimaryPlatform').value=primary;
renderConfigurationDependencyError(collectCreationConfiguration());
history.push({platforms:collectCreationConfiguration().configuration.platforms,
checks:compatible.filter(x=>x.checked).map(x=>x.value),message:$('#configurationValidation').textContent});
}
if(input.action==='reset')resetCreationConfiguration();
if(input.action==='start')await startCreation();
if(input.action==='schedule')await scheduleQuickstart();
if(input.action==='preview')try{await configurationPreview();}catch(e){alerts.push(e.message);}
if(input.action==='save')await $('#configurationSave').click();
console.log(JSON.stringify({submission:collectCreationConfiguration(),requests,alerts,
inline:$('#configurationValidation').textContent,inlineHidden:$('#configurationValidation').hidden,history,
compatibleChecks:compatible.filter(x=>x.checked).map(x=>x.value),drafts:[...memory]}));
})().catch(e=>{console.error(e.stack);process.exit(1)});
"""
    completed = subprocess.run(['/opt/homebrew/bin/node', '-e', script,
        json.dumps({'value': value, 'action': action, 'primary_change': primary_change, 'primary_changes': primary_changes})],
        capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


@pytest.mark.parametrize('action', ['preview', 'save', 'start', 'schedule'])
def test_neutral_empty_platforms_are_valid_in_all_real_page_handlers(action):
    value = submission()
    result = page(value, action)
    assert result['inlineHidden'] and result['requests']
    body = result['requests'][0]['body']
    submitted = body.get('configuration_submission', body)
    assert submitted['configuration']['platforms'] == {'primary': None, 'compatible': []}
    assert config.parse_configuration_submission(submitted).configuration.platforms.primary is None


@pytest.mark.parametrize('action', ['preview', 'save', 'start', 'schedule'])
def test_dangling_compatible_draft_is_preserved_visible_and_never_sent(action):
    value = submission(compatible=['fanqie', 'qidian'])
    original = deepcopy(value)
    result = page(value, action)
    assert result['requests'] == []
    assert result['compatibleChecks'] == ['fanqie', 'qidian']
    assert result['submission']['configuration']['platforms'] == value['configuration']['platforms']
    assert '请选择' not in result['inline']  # precise dependency, not a generic error
    assert '请先选择主平台' in result['inline'] and not result['inlineHidden']
    with pytest.raises(ValueError) as error:
        config.parse_configuration_submission(value)
    assert str(error.value) == result['inline'] and value == original


def test_reset_drops_only_new_draft_choices_without_seeding_a_platform():
    result = page(submission(compatible=['fanqie', 'qidian']), 'reset')
    assert result['compatibleChecks'] == [] and result['inlineHidden']
    assert result['submission']['configuration']['platforms'] == {'primary': None, 'compatible': []}


def test_clearing_primary_preserves_explicit_compatibility_as_an_invalid_draft():
    result = page(submission('qidian', ['fanqie']), 'start', primary_change='')
    assert not result['requests'] and '请先选择主平台' in result['inline']
    assert result['compatibleChecks'] == ['fanqie']
    assert result['submission']['configuration']['platforms']['primary'] is None


def test_selecting_primary_preserves_compatible_and_full_user_fields():
    value = submission(compatible=['fanqie'], reader='精密推演读者', secondary=['幽默读者'])
    result = page(value, 'start', primary_change='qidian')
    body = result['requests'][0]['body']['configuration_submission']
    assert body['configuration']['platforms'] == {'primary': 'qidian', 'compatible': ['fanqie']}
    assert body['configuration']['readers'] == value['configuration']['readers']
    assert body['configuration']['book'] == value['configuration']['book']


def test_primary_compatibility_roundtrip_keeps_dom_choice_but_blocks_cleared_primary():
    result = page(submission('qidian', ['fanqie']), 'start', primary_changes=['fanqie', ''])
    assert result['history'][0]['platforms'] == {'primary': 'fanqie', 'compatible': []}
    assert result['history'][0]['checks'] == ['fanqie']
    assert result['history'][1]['platforms'] == {'primary': None, 'compatible': ['fanqie']}
    assert result['history'][1]['checks'] == ['fanqie']
    assert '请先选择主平台' in result['history'][1]['message']
    assert not result['requests']


def test_secondary_reader_dependency_uses_same_frontend_backend_contract():
    value = submission(secondary=['次要读者'])
    result = page(value, 'start')
    assert not result['requests'] and '主要读者' in result['inline']
    with pytest.raises(ValueError) as error:
        config.parse_configuration_submission(value)
    assert str(error.value) == result['inline']


def test_real_quickstart_rejects_before_task_dispatch_and_accepts_neutral(monkeypatch):
    from bestseller.web import server
    captured = []
    def no_dispatch(self, payload):
        captured.append(deepcopy(payload))
        return {'task_id': 'offline-placeholder-not-created'}
    monkeypatch.setattr(server.WebTaskManager, 'create_autowrite_task', no_dispatch)
    manager = server.WebTaskManager()
    with pytest.raises(ValueError, match='请先选择主平台'):
        manager.create_quickstart_task({'genre_key': 'xianxia-upgrade', 'chapter_count': 30,
            'configuration_submission': submission(compatible=['fanqie', 'qidian'])})
    assert captured == [] and manager.list_tasks() == []
    manager.create_quickstart_task({'genre_key': 'xianxia-upgrade', 'chapter_count': 30,
        'configuration_submission': submission()})
    assert len(captured) == 1
    assert captured[0]['configuration_submission']['configuration']['platforms'] == {'primary': None, 'compatible': []}
    assert manager.list_tasks() == []

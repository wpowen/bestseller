"""Q4 short-chain contracts. Pure request/entry order evidence, no admission."""
import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import fanqie_beat_recovery as beat
from bestseller.services import fanqie_outline_contract_completion as completion
from bestseller.services.source_bound_planning_content import new_reader_first_binding

pytestmark = pytest.mark.unit


def project(marked):
    marker = new_reader_first_binding('An ordinary family choice and its emotional consequences.')
    return SimpleNamespace(id=uuid4(), target_chapters=4, language='zh-CN',
        metadata_json={'writing_profile': {'reader_first_planning': marker}} if marked else {}), marker


@pytest.mark.parametrize('stage', ['beat', 'completion'])
def test_native_short_planning_requests_carry_reader_protocol_without_changing_legacy(stage):
    p, marker = project(True)
    if stage == 'beat':
        source = {'effective_pov': {'beat_sheet_pov': 'first_person'}}
        request = beat.build_current_request(p, source, workflow_run_id=uuid4())
    else:
        source = {'selected_source': {'project_inputs': {'target_chapters': 4}},
                  'original_beat': {'raw': {'beats': [{'segment_number': n} for n in range(1,5)]}},
                  'base_outline': {'chapters': [{'chapter_number': n} for n in range(1,5)]}}
        request = completion._request(p, source, {}, [], {'/chapters/0/title': {'type': 'string'}},
                                      uuid4(), 1, 1, None)
    assert 'reader-first-planning.v1' in request.system_prompt
    assert request.metadata['reader_first_planning'] == marker
    assert 'reader-first' in request.prompt_version
    p.metadata_json = {}
    if stage == 'beat':
        old = beat.build_current_request(p, source, workflow_run_id=request.workflow_run_id)
        assert old.system_prompt == beat.SYSTEM and old.prompt_version == beat.PROMPT_VERSION
    else:
        old = completion._request(p, source, {}, [], {'/chapters/0/title': {'type': 'string'}},
                                  request.workflow_run_id, 1, 1, None)
        assert old.system_prompt == completion.SYSTEM and old.prompt_version == completion.PROMPT_VERSION
    assert 'reader_first_planning' not in old.metadata


def test_short_final_reader_checkpoint_precedes_artifact_write_and_materialization():
    source = Path(beat.__file__).with_name('fanqie_short_pipeline.py')
    tree = ast.parse(source.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == 'run_fanqie_short_pipeline')
    calls = {n.func.id: n.lineno for n in ast.walk(function)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert 'prepare_reader_first_short_outline' in calls
    assert calls['complete_missing_outline_contract'] < calls['prepare_reader_first_short_outline']
    assert calls['prepare_reader_first_short_outline'] < calls['_write_fanqie_source_artifacts']
    assert calls['prepare_reader_first_short_outline'] < calls['persist_fanqie_chapter_outline']

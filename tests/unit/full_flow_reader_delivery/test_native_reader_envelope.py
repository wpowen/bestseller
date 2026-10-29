"""SQLite/physical custody only; selected plan is a premise, not native adoption."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm.attributes import flag_modified
from test_foundation_field_recovery import record_completion
from test_retained_planning_generation import case as case
from test_q4_reader_first_planner import outline

from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import llm
from bestseller.services import planner
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def reader_case(case):
    c = case
    binding = protocol.new_reader_first_binding('一家人决定重新一起吃晚饭。')
    c.project.target_chapters = 4
    c.project.metadata_json = {**c.project.metadata_json, 'reader_first_planning': binding}
    source = {'outline': outline(4)}
    raw = {'schema': 'reader-first-review.v1', 'checkpoint': 'outline',
           'source_sha256': content_hash(source), 'config_sha256': binding['config_sha256'],
           'dimensions': {name: {'score': .8, 'reason': 'Fixture choice changes the relationship.',
                                'evidence': [{'path': '/outline/chapters/0/chapter_goal',
                                              'quote': source['outline']['chapters'][0]['chapter_goal']}]}
                          for name in protocol.READER_FIRST_DIMENSIONS}, 'issues': [], 'options': ['Keep the relationship and clarify the choice.', 'Keep the choice and clarify its consequence.']}
    parsed = protocol.parse_reader_first_review(raw, source=source, binding=binding,
                                                checkpoint='outline')
    assert parsed['status'] == 'passed'
    system, user = protocol.reader_first_review_prompts(binding, source=source, checkpoint='outline')
    request = llm.LLMCompletionRequest(
        logical_role='critic', project_id=c.project.id, workflow_run_id=c.original.id,
        system_prompt=system, user_prompt=user, fallback_response='{}',
        prompt_template='planner_reader_first_outline_review', prompt_version='rf-outline-review-v1',
        max_tokens_override=4096, temperature_override=.2,
        metadata={'source_sha256': content_hash(source), 'reader_first_planning': binding,
                  'checkpoint': 'outline',
                  'coverage': planner._reader_first_outline_coverage(source, c.project, binding)},
    )
    paid, run = await record_completion(c, request, content=json.dumps(raw, ensure_ascii=False))
    entry = {'source_sha256': content_hash(source), 'binding': binding,
             'workflow_run_id': str(c.original.id), 'source': source,
             'raw_review': paid.content, 'receipt': parsed, 'llm_run_id': str(run.id)}
    checkpoint = {'schema': 'reader-first-outline-checkpoint.v1', 'status': 'passed',
                  'history': [entry], 'review_dispatches': 1, 'repairs_used': 0}
    c.project.metadata_json = {**c.project.metadata_json,
                               'reader_first_outline_checkpoint': checkpoint}
    await c.session.commit()
    c.envelope_artifact = SimpleNamespace(id=uuid4(), project_id=c.project.id,
                                         content=deepcopy(source['outline']))
    c.envelope_chapter = SimpleNamespace(id=uuid4(), project_id=c.project.id, chapter_number=1)
    c.reader_run, c.reader_entry = run, entry
    c.reader_source = source
    c.original_receipt = {'chapter_numbers': [1, 2, 3, 4], 'keep': {'exact': [True, 1]}}
    return c


async def invoke(c):
    function = getattr(delivery, 'load_reader_first_source_receipt', None)
    assert callable(function), 'READER_FIRST_NATIVE_ENVELOPE_NOT_IMPLEMENTED'
    return await function(c.session, c.settings, project=c.project,
                          chapter=c.envelope_chapter, artifact=c.envelope_artifact,
                          receipt=c.original_receipt)


async def test_actual_settled_reader_return_builds_detached_envelope_without_writes(reader_case):
    c = reader_case
    before = deepcopy(c.original_receipt)
    writes = len(c.writes)
    result = await invoke(c)
    envelope = result['reader_first_source']
    assert envelope['source'] == c.reader_source
    assert envelope['reader_receipt'] == c.reader_entry['receipt']
    assert envelope['chapter_pointer'] == '/outline/chapters/0'
    assert envelope['source_binding']['artifact_id'] == str(c.envelope_artifact.id)
    assert c.original_receipt == before
    assert len(c.writes) == writes and not c.denied
    envelope['source']['outline']['chapters'].clear()
    assert len((await invoke(c))['reader_first_source']['source']['outline']['chapters']) == 4


@pytest.mark.parametrize('fault', ['raw', 'source_type', 'reader_id', 'response_bytes',
                                  'unknown_operation', 'artifact', 'foreign_chapter',
                                  'wrong_role', 'metadata_only'])
async def test_corrupt_reader_custody_never_grants_delivery(reader_case, fault):
    c = reader_case
    meta = deepcopy(c.project.metadata_json)
    entry = meta['reader_first_outline_checkpoint']['history'][0]
    if fault == 'raw':
        entry['raw_review'] += ' changed'
    elif fault == 'source_type':
        entry['source']['outline']['chapters'][0]['chapter_number'] = True
    elif fault == 'reader_id':
        entry['llm_run_id'] = str(uuid4())
    elif fault == 'response_bytes':
        path = c.paths[f'{c.reader_run.id}:response']
        value = json.loads(path.read_text())
        value['content']['text'] += ' changed'
        path.write_text(json.dumps(value))
    elif fault == 'unknown_operation':
        from bestseller.infra.db.models import ExecutionOperationModel
        operation = await c.session.get(ExecutionOperationModel,
                                        c.reader_run.metadata_json['execution_receipt']['operation_key'])
        operation.status = 'unknown'
    elif fault == 'artifact':
        c.envelope_artifact.content['chapters'][0]['chapter_goal'] += ' changed'
    elif fault == 'foreign_chapter':
        c.envelope_chapter.project_id = uuid4()
    elif fault == 'wrong_role':
        c.reader_run.logical_role = 'planner'
    elif fault == 'metadata_only':
        c.reader_run.request_payload_ref = None
    c.project.metadata_json = meta
    # Explicitly persist corrupt JSON types: SQLAlchemy sees True == 1 otherwise.
    flag_modified(c.project, 'metadata_json')
    await c.session.commit()
    with pytest.raises(ValueError):
        await invoke(c)
    assert not c.denied


async def test_unsaved_checkpoint_cannot_override_native_reader_history(reader_case):
    c = reader_case
    metadata = deepcopy(c.project.metadata_json)
    metadata['reader_first_outline_checkpoint']['history'][0]['raw_review'] = '{}'
    c.project.metadata_json = metadata
    result = await invoke(c)
    assert result['reader_first_source']['review'] == c.reader_entry['raw_review']
    assert c.project in c.session.dirty

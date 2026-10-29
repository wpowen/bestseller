"""An initial body must exist as an ordinary candidate before paid repair."""
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from bestseller.services import drafts
from bestseller.services.source_bound_planning_content import new_reader_first_binding

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]
BODY='她回头叫了一声妈妈。修订说明：本章还需修改。'

@pytest.mark.parametrize('marked',[True,False])
async def test_initial_cleanup_retains_marked_body_until_native_revision(monkeypatch,marked):
    fn=getattr(drafts,'_clean_initial_chapter_content',None)
    assert callable(fn), 'INITIAL_BODY_REPAIR_BEFORE_NATIVE_SOURCE'
    project=SimpleNamespace(id=uuid4(),metadata_json={'reader_first_planning':
        new_reader_first_binding('An ordinary family choice.')} if marked else {})
    cleaner=AsyncMock(return_value='她回头叫了一声妈妈。')
    monkeypatch.setattr(drafts,'validate_and_clean_novel_content',cleaner)
    text,deferred=await fn(None,object(),BODY,project=project,workflow_run_id=uuid4(),step_run_id=None)
    assert deferred is marked
    assert text==(BODY if marked else '她回头叫了一声妈妈。')
    assert cleaner.await_count==(0 if marked else 1)

async def test_clean_initial_body_does_not_create_a_false_repair_debt(monkeypatch):
    fn=getattr(drafts,'_clean_initial_chapter_content',None)
    assert callable(fn), 'INITIAL_BODY_REPAIR_BEFORE_NATIVE_SOURCE'
    cleaner=AsyncMock()
    monkeypatch.setattr(drafts,'validate_and_clean_novel_content',cleaner)
    project=SimpleNamespace(id=uuid4(),metadata_json={'reader_first_planning':
        new_reader_first_binding('An ordinary family choice.')})
    text='她回头叫了一声妈妈。'
    assert await fn(None,object(),text,project=project,workflow_run_id=uuid4(),step_run_id=None)==(text,False)
    assert cleaner.await_count==0

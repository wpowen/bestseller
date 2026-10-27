import asyncio
from types import SimpleNamespace
import pytest
from bestseller.services.commercial_sample_execution import serial_commercial_samples
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def test_three_independent_owned_sessions_commit_before_next_entry_and_keep_arguments():
 events=[]
 class Session:
  def __init__(self,slot):self.slot=slot
  async def commit(self):events.append(('commit',self.slot))
  async def rollback(self):events.append(('rollback',self.slot))
 async def evaluate(session,settings,**kw):
  events.append(('start',session.slot));await asyncio.sleep(0)
  assert kw=={'sample_slot':session.slot,'body_hash':'same-body','evaluation_contract':'unchanged'}
  return {'slot':session.slot,'native_result':True}
 async def wait(awaitable,**kw):return await awaitable
 judge=SimpleNamespace(judge_chapter_commercial_quality=evaluate);review=SimpleNamespace(_await_optional_chapter_review_llm=wait)
 with serial_commercial_samples(judge_module=judge,review_module=review) as audit:
  results=await asyncio.gather(*(judge.judge_chapter_commercial_quality(Session(i),None,sample_slot=i,body_hash='same-body',evaluation_contract='unchanged') for i in range(3)))
 assert events==[('start',0),('commit',0),('start',1),('commit',1),('start',2),('commit',2)]
 assert [r['slot'] for r in results]==[0,1,2] and audit['max_active']==1
 assert judge.judge_chapter_commercial_quality is evaluate

async def test_failure_rolls_back_owned_sample_without_inventing_a_result():
 events=[]
 class Session:
  async def rollback(self):events.append('rollback')
  async def commit(self):events.append('commit')
 async def fail(*args,**kw):raise RuntimeError('original native failure')
 async def wait(awaitable,**kw):return await awaitable
 judge=SimpleNamespace(judge_chapter_commercial_quality=fail);review=SimpleNamespace(_await_optional_chapter_review_llm=wait)
 with serial_commercial_samples(judge_module=judge,review_module=review):
  with pytest.raises(RuntimeError,match='original native failure'):await judge.judge_chapter_commercial_quality(Session(),None,sample_slot='original')
 assert events==['rollback']

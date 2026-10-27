"""Actual SQLite history reads require a successful matching character receipt."""
from copy import deepcopy
import pytest
from test_character_snapshot_sources import configured as configured, db as db, local_only as local_only
from test_adopted_bible_lineage import stub_llm
from bestseller.services import story_bible
from bestseller.services.character_snapshot_sources import read_character_snapshot_knowledge
from bestseller.settings import load_settings

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('fault',['none','missing_receipt','failed_receipt','payload_mismatch','wrong_character'])
async def test_history_requires_success_payload_and_character(configured,monkeypatch,fault):
 session,project,chapter,draft,character=configured
 payload={'character_updates':[{'name':character.name,'knowledge_gained':['钥匙在桌上'],'false_beliefs':[]}],
          'relationship_updates':[],'world_updates':[]}
 calls=stub_llm(monkeypatch,response=payload)
 result=await story_bible.update_story_bible_from_chapter(session,load_settings(),project=project,chapter=chapter,
  chapter_text=draft.content_md,source_chapter_draft_version_id=draft.id)
 assert result['extraction_status']=='ok' and len(calls)==1
 await session.commit()
 cm=deepcopy(chapter.metadata_json);m=deepcopy(character.metadata_json)
 if fault=='missing_receipt':cm['story_bible_update_receipts']=[]
 elif fault=='failed_receipt':cm['story_bible_update_receipts'][-1]['status']='requires_projection_reconciliation'
 elif fault=='payload_mismatch':cm['story_bible_update_receipts'][-1]['candidate_payload']['character_updates']=[]
 elif fault=='wrong_character':
  m['story_bible_update_history'][-1]['update']['name']='另一名角色'
  cm['story_bible_update_receipts'][-1]['candidate_payload']['character_updates'][0]['name']='另一名角色'
 chapter.metadata_json=cm;character.metadata_json=m;await session.commit()
 before=(deepcopy(character.metadata_json),deepcopy(chapter.metadata_json),deepcopy(character.knowledge_state_json))
 knowledge=await read_character_snapshot_knowledge(session,project_id=project.id,character=character,before_chapter_number=2,before_scene_number=1)
 assert knowledge.get('knows',[])==(['钥匙在桌上'] if fault=='none' else [])
 assert before==(character.metadata_json,chapter.metadata_json,character.knowledge_state_json)

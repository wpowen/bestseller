"""Selected foundation IDs retain their contract; chapter names cannot rename them."""
import hashlib,json
from uuid import uuid4

import pytest
from sqlalchemy import select
from bestseller.infra.db.models import WorldRuleModel
from bestseller.services import story_bible as bible
from test_world_rule_identity_recovery import db as db, knowledge_db as knowledge_db, offline as offline
from test_story_bible_services import build_world_spec

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def test_actual_selected_world_spec_explicit_id_revises_display_name_with_evidence(db):
    session,project,_,_=db
    row=WorldRuleModel(id=uuid4(),project_id=project.id,rule_code='R001',name='旧规则标题',description='旧描述',metadata_json={'opaque':{'retain':[1]},'story_bible_update_history':[]})
    session.add(row);await session.flush();old_id=row.id
    content=build_world_spec();content['locations']=[];content['factions']=[];content['rules']=[{'rule_id':'R001','name':'作者新版规则','description':'明确的新基础规则描述'}]
    assert (await bible.upsert_world_spec(session,project,content))['world_rules_upserted']==1
    await session.flush()
    assert row.id==old_id and row.name=='作者新版规则' and row.rule_code=='R001'
    assert row.metadata_json['opaque']=={'retain':[1]}
    change=row.metadata_json['world_spec_rule_identity_changes'][-1]
    assert change['previous_name']=='旧规则标题' and change['current_name']=='作者新版规则'
    assert change['selected_spec_sha256']==hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    history=list(row.metadata_json['world_spec_rule_identity_changes'])
    await bible.upsert_world_spec(session,project,content)
    assert row.metadata_json['world_spec_rule_identity_changes']==history
    assert len(list(await session.scalars(select(WorldRuleModel).where(WorldRuleModel.project_id==project.id))))==1


async def test_same_code_different_chapter_name_still_rejected(db):
    session,project,_,_=db
    row=WorldRuleModel(id=uuid4(),project_id=project.id,rule_code='R001',name='作者规则',description='保留')
    session.add(row);await session.flush()
    with pytest.raises(ValueError,match='world_rule_identity_code_conflict'):
        await bible._get_world_rule_by_identity(session,project_id=project.id,rule_id=uuid4(),rule_code='R001',name='不同章节规则')
    assert row.name=='作者规则' and row.description=='保留'


async def test_conflicting_explicit_code_and_existing_name_fail_closed(db):
    session,project,_,_=db
    rows=[WorldRuleModel(id=uuid4(),project_id=project.id,rule_code=code,name=name,description='保留') for code,name in [('R001','甲'),('R002','乙')]]
    session.add_all(rows);await session.flush()
    with pytest.raises(ValueError,match='world_rule_identity_code_name_conflict'):
        await bible._get_world_rule_by_identity(session,project_id=project.id,rule_id=uuid4(),rule_code='R001',name='乙',explicit_world_spec_code=True)
    assert [(r.rule_code,r.name) for r in rows]==[('R001','甲'),('R002','乙')]

# ruff: noqa: F811
from uuid import uuid4
from copy import deepcopy
from sqlalchemy import select
import pytest
from test_chapter_outline_readiness import rig,sqlite_identity_store  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case  # noqa:F401
from test_prefix_window_activation_sqlite import setup
from bestseller.infra.db.models import ProjectModel,ChapterModel,ChapterDraftVersionModel
from bestseller.services.inspected_repair_source import select_inspected_repair_source
from bestseller.services.prompt_source_policy import content_hash
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('fault',[None,'no_consent','used_budget','source_changed','adopted'])
async def test_explicit_working_source_preserves_counters_and_never_adopts(entry_case,monkeypatch,fault):
    c=entry_case;await setup(c,monkeypatch)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id);ch=await s.scalar(select(ChapterModel).where(ChapterModel.project_id==p.id))
        old=ChapterDraftVersionModel(id=uuid4(),project_id=p.id,chapter_id=ch.id,version_no=1,content_md='lamp '*500,word_count=500,is_current=True,promotion_state='candidate')
        src=ChapterDraftVersionModel(id=uuid4(),project_id=p.id,chapter_id=ch.id,version_no=2,content_md='signal '*ch.target_word_count,word_count=ch.target_word_count,is_current=False,promotion_state='candidate')
        s.add_all([old,src]);ch.metadata_json={**ch.metadata_json,'chapter_first_local_repair_count':1,
            'auto_repair_total_attempts':2,'rewrite_attempts_by_kind':{'length':3},'chapter_first_full_regeneration_count':1,
            'repair_plateau':{'no_gain':4},'chapter_critic_cycles':{'old':{'state':'waiting_infrastructure'}}}
        if fault=='used_budget':ch.metadata_json={**ch.metadata_json,'chapter_first_local_repair_count':2}
        if fault=='adopted':old.promotion_state='promoted'
        await s.commit();before=deepcopy(ch.metadata_json);paid=len(c.sends)
        kw=dict(project=p,chapter=ch,expected_current_id=old.id,expected_current_body_hash=content_hash(old.content_md),
            source_id=src.id,source_body_hash='wrong' if fault=='source_changed' else content_hash(src.content_md),
            inspection={'repair_scope':['pov','source_history_scope'],'new_length_expansion':False,'grade_asserted':False},
            actor='fixture-human',authorization_ref='fixture-only',explicit_user_approval=fault!='no_consent')
        if fault:
            with pytest.raises(ValueError):await select_inspected_repair_source(s,c.settings,**kw)
            assert old.is_current and not src.is_current and ch.metadata_json==before
        else:
            result=await select_inspected_repair_source(s,c.settings,**kw)
            assert result['binding']['selection_only'] and not result['binding']['adoption_authorized']
            assert src.is_current and not old.is_current and src.promotion_state=='candidate'
            for k,v in before.items():assert ch.metadata_json[k]==v
            await s.commit()
            again=await select_inspected_repair_source(s,c.settings,**kw)
            assert again['already_selected'] and again['workflow_id']==result['workflow_id']
            assert ch.metadata_json['current_body_review_status']=='pending'
            assert ch.metadata_json['current_body_draft_id']==str(src.id)
        assert len(c.sends)==paid

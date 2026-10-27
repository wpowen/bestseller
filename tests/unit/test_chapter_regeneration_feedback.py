import hashlib
from types import SimpleNamespace
from uuid import uuid4
import pytest
from bestseller.services.chapter_regeneration_feedback import build_regeneration_feedback,append_regeneration_feedback
pytestmark=pytest.mark.unit

def test_exact_candidate_feedback_changes_request_not_cache_namespace_or_story():
    p=SimpleNamespace(id=uuid4(),language='zh-CN');c=SimpleNamespace(id=uuid4(),target_word_count=2000)
    d=SimpleNamespace(id=uuid4(),project_id=p.id,chapter_id=c.id,version_no=1,content_md='我核对航标，没有签字。')
    f=build_regeneration_feedback(p,c,d,['LENGTH_UNDER','POV_DRIFT'],'Actual rejected draft is short and wrong POV')
    user='Approved exact world and scene source remains unchanged.'
    rendered=append_regeneration_feedback(user,f)
    assert rendered.startswith(user) and d.content_md in rendered
    assert f['source_body_sha256']==hashlib.sha256(d.content_md.encode()).hexdigest()
    assert f['block_codes']==['LENGTH_UNDER','POV_DRIFT'] and f['target_word_count']==2000
    assert rendered==append_regeneration_feedback(user,f)
    f['source_body']+='Unbound addition'
    with pytest.raises(ValueError):append_regeneration_feedback(user,f)


def test_length_and_pov_repairs_do_not_freeze_the_invalid_old_voice():
    from bestseller.services.pipelines import _render_chapter_first_local_repair_instructions
    c=SimpleNamespace(chapter_number=4,target_word_count=2000,current_word_count=921,metadata_json={})
    mixed=_render_chapter_first_local_repair_instructions(chapter=c,block_codes=('LENGTH_UNDER','POV_DRIFT'),scene_hints=[])
    assert '统一叙述视角' in mixed and '一字不改地全部保留' not in mixed and '2000' in mixed
    only=_render_chapter_first_local_repair_instructions(chapter=c,block_codes=('LENGTH_UNDER',),scene_hints=[])
    assert '一字不改地全部保留' in only

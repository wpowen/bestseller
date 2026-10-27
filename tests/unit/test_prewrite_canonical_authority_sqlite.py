"""Editorial approval preserves the canonical source's stronger authority."""
from copy import deepcopy
from uuid import uuid4
import pytest
from test_bounded_prefix_consumer_sqlite import consumer as consumer  # noqa:F401
from test_bounded_prefix_consumer_sqlite import bounded as bounded,volume as volume,harness as harness,prefix_rig as prefix_rig  # noqa:F401
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.services.prewrite_review import approve_prewrite_materials
from bestseller.services.prompt_source_policy import content_hash

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def test_real_sqlite_approval_and_repeat_never_demote_canonical_source(consumer):
    h=consumer;identity=uuid4()
    content={'character':'Mara','global_rule':'The clock remains sealed.'}
    async with h.session_factory() as s:
        project=await s.get(ProjectModel,h.project.id)
        row=PlanningArtifactVersionModel(id=identity,project_id=project.id,
            artifact_type='conception_snapshot',version_no=1,status='canonical',
            schema_version='fixture.v1',content=deepcopy(content),created_by='isolated-fixture')
        s.add(row);await s.commit()
        for _ in range(2):
            result=await approve_prewrite_materials(s,project.slug,approved_by='ISOLATED FIXTURE')
            await s.commit();await s.refresh(row)
            assert row.status=='canonical' and content_hash(row.content)==content_hash(content)
            assert result['current_snapshot']['conception_snapshot']['status']=='canonical'
            assert project.metadata_json['prewrite_review']['approved_snapshot']['conception_snapshot']['artifact_id']==str(identity)

"""Downstream prose diagnostics cannot invalidate an unchanged source approval."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_bounded_prefix_consumer_sqlite import consumer as consumer  # noqa: F401
from test_bounded_prefix_consumer_sqlite import bounded as bounded, volume as volume, harness as harness, prefix_rig as prefix_rig  # noqa: F401
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.services.prewrite_review import approve_prewrite_materials, load_prewrite_review_payload

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize('artifact_type,expected', [
    ('fanqie_long_ranking_readiness', True),
    ('book_spec', False), ('world_spec', False), ('cast_spec', False),
    ('volume_plan', False), ('chapter_outline_batch', False),
    ('unknown_authored_contract', False),
])
async def test_real_reopen_preserves_approval_only_for_known_downstream(consumer, artifact_type, expected):
    h = consumer
    async with h.session_factory() as s:
        p = await s.get(ProjectModel, h.project.id)
        await approve_prewrite_materials(s, p.slug, approved_by='ISOLATED TEST')
        original = deepcopy(p.metadata_json['prewrite_review'])
        # A new downstream result or a genuinely changed source appears later.
        row = PlanningArtifactVersionModel(id=uuid4(), project_id=p.id,
            artifact_type=artifact_type, version_no=1000, status='approved',
            schema_version='test.v1', content={'new': 'observatory'}, created_by='ISOLATED TEST')
        s.add(row)
        await s.commit()
        identity = row.id
    async with h.session_factory() as s:
        p = await s.get(ProjectModel, h.project.id)
        result = await load_prewrite_review_payload(s, p.slug)
        assert result['is_approved'] is expected
        assert p.metadata_json['prewrite_review'] == original
        assert (await s.get(PlanningArtifactVersionModel, identity)).content == {'new': 'observatory'}
        if expected:
            assert artifact_type not in result['current_snapshot']
            assert result['snapshot_projection']['excluded_downstream_current'][artifact_type]['artifact_id'] == str(identity)
            assert next(m for m in result['materials'] if m['artifact_type'] == artifact_type)['applicability'] == 'downstream_evidence'
        else:
            assert artifact_type in result['stale_artifact_types']


async def test_legacy_approval_retains_raw_downstream_entry_but_compares_sources(consumer):
    h = consumer
    async with h.session_factory() as s:
        p = await s.get(ProjectModel, h.project.id)
        await approve_prewrite_materials(s, p.slug, approved_by='ISOLATED TEST')
        review = deepcopy(p.metadata_json['prewrite_review'])
        legacy = {'artifact_id': str(uuid4()), 'version_no': 1, 'status': 'approved'}
        review['approved_snapshot']['fanqie_long_ranking_readiness'] = legacy
        p.metadata_json = {**p.metadata_json, 'prewrite_review': review}
        await s.commit()
        result = await load_prewrite_review_payload(s, p.slug)
        assert result['is_approved']
        assert result['approved_snapshot']['fanqie_long_ranking_readiness'] == legacy
        assert result['snapshot_projection']['excluded_downstream_approved']['fanqie_long_ranking_readiness'] == legacy
        assert p.metadata_json['prewrite_review'] == review


@pytest.mark.parametrize('field', ['id', 'status'])
async def test_original_source_identity_and_status_changes_still_invalidate(consumer, field):
    h = consumer
    async with h.session_factory() as s:
        p = await s.get(ProjectModel, h.project.id)
        source = PlanningArtifactVersionModel(id=uuid4(), project_id=p.id,
            artifact_type='custom_author_contract', version_no=1, status='canonical',
            schema_version='test.v1', content={'rule': 'only one moon'}, created_by='ISOLATED TEST')
        s.add(source)
        await s.flush()
        await approve_prewrite_materials(s, p.slug, approved_by='ISOLATED TEST')
        original = deepcopy(p.metadata_json['prewrite_review'])
        if field == 'status':
            source.status = 'rejected'
        else:
            source.id = uuid4()
        await s.commit()
        result = await load_prewrite_review_payload(s, p.slug)
        assert not result['is_approved']
        assert 'custom_author_contract' in result['stale_artifact_types']
        assert p.metadata_json['prewrite_review'] == original

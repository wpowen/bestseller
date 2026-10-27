"""Source-custody audit metadata is retained without recursively entering prose."""
from copy import deepcopy
import pytest
from test_chapter_runtime_metadata_projection import source, projections  # noqa: F401

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('key',[
    'outline_source_admission', 'outline_source_admission_history',
    'outline_materialization_history','outline_prefix_readiness','outline_prefix_readiness_history'])
def test_reconciliation_receipts_do_not_change_story_inputs(source,key):  # noqa: F811 - fixture
    before=projections(source)
    chapter=source[1]
    chapter.metadata_json[key]={'untrusted_previous_raw':{'injected':'AUDIT_ONLY_DO_NOT_WRITE'},
        'artifact_id':'history-source','workflow_run_id':'history-workflow'}
    raw=deepcopy(chapter.metadata_json)
    assert projections(source)==before
    assert chapter.metadata_json==raw
    chapter.chapter_goal='Keep the new current authored observation instead.'
    assert projections(source)!=before

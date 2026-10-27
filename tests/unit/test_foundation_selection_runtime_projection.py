from copy import deepcopy
from test_chapter_runtime_metadata_projection import source, projections  # noqa: F401


def test_selected_foundation_receipts_do_not_become_story_source_text(source):  # noqa: F811
    project, chapter, scene, context = source
    baseline = projections(source)
    project.metadata_json['active_foundation_selection'] = {
        'schema': 'foundation-selection.v1', 'artifact_ids': {'book_spec': 'runtime-selection'},
        'historical_projection_compatibility': {'raw_instruction': 'UNTRUSTED_RUNTIME_ONLY'},
        'grade_asserted': False, 'credit_granted': False,
    }
    before = deepcopy(project.metadata_json)
    assert projections(source) == baseline
    assert project.metadata_json == before


def test_unknown_authored_foundation_clause_remains_real_source(source):  # noqa: F811
    project, chapter, scene, context = source
    baseline_bundle, _ = projections(source)
    chapter.metadata_json['active_foundation_authored_clause'] = 'Keep the observation record intact.'
    updated_bundle, _ = projections(source)
    assert updated_bundle != baseline_bundle

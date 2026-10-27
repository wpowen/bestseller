"""Repair the full typed report, never its truncated display summary."""
import json

import pytest

from bestseller.services.planner import (
    OutlineContractValidationError, _outline_repair_directives_from_error,
)
from bestseller.services.narrative_contracts import (
    NarrativeContractViolation, NarrativeContractReport,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_all_structural_paths_survive_display_summary_and_only_fields_are_repaired(language):
    violations = tuple(NarrativeContractViolation(
        code='PLAN_SCENE_UNKNOWN_PARTICIPANT',
        location=f'chapter_outline_batch.chapters[{i}].scenes[0].participants',
        message=f"Participant 'Observer {i}(archivist)' is not in the locked identity manifest.",
        metadata={'chapter_number':i+1,'scene_number':1,'participant':f'Observer {i}(archivist)'},
    ) for i in range(22))
    report= NarrativeContractReport('chapter_plan_contract', violations=violations)
    error=OutlineContractValidationError(report.error_message(),report=report.to_dict())
    assert '+14 more' in str(error)
    directives=_outline_repair_directives_from_error(error,language=language)
    assert len(directives)==2
    payload=json.loads(directives[0].split('\n',2)[2])
    assert payload['violations']==report.to_dict()['violations']
    assert '... +14 more' not in directives[0]
    assert ('Repair only' if language=='en' else '只修复列明') in directives[0]
    assert ('Preserve the events' if language=='en' else '不要因此重写剧情') in directives[0]


def test_distinct_codes_and_unknown_evidence_are_preserved_without_story_recipe():
    entries=[{'code':code,'severity':'critical','location':f'chapters[0].{code}',
        'message':'diagnostic quote: use exact source', 'metadata':{'unknown':{'authored':False}}}
        for code in ('PLAN_SCENE_TIME_MISSING','UNRECOGNIZED_STRUCTURAL_CODE')]
    error=OutlineContractValidationError('display only',report={'passed':False,'violations':entries})
    directives=_outline_repair_directives_from_error(error,language='en')
    assert len(directives)==3
    assert [json.loads(d.split('\n',2)[2])['violations'][0] for d in directives[:-1]]==entries

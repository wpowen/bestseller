"""Derived repair views cannot resurrect audit-only findings from old arrays."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bestseller.services import pipelines
from bestseller.services.write_gate import GateConfig

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('code', ['OPENING_PRESSURE_THIN', 'ENDING_HOOK_MISSING'])
@pytest.mark.parametrize('cache_key', ['production_block_code', 'auto_repair_last_block_codes',
                                     'quality_gate_block_codes', 'quality_bundle_blocking_codes'])
@pytest.mark.parametrize('mode', ['audit_only', 'block'])
def test_real_pipeline_views_follow_action_and_preserve_old_arrays(monkeypatch, code, cache_key, mode):
    config = replace(pipelines.get_quality_gates_config(),
                     l6_gate=GateConfig(mode_by_violation={code: mode}))
    monkeypatch.setattr(pipelines, 'get_quality_gates_config', lambda: config)
    metadata = {'deterministic_audit_latest': {'chapter_number': 2, 'passed': False,
                 'findings': [{'code': code, 'severity': 'high'}]},
                cache_key: code if cache_key == 'production_block_code'
                           else [code, 'CANON_FORBIDDEN_TERM', 'UNRECOGNIZED_SOURCE_BLOCK']}
    original = deepcopy(metadata)
    current = pipelines._current_auto_repair_block_codes(SimpleNamespace(metadata_json=metadata))
    assert (code in current) is (mode == 'block')
    if cache_key != 'production_block_code':
        assert 'CANON_FORBIDDEN_TERM' in current
        assert 'UNRECOGNIZED_SOURCE_BLOCK' in current
    assert (code in pipelines._active_quality_block_codes_from_metadata(metadata)) is (mode == 'block')
    assert (code in pipelines._deterministic_audit_block_codes_from_metadata(metadata)) is (mode == 'block')
    assert metadata == original


def test_absent_report_evidence_never_authorizes_dropping_a_flat_block():
    metadata = {'auto_repair_last_block_codes': ['OPENING_PRESSURE_THIN'],
                'deterministic_audit_latest': {'chapter_number': 2, 'passed': True, 'findings': []}}
    before = deepcopy(metadata)
    assert pipelines._current_auto_repair_block_codes(SimpleNamespace(metadata_json=metadata)) == ('OPENING_PRESSURE_THIN',)
    assert metadata == before

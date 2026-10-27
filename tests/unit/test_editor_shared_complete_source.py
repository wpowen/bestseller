"""Actual pre-send counterexample and unrelated source retain all data at fixed reserve."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from bestseller.services.lossless_rewrite_prompt import prepare_source_complete_prompt
from bestseller.services.primary_review_transport import verify_primary_review_transport, prepare_source_complete_prompt as prepare_shared
from bestseller.services.prompt_compiler import PromptBudgetError

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'reports/framework-recovery-2026-10-21'


def replace_values(value):
    if isinstance(value, dict):
        return {key: replace_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_values(item) for item in value]
    if isinstance(value, str):
        return value.replace('收音机', '测绘仪').replace('磁带', '图纸').replace('父亲', '船长').replace('母亲', '队长')
    return value


@pytest.mark.parametrize('unrelated', [False, True])
def test_actual_native_editor_overflow_uses_shared_lossless_transport(unrelated):
    capture = json.loads((R / 'phase8y7a-editor-native-capacity.json').read_text())
    assert capture['provider_calls'] == capture['database_writes_committed'] == 0
    arguments = deepcopy(capture['captured'])
    if unrelated:
        arguments = replace_values(arguments)
    before = deepcopy(arguments)
    spec = importlib.util.spec_from_file_location('old_editor_overflow_contract', R / 'phase8y7a-before-lossless_rewrite_prompt.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    with pytest.raises(PromptBudgetError):
        old.prepare_source_complete_prompt(**arguments)
    system, user, receipt = prepare_source_complete_prompt(**arguments)
    assert receipt['editor_source_delegate'] == 'complete-source-transport.v1'
    assert receipt['output_reserve_tokens'] == arguments['output_tokens'] == 6912
    assert receipt['source_roundtrip_verified'] and receipt['source_complete']
    assert receipt['budget_increased'] is False
    assert arguments['system_prompt'] in system
    assert user.endswith(arguments['user_prompt'])
    shared_system, shared_user, shared_receipt = prepare_shared(**arguments, tools=None)
    assert (system, user) == (shared_system, shared_user)
    assert verify_primary_review_transport(system, user, source_context=arguments['source_context'], receipt=shared_receipt,
                                          tools=None, output_tokens=6912) is None
    for key, value in shared_receipt.items():
        assert receipt[key] == value
    assert receipt['compiler']['dropped'] == receipt['compiler']['truncated'] == []
    assert arguments == before
    assert prepare_source_complete_prompt(**arguments) == (system, user, receipt)

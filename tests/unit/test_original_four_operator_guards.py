"""Exercise operator guards without importing settings, a gateway or database."""
import ast
import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
OPERATORS = ROOT / 'reports/framework-recovery-2026-10-21'


def extracted(filename, *names):
    tree = ast.parse((OPERATORS / filename).read_bytes())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in functions} == set(names)
    scope = {'hashlib': hashlib}
    exec(compile(ast.Module(body=functions, type_ignores=[]), filename, 'exec', optimize=2), scope)
    return scope


@pytest.mark.parametrize('filename', [
    'review_original_four_whole_semantics_v4.py',
    'review_original_four_whole_semantics_v5_protocol.py',
    'select_original_four_paid_title.py',
    'review_original_four_typed_evidence.py',
    'preview_original_four_selection_capacity.py',
    'review_original_ch4_after_truncated_submission_fix.py',
    'export_original_four_verified.py',
])
def test_dispatch_and_export_guards_survive_python_optimization(filename):
    tree = ast.parse((OPERATORS / filename).read_bytes())
    assert not any(isinstance(n, ast.Assert) for n in ast.walk(tree))
    need = extracted(filename, '_need')['_need']
    with pytest.raises(ValueError, match='exact_guard_failed'):
        need(False, 'exact_guard_failed')
    assert need(True, 'valid') is None


@pytest.fixture
def history_guard():
    return extracted('review_original_four_whole_semantics_v4.py',
        'reject_existing_semantic_history')['reject_existing_semantic_history']


@pytest.mark.parametrize('artifact_status', ['failed', 'validated'])
def test_same_source_artifact_never_grants_rejudge_for_new_implementation(history_guard, artifact_status):
    # The service SELECT intentionally includes every version/status.
    content = {'status': artifact_status, 'request_payload': {'full_text': '原成稿', 'title': '现标题'}}
    with pytest.raises(ValueError, match='existing_same_manuscript_artifact_no_rejudge'):
        history_guard([content], [], full_text='原成稿', title='现标题')


@pytest.mark.parametrize('state', ['running', 'machine_blocked', 'completed', 'failed'])
def test_original_workflow_without_artifact_requires_retained_recovery(history_guard, state):
    metadata = {'previous_status': state, 'implementation': 'different-code-hash',
        'source_manifest': {'assembly_sha256': hashlib.sha256('原成稿'.encode()).hexdigest(),
            'project': {'title': '现标题'}}}
    with pytest.raises(ValueError, match='requires_retained_recovery'):
        history_guard([], [metadata], full_text='原成稿', title='现标题')


@pytest.mark.parametrize('artifact', [None, {}, {'request_payload': {}},
    {'request_payload': {'full_text': '原成稿', 'title': None}}])
def test_unclassifiable_artifact_blocks_new_dispatch(history_guard, artifact):
    with pytest.raises(ValueError, match='history_unclassifiable_no_dispatch'):
        history_guard([artifact], [], full_text='原成稿', title='现标题')


@pytest.mark.parametrize('workflow', [None, {}, {'source_manifest': {}},
    {'source_manifest': {'assembly_sha256': 'sha', 'project': {}}},
    {'source_manifest': {'assembly_sha256': 'sha', 'project': {'title': '现标题'}}},
    {'source_manifest': {'assembly_sha256': 'F' * 64, 'project': {'title': '现标题'}}}])
def test_unclassifiable_workflow_blocks_new_dispatch(history_guard, workflow):
    with pytest.raises(ValueError, match='workflow_history_unclassifiable_no_dispatch'):
        history_guard([], [workflow], full_text='原成稿', title='现标题')


def test_provably_different_retained_source_or_title_is_not_the_current_review(history_guard):
    body = '原成稿'
    artifact = {'request_payload': {'full_text': '实际旧成稿', 'title': '原标题'}}
    workflow = {'source_manifest': {'assembly_sha256': hashlib.sha256(body.encode()).hexdigest(),
        'project': {'title': '原标题'}}}
    assert history_guard([artifact], [workflow], full_text=body, title='现标题') is None


@pytest.mark.parametrize('fault', ['missing', 'foreign_parent', 'changed_error', 'changed_content', 'extra_key'])
def test_protocol_recovery_requires_the_exact_retained_failed_batch(fault):
    name = 'review_original_four_whole_semantics_v5_protocol.py'
    tree = ast.parse((OPERATORS / name).read_bytes())
    constant = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'EXPECTED_PARENT' for t in n.targets))
    expected = ast.literal_eval(constant.value)
    scope = extracted(name, '_need', 'require_original_failed_parent')
    scope['EXPECTED_PARENT'] = expected
    check = scope['require_original_failed_parent']
    assert check(dict(expected)) == expected
    bad = dict(expected)
    if fault == 'missing':
        bad.pop('content_sha256')
    elif fault == 'foreign_parent':
        bad['artifact_id'] = 'a' * 36
    elif fault == 'changed_error':
        bad['error'] = None
    elif fault == 'changed_content':
        bad['content_sha256'] = '0' * 64
    else:
        bad['verified'] = True
    with pytest.raises(ValueError, match='exact_original_failed_v4_parent_required'):
        check(bad)

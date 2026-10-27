"""Inventory proof must exactly match the real hash, including domain paths."""
import hashlib
from pathlib import Path

import pytest

from bestseller.services import pipelines
from bestseller.services.chapter_review_reconciliation import _review_policy_inventory

pytestmark = pytest.mark.unit


def test_real_inventory_digest_equals_actual_current_policy():
    root = Path(pipelines.__file__).resolve().parents[3]
    paths = _review_policy_inventory(root, Path(pipelines.__file__).read_text())
    for name in ("context.py", "project.py", "workflow.py"):
        assert root / "src/bestseller/domain" / name in paths
    assert root / "src/bestseller/services/context.py" in paths
    assert root / "src/bestseller/services/project.py" not in paths
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    assert digest.hexdigest() == pipelines.chapter_review_recovery_source_hash()


def test_domain_context_and_service_context_remain_distinct(tmp_path):
    source = '''
def chapter_review_recovery_source_hash():
    policy_files = {services / name for name in ("context.py", "pipelines.py")}
    policy_files.update(root / "src/bestseller/domain" / name for name in ("context.py", "project.py", "workflow.py"))
    policy_files.add(root / "config/quality_gates.yaml")
    return "not executed"
'''
    paths = _review_policy_inventory(tmp_path, source)
    assert paths == {tmp_path / value for value in (
        "src/bestseller/services/context.py", "src/bestseller/services/pipelines.py",
        "src/bestseller/domain/context.py", "src/bestseller/domain/project.py",
        "src/bestseller/domain/workflow.py", "config/quality_gates.yaml")}


@pytest.mark.parametrize("mutation", ["hidden", "unknown", "traversal", "dynamic"])
def test_unrepresented_inventory_never_silently_drops_paths(tmp_path, mutation):
    source = 'def chapter_review_recovery_source_hash():\n    policy_files = {services / name for name in ("pipelines.py",)}\n'
    source += {
        "hidden": '    if False:\n        policy_files.add(root / "src/bestseller/domain/context.py")\n',
        "unknown": '    policy_files.clear()\n',
        "traversal": '    policy_files.add(root / "../outside.py")\n',
        "dynamic": '    policy_files.update(find_unknown_files())\n',
    }[mutation]
    with pytest.raises(ValueError, match="policy_inventory"):
        _review_policy_inventory(tmp_path, source)

"""Runtime package/source identity and pure JSON roundtrip; zero DB/provider."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_fanqie_short_semantic_review import kwargs, response
from bestseller.services import fanqie_short_semantic_review as review
from bestseller.services import native_publication_title as mapping
from bestseller.services.native_title_operator import title_operator, title_operator_native

pytestmark = pytest.mark.unit


def test_runtime_producer_files_are_byte_exact_and_code_hash_unchanged():
    root = next(p for p in Path(__file__).resolve().parents if (p / "src/bestseller").is_dir())
    base = root / "reports/project-reliability-review-2026-10-24/implementation"
    # Old sources/preimages and old serialized paid fixture are history, not
    # the expected hash of today's producer. Never rewrite them to bless R2.
    protected = json.loads((base / "whole-semantic-runtime-integration/protected-stage-sha256.json").read_text())
    for name, digest in protected.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    old_fixture = base / "whole-semantic-input-factory-stage/synthetic-publication-fixture.json"
    assert hashlib.sha256(old_fixture.read_bytes()).hexdigest() == "38d65edd4e4a16577474a1f50240c62bfd59e2b3d70ad995510127335949b824"
    candidate = base / "title-census-recovery-stage/revision-2/active-operator-candidate"
    reviewed = json.loads((candidate.parent / "sourcehashes.json").read_text())["files"]
    for name in ("title_operator.py", "title_operator_native.py"):
        assert hashlib.sha256((candidate / name).read_bytes()).hexdigest() == reviewed["active-operator-candidate/" + name]
        assert (candidate / name).read_bytes() == (mapping.OPERATOR / name).read_bytes()
    assert mapping.OPERATOR_CODE_HASH == title_operator.operator_code_hash()
    assert title_operator_native.op is title_operator
    assert mapping.op is title_operator


def test_actual_source_hashes_are_in_payload_binding_and_native_metadata():
    p = review.prepare_review(**kwargs())
    actual = review.implementation_source_hashes()
    assert len(actual) == 7
    root = Path(review.__file__).parent
    assert all(actual[name] == hashlib.sha256((root / name).read_bytes()).hexdigest() for name in actual)
    assert p["implementation_source_hashes"] == actual
    assert p["binding"]["implementation_source_hashes_sha256"] == review.fingerprint(actual)
    assert review.native_request_fields(p)["metadata"]["implementation_source_sha256"] == review.fingerprint(actual)
    assert "implementation_source_hashes" not in json.loads(review._user_prompt(p))


@pytest.mark.parametrize("component", list(review.implementation_source_hashes()))
def test_any_implementation_drift_stops_old_request_and_reprepare(monkeypatch, component):
    p = review.prepare_review(**kwargs())
    completion, physical = response(p)
    actual = Path.read_bytes
    target = Path(review.__file__).resolve().parent / component
    def changed(path):
        return actual(path) + b"\n# simulated hot source drift\n" if path == target else actual(path)
    # Simulate disk bytes only, never write shared sources to exercise drift.
    monkeypatch.setattr(Path, "read_bytes", changed)
    for action in (lambda: review.native_request_fields(p), lambda: review.prepare_review(**kwargs())):
        with pytest.raises(review.ReviewInputError, match="implementation_source_changed_restart_required"):
            action()
    result = review.validate_response(p, completion, current_binding=p["binding"], retained_payloads=physical)
    assert result["error"] == "implementation_source_changed_restart_required"
    assert all(row["verdict"] == "uncertain" for row in result["reviews"])


def test_json_roundtrip_exact_physical_request_and_response(tmp_path):
    p = json.loads(review._json(review.prepare_review(**kwargs())))
    completion, physical = response(p)
    path = tmp_path / "retained.json"
    path.write_text(review._json(physical))
    result = review.validate_response(p, json.loads(review._json(completion)), current_binding=deepcopy(p["binding"]), retained_payloads=json.loads(path.read_text()))
    assert result["error"] is None
    assert result["raw_response"] == completion["content"]
    assert result["request_payload"]["implementation_source_hashes"] == p["implementation_source_hashes"]
    assert "passed" not in result


def test_runtime_imports_outside_repo_without_any_report_access(tmp_path):
    src = Path(review.__file__).resolve().parents[2]
    code = '''
import importlib.abc, sys, socket, sqlite3
from unittest.mock import patch
from pathlib import Path
class NoGateway(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'bestseller.services.llm': raise AssertionError('provider gateway import forbidden')
sys.meta_path.insert(0, NoGateway())
def audit(event,args):
 if event == 'open' and isinstance(args[0],str) and '/reports/' in args[0]: raise AssertionError('runtime report dependency')
sys.addaudithook(audit)
with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')), patch.object(sqlite3,'connect',side_effect=AssertionError('DB forbidden')):
 from bestseller.services import fanqie_short_semantic_review as s
 from bestseller.services import native_publication_title as n
 from bestseller.services.native_title_operator import title_operator, title_operator_native
 assert title_operator_native.op is title_operator is n.op
 assert n.OPERATOR == Path(s.__file__).parent/'native_title_operator'
 assert n.OPERATOR_CODE_HASH == title_operator.operator_code_hash()
 assert len(s.implementation_source_hashes()) == 7
 assert 'title_operator' not in sys.modules
 print('runtime src-only package import OK; no reports/DB/provider gateway')
'''
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "src-only package import OK" in result.stdout

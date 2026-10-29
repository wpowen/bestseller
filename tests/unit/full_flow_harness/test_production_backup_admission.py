"""Pure backup admission only; never import/run the driver, Docker, DB or git."""

import ast
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from support.cp1_rehearsal import HarnessInvariantError, require

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "reports/full-flow-2026-10-28/USER-AUTHORIZATION-20261029-1710.md"
AUTH_SHA = "fc24dd5f143bb14c0c458b70f04592338e9c764947c1ce422c47b5dfc330e969"
pytestmark = pytest.mark.unit


@pytest.fixture
def admission():
    path = ROOT / "scripts/full_flow/production_backup.py"
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"equal", "admit_backup_scope"}]
    assert len(functions) == 2
    pin = next(node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "BACKUP_AUTHORIZATION_SHA"
                       for t in node.targets))
    assert ast.literal_eval(pin) == AUTH_SHA
    namespace = {"require": require, "hashlib": hashlib,
                 "BACKUP_AUTHORIZATION": AUTH, "BACKUP_AUTHORIZATION_SHA": AUTH_SHA}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["admit_backup_scope"]


@pytest.fixture
def grant():
    decision = AUTH.read_bytes()
    assert hashlib.sha256(decision).hexdigest() == AUTH_SHA
    return {"scope": "production_backup_before_release_only",
            "authorization": {"path": str(AUTH), "sha256": AUTH_SHA}}, decision


def test_explicit_backup_grant_does_not_require_or_mint_release_pass(admission, grant):
    manifest, decision = grant
    original = deepcopy(manifest)
    result = admission(manifest, decision, pre_release=True, after_main_gates=False)
    assert manifest == original
    assert result == {
        "mode": "pre_release_backup", "scope": "production_backup_before_release_only",
        "authorization_sha256": AUTH_SHA, "release_gates_verified": False,
        "l1": "not_evaluated", "deployment_authority": False,
    }


@pytest.mark.parametrize("fault,field", [
    ("scope", "authorization.scope"),
    ("path", "authorization.path"),
    ("sha", "authorization.sha256"),
    ("changed_bytes", "authorization.original_bytes_sha256"),
    ("neither_mode", "invocation.one_authorization_mode"),
    ("both_modes", "invocation.one_authorization_mode"),
])
def test_pre_release_admission_rejects_wrong_binding(admission, grant, fault, field):
    manifest, decision = grant
    pre_release, after_main_gates = True, False
    if fault == "scope":
        manifest["scope"] = "production_deployment"
    elif fault == "path":
        manifest["authorization"]["path"] = "/private/tmp/unrelated-authorization.md"
    elif fault == "sha":
        manifest["authorization"]["sha256"] = "0" * 64
    elif fault == "changed_bytes":
        decision += b"changed"
    elif fault == "neither_mode":
        pre_release = False
    elif fault == "both_modes":
        after_main_gates = True
    with pytest.raises(HarnessInvariantError) as caught:
        admission(manifest, decision, pre_release=pre_release, after_main_gates=after_main_gates)
    assert caught.value.differences[0]["field"] == field


def test_legacy_admission_still_requires_main_prevalidation(admission, grant):
    with pytest.raises(HarnessInvariantError, match=r"gate\.main_prevalidated"):
        admission(grant[0], None, pre_release=False, after_main_gates=True)
    assert admission({"main_prevalidated": True}, None,
                     pre_release=False, after_main_gates=True) == {
        "mode": "after_main_gates", "deployment_authority": False,
    }


@pytest.fixture
def native_schema_comparison():
    path = ROOT / "scripts/full_flow/production_backup.py"
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"equal", "verify_native_schema_addendum"}]
    assert len(functions) == 2
    namespace = {"require": require, "hashlib": hashlib}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["verify_native_schema_addendum"]


def test_native_schema_exact_bytes_do_not_replace_original_result(native_schema_comparison):
    schema = b"ALTER TABLE public.example OWNER TO bestseller;\n"
    assert native_schema_comparison(schema, schema) == {
        "native_schema_bytes_equal": True,
        "canonical_schema_sha256": hashlib.sha256(schema).hexdigest(),
        "original_result_replaced": False, "deployment_authority": False,
    }


def test_native_schema_comparison_never_normalizes_array_casts(native_schema_comparison):
    original = b"CHECK (value = ANY ((ARRAY['queued'::character varying])::text[]));\n"
    reparsed = b"CHECK (value = ANY (ARRAY[('queued'::character varying)::text]));\n"
    with pytest.raises(HarnessInvariantError) as caught:
        native_schema_comparison(original, reparsed)
    difference = caught.value.differences[0]
    assert difference["field"] == "schema.native_canonical_bytes"
    assert difference["expected"]["type"] == difference["actual"]["type"] == "bytes"
    assert difference["expected"]["length"] == len(original)
    assert difference["actual"]["length"] == len(reparsed)
    assert difference["expected"]["sha256"] != difference["actual"]["sha256"]


def test_native_schema_comparison_preserves_owner_acl_and_literals(native_schema_comparison):
    schema = (b"CHECK (state = 'queued');\n"
              b"ALTER TABLE public.example OWNER TO bestseller;\n"
              b"GRANT SELECT ON public.example TO bestseller;\n")
    for changed in (schema.replace(b"queued", b"pending"),
                    schema.replace(b"OWNER TO bestseller", b"OWNER TO postgres"),
                    schema.replace(b"GRANT SELECT ON public.example TO bestseller;\n", b"")):
        with pytest.raises(HarnessInvariantError) as caught:
            native_schema_comparison(schema, changed)
        assert caught.value.differences[0]["field"] == "schema.native_canonical_bytes"

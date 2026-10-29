"""File-admission RED controls only; no driver entrypoint, app, DB or SDK runs.

The small private fixtures prove input integrity, never reply coverage, native
grades or first-chapter adoption. Main alone executes this test module.
"""

import ast
import hashlib
import inspect
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

import pytest

DRIVER = Path(__file__).resolve().parents[3] / "scripts/full_flow/run_l1.py"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write(root, name, value):
    path = root / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def _files(root):
    return {p.name: _sha(p.read_bytes()) for p in root.iterdir() if p.is_file()}


def _seal(root):
    _write(
        root,
        "manifest.json",
        {
            "schema": "private-finite-prefix-manifest.v3",
            "state": "FINITE_PREFIX_PREPARED",
            "files": {
                p.name: {"bytes": len(p.read_bytes()), "sha256": _sha(p.read_bytes())}
                for p in root.iterdir()
                if p.is_file() and p.name != "manifest.json"
            },
        },
    )


def _inputs(root):
    # Deliberately not an executable authoring recipe or an HTTP submission.
    _write(
        root,
        "script.json",
        {
            "schema": "normal-quickstart-private.v1",
            "story": {"fixture": "文件完整性"},
            "replies": [],
        },
    )
    _write(root, "quickstart.request.json", {"fixture": "input-integrity-only"})
    _write(root, "chapter-input.json", {"fixture": "finite authored material"})


def _prefix(root):
    _inputs(root)
    script = json.loads((root / "script.json").read_bytes())
    _write(
        root,
        "readiness.json",
        {
            "schema": "finite-firstchapter-prefix-readiness.v3",
            "state": "FINITE_PREFIX_PREPARED",
            "config_sha256": _sha(_canonical(script).encode()),
            "script_sha256": _sha((root / "script.json").read_bytes()),
            "full_firstchapter_ready": False,
            "native_authority_or_grades_created": False,
            "startup_requires_prior_native_dispatch": False,
            "required_explicit_rejections": [{"code": "SCRIPT_TEMPLATE_UNCOVERED"}],
            "evidence": {"normal_sdk_execution": "Not observed; not a startup condition."},
        },
    )
    _seal(root)


def _compiled(root):
    _inputs(root)
    _write(
        root,
        "authoring-status.json",
        {
            "state": "STRUCTURE_READY_FOR_MAIN_REVIEW",
            "blockers": [],
            "config_sha256": _sha(
                _canonical(json.loads((root / "script.json").read_bytes())).encode()
            ),
            "artifact_sha256": _files(root),
        },
    )


@pytest.fixture
def private_root():
    # Remain outside the checkout even if main's pytest basetemp is in a repo.
    with tempfile.TemporaryDirectory(prefix="l1-finite-prefix-") as directory:
        root = Path(directory).resolve()
        root.chmod(0o700)
        yield root


@pytest.fixture
def admit():
    tree = ast.parse(DRIVER.read_bytes(), filename=str(DRIVER))
    namespace = {
        "Path": Path,
        "PurePosixPath": PurePosixPath,
        "json": json,
        "hashlib": hashlib,
        "stat": stat,
        "re": re,
    }
    # Load definitions only. No top-level imports, main(), host() or inside().
    definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.isupper():
                try:
                    namespace[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(DRIVER), "exec"), namespace)
    return namespace["private_tree"]


def _denied(call, code=None):
    with pytest.raises(RuntimeError) as caught:
        call()
    error = caught.value
    assert getattr(error, "code", "").startswith("L1_"), type(error).__name__
    if code is not None:
        assert error.code == code
    assert error.differences, "admission denial must retain safe typed operands"
    for difference in error.differences:
        assert difference["field"]
        for side in ("expected", "actual"):
            assert set(difference[side]) == {"type", "length", "sha256"}
            assert re.fullmatch(r"[0-9a-f]{64}", difference[side]["sha256"])


def test_finite_prefix_api_requires_explicit_keyword_bool_default_false(admit):
    parameter = inspect.signature(admit).parameters.get("allow_finite_prefix")
    assert parameter is not None, "missing private_tree(..., *, allow_finite_prefix=False)"
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False


@pytest.mark.parametrize("explicit_false", [False, True])
def test_finite_prefix_is_not_admitted_by_default(admit, private_root, explicit_false):
    _prefix(private_root)
    kwargs = {"allow_finite_prefix": False} if explicit_false else {}
    _denied(lambda: admit(private_root, **kwargs))


@pytest.mark.parametrize("flag", [1, "true", None])
def test_finite_prefix_flag_cannot_use_truthy_or_nonbool_values(admit, private_root, flag):
    _prefix(private_root)
    _denied(lambda: admit(private_root, allow_finite_prefix=flag))


def test_explicit_v3_prefix_needs_no_compiler_or_prior_sdk_evidence(admit, private_root):
    _prefix(private_root)
    before = {p.name: p.read_bytes() for p in private_root.iterdir()}
    readiness = json.loads(before["readiness.json"])
    assert readiness["config_sha256"] != readiness["script_sha256"]
    assert not (private_root / "authoring-status.json").exists()
    assert admit(private_root, allow_finite_prefix=True) == _files(private_root)
    assert {p.name: p.read_bytes() for p in private_root.iterdir()} == before
    assert readiness["full_firstchapter_ready"] is False


@pytest.mark.parametrize("flag", ["omitted", False, True])
def test_original_complete_compiler_path_is_preserved(admit, private_root, flag):
    _compiled(private_root)
    kwargs = {} if flag == "omitted" else {"allow_finite_prefix": flag}
    assert admit(private_root, **kwargs) == _files(private_root)


def test_original_compiler_unknown_state_still_denies(admit, private_root):
    _compiled(private_root)
    path = private_root / "authoring-status.json"
    value = json.loads(path.read_bytes())
    value["state"] = "UNKNOWN"
    _write(private_root, path.name, value)
    _denied(lambda: admit(private_root), "L1_AUTHORING_STRUCTURALLY_BLOCKED")


@pytest.mark.parametrize(
    "file,field,value",
    [
        ("readiness.json", "schema", "finite-firstchapter-prefix-readiness.v2"),
        ("readiness.json", "state", "UNKNOWN"),
        ("readiness.json", "config_sha256", "0" * 64),
        ("readiness.json", "script_sha256", "0" * 64),
        ("manifest.json", "schema", "private-finite-prefix-manifest.v2"),
        ("manifest.json", "state", "UNKNOWN"),
        ("manifest.json", "files", {}),
    ],
)
def test_prefix_claims_are_validated_even_after_outer_hash_is_resealed(
    admit,
    private_root,
    file,
    field,
    value,
):
    _prefix(private_root)
    changed = json.loads((private_root / file).read_bytes())
    changed[field] = value
    _write(private_root, file, changed)
    if file == "readiness.json":
        _seal(private_root)
    _denied(lambda: admit(private_root, allow_finite_prefix=True))


@pytest.mark.parametrize(
    "name",
    [
        "script.json",
        "quickstart.request.json",
        "readiness.json",
        "manifest.json",
    ],
)
def test_prefix_missing_required_file_denies(admit, private_root, name):
    _prefix(private_root)
    (private_root / name).unlink()
    _denied(lambda: admit(private_root, allow_finite_prefix=True))


@pytest.mark.parametrize(
    "name",
    [
        "script.json",
        "quickstart.request.json",
        "readiness.json",
        "chapter-input.json",
    ],
)
def test_prefix_any_bound_file_byte_change_denies(admit, private_root, name):
    _prefix(private_root)
    path = private_root / name
    path.write_bytes(path.read_bytes() + b" ")  # valid JSON; physical SHA must still differ
    _denied(lambda: admit(private_root, allow_finite_prefix=True))


@pytest.mark.parametrize("mutation", ["wrong_size", "wrong_sha", "unlisted_file"])
def test_manifest_covers_file_bytes_and_complete_payload_inventory(admit, private_root, mutation):
    _prefix(private_root)
    manifest = json.loads((private_root / "manifest.json").read_bytes())
    if mutation == "wrong_size":
        manifest["files"]["quickstart.request.json"]["bytes"] += 1
    elif mutation == "wrong_sha":
        manifest["files"]["quickstart.request.json"]["sha256"] = "0" * 64
    else:
        _write(private_root, "unlisted.json", {"fixture": "not bound"})
    _write(private_root, "manifest.json", manifest)
    _denied(lambda: admit(private_root, allow_finite_prefix=True))


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("root_mode", "L1_AUTHORING_MODE"),
        ("file_mode", "L1_AUTHORING_MODE"),
        ("symlink", "L1_AUTHORING_SPECIAL_FILE"),
        ("checkout", "L1_AUTHORING_INSIDE_CHECKOUT"),
        ("secret", "L1_AUTHORING_SECRET_CONFIG"),
        ("used", "L1_USED_AUTHORING_ROOT"),
    ],
)
def test_prefix_opt_in_preserves_existing_private_and_fresh_guards(
    admit,
    private_root,
    mutation,
    code,
):
    _prefix(private_root)
    if mutation == "root_mode":
        private_root.chmod(0o755)
    elif mutation == "file_mode":
        (private_root / "script.json").chmod(0o644)
    elif mutation == "symlink":
        (private_root / "linked.json").symlink_to(private_root / "script.json")
    elif mutation == "checkout":
        (private_root / ".git").mkdir(mode=0o700)
    elif mutation == "secret":
        _write(private_root, "unused.env", {})
    else:
        _write(private_root, "physical-ordinals.json", {})
    _denied(lambda: admit(private_root, allow_finite_prefix=True), code)


def test_cli_opt_in_reaches_original_and_copied_authoring_checks():
    tree = ast.parse(DRIVER.read_bytes())
    declarations = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_argument"
        and any(isinstance(a, ast.Constant) and a.value == "--allow-finite-prefix" for a in n.args)
    ]
    assert len(declarations) == 1, "explicit --allow-finite-prefix CLI flag is missing"
    options = {k.arg: k.value for k in declarations[0].keywords}
    assert ast.literal_eval(options["action"]) == "store_true"
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "private_tree"
    ]
    assert len(calls) >= 2, "original and copied authoring both need verification"
    expected = ast.dump(ast.parse("args.allow_finite_prefix", mode="eval").body)
    for call in calls:
        flag = next((k.value for k in call.keywords if k.arg == "allow_finite_prefix"), None)
        assert flag is not None and ast.dump(flag) == expected, (
            f"unbound prefix flag at {call.lineno}"
        )


def test_finite_prefix_cannot_replace_native_first_adoption_success_condition():
    # Static preservation only, not evidence that a native adoption occurred.
    tree = ast.parse(DRIVER.read_bytes())
    inside = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "inside"
    )
    expected = ast.parse(
        'if state["adoption"] is not None:\n'
        "    result.update(l1_first_chapter_adopted=True, native_state=state)\n"
        "    break\n"
    ).body[0]
    assert any(ast.dump(n) == ast.dump(expected) for n in ast.walk(inside) if isinstance(n, ast.If))
    native = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "native_state"
    )
    called = {
        n.func.id
        for n in ast.walk(native)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {"select_adopted_chapter_source", "validate_adopted_knowledge_source"} <= called
    assert not any(
        isinstance(n, ast.Attribute) and n.attr == "allow_finite_prefix" for n in ast.walk(native)
    )

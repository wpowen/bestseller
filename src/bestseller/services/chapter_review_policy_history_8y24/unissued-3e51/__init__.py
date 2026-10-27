"""Fixed native policy epochs; every selector verifies all runtime bytes.

Only original 8Y24, prior R09, A296 and the reviewed Beat-resume epoch exist.
Historical receipts are data to replay, never approval or runtime authority.
"""

import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OLD_POLICY = "1fd308264eae63f0f7ab761da701a50e971014d9e693c3c2bcec98181b111b3c"
PRIOR_POLICY = "db1b38eba0e68ad0bc0b431329865aa55ec7ddd5cb0c37c426031a745a1fd634"
ORIGINAL_SHA256 = "318cde073c31d22cbc46c0782f014a9afe1c864abe2ec9b3d714792079a1525b"
# current.json is the immutable R09 manifest, retained byte-for-byte.
CURRENT_SHA256 = "8dd6490f0dcf9984b98484ee73b582ad4e7e80a2dca9b79dff1ba91b1b6a685c"
A296_POLICY = "a2962a25cf438282381bc1e432a46496a6e2315148895f477f1f2fbc51b6623a"
A296_SHA256 = "ad84ee5bf4a0fcbee4a1d55ea39ef7d5039dc32718c2cd1ccf5c794bc55f3371"
# Final reviewed explicit source freezes, not runtime-discovered inventory.
# Never populate these from runtime bytes, environment, or an operator probe.
FINAL_SHA256 = "78c202ca2204c2c12477558b2f590c294e2ee8f79d2e3de62dc19d1107e7e7c6"
SCHEMA = "native-review-policy-compatibility.8y24-r04.v1"
_HELPER = "src/bestseller/services/fanqie_short_plan_resume.py"
_RECONCILIATION = "src/bestseller/services/chapter_review_reconciliation.py"
_SHORT_PIPELINE = "src/bestseller/services/fanqie_short_pipeline.py"
_BEAT = "src/bestseller/services/fanqie_beat_recovery.py"
REVIEWED_BEAT_RESUME_SHA256 = {
    _HELPER: "cc66105fc0cb43a7185464d25630981833c18ade5e6247b849bd61ad58491cf5",
    _SHORT_PIPELINE: "f22c3cac22e7070c4771a789f64e169e9486575ca0401cad88ae660aaa46da82",
    _BEAT: "e3982c8d3152ef51c3903492a99bad54a32f6299ed47b8b8a6c835fc56e83dc4",
}
_A296_SNAPSHOTS = frozenset((_HELPER, _SHORT_PIPELINE, _BEAT, _RECONCILIATION))


def _need(value, code):
    if not value:
        raise ValueError("native_review_policy_" + code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _document(name, expected):
    raw = (HERE / name).read_bytes()
    _need(_sha(raw) == expected, "manifest_not_frozen")
    return json.loads(raw)


def _aggregate(files):
    digest = hashlib.sha256()
    for name, value in sorted(files.items()):
        digest.update(name.encode() + b"\0")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def original_bytes(relative):
    manifest = _document("original.json", ORIGINAL_SHA256)
    files = {**manifest["inventory"], **manifest["additional_sources"]}
    _need(relative in files, "unknown_original_source")
    raw = (HERE / "bytes" / (relative + ".source")).read_bytes()
    _need(_sha(raw) == files[relative], "original_source_changed")
    return raw


def prior_bytes(relative):
    """Reconstruct exact db1b bytes from original bytes plus its fixed delta."""
    original = _document("original.json", ORIGINAL_SHA256)
    prior = _document("current.json", CURRENT_SHA256)
    _need(
        (HERE / "prior-current.json").read_bytes() == (HERE / "current.json").read_bytes(),
        "prior_manifest_changed",
    )
    files = {**prior["inventory"], **prior["additional_sources"]}
    old = {**original["inventory"], **original["additional_sources"]}
    _need(relative in files and relative in old, "unknown_prior_source")
    raw = (
        original_bytes(relative)
        if files[relative] == old[relative]
        else (HERE / "prior-bytes" / (relative + ".source")).read_bytes()
    )
    _need(_sha(raw) == files[relative], "prior_source_changed")
    return raw


def a296_bytes(relative):
    """Recover A296 only from pinned historical bytes, never live files."""
    prior = _document("current.json", CURRENT_SHA256)
    current = _document("a296-final.json", A296_SHA256)
    files = {**current["inventory"], **current["additional_sources"]}
    previous = {**prior["inventory"], **prior["additional_sources"]}
    _need(relative in files and relative in previous, "unknown_a296_source")
    if relative in _A296_SNAPSHOTS:
        raw = (HERE / "a296-bytes" / (relative + ".source")).read_bytes()
    else:
        _need(files[relative] == previous[relative], "unarchived_a296_delta")
        raw = prior_bytes(relative)
    _need(_sha(raw) == files[relative], "a296_source_changed")
    return raw


def _ast(node):
    return ast.dump(node, include_attributes=False)


def _exact_delta_boundary(previous, current, relative):
    """Extra structural fence, in addition to exact full-file hash pins."""
    before, after = ast.parse(previous), ast.parse(current)
    if relative == _RECONCILIATION:
        allowed = {
            "validate_native_policy_alias",
            "native_waiting_record_binding",
            "_prepare_native_chapter_resume",
        }
        # These are the only old definitions changed. Newly added internals
        # are still exact-byte pinned by final.json.
        old_defs = {
            n.name: n for n in before.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        new_defs = {
            n.name: n for n in after.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        _need(set(old_defs) <= set(new_defs), "reconciliation_definition_removed")
        _need(
            {n for n in old_defs if _ast(old_defs[n]) != _ast(new_defs[n])} == allowed,
            "reconciliation_delta_boundary",
        )
        old_other = [
            n for n in before.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        new_other = [
            n for n in after.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        _need(
            [_ast(n) for n in old_other] == [_ast(n) for n in new_other],
            "reconciliation_module_boundary",
        )
    elif relative == _HELPER:
        name = "read_existing_complete_fanqie_plan"
        old_fn = next(
            n for n in before.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        )
        new_fn = next(
            n for n in after.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        )

        def strict_tail(fn):
            block = next(n for n in fn.body if isinstance(n, ast.With))
            # Every original materialized proof statement from target_range
            # through the function return must stay exactly the same.
            index = next(
                i
                for i, n in enumerate(block.body)
                if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and any(
                    isinstance(a, ast.Constant) and a.value == "target_range" for a in n.value.args
                )
            )
            return [_ast(n) for n in block.body[index:]]

        _need(strict_tail(old_fn) == strict_tail(new_fn), "materialized_proof_changed")

        # Every top-level class/function except this classifier stays exact.
        def unaffected(module):
            return {
                n.name: _ast(n)
                for n in module.body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name != name
            }

        _need(unaffected(before) == unaffected(after), "planning_other_definition_changed")
    else:
        _need(False, "unknown_epoch_delta")


def _proof(previous, current, previous_manifest_sha, current_manifest_sha):
    old = {**previous["inventory"], **previous["additional_sources"]}
    new = {**current["inventory"], **current["additional_sources"]}
    return {
        "schema": SCHEMA,
        "previous_policy_hash": previous["policy_hash"],
        "current_policy_hash": current["policy_hash"],
        "original_manifest_sha256": previous_manifest_sha,
        "current_manifest_sha256": current_manifest_sha,
        "reviewed_delta_files": sorted(n for n in old if old[n] != new[n]),
        "provider_calls": 0,
        "new_credit": False,
        "dispatch_authorized": False,
        "grade_recomputed": False,
    }


def _beat_resume_boundary(previous, current, relative):
    """Structural fences supplement, never replace, reviewed whole-file pins."""
    if relative == _HELPER:
        before, after = ast.parse(previous), ast.parse(current)
        name = "read_existing_complete_fanqie_plan"
        function = next(n for n in after.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == name)
        block = next(n for n in function.body if isinstance(n, ast.With))
        index = next(i for i, n in enumerate(block.body)
                     if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                     and any(isinstance(a, ast.Constant) and a.value == "target_range"
                             for a in n.value.args))
        branch = block.body[index - 1]
        _need(isinstance(branch, ast.If) and any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "read_selected_foundation_stage"
            for n in ast.walk(branch)), "foundation_classification_boundary")
        del block.body[index - 1]
        _need(_ast(before) == _ast(after), "foundation_materialized_or_other_code_changed")
    elif relative == _SHORT_PIPELINE:
        before, after = ast.parse(previous), ast.parse(current)
        name = "run_fanqie_short_pipeline"

        def parts(module):
            function = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                            and n.name == name)
            read_index = next(i for i, n in enumerate(function.body)
                              if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
                              and isinstance(n.value.value, ast.Call)
                              and isinstance(n.value.value.func, ast.Name)
                              and n.value.value.func.id
                              == "require_or_read_existing_complete_fanqie_plan")
            stage_index = next(i for i, n in enumerate(function.body)
                               if isinstance(n, ast.If) and any(
                                   isinstance(v, ast.Assign) and isinstance(v.value, ast.Call)
                                   and isinstance(v.value.func, ast.Name)
                                   and v.value.func.id == "build_fanqie_segment_outline_batch"
                                   for v in n.body))
            stage = function.body[stage_index]
            projection_index = next(i for i, n in enumerate(stage.body)
                                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                                    and isinstance(n.value.func, ast.Name)
                                    and n.value.func.id == "build_fanqie_segment_outline_batch")
            beat = next(n for n in stage.body if isinstance(n, ast.Assign)
                        and isinstance(n.value, ast.Await)
                        and isinstance(n.value.value, ast.Call)
                        and isinstance(n.value.value.func, ast.Name)
                        and n.value.value.func.id == "generate_fanqie_beat_sheet")
            protected = [*function.body[:read_index + 1], *stage.body[projection_index:],
                         *stage.orelse, *function.body[stage_index + 1:]]
            signature = (_ast(function.args), [_ast(n) for n in function.decorator_list],
                         _ast(function.returns) if function.returns else None,
                         function.type_comment, _ast(stage.test), _ast(beat))
            function.body = []
            return [_ast(n) for n in protected], signature, _ast(module)

        _need(parts(before) == parts(after), "short_pipeline_nonplanning_code_changed")
    else:
        # Beat producer and reconciliation are approved by complete-file pins;
        # their internals are not normalized out of runtime verification.
        _need(relative in (_BEAT, _RECONCILIATION), "unknown_beat_resume_delta")


def _verified_four_epochs():
    _need(FINAL_SHA256 is not None
          and all(REVIEWED_BEAT_RESUME_SHA256.values()), "final_epoch_not_reviewed")
    original = _document("original.json", ORIGINAL_SHA256)
    prior = _document("current.json", CURRENT_SHA256)
    a296 = _document("a296-final.json", A296_SHA256)
    current = _document("final.json", FINAL_SHA256)
    _need(
        original["policy_hash"] == OLD_POLICY and prior["policy_hash"] == PRIOR_POLICY
        and a296["policy_hash"] == A296_POLICY
        and current["policy_hash"] not in (OLD_POLICY, PRIOR_POLICY, A296_POLICY),
        "historical_policy",
    )
    for manifest in (original, prior, a296, current):
        _need(
            len(manifest["inventory"]) == 263
            and _aggregate(manifest["inventory"]) == manifest["policy_hash"],
            "policy_inventory",
        )
        _need(
            set(manifest["inventory"]) == set(original["inventory"])
            and set(manifest["additional_sources"]) == set(original["additional_sources"]),
            "inventory_changed",
        )
    old_files = {**original["inventory"], **original["additional_sources"]}
    prior_files = {**prior["inventory"], **prior["additional_sources"]}
    a296_files = {**a296["inventory"], **a296["additional_sources"]}
    new_files = {**current["inventory"], **current["additional_sources"]}
    _need(
        sorted(n for n in old_files if old_files[n] != prior_files[n])
        == prior["reviewed_delta_files"],
        "prior_delta_set_changed",
    )
    _need(
        sorted(n for n in old_files if old_files[n] != a296_files[n])
        == a296["reviewed_delta_files"],
        "a296_delta_set_changed",
    )
    _need(
        sorted(n for n in old_files if old_files[n] != new_files[n])
        == current["reviewed_delta_files"],
        "delta_set_changed",
    )
    _need(
        sorted(n for n in prior_files if prior_files[n] != a296_files[n])
        == sorted((_HELPER, _RECONCILIATION)),
        "a296_epoch_delta_set_changed",
    )
    _need(
        sorted(n for n in a296_files if a296_files[n] != new_files[n])
        == sorted(_A296_SNAPSHOTS)
        == current["epoch_delta_files"]
        and current["prior_policy_hash"] == A296_POLICY,
        "epoch_delta_set_changed",
    )
    _need(
        all(new_files[n] == value for n, value in REVIEWED_BEAT_RESUME_SHA256.items()),
        "unreviewed_beat_resume_source",
    )
    for relative in sorted(old_files):
        original_bytes(relative)
        previous = prior_bytes(relative)
        intermediate = a296_bytes(relative)
        if prior_files[relative] != a296_files[relative]:
            _exact_delta_boundary(previous, intermediate, relative)
        actual = (ROOT / relative).read_bytes()
        _need(_sha(actual) == new_files[relative], "unlisted_runtime_delta:" + relative)
        if a296_files[relative] != new_files[relative]:
            _beat_resume_boundary(intermediate, actual, relative)
    return original, prior, a296, current


def _verified_epochs():
    """Preserve the existing three-result internal API: original, prior, latest."""
    original, prior, _, current = _verified_four_epochs()
    return original, prior, current


def verified_transition_from(previous_policy):
    """Internal selector: only a native row's known epoch may be supplied."""
    original, prior, a296, current = _verified_four_epochs()
    sources = {OLD_POLICY: (original, ORIGINAL_SHA256),
               PRIOR_POLICY: (prior, CURRENT_SHA256), A296_POLICY: (a296, A296_SHA256)}
    _need(previous_policy in sources, "unsupported_previous_policy")
    source, digest = sources[previous_policy]
    return _proof(source, current, digest, FINAL_SHA256)


def verified_alias_transition(claimed):
    """Replay an exact issued transition after verifying actual CURRENT bytes."""
    original, prior, a296, current = _verified_four_epochs()
    recognized = (
        _proof(original, prior, ORIGINAL_SHA256, CURRENT_SHA256),
        _proof(original, a296, ORIGINAL_SHA256, A296_SHA256),
        _proof(prior, a296, CURRENT_SHA256, A296_SHA256),
        _proof(original, current, ORIGINAL_SHA256, FINAL_SHA256),
        _proof(prior, current, CURRENT_SHA256, FINAL_SHA256),
        _proof(a296, current, A296_SHA256, FINAL_SHA256),
    )
    _need(type(claimed) is dict and claimed in recognized, "unknown_alias_transition")
    return claimed


def verified_transition():
    """No operator source/hash/probe input; default original-to-current epoch."""
    return verified_transition_from(OLD_POLICY)

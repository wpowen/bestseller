"""Execute bounded fragments of CURRENT callers, never archived issuer code.

These probes cannot return a review, a quality verdict, or adoption permission.
The nested matcher probe tests only its local identity fence. Call-boundary
sentinels always raise; none stand in for a successful quality verifier.
"""

import ast
from copy import deepcopy
import inspect
import textwrap

from issuer_view_test_support import filesystem, rows

from bestseller.services import pipelines


class ReachedCommonPreflight(BaseException):
    """Stop before any result is returned to a consumer."""


def current_function(name):
    function = getattr(pipelines, name)
    return ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]


def retained_matcher(case, proof):
    parent = current_function("run_chapter_pipeline")
    nodes = [
        node
        for node in ast.walk(parent)
        if isinstance(node, ast.FunctionDef) and node.name == "retained_review_matches"
    ]
    assert len(nodes) == 1
    namespace = {
        **vars(pipelines),
        "retained_review_reuse": proof,
        "project": case.project,
        "chapter": case.chapter,
        "critic_cycle_id": case.ids["cycle_id"],
    }
    # Copy the complete nested function, with no AST rewriting or guard removal.
    module = ast.Module(body=[deepcopy(nodes[0])], type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(module), "<current-retained-matcher>", "exec"), namespace
    )
    return namespace["retained_review_matches"]


def selection_call_probe(namespace):
    parent = current_function("_promote_best_scoring_chapter_draft_on_stall")
    calls = [
        node
        for node in ast.walk(parent)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "eligible_reviewed_candidates"
    ]
    assert len(calls) == 1
    probe = ast.parse("async def probe():\n    pass\n")
    probe.body[0].body = [ast.Expr(value=deepcopy(calls[0]))]
    environment = {**vars(pipelines), **namespace}
    exec(compile(ast.fix_missing_locations(probe), "<current-candidate-call>", "exec"), environment)
    return environment["probe"]


async def snapshot(case):
    return await rows(case.session, case.models), filesystem(case.artifact_root)


def comment_upgrade(case):
    path = case.root / "src/bestseller/services/reviews.py"
    path.write_bytes(path.read_bytes() + b"\n# bounded caller comment-equivalence input\n")


def assert_denials(receipt):
    for key in (
        "grade_granted",
        "new_credit_allowed",
        "dispatch_authorized",
        "adoption_authorized",
        "unknown_reconciled",
        "issued_report_verified",
        "full_quality_proof_verified",
        "current_applicability_checked",
    ):
        assert receipt[key] is False, key

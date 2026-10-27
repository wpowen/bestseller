"""Only the reviewed promotion-control AST transition can alias a paid review."""
import ast
import hashlib
from pathlib import Path

import pytest

from bestseller.services import chapter_review_reconciliation as recovery

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def sources():
    current = (ROOT / "src/bestseller/services/draft_promotion.py").read_text()
    tree = ast.parse(current)
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "promote_best_draft")
    old_function = (ROOT / "tests/fixtures/promotion_control_before_source_rejection.txt").read_text()
    lines = current.splitlines(keepends=True)
    previous = "".join(lines[:function.lineno-1]) + old_function + "".join(lines[function.end_lineno:])
    return previous.encode(), current.encode()


def test_reviewed_promotion_transition_preserves_every_other_ast():
    old, current = sources()
    receipt = recovery._promotion_admission_policy_compatibility(old, current)
    assert receipt["transition"] == "source-rejected-incumbent-admission.v1"
    assert receipt["all_other_promotion_code_ast_unchanged"] is True
    assert receipt["review_evaluator_changed"] is False
    assert receipt["grade_reused_across_input_changes"] is False
    assert receipt["previous_function_ast_sha256"] != receipt["current_function_ast_sha256"]


@pytest.mark.parametrize("mutation", ["threshold", "ranking", "outside", "wrong_old", "missing", "body_guard"])
def test_unreviewed_promotion_mutations_fail_closed(mutation):
    old, current = sources()
    if mutation == "threshold":
        current = current.replace(b"min_overall: float = 0.85", b"min_overall: float = 0.01")
    elif mutation == "ranking":
        current = current.replace(b"<= promotion_rank_key(incumbent_ranked.candidate)",
                                  b">= promotion_rank_key(incumbent_ranked.candidate)")
    elif mutation == "outside":
        current += b"\nUNREVIEWED_SCORING_POLICY = True\n"
    elif mutation == "wrong_old":
        old = old.replace(b"min_core: float = 0.80", b"min_core: float = 0.01")
    elif mutation == "missing":
        current = current.replace(b"async def promote_best_draft(", b"async def other_promotion(")
    else:
        current = current.replace(b"expected_body_sha256", b"unchecked_body_sha256")
    with pytest.raises(ValueError, match="review_compatibility_promotion"):
        recovery._promotion_admission_policy_compatibility(old, current)


def test_actual_policy_manifests_require_exact_old_promotion_bytes(tmp_path):
    old, current = sources()
    services = tmp_path / "src/bestseller/services"
    services.mkdir(parents=True)
    pipeline = b'def chapter_review_recovery_source_hash():\n    return ("pipelines.py", "draft_promotion.py")\n'
    (services / "pipelines.py").write_bytes(pipeline)
    (services / "draft_promotion.py").write_bytes(current)
    # The compatibility service always records the unmodified reviewer bytes.
    (services / "reviews.py").write_text("def evaluate_review():\n    return 'unchanged'\n")

    def policy(promotion):
        result = hashlib.sha256()
        for name, data in (("draft_promotion.py", promotion), ("pipelines.py", pipeline)):
            result.update(("src/bestseller/services/" + name).encode() + b"\0")
            result.update(hashlib.sha256(data).digest())
        return result.hexdigest()

    kwargs = dict(root=tmp_path, previous_pipeline_source=pipeline,
                  expected_previous_policy_hash=policy(old), expected_current_policy_hash=policy(current))
    with pytest.raises(ValueError, match="original_policy_bytes_unverified"):
        recovery.candidate_selection_policy_compatibility(**kwargs)
    result = recovery.candidate_selection_policy_compatibility(**kwargs, previous_promotion_source=old)
    assert result["promotion_admission_compatibility"]["review_evaluator_changed"] is False
    assert result["previous_policy_hash"] == policy(old)
    assert result["current_policy_hash"] == policy(current)
    with pytest.raises(ValueError, match="original_policy_bytes_unverified"):
        recovery.candidate_selection_policy_compatibility(
            **{**kwargs, "expected_previous_policy_hash": "0"*64}, previous_promotion_source=old)
    with pytest.raises(ValueError, match="policy_inventory_unverified"):
        recovery.candidate_selection_policy_compatibility(
            **{**kwargs, "expected_current_policy_hash": "0"*64}, previous_promotion_source=old)

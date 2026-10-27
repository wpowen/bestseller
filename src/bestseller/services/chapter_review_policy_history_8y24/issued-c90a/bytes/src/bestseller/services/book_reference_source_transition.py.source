"""Exact additive book-schema dispatch proof for existing manuscript references.

Legacy parsers, source selection, grading, and physical-return verification must
remain AST-identical. This proof grants no grade or reference by itself; the
existing explicit migration rechecks original physical and deterministic evidence.
"""

import ast
from pathlib import Path

from bestseller.services import draft_continuity_revalidation as policy
from bestseller.services.prompt_source_policy import content_hash

PREFIX = "src/bestseller/services/"
NEW = {"book_reference_source_transition.py", "book_source_review.py", "book_continuity_index.py"}
OWNED = {"draft_continuity.py", "draft_continuity_reference_transition.py", "pipelines.py"}
CHANGED = {PREFIX + name for name in NEW | OWNED}
BRANCH = """if changed == {
    'src/bestseller/services/draft_continuity.py',
    'src/bestseller/services/draft_continuity_reference_transition.py',
    'src/bestseller/services/pipelines.py',
    'src/bestseller/services/book_reference_source_transition.py',
    'src/bestseller/services/book_source_review.py',
    'src/bestseller/services/book_continuity_index.py',
}:
    from bestseller.services.book_reference_source_transition import verify_additive_dispatch
    return verify_additive_dispatch(baseline_manifest=baseline_manifest, baseline_sources=baseline_sources, current_root=current_root)
"""


def _need(ok, code):
    if not ok:
        raise ValueError("book_reference_transition_" + code)


def _dump(nodes):
    return ast.dump(ast.Module(body=nodes, type_ignores=[]))


def verify_additive_dispatch(*, baseline_manifest, baseline_sources, current_root):
    current_root = Path(current_root)
    current = policy.policy_manifest(current_root)
    changed = {
        p
        for p in set(current) | set(baseline_manifest)
        if current.get(p) != baseline_manifest.get(p)
    }
    _need(changed == CHANGED, "unrelated_policy_change")
    for name in NEW:
        _need(PREFIX + name not in baseline_manifest, "new_protocol_already_existed")
    trees = {}
    for name in OWNED:
        key = PREFIX + name
        _need(
            key in baseline_sources
            and policy.sha(baseline_sources[key].encode()) == baseline_manifest.get(key),
            "old_source_witness",
        )
        before = ast.parse(baseline_sources[key])
        after = ast.parse((current_root / key).read_text())
        trees[name] = before, after
    before, after = trees["draft_continuity.py"]
    fn = next(
        n
        for n in after.body
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "_select_reference_without_source_scope"
    )
    expected = ast.parse("""if proof.get('schema')=='book-qualified-source.v1':
    from bestseller.services.book_source_review import validate_reference
    await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)
else:
    pass
""").body[0]
    branches = [
        n for n in fn.body if isinstance(n, ast.If) and ast.dump(n.test) == ast.dump(expected.test)
    ]
    _need(len(branches) == 1, "exact_new_schema_branch")
    branch = branches[0]
    _need(
        _dump(branch.body) == _dump(expected.body)
        and len(branch.orelse) == 1
        and isinstance(branch.orelse[0], ast.If),
        "exact_new_dispatch",
    )
    fn.body[fn.body.index(branch)] = branch.orelse[0]
    _need(ast.dump(before) == ast.dump(after), "legacy_reference_rules_changed")

    before, after = trees["pipelines.py"]
    fn = next(
        n
        for n in after.body
        if isinstance(n, ast.FunctionDef) and n.name == "chapter_review_recovery_source_hash"
    )
    values = next(
        n.iter
        for n in ast.walk(fn)
        if isinstance(n, ast.comprehension)
        and isinstance(n.target, ast.Name)
        and n.target.id == "name"
    )
    added = [n for n in values.elts if isinstance(n, ast.Constant) and n.value in NEW]
    _need(len(added) == len(NEW) and {n.value for n in added} == NEW, "exact_manifest_additions")
    values.elts = [n for n in values.elts if n not in added]
    _need(ast.dump(before) == ast.dump(after), "other_pipeline_rules_changed")

    before, after = trees["draft_continuity_reference_transition.py"]
    fn = next(
        n
        for n in after.body
        if isinstance(n, ast.FunctionDef) and n.name == "verify_dispatch_delta"
    )
    expected = ast.parse(BRANCH).body[0]
    matches = [n for n in fn.body if ast.dump(n) == ast.dump(expected)]
    _need(len(matches) == 1, "exact_transition_dispatch")
    fn.body.remove(matches[0])
    _need(ast.dump(before) == ast.dump(after), "prior_migration_rules_changed")
    return current, {
        "schema": "additive-book-reference-schema-transition.v1",
        "baseline_policy_sha256": policy.manifest_hash(baseline_manifest),
        "current_policy_sha256": policy.manifest_hash(current),
        "baseline_manifest_sha256": content_hash(baseline_manifest),
        "current_manifest_sha256": content_hash(current),
        "changed_paths": sorted(changed),
        "old_eligibility_parser_and_source_rules_unchanged": True,
        "original_grade_relabelled": False,
        "new_model_calls": 0,
    }

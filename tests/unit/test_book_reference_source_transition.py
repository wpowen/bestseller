"""Reject any legacy semantic change when adding a new book reference schema."""

import pytest
from bestseller.services import book_reference_source_transition as change
from bestseller.services import draft_continuity_revalidation as policy

pytestmark = pytest.mark.unit


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    prefix = tmp_path / change.PREFIX
    prefix.mkdir(parents=True)
    before = {
        "draft_continuity.py": "async def _select_reference_without_source_scope(session,settings,*,project,chapter,_memo=None):\n    if proof.get('schema')=='old-schema':\n        await old_validate()\n    else:\n        reject()\n",
        "pipelines.py": "def chapter_review_recovery_source_hash():\n    return tuple(name for name in ('old.py',))\n",
        "draft_continuity_reference_transition.py": "def verify_dispatch_delta(*,baseline_manifest,baseline_sources,current_root):\n    changed=set()\n    return legacy_verification()\n",
    }
    after = dict(before)
    after["draft_continuity.py"] = before["draft_continuity.py"].replace(
        "    if proof.get('schema')=='old-schema':",
        "    if proof.get('schema')=='book-qualified-source.v1':\n        from bestseller.services.book_source_review import validate_reference\n        await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)\n    elif proof.get('schema')=='old-schema':",
    )
    after["pipelines.py"] = before["pipelines.py"].replace(
        "('old.py',)", repr(("old.py", *sorted(change.NEW)))
    )
    after["draft_continuity_reference_transition.py"] = before[
        "draft_continuity_reference_transition.py"
    ].replace(
        "    return legacy_verification()",
        "".join("    " + line + "\n" for line in change.BRANCH.rstrip().splitlines())
        + "    return legacy_verification()",
    )
    for name, source in after.items():
        (prefix / name).write_text(source)
    for name in change.NEW:
        (prefix / name).write_text("# distinct new-schema evaluator\n")
    manifest = {change.PREFIX + n: policy.sha(s.encode()) for n, s in before.items()}
    sources = {change.PREFIX + n: s for n, s in before.items()}

    def current(root):
        return {str(p.relative_to(root)): policy.sha(p.read_bytes()) for p in prefix.glob("*.py")}

    monkeypatch.setattr(policy, "policy_manifest", current)
    return tmp_path, manifest, sources


def verify(fixture):
    root, manifest, sources = fixture
    return change.verify_additive_dispatch(
        baseline_manifest=manifest, baseline_sources=sources, current_root=root
    )


def test_exact_addition_retains_entire_old_ast_and_manifest_witness(fixture):
    _, delta = verify(fixture)
    assert delta["old_eligibility_parser_and_source_rules_unchanged"]
    assert not delta["original_grade_relabelled"]
    assert set(delta["changed_paths"]) == change.CHANGED


@pytest.mark.parametrize(
    "file,old,new",
    [
        ("draft_continuity.py", "await old_validate()", "accept_without_validation()"),
        ("draft_continuity.py", "draft=draft", "draft=other_draft"),
        ("pipelines.py", "'old.py'", "'different_legacy_policy.py'"),
        (
            "draft_continuity_reference_transition.py",
            "legacy_verification()",
            "skip_legacy_verification()",
        ),
    ],
)
def test_other_semantic_change_never_receives_compatibility_receipt(fixture, file, old, new):
    root, _, _ = fixture
    p = root / change.PREFIX / file
    p.write_text(p.read_text().replace(old, new))
    with pytest.raises(ValueError, match="book_reference_transition_"):
        verify(fixture)


def test_unlisted_policy_addition_and_forged_old_witness_fail_closed(fixture):
    root, _, sources = fixture
    extra = root / change.PREFIX / "unrelated.py"
    extra.write_text("changed=True\n")
    with pytest.raises(ValueError, match="unrelated_policy_change"):
        verify(fixture)
    extra.unlink()
    sources[change.PREFIX + "draft_continuity.py"] += "\nchanged=True\n"
    with pytest.raises(ValueError, match="old_source_witness"):
        verify(fixture)

"""Q4 source changes require a separately reviewed transition, never old pins."""

from copy import deepcopy

import pytest

from bestseller.services import draft_continuity_reference_transition as reference
from bestseller.services import draft_continuity_revalidation as revalidation

pytestmark = pytest.mark.unit
PREFIX = "src/bestseller/services/"


@pytest.mark.parametrize(
    "changed_names",
    [
        ("writer_source_capacity.py",),
        ("chapter_writer_sources.py",),
        ("chapter_writer_delivery.py", "writer_source_capacity.py", "chapter_writer_sources.py"),
        ("chapter_writer_delivery.py", "planner.py", "source_bound_planning_content.py"),
    ],
)
def test_old_transition_routes_refuse_unreviewed_q4_delta(monkeypatch, tmp_path, changed_names):
    names = {PREFIX + name for name in changed_names}
    old_sources = dict.fromkeys(names, "# synthetic old policy witness\n")
    old_manifest = {name: revalidation.sha(text.encode()) for name, text in old_sources.items()}
    current_manifest = {
        name: revalidation.sha((text + "# v3 change\n").encode())
        for name, text in old_sources.items()
    }
    before = deepcopy((old_sources, old_manifest, current_manifest))
    monkeypatch.setattr(revalidation, "policy_manifest", lambda root: deepcopy(current_manifest))
    with pytest.raises(ValueError, match="unrelated_policy_change"):
        revalidation.verify_source_transition(
            old_manifest=old_manifest,
            old_sources=old_sources,
            current_root=tmp_path,
            old_policy=revalidation.manifest_hash(old_manifest),
            new_policy=revalidation.manifest_hash(current_manifest),
        )
    with pytest.raises(ValueError, match="unrelated_policy_change"):
        reference.verify_dispatch_delta(
            baseline_manifest=old_manifest,
            baseline_sources=old_sources,
            current_root=tmp_path,
        )
    assert (old_sources, old_manifest, current_manifest) == before


@pytest.mark.parametrize("which", ["old", "current"])
def test_old_transition_requires_exact_manifest_identity(monkeypatch, tmp_path, which):
    name = PREFIX + "writer_source_capacity.py"
    old = {name: "a" * 64}
    current = {name: "b" * 64}
    monkeypatch.setattr(revalidation, "policy_manifest", lambda root: deepcopy(current))
    with pytest.raises(ValueError, match="policy_manifest"):
        revalidation.verify_source_transition(
            old_manifest=old,
            old_sources={},
            current_root=tmp_path,
            old_policy="0" * 64 if which == "old" else revalidation.manifest_hash(old),
            new_policy="0" * 64 if which == "current" else revalidation.manifest_hash(current),
        )

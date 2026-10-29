"""A semantic guard cannot change without invalidating a new issuer domain."""
from pathlib import Path
import pytest
from bestseller.services import pipelines, chapter_review_reconciliation

pytestmark = pytest.mark.unit

@pytest.mark.parametrize("name", ["reader_judge.py", "chapter_revision.py", "deslop_revise.py", "conception_project_handoff.py"])
def test_new_policy_tracks_reader_and_revision_guards(tmp_path, monkeypatch, name):
    root = Path(pipelines.__file__).resolve().parents[3]
    pipeline = root / "src/bestseller/services/pipelines.py"
    paths = chapter_review_reconciliation._review_policy_inventory(root, pipeline.read_text())
    guard = root / "src/bestseller/services" / name
    for original in set(paths) | {guard}:
        destination = tmp_path / original.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(original.read_bytes())
    monkeypatch.setattr(pipelines, "__file__", str(tmp_path / pipeline.relative_to(root)))
    before = pipelines.chapter_review_recovery_source_hash()
    changed = tmp_path / guard.relative_to(root)
    changed.write_bytes(changed.read_bytes() + b"\n# isolated policy inventory mutation\n")
    assert pipelines.chapter_review_recovery_source_hash() != before, "READER_GUARD_OUTSIDE_ISSUER_INVENTORY:" + name

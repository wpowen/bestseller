"""New acceptance dependencies must participate in the paid review identity."""
from pathlib import Path

import pytest

from bestseller.services import pipelines


@pytest.mark.parametrize('relative',['full_flow_length/__init__.py','fanqie_whole_repair.py','full_flow_title_sources.py','review_issuer_archive.py','../../../config/methodology_profiles/reader_first_planning_v1.yaml'])
def test_changing_new_acceptance_helper_changes_review_policy_identity(monkeypatch,relative):
    helper=(Path(pipelines.__file__).parent/relative).resolve()
    before=pipelines.chapter_review_recovery_source_hash()
    original=Path.read_bytes
    def changed(path):
        raw=original(path)
        return raw+b'\n# simulated contract change\n' if path==helper else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert pipelines.chapter_review_recovery_source_hash()!=before

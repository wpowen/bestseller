"""Review identity cannot silently omit a profile consumed by the loader."""
from pathlib import Path

import pytest

from bestseller.services import pipelines
from bestseller.services.chapter_review_reconciliation import _review_policy_inventory

pytestmark = pytest.mark.unit


def test_every_current_genre_yaml_and_loader_are_in_actual_policy():
    root = Path(pipelines.__file__).resolve().parents[3]
    actual = set((root / 'config/genre_profile_thresholds').glob('*.yaml'))
    paths = _review_policy_inventory(root, Path(pipelines.__file__).read_text())
    assert actual and actual <= paths
    assert root / 'src/bestseller/services/genre_profile_thresholds.py' in paths


@pytest.mark.parametrize('filename', ['custom_author_profile.yaml', '新题材.yaml'])
def test_unregistered_consumable_genre_yaml_refuses_real_policy_hash(tmp_path, monkeypatch, filename):
    directory = tmp_path / 'config/genre_profile_thresholds'
    directory.mkdir(parents=True)
    (directory / filename).write_text('genre_id: custom\n')
    source = tmp_path / 'src/bestseller/services/pipelines.py'
    source.parent.mkdir(parents=True)
    monkeypatch.setattr(pipelines, 'Path', lambda _: source)
    # The real function refuses before reading other absent fixture files.
    # No extra YAML is silently blessed by the unchanged finite inventory.
    with pytest.raises(ValueError, match='chapter_review_genre_policy_inventory_incomplete'):
        pipelines.chapter_review_recovery_source_hash()

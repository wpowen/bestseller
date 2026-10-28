"""Full native inventory copies; no database, paid identity, or epoch fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

from issuer_archive_test_support import RELATIVE_MODULE, RELATIVE_PIPELINE, aggregate, sha
import pytest

from bestseller.services import chapter_review_reconciliation, pipelines
from bestseller.settings import ArtifactStoreSettings

MODULE = "bestseller.services.review_issuer_archive"


@dataclass
class ArchiveCase:
    module: object
    root: Path
    artifact_root: Path
    settings: SimpleNamespace
    originals: dict[str, bytes]
    runtime_policy: str

    def capture(self) -> dict:
        return self.module.capture_current_issuer_archive(self.settings)

    def verify(self, capsule: dict) -> dict:
        return self.module.verify_current_issuer_archive(self.settings, capsule=capsule)

    def archive(self, capsule: dict) -> Path:
        return self.artifact_root / capsule["archive_ref"]


@pytest.fixture
def issuer_module():
    # Deliberately fails until main authorizes and the new primitive exists.
    # Import here, rather than skipping or manufacturing a replacement module.
    return importlib.import_module(MODULE)


@pytest.fixture
def archive_case(tmp_path, issuer_module, monkeypatch):
    native_root = Path(pipelines.__file__).resolve().parents[3]
    pipeline_source = (native_root / RELATIVE_PIPELINE).read_text()
    native_paths = chapter_review_reconciliation._review_policy_inventory(
        native_root, pipeline_source
    )
    before = pipelines.chapter_review_recovery_source_hash()
    originals = {}
    root = tmp_path / "native-policy-copy"
    for path in sorted(native_paths):
        assert not path.is_symlink(), "A symlink is not a trusted positive fixture."
        relative = path.relative_to(native_root).as_posix()
        originals[relative] = path.read_bytes()
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(originals[relative])
    assert aggregate({p: sha(raw) for p, raw in originals.items()}) == before
    assert pipelines.chapter_review_recovery_source_hash() == before
    # Isolate either supported installation-location lookup. The native hash
    # function, its inventory, and all verifier functions remain unmodified.
    monkeypatch.setattr(pipelines, "__file__", str(root / RELATIVE_PIPELINE))

    # Load only the CURRENT implementation in an isolated checkout location.
    # This is never a module loaded from an archive. No path/allow-list resolver
    # or verification function is replaced, and the real inventory is unedited.
    implementation = root / RELATIVE_MODULE
    if RELATIVE_MODULE not in originals:
        implementation.parent.mkdir(parents=True, exist_ok=True)
        implementation.write_bytes(Path(issuer_module.__file__).read_bytes())
    name = "bestseller.services._issuer_test_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, implementation)
    assert spec is not None and spec.loader is not None
    relocated = importlib.util.module_from_spec(spec)
    sys.modules[name] = relocated
    try:
        spec.loader.exec_module(relocated)
        artifact_root = tmp_path / "artifacts"
        settings = SimpleNamespace(
            artifact_store=ArtifactStoreSettings(mode="local", local_dir=str(artifact_root))
        )
        yield ArchiveCase(relocated, root, artifact_root, settings, originals, before)
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def captured(archive_case):
    # No synthetic inventory entry is inserted to make this pass. Main must
    # register the primitive in the ACTUAL pipeline inventory before positives
    # can become green; otherwise capture must fail closed (see README).
    assert RELATIVE_MODULE in archive_case.originals, (
        "Native pipeline inventory does not cover review_issuer_archive.py; "
        "main must register it, not bypass the primitive's self-coverage guard."
    )
    receipt = archive_case.capture()
    return archive_case, receipt, receipt["capsule"]

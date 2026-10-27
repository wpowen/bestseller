from types import SimpleNamespace
from uuid import uuid4
import hashlib

import pytest

from bestseller.services import exports, pipelines


@pytest.mark.parametrize("passed", [True, False])
def test_terminal_export_never_changes_adopted_body(monkeypatch, passed):
    chapter = SimpleNamespace(id=uuid4(), chapter_number=1, metadata_json={})
    draft = SimpleNamespace(id=uuid4(), content_md="已采用的原稿。", word_count=7, promotion_state="promoted")
    monkeypatch.setattr(pipelines, "run_final_quality_gates", lambda **kw: SimpleNamespace(
        patched_text="未经采用的新句子。", passed=passed, errors=[], issues=[],
    ))
    with pytest.raises(ValueError):
        exports._run_terminal_export_gate(SimpleNamespace(language="zh-CN"), [(chapter, draft)])
    assert draft.content_md == "已采用的原稿。"
    assert draft.word_count == 7


def test_export_artifact_binds_exact_source_and_preserves_immutable_bytes(tmp_path):
    chapter = SimpleNamespace(id=uuid4(), chapter_number=1)
    draft = SimpleNamespace(id=uuid4(), version_no=2, content_md="正式采用的正文。", promotion_state="promoted")
    path = tmp_path / "chapter.md"
    original = "# 第一章\n\n正式采用的正文。"
    path.write_text(original)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact = exports.create_export_artifact(
        project_id=uuid4(), export_type="markdown", source_scope="chapter", source_id=chapter.id,
        storage_uri=str(path), checksum=checksum, version_label="chapter-001-v2", created_by_run_id=None,
        source_payloads=[(chapter, draft)],
    )
    path.write_text("后续导出的新版本。")
    from pathlib import Path
    assert Path(artifact.storage_uri).read_text() == original
    source = artifact.metadata_json["source_drafts"][0]
    assert source["draft_id"] == str(draft.id)
    assert source["body_fingerprint"] == __import__(
        "bestseller.services.reviews", fromlist=["chapter_review_body_fingerprint"],
    ).chapter_review_body_fingerprint(draft.content_md)

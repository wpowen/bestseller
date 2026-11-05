"""Snapshot generation never persists raw transport metadata or env secrets."""

import json
from pathlib import Path

from tools.real_library_snapshot import capture


def test_snapshot_redacts_and_keeps_only_reader_shape(tmp_path):
    source = tmp_path / "source"
    book = source / "webui/one"
    (book / "chapters").mkdir(parents=True)
    (book / "final").mkdir()
    (book / "web.json").write_text(
        json.dumps(
            dict(
                title="原书",
                chapters=5,
                status="complete",
                api_key="unknown-sensitive-key",
                pipeline={"book": {"pid": 123}},
            )
        )
    )
    (book / "run.json").write_text(json.dumps(dict(book={"chapters": 5, "user_input": "private"}, status="complete")))
    (book / "calls.json").write_text(
        json.dumps(
            [
                dict(
                    model="flash",
                    authorization="hidden",
                    usage={"prompt_tokens": 1, "completion_tokens": 2, "secret": "not-from-env"},
                )
            ]
        )
    )
    for n in range(1, 6):
        (book / f"chapter-{n:03d}.body.md").write_text("正文 fake-fixture-secret-123456")
        (book / f"chapters/{n:03d}.json").write_text(json.dumps(dict(title="章名", status="complete")))
    (book / "final/chapter-004.deai.json").write_text(json.dumps(dict(status="retained", processing_complete=True)))
    (book / "final/chapter-004.md").write_text("回滚原稿")
    env = tmp_path / ".env"
    env.write_text("KEY=fake-fixture-secret-123456\n")
    output = tmp_path / "snapshot"
    before = {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = capture(source, output, [env])
    assert result["books"] == 1
    assert (output / "webui/one/chapter-004.body.md").exists()
    values = "\n".join(p.read_text() for p in output.rglob("*") if p.is_file())
    assert "fake-fixture-secret-123456" not in values
    assert "[REDACTED]" in values
    assert "unknown-sensitive-key" not in values
    assert "not-from-env" not in values
    assert before == {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    # Let pytest remove our read-only capture when cleaning its temporary tree.
    for p in output.rglob("*"):
        p.chmod(0o700 if p.is_dir() else 0o600)
    output.chmod(0o700)

# ruff: noqa: RUF001

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path("scripts/materialize_forward_state.py")
    spec = importlib.util.spec_from_file_location("materialize_forward_state", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_draft_forward_promises_uses_recent_chapter_anchors(tmp_path) -> None:
    module = _load_module()
    (tmp_path / "chapter-010.md").write_text("林渊握着罗盘，陈默递来回执。", encoding="utf-8")

    entries = module.draft_forward_promises(
        book_dir=tmp_path,
        current_chapter=10,
        window=3,
        reveal_tokens={"kou_zhang_ren@20": ["扣账人"]},
    )

    assert [entry["chapter_no"] for entry in entries] == [11, 12, 13]
    assert "林渊" in entries[0]["promise"]
    assert entries[0]["locked_reveal_tokens"] == ["扣账人"]


def test_append_forward_promises_creates_section(tmp_path) -> None:
    module = _load_module()
    ledger = tmp_path / "event-state-ledger.md"

    module.append_forward_promises(
        ledger,
        [
            {
                "chapter_no": 11,
                "promise": "林渊必须承接303门牌继续查。",
                "rollback_forbidden": "不得跳去新怪谈。",
                "locked_reveal_tokens": ["扣账人"],
            }
        ],
    )

    text = ledger.read_text(encoding="utf-8")
    assert "## Forward Promises" in text
    assert "第 11 章" in text
    assert "扣账人" in text

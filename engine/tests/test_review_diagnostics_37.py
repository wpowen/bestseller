"""Legacy books must surface missing promise evidence and invalid design fields."""

import json
from pathlib import Path
import yaml
from tools.book_checkup import check_book


def test_legacy_design_is_checked_without_inventing_promise_adoption(tmp_path):
    bible = json.loads((Path(__file__).parent / "fixtures/promise37/eval33.json").read_text())
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True))
    for n in [1, 2, 3]:
        (tmp_path / f"chapter-{n:03d}.body.md").write_text("沈亦衡没有借招。")
    actual = check_book(tmp_path, source="draft")["review_contracts"]
    assert actual["cost_design"]["invalid_stages"] == [1, 2, 3, 4, 5]
    assert any(i["address"] == "阿砺" for i in actual["address_design"]["issues"])
    assert actual["opening_promises"]["counts"] == {"unknown": 3}
    assert actual["opening_promises"]["authority"] == "agent_editorial_not_human"


def test_saved_promise_notes_remain_reference_with_sources(tmp_path):
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapter-001.body.md").write_text("她拨钟救人。")
    (tmp_path / "chapters/001.json").write_text(
        json.dumps(
            dict(
                opening_promises=dict(
                    status="repaired",
                    after=dict(
                        items=[dict(id="core-1", status="fulfilled", evidence=[dict(chapter=1, quote="不存在的句子")])]
                    ),
                )
            )
        )
    )
    actual = check_book(tmp_path, source="draft")["review_contracts"]["opening_promises"]
    assert actual["counts"] == {"unknown": 1}

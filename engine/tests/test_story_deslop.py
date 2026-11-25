import json

from deai.io import process

ENDING = "许林把课本递给周桐。周桐听见走廊有脚步，隔着门往外看，楼梯口的几盏灯还亮着。"
SOURCE = f"门开了。屋门已经打开，进屋的路已经敞开。\n\n{ENDING}"
REPLACEMENT = "屋门开了，进屋那条路已经敞开。"


class SkillModel:
    reject = False

    def __init__(self, *_args, **_kwargs):
        self.calls = []

    def __call__(self, messages, *, stage, max_tokens):
        self.calls.append({"stage": stage})
        if stage == "story-deslop-scan-chapter":
            assert "叠加式描写" in messages[1]["content"]
            assert "屋门已经打开" in messages[1]["content"]
            return {
                "tier": "重度",
                "findings": [
                    {
                        "id": "P2",
                        "gates": ["C", "D"],
                        "kind": "redundant_explanation",
                        "supports": {"id": "P1", "quote": "门开了"},
                        "quote": "屋门已经打开",
                        "reason": "连写三遍开门",
                        "keep": ["屋门"],
                    },
                ],
            }
        if stage == "story-deslop-edit-chapter":
            assert "一次最小改写" in messages[1]["content"]
            return {"edits": [{"id": "P1", "original": SOURCE.split("\n")[0], "replacement": REPLACEMENT}]}
        assert stage == "story-deslop-recheck-chapter"
        return {
            "facts_preserved": not self.reject,
            "verdicts": [
                {
                    "id": "P1",
                    "preserves": not self.reject,
                    "improves": True,
                    "fact_quote": REPLACEMENT,
                    "reason": "候选改变了叙述" if self.reject else "合并重复",
                },
                {"id": "P2", "preserves": True, "improves": False, "reason": "额外评论了未修改段"},
            ],
        }

    def estimate(self):
        return 0


def run_skill(book, model):
    first = book / "chapter-001.body.md"
    first.write_text(SOURCE, encoding="utf-8")
    status = process(book, "001", first, None, "local", None, model, editor="story-deslop")
    assert first.read_text() == SOURCE
    report = json.loads((book / "final/chapter-001.deai.json").read_text())
    assert report["text"] == (book / "final/chapter-001.md").read_text()
    assert report["checks"]["story_deslop"]["name"] == "story-deslop"
    return status, report


def test_framework_runs_bundled_skill_and_publishes_checked_edit(tmp_path):
    status, report = run_skill(tmp_path, SkillModel)
    assert status == "polished"
    assert report["text"] == f"{REPLACEMENT}\n\n{ENDING}"
    assert report["checks"]["story_deslop"]["accepted_gates"] == {"C": 1, "D": 1}
    assert [row["id"] for row in report["checks"]["story_deslop"]["review"]["verdicts"]] == ["P1"]
    assert report["cumulative"]["requests"] == 3
    check = report["checks"]["story_deslop"]
    assert (check["text_effect"], check["proposed_paragraphs"], check["accepted_paragraphs"]) == (
        "paragraph_edits",
        1,
        1,
    )


def test_framework_retains_source_when_skill_review_fails(tmp_path):
    class Reject(SkillModel):
        reject = True

    status, report = run_skill(tmp_path, Reject)
    assert status == "unchanged"
    assert report["text"] == SOURCE
    assert report["checks"]["story_deslop"]["review"]["facts_preserved"] is False
    check = report["checks"]["story_deslop"]
    assert (check["text_effect"], check["proposed_paragraphs"], check["accepted_paragraphs"]) == ("unchanged", 1, 0)


def test_absent_findings_leave_every_character_untouched(tmp_path):
    class NoFindings(SkillModel):
        def __call__(self, messages, *, stage, max_tokens):
            assert stage == "story-deslop-scan-chapter"
            self.calls.append({"stage": stage})
            return {"tier": "轻度", "findings": []}

    source = tmp_path / "chapter-001.body.md"
    source.write_text("他摸到门边——那里还有一把锁。")
    process(tmp_path, "001", source, None, "local", None, NoFindings, editor="story-deslop")
    report = json.loads((tmp_path / "final/chapter-001.deai.json").read_text())
    check = report["checks"]["story_deslop"]
    assert report["text"] == source.read_text() and report["edits"] == []
    assert (check["text_effect"], check["proposed_paragraphs"], check["accepted_paragraphs"]) == (
        "unchanged",
        0,
        0,
    )

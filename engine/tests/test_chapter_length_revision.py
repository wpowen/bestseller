from bookengine.q1_amend import length_edit_status, retained_state_stage
from bookengine.q1_chapter import amend
from bookengine.storage import save
from support_minimal_b import CaptureClient, MinimalBTests, body


class TestLengthRevision(MinimalBTests):
    def test_closer_but_outside_band_keeps_draft_and_original_state(self):
        draft = body(20, 100)
        stage = "r2-ch2-retained-state-1"
        contaminated = self.extraction(
            summary="新稿中她去了码头。", state={"characters": {"阿宁": {"location": "码头"}}}
        )
        save(self.out / "steps/r2-ch2-final-state.json", contaminated)
        first = dict(contaminated, body=body(20, 160))
        client = CaptureClient(self.out, [draft], {"r2-ch2-finish": first, stage: self.extraction()})
        row = self.run_chapter(client)
        assert [c["stage"] for c in client.calls if c["stage"] != "r2-ch2-boundary-reextract"] == [
            "r2-ch2-draft",
            "r2-ch2-finish",
            stage,
        ]
        self.assert_saved(row, draft)
        assert not row["within_band"] and not row["state_failed"]
        assert row["polish"]["length_revision"] == "candidate_out_of_band"
        prompt = next(call["prompt"] for call in client.calls if call["stage"] == "r2-ch2-finish")
        assert "当前100汉字，本章目标200，范围170—230汉字" in prompt
        assert "2200" not in prompt and "2000–2400" not in prompt
        assert row["state"]["characters"]["阿宁"]["location"] == "院"
        assert row["summary"] == "阿宁在院里等她。"
        assert (self.out / "steps" / (stage + ".input.md")).read_text() == draft
        assert retained_state_stage(self.out, "r2-ch2", body(20, 101)) != stage
        (self.out / "chapters/002.json").unlink()  # Resume before completion, with the existing finish and recheck.
        resumed = CaptureClient(self.out, [])
        assert self.run_chapter(resumed)["state"] == row["state"]
        assert not resumed.calls

    def test_short_complete_draft_without_replacement_needs_no_length_recheck(self):
        draft = body(20, 100)
        client = CaptureClient(self.out, [draft], {"r2-ch2-finish": self.extraction()})
        row = self.run_chapter(client)
        self.assert_calls(client, ["r2-ch2-draft", "r2-ch2-finish"])
        assert row["polish"]["length_revision"] == "not_proposed" and not row["within_band"]

    def test_failed_recheck_of_length_edit_rolls_back_with_original_state(self):
        draft = body(20, 100)
        invalid = {"state": {"characters": {"阿宁": {"location": "码头"}}}}
        stage = "r2-ch2-retained-state-1"
        client = CaptureClient(
            self.out,
            [draft],
            {"r2-ch2-finish": self.extraction(body=body(20)), "r2-ch2-final-state": invalid, stage: self.extraction()},
        )
        row = self.run_chapter(client)
        assert [c["stage"] for c in client.calls if c["stage"] != "r2-ch2-boundary-reextract"] == [
            "r2-ch2-draft",
            "r2-ch2-finish",
            "r2-ch2-final-state",
            stage,
        ]
        self.assert_saved(row, draft)
        assert not row["polish"]["body_adopted"] and not row["state_failed"]
        assert row["polish"]["length_revision"] == "state_recheck_failed"
        assert row["state"]["characters"]["阿宁"]["location"] == "院"


def test_within_band_rewrite_of_all_paragraphs_is_not_a_local_length_edit():
    original = "阿宁收起钥匙。\n\n她开门。"
    revised = "陈师傅站在漆黑的院子里。\n\n月光从墙上的缺口照进来。"
    assert length_edit_status(original, revised, (18, 30)) == "nonlocal_rewrite"
    assert length_edit_status(body(20, 340), body(20, 240), (170, 230)) == "candidate_out_of_band"


def test_one_paragraph_draft_can_retain_its_unaffected_sentence():
    opening = "她贴着墙把钥匙插进门锁。"
    assert length_edit_status(opening + "门轴断了。", opening + "门轴一断，铁门砸在她脚边。", (18, 26)) == "accepted"


def test_local_edit_can_take_place_within_one_sentence():
    assert length_edit_status("她拿起钥匙走回门边。", "她拿起生了锈的钥匙走回门边。", (11, 13)) == "accepted"


def test_local_edit_can_split_one_paragraph_across_two():
    opening = "她把钥匙留在门背后，侧着身推开门。"
    closing = "外头的人一声没吭，帮她托住门板。"
    original = f"{opening}\n\n门轴断了。\n\n{closing}"
    revised = f"{opening}\n\n门轴往下一沉。\n\n她伸手挡住门板。\n\n{closing}"
    assert length_edit_status(original, revised, (35, 55)) == "accepted"


def test_one_shared_opening_cannot_authorize_replacing_every_other_paragraph():
    opening = "天亮了。\n\n"
    original = opening + "她在树下挖出一个空匣。\n\n匣底还有一层，木片已经翘了起来。"
    proposed = opening + "客栈里的人听到门外的马蹄声。\n\n她穿过长街，看见渡口已经围满了人。"
    assert length_edit_status(original, proposed, (29, 38)) == "nonlocal_rewrite"


def test_targeted_sentence_edit_takes_precedence_over_optional_length_edit():
    draft = "她明白这不是胜利，而是新的开始。\n\n她关上门。"
    revised = draft.replace("她关上门。", "她关上门，拿起木栓，插进门边的空槽。")
    raw = {"body": revised, "edits": [{"id": 1, "text": "她拿起钥匙。"}]}
    actual, note = amend(draft, raw, {}, {}, {"word_range": (25, 42)})
    assert actual == "她拿起钥匙。\n\n她关上门。"
    assert not note["body_adopted"] and note["length_revision"] == "local_repair_preferred"

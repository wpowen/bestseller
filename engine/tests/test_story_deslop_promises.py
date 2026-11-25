"""Exercise local guards even when the scripted independent reviewer approves everything."""

import json

import pytest

from test_story_deslop_evidence import REST, run


class ApproveEdits:
    def __init__(self, edits):
        self.edits = edits
        self.calls = []

    def __call__(self, messages, *, stage, max_tokens):
        self.calls.append({"stage": stage})
        data = json.loads(messages[1]["content"].rsplit("\n", 1)[-1])
        if stage == "story-deslop-scan-chapter":
            findings = [
                dict(
                    id=pid,
                    quote=old,
                    keep=[keep],
                    gates=["C"],
                    kind="expression",
                    reason="对白归属的措辞妨碍辨别说话人",
                )
                for pid, (old, _, keep) in self.edits.items()
            ]
            return dict(tier="轻度", findings=findings)
        if stage == "story-deslop-edit-chapter":
            return dict(edits=[dict(id=k, original=v[0], replacement=v[1]) for k, v in self.edits.items()])
        assert stage == "story-deslop-recheck-chapter"
        return dict(
            facts_preserved=True,
            verdicts=[
                dict(id=r["id"], preserves=True, improves=True, fact_quote=r["after"], reason="照旧兑现")
                for r in data["changes"]
            ],
        )

    def estimate(self):
        return 0


OFFER = "“只要船能靠岸，我就替你送信。”阿棠说。"


def test_conditional_offer_deletion_is_isolated_before_review(tmp_path):
    bare = "“我替你送信。”阿棠说。"
    coat, coat_edit = "他把湿衣裳脱下来，脱了搁到门边。", "他脱下湿衣裳，搁到门边。"
    model = ApproveEdits({"P1": (OFFER, bare, "替你送信"), "P2": (coat, coat_edit, "湿衣裳")})
    _, report = run(tmp_path, "\n\n".join((OFFER, coat, REST)), model)
    check = report["checks"]["story_deslop"]
    assert check["programmatic_rejections"] == [
        dict(
            pid="P1",
            reasons=["removed_promise_condition"],
            promise_evidence=dict(
                before="“只要船能靠岸，我就替你送信。”", after="“我替你送信。”", condition="只要船能靠岸"
            ),
        )
    ]
    assert report["text"] == "\n\n".join((OFFER, coat_edit, REST))
    assert [r["id"] for r in check["review"]["verdicts"]] == ["P2"]


@pytest.mark.parametrize(
    "old,new,keep",
    [
        (OFFER, OFFER.replace("只要船能靠岸，我就", "船一靠岸，我便"), "替你送信"),
        ("“如果天亮了，门口大概就有人。”", "“天亮以后，门口兴许有人。”", "门口"),
        ("“若是我没记错，我替你送过信。”", "“我记得我替你送过信，也许记岔了。”", "送过信"),
        ("“我替你送信。”阿棠开口说道。", "“我替你送信。”阿棠说。", "替你送信"),
    ],
)
def test_no_blanket_ban_on_hypotheticals_or_spoken_offers(tmp_path, old, new, keep):
    # A prerequisite in some other paragraph should not bind an unrelated edit here.
    before = "“要是起雾了，先停船。”撑船人回头招呼。"
    _, report = run(tmp_path, "\n\n".join((before, old, REST)), ApproveEdits({"P2": (old, new, keep)}))
    assert not report["checks"]["story_deslop"]["programmatic_rejections"]
    assert report["text"] == "\n\n".join((before, new, REST))


def test_clean_scan_preserves_sounds_and_unflagged_punctuation(tmp_path):
    lines = ("“当——当——当——”", "“咯吱——嘎吱——”", "“给我——起！”", "“咔嚓——！”", "她终于听清了——原来是门上的铜铃。")
    model = ApproveEdits({})
    _, report = run(tmp_path, "\n\n".join(lines), model)
    assert report["text"] == "\n\n".join(lines)
    assert len(model.calls) == 1
    assert report["checks"]["story_deslop"]["punctuation_edits"] == 0


def test_v3_final_is_preserved_until_an_explicit_v4_run(tmp_path):
    model = ApproveEdits({})
    source = "“咯吱——嘎吱——”"
    run(tmp_path, source, model)
    path = tmp_path / "final/.deai/003/policy.json"
    policy = json.loads(path.read_text())
    assert policy["editor"]["adapter_version"] == 4
    policy["editor"]["adapter_version"] = 3
    path.write_text(json.dumps(policy))
    assert run(tmp_path, source, model, preserve=True)[0] == "cached"
    assert len(model.calls) == 1
    assert run(tmp_path, source, model)[0] == "unchanged"
    assert len(model.calls) == 2

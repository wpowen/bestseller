"""A grounded mention alone does not close a foreshadowing obligation."""

import pytest

from bookengine.longutil import ask as pack_review
from bookengine.story_design import normalize
from bookengine.story_duties import due_duties, load_ledger, record_story_duties, with_story_duties
from test_story_design61 import make_bible, model_design


def duty(kind="payoff", requirement="拆开蓝信封，确认写信人"):
    return dict(id="letter-payoff", kind=kind, chapter=14, anchors=["蓝信封"], names=[], text=requirement)


@pytest.mark.parametrize(
    "text",
    [
        "蓝信封还在桌上，没有人拆。",
        "她看了看蓝信封，打算拆开蓝信封，认出写信人是沈青梧。",
        "她尚未拆开蓝信封，也不知写信人是谁。",
        "如果她拆开蓝信封，就能认出写信人是沈青梧。",
        "她拆开蓝信封，仍不知道写信人是谁。",
        "她拆开蓝信封，想确认写信人是不是沈青梧。",
        "蓝信封仍在手里，她拆开红信封，从落款认出写信人是沈青梧。",
        "她想象着下一幕。她拆开蓝信封，从落款认出写信人是沈青梧。",
        "她拆开蓝信封，从落款认出写信人是沈青梧。这只是她的设想。",
    ],
)
def test_letter_mention_denial_and_plan_stay_unverified(tmp_path, text):
    outline = {"story_duties": [duty()]}
    assert record_story_duties(tmp_path, 14, text, outline)[0]["status"] == "unverified"
    assert load_ledger(tmp_path)["letter-payoff"]["mentioned"] is True


def test_opening_letter_and_identifying_sender_closes_without_a_review_call(tmp_path):
    text = "她拆开蓝信封，从落款认出写信人是沈青梧。"

    def ask(*args, **kwargs):
        pytest.fail("explicit action and result should be verified locally")

    assert record_story_duties(tmp_path, 14, text, {"story_duties": [duty()]}, ask=ask)[0]["status"] == "done"
    entry = load_ledger(tmp_path)["letter-payoff"]
    assert entry["verification"] == "deterministic" and entry["quote"] == text


def test_ambiguous_obligations_use_one_grounded_review(tmp_path):
    text = "她把蓝信封折成了船。纸船一入水，禁制便消失。"
    action = "她把蓝信封折成了船。"
    items = [
        duty(requirement="用蓝信封渡过禁制"),
        dict(duty(kind="remind", requirement="蓝信封帮她试探禁制"), id="letter-remind"),
    ]
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        calls.append((key, dict(sections)))
        return {
            "items": [
                dict(id="letter-payoff", chapter=14, status="fulfilled", quote=text),
                dict(id="letter-remind", chapter=13, status="fulfilled", quote=action),
            ]
        }

    result = record_story_duties(tmp_path, 14, text, {"story_duties": items}, ask=ask)
    assert [row["status"] for row in result] == ["done", "unverified"]
    assert len(calls) == 1 and text in calls[0][1].values()
    entry = load_ledger(tmp_path)["letter-payoff"]
    assert entry["verification"] == "grounded_review" and entry["quote"] == text


@pytest.mark.parametrize(
    "text,quote",
    [
        ("她并没有拆开蓝信封。", "拆开蓝信封"),
        ("蓝信封摆在桌上。", "蓝信封摆在桌上。"),
        ("她看了蓝信封一眼。", "她看了蓝信封一眼。"),
        ("蓝信封还在桌上，写信人是谁仍不清楚。", "蓝信封还在桌上，写信人是谁仍不清楚。"),
        ("她把蓝信封折成了船。", "她拆开蓝信封，信尾写着沈青梧。"),
    ],
)
def test_invalid_or_non_event_review_quotes_cannot_close_a_duty(tmp_path, text, quote):
    def ask(*args, **kwargs):
        return {"items": [dict(id="letter-payoff", chapter=14, status="fulfilled", quote=quote)]}

    result = record_story_duties(tmp_path, 14, text, {"story_duties": [duty()]}, ask=ask)
    assert result[0]["status"] == "unverified"


def test_a_later_chapter_without_the_anchor_keeps_the_last_mention(tmp_path):
    outline = {"story_duties": [duty()]}
    mention = "蓝信封还在桌上，没有人拆。"
    record_story_duties(tmp_path, 14, mention, outline)
    record_story_duties(tmp_path, 15, "她去了城南。", outline)
    entry = load_ledger(tmp_path)["letter-payoff"]
    assert (entry["chapter"], entry["quote"], entry["status"]) == (14, mention, "unverified")
    assert entry["attempts"] == [14, 15]


def test_unverified_payoff_remains_due():
    bible = make_bible()
    design = normalize(model_design(), bible, 50)
    plant = design["foreshadows"][0]
    chapter = plant["payoff"] + 1
    ledger = {f"{plant['id']}-payoff": dict(status="unverified", chapter=chapter - 1)}
    pending = due_duties(design, bible, chapter, "日常", ledger)
    assert f"{plant['id']}-payoff" in {row["id"] for row in pending}


def test_early_plant_cannot_close_on_a_plan_to_find_the_object(tmp_path):
    bible = make_bible()
    bible["story_design"] = normalize(model_design(), bible, 50)
    plant = bible["story_design"]["foreshadows"][0]
    (tmp_path / "chapter-001.body.md").write_text(f"等到明日，他打算去寻找{plant['anchor']}。", encoding="utf-8")
    outline = with_story_duties(dict(number=plant["plant"], type="对抗", scenes=[]), bible, tmp_path)
    assert f"{plant['id']}-plant" in {row["id"] for row in outline["story_duties"]}
    assert f"{plant['id']}-plant" not in load_ledger(tmp_path)


def test_unreadable_review_is_cached_as_unknown_without_repeating(tmp_path):
    stages = []

    class Client:
        prefix = ""

        def stage(self, name, prompt, schema, *, parser, **kwargs):
            stages.append(name)
            return parser("无法从这几句作判断")

    result = record_story_duties(
        tmp_path,
        14,
        "她把蓝信封折成了船。",
        {"story_duties": [duty(requirement="用蓝信封渡过禁制")]},
        client=Client(),
        prefix="r2-ch14",
        ask=pack_review,
    )
    assert result[0]["status"] == "unverified"
    assert stages == ["r2-ch14-foreshadow-verification"]


def test_literal_truth_can_be_verified_but_not_inside_a_plan(tmp_path):
    knowledge = "旧铜扣就是藏室钥匙"
    reveal = dict(duty("truth", "让她查明旧铜扣的用途"), anchors=["旧铜扣"], proof_event=knowledge)
    outline = {"story_duties": [reveal]}
    planned = f"她打算证明{knowledge}。"
    observed = f"铜片与锁眼完全吻合，她终于看清{knowledge}。"
    assert record_story_duties(tmp_path, 14, planned, outline)[0]["status"] == "unverified"
    assert record_story_duties(tmp_path, 14, observed, outline)[0]["status"] == "done"

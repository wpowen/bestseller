from copy import deepcopy

from bookengine.hook_momentum import repair_after_draft


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.names = []


def fake_ask(client, name, prompt, sections, schema=None, **kwargs):
    client.names.append(name)
    return client.answers.pop(0)


def fake_extract(client, name, text, state, bible, outline, rows, final=False):
    quote = "顾冬岚夺回三枚灵石"
    raw = {
        "state": {},
        "payoff": {
            "type": outline.get("payoff_type"),
            "fulfilled": quote in text,
            "quote": quote if quote in text else "",
        },
    }
    return raw, False, 0


def test_missing_due_payoff_gets_one_grounded_patch_without_changing_tail():
    original = "顾冬岚推开石门。\n\n门外的追兵已经举刀。"
    client = FakeClient([{"patch": {"after": 1, "text": "顾冬岚夺回三枚灵石。"}}])
    changed, raw, failed, note = repair_after_draft(
        client,
        "r2-ch1",
        original,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {
            "number": 1,
            "hook": "迫近危险：追兵举刀",
            "ending_type": "迫近危险",
            "payoff_due": True,
            "payoff_type": "得到回报",
        },
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert "顾冬岚夺回三枚灵石" in changed
    assert changed.endswith("门外的追兵已经举刀。")
    assert note["payoff"]["status"] == "repaired"
    assert note["hook"]["status"] == "kept"
    assert raw["payoff"]["fulfilled"] is True
    assert client.names == ["r2-ch1-momentum-payoff-repair"]
    assert not failed


def test_repair_does_nothing_when_hook_and_payoff_are_already_grounded():
    text = "顾冬岚夺回三枚灵石。\n\n门外的追兵已经举刀。"
    raw = {"state": {}, "payoff": {"fulfilled": True, "quote": "顾冬岚夺回三枚灵石"}}
    client = FakeClient([])
    changed, observed, failed, note = repair_after_draft(
        client,
        "r2-ch1",
        text,
        deepcopy(raw),
        False,
        {},
        {},
        {"number": 1, "ending_type": "迫近危险", "payoff_due": True},
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed == text
    assert observed == raw
    assert note["hook"]["status"] == "kept"
    assert note["payoff"]["status"] == "kept"
    assert client.answers == []
    assert not failed


def test_hook_repair_rolls_back_if_it_loses_an_already_grounded_payoff():
    quote = "顾冬岚夺回三枚灵石"
    original = "前文不动。\n\n" + quote + "。\n\n“好。”"
    raw = {"state": {}, "payoff": {"fulfilled": True, "quote": quote}}
    client = FakeClient([{"tail": ["门外忽然响起密集脚步。", "追兵的刀光已经逼到门缝。"]}])
    changed, observed, failed, note = repair_after_draft(
        client,
        "r2-ch1",
        original,
        deepcopy(raw),
        False,
        {},
        {},
        {
            "number": 1,
            "hook": "迫近危险：追兵举刀",
            "ending_type": "迫近危险",
            "payoff_due": True,
        },
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed == original
    assert observed == raw
    assert note["hook"]["status"] == "rolled_back"
    assert note["after"]["payoff"]["classification"] == "fulfilled"
    assert not failed

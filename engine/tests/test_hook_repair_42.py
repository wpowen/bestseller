from bookengine.hook_momentum import diagnose_ending, repair_after_draft


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.names = []
        self.schemas = []


def fake_ask(client, name, prompt, sections, schema=None, **kwargs):
    client.names.append(name)
    client.schemas.append(schema)
    return client.answers.pop(0)


def fake_extract(client, name, text, state, bible, outline, rows, final=False):
    return {"state": {}, "payoff": {}}, False, 0


def test_repair_after_draft_changes_only_tail_and_accepts_grounded_hook():
    prefix = "前文第一段。\n\n前文第二段。\n\n"
    text = prefix + "众人收起东西。\n\n“好。”"
    client = FakeClient([{"tail": ["众人刚要离开，门外忽然响起密集脚步。", "追兵的刀光已经逼到门缝。"]}])
    changed, raw, failed, note = repair_after_draft(
        client,
        "r2-ch1",
        text,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {"number": 1, "hook": "迫近危险：追兵封门", "ending_type": "迫近危险", "payoff_due": False},
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed.startswith(prefix)
    assert diagnose_ending(changed)["classification"] == "hook_candidate"
    assert note["hook"]["status"] == "repaired"
    assert client.names == ["r2-ch1-momentum-hook-repair"]
    assert isinstance(client.schemas[0], str)
    assert raw["state"] == {}
    assert not failed


def test_repair_after_draft_rolls_back_when_replacement_is_still_flat():
    original = "顾冬岚推开石门。\n\n“走吧。”"
    client = FakeClient([{"tail": ["众人收起东西。", "“好。”"]}])
    changed, raw, failed, note = repair_after_draft(
        client,
        "r2-ch1",
        original,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {"number": 1, "hook": "反常物件：铜铃自鸣", "ending_type": "反常物件", "payoff_due": False},
        {"number": 2, "scenes": [{"goal": "追查铜铃"}]},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed == original
    assert note["hook"]["status"] == "rolled_back"
    assert client.names == ["r2-ch1-momentum-hook-repair"]
    assert raw["state"] == {}
    assert not failed


def test_repair_uses_last_two_paragraphs_when_model_echoes_context_in_one_item():
    original = "前文不动。\n\n众人收起东西。\n\n“好。”"
    echoed = "前文不动。\n\n众人收起东西。\n\n“好。”\n\n门外忽然响起密集脚步。\n\n追兵的刀光已经逼到门缝。"
    client = FakeClient([{"tail": [echoed]}])
    changed, _, _, note = repair_after_draft(
        client,
        "r2-ch1",
        original,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {"number": 1, "hook": "迫近危险：追兵封门", "ending_type": "迫近危险", "payoff_due": False},
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed.startswith("前文不动。\n\n")
    assert changed.endswith("门外忽然响起密集脚步。\n\n追兵的刀光已经逼到门缝。")
    assert note["hook"]["status"] == "repaired"


def test_repair_accepts_a_different_concrete_hook_type_than_the_plan_requested():
    original = "前文不动。\n\n众人收起东西。\n\n“好。”"
    client = FakeClient([{"tail": ["他没有站起来，仍靠着山壁。", "他还在往前爬。"]}])
    changed, _, _, note = repair_after_draft(
        client,
        "r2-ch1",
        original,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {"number": 1, "hook": "迫近危险：余石松动", "ending_type": "迫近危险", "payoff_due": False},
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed != original
    assert note["hook"]["status"] == "repaired"
    assert note["hook"]["types"] == ["悬停动作"]


def test_repair_rolls_back_when_tail_exceeds_saved_word_band():
    original = "前文不动。\n\n众人收起东西。\n\n“好。”"
    client = FakeClient([{"tail": ["门外忽然响起密集脚步。" * 20, "追兵的刀光已经逼到门缝。" * 20]}])
    changed, _, _, note = repair_after_draft(
        client,
        "r2-ch1",
        original,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {
            "number": 1,
            "hook": "迫近危险：追兵封门",
            "ending_type": "迫近危险",
            "payoff_due": False,
            "word_range": [10, 40],
        },
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed == original
    assert note["hook"]["status"] == "rolled_back"


def test_planned_stage_close_may_keep_one_flat_ending():
    original = "众人收起东西。\n\n“走吧。”"
    client = FakeClient([])
    changed, _, _, note = repair_after_draft(
        client,
        "r2-ch10",
        original,
        {"state": {}, "payoff": {}},
        False,
        {},
        {},
        {"number": 10, "hook": "阶段收束", "ending_type": "平收", "payoff_due": False},
        {},
        [],
        fake_extract,
        fake_ask,
    )
    assert changed == original
    assert note["hook"]["status"] == "kept"
    assert client.answers == []

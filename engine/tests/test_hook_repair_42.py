from bookengine.hook_momentum import diagnose_ending, repair_after_draft
from bookengine.prose_similarity import analyze_prose


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


def test_web59c_hook_rewrite_replaces_semantic_source_instead_of_appending_it():
    source = (
        "前文不动。\n\n"
        "顾长风却在这时察觉到另一处不对。方才第四具失去校准的瞬间，他右臂深处那截残余线曾极轻地一跳，"
        "恰与木偶迈向第二步的节拍相同；那不是外廊阵纹的回馈，而是他自己的断线残权在替他补正步幅。\n\n"
        "众人暂时收起证匣。\n\n"
        "众人散去。"
    )
    rewritten = [
        "第四具木偶失去校准的瞬间，他右臂深处那截残余线曾极轻地一跳，节拍恰与木偶迈出的第二步重合。"
        "那一下来自他自己，而非外廊阵纹的回馈；是尚未彻底崩解的断线残权，替他补上了缺失的步幅。",
        "顾长风刚要追索那股牵力，院墙外忽然传来整齐的木足声。",
    ]
    client = FakeClient([{"tail": rewritten}])

    changed, _, _, note = repair_after_draft(
        client,
        "r2-ch8",
        source,
        {"state": {}, "payoff": {}},
        False,
        {},
        {"characters": [{"name": "顾长风", "gender": "男"}]},
        {"number": 8, "hook": "迫近危险：木足逼近", "ending_type": "迫近危险", "payoff_due": False},
        {},
        [],
        fake_extract,
        fake_ask,
    )

    assert "顾长风却在这时察觉到另一处不对" not in changed
    assert "顾长风右臂深处" in changed
    grounded = [rewritten[0].replace("他右臂", "顾长风右臂", 1), rewritten[1]]
    assert changed.endswith("\n\n".join(grounded))
    assert analyze_prose(changed)["counts"]["near_paragraph_pairs"] == 0
    assert note["hook"]["status"] == "repaired"


def test_adjacent_semantic_repetition_threshold_catches_web59c_pair_without_false_positive():
    duplicate = (
        "顾长风却在这时察觉到另一处不对。方才第四具失去校准的瞬间，他右臂深处那截残余线曾极轻地一跳，"
        "恰与木偶迈向第二步的节拍相同；那不是外廊阵纹的回馈，而是他自己的断线残权在替他补正步幅。\n\n"
        "第四具木偶失去校准的瞬间，他右臂深处那截残余线曾极轻地一跳，节拍恰与木偶迈出的第二步重合。"
        "那一下来自他自己，而非外廊阵纹的回馈；是尚未彻底崩解的断线残权，替他补上了缺失的步幅。"
    )
    unrelated = "顾长风收起证匣，沿石阶返回外院。\n\n沈展尧按住伤口，没有追问木偶的来历。"

    assert analyze_prose(duplicate)["counts"]["near_paragraph_pairs"] == 1
    assert analyze_prose(unrelated)["counts"]["near_paragraph_pairs"] == 0


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

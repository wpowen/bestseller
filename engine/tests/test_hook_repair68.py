"""Instruction 68 (E6): an ending is rewritten only on evidence that it is flat, and never made worse."""

from bookengine.hook_momentum import diagnose_ending, needs_hook_repair, repair_after_draft, tail_regression


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.names = []


def fake_ask(client, name, prompt, sections, schema=None, **kwargs):
    client.names.append(name)
    return client.answers.pop(0)


def fake_extract(client, name, text, state, bible, outline, rows, final=False):
    quote = "石子翎接下了雷九的拳"
    return {"state": {}, "payoff": {"fulfilled": quote in text, "quote": quote if quote in text else ""}}, False, 0


def _run(text, answers, **outline):
    client = FakeClient(answers)
    plan = {"number": 5, "hook": "反常物件：旧木桩渗水", "ending_type": "反常物件", "payoff_due": False, **outline}
    changed, _, _, note = repair_after_draft(
        client, "r2-ch5", text, {"state": {}, "payoff": {}}, False, {}, {}, plan, {}, [], fake_extract, fake_ask
    )
    return changed, note, client


# The saved draft tail of 《武馆新客·挨打成钢》 chapter 5 (r2-ch5-draft).
DRAFT_TAIL = (
    "“没什么。”\n\n"
    "他盯着那根旧木桩，没有说出异常，也没有靠近查看。可下一息，桩头忽然裂开一道缝，"
    "里面渗出的不是木屑，而是一线暗红水痕，沿木纹无声地爬向地面。\n\n"
    "石子翎咬住衣角，仍没有回头。"
)
# What r2-ch5-momentum-hook-repair returned and the book adopted.
ADOPTED_TAIL = [
    "石子翎缓缓放下拳头，指节仍在发麻，却再没有退回沙袋后。木桩院里的学徒们没有出声，雷九脸色铁青的沉默，"
    "却比任何喝彩都更清楚地见证了石子翎从沙袋旁走出的第一步。",
    "他转身走向练武场门前时，身后的沙袋忽然发出一声沉闷的摩擦。那只旧沙袋竟自行从地面立了起来。石子翎停在门前，尚未走近查看。",
]


def test_saved_draft_ending_is_recognised_as_a_hook_and_left_alone():
    text = "前文。\n\n" + DRAFT_TAIL
    assert diagnose_ending(text)["classification"] == "hook_candidate"
    changed, note, client = _run(text, [])
    assert changed == text
    assert note["hook"]["status"] == "kept"
    assert client.names == []


def test_ending_without_a_keyword_is_not_rewritten_unless_it_is_explicitly_flat():
    text = "前文。\n\n秦野朝石元铮点头：“拆。”\n\n石元铮的炉锤高高举起，对准主火管第一道旧铜封。"
    diagnosis = diagnose_ending(text)
    assert diagnosis["classification"] == "flat_candidate" and not diagnosis["explicit_flat"]
    assert not needs_hook_repair(diagnosis)
    changed, note, client = _run(text, [])
    assert changed == text and client.names == []


def test_explicitly_flat_ending_is_still_repaired():
    text = "前文第一段。\n\n众人收起东西。\n\n“走吧。”"
    assert needs_hook_repair(diagnose_ending(text))
    changed, note, client = _run(text, [{"tail": ["众人刚要离开，门外忽然响起密集脚步。", "追兵的刀光已经逼到门缝。"]}])
    assert note["hook"]["status"] == "repaired"
    assert changed.startswith("前文第一段。\n\n")


def test_summary_sentence_in_the_rewritten_tail_rolls_the_repair_back():
    text = "前文第一段。\n\n众人收起东西。\n\n“走吧。”"
    changed, note, client = _run(text, [{"tail": ADOPTED_TAIL}])
    assert changed == text
    assert note["hook"] == {"status": "rolled_back", "reason": "summary_tail"}
    assert client.names == ["r2-ch5-momentum-hook-repair"]


def test_tail_that_grows_far_beyond_the_original_rolls_back():
    before = "前文。\n\n" + "他把刀收回鞘中，看了一眼门外的雪。" * 4 + "\n\n" + "众人散去。"
    after = "前文。\n\n" + "追兵的刀光逼近门缝，" * 20 + "。\n\n" + "他刚要起身，门外的脚步又近了一层，" * 12 + "。"
    assert tail_regression(before, after) == "tail_bloat"
    assert tail_regression(before, before) is None


def test_payoff_patch_never_lands_after_a_closing_hook():
    text = "石子翎走进木桩院。\n\n雷九抬起了棍。\n\n门外的追兵已经举刀，正朝他逼近。"
    changed, note, client = _run(
        text, [{"patch": {"after": 1, "text": "石子翎接下了雷九的拳。"}}], payoff_due=True, payoff_type="收获"
    )
    assert note["payoff"]["status"] == "repaired" and note["payoff"]["placement"] == "before_final"
    assert changed.endswith("门外的追兵已经举刀，正朝他逼近。")
    assert changed.index("雷九抬起了棍。") < changed.index("石子翎接下了雷九的拳。")
    assert note["hook"]["status"] == "kept"


def test_payoff_patch_stays_before_a_last_paragraph_no_keyword_recognises():
    # 《末世零件铺》 chapter 26: the patch was appended after this closing beat.
    text = "周铁匠扣紧最后一包轴承。\n\n唐岚没有再追问，只把那把旧钥匙攥进掌心。金属硌着伤口，她却没松手。"
    changed, note, _ = _run(
        text, [{"patch": {"after": 1, "text": "石子翎接下了雷九的拳。"}}], payoff_due=True, payoff_type="收获"
    )
    assert note["payoff"]["status"] == "repaired"
    assert changed.endswith("金属硌着伤口，她却没松手。")

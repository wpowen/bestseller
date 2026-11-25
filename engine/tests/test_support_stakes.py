"""A supporting character's choice and the fate of their private goal are separate obligations."""

import json

from bookengine.story_design import normalize
from bookengine.story_duties import due_duties, load_ledger, planner_sections, record_story_duties, with_story_duties
from bookengine.story_payoffs import repair_story_payoffs
from bookengine.story_report import duty_summary


def book():
    bible = {
        "characters": [
            {"name": "朝棠", "role": "protagonist"},
            {"name": "何叶", "role": "support", "desire": "取回失传的家谱"},
        ],
        "volumes": [{"number": 1, "start": 1, "end": 10}],
    }
    choice = dict(
        name="何叶",
        target="家谱",
        choice="推掉掌柜以她改姓换取家谱的提议",
        choice_chapter=3,
        consequence="交代她和家人怎样承担她的决定",
        consequence_chapter=6,
    )
    design = normalize(dict(support_stakes=[dict(choice, name="陌生人"), choice, choice]), bible, 10)
    return dict(bible, story_design=design), design


def scheduled(n, bible, out):
    return with_story_duties(dict(number=n, type="日常", scenes=[dict(characters=["何叶"])]), bible, out)


def review(outline, text, state, quotes, model_status="fulfilled"):
    number = outline["number"]
    calls = []

    def ask(_, key, instruction, sections, schema, **kwargs):
        calls.append(key)
        items = json.loads(dict(sections)["本章必须发生的事"])
        assert len(items) == 1 and "goal_state" in schema and "拒绝" in instruction
        return dict(
            items=[
                dict(
                    id=items[0]["id"],
                    status=model_status,
                    goal_state=state,
                    evidence=[dict(chapter=number, quote=q) for q in quotes],
                )
            ]
        )

    body, _, _, result = repair_story_payoffs(None, "audit", text, {}, False, {}, {}, outline, None, ask)
    assert calls == ["audit-prose-review"] and body == text
    return result


def test_desire_belongs_to_existing_support_and_schedule_respects_completed_books(tmp_path):
    bible, design = book()
    assert len(design["support_stakes"]) == 1 and design["support_stakes"][0]["desire"] == "取回失传的家谱"
    invalid = normalize(dict(support_stakes=[dict(design["support_stakes"][0], consequence_chapter=11)]), bible, 10)
    assert not invalid["support_stakes"]
    foreign = normalize(dict(support_stakes=[dict(design["support_stakes"][0], target="赎金")]), bible, 10)
    assert not foreign["support_stakes"]
    old = {key: value for key, value in design.items() if key != "support_stakes"}
    assert not any(d["kind"].startswith("support_") for d in due_duties(old, bible, 10, "日常", {}))
    assert "改姓换取家谱" in str(planner_sections(bible, tmp_path, 2, 6, 1))


def test_nonfinancial_choice_is_recorded_and_not_misreported_as_recovery(tmp_path):
    bible, design = book()
    text = "何叶把写着新姓的纸推回掌柜那头。掌柜立刻把木匣锁上提走了。"
    quotes = ["何叶把写着新姓的纸推回掌柜那头。", "掌柜立刻把木匣锁上提走了。"]
    choice = scheduled(3, bible, tmp_path)
    note = review(choice, text, "chose", quotes)
    record_story_duties(tmp_path, 3, text, choice, payoff_review=note)
    entry = load_ledger(tmp_path)["support-何叶-choice"]
    assert entry["status"] == "done" and entry["goal_state"] == "chose"
    subsequent = scheduled(6, bible, tmp_path)
    assert any("第3章已作选择" in duty["text"] for duty in subsequent["story_duties"])
    note = review(subsequent, text, "chose", quotes)
    record_story_duties(tmp_path, 6, text, subsequent, payoff_review=note)
    entry = load_ledger(tmp_path)["support-何叶-consequence"]
    assert entry["status"] == "unverified" and entry["goal_state"] == "chose"
    note = review(subsequent, text, "achieved", quotes)
    assert note["before"]["items"][0]["goal_state"] == "unknown"
    assert not any(
        d["kind"].startswith("support_") for d in due_duties(design, bible, 7, "日常", load_ledger(tmp_path))
    )


def test_promise_partial_and_grounded_outcome_remain_distinct_after_polishing(tmp_path):
    bible, design = book()
    outline = scheduled(6, bible, tmp_path)
    # Isolate the scheduled consequence for this checkpoint.
    outline["story_duties"] = [d for d in outline["story_duties"] if d["kind"] == "support_consequence"]
    offer = "何叶把手伸向木匣。掌柜笑道：“只要改姓，今日便交还家谱。”"
    for supplied, expected in [("promised", "promised"), ("achieved", "unknown")]:
        note = review(outline, offer, supplied, ["何叶把手伸向木匣。", "今日便交还家谱。"])
        record_story_duties(tmp_path, 6, offer, outline, payoff_review=note)
        entry = load_ledger(tmp_path)["support-何叶-consequence"]
        assert entry["status"] == "unverified" and entry["goal_state"] == expected
    body = "何叶解开家谱上的旧封。母亲收下谱册，在自家祠堂重新摆好。"
    quotes = ["何叶解开家谱上的旧封。", "母亲收下谱册，在自家祠堂重新摆好。"]
    note = review(outline, body, "achieved", quotes)
    record_story_duties(tmp_path, 6, body, outline, payoff_review=note)
    ledger = load_ledger(tmp_path)

    def get_result(text):
        return duty_summary(design, bible, ledger, 6, {6: text})["support_stakes"][-1]

    assert get_result(body)["goal_state"] == "achieved"
    assert get_result(body.replace(quotes[1], ""))["status"] == "unverified"
    assert get_result(body.replace(quotes[1], ""))["goal_state"] == "unknown"
    fake = dict(note, status="rolled_back", before={"items": []}, after=note["before"])
    record_story_duties(tmp_path, 6, body, outline, payoff_review=fake)
    assert load_ledger(tmp_path)["support-何叶-consequence"]["status"] == "unverified"


def test_a_paid_delay_is_a_consequence_when_the_goal_itself_stays_open(tmp_path):
    bible, _ = book()
    outline = scheduled(6, bible, tmp_path)
    outline["story_duties"] = [d for d in outline["story_duties"] if d["kind"] == "support_consequence"]
    first = "何叶把改姓的字据投进了火里。"
    last = "掌柜封死存放家谱的匣子，当场收走她下月进祠的牌子。"
    note = review(outline, first + "\n\n" + last, "deferred_with_cost", [first, last])
    row = note["before"]["items"][0]
    assert row["status"] == "fulfilled" and row["goal_state"] == "deferred_with_cost"


def test_a_free_relative_can_be_named_in_one_paragraph_and_act_in_the_next(tmp_path):
    bible, _ = book()
    bible["characters"][1]["desire"] = "接回被扣的妹妹"
    row = dict(bible["story_design"]["support_stakes"][0], target="妹妹")
    bible["story_design"] = normalize(dict(support_stakes=[row]), bible, 10)
    outline = scheduled(6, bible, tmp_path)
    outline["story_duties"] = [d for d in outline["story_duties"] if d["kind"] == "support_consequence"]
    first = "何叶把钱袋递进门缝。"
    last = "她提起铺盖，穿过刚打开的院门。"
    for presence, expected in [("妹妹把袋子里的银钱倒在桌上。", "achieved"), ("看守问：“你妹妹还等着吗？”", "unknown")]:
        row = review(outline, first + presence + "\n\n" + last, "achieved", [first, last])["before"]["items"][0]
        assert row["goal_state"] == expected

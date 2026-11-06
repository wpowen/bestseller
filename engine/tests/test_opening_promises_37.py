"""R2: real negation/address failures, grounded acceptance and transactional repair."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from bookengine.opening_promises import opening_contract, review_opening, repair_opening_promises
from bookengine.address_contracts import address_issues, address_findings, repair_addresses

FIXTURE = json.loads((Path(__file__).parent / "fixtures/promise37/eval33.json").read_text())


def bible():
    return deepcopy(FIXTURE)


def test_real_eval33_contract_splits_opening_and_excludes_later_promises():
    items = opening_contract(bible())
    assert {i["kind"] for i in items} >= {"core_use", "visible_cost", "reader"}
    opening = [i for i in items if i["kind"] == "reader"]
    assert any("借招反杀" in i["requirement"] for i in opening)
    assert any("声音被夺" in i["requirement"] for i in opening)
    assert not any("终局" in i["requirement"] or "每十章" in i["requirement"] for i in opening)
    assert {i["chapter"] for i in items if i["kind"] == "core_use"} == {1, 2, 3}


def test_real_eval33_negative_quote_cannot_prove_use_even_if_reviewer_says_yes():
    text = FIXTURE["chapter1_negative"]

    def ask(client, key, prompt, sections, schema, **kwargs):
        items = json.loads(dict(sections)["逐项验收"])
        return dict(items=[dict(id=i["id"], status="fulfilled", evidence=[dict(chapter=1, quote=text)]) for i in items])

    note = review_opening(None, "test", text, bible(), 1, {}, ask)
    assert next(i for i in note["items"] if i["kind"] == "core_use")["status"] != "fulfilled"
    assert note["status"] != "checked"


@pytest.mark.parametrize("response", [{}, {"items": []}, {"items": [{"id": "core-1", "status": "unknown"}]}])
def test_missing_or_unknown_review_never_passes(response):
    note = review_opening(None, "test", "周远舟攥紧旧钟。", {}, 1, {}, lambda *a, **k: response)
    assert note["status"] == "unfulfilled"


def test_real_eval33_address_must_have_origin_even_if_in_card():
    issues = address_issues(bible())
    assert any(i["address"] == "阿砺" for i in issues)
    found = address_findings(FIXTURE["chapter2_address"], bible(), {})
    assert any(i["address"] == "砺哥" and i["status"] != "Evidence" for i in found)


def test_name_derived_and_address_origin_first_use_in_later_chapter():
    b = dict(characters=[dict(name="周远舟", role="protagonist", address="阿舟")])
    assert not address_issues(b)
    b["characters"][0].update(address="石头", address_origin="幼时替妹妹挡下落石")
    assert not address_issues(b)
    assert address_findings("石头，快走！", b, {})
    text = "石头，是他幼时替妹妹挡下落石后得来的称呼。"
    assert not address_findings(text, b, {})
    assert not address_findings("石头，快走！", b, {4: text})
    assert address_findings("石头，快走！", b, {4: "他走了。"})


def test_address_origin_cannot_be_unrelated_or_denied():
    b = dict(characters=[dict(name="周远舟", role="protagonist", address="石头", address_origin="挡下落石")])
    assert address_findings("石头走了。\n\n旁人曾挡下落石。", b, {})
    assert address_findings("石头这个称呼不是因为挡下落石。", b, {})


def test_address_repair_only_updates_owned_fields_and_rechecks():
    b = bible()
    before = deepcopy(b)

    def ask(kind, key, sections, redo):
        return dict(
            characters=[
                dict(name=c["name"], address="阿衡", address_target="沈亦衡", title="禁止改动") for c in b["characters"]
            ]
        )

    changed, note = repair_addresses(b, ask)
    assert note["status"] == "passed"
    assert b == before
    assert changed["title"] == before["title"]
    assert changed["characters"][0]["name"] == before["characters"][0]["name"]
    assert changed["characters"][0]["address"] == "阿衡"


def replies(client, key, prompt, sections, schema, **kwargs):
    if key.endswith("-repair"):
        return dict(edits=[dict(id=1, original="周远舟握钟。", text="周远舟借钟声震退追兵，手心裂开一道血口。")])
    text = dict(sections)["本章正文"]
    items = json.loads(dict(sections)["逐项验收"])
    return dict(
        items=[
            dict(
                id=i["id"],
                status="fulfilled" if "借钟声" in text else "missing",
                evidence=[dict(chapter=1, quote=text)] if "借钟声" in text else [],
                paragraphs=[1],
            )
            for i in items
        ]
    )


@pytest.mark.parametrize("invalid", [False, True])
def test_targeted_repair_reextracts_and_rolls_back_if_final_state_fails(invalid):
    original = "周远舟握钟。"
    raw = {"state": {}, "events": []}
    extracted = []

    def extract(*args, **kwargs):
        extracted.append((args[2], kwargs))
        return {"state": {}, "events": [], "summary": "新正文"}, invalid, []

    text, checked, failed, note = repair_opening_promises(
        None, "ch1", original, raw, False, {}, {}, {"number": 1}, {}, extract, replies
    )
    assert extracted and extracted[0][1]["final"] is True
    assert note["status"] == ("rolled_back" if invalid else "repaired")
    assert text == (original if invalid else "周远舟借钟声震退追兵，手心裂开一道血口。")
    assert checked == (raw if invalid else {"state": {}, "events": [], "summary": "新正文"})
    assert failed is False


def test_opening_checks_each_due_item_against_first_three_chapters():
    b = dict(reader_expectation="前三章兑现救回妹妹与交出家传戒指")

    def ask(client, key, prompt, sections, schema, **kwargs):
        items = json.loads(dict(sections)["逐项验收"])
        return dict(
            items=[dict(id=i["id"], status="fulfilled", evidence=[dict(chapter=1, quote="救回妹妹")]) for i in items]
        )

    note = review_opening(None, "test", "交出家传戒指。", b, 3, {1: "他救回妹妹。"}, ask)
    assert all(i["status"] != "fulfilled" for i in note["items"] if i["kind"] in ("core_use", "visible_cost"))
    assert (
        review_opening(None, "test", "无关", b, 4, {}, lambda *a, **k: pytest.fail("late review"))["status"]
        == "not_due"
    )


def test_repair_missing_or_invalid_edits_preserves_original():
    def ask(*args, **kwargs):
        if args[1].endswith("-repair"):
            return {"edits": [{"id": 99, "original": "不存在", "text": "改稿"}]}
        return {}

    text, raw, failed, note = repair_opening_promises(
        None, "ch1", "原文。", {}, True, {}, {}, {"number": 1}, {}, lambda *a, **k: pytest.fail("invalid edit"), ask
    )
    assert (text, raw, failed) == ("原文。", {}, True)
    assert note["status"] == "rolled_back"


def test_prepare_persists_contract_and_resume_preserves_design_without_calls(tmp_path):
    from bookengine.config import Book
    from bookengine.q1_prepare import prepare

    b = dict(
        title="借钟救人",
        reader_expectation="前三章救回妹妹与交出戒指",
        characters=[
            dict(name=n, role=r, voice="直说", desire="救人")
            for n, r in [("周远舟", "protagonist"), ("赵听禾", "support"), ("王见微", "antagonist")]
        ],
        volumes=[
            dict(
                number=1,
                start=1,
                end=3,
                location="街口",
                antagonist="王见微",
                goal="救人",
                climax="救出妹妹",
                exit_chapter=3,
            )
        ],
        antagonists=[dict(name="王见微", stage=1, tier=1, exit=3)],
    )

    class Client:
        prefix = ""
        calls = []

        def stage(self, key, prompt, schema, **kwargs):
            assert key == "plan-q1-prepare"
            assert "第1章必须实际展示" in prompt
            assert "address_target" in schema
            self.calls.append(key)
            return kwargs["parser"](json.dumps(dict(bible=b, threads={}), ensure_ascii=False))

    c = Client()
    book = Book("suspense-mystery", 3, 2200, "")
    assets = Path(__file__).resolve().parents[2] / "config"
    result, _, _ = prepare(c, book, tmp_path, assets)
    assert result["opening_contract"] == opening_contract(result)
    before = (tmp_path / "story_bible.yaml").read_bytes()
    prepare(c, book, tmp_path, assets)
    assert len(c.calls) == 1
    assert (tmp_path / "story_bible.yaml").read_bytes() == before


def test_late_first_address_use_still_enters_transactional_repair():
    b = dict(characters=[dict(name="周远舟", role="protagonist", address="石头", address_origin="替妹妹挡下落石")])
    note = review_opening(None, "ch8", "石头，走！", b, 8, {}, lambda *a, **k: pytest.fail("no model needed"))
    assert note["status"] == "unfulfilled"
    assert note["items"][0]["kind"] == "address_origin"

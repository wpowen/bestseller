"""INBOX40 item 6: real bible conflicts and bounded first-use origin evidence."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from bookengine import address_contracts as addresses
from bookengine.opening_promises import review_opening


FIXTURES = Path(__file__).parent / "fixtures/address40"


def real_bible(name):
    return yaml.safe_load((FIXTURES / f"{name}-story_bible.yaml").read_text())


def origin_bible():
    return dict(characters=[dict(name="周远舟", role="protagonist", address="石头", address_origin="挡下落石")])


def check(bible, corpus):
    return addresses.address_checkup(bible, [dict(number=n, text=t) for n, t in corpus.items()])


def test_real_trial50_names_originator_but_their_own_address_disagrees():
    bible = real_bible("trial-50")
    before = deepcopy(bible)
    conflicts = [i for i in addresses.address_issues(bible) if i.get("reason") == "originator_address_mismatch"]
    assert any(i["address"] == "砚哥" and i["originator"] == "顾安澜" for i in conflicts)
    assert any(i["speaker"] == "顾安澜" and i["expected_address"] == "砚哥" for i in conflicts)
    assert bible == before


def test_real_eval33_still_requires_origins_for_both_unrelated_addresses():
    issues = addresses.address_issues(real_bible("eval-33"))
    assert {"阿砺", "砺哥"} <= {i["address"] for i in issues}


def test_consistency_repair_can_patch_originator_only_and_preserves_other_fields():
    bible = real_bible("trial-50")
    before = deepcopy(bible)
    calls = []

    def ask(kind, key, sections, retry):
        calls.append(sections)
        return dict(
            characters=[dict(name="顾安澜", address="砚哥", address_origin="顾安澜自幼叫哥哥砚哥", identity="禁止改写")]
        )

    repaired, note = addresses.repair_addresses(bible, ask)
    assert calls and note["status"] == "passed"
    assert next(c for c in repaired["characters"] if c["name"] == "顾安澜")["address"] == "砚哥"
    assert bible == before
    for old, new in zip(before["characters"], repaired["characters"]):
        editable = {"address", "address_origin"} if old["name"] == "顾安澜" else set()
        assert {k: v for k, v in old.items() if k not in editable} == {
            k: v for k, v in new.items() if k not in editable
        }


def test_origin_mentions_another_actor_without_naming_action_is_not_a_conflict():
    bible = origin_bible()
    bible["characters"][0]["address_origin"] = "替郑玉兰挡下落石"
    bible["characters"].append(dict(name="郑玉兰", address="玉兰"))
    assert addresses.address_issues(bible) == []


@pytest.mark.parametrize("address", [None, "", "阿兰"])
def test_originator_needs_same_target_and_label_even_if_own_card_has_no_address(address):
    bible = origin_bible()
    bible["characters"][0]["address_origin"] = "郑玉兰小时候喊他石头"
    bible["characters"].append(dict(name="郑玉兰", address=address))
    assert any(i.get("originator") == "郑玉兰" for i in addresses.address_issues(bible))


def test_originator_with_multiple_valid_addresses_is_consistent():
    bible = origin_bible()
    bible["characters"][0]["address_origin"] = "郑玉兰小时候喊他石头"
    bible["characters"].append(dict(name="郑玉兰", address="石头/周大哥", address_target="周远舟"))
    assert not [i for i in addresses.address_issues(bible) if i.get("reason") == "originator_address_mismatch"]


@pytest.mark.parametrize("chapter", [4, 5, 6])
def test_explanation_anywhere_in_first_use_adjacent_window_is_accepted(chapter):
    corpus = {n: "他在屋中。" for n in range(1, 8)}
    corpus[5] = "石头，快走！"
    # The explanation itself includes the address, so chapter 4 becomes first use.
    corpus[chapter] = "他推开门。\n\n石头，是他挡下落石后得来的称呼。"
    result = check(origin_bible(), corpus)
    assert result["findings"] == []
    row = result["checks"][0]
    assert row["status"] == "explained" and row["evidence"][0]["chapter"] == chapter
    assert row["first_use_chapter"] == min(5, chapter)


def test_late_explanation_does_not_erase_first_use_failure():
    result = check(origin_bible(), {1: "石头快走！", 2: "众人走了。", 3: "石头是他挡下落石后得来的称呼。"})
    assert result["findings"][0]["first_use_chapter"] == 1
    assert result["findings"][0]["status"] == "missing"


@pytest.mark.parametrize(
    "explanation",
    [
        "石头不是因为挡下落石才有的称呼。",
        "石头并非因为挡下落石才有的称呼。",
        "石头的名字与挡下落石无关。",
        "石头因为挡下落石才有了这个称呼？",
        "石头推开门。\n\n旁人曾挡下落石。",
    ],
)
def test_adjacent_window_cannot_accept_denied_or_unrelated_explanations(explanation):
    result = check(origin_bible(), {1: "石头快走！", 2: explanation})
    assert result["findings"] and result["checks"][0]["status"] == "missing"


def test_unwritten_next_chapter_is_pending_and_a_gap_remains_unknown():
    assert check(origin_bible(), {5: "石头，快走！"})["checks"][0]["status"] == "pending"
    assert check(origin_bible(), {1: "石头，快走！", 3: "他走了。"})["checks"][0]["status"] == "Unknown"


def test_complete_book_closes_last_chapter_window_but_not_an_interior_gap():
    result = addresses.address_checkup(origin_bible(), [dict(number=1, text="石头，快走！")], complete=True)
    assert result["checks"][0]["status"] == "missing"
    result = addresses.address_checkup(
        origin_bible(), [dict(number=1, text="石头，快走！"), dict(number=3, text="他回家了。")], complete=True
    )
    assert result["checks"][0]["status"] == "Unknown"


def test_whole_book_scan_reports_an_unexplained_first_use_even_without_later_mentions():
    result = check(origin_bible(), {1: "石头快走！", 2: "他走了。", 3: "下雨了。"})
    assert len(result["findings"]) == 1
    row = result["findings"][0]
    assert row["chapter"] == 1 and row["quote"] == "石头"
    assert result["authority"] == "agent_editorial_not_human"


def test_repeated_unexplained_first_use_is_not_cleared_by_remote_explanation():
    finding = addresses.address_findings(
        "石头，快走！", origin_bible(), {1: "石头快走！", 2: "雨停了。", 3: "石头是他挡下落石后得来的称呼。"}
    )
    assert finding and finding[0]["first_use_chapter"] == 1


def test_runtime_can_explain_later_in_same_chapter_and_uses_real_chapter_number():
    text = "石头快走！\n\n他推开门。\n\n石头是他挡下落石后得来的称呼。"
    note = review_opening(None, "chapter5", text, origin_bible(), 5, {1: "他回家了。"}, None)
    assert note["status"] == "not_due"


def test_conflicting_shared_label_cannot_hide_missing_origin_in_second_row():
    bible = origin_bible()
    bible["characters"].append(dict(name="郑玉兰", address="石头", address_target="周远舟"))
    result = check(bible, {1: "石头是他挡下落石后得来的称呼。", 2: "他们离开了。"})
    assert any(i["speaker"] == "郑玉兰" for i in result["findings"])

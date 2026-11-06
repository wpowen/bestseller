"""INBOX40 supervisor follow-up: plain kinship terms need no origin story."""

import pytest

from bookengine import address_contracts as addresses


def sibling_bible(label):
    return dict(
        characters=[
            dict(name="顾冬岚", role="protagonist"),
            dict(name="顾安澜", role="support", address=label, address_target="顾冬岚"),
        ]
    )


def kinship_rows(bible, label):
    return [i for i in addresses.address_issues(bible) if i["address"] == label]


@pytest.mark.parametrize("label", ["哥", "哥哥", "大哥", "姐", "娘", "师父", "师兄"])
def test_plain_kinship_address_needs_no_origin(label):
    bible = sibling_bible(label)
    assert kinship_rows(bible, label) == []
    corpus = [dict(number=1, text=f"“{label}，等等我。”顾安澜说。")]
    report = addresses.address_checkup(bible, corpus)
    assert all(row.get("address") != label for row in report["findings"])


def test_unrelated_nickname_still_needs_origin():
    assert kinship_rows(sibling_bible("砚哥"), "砚哥")


def test_kinship_address_has_no_runtime_repair_finding():
    bible = sibling_bible("哥")
    assert addresses.address_findings("“哥，等等我。”顾安澜说。", bible, {}, number=1) == []

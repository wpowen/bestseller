"""Renaming a cast member must carry name-derived addresses along (砚哥/阿砺 bug)."""

from bookengine.rename_addresses import rename_addresses


def cards(**protagonist):
    return dict(
        characters=[
            dict(name="顾冬岚", role="protagonist", **protagonist),
            dict(name="顾安澜", role="support", address="哥", address_target="顾冬岚"),
        ]
    )


def test_single_char_given_name_address_follows_rename():
    bible = cards(address="砚哥", address_origin="顾安澜小时候嫌他名字生硬，撒娇时只肯叫他砚哥")
    out = rename_addresses(bible, {"顾砚": "顾冬岚"})
    hero = out["characters"][0]
    assert hero["address"] == "岚哥"
    assert hero["address_origin"] == "顾安澜小时候嫌他名字生硬，撒娇时只肯叫他岚哥"
    assert bible["characters"][0]["address"] == "砚哥"


def test_prefix_nickname_follows_rename_and_other_text_is_untouched():
    bible = cards(address="阿砺/砺哥", address_origin="师父说他像块磨砺过的石头，便叫他阿砺")
    out = rename_addresses(bible, {"沈砺": "沈亦衡"})
    hero = out["characters"][0]
    assert hero["address"] == "阿衡/衡哥"
    assert hero["address_origin"] == "师父说他像块磨砺过的石头，便叫他阿衡"


def test_plain_kinship_and_unrelated_addresses_are_kept():
    bible = cards(address="小石头", address_origin="挡下过落石")
    out = rename_addresses(bible, {"顾砚": "顾冬岚"})
    assert out["characters"][0]["address"] == "小石头"
    assert out["characters"][1]["address"] == "哥"


def test_no_mapping_returns_equal_copy():
    bible = cards(address="砚哥")
    assert rename_addresses(bible, {}) == bible

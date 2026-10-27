from __future__ import annotations

# ruff: noqa: RUF001 — the regression preserves the source Chinese punctuation.
from bestseller.services.book_design import extract_creation_protagonist_name


def test_leading_action_clause_does_not_become_creation_protagonist() -> None:
    premise = (
        "宗门宣布，灵根最弱的弟子必须替全宗挡下天劫。"
        "陆衡的妹妹被推上祭台时，他才发现自己听得见雷声里的求救。"
    )

    # The later ``陆衡的妹妹`` relation is not enough to assert that陆衡 is
    # the protagonist.  Returning unknown is safer than locking ``宗门宣布``
    # as a person and lets the existing semantic identity review decide.
    assert extract_creation_protagonist_name({"premise": premise}) == ""


def test_other_event_heads_also_remain_unknown() -> None:
    for premise in (
        "王朝覆灭，百姓流离失所。林场失火，巡守连夜赶来。",
        "陈国破碎，旧臣各自逃亡。",
        "商队启程，风雪很快封住山口。",
    ):
        assert extract_creation_protagonist_name({"premise": premise}) == ""


def test_explicit_creation_metadata_keeps_a_four_character_single_surname_name() -> None:
    assert (
        extract_creation_protagonist_name(
            {"premise": "王朝覆灭，旧臣逃亡。", "creation_protagonist_name": "王小明远"}
        )
        == "王小明远"
    )

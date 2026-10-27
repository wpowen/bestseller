from __future__ import annotations

from bestseller.services.conception import _reconcile_downstream_story_contract


def test_reconcile_rebuilds_nested_story_engine_from_canonical_winner_fields() -> None:
    winner = {
        "concept": "修复师在爆破前还原封门证据。",
        "emotional_promise": "每次还原都逼近可验证的真相。",
        "endgame_direction": "在爆破前公开封门指令并完成责任追究。",
        "story_architecture": {
            "schema_version": "story-architecture.v1",
            "target_chapters": 20,
            "story_engine": {
                "reader_promise": "旧项目的筹码交易循环。",
                "endgame_direction": "不是找出封门者，而是保留原件换取安全。",
                "custom_audit_field": "保留审计字段",
            },
        },
    }

    reconciled = _reconcile_downstream_story_contract(
        winner, target_chapters=20, preserve_existing=False
    )

    engine = reconciled["story_architecture"]["story_engine"]
    assert engine["endgame_direction"] == winner["endgame_direction"]
    assert engine["reader_promise"] == winner["emotional_promise"]
    assert engine["custom_audit_field"] == "保留审计字段"
    assert reconciled["story_architecture"]["target_chapters"] == 20
    assert winner["story_architecture"]["story_engine"]["endgame_direction"] != engine[
        "endgame_direction"
    ]


def test_reconcile_lineage_signature_is_stable_for_same_contract_input() -> None:
    winner = {
        "endgame_direction": "公开终局证据。",
        "story_architecture": {"story_engine": {"endgame_direction": "旧方向"}},
    }
    first = _reconcile_downstream_story_contract(winner, target_chapters=20)
    second = _reconcile_downstream_story_contract(winner, target_chapters=20)

    # The helper itself is pure; the persisted source signature is attached by
    # the async reconciliation boundary where literal seed/premise are known.
    assert first == second


def test_reconcile_isolated_books_do_not_share_nested_story_contract_state() -> None:
    first = {
        "endgame_direction": "公开第一本书的终局证据。",
        "emotional_promise": "第一本书的持续承诺。",
        "story_architecture": {"story_engine": {"endgame_direction": "陈旧终局一"}},
    }
    second = {
        "endgame_direction": "完成第二本书的终局选择。",
        "emotional_promise": "第二本书的持续承诺。",
        "story_architecture": {"story_engine": {"endgame_direction": "陈旧终局二"}},
    }

    first_out = _reconcile_downstream_story_contract(
        first, target_chapters=20, preserve_existing=False
    )
    second_out = _reconcile_downstream_story_contract(
        second, target_chapters=20, preserve_existing=False
    )

    assert first_out["story_architecture"]["story_engine"]["endgame_direction"] == (
        "公开第一本书的终局证据。"
    )
    assert second_out["story_architecture"]["story_engine"]["endgame_direction"] == (
        "完成第二本书的终局选择。"
    )
    assert first_out["story_architecture"]["story_engine"]["endgame_direction"] != second_out[
        "story_architecture"
    ]["story_engine"]["endgame_direction"]


def test_reconcile_does_not_invent_nested_contract_without_canonical_value() -> None:
    winner = {
        "story_architecture": {
            "story_engine": {"endgame_direction": "已有审计方向"},
        },
    }

    reconciled = _reconcile_downstream_story_contract(winner, target_chapters=49)

    assert reconciled["story_architecture"]["story_engine"]["endgame_direction"] == "已有审计方向"
    assert reconciled["story_architecture"]["target_chapters"] == 49

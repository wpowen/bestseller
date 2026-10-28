"""Offline contracts: no database, pipeline, provider or model required."""

# Chinese punctuation is part of the input contract.
# ruff: noqa: RUF001
from decimal import Decimal

import pytest

from bestseller.services.checker_schema import CheckerReport
from bestseller.services.quantity_consistency_probe import (
    extract_facts,
    parse_number,
    probe_chapters,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("五十二", "52"),
        ("两百零五", "205"),
        ("一万零三十", "10030"),
        ("三千五百五十", "3550"),
        ("一亿零三万", "100030000"),
        ("二〇二六", "2026"),
        ("三点五", "3.5"),
        ("1,200.50", "1200.50"),
        ("半", ".5"),
        ("十二万三千", "123000"),
    ],
)
def test_number_normalization(raw, expected):
    assert parse_number(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "几", "十余", "一百百", "1,20"])
def test_ambiguous_or_invalid_number_is_not_guessed(raw):
    with pytest.raises(ValueError):
        parse_number(raw)


def codes(*texts, **kwargs):
    return {i.type for i in probe_chapters(dict(enumerate(texts, 1)), **kwargs).issues}


def test_quantity_money_clock_duration_and_list_spans():
    text = "预算余额五十元。下午三点半开席。补货半小时后到。四道冷菜：木耳、黄瓜、猪耳、白斩鸡。"
    facts = extract_facts(text, chapter=2)
    assert {"money", "clock", "duration", "list"} <= {f.kind for f in facts}
    assert any(f.value == Decimal(930) and f.unit == "minute_of_day" for f in facts)
    assert any(f.value == Decimal(30) and f.unit == "minute" for f in facts)
    assert all(f.chapter == 2 and text[f.start : f.end] == f.surface for f in facts)


def test_same_entity_across_chapters_and_aliases():
    assert "quantity_mismatch" in codes(
        "婚宴共十桌。", "喜宴共五十二桌。", aliases={"喜宴": "婚宴"}
    )
    assert "quantity_mismatch" in codes("预算余额为五十元。", "预算余额为五元。")


def test_unrelated_entities_and_legitimate_changes_do_not_conflict():
    assert not codes("东厅共十桌。西厅共五十二桌。")
    assert not codes("预算余额五十元。付了四十五元，预算余额变成五元。")
    assert not codes("原计划十桌。后来增至十二桌。")
    assert not codes("每桌上限三百六十元。总预算三千六百元。")
    assert not codes("预算余额约五十元。预算余额五元。")


def test_budget_total_is_not_confused_with_per_table_margin():
    assert "arithmetic_mismatch" in codes("总预算三千六百元。总计三千五百五十元。离上限还差五元。")
    assert not codes("总预算三千六百元。总计三千五百五十元。总余额五十元。每桌余五元。")


def test_itemized_sum_and_mass_conversion():
    assert "sum_mismatch" in codes("成本合计一百元：鸡六十元；鱼五十元。")
    assert not codes("成本合计一百一十元：鸡六十元；鱼五十元。")
    assert "stock_shortfall" in codes("婚宴共十桌。基围虾每桌六两。基围虾四斤够用。")
    assert not codes("婚宴共十桌。基围虾每桌六两。基围虾六斤够用。")


def test_explicit_entity_quantity_not_arbitrary_shared_unit():
    assert "quantity_mismatch" in codes("婚宴有十桌。\n\n婚宴有五十二桌。")
    assert not codes("锅里有两斤猪肉。\n\n锅里有三斤土豆。")


def test_deadline_dependency_and_existing_stock_escape():
    assert "time_dependency" in codes("鱼还需一小时后到。第一轮先上鱼，还有八分钟。")
    assert not codes("鱼还需一小时后到。第一轮只出冷盘，不等补货，还有八分钟。")
    assert not codes("鱼还需一小时后到。第一轮先上鱼，还有八十分钟。")


def test_explicit_clock_impossibility_requires_same_day_and_dependency():
    assert "time_dependency" in codes("今天下午三点开席，必须用鱼。今天下午四点鱼才到货。")
    assert not codes("今天下午三点开席。明天下午四点鱼才到货。")


def test_list_count_and_category_rounds():
    assert "list_count_mismatch" in codes("四道冷菜：木耳、黄瓜、猪耳。")
    assert "round_category_mismatch" in codes(
        "六道热荤分成四轮：先小炒肉和豆腐，再排骨和扣肉，随后鱼与虾，最后补包菜、娃娃菜和汤。"
    )
    assert not codes("四轮出菜：先冷盘，再热荤，随后素菜，最后汤。")


def test_menu_item_change_needs_explicit_alias_and_accepts_announced_change():
    assert "menu_item_mismatch" in codes(
        "菜单：番茄鸡蛋汤。",
        "番茄炒蛋先走。",
        aliases={"番茄鸡蛋汤": "番茄鸡蛋", "番茄炒蛋": "番茄鸡蛋"},
    )
    assert not codes(
        "菜单：番茄鸡蛋汤。",
        "改成番茄炒蛋先走。",
        aliases={"番茄鸡蛋汤": "番茄鸡蛋", "番茄炒蛋": "番茄鸡蛋"},
    )


def test_audit_report_roundtrips_never_blocks_and_has_traceable_evidence():
    report = probe_chapters({1: "婚宴共十桌。", 3: "婚宴共五十二桌。"})
    assert isinstance(report, CheckerReport)
    assert report.issues and not report.passed and not report.blocks_write
    assert not report.hard_violations and report.soft_suggestions == report.issues
    assert report.metrics["mode"] == "audit_only"
    assert len(report.metrics["source_sha256"]) == 2
    assert report.metrics["findings"][0]["fact_refs"]
    assert CheckerReport.from_json(report.to_json()) == report
    assert report.to_json() == probe_chapters({3: "婚宴共五十二桌。", 1: "婚宴共十桌。"}).to_json()


def test_empty_input_reports_no_coverage_not_factual_acceptance():
    report = probe_chapters({})
    assert not report.passed and not report.issues
    assert report.metrics["coverage"] == 0


def test_implicit_table_reference_requires_unique_explicit_event():
    assert "quantity_mismatch" in codes("宴会厅里的十桌宾客坐齐。等下去只会让五十二桌的菜凉掉。")
    assert not codes("东厅十桌。西厅五桌。五十二桌的菜凉掉。")


def test_contextual_budget_margin_and_colloquial_cap():
    assert "arithmetic_mismatch" in codes(
        "五千二砍到三千六。三千五百五十元，离主人给的上限还差五元。"
    )


def test_unbound_delivery_is_suspicion_not_proven_dependency():
    report = probe_chapters(
        {
            1: "新采购单尚未下单。最近两家半小时内。全部到齐，最多一个钟头。"
            "司仪催第一轮上菜，还有八分钟！"
        }
    )
    assert any(
        i.type == "time_dependency" and "依赖关系未明" in i.description for i in report.issues
    )
    assert not report.blocks_write


def test_ordinals_and_generic_payment_labels_are_not_entity_balances():
    assert not codes("第一轮结束。第二轮开始。")
    assert not codes("一笔八百元，另一笔六百元。")


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("十六点三十分。", 990),
        ("十七点零三分。", 1023),
        ("下午三点半。", 930),
    ],
)
def test_clock_grammar_does_not_consume_decimal_or_require_event_words(text, minutes):
    clocks = [f for f in extract_facts(text) if f.kind == "clock"]
    assert len(clocks) == 1 and clocks[0].value == minutes
    assert not any(f.kind == "duration" for f in extract_facts(text))


@pytest.mark.parametrize(
    ("text", "minutes"), [("一个半小时后到。", 90), ("半个小时后到。", 30), ("两个小时后到。", 120)]
)
def test_compound_durations_are_one_fact(text, minutes):
    facts = extract_facts(text)
    assert len(facts) == 1 and facts[0].kind == "duration" and facts[0].value == minutes


def test_physical_blocks_and_fractions_are_not_money_or_durations():
    assert not any(f.kind == "money" for f in extract_facts("两块碎花布。机壳鼓起一块。"))
    assert not any(
        f.kind in ("clock", "duration") for f in extract_facts("车开到三分之一。重量三点五斤。")
    )


def test_latest_full_delivery_bound_is_not_first_supplier_bound():
    report = probe_chapters(
        {
            1: "新采购单尚未下单。最近两家半小时内。全部到齐，最多一个钟头。"
            "司仪催第一轮上菜，还有八分钟！"
        }
    )
    assert any("60" in i.description for i in report.issues if i.type == "time_dependency")

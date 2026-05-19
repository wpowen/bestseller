from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.framework_self_closure import (
    build_framework_self_closure_report,
    render_framework_self_closure_markdown,
    write_framework_self_closure_artifacts,
)


pytestmark = pytest.mark.unit


def test_framework_self_closure_has_no_blocked_core_categories() -> None:
    report = build_framework_self_closure_report()
    payload = report.to_dict()
    cards = {card["category_key"]: card for card in payload["category_cards"]}

    for category in (
        "urban-contemporary",
        "science-fiction-progression",
        "wuxia-jianghu",
        "suspense-mystery",
        "otherworld-cross-system",
    ):
        assert category in cards
        assert cards[category]["status"] in {"closed", "repairable"}
        assert cards[category]["capabilities"]["taxonomy_bridge"] == "ready"
        assert cards[category]["capabilities"]["category_state_engine"] == "ready"
        assert cards[category]["capabilities"]["autonomous_repair_loop"] == "ready"
        supplement = cards[category]["distillation_supplement"]
        assert supplement["planner_must_bind"]
        assert supplement["category_state_ledgers"]
        assert supplement["repair_loop_inputs"]

    assert report.overall_status in {"closed", "repairable"}
    assert all(card["status"] != "blocked" for card in cards.values())


def test_framework_self_closure_surfaces_distillation_backlog_without_blocking() -> None:
    report = build_framework_self_closure_report(
        categories=["urban-contemporary", "science-fiction-progression", "wuxia-jianghu"]
    )
    payload = report.to_dict()

    assert payload["overall_status"] == "repairable"
    assert payload["summary"]["status_counts"]["repairable"] == 3
    assert payload["backlog"]
    assert {
        item["code"] for item in payload["backlog"]
    } == {"category_specific_distillation_missing"}
    assert all(item["priority"] == "P2" for item in payload["backlog"])
    for card in payload["category_cards"]:
        assert (
            card["distillation_supplement"]["source"]
            == "generic_book_distillation_plus_category_contract"
        )
        assert card["distillation_supplement"]["missing_category_distillation_assets"]


def test_framework_self_closure_writes_repo_safe_artifacts(tmp_path: Path) -> None:
    report = build_framework_self_closure_report(categories=["wuxia-jianghu"])
    json_path, md_path = write_framework_self_closure_artifacts(
        report,
        output_dir=tmp_path / "data",
        markdown_path=tmp_path / "report.md",
    )

    assert json_path.exists()
    assert md_path.exists()
    rendered = render_framework_self_closure_markdown(report)
    assert "小说框架全类型自闭环能力报告" in rendered
    assert "wuxia-jianghu" in rendered

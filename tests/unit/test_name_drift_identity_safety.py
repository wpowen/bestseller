from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.planner import _repair_protagonist_name_drift_for_planner

pytestmark = pytest.mark.unit


def test_name_drift_repair_does_not_rename_another_cast_member() -> None:
    """A stale protagonist ledger must not rewrite an antagonist's identity."""

    project = SimpleNamespace(
        slug="identity-drift-regression",
        language="zh-CN",
        genre="玄幻",
        metadata_json={
            "protagonist": {
                "name": "纪年",
                # The old heuristic used ``孙砚把`` here as a fake old-name
                # candidate because it was followed by the character ``把``.
                "growth_curve": "纪年持续推进，孙砚把柄簿多一页。",
            },
            "book_spec": {
                "protagonist": {
                    "name": "纪年",
                    "external_goal": "纪年让孙砚把柄簿多一页。",
                }
            },
        },
    )
    payload = {
        "protagonist": {"name": "纪年", "goal": "主角必须继续推进。"},
        "antagonist": {
            "name": "孙砚",
            "role": "antagonist",
            "name_reasoning": "孙砚以砚字为名。",
        },
        "supporting_cast": [{"name": "孙砚的属下", "role": "supporting"}],
    }

    repaired = _repair_protagonist_name_drift_for_planner(
        project,
        payload,
        protagonist_name="纪年",
        artifact_type="cast_spec",
    )

    assert repaired["antagonist"]["name"] == "孙砚"
    assert repaired["antagonist"]["name_reasoning"] == "孙砚以砚字为名。"
    assert repaired["supporting_cast"][0]["name"] == "孙砚的属下"


def test_explicit_protagonist_label_still_repairs_owned_prose() -> None:
    project = SimpleNamespace(
        slug="identity-drift-label",
        language="zh-CN",
        genre="玄幻",
        metadata_json={"protagonist": {"name": "纪年"}},
    )
    payload = {
        "protagonist": {"name": "纪年", "goal": "主角必须在三日内开张。"},
        "antagonist": {"name": "孙砚", "role": "antagonist"},
    }

    repaired = _repair_protagonist_name_drift_for_planner(
        project,
        payload,
        protagonist_name="纪年",
        artifact_type="cast_spec",
    )

    assert repaired["protagonist"]["goal"] == "纪年必须在三日内开张。"
    assert repaired["antagonist"]["name"] == "孙砚"

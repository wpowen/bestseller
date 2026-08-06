import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import story_source


@pytest.mark.asyncio
async def test_existing_project_resume_uses_latest_accepted_premise(monkeypatch) -> None:
    artifact = SimpleNamespace(content={"premise": "  已批准创意  "})
    lookup = AsyncMock(return_value=artifact)
    monkeypatch.setattr(story_source, "get_latest_planning_artifact", lookup)
    project = SimpleNamespace(
        id="project-id",
        title="标题回退",
        metadata_json={"premise": "旧元数据"},
    )

    premise = await story_source.load_accepted_project_premise(
        SimpleNamespace(), project
    )

    assert premise == "已批准创意"
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_locked_design_is_fail_safe_fallback_when_artifact_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        story_source,
        "get_latest_planning_artifact",
        AsyncMock(return_value=None),
    )
    project = SimpleNamespace(
        id="project-id",
        title="标题回退",
        metadata_json={
            "book_design_snapshot_status": "locked",
            "book_design_snapshot": {
                "reader_promise": "批准的读者承诺",
                "core_story_engine": "批准的持续引擎",
            },
        },
    )

    premise = await story_source.load_accepted_project_premise(
        SimpleNamespace(), project
    )

    assert premise == "批准的读者承诺\n持续故事引擎：批准的持续引擎"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protagonist", "premise", "forbidden", "replacement"),
    [
        (
            "苏禾",
            "苏禾是守塔学徒。每次借用星火都会灼伤经脉。她必须在冬至前点亮主塔。",
            "灼伤经脉",
            "耗尽当日星火配额",
        ),
        (
            "陆遥",
            "陆遥是边城信使。强行读取铜简会让经脉受损；他仍要赶在封城前送达证据。",
            "经脉受损",
            "耗尽本旬铜简读取次数",
        ),
    ],
)
async def test_minimal_cost_source_repair_leaves_the_approved_premise_alone(
    protagonist: str,
    premise: str,
    forbidden: str,
    replacement: str,
) -> None:
    """2026-08-02: an approved premise is no longer rewritten over cost words.

    This used to detect "灼伤经脉 / 经脉受损" in the accepted premise of a 纯爽
    book, send the clause to an LLM for replacement, and force a foundation
    replan — editing the story the user had already approved because the
    framework disliked its vocabulary.
    """
    del replacement
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={
            "story_enhancers": {"cost_style": "minimal"},
            "premise": premise,
            "conception_log": [{"premise": premise}],
        }
    )

    repaired, audit = await story_source.repair_minimal_cost_source_contract(
        SimpleNamespace(),
        SimpleNamespace(),
        project,
        premise,
        repair_revision="test-repair-v1",
    )

    assert repaired == premise
    assert forbidden in repaired
    assert repaired.startswith(f"{protagonist}是")
    assert audit["status"] == "not_required"
    assert "source_contract_repair_status" not in project.metadata_json


@pytest.mark.asyncio
async def test_minimal_cost_source_repair_is_noop_for_compliant_source() -> None:
    premise = "余烬每旬只能使用一份雷元，超额后必须等待下一旬。"
    project = SimpleNamespace(
        metadata_json={
            "story_enhancers": {"cost_style": "minimal"},
            "premise": premise,
        }
    )

    repaired, audit = await story_source.repair_minimal_cost_source_contract(
        SimpleNamespace(),
        SimpleNamespace(),
        project,
        premise,
        repair_revision="test-repair-v1",
    )

    assert repaired == premise
    assert audit["status"] == "not_required"
    assert "source_contract_repair_history" not in project.metadata_json

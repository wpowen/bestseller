from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services.emotion_driven_kernel import emotion_driven_kernel_from_dict
from bestseller.services.emotion_kernel_backfill import ensure_project_emotion_driven_kernel

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, artifacts: list[object | None] | None = None) -> None:
        self.artifacts = list(artifacts or [])

    async def scalar(self, stmt: object) -> object | None:
        if not self.artifacts:
            return None
        return self.artifacts.pop(0)


def _project(metadata: dict[str, object] | None = None) -> ProjectModel:
    project = ProjectModel(
        slug="legacy-story",
        title="旧港风暴",
        language="zh-CN",
        genre="悬疑",
        sub_genre="权谋",
        target_word_count=120000,
        target_chapters=24,
        metadata_json=metadata or {},
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_emotion_kernel_backfill_creates_metadata_kernel_for_legacy_project() -> None:
    project = _project(
        {
            "premise": "沈姝追查旧港账本，却发现救母亲和公开真相无法同时完成。",
            "book_spec": {
                "protagonist": {
                    "name": "沈姝",
                    "goal": "公开旧港账本",
                    "internal_need": "不再独自承担一切",
                    "core_wound": "害怕牵连亲人",
                },
                "central_conflict": "旧港账本会牵出港务官的伪善秩序",
            },
            "world_spec": {
                "rules": [
                    {
                        "name": "旧港账册",
                        "visible_cost": "每次公开线索都会暴露一个保护对象",
                    }
                ]
            },
            "cast_spec": {
                "protagonist": {"name": "沈姝", "goal": "公开旧港账本"},
                "antagonist": {
                    "name": "港务官",
                    "goal": "维持旧港秩序",
                    "hidden_desire": "保住自己的清官名声",
                },
            },
        }
    )

    result = await ensure_project_emotion_driven_kernel(
        FakeSession(),
        project,
        requested_by="unit-test",
    )

    assert result.status == "created"
    assert result.changed is True
    assert project.metadata_json["emotion_driven_kernel_backfill"]["source"] == (
        "legacy_backfill"
    )
    kernel = emotion_driven_kernel_from_dict(project.metadata_json["emotion_driven_kernel"])
    assert kernel.empathy_contracts[0].current_desire == "公开旧港账本"
    assert kernel.antagonist_moral_contracts[0].antagonist_key == "港务官"


@pytest.mark.asyncio
async def test_emotion_kernel_backfill_preserves_existing_valid_kernel() -> None:
    project = _project()
    created = await ensure_project_emotion_driven_kernel(
        FakeSession(),
        project,
    )
    project.metadata_json = {
        **project.metadata_json,
        "emotion_driven_kernel_backfill": {"status": "existing-marker"},
    }

    existing = await ensure_project_emotion_driven_kernel(
        FakeSession(),
        project,
    )

    assert created.status == "created"
    assert existing.status == "existing"
    assert existing.changed is False
    assert project.metadata_json["emotion_driven_kernel_backfill"]["status"] == (
        "existing-marker"
    )


@pytest.mark.asyncio
async def test_emotion_kernel_backfill_repairs_invalid_existing_kernel() -> None:
    project = _project({"emotion_driven_kernel": {"version": "bad"}})

    result = await ensure_project_emotion_driven_kernel(
        FakeSession(),
        project,
        requested_by="unit-test",
    )

    assert result.status == "repaired_invalid"
    assert result.changed is True
    emotion_driven_kernel_from_dict(project.metadata_json["emotion_driven_kernel"])


@pytest.mark.asyncio
async def test_emotion_kernel_backfill_can_read_latest_planning_artifacts() -> None:
    project = _project()
    artifacts = [
        SimpleNamespace(
            content={
                "protagonist": {"name": "陆沉", "goal": "夺回盐铁账"},
                "central_conflict": "陆沉与盐铁司的控制权冲突",
            }
        ),
        SimpleNamespace(
            content={
                "rules": [
                    {
                        "name": "盐铁账",
                        "visible_cost": "每次夺账都会失去一个旧盟友",
                    }
                ]
            }
        ),
        SimpleNamespace(
            content={
                "protagonist": {"name": "陆沉", "goal": "夺回盐铁账"},
                "antagonist": {"name": "盐铁使", "goal": "保住盐路控制"},
            }
        ),
        SimpleNamespace(content={"reader_promise": "读者等待盐铁账真相爆开"}),
        SimpleNamespace(content={"premise": "陆沉追查盐铁账。"}),
    ]

    result = await ensure_project_emotion_driven_kernel(
        FakeSession(artifacts),
        project,
    )

    kernel = emotion_driven_kernel_from_dict(result.kernel)
    assert kernel.empathy_contracts[0].current_desire == "夺回盐铁账"
    assert kernel.antagonist_moral_contracts[0].antagonist_key == "盐铁使"
    assert project.metadata_json["emotion_driven_kernel_backfill"][
        "used_artifact_fallback"
    ]["book_spec"] is True

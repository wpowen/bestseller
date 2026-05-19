from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services.compliance_boundary_kernel import compliance_boundary_kernel_from_dict
from bestseller.services.public_emotion_backfill import ensure_project_public_emotion_kernels
from bestseller.services.public_emotion_kernel import public_emotion_kernel_from_dict

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
        slug="legacy-public-emotion",
        title="旧榜夜行",
        language="zh-CN",
        genre="悬疑",
        sub_genre="规则破局",
        audience="悬疑读者",
        target_word_count=120000,
        target_chapters=24,
        metadata_json=metadata or {},
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_public_emotion_backfill_creates_metadata_kernels() -> None:
    project = _project(
        {
            "premise": "沈姝追查虚构旧榜案，必须证明旧规则失效。",
            "book_spec": {
                "title": "旧榜夜行",
                "genre": "悬疑",
                "logline": "主角用新证据推翻虚构旧榜规则。",
            },
            "target_audiences": ["被误判但想翻案的读者"],
        }
    )

    result = await ensure_project_public_emotion_kernels(
        FakeSession(),
        project,
        requested_by="unit-test",
    )

    assert result.status == "created"
    assert result.changed is True
    assert project.metadata_json["public_emotion_kernel_backfill"]["source"] == (
        "legacy_backfill"
    )
    public_kernel = public_emotion_kernel_from_dict(
        project.metadata_json["public_emotion_kernel"]
    )
    compliance_kernel = compliance_boundary_kernel_from_dict(
        project.metadata_json["compliance_boundary_kernel"]
    )
    assert public_kernel.target_segments[0].group_label == "被误判但想翻案的读者"
    assert public_kernel.emotion_bridges[0].title_hook == "旧榜夜行"
    assert compliance_kernel.policy_pack_key == "cn-mainland-general"


@pytest.mark.asyncio
async def test_public_emotion_backfill_preserves_existing_valid_kernels() -> None:
    project = _project()
    created = await ensure_project_public_emotion_kernels(FakeSession(), project)
    project.metadata_json = {
        **project.metadata_json,
        "public_emotion_kernel_backfill": {"status": "existing-marker"},
    }

    existing = await ensure_project_public_emotion_kernels(FakeSession(), project)

    assert created.status == "created"
    assert existing.status == "existing"
    assert existing.changed is False
    assert project.metadata_json["public_emotion_kernel_backfill"]["status"] == (
        "existing-marker"
    )


@pytest.mark.asyncio
async def test_public_emotion_backfill_can_read_latest_planning_artifacts() -> None:
    project = _project()
    artifacts = [
        SimpleNamespace(
            content={
                "title": "盐铁旧账",
                "genre": "权谋悬疑",
                "logline": "陆沉夺回盐铁账，推翻虚构盐路旧规。",
                "target_audiences": ["被资源规则压住的读者"],
            }
        ),
        SimpleNamespace(content={"premise": "陆沉追查盐铁账。"}),
    ]

    result = await ensure_project_public_emotion_kernels(
        FakeSession(artifacts),
        project,
    )

    public_kernel = public_emotion_kernel_from_dict(result.public_emotion_kernel)
    assert public_kernel.target_segments[0].group_label == "被资源规则压住的读者"
    assert project.metadata_json["public_emotion_kernel_backfill"][
        "used_artifact_fallback"
    ]["book_spec"] is True

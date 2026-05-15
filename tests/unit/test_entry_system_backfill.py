from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import pipelines as pipeline_services
from bestseller.services.entry_registry import entry_registry_from_dict
from bestseller.services.entry_system_backfill import (
    ensure_active_projects_entry_system_compat,
    ensure_project_entry_system_compat,
)
from bestseller.services.entry_system_kernel import entry_system_kernel_from_dict
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        artifacts: list[object | None] | None = None,
        projects: list[ProjectModel] | None = None,
    ) -> None:
        self.artifacts = list(artifacts or [])
        self.projects = list(projects or [])

    async def scalar(self, stmt: object) -> object | None:
        if not self.artifacts:
            return None
        return self.artifacts.pop(0)

    async def scalars(self, stmt: object) -> list[ProjectModel]:
        return list(self.projects)


def _project(metadata: dict[str, object] | None = None) -> ProjectModel:
    project = ProjectModel(
        slug="legacy-entry-story",
        title="玄门旧账",
        language="zh-CN",
        genre="玄幻",
        sub_genre="修仙",
        target_word_count=300000,
        target_chapters=80,
        status="writing",
        metadata_json=metadata or {},
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_entry_system_backfill_creates_kernel_and_registry_for_legacy_project() -> None:
    project = _project(
        {
            "story_design_kernel": {"reader_promise": "每次变强都要有资源账和代价。"},
            "world_spec": {
                "power_system": {
                    "name": "玄门账法",
                    "techniques": [{"name": "青木诀", "cost": "耗损灵息"}],
                    "artifacts": [{"name": "灵纹账册", "limit": "只能记录已见证之事"}],
                }
            },
        }
    )

    result = await ensure_project_entry_system_compat(
        FakeSession(),
        project,
        requested_by="unit-test",
    )

    assert result.status == "created"
    assert result.changed is True
    assert project.metadata_json["entry_system_backfill"]["source"] == "legacy_backfill"
    kernel = entry_system_kernel_from_dict(project.metadata_json["entry_system_kernel"])
    registry = entry_registry_from_dict(project.metadata_json["entry_registry"])
    assert kernel.taxonomy
    assert any(entry.name == "青木诀" for entry in registry.entries)
    assert any(entry.name == "灵纹账册" for entry in registry.entries)


@pytest.mark.asyncio
async def test_entry_system_backfill_preserves_existing_valid_package() -> None:
    project = _project()
    created = await ensure_project_entry_system_compat(FakeSession(), project)
    project.metadata_json = {
        **project.metadata_json,
        "entry_system_backfill": {"status": "existing-marker"},
    }

    existing = await ensure_project_entry_system_compat(FakeSession(), project)

    assert created.status == "created"
    assert existing.status == "existing"
    assert existing.changed is False
    assert project.metadata_json["entry_system_backfill"]["status"] == "existing-marker"


@pytest.mark.asyncio
async def test_entry_system_backfill_repairs_invalid_existing_package() -> None:
    project = _project(
        {
            "entry_system_kernel": {"taxonomy": []},
            "entry_registry": {"entries": [{"entry_id": ""}]},
        }
    )

    result = await ensure_project_entry_system_compat(
        FakeSession(),
        project,
        requested_by="unit-test",
    )

    assert result.status == "repaired_invalid"
    assert result.changed is True
    entry_system_kernel_from_dict(project.metadata_json["entry_system_kernel"])
    entry_registry_from_dict(project.metadata_json["entry_registry"])


@pytest.mark.asyncio
async def test_entry_system_backfill_can_restore_kernel_from_latest_artifact() -> None:
    seed_project = _project()
    seed = await ensure_project_entry_system_compat(FakeSession(), seed_project)
    project = _project()

    result = await ensure_project_entry_system_compat(
        FakeSession([SimpleNamespace(content=seed.kernel), None]),
        project,
        requested_by="unit-test",
    )

    assert result.status == "restored_from_artifact"
    assert result.source == "artifact"
    assert project.metadata_json["entry_system_backfill"]["source"] == "artifact"
    entry_system_kernel_from_dict(project.metadata_json["entry_system_kernel"])


@pytest.mark.asyncio
async def test_pipeline_entry_system_backfill_runs_before_writer_context() -> None:
    project = _project({"story_design_kernel": {"reader_promise": "词条必须持续兑现。"}})
    events: list[tuple[str, dict[str, object] | None]] = []

    await pipeline_services._ensure_entry_system_backfill_for_pipeline(
        FakeSession(),
        load_settings(env={}),
        project,
        requested_by="unit-test",
        progress=lambda event, payload: events.append((event, payload)),
    )

    assert "entry_system_kernel" in project.metadata_json
    assert "entry_registry" in project.metadata_json
    assert events[0][0] == "entry_system_backfilled"


@pytest.mark.asyncio
async def test_active_project_entry_system_compat_scans_non_completed_projects() -> None:
    active = _project()
    completed = _project()
    completed.status = "completed"

    summary = await ensure_active_projects_entry_system_compat(
        FakeSession(projects=[active, completed]),
        requested_by="unit-test",
    )

    assert summary.scanned == 1
    assert summary.changed == 1
    assert "entry_system_kernel" in active.metadata_json
    assert "entry_system_kernel" not in completed.metadata_json

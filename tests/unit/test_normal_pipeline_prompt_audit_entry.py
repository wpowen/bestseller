"""Actual normal/project/progressive entry audit handoffs; no provider or PG."""
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from bestseller.domain.enums import ProjectType
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import ProjectModel
from bestseller.services import llm, pipelines, fanqie_short_pipeline
from bestseller.services import creation_configuration, fanqie_short_plan_resume
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ReachedProjectContinuation(RuntimeError):
    pass


@pytest.mark.parametrize("route", ["normal", "progressive", "short"])
@pytest.mark.parametrize("existing", [True, False], ids=["resume", "new-project"])
async def test_actual_entry_binds_only_resolved_project_and_restores_scope(
    monkeypatch, route, existing,
):
    settings = load_settings(env={})
    settings.artifact_store.retain_prompt_payloads = True
    pid, misleading_story_pid, outer = uuid4(), uuid4(), uuid4()
    kind = ProjectType.FANQIE_SHORT if route == "short" else ProjectType.LINEAR
    payload = ProjectCreate(
        slug="unrelated-lighthouse-account", title="灯塔值班簿", genre="悬疑",
        target_word_count=8000, target_chapters=4, project_type=kind,
        metadata={"project_prompt_audit":str(misleading_story_pid),
                  **({"content_mode":"fanqie_short_story"} if route == "short" else {})},
    )
    project = ProjectModel(
        id=pid, slug=payload.slug, title=payload.title, genre=payload.genre,
        project_type=kind.value, metadata_json=payload.metadata,
    )
    owner = fanqie_short_pipeline if route == "short" else pipelines
    get = AsyncMock(return_value=project if existing else None)
    create = AsyncMock(return_value=project)
    monkeypatch.setattr(owner, "get_project_by_slug", get)
    monkeypatch.setattr(owner, "create_project", create)
    monkeypatch.setattr(owner, "_checkpoint_commit", AsyncMock())
    monkeypatch.setattr(pipelines, "_should_use_progressive_pipeline", lambda *_:False)
    seen = []

    def check_and_stop(*args, **kwargs):
        audit = llm._project_prompt_audit.get()
        assert audit is not None and audit[0] == pid and audit[0] != misleading_story_pid
        assert isinstance(audit[1], str) and audit[1].strip()
        assert settings.artifact_store.retain_prompt_payloads is True
        seen.append(audit)
        raise ReachedProjectContinuation()

    async def async_check(*args, **kwargs):
        return check_and_stop(*args, **kwargs)

    if route == "normal":
        monkeypatch.setattr(pipelines, "_clear_auto_resumable_project_pause", async_check)
        entry = pipelines.run_autowrite_pipeline
    elif route == "progressive":
        monkeypatch.setattr(creation_configuration, "project_settings", check_and_stop)
        entry = pipelines.run_progressive_autowrite_pipeline
    else:
        monkeypatch.setattr(fanqie_short_plan_resume,
                            "require_or_read_existing_complete_fanqie_plan", async_check)
        entry = fanqie_short_pipeline.run_fanqie_short_pipeline
    with llm.bind_project_prompt_audit(outer, reason="Separate outer operation"):
        before = llm._project_prompt_audit.get()
        with pytest.raises(ReachedProjectContinuation):
            await entry(object(), settings, project_payload=payload, premise="值班记录的来源核对")
        assert llm._project_prompt_audit.get() == before
    assert llm._project_prompt_audit.get() is None
    assert len(seen) == 1 and create.await_count == int(not existing)
    assert get.await_count == 1

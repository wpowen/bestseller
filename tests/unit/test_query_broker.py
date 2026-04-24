from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services import query_broker
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class DummySession:
    pass


def build_settings():
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=100000,
        target_chapters=30,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_context(project: ProjectModel) -> SceneWriterContextPacket:
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=1,
        title="第一章",
        chapter_goal="推进主线",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="setup",
        title="开场",
        participants=["沈砚", "李渡"],
        purpose={"story": "抛出冲突", "emotion": "压迫感"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return SceneWriterContextPacket(
        project_id=project.id,
        project_slug=project.slug,
        chapter_id=chapter.id,
        scene_id=scene.id,
        chapter_number=1,
        scene_number=1,
        query_text="推进主线 开场",
        story_bible={},
        contradiction_warnings=["角色身份别写错"],
    )


@pytest.mark.asyncio
async def test_run_scene_query_brief_serializes_tool_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_tool_loop(*args, **kwargs):
        return SimpleNamespace(
            final_content="【写前补充简报】\n- 硬事实：沈砚不能提前知道真相",
            final_tool_results={},
            rounds=2,
            exit_reason="text",
            trace=[
                SimpleNamespace(
                    round_index=1,
                    tool_name="query_character_truth",
                    arguments={"names": ["沈砚"]},
                    result={"characters": [{"name": "沈砚"}]},
                    error=None,
                )
            ],
        )

    monkeypatch.setattr(query_broker, "run_tool_loop", fake_run_tool_loop)
    project = build_project()
    context = build_context(project)

    result = await query_broker.run_scene_query_brief(
        DummySession(),  # type: ignore[arg-type]
        build_settings(),
        project=project,
        chapter_number=1,
        scene_number=1,
        scene_title="开场",
        scene_type="setup",
        participants=["沈砚", "李渡"],
        story_purpose="抛出冲突",
        emotion_purpose="压迫感",
        context_packet=context,
    )

    assert "写前补充简报" in result["brief"]
    assert result["rounds"] == 2
    assert result["trace"][0]["tool_name"] == "query_character_truth"


@pytest.mark.asyncio
async def test_run_scene_query_brief_falls_back_to_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_tool_loop(*args, **kwargs):
        return SimpleNamespace(
            final_content="",
            final_tool_results={
                "query_clue_status": {
                    "clues": [{"clue_code": "blood-lotus"}],
                }
            },
            rounds=1,
            exit_reason="max_rounds",
            trace=[],
        )

    monkeypatch.setattr(query_broker, "run_tool_loop", fake_run_tool_loop)
    project = build_project()

    result = await query_broker.run_scene_query_brief(
        DummySession(),  # type: ignore[arg-type]
        build_settings(),
        project=project,
        chapter_number=1,
        scene_number=1,
        scene_title="开场",
        scene_type="setup",
        participants=["沈砚"],
        story_purpose="抛出冲突",
        emotion_purpose="压迫感",
        context_packet=None,
    )

    assert "blood-lotus" in result["brief"]

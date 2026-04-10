from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import CanonFactModel, ProjectModel, SceneCardModel, SceneDraftVersionModel, StyleGuideModel, TimelineEventModel
from bestseller.services import knowledge as knowledge_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        get_map: dict[object, object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        return []

    async def get(self, model: object, key: object) -> object | None:
        return self.get_map.get((model, key))

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)


def build_settings():
    return load_settings(env={})


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="sci-fi",
        target_word_count=90000,
        target_chapters=18,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_english_project() -> ProjectModel:
    project = ProjectModel(
        slug="storm-ledger",
        title="Storm Ledger",
        genre="Fantasy",
        language="en-US",
        target_word_count=90000,
        target_chapters=18,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_chapter(project_id):
    from bestseller.infra.db.models import ChapterModel

    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=1,
        title="封港",
        chapter_goal="推进主线",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def build_scene(project_id, chapter_id) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=1,
        scene_type="setup",
        title="封港命令",
        time_label="深夜",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感和抗拒"},
        entry_state={},
        exit_state={"沈砚": {"stance": "被迫接单"}, "港务官": {"stance": "强制执行"}},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return scene


def build_draft(project_id, scene_id) -> SceneDraftVersionModel:
    draft = SceneDraftVersionModel(
        project_id=project_id,
        scene_card_id=scene_id,
        version_no=1,
        content_md="## 场景 1：封港命令\n\n沈砚与港务官正面对峙。",
        word_count=520,
        is_current=True,
        generation_params={},
    )
    draft.id = uuid4()
    return draft


def build_style(project_id) -> StyleGuideModel:
    return StyleGuideModel(
        project_id=project_id,
        pov_type="third-limited",
        tense="present",
        tone_keywords=["冷峻", "压迫"],
        prose_style="baseline",
        sentence_style="mixed",
        info_density="medium",
        dialogue_ratio=0.35,
        taboo_words=[],
        taboo_topics=[],
        reference_works=[],
        custom_rules=[],
    )


def test_scene_summary_prompts_switch_to_english_for_english_projects() -> None:
    project = build_english_project()
    chapter = build_chapter(project.id)
    chapter.title = "Storm Wake"
    chapter.chapter_goal = "Force Elara to move first"
    scene = build_scene(project.id, chapter.id)
    scene.title = "The Order Arrives"
    scene.participants = ["Elara", "Captain Vale"]
    scene.purpose = {"story": "Trigger the execution order", "emotion": "panic turning into resolve"}
    draft = build_draft(project.id, scene.id)
    draft.content_md = "The execution order arrived before dawn."
    style = build_style(project.id)

    system_prompt, user_prompt = knowledge_services.build_scene_summary_prompts(
        project,
        chapter,
        scene,
        draft,
        style,
    )
    fallback = knowledge_services.render_scene_summary_fallback(project, chapter, scene)

    combined = system_prompt + "\n" + user_prompt + "\n" + fallback
    assert "Write a concise 2-3 sentence English summary" in system_prompt
    assert "Project: Storm Ledger" in user_prompt
    assert "Scene 1: The Order Arrives" in user_prompt
    assert "In Storm Ledger, Chapter 1, Scene 1" in fallback
    assert "请用中文输出" not in combined
    assert "《Storm Ledger》" not in combined


@pytest.mark.asyncio
async def test_refresh_scene_knowledge_creates_canon_and_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    draft = build_draft(project.id, scene.id)
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(knowledge_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[
            chapter,
            scene,
            draft,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
        get_map={(StyleGuideModel, project.id): style},
    )

    result = await knowledge_services.refresh_scene_knowledge(
        session,
        build_settings(),
        "my-story",
        1,
        1,
    )

    canon_facts = [obj for obj in session.added if isinstance(obj, CanonFactModel)]
    timeline_events = [obj for obj in session.added if isinstance(obj, TimelineEventModel)]

    assert result.canon_facts_created == 6
    assert result.timeline_events_created == 1
    assert result.summary_text
    assert len(canon_facts) == 6
    assert len(timeline_events) == 1


@pytest.mark.asyncio
async def test_list_functions_filter_project_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fact = CanonFactModel(
        project_id=project.id,
        subject_type="character",
        subject_id=uuid4(),
        subject_label="沈砚",
        predicate="last_known_state",
        fact_type="state",
        value_json={"stance": "被迫接单"},
        valid_from_chapter_no=1,
        tags=[],
    )
    fact.id = uuid4()
    event = TimelineEventModel(
        project_id=project.id,
        chapter_id=uuid4(),
        scene_card_id=uuid4(),
        event_name="封港命令",
        event_type="setup",
        story_time_label="深夜",
        story_order=1.01,
        participant_ids=["沈砚"],
        consequences=["抛出禁令任务"],
        metadata_json={},
    )
    event.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(knowledge_services, "get_project_by_slug", fake_get_project_by_slug)

    class ListSession(FakeSession):
        async def scalars(self, stmt: object):
            table_names = {table.name for table in stmt.get_final_froms()}
            if "canon_facts" in table_names:
                return [fact]
            return [event]

    session = ListSession()
    facts = await knowledge_services.list_canon_facts(session, "my-story")
    events = await knowledge_services.list_timeline_events(session, "my-story")

    assert facts[0].subject_label == "沈砚"
    assert events[0].event_name == "封港命令"

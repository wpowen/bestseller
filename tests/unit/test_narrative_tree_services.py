from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterContractModel,
    ChapterModel,
    CharacterModel,
    ClueModel,
    NarrativeTreeNodeModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    SceneCardModel,
    SceneContractModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services import narrative_tree as narrative_tree_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is not None and "id" in table.c and getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        metadata_json={
            "logline": "被放逐的导航员调查被篡改的航线。",
            "themes": ["真相", "牺牲"],
            "world_name": "边境星区",
            "world_premise": "帝国正在篡改边境航线。",
        },
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_rebuild_narrative_tree_exports_deterministic_paths() -> None:
    project = build_project()
    volume = VolumeModel(
        project_id=project.id,
        volume_number=1,
        title="边境疑云",
        goal="拿到第一份铁证",
        obstacle="封锁升级",
        metadata_json={"reader_hook_to_next": "更高层操盘者开始清场"},
    )
    volume.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        volume_id=volume.id,
        chapter_number=1,
        title="封港命令",
        chapter_goal="推进主线调查",
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
        title="异常航标",
        participants=["沈砚"],
        purpose={"story": "抛出异常航标", "emotion": "警觉"},
        entry_state={"risk": "高"},
        exit_state={"risk": "更高"},
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    world_rule = WorldRuleModel(
        project_id=project.id,
        rule_code="R001",
        name="记录优先",
        description="官方记录高于个人证词。",
        story_consequence="主角无法直接翻案。",
        metadata_json={},
    )
    world_rule.id = uuid4()
    character = CharacterModel(
        project_id=project.id,
        name="沈砚",
        role="protagonist",
        goal="找证据",
        arc_state="开场",
        knowledge_state_json={},
        metadata_json={},
    )
    character.id = uuid4()
    arc = PlotArcModel(
        project_id=project.id,
        arc_code="main_plot",
        name="主线推进",
        arc_type="main_plot",
        promise="调查被篡改的航线。",
        core_question="主角能否揭开真相？",
        status="planned",
        scope_level="project",
        metadata_json={},
    )
    arc.id = uuid4()
    clue = ClueModel(
        project_id=project.id,
        plot_arc_id=arc.id,
        clue_code="clue-001",
        label="异常航标",
        clue_type="foreshadow",
        description="异常航标暗示有人留下了信息。",
        planted_in_chapter_number=1,
        planted_in_scene_number=1,
        status="planted",
        metadata_json={},
    )
    clue.id = uuid4()
    payoff = PayoffModel(
        project_id=project.id,
        plot_arc_id=arc.id,
        source_clue_id=clue.id,
        payoff_code="payoff-001",
        label="求救信号兑现",
        description="求救信号指向底层日志库入口。",
        target_chapter_number=2,
        status="planned",
        metadata_json={},
    )
    payoff.id = uuid4()
    chapter_contract = ChapterContractModel(
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        contract_summary="本章要抛出主线异常。",
        opening_state={"risk": "高"},
        primary_arc_codes=["main_plot"],
        supporting_arc_codes=[],
        active_arc_beat_ids=[],
        planted_clue_codes=["clue-001"],
        due_payoff_codes=[],
        metadata_json={},
    )
    chapter_contract.id = uuid4()
    scene_contract = SceneContractModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_card_id=scene.id,
        chapter_number=1,
        scene_number=1,
        contract_summary="本场必须抛出异常航标。",
        entry_state={"risk": "高"},
        exit_state={"risk": "更高"},
        arc_codes=["main_plot"],
        arc_beat_ids=[],
        planted_clue_codes=["clue-001"],
        payoff_codes=[],
        metadata_json={},
    )
    scene_contract.id = uuid4()

    session = FakeSession(
        scalars_results=[
            [volume],
            [chapter],
            [scene],
            [world_rule],
            [],
            [],
            [character],
            [arc],
            [clue],
            [payoff],
            [chapter_contract],
            [scene_contract],
        ]
    )

    counts = await narrative_tree_services.rebuild_narrative_tree(session, project=project)

    node_paths = {
        item.node_path
        for item in session.added
        if isinstance(item, NarrativeTreeNodeModel)
    }
    assert counts["node_count"] >= 12
    assert "/book/premise" in node_paths
    assert "/world/rules/r001" in node_paths
    assert "/characters/沈砚" in node_paths
    assert "/arcs/main-plot" in node_paths
    assert "/chapters/001/contract" in node_paths
    assert "/scenes/001-01/contract" in node_paths


@pytest.mark.asyncio
async def test_resolve_and_search_narrative_tree_respect_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    visible_node = NarrativeTreeNodeModel(
        project_id=project.id,
        node_path="/chapters/001/contract",
        parent_path="/chapters/001",
        depth=3,
        node_type="chapter_contract",
        title="第1章 contract",
        summary="本章需要推进主线。",
        body_md="# 第1章 contract\n- summary：本章需要推进主线。",
        source_type="chapter_contract",
        source_ref_id=uuid4(),
        scope_level="chapter",
        scope_chapter_number=1,
        lexical_document="chapter contract 主线 推进",
        metadata_json={},
    )
    visible_node.id = uuid4()
    future_node = NarrativeTreeNodeModel(
        project_id=project.id,
        node_path="/chapters/003",
        parent_path="/chapters",
        depth=2,
        node_type="chapter",
        title="第3章",
        summary="未来章节。",
        body_md="# 第3章\n- goal：未来章节",
        source_type="chapter",
        source_ref_id=uuid4(),
        scope_level="chapter",
        scope_chapter_number=3,
        lexical_document="future chapter 未来章节",
        metadata_json={},
    )
    future_node.id = uuid4()

    async def fake_ensure(session, project_obj):
        return 0

    monkeypatch.setattr(narrative_tree_services, "ensure_project_narrative_tree", fake_ensure)

    resolve_session = FakeSession(scalars_results=[[visible_node, future_node]])
    resolved = await narrative_tree_services.resolve_narrative_tree_paths_for_project(
        resolve_session,
        project,
        ["/chapters/001/contract", "/chapters/003"],
        current_chapter_number=1,
        current_scene_number=1,
    )
    assert [item.node_path for item in resolved] == ["/chapters/001/contract"]

    search_session = FakeSession(scalars_results=[[visible_node, future_node]])
    result = await narrative_tree_services.search_narrative_tree_for_project(
        search_session,
        project,
        "主线 推进",
        preferred_paths=["/chapters/001"],
        current_chapter_number=1,
        current_scene_number=1,
        top_k=5,
    )

    assert len(result.hits) == 1
    assert result.hits[0].node_path == "/chapters/001/contract"


@pytest.mark.asyncio
async def test_get_narrative_tree_node_by_path_returns_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    node = NarrativeTreeNodeModel(
        project_id=project.id,
        node_path="/book/premise",
        parent_path="/book",
        depth=2,
        node_type="premise",
        title="作品 premise",
        summary="被放逐的导航员调查被篡改的航线。",
        body_md="# 作品 premise",
        source_type="project",
        source_ref_id=project.id,
        scope_level="project",
        lexical_document="premise 调查 航线",
        metadata_json={},
    )
    node.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_ensure(session, project_obj):
        return 0

    monkeypatch.setattr(narrative_tree_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(narrative_tree_services, "ensure_project_narrative_tree", fake_ensure)

    session = FakeSession(scalar_results=[node])
    result = await narrative_tree_services.get_narrative_tree_node_by_path(
        session,
        "my-story",
        "/book/premise",
    )

    assert result is not None
    assert result.node_path == "/book/premise"

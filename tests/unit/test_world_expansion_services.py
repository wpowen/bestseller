from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    CharacterModel,
    ChapterModel,
    DeferredRevealModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    PlotArcModel,
    ProjectModel,
    VolumeFrontierModel,
    VolumeModel,
    WorldBackboneModel,
    WorldRuleModel,
)
from bestseller.services import world_expansion as world_expansion_services


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
        target_word_count=120000,
        target_chapters=60,
        metadata_json={
            "logline": "被放逐的导航员调查被篡改的航线。",
            "themes": ["真相", "牺牲"],
            "world_name": "边境星门航道",
            "world_premise": "航道记录决定一切。",
            "power_structure": "帝国控制解释权",
            "forbidden_zones": "日志库",
            "series_engine": {"hook_style": "章末升级风险"},
        },
    )
    project.id = uuid4()
    return project


def build_volume(project_id, volume_number: int, title: str, *, chapter_count: int, key_reveal: str) -> VolumeModel:
    volume = VolumeModel(
        project_id=project_id,
        volume_number=volume_number,
        title=title,
        theme="面对真相" if volume_number == 1 else "扩大冲突",
        goal="拿到铁证" if volume_number == 1 else "追上幕后层级",
        obstacle="封港追捕" if volume_number == 1 else "更高层灭口",
        target_chapter_count=chapter_count,
        target_word_count=chapter_count * 5500,
        metadata_json={
            "opening_state": {"world_situation": "边境封锁升级"},
            "key_reveals": [key_reveal],
            "foreshadowing_planted": ["更高层有人在清场"],
            "foreshadowing_paid_off": [],
            "reader_hook_to_next": "主角发现还有第二层操盘者。",
        },
    )
    volume.id = uuid4()
    return volume


def build_chapter(project_id, volume_id, chapter_number: int) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        volume_id=volume_id,
        chapter_number=chapter_number,
        chapter_goal="推进主线",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=5500,
    )
    chapter.id = uuid4()
    return chapter


@pytest.mark.asyncio
async def test_refresh_world_expansion_boundaries_creates_backbone_frontiers_and_gates() -> None:
    project = build_project()
    volume1 = build_volume(project.id, 1, "失准航线", chapter_count=20, key_reveal="签名链被盗用")
    volume2 = build_volume(project.id, 2, "静默航道", chapter_count=20, key_reveal="幕后操盘者并非航道署")
    chapter1 = build_chapter(project.id, volume1.id, 1)
    chapter20 = build_chapter(project.id, volume1.id, 20)
    rule = WorldRuleModel(
        project_id=project.id,
        rule_code="R001",
        name="航道记录优先",
        description="官方航图高于证词。",
        metadata_json={},
    )
    location = LocationModel(
        project_id=project.id,
        name="碎潮星港",
        location_type="星港",
        key_rule_codes=["R001"],
        metadata_json={},
    )
    faction = FactionModel(
        project_id=project.id,
        name="帝国航道署",
        metadata_json={},
    )
    protagonist = CharacterModel(
        project_id=project.id,
        name="沈砚",
        role="protagonist",
        goal="找到账目证据",
        arc_trajectory="从控制到协作",
        knowledge_state_json={},
        metadata_json={},
    )
    antagonist = CharacterModel(
        project_id=project.id,
        name="祁镇",
        role="antagonist",
        goal="删光旧记录",
        knowledge_state_json={},
        metadata_json={},
    )
    arc = PlotArcModel(
        project_id=project.id,
        arc_code="main_plot",
        name="主线推进",
        arc_type="main_plot",
        promise="调查被篡改的航线。",
        core_question="主角能否撬开真相？",
        scope_level="project",
        metadata_json={},
    )

    session = FakeSession(
        scalars_results=[
            [volume1, volume2],
            [chapter1, chapter20],
            [rule],
            [location],
            [faction],
            [arc],
            [protagonist, antagonist],
        ]
    )

    counts = await world_expansion_services.refresh_world_expansion_boundaries(session, project=project)

    assert counts["world_backbones_upserted"] == 1
    assert counts["volume_frontiers_upserted"] == 2
    assert counts["deferred_reveals_upserted"] == 2
    assert counts["expansion_gates_upserted"] == 2

    backbone = next(item for item in session.added if isinstance(item, WorldBackboneModel))
    frontiers = [item for item in session.added if isinstance(item, VolumeFrontierModel)]
    reveals = [item for item in session.added if isinstance(item, DeferredRevealModel)]
    gates = [item for item in session.added if isinstance(item, ExpansionGateModel)]

    assert backbone.mainline_drive == "拿到铁证"
    assert "航道记录优先" in backbone.invariant_elements
    assert frontiers[0].start_chapter_number == 1
    assert frontiers[0].future_reveal_codes == ["volume-02-reveal-01"]
    assert reveals[1].reveal_volume_number == 2
    assert gates[1].unlock_volume_number == 2


def test_estimate_volume_chapter_ranges_prefers_actual_chapter_numbers() -> None:
    project = build_project()
    volume1 = build_volume(project.id, 1, "失准航线", chapter_count=10, key_reveal="A")
    volume2 = build_volume(project.id, 2, "静默航道", chapter_count=10, key_reveal="B")
    chapter3 = build_chapter(project.id, volume1.id, 3)
    chapter8 = build_chapter(project.id, volume1.id, 8)

    ranges = world_expansion_services.estimate_volume_chapter_ranges(
        project,
        [volume1, volume2],
        [chapter3, chapter8],
    )

    assert ranges[1] == (3, 8)
    assert ranges[2] == (9, 18)


@pytest.mark.asyncio
async def test_load_world_expansion_context_returns_boundary_payload() -> None:
    project = build_project()
    backbone = WorldBackboneModel(
        project_id=project.id,
        title="全书世界主干",
        core_promise="被放逐的导航员调查被篡改的航线。",
        mainline_drive="先在边境找到铁证，再撬出幕后层级。",
        invariant_elements=["航道记录优先"],
        stable_unknowns=["更高层操盘者身份"],
        metadata_json={},
    )
    frontier = VolumeFrontierModel(
        project_id=project.id,
        volume_number=1,
        title="失准航线",
        frontier_summary="当前只展开边境星港与航道署。",
        start_chapter_number=1,
        end_chapter_number=20,
        visible_rule_codes=["R001"],
        active_locations=["碎潮星港"],
        active_factions=["帝国航道署"],
        active_arc_codes=["main_plot"],
        future_reveal_codes=["volume-02-reveal-01"],
        metadata_json={},
    )
    gate = ExpansionGateModel(
        project_id=project.id,
        gate_code="unlock-volume-02",
        label="第2卷世界扩张闸门",
        gate_type="world_expansion",
        condition_summary="完成第一份铁证并承受封港代价。",
        unlocks_summary="展开更高层航道势力。",
        unlock_volume_number=2,
        unlock_chapter_number=21,
        status="planned",
        metadata_json={},
    )
    session = FakeSession(scalar_results=[backbone, frontier, 3, gate])

    payload = await world_expansion_services.load_world_expansion_context(
        session,
        project=project,
        volume_number=1,
        chapter_number=5,
    )

    assert payload["world_backbone"]["mainline_drive"] == "先在边境找到铁证，再撬出幕后层级。"
    assert payload["volume_frontier"]["active_locations"] == ["碎潮星港"]
    assert payload["deferred_reveal_status"]["hidden_count"] == 3
    assert payload["next_expansion_gate"]["unlock_volume_number"] == 2


@pytest.mark.asyncio
async def test_load_world_expansion_context_tolerates_legacy_scalar_sequences() -> None:
    project = build_project()
    session = FakeSession(scalar_results=[0])

    payload = await world_expansion_services.load_world_expansion_context(
        session,
        project=project,
        volume_number=1,
        chapter_number=1,
    )

    assert payload["world_backbone"] == {}
    assert payload["volume_frontier"] == {}
    assert payload["deferred_reveal_status"]["hidden_count"] == 0


@pytest.mark.asyncio
async def test_sync_world_expansion_progress_updates_project_and_gate_statuses() -> None:
    project = build_project()
    project.current_chapter_number = 24
    project.current_volume_number = 1
    frontier1 = VolumeFrontierModel(
        project_id=project.id,
        volume_number=1,
        title="失准航线",
        frontier_summary="第一卷边界",
        start_chapter_number=1,
        end_chapter_number=20,
        visible_rule_codes=[],
        active_locations=[],
        active_factions=[],
        active_arc_codes=[],
        future_reveal_codes=[],
        metadata_json={},
    )
    frontier2 = VolumeFrontierModel(
        project_id=project.id,
        volume_number=2,
        title="静默航道",
        frontier_summary="第二卷边界",
        start_chapter_number=21,
        end_chapter_number=40,
        visible_rule_codes=[],
        active_locations=[],
        active_factions=[],
        active_arc_codes=[],
        future_reveal_codes=[],
        metadata_json={},
    )
    gate1 = ExpansionGateModel(
        project_id=project.id,
        gate_code="unlock-volume-01",
        label="第1卷世界扩张闸门",
        gate_type="world_expansion",
        condition_summary="完成开篇建场",
        unlocks_summary="展开第1卷",
        unlock_volume_number=1,
        unlock_chapter_number=1,
        status="planned",
        metadata_json={},
    )
    gate2 = ExpansionGateModel(
        project_id=project.id,
        gate_code="unlock-volume-02",
        label="第2卷世界扩张闸门",
        gate_type="world_expansion",
        condition_summary="拿到第一份铁证",
        unlocks_summary="展开第2卷",
        unlock_volume_number=2,
        unlock_chapter_number=21,
        status="planned",
        metadata_json={},
    )
    gate3 = ExpansionGateModel(
        project_id=project.id,
        gate_code="unlock-volume-03",
        label="第3卷世界扩张闸门",
        gate_type="world_expansion",
        condition_summary="进入第二层势力",
        unlocks_summary="展开第3卷",
        unlock_volume_number=3,
        unlock_chapter_number=41,
        status="planned",
        metadata_json={},
    )
    session = FakeSession(
        scalars_results=[
            [frontier1, frontier2],
            [gate1, gate2, gate3],
        ]
    )

    payload = await world_expansion_services.sync_world_expansion_progress(session, project=project)

    assert project.current_volume_number == 2
    assert gate1.status == "unlocked"
    assert gate2.status == "unlocked"
    assert gate3.status == "active"
    assert frontier2.metadata_json["progress_state"] == "current"
    assert payload["unlocked_gate_count"] == 2

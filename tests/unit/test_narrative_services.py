from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ArcBeatModel,
    ChapterContractModel,
    ChapterModel,
    CharacterModel,
    ClueModel,
    EmotionTrackModel,
    EndingContractModel,
    MotifPlacementModel,
    PacingCurvePointModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    ReaderKnowledgeEntryModel,
    RelationshipEventModel,
    RelationshipModel,
    SceneCardModel,
    SceneContractModel,
    SubplotScheduleModel,
    ThemeArcModel,
    VolumeModel,
)
from bestseller.services import narrative as narrative_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, *, scalars_results: list[list[object]] | None = None) -> None:
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
        },
    )
    project.id = uuid4()
    return project


def build_volume(project_id, volume_number: int) -> VolumeModel:
    volume = VolumeModel(
        project_id=project_id,
        volume_number=volume_number,
        title=f"第{volume_number}卷",
        goal="找到铁证",
        obstacle="封锁升级",
        metadata_json={},
    )
    volume.id = uuid4()
    return volume


def build_chapter(project_id, volume_id, chapter_number: int, title: str) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        volume_id=volume_id,
        chapter_number=chapter_number,
        title=title,
        chapter_goal="推进调查",
        opening_situation="主角被迫接手高风险任务。",
        main_conflict="主角必须在封锁前拿到证据。",
        hook_description="新的异常坐标浮现。",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        chapter_emotion_arc="从戒备到主动出击",
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def build_scene(project_id, chapter_id, scene_number: int, title: str, participants: list[str]) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=scene_number,
        scene_type="setup",
        title=title,
        participants=participants,
        purpose={"story": "推进主线调查", "emotion": "紧绷"},
        entry_state={"risk": "高"},
        exit_state={"risk": "更高"},
        hook_requirement="结尾必须抛出更大风险。",
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return scene


def build_character(project_id, name: str, role: str) -> CharacterModel:
    character = CharacterModel(
        project_id=project_id,
        name=name,
        role=role,
        goal="揭开真相" if role == "protagonist" else "压制主角",
        secret="幕后另有更高层操盘者",
        arc_trajectory="从单打独斗到建立同盟" if role == "protagonist" else "从幕后操盘到公开下场",
        arc_state="开场",
        knowledge_state_json={},
        metadata_json={},
    )
    character.id = uuid4()
    return character


def build_relationship(
    project_id,
    character_a_id,
    character_b_id,
    relationship_type: str,
    *,
    strength: float = 0.6,
) -> RelationshipModel:
    relationship = RelationshipModel(
        project_id=project_id,
        character_a_id=character_a_id,
        character_b_id=character_b_id,
        relationship_type=relationship_type,
        strength=strength,
        public_face="旧搭档",
        private_reality="双方都还保留未说出口的旧账。",
        tension_summary="信任还没有恢复，但被迫继续合作。",
        established_chapter_no=1,
        last_changed_chapter_no=1,
        metadata_json={},
    )
    relationship.id = uuid4()
    return relationship


@pytest.mark.asyncio
async def test_rebuild_narrative_graph_creates_arcs_beats_clues_and_contracts() -> None:
    project = build_project()
    volume = build_volume(project.id, 1)
    chapter1 = build_chapter(project.id, volume.id, 1, "封港命令")
    chapter2 = build_chapter(project.id, volume.id, 2, "静默航道")
    scene1 = build_scene(project.id, chapter1.id, 1, "异常航标", ["沈砚"])
    scene2 = build_scene(project.id, chapter2.id, 1, "旧搭档回舰", ["沈砚", "顾临"])
    protagonist = build_character(project.id, "沈砚", "protagonist")
    antagonist = build_character(project.id, "顾临", "antagonist")
    relationship = build_relationship(project.id, protagonist.id, antagonist.id, "旧搭档")

    session = FakeSession(
        scalars_results=[
            [chapter1, chapter2],
            [volume],
            [scene1, scene2],
            [protagonist, antagonist],
            [relationship],
        ]
    )

    counts = await narrative_services.rebuild_narrative_graph(
        session,
        project=project,
        volume_plan_content=[
            {
                "volume_number": 1,
                "title": "边境疑云",
                "volume_goal": "拿到第一份铁证",
                "volume_obstacle": "封港和追杀同步升级",
                "volume_climax": "主角抢到关键底层记录",
                "key_reveals": ["帝国正在系统性篡改航线记录"],
                "foreshadowing_planted": ["异常航标其实是人为留下的求救信号"],
                "foreshadowing_paid_off": ["求救信号指向真正的底层日志库入口"],
                "reader_hook_to_next": "更高层操盘者开始清场",
            }
        ],
    )

    assert counts["plot_arc_count"] >= 3
    assert counts["arc_beat_count"] >= 4
    assert counts["clue_count"] >= 1
    assert counts["payoff_count"] >= 1
    assert counts["chapter_contract_count"] == 2
    assert counts["scene_contract_count"] == 2
    assert counts["emotion_track_count"] >= 1
    assert counts["antagonist_plan_count"] >= 1
    # New narrative depth counts
    assert counts["theme_arc_count"] >= 1
    assert counts["motif_placement_count"] >= 1
    assert counts["subplot_schedule_count"] >= 0
    assert counts["relationship_event_count"] >= 1
    assert counts["reader_knowledge_count"] >= 1
    assert counts["ending_contract_count"] == 1
    assert counts["pacing_curve_point_count"] == 2  # 2 chapters
    # Verify model types are added
    assert any(isinstance(obj, PlotArcModel) for obj in session.added)
    assert any(isinstance(obj, ArcBeatModel) for obj in session.added)
    assert any(isinstance(obj, ClueModel) for obj in session.added)
    assert any(isinstance(obj, PayoffModel) for obj in session.added)
    assert any(isinstance(obj, ChapterContractModel) for obj in session.added)
    assert any(isinstance(obj, SceneContractModel) for obj in session.added)
    assert any(isinstance(obj, EmotionTrackModel) for obj in session.added)
    assert any(isinstance(obj, AntagonistPlanModel) for obj in session.added)
    assert any(isinstance(obj, ThemeArcModel) for obj in session.added)
    assert any(isinstance(obj, EndingContractModel) for obj in session.added)
    assert any(isinstance(obj, PacingCurvePointModel) for obj in session.added)
    assert any(isinstance(obj, RelationshipEventModel) for obj in session.added)
    # 15 model types deleted in cleanup
    assert len(session.executed) == 15


@pytest.mark.asyncio
async def test_build_narrative_overview_renders_materialized_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
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
    beat = ArcBeatModel(
        project_id=project.id,
        plot_arc_id=arc.id,
        beat_order=1,
        scope_level="chapter",
        scope_chapter_number=1,
        beat_kind="chapter_push",
        summary="第1章承担主线推进。",
        status="planned",
        metadata_json={"arc_code": "main_plot"},
    )
    beat.id = uuid4()
    clue = ClueModel(
        project_id=project.id,
        plot_arc_id=arc.id,
        clue_code="clue-001",
        label="异常航标",
        clue_type="foreshadow",
        description="异常航标暗示有人留下了信息。",
        planted_in_chapter_number=1,
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
        actual_chapter_number=2,
        status="paid_off",
        metadata_json={},
    )
    payoff.id = uuid4()
    chapter_contract = ChapterContractModel(
        project_id=project.id,
        chapter_id=uuid4(),
        chapter_number=1,
        contract_summary="本章要抛出主线异常。",
        opening_state={"risk": "高"},
        primary_arc_codes=["main_plot"],
        supporting_arc_codes=[],
        active_arc_beat_ids=[str(beat.id)],
        planted_clue_codes=["clue-001"],
        due_payoff_codes=[],
        metadata_json={},
    )
    chapter_contract.id = uuid4()
    scene_contract = SceneContractModel(
        project_id=project.id,
        chapter_id=chapter_contract.chapter_id,
        scene_card_id=uuid4(),
        chapter_number=1,
        scene_number=1,
        contract_summary="本场必须抛出异常航标。",
        entry_state={"risk": "高"},
        exit_state={"risk": "更高"},
        arc_codes=["main_plot"],
        arc_beat_ids=[str(beat.id)],
        planted_clue_codes=["clue-001"],
        payoff_codes=[],
        metadata_json={},
    )
    scene_contract.id = uuid4()
    emotion_track = EmotionTrackModel(
        project_id=project.id,
        track_code="bond-shenyan-gulin",
        track_type="bond",
        title="沈砚 / 顾临 关系线",
        character_a_label="沈砚",
        character_b_label="顾临",
        relationship_type="旧搭档",
        summary="两人的信任尚未恢复。",
        desired_payoff="在高潮前恢复最低限度的联手。",
        trust_level=0.45,
        attraction_level=0.1,
        distance_level=0.62,
        conflict_level=0.66,
        intimacy_stage="push_pull",
        status="active",
        metadata_json={},
    )
    emotion_track.id = uuid4()
    antagonist_plan = AntagonistPlanModel(
        project_id=project.id,
        antagonist_character_id=uuid4(),
        antagonist_label="顾临",
        plan_code="volume-01-pressure",
        title="第1卷反派升级",
        threat_type="volume_pressure",
        goal="持续封锁主角调查路径。",
        current_move="封港并清理底层日志。",
        next_countermove="安排代理人截断证据链。",
        escalation_condition="主角拿到第一份铁证。",
        reveal_timing="第1卷",
        scope_volume_number=1,
        target_chapter_number=2,
        pressure_level=0.82,
        status="active",
        metadata_json={},
    )
    antagonist_plan.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        assert slug == "my-story"
        return project

    monkeypatch.setattr(narrative_services, "get_project_by_slug", fake_get_project_by_slug)

    session = FakeSession(
        scalars_results=[
            [arc],
            [beat],
            [clue],
            [payoff],
            [chapter_contract],
            [scene_contract],
            [emotion_track],
            [antagonist_plan],
            [],  # theme_arcs
            [],  # motif_placements
            [],  # subplot_schedule
            [],  # relationship_events
            [],  # reader_knowledge
            [],  # ending_contracts
            [],  # pacing_curve
        ]
    )

    overview = await narrative_services.build_narrative_overview(session, "my-story")

    assert overview.project_slug == "my-story"
    assert overview.plot_arcs[0].arc_code == "main_plot"
    assert overview.arc_beats[0].summary == "第1章承担主线推进。"
    assert overview.clues[0].clue_code == "clue-001"
    assert overview.payoffs[0].payoff_code == "payoff-001"
    assert overview.chapter_contracts[0].contract_summary == "本章要抛出主线异常。"
    assert overview.scene_contracts[0].contract_summary == "本场必须抛出异常航标。"
    assert overview.emotion_tracks[0].track_code == "bond-shenyan-gulin"
    assert overview.antagonist_plans[0].plan_code == "volume-01-pressure"


def test_build_theme_arc_specs_extracts_book_and_volume_themes() -> None:
    project = build_project()
    project.metadata_json["book_spec"] = {"theme": "真相与牺牲的代价"}
    volume = build_volume(project.id, 1)
    volume.theme = "信任的崩塌"
    specs = narrative_services._build_theme_arc_specs(
        project, volumes=[volume], volume_entries={},
    )
    assert len(specs) >= 2
    codes = [s["theme_code"] for s in specs]
    assert "main-theme" in codes
    assert "vol-01-theme" in codes
    assert specs[0]["theme_statement"] == "真相与牺牲的代价"


def test_build_pacing_curve_specs_creates_one_point_per_chapter() -> None:
    project = build_project()
    volume = build_volume(project.id, 1)
    chapters = [
        build_chapter(project.id, volume.id, i, f"第{i}章")
        for i in range(1, 6)
    ]
    specs = narrative_services._build_pacing_curve_specs(
        chapters=chapters, scenes_by_chapter={},
    )
    assert len(specs) == 5
    assert all(0.05 <= s["tension_level"] <= 0.99 for s in specs)
    assert specs[0]["chapter_number"] == 1
    assert specs[-1]["chapter_number"] == 5


def test_build_relationship_event_specs_creates_milestone_events() -> None:
    project = build_project()
    protagonist = build_character(project.id, "沈砚", "protagonist")
    antagonist = build_character(project.id, "顾临", "antagonist")
    relationship = build_relationship(project.id, protagonist.id, antagonist.id, "旧搭档")
    relationship.last_changed_chapter_no = 3  # simulate a change
    chapters = [build_chapter(project.id, uuid4(), i, f"第{i}章") for i in range(1, 5)]
    characters_by_id = {protagonist.id: protagonist, antagonist.id: antagonist}
    specs = narrative_services._build_relationship_event_specs(
        relationships=[relationship],
        characters_by_id=characters_by_id,
        chapters=chapters,
    )
    assert len(specs) >= 2  # establishment + change
    assert specs[0]["is_milestone"] is True
    assert specs[0]["chapter_number"] == 1
    assert specs[1]["chapter_number"] == 3


def test_build_ending_contract_spec_collects_open_arcs_and_clues() -> None:
    project_id = uuid4()
    arc = PlotArcModel(
        project_id=project_id,
        arc_code="main_plot",
        name="主线",
        arc_type="main_plot",
        promise="揭开真相",
        core_question="能否成功？",
        status="active",
        scope_level="project",
        metadata_json={},
    )
    arc.id = uuid4()
    clue = ClueModel(
        project_id=project_id,
        clue_code="clue-001",
        label="异常航标",
        clue_type="foreshadow",
        description="航标异常",
        status="planted",
        metadata_json={},
    )
    clue.id = uuid4()
    theme_arc = ThemeArcModel(
        project_id=project_id,
        theme_code="main-theme",
        theme_statement="真相与代价",
        symbol_set=[],
        evolution_stages=[],
        metadata_json={},
    )
    theme_arc.id = uuid4()
    spec = narrative_services._build_ending_contract_spec(
        arcs_by_code={"main_plot": arc},
        clues_by_code={"clue-001": clue},
        emotion_track_models=[],
        theme_arcs=[theme_arc],
    )
    assert "main_plot" in spec["arcs_to_resolve"]
    assert "clue-001" in spec["clues_to_payoff"]
    assert spec["thematic_final_expression"] == "真相与代价"

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.story_bible import CastSpecInput
from bestseller.infra.db.models import (
    CharacterModel,
    CharacterStateSnapshotModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    ProjectModel,
    RelationshipModel,
    StyleGuideModel,
    VolumeModel,
    VolumeFrontierModel,
    WorldBackboneModel,
    WorldRuleModel,
)
from bestseller.services import story_bible as story_bible_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
        get_map: dict[tuple[object, object], object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is not None and "id" in table.c and getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())
            if getattr(obj, "id", None) is not None:
                self.get_map[(type(obj), getattr(obj, "id"))] = obj

    async def get(self, model: object, key: object) -> object | None:
        return self.get_map.get((model, key))

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="science-fantasy",
        target_word_count=100000,
        target_chapters=24,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_book_spec() -> dict[str, object]:
    return {
        "title": "长夜巡航",
        "genre": "science-fantasy",
        "target_audience": "web-serial",
        "tone": ["紧张", "冷峻"],
        "themes": ["真相", "牺牲"],
        "logline": "被放逐的导航员调查被篡改的航线。",
        "series_engine": {"hook_style": "每章末抛出更大的风险"},
        "protagonist": {"name": "沈砚"},
        "stakes": {"personal": "失去搭档"},
    }


def build_world_spec() -> dict[str, object]:
    return {
        "world_name": "边境星门航道",
        "world_premise": "航道记录决定一切。",
        "rules": [
            {
                "rule_id": "R001",
                "name": "航道记录优先",
                "description": "官方航图高于个人证词。",
                "story_consequence": "主角无法直接翻案。",
                "exploitation_potential": "拿到底层日志即可翻盘。",
            }
        ],
        "locations": [
            {
                "name": "碎潮星港",
                "type": "星港",
                "atmosphere": "压抑",
                "key_rules": ["R001"],
                "story_role": "开局舞台",
            }
        ],
        "factions": [
            {
                "name": "帝国航道署",
                "goal": "维持统治",
                "method": "删档",
                "relationship_to_protagonist": "敌对",
                "internal_conflict": "技术官僚也在自保",
            }
        ],
        "power_system": {
            "name": "导航印记",
            "tiers": ["学徒", "导航员"],
            "protagonist_starting_tier": "导航员",
        },
        "power_structure": "帝国控制解释权",
        "forbidden_zones": "日志库",
    }


def build_cast_spec() -> dict[str, object]:
    return {
        "protagonist": {
            "name": "沈砚",
            "age": 29,
            "background": "被放逐的导航员",
            "goal": "找到账目证据",
            "fear": "再次害死同伴",
            "flaw": "不信任别人",
            "strength": "判断敏锐",
            "secret": "怀疑事故另有内情",
            "arc_trajectory": "从控制到协作",
            "arc_state": "逃避真相",
            "knowledge_state": {
                "knows": ["事故数据不对劲"],
                "falsely_believes": ["顾临抛弃了自己"],
                "unaware_of": ["签名链被盗用"],
            },
            "power_tier": "导航员",
            "relationships": [
                {"character": "顾临", "type": "旧搭档", "tension": "误会仍未解开"},
            ],
        },
        "antagonist": {
            "name": "祁镇",
            "background": "校准总监",
            "goal": "删光旧记录",
            "fear": "被高层清算",
            "flaw": "把秩序看得高于真实",
            "strength": "权力和档案权限",
            "secret": "批准过非法校准",
            "arc_trajectory": "从幕后到公开追杀",
            "arc_state": "仍在控制局面",
            "knowledge_state": {"knows": ["日志仍存在"]},
            "power_tier": "首席校准官",
            "relationships": [
                {"character": "沈砚", "type": "敌人", "tension": "必须抢先拿到日志"},
            ],
            "justification": "秩序优先于真相",
        },
        "supporting_cast": [
            {
                "name": "顾临",
                "role": "ally",
                "goal": "确认事故真相",
                "arc_state": "谨慎观望",
                "knowledge_state": {"knows": ["现场出现异常指令"]},
                "relationships": [
                    {"character": "沈砚", "type": "旧搭档", "tension": "彼此都以为被抛下"},
                ],
                "value_to_story": "行动执行者",
            }
        ],
        "conflict_map": [
            {
                "character_a": "沈砚",
                "character_b": "祁镇",
                "conflict_type": "目标冲突",
                "trigger_condition": "一旦接近日志库就会爆发公开追杀",
            }
        ],
    }


def build_volume_plan() -> list[dict[str, object]]:
    return [
        {
            "volume_number": 1,
            "volume_title": "失准航线",
            "volume_theme": "面对真相",
            "word_count_target": "20万字",
            "chapter_count_target": 40,
            "opening_state": {
                "protagonist_status": "被放逐",
                "protagonist_power_tier": "导航员",
            },
            "volume_goal": "找到第一份铁证",
            "volume_obstacle": "封港追捕",
            "volume_climax": "闯入静默航道抢黑匣子",
            "volume_resolution": {
                "protagonist_power_tier": "导航员",
                "goal_achieved": True,
            },
            "key_reveals": ["签名链被盗用"],
        }
    ]


@pytest.mark.asyncio
async def test_apply_book_spec_updates_project_and_style() -> None:
    project = build_project()
    session = FakeSession()

    changed = await story_bible_services.apply_book_spec(session, project, build_book_spec())

    assert changed is True
    styles = [item for item in session.added if isinstance(item, StyleGuideModel)]
    assert len(styles) == 1
    assert project.title == "长夜巡航"
    assert project.metadata_json["book_spec"]["genre"] == "science-fantasy"
    assert styles[0].tone_keywords == ["紧张", "冷峻"]
    assert "主题:真相" in styles[0].custom_rules


@pytest.mark.asyncio
async def test_upsert_world_spec_creates_world_entities() -> None:
    project = build_project()
    session = FakeSession()

    counts = await story_bible_services.upsert_world_spec(session, project, build_world_spec())

    assert counts == {
        "world_rules_upserted": 1,
        "locations_upserted": 1,
        "factions_upserted": 1,
    }
    assert any(isinstance(item, WorldRuleModel) for item in session.added)
    assert any(isinstance(item, LocationModel) for item in session.added)
    assert any(isinstance(item, FactionModel) for item in session.added)
    assert project.metadata_json["world_name"] == "边境星门航道"


@pytest.mark.asyncio
async def test_upsert_cast_spec_creates_characters_relationships_and_snapshots() -> None:
    project = build_project()
    session = FakeSession(scalar_results=[None, None, None])

    counts = await story_bible_services.upsert_cast_spec(session, project, build_cast_spec())

    characters = [item for item in session.added if isinstance(item, CharacterModel)]
    relationships = [item for item in session.added if isinstance(item, RelationshipModel)]
    snapshots = [item for item in session.added if isinstance(item, CharacterStateSnapshotModel)]

    assert counts["characters_upserted"] == 3
    assert counts["state_snapshots_created"] == 3
    assert len(characters) == 3
    assert len(relationships) >= 2
    assert len(snapshots) == 3
    protagonist = next(item for item in characters if item.name == "沈砚")
    assert protagonist.role == "protagonist"
    assert protagonist.is_pov_character is True
    assert protagonist.knowledge_state_json["knows"] == ["事故数据不对劲"]


@pytest.mark.asyncio
async def test_upsert_cast_spec_backfills_active_volumes_from_forces() -> None:
    """antagonist_forces[].active_volumes must propagate into character.metadata_json
    so narrative._build_antagonist_plan_specs can route per-volume antagonists
    correctly. Root cause of the xianxia (道种破虚) failure — every volume
    plan collapsed onto the primary antagonist because no character row had
    active_volumes set."""

    project = build_project()
    session = FakeSession(scalar_results=[None, None, None])

    cast = build_cast_spec()
    # Pin the primary antagonist to vols 11-12, and add two force entries
    # routing supporting-cast characters to vols 1-5 and 6-10.
    cast["supporting_cast"] = [
        {"name": "顾临", "role": "ally", "goal": "确认真相"},
        {"name": "苏瑶", "role": "antagonist", "goal": "阻击主角"},
        {"name": "寒鸦", "role": "antagonist", "goal": "切断补给"},
    ]
    cast["antagonist_forces"] = [
        {"name": "初期围堵", "force_type": "character",
         "character_ref": "苏瑶", "active_volumes": [1, 2, 3, 4, 5]},
        {"name": "中期封锁", "force_type": "character",
         "character_ref": "寒鸦", "active_volumes": [6, 7, 8, 9, 10]},
        {"name": "终局对决", "force_type": "character",
         "character_ref": "祁镇", "active_volumes": [11, 12]},
    ]

    await story_bible_services.upsert_cast_spec(session, project, cast)

    characters = [item for item in session.added if isinstance(item, CharacterModel)]
    by_name = {c.name: c for c in characters}
    assert by_name["苏瑶"].metadata_json.get("active_volumes") == [1, 2, 3, 4, 5]
    assert by_name["寒鸦"].metadata_json.get("active_volumes") == [6, 7, 8, 9, 10]
    assert by_name["祁镇"].metadata_json.get("active_volumes") == [11, 12]
    # Characters not referenced by any force keep an empty/undefined active_volumes
    assert "active_volumes" not in (by_name["顾临"].metadata_json or {})
    # Referenced supporting_cast entries are promoted to role='antagonist' so
    # downstream narrative routing (_build_antagonist_plan_specs) can consider
    # them as per-volume antagonist candidates.
    assert by_name["苏瑶"].role == "antagonist"
    assert by_name["寒鸦"].role == "antagonist"
    # Non-referenced allies are NOT promoted.
    assert by_name["顾临"].role == "ally"


@pytest.mark.asyncio
async def test_upsert_cast_spec_counts_voice_profiles_and_moral_frameworks() -> None:
    project = build_project()
    session = FakeSession(scalar_results=[None, None, None])

    cast_with_voice = build_cast_spec()
    cast_with_voice["protagonist"]["voice_profile"] = {
        "speech_register": "冷静克制",
        "verbal_tics": ["……"],
        "sentence_style": "短句利落型",
    }
    cast_with_voice["protagonist"]["moral_framework"] = {
        "core_values": ["真相高于秩序"],
        "lines_never_crossed": ["不伤害无辜"],
    }

    counts = await story_bible_services.upsert_cast_spec(session, project, cast_with_voice)

    assert counts["voice_profiles_populated"] >= 1
    assert counts["moral_frameworks_populated"] >= 1
    characters = [item for item in session.added if isinstance(item, CharacterModel)]
    protagonist = next(item for item in characters if item.name == "沈砚")
    assert protagonist.voice_profile_json["speech_register"] == "冷静克制"
    assert protagonist.moral_framework_json["core_values"] == ["真相高于秩序"]


@pytest.mark.asyncio
async def test_upsert_volume_plan_creates_and_updates_volumes() -> None:
    project = build_project()
    session = FakeSession(scalar_results=[None])

    counts = await story_bible_services.upsert_volume_plan(session, project, build_volume_plan())

    volumes = [item for item in session.added if isinstance(item, VolumeModel)]
    assert counts["volumes_upserted"] == 1
    assert len(volumes) == 1
    assert volumes[0].title == "失准航线"
    assert volumes[0].target_word_count == 20
    assert project.current_volume_number == 1


def test_is_placeholder_volume_title_detects_generic_names() -> None:
    assert story_bible_services._is_placeholder_volume_title("第3卷")
    assert story_bible_services._is_placeholder_volume_title(" 第 12 卷 ")
    assert story_bible_services._is_placeholder_volume_title("Volume 4")
    assert story_bible_services._is_placeholder_volume_title("Vol. 7")
    assert story_bible_services._is_placeholder_volume_title("")
    assert story_bible_services._is_placeholder_volume_title(None)
    assert not story_bible_services._is_placeholder_volume_title("逆命入局")
    assert not story_bible_services._is_placeholder_volume_title("Ashes of the Court")


def test_normalize_volume_plan_titles_replaces_placeholders() -> None:
    volumes = [
        {"volume_number": 1, "volume_title": "逆命入局", "conflict_phase": "individual_survival"},
        {"volume_number": 2, "volume_title": "第2卷", "conflict_phase": "faction_friction"},
        {"volume_number": 3, "volume_title": "", "conflict_phase": "power_system_test"},
        {"volume_number": 4, "volume_title": "Volume 4", "conflict_phase": "power_system_test"},
    ]
    normalized, replaced = story_bible_services.normalize_volume_plan_titles(
        volumes, is_en=False
    )
    titles = [e["volume_title"] for e in normalized]
    assert replaced == 3
    assert titles[0] == "逆命入局"  # real title kept
    assert "第" not in titles[1]
    assert titles[2] and titles[3]
    assert titles[2] != titles[3]  # same phase, different occurrence
    assert len(titles) == len(set(titles))  # all unique


@pytest.mark.asyncio
async def test_upsert_volume_plan_normalizes_placeholder_titles() -> None:
    project = build_project()
    project.metadata_json = {"category_key": "action-progression"}
    session = FakeSession(scalar_results=[None, None])

    plan = [
        {
            "volume_number": 1,
            "volume_title": "逆命入局",
            "conflict_phase": "individual_survival",
            "opening_state": {"protagonist_status": "低谷", "protagonist_power_tier": "低阶"},
            "key_reveals": [],
        },
        {
            "volume_number": 2,
            "volume_title": "第2卷",
            "conflict_phase": "faction_friction",
            "opening_state": {"protagonist_status": "入局", "protagonist_power_tier": "低阶"},
            "key_reveals": [],
        },
    ]

    await story_bible_services.upsert_volume_plan(session, project, plan)

    volumes = [item for item in session.added if isinstance(item, VolumeModel)]
    titles = {v.volume_number: v.title for v in volumes}
    assert titles[1] == "逆命入局"
    assert not story_bible_services._is_placeholder_volume_title(titles[2])
    # project metadata should also carry the normalized title for later reads.
    stored = project.metadata_json["volume_plan"]
    stored_titles = {e["volume_number"]: e["volume_title"] for e in stored}
    assert not story_bible_services._is_placeholder_volume_title(stored_titles[2])


@pytest.mark.asyncio
async def test_load_scene_story_bible_context_includes_roles_states_and_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    volume = VolumeModel(project_id=project.id, volume_number=1, title="失准航线", metadata_json={})
    volume.id = uuid4()
    volume.goal = "找到第一份铁证"
    volume.obstacle = "封港追捕"
    chapter = type("ChapterStub", (), {"chapter_number": 1, "volume_id": volume.id})()
    scene = type(
        "SceneStub",
        (),
        {"participants": ["沈砚", "顾临"], "scene_number": 1},
    )()
    shen = CharacterModel(
        id=story_bible_services.stable_character_id(project.id, "沈砚"),
        project_id=project.id,
        name="沈砚",
        role="protagonist",
        goal="找证据",
        knowledge_state_json={"knows": ["事故数据不对劲"]},
        metadata_json={},
    )
    gu = CharacterModel(
        id=story_bible_services.stable_character_id(project.id, "顾临"),
        project_id=project.id,
        name="顾临",
        role="ally",
        goal="查真相",
        knowledge_state_json={"knows": ["异常指令"]},
        metadata_json={},
    )
    backbone = WorldBackboneModel(
        project_id=project.id,
        title="全书世界主干",
        core_promise="被放逐的导航员调查被篡改的航线。",
        mainline_drive="追查真相并撬动记录垄断。",
        invariant_elements=["航道记录优先"],
        stable_unknowns=["更高层操盘者身份"],
        metadata_json={},
    )
    frontier = VolumeFrontierModel(
        project_id=project.id,
        volume_id=volume.id,
        volume_number=1,
        title="失准航线",
        frontier_summary="只展开边境星港、航道署和底层日志的局部世界。",
        expansion_focus="边境封锁与取证",
        start_chapter_number=1,
        end_chapter_number=40,
        visible_rule_codes=["R001"],
        active_locations=["碎潮星港"],
        active_factions=["帝国航道署"],
        active_arc_codes=["main_plot"],
        future_reveal_codes=["volume-02-reveal-01"],
        metadata_json={},
    )
    next_gate = ExpansionGateModel(
        project_id=project.id,
        volume_id=volume.id,
        gate_code="unlock-volume-02",
        label="第2卷世界扩张闸门",
        gate_type="world_expansion",
        condition_summary="完成第一份铁证并承受封港代价。",
        unlocks_summary="展开更高层航道势力。",
        unlock_volume_number=2,
        unlock_chapter_number=41,
        status="planned",
        metadata_json={},
    )
    session = FakeSession(
        scalar_results=[backbone, frontier, 2, next_gate],
        get_map={
            (VolumeModel, volume.id): volume,
            (CharacterModel, shen.id): shen,
            (CharacterModel, gu.id): gu,
        },
        scalars_results=[
            [
                WorldRuleModel(
                    project_id=project.id,
                    rule_code="R001",
                    name="航道记录优先",
                    description="官方航图高于证词",
                    metadata_json={},
                )
            ],
            # get_effective_character_state queries CharacterStateSnapshotModel once per
            # participant (沈砚, 顾临) before the relationship query runs.
            [],  # snapshots for 沈砚
            [],  # snapshots for 顾临
            [
                RelationshipModel(
                    project_id=project.id,
                    character_a_id=shen.id,
                    character_b_id=gu.id,
                    relationship_type="旧搭档",
                    strength=0.6,
                    tension_summary="误会仍未解开",
                    metadata_json={},
                )
            ],
        ],
    )

    async def fake_latest_character_state(session, **kwargs):
        return CharacterStateSnapshotModel(
            project_id=project.id,
            character_id=kwargs["character_id"],
            chapter_number=0,
            scene_number=0,
            arc_state="开场",
            emotional_state="压抑",
            trust_map={},
            beliefs=[],
        )

    monkeypatch.setattr(story_bible_services, "get_latest_character_state", fake_latest_character_state)

    context = await story_bible_services.load_scene_story_bible_context(
        session,
        project=project,
        chapter=chapter,
        scene=scene,
    )

    assert context["volume"]["goal"] == "找到第一份铁证"
    assert context["world_backbone"]["mainline_drive"] == "追查真相并撬动记录垄断。"
    assert context["volume_frontier"]["active_locations"] == ["碎潮星港"]
    assert context["deferred_reveal_status"]["hidden_count"] == 2
    assert context["next_expansion_gate"]["unlock_volume_number"] == 2
    assert context["world_rules"][0]["rule_code"] == "R001"
    assert context["participants"][0]["name"] == "沈砚"
    assert context["relationships"][0]["relationship_type"] == "旧搭档"


def test_parse_models_cover_list_and_role_normalization() -> None:
    cast_spec = CastSpecInput.model_validate(build_cast_spec())
    volumes = story_bible_services.parse_volume_plan_input(build_volume_plan())
    world_spec = story_bible_services.parse_world_spec_input(build_world_spec())

    assert cast_spec.protagonist is not None
    assert cast_spec.protagonist.role == "protagonist"
    assert cast_spec.antagonist is not None
    assert cast_spec.antagonist.role == "antagonist"
    assert [item.name for item in cast_spec.all_characters()] == ["沈砚", "祁镇", "顾临"]
    assert volumes[0].volume_title == "失准航线"
    assert world_spec.locations[0].location_type == "星港"

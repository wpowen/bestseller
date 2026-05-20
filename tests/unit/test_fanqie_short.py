"""番茄短故事功能单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import ProjectType
from bestseller.domain.fanqie_short import (
    FanqieShortBeat,
    FanqieShortBeatSheet,
    build_fanqie_short_metadata,
    resolve_length_preset,
    validate_fanqie_short_project,
)
from bestseller.domain.project import ProjectCreate
from bestseller.services.fanqie_short_export import (
    build_signing_readiness_report,
    insert_unlock_line_marker,
)
from bestseller.services.fanqie_short_opening_gate import scan_fanqie_short_taboo_signals
from bestseller.services.fanqie_short_planner import (
    _fallback_beat_sheet,
    build_fanqie_segment_outline_batch,
)
from bestseller.services.fanqie_short_ranking_gate import (
    evaluate_fanqie_closure_gate,
    evaluate_fanqie_ranking_readiness,
    evaluate_fanqie_unlock_ranking_gate,
)
from bestseller.services.story_shape_router import derive_story_shape


pytestmark = pytest.mark.unit


def test_resolve_length_preset_defaults() -> None:
    spec = resolve_length_preset(None)
    assert spec["target_words"] == 15_000
    assert spec["segment_count"] == 6


def test_build_fanqie_short_metadata_contract() -> None:
    meta = build_fanqie_short_metadata(length_key="fanqie-short-8k", pov="first_person")
    assert meta["content_mode"] == "fanqie_short_story"
    assert meta["platform_key"] == "tomato_short"
    assert meta["segment_count"] == 4
    assert meta["unlock_line_ratio"] == 0.30


def test_project_create_validates_fanqie_short() -> None:
    meta = build_fanqie_short_metadata(length_key="fanqie-short-15k")
    payload = ProjectCreate(
        slug="fanqie-test-001",
        title="测试短故事",
        genre="都市",
        target_word_count=15_000,
        target_chapters=6,
        project_type=ProjectType.FANQIE_SHORT,
        metadata=meta,
    )
    validate_fanqie_short_project(payload)
    assert payload.project_type == ProjectType.FANQIE_SHORT


def test_project_create_rejects_fanqie_word_count() -> None:
    meta = build_fanqie_short_metadata()
    with pytest.raises(ValueError, match="target_word_count"):
        ProjectCreate(
            slug="fanqie-bad-words",
            title="坏字数",
            genre="都市",
            target_word_count=3_000,
            target_chapters=4,
            project_type=ProjectType.FANQIE_SHORT,
            metadata=meta,
        )


def test_insert_unlock_line_marker_at_ratio() -> None:
    text = "a" * 1000
    marked, pos = insert_unlock_line_marker(text, unlock_line_ratio=0.30)
    assert "<!-- UNLOCK_LINE: 30%" in marked
    assert 250 <= pos <= 350


def test_signing_readiness_report_shape() -> None:
    sample = "我" + "冲突" * 50 + "反击" * 30 + "爆点" * 20
    report = build_signing_readiness_report(sample, target_word_count=len(sample))
    assert report["platform"] == "tomato_short"
    assert "total_words" in report
    assert "opening_gate_passed" in report
    assert "ranking_gate_passed" in report
    assert "ranking_findings" in report


def test_taboo_scan_detects_review_begging() -> None:
    issues = scan_fanqie_short_taboo_signals("这是一段正文，求过审，谢谢编辑。")
    assert "contains_review_begging" in issues


def test_fallback_beat_sheet_segment_count() -> None:
    project = type(
        "P",
        (),
        {
            "target_chapters": 6,
            "target_word_count": 15_000,
            "title": "测试",
            "metadata_json": {"pov": "first_person"},
        },
    )()
    sheet = _fallback_beat_sheet(project, "主角遭遇背叛后反击。")
    assert len(sheet.beats) == 6
    assert sheet.unlock_milestone_segment >= 2
    assert sheet.beats[0].opening_contract
    assert sheet.beats[-1].closure_contract


def test_fanqie_ranking_gate_passes_strong_single_piece() -> None:
    text = (
        "我被主管按在会议桌前，逼我签下挪用公款的认罪书。父亲手术费只剩最后一小时，"
        "可他把伪造证据推到我脸上。我的手腕疼得发烫，情绪能量第一次解锁，我反手夺过"
        "录音笔，当众放出他威胁财务的证据。全场安静，我拿到第一枚筹码。\n\n"
        "后面我继续追查，能力每次使用都会反噬失控，让我暴露在反派眼前。"
        "我忍着疼痛换来关键证据，公开反制他的局。\n\n"
        "最终真相大白，反派认罪，父亲获救，我离开那间办公室。故事在这里收场。"
    )
    report = evaluate_fanqie_ranking_readiness(text, protagonist_name="我")
    assert report.passed


def test_fanqie_ranking_gate_blocks_weak_background_opening() -> None:
    text = (
        "清晨的阳光落在窗台上，这座城市已经沉睡多年。关于情绪能量的传说很复杂，"
        "多年以前有人建立了实验室，世界观由此展开。"
    )
    report = evaluate_fanqie_ranking_readiness(text, protagonist_name="我")
    assert not report.passed
    assert {finding.code for finding in report.findings if finding.severity == "critical"}


def test_fanqie_unlock_gate_requires_payoff_before_30_percent() -> None:
    text = "我被逼着签字，对方威胁我，否则报警。" + "我只能继续忍耐。" * 120
    report = evaluate_fanqie_unlock_ranking_gate(text)
    assert not report.passed
    assert any(finding.code == "unlock_payoff_missing" for finding in report.findings)


def test_fanqie_opening_gate_requires_fast_payoff_and_early_ability() -> None:
    text = (
        "我被主管按在会议桌前，逼我签下挪用公款的认罪书。父亲手术费只剩最后一小时，"
        "可他把伪造材料推到我脸上。我只能忍着，听他继续威胁，否则报警，否则封杀。"
        "他又把离职单推回来，盯着我的身份证号念，逼我承认所有账目都是我做的。"
        "会议室外的同事没有一个说话，我只能继续低头站着。办公室的空调声很响，"
        "他一遍遍让我签名、按手印、写情况说明，还说医院那边也等不了多久。"
        "我把手放在桌沿，指节发白，却没有任何人替我开口。门外有人经过，"
        "看见我又立刻低头走开。时间被拉得很长，每一秒都只剩压迫和羞辱。"
        "他开始念公司制度，一条一条压下来，又让我给医院打电话，说别让家里人等。"
        "我听见自己的呼吸越来越重，却只能看着那张纸贴在桌面上。"
        "窗外有人笑，会议室里没人笑。桌上的水杯被推倒，水流到我袖口，冷得像冰。"
        "我没有筹码，没有帮手，没有可以立刻拿出来的材料。"
        "直到很久之后，"
        "黑屏才弹出系统提示，我终于反击，拿到证据公开翻盘。"
        "最终真相大白，故事在这里收场。"
    )
    report = evaluate_fanqie_ranking_readiness(text, protagonist_name="我")

    codes = {finding.code for finding in report.findings if finding.severity == "critical"}
    assert "opening_fast_payoff_missing" in codes
    assert "opening_ability_late" in codes


def test_fanqie_closure_gate_blocks_serial_teaser() -> None:
    text = (
        "我公开证据，暂时赢下这一局。\n\n"
        "五个小时后，地下三层。\n\n顾颜的真相。\n\n你欠我一个真相。"
    )
    report = evaluate_fanqie_closure_gate(text)
    assert not report.passed
    assert any(finding.code == "serial_cliffhanger_ending" for finding in report.findings)


@pytest.mark.asyncio
async def test_generate_fanqie_beat_sheet_resolves_prompt_pack_with_project_genre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import fanqie_short_planner

    project = SimpleNamespace(
        id=uuid4(),
        slug="fanqie-beat-test",
        title="短篇测试",
        genre="都市异能",
        sub_genre="现实逆袭",
        target_chapters=6,
        target_word_count=15_000,
        metadata_json={"pov": "first_person"},
    )
    captured: dict[str, object] = {}

    async def fake_get_project_by_slug(_session: object, slug: str) -> object:
        assert slug == project.slug
        return project

    async def fake_complete_text(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            content=(
                '{"title":"短篇测试","logline":"误解后反击","pov":"first_person",'
                '"unlock_milestone_segment":2,'
                '"beats":['
                '{"segment_number":1,"beat_role":"hook","purpose":"开局冲突",'
                '"payoff":"第一次爽点","emotional_turn":"压迫到反击"},'
                '{"segment_number":2,"beat_role":"rising","purpose":"升级误解",'
                '"payoff":"证据浮现","emotional_turn":"反击到受阻"},'
                '{"segment_number":3,"beat_role":"rising","purpose":"反向布局",'
                '"payoff":"局势反转","emotional_turn":"受阻到主动"},'
                '{"segment_number":4,"beat_role":"midpoint","purpose":"公开交锋",'
                '"payoff":"打脸成立","emotional_turn":"主动到压迫"},'
                '{"segment_number":5,"beat_role":"crisis","purpose":"最终代价",'
                '"payoff":"真相逼近","emotional_turn":"压迫到爆发"},'
                '{"segment_number":6,"beat_role":"resolution","purpose":"收束主线",'
                '"payoff":"完整落点","emotional_turn":"爆发到余波"}]}'
            )
        )

    async def fake_import_planning_artifact(
        _session: object,
        slug: str,
        artifact: object,
    ) -> object:
        captured["slug"] = slug
        captured["artifact"] = artifact
        return SimpleNamespace(id=uuid4(), version_no=1)

    monkeypatch.setattr(fanqie_short_planner, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(fanqie_short_planner, "complete_text", fake_complete_text)
    monkeypatch.setattr(
        fanqie_short_planner,
        "import_planning_artifact",
        fake_import_planning_artifact,
    )

    sheet = await fanqie_short_planner.generate_fanqie_beat_sheet(
        object(),
        object(),
        project.slug,
        "女主被误解后靠证据反击。",
    )

    assert sheet.title == "短篇测试"
    assert len(sheet.beats) == 6
    assert captured["slug"] == project.slug


def test_build_segment_outline_single_scene_per_segment() -> None:
    project = type(
        "P",
        (),
        {
            "target_chapters": 4,
            "target_word_count": 8_000,
            "slug": "fanqie-outline-test",
            "title": "大纲测试",
            "language": "zh-CN",
        },
    )()
    beats = FanqieShortBeatSheet(
        beats=[
            FanqieShortBeat(
                segment_number=i,
                beat_role="rising",
                purpose=f"段{i}目的",
            )
            for i in range(1, 5)
        ]
    )
    batch = build_fanqie_segment_outline_batch(project, beats)
    assert len(batch["chapters"]) == 4
    for ch in batch["chapters"]:
        assert ch["title"].endswith("段")
        assert "章" not in ch["title"]
        assert len(ch["scenes"]) == 1
    assert "爽点合同" in batch["chapters"][0]["chapter_goal"]


def test_story_shape_fanqie_short_metadata() -> None:
    shape = derive_story_shape(
        metadata=build_fanqie_short_metadata(),
        target_chapters=6,
        target_word_count=15_000,
    )
    assert shape.length_class == "short"
    assert shape.outline_depth == "scene"
    assert shape.publication_mode == "commercial_book"


@pytest.mark.asyncio
async def test_fanqie_short_pipeline_materializes_narrative_graph_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import fanqie_short_pipeline
    from bestseller.settings import load_settings

    project = SimpleNamespace(
        id=uuid4(),
        slug="fanqie-materialize-graph-test",
        title="物化测试",
        genre="都市异能",
        sub_genre="现实逆袭",
        language="zh-CN",
        target_chapters=1,
        target_word_count=8_000,
        project_type=ProjectType.FANQIE_SHORT.value,
        metadata_json=build_fanqie_short_metadata(length_key="fanqie-short-8k"),
    )
    calls: list[str] = []
    graph_workflow_id = uuid4()

    async def fake_get_project_by_slug(_session: object, slug: str) -> object:
        assert slug == project.slug
        return project

    async def fake_noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_generate_foundation_plan(*_args: object, **_kwargs: object) -> object:
        calls.append("foundation")
        return SimpleNamespace(workflow_run_id=uuid4())

    async def fake_get_latest_planning_artifact(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(content={})

    async def fake_generate_fanqie_beat_sheet(*_args: object, **_kwargs: object) -> object:
        calls.append("beat_sheet")
        return FanqieShortBeatSheet(
            beats=[FanqieShortBeat(segment_number=1, beat_role="hook", purpose="开局冲突")],
            unlock_milestone_segment=1,
        )

    async def fake_persist_fanqie_chapter_outline(*_args: object, **_kwargs: object) -> None:
        calls.append("persist_outline")

    async def fake_materialize_story_bible(*_args: object, **_kwargs: object) -> object:
        calls.append("story_bible")
        return SimpleNamespace(workflow_run_id=uuid4())

    async def fake_materialize_outline(*_args: object, **_kwargs: object) -> object:
        calls.append("outline")
        return SimpleNamespace(workflow_run_id=uuid4(), chapters_created=1, scenes_created=1)

    async def fake_materialize_narrative_graph(*_args: object, **_kwargs: object) -> object:
        calls.append("narrative_graph")
        return SimpleNamespace(workflow_run_id=graph_workflow_id, plot_arc_count=1, clue_count=0)

    async def fake_run_chapter_pipeline(*_args: object, **_kwargs: object) -> None:
        calls.append("write")

    async def fake_load_chapter_drafts(*_args: object, **_kwargs: object) -> list:
        return []

    monkeypatch.setattr(fanqie_short_pipeline, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(fanqie_short_pipeline, "_ensure_project_invariants", fake_noop)
    monkeypatch.setattr(fanqie_short_pipeline, "_checkpoint_commit", fake_noop)
    monkeypatch.setattr(fanqie_short_pipeline, "generate_foundation_plan", fake_generate_foundation_plan)
    monkeypatch.setattr(
        fanqie_short_pipeline,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )
    monkeypatch.setattr(
        fanqie_short_pipeline,
        "generate_fanqie_beat_sheet",
        fake_generate_fanqie_beat_sheet,
    )
    monkeypatch.setattr(
        fanqie_short_pipeline,
        "persist_fanqie_chapter_outline",
        fake_persist_fanqie_chapter_outline,
    )
    monkeypatch.setattr(
        fanqie_short_pipeline,
        "materialize_latest_story_bible",
        fake_materialize_story_bible,
    )
    monkeypatch.setattr(
        fanqie_short_pipeline,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_outline,
    )
    monkeypatch.setattr(
        fanqie_short_pipeline,
        "materialize_latest_narrative_graph",
        fake_materialize_narrative_graph,
    )
    monkeypatch.setattr(fanqie_short_pipeline, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(fanqie_short_pipeline, "_load_chapter_drafts", fake_load_chapter_drafts)

    payload = ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        sub_genre=project.sub_genre,
        target_word_count=8_000,
        target_chapters=4,
        project_type=ProjectType.FANQIE_SHORT,
        metadata=project.metadata_json,
    )

    result = await fanqie_short_pipeline.run_fanqie_short_pipeline(
        object(),
        load_settings(),
        project_payload=payload,
        premise="测试短故事",
        export_markdown=False,
    )

    assert calls.index("outline") < calls.index("narrative_graph") < calls.index("write")
    assert result.narrative_graph_workflow_run_id == graph_workflow_id


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_routes_fanqie_short(monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import uuid4

    from bestseller.domain.planning import AutowriteResult
    from bestseller.domain.project import ProjectCreate
    from bestseller.services import pipelines
    from bestseller.settings import load_settings

    called: dict[str, bool] = {"routed": False}

    async def _fake_fanqie_short_pipeline(*_args, **_kwargs) -> AutowriteResult:
        called["routed"] = True
        return AutowriteResult(
            project_id=uuid4(),
            project_slug="fanqie-route-test",
            planning_workflow_run_id=uuid4(),
            project_workflow_run_id=uuid4(),
            chapter_count=6,
        )

    monkeypatch.setattr(
        "bestseller.services.fanqie_short_pipeline.run_fanqie_short_pipeline",
        _fake_fanqie_short_pipeline,
    )
    monkeypatch.setattr(
        pipelines,
        "_should_use_progressive_pipeline",
        lambda *_a, **_k: False,
    )

    meta = build_fanqie_short_metadata()
    payload = ProjectCreate(
        slug="fanqie-route-test",
        title="路由测试",
        genre="都市",
        target_word_count=15_000,
        target_chapters=6,
        project_type=ProjectType.FANQIE_SHORT,
        metadata=meta,
    )

    class _DummySession:
        pass

    result = await pipelines.run_autowrite_pipeline(
        _DummySession(),  # type: ignore[arg-type]
        load_settings(),
        project_payload=payload,
        premise="测试路由",
    )
    assert called["routed"] is True
    assert result.project_slug == "fanqie-route-test"

def test_evaluate_genre_suitable_for_short_story_allowlist() -> None:
    from bestseller.domain.fanqie_short import (
        ensure_fanqie_short_genre_compatible,
        evaluate_genre_suitable_for_short_story,
    )

    assert evaluate_genre_suitable_for_short_story({"key": "urban-revenge"})
    assert evaluate_genre_suitable_for_short_story(
        {
            "key": "custom",
            "name": "都市复仇",
            "recommended_platforms": ["番茄小说"],
            "language": "zh-CN",
            "description": "打脸逆袭悬疑",
        }
    )
    assert not evaluate_genre_suitable_for_short_story({"key": "xianxia-upgrade"})
    ensure_fanqie_short_genre_compatible("urban-revenge", suitable=True)
    with pytest.raises(ValueError, match="适合短篇"):
        ensure_fanqie_short_genre_compatible("xianxia-upgrade", suitable=False)


def test_genre_preset_exposes_suitable_for_short_story_flag() -> None:
    from bestseller.services.writing_presets import list_genre_presets

    presets = list_genre_presets()
    assert presets
    assert any(p.suitable_for_short_story for p in presets)
    assert not all(p.suitable_for_short_story for p in presets)

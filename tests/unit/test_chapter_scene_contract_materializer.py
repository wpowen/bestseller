from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.chapter_scene_contract_materializer import (
    materialize_chapter_contract_from_chapter,
    materialize_chapter_scene_contracts,
)


def test_materializer_fills_front_scene_writer_aliases_from_canonical_overlay() -> None:
    chapter = SimpleNamespace(chapter_number=1, metadata_json={})
    scene = SimpleNamespace(
        scene_number=1,
        scene_type="opening",
        participants=["林渊"],
        purpose={
            "story": "王建业求救电话把林家旧账和子时倒计时绑在一起。",
            "reader_hook": "电话里响起第二个王建业的笑声。",
        },
        hook_requirement=None,
        metadata_json={
            "methodology_contract": {
                "conflict_stakes": "林渊若误判，王建业会在子时前被镜债收账。",
                "conflict_buffs": ["子时倒计时", "铜钱发烫"],
                "spotlight_character": "林渊",
                "reveal_mode": "电话声证据先行",
                "signature_image": "康熙铜钱冒出黑红水汽",
                "cut_point": "电话里响起第二个王建业的笑声。",
            }
        },
    )

    report = materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene])

    contract = scene.metadata_json["methodology_contract"]
    assert report.changed is True
    assert report.complete is True
    assert contract["pressure_stack"] == ["子时倒计时", "铜钱发烫"]
    assert contract["focus_character"] == "林渊"
    assert contract["stakes"].startswith("林渊若误判")
    assert contract["breakpoint"].startswith("电话里")
    assert scene.metadata_json["gate_function"].startswith("opening_pull")
    assert scene.metadata_json["visible_progress"].startswith("王建业求救电话")
    assert chapter.metadata_json["chapter_scene_contract_materialization"]["complete"] is True


def test_materializer_reports_unresolved_fields_when_scene_is_too_thin() -> None:
    chapter = SimpleNamespace(chapter_number=1, metadata_json={})
    scene = SimpleNamespace(
        scene_number=1,
        scene_type="opening",
        participants=[],
        purpose={},
        hook_requirement=None,
        metadata_json={"methodology_contract": {}},
    )

    report = materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene])

    assert report.changed is True
    assert report.complete is False
    unresolved = report.scene_changes[0].unresolved_fields
    assert "stakes" in unresolved
    assert "focus_character" in unresolved
    assert scene.metadata_json["gate_function"].startswith("opening_pull")


def test_chapter_contract_materializer_refreshes_stale_writer_contract() -> None:
    chapter = SimpleNamespace(
        chapter_goal="新章节任务",
        opening_situation="新开篇：王建业说明报警和物业都失败后才找林渊。",
        main_conflict="新冲突：林渊必须在父亲旧案和职业判断之间选择。",
        chapter_emotion_arc="由警觉转为主动承压",
        information_revealed=["王建业找林渊不是随机求助", {"summary": "十七栋地址和父亲旧案重合"}],
        hook_description="旧铜钥匙敲出三短一长。",
    )
    contract = SimpleNamespace(
        contract_summary="旧章节任务",
        opening_state={"opening_situation": "旧开篇"},
        core_conflict="旧冲突",
        emotional_shift="旧情绪",
        information_release="旧信息",
        closing_hook="旧钩子",
        metadata_json={"methodology_contract": {"pacing_mode": "accelerate"}},
    )

    report = materialize_chapter_contract_from_chapter(chapter=chapter, chapter_contract=contract)

    assert report.changed is True
    assert "opening_state.opening_situation" in report.filled_fields
    assert contract.contract_summary == "新章节任务"
    assert contract.opening_state["opening_situation"].startswith("新开篇")
    assert contract.core_conflict.startswith("新冲突")
    assert "父亲旧案重合" in contract.information_release
    assert contract.closing_hook.startswith("旧铜钥匙")
    assert contract.metadata_json["methodology_contract"]["pacing_mode"] == "accelerate"

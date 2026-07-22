from __future__ import annotations

from types import SimpleNamespace

from bestseller.api.schemas.tasks import PipelineRequest
from bestseller.domain.workflow import ChapterOutlineInput
from bestseller.services.chapter_generation_input_builder import (
    _merge_chapter_metadata_contract,
)
from bestseller.services.drafts import (
    _chapter_first_acceptance_contract_payload,
    _chapter_first_writer_aim,
    _render_chapter_first_scene_cards,
    _render_whole_chapter_logic_contract,
)
from bestseller.services.workflows import _sync_chapter_causality_metadata


def _logic_contract() -> dict[str, object]:
    return {
        "entry_state": {"time": "冬祭前二日", "location": "主炉外圈"},
        "causal_chain": ["压力产生", "主动选择", "对手反制", "代价落地"],
        "ordered_events": ["敲钟", "公开核验", "取得半日权限"],
        "numeric_facts": [{"value": 312, "source": "五十七名旧契合计"}],
        "knowledge_boundaries": {"许照川_must_not_know": ["沉木用途"]},
        "cheap_solutions_excluded": {"直接删名": "炉审无权改总契"},
        "exit_state": {"permission": "查账到酉末"},
    }


def test_chapter_outline_accepts_whole_chapter_logic_contract_alias() -> None:
    outline = ChapterOutlineInput.model_validate(
        {
            "chapter_number": 5,
            "goal": "公开启动炉审并取得查账权",
            "logic_contract": _logic_contract(),
        }
    )

    assert outline.whole_chapter_logic_contract["ordered_events"] == [
        "敲钟",
        "公开核验",
        "取得半日权限",
    ]


def test_materialization_persists_logic_contract_and_generation_input_reads_it() -> None:
    chapter = SimpleNamespace(metadata_json={})
    outline = ChapterOutlineInput(
        chapter_number=5,
        chapter_goal="公开启动炉审并取得查账权",
        whole_chapter_logic_contract=_logic_contract(),
    )

    _sync_chapter_causality_metadata(chapter, outline)
    merged = _merge_chapter_metadata_contract({}, chapter=chapter)

    assert chapter.metadata_json["whole_chapter_logic_contract"] == _logic_contract()
    assert merged["whole_chapter_logic_contract"] == _logic_contract()


def test_chapter_first_prompt_treats_scene_cards_as_hidden_nodes() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        title="三次敲钟",
        scene_type="pressure",
        target_word_count=900,
        time_label="午时",
        participants=["许照川", "顾箬"],
        purpose={"story": "启动炉审", "emotion": "压力递增"},
        entry_state={"location": "主炉外圈"},
        exit_state={"review": "opened"},
        metadata_json={},
        forbidden_actions=[],
        key_dialogue_beats=[],
        sensory_anchors={},
        hook_requirement="取得临时铜签",
        rewrite_hint="",
    )
    nodes = _render_chapter_first_scene_cards([scene])
    contract = _render_whole_chapter_logic_contract(
        {"whole_chapter_logic_contract": _logic_contract()},
        language="zh-CN",
    )

    assert "节点1" in nodes
    assert "弱场景逻辑地图" in nodes
    assert "低优先级" in nodes
    assert "字数边界" not in nodes
    assert "整章逻辑合同·隐藏硬事实" in contract
    assert "数字依据" not in contract or "numeric_facts" in contract
    assert "不得向读者解释" in contract


def test_chapter_first_scene_map_excludes_prose_bearing_scene_fields() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        title="不进入正文的标题",
        scene_type="pressure",
        target_word_count=900,
        time_label="午时",
        participants=["许照川", "顾箬"],
        purpose={"story": "公开核验账目", "emotion": "手心发冷"},
        entry_state={"location": "主炉外圈", "ledger": "closed"},
        exit_state={"location": "主炉外圈", "ledger": "opened"},
        metadata_json={
            "methodology_contract": {
                "action_sequence": "许照川先敲三次钟，再把铜签拍在桌上，所有人同时回头。"
            }
        },
        forbidden_actions=["不能直接删名"],
        key_dialogue_beats=["顾箬压低声音说：这份账今天谁碰，谁就得把命留下。"],
        sensory_anchors={"sound": "钟声像铁屑一样刮过每个人的耳膜"},
        hook_requirement="铜签背面出现第二个名字",
        rewrite_hint="把上面的动作逐条扩写成三段",
    )

    rendered = _render_chapter_first_scene_cards([scene])

    assert "公开核验账目" in rendered
    assert '"ledger":{"from":"closed","to":"opened"}' in rendered
    assert "不进入正文的标题" not in rendered
    assert "谁就得把命留下" not in rendered
    assert "钟声像铁屑" not in rendered
    assert "先敲三次钟" not in rendered
    assert "逐条扩写" not in rendered
    assert len(rendered) <= 1400


def test_chapter_first_acceptance_contract_drops_duplicate_scene_payloads() -> None:
    payload = _chapter_first_acceptance_contract_payload(
        {
            "schema_version": "chapter-acceptance-contract.v1",
            "must_deliver": [{"label": "chapter_goal", "value": "完成核验"}],
            "scene_gate_targets": [{"visible_progress": "照搬场景句子"}],
            "methodology_application_contract": {
                "applications": [{"evidence": "再次照搬场景句子"}]
            },
            "ending_frame_contract": {"rule": "落在可见动作"},
        }
    )

    assert payload["must_deliver"][0]["value"] == "完成核验"
    assert "ending_frame_contract" in payload
    assert "scene_gate_targets" not in payload
    assert "methodology_application_contract" not in payload


def test_pipeline_request_exposes_book_level_chapter_first_controls() -> None:
    request = PipelineRequest(
        chapter_first=True,
        stop_on_chapter_failure=True,
    )

    assert request.chapter_first is True
    assert request.stop_on_chapter_failure is True


def test_chapter_first_writer_aim_is_optional_and_clamped_to_publish_band() -> None:
    project = SimpleNamespace(
        language="zh-CN",
        target_word_count=28_000,
        target_chapters=10,
        metadata_json={
            "words_per_chapter": {"min": 2500, "target": 2800, "max": 3500},
            "chapter_first_writer_aim": 3400,
        },
    )

    assert _chapter_first_writer_aim(project, 2800) == 3400

    project.metadata_json["chapter_first_writer_aim"] = 9999
    assert _chapter_first_writer_aim(project, 2800) == 3500

    del project.metadata_json["chapter_first_writer_aim"]
    assert _chapter_first_writer_aim(project, 2800) == 2800

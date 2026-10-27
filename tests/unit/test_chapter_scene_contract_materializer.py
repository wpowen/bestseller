from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services.chapter_scene_contract_materializer import (
    chapter_contract_has_adopted_source,
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

    report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    )

    contract = scene.metadata_json["scene_contract_materialized_projection"]["methodology_contract"]
    assert report.changed is True
    assert report.complete is True
    assert contract["pressure_stack"] == ["子时倒计时", "铜钱发烫"]
    assert contract["focus_character"] == "林渊"
    assert contract["stakes"].startswith("林渊若误判")
    assert contract["breakpoint"].startswith("电话里")
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["gate_function"].startswith("opening_pull")
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["visible_progress"].startswith("王建业求救电话")
    assert chapter.metadata_json["chapter_contract_materialized_scene_projection"]["complete"] is True


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

    report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    )

    assert report.changed is True
    assert report.complete is False
    unresolved = report.scene_changes[0].unresolved_fields
    assert "stakes" in unresolved
    assert "focus_character" in unresolved
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["gate_function"].startswith("opening_pull")


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
        metadata_json={
            "methodology_contract": {"pacing_mode": "accelerate"},
            "chapter_contract_materialized_fields": [
                "contract_summary",
                "opening_state.opening_situation",
                "core_conflict",
                "emotional_shift",
                "information_release",
                "closing_hook",
            ],
        },
    )

    report = materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )

    assert report.changed is True
    assert "opening_state.opening_situation" in report.filled_fields
    assert contract.contract_summary == "新章节任务"
    assert contract.opening_state["opening_situation"].startswith("新开篇")
    assert contract.core_conflict.startswith("新冲突")
    assert "父亲旧案重合" in contract.information_release
    assert contract.closing_hook.startswith("旧铜钥匙")
    assert contract.metadata_json["methodology_contract"]["pacing_mode"] == "accelerate"


def _chapter(**kwargs):
    return SimpleNamespace(
        **{
            "chapter_number": 2,
            "status": "planned",
            "production_state": "pending",
            "chapter_goal": "恢复轨道供水",
            "opening_situation": "泵站收到断流警报",
            "main_conflict": "抢修与疏散争用电力",
            "chapter_emotion_arc": "犹疑转为担责",
            "information_revealed": ["备用管路仍然完好"],
            "hook_description": "下一站失压",
            "metadata_json": {},
            **kwargs,
        }
    )


def _contract(**kwargs):
    return SimpleNamespace(
        **{
            "contract_summary": "",
            "opening_state": {"author_note": "retain"},
            "core_conflict": None,
            "emotional_shift": None,
            "information_release": None,
            "closing_hook": None,
            "metadata_json": {"author_note": "retain"},
            **kwargs,
        }
    )


def _scene(**kwargs):
    return SimpleNamespace(
        **{
            "scene_number": 2,
            "scene_type": "action",
            "participants": ["工程师"],
            "purpose": {"story": "接通临时管线", "reader_hook": "仪表出现异常波形"},
            "hook_requirement": "紧急隔离阀锁死",
            "metadata_json": {
                "author_note": "retain",
                "methodology_contract": {
                    "conflict_stakes": "居民区将停水",
                    "conflict_buffs": ["氧气限量"],
                    "spotlight_character": "工程师",
                    "information_control_mode": "先见现象",
                    "memorable_image": "蓝色水柱浮在失重舱",
                    "cut_point": "隔离阀指示灯熄灭",
                },
            },
            **kwargs,
        }
    )


def test_chapter_refresh_is_source_bound_targeted_historical_and_idempotent():
    chapter, contract = _chapter(), _contract()
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    original = deepcopy(contract.metadata_json)
    chapter.main_conflict = "抢修队必须先恢复备用电源"
    report = materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    assert report.filled_fields == ("core_conflict",)
    assert contract.core_conflict == chapter.main_conflict
    history = contract.metadata_json["chapter_contract_materialized_history"]
    assert len(history) == 1
    assert (
        history[0]["previous"]
        == original["chapter_contract_materialized_bindings"]["core_conflict"]
    )
    assert history[0]["previous"]["dependencies"]["chapter.main_conflict"] == "抢修与疏散争用电力"
    assert contract.opening_state["author_note"] == "retain"
    snapshot = deepcopy(vars(contract))
    assert not materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    ).changed
    assert vars(contract) == snapshot


@pytest.mark.parametrize(
    "field",
    [
        "contract_summary",
        "core_conflict",
        "emotional_shift",
        "information_release",
        "closing_hook",
    ],
)
def test_chapter_untracked_explicit_fields_survive_even_with_generic_materializer_stamp(field):
    chapter, contract = _chapter(), _contract(**{field: "作者明确要求"})
    contract.metadata_json["chapter_contract_materialized_by"] = "previous_materializer"
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    assert getattr(contract, field) == "作者明确要求"
    assert field not in contract.metadata_json["chapter_contract_materialized_bindings"]


def test_explicit_opening_and_other_nested_keys_are_preserved():
    chapter, contract = (
        _chapter(),
        _contract(
            opening_state={
                "opening_situation": "用户指定开场",
                "time": {"hour": 12},
            }
        ),
    )
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    assert contract.opening_state == {"opening_situation": "用户指定开场", "time": {"hour": 12}}


@pytest.mark.parametrize("override", ["作者改写冲突", "", None])
def test_chapter_user_edit_detaches_ownership_including_empty_override(override):
    chapter, contract = _chapter(), _contract()
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    contract.core_conflict = override
    chapter.main_conflict = "替换来源"
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    snapshot = deepcopy(vars(contract))
    assert contract.core_conflict == override
    assert not materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    ).changed
    assert vars(contract) == snapshot


def test_empty_chapter_sources_retire_only_derived_fields_and_can_be_repopulated():
    chapter, contract = _chapter(), _contract(closing_hook="作者钩子")
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    chapter.information_revealed = []
    chapter.hook_description = None
    report = materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    assert report.filled_fields == ("information_release",)
    assert contract.information_release == ""
    assert contract.closing_hook == "作者钩子"
    assert not materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    ).changed
    chapter.information_revealed = ["发现另一条通道"]
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    assert contract.information_release == "发现另一条通道"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("蓝色水柱浮在失重舱", "维修机器人托起一滴水"),
        ("出土陶片拼成鸟纹", "断裂的青铜器重新扣合"),
        ("证人交出一张货运单", "法庭屏幕显示两份矛盾账簿"),
    ],
)
def test_unrelated_subjects_refresh_scene_alias_and_transitive_payoff_without_self_feed(old, new):
    chapter, scene = _chapter(), _scene()
    scene.metadata_json["methodology_contract"]["memorable_image"] = old
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    initial = deepcopy(scene.metadata_json)
    scene.metadata_json["methodology_contract"]["memorable_image"] = new
    report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    )
    assert set(report.scene_changes[0].filled_fields) == {"signature_image", "reader_payoff"}
    assert scene.metadata_json["scene_contract_materialized_projection"]["methodology_contract"]["signature_image"] == new
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["reader_payoff"] == new
    history = scene.metadata_json["scene_contract_materialized_history"]
    assert {row["field"] for row in history} == {"signature_image", "reader_payoff"}
    assert history[0]["previous"]["dependencies"]["contract.memorable_image"] == old
    assert initial["scene_contract_materialized_projection"]["methodology_contract"]["signature_image"] == old
    # Persisted JSON round-trip must retain the same ownership and idempotency.
    scene.metadata_json = json.loads(json.dumps(scene.metadata_json))
    snapshot = deepcopy((chapter.metadata_json, scene.metadata_json))
    assert not materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    ).changed
    assert (chapter.metadata_json, scene.metadata_json) == snapshot


def test_scene_source_changes_refresh_all_owned_aliases_and_gate_fields():
    chapter, scene = _chapter(), _scene()
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    scene.metadata_json["methodology_contract"].update(
        {
            "conflict_stakes": "粮仓将断供",
            "conflict_buffs": ["风暴来袭"],
            "spotlight_character": "领航员",
            "information_control_mode": "证据晚揭示",
            "cut_point": "对接舱门弹开",
        }
    )
    scene.purpose["story"] = "调转货运船"
    scene.scene_type = "reveal"
    report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    )
    assert set(report.scene_changes[0].filled_fields) == {
        "stakes",
        "pressure_stack",
        "focus_character",
        "reveal_mode",
        "breakpoint",
        "gate_function",
        "visible_progress",
        "ending_hook_payload",
    }
    assert scene.metadata_json["scene_contract_materialized_projection"]["methodology_contract"]["pressure_stack"] == ["风暴来袭"]
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["visible_progress"] == "调转货运船"
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["ending_hook_payload"] == "对接舱门弹开"
    assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"]["gate_function"].startswith("information_release")


def test_legacy_field_list_without_value_binding_cannot_erase_raw_explicit_or_unknown_fields():
    chapter, scene = _chapter(), _scene()
    chapter.metadata_json = {"chapter_scene_contract_materialization": {
        "source": "legacy", "scene_changes": [{"scene_number": 2,
            "filled_fields": ["stakes", "visible_progress"]}]}}
    scene.metadata_json["methodology_contract"].update(
        {"stakes": "未绑定的旧字段", "focus_character": "作者指定人"})
    scene.metadata_json["visible_progress"] = "未绑定的旧进度"
    original = deepcopy(scene.metadata_json["methodology_contract"])
    report = materialize_chapter_scene_contracts(methodology_selected=True,
        chapter=chapter, scenes=[scene], verified_adoption=False)
    assert report.changed
    assert scene.metadata_json["methodology_contract"] == original
    assert scene.metadata_json["visible_progress"] == "未绑定的旧进度"
    assert scene.metadata_json["scene_contract_materialized_history"] == []
    assert not materialize_chapter_scene_contracts(methodology_selected=True,
        chapter=chapter, scenes=[scene], verified_adoption=False).changed


def test_scene_generic_stamp_does_not_grant_ownership_of_explicit_values():
    chapter, scene = _chapter(), _scene()
    scene.metadata_json.update(
        {
            "chapter_scene_contract_materialized_by": "legacy",
            "reader_payoff": "用户要求的回报",
            "focus_character": "用户指定视角",
        }
    )
    scene.metadata_json["methodology_contract"]["stakes"] = "用户定义的风险"
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    assert scene.metadata_json["reader_payoff"] == "用户要求的回报"
    assert scene.metadata_json["methodology_contract"]["stakes"] == "用户定义的风险"
    assert "focus_character" not in scene.metadata_json["methodology_contract"]


@pytest.mark.parametrize("override", ["用户覆写", "", None])
def test_scene_manual_edit_survives_source_refresh_and_repeated_calls(override):
    chapter, scene = _chapter(), _scene()
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    scene.metadata_json["methodology_contract"]["stakes"] = override
    scene.metadata_json["reader_payoff"] = override
    scene.metadata_json["methodology_contract"]["conflict_stakes"] = "新版风险"
    scene.metadata_json["methodology_contract"]["memorable_image"] = "新版画面"
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    snapshot = deepcopy(scene.metadata_json)
    assert scene.metadata_json["methodology_contract"]["stakes"] == override
    assert scene.metadata_json["reader_payoff"] == override
    assert not materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    ).changed
    assert scene.metadata_json == snapshot


def test_scene_removed_sources_cannot_resurrect_old_image_or_hook_from_derived_fields():
    chapter, scene = _chapter(), _scene()
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    source_contract = scene.metadata_json["methodology_contract"]
    del source_contract["memorable_image"]
    del source_contract["cut_point"]
    scene.purpose = {}
    scene.hook_requirement = None
    report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    )
    assert not report.complete
    assert {"signature_image", "breakpoint"} <= set(report.scene_changes[0].unresolved_fields)
    for key in ("reader_payoff", "ending_hook_payload", "visible_progress"):
        assert scene.metadata_json["scene_contract_materialized_projection"]["gate_fields"][key] == ""
    assert not materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    ).changed


@pytest.mark.parametrize(
    "state",
    [
        {"status": "complete"},
        {"production_state": "ok"},
        {"production_state": "quality_debt"},
        {
            "status": "revision",
            "production_state": "blocked",
            "metadata_json": {
                "numeric_promotion_debt": {"draft_id": "adopted-version"},
            },
        },
    ],
)
def test_adopted_chapter_models_are_immutable_in_both_entrypoints(state):
    from bestseller.infra.db.models import ChapterModel

    # Actual mapped fields, no database/session or model generation involved.
    chapter = ChapterModel(**vars(_chapter(**state)))
    contract, scene = _contract(), _scene()
    snapshot = deepcopy((chapter.metadata_json, vars(contract), vars(scene)))
    assert not materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=True
    ).changed
    report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=True
    )
    assert not report.changed and not report.complete
    assert (chapter.metadata_json, vars(contract), vars(scene)) == snapshot


def test_later_chapters_and_missing_contract_remain_out_of_scope():
    chapter, scene = _chapter(chapter_number=11), _scene()
    snapshot = deepcopy((chapter.metadata_json, scene.metadata_json))
    assert not materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    ).changed
    assert (chapter.metadata_json, scene.metadata_json) == snapshot
    assert not materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=None, verified_adoption=False
    ).changed


@pytest.mark.parametrize("verified", [False])
@pytest.mark.parametrize(
    "state",
    [
        {"production_state": "ok"},
        {"status": "complete", "production_state": "quality_debt"},
    ],
)
def test_candidate_only_status_labels_do_not_prevent_refresh(verified, state):
    chapter, contract, scene = _chapter(**state), _contract(), _scene()
    materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    )
    materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], verified_adoption=False)
    chapter.main_conflict = "新规划冲突"
    scene.metadata_json["methodology_contract"]["conflict_stakes"] = "新规划风险"
    assert materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=verified
    ).changed
    assert materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=verified
    ).changed
    assert contract.core_conflict == "新规划冲突"
    assert scene.metadata_json["scene_contract_materialized_projection"]["methodology_contract"]["stakes"] == "新规划风险"


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"numeric_promotion_debt": None},
        {"numeric_promotion_debt": {"draft_id": "unverified"}},
        {"numeric_promotion_debt": {}},
        {"numeric_promotion_debt": "invalid-receipt"},
    ],
)
@pytest.mark.parametrize("verification", [{}, {"verified_adoption": None}])
@pytest.mark.parametrize("entrypoint", ["chapter", "scene"])
def test_missing_verification_raises_before_mutation_with_or_without_numeric(
    metadata,
    verification,
    entrypoint,
):
    chapter = _chapter(metadata_json=metadata)
    contract, scene = _contract(), _scene()
    snapshot = deepcopy((vars(chapter), vars(contract), vars(scene)))
    with pytest.raises(ValueError, match=r"^materializer_adoption_verification_required$"):
        if entrypoint == "chapter":
            materialize_chapter_contract_from_chapter(
                chapter=chapter,
                chapter_contract=contract,
                **verification,
            )
        else:
            materialize_chapter_scene_contracts(methodology_selected=True, chapter=chapter, scenes=[scene], **verification)
    assert (vars(chapter), vars(contract), vars(scene)) == snapshot


def test_verified_false_allows_invalid_numeric_marker_without_erasing_it():
    chapter = _chapter(metadata_json={"numeric_promotion_debt": {"draft_id": "invalid"}})
    contract, scene = _contract(), _scene()
    assert materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=False
    ).changed
    assert materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=False
    ).changed
    assert chapter.metadata_json["numeric_promotion_debt"] == {"draft_id": "invalid"}


@pytest.mark.parametrize("verified", ["False", "True", "", 0, 1, [], {}, object()])
@pytest.mark.parametrize("entrypoint", ["chapter", "scene"])
def test_invalid_verification_type_raises_before_any_mutation(verified, entrypoint):
    chapter, contract, scene = _chapter(), _contract(), _scene()
    snapshot = deepcopy((vars(chapter), vars(contract), vars(scene)))
    with pytest.raises(TypeError, match=r"^verified_adoption must be bool or None$"):
        if entrypoint == "chapter":
            materialize_chapter_contract_from_chapter(
                chapter=chapter, chapter_contract=contract, verified_adoption=verified
            )
        else:
            materialize_chapter_scene_contracts(methodology_selected=True, 
                chapter=chapter, scenes=[scene], verified_adoption=verified
            )
    assert (vars(chapter), vars(contract), vars(scene)) == snapshot


@pytest.mark.parametrize("kind", ["historical_promoted", "numeric", "candidate_only"])
@pytest.mark.asyncio
async def test_selector_result_controls_both_materializers_with_lock_false(monkeypatch, kind):
    from bestseller.services import adopted_chapter_source

    chapter = _chapter(production_state="ok")
    chapter.id, chapter.project_id = uuid4(), uuid4()
    contract, scene = _contract(), _scene()
    adopted = None
    receipt = None
    if kind == "historical_promoted":
        # The working candidate may own is_current; the selector still resolves
        # the historical promoted source. The helper must not recheck is_current.
        adopted = SimpleNamespace(promotion_state="promoted", is_current=False)
    elif kind == "numeric":
        adopted = SimpleNamespace(promotion_state="candidate", is_current=True)
        receipt = {"draft_id": "verified-numeric"}
        chapter.metadata_json["numeric_promotion_debt"] = deepcopy(receipt)
    selector = AsyncMock(return_value=(adopted, receipt))
    monkeypatch.setattr(adopted_chapter_source, "select_adopted_chapter_source", selector)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]))
    verified = await chapter_contract_has_adopted_source(session, chapter=chapter)
    selector.assert_awaited_once_with(session, chapter=chapter, lock=False)
    assert verified is (kind != "candidate_only")
    snapshot = deepcopy((vars(chapter), vars(contract), vars(scene)))
    chapter_report = materialize_chapter_contract_from_chapter(
        chapter=chapter, chapter_contract=contract, verified_adoption=verified
    )
    scene_report = materialize_chapter_scene_contracts(methodology_selected=True, 
        chapter=chapter, scenes=[scene], verified_adoption=verified
    )
    if verified:
        assert not chapter_report.changed and not scene_report.changed
        assert (vars(chapter), vars(contract), vars(scene)) == snapshot
    else:
        assert chapter_report.changed and scene_report.changed


@pytest.mark.parametrize("case", [
    "missing_times", "equal_times", "bad_numeric", "missing_numeric_source",
    "empty_numeric", "null_numeric", "null_numeric_with_promoted", "candidate_only_ok",
])
@pytest.mark.asyncio
async def test_unresolved_selector_evidence_is_ambiguous_not_unadopted(case):
    from datetime import UTC, datetime

    chapter = _chapter(production_state="ok")
    chapter.id, chapter.project_id = uuid4(), uuid4()
    promoted = []
    if case in {"missing_times", "equal_times", "null_numeric_with_promoted"}:
        stamp = None if case == "missing_times" else datetime(2026, 1, 1, tzinfo=UTC)
        promoted = [SimpleNamespace(id=uuid4(), project_id=chapter.project_id,
            chapter_id=chapter.id, promotion_state="promoted", is_current=False,
            promoted_at=stamp, content_md="The pilot records the harbour signal.")
            for _ in range(1 if case == "null_numeric_with_promoted" else 2)]
    debt_markers = {
        "bad_numeric": {"draft_id": "not-a-uuid"},
        "missing_numeric_source": {"draft_id": str(uuid4())},
        "empty_numeric": {}, "null_numeric": None, "null_numeric_with_promoted": None,
    }
    if case in debt_markers:
        chapter.metadata_json["numeric_promotion_debt"] = debt_markers[case]
    # Exercise the real selector's unresolved branch and the complete promoted
    # inventory; a limited existence probe cannot prove all sources rejected.
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=promoted),
        scalar=AsyncMock(return_value=promoted[0].id if promoted else None),
    )
    snapshot = deepcopy(vars(chapter))
    if case == "null_numeric_with_promoted":
        assert await chapter_contract_has_adopted_source(session, chapter=chapter) is True
    elif case in {"candidate_only_ok", "null_numeric"}:
        assert await chapter_contract_has_adopted_source(session, chapter=chapter) is False
        statement = session.scalars.call_args.args[0]
        assert statement.compile().params == {
            "project_id_1": chapter.project_id, "chapter_id_1": chapter.id,
            "promotion_state_1": "promoted",
        }
        assert statement._limit_clause is None
        assert statement.get_execution_options()["autoflush"] is False
    else:
        for _ in range(2):
            with pytest.raises(ValueError, match=r"^materializer_adoption_ambiguous$"):
                await chapter_contract_has_adopted_source(session, chapter=chapter)
    assert vars(chapter) == snapshot


@pytest.mark.parametrize("methodology", [None, {}, {"conflict_stakes": "Creator locked clause", "cut_point": "Keep the ending"}])
@pytest.mark.parametrize("selection", [False, True])
def test_prefix_bound_source_remains_exact_in_both_entrypoints(methodology, selection):
    from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
    chapter = _chapter(metadata_json={"outline_prefix_readiness": {
        "artifact_id": str(uuid4()), "workflow_run_id": str(uuid4()), "projection_hash": "original-marker"}})
    scene = _scene(metadata_json={"methodology_contract": deepcopy(methodology)})
    contract = _contract()
    before = deepcopy((vars(chapter), vars(scene), vars(contract)))
    digest = outline_prefix_projection_hash(chapter, [scene])
    assert not materialize_chapter_contract_from_chapter(chapter=chapter, chapter_contract=contract,
        verified_adoption=False, source_plan_locked=False).changed
    assert not materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene],
        verified_adoption=False, methodology_selected=selection, source_plan_locked=False).changed
    assert (vars(chapter), vars(scene), vars(contract)) == before
    assert outline_prefix_projection_hash(chapter, [scene]) == digest


@pytest.mark.parametrize("story,participant,hook", [
    ("Repair the orbiting antenna", "Engineer", "A second signal appears"),
    ("Examine the sealed archive", "Archivist", "The handwriting changes"),
    ("Deliver the wedding invitation", "Courier", "The addressee has left"),
])
def test_unselected_methodology_never_invented_from_purpose_participant_or_hook(story, participant, hook):
    chapter = _chapter()
    scene = _scene(purpose={"story": story}, participants=[participant], hook_requirement=hook,
        metadata_json={"methodology_contract": None, "author_note": "literal"})
    before = deepcopy((vars(chapter), vars(scene)))
    report = materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene], verified_adoption=False)
    assert not report.changed
    assert (vars(chapter), vars(scene)) == before


def test_selected_runtime_projection_is_separate_and_raw_explicit_contract_is_immutable():
    chapter, scene = _chapter(), _scene()
    original = deepcopy(scene.metadata_json["methodology_contract"])
    report = materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene],
        verified_adoption=False, methodology_selected=True)
    assert report.changed
    assert scene.metadata_json["methodology_contract"] == original
    projection = scene.metadata_json["scene_contract_materialized_projection"]
    assert projection["schema"] == "scene-contract-runtime.v1"
    assert projection["methodology_contract"]["stakes"] == original["conflict_stakes"]
    assert "gate_function" not in scene.metadata_json
    assert projection["gate_fields"]["visible_progress"] == scene.purpose["story"]
    snapshot = deepcopy((chapter.metadata_json, scene.metadata_json))
    assert not materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene],
        verified_adoption=False, methodology_selected=True).changed
    assert (chapter.metadata_json, scene.metadata_json) == snapshot


def test_runtime_cache_retirement_preserves_raw_history_and_rebuilds_without_self_feeding():
    chapter, scene = _chapter(), _scene()
    original = deepcopy(scene.metadata_json["methodology_contract"])
    materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene],
        verified_adoption=False, methodology_selected=True)
    previous = deepcopy(scene.metadata_json["scene_contract_materialized_projection"])
    retired = materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene], verified_adoption=False)
    assert retired.changed and not retired.complete
    assert "scene_contract_materialized_projection" not in scene.metadata_json
    assert scene.metadata_json["scene_contract_materialized_history"][-1]["previous"] == previous
    assert scene.metadata_json["methodology_contract"] == original
    snapshot = deepcopy(scene.metadata_json)
    assert not materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene], verified_adoption=False).changed
    assert scene.metadata_json == snapshot
    scene.metadata_json["methodology_contract"]["conflict_stakes"] = "New explicit stakes"
    materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene],
        verified_adoption=False, methodology_selected=True)
    assert scene.metadata_json["scene_contract_materialized_projection"]["methodology_contract"]["stakes"] == "New explicit stakes"
    assert previous["methodology_contract"]["stakes"] != "New explicit stakes"


@pytest.mark.parametrize("override", [False, True])
def test_owned_legacy_raw_cache_retirement_is_value_bound_and_keeps_user_overrides(override):
    chapter, scene = _chapter(), _scene()
    scene.metadata_json["methodology_contract"]["stakes"] = "User override" if override else "Old derived stakes"
    receipt = {"value": "Old derived stakes", "dependencies": {"purpose.story": "old"}, "source": "old-producer"}
    scene.metadata_json["chapter_scene_contract_materialized_bindings"] = {"stakes": deepcopy(receipt)}
    original_source = scene.metadata_json["methodology_contract"]["conflict_stakes"]
    materialize_chapter_scene_contracts(chapter=chapter, scenes=[scene], verified_adoption=False)
    assert scene.metadata_json["methodology_contract"]["conflict_stakes"] == original_source
    if override:
        assert scene.metadata_json["methodology_contract"]["stakes"] == "User override"
    else:
        assert "stakes" not in scene.metadata_json["methodology_contract"]
        assert scene.metadata_json["scene_contract_materialized_history"][-1]["previous"] == receipt
    assert scene.metadata_json["chapter_scene_contract_materialized_bindings"]["stakes"] == receipt


@pytest.mark.parametrize("corrupt", [None, "chapter", "scene"])
@pytest.mark.asyncio
async def test_actual_pipeline_materializer_segment_checks_before_checkpoint_and_rolls_back(corrupt):
    """Execute the real pipeline block with fault injection; no model/PG boundary."""
    import ast
    from pathlib import Path
    from sqlalchemy import select
    from bestseller.domain.enums import WorkflowStatus
    from bestseller.infra.db.models import ChapterContractModel
    from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
    path = Path(__file__).parents[2] / "src/bestseller/services/pipelines.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_chapter_pipeline")
    blocks = [node for node in ast.walk(function) if isinstance(node, ast.If) and any(
        isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
        and item.value.value == "chapter_scene_contract_materializer" for item in node.body)]
    assert len(blocks) == 1
    chapter, scene, contract = _chapter(), _scene(metadata_json={"methodology_contract": None}), _contract()
    chapter.id, chapter.project_id = uuid4(), uuid4()
    marker = {"artifact_id": str(uuid4()), "projection_hash": outline_prefix_projection_hash(chapter, [scene])}
    chapter.metadata_json["outline_prefix_readiness"] = deepcopy(marker)
    initial = deepcopy((vars(chapter), vars(scene), vars(contract)))
    events = []
    class Savepoint:
        async def __aenter__(self): events.append("savepoint"); return self
        async def __aexit__(self, exc_type, exc, tb):
            if exc_type:
                for obj, fields in zip((chapter, scene, contract), initial):
                    vars(obj).clear(); vars(obj).update(deepcopy(fields))
                events.append("rollback")
            return False
    class Session:
        async def scalar(self, query): return contract
        def begin_nested(self): return Savepoint()
        async def flush(self): events.append("flush")
    async def adoption(*args, **kwargs): return False
    async def verify(*args, **kwargs):
        events.append("verify")
        if outline_prefix_projection_hash(chapter, [scene]) != marker["projection_hash"]:
            raise ValueError("prefix_materialized_projection_changed")
    async def step(*args, **kwargs): events.append("step")
    async def checkpoint(*args, **kwargs): events.append("checkpoint")
    def chapter_sync(**kwargs):
        if corrupt == "chapter": chapter.chapter_goal = "Unauthorized change"
        return materialize_chapter_contract_from_chapter(**kwargs)
    def scene_sync(**kwargs):
        if corrupt == "scene": scene.metadata_json["methodology_contract"] = {"conflict_stakes": "Unauthorized"}
        return materialize_chapter_scene_contracts(**kwargs)
    environment = {"select": select, "ChapterContractModel": ChapterContractModel, "WorkflowStatus": WorkflowStatus,
        "UUID": __import__("uuid").UUID, "chapter_contract_has_adopted_source": adoption,
        "materialize_chapter_contract_from_chapter": chapter_sync,
        "materialize_chapter_scene_contracts": scene_sync, "verify_outline_prefix_readiness": verify,
        "create_workflow_step_run": step, "_checkpoint_commit": checkpoint,
        "session": Session(), "project": SimpleNamespace(id=chapter.project_id, target_chapters=50, metadata_json={}),
        "chapter": chapter, "scenes": [scene], "workflow_run": SimpleNamespace(id=uuid4(), metadata_json={}),
        "settings": SimpleNamespace(), "prefix_marker": marker, "prefix_source": None,
        "step_order": 0}
    body = ast.AsyncFunctionDef(name="segment", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
        kw_defaults=[], defaults=[]), body=[ast.Assign(targets=[ast.Name(id="step_order", ctx=ast.Store())],
            value=ast.Constant(value=0)), *blocks[0].body], decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[body], type_ignores=[])), str(path), "exec"), environment)
    if corrupt:
        with pytest.raises(ValueError, match="prefix_materialized_projection_changed"):
            await environment["segment"]()
        assert events == ["savepoint", "verify", "rollback"]
        assert (vars(chapter), vars(scene), vars(contract)) == initial
    else:
        await environment["segment"]()
        assert events.index("verify") < events.index("step") < events.index("checkpoint")
        assert (vars(chapter), vars(scene), vars(contract)) == initial

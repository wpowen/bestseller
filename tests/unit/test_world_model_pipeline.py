# ruff: noqa: RUF001, E501

from __future__ import annotations

from types import SimpleNamespace

from bestseller.domain.world_model import world_model_from_dict
from bestseller.services.world_law_consistency_gate import (
    _parse_judge_violations,
    check_world_law_consistency_gate,
    detect_tier_violations,
    detect_world_law_violations,
)
from bestseller.services.world_model_injection import (
    build_active_law_prose_block_for_scene,
    extract_world_model,
    render_active_law_block,
    select_active_laws,
)
from bestseller.services.world_ripple import (
    apply_ripples_to_kernel,
    compute_state_ripples,
)

# A representative derived world model reused across the pipeline tests.
WORLD_MODEL = {
    "axioms": ["灵力成为唯一能源", "人人可飞"],
    "baseline": "现代都市社会",
    "world_laws": [
        {
            "dimension": "mobility_and_transport",
            "delta": "人人可飞,地面汽车贬值,空域成稀缺",
            "order": 2,
            "derived_from": ["人人可飞"],
            "enforcement": "默认出行为飞行;出现地面车辆通勤须显式给理由",
            "story_use": "出行方式改变重塑城市",
        },
        {
            "dimension": "value_and_currency",
            "delta": "灵石成为硬通货",
            "order": 2,
            "derived_from": ["灵力成为唯一能源"],
            "enforcement": "交易须以灵石计价;不得使用纸币现金",
            "story_use": "谁控制灵脉谁掌握经济",
        },
        {
            "dimension": "life_death_and_time",
            "delta": "灵力枯竭即死亡",
            "order": 3,
            "derived_from": ["灵力成为唯一能源"],
            "enforcement": "角色透支灵力须付出可见代价",
            "story_use": "代价系统",
        },
    ],
    "fault_lines": [
        {
            "name": "空域管制×自由飞行",
            "tension": "管制权与个体飞行自由冲突",
            "world_law_refs": ["mobility_and_transport"],
            "used_by_protagonist": True,
        }
    ],
}


# ---------------------------------------------------------------------------
# Task 5 — active-law selection + injection
# ---------------------------------------------------------------------------


def test_extract_world_model_finds_nested_and_top_level() -> None:
    assert extract_world_model({"world_model": WORLD_MODEL})["baseline"] == "现代都市社会"
    nested = {"story_design_kernel": {"world_model": WORLD_MODEL}}
    assert extract_world_model(nested)["baseline"] == "现代都市社会"
    assert extract_world_model({}) is None
    assert extract_world_model(None) is None


def test_select_active_laws_prefers_context_relevant() -> None:
    laws = select_active_laws(WORLD_MODEL, context_text="主角驾驭飞行穿过空域与地面车辆", max_laws=1)
    assert len(laws) == 1
    assert laws[0].dimension == "mobility_and_transport"


def test_select_active_laws_caps_and_backfills_without_context() -> None:
    laws = select_active_laws(WORLD_MODEL, context_text="", max_laws=2)
    assert len(laws) == 2  # backfilled even with no signal
    laws_all = select_active_laws(WORLD_MODEL, context_text="", max_laws=10)
    assert len(laws_all) == 3  # never exceeds available


def test_select_active_laws_empty_for_missing_model() -> None:
    assert select_active_laws(None, context_text="x") == []
    assert select_active_laws({"world_laws": []}, context_text="x") == []


def test_render_active_law_block_surfaces_enforcement() -> None:
    laws = select_active_laws(WORLD_MODEL, context_text="灵石交易", max_laws=1)
    block = render_active_law_block(laws, language="zh")
    assert "世界规律" in block
    assert "灵石" in block
    assert render_active_law_block([]) == ""


def test_build_block_for_scene_reads_project_metadata() -> None:
    project = SimpleNamespace(metadata_json={"world_model": WORLD_MODEL})
    chapter = SimpleNamespace(
        chapter_goal="主角驾驭飞行逃离空域管制",
        main_conflict="与空域管制冲突",
        opening_situation=None,
        location_tag="高空",
        title="第一章",
        information_revealed=[],
    )
    scene = SimpleNamespace(scene_type="action", purpose={"goal": "飞行追逐"})
    block = build_active_law_prose_block_for_scene(project, chapter, scene)
    assert "mobility_and_transport" in block
    # no world model → empty, never raises
    empty_project = SimpleNamespace(metadata_json={})
    assert build_active_law_prose_block_for_scene(empty_project, chapter, scene) == ""


# ---------------------------------------------------------------------------
# Task 6 — world-law consistency gate (advisory)
# ---------------------------------------------------------------------------


def test_detect_flags_missing_justification() -> None:
    model = world_model_from_dict(WORLD_MODEL)
    prose = "他懒得飞，驾着地面车辆通勤穿过拥挤的城市街道。"  # trigger present, no reason
    violations = detect_world_law_violations(prose, model.world_laws)
    assert any(v.kind == "missing_justification" for v in violations)


def test_detect_clears_when_justification_present() -> None:
    model = world_model_from_dict(WORLD_MODEL)
    prose = "因为灵力被封印无法飞行，他只能改乘地面车辆通勤穿过城市。"  # reason present
    violations = detect_world_law_violations(prose, model.world_laws)
    assert not any(
        v.kind == "missing_justification" and v.dimension == "mobility_and_transport"
        for v in violations
    )


def test_detect_flags_prohibition() -> None:
    model = world_model_from_dict(WORLD_MODEL)
    prose = "她从钱包里掏出几张纸币现金递了过去。"  # 不得使用纸币现金
    violations = detect_world_law_violations(prose, model.world_laws)
    assert any(v.kind == "prohibition" for v in violations)


def test_gate_is_advisory_and_safe() -> None:
    prose = "他开车通勤穿过城市。"
    report = check_world_law_consistency_gate(
        prose, chapter_position=5, world_model=WORLD_MODEL
    )
    checker = report.to_checker_report()
    assert checker.metrics["active_law_count"] >= 1
    # no world model / empty text → trivially passes, never raises
    assert check_world_law_consistency_gate("", world_model=WORLD_MODEL).passed
    assert check_world_law_consistency_gate("文本", world_model=None).passed


def test_detect_tier_violations_flags_numeric_contradiction() -> None:
    model = world_model_from_dict(
        {
            "axioms": ["境界决定寿元"],
            "world_laws": [
                {
                    "dimension": "life_death_and_time",
                    "delta": "寿元随境界增长",
                    "enforcement": "寿元须符合境界",
                    "derived_from": ["境界决定寿元"],
                    "tiers": [{"tier": "筑基", "value": "三百岁"}],
                }
            ],
        }
    )
    bad = detect_tier_violations("他筑基之后,寿元可达四百年。", model.world_laws)
    assert any(v.kind == "tier_mismatch" for v in bad)
    ok = detect_tier_violations("他筑基之后,寿元三百年。", model.world_laws)
    assert not ok  # matches the ladder → no violation


def test_parse_judge_violations_validates_dimensions() -> None:
    model = world_model_from_dict(WORLD_MODEL)
    content = (
        '```json\n{"violations":[{"dimension":"value_and_currency","reason":"用纸币交易"},'
        '{"dimension":"not_a_real_dim","reason":"x"}]}\n```'
    )
    viols = _parse_judge_violations(content, model.world_laws)
    assert len(viols) == 1  # the bogus dimension is dropped
    assert viols[0].dimension == "value_and_currency"
    assert viols[0].kind == "semantic"
    assert _parse_judge_violations('{"violations":[]}', model.world_laws) == []


def test_gate_accepts_injected_judge() -> None:
    sentinel = list(
        detect_world_law_violations(
            "他开车通勤", world_model_from_dict(WORLD_MODEL).world_laws
        )
    )

    def judge(text, laws):
        return sentinel

    report = check_world_law_consistency_gate(
        "任意正文", chapter_position=1, world_model=WORLD_MODEL, judge=judge
    )
    assert report.active_law_count >= 1


# ---------------------------------------------------------------------------
# Task 7 — dynamic ripple layer
# ---------------------------------------------------------------------------

STATE_VARS = [
    {
        "key": "空域管制强度",
        "variable_type": "risk",
        "current_value": "",
        "desired_direction": "升高",
        "change_triggers": ["主角违规飞行被巡查发现", "空域稽查队出动"],
        "failure_mode": "停滞则冲突无升级",
    },
    {
        "key": "灵石储备",
        "variable_type": "resource",
        "current_value": "充足",
        "desired_direction": "下降",
        "change_triggers": ["大额灵石支付", "灵脉被夺"],
        "failure_mode": "停滞则经济无张力",
    },
]


def test_compute_state_ripples_fires_only_on_contact() -> None:
    text = "主角违规飞行被巡查发现，惊动了整片城区。"
    updates = compute_state_ripples(STATE_VARS, text, chapter_number=7)
    keys = {u["key"] for u in updates}
    assert "空域管制强度" in keys
    assert "灵石储备" not in keys  # no trigger contact
    upd = next(u for u in updates if u["key"] == "空域管制强度")
    assert "第7章" in upd["current_value"]
    assert upd["previous_value"] == ""


def test_compute_state_ripples_no_contact_returns_empty() -> None:
    assert compute_state_ripples(STATE_VARS, "一段无关的日常描写。", chapter_number=3) == []
    assert compute_state_ripples(STATE_VARS, "", chapter_number=3) == []


def test_apply_ripples_to_kernel_advances_state() -> None:
    kernel = {"worldview_kernel": {"state_variables": [dict(v) for v in STATE_VARS]}}
    text = "大额灵石支付之后，库房肉眼可见地空了下去。"
    updated, updates = apply_ripples_to_kernel(kernel, text, chapter_number=12)
    assert len(updates) == 1
    new_vars = {v["key"]: v for v in updated["worldview_kernel"]["state_variables"]}
    assert new_vars["灵石储备"]["last_updated_chapter"] == 12
    assert "第12章" in new_vars["灵石储备"]["current_value"]
    # original kernel dict untouched (immutability)
    assert kernel["worldview_kernel"]["state_variables"][1]["current_value"] == "充足"


def test_apply_ripples_safe_on_malformed_kernel() -> None:
    assert apply_ripples_to_kernel({}, "text", chapter_number=1) == ({}, [])
    _k, u = apply_ripples_to_kernel({"worldview_kernel": {}}, "text", chapter_number=1)
    assert u == []


def test_compute_state_ripples_cascades_and_is_directional() -> None:
    """A milestone must propagate causally across variables, not stamp one note."""

    state_vars = [
        {
            "key": "韩立境界",
            "current_value": "炼气期",
            "desired_direction": "提升",
            "change_triggers": ["突破筑基", "筑基成功"],
            "cascades_to": ["韩立寿元", "御器飞行解锁"],
        },
        {"key": "韩立寿元", "current_value": "约百年", "desired_direction": "增长", "change_triggers": ["寿元增长"]},
        {"key": "御器飞行解锁", "current_value": "未解锁", "desired_direction": "解锁", "change_triggers": ["筑基成功"]},
        {"key": "无关变量", "current_value": "", "desired_direction": "升高", "change_triggers": ["毫不相干的事"]},
    ]
    updates = compute_state_ripples(state_vars, "他终于突破筑基,灵力暴涨。", chapter_number=42)
    keys = {u["key"] for u in updates}
    # primary (境界) + cascaded (寿元, 飞行解锁), but NOT the unrelated var
    assert {"韩立境界", "韩立寿元", "御器飞行解锁"} <= keys
    assert "无关变量" not in keys
    realm_upd = next(u for u in updates if u["key"] == "韩立境界")
    life_upd = next(u for u in updates if u["key"] == "韩立寿元")
    assert realm_upd["cascaded_from"] is None  # primary
    assert life_upd["cascaded_from"] == "韩立境界"  # causal propagation
    assert "提升" in realm_upd["current_value"]  # directional, not a bare timestamp

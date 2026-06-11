# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.domain.anti_commonsense_hook import HookSpec
from bestseller.services.hook_propagation import (
    apply_hook_to_book_spec,
    apply_hook_to_volume_plan,
    apply_hook_to_world_spec,
    hook_outline_extra_constraints,
    render_hook_spec_prompt_block,
)


def _spec() -> HookSpec:
    return HookSpec(
        mechanism_key="emotion_value",
        genre="都市",
        setting_locale=None,
        protagonist_role="主角",
        base_desire="证明清白",
        reversal="必须引爆指定情绪才能让证据显影",
        rewards=("证据显影",),
        constraints={"emotion": "只有真实情绪波动才结算", "ban": "虚假表演不结算"},
        anti_cheat=("刷同一人的情绪会衰减",),
        costs=("温暖记忆被扣除",),
        misunderstanding="旁人以为主角在炒作",
        arc_engine=("情绪种类", "公众场域", "代价亲密度"),
        one_liner="主角想证明清白，却必须引爆他人情绪；赢来证据显影，也付出温暖记忆。",
        core_rule="情绪越真实，证据越清晰；每次使用都会扣除一段温暖记忆。",
    )


def test_apply_hook_to_book_and_world_spec() -> None:
    spec = _spec()

    book = apply_hook_to_book_spec({"title": "旧标题", "series_engine": {}}, spec)
    world = apply_hook_to_world_spec({"rules": [], "power_system": {}}, spec)

    assert book["unique_hook"] == spec.one_liner
    assert book["series_engine"]["anti_cheat_rules"] == list(spec.anti_cheat)
    assert book["series_engine"]["chapter_ending_hook_strategy"] == (
        "每次成功使用核心规则，都必须制造可见代价、误解升级或反作弊压力。"
    )
    assert world["rules"][0]["story_consequence"] == "；".join(spec.constraints.values())
    assert "虚假表演不结算" in world["power_system"]["hard_limits"]


def test_apply_hook_to_book_spec_preserves_existing_logline() -> None:
    spec = _spec()

    book = apply_hook_to_book_spec(
        {
            "title": "旧标题",
            "logline": "沈砚是灵务署临聘巡检员，靠公务工单和岗位权限考编升级。",
            "unique_hook": "岗位权限考编升级",
            "series_engine": {
                "reader_promise": "读者追看临聘巡检员如何用公务规则一路翻盘。"
            },
        },
        spec,
    )

    assert book["logline"] == "沈砚是灵务署临聘巡检员，靠公务工单和岗位权限考编升级。"
    assert book["unique_hook"] == "岗位权限考编升级"
    assert book["series_engine"]["reader_promise"] == (
        "读者追看临聘巡检员如何用公务规则一路翻盘。"
    )
    assert book["anti_commonsense_hook"]["one_liner"] == spec.one_liner


def test_apply_hook_to_book_spec_does_not_add_reader_promise_for_mismatched_hook() -> None:
    mismatched = _spec().model_copy(
        update={
            "mechanism_key": "script_within_script",
            "one_liner": "想接近真凶？可以，但越接近真凶越发现自己只是嵌套剧本里的演员。",
            "core_rule": "揭谜必须先承认自己也在被写，第四面墙突破会留下真相反噬。",
        }
    )

    book = apply_hook_to_book_spec(
        {
            "title": "临聘仙官从工单考编开始",
            "logline": "沈砚是灵务署临聘巡检员，靠公务工单和岗位权限考编升级。",
            "series_engine": {},
        },
        mismatched,
        premise_context={
            "premise": "沈砚是海城灵务署临聘巡检员，靠岗位权限、公务工单和考编资格升级。",
            "title": "临聘仙官从工单考编开始",
        },
    )

    assert "reader_promise" not in book["series_engine"]


def test_apply_hook_to_volume_plan_and_prompt_constraints() -> None:
    spec = _spec()
    plan = apply_hook_to_volume_plan(
        [{"volume_number": 1, "volume_resolution": {"cost_paid": "失去工作"}}],
        spec,
    )

    assert isinstance(plan, list)
    assert plan[0]["hook_arc_engine"] == list(spec.arc_engine)
    assert "温暖记忆被扣除" in plan[0]["volume_resolution"]["cost_paid"]
    assert "HookSpec" in render_hook_spec_prompt_block(spec)
    assert any("conflict_stakes" in item for item in hook_outline_extra_constraints(spec))

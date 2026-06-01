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

    assert book["logline"] == spec.one_liner
    assert book["series_engine"]["anti_cheat_rules"] == list(spec.anti_cheat)
    assert world["rules"][0]["story_consequence"] == "；".join(spec.constraints.values())
    assert "虚假表演不结算" in world["power_system"]["hard_limits"]


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

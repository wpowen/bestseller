"""Genre-neutral platform defaults: signing/爽文 must be opt-in, not catch-all."""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.domain.project import MarketPositioningConfig, WritingProfile
from bestseller.services.methodology_bridge import (
    pack_uses_shuangwen_loop,
    resolve_shuangwen_fusion,
)
from bestseller.services.planner import (
    opening_quality_gate_requested,
    persist_qimao_opening_contract,
)
from bestseller.services.prompt_packs import infer_default_prompt_pack_key
from bestseller.services.quality_levers.chapter_position_profiles import (
    load_chapter_position_profiles,
)
from bestseller.services.quality_levers.platform_profiles import load_platform_profiles
from bestseller.services.reviews import _FOLK_HORROR_TAIL_HOOK_TERMS
from bestseller.settings import PipelineSettings


def test_market_defaults_are_platform_neutral() -> None:
    market = MarketPositioningConfig()

    assert market.platform_target == "未指定平台"
    assert market.content_mode == "长篇小说"
    assert market.pacing_profile == "medium"
    assert "不强制" in market.payoff_rhythm
    assert "番茄" not in market.opening_strategy
    assert "打脸" not in market.opening_strategy


def test_empty_writing_profile_does_not_select_signing_platform() -> None:
    profile = WritingProfile()
    dumped = profile.model_dump(mode="json")

    assert dumped["market"]["platform_target"] == "未指定平台"
    project = SimpleNamespace(
        metadata_json={"writing_profile": dumped},
        audience=None,
    )
    assert opening_quality_gate_requested(project) is False


def test_opening_quality_gate_requires_platform_or_explicit_flag() -> None:
    leftover = SimpleNamespace(
        metadata_json={"qimao_opening_contract": {"opening_incident": "残留合同"}},
        audience=None,
    )
    opted_in = SimpleNamespace(
        metadata_json={"opening_quality_gate_enabled": True},
        audience=None,
    )
    qimao = SimpleNamespace(
        metadata_json={"writing_profile": {"market": {"platform_target": "七猫小说"}}},
        audience=None,
    )

    assert opening_quality_gate_requested(leftover) is False
    assert opening_quality_gate_requested(opted_in) is True
    assert opening_quality_gate_requested(qimao) is True


def test_persist_qimao_skips_without_signing_platform() -> None:
    project = SimpleNamespace(title="无平台长篇", metadata_json={}, audience=None)

    contract = persist_qimao_opening_contract(
        project,
        premise="一名档案员发现航线被改写。",
        book_spec={},
        cast_spec={},
        volume_plan={},
    )

    assert contract is None
    assert "qimao_opening_contract" not in (project.metadata_json or {})


def test_shuangwen_fusion_defaults_off_and_auto_enables_loop_packs() -> None:
    assert PipelineSettings().enable_shuangwen_fusion is False
    assert pack_uses_shuangwen_loop("xianxia-upgrade-core") is True
    assert pack_uses_shuangwen_loop("romance-tension-growth") is False
    assert pack_uses_shuangwen_loop("epic-fantasy") is False
    assert resolve_shuangwen_fusion(enabled_flag=False, pack_key="cozy-fantasy") is False
    assert resolve_shuangwen_fusion(
        enabled_flag=False, pack_key="xianxia-upgrade-core"
    ) is True
    assert resolve_shuangwen_fusion(enabled_flag=True, pack_key="cozy-fantasy") is True


def test_western_fantasy_does_not_fall_through_to_xianxia_pack() -> None:
    assert infer_default_prompt_pack_key("西方奇幻", None) == "epic-fantasy"
    assert infer_default_prompt_pack_key("史诗奇幻", None) == "epic-fantasy"
    assert infer_default_prompt_pack_key("升级流", None) == "xianxia-upgrade-core"


def test_first_chapter_payoff_gate_is_genre_neutral() -> None:
    config = load_chapter_position_profiles()
    opening = next(
        window
        for window in config.sensitive_anti_patterns.windows
        if window.window_id == "opening_window"
    )
    no_payoff = next(item for item in opening.banned if item.pattern_id == "no_payoff_in_ch1")
    assert "打脸" not in no_payoff.definition
    first = config.profiles["first_chapter"]
    payoff_blob = "\n".join(first.weighted_checks)
    assert "吃瘪" not in payoff_blob
    assert "打脸" not in payoff_blob


def test_opening_hook_bank_is_not_a_mandatory_generic_template() -> None:
    config = load_platform_profiles()
    countdown = next(hook for hook in config.opening_hooks if hook.hook_id == "countdown_threat")
    corpse = next(hook for hook in config.opening_hooks if hook.hook_id == "corpse_speaks")
    assert "烧尸" not in countdown.example_first_line
    assert "鬼魂" not in corpse.pattern or "不是通用默认" in corpse.pattern


def test_folk_horror_tail_hook_terms_omit_book_private_copper_coin() -> None:
    assert "铜钱" not in _FOLK_HORROR_TAIL_HOOK_TERMS

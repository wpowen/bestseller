"""画像判官否决必须能自己算，不能依赖调用方手工补的键。

2026-08-23：我用 `PersonaClickReport.to_dict()` 的结果去跑 `persona_hard_veto`
做误杀审计，16 份样本全判「通过」——其中包括 0/3 点击、均分 1.0 的 AI 腔烂稿。
结果本身荒谬，才发现是量具坏了：

* `advisory_pass` 是个**要传阈值的方法**，因此不在 `to_dict()` 里；
* `persona_hard_veto` 读的正是 `report["advisory_pass"]`，缺键时
  `.get("advisory_pass", True)` → True → 永不否决。

生产路径没出事，只因为 `_persona_click_advisory` 另外手工把这个键补了进去
——判据与数据住在两个地方（本项目反复出现的元病）。任何从序列化报告重建的
调用方（审计脚本、离线复验、未来的新入口）拿到的都是一条静默失效的门。

修：否决用报告自带的数字（click_rate/llm_used）+ 配置阈值自行判定；
仍保留对 `advisory_pass` 键的兼容，有就优先用。
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.persona_click_judge import PersonaClickReport
from bestseller.services.story_appeal import persona_hard_veto

# 阈值取「至少 1/3 会点」的意图值（见 TestShippedConfigMatchesItsOwnIntent）。
_CFG = {"persona_judge": {"block_below": True, "click_rate_min": 0.33}}


def _report(clicks: int, samples: int = 3, llm_used: bool = True) -> PersonaClickReport:
    return PersonaClickReport(
        channel="男频",
        samples=samples,
        clicks=clicks,
        click_rate=clicks / samples if samples else 0.0,
        avg_score=1.0,
        reasons=("划走",),
        llm_used=llm_used,
    )


class TestVetoWorksFromSerialisedReport:
    def test_zero_click_report_from_to_dict_is_vetoed(self) -> None:
        # 这正是审计里那条 0/3、均分 1.0 的 AI 腔烂稿。
        assert persona_hard_veto(_report(0).to_dict(), _CFG) is True

    def test_one_of_three_clicks_passes(self) -> None:
        assert persona_hard_veto(_report(1).to_dict(), _CFG) is False

    def test_judge_unavailable_fails_open(self) -> None:
        assert persona_hard_veto(_report(0, llm_used=False).to_dict(), _CFG) is False

    def test_block_switch_off_never_vetoes(self) -> None:
        cfg = {"persona_judge": {"block_below": False, "click_rate_min": 0.33}}
        assert persona_hard_veto(_report(0).to_dict(), cfg) is False


class TestBackCompat:
    def test_explicit_advisory_pass_key_still_wins(self) -> None:
        d = _report(0).to_dict()
        d["advisory_pass"] = True   # 生产路径手工补的键
        assert persona_hard_veto(d, _CFG) is False

    def test_malformed_report_never_blocks(self) -> None:
        assert persona_hard_veto(None, _CFG) is False
        assert persona_hard_veto({}, _CFG) is False


class TestShippedConfigMatchesItsOwnIntent:
    """配置契约钉：阈值必须让「3 个读者里正好 1 个点」通过。

    2026-08-23：注释写「至少 1/3 模拟读者会点」，阈值却是 0.34，而
    1/3 = 0.3333 < 0.34 —— 注释与数字自相矛盾，真实爆款《亵渎》《诛仙》
    都是 1/3 点击，被这 0.007 的差额毙掉。
    """

    def test_exactly_one_of_three_clicks_passes_under_shipped_config(self) -> None:
        from bestseller.services.story_appeal import load_story_appeal_config

        cfg = load_story_appeal_config()
        assert persona_hard_veto(_report(1).to_dict(), cfg) is False

    def test_zero_of_three_still_vetoed_under_shipped_config(self) -> None:
        from bestseller.services.story_appeal import load_story_appeal_config

        cfg = load_story_appeal_config()
        assert persona_hard_veto(_report(0).to_dict(), cfg) is True

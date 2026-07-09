"""L1 tests for T4 — reader_promise / hook one_liner 止血 (conception.py).

真机根因(tracked-rulehorror-v1, 2026-07-09)：``selected_hook_spec.one_liner``
是候选池机械选优产物（如"锦鲤代价"钩子骨架），未经本书语境适配就被机械覆盖到
``writing_profile.market.logline`` 与 ``reader_promise``，产出与本书完全无关、
且本身是模板插值病句的读者承诺。

三段决策：
  1. 适配检查（``_hook_one_liner_is_adapted``，确定性，零 LLM）：无 fatal 病理
     且含本书实体（主角名/书名）→ 直接采用。
  2. 不满足 → 一次 LLM 改写（``_adapt_hook_one_liner``），改写结果再查一遍。
  3. 仍不满足 → spine 兜底：``reader_promise = story_spine.question``；
     ``market.logline`` 保持 finalize 产出的原值不动。

三段决策的整体编排内联在 ~4000 行的 ``run_conception_pipeline`` 里（与本仓既有
测试惯例一致：见 test_web_server.py 对该函数整体打桩、test_persona_click_judge_
wiring.py 用源码结构断言钉控制流）。``_hook_one_liner_is_adapted`` 和
``_adapt_hook_one_liner`` 是独立可测的模块级函数，直接测试其行为；整段编排的
接线用结构断言钉住关键锚点。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures are intentional.
import inspect

import pytest

from bestseller.services import conception as conception_services

REAL_BAD_ONE_LINER = (
    "先付替人接下重病或灾祸，才换得到临时神运；主角越想被命运眷顾，"
    "越会被迫命运越眷顾，越要替别人接下灾厄，并让被替者不知主角的牺牲持续发酵。"
)


@pytest.mark.unit
class TestHookOneLinerIsAdapted:
    def test_real_bad_one_liner_fails_pathology(self):
        assert not conception_services._hook_one_liner_is_adapted(
            REAL_BAD_ONE_LINER, protagonist="闻雀", title="闻雀试睡",
        )

    def test_clean_one_liner_with_protagonist_name_passes(self):
        text = "闻雀每守一次规则就换一笔福利，但下一次违规的门槛会悄悄降低。"
        assert conception_services._hook_one_liner_is_adapted(
            text, protagonist="闻雀", title="闻雀试睡",
        )

    def test_generic_one_liner_without_any_entity_fails(self):
        # 无病理但也不含任何本书实体（主角名/书名一个字都没提）→ 判定未适配。
        text = "先得到好运，才要付出代价，代价会一次比一次沉重。"
        assert not conception_services._hook_one_liner_is_adapted(
            text, protagonist="闻雀", title="试睡笔记",
        )

    def test_empty_text_fails(self):
        assert not conception_services._hook_one_liner_is_adapted(
            "", protagonist="闻雀", title="闻雀试睡",
        )

    def test_missing_entities_does_not_block_on_entity_check(self):
        # 主角名/书名都太短或缺失时，不因数据缺失误判——只要无病理就通过。
        text = "他熬过了这一夜，但下一夜的规则会更狠。"
        assert conception_services._hook_one_liner_is_adapted(
            text, protagonist="", title="",
        )


@pytest.mark.unit
@pytest.mark.asyncio
class TestAdaptHookOneLiner:
    async def test_llm_rewrite_is_used_when_available(self, monkeypatch):
        async def fake_complete_text(session, settings, request):
            class _Completion:
                content = '{"reader_promise": "闻雀每守一夜规则就多领一笔奖金，但下一次违规的代价会更狠。"}'
                llm_run_id = "run-1"

            return _Completion()

        monkeypatch.setattr(conception_services, "complete_text", fake_complete_text)
        rewritten, ids = await conception_services._adapt_hook_one_liner(
            None, None,
            one_liner=REAL_BAD_ONE_LINER, title="闻雀试睡", protagonist="闻雀",
            premise="试睡员闻雀为保饭碗接下最后一单。", genre="悬疑推理", is_en=False,
        )
        assert "闻雀" in rewritten
        assert rewritten != REAL_BAD_ONE_LINER
        assert ids

    async def test_malformed_json_falls_back_to_original_one_liner(self, monkeypatch):
        # _llm_call_json 对无法解析的 JSON 有内建修复重试，两轮都失败则用
        # fallback payload（原句包在 {"reader_promise": one_liner} 里）。
        async def fake_garbage(session, settings, request):
            class _Completion:
                content = "不是 JSON 的纯文本"
                llm_run_id = None

            return _Completion()

        monkeypatch.setattr(conception_services, "complete_text", fake_garbage)
        rewritten, _ids = await conception_services._adapt_hook_one_liner(
            None, None,
            one_liner=REAL_BAD_ONE_LINER, title="闻雀试睡", protagonist="闻雀",
            premise="x", genre="悬疑推理", is_en=False,
        )
        assert rewritten == REAL_BAD_ONE_LINER

    async def test_raw_call_failure_propagates_for_orchestrator_level_fail_open(
        self, monkeypatch
    ):
        # _adapt_hook_one_liner 不独立吞异常(与 _polish_golden_finger_mechanism
        # 同规约)——原始调用失败(非 JSON 解析失败)直接向上抛，由 orchestrator
        # 里包住整段三步决策的 try/except 提供 fail-open(见下方结构断言测试)。
        async def fake_raise(session, settings, request):
            raise RuntimeError("llm down")

        monkeypatch.setattr(conception_services, "complete_text", fake_raise)
        with pytest.raises(RuntimeError):
            await conception_services._adapt_hook_one_liner(
                None, None,
                one_liner=REAL_BAD_ONE_LINER, title="闻雀试睡", protagonist="闻雀",
                premise="x", genre="悬疑推理", is_en=False,
            )


@pytest.mark.unit
def test_finalize_wires_three_step_hook_adaptation_after_spine() -> None:
    """结构断言：三段决策必须在 story_spine 计算完成之后运行（fallback 需要读
    ``story_spine.get("question")``），且只在 ``concept_bundle is None`` 时生效
    （concept_bundle 存在时维持其原有优先级，不被本批新逻辑覆盖）。同时钉住
    旧的机械覆盖(``market_profile["reader_promise"] = selected_hook_spec.one_liner``)
    必须已被移除——那正是真机病句的直接来源。
    """

    source = inspect.getsource(conception_services.run_conception_pipeline)

    spine_gate_pos = source.index('"agent": "story_spine_gate"')
    hook_adapt_pos = source.index("hook_one_liner_adaptation_gate")
    assert hook_adapt_pos > spine_gate_pos, (
        "hook one_liner adaptation must run after story_spine is computed "
        "(its fallback reads story_spine.get('question'))"
    )
    assert "if selected_hook_spec is not None and concept_bundle is None:" in source
    assert 'market_profile["reader_promise"] = story_spine.get("question") or premise' in source
    # 旧的机械覆盖必须已被移除，不能同时存在两条写入路径。
    assert 'market_profile["reader_promise"] = selected_hook_spec.one_liner' not in source
    assert 'market_profile["logline"] = selected_hook_spec.one_liner' not in source


@pytest.mark.unit
def test_hook_adaptation_block_is_wrapped_in_fail_open_try_except() -> None:
    """_adapt_hook_one_liner 本身不吞异常(见上方 test_raw_call_failure_propagates_
    for_orchestrator_level_fail_open)——整段三步决策必须被 try/except Exception
    包住，否则一次 LLM 网络故障会让整本书的构思直接崩溃。"""

    source = inspect.getsource(conception_services.run_conception_pipeline)
    block_start = source.index(
        "if selected_hook_spec is not None and concept_bundle is None:"
    )
    surrounding = source[max(0, block_start - 20) : block_start + 2500]
    assert "try:" in surrounding
    assert 'logger.warning("hook one_liner adaptation failed (non-fatal)"' in surrounding

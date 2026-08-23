"""事件级沉重度检察官：情绪词表测不出「用事件写的沉重」。

2026-08-23 真机（验证书 8 原稿，tone=light + 喜剧引擎 + 代价档 minimal）：

    「每夜只卖十碗……这十碗面是给十个当晚必死之人吃的，主角要在他们咽气前
      当面问清怎么死……三年来攒下的『人头账』……把每一个将死之人当棋子拆骨」

`_creation_intent_content_violations` 判定：**零违规**。因为
`_HEAVY_TONE_MARKERS` 是 11 个情绪形容词（黑暗/压抑/绝望/尸体…），这段一个
都没命中。

这是同一教训第三次复发——concept_tournament.py 里 2026-08-13 就写着「情绪词表
测不出**用事件写的沉重**」，当时补的 `_COERCION_STAKE_PATTERNS` 只覆盖了
人质与限期处刑两种事件形状，本例（每夜一批必死之人）不在其中。再往词表里
加词是打地鼠：真正缺的是按事件判、而不是按心情词判的判据。

按今日已验证的模式实现（blurb 逻辑轴）：窄任务检察官 + 正例/反例边界 +
引文必须逐字接地 + fail-open。按「新检测器只挣重生和留痕」规矩，它**不进
detected**、不毙书，只换一次重写并留痕。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.heavy_tone_judge import (
    HeavyToneFinding,
    build_heavy_tone_messages,
    parse_and_verify_heavy_tone,
)

_REAL = (
    "每夜在城中开一面馆、只卖十碗。这十碗面是给十个当晚必死之人吃的，"
    "主角要在他们咽气前当面问清怎么死、为何来。他靠三年来攒下的人头账，"
    "在面摊上把每一个将死之人当棋子拆骨。"
)


class TestPrompt:
    def test_prompt_is_an_event_task_not_a_mood_task(self) -> None:
        system, user = build_heavy_tone_messages(_REAL)
        # 判据必须说的是事件，不是情绪词。
        assert "事件" in system
        assert "情绪词" in system or "形容词" in system
        # 反例边界与反硬凑护栏（防冤案）。
        assert "不算" in system
        assert "不要硬凑" in system
        assert _REAL[:12] in user

    def test_prompt_does_not_seed_motif_vocabulary(self) -> None:
        """否定式指令点名母题词=种词（2026-08-06 定案）。

        prompt 里不得出现「债/账/寿元/殡仪」这类会被写手当灵感抄走的具体
        母题词——判据只描述类别与结构。
        """

        system, _ = build_heavy_tone_messages(_REAL)
        for seeded in ("债", "寿元", "殡仪", "尸油"):
            assert seeded not in system


class TestQuoteGrounding:
    def test_grounded_finding_survives(self) -> None:
        raw = (
            '{"heavy_events": [{"quote": "给十个当晚必死之人吃的", '
            '"why": "每夜有一批人当晚必死，是持续的死亡事件不是气氛词"}]}'
        )
        found, dropped = parse_and_verify_heavy_tone(raw, source_text=_REAL)
        assert dropped == 0
        assert len(found) == 1
        assert isinstance(found[0], HeavyToneFinding)
        assert found[0].quote in _REAL

    def test_hallucinated_quote_is_dropped(self) -> None:
        raw = '{"heavy_events": [{"quote": "他把全城的人都活埋了", "why": "x"}]}'
        found, dropped = parse_and_verify_heavy_tone(raw, source_text=_REAL)
        assert found == ()
        assert dropped == 1

    def test_empty_and_garbage_are_safe(self) -> None:
        for raw in ("", "not json", '{"heavy_events": []}', "{}"):
            found, dropped = parse_and_verify_heavy_tone(raw, source_text=_REAL)
            assert found == ()

    def test_trimmed_quote_still_grounds(self) -> None:
        # 模型常丢标点/空白，逐字核对要容忍这个但不容忍改字。
        raw = '{"heavy_events": [{"quote": "十个当晚必死之人吃的\\n", "why": "y"}]}'
        found, dropped = parse_and_verify_heavy_tone(raw, source_text=_REAL)
        assert len(found) == 1


class TestAdvisoryOnly:
    def test_findings_render_rewrite_feedback_with_quotes(self) -> None:
        from bestseller.services.heavy_tone_judge import render_heavy_tone_feedback

        text = render_heavy_tone_feedback(
            (HeavyToneFinding(quote="给十个当晚必死之人吃的", why="持续死亡事件"),)
        )
        assert "给十个当晚必死之人吃的" in text
        # 反馈是给重写用的整改行，不是判决书。
        assert "轻松" in text

    def test_no_findings_renders_nothing(self) -> None:
        from bestseller.services.heavy_tone_judge import render_heavy_tone_feedback

        assert render_heavy_tone_feedback(()) == ""


class TestConceptionWiring:
    """接线钉：判官必须真的挂在构思重生路径上，且不得握有杀权。"""

    def test_pipeline_calls_the_judge_and_feeds_the_retry(self) -> None:
        import inspect

        from bestseller.services import conception

        src = inspect.getsource(conception.run_conception_pipeline)
        assert "detect_heavy_tone_events(" in src
        assert "render_heavy_tone_feedback(heavy_tone_hits)" in src
        # 触发条件里有它（挣重生）
        assert "or heavy_tone_hits" in src
        # 但绝不进 detected（不毙书）——detected 的每一次 append/extend 都不许
        # 碰 heavy_tone。
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("detected.append") or stripped.startswith(
                "detected.extend"
            ):
                assert "heavy_tone" not in stripped

    def test_judge_is_light_tone_only(self) -> None:
        import asyncio

        from bestseller.services.heavy_tone_judge import detect_heavy_tone_events

        # 非 light 调性直接短路，不花 LLM 钱、也不可能误伤重压题材。
        out = asyncio.run(
            detect_heavy_tone_events(
                None, None, text="满地尸体", tone_preference="dark"
            )
        )
        assert out == ()

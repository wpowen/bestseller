"""T7 (2026-07-09) — 物料收敛：promotional_brief 消费终版简介 + 书名覆盖收紧。

真机根因：planner._generate_promotional_brief 独立再生一份 blurb，与构思终稿
synopsis（已过 blurb_pathology 病理检测器 + blurb_copywriter 淘汰赛，见 T3/T6）
互不校验、各写各的，还能无条件覆盖已过 appeal 闸门的书名——构思侧的质量把关
可被规划侧无声推翻。

``_generate_promotional_brief`` 本身此前零测试覆盖（触及真实 DB 写入：
import_planning_artifact/create_workflow_step_run），核心新增逻辑已抽成两个
纯函数直接测试；再用源码结构断言钉住它们确实被调用（同 T1/T4/T6 的既有惯例）。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import planner as planner_services
from bestseller.services.planner import (
    _resolve_promotional_brief_blurb,
    _should_overwrite_promotional_title,
)

pytestmark = pytest.mark.unit


class TestResolvePromotionalBriefBlurb:
    def test_converged_synopsis_wins_over_llm_blurb(self):
        blurb = _resolve_promotional_brief_blurb(
            converged_synopsis="定稿简介正文。",
            llm_blurb="planner 独立生成的另一份简介。",
            premise_fallback="故事核。",
        )
        assert blurb == "定稿简介正文。"

    def test_converged_synopsis_gets_sentence_truncated(self):
        long_synopsis = "。".join(f"第{i}句内容填充文字" for i in range(80)) + "。"
        blurb = _resolve_promotional_brief_blurb(
            converged_synopsis=long_synopsis, llm_blurb="x", premise_fallback="y",
        )
        assert len(blurb) <= 500
        assert blurb.endswith("。")

    def test_empty_synopsis_falls_through_to_clean_llm_blurb(self):
        blurb = _resolve_promotional_brief_blurb(
            converged_synopsis="", llm_blurb="干净的LLM简介，不含病理。",
            premise_fallback="故事核。",
        )
        assert blurb == "干净的LLM简介，不含病理。"

    def test_empty_synopsis_and_fatal_llm_blurb_falls_back_to_premise(self):
        # "保饭碗还是丢工作？"命中同义反复选择句 fatal 病理。
        blurb = _resolve_promotional_brief_blurb(
            converged_synopsis="", llm_blurb="保饭碗还是丢工作？",
            premise_fallback="试睡员为保饭碗接下最后一单。",
        )
        assert blurb == "试睡员为保饭碗接下最后一单。"

    def test_all_empty_returns_empty_string(self):
        assert _resolve_promotional_brief_blurb(
            converged_synopsis="", llm_blurb="", premise_fallback="",
        ) == ""


class TestShouldOverwritePromotionalTitle:
    def test_old_bare_taxonomy_and_candidate_clears_bar_overwrites(self):
        assert _should_overwrite_promotional_title(
            candidate_total=85.0, title_min=80.0, old_title_is_bare=True, old_total=None,
        ) is True

    def test_old_not_bare_and_candidate_scores_higher_overwrites(self):
        assert _should_overwrite_promotional_title(
            candidate_total=90.0, title_min=80.0, old_title_is_bare=False, old_total=70.0,
        ) is True

    def test_old_not_bare_and_candidate_scores_lower_keeps_old(self):
        assert _should_overwrite_promotional_title(
            candidate_total=75.0, title_min=0.0, old_title_is_bare=False, old_total=90.0,
        ) is False

    def test_candidate_below_title_min_keeps_old_even_if_old_is_bare(self):
        # 候选本身不达标(<title_min)——即使旧名是裸题材名也不该拿一个更差的名字换。
        assert _should_overwrite_promotional_title(
            candidate_total=50.0, title_min=80.0, old_title_is_bare=True, old_total=None,
        ) is False

    def test_title_min_disabled_only_needs_old_beaten(self):
        assert _should_overwrite_promotional_title(
            candidate_total=10.0, title_min=0.0, old_title_is_bare=True, old_total=None,
        ) is True

    def test_old_total_none_and_not_bare_keeps_old(self):
        # 旧名评估失败(old_total=None)且不是裸题材名 → 无法证明候选更好 → 保守不覆盖。
        assert _should_overwrite_promotional_title(
            candidate_total=95.0, title_min=0.0, old_title_is_bare=False, old_total=None,
        ) is False


class TestGeneratePromotionalBriefWiring:
    def _source(self) -> str:
        return inspect.getsource(planner_services._generate_promotional_brief)

    def test_blurb_resolution_is_wired(self):
        source = self._source()
        assert "blurb = _resolve_promotional_brief_blurb(" in source
        assert 'str(_metadata.get("synopsis") or "")' in source
        assert "converged_synopsis=" in source

    def test_title_overwrite_gate_is_wired(self):
        source = self._source()
        assert "_should_overwrite_promotional_title(" in source
        # 旧的无条件覆盖判据(仅"非空+非题材裸名")不应再是唯一判据——必须经过新函数。
        assert "if _should_overwrite_promotional_title(" in source

    def test_title_comparison_failure_falls_open_to_keeping_old_title(self):
        source = self._source()
        idx = source.index("_should_overwrite_promotional_title(")
        surrounding = source[max(0, idx - 900) : idx + 800]
        assert "try:" in surrounding
        assert "except Exception:" in surrounding
        assert "keeping existing title" in surrounding

"""在架稿不合格时，重写必须能顶上去——否则章节被永久冻结。

2026-08-24 真机（验证书 9 第 8 章）：7 份草稿，**4 次审稿全部评的是 v3**
（23:26/23:29/23:31/00:45），而 v5 生成于 23:27、v6 于 23:30、v7 于 00:44
——重写产出的新稿从头到尾没被看过一眼。

机制：`review_chapter_draft` 加载 `is_current` 那一份；重写稿要成为 current，
原判据是「它自己没被质量门 blocked」。三份稿离线实测同被 POV_DRIFT 阻断
（v3 在架、v5/v6 候选），于是谁也顶不上去 → 审稿永远读同一份旧文本 →
每轮发现完全相同 → 重写永不收敛 → 后续所有重写 token 纯浪费。
全书 66 份草稿仅 17 份被评分（26%），一半章节卡在旧版本上。

**判据立足调用点语义**：`rewrite_chapter_from_task` 之所以被调用，就是因为
在架稿需要重写——它在这里按定义就是不满意的。所以默认换稿，只在挑战者
**根本不可用**时保留旧稿：重复内容、确定性审计不过、AI 味实测回退。

⚠️ 这条判据我走错过两次，两次都写进了实现的 docstring：
①两值解包一个单值返回 → 运行时抛错被 except 吞掉 → 修复是空操作；
②改用 `chapter.production_state` → 那个状态在决策点之后才写入 → 判据永远
  读到「在架稿是干净的」（真机第 18 章：v2 生成于 01:50:14，章状态此后才变
  blocked），修复照样不生效。
教训：判据要么用调用点本身就成立的语义，要么用决策时刻**已经写入**的事实。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
from bestseller.services.reviews import challenger_takes_current


class TestDefaultIsToTakeOver:
    def test_clean_challenger_takes_over(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=False,
                incumbent_gate_outcome="ok",
                has_duplicate_findings=False,
                deterministic_audit_failed=False,
            )
            is True
        )

    def test_clean_incumbent_is_protected(self) -> None:
        """在架稿自己是干净的：不合格的挑战者不许顶掉它（原有保护）。"""

        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_gate_outcome="ok",
                has_duplicate_findings=False,
                deterministic_audit_failed=False,
                violation_codes=("POV_DRIFT",),
            )
            is False
        )

    def test_merely_imperfect_challenger_takes_over(self) -> None:
        """真机第 8 章原样：只是被通用质量门（POV_DRIFT）拦下。

        在架稿卡在同一条违规上，继续占位只会让审稿反复评同一份文本。
        """

        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_gate_outcome="blocked",
                has_duplicate_findings=False,
                deterministic_audit_failed=False,
                violation_codes=("POV_DRIFT",),
            )
            is True
        )


class TestHardUnusableChallengerIsRejected:
    def test_duplicate_content_keeps_the_incumbent(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_gate_outcome="blocked",
                has_duplicate_findings=True,
                deterministic_audit_failed=False,
            )
            is False
        )

    def test_failed_deterministic_audit_keeps_the_incumbent(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_gate_outcome="blocked",
                has_duplicate_findings=False,
                deterministic_audit_failed=True,
            )
            is False
        )

    def test_ai_flavor_regression_keeps_the_incumbent(self) -> None:
        """AI 味回退是真正做过「比在架差」比较的信号，理应保留旧稿。"""

        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_gate_outcome="blocked",
                has_duplicate_findings=False,
                deterministic_audit_failed=False,
                violation_codes=("AI_FLAVOR_REGRESSION", "POV_DRIFT"),
            )
            is False
        )


class TestWiring:
    def test_rewrite_path_uses_the_decision(self) -> None:
        import inspect

        from bestseller.services import reviews

        src = inspect.getsource(reviews.rewrite_chapter_from_task)
        assert "challenger_takes_current(" in src
        # 旧的「只看自己是否 blocked」写法必须消失
        assert "is_current=not quality_gate_rejected_current_promotion" not in src
        # 回执与状态共用同一个决定值
        assert "is_current=_took_current" in src
        assert "took_current=_took_current" in src

    def test_decision_uses_no_downstream_state_and_no_extra_query(self) -> None:
        """⚠️ 两次走错的防复发钉：不得再用决策点之后才写入的状态，
        也不得在判定处新增查询（会打乱按调用序返回的测试桩）。"""

        import inspect

        from bestseller.services import reviews

        src = inspect.getsource(reviews.rewrite_chapter_from_task)
        idx = src.find("_took_current = challenger_takes_current(")
        assert idx != -1
        decision = src[idx : idx + 700]
        assert "production_state" not in decision  # 决策点之后才写入的状态不可用
        assert "session.scalar" not in decision
        assert "_evaluate_chapter_quality_gate" not in decision

    def test_gate_helper_is_single_valued(self) -> None:
        """`_evaluate_chapter_quality_gate` 返回单值——任何两值解包都会在运行时炸。"""

        import inspect

        from bestseller.services.drafts import _evaluate_chapter_quality_gate

        ret = str(inspect.signature(_evaluate_chapter_quality_gate).return_annotation)
        assert "tuple" not in ret.lower()

        import re

        from bestseller.services import reviews

        assert not re.search(
            r"\w+\s*,\s*\w+\s*=\s*await\s+_evaluate_chapter_quality_gate",
            inspect.getsource(reviews),
        )

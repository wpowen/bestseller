"""没有种子时，采样量必须撑得起「靠随机撞出一个合格冠军」。

淘汰赛有七个硬门，且**必须同时**达标。真机实测（2026-07-29，空题材玄幻，
10 个候选）：

    候选   fresh click chara mecha genre plain story
      4     6.0   7.0   8.0   8.0   7.0   8.0   7.0   ← 唯一通过
    地板    6.0   7.0   7.0   7.0   7.0   7.0   7.0

单候选通过率约 **10%**（1/10）。人物决策与机制因果稳定在 7~8 全过，新颖度
分布 3~6、中位数 4.5 却要求 ≥6.0——它是主要杀手。

而重试轮的采样量是写死的 2：

    attempt_config = {**cfg, "n_candidates": 2} if attempt > 1 else cfg

于是 P(第1轮全灭)=0.9⁶≈53%，P(第2轮全灭)=0.9²≈**81%**，两轮都灭≈43%。
**重试比首轮还窄。**

2 这个数字对「定向补强」是对的——拿一个近失候选去打磨，不需要六个变体。但
2026-07-28 我改了种子逻辑：新颖度挂了就**不给种子**，让重试自由重掷。重掷需要
的是宽采样，我却把打磨用的预算留在了那里。两处修复互相抵消。

判据：采样量跟着重试**在做什么**走，而不是跟着轮次走。
- 有种子可打磨 → 窄采样够用
- 从零重掷 → 至少要和首轮一样宽
另外，没有任何用户创意的建书本来就更依赖随机撞，首轮也该比默认宽。
"""

from __future__ import annotations

import pytest

from bestseller.services.conception import _tournament_attempt_candidate_count

pytestmark = pytest.mark.unit


class TestRefinementStaysNarrow:
    def test_refining_a_seed_keeps_the_small_sample(self) -> None:
        """打磨一个近失候选不需要宽采样——那是它当初设成 2 的理由。"""

        assert (
            _tournament_attempt_candidate_count(
                attempt=2, baseline=6, has_seed=True, refining=True
            )
            == 2
        )


class TestRerollIsNeverNarrowerThanTheFirstAttempt:
    def test_a_seedless_reroll_is_at_least_as_wide(self) -> None:
        n = _tournament_attempt_candidate_count(
            attempt=2, baseline=6, has_seed=False, refining=False
        )
        assert n >= 6, "重掷比首轮还窄，等于让重试注定失败"

    def test_the_third_attempt_is_also_wide(self) -> None:
        n = _tournament_attempt_candidate_count(
            attempt=3, baseline=6, has_seed=False, refining=False
        )
        assert n >= 6


class TestSeedlessRunsSampleWider:
    def test_no_user_seed_widens_the_first_attempt(self) -> None:
        """七轴同时达标、单候选约 10% 通过——默认 6 个的全灭率是 53%。"""

        seeded = _tournament_attempt_candidate_count(
            attempt=1, baseline=6, has_seed=True, refining=False
        )
        seedless = _tournament_attempt_candidate_count(
            attempt=1, baseline=6, has_seed=False, refining=False
        )
        assert seedless > seeded

    def test_a_user_seed_leaves_the_baseline_alone(self) -> None:
        """用户给了创意，新颖度这一轴被锚定，不需要多花钱撞。"""

        assert (
            _tournament_attempt_candidate_count(
                attempt=1, baseline=6, has_seed=True, refining=False
            )
            == 6
        )

    def test_the_widened_count_is_bounded(self) -> None:
        """宽采样是为了过门，不是无上限烧钱。"""

        n = _tournament_attempt_candidate_count(
            attempt=1, baseline=6, has_seed=False, refining=False
        )
        assert n <= 24


class TestDegradesSafely:
    def test_a_missing_baseline_still_returns_something_usable(self) -> None:
        n = _tournament_attempt_candidate_count(
            attempt=1, baseline=0, has_seed=False, refining=False
        )
        assert n >= 1

    def test_the_count_is_always_a_positive_int(self) -> None:
        for attempt in (1, 2, 3):
            for has_seed in (True, False):
                for refining in (True, False):
                    n = _tournament_attempt_candidate_count(
                        attempt=attempt,
                        baseline=6,
                        has_seed=has_seed,
                        refining=refining,
                    )
                    assert isinstance(n, int) and n >= 1

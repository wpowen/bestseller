
from __future__ import annotations

import pytest

from bestseller.domain.market_validation import (
    BlurbBenchmarkSection,
    CompetitorSimilarity,
    GenreHeatSection,
    MarketSectionStatus,
    MarketVerdictBand,
    TitleCheckFinding,
    TitleCheckSection,
)
from bestseller.services.market_validation.judge import (
    parse_collisions,
    score_verdict,
)

pytestmark = pytest.mark.unit


def _heat(**kwargs) -> GenreHeatSection:
    defaults = {"status": MarketSectionStatus.OK, "sample_size": 50, "heat_p50": 200_000}
    defaults.update(kwargs)
    return GenreHeatSection(**defaults)


def _titles(verdict: str) -> TitleCheckSection:
    return TitleCheckSection(
        status=MarketSectionStatus.OK,
        findings=[TitleCheckFinding(candidate="书名", verdict=verdict)],
    )


class TestParseCollisions:
    def test_grounded_titles_kept_hallucinated_dropped(self) -> None:
        content = (
            '{"collisions": ['
            '{"title": "真书", "similarity": "high", "overlap_points": ["同设定"]},'
            '{"title": "编造的书", "similarity": "high"}]}'
        )

        collisions, dropped = parse_collisions(content, known_titles={"真书"})

        assert [item.title for item in collisions] == ["真书"]
        assert dropped == 1

    def test_low_similarity_filtered(self) -> None:
        content = '{"collisions": [{"title": "真书", "similarity": "low"}]}'

        collisions, dropped = parse_collisions(content, known_titles={"真书"})

        assert collisions == [] and dropped == 0

    def test_garbage_returns_empty(self) -> None:
        assert parse_collisions("不是JSON", known_titles={"x"}) == ([], 0)


class TestScoreVerdict:
    def test_healthy_market_clean_title_goes(self) -> None:
        verdict = score_verdict(
            genre_heat=_heat(heat_p50=400_000, new_entry_share=0.2, rising_share=0.6),
            title_check=_titles("pass"),
            collisions=[],
            collisions_judged=True,
            blurb=BlurbBenchmarkSection(status=MarketSectionStatus.OK),
            has_concept=True,
        )

        assert verdict.band == MarketVerdictBand.GO
        assert verdict.score >= 70
        assert any("基准分" in line for line in verdict.rationale)

    def test_all_titles_fail_and_high_collision_no_go(self) -> None:
        verdict = score_verdict(
            genre_heat=_heat(heat_p50=20_000, new_entry_share=0.02),
            title_check=_titles("fail"),
            collisions=[CompetitorSimilarity(title="占位书", similarity="high")],
            collisions_judged=True,
            blurb=BlurbBenchmarkSection(status=MarketSectionStatus.OK),
            has_concept=True,
        )

        assert verdict.band == MarketVerdictBand.NO_GO
        assert verdict.risks

    def test_no_data_stays_neutral_revise(self) -> None:
        verdict = score_verdict(
            genre_heat=GenreHeatSection(status=MarketSectionStatus.SKIPPED),
            title_check=TitleCheckSection(status=MarketSectionStatus.SKIPPED),
            collisions=[],
            collisions_judged=False,
            blurb=BlurbBenchmarkSection(status=MarketSectionStatus.SKIPPED),
            has_concept=False,
        )

        assert verdict.band == MarketVerdictBand.REVISE
        assert 40 <= verdict.score <= 60

    def test_score_clamped(self) -> None:
        verdict = score_verdict(
            genre_heat=_heat(heat_p50=10_000, new_entry_share=0.0),
            title_check=_titles("fail"),
            collisions=[
                CompetitorSimilarity(title=f"书{i}", similarity="high") for i in range(5)
            ],
            collisions_judged=True,
            blurb=BlurbBenchmarkSection(
                status=MarketSectionStatus.OK, warnings=["长", "碎", "怪"]
            ),
            has_concept=True,
        )

        assert verdict.score >= 5

    def test_every_adjustment_traced_in_rationale(self) -> None:
        verdict = score_verdict(
            genre_heat=_heat(heat_p50=400_000),
            title_check=_titles("pass"),
            collisions=[],
            collisions_judged=True,
            blurb=BlurbBenchmarkSection(status=MarketSectionStatus.OK),
            has_concept=True,
        )

        base_and_adjustments = sum(
            1 for line in verdict.rationale if line[0] in "+-基"
        )
        assert base_and_adjustments == len(verdict.rationale)

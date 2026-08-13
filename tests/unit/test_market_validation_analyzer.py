# ruff: noqa: RUF001, RUF003 — Chinese market vocabulary is intentional.
from __future__ import annotations

import pytest

from bestseller.domain.market_validation import (
    MarketBookObservation,
    MarketCategoryRef,
    MarketSectionStatus,
)
from bestseller.services.market_validation.analyzer import (
    benchmark_blurb,
    build_genre_heat,
    check_titles,
    extract_title_shell,
    title_distance,
)
from bestseller.services.market_validation.config import (
    load_market_validation_config,
    reset_market_validation_config_cache,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_market_validation_config_cache()
    yield
    reset_market_validation_config_cache()


def _book(title: str, heat: int = 100_000, **kwargs) -> MarketBookObservation:
    defaults = {"platform": "fanqie", "category": "东方仙侠", "board_type": "阅读榜"}
    defaults.update(kwargs)
    return MarketBookObservation(title=title, heat=heat, **defaults)


def _refs() -> list[MarketCategoryRef]:
    return [
        MarketCategoryRef(
            platform="fanqie", channel_label="男频", category_label="东方仙侠"
        )
    ]


class TestGenreHeat:
    def test_percentiles_and_top_books(self) -> None:
        books = [_book(f"书{i}", heat=(i + 1) * 10_000) for i in range(20)]

        section = build_genre_heat(books, _refs(), min_sample=10, top_books=5)

        assert section.status == MarketSectionStatus.OK
        assert section.sample_size == 20
        assert section.heat_p50 == pytest.approx(105_000, rel=0.1)
        assert section.heat_p10 < section.heat_p50 < section.heat_p90
        assert len(section.top_books) == 5
        assert section.top_books[0].heat == 200_000

    def test_new_entry_and_rising_share(self) -> None:
        books = [
            _book("甲", is_new_entry=True, heat_delta=500),
            _book("乙", is_new_entry=False, heat_delta=-200),
            _book("丙", is_new_entry=False, heat_delta=300),
            _book("丁", is_new_entry=False, heat_delta=None),
        ]

        section = build_genre_heat(books, _refs(), min_sample=1, top_books=3)

        assert section.new_entry_share == pytest.approx(0.25)
        # rising 只在有 delta 的样本里算：2/3 上涨
        assert section.rising_share == pytest.approx(2 / 3, rel=0.01)

    def test_thin_sample_degrades(self) -> None:
        section = build_genre_heat([_book("孤本")], _refs(), min_sample=10, top_books=5)

        assert section.status == MarketSectionStatus.DEGRADED
        assert "样本" in section.reason or "sample" in section.reason.lower()

    def test_empty_categories_skips(self) -> None:
        section = build_genre_heat([], [], min_sample=10, top_books=5)

        assert section.status == MarketSectionStatus.SKIPPED


class TestTitleShell:
    def test_wo_zai_x_dang_y(self) -> None:
        config = load_market_validation_config()
        shell = extract_title_shell("我在天庭当HR", config.title_check.shells)

        assert shell is not None
        assert shell.name == "我在X当/做Y"

    def test_kaiju(self) -> None:
        config = load_market_validation_config()
        shell = extract_title_shell("开局签到荒古圣体", config.title_check.shells)

        assert shell is not None
        assert shell.name == "开局X"

    def test_colon_shell(self) -> None:
        config = load_market_validation_config()
        shell = extract_title_shell("领主：我在苦痛世界养成少女", config.title_check.shells)

        assert shell is not None
        assert "副标题" in shell.name

    def test_plain_title_no_shell(self) -> None:
        config = load_market_validation_config()

        assert extract_title_shell("十日终焉", config.title_check.shells) is None


class TestTitleDistance:
    def test_identical_after_normalization(self) -> None:
        assert title_distance("十日终焉", "十日终焉！") == 0

    def test_one_char_swap(self) -> None:
        assert title_distance("诸神愚戏", "诸神游戏") == 1


class TestCheckTitles:
    def test_exact_hit_fails(self) -> None:
        config = load_market_validation_config()
        board = [_book("十日终焉", heat=2_000_000)]

        section = check_titles(["十日终焉"], board, {}, config.title_check)

        assert section.findings[0].verdict == "fail"
        assert section.findings[0].exact_hits

    def test_crowded_weak_shell_fails(self) -> None:
        config = load_market_validation_config()
        board = [
            _book("我在天庭收垃圾", heat=8_000),
            _book("我在仙界当保安", heat=5_000),
            _book("我在地府开餐厅", heat=6_000),
        ]

        section = check_titles(["我在天庭当HR"], board, {}, config.title_check)

        finding = section.findings[0]
        assert finding.shell is not None
        assert finding.shell.board_count == 3
        assert finding.verdict == "fail"

    def test_crowded_hot_shell_cautions(self) -> None:
        config = load_market_validation_config()
        board = [
            _book("我在精神病院学斩神", heat=3_000_000),
            _book("我在仙界当大佬", heat=1_500_000),
            _book("我在地府做阎王", heat=900_000),
        ]

        section = check_titles(["我在天庭当HR"], board, {}, config.title_check)

        assert section.findings[0].verdict == "caution"

    def test_clean_title_passes(self) -> None:
        config = load_market_validation_config()
        board = [_book("完全无关的书名", heat=100_000)]

        section = check_titles(["焚天纪"], board, {}, config.title_check)

        assert section.findings[0].verdict == "pass"

    def test_web_hits_recorded_and_caution(self) -> None:
        config = load_market_validation_config()

        section = check_titles(
            ["焚天纪"],
            [],
            {"焚天纪": ["fanqienovel.com 已有《焚天纪》"]},
            config.title_check,
        )

        finding = section.findings[0]
        assert finding.web_hits
        assert finding.verdict in {"caution", "fail"}

    def test_board_shape_stats(self) -> None:
        config = load_market_validation_config()
        board = [
            _book("十日终焉"),
            _book("领主：养成少女"),
            _book("诸神愚戏"),
            _book("异兽迷城"),
        ]

        section = check_titles(["随便"], board, {}, config.title_check)

        assert section.board_title_length_p50 >= 4
        assert section.board_title_colon_share == pytest.approx(0.25)


class TestBlurbBenchmark:
    def test_shapes_and_warning_on_length_gap(self) -> None:
        ours = "他推开门。" * 40  # 200 字、40 句的碎句简介
        board = ["少年觉醒，逆天改命。全城震动！" * 3] * 10  # ~45 字

        section = benchmark_blurb(ours, board, min_board_samples=8)

        assert section.status == MarketSectionStatus.OK
        assert section.ours is not None and section.board_median is not None
        assert section.ours.char_count == 200
        assert section.warnings  # 长度偏离过大要报 warning

    def test_tag_prefix_detected(self) -> None:
        section = benchmark_blurb(
            "【种田+慢热+西幻】穿越中世纪成为贵族。",
            ["【无敌+爽文】少年崛起。"] * 10,
            min_board_samples=8,
        )

        assert section.ours is not None
        assert section.ours.has_tag_prefix is True

    def test_thin_board_degrades(self) -> None:
        section = benchmark_blurb("我们的简介。", ["榜单简介。"], min_board_samples=8)

        assert section.status == MarketSectionStatus.DEGRADED

    def test_no_blurb_skips(self) -> None:
        section = benchmark_blurb("", ["榜单简介。"] * 10, min_board_samples=8)

        assert section.status == MarketSectionStatus.SKIPPED

# ruff: noqa: RUF002, RUF003 — Chinese market vocabulary is intentional.
"""题材键解析 pin。

2026-08-08 L3 真栈验证抓到的 bug：网页建书把合成预设键 ``custom-xianxia``
当规范题材键传进市场验证，映射失败 → 热度/竞品两节整节 SKIPPED（报告还是
"成功"产出的，所以离线测试全绿也看不见）。规范键的真源在
``metadata["genre_intent_contract"]``，不同建书入口各有各的叫法——所以解析
必须只有一个实现，三个调用点共用。
"""

from __future__ import annotations

import pytest

from bestseller.services.market_validation.request_builder import (
    build_creation_request,
    resolve_taxonomy_keys,
)

pytestmark = pytest.mark.unit


class TestResolveTaxonomyKeys:
    def test_genre_intent_contract_wins(self) -> None:
        """网页建书路径：规范键住在创建时契约里。"""

        keys = resolve_taxonomy_keys(
            metadata={
                "genre_intent_contract": {
                    "genre_key": "xianxia",
                    "sub_genre_key": "classic-xianxia",
                }
            },
            genre_label="古典仙侠",
            sub_genre_label="古典仙侠",
            fallback_genre_key="custom-xianxia",
        )

        assert keys == ("xianxia", "classic-xianxia")

    def test_genre_canonical_used_when_no_contract(self) -> None:
        """CLI/API 路径：resolve_selection 写的是 genre_canonical。"""

        keys = resolve_taxonomy_keys(
            metadata={"genre_canonical": "suspense"},
            genre_label="悬疑推理",
            sub_genre_label="规则怪谈",
        )

        assert keys[0] == "suspense"
        assert keys[1] == "rule-horror"  # 由 label 反查补齐

    def test_labels_canonicalized_when_metadata_empty(self) -> None:
        keys = resolve_taxonomy_keys(
            metadata={}, genre_label="古典仙侠", sub_genre_label="古典仙侠"
        )

        assert keys == ("xianxia", "classic-xianxia")

    def test_synthetic_preset_key_never_leaks_through(self) -> None:
        """``custom-xianxia`` 不是 taxonomy 公民，必须被归一或丢弃。"""

        keys = resolve_taxonomy_keys(
            metadata=None, genre_label="", sub_genre_label="",
            fallback_genre_key="custom-xianxia",
        )

        assert keys[0] == "xianxia"

    def test_unresolvable_returns_empty_not_garbage(self) -> None:
        keys = resolve_taxonomy_keys(
            metadata=None, genre_label="", sub_genre_label="", fallback_genre_key=""
        )

        assert keys == ("", "")

    def test_garbage_metadata_does_not_raise(self) -> None:
        keys = resolve_taxonomy_keys(
            metadata={"genre_intent_contract": "not-a-dict", "genre_canonical": 42},
            genre_label="古典仙侠",
        )

        assert keys[0] == "xianxia"


class TestBuildCreationRequest:
    def test_web_creation_shape_maps_to_real_categories(self) -> None:
        from bestseller.services.market_validation.category_map import (
            resolve_fanqie_categories,
        )
        from bestseller.services.market_validation.config import (
            load_market_validation_config,
        )

        request = build_creation_request(
            metadata={
                "genre_intent_contract": {
                    "genre_key": "xianxia",
                    "sub_genre_key": "urban-cultivation",
                }
            },
            genre_label="都市修真",
            sub_genre_label="都市修真",
            title="我以阳寿换剑",
            concept="以寿命为代价的剑诀",
            blurb="简介文本。",
        )

        assert request.genre_key == "xianxia"
        assert request.sub_genre_key == "urban-cultivation"
        assert request.title_candidates == ("我以阳寿换剑",)

        # 关键回归断言：解析出的键必须真能映射到平台分类（不是空转）
        refs = resolve_fanqie_categories(
            load_market_validation_config(),
            genre_key=request.genre_key,
            sub_genre_key=request.sub_genre_key,
        )
        assert [ref.category_label for ref in refs] == ["都市修真", "东方仙侠"]

    def test_empty_title_and_blurb_are_dropped_not_blank_entries(self) -> None:
        request = build_creation_request(
            metadata={"genre_canonical": "xianxia"},
            genre_label="仙侠",
            title="   ",
            concept="",
            blurb="",
        )

        assert request.title_candidates == ()
        assert request.concept == ""
        assert request.blurb == ""

    def test_long_inputs_truncated_to_contract_limits(self) -> None:
        request = build_creation_request(
            metadata={"genre_canonical": "xianxia"},
            genre_label="仙侠",
            concept="概" * 5000,
            blurb="简" * 9000,
        )

        assert len(request.concept) <= 2000
        assert len(request.blurb) <= 5000

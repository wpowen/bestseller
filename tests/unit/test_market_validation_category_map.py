
from __future__ import annotations

import pytest

from bestseller.services.market_validation.category_map import (
    qimao_match_labels,
    resolve_fanqie_categories,
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


def test_resolve_genre_level_mapping() -> None:
    config = load_market_validation_config()

    refs = resolve_fanqie_categories(config, genre_key="xianxia")

    assert refs
    assert refs[0].platform == "fanqie"
    assert refs[0].channel_label == "男频"
    assert refs[0].category_label == "东方仙侠"
    assert refs[0].cat_id == "1140"


def test_sub_genre_override_wins() -> None:
    config = load_market_validation_config()

    refs = resolve_fanqie_categories(
        config, genre_key="xianxia", sub_genre_key="urban-cultivation"
    )

    assert [ref.category_label for ref in refs] == ["都市修真", "东方仙侠"]


def test_unknown_genre_returns_empty() -> None:
    config = load_market_validation_config()

    assert resolve_fanqie_categories(config, genre_key="nope") == []
    assert resolve_fanqie_categories(config, genre_key="") == []


def test_empty_mapping_returns_empty_not_error() -> None:
    config = load_market_validation_config()

    assert resolve_fanqie_categories(config, genre_key="pure-love") == []


def test_weights_descend_with_position() -> None:
    config = load_market_validation_config()

    refs = resolve_fanqie_categories(config, genre_key="suspense")

    assert len(refs) >= 2
    assert refs[0].weight >= refs[1].weight


def test_qimao_match_labels_include_taxonomy_and_aliases() -> None:
    config = load_market_validation_config()

    labels = qimao_match_labels(
        config,
        genre_key="xianxia",
        genre_label="仙侠",
        sub_genre_labels=("古典仙侠", "都市修真"),
    )

    assert "仙侠" in labels
    assert "古典仙侠" in labels
    assert "都市修真" in labels
    assert "修真" in labels  # 来自 config 别名


def test_qimao_match_labels_no_alias_still_works() -> None:
    config = load_market_validation_config()

    labels = qimao_match_labels(
        config, genre_key="unknown-key", genre_label="现实", sub_genre_labels=()
    )

    assert labels == {"现实"}

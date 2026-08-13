# ruff: noqa: RUF002, RUF003 — Chinese market vocabulary is intentional.
from __future__ import annotations

import pytest

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


def test_load_real_config_parses_and_validates() -> None:
    config = load_market_validation_config()

    assert config.enabled is True
    assert config.sources.fanqiehub.enabled is True
    assert config.sources.fanqiehub.base_url.startswith("https://")
    # 番茄分类注册表：男频19 / 女频18
    assert len(config.fanqie_categories["男频"]) == 19
    assert len(config.fanqie_categories["女频"]) == 18


def test_fanqie_category_labels_have_cat_ids() -> None:
    config = load_market_validation_config()

    for channel, entries in config.fanqie_categories.items():
        for entry in entries:
            assert entry.label, f"empty label in {channel}"
            assert entry.cat_id, f"missing cat_id for {channel}/{entry.label}"


def test_genre_map_categories_exist_in_registry() -> None:
    """genre_map 引用的每个番茄分类都必须真实存在于注册表（防手滑写错分类名）。"""

    config = load_market_validation_config()
    known = {
        (channel, entry.label)
        for channel, entries in config.fanqie_categories.items()
        for entry in entries
    }
    for genre_key, mapping in config.genre_map.items():
        refs = [mapping.fanqie] if mapping.fanqie else []
        refs.extend(
            override.fanqie
            for override in mapping.sub_overrides.values()
            if override.fanqie
        )
        for ref in refs:
            for label in ref.categories:
                assert (ref.channel, label) in known, (
                    f"genre_map[{genre_key}] references unknown fanqie category "
                    f"{ref.channel}/{label}"
                )


def test_title_shell_rules_compile() -> None:
    import re

    config = load_market_validation_config()
    assert config.title_check.shells
    for shell in config.title_check.shells:
        re.compile(shell.pattern)


def test_disabled_config_short_circuits(tmp_path) -> None:
    path = tmp_path / "mv.yaml"
    path.write_text("version: 1\nenabled: false\n", encoding="utf-8")

    config = load_market_validation_config(path)

    assert config.enabled is False
    # 全部 source 默认存在且不炸
    assert config.sources.fanqiehub.base_url

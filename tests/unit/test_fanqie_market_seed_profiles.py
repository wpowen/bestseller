from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bestseller.services.fanqie_seed_profiles import (
    load_fanqie_seed_profile,
    seed_profile_to_artifacts,
)

pytestmark = pytest.mark.unit


PROFILE_DIR = Path("config/market_profiles/fanqie")
REQUIRED_KEYS = {
    "profile_key",
    "category",
    "reader_promise",
    "entry_pressure_patterns",
    "advantage_patterns",
    "chapter_loop",
    "style_controls",
    "anti_patterns",
    "copy_boundaries",
}


def test_fanqie_seed_profiles_exist_for_core_categories() -> None:
    profiles = sorted(path.stem for path in PROFILE_DIR.glob("*.yaml"))

    assert profiles == [
        "modern-romance-brain",
        "suspense-brain",
        "urban-brain",
        "urban-high-martial",
        "xuanhuan-brain",
    ]


def test_fanqie_seed_profiles_have_safe_required_contracts() -> None:
    for path in PROFILE_DIR.glob("*.yaml"):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))

        assert set(payload) >= REQUIRED_KEYS, path
        assert payload["profile_key"] == path.stem
        assert all(payload[key] for key in REQUIRED_KEYS), path
        assert any("禁止" in item for item in payload["copy_boundaries"]), path
        assert not any(
            "模仿" in item and "作者文风" not in item
            for item in payload["copy_boundaries"]
        )


def test_seed_profile_compiles_to_project_artifact_payloads() -> None:
    payload = load_fanqie_seed_profile("urban-brain")

    artifacts = seed_profile_to_artifacts(payload)

    assert artifacts["summary"]["source"] == "fanqie_seed_profile"
    assert artifacts["summary"]["profile_key"] == "urban-brain"
    assert artifacts["category_profile"]["category"] == "都市脑洞"
    assert "系统面板" in artifacts["category_profile"]["protagonist_archetypes"]
    assert any(
        "禁止" in item
        for item in artifacts["craft_profile"]["disallowed_copy_targets"]
    )

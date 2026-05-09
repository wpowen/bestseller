from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import bestseller.services.material_density as material_density
from bestseller.services.material_density import (
    PROJECT_MATERIAL_TARGETS,
    _build_qingnang_pack,
    _category_pack_spec,
    _decision_policy_for_pack,
    _initial_premium_state_ledger_for_pack,
    _localized_category_pack_id,
    _select_material_pack,
    _blueprint_for_pack,
    hydrate_story_bible_materials,
)
from bestseller.services.novel_categories import load_novel_category_registry
from bestseller.services.premium_state_ledger import (
    materialize_premium_state_snapshot,
    validate_premium_state_ledger,
)
from bestseller.services.writing_presets import GenrePreset, load_writing_preset_catalog

pytestmark = pytest.mark.unit


def test_qingnang_pack_meets_density_targets() -> None:
    materials = _build_qingnang_pack("proj-1")
    counts: dict[str, int] = {}
    for material in materials:
        counts[material.material_type] = counts.get(material.material_type, 0) + 1

    assert len(materials) >= sum(PROJECT_MATERIAL_TARGETS.values())
    for dimension, target in PROJECT_MATERIAL_TARGETS.items():
        assert counts.get(dimension, 0) >= target


@pytest.mark.parametrize(
    ("title", "genre", "sub_genre", "language", "signal", "expected_pack"),
    [
        (
            "Shadowbound to the Crown",
            "Fantasy Romance",
            "Fae & Chosen One",
            "en",
            "shadow sight court bargain exile mystery",
            "english_romantasy",
        ),
        (
            "Breaking Point",
            "Science Fiction / Urban Power Fantasy / Superhero Progression",
            "Superhero",
            "en",
            "Cole reservoir kinetics Sophie deadline Victor Kane",
            "english_superhero_breaking_point",
        ),
        (
            "The Witness Protocol",
            "Science Fiction",
            "Superhero",
            "en",
            "Kade sixty-second mimicry Maya Marcus Mercer",
            "english_superhero_witness_protocol",
        ),
        (
            "代价之鸢",
            "女性成长/末世异能",
            "无CP大女主",
            "zh-CN",
            "方舟城 源初 代价转化 林鸢",
            "female_no_cp_apocalypse",
        ),
        (
            "道种破虚",
            "仙侠升级流",
            "宗门逆袭",
            "zh-CN",
            "宁尘 道种 炼气 宗门资源账",
            "xianxia_upgrade",
        ),
    ],
)
def test_supported_type_packs_meet_density_targets(
    title: str,
    genre: str,
    sub_genre: str,
    language: str,
    signal: str,
    expected_pack: str,
) -> None:
    pack_id, materials = _select_material_pack(
        "proj-1",
        signal,
        title=title,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    counts: dict[str, int] = {}
    for material in materials:
        counts[material.material_type] = counts.get(material.material_type, 0) + 1

    assert pack_id == expected_pack
    assert len(materials) >= sum(PROJECT_MATERIAL_TARGETS.values())
    for dimension, target in PROJECT_MATERIAL_TARGETS.items():
        assert counts.get(dimension, 0) >= target


@pytest.mark.parametrize(
    "pack_id",
    [
        "qingnang",
        "english_romantasy",
        "english_superhero_breaking_point",
        "english_superhero_witness_protocol",
        "english_superhero_progression",
        "female_no_cp_apocalypse",
        "xianxia_upgrade",
    ],
)
def test_supported_packs_seed_valid_premium_capability_metadata(pack_id: str) -> None:
    policy = _decision_policy_for_pack(pack_id)
    ledger = _initial_premium_state_ledger_for_pack(pack_id)

    assert policy and policy["character_name"]
    assert ledger
    report = validate_premium_state_ledger(ledger)
    snapshot = materialize_premium_state_snapshot(ledger)
    assert report.passed is True
    assert snapshot["passed"] is True
    assert snapshot["faction_pressure_queue"]


def _assert_materials_meet_density(materials: list[object]) -> None:
    counts: dict[str, int] = {}
    for material in materials:
        material_type = getattr(material, "material_type")
        counts[material_type] = counts.get(material_type, 0) + 1

    assert len(materials) >= sum(PROJECT_MATERIAL_TARGETS.values())
    for dimension, target in PROJECT_MATERIAL_TARGETS.items():
        assert counts.get(dimension, 0) >= target


@pytest.mark.parametrize(
    "preset",
    load_writing_preset_catalog().genre_presets,
    ids=lambda preset: preset.key,
)
def test_all_writing_genre_presets_select_material_and_premium_pack(
    preset: GenrePreset,
) -> None:
    pack_id, materials = _select_material_pack(
        "proj-1",
        "",
        title=preset.name,
        genre=preset.genre,
        sub_genre=preset.sub_genre,
        language=preset.language,
    )

    assert pack_id is not None
    _assert_materials_meet_density(materials)
    assert _decision_policy_for_pack(pack_id)
    ledger = _initial_premium_state_ledger_for_pack(pack_id)
    assert ledger
    assert validate_premium_state_ledger(ledger).passed


@pytest.mark.parametrize(
    "category_key",
    sorted(load_novel_category_registry()),
)
def test_all_novel_creation_categories_have_density_and_premium_pack(
    category_key: str,
) -> None:
    zh_pack_id = _localized_category_pack_id(category_key, "zh-CN")
    en_pack_id = _localized_category_pack_id(category_key, "en")

    for pack_id in (zh_pack_id, en_pack_id):
        blueprint = _blueprint_for_pack(pack_id)
        assert blueprint is not None
        materials = material_density._build_spec_pack(  # noqa: SLF001
            "proj-1",
            _category_pack_spec(blueprint, pack_id=pack_id),
        )
        _assert_materials_meet_density(materials)
        assert _decision_policy_for_pack(pack_id)
        ledger = _initial_premium_state_ledger_for_pack(pack_id)
        assert ledger
        assert validate_premium_state_ledger(ledger).passed


async def test_hydrate_story_bible_materials_detects_qingnang_dry_run(
    tmp_path: Path,
) -> None:
    package = tmp_path / "qingnang"
    story_bible = package / "story-bible"
    story_bible.mkdir(parents=True)
    (package / "README.md").write_text("青囊 困魂镜 三族 林渊", encoding="utf-8")

    result = await hydrate_story_bible_materials(
        object(),  # type: ignore[arg-type]
        project_id="proj-1",
        package_root=package,
        apply=False,
    )

    assert result["supported_pack"] == "qingnang"
    assert result["candidate_count"] >= sum(PROJECT_MATERIAL_TARGETS.values())
    assert result["applied"] is False


async def test_hydrate_story_bible_materials_apply_refreshes_reference_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "qingnang"
    package.mkdir()
    (package / "README.md").write_text("青囊 困魂镜 三族 林渊", encoding="utf-8")
    material = material_density._mat(  # noqa: SLF001
        "proj-1",
        "world_settings",
        "mirror-debt-city",
        "镜债外溢都市",
        "困魂镜规则外溢到现实城市。",
        {"rules": ["回执外开门"]},
    )
    inserted: list[object] = []

    async def _fake_insert(session: object, mat: object) -> object:
        inserted.append(mat)
        return mat

    async def _fake_refresh(
        session: object,
        *,
        project_id: str,
        include_content_preview: bool = False,
    ) -> dict[str, object]:
        return {
            "project_id": project_id,
            "present": True,
            "line_count": 6,
            "char_count": 120,
        }

    monkeypatch.setattr(
        material_density,
        "_build_qingnang_pack",
        lambda project_id: [material],
    )
    monkeypatch.setattr(material_density, "insert_project_material", _fake_insert)
    monkeypatch.setattr(
        material_density,
        "refresh_project_material_reference_block",
        _fake_refresh,
    )

    session = AsyncMock()
    result = await hydrate_story_bible_materials(
        session,
        project_id="proj-1",
        package_root=package,
        apply=True,
    )

    assert inserted == [material]
    session.flush.assert_awaited_once()
    assert result["reference_block"]["present"] is True

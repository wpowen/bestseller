from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import bestseller.services.material_density as material_density
from bestseller.services.material_density import (
    PROJECT_MATERIAL_TARGETS,
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


_DELETED_SINGLE_BOOK_PACKS = {
    "qingnang",
    "english_romantasy",
    "english_superhero_breaking_point",
    "english_superhero_witness_protocol",
    "female_no_cp_apocalypse",
    "xianxia_upgrade",
}


@pytest.mark.parametrize(
    ("title", "genre", "sub_genre", "language", "signal"),
    [
        (
            "Shadowbound to the Crown",
            "Fantasy Romance",
            "Fae & Chosen One",
            "en",
            "shadowbound shadow sight court bargain exile mystery",
        ),
        (
            "Breaking Point",
            "Science Fiction",
            "Progression",
            "en",
            "breaking point reservoir kinetics sophie deadline",
        ),
        (
            "The Witness Protocol",
            "Science Fiction",
            "Progression",
            "en",
            "witness protocol sixty-second marcus mercer",
        ),
        (
            "代价之鸢",
            "女性成长/末世异能",
            "无CP大女主",
            "zh-CN",
            "方舟城 源初 代价转化 末世异能",
        ),
        (
            "道种破虚",
            "仙侠升级流",
            "宗门逆袭",
            "zh-CN",
            "道种破虚 道种 炼气 宗门资源账",
        ),
        (
            "青囊不语问阴阳",
            "民俗灵异",
            "风水悬疑",
            "zh-CN",
            "青囊 困魂镜 三族 林渊",
        ),
    ],
)
def test_single_book_reference_packs_never_route(
    title: str,
    genre: str,
    sub_genre: str,
    language: str,
    signal: str,
) -> None:
    """2026-07-31 product ruling: historical books' private worlds were
    deleted from framework source. Even an exact-title signal must not
    resurrect them — a new book only ever gets category-level material."""
    pack_id, _materials = _select_material_pack(
        "proj-1",
        signal,
        title=title,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    assert pack_id not in _DELETED_SINGLE_BOOK_PACKS


def test_generic_superhero_pack_still_routes_and_meets_density() -> None:
    pack_id, materials = _select_material_pack(
        "proj-1",
        "superhero urban power progression",
        title="Cape City",
        genre="Superhero",
        sub_genre="Progression",
        language="en",
    )
    counts: dict[str, int] = {}
    for material in materials:
        counts[material.material_type] = counts.get(material.material_type, 0) + 1

    assert pack_id == "english_superhero_progression"
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


def test_generic_premium_pack_accepts_project_protagonist_override() -> None:
    pack_id = "category_eastern_aesthetic_zh"
    policy = _decision_policy_for_pack(pack_id, protagonist_name="苏砚")
    ledger = _initial_premium_state_ledger_for_pack(pack_id, protagonist_name="苏砚")

    assert policy
    assert policy["character_name"] == "苏砚"
    assert ledger
    assert ledger["progression_events"][0]["subject"] == "苏砚"
    assert ledger["relationship_events"][0]["character_a"] == "苏砚"
    assert validate_premium_state_ledger(ledger).passed


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


async def test_hydrate_story_bible_materials_no_longer_recognises_qingnang(
    tmp_path: Path,
) -> None:
    """The qingnang single-book pack was deleted; its package signal must
    return an empty dry report instead of the historical book's world."""
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

    assert result["supported_pack"] != "qingnang"
    assert result["applied"] is False


async def test_hydrate_story_bible_materials_apply_refreshes_reference_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "some-book"
    package.mkdir()
    (package / "README.md").write_text("普通题材 参考包信号", encoding="utf-8")
    material = material_density._mat(  # noqa: SLF001
        "proj-1",
        "world_settings",
        "test-world",
        "测试世界",
        "一条测试用世界规则。",
        {"rules": ["测试规则"]},
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
        "_select_material_pack",
        lambda project_id, package_text, **kwargs: ("test_pack", [material]),
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


@pytest.mark.parametrize(
    ("signal", "title", "genre", "sub_genre"),
    [
        ("道种破虚 道种 炼气 宗门资源账", "道种破虚", "仙侠升级流", "宗门逆袭"),
        ("方舟城 源初 代价转化 末世异能", "代价之鸢", "女性成长/末世异能", "无CP大女主"),
    ],
)
def test_zh_packs_carry_no_baked_personal_names(
    signal: str, title: str, genre: str, sub_genre: str
) -> None:
    """De-homogenisation guard: packs must not ship baked protagonist/ally
    names (宁尘/苏瑶/陆沉/林鸢/...) that leak into every pack-hit book."""
    banned = ("宁尘", "苏瑶", "方域", "陆沉", "林鸢", "姜澄", "周砚宁", "霍沉")
    _pack_id, materials = _select_material_pack(
        "proj-1", signal, title=title, genre=genre, sub_genre=sub_genre, language="zh-CN"
    )
    blob = "\n".join(
        f"{m.name}\n{m.narrative_summary}\n{m.content_json}" for m in materials
    )
    leaked = [name for name in banned if name in blob]
    assert not leaked, f"baked names leaked into {_pack_id}: {leaked}"


@pytest.mark.parametrize("pack_id", ["xianxia_upgrade", "female_no_cp_apocalypse"])
def test_de_named_zh_packs_default_neutral_and_respect_override(pack_id: str) -> None:
    # No upstream name → neutral placeholder, never a baked proper name.
    policy = _decision_policy_for_pack(pack_id)
    ledger = _initial_premium_state_ledger_for_pack(pack_id)
    assert policy["character_name"] == "主角"
    assert ledger["progression_events"][0]["subject"] == "主角"
    assert ledger["relationship_events"][0]["character_a"] == "主角"
    assert validate_premium_state_ledger(ledger).passed

    # Upstream name chosen → flows through consistently.
    named = _initial_premium_state_ledger_for_pack(pack_id, protagonist_name="周临渊")
    assert _decision_policy_for_pack(pack_id, protagonist_name="周临渊")["character_name"] == "周临渊"
    assert named["progression_events"][0]["subject"] == "周临渊"
    assert named["relationship_events"][0]["character_a"] == "周临渊"
    assert validate_premium_state_ledger(named).passed


def test_category_blueprints_carry_no_baked_protagonist_name() -> None:
    # Batch A: the generic category path (most books) must not seed a baked
    # protagonist name (许燃/江晚/沈砚/...) — neutral placeholder unless override.
    from bestseller.services.material_density import _CATEGORY_BLUEPRINTS

    baked = {
        "许燃", "江晚", "沈砚", "顾衡", "周野", "林澈", "陆青舟", "云岫",
        "Rowan Vale", "Mara Vale", "Elise Ward", "Adrien Vale",
        "Kai Mercer", "Vera Lin", "Mira Chen", "Lin Yun",
    }
    for key, bp in _CATEGORY_BLUEPRINTS.items():
        assert bp.protagonist_zh not in baked, f"{key} still bakes {bp.protagonist_zh}"
        assert bp.protagonist_en not in baked, f"{key} still bakes {bp.protagonist_en}"
        assert bp.protagonist_zh == "主角"
        assert bp.protagonist_en == "The Protagonist"
    # Override still flows through.
    assert _decision_policy_for_pack(
        "category_eastern_aesthetic_zh", protagonist_name="沈砚"
    )["character_name"] == "沈砚"
    # No override → neutral.
    assert _decision_policy_for_pack("category_eastern_aesthetic_zh")["character_name"] == "主角"


def test_all_bespoke_packs_carry_no_baked_person_names() -> None:
    """Batch C/D: english + qingnang pilot packs must not ship baked person
    names that recur across books."""
    banned = (
        "Cole", "Sophie", "Kade", "Maya", "Marcus Mercer", "Silas Crane",
        "Elena Vasquez", "Victor Kane", "Rowan", "Nora Chen", "Victor Hale",
        "林渊", "苏婉宁", "孙九斤", "钱婆婆", "陈默", "林家辉", "林正淳", "林远山",
    )
    triggers = [
        ("shadowbound romantasy fae chosen one", "en"),
        ("breaking point cole reservoir kinetics sophie deadline", "en"),
        ("witness protocol kade sixty-second maya marcus mercer", "en"),
        ("superhero urban power", "en"),
        ("青囊 困魂镜 三族", "zh-CN"),
    ]
    for signal, lang in triggers:
        _pack_id, materials = _select_material_pack(
            "proj-1", signal, title=signal, genre=signal, sub_genre=signal, language=lang
        )
        blob = "\n".join(
            f"{m.name}\n{m.narrative_summary}\n{m.content_json}" for m in materials
        )
        leaked = [n for n in banned if n in blob]
        assert not leaked, f"{_pack_id} leaked baked names: {leaked}"

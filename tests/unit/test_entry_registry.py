from bestseller.domain.entry_system import EntryDefinition, EntryRegistry
from bestseller.services.entry_registry import (
    build_entry_coverage_matrix,
    build_fallback_entry_registry,
    entries_from_progression_metadata,
    merge_entry_registries,
    render_entry_registry_prompt_block,
)
from bestseller.services.entry_system_kernel import build_fallback_entry_system_kernel


class _Project:
    genre = "玄幻"
    sub_genre = "修仙"
    category_key = "xianxia"
    target_chapters = 80


def test_build_entry_coverage_matrix_uses_kernel_targets() -> None:
    kernel = build_fallback_entry_system_kernel(_Project())

    matrix = build_entry_coverage_matrix(kernel, target_chapters=80, genre="玄幻")

    assert matrix["target_chapters"] == 80
    assert matrix["pillar_entries"] >= 3
    assert "artifact" in matrix["type_counts"]
    assert matrix["per_volume_minimums"]["major_entry_payoffs"] == 1


def test_build_fallback_entry_registry_creates_taxonomy_refs() -> None:
    kernel = build_fallback_entry_system_kernel(_Project())
    matrix = build_entry_coverage_matrix(kernel, target_chapters=80)

    registry = build_fallback_entry_registry(kernel, matrix)

    assert registry.entries
    assert all(entry.taxonomy_ref in kernel.taxonomy_by_type for entry in registry.entries)
    assert any(entry.tier == "pillar" for entry in registry.entries)
    assert registry.coverage_matrix["target_chapters"] == 80


def test_entry_registry_rejects_duplicate_ids() -> None:
    entry = EntryDefinition(
        entry_id="artifact-core",
        type="artifact",
        name="核心法宝",
        tier="pillar",
        taxonomy_ref="artifact",
        capabilities=("破局",),
        limits=("必须支付代价",),
    )

    try:
        EntryRegistry(entries=(entry, entry))
    except ValueError as exc:
        assert "entry ids must be unique" in str(exc)
    else:
        raise AssertionError("duplicate entry ids should fail")


def test_render_entry_registry_prompt_block_only_shows_active_entries() -> None:
    kernel = build_fallback_entry_system_kernel(_Project())
    matrix = build_entry_coverage_matrix(kernel, target_chapters=20)
    registry = build_fallback_entry_registry(kernel, matrix)
    first = registry.entries[0]
    state = {
        "entry_states": {
            first.entry_id: {"state": "owned", "current_grade": "usable"},
            registry.entries[1].entry_id: {"state": "lost"},
        }
    }

    block = render_entry_registry_prompt_block(registry, current_state=state)

    assert "【词条注册表】" in block
    assert first.name in block
    assert registry.entries[1].name not in block


def test_entries_from_progression_metadata_maps_realm_and_assets() -> None:
    entries = entries_from_progression_metadata(
        {
            "power_system": {
                "name": "灵脉修行",
                "realms": [{"name": "练气"}, {"name": "筑基"}],
                "resources": ["灵石"],
                "techniques": ["御剑术"],
                "artifacts": ["身份令"],
            }
        }
    )

    types = {entry.type for entry in entries}
    assert {"cultivation_method", "technique", "artifact", "resource"} <= types


def test_entries_from_progression_metadata_clamps_long_legacy_names() -> None:
    long_name = "双轨并行互补体系" * 40

    entries = entries_from_progression_metadata(
        {
            "power_system": {
                "name": long_name,
                "techniques": [{"name": long_name}],
            }
        }
    )

    assert entries
    assert all(len(entry.entry_id) <= 160 for entry in entries)
    assert all(len(entry.name) <= 200 for entry in entries)
    assert len(entries[0].entry_id) == 160
    assert "-" in entries[0].entry_id[-11:]


def test_merge_entry_registries_deduplicates_by_id() -> None:
    kernel = build_fallback_entry_system_kernel(_Project())
    matrix = build_entry_coverage_matrix(kernel, target_chapters=20)
    registry = build_fallback_entry_registry(kernel, matrix)

    merged = merge_entry_registries(registry, registry)

    assert len(merged.entries) == len(registry.entries)

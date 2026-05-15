from __future__ import annotations

from bestseller.domain.entry_system import (
    EntryDefinition,
    EntryEvent,
    EntryGradeLadder,
    EntryGradeLevel,
    EntryRegistry,
    EntrySystemKernel,
    EntryTypeDefinition,
)


def test_entry_system_kernel_accepts_minimal_valid_payload() -> None:
    kernel = EntrySystemKernel(
        system_promise="本书所有词条都必须产生状态变化和可见代价。",
        taxonomy=(
            EntryTypeDefinition(
                type="artifact",
                label="法宝",
                allowed_roles=("weapon", "proof"),
                required_fields=("origin", "capabilities", "limits"),
            ),
        ),
        grade_ladders=(
            EntryGradeLadder(
                ladder_key="artifact_grade",
                label="法宝品阶",
                levels=(
                    EntryGradeLevel(
                        key="dormant",
                        name="沉眠",
                        capability_ceiling="只能被动响应",
                    ),
                ),
                promotion_rule="升级必须有触发和代价。",
            ),
        ),
        uniqueness_rules=("同功能主法宝不得重复。",),
    )

    assert kernel.taxonomy[0].type == "artifact"
    assert kernel.taxonomy_by_type["artifact"].label == "法宝"
    assert kernel.grade_ladders[0].levels[0].key == "dormant"


def test_major_entry_requires_limits_for_gate_consumers() -> None:
    entry = EntryDefinition(
        entry_id="artifact-core",
        type="artifact",
        name="核心法宝",
        tier="pillar",
        taxonomy_ref="artifact",
        capabilities=("破阵",),
        limits=(),
        narrative_roles=("key",),
    )

    assert entry.is_major is True
    assert entry.has_limits is False


def test_entry_event_requires_trigger_for_state_change() -> None:
    event = EntryEvent(
        chapter_number=3,
        entry_id="artifact-core",
        event_type="upgraded",
        trigger="公开见证",
        from_state="owned",
        to_state="bonded",
    )

    assert event.trigger == "公开见证"


def test_registry_deduplicates_and_indexes_entries() -> None:
    registry = EntryRegistry(
        entries=(
            EntryDefinition(
                entry_id="artifact-core",
                type="artifact",
                name="核心法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                capabilities=("破阵",),
                limits=("不可无代价破境",),
                narrative_roles=("key",),
            ),
        ),
    )

    assert registry.by_id["artifact-core"].name == "核心法宝"

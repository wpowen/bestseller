from bestseller.domain.entry_system import EntryDefinition, EntryEvent, EntryRegistry
from bestseller.services.entry_state_ledger import (
    apply_entry_events,
    build_entry_migration_report,
    current_entry_state,
    detect_stale_entries,
    render_entry_state_ledger_block,
)


def _registry() -> EntryRegistry:
    return EntryRegistry(
        entries=(
            EntryDefinition(
                entry_id="artifact-core",
                type="artifact",
                name="核心法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                current_grade="seeded",
                capabilities=("破局",),
                limits=("使用后暴露",),
            ),
            EntryDefinition(
                entry_id="resource-stone",
                type="resource",
                name="灵石",
                tier="supporting",
                taxonomy_ref="resource",
                capabilities=("支付消耗",),
                limits=("数量有限",),
            ),
        )
    )


def test_apply_acquired_event_sets_owner_and_state() -> None:
    snapshot = apply_entry_events(
        _registry(),
        (
            EntryEvent(
                chapter_number=2,
                entry_id="artifact-core",
                event_type="acquired",
                trigger="试炼所得",
                to_state="owned",
                owner_after="宁尘",
            ),
        ),
    )

    state = current_entry_state(snapshot, "artifact-core")
    assert state["state"] == "owned"
    assert state["owner"] == "宁尘"
    assert state["last_event_chapter"] == 2


def test_apply_upgraded_event_changes_grade_and_keeps_note() -> None:
    snapshot = apply_entry_events(
        _registry(),
        (
            EntryEvent(
                chapter_number=5,
                entry_id="artifact-core",
                event_type="upgraded",
                trigger="公开见证",
                to_grade="usable",
                continuity_note="之后所有战斗必须承认它已可主动使用。",
            ),
        ),
    )

    state = current_entry_state(snapshot, "artifact-core")
    assert state["current_grade"] == "usable"
    assert "之后所有战斗" in state["continuity_notes"][0]


def test_detect_stale_major_entries() -> None:
    findings = detect_stale_entries(
        _registry(),
        (
            EntryEvent(
                chapter_number=1,
                entry_id="artifact-core",
                event_type="introduced",
                trigger="开篇展示",
            ),
        ),
        current_chapter=20,
        max_gap=6,
    )

    assert findings[0].code == "stale_major_entry"
    assert findings[0].entry_id == "artifact-core"


def test_build_entry_migration_report_for_changed_limits() -> None:
    old = _registry()
    new_entry = old.entries[0].model_copy(update={"limits": ("使用后会永久损伤",)})
    new = EntryRegistry(entries=(new_entry, old.entries[1]))

    report = build_entry_migration_report(old, new, reason="后续章节提高代价")

    assert report.changes[0].change_type == "limits_changed"
    assert report.changes[0].requires_story_patch is True
    assert report.required_repairs


def test_render_entry_state_ledger_block_lists_current_state() -> None:
    snapshot = apply_entry_events(
        _registry(),
        (
            EntryEvent(
                chapter_number=3,
                entry_id="artifact-core",
                event_type="acquired",
                trigger="代价交换",
                to_state="owned",
                cost_paid="失去安全身份",
            ),
        ),
    )

    block = render_entry_state_ledger_block(snapshot)

    assert "【词条状态账本】" in block
    assert "artifact-core" in block
    assert "失去安全身份" in block

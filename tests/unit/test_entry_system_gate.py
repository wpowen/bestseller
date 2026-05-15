from bestseller.domain.entry_system import (
    EntryDefinition,
    EntryEvent,
    EntryGradeLadder,
    EntryRegistry,
    EntrySystemKernel,
    EntryTypeDefinition,
)
from bestseller.services.entry_system_gate import (
    validate_entry_events,
    validate_entry_system_package,
    validate_kernel,
    validate_registry,
    validate_reward_specificity,
)


def _kernel() -> EntrySystemKernel:
    return EntrySystemKernel(
        system_promise="词条必须有边界和代价。",
        taxonomy=(
            EntryTypeDefinition(
                type="artifact",
                label="法宝",
                required_fields=("capabilities", "limits"),
            ),
        ),
        grade_ladders=(
            EntryGradeLadder(
                ladder_key="artifact_grade",
                label="法宝品阶",
                levels=(),
                promotion_rule=" ",
                applies_to=("artifact",),
            ),
        ),
    )


def test_validate_registry_flags_major_artifact_without_limits() -> None:
    registry = EntryRegistry(
        entries=(
            EntryDefinition(
                entry_id="artifact-core",
                type="artifact",
                name="核心法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                capabilities=("破局",),
            ),
        )
    )

    findings = validate_registry(_kernel(), registry)

    assert {finding.code for finding in findings} >= {"entry_limit_missing"}


def test_validate_kernel_flags_missing_promotion_rule() -> None:
    findings = validate_kernel(_kernel())

    assert "grade_ladder_promotion_rule_missing" in {finding.code for finding in findings}


def test_validate_entry_events_flags_unknown_entry_and_missing_trigger() -> None:
    registry = EntryRegistry(
        entries=(
            EntryDefinition(
                entry_id="artifact-core",
                type="artifact",
                name="核心法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                limits=("有限制",),
            ),
        )
    )

    findings = validate_entry_events(
        registry,
        (
            {"chapter_number": 1, "entry_id": "missing", "event_type": "used", "trigger": "使用"},
            {"chapter_number": 2, "entry_id": "artifact-core", "event_type": "upgraded"},
        ),
    )

    codes = {finding.code for finding in findings}
    assert "entry_event_unknown_entry" in codes
    assert "entry_event_trigger_missing" in codes


def test_validate_registry_flags_duplicate_major_narrative_role() -> None:
    registry = EntryRegistry(
        entries=(
            EntryDefinition(
                entry_id="artifact-a",
                type="artifact",
                name="甲法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                limits=("有限制",),
                narrative_roles=("key",),
            ),
            EntryDefinition(
                entry_id="artifact-b",
                type="artifact",
                name="乙法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                limits=("有限制",),
                narrative_roles=("key",),
            ),
        )
    )

    findings = validate_registry(_kernel(), registry)

    assert "duplicate_major_entry_role" in {finding.code for finding in findings}


def test_validate_reward_specificity_flags_vague_reward() -> None:
    findings = validate_reward_specificity("主角获得奖励并变强。")

    assert findings[0].code == "entry_reward_too_vague"


def test_validate_entry_system_package_returns_report() -> None:
    registry = EntryRegistry(
        entries=(
            EntryDefinition(
                entry_id="artifact-core",
                type="artifact",
                name="核心法宝",
                tier="pillar",
                taxonomy_ref="artifact",
                capabilities=("破局",),
            ),
        )
    )

    report = validate_entry_system_package(
        _kernel(),
        registry,
        (
            EntryEvent(
                chapter_number=1,
                entry_id="artifact-core",
                event_type="used",
                trigger="公开使用",
            ),
        ),
    )

    assert report.passed is False
    assert report.to_dict()["findings"]

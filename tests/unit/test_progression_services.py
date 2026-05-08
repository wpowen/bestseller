from __future__ import annotations

import pytest

from bestseller.domain.progression import (
    Artifact,
    BreakthroughCause,
    BreakthroughCauseKind,
    BreakthroughEvent,
    PowerRealm,
    PowerSystem,
    ProgressionBottleneck,
    ResourceLedger,
    ResourceLedgerEntry,
    Technique,
)
from bestseller.services.progression import (
    build_progression_constraint_block,
    build_progression_context_block,
    materialize_power_system,
    materialize_progression_context,
    realm_index,
    resource_balance,
    validate_artifact_capability,
    validate_breakthrough,
    validate_realm_ladder,
    validate_resource_spend,
    validate_technique_use,
)

pytestmark = pytest.mark.unit


def _xianxia_system() -> PowerSystem:
    return PowerSystem(
        key="xianxia",
        name="修仙境界",
        realms=(
            PowerRealm(name="炼气", order=0, aliases=("炼气期",)),
            PowerRealm(name="筑基", order=1, aliases=("筑基期",)),
            PowerRealm(name="金丹", order=2, aliases=("金丹境",)),
        ),
        bottlenecks=(
            ProgressionBottleneck(
                key="foundation_bottleneck",
                at_realm="炼气",
                target_realm="筑基",
                description="炼气突破筑基必须有筑基丹或等价机缘。",
                required_cause_kinds=(BreakthroughCauseKind.RESOURCE,),
                required_resource_keys=("筑基丹",),
            ),
        ),
    )


def _ledger() -> ResourceLedger:
    return ResourceLedger(
        owner="韩立",
        entries=(
            ResourceLedgerEntry(
                resource_key="筑基丹",
                amount=1,
                chapter_no=18,
                source="血色禁地试炼",
                reason="击败强敌后取得丹药",
            ),
        ),
    )


def _plain_system() -> PowerSystem:
    return PowerSystem(
        key="plain_xianxia",
        name="基础修仙境界",
        realms=(
            PowerRealm(name="炼气", order=0),
            PowerRealm(name="筑基", order=1),
            PowerRealm(name="金丹", order=2),
        ),
    )


def test_realm_index_matches_alias_and_suffix() -> None:
    system = _xianxia_system()

    assert realm_index(system, "炼气三层") == 0
    assert realm_index(system, "筑基期") == 1
    assert realm_index(system, "金丹境") == 2
    assert realm_index(system, "元婴") == -1


def test_materialize_power_system_from_world_spec() -> None:
    system = materialize_power_system(
        {
            "world_name": "修仙界",
            "power_system": {
                "name": "修仙境界",
                "tiers": ["炼气", "筑基", "金丹"],
                "acquisition_method": "吐纳灵气并突破瓶颈",
                "hard_limits": "突破必须有丹药、功法或顿悟。",
                "bottlenecks": [
                    {
                        "key": "foundation",
                        "at_realm": "炼气",
                        "target_realm": "筑基",
                        "description": "炼气到筑基必须筑基丹或等价机缘。",
                        "required_cause_kinds": ["resource"],
                        "resources": ["筑基丹"],
                    },
                ],
            },
        },
    )

    assert system.name == "修仙境界"
    assert [realm.name for realm in system.ordered_realms] == ["炼气", "筑基", "金丹"]
    assert system.bottlenecks[0].required_cause_kinds == (BreakthroughCauseKind.RESOURCE,)
    assert "吐纳灵气" in system.terminology_notes[0]


def test_materialize_progression_context_from_story_bible_artifacts() -> None:
    context = materialize_progression_context(
        {
            "power_system": {
                "name": "修仙境界",
                "tiers": ["炼气", "筑基", "金丹"],
                "bottlenecks": [
                    {
                        "key": "foundation",
                        "at_realm": "炼气",
                        "target_realm": "筑基",
                        "description": "炼气到筑基必须筑基丹或等价机缘。",
                        "required_cause_kinds": ["resource"],
                        "resources": ["筑基丹"],
                    },
                ],
                "techniques": [
                    {
                        "key": "changchun",
                        "name": "长春功",
                        "required_realm": "炼气",
                        "unlocks_realms": ["筑基"],
                    },
                ],
            },
        },
        {
            "protagonist": {
                "name": "韩立",
                "power_tier": "炼气十层",
                "resources": [
                    {
                        "key": "筑基丹",
                        "amount": 1,
                        "chapter_no": 18,
                        "source": "血色禁地试炼",
                    },
                ],
                "artifacts": [
                    {
                        "key": "green_bottle",
                        "name": "掌天瓶",
                        "capabilities": ["催熟灵药"],
                        "known_limit": "只能催熟灵药, 不能直接提升境界。",
                    },
                ],
            },
        },
        [
            {
                "volume_number": 1,
                "title": "初入仙途",
                "opening_state": {"protagonist_power_tier": "炼气十层"},
            },
        ],
        current_volume=1,
    )

    assert context.character_realms["韩立"] == "炼气十层"
    assert context.active_bottleneck is not None
    assert context.active_bottleneck.target_realm == "筑基"
    assert resource_balance(context.resource_ledgers["韩立"], "筑基丹") == 1
    assert context.techniques[0].name == "长春功"
    assert context.artifacts[0].name == "掌天瓶"


def test_realm_ladder_reports_structural_errors() -> None:
    broken_system = PowerSystem(
        key="broken",
        name="破损体系",
        realms=(
            PowerRealm(name="炼气", order=0),
            PowerRealm(name="筑基", order=0, aliases=("炼气期",)),
        ),
        bottlenecks=(
            ProgressionBottleneck(
                key="bad_bottleneck",
                at_realm="未知",
                target_realm="元婴",
                description="指向不存在的境界。",
            ),
        ),
    )

    report = validate_realm_ladder(broken_system)

    assert not report.passed
    assert {
        "DUPLICATE_REALM_ORDER",
        "DUPLICATE_REALM_NAME",
        "UNKNOWN_BOTTLENECK_SOURCE",
        "UNKNOWN_BOTTLENECK_TARGET",
    }.issubset({finding.code for finding in report.findings})


def test_missing_realm_ladder_fails() -> None:
    report = validate_realm_ladder(PowerSystem(key="empty", name="空体系"))

    assert not report.passed
    assert {finding.code for finding in report.findings} == {"MISSING_REALMS"}


def test_empty_breakthrough_fails_as_unearned() -> None:
    report = validate_breakthrough(
        _xianxia_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="炼气",
            to_realm="筑基",
            chapter_no=20,
            causes=(),
        ),
    )

    assert not report.passed
    assert {finding.code for finding in report.findings} == {"UNEARNED_BREAKTHROUGH"}


def test_resource_supported_breakthrough_passes() -> None:
    report = validate_breakthrough(
        _xianxia_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="炼气期",
            to_realm="筑基",
            chapter_no=20,
            causes=(
                BreakthroughCause(
                    kind=BreakthroughCauseKind.RESOURCE,
                    ref_key="筑基丹",
                    detail="此前试炼中取得筑基丹, 本章消耗丹药冲关。",
                ),
            ),
        ),
        resource_ledger=_ledger(),
    )

    assert report.passed
    assert report.findings == ()


def test_resource_cause_without_available_balance_fails() -> None:
    empty_ledger = ResourceLedger(owner="韩立")
    report = validate_breakthrough(
        _xianxia_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="炼气",
            to_realm="筑基",
            chapter_no=20,
            causes=(
                BreakthroughCause(
                    kind=BreakthroughCauseKind.RESOURCE,
                    ref_key="筑基丹",
                    detail="试图直接消耗筑基丹。",
                ),
            ),
        ),
        resource_ledger=empty_ledger,
    )

    assert not report.passed
    assert "RESOURCE_CAUSE_UNAVAILABLE" in {finding.code for finding in report.findings}


def test_resource_spend_uses_signed_ledger_balance() -> None:
    ledger = ResourceLedger(
        owner="韩立",
        entries=(
            ResourceLedgerEntry(
                resource_key="灵石",
                amount=5,
                chapter_no=3,
                source="宗门月例",
            ),
            ResourceLedgerEntry(
                resource_key="灵石",
                amount=-2,
                chapter_no=4,
                source="坊市交易",
                reason="购买符箓",
            ),
        ),
    )

    assert resource_balance(ledger, "灵石") == 3
    assert validate_resource_spend(ledger, "灵石", 3).passed
    assert not validate_resource_spend(ledger, "灵石", 4).passed


def test_invalid_resource_spend_amount_fails() -> None:
    report = validate_resource_spend(_ledger(), "筑基丹", 0)

    assert not report.passed
    assert {finding.code for finding in report.findings} == {"INVALID_RESOURCE_SPEND"}


def test_technique_prerequisite_blocks_early_use() -> None:
    technique = Technique(
        key="azure_sword",
        name="青元剑诀",
        required_realm="筑基",
        unlocks_realms=("金丹",),
    )

    report = validate_technique_use(_xianxia_system(), technique, "炼气")

    assert not report.passed
    assert {finding.code for finding in report.findings} == {"TECHNIQUE_PREREQUISITE_UNMET"}


def test_technique_unknown_current_and_required_realms_fail() -> None:
    unknown_current = validate_technique_use(
        _plain_system(),
        Technique(key="core", name="结丹诀", required_realm="筑基"),
        "未知",
    )
    unknown_required = validate_technique_use(
        _plain_system(),
        Technique(key="nascent", name="元婴秘法", required_realm="元婴"),
        "金丹",
    )

    assert {finding.code for finding in unknown_current.findings} == {"UNKNOWN_CURRENT_REALM"}
    assert {finding.code for finding in unknown_required.findings} == {
        "UNKNOWN_TECHNIQUE_REQUIREMENT",
    }


def test_technique_supported_breakthrough_passes_after_prerequisite() -> None:
    technique = Technique(
        key="azure_sword",
        name="青元剑诀",
        required_realm="筑基",
        unlocks_realms=("金丹",),
    )
    report = validate_breakthrough(
        _xianxia_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="筑基",
            to_realm="金丹",
            chapter_no=120,
            causes=(
                BreakthroughCause(
                    kind=BreakthroughCauseKind.TECHNIQUE,
                    ref_key="azure_sword",
                    detail="长期修炼青元剑诀, 瓶颈处以剑诀化解。",
                ),
            ),
        ),
        techniques=(technique,),
    )

    assert report.passed


def test_technique_cause_must_reference_known_unlocking_technique() -> None:
    missing_ref = validate_breakthrough(
        _plain_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="筑基",
            to_realm="金丹",
            chapter_no=40,
            causes=(BreakthroughCause(kind=BreakthroughCauseKind.TECHNIQUE, detail="剑诀破境"),),
        ),
    )
    wrong_unlock = validate_breakthrough(
        _plain_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="筑基",
            to_realm="金丹",
            chapter_no=40,
            causes=(
                BreakthroughCause(
                    kind=BreakthroughCauseKind.TECHNIQUE,
                    ref_key="azure_sword",
                    detail="剑诀破境",
                ),
            ),
        ),
        techniques=(
            Technique(
                key="azure_sword",
                name="青元剑诀",
                required_realm="筑基",
                unlocks_realms=("元婴",),
            ),
        ),
    )

    assert {finding.code for finding in missing_ref.findings} == {"MISSING_TECHNIQUE_REF"}
    assert {finding.code for finding in wrong_unlock.findings} == {
        "TECHNIQUE_DOES_NOT_UNLOCK_REALM",
    }


def test_artifact_capability_limit_is_enforced() -> None:
    artifact = Artifact(
        key="green_bottle",
        name="掌天瓶",
        capabilities=("催熟灵药",),
        unlocks_realms=("筑基",),
    )

    assert validate_artifact_capability(artifact, "催熟灵药").passed
    report = validate_artifact_capability(artifact, "直接结丹")
    assert not report.passed
    assert {finding.code for finding in report.findings} == {"ARTIFACT_CAPABILITY_MISSING"}


def test_artifact_cause_must_reference_active_unlocking_artifact() -> None:
    inactive = validate_breakthrough(
        _plain_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="筑基",
            to_realm="金丹",
            chapter_no=40,
            causes=(
                BreakthroughCause(
                    kind=BreakthroughCauseKind.ARTIFACT,
                    ref_key="green_bottle",
                    detail="借助法宝破境",
                ),
            ),
        ),
        artifacts=(
            Artifact(
                key="green_bottle",
                name="掌天瓶",
                active=False,
                unlocks_realms=("元婴",),
            ),
        ),
    )

    assert not inactive.passed
    assert {
        "ARTIFACT_INACTIVE",
        "ARTIFACT_DOES_NOT_UNLOCK_REALM",
    }.issubset({finding.code for finding in inactive.findings})


def test_non_forward_breakthrough_transition_fails() -> None:
    report = validate_breakthrough(
        _plain_system(),
        BreakthroughEvent(
            character_name="韩立",
            from_realm="金丹",
            to_realm="筑基",
            chapter_no=99,
            causes=(BreakthroughCause(kind=BreakthroughCauseKind.INSIGHT, detail="强行降级"),),
        ),
    )

    assert not report.passed
    assert "INVALID_REALM_TRANSITION" in {finding.code for finding in report.findings}


def test_progression_constraint_block_exposes_hard_prompt_rules() -> None:
    block = build_progression_constraint_block(
        _xianxia_system(),
        {"韩立": "炼气十层"},
    )

    assert "进阶体系约束" in block
    assert "炼气 → 筑基 → 金丹" in block
    assert "突破必须有资源/功法/顿悟/试炼等因果支撑" in block


def test_progression_constraint_block_supports_english_and_empty_inputs() -> None:
    empty_block = build_progression_constraint_block(_plain_system(), {})
    english_block = build_progression_constraint_block(
        _plain_system(),
        {"Han Li": "Qi Condensation"},
        language="en",
    )

    assert empty_block == ""
    assert "[PROGRESSION CONSTRAINTS]" in english_block
    assert "breakthroughs require explicit" in english_block


def test_progression_context_block_lists_active_mechanics() -> None:
    context = materialize_progression_context(
        {
            "power_system": {
                "name": "修仙境界",
                "tiers": ["炼气", "筑基"],
                "protagonist_starting_tier": "炼气",
            },
        },
        {
            "protagonist": {
                "name": "韩立",
                "power_tier": "炼气",
                "resources": [{"key": "筑基丹", "amount": 1, "source": "禁地"}],
                "artifacts": [{"key": "green_bottle", "name": "掌天瓶"}],
            },
        },
    )

    block = build_progression_context_block(context)

    assert "进阶体系约束" in block
    assert "当前瓶颈" in block
    assert "筑基丹=1" in block
    assert "掌天瓶" in block
    assert "不得空升级" in block

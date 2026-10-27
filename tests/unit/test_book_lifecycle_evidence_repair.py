from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, CharacterModel, ProjectModel
from bestseller.services.book_lifecycle_evidence import (
    build_book_lifecycle_evidence_from_project_state,
)
from bestseller.services.book_lifecycle_evidence_repair import (
    _bind_distilled_strategy_to_planning,
    _distilled_strategy_payload,
    build_lifecycle_book_spec,
    build_lifecycle_cast_spec,
    build_lifecycle_story_design_kernel,
    build_lifecycle_volume_plan,
    build_lifecycle_world_spec,
    build_reverse_outline_payload,
    repair_character_identity_and_personhood,
)
from bestseller.services.character_intelligence.strategy import (
    AdoptedReferenceUnavailable,
    adopt_reference_projection,
    required_reference_projection,
)
from bestseller.services.planning_kernel import (
    build_project_planning_kernel,
    evaluate_prewrite_readiness,
)
from bestseller.services.reverse_outline_gate import evaluate_reverse_outline_gate
from bestseller.services.prompt_source_policy import stamp_reference_cache
from bestseller.services.story_design_kernel import story_design_kernel_from_dict

pytestmark = pytest.mark.unit


def _project() -> ProjectModel:
    return ProjectModel(
        id=uuid4(),
        slug="legacy-test",
        title="青囊不语问阴阳",
        language="zh-CN",
        genre="惊悚灵异",
        sub_genre="驱魔探案综合",
        target_word_count=1_500_000,
        target_chapters=500,
        audience="男频",
        reader_contract_json={"core_fantasy": "证据推进必须改变人物选择"},
        dramatic_question="林渊如何查明公开证据背后的真相？",
        theme_statement="真相与关系代价",
        metadata_json={"canonical_category": "suspense-mystery"},
    )


def test_lifecycle_evidence_repair_builds_target_length_planning_contract() -> None:
    project = _project()
    metadata = dict(project.metadata_json)
    volume_plan = build_lifecycle_volume_plan(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    book_spec = build_lifecycle_book_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        protagonist_name="林渊",
    )
    world_spec = build_lifecycle_world_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    cast_spec = build_lifecycle_cast_spec(
        project,
        characters=[CharacterModel(name="林渊", role="protagonist")],
    )
    story_design = build_lifecycle_story_design_kernel(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )

    assert len(volume_plan) >= 10
    assert volume_plan[-1]["chapter_range"] == "451-500"
    assert story_design_kernel_from_dict(dict(story_design)).reader_promise

    kernel = build_project_planning_kernel(
        project,
        project_metadata={
            **metadata,
            "benchmark_works": ["anonymous-category-benchmark:suspense-mystery"],
            "unique_hook": book_spec["unique_hook"],
        },
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
        story_design_kernel=story_design,
    )
    report = evaluate_prewrite_readiness(
        kernel,
        genre=project.genre,
        sub_genre=project.sub_genre,
        target_chapters=project.target_chapters,
    )

    assert report.passed is True


def test_lifecycle_evidence_repair_consumes_distilled_strategy_card() -> None:
    strategy_card = {
        "aggregate_key": "suspense-mystery",
        "maturity_score": 0.72,
        "maturity_status": "review",
        "source_count": 3,
        "provenance_status": "anonymous_aggregate",
        "privacy_status": "redacted",
        "genre_profile_key": "suspense-mystery",
        "selected_mechanisms": [
            {
                "mechanism_id": "evidence-ledger-pressure",
                "source_confidence": 0.86,
                "design_role": "series_engine",
                "adaptation_instruction": "转化为本项目证据账本。",
                "required_project_specific_binding": "绑定到林渊每卷必须付出的公开证据代价。",
                "failure_mode": "证据只作背景说明。",
            }
        ],
        "required_state_variables": ["evidence_pressure"],
        "required_change_vectors": ["public_cost_reversal"],
        "reader_reward_mix": ["deduction_payoff"],
        "anti_copy_boundaries": ["exact-source-opening-chain"],
        "worldview_bindings": {
            "distilled_mechanism_bindings": [
                {
                    "aggregate_key": "suspense-mystery",
                    "mechanism_id": "evidence-ledger-pressure",
                    "design_role": "world_pressure",
                    "source_confidence": 0.86,
                    "required_project_binding": "把证据账本改写成本书独有的公开代价。",
                    "state_variables": ["evidence_pressure"],
                    "required_cost": "每次推进都会暴露一层关系风险。",
                    "anti_copy_boundaries": ["exact-source-opening-chain"],
                }
            ],
            "state_variables": [
                {
                    "key": "evidence_pressure",
                    "variable_type": "information",
                    "current_value": "线索压力尚未公开。",
                    "desired_direction": "逐卷转为公开代价。",
                    "change_triggers": ["证据公开", "关系反制"],
                    "failure_mode": "推理退化为资料说明。",
                    "source_mechanism_ids": ["evidence-ledger-pressure"],
                }
            ],
        },
    }
    project = _project()
    metadata = {**dict(project.metadata_json), "distilled_strategy_card": strategy_card}
    volume_plan = build_lifecycle_volume_plan(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    book_spec = build_lifecycle_book_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        protagonist_name="林渊",
    )
    world_spec = build_lifecycle_world_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    cast_spec = build_lifecycle_cast_spec(
        project,
        characters=[CharacterModel(name="林渊", role="protagonist")],
    )
    story_design = build_lifecycle_story_design_kernel(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )
    # Receipts bind the actual final context, including the generated hook.
    project.metadata_json = {
        **metadata,
        "benchmark_works": ["anonymous-category-benchmark:suspense-mystery"],
        "unique_hook": book_spec["unique_hook"],
    }
    stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
    adopt_reference_projection(project, "distilled_strategy_card", adopted_by="fixture-author", required=True)
    metadata = dict(project.metadata_json)
    adopted_card = required_reference_projection(metadata, "distilled_strategy_card", project=project)
    assert adopted_card == strategy_card
    story_design, volume_plan, bound = _bind_distilled_strategy_to_planning(
        story_design=story_design,
        volume_plan=volume_plan,
        strategy_card=adopted_card,
    )

    assert bound is True
    assert "exact-source-opening-chain" not in str(story_design)
    assert volume_plan[0]["distilled_state_delta"] == (
        "evidence_pressure -> public_cost_reversal"
    )

    kernel = build_project_planning_kernel(
        project,
        project_metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
        story_design_kernel=story_design,
    )
    report = evaluate_prewrite_readiness(
        kernel,
        genre=project.genre,
        sub_genre=project.sub_genre,
        target_chapters=project.target_chapters,
    )
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is True
    assert "distilled_strategy_not_consumed" not in codes
    assert report.capability_snapshot["distilled_strategy_ready"] is True


@pytest.mark.parametrize("receipted", [False, True])
def test_optional_lifecycle_card_does_not_rewrite_planning(receipted: bool) -> None:
    project = _project()
    project.metadata_json["distilled_strategy_card"] = {
        "aggregate_key": "suspense-mystery",
        "required_state_variables": ["unselected_state"],
        "required_change_vectors": ["unselected_change"],
    }
    if receipted:
        stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
        adopt_reference_projection(project, "distilled_strategy_card", adopted_by="fixture-author", required=False)
    metadata = deepcopy(project.metadata_json)
    story = {"reader_promise": "作者原有承诺"}
    volumes = [{"volume_number": 1, "core_payoff": "作者原有兑现"}]
    required_card = required_reference_projection(metadata, "distilled_strategy_card", project=project)

    patched_story, patched_volumes, bound = _bind_distilled_strategy_to_planning(
        story_design=story, volume_plan=volumes, strategy_card=required_card,
    )

    assert required_card is None
    assert bound is False
    assert patched_story == story
    assert patched_volumes == volumes
    assert project.metadata_json == metadata
    if not receipted:
        assert _distilled_strategy_payload(
            project, metadata, category_key=None, protagonist_name="林渊",
        ) is None


def test_lifecycle_required_card_without_receipt_is_not_rebound_from_raw_value() -> None:
    project = _project()
    project.metadata_json["distilled_strategy_card"] = {
        "aggregate_key": "suspense-mystery", "required_state_variables": ["evidence_pressure"],
    }
    stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
    adopt_reference_projection(project, "distilled_strategy_card", adopted_by="fixture-author", required=True)
    project.metadata_json["prompt_reference_receipts"].pop("distilled_strategy_card")
    metadata = deepcopy(project.metadata_json)

    with pytest.raises(AdoptedReferenceUnavailable):
        _distilled_strategy_payload(project, metadata, category_key=None, protagonist_name="林渊")

    assert project.metadata_json == metadata


def test_lifecycle_evidence_repair_builds_reverse_outline_that_passes_gate() -> None:
    project = _project()
    metadata = dict(project.metadata_json)
    volume_plan = build_lifecycle_volume_plan(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    book_spec = build_lifecycle_book_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        protagonist_name="林渊",
    )
    world_spec = build_lifecycle_world_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    cast_spec = build_lifecycle_cast_spec(
        project,
        characters=[CharacterModel(name="林渊", role="protagonist")],
    )
    story_design = build_lifecycle_story_design_kernel(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )
    chapters = [
        ChapterModel(
            chapter_number=1,
            title="纸符开眼",
            chapter_goal="林渊获得第一条可验证线索。",
            main_conflict="证据链被封锁，林渊必须付出代价才能获得推进资格。",
            hook_description="线索留下新的身份风险。",
            target_word_count=3000,
        )
    ]

    outline = build_reverse_outline_payload(
        chapters=chapters,
        story_design_kernel=story_design,
    )
    report = evaluate_reverse_outline_gate(story_design, outline)

    assert report.passed is True


def test_build_reverse_outline_payload_neutralizes_forbidden_default_motifs() -> None:
    story_design = {
        "change_vectors": ["关系变化", "代价变化", "证据推进"],
    }
    chapter = ChapterModel(
        chapter_number=1,
        title="遗嘱之夜",
        chapter_goal="父母失踪后主角为了查清身世，必须追击失踪旧案。",
        main_conflict="亲属创伤默认驱动触发角色崩溃，他需要证明身世旧案。",
        target_word_count=2500,
    )

    outline = build_reverse_outline_payload(chapters=[chapter], story_design_kernel=story_design)
    rebuilt = outline["chapters"][0]

    assert "家庭创伤或身世旧案默认驱动" not in rebuilt["goal"]
    assert "亲属创伤默认驱动" not in rebuilt["goal"]
    assert "身世旧案" not in rebuilt["main_conflict"]
    assert "失踪" not in rebuilt["goal"]
    assert "牵引" in rebuilt["goal"]


def test_lifecycle_evidence_repair_backfills_identity_and_personhood() -> None:
    project = _project()
    character = CharacterModel(name="何仙姑", role="supporting")
    mirror = CharacterModel(
        name="镜影林渊",
        role="supporting",
        metadata_json={"aliases": ["何仙姑", "镜影"]},
    )

    manifest, identity_updates, personhood_updates = repair_character_identity_and_personhood(
        project,
        [character, mirror],
        dry_run=False,
    )

    assert manifest[0]["name"] == "何仙姑"
    assert manifest[1]["aliases"] == ["镜影"]
    assert identity_updates == 2
    assert personhood_updates == 2
    assert character.metadata_json["pronoun_set_zh"] == "TA"
    assert character.goal
    assert character.fear

    project.metadata_json = {
        **dict(project.metadata_json),
        "identity_manifest_status": "locked",
        "identity_manifest": manifest,
        "character_drama_map": {"source": "test"},
        "cast_spec": {"protagonist": {"name": "何仙姑"}},
    }
    evidence = build_book_lifecycle_evidence_from_project_state(
        project,
        [character, mirror],
    )
    character_report = evidence["character_report"]
    assert character_report["character_gate_report"]["metrics"][
        "identity_manifest_duplicate_count"
    ] == 0
